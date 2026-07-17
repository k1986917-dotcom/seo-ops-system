from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.utils import json_dumps, json_loads, utc_now

METHOD_VERSION = "cold-start-0.2.0"


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    run_id: int
    status: str
    opportunity_count: int
    top_count: int
    message: str


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = "/" + (parsed.path or "").strip("/")
    if path == "/":
        path = ""
    return f"{host}{path}".lower()


def _position_bucket(position: float | None) -> str | None:
    if position is None:
        return None
    if position <= 3:
        return "1-3"
    if position <= 7:
        return "4-7"
    if position <= 10:
        return "8-10"
    if position <= 20:
        return "11-20"
    return "21+"


def _metric_value(row: sqlite3.Row, key: str) -> float:
    value = row[key]
    return float(value) if value is not None else 0.0


def _relative_strength(impressions: float, max_impressions: float, signal: float) -> float:
    if max_impressions <= 0:
        demand_component = 0.0
    else:
        demand_component = math.log1p(max(impressions, 0)) / math.log1p(max_impressions)
    return round(min(100.0, max(0.0, demand_component * 55 + signal * 45)), 1)


def _priority(strength: float, confidence_weight: float, effort: float) -> float:
    return round(strength * confidence_weight / max(effort, 0.5), 1)


def _latest_gsc_import(conn: sqlite3.Connection, site_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT i.*
        FROM imports i
        JOIN gsc_metrics gm ON gm.import_id = i.id AND gm.dimension = 'page'
        WHERE i.site_id = ? AND i.source_type = 'gsc' AND i.status = 'success'
          AND i.analysis_active = 1 AND i.quality_eligible = 1
        GROUP BY i.id
        ORDER BY i.imported_at DESC, i.id DESC
        LIMIT 1
        """,
        (site_id,),
    ).fetchone()


def _latest_source_import_ids(conn: sqlite3.Connection, site_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT i.id FROM imports i
        WHERE i.site_id = ? AND i.status = 'success'
          AND (i.source_type <> 'gsc' OR (i.analysis_active = 1 AND i.quality_eligible = 1))
          AND i.id = (
            SELECT i2.id FROM imports i2
            WHERE i2.site_id = i.site_id AND i2.source_type = i.source_type AND i2.status = 'success'
              AND (i2.source_type <> 'gsc' OR (i2.analysis_active = 1 AND i2.quality_eligible = 1))
            ORDER BY i2.imported_at DESC, i2.id DESC LIMIT 1
          )
        ORDER BY i.source_type
        """,
        (site_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _content_map(conn: sqlite3.Connection, site_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM content_items WHERE site_id = ? AND status = 'active'", (site_id,)
    ).fetchall()
    return {_normalize_url(row["canonical_url"]): dict(row) for row in rows}


def _joint_rows_for_window(
    conn: sqlite3.Connection,
    import_id: int,
    start_date: str,
    end_date: str,
) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT page, query,
               SUM(COALESCE(clicks, 0)) AS clicks,
               SUM(COALESCE(impressions, 0)) AS impressions,
               CASE
                 WHEN SUM(COALESCE(impressions, 0)) > 0
                 THEN SUM(COALESCE(clicks, 0)) / SUM(COALESCE(impressions, 0))
                 ELSE NULL
               END AS ctr,
               CASE
                 WHEN SUM(
                   CASE WHEN position IS NOT NULL AND impressions IS NOT NULL
                        THEN impressions ELSE 0 END
                 ) > 0
                 THEN SUM(
                   CASE WHEN position IS NOT NULL AND impressions IS NOT NULL
                        THEN position * impressions ELSE 0 END
                 ) / SUM(
                   CASE WHEN position IS NOT NULL AND impressions IS NOT NULL
                        THEN impressions ELSE 0 END
                 )
                 ELSE AVG(position)
               END AS position
        FROM gsc_query_page_metrics
        WHERE import_id = ? AND data_date BETWEEN ? AND ?
        GROUP BY page, query
        ORDER BY page, impressions DESC, query
        """,
        (import_id, start_date, end_date),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        normalized_page = _normalize_url(str(row["page"]))
        items = grouped.setdefault(normalized_page, [])
        if len(items) >= 10:
            continue
        items.append(
            {
                "query": str(row["query"]),
                "clicks": _metric_value(row, "clicks"),
                "impressions": _metric_value(row, "impressions"),
                "ctr": float(row["ctr"]) if row["ctr"] is not None else None,
                "position": (float(row["position"]) if row["position"] is not None else None),
                "start_date": start_date,
                "end_date": end_date,
            }
        )
    return grouped


def _query_page_evidence(
    conn: sqlite3.Connection,
    import_id: int,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    set[str],
]:
    imported = conn.execute(
        "SELECT metadata_json FROM imports WHERE id = ?", (import_id,)
    ).fetchone()
    metadata = json_loads(imported["metadata_json"], {}) if imported else {}
    current_start = str(metadata.get("current_start_date") or "")
    current_end = str(metadata.get("current_end_date") or "")
    if not current_start or not current_end:
        return {}, {}, set()
    current = _joint_rows_for_window(conn, import_id, current_start, current_end)
    previous_start = str(metadata.get("previous_start_date") or "")
    previous_end = str(metadata.get("previous_end_date") or "")
    previous = (
        _joint_rows_for_window(conn, import_id, previous_start, previous_end)
        if previous_start and previous_end
        else {}
    )
    mapped_queries = {str(item["query"]).casefold() for items in current.values() for item in items}
    return current, previous, mapped_queries


def _candidate(
    *,
    rule_key: str,
    opportunity_type: str,
    target_kind: str,
    target_ref: str,
    title: str,
    action: str,
    gate_status: str,
    gate_reasons: list[str],
    evidence: dict[str, Any],
    strength: float,
    confidence: str,
    confidence_weight: float,
    effort: float,
) -> dict[str, Any]:
    return {
        "rule_key": rule_key,
        "opportunity_type": opportunity_type,
        "target_kind": target_kind,
        "target_ref": target_ref,
        "title": title,
        "recommended_action": action,
        "gate_status": gate_status,
        "gate_reasons": gate_reasons,
        "evidence": evidence,
        "strength": strength,
        "confidence": confidence,
        "confidence_weight": confidence_weight,
        "effort": effort,
        "priority": _priority(strength, confidence_weight, effort),
    }


def _build_candidates(
    conn: sqlite3.Connection, site_id: int, import_id: int
) -> list[dict[str, Any]]:
    page_rows = conn.execute(
        """
        SELECT * FROM gsc_metrics
        WHERE import_id = ? AND dimension = 'page'
        ORDER BY impressions DESC
        """,
        (import_id,),
    ).fetchall()
    query_rows = conn.execute(
        """
        SELECT * FROM gsc_metrics
        WHERE import_id = ? AND dimension = 'query' AND period = 'current'
        ORDER BY impressions DESC
        """,
        (import_id,),
    ).fetchall()
    if not page_rows:
        raise ValueError("选定 GSC 导入中没有网页维度")

    current_pages = {row["dimension_value"]: row for row in page_rows if row["period"] == "current"}
    previous_pages = {
        row["dimension_value"]: row for row in page_rows if row["period"] == "previous"
    }
    current_joint_by_page, previous_joint_by_page, mapped_queries = _query_page_evidence(
        conn, import_id
    )
    content_by_url = _content_map(conn, site_id)
    max_impressions = max(
        (_metric_value(row, "impressions") for row in current_pages.values()), default=1
    )

    ctr_buckets: dict[str, list[float]] = {}
    for row in current_pages.values():
        position = row["position"]
        ctr = row["ctr"]
        impressions = _metric_value(row, "impressions")
        bucket = _position_bucket(float(position) if position is not None else None)
        if bucket and ctr is not None and impressions >= 20:
            ctr_buckets.setdefault(bucket, []).append(float(ctr))
    ctr_baselines = {
        bucket: median(values) for bucket, values in ctr_buckets.items() if len(values) >= 3
    }

    candidates: list[dict[str, Any]] = []
    for url, current in current_pages.items():
        normalized = _normalize_url(url)
        content_item = content_by_url.get(normalized)
        if not content_item:
            continue
        previous = previous_pages.get(url)
        impressions = _metric_value(current, "impressions")
        clicks = _metric_value(current, "clicks")
        ctr = float(current["ctr"]) if current["ctr"] is not None else None
        position = float(current["position"]) if current["position"] is not None else None
        target_kind = content_item["content_type"]
        evidence_id = f"gsc:{import_id}:page:current:{url}"
        current_joint = current_joint_by_page.get(normalized, [])
        previous_joint = previous_joint_by_page.get(normalized, [])
        has_current_joint = bool(current_joint)
        joint_evidence_ids = [
            f"gsc:{import_id}:query_page:{item['query']}:{url}" for item in current_joint
        ]

        if previous:
            previous_clicks = _metric_value(previous, "clicks")
            previous_impressions = _metric_value(previous, "impressions")
            if (
                previous_clicks >= 3
                and previous_impressions >= 50
                and clicks <= previous_clicks * 0.7
            ):
                has_comparable_joint = bool(current_joint and previous_joint)
                drop_ratio = (previous_clicks - clicks) / max(previous_clicks, 1)
                strength = _relative_strength(previous_impressions, max_impressions, drop_ratio)
                candidates.append(
                    _candidate(
                        rule_key="protect_click_loss",
                        opportunity_type="protect",
                        target_kind=target_kind,
                        target_ref=url,
                        title=f"先诊断点击损失：{content_item['title']}",
                        action="检查损失来自展示、排名还是 CTR，再决定更新标题、正文或暂不修改。",
                        gate_status=("passed" if has_comparable_joint else "needs_evidence"),
                        gate_reasons=(
                            ["两个窗口均有查询—页面联合数据", "页面可在 CMS 中定位并编辑"]
                            if has_comparable_joint
                            else ["只有页面级点击对比，尚不能解释查询结构变化"]
                        ),
                        evidence={
                            "evidence_ids": [
                                evidence_id,
                                f"gsc:{import_id}:page:previous:{url}",
                                f"cms:content_item:{content_item['id']}",
                                *joint_evidence_ids,
                            ],
                            "current": {
                                "clicks": clicks,
                                "impressions": impressions,
                                "ctr": ctr,
                                "position": position,
                            },
                            "previous": {
                                "clicks": previous_clicks,
                                "impressions": previous_impressions,
                                "ctr": previous["ctr"],
                                "position": previous["position"],
                            },
                            "query_page": {
                                "current": current_joint,
                                "previous": previous_joint,
                            },
                            "limitations": [
                                "尚未控制季节性或算法更新",
                                *(
                                    []
                                    if has_comparable_joint
                                    else ["缺少两个窗口可比的查询—页面联合行"]
                                ),
                            ],
                        },
                        strength=strength,
                        confidence="high" if has_comparable_joint else "low",
                        confidence_weight=1.0 if has_comparable_joint else 0.4,
                        effort=1.5,
                    )
                )

        if position is not None and 8 <= position <= 20 and impressions >= 50:
            closeness = 1 - ((position - 8) / 12)
            strength = _relative_strength(impressions, max_impressions, closeness)
            candidates.append(
                _candidate(
                    rule_key="striking_distance_page",
                    opportunity_type="optimize",
                    target_kind=target_kind,
                    target_ref=url,
                    title=f"评估临界排名页：{content_item['title']}",
                    action="查看该页实际查询与内容缺口，优先更新现有页面；没有补充证据前不新建相近文章。",
                    gate_status="passed" if has_current_joint else "needs_evidence",
                    gate_reasons=(
                        ["页面有展示量", "平均排名处于 8–20 区间", "已有查询—页面联合行"]
                        if has_current_joint
                        else ["页面级信号只够诊断；缺少查询—页面联合行"]
                    ),
                    evidence={
                        "evidence_ids": [
                            evidence_id,
                            f"cms:content_item:{content_item['id']}",
                            *joint_evidence_ids,
                        ],
                        "current": {
                            "clicks": clicks,
                            "impressions": impressions,
                            "ctr": ctr,
                            "position": position,
                        },
                        "query_page": {"current": current_joint},
                        "limitations": [
                            "平均排名不代表单个查询排名",
                            "已使用查询—页面联合行"
                            if has_current_joint
                            else "需要查询—页面联合数据确认修改方向",
                        ],
                    },
                    strength=strength,
                    confidence="medium" if has_current_joint else "low",
                    confidence_weight=0.7 if has_current_joint else 0.4,
                    effort=2.5 if target_kind == "blog" else 2.0,
                )
            )

        bucket = _position_bucket(position)
        baseline = ctr_baselines.get(bucket or "")
        if (
            ctr is not None
            and baseline is not None
            and impressions >= 100
            and ctr < baseline * 0.5
            and position is not None
            and position <= 20
        ):
            gap_ratio = min(1.0, (baseline - ctr) / max(baseline, 0.0001))
            strength = _relative_strength(impressions, max_impressions, gap_ratio)
            candidates.append(
                _candidate(
                    rule_key="site_relative_ctr",
                    opportunity_type="optimize",
                    target_kind=target_kind,
                    target_ref=url,
                    title=f"检查站内相对低 CTR：{content_item['title']}",
                    action="先核对查询意图与搜索结果外观，再考虑改标题和描述；不是看到低 CTR 就自动改。",
                    gate_status="passed" if has_current_joint else "needs_evidence",
                    gate_reasons=(
                        ["使用本站同排名区间中位数", "达到最低展示量", "已有查询—页面联合行"]
                        if has_current_joint
                        else ["页面聚合 CTR 只够诊断；缺少查询—页面联合行"]
                    ),
                    evidence={
                        "evidence_ids": [
                            evidence_id,
                            f"cms:content_item:{content_item['id']}",
                            *joint_evidence_ids,
                        ],
                        "current": {
                            "clicks": clicks,
                            "impressions": impressions,
                            "ctr": ctr,
                            "position": position,
                        },
                        "site_baseline": {"position_bucket": bucket, "median_ctr": baseline},
                        "query_page": {"current": current_joint},
                        "limitations": [
                            "未按设备、国家和搜索外观进一步分层",
                            "已按联合行列出页面查询"
                            if has_current_joint
                            else "页面聚合 CTR 混合多个未知查询",
                        ],
                    },
                    strength=strength,
                    confidence="medium" if has_current_joint else "low",
                    confidence_weight=0.7 if has_current_joint else 0.4,
                    effort=1.0,
                )
            )

    evidence_gap_rows = [
        row
        for row in query_rows
        if _metric_value(row, "impressions") >= 100
        and row["position"] is not None
        and float(row["position"]) <= 30
        and str(row["dimension_value"]).casefold() not in mapped_queries
    ][:5]
    max_query_impressions = max(
        (_metric_value(row, "impressions") for row in evidence_gap_rows), default=1
    )
    for row in evidence_gap_rows:
        query = row["dimension_value"]
        impressions = _metric_value(row, "impressions")
        position = float(row["position"]) if row["position"] is not None else None
        strength = _relative_strength(impressions, max_query_impressions, 0.5)
        candidates.append(
            _candidate(
                rule_key="query_page_evidence_gap",
                opportunity_type="evidence",
                target_kind="query",
                target_ref=query,
                title=f"确认查询落到哪个页面：{query}",
                action="自动同步未返回该查询的联合行；保持待诊断，必要时再用 GSC 手工筛选作为例外补证据。",
                gate_status="needs_evidence",
                gate_reasons=["查询有第一方需求信号", "当前批次没有该查询的查询—页面联合行"],
                evidence={
                    "evidence_ids": [f"gsc:{import_id}:query:current:{query}"],
                    "current": {
                        "clicks": _metric_value(row, "clicks"),
                        "impressions": impressions,
                        "ctr": row["ctr"],
                        "position": position,
                    },
                    "missing": ["query_page_mapping"],
                    "limitations": ["该任务不是新内容建议，只是补齐决策所需证据"],
                },
                strength=strength,
                confidence="low",
                confidence_weight=0.4,
                effort=0.5,
            )
        )

    return candidates


def _insert_candidates(
    conn: sqlite3.Connection, run_id: int, site_id: int, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    created_at = utc_now()
    inserted: list[dict[str, Any]] = []
    for candidate in candidates:
        cursor = conn.execute(
            """
            INSERT INTO opportunities(
                analysis_run_id, site_id, rule_key, opportunity_type, target_kind,
                target_ref, title, recommended_action, gate_status, gate_reasons_json,
                evidence_json, strength, confidence, confidence_weight, effort,
                priority, method_version, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                site_id,
                candidate["rule_key"],
                candidate["opportunity_type"],
                candidate["target_kind"],
                candidate["target_ref"],
                candidate["title"],
                candidate["recommended_action"],
                candidate["gate_status"],
                json_dumps(candidate["gate_reasons"]),
                json_dumps(candidate["evidence"]),
                candidate["strength"],
                candidate["confidence"],
                candidate["confidence_weight"],
                candidate["effort"],
                candidate["priority"],
                METHOD_VERSION,
                created_at,
            ),
        )
        inserted.append({**candidate, "id": int(cursor.lastrowid)})
    return inserted


def _assign_portfolio(conn: sqlite3.Connection, candidates: list[dict[str, Any]]) -> int:
    selected_ids: set[int] = set()
    selected_targets: set[str] = set()

    def assign(slot: str, pool: list[dict[str, Any]]) -> bool:
        for item in sorted(pool, key=lambda candidate: candidate["priority"], reverse=True):
            if item["id"] in selected_ids or item["target_ref"] in selected_targets:
                continue
            conn.execute(
                "UPDATE opportunities SET portfolio_slot = ? WHERE id = ?", (slot, item["id"])
            )
            selected_ids.add(item["id"])
            selected_targets.add(item["target_ref"])
            return True
        return False

    passed = [
        item
        for item in candidates
        if item["gate_status"] == "passed"
        and item.get("status", "proposed") in {"proposed", "accepted", "in_progress"}
    ]
    old_articles = [
        item
        for item in passed
        if item["target_kind"] == "blog" and item["opportunity_type"] in {"protect", "optimize"}
    ]
    new_articles = [item for item in passed if item["opportunity_type"] == "create"]
    for index in range(1, 3):
        if not assign(f"旧文章 {index}", old_articles):
            break
    for index in range(1, 3):
        if not assign(f"新文章 {index}", new_articles):
            break
    return len(selected_ids)


def rebalance_site_portfolio(conn: sqlite3.Connection, site_id: int, analysis_run_id: int) -> int:
    """Select at most two old and two new article tasks without forced filling."""

    conn.execute("UPDATE opportunities SET portfolio_slot = NULL WHERE site_id = ?", (site_id,))
    rows = conn.execute(
        """
        SELECT * FROM opportunities
        WHERE site_id = ?
          AND gate_status = 'passed'
          AND status IN ('proposed','accepted','in_progress')
          AND (
            (analysis_run_id = ? AND target_kind = 'blog'
             AND opportunity_type IN ('protect','optimize'))
            OR opportunity_type = 'create'
          )
        ORDER BY priority DESC, id DESC
        """,
        (site_id, analysis_run_id),
    ).fetchall()
    top_count = _assign_portfolio(conn, [dict(row) for row in rows])
    run = conn.execute(
        "SELECT metadata_json FROM analysis_runs WHERE id = ? AND site_id = ?",
        (analysis_run_id, site_id),
    ).fetchone()
    if run:
        metadata = json_loads(run["metadata_json"], {})
        metadata["top_count"] = top_count
        metadata["portfolio_policy"] = "最多 2 篇旧文章 + 2 篇新文章，不足不补位"
        conn.execute(
            "UPDATE analysis_runs SET metadata_json = ? WHERE id = ?",
            (json_dumps(metadata), analysis_run_id),
        )
    return top_count


def run_analysis(site_id: int, settings: Settings | None = None) -> AnalysisOutcome:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        gsc_import = _latest_gsc_import(conn, site_id)
        if not gsc_import:
            raise ValueError("没有可用于分析的成功 GSC 导入")
        source_ids = _latest_source_import_ids(conn, site_id)
        cursor = conn.execute(
            """
            INSERT INTO analysis_runs(
                site_id, method_version, source_import_ids_json, status, started_at
            ) VALUES(?, ?, ?, 'running', ?)
            """,
            (site_id, METHOD_VERSION, json_dumps(source_ids), utc_now()),
        )
        run_id = int(cursor.lastrowid)

    try:
        with connection(active_settings) as conn:
            candidates = _build_candidates(conn, site_id, int(gsc_import["id"]))
            inserted = _insert_candidates(conn, run_id, site_id, candidates)
            top_count = rebalance_site_portfolio(conn, site_id, run_id)
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'success', completed_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    json_dumps(
                        {
                            "gsc_import_id": int(gsc_import["id"]),
                            "candidate_count": len(inserted),
                            "top_count": top_count,
                            "notes": "资格门槛优先；最多 2 篇旧文章和 2 篇新文章，不足不补位。",
                        }
                    ),
                    run_id,
                ),
            )
        return AnalysisOutcome(
            run_id,
            "success",
            len(inserted),
            top_count,
            f"生成 {len(inserted)} 个候选，其中 {top_count} 个进入当前组合",
        )
    except Exception as exc:
        with connection(active_settings) as conn:
            conn.execute(
                """
                UPDATE analysis_runs SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc), utc_now(), run_id),
            )
        return AnalysisOutcome(run_id, "failed", 0, 0, f"分析失败：{exc}")
