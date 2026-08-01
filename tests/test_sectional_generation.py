import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from seo_ops.services.sectional_context import (
    build_candidate_registry,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_generation import (
    FRAME_CONCLUSION_MARKER,
    FRAME_FAQ_MARKER,
    FRAME_INTRO_MARKER,
    FRAME_TAKEAWAYS_MARKER,
    SECTION_DECISIONS_MARKER,
    SECTION_MARKDOWN_MARKER,
    SectionGenerationError,
    _writing_safe_evidence_context,
    article_frame_checkpoint_path,
    build_article_frame_package,
    build_article_frame_prompt,
    build_section_generation_package,
    build_section_generation_prompt,
    load_article_frame_checkpoint,
    load_section_checkpoint,
    parse_article_frame_response,
    parse_section_generation_response,
    persist_article_frame_checkpoint,
    persist_section_checkpoint,
    run_article_frame_generation,
    run_section_generation_sequence,
    section_checkpoint_path,
    validate_article_frame_output,
    validate_claim_evidence_strength,
)
from seo_ops.services.sectional_writing import (
    build_contract_bundle,
    build_contract_bundle_from_brief,
)

ARTICLES = """# Internal Links

| Title | URL | Primary Keyword |
|---|---|---|
| Marking Tool Selection Guide | https://example.com/blog/tool-selection | marking tool selection |
| Worksite Safety Guide | https://example.com/blog/worksite-safety | worksite safety compliance |
"""

PRODUCTS = """# Products

| SKU | Title | URL | Application | Reach |
|---|---|---|---|---|
| T100 | Professional Ceiling Marking Tool | https://example.com/p-T100.html | ceiling marking | long range |
| T200 | Compact Worksite Marking Tool | https://example.com/p-T200.html | worksite marking | medium range |
"""

EVIDENCE = {
    "all_cards": [
        {
            "evidence_id": "ev_selection",
            "source_url": "https://source.example/selection",
            "support": "Tool selection should account for the work area and visibility needs.",
            "concepts": ["tool selection", "work area", "visibility"],
            "claim_types": ["guidance"],
            "required": False,
        },
        {
            "evidence_id": "ev_safety",
            "source_url": "https://source.example/safety",
            "support": "Worksite safety procedures should be followed before using marking equipment.",
            "concepts": ["worksite safety", "compliance"],
            "claim_types": ["safety"],
            "required": True,
        },
    ]
}


def _setup():
    bundle = build_contract_bundle(
        topic="Professional Ceiling Marking Tools",
        tier="Cluster Content",
        intent="Help professionals select and use a suitable marking tool.",
        outline=[
            "Why Clear Ceiling Marking Matters",
            "Choosing the Right Ceiling Marking Tool",
            "Worksite Safety and Compliance",
        ],
    )
    for section in bundle["section_contracts"]["sections"]:
        section["target_words"] = {"min": 45, "max": 130}
    registry = build_candidate_registry(
        internal_links_map=ARTICLES,
        product_report=PRODUCTS,
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    return bundle["section_contracts"], shadow


def _package_for_stage(stage: str, previous_summary: str = ""):
    sections, shadow = _setup()
    section = next(item for item in sections["sections"] if item["reader_stage"] == stage)
    return build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section["section_id"],
        previous_summary=previous_summary,
    )


def _response(package, *, omit_required=None, raw_url=False, cjk=False):
    omit_required = set(omit_required or [])
    gates = package["link_gates"]
    used = {key: [] for key in gates}
    paragraph_one_placeholders = []
    paragraph_two_placeholders = []

    article_gate = gates["article_links"]
    if article_gate["opportunity_state"] == "required" and "article_links" not in omit_required:
        candidate_id = article_gate["selected_ids"][0]
        used["article_links"].append(candidate_id)
        paragraph_one_placeholders.append(
            f"[[ARTICLE:{candidate_id}|detailed tool selection guidance]]"
        )

    product_gate = gates["product_links"]
    if product_gate["opportunity_state"] == "required" and "product_links" not in omit_required:
        candidate_id = product_gate["selected_ids"][0]
        used["product_links"].append(candidate_id)
        paragraph_two_placeholders.append(
            f"[[PRODUCT:{candidate_id}|professional ceiling marking tool]]"
        )

    evidence_gate = gates["external_citations"]
    if (
        evidence_gate["opportunity_state"] == "required"
        and "external_citations" not in omit_required
    ):
        candidate_id = evidence_gate["selected_ids"][0]
        used["external_citations"].append(candidate_id)
        paragraph_two_placeholders.append(f"[[CITE:{candidate_id}]]")

    first = (
        "A clear marking process helps the team identify the intended location, "
        "communicate the task, and avoid unnecessary movement around the work area. "
        "The section answers the practical question first and then explains the "
        "selection factors that matter for professional work."
    )
    second = (
        "Professionals should match the tool to the working distance, surrounding "
        "conditions, handling needs, and established site procedures. The choice "
        "should remain easy to explain, suitable for the actual task, and supported "
        "by the available catalog and evidence rather than an invented specification."
    )
    if paragraph_one_placeholders:
        first += " " + " ".join(paragraph_one_placeholders)
    if paragraph_two_placeholders:
        second += " " + " ".join(paragraph_two_placeholders)
    if raw_url:
        second += " https://unapproved.example/item"
    if cjk:
        second += " 这是中文。"

    decisions = {}
    for key, gate in gates.items():
        ids = used[key]
        if ids:
            reason = "used_approved_candidate"
        elif gate["opportunity_state"] == "none":
            reason = gate["reason_code"]
        else:
            reason = "not_needed_for_this_section"
        decisions[key] = {"used_ids": ids, "reason_code": reason}
    return (
        f"{SECTION_MARKDOWN_MARKER}\n"
        f"## {package['heading']}\n\n{first}\n\n{second}\n"
        f"{SECTION_DECISIONS_MARKER}\n"
        f"{json.dumps(decisions, sort_keys=True)}"
    )


def _same_sentence_internal_link_collision_response(package):
    gates = package["link_gates"]
    article_id = gates["article_links"]["selected_ids"][0]
    product_id = gates["product_links"]["selected_ids"][0]
    external_ids = []
    external_placeholder = ""
    if gates["external_citations"]["opportunity_state"] == "required":
        external_ids = [gates["external_citations"]["selected_ids"][0]]
        external_placeholder = f" [[CITE:{external_ids[0]}]]"
    first = (
        "A clear marking process helps the team identify the intended location, "
        "communicate the task, and avoid unnecessary movement around the work area. "
        f"Compare [[ARTICLE:{article_id}|detailed tool selection guidance]] and "
        f"[[PRODUCT:{product_id}|professional ceiling marking tool]] before work begins."
    )
    second = (
        "Professionals should match the tool to the working distance, surrounding "
        "conditions, handling needs, and established site procedures. The choice "
        "should remain easy to explain and supported by the available evidence."
        + external_placeholder
    )
    decisions = {
        "article_links": {
            "used_ids": [article_id],
            "reason_code": "used_approved_candidate",
        },
        "product_links": {
            "used_ids": [product_id],
            "reason_code": "used_approved_candidate",
        },
        "external_citations": {
            "used_ids": external_ids,
            "reason_code": (
                "used_approved_candidate"
                if external_ids
                else gates["external_citations"]["reason_code"]
                if gates["external_citations"]["opportunity_state"] == "none"
                else "not_needed_for_this_section"
            ),
        },
    }
    return (
        f"{SECTION_MARKDOWN_MARKER}\n"
        f"## {package['heading']}\n\n{first}\n\n{second}\n"
        f"{SECTION_DECISIONS_MARKER}\n"
        f"{json.dumps(decisions, sort_keys=True)}"
    )


def _separated_internal_link_response(package):
    response = _same_sentence_internal_link_collision_response(package)
    article_id = package["link_gates"]["article_links"]["selected_ids"][0]
    product_id = package["link_gates"]["product_links"]["selected_ids"][0]
    collision = (
        f"Compare [[ARTICLE:{article_id}|detailed tool selection guidance]] and "
        f"[[PRODUCT:{product_id}|professional ceiling marking tool]] before work begins."
    )
    separated = (
        f"Review [[ARTICLE:{article_id}|detailed tool selection guidance]] before "
        "work begins.\n\n"
        f"Review [[PRODUCT:{product_id}|professional ceiling marking tool]] before "
        "work begins."
    )
    return response.replace(collision, separated)


def _package_from_prompt(user_prompt: str):
    payload = user_prompt.split("SECTION PACKAGE\n", 1)[1]
    return json.JSONDecoder().raw_decode(payload)[0]


def test_generation_package_is_compact_scoped_and_deterministic():
    package = _package_for_stage("select", previous_summary="Earlier section summary.")
    same = _package_for_stage("select", previous_summary="Earlier section summary.")

    assert package == same
    assert package["content_language"] == "en"
    assert package["previous_summary"] == "Earlier section summary."
    assert set(package["candidates"]) == {"articles", "products", "evidence"}
    assert "registry" not in package
    assert all("url" not in item for item in package["candidates"]["products"])
    assert all("url" not in item for item in package["candidates"]["articles"])
    assert all(
        item["support_basis"] == "key_finding" and "concepts" not in item
        for item in package["candidates"]["evidence"]
    )
    assert package["context_char_counts"]["products"] < 3000


def test_generation_prompt_contains_protocol_but_not_full_registry():
    package = _package_for_stage("select")
    prompt = build_section_generation_prompt(package)

    assert SECTION_MARKDOWN_MARKER in prompt["system"]
    assert SECTION_DECISIONS_MARKER in prompt["system"]
    assert "Do not invent URLs" in prompt["system"]
    assert "at most one ARTICLE or PRODUCT placeholder" in prompt["system"]
    assert "set reason_code to used_approved_candidate" in prompt["system"]
    assert "This package has zero source-verified quotes" in prompt["system"]
    assert "do not name OSHA" in prompt["system"]
    assert "do not choose a winner" in prompt["system"]
    assert package["section_id"] in prompt["user"]
    assert "catalog_data_issues" not in prompt["user"]


def test_zero_verified_quotes_remove_named_authority_from_model_contract():
    bundle = build_contract_bundle(
        topic="Worksite Marking Guide",
        tier="Cluster Content",
        intent="Explain how to verify applicable worksite requirements.",
        outline=["What OSHA Says About Worksite Marking"],
    )
    registry = build_candidate_registry(
        internal_links_map=ARTICLES,
        product_report=PRODUCTS,
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    section = bundle["section_contracts"]["sections"][0]
    package = build_section_generation_package(
        bundle["section_contracts"],
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section["section_id"],
    )
    prompt = build_section_generation_prompt(package)

    assert package["section_id"] == section["section_id"]
    assert package["heading"] == "How to Verify Applicable Requirements for Worksite Marking"
    model_contract = json.dumps(
        {
            "heading": package["heading"],
            "reader_question": package["reader_question"],
            "section_goal": package["section_goal"],
            "must_answer": package["must_answer"],
            "must_not_repeat": package["must_not_repeat"],
            "previous_heading": package["previous_heading"],
            "previous_summary": package["previous_summary"],
            "next_heading": package["next_heading"],
        }
    )
    assert "OSHA" not in model_contract
    assert "OSHA" not in prompt["user"]
    assert "This package has zero source-verified quotes" in prompt["system"]

    verified_manifest = copy.deepcopy(shadow["context_manifest"])
    for candidate in verified_manifest["registry"]["evidence"]["candidates"]:
        candidate["support_basis"] = "verified_quote"
    verified_package = build_section_generation_package(
        bundle["section_contracts"],
        shadow["section_link_contracts"],
        verified_manifest,
        section["section_id"],
    )
    verified_prompt = build_section_generation_prompt(verified_package)

    assert verified_package["heading"] == "How to Verify OSHA Requirements for Worksite Marking"
    assert "This package has zero source-verified quotes" not in verified_prompt["system"]


def test_zero_verified_quotes_neutralize_technical_winner_contracts():
    bundle = build_contract_bundle(
        topic="Ceiling Pointing Guide",
        tier="Cluster Content",
        intent="Compare laser options without unsupported safety conclusions.",
        outline=[
            "Class 2 vs Class 3R — Which Laser Class Works for Above-Ceiling Pointing?",
            "Why Green (532nm) Is A Practical Choice for Ceiling Pointing",
        ],
    )
    registry = build_candidate_registry(
        internal_links_map=ARTICLES,
        product_report=PRODUCTS,
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    packages = [
        build_section_generation_package(
            bundle["section_contracts"],
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            section_id,
        )
        for section_id in bundle["section_contracts"]["section_order"]
    ]

    assert packages[0]["heading"] == (
        "How to Compare Class 2 and Class 3R for Above-Ceiling Pointing"
    )
    assert packages[1]["heading"] == "How to Evaluate Green (532nm) for Ceiling Pointing"
    model_contract = json.dumps(packages, ensure_ascii=False)
    assert "Which Laser Class Works" not in model_contract
    assert "A Practical Choice" not in model_contract
    assert "How to Compare Class 2 and Class 3R" in packages[0]["section_goal"]
    assert "How to Evaluate Green (532nm)" in packages[1]["section_goal"]
    assert all(
        "do not choose a winner" in build_section_generation_prompt(package)["system"]
        for package in packages
    )


def test_writing_context_filters_unverified_strong_support_without_relaxing_gate():
    gate = {
        "candidate_count": 2,
        "max_allowed": 2,
        "min_required": 1,
        "opportunity_state": "required",
        "reason_code": "required_or_verification_evidence",
        "rejected": [],
        "selected_ids": ["ev_unsafe", "ev_safe"],
    }
    candidates = [
        {
            "candidate_id": "ev_unsafe",
            "evidence_id": "ev_unsafe",
            "support": "OSHA guidance states that employers must use Class 3R.",
            "support_basis": "quote",
        },
        {
            "candidate_id": "ev_safe",
            "evidence_id": "ev_safe",
            "support": "Verify the current worksite procedure before using the tool.",
            "support_basis": "key_finding",
        },
    ]

    filtered_gate, safe = _writing_safe_evidence_context(
        gate,
        candidates,
        section_id="section-example",
    )

    assert [item["evidence_id"] for item in safe] == ["ev_safe"]
    assert filtered_gate["selected_ids"] == ["ev_safe"]
    assert filtered_gate["candidate_count"] == 1
    assert filtered_gate["max_allowed"] == 1
    assert filtered_gate["min_required"] == 1
    assert filtered_gate["rejected"] == [
        {
            "candidate_id": "ev_unsafe",
            "reason_codes": ["unverified_support_contains_strong_claim"],
        }
    ]
    assert gate["selected_ids"] == ["ev_unsafe", "ev_safe"]


def test_writing_context_filters_blink_response_safety_claim():
    gate = {
        "candidate_count": 2,
        "max_allowed": 2,
        "min_required": 1,
        "opportunity_state": "required",
        "reason_code": "required_or_verification_evidence",
        "rejected": [],
        "selected_ids": ["ev_blink", "ev_process"],
    }
    candidates = [
        {
            "candidate_id": "ev_blink",
            "evidence_id": "ev_blink",
            "support": (
                "Class 2 lasers are generally considered safe because the natural "
                "blink response offers protection."
            ),
            "support_basis": "key_finding",
        },
        {
            "candidate_id": "ev_process",
            "evidence_id": "ev_process",
            "support": "Compare the product label with the documented site procedure.",
            "support_basis": "key_finding",
        },
    ]

    filtered_gate, safe = _writing_safe_evidence_context(
        gate,
        candidates,
        section_id="section-compare",
    )

    assert [item["evidence_id"] for item in safe] == ["ev_process"]
    assert filtered_gate["selected_ids"] == ["ev_process"]
    assert filtered_gate["min_required"] == 1


def test_writing_context_keeps_source_verified_strong_support():
    gate = {
        "candidate_count": 1,
        "max_allowed": 1,
        "min_required": 1,
        "opportunity_state": "required",
        "reason_code": "required_or_verification_evidence",
        "rejected": [],
        "selected_ids": ["ev_verified"],
    }
    candidates = [
        {
            "candidate_id": "ev_verified",
            "evidence_id": "ev_verified",
            "support": "OSHA requires the documented control described in this quote.",
            "support_basis": "verified_quote",
        }
    ]

    filtered_gate, safe = _writing_safe_evidence_context(
        gate,
        candidates,
        section_id="section-example",
    )

    assert safe == candidates
    assert filtered_gate["selected_ids"] == ["ev_verified"]
    assert filtered_gate["rejected"] == []


def test_writing_context_fails_closed_when_required_safe_evidence_is_exhausted():
    gate = {
        "candidate_count": 1,
        "max_allowed": 1,
        "min_required": 1,
        "opportunity_state": "required",
        "reason_code": "required_or_verification_evidence",
        "rejected": [],
        "selected_ids": ["ev_unsafe"],
    }
    candidates = [
        {
            "candidate_id": "ev_unsafe",
            "evidence_id": "ev_unsafe",
            "support": "Class 2 is eye-safe and prevents retinal damage.",
            "support_basis": "key_finding",
        }
    ]

    with pytest.raises(
        SectionGenerationError,
        match=(
            r"section section-example lacks enough writing-safe evidence.*"
            r"required 1, available 0"
        ),
    ):
        _writing_safe_evidence_context(
            gate,
            candidates,
            section_id="section-example",
        )


def test_generation_package_excludes_unsafe_unverified_support_from_prompt():
    sections, shadow = _setup()
    links = copy.deepcopy(shadow["section_link_contracts"])
    manifest = copy.deepcopy(shadow["context_manifest"])
    verify = next(item for item in sections["sections"] if item["reader_stage"] == "verify")
    link = next(item for item in links["sections"] if item["section_id"] == verify["section_id"])
    link["external_citations"].update(
        {
            "candidate_count": 2,
            "max_allowed": 2,
            "min_required": 1,
            "opportunity_state": "required",
            "selected_ids": ["ev_selection", "ev_safety"],
        }
    )
    evidence = manifest["registry"]["evidence"]["candidates"]
    unsafe = next(item for item in evidence if item["evidence_id"] == "ev_selection")
    unsafe["support"] = "OSHA guidance states that employers must use Class 3R for this work."
    unsafe["support_basis"] = "quote"
    safe = next(item for item in evidence if item["evidence_id"] == "ev_safety")
    safe["support"] = "Verify the current worksite procedure before using the tool."
    safe["support_basis"] = "key_finding"

    package = build_section_generation_package(
        sections,
        links,
        manifest,
        verify["section_id"],
    )
    prompt = build_section_generation_prompt(package)

    assert [item["evidence_id"] for item in package["candidates"]["evidence"]] == ["ev_safety"]
    gate = package["link_gates"]["external_citations"]
    assert gate["selected_ids"] == ["ev_safety"]
    assert gate["min_required"] == 1
    assert gate["max_allowed"] == 1
    assert gate["rejected"][-1] == {
        "candidate_id": "ev_selection",
        "reason_codes": ["unverified_support_contains_strong_claim"],
    }
    assert "OSHA guidance states" not in prompt["user"]


def test_related_catalog_product_is_required_without_exact_use_case_claims():
    bundle = build_contract_bundle(
        topic="Industrial Marking Equipment Guide",
        tier="Cluster Content",
        intent="Help buyers compare related catalog products.",
        outline=["Compare Options for Difficult Installations"],
    )
    registry = build_candidate_registry(
        internal_links_map=ARTICLES,
        product_report="""# Products

| SKU | Title | URL | Platform |
|---|---|---|---|
| M100 | Industrial Marking Equipment Model A | https://example.com/p-M100.html | Standard |
| M200 | Industrial Marking Equipment Model B | https://example.com/p-M200.html | Standard |
""",
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    section_id = bundle["section_contracts"]["section_order"][0]
    package = build_section_generation_package(
        bundle["section_contracts"],
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section_id,
    )
    prompt = build_section_generation_prompt(package)

    assert package["link_gates"]["product_links"]["opportunity_state"] == "required"
    assert package["link_gates"]["product_links"]["min_required"] == 1
    assert package["candidates"]["products"]
    assert all(item["fit_level"] == "related_catalog" for item in package["candidates"]["products"])
    assert "related catalog option" in prompt["system"]
    assert "never claim it was designed for" in prompt["system"]


def test_catalog_conflicting_brief_point_stays_out_of_generation_prompt():
    brief = """## 3. Recommended Outline (H2)

```
H2: Top Ceiling Marking Tool Recommendations (120 words)
- Only recommend 5mW green products
- Compare products from the current catalog
```
"""
    bundle = build_contract_bundle_from_brief(
        topic="Professional Ceiling Marking Tools",
        tier="Cluster Content",
        intent="Recommend products from the current catalog.",
        brief_text=brief,
    )
    registry = build_candidate_registry(
        internal_links_map=ARTICLES,
        product_report=PRODUCTS,
        evidence_cards=EVIDENCE,
    )
    shadow = resolve_shadow_opportunities(
        bundle["section_contracts"],
        bundle["section_link_contracts"],
        registry,
    )
    section_id = bundle["section_contracts"]["section_order"][0]
    package = build_section_generation_package(
        bundle["section_contracts"],
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section_id,
    )
    prompt = build_section_generation_prompt(package)

    assert package["brief_points"] == ["Compare products from the current catalog"]
    assert package["brief_points_rejected"] == [
        {
            "text": "Only recommend 5mW green products",
            "reason_code": "brief_catalog_alignment_pending",
        }
    ]
    assert "Only recommend 5mW green products" not in prompt["user"]


def test_valid_section_response_passes_required_link_gates():
    package = _package_for_stage("select")
    output = parse_section_generation_response(_response(package), package)

    assert output["section_id"] == package["section_id"]
    assert output["word_count"] >= package["target_words"]["min"]
    assert output["paragraph_count"] == 2
    assert output["used_ids"]["product_links"]
    assert output["content_language"] == "en"


def test_unknown_product_fit_level_is_rejected():
    sections, shadow = _setup()
    section = next(item for item in sections["sections"] if item["reader_stage"] == "select")
    manifest_section = next(
        item
        for item in shadow["context_manifest"]["sections"]
        if item["section_id"] == section["section_id"]
    )
    assert manifest_section["product_candidates"]
    manifest_section["product_candidates"][0]["fit_level"] = "invented_fit"

    with pytest.raises(SectionGenerationError, match="fit_level"):
        build_section_generation_package(
            sections,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            section["section_id"],
        )


def test_required_link_cannot_be_silently_omitted():
    package = _package_for_stage("select")
    required_types = [
        key
        for key, gate in package["link_gates"].items()
        if gate["opportunity_state"] == "required"
    ]
    assert required_types

    with pytest.raises(SectionGenerationError, match="min_required"):
        parse_section_generation_response(
            _response(package, omit_required={required_types[0]}),
            package,
        )


def test_recommended_zero_requires_an_explicit_reason():
    package = _package_for_stage("discover")
    response = _response(package)
    response = response.replace(
        '"reason_code": "not_needed_for_this_section"',
        '"reason_code": "used"',
        1,
    )

    with pytest.raises(SectionGenerationError, match="rejection reason"):
        parse_section_generation_response(response, package)


def test_used_reason_code_is_canonicalized_from_verified_placeholders():
    package = _package_for_stage("select")
    response = _response(package)
    response = response.replace(
        '"reason_code": "used_approved_candidate"',
        '"reason_code": "evidence_required"',
        1,
    )

    output = parse_section_generation_response(response, package)
    used_type = next(key for key, value in output["used_ids"].items() if value)
    assert output["decisions"][used_type]["reason_code"] == ("used_approved_candidate")


def test_blank_used_reason_code_is_canonicalized():
    package = _package_for_stage("select")
    response = _response(package)
    response = response.replace(
        '"reason_code": "used_approved_candidate"',
        '"reason_code": ""',
        1,
    )

    output = parse_section_generation_response(response, package)
    used_type = next(key for key, value in output["used_ids"].items() if value)
    assert output["decisions"][used_type]["reason_code"] == ("used_approved_candidate")


def test_blank_unused_reason_code_is_rejected():
    package = _package_for_stage("discover")
    response = _response(package)
    response = response.replace(
        '"reason_code": "not_needed_for_this_section"',
        '"reason_code": ""',
        1,
    )

    with pytest.raises(SectionGenerationError, match="reason_code when unused"):
        parse_section_generation_response(response, package)


def test_internal_links_in_separate_sentences_are_split_into_paragraphs():
    package = _package_for_stage("select")
    response = _response(package)
    markdown_text, decisions_text = response.split(SECTION_DECISIONS_MARKER, 1)
    blocks = markdown_text.split("\n\n")
    assert len(blocks) == 3
    dense_response = (
        f"{blocks[0]}\n\n{blocks[1]} {blocks[2]}{SECTION_DECISIONS_MARKER}{decisions_text}"
    )

    output = parse_section_generation_response(dense_response, package)

    assert output["paragraph_count"] == 2
    assert output["used_ids"]["article_links"]
    assert output["used_ids"]["product_links"]
    for block in output["markdown"].split("\n\n")[1:]:
        assert block.count("[[ARTICLE:") + block.count("[[PRODUCT:") <= 1


def test_six_prose_paragraphs_are_compacted_without_losing_links():
    package = _package_for_stage("select")
    response = _response(package)
    markdown_text, decisions_text = response.split(SECTION_DECISIONS_MARKER, 1)
    blocks = markdown_text.split("\n\n")
    article_start = blocks[1].index("[[ARTICLE:")
    article_end = blocks[1].index("]]", article_start) + 2
    article_placeholder = blocks[1][article_start:article_end]
    product_start = blocks[2].index("[[PRODUCT:")
    product_end = blocks[2].index("]]", product_start) + 2
    product_placeholder = blocks[2][product_start:product_end]
    cite_start = blocks[2].index("[[CITE:")
    cite_end = blocks[2].index("]]", cite_start) + 2
    cite_placeholder = blocks[2][cite_start:cite_end]
    paragraphs = [
        "Clear marking keeps the crew focused on the intended ceiling location.",
        f"Teams can review {article_placeholder} before choosing the work method.",
        "Working distance and ambient light both affect practical visibility.",
        f"The approved {product_placeholder} should match the actual task conditions.",
        f"Catalog evidence should support every product statement in the section {cite_placeholder}.",
        "Established site procedures remain part of a responsible selection process.",
    ]
    six_paragraph_response = (
        f"{blocks[0]}\n\n" + "\n\n".join(paragraphs) + f"{SECTION_DECISIONS_MARKER}{decisions_text}"
    )

    output = parse_section_generation_response(six_paragraph_response, package)

    assert output["paragraph_count"] == 5
    assert output["used_ids"]["article_links"]
    assert output["used_ids"]["product_links"]
    assert all(paragraph in output["markdown"] for paragraph in paragraphs[2:])
    for block in output["markdown"].split("\n\n")[1:]:
        assert block.count("[[ARTICLE:") + block.count("[[PRODUCT:") <= 1


def test_single_prose_paragraph_is_split_at_an_existing_sentence_boundary():
    package = _package_for_stage("discover")
    response = _response(package)
    markdown_text, decisions_text = response.split(SECTION_DECISIONS_MARKER, 1)
    blocks = markdown_text.split("\n\n")
    one_paragraph_response = (
        f"{blocks[0]}\n\n{blocks[1]} "
        f"U.S. safety guidance remains relevant for professional work. {blocks[2]}"
        f"{SECTION_DECISIONS_MARKER}{decisions_text}"
    )

    output = parse_section_generation_response(one_paragraph_response, package)

    assert output["paragraph_count"] == 2
    assert "U.S. safety guidance" in output["markdown"]
    assert "U.S.\n\nsafety guidance" not in output["markdown"]


def test_two_internal_links_in_one_sentence_still_fail_closed():
    package = _package_for_stage("select")
    response = _response(package)
    markdown_text, decisions_text = response.split(SECTION_DECISIONS_MARKER, 1)
    blocks = markdown_text.split("\n\n")
    assert len(blocks) == 3
    first, second = blocks[1], blocks[2]
    product_start = second.index("[[PRODUCT:")
    product_end = second.index("]]", product_start) + 2
    product_placeholder = second[product_start:product_end]
    second = second[:product_start] + second[product_end:]
    article_end = first.index("]]", first.index("[[ARTICLE:")) + 2
    first = first[:article_end] + f" and {product_placeholder}" + first[article_end:]
    invalid_response = (
        f"{blocks[0]}\n\n{first}\n\n{second}{SECTION_DECISIONS_MARKER}{decisions_text}"
    )

    with pytest.raises(SectionGenerationError, match="at most one internal link"):
        parse_section_generation_response(invalid_response, package)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"raw_url": True}, "raw links"),
        ({"cjk": True}, "must be English"),
    ],
)
def test_section_rejects_raw_urls_and_non_english_text(kwargs, message):
    package = _package_for_stage("select")

    with pytest.raises(SectionGenerationError, match=message):
        parse_section_generation_response(_response(package, **kwargs), package)


