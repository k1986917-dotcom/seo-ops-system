from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.rules.research_workflow import (
    QUALIFICATION_STATUS_BLOCKED,
    QUALIFICATION_STATUS_QUALIFIED,
    RELATIONSHIP_DISTINCT,
    RESEARCH_METHOD_VERSION,
    assess_discovered_topic,
    current_topic_policy_block,
    qualify_candidate,
    topic_matches_research_branch,
)
from seo_ops.services.ai import AIResponse
from seo_ops.services.research_workflow import (
    ResearchUnavailable,
    _ai_intent_deduplicate_pool,
    _anchor_fit_for_source_observation,
    _content_search_items,
    _insert_candidates,
    _natural_hypothesis_seeds,
    _open_scheduler_family_specs,
    _previous_run_cooldown,
    _repeats_prior_candidate_primary_task,
    _select_diverse_seed_frontier,
    record_research_candidate_decision,
    research_topic_options,
    run_topic_research,
)
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def _prepare(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    run_analysis(1, settings)


def test_unstructured_branches_use_multiple_plan_lenses_without_fabricating_topics():
    seeds = _natural_hypothesis_seeds(
        "buy-quality",
        "laser pointer quality seller claims specification verification",
        dimension_key="failure",
        prior_queries=set(),
    )

    assert len(seeds) == 2
    assert len({seed["target_ref"] for seed in seeds}) == 2
    assert all(seed["discovery_only"] for seed in seeds)
    assert all(seed["hypothesis"]["topic"] == "" for seed in seeds)
    assert all(seed["hypothesis"]["intent"] == "" for seed in seeds)
    assert all('"laser pointer"' in seed["target_ref"] for seed in seeds)
    assert [seed["seed_source"] for seed in seeds] == [
        "plan_question_chain",
        "public_third_party_language_probe",
    ]
    assert seeds[0]["target_ref"] == (
        '"laser pointer" seller claims verification problems symptoms solutions'
    )
    assert seeds[1]["target_ref"] == (
        '"laser pointer" seller claims verification mistakes limitations alternatives '
        "forum reviews questions"
    )


def test_task_cards_precede_generic_lenses_and_skip_known_body_duplicates():
    branch_label = "laser pointer construction landscaping professional work"
    seeds = _natural_hypothesis_seeds(
        "use-professional",
        branch_label,
        dimension_key="audience",
        prior_queries=set(),
        content_items=[],
    )

    assert len(seeds) == 3
    assert [seed["seed_source"] for seed in seeds] == [
        "old_plan_role_task_frontier",
        "old_plan_public_constraint",
        "old_plan_role_task_frontier",
    ]
    assert all(seed["discovery_only"] for seed in seeds)
    assert all(isinstance(seed["task_card"], dict) for seed in seeds)
    assert {seed["task_card"]["role"] for seed in seeds} == {
        "arborist or tree-care professional",
        "building inspector",
        "alignment technician",
    }

    covered_content = [
        {
            "id": 1,
            "content_type": "blog",
            "title": "Using a Laser Pointer to Mark Pruning Locations From the Ground",
            "slug": "pruning-location",
            "seo_title": "Using a Laser Pointer to Mark Pruning Locations From the Ground",
            "canonical_url": "https://example.com/blog/pruning-location",
            "search_text": "Using a Laser Pointer to Mark Pruning Locations From the Ground",
            "coverage_text": "Using a Laser Pointer to Mark Pruning Locations From the Ground",
        }
    ]
    filtered = _natural_hypothesis_seeds(
        "use-professional",
        branch_label,
        dimension_key="audience",
        prior_queries=set(),
        content_items=covered_content,
    )
    assert all("arborist" not in seed["target_ref"] for seed in filtered)
    retained_cards = [seed["task_card"] for seed in filtered if seed["task_card"]]
    assert {card["preflight"]["status"] for card in retained_cards} == {
        QUALIFICATION_STATUS_QUALIFIED
    }
    assert len(retained_cards) == 2
    assert filtered[-1]["seed_source"] == "plan_question_chain"


def test_single_task_card_keeps_two_independent_old_plan_probes():
    seeds = _natural_hypothesis_seeds(
        "use-outdoor",
        "laser pointer camping hiking emergency signaling",
        dimension_key="journey",
        prior_queries=set(),
        content_items=[],
        max_seed_count=3,
    )

    assert len(seeds) == 3
    assert seeds[0]["task_card"]["task"] == (
        "assess whether a beam remains visible enough for an emergency signal"
    )
    assert [seed["seed_source"] for seed in seeds] == [
        "old_plan_public_pain",
        "plan_question_chain",
        "public_third_party_language_probe",
    ]
    assert all(seed["task_card"] is None for seed in seeds[1:])


def test_one_run_semantic_cooldown_is_soft_and_not_a_keyword_ban():
    candidates = [
        {
            "target_ref": "laser pointer battery voltage sag",
            "semantic_cluster": "battery_charging",
            "source_family": "community",
            "anchor_fit": "core",
            "_frontier_order": 0,
        },
        {
            "target_ref": "green laser pointer speckle pattern",
            "semantic_cluster": "beam_optics",
            "source_family": "paa_related",
            "anchor_fit": "core",
            "_frontier_order": 1,
        },
        {
            "target_ref": "protected 18650 physical fit",
            "semantic_cluster": "battery_charging",
            "source_family": "review",
            "anchor_fit": "core",
            "_frontier_order": 2,
        },
    ]

    selected = _select_diverse_seed_frontier(
        candidates,
        cooled_clusters={"battery_charging"},
        limit=2,
    )
    assert selected[0]["semantic_cluster"] == "beam_optics"
    assert selected[1]["semantic_cluster"] == "battery_charging"

    only_battery = _select_diverse_seed_frontier(
        candidates[:1],
        cooled_clusters={"battery_charging"},
        limit=1,
    )
    assert only_battery[0]["target_ref"] == "laser pointer battery voltage sag"


def test_generated_pointer_query_cannot_upgrade_an_off_anchor_source():
    assert (
        _anchor_fit_for_source_observation(
            "This video explains how to continuously frame in LightBurn for an engraving job.",
            '"laser pointer" How to continuously frame in LightBurn',
        )
        == "off_anchor"
    )


def test_core_source_frontier_beats_adjacent_source_diversity():
    candidates = [
        {
            "target_ref": "core one",
            "semantic_cluster": "emerging",
            "source_family": "paa_related",
            "anchor_fit": "core",
            "_frontier_order": 0,
        },
        {
            "target_ref": "core two",
            "semantic_cluster": "emerging",
            "source_family": "paa_related",
            "anchor_fit": "core",
            "_frontier_order": 1,
        },
        {
            "target_ref": "adjacent sight",
            "semantic_cluster": "emerging",
            "source_family": "social_or_video",
            "anchor_fit": "adjacent",
            "_frontier_order": 2,
        },
    ]
    selected = _select_diverse_seed_frontier(candidates, cooled_clusters=set(), limit=2)
    assert [item["target_ref"] for item in selected] == ["core one", "core two"]


def test_repeat_primary_task_is_removed_without_blocking_a_distinct_subtopic():
    previous = [
        {
            "title": "Using a Green Laser Pointer to Mark Pruning Locations from Ground Level",
            "intent": "Help an arborist mark tree pruning points and communicate them to a crew.",
        }
    ]
    assert _repeats_prior_candidate_primary_task(
        "How to use a green laser pointer for tree inspection and pruning communication",
        "Help arborists point out pruning recommendations to clients.",
        previous,
    )
    assert not _repeats_prior_candidate_primary_task(
        "Choosing a green laser pointer for astronomy",
        "Help observers compare visibility outdoors.",
        previous,
    )


def test_cooldown_reads_only_immediately_previous_completed_run(settings):
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO research_runs(
                site_id, status, budgets_json, started_at, completed_at, filters_json
            ) VALUES(1, 'success', '{}', ?, ?, ?)
            """,
            (
                "2026-07-18T00:00:00+00:00",
                "2026-07-18T00:01:00+00:00",
                json.dumps({"dominant_semantic_clusters": ["battery_charging"]}),
            ),
        )
    first = _previous_run_cooldown(settings, 1)
    assert first["clusters"] == ["battery_charging"]

    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO research_runs(
                site_id, status, budgets_json, started_at, completed_at, filters_json
            ) VALUES(1, 'success', '{}', ?, ?, ?)
            """,
            (
                "2026-07-18T01:00:00+00:00",
                "2026-07-18T01:01:00+00:00",
                json.dumps({"dominant_semantic_clusters": []}),
            ),
        )
    second = _previous_run_cooldown(settings, 1)
    assert second["clusters"] == []
    assert second["status"] == "no_dominant_cluster"


