from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.ingest import ImportOutcome, import_gsc_api_data
from seo_ops.repositories import get_site
from seo_ops.utils import json_dumps, utc_now

GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
GSC_ROW_LIMIT = 25_000
GSC_MAX_PAGES = 100
OAUTH_STATE_TTL_SECONDS = 10 * 60


class GSCError(ValueError):
    """A credential-safe error suitable for the local operator UI."""

    def __init__(self, message: str, code: str = "gsc_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OAuthClientConfig:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    auth_uri: str
    token_uri: str


@dataclass(frozen=True, slots=True)
class OAuthState:
    site_id: int
    redirect_uri: str
    code_verifier: str = field(repr=False)
    created_at: float


class OAuthStateStore:
    """Keep short-lived OAuth state in memory; never persist it to SQLite."""

    def __init__(self) -> None:
        self._states: dict[str, OAuthState] = {}

    def create(self, site_id: int, redirect_uri: str) -> tuple[str, str]:
        self._remove_expired()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        self._states[state] = OAuthState(
            site_id=site_id,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
            created_at=time.monotonic(),
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        return state, challenge

    def consume(self, state: str) -> OAuthState:
        item = self._states.pop(state, None)
        if not item or time.monotonic() - item.created_at > OAUTH_STATE_TTL_SECONDS:
            raise GSCError("Google 授权请求已过期，请重新点击连接", "oauth_state_invalid")
        return item

    def discard(self, state: str | None) -> None:
        if state:
            self._states.pop(state, None)

    def _remove_expired(self) -> None:
        now = time.monotonic()
        self._states = {
            key: value
            for key, value in self._states.items()
            if now - value.created_at <= OAUTH_STATE_TTL_SECONDS
        }


@dataclass(frozen=True, slots=True)
class GSCConnectionStatus:
    client_configured: bool
    client_file_secure: bool
    token_present: bool
    token_file_secure: bool
    connected: bool
    property_uri: str | None
    permission_level: str | None
    trusted_start_date: str
    last_sync_at: str | None
    last_error_message: str | None
    latest_sync_status: str | None
    latest_query_page_rows: int
    configuration_error: str | None = None


@dataclass(frozen=True, slots=True)
class GSCAuthOutcome:
    site_id: int
    property_uri: str
    permission_level: str
    message: str


@dataclass(frozen=True, slots=True)
class GSCSyncOutcome:
    sync_run_id: int
    import_id: int
    duplicate: bool
    aggregate_row_count: int
    query_page_row_count: int
    actual_start_date: str
    actual_end_date: str
    request_count: int
    message: str


def _is_private_file(path: Path | None) -> bool:
    if not path or not path.is_file():
        return False
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def _load_client_config(settings: Settings) -> OAuthClientConfig:
    path = settings.gsc_oauth_client_file
    if not path or not path.is_file():
        raise GSCError("尚未配置 GSC OAuth 客户端文件", "client_file_missing")
    if not _is_private_file(path):
        raise GSCError("GSC OAuth 客户端文件权限必须为 0600", "client_file_insecure")
    if path.stat().st_size > 100_000:
        raise GSCError("GSC OAuth 客户端文件异常", "client_file_invalid")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        client = document["installed"]
        client_id = str(client["client_id"])
        client_secret = str(client["client_secret"])
        auth_uri = str(client["auth_uri"])
        token_uri = str(client["token_uri"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GSCError("GSC OAuth 客户端 JSON 无效", "client_file_invalid") from exc
    auth = urlsplit(auth_uri)
    token = urlsplit(token_uri)
    if (
        not client_id
        or not client_secret
        or auth.scheme != "https"
        or auth.hostname != "accounts.google.com"
        or token.scheme != "https"
        or token.hostname != "oauth2.googleapis.com"
    ):
        raise GSCError("GSC OAuth 客户端端点不是 Google 官方地址", "client_file_invalid")
    return OAuthClientConfig(
        client_id=client_id,
        client_secret=client_secret,
        auth_uri=auth_uri,
        token_uri=token_uri,
    )


def _redirect_uri(settings: Settings) -> str:
    value = settings.gsc_redirect_uri
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise GSCError("GSC OAuth 回调必须是本机根地址", "redirect_uri_invalid")
    return value.rstrip("/")


def _client_id_hash(config: OAuthClientConfig) -> str:
    return hashlib.sha256(config.client_id.encode("utf-8")).hexdigest()


def _readonly_scope(value: Any) -> str:
    scopes = {item for item in str(value or GSC_READONLY_SCOPE).split() if item}
    if scopes != {GSC_READONLY_SCOPE}:
        raise GSCError("Google OAuth 授权不是限定的 GSC 只读权限", "scope_invalid")
    return GSC_READONLY_SCOPE


def _token_path(settings: Settings) -> Path:
    path = settings.gsc_oauth_token_file
    if not path:
        raise GSCError("尚未配置 GSC OAuth token 文件位置", "token_file_missing")
    return path


def _write_token(settings: Settings, payload: dict[str, Any]) -> None:
    path = _token_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json_dumps(payload) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _read_token(settings: Settings, config: OAuthClientConfig) -> dict[str, Any]:
    path = _token_path(settings)
    if not path.is_file():
        raise GSCError("尚未完成 Google 授权", "token_file_missing")
    if not _is_private_file(path):
        raise GSCError("GSC OAuth token 文件权限必须为 0600", "token_file_insecure")
    if path.stat().st_size > 100_000:
        raise GSCError("GSC OAuth token 文件异常", "token_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise GSCError("GSC OAuth token 文件无效", "token_file_invalid") from exc
    if payload.get("client_id_sha256") != _client_id_hash(config):
        raise GSCError("OAuth 客户端已变化，请重新连接 Google", "token_client_mismatch")
    _readonly_scope(payload.get("scope"))
    return payload


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise GSCError("Google 返回了无法解析的响应", "response_invalid") from exc
    if not isinstance(payload, dict):
        raise GSCError("Google 返回的数据结构无效", "response_invalid")
    return payload


def _http_error(response: httpx.Response, *, authorization: bool = False) -> GSCError:
    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass
    code = str(payload.get("error") or "")
    nested = payload.get("error")
    if isinstance(nested, dict):
        code = str(nested.get("status") or nested.get("code") or "")
    if response.status_code in {401, 403}:
        return GSCError("Google 授权已失效或没有该站点权限，请重新连接", "authorization_failed")
    if response.status_code == 429:
        return GSCError("GSC API 调用频率受限，请稍后重试", "rate_limited")
    if authorization and code == "invalid_grant":
        return GSCError("Google 授权码已失效，请重新连接", "invalid_grant")
    return GSCError(f"Google GSC API 返回 HTTP {response.status_code}", "google_http_error")


def build_authorization_url(
    site_id: int,
    settings: Settings,
    state_store: OAuthStateStore,
) -> str:
    config = _load_client_config(settings)
    redirect_uri = _redirect_uri(settings)
    state, challenge = state_store.create(site_id, redirect_uri)
    parameters = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GSC_READONLY_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{config.auth_uri}?{urlencode(parameters)}"


def _property_domain(property_uri: str) -> str:
    if property_uri.startswith("sc-domain:"):
        return property_uri.removeprefix("sc-domain:").lower().removeprefix("www.")
    return (urlsplit(property_uri).hostname or "").lower().removeprefix("www.")


def _is_whole_site_property(property_uri: str, domain: str) -> bool:
    expected = domain.lower().removeprefix("www.")
    if _property_domain(property_uri) != expected:
        return False
    if property_uri.startswith("sc-domain:"):
        return True
    parsed = urlsplit(property_uri)
    path = parsed.path or "/"
    return (
        parsed.scheme.lower() in {"http", "https"}
        and not path.rstrip("/")
        and not parsed.query
        and not parsed.fragment
    )


def _choose_property(entries: list[dict[str, Any]], domain: str) -> tuple[str, str]:
    expected = domain.lower().removeprefix("www.")
    permission_rank = {
        "siteOwner": 4,
        "siteFullUser": 3,
        "siteRestrictedUser": 2,
        "siteUnverifiedUser": 0,
    }
    candidates: list[tuple[tuple[int, int, int, int], str, str]] = []
    for entry in entries:
        uri = str(entry.get("siteUrl") or "")
        permission = str(entry.get("permissionLevel") or "")
        if (
            not uri
            or not _is_whole_site_property(uri, expected)
            or permission_rank.get(permission, 0) <= 0
        ):
            continue

        if uri.startswith("sc-domain:"):
            coverage_rank = 2
            scheme_rank = 0
            exact_host_rank = int(uri.removeprefix("sc-domain:").lower() == expected)
        else:
            parsed = urlsplit(uri)
            coverage_rank = 1
            scheme_rank = int(parsed.scheme.lower() == "https")
            exact_host_rank = int((parsed.hostname or "").lower() == expected)

        score = (
            coverage_rank,
            scheme_rank,
            exact_host_rank,
            permission_rank[permission],
        )
        candidates.append((score, uri, permission))
    if not candidates:
        raise GSCError(
            f"Google 账户中没有可读取的 {expected} 整站 Search Console 属性；"
            "子路径或单页属性不会用于全站分析",
            "property_not_found",
        )
    _, uri, permission = max(candidates, key=lambda candidate: candidate[0])
    return uri, permission


async def complete_authorization(
    code: str,
    state: str,
    settings: Settings,
    state_store: OAuthStateStore,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GSCAuthOutcome:
    oauth_state = state_store.consume(state)
    config = _load_client_config(settings)
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            response = await client.post(
                config.token_uri,
                data={
                    "code": code,
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "redirect_uri": oauth_state.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": oauth_state.code_verifier,
                },
            )
            if response.status_code != 200:
                raise _http_error(response, authorization=True)
            token_response = _safe_json(response)
            access_token = str(token_response.get("access_token") or "")
            refresh_token = str(token_response.get("refresh_token") or "")
            if not access_token or not refresh_token:
                raise GSCError(
                    "Google 未返回长期只读授权，请重新连接并同意访问",
                    "refresh_token_missing",
                )
            granted_scope = _readonly_scope(token_response.get("scope"))
            sites_response = await client.get(
                GSC_SITES_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if sites_response.status_code != 200:
                raise _http_error(sites_response)
            site_payload = _safe_json(sites_response)
    except httpx.HTTPError as exc:
        raise GSCError("无法连接 Google OAuth/GSC 服务", "network_error") from exc

    with connection(settings) as conn:
        site = get_site(conn, oauth_state.site_id)
    if not site:
        raise GSCError("站点不存在", "site_missing")
    entries = site_payload.get("siteEntry") or []
    if not isinstance(entries, list):
        raise GSCError("Google 站点列表结构无效", "response_invalid")
    property_uri, permission = _choose_property(entries, str(site["domain"]))
    now_epoch = time.time()
    expires_in = int(token_response.get("expires_in") or 3600)
    stored_token = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(token_response.get("token_type") or "Bearer"),
        "scope": granted_scope,
        "expires_at": now_epoch + max(60, expires_in),
        "client_id_sha256": _client_id_hash(config),
    }
    _write_token(settings, stored_token)

    now = utc_now()
    trusted_reason = (
        f"运营者确认 {settings.gsc_trusted_start_date} 以前的 GSC 数据不可信；"
        "OAuth API 同步不得请求更早日期"
    )
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO gsc_connections(
                site_id, property_uri, permission_level, trusted_start_date,
                trusted_start_reason, connected_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                property_uri = excluded.property_uri,
                permission_level = excluded.permission_level,
                trusted_start_date = gsc_connections.trusted_start_date,
                trusted_start_reason = gsc_connections.trusted_start_reason,
                updated_at = excluded.updated_at,
                last_error_code = NULL,
                last_error_message = NULL
            """,
            (
                oauth_state.site_id,
                property_uri,
                permission,
                settings.gsc_trusted_start_date,
                trusted_reason,
                now,
                now,
            ),
        )
    return GSCAuthOutcome(
        site_id=oauth_state.site_id,
        property_uri=property_uri,
        permission_level=permission,
        message="Google Search Console 已完成只读授权",
    )


async def _access_token(
    settings: Settings,
    config: OAuthClientConfig,
    client: httpx.AsyncClient,
) -> str:
    token = _read_token(settings, config)
    access_token = str(token.get("access_token") or "")
    expires_at = float(token.get("expires_at") or 0)
    if access_token and expires_at > time.time() + 60:
        return access_token
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise GSCError("Google 授权已失效，请重新连接", "refresh_token_missing")
    response = await client.post(
        config.token_uri,
        data={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if response.status_code != 200:
        raise _http_error(response, authorization=True)
    refreshed = _safe_json(response)
    access_token = str(refreshed.get("access_token") or "")
    if not access_token:
        raise GSCError("Google token 刷新响应无效", "response_invalid")
    token.update(
        {
            "access_token": access_token,
            "token_type": str(refreshed.get("token_type") or token.get("token_type") or "Bearer"),
            "scope": _readonly_scope(refreshed.get("scope") or token.get("scope")),
            "expires_at": time.time() + max(60, int(refreshed.get("expires_in") or 3600)),
        }
    )
    _write_token(settings, token)
    return access_token


def _search_analytics_url(property_uri: str) -> str:
    encoded = quote(property_uri, safe="")
    return f"https://www.googleapis.com/webmasters/v3/sites/{encoded}/searchAnalytics/query"


async def _fetch_rows(
    client: httpx.AsyncClient,
    access_token: str,
    property_uri: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    captures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start_row = 0
    for _ in range(GSC_MAX_PAGES):
        request_body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "type": "web",
            "dataState": "final",
            "rowLimit": GSC_ROW_LIMIT,
            "startRow": start_row,
        }
        response = await client.post(
            _search_analytics_url(property_uri),
            headers={"Authorization": f"Bearer {access_token}"},
            json=request_body,
        )
        if response.status_code != 200:
            raise _http_error(response)
        payload = _safe_json(response)
        page_rows = payload.get("rows") or []
        if not isinstance(page_rows, list) or any(not isinstance(row, dict) for row in page_rows):
            raise GSCError("GSC Search Analytics 行结构无效", "response_invalid")
        clean_response = {
            "rows": page_rows,
            "responseAggregationType": payload.get("responseAggregationType"),
            "metadata": payload.get("metadata"),
        }
        captures.append({"request": request_body, "response": clean_response})
        rows.extend(page_rows)
        if len(page_rows) < GSC_ROW_LIMIT:
            return sorted(
                rows, key=lambda row: tuple(str(value) for value in row.get("keys") or [])
            )
        start_row += GSC_ROW_LIMIT
    raise GSCError("GSC API 分页超过安全上限，未激活本批次", "pagination_limit")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise GSCError("GSC 指标包含非有限数值", "response_invalid")
    return result


def _normalize_aggregate(
    dimension: str,
    rows: list[dict[str, Any]],
    *,
    period: str = "current",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        keys = row.get("keys")
        if not isinstance(keys, list) or len(keys) != 1:
            raise GSCError(f"GSC {dimension} 维度键结构无效", "response_invalid")
        value = str(keys[0]).strip()
        if not value or value in seen:
            raise GSCError(f"GSC {dimension} 维度存在空值或重复值", "response_invalid")
        seen.add(value)
        normalized.append(
            {
                "dimension": dimension,
                "value": value,
                "period": period,
                "clicks": _number(row.get("clicks")),
                "impressions": _number(row.get("impressions")),
                "ctr": _number(row.get("ctr")),
                "position": _number(row.get("position")),
            }
        )
    return normalized


def _normalize_query_page(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        keys = row.get("keys")
        if not isinstance(keys, list) or len(keys) != 3:
            raise GSCError("GSC query + page 联合维度键结构无效", "response_invalid")
        data_date, query_text, page = (str(value).strip() for value in keys)
        key = (data_date, query_text, page)
        if not all(key) or key in seen:
            raise GSCError("GSC query + page 行存在空值或重复值", "response_invalid")
        try:
            date.fromisoformat(data_date)
        except ValueError as exc:
            raise GSCError("GSC query + page 日期无效", "response_invalid") from exc
        seen.add(key)
        normalized.append(
            {
                "date": data_date,
                "query": query_text,
                "page": page,
                "clicks": _number(row.get("clicks")),
                "impressions": _number(row.get("impressions")),
                "ctr": _number(row.get("ctr")),
                "position": _number(row.get("position")),
            }
        )
    return normalized


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    current = start
    while current <= end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        window_end = min(end, date.fromordinal(next_month.toordinal() - 1))
        windows.append((current, window_end))
        current = next_month
    return windows


def _record_sync_failure(
    settings: Settings,
    sync_run_id: int,
    site_id: int,
    error: GSCError,
    request_count: int,
) -> None:
    now = utc_now()
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE gsc_sync_runs
            SET status = 'failed', request_count = ?, error_code = ?,
                error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (request_count, error.code, str(error), now, sync_run_id),
        )
        conn.execute(
            """
            UPDATE gsc_connections
            SET last_error_code = ?, last_error_message = ?, updated_at = ?
            WHERE site_id = ?
            """,
            (error.code, str(error), now, site_id),
        )


async def sync_gsc(
    site_id: int,
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GSCSyncOutcome:
    active = settings or get_settings()
    config = _load_client_config(active)
    with connection(active) as conn:
        row = conn.execute("SELECT * FROM gsc_connections WHERE site_id = ?", (site_id,)).fetchone()
        site = get_site(conn, site_id)
    if not row:
        raise GSCError("请先连接 Google Search Console", "not_connected")
    if not site:
        raise GSCError("站点不存在", "site_missing")
    property_uri = str(row["property_uri"])
    if not _is_whole_site_property(property_uri, str(site["domain"])):
        raise GSCError(
            "当前连接的是子路径或单页 Search Console 属性，请重新授权整站属性",
            "property_scope_invalid",
        )
    try:
        trusted_start = date.fromisoformat(str(row["trusted_start_date"]))
    except ValueError as exc:
        raise GSCError("GSC 可信起始日无效", "trusted_start_invalid") from exc
    requested_end = datetime.now(UTC).date()
    if requested_end < trusted_start:
        raise GSCError("GSC 可信起始日晚于当前日期", "trusted_start_invalid")

    started_at = utc_now()
    with connection(active) as conn:
        cursor = conn.execute(
            """
            INSERT INTO gsc_sync_runs(
                site_id, property_uri, requested_start_date, requested_end_date,
                status, started_at
            ) VALUES(?, ?, ?, ?, 'running', ?)
            """,
            (
                site_id,
                property_uri,
                trusted_start.isoformat(),
                requested_end.isoformat(),
                started_at,
            ),
        )
        sync_run_id = int(cursor.lastrowid)

    captures: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60, transport=transport) as client:
            access_token = await _access_token(active, config, client)
            date_rows = await _fetch_rows(
                client,
                access_token,
                property_uri,
                trusted_start.isoformat(),
                requested_end.isoformat(),
                ["date"],
                captures,
            )
            normalized_dates = _normalize_aggregate("date", date_rows)
            if not normalized_dates:
                raise GSCError("可信日期范围内没有已最终化的 GSC 数据", "no_final_data")
            actual_dates = sorted(date.fromisoformat(row["value"]) for row in normalized_dates)
            actual_start = actual_dates[0]
            actual_end = actual_dates[-1]
            current_end = actual_end
            current_start = max(trusted_start, current_end - timedelta(days=27))
            previous_end = current_start - timedelta(days=1)
            proposed_previous_start = previous_end - timedelta(days=27)
            previous_start = (
                proposed_previous_start if proposed_previous_start >= trusted_start else None
            )
            page_rows = await _fetch_rows(
                client,
                access_token,
                property_uri,
                current_start.isoformat(),
                current_end.isoformat(),
                ["page"],
                captures,
            )
            query_rows = await _fetch_rows(
                client,
                access_token,
                property_uri,
                current_start.isoformat(),
                current_end.isoformat(),
                ["query"],
                captures,
            )
            previous_page_rows: list[dict[str, Any]] = []
            previous_query_rows: list[dict[str, Any]] = []
            if previous_start is not None:
                previous_page_rows = await _fetch_rows(
                    client,
                    access_token,
                    property_uri,
                    previous_start.isoformat(),
                    previous_end.isoformat(),
                    ["page"],
                    captures,
                )
                previous_query_rows = await _fetch_rows(
                    client,
                    access_token,
                    property_uri,
                    previous_start.isoformat(),
                    previous_end.isoformat(),
                    ["query"],
                    captures,
                )
            raw_query_page_rows: list[dict[str, Any]] = []
            joint_start = previous_start or current_start
            for window_start, window_end in _month_windows(joint_start, current_end):
                raw_query_page_rows.extend(
                    await _fetch_rows(
                        client,
                        access_token,
                        property_uri,
                        window_start.isoformat(),
                        window_end.isoformat(),
                        ["date", "query", "page"],
                        captures,
                    )
                )
            aggregate_rows = [
                *normalized_dates,
                *_normalize_aggregate("page", page_rows, period="current"),
                *_normalize_aggregate("query", query_rows, period="current"),
                *_normalize_aggregate("page", previous_page_rows, period="previous"),
                *_normalize_aggregate("query", previous_query_rows, period="previous"),
            ]
            query_page_rows = _normalize_query_page(raw_query_page_rows)
            if any(
                date.fromisoformat(item["date"]) < joint_start
                or date.fromisoformat(item["date"]) > current_end
                for item in query_page_rows
            ):
                raise GSCError(
                    "GSC API 返回了可信范围外的数据，未激活本批次",
                    "date_boundary_violation",
                )
    except GSCError as exc:
        _record_sync_failure(active, sync_run_id, site_id, exc, len(captures))
        raise
    except httpx.HTTPError as exc:
        error = GSCError("无法连接 GSC API，请稍后重试", "network_error")
        _record_sync_failure(active, sync_run_id, site_id, error, len(captures))
        raise error from exc
    except (TypeError, ValueError) as exc:
        error = GSCError("GSC API 响应无法标准化，未激活本批次", "response_invalid")
        _record_sync_failure(active, sync_run_id, site_id, error, len(captures))
        raise error from exc

    raw_payload = {
        "schema_version": 1,
        "source": "google_search_console_search_analytics_api",
        "property_uri": property_uri,
        "trusted_start_date": trusted_start.isoformat(),
        "requested_start_date": trusted_start.isoformat(),
        "requested_end_date": requested_end.isoformat(),
        "actual_start_date": actual_start.isoformat(),
        "actual_end_date": actual_end.isoformat(),
        "comparison_window_days": 28,
        "current_start_date": current_start.isoformat(),
        "current_end_date": current_end.isoformat(),
        "previous_start_date": (previous_start.isoformat() if previous_start is not None else None),
        "previous_end_date": previous_end.isoformat() if previous_start is not None else None,
        "data_state": "final",
        "search_type": "web",
        "responses": captures,
    }
    try:
        outcome: ImportOutcome = import_gsc_api_data(
            site_id,
            f"gsc-api-{actual_start.isoformat()}-to-{actual_end.isoformat()}.json",
            raw_payload,
            aggregate_rows,
            query_page_rows,
            {
                "source": "oauth_api",
                "property_uri": property_uri,
                "trusted_start_date": trusted_start.isoformat(),
                "trusted_start_reason": str(row["trusted_start_reason"]),
                "requested_start_date": trusted_start.isoformat(),
                "requested_end_date": requested_end.isoformat(),
                "actual_start_date": actual_start.isoformat(),
                "actual_end_date": actual_end.isoformat(),
                "comparison_window_days": 28,
                "current_start_date": current_start.isoformat(),
                "current_end_date": current_end.isoformat(),
                "previous_start_date": (
                    previous_start.isoformat() if previous_start is not None else None
                ),
                "previous_end_date": (
                    previous_end.isoformat() if previous_start is not None else None
                ),
                "data_state": "final",
                "search_type": "web",
                "request_count": len(captures),
                "limitations": [
                    "匿名查询不会由 GSC 返回",
                    "Search Analytics API 仍可能只返回顶部数据行",
                ],
            },
            active,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        error = GSCError(
            "GSC API 数据保存失败，旧活动批次保持不变",
            "storage_failed",
        )
        _record_sync_failure(active, sync_run_id, site_id, error, len(captures))
        raise error from exc
    if outcome.status != "success":
        error = GSCError("GSC API 数据保存失败，旧活动批次保持不变", "storage_failed")
        _record_sync_failure(active, sync_run_id, site_id, error, len(captures))
        raise error

    completed_at = utc_now()
    with connection(active) as conn:
        conn.execute(
            """
            UPDATE gsc_sync_runs
            SET import_id = ?, status = 'success', aggregate_row_count = ?,
                query_page_row_count = ?, request_count = ?, reused = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                outcome.import_id,
                len(aggregate_rows),
                len(query_page_rows),
                len(captures),
                int(outcome.duplicate),
                completed_at,
                sync_run_id,
            ),
        )
        conn.execute(
            """
            UPDATE gsc_connections
            SET last_sync_at = ?, last_error_code = NULL,
                last_error_message = NULL, updated_at = ?
            WHERE site_id = ?
            """,
            (completed_at, completed_at, site_id),
        )
    return GSCSyncOutcome(
        sync_run_id=sync_run_id,
        import_id=outcome.import_id,
        duplicate=outcome.duplicate,
        aggregate_row_count=len(aggregate_rows),
        query_page_row_count=len(query_page_rows),
        actual_start_date=actual_start.isoformat(),
        actual_end_date=actual_end.isoformat(),
        request_count=len(captures),
        message=outcome.message,
    )


def gsc_connection_status(
    site_id: int,
    settings: Settings | None = None,
) -> GSCConnectionStatus:
    active = settings or get_settings()
    client_path = active.gsc_oauth_client_file
    token_path = active.gsc_oauth_token_file
    client_configured = bool(client_path and client_path.is_file())
    configuration_error = None
    if client_configured:
        try:
            _load_client_config(active)
        except GSCError as exc:
            configuration_error = str(exc)
    with connection(active) as conn:
        row = conn.execute("SELECT * FROM gsc_connections WHERE site_id = ?", (site_id,)).fetchone()
        latest = conn.execute(
            """
            SELECT status, query_page_row_count
            FROM gsc_sync_runs
            WHERE site_id = ?
            ORDER BY started_at DESC, id DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
    token_present = bool(token_path and token_path.is_file())
    return GSCConnectionStatus(
        client_configured=client_configured,
        client_file_secure=_is_private_file(client_path),
        token_present=token_present,
        token_file_secure=_is_private_file(token_path),
        connected=bool(row and token_present and not configuration_error),
        property_uri=str(row["property_uri"]) if row else None,
        permission_level=str(row["permission_level"]) if row else None,
        trusted_start_date=(
            str(row["trusted_start_date"]) if row else active.gsc_trusted_start_date
        ),
        last_sync_at=str(row["last_sync_at"]) if row and row["last_sync_at"] else None,
        last_error_message=(
            str(row["last_error_message"]) if row and row["last_error_message"] else None
        ),
        latest_sync_status=str(latest["status"]) if latest else None,
        latest_query_page_rows=int(latest["query_page_row_count"]) if latest else 0,
        configuration_error=configuration_error,
    )
