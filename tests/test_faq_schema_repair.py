import json

from data_sources.modules import write_pre_check
from seo_ops.services import legacy_workflow as lw


def _faq_payload():
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [],
    }


def _script(payload, attrs='type="application/ld+json"'):
    return (
        f"<script {attrs}>\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n</script>"
    )


def _faq_draft(question_count=3):
    questions = []
    for index in range(1, question_count + 1):
        questions.append(
            f"### Question {index}?\n\n"
            f"Answer {index} comes directly from the visible article text."
        )
    return (
        "---\n"
        "Title: Laser Pointer Planning Guide\n"
        "SEO Title: Laser Pointer Planning Guide for Commercial Projects\n"
        "SEO Description: A practical planning guide for choosing and using a "
        "laser pointer in commercial projects while reviewing workflow, safety, "
        "visibility, and jobsite communication needs.\n"
        "SEO Keywords: laser pointer, commercial construction\n"
        "---\n\n"
        "# Laser Pointer Planning Guide\n\n"
        "This guide focuses on laser pointer planning for commercial projects.\n\n"
        "## Laser Pointer Planning Basics\n\n"
        "Review the intended workflow before choosing an approach.\n\n"
        "## Laser Pointer Use on Commercial Projects\n\n"
        "Compare the available options with the needs of the project.\n\n"
        "> **Key Takeaways**\n"
        "> - Match the approach to the intended workflow.\n"
        "> - Review visibility and communication needs.\n"
        "> - Confirm the choice fits the project environment.\n\n"
        "## Frequently Asked Questions\n\n"
        + "\n\n".join(questions)
        + "\n"
    )


def _extract_schema_payload(text):
    match = write_pre_check._JSON_LD_SCRIPT_BLOCK_RE.search(text)
    assert match is not None
    return json.loads(match.group("payload"))


def test_faq_schema_validator_accepts_supported_jsonld_forms():
    standard = _script(_faq_payload())
    single_quote_and_extra_attrs = _script(
        _faq_payload(),
        "nonce='abc' TYPE = 'application/ld+json' data-kind='schema'",
    )
    root_array = _script([
        {"@type": "Article"},
        _faq_payload(),
    ])
    graph = _script({
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Article"},
            _faq_payload(),
        ],
    })

    for raw in (
        standard,
        single_quote_and_extra_attrs,
        root_array,
        graph,
    ):
        assert write_pre_check._has_valid_faq_schema(raw) is True


def test_faq_schema_validator_rejects_false_positives():
    assert write_pre_check._has_valid_faq_schema(
        "The text mentions FAQPage but contains no JSON-LD."
    ) is False
    assert write_pre_check._has_valid_faq_schema(
        '<script type="application/ld+json">{"@type":</script>'
    ) is False
    assert write_pre_check._has_valid_faq_schema(
        _script({"@type": "Article"})
    ) is False
    assert write_pre_check._has_valid_faq_schema(
        '<script type="text/javascript">{"@type":"FAQPage"}</script>'
    ) is False


def test_ensure_w1b_faq_schema_builds_from_visible_faq_only():
    generated = lw._ensure_w1b_faq_schema(_faq_draft())
    payload = _extract_schema_payload(generated)

    assert payload["@type"] == "FAQPage"
    assert len(payload["mainEntity"]) == 3
    for index, entity in enumerate(payload["mainEntity"], start=1):
        assert entity["name"] == f"Question {index}?"
        assert (
            entity["acceptedAnswer"]["text"]
            == f"Answer {index} comes directly from the visible article text."
        )


def test_ensure_w1b_faq_schema_is_idempotent():
    once = lw._ensure_w1b_faq_schema(_faq_draft())
    twice = lw._ensure_w1b_faq_schema(once)

    assert twice == once
    assert len(
        list(write_pre_check._JSON_LD_SCRIPT_BLOCK_RE.finditer(twice))
    ) == 1


def test_ensure_w1b_faq_schema_preserves_existing_valid_schema():
    existing = _faq_draft() + "\n" + _script(_faq_payload()) + "\n"

    assert lw._ensure_w1b_faq_schema(existing) == existing


def test_ensure_w1b_faq_schema_requires_three_visible_questions():
    two_questions = _faq_draft(question_count=2)
    no_faq_heading = _faq_draft().replace(
        "## Frequently Asked Questions",
        "## Additional Guidance",
    )

    assert lw._ensure_w1b_faq_schema(two_questions) == two_questions
    assert lw._ensure_w1b_faq_schema(no_faq_heading) == no_faq_heading


def test_generated_schema_is_excluded_from_prose_and_fact_audit():
    generated = lw._ensure_w1b_faq_schema(_faq_draft())
    match = write_pre_check._JSON_LD_SCRIPT_BLOCK_RE.search(generated)
    assert match is not None
    schema_block = match.group(0)

    assert write_pre_check._strip_non_prose_blocks(schema_block).strip() == ""
    assert write_pre_check._extract_factual_sentences(schema_block) == []


def test_w1b_repair_contract_mentions_real_faq_schema(tmp_path):
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_faq_draft(), encoding="utf-8")

    contract = lw._w1b_repair_contract(
        draft_path,
        "Laser Pointer Planning Guide",
    )

    assert "FAQPage" in contract
    assert "application/ld+json" in contract
    assert "Do not invent FAQ questions or answers" in contract


def test_normalize_w1b_candidate_fixes_schema_check(tmp_path):
    normalized = lw._normalize_w1b_candidate(
        _faq_draft(),
        "laser pointer",
    )
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(normalized, encoding="utf-8")

    result = write_pre_check.run(
        str(draft_path),
        tier="Cluster Content",
        keywords="laser pointer",
    )
    schema_check = next(
        check
        for check in result["checks"]
        if check["item"] == "FAQ Schema 存在"
    )

    assert schema_check["pass"] is True
    assert write_pre_check._has_valid_faq_schema(normalized) is True