def test_source_observation_is_saved_then_used_as_next_run_seed(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        tavily_api_key="tavily-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=0,
        research_tavily_budget=5,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "use-professional"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "serpapi.com":
            query = str(request.url.params["q"])
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {"engine": "google", "q": query},
                    "organic_results": [],
                    "related_questions": [
                        {"question": "Can an arborist use a laser pointer to show a pruning cut?"}
                    ],
                    "related_searches": [
                        {"query": "laser pointer tree pruning crew communication"}
                    ],
                },
            )
        if request.url.host == "api.tavily.com":
            body = json.loads(request.content)
            query = str(body["query"])
            host = "reddit.com" if "reddit.com" in query else "example-competitor.com"
            return httpx.Response(
                200,
                json={
                    "query": query,
                    "results": [
                        {
                            "title": "Laser pointer pruning-location discussion",
                            "url": f"https://{host}/laser-pointer-pruning",
                            "content": "A crew needs a visible way to point at a pruning location from ground level.",
                            "score": 0.8,
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    transport = httpx.MockTransport(handler)
    first = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            dimension_key="audience",
            transport=transport,
        )
    )
    with connection(configured) as conn:
        first_filters = json.loads(
            conn.execute(
                "SELECT filters_json FROM research_runs WHERE id = ?", (first.run_id,)
            ).fetchone()["filters_json"]
        )
        observation_count = conn.execute(
            "SELECT COUNT(*) FROM research_seed_observations WHERE research_run_id = ?",
            (first.run_id,),
        ).fetchone()[0]
    assert first_filters["source_observations"]["stored"] > 0
    assert observation_count > 0

    second = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            dimension_key="audience",
            transport=transport,
        )
    )
    with connection(configured) as conn:
        second_filters = json.loads(
            conn.execute(
                "SELECT filters_json FROM research_runs WHERE id = ?", (second.run_id,)
            ).fetchone()["filters_json"]
        )
    assert second_filters["source_observations"]["selected_from_prior_runs"] >= 1
    assert second_filters["seed_frontier"][0]["source_observation_id"] is not None


def test_generic_collection_wording_is_a_diagnostic_not_a_gate():
    generic = assess_discovered_topic(
        "Frequently asked questions for photographers about laser pointer light painting",
        [],
    )
    specific = assess_discovered_topic(
        "What laser power works for daylight presentations",
        [],
    )

    assert generic.gate_status == "needs_evidence"
    assert "不阻断" in generic.limitations[-1]
    assert specific.gate_status == "needs_evidence"


def test_scope_and_placeholder_intent_are_diagnostics_not_gates():
    school_topic = "Laser pointer activities for school students"
    assert "运营者可自行决定" in str(current_topic_policy_block(school_topic))
    assert "运营者可自行决定" in str(current_topic_policy_block("Laser pointer gift for teenagers"))
    assert (
        current_topic_policy_block("How to safely terminate a high-power laser pointer beam")
        is None
    )

    result = qualify_candidate(
        school_topic,
        "",
        [],
        [],
        [],
        [],
        research_label="laser pointer quality verification",
    )
    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert result.recommended_disposition == "new_article"
    assert any("不阻断" in note for note in result.limitations)
    assert any("intent 为空或是占位文本" in note for note in result.limitations)
    assert any("不等于与现有文章重复" in note for note in result.limitations)


def test_explicit_adjacent_laser_product_is_a_soft_priority_diagnostic():
    result = qualify_candidate(
        "Rotary Laser Levels for Construction: When to Choose Them Over Handheld Laser Pointers",
        "Compare an adjacent product category with a handheld laser pointer for a construction task.",
        [],
        [],
        [],
        [],
        research_label="laser pointer construction landscaping professional work",
    )

    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert result.recommended_disposition == "new_article"
    assert any("明确换成了相邻激光产品对象" in value for value in result.limitations)


def test_adjacent_laser_product_drift_is_a_soft_diagnostic():
    topic = "Best laser level for outdoor construction and landscaping"
    branch = "laser pointer construction landscaping professional work"

    assert topic_matches_research_branch(topic, branch) is False
    result = qualify_candidate(
        topic,
        "Help crews choose a tool for outdoor alignment",
        [],
        [],
        [],
        [],
        research_label=branch,
        seed_anchor_fit="off_anchor",
        seed_anchor_note="The evidence changed the product object from pointer to level.",
    )

    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert any("明确换成了相邻激光产品" in note for note in result.limitations)
    assert any("偏离本轮核心对象" in note for note in result.limitations)


def test_niche_use_with_laser_pointer_as_core_is_not_penalized_for_crossing_branch():
    topic = "How Arborists Use Laser Pointers to Pinpoint Branches for Trimming"
    branch = "laser pointer construction landscaping professional work"

    assert topic_matches_research_branch(topic, branch) is False
    result = qualify_candidate(
        topic,
        "Help arborists indicate a particular branch from the ground",
        ["external:1:serp_snapshot contains a public question"],
        ["https://example.org/arborist-thread"],
        ["external:1:serp_snapshot"],
        [],
        research_label=branch,
        seed_anchor_fit="core",
    )

    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert any("不因跨分支自动降权" in note for note in result.limitations)
    assert not any("明确换成了相邻激光产品" in note for note in result.limitations)


def test_generic_high_power_overlap_cannot_claim_technical_subtopic_coverage():
    existing = [
        {
            "id": 17,
            "content_type": "blog",
            "title": "Inside the Beam: How Does a High-Power Laser Pointer Actually Work?",
            "canonical_url": "https://example.com/blog/high-power-beam",
            "search_text": "High power laser pointer beam operation and safety",
            "coverage_text": "High power laser pointer beam operation and safety",
            "_body_text": "This article explains high-power laser beam operation.",
        }
    ]
    result = qualify_candidate(
        "How to Safely Terminate a High-Power Laser Pointer Beam",
        "Help owners choose a suitable beam terminus.",
        ["external:1:serp_snapshot contains beam-termination demand"],
        ["https://example.edu/laser-safety", "https://example.org/beam-stop"],
        ["external:1:serp_snapshot", "external:2:page_capture"],
        existing,
    )

    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert result.closest_existing["relationship"] == "uncertain"
    assert result.evidence_gap["ok"] is True


