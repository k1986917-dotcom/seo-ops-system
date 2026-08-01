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

from seo_ops.services.sectional_consistency import wavelength_color_conflicts
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
_ARTICLE_PLACEHOLDER = re.compile(r"\[\[ARTICLE:([a-zA-Z0-9._-]+)\|([^\]\n]+)\]\]")
_PRODUCT_PLACEHOLDER = re.compile(r"\[\[PRODUCT:([a-zA-Z0-9._-]+)\|([^\]\n]+)\]\]")
_CITE_PLACEHOLDER = re.compile(r"\[\[CITE:([a-zA-Z0-9._-]+)\]\]")
_UNAPPROVED_CANDIDATE_ERROR = re.compile(
    r"(?P<link_type>article_links|product_links|external_citations) "
    r"uses an unapproved candidate"
)
_RESPONSE_FORMAT_ERRORS = {
    "generator response markers are missing or duplicated",
    "unexpected text before the first response marker",
    "generator response blocks must not be empty",
}
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_RAW_URL = re.compile(r"https?://", re.IGNORECASE)
_RAW_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\((?:https?://|/)[^)]+\)")
_HTML_LINK = re.compile(r"<a\s+[^>]*href\s*=", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_POTENTIAL_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_COMMON_ABBREVIATION = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|mr|mrs|ms|dr|prof|sr|jr|st|no)\.$",
    re.IGNORECASE,
)
_INITIALISM = re.compile(r"(?:[A-Za-z]\.){2,}$")
_AUTHORITY_TERM = re.compile(
    r"\b(?:OSHA|FDA|EPA|FTC|CDC|NIOSH|regulator(?:y|s)?|law|legal)\b",
    re.IGNORECASE,
)
_NAMED_AUTHORITY_TERM = re.compile(
    r"\b(?:OSHA|FDA|EPA|FTC|CDC|NIOSH|"
    r"Occupational\s+Safety\s+and\s+Health\s+Administration)\b",
    re.IGNORECASE,
)
_STRONG_RECOMMENDATION_TERM = re.compile(
    r"\b(?:recommend(?:ed|s|ation)?|compliant|compliance|approved|acceptable|"
    r"requires?|must|should\s+stick|only\s+choice|go-to\s+choice|best\s+choice|"
    r"best\s+balance|safest\s+default|practical\s+sweet\s+spot)\b",
    re.IGNORECASE,
)
_AUTHORITY_ATTRIBUTION_TERM = re.compile(
    r"\b(?:says?|states?|warns?|advises?|guidance|according\s+to|"
    r"considers?|treats?|defines?|prohibits?|permits?|allows?)\b",
    re.IGNORECASE,
)
_SAFETY_ABSOLUTE_TERM = re.compile(
    r"\b(?:eye[- ]safe|safe\s+for\s+accidental\s+eye\s+exposure|"
    r"prevents?\s+retinal\s+damage|cannot\s+cause\s+eye\s+injury|"
    r"no\s+risk\s+of\s+eye\s+injury|generally\s+(?:considered\s+)?safe|"
    r"safe(?:r|st)?\s+(?:option|choice|class|laser)|"
    r"blink\s+(?:reflex|response).{0,80}(?:protect|protection|prevent))\b",
    re.IGNORECASE | re.DOTALL,
)
_TECHNICAL_CHOICE_TERM = re.compile(
    r"\b(?:class\s*(?:1|2|2m|3r|3a|3b|4)|\d{3,4}\s*nm|laser\s+class)\b",
    re.IGNORECASE,
)
_DIRECT_QUOTE = re.compile(r'(?P<quote>"[^"\n]{18,}"|“[^”\n]{18,}”)')
_UNVERIFIED_ATTRIBUTION = re.compile(
    r"\b(?:as|according\s+to)\b[^,.]{0,100}\b"
    r"(?:said|noted|wrote|reported|stated|observed)\b\s*[,：:]?\s*"
    r"(?P<claim>[^,.!?\n]{18,})",
    re.IGNORECASE,
)
_PRODUCT_USE_CASE_CLAIM = re.compile(
    r"\b(?:ceiling[- ]focused|construction[- ]focused|"
    r"(?:designed|built|made|engineered|intended|proven|ideal|perfect|best)\s+for\s+"
    r"[^.!?]{0,100}(?:ceiling|construction|job\s*site|worksite|"
    r"long[- ]distance\s+pointing|professional\s+pointing))\b",
    re.IGNORECASE,
)
_EVIDENCE_OVERSTATEMENT_ERROR = "section contains authority or recommendation language unsupported by source-verified quote evidence"
_UNVERIFIED_QUOTE_ERROR = "section publishes unverified evidence as a direct quote"
_PRODUCT_PROVENANCE_ERROR = "product-linked sentence exceeds approved catalog provenance"
_PRODUCT_COPY_POLICY_ERROR = "product-linked sentence violates the active site copy policy"
_FRAME_OVERSTATEMENT_ERROR = "article frame contains authority, compliance, safety, or superlative language unsupported by source-verified quote evidence"
_INTERNAL_LINK_LAYOUT_ERROR = "a paragraph may contain at most one internal link placeholder"
_TECHNICAL_CONSISTENCY_ERROR = "section contains contradictory wavelength and color pairing"


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


def _suppress_product_copy_text(value: str, patterns: list[str]) -> str:
    text = str(value or "")
    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -–—,;:/")


def _sanitize_product_candidate_for_copy(
    candidate: dict[str, Any],
    patterns: list[str],
) -> dict[str, Any]:
    if not patterns:
        return candidate
    sanitized = dict(candidate)
    sanitized["title"] = _suppress_product_copy_text(
        str(candidate.get("title") or ""),
        patterns,
    )
    attributes = candidate.get("attributes")
    if isinstance(attributes, dict):
        sanitized_attributes: dict[str, str] = {}
        for key, value in attributes.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            clean_value = _suppress_product_copy_text(value, patterns)
            if clean_value:
                sanitized_attributes[key] = clean_value
        sanitized["attributes"] = sanitized_attributes
    matched_variant = candidate.get("matched_variant")
    if isinstance(matched_variant, dict):
        sanitized_variant = dict(matched_variant)
        if isinstance(matched_variant.get("text"), str):
            sanitized_variant["text"] = _suppress_product_copy_text(
                matched_variant["text"],
                patterns,
            )
        sanitized["matched_variant"] = sanitized_variant
    return sanitized


def _registry_indexes(registry: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    validated = validate_candidate_registry(registry)
    return {
        kind: {candidate["candidate_id"]: candidate for candidate in validated[kind]["candidates"]}
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
            result.append(
                {
                    "candidate_id": candidate_id,
                    "title": candidate.get("title", ""),
                    "primary_keyword": candidate.get("primary_keyword", ""),
                }
            )
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
            result.append(
                {
                    "candidate_id": candidate_id,
                    "product_id": candidate.get("product_id", ""),
                    "title": candidate.get("title", ""),
                    "attributes": _compact_product_attributes(candidate),
                    "fit_level": fit_level,
                    "fit_reason": metadata.get("fit_reason", "approved_candidate"),
                    "site_preference_score": int(metadata.get("site_preference_score", 0) or 0),
                    "active_site_rules": list(metadata.get("active_site_rules", []) or []),
                    "matched_site_preferences": list(
                        metadata.get("matched_site_preferences", []) or []
                    ),
                    "matched_variant": metadata.get("matched_variant"),
                }
            )
        else:
            result.append(
                {
                    "candidate_id": candidate_id,
                    "evidence_id": candidate.get("evidence_id", candidate_id),
                    "support": _truncate(candidate.get("support", ""), 700),
                    "support_basis": candidate.get("support_basis", "key_finding"),
                    "claim_types": candidate.get("claim_types", [])[:6],
                    "source_url": candidate.get("url", ""),
                }
            )
    return result


def _unverified_support_is_unsafe_for_writing(candidate: dict[str, Any]) -> bool:
    """Return whether model-facing support contains an unverified strong claim."""
    basis = str(candidate.get("support_basis") or "key_finding")
    if basis == "verified_quote":
        return False
    support = " ".join(str(candidate.get("support") or "").split())
    if not support:
        return True
    if _contains_strong_evidence_claim(support):
        return True
    if _STRONG_RECOMMENDATION_TERM.search(support):
        return True
    if _SAFETY_ABSOLUTE_TERM.search(support):
        return True
    return bool(
        _TECHNICAL_CHOICE_TERM.search(support)
        and re.search(
            r"\b(?:safe|safer|safest|recommended|compliant|acceptable|approved|"
            r"preferred|suitable|practical\s+choice|balanced\s+choice|"
            r"most\s+balanced|works?\s+better)\b",
            support,
            re.IGNORECASE,
        )
    )


def _writing_safe_evidence_context(
    gate: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    section_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Filter unsafe evidence from writing context without relaxing gate minima."""
    unsafe = [item for item in candidates if _unverified_support_is_unsafe_for_writing(item)]
    unsafe_ids = {str(item.get("evidence_id") or item.get("candidate_id") or "") for item in unsafe}
    safe = [
        item
        for item in candidates
        if str(item.get("evidence_id") or item.get("candidate_id") or "") not in unsafe_ids
    ]
    filtered_gate = dict(gate)
    filtered_gate["selected_ids"] = [
        item for item in gate.get("selected_ids", []) if item not in unsafe_ids
    ]
    filtered_gate["candidate_count"] = len(filtered_gate["selected_ids"])
    filtered_gate["max_allowed"] = min(
        int(gate.get("max_allowed", 0)),
        len(filtered_gate["selected_ids"]),
    )
    rejected = list(gate.get("rejected") or [])
    rejected.extend(
        {
            "candidate_id": evidence_id,
            "reason_codes": ["unverified_support_contains_strong_claim"],
        }
        for evidence_id in sorted(unsafe_ids)
    )
    filtered_gate["rejected"] = rejected
    minimum = int(filtered_gate.get("min_required", 0))
    if filtered_gate.get("opportunity_state") == "required" and len(safe) < minimum:
        raise SectionGenerationError(
            f"section {section_id} lacks enough writing-safe evidence after filtering "
            f"unverified strong claims: required {minimum}, available {len(safe)}"
        )
    return filtered_gate, safe


def _evidence_neutral_contract_text(value: str) -> str:
    """Remove unsupported authority and winner-selection direction from model prose."""
    text = str(value or "")
    text = re.sub(
        r"\bHow\s+to\s+Verify\s+"
        r"(?:OSHA|FDA|EPA|FTC|CDC|NIOSH)\s+Requirements\s+for\s+Lasers\b",
        "How to Verify Applicable Laser Requirements",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bHow\s+to\s+Verify\s+"
        r"(?:OSHA|FDA|EPA|FTC|CDC|NIOSH)\s+Requirements\s+for\s+",
        "How to Verify Applicable Requirements for ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bWhat\s+(?:OSHA|FDA|EPA|FTC|CDC|NIOSH)\s+Says\s+About\b",
        "How to Verify Applicable Requirements for",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?P<left>Class\s*[1-4](?:R|M|A|B)?)\s+vs\.?\s+"
        r"(?P<right>Class\s*[1-4](?:R|M|A|B)?)\s*[—–:-]\s*"
        r"Which\s+Laser\s+Class\s+Works\s+for\s+(?P<context>[^?]+)\?",
        r"How to Compare \g<left> and \g<right> for \g<context>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bWhy\s+(?P<subject>.+?)\s+Is\s+(?:A|An|The)\s+"
        r"(?:Practical|Best|Safest|Only|Go-to)\s+Choice\s+for\s+"
        r"(?P<context>.+?)(?=\s+after\s+validating|\s*$|[?.])",
        r"How to Evaluate \g<subject> for \g<context>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bAnswer\s+How\s+to\s+(Compare|Evaluate)\b",
        r"Explain how to \1",
        text,
        flags=re.IGNORECASE,
    )
    return _NAMED_AUTHORITY_TERM.sub("the applicable authority", text)


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

    article_candidates = _selected_candidates(
        link["article_links"],
        indexes["articles"],
        kind="article",
    )
    product_candidates = _selected_candidates(
        link["product_links"],
        indexes["products"],
        kind="product",
        metadata_by_id={
            item["candidate_id"]: item
            for item in manifest_section.get("product_candidates", [])
            if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
        },
    )
    site_profile_payload: dict[str, Any] | None = None
    if link["product_links"]["opportunity_state"] != "none":
        site_profile_payload = dict(
            context_manifest.get("site_profile")
            or {
                "site_slug": "default",
                "version": 1,
                "product_candidate_limit": 2,
                "product_link_limit_per_section": 2,
                "unique_product_per_article": False,
                "required_fit_levels": [
                    "approved_constraint",
                    "contextual",
                    "related_catalog",
                    "strong",
                ],
                "high_power_policy": "not_applicable",
                "product_copy_instruction": "",
                "product_copy_suppressed_patterns": [],
                "use_case_rules": [],
            }
        )
        copy_patterns = site_profile_payload.get("product_copy_suppressed_patterns", [])
        if isinstance(copy_patterns, list) and all(isinstance(item, str) for item in copy_patterns):
            product_candidates = [
                _sanitize_product_candidate_for_copy(item, copy_patterns)
                for item in product_candidates
            ]
    evidence_candidates = _selected_candidates(
        link["external_citations"],
        indexes["evidence"],
        kind="evidence",
    )
    evidence_gate, evidence_candidates = _writing_safe_evidence_context(
        link["external_citations"],
        evidence_candidates,
        section_id=clean_section_id,
    )
    has_verified_quote = any(
        item.get("support_basis") == "verified_quote" for item in evidence_candidates
    )
    model_text = (lambda value: value) if has_verified_quote else _evidence_neutral_contract_text

    package = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": sections["content_language"],
        "section_id": clean_section_id,
        "position": section["position"],
        "heading": model_text(section["heading"]),
        "reader_stage": section["reader_stage"],
        "reader_question": model_text(section["reader_question"]),
        "section_goal": model_text(section["section_goal"]),
        "must_answer": [model_text(item) for item in section["must_answer"]],
        "brief_points": [model_text(item) for item in approved_brief_points],
        "brief_points_rejected": rejected_brief_points,
        "must_not_repeat": [model_text(item) for item in section["must_not_repeat"]],
        "target_words": section["target_words"],
        "previous_heading": model_text(section["previous_section"]),
        "previous_summary": model_text(previous),
        "next_heading": model_text(section["next_section"]),
        "link_gates": {
            "article_links": dict(link["article_links"]),
            "product_links": dict(link["product_links"]),
            "external_citations": evidence_gate,
        },
        "candidates": {
            "articles": article_candidates,
            "products": product_candidates,
            "evidence": evidence_candidates,
        },
    }
    if site_profile_payload is not None:
        package["site_profile"] = site_profile_payload
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
    site_profile = package.get("site_profile")
    if site_profile is not None and (
        not isinstance(site_profile, dict)
        or not isinstance(site_profile.get("site_slug"), str)
        or not site_profile["site_slug"]
        or not isinstance(site_profile.get("version"), int)
        or not isinstance(site_profile.get("high_power_policy"), str)
        or not isinstance(site_profile.get("product_copy_instruction", ""), str)
        or not isinstance(site_profile.get("product_copy_suppressed_patterns", []), list)
        or any(
            not isinstance(item, str)
            for item in site_profile.get("product_copy_suppressed_patterns", [])
        )
    ):
        raise SectionGenerationError("section generation package site_profile is invalid")
    if site_profile is not None:
        for pattern in site_profile.get("product_copy_suppressed_patterns", []):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise SectionGenerationError(
                    "section generation package product copy pattern is invalid"
                ) from exc
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
    verified_ids = _verified_quote_ids(validated)
    site_profile = validated.get("site_profile") or {}
    site_policy_rule = (
        "\nThe active site profile treats high output as neutral for product ranking: "
        "do not reject, apologize for, or replace an approved product merely because "
        "its supplied power is high. This does not permit safety, compliance, approval, "
        "or universal-use claims. Product-free classification, hazard, safety, and "
        "compliance sections are enforced by a none product gate; never add a product "
        "there. When one catalog page contains multiple color or power variants, discuss "
        "only the exact variant supported by the supplied attributes and matched site "
        "preferences. Never merge specifications from different variants."
        if site_profile.get("high_power_policy") == "neutral_for_ranking_and_never_an_exclusion"
        else ""
    )
    product_copy_instruction = str(site_profile.get("product_copy_instruction") or "").strip()
    if product_copy_instruction:
        site_policy_rule += "\nPRODUCT COPY POLICY. " + product_copy_instruction
    authority_free_rule = (
        "\nThis package has zero source-verified quotes. In body paragraphs, do not "
        "name OSHA, FDA, EPA, FTC, CDC, NIOSH, the Occupational Safety and Health "
        "Administration, any regulator, or any named authority. Do not claim that an "
        "authority publishes, issues, sets, governs, defines, requires, permits, "
        "prohibits, recommends, warns, approves, or enforces anything. The exact H2 "
        "may contain an authority name; do not repeat that name below the H2. Do not "
        "claim that one laser class, wavelength, output level, or product is safe, "
        "safer, safest, generally safe, compliant, acceptable, preferred, best, more "
        "suitable, a practical or balanced choice, or protected by a blink reflex or "
        "blink response. If the section compares options, explain only how to compare "
        "and verify them from the supplied support; do not choose a winner. Explain "
        "only neutral verification steps, employer/site procedures, labels, hazard "
        "assessment, training, and operational controls supported by the package."
        if not verified_ids
        else ""
    )
    system = (
        """You write exactly one English H2 section for a larger article.
Use only the supplied section contract, evidence, article candidates, and product candidates.
Do not invent URLs, product IDs, article IDs, evidence IDs, specifications, statistics, laws, or claims.
Do not output raw URLs, Markdown links, HTML links, an H1, or a second H2.
For evidence candidates, support is the only factual source text. claim_types and all other fields are metadata, not facts. support_basis=verified_quote means the quote was explicitly verified against its source. support_basis=quote means quotation text exists but has not been source-verified: paraphrase it without quotation marks and never present it as words someone said, wrote, or noted. support_basis=key_finding means the support is a synthesized research note. Only verified_quote may support an authority attribution, regulatory recommendation or requirement, compliance/approval/acceptable-use conclusion, absolute safety claim, or superlative such as only/best/go-to/safest.
Product candidates include a fit_level. A strong or approved_constraint candidate may be described only with the supplied attributes. A contextual candidate has limited section overlap. A related_catalog candidate is related to the article topic but is not an exact use-case match: it may be recommended as a related catalog option, but never claim it was designed for, proven for, compliant with, or specifically suitable for the exact section use case.
Use approved placeholders only:
[[ARTICLE:candidate_id|natural English anchor text]]
[[PRODUCT:candidate_id|natural English anchor text]]
[[CITE:evidence_id]]
The first content line must be the exact requested H2. Write 2-5 coherent paragraphs and answer directly before expanding.
Respect every link gate. A required gate must meet min_required. A none gate must not be used. If a recommended gate is unused, give a concise machine-readable reason_code in the decisions JSON.
Each paragraph may contain at most one ARTICLE or PRODUCT placeholder. Put additional internal links in separate sentences and separate paragraphs. CITE placeholders do not count toward this internal-link limit.
Decision reason codes are deterministic: whenever used_ids is non-empty, set reason_code to used_approved_candidate. When a recommended gate is unused, use a concise rejection reason such as not_needed_for_this_section. When a none gate is unused, copy that gate's supplied reason_code.
Return exactly two blocks and no other text:
===SECTION_MARKDOWN===
<section markdown>
===SECTION_DECISIONS===
{"article_links":{"used_ids":[],"reason_code":"..."},"product_links":{"used_ids":[],"reason_code":"..."},"external_citations":{"used_ids":[],"reason_code":"..."}}"""
        + authority_free_rule
        + site_policy_rule
    )
    user_keys = [
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
    ]
    if "site_profile" in validated:
        user_keys.insert(2, "site_profile")
    user_payload = {key: validated[key] for key in user_keys}
    user = "SECTION PACKAGE\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {"system": system, "user": user}


def _contains_strong_evidence_claim(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean:
        return False
    absolute = re.search(
        r"\b(?:only\s+choice|go-to\s+choice|best\s+choice|best\s+balance|"
        r"safest\s+default|practical\s+sweet\s+spot)\b",
        clean,
        re.IGNORECASE,
    )
    if absolute:
        return True
    if _AUTHORITY_TERM.search(clean) and _STRONG_RECOMMENDATION_TERM.search(clean):
        return True
    if _AUTHORITY_TERM.search(clean) and _AUTHORITY_ATTRIBUTION_TERM.search(clean):
        return True
    if _SAFETY_ABSOLUTE_TERM.search(clean):
        return True
    if _TECHNICAL_CHOICE_TERM.search(clean) and re.search(
        r"\b(?:recommended|compliant|acceptable|approved|should\s+stick|preferred|"
        r"suitable|practical\s+choice|balanced\s+choice|most\s+balanced|"
        r"works?\s+better)\b",
        clean,
        re.IGNORECASE,
    ):
        return True
    return False


def _evidence_support_basis(package: dict[str, Any]) -> dict[str, str]:
    evidence = package.get("candidates", {}).get("evidence", [])
    if not isinstance(evidence, list):
        return {}
    return {
        str(item.get("evidence_id") or item.get("candidate_id") or ""): str(
            item.get("support_basis") or "key_finding"
        )
        for item in evidence
        if isinstance(item, dict)
    }


def _verified_quote_ids(package: dict[str, Any]) -> list[str]:
    return [
        evidence_id
        for evidence_id, basis in _evidence_support_basis(package).items()
        if basis == "verified_quote"
    ]


def _normalized_match_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _validate_unverified_direct_quotes(
    markdown: str,
    package: dict[str, Any],
) -> None:
    evidence = package.get("candidates", {}).get("evidence", [])
    if not isinstance(evidence, list):
        return
    unverified: list[tuple[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict) or item.get("support_basis") == "verified_quote":
            continue
        evidence_id = str(item.get("evidence_id") or item.get("candidate_id") or "")
        support = _normalized_match_text(str(item.get("support") or ""))
        if evidence_id and len(support) >= 18:
            unverified.append((evidence_id, support))
    if not unverified:
        return
    for match in _DIRECT_QUOTE.finditer(markdown):
        quoted = match.group("quote").strip('"“”')
        quoted_norm = _normalized_match_text(quoted)
        if len(quoted_norm) < 18:
            continue
        for evidence_id, support_norm in unverified:
            if quoted_norm in support_norm or support_norm in quoted_norm:
                raise SectionGenerationError(f"{_UNVERIFIED_QUOTE_ERROR}: {evidence_id}")
    for match in _UNVERIFIED_ATTRIBUTION.finditer(markdown):
        claim_tokens = set(_normalized_match_text(match.group("claim")).split())
        if len(claim_tokens) < 4:
            continue
        for evidence_id, support_norm in unverified:
            support_tokens = set(support_norm.split())
            overlap = claim_tokens & support_tokens
            if (
                len(overlap) >= 4
                and len(overlap)
                / min(
                    len(claim_tokens),
                    len(support_tokens),
                )
                >= 0.45
            ):
                raise SectionGenerationError(f"{_UNVERIFIED_QUOTE_ERROR}: {evidence_id}")


def _validate_product_catalog_provenance(
    markdown: str,
    package: dict[str, Any],
) -> None:
    candidates = package.get("candidates", {}).get("products", [])
    if not isinstance(candidates, list):
        return
    by_id = {
        str(item.get("candidate_id") or ""): item for item in candidates if isinstance(item, dict)
    }
    suppressed_patterns = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in (package.get("site_profile") or {}).get(
            "product_copy_suppressed_patterns",
            [],
        )
    ]
    body = "\n".join(markdown.splitlines()[1:])
    for block in re.split(r"\n\s*\n", body):
        for sentence in _POTENTIAL_SENTENCE_BREAK.split(block):
            product_ids = [item[0] for item in _PRODUCT_PLACEHOLDER.findall(sentence)]
            for candidate_id in product_ids:
                candidate = by_id.get(candidate_id)
                if candidate is None:
                    continue
                visible_sentence = _PRODUCT_PLACEHOLDER.sub(
                    lambda match: match.group(2),
                    sentence,
                )
                for pattern in suppressed_patterns:
                    if pattern.search(visible_sentence):
                        raise SectionGenerationError(
                            f"{_PRODUCT_COPY_POLICY_ERROR}: {candidate_id}"
                        )
                source_text = _normalized_match_text(
                    " ".join(
                        [
                            str(candidate.get("title") or ""),
                            json.dumps(
                                candidate.get("attributes") or {},
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            json.dumps(
                                candidate.get("matched_variant") or {},
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        ]
                    )
                )
                for claim_match in _PRODUCT_USE_CASE_CLAIM.finditer(visible_sentence):
                    claim_text = _normalized_match_text(claim_match.group(0))
                    if claim_text and claim_text not in source_text:
                        raise SectionGenerationError(f"{_PRODUCT_PROVENANCE_ERROR}: {candidate_id}")


def _validate_section_technical_consistency(markdown: str) -> None:
    visible = _visible_markdown(markdown)
    conflicts = wavelength_color_conflicts(visible)
    if not conflicts:
        return
    detail = ", ".join(
        f"{item['wavelength_nm']}nm stated as {item['stated_color']} "
        f"instead of {item['expected_color']}"
        for item in conflicts[:4]
    )
    raise SectionGenerationError(f"{_TECHNICAL_CONSISTENCY_ERROR}: {detail}")


def _validate_section_evidence_strength(
    markdown: str,
    package: dict[str, Any],
) -> None:
    basis_by_id = _evidence_support_basis(package)
    for block in re.split(r"\n\s*\n", markdown):
        body_block = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("#")
        )
        visible = _ARTICLE_PLACEHOLDER.sub(lambda match: match.group(2), body_block)
        visible = _PRODUCT_PLACEHOLDER.sub(lambda match: match.group(2), visible)
        visible = _CITE_PLACEHOLDER.sub("", visible)
        if not _contains_strong_evidence_claim(visible):
            continue
        citation_ids = _CITE_PLACEHOLDER.findall(body_block)
        if any(basis_by_id.get(item) == "verified_quote" for item in citation_ids):
            continue
        excerpt = " ".join(visible.split())[:500]
        raise SectionGenerationError(f"{_EVIDENCE_OVERSTATEMENT_ERROR}: {excerpt}")


def validate_claim_evidence_strength(
    claim_ledger: dict[str, Any],
    context_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed on strong recommendations backed only by synthesized notes."""
    if not isinstance(claim_ledger, dict):
        raise SectionGenerationError("claim ledger must be an object")
    registry = validate_candidate_registry(context_manifest.get("registry"))
    basis_by_id = {
        str(item.get("evidence_id") or item.get("candidate_id") or ""): str(
            item.get("support_basis") or "key_finding"
        )
        for item in registry["evidence"]["candidates"]
    }
    for claim in claim_ledger.get("claims", []):
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("claim_text") or "")
        if not _contains_strong_evidence_claim(text):
            continue
        evidence_ids = claim.get("evidence_ids") or []
        if any(basis_by_id.get(str(item)) == "verified_quote" for item in evidence_ids):
            continue
        sentence_id = str(claim.get("sentence_id") or "unknown")
        raise SectionGenerationError(f"{_EVIDENCE_OVERSTATEMENT_ERROR}: {sentence_id}")
    return claim_ledger


_SECTION_WORD_COUNT_ERROR = re.compile(
    r"^section word count (?P<count>\d+) is outside "
    r"(?P<minimum>\d+)-(?P<maximum>\d+)$"
)
_REQUIRED_LINK_ERROR = re.compile(
    r"^(?P<link_type>article_links|product_links|external_citations) "
    r"does not meet min_required$"
)


def _build_word_count_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one constrained retry only for an otherwise parseable length miss."""
    match = _SECTION_WORD_COUNT_ERROR.fullmatch(str(error))
    if match is None:
        return None
    validated = validate_section_generation_package(package)
    target = validated["target_words"]
    count = int(match.group("count"))
    span = target["max"] - target["min"]
    buffer = max(10, min(40, span // 6))
    preferred_min = min(target["max"], target["min"] + buffer)
    preferred_max = max(preferred_min, target["max"] - buffer)
    direction = "expand" if count < target["min"] else "trim"
    if direction == "trim":
        minimum_change = max(1, count - preferred_max)
        maximum_change = max(minimum_change, count - preferred_min)
        exact_action = (
            f"Delete {minimum_change}-{maximum_change} visible words. Do not add any "
            "new words or replace short wording with longer wording. Prefer deleting "
            "one non-essential clause or sentence that contains no placeholder or citation. "
        )
    else:
        minimum_change = max(1, preferred_min - count)
        maximum_change = max(minimum_change, preferred_max - count)
        exact_action = (
            f"Add {minimum_change}-{maximum_change} visible words using only the "
            "existing approved meaning. Do not add a new claim or specification. "
        )
    base = build_section_generation_prompt(validated)
    repair_system = (
        base["system"]
        + "\nThis is the only repair attempt for a section that missed its word-count "
        "contract. Preserve the approved H2, factual meaning, placeholder IDs, "
        "link decisions, and 2-5 paragraph structure. Do not introduce any new "
        "claim, URL, product, specification, law, statistic, or evidence ID."
    )
    repair_user = (
        base["user"]
        + "\n\nWORD COUNT REPAIR\n"
        + f"The previous section had {count} visible words and must be {target['min']}-"
        + f"{target['max']}. {direction.capitalize()} only the existing approved "
        + f"content and aim safely inside {preferred_min}-{preferred_max} visible words. "
        + exact_action
        + "Return the complete two-block response again.\n\n"
        + "PREVIOUS RESPONSE\n"
        + response_text
    )
    return {"system": repair_system, "user": repair_user}


def _build_final_word_count_trim_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one deletion-only final trim when the normal repair remains too long."""
    match = _SECTION_WORD_COUNT_ERROR.fullmatch(str(error))
    if match is None:
        return None
    validated = validate_section_generation_package(package)
    target = validated["target_words"]
    count = int(match.group("count"))
    if count <= target["max"]:
        return None
    final_max = max(target["min"], target["max"] - 10)
    final_min = max(target["min"], final_max - 30)
    minimum_delete = max(1, count - final_max)
    maximum_delete = max(minimum_delete, count - final_min)
    base = build_section_generation_prompt(validated)
    system = (
        base["system"] + "\nFINAL WORD COUNT TRIM. This is deletion-only, not a rewrite. Preserve "
        "the exact H2, factual meaning, approved placeholders and their order, link "
        "decisions, citations, and 2-5 paragraph structure. Do not add, substitute, "
        "or expand any wording. Delete only non-essential prose that contains no "
        "placeholder or citation. Keep the decisions JSON unchanged."
    )
    user = (
        base["user"]
        + "\n\nFINAL WORD COUNT TRIM\n"
        + f"The server still counts {count} visible words after the normal repair. "
        + f"Delete {minimum_delete}-{maximum_delete} visible words so the final body "
        + f"lands inside {final_min}-{final_max}. Do not add or paraphrase anything. "
        + "Return the complete two-block response.\n\nPREVIOUS RESPONSE\n"
        + response_text
    )
    return {"system": system, "user": user}


def _build_internal_link_layout_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one formatting-only repair for an unresolved link collision."""
    if str(error) != _INTERNAL_LINK_LAYOUT_ERROR:
        return None
    base = build_section_generation_prompt(package)
    system = (
        base["system"] + "\nINTERNAL LINK LAYOUT REPAIR. Preserve the exact H2, factual meaning, "
        "approved placeholder IDs, citation IDs, and link decisions. Do not add or "
        "remove any fact, product, article, evidence item, URL, specification, law, "
        "or recommendation. Reformat the body so every paragraph contains at most "
        "one ARTICLE or PRODUCT placeholder. Prefer turning existing sentence breaks "
        "into paragraph breaks. If two internal placeholders occur in one sentence, "
        "split only that sentence into two concise sentences with equivalent meaning "
        "and place them in separate paragraphs. Keep 2-5 coherent paragraphs, keep "
        "the same placeholder inventory and order, and return the complete two-block "
        "response with decisions JSON matching the final placeholders."
    )
    user = (
        base["user"]
        + "\n\nINTERNAL LINK LAYOUT REPAIR\n"
        + "The previous response passed the content contract but still placed more "
        "than one ARTICLE or PRODUCT placeholder in a paragraph. Make only the "
        "minimum formatting or sentence-split change needed to satisfy the rule. "
        "Do not rewrite unrelated prose.\n\nPREVIOUS RESPONSE\n" + response_text
    )
    return {"system": system, "user": user}


def _build_required_link_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one bounded repair that restores only missing required placeholders."""
    match = _REQUIRED_LINK_ERROR.fullmatch(str(error))
    if match is None:
        return None
    validated = validate_section_generation_package(package)
    link_type = match.group("link_type")
    gate = validated["link_gates"][link_type]
    if gate["opportunity_state"] != "required":
        return None
    markdown, _ = _split_response(
        response_text,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    inventory = _placeholder_inventory(markdown)
    missing_count = int(gate["min_required"]) - len(inventory[link_type])
    if missing_count <= 0:
        return None
    available_ids = [
        candidate_id
        for candidate_id in gate["selected_ids"]
        if candidate_id not in inventory[link_type]
    ]
    if len(available_ids) < missing_count:
        return None
    required_ids = available_ids[:missing_count]
    if link_type == "article_links":
        placeholder_rule = (
            "Add exactly one ARTICLE placeholder for each listed ID by wrapping an "
            "existing relevant phrase with a natural English anchor. Do not add a new "
            "article claim or summary."
        )
    elif link_type == "product_links":
        placeholder_rule = (
            "Add exactly one PRODUCT placeholder for each listed ID. Place it around "
            "existing neutral catalog-reference wording, or add one short neutral "
            "navigation sentence identifying it only as a related catalog option. Do "
            "not claim that a product was designed for, proven for, safe for, compliant "
            "with, preferred for, best for, or specifically suitable for this use case."
        )
    else:
        placeholder_rule = (
            "Add exactly one CITE placeholder for each listed evidence ID immediately "
            "after an existing sentence already supported by that evidence. Do not add, "
            "strengthen, or rephrase a factual claim merely to place the citation."
        )
    base = build_section_generation_prompt(validated)
    system = (
        base["system"] + "\nREQUIRED LINK REPAIR. This is the only attempt to restore a missing "
        "required placeholder. Preserve the exact H2, supported factual meaning, "
        "existing placeholder tokens and order, word-count contract, and 2-5 paragraph "
        "structure. Do not remove, replace, or reorder an existing placeholder. Do not "
        "add any placeholder except the exact approved IDs listed below. Place any new "
        "ARTICLE or PRODUCT placeholder in a paragraph that contains no other ARTICLE "
        "or PRODUCT placeholder. Keep the decisions JSON valid; the server will derive "
        "used_ids from the final Markdown."
    )
    user = (
        base["user"]
        + "\n\nREQUIRED LINK REPAIR\n"
        + f"Missing link type: {link_type}. Minimum required: {gate['min_required']}. "
        + "Add exactly these approved IDs once each: "
        + ", ".join(required_ids)
        + ".\n"
        + placeholder_rule
        + " If the added placeholder would push the body over its word-count maximum, "
        "delete only equivalent non-essential prose that contains no placeholder or "
        "citation. Return the complete two-block response.\n\nPREVIOUS RESPONSE\n" + response_text
    )
    return {"system": system, "user": user}


def _build_unapproved_candidate_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
    *,
    final: bool = False,
) -> dict[str, str] | None:
    """Build one bounded repair for placeholders outside the approved gate."""

    match = _UNAPPROVED_CANDIDATE_ERROR.fullmatch(str(error))
    if match is None:
        return None
    validated = validate_section_generation_package(package)
    link_type = match.group("link_type")
    markdown, _ = _split_response(
        response_text,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    inventory = _placeholder_inventory(markdown)
    gate = validated["link_gates"][link_type]
    allowed_ids = list(gate["selected_ids"])
    invalid_ids = [item for item in inventory[link_type] if item not in allowed_ids]
    if not invalid_ids:
        return None

    if link_type == "product_links":
        target_rule = (
            "Use only PRODUCT candidates present in the clean section package. Remove "
            "every name, model, specification, feature, suitability claim, and anchor "
            "that belongs to an unapproved product. If the product gate is required, "
            "include an approved PRODUCT placeholder and describe only attributes "
            "actually supplied for that approved candidate."
        )
    elif link_type == "article_links":
        target_rule = (
            "Use only approved ARTICLE candidates. Do not retain a title, topic, anchor, "
            "or summary that belongs to an unapproved article."
        )
    else:
        target_rule = (
            "Use only approved CITE evidence IDs. Remove an unapproved citation rather "
            "than attaching an approved citation to a sentence it does not support."
        )

    state = gate["opportunity_state"]
    if state == "none":
        gate_rule = f"The {link_type} gate is none, so return zero placeholders of this type."
    else:
        gate_rule = (
            f"Allowed IDs: {', '.join(allowed_ids) or '(none)'}. Keep between "
            f"{gate['min_required']} and {gate['max_allowed']} placeholders of this type."
        )

    base = build_section_generation_prompt(validated)
    label = "FINAL APPROVED CANDIDATE REPAIR" if final else "APPROVED CANDIDATE REPAIR"
    preservation_rule = (
        "Rebuild every link type from the clean section package. Every ARTICLE, PRODUCT, "
        "and CITE placeholder must use only its gate's selected_ids and satisfy that "
        "gate's min/max counts. Do not preserve placeholder choices from the rejected "
        "response. The server will canonicalize decisions from the final Markdown. "
        if final
        else "Preserve every placeholder of the other two link types with the same IDs "
        "and order. The server will canonicalize decisions from the final Markdown. "
    )
    system = (
        base["system"] + f"\n{label}. The server rejected placeholder IDs outside the active gate. "
        "Never widen the gate, invent an ID, or reuse a candidate from another section "
        "or an earlier run. Preserve the exact H2, supported non-candidate facts, word "
        "count, and paragraph contract. " + preservation_rule + target_rule
    )
    user = (
        base["user"]
        + f"\n\n{label}\n"
        + f"Rejected link type: {link_type}. Rejected IDs: {', '.join(invalid_ids)}. "
        + gate_rule
        + " Return the complete two-block response."
    )
    if final:
        user += (
            " Rewrite from the clean section package above. Do not reuse product, "
            "article, citation, anchor, specification, or recommendation wording from "
            "the rejected response."
        )
    else:
        user += "\n\nPREVIOUS RESPONSE\n" + response_text
    return {"system": system, "user": user}


def _build_final_candidate_selection_repair_prompt(
    package: dict[str, Any],
) -> dict[str, str]:
    """Rebuild one section from clean gates after a bounded repair drifted."""

    validated = validate_section_generation_package(package)
    base = build_section_generation_prompt(validated)
    gate_lines = []
    for link_type in _LINK_TYPES:
        gate = validated["link_gates"][link_type]
        gate_lines.append(
            f"- {link_type}: state={gate['opportunity_state']}; "
            f"allowed={', '.join(gate['selected_ids']) or '(none)'}; "
            f"min={gate['min_required']}; max={gate['max_allowed']}"
        )
    system = (
        base["system"]
        + "\nFINAL APPROVED CANDIDATE REPAIR. Rewrite the complete section only from "
        "the clean SECTION PACKAGE. Do not reuse any product, article, citation, anchor, "
        "specification, feature, suitability claim, recommendation, or placeholder "
        "choice from a rejected response. Rebuild all three placeholder inventories "
        "from their active gates. Never widen a gate or invent an ID. PRODUCT wording "
        "may describe only attributes supplied for the approved product candidate. "
        "Preserve the exact H2, supported non-candidate facts, word-count contract, and "
        "paragraph contract. The server will derive decisions from the final Markdown."
    )
    user = (
        base["user"]
        + "\n\nFINAL APPROVED CANDIDATE REPAIR\n"
        + "Build a fresh complete two-block response. Obey these gates exactly:\n"
        + "\n".join(gate_lines)
        + "\nDo not refer to either rejected response."
    )
    return {"system": system, "user": user}


def _build_response_format_repair_prompt(
    package: dict[str, Any],
    error: SectionGenerationError,
    *,
    final: bool = False,
) -> dict[str, str] | None:
    """Retry a malformed two-block response from the clean section package."""

    if str(error) not in _RESPONSE_FORMAT_ERRORS:
        return None
    validated = validate_section_generation_package(package)
    base = build_section_generation_prompt(validated)
    target = validated["target_words"]
    label = "FINAL RESPONSE FORMAT REPAIR" if final else "RESPONSE FORMAT REPAIR"
    system = (
        base["system"] + f"\n{label}. The previous completion could not be parsed as the required "
        "two-block response. Start again from the clean SECTION PACKAGE. Do not echo "
        "instructions, use a code fence, add commentary, or mention this repair. Emit "
        f"{SECTION_MARKDOWN_MARKER} exactly once as the first output line and "
        f"{SECTION_DECISIONS_MARKER} exactly once after the complete section Markdown. "
        "Return valid JSON after the decisions marker and no text after that JSON. "
        f"Keep the body inside 2-5 coherent paragraphs and {target['min']}-{target['max']} "
        "visible words. All content, word-count, paragraph, evidence, product, and "
        "link gates remain unchanged and will be validated server-side."
    )
    user = (
        base["user"]
        + f"\n\n{label}\n"
        + "Generate a fresh complete response from the clean package above. The exact "
        "output shape is:\n"
        + f"{SECTION_MARKDOWN_MARKER}\n"
        + "## <exact requested H2>\n\n<section body>\n"
        + f"{SECTION_DECISIONS_MARKER}\n"
        + '{"article_links":{"used_ids":[],"reason_code":"..."},'
        + '"product_links":{"used_ids":[],"reason_code":"..."},'
        + '"external_citations":{"used_ids":[],"reason_code":"..."}}'
    )
    return {"system": system, "user": user}


def _build_technical_consistency_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one bounded repair for an explicit wavelength/color contradiction."""
    error_text = str(error)
    if not error_text.startswith(_TECHNICAL_CONSISTENCY_ERROR):
        return None
    base = build_section_generation_prompt(package)
    detail = error_text.removeprefix(_TECHNICAL_CONSISTENCY_ERROR).lstrip(": ")
    system = (
        base["system"] + "\nTECHNICAL CONSISTENCY REPAIR. Correct only explicit wavelength/color "
        "contradictions. Use only the clean product attributes and evidence supplied "
        "in the section package. Do not guess a wavelength, color, output, product "
        "feature, law, safety conclusion, URL, or evidence ID. Preserve the exact H2, "
        "supported factual meaning, placeholder IDs and order, paragraph contract, "
        "word-count contract, and decisions JSON. If a contradictory product detail "
        "is not supported by the clean package, remove that detail rather than "
        "inventing a replacement."
    )
    user = (
        base["user"]
        + "\n\nTECHNICAL CONSISTENCY REPAIR\n"
        + "The server detected this deterministic contradiction: "
        + detail
        + ". Make the minimum correction needed and return the complete two-block "
        "response. Do not rewrite unrelated prose.\n\nPREVIOUS RESPONSE\n" + response_text
    )
    return {"system": system, "user": user}


def _build_content_provenance_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
    *,
    final: bool = False,
) -> dict[str, str] | None:
    """Repair unverified quotes or product copy that exceeds catalog policy."""

    error_text = str(error)
    quote_error = error_text.startswith(_UNVERIFIED_QUOTE_ERROR)
    product_error = error_text.startswith((_PRODUCT_PROVENANCE_ERROR, _PRODUCT_COPY_POLICY_ERROR))
    if not quote_error and not product_error:
        return None
    validated = validate_section_generation_package(package)
    base = build_section_generation_prompt(validated)
    label = "FINAL CONTENT PROVENANCE REPAIR" if final else "CONTENT PROVENANCE REPAIR"
    if quote_error:
        specific = (
            "Remove direct quotation marks and any wording that presents the unverified "
            "text as words someone said, wrote, or noted. Paraphrase only the supported "
            "meaning. Keep the approved citation placeholder if it remains relevant."
        )
    else:
        specific = (
            "Rewrite each PRODUCT-linked sentence as natural commercial copy using only "
            "the approved catalog candidate. Omit wattage, output level, laser class, "
            "safety, compliance, certification, and hazard wording when the site copy "
            "policy suppresses it. Do not explain or reconcile the omitted fields. Do "
            "not call the product ceiling-focused, construction-focused, designed for, "
            "approved for, safe for, or specifically suitable for the article use case "
            "unless that exact claim appears in the supplied catalog attributes. Keep "
            "the approved PRODUCT ID and describe only neutral catalog attributes such "
            "as wavelength/color, form factor, beam mode, focus, charging, or visibility."
        )
    preservation = (
        "Rebuild the complete response from the clean SECTION PACKAGE and all active "
        "link gates. Do not reuse prose from the rejected response."
        if final
        else "Make the minimum change and preserve every approved placeholder ID and order."
    )
    system = (
        base["system"]
        + f"\n{label}. {specific} {preservation} Preserve the exact H2, factual meaning "
        "that remains supported, word-count contract, paragraph contract, and decisions "
        "JSON. Never invent a new fact, product, candidate ID, specification, URL, or "
        "evidence ID."
    )
    user = base["user"] + f"\n\n{label}\n" + specific + " Return the complete two-block response."
    if not final:
        user += "\n\nPREVIOUS RESPONSE\n" + response_text
    return {"system": system, "user": user}


def _build_section_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> tuple[dict[str, str], str] | None:
    response_format_prompt = _build_response_format_repair_prompt(
        package,
        error,
    )
    if response_format_prompt is not None:
        return response_format_prompt, "response_format"
    word_count_prompt = _build_word_count_repair_prompt(
        package,
        response_text,
        error,
    )
    if word_count_prompt is not None:
        return word_count_prompt, "word_count"
    link_layout_prompt = _build_internal_link_layout_repair_prompt(
        package,
        response_text,
        error,
    )
    if link_layout_prompt is not None:
        return link_layout_prompt, "link_layout"
    required_link_prompt = _build_required_link_repair_prompt(
        package,
        response_text,
        error,
    )
    if required_link_prompt is not None:
        return required_link_prompt, "required_link"
    candidate_prompt = _build_unapproved_candidate_repair_prompt(
        package,
        response_text,
        error,
    )
    if candidate_prompt is not None:
        return candidate_prompt, "candidate_selection"
    technical_prompt = _build_technical_consistency_repair_prompt(
        package,
        response_text,
        error,
    )
    if technical_prompt is not None:
        return technical_prompt, "technical_consistency"
    provenance_prompt = _build_content_provenance_repair_prompt(
        package,
        response_text,
        error,
    )
    if provenance_prompt is not None:
        return provenance_prompt, "content_provenance"
    error_text = str(error)
    if not error_text.startswith(_EVIDENCE_OVERSTATEMENT_ERROR):
        return None
    base = build_section_generation_prompt(package)
    offending = error_text.removeprefix(_EVIDENCE_OVERSTATEMENT_ERROR).lstrip(": ")
    verified_ids = _verified_quote_ids(package)
    verified_note = (
        "This package has no source-verified quote evidence. Do not state or imply "
        "that any named authority says, publishes, issues, sets, governs, defines, "
        "warns, requires, permits, prohibits, allows, recommends, approves, enforces, "
        "or regulates anything. Do not name OSHA, FDA, EPA, FTC, CDC, NIOSH, the "
        "Occupational Safety and Health Administration, a regulator, or an agency in "
        "body paragraphs. Do not say that a class, wavelength, output level, or product "
        "is safe, safer, generally safe, compliant, acceptable, preferred, best, more "
        "suitable, a practical or balanced choice, or protected by a blink response. "
        "The exact H2 may retain an authority name. Replace all body attribution and "
        "winner-selection language with neutral verification steps and site-specific "
        "safety controls."
        if not verified_ids
        else "Only the listed source-verified evidence IDs may support a direct authority attribution: "
        + ", ".join(verified_ids)
        + "."
    )
    repair_system = (
        base["system"] + "\nThis is the only repair attempt for unsupported authority, compliance, "
        "recommendation, or superlative language. Keep the exact approved H2, "
        "answer the same reader question, and stay inside the same word-count and "
        "paragraph contracts. Rewrite the overstatement as neutral analysis using "
        "only each evidence candidate's support text. A key_finding may not be "
        "presented as an authority's exact recommendation or requirement. Preserve "
        "approved placeholder IDs where they remain relevant and keep decisions JSON "
        "consistent with the final placeholders. Do not merely add hedging words to "
        "the same unsupported attribution; remove or recast the attribution itself."
    )
    repair_user = (
        base["user"]
        + "\n\nEVIDENCE STRENGTH REPAIR\n"
        + verified_note
        + "\n\nSERVER-DETECTED OFFENDING PASSAGE\n"
        + offending
        + "\n\n"
        + "Remove or qualify every unsupported recommendation, compliance claim, "
        + "authority attribution, and absolute phrase such as only/best/go-to. Do not "
        + "introduce any new fact, number, URL, product, law, or evidence ID. Return "
        + "the complete two-block response again.\n\nPREVIOUS RESPONSE\n"
        + response_text
    )
    return {"system": repair_system, "user": repair_user}, "evidence_strength"


def _build_authority_free_repair_prompt(
    package: dict[str, Any],
    _response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    """Build one final bounded repair when zero verified quotes remain."""
    error_text = str(error)
    if not error_text.startswith(_EVIDENCE_OVERSTATEMENT_ERROR) or _verified_quote_ids(package):
        return None
    base = build_section_generation_prompt(package)
    system = (
        base["system"] + "\nFINAL AUTHORITY-FREE REPAIR. Keep the exact H2, but after that H2 the "
        "body must contain none of these names or labels: OSHA, Occupational Safety "
        "and Health Administration, FDA, EPA, FTC, CDC, NIOSH, regulator, agency. "
        "Do not discuss what any authority publishes, governs, requires, permits, "
        "prohibits, recommends, warns, approves, or enforces. Do not say that a laser "
        "class, wavelength, output level, or product is safe, safer, safest, generally "
        "safe, compliant, acceptable, preferred, best, more suitable, a practical or "
        "balanced choice, or protected by a blink reflex or blink response. For a "
        "comparison section, describe only a non-conclusive evaluation process using "
        "the supplied support; do not select a winner. Use no new facts."
    )
    user = (
        base["user"]
        + "\n\nFINAL AUTHORITY-FREE REPAIR\n"
        + "The previous attempts contained unsupported authority, safety, compliance, "
        "or winner-selection language. Rewrite the complete response from scratch "
        "using only the clean section package above. Preserve only supported facts and "
        "approved placeholders. Do not reuse any prose from either previous response. "
        "Return the complete two-block response again."
    )
    return {"system": system, "user": user}


def _build_final_section_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
    prior_kind: str,
) -> tuple[dict[str, str], str] | None:
    """Choose one final bounded repair from the newly exposed failure kind."""
    response_format = _build_response_format_repair_prompt(
        package,
        error,
        final=True,
    )
    if response_format is not None:
        return response_format, "response_format"
    if prior_kind == "candidate_selection":
        return _build_final_candidate_selection_repair_prompt(package), "candidate_selection"
    if prior_kind == "word_count":
        strict_trim = _build_final_word_count_trim_prompt(
            package,
            response_text,
            error,
        )
        if strict_trim is not None:
            return strict_trim, "word_count_strict_trim"
    word_count = _build_word_count_repair_prompt(
        package,
        response_text,
        error,
    )
    if word_count is not None:
        return word_count, "word_count"
    if prior_kind != "technical_consistency":
        technical = _build_technical_consistency_repair_prompt(
            package,
            response_text,
            error,
        )
        if technical is not None:
            return technical, "technical_consistency"
    if prior_kind != "link_layout":
        link_layout = _build_internal_link_layout_repair_prompt(
            package,
            response_text,
            error,
        )
        if link_layout is not None:
            return link_layout, "link_layout"
    if prior_kind != "required_link":
        required_link = _build_required_link_repair_prompt(
            package,
            response_text,
            error,
        )
        if required_link is not None:
            return required_link, "required_link"
    candidate_selection = _build_unapproved_candidate_repair_prompt(
        package,
        response_text,
        error,
        final=True,
    )
    if candidate_selection is not None:
        return candidate_selection, "candidate_selection"
    provenance = _build_content_provenance_repair_prompt(
        package,
        response_text,
        error,
        final=True,
    )
    if provenance is not None:
        return provenance, "content_provenance"
    authority_free = _build_authority_free_repair_prompt(
        package,
        response_text,
        error,
    )
    if authority_free is not None:
        return authority_free, "authority_free"
    return None


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


def _placeholder_token_sequence(markdown: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for pattern in (_ARTICLE_PLACEHOLDER, _PRODUCT_PLACEHOLDER, _CITE_PLACEHOLDER):
        matches.extend((match.start(), match.group(0)) for match in pattern.finditer(markdown))
    return [token for _, token in sorted(matches)]


def _canonical_decisions_for_inventory(
    inventory: dict[str, list[str]],
    package: dict[str, Any],
) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for link_type in _LINK_TYPES:
        gate = package["link_gates"][link_type]
        used_ids = list(inventory[link_type])
        if used_ids:
            reason = "used_approved_candidate"
        elif gate["opportunity_state"] == "none":
            reason = gate["reason_code"]
        else:
            reason = "not_needed_for_this_section"
        decisions[link_type] = {
            "used_ids": used_ids,
            "reason_code": reason,
        }
    return _validate_decisions(decisions, inventory, package)


def _canonicalize_link_layout_repair_response(
    previous_response: str,
    repaired_response: str,
    package: dict[str, Any],
) -> str:
    """Keep formatting-only repairs from rewriting redundant decisions JSON.

    The repaired Markdown is accepted only when every exact placeholder token,
    including anchor text and global order, matches the response that triggered
    the layout repair.  The server then regenerates decisions from that immutable
    placeholder inventory and still validates all package gates fail-closed.
    """
    previous_markdown, _ = _split_response(
        previous_response,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    repaired_markdown, _ = _split_response(
        repaired_response,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    if _placeholder_token_sequence(previous_markdown) != _placeholder_token_sequence(
        repaired_markdown
    ):
        raise SectionGenerationError("link_layout repair changed placeholder inventory or order")
    inventory = _placeholder_inventory(repaired_markdown)
    decisions = _canonical_decisions_for_inventory(inventory, package)
    return (
        f"{SECTION_MARKDOWN_MARKER}\n{repaired_markdown}\n"
        f"{SECTION_DECISIONS_MARKER}\n"
        f"{json.dumps(decisions, ensure_ascii=False, sort_keys=True)}"
    )


def _canonicalize_candidate_selection_repair_response(
    previous_response: str,
    repaired_response: str,
    package: dict[str, Any],
    *,
    preserve_unrelated: bool = True,
) -> str:
    """Canonicalize candidate repair while keeping every gate fail-closed.

    The first repair is a minimal edit and must preserve unrelated placeholder
    inventories.  The final repair is generated from the clean package and may
    rebuild all three inventories, but every ID and count is still validated
    against the active gates before the response can be accepted.
    """

    repaired_markdown, _ = _split_response(
        repaired_response,
        SECTION_MARKDOWN_MARKER,
        SECTION_DECISIONS_MARKER,
    )
    repaired_inventory = _placeholder_inventory(repaired_markdown)
    if preserve_unrelated:
        previous_markdown, _ = _split_response(
            previous_response,
            SECTION_MARKDOWN_MARKER,
            SECTION_DECISIONS_MARKER,
        )
        previous_inventory = _placeholder_inventory(previous_markdown)
        invalid_types = {
            link_type
            for link_type in _LINK_TYPES
            if any(
                candidate_id not in package["link_gates"][link_type]["selected_ids"]
                for candidate_id in previous_inventory[link_type]
            )
        }
        if not invalid_types:
            raise SectionGenerationError(
                "candidate_selection repair has no rejected placeholder to repair"
            )
        for link_type in _LINK_TYPES:
            if link_type in invalid_types:
                continue
            if repaired_inventory[link_type] != previous_inventory[link_type]:
                raise SectionGenerationError(
                    "candidate_selection repair changed unrelated placeholder inventory or order"
                )
    decisions = _canonical_decisions_for_inventory(repaired_inventory, package)
    return (
        f"{SECTION_MARKDOWN_MARKER}\n{repaired_markdown}\n"
        f"{SECTION_DECISIONS_MARKER}\n"
        f"{json.dumps(decisions, ensure_ascii=False, sort_keys=True)}"
    )


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
            {"candidate_id": candidate_id} for candidate_id in _CITE_PLACEHOLDER.findall(markdown)
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


def _internal_placeholder_count(value: str) -> int:
    return len(_ARTICLE_PLACEHOLDER.findall(value)) + len(_PRODUCT_PLACEHOLDER.findall(value))


def _plain_prose_block(value: str) -> bool:
    return not any(
        re.match(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```|~~~|\|)", line.strip())
        for line in value.splitlines()
        if line.strip()
    )


def _split_prose_sentences(value: str) -> list[str]:
    sentences: list[str] = []
    cursor = 0
    for match in _POTENTIAL_SENTENCE_BREAK.finditer(value):
        candidate = value[cursor : match.start()].strip()
        if not candidate:
            cursor = match.end()
            continue
        if _COMMON_ABBREVIATION.search(candidate) or _INITIALISM.search(candidate):
            continue
        sentences.append(candidate)
        cursor = match.end()
    tail = value[cursor:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _link_safe_sentence_groups(block: str) -> list[str] | None:
    sentences = _split_prose_sentences(block)
    if not sentences or any(_internal_placeholder_count(item) > 1 for item in sentences):
        return None
    groups: list[list[str]] = []
    current: list[str] = []
    current_internal_count = 0
    for sentence in sentences:
        sentence_internal_count = _internal_placeholder_count(sentence)
        if sentence_internal_count and current_internal_count:
            groups.append(current)
            current = [sentence]
            current_internal_count = sentence_internal_count
            continue
        current.append(sentence)
        current_internal_count += sentence_internal_count
    if current:
        groups.append(current)
    return [" ".join(group) for group in groups]


def _merge_paragraph_groups(groups: list[str], limit: int = 5) -> list[str] | None:
    merged = list(groups)
    while len(merged) > limit:
        candidates: list[tuple[int, int, int]] = []
        for index in range(len(merged) - 1):
            combined_internal_count = _internal_placeholder_count(
                merged[index] + " " + merged[index + 1]
            )
            if combined_internal_count <= 1:
                candidates.append(
                    (
                        combined_internal_count,
                        len(merged[index]) + len(merged[index + 1]),
                        index,
                    )
                )
        if not candidates:
            return None
        _, _, index = min(candidates)
        merged[index : index + 2] = [merged[index] + " " + merged[index + 1]]
    return merged


def _split_single_paragraph(block: str) -> list[str] | None:
    sentences = _split_prose_sentences(block)
    if len(sentences) < 2:
        return None
    best: tuple[int, list[str]] | None = None
    target = len(block) // 2
    for index in range(1, len(sentences)):
        left = " ".join(sentences[:index])
        right = " ".join(sentences[index:])
        if _internal_placeholder_count(left) > 1 or _internal_placeholder_count(right) > 1:
            continue
        score = abs(len(left) - target)
        if best is None or score < best[0]:
            best = (score, [left, right])
    return best[1] if best is not None else None


def _normalize_section_paragraphs(markdown: str) -> str:
    """Repair formatting-only paragraph failures without changing content.

    The normalizer preserves visible wording, placeholder inventory and order.
    It may split only at existing sentence boundaries or merge adjacent prose
    paragraphs.  Structured Markdown and same-sentence link collisions remain
    fail-closed.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown.strip())]
    if len(blocks) <= 1:
        return markdown
    heading, body = blocks[0], blocks[1:]
    needs_normalization = (
        len(body) < 2
        or len(body) > 5
        or any(_internal_placeholder_count(block) > 1 for block in body)
    )
    if not needs_normalization or any(not _plain_prose_block(block) for block in body):
        return markdown

    groups: list[str] = []
    for block in body:
        if _internal_placeholder_count(block) <= 1:
            groups.append(" ".join(block.split()))
            continue
        split_groups = _link_safe_sentence_groups(block)
        if split_groups is None:
            return markdown
        groups.extend(split_groups)

    if len(groups) > 5:
        compacted = _merge_paragraph_groups(groups)
        if compacted is None:
            return markdown
        groups = compacted
    if len(groups) == 1:
        split_groups = _split_single_paragraph(groups[0])
        if split_groups is None:
            return markdown
        groups = split_groups
    if not 2 <= len(groups) <= 5:
        return markdown
    return "\n\n".join([heading, *groups])


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
        reported_used_ids = decision["used_ids"]
        reason = _clean_text(
            decision["reason_code"],
            f"{link_type}.reason_code",
            allow_empty=True,
        )
        if (
            not isinstance(reported_used_ids, list)
            or any(not isinstance(item, str) or not item for item in reported_used_ids)
            or len(reported_used_ids) != len(set(reported_used_ids))
        ):
            raise SectionGenerationError(f"{link_type}.used_ids must be unique strings")
        # Markdown placeholders are the user-visible source of truth. The model's
        # used_ids list is redundant metadata and is normalized from the validated
        # inventory so a copy error cannot block an otherwise valid section. All
        # candidate, count and required/prohibited gates below still apply to the
        # actual placeholder inventory.
        used_ids = list(inventory[link_type])
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
            raise SectionGenerationError(f"{link_type} needs a reason_code when unused")
        if (
            state == "recommended"
            and not used_ids
            and reason
            in {
                "used",
                "used_approved_candidate",
            }
        ):
            raise SectionGenerationError(f"{link_type} needs a rejection reason when unused")
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
    markdown = _normalize_section_paragraphs(markdown)
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
        raise SectionGenerationError(
            f"section must contain 2-5 coherent paragraphs (got {paragraph_count})"
        )
    _validate_section_technical_consistency(markdown)
    _validate_unverified_direct_quotes(markdown, validated_package)
    _validate_product_catalog_provenance(markdown, validated_package)
    _validate_section_evidence_strength(markdown, validated_package)
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
        count = len(_ARTICLE_PLACEHOLDER.findall(block)) + len(_PRODUCT_PLACEHOLDER.findall(block))
        if count > 1:
            raise SectionGenerationError(_INTERNAL_LINK_LAYOUT_ERROR)
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
    _validate_section_technical_consistency(markdown)
    _validate_unverified_direct_quotes(markdown, validated_package)
    _validate_product_catalog_provenance(markdown, validated_package)
    _validate_section_evidence_strength(markdown, validated_package)
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
        count = len(_ARTICLE_PLACEHOLDER.findall(block)) + len(_PRODUCT_PLACEHOLDER.findall(block))
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
        payload = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
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
    word_count_retry_count = 0
    evidence_strength_retry_count = 0
    link_layout_retry_count = 0
    required_link_retry_count = 0
    candidate_selection_retry_count = 0
    response_format_retry_count = 0
    technical_consistency_retry_count = 0
    content_provenance_retry_count = 0
    for section_id in sections["section_order"]:
        package = build_section_generation_package(
            sections,
            link_contracts,
            context_manifest,
            section_id,
            previous_summary=previous_summary,
        )
        output = load_section_checkpoint(workspace, slug, package) if resume else None
        if output is None:
            prompt = build_section_generation_prompt(package)
            response = generate_text(prompt["system"], prompt["user"])
            try:
                output = parse_section_generation_response(response, package)
            except SectionGenerationError as exc:
                repair = _build_section_repair_prompt(
                    package,
                    response,
                    exc,
                )
                if repair is None:
                    raise SectionGenerationError(
                        f"section {section_id} ({package['heading']}) failed: {exc}"
                    ) from exc
                repair_prompt, repair_kind = repair
                repaired_response = generate_text(
                    repair_prompt["system"],
                    repair_prompt["user"],
                )
                if repair_kind == "word_count":
                    word_count_retry_count += 1
                elif repair_kind == "link_layout":
                    link_layout_retry_count += 1
                elif repair_kind == "required_link":
                    required_link_retry_count += 1
                elif repair_kind == "candidate_selection":
                    candidate_selection_retry_count += 1
                elif repair_kind == "response_format":
                    response_format_retry_count += 1
                elif repair_kind == "technical_consistency":
                    technical_consistency_retry_count += 1
                elif repair_kind == "content_provenance":
                    content_provenance_retry_count += 1
                else:
                    evidence_strength_retry_count += 1
                try:
                    if repair_kind == "link_layout":
                        repaired_response = _canonicalize_link_layout_repair_response(
                            response,
                            repaired_response,
                            package,
                        )
                    elif repair_kind == "candidate_selection":
                        repaired_response = _canonicalize_candidate_selection_repair_response(
                            response,
                            repaired_response,
                            package,
                        )
                    output = parse_section_generation_response(repaired_response, package)
                except SectionGenerationError as repair_exc:
                    final_repair = _build_final_section_repair_prompt(
                        package,
                        repaired_response,
                        repair_exc,
                        repair_kind,
                    )
                    if final_repair is None:
                        raise SectionGenerationError(
                            f"section {section_id} ({package['heading']}) "
                            f"{repair_kind} repair failed: {repair_exc}"
                        ) from repair_exc
                    final_prompt, final_kind = final_repair
                    final_response = generate_text(
                        final_prompt["system"],
                        final_prompt["user"],
                    )
                    if final_kind == "link_layout":
                        link_layout_retry_count += 1
                    elif final_kind == "required_link":
                        required_link_retry_count += 1
                    elif final_kind == "candidate_selection":
                        candidate_selection_retry_count += 1
                    elif final_kind == "response_format":
                        response_format_retry_count += 1
                    elif final_kind == "technical_consistency":
                        technical_consistency_retry_count += 1
                    elif final_kind == "content_provenance":
                        content_provenance_retry_count += 1
                    elif final_kind in ("word_count", "word_count_strict_trim"):
                        word_count_retry_count += 1
                    else:
                        evidence_strength_retry_count += 1
                    try:
                        if final_kind == "link_layout":
                            final_response = _canonicalize_link_layout_repair_response(
                                repaired_response,
                                final_response,
                                package,
                            )
                        elif final_kind == "candidate_selection":
                            final_response = _canonicalize_candidate_selection_repair_response(
                                repaired_response,
                                final_response,
                                package,
                                preserve_unrelated=False,
                            )
                        output = parse_section_generation_response(final_response, package)
                    except SectionGenerationError as final_exc:
                        raise SectionGenerationError(
                            f"section {section_id} ({package['heading']}) "
                            f"{final_kind} repair failed: {final_exc}"
                        ) from final_exc
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
        "word_count_retry_count": word_count_retry_count,
        "evidence_strength_retry_count": evidence_strength_retry_count,
        "link_layout_retry_count": link_layout_retry_count,
        "required_link_retry_count": required_link_retry_count,
        "candidate_selection_retry_count": candidate_selection_retry_count,
        "response_format_retry_count": response_format_retry_count,
        "technical_consistency_retry_count": technical_consistency_retry_count,
        "content_provenance_retry_count": content_provenance_retry_count,
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
Summarize trade-offs neutrally. Do not state that an authority recommends, approves, accepts, or deems a product/class compliant. Do not use absolute phrases such as only choice, go-to choice, best choice, safest default, or practical sweet spot.
Do not name OSHA, FDA, EPA, FTC, CDC, NIOSH, the Occupational Safety and Health Administration, any regulator, or any agency. The article frame has no citation channel, so named-authority statements are prohibited even when a body H2 mentions an authority.
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


def _validate_article_frame_evidence_language(value: str) -> None:
    for part in re.split(r"(?<=[.!?])\s+|\n+", value):
        if _contains_strong_evidence_claim(part):
            offending = " ".join(part.split())[:500]
            raise SectionGenerationError(f"{_FRAME_OVERSTATEMENT_ERROR}: {offending}")


def _build_article_frame_repair_prompt(
    package: dict[str, Any],
    response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    error_text = str(error)
    if not error_text.startswith(_FRAME_OVERSTATEMENT_ERROR):
        return None
    offending = error_text.removeprefix(_FRAME_OVERSTATEMENT_ERROR).lstrip(": ")
    base = build_article_frame_prompt(package)
    return {
        "system": (
            base["system"] + "\nThis is the only repair attempt. Preserve the four-block protocol, "
            "word/count limits, questions, and useful reader guidance, but rewrite "
            "authority attributions, compliance claims, recommendations, and "
            "superlatives as neutral trade-off language. Do not name OSHA, FDA, EPA, "
            "FTC, CDC, NIOSH, any regulator, or any agency. Do not add facts."
        ),
        "user": (
            base["user"]
            + "\n\nFRAME EVIDENCE STRENGTH REPAIR\n"
            + "Remove unsupported recommended/compliant/only/best/go-to wording and "
            "return the complete four-block response again.\n\n"
            "SERVER-DETECTED OFFENDING PASSAGE\n"
            + offending
            + "\n\nPREVIOUS RESPONSE\n"
            + response_text
        ),
    }


def _build_article_frame_authority_free_repair_prompt(
    package: dict[str, Any],
    _response_text: str,
    error: SectionGenerationError,
) -> dict[str, str] | None:
    if not str(error).startswith(_FRAME_OVERSTATEMENT_ERROR):
        return None
    base = build_article_frame_prompt(package)
    return {
        "system": (
            base["system"] + "\nFINAL FRAME AUTHORITY-FREE REPAIR. Rewrite all four "
            "blocks from scratch using only the ARTICLE FRAME PACKAGE section summaries. "
            "None of the prose or FAQ text may contain OSHA, "
            "Occupational Safety and Health Administration, FDA, EPA, FTC, CDC, "
            "NIOSH, regulator, or agency. Do not describe what an authority publishes, "
            "governs, requires, permits, prohibits, recommends, warns, approves, or "
            "enforces. Do not call anything compliant, approved, acceptable, safe, safer, "
            "safest, best, preferred, suitable, a practical choice, a balanced choice, "
            "or a winner. Do not select Class 2, Class 3R, a wavelength, an output level, "
            "or a product as the answer. Use neutral process language such as compare, "
            "verify, review, document, check, depends on site conditions, and follow "
            "established site procedures. Do not add any fact, number, regulation, "
            "product, URL, link, or citation."
        ),
        "user": (
            base["user"]
            + "\n\nFINAL FRAME AUTHORITY-FREE REPAIR\n"
            + "Generate a fresh introduction, 3-5 key takeaways, conclusion, and 3-4 "
            "FAQ items from the supplied section summaries only. Return the complete "
            "four-block response. Do not reuse or imitate any earlier frame response."
        ),
    }


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
    _validate_article_frame_evidence_language(combined)
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
        requirements["faq_count"]["min"] <= len(faq["faqs"]) <= requirements["faq_count"]["max"]
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
        + [f"{item.get('question', '')} {item.get('answer', '')}" for item in faq]
    )
    if _CJK.search(combined):
        raise SectionGenerationError("article frame output must be English")
    if (
        _RAW_URL.search(combined)
        or _RAW_MARKDOWN_LINK.search(combined)
        or _HTML_LINK.search(combined)
    ):
        raise SectionGenerationError("article frame output must not contain links")
    _validate_article_frame_evidence_language(combined)
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
    if not (requirements["faq_count"]["min"] <= len(faq) <= requirements["faq_count"]["max"]):
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
        Path(workspace) / "drafts" / "sectional" / clean_slug / "checkpoints" / "article-frame.json"
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
    output = load_article_frame_checkpoint(workspace, slug, package) if resume else None
    if output is not None:
        return {
            "output": output,
            "generated": False,
            "resumed": True,
            "evidence_strength_repaired": False,
            "evidence_strength_retry_count": 0,
        }
    prompt = build_article_frame_prompt(package)
    response = generate_text(prompt["system"], prompt["user"])
    try:
        output = parse_article_frame_response(response, package)
        repaired = False
        retry_count = 0
    except SectionGenerationError as exc:
        repair_prompt = _build_article_frame_repair_prompt(package, response, exc)
        if repair_prompt is None:
            raise
        repaired_response = generate_text(
            repair_prompt["system"],
            repair_prompt["user"],
        )
        try:
            output = parse_article_frame_response(repaired_response, package)
            retry_count = 1
        except SectionGenerationError as repair_exc:
            final_prompt = _build_article_frame_authority_free_repair_prompt(
                package,
                repaired_response,
                repair_exc,
            )
            if final_prompt is None:
                raise
            final_response = generate_text(
                final_prompt["system"],
                final_prompt["user"],
            )
            try:
                output = parse_article_frame_response(final_response, package)
            except SectionGenerationError as final_exc:
                raise SectionGenerationError(
                    f"article frame authority_free repair failed: {final_exc}"
                ) from final_exc
            retry_count = 2
        repaired = True
    persist_article_frame_checkpoint(workspace, slug, package, output)
    return {
        "output": output,
        "generated": True,
        "resumed": False,
        "evidence_strength_repaired": repaired,
        "evidence_strength_retry_count": retry_count,
    }
