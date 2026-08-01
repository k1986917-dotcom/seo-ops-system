import hashlib
import json
from pathlib import Path

import pytest

from seo_ops.services.sectional_generation import (
    FRAME_INTRO_MARKER,
    SECTION_DECISIONS_MARKER,
    SECTION_MARKDOWN_MARKER,
)
from seo_ops.services.sectional_pipeline import (
    SectionalPipelineError,
    run_sectional_shadow_candidate,
    validate_sectional_pipeline_result,
)


def _inputs():
    from tests.test_sectional_assembly import _metadata

    articles = """# Internal Links

| Title | URL | Primary Keyword |
|---|---|---|
| Ceiling Visibility Planning | https://example.com/blog/ceiling-visibility | ceiling visibility |
| Ceiling Marking Tool Selection | https://example.com/blog/tool-selection | marking tool selection |
| Worksite Safety Procedures | https://example.com/blog/worksite-safety | worksite safety |
"""
    products = """# Products

| SKU | Title | URL | Application | Reach |
|---|---|---|---|---|
| T100 | Professional Ceiling Marking Tool | https://example.com/p-T100.html | ceiling marking selection | long range |
| T200 | Compact Worksite Marking Tool | https://example.com/p-T200.html | worksite marking | medium range |
"""
    evidence = {
        "all_cards": [
            {
                "evidence_id": "ev_visibility",
                "source_url": "https://source.example/visibility",
                "support": "Visibility planning should reflect the work area and viewing distance.",
                "concepts": ["ceiling visibility", "work area", "distance"],
                "claim_types": ["guidance"],
                "required": False,
            },
            {
                "evidence_id": "ev_selection",
                "source_url": "https://source.example/selection",
                "support": "Tool selection should compare handling, reach and documented catalog attributes.",
                "concepts": ["tool selection", "handling", "reach"],
                "claim_types": ["guidance"],
                "required": False,
            },
            {
                "evidence_id": "ev_safety",
                "source_url": "https://safety.example/procedure",
                "support": "Worksite safety procedures should be followed before equipment use.",
                "concepts": ["worksite safety", "procedure", "compliance"],
                "claim_types": ["safety"],
                "required": True,
            },
        ]
    }

    brief = """# Research Brief

H2: Why Clear Ceiling Marking Matters (100 words)
- Explain visibility and work-area context.

H2: Choosing the Right Ceiling Marking Tool (100 words)
- Compare related catalog options without inventing fit.

H2: Worksite Safety and Compliance (100 words)
- Keep procedural guidance evidence-bound.
"""
    return {
        "slug": "professional-ceiling-marking-tools",
        "topic": "Professional Ceiling Marking Tools",
        "tier": "Cluster Content",
        "intent": "Help professionals compare and use related marking tools.",
        "brief_text": brief,
        "internal_links_map": articles,
        "product_report": products,
        "evidence_cards": evidence,
        "metadata": _metadata(minimum=300, maximum=1200),
    }


