from __future__ import annotations

import json

from fastapi.testclient import TestClient

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.services.topic_graph import sync_topic_graph
from seo_ops.services.workflow_reset import reset_workflow_state, workflow_reset_preview
from seo_ops.web.app import create_app
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def _prepare_internal_data(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    return run_analysis(1, settings)


def _insert_research_candidate(settings, analysis_run_id: int, topic: str) -> int:
    now = "2026-07-16T00:00:00+00:00"
    with connection(settings) as conn:
        parent = conn.execute(
            "SELECT id FROM topic_nodes WHERE site_id = 1 AND topic_key = 'use-professional'"
        ).fetchone()
        cursor = conn.execute(
            """
            INSERT INTO research_runs(
                site_id, analysis_run_id, status, budgets_json, usage_json,
                seed_queries_json, candidate_count, ai_model, started_at,
                completed_at, seed_type, topic_id, filters_json
            ) VALUES(1, ?, 'success', '{"serpapi": 2}', '{"serpapi": {"actual_requests": 1, "successful": 1, "failed": 0, "reused": 0, "budget": 2}}',
                     '["construction laser questions"]', 1, 'deepseek-v4-flash',
                     ?, ?, 'topic_gap', ?, '{}')
            """,
            (analysis_run_id, now, now, int(parent["id"]) if parent else None),
        )
        run_id = int(cursor.lastrowid)
        cursor = conn.execute(
            """
            INSERT INTO research_candidates(
                research_run_id, site_id, topic, normalized_topic, intent,
                rationale, recommended_next_step, gate_status, evidence_refs_json,
                source_urls_json, facts_json, inference_json, overlap_json,
                limitations_json, created_at, suggested_parent_topic_id
            ) VALUES(?, 1, ?, ?, 'problem solving',
                     'Users need a focused answer for one construction task.',
                     'Review and write', 'needs_evidence', '["external:1:serp_snapshot"]',
                     '["https://example.org/source"]',
                     '["external:1:serp_snapshot supports this question"]',
                     '["A focused article may help"]', '{"max_score": 0.2}', '[]', ?, ?)
            """,
            (
                run_id,
                topic,
                topic.casefold(),
                now,
                int(parent["id"]) if parent else None,
            ),
        )
        return int(cursor.lastrowid)


def test_five_step_navigation_and_one_card_one_home(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "How to use a laser pointer for construction alignment checks"
    candidate_id = _insert_research_candidate(settings, analysis.run_id, topic)
    app = create_app(settings)

    with TestClient(app) as client:
        start = client.get("/")
        research = client.get("/research")
        suggestions = client.get("/opportunities")
        workflow_css = client.get("/static/workflow.css")
        before_external = None
        with connection(settings) as conn:
            before_external = conn.execute("SELECT COUNT(*) FROM external_runs").fetchone()[0]
        client.get("/opportunities")
        with connection(settings) as conn:
            after_external = conn.execute("SELECT COUNT(*) FROM external_runs").fetchone()[0]

    assert start.url.path == "/imports"
    ordered = [
        start.text.index("数据导入"),
        start.text.index("主题调研"),
        start.text.index("文章建议"),
        start.text.index("文章制作"),
        start.text.index("主题图谱"),
    ]
    assert ordered == sorted(ordered)
    assert workflow_css.status_code == 200
    assert ".workflow-sidebar .nav-item .nav-copy" in workflow_css.text
    assert "width: auto" in workflow_css.text
    assert "white-space: nowrap" in workflow_css.text
    assert topic not in research.text
    assert "保存了 1 个主题线索" in research.text
    assert "最近一次" in research.text
    assert "SerpAPI" in research.text
    assert "成功 1 · 复用 0 · 失败 0" in research.text
    assert topic in suggestions.text
    assert "更新旧文章" in suggestions.text
    assert "制作新文章" in suggestions.text
    assert suggestions.text.count("开始执行") >= 1
    assert "暂时跳过" in suggestions.text
    assert "不再推荐" in suggestions.text
    assert before_external == after_external

    with TestClient(app) as client:
        moved = client.post(
            f"/research/candidates/{candidate_id}/decision",
            data={"decision": "execute"},
            headers={"Origin": "http://127.0.0.1:8787"},
            follow_redirects=False,
        )
        page_three = client.get("/opportunities")
        page_four = client.get("/actions")

    assert moved.status_code == 303
    assert moved.headers["location"].startswith("/actions")
    assert topic not in page_three.text
    assert topic in page_four.text
    with connection(settings) as conn:
        action = conn.execute(
            """
            SELECT a.id FROM actions a
            JOIN opportunities o ON o.id = a.opportunity_id
            WHERE o.target_ref = ?
            """,
            (topic,),
        ).fetchone()
        candidate_nodes = conn.execute(
            "SELECT COUNT(*) FROM topic_nodes WHERE site_id = 1 AND node_type = 'candidate'"
        ).fetchone()[0]
    assert action is not None
    assert candidate_nodes == 0


def test_published_task_leaves_production_and_graph_waits_for_cms(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "Laser pointer setup checklist for indoor construction alignment"
    candidate_id = _insert_research_candidate(settings, analysis.run_id, topic)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post(
            f"/research/candidates/{candidate_id}/decision",
            data={"decision": "execute"},
        )
    with connection(settings) as conn:
        action_id = int(
            conn.execute(
                """
                SELECT a.id FROM actions a
                JOIN opportunities o ON o.id = a.opportunity_id
                WHERE o.target_ref = ?
                """,
                (topic,),
            ).fetchone()["id"]
        )
        deliverable = {
            "title": topic,
            "slug": "construction-alignment-checklist",
            "summary": "Focused summary",
            "content": "## Checklist\n\nUseful content.",
            "tags": "construction, laser pointer",
            "seo_title": topic,
            "seo_description": "Focused description",
            "seo_keywords": "laser pointer construction alignment",
            "reason": "Focused new article",
        }
        conn.execute(
            """
            UPDATE actions SET actual_change = ?, workflow_status = 'in_progress'
            WHERE id = ?
            """,
            (json.dumps(deliverable), action_id),
        )

    with TestClient(app) as client:
        published = client.post(
            f"/actions/{action_id}/published",
            data={"published": "1"},
        )
    assert topic not in published.text
    sync_topic_graph(1, settings)
    with connection(settings) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM topic_nodes WHERE preferred_name = ?", (topic,)
            ).fetchone()[0]
            == 0
        )

    payload = json.loads(blog_export_bytes())
    payload["items"].append(
        {
            "id": "b-new",
            "title": topic,
            "slug": "construction-alignment-checklist",
            "summary": "Focused summary",
            "content": "## Checklist\n\nUseful content.",
            "tags": ["construction"],
            "seoTitle": topic,
            "seoDescription": "Focused description",
            "seoKeywords": ["laser pointer construction alignment"],
            "active": True,
            "createdAt": "2026-07-16T00:00:00Z",
            "updatedAt": "2026-07-16T00:00:00Z",
        }
    )
    payload["count"] = len(payload["items"])
    import_cms_bytes(1, "blogs-after-publish.json", json.dumps(payload).encode(), settings)
    sync_topic_graph(1, settings)
    with connection(settings) as conn:
        node = conn.execute(
            """
            SELECT node_type, source_type FROM topic_nodes
            WHERE preferred_name = ?
            """,
            (topic,),
        ).fetchone()
    assert node["node_type"] == "article_topic"
    assert node["source_type"] == "import"


def test_reset_clears_only_workflow_traces(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "Focused construction laser pointer question"
    _insert_research_candidate(settings, analysis.run_id, topic)
    snapshot = settings.snapshots_dir / "derived-test.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text("{}", encoding="utf-8")
    with connection(settings) as conn:
        now = "2026-07-16T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO external_runs(
                site_id, provider, purpose, request_sha256, input_refs_json,
                parameters_json, status, response_snapshot_path, usage_json,
                started_at, completed_at
            ) VALUES(1, 'test', 'topic_research', 'hash', '[]', '{}', 'success',
                     ?, '{}', ?, ?)
            """,
            (str(snapshot), now, now),
        )
        conn.execute(
            """
            INSERT INTO topic_decisions(
                site_id, normalized_topic, decision, created_at, updated_at
            ) VALUES(1, 'keep-this-rejection', 'dont_recommend', ?, ?)
            """,
            (now, now),
        )
        run = conn.execute(
            "SELECT id, topic_id FROM research_runs WHERE site_id = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO topic_research_memory(
                site_id, topic_id, seed_type, filters_json, query_text,
                normalized_query, result_status, result_summary,
                research_run_id, created_at
            ) VALUES(1, ?, 'topic_gap', '{}', ?, ?, 'found', 'tested branch', ?, ?)
            """,
            (
                run["topic_id"],
                "laser pointer presentations classrooms common questions",
                "laser pointer presentations classrooms common questions",
                run["id"],
                now,
            ),
        )

    preview = workflow_reset_preview(1, settings)
    assert preview["preserve"]["imports"] == 3
    assert preview["preserve"]["content_items"] == 3
    assert preview["preserve"]["research_memory"] == 1
    result = reset_workflow_state(1, settings)
    assert result.preserved_imports == 3
    assert result.preserved_content_items == 3
    assert not snapshot.exists()

    with connection(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM external_runs").fetchone()[0] == 0
        memory = conn.execute(
            "SELECT topic_id, research_run_id FROM topic_research_memory"
        ).fetchone()
        decision = conn.execute(
            "SELECT decision FROM topic_decisions WHERE normalized_topic = 'keep-this-rejection'"
        ).fetchone()
    assert memory["topic_id"] is not None
    assert memory["research_run_id"] is None
    assert decision["decision"] == "dont_recommend"
