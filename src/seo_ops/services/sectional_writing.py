"""Deterministic contracts for the section-based writing pipeline.

Phase 1 is intentionally side-effect free unless ``persist_contract_bundle``
is called explicitly. Nothing in the live Legacy workflow imports this module
yet; it establishes strict, rebuildable data contracts for later shadow mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 1
DEFAULT_CONTENT_LANGUAGE = "en"
READER_STAGES = frozenset(
    {
        "discover",
        "understand",
        "compare",
        "select",
        "apply",
        "verify",
    }
)
OPPORTUNITY_STATES = frozenset({"unassessed", "required", "recommended", "none"})
PRODUCT_CONSTRAINT_OPERATORS = frozenset(
    {
        "contains",
        "equals",
        "exists",
        "gte",
        "lte",
        "one_of",
    }
)
PRODUCT_CONSTRAINT_SOURCES = frozenset(
    {
        "catalog_policy",
        "operator_approved",
        "site_policy",
    }
)
_DEFERRED_FRAME_HEADINGS = frozenset(
    {
        "introduction",
        "key takeaways",
        "conclusion",
        "faq",
        "faqs",
        "frequently asked questions",
    }
)

_VERIFY_TERMS = frozenset(
    {
        "accident",
        "compliance",
        "danger",
        "injury",
        "law",
        "legal",
        "mistake",
        "mistakes",
        "regulation",
        "safety",
        "warning",
    }
)
_COMPARE_TERMS = frozenset({"compare", "comparison", "versus", "vs"})
_STRONG_SELECT_TERMS = frozenset(
    {
        "choose",
        "choosing",
        "recommend",
        "recommendation",
        "recommendations",
        "recommended",
        "selection",
    }
)
_WEAK_SELECT_TERMS = frozenset(
    {
        "best",
        "top",
        "which",
    }
)
_APPLY_TERMS = frozenset({"apply", "install", "mount", "setup", "use", "using"})


class ContractValidationError(ValueError):
    """Raised when a sectional writing contract is malformed."""


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field} must be a str, got {type(value).__name__}")
    result = " ".join(value.split())
    if not allow_empty and not result:
        raise ContractValidationError(f"{field} must not be empty")
    return result


def _normalized_heading(heading: str) -> str:
    value = re.sub(r"^\s*(?:#{1,6}\s*|H2:\s*)", "", heading, flags=re.IGNORECASE)
    return _text(value, "heading")


def _neutralized_heading(heading: str) -> str:
    """Soften unsupported absolute outline language without changing its ID."""
    normalized = _normalized_heading(heading)
    neutralized = re.sub(
        r"\bthe\s+only\s+choice\b",
        "A Practical Choice",
        normalized,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\bwhat\s+(OSHA|FDA|EPA|FTC|CDC|NIOSH)\s+says\s+about\s+(.+)$",
        r"How to Verify \1 Requirements for \2",
        neutralized,
        flags=re.IGNORECASE,
    )


def _stable_heading_identity(heading: str) -> str:
    normalized = _normalized_heading(heading).casefold()
    normalized = re.sub(r"\ba\s+practical\s+choice\b", "the only choice", normalized)
    return re.sub(
        r"\bhow\s+to\s+verify\s+(osha|fda|epa|ftc|cdc|niosh)\s+"
        r"requirements\s+for\s+(.+)$",
        r"what \1 says about \2",
        normalized,
    )


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", value))


def _english_legacy_heading(value: str) -> str:
    cleaned = re.sub(
        r"\([^)]*[\u3400-\u4dbf\u4e00-\u9fff][^)]*\)",
        "",
        value,
    )
    cleaned = " ".join(cleaned.split())
    if _contains_cjk(cleaned):
        raise ContractValidationError(
            "brief H2 must be English before entering the writing contract"
        )
    return cleaned


def stable_section_id(position: int, heading: str) -> str:
    """Return a stable section ID that survives harmless outline reordering."""
    if isinstance(position, bool) or not isinstance(position, int) or position < 1:
        raise ContractValidationError("position must be an integer >= 1")
    normalized = _stable_heading_identity(heading)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"section-{digest}"


def infer_reader_stage(heading: str, position: int) -> str:
    """Conservatively infer a reader stage from a planned H2 heading."""
    normalized_heading = _normalized_heading(heading).lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized_heading))
    if tokens & _STRONG_SELECT_TERMS:
        return "select"
    if "what to look for" in normalized_heading:
        return "select"
    if tokens & _COMPARE_TERMS:
        return "compare"
    if tokens & _VERIFY_TERMS:
        return "verify"
    if position == 1:
        return "discover"
    if tokens & _WEAK_SELECT_TERMS:
        return "select"
    if tokens & _APPLY_TERMS:
        return "apply"
    return "understand"


def _product_link_policy(stage: str) -> tuple[bool, str]:
    if stage == "verify":
        return False, "section_role_prohibits_product_link"
    if stage in {"compare", "select", "apply"}:
        return True, "phase2_pending"
    return False, "reader_not_ready_for_product_link"


def build_article_blueprint(
    *,
    topic: str,
    tier: str,
    intent: str,
    outline: list[str],
    guidance: str = "",
    content_language: str = DEFAULT_CONTENT_LANGUAGE,
) -> dict[str, Any]:
    """Build a stable article blueprint from an approved ordered outline."""
    clean_topic = _text(topic, "topic")
    clean_tier = _text(tier, "tier", allow_empty=True)
    clean_intent = _text(intent, "intent", allow_empty=True)
    clean_guidance = _text(guidance, "guidance", allow_empty=True)
    clean_language = _text(content_language, "content_language").casefold()
    if clean_language != DEFAULT_CONTENT_LANGUAGE:
        raise ContractValidationError("sectional writing currently requires content_language=en")
    if not isinstance(outline, list) or not outline:
        raise ContractValidationError("outline must be a non-empty list")

    sections: list[dict[str, Any]] = []
    seen_headings: set[str] = set()
    for position, raw_heading in enumerate(outline, start=1):
        heading = _neutralized_heading(raw_heading)
        heading_key = heading.casefold()
        if heading_key in seen_headings:
            raise ContractValidationError(f"duplicate outline heading: {heading}")
        seen_headings.add(heading_key)
        section_id = stable_section_id(position, heading)
        sections.append(
            {
                "section_id": section_id,
                "position": position,
                "heading": heading,
                "reader_stage": infer_reader_stage(heading, position),
            }
        )

    blueprint = {
        "version": CONTRACT_VERSION,
        "topic": clean_topic,
        "tier": clean_tier,
        "intent": clean_intent,
        "core_task": clean_intent or clean_topic,
        "guidance": clean_guidance,
        "content_language": clean_language,
        "section_order": [section["section_id"] for section in sections],
        "sections": sections,
    }
    return validate_article_blueprint(blueprint)


def build_section_contracts(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic writing contracts from a validated blueprint."""
    validated = validate_article_blueprint(blueprint)
    sections: list[dict[str, Any]] = []
    source_sections = validated["sections"]
    for index, source in enumerate(source_sections):
        previous_heading = source_sections[index - 1]["heading"] if index else ""
        next_heading = (
            source_sections[index + 1]["heading"] if index + 1 < len(source_sections) else ""
        )
        stage = source["reader_stage"]
        product_allowed, product_reason = _product_link_policy(stage)
        sections.append(
            {
                **source,
                "reader_question": source["heading"],
                "section_goal": f"Give a complete, useful answer about {source['heading']}.",
                "must_answer": [source["heading"]],
                "brief_points": [],
                "brief_points_rejected": [],
                "must_not_repeat": (
                    [f"Do not repeat the full explanation from {previous_heading}."]
                    if previous_heading
                    else []
                ),
                "previous_section": previous_heading,
                "next_section": next_heading,
                "target_words": {"min": 350, "max": 500},
                "allowed_evidence_ids": [],
                "product_link_allowed": product_allowed,
                "product_link_policy_reason": product_reason,
                "product_constraints": [],
            }
        )
    contracts = {
        "version": CONTRACT_VERSION,
        "topic": validated["topic"],
        "content_language": validated["content_language"],
        "section_order": validated["section_order"],
        "sections": sections,
    }
    return validate_section_contracts(contracts, validated)


