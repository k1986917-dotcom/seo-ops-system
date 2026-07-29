from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from seo_ops.db import connection
from seo_ops.services.external_evidence import ExternalRunResult
from seo_ops.services.hermes_orchestrator import (
    collect_hermes_search_results,
)
from seo_ops.web.app import create_app


def test_hermes_start_creates_action_and_stops_for_manual_search(settings):
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/hermes/runs",
            json={
                "site": "laserpointerhub",
                "topic": "How to test a laser pointer outdoors",
                "requirements": "English; explain the practical setup for beginners",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "needs_manual_search"
    assert body["legacy_stage"] == "r0_prompt"
    assert body["next"] == "manual_search"
    assert body["search"]["providers"] == []
    with connection(settings) as conn:
        action = conn.execute(
            "SELECT target_ref, decision, workflow_status, legacy_stage, baseline_json "
            "FROM actions WHERE id = ?",
            (body["action_id"],),
        ).fetchone()
    assert action["target_ref"] == "How to test a laser pointer outdoors"
    assert action["decision"] == "accepted"
    assert action["workflow_status"] == "in_progress"
    assert action["legacy_stage"] == "r0_prompt"
    assert "practical setup" in action["baseline_json"]

    with TestClient(app) as client:
        status = client.get(f"/api/hermes/runs/{body['action_id']}")
        prompt = client.get(f"/api/hermes/runs/{body['action_id']}/prompt")
    assert status.status_code == 200
    assert status.json()["next_stage"].startswith("继续外部搜索")
    assert prompt.status_code == 200
    assert "SERP Analysis" in prompt.json()["prompt"]


def test_hermes_search_bridge_formats_new_system_results(settings, monkeypatch):
    with connection(settings) as conn:
        site = dict(conn.execute("SELECT * FROM sites WHERE id = 1").fetchone())

    serp = ExternalRunResult(
        "serp_snapshot",
        "success",
        11,
        "external:11:serp_snapshot",
        False,
        "snapshot saved",
        {
            "query": "laser pointer outdoors",
            "organic_results": [
                {
                    "title": "Outdoor laser visibility guide",
                    "link": "https://example.test/outdoor",
                }
            ],
            "related_questions": ["Why does a beam disappear outdoors?"],
            "related_searches": ["laser beam visibility distance"],
        },
    )
    tavily = ExternalRunResult(
        "source_discovery",
        "success",
        12,
        "external:12:source_discovery",
        False,
        "snapshot saved",
        {
            "query": "laser pointer outdoors",
            "results": [
                {
                    "title": "Safety source",
                    "url": "https://example.test/safety",
                    "content": "Use suitable controls and avoid eye exposure.",
                }
            ],
        },
    )

    async def fake_topic_evidence(*args, **kwargs):
        from seo_ops.services.external_evidence import EvidenceCollectionOutcome

        return EvidenceCollectionOutcome("success", "ok", (serp,), None)

    async def fake_tavily(*args, **kwargs):
        return tavily

    monkeypatch.setattr(
        "seo_ops.services.hermes_orchestrator.collect_topic_query_evidence",
        fake_topic_evidence,
    )
    monkeypatch.setattr(
        "seo_ops.services.hermes_orchestrator.execute_tavily_search",
        fake_tavily,
    )
    # Settings in the fixture have no credentials; replace only the immutable
    # dataclass fields through a copy so the bridge exercises both providers.
    from dataclasses import replace

    configured = replace(settings, serpapi_api_key="serp", tavily_api_key="tavily")
    result = asyncio.run(
        collect_hermes_search_results(
            site_id=int(site["id"]),
            topic="laser pointer outdoors",
            requirements="focus on visibility",
            settings=configured,
        )
    )

    assert result.status == "success"
    assert "Outdoor laser visibility guide" in result.markdown
    assert "Why does a beam disappear outdoors?" in result.markdown
    assert "Safety source" in result.markdown
    assert {item["evidence_id"] for item in result.providers} == {
        "external:11:serp_snapshot",
        "external:12:source_discovery",
    }