def test_unapproved_placeholder_id_is_rejected():
    package = _package_for_stage("select")
    response = _response(package)
    approved = package["link_gates"]["product_links"]["selected_ids"][0]
    response = response.replace(approved, "product-unapproved")

    with pytest.raises(SectionGenerationError, match="unapproved"):
        parse_section_generation_response(response, package)


def test_checkpoint_round_trip_and_stale_context_invalidation(tmp_path):
    package = _package_for_stage("select", previous_summary="Original summary")
    output = parse_section_generation_response(_response(package), package)
    persist_section_checkpoint(tmp_path, "marking-guide", package, output)

    assert load_section_checkpoint(tmp_path, "marking-guide", package) == output
    changed = _package_for_stage("select", previous_summary="Changed summary")
    assert changed["package_sha256"] != package["package_sha256"]
    assert load_section_checkpoint(tmp_path, "marking-guide", changed) is None


def test_corrupted_section_checkpoint_is_not_resumed(tmp_path):
    package = _package_for_stage("select")
    output = parse_section_generation_response(_response(package), package)
    path = Path(
        persist_section_checkpoint(
            tmp_path,
            "marking-guide",
            package,
            output,
        )
    )
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["output"]["summary"] = "Corrupted summary"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    assert load_section_checkpoint(tmp_path, "marking-guide", package) is None


