"""Shadow-only link binding and per-section claim-ledger generation.

The module consumes validated Phase 3 section outputs.  It resolves approved
ARTICLE/PRODUCT/CITE placeholders to registry URLs, assigns global sentence IDs
from the final resolved Markdown, then audits each section against only its
approved evidence cards.  It does not import or replace the formal Legacy W0
workflow.
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
from urllib.parse import urlparse

from data_sources.modules import seo_common
from seo_ops.services.ai import AIEmptyTextError
from seo_ops.services.sectional_context import validate_candidate_registry
from seo_ops.services.sectional_generation import (
    build_article_frame_package,
    parse_section_placeholders,
    validate_article_frame_output,
)
from seo_ops.services.sectional_writing import (
    CONTRACT_VERSION,
    DEFAULT_CONTENT_LANGUAGE,
    ContractValidationError,
    validate_section_link_contracts,
)

LEDGER_MAX_ATTEMPTS = 2
LEDGER_MAX_TOKENS = 4000

_SHA256 = re.compile(r"[a-f0-9]{64}")
_SECTION_ID = re.compile(r"section-[a-f0-9]{10}")
_FRAME_UNIT_IDS = (
    "frame-introduction",
    "frame-takeaways",
    "frame-conclusion",
    "frame-faq",
)
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PLACEHOLDER_REMAINDER = re.compile(r"\[\[(?:ARTICLE|PRODUCT|CITE):")
_CLAIM_KEYS = frozenset({"sentence_id", "claim_type", "evidence_ids"})


class SectionDeliveryError(ContractValidationError):
    """Raised when binding, sentence assignment or ledger validation fails."""


def _valid_unit_id(value: str) -> bool:
    return bool(_SECTION_ID.fullmatch(value) or value in _FRAME_UNIT_IDS)


def _clean_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SectionDeliveryError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not allow_empty and not cleaned:
        raise SectionDeliveryError(f"{field} must not be empty")
    return cleaned


def _json_digest(data: Any) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _markdown_digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _registry_indexes(registry: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    validated = validate_candidate_registry(registry)
    return {
        kind: {
            candidate["candidate_id"]: candidate
            for candidate in validated[kind]["candidates"]
        }
        for kind in ("articles", "products", "evidence")
    }


def _replace_once(markdown: str, token: str, replacement: str) -> str:
    if markdown.count(token) != 1:
        raise SectionDeliveryError(
            f"placeholder token must occur exactly once during binding: {token[:80]}"
        )
    return markdown.replace(token, replacement, 1)


def _citation_anchor(url: str) -> str:
    host = (urlparse(url).hostname or "source").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host or "source"


def _validate_section_run(section_run: Any) -> dict[str, Any]:
    if not isinstance(section_run, dict) or section_run.get("version") != CONTRACT_VERSION:
        raise SectionDeliveryError("section_run must be a version 1 object")
    if section_run.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionDeliveryError("section_run content_language must be en")
    order = section_run.get("section_order")
    outputs = section_run.get("outputs")
    if not isinstance(order, list) or not order:
        raise SectionDeliveryError("section_run.section_order must be a non-empty list")
    if not isinstance(outputs, list) or len(outputs) != len(order):
        raise SectionDeliveryError("section_run outputs are incomplete")
    if [item.get("section_id") for item in outputs if isinstance(item, dict)] != order:
        raise SectionDeliveryError("section_run outputs are out of order")
    if len(order) != len(set(order)) or any(
        not isinstance(item, str) or not _SECTION_ID.fullmatch(item)
        for item in order
    ):
        raise SectionDeliveryError("section_run section IDs are invalid")
    return section_run


def _build_frame_units(
    section_run: dict[str, Any],
    article_frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    package = build_article_frame_package(section_run)
    frame = validate_article_frame_output(article_frame, package)
    faq_lines = ["## Frequently Asked Questions"]
    for item in frame["faq"]:
        faq_lines.extend([
            "",
            f"### {item['question']}",
            "",
            item["answer"],
        ])
    markdown_by_id = {
        "frame-introduction": (
            f"# {section_run['topic']}\n\n{frame['introduction']}"
        ),
        "frame-takeaways": (
            "## Key Takeaways\n\n"
            + "\n".join(f"- {item}" for item in frame["key_takeaways"])
        ),
        "frame-conclusion": f"## Conclusion\n\n{frame['conclusion']}",
        "frame-faq": "\n".join(faq_lines),
    }
    headings = {
        "frame-introduction": "Introduction",
        "frame-takeaways": "Key Takeaways",
        "frame-conclusion": "Conclusion",
        "frame-faq": "Frequently Asked Questions",
    }
    return {
        unit_id: {
            "section_id": unit_id,
            "unit_kind": unit_id.removeprefix("frame-"),
            "heading": headings[unit_id],
            "source_markdown_sha256": _markdown_digest(markdown),
            "markdown": markdown,
            "markdown_sha256": _markdown_digest(markdown),
            "bindings": [],
            "sentence_ids": [],
        }
        for unit_id, markdown in markdown_by_id.items()
    }


def _manifest_sections(context_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = context_manifest.get("sections")
    if not isinstance(sections, list):
        raise SectionDeliveryError("context_manifest.sections must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in sections:
        if not isinstance(item, dict):
            raise SectionDeliveryError("context manifest section must be an object")
        section_id = _clean_text(item.get("section_id"), "manifest.section_id")
        if section_id in result:
            raise SectionDeliveryError("context manifest section IDs must be unique")
        result[section_id] = item
    return result


def _validate_context_manifest(context_manifest: Any) -> dict[str, Any]:
    if not isinstance(context_manifest, dict) or context_manifest.get("version") != 1:
        raise SectionDeliveryError("context_manifest must be a version 1 object")
    if context_manifest.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionDeliveryError("context manifest content_language must be en")
    registry = context_manifest.get("registry")
    if not isinstance(registry, dict):
        raise SectionDeliveryError("context_manifest.registry is missing")
    validate_candidate_registry(registry)
    expected_sha = hashlib.sha256(
        json.dumps(registry, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if context_manifest.get("registry_sha256") != expected_sha:
        raise SectionDeliveryError("context manifest registry SHA mismatch")
    _manifest_sections(context_manifest)
    return context_manifest


def _validate_output_integrity(output: dict[str, Any]) -> None:
    markdown = output.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise SectionDeliveryError("section output markdown is missing")
    if output.get("markdown_sha256") != _markdown_digest(markdown):
        raise SectionDeliveryError("section output markdown SHA mismatch")
    parsed = parse_section_placeholders(markdown)
    inventory = {
        key: [item["candidate_id"] for item in records]
        for key, records in parsed.items()
    }
    if output.get("used_ids") != inventory:
        raise SectionDeliveryError("section output used_ids do not match placeholders")
    decisions = output.get("decisions")
    if not isinstance(decisions, dict):
        raise SectionDeliveryError("section output decisions are missing")
    for key, ids in inventory.items():
        decision = decisions.get(key)
        if not isinstance(decision, dict) or decision.get("used_ids") != ids:
            raise SectionDeliveryError(
                f"section output decision does not match placeholders: {key}"
            )


def _approved_manifest_ids(
    manifest_section: dict[str, Any],
    link_section: dict[str, Any],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for field, key in (
        ("article_candidates", "article_links"),
        ("product_candidates", "product_links"),
        ("evidence_candidates", "external_citations"),
    ):
        records = manifest_section.get(field)
        if not isinstance(records, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str)
            for item in records
        ):
            raise SectionDeliveryError(f"manifest {field} is invalid")
        available = {item["candidate_id"] for item in records}
        gate = link_section.get(key)
        if not isinstance(gate, dict) or not isinstance(gate.get("selected_ids"), list):
            raise SectionDeliveryError(f"link contract {key} is invalid")
        selected = set(gate["selected_ids"])
        if not selected <= available:
            raise SectionDeliveryError(
                f"link contract {key} contains IDs missing from the context manifest"
            )
        result[key] = selected
    return result


def resolve_section_placeholders(
    section_run: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    article_frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind approved placeholders and assign final global sentence IDs."""
    run = _validate_section_run(section_run)
    links = validate_section_link_contracts(link_contracts)
    if links.get("topic") != run.get("topic"):
        raise SectionDeliveryError("link contract topic does not match section run")
    if links.get("section_order") != run.get("section_order"):
        raise SectionDeliveryError("link contract order does not match section run")
    context_manifest = _validate_context_manifest(context_manifest)
    if context_manifest.get("topic") != run.get("topic"):
        raise SectionDeliveryError("context manifest topic does not match section run")
    registry = context_manifest.get("registry")
    assert isinstance(registry, dict)
    indexes = _registry_indexes(registry)
    manifest_by_id = _manifest_sections(context_manifest)
    link_by_id = {item["section_id"]: item for item in links["sections"]}
    resolved_frame = article_frame or section_run.get("article_frame")
    if not isinstance(resolved_frame, dict):
        raise SectionDeliveryError("article_frame is required for final delivery")
    frame_units = _build_frame_units(run, resolved_frame)

    resolved_sections: list[dict[str, Any]] = [
        frame_units["frame-introduction"],
        frame_units["frame-takeaways"],
    ]
    all_bindings: list[dict[str, Any]] = []
    for output in run["outputs"]:
        if not isinstance(output, dict):
            raise SectionDeliveryError("section output must be an object")
        _validate_output_integrity(output)
        section_id = output["section_id"]
        if section_id not in manifest_by_id or section_id not in link_by_id:
            raise SectionDeliveryError("section output is missing from context manifest")
        markdown = output["markdown"]
        parsed = parse_section_placeholders(markdown)
        approved = _approved_manifest_ids(
            manifest_by_id[section_id],
            link_by_id[section_id],
        )
        for link_type, records in parsed.items():
            used = {item["candidate_id"] for item in records}
            unknown = used - approved[link_type]
            if unknown:
                raise SectionDeliveryError(
                    f"section uses candidates not approved for {link_type}: "
                    f"{' '.join(sorted(unknown))}"
                )
        bindings: list[dict[str, Any]] = []

        for record in parsed["article_links"]:
            candidate = indexes["articles"].get(record["candidate_id"])
            if candidate is None:
                raise SectionDeliveryError(
                    f"unknown article candidate: {record['candidate_id']}"
                )
            token = f"[[ARTICLE:{record['candidate_id']}|{record['anchor']}]]"
            replacement = f"[{record['anchor']}]({candidate['url']})"
            markdown = _replace_once(markdown, token, replacement)
            bindings.append({
                "kind": "article",
                "candidate_id": record["candidate_id"],
                "anchor": record["anchor"],
                "url": candidate["url"],
            })

        for record in parsed["product_links"]:
            candidate = indexes["products"].get(record["candidate_id"])
            if candidate is None:
                raise SectionDeliveryError(
                    f"unknown product candidate: {record['candidate_id']}"
                )
            if candidate.get("attribute_conflicts"):
                raise SectionDeliveryError(
                    f"conflicted product cannot be bound: {record['candidate_id']}"
                )
            token = f"[[PRODUCT:{record['candidate_id']}|{record['anchor']}]]"
            replacement = f"[{record['anchor']}]({candidate['url']})"
            markdown = _replace_once(markdown, token, replacement)
            bindings.append({
                "kind": "product",
                "candidate_id": record["candidate_id"],
                "product_id": candidate.get("product_id", ""),
                "anchor": record["anchor"],
                "url": candidate["url"],
            })

        for record in parsed["external_citations"]:
            candidate = indexes["evidence"].get(record["candidate_id"])
            if candidate is None:
                raise SectionDeliveryError(
                    f"unknown evidence candidate: {record['candidate_id']}"
                )
            token = f"[[CITE:{record['candidate_id']}]]"
            anchor = _citation_anchor(candidate["url"])
            replacement = f"([{anchor}]({candidate['url']}))"
            markdown = _replace_once(markdown, token, replacement)
            bindings.append({
                "kind": "external_citation",
                "candidate_id": record["candidate_id"],
                "evidence_id": candidate.get("evidence_id", record["candidate_id"]),
                "anchor": anchor,
                "url": candidate["url"],
            })

        if _PLACEHOLDER_REMAINDER.search(markdown) or "[[" in markdown or "]]" in markdown:
            raise SectionDeliveryError("resolved section still contains a placeholder")
        resolved_sections.append({
            "section_id": section_id,
            "unit_kind": "body_section",
            "heading": output["heading"],
            "source_markdown_sha256": output["markdown_sha256"],
            "markdown": markdown,
            "markdown_sha256": _markdown_digest(markdown),
            "bindings": bindings,
            "sentence_ids": [],
        })
        all_bindings.extend({"section_id": section_id, **item} for item in bindings)

    resolved_sections.extend([
        frame_units["frame-conclusion"],
        frame_units["frame-faq"],
    ])

    draft_markdown = "\n\n".join(
        item["markdown"].strip() for item in resolved_sections
    ).strip() + "\n"
    authoritative = seo_common.extract_draft_sentences(draft_markdown)
    sentence_records: list[dict[str, str]] = []
    cursor = 0
    for section in resolved_sections:
        local = seo_common.extract_draft_sentences(section["markdown"])
        ids: list[str] = []
        for local_sentence in local:
            if cursor >= len(authoritative):
                raise SectionDeliveryError("section sentence mapping exceeds full body")
            global_sentence = authoritative[cursor]
            if (
                local_sentence["text"] != global_sentence["text"]
                or local_sentence["norm"] != global_sentence["norm"]
            ):
                raise SectionDeliveryError(
                    "section sentence mapping differs from authoritative body extraction"
                )
            ids.append(global_sentence["sentence_id"])
            sentence_records.append({
                **global_sentence,
                "section_id": section["section_id"],
            })
            cursor += 1
        section["sentence_ids"] = ids
    if cursor != len(authoritative):
        raise SectionDeliveryError("not all authoritative body sentences were mapped")

    delivery = {
        "version": CONTRACT_VERSION,
        "topic": run["topic"],
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "body_section_order": list(run["section_order"]),
        "section_order": [item["section_id"] for item in resolved_sections],
        "sections": resolved_sections,
        "bindings": all_bindings,
        "draft_markdown": draft_markdown,
        "draft_sha256": _markdown_digest(draft_markdown),
        "sentences": sentence_records,
    }
    delivery["delivery_sha256"] = _json_digest(delivery)
    return validate_resolved_delivery(delivery)


