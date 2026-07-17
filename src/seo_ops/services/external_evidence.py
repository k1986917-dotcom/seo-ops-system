from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.ingest.snapshot import save_snapshot
from seo_ops.repositories import get_opportunity
from seo_ops.rules.evidence_workflow import (
    EXTERNAL_QUERY_REVIEW_RULE,
    METHOD_VERSION,
    NEW_ARTICLE_CANDIDATE_RULE,
    assess_new_article_candidate,
)
from seo_ops.utils import json_dumps, json_loads, utc_now

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
MAX_EXTERNAL_RESPONSE_BYTES = 20 * 1024 * 1024
DEFAULT_REUSE_HOURS = 24


class EvidenceCollectionUnavailable(RuntimeError):
    """Raised when a governed evidence run cannot be started."""


@dataclass(frozen=True, slots=True)
class ExternalRunResult:
    purpose: str
    status: str
    run_id: int | None
    evidence_id: str | None
    reused: bool
    message: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvidenceCollectionOutcome:
    status: str
    message: str
    runs: tuple[ExternalRunResult, ...]
    new_candidate_id: int | None


def _request_sha256(provider: str, purpose: str, parameters: dict[str, Any]) -> str:
    canonical = json_dumps(
        {"provider": provider, "purpose": purpose, "parameters": parameters}
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _serpapi_timezone_offset(timezone_name: str) -> str:
    try:
        offset = datetime.now(ZoneInfo(timezone_name)).utcoffset()
    except (ValueError, ZoneInfoNotFoundError):
        return "0"
    if offset is None:
        return "0"
    # SerpAPI follows the JavaScript convention: minutes west of UTC.
    return str(-int(offset.total_seconds() / 60))


def _redact_response(content: bytes, secret: str) -> bytes:
    sanitized = content
    replacements = {secret, quote(secret, safe=""), quote_plus(secret)}
    for value in replacements:
        if value:
            sanitized = sanitized.replace(value.encode("utf-8"), b"[REDACTED]")
    return sanitized


def _safe_http_error(status_code: int, provider: str = "SerpAPI") -> tuple[str, str]:
    if status_code == 403 and provider == "Firecrawl":
        return "forbidden", "Firecrawl 请求被拒；可能是目标站点、套餐或凭据权限限制"
    if status_code in {401, 403}:
        return "auth_error", f"{provider} 凭据无效或没有权限"
    if status_code == 429:
        return "rate_limited", f"{provider} 额度或调用频率受限"
    return f"http_{status_code}", f"{provider} 返回 HTTP {status_code}"


def _parse_completed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _load_reusable_run(
    settings: Settings,
    *,
    site_id: int,
    provider: str,
    purpose: str,
    request_hash: str,
    reuse_hours: int = DEFAULT_REUSE_HOURS,
) -> ExternalRunResult | None:
    cutoff = datetime.now(UTC) - timedelta(hours=reuse_hours)
    with connection(settings) as conn:
        row = conn.execute(
            """
            SELECT er.*, ei.evidence_id, ei.payload_json
            FROM external_runs er
            JOIN evidence_items ei ON ei.external_run_id = er.id
            WHERE er.site_id = ? AND er.provider = ? AND er.purpose = ?
              AND er.request_sha256 = ? AND er.status = 'success'
            ORDER BY er.completed_at DESC, er.id DESC
            LIMIT 1
            """,
            (site_id, provider, purpose, request_hash),
        ).fetchone()
    if (
        not row
        or (_parse_completed_at(row["completed_at"]) or datetime.min.replace(tzinfo=UTC)) < cutoff
    ):
        return None
    snapshot_path = row["response_snapshot_path"]
    if not snapshot_path or not Path(snapshot_path).is_file():
        return None
    return ExternalRunResult(
        purpose=purpose,
        status="success",
        run_id=int(row["id"]),
        evidence_id=str(row["evidence_id"]),
        reused=True,
        message="复用 24 小时内相同口径的不可变快照",
        payload=json_loads(row["payload_json"], {}),
    )


def _start_run(
    settings: Settings,
    *,
    site_id: int,
    opportunity_id: int | None,
    provider: str,
    purpose: str,
    request_hash: str,
    input_refs: list[str],
    parameters: dict[str, Any],
) -> int:
    with connection(settings) as conn:
        cursor = conn.execute(
            """
            INSERT INTO external_runs(
                site_id, opportunity_id, provider, purpose, request_sha256,
                input_refs_json, parameters_json, status, started_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                site_id,
                opportunity_id,
                provider,
                purpose,
                request_hash,
                json_dumps(input_refs),
                json_dumps(parameters),
                utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def _record_failure(
    settings: Settings,
    run_id: int,
    *,
    error_code: str,
    message: str,
    snapshot_path: str | None = None,
    response_sha256: str | None = None,
    response_size: int | None = None,
    response_received: bool = False,
) -> ExternalRunResult:
    usage = {
        "requests_attempted": 1,
        "response_received": response_received,
        "billable": None,
        "monetary_cost": None,
    }
    with connection(settings) as conn:
        purpose = str(
            conn.execute("SELECT purpose FROM external_runs WHERE id = ?", (run_id,)).fetchone()[
                "purpose"
            ]
        )
        conn.execute(
            """
            UPDATE external_runs
            SET status = 'failed', response_snapshot_path = ?, response_sha256 = ?,
                response_size = ?, usage_json = ?, error_code = ?, error_message = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                snapshot_path,
                response_sha256,
                response_size,
                json_dumps(usage),
                error_code,
                message,
                utc_now(),
                run_id,
            ),
        )
    return ExternalRunResult(purpose, "failed", run_id, None, False, message)


