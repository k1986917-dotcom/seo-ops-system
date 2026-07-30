from data_sources.modules import write_pre_check
from seo_ops.services import legacy_workflow as lw

_PRIMARY_KEYWORD = "laser pointer"


def _draft_with_seo_title(seo_title: str) -> str:
    return (
        "---\n"
        "Title: Laser Pointer Planning Guide\n"
        f"SEO Title: {seo_title}\n"
        "SEO Description: " + "x" * 155 + "\n"
        f"SEO Keywords: {_PRIMARY_KEYWORD}\n"
        "---\n\n"
        "# Laser Pointer Planning Guide\n\n"
        "This guide focuses on laser pointer.\n\n"
        "## Laser Pointer Basics\n\n"
        "Some content here.\n\n"
        "## Laser Pointer Use\n\n"
        "More content here.\n\n"
        "> **Key Takeaways**\n"
        "> - first\n"
        "> - second\n"
        "> - third\n\n"
        "## Frequently Asked Questions\n\n"
        "### Q1\n\nA1.\n\n"
        "### Q2\n\nA2.\n\n"
        "### Q3\n\nA3.\n"
    )


def test_61_char_seo_title_shortened_to_valid_range():
    title = "A Laser Pointer for Above Ceilings in Commercial Construction"
    assert len(title) == 61

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert 50 <= len(fitted) <= 60
    assert "laser" in fitted.lower()
    assert "pointer" in fitted.lower()


def test_62_char_seo_title_shortened_to_valid_range():
    title = "A Laser for Pointing Above Ceilings in Commercial Construction"
    assert len(title) == 62

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert 50 <= len(fitted) <= 60
    assert "laser" in fitted.lower()


def test_actual_failing_title_first_candidate_normalizes():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert 50 <= len(fitted) <= 60
    assert "Laser" in fitted or "laser" in fitted
    assert "Pointer" in fitted or "pointer" in fitted


def test_actual_failing_title_second_candidate_normalizes():
    title = "Laser for Pointing Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert 50 <= len(fitted) <= 60
    assert "Laser" in fitted or "laser" in fitted


def test_valid_55_char_seo_title_unchanged():
    title = "Laser Pointer Above Ceilings in Commercial Construction"
    assert len(title) == 55

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert fitted == title


def test_idempotent_double_call():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"
    once = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)
    twice = lw._fit_w1b_seo_title(once, _PRIMARY_KEYWORD)

    assert once == twice
    assert 50 <= len(once) <= 60
    assert 50 <= len(twice) <= 60


def test_does_not_cut_words_in_half():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    words_in_original = {w.lower() for w in title.split()}
    words_in_fitted = {w.lower() for w in fitted.split()}
    assert words_in_fitted <= words_in_original


def test_preserves_primary_keyword_content_words():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert "Laser" in fitted or "laser" in fitted
    assert "Pointer" in fitted or "pointer" in fitted


def test_leaves_h1_and_plain_title_unchanged():
    draft = _draft_with_seo_title(
        "Laser for Pointing Above Ceilings in Commercial Construction"
    )
    normalized = lw._normalize_w1b_frontmatter(draft, _PRIMARY_KEYWORD)

    assert "# Laser Pointer Planning Guide" in normalized
    assert "Title: Laser Pointer Planning Guide" in normalized
    assert "SEO Title:" in normalized

    seo_title_line = next(
        line for line in normalized.splitlines()
        if line.startswith("SEO Title:")
    )
    fitted = seo_title_line.split(":", 1)[1].strip()
    assert 50 <= len(fitted) <= 60


def test_collapses_whitespace_and_strips_quotes():
    title = '  "Laser   Pointer    for   Above   Ceilings in Commercial Construction"  '

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert 50 <= len(fitted) <= 60
    assert "  " not in fitted
    assert not fitted.startswith('"')
    assert not fitted.endswith('"')


def test_does_not_add_ellipsis_or_claims():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert "..." not in fitted
    assert "2026" not in fitted
    assert "nm" not in fitted.lower()


def test_no_trailing_punctuation_after_shortening():
    title = "Laser Pointer for Above Ceilings in Commercial Construction"

    fitted = lw._fit_w1b_seo_title(title, _PRIMARY_KEYWORD)

    assert not any(fitted.endswith(ch) for ch in ":|-,")


def test_normalize_w1b_candidate_passes_real_seo_title_check(tmp_path):
    draft = _draft_with_seo_title(
        "Laser for Pointing Above Ceilings in Commercial Construction"
    )
    normalized = lw._normalize_w1b_candidate(draft, _PRIMARY_KEYWORD)

    candidate_path = tmp_path / "candidate.md"
    candidate_path.write_text(normalized, encoding="utf-8")

    result = write_pre_check.run(
        str(candidate_path),
        tier="Cluster Content",
        keywords=_PRIMARY_KEYWORD,
    )

    seo_title_check = next(
        (check for check in result.get("checks", [])
         if check.get("item") == "SEO Title 50-60字符"),
        None,
    )
    assert seo_title_check is not None
    assert seo_title_check["pass"] is True, seo_title_check.get("detail")



def test_missing_seo_title_is_derived_without_mutating_plain_title():
    plain_title = "Laser Pointer for Pointing Above Ceilings in Commercial Construction"
    assert len(plain_title) == 68
    draft = (
        "---\n"
        f"Title: {plain_title}\n"
        "SEO Description: " + "x" * 155 + "\n"
        f"SEO Keywords: {_PRIMARY_KEYWORD}\n"
        "---\n\n"
        "# Laser Pointer Planning Guide\n"
    )

    normalized = lw._normalize_w1b_frontmatter(draft, _PRIMARY_KEYWORD)

    assert f"Title: {plain_title}" in normalized
    assert "# Laser Pointer Planning Guide" in normalized
    seo_title_line = next(
        line for line in normalized.splitlines()
        if line.startswith("SEO Title:")
    )
    seo_title = seo_title_line.split(":", 1)[1].strip()
    assert 50 <= len(seo_title) <= 60
    assert "laser" in seo_title.lower()
    assert "pointer" in seo_title.lower()


def test_w1b_contract_requires_separate_title_and_1350_words(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text(
        _draft_with_seo_title("Laser Pointer Above Ceilings in Commercial Construction"),
        encoding="utf-8",
    )

    contract = lw._w1b_repair_contract(draft, "Laser Pointer Planning Guide")

    assert "separate " + chr(96) + "SEO Title:" + chr(96) in contract
    assert "ordinary " + chr(96) + "Title:" + chr(96) in contract
    assert "at least 1350 checker-visible body words" in contract
