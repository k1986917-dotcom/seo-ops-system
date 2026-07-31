import copy
from pathlib import Path

import pytest

from seo_ops.services.sectional_writing import (
    ContractValidationError,
    build_contract_bundle,
    build_contract_bundle_from_brief,
    load_contract_bundle,
    parse_brief_section_specs,
    persist_contract_bundle,
    stable_section_id,
    validate_contract_bundle,
    validate_section_link_contracts,
)


def _bundle():
    return build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Help contractors choose and use a suitable pointing tool.",
        outline=[
            "Why Ceiling Layout Needs Clear Pointing",
            "Compare Red vs Green Visibility",
            "Choosing a Tool for the Work Area",
            "Safety and Compliance Limits",
            "How to Use the Tool on Site",
        ],
        guidance="Keep recommendations evidence-bound.",
    )


def test_contract_bundle_is_stable_and_aligned():
    first = _bundle()
    second = _bundle()

    assert first == second
    blueprint = first["article_blueprint"]
    sections = first["section_contracts"]
    links = first["section_link_contracts"]
    assert (
        blueprint["content_language"]
        == sections["content_language"]
        == links["content_language"]
        == "en"
    )
    assert blueprint["section_order"] == sections["section_order"] == links["section_order"]
    assert blueprint["section_order"][0] == stable_section_id(
        1, "Why Ceiling Layout Needs Clear Pointing"
    )
    assert len(set(blueprint["section_order"])) == 5
    assert all(
        section["target_words"] == {"min": 350, "max": 500} for section in sections["sections"]
    )


def test_section_ids_survive_outline_reordering():
    first = _bundle()["article_blueprint"]["sections"]
    reordered = build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Help contractors choose and use a suitable pointing tool.",
        outline=[
            "Safety and Compliance Limits",
            "Why Ceiling Layout Needs Clear Pointing",
            "Compare Red vs Green Visibility",
            "Choosing a Tool for the Work Area",
            "How to Use the Tool on Site",
        ],
    )["article_blueprint"]["sections"]

    first_ids = {section["heading"]: section["section_id"] for section in first}
    reordered_ids = {section["heading"]: section["section_id"] for section in reordered}
    assert first_ids == reordered_ids


def test_reader_stages_control_initial_product_link_policy():
    bundle = _bundle()
    sections = bundle["section_contracts"]["sections"]
    links = bundle["section_link_contracts"]["sections"]

    by_heading = {section["heading"]: section for section in sections}
    link_by_id = {section["section_id"]: section for section in links}

    choosing = by_heading["Choosing a Tool for the Work Area"]
    assert choosing["reader_stage"] == "select"
    assert choosing["product_link_allowed"] is True
    choosing_gate = link_by_id[choosing["section_id"]]["product_links"]
    assert choosing_gate["opportunity_state"] == "unassessed"
    assert choosing_gate["min_required"] is None

    comparison = by_heading["Compare Red vs Green Visibility"]
    assert comparison["reader_stage"] == "compare"
    assert comparison["product_link_allowed"] is True
    assert (
        link_by_id[comparison["section_id"]]["product_links"]["opportunity_state"] == "unassessed"
    )

    safety = by_heading["Safety and Compliance Limits"]
    assert safety["reader_stage"] == "verify"
    assert safety["product_link_allowed"] is False
    safety_gate = link_by_id[safety["section_id"]]["product_links"]
    assert safety_gate["opportunity_state"] == "none"
    assert safety_gate["min_required"] == 0
    assert safety_gate["reason_code"] == "section_role_prohibits_product_link"


def test_reader_stage_inference_handles_real_outline_phrasing():
    bundle = build_contract_bundle(
        topic="Professional Tools",
        tier="Cluster Content",
        intent="Explain selection, use, and mistakes.",
        outline=[
            "Why This Professional Use Case Is Different",
            "What to Look For in a Professional Tool",
            "Common Mistakes When Using the Tool",
        ],
    )
    stages = {
        item["heading"]: item["reader_stage"] for item in bundle["section_contracts"]["sections"]
    }

    assert stages["Why This Professional Use Case Is Different"] == "discover"
    assert stages["What to Look For in a Professional Tool"] == "select"
    assert stages["Common Mistakes When Using the Tool"] == "verify"


def test_recommendation_heading_enters_product_selection_stage():
    bundle = build_contract_bundle(
        topic="Recommendations",
        tier="Cluster Content",
        intent="Recommend a suitable option.",
        outline=["Top Recommendations with Safety Compliance Filter"],
    )
    section = bundle["section_contracts"]["sections"][0]

    assert section["reader_stage"] == "select"
    assert section["product_link_allowed"] is True
    assert section["product_constraints"] == []