def test_checkpoint_atomic_write_restores_previous_bytes(tmp_path, monkeypatch):
    from seo_ops.services import sectional_generation as sg

    package = _package_for_stage("select")
    output = parse_section_generation_response(_response(package), package)
    path = Path(
        persist_section_checkpoint(
            tmp_path,
            "marking-guide",
            package,
            output,
        )
    )
    snapshot = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated checkpoint replace failure")

    monkeypatch.setattr(sg.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated checkpoint"):
        persist_section_checkpoint(
            tmp_path,
            "marking-guide",
            package,
            copy.deepcopy(output),
        )
    assert path.read_bytes() == snapshot


def test_sequence_stops_on_failure_then_resumes_valid_checkpoints(tmp_path):
    sections, shadow = _setup()
    calls = []

    def fail_second(system, user):
        package = _package_from_prompt(user)
        calls.append(package["section_id"])
        if len(calls) == 2:
            raise RuntimeError("simulated provider failure")
        return _response(package)

    with pytest.raises(RuntimeError, match="simulated provider"):
        run_section_generation_sequence(
            workspace=tmp_path,
            slug="marking-guide",
            section_contracts=sections,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=fail_second,
        )
    first_path = section_checkpoint_path(
        tmp_path,
        "marking-guide",
        sections["section_order"][0],
    )
    assert first_path.exists()

    resumed_calls = []

    def succeed(system, user):
        package = _package_from_prompt(user)
        resumed_calls.append(package["section_id"])
        return _response(package)

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=succeed,
    )

    assert result["complete"] is True
    assert result["resumed_count"] == 1
    assert result["generated_count"] == 2
    assert resumed_calls == sections["section_order"][1:]


