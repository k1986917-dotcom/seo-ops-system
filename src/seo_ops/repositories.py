from __future__ import annotations

import sqlite3
from typing import Any

from seo_ops.rules.research_workflow import current_topic_policy_block
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
    active_gsc = conn.execute(
        """
        SELECT id FROM imports
        WHERE site_id = ? AND source_type = 'gsc' AND status = 'success'
          AND analysis_active = 1 AND quality_eligible = 1
        ORDER BY imported_at DESC, id DESC LIMIT 1
        """,
        (site_id,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT * FROM analysis_runs
        WHERE site_id = ? AND status = 'success'
        ORDER BY started_at DESC, id DESC
        """,
        (site_id,),
    ).fetchall()
    if active_gsc:
        active_gsc_id = int(active_gsc["id"])
        for row in rows:
            item = dict(row)
            metadata = json_loads(item.get("metadata_json"), {})
            try:
                if int(metadata.get("gsc_import_id")) == active_gsc_id:
                    return item
            except (TypeError, ValueError):
                continue
    for row in rows:
        item = dict(row)
        metadata = json_loads(item.get("metadata_json"), {})
        if metadata.get("research_only") is True:
            return item
    return None


def list_opportunities(
    conn: sqlite3.Connection, site_id: int, only_latest: bool = True
) -> list[dict[str, Any]]:
    run = latest_analysis_run(conn, site_id) if only_latest else None
    if only_latest and not run:
        return []
    params: tuple[Any, ...]
    where: str
    if run:
        where = """
        WHERE o.analysis_run_id = ?
           OR (o.site_id = ? AND o.rule_key = 'research_topic_candidate'
               AND o.status IN ('proposed','accepted','in_progress'))
        """
        params = (run["id"], site_id)
    else:
        where = """
        WHERE o.site_id = ? AND o.rule_key = 'research_topic_candidate'
          AND o.status IN ('proposed','accepted','in_progress')
        """
        params = (site_id,)
    rows = conn.execute(
        f"""
        SELECT o.* FROM opportunities o
        {where}
        ORDER BY
            CASE WHEN portfolio_slot IS NOT NULL THEN 0 ELSE 1 END,
            CASE portfolio_slot
                WHEN '旧文章 1' THEN 1 WHEN '旧文章 2' THEN 2
                WHEN '新文章 1' THEN 3 WHEN '新文章 2' THEN 4
                ELSE 5
            END,
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
    connected_external = conn.execute(
        "SELECT COUNT(*) FROM source_connections WHERE status = 'connected'"
    ).fetchone()[0]
    external_evidence = conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE site_id = ?", (site_id,)
    ).fetchone()[0]
    analysis = latest_analysis_run(conn, site_id)
    latest_research = None
    if analysis:
        latest_research = conn.execute(
            """
            SELECT id, status, candidate_count, completed_at
            FROM research_runs
            WHERE site_id = ? AND analysis_run_id = ?
            ORDER BY started_at DESC, id DESC LIMIT 1
            """,
            (site_id, analysis["id"]),
        ).fetchone()
    top_opportunities = [item for item in opportunities if item["portfolio_slot"]][:4]
    return {
        "blogs": counts.get("blog", 0),
        "products": counts.get("product", 0),
        "sources": [dict(row) for row in source_rows],
        "opportunity_count": len(top_opportunities),
        "candidate_count": len(opportunities),
        "top_opportunities": top_opportunities,
        "latest_run": analysis,
        "connected_external": int(connected_external),
        "external_evidence_count": int(external_evidence),
        "latest_research": dict(latest_research) if latest_research else None,
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


def list_actions(conn: sqlite3.Connection, site_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*, o.title AS opportunity_title, o.gate_status, o.rule_key,
               o.method_version, o.evidence_json AS opportunity_evidence_json
        FROM actions a
        LEFT JOIN opportunities o ON o.id = a.opportunity_id
        WHERE a.site_id = ?
        ORDER BY
            CASE a.workflow_status
                WHEN 'in_progress' THEN 1
                WHEN 'planned' THEN 2
                WHEN 'completed' THEN 3
                ELSE 4
            END,
            a.decided_at DESC,
            a.id DESC
        """,
        (site_id,),
    ).fetchall()
    actions = []
    for row in rows:
        item = dict(row)
        item["baseline"] = json_loads(item.pop("baseline_json"), {})
        item["deliverable"] = json_loads(item.get("actual_change"), None)
        item["opportunity_evidence"] = json_loads(item.pop("opportunity_evidence_json"), {})
        step_rows = conn.execute(
            """
            SELECT * FROM action_steps
            WHERE action_id = ? ORDER BY step_order
            """,
            (item["id"],),
        ).fetchall()
        steps = []
        for step_row in step_rows:
            step = dict(step_row)
            step["requires_human"] = bool(step["requires_human"])
            step["evidence_refs"] = json_loads(step.pop("evidence_refs_json"), [])
            steps.append(step)
        done_count = sum(1 for step in steps if step["status"] == "done")
        next_step = next((step for step in steps if step["status"] == "pending"), None)
        item["steps"] = steps
        item["done_count"] = done_count
        item["total_count"] = len(steps)
        item["progress"] = round(done_count * 100 / len(steps)) if steps else 0
        item["next_step_id"] = next_step["id"] if next_step else None
        actions.append(item)
    return actions


def list_research_runs(
    conn: sqlite3.Connection,
    site_id: int,
    limit: int = 10,
    *,
    include_candidates: bool = True,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM research_runs
        WHERE site_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (site_id, limit),
    ).fetchall()
    runs = []
    for row in rows:
        item = dict(row)
        item["budgets"] = json_loads(item.pop("budgets_json"), {})
        item["usage"] = json_loads(item.pop("usage_json"), {})
        item["seed_queries"] = json_loads(item.pop("seed_queries_json"), [])
        item["filters"] = json_loads(item.pop("filters_json"), {})
        item["topic_name"] = None
        if item.get("topic_id"):
            topic = conn.execute(
                "SELECT preferred_name FROM topic_nodes WHERE id = ? AND site_id = ?",
                (item["topic_id"], site_id),
            ).fetchone()
            item["topic_name"] = str(topic["preferred_name"]) if topic else None
        candidate_rows = (
            conn.execute(
                """
            SELECT * FROM research_candidates
            WHERE research_run_id = ?
            ORDER BY CASE gate_status WHEN 'needs_evidence' THEN 0 ELSE 1 END, id
            """,
                (item["id"],),
            ).fetchall()
            if include_candidates
            else []
        )
        candidates = []
        for candidate_row in candidate_rows:
            candidate = dict(candidate_row)
            candidate["evidence_refs"] = json_loads(candidate.pop("evidence_refs_json"), [])
            candidate["source_urls"] = json_loads(candidate.pop("source_urls_json"), [])
            candidate["facts"] = json_loads(candidate.pop("facts_json"), [])
            candidate["inference"] = json_loads(candidate.pop("inference_json"), [])
            candidate["overlap"] = json_loads(candidate.pop("overlap_json"), {})
            candidate["limitations"] = json_loads(candidate.pop("limitations_json"), [])
            if candidate.get("decision") == "pending":
                policy_block = current_topic_policy_block(str(candidate["topic"]))
                if policy_block:
                    candidate["stored_gate_status"] = candidate["gate_status"]
                    candidate["gate_status"] = "blocked"
                    if policy_block not in candidate["limitations"]:
                        candidate["limitations"].append(policy_block)
                    candidate["recommended_next_step"] = (
                        "拆成一个可独立回答、可验证且与现有文章不同的具体用户任务。"
                    )
            candidates.append(candidate)
        item["candidates"] = candidates
        runs.append(item)
    return runs
