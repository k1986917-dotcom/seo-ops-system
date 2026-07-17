from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.ingest.cms import parse_cms_export
from seo_ops.ingest.gsc import parse_gsc_workbook
from seo_ops.ingest.snapshot import Snapshot, save_snapshot
from seo_ops.repositories import get_site
from seo_ops.utils import json_dumps, json_loads, utc_now


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    import_id: int
    source_type: str
    status: str
    row_count: int
    duplicate: bool
    message: str
    snapshot_path: str


@dataclass(frozen=True, slots=True)
class ImportDeletionOutcome:
    import_id: int
    deleted_metrics: int
    deleted_query_page_metrics: int
    deleted_analysis_runs: int
    deleted_snapshot: bool
    message: str


class ImportDeletionError(ValueError):
    pass


def _stored_snapshot_path(settings: Settings, stored_path: str) -> Path:
    path = Path(stored_path)
    if not path.is_absolute():
        path = settings.project_root / path
    resolved = path.resolve()
    snapshots_root = settings.snapshots_dir.resolve()
    if not resolved.is_relative_to(snapshots_root):
        raise ImportDeletionError("导入记录的快照路径不在系统快照目录中，已停止删除")
    return resolved


def _analysis_runs_using_import(
    conn: sqlite3.Connection, site_id: int, import_id: int
) -> list[int]:
    run_ids: list[int] = []
    rows = conn.execute(
        """
        SELECT id, source_import_ids_json, metadata_json
        FROM analysis_runs WHERE site_id = ?
        """,
        (site_id,),
    ).fetchall()
    for row in rows:
        source_ids = json_loads(row["source_import_ids_json"], [])
        metadata = json_loads(row["metadata_json"], {})
        if import_id in source_ids or metadata.get("gsc_import_id") == import_id:
            run_ids.append(int(row["id"]))
    return run_ids


def _placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def delete_gsc_import(
    site_id: int,
    import_id: int,
    settings: Settings | None = None,
) -> ImportDeletionOutcome:
    """Physically remove one selected GSC import and results derived from it."""

    active_settings = settings or get_settings()
    snapshot_paths: list[Path] = []
    deleted_metrics = 0
    deleted_query_page_metrics = 0
    deleted_analysis_runs = 0

    with connection(active_settings) as conn:
        item = conn.execute(
            """
            SELECT id, site_id, source_type, snapshot_path
            FROM imports WHERE id = ? AND site_id = ?
            """,
            (import_id, site_id),
        ).fetchone()
        if not item:
            raise ImportDeletionError("找不到这次导入，可能已经删除")
        if item["source_type"] != "gsc":
            raise ImportDeletionError("目前只允许逐条删除 GSC 导入")

        snapshot_paths.append(_stored_snapshot_path(active_settings, item["snapshot_path"]))
        deleted_metrics = int(
            conn.execute(
                "SELECT COUNT(*) FROM gsc_metrics WHERE import_id = ?", (import_id,)
            ).fetchone()[0]
        )
        deleted_query_page_metrics = int(
            conn.execute(
                "SELECT COUNT(*) FROM gsc_query_page_metrics WHERE import_id = ?",
                (import_id,),
            ).fetchone()[0]
        )
        analysis_run_ids = _analysis_runs_using_import(conn, site_id, import_id)
        deleted_analysis_runs = len(analysis_run_ids)

        if analysis_run_ids:
            run_marks = _placeholders(analysis_run_ids)
            opportunity_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM opportunities WHERE analysis_run_id IN ({run_marks})",
                    analysis_run_ids,
                ).fetchall()
            ]
            research_run_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM research_runs WHERE analysis_run_id IN ({run_marks})",
                    analysis_run_ids,
                ).fetchall()
            ]

            external_run_ids: set[int] = set()
            if opportunity_ids:
                opportunity_marks = _placeholders(opportunity_ids)
                external_run_ids.update(
                    int(row["id"])
                    for row in conn.execute(
                        f"SELECT id FROM external_runs WHERE opportunity_id IN ({opportunity_marks})",
                        opportunity_ids,
                    ).fetchall()
                )
                conn.execute(
                    f"DELETE FROM actions WHERE opportunity_id IN ({opportunity_marks})",
                    opportunity_ids,
                )
            if research_run_ids:
                research_marks = _placeholders(research_run_ids)
                external_run_ids.update(
                    int(row["external_run_id"])
                    for row in conn.execute(
                        f"""
                        SELECT external_run_id FROM research_run_items
                        WHERE research_run_id IN ({research_marks})
                        """,
                        research_run_ids,
                    ).fetchall()
                )
                conn.execute(
                    f"DELETE FROM research_runs WHERE id IN ({research_marks})",
                    research_run_ids,
                )
            if external_run_ids:
                external_ids = sorted(external_run_ids)
                external_marks = _placeholders(external_ids)
                for row in conn.execute(
                    f"""
                    SELECT response_snapshot_path FROM external_runs
                    WHERE id IN ({external_marks}) AND response_snapshot_path IS NOT NULL
                    """,
                    external_ids,
                ).fetchall():
                    snapshot_paths.append(
                        _stored_snapshot_path(active_settings, row["response_snapshot_path"])
                    )
                conn.execute(
                    f"DELETE FROM external_runs WHERE id IN ({external_marks})", external_ids
                )

            conn.execute(f"DELETE FROM analysis_runs WHERE id IN ({run_marks})", analysis_run_ids)

        conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))

    deleted_snapshot = False
    for snapshot_path in dict.fromkeys(snapshot_paths):
        if snapshot_path.exists():
            snapshot_path.unlink()
            deleted_snapshot = True

    return ImportDeletionOutcome(
        import_id=import_id,
        deleted_metrics=deleted_metrics,
        deleted_query_page_metrics=deleted_query_page_metrics,
        deleted_analysis_runs=deleted_analysis_runs,
        deleted_snapshot=deleted_snapshot,
        message=(
            f"已删除 GSC 导入 #{import_id}、{deleted_metrics} 条汇总指标、"
            f"{deleted_query_page_metrics} 条查询—页面明细"
            f"和 {deleted_analysis_runs} 轮关联旧分析"
        ),
    )