def test_sequence_repairs_one_word_count_miss_then_persists_checkpoint(tmp_path):
    sections, shadow = _setup()
    first_id = sections["section_order"][0]
    baseline_package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        first_id,
        previous_summary="",
    )
    baseline_count = parse_section_generation_response(
        _response(baseline_package),
        baseline_package,
    )["word_count"]
    first_contract = next(item for item in sections["sections"] if item["section_id"] == first_id)
    first_contract["target_words"] = {
        "min": baseline_count + 5,
        "max": baseline_count + 80,
    }
    calls = []

    def generate(system, user):
        package = _package_from_prompt(user)
        calls.append({"system": system, "user": user})
        response = _response(package)
        if len(calls) == 1:
            return response
        if "WORD COUNT REPAIR" in user:
            extra = (
                "The approved guidance also benefits from a little more practical "
                "context so readers can apply the same supported factors consistently "
                "without adding a new claim."
            )
            return response.replace(
                f"\n{SECTION_DECISIONS_MARKER}",
                f"\n\n{extra}\n{SECTION_DECISIONS_MARKER}",
            )
        return response

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )

    assert result["complete"] is True
    assert result["word_count_retry_count"] == 1
    assert len(calls) == len(sections["section_order"]) + 1
    assert "WORD COUNT REPAIR" in calls[1]["user"]
    assert "PREVIOUS RESPONSE" in calls[1]["user"]
    first_output = result["outputs"][0]
    assert first_output["word_count"] >= first_contract["target_words"]["min"]
    assert (
        load_section_checkpoint(
            tmp_path,
            "marking-guide",
            build_section_generation_package(
                sections,
                shadow["section_link_contracts"],
                shadow["context_manifest"],
                first_id,
                previous_summary="",
            ),
        )
        == first_output
    )


