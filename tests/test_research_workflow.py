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
    assess_discovered_topic,
    topic_matches_research_branch,
)
from seo_ops.services.ai import AIResponse
from seo_ops.services.research_workflow import (
    ResearchUnavailable,
    _content_search_items,
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


def test_generic_collection_topics_are_blocked_but_specific_primary_topic_is_not():
    generic = assess_discovered_topic(
        "Frequently asked questions for photographers about laser pointer light painting",
        [],
    )
    specific = assess_discovered_topic(
        "What laser power works for daylight presentations",
        [],
    )

    assert generic.gate_status == "blocked"
    assert "泛 FAQ" in generic.limitations[-1]
    assert specific.gate_status == "needs_evidence"


def test_non_english_and_canonical_duplicate_topics_are_blocked():
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

    assert translated.gate_status == "blocked"
    assert "英语" in translated.limitations[-1]
    assert duplicate.gate_status == "blocked"
    assert duplicate.overlap["max_score"] >= 0.68
    assert duplicate.overlap["matches"][0]["matched_tokens"] == [
        "battery",
        "charge",
    ]
    assert distinct.gate_status == "needs_evidence"
    assert "只有候选来自 GSC 查询" in distinct.next_step
    assert presentation_duplicate.gate_status == "blocked"
    assert presentation_duplicate.overlap["max_score"] >= 0.9


class FakeResearchAI:
    def __init__(self):
        self.calls = 0

    async def complete_json(self, system_prompt, user_payload):
        self.calls += 1
        refs = [item["evidence_id"] for item in user_payload["evidence"]]
        ref = refs[0]
        short_ref = f"{':'.join(ref.split(':')[:2])}:1"
        return AIResponse(
            provider="fake",
            model="deepseek-v4-flash",
            prompt_sha256="a" * 64,
            content={
                "summary": "Synthesis of the stored evidence",
                "topics": [
                    {
                        "topic": "How to protect a laser pointer lens during storage",
                        "intent": "Maintenance question requiring operator review",
                        "rationale": "The stored related question justifies further verification",
                        "evidence_ids": [short_ref, "external:999:1"],
                        "facts": [
                            f"{short_ref} contains a maintenance result",
                            "Unsupported assertion",
                        ],
                        "inference": ["Storage dust protection may need a separate explanation"],
                    }
                ],
            },
        )


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
    assert "Unsupported assertion" not in ai_candidate["facts_json"]
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
    assert created["portfolio_slot"] == "新文章 1"
    assert json.loads(analysis["metadata_json"])["top_count"] == portfolio_count


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


def test_structured_article_coverage_blocks_subtopics_and_branch_drift():
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