def _unassessed_link_gate(max_allowed: int) -> dict[str, Any]:
    return {
        "opportunity_state": "unassessed",
        "candidate_count": 0,
        "min_required": None,
        "max_allowed": max_allowed,
        "reason_code": "phase2_pending",
        "selected_ids": [],
        "rejected": [],
    }


def _none_link_gate(reason_code: str) -> dict[str, Any]:
    return {
        "opportunity_state": "none",
        "candidate_count": 0,
        "min_required": 0,
        "max_allowed": 0,
        "reason_code": reason_code,
        "selected_ids": [],
        "rejected": [],
    }


def build_section_link_contracts(
    section_contracts: dict[str, Any],
) -> dict[str, Any]:
    """Create unresolved link gates without silently defaulting to zero."""
    validated = validate_section_contracts(section_contracts)
    sections = []
    for section in validated["sections"]:
        product_gate = (
            _unassessed_link_gate(1)
            if section["product_link_allowed"]
            else _none_link_gate(section["product_link_policy_reason"])
        )
        sections.append(
            {
                "section_id": section["section_id"],
                "article_links": _unassessed_link_gate(2),
                "product_links": product_gate,
                "external_citations": _unassessed_link_gate(3),
            }
        )
    contracts = {
        "version": CONTRACT_VERSION,
        "topic": validated["topic"],
        "content_language": validated["content_language"],
        "section_order": validated["section_order"],
        "sections": sections,
    }
    return validate_section_link_contracts(contracts, validated)