def _serp_payload(raw: dict[str, Any]) -> dict[str, Any]:
    depth = int(EXTERNAL_QUERY_REVIEW_RULE["config"]["organic_result_depth"])
    organic_results = []
    for item in raw.get("organic_results") or []:
        if not isinstance(item, dict):
            continue
        organic_results.append(
            {
                "position": item.get("position"),
                "title": item.get("title"),
                "link": item.get("link"),
                "displayed_link": item.get("displayed_link"),
            }
        )
        if len(organic_results) >= depth:
            break
    feature_keys = (
        "ai_overview",
        "answer_box",
        "featured_snippet",
        "knowledge_graph",
        "local_results",
        "shopping_results",
        "inline_videos",
        "related_questions",
        "discussions_and_forums",
    )
    metadata = raw.get("search_metadata") or {}
    related_questions = [
        str(item.get("question")).strip()
        for item in (raw.get("related_questions") or [])
        if isinstance(item, dict) and str(item.get("question") or "").strip()
    ][:10]
    related_searches = [
        str(item.get("query")).strip()
        for item in (raw.get("related_searches") or [])
        if isinstance(item, dict) and str(item.get("query") or "").strip()
    ][:10]
    return {
        "query": (raw.get("search_parameters") or {}).get("q"),
        "provider_status": metadata.get("status"),
        "provider_created_at": metadata.get("created_at"),
        "organic_results": organic_results,
        "serp_features": [key for key in feature_keys if raw.get(key)],
        "related_questions": related_questions,
        "related_searches": related_searches,
        "result_depth": depth,
    }