def test_hazard_classification_alias_routes_to_existing_class_safety_article():
    existing = [
        {
            "id": 23,
            "content_type": "blog",
            "title": "Class 3R vs Class 4 Laser Safety: Complete Guide to Safe Use",
            "canonical_url": "https://example.com/blog/class-3r-vs-class-4",
            "search_text": "Class 3R vs Class 4 Laser Safety Complete Guide",
            "coverage_text": "Laser hazard classes, classification, safety and safe use",
            "_body_text": "This article explains laser hazard classes and classification.",
        }
    ]

    result = qualify_candidate(
        "What are the hazard classification of lasers?",
        "",
        [],
        [],
        [],
        existing,
    )

    assert result.qualification_status == QUALIFICATION_STATUS_BLOCKED
    assert result.relationship == "same_intent"
    assert result.recommended_disposition == "update_existing"
    assert result.closest_existing["content_item_id"] == 23


def test_non_english_is_diagnostic_while_canonical_duplicates_still_block():
    translated = assess_discovered_topic("激光笔电池充电指南", [])
    content_items = [
        {
            "id": 1,
            "content_type": "blog",
            "title": "Laser Pointer Battery Charging Guide",
            "slug": "laser-pointer-battery-charging-guide",
            "seo_title": "Laser Pointer Battery Charging Guide",
            "canonical_url": "https://example.com/blog/battery-charging",
            "search_text": "Laser Pointer Battery Charging Guide",
        }
    ]
    duplicate = assess_discovered_topic(
        "How to charge laser pointer batteries",
        content_items,
    )
    distinct = assess_discovered_topic(
        "How to store a laser pointer lens",
        content_items,
    )
    presentation_duplicate = assess_discovered_topic(
        "How to choose between red and green laser pointers for presentations",
        [
            {
                "id": 46,
                "content_type": "blog",
                "title": "Red vs. Green Laser for Presentations: Why Your Brighter Laser Is Not Visible",
                "slug": "red-vs-green-laser-presentation-visibility",
                "seo_title": "Red vs Green Laser for Presentations",
                "canonical_url": "https://laserpointerhub.com/blog/red-vs-green-laser-presentation-visibility",
                "search_text": "Red vs Green Laser for Presentations and Visibility",
            }
        ],
    )

    assert translated.gate_status == "needs_evidence"
    assert "不阻断" in translated.limitations[-1]
    assert duplicate.gate_status == "blocked"
    assert duplicate.overlap["max_score"] >= 0.68
    assert duplicate.overlap["matches"][0]["matched_tokens"] == [
        "battery",
        "charge",
    ]
    assert distinct.gate_status == "needs_evidence"
    assert "旧审计字段不决定资格" in distinct.next_step
    assert presentation_duplicate.gate_status == "blocked"
    assert presentation_duplicate.overlap["max_score"] >= 0.9


class FakeResearchAI:
    def __init__(self):
        self.calls = 0
        self.payloads = []

    async def complete_json(self, system_prompt, user_payload):
        self.calls += 1
        self.payloads.append(user_payload)
        refs = {item["purpose"]: item["evidence_id"] for item in user_payload["evidence"]}
        demand_ref = refs["serp_snapshot"]
        page_ref = refs["page_capture"]
        source_ref = refs["source_discovery"]
        return AIResponse(
            provider="fake",
            model="deepseek-v4-flash",
            prompt_sha256="a" * 64,
            content={
                "summary": "Synthesis of the stored evidence",
                "topics": [
                    {
                        "topic": "How to protect a laser pointer lens during storage",
                        "intent": "Help owners keep a stored laser pointer lens free from dust, scratches and condensation",
                        "rationale": "The stored related question justifies further verification",
                        "anchor_fit": "core",
                        "anchor_note": "The angle preserves the laser pointer product object.",
                        "evidence_ids": [demand_ref, source_ref, page_ref, "external:999:1"],
                        "facts": [
                            f"{demand_ref.upper()} contains a related lens-care question",
                            f"{page_ref} contains page-level lens-maintenance material",
                            "Unsupported assertion",
                        ],
                        "inference": ["Storage dust protection may need a separate explanation"],
                    }
                ],
            },
        )


class FakeOpenSchedulerAI:
    def __init__(self):
        self.calls = 0

    async def complete_json(self, system_prompt, user_payload):
        self.calls += 1
        if "source_observations" in user_payload:
            observations = user_payload["source_observations"]
            topics = [
                "Example Laser Guide",
                "Cold-start stabilization time for a green laser pointer",
                "How hand warmth changes laser pointer output in winter",
                "How wind changes handheld laser pointer housing cooldown",
                "Tripod-mounted laser pointer heat buildup during alignment",
                "Pocket-to-outdoor temperature recovery for laser pointers",
                "Battery heat versus diode heat symptoms in a laser pointer",
                "Copper versus aluminum laser pointer housing cooldown time",
                "Repeated short activations versus one long laser pointer activation",
                "Condensation after bringing a cold laser pointer indoors",
                "Thermal shutdown signs in a rechargeable laser pointer",
                "Measuring laser pointer housing temperature without opening it",
            ]
            proposals = []
            families = [item["key"] for item in user_payload["family_map"]]
            for index, topic in enumerate(topics):
                observation = observations[index % len(observations)]
                proposals.append(
                    {
                        "query": topic,
                        "topic": topic,
                        "intent": f"Answer the distinct thermal question: {topic}",
                        "family": families[index % len(families)],
                        "semantic_cluster": f"thermal_case_{index}",
                        "shape": "phenomenon",
                        "anchor_fit": "core",
                        "evidence_ids": [observation["evidence_id"]],
                        "rationale": "A current public source exposed this specific thermal angle.",
                    }
                )
            content = {"seed_proposals": proposals}
        elif "current_candidates" in user_payload:
            content = {"same_intent_clusters": [], "duplicates_of_prior": []}
        else:
            seed = user_payload["seed_context"]["seeds"][0]
            evidence_ref = user_payload["evidence"][0]["evidence_id"]
            topic = str(seed["hypothesis"]["topic"])
            content = {
                "summary": "One selected source seed was expanded.",
                "topics": [
                    {
                        "topic": f"Practical test: {topic}",
                        "intent": f"Test the operating condition behind {topic}",
                        "rationale": "The selected seed has current source evidence.",
                        "anchor_fit": "core",
                        "anchor_note": "The handheld laser pointer remains the core object.",
                        "semantic_cluster": str(seed["semantic_cluster"]),
                        "source_support": "snippet_supported",
                        "evidence_ids": [evidence_ref],
                        "facts": [f"{evidence_ref} stores the selected source evidence"],
                        "inference": ["This is a distinct operating test."],
                    }
                ],
                "source_observations": [],
            }
        return AIResponse(
            provider="fake",
            model="deepseek-v4-flash",
            prompt_sha256=f"{self.calls:064x}",
            content=content,
        )


