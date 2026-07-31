import asyncio
import json
from dataclasses import replace

import pytest

from seo_ops.services.sectional_legacy_adapter import (
    SectionalLegacyAdapterError,
    build_legacy_assembly_metadata,
    run_legacy_sectional_rollout,
)


def _run_rollout(**kwargs):
    return asyncio.run(run_legacy_sectional_rollout(**kwargs))


def _draft() -> str:
    return """---
Title: Professional Ceiling Marking Tools
Slug: professional-ceiling-marking-tools
Author: Example Tools
Summary: A practical guide to comparing ceiling marking tools while keeping specifications, evidence, and recommendation limits clear for professional teams.
Tags: ceiling marking, worksite tools, product selection
Page Type: Cluster Content
SEO Title: Professional Ceiling Marking Tools for Worksite Teams
SEO Description: Professional ceiling marking guidance helps worksite teams compare related tools, review visibility needs, and keep catalog recommendations accurate and clear.
SEO Keywords: professional ceiling marking tools, worksite marking
---

# Professional Ceiling Marking Tools

Legacy body.
"""


def test_build_metadata_from_legacy_frontmatter():
    metadata = build_legacy_assembly_metadata(
        _draft(),
        topic="Professional Ceiling Marking Tools",
        slug="professional-ceiling-marking-tools",
        author="Fallback Author",
        tier="Cluster Content",
    )
    assert metadata["title"] == "Professional Ceiling Marking Tools"
    assert metadata["author"] == "Example Tools"
    assert metadata["tags"] == ["ceiling marking", "worksite tools", "product selection"]
    assert metadata["target_words"] == {"min": 1200, "max": 1920}


def test_metadata_rejects_missing_frontmatter():
    with pytest.raises(SectionalLegacyAdapterError, match="missing frontmatter"):
        build_legacy_assembly_metadata(
            "# Body",
            topic="Topic",
            slug="topic",
            author="Author",
            tier="Cluster Content",
        )


def test_off_mode_returns_legacy_only_without_reading_inputs(tmp_path, settings):
    result = _run_rollout(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        tier="Cluster Content",
        intent="compare tools",
        guidance="",
        workspace=tmp_path,
        slug="professional-ceiling-marking-tools",
        contracts={},
        formal_draft_path=tmp_path / "missing.md",
        formal_claim_path=tmp_path / "missing.json",
        settings=settings,
        generate_text_async=lambda *args, **kwargs: None,
    )
    assert result["status"] == "legacy_only"
    assert result["decision"]["reason_code"] == "sectional_rollout_off"


def test_shadow_mode_runs_pipeline_but_never_promotes(tmp_path, settings, monkeypatch):
    draft_path = tmp_path / "drafts" / "formal.md"
    claim_path = tmp_path / "research" / "claim.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    draft_path.write_text(_draft(), encoding="utf-8")
    claim_path.write_text(json.dumps({"draft_sha256": "unused", "claims": []}), encoding="utf-8")
    (tmp_path / "context").mkdir()
    (tmp_path / "products").mkdir()
    (tmp_path / "context" / "internal-links-map.md").write_text("links", encoding="utf-8")
    (tmp_path / "products" / "live_products_report.md").write_text("products", encoding="utf-8")
    configured = replace(settings, sectional_writing_mode="shadow")
    assembly = {
        "topic": "Professional Ceiling Marking Tools",
        "assembly_sha256": "a" * 64,
    }
    pipeline = {"assembly": assembly, "run_metrics": {}, "result_sha256": "b" * 64}
    comparison = {
        "recommendation": "eligible_for_single_action_promotion",
        "report_sha256": "c" * 64,
    }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_sectional_shadow_candidate",
        lambda **kwargs: pipeline,
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.build_shadow_comparison",
        lambda **kwargs: comparison,
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.persist_shadow_comparison",
        lambda *args, **kwargs: str(tmp_path / "comparison.json"),
    )

    result = _run_rollout(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        tier="Cluster Content",
        intent="compare tools",
        guidance="",
        workspace=tmp_path,
        slug="professional-ceiling-marking-tools",
        contracts={
            "brief": {"research_brief_excerpt": "H2: Example (100 words)"},
            "cards": {"all_cards": []},
        },
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )
    assert result["status"] == "shadow_complete"
    assert result["decision"]["promotion_allowed"] is False


