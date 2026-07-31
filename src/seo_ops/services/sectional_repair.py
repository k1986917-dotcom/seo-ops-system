"""Shadow-only section-scoped W1b/W2 repair orchestration.

The module converts structured pre-check/post-process failures into a bounded
repair plan, regenerates only affected body sections (or their link markup),
rebuilds the final delivery, remaps unchanged claims to new global sentence
IDs, and re-runs the deterministic Phase 5 assembly gates.

It deliberately does not import or replace the formal Legacy W1b/W2 routes.
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

from data_sources.modules import seo_common
from seo_ops.services.sectional_assembly import (
    SectionAssemblyError,
    assemble_sectional_article,
    validate_assembly_metadata,
    validate_sectional_assembly,
)
from seo_ops.services.sectional_delivery import (
    SectionDeliveryError,
    build_section_claim_package,
    generate_section_claim_output,
    merge_section_claim_ledgers,
    resolve_section_placeholders,
    validate_resolved_delivery,
    validate_section_claim_output,
)
from seo_ops.services.sectional_generation import (
    SECTION_DECISIONS_MARKER,
    SECTION_MARKDOWN_MARKER,
    SectionGenerationError,
    build_article_frame_package,
    build_article_frame_prompt,
    build_section_generation_package,
    parse_article_frame_response,
    parse_section_generation_response,
    validate_article_frame_output,
    validate_section_generation_output,
    validate_section_generation_package,
)
from seo_ops.services.sectional_writing import (
    CONTRACT_VERSION,
    DEFAULT_CONTENT_LANGUAGE,
    ContractValidationError,
    validate_section_contracts,
    validate_section_link_contracts,
)

MAX_REPAIR_ROUNDS = 2
REPAIR_VERSION = 1

_SHA256 = re.compile(r"[a-f0-9]{64}")
_SECTION_ID = re.compile(r"section-[a-f0-9]{10}")
_SENTENCE_ID = re.compile(r"S\d{3,}")
_URL = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
_FRAME_IDS = {
    "frame-introduction",
    "frame-takeaways",
    "frame-conclusion",
    "frame-faq",
}
_ACTION_PRIORITY = {
    "ledger_repair": 1,
    "link_repair": 2,
    "section_rewrite": 3,
    "frame_rewrite": 4,
}
_REPORT_KEYS = {
    "error",
    "item",
    "detail",
    "reason",
    "reason_code",
    "sentence",
    "sentence_id",
    "section_id",
    "url",
    "failure_type",
}
_NESTED_FAILURE_KEYS = {
    "checks",
    "failed",
    "failures",
    "fact_issues",
    "missing_entities",
    "link_issues",
    "fix_items",
    "issues",
}


class SectionRepairError(ContractValidationError):
    """Raised when a repair plan or bounded repair run is unsafe."""


def _clean_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SectionRepairError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not allow_empty and not cleaned:
        raise SectionRepairError(f"{field} must not be empty")
    return cleaned


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _markdown_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_unit_id(value: str) -> bool:
    return bool(_SECTION_ID.fullmatch(value) or value in _FRAME_IDS)


def _normalize_issue_text(value: str) -> str:
    return seo_common.normalize_claim_text(value).casefold()


def _record_from_mapping(value: dict[str, Any], source: str) -> dict[str, Any] | None:
    record: dict[str, Any] = {"source": source}
    for key in _REPORT_KEYS:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            record[key] = " ".join(item.split())
    if len(record) == 1:
        return None
    record["raw"] = {
        key: value[key]
        for key in sorted(value)
        if key in _REPORT_KEYS and isinstance(value[key], (str, int, float, bool))
    }
    return record


def _collect_issue_records(
    payload: Any,
    *,
    source: str,
    inherited_item: str = "",
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if payload is None:
        return records
    if isinstance(payload, str):
        for line in payload.splitlines():
            cleaned = line.strip(" -*\t")
            if cleaned:
                records.append({
                    "source": source,
                    "item": inherited_item or source,
                    "detail": cleaned,
                    "raw": cleaned,
                })
        return records
    if isinstance(payload, list):
        for item in payload:
            records.extend(
                _collect_issue_records(
                    item,
                    source=source,
                    inherited_item=inherited_item,
                )
            )
        return records
    if not isinstance(payload, dict):
        return records

    if payload.get("pass") is True or payload.get("level") == "ok":
        return records

    nested_keys_present = any(key in payload for key in _NESTED_FAILURE_KEYS)
    has_locator = any(
        isinstance(payload.get(key), str) and payload.get(key, "").strip()
        for key in ("sentence_id", "sentence", "section_id", "url")
    )
    own = (
        _record_from_mapping(payload, source)
        if has_locator or not nested_keys_present
        else None
    )
    if own is not None:
        if inherited_item and "item" not in own:
            own["item"] = inherited_item
        records.append(own)
    nested_item = str(payload.get("item") or inherited_item or source)
    for key in _NESTED_FAILURE_KEYS:
        if key not in payload:
            continue
        nested = payload[key]
        if key == "checks" and isinstance(nested, list):
            nested = [
                item
                for item in nested
                if not isinstance(item, dict)
                or item.get("pass") is False
                or item.get("level") == "fail"
            ]
        records.extend(
            _collect_issue_records(
                nested,
                source=source,
                inherited_item=nested_item,
            )
        )
    return records


def normalize_failure_reports(
    *,
    precheck_report: Any = None,
    postprocess_report: Any = None,
) -> list[dict[str, Any]]:
    """Return deterministic, deduplicated issue records from W1b/W2 payloads."""
    records = [
        *_collect_issue_records(precheck_report, source="w1b"),
        *_collect_issue_records(postprocess_report, source="w2"),
    ]
    if isinstance(postprocess_report, dict):
        boolean_failures = (
            ("score_error", True),
            ("cannibal_error", True),
            ("cannibal_block", True),
            ("score_block", not bool(postprocess_report.get("fix_items"))),
            ("link_block", not bool(postprocess_report.get("link_issues"))),
        )
        for field, should_add in boolean_failures:
            if should_add and postprocess_report.get(field) is True:
                records.append({
                    "source": "w2",
                    "item": field,
                    "detail": field,
                    "reason_code": field,
                    "raw": {field: True},
                })
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = _json_digest({key: record.get(key) for key in sorted(record) if key != "raw"})
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(record)
    return deduplicated


def _delivery_indexes(delivery: dict[str, Any]) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    by_sentence_id = {
        item["sentence_id"]: item["section_id"]
        for item in resolved["sentences"]
    }
    by_sentence_norm: dict[str, set[str]] = {}
    for item in resolved["sentences"]:
        by_sentence_norm.setdefault(item["norm"].casefold(), set()).add(item["section_id"])
    by_heading = {
        item["heading"].casefold(): item["section_id"]
        for item in resolved["sections"]
    }
    by_url: dict[str, set[str]] = {}
    for binding in resolved["bindings"]:
        by_url.setdefault(binding["url"], set()).add(binding["section_id"])
    return {
        "delivery": resolved,
        "by_sentence_id": by_sentence_id,
        "by_sentence_norm": by_sentence_norm,
        "by_heading": by_heading,
        "by_url": by_url,
    }


def _issue_text(issue: dict[str, Any]) -> str:
    return " ".join(
        str(issue.get(key) or "")
        for key in (
            "item",
            "detail",
            "reason",
            "reason_code",
            "failure_type",
            "sentence",
            "url",
        )
    ).strip()


def _map_issue_to_units(
    issue: dict[str, Any],
    indexes: dict[str, Any],
) -> list[str]:
    resolved = indexes["delivery"]
    mapped: set[str] = set()
    direct = issue.get("section_id")
    if isinstance(direct, str) and direct in resolved["section_order"]:
        mapped.add(direct)
    sentence_id = issue.get("sentence_id")
    if isinstance(sentence_id, str):
        section_id = indexes["by_sentence_id"].get(sentence_id)
        if section_id:
            mapped.add(section_id)
    detail = _issue_text(issue)
    for matched_id in _SENTENCE_ID.findall(detail):
        section_id = indexes["by_sentence_id"].get(matched_id)
        if section_id:
            mapped.add(section_id)
    sentence = issue.get("sentence")
    if isinstance(sentence, str) and sentence.strip():
        units = indexes["by_sentence_norm"].get(_normalize_issue_text(sentence), set())
        if len(units) == 1:
            mapped.update(units)
    for url in _URL.findall(detail):
        mapped.update(indexes["by_url"].get(url.rstrip(".,;:"), set()))
    detail_lower = detail.casefold()
    frame_aliases = {
        "frame-introduction": ("introduction", "intro", "h1"),
        "frame-takeaways": ("key takeaways", "takeaways"),
        "frame-conclusion": ("conclusion",),
        "frame-faq": ("frequently asked questions", "faq"),
    }
    for unit_id, aliases in frame_aliases.items():
        if any(
            re.search(rf"\b{re.escape(alias)}\b", detail_lower)
            if alias in {"intro", "h1", "faq"}
            else alias in detail_lower
            for alias in aliases
        ):
            mapped.add(unit_id)
    for heading, section_id in indexes["by_heading"].items():
        if heading and heading in detail_lower:
            mapped.add(section_id)
    return [section_id for section_id in resolved["section_order"] if section_id in mapped]


def _classify_issue(issue: dict[str, Any], mapped_units: list[str]) -> str:
    text = _issue_text(issue).casefold()
    reason = str(issue.get("reason") or issue.get("reason_code") or "").casefold()
    if any(
        token in text
        for token in (
            "score_error",
            "score_block",
            "评分失败",
            "cannibal",
            "cannibal_block",
            "cannibal_error",
            "蚕食",
            "provider failed",
            "database",
            "系统错误",
            "traceback",
        )
    ):
        return "global_blocker"
    if reason == "uncovered_factual_sentence" or any(
        token in text
        for token in (
            "claim ledger",
            "claim-ledger",
            "missing claim",
            "uncovered factual sentence",
            "事实句未覆盖",
        )
    ):
        return "ledger_repair"
    if any(
        token in text
        for token in (
            "link",
            "anchor",
            "url",
            "内链",
            "外链",
            "产品链接",
            "链接问题",
        )
    ):
        return "link_repair"
    if any(unit_id in _FRAME_IDS for unit_id in mapped_units):
        return "frame_rewrite"
    if any(
        token in text
        for token in (
            "word count",
            "字数",
            "paragraph",
            "段落",
            "unsupported",
            "fact",
            "事实",
            "missing entit",
            "实体",
            "keyword",
            "关键词",
            "h1",
            "h2",
            "heading",
            "readability",
            "structure",
            "cta",
        )
    ):
        return "section_rewrite"
    return "global_blocker"


def build_section_repair_plan(
    delivery: dict[str, Any],
    *,
    precheck_report: Any = None,
    postprocess_report: Any = None,
    round_number: int = 1,
) -> dict[str, Any]:
    """Map W1b/W2 failures to bounded body/frame repair actions."""
    resolved = validate_resolved_delivery(delivery)
    if not isinstance(round_number, int) or not 1 <= round_number <= MAX_REPAIR_ROUNDS:
        raise SectionRepairError(
            f"round_number must be between 1 and {MAX_REPAIR_ROUNDS}"
        )
    issues = normalize_failure_reports(
        precheck_report=precheck_report,
        postprocess_report=postprocess_report,
    )
    if not issues:
        raise SectionRepairError("failure reports contain no actionable issues")
    indexes = _delivery_indexes(resolved)
    action_by_unit: dict[str, dict[str, Any]] = {}
    global_blockers: list[dict[str, Any]] = []
    mapped_issues: list[dict[str, Any]] = []
    for issue in issues:
        units = _map_issue_to_units(issue, indexes)
        action = _classify_issue(issue, units)
        canonical = {
            "source": issue["source"],
            "item": str(issue.get("item") or issue["source"]),
            "detail": _issue_text(issue),
            "reason_code": str(
                issue.get("reason_code") or issue.get("reason") or "reported_failure"
            ),
            "mapped_units": units,
            "action": action,
        }
        mapped_issues.append(canonical)
        if action == "global_blocker" or not units:
            global_blockers.append(canonical)
            continue
        for unit_id in units:
            unit_action = action
            if unit_id in _FRAME_IDS and action != "ledger_repair":
                unit_action = "frame_rewrite"
            current = action_by_unit.get(unit_id)
            if current is None:
                action_by_unit[unit_id] = {
                    "section_id": unit_id,
                    "action": unit_action,
                    "issues": [canonical],
                }
            else:
                if _ACTION_PRIORITY[unit_action] > _ACTION_PRIORITY[current["action"]]:
                    current["action"] = unit_action
                current["issues"].append(canonical)
    actions = [
        action_by_unit[section_id]
        for section_id in resolved["section_order"]
        if section_id in action_by_unit
    ]
    frame_refresh = any(
        item["action"] in {"section_rewrite", "frame_rewrite"}
        for item in actions
    )
    plan = {
        "version": REPAIR_VERSION,
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "topic": resolved["topic"],
        "round_number": round_number,
        "delivery_sha256": resolved["delivery_sha256"],
        "draft_sha256": resolved["draft_sha256"],
        "actions": actions,
        "frame_refresh": frame_refresh,
        "global_blockers": global_blockers,
        "issues": mapped_issues,
    }
    plan["plan_sha256"] = _json_digest(plan)
    return validate_section_repair_plan(plan, resolved)


def validate_section_repair_plan(
    plan: Any,
    delivery: dict[str, Any],
) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    if not isinstance(plan, dict) or plan.get("version") != REPAIR_VERSION:
        raise SectionRepairError("repair plan must be a version 1 object")
    if plan.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionRepairError("repair plan language must be en")
    if plan.get("topic") != resolved["topic"]:
        raise SectionRepairError("repair plan topic mismatch")
    if plan.get("delivery_sha256") != resolved["delivery_sha256"]:
        raise SectionRepairError("repair plan delivery SHA mismatch")
    if plan.get("draft_sha256") != resolved["draft_sha256"]:
        raise SectionRepairError("repair plan draft SHA mismatch")
    round_number = plan.get("round_number")
    if not isinstance(round_number, int) or not 1 <= round_number <= MAX_REPAIR_ROUNDS:
        raise SectionRepairError("repair plan round_number is invalid")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise SectionRepairError("repair plan actions must be a list")
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {
            "section_id",
            "action",
            "issues",
        }:
            raise SectionRepairError("repair action shape is invalid")
        section_id = _clean_text(action["section_id"], "repair section_id")
        if section_id not in resolved["section_order"] or section_id in seen:
            raise SectionRepairError("repair action section_id is invalid or duplicated")
        seen.add(section_id)
        expected_actions = (
            {"frame_rewrite", "ledger_repair"}
            if section_id in _FRAME_IDS
            else {"section_rewrite", "link_repair", "ledger_repair"}
        )
        if action["action"] not in expected_actions:
            raise SectionRepairError("repair action is invalid for the target unit")
        if not isinstance(action["issues"], list) or not action["issues"]:
            raise SectionRepairError("repair action needs at least one issue")
    blockers = plan.get("global_blockers")
    issues = plan.get("issues")
    if not isinstance(blockers, list) or not isinstance(issues, list):
        raise SectionRepairError("repair plan issues/blockers are invalid")
    expected_refresh = any(
        item["action"] in {"section_rewrite", "frame_rewrite"}
        for item in actions
    )
    if plan.get("frame_refresh") is not expected_refresh:
        raise SectionRepairError("repair plan frame_refresh is inconsistent")
    unsigned = dict(plan)
    digest = unsigned.pop("plan_sha256", None)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise SectionRepairError("repair plan SHA is invalid")
    if digest != _json_digest(unsigned):
        raise SectionRepairError("repair plan SHA mismatch")
    return plan


def _section_output_map(section_run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outputs = section_run.get("outputs")
    order = section_run.get("section_order")
    if not isinstance(outputs, list) or not isinstance(order, list):
        raise SectionRepairError("section_run outputs/order are invalid")
    if [item.get("section_id") for item in outputs if isinstance(item, dict)] != order:
        raise SectionRepairError("section_run outputs are incomplete or out of order")
    return {item["section_id"]: item for item in outputs}


def _previous_summary(
    section_id: str,
    section_order: list[str],
    output_by_id: dict[str, dict[str, Any]],
) -> str:
    index = section_order.index(section_id)
    if index == 0:
        return ""
    return str(output_by_id[section_order[index - 1]].get("summary") or "")


def build_section_repair_package(
    *,
    section_contracts: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    section_run: dict[str, Any],
    current_section_run: dict[str, Any] | None = None,
    plan: dict[str, Any],
    section_id: str,
) -> dict[str, Any]:
    sections = validate_section_contracts(section_contracts)
    links = validate_section_link_contracts(link_contracts, sections)
    output_by_id = _section_output_map(section_run)
    current_output_by_id = _section_output_map(current_section_run or section_run)
    clean_id = _clean_text(section_id, "section_id")
    if clean_id not in output_by_id or clean_id not in current_output_by_id:
        raise SectionRepairError("repair section is missing from section_run")
    action = next(
        (item for item in plan["actions"] if item["section_id"] == clean_id),
        None,
    )
    if action is None or action["action"] not in {"section_rewrite", "link_repair"}:
        raise SectionRepairError("section does not have a body repair action")
    base = build_section_generation_package(
        sections,
        links,
        context_manifest,
        clean_id,
        previous_summary=_previous_summary(
            clean_id,
            sections["section_order"],
            output_by_id,
        ),
    )
    current_base = build_section_generation_package(
        sections,
        links,
        context_manifest,
        clean_id,
        previous_summary=_previous_summary(
            clean_id,
            sections["section_order"],
            current_output_by_id,
        ),
    )
    current = validate_section_generation_output(
        current_output_by_id[clean_id],
        current_base,
    )
    package = {
        "version": REPAIR_VERSION,
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "topic": sections["topic"],
        "repair_mode": action["action"],
        "round_number": plan["round_number"],
        "plan_sha256": plan["plan_sha256"],
        "section_id": clean_id,
        "generation_package": base,
        "current_generation_package": current_base,
        "current_output": current,
        "failures": action["issues"],
    }
    package["package_sha256"] = _json_digest(package)
    return validate_section_repair_package(package)


def validate_section_repair_package(package: Any) -> dict[str, Any]:
    if not isinstance(package, dict) or package.get("version") != REPAIR_VERSION:
        raise SectionRepairError("repair package must be a version 1 object")
    if package.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionRepairError("repair package language must be en")
    if package.get("repair_mode") not in {"section_rewrite", "link_repair"}:
        raise SectionRepairError("repair package mode is invalid")
    section_id = _clean_text(package.get("section_id"), "repair package section_id")
    if not _SECTION_ID.fullmatch(section_id):
        raise SectionRepairError("repair package section_id is invalid")
    base = package.get("generation_package")
    current_base = package.get("current_generation_package")
    try:
        base = validate_section_generation_package(base)
        current_base = validate_section_generation_package(current_base)
    except SectionGenerationError as exc:
        raise SectionRepairError(f"repair generation package is invalid: {exc}") from exc
    if base.get("section_id") != section_id or current_base.get("section_id") != section_id:
        raise SectionRepairError("repair package generation package section mismatch")
    if package.get("topic") != base.get("topic") or package.get("topic") != current_base.get("topic"):
        raise SectionRepairError("repair package topic does not match generation packages")
    current = package.get("current_output")
    try:
        validate_section_generation_output(current, current_base)
    except SectionGenerationError as exc:
        raise SectionRepairError(f"repair current output is invalid: {exc}") from exc
    failures = package.get("failures")
    if not isinstance(failures, list) or not failures:
        raise SectionRepairError("repair package failures are missing")
    for field in ("plan_sha256", "package_sha256"):
        value = package.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise SectionRepairError(f"repair package {field} is invalid")
    unsigned = dict(package)
    digest = unsigned.pop("package_sha256")
    if digest != _json_digest(unsigned):
        raise SectionRepairError("repair package SHA mismatch")
    return package


def build_section_repair_prompt(package: dict[str, Any]) -> dict[str, str]:
    validated = validate_section_repair_package(package)
    mode = validated["repair_mode"]
    if mode == "link_repair":
        task = """Repair only ARTICLE/PRODUCT/CITE placeholders and the matching