def test_open_scheduler_family_entries_are_not_narrowed_by_selected_direction():
    thermal_specs = _open_scheduler_family_specs("laser pointer thermal design reliability")
    battery_specs = _open_scheduler_family_specs("laser pointer battery charging compatibility")

    assert thermal_specs == battery_specs
    assert len(thermal_specs) == 12
    queries = {item["family"]: item["query"] for item in thermal_specs}
    assert queries["principles"] == ('laser pointer unusual beam dot phenomenon myths "why" forum')
    assert queries["tasks"] == ("laser pointer unusual practical task use case site:reddit.com")
    assert queries["emerging"] == (
        '"laser pointer" unexpected question real use problem site:youtube.com OR site:reddit.com'
    )
    assert all("thermal" not in query.casefold() for query in queries.values())
    assert all("battery" not in query.casefold() for query in queries.values())


class FakeIntentClusterAI:
    async def complete_json(self, system_prompt, user_payload):
        assert "deduplicate an SEO topic pool" in system_prompt
        assert len(user_payload["current_candidates"]) == 3
        return AIResponse(
            provider="fake",
            model="deepseek-v4-flash",
            prompt_sha256="c" * 64,
            content={
                "same_intent_clusters": [
                    {
                        "representative_id": "current-0",
                        "member_ids": ["current-0", "current-1"],
                        "reason": "Both answer laser-pointer syndrome risks for dogs.",
                    }
                ],
                "duplicates_of_prior": [],
            },
        )


def test_intent_clustering_merges_wording_variants_but_keeps_distinct_detail(settings):
    _prepare(settings)
    candidates = [
        {
            "topic": "Laser Pointer Syndrome in Dogs: Risks and Safer Alternatives",
            "intent": "Explain psychological risks of laser chasing for dogs",
            "rationale": "A public veterinary source exposed the concern.",
            "evidence_refs": ["external:1:source_discovery"],
            "facts": ["The source describes unresolved chasing behavior."],
            "inference": [],
            "candidate_stage": "synthesized_angle",
        },
        {
            "topic": "Laser Pointer Syndrome in Dogs: Causes, Symptoms, and Recovery",
            "intent": "Help owners understand and address laser chasing obsession",
            "rationale": "A public owner discussion exposed the same concern.",
            "evidence_refs": ["external:2:page_capture"],
            "facts": ["The page discusses obsessive light chasing."],
            "inference": [],
            "candidate_stage": "synthesized_angle",
        },
        {
            "topic": "How Fog Changes Laser Pointer Visibility for Emergency Signaling",
            "intent": "Assess weather visibility for outdoor emergency signaling",
            "rationale": "A public outdoor source exposed this operating condition.",
            "evidence_refs": ["external:3:source_discovery"],
            "facts": ["The source discusses fog visibility."],
            "inference": [],
            "candidate_stage": "synthesized_angle",
        },
    ]

    result, _model, ok, audit = asyncio.run(
        _ai_intent_deduplicate_pool(
            settings,
            site_id=1,
            research_run_id=999,
            candidates=candidates,
            provider=FakeIntentClusterAI(),
        )
    )

    assert ok is True
    assert len(result) == 2
    assert result[0]["topic"] == candidates[0]["topic"]
    assert result[0]["evidence_refs"] == [
        "external:1:source_discovery",
        "external:2:page_capture",
    ]
    assert result[1]["topic"] == candidates[2]["topic"]
    assert audit["same_intent_clusters"] == 1
    assert audit["merged_current_variants"] == 1
    assert audit["current_variant_merges"] == [
        {
            "retained_topic": candidates[0]["topic"],
            "merged_topics": [candidates[1]["topic"]],
            "reason": "Both answer laser-pointer syndrome risks for dogs.",
        }
    ]


def test_open_scheduler_keeps_all_distinct_seeds_and_only_deep_expands_five(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        tavily_api_key="tavily-secret",
        ai_base_url="https://ai.example/v1",
        ai_api_key="ai-secret",
        ai_model="deepseek-v4-flash",
        research_serpapi_budget=2,
        research_firecrawl_budget=0,
        research_tavily_budget=17,
        research_ai_budget=13,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "tech-thermal"
    )
    tavily_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tavily_calls
        if request.url.host == "api.tavily.com":
            tavily_calls += 1
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "results": [
                        {
                            "title": f"Handheld laser pointer thermal note {tavily_calls}",
                            "url": f"https://thermal-{tavily_calls}.example.org/pointer",
                            "content": (
                                "A handheld laser pointer owner describes a distinct temperature, "
                                "cooldown, housing, or operating-condition question."
                            ),
                            "score": 0.8,
                        }
                    ],
                },
            )
        if request.url.host == "serpapi.com":
            query = str(request.url.params["q"])
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {"engine": "google", "q": query},
                    "organic_results": [],
                    "related_questions": [{"question": f"How should owners test {query}?"}],
                },
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    ai = FakeOpenSchedulerAI()
    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            transport=httpx.MockTransport(handler),
            ai_provider=ai,
        )
    )

    assert outcome.usage["serpapi"]["actual_requests"] == 2
    assert outcome.usage["tavily"]["actual_requests"] == 17
    assert outcome.usage["ai"]["actual_requests"] == 7
    assert ai.calls == 7
    assert outcome.candidate_count >= 10
    with connection(configured) as conn:
        filters = json.loads(
            conn.execute(
                "SELECT filters_json FROM research_runs WHERE id = ?", (outcome.run_id,)
            ).fetchone()["filters_json"]
        )
    assert filters["open_scheduler"]["active"] is True
    assert filters["open_scheduler"]["seed_proposal_count"] == 12
    assert filters["open_scheduler"]["selected_seed_count"] == 5
    assert filters["open_scheduler"]["visible_distinct_seed_count"] >= 10
    cms_duplicate_audit = filters["open_scheduler"]["cms_duplicate_seed_audit"]
    assert len(cms_duplicate_audit) == 1
    assert cms_duplicate_audit[0]["topic"] == "Example Laser Guide"
    assert cms_duplicate_audit[0]["matches"][0]["title"] == "Example Laser Guide"
    assert filters["open_scheduler"]["intent_clustering"]["same_intent_clusters"] == 0
    assert filters["open_scheduler"]["intent_clustering"]["current_variant_merges"] == []
    assert "candidate_resolution_audit" in filters


