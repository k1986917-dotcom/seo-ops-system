from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.utils import json_dumps, utc_now


class AIUnavailable(RuntimeError):
    """Raised when the optional AI provider is not configured."""


class AIEmptyTextError(ValueError):
    """Provider succeeded but returned no final assistant text."""

    def __init__(
        self,
        *,
        finish_reason: str | None = None,
        completion_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_chars: int = 0,
        response_model: str | None = None,
    ) -> None:
        self.finish_reason = finish_reason
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.reasoning_chars = reasoning_chars
        self.response_model = response_model
        self.retryable = finish_reason in {
            None,
            "stop",
            "insufficient_system_resource",
        }

        diagnostics = [
            f"finish_reason={finish_reason or 'missing'}",
            "completion_tokens="
            f"{completion_tokens if completion_tokens is not None else 'unknown'}",
            "reasoning_tokens="
            f"{reasoning_tokens if reasoning_tokens is not None else 'unknown'}",
            f"reasoning_chars={reasoning_chars}",
        ]
        if response_model:
            diagnostics.append(f"response_model={response_model}")
        super().__init__(f"AI 返回空文本（{'; '.join(diagnostics)}）")


@dataclass(frozen=True, slots=True)
class AIResponse:
    provider: str
    model: str
    content: dict[str, Any]
    prompt_sha256: str


@dataclass(frozen=True, slots=True)
class AITextResponse:
    """Free-form completion. Legacy Research/Write produce long Markdown —
    material packs and 2500-4000 word drafts — which JSON mode truncates and
    mangles through string escaping."""

    provider: str
    model: str
    text: str
    prompt_sha256: str


# Long-form generation runs well past the 150s used for structured evidence
# explanation; a 4000-word draft regularly needs several minutes.
TEXT_TIMEOUT_SECONDS = 600.0


class AIProvider(Protocol):
    async def complete_json(
        self, system_prompt: str, user_payload: dict[str, Any]
    ) -> AIResponse: ...

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        thinking_mode: str | None = None,
    ) -> AITextResponse: ...


class DisabledAIProvider:
    async def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> AIResponse:
        raise AIUnavailable("AI 尚未配置；核心分析仍可正常使用")

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        thinking_mode: str | None = None,
    ) -> AITextResponse:
        raise AIUnavailable("AI 尚未配置；核心分析仍可正常使用")


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _supports_deepseek_thinking(settings: Settings) -> bool:
    hostname = urlparse(settings.ai_base_url or "").hostname
    return bool(hostname and hostname.lower() == "api.deepseek.com")