def validate_resolved_delivery(delivery: Any) -> dict[str, Any]:
    if not isinstance(delivery, dict) or delivery.get("version") != CONTRACT_VERSION:
        raise SectionDeliveryError("resolved delivery must be a version 1 object")
    if delivery.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionDeliveryError("resolved delivery content_language must be en")
    _clean_text(delivery.get("topic"), "resolved delivery topic")
    order = delivery.get("section_order")
    body_order = delivery.get("body_section_order")
    sections = delivery.get("sections")
    if (
        not isinstance(order, list)
        or not isinstance(body_order, list)
        or not isinstance(sections, list)
    ):
        raise SectionDeliveryError("resolved delivery sections/order are invalid")
    if any(
        not isinstance(item, str) or not _SECTION_ID.fullmatch(item)
        for item in body_order
    ) or len(body_order) != len(set(body_order)):
        raise SectionDeliveryError("resolved delivery body section order is invalid")
    expected_order = [
        "frame-introduction",
        "frame-takeaways",
        *body_order,
        "frame-conclusion",
        "frame-faq",
    ]
    if order != expected_order:
        raise SectionDeliveryError("resolved delivery audit unit order is invalid")
    if [item.get("section_id") for item in sections if isinstance(item, dict)] != order:
        raise SectionDeliveryError("resolved delivery sections are out of order")
    expected_body_parts: list[str] = []
    flattened_bindings: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {
            "section_id",
            "unit_kind",
            "heading",
            "source_markdown_sha256",
            "markdown",
            "markdown_sha256",
            "bindings",
            "sentence_ids",
        }:
            raise SectionDeliveryError("resolved delivery section shape is invalid")
        section_id = section["section_id"]
        unit_kind = section["unit_kind"]
        if section_id in _FRAME_UNIT_IDS:
            if unit_kind != section_id.removeprefix("frame-"):
                raise SectionDeliveryError("resolved frame unit kind is invalid")
        elif section_id in body_order:
            if unit_kind != "body_section":
                raise SectionDeliveryError("resolved body section kind is invalid")
        else:
            raise SectionDeliveryError("resolved delivery contains an unknown unit ID")
        heading = _clean_text(section["heading"], "resolved section heading")
        markdown = section["markdown"]
        if not isinstance(markdown, str) or not markdown.strip():
            raise SectionDeliveryError("resolved section markdown is missing")
        expected_first_line = (
            f"# {delivery['topic']}"
            if section_id == "frame-introduction"
            else f"## {heading}"
        )
        if markdown.splitlines()[0].strip() != expected_first_line:
            raise SectionDeliveryError("resolved section heading does not match Markdown")
        if not _SHA256.fullmatch(str(section["source_markdown_sha256"])):
            raise SectionDeliveryError("resolved section source Markdown SHA is invalid")
        if section["markdown_sha256"] != _markdown_digest(markdown):
            raise SectionDeliveryError("resolved section Markdown SHA mismatch")
        if "[[" in markdown or "]]" in markdown:
            raise SectionDeliveryError("resolved section contains placeholders")
        bindings = section["bindings"]
        if not isinstance(bindings, list):
            raise SectionDeliveryError("resolved section bindings must be a list")
        for binding in bindings:
            if not isinstance(binding, dict):
                raise SectionDeliveryError("resolved section binding is invalid")
            if binding.get("kind") not in {
                "article",
                "product",
                "external_citation",
            }:
                raise SectionDeliveryError("resolved section binding kind is invalid")
            candidate_id = _clean_text(
                binding.get("candidate_id"),
                "resolved binding candidate_id",
            )
            url = _clean_text(binding.get("url"), "resolved binding URL")
            if candidate_id not in markdown and url not in markdown:
                raise SectionDeliveryError("resolved binding is not represented in Markdown")
            if url not in markdown:
                raise SectionDeliveryError("resolved binding URL is missing from Markdown")
            flattened_bindings.append({
                "section_id": section["section_id"],
                **binding,
            })
        expected_body_parts.append(markdown.strip())
    draft = delivery.get("draft_markdown")
    if not isinstance(draft, str) or not draft.strip():
        raise SectionDeliveryError("resolved delivery draft_markdown is missing")
    if delivery.get("draft_sha256") != _markdown_digest(draft):
        raise SectionDeliveryError("resolved delivery draft SHA mismatch")
    expected_draft = "\n\n".join(expected_body_parts).strip() + "\n"
    if draft != expected_draft:
        raise SectionDeliveryError("resolved delivery draft is not the unit concatenation")
    if "[[" in draft or "]]" in draft:
        raise SectionDeliveryError("resolved delivery draft contains placeholders")
    if delivery.get("bindings") != flattened_bindings:
        raise SectionDeliveryError("resolved delivery bindings do not match sections")
    sentences = delivery.get("sentences")
    authoritative = seo_common.extract_draft_sentences(draft)
    if not isinstance(sentences, list) or len(sentences) != len(authoritative):
        raise SectionDeliveryError("resolved delivery sentence table is invalid")
    for index, (record, expected) in enumerate(zip(sentences, authoritative, strict=True)):
        if not isinstance(record, dict):
            raise SectionDeliveryError(f"resolved sentence {index} is invalid")
        if any(record.get(key) != expected[key] for key in ("sentence_id", "text", "norm")):
            raise SectionDeliveryError("resolved sentence table differs from body")
        if record.get("section_id") not in order:
            raise SectionDeliveryError("resolved sentence has unknown section_id")
    by_section = {
        section_id: [
            item["sentence_id"]
            for item in sentences
            if item["section_id"] == section_id
        ]
        for section_id in order
    }
    for section in sections:
        if section.get("sentence_ids") != by_section[section["section_id"]]:
            raise SectionDeliveryError("resolved section sentence_ids are inconsistent")
    unsigned = dict(delivery)
    digest = unsigned.pop("delivery_sha256", None)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise SectionDeliveryError("resolved delivery SHA is invalid")
    if digest != _json_digest(unsigned):
        raise SectionDeliveryError("resolved delivery SHA mismatch")
    return delivery


