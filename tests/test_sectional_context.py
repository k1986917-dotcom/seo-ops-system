import copy
from pathlib import Path

import pytest

from seo_ops.services.sectional_context import (
    build_candidate_registry,
    parse_article_registry,
    parse_product_registry,
    persist_shadow_context,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_writing import (
    build_contract_bundle,
    build_contract_bundle_from_brief,
    persist_contract_bundle,
    validate_section_link_contracts,
)

INTERNAL_LINKS = """# Internal Links Map

| # | Title | URL | Primary Keyword |
|---|---|---|---|
| 1 | Current Guide | https://example.com/blog/current | current topic |
| 2 | Product Selection Guide | https://example.com/blog/product-selection | product selection |
| 3 | Safety Guide | https://example.com/blog/safety | safety compliance |
| 4 | Duplicate | https://example.com/blog/safety | duplicate |
| 5 | Broken | not-a-url | broken |
"""

LASER_PRODUCTS = """# Products

| SKU | Title | URL | Price | Power | Wavelength |
|---|---|---|---|---|---|
| L100 | High Power Green Laser Pointer | https://example.com/p-L100 | 199 | 2000mW | 520nm green |
| L200 | Blue Laser Pointer | https://example.com/p-L200 | 149 | 1600mW | 450nm blue |
| L300 | Conflicting Laser | https://example.com/p-L300 | 99 | 1200mW | 650nm red |

### L100 — High Power Green Laser Pointer
- **URL**: https://example.com/p-L100
- **Features**: Long-range green beam

### L300 — Conflicting Laser
- **Wavelength**: 532nm green
"""

INKJET_PRODUCTS = """# Products

| Model | Name | URL | Print Height | Ink Type | Speed |
|---|---|---|---|---|---|
| IJ-10 | Handheld Inkjet Printer for Cartons | https://printer.example/ij-10 | 12.7mm | Solvent | 60m/min |
| IJ-25 | Large Character Inkjet Printer | https://printer.example/ij-25 | 25.4mm | Water-based | 45m/min |
"""

GARDEN_PRODUCTS = """# Products

| Product ID | Product Name | Product URL | Battery Platform | Cutting Width |
|---|---|---|---|---|
| GT-40 | 40V Cordless Lawn Mower | https://garden.example/gt-40 | 40V | 46cm |
| HT-40 | 40V Cordless Hedge Trimmer | https://garden.example/ht-40 | 40V | 60cm |
"""

EVIDENCE = {
    "all_cards": [
        {
            "evidence_id": "ev_select",
            "source_url": "https://source.example/select",
            "support": "Product selection should match the intended application.",
            "concepts": ["product selection", "application"],
            "claim_types": ["selection"],
            "required": False,
        },
        {
            "evidence_id": "ev_safety",
            "source_url": "https://source.example/safety",
            "support": "Safety and compliance requirements must be checked.",
            "concepts": ["safety", "compliance"],
            "claim_types": ["regulatory"],
            "required": True,
        },
    ]
}


def _laser_bundle():
    return build_contract_bundle(
        topic="High Power Laser Pointer Buying Guide",
        tier="Cluster Content",
        intent="Help buyers compare high power laser pointers.",
        outline=[
            "Compare High Power Laser Pointer Options",
            "Top High Power Laser Pointer Recommendations",
            "Safety and Compliance",
        ],
    )


def _registry(product_report=LASER_PRODUCTS):
    return build_candidate_registry(
        internal_links_map=INTERNAL_LINKS,
        product_report=product_report,
        evidence_cards=EVIDENCE,
        current_url="https://example.com/blog/current/",
        current_slug="current",
    )


def test_article_registry_rejects_self_duplicate_and_invalid_urls():
    registry = parse_article_registry(
        INTERNAL_LINKS,
        current_url="https://example.com/blog/current/",
        current_slug="current",
    )

    assert [item["title"] for item in registry["candidates"]] == [
        "Product Selection Guide",
        "Safety Guide",
    ]
    assert [item["reason_code"] for item in registry["rejected"]] == [
        "self_link",
        "duplicate_article_url",
        "invalid_article_url",
    ]


def test_evidence_registry_accepts_markdown_wrapped_source_url():
    registry = build_candidate_registry(
        internal_links_map=INTERNAL_LINKS,
        product_report=LASER_PRODUCTS,
        evidence_cards={
            "all_cards": [
                {
                    "evidence_id": "ev_markdown_url",
                    "source_url": "[Source title](https://source.example/markdown)",
                    "support": "A concise supported statement.",
                    "concepts": ["selection"],
                    "claim_types": ["guidance"],
                    "required": False,
                }
            ]
        },
    )

    assert registry["evidence"]["candidates"][0]["url"] == (
        "https://source.example/markdown"
    )


def test_evidence_registry_preserves_quote_vs_key_finding_basis():
    cards = {
        "all_cards": [
            {
                "evidence_id": "ev_verified",
                "source_url": "https://source.example/verified",
                "support": "Verified exact source language.",
                "support_basis": "verified_quote",
                "concepts": ["source"],
                "claim_types": ["regulatory"],
                "required": False,
            },
            {
                "evidence_id": "ev_quote",
                "source_url": "https://source.example/quote",
                "support": "Exact source language.",
                "support_basis": "quote",
                "concepts": ["source"],
                "claim_types": ["regulatory"],
                "required": False,
            },
            {
                "evidence_id": "ev_note",
                "source_url": "https://source.example/note",
                "support": "Synthesized research note.",
                "concepts": ["research"],
                "claim_types": ["guidance"],
                "required": False,
            },
        ]
    }
    registry = build_candidate_registry(
        internal_links_map=INTERNAL_LINKS,
        product_report=LASER_PRODUCTS,
        evidence_cards=cards,
    )

    by_id = {
        item["evidence_id"]: item
        for item in registry["evidence"]["candidates"]
    }
    assert by_id["ev_verified"]["support_basis"] == "verified_quote"
    assert by_id["ev_quote"]["support_basis"] == "quote"
    assert by_id["ev_note"]["support_basis"] == "key_finding"


def test_common_site_topic_words_cannot_create_false_article_opportunity():
    links = """# Internal Links

| Title | URL | Primary Keyword |
|---|---|---|
| Laser Pointer Buying Guide | https://example.com/blog/buying | laser pointer |
| Laser Pointer Battery Guide | https://example.com/blog/battery | laser pointer |
| Laser Pointer Color Guide | https://example.com/blog/color | laser pointer |
| Laser Pointer Range Guide | https://example.com/blog/range | laser pointer |
| Laser Pointer Bird Deterrent Guide | https://example.com/blog/birds | laser pointer |
"""
    bundle = build_contract_bundle(
        topic="Laser Pointer Guide",
        tier="Cluster Content",
        intent="Explain common worksite mistakes.",
        outline=["Common Mistakes During Professional Installation"],
    )
    registry = build_candidate_registry(
        internal_links_map=links,
        product_report=LASER_PRODUCTS,
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    manifest = shadow["context_manifest"]["sections"][0]
    gate = shadow["section_link_contracts"]["sections"][0]["article_links"]

    assert {"laser", "pointer"} <= set(
        registry["article_profile"]["common_article_tokens"]
    )
    assert manifest["article_candidates"] == []
    assert gate["opportunity_state"] == "none"
    assert gate["candidate_count"] == 0
    assert gate["reason_code"] == "no_relevant_article_candidates"


def test_laser_catalog_is_parsed_as_generic_attributes():
    registry = parse_product_registry(LASER_PRODUCTS)
    products = {item["product_id"]: item for item in registry["candidates"]}

    assert products["L100"]["attributes"]["table"]["power"] == "2000mW"
    assert products["L100"]["attributes"]["table"]["wavelength"] == "520nm green"
    assert "url" not in products["L100"]["attributes"]["details"]
    assert "https" not in products["L100"]["search_text"].casefold()
    assert products["L300"]["attribute_conflicts"] == [
        {
            "field": "wavelength",
            "table": "650nm red",
            "detail": "532nm green",
        }
    ]


def test_title_spec_conflict_is_detected_without_industry_specific_logic():
    report = """# Products

| SKU | Title | URL | Wavelength |
|---|---|---|---|
| X1 | Tactical Green Product 532nm | https://example.com/x1 | 650nm red |
"""
    product = parse_product_registry(report)["candidates"][0]

    assert product["attribute_conflicts"] == [
        {
            "field": "wavelength",
            "title": ["532"],
            "attribute": ["650"],
            "unit": "nm",
        }
    ]


def test_inkjet_catalog_needs_no_laser_specific_columns():
    registry = parse_product_registry(INKJET_PRODUCTS)

    assert [item["product_id"] for item in registry["candidates"]] == [
        "IJ-10",
        "IJ-25",
    ]
    assert registry["candidates"][0]["attributes"]["table"] == {
        "print_height": "12.7mm",
        "ink_type": "Solvent",
        "speed": "60m/min",
    }


def test_garden_catalog_needs_no_laser_specific_columns():
    registry = _registry(GARDEN_PRODUCTS)

    assert registry["catalog_profile"]["product_count"] == 2
    assert registry["catalog_profile"]["attribute_coverage"] == {
        "battery_platform": 2,
        "cutting_width": 2,
    }


def test_candidate_registry_is_deterministic():
    assert _registry() == _registry()


def test_live_catalog_drives_product_candidates():
    bundle = _laser_bundle()
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )
    select_section = next(
        item
        for item in shadow["context_manifest"]["sections"]
        if item["reader_stage"] == "select"
    )
    product_ids = [item["product_id"] for item in select_section["product_candidates"]]

    assert "L100" in product_ids
    assert "L200" in product_ids
    assert "L300" not in product_ids


def test_english_brief_cannot_turn_5mw_green_prose_into_product_filter():
    brief = """## 3. Recommended Outline (H2)

```
H2: Top Recommendations (500 words)
- Only recommend 5mW green laser pointers
- Compare the available products
```
"""
    bundle = build_contract_bundle_from_brief(
        topic="High Power Laser Pointer Buying Guide",
        tier="Cluster Content",
        intent="Recommend products from the live catalog.",
        brief_text=brief,
    )
    section = bundle["section_contracts"]["sections"][0]
    assert section["product_constraints"] == []

    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )
    manifest = shadow["context_manifest"]["sections"][0]
    gate = shadow["section_link_contracts"]["sections"][0]["product_links"]

    assert manifest["brief_catalog_conflicts"] == [
        {
            "reason_code": "prescriptive_brief_catalog_mismatch",
            "brief_point": "Only recommend 5mW green laser pointers",
            "missing_catalog_specs": ["5mw"],
        }
    ]
    assert gate["opportunity_state"] == "required"
    assert gate["reason_code"] == "catalog_truth_overrides_brief"
    assert gate["min_required"] == 1
    assert gate["candidate_count"] >= 1
    assert all(
        item["fit_level"] == "related_catalog"
        for item in manifest["product_candidates"]
    )
    assert {
        item["product_id"] for item in manifest["product_rejections"]
    } == {"L300"}


