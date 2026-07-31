import copy
import hashlib
import json
from pathlib import Path

import pytest

from seo_ops.services.sectional_assembly import assemble_sectional_article
from seo_ops.services.sectional_context import (
    build_candidate_registry,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_delivery import (
    build_section_claim_package,
    merge_section_claim_ledgers,
    resolve_section_placeholders,
    run_section_claim_ledger_sequence,
)
from seo_ops.services.sectional_generation import (
    FRAME_CONCLUSION_MARKER,
    FRAME_FAQ_MARKER,
    FRAME_INTRO_MARKER,
    FRAME_TAKEAWAYS_MARKER,
    SECTION_DECISIONS_MARKER,
    SECTION_MARKDOWN_MARKER,
    build_article_frame_package,
    build_section_generation_package,
    parse_article_frame_response,
    parse_section_generation_response,
)
from seo_ops.services.sectional_repair import (
    MAX_REPAIR_ROUNDS,
    SectionRepairError,
    build_section_repair_package,
    build_section_repair_plan,
    load_sectional_repair_result,
    parse_section_repair_response,
    persist_sectional_repair_result,
    remap_section_claim_output,
    repair_result_path,
    run_sectional_repair_sequence,
    validate_section_repair_plan,
)
from seo_ops.services.sectional_writing import build_contract_bundle

ARTICLES = """# Internal Links

| Title | URL | Primary Keyword |
|---|---|---|
| Ceiling Tool Selection Guide | https://example.com/blog/tool-selection | ceiling tool selection |
| Professional Marking Comparison | https://example.com/blog/marking-comparison | marking comparison |
| Worksite Safety Guide | https://example.com/blog/worksite-safety | worksite safety |
| Battery Maintenance Guide | https://example.com/blog/battery | battery maintenance |
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
            "support": "Tool selection should account for work area and visibility needs.",
            "concepts": ["tool selection", "work area", "visibility"],
            "claim_types": ["guidance"],
            "required": False,
        },
        {
            "evidence_id": "ev_safety",
            "source_url": "https://safety.example/procedure",
            "support": "Worksite safety procedures should be followed before equipment use.",
            "concepts": ["worksite safety", "procedure", "compliance"],
            "claim_types": ["safety"],
            "required": True,
        },
    ]
}


def _section_response(package):
    gates = package["link_gates"]
    used = {key: [] for key in gates}
    first_links = []
    second_links = []
    if gates["article_links"]["selected_ids"]:
        article_ids = gates["article_links"]["selected_ids"]
        candidate_id = article_ids[(package["position"] - 1) % len(article_ids)]
        used["article_links"].append(candidate_id)
        first_links.append(
            f"[[ARTICLE:{candidate_id}|ceiling tool selection guidance]]"
        )
    if gates["product_links"]["opportunity_state"] != "none":
        product_ids = gates["product_links"]["selected_ids"]
        candidate_id = product_ids[(package["position"] - 1) % len(product_ids)]
        used["product_links"].append(candidate_id)
        second_links.append(
            f"[[PRODUCT:{candidate_id}|related professional marking tool]]"
        )
    if gates["external_citations"]["selected_ids"]:
        evidence_ids = gates["external_citations"]["selected_ids"]
        candidate_id = evidence_ids[(package["position"] - 1) % len(evidence_ids)]
        used["external_citations"].append(candidate_id)
        second_links.append(f"[[CITE:{candidate_id}]]")

    first = (
        f"The {package['heading']} section helps a team identify the intended location "
        "and communicate the task before work begins. "
        f"For {package['heading']}, the practical choice depends on visibility, handling, "
        "and the surrounding work area."
    )
    second = (
        f"At the {package['reader_stage']} stage for {package['heading']}, professionals "
        "should compare catalog "
        "options with the actual working distance and established site procedures. "
        f"A product discussed in {package['heading']} may be presented without claiming "
        "it was designed or certified for this exact use."
    )
    if first_links:
        first += " " + " ".join(first_links)
    if second_links:
        second += " " + " ".join(second_links)
    decisions = {}
    for key, gate in gates.items():
        ids = used[key]
        decisions[key] = {
            "used_ids": ids,
            "reason_code": (
                "used_approved_candidate"
                if ids
                else gate["reason_code"]
                if gate["opportunity_state"] == "none"
                else "not_needed_for_this_section"
            ),
        }
    return (
        f"{SECTION_MARKDOWN_MARKER}\n"
        f"## {package['heading']}\n\n{first}\n\n{second}\n"
        f"{SECTION_DECISIONS_MARKER}\n"
        f"{json.dumps(decisions, sort_keys=True)}"
    )


def _frame_response():
    introduction = " ".join([
        "Professional teams should define the ceiling marking task before selecting equipment.",
        "They should review distance, visibility, handling, and the surrounding work area.",
        "The current catalog provides the commercial options that can be discussed honestly.",
        "Supporting evidence should remain tied to the claims it actually supports.",
        "A related product can be useful without being described as purpose-built.",
        "Clear stages help readers compare options without confusing advice and verification.",
        "Consistent product data prevents conflicting specifications from entering recommendations.",
        "This process keeps the article useful when products or site conditions change.",
    ])
    conclusion = " ".join([
        "A reliable marking workflow begins with the real task and current work area.",
        "Readers should compare available options against distance, visibility, and handling needs.",
        "Product recommendations should reflect the live catalog rather than an old writing note.",
        "Safety and compliance statements should remain connected to appropriate evidence.",
        "Conflicting catalog data should be reported before an automatic link is created.",
        "Related products may still be presented when their limitations are stated accurately.",
        "This approach produces a recommendation that is useful and transparent.",
        "It avoids turning an unsupported assumption into a product or regulatory claim.",
    ])
    faqs = {
        "faqs": [
            {
                "question": "What should professionals evaluate before choosing a marking tool?",
                "answer": (
                    "They should evaluate the task, working distance, visibility conditions, "
                    "handling requirements, catalog fit, and relevant site procedures before "
                    "selecting a documented option for the work."
                ),
            },
            {
                "question": "Why should product data be checked before adding a link?",
                "answer": (
                    "Consistent product data prevents conflicting specifications from entering "
                    "the article and lets the linked product be described accurately for readers "
                    "reviewing current catalog options."
                ),
            },
            {
                "question": "Can a historical brief override the current catalog?",
                "answer": (
                    "No. A historical brief may suggest an angle, but the current catalog and "
                    "approved site policy remain the commercial source of truth for product "
                    "descriptions and recommendations."
                ),
            },
        ]
    }
    return (
        f"{FRAME_INTRO_MARKER}\n{introduction}\n"
        f"{FRAME_TAKEAWAYS_MARKER}\n"
        "- Start with the real task and working conditions.\n"
        "- Use products supported by the current catalog.\n"
        "- Keep safety guidance evidence-bound.\n"
        f"{FRAME_CONCLUSION_MARKER}\n{conclusion}\n"
        f"{FRAME_FAQ_MARKER}\n{json.dumps(faqs)}"
    )


def _metadata():
    return {
        "title": "Professional Ceiling Marking Tools",
        "slug": "professional-ceiling-marking-tools",
        "author": "Example Tools",
        "summary": (
            "A practical guide to comparing related ceiling marking tools while keeping "
            "catalog specifications, evidence, and recommendation limits clear."
        ),
        "tags": ["ceiling marking", "worksite tools", "product selection"],
        "page_type": "Cluster Content",
        "seo_title": "Professional Ceiling Marking Tools for Worksite Teams",
        "seo_description": (
            "Professional ceiling marking guidance helps worksite teams compare related tools, "
            "review visibility needs, and keep catalog recommendations accurate and clear."
        ),
        "seo_keywords": ["professional ceiling marking tools", "worksite marking"],
        "target_words": {"min": 100, "max": 1500},
    }


def _claim_response(package):
    if not package["evidence"]:
        return json.dumps({"version": 1, "claims": []})
    sentence = next((
        item
        for item in package["sentences"]
        if not item["text"].lstrip().startswith("#")
        and not item["text"].lstrip().startswith(">")
    ), None)
    if sentence is None:
        return json.dumps({"version": 1, "claims": []})
    return json.dumps({
        "version": 1,
        "claims": [
            {
                "sentence_id": sentence["sentence_id"],
                "claim_type": "general",
                "evidence_ids": [package["evidence"][0]["evidence_id"]],
            }
        ],
    })


def _claim_generator(calls=None):
    calls = calls if calls is not None else []

    def generate(system, user, **kwargs):
        package = json.loads(user.split("SECTION CLAIM PACKAGE\n", 1)[1].split("\n\n", 1)[0])
        calls.append({"package": package, **kwargs})
        return _claim_response(package)

    return generate


def _setup(tmp_path):
    bundle = build_contract_bundle(
        topic="Professional Ceiling Marking Tools",
        tier="Cluster Content",
        intent="Help professionals compare and use related marking tools.",
        outline=[
            "Why Clear Ceiling Marking Matters",
            "Choosing the Right Ceiling Marking Tool",
            "Worksite Safety and Compliance",
        ],
    )
    for section in bundle["section_contracts"]["sections"]:
        section["target_words"] = {"min": 35, "max": 140}
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
    outputs = []
    previous_summary = ""
    for section_id in bundle["section_contracts"]["section_order"]:
        package = build_section_generation_package(
            bundle["section_contracts"],
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            section_id,
            previous_summary=previous_summary,
        )
        output = parse_section_generation_response(_section_response(package), package)
        outputs.append(output)
        previous_summary = output["summary"]
    section_run = {
        "version": 1,
        "topic": bundle["section_contracts"]["topic"],
        "content_language": "en",
        "section_order": bundle["section_contracts"]["section_order"],
        "outputs": outputs,
        "generated_count": len(outputs),
        "resumed_count": 0,
        "complete": True,
    }
    frame_package = build_article_frame_package(section_run)
    article_frame = parse_article_frame_response(_frame_response(), frame_package)
    section_run["article_frame"] = article_frame
    delivery = resolve_section_placeholders(
        section_run,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        article_frame=article_frame,
    )
    ledger_run = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=_claim_generator(),
        resume=False,
    )
    merged = merge_section_claim_ledgers(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        ledger_run,
    )
    assembly = assemble_sectional_article(delivery, _metadata(), merged)
    return bundle, shadow, section_run, article_frame, delivery, ledger_run, assembly


def _repair_generator(calls=None, *, change_visible=False):
    calls = calls if calls is not None else []

    def generate(system, user):
        package = json.loads(user.split("SECTION REPAIR PACKAGE\n", 1)[1])
        calls.append(package)
        current = package["current_output"]
        markdown = current["markdown"]
        if package["repair_mode"] == "section_rewrite":
            markdown = markdown.replace(
                "and the surrounding work area.",
                "and the surrounding work area. "
                f"The repaired {package['generation_package']['heading']} discussion "
                "now separates documented guidance from unsupported assumptions.",
                1,
            )
        elif change_visible:
            markdown = markdown.replace(" section helps", " section clearly helps", 1)
        return (
            f"{SECTION_MARKDOWN_MARKER}\n{markdown}\n"
            f"{SECTION_DECISIONS_MARKER}\n"
            f"{json.dumps(current['decisions'], sort_keys=True)}"
        )

    return generate


def _frame_generator(calls=None):
    calls = calls if calls is not None else []

    def generate(system, user):
        calls.append(user)
        return _frame_response()

    return generate


def _fact_report(sentence_id, sentence, reason="unsupported_factual_sentence"):
    return {
        "fail_count": 1,
        "checks": [
            {
                "item": "事实校验",
                "level": "fail",
                "pass": False,
                "detail": "One factual sentence needs repair.",
                "fact_issues": [
                    {
                        "sentence_id": sentence_id,
                        "sentence": sentence,
                        "reason": reason,
                    }
                ],
            }
        ],
    }


def test_plan_maps_fact_sentence_to_one_body_section(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    sentence = next(
        item for item in delivery["sentences"] if item["section_id"] in delivery["body_section_order"]
    )
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )

    assert plan["global_blockers"] == []
    assert plan["actions"] == [
        {
            "section_id": sentence["section_id"],
            "action": "section_rewrite",
            "issues": plan["actions"][0]["issues"],
        }
    ]
    assert plan["frame_refresh"] is True


def test_uncovered_fact_maps_to_ledger_only_repair(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    sentence = next(
        item for item in delivery["sentences"] if item["section_id"] in delivery["body_section_order"]
    )
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(
            sentence["sentence_id"],
            sentence["text"],
            reason="uncovered_factual_sentence",
        ),
    )

    assert plan["actions"][0]["action"] == "ledger_repair"
    assert plan["frame_refresh"] is False


def test_w2_url_maps_link_issue_to_bound_section(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    binding = next(item for item in delivery["bindings"] if item["kind"] == "product")
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={
            "link_issues": f"Duplicate or misplaced product link: {binding['url']}"
        },
    )

    assert plan["actions"][0]["section_id"] == binding["section_id"]
    assert plan["actions"][0]["action"] == "link_repair"
    assert plan["frame_refresh"] is False


def test_unlocalized_score_and_cannibal_failures_are_global_blockers(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={
            "fix_items": "评分失败，请手动检查\nCannibal overlap exceeds the block line"
        },
    )

    assert plan["actions"] == []
    assert len(plan["global_blockers"]) == 2


@pytest.mark.parametrize(
    "field",
    ["score_error", "cannibal_error", "cannibal_block", "score_block", "link_block"],
)
def test_boolean_only_w2_failures_are_never_silently_dropped(tmp_path, field):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={field: True},
    )

    assert plan["actions"] == []
    assert any(item["reason_code"] == field for item in plan["global_blockers"])


def test_frame_aliases_map_faq_and_introduction_failures(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    plan = build_section_repair_plan(
        delivery,
        precheck_report={
            "failed": [
                {"item": "FAQ", "detail": "FAQ needs a clearer answer."},
                {"item": "Introduction", "detail": "Introduction repeats the topic."},
            ]
        },
    )

    assert plan["global_blockers"] == []
    assert [item["section_id"] for item in plan["actions"]] == [
        "frame-introduction",
        "frame-faq",
    ]
    assert all(item["action"] == "frame_rewrite" for item in plan["actions"])


def test_plan_round_limit_and_sha_tampering_fail_closed(tmp_path):
    _, _, _, _, delivery, _, _ = _setup(tmp_path)
    sentence = delivery["sentences"][0]
    with pytest.raises(SectionRepairError, match="round_number"):
        build_section_repair_plan(
            delivery,
            precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
            round_number=MAX_REPAIR_ROUNDS + 1,
        )
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    tampered = copy.deepcopy(plan)
    tampered["round_number"] = 2
    with pytest.raises(SectionRepairError, match="SHA mismatch"):
        validate_section_repair_plan(tampered, delivery)


def test_link_repair_must_preserve_visible_text(tmp_path):
    bundle, shadow, section_run, _, delivery, _, _ = _setup(tmp_path)
    binding = next(item for item in delivery["bindings"] if item["kind"] == "article")
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={"link_issues": f"Repair link {binding['url']}"},
    )
    package = build_section_repair_package(
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        plan=plan,
        section_id=binding["section_id"],
    )
    response = _repair_generator(change_visible=True)("system", "SECTION REPAIR PACKAGE\n" + json.dumps(package))

    with pytest.raises(SectionRepairError, match="changed reader-visible text"):
        parse_section_repair_response(response, package)


def test_section_rewrite_changes_only_target_body_and_refreshes_frame(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    section_calls = []
    frame_calls = []
    claim_calls = []
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(section_calls),
        generate_frame_text=_frame_generator(frame_calls),
        generate_claim_text=_claim_generator(claim_calls),
        resume=False,
    )

    assert result["changed_body_units"] == [target]
    assert result["frame_refreshed"] is True
    assert len(section_calls) == 1
    assert len(frame_calls) == 1
    assert result["generated_ledgers"] == 5
    assert result["remapped_ledgers"] == len(delivery["section_order"]) - 5
    assert result["assembly"]["audit"]["blockers"] == []
    old_by_id = {item["section_id"]: item for item in section_run["outputs"]}
    new_by_id = {item["section_id"]: item for item in result["section_run"]["outputs"]}
    for section_id in delivery["body_section_order"]:
        if section_id == target:
            assert new_by_id[section_id] != old_by_id[section_id]
        else:
            assert new_by_id[section_id] == old_by_id[section_id]
    assert all(call["thinking_mode"] == "disabled" for call in claim_calls)


def test_unchanged_claims_remap_after_global_sentence_ids_shift(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    later = delivery["body_section_order"][1]
    old_later = next(item for item in ledger_run["outputs"] if item["section_id"] == later)
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-remap",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=False,
    )
    new_later = next(
        item for item in result["ledger_run"]["outputs"] if item["section_id"] == later
    )

    if old_later["claims"]:
        assert new_later["claims"][0]["claim_text"] == old_later["claims"][0]["claim_text"]
        assert new_later["claims"][0]["sentence_id"] != old_later["claims"][0]["sentence_id"]


def test_ledger_only_repair_skips_section_and_frame_generation(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(
            sentence["sentence_id"],
            sentence["text"],
            reason="uncovered_factual_sentence",
        ),
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("section/frame generator should not run")

    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-ledger",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=must_not_run,
        generate_frame_text=must_not_run,
        generate_claim_text=_claim_generator(),
        resume=False,
    )

    assert result["new_delivery_sha256"] == result["old_delivery_sha256"]
    assert result["generated_sections"] == 0
    assert result["frame_refreshed"] is False
    assert result["generated_ledgers"] == 1
    assert result["remapped_ledgers"] == len(delivery["section_order"]) - 1


def test_link_only_repair_keeps_frame_and_all_visible_prose(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    binding = next(item for item in delivery["bindings"] if item["kind"] == "product")
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={"link_issues": f"Review product link {binding['url']}"},
    )
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-link",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=lambda *_: (_ for _ in ()).throw(AssertionError("frame")),
        generate_claim_text=_claim_generator(),
        resume=False,
    )

    assert result["frame_refreshed"] is False
    assert result["article_frame"] == article_frame
    assert result["delivery"]["draft_markdown"] == delivery["draft_markdown"]


def test_frame_only_repair_never_regenerates_body_sections(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    plan = build_section_repair_plan(
        delivery,
        precheck_report={
            "failed": [
                {"item": "FAQ", "detail": "FAQ answer needs clearer wording."}
            ]
        },
    )
    frame_calls = []

    def body_must_not_run(*args, **kwargs):
        raise AssertionError("body generator must not run for a frame-only repair")

    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-frame",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=body_must_not_run,
        generate_frame_text=_frame_generator(frame_calls),
        generate_claim_text=_claim_generator(),
        resume=False,
    )

    assert result["changed_body_units"] == []
    assert result["unchanged_body_units"] == delivery["body_section_order"]
    assert result["section_run"]["outputs"] == section_run["outputs"]
    assert result["frame_refreshed"] is True
    assert len(frame_calls) == 1
    assert result["generated_ledgers"] == 4
    assert result["remapped_ledgers"] == len(delivery["body_section_order"])


def test_tampered_link_repair_checkpoint_is_not_resumed(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    binding = next(item for item in delivery["bindings"] if item["kind"] == "product")
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={"link_issues": f"Review product link {binding['url']}"},
    )
    section_id = plan["actions"][0]["section_id"]
    run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-tampered-link",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=True,
    )
    package = build_section_repair_package(
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        plan=plan,
        section_id=section_id,
    )
    malicious_response = _repair_generator(change_visible=True)(
        "system",
        "SECTION REPAIR PACKAGE\n" + json.dumps(package),
    )
    malicious_output = parse_section_generation_response(
        malicious_response,
        package["generation_package"],
    )
    checkpoint = (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking-tampered-link"
        / "repair-checkpoints"
        / f"round-1-{section_id}.json"
    )
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    data["output"] = malicious_output
    checkpoint.write_text(json.dumps(data), encoding="utf-8")
    calls = []
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-tampered-link",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(calls),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=True,
    )

    assert result["generated_sections"] == 1
    assert result["resumed_sections"] == 0
    assert len(calls) == 1


def test_global_blocker_stops_before_any_generator(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    plan = build_section_repair_plan(
        delivery,
        postprocess_report={"fix_items": "评分失败，请手动检查"},
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("no generator should run")

    with pytest.raises(SectionRepairError, match="global blockers"):
        run_sectional_repair_sequence(
            workspace=tmp_path,
            slug="ceiling-marking-blocked",
            section_contracts=bundle["section_contracts"],
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            section_run=section_run,
            article_frame=article_frame,
            delivery=delivery,
            ledger_run=ledger_run,
            metadata=_metadata(),
            repair_plan=plan,
            generate_section_text=must_not_run,
            generate_frame_text=must_not_run,
            generate_claim_text=must_not_run,
        )


def test_repair_checkpoint_resumes_target_section(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    first = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-resume",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=True,
    )

    def section_must_not_run(*args, **kwargs):
        raise AssertionError("repair checkpoint should resume")

    second = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-resume",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=section_must_not_run,
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=True,
    )

    assert first["generated_sections"] == 1
    assert second["generated_sections"] == 0
    assert second["resumed_sections"] == 1


def test_multiple_section_repairs_use_prior_repaired_summary(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    first, second = delivery["body_section_order"][:2]
    first_sentence = next(
        item for item in delivery["sentences"] if item["section_id"] == first
    )
    second_sentence = next(
        item for item in delivery["sentences"] if item["section_id"] == second
    )
    plan = build_section_repair_plan(
        delivery,
        precheck_report={
            "checks": [
                {
                    "item": "事实校验",
                    "pass": False,
                    "level": "fail",
                    "fact_issues": [
                        {
                            "sentence_id": first_sentence["sentence_id"],
                            "sentence": first_sentence["text"],
                            "reason": "unsupported_factual_sentence",
                        },
                        {
                            "sentence_id": second_sentence["sentence_id"],
                            "sentence": second_sentence["text"],
                            "reason": "unsupported_factual_sentence",
                        },
                    ],
                }
            ]
        },
    )
    packages = []

    def generate(system, user):
        package = json.loads(user.split("SECTION REPAIR PACKAGE\n", 1)[1])
        packages.append(package)
        return _repair_generator()(system, user)

    run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-multiple",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=generate,
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=False,
    )

    assert len(packages) == 2
    assert "now separates documented guidance" in packages[1]["generation_package"][
        "previous_summary"
    ].casefold()


def test_repair_result_round_trip_and_corruption_rejection(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-result",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=False,
    )
    path = Path(
        persist_sectional_repair_result(
            tmp_path,
            "ceiling-marking-result",
            result,
        )
    )
    loaded = load_sectional_repair_result(
        tmp_path,
        "ceiling-marking-result",
        1,
        expected_plan_sha256=plan["plan_sha256"],
    )

    assert path == repair_result_path(tmp_path, "ceiling-marking-result", 1)
    assert loaded == result
    data = json.loads(path.read_text(encoding="utf-8"))
    data["changed_body_units"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_sectional_repair_result(
        tmp_path,
        "ceiling-marking-result",
        1,
    ) is None


def test_repair_result_rejects_nested_assembly_tampering(tmp_path):
    bundle, shadow, section_run, article_frame, delivery, ledger_run, _ = _setup(tmp_path)
    target = delivery["body_section_order"][0]
    sentence = next(item for item in delivery["sentences"] if item["section_id"] == target)
    plan = build_section_repair_plan(
        delivery,
        precheck_report=_fact_report(sentence["sentence_id"], sentence["text"]),
    )
    result = run_sectional_repair_sequence(
        workspace=tmp_path,
        slug="ceiling-marking-nested",
        section_contracts=bundle["section_contracts"],
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        section_run=section_run,
        article_frame=article_frame,
        delivery=delivery,
        ledger_run=ledger_run,
        metadata=_metadata(),
        repair_plan=plan,
        generate_section_text=_repair_generator(),
        generate_frame_text=_frame_generator(),
        generate_claim_text=_claim_generator(),
        resume=False,
    )
    tampered = copy.deepcopy(result)
    tampered["assembly"]["audit"]["metrics"]["word_count"] += 1
    unsigned = dict(tampered)
    unsigned.pop("result_sha256")
    tampered["result_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(SectionRepairError, match="assembly is invalid"):
        persist_sectional_repair_result(
            tmp_path,
            "ceiling-marking-nested",
            tampered,
        )


def test_claim_remap_rejects_non_unique_text(tmp_path):
    _, shadow, _, _, delivery, ledger_run, _ = _setup(tmp_path)
    unit = next(item for item in ledger_run["outputs"] if item["claims"])
    package = build_section_claim_package(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        unit["section_id"],
    )
    package = copy.deepcopy(package)
    claim_text = unit["claims"][0]["claim_text"]
    duplicate = copy.deepcopy(
        next(item for item in package["sentences"] if item["text"] == claim_text)
    )
    duplicate["sentence_id"] = "S999"
    package["sentences"].append(duplicate)
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

    with pytest.raises(SectionRepairError, match="does not map uniquely"):
        remap_section_claim_output(unit, package)