def _display_path(settings: Settings, path: Path) -> str:
    try:
        return str(path.relative_to(settings.project_root))
    except ValueError:
        return str(path)


def _prepare_import(
    settings: Settings,
    site_id: int,
    source_type: str,
    original_name: str,
    snapshot: Snapshot,
) -> tuple[int, bool]:
    with connection(settings) as conn:
        existing = conn.execute(
            "SELECT id, status FROM imports WHERE site_id = ? AND source_type = ? AND sha256 = ?",
            (site_id, source_type, snapshot.sha256),
        ).fetchone()
        if existing and existing["status"] == "success":
            return int(existing["id"]), True
        now = utc_now()
        if existing:
            import_id = int(existing["id"])
            conn.execute(
                """
                UPDATE imports SET status = 'processing', error_message = NULL,
                    imported_at = ?, completed_at = NULL,
                    analysis_active = CASE WHEN source_type = 'gsc' THEN 0 ELSE analysis_active END,
                    quality_eligible = CASE WHEN source_type = 'gsc' THEN 0 ELSE quality_eligible END
                WHERE id = ?
                """,
                (now, import_id),
            )
            return import_id, False
        cursor = conn.execute(
            """
            INSERT INTO imports(
                site_id, source_type, original_name, sha256, snapshot_path,
                status, imported_at, metadata_json, analysis_active, quality_eligible
            ) VALUES(?, ?, ?, ?, ?, 'processing', ?, '{}', ?, ?)
            """,
            (
                site_id,
                source_type,
                original_name,
                snapshot.sha256,
                _display_path(settings, snapshot.path),
                now,
                0 if source_type == "gsc" else 1,
                0 if source_type == "gsc" else 1,
            ),
        )
        return int(cursor.lastrowid), False


def _finish_import(
    settings: Settings,
    import_id: int,
    *,
    status: str,
    row_count: int = 0,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with connection(settings) as conn:
        item = conn.execute(
            "SELECT site_id, source_type FROM imports WHERE id = ?", (import_id,)
        ).fetchone()
        conn.execute(
            """
            UPDATE imports
            SET status = ?, row_count = ?, metadata_json = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, row_count, json_dumps(metadata or {}), error, utc_now(), import_id),
        )
        if item and item["source_type"] == "gsc" and status == "success":
            conn.execute(
                """
                UPDATE imports SET analysis_active = 0
                WHERE site_id = ? AND source_type = 'gsc'
                """,
                (item["site_id"],),
            )
            conn.execute(
                "UPDATE imports SET analysis_active = 1, quality_eligible = 1 WHERE id = ?",
                (import_id,),
            )


def import_gsc_bytes(
    site_id: int,
    original_name: str,
    content: bytes,
    settings: Settings | None = None,
) -> ImportOutcome:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        site = get_site(conn, site_id)
    if not site:
        raise ValueError(f"站点不存在: {site_id}")

    snapshot = save_snapshot(active_settings, site["slug"], "gsc", original_name, content)
    import_id, duplicate = _prepare_import(active_settings, site_id, "gsc", original_name, snapshot)
    if duplicate:
        return ImportOutcome(
            import_id, "gsc", "success", 0, True, "相同文件已导入，未重复写入", str(snapshot.path)
        )

    try:
        parsed = parse_gsc_workbook(content)
        with connection(active_settings) as conn:
            conn.execute("DELETE FROM gsc_metrics WHERE import_id = ?", (import_id,))
            conn.executemany(
                """
                INSERT INTO gsc_metrics(
                    import_id, site_id, dimension, dimension_value, period,
                    clicks, impressions, ctr, position, sheet_name, source_row
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        site_id,
                        metric.dimension,
                        metric.value,
                        metric.period,
                        metric.clicks,
                        metric.impressions,
                        metric.ctr,
                        metric.position,
                        metric.sheet_name,
                        metric.source_row,
                    )
                    for metric in parsed.metrics
                ],
            )
        metadata = {**parsed.metadata, "snapshot_size": snapshot.size}
        _finish_import(
            active_settings,
            import_id,
            status="success",
            row_count=len(parsed.metrics),
            metadata=metadata,
        )
        return ImportOutcome(
            import_id,
            "gsc",
            "success",
            len(parsed.metrics),
            False,
            f"已导入 {len(parsed.metrics)} 条标准化 GSC 指标",
            str(snapshot.path),
        )
    except Exception as exc:
        _finish_import(active_settings, import_id, status="failed", error=str(exc))
        return ImportOutcome(
            import_id, "gsc", "failed", 0, False, f"导入失败：{exc}", str(snapshot.path)
        )