def test_non_english_brief_point_is_audited_but_not_used_for_scoring():
    brief = """## 3. Recommended Outline (H2)

```
H2: Top Recommendations (500 words)
- 我们只推荐5mW绿光指示笔
- Compare the available products
```
"""
    bundle = build_contract_bundle_from_brief(
        topic="High Power Laser Pointer Buying Guide",
        tier="Cluster Content",
        intent="Recommend products from the live catalog.",
        brief_text=brief,
    )
    section = bundle["section_contracts"]["sections"][0]
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )

    assert section["brief_points"] == ["Compare the available products"]
    assert section["brief_points_rejected"] == [
        {
            "text": "我们只推荐5mW绿光指示笔",
            "reason_code": "non_english_brief_point",
        }
    ]
    assert shadow["context_manifest"]["sections"][0][
        "brief_catalog_conflicts"
    ] == []


def test_only_approved_generic_constraint_filters_catalog():
    bundle = build_contract_bundle(
        topic="40V Cordless Garden Tools",
        tier="Cluster Content",
        intent="Recommend compatible 40V tools.",
        outline=["Top 40V Garden Tool Recommendations"],
    )
    section = bundle["section_contracts"]["sections"][0]
    section["product_constraints"] = [
        {
            "field": "battery_platform",
            "operator": "equals",
            "value": "40V",
            "unit": "",
            "source": "site_policy",
            "reason": "Keep recommendations inside the site's battery ecosystem.",
        }
    ]
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(GARDEN_PRODUCTS),
    )
    gate = shadow["section_link_contracts"]["sections"][0]["product_links"]

    assert gate["opportunity_state"] == "required"
    assert gate["min_required"] == 1
    assert gate["candidate_count"] == 2


