import copy
import hashlib
import json
import os

import pytest

from data_sources.modules import seo_common, write_pre_check
from seo_ops.services.sectional_assembly import (
    SectionAssemblyError,
    assemble_sectional_article,
    assembly_paths,
    audit_sectional_delivery,
    load_sectional_assembly,
    persist_sectional_assembly,
    validate_assembly_metadata,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _section(section_id, unit_kind, heading, markdown, bindings=None):
    return {
        "section_id": section_id,
        "unit_kind": unit_kind,
        "heading": heading,
        "source_markdown_sha256": _sha(markdown),
        "markdown": markdown,
        "markdown_sha256": _sha(markdown),
        "bindings": list(bindings or []),
        "sentence_ids": [],
    }


def _build_delivery(
    *,
    links=True,
    duplicate_product=False,
    generic_anchor=False,
    untracked_link=False,
):
    topic = "Professional Ceiling Marking Tools"
    article_anchor = "click here" if generic_anchor else "ceiling tool selection guide"
    first_links = ""
    first_bindings = []
    if links:
        first_links = (
            f" Review the [{article_anchor}](https://example.com/blog/selection) "
            "and the ([source.example](https://source.example/selection))."
        )
        first_bindings = [
            {
                "kind": "article",
                "candidate_id": "article-selection",
                "anchor": article_anchor,
                "url": "https://example.com/blog/selection",
            },
            {
                "kind": "external_citation",
                "candidate_id": "ev-selection",
                "evidence_id": "ev-selection",
                "anchor": "source.example",
                "url": "https://source.example/selection",
            },
        ]
    second_links = ""
    second_bindings = []
    if links:
        second_links = (
            " A [related professional marking tool](https://example.com/p-T100.html) "
            "can be presented without claiming it was designed for every worksite."
        )
        second_bindings = [
            {
                "kind": "product",
                "candidate_id": "product-t100",
                "product_id": "T100",
                "anchor": "related professional marking tool",
                "url": "https://example.com/p-T100.html",
            }
        ]
    if duplicate_product:
        first_links += (
            " A [current marking product](https://example.com/p-T100.html) may also "
            "be considered when its documented limitations are explained."
        )
        first_bindings.append({
            "kind": "product",
            "candidate_id": "product-t100",
            "product_id": "T100",
            "anchor": "current marking product",
            "url": "https://example.com/p-T100.html",
        })
    if untracked_link:
        first_links += (
            " An [untracked resource](https://unknown.example/resource) must not "
            "bypass the Phase 4 binding registry."
        )

    intro = _section(
        "frame-introduction",
        "introduction",
        "Introduction",
        "# Professional Ceiling Marking Tools\n\n"
        "Professional teams should define the marking task before comparing equipment. "
        "Working distance, visibility, handling, and the surrounding area shape the decision. "
        "The current catalog provides the commercial options that can be discussed honestly. "
        "Evidence remains tied to the claims it actually supports.",
    )
    takeaways = _section(
        "frame-takeaways",
        "takeaways",
        "Key Takeaways",
        "> **Key Takeaways**\n"
        "> - Start with the real task and working conditions.\n"
        "> - Use products supported by the current catalog.\n"
        "> - Keep factual and safety guidance evidence-bound.",
    )
    first = _section(
        "section-1111111111",
        "body_section",
        "Choosing a Ceiling Marking Approach",
        "## Choosing a Ceiling Marking Approach\n\n"
        "A practical comparison begins with the area that must be identified and the people "
        "who need to see the mark. Teams should compare visibility, handling, and distance "
        "without assuming that one catalog item is purpose-built for every ceiling task."
        + first_links,
        first_bindings,
    )
    second = _section(
        "section-2222222222",
        "body_section",
        "Comparing Related Catalog Options",
        "## Comparing Related Catalog Options\n\n"
        "Related catalog products can be introduced when their actual specifications and "
        "limitations remain clear. The recommendation should explain why an option is relevant "
        "without inventing certification, compatibility, or a dedicated use case."
        + second_links,
        second_bindings,
    )
    conclusion = _section(
        "frame-conclusion",
        "conclusion",
        "Conclusion",
        "## Conclusion\n\n"
        "A reliable recommendation starts with the real task and the current product catalog. "
        "Readers can compare related options while keeping specifications, evidence, and limits "
        "clear. This approach remains useful when inventory or work conditions change.",
    )
    faq = _section(
        "frame-faq",
        "faq",
        "Frequently Asked Questions",
        "## Frequently Asked Questions\n\n"
        "### What should teams evaluate before choosing a marking tool?\n\n"
        "They should evaluate the task, working distance, visibility, handling requirements, "
        "catalog fit, and relevant site procedures before selecting an option.\n\n"
        "### Why should product data be checked before adding a link?\n\n"
        "Consistent product data prevents conflicting specifications from entering the article "
        "and allows the linked item to be described accurately for readers.\n\n"
        "### Can an old writing brief override the current catalog?\n\n"
        "No. A historical brief may suggest an angle, but the current catalog and approved site "
        "policy remain the commercial source of truth.",
    )
    sections = [intro, takeaways, first, second, conclusion, faq]
    draft = "\n\n".join(item["markdown"].strip() for item in sections).strip() + "\n"
    authoritative = seo_common.extract_draft_sentences(draft)
    records = []
    cursor = 0
    for section in sections:
        local = seo_common.extract_draft_sentences(section["markdown"])
        section["sentence_ids"] = []
        for item in local:
            expected = authoritative[cursor]
            assert item["text"] == expected["text"]
            section["sentence_ids"].append(expected["sentence_id"])
            records.append({**expected, "section_id": section["section_id"]})
            cursor += 1
    bindings = [
        {"section_id": section["section_id"], **binding}
        for section in sections
        for binding in section["bindings"]
    ]
    delivery = {
        "version": 1,
        "topic": topic,
        "content_language": "en",
        "body_section_order": ["section-1111111111", "section-2222222222"],
        "section_order": [section["section_id"] for section in sections],
        "sections": sections,
        "bindings": bindings,
        "draft_markdown": draft,
        "draft_sha256": _sha(draft),
        "sentences": records,
    }
    delivery["delivery_sha256"] = _digest(delivery)
    return delivery


def _metadata(*, minimum=100, maximum=1200):
    seo_title = "Professional Ceiling Marking Tools for Worksite Teams"
    assert 50 <= len(seo_title) <= 60
    description = (
        "Professional ceiling marking guidance helps worksite teams compare related tools, "
        "review visibility needs, and keep catalog recommendations accurate and clear."
    )
    assert 150 <= len(description) <= 160
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
        "seo_title": seo_title,
        "seo_description": description,
        "seo_keywords": ["professional ceiling marking tools", "worksite marking"],
        "target_words": {"min": minimum, "max": maximum},
    }


