"""End-to-end shadow orchestration for the sectional writing candidate.

The runner persists only sectional research/checkpoint artifacts.  It never
writes the formal Legacy draft or claim ledger; promotion is a separate,
allowlisted operation in :mod:`sectional_rollout`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from seo_ops.services.ai import AIEmptyTextError
from seo_ops.services.sectional_assembly import (
    SectionAssemblyError,
    persist_sectional_assembly,
    validate_assembly_metadata,
)
from seo_ops.services.sectional_context import (
    build_candidate_registry,
    persist_shadow_context,
    resolve_shadow_opportunities,
)
from seo_ops.services.sectional_delivery import (
    merge_section_claim_ledgers,
    persist_resolved_delivery,
    resolve_section_placeholders,
    run_section_claim_ledger_sequence,
)
from seo_ops.services.sectional_generation import (
    run_article_frame_generation,
    run_section_generation_sequence,
    validate_claim_evidence_strength,
)
from seo_ops.services.sectional_writing import (
    ContractValidationError,
    build_contract_bundle_from_brief,
    persist_contract_bundle,
)

PIPELINE_VERSION = 1


class SectionalPipelineError(RuntimeError):
    """Raised when a shadow candidate cannot be built safely."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SectionalPipelineError(f"{field} must be a non-empty string")
    return value.strip()


