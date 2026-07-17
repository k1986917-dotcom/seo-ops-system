import json

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_api_data, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.opportunities.engine import _assign_portfolio
from seo_ops.repositories import dashboard_summary, latest_analysis_run, list_opportunities
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
    assert protect[0]["gate_status"] == "needs_evidence"
    assert evidence
    assert evidence[0]["gate_status"] == "needs_evidence"
    assert evidence[0]["target_kind"] == "query"


def test_api_query_page_rows_promote_page_diagnosis_to_executable(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    page = "https://laserpointerhub.com/blog/example"
    outcome = import_gsc_api_data(
        1,
        "gsc-api-window.json",
        {"source": "test", "responses": []},
        [
            {
                "dimension": "page",
                "value": page,
                "period": "current",
                "clicks": 2,
                "impressions": 100,
                "ctr": 0.02,
                "position": 10,
            },
            {
                "dimension": "page",
                "value": page,
                "period": "previous",
                "clicks": 10,
                "impressions": 100,
                "ctr": 0.1,
                "position": 8,
            },
            {
                "dimension": "query",
                "value": "best example laser",
                "period": "current",
                "clicks": 2,
                "impressions": 100,
                "ctr": 0.02,
                "position": 10,
            },
        ],
        [
            {
                "date": "2026-07-25",
                "query": "best example laser",
                "page": page,
                "clicks": 2,
                "impressions": 100,
                "ctr": 0.02,
                "position": 10,
            },
            {
                "date": "2026-06-25",
                "query": "best example laser",
                "page": page,
                "clicks": 10,
                "impressions": 100,
                "ctr": 0.1,
                "position": 8,
            },
        ],
        {
            "current_start_date": "2026-07-20",
            "current_end_date": "2026-08-16",
            "previous_start_date": "2026-06-22",
            "previous_end_date": "2026-07-19",
        },
        settings,
    )
    assert outcome.status == "success"

    analysis = run_analysis(1, settings)
    assert analysis.status == "success"
    with connection(settings) as conn:
        opportunities = list_opportunities(conn, 1)

    protect = [item for item in opportunities if item["opportunity_type"] == "protect"]
    assert protect
    assert protect[0]["gate_status"] == "passed"
    assert protect[0]["evidence"]["query_page"]["current"][0]["query"] == ("best example laser")


def test_analysis_uses_active_standard_import_over_older_comparison(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    old = import_gsc_bytes(1, "old-comparison.xlsx", workbook_bytes(comparison=True), settings)
    new = import_gsc_bytes(1, "new-standard.xlsx", workbook_bytes(), settings)

    outcome = run_analysis(1, settings)
    assert outcome.status == "success"

    with connection(settings) as conn:
        run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (outcome.run_id,)).fetchone()
        metadata = json.loads(run["metadata_json"])
        source_ids = json.loads(run["source_import_ids_json"])
        opportunities = list_opportunities(conn, 1)

    assert metadata["gsc_import_id"] == new.import_id
    assert new.import_id in source_ids
    assert old.import_id not in source_ids
    assert opportunities
    assert all(
        f"gsc:{old.import_id}:" not in json.dumps(item["evidence"]) for item in opportunities
    )


def test_switching_gsc_batch_hides_stale_analysis_until_current_batch_is_analyzed(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    old = import_gsc_bytes(1, "old.xlsx", workbook_bytes(comparison=True), settings)
    first = run_analysis(1, settings)
    assert first.status == "success"

    new = import_gsc_bytes(1, "new.xlsx", workbook_bytes(), settings)

    with connection(settings) as conn:
        assert latest_analysis_run(conn, 1) is None
        assert list_opportunities(conn, 1) == []
        assert dashboard_summary(conn, 1)["latest_run"] is None

    second = run_analysis(1, settings)
    assert second.status == "success"

    with connection(settings) as conn:
        current = latest_analysis_run(conn, 1)
        metadata = json.loads(current["metadata_json"])

    assert current["id"] == second.run_id
    assert metadata["gsc_import_id"] == new.import_id
    assert old.import_id not in json.loads(current["source_import_ids_json"])


class PortfolioRecorder:
    def __init__(self):
        self.slots = []

    def execute(self, statement, params):
        self.slots.append(params)


def test_portfolio_is_capped_at_two_new_and_two_old_articles():
    conn = PortfolioRecorder()
    candidates = [
        {
            "id": index,
            "target_ref": f"target-{index}",
            "priority": 100 - index,
            "gate_status": "passed",
            "status": "proposed",
            "target_kind": "blog" if index <= 3 else "topic",
            "opportunity_type": "optimize" if index <= 3 else "create",
        }
        for index in range(1, 7)
    ]
    candidates.append(
        {
            "id": 7,
            "target_ref": "product-page",
            "priority": 200,
            "gate_status": "passed",
            "status": "proposed",
            "target_kind": "product",
            "opportunity_type": "optimize",
        }
    )

    selected = _assign_portfolio(conn, candidates)

    assert selected == 4
    assert [slot for slot, _ in conn.slots] == [
        "旧文章 1",
        "旧文章 2",
        "新文章 1",
        "新文章 2",
    ]
    assert {item_id for _, item_id in conn.slots} == {1, 2, 4, 5}