def test_legacy_brief_points_stay_advisory_until_product_rules_are_approved():
    brief = """## 3. Recommended Outline (H2)

```
H2: Key Considerations When Choosing a Pointer (600 words)
- Visibility and durability
- FDA ≤5mW limit

H2: Top Recommendations with Safety Compliance Filter (500 words)
- 我们只推荐5mW绿光指示笔
- Explain why higher power is not appropriate

H2: Safety and Regulatory Compliance (300 words)
- Explain the legal boundary
```
"""
    specs = parse_brief_section_specs(brief)
    assert specs[0] == {
        "heading": "Key Considerations When Choosing a Pointer",
        "target_words": 600,
        "bullets": ["Visibility and durability", "FDA ≤5mW limit"],
    }

    bundle = build_contract_bundle_from_brief(
        topic="Ceiling Work",
        tier="Cluster Content",
        intent="Recommend a compliant product.",
        brief_text=brief,
    )
    sections = {item["heading"]: item for item in bundle["section_contracts"]["sections"]}
    considerations = sections["Key Considerations When Choosing a Pointer"]
    recommendations = sections["Top Recommendations with Safety Compliance Filter"]
    safety = sections["Safety and Regulatory Compliance"]

    assert considerations["target_words"] == {"min": 450, "max": 750}
    assert considerations["must_answer"] == ["Key Considerations When Choosing a Pointer"]
    assert considerations["brief_points"] == [
        "Visibility and durability",
        "FDA ≤5mW limit",
    ]
    assert considerations["product_constraints"] == []
    assert recommendations["reader_stage"] == "select"
    assert recommendations["brief_points"] == [
        "Explain why higher power is not appropriate",
    ]
    assert recommendations["brief_points_rejected"] == [
        {
            "text": "我们只推荐5mW绿光指示笔",
            "reason_code": "non_english_brief_point",
        }
    ]
    assert recommendations["product_constraints"] == []
    assert safety["reader_stage"] == "verify"
    assert safety["product_link_allowed"] is False


def test_numbered_bold_legacy_outline_rebuilds_english_h2_contracts():
    brief = """# Research Brief

## 3. Recommended Outline

**Target word count:** 1800–2500 words

**H2 Structure:**
1. **H2: Why Ceiling Work Is Different** — 中文历史说明，不进入英文写作上下文
2. **H2: Class 2 vs Class 3R — Which Option Fits the Task?** — Compare the options with evidence
3. **H2: Frequently Asked Questions** — 嵌入历史问题

## 4. Supporting Elements
"""
    specs = parse_brief_section_specs(brief)

    assert [item["heading"] for item in specs] == [
        "Why Ceiling Work Is Different",
        "Class 2 vs Class 3R — Which Option Fits the Task?",
        "Frequently Asked Questions",
    ]
    assert specs[0]["bullets"] == ["中文历史说明，不进入英文写作上下文"]
    assert specs[1]["bullets"] == ["Compare the options with evidence"]

    bundle = build_contract_bundle_from_brief(
        topic="Ceiling Work",
        tier="Cluster Content",
        intent="Explain selection and use.",
        brief_text=brief,
    )
    sections = bundle["section_contracts"]["sections"]

    assert [item["heading"] for item in sections] == [
        "Why Ceiling Work Is Different",
        "Class 2 vs Class 3R — Which Option Fits the Task?",
    ]
    assert sections[0]["brief_points"] == []
    assert sections[0]["brief_points_rejected"] == [
        {
            "text": "中文历史说明，不进入英文写作上下文",
            "reason_code": "non_english_brief_point",
        }
    ]
    assert sections[1]["brief_points"] == ["Compare the options with evidence"]


def test_brief_with_only_deferred_frame_headings_is_rejected():
    brief = """## 3. Recommended Outline (H2)

```
H2: Introduction
H2: Key Takeaways
H2: Conclusion
H2: Frequently Asked Questions
```
"""
    with pytest.raises(ContractValidationError, match="only deferred"):
        build_contract_bundle_from_brief(
            topic="Deferred article frame",
            tier="Cluster Content",
            intent="Build an article frame.",
            brief_text=brief,
        )