def build_contract_bundle(
    *,
    topic: str,
    tier: str,
    intent: str,
    outline: list[str],
    guidance: str = "",
    content_language: str = DEFAULT_CONTENT_LANGUAGE,
) -> dict[str, Any]:
    blueprint = build_article_blueprint(
        topic=topic,
        tier=tier,
        intent=intent,
        outline=outline,
        guidance=guidance,
        content_language=content_language,
    )
    section_contracts = build_section_contracts(blueprint)
    link_contracts = build_section_link_contracts(section_contracts)
    return {
        "article_blueprint": blueprint,
        "section_contracts": section_contracts,
        "section_link_contracts": link_contracts,
    }


def parse_brief_section_specs(brief_text: str) -> list[dict[str, Any]]:
    """Extract H2 headings, target words and bullets from an approved brief."""
    if not isinstance(brief_text, str) or not brief_text.strip():
        raise ContractValidationError("brief_text must be a non-empty string")
    outline_match = re.search(
        r"(?ms)^##\s+3\.\s+Recommended Outline.*?^```\s*$\n(.*?)^```\s*$",
        brief_text,
    )
    source = outline_match.group(1) if outline_match else brief_text
    matches = list(re.finditer(r"(?mi)^\s*H2:\s*(.+?)\s*$", source))
    numbered_matches: list[re.Match[str]] = []
    if not matches:
        outline_section = re.search(
            r"(?ms)^##\s+3\.\s+Recommended Outline\s*$\n(.*?)(?=^##\s+\d+\.|\Z)",
            brief_text,
        )
        numbered_source = outline_section.group(1) if outline_section else brief_text
        numbered_matches = list(
            re.finditer(
                r"(?mi)^\s*(?:\d+\.|[-*])\s+\*\*H2:\s*(.+?)\*\*"
                r"(?:\s*(?:—|–|-)\s*(.*?))?\s*$",
                numbered_source,
            )
        )
        source = numbered_source
    if not matches and not numbered_matches:
        raise ContractValidationError("brief contains no H2 outline entries")

    active_matches = matches or numbered_matches

    specs: list[dict[str, Any]] = []
    for index, match in enumerate(active_matches):
        raw_heading = _english_legacy_heading(match.group(1).strip())
        word_match = re.search(r"\((\d+)\s+words?\)\s*$", raw_heading, re.I)
        target_words = int(word_match.group(1)) if word_match else None
        heading = raw_heading[: word_match.start()].strip() if word_match else raw_heading
        end = active_matches[index + 1].start() if index + 1 < len(active_matches) else len(source)
        body = source[match.end() : end]
        bullets = (
            []
            if numbered_matches
            else [
                _text(item, "brief bullet")
                for item in re.findall(r"(?m)^\s*-\s+(.+?)\s*$", body)
                if item.strip()
            ]
        )
        if numbered_matches and match.lastindex and match.lastindex >= 2:
            trailing_note = _text(
                match.group(2) or "",
                "brief outline note",
                allow_empty=True,
            )
            if trailing_note:
                bullets.insert(0, trailing_note)
        specs.append(
            {
                "heading": _normalized_heading(heading),
                "target_words": target_words,
                "bullets": bullets,
            }
        )
    return specs