def test_compare_section_requires_one_related_product_when_available():
    bundle = _laser_bundle()
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )
    compare = next(
        item
        for item in bundle["section_contracts"]["sections"]
        if item["reader_stage"] == "compare"
    )
    gate = next(
        item["product_links"]
        for item in shadow["section_link_contracts"]["sections"]
        if item["section_id"] == compare["section_id"]
    )

    assert gate["opportunity_state"] == "required"
    assert gate["min_required"] == 1
    assert gate["candidate_count"] >= 1


def test_verify_section_prohibits_products_and_requires_matching_evidence():
    bundle = _laser_bundle()
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )
    verify = next(
        item
        for item in bundle["section_contracts"]["sections"]
        if item["reader_stage"] == "verify"
    )
    resolved = next(
        item
        for item in shadow["section_link_contracts"]["sections"]
        if item["section_id"] == verify["section_id"]
    )

    assert resolved["product_links"]["opportunity_state"] == "none"
    assert resolved["external_citations"]["opportunity_state"] == "required"
    assert resolved["external_citations"]["selected_ids"] == ["ev_safety"]


def test_resolved_contract_passes_strict_gate_validation():
    bundle = _laser_bundle()
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )

    assert validate_section_link_contracts(
        shadow["section_link_contracts"],
        bundle["section_contracts"],
    ) == shadow["section_link_contracts"]


