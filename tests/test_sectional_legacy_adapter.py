import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from seo_ops.services.legacy_workflow import stage_sectional_shadow_existing_pair
from seo_ops.services.sectional_assembly import validate_assembly_metadata
from seo_ops.services.sectional_legacy_adapter import (
    SectionalLegacyAdapterError,
    build_legacy_assembly_metadata,
    run_existing_legacy_sectional_shadow,
    run_legacy_sectional_rollout,
)


def _run_rollout(**kwargs):
    return asyncio.run(run_legacy_sectional_rollout(**kwargs))


def _run_existing_shadow(**kwargs):
    return asyncio.run(run_existing_legacy_sectional_shadow(**kwargs))


def _run_existing_stage(**kwargs):
    return asyncio.run(stage_sectional_shadow_existing_pair(**kwargs))


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


def test_build_metadata_decodes_quoted_legacy_frontmatter():
    draft = """---
title: "Professional Ceiling Marking Tools"
author: 'Example Tools'
summary: "Choose a pointer for Bob's commercial crew."
tags: 'ceiling marking, worksite tools, product selection'
seo title: "Professional Ceiling Marking Tools"
seo description: 'Bob''s practical ceiling marking guide.'
seo keywords: "ceiling marking, laser pointer"
---

Legacy body.
"""

    metadata = build_legacy_assembly_metadata(
        draft,
        topic="Professional Ceiling Marking Tools",
        slug="professional-ceiling-marking-tools",
        author="Fallback Author",
        tier="Cluster Content",
    )

    assert metadata["title"] == "Professional Ceiling Marking Tools"
    assert metadata["author"] == "Example Tools"
    assert metadata["summary"] == "Choose a pointer for Bob's commercial crew."
    assert metadata["seo_description"] == "Bob's practical ceiling marking guide."
    assert metadata["tags"] == [
        "ceiling marking",
        "worksite tools",
        "product selection",
    ]
    assert metadata["seo_keywords"] == ["ceiling marking", "laser pointer"]


def test_build_metadata_rejects_malformed_frontmatter_quotes():
    draft = """---
title: "Professional Ceiling Marking Tools'
---

Legacy body.
"""

    with pytest.raises(SectionalLegacyAdapterError, match="malformed quotes"):
        build_legacy_assembly_metadata(
            draft,
            topic="Professional Ceiling Marking Tools",
            slug="professional-ceiling-marking-tools",
            author="Fallback Author",
            tier="Cluster Content",
        )


def test_build_metadata_keeps_plain_scalar_ending_in_apostrophe():
    draft = _draft().replace(
        "Summary: A practical guide",
        "Summary: Professionals' practical guide",
    )

    metadata = build_legacy_assembly_metadata(
        draft,
        topic="Professional Ceiling Marking Tools",
        slug="professional-ceiling-marking-tools",
        author="Fallback Author",
        tier="Cluster Content",
    )

    assert metadata["summary"].startswith("Professionals' practical guide")


def test_sparse_quoted_legacy_frontmatter_builds_valid_sectional_metadata():
    topic = "Laser Pointer for Pointing Above Ceilings in Commercial Construction"
    draft = """---
title: "Laser Pointer for Pointing Above Ceilings in Commercial Construction"
description: "Find the best laser pointer for pointing above ceilings in commercial construction. Learn safety, OSHA compliant options, green 532nm vs red, and what to look for."
author: "LaserPointerHub"
---

Legacy body.
"""

    metadata = build_legacy_assembly_metadata(
        draft,
        topic=topic,
        slug="laser-pointer-for-pointing-above-ceilings-in-commercial-construction",
        author="Fallback Author",
        tier="Cluster Content",
    )
    validated = validate_assembly_metadata(metadata, expected_topic=topic)

    assert validated["title"] == topic
    assert validated["author"] == "LaserPointerHub"
    assert validated["summary"].startswith("Find the best laser pointer")
    assert validated["tags"] == [
        "laser pointer",
        "pointing above ceilings",
        "commercial construction",
    ]
    assert validated["seo_keywords"][0] == topic.casefold()
    assert 50 <= len(validated["seo_title"]) <= 60
    assert validated["seo_title"] == (
        "Laser Pointer for Commercial Construction: Above Ceilings"
    )
    assert 150 <= len(validated["seo_description"]) <= 160
    assert validated["seo_description"].endswith("what to look for.")


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