def run_sectional_shadow_candidate(
    *,
    workspace: Path,
    slug: str,
    topic: str,
    tier: str,
    intent: str,
    brief_text: str,
    internal_links_map: str,
    product_report: str,
    evidence_cards: dict[str, Any],
    metadata: dict[str, Any],
    generate_text: Callable[..., str],
    guidance: str = "",
    current_url: str = "",
    current_slug: str = "",
    resume: bool = True,
    max_ai_calls: int = 24,
) -> dict[str, Any]:
    """Build and persist one complete sectional candidate in shadow storage."""
    clean_topic = _clean_text(topic, "topic")
    clean_slug = _clean_text(slug, "slug")
    clean_brief = _clean_text(brief_text, "brief_text")
    clean_links = _clean_text(internal_links_map, "internal_links_map")
    clean_products = _clean_text(product_report, "product_report")
    if not isinstance(evidence_cards, dict):
        raise SectionalPipelineError("evidence_cards must be an object")
    if not callable(generate_text):
        raise SectionalPipelineError("generate_text must be callable")
    if (
        not isinstance(max_ai_calls, int)
        or isinstance(max_ai_calls, bool)
        or not 1 <= max_ai_calls <= 100
    ):
        raise SectionalPipelineError("max_ai_calls must be an integer from 1 to 100")
    try:
        clean_metadata = validate_assembly_metadata(metadata, expected_topic=clean_topic)
        bundle = build_contract_bundle_from_brief(
            topic=clean_topic,
            tier=str(tier or ""),
            intent=str(intent or ""),
            brief_text=clean_brief,
            guidance=str(guidance or ""),
        )
        contract_paths = persist_contract_bundle(workspace, clean_slug, bundle)
        registry = build_candidate_registry(
            internal_links_map=clean_links,
            product_report=clean_products,
            evidence_cards=evidence_cards,
            current_url=current_url,
            current_slug=current_slug,
        )
        shadow = resolve_shadow_opportunities(
            bundle["section_contracts"],
            bundle["section_link_contracts"],
            registry,
        )
        shadow_paths = persist_shadow_context(
            workspace,
            clean_slug,
            shadow,
            bundle["section_contracts"],
        )
    except (ContractValidationError, SectionAssemblyError) as exc:
        raise SectionalPipelineError(f"sectional contract/context setup failed: {exc}") from exc

    counters = {
        "ai_calls": 0,
        "prompt_chars": 0,
        "max_tokens_requested": 0,
        "completion_tokens": None,
        "empty_response_count": 0,
    }

    def counted_generate(system: str, user: str, **kwargs: Any) -> str:
        if counters["ai_calls"] >= max_ai_calls:
            raise SectionalPipelineError(f"sectional AI call budget exhausted ({max_ai_calls})")
        counters["ai_calls"] += 1
        counters["prompt_chars"] += len(system) + len(user)
        max_tokens = kwargs.get("max_tokens", 0)
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
            counters["max_tokens_requested"] += max(0, max_tokens)
        try:
            return generate_text(system, user, **kwargs)
        except AIEmptyTextError:
            counters["empty_response_count"] += 1
            raise

    try:
        section_run = run_section_generation_sequence(
            workspace=workspace,
            slug=clean_slug,
            section_contracts=bundle["section_contracts"],
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=counted_generate,
            resume=resume,
        )
        frame_run = run_article_frame_generation(
            workspace=workspace,
            slug=clean_slug,
            section_run=section_run,
            generate_text=counted_generate,
            resume=resume,
        )
        article_frame = frame_run["output"]
        section_run = dict(section_run)
        section_run["article_frame"] = article_frame
        delivery = resolve_section_placeholders(
            section_run,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            article_frame=article_frame,
        )
        delivery_path = persist_resolved_delivery(workspace, clean_slug, delivery)
        ledger_run = run_section_claim_ledger_sequence(
            workspace=workspace,
            slug=clean_slug,
            delivery=delivery,
            link_contracts=shadow["section_link_contracts"],
            context_manifest=shadow["context_manifest"],
            generate_text=counted_generate,
            resume=resume,
        )
        claim_ledger = merge_section_claim_ledgers(
            delivery,
            shadow["section_link_contracts"],
            shadow["context_manifest"],
            ledger_run,
        )
        validate_claim_evidence_strength(
            claim_ledger,
            shadow["context_manifest"],
        )
        from seo_ops.services.sectional_assembly import assemble_sectional_article

        assembly = assemble_sectional_article(delivery, clean_metadata, claim_ledger)
        assembly_paths = persist_sectional_assembly(workspace, assembly)
    except Exception as exc:
        raise SectionalPipelineError(f"sectional shadow candidate failed: {exc}") from exc

    expected_first_attempt_calls = (
        section_run["generated_count"]
        + int(bool(frame_run["generated"]))
        + ledger_run["generated_count"]
    )
    counters["retry_count"] = max(
        0,
        counters["ai_calls"] - expected_first_attempt_calls,
    )
    result = {
        "version": PIPELINE_VERSION,
        "topic": clean_topic,
        "slug": clean_slug,
        "contract_paths": contract_paths,
        "shadow_paths": shadow_paths,
        "delivery_path": delivery_path,
        "assembly_paths": assembly_paths,
        "section_count": len(section_run["section_order"]),
        "generated_sections": section_run["generated_count"],
        "resumed_sections": section_run["resumed_count"],
        "section_word_count_retries": section_run["word_count_retry_count"],
        "section_link_layout_retries": section_run["link_layout_retry_count"],
        "section_required_link_retries": section_run["required_link_retry_count"],
        "section_technical_consistency_retries": section_run["technical_consistency_retry_count"],
        "frame_generated": bool(frame_run["generated"]),
        "frame_resumed": bool(frame_run["resumed"]),
        "section_evidence_strength_retries": section_run["evidence_strength_retry_count"],
        "frame_evidence_strength_repaired": bool(frame_run["evidence_strength_repaired"]),
        "frame_evidence_strength_retries": frame_run["evidence_strength_retry_count"],
        "generated_ledgers": ledger_run["generated_count"],
        "resumed_ledgers": ledger_run["resumed_count"],
        "run_metrics": counters,
        "max_ai_calls": max_ai_calls,
        "assembly": assembly,
    }
    result["result_sha256"] = _digest(result)
    return validate_sectional_pipeline_result(result)


