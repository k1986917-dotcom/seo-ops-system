"""Controlled rollout, comparison, promotion and rollback for sectional writing.

This module is intentionally independent from the Legacy W0 route.  It provides
the fail-closed primitives required before a single Action can opt into the new
pipeline.  The default policy is ``off`` and no function here runs AI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from data_sources.modules import seo_common
from seo_ops.services.sectional_assembly import (
    SectionAssemblyError,
    validate_sectional_assembly,
)

ROLLOUT_VERSION = 1
ROLLOUT_MODES = {"off", "shadow", "action"}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)")

OPERATOR_OVERRIDE_BLOCKER = "excessive_ai_retries"
_OPERATOR_REVIEW_KEYS = {
    "approved",
    "action_id",
    "reviewer",
    "reason",
    "comparison_sha256",
    "assembly_sha256",
}
_OPERATOR_MANIFEST_FIELDS = {
    "operator_override",
    "operator_reviewer",
    "operator_reason",
    "operator_reviewed_comparison_sha256",
    "operator_reviewed_assembly_sha256",
    "overridden_blockers",
}


class SectionalRolloutError(ValueError):
    """Raised when rollout state is invalid or unsafe."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SectionalRolloutError(f"{field} must be a non-empty string")
    return value.strip()


def build_rollout_policy(
    mode: str,
    allowed_action_ids: tuple[int, ...] | list[int] = (),
) -> dict[str, Any]:
    clean_mode = str(mode or "").strip().casefold()
    if clean_mode not in ROLLOUT_MODES:
        raise SectionalRolloutError("rollout mode must be off, shadow or action")
    if not isinstance(allowed_action_ids, (tuple, list)):
        raise SectionalRolloutError("allowed_action_ids must be a list or tuple")
    cleaned: set[int] = set()
    for value in allowed_action_ids:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SectionalRolloutError("allowed Action IDs must be positive integers")
        cleaned.add(value)
    policy = {
        "version": ROLLOUT_VERSION,
        "mode": clean_mode,
        "allowed_action_ids": sorted(cleaned),
    }
    policy["policy_sha256"] = _digest(policy)
    return validate_rollout_policy(policy)


def validate_rollout_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict) or policy.get("version") != ROLLOUT_VERSION:
        raise SectionalRolloutError("rollout policy must be a version 1 object")
    if set(policy) != {"version", "mode", "allowed_action_ids", "policy_sha256"}:
        raise SectionalRolloutError("rollout policy shape is invalid")
    if policy.get("mode") not in ROLLOUT_MODES:
        raise SectionalRolloutError("rollout policy mode is invalid")
    ids = policy.get("allowed_action_ids")
    if (
        not isinstance(ids, list)
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in ids)
        or ids != sorted(set(ids))
    ):
        raise SectionalRolloutError("rollout policy Action IDs are invalid")
    unsigned = dict(policy)
    digest = unsigned.pop("policy_sha256", None)
    if digest != _digest(unsigned):
        raise SectionalRolloutError("rollout policy SHA mismatch")
    return policy


def rollout_decision(policy: dict[str, Any], action_id: int) -> dict[str, Any]:
    validated = validate_rollout_policy(policy)
    if not isinstance(action_id, int) or isinstance(action_id, bool) or action_id <= 0:
        raise SectionalRolloutError("action_id must be a positive integer")
    mode = validated["mode"]
    allowed = action_id in validated["allowed_action_ids"]
    if mode == "off":
        reason = "sectional_rollout_off"
        shadow = False
        promote = False
    elif mode == "shadow":
        reason = "shadow_only"
        shadow = True
        promote = False
    elif allowed:
        reason = "action_allowlisted"
        shadow = True
        promote = True
    else:
        reason = "action_not_allowlisted"
        shadow = False
        promote = False
    return {
        "version": ROLLOUT_VERSION,
        "action_id": action_id,
        "mode": mode,
        "shadow_enabled": shadow,
        "promotion_allowed": promote,
        "reason_code": reason,
        "policy_sha256": validated["policy_sha256"],
    }


