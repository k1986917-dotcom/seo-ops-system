from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from seo_ops.config import Settings, get_settings

NON_SECRET_FIELDS = {
    "ai_provider": "SEO_OPS_AI_PROVIDER",
    "ai_base_url": "SEO_OPS_AI_BASE_URL",
    "ai_model": "SEO_OPS_AI_MODEL",
    "serpapi_country": "SEO_OPS_SERPAPI_COUNTRY",
    "serpapi_language": "SEO_OPS_SERPAPI_LANGUAGE",
    "serpapi_device": "SEO_OPS_SERPAPI_DEVICE",
    "serpapi_location": "SEO_OPS_SERPAPI_LOCATION",
    "firecrawl_base_url": "SEO_OPS_FIRECRAWL_BASE_URL",
    "tavily_base_url": "SEO_OPS_TAVILY_BASE_URL",
    "trends_provider": "SEO_OPS_TRENDS_PROVIDER",
    "trends_geo": "SEO_OPS_TRENDS_GEO",
    "trends_timeframe": "SEO_OPS_TRENDS_TIMEFRAME",
}

SECRET_FIELDS = {
    "ai_api_key": "SEO_OPS_AI_API_KEY",
    "serpapi_api_key": "SEO_OPS_SERPAPI_KEY",
    "firecrawl_api_key": "SEO_OPS_FIRECRAWL_API_KEY",
    "tavily_api_key": "SEO_OPS_TAVILY_API_KEY",
}

_ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")
_ALLOWED_DEVICES = {"desktop", "mobile", "tablet"}
_ALLOWED_TRENDS_PROVIDERS = {"serpapi", "manual_csv", "google_alpha"}
_ALLOWED_TRENDS_TIMEFRAMES = {"today 3-m", "today 12-m", "today 5-y"}


def _one_line(value: str, *, label: str, max_length: int = 4096) -> str:
    cleaned = value.strip()
    if "\n" in cleaned or "\r" in cleaned or "\x00" in cleaned:
        raise ValueError(f"{label} 不能包含换行或空字符")
    if len(cleaned) > max_length:
        raise ValueError(f"{label} 过长")
    return cleaned


def _optional_url(value: str, *, label: str) -> str | None:
    cleaned = _one_line(value, label=label, max_length=500)
    if not cleaned:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} 必须是 http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} 不能包含用户名或密钥")
    return cleaned.rstrip("/")


def _validate_non_secret(field: str, value: str) -> str | None:
    if field in {"ai_base_url", "firecrawl_base_url", "tavily_base_url"}:
        return _optional_url(value, label=field)

    cleaned = _one_line(value, label=field, max_length=500)
    if field == "ai_provider" and cleaned != "openai-compatible":
        raise ValueError("当前只支持 openai-compatible AI Provider")
    if field == "serpapi_device" and cleaned not in _ALLOWED_DEVICES:
        raise ValueError("SerpAPI 设备类型无效")
    if field == "serpapi_country" and not re.fullmatch(r"[A-Za-z]{2}", cleaned):
        raise ValueError("SerpAPI 国家代码必须是两位字母")
    if field == "serpapi_language" and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?", cleaned):
        raise ValueError("SerpAPI 语言代码无效")
    if field == "trends_provider" and cleaned not in _ALLOWED_TRENDS_PROVIDERS:
        raise ValueError("Google Trends 接入方式无效")
    if field == "trends_timeframe" and cleaned not in _ALLOWED_TRENDS_TIMEFRAMES:
        raise ValueError("Google Trends 时间范围无效")
    if field == "trends_geo" and not re.fullmatch(r"[A-Za-z0-9-]{0,12}", cleaned):
        raise ValueError("Google Trends 地区代码无效")
    if field in {"ai_model", "serpapi_location"}:
        return cleaned or None
    return cleaned


def _write_env_file(path: Path, updates: Mapping[str, str], removals: set[str]) -> None:
    original = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    handled: set[str] = set()

    for line in original:
        match = _ENV_LINE.match(line)
        key = match.group(1) if match else None
        if not key or (key not in updates and key not in removals):
            output.append(line)
            continue
        if key in handled or key in removals:
            continue
        output.append(f"{key}={updates[key]}")
        handled.add(key)

    if not original:
        output.extend(
            [
                "# SEO Ops 本地配置。禁止提交到 Git。",
                "# 密钥由设置页写入，页面和数据库不会回显或保存密钥。",
            ]
        )
    for key in sorted(updates):
        if key not in handled:
            output.append(f"{key}={updates[key]}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".env.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def update_local_settings(
    current: Settings,
    submitted: Mapping[str, str],
    clear_secrets: set[str] | None = None,
) -> Settings:
    """Persist selected local settings without ever returning secret values to a template."""

    clear = clear_secrets or set()
    replacements: dict[str, str | None] = {}
    env_updates: dict[str, str] = {}
    env_removals: set[str] = set()

    for field, env_key in NON_SECRET_FIELDS.items():
        if field not in submitted:
            continue
        value = _validate_non_secret(field, str(submitted[field]))
        replacements[field] = value
        if value is None or value == "":
            env_removals.add(env_key)
        else:
            env_updates[env_key] = value

    for field, env_key in SECRET_FIELDS.items():
        if field in clear:
            replacements[field] = None
            env_removals.add(env_key)
            continue
        raw = str(submitted.get(field, ""))
        if not raw.strip():
            continue
        value = _one_line(raw, label=field)
        replacements[field] = value
        env_updates[env_key] = value

    _write_env_file(current.project_root / ".env", env_updates, env_removals)
    for key, value in env_updates.items():
        os.environ[key] = value
    for key in env_removals:
        os.environ.pop(key, None)
    get_settings.cache_clear()
    return replace(current, **replacements)