def resolved_delivery_path(workspace: Path, slug: str) -> Path:
    clean_slug = _validate_slug(slug)
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / "resolved-delivery.json"
    )


def persist_resolved_delivery(
    workspace: Path,
    slug: str,
    delivery: dict[str, Any],
) -> str:
    validated = validate_resolved_delivery(delivery)
    path = resolved_delivery_path(workspace, slug)
    _atomic_json_write(path, validated)
    return str(path)


def load_resolved_delivery(
    workspace: Path,
    slug: str,
    *,
    expected_delivery_sha256: str | None = None,
) -> dict[str, Any] | None:
    path = resolved_delivery_path(workspace, slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        validated = validate_resolved_delivery(data)
    except (OSError, json.JSONDecodeError, SectionDeliveryError):
        return None
    if expected_delivery_sha256 is not None:
        if validated["delivery_sha256"] != expected_delivery_sha256:
            return None
    return validated


def _allowed_evidence_for_section(
    context_manifest: dict[str, Any],
    link_contracts: dict[str, Any],
    section_id: str,
) -> list[dict[str, Any]]:
    context_manifest = _validate_context_manifest(context_manifest)
    links = validate_section_link_contracts(link_contracts)
    manifest_by_id = _manifest_sections(context_manifest)
    registry = validate_candidate_registry(context_manifest["registry"])
    by_id = {
        candidate["candidate_id"]: candidate
        for candidate in registry["evidence"]["candidates"]
    }
    if section_id in _FRAME_UNIT_IDS:
        ranked_rows = [
            row
            for manifest in manifest_by_id.values()
            for row in manifest.get("evidence_candidates", [])
            if isinstance(row, dict)
        ]
        selected_ids = []
        seen: set[str] = set()
        for link_section in links["sections"]:
            for candidate_id in link_section["external_citations"]["selected_ids"]:
                if candidate_id not in seen:
                    seen.add(candidate_id)
                    selected_ids.append(candidate_id)
    else:
        manifest = manifest_by_id.get(section_id)
        if manifest is None:
            raise SectionDeliveryError("section is missing from context manifest")
        ranked = manifest.get("evidence_candidates")
        if not isinstance(ranked, list):
            raise SectionDeliveryError("manifest evidence_candidates must be a list")
        ranked_rows = [item for item in ranked if isinstance(item, dict)]
        link_section = next(
            (
                item
                for item in links["sections"]
                if item.get("section_id") == section_id
            ),
            None,
        )
        if not isinstance(link_section, dict):
            raise SectionDeliveryError("section is missing from link contracts")
        selected_ids = link_section["external_citations"]["selected_ids"]
    available_ids = {item.get("candidate_id") for item in ranked_rows}
    if not set(selected_ids) <= available_ids:
        raise SectionDeliveryError(
            "link contract evidence IDs are missing from the context manifest"
        )
    result: list[dict[str, Any]] = []
    for candidate_id in selected_ids:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise SectionDeliveryError(
                f"manifest evidence candidate missing from registry: {candidate_id}"
            )
        result.append({
            "evidence_id": candidate["evidence_id"],
            "support": candidate["support"],
            "concepts": candidate.get("concepts", []),
            "claim_types": candidate.get("claim_types", []),
            "source_url": candidate["url"],
        })
    return result


def build_section_claim_package(
    delivery: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    section_id: str,
) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    links = validate_section_link_contracts(link_contracts)
    context_manifest = _validate_context_manifest(context_manifest)
    if links.get("topic") != resolved["topic"]:
        raise SectionDeliveryError("claim package link-contract topic mismatch")
    if links.get("section_order") != resolved["body_section_order"]:
        raise SectionDeliveryError("claim package link-contract order mismatch")
    if context_manifest.get("topic") != resolved["topic"]:
        raise SectionDeliveryError("claim package context topic mismatch")
    if [
        item["section_id"]
        for item in context_manifest["sections"]
        if isinstance(item, dict) and "section_id" in item
    ] != resolved["body_section_order"]:
        raise SectionDeliveryError("claim package context section order mismatch")
    clean_id = _clean_text(section_id, "section_id")
    if clean_id not in resolved["section_order"]:
        raise SectionDeliveryError("section_id is not in resolved delivery")
    sentences = [
        {
            "sentence_id": item["sentence_id"],
            "text": item["text"],
        }
        for item in resolved["sentences"]
        if item["section_id"] == clean_id
    ]
    section = next(item for item in resolved["sections"] if item["section_id"] == clean_id)
    package = {
        "version": CONTRACT_VERSION,
        "topic": resolved["topic"],
        "content_language": DEFAULT_CONTENT_LANGUAGE,
        "delivery_sha256": resolved["delivery_sha256"],
        "draft_sha256": resolved["draft_sha256"],
        "section_id": clean_id,
        "heading": section["heading"],
        "sentences": sentences,
        "evidence": _allowed_evidence_for_section(
            context_manifest,
            links,
            clean_id,
        ),
    }
    package["package_sha256"] = _json_digest(package)
    return validate_section_claim_package(package)


def validate_section_claim_package(package: Any) -> dict[str, Any]:
    if not isinstance(package, dict) or package.get("version") != CONTRACT_VERSION:
        raise SectionDeliveryError("claim package must be a version 1 object")
    if package.get("content_language") != DEFAULT_CONTENT_LANGUAGE:
        raise SectionDeliveryError("claim package content_language must be en")
    if not _valid_unit_id(_clean_text(package.get("section_id"), "section_id")):
        raise SectionDeliveryError("claim package section_id is invalid")
    for field in ("delivery_sha256", "draft_sha256", "package_sha256"):
        value = _clean_text(package.get(field), f"claim package {field}")
        if not _SHA256.fullmatch(value):
            raise SectionDeliveryError(f"claim package {field} is invalid")
    sentences = package.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise SectionDeliveryError("claim package sentences must be non-empty")
    sentence_ids = []
    for item in sentences:
        if not isinstance(item, dict) or set(item) != {"sentence_id", "text"}:
            raise SectionDeliveryError("claim package sentence is invalid")
        sentence_ids.append(_clean_text(item["sentence_id"], "sentence_id"))
        _clean_text(item["text"], "sentence text")
    if len(sentence_ids) != len(set(sentence_ids)):
        raise SectionDeliveryError("claim package sentence IDs must be unique")
    evidence = package.get("evidence")
    if not isinstance(evidence, list):
        raise SectionDeliveryError("claim package evidence must be a list")
    evidence_ids = []
    for item in evidence:
        if not isinstance(item, dict):
            raise SectionDeliveryError("claim package evidence item is invalid")
        evidence_ids.append(_clean_text(item.get("evidence_id"), "evidence_id"))
        _clean_text(item.get("support"), "evidence support")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise SectionDeliveryError("claim package evidence IDs must be unique")
    unsigned = dict(package)
    digest = unsigned.pop("package_sha256")
    if digest != _json_digest(unsigned):
        raise SectionDeliveryError("claim package SHA mismatch")
    return package


def build_section_claim_prompt(package: dict[str, Any]) -> dict[str, str]:
    validated = validate_section_claim_package(package)
    system = """You are an evidence auditor for one English article section.
Inspect every supplied global sentence ID. Emit a claim only when an allowed
evidence card directly supports the factual sentence. Never invent or modify a
sentence ID or evidence ID. Do not copy claim_text; the server fills it from the
final resolved Markdown. Return JSON only with this exact shape:
{"version":1,"claims":[{"sentence_id":"S001","claim_type":"general","evidence_ids":["ev_x"]}]}
Allowed claim keys are sentence_id, claim_type and evidence_ids. If no supplied
sentence is supported, return exactly {"version":1,"claims":[]}."""
    user = "SECTION CLAIM PACKAGE\n" + json.dumps(
        validated,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return {"system": system, "user": user}


def parse_section_claim_response(
    response_text: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_section_claim_package(package)
    if not isinstance(response_text, str) or not response_text.strip():
        raise SectionDeliveryError("claim response is empty")
    clean = response_text.strip()
    if clean.startswith("```") or clean.endswith("```"):
        raise SectionDeliveryError("claim response must not use Markdown fences")
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise SectionDeliveryError("claim response is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) != {"version", "claims"}:
        raise SectionDeliveryError("claim response top-level shape is invalid")
    if data.get("version") != CONTRACT_VERSION or not isinstance(data.get("claims"), list):
        raise SectionDeliveryError("claim response version/claims are invalid")
    sentence_by_id = {
        item["sentence_id"]: item["text"] for item in validated["sentences"]
    }
    allowed_evidence = {
        item["evidence_id"] for item in validated["evidence"]
    }
    canonical: list[dict[str, Any]] = []
    identities: set[tuple[str, str, tuple[str, ...]]] = set()
    for index, claim in enumerate(data["claims"]):
        if not isinstance(claim, dict) or set(claim) != _CLAIM_KEYS:
            raise SectionDeliveryError(f"claims[{index}] shape is invalid")
        sentence_id = claim.get("sentence_id")
        if not isinstance(sentence_id, str) or sentence_id not in sentence_by_id:
            raise SectionDeliveryError(f"claims[{index}] sentence_id is not allowed")
        claim_type = _clean_text(claim.get("claim_type"), f"claims[{index}].claim_type")
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise SectionDeliveryError(f"claims[{index}] evidence_ids must be non-empty")
        if any(not isinstance(item, str) or not item for item in evidence_ids):
            raise SectionDeliveryError(f"claims[{index}] evidence_ids are invalid")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SectionDeliveryError(f"claims[{index}] evidence_ids must be unique")
        unknown = set(evidence_ids) - allowed_evidence
        if unknown:
            raise SectionDeliveryError(
                f"claims[{index}] uses unapproved evidence IDs: {' '.join(sorted(unknown))}"
            )
        identity = (sentence_id, claim_type, tuple(evidence_ids))
        if identity in identities:
            raise SectionDeliveryError(f"claims[{index}] duplicates an earlier claim")
        identities.add(identity)
        canonical.append({
            "sentence_id": sentence_id,
            "claim_text": sentence_by_id[sentence_id],
            "claim_type": claim_type,
            "evidence_ids": list(evidence_ids),
        })
    return {
        "version": CONTRACT_VERSION,
        "section_id": validated["section_id"],
        "package_sha256": validated["package_sha256"],
        "claims": canonical,
    }


def validate_section_claim_output(
    output: Any,
    package: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_section_claim_package(package)
    if not isinstance(output, dict) or output.get("version") != CONTRACT_VERSION:
        raise SectionDeliveryError("section claim output must be a version 1 object")
    if output.get("section_id") != validated["section_id"]:
        raise SectionDeliveryError("section claim output section_id mismatch")
    if output.get("package_sha256") != validated["package_sha256"]:
        raise SectionDeliveryError("section claim output package SHA mismatch")
    claims = output.get("claims")
    if not isinstance(claims, list):
        raise SectionDeliveryError("section claim output claims must be a list")
    raw = {
        "version": CONTRACT_VERSION,
        "claims": [
            {
                "sentence_id": item.get("sentence_id"),
                "claim_type": item.get("claim_type"),
                "evidence_ids": item.get("evidence_ids"),
            }
            for item in claims
            if isinstance(item, dict)
        ],
    }
    if len(raw["claims"]) != len(claims):
        raise SectionDeliveryError("section claim output contains a non-object claim")
    canonical = parse_section_claim_response(json.dumps(raw), validated)
    if canonical != output:
        raise SectionDeliveryError("section claim output differs from canonical claims")
    return output


def _validate_slug(slug: str) -> str:
    clean = _clean_text(slug, "slug")
    if not _SLUG.fullmatch(clean):
        raise SectionDeliveryError("slug must use lowercase letters, numbers and hyphens")
    return clean


def section_ledger_checkpoint_path(
    workspace: Path,
    slug: str,
    section_id: str,
) -> Path:
    clean_slug = _validate_slug(slug)
    clean_id = _clean_text(section_id, "section_id")
    if not _valid_unit_id(clean_id):
        raise SectionDeliveryError("section_id format is invalid")
    return (
        Path(workspace)
        / "drafts"
        / "sectional"
        / clean_slug
        / "ledger-checkpoints"
        / f"{clean_id}.json"
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


def persist_section_ledger_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
    output: dict[str, Any],
) -> str:
    validated_package = validate_section_claim_package(package)
    validated_output = validate_section_claim_output(output, validated_package)
    path = section_ledger_checkpoint_path(
        workspace,
        slug,
        validated_package["section_id"],
    )
    _atomic_json_write(path, {
        "version": CONTRACT_VERSION,
        "package_sha256": validated_package["package_sha256"],
        "output": validated_output,
    })
    return str(path)


def load_section_ledger_checkpoint(
    workspace: Path,
    slug: str,
    package: dict[str, Any],
) -> dict[str, Any] | None:
    validated = validate_section_claim_package(package)
    path = section_ledger_checkpoint_path(workspace, slug, validated["section_id"])
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != CONTRACT_VERSION:
        return None
    if checkpoint.get("package_sha256") != validated["package_sha256"]:
        return None
    output = checkpoint.get("output")
    try:
        canonical = validate_section_claim_output(output, validated)
    except SectionDeliveryError:
        return None
    return canonical


def _generate_one_section_ledger(
    package: dict[str, Any],
    generate_text: Callable[..., str],
) -> dict[str, Any]:
    prompt = build_section_claim_prompt(package)
    last_error: Exception | None = None
    for attempt in range(1, LEDGER_MAX_ATTEMPTS + 1):
        retry_note = ""
        if attempt > 1:
            retry_note = (
                "\n\nThe previous response was invalid. Return a fresh complete JSON "
                "object only; do not continue the previous response."
            )
        try:
            response = generate_text(
                prompt["system"],
                prompt["user"] + retry_note,
                max_tokens=LEDGER_MAX_TOKENS,
                thinking_mode="disabled",
            )
        except AIEmptyTextError as exc:
            if not exc.retryable:
                raise SectionDeliveryError(
                    f"claim ledger provider stopped without text: {exc}"
                ) from exc
            last_error = exc
            continue
        except Exception as exc:
            raise SectionDeliveryError(f"claim ledger provider failed: {exc}") from exc
        try:
            return parse_section_claim_response(response, package)
        except SectionDeliveryError as exc:
            last_error = exc
            continue
    raise SectionDeliveryError(
        f"section claim ledger failed after {LEDGER_MAX_ATTEMPTS} attempts: {last_error}"
    )


def run_section_claim_ledger_sequence(
    *,
    workspace: Path,
    slug: str,
    delivery: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    generate_text: Callable[..., str],
    resume: bool = True,
) -> dict[str, Any]:
    if not callable(generate_text):
        raise SectionDeliveryError("generate_text must be callable")
    resolved = validate_resolved_delivery(delivery)
    outputs: list[dict[str, Any]] = []
    generated_count = 0
    resumed_count = 0
    for section_id in resolved["section_order"]:
        package = build_section_claim_package(
            resolved,
            link_contracts,
            context_manifest,
            section_id,
        )
        output = (
            load_section_ledger_checkpoint(workspace, slug, package)
            if resume
            else None
        )
        if output is None:
            output = _generate_one_section_ledger(package, generate_text)
            persist_section_ledger_checkpoint(workspace, slug, package, output)
            generated_count += 1
        else:
            resumed_count += 1
        outputs.append(output)
    return {
        "version": CONTRACT_VERSION,
        "delivery_sha256": resolved["delivery_sha256"],
        "draft_sha256": resolved["draft_sha256"],
        "section_order": list(resolved["section_order"]),
        "outputs": outputs,
        "generated_count": generated_count,
        "resumed_count": resumed_count,
        "complete": len(outputs) == len(resolved["section_order"]),
    }


def merge_section_claim_ledgers(
    delivery: dict[str, Any],
    link_contracts: dict[str, Any],
    context_manifest: dict[str, Any],
    ledger_run: dict[str, Any],
) -> dict[str, Any]:
    resolved = validate_resolved_delivery(delivery)
    if not isinstance(ledger_run, dict) or ledger_run.get("version") != CONTRACT_VERSION:
        raise SectionDeliveryError("ledger_run must be a version 1 object")
    if ledger_run.get("delivery_sha256") != resolved["delivery_sha256"]:
        raise SectionDeliveryError("ledger_run delivery SHA mismatch")
    if ledger_run.get("draft_sha256") != resolved["draft_sha256"]:
        raise SectionDeliveryError("ledger_run draft SHA mismatch")
    if ledger_run.get("section_order") != resolved["section_order"]:
        raise SectionDeliveryError("ledger_run section order mismatch")
    outputs = ledger_run.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(resolved["section_order"]):
        raise SectionDeliveryError("ledger_run outputs are incomplete")
    if [item.get("section_id") for item in outputs if isinstance(item, dict)] != resolved[
        "section_order"
    ]:
        raise SectionDeliveryError("ledger_run outputs are out of order")
    sentence_by_id = {
        item["sentence_id"]: item for item in resolved["sentences"]
    }
    claims: list[dict[str, Any]] = []
    identities: set[tuple[str, str, tuple[str, ...]]] = set()
    for output, section_id in zip(outputs, resolved["section_order"], strict=True):
        package = build_section_claim_package(
            resolved,
            link_contracts,
            context_manifest,
            section_id,
        )
        canonical_output = validate_section_claim_output(output, package)
        for claim in canonical_output["claims"]:
            sentence = sentence_by_id.get(claim.get("sentence_id"))
            if sentence is None or sentence["section_id"] != section_id:
                raise SectionDeliveryError("merged claim references the wrong section sentence")
            if claim.get("claim_text") != sentence["text"]:
                raise SectionDeliveryError("merged claim_text differs from final body sentence")
            evidence_ids = claim.get("evidence_ids")
            identity = (
                claim["sentence_id"],
                claim["claim_type"],
                tuple(evidence_ids),
            )
            if identity in identities:
                raise SectionDeliveryError("merged ledger contains a duplicate claim")
            identities.add(identity)
            claims.append({
                "sentence_id": claim["sentence_id"],
                "claim_text": claim["claim_text"],
                "claim_type": claim["claim_type"],
                "evidence_ids": list(evidence_ids),
            })
    claims.sort(key=lambda item: (int(item["sentence_id"][1:]), item["claim_type"]))
    return {
        "version": CONTRACT_VERSION,
        "draft_sha256": resolved["draft_sha256"],
        "claims": claims,
    }