def _is_deferred_frame_heading(heading: str) -> bool:
    normalized = " ".join(re.findall(r"[a-z0-9]+", heading.casefold()))
    return normalized in _DEFERRED_FRAME_HEADINGS


def build_contract_bundle_from_brief(
    *,
    topic: str,
    tier: str,
    intent: str,
    brief_text: str,
    guidance: str = "",
    content_language: str = DEFAULT_CONTENT_LANGUAGE,
    approved_product_constraints: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Rebuild rich contracts without promoting brief prose into product rules.

    Product constraints are accepted only through the explicit structured
    ``approved_product_constraints`` argument. A Legacy brief may contain stale,
    speculative or site-incompatible recommendations, so its natural-language
    bullets remain writing requirements rather than catalog filters.
    """
    specs = parse_brief_section_specs(brief_text)
    body_specs = [item for item in specs if not _is_deferred_frame_heading(item["heading"])]
    if not body_specs:
        raise ContractValidationError("brief contains only deferred article-frame headings")
    bundle = build_contract_bundle(
        topic=topic,
        tier=tier,
        intent=intent,
        outline=[item["heading"] for item in body_specs],
        guidance=guidance,
        content_language=content_language,
    )
    by_heading = {_neutralized_heading(item["heading"]): item for item in body_specs}
    for section in bundle["section_contracts"]["sections"]:
        spec = by_heading[section["heading"]]
        bullets = spec["bullets"]
        if bullets:
            section["brief_points"] = [point for point in bullets if not _contains_cjk(point)]
            section["brief_points_rejected"] = [
                {
                    "text": point,
                    "reason_code": "non_english_brief_point",
                }
                for point in bullets
                if _contains_cjk(point)
            ]
            section["section_goal"] = (
                f"Answer {section['heading']} after validating the brief points "
                "against evidence and the site catalog."
            )
        target_words = spec["target_words"]
        if target_words:
            section["target_words"] = {
                "min": max(1, round(target_words * 0.75)),
                "max": round(target_words * 1.25),
            }
        if approved_product_constraints:
            constraint_key = (
                section["heading"]
                if section["heading"] in approved_product_constraints
                else spec["heading"]
                if spec["heading"] in approved_product_constraints
                else ""
            )
            if constraint_key:
                section["product_constraints"] = approved_product_constraints[constraint_key]
    validate_section_contracts(
        bundle["section_contracts"],
        bundle["article_blueprint"],
    )
    bundle["section_link_contracts"] = build_section_link_contracts(bundle["section_contracts"])
    return validate_contract_bundle(bundle)


def validate_article_blueprint(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ContractValidationError("article blueprint must be an object")
    if data.get("version") != CONTRACT_VERSION:
        raise ContractValidationError("article blueprint version must be 1")
    _text(data.get("topic"), "article_blueprint.topic")
    _text(data.get("tier"), "article_blueprint.tier", allow_empty=True)
    _text(data.get("intent"), "article_blueprint.intent", allow_empty=True)
    _text(data.get("core_task"), "article_blueprint.core_task")
    _text(data.get("guidance"), "article_blueprint.guidance", allow_empty=True)
    if data.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise ContractValidationError("article_blueprint.content_language must be en")
    order = data.get("section_order")
    sections = data.get("sections")
    if not isinstance(order, list) or not isinstance(sections, list) or not sections:
        raise ContractValidationError("article blueprint requires sections and section_order")
    ids: list[str] = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise ContractValidationError(f"article section {index} must be an object")
        section_id = _text(section.get("section_id"), f"article section {index}.section_id")
        heading = _text(section.get("heading"), f"article section {index}.heading")
        if section.get("position") != index:
            raise ContractValidationError(f"article section {index} position is unstable")
        if section_id != stable_section_id(index, heading):
            raise ContractValidationError(f"article section {index} has an unstable section_id")
        if section.get("reader_stage") not in READER_STAGES:
            raise ContractValidationError(f"article section {index} has invalid reader_stage")
        ids.append(section_id)
    if len(ids) != len(set(ids)) or order != ids:
        raise ContractValidationError("article section IDs/order must be unique and aligned")
    return data


def validate_section_contracts(
    data: Any,
    blueprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ContractValidationError("section contracts must be an object")
    if data.get("version") != CONTRACT_VERSION:
        raise ContractValidationError("section contracts version must be 1")
    topic = _text(data.get("topic"), "section_contracts.topic")
    if data.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise ContractValidationError("section_contracts.content_language must be en")
    sections = data.get("sections")
    order = data.get("section_order")
    if not isinstance(sections, list) or not sections or not isinstance(order, list):
        raise ContractValidationError("section contracts require sections and section_order")
    ids = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise ContractValidationError(f"section contract {index} must be an object")
        section_id = _text(section.get("section_id"), f"section contract {index}.section_id")
        if section.get("reader_stage") not in READER_STAGES:
            raise ContractValidationError(f"section contract {index} has invalid reader_stage")
        for field in ("reader_question", "section_goal", "previous_section", "next_section"):
            _text(
                section.get(field),
                f"section contract {index}.{field}",
                allow_empty=field in {"previous_section", "next_section"},
            )
        for field in (
            "must_answer",
            "brief_points",
            "must_not_repeat",
            "allowed_evidence_ids",
        ):
            values = section.get(field)
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ContractValidationError(
                    f"section contract {index}.{field} must be a list of strings"
                )
        rejected_points = section.get("brief_points_rejected")
        if not isinstance(rejected_points, list):
            raise ContractValidationError(
                f"section contract {index}.brief_points_rejected must be a list"
            )
        for rejected_point in rejected_points:
            if (
                not isinstance(rejected_point, dict)
                or not isinstance(rejected_point.get("text"), str)
                or not rejected_point["text"].strip()
                or rejected_point.get("reason_code") != "non_english_brief_point"
            ):
                raise ContractValidationError(
                    f"section contract {index}.brief_points_rejected is invalid"
                )
        target = section.get("target_words")
        if not isinstance(target, dict):
            raise ContractValidationError(
                f"section contract {index} target_words must be an object"
            )
        minimum = target.get("min")
        maximum = target.get("max")
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or minimum < 1
            or maximum < minimum
        ):
            raise ContractValidationError(f"section contract {index} has invalid target_words")
        if not isinstance(section.get("product_link_allowed"), bool):
            raise ContractValidationError(
                f"section contract {index} product_link_allowed must be bool"
            )
        _text(
            section.get("product_link_policy_reason"),
            f"section contract {index}.product_link_policy_reason",
        )
        product_constraints = section.get("product_constraints")
        if not isinstance(product_constraints, list):
            raise ContractValidationError(
                f"section contract {index}.product_constraints must be a list"
            )
        for constraint_index, constraint in enumerate(product_constraints, start=1):
            field = f"section contract {index}.product_constraints[{constraint_index}]"
            if not isinstance(constraint, dict):
                raise ContractValidationError(f"{field} must be an object")
            _text(constraint.get("field"), f"{field}.field")
            operator = constraint.get("operator")
            if operator not in PRODUCT_CONSTRAINT_OPERATORS:
                raise ContractValidationError(f"{field}.operator is invalid")
            source = constraint.get("source")
            if source not in PRODUCT_CONSTRAINT_SOURCES:
                raise ContractValidationError(f"{field}.source is not approved")
            unit = constraint.get("unit", "")
            _text(unit, f"{field}.unit", allow_empty=True)
            _text(constraint.get("reason", ""), f"{field}.reason", allow_empty=True)
            if operator != "exists" and "value" not in constraint:
                raise ContractValidationError(f"{field}.value is required")
            value = constraint.get("value")
            if operator in {"lte", "gte"} and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ContractValidationError(f"{field}.value must be numeric")
            if operator == "one_of" and (not isinstance(value, list) or not value):
                raise ContractValidationError(f"{field}.value must be a non-empty list")
        ids.append(section_id)
    if len(ids) != len(set(ids)) or order != ids:
        raise ContractValidationError("section contract IDs/order must be unique and aligned")
    if blueprint is not None:
        validated_blueprint = validate_article_blueprint(blueprint)
        if topic != validated_blueprint["topic"]:
            raise ContractValidationError("section contracts topic does not match blueprint")
        if data["content_language"] != validated_blueprint["content_language"]:
            raise ContractValidationError("section contracts language does not match blueprint")
        if order != validated_blueprint["section_order"]:
            raise ContractValidationError("section contracts do not match article blueprint")
    return data


def _validate_link_gate(gate: Any, field: str) -> None:
    if not isinstance(gate, dict):
        raise ContractValidationError(f"{field} must be an object")
    state = gate.get("opportunity_state")
    if state not in OPPORTUNITY_STATES:
        raise ContractValidationError(f"{field} has invalid opportunity_state")
    candidate_count = gate.get("candidate_count")
    maximum = gate.get("max_allowed")
    minimum = gate.get("min_required")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 0
    ):
        raise ContractValidationError(f"{field}.candidate_count must be >= 0")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ContractValidationError(f"{field}.max_allowed must be >= 0")
    if state == "unassessed":
        if minimum is not None or gate.get("reason_code") != "phase2_pending":
            raise ContractValidationError(
                f"{field} unassessed state must use min_required=null and phase2_pending"
            )
    else:
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ContractValidationError(f"{field}.min_required must be >= 0")
        if minimum > maximum:
            raise ContractValidationError(f"{field}.min_required exceeds max_allowed")
        if state == "required" and (minimum < 1 or candidate_count < 1):
            raise ContractValidationError(
                f"{field} required state needs a candidate and min_required >= 1"
            )
        if state in {"recommended", "none"} and minimum != 0:
            raise ContractValidationError(f"{field} {state} state must use min_required=0")
        _text(gate.get("reason_code"), f"{field}.reason_code")
    selected_ids = gate.get("selected_ids")
    rejected = gate.get("rejected")
    if not isinstance(selected_ids, list) or not isinstance(rejected, list):
        raise ContractValidationError(f"{field} decisions must be lists")
    if any(not isinstance(item, str) or not item for item in selected_ids):
        raise ContractValidationError(f"{field}.selected_ids must contain non-empty strings")
    if len(selected_ids) != len(set(selected_ids)):
        raise ContractValidationError(f"{field}.selected_ids must be unique")
    if any(not isinstance(item, dict) for item in rejected):
        raise ContractValidationError(f"{field}.rejected must contain objects")
    if state == "unassessed":
        if selected_ids or rejected or candidate_count != 0:
            raise ContractValidationError(
                f"{field} unassessed state cannot contain phase2 decisions"
            )
    else:
        if candidate_count != len(selected_ids):
            raise ContractValidationError(f"{field}.candidate_count must equal selected_ids length")
        if candidate_count > maximum:
            raise ContractValidationError(f"{field}.candidate_count exceeds max_allowed")
        if state == "none" and (candidate_count or selected_ids or maximum != 0):
            raise ContractValidationError(
                f"{field} none state must expose zero approved candidates"
            )


def validate_section_link_contracts(
    data: Any,
    section_contracts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ContractValidationError("section link contracts must be an object")
    if data.get("version") != CONTRACT_VERSION:
        raise ContractValidationError("section link contracts version must be 1")
    topic = _text(data.get("topic"), "section_link_contracts.topic")
    if data.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise ContractValidationError("section_link_contracts.content_language must be en")
    sections = data.get("sections")
    order = data.get("section_order")
    if not isinstance(sections, list) or not sections or not isinstance(order, list):
        raise ContractValidationError("section link contracts require sections and order")
    ids = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise ContractValidationError(f"link contract {index} must be an object")
        section_id = _text(section.get("section_id"), f"link contract {index}.section_id")
        ids.append(section_id)
        for link_type in ("article_links", "product_links", "external_citations"):
            _validate_link_gate(section.get(link_type), f"link contract {index}.{link_type}")
    if len(ids) != len(set(ids)) or order != ids:
        raise ContractValidationError("link contract IDs/order must be unique and aligned")
    if section_contracts is not None:
        validated_sections = validate_section_contracts(section_contracts)
        if topic != validated_sections["topic"]:
            raise ContractValidationError("link contracts topic does not match section contracts")
        if data["content_language"] != validated_sections["content_language"]:
            raise ContractValidationError(
                "link contracts language does not match section contracts"
            )
        if order != validated_sections["section_order"]:
            raise ContractValidationError("link contracts do not match section contracts")
    return data


def validate_contract_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ContractValidationError("contract bundle must be an object")
    blueprint = validate_article_blueprint(bundle.get("article_blueprint"))
    sections = validate_section_contracts(bundle.get("section_contracts"), blueprint)
    validate_section_link_contracts(bundle.get("section_link_contracts"), sections)
    return bundle


def contract_paths(workspace: Path, slug: str) -> dict[str, Path]:
    clean_slug = _text(slug, "slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean_slug):
        raise ContractValidationError("slug must contain lowercase letters, numbers, and hyphens")
    research = Path(workspace) / "research"
    return {
        "article_blueprint": research / f"article-blueprint-{clean_slug}.json",
        "section_contracts": research / f"section-contracts-{clean_slug}.json",
        "section_link_contracts": research / f"section-link-contracts-{clean_slug}.json",
    }


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def persist_contract_bundle(
    workspace: Path,
    slug: str,
    bundle: dict[str, Any],
) -> dict[str, str]:
    """Persist the three shadow contract files with rollback on partial failure."""
    validated = validate_contract_bundle(bundle)
    paths = contract_paths(workspace, slug)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    snapshots = {name: path.read_bytes() if path.exists() else None for name, path in paths.items()}
    temp_paths: dict[str, Path] = {}
    try:
        for name, path in paths.items():
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(temp_name)
            temp_paths[name] = temp_path
            with os.fdopen(fd, "wb") as handle:
                handle.write(_json_bytes(validated[name]))
                handle.flush()
                os.fsync(handle.fileno())
        for name, path in paths.items():
            os.replace(temp_paths[name], path)
        return {name: str(path) for name, path in paths.items()}
    except Exception:
        for name, path in paths.items():
            snapshot = snapshots[name]
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(snapshot)
        raise
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


def load_contract_bundle(workspace: Path, slug: str) -> dict[str, Any]:
    paths = contract_paths(workspace, slug)
    loaded: dict[str, Any] = {}
    for name, path in paths.items():
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractValidationError(f"cannot load {name}: {exc}") from None
    return validate_contract_bundle(loaded)
