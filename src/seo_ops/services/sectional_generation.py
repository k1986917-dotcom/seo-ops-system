"""Shadow-only section generation, validation and resumable checkpoints.

This module deliberately does not import or replace the Legacy W0 workflow.
Callers inject a text generator, making the engine testable without an API.
Only compact section-scoped context is sent to the generator; approved link and
evidence IDs are represented by placeholders and validated server-side.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from seo_ops.services.sectional_context import validate_candidate_registry
from seo_ops.services.sectional_writing import (
    CONTRACT_VERSION,
    DEFAULT_CONTENT_LANGUAGE,
    ContractValidationError,
    validate_section_contracts,
    validate_section_link_contracts,
)

SECTION_MARKDOWN_MARKER = "===SECTION_MARKDOWN==="
SECTION_DECISIONS_MARKER = "===SECTION_DECISIONS==="
FRAME_INTRO_MARKER = "===INTRODUCTION==="
FRAME_TAKEAWAYS_MARKER = "===KEY_TAKEAWAYS==="
FRAME_CONCLUSION_MARKER = "===CONCLUSION==="
FRAME_FAQ_MARKER = "===FAQ_JSON==="

_LINK_TYPES = ("article_links", "product_links", "external_citations")
_PRODUCT_FIT_LEVELS = {
    "strong",
    "approved_constraint",
    "contextual",
    "related_catalog",
}
_ARTICLE_FRAME_REQUIREMENTS = {
    "introduction_words": {"min": 80, "max": 180},
    "takeaway_count": {"min": 3, "max": 5},
    "conclusion_words": {"min": 80, "max": 180},
    "faq_count": {"min": 3, "max": 4},
    "faq_answer_words": {"min": 20, "max": 90},
}
_ARTICLE_PLACEHOLDER = re.compile(
    r"\[\[ARTICLE:([a-zA-Z0-9._-]+)\|([^\]\n]+)\]\]"
)
_PRODUCT_PLACEHOLDER = re.compile(
    r"\[\[PRODUCT:([a-zA-Z0-9._-]+)\|([^\]\n]+)\]\]"
)
_CITE_PLACEHOLDER = re.compile(r"\[\[CITE:([a-zA-Z0-9._-]+)\]\]")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_RAW_URL = re.compile(r"https?://", re.IGNORECASE)
_RAW_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\((?:https?://|/)[^)]+\)")
_HTML_LINK = re.compile(r"<a\s+[^>]*href\s*=", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


class SectionGenerationError(ContractValidationError):
    """Raised when a section package, response or checkpoint is invalid."""


def _clean_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SectionGenerationError(f"{field} must be a string")
    result = " ".join(value.split())
    if not allow_empty and not result:
        raise SectionGenerationError(f"{field} must not be empty")
    return result


def _json_digest(data: Any) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _truncate(value: str, limit: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


def _compact_product_attributes(candidate: dict[str, Any]) -> dict[str, str]:
    merged = candidate.get("attributes", {}).get("merged", {})
    if not isinstance(merged, dict):
        return {}
    compact: dict[str, str] = {}
    for key in sorted(merged):
        value = merged[key]
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            continue
        compact[key] = _truncate(value, 180)
        if len(compact) >= 8:
            break
    return compact


def _registry_indexes(registry: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    validated = validate_candidate_registry(registry)
    return {
        kind: {
            candidate["candidate_id"]: candidate
            for candidate in validated[kind]["candidates"]
        }
        for kind in ("articles", "products", "evidence")
    }


def _manifest_section_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = manifest.get("sections")
    if not isinstance(sections, list):
        raise SectionGenerationError("context_manifest.sections must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in sections:
        if not isinstance(item, dict):
            raise SectionGenerationError("context manifest section must be an object")
        section_id = _clean_text(item.get("section_id"), "manifest.section_id")
        if section_id in result:
            raise SectionGenerationError("context manifest section IDs must be unique")
        result[section_id] = item
    return result


def _selected_candidates(
    gate: dict[str, Any],
    index: dict[str, dict[str, Any]],
    *,
    kind: str,
    metadata_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected = gate.get("selected_ids")
    if not isinstance(selected, list):
        raise SectionGenerationError(f"{kind} selected_ids must be a list")
    result = []
    for candidate_id in selected:
        candidate = index.get(candidate_id)
        if candidate is None:
            raise SectionGenerationError(
                f"approved {kind} candidate is missing from registry: {candidate_id}"
            )
        if kind == "article":
            result.append({
                "candidate_id": candidate_id,
                "title": candidate.get("title", ""),
                "primary_keyword": candidate.get("primary_keyword", ""),
            })
        elif kind == "product":
            if candidate.get("attribute_conflicts"):
                raise SectionGenerationError(
                    f"conflicted product cannot enter generation context: {candidate_id}"
                )
            metadata = (metadata_by_id or {}).get(candidate_id, {})
            fit_level = metadata.get("fit_level", "contextual")
            if fit_level not in _PRODUCT_FIT_LEVELS:
                raise SectionGenerationError(
                    f"product candidate fit_level is invalid: {candidate_id}"
                )
            result.append({
                "candidate_id": candidate_id,
                "product_id": candidate.get("product_id", ""),
                "title": candidate.get("title", ""),
                "attributes": _compact_product_attributes(candidate),
                "fit_level": fit_level,
                "fit_reason": metadata.get("fit_reason", "approved_candidate"),
            })
        else:
            result.append({
                "candidate_id": candidate_id,
                "evidence_id": candidate.get("evidence_id", candidate_id),
                "support": _truncate(candidate.get("support", ""), 700),
                "concepts": candidate.get("concepts", [])[:8],
                "claim_types": candidate.get("claim_types", [])[:6],
                "source_url": candidate.get("url", ""),
            })
    return result


def build_section_generation_package(
    section_contracts: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    section_id: str,
    *,
    previous_summary: str = "",
) -> dict[str, Any]:
    """Build one compact, deterministic section-scoped generation package."""
    sections = validate_section_contracts(section_contracts)
    links = validate_section_link_contracts(link_contracts, sections)
    clean_section_id = _clean_text(section_id, "section_id")
    if context_manifest.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("context_manifest version must be 1")
    if context_manifest.get("topic") != sections["topic"]:
        raise SectionGenerationError("context manifest topic does not match contracts")
    if context_manifest.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("context manifest content_language must be en")
    registry = context_manifest.get("registry")
    if not isinstance(registry, dict):
        raise SectionGenerationError("context_manifest.registry is missing")
    indexes = _registry_indexes(registry)
    manifest_by_id = _manifest_section_map(context_manifest)
    if clean_section_id not in manifest_by_id:
        raise SectionGenerationError("section is missing from context manifest")
    manifest_section = manifest_by_id[clean_section_id]

    section_by_id = {item["section_id"]: item for item in sections["sections"]}
    link_by_id = {item["section_id"]: item for item in links["sections"]}
    if clean_section_id not in section_by_id or clean_section_id not in link_by_id:
        raise SectionGenerationError("section is missing from contracts")
    section = section_by_id[clean_section_id]
    link = link_by_id[clean_section_id]
    brief_conflicts = manifest_section.get("brief_catalog_conflicts", [])
    if not isinstance(brief_conflicts, list) or any(
        not isinstance(item, dict) for item in brief_conflicts
    ):
        raise SectionGenerationError("manifest brief_catalog_conflicts are invalid")
    conflicting_points = {
        item.get("brief_point")
        for item in brief_conflicts
        if isinstance(item.get("brief_point"), str)
    }
    approved_brief_points = [
        point for point in section["brief_points"] if point not in conflicting_points
    ]
    rejected_brief_points = [
        {
            "text": point,
            "reason_code": "brief_catalog_alignment_pending",
        }
        for point in section["brief_points"]
        if point in conflicting_points
    ]
    previous = _clean_text(
        previous_summary,
        "previous_summary",
        allow_empty=True,
    )
    previous = _truncate(previous, 600)

    package = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "section_id": clean_section_id,
        "position": section["position"],
        "heading": section["heading"],
        "reader_stage": section["reader_stage"],
        "reader_question": section["reader_question"],
        "section_goal": section["section_goal"],
        "must_answer": section["must_answer"],
        "brief_points": approved_brief_points,
        "brief_points_rejected": rejected_brief_points,
        "must_not_repeat": section["must_not_repeat"],
        "target_words": section["target_words"],
        "previous_heading": section["previous_section"],
        "previous_summary": previous,
        "next_heading": section["next_section"],
        "link_gates": {
            key: link[key]
            for key in _LINK_TYPES
        },
        "candidates": {
            "articles": _selected_candidates(
                link["article_links"],
                indexes["articles"],
                kind="article",
            ),
            "products": _selected_candidates(
                link["product_links"],
                indexes["products"],
                kind="product",
                metadata_by_id={
                    item["candidate_id"]: item
                    for item in manifest_section.get("product_candidates", [])
                    if isinstance(item, dict)
                    and isinstance(item.get("candidate_id"), str)
                },
            ),
            "evidence": _selected_candidates(
                link["external_citations"],
                indexes["evidence"],
                kind="evidence",
            ),
        },
    }
    package["context_char_counts"] = {
        key: len(json.dumps(value, ensure_ascii=False, sort_keys=True))
        for key, value in package["candidates"].items()
    }
    package["package_sha256"] = _json_digest(package)
    return validate_section_generation_package(package)


def validate_section_generation_package(package: Any) -> dict[str, Any]:
    if not isinstance(package, dict) or package.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("section generation package must be version 1")
    for field in (
        "topic",
        "section_id",
        "heading",
        "reader_stage",
        "reader_question",
        "section_goal",
        "package_sha256",
    ):
        _clean_text(package.get(field), f"package.{field}")
    if package.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("section generation package language must be en")
    if _CJK.search(package["heading"]):
        raise SectionGenerationError("section heading must be English")
    for field in ("must_answer", "brief_points", "must_not_repeat"):
        values = package.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise SectionGenerationError(f"package.{field} must be a list of strings")
        if any(_CJK.search(item) for item in values):
            raise SectionGenerationError(f"package.{field} must contain English text")
    rejected_brief_points = package.get("brief_points_rejected")
    if not isinstance(rejected_brief_points, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("text"), str)
        or item.get("reason_code") != "brief_catalog_alignment_pending"
        for item in rejected_brief_points
    ):
        raise SectionGenerationError("package.brief_points_rejected is invalid")
    target = package.get("target_words")
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("min"), int)
        or not isinstance(target.get("max"), int)
        or target["min"] < 1
        or target["max"] < target["min"]
    ):
        raise SectionGenerationError("package target_words is invalid")
    gates = package.get("link_gates")
    candidates = package.get("candidates")
    if not isinstance(gates, dict) or set(gates) != set(_LINK_TYPES):
        raise SectionGenerationError("package link_gates are invalid")
    if not isinstance(candidates, dict) or set(candidates) != {
        "articles",
        "products",
        "evidence",
    }:
        raise SectionGenerationError("package candidates are invalid")
    expected_sha = package["package_sha256"]
    unsigned = dict(package)
    unsigned.pop("package_sha256")
    if expected_sha != _json_digest(unsigned):
        raise SectionGenerationError("section generation package SHA mismatch")
    return package


def build_section_generation_prompt(package: dict[str, Any]) -> dict[str, str]:
    validated = validate_section_generation_package(package)
    system = """You write exactly one English H2 section for a larger article.