def test_sequence_does_not_retry_non_length_generation_errors(tmp_path):
    sections, shadow = _setup()
    calls = []

    def generate(system, user):
        package = _package_from_prompt(user)
        calls.append(package["section_id"])
        return _response(package, raw_url=True)

    with pytest.raises(SectionGenerationError, match="raw links"):
        run_section_generation_sequence(
            workspace=tmp_path,
            slug="marking-guide",
            section_contracts=sections,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=generate,
        )

    assert calls == [sections["section_order"][0]]


def test_sequence_limits_word_count_repair_to_one_attempt(tmp_path):
    sections, shadow = _setup()
    first_id = sections["section_order"][0]
    baseline_package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        first_id,
        previous_summary="",
    )
    baseline_count = parse_section_generation_response(
        _response(baseline_package),
        baseline_package,
    )["word_count"]
    first_contract = next(item for item in sections["sections"] if item["section_id"] == first_id)
    first_contract["target_words"] = {
        "min": baseline_count + 5,
        "max": baseline_count + 80,
    }
    calls = []

    def remain_short(system, user):
        package = _package_from_prompt(user)
        calls.append(user)
        return _response(package)

    with pytest.raises(SectionGenerationError, match="section word count"):
        run_section_generation_sequence(
            workspace=tmp_path,
            slug="marking-guide",
            section_contracts=sections,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=remain_short,
        )

    assert len(calls) == 2
    assert "WORD COUNT REPAIR" in calls[1]