def _generator(calls):
    from tests.test_sectional_delivery import (
        _claim_response,
        _frame_response,
    )

    used_internal_ids = set()

    def section_response(package):
        gates = package["link_gates"]
        used = {key: [] for key in gates}
        first_links = []
        second_links = []
        if gates["article_links"]["selected_ids"]:
            candidate_id = next(
                (
                    item
                    for item in gates["article_links"]["selected_ids"]
                    if item not in used_internal_ids
                ),
                None,
            )
            if candidate_id is not None:
                used_internal_ids.add(candidate_id)
                used["article_links"].append(candidate_id)
                first_links.append(
                    f"[[ARTICLE:{candidate_id}|{package['heading'].casefold()} guidance]]"
                )
        if gates["product_links"]["opportunity_state"] != "none":
            candidate_id = next(
                (
                    item
                    for item in gates["product_links"]["selected_ids"]
                    if item not in used_internal_ids
                ),
                None,
            )
            if candidate_id is not None:
                used_internal_ids.add(candidate_id)
                used["product_links"].append(candidate_id)
                second_links.append(f"[[PRODUCT:{candidate_id}|documented catalog option]]")
        if gates["external_citations"]["selected_ids"]:
            candidate_id = gates["external_citations"]["selected_ids"][0]
            used["external_citations"].append(candidate_id)
            second_links.append(f"[[CITE:{candidate_id}]]")
        heading = package["heading"]
        first = (
            f"{heading} begins with the specific reader question assigned to this section. "
            f"For {heading}, teams should examine the work area, visibility needs, and documented "
            "constraints before treating a general recommendation as a final equipment decision."
        )
        second = (
            f"For {heading}, the explanation should connect approved evidence and current catalog "
            f"details without inventing certification or purpose-built compatibility. The {heading} "
            "discussion therefore remains distinct while giving readers a practical next step."
        )
        if first_links:
            first += " " + " ".join(first_links)
        if second_links:
            second += " " + " ".join(second_links)
        decisions = {}
        for key, gate in gates.items():
            ids = used[key]
            if ids:
                reason = "used_approved_candidate"
            elif gate["opportunity_state"] == "none":
                reason = gate["reason_code"]
            else:
                reason = "not_needed_for_this_section"
            decisions[key] = {"used_ids": ids, "reason_code": reason}
        return (
            f"{SECTION_MARKDOWN_MARKER}\n"
            f"## {heading}\n\n{first}\n\n{second}\n"
            f"{SECTION_DECISIONS_MARKER}\n"
            f"{json.dumps(decisions, sort_keys=True)}"
        )

    def generate(system, user, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        if "SECTION CLAIM PACKAGE\n" in user:
            package = json.loads(user.split("SECTION CLAIM PACKAGE\n", 1)[1].split("\n\n", 1)[0])
            if not package["evidence"]:
                return json.dumps({"version": 1, "claims": []})
            return _claim_response(package)
        if "ARTICLE FRAME PACKAGE\n" in user:
            return _frame_response()
        if "SECTION PACKAGE\n" in user:
            package = json.loads(user.split("SECTION PACKAGE\n", 1)[1])
            return section_response(package)
        raise AssertionError("unexpected sectional prompt")

    return generate


def test_shadow_pipeline_builds_complete_candidate_without_formal_artifacts(tmp_path):
    calls = []
    result = run_sectional_shadow_candidate(
        workspace=tmp_path,
        generate_text=_generator(calls),
        resume=True,
        **_inputs(),
    )

    assert result["section_count"] == 3
    assert result["generated_sections"] == 3
    assert result["section_link_layout_retries"] == 0
    assert result["section_required_link_retries"] == 0
    assert result["section_product_fit_retries"] == 0
    assert result["section_technical_consistency_retries"] == 0
    assert result["frame_generated"] is True
    assert result["frame_authority_free_fallback_applied"] is False
    assert result["generated_ledgers"] == len(result["assembly"]["delivery"]["section_order"])
    assert result["run_metrics"]["ai_calls"] == len(calls)
    assert result["run_metrics"]["completion_tokens"] is None
    assert result["assembly"]["audit"]["blockers"] == []
    assert Path(result["delivery_path"]).exists()
    assert all(Path(path).exists() for path in result["assembly_paths"].values())
    assert not list((tmp_path / "drafts").glob("professional-ceiling-marking-tools-*.md"))
    assert not (
        tmp_path / "research" / "claim-ledger-professional-ceiling-marking-tools.json"
    ).exists()


def test_shadow_pipeline_resumes_all_valid_checkpoints_without_ai(tmp_path):
    first = run_sectional_shadow_candidate(
        workspace=tmp_path,
        generate_text=_generator([]),
        resume=True,
        **_inputs(),
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("all sectional checkpoints should resume")

    second = run_sectional_shadow_candidate(
        workspace=tmp_path,
        generate_text=must_not_run,
        resume=True,
        **_inputs(),
    )
    assert second["generated_sections"] == 0
    assert second["resumed_sections"] == first["section_count"]
    assert second["frame_resumed"] is True
    assert second["generated_ledgers"] == 0
    assert second["resumed_ledgers"] == len(second["assembly"]["delivery"]["section_order"])
    assert second["run_metrics"]["ai_calls"] == 0


def test_shadow_pipeline_wraps_generation_failure_and_keeps_formal_pair_absent(tmp_path):
    def fail(system, user, **kwargs):
        if SECTION_MARKDOWN_MARKER in system or "SECTION PACKAGE" in user:
            raise RuntimeError("provider unavailable")
        if FRAME_INTRO_MARKER in system:
            raise AssertionError("frame should not run")
        raise AssertionError("unexpected call")

    with pytest.raises(SectionalPipelineError, match="shadow candidate failed"):
        run_sectional_shadow_candidate(
            workspace=tmp_path,
            generate_text=fail,
            resume=False,
            **_inputs(),
        )
    assert not list((tmp_path / "drafts").glob("professional-ceiling-marking-tools-*.md"))


def test_shadow_pipeline_stops_at_ai_call_budget_without_formal_pair(tmp_path):
    with pytest.raises(SectionalPipelineError, match="AI call budget exhausted"):
        run_sectional_shadow_candidate(
            workspace=tmp_path,
            generate_text=_generator([]),
            resume=False,
            max_ai_calls=1,
            **_inputs(),
        )
    assert not list((tmp_path / "drafts").glob("professional-ceiling-marking-tools-*.md"))
    assert not (
        tmp_path / "research" / "claim-ledger-professional-ceiling-marking-tools.json"
    ).exists()


def test_pipeline_result_rejects_nested_tampering(tmp_path):
    result = run_sectional_shadow_candidate(
        workspace=tmp_path,
        generate_text=_generator([]),
        resume=False,
        **_inputs(),
    )
    result["assembly"]["draft_markdown"] += "tampered"
    with pytest.raises(SectionalPipelineError, match="assembly is invalid"):
        validate_sectional_pipeline_result(result)


def test_pipeline_result_accepts_legacy_version_one_without_new_retry_counts(tmp_path):
    result = run_sectional_shadow_candidate(
        workspace=tmp_path,
        generate_text=_generator([]),
        resume=False,
        **_inputs(),
    )
    result.pop("frame_evidence_strength_retries")
    result.pop("section_link_layout_retries")
    result.pop("section_required_link_retries")
    result.pop("section_product_fit_retries")
    result.pop("section_technical_consistency_retries")
    result.pop("frame_authority_free_fallback_applied")
    unsigned = dict(result)
    unsigned.pop("result_sha256")
    result["result_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert validate_sectional_pipeline_result(result) == result
