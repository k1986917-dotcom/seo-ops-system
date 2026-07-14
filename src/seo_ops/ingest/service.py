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
from seo_ops.utils import json_dumps, utc_now


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    import_id: int
    source_type: str
    status: str
    row_count: int
    duplicate: bool
    message: str
    snapshot_path: str


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
                    imported_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (now, import_id),
            )
            return import_id, False
        cursor = conn.execute(
            """
            INSERT INTO imports(
                site_id, source_type, original_name, sha256, snapshot_path,
                status, imported_at, metadata_json
            ) VALUES(?, ?, ?, ?, ?, 'processing', ?, '{}')
            """,
            (
                site_id,
                source_type,
                original_name,
                snapshot.sha256,
                _display_path(settings, snapshot.path),
                now,
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
        conn.execute(
            """
            UPDATE imports
            SET status = ?, row_count = ?, metadata_json = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, row_count, json_dumps(metadata or {}), error, utc_now(), import_id),
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