def test_sequence_repairs_key_finding_authority_overstatement_once(tmp_path):
    sections, shadow = _setup()
    calls = []

    def generate(system, user):
        package = _package_from_prompt(user)
        calls.append(user)
        safe = _response(package)
        if len(calls) == 1:
            return safe.replace(
                "A clear marking process helps the team identify the intended location,",
                "OSHA recommends Class 3R as the best choice for ceiling marking. "
                "A clear marking process helps the team identify the intended location,",
            )
        return safe

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )

    assert result["complete"] is True
    assert result["evidence_strength_retry_count"] == 1
    assert result["word_count_retry_count"] == 0
    assert len(calls) == len(sections["section_order"]) + 1
    assert "EVIDENCE STRENGTH REPAIR" in calls[1]
    assert "SERVER-DETECTED OFFENDING PASSAGE" in calls[1]
    assert "This package has no source-verified quote evidence" in calls[1]
    assert "OSHA recommends Class 3R" in calls[1]


def test_sequence_reports_section_identity_when_evidence_repair_still_fails(tmp_path):
    sections, shadow = _setup()
    first_id = sections["section_order"][0]
    first_heading = sections["sections"][0]["heading"]
    calls = []

    def remain_unsupported(system, user):
        package = _package_from_prompt(user)
        calls.append((system, user))
        return _response(package).replace(
            "A clear marking process helps the team identify the intended location,",
            "OSHA recommends Class 3R as the best choice for ceiling marking. "
            "A clear marking process helps the team identify the intended location,",
        )

    with pytest.raises(
        SectionGenerationError,
        match=(
            rf"section {first_id} \({re.escape(first_heading)}\) "
            r"authority_free repair failed"
        ),
    ):
        run_section_generation_sequence(
            workspace=tmp_path,
            slug="marking-guide",
            section_contracts=sections,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=remain_unsupported,
        )

    assert len(calls) == 3
    assert "EVIDENCE STRENGTH REPAIR" in calls[1][1]
    assert "FINAL AUTHORITY-FREE REPAIR" in calls[2][1]
    assert "body must contain none of these names" in calls[2][0]
    assert "do not select a winner" in calls[2][0]
    assert "PREVIOUS REPAIRED RESPONSE" not in calls[2][1]
    assert "OSHA recommends Class 3R" not in calls[2][1]


def test_sequence_uses_final_authority_free_repair_before_stopping(tmp_path):
    sections, shadow = _setup()
    calls = []

    def generate(system, user):
        package = _package_from_prompt(user)
        calls.append((system, user))
        safe = _response(package)
        if len(calls) < 3:
            return safe.replace(
                "A clear marking process helps the team identify the intended location,",
                "OSHA guidance states that employers must follow this rule. "
                "A clear marking process helps the team identify the intended location,",
            )
        return safe

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )

    assert result["complete"] is True
    assert result["evidence_strength_retry_count"] == 2
    assert len(calls) == len(sections["section_order"]) + 2
    assert "FINAL AUTHORITY-FREE REPAIR" in calls[2][1]


def test_sequence_repairs_same_sentence_internal_link_collision(tmp_path):
    sections, shadow = _setup()
    calls = []
    target_attempts = 0

    def generate(system, user):
        nonlocal target_attempts
        package = _package_from_prompt(user)
        calls.append((package["reader_stage"], system, user))
        if package["reader_stage"] == "select":
            target_attempts += 1
            if target_attempts == 1:
                return _same_sentence_internal_link_collision_response(package)
            return _separated_internal_link_response(package)
        return _response(package)

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )

    select_calls = [item for item in calls if item[0] == "select"]
    assert result["complete"] is True
    assert result["link_layout_retry_count"] == 1
    assert result["evidence_strength_retry_count"] == 0
    assert len(select_calls) == 2
    assert "INTERNAL LINK LAYOUT REPAIR" in select_calls[1][1]
    assert "PREVIOUS RESPONSE" in select_calls[1][2]


def test_sequence_stops_after_one_link_layout_retry(tmp_path):
    sections, shadow = _setup()
    calls = []

    def remain_collided(system, user):
        package = _package_from_prompt(user)
        calls.append((package["reader_stage"], system, user))
        if package["reader_stage"] == "select":
            return _same_sentence_internal_link_collision_response(package)
        return _response(package)

    with pytest.raises(SectionGenerationError, match="link_layout repair failed"):
        run_section_generation_sequence(
            workspace=tmp_path,
            slug="marking-guide",
            section_contracts=sections,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=remain_collided,
        )

    select_calls = [item for item in calls if item[0] == "select"]
    assert len(select_calls) == 2
    assert "INTERNAL LINK LAYOUT REPAIR" in select_calls[1][1]


def test_evidence_repair_can_expose_one_final_link_layout_repair(tmp_path):
    sections, shadow = _setup()
    calls = []
    target_attempts = 0

    def generate(system, user):
        nonlocal target_attempts
        package = _package_from_prompt(user)
        calls.append((package["reader_stage"], system, user))
        if package["reader_stage"] != "select":
            return _response(package)
        target_attempts += 1
        if target_attempts == 1:
            return _same_sentence_internal_link_collision_response(package).replace(
                "A clear marking process helps the team identify the intended location,",
                "OSHA recommends Class 3R as the best choice for ceiling marking. "
                "A clear marking process helps the team identify the intended location,",
            )
        if target_attempts == 2:
            return _same_sentence_internal_link_collision_response(package)
        return _separated_internal_link_response(package)

    result = run_section_generation_sequence(
        workspace=tmp_path,
        slug="marking-guide",
        section_contracts=sections,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )

    select_calls = [item for item in calls if item[0] == "select"]
    assert result["complete"] is True
    assert result["evidence_strength_retry_count"] == 1
    assert result["link_layout_retry_count"] == 1
    assert len(select_calls) == 3
    assert "EVIDENCE STRENGTH REPAIR" in select_calls[1][2]
    assert "INTERNAL LINK LAYOUT REPAIR" in select_calls[2][1]
    assert "PREVIOUS RESPONSE" in select_calls[2][2]


