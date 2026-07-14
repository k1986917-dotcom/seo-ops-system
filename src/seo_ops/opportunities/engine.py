from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.utils import json_dumps, utc_now

METHOD_VERSION = "cold-start-0.1.0"


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
        SELECT i.*,
               MAX(CASE WHEN gm.period = 'previous' THEN 1 ELSE 0 END) AS has_previous
        FROM imports i
        JOIN gsc_metrics gm ON gm.import_id = i.id AND gm.dimension = 'page'
        WHERE i.site_id = ? AND i.source_type = 'gsc' AND i.status = 'success'
        GROUP BY i.id
        ORDER BY has_previous DESC, i.imported_at DESC, i.id DESC
        LIMIT 1
        """,
        (site_id,),
    ).fetchone()


def _latest_source_import_ids(conn: sqlite3.Connection, site_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT i.id FROM imports i
        WHERE i.site_id = ? AND i.status = 'success'
          AND i.id = (
            SELECT i2.id FROM imports i2
            WHERE i2.site_id = i.site_id AND i2.source_type = i.source_type AND i2.status = 'success'
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

        if previous:
            previous_clicks = _metric_value(previous, "clicks")
            previous_impressions = _metric_value(previous, "impressions")
            if (
                previous_clicks >= 3
                and previous_impressions >= 50
                and clicks <= previous_clicks * 0.7
            ):
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
                        gate_status="passed",
                        gate_reasons=["存在页面级对比数据", "页面可在 CMS 中定位并编辑"],
                        evidence={
                            "evidence_ids": [
                                evidence_id,
                                f"gsc:{import_id}:page:previous:{url}",
                                f"cms:content_item:{content_item['id']}",
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
                            "limitations": ["尚未控制季节性、算法更新或查询结构变化"],
                        },
                        strength=strength,
                        confidence="high",
                        confidence_weight=1.0,
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
                    gate_status="passed",
                    gate_reasons=["页面有展示量", "页面平均排名处于冷启动 8–20 区间", "页面可编辑"],
                    evidence={
                        "evidence_ids": [evidence_id, f"cms:content_item:{content_item['id']}"],
                        "current": {
                            "clicks": clicks,
                            "impressions": impressions,
                            "ctr": ctr,
                            "position": position,
                        },
                        "limitations": [
                            "平均排名不代表单个查询排名",
                            "需要定向查询—页面数据确认修改方向",
                        ],
                    },
                    strength=strength,
                    confidence="medium",
                    confidence_weight=0.7,
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
                    gate_status="passed",
                    gate_reasons=["使用本站同排名区间中位数", "达到最低展示量", "页面可编辑"],
                    evidence={
                        "evidence_ids": [evidence_id, f"cms:content_item:{content_item['id']}"],
                        "current": {
                            "clicks": clicks,
                            "impressions": impressions,
                            "ctr": ctr,
                            "position": position,
                        },
                        "site_baseline": {"position_bucket": bucket, "median_ctr": baseline},
                        "limitations": [
                            "未按设备、国家和搜索外观进一步分层",
                            "页面聚合 CTR 混合多个查询",
                        ],
                    },
                    strength=strength,
                    confidence="medium",
                    confidence_weight=0.7,
                    effort=1.0,
                )
            )

    evidence_gap_rows = [
        row
        for row in query_rows
        if _metric_value(row, "impressions") >= 100
        and row["position"] is not None
        and float(row["position"]) <= 30
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
                action="在 GSC 中筛选该查询并导出网页维度；确认目标 URL 后再判断更新、新建或忽略。",
                gate_status="needs_evidence",
                gate_reasons=["查询有第一方需求信号", "当前普通导出没有查询—页面联合关系"],
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

    passed = [item for item in candidates if item["gate_status"] == "passed"]
    protect = [item for item in passed if item["opportunity_type"] == "protect"]
    evidence = [item for item in candidates if item["gate_status"] == "needs_evidence"]
    assign("防损", protect)
    assign("最高价值", passed)
    assign("补证据", evidence)
    return len(selected_ids)


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
            top_count = _assign_portfolio(conn, inserted)
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
                            "notes": "资格门槛优先；没有合格项时允许 Top 3 不满。",
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