def test_budgeted_research_uses_all_sources_and_reuses_snapshots(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        firecrawl_api_key="fire-secret",
        tavily_api_key="tavily-secret",
        ai_base_url="https://ai.example/v1",
        ai_api_key="ai-secret",
        ai_model="deepseek-v4-flash",
        research_serpapi_budget=2,
        research_firecrawl_budget=1,
        research_tavily_budget=2,
        research_ai_budget=1,
    )
    network_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "serpapi.com":
            engine = str(request.url.params["engine"])
            network_calls.append(f"serpapi:{engine}")
            if engine == "google_trends":
                return httpx.Response(
                    200,
                    json={
                        "search_metadata": {"status": "Success"},
                        "search_parameters": {
                            "engine": "google_trends",
                            "q": "best example laser",
                            "date": "today 12-m",
                            "geo": "US",
                        },
                        "interest_over_time": {"timeline_data": []},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "api_key": "serp-secret",
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {"engine": "google", "q": "best example laser"},
                    "organic_results": [
                        {
                            "position": 1,
                            "title": "Lens maintenance guide",
                            "link": "https://example.org/lens-maintenance",
                        }
                    ],
                    "related_questions": [
                        {"question": "How do you keep a laser pointer lens clean?"}
                    ],
                    "related_searches": [{"query": "laser pointer lens storage"}],
                },
            )
        body = json.loads(request.content)
        if host == "api.tavily.com":
            network_calls.append("tavily")
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "debug": "tavily-secret",
                    "results": [
                        {
                            "title": "Safe lens care",
                            "url": "https://docs.example.net/lens-care",
                            "content": "A source snippet for later verification.",
                            "score": 0.8,
                        }
                    ],
                    "usage": {"credits": 1},
                },
            )
        if host == "api.firecrawl.dev":
            network_calls.append("firecrawl")
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "debug": "fire-secret",
                    "data": {
                        "markdown": "# Lens maintenance\nVerified page text.",
                        "metadata": {
                            "sourceURL": body["url"],
                            "title": "Lens maintenance",
                            "statusCode": 200,
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected host {host}")

    ai = FakeResearchAI()
    transport = httpx.MockTransport(handler)
    first = asyncio.run(run_topic_research(1, configured, transport=transport, ai_provider=ai))
    assert first.status == "success"
    assert first.usage["serpapi"]["actual_requests"] == 2
    assert first.usage["firecrawl"]["actual_requests"] == 1
    assert first.usage["tavily"]["actual_requests"] == 2
    assert first.usage["ai"]["actual_requests"] == 1
    assert first.candidate_count > 0
    assert ai.calls == 1
    seed_context = ai.payloads[0]["seed_context"]
    assert seed_context["core_object"] == "laser pointer"
    assert seed_context["seeds"]
    assert seed_context["method"].startswith("deterministic seed selection")
    assert "Sparse niche-market signals" in seed_context["signal_policy"]
    assert "never first-party" in seed_context["source_policy"]

    with connection(configured) as conn:
        runs = conn.execute("SELECT * FROM external_runs ORDER BY id").fetchall()
        candidates = conn.execute(
            "SELECT * FROM research_candidates WHERE research_run_id = ?", (first.run_id,)
        ).fetchall()
        ai_row = conn.execute(
            "SELECT * FROM ai_runs WHERE purpose = 'topic_research' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert len(runs) == 5
    assert len(candidates) == first.candidate_count
    assert all(row["gate_status"] in {"needs_evidence", "blocked"} for row in candidates)
    ai_candidate = next(row for row in candidates if row["topic"].startswith("How to protect"))
    assert ":serp_snapshot" in ai_candidate["evidence_refs_json"]
    assert ai_candidate["qualification_status"] == QUALIFICATION_STATUS_QUALIFIED
    assert "Unsupported assertion" not in ai_candidate["facts_json"]
    assert "Seed anchor fit: core." in ai_candidate["inference_json"]
    assert ai_row["model"] == "deepseek-v4-flash"
    for row in runs:
        snapshot = Path(row["response_snapshot_path"]).read_text(encoding="utf-8")
        assert "serp-secret" not in snapshot
        assert "fire-secret" not in snapshot
        assert "tavily-secret" not in snapshot

    calls_after_first = list(network_calls)
    second = asyncio.run(run_topic_research(1, configured, transport=transport, ai_provider=ai))
    assert second.usage["serpapi"]["actual_requests"] == 0
    assert second.usage["firecrawl"]["actual_requests"] <= 1
    assert second.usage["tavily"]["actual_requests"] <= 2
    assert second.usage["serpapi"]["reused"] == 2
    assert second.usage["tavily"]["reused"] == 2
    assert second.usage["firecrawl"]["reused"] >= 1
    assert not any(call.startswith("serpapi:") for call in network_calls[len(calls_after_first) :])
    assert ai.calls == 2

    message = record_research_candidate_decision(int(ai_candidate["id"]), "do", settings=configured)
    assert "已确认" in message
    with connection(configured) as conn:
        created = conn.execute(
            """
            SELECT * FROM opportunities
            WHERE rule_key = 'research_topic_candidate' AND target_ref = ?
            """,
            (ai_candidate["topic"],),
        ).fetchone()
        analysis = conn.execute(
            "SELECT metadata_json FROM analysis_runs WHERE id = ?",
            (created["analysis_run_id"],),
        ).fetchone()
        portfolio_count = conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE portfolio_slot IS NOT NULL"
        ).fetchone()[0]

    assert created["gate_status"] == "passed"
    assert created["method_version"] == RESEARCH_METHOD_VERSION
    assert created["portfolio_slot"] == "新文章 1"
    assert json.loads(analysis["metadata_json"])["top_count"] == portfolio_count


def test_qualification_keeps_directions_without_demand_and_page_level_material():
    topic = "How to protect a laser pointer lens during storage"
    intent = "Help owners prevent dust and scratches while the device is stored"
    urls = ["https://example.org/lens-care", "https://docs.example.net/storage"]

    discovery_only = qualify_candidate(
        topic,
        intent,
        ["external:1:page_capture contains storage guidance"],
        urls,
        ["external:1:source_discovery", "external:2:page_capture"],
        [],
    )
    serp_only = qualify_candidate(
        topic,
        intent,
        ["external:3:serp_snapshot contains a related user question"],
        urls,
        ["external:3:serp_snapshot", "external:4:source_discovery"],
        [],
    )
    complete = qualify_candidate(
        topic,
        intent,
        [
            "external:3:serp_snapshot contains a related user question",
            "external:5:page_capture contains page-level storage guidance",
        ],
        urls,
        [
            "external:3:serp_snapshot",
            "external:4:source_discovery",
            "external:5:page_capture",
        ],
        [],
    )

    assert discovery_only.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert discovery_only.evidence_demand["ok"] is False
    assert serp_only.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert serp_only.evidence_material["ok"] is False
    assert complete.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert complete.evidence_demand["ok"] is True
    assert complete.evidence_material["ok"] is True


def test_qualification_keeps_an_adjacent_finer_angle_without_drafting_material():
    content_items = [
        {
            "id": 5,
            "content_type": "blog",
            "title": "Laser Pointer Light Painting: The Complete Photography Guide",
            "canonical_url": "https://example.com/blog/light-painting",
            "search_text": "Laser Pointer Light Painting Photography Guide",
            "coverage_text": (
                "Laser Pointer Light Painting Photography Guide Camera Sensor Damage "
                "Base Settings Choosing the Right Laser Diffraction Grating Caps "
                "Long Exposure Settings"
            ),
        }
    ]

    result = qualify_candidate(
        "Common mistakes in laser light painting",
        "Help photographers identify and avoid mistakes in a light-painting session",
        [],
        [],
        [],
        content_items,
    )

    assert result.relationship == "adjacent"
    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert result.recommended_disposition == "new_article"
    assert result.evidence_demand["ok"] is False
    assert result.evidence_material["ok"] is False


def test_qualification_routes_duplicate_to_the_relevant_existing_article():
    content_items = [
        {
            "id": 1,
            "content_type": "blog",
            "title": "How to Choose Laser Pointer Power Based on Use Case",
            "canonical_url": "https://example.com/blog/power",
            "search_text": "How to Choose Laser Pointer Power Based on Use Case",
            "coverage_text": "Power ranges for different use cases",
        },
        {
            "id": 2,
            "content_type": "blog",
            "title": "Laser Pointer Colors Explained",
            "canonical_url": "https://example.com/blog/colors",
            "search_text": "Laser Pointer Colors Explained Red Green Blue",
            "coverage_text": "Color visibility wavelength and use cases",
        },
    ]
    result = qualify_candidate(
        "Selecting laser pointer color based on use case",
        "Choose a pointer color for a specific task",
        [
            "external:1:serp_snapshot contains color-selection demand",
            "external:2:page_capture contains page-level color material",
        ],
        ["https://example.org/colors", "https://docs.example.net/color-use"],
        ["external:1:serp_snapshot", "external:2:page_capture"],
        content_items,
    )

    assert result.qualification_status == QUALIFICATION_STATUS_BLOCKED
    assert result.recommended_disposition == "update_existing"
    assert result.closest_existing["content_item_id"] == 2
    assert result.closest_existing["url"].endswith("/colors")


def test_same_intent_research_lead_creates_an_existing_article_opportunity(settings):
    _prepare(settings)
    content_items = _content_search_items(settings, 1)
    with connection(settings) as conn:
        run_id = int(
            conn.execute(
                """
                INSERT INTO research_runs(
                    site_id, analysis_run_id, status, budgets_json, seed_queries_json,
                    ai_model, started_at, seed_type, filters_json
                ) VALUES(?, 1, 'success', '{}', '[]', NULL, '2026-07-17T00:00:00Z', 'topic_gap', '{}')
                """,
                (1,),
            ).lastrowid
        )
    evidence = {
        "external:1:serp_snapshot": {
            "provider": "serpapi",
            "purpose": "serp_snapshot",
            "payload": {"url": "https://demand.example/laser-guide"},
        },
        "external:2:page_capture": {
            "provider": "firecrawl",
            "purpose": "page_capture",
            "payload": {"url": "https://material.example/laser-guide"},
        },
    }
    inserted = _insert_candidates(
        settings,
        research_run_id=run_id,
        site_id=1,
        candidates=[
            {
                "topic": "Example Laser Guide",
                "intent": "Answer the same guide task as the current article.",
                "rationale": "Controlled same-intent lead.",
                "evidence_refs": list(evidence),
                "facts": ["external:1:serp_snapshot contains real demand for this guide."],
                "inference": [],
            }
        ],
        evidence=evidence,
        content_items=content_items,
        suggested_parent_topic_id=None,
        research_label=None,
    )

    assert inserted == 1
    with connection(settings) as conn:
        candidate = conn.execute(
            "SELECT qualification_status, recommended_disposition FROM research_candidates WHERE research_run_id = ?",
            (run_id,),
        ).fetchone()
        opportunity = conn.execute(
            """
            SELECT target_ref, rule_key, opportunity_type FROM opportunities
            WHERE rule_key = 'research_existing_content_gap'
            """
        ).fetchone()
    assert candidate["qualification_status"] == QUALIFICATION_STATUS_BLOCKED
    assert candidate["recommended_disposition"] == "update_existing"
    assert opportunity["target_ref"].endswith("/blog/example")
    assert opportunity["opportunity_type"] == "optimize"


def test_strong_body_coverage_routes_a_narrow_lead_to_the_existing_article():
    existing = [
        {
            "id": 13,
            "content_type": "blog",
            "title": "Laser Pointer Case and Storage Guide",
            "canonical_url": "https://example.com/blog/storage",
            "search_text": "Laser Pointer Case and Storage Guide",
            "coverage_text": (
                "Hard cases soft pouches DIY storage protection from dust impact "
                "and accidental activation"
            ),
        }
    ]
    result = qualify_candidate(
        "DIY laser pointer storage cases: repurposing containers for protection",
        "Assess household containers for storage protection",
        ["external:2:page_capture contains page-level forum material"],
        ["https://example.org/storage", "https://forum.example.net/cases"],
        ["external:1:source_discovery", "external:2:page_capture"],
        existing,
    )

    assert result.qualification_status == QUALIFICATION_STATUS_BLOCKED
    assert result.recommended_disposition == "covered_existing"
    assert result.closest_existing["content_item_id"] == 13


def test_product_copy_does_not_count_as_an_existing_article_answer():
    product = {
        "id": 71,
        "content_type": "product",
        "title": "Wireless Presentation Pointer with Built-in Tools",
        "canonical_url": "https://example.com/p-P001.html",
        "search_text": "Wireless Presentation Pointer with Built-in Tools",
        "coverage_text": (
            "Virtual pointer PowerPoint presentation built-in tool software alternatives"
        ),
    }
    result = qualify_candidate(
        "Virtual Laser Pointers for PowerPoint Presentations",
        "Show presenters how to use software-based pointing alternatives",
        [
            "external:1:serp_snapshot contains a virtual laser pointer question",
            "external:2:page_capture contains software pointer instructions",
        ],
        [
            "https://support.example.org/powerpoint-pointer",
            "https://docs.example.net/virtual-pointer",
        ],
        ["external:1:serp_snapshot", "external:2:page_capture"],
        [product],
        research_label="laser pointer presentations classrooms",
    )

    assert result.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert result.closest_existing["content_item_id"] is None


def test_uncertain_relationship_is_retained_and_can_be_human_overridden():
    content_items = [
        {
            "id": 9,
            "content_type": "blog",
            "title": "Construction Laser Alignment Guide",
            "canonical_url": "https://example.com/blog/construction-alignment",
            "search_text": "Construction Laser Alignment Guide",
            "coverage_text": "Construction alignment setup",
            "_h2_sections": ["Mounting stability checks"],
        }
    ]
    arguments = {
        "topic": "Laser pointer mounting stability for construction alignment",
        "intent": "Help installers prevent movement during a construction alignment check",
        "facts": [
            "external:1:serp_snapshot contains a mounting stability question",
            "external:2:page_capture contains page-level setup material",
        ],
        "source_urls": ["https://example.org/mounting", "https://docs.example.net/alignment"],
        "evidence_ids": ["external:1:serp_snapshot", "external:2:page_capture"],
        "content_items": content_items,
    }

    uncertain = qualify_candidate(**arguments)
    reviewed = qualify_candidate(**arguments, human_relationship=RELATIONSHIP_DISTINCT)

    assert uncertain.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert uncertain.recommended_disposition == "new_article"
    assert reviewed.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert reviewed.closest_existing["program_relationship"] == "uncertain"
    assert reviewed.closest_existing["human_relationship"] == "distinct"


def test_alternatives_intent_and_branch_drift_are_retained_when_not_duplicates():
    evidence = {
        "facts": [
            "external:1:serp_snapshot contains a PowerPoint virtual pointer question",
            "external:2:page_capture contains software pointer instructions",
        ],
        "source_urls": [
            "https://support.example.org/powerpoint-pointer",
            "https://docs.example.net/virtual-pointer",
        ],
        "evidence_ids": ["external:1:serp_snapshot", "external:2:page_capture"],
        "content_items": [],
        "research_label": "laser pointer presentations classrooms",
    }
    valid = qualify_candidate(
        "Virtual Laser Pointers for PowerPoint Presentations",
        "Show presenters how to use software-based pointing alternatives",
        **evidence,
    )
    drifted = qualify_candidate(
        "Common mistakes to avoid in business presentations",
        "Help presenters avoid generic speaking mistakes",
        **evidence,
    )

    assert valid.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert drifted.qualification_status == QUALIFICATION_STATUS_QUALIFIED
    assert any("不等于与现有文章重复" in note for note in drifted.limitations)


def test_research_partial_failure_keeps_successful_evidence(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        tavily_api_key="tavily-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=0,
        research_tavily_budget=1,
        research_ai_budget=0,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "serpapi.com":
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {"engine": "google", "q": "best example laser"},
                    "organic_results": [],
                    "related_questions": [{"question": "How should a laser be stored?"}],
                },
            )
        return httpx.Response(429, json={"detail": "rate limited"})

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            transport=httpx.MockTransport(handler),
        )
    )
    assert outcome.status == "partial"
    assert outcome.usage["serpapi"]["successful"] == 1
    assert outcome.usage["tavily"]["failed"] == 1
    with connection(configured) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM external_runs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0] >= 1


