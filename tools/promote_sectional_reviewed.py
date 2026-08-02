#!/usr/bin/env python3
"""Preflight or execute an operator-reviewed sectional promotion override.

The only comparison blocker an operator may override is exactly
``["excessive_ai_retries"]``.  The default command is a read-only preflight
that never writes files.  ``--execute`` additionally requires explicit
comparison and assembly SHA confirmations and then runs the fail-closed
promotion with the operator review recorded in the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from seo_ops.__main__ import _load_local_env
from seo_ops.config import get_settings
from seo_ops.services.sectional_assembly import load_sectional_assembly
from seo_ops.services.sectional_rollout import (
    build_rollout_policy,
    load_shadow_comparison,
    operator_override_eligibility,
    promote_sectional_assembly,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    value = re.sub(r"-+", "-", value)
    if value:
        return value
    return f"topic-{hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:12]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action-id", type=int, required=True)
    parser.add_argument("--reviewer", required=True, help="operator reviewer name")
    parser.add_argument(
        "--reason",
        required=True,
        help="human review justification (at least 20 characters)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually promote; requires both SHA confirmations",
    )
    parser.add_argument(
        "--confirm-comparison-sha",
        default="",
        help="64-hex comparison report_sha256 that must match the current comparison",
    )
    parser.add_argument(
        "--confirm-assembly-sha",
        default="",
        help="64-hex assembly_sha256 that must match the current assembly",
    )
    return parser


def _action_row(database_path: Path, action_id: int) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT id, site_id, legacy_stage, workflow_status, target_ref "
            "FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SystemExit(f"Action #{action_id} does not exist")
    return dict(row)


def _formal_pair(workspace: Path, slug: str) -> tuple[Path, Path]:
    candidates = sorted(
        (workspace / "drafts").glob(f"{slug}-*.md"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    if not candidates:
        raise SystemExit(f"formal Legacy draft is missing for {slug}")
    draft = candidates[-1]
    claim = workspace / "research" / f"claim-ledger-{slug}.json"
    if not claim.exists():
        raise SystemExit(f"formal claim ledger is missing for {slug}")
    return draft, claim


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action_id <= 0:
        raise SystemExit("action-id must be a positive integer")
    _load_local_env()
    try:
        get_settings.cache_clear()
    except AttributeError:
        pass
    settings = get_settings()
    action = _action_row(settings.database_path, args.action_id)
    topic = action["target_ref"]
    if not isinstance(topic, str) or not topic.strip():
        raise SystemExit("Action target_ref is empty")
    slug = _slugify(topic)
    workspace = (
        Path(settings.data_dir)
        / "legacy_workflow"
        / "laserpointerhub"
        / "runs"
        / f"action-{args.action_id}"
        / "current"
        / "laserpointerhub"
    )
    comparison = load_shadow_comparison(workspace, slug, args.action_id)
    assembly = load_sectional_assembly(workspace, slug)
    draft_path, claim_path = _formal_pair(workspace, slug)
    draft_sha = _sha256(draft_path)
    claim_sha = _sha256(claim_path)
    policy = build_rollout_policy(
        settings.sectional_writing_mode,
        list(settings.sectional_action_allowlist),
    )

    comparison_sha = comparison["report_sha256"] if comparison else ""
    assembly_sha = assembly["assembly_sha256"] if assembly else ""
    review = {
        "approved": True,
        "action_id": args.action_id,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "comparison_sha256": comparison_sha,
        "assembly_sha256": assembly_sha,
    }

    eligibility: dict[str, Any] = {"eligible": False, "reason": "comparison or assembly missing"}
    if comparison is not None and assembly is not None:
        formal_matches = True
        formal_reason = ""
        if draft_sha != comparison.get("old", {}).get("draft_sha256"):
            formal_matches = False
            formal_reason = "formal draft changed since the shadow comparison"
        try:
            claim_json = json.loads(claim_path.read_text(encoding="utf-8"))
            if claim_json.get("draft_sha256") != comparison.get("old", {}).get("draft_sha256"):
                formal_matches = False
                formal_reason = "formal claim ledger changed since the shadow comparison"
        except (OSError, json.JSONDecodeError):
            formal_matches = False
            formal_reason = "formal claim ledger is not readable JSON"
        eligibility = operator_override_eligibility(
            action_id=args.action_id,
            policy=policy,
            comparison=comparison,
            assembly=assembly,
            operator_review=review,
        )
        if eligibility["eligible"] and not formal_matches:
            eligibility = {"eligible": False, "reason": formal_reason}

    promotions_base = (
        workspace / "drafts" / "sectional" / slug / "promotions" / f"action-{args.action_id}"
    )
    existing = [
        int(item.name.split("-")[-1])
        for item in promotions_base.glob("promotion-*")
        if item.is_dir() and item.name.split("-")[-1].isdigit()
    ]
    promotion_root = promotions_base / f"promotion-{max(existing, default=0) + 1}"

    payload: dict[str, Any] = {
        "action": action,
        "formal_draft_path": str(draft_path),
        "formal_draft_sha256": draft_sha,
        "formal_claim_path": str(claim_path),
        "formal_claim_sha256": claim_sha,
        "comparison_path": str(
            workspace
            / "drafts"
            / "sectional"
            / slug
            / f"shadow-comparison-action-{args.action_id}.json"
        ),
        "comparison_report_sha256": comparison_sha,
        "assembly_sha256": assembly_sha,
        "blockers": list(comparison.get("blockers") or []) if comparison else [],
        "operator_override_eligible": bool(eligibility["eligible"]),
        "operator_override_reason": eligibility["reason"],
        "operator_review": review,
        "promotion_root": str(promotion_root),
        "execute": bool(args.execute),
    }

    if not args.execute:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not eligibility["eligible"]:
        raise SystemExit(f"operator override is not allowed: {eligibility['reason']}")
    if not _SHA256.fullmatch(args.confirm_comparison_sha or ""):
        raise SystemExit("--confirm-comparison-sha must be a 64-hex SHA")
    if not _SHA256.fullmatch(args.confirm_assembly_sha or ""):
        raise SystemExit("--confirm-assembly-sha must be a 64-hex SHA")
    if args.confirm_comparison_sha != comparison_sha:
        raise SystemExit("--confirm-comparison-sha does not match the current comparison")
    if args.confirm_assembly_sha != assembly_sha:
        raise SystemExit("--confirm-assembly-sha does not match the current assembly")

    manifest = promote_sectional_assembly(
        workspace=workspace,
        slug=slug,
        action_id=args.action_id,
        policy=policy,
        comparison=comparison,
        assembly=assembly,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        expected_draft_sha256=draft_sha,
        expected_claim_sha256=claim_sha,
        operator_review=review,
    )
    payload["promotion_manifest"] = manifest
    payload["promotion_manifest_path"] = str(promotion_root / "promotion-manifest.json")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