def test_section_authority_recommendation_requires_verified_quote_citation():
    sections, shadow = _setup()
    verify = next(item for item in sections["sections"] if item["reader_stage"] == "verify")
    package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        verify["section_id"],
    )
    response = _response(package).replace(
        "Professionals should match the tool to the working distance,",
        "OSHA recommends Class 3R as the best choice for this work. "
        "Professionals should match the tool to the working distance,",
    )

    with pytest.raises(
        SectionGenerationError,
        match="source-verified quote evidence",
    ):
        parse_section_generation_response(response, package)

    blink_response = _response(package).replace(
        "Professionals should match the tool to the working distance,",
        "Class 2 lasers are generally considered safe because the natural blink "
        "response offers protection. Professionals should match the tool to the "
        "working distance,",
    )
    with pytest.raises(
        SectionGenerationError,
        match="source-verified quote evidence",
    ):
        parse_section_generation_response(blink_response, package)

    practical_choice_response = _response(package).replace(
        "Professionals should match the tool to the working distance,",
        "Class 3R is a practical choice for this task. Professionals should match "
        "the tool to the working distance,",
    )
    with pytest.raises(
        SectionGenerationError,
        match="source-verified quote evidence",
    ):
        parse_section_generation_response(practical_choice_response, package)

    neutral_process_response = _response(package).replace(
        "Professionals should match the tool to the working distance,",
        "The practical choice depends on visibility, handling, and the surrounding "
        "work area. Professionals should match the tool to the working distance,",
    )
    assert parse_section_generation_response(neutral_process_response, package)

    unverified_manifest = copy.deepcopy(shadow["context_manifest"])
    for candidate in unverified_manifest["registry"]["evidence"]["candidates"]:
        candidate["support_basis"] = "quote"
    unverified_package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        unverified_manifest,
        verify["section_id"],
    )
    unverified_response = _response(unverified_package).replace(
        "Professionals should match the tool to the working distance,",
        "OSHA recommends Class 3R as the best choice for this work. "
        "Professionals should match the tool to the working distance,",
    )
    with pytest.raises(
        SectionGenerationError,
        match="source-verified quote evidence",
    ):
        parse_section_generation_response(unverified_response, unverified_package)

    verified_manifest = copy.deepcopy(shadow["context_manifest"])
    for candidate in verified_manifest["registry"]["evidence"]["candidates"]:
        candidate["support_basis"] = "verified_quote"
    verified_package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        verified_manifest,
        verify["section_id"],
    )
    verified_response = _response(verified_package).replace(
        "Professionals should match the tool to the working distance,",
        "OSHA recommends Class 3R as the best choice for this work. "
        "Professionals should match the tool to the working distance,",
    )
    assert parse_section_generation_response(verified_response, verified_package)


@pytest.mark.parametrize(
    "unsupported_sentence",
    [
        "The FDA warns that Class 3R is acceptable for this work.",
        "Class 2 is eye-safe and prevents retinal damage during accidental exposure.",
    ],
)
def test_key_finding_cannot_support_authority_or_absolute_safety_language(
    unsupported_sentence,
):
    package = _package_for_stage("select")
    response = _response(package).replace(
        "A clear marking process helps the team identify the intended location,",
        unsupported_sentence
        + " A clear marking process helps the team identify the intended location,",
    )

    with pytest.raises(
        SectionGenerationError,
        match="source-verified quote evidence",
    ):
        parse_section_generation_response(response, package)


def test_authority_words_in_exact_h2_do_not_trigger_body_evidence_gate():
    sections, shadow = _setup()
    first = sections["sections"][0]
    first["heading"] = "What OSHA Says About Worksite Marking"
    first["must_answer"] = [first["heading"]]
    package = build_section_generation_package(
        sections,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        first["section_id"],
    )

    assert parse_section_generation_response(_response(package), package)


def test_final_claim_strength_gate_rejects_key_finding_regulatory_recommendation():
    _, shadow = _setup()
    ledger = {
        "version": 1,
        "claims": [
            {
                "sentence_id": "S001",
                "claim_type": "regulatory",
                "claim_text": "OSHA recommends Class 3R as the best choice.",
                "evidence_ids": ["ev_safety"],
            }
        ],
    }

    with pytest.raises(SectionGenerationError, match="S001"):
        validate_claim_evidence_strength(ledger, shadow["context_manifest"])

    quote_manifest = copy.deepcopy(shadow["context_manifest"])
    evidence = quote_manifest["registry"]["evidence"]["candidates"]
    next(item for item in evidence if item["evidence_id"] == "ev_safety")["support_basis"] = "quote"
    with pytest.raises(SectionGenerationError, match="S001"):
        validate_claim_evidence_strength(ledger, quote_manifest)

    next(item for item in evidence if item["evidence_id"] == "ev_safety")["support_basis"] = (
        "verified_quote"
    )
    assert validate_claim_evidence_strength(ledger, quote_manifest) == ledger


def _completed_section_run():
    sections, shadow = _setup()
    outputs = []
    previous = ""
    for section_id in sections["section_order"]:
        package = build_section_generation_package(
            sections,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            section_id,
            previous_summary=previous,
        )
        output = parse_section_generation_response(_response(package), package)
        outputs.append(output)
        previous = output["summary"]
    return {
        "version": 1,
        "topic": sections["topic"],
        "content_language": "en",
        "section_order": sections["section_order"],
        "outputs": outputs,
    }


def _frame_response(*, raw_url=False, cjk=False):
    intro = (
        "Professional ceiling marking depends on a clear method, a suitable tool, "
        "and disciplined site practices. This guide explains how to evaluate the "
        "working area, compare practical options, and make a defensible selection "
        "without relying on invented specifications. It also shows why the final "
        "choice should reflect the real catalog, the intended task, and established "
        "worksite procedures before the tool is used. By separating selection, "
        "application, and verification into clear steps, the reader can understand "
        "what matters, avoid irrelevant recommendations, and follow a process that "
        "remains useful when products or site conditions change."
    )
    conclusion = (
        "A reliable marking workflow starts with the task rather than the product. "
        "Define the location, distance, visibility conditions, handling needs, and "
        "site rules, then compare only the options that genuinely fit those facts. "
        "Keeping product data consistent and using approved supporting evidence "
        "makes the recommendation easier to trust, easier to maintain, and less "
        "likely to mislead the reader when the catalog or working conditions change. "
        "The final recommendation should therefore explain the practical match, "
        "respect the current catalog, and keep safety or compliance statements tied "
        "to appropriate evidence instead of turning assumptions into product claims."
    )
    if raw_url:
        conclusion += " https://unapproved.example"
    if cjk:
        conclusion += " 中文内容。"
    faq = {
        "faqs": [
            {
                "question": "What should professionals evaluate before choosing a marking tool?",
                "answer": "They should evaluate the task, working distance, visibility conditions, handling requirements, catalog fit, and relevant site procedures before selecting an option.",
            },
            {
                "question": "Why should product data be checked before adding a product link?",
                "answer": "Consistent product data prevents a recommendation from repeating conflicting specifications and allows the system to link only to a product that can be described accurately.",
            },
            {
                "question": "Can a historical writing brief override the current product catalog?",
                "answer": "No. A historical brief can suggest an angle, but the current catalog and approved site policy remain the commercial source of truth for product recommendations.",
            },
        ]
    }
    return (
        f"{FRAME_INTRO_MARKER}\n{intro}\n"
        f"{FRAME_TAKEAWAYS_MARKER}\n"
        "- Start with the real task and working conditions.\n"
        "- Use only products supported by the current catalog.\n"
        "- Keep safety and compliance guidance evidence-bound.\n"
        "- Report conflicting product data before creating links.\n"
        f"{FRAME_CONCLUSION_MARKER}\n{conclusion}\n"
        f"{FRAME_FAQ_MARKER}\n{json.dumps(faq)}"
    )