def _existing_pair_workspace(tmp_path):
    slug = "professional-ceiling-marking-tools"
    draft_path = tmp_path / "drafts" / f"{slug}-2026-07-31.md"
    claim_path = tmp_path / "research" / f"claim-ledger-{slug}.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    draft_path.write_text(_draft(), encoding="utf-8")
    claim_path.write_text(
        json.dumps({"draft_sha256": "unused", "claims": []}),
        encoding="utf-8",
    )
    contracts = {
        f"write-brief-{slug}.json": {
            "tier": "Cluster Content",
            "intent": "compare tools",
            "guidance": "Use existing evidence only.",
            "research_brief_excerpt": "H2: Example (100 words)",
        },
        f"coverage-contract-{slug}.json": {"sections": []},
        f"evidence-cards-{slug}.json": {"all_cards": []},
    }
    for name, payload in contracts.items():
        (tmp_path / "research" / name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    return slug, draft_path, claim_path


def test_existing_pair_shadow_uses_current_pair_without_mutating_it(
    tmp_path,
    settings,
    monkeypatch,
):
    slug, draft_path, claim_path = _existing_pair_workspace(tmp_path)
    configured = replace(settings, sectional_writing_mode="shadow")
    draft_before = draft_path.read_bytes()
    claim_before = claim_path.read_bytes()
    calls = []

    async def fake_rollout(**kwargs):
        calls.append(kwargs)
        return {
            "status": "shadow_complete",
            "decision": {"promotion_allowed": False},
        }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_legacy_sectional_rollout",
        fake_rollout,
    )
    result = _run_existing_shadow(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        workspace=tmp_path,
        slug=slug,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )

    assert len(calls) == 1
    assert calls[0]["formal_draft_path"] == draft_path
    assert calls[0]["formal_claim_path"] == claim_path
    assert calls[0]["tier"] == "Cluster Content"
    assert calls[0]["contracts"]["brief"]["intent"] == "compare tools"
    assert draft_path.read_bytes() == draft_before
    assert claim_path.read_bytes() == claim_before
    assert result["formal_pair"]["unchanged"] is True
    assert result["formal_pair"]["draft_sha256_before"] == (
        result["formal_pair"]["draft_sha256_after"]
    )
    assert result["formal_pair"]["claim_sha256_before"] == (
        result["formal_pair"]["claim_sha256_after"]
    )
    assert result["existing_pair_inputs"] == {
        "tier": "Cluster Content",
        "tier_source": "write_brief",
    }


def test_existing_pair_shadow_recovers_empty_brief_tier_from_matching_w1b_state(
    tmp_path,
    settings,
    monkeypatch,
):
    slug, draft_path, _ = _existing_pair_workspace(tmp_path)
    brief_path = tmp_path / "research" / f"write-brief-{slug}.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["tier"] = ""
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    state_path = tmp_path / "reports" / f"w2-state-{slug}.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "precheck_tier": "Cluster Content",
                "precheck_draft_sha256": hashlib.sha256(
                    draft_path.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    configured = replace(settings, sectional_writing_mode="shadow")
    calls = []

    async def fake_rollout(**kwargs):
        calls.append(kwargs)
        return {
            "status": "shadow_complete",
            "decision": {"promotion_allowed": False},
        }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_legacy_sectional_rollout",
        fake_rollout,
    )
    result = _run_existing_shadow(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        workspace=tmp_path,
        slug=slug,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )

    assert calls[0]["tier"] == "Cluster Content"
    assert result["existing_pair_inputs"] == {
        "tier": "Cluster Content",
        "tier_source": "matching_w1b_state",
    }


def test_existing_pair_shadow_rejects_stale_w1b_tier_fallback(
    tmp_path,
    settings,
):
    slug, _, _ = _existing_pair_workspace(tmp_path)
    brief_path = tmp_path / "research" / f"write-brief-{slug}.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["tier"] = ""
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    state_path = tmp_path / "reports" / f"w2-state-{slug}.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "precheck_tier": "Cluster Content",
                "precheck_draft_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    configured = replace(settings, sectional_writing_mode="shadow")

    with pytest.raises(
        SectionalLegacyAdapterError,
        match="does not belong to the current formal draft",
    ):
        _run_existing_shadow(
            action_id=3,
            topic="Professional Ceiling Marking Tools",
            author="Example Tools",
            workspace=tmp_path,
            slug=slug,
            settings=configured,
            generate_text_async=lambda *args, **kwargs: None,
        )


