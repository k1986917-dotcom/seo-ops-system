import copy
import json
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
    section = next(
        item for item in sections["sections"] if item["reader_stage"] == stage
    )
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
    if (
        article_gate["opportunity_state"] == "required"
        and "article_links" not in omit_required
    ):
        candidate_id = article_gate["selected_ids"][0]
        used["article_links"].append(candidate_id)
        paragraph_one_placeholders.append(
            f"[[ARTICLE:{candidate_id}|detailed tool selection guidance]]"
        )

    product_gate = gates["product_links"]
    if (
        product_gate["opportunity_state"] == "required"
        and "product_links" not in omit_required
    ):
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


def _package_from_prompt(user_prompt: str):
    return json.loads(user_prompt.split("SECTION PACKAGE\n", 1)[1])


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
    assert package["context_char_counts"]["products"] < 3000


def test_generation_prompt_contains_protocol_but_not_full_registry():
    package = _package_for_stage("select")
    prompt = build_section_generation_prompt(package)

    assert SECTION_MARKDOWN_MARKER in prompt["system"]
    assert SECTION_DECISIONS_MARKER in prompt["system"]
    assert "Do not invent URLs" in prompt["system"]
    assert package["section_id"] in prompt["user"]
    assert "catalog_data_issues" not in prompt["user"]


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
    assert all(
        item["fit_level"] == "related_catalog"
        for item in package["candidates"]["products"]
    )
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
    path = Path(persist_section_checkpoint(
        tmp_path,
        "marking-guide",
        package,
        output,
    ))
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["output"]["summary"] = "Corrupted summary"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    assert load_section_checkpoint(tmp_path, "marking-guide", package) is None


def test_checkpoint_atomic_write_restores_previous_bytes(tmp_path, monkeypatch):
    from seo_ops.services import sectional_generation as sg

    package = _package_for_stage("select")
    output = parse_section_generation_response(_response(package), package)
    path = Path(persist_section_checkpoint(
        tmp_path,
        "marking-guide",
        package,
        output,
    ))
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
    assert len(output["key_takeaways"]) == 4
    assert len(output["faq"]) == 3


def test_article_frame_checkpoint_round_trip_and_resume(tmp_path):
    section_run = _completed_section_run()
    package = build_article_frame_package(section_run)
    output = parse_article_frame_response(_frame_response(), package)
    path = Path(persist_article_frame_checkpoint(
        tmp_path,
        "marking-guide",
        package,
        output,
    ))

    assert path == article_frame_checkpoint_path(tmp_path, "marking-guide")
    assert load_article_frame_checkpoint(
        tmp_path,
        "marking-guide",
        package,
    ) == output

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
    path = Path(persist_article_frame_checkpoint(
        tmp_path,
        "marking-guide",
        package,
        output,
    ))
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["output"]["introduction"] = "Too short."
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    assert load_article_frame_checkpoint(
        tmp_path,
        "marking-guide",
        package,
    ) is None


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
