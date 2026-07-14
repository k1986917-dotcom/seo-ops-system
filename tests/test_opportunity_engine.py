from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import list_opportunities
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def test_analysis_uses_page_data_and_keeps_evidence_gap_separate(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)

    outcome = run_analysis(1, settings)
    assert outcome.status == "success"

    with connection(settings) as conn:
        opportunities = list_opportunities(conn, 1)

    protect = [item for item in opportunities if item["opportunity_type"] == "protect"]
    evidence = [item for item in opportunities if item["opportunity_type"] == "evidence"]
    assert protect
    assert protect[0]["target_ref"].endswith("/blog/example")
    assert protect[0]["gate_status"] == "passed"
    assert evidence
    assert evidence[0]["gate_status"] == "needs_evidence"
    assert evidence[0]["target_kind"] == "query"