def _numeric_interest(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if cleaned.startswith("<"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _trends_payload(raw: dict[str, Any]) -> dict[str, Any]:
    timeline = (raw.get("interest_over_time") or {}).get("timeline_data") or []
    series = []
    for point in timeline:
        if not isinstance(point, dict):
            continue
        values = point.get("values") or []
        first = values[0] if values and isinstance(values[0], dict) else {}
        raw_value = first.get("extracted_value", first.get("value"))
        value = _numeric_interest(raw_value)
        series.append(
            {
                "date": point.get("date"),
                "timestamp": point.get("timestamp"),
                "value": value,
                "raw_value": raw_value if value is None else None,
                "is_low_volume": isinstance(raw_value, str) and raw_value.strip().startswith("<"),
                "is_partial": bool(point.get("isPartial") or point.get("is_partial")),
            }
        )
    numeric = [float(point["value"]) for point in series if point["value"] is not None]
    metadata = raw.get("search_metadata") or {}
    return {
        "query": (raw.get("search_parameters") or {}).get("q"),
        "provider_status": metadata.get("status"),
        "provider_created_at": metadata.get("created_at"),
        "timeframe": (raw.get("search_parameters") or {}).get("date"),
        "geo": (raw.get("search_parameters") or {}).get("geo"),
        "summary": {
            "points": len(series),
            "minimum": min(numeric) if numeric else None,
            "maximum": max(numeric) if numeric else None,
            "average": round(sum(numeric) / len(numeric), 2) if numeric else None,
            "latest": numeric[-1] if numeric else None,
            "start_date": series[0]["date"] if series else None,
            "end_date": series[-1]["date"] if series else None,
            "partial_points": sum(1 for point in series if point["is_partial"]),
            "low_volume_points": sum(1 for point in series if point["is_low_volume"]),
        },
        "series": series,
    }


def _compact_payload(purpose: str, payload: dict[str, Any]) -> dict[str, Any]:
    if purpose == "trends_timeseries":
        return {
            key: payload.get(key)
            for key in ("query", "provider_created_at", "timeframe", "geo", "summary")
        }
    return payload


async def _execute_serpapi_run(
    settings: Settings,
    *,
    site: dict[str, Any],
    opportunity_id: int | None,
    purpose: str,
    parameters: dict[str, Any],
    input_refs: list[str],
    transport: httpx.AsyncBaseTransport | None,
) -> ExternalRunResult:
    request_hash = _request_sha256("serpapi", purpose, parameters)
    reusable = _load_reusable_run(
        settings,
        site_id=int(site["id"]),
        provider="serpapi",
        purpose=purpose,
        request_hash=request_hash,
        reuse_hours=int(EXTERNAL_QUERY_REVIEW_RULE["config"]["reuse_hours"]),
    )
    if reusable:
        return reusable

    run_id = _start_run(
        settings,
        site_id=int(site["id"]),
        opportunity_id=opportunity_id,
        provider="serpapi",
        purpose=purpose,
        request_hash=request_hash,
        input_refs=input_refs,
        parameters=parameters,
    )
    try:
        async with httpx.AsyncClient(timeout=45, transport=transport) as client:
            response = await client.get(
                SERPAPI_ENDPOINT,
                params={**parameters, "api_key": settings.serpapi_api_key},
            )
    except httpx.HTTPError:
        return _record_failure(
            settings,
            run_id,
            error_code="network_error",
            message="SerpAPI 网络连接失败",
        )

    sanitized = _redact_response(response.content, settings.serpapi_api_key or "")
    if len(sanitized) > MAX_EXTERNAL_RESPONSE_BYTES:
        return _record_failure(
            settings,
            run_id,
            error_code="response_too_large",
            message="SerpAPI 响应超过 20MB 安全限制",
            response_received=True,
        )
    snapshot = save_snapshot(
        settings,
        str(site["slug"]),
        f"external-serpapi-{purpose}",
        f"{purpose}.json",
        sanitized,
    )
    snapshot_values = {
        "snapshot_path": str(snapshot.path),
        "response_sha256": snapshot.sha256,
        "response_size": snapshot.size,
    }
    if response.status_code != 200:
        error_code, message = _safe_http_error(response.status_code)
        return _record_failure(
            settings,
            run_id,
            error_code=error_code,
            message=message,
            response_received=True,
            **snapshot_values,
        )
    try:
        raw = json.loads(sanitized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _record_failure(
            settings,
            run_id,
            error_code="invalid_json",
            message="SerpAPI 响应不是可解析 JSON",
            response_received=True,
            **snapshot_values,
        )
    if not isinstance(raw, dict):
        return _record_failure(
            settings,
            run_id,
            error_code="provider_error",
            message="SerpAPI 未返回可用结果",
            response_received=True,
            **snapshot_values,
        )
    if raw.get("error"):
        error_text = str(raw.get("error") or "").casefold()
        no_results = purpose == "trends_timeseries" and any(
            marker in error_text
            for marker in ("no result", "no data", "hasn't returned", "not enough data")
        )
        return _record_failure(
            settings,
            run_id,
            error_code="no_results" if no_results else "provider_error",
            message=(
                "Google Trends 未返回该查询的可用时间序列"
                if no_results
                else "SerpAPI 未返回可用结果"
            ),
            response_received=True,
            **snapshot_values,
        )
    provider_status = str((raw.get("search_metadata") or {}).get("status") or "")
    if provider_status and provider_status.lower() not in {"success", "cached"}:
        return _record_failure(
            settings,
            run_id,
            error_code="provider_status_error",
            message="SerpAPI 搜索未成功完成",
            response_received=True,
            **snapshot_values,
        )

    payload = _serp_payload(raw) if purpose == "serp_snapshot" else _trends_payload(raw)
    evidence_id = f"external:{run_id}:{purpose}"
    captured_at = utc_now()
    limitations = (
        ["时点 SERP 不能证明稳定排名、搜索量或内容质量"]
        if purpose == "serp_snapshot"
        else ["Google Trends 是归一化相对兴趣，不是绝对搜索量；零值不等于无人搜索"]
    )
    usage = {
        "requests": 1,
        "billing_unit": "SerpAPI search request",
        "monetary_cost": None,
    }
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE external_runs
            SET status = 'success', response_snapshot_path = ?, response_sha256 = ?,
                response_size = ?, usage_json = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                str(snapshot.path),
                snapshot.sha256,
                snapshot.size,
                json_dumps(usage),
                captured_at,
                run_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO evidence_items(
                evidence_id, site_id, external_run_id, evidence_type, evidence_level,
                source_ref, payload_json, limitations_json, captured_at
            ) VALUES(?, ?, ?, ?, 'C', ?, ?, ?, ?)
            """,
            (
                evidence_id,
                site["id"],
                run_id,
                purpose,
                (
                    "https://serpapi.com/search-api"
                    if purpose == "serp_snapshot"
                    else "https://serpapi.com/google-trends-api"
                ),
                json_dumps(payload),
                json_dumps(limitations),
                captured_at,
            ),
        )
    return ExternalRunResult(
        purpose,
        "success",
        run_id,
        evidence_id,
        False,
        "已保存不可变响应快照并生成 evidence ID",
        payload,
    )


def _public_http_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EvidenceCollectionUnavailable("Firecrawl 只接受公开 http(s) URL")
    if parsed.username or parsed.password:
        raise EvidenceCollectionUnavailable("Firecrawl URL 不能包含用户名或密码")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise EvidenceCollectionUnavailable("Firecrawl 不采集本机或内网 URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise EvidenceCollectionUnavailable("Firecrawl 不采集本机或内网 URL")
    return cleaned


async def _execute_post_json_run(
    settings: Settings,
    *,
    site: dict[str, Any],
    opportunity_id: int | None,
    provider: str,
    provider_label: str,
    purpose: str,
    endpoint: str,
    parameters: dict[str, Any],
    headers: dict[str, str],
    secret: str,
    input_refs: list[str],
    evidence_level: str,
    source_ref: str | None,
    limitations: list[str],
    billing_unit: str,
    valid_response: Callable[[dict[str, Any]], bool],
    payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
    usage_builder: Callable[[dict[str, Any]], dict[str, Any]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExternalRunResult:
    request_hash = _request_sha256(provider, purpose, parameters)
    reusable = _load_reusable_run(
        settings,
        site_id=int(site["id"]),
        provider=provider,
        purpose=purpose,
        request_hash=request_hash,
    )
    if reusable:
        return reusable

    run_id = _start_run(
        settings,
        site_id=int(site["id"]),
        opportunity_id=opportunity_id,
        provider=provider,
        purpose=purpose,
        request_hash=request_hash,
        input_refs=input_refs,
        parameters=parameters,
    )
    try:
        async with httpx.AsyncClient(timeout=60, transport=transport) as client:
            response = await client.post(endpoint, headers=headers, json=parameters)
    except httpx.HTTPError:
        return _record_failure(
            settings,
            run_id,
            error_code="network_error",
            message=f"{provider_label} 网络连接失败",
        )

    sanitized = _redact_response(response.content, secret)
    if len(sanitized) > MAX_EXTERNAL_RESPONSE_BYTES:
        return _record_failure(
            settings,
            run_id,
            error_code="response_too_large",
            message=f"{provider_label} 响应超过 20MB 安全限制",
            response_received=True,
        )
    snapshot = save_snapshot(
        settings,
        str(site["slug"]),
        f"external-{provider}-{purpose}",
        f"{purpose}.json",
        sanitized,
    )
    snapshot_values = {
        "snapshot_path": str(snapshot.path),
        "response_sha256": snapshot.sha256,
        "response_size": snapshot.size,
    }
    if response.status_code != 200:
        error_code, message = _safe_http_error(response.status_code, provider_label)
        return _record_failure(
            settings,
            run_id,
            error_code=error_code,
            message=message,
            response_received=True,
            **snapshot_values,
        )
    try:
        raw = json.loads(sanitized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _record_failure(
            settings,
            run_id,
            error_code="invalid_json",
            message=f"{provider_label} 响应不是可解析 JSON",
            response_received=True,
            **snapshot_values,
        )
    if not isinstance(raw, dict) or not valid_response(raw):
        return _record_failure(
            settings,
            run_id,
            error_code="provider_error",
            message=f"{provider_label} 未返回可用结果",
            response_received=True,
            **snapshot_values,
        )

    try:
        payload = payload_builder(raw)
        provider_usage = usage_builder(raw)
    except (KeyError, TypeError, ValueError):
        return _record_failure(
            settings,
            run_id,
            error_code="invalid_payload",
            message=f"{provider_label} 响应结构无法使用",
            response_received=True,
            **snapshot_values,
        )
    evidence_id = f"external:{run_id}:{purpose}"
    captured_at = utc_now()
    usage = {
        "requests": 1,
        "billing_unit": billing_unit,
        "monetary_cost": None,
        **provider_usage,
    }
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE external_runs
            SET status = 'success', response_snapshot_path = ?, response_sha256 = ?,
                response_size = ?, usage_json = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                str(snapshot.path),
                snapshot.sha256,
                snapshot.size,
                json_dumps(usage),
                captured_at,
                run_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO evidence_items(
                evidence_id, site_id, external_run_id, evidence_type, evidence_level,
                source_ref, payload_json, limitations_json, captured_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                site["id"],
                run_id,
                purpose,
                evidence_level,
                source_ref,
                json_dumps(payload),
                json_dumps(limitations),
                captured_at,
            ),
        )
    return ExternalRunResult(
        purpose,
        "success",
        run_id,
        evidence_id,
        False,
        "已保存不可变响应快照并生成 evidence ID",
        payload,
    )


def _tavily_payload(raw: dict[str, Any]) -> dict[str, Any]:
    results = []
    for item in raw.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            {
                "title": item.get("title"),
                "url": url,
                "content": str(item.get("content") or "")[:2000],
                "score": item.get("score"),
                "published_date": item.get("published_date"),
            }
        )
        if len(results) >= 10:
            break
    return {
        "query": raw.get("query"),
        "request_id": raw.get("request_id"),
        "response_time": raw.get("response_time"),
        "results": results,
    }


async def execute_tavily_search(
    settings: Settings,
    *,
    site: dict[str, Any],
    opportunity_id: int | None,
    query: str,
    input_refs: list[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExternalRunResult:
    if not settings.tavily_api_key:
        raise EvidenceCollectionUnavailable("请先在设置页配置并测试 Tavily")
    parameters = {
        "query": query.strip()[:400],
        "search_depth": "basic",
        "topic": "general",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
    }
    endpoint = f"{settings.tavily_base_url.rstrip('/')}/search"
    return await _execute_post_json_run(
        settings,
        site=site,
        opportunity_id=opportunity_id,
        provider="tavily",
        provider_label="Tavily",
        purpose="source_discovery",
        endpoint=endpoint,
        parameters=parameters,
        headers={
            "Authorization": f"Bearer {settings.tavily_api_key}",
            "Content-Type": "application/json",
        },
        secret=settings.tavily_api_key,
        input_refs=input_refs,
        evidence_level="E",
        source_ref="https://docs.tavily.com/documentation/api-reference/endpoint/search",
        limitations=[
            "Tavily 只用于发现来源；结果顺序不代表 Google 排名",
            "摘要不是原始来源事实，必须回到来源网页核验",
        ],
        billing_unit="Tavily search request",
        valid_response=lambda raw: isinstance(raw.get("results"), list),
        payload_builder=_tavily_payload,
        usage_builder=lambda raw: {
            "provider_credits": (raw.get("usage") or {}).get("credits")
            if isinstance(raw.get("usage"), dict)
            else None
        },
        transport=transport,
    )


def _firecrawl_payload(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ValueError("missing data")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    markdown = str(data.get("markdown") or "")
    return {
        "url": metadata.get("sourceURL") or metadata.get("url"),
        "title": metadata.get("title"),
        "description": metadata.get("description"),
        "language": metadata.get("language"),
        "status_code": metadata.get("statusCode"),
        "markdown_excerpt": markdown[:12000],
        "markdown_chars": len(markdown),
        "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }


async def execute_firecrawl_scrape(
    settings: Settings,
    *,
    site: dict[str, Any],
    opportunity_id: int | None,
    url: str,
    input_refs: list[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExternalRunResult:
    if not settings.firecrawl_api_key:
        raise EvidenceCollectionUnavailable("请先在设置页配置并测试 Firecrawl")
    public_url = _public_http_url(url)
    parameters = {
        "url": public_url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "maxAge": 172800000,
        "timeout": 30000,
    }
    endpoint = f"{settings.firecrawl_base_url.rstrip('/')}/scrape"
    return await _execute_post_json_run(
        settings,
        site=site,
        opportunity_id=opportunity_id,
        provider="firecrawl",
        provider_label="Firecrawl",
        purpose="page_capture",
        endpoint=endpoint,
        parameters=parameters,
        headers={
            "Authorization": f"Bearer {settings.firecrawl_api_key}",
            "Content-Type": "application/json",
        },
        secret=settings.firecrawl_api_key,
        input_refs=input_refs,
        evidence_level="C",
        source_ref=public_url,
        limitations=[
            "抓取只证明该公开页面在采集时写了什么",
            "网页内容本身不自动证明需求、排名、权威或事实真实性",
        ],
        billing_unit="Firecrawl scrape request",
        valid_response=lambda raw: raw.get("success") is True and isinstance(raw.get("data"), dict),
        payload_builder=_firecrawl_payload,
        usage_builder=lambda raw: {"provider_credits": raw.get("creditsUsed")},
        transport=transport,
    )


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten_text(item)]
    if isinstance(value, list):
        return [text for item in value for text in _flatten_text(item)]
    return []


def _content_search_items(conn, site_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ci.id, ci.content_type, ci.title, ci.slug, ci.canonical_url,
               cs.summary, cs.seo_title, cs.seo_description, cs.metadata_json
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = (
            SELECT cs2.id FROM content_snapshots cs2
            WHERE cs2.content_item_id = ci.id
            ORDER BY cs2.captured_at DESC, cs2.id DESC LIMIT 1
        )
        WHERE ci.site_id = ? AND ci.status = 'active'
        ORDER BY ci.id
        """,
        (site_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        metadata = json_loads(item.pop("metadata_json"), {})
        search_parts = [
            str(item.get("title") or ""),
            str(item.get("slug") or ""),
            str(item.get("summary") or ""),
            str(item.get("seo_title") or ""),
            str(item.get("seo_description") or ""),
            *_flatten_text(metadata),
        ]
        item["search_text"] = " ".join(part for part in search_parts if part)
        items.append(item)
    return items


def _attach_external_evidence(
    settings: Settings,
    opportunity_id: int,
    successful_runs: list[ExternalRunResult],
) -> None:
    if not successful_runs:
        return
    with connection(settings) as conn:
        row = conn.execute(
            "SELECT evidence_json FROM opportunities WHERE id = ?", (opportunity_id,)
        ).fetchone()
        if not row:
            return
        evidence = json_loads(row["evidence_json"], {})
        evidence_ids = list(evidence.get("evidence_ids") or [])
        external = dict(evidence.get("external_evidence") or {})
        for result in successful_runs:
            if result.evidence_id and result.evidence_id not in evidence_ids:
                evidence_ids.append(result.evidence_id)
            external[result.purpose] = {
                "evidence_id": result.evidence_id,
                "external_run_id": result.run_id,
                "reused": result.reused,
                "payload": _compact_payload(result.purpose, result.payload or {}),
            }
        evidence["evidence_ids"] = evidence_ids
        evidence["external_evidence"] = external
        conn.execute(
            "UPDATE opportunities SET evidence_json = ? WHERE id = ?",
            (json_dumps(evidence), opportunity_id),
        )


def _upsert_new_article_candidate(
    settings: Settings,
    opportunity: dict[str, Any],
    site: dict[str, Any],
    serp_run: ExternalRunResult,
    trends_run: ExternalRunResult | None,
) -> int:
    with connection(settings) as conn:
        content_items = _content_search_items(conn, int(site["id"]))
        assessment = assess_new_article_candidate(
            query=str(opportunity["target_ref"]),
            site_domain=str(site["domain"]),
            content_items=content_items,
            serp_payload=serp_run.payload or {},
        )
        evidence_ids = list(opportunity["evidence"].get("evidence_ids") or [])
        for run in (serp_run, trends_run):
            if run and run.evidence_id and run.evidence_id not in evidence_ids:
                evidence_ids.append(run.evidence_id)
        limitations = list(assessment.limitations)
        if not trends_run or trends_run.status != "success":
            limitations.append("本次没有可用 Trends 时间序列，不能判断季节性背景")
        evidence = {
            "evidence_ids": evidence_ids,
            "source_opportunity_id": opportunity["id"],
            "facts": {
                "gsc_query": opportunity["evidence"].get("current", {}),
                "serp": _compact_payload("serp_snapshot", serp_run.payload or {}),
                "trends": (
                    _compact_payload("trends_timeseries", trends_run.payload or {})
                    if trends_run and trends_run.status == "success"
                    else None
                ),
            },
            "program_inference": {"content_overlap": assessment.overlap},
            "missing": assessment.missing,
            "limitations": limitations,
        }
        config = NEW_ARTICLE_CANDIDATE_RULE["config"]
        confidence_weight = float(config["confidence_weight"])
        effort = float(config["effort"])
        strength = float(opportunity["strength"])
        priority = round(strength * confidence_weight / effort, 1)
        blocked = assessment.gate_status == "blocked"
        values = ("新文章候选已拦截：" if blocked else "新文章候选待确认：") + str(
            opportunity["target_ref"]
        )
        existing = conn.execute(
            """
            SELECT id, status FROM opportunities
            WHERE analysis_run_id = ? AND rule_key = 'new_article_candidate'
              AND target_ref = ?
            ORDER BY id DESC LIMIT 1
            """,
            (opportunity["analysis_run_id"], opportunity["target_ref"]),
        ).fetchone()
        if existing and existing["status"] != "proposed":
            return int(existing["id"])
        if existing:
            conn.execute(
                """
                UPDATE opportunities
                SET title = ?, recommended_action = ?, gate_status = ?,
                    gate_reasons_json = ?, evidence_json = ?, strength = ?,
                    confidence = 'low', confidence_weight = ?, effort = ?, priority = ?,
                    method_version = ?
                WHERE id = ?
                """,
                (
                    values,
                    assessment.recommended_action,
                    assessment.gate_status,
                    json_dumps(assessment.gate_reasons),
                    json_dumps(evidence),
                    strength,
                    confidence_weight,
                    effort,
                    priority,
                    METHOD_VERSION,
                    existing["id"],
                ),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """
            INSERT INTO opportunities(
                analysis_run_id, site_id, rule_key, opportunity_type, target_kind,
                target_ref, title, recommended_action, gate_status, gate_reasons_json,
                evidence_json, strength, confidence, confidence_weight, effort,
                priority, method_version, created_at
            ) VALUES(?, ?, 'new_article_candidate', 'create', 'query', ?, ?, ?, ?, ?, ?,
                     ?, 'low', ?, ?, ?, ?, ?)
            """,
            (
                opportunity["analysis_run_id"],
                site["id"],
                opportunity["target_ref"],
                values,
                assessment.recommended_action,
                assessment.gate_status,
                json_dumps(assessment.gate_reasons),
                json_dumps(evidence),
                strength,
                confidence_weight,
                effort,
                priority,
                METHOD_VERSION,
                utc_now(),
            ),
        )
        return int(cursor.lastrowid)


async def collect_query_evidence(
    opportunity_id: int,
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvidenceCollectionOutcome:
    active_settings = settings or get_settings()
    if not active_settings.serpapi_api_key:
        raise EvidenceCollectionUnavailable("请先在设置页配置并测试 SerpAPI")
    with connection(active_settings) as conn:
        opportunity = get_opportunity(conn, opportunity_id)
        if not opportunity:
            raise EvidenceCollectionUnavailable("机会不存在")
        site_row = conn.execute(
            "SELECT * FROM sites WHERE id = ?", (opportunity["site_id"],)
        ).fetchone()
        site = dict(site_row) if site_row else None
    if not site:
        raise EvidenceCollectionUnavailable("站点不存在")
    if opportunity["target_kind"] != "query":
        raise EvidenceCollectionUnavailable(
            "页面候选仍需定向 GSC query→page 数据，不能用页面标题猜测查询"
        )
    if opportunity["rule_key"] != "query_page_evidence_gap":
        raise EvidenceCollectionUnavailable("当前只支持 GSC query→page 补证据候选")
    if opportunity["status"] not in {"proposed", "accepted", "in_progress"}:
        raise EvidenceCollectionUnavailable("该补证据任务已结束或取消，不能继续发起外部调用")
    with connection(active_settings) as conn:
        latest_run = conn.execute(
            """
            SELECT id FROM analysis_runs
            WHERE site_id = ? AND status = 'success'
            ORDER BY started_at DESC, id DESC LIMIT 1
            """,
            (opportunity["site_id"],),
        ).fetchone()
    is_latest = latest_run and int(latest_run["id"]) == int(opportunity["analysis_run_id"])
    if not is_latest:
        with connection(active_settings) as conn:
            accepted_action = conn.execute(
                """
                SELECT 1 FROM actions
                WHERE opportunity_id = ? AND decision = 'accepted'
                  AND workflow_status != 'cancelled'
                LIMIT 1
                """,
                (opportunity["id"],),
            ).fetchone()
        if not accepted_action:
            raise EvidenceCollectionUnavailable("该候选不属于当前分析，请在最新 Top 组合中补证据")
    query = str(opportunity["target_ref"]).strip()
    max_length = int(EXTERNAL_QUERY_REVIEW_RULE["config"]["max_query_length"])
    if not query or len(query) > max_length:
        raise EvidenceCollectionUnavailable(f"查询必须为 1–{max_length} 个字符")
    input_refs = [str(item) for item in opportunity["evidence"].get("evidence_ids", [])]
    serp_parameters: dict[str, Any] = {
        "engine": "google",
        "q": query,
        "gl": active_settings.serpapi_country,
        "hl": active_settings.serpapi_language,
        "device": active_settings.serpapi_device,
        "num": int(EXTERNAL_QUERY_REVIEW_RULE["config"]["organic_result_depth"]),
    }
    if active_settings.serpapi_location:
        serp_parameters["location"] = active_settings.serpapi_location
    serp_run = await _execute_serpapi_run(
        active_settings,
        site=site,
        opportunity_id=int(opportunity["id"]),
        purpose="serp_snapshot",
        parameters=serp_parameters,
        input_refs=input_refs,
        transport=transport,
    )

    runs = [serp_run]
    trends_run: ExternalRunResult | None = None
    if active_settings.trends_provider == "serpapi":
        trends_parameters = {
            "engine": "google_trends",
            "q": query,
            "data_type": "TIMESERIES",
            "date": active_settings.trends_timeframe,
            "geo": active_settings.trends_geo,
            "hl": active_settings.serpapi_language.split("-", 1)[0],
            "tz": _serpapi_timezone_offset(active_settings.timezone),
        }
        trends_run = await _execute_serpapi_run(
            active_settings,
            site=site,
            opportunity_id=int(opportunity["id"]),
            purpose="trends_timeseries",
            parameters=trends_parameters,
            input_refs=input_refs,
            transport=transport,
        )
        runs.append(trends_run)

    successful = [run for run in runs if run.status == "success"]
    _attach_external_evidence(active_settings, opportunity_id, successful)
    new_candidate_id = None
    if serp_run.status == "success":
        new_candidate_id = _upsert_new_article_candidate(
            active_settings, opportunity, site, serp_run, trends_run
        )

    if len(successful) == len(runs):
        status = "success"
        reused_count = sum(1 for run in successful if run.reused)
        suffix = f"；其中 {reused_count} 项复用 24 小时内快照" if reused_count else ""
        message = f"已补齐 {len(successful)} 项外部证据并生成新文章门槛候选{suffix}"
    elif successful:
        status = "partial"
        message = "外部证据部分完成；已保留成功快照，失败项未被当作证据"
    else:
        status = "failed"
        message = "外部证据采集失败；未改变机会分数或资格门槛"
    if active_settings.trends_provider != "serpapi" and serp_run.status == "success":
        status = "partial"
        message = "已保存 SERP 证据；当前 Trends 接入方式需要手动导入"
    return EvidenceCollectionOutcome(status, message, tuple(runs), new_candidate_id)


async def collect_research_query_evidence(
    opportunity_id: int,
    max_calls: int,
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvidenceCollectionOutcome:
    """Collect up to two SerpAPI facts for a latest, explicit GSC query candidate.

    Unlike the single-card endpoint, this entry point is used only inside an
    explicitly confirmed, budgeted research run and may cover non-portfolio query
    candidates from the latest deterministic analysis.
    """

    active_settings = settings or get_settings()
    if not active_settings.serpapi_api_key:
        raise EvidenceCollectionUnavailable("请先在设置页配置并测试 SerpAPI")
    if max_calls < 1:
        raise EvidenceCollectionUnavailable("本轮 SerpAPI 预算为 0")
    with connection(active_settings) as conn:
        opportunity = get_opportunity(conn, opportunity_id)
        if not opportunity:
            raise EvidenceCollectionUnavailable("机会不存在")
        site_row = conn.execute(
            "SELECT * FROM sites WHERE id = ?", (opportunity["site_id"],)
        ).fetchone()
        latest_run = conn.execute(
            """
            SELECT id FROM analysis_runs
            WHERE site_id = ? AND status = 'success'
            ORDER BY started_at DESC, id DESC LIMIT 1
            """,
            (opportunity["site_id"],),
        ).fetchone()
    site = dict(site_row) if site_row else None
    if not site:
        raise EvidenceCollectionUnavailable("站点不存在")
    if not latest_run or int(latest_run["id"]) != int(opportunity["analysis_run_id"]):
        raise EvidenceCollectionUnavailable("主题调研只使用最新一轮分析中的查询候选")
    if (
        opportunity["target_kind"] != "query"
        or opportunity["rule_key"] != "query_page_evidence_gap"
    ):
        raise EvidenceCollectionUnavailable("主题调研只接受明确的 GSC 查询候选")
    if opportunity["status"] not in {"proposed", "accepted", "in_progress"}:
        raise EvidenceCollectionUnavailable("该查询候选已结束，不能进入本轮调研")

    query = str(opportunity["target_ref"]).strip()
    max_length = int(EXTERNAL_QUERY_REVIEW_RULE["config"]["max_query_length"])
    if not query or len(query) > max_length:
        raise EvidenceCollectionUnavailable(f"查询必须为 1–{max_length} 个字符")
    input_refs = [str(item) for item in opportunity["evidence"].get("evidence_ids", [])]
    serp_parameters: dict[str, Any] = {
        "engine": "google",
        "q": query,
        "gl": active_settings.serpapi_country,
        "hl": active_settings.serpapi_language,
        "device": active_settings.serpapi_device,
        "num": int(EXTERNAL_QUERY_REVIEW_RULE["config"]["organic_result_depth"]),
    }
    if active_settings.serpapi_location:
        serp_parameters["location"] = active_settings.serpapi_location
    serp_run = await _execute_serpapi_run(
        active_settings,
        site=site,
        opportunity_id=int(opportunity["id"]),
        purpose="serp_snapshot",
        parameters=serp_parameters,
        input_refs=input_refs,
        transport=transport,
    )
    runs = [serp_run]
    trends_run: ExternalRunResult | None = None
    if max_calls >= 2 and active_settings.trends_provider == "serpapi":
        trends_parameters = {
            "engine": "google_trends",
            "q": query,
            "data_type": "TIMESERIES",
            "date": active_settings.trends_timeframe,
            "geo": active_settings.trends_geo,
            "hl": active_settings.serpapi_language.split("-", 1)[0],
            "tz": _serpapi_timezone_offset(active_settings.timezone),
        }
        trends_run = await _execute_serpapi_run(
            active_settings,
            site=site,
            opportunity_id=int(opportunity["id"]),
            purpose="trends_timeseries",
            parameters=trends_parameters,
            input_refs=input_refs,
            transport=transport,
        )
        runs.append(trends_run)

    successful = [run for run in runs if run.status == "success"]
    _attach_external_evidence(active_settings, opportunity_id, successful)
    new_candidate_id = None
    if serp_run.status == "success":
        new_candidate_id = _upsert_new_article_candidate(
            active_settings, opportunity, site, serp_run, trends_run
        )
    if len(successful) == len(runs):
        status = "success"
        message = f"查询“{query}”已补 {len(successful)} 项外部证据"
    elif successful:
        status = "partial"
        message = f"查询“{query}”部分完成；失败项未被当作证据"
    else:
        status = "failed"
        message = f"查询“{query}”采集失败；未改变原机会分数"
    return EvidenceCollectionOutcome(status, message, tuple(runs), new_candidate_id)


async def collect_topic_query_evidence(
    site_id: int,
    query: str,
    max_calls: int,
    settings: Settings | None = None,
    *,
    input_refs: list[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvidenceCollectionOutcome:
    """Collect SERP facts for a graph or boundary seed without inventing a GSC link."""

    active_settings = settings or get_settings()
    if not active_settings.serpapi_api_key:
        raise EvidenceCollectionUnavailable("请先在设置页配置并测试 SerpAPI")
    if max_calls < 1:
        raise EvidenceCollectionUnavailable("本轮 SerpAPI 预算为 0")
    cleaned_query = " ".join(query.split()).strip()
    max_length = int(EXTERNAL_QUERY_REVIEW_RULE["config"]["max_query_length"])
    if not cleaned_query or len(cleaned_query) > max_length:
        raise EvidenceCollectionUnavailable(f"查询必须为 1–{max_length} 个字符")
    with connection(active_settings) as conn:
        site_row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not site_row:
        raise EvidenceCollectionUnavailable("站点不存在")
    site = dict(site_row)
    refs = list(input_refs or [])
    serp_parameters: dict[str, Any] = {
        "engine": "google",
        "q": cleaned_query,
        "gl": active_settings.serpapi_country,
        "hl": active_settings.serpapi_language,
        "device": active_settings.serpapi_device,
        "num": int(EXTERNAL_QUERY_REVIEW_RULE["config"]["organic_result_depth"]),
    }
    if active_settings.serpapi_location:
        serp_parameters["location"] = active_settings.serpapi_location
    serp_run = await _execute_serpapi_run(
        active_settings,
        site=site,
        opportunity_id=None,
        purpose="serp_snapshot",
        parameters=serp_parameters,
        input_refs=refs,
        transport=transport,
    )
    runs = [serp_run]
    if max_calls >= 2 and active_settings.trends_provider == "serpapi":
        trends_parameters = {
            "engine": "google_trends",
            "q": cleaned_query,
            "data_type": "TIMESERIES",
            "date": active_settings.trends_timeframe,
            "geo": active_settings.trends_geo,
            "hl": active_settings.serpapi_language.split("-", 1)[0],
            "tz": _serpapi_timezone_offset(active_settings.timezone),
        }
        runs.append(
            await _execute_serpapi_run(
                active_settings,
                site=site,
                opportunity_id=None,
                purpose="trends_timeseries",
                parameters=trends_parameters,
                input_refs=refs,
                transport=transport,
            )
        )
    successful = [run for run in runs if run.status == "success"]
    if len(successful) == len(runs):
        status = "success"
        message = f"主题查询“{cleaned_query}”已保存 {len(successful)} 项外部证据"
    elif successful:
        status = "partial"
        message = f"主题查询“{cleaned_query}”部分完成"
    else:
        status = "failed"
        message = f"主题查询“{cleaned_query}”没有取得可用外部证据"
    return EvidenceCollectionOutcome(status, message, tuple(runs), None)
