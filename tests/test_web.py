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


def test_two_stage_pages_are_simple_and_generic_collection_is_retained(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    analysis = run_analysis(1, settings)
    now = "2026-07-15T00:00:00+00:00"
    usage = {
        "serpapi": {"actual_requests": 2, "budget": 2, "successful": 2, "reused": 0, "failed": 0},
        "firecrawl": {"actual_requests": 3, "budget": 3, "successful": 3, "reused": 0, "failed": 0},
        "tavily": {"actual_requests": 17, "budget": 17, "successful": 17, "reused": 0, "failed": 0},
        "ai": {"actual_requests": 4, "budget": 8, "successful": 4, "reused": 0, "failed": 0},
    }
    filters = {
        "open_scheduler": {
            "active": True,
            "family_count": 12,
            "initial_observation_count": 58,
            "seed_proposal_count": 15,
            "cms_duplicate_seed_count": 1,
            "visible_distinct_seed_count": 14,
            "selected_seed_count": 5,
            "cms_duplicate_seed_audit": [
                {
                    "topic": "How to Charge a Laser Pointer Battery",
                    "reason": "程序按当前 CMS 主主题和正文覆盖预筛为强重叠，未送去深挖。",
                    "max_score": 0.9,
                    "matches": [
                        {
                            "title": "Laser Pointer Battery Charging Guide",
                            "canonical_url": "https://example.com/blog/battery-charging",
                            "matched_tokens": ["battery", "charge"],
                        }
                    ],
                }
            ],
            "intent_clustering": {
                "merged_current_variants": 3,
                "prior_pool_duplicates": 0,
                "current_variant_merges": [
                    {
                        "retained_topic": "Why a Laser Pointer Beam Fades Outdoors",
                        "merged_topics": ["Why a Laser Beam Seems to Disappear Outside"],
                        "reason": "Both answer the same outdoor visibility question.",
                    }
                ],
                "prior_pool_duplicate_audit": [],
            },
        },
        "topic_pool": {"formed_independent_topics": 10, "raw_leads": 4},
        "source_observations": {"stored": 24, "selected_from_prior_runs": 0},
        "seed_frontier": [
            {
                "query": "laser pointer beam disappears outdoors",
                "source_type": "open_scheduler_source_seed",
                "source_family": "contexts",
                "semantic_cluster": "outdoor_visibility",
                "anchor_fit": "core",
            }
        ],
        "candidate_resolution_audit": [
            {
                "topic": "How to Charge a Laser Pointer Battery",
                "reason": "最终资格判断为与现有内容同主意图或正文已覆盖，已路由为旧文更新线索，而非新文章主题。",
                "kind": "program_inference",
                "related_topic": "Laser Pointer Battery Charging Guide",
            }
        ],
    }
    with connection(settings) as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_runs(
                site_id, analysis_run_id, status, budgets_json, usage_json,
                seed_queries_json, candidate_count, ai_model, started_at,
                completed_at, seed_type, filters_json
            ) VALUES(1, ?, 'success', '{}', ?, '[]', 1, 'deepseek-v4-flash',
                     ?, ?, 'topic_gap', ?)
            """,
            (analysis.run_id, json.dumps(usage), now, now, json.dumps(filters)),
        )
        research_run_id = int(cursor.lastrowid)
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
                research_run_id,
                "Frequently asked questions for photographers about laser pointer light painting",
                "frequently asked questions for photographers about laser pointer light painting",
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO research_seed_observations(
                site_id, research_run_id, source_kind, source_url, source_title,
                observed_text, source_quote, evidence_ref, task_card_json,
                follow_up_query, normalized_query, provisional_topic,
                semantic_cluster, anchor_fit, created_at
            ) VALUES(1, ?, 'forum_community', 'https://forum.example/outdoor-beam',
                     'Forum owner report', 'Owners describe a beam that seems to fade outdoors.',
                     'A beam that seems to fade outdoors.', 'external:1:test', '{}',
                     'laser pointer beam disappears outdoors',
                     'laser pointer beam disappears outdoors',
                     'Outdoor beam visibility question', 'outdoor_visibility', 'core', ?)
            """,
            (research_run_id, now),
        )
        conn.execute(
            """
            INSERT INTO research_runs(
                site_id, analysis_run_id, status, budgets_json, usage_json,
                seed_queries_json, candidate_count, ai_model, started_at,
                completed_at, seed_type, filters_json
            ) VALUES(1, ?, 'success', '{}', '{}', '[]', 2, NULL,
                     '2026-07-14T00:00:00+00:00', '2026-07-14T00:00:00+00:00', 'boundary', '{}')
            """,
            (analysis.run_id,),
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
    assert "所有任务先核对现有素材，再决定要不要手工补充" in actions.text
    topic = "Frequently asked questions for photographers about laser pointer light painting"
    assert topic not in research.text
    assert topic in opportunities.text
    assert "通过明显重复拦截" in opportunities.text
    assert "累计待选 1（本轮新增 1）" in opportunities.text
    assert "原始搜索线索" not in opportunities.text
    assert "待人工核对的灰色主题" not in opportunities.text
    assert "已归入旧文章判断" not in opportunities.text
    assert "本轮执行汇报" in research.text
    assert "12 类来源 → 58 条来源观察 → 15 个候选种子" in research.text
    assert "本轮状态正常：没有记录到调用失败。" in research.text
    assert "本轮过程账" in research.text
    assert "调度起点、冷却与实际深挖种子" in research.text
    assert "完整流程共 8 步" not in research.text
    assert "Forum owner report" in research.text
    assert "How to Charge a Laser Pointer Battery" in research.text
    assert "Why a Laser Pointer Beam Fades Outdoors" in research.text
    assert "查看以前的调研概况" not in research.text
    assert "查看文章建议" not in research.text


def test_research_page_reports_partial_provider_failure(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO research_runs(
                site_id, status, budgets_json, usage_json, seed_queries_json,
                candidate_count, started_at, completed_at, seed_type, filters_json,
                error_message
            ) VALUES(1, 'partial', '{}', ?, '[]', 2, ?, ?, 'boundary', ?, ?)
            """,
            (
                json.dumps(
                    {
                        "serpapi": {
                            "actual_requests": 2,
                            "budget": 2,
                            "successful": 2,
                            "reused": 0,
                            "failed": 0,
                        },
                        "firecrawl": {
                            "actual_requests": 3,
                            "budget": 3,
                            "successful": 2,
                            "reused": 0,
                            "failed": 1,
                        },
                        "tavily": {
                            "actual_requests": 17,
                            "budget": 17,
                            "successful": 17,
                            "reused": 0,
                            "failed": 0,
                        },
                        "ai": {
                            "actual_requests": 4,
                            "budget": 8,
                            "successful": 4,
                            "reused": 0,
                            "failed": 0,
                        },
                    }
                ),
                "2026-07-16T00:00:00+00:00",
                "2026-07-16T00:10:00+00:00",
                json.dumps({"open_scheduler": {"active": True, "family_count": 12}}),
                "部分供应商失败或没有形成候选",
            ),
        )

    app = create_app(settings)
    with TestClient(app) as client:
        research = client.get("/research")

    assert research.status_code == 200
    assert (
        "本轮问题：Firecrawl 失败 1 次；系统记录：部分供应商失败或没有形成候选。已成功保存的结果仍可用。"
        in research.text
    )


def test_research_page_marks_legacy_partial_audit_as_unknown(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO research_runs(
                site_id, status, budgets_json, usage_json, seed_queries_json,
                candidate_count, started_at, completed_at, seed_type, filters_json
            ) VALUES(1, 'success', '{}', ?, '[]', 8, ?, ?, 'topic_gap', '{}')
            """,
            (
                json.dumps(
                    {
                        "serpapi": {
                            "actual_requests": 2,
                            "budget": 2,
                            "successful": 2,
                            "reused": 0,
                            "failed": 0,
                        }
                    }
                ),
                "2026-07-16T00:00:00+00:00",
                "2026-07-16T00:10:00+00:00",
            ),
        )

    app = create_app(settings)
    with TestClient(app) as client:
        research = client.get("/research")

    assert research.status_code == 200
    assert "调用层面没有记录失败；但这是一条旧记录" in research.text
    assert "本轮状态正常：没有记录到调用失败。" not in research.text


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