def _ledger(delivery):
    return {
        "version": 1,
        "draft_sha256": delivery["draft_sha256"],
        "claims": [],
    }


def test_assembly_adds_frontmatter_schema_and_preserves_sentence_ids():
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))

    assert assembly["draft_markdown"].startswith("---\nTitle: Professional")
    assert '<script type="application/ld+json">' in assembly["draft_markdown"]
    assert write_pre_check._has_valid_faq_schema(assembly["draft_markdown"])
    assert assembly["claim_ledger"]["draft_sha256"] == assembly["draft_sha256"]
    assert seo_common.extract_draft_sentences(assembly["draft_markdown"]) == [
        {
            "sentence_id": item["sentence_id"],
            "text": item["text"],
            "norm": item["norm"],
        }
        for item in delivery["sentences"]
    ]


def test_faq_schema_matches_visible_questions_and_answers():
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))
    entities = assembly["faq_schema"]["mainEntity"]

    assert len(entities) == 3
    assert entities[0]["name"] == (
        "What should teams evaluate before choosing a marking tool?"
    )
    assert "working distance" in entities[0]["acceptedAnswer"]["text"]


def test_advisory_link_ratios_never_force_missing_links():
    delivery = _build_delivery(links=False)
    audit = audit_sectional_delivery(delivery, _metadata())

    assert audit["passed"] is True
    assert audit["blockers"] == []
    assert audit["metrics"]["link_counts"] == {
        "article": 0,
        "product": 0,
        "external_citation": 0,
    }
    assert all(
        item["code"] == "advisory_link_density_below_ratio"
        or item["code"].startswith("primary_keyword_")
        or item["code"] == "main_h2_count_outside_recommended_range"
        for item in audit["warnings"]
    )


def test_metadata_rejects_unsupported_compliance_language():
    metadata = _metadata()
    metadata["seo_description"] = (
        "Compare professional ceiling marking tools for worksite teams, including "
        "OSHA compliant options, practical requirements, product details, and selection."
    )
    assert 150 <= len(metadata["seo_description"]) <= 160

    with pytest.raises(
        SectionAssemblyError,
        match="unsupported authority or compliance language",
    ):
        validate_assembly_metadata(metadata)


