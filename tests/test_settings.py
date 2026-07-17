from __future__ import annotations

import os
import stat
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from seo_ops.services.external_sources import test_source_connection as check_source_connection
from seo_ops.web.app import create_app


def _settings_form(secret: str) -> dict[str, str]:
    return {
        "ai_provider": "openai-compatible",
        "ai_base_url": "",
        "ai_model": "",
        "ai_api_key": "",
        "serpapi_api_key": secret,
        "serpapi_country": "us",
        "serpapi_language": "en",
        "serpapi_device": "desktop",
        "serpapi_location": "United States",
        "firecrawl_api_key": "",
        "firecrawl_base_url": "https://api.firecrawl.dev/v2",
        "tavily_api_key": "",
        "tavily_base_url": "https://api.tavily.com",
        "trends_provider": "serpapi",
        "trends_geo": "US",
        "trends_timeframe": "today 12-m",
        "research_serpapi_budget": "4",
        "research_firecrawl_budget": "3",
        "research_tavily_budget": "5",
        "research_ai_budget": "1",
        "content_ai_call_limit": "4",
    }


def test_settings_page_saves_secret_without_reflecting_it(settings):
    secret = "unit-test-serpapi-secret"
    app = create_app(replace(settings, ai_model="deepseek-v4-flash"))
    try:
        with TestClient(app) as client:
            page = client.get("/settings")
            research = client.get("/research")
            saved = client.post("/settings", data=_settings_form(secret), follow_redirects=True)
            health = client.get("/api/health")

        assert page.status_code == 200
        assert "设置与数据连接" in page.text
        assert "Google Trends" in page.text
        assert "每轮外部主题调研预算" in page.text
        assert research.status_code == 200
        assert "每篇文章 AI 调用上限" in page.text
        assert "deepseek-v4-flash" in page.text
        assert saved.status_code == 200
        assert secret not in saved.text
        assert health.json()["configured_sources"]["serpapi"] is True

        env_path = settings.project_root / ".env"
        assert secret in env_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    finally:
        os.environ.pop("SEO_OPS_SERPAPI_KEY", None)
        for key in (
            "SEO_OPS_RESEARCH_SERPAPI_BUDGET",
            "SEO_OPS_RESEARCH_FIRECRAWL_BUDGET",
            "SEO_OPS_RESEARCH_TAVILY_BUDGET",
            "SEO_OPS_RESEARCH_AI_BUDGET",
            "SEO_OPS_CONTENT_AI_CALL_LIMIT",
        ):
            os.environ.pop(key, None)


def test_settings_rejects_cross_origin_post(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/settings",
            data=_settings_form("not-saved"),
            headers={"Origin": "https://malicious.example"},
        )
    assert response.status_code == 403
    assert not (settings.project_root / ".env").exists()


def test_settings_rejects_research_budget_above_hard_limit(settings):
    form = _settings_form("")
    form["research_serpapi_budget"] = "11"
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/settings", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert "serpapi 每轮预算不能超过 10" in response.text
    assert not (settings.project_root / ".env").exists()


def test_settings_rejects_content_ai_limit_above_hard_cap(settings):
    form = _settings_form("")
    form["content_ai_call_limit"] = "11"
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/settings", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert "每篇文章 AI 调用上限必须在 3–10 之间" in response.text
    assert not (settings.project_root / ".env").exists()


async def _serpapi_success_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params.get("api_key") == "unit-test-secret"
    return httpx.Response(
        200,
        json={
            "api_key": "unit-test-secret",
            "account_status": "Active",
            "plan_name": "Test",
            "total_searches_left": 10,
        },
    )


def test_serpapi_connection_discards_secret_from_result(settings):
    configured = replace(settings, serpapi_api_key="unit-test-secret")
    transport = httpx.MockTransport(_serpapi_success_handler)

    import asyncio

    result = asyncio.run(check_source_connection("serpapi", configured, transport=transport))
    assert result.ok is True
    assert result.details["searches_left"] == 10
    assert "unit-test-secret" not in str(result)


def test_serpapi_network_error_never_returns_request_url(settings):
    configured = replace(settings, serpapi_api_key="unit-test-secret")

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    import asyncio

    result = asyncio.run(
        check_source_connection("serpapi", configured, transport=httpx.MockTransport(fail))
    )
    assert result.ok is False
    assert "unit-test-secret" not in result.message
    assert "http" not in result.message.lower()
