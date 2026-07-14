from pathlib import Path

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
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
