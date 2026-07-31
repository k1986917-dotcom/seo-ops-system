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
READER_STAGES = frozenset({
    "discover",
    "understand",
    "compare",
    "select",
    "apply",
    "verify",
})
OPPORTUNITY_STATES = frozenset({"unassessed", "required", "recommended", "none"})

_VERIFY_TERMS = frozenset({
    "accident",
    "compliance",
    "danger",
    "injury",
    "law",
    "legal",
    "regulation",
    "safety",
    "warning",
})
_COMPARE_TERMS = frozenset({"compare", "comparison", "versus", "vs"})
_SELECT_TERMS = frozenset({"best", "choose", "choosing", "selection", "which"})
_APPLY_TERMS = frozenset({"apply", "install", "mount", "setup", "use", "using"})


class ContractValidationError(ValueError):
    """Raised when a sectional writing contract is malformed."""


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(
            f"{field} must be a str, got {type(value).__name__}"
        )
    result = " ".join(value.split())
    if not allow_empty and not result:
        raise ContractValidationError(f"{field} must not be empty")
    return result


def _normalized_heading(heading: str) -> str:
    value = re.sub(r"^\s*(?:#{1,6}\s*|H2:\s*)", "", heading, flags=re.IGNORECASE)
    return _text(value, "heading")


def stable_section_id(position: int, heading: str) -> str:
    """Return a stable section ID that survives harmless outline reordering."""
    if isinstance(position, bool) or not isinstance(position, int) or position < 1:
        raise ContractValidationError("position must be an integer >= 1")
    normalized = _normalized_heading(heading).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"section-{digest}"


def infer_reader_stage(heading: str, position: int) -> str:
    """Conservatively infer a reader stage from a planned H2 heading."""
    tokens = set(re.findall(r"[a-z0-9]+", _normalized_heading(heading).lower()))
    if tokens & _VERIFY_TERMS:
        return "verify"
    if tokens & _COMPARE_TERMS:
        return "compare"
    if tokens & _SELECT_TERMS:
        return "select"
    if tokens & _APPLY_TERMS:
        return "apply"
    if position == 1:
        return "discover"
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
) -> dict[str, Any]:
    """Build a stable article blueprint from an approved ordered outline."""
    clean_topic = _text(topic, "topic")
    clean_tier = _text(tier, "tier", allow_empty=True)
    clean_intent = _text(intent, "intent", allow_empty=True)
    clean_guidance = _text(guidance, "guidance", allow_empty=True)
    if not isinstance(outline, list) or not outline:
        raise ContractValidationError("outline must be a non-empty list")

    sections: list[dict[str, Any]] = []
    seen_headings: set[str] = set()
    for position, raw_heading in enumerate(outline, start=1):
        heading = _normalized_heading(raw_heading)
        heading_key = heading.casefold()
        if heading_key in seen_headings:
            raise ContractValidationError(f"duplicate outline heading: {heading}")
        seen_headings.add(heading_key)
        section_id = stable_section_id(position, heading)
        sections.append({
            "section_id": section_id,
            "position": position,
            "heading": heading,
            "reader_stage": infer_reader_stage(heading, position),
        })

    blueprint = {
        "version": CONTRACT_VERSION,
        "topic": clean_topic,
        "tier": clean_tier,
        "intent": clean_intent,
        "core_task": clean_intent or clean_topic,
        "guidance": clean_guidance,
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
            source_sections[index + 1]["heading"]
            if index + 1 < len(source_sections)
            else ""
        )
        stage = source["reader_stage"]
        product_allowed, product_reason = _product_link_policy(stage)
        sections.append({
            **source,
            "reader_question": source["heading"],
            "section_goal": f"Give a complete, useful answer about {source['heading']}.",
            "must_answer": [source["heading"]],
            "must_not_repeat": (
                [f"Do not repeat the full explanation from {previous_heading}."]
                if previous_heading
                else []
            ),
            "previous_section": previous_heading,
            "next_section": next_heading,
            "target_words": {"min": 220, "max": 360},
            "allowed_evidence_ids": [],
            "product_link_allowed": product_allowed,
            "product_link_policy_reason": product_reason,
        })
    contracts = {
        "version": CONTRACT_VERSION,
        "topic": validated["topic"],
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
        sections.append({
            "section_id": section["section_id"],
            "article_links": _unassessed_link_gate(2),
            "product_links": product_gate,
            "external_citations": _unassessed_link_gate(3),
        })
    contracts = {
        "version": CONTRACT_VERSION,
        "topic": validated["topic"],
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
) -> dict[str, Any]:
    blueprint = build_article_blueprint(
        topic=topic,
        tier=tier,
        intent=intent,
        outline=outline,
        guidance=guidance,
    )
    section_contracts = build_section_contracts(blueprint)
    link_contracts = build_section_link_contracts(section_contracts)
    return {
        "article_blueprint": blueprint,
        "section_contracts": section_contracts,
        "section_link_contracts": link_contracts,
    }


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
        for field in ("must_answer", "must_not_repeat", "allowed_evidence_ids"):
            values = section.get(field)
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ContractValidationError(
                    f"section contract {index}.{field} must be a list of strings"
                )
        target = section.get("target_words")
        if not isinstance(target, dict):
            raise ContractValidationError(f"section contract {index} target_words must be an object")
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
        ids.append(section_id)
    if len(ids) != len(set(ids)) or order != ids:
        raise ContractValidationError("section contract IDs/order must be unique and aligned")
    if blueprint is not None:
        validated_blueprint = validate_article_blueprint(blueprint)
        if topic != validated_blueprint["topic"]:
            raise ContractValidationError("section contracts topic does not match blueprint")
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
    if not isinstance(gate.get("selected_ids"), list) or not isinstance(
        gate.get("rejected"), list
    ):
        raise ContractValidationError(f"{field} decisions must be lists")


def validate_section_link_contracts(
    data: Any,
    section_contracts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ContractValidationError("section link contracts must be an object")
    if data.get("version") != CONTRACT_VERSION:
        raise ContractValidationError("section link contracts version must be 1")
    topic = _text(data.get("topic"), "section_link_contracts.topic")
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

    snapshots = {
        name: path.read_bytes() if path.exists() else None
        for name, path in paths.items()
    }
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
