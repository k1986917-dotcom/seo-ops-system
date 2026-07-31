"""Print a read-only sectional context shadow report for one Legacy action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from seo_ops.services.sectional_context import (
    build_candidate_registry,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_writing import build_contract_bundle_from_brief


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read existing Legacy writing inputs and print a shadow-only "
            "section candidate/opportunity report. No files are modified."
        )
    )
    parser.add_argument("--topic", required=True)
    parser.add_argument("--tier", default="")
    parser.add_argument("--intent", required=True)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--internal-links", type=Path, required=True)
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--evidence-cards", type=Path, required=True)
    parser.add_argument("--current-url", default="")
    parser.add_argument("--current-slug", default="")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the complete shadow bundle instead of the concise summary.",
    )
    return parser


def _summary(shadow: dict[str, Any]) -> dict[str, Any]:
    def gate_summary(gate: dict[str, Any]) -> dict[str, Any]:
        return {
            "opportunity_state": gate["opportunity_state"],
            "candidate_count": gate["candidate_count"],
            "min_required": gate["min_required"],
            "max_allowed": gate["max_allowed"],
            "reason_code": gate["reason_code"],
            "selected_ids": gate["selected_ids"],
        }

    def candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            key: candidate[key]
            for key in (
                "candidate_id",
                "product_id",
                "title",
                "score",
                "section_match_count",
                "matched_terms",
            )
            if key in candidate
            and candidate[key] != ""
            and candidate[key] != []
        }

    manifest_by_id = {
        item["section_id"]: item
        for item in shadow["context_manifest"]["sections"]
    }
    sections = []
    for resolved in shadow["section_link_contracts"]["sections"]:
        manifest = manifest_by_id[resolved["section_id"]]
        sections.append({
            "section_id": resolved["section_id"],
            "heading": manifest["heading"],
            "reader_stage": manifest["reader_stage"],
            "article_gate": gate_summary(resolved["article_links"]),
            "product_gate": gate_summary(resolved["product_links"]),
            "evidence_gate": gate_summary(resolved["external_citations"]),
            "article_candidates": [
                candidate_summary(item) for item in manifest["article_candidates"]
            ],
            "product_candidates": [
                candidate_summary(item) for item in manifest["product_candidates"]
            ],
            "product_rejections": manifest["product_rejections"],
            "brief_catalog_conflicts": manifest["brief_catalog_conflicts"],
            "evidence_candidates": [
                candidate_summary(item) for item in manifest["evidence_candidates"]
            ],
        })
    return {
        "shadow_report": shadow["shadow_report"],
        "sections": sections,
    }


def main() -> int:
    args = _parser().parse_args()
    bundle = build_contract_bundle_from_brief(
        topic=args.topic,
        tier=args.tier,
        intent=args.intent,
        brief_text=args.brief.read_text(encoding="utf-8"),
    )
    registry = build_candidate_registry(
        internal_links_map=args.internal_links.read_text(encoding="utf-8"),
        live_products_report=args.products.read_text(encoding="utf-8"),
        evidence_cards=json.loads(args.evidence_cards.read_text(encoding="utf-8")),
        current_url=args.current_url,
        current_slug=args.current_slug,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    output = shadow if args.full else _summary(shadow)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