def test_topic_research_spreads_serp_budget_across_frontier_queries(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        research_serpapi_budget=2,
        research_firecrawl_budget=0,
        research_tavily_budget=0,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "use-presentations"
    )
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        engine = str(request.url.params["engine"])
        query = str(request.url.params["q"])
        requests.append((engine, query))
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_parameters": {"engine": engine, "q": query},
                "organic_results": [],
                "related_questions": [
                    {"question": "What makes a laser pointer fail on an LED presentation screen?"}
                ],
            },
        )

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="boundary",
            topic_id=topic_id,
            dimension_key="failure",
            transport=httpx.MockTransport(handler),
        )
    )

    assert outcome.usage["serpapi"]["actual_requests"] == 2
    assert [engine for engine, _ in requests] == ["google", "google"]
    assert len({query for _, query in requests}) == 2
    assert all(len(query) <= 100 for _, query in requests)


def test_boundary_research_sources_frontiers_and_spreads_page_captures(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        firecrawl_api_key="fire-secret",
        tavily_api_key="tavily-secret",
        research_serpapi_budget=2,
        research_firecrawl_budget=2,
        research_tavily_budget=2,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "use-presentations"
    )
    serp_queries: list[str] = []
    tavily_queries: list[str] = []
    scraped_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "serpapi.com":
            query = str(request.url.params["q"])
            serp_queries.append(query)
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {"engine": "google", "q": query},
                    "organic_results": [],
                    "related_questions": [
                        {"question": f"What evidence answers frontier {len(serp_queries)}?"}
                    ],
                },
            )
        body = json.loads(request.content)
        if request.url.host == "api.tavily.com":
            query_number = len(tavily_queries) + 1
            tavily_queries.append(str(body["query"]))
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "results": [
                        {
                            "title": f"Frontier {query_number} primary",
                            "url": f"https://frontier-{query_number}-primary.example/material",
                            "content": "Primary material.",
                            "score": 0.9,
                        },
                        {
                            "title": f"Frontier {query_number} secondary",
                            "url": f"https://frontier-{query_number}-secondary.example/material",
                            "content": "Secondary material.",
                            "score": 0.8,
                        },
                    ],
                },
            )
        if request.url.host == "api.firecrawl.dev":
            scraped_urls.append(str(body["url"]))
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "# Frontier material\nPage-level material.",
                        "metadata": {
                            "sourceURL": body["url"],
                            "title": "Frontier material",
                            "statusCode": 200,
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="boundary",
            topic_id=topic_id,
            dimension_key="failure",
            transport=httpx.MockTransport(handler),
        )
    )

    assert tavily_queries == serp_queries
    assert scraped_urls == [
        "https://frontier-1-primary.example/material",
        "https://frontier-2-primary.example/material",
    ]