def _empty_text_error(
    response_payload: dict[str, Any],
    choice: dict[str, Any],
    message: dict[str, Any],
) -> AIEmptyTextError:
    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    token_details = usage.get("completion_tokens_details")
    if not isinstance(token_details, dict):
        token_details = {}

    finish_reason = choice.get("finish_reason")
    completion_tokens = _non_negative_int(usage.get("completion_tokens"))
    reasoning_tokens = _non_negative_int(token_details.get("reasoning_tokens"))
    reasoning_content = message.get("reasoning_content")
    reasoning_chars = (
        len(reasoning_content) if isinstance(reasoning_content, str) else 0
    )
    response_model = response_payload.get("model")

    return AIEmptyTextError(
        finish_reason=(
            finish_reason if isinstance(finish_reason, str) else None
        ),
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        reasoning_chars=reasoning_chars,
        response_model=(
            response_model.strip()
            if isinstance(response_model, str) and response_model.strip()
            else None
        ),
    )


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings):
        if not settings.ai_enabled:
            raise AIUnavailable("AI 需要配置 SEO_OPS_AI_BASE_URL 与 SEO_OPS_AI_MODEL")
        self.settings = settings

    @property
    def endpoint(self) -> str:
        base = (self.settings.ai_base_url or "").rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    async def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> AIResponse:
        prompt_text = json_dumps({"system": system_prompt, "user": user_payload})
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_api_key}"
        payload = {
            "model": self.settings.ai_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json_dumps(user_payload)},
            ],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=20.0)) as client:
            response = await client.post(self.endpoint, headers=headers, json=payload)
            if response.status_code == 400:
                payload.pop("response_format", None)
                response = await client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            response_payload = response.json()
        raw_content = response_payload["choices"][0]["message"]["content"]
        if isinstance(raw_content, dict):
            parsed = raw_content
        else:
            clean = str(raw_content).strip()
            if clean.startswith("```"):
                clean = clean.removeprefix("```json").removeprefix("```")
                clean = clean.removesuffix("```").strip()
            parsed = json.loads(clean)
        if not isinstance(parsed, dict):
            raise ValueError("AI 返回值不是 JSON 对象")
        return AIResponse(
            provider=self.settings.ai_provider,
            model=self.settings.ai_model or "unknown",
            content=parsed,
            prompt_sha256=prompt_hash,
        )

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        thinking_mode: str | None = None,
    ) -> AITextResponse:
        if thinking_mode not in (None, "enabled", "disabled"):
            raise ValueError("thinking_mode 必须是 enabled、disabled 或 None")
        prompt_text = json_dumps({"system": system_prompt, "user": user_prompt})
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_api_key}"
        payload: dict[str, Any] = {
            "model": self.settings.ai_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if thinking_mode and _supports_deepseek_thinking(self.settings):
            payload["thinking"] = {"type": thinking_mode}
        timeout = httpx.Timeout(TEXT_TIMEOUT_SECONDS, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.endpoint, headers=headers, json=payload)
            # Providers differ on the max_tokens ceiling; retry once without it
            # rather than failing a draft that is otherwise ready to generate.
            if response.status_code == 400 and "max_tokens" in payload:
                payload.pop("max_tokens", None)
                response = await client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            response_payload = response.json()
        if not isinstance(response_payload, dict):
            raise ValueError("AI 响应不是 JSON 对象")
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("AI 响应缺少 choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("AI 响应 choice 不是对象")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("AI 响应缺少 assistant message")
        raw_content = message.get("content")
        text = str(raw_content or "").strip()
        if not text:
            raise _empty_text_error(response_payload, choice, message)
        return AITextResponse(
            provider=self.settings.ai_provider,
            model=str(response_payload.get("model") or self.settings.ai_model or "unknown"),
            text=text,
            prompt_sha256=prompt_hash,
        )


async def complete_text_logged(
    provider: AIProvider,
    *,
    purpose: str,
    system_prompt: str,
    user_prompt: str,
    site_id: int = 1,
    input_refs: Any = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    thinking_mode: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Run a free-form completion and record it in ai_runs, success or failure.

    Returns the generated text. Used by the Legacy Research/Write stages so
    their calls show up in the same audit trail as structured AI runs.
    """

    active_settings = settings or get_settings()
    refs = input_refs if input_refs is not None else []
    try:
        response = await provider.complete_text(
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_mode=thinking_mode,
        )
    except AIUnavailable:
        raise
    except Exception as exc:
        prompt_hash = hashlib.sha256(
            json_dumps({"system": system_prompt, "user": user_prompt}).encode("utf-8")
        ).hexdigest()
        with connection(active_settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, status, error_message, created_at
                ) VALUES(?, NULL, ?, ?, ?, ?, ?, 'failed', ?, ?)
                """,
                (
                    site_id,
                    purpose,
                    active_settings.ai_provider,
                    active_settings.ai_model or "unconfigured",
                    prompt_hash,
                    json_dumps(refs),
                    str(exc),
                    utc_now(),
                ),
            )
        raise

    with connection(active_settings) as conn:
        conn.execute(
            """
            INSERT INTO ai_runs(
                site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                input_refs_json, output_json, status, created_at
            ) VALUES(?, NULL, ?, ?, ?, ?, ?, ?, 'success', ?)
            """,
            (
                site_id,
                purpose,
                response.provider,
                response.model,
                response.prompt_sha256,
                json_dumps(refs),
                json_dumps({
                    "chars": len(response.text),
                    "thinking_mode": thinking_mode,
                }),
                utc_now(),
            ),
        )
    return response.text


def build_ai_provider(settings: Settings | None = None) -> AIProvider:
    active_settings = settings or get_settings()
    if not active_settings.ai_enabled:
        return DisabledAIProvider()
    if active_settings.ai_provider != "openai-compatible":
        raise AIUnavailable(f"暂不支持 AI Provider: {active_settings.ai_provider}")
    return OpenAICompatibleProvider(active_settings)


SYSTEM_PROMPT = """你是 SEO 运营系统中的证据解释器，不是自由选题助手。
只能使用输入 evidence 中的事实，不能补充模型记忆、猜测、虚构体验或来源。
不得修改 gate_status、strength、confidence、effort、priority 或推荐动作类型。
把事实、推断、不确定性和下一步分开。输出严格 JSON：
{
  "summary": "一句话解释",
  "facts": ["有 evidence_id 支持的事实"],
  "inference": ["从事实得到的有限推断"],
  "next_steps": ["运营者可执行步骤"],
  "cautions": ["局限和风险"]
}
每条 facts 必须包含对应 evidence_id。没有证据时明确写缺失，不要补齐。"""


def _validate_explanation(content: dict[str, Any]) -> dict[str, Any]:
    required = ("summary", "facts", "inference", "next_steps", "cautions")
    missing = [key for key in required if key not in content]
    if missing:
        raise ValueError(f"AI 输出缺少字段: {', '.join(missing)}")
    for key in required[1:]:
        if not isinstance(content[key], list):
            raise ValueError(f"AI 输出字段 {key} 必须是数组")
    return {key: content[key] for key in required}


async def explain_opportunity(
    opportunity: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    active_settings = settings or get_settings()
    provider = build_ai_provider(active_settings)
    payload = {
        "opportunity": {
            "id": opportunity["id"],
            "title": opportunity["title"],
            "rule_key": opportunity["rule_key"],
            "target_ref": opportunity["target_ref"],
            "recommended_action": opportunity["recommended_action"],
            "gate_status": opportunity["gate_status"],
            "strength": opportunity["strength"],
            "confidence": opportunity["confidence"],
            "effort": opportunity["effort"],
            "priority": opportunity["priority"],
        },
        "evidence": opportunity["evidence"],
        "gate_reasons": opportunity["gate_reasons"],
    }
    input_refs = opportunity["evidence"].get("evidence_ids", [])
    try:
        response = await provider.complete_json(SYSTEM_PROMPT, payload)
        explanation = _validate_explanation(response.content)
        with connection(active_settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, output_json, status, created_at
                ) VALUES(?, ?, 'explain_opportunity', ?, ?, ?, ?, ?, 'success', ?)
                """,
                (
                    opportunity["site_id"],
                    opportunity["id"],
                    response.provider,
                    response.model,
                    response.prompt_sha256,
                    json_dumps(input_refs),
                    json_dumps(explanation),
                    utc_now(),
                ),
            )
            conn.execute(
                "UPDATE opportunities SET ai_explanation_json = ? WHERE id = ?",
                (json_dumps(explanation), opportunity["id"]),
            )
        return explanation
    except AIUnavailable:
        raise
    except Exception as exc:
        prompt_hash = hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()
        with connection(active_settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, status, error_message, created_at
                ) VALUES(?, ?, 'explain_opportunity', ?, ?, ?, ?, 'failed', ?, ?)
                """,
                (
                    opportunity["site_id"],
                    opportunity["id"],
                    active_settings.ai_provider,
                    active_settings.ai_model or "unconfigured",
                    prompt_hash,
                    json_dumps(input_refs),
                    str(exc),
                    utc_now(),
                ),
            )
        raise