Use only the supplied section contract, evidence, article candidates, and product candidates.
Do not invent URLs, product IDs, article IDs, evidence IDs, specifications, statistics, laws, or claims.
Do not output raw URLs, Markdown links, HTML links, an H1, or a second H2.
Product candidates include a fit_level. A strong or approved_constraint candidate may be described only with the supplied attributes. A contextual candidate has limited section overlap. A related_catalog candidate is related to the article topic but is not an exact use-case match: it may be recommended as a related catalog option, but never claim it was designed for, proven for, compliant with, or specifically suitable for the exact section use case.
Use approved placeholders only:
[[ARTICLE:candidate_id|natural English anchor text]]
[[PRODUCT:candidate_id|natural English anchor text]]
[[CITE:evidence_id]]
The first content line must be the exact requested H2. Write 2-5 coherent paragraphs and answer directly before expanding.
Respect every link gate. A required gate must meet min_required. A none gate must not be used. If a recommended gate is unused, give a concise machine-readable reason_code in the decisions JSON.
Decision reason codes are deterministic: whenever used_ids is non-empty, set reason_code to used_approved_candidate. When a recommended gate is unused, use a concise rejection reason such as not_needed_for_this_section. When a none gate is unused, copy that gate's supplied reason_code.
Return exactly two blocks and no other text:
===SECTION_MARKDOWN===
<section markdown>
===SECTION_DECISIONS===
{"article_links":{"used_ids":[],"reason_code":"..."},"product_links":{"used_ids":[],"reason_code":"..."},"external_citations":{"used_ids":[],"reason_code":"..."}}"""
    user_payload = {
        key: validated[key]
        for key in (
            "topic",
            "content_language",
            "section_id",
            "heading",
            "reader_stage",
            "reader_question",
            "section_goal",
            "must_answer",
            "brief_points",
            "must_not_repeat",
            "target_words",
            "previous_heading",
            "previous_summary",
            "next_heading",
            "link_gates",
            "candidates",
        )
    }
    user = "SECTION PACKAGE\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {"system": system, "user": user}


def _split_response(text: str, first: str, second: str) -> tuple[str, str]:
    if not isinstance(text, str):
        raise SectionGenerationError("generator response must be a string")
    if text.count(first) != 1 or text.count(second) != 1:
        raise SectionGenerationError("generator response markers are missing or duplicated")
    before, remainder = text.split(first, 1)
    middle, after = remainder.split(second, 1)
    if before.strip():
        raise SectionGenerationError("unexpected text before the first response marker")
    if not middle.strip() or not after.strip():
        raise SectionGenerationError("generator response blocks must not be empty")
    return middle.strip(), after.strip()


def _placeholder_inventory(markdown: str) -> dict[str, list[str]]:
    articles = _ARTICLE_PLACEHOLDER.findall(markdown)
    products = _PRODUCT_PLACEHOLDER.findall(markdown)
    citations = _CITE_PLACEHOLDER.findall(markdown)
    stripped = _ARTICLE_PLACEHOLDER.sub("", markdown)
    stripped = _PRODUCT_PLACEHOLDER.sub("", stripped)
    stripped = _CITE_PLACEHOLDER.sub("", stripped)
    if "[[" in stripped or "]]" in stripped:
        raise SectionGenerationError("section contains an invalid placeholder")
    for _, anchor in articles + products:
        clean_anchor = _clean_text(anchor, "placeholder anchor")
        if _CJK.search(clean_anchor):
            raise SectionGenerationError("placeholder anchors must be English")
        if clean_anchor.casefold() in {"click here", "learn more", "read more"}:
            raise SectionGenerationError("placeholder anchor is too generic")
        if len(clean_anchor) > 100:
            raise SectionGenerationError("placeholder anchor is too long")
    return {
        "article_links": [candidate_id for candidate_id, _ in articles],
        "product_links": [candidate_id for candidate_id, _ in products],
        "external_citations": citations,
    }


def parse_section_placeholders(markdown: str) -> dict[str, list[dict[str, str]]]:
    """Return validated placeholder records without exposing registry URLs.

    Phase 4 resolves these records server-side.  Keeping parsing in the
    generation module prevents the writer and delivery layers from drifting to
    different placeholder grammars.
    """
    _placeholder_inventory(markdown)
    return {
        "article_links": [
            {"candidate_id": candidate_id, "anchor": anchor}
            for candidate_id, anchor in _ARTICLE_PLACEHOLDER.findall(markdown)
        ],
        "product_links": [
            {"candidate_id": candidate_id, "anchor": anchor}
            for candidate_id, anchor in _PRODUCT_PLACEHOLDER.findall(markdown)
        ],
        "external_citations": [
            {"candidate_id": candidate_id}
            for candidate_id in _CITE_PLACEHOLDER.findall(markdown)
        ],
    }


def _visible_markdown(markdown: str) -> str:
    value = _ARTICLE_PLACEHOLDER.sub(lambda match: match.group(2), markdown)
    value = _PRODUCT_PLACEHOLDER.sub(lambda match: match.group(2), value)
    value = _CITE_PLACEHOLDER.sub("", value)
    value = re.sub(r"^#{1,6}\s+.*$", "", value, flags=re.MULTILINE)
    return value


def _paragraph_count(markdown: str) -> int:
    body = "\n".join(markdown.splitlines()[1:]).strip()
    return len([block for block in re.split(r"\n\s*\n", body) if block.strip()])


def _validate_decisions(
    decisions: Any,
    inventory: dict[str, list[str]],
    package: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(decisions, dict) or set(decisions) != set(_LINK_TYPES):
        raise SectionGenerationError("section decisions must contain all three link types")
    normalized: dict[str, Any] = {}
    for link_type in _LINK_TYPES:
        decision = decisions[link_type]
        if not isinstance(decision, dict) or set(decision) != {
            "used_ids",
            "reason_code",
        }:
            raise SectionGenerationError(f"{link_type} decision shape is invalid")
        used_ids = decision["used_ids"]
        reason = _clean_text(
            decision["reason_code"],
            f"{link_type}.reason_code",
            allow_empty=True,
        )
        if (
            not isinstance(used_ids, list)
            or any(not isinstance(item, str) or not item for item in used_ids)
            or len(used_ids) != len(set(used_ids))
        ):
            raise SectionGenerationError(f"{link_type}.used_ids must be unique strings")
        if used_ids != inventory[link_type]:
            raise SectionGenerationError(
                f"{link_type} decisions do not match section placeholders"
            )
        gate = package["link_gates"][link_type]
        allowed = gate["selected_ids"]
        if any(item not in allowed for item in used_ids):
            raise SectionGenerationError(f"{link_type} uses an unapproved candidate")
        if len(used_ids) > gate["max_allowed"]:
            raise SectionGenerationError(f"{link_type} exceeds max_allowed")
        state = gate["opportunity_state"]
        if state == "required" and len(used_ids) < gate["min_required"]:
            raise SectionGenerationError(f"{link_type} does not meet min_required")
        if state == "none" and used_ids:
            raise SectionGenerationError(f"{link_type} is prohibited for this section")
        if not used_ids and not reason:
            raise SectionGenerationError(
                f"{link_type} needs a reason_code when unused"
            )
        if state == "recommended" and not used_ids and reason in {
            "used",
            "used_approved_candidate",
        }:
            raise SectionGenerationError(
                f"{link_type} needs a rejection reason when unused"
            )
        if used_ids:
            reason = "used_approved_candidate"
        normalized[link_type] = {
            "used_ids": list(used_ids),
            "reason_code": reason,
        }
    return normalized


def _section_summary(markdown: str, limit: int = 420) -> str:
    visible = _visible_markdown(markdown)
    visible = re.sub(r"\s+", " ", visible).strip()
    return _truncate(visible, limit)


def parse_section_generation_response(
    response_text: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated_package = validate_section_generation_package(package)
    markdown, decisions_text = _split_response(
        response_text,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    if _CJK.search(markdown):
        raise SectionGenerationError("section markdown must be English")
    if _RAW_URL.search(markdown) or _RAW_MARKDOWN_LINK.search(markdown):
        raise SectionGenerationError("section markdown must not contain raw links")
    if _HTML_LINK.search(markdown):
        raise SectionGenerationError("section markdown must not contain HTML links")
    lines = markdown.splitlines()
    expected_heading = f"## {validated_package['heading']}"
    if not lines or lines[0].strip() != expected_heading:
        raise SectionGenerationError("section must start with the exact approved H2")
    if any(line.startswith("# ") or line.startswith("## ") for line in lines[1:]):
        raise SectionGenerationError("section must not contain another H1 or H2")
    paragraph_count = _paragraph_count(markdown)
    if not 2 <= paragraph_count <= 5:
        raise SectionGenerationError("section must contain 2-5 coherent paragraphs")
    visible = _visible_markdown(markdown)
    word_count = len(_WORD.findall(visible))
    target = validated_package["target_words"]
    if word_count < target["min"] or word_count > target["max"]:
        raise SectionGenerationError(
            f"section word count {word_count} is outside {target['min']}-{target['max']}"
        )
    inventory = _placeholder_inventory(markdown)
    internal_ids = inventory["article_links"] + inventory["product_links"]
    if len(internal_ids) != len(set(internal_ids)):
        raise SectionGenerationError("internal link candidates must not repeat in one section")
    for block in re.split(r"\n\s*\n", "\n".join(lines[1:])):
        count = len(_ARTICLE_PLACEHOLDER.findall(block)) + len(
            _PRODUCT_PLACEHOLDER.findall(block)
        )
        if count > 1:
            raise SectionGenerationError(
                "a paragraph may contain at most one internal link placeholder"
            )
    try:
        decisions = json.loads(decisions_text)
    except json.JSONDecodeError as exc:
        raise SectionGenerationError("section decisions are not valid JSON") from exc
    decisions = _validate_decisions(
        decisions,
        inventory,
        validated_package,
    )
    result = {
        "version": CONTRACT_VERSION,
        "section_id": validated_package["section_id"],
        "heading": validated_package["heading"],
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "package_sha256": validated_package["package_sha256"],
        "markdown": markdown,
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "word_count": word_count,
        "paragraph_count": paragraph_count,
        "decisions": decisions,
        "used_ids": inventory,
        "summary": _section_summary(markdown),
    }
    return validate_section_generation_output(result, validated_package)


def validate_section_generation_output(
    output: Any,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated_package = validate_section_generation_package(package)
    if not isinstance(output, dict) or output.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("section generation output must be version 1")
    if output.get("section_id") != validated_package["section_id"]:
        raise SectionGenerationError("section output ID does not match package")
    if output.get("heading") != validated_package["heading"]:
        raise SectionGenerationError("section output heading does not match package")
    if output.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("section output language must be en")
    if output.get("package_sha256") != validated_package["package_sha256"]:
        raise SectionGenerationError("section output package SHA mismatch")
    markdown = output.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise SectionGenerationError("section output markdown is missing")
    expected_markdown_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    if output.get("markdown_sha256") != expected_markdown_sha:
        raise SectionGenerationError("section output markdown SHA mismatch")
    if _CJK.search(markdown):
        raise SectionGenerationError("section output markdown must be English")
    if _RAW_URL.search(markdown) or _RAW_MARKDOWN_LINK.search(markdown):
        raise SectionGenerationError("section output markdown must not contain raw links")
    if _HTML_LINK.search(markdown):
        raise SectionGenerationError("section output markdown must not contain HTML links")
    lines = markdown.splitlines()
    expected_heading = f"## {validated_package['heading']}"
    if not lines or lines[0].strip() != expected_heading:
        raise SectionGenerationError("section output must start with the approved H2")
    if any(line.startswith("# ") or line.startswith("## ") for line in lines[1:]):
        raise SectionGenerationError("section output contains an extra H1 or H2")
    paragraph_count = _paragraph_count(markdown)
    if output.get("paragraph_count") != paragraph_count or not 2 <= paragraph_count <= 5:
        raise SectionGenerationError("section output paragraph count is invalid")
    visible = _visible_markdown(markdown)
    word_count = len(_WORD.findall(visible))
    target = validated_package["target_words"]
    if (
        output.get("word_count") != word_count
        or word_count < target["min"]
        or word_count > target["max"]
    ):
        raise SectionGenerationError("section output word count is invalid")
    inventory = _placeholder_inventory(markdown)
    internal_ids = inventory["article_links"] + inventory["product_links"]
    if len(internal_ids) != len(set(internal_ids)):
        raise SectionGenerationError("section output repeats an internal candidate")
    for block in re.split(r"\n\s*\n", "\n".join(lines[1:])):
        count = len(_ARTICLE_PLACEHOLDER.findall(block)) + len(
            _PRODUCT_PLACEHOLDER.findall(block)
        )
        if count > 1:
            raise SectionGenerationError(
                "section output paragraph contains multiple internal links"
            )
    decisions = _validate_decisions(
        output.get("decisions"),
        inventory,
        validated_package,
    )
    if output.get("used_ids") != inventory:
        raise SectionGenerationError("section output used_ids do not match placeholders")
    if output.get("decisions") != decisions:
        raise SectionGenerationError("section output decisions are invalid")
    expected_summary = _section_summary(markdown)
    if output.get("summary") != expected_summary:
        raise SectionGenerationError("section output summary does not match markdown")
    return output


def _validate_slug_and_section(slug: str, section_id: str) -> tuple[str, str]:
    clean_slug = _clean_text(slug, "slug")
    clean_section_id = _clean_text(section_id, "section_id")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean_slug):
        raise SectionGenerationError("slug must use lowercase letters, numbers and hyphens")
    if not re.fullmatch(r"section-[a-f0-9]{10}", clean_section_id):
        raise SectionGenerationError("section_id format is invalid")
    return clean_slug, clean_section_id


def section_checkpoint_path(workspace: Path, slug: str, section_id: str) -> Path:
    clean_slug, clean_section_id = _validate_slug_and_section(slug, section_id)
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / "checkpoints"
        / f"{clean_section_id}.json"
    )


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = path.read_bytes() if path.exists() else None
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        payload = (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if snapshot is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(snapshot)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def persist_section_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
    output: dict[str, Any],
) -> str:
    validated_package = validate_section_generation_package(package)
    validated_output = validate_section_generation_output(output, validated_package)
    path = section_checkpoint_path(
        workspace,
        slug,
        validated_package["section_id"],
    )
    checkpoint = {
        "version": CONTRACT_VERSION,
        "section_id": validated_package["section_id"],
        "package_sha256": validated_package["package_sha256"],
        "output": validated_output,
    }
    _atomic_json_write(path, checkpoint)
    return str(path)


def load_section_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
) -> dict[str, Any] | None:
    validated_package = validate_section_generation_package(package)
    path = section_checkpoint_path(
        workspace,
        slug,
        validated_package["section_id"],
    )
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != CONTRACT_VERSION:
        return None
    if checkpoint.get("package_sha256") != validated_package["package_sha256"]:
        return None
    try:
        return validate_section_generation_output(
            checkpoint.get("output"),
            validated_package,
        )
    except SectionGenerationError:
        return None


def run_section_generation_sequence(
    *,
    workspace: Path,
    slug: str,
    section_contracts: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    generate_text: Callable[[str, str], str],
    resume: bool = True,
) -> dict[str, Any]:
    """Generate body sections sequentially and resume from valid checkpoints."""
    if not callable(generate_text):
        raise SectionGenerationError("generate_text must be callable")
    sections = validate_section_contracts(section_contracts)
    validate_section_link_contracts(link_contracts, sections)
    outputs: list[dict[str, Any]] = []
    previous_summary = ""
    generated_count = 0
    resumed_count = 0
    for section_id in sections["section_order"]:
        package = build_section_generation_package(
            sections,
            link_contracts,
            context_manifest,
            section_id,
            previous_summary=previous_summary,
        )
        output = (
            load_section_checkpoint(workspace, slug, package)
            if resume
            else None
        )
        if output is None:
            prompt = build_section_generation_prompt(package)
            response = generate_text(prompt["system"], prompt["user"])
            output = parse_section_generation_response(response, package)
            persist_section_checkpoint(workspace, slug, package, output)
            generated_count += 1
        else:
            resumed_count += 1
        outputs.append(output)
        previous_summary = output["summary"]
    return {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "section_order": sections["section_order"],
        "outputs": outputs,
        "generated_count": generated_count,
        "resumed_count": resumed_count,
        "complete": len(outputs) == len(sections["section_order"]),
    }


def build_article_frame_package(section_run: dict[str, Any]) -> dict[str, Any]:
    """Build a compact post-body package from completed section summaries."""
    if not isinstance(section_run, dict) or section_run.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("section run must be a version 1 object")
    outputs = section_run.get("outputs")
    order = section_run.get("section_order")
    if not isinstance(outputs, list) or not isinstance(order, list):
        raise SectionGenerationError("section run outputs/order are invalid")
    if [item.get("section_id") for item in outputs] != order:
        raise SectionGenerationError("section run outputs are incomplete or out of order")
    package = {
        "version": CONTRACT_VERSION,
        "topic": _clean_text(section_run.get("topic"), "section run topic"),
        "content_language": section_run.get("content_language"),
        "sections": [
            {
                "section_id": item["section_id"],
                "heading": item["heading"],
                "summary": item["summary"],
            }
            for item in outputs
        ],
        "requirements": json.loads(json.dumps(_ARTICLE_FRAME_REQUIREMENTS)),
    }
    if package["content_language"] != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("article frame language must be en")
    package["package_sha256"] = _json_digest(package)
    return validate_article_frame_package(package)


def validate_article_frame_package(package: Any) -> dict[str, Any]:
    if not isinstance(package, dict) or package.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("article frame package must be version 1")
    _clean_text(package.get("topic"), "article frame topic")
    if package.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("article frame package language must be en")
    sections = package.get("sections")
    if not isinstance(sections, list) or not sections:
        raise SectionGenerationError("article frame package needs section summaries")
    section_ids = []
    for item in sections:
        if not isinstance(item, dict) or set(item) != {
            "section_id",
            "heading",
            "summary",
        }:
            raise SectionGenerationError("article frame section summary is invalid")
        section_ids.append(_clean_text(item["section_id"], "frame section_id"))
        _clean_text(item["heading"], "frame heading")
        _clean_text(item["summary"], "frame summary")
    if len(section_ids) != len(set(section_ids)):
        raise SectionGenerationError("article frame section IDs must be unique")
    if package.get("requirements") != _ARTICLE_FRAME_REQUIREMENTS:
        raise SectionGenerationError("article frame requirements are invalid")
    expected_sha = _clean_text(
        package.get("package_sha256"),
        "article frame package_sha256",
    )
    unsigned = dict(package)
    unsigned.pop("package_sha256")
    if expected_sha != _json_digest(unsigned):
        raise SectionGenerationError("article frame package SHA mismatch")
    return package


def build_article_frame_prompt(package: dict[str, Any]) -> dict[str, str]:
    package = validate_article_frame_package(package)
    system = """Create the short framing components for an English article whose body sections are already complete.
