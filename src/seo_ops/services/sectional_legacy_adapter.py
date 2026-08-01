"""Legacy W0 adapter for sectional shadow generation and controlled promotion."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from data_sources.modules.seo_config import TIER_MIN_WORDS
from seo_ops.services.sectional_consistency import contains_unsafe_legacy_metadata_claim
from seo_ops.services.sectional_pipeline import run_sectional_shadow_candidate
from seo_ops.services.sectional_rollout import (
    build_rollout_policy,
    build_shadow_comparison,
    persist_shadow_comparison,
    promote_sectional_assembly,
    rollout_decision,
)


class SectionalLegacyAdapterError(RuntimeError):
    """Raised when Legacy inputs cannot safely produce a sectional candidate."""


def _frontmatter_scalar(value: str) -> str:
    """Return the text value of a simple YAML frontmatter scalar.

    Legacy drafts commonly quote frontmatter values.  The sectional adapter
    does not need a full YAML parser, but it must compare the represented value
    rather than the literal quote characters.  Double-quoted values are decoded
    with JSON-compatible escapes; single-quoted YAML values collapse doubled
    apostrophes.  Malformed or unmatched outer quoting is rejected here so it
    cannot accidentally satisfy a downstream length-only metadata gate.
    """
    clean = value.strip()
    starts_quoted = bool(clean) and clean[0] in {'"', "'"}
    if starts_quoted and (len(clean) < 2 or clean[0] != clean[-1]):
        raise SectionalLegacyAdapterError("Legacy frontmatter scalar has malformed quotes")
    if len(clean) < 2 or not starts_quoted:
        return clean
    if clean[0] == '"':
        try:
            decoded = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise SectionalLegacyAdapterError(
                "Legacy frontmatter double-quoted scalar is invalid"
            ) from exc
        if not isinstance(decoded, str):
            raise SectionalLegacyAdapterError(
                "Legacy frontmatter quoted scalar must decode to text"
            )
        return decoded
    inner = clean[1:-1]
    if "'" in inner.replace("''", ""):
        raise SectionalLegacyAdapterError("Legacy frontmatter single-quoted scalar is invalid")
    return inner.replace("''", "'")


def _frontmatter(draft: str) -> dict[str, str]:
    if not isinstance(draft, str) or not draft.startswith("---"):
        raise SectionalLegacyAdapterError("Legacy draft is missing frontmatter")
    parts = draft.split("---", 2)
    if len(parts) < 3:
        raise SectionalLegacyAdapterError("Legacy draft frontmatter is not closed")
    result: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = re.match(r"^([^:]+?):\s*(.+?)\s*$", line.strip())
        if match:
            result[match.group(1).strip().casefold()] = _frontmatter_scalar(match.group(2))
    return result


def _list_value(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _fit_metadata_text(
    values: list[str],
    *,
    minimum: int,
    maximum: int,
    removable_phrases: tuple[str, ...] = (),
) -> str:
    """Choose and word-trim an existing metadata value into a strict range."""
    fallback = ""
    for value in values:
        clean = " ".join(str(value or "").split())
        if not clean:
            continue
        if not fallback:
            fallback = clean
        candidate = clean
        if len(candidate) > maximum:
            for phrase in removable_phrases:
                compacted = re.sub(
                    rf"\b{re.escape(phrase)}\b\s*",
                    "",
                    candidate,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if minimum <= len(compacted) <= maximum:
                    return compacted
        if len(candidate) > maximum:
            shortened = candidate[: maximum + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
            candidate = shortened or candidate[:maximum].rstrip(" ,;:-")
        if minimum <= len(candidate) <= maximum:
            return candidate
    return fallback


def _fit_seo_title(values: list[str]) -> str:
    for value in values:
        clean = " ".join(str(value or "").split())
        if not clean:
            continue
        if 50 <= len(clean) <= 60:
            return clean
        pattern = re.fullmatch(
            r"(.+?)\s+for\s+(.+?)\s+in\s+(.+)",
            clean,
            flags=re.IGNORECASE,
        )
        if pattern:
            subject, purpose, context = (part.strip() for part in pattern.groups())
            purpose_words = purpose.split()
            if len(purpose_words) > 1 and purpose_words[0].casefold().endswith("ing"):
                purpose = " ".join(purpose_words[1:])
            candidate = f"{subject} for {context}: {purpose}"
            if 50 <= len(candidate) <= 60:
                return candidate
    return _fit_metadata_text(values, minimum=50, maximum=60)


def _topic_metadata_phrases(topic: str) -> list[str]:
    clean = " ".join(str(topic or "").split())
    if not clean:
        return []
    candidates = [
        part.strip().casefold()
        for part in re.split(
            r"\b(?:for|in|with|versus|vs\.?|and|to|by|on)\b",
            clean,
            flags=re.IGNORECASE,
        )
        if len(part.strip().split()) >= 2
    ]
    candidates.append(clean.casefold())
    words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", clean)
    if len(words) >= 2:
        candidates.extend(
            [
                " ".join(words[:2]).casefold(),
                " ".join(words[-2:]).casefold(),
            ]
        )
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split())
        key = normalized.casefold()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
    return result


def _bounded_metadata_list(
    primary: list[str],
    fallback: list[str],
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in primary:
        clean = " ".join(str(value or "").split())
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
        if len(result) >= maximum:
            break
    if len(result) >= minimum:
        return result
    for value in fallback:
        clean = " ".join(str(value or "").split())
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
        if len(result) >= minimum or len(result) >= maximum:
            break
    return result


def _safe_metadata_values(values: list[str]) -> list[str]:
    return [value for value in values if value and not contains_unsafe_legacy_metadata_claim(value)]


def _safe_metadata_fallback(topic: str) -> str:
    return (
        f"Evaluate options for {topic.lower()}. Review operating requirements, "
        "product details, comparison steps, practical constraints, and key selection "
        "factors before making a decision."
    )


def build_legacy_assembly_metadata(
    draft: str,
    *,
    topic: str,
    slug: str,
    author: str,
    tier: str,
) -> dict[str, Any]:
    meta = _frontmatter(draft)
    minimum = int(TIER_MIN_WORDS.get(tier, 1200))
    if tier in {"Cluster Content", "cluster"}:
        minimum = max(minimum, 3000)
    title = meta.get("title") or topic
    description = meta.get("description", "")
    safe_fallback = _safe_metadata_fallback(topic)
    topic_phrases = _topic_metadata_phrases(topic)
    tags = _bounded_metadata_list(
        _list_value(meta.get("tags", "")),
        topic_phrases,
        minimum=3,
        maximum=5,
    )
    seo_keywords = _bounded_metadata_list(
        _list_value(meta.get("seo keywords", "")),
        [topic.casefold(), *topic_phrases],
        minimum=1,
        maximum=8,
    )
    return {
        "title": title,
        "slug": meta.get("slug") or slug,
        "author": meta.get("author") or author,
        "summary": _fit_metadata_text(
            _safe_metadata_values([meta.get("summary", ""), description]) + [safe_fallback],
            minimum=80,
            maximum=300,
        ),
        "tags": tags,
        "page_type": meta.get("page type") or meta.get("tier") or tier,
        "seo_title": _fit_seo_title(
            _safe_metadata_values([meta.get("seo title", "")]) + [title, topic]
        ),
        "seo_description": _fit_metadata_text(
            _safe_metadata_values(
                [meta.get("seo description", ""), description, meta.get("summary", "")]
            )
            + [safe_fallback],
            minimum=150,
            maximum=160,
            removable_phrases=(
                "best",
                "complete",
                "comprehensive",
                "detailed",
                "ultimate",
                "in-depth",
            ),
        ),
        "seo_keywords": seo_keywords,
        "target_words": {
            "min": minimum,
            "max": max(minimum, round(minimum * 1.4)),
        },
    }


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SectionalLegacyAdapterError(f"cannot load {field}: {exc}") from exc
    if not isinstance(value, dict):
        raise SectionalLegacyAdapterError(f"{field} must be a JSON object")
    return value


def _with_evidence_support_basis(
    workspace: Path,
    slug: str,
    cards: dict[str, Any],
) -> dict[str, Any]:
    """Annotate persisted legacy cards without mutating their source files.

    Older R3 hand-offs predate ``support_basis``.  The original evidence ledger
    still records whether a card came from an exact quote or only a synthesized
    key finding, so shadow generation can recover that distinction in memory.
    Unknown/missing entries are treated conservatively as key findings.
    """
    evidence_ledger_path = Path(workspace) / "research" / f"evidence-ledger-{slug}.json"
    evidence_ledger = (
        _load_json(evidence_ledger_path, "evidence ledger")
        if evidence_ledger_path.exists()
        else {"evidence": []}
    )
    basis_by_id = {
        str(item.get("evidence_id") or ""): (
            "verified_quote"
            if str(item.get("quote") or "").strip()
            and item.get("quote_verified") is True
            else "quote"
            if str(item.get("quote") or "").strip()
            else "key_finding"
        )
        for item in evidence_ledger.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    annotated = json.loads(json.dumps(cards, ensure_ascii=False))

    def annotate(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            item["support_basis"] = basis_by_id.get(evidence_id, "key_finding")

    annotate(annotated.get("all_cards"))
    for section in annotated.get("sections", []):
        if isinstance(section, dict):
            annotate(section.get("cards"))
    return annotated


def _latest_formal_draft(workspace: Path, slug: str) -> Path:
    candidates = [
        path for path in (Path(workspace) / "drafts").glob(f"{slug}-*.md") if path.is_file()
    ]
    if not candidates:
        raise SectionalLegacyAdapterError("formal Legacy draft is missing")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _atomic_restore(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.shadow-restore.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_formal_pair(
    draft_path: Path,
    draft_bytes: bytes,
    claim_path: Path,
    claim_bytes: bytes,
) -> None:
    try:
        if draft_path.read_bytes() != draft_bytes:
            _atomic_restore(draft_path, draft_bytes)
        if claim_path.read_bytes() != claim_bytes:
            _atomic_restore(claim_path, claim_bytes)
    except OSError as exc:
        raise SectionalLegacyAdapterError(
            f"cannot restore formal Legacy pair after shadow failure: {exc}"
        ) from exc


def _validated_tier(value: Any, field: str) -> str:
    tier = str(value or "").strip()
    if tier and tier not in TIER_MIN_WORDS:
        raise SectionalLegacyAdapterError(f"{field} is unsupported: {tier}")
    return tier


def _resolve_existing_pair_tier(
    *,
    workspace: Path,
    slug: str,
    brief: dict[str, Any],
    draft_bytes: bytes,
) -> tuple[str, str]:
    """Resolve a legacy tier without mutating resumed Action artifacts.

    Older Actions may have an empty ``write-brief.tier`` even though W1b has
    already recorded the tier used to validate the exact formal draft.  That
    state is a safe compatibility source only when its draft SHA matches the
    current formal draft byte-for-byte.
    """
    brief_tier = _validated_tier(brief.get("tier"), "write brief tier")
    state_path = workspace / "reports" / f"w2-state-{slug}.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        state = _load_json(state_path, "W2 state")
    draft_sha256 = hashlib.sha256(draft_bytes).hexdigest()
    state_draft_sha256 = str(state.get("precheck_draft_sha256") or "").strip()
    state_matches_draft = state_draft_sha256 == draft_sha256
    raw_state_tier = state.get("precheck_tier")

    if brief_tier:
        if state_matches_draft:
            state_tier = _validated_tier(raw_state_tier, "W1b state tier")
        else:
            state_tier = ""
        if state_tier and state_tier != brief_tier:
            raise SectionalLegacyAdapterError(
                "write brief tier conflicts with the matching W1b state tier"
            )
        return brief_tier, "write_brief"

    state_tier = _validated_tier(raw_state_tier, "W1b state tier")
    if not state_tier:
        raise SectionalLegacyAdapterError(
            "write brief tier is missing and matching W1b state tier is unavailable"
        )
    if not state_matches_draft:
        raise SectionalLegacyAdapterError(
            "W1b state tier does not belong to the current formal draft"
        )
    return state_tier, "matching_w1b_state"


async def run_existing_legacy_sectional_shadow(
    *,
    action_id: int,
    topic: str,
    author: str,
    workspace: Path,
    slug: str,
    site_slug: str = "",
    settings: Any,
    generate_text_async: Callable[..., Awaitable[str]],
) -> dict[str, Any]:
    """Run shadow against an existing formal pair without re-running Legacy W0.

    This is the controlled operational path for an Action that already has a
    formal draft/claim ledger.  It is intentionally shadow-only and verifies
    that the formal pair remains byte-for-byte unchanged.
    """
    if getattr(settings, "sectional_writing_mode", "off") != "shadow":
        raise SectionalLegacyAdapterError(
            "existing-pair sectional run requires sectional_writing_mode=shadow"
        )

    workspace = Path(workspace)
    formal_draft_path = _latest_formal_draft(workspace, slug)
    formal_claim_path = workspace / "research" / f"claim-ledger-{slug}.json"
    try:
        draft_before = formal_draft_path.read_bytes()
        claim_before = formal_claim_path.read_bytes()
    except OSError as exc:
        raise SectionalLegacyAdapterError(f"cannot snapshot formal Legacy pair: {exc}") from exc

    contracts = {
        "brief": _load_json(
            workspace / "research" / f"write-brief-{slug}.json",
            "write brief",
        ),
        "coverage": _load_json(
            workspace / "research" / f"coverage-contract-{slug}.json",
            "coverage contract",
        ),
        "cards": _load_json(
            workspace / "research" / f"evidence-cards-{slug}.json",
            "evidence cards",
        ),
    }
    contracts["cards"] = _with_evidence_support_basis(
        workspace,
        slug,
        contracts["cards"],
    )
    brief = contracts["brief"]
    tier, tier_source = _resolve_existing_pair_tier(
        workspace=workspace,
        slug=slug,
        brief=brief,
        draft_bytes=draft_before,
    )

    try:
        result = await run_legacy_sectional_rollout(
            action_id=action_id,
            topic=topic,
            author=author,
            tier=tier,
            intent=str(brief.get("intent") or ""),
            guidance=str(brief.get("guidance") or ""),
            workspace=workspace,
            slug=slug,
            site_slug=site_slug,
            contracts=contracts,
            formal_draft_path=formal_draft_path,
            formal_claim_path=formal_claim_path,
            settings=settings,
            generate_text_async=generate_text_async,
        )
    except Exception:
        _restore_formal_pair(
            formal_draft_path,
            draft_before,
            formal_claim_path,
            claim_before,
        )
        raise
    try:
        draft_after = formal_draft_path.read_bytes()
        claim_after = formal_claim_path.read_bytes()
    except OSError as exc:
        raise SectionalLegacyAdapterError(
            f"cannot verify formal Legacy pair after shadow: {exc}"
        ) from exc
    if draft_after != draft_before or claim_after != claim_before:
        _restore_formal_pair(
            formal_draft_path,
            draft_before,
            formal_claim_path,
            claim_before,
        )
        raise SectionalLegacyAdapterError(
            "formal Legacy pair changed during existing-pair shadow and was restored"
        )
    if result.get("status") != "shadow_complete":
        raise SectionalLegacyAdapterError(
            f"existing-pair shadow did not complete: {result.get('status')}"
        )
    decision = result.get("decision")
    if not isinstance(decision, dict) or decision.get("promotion_allowed") is not False:
        raise SectionalLegacyAdapterError("existing-pair shadow unexpectedly allowed promotion")

    result["formal_pair"] = {
        "draft_path": str(formal_draft_path),
        "claim_path": str(formal_claim_path),
        "draft_sha256_before": hashlib.sha256(draft_before).hexdigest(),
        "draft_sha256_after": hashlib.sha256(draft_after).hexdigest(),
        "claim_sha256_before": hashlib.sha256(claim_before).hexdigest(),
        "claim_sha256_after": hashlib.sha256(claim_after).hexdigest(),
        "unchanged": True,
    }
    result["existing_pair_inputs"] = {
        "tier": tier,
        "tier_source": tier_source,
        "site_slug": site_slug or "default",
    }
    return result


async def run_legacy_sectional_rollout(
    *,
    action_id: int,
    topic: str,
    author: str,
    tier: str,
    intent: str,
    guidance: str,
    workspace: Path,
    slug: str,
    site_slug: str = "",
    contracts: dict[str, Any],
    formal_draft_path: Path,
    formal_claim_path: Path,
    settings: Any,
    generate_text_async: Callable[..., Awaitable[str]],
) -> dict[str, Any]:
    """Run sectional shadow after Legacy W0 and optionally promote one Action."""
    policy = build_rollout_policy(
        getattr(settings, "sectional_writing_mode", "off"),
        list(getattr(settings, "sectional_action_allowlist", ())),
    )
    decision = rollout_decision(policy, action_id)
    if not decision["shadow_enabled"]:
        return {"decision": decision, "status": "legacy_only"}

    try:
        old_draft_bytes = Path(formal_draft_path).read_bytes()
        old_claim_bytes = Path(formal_claim_path).read_bytes()
    except OSError as exc:
        raise SectionalLegacyAdapterError(f"cannot read formal Legacy pair: {exc}") from exc
    old_draft = old_draft_bytes.decode("utf-8")
    try:
        old_claim = json.loads(old_claim_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SectionalLegacyAdapterError("formal claim ledger is invalid JSON") from exc
    if not isinstance(old_claim, dict):
        raise SectionalLegacyAdapterError("formal claim ledger must be an object")

    brief = contracts.get("brief") if isinstance(contracts, dict) else None
    cards = contracts.get("cards") if isinstance(contracts, dict) else None
    if not isinstance(brief, dict) or not isinstance(cards, dict):
        raise SectionalLegacyAdapterError("Legacy write contracts are incomplete")
    brief_text = brief.get("research_brief_excerpt")
    if not isinstance(brief_text, str) or not brief_text.strip():
        raise SectionalLegacyAdapterError("Legacy research brief excerpt is missing")
    internal_links_path = Path(workspace) / "context" / "internal-links-map.md"
    products_path = Path(workspace) / "products" / "live_products_report.md"
    try:
        internal_links = internal_links_path.read_text(encoding="utf-8")
        products = products_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SectionalLegacyAdapterError(f"sectional catalog input is missing: {exc}") from exc
    metadata = build_legacy_assembly_metadata(
        old_draft,
        topic=topic,
        slug=slug,
        author=author,
        tier=tier,
    )

    def worker() -> dict[str, Any]:
        def generate(system: str, user: str, **kwargs: Any) -> str:
            return asyncio.run(generate_text_async(system, user, **kwargs))

        return run_sectional_shadow_candidate(
            workspace=workspace,
            slug=slug,
            topic=topic,
            tier=tier,
            intent=intent,
            brief_text=brief_text,
            internal_links_map=internal_links,
            product_report=products,
            evidence_cards=cards,
            metadata=metadata,
            generate_text=generate,
            guidance=guidance,
            current_slug=slug,
            site_slug=site_slug,
            resume=True,
            max_ai_calls=int(getattr(settings, "sectional_ai_call_limit", 24)),
        )

    pipeline = await asyncio.to_thread(worker)
    comparison = build_shadow_comparison(
        action_id=action_id,
        topic=topic,
        old_draft=old_draft,
        old_claim_ledger=old_claim,
        new_assembly=pipeline["assembly"],
        run_metrics=pipeline["run_metrics"],
    )
    comparison_path = persist_shadow_comparison(workspace, slug, comparison)
    result: dict[str, Any] = {
        "decision": decision,
        "status": "shadow_complete",
        "pipeline_result_sha256": pipeline["result_sha256"],
        "comparison_path": comparison_path,
        "comparison": comparison,
    }
    if decision["promotion_allowed"] and comparison["recommendation"] == (
        "eligible_for_single_action_promotion"
    ):
        manifest = promote_sectional_assembly(
            workspace=workspace,
            slug=slug,
            action_id=action_id,
            policy=policy,
            comparison=comparison,
            assembly=pipeline["assembly"],
            formal_draft_path=formal_draft_path,
            formal_claim_path=formal_claim_path,
            expected_draft_sha256=hashlib.sha256(old_draft_bytes).hexdigest(),
            expected_claim_sha256=hashlib.sha256(old_claim_bytes).hexdigest(),
        )
        result["status"] = "promoted"
        result["promotion_manifest"] = manifest
    elif decision["promotion_allowed"]:
        result["status"] = "kept_legacy"
        result["promotion_blockers"] = list(comparison.get("blockers") or [])
    return result
