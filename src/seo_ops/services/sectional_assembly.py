"""Deterministic Phase 5 assembly and global delivery gates.

This module does not call AI and does not write the formal Legacy draft.  It
turns a validated Phase 4 delivery plus its merged claim ledger into a complete
shadow deliverable with canonical frontmatter, FAQPage JSON-LD, global link and
duplication audits, and transactionally persisted draft/report/ledger files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from data_sources.modules import seo_common
from data_sources.modules.seo_config import (
    LINK_RATIO_BLOG,
    LINK_RATIO_EXTERNAL,
    LINK_RATIO_PRODUCT,
)
from seo_ops.services.sectional_consistency import (
    contains_unsafe_metadata_compliance_claim,
    wavelength_color_conflicts,
)
from seo_ops.services.sectional_delivery import (
    sectional_link_hard_caps,
    validate_resolved_delivery,
)
from seo_ops.services.sectional_writing import (
    CONTRACT_VERSION,
    DEFAULT_CONTENT_LANGUAGE,
    ContractValidationError,
)

ASSEMBLY_VERSION = 1

_SHA256 = re.compile(r"[a-f0-9]{64}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_GENERIC_ANCHORS = frozenset(
    {
        "click",
        "click here",
        "here",
        "learn more",
        "more",
        "product",
        "read more",
        "this",
        "this article",
        "this product",
    }
)
_METADATA_KEYS = frozenset(
    {
        "title",
        "slug",
        "author",
        "summary",
        "tags",
        "page_type",
        "seo_title",
        "seo_description",
        "seo_keywords",
        "target_words",
    }
)
_ASSEMBLY_KEYS = frozenset(
    {
        "version",
        "content_language",
        "topic",
        "metadata",
        "delivery",
        "frontmatter",
        "faq_schema",
        "faq_schema_block",
        "draft_markdown",
        "draft_sha256",
        "claim_ledger",
        "audit",
        "assembly_sha256",
    }
)
_LEDGER_KEYS = frozenset({"version", "draft_sha256", "claims"})


class SectionAssemblyError(ContractValidationError):
    """Raised when final assembly or a global delivery gate fails."""


def _clean_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SectionAssemblyError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not allow_empty and not cleaned:
        raise SectionAssemblyError(f"{field} must not be empty")
    if "\n" in value or "\r" in value:
        raise SectionAssemblyError(f"{field} must be one line")
    return cleaned


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _visible_text(markdown: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown or "")
    text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+\.\s+)", "", text)
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.split())


def _word_count(markdown: str) -> int:
    return len(re.findall(r"[a-z0-9]+", _visible_text(markdown).casefold()))


def _validate_string_list(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise SectionAssemblyError(f"{field} must contain {minimum}-{maximum} strings")
    cleaned = [_clean_text(item, field) for item in value]
    if len(cleaned) != len({item.casefold() for item in cleaned}):
        raise SectionAssemblyError(f"{field} must not contain duplicates")
    return cleaned


def validate_assembly_metadata(
    metadata: Any,
    *,
    expected_topic: str | None = None,
) -> dict[str, Any]:
    if not isinstance(metadata, dict) or set(metadata) != _METADATA_KEYS:
        raise SectionAssemblyError("assembly metadata has an invalid shape")
    title = _clean_text(metadata.get("title"), "metadata.title")
    if expected_topic is not None and title != expected_topic:
        raise SectionAssemblyError("metadata title must equal the delivery topic")
    slug = _clean_text(metadata.get("slug"), "metadata.slug")
    if not _SLUG.fullmatch(slug):
        raise SectionAssemblyError("metadata.slug must be lowercase kebab-case")
    author = _clean_text(metadata.get("author"), "metadata.author")
    summary = _clean_text(metadata.get("summary"), "metadata.summary")
    if not 80 <= len(summary) <= 300:
        raise SectionAssemblyError("metadata.summary must be 80-300 characters")
    tags = _validate_string_list(metadata.get("tags"), "metadata.tags", minimum=3, maximum=5)
    page_type = _clean_text(metadata.get("page_type"), "metadata.page_type")
    seo_title = _clean_text(metadata.get("seo_title"), "metadata.seo_title")
    if not 50 <= len(seo_title) <= 60:
        raise SectionAssemblyError("metadata.seo_title must be 50-60 characters")
    seo_description = _clean_text(
        metadata.get("seo_description"),
        "metadata.seo_description",
    )
    if not 150 <= len(seo_description) <= 160:
        raise SectionAssemblyError("metadata.seo_description must be 150-160 characters")
    for field, value in (
        ("summary", summary),
        ("seo_title", seo_title),
        ("seo_description", seo_description),
    ):
        if contains_unsafe_metadata_compliance_claim(value):
            raise SectionAssemblyError(
                f"metadata.{field} contains unsupported authority or compliance language"
            )
    seo_keywords = _validate_string_list(
        metadata.get("seo_keywords"),
        "metadata.seo_keywords",
        minimum=1,
        maximum=8,
    )
    target_words = metadata.get("target_words")
    if not isinstance(target_words, dict) or set(target_words) != {"min", "max"}:
        raise SectionAssemblyError("metadata.target_words must contain min and max")
    minimum = target_words.get("min")
    maximum = target_words.get("max")
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum < 100
        or maximum < minimum
        or maximum > 10000
    ):
        raise SectionAssemblyError("metadata.target_words range is invalid")
    english_fields = [
        title,
        author,
        summary,
        *tags,
        page_type,
        seo_title,
        seo_description,
        *seo_keywords,
    ]
    if any(_CJK.search(item) for item in english_fields):
        raise SectionAssemblyError("assembly metadata must be English")
    return {
        "title": title,
        "slug": slug,
        "author": author,
        "summary": summary,
        "tags": tags,
        "page_type": page_type,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "seo_keywords": seo_keywords,
        "target_words": {"min": minimum, "max": maximum},
    }


def render_frontmatter(metadata: dict[str, Any]) -> str:
    validated = validate_assembly_metadata(metadata)
    lines = [
        "---",
        f"Title: {validated['title']}",
        f"Slug: {validated['slug']}",
        f"Author: {validated['author']}",
        f"Summary: {validated['summary']}",
        f"Tags: {', '.join(validated['tags'])}",
        f"Page Type: {validated['page_type']}",
        f"SEO Title: {validated['seo_title']}",
        f"SEO Description: {validated['seo_description']}",
        f"SEO Keywords: {', '.join(validated['seo_keywords'])}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _faq_pairs(delivery: dict[str, Any]) -> list[dict[str, str]]:
    faq = next(
        (item for item in delivery["sections"] if item["section_id"] == "frame-faq"),
        None,
    )
    if faq is None:
        raise SectionAssemblyError("resolved delivery is missing the FAQ frame")
    markdown = faq["markdown"]
    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", markdown))
    pairs: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        question = _visible_text(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        answer = _visible_text(markdown[match.end() : end])
        if not question.endswith("?") or not answer:
            raise SectionAssemblyError("visible FAQ question or answer is invalid")
        pairs.append({"question": question, "answer": answer})
    if not 3 <= len(pairs) <= 4:
        raise SectionAssemblyError("visible FAQ must contain 3-4 questions")
    return pairs


def build_faq_schema(delivery: dict[str, Any]) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item["answer"],
                },
            }
            for item in _faq_pairs(resolved)
        ],
    }


def render_faq_schema(delivery: dict[str, Any]) -> str:
    schema = build_faq_schema(delivery)
    return (
        '<script type="application/ld+json">\n'
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n</script>\n"
    )


def _issue(code: str, detail: str, *, section_id: str = "") -> dict[str, str]:
    result = {"code": code, "detail": detail}
    if section_id:
        result["section_id"] = section_id
    return result


def _validate_legacy_claim_ledger(
    claim_ledger: dict[str, Any],
    draft: str,
) -> None:
    # Imported lazily so Legacy can later import this Phase 5 module without a
    # module-initialization cycle during Phase 7 integration.
    from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

    _validate_claim_ledger_json(
        json.dumps(claim_ledger, ensure_ascii=False),
        draft,
    )


def _duplicate_content_issues(delivery: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen_sentences: dict[str, str] = {}
    for record in delivery["sentences"]:
        text = record["text"].strip()
        norm = record["norm"].casefold()
        if text.startswith("#") or len(norm) < 60:
            continue
        previous = seen_sentences.get(norm)
        if previous and previous != record["section_id"]:
            issues.append(
                _issue(
                    "duplicate_sentence_across_sections",
                    text[:160],
                    section_id=record["section_id"],
                )
            )
        else:
            seen_sentences[norm] = record["section_id"]

    seen_paragraphs: dict[str, str] = {}
    for section in delivery["sections"]:
        for paragraph in re.split(r"\n\n+", section["markdown"]):
            visible = _visible_text(paragraph).casefold()
            if len(visible.split()) < 12:
                continue
            previous = seen_paragraphs.get(visible)
            if previous and previous != section["section_id"]:
                issues.append(
                    _issue(
                        "duplicate_paragraph_across_sections",
                        visible[:160],
                        section_id=section["section_id"],
                    )
                )
            else:
                seen_paragraphs[visible] = section["section_id"]
    return issues


def _product_provenance_audit(
    delivery: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Bind every product URL to exactly one canonical sentence and catalog digest."""

    records: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for binding in delivery["bindings"]:
        if binding.get("kind") != "product":
            continue
        section_id = str(binding.get("section_id") or "")
        url = str(binding.get("url") or "")
        matches = [
            item
            for item in delivery["sentences"]
            if item.get("section_id") == section_id and url in str(item.get("text") or "")
        ]
        if len(matches) != 1:
            issues.append(
                _issue(
                    "product_binding_missing_sentence_provenance",
                    f"{binding.get('candidate_id')}: sentence matches={len(matches)}",
                    section_id=section_id,
                )
            )
            continue
        provenance = binding.get("catalog_provenance")
        if not isinstance(provenance, dict) or not provenance.get("catalog_facts"):
            issues.append(
                _issue(
                    "product_binding_missing_catalog_provenance",
                    str(binding.get("candidate_id") or ""),
                    section_id=section_id,
                )
            )
            continue
        records.append(
            {
                "candidate_id": binding["candidate_id"],
                "product_id": binding["product_id"],
                "section_id": section_id,
                "sentence_id": matches[0]["sentence_id"],
                "catalog_sha256": provenance["catalog_sha256"],
                "catalog_fact_keys": sorted(provenance["catalog_facts"]),
            }
        )
    return records, issues


