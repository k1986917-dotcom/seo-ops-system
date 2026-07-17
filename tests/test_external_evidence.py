from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import list_opportunities
from seo_ops.services.action_workflow import ActionWorkflowError, record_opportunity_decision
from seo_ops.services.external_evidence import collect_query_evidence
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def _query_opportunity(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    run_analysis(1, settings)
    with connection(settings) as conn:
        opportunities = list_opportunities(conn, 1)
    return next(item for item in opportunities if item["rule_key"] == "query_page_evidence_gap")


def _trends_response(secret: str) -> dict:
    return {
        "api_key": secret,
        "search_metadata": {
            "status": "Success",
            "created_at": "2026-07-14 10:00:00 UTC",
        },
        "search_parameters": {
            "engine": "google_trends",
            "q": "best example laser",
            "date": "today 12-m",
            "geo": "US",
        },
        "interest_over_time": {
            "timeline_data": [
                {
                    "date": "Jun 1 – 7, 2026",
                    "timestamp": "1780272000",
                    "values": [{"query": "best example laser", "extracted_value": 40}],
                },
                {
                    "date": "Jun 8 – 14, 2026",
                    "timestamp": "1780876800",
                    "values": [{"query": "best example laser", "extracted_value": 55}],
                },
            ]
        },
    }


def test_external_evidence_snapshots_are_governed_and_reused(settings):
    opportunity = _query_opportunity(settings)
    secret = "unit-test-search-secret"
    configured = replace(settings, serpapi_api_key=secret)
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("api_key") == secret
        engine = str(request.url.params["engine"])
        calls.append(engine)
        if engine == "google":
            return httpx.Response(
                200,
                json={
                    "api_key": secret,
                    "search_metadata": {
                        "status": "Success",
                        "created_at": "2026-07-14 10:00:00 UTC",
                    },
                    "search_parameters": {
                        "engine": "google",
                        "q": "best example laser",
                    },
                    "organic_results": [
                        {
                            "position": 1,
                            "title": "Independent laser guide",
                            "link": "https://example.net/laser-guide",
                        }
                    ],
                    "related_questions": [{"question": "Which laser is best?"}],
                },
            )
        return httpx.Response(200, json=_trends_response(secret))

    transport = httpx.MockTransport(handler)
    outcome = asyncio.run(
        collect_query_evidence(opportunity["id"], configured, transport=transport)
    )
    assert outcome.status == "success"
    assert len(outcome.runs) == 2
    assert outcome.new_candidate_id is not None

    with connection(configured) as conn:
        runs = conn.execute("SELECT * FROM external_runs ORDER BY id").fetchall()
        evidence_count = conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0]
        opportunities = list_opportunities(conn, 1)
    assert len(runs) == 2
    assert evidence_count == 2
    assert all(row["status"] == "success" for row in runs)
    trends_parameters = json.loads(runs[1]["parameters_json"])
    assert trends_parameters["tz"] == "-480"
    for row in runs:
        assert secret not in row["parameters_json"]
        assert "api_key" not in row["parameters_json"]
        assert secret.encode() not in Path(row["response_snapshot_path"]).read_bytes()

    candidate = next(item for item in opportunities if item["rule_key"] == "new_article_candidate")
    assert candidate["gate_status"] == "blocked"
    assert "query_page_mapping" in candidate["evidence"]["missing"]
    assert candidate["method_version"] == "evidence-workflow-0.4.1"

    second = asyncio.run(collect_query_evidence(opportunity["id"], configured, transport=transport))
    assert second.status == "success"
    assert all(run.reused for run in second.runs)
    assert calls == ["google", "google_trends"]
    with connection(configured) as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_runs").fetchone()[0] == 2


def test_site_result_blocks_new_article_candidate(settings):
    opportunity = _query_opportunity(settings)
    configured = replace(settings, serpapi_api_key="secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["engine"] == "google_trends":
            return httpx.Response(200, json=_trends_response("secret"))
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success"},
                "search_parameters": {"engine": "google", "q": "best example laser"},
                "organic_results": [
                    {
                        "position": 4,
                        "title": "Example Laser Guide",
                        "link": "https://laserpointerhub.com/blog/example",
                    }
                ],
            },
        )

    outcome = asyncio.run(
        collect_query_evidence(
            opportunity["id"], configured, transport=httpx.MockTransport(handler)
        )
    )
    with connection(configured) as conn:
        candidate = next(
            item for item in list_opportunities(conn, 1) if item["id"] == outcome.new_candidate_id
        )
    assert candidate["gate_status"] == "blocked"
    assert "SERP 前 10 条中已出现本站页面" in candidate["gate_reasons"]
    try:
        record_opportunity_decision(candidate["id"], "accepted", settings=configured)
    except ActionWorkflowError as exc:
        assert "已阻塞" in str(exc)
    else:
        raise AssertionError("blocked candidate must not enter execution")
