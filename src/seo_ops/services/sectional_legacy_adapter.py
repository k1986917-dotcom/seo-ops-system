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
            result[match.group(1).strip().casefold()] = match.group(2).strip()
    return result


def _list_value(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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
    return {
        "title": meta.get("title") or topic,
        "slug": meta.get("slug") or slug,
        "author": meta.get("author") or author,
        "summary": meta.get("summary", ""),
        "tags": _list_value(meta.get("tags", "")),
        "page_type": meta.get("page type") or meta.get("tier") or tier,
        "seo_title": meta.get("seo title") or meta.get("title", ""),
        "seo_description": meta.get("seo description") or meta.get("summary", ""),
        "seo_keywords": _list_value(meta.get("seo keywords", "")),
        "target_words": {"min": minimum, "max": max(minimum, round(minimum * 1.6))},
    }


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SectionalLegacyAdapterError(f"cannot load {field}: {exc}") from exc
    if not isinstance(value, dict):
        raise SectionalLegacyAdapterError(f"{field} must be a JSON object")
    return value


def _latest_formal_draft(workspace: Path, slug: str) -> Path:
    candidates = [
        path
        for path in (Path(workspace) / "drafts").glob(f"{slug}-*.md")
        if path.is_file()
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


async def run_existing_legacy_sectional_shadow(
    *,
    action_id: int,
    topic: str,
    author: str,
    workspace: Path,
    slug: str,
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
        raise SectionalLegacyAdapterError(
            f"cannot snapshot formal Legacy pair: {exc}"
        ) from exc

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
    brief = contracts["brief"]
    tier = str(brief.get("tier") or "").strip()
    if not tier:
        raise SectionalLegacyAdapterError("write brief tier is missing")

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
        raise SectionalLegacyAdapterError(
            "existing-pair shadow unexpectedly allowed promotion"
        )

    result["formal_pair"] = {
        "draft_path": str(formal_draft_path),
        "claim_path": str(formal_claim_path),
        "draft_sha256_before": hashlib.sha256(draft_before).hexdigest(),
        "draft_sha256_after": hashlib.sha256(draft_after).hexdigest(),
        "claim_sha256_before": hashlib.sha256(claim_before).hexdigest(),
        "claim_sha256_after": hashlib.sha256(claim_after).hexdigest(),
        "unchanged": True,
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
