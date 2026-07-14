from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

KNOWN_GSC_ANOMALIES = (
    {
        "key": "gsc-impressions-2025-2026",
        "start": date(2025, 5, 13),
        "end": date(2026, 4, 27),
        "metric": "impressions",
        "title": "GSC 曝光统计错误",
        "effect": "曝光、CTR 与平均位置可能失真；点击不受该错误影响。修复后曝光可能下降。",
        "source_url": "https://support.google.com/webmasters/answer/6211453?hl=en",
    },
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _overlapping_anomalies(start: date | None, end: date | None) -> list[dict[str, Any]]:
    if not start or not end:
        return []
    return [item for item in KNOWN_GSC_ANOMALIES if start <= item["end"] and end >= item["start"]]


def assess_gsc_quality(
    conn: sqlite3.Connection,
    site_id: int,
    import_id: int | None = None,
) -> dict[str, Any]:
    imports = conn.execute(
        """
        SELECT id, imported_at, metadata_json
        FROM imports
        WHERE site_id = ? AND source_type = 'gsc' AND status = 'success'
        ORDER BY imported_at DESC, id DESC
        """,
        (site_id,),
    ).fetchall()
    if not imports:
        return {
            "status": "missing",
            "label": "没有 GSC 数据",
            "snapshot_count": 0,
            "independent_windows": 0,
            "anomalies": [],
            "reasons": ["尚未导入 GSC"],
            "decision_note": "不能生成基于搜索表现的决策",
        }

    selected_id = import_id or int(imports[0]["id"])
    bounds = conn.execute(
        """
        SELECT MIN(dimension_value) AS start_date, MAX(dimension_value) AS end_date
        FROM gsc_metrics
        WHERE import_id = ? AND dimension = 'date' AND period = 'current'
        """,
        (selected_id,),
    ).fetchone()
    start = _parse_date(bounds["start_date"] if bounds else None)
    end = _parse_date(bounds["end_date"] if bounds else None)
    anomalies = _overlapping_anomalies(start, end)

    window_keys: set[str] = set()
    comparison_count = 0
    for item in imports:
        try:
            metadata = json.loads(item["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if metadata.get("comparison"):
            comparison_count += 1
        date_bound = conn.execute(
            """
            SELECT MAX(dimension_value) AS end_date
            FROM gsc_metrics
            WHERE import_id = ? AND dimension = 'date' AND period = 'current'
            """,
            (item["id"],),
        ).fetchone()
        observed = date_bound["end_date"] if date_bound else None
        window_keys.add(str(observed or item["imported_at"][:10]))

    independent_windows = len(window_keys)
    reasons: list[str] = []
    if anomalies:
        status = "known_anomaly"
        label = "命中已知数据异常"
        reasons.extend(item["effect"] for item in anomalies)
        decision_note = "曝光相关机会只能进入补证据，不应直接触发内容修改"
    elif independent_windows < 3:
        status = "provisional"
        label = "观察窗口不足"
        reasons.append(f"目前只有 {independent_windows} 个独立观察窗口；至少积累 3 个再判断稳定性")
        decision_note = "可以生成低/中置信的诊断任务，不能把一次波动当成效果或原因"
    else:
        status = "usable"
        label = "可用于决策，仍需分层核验"
        reasons.append("已具备至少 3 个独立观察窗口")
        decision_note = "候选仍需按查询、页面、国家、设备与搜索外观核验"

    if comparison_count == 0:
        reasons.append("没有可识别的同期对比导出")
    reasons.append("GSC API/导出可能只返回顶部数据，维度聚合也会改变数字")

    return {
        "status": status,
        "label": label,
        "snapshot_count": len(imports),
        "independent_windows": independent_windows,
        "comparison_count": comparison_count,
        "selected_import_id": selected_id,
        "date_start": start.isoformat() if start else None,
        "date_end": end.isoformat() if end else None,
        "anomalies": anomalies,
        "reasons": reasons,
        "decision_note": decision_note,
        "policy": [
            "最近不完整数据不进入正式比较",
            "高曝光零点击至少跨两个完整窗口仍存在，才进入 SERP 验证",
            "必须拆分查询/页面/国家/设备/搜索外观，避免聚合错觉",
            "遇到突变先核对 Google 数据异常与排名更新日历",
            "SerpAPI 验证结果页，Trends 区分季节性；二者不能替代 GSC",
        ],
    }
