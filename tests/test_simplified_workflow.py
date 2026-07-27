from __future__ import annotations

import json

from fastapi.testclient import TestClient

from seo_ops.db import connection, init_db
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.rules.research_workflow import (
    QUALIFICATION_RULE_VERSION,
    compute_cms_fingerprint,
    compute_evidence_fingerprint,
)
from seo_ops.services.article_suggestions import (
    _candidate_discovery_stage,
    _candidate_priority,
    _feedback_priority_adjustment,
    list_article_suggestions,
)
from seo_ops.services.research_workflow import _content_search_items
from seo_ops.services.topic_graph import sync_topic_graph
from seo_ops.services.workflow_reset import reset_workflow_state, workflow_reset_preview
from seo_ops.web.app import create_app
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def test_demand_backed_synthesized_angle_ranks_above_evidence_heavy_raw_lead():
    raw_lead = {
        "rationale": "The current SERP exposed this related search",
        "evidence_refs": [f"external:{index}" for index in range(6)],
        "facts": [f"fact {index}" for index in range(5)],
        "source_urls": [f"https://source{index}.example" for index in range(5)],
        "overlap": {"max_score": 0.0},
        "closest_existing": {"relationship": "adjacent"},
        "evidence_demand": {"ok": False},
        "evidence_material": {"ok": True},
    }
    synthesized = {
        "rationale": "Multiple sources support a focused seller-liability angle.",
        "evidence_refs": [f"external:{index}" for index in range(4)],
        "facts": [f"fact {index}" for index in range(4)],
        "source_urls": [f"https://source{index}.example" for index in range(2)],
        "overlap": {"max_score": 0.1},
        "closest_existing": {"relationship": "adjacent"},
        "evidence_demand": {"ok": True},
        "evidence_material": {"ok": True},
    }

    assert _candidate_discovery_stage(raw_lead) == "raw_lead"
    assert _candidate_discovery_stage(synthesized) == "synthesized_angle"
    assert _candidate_priority(synthesized) > _candidate_priority(raw_lead)


def test_seed_drift_and_old_plan_feedback_are_soft_priority_signals():
    anchored = {
        "rationale": "A focused construction-use angle.",
        "evidence_refs": ["external:1:serp_snapshot"],
        "facts": ["external:1:serp_snapshot contains a specific question"],
        "source_urls": [],
        "overlap": {"max_score": 0.0},
        "closest_existing": {"relationship": "distinct"},
        "evidence_demand": {"ok": True},
        "evidence_material": {"ok": False},
        "limitations": [],
    }
    drifted = {
        **anchored,
        "limitations": ["种子锚点诊断（不阻断）：候选明确换成了相邻激光产品对象"],
    }
    assert _candidate_priority(drifted) < _candidate_priority(anchored)

    cross_branch = {
        **anchored,
        "limitations": ["种子分支诊断（不阻断）：只要激光笔仍是核心对象，不因跨分支自动降权"],
    }
    adjacent_task = {
        **anchored,
        "limitations": ["AI 种子锚点判断（不阻断）：候选跨到相邻任务；保留且不因跨分支自动降权"],
    }
    assert _candidate_priority(cross_branch) == _candidate_priority(anchored)
    assert _candidate_priority(adjacent_task) == _candidate_priority(anchored)

    topic = "Laser pointer battery runtime for construction crews"
    preferred = [
        {"normalized_topic": "laser pointer battery charging and runtime", "decision": "do"}
    ]
    rejected = [
        {"normalized_topic": "laser pointer battery runtime failures", "decision": "dont_recommend"}
    ]
    assert _feedback_priority_adjustment(topic, preferred) > 0
    assert _feedback_priority_adjustment(topic, rejected) < 0