def validate_sectional_pipeline_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("version") != PIPELINE_VERSION:
        raise SectionalPipelineError("pipeline result must be a version 1 object")
    _clean_text(result.get("topic"), "pipeline result topic")
    _clean_text(result.get("slug"), "pipeline result slug")
    try:
        from seo_ops.services.sectional_assembly import validate_sectional_assembly

        assembly = validate_sectional_assembly(result.get("assembly"))
    except SectionAssemblyError as exc:
        raise SectionalPipelineError(f"pipeline result assembly is invalid: {exc}") from exc
    if assembly["topic"] != result["topic"]:
        raise SectionalPipelineError("pipeline result topic does not match assembly")
    for field in (
        "section_count",
        "generated_sections",
        "resumed_sections",
        "section_word_count_retries",
        "section_evidence_strength_retries",
        "generated_ledgers",
        "resumed_ledgers",
        "max_ai_calls",
    ):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SectionalPipelineError(f"pipeline result {field} is invalid")
    frame_retry_count = result.get("frame_evidence_strength_retries")
    if frame_retry_count is not None and (
        not isinstance(frame_retry_count, int)
        or isinstance(frame_retry_count, bool)
        or frame_retry_count < 0
    ):
        raise SectionalPipelineError("pipeline result frame_evidence_strength_retries is invalid")
    link_layout_retry_count = result.get("section_link_layout_retries")
    if link_layout_retry_count is not None and (
        not isinstance(link_layout_retry_count, int)
        or isinstance(link_layout_retry_count, bool)
        or link_layout_retry_count < 0
    ):
        raise SectionalPipelineError("pipeline result section_link_layout_retries is invalid")
    required_link_retry_count = result.get("section_required_link_retries")
    if required_link_retry_count is not None and (
        not isinstance(required_link_retry_count, int)
        or isinstance(required_link_retry_count, bool)
        or required_link_retry_count < 0
    ):
        raise SectionalPipelineError("pipeline result section_required_link_retries is invalid")
    technical_retry_count = result.get("section_technical_consistency_retries")
    if technical_retry_count is not None and (
        not isinstance(technical_retry_count, int)
        or isinstance(technical_retry_count, bool)
        or technical_retry_count < 0
    ):
        raise SectionalPipelineError(
            "pipeline result section_technical_consistency_retries is invalid"
        )
    if result["generated_sections"] + result["resumed_sections"] != result["section_count"]:
        raise SectionalPipelineError("pipeline section counts do not reconcile")
    if result["generated_ledgers"] + result["resumed_ledgers"] != len(
        assembly["delivery"]["section_order"]
    ):
        raise SectionalPipelineError("pipeline ledger counts do not reconcile")
    if not isinstance(result.get("frame_evidence_strength_repaired"), bool):
        raise SectionalPipelineError("pipeline result frame_evidence_strength_repaired is invalid")
    counters = result.get("run_metrics")
    if not isinstance(counters, dict):
        raise SectionalPipelineError("pipeline run_metrics are missing")
    if not 1 <= result["max_ai_calls"] <= 100:
        raise SectionalPipelineError("pipeline result max_ai_calls is invalid")
    for field in (
        "ai_calls",
        "prompt_chars",
        "max_tokens_requested",
        "retry_count",
        "empty_response_count",
    ):
        value = counters.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SectionalPipelineError(f"pipeline run_metrics.{field} is invalid")
    completion_tokens = counters.get("completion_tokens")
    if completion_tokens is not None and (
        not isinstance(completion_tokens, int)
        or isinstance(completion_tokens, bool)
        or completion_tokens < 0
    ):
        raise SectionalPipelineError("pipeline run_metrics.completion_tokens is invalid")
    digest = result.get("result_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SectionalPipelineError("pipeline result SHA is invalid")
    unsigned = dict(result)
    unsigned.pop("result_sha256")
    if digest != _digest(unsigned):
        raise SectionalPipelineError("pipeline result SHA mismatch")
    return result