def test_only_structured_approved_product_constraints_become_hard_rules():
    heading = "Top Recommendations"
    brief = f"""## 3. Recommended Outline (H2)

```
H2: {heading} (400 words)
- Compare products from the live catalog
```
"""
    approved = {
        heading: [
            {
                "field": "battery_platform",
                "operator": "equals",
                "value": "40V",
                "unit": "",
                "source": "site_policy",
                "reason": "The site sells this battery platform.",
            }
        ]
    }
    bundle = build_contract_bundle_from_brief(
        topic="Garden tools",
        tier="Cluster Content",
        intent="Recommend compatible tools.",
        brief_text=brief,
        approved_product_constraints=approved,
    )

    section = bundle["section_contracts"]["sections"][0]
    assert section["product_constraints"] == approved[heading]


def test_non_english_output_language_is_rejected():
    with pytest.raises(ContractValidationError, match="content_language=en"):
        build_contract_bundle(
            topic="Garden tools",
            tier="Cluster Content",
            intent="Recommend tools.",
            outline=["Top Recommendations"],
            content_language="zh",
        )


def test_mixed_legacy_faq_heading_is_normalized_to_english():
    brief = """## 3. Recommended Outline (H2)

```
H2: FAQ (3-4个问答)
- Answer common questions
```
"""
    specs = parse_brief_section_specs(brief)

    assert specs[0]["heading"] == "FAQ"


def test_fully_non_english_heading_is_rejected():
    brief = """## 3. Recommended Outline (H2)

```
H2: 产品推荐
- Compare products
```
"""
    with pytest.raises(ContractValidationError, match="H2 must be English"):
        parse_brief_section_specs(brief)


def test_unassessed_gate_cannot_silently_default_to_zero():
    bundle = _bundle()
    broken = copy.deepcopy(bundle["section_link_contracts"])
    broken["sections"][0]["article_links"]["min_required"] = 0

    with pytest.raises(ContractValidationError, match="min_required=null"):
        validate_section_link_contracts(broken, bundle["section_contracts"])


def test_required_gate_needs_candidate_and_minimum_one():
    bundle = _bundle()
    broken = copy.deepcopy(bundle["section_link_contracts"])
    gate = broken["sections"][0]["article_links"]
    gate.update(
        {
            "opportunity_state": "required",
            "candidate_count": 0,
            "min_required": 1,
            "reason_code": "high_relevance_article_candidate",
        }
    )

    with pytest.raises(ContractValidationError, match="needs a candidate"):
        validate_section_link_contracts(broken, bundle["section_contracts"])


def test_duplicate_outline_heading_fails_closed():
    with pytest.raises(ContractValidationError, match="duplicate outline heading"):
        build_contract_bundle(
            topic="Duplicate",
            tier="Cluster Content",
            intent="Test",
            outline=["Safety", " safety "],
        )


def test_bundle_round_trip_is_deterministic(tmp_path):
    bundle = _bundle()
    paths = persist_contract_bundle(tmp_path, "ceiling-layout", bundle)
    loaded = load_contract_bundle(tmp_path, "ceiling-layout")

    assert loaded == bundle
    assert set(paths) == {
        "article_blueprint",
        "section_contracts",
        "section_link_contracts",
    }
    first_bytes = {name: Path(path).read_bytes() for name, path in paths.items()}
    persist_contract_bundle(tmp_path, "ceiling-layout", bundle)
    second_bytes = {name: Path(path).read_bytes() for name, path in paths.items()}
    assert first_bytes == second_bytes


def test_bundle_validation_rejects_misaligned_section_order():
    bundle = _bundle()
    broken = copy.deepcopy(bundle)
    broken["section_contracts"]["section_order"] = list(
        reversed(broken["section_contracts"]["section_order"])
    )

    with pytest.raises(ContractValidationError, match="IDs/order"):
        validate_contract_bundle(broken)


def test_bundle_validation_rejects_cross_file_topic_mismatch():
    bundle = _bundle()
    broken = copy.deepcopy(bundle)
    broken["section_link_contracts"]["topic"] = "Different topic"

    with pytest.raises(ContractValidationError, match="topic does not match"):
        validate_contract_bundle(broken)


def test_persist_rolls_back_existing_bundle_on_partial_replace_failure(tmp_path, monkeypatch):
    from seo_ops.services import sectional_writing as sw

    original = _bundle()
    paths = persist_contract_bundle(tmp_path, "ceiling-layout", original)
    snapshots = {name: Path(path).read_bytes() for name, path in paths.items()}

    changed = build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Changed intent.",
        outline=[
            "Why Ceiling Layout Needs Clear Pointing",
            "Choosing a Tool for the Work Area",
        ],
    )
    real_replace = sw.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated contract bundle replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(sw.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="simulated contract bundle"):
        persist_contract_bundle(tmp_path, "ceiling-layout", changed)

    assert {name: Path(path).read_bytes() for name, path in paths.items()} == snapshots