def _claim_metrics(draft: str, ledger: Any) -> dict[str, Any]:
    sentences = seo_common.extract_draft_sentences(draft)
    sentence_ids = {item["sentence_id"] for item in sentences}
    claims = ledger.get("claims", []) if isinstance(ledger, dict) else []
    valid_claims = [
        item
        for item in claims
        if isinstance(item, dict) and item.get("sentence_id") in sentence_ids
    ]
    covered = {item["sentence_id"] for item in valid_claims}
    return {
        "sentence_count": len(sentences),
        "claim_count": len(valid_claims),
        "covered_sentence_count": len(covered),
        "claim_coverage_ratio": round(len(covered) / len(sentences), 6) if sentences else 0.0,
    }


def _duplicate_sentence_count(draft: str) -> int:
    counts: dict[str, int] = {}
    for item in seo_common.extract_draft_sentences(draft):
        norm = str(item.get("norm") or "")
        if norm:
            counts[norm] = counts.get(norm, 0) + 1
    return sum(count - 1 for count in counts.values() if count > 1)


def _draft_metrics(draft: str, ledger: Any) -> dict[str, Any]:
    metrics = {
        "word_count": len(_WORD.findall(draft)),
        "markdown_link_count": len(_MARKDOWN_LINK.findall(draft)),
        "duplicate_sentence_count": _duplicate_sentence_count(draft),
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
    }
    metrics.update(_claim_metrics(draft, ledger))
    return metrics