def test_article_frame_uses_summaries_and_validates_all_components():
    package = build_article_frame_package(_completed_section_run())
    prompt = build_article_frame_prompt(package)
    output = parse_article_frame_response(_frame_response(), package)

    assert len(package["sections"]) == 3
    assert "markdown" not in package["sections"][0]
    assert FRAME_FAQ_MARKER in prompt["system"]
    assert "Do not name OSHA" in prompt["system"]
    assert len(output["key_takeaways"]) == 4
    assert len(output["faq"]) == 3


def test_article_frame_contract_matches_phase5_limits():
    package = build_article_frame_package(_completed_section_run())
    prompt = build_article_frame_prompt(package)

    assert package["requirements"]["takeaway_count"] == {"min": 3, "max": 5}
    assert package["requirements"]["faq_count"] == {"min": 3, "max": 4}
    assert "<3-5 Markdown bullet points>" in prompt["system"]
    assert "exactly 3-4 items" in prompt["system"]


def test_article_frame_rejects_six_takeaways_or_five_faqs():
    package = build_article_frame_package(_completed_section_run())
    output = parse_article_frame_response(_frame_response(), package)

    too_many_takeaways = copy.deepcopy(output)
    too_many_takeaways["key_takeaways"].extend(
        [
            "Keep catalog facts consistent.",
            "Use only approved evidence.",
        ]
    )
    with pytest.raises(SectionGenerationError, match="takeaway count"):
        validate_article_frame_output(too_many_takeaways, package)

    too_many_faqs = copy.deepcopy(output)
    too_many_faqs["faq"].extend(
        [
            {
                "question": "How should teams document the final selection?",
                "answer": "Teams should record the task, chosen product, relevant catalog facts, and approved evidence so the recommendation remains traceable and maintainable.",
            },
            {
                "question": "When should the selection be reviewed again?",
                "answer": "The selection should be reviewed when the catalog, working conditions, product data, or applicable site procedures materially change.",
            },
        ]
    )
    with pytest.raises(SectionGenerationError, match="FAQ count"):
        validate_article_frame_output(too_many_faqs, package)


def test_article_frame_rejects_tampered_limits():
    package = build_article_frame_package(_completed_section_run())
    package["requirements"]["faq_count"]["max"] = 5
    unsigned = dict(package)
    unsigned.pop("package_sha256")
    package["package_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(SectionGenerationError, match="requirements"):
        build_article_frame_prompt(package)


def test_article_frame_checkpoint_round_trip_and_resume(tmp_path):
    section_run = _completed_section_run()
    package = build_article_frame_package(section_run)
    output = parse_article_frame_response(_frame_response(), package)
    path = Path(
        persist_article_frame_checkpoint(
            tmp_path,
            "marking-guide",
            package,
            output,
        )
    )

    assert path == article_frame_checkpoint_path(tmp_path, "marking-guide")
    assert (
        load_article_frame_checkpoint(
            tmp_path,
            "marking-guide",
            package,
        )
        == output
    )

    calls = []

    def should_not_run(system, user):
        calls.append((system, user))
        return _frame_response()

    result = run_article_frame_generation(
        workspace=tmp_path,
        slug="marking-guide",
        section_run=section_run,
        generate_text=should_not_run,
    )
    assert result["resumed"] is True
    assert result["generated"] is False
    assert calls == []


def test_corrupted_article_frame_checkpoint_is_not_resumed(tmp_path):
    section_run = _completed_section_run()
    package = build_article_frame_package(section_run)
    output = parse_article_frame_response(_frame_response(), package)
    path = Path(
        persist_article_frame_checkpoint(
            tmp_path,
            "marking-guide",
            package,
            output,
        )
    )
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["output"]["introduction"] = "Too short."
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    assert (
        load_article_frame_checkpoint(
            tmp_path,
            "marking-guide",
            package,
        )
        is None
    )


def test_article_frame_generation_writes_checkpoint_once(tmp_path):
    section_run = _completed_section_run()
    calls = []

    def generate(system, user):
        calls.append((system, user))
        return _frame_response()

    result = run_article_frame_generation(
        workspace=tmp_path,
        slug="marking-guide",
        section_run=section_run,
        generate_text=generate,
    )

    assert result["generated"] is True
    assert result["resumed"] is False
    assert len(calls) == 1
    assert article_frame_checkpoint_path(tmp_path, "marking-guide").exists()


def test_article_frame_repairs_unsupported_recommendation_once(tmp_path):
    section_run = _completed_section_run()
    calls = []

    def generate(system, user):
        calls.append(user)
        if len(calls) == 1:
            return _frame_response().replace(
                "Professional ceiling marking depends on a clear method,",
                "OSHA recommends Class 3R as the best choice. "
                "Professional ceiling marking depends on a clear method,",
            )
        return _frame_response()

    result = run_article_frame_generation(
        workspace=tmp_path,
        slug="marking-guide",
        section_run=section_run,
        generate_text=generate,
    )

    assert result["generated"] is True
    assert result["evidence_strength_repaired"] is True
    assert result["evidence_strength_retry_count"] == 1
    assert len(calls) == 2
    assert "FRAME EVIDENCE STRENGTH REPAIR" in calls[1]


def test_article_frame_uses_final_authority_free_repair(tmp_path):
    section_run = _completed_section_run()
    calls = []

    def generate(system, user):
        calls.append((system, user))
        if len(calls) < 3:
            return _frame_response().replace(
                "Professional ceiling marking depends on a clear method,",
                "OSHA guidance states that employers must follow this rule. "
                "Professional ceiling marking depends on a clear method,",
            )
        return _frame_response()

    result = run_article_frame_generation(
        workspace=tmp_path,
        slug="marking-guide",
        section_run=section_run,
        generate_text=generate,
    )

    assert result["generated"] is True
    assert result["evidence_strength_repaired"] is True
    assert result["evidence_strength_retry_count"] == 2
    assert len(calls) == 3
    assert "FINAL FRAME AUTHORITY-FREE REPAIR" in calls[2][1]


def test_article_frame_reports_final_authority_free_failure(tmp_path):
    section_run = _completed_section_run()
    calls = []

    def remain_unsupported(system, user):
        calls.append((system, user))
        return _frame_response().replace(
            "Professional ceiling marking depends on a clear method,",
            "OSHA guidance states that employers must follow this rule. "
            "Professional ceiling marking depends on a clear method,",
        )

    with pytest.raises(
        SectionGenerationError,
        match="article frame authority_free repair failed",
    ):
        run_article_frame_generation(
            workspace=tmp_path,
            slug="marking-guide",
            section_run=section_run,
            generate_text=remain_unsupported,
        )

    assert len(calls) == 3
    assert "FINAL FRAME AUTHORITY-FREE REPAIR" in calls[2][1]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"raw_url": True}, "must not contain links"),
        ({"cjk": True}, "must be English"),
    ],
)
def test_article_frame_rejects_links_and_non_english_text(kwargs, message):
    package = build_article_frame_package(_completed_section_run())

    with pytest.raises(SectionGenerationError, match=message):
        parse_article_frame_response(_frame_response(**kwargs), package)