def test_existing_pair_shadow_rejects_conflicting_matching_tiers(
    tmp_path,
    settings,
):
    slug, draft_path, _ = _existing_pair_workspace(tmp_path)
    state_path = tmp_path / "reports" / f"w2-state-{slug}.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "precheck_tier": "Pillar Page",
                "precheck_draft_sha256": hashlib.sha256(
                    draft_path.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    configured = replace(settings, sectional_writing_mode="shadow")

    with pytest.raises(
        SectionalLegacyAdapterError,
        match="conflicts with the matching W1b state tier",
    ):
        _run_existing_shadow(
            action_id=3,
            topic="Professional Ceiling Marking Tools",
            author="Example Tools",
            workspace=tmp_path,
            slug=slug,
            settings=configured,
            generate_text_async=lambda *args, **kwargs: None,
        )


def test_existing_pair_shadow_ignores_stale_state_tier_when_brief_tier_is_valid(
    tmp_path,
    settings,
    monkeypatch,
):
    slug, _, _ = _existing_pair_workspace(tmp_path)
    state_path = tmp_path / "reports" / f"w2-state-{slug}.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "precheck_tier": "obsolete-unsupported-tier",
                "precheck_draft_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    configured = replace(settings, sectional_writing_mode="shadow")
    calls = []

    async def fake_rollout(**kwargs):
        calls.append(kwargs)
        return {
            "status": "shadow_complete",
            "decision": {"promotion_allowed": False},
        }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_legacy_sectional_rollout",
        fake_rollout,
    )
    result = _run_existing_shadow(
        action_id=3,
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        workspace=tmp_path,
        slug=slug,
        settings=configured,
        generate_text_async=lambda *args, **kwargs: None,
    )

    assert calls[0]["tier"] == "Cluster Content"
    assert result["existing_pair_inputs"]["tier_source"] == "write_brief"


def test_existing_pair_shadow_requires_shadow_mode(tmp_path, settings):
    with pytest.raises(SectionalLegacyAdapterError, match="requires.*shadow"):
        _run_existing_shadow(
            action_id=3,
            topic="Professional Ceiling Marking Tools",
            author="Example Tools",
            workspace=tmp_path,
            slug="professional-ceiling-marking-tools",
            settings=settings,
            generate_text_async=lambda *args, **kwargs: None,
        )


def test_existing_pair_shadow_restores_formal_pair_after_mutation(
    tmp_path,
    settings,
    monkeypatch,
):
    slug, draft_path, claim_path = _existing_pair_workspace(tmp_path)
    configured = replace(settings, sectional_writing_mode="shadow")
    draft_before = draft_path.read_bytes()
    claim_before = claim_path.read_bytes()

    async def mutate_formal_pair(**kwargs):
        kwargs["formal_draft_path"].write_text("changed", encoding="utf-8")
        kwargs["formal_claim_path"].write_text("{}", encoding="utf-8")
        return {
            "status": "shadow_complete",
            "decision": {"promotion_allowed": False},
        }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_legacy_sectional_rollout",
        mutate_formal_pair,
    )
    with pytest.raises(SectionalLegacyAdapterError, match="changed.*restored"):
        _run_existing_shadow(
            action_id=3,
            topic="Professional Ceiling Marking Tools",
            author="Example Tools",
            workspace=tmp_path,
            slug=slug,
            settings=configured,
            generate_text_async=lambda *args, **kwargs: None,
        )
    assert draft_path.read_bytes() == draft_before
    assert claim_path.read_bytes() == claim_before


def test_existing_pair_stage_requires_shadow_mode(tmp_path, settings):
    result = _run_existing_stage(
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        workspace=tmp_path,
        settings=settings,
        action_id=3,
    )
    assert result["success"] is False
    assert "SECTIONAL_WRITING_MODE=shadow" in result["error"]


def test_existing_pair_stage_delegates_without_changing_legacy_stage(
    tmp_path,
    settings,
    monkeypatch,
):
    configured = replace(settings, sectional_writing_mode="shadow")
    calls = []

    async def fake_existing_shadow(**kwargs):
        calls.append(kwargs)
        return {
            "status": "shadow_complete",
            "decision": {"promotion_allowed": False},
            "formal_pair": {"unchanged": True},
        }

    monkeypatch.setattr(
        "seo_ops.services.sectional_legacy_adapter.run_existing_legacy_sectional_shadow",
        fake_existing_shadow,
    )
    result = _run_existing_stage(
        topic="Professional Ceiling Marking Tools",
        author="Example Tools",
        workspace=tmp_path,
        settings=configured,
        action_id=3,
    )

    assert result["success"] is True
    assert result["formal_pair"] == {"unchanged": True}
    assert len(calls) == 1
    assert calls[0]["action_id"] == 3
    assert calls[0]["slug"] == "professional-ceiling-marking-tools"


def test_existing_pair_shadow_web_route_is_post_only(settings):
    from seo_ops.web.app import create_app

    app = create_app(settings)
    route = next(
        item
        for item in app.routes
        if getattr(item, "path", "")
        == "/actions/{action_id}/legacy/stage/sectional-shadow"
    )
    assert route.methods == {"POST"}


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
