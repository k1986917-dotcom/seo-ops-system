from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import list_opportunities
from seo_ops.services.data_quality import assess_gsc_quality
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def test_single_gsc_window_is_provisional_and_lowers_confidence(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)

    with connection(settings) as conn:
        quality = assess_gsc_quality(conn, 1)
    assert quality["status"] == "provisional"
    assert quality["independent_windows"] == 1

    outcome = run_analysis(1, settings)
    assert outcome.status == "success"
    with connection(settings) as conn:
        opportunities = list_opportunities(conn, 1)
    protect = next(item for item in opportunities if item["opportunity_type"] == "protect")
    assert protect["gate_status"] == "needs_evidence"
    assert protect["confidence"] == "low"
    assert protect["evidence"]["data_quality"]["status"] == "provisional"
    assert protect["evidence"]["query_page"]["current"] == []
