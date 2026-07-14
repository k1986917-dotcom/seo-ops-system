from __future__ import annotations

import sqlite3
from typing import Any

from seo_ops.utils import json_loads


def list_sites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM sites ORDER BY id")]


def get_site(conn: sqlite3.Connection, site_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return dict(row) if row else None


def get_site_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sites WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def list_imports(conn: sqlite3.Connection, site_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM imports
        WHERE site_id = ?
        ORDER BY imported_at DESC, id DESC
        LIMIT ?
        """,
        (site_id, limit),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json_loads(item.pop("metadata_json"), {})
        result.append(item)
    return result


def latest_analysis_run(conn: sqlite3.Connection, site_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM analysis_runs
        WHERE site_id = ? AND status = 'success'
        ORDER BY started_at DESC, id DESC LIMIT 1
        """,
        (site_id,),
    ).fetchone()
    return dict(row) if row else None


def list_opportunities(
    conn: sqlite3.Connection, site_id: int, only_latest: bool = True
) -> list[dict[str, Any]]:
    run = latest_analysis_run(conn, site_id) if only_latest else None
    if only_latest and not run:
        return []
    params: tuple[Any, ...]
    where: str
    if run:
        where = "WHERE o.analysis_run_id = ?"
        params = (run["id"],)
    else:
        where = "WHERE o.site_id = ?"
        params = (site_id,)
    rows = conn.execute(
        f"""
        SELECT o.* FROM opportunities o
        {where}
        ORDER BY
            CASE WHEN portfolio_slot IS NOT NULL THEN 0 ELSE 1 END,
            CASE portfolio_slot WHEN '防损' THEN 1 WHEN '最高价值' THEN 2 WHEN '补证据' THEN 3 ELSE 4 END,
            priority DESC,
            id
        """,
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json_loads(item.pop("evidence_json"), {})
        item["gate_reasons"] = json_loads(item.pop("gate_reasons_json"), [])
        item["ai_explanation"] = json_loads(item.pop("ai_explanation_json"), None)
        result.append(item)
    return result


def get_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["evidence"] = json_loads(item.pop("evidence_json"), {})
    item["gate_reasons"] = json_loads(item.pop("gate_reasons_json"), [])
    item["ai_explanation"] = json_loads(item.pop("ai_explanation_json"), None)
    return item


def dashboard_summary(conn: sqlite3.Connection, site_id: int) -> dict[str, Any]:
    counts = {
        row["content_type"]: row["count"]
        for row in conn.execute(
            "SELECT content_type, COUNT(*) AS count FROM content_items WHERE site_id = ? GROUP BY content_type",
            (site_id,),
        )
    }
    source_rows = conn.execute(
        """
        SELECT source_type, MAX(imported_at) AS last_imported,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful
        FROM imports WHERE site_id = ? GROUP BY source_type
        """,
        (site_id,),
    ).fetchall()
    opportunities = list_opportunities(conn, site_id)
    return {
        "blogs": counts.get("blog", 0),
        "products": counts.get("product", 0),
        "sources": [dict(row) for row in source_rows],
        "opportunity_count": len(opportunities),
        "top_opportunities": [item for item in opportunities if item["portfolio_slot"]][:3],
        "latest_run": latest_analysis_run(conn, site_id),
    }


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM rule_versions WHERE status = 'active' ORDER BY rule_key, version"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["config"] = json_loads(item.pop("config_json"), {})
        item["source_urls"] = json_loads(item.pop("source_urls_json"), [])
        item["known_failures"] = json_loads(item.pop("known_failures_json"), [])
        result.append(item)
    return result