def test_action_mode_promotes_only_allowlisted_action(tmp_path, settings, monkeypatch):
    draft_path = tmp_path / "drafts" / "formal.md"
    claim_path = tmp_path / "research" / "claim.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    draft_path.write_text(_draft(), encoding="utf-8")
    claim_path.write_text(json.dumps({"draft_sha256": "unused", "claims": []}), encoding="utf-8")
    (tmp_path / "context").mkdir()
    (tmp_path / "products").mkdir()
    (tmp_path / "context" / "internal-links-map.md").write_text("links", encoding="utf-8")
    (tmp_path / "products" / "live_products_report.md").write_text("products", encoding="utf-8")
    configured = replace(
        settings,
        sectional_writing_mode="action",
        sectional_action_allowlist=(3,),
    )
    assembly = {
        "topic": "Professional Ceiling Marking Tools",
        "assembly_sha256": "a" * 64,
    }
    pipeline = {"assembly": assembly, "run_metrics": {}, "result_sha256": "b" * 64}
    comparison = {
        "recommendation": "eligible_for_single_action_promotion",
        "report_sha256": "c" * 64,
    }
    promote_calls = []

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_sectional_shadow_candidate",
        lambda **kwargs: pipeline,
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.build_shadow_comparison",
        lambda **kwargs: comparison,
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.persist_shadow_comparison",
        lambda *args, **kwargs: str(tmp_path / "comparison.json"),
    )

    def promote(**kwargs):
        promote_calls.append(kwargs)
        return {"status": "promoted", "manifest_sha256": "d" * 64}

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.promote_sectional_assembly",
        promote,
    )
    contracts = {
        "brief": {"research_brief_excerpt": "H2: Example (100 words)"},
        "cards": {"all_cards": []},
    }
    result = _run_rollout(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        tier="Cluster Content",
        intent="compare tools",
        guidance="",
        workspace=tmp_path,
        slug="professional-ceiling-marking-tools",
        contracts=contracts,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )
    assert result["status"] == "promoted"
    assert len(promote_calls) == 1

    denied = _run_rollout(
        action_id=4,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        tier="Cluster Content",
        intent="compare tools",
        guidance="",
        workspace=tmp_path,
        slug="professional-ceiling-marking-tools",
        contracts=contracts,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )
    assert denied["status"] == "legacy_only"
    assert denied["decision"]["reason_code"] == "action_not_allowlisted"
    assert len(promote_calls) == 1


def test_action_mode_keeps_legacy_when_comparison_has_blockers(
    tmp_path,
    settings,
    monkeypatch,
):
    draft_path = tmp_path / "drafts" / "formal.md"
    claim_path = tmp_path / "research" / "claim.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    draft_path.write_text(_draft(), encoding="utf-8")
    claim_path.write_text(json.dumps({"draft_sha256": "unused", "claims": []}), encoding="utf-8")
    (tmp_path / "context").mkdir()
    (tmp_path / "products").mkdir()
    (tmp_path / "context" / "internal-links-map.md").write_text("links", encoding="utf-8")
    (tmp_path / "products" / "live_products_report.md").write_text("products", encoding="utf-8")
    configured = replace(
        settings,
        sectional_writing_mode="action",
        sectional_action_allowlist=(3,),
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_sectional_shadow_candidate",
        lambda **kwargs: {
            "assembly": {"topic": "Professional Ceiling Marking Tools"},
            "run_metrics": {},
            "result_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.build_shadow_comparison",
        lambda **kwargs: {
            "recommendation": "keep_legacy",
            "blockers": ["claim_coverage_regression"],
            "report_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.persist_shadow_comparison",
        lambda *args, **kwargs: str(tmp_path / "comparison.json"),
    )
    result = _run_rollout(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        tier="Cluster Content",
        intent="compare tools",
        guidance="",
        workspace=tmp_path,
        slug="professional-ceiling-marking-tools",
        contracts={
            "brief": {"research_brief_excerpt": "H2: Example (100 words)"},
            "cards": {"all_cards": []},
        },
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )
    assert result["status"] == "kept_legacy"
    assert result["promotion_blockers"] == ["claim_coverage_regression"]


def test_missing_catalog_input_fails_closed(tmp_path, settings):
    configured = replace(settings, sectional_writing_mode="shadow")
    draft_path = tmp_path / "draft.md"
    claim_path = tmp_path / "claim.json"
    draft_path.write_text(_draft(), encoding="utf-8")
    claim_path.write_text("{}", encoding="utf-8")
    with pytest.raises(SectionalLegacyAdapterError, match="catalog input"):
        _run_rollout(
            action_id=3,
            topic="Professional Ceiling Marking Tools",
            author="Example Tools",
            tier="Cluster Content",
            intent="compare tools",
            guidance="",
            workspace=tmp_path,
            slug="professional-ceiling-marking-tools",
            contracts={
                "brief": {"research_brief_excerpt": "H2: Example (100 words)"},
                "cards": {"all_cards": []},
            },
            formal_draft_path=draft_path,
            formal_claim_path=claim_path,
            settings=configured,
            generate_text_async=lambda *args, **kwargs: None,
        )