def audit_sectional_delivery(
    delivery: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    meta = validate_assembly_metadata(metadata, expected_topic=resolved["topic"])
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    body = resolved["draft_markdown"]
    technical_conflicts = wavelength_color_conflicts(_visible_text(body))
    blockers.extend(
        _issue(
            "wavelength_color_mismatch",
            f"{item['wavelength_nm']}nm stated as {item['stated_color']} "
            f"instead of {item['expected_color']}",
        )
        for item in technical_conflicts
    )
    words = _word_count(body)
    target = meta["target_words"]
    if words < target["min"]:
        blockers.append(
            _issue(
                "article_below_target_word_minimum",
                f"{words} < {target['min']}",
            )
        )
    elif words > target["max"]:
        warnings.append(
            _issue(
                "article_above_target_word_maximum",
                f"{words} > {target['max']}",
            )
        )

    h1s = re.findall(r"(?m)^#\s+(.+?)\s*$", body)
    if h1s != [meta["title"]]:
        blockers.append(_issue("invalid_h1", "H1 must occur once and equal metadata.title"))
    h2s = re.findall(r"(?m)^##\s+(.+?)\s*$", body)
    body_headings = [
        item["heading"] for item in resolved["sections"] if item["unit_kind"] == "body_section"
    ]
    expected_h2s = [*body_headings, "Conclusion", "Frequently Asked Questions"]
    if h2s != expected_h2s:
        blockers.append(_issue("invalid_h2_order", "visible H2 order differs from delivery"))
    if not 4 <= len(body_headings) <= 7:
        warnings.append(
            _issue(
                "main_h2_count_outside_recommended_range",
                f"{len(body_headings)} main H2 sections; recommended 4-7",
            )
        )
    takeaway_count = len(re.findall(r"(?m)^>\s+-\s+", body))
    if not 3 <= takeaway_count <= 5:
        blockers.append(_issue("invalid_key_takeaway_count", str(takeaway_count)))
    faq_count = len(_faq_pairs(resolved))

    blockers.extend(_duplicate_content_issues(resolved))
    product_provenance, provenance_issues = _product_provenance_audit(resolved)
    blockers.extend(provenance_issues)

    counts = {"article": 0, "product": 0, "external_citation": 0}
    urls_by_kind: dict[str, set[str]] = {key: set() for key in counts}
    candidates_by_kind: dict[str, set[str]] = {key: set() for key in counts}
    anchors: dict[str, str] = {}
    for binding in resolved["bindings"]:
        kind = binding["kind"]
        section_id = binding["section_id"]
        counts[kind] += 1
        if section_id.startswith("frame-"):
            blockers.append(
                _issue(
                    "link_in_noncommercial_frame_unit",
                    kind,
                    section_id=section_id,
                )
            )
        url = binding["url"]
        candidate_id = binding["candidate_id"]
        if kind in {"article", "product"}:
            if url in urls_by_kind[kind] or candidate_id in candidates_by_kind[kind]:
                blockers.append(
                    _issue(
                        "duplicate_internal_link_target",
                        url,
                        section_id=section_id,
                    )
                )
        elif url in urls_by_kind[kind]:
            warnings.append(
                _issue(
                    "repeated_external_citation",
                    url,
                    section_id=section_id,
                )
            )
        urls_by_kind[kind].add(url)
        candidates_by_kind[kind].add(candidate_id)
        anchor = _clean_text(binding.get("anchor", ""), "binding.anchor")
        anchor_norm = anchor.casefold().strip(" .,:;!?\"'")
        if kind in {"article", "product"} and (
            anchor_norm in _GENERIC_ANCHORS or len(anchor_norm.split()) < 2
        ):
            blockers.append(
                _issue(
                    "generic_or_too_short_anchor",
                    anchor,
                    section_id=section_id,
                )
            )
        previous_url = anchors.get(anchor_norm)
        if previous_url and previous_url != url:
            warnings.append(
                _issue(
                    "reused_anchor_for_different_targets",
                    anchor,
                    section_id=section_id,
                )
            )
        else:
            anchors[anchor_norm] = url

    markdown_urls = Counter(
        match.group(2)
        for match in re.finditer(
            r"\[([^\]]+)\]\((https?://[^)]+)\)",
            body,
        )
    )
    binding_urls = Counter(binding["url"] for binding in resolved["bindings"])
    if markdown_urls != binding_urls:
        blockers.append(
            _issue(
                "markdown_links_do_not_match_phase4_bindings",
                f"markdown={dict(markdown_urls)} bindings={dict(binding_urls)}",
            )
        )

    hard_caps = sectional_link_hard_caps(words)
    for kind, count in counts.items():
        if count > hard_caps[kind]:
            blockers.append(
                _issue(
                    "link_density_exceeds_hard_cap",
                    f"{kind}: {count} > {hard_caps[kind]}",
                )
            )

    advisory_targets = {
        "article": round(words / LINK_RATIO_BLOG),
        "product": round(words / LINK_RATIO_PRODUCT),
        "external_citation": round(words / LINK_RATIO_EXTERNAL),
    }
    for kind, target_count in advisory_targets.items():
        if target_count > counts[kind]:
            warnings.append(
                _issue(
                    "advisory_link_density_below_ratio",
                    f"{kind}: {counts[kind]} < advisory {target_count}",
                )
            )

    product_positions = [
        body.find(binding["url"]) / max(1, len(body))
        for binding in resolved["bindings"]
        if binding["kind"] == "product" and binding["url"] in body
    ]
    if product_positions and min(product_positions) < 0.20:
        warnings.append(
            _issue(
                "product_link_appears_early",
                f"first product link at {min(product_positions):.1%}",
            )
        )

    primary = meta["seo_keywords"][0].casefold()
    visible = _visible_text(body).casefold()
    first_100 = " ".join(re.findall(r"[a-z0-9]+", visible)[:100])
    if primary not in meta["title"].casefold():
        warnings.append(_issue("primary_keyword_missing_from_title", primary))
    if primary not in first_100:
        warnings.append(_issue("primary_keyword_missing_from_first_100_words", primary))
    h2_hits = sum(primary in heading.casefold() for heading in body_headings)
    if h2_hits < 2:
        warnings.append(
            _issue(
                "primary_keyword_in_fewer_than_two_main_h2s",
                f"{h2_hits} hits",
            )
        )

    return {
        "version": ASSEMBLY_VERSION,
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "metrics": {
            "word_count": words,
            "target_words": target,
            "main_h2_count": len(body_headings),
            "takeaway_count": takeaway_count,
            "faq_count": faq_count,
            "link_counts": counts,
            "link_hard_caps": hard_caps,
            "link_advisory_targets": advisory_targets,
            "product_provenance": product_provenance,
        },
    }


def _validate_input_claim_ledger(
    claim_ledger: Any,
    delivery: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(claim_ledger, dict) or set(claim_ledger) != _LEDGER_KEYS:
        raise SectionAssemblyError("claim ledger must be an object")
    if claim_ledger.get("version") != CONTRACT_VERSION:
        raise SectionAssemblyError("claim ledger version must be 1")
    if claim_ledger.get("draft_sha256") != delivery["draft_sha256"]:
        raise SectionAssemblyError("claim ledger does not match the Phase 4 draft")
    try:
        _validate_legacy_claim_ledger(claim_ledger, delivery["draft_markdown"])
    except ValueError as exc:
        raise SectionAssemblyError(f"claim ledger is invalid: {exc}") from None
    return claim_ledger


def assemble_sectional_article(
    delivery: dict[str, Any],
    metadata: dict[str, Any],
    claim_ledger: dict[str, Any],
) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    meta = validate_assembly_metadata(metadata, expected_topic=resolved["topic"])
    ledger = _validate_input_claim_ledger(claim_ledger, resolved)
    audit = audit_sectional_delivery(resolved, meta)
    if audit["blockers"]:
        codes = ", ".join(item["code"] for item in audit["blockers"][:8])
        raise SectionAssemblyError(f"global assembly gates failed: {codes}")
    frontmatter = render_frontmatter(meta)
    schema = build_faq_schema(resolved)
    schema_block = render_faq_schema(resolved)
    draft = frontmatter + resolved["draft_markdown"].rstrip() + "\n\n" + schema_block
    final_sentences = seo_common.extract_draft_sentences(draft)
    expected_sentences = [
        {
            "sentence_id": item["sentence_id"],
            "text": item["text"],
            "norm": item["norm"],
        }
        for item in resolved["sentences"]
    ]
    if final_sentences != expected_sentences:
        raise SectionAssemblyError("frontmatter or FAQ schema changed canonical body sentence IDs")
    final_ledger = dict(ledger)
    final_ledger["draft_sha256"] = _sha256_text(draft)
    try:
        _validate_legacy_claim_ledger(final_ledger, draft)
    except ValueError as exc:
        raise SectionAssemblyError(
            f"final claim ledger is incompatible with assembled draft: {exc}"
        ) from None
    assembly = {
        "version": ASSEMBLY_VERSION,
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "topic": resolved["topic"],
        "metadata": meta,
        "delivery": resolved,
        "frontmatter": frontmatter,
        "faq_schema": schema,
        "faq_schema_block": schema_block,
        "draft_markdown": draft,
        "draft_sha256": _sha256_text(draft),
        "claim_ledger": final_ledger,
        "audit": audit,
    }
    assembly["assembly_sha256"] = _json_digest(assembly)
    return validate_sectional_assembly(assembly)


def validate_sectional_assembly(assembly: Any) -> dict[str, Any]:
    if (
        not isinstance(assembly, dict)
        or set(assembly) != _ASSEMBLY_KEYS
        or assembly.get("version") != ASSEMBLY_VERSION
    ):
        raise SectionAssemblyError("sectional assembly must be a version 1 object")
    if assembly.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionAssemblyError("sectional assembly content_language must be en")
    delivery = validate_resolved_delivery(assembly.get("delivery"))
    metadata = validate_assembly_metadata(
        assembly.get("metadata"),
        expected_topic=delivery["topic"],
    )
    if assembly.get("topic") != delivery["topic"]:
        raise SectionAssemblyError("assembly topic does not match delivery")
    frontmatter = render_frontmatter(metadata)
    if assembly.get("frontmatter") != frontmatter:
        raise SectionAssemblyError("assembly frontmatter is not deterministic")
    schema = build_faq_schema(delivery)
    if assembly.get("faq_schema") != schema:
        raise SectionAssemblyError("assembly FAQ schema does not match visible FAQ")
    schema_block = render_faq_schema(delivery)
    if assembly.get("faq_schema_block") != schema_block:
        raise SectionAssemblyError("assembly FAQ schema block is not deterministic")
    expected_draft = frontmatter + delivery["draft_markdown"].rstrip() + "\n\n" + schema_block
    draft = assembly.get("draft_markdown")
    if draft != expected_draft:
        raise SectionAssemblyError("assembly draft is not deterministic")
    if assembly.get("draft_sha256") != _sha256_text(draft):
        raise SectionAssemblyError("assembly draft SHA mismatch")
    expected_sentences = [
        {
            "sentence_id": item["sentence_id"],
            "text": item["text"],
            "norm": item["norm"],
        }
        for item in delivery["sentences"]
    ]
    if seo_common.extract_draft_sentences(draft) != expected_sentences:
        raise SectionAssemblyError("assembly sentence IDs differ from delivery")
    audit = audit_sectional_delivery(delivery, metadata)
    if audit.get("blockers") or assembly.get("audit") != audit:
        raise SectionAssemblyError("assembly audit is invalid or no longer passes")
    ledger = assembly.get("claim_ledger")
    if (
        not isinstance(ledger, dict)
        or set(ledger) != _LEDGER_KEYS
        or ledger.get("draft_sha256") != assembly["draft_sha256"]
    ):
        raise SectionAssemblyError("assembly claim ledger draft SHA mismatch")
    try:
        _validate_legacy_claim_ledger(ledger, draft)
    except ValueError as exc:
        raise SectionAssemblyError(f"assembly claim ledger is invalid: {exc}") from None
    unsigned = dict(assembly)
    digest = unsigned.pop("assembly_sha256", None)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise SectionAssemblyError("assembly SHA is invalid")
    if digest != _json_digest(unsigned):
        raise SectionAssemblyError("assembly SHA mismatch")
    return assembly


def assembly_paths(workspace: Path, slug: str) -> dict[str, Path]:
    clean_slug = _clean_text(slug, "slug")
    if not _SLUG.fullmatch(clean_slug):
        raise SectionAssemblyError("slug must be lowercase kebab-case")
    root = Path(workspace) / "drafts" / "sectional" / clean_slug
    return {
        "draft": root / "assembled-draft.md",
        "ledger": root / "assembled-claim-ledger.json",
        "report": root / "assembly-report.json",
    }


def _temp_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)
    finally:
        handle.close()


def _restore_path(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    temp = _temp_file(path, previous)
    os.replace(temp, path)


def persist_sectional_assembly(
    workspace: Path,
    assembly: dict[str, Any],
) -> dict[str, str]:
    validated = validate_sectional_assembly(assembly)
    paths = assembly_paths(workspace, validated["metadata"]["slug"])
    payloads = {
        "draft": validated["draft_markdown"].encode("utf-8"),
        "ledger": (
            json.dumps(
                validated["claim_ledger"],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
        "report": (
            json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }
    previous = {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}
    temps = {key: _temp_file(paths[key], payload) for key, payload in payloads.items()}
    replaced: list[str] = []
    try:
        for key in ("draft", "ledger", "report"):
            os.replace(temps[key], paths[key])
            replaced.append(key)
    except Exception:
        for key in reversed(replaced):
            _restore_path(paths[key], previous[key])
        raise
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)
    return {key: str(path) for key, path in paths.items()}


def load_sectional_assembly(
    workspace: Path,
    slug: str,
    *,
    expected_assembly_sha256: str | None = None,
) -> dict[str, Any] | None:
    paths = assembly_paths(workspace, slug)
    if not all(path.exists() for path in paths.values()):
        return None
    try:
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        validated = validate_sectional_assembly(report)
        if paths["draft"].read_text(encoding="utf-8") != validated["draft_markdown"]:
            return None
        if json.loads(paths["ledger"].read_text(encoding="utf-8")) != validated["claim_ledger"]:
            return None
    except (OSError, json.JSONDecodeError, SectionAssemblyError, ValueError):
        return None
    if (
        expected_assembly_sha256 is not None
        and validated["assembly_sha256"] != expected_assembly_sha256
    ):
        return None
    return validated
