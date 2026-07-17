from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import list_opportunities
from seo_ops.services.gsc_oauth import (
    GSC_READONLY_SCOPE,
    GSCError,
    OAuthStateStore,
    _choose_property,
    build_authorization_url,
    complete_authorization,
    gsc_connection_status,
    sync_gsc,
)
from seo_ops.utils import utc_now
from tests.helpers import blog_export_bytes


def _oauth_settings(settings):
    client_file = settings.project_root / ".secrets" / "google" / "gsc-client.json"
    token_file = settings.project_root / ".secrets" / "google" / "gsc-token.json"
    client_file.parent.mkdir(parents=True)
    client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )
    os.chmod(client_file.parent, 0o700)
    os.chmod(client_file, 0o600)
    return replace(
        settings,
        gsc_oauth_client_file=client_file,
        gsc_oauth_token_file=token_file,
        gsc_redirect_uri="http://127.0.0.1:8787",
    )


def _write_valid_token(configured) -> None:
    client_id_hash = hashlib.sha256(b"test-client.apps.googleusercontent.com").hexdigest()
    configured.gsc_oauth_token_file.write_text(
        json.dumps(
            {
                "access_token": "local-access-token",
                "refresh_token": "local-refresh-token",
                "token_type": "Bearer",
                "scope": GSC_READONLY_SCOPE,
                "expires_at": time.time() + 3600,
                "client_id_sha256": client_id_hash,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(configured.gsc_oauth_token_file, 0o600)


def test_property_selection_prefers_whole_site_and_rejects_page_only_access():
    root_property = {
        "siteUrl": "https://laserpointerhub.com/",
        "permissionLevel": "siteOwner",
    }
    page_property = {
        "siteUrl": "https://laserpointerhub.com/p-B017.html/",
        "permissionLevel": "siteOwner",
    }

    property_uri, permission = _choose_property(
        [root_property, page_property], "laserpointerhub.com"
    )

    assert property_uri == "https://laserpointerhub.com/"
    assert permission == "siteOwner"
    with pytest.raises(GSCError, match="子路径或单页属性"):
        _choose_property([page_property], "laserpointerhub.com")


def test_sync_rejects_a_stored_single_page_property_before_any_request(settings):
    configured = _oauth_settings(settings)
    _write_valid_token(configured)
    with connection(configured) as conn:
        conn.execute(
            """
            INSERT INTO gsc_connections(
                site_id, property_uri, permission_level, trusted_start_date,
                trusted_start_reason, connected_at, updated_at
            ) VALUES(1, ?, 'siteOwner', ?, 'operator boundary', ?, ?)
            """,
            (
                "https://laserpointerhub.com/p-B017.html/",
                configured.gsc_trusted_start_date,
                utc_now(),
                utc_now(),
            ),
        )

    with pytest.raises(GSCError, match="子路径或单页") as error:
        asyncio.run(sync_gsc(1, configured))

    assert error.value.code == "property_scope_invalid"


def test_oauth_uses_readonly_scope_and_keeps_tokens_out_of_sqlite(settings):
    configured = _oauth_settings(settings)
    state_store = OAuthStateStore()
    authorization_url = build_authorization_url(1, configured, state_store)
    parameters = parse_qs(urlsplit(authorization_url).query)
    state = parameters["state"][0]

    assert parameters["scope"] == [GSC_READONLY_SCOPE]
    assert parameters["include_granted_scopes"] == ["false"]
    assert parameters["code_challenge_method"] == ["S256"]
    assert "test-client-secret" not in authorization_url

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "oauth-access-token",
                    "refresh_token": "oauth-refresh-token",
                    "token_type": "Bearer",
                    "scope": GSC_READONLY_SCOPE,
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/webmasters/v3/sites"):
            return httpx.Response(
                200,
                json={
                    "siteEntry": [
                        {
                            "siteUrl": "sc-domain:laserpointerhub.com",
                            "permissionLevel": "siteFullUser",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    outcome = asyncio.run(
        complete_authorization(
            "one-time-code",
            state,
            configured,
            state_store,
            transport=httpx.MockTransport(handler),
        )
    )

    assert outcome.property_uri == "sc-domain:laserpointerhub.com"
    assert stat.S_IMODE(configured.gsc_oauth_token_file.stat().st_mode) == 0o600
    token_document = configured.gsc_oauth_token_file.read_text(encoding="utf-8")
    assert "oauth-refresh-token" in token_document
    with connection(configured) as conn:
        connection_row = conn.execute("SELECT * FROM gsc_connections WHERE site_id = 1").fetchone()
        database_dump = "\n".join(conn.iterdump())
    assert connection_row["permission_level"] == "siteFullUser"
    assert "oauth-access-token" not in database_dump
    assert "oauth-refresh-token" not in database_dump
    assert gsc_connection_status(1, configured).connected is True


def test_one_click_sync_never_requests_before_trusted_date_and_stores_joint_rows(
    settings,
):
    configured = _oauth_settings(settings)
    _write_valid_token(configured)
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), configured)
    trusted_start = datetime.fromisoformat(configured.gsc_trusted_start_date).date()
    final_end = datetime.now(UTC).date() - timedelta(days=2)
    page = "https://laserpointerhub.com/blog/example"
    captured_bodies: list[dict] = []

    with connection(configured) as conn:
        conn.execute(
            """
            INSERT INTO gsc_connections(
                site_id, property_uri, permission_level, trusted_start_date,
                trusted_start_reason, connected_at, updated_at
            ) VALUES(1, 'sc-domain:laserpointerhub.com', 'siteFullUser', ?, ?, ?, ?)
            """,
            (
                trusted_start.isoformat(),
                "Operator excluded earlier erroneous data",
                utc_now(),
                utc_now(),
            ),
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer local-access-token"
        body = json.loads(request.content)
        captured_bodies.append(body)
        dimensions = body["dimensions"]
        if dimensions == ["date"]:
            rows = [
                {"keys": [trusted_start.isoformat()], "clicks": 1, "impressions": 10},
                {"keys": [final_end.isoformat()], "clicks": 2, "impressions": 20},
            ]
        elif dimensions == ["page"]:
            current = body["endDate"] == final_end.isoformat()
            rows = [
                {
                    "keys": [page],
                    "clicks": 2 if current else 10,
                    "impressions": 100,
                    "ctr": 0.02 if current else 0.1,
                    "position": 12 if current else 8,
                }
            ]
        elif dimensions == ["query"]:
            current = body["endDate"] == final_end.isoformat()
            rows = [
                {
                    "keys": ["best example laser"],
                    "clicks": 2 if current else 10,
                    "impressions": 100,
                    "ctr": 0.02 if current else 0.1,
                    "position": 12 if current else 8,
                }
            ]
        else:
            assert dimensions == ["date", "query", "page"]
            rows = [
                {
                    "keys": [body["endDate"], "best example laser", page],
                    "clicks": 2,
                    "impressions": 50,
                    "ctr": 0.04,
                    "position": 12,
                }
            ]
        return httpx.Response(200, json={"rows": rows})

    outcome = asyncio.run(sync_gsc(1, configured, transport=httpx.MockTransport(handler)))

    assert outcome.query_page_row_count > 0
    assert all(body["startDate"] >= trusted_start.isoformat() for body in captured_bodies)
    assert all(body["dataState"] == "final" for body in captured_bodies)
    assert any(body["dimensions"] == ["date", "query", "page"] for body in captured_bodies)
    with connection(configured) as conn:
        imported = conn.execute(
            "SELECT * FROM imports WHERE id = ?", (outcome.import_id,)
        ).fetchone()
        joint_count = conn.execute(
            "SELECT COUNT(*) FROM gsc_query_page_metrics WHERE import_id = ?",
            (outcome.import_id,),
        ).fetchone()[0]
    assert joint_count == outcome.query_page_row_count
    snapshot_text = (configured.project_root / imported["snapshot_path"]).read_text(
        encoding="utf-8"
    )
    assert "local-access-token" not in snapshot_text
    assert "Authorization" not in snapshot_text

    analysis = run_analysis(1, configured)
    assert analysis.status == "success"
    with connection(configured) as conn:
        opportunities = list_opportunities(conn, 1)
    executable_old = [
        item
        for item in opportunities
        if item["target_kind"] == "blog" and item["gate_status"] == "passed"
    ]
    assert executable_old
    assert executable_old[0]["evidence"]["query_page"]["current"]
