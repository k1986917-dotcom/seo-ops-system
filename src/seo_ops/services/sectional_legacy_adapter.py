"""Legacy W0 adapter for sectional shadow generation and controlled promotion."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
