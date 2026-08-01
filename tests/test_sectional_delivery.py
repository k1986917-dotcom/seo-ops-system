import copy
import hashlib
import json
from pathlib import Path

import pytest

from seo_ops.services.ai import AIEmptyTextError
from seo_ops.services.sectional_context import (
    build_candidate_registry,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_delivery import (
    LEDGER_MAX_TOKENS,
    SectionDeliveryError,
    build_section_claim_package,
    build_section_claim_prompt,
    load_resolved_delivery,
    load_section_ledger_checkpoint,
    merge_section_claim_ledgers,
    parse_section_claim_response,
    persist_resolved_delivery,
    persist_section_ledger_checkpoint,
    resolve_section_placeholders,
    resolved_delivery_path,
    run_section_claim_ledger_sequence,
    section_ledger_checkpoint_path,
    sectional_link_hard_caps,
    validate_resolved_delivery,
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
    parse_section_placeholders,
)
from seo_ops.services.sectional_writing import build_contract_bundle

ARTICLES = """# Internal Links

| Title | URL | Primary Keyword |
|---|---|---|
| Ceiling Tool Selection Guide | https://example.com/blog/tool-selection | ceiling tool selection |
| Worksite Safety Guide | https://example.com/blog/worksite-safety | worksite safety |
| Battery Maintenance Guide | https://example.com/blog/battery | battery maintenance |
| Unrelated Landscaping Guide | https://example.com/blog/landscaping | landscape planning |
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
        {
            "evidence_id": "ev_unrelated",
            "source_url": "https://source.example/landscape",
            "support": "Landscaping plans may include seasonal planting schedules.",
            "concepts": ["landscaping", "planting"],
            "claim_types": ["general"],
            "required": False,
        },
    ]
}


def _response(package):
    gates = package["link_gates"]
    used = {key: [] for key in gates}
    first_links = []
    second_links = []

    if gates["article_links"]["selected_ids"]:
        candidate_id = gates["article_links"]["selected_ids"][0]
        used["article_links"].append(candidate_id)
        first_links.append(f"[[ARTICLE:{candidate_id}|ceiling tool selection guidance]]")
    if gates["product_links"]["opportunity_state"] != "none":
        candidate_id = gates["product_links"]["selected_ids"][0]
        used["product_links"].append(candidate_id)
        second_links.append(f"[[PRODUCT:{candidate_id}|related professional marking tool]]")
    if gates["external_citations"]["selected_ids"]:
        candidate_id = gates["external_citations"]["selected_ids"][0]
        used["external_citations"].append(candidate_id)
        second_links.append(f"[[CITE:{candidate_id}]]")

    first = (
        "Clear ceiling marking helps a team identify the intended location and "
        "communicate the task before work begins. The practical choice depends on "
        "visibility, handling, and the surrounding work area."
    )
    second = (
        "Professionals should compare the available catalog options with the actual "
        "working distance and established site procedures. A related product may be "
        "presented without claiming it was designed or certified for this exact use."
    )
    if first_links:
        first += " " + " ".join(first_links)
    if second_links:
        second += " " + " ".join(second_links)

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


def _frame_response():
    intro = " ".join(
        [
            "Professional teams should define the ceiling marking task before selecting equipment.",
            "They should review distance, visibility, handling, and the surrounding work area.",
            "The current catalog provides the commercial options that can be discussed honestly.",
            "Supporting evidence should remain tied to the claims it actually supports.",
            "A related product can be useful without being described as purpose-built.",
            "Clear stages help readers compare options without confusing advice and verification.",
            "Consistent product data also prevents conflicting specifications from entering recommendations.",
            "This process keeps the article useful when products or site conditions change.",
        ]
    )
    conclusion = " ".join(
        [
            "A reliable marking workflow begins with the real task and the current work area.",
            "Readers should compare available options against distance, visibility, and handling needs.",
            "Product recommendations should reflect the live catalog rather than an old writing note.",
            "Safety and compliance statements should remain connected to appropriate evidence.",
            "Conflicting catalog data should be reported before an automatic product link is created.",
            "Related products may still be presented when their limitations are stated accurately.",
            "This approach produces a recommendation that is useful, maintainable, and transparent.",
            "It also avoids turning an unsupported assumption into a product or regulatory claim.",
        ]
    )
    faq = {
        "faqs": [
            {
                "question": "What should professionals evaluate before choosing a marking tool?",
                "answer": "They should evaluate the task, working distance, visibility conditions, handling requirements, catalog fit, and relevant site procedures before selecting an option.",
            },
            {
                "question": "Why should product data be checked before adding a link?",
                "answer": "Consistent product data prevents conflicting specifications from entering the article and allows the linked product to be described accurately.",
            },
            {
                "question": "Can a historical brief override the current catalog?",
                "answer": "No. A historical brief may suggest an angle, but the current catalog and approved site policy remain the commercial source of truth.",
            },
        ]
    }
    for item in faq["faqs"]:
        item["answer"] += (
            " This additional explanation keeps the recommendation clear for "
            "readers reviewing the current catalog and work requirements."
        )
    return (
        f"{FRAME_INTRO_MARKER}\n{intro}\n"
        f"{FRAME_TAKEAWAYS_MARKER}\n"
        "- Start with the real task and working conditions.\n"
        "- Use products supported by the current catalog.\n"
        "- Keep safety guidance evidence-bound.\n"
        f"{FRAME_CONCLUSION_MARKER}\n{conclusion}\n"
        f"{FRAME_FAQ_MARKER}\n{json.dumps(faq)}"
    )


def _setup():
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
        section["target_words"] = {"min": 35, "max": 130}
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
        output = parse_section_generation_response(_response(package), package)
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
    delivery = resolve_section_placeholders(
        section_run,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        article_frame=article_frame,
    )
    section_run["article_frame"] = article_frame
    return bundle, shadow, section_run, delivery


def _claim_response(package, *, evidence_id=None, sentence_id=None):
    body_sentences = [
        item for item in package["sentences"] if not item["text"].lstrip().startswith(chr(35))
    ]
    selected_sentence = sentence_id or body_sentences[0]["sentence_id"]
    selected_evidence = evidence_id or package["evidence"][0]["evidence_id"]
    return json.dumps(
        {
            "version": 1,
            "claims": [
                {
                    "sentence_id": selected_sentence,
                    "claim_type": "general",
                    "evidence_ids": [selected_evidence],
                }
            ],
        }
    )


def _generator_from_prompt(calls=None):
    calls = calls if calls is not None else []

    def generate(system, user, **kwargs):
        package = json.loads(user.split("SECTION CLAIM PACKAGE\n", 1)[1])
        calls.append({"system": system, "package": package, **kwargs})
        return _claim_response(package)

    return generate


def test_resolve_placeholders_binds_registry_urls_and_assigns_global_sentences():
    _, shadow, _, delivery = _setup()

    assert "[[" not in delivery["draft_markdown"]
    assert "> **Key Takeaways**" in delivery["draft_markdown"]
    assert "## Frequently Asked Questions" in delivery["draft_markdown"]
    assert "https://example.com/" in delivery["draft_markdown"]
    assert "https://source.example/" in delivery["draft_markdown"] or (
        "https://safety.example/" in delivery["draft_markdown"]
    )
    assert (
        delivery["draft_sha256"]
        == hashlib.sha256(delivery["draft_markdown"].encode("utf-8")).hexdigest()
    )
    assert [item["sentence_id"] for item in delivery["sentences"]] == [
        f"S{index:03d}" for index in range(1, len(delivery["sentences"]) + 1)
    ]
    assert all(item["sentence_ids"] for item in delivery["sections"])
    assert any(item["kind"] == "product" for item in delivery["bindings"])
    assert any(item["kind"] == "external_citation" for item in delivery["bindings"])
    product_binding = next(item for item in delivery["bindings"] if item["kind"] == "product")
    assert len(product_binding["catalog_provenance"]["catalog_sha256"]) == 64
    assert product_binding["catalog_provenance"]["catalog_facts"]
    assert delivery["topic"] == shadow["context_manifest"]["topic"]


def test_resolved_delivery_rejects_tampered_product_catalog_provenance():
    _, _, _, delivery = _setup()
    tampered = copy.deepcopy(delivery)
    product_binding = next(item for item in tampered["bindings"] if item["kind"] == "product")
    product_binding["catalog_provenance"]["catalog_facts"]["Reach"] = "invented range"
    section_binding = next(
        binding
        for section in tampered["sections"]
        for binding in section["bindings"]
        if binding["kind"] == "product"
    )
    section_binding["catalog_provenance"]["catalog_facts"]["Reach"] = "invented range"
    unsigned = dict(tampered)
    unsigned.pop("delivery_sha256")
    tampered["delivery_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(SectionDeliveryError, match="catalog provenance SHA mismatch"):
        validate_resolved_delivery(tampered)


def test_global_link_allocation_prefers_required_sections_and_unique_targets():
    _, shadow, section_run, delivery = _setup()
    raw_article_ids = [
        item["candidate_id"]
        for output in section_run["outputs"]
        for item in parse_section_placeholders(output["markdown"])["article_links"]
    ]
    duplicate_id = next(
        candidate_id for candidate_id in raw_article_ids if raw_article_ids.count(candidate_id) > 1
    )
    required_sections = {
        item["section_id"]
        for item in shadow["section_link_contracts"]["sections"]
        if item["article_links"]["min_required"] > 0
    }
    bound_duplicate = [
        item
        for item in delivery["bindings"]
        if item["kind"] == "article" and item["candidate_id"] == duplicate_id
    ]
    assert len(bound_duplicate) == 1
    assert bound_duplicate[0]["section_id"] in required_sections

    for kind in ("article", "product", "external_citation"):
        urls = [item["url"] for item in delivery["bindings"] if item["kind"] == kind]
        assert len(urls) == len(set(urls))


def test_sectional_link_caps_use_one_external_citation_per_600_words():
    assert sectional_link_hard_caps(2770) == {
        "article": 8,
        "product": 7,
        "external_citation": 5,
    }


def test_resolved_delivery_round_trip_and_frontmatter_stability(tmp_path):
    from data_sources.modules import seo_common

    _, _, _, delivery = _setup()
    path = Path(
        persist_resolved_delivery(
            tmp_path,
            "ceiling-marking",
            delivery,
        )
    )
    loaded = load_resolved_delivery(
        tmp_path,
        "ceiling-marking",
        expected_delivery_sha256=delivery["delivery_sha256"],
    )

    assert path == resolved_delivery_path(tmp_path, "ceiling-marking")
    assert loaded == delivery
    with_frontmatter = (
        "---\nTitle: Professional Ceiling Marking Tools\nSlug: ceiling-marking\n---\n\n"
        + delivery["draft_markdown"]
    )
    assert seo_common.extract_draft_sentences(with_frontmatter) == [
        {
            "sentence_id": item["sentence_id"],
            "text": item["text"],
            "norm": item["norm"],
        }
        for item in delivery["sentences"]
    ]


def test_corrupted_resolved_delivery_is_not_loaded(tmp_path):
    _, _, _, delivery = _setup()
    path = Path(
        persist_resolved_delivery(
            tmp_path,
            "ceiling-marking",
            delivery,
        )
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sections"][0]["markdown"] += " changed"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_resolved_delivery(tmp_path, "ceiling-marking") is None


def test_delivery_rejects_candidate_not_approved_for_the_section():
    setup_result = _setup()
    _, shadow, section_run, _ = setup_result
    tampered = copy.deepcopy(section_run)
    output = next(
        item
        for item in tampered["outputs"]
        if parse_section_placeholders(item["markdown"])["article_links"]
    )
    parsed = parse_section_placeholders(output["markdown"])
    old = parsed["article_links"][0]
    approved = {
        item["candidate_id"]
        for item in next(
            section
            for section in shadow["context_manifest"]["sections"]
            if section["section_id"] == output["section_id"]
        )["article_candidates"]
    }
    registry_ids = {
        item["candidate_id"]
        for item in shadow["context_manifest"]["registry"]["articles"]["candidates"]
    }
    replacement_id = next(iter(registry_ids - approved))
    old_token = f"[[ARTICLE:{old['candidate_id']}|{old['anchor']}]]"
    new_token = f"[[ARTICLE:{replacement_id}|{old['anchor']}]]"
    output["markdown"] = output["markdown"].replace(old_token, new_token)
    output["markdown_sha256"] = hashlib.sha256(output["markdown"].encode("utf-8")).hexdigest()
    output["used_ids"]["article_links"] = [replacement_id]
    output["decisions"]["article_links"]["used_ids"] = [replacement_id]

    with pytest.raises(SectionDeliveryError, match="not approved"):
        resolve_section_placeholders(
            tampered,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
        )


def test_delivery_rejects_conflicted_product_even_if_output_is_tampered():
    _, shadow, section_run, _ = _setup()
    context = copy.deepcopy(shadow["context_manifest"])
    product_id = next(
        record["candidate_id"]
        for output in section_run["outputs"]
        for record in parse_section_placeholders(output["markdown"])["product_links"]
    )
    product = next(
        item
        for item in context["registry"]["products"]["candidates"]
        if item["candidate_id"] == product_id
    )
    product["attribute_conflicts"] = [{"field": "power", "table": "100", "detail": "200"}]
    context["registry_sha256"] = hashlib.sha256(
        json.dumps(
            context["registry"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(SectionDeliveryError, match="conflicted product"):
        resolve_section_placeholders(
            section_run,
            shadow["section_link_contracts"],
            context,
        )


def test_delivery_rejects_registry_manifest_sha_tampering():
    _, shadow, section_run, _ = _setup()
    context = copy.deepcopy(shadow["context_manifest"])
    context["registry"]["articles"]["candidates"][0]["title"] = "Tampered title"

    with pytest.raises(SectionDeliveryError, match="registry SHA mismatch"):
        resolve_section_placeholders(
            section_run,
            shadow["section_link_contracts"],
            context,
        )


def test_link_contract_none_blocks_manifest_product_candidate():
    _, shadow, section_run, _ = _setup()
    tampered = copy.deepcopy(section_run)
    target = next(
        item
        for item in tampered["outputs"]
        if next(
            section
            for section in shadow["section_link_contracts"]["sections"]
            if section["section_id"] == item["section_id"]
        )["product_links"]["opportunity_state"]
        == "none"
    )
    manifest = next(
        item
        for item in shadow["context_manifest"]["sections"]
        if item["section_id"] == target["section_id"]
    )
    assert manifest["product_candidates"]
    candidate_id = manifest["product_candidates"][0]["candidate_id"]
    token = f"[[PRODUCT:{candidate_id}|related marking tool]]"
    target["markdown"] += f"\n\n{token}"
    target["markdown_sha256"] = hashlib.sha256(target["markdown"].encode("utf-8")).hexdigest()
    target["used_ids"]["product_links"] = [candidate_id]
    target["decisions"]["product_links"] = {
        "used_ids": [candidate_id],
        "reason_code": "tampered",
    }

    with pytest.raises(SectionDeliveryError, match="not approved"):
        resolve_section_placeholders(
            tampered,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
        )


def test_claim_package_contains_only_one_sections_global_ids_and_evidence():
    _, shadow, _, delivery = _setup()
    section_id = delivery["body_section_order"][1]
    package = build_section_claim_package(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section_id,
    )

    assert {item["sentence_id"] for item in package["sentences"]} == set(
        next(item for item in delivery["sections"] if item["section_id"] == section_id)[
            "sentence_ids"
        ]
    )
    assert all(item["evidence_id"].startswith("ev_") for item in package["evidence"])
    assert all(
        item["section_id"] == section_id
        for item in delivery["sentences"]
        if item["sentence_id"] in {row["sentence_id"] for row in package["sentences"]}
    )
    prompt = build_section_claim_prompt(package)
    assert "claim_text" in prompt["system"]
    assert delivery["draft_markdown"] not in prompt["user"]


def test_claim_response_server_fills_claim_text_and_rejects_unapproved_ids():
    _, shadow, _, delivery = _setup()
    package = build_section_claim_package(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        delivery["section_order"][0],
    )
    canonical = parse_section_claim_response(_claim_response(package), package)

    claim = canonical["claims"][0]
    sentence = next(
        item for item in package["sentences"] if item["sentence_id"] == claim["sentence_id"]
    )
    assert claim["claim_text"] == sentence["text"]

    with pytest.raises(SectionDeliveryError, match="unapproved evidence"):
        parse_section_claim_response(
            _claim_response(package, evidence_id="ev_not_allowed"),
            package,
        )
    with pytest.raises(SectionDeliveryError, match="sentence_id is not allowed"):
        parse_section_claim_response(
            _claim_response(package, sentence_id="S999"),
            package,
        )
    bad = json.loads(_claim_response(package))
    bad["claims"][0]["claim_text"] = "model supplied text"
    with pytest.raises(SectionDeliveryError, match="shape is invalid"):
        parse_section_claim_response(json.dumps(bad), package)


def test_ledger_runner_disables_thinking_and_resumes_checkpoints(tmp_path):
    _, shadow, _, delivery = _setup()
    calls = []
    first = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=_generator_from_prompt(calls),
    )

    assert first["generated_count"] == len(delivery["section_order"])
    assert first["resumed_count"] == 0
    assert all(call["thinking_mode"] == "disabled" for call in calls)
    assert all(call["max_tokens"] == LEDGER_MAX_TOKENS for call in calls)

    def must_not_run(*args, **kwargs):
        raise AssertionError("valid ledger checkpoints should have resumed")

    resumed = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=must_not_run,
    )
    assert resumed["generated_count"] == 0
    assert resumed["resumed_count"] == len(delivery["section_order"])


def test_non_retryable_length_empty_response_stops_after_one_call(tmp_path):
    _, shadow, _, delivery = _setup()
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AIEmptyTextError(
            finish_reason="length",
            completion_tokens=4000,
            reasoning_tokens=4000,
        )

    with pytest.raises(SectionDeliveryError, match="provider stopped"):
        run_section_claim_ledger_sequence(
            workspace=tmp_path,
            slug="ceiling-marking",
            delivery=delivery,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=fail,
        )
    assert calls == 1


def test_invalid_json_retries_once_then_succeeds(tmp_path):
    _, shadow, _, delivery = _setup()
    calls = 0

    def generate(system, user, **kwargs):
        nonlocal calls
        calls += 1
        package = json.loads(user.split("SECTION CLAIM PACKAGE\n", 1)[1].split("\n\n", 1)[0])
        if calls == 1:
            return "{"
        return _claim_response(package)

    result = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=generate,
    )
    assert result["complete"] is True
    assert calls == len(delivery["section_order"]) + 1


def test_corrupted_ledger_checkpoint_is_not_resumed(tmp_path):
    _, shadow, _, delivery = _setup()
    section_id = delivery["section_order"][0]
    package = build_section_claim_package(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        section_id,
    )
    output = parse_section_claim_response(_claim_response(package), package)
    path = Path(
        persist_section_ledger_checkpoint(
            tmp_path,
            "ceiling-marking",
            package,
            output,
        )
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["output"]["claims"][0]["claim_text"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert (
        load_section_ledger_checkpoint(
            tmp_path,
            "ceiling-marking",
            package,
        )
        is None
    )


def test_checkpoint_atomic_write_restores_previous_bytes(tmp_path, monkeypatch):
    from seo_ops.services import sectional_delivery as sd

    _, shadow, _, delivery = _setup()
    package = build_section_claim_package(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        delivery["section_order"][0],
    )
    output = parse_section_claim_response(_claim_response(package), package)
    path = Path(
        persist_section_ledger_checkpoint(
            tmp_path,
            "ceiling-marking",
            package,
            output,
        )
    )
    snapshot = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated ledger checkpoint failure")

    monkeypatch.setattr(sd.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated ledger checkpoint"):
        persist_section_ledger_checkpoint(
            tmp_path,
            "ceiling-marking",
            package,
            output,
        )
    assert path.read_bytes() == snapshot


def test_merge_section_ledgers_binds_final_draft_sha_and_revalidates_outputs(tmp_path):
    _, shadow, _, delivery = _setup()
    ledger_run = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=_generator_from_prompt(),
    )
    merged = merge_section_claim_ledgers(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        ledger_run,
    )

    assert merged["draft_sha256"] == delivery["draft_sha256"]
    assert merged["claims"]
    assert [int(item["sentence_id"][1:]) for item in merged["claims"]] == sorted(
        int(item["sentence_id"][1:]) for item in merged["claims"]
    )
    assert all(
        item["claim_text"]
        == next(
            sentence["text"]
            for sentence in delivery["sentences"]
            if sentence["sentence_id"] == item["sentence_id"]
        )
        for item in merged["claims"]
    )

    tampered = copy.deepcopy(ledger_run)
    tampered["outputs"][0]["claims"][0]["claim_text"] = "tampered"
    with pytest.raises(SectionDeliveryError, match="canonical"):
        merge_section_claim_ledgers(
            delivery,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            tampered,
        )


def test_merged_ledger_passes_legacy_authoritative_validator(tmp_path):
    from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

    _, shadow, _, delivery = _setup()
    ledger_run = run_section_claim_ledger_sequence(
        workspace=tmp_path,
        slug="ceiling-marking",
        delivery=delivery,
        link_contracts=shadow["section_link_contracts"],
        context_manifest=shadow["context_manifest"],
        generate_text=_generator_from_prompt(),
    )
    merged = merge_section_claim_ledgers(
        delivery,
        shadow["section_link_contracts"],
        shadow["context_manifest"],
        ledger_run,
    )

    validated = _validate_claim_ledger_json(
        json.dumps(merged),
        delivery["draft_markdown"],
    )
    assert validated == merged


def test_checkpoint_path_is_scoped_to_slug_and_section(tmp_path):
    path = section_ledger_checkpoint_path(
        tmp_path,
        "ceiling-marking",
        "section-0123456789",
    )
    assert path == (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking"
        / "ledger-checkpoints"
        / "section-0123456789.json"
    )