link-decisions JSON. Preserve every reader-visible word, heading, paragraph,
punctuation mark, and paragraph boundary exactly. You may wrap an existing
phrase in an approved placeholder, remove a placeholder, or move a citation
without changing visible prose. Never add a raw URL."""
    else:
        task = """Rewrite only this approved H2 to fix every listed failure.
Keep the exact H2, stay within the supplied word range, use 2-5 coherent
paragraphs, and use only approved candidates/evidence. Do not change any other
article section or invent product fit, compliance, measurements, or URLs."""
    system = f"""You are repairing one section of an English article.
{task}
Return exactly two blocks:
{SECTION_MARKDOWN_MARKER}
<complete repaired H2 Markdown>
{SECTION_DECISIONS_MARKER}
<one JSON object containing article_links, product_links, external_citations>
The result must be a complete replacement, not a patch or explanation."""
    user = "SECTION REPAIR PACKAGE\n" + json.dumps(
        validated,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {"system": system, "user": user}


def _visible_section_text(markdown: str) -> str:
    value = re.sub(
        r"\[\[ARTICLE:[^|\]]+\|([^\]]+)\]\]",
        r"\1",
        markdown,
    )
    value = re.sub(
        r"\[\[PRODUCT:[^|\]]+\|([^\]]+)\]\]",
        r"\1",
        value,
    )
    value = re.sub(r"\[\[CITE:[^\]]+\]\]", "", value)
    return re.sub(r"[ \t]+", " ", value).strip()


def parse_section_repair_response(
    response_text: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_section_repair_package(package)
    try:
        output = parse_section_generation_response(
            response_text,
            validated["generation_package"],
        )
    except SectionGenerationError as exc:
        raise SectionRepairError(f"repaired section is invalid: {exc}") from exc
    return validate_section_repair_output(output, validated)


def validate_section_repair_output(
    output: Any,
    package: dict[str, Any],
) -> dict[str, Any]:
    """Validate one repair output, including link-only visible-text invariants."""
    validated = validate_section_repair_package(package)
    try:
        canonical = validate_section_generation_output(
            output,
            validated["generation_package"],
        )
    except SectionGenerationError as exc:
        raise SectionRepairError(f"repaired section is invalid: {exc}") from exc
    if validated["repair_mode"] == "link_repair":
        before = _visible_section_text(validated["current_output"]["markdown"])
        after = _visible_section_text(canonical["markdown"])
        if before != after:
            raise SectionRepairError(
                "link repair changed reader-visible text instead of only link markup"
            )
    return canonical


def _repair_checkpoint_path(
    workspace: Path,
    slug: str,
    round_number: int,
    section_id: str,
) -> Path:
    clean_slug = _clean_text(slug, "slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean_slug):
        raise SectionRepairError("slug format is invalid")
    if not _SECTION_ID.fullmatch(section_id):
        raise SectionRepairError("repair checkpoint section_id is invalid")
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / "repair-checkpoints"
        / f"round-{round_number}-{section_id}.json"
    )


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = path.read_bytes() if path.exists() else None
    temp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(name)
        payload = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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


def _load_repair_checkpoint(
    path: Path,
    package: dict[str, Any],
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != REPAIR_VERSION
        or data.get("package_sha256") != package["package_sha256"]
    ):
        return None
    output = data.get("output")
    try:
        return validate_section_repair_output(output, package)
    except SectionRepairError:
        return None


def _persist_repair_checkpoint(
    path: Path,
    package: dict[str, Any],
    output: dict[str, Any],
) -> None:
    validated_package = validate_section_repair_package(package)
    validated_output = validate_section_repair_output(output, validated_package)
    _atomic_json_write(path, {
        "version": REPAIR_VERSION,
        "package_sha256": validated_package["package_sha256"],
        "output": validated_output,
    })


def _generate_repaired_section(
    package: dict[str, Any],
    generate_text: Callable[[str, str], str],
) -> dict[str, Any]:
    prompt = build_section_repair_prompt(package)
    try:
        response = generate_text(prompt["system"], prompt["user"])
    except Exception as exc:
        raise SectionRepairError(f"section repair provider failed: {exc}") from exc
    return parse_section_repair_response(response, package)


def _generate_refreshed_frame(
    section_run: dict[str, Any],
    generate_text: Callable[[str, str], str],
) -> dict[str, Any]:
    package = build_article_frame_package(section_run)
    prompt = build_article_frame_prompt(package)
    try:
        response = generate_text(prompt["system"], prompt["user"])
    except Exception as exc:
        raise SectionRepairError(f"article frame repair provider failed: {exc}") from exc
    try:
        return parse_article_frame_response(response, package)
    except SectionGenerationError as exc:
        raise SectionRepairError(f"article frame repair is invalid: {exc}") from exc


def _old_ledger_output_map(
    ledger_run: dict[str, Any],
    delivery: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    resolved = validate_resolved_delivery(delivery)
    if not isinstance(ledger_run, dict) or ledger_run.get("version") != CONTRACT_VERSION:
        raise SectionRepairError("old ledger_run must be a version 1 object")
    if ledger_run.get("delivery_sha256") != resolved["delivery_sha256"]:
        raise SectionRepairError("old ledger_run delivery SHA mismatch")
    if ledger_run.get("draft_sha256") != resolved["draft_sha256"]:
        raise SectionRepairError("old ledger_run draft SHA mismatch")
    if ledger_run.get("section_order") != resolved["section_order"]:
        raise SectionRepairError("old ledger_run section order mismatch")
    outputs = ledger_run.get("outputs")
    if not isinstance(outputs, list):
        raise SectionRepairError("old ledger_run outputs are invalid")
    return {
        item["section_id"]: item
        for item in outputs
        if isinstance(item, dict) and isinstance(item.get("section_id"), str)
    }


def remap_section_claim_output(
    old_output: dict[str, Any],
    new_package: dict[str, Any],
) -> dict[str, Any]:
    """Remap unchanged claims to new global S-IDs by unique normalized text."""
    if not isinstance(old_output, dict) or not isinstance(old_output.get("claims"), list):
        raise SectionRepairError("old section claim output is invalid")
    package = new_package
    allowed_evidence = {
        item["evidence_id"] for item in package["evidence"]
    }
    by_norm: dict[str, list[dict[str, str]]] = {}
    for sentence in package["sentences"]:
        by_norm.setdefault(
            seo_common.normalize_claim_text(sentence["text"]),
            [],
        ).append(sentence)
    remapped_claims: list[dict[str, Any]] = []
    for claim in old_output["claims"]:
        if not isinstance(claim, dict):
            raise SectionRepairError("old claim is invalid")
        claim_text = claim.get("claim_text")
        if not isinstance(claim_text, str):
            raise SectionRepairError("old claim_text is invalid")
        matches = by_norm.get(seo_common.normalize_claim_text(claim_text), [])
        if len(matches) != 1:
            raise SectionRepairError(
                "old claim_text does not map uniquely into the repaired delivery"
            )
        evidence_ids = claim.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(item not in allowed_evidence for item in evidence_ids)
        ):
            raise SectionRepairError("old claim evidence is no longer approved")
        remapped_claims.append({
            "sentence_id": matches[0]["sentence_id"],
            "claim_text": matches[0]["text"],
            "claim_type": claim.get("claim_type"),
            "evidence_ids": list(evidence_ids),
        })
    candidate = {
        "version": CONTRACT_VERSION,
        "section_id": package["section_id"],
        "package_sha256": package["package_sha256"],
        "claims": remapped_claims,
    }
    try:
        return validate_section_claim_output(candidate, package)
    except SectionDeliveryError as exc:
        raise SectionRepairError(f"remapped claim output is invalid: {exc}") from exc


def _generate_or_remap_ledgers(
    *,
    old_delivery: dict[str, Any],
    new_delivery: dict[str, Any],
    old_ledger_run: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    regenerate_units: set[str],
    generate_claim_text: Callable[..., str],
) -> tuple[dict[str, Any], int, int, int]:
    old_outputs = _old_ledger_output_map(old_ledger_run, old_delivery)
    outputs: list[dict[str, Any]] = []
    generated_count = 0
    remapped_count = 0
    fallback_generated_count = 0
    for unit_id in new_delivery["section_order"]:
        package = build_section_claim_package(
            new_delivery,
            link_contracts,
            context_manifest,
            unit_id,
        )
        output: dict[str, Any] | None = None
        if unit_id not in regenerate_units and unit_id in old_outputs:
            try:
                output = remap_section_claim_output(old_outputs[unit_id], package)
                remapped_count += 1
            except SectionRepairError:
                fallback_generated_count += 1
        if output is None:
            output = generate_section_claim_output(package, generate_claim_text)
            generated_count += 1
        outputs.append(output)
    ledger_run = {
        "version": CONTRACT_VERSION,
        "delivery_sha256": new_delivery["delivery_sha256"],
        "draft_sha256": new_delivery["draft_sha256"],
        "section_order": list(new_delivery["section_order"]),
        "outputs": outputs,
        "generated_count": generated_count,
        "resumed_count": 0,
        "remapped_count": remapped_count,
        "fallback_generated_count": fallback_generated_count,
        "complete": len(outputs) == len(new_delivery["section_order"]),
    }
    return ledger_run, generated_count, remapped_count, fallback_generated_count


def run_sectional_repair_sequence(
    *,
    workspace: Path,
    slug: str,
    section_contracts: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    section_run: dict[str, Any],
    article_frame: dict[str, Any],
    delivery: dict[str, Any],
    ledger_run: dict[str, Any],
    metadata: dict[str, Any],
    repair_plan: dict[str, Any],
    generate_section_text: Callable[[str, str], str],
    generate_frame_text: Callable[[str, str], str],
    generate_claim_text: Callable[..., str],
    resume: bool = True,
) -> dict[str, Any]:
    """Execute one bounded shadow repair round and re-run all Phase 4/5 gates."""
    old_delivery = validate_resolved_delivery(delivery)
    plan = validate_section_repair_plan(repair_plan, old_delivery)
    if plan["global_blockers"]:
        raise SectionRepairError(
            "repair plan contains global blockers that cannot be safely localized"
        )
    if not plan["actions"]:
        raise SectionRepairError("repair plan contains no localized actions")
    sections = validate_section_contracts(section_contracts)
    links = validate_section_link_contracts(link_contracts, sections)
    meta = validate_assembly_metadata(metadata, expected_topic=old_delivery["topic"])
    output_by_id = _section_output_map(section_run)
    action_by_id = {item["section_id"]: item for item in plan["actions"]}
    new_outputs: list[dict[str, Any]] = []
    changed_body_units: set[str] = set()
    generated_sections = 0
    resumed_sections = 0
    working_output_by_id = dict(output_by_id)
    for section_id in sections["section_order"]:
        action = action_by_id.get(section_id)
        if action is None or action["action"] == "ledger_repair":
            new_outputs.append(output_by_id[section_id])
            continue
        working_section_run = {
            **section_run,
            "outputs": [
                working_output_by_id[item_id]
                for item_id in sections["section_order"]
            ],
        }
        package = build_section_repair_package(
            section_contracts=sections,
            link_contracts=links,
            context_manifest=context_manifest,
            section_run=working_section_run,
            current_section_run=section_run,
            plan=plan,
            section_id=section_id,
        )
        checkpoint = _repair_checkpoint_path(
            workspace,
            slug,
            plan["round_number"],
            section_id,
        )
        repaired = _load_repair_checkpoint(checkpoint, package) if resume else None
        if repaired is None:
            repaired = _generate_repaired_section(package, generate_section_text)
            _persist_repair_checkpoint(checkpoint, package, repaired)
            generated_sections += 1
        else:
            resumed_sections += 1
        new_outputs.append(repaired)
        working_output_by_id[section_id] = repaired
        changed_body_units.add(section_id)
    new_section_run = {
        "version": CONTRACT_VERSION,
        "topic": sections["topic"],
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "section_order": list(sections["section_order"]),
        "outputs": new_outputs,
        "generated_count": generated_sections,
        "resumed_count": resumed_sections,
        "complete": True,
    }
    if plan["frame_refresh"]:
        new_frame = _generate_refreshed_frame(new_section_run, generate_frame_text)
        changed_frame_units = set(_FRAME_IDS)
    else:
        frame_package = build_article_frame_package(new_section_run)
        try:
            new_frame = validate_article_frame_output(article_frame, frame_package)
        except SectionGenerationError as exc:
            raise SectionRepairError(
                "existing article frame is stale for the repaired section run"
            ) from exc
        changed_frame_units = set()
    new_section_run["article_frame"] = new_frame
    try:
        new_delivery = resolve_section_placeholders(
            new_section_run,
            links,
            context_manifest,
            article_frame=new_frame,
        )
    except SectionDeliveryError as exc:
        raise SectionRepairError(f"repaired delivery failed: {exc}") from exc
    ledger_only_units = {
        item["section_id"]
        for item in plan["actions"]
        if item["action"] == "ledger_repair"
    }
    regenerate_units = changed_body_units | changed_frame_units | ledger_only_units
    if not callable(generate_claim_text):
        raise SectionRepairError("generate_claim_text must be callable")
    new_ledger_run, generated_ledgers, remapped_ledgers, fallback_ledgers = (
        _generate_or_remap_ledgers(
            old_delivery=old_delivery,
            new_delivery=new_delivery,
            old_ledger_run=ledger_run,
            link_contracts=links,
            context_manifest=context_manifest,
            regenerate_units=regenerate_units,
            generate_claim_text=generate_claim_text,
        )
    )
    try:
        merged_ledger = merge_section_claim_ledgers(
            new_delivery,
            links,
            context_manifest,
            new_ledger_run,
        )
        assembly = assemble_sectional_article(new_delivery, meta, merged_ledger)
    except (SectionDeliveryError, SectionAssemblyError) as exc:
        raise SectionRepairError(f"repaired article failed final gates: {exc}") from exc
    untouched_body_units = [
        section_id
        for section_id in sections["section_order"]
        if section_id not in changed_body_units
    ]
    for section_id in untouched_body_units:
        before = output_by_id[section_id]
        after = next(item for item in new_outputs if item["section_id"] == section_id)
        if before != after:
            raise SectionRepairError("repair changed an untargeted body section")
    result = {
        "version": REPAIR_VERSION,
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "topic": sections["topic"],
        "round_number": plan["round_number"],
        "plan_sha256": plan["plan_sha256"],
        "repair_plan": plan,
        "old_delivery_sha256": old_delivery["delivery_sha256"],
        "new_delivery_sha256": new_delivery["delivery_sha256"],
        "old_draft_sha256": old_delivery["draft_sha256"],
        "new_draft_sha256": new_delivery["draft_sha256"],
        "changed_body_units": [
            section_id
            for section_id in sections["section_order"]
            if section_id in changed_body_units
        ],
        "unchanged_body_units": untouched_body_units,
        "frame_refreshed": bool(changed_frame_units),
        "generated_sections": generated_sections,
        "resumed_sections": resumed_sections,
        "generated_ledgers": generated_ledgers,
        "remapped_ledgers": remapped_ledgers,
        "fallback_generated_ledgers": fallback_ledgers,
        "section_run": new_section_run,
        "article_frame": new_frame,
        "delivery": new_delivery,
        "ledger_run": new_ledger_run,
        "claim_ledger": assembly["claim_ledger"],
        "assembly": assembly,
    }
    result["result_sha256"] = _json_digest(result)
    return validate_sectional_repair_result(result)


def validate_sectional_repair_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("version") != REPAIR_VERSION:
        raise SectionRepairError("repair result must be a version 1 object")
    if result.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionRepairError("repair result language must be en")
    if not isinstance(result.get("round_number"), int) or not (
        1 <= result["round_number"] <= MAX_REPAIR_ROUNDS
    ):
        raise SectionRepairError("repair result round_number is invalid")
    for field in (
        "plan_sha256",
        "old_delivery_sha256",
        "new_delivery_sha256",
        "old_draft_sha256",
        "new_draft_sha256",
        "result_sha256",
    ):
        value = result.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise SectionRepairError(f"repair result {field} is invalid")
    plan = result.get("repair_plan")
    if not isinstance(plan, dict):
        raise SectionRepairError("repair result repair_plan is missing")
    plan_unsigned = dict(plan)
    plan_digest = plan_unsigned.pop("plan_sha256", None)
    if plan_digest != result["plan_sha256"] or plan_digest != _json_digest(plan_unsigned):
        raise SectionRepairError("repair result plan SHA mismatch")
    if plan.get("round_number") != result["round_number"]:
        raise SectionRepairError("repair result plan round mismatch")
    if plan.get("topic") != result.get("topic"):
        raise SectionRepairError("repair result topic does not match plan")
    if plan.get("delivery_sha256") != result["old_delivery_sha256"]:
        raise SectionRepairError("repair result old delivery does not match plan")
    if plan.get("draft_sha256") != result["old_draft_sha256"]:
        raise SectionRepairError("repair result old draft does not match plan")
    if plan.get("global_blockers"):
        raise SectionRepairError("repair result cannot originate from a blocked plan")
    delivery = validate_resolved_delivery(result.get("delivery"))
    if delivery["delivery_sha256"] != result["new_delivery_sha256"]:
        raise SectionRepairError("repair result delivery SHA mismatch")
    if delivery["draft_sha256"] != result["new_draft_sha256"]:
        raise SectionRepairError("repair result draft SHA mismatch")
    try:
        assembly = validate_sectional_assembly(result.get("assembly"))
    except SectionAssemblyError as exc:
        raise SectionRepairError(f"repair result assembly is invalid: {exc}") from exc
    if assembly.get("delivery") != delivery:
        raise SectionRepairError("repair result assembly does not use repaired delivery")
    if result.get("claim_ledger") != assembly.get("claim_ledger"):
        raise SectionRepairError("repair result claim ledger differs from assembly")
    section_run = result.get("section_run")
    if not isinstance(section_run, dict) or section_run.get("version") != CONTRACT_VERSION:
        raise SectionRepairError("repair result section_run is invalid")
    if section_run.get("topic") != delivery["topic"]:
        raise SectionRepairError("repair result section_run topic mismatch")
    if section_run.get("section_order") != delivery["body_section_order"]:
        raise SectionRepairError("repair result section_run order mismatch")
    outputs = section_run.get("outputs")
    if (
        not isinstance(outputs, list)
        or [item.get("section_id") for item in outputs if isinstance(item, dict)]
        != delivery["body_section_order"]
    ):
        raise SectionRepairError("repair result section_run outputs are invalid")
    delivery_body = {
        item["section_id"]: item
        for item in delivery["sections"]
        if item["section_id"] in delivery["body_section_order"]
    }
    for output in outputs:
        if not isinstance(output, dict):
            raise SectionRepairError("repair result section output is invalid")
        section_id = output["section_id"]
        markdown = output.get("markdown")
        if not isinstance(markdown, str):
            raise SectionRepairError("repair result section output Markdown is invalid")
        if output.get("markdown_sha256") != _markdown_digest(markdown):
            raise SectionRepairError("repair result section output Markdown SHA mismatch")
        if delivery_body[section_id]["source_markdown_sha256"] != output["markdown_sha256"]:
            raise SectionRepairError("repair result section output differs from delivery source")
        if delivery_body[section_id]["heading"] != output.get("heading"):
            raise SectionRepairError("repair result section heading differs from delivery")
    try:
        frame_package = build_article_frame_package(section_run)
        validate_article_frame_output(result.get("article_frame"), frame_package)
    except SectionGenerationError as exc:
        raise SectionRepairError(f"repair result article frame is invalid: {exc}") from exc
    ledger_run = result.get("ledger_run")
    if not isinstance(ledger_run, dict) or ledger_run.get("version") != CONTRACT_VERSION:
        raise SectionRepairError("repair result ledger_run is invalid")
    if ledger_run.get("delivery_sha256") != delivery["delivery_sha256"]:
        raise SectionRepairError("repair result ledger_run delivery SHA mismatch")
    if ledger_run.get("draft_sha256") != delivery["draft_sha256"]:
        raise SectionRepairError("repair result ledger_run draft SHA mismatch")
    if ledger_run.get("section_order") != delivery["section_order"]:
        raise SectionRepairError("repair result ledger_run order mismatch")
    ledger_outputs = ledger_run.get("outputs")
    if (
        not isinstance(ledger_outputs, list)
        or [item.get("section_id") for item in ledger_outputs if isinstance(item, dict)]
        != delivery["section_order"]
    ):
        raise SectionRepairError("repair result ledger_run outputs are invalid")
    changed = result.get("changed_body_units")
    unchanged = result.get("unchanged_body_units")
    if not isinstance(changed, list) or not isinstance(unchanged, list):
        raise SectionRepairError("repair result body unit lists are invalid")
    if set(changed) & set(unchanged):
        raise SectionRepairError("repair result body unit lists overlap")
    body_order = delivery["body_section_order"]
    changed_set = set(changed)
    unchanged_set = set(unchanged)
    if changed_set | unchanged_set != set(body_order):
        raise SectionRepairError("repair result body unit lists do not cover body order")
    if changed != [item for item in body_order if item in changed_set]:
        raise SectionRepairError("repair result changed units are out of order")
    if unchanged != [item for item in body_order if item in unchanged_set]:
        raise SectionRepairError("repair result unchanged units are out of order")
    planned_changed = [
        item["section_id"]
        for item in plan.get("actions", [])
        if item.get("section_id") in body_order
        and item.get("action") in {"section_rewrite", "link_repair"}
    ]
    if changed != planned_changed:
        raise SectionRepairError("repair result changed units differ from repair plan")
    count_fields = (
        "generated_sections",
        "resumed_sections",
        "generated_ledgers",
        "remapped_ledgers",
        "fallback_generated_ledgers",
    )
    if any(
        not isinstance(result.get(field), int) or result[field] < 0
        for field in count_fields
    ):
        raise SectionRepairError("repair result counters are invalid")
    if result["generated_sections"] + result["resumed_sections"] != len(changed):
        raise SectionRepairError("repair result section counters are inconsistent")
    if result["generated_ledgers"] + result["remapped_ledgers"] != len(
        delivery["section_order"]
    ):
        raise SectionRepairError("repair result ledger counters are inconsistent")
    if result["fallback_generated_ledgers"] > result["generated_ledgers"]:
        raise SectionRepairError("repair result fallback ledger counter is invalid")
    if not isinstance(result.get("frame_refreshed"), bool):
        raise SectionRepairError("repair result frame_refreshed is invalid")
    if result["frame_refreshed"] is not bool(plan.get("frame_refresh")):
        raise SectionRepairError("repair result frame refresh differs from repair plan")
    unsigned = dict(result)
    digest = unsigned.pop("result_sha256")
    if digest != _json_digest(unsigned):
        raise SectionRepairError("repair result SHA mismatch")
    return result


def repair_result_path(workspace: Path, slug: str, round_number: int) -> Path:
    clean_slug = _clean_text(slug, "slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean_slug):
        raise SectionRepairError("slug format is invalid")
    if not isinstance(round_number, int) or not 1 <= round_number <= MAX_REPAIR_ROUNDS:
        raise SectionRepairError("repair result round is invalid")
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / f"repair-round-{round_number}.json"
    )


def persist_sectional_repair_result(
    workspace: Path,
    slug: str,
    result: dict[str, Any],
) -> str:
    validated = validate_sectional_repair_result(result)
    path = repair_result_path(workspace, slug, validated["round_number"])
    _atomic_json_write(path, validated)
    return str(path)


def load_sectional_repair_result(
    workspace: Path,
    slug: str,
    round_number: int,
    *,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any] | None:
    path = repair_result_path(workspace, slug, round_number)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        validated = validate_sectional_repair_result(value)
    except (OSError, json.JSONDecodeError, SectionRepairError):
        return None
    if (
        expected_plan_sha256 is not None
        and validated["plan_sha256"] != expected_plan_sha256
    ):
        return None
    return validated