Use only the supplied section summaries. Do not introduce new specifications, statistics, legal claims, products, URLs, links, or citations.
Return exactly four blocks and no other text:
===INTRODUCTION===
<80-180 words, one or two paragraphs, direct answer first>
===KEY_TAKEAWAYS===
<3-5 Markdown bullet points>
===CONCLUSION===
<80-180 words, no new facts>
===FAQ_JSON===
{"faqs":[{"question":"...","answer":"..."}]} with exactly 3-4 items"""
    user = "ARTICLE FRAME PACKAGE\n" + json.dumps(
        package,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {"system": system, "user": user}


def _between(text: str, start: str, end: str | None) -> str:
    if text.count(start) != 1:
        raise SectionGenerationError(f"article frame marker missing or duplicated: {start}")
    remainder = text.split(start, 1)[1]
    if end is None:
        return remainder.strip()
    if remainder.count(end) != 1:
        raise SectionGenerationError(f"article frame marker missing or duplicated: {end}")
    return remainder.split(end, 1)[0].strip()


def parse_article_frame_response(
    response_text: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    package = validate_article_frame_package(package)
    if not isinstance(response_text, str):
        raise SectionGenerationError("article frame response must be a string")
    if response_text.split(FRAME_INTRO_MARKER, 1)[0].strip():
        raise SectionGenerationError("unexpected text before article frame")
    intro = _between(response_text, FRAME_INTRO_MARKER, FRAME_TAKEAWAYS_MARKER)
    takeaways = _between(
        response_text,
        FRAME_TAKEAWAYS_MARKER,
        FRAME_CONCLUSION_MARKER,
    )
    conclusion = _between(
        response_text,
        FRAME_CONCLUSION_MARKER,
        FRAME_FAQ_MARKER,
    )
    faq_text = _between(response_text, FRAME_FAQ_MARKER, None)
    combined = "\n".join([intro, takeaways, conclusion, faq_text])
    if _CJK.search(combined):
        raise SectionGenerationError("article frame must be English")
    if _RAW_URL.search(combined) or _RAW_MARKDOWN_LINK.search(combined):
        raise SectionGenerationError("article frame must not contain links")
    requirements = package["requirements"]
    intro_words = len(_WORD.findall(intro))
    conclusion_words = len(_WORD.findall(conclusion))
    if not (
        requirements["introduction_words"]["min"]
        <= intro_words
        <= requirements["introduction_words"]["max"]
    ):
        raise SectionGenerationError("introduction word count is outside the contract")
    if not (
        requirements["conclusion_words"]["min"]
        <= conclusion_words
        <= requirements["conclusion_words"]["max"]
    ):
        raise SectionGenerationError("conclusion word count is outside the contract")
    takeaway_items = [
        line[2:].strip()
        for line in takeaways.splitlines()
        if line.startswith("- ") and line[2:].strip()
    ]
    if len(takeaway_items) != len([line for line in takeaways.splitlines() if line.strip()]):
        raise SectionGenerationError("key takeaways must contain bullet lines only")
    if not (
        requirements["takeaway_count"]["min"]
        <= len(takeaway_items)
        <= requirements["takeaway_count"]["max"]
    ):
        raise SectionGenerationError("key takeaway count is outside the contract")
    try:
        faq = json.loads(faq_text)
    except json.JSONDecodeError as exc:
        raise SectionGenerationError("FAQ block is not valid JSON") from exc
    if not isinstance(faq, dict) or set(faq) != {"faqs"} or not isinstance(faq["faqs"], list):
        raise SectionGenerationError("FAQ JSON shape is invalid")
    if not (
        requirements["faq_count"]["min"]
        <= len(faq["faqs"])
        <= requirements["faq_count"]["max"]
    ):
        raise SectionGenerationError("FAQ count is outside the contract")
    for item in faq["faqs"]:
        if not isinstance(item, dict) or set(item) != {"question", "answer"}:
            raise SectionGenerationError("FAQ item shape is invalid")
        question = _clean_text(item["question"], "FAQ question")
        answer = _clean_text(item["answer"], "FAQ answer")
        if not question.endswith("?"):
            raise SectionGenerationError("FAQ questions must end with a question mark")
        answer_words = len(_WORD.findall(answer))
        if not (
            requirements["faq_answer_words"]["min"]
            <= answer_words
            <= requirements["faq_answer_words"]["max"]
        ):
            raise SectionGenerationError("FAQ answer word count is outside the contract")
    result = {
        "version": CONTRACT_VERSION,
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "package_sha256": package["package_sha256"],
        "introduction": intro,
        "key_takeaways": takeaway_items,
        "conclusion": conclusion,
        "faq": faq["faqs"],
    }
    return validate_article_frame_output(result, package)


def validate_article_frame_output(
    output: Any,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated_package = validate_article_frame_package(package)
    if not isinstance(output, dict) or output.get("version") != CONTRACT_VERSION:
        raise SectionGenerationError("article frame output must be version 1")
    if output.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionGenerationError("article frame output language must be en")
    if output.get("package_sha256") != validated_package["package_sha256"]:
        raise SectionGenerationError("article frame output package SHA mismatch")
    introduction = _clean_text(
        output.get("introduction"),
        "article frame output introduction",
    )
    conclusion = _clean_text(
        output.get("conclusion"),
        "article frame output conclusion",
    )
    takeaways = output.get("key_takeaways")
    faq = output.get("faq")
    if not isinstance(takeaways, list) or any(
        not isinstance(item, str) or not item.strip() for item in takeaways
    ):
        raise SectionGenerationError("article frame takeaways are invalid")
    if not isinstance(faq, list) or any(not isinstance(item, dict) for item in faq):
        raise SectionGenerationError("article frame FAQ is invalid")
    combined = "\n".join(
        [introduction, *takeaways, conclusion]
        + [
            f"{item.get('question', '')} {item.get('answer', '')}"
            for item in faq
        ]
    )
    if _CJK.search(combined):
        raise SectionGenerationError("article frame output must be English")
    if (
        _RAW_URL.search(combined)
        or _RAW_MARKDOWN_LINK.search(combined)
        or _HTML_LINK.search(combined)
    ):
        raise SectionGenerationError("article frame output must not contain links")
    requirements = validated_package["requirements"]
    intro_words = len(_WORD.findall(introduction))
    conclusion_words = len(_WORD.findall(conclusion))
    if not (
        requirements["introduction_words"]["min"]
        <= intro_words
        <= requirements["introduction_words"]["max"]
    ):
        raise SectionGenerationError("article frame introduction count is invalid")
    if not (
        requirements["conclusion_words"]["min"]
        <= conclusion_words
        <= requirements["conclusion_words"]["max"]
    ):
        raise SectionGenerationError("article frame conclusion count is invalid")
    if not (
        requirements["takeaway_count"]["min"]
        <= len(takeaways)
        <= requirements["takeaway_count"]["max"]
    ):
        raise SectionGenerationError("article frame takeaway count is invalid")
    if not (
        requirements["faq_count"]["min"]
        <= len(faq)
        <= requirements["faq_count"]["max"]
    ):
        raise SectionGenerationError("article frame FAQ count is invalid")
    seen_questions: set[str] = set()
    for item in faq:
        if set(item) != {"question", "answer"}:
            raise SectionGenerationError("article frame FAQ item shape is invalid")
        question = _clean_text(item["question"], "article frame FAQ question")
        answer = _clean_text(item["answer"], "article frame FAQ answer")
        if not question.endswith("?"):
            raise SectionGenerationError("article frame FAQ question is invalid")
        normalized_question = question.casefold()
        if normalized_question in seen_questions:
            raise SectionGenerationError("article frame FAQ questions must be unique")
        seen_questions.add(normalized_question)
        answer_words = len(_WORD.findall(answer))
        if not (
            requirements["faq_answer_words"]["min"]
            <= answer_words
            <= requirements["faq_answer_words"]["max"]
        ):
            raise SectionGenerationError("article frame FAQ answer count is invalid")
    return output


def article_frame_checkpoint_path(workspace: Path, slug: str) -> Path:
    clean_slug = _clean_text(slug, "slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean_slug):
        raise SectionGenerationError("slug must use lowercase letters, numbers and hyphens")
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / "checkpoints"
        / "article-frame.json"
    )


def persist_article_frame_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
    output: dict[str, Any],
) -> str:
    validated_package = validate_article_frame_package(package)
    validated_output = validate_article_frame_output(output, validated_package)
    path = article_frame_checkpoint_path(workspace, slug)
    checkpoint = {
        "version": CONTRACT_VERSION,
        "package_sha256": validated_package["package_sha256"],
        "output": validated_output,
    }
    _atomic_json_write(path, checkpoint)
    return str(path)


def load_article_frame_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
) -> dict[str, Any] | None:
    validated_package = validate_article_frame_package(package)
    path = article_frame_checkpoint_path(workspace, slug)
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != CONTRACT_VERSION:
        return None
    if checkpoint.get("package_sha256") != validated_package["package_sha256"]:
        return None
    try:
        return validate_article_frame_output(
            checkpoint.get("output"),
            validated_package,
        )
    except SectionGenerationError:
        return None


def run_article_frame_generation(
    *,
    workspace: Path,
    slug: str,
    section_run: dict[str, Any],
    generate_text: Callable[[str, str], str],
    resume: bool = True,
) -> dict[str, Any]:
    if not callable(generate_text):
        raise SectionGenerationError("generate_text must be callable")
    package = build_article_frame_package(section_run)
    output = (
        load_article_frame_checkpoint(workspace, slug, package)
        if resume
        else None
    )
    if output is not None:
        return {"output": output, "generated": False, "resumed": True}
    prompt = build_article_frame_prompt(package)
    response = generate_text(prompt["system"], prompt["user"])
    output = parse_article_frame_response(response, package)
    persist_article_frame_checkpoint(workspace, slug, package, output)
    return {"output": output, "generated": True, "resumed": False}