def test_wavelength_color_mismatch_is_an_assembly_blocker():
    delivery = _build_delivery()
    body_section = next(
        item for item in delivery["sections"] if item["unit_kind"] == "body_section"
    )
    body_section["markdown"] += (
        "\n\nA 520nm blue laser and a 450nm green laser are contradictory labels."
    )
    body_section["markdown_sha256"] = _sha(body_section["markdown"])
    delivery["draft_markdown"] = (
        "\n\n".join(item["markdown"].strip() for item in delivery["sections"]).strip() + "\n"
    )
    delivery["draft_sha256"] = _sha(delivery["draft_markdown"])
    authoritative = seo_common.extract_draft_sentences(delivery["draft_markdown"])
    records = []
    cursor = 0
    for section in delivery["sections"]:
        local = seo_common.extract_draft_sentences(section["markdown"])
        section["sentence_ids"] = []
        for item in local:
            expected = authoritative[cursor]
            assert item["text"] == expected["text"]
            section["sentence_ids"].append(expected["sentence_id"])
            records.append({**expected, "section_id": section["section_id"]})
            cursor += 1
    delivery["sentences"] = records
    delivery["delivery_sha256"] = _digest(
        {key: value for key, value in delivery.items() if key != "delivery_sha256"}
    )

    audit = audit_sectional_delivery(delivery, _metadata())

    assert "wavelength_color_mismatch" in {item["code"] for item in audit["blockers"]}


def test_duplicate_product_target_is_a_hard_blocker():
    delivery = _build_delivery(duplicate_product=True)
    audit = audit_sectional_delivery(delivery, _metadata())

    assert audit["passed"] is False
    assert "duplicate_internal_link_target" in {
        item["code"] for item in audit["blockers"]
    }
    with pytest.raises(SectionAssemblyError, match="global assembly gates failed"):
        assemble_sectional_article(delivery, _metadata(), _ledger(delivery))


def test_generic_anchor_is_a_hard_blocker():
    delivery = _build_delivery(generic_anchor=True)
    audit = audit_sectional_delivery(delivery, _metadata())

    assert "generic_or_too_short_anchor" in {
        item["code"] for item in audit["blockers"]
    }


def test_untracked_markdown_link_is_a_hard_blocker():
    delivery = _build_delivery(untracked_link=True)
    audit = audit_sectional_delivery(delivery, _metadata())

    assert "markdown_links_do_not_match_phase4_bindings" in {
        item["code"] for item in audit["blockers"]
    }


def test_word_floor_is_a_hard_blocker():
    delivery = _build_delivery()
    audit = audit_sectional_delivery(delivery, _metadata(minimum=1000))

    assert "article_below_target_word_minimum" in {
        item["code"] for item in audit["blockers"]
    }


def test_input_ledger_must_match_phase4_draft():
    delivery = _build_delivery()
    ledger = _ledger(delivery)
    ledger["draft_sha256"] = "0" * 64

    with pytest.raises(SectionAssemblyError, match="does not match the Phase 4 draft"):
        assemble_sectional_article(delivery, _metadata(), ledger)


def test_persist_and_load_three_file_bundle(tmp_path):
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))
    paths = persist_sectional_assembly(tmp_path, assembly)

    assert set(paths) == {"draft", "ledger", "report"}
    assert all(os.path.exists(path) for path in paths.values())
    assert load_sectional_assembly(
        tmp_path,
        assembly["metadata"]["slug"],
        expected_assembly_sha256=assembly["assembly_sha256"],
    ) == assembly


def test_corrupted_bundle_is_not_loaded(tmp_path):
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))
    paths = persist_sectional_assembly(tmp_path, assembly)
    with open(paths["draft"], "a", encoding="utf-8") as handle:
        handle.write("tampered")

    assert load_sectional_assembly(
        tmp_path,
        assembly["metadata"]["slug"],
    ) is None


def test_partial_replace_failure_restores_previous_bundle(tmp_path, monkeypatch):
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))
    paths = assembly_paths(tmp_path, assembly["metadata"]["slug"])
    for key, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"old-{key}", encoding="utf-8")
    original = os.replace
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second replace failure")
        return original(source, destination)

    monkeypatch.setattr("seo_ops.services.sectional_assembly.os.replace", fail_second)

    with pytest.raises(OSError, match="simulated second replace failure"):
        persist_sectional_assembly(tmp_path, assembly)
    assert {
        key: path.read_text(encoding="utf-8")
        for key, path in paths.items()
    } == {key: f"old-{key}" for key in paths}


def test_assembly_validation_detects_nested_tampering():
    delivery = _build_delivery()
    assembly = assemble_sectional_article(delivery, _metadata(), _ledger(delivery))
    tampered = copy.deepcopy(assembly)
    tampered["faq_schema"]["mainEntity"][0]["name"] = "Changed?"

    from seo_ops.services.sectional_assembly import validate_sectional_assembly

    with pytest.raises(SectionAssemblyError, match="FAQ schema"):
        validate_sectional_assembly(tampered)
