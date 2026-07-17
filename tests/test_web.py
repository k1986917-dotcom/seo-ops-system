import json

from fastapi.testclient import TestClient

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.web.app import create_app
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def test_health_and_dashboard_render(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        dashboard = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert dashboard.url.path == "/imports"
    assert "数据导入" in dashboard.text


def test_import_history_can_delete_one_gsc_batch(settings):
    imported = import_gsc_bytes(1, "bad.xlsx", workbook_bytes(), settings)
    app = create_app(settings)

    with TestClient(app) as client:
        page = client.get("/imports")
        response = client.post(
            f"/imports/{imported.import_id}/delete",
            data={"site_id": "1"},
            headers={"Origin": "http://127.0.0.1:8787"},
            follow_redirects=False,
        )

    assert "删除这次导入" in page.text
    assert response.status_code == 303
    with connection(settings) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM imports WHERE id = ?", (imported.import_id,)
        ).fetchone()[0]
        metrics = conn.execute(
            "SELECT COUNT(*) FROM gsc_metrics WHERE import_id = ?", (imported.import_id,)
        ).fetchone()[0]
    assert remaining == 0
    assert metrics == 0


def test_topic_tree_page_maps_imported_content(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/topics")

    assert response.status_code == 200
    assert "主题图谱" in response.text
    assert "Example Laser Guide" in response.text
    assert "Demo Laser" in response.text
    with connection(settings) as conn:
        primary_count = conn.execute(
            "SELECT COUNT(*) FROM topic_content_links WHERE coverage_role = 'primary'"
        ).fetchone()[0]
    assert primary_count == 3


def test_two_stage_pages_are_simple_and_generic_collection_is_blocked(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    analysis = run_analysis(1, settings)
    now = "2026-07-15T00:00:00+00:00"
    with connection(settings) as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_runs(
                site_id, analysis_run_id, status, budgets_json, usage_json,
                seed_queries_json, candidate_count, ai_model, started_at,
                completed_at, seed_type, filters_json
            ) VALUES(1, ?, 'success', '{}', '{}', '[]', 1, 'deepseek-v4-flash',
                     ?, ?, 'topic_gap', '{}')
            """,
            (analysis.run_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO research_candidates(
                research_run_id, site_id, topic, normalized_topic, intent,
                rationale, recommended_next_step, gate_status, evidence_refs_json,
                source_urls_json, facts_json, inference_json, overlap_json,
                limitations_json, created_at
            ) VALUES(?, 1, ?, ?, 'informational', 'broad collection',
                     'review', 'needs_evidence', '["external:1:test"]',
                     '[]', '[]', '[]', '{}', '[]', ?)
            """,
            (
                int(cursor.lastrowid),
                "Frequently asked questions for photographers about laser pointer light painting",
                "frequently asked questions for photographers about laser pointer light painting",
                now,
            ),
        )

    app = create_app(settings)
    with TestClient(app) as client:
        dashboard = client.get("/")
        opportunities = client.get("/opportunities")
        research = client.get("/research")
        actions = client.get("/actions")

    assert "数据导入" in dashboard.text
    assert "更新旧文章" in opportunities.text
    assert "制作新文章" in opportunities.text
    assert "先看现有素材，再决定要不要手工补充" in actions.text
    topic = "Frequently asked questions for photographers about laser pointer light painting"
    assert topic not in research.text
    assert topic not in opportunities.text
    assert "具体文章主题只在第 3 步" in research.text


def test_research_new_article_decision_accepts_list_facts(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    analysis = run_analysis(1, settings)
    with connection(settings) as conn:
        cursor = conn.execute(
            """
            INSERT INTO opportunities(
                analysis_run_id, site_id, rule_key, opportunity_type, target_kind,
                target_ref, title, recommended_action, gate_status, gate_reasons_json,
                evidence_json, strength, confidence, confidence_weight, effort,
                priority, method_version, status, created_at
            ) VALUES(
                ?, 1, 'research_topic_candidate', 'create', 'topic', ?, ?,
                'Create a focused new article', 'passed', '["operator confirmed"]',
                ?, 50, 'medium', 0.7, 4, 8.75,
                'topic-research-0.5.0', 'proposed', '2026-07-15T00:00:00+00:00'
            )
            """,
            (
                analysis.run_id,
                "construction site laser pointer problems",
                "Common Laser Pointer Problems on Construction Sites",
                json.dumps(
                    {
                        "evidence_ids": ["external:test"],
                        "facts": ["operator-confirmed research fact"],
                    }
                ),
            ),
        )
        opportunity_id = int(cursor.lastrowid)

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            f"/opportunities/{opportunity_id}/decision",
            data={"decision": "accepted"},
            headers={"Origin": "http://127.0.0.1:8787"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/actions")
    with connection(settings) as conn:
        action = conn.execute(
            "SELECT id, baseline_json FROM actions WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        step_count = conn.execute(
            "SELECT COUNT(*) FROM action_steps WHERE action_id = ?", (action["id"],)
        ).fetchone()[0]
    assert json.loads(action["baseline_json"])["metrics"] == {}
    assert step_count == 6