def build_shadow_comparison(
    *,
    action_id: int,
    topic: str,
    old_draft: str,
    old_claim_ledger: dict[str, Any],
    new_assembly: dict[str, Any],
    run_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(action_id, int) or isinstance(action_id, bool) or action_id <= 0:
        raise SectionalRolloutError("action_id must be a positive integer")
    clean_topic = _clean_text(topic, "topic")
    if not isinstance(old_draft, str) or not old_draft.strip():
        raise SectionalRolloutError("old_draft must be non-empty")
    if not isinstance(old_claim_ledger, dict):
        raise SectionalRolloutError("old_claim_ledger must be an object")
    old_sha = hashlib.sha256(old_draft.encode("utf-8")).hexdigest()
    if old_claim_ledger.get("draft_sha256") != old_sha:
        raise SectionalRolloutError("old claim ledger does not match the old draft")
    try:
        assembly = validate_sectional_assembly(new_assembly)
    except SectionAssemblyError as exc:
        raise SectionalRolloutError(f"new assembly is invalid: {exc}") from exc
    if assembly["topic"] != clean_topic:
        raise SectionalRolloutError("comparison topic does not match new assembly")
    metrics = dict(run_metrics or {})
    for field in (
        "ai_calls",
        "retry_count",
        "empty_response_count",
        "prompt_chars",
    ):
        value = metrics.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SectionalRolloutError(f"run_metrics.{field} must be a non-negative integer")
        metrics[field] = value
    completion_tokens = metrics.get("completion_tokens")
    if completion_tokens is not None and (
        not isinstance(completion_tokens, int)
        or isinstance(completion_tokens, bool)
        or completion_tokens < 0
    ):
        raise SectionalRolloutError(
            "run_metrics.completion_tokens must be null or a non-negative integer"
        )
    metrics["completion_tokens"] = completion_tokens
    metrics["completion_tokens_known"] = completion_tokens is not None
    old_metrics = _draft_metrics(old_draft, old_claim_ledger)
    new_metrics = _draft_metrics(assembly["draft_markdown"], assembly["claim_ledger"])
    new_metrics["binding_counts"] = {
        kind: sum(1 for item in assembly["delivery"]["bindings"] if item["kind"] == kind)
        for kind in ("article", "product", "external_citation")
    }
    product_provenance = assembly["audit"]["metrics"].get("product_provenance", [])
    new_metrics["product_provenance_count"] = (
        len(product_provenance) if isinstance(product_provenance, list) else 0
    )
    blockers: list[str] = []
    if assembly["audit"]["blockers"]:
        blockers.append("new_assembly_has_gate_blockers")
    if new_metrics["duplicate_sentence_count"] > old_metrics["duplicate_sentence_count"]:
        blockers.append("duplicate_sentence_regression")
    if new_metrics["claim_coverage_ratio"] < old_metrics["claim_coverage_ratio"]:
        blockers.append("claim_coverage_regression")
    if new_metrics["product_provenance_count"] != new_metrics["binding_counts"]["product"]:
        blockers.append("product_copy_provenance_missing")
    if metrics["empty_response_count"]:
        blockers.append("empty_ai_response_observed")
    if metrics["retry_count"] > 2:
        blockers.append("excessive_ai_retries")
    report = {
        "version": ROLLOUT_VERSION,
        "action_id": action_id,
        "topic": clean_topic,
        "old": old_metrics,
        "new": new_metrics,
        "deltas": {
            "word_count": new_metrics["word_count"] - old_metrics["word_count"],
            "markdown_link_count": (
                new_metrics["markdown_link_count"] - old_metrics["markdown_link_count"]
            ),
            "claim_coverage_ratio": round(
                new_metrics["claim_coverage_ratio"] - old_metrics["claim_coverage_ratio"],
                6,
            ),
            "duplicate_sentence_count": (
                new_metrics["duplicate_sentence_count"] - old_metrics["duplicate_sentence_count"]
            ),
        },
        "run_metrics": metrics,
        "blockers": blockers,
        "recommendation": "eligible_for_single_action_promotion" if not blockers else "keep_legacy",
        "assembly_sha256": assembly["assembly_sha256"],
    }
    report["report_sha256"] = _digest(report)
    return validate_shadow_comparison(report)


def validate_shadow_comparison(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("version") != ROLLOUT_VERSION:
        raise SectionalRolloutError("shadow comparison must be a version 1 object")
    if not isinstance(report.get("action_id"), int) or report["action_id"] <= 0:
        raise SectionalRolloutError("shadow comparison action_id is invalid")
    _clean_text(report.get("topic"), "shadow comparison topic")
    for field in ("assembly_sha256", "report_sha256"):
        if not isinstance(report.get(field), str) or not _SHA256.fullmatch(report[field]):
            raise SectionalRolloutError(f"shadow comparison {field} is invalid")
    if report.get("recommendation") not in {
        "eligible_for_single_action_promotion",
        "keep_legacy",
    }:
        raise SectionalRolloutError("shadow comparison recommendation is invalid")
    blockers = report.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(item, str) for item in blockers):
        raise SectionalRolloutError("shadow comparison blockers are invalid")
    if bool(blockers) == (report["recommendation"] == "eligible_for_single_action_promotion"):
        raise SectionalRolloutError("shadow comparison recommendation contradicts blockers")
    unsigned = dict(report)
    digest = unsigned.pop("report_sha256")
    if digest != _digest(unsigned):
        raise SectionalRolloutError("shadow comparison SHA mismatch")
    return report


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def persist_shadow_comparison(workspace: Path, slug: str, report: dict[str, Any]) -> str:
    clean_slug = _clean_text(slug, "slug")
    if not _SLUG.fullmatch(clean_slug):
        raise SectionalRolloutError("slug must be lowercase kebab-case")
    validated = validate_shadow_comparison(report)
    path = (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / f"shadow-comparison-action-{validated['action_id']}.json"
    )
    _atomic_write(
        path,
        (json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return str(path)


def load_shadow_comparison(
    workspace: Path,
    slug: str,
    action_id: int,
    *,
    expected_report_sha256: str | None = None,
) -> dict[str, Any] | None:
    clean_slug = _clean_text(slug, "slug")
    if not _SLUG.fullmatch(clean_slug):
        raise SectionalRolloutError("slug must be lowercase kebab-case")
    path = (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / f"shadow-comparison-action-{action_id}.json"
    )
    if not path.exists():
        return None
    try:
        value = validate_shadow_comparison(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, SectionalRolloutError):
        return None
    if expected_report_sha256 and value["report_sha256"] != expected_report_sha256:
        return None
    return value


def _ensure_workspace_path(workspace: Path, path: Path) -> Path:
    root = Path(workspace).resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SectionalRolloutError("formal artifact path is outside the workspace") from exc
    return resolved


def _next_promotion_root(workspace: Path, slug: str, action_id: int) -> Path:
    base = Path(workspace) / "drafts" / "sectional" / slug / "promotions" / f"action-{action_id}"
    base.mkdir(parents=True, exist_ok=True)
    existing = [
        int(item.name.split("-")[-1])
        for item in base.glob("promotion-*")
        if item.is_dir() and item.name.split("-")[-1].isdigit()
    ]
    return base / f"promotion-{max(existing, default=0) + 1}"


def _replace_pair_transactionally(
    first_path: Path,
    first_payload: bytes,
    second_path: Path,
    second_payload: bytes,
) -> None:
    old_first = first_path.read_bytes() if first_path.exists() else None
    old_second = second_path.read_bytes() if second_path.exists() else None
    first_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    first_temp: Path | None = None
    second_temp: Path | None = None
    try:
        for target, payload, marker in (
            (first_path, first_payload, "first"),
            (second_path, second_payload, "second"),
        ):
            fd, name = tempfile.mkstemp(
                prefix=f".{target.name}.{marker}.", suffix=".tmp", dir=target.parent
            )
            temp = Path(name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if marker == "first":
                first_temp = temp
            else:
                second_temp = temp
        os.replace(first_temp, first_path)
        first_temp = None
        os.replace(second_temp, second_path)
        second_temp = None
    except Exception:
        if old_first is None:
            first_path.unlink(missing_ok=True)
        else:
            first_path.write_bytes(old_first)
        if old_second is None:
            second_path.unlink(missing_ok=True)
        else:
            second_path.write_bytes(old_second)
        raise
    finally:
        if first_temp is not None:
            first_temp.unlink(missing_ok=True)
        if second_temp is not None:
            second_temp.unlink(missing_ok=True)


def validate_operator_review(
    review: Any,
    *,
    action_id: int,
    comparison: dict[str, Any],
    assembly: dict[str, Any],
) -> dict[str, Any]:
    """Validate one explicit operator review object for a promotion override.

    Every field is mandatory and strictly checked.  The returned normalized
    object is written into the promotion manifest and covered by its SHA.
    """
    if not isinstance(review, dict):
        raise SectionalRolloutError("operator review must be an object")
    if set(review) != _OPERATOR_REVIEW_KEYS:
        raise SectionalRolloutError("operator review shape is invalid")
    if review.get("approved") is not True:
        raise SectionalRolloutError("operator review approved must be exactly true")
    review_action = review.get("action_id")
    if not isinstance(review_action, int) or isinstance(review_action, bool) or review_action <= 0:
        raise SectionalRolloutError("operator review action_id must be a positive integer")
    if review_action != action_id:
        raise SectionalRolloutError("operator review action_id does not match the promotion Action")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise SectionalRolloutError("operator reviewer must be a non-empty string")
    reason = review.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise SectionalRolloutError("operator reason must be a non-empty string")
    if len(reason.strip()) < 20:
        raise SectionalRolloutError("operator reason must be at least 20 characters")
    comparison_sha = review.get("comparison_sha256")
    if not isinstance(comparison_sha, str) or not _SHA256.fullmatch(comparison_sha):
        raise SectionalRolloutError("operator review comparison_sha256 is invalid")
    if comparison_sha != comparison["report_sha256"]:
        raise SectionalRolloutError("operator review comparison SHA does not match the comparison")
    assembly_sha = review.get("assembly_sha256")
    if not isinstance(assembly_sha, str) or not _SHA256.fullmatch(assembly_sha):
        raise SectionalRolloutError("operator review assembly_sha256 is invalid")
    if assembly_sha != assembly["assembly_sha256"]:
        raise SectionalRolloutError("operator review assembly SHA does not match the assembly")
    return {
        "approved": True,
        "action_id": review_action,
        "reviewer": reviewer.strip(),
        "reason": reason.strip(),
        "comparison_sha256": comparison_sha,
        "assembly_sha256": assembly_sha,
    }


def _operator_override_allowed(
    report: dict[str, Any],
    validated_assembly: dict[str, Any],
    policy: dict[str, Any],
    action_id: int,
) -> None:
    """Raise unless every strict operator-override condition holds."""
    if report["recommendation"] != "keep_legacy":
        raise SectionalRolloutError("operator override only applies to keep_legacy comparisons")
    if report["blockers"] != [OPERATOR_OVERRIDE_BLOCKER]:
        raise SectionalRolloutError(
            "operator override may only override exactly ['excessive_ai_retries']"
        )
    audit = validated_assembly.get("audit") or {}
    if audit.get("passed") is not True:
        raise SectionalRolloutError("operator override requires assembly audit passed")
    if audit.get("blockers"):
        raise SectionalRolloutError("operator override requires zero assembly gate blockers")
    new_metrics = report.get("new") or {}
    if new_metrics.get("binding_counts", {}).get("product") != new_metrics.get(
        "product_provenance_count"
    ):
        raise SectionalRolloutError(
            "operator override requires product provenance coverage in the comparison"
        )
    decision = rollout_decision(policy, action_id)
    if not decision["promotion_allowed"]:
        raise SectionalRolloutError("sectional promotion is not allowed for this Action")
    if policy.get("mode") != "action":
        raise SectionalRolloutError("operator override requires action rollout mode")
    if policy.get("allowed_action_ids") != [action_id]:
        raise SectionalRolloutError(
            "operator override requires the action allowlist to contain only the current Action"
        )


def operator_override_eligibility(
    *,
    action_id: int,
    policy: dict[str, Any],
    comparison: dict[str, Any],
    assembly: dict[str, Any],
    operator_review: dict[str, Any],
) -> dict[str, Any]:
    """Read-only preflight: report whether an operator override would be allowed."""
    try:
        report = validate_shadow_comparison(comparison)
        if report["action_id"] != action_id:
            raise SectionalRolloutError("comparison does not match this Action")
        try:
            validated_assembly = validate_sectional_assembly(assembly)
        except SectionAssemblyError as exc:
            raise SectionalRolloutError(f"assembly is invalid: {exc}") from exc
        if report["assembly_sha256"] != validated_assembly["assembly_sha256"]:
            raise SectionalRolloutError("comparison was produced for a different assembly")
        _operator_override_allowed(report, validated_assembly, policy, action_id)
        validate_operator_review(
            operator_review,
            action_id=action_id,
            comparison=report,
            assembly=validated_assembly,
        )
    except SectionalRolloutError as exc:
        return {"eligible": False, "reason": str(exc)}
    return {"eligible": True, "reason": ""}


def promote_sectional_assembly(
    *,
    workspace: Path,
    slug: str,
    action_id: int,
    policy: dict[str, Any],
    comparison: dict[str, Any],
    assembly: dict[str, Any],
    formal_draft_path: Path,
    formal_claim_path: Path,
    expected_draft_sha256: str,
    expected_claim_sha256: str,
    operator_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_slug = _clean_text(slug, "slug")
    if not _SLUG.fullmatch(clean_slug):
        raise SectionalRolloutError("slug must be lowercase kebab-case")
    decision = rollout_decision(policy, action_id)
    if not decision["promotion_allowed"]:
        raise SectionalRolloutError("sectional promotion is not allowed for this Action")
    report = validate_shadow_comparison(comparison)
    if report["action_id"] != action_id:
        raise SectionalRolloutError("comparison does not match this Action")
    try:
        validated_assembly = validate_sectional_assembly(assembly)
    except SectionAssemblyError as exc:
        raise SectionalRolloutError(f"assembly is invalid: {exc}") from exc
    if report["assembly_sha256"] != validated_assembly["assembly_sha256"]:
        raise SectionalRolloutError("comparison was produced for a different assembly")
    operator_review_used = False
    if report["recommendation"] == "eligible_for_single_action_promotion":
        pass
    elif report["recommendation"] == "keep_legacy" and operator_review is not None:
        _operator_override_allowed(report, validated_assembly, policy, action_id)
        review = validate_operator_review(
            operator_review,
            action_id=action_id,
            comparison=report,
            assembly=validated_assembly,
        )
        operator_review_used = True
    else:
        raise SectionalRolloutError("comparison does not approve this Action for promotion")
    draft_path = _ensure_workspace_path(workspace, formal_draft_path)
    claim_path = _ensure_workspace_path(workspace, formal_claim_path)
    if not draft_path.exists() or not claim_path.exists():
        raise SectionalRolloutError("formal draft and claim ledger must already exist")
    old_draft = draft_path.read_bytes()
    old_claim = claim_path.read_bytes()
    if _sha_bytes(old_draft) != expected_draft_sha256:
        raise SectionalRolloutError("formal draft changed after shadow comparison")
    if _sha_bytes(old_claim) != expected_claim_sha256:
        raise SectionalRolloutError("formal claim ledger changed after shadow comparison")
    root = _next_promotion_root(workspace, clean_slug, action_id)
    root.mkdir(parents=True, exist_ok=False)
    backup_draft = root / "legacy-draft.md"
    backup_claim = root / "legacy-claim-ledger.json"
    new_draft = validated_assembly["draft_markdown"].encode("utf-8")
    new_claim = (
        json.dumps(
            validated_assembly["claim_ledger"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    manifest = {
        "version": ROLLOUT_VERSION,
        "status": "prepared",
        "action_id": action_id,
        "slug": clean_slug,
        "workspace_root": str(Path(workspace).resolve()),
        "policy_sha256": decision["policy_sha256"],
        "comparison_sha256": report["report_sha256"],
        "assembly_sha256": validated_assembly["assembly_sha256"],
        "formal_draft_path": str(draft_path),
        "formal_claim_path": str(claim_path),
        "backup_draft_path": str(backup_draft),
        "backup_claim_path": str(backup_claim),
        "before_draft_sha256": _sha_bytes(old_draft),
        "before_claim_sha256": _sha_bytes(old_claim),
        "after_draft_sha256": _sha_bytes(new_draft),
        "after_claim_sha256": _sha_bytes(new_claim),
    }
    if operator_review_used:
        manifest.update(
            {
                "operator_override": True,
                "operator_reviewer": review["reviewer"],
                "operator_reason": review["reason"],
                "operator_reviewed_comparison_sha256": review["comparison_sha256"],
                "operator_reviewed_assembly_sha256": review["assembly_sha256"],
                "overridden_blockers": [OPERATOR_OVERRIDE_BLOCKER],
            }
        )
    manifest["manifest_sha256"] = _digest(manifest)
    manifest = validate_promotion_manifest(manifest)
    manifest_path = root / "promotion-manifest.json"
    try:
        _atomic_write(backup_draft, old_draft)
        _atomic_write(backup_claim, old_claim)
        _atomic_write(
            manifest_path,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        _replace_pair_transactionally(draft_path, new_draft, claim_path, new_claim)
        promoted = dict(manifest)
        promoted["status"] = "promoted"
        promoted.pop("manifest_sha256", None)
        promoted["manifest_sha256"] = _digest(promoted)
        promoted = validate_promotion_manifest(promoted)
        try:
            _atomic_write(
                manifest_path,
                (json.dumps(promoted, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                ),
            )
        except Exception:
            _replace_pair_transactionally(draft_path, old_draft, claim_path, old_claim)
            raise
        return promoted
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def validate_promotion_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("version") != ROLLOUT_VERSION:
        raise SectionalRolloutError("promotion manifest must be a version 1 object")
    if manifest.get("status") not in {"prepared", "promoted", "rolled_back"}:
        raise SectionalRolloutError("promotion manifest status is invalid")
    if (
        not isinstance(manifest.get("action_id"), int)
        or isinstance(manifest["action_id"], bool)
        or manifest["action_id"] <= 0
    ):
        raise SectionalRolloutError("promotion manifest action_id is invalid")
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise SectionalRolloutError("promotion manifest slug is invalid")
    workspace_root = Path(_clean_text(manifest.get("workspace_root"), "workspace_root")).resolve()
    if not workspace_root.is_absolute():
        raise SectionalRolloutError("promotion manifest workspace_root must be absolute")
    for field in (
        "formal_draft_path",
        "formal_claim_path",
        "backup_draft_path",
        "backup_claim_path",
    ):
        candidate = Path(_clean_text(manifest.get(field), field)).resolve()
        if not candidate.is_absolute():
            raise SectionalRolloutError(f"promotion manifest {field} must be absolute")
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise SectionalRolloutError(
                f"promotion manifest {field} is outside the workspace"
            ) from exc
    for field in (
        "policy_sha256",
        "comparison_sha256",
        "assembly_sha256",
        "before_draft_sha256",
        "before_claim_sha256",
        "after_draft_sha256",
        "after_claim_sha256",
        "manifest_sha256",
    ):
        if not isinstance(manifest.get(field), str) or not _SHA256.fullmatch(manifest[field]):
            raise SectionalRolloutError(f"promotion manifest {field} is invalid")
    operator_fields = _OPERATOR_MANIFEST_FIELDS.intersection(manifest)
    override = manifest.get("operator_override")
    if override is True:
        if operator_fields != _OPERATOR_MANIFEST_FIELDS:
            raise SectionalRolloutError("operator override manifest fields are incomplete")
        _clean_text(manifest.get("operator_reviewer"), "operator_reviewer")
        if (
            not isinstance(manifest.get("operator_reason"), str)
            or len(manifest["operator_reason"].strip()) < 20
        ):
            raise SectionalRolloutError("operator_reason must be at least 20 characters")
        for field in (
            "operator_reviewed_comparison_sha256",
            "operator_reviewed_assembly_sha256",
        ):
            if not isinstance(manifest.get(field), str) or not _SHA256.fullmatch(manifest[field]):
                raise SectionalRolloutError(f"promotion manifest {field} is invalid")
        if manifest.get("overridden_blockers") != [OPERATOR_OVERRIDE_BLOCKER]:
            raise SectionalRolloutError(
                "promotion manifest overridden_blockers must be exactly ['excessive_ai_retries']"
            )
    elif override is None:
        if operator_fields:
            raise SectionalRolloutError(
                "operator override manifest fields require operator_override=true"
            )
    else:
        raise SectionalRolloutError("promotion manifest operator_override must be exactly true")

    unsigned = dict(manifest)
    digest = unsigned.pop("manifest_sha256")
    if digest != _digest(unsigned):
        raise SectionalRolloutError("promotion manifest SHA mismatch")
    return manifest


def rollback_sectional_promotion(manifest_path: Path) -> dict[str, Any]:
    path = Path(manifest_path)
    try:
        manifest = validate_promotion_manifest(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, SectionalRolloutError) as exc:
        raise SectionalRolloutError("promotion manifest cannot be loaded") from exc
    workspace_root = Path(manifest["workspace_root"])
    try:
        path.resolve().relative_to(workspace_root)
    except ValueError as exc:
        raise SectionalRolloutError("promotion manifest path is outside the workspace") from exc
    if manifest["status"] != "promoted":
        raise SectionalRolloutError("promotion has already been rolled back")
    draft_path = Path(manifest["formal_draft_path"])
    claim_path = Path(manifest["formal_claim_path"])
    backup_draft = Path(manifest["backup_draft_path"])
    backup_claim = Path(manifest["backup_claim_path"])
    current_draft = draft_path.read_bytes()
    current_claim = claim_path.read_bytes()
    if _sha_bytes(current_draft) != manifest["after_draft_sha256"]:
        raise SectionalRolloutError("formal draft changed after promotion; rollback stopped")
    if _sha_bytes(current_claim) != manifest["after_claim_sha256"]:
        raise SectionalRolloutError("formal claim ledger changed after promotion; rollback stopped")
    old_draft = backup_draft.read_bytes()
    old_claim = backup_claim.read_bytes()
    if _sha_bytes(old_draft) != manifest["before_draft_sha256"]:
        raise SectionalRolloutError("draft backup SHA mismatch")
    if _sha_bytes(old_claim) != manifest["before_claim_sha256"]:
        raise SectionalRolloutError("claim backup SHA mismatch")
    _replace_pair_transactionally(draft_path, old_draft, claim_path, old_claim)
    updated = dict(manifest)
    updated["status"] = "rolled_back"
    updated.pop("manifest_sha256", None)
    updated["manifest_sha256"] = _digest(updated)
    updated = validate_promotion_manifest(updated)
    try:
        _atomic_write(
            path,
            (json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
    except Exception:
        _replace_pair_transactionally(
            draft_path,
            current_draft,
            claim_path,
            current_claim,
        )
        raise
    return updated