def test_shadow_report_never_claims_formal_changes():
    bundle = _laser_bundle()
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        _registry(),
    )
    report = shadow["shadow_report"]

    assert report["registry_counts"] == {
        "articles": 2,
        "products": 3,
        "evidence": 2,
    }
    assert report["catalog_profile"]["product_count"] == 3
    assert report["catalog_data_issue_count"] == 1
    assert report["catalog_data_issues"][0] == {
        "issue_id": report["catalog_data_issues"][0]["issue_id"],
        "severity": "error",
        "reason_code": "catalog_attribute_conflict",
        "product_id": "L300",
        "title": "Conflicting Laser",
        "url": "https://example.com/p-L300",
        "conflicts": [
            {
                "field": "wavelength",
                "table": "650nm red",
                "detail": "532nm green",
            }
        ],
        "blocking_for_auto_link": True,
        "suggested_action": (
            "Correct the product title, catalog table or product detail so the "
            "same attribute has one consistent value, then refresh the product "
            "report."
        ),
    }
    assert report["formal_draft_modified"] is False
    assert report["ai_called"] is False


def test_shadow_persist_rolls_back_all_files_on_partial_replace_failure(
    tmp_path, monkeypatch
):
    from seo_ops.services import sectional_context as sc

    contracts = _laser_bundle()
    persist_contract_bundle(tmp_path, "catalog-guide", contracts)
    shadow = resolve_shadow_opportunities(
        contracts["section_contracts"],
        contracts["section_link_contracts"],
        _registry(),
    )
    paths = persist_shadow_context(
        tmp_path,
        "catalog-guide",
        shadow,
        contracts["section_contracts"],
    )
    snapshots = {name: Path(path).read_bytes() for name, path in paths.items()}
    changed = copy.deepcopy(shadow)
    changed["shadow_report"]["section_count"] = 999
    real_replace = sc.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated shadow replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(sc.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="simulated shadow"):
        persist_shadow_context(
            tmp_path,
            "catalog-guide",
            changed,
            contracts["section_contracts"],
        )

    assert {name: Path(path).read_bytes() for name, path in paths.items()} == snapshots
