from pathlib import Path

from seo_ops.db import connection
from seo_ops.ingest import delete_gsc_import, import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.services.data_quality import assess_gsc_quality
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def test_cms_import_is_idempotent_and_keeps_snapshot(settings):
    first = import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    second = import_cms_bytes(1, "blogs-copy.json", blog_export_bytes(), settings)

    assert first.status == "success"
    assert first.row_count == 2
    assert second.duplicate is True
    assert Path(first.snapshot_path).exists()
    with connection(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM content_snapshots").fetchone()[0] == 2


def test_real_source_types_can_coexist(settings):
    blog = import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    product = import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    gsc = import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)

    assert (blog.source_type, product.source_type, gsc.source_type) == (
        "cms_blogs",
        "cms_products",
        "gsc",
    )
    assert all(outcome.status == "success" for outcome in (blog, product, gsc))


def test_gsc_import_keeps_one_active_dataset_and_accumulates_trusted_windows(settings):
    comparison_bytes = workbook_bytes(comparison=True)
    old = import_gsc_bytes(1, "old-comparison.xlsx", comparison_bytes, settings)
    new = import_gsc_bytes(1, "new-standard.xlsx", workbook_bytes(), settings)
    duplicate_old = import_gsc_bytes(1, "old-comparison-copy.xlsx", comparison_bytes, settings)

    assert duplicate_old.duplicate is True
    with connection(settings) as conn:
        conn.execute(
            "UPDATE imports SET imported_at = ? WHERE id = ?",
            ("2026-07-01T00:00:00+00:00", old.import_id),
        )
        conn.execute(
            "UPDATE imports SET imported_at = ? WHERE id = ?",
            ("2026-07-14T00:00:00+00:00", new.import_id),
        )
        rows = conn.execute(
            """
            SELECT id, analysis_active, quality_eligible FROM imports
            WHERE source_type = 'gsc' ORDER BY id
            """
        ).fetchall()
        metric_counts = {
            row["import_id"]: row["count"]
            for row in conn.execute(
                """
                SELECT import_id, COUNT(*) AS count
                FROM gsc_metrics GROUP BY import_id
                """
            )
        }
        trusted_quality = assess_gsc_quality(conn, 1)
        conn.execute("UPDATE imports SET quality_eligible = 0 WHERE id = ?", (old.import_id,))
        excluded_quality = assess_gsc_quality(conn, 1)

    assert [(row["id"], row["analysis_active"], row["quality_eligible"]) for row in rows] == [
        (old.import_id, 0, 1),
        (new.import_id, 1, 1),
    ]
    assert metric_counts[old.import_id] > 0
    assert metric_counts[new.import_id] > 0
    assert trusted_quality["selected_import_id"] == new.import_id
    assert trusted_quality["snapshot_count"] == 2
    assert trusted_quality["independent_windows"] == 2
    assert trusted_quality["trusted_history_count"] == 1
    assert trusted_quality["excluded_snapshot_count"] == 0
    assert excluded_quality["snapshot_count"] == 1
    assert excluded_quality["independent_windows"] == 1
    assert excluded_quality["excluded_snapshot_count"] == 1
    assert excluded_quality["archived_snapshot_count"] == 1


def test_delete_selected_gsc_import_removes_only_its_data_and_old_analysis(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    old = import_gsc_bytes(1, "old-comparison.xlsx", workbook_bytes(comparison=True), settings)
    old_run = run_analysis(1, settings)
    new = import_gsc_bytes(1, "new-standard.xlsx", workbook_bytes(), settings)
    new_run = run_analysis(1, settings)
    old_snapshot = Path(old.snapshot_path)
    new_snapshot = Path(new.snapshot_path)

    outcome = delete_gsc_import(1, old.import_id, settings)

    assert outcome.deleted_metrics > 0
    assert outcome.deleted_analysis_runs == 1
    assert outcome.deleted_snapshot is True
    assert not old_snapshot.exists()
    assert new_snapshot.exists()
    with connection(settings) as conn:
        imports = conn.execute(
            "SELECT id, analysis_active, quality_eligible FROM imports WHERE source_type = 'gsc'"
        ).fetchall()
        old_metrics = conn.execute(
            "SELECT COUNT(*) FROM gsc_metrics WHERE import_id = ?", (old.import_id,)
        ).fetchone()[0]
        run_ids = {
            int(row["id"]) for row in conn.execute("SELECT id FROM analysis_runs").fetchall()
        }

    assert [(row["id"], row["analysis_active"], row["quality_eligible"]) for row in imports] == [
        (new.import_id, 1, 1)
    ]
    assert old_metrics == 0
    assert old_run.run_id not in run_ids
    assert new_run.run_id in run_ids


def test_deleting_current_gsc_import_does_not_silently_reactivate_history(settings):
    old = import_gsc_bytes(1, "old-comparison.xlsx", workbook_bytes(comparison=True), settings)
    current = import_gsc_bytes(1, "current-standard.xlsx", workbook_bytes(), settings)

    delete_gsc_import(1, current.import_id, settings)

    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, analysis_active, quality_eligible FROM imports
            WHERE source_type = 'gsc' ORDER BY id
            """
        ).fetchall()

    assert [(row["id"], row["analysis_active"], row["quality_eligible"]) for row in rows] == [
        (old.import_id, 0, 1)
    ]
