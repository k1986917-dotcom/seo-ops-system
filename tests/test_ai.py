import asyncio
import dataclasses

import httpx
import pytest

from seo_ops.services.ai import (
    AIUnavailable,
    OpenAICompatibleProvider,
    build_ai_provider,
    complete_text_logged,
)
from seo_ops.db import connection


def test_ai_is_optional(settings):
    provider = build_ai_provider(settings)
    with pytest.raises(AIUnavailable):
        asyncio.run(provider.complete_json("system", {"evidence": []}))


def test_disabled_provider_rejects_text(settings):
    provider = build_ai_provider(settings)
    with pytest.raises(AIUnavailable):
        asyncio.run(provider.complete_text("system", "write me an article"))


@pytest.fixture
def ai_settings(settings):
    return dataclasses.replace(
        settings,
        ai_base_url="http://localhost:9999/v1",
        ai_model="test-model",
    )


class _FakeTransport(httpx.AsyncBaseTransport):
    """Records requests and replays canned responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def handle_async_request(self, request):
        import json as _json

        self.requests.append(_json.loads(request.content))
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)


def _patch_transport(monkeypatch, transport):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _chat_body(text):
    return {"choices": [{"message": {"content": text}}]}


class TestCompleteText:
    def test_returns_plain_text_without_json_mode(self, ai_settings, monkeypatch):
        transport = _FakeTransport([(200, _chat_body("# Article\n\nBody"))])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        result = asyncio.run(
            provider.complete_text("sys", "user", temperature=0.4, max_tokens=1234)
        )

        assert result.text == "# Article\n\nBody"
        assert result.model == "test-model"
        sent = transport.requests[0]
        # JSON mode would mangle long Markdown, so it must not be requested.
        assert "response_format" not in sent
        assert sent["temperature"] == 0.4
        assert sent["max_tokens"] == 1234
        assert sent["messages"][1]["content"] == "user"

    def test_retries_without_max_tokens_on_400(self, ai_settings, monkeypatch):
        transport = _FakeTransport([
            (400, {"error": "max_tokens too large"}),
            (200, _chat_body("recovered")),
        ])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        result = asyncio.run(provider.complete_text("sys", "user", max_tokens=999999))

        assert result.text == "recovered"
        assert len(transport.requests) == 2
        assert "max_tokens" in transport.requests[0]
        assert "max_tokens" not in transport.requests[1]

    def test_does_not_retry_400_without_max_tokens(self, ai_settings, monkeypatch):
        transport = _FakeTransport([(400, {"error": "bad model"})])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(provider.complete_text("sys", "user"))
        assert len(transport.requests) == 1

    def test_empty_completion_is_an_error(self, ai_settings, monkeypatch):
        transport = _FakeTransport([(200, _chat_body("   "))])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(ValueError):
            asyncio.run(provider.complete_text("sys", "user"))


class TestCompleteTextLogged:
    def test_success_is_recorded(self, ai_settings, monkeypatch):
        transport = _FakeTransport([(200, _chat_body("drafted"))])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        text = asyncio.run(
            complete_text_logged(
                provider,
                purpose="legacy_write_draft",
                system_prompt="sys",
                user_prompt="user",
                settings=ai_settings,
            )
        )
        assert text == "drafted"

        with connection(ai_settings) as conn:
            row = conn.execute(
                "SELECT purpose, status, model FROM ai_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["purpose"] == "legacy_write_draft"
        assert row["status"] == "success"
        assert row["model"] == "test-model"

    def test_failure_is_recorded_and_reraised(self, ai_settings, monkeypatch):
        transport = _FakeTransport([(500, {"error": "boom"})])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(
                complete_text_logged(
                    provider,
                    purpose="legacy_research_analyze",
                    system_prompt="sys",
                    user_prompt="user",
                    settings=ai_settings,
                )
            )

        with connection(ai_settings) as conn:
            row = conn.execute(
                "SELECT purpose, status, error_message FROM ai_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["purpose"] == "legacy_research_analyze"
        assert row["status"] == "failed"
        assert row["error_message"]

    def test_unavailable_provider_is_not_logged(self, settings):
        provider = build_ai_provider(settings)
        with pytest.raises(AIUnavailable):
            asyncio.run(
                complete_text_logged(
                    provider,
                    purpose="legacy_write_draft",
                    system_prompt="sys",
                    user_prompt="user",
                    settings=settings,
                )
            )
        with connection(settings) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM ai_runs").fetchone()["c"] == 0
