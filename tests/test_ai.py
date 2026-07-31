import asyncio
import dataclasses

import httpx
import pytest

from seo_ops.db import connection
from seo_ops.services.ai import (
    AIEmptyTextError,
    AIUnavailable,
    OpenAICompatibleProvider,
    build_ai_provider,
    complete_text_logged,
)


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

    def test_deepseek_request_can_disable_thinking(
        self, ai_settings, monkeypatch
    ):
        deepseek_settings = dataclasses.replace(
            ai_settings,
            ai_base_url="https://api.deepseek.com",
            ai_model="deepseek-v4-flash",
        )
        transport = _FakeTransport([(200, _chat_body("drafted"))])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(deepseek_settings)
        result = asyncio.run(
            provider.complete_text(
                "sys", "user", max_tokens=8000, thinking_mode="disabled"
            )
        )

        assert result.text == "drafted"
        assert transport.requests[0]["thinking"] == {"type": "disabled"}

    def test_non_deepseek_request_omits_thinking_toggle(
        self, ai_settings, monkeypatch
    ):
        transport = _FakeTransport([(200, _chat_body("drafted"))])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        asyncio.run(
            provider.complete_text(
                "sys", "user", max_tokens=8000, thinking_mode="disabled"
            )
        )

        assert "thinking" not in transport.requests[0]

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
        with pytest.raises(AIEmptyTextError):
            asyncio.run(provider.complete_text("sys", "user"))

    def test_empty_completion_reports_safe_provider_diagnostics(
        self, ai_settings, monkeypatch
    ):
        transport = _FakeTransport([(
            200,
            {
                "model": "deepseek-v4-flash",
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": "private reasoning must not be logged",
                    },
                }],
                "usage": {
                    "completion_tokens": 8000,
                    "completion_tokens_details": {"reasoning_tokens": 7984},
                },
            },
        )])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(AIEmptyTextError) as caught:
            asyncio.run(provider.complete_text("sys", "user"))

        message = str(caught.value)
        assert "finish_reason=length" in message
        assert "completion_tokens=8000" in message
        assert "reasoning_tokens=7984" in message
        assert "reasoning_chars=36" in message
        assert "private reasoning" not in message
        assert caught.value.retryable is False

    def test_resource_failure_empty_completion_is_retryable(
        self, ai_settings, monkeypatch
    ):
        transport = _FakeTransport([(
            200,
            {
                "choices": [{
                    "finish_reason": "insufficient_system_resource",
                    "message": {"content": None},
                }],
            },
        )])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(AIEmptyTextError) as caught:
            asyncio.run(provider.complete_text("sys", "user"))

        assert caught.value.retryable is True

    def test_content_filter_empty_completion_is_not_retryable(
        self, ai_settings, monkeypatch
    ):
        transport = _FakeTransport([(
            200,
            {
                "choices": [{
                    "finish_reason": "content_filter",
                    "message": {"content": ""},
                }],
            },
        )])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(AIEmptyTextError) as caught:
            asyncio.run(provider.complete_text("sys", "user"))

        assert caught.value.retryable is False


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

    def test_empty_text_log_excludes_reasoning_content(
        self, ai_settings, monkeypatch
    ):
        secret_reasoning = "private chain content must never enter ai_runs"
        transport = _FakeTransport([(
            200,
            {
                "model": "deepseek-v4-flash",
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": secret_reasoning,
                    },
                }],
                "usage": {
                    "completion_tokens": 8000,
                    "completion_tokens_details": {"reasoning_tokens": 7990},
                },
            },
        )])
        _patch_transport(monkeypatch, transport)

        provider = OpenAICompatibleProvider(ai_settings)
        with pytest.raises(AIEmptyTextError):
            asyncio.run(
                complete_text_logged(
                    provider,
                    purpose="legacy_write_revise_body",
                    system_prompt="sys",
                    user_prompt="user",
                    settings=ai_settings,
                )
            )

        with connection(ai_settings) as conn:
            row = conn.execute(
                "SELECT status, error_message FROM ai_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["status"] == "failed"
        assert "finish_reason=length" in row["error_message"]
        assert "reasoning_tokens=7990" in row["error_message"]
        assert secret_reasoning not in row["error_message"]

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
