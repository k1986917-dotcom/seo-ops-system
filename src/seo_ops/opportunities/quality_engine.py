from __future__ import annotations

from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.opportunities import engine as base_engine
from seo_ops.services.data_quality import assess_gsc_quality
from seo_ops.utils import json_dumps, utc_now

METHOD_VERSION = "cold-start-0.2.0"
AnalysisOutcome = base_engine.AnalysisOutcome

_IMPRESSION_DEPENDENT_RULES = {
    "protect_click_loss",
    "site_relative_ctr",
    "striking_distance_page",
    "query_page_evidence_gap",
}


def _apply_quality_gate(
    candidates: list[dict[str, Any]], quality: dict[str, Any]
) -> list[dict[str, Any]]:
    status = quality["status"]
    context = {
        "status": status,
        "independent_windows": quality.get("independent_windows", 0),
        "selected_import_id": quality.get("selected_import_id"),
        "date_start": quality.get("date_start"),
        "date_end": quality.get("date_end"),
        "anomaly_keys": [item["key"] for item in quality.get("anomalies", [])],
        "decision_note": quality["decision_note"],
    }

    for candidate in candidates:
        if candidate["rule_key"] not in _IMPRESSION_DEPENDENT_RULES:
            continue
        evidence = candidate["evidence"]
        evidence["data_quality"] = context
        evidence.setdefault("limitations", []).append(quality["decision_note"])

        if status == "known_anomaly":
            candidate["gate_status"] = "needs_evidence"
            candidate["confidence"] = "low"
            candidate["confidence_weight"] = min(candidate["confidence_weight"], 0.4)
            candidate["gate_reasons"].append("所选日期命中 Google 官方 GSC 数据异常")
        elif status == "provisional":
            candidate["gate_reasons"].append(
                f"当前仅有 {quality['independent_windows']} 个独立观察窗口，结论仍在观察中"
            )
            if candidate["confidence"] == "high":
                candidate["confidence"] = "medium"
                candidate["confidence_weight"] = min(candidate["confidence_weight"], 0.7)
            elif candidate["confidence"] == "medium":
                candidate["confidence_weight"] = min(candidate["confidence_weight"], 0.6)

        candidate["priority"] = round(
            candidate["strength"] * candidate["confidence_weight"] / max(candidate["effort"], 0.5),
            1,
        )
    return candidates


def run_analysis(site_id: int, settings: Settings | None = None) -> AnalysisOutcome:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        gsc_import = base_engine._latest_gsc_import(conn, site_id)
        if not gsc_import:
            raise ValueError("没有可用于分析的成功 GSC 导入")
        source_ids = base_engine._latest_source_import_ids(conn, site_id)
        quality = assess_gsc_quality(conn, site_id, int(gsc_import["id"]))
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
            candidates = base_engine._build_candidates(conn, site_id, int(gsc_import["id"]))
            candidates = _apply_quality_gate(candidates, quality)
            inserted = base_engine._insert_candidates(conn, run_id, site_id, candidates)
            conn.execute(
                "UPDATE opportunities SET method_version = ? WHERE analysis_run_id = ?",
                (METHOD_VERSION, run_id),
            )
            top_count = base_engine._assign_portfolio(conn, inserted)
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
                            "gsc_quality_status": quality["status"],
                            "gsc_independent_windows": quality["independent_windows"],
                            "notes": "资格门槛与 GSC 稳定性门槛优先；没有合格项时允许 Top 3 不满。",
                        }
                    ),
                    run_id,
                ),
            )
        suffix = ""
        if quality["status"] == "provisional":
            suffix = "；当前 GSC 信号仍在观察中，已降低置信权重"
        elif quality["status"] == "known_anomaly":
            suffix = "；所选日期命中已知数据异常，曝光相关项已改为补证据"
        return AnalysisOutcome(
            run_id,
            "success",
            len(inserted),
            top_count,
            f"生成 {len(inserted)} 个候选，其中 {top_count} 个进入当前组合{suffix}",
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