def _prepare_internal_data(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    return run_analysis(1, settings)


def _insert_research_candidate(settings, analysis_run_id: int, topic: str) -> int:
    now = "2026-07-16T00:00:00+00:00"
    cms_fingerprint = compute_cms_fingerprint(_content_search_items(settings, 1))
    evidence_fingerprint = compute_evidence_fingerprint(
        ["external:1:serp_snapshot", "external:2:page_capture"],
        ["https://example.org/source", "https://docs.example.net/page"],
    )
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
                limitations_json, created_at, suggested_parent_topic_id,
                qualification_status, qualification_version, cms_fingerprint,
                evidence_fingerprint, closest_existing_json, evidence_demand_json,
                evidence_gap_json, evidence_material_json, recommended_disposition,
                qualified_at
            ) VALUES(?, 1, ?, ?, 'problem solving',
                     'Users need a focused answer for one construction task.',
                     'Review and write', 'needs_evidence',
                     '["external:1:serp_snapshot", "external:2:page_capture"]',
                     '["https://example.org/source", "https://docs.example.net/page"]',
                     '["external:1:serp_snapshot supports this question"]',
                     '["A focused article may help"]', '{"max_score": 0.2}', '[]', ?, ?,
                     'qualified', ?, ?, ?,
                     '{"relationship": "distinct"}', '{"ok": true}',
                     '{"ok": true}', '{"ok": true}', 'new_article', ?)
            """,
            (
                run_id,
                topic,
                topic.casefold(),
                now,
                int(parent["id"]) if parent else None,
                QUALIFICATION_RULE_VERSION,
                cms_fingerprint,
                evidence_fingerprint,
                now,
            ),
        )
        return int(cursor.lastrowid)


def test_raw_search_lead_stays_in_the_research_pool_not_article_choices(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "What are the five generic parameters of a laser?"
    candidate_id = _insert_research_candidate(settings, analysis.run_id, topic)
    with connection(settings) as conn:
        research_run_id = conn.execute(
            "SELECT research_run_id FROM research_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE research_candidates SET rationale = ? WHERE id = ?",
            ("The current SERP exposed this related question", candidate_id),
        )
        conn.execute(
            """
            INSERT INTO research_seed_observations(
                site_id, research_run_id, source_kind, source_url, source_title,
                observed_text, source_quote, evidence_ref, task_card_json,
                follow_up_query, normalized_query, provisional_topic,
                semantic_cluster, anchor_fit, created_at
            ) VALUES(1, ?, 'paa_related', 'https://example.org/generic-parameters',
                     'Generic parameters question', 'A public question about laser parameters.',
                     'What are the five generic parameters of a laser?', 'external:1:serp_snapshot',
                     '{}', 'laser pointer generic parameters', 'laser pointer generic parameters',
                     ?, 'beam_optics', 'core', '2026-07-16T00:00:00+00:00')
            """,
            (research_run_id, topic),
        )

    suggestions = list_article_suggestions(1, settings)

    assert suggestions["new"]["total"] == 0
    assert "leads" not in suggestions
    with connection(settings) as conn:
        observation = conn.execute(
            """
            SELECT status, follow_up_query FROM research_seed_observations
            WHERE research_run_id = ?
            """,
            (research_run_id,),
        ).fetchone()
        candidate = conn.execute(
            "SELECT decision FROM research_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
    assert observation["status"] == "observed"
    assert observation["follow_up_query"] == "laser pointer generic parameters"
    assert candidate["decision"] == "pending"


def test_legacy_review_state_is_requalified_into_a_final_lane(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "How to use a laser pointer for construction alignment checks"
    candidate_id = _insert_research_candidate(settings, analysis.run_id, topic)
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE research_candidates
            SET qualification_status = 'needs_human_review',
                recommended_disposition = NULL,
                qualification_version = ?
            WHERE id = ?
            """,
            (QUALIFICATION_RULE_VERSION, candidate_id),
        )

    suggestions = list_article_suggestions(1, settings)

    assert suggestions["new"]["total"] == 1
    assert suggestions["new"]["featured"][0]["topic"] == topic
    with connection(settings) as conn:
        candidate = conn.execute(
            """
            SELECT qualification_status, recommended_disposition
            FROM research_candidates WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
    assert candidate["qualification_status"] == "qualified"
    assert candidate["recommended_disposition"] == "new_article"


def test_legacy_evidence_state_routes_same_intent_to_the_old_article_lane(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    candidate_id = _insert_research_candidate(settings, analysis.run_id, "Example Laser Guide")
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE research_candidates
            SET qualification_status = 'needs_evidence',
                recommended_disposition = NULL,
                qualification_version = ?
            WHERE id = ?
            """,
            (QUALIFICATION_RULE_VERSION, candidate_id),
        )

    suggestions = list_article_suggestions(1, settings)

    with connection(settings) as conn:
        candidate = conn.execute(
            """
            SELECT qualification_status, recommended_disposition
            FROM research_candidates WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
    assert candidate["qualification_status"] == "blocked"
    assert candidate["recommended_disposition"] == "update_existing"
    assert any(
        item["content_title"] == "Example Laser Guide" for item in suggestions["old"]["featured"]
    )


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
    assert "所有待选均在此列出" in suggestions.text
    assert "累计待选 1（本轮新增 1）" in suggestions.text
    assert "原始搜索线索" not in suggestions.text
    assert "待人工核对的灰色主题" not in suggestions.text
    assert "已归入旧文章判断" not in suggestions.text
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


def test_startup_cancels_unstarted_work_from_a_stale_candidate(settings):
    analysis = _prepare_internal_data(settings)
    sync_topic_graph(1, settings)
    topic = "Laser pointer mounting stability for construction alignment"
    candidate_id = _insert_research_candidate(settings, analysis.run_id, topic)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            f"/research/candidates/{candidate_id}/decision",
            data={"decision": "execute"},
            follow_redirects=False,
        )
    assert response.headers["location"].startswith("/actions")

    with connection(settings) as conn:
        conn.execute(
            "UPDATE research_candidates SET qualification_status = 'stale' WHERE id = ?",
            (candidate_id,),
        )

    init_db(settings)

    with connection(settings) as conn:
        row = conn.execute(
            """
            SELECT o.status AS opportunity_status, a.workflow_status
            FROM opportunities o
            JOIN actions a ON a.opportunity_id = o.id
            WHERE o.target_ref = ?
            """,
            (topic,),
        ).fetchone()
    assert row["opportunity_status"] == "cancelled"
    assert row["workflow_status"] == "cancelled"


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