def test_paa_lineage_can_qualify_without_ai_when_page_material_exists(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        firecrawl_api_key="fire-secret",
        tavily_api_key="tavily-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=1,
        research_tavily_budget=1,
        research_ai_budget=0,
    )
    question = "How do you prevent a laser pointer lens from fogging after cold storage?"
    scraped_urls: list[str] = []
    tavily_queries: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "serpapi.com":
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {
                        "engine": "google",
                        "q": str(request.url.params["q"]),
                    },
                    "organic_results": [
                        {
                            "title": "General storage result",
                            "link": "https://organic.example.org/storage",
                        }
                    ],
                    "related_questions": [{"question": question}],
                },
            )
        body = json.loads(request.content)
        if request.url.host == "api.tavily.com":
            tavily_queries.append(str(body["query"]))
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "results": [
                        {
                            "title": "Cold storage lens care",
                            "url": "https://docs.example.net/cold-storage-lens-care",
                            "content": "Condensation prevention material.",
                            "score": 0.9,
                        }
                    ],
                },
            )
        if request.url.host == "api.firecrawl.dev":
            scraped_urls.append(str(body["url"]))
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "# Cold storage lens care\nPage-level material.",
                        "metadata": {
                            "sourceURL": body["url"],
                            "title": "Cold storage lens care",
                            "statusCode": 200,
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="gsc",
            transport=httpx.MockTransport(handler),
        )
    )

    with connection(configured) as conn:
        candidate = conn.execute(
            "SELECT * FROM research_candidates WHERE research_run_id = ? AND topic = ?",
            (outcome.run_id, question),
        ).fetchone()
    assert tavily_queries == [question]
    assert scraped_urls == ["https://docs.example.net/cold-storage-lens-care"]
    assert candidate["qualification_status"] == QUALIFICATION_STATUS_QUALIFIED
    assert ":serp_snapshot" in candidate["evidence_refs_json"]
    assert ":source_discovery" in candidate["evidence_refs_json"]
    assert ":page_capture" in candidate["evidence_refs_json"]


def test_current_rule_supersedes_a_legacy_pending_needs_evidence_candidate(settings):
    _prepare(settings)
    question = "How do you prevent a laser pointer lens from fogging after cold storage?"
    base = replace(
        settings,
        serpapi_api_key="serp-secret",
        tavily_api_key="tavily-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=0,
        research_tavily_budget=1,
        research_ai_budget=0,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "serpapi.com":
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "search_parameters": {
                        "engine": "google",
                        "q": str(request.url.params["q"]),
                    },
                    "organic_results": [
                        {
                            "title": "General storage result",
                            "link": "https://organic.example.org/storage",
                        }
                    ],
                    "related_questions": [{"question": question}],
                },
            )
        body = json.loads(request.content)
        if request.url.host == "api.tavily.com":
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "results": [
                        {
                            "title": "Cold storage lens care",
                            "url": "https://docs.example.net/cold-storage-lens-care",
                            "content": "Condensation prevention material.",
                            "score": 0.9,
                        }
                    ],
                },
            )
        if request.url.host == "api.firecrawl.dev":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "# Cold storage lens care\nPage-level material.",
                        "metadata": {
                            "sourceURL": body["url"],
                            "title": "Cold storage lens care",
                            "statusCode": 200,
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    transport = httpx.MockTransport(handler)
    first = asyncio.run(run_topic_research(1, base, seed_type="gsc", transport=transport))
    with connection(base) as conn:
        conn.execute(
            """
            UPDATE research_candidates
            SET qualification_status = 'needs_evidence',
                qualification_version = 'candidate-qualification-0.9.0'
            WHERE research_run_id = ? AND topic = ?
            """,
            (first.run_id, question),
        )
    with_material = replace(base, firecrawl_api_key="fire-secret", research_firecrawl_budget=1)
    second = asyncio.run(run_topic_research(1, with_material, seed_type="gsc", transport=transport))

    with connection(with_material) as conn:
        rows = conn.execute(
            """
            SELECT research_run_id, qualification_status, previous_assessment_json
            FROM research_candidates WHERE topic = ? ORDER BY id
            """,
            (question,),
        ).fetchall()
    assert [row["research_run_id"] for row in rows] == [first.run_id, second.run_id]
    assert [row["qualification_status"] for row in rows] == [
        "stale",
        QUALIFICATION_STATUS_QUALIFIED,
    ]
    assert "superseded_by_current_qualification" in rows[0]["previous_assessment_json"]


def test_gsc_entry_explains_when_current_batch_has_no_analysis(settings):
    _prepare(settings)
    import_gsc_bytes(1, "new-standard.xlsx", workbook_bytes(), settings)

    with pytest.raises(ResearchUnavailable, match="没有可用的 GSC"):
        asyncio.run(run_topic_research(1, settings, seed_type="gsc"))

    with connection(settings) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert run_count == 0


def test_topic_gap_entry_runs_without_current_gsc_analysis(settings):
    _prepare(settings)
    import_gsc_bytes(1, "new-standard.xlsx", workbook_bytes(), settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=0,
        research_tavily_budget=0,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "use-photography"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_parameters": {
                    "engine": "google",
                    "q": str(request.url.params["q"]),
                },
                "organic_results": [],
                "related_questions": [
                    {"question": "What mistakes happen in laser light painting?"}
                ],
            },
        )

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            transport=httpx.MockTransport(handler),
        )
    )

    assert outcome.status == "success"
    # The branch has no structured strategic hypothesis. Its discovery query
    # may surface an evidence-bound SERP question, but the branch label itself
    # is never promoted as a fabricated topic.
    assert outcome.candidate_count == 1
    with connection(configured) as conn:
        run = conn.execute(
            "SELECT seed_type, topic_id FROM research_runs WHERE id = ?", (outcome.run_id,)
        ).fetchone()
        memory_count = conn.execute(
            "SELECT COUNT(*) FROM topic_research_memory WHERE research_run_id = ?",
            (outcome.run_id,),
        ).fetchone()[0]
    assert run["seed_type"] == "topic_gap"
    assert run["topic_id"] is not None
    assert memory_count > 0