def import_gsc_api_data(
    site_id: int,
    original_name: str,
    raw_payload: dict[str, Any],
    aggregate_rows: list[dict[str, Any]],
    query_page_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    settings: Settings | None = None,
) -> ImportOutcome:
    """Store one immutable OAuth API response and its normalized fact rows."""

    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        site = get_site(conn, site_id)
    if not site:
        raise ValueError(f"站点不存在: {site_id}")

    raw_bytes = json_dumps(raw_payload).encode("utf-8")
    snapshot = save_snapshot(
        active_settings,
        site["slug"],
        "gsc",
        original_name,
        raw_bytes,
    )
    import_id, duplicate = _prepare_import(
        active_settings,
        site_id,
        "gsc",
        original_name,
        snapshot,
    )
    if duplicate:
        with connection(active_settings) as conn:
            existing = conn.execute(
                "SELECT row_count FROM imports WHERE id = ?", (import_id,)
            ).fetchone()
            conn.execute(
                """
                UPDATE imports SET analysis_active = 0
                WHERE site_id = ? AND source_type = 'gsc'
                """,
                (site_id,),
            )
            conn.execute(
                """
                UPDATE imports
                SET analysis_active = 1, quality_eligible = 1
                WHERE id = ?
                """,
                (import_id,),
            )
        row_count = int(existing["row_count"]) if existing else 0
        return ImportOutcome(
            import_id,
            "gsc",
            "success",
            row_count,
            True,
            "GSC API 返回内容未变化，已复用原批次",
            str(snapshot.path),
        )

    try:
        if not aggregate_rows:
            raise ValueError("GSC API 响应中没有可用汇总指标")
        with connection(active_settings) as conn:
            conn.execute("DELETE FROM gsc_metrics WHERE import_id = ?", (import_id,))
            conn.execute(
                "DELETE FROM gsc_query_page_metrics WHERE import_id = ?",
                (import_id,),
            )
            conn.executemany(
                """
                INSERT INTO gsc_metrics(
                    import_id, site_id, dimension, dimension_value, period,
                    clicks, impressions, ctr, position, sheet_name, source_row
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        site_id,
                        row["dimension"],
                        row["value"],
                        row.get("period", "current"),
                        row.get("clicks"),
                        row.get("impressions"),
                        row.get("ctr"),
                        row.get("position"),
                        f"api:{row['dimension']}:{row.get('period', 'current')}",
                        index,
                    )
                    for index, row in enumerate(aggregate_rows, start=1)
                ],
            )
            conn.executemany(
                """
                INSERT INTO gsc_query_page_metrics(
                    import_id, site_id, data_date, query, page, search_type,
                    clicks, impressions, ctr, position, source_row
                ) VALUES(?, ?, ?, ?, ?, 'web', ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        site_id,
                        row["date"],
                        row["query"],
                        row["page"],
                        row.get("clicks"),
                        row.get("impressions"),
                        row.get("ctr"),
                        row.get("position"),
                        index,
                    )
                    for index, row in enumerate(query_page_rows, start=1)
                ],
            )
        row_count = len(aggregate_rows) + len(query_page_rows)
        saved_metadata = {
            **metadata,
            "mode": "api_query_page",
            "comparison": any(row.get("period") == "previous" for row in aggregate_rows),
            "dimensions": ["date", "query", "page", "query+page"],
            "aggregate_rows": len(aggregate_rows),
            "query_page_rows": len(query_page_rows),
            "snapshot_size": snapshot.size,
        }
        _finish_import(
            active_settings,
            import_id,
            status="success",
            row_count=row_count,
            metadata=saved_metadata,
        )
        return ImportOutcome(
            import_id,
            "gsc",
            "success",
            row_count,
            False,
            f"已同步 {len(aggregate_rows)} 条汇总指标和 {len(query_page_rows)} 条查询—页面明细",
            str(snapshot.path),
        )
    except Exception as exc:
        _finish_import(active_settings, import_id, status="failed", error=str(exc))
        return ImportOutcome(
            import_id,
            "gsc",
            "failed",
            0,
            False,
            f"GSC API 数据保存失败：{exc}",
            str(snapshot.path),
        )


def _upsert_content_item(
    conn: sqlite3.Connection,
    *,
    site_id: int,
    import_id: int,
    item: Any,
    captured_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO content_items(
            site_id, content_type, external_id, slug, title, canonical_url, status,
            source_created_at, source_updated_at, first_seen_at, last_seen_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(site_id, content_type, external_id) DO UPDATE SET
            slug = excluded.slug,
            title = excluded.title,
            canonical_url = excluded.canonical_url,
            status = excluded.status,
            source_created_at = excluded.source_created_at,
            source_updated_at = excluded.source_updated_at,
            last_seen_at = excluded.last_seen_at
        """,
        (
            site_id,
            item.content_type,
            item.external_id,
            item.slug,
            item.title,
            item.canonical_url,
            item.status,
            item.source_created_at,
            item.source_updated_at,
            captured_at,
            captured_at,
        ),
    )
    content_item_id = conn.execute(
        """
        SELECT id FROM content_items
        WHERE site_id = ? AND content_type = ? AND external_id = ?
        """,
        (site_id, item.content_type, item.external_id),
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO content_snapshots(
            import_id, content_item_id, title, summary, body, body_sha256,
            seo_title, seo_description, metadata_json, captured_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_id,
            content_item_id,
            item.title,
            item.summary,
            item.body,
            item.body_sha256,
            item.seo_title,
            item.seo_description,
            json_dumps(item.metadata),
            captured_at,
        ),
    )


def import_cms_bytes(
    site_id: int,
    original_name: str,
    content: bytes,
    settings: Settings | None = None,
) -> ImportOutcome:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        site = get_site(conn, site_id)
    if not site:
        raise ValueError(f"站点不存在: {site_id}")

    # Parse once to determine whether this is a blog or product export before naming the source.
    try:
        parsed = parse_cms_export(
            content,
            domain=site["domain"],
            blog_path_template=site["blog_path_template"],
            product_path_template=site["product_path_template"],
        )
    except Exception as exc:
        source_type = "cms_unknown"
        snapshot = save_snapshot(active_settings, site["slug"], source_type, original_name, content)
        import_id, duplicate = _prepare_import(
            active_settings, site_id, source_type, original_name, snapshot
        )
        if not duplicate:
            _finish_import(active_settings, import_id, status="failed", error=str(exc))
        return ImportOutcome(
            import_id,
            source_type,
            "failed",
            0,
            duplicate,
            f"导入失败：{exc}",
            str(snapshot.path),
        )

    source_type = f"cms_{parsed.kind}s"
    snapshot = save_snapshot(active_settings, site["slug"], source_type, original_name, content)
    import_id, duplicate = _prepare_import(
        active_settings, site_id, source_type, original_name, snapshot
    )
    if duplicate:
        return ImportOutcome(
            import_id,
            source_type,
            "success",
            0,
            True,
            "相同文件已导入，未重复写入",
            str(snapshot.path),
        )

    try:
        captured_at = utc_now()
        with connection(active_settings) as conn:
            conn.execute("DELETE FROM content_snapshots WHERE import_id = ?", (import_id,))
            for item in parsed.items:
                _upsert_content_item(
                    conn,
                    site_id=site_id,
                    import_id=import_id,
                    item=item,
                    captured_at=captured_at,
                )
        metadata = {
            "kind": parsed.kind,
            "exported_at": parsed.exported_at,
            "declared_count": parsed.declared_count,
            "excluded_fields": parsed.excluded_fields,
            "snapshot_size": snapshot.size,
        }
        _finish_import(
            active_settings,
            import_id,
            status="success",
            row_count=len(parsed.items),
            metadata=metadata,
        )
        return ImportOutcome(
            import_id,
            source_type,
            "success",
            len(parsed.items),
            False,
            f"已导入 {len(parsed.items)} 条 {parsed.kind} 内容",
            str(snapshot.path),
        )
    except Exception as exc:
        _finish_import(active_settings, import_id, status="failed", error=str(exc))
        return ImportOutcome(
            import_id,
            source_type,
            "failed",
            0,
            False,
            f"导入失败：{exc}",
            str(snapshot.path),
        )