def test_topic_gap_uses_persisted_diverse_task_cards_before_generic_lenses(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        research_serpapi_budget=3,
        research_firecrawl_budget=0,
        research_tavily_budget=0,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "use-professional"
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params["q"]))
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_parameters": {"engine": "google", "q": calls[-1]},
                "organic_results": [],
                "related_questions": [],
                "related_searches": [],
            },
        )

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            transport=httpx.MockTransport(handler),
        )
    )

    assert outcome.usage["serpapi"]["actual_requests"] == 3
    assert len(calls) == 3
    with connection(configured) as conn:
        run = conn.execute(
            "SELECT filters_json FROM research_runs WHERE id = ?", (outcome.run_id,)
        ).fetchone()
    filters = json.loads(run["filters_json"])
    assert (
        filters["seed_method"]
        == "source_observation_frontier_then_old_plan_task_cards_then_evidence_then_ai_packaging"
    )
    assert len(filters["seed_cards"]) == 3
    assert {card["card"]["role"] for card in filters["seed_cards"]} == {
        "arborist or tree-care professional",
        "building inspector",
        "alignment technician",
    }
    assert all(card["card"]["preflight"]["status"] == "qualified" for card in filters["seed_cards"])


def test_topic_gap_without_a_structured_hypothesis_never_promotes_a_label_to_a_topic(settings):
    _prepare(settings)
    configured = replace(
        settings,
        serpapi_api_key="serp-secret",
        research_serpapi_budget=1,
        research_firecrawl_budget=0,
        research_tavily_budget=0,
        research_ai_budget=0,
    )
    topic_id = next(
        item["id"]
        for item in research_topic_options(1, configured)
        if item["topic_key"] == "buy-quality"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_parameters": {
                    "engine": "google",
                    "q": str(request.url.params["q"]),
                },
                "organic_results": [],
                "related_questions": [
                    {"question": "How do I choose a rechargeable laser pointer?"}
                ],
            },
        )

    outcome = asyncio.run(
        run_topic_research(
            1,
            configured,
            seed_type="topic_gap",
            topic_id=topic_id,
            transport=httpx.MockTransport(handler),
        )
    )

    with connection(configured) as conn:
        topics = [
            row["topic"]
            for row in conn.execute(
                "SELECT topic FROM research_candidates WHERE research_run_id = ?",
                (outcome.run_id,),
            ).fetchall()
        ]
    # The evidence-bound SERP question survives even when its wording drifts
    # from the selected branch. The generic plan lens itself is never promoted.
    assert topics == ["How do I choose a rechargeable laser pointer?"]
    assert not any("specific owner" in topic.lower() for topic in topics)


def test_structured_article_coverage_blocks_subtopics_and_reports_branch_alignment():
    content_items = [
        {
            "id": 5,
            "content_type": "blog",
            "title": "Laser Pointer Light Painting: The Complete Photography Guide",
            "slug": "laser-pointer-light-painting-photography-guide",
            "seo_title": "Laser Pointer Light Painting Photography Guide",
            "canonical_url": "https://example.com/blog/light-painting",
            "search_text": "Laser Pointer Light Painting Photography Guide",
            "coverage_text": " ".join(
                [
                    "Laser Pointer Light Painting Photography Guide",
                    "Camera Sensor Damage",
                    "Base Settings for Laser Light Painting",
                    "Choosing the Right Laser for Light Painting",
                    "Diffraction Grating Caps",
                    "Long Exposure Settings",
                ]
            ),
        }
    ]
    covered_topics = [
        "Can laser pointers damage camera sensors?",
        "Basic setup for laser light painting",
        "Choosing the right laser pointer for light painting",
        "Using diffraction gratings for laser light painting",
        "Long exposure settings for laser light painting",
    ]

    for topic in covered_topics:
        assessment = assess_discovered_topic(topic, content_items)
        assert assessment.gate_status == "blocked", topic
        assert assessment.overlap["matches"][0]["coverage_score"] >= 0.72

    distinct = assess_discovered_topic(
        "Common mistakes in laser light painting",
        content_items,
    )
    assert distinct.gate_status == "needs_evidence"
    assert topic_matches_research_branch(
        "Common mistakes in laser light painting",
        "laser pointer light painting photography",
    )
    assert not topic_matches_research_branch(
        "What is the 20-60-20 rule in photography?",
        "laser pointer light painting photography",
    )
    presentation_branch = "laser pointer presentations classrooms"
    assert topic_matches_research_branch(
        "Classroom laser pointer safety guidelines for educators",
        presentation_branch,
    )
    assert not topic_matches_research_branch(
        "Green laser pointer restrictions in educational settings",
        presentation_branch,
    )
    assert topic_matches_research_branch(
        "How to choose a presentation laser pointer for hybrid teaching",
        presentation_branch,
    )
    assert not topic_matches_research_branch(
        "Laser pointer techniques for night fishing",
        presentation_branch,
    )


def test_content_search_items_extracts_headings_without_exposing_body(settings):
    _prepare(settings)
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE content_snapshots
            SET body = ?
            WHERE id = (SELECT MAX(id) FROM content_snapshots WHERE content_item_id = 1)
            """,
            (
                "Intro paragraph.\n\n## Camera Sensor Damage\nText.\n\n"
                "### Diffraction Grating Caps\nMore text.",
            ),
        )

    item = next(value for value in _content_search_items(settings, 1) if value["id"] == 1)

    assert "Camera Sensor Damage" in item["coverage_text"]
    assert "Diffraction Grating Caps" in item["coverage_text"]
    assert "body" not in item
