from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import list_actions, list_opportunities
from seo_ops.services.action_workflow import (
    ActionWorkflowError,
    record_opportunity_decision,
    set_action_cancelled,
    set_action_published,
    update_action_step,
)
from seo_ops.services.ai import AIResponse
from seo_ops.services.content_production import (
    ContentProductionError,
    _external_material_record,
    _normalize_existing_deliverable,
    _source_role,
    generate_content_deliverable,
)
from seo_ops.services.material_workflow import build_material_preview, save_manual_material
from seo_ops.web.app import create_app
from tests.helpers import blog_export_bytes, product_export_bytes, workbook_bytes


def _prepare(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)
    import_gsc_bytes(1, "gsc.xlsx", workbook_bytes(comparison=True), settings)
    run_analysis(1, settings)
    with connection(settings) as conn:
        return next(
            item
            for item in list_opportunities(conn, 1)
            if item["rule_key"] == "query_page_evidence_gap"
        )


def _passed_opportunity(settings):
    _prepare(settings)
    current_start = "2026-07-01"
    current_end = "2026-07-28"
    previous_start = "2026-06-03"
    previous_end = "2026-06-30"
    page = "https://laserpointerhub.com/blog/example"
    with connection(settings) as conn:
        imported = conn.execute(
            """
            SELECT id, metadata_json FROM imports
            WHERE site_id = 1 AND source_type = 'gsc' AND analysis_active = 1
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        metadata = json.loads(imported["metadata_json"])
        metadata.update(
            {
                "current_start_date": current_start,
                "current_end_date": current_end,
                "previous_start_date": previous_start,
                "previous_end_date": previous_end,
            }
        )
        conn.execute(
            "UPDATE imports SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata), imported["id"]),
        )
        conn.executemany(
            """
            INSERT INTO gsc_query_page_metrics(
                import_id, site_id, data_date, query, page, search_type,
                clicks, impressions, ctr, position, source_row
            ) VALUES(?, 1, ?, 'best example laser', ?, 'web', ?, ?, ?, ?, ?)
            """,
            [
                (
                    imported["id"],
                    "2026-06-30",
                    page,
                    10,
                    240,
                    0.0417,
                    8,
                    1,
                ),
                (
                    imported["id"],
                    "2026-07-28",
                    page,
                    2,
                    120,
                    0.0167,
                    12,
                    2,
                ),
            ],
        )
    run_analysis(1, settings)
    with connection(settings) as conn:
        return next(
            item for item in list_opportunities(conn, 1) if item["opportunity_type"] == "protect"
        )


def _confirm_old_article_materials(action_id, settings) -> None:
    save_manual_material(
        action_id,
        "\n".join(
            (
                "A User pain point: Readers need a clear answer before changing an existing laser guide.",
                "https://www.reddit.com/r/lasers/comments/example/reader_question",
                "E Authoritative source: Use the official laser-product safety guidance for factual boundaries.",
                "https://www.fda.gov/radiation-emitting-products/laser-products-and-instruments",
                "G Real search question: Why did my example laser guide lose clicks?",
                "https://www.reddit.com/r/lasers/comments/example/reader_question",
            )
        ),
        settings,
    )


def test_evidence_signal_cannot_enter_execution(settings):
    opportunity = _prepare(settings)
    with pytest.raises(ActionWorkflowError, match="分析线索"):
        record_opportunity_decision(opportunity["id"], "accepted", settings=settings)
    with connection(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


def test_action_can_be_cancelled_resumed_and_rendered(settings):
    opportunity = _passed_opportunity(settings)
    outcome = record_opportunity_decision(opportunity["id"], "accepted", settings=settings)
    message = set_action_cancelled(outcome.action_id, cancelled=True, settings=settings)
    assert "已取消" in message

    app = create_app(settings)
    with TestClient(app) as client:
        current_page = client.get("/actions")
        history_page = client.get("/actions?history=1")
    assert "观察与历史" not in current_page.text
    assert opportunity["title"] not in current_page.text
    assert opportunity["title"] not in history_page.text
    with pytest.raises(ActionWorkflowError, match="已取消"):
        with connection(settings) as conn:
            first_step = conn.execute(
                "SELECT id FROM action_steps WHERE action_id = ? ORDER BY step_order LIMIT 1",
                (outcome.action_id,),
            ).fetchone()["id"]
        update_action_step(outcome.action_id, first_step, completed=True, settings=settings)
    assert "已恢复" in set_action_cancelled(outcome.action_id, cancelled=False, settings=settings)

    with TestClient(app) as client:
        page = client.get("/actions")
    assert page.status_code == 200
    assert "文章制作" in page.text
    assert "旧文章保留 Slug 和主要意图" in page.text
    assert "所有任务先核对现有素材" in page.text


def test_page_execution_and_publish_timestamps_follow_actual_steps(settings):
    opportunity = _passed_opportunity(settings)
    outcome = record_opportunity_decision(opportunity["id"], "accepted", settings=settings)
    with connection(settings) as conn:
        action = next(item for item in list_actions(conn, 1) if item["id"] == outcome.action_id)

    for step in action["steps"]:
        update_action_step(action["id"], step["id"], completed=True, settings=settings)
        if step["step_key"] == "apply_cms_change":
            break
    with connection(settings) as conn:
        executing = next(item for item in list_actions(conn, 1) if item["id"] == action["id"])
    assert executing["workflow_status"] == "in_progress"
    assert executing["executed_at"] is not None
    assert executing["published_at"] is None
    assert executing["completed_at"] is None

    publish_step = next(step for step in action["steps"] if step["step_key"] == "qa_and_publish")
    update_action_step(action["id"], publish_step["id"], completed=True, settings=settings)
    with connection(settings) as conn:
        published = next(item for item in list_actions(conn, 1) if item["id"] == action["id"])
    assert published["workflow_status"] == "in_progress"
    assert published["published_at"] is not None
    assert published["completed_at"] is None


class FakeContentAI:
    def __init__(self):
        self.calls: list[str] = []

    async def complete_json(self, system_prompt, user_payload):
        self.calls.append(str(user_payload.get("stage") or "existing_draft"))
        candidates = user_payload["available_internal_links"]
        valid_link = candidates[0]["canonical_url"] if candidates else ""
        sentence = (
            "This focused replacement explains the original topic with clear steps, practical "
            "context, and careful limits for readers."
        )
        section = f"{sentence} {sentence} {sentence}\n\n{sentence} {sentence} {sentence} {sentence}"
        return AIResponse(
            provider="fake",
            model="deepseek-v4-flash",
            prompt_sha256="c" * 64,
            content={
                "change_type": "partial_update",
                "reason": "Keep the same intent and improve the weak section.",
                "title": "Updated Example Laser Guide",
                "slug": "do-not-change-this-slug",
                "summary": "A clearer summary.",
                "content": f"## What changed\n\n{section}",
                "tags": "laser pointer, guide",
                "seo_title": "Updated Example Laser Guide",
                "seo_description": "A concise, page-specific description for the existing guide.",
                "seo_keywords": "laser pointer guide",
                "internal_links": [
                    item
                    for item in (
                        (
                            [{"url": valid_link, "anchor": "example laser guide"}]
                            if valid_link
                            else []
                        )
                        + [{"url": "https://invented.example/page", "anchor": "fake"}]
                    )
                ],
                "external_sources": [
                    {"url": "https://invented.example/source", "supports": "invented fact"}
                ],
                "self_review": ["Checked the requested scope"],
                "operator_note": "Replace only the indicated section.",
                "quality_gate": {"status": "pass", "issues": []},
            },
        )


class ChineseContentAI(FakeContentAI):
    async def complete_json(self, system_prompt, user_payload):
        response = await super().complete_json(system_prompt, user_payload)
        response.content["summary"] = "这是一段不应进入英文网站的中文摘要。"
        return response


def test_content_generation_locks_slug_and_filters_invented_links(settings):
    opportunity = _passed_opportunity(settings)
    outcome = record_opportunity_decision(opportunity["id"], "accepted", settings=settings)
    provider = FakeContentAI()
    with pytest.raises(ContentProductionError, match="素材还缺少"):
        asyncio.run(generate_content_deliverable(outcome.action_id, settings, provider=provider))
    assert provider.calls == []
    _confirm_old_article_materials(outcome.action_id, settings)
    result = asyncio.run(
        generate_content_deliverable(
            outcome.action_id,
            settings,
            provider=provider,
            confirm_materials=True,
        )
    )

    with connection(settings) as conn:
        page = conn.execute(
            "SELECT slug FROM content_items WHERE canonical_url = ?",
            (opportunity["target_ref"],),
        ).fetchone()
        stored = list_actions(conn, 1)[0]["deliverable"]

    assert result["slug"] == page["slug"]
    assert stored["slug"] == page["slug"]
    assert result["internal_links"] == []
    assert result["external_sources"] == []
    assert "Slug 已按任务类型校验" in result["self_review"]
    assert result["quality_report"]["status"] == "pass"
    assert provider.calls == ["existing_draft", "existing_review"]


def test_old_article_generation_rechecks_joint_evidence_before_ai(settings):
    _prepare(settings)
    with connection(settings) as conn:
        opportunity = next(
            item for item in list_opportunities(conn, 1) if item["opportunity_type"] == "protect"
        )
        conn.execute(
            "UPDATE opportunities SET gate_status = 'passed' WHERE id = ?",
            (opportunity["id"],),
        )
    outcome = record_opportunity_decision(
        opportunity["id"],
        "accepted",
        settings=settings,
    )
    provider = FakeContentAI()
    _confirm_old_article_materials(outcome.action_id, settings)

    with pytest.raises(ContentProductionError, match="缺少查询—页面联合证据"):
        asyncio.run(
            generate_content_deliverable(
                outcome.action_id,
                settings,
                provider=provider,
                confirm_materials=True,
            )
        )

    assert provider.calls == []


def test_old_article_chinese_output_never_reaches_deliverable(settings):
    opportunity = _passed_opportunity(settings)
    outcome = record_opportunity_decision(opportunity["id"], "accepted", settings=settings)
    provider = ChineseContentAI()
    _confirm_old_article_materials(outcome.action_id, settings)

    with pytest.raises(ContentProductionError, match="读者可见字段必须全部为英语"):
        asyncio.run(
            generate_content_deliverable(
                outcome.action_id,
                settings,
                provider=provider,
                confirm_materials=True,
            )
        )

    assert len(provider.calls) == settings.content_ai_call_limit
    with connection(settings) as conn:
        action = conn.execute(
            "SELECT actual_change FROM actions WHERE id = ?",
            (outcome.action_id,),
        ).fetchone()
    assert action["actual_change"] is None


def test_old_article_delivery_rejects_market_and_price_filler():
    result, issues = _normalize_existing_deliverable(
        {
            "change_type": "partial_update",
            "title": "Example Laser Guide",
            "slug": "must-be-locked",
            "summary": "A clearer guide for the same reader task.",
            "content": (
                "## Replacement section\n\n"
                "This update explains the reader decision with a price range, a clear boundary, "
                "and enough practical detail to replace the weak section without changing scope."
            ),
            "tags": "laser pointer, guide",
            "seo_title": "Example Laser Guide",
            "seo_description": "A focused description for the same example laser guide reader task.",
            "seo_keywords": "example laser guide",
            "operator_note": "Replace the named section only.",
        },
        existing={"title": "Example Laser Guide", "slug": "example-laser-guide", "body": ""},
        allowed_urls=set(),
    )

    assert result["slug"] == "example-laser-guide"
    assert "旧文章修改稿不应加入市场或价格内容" in issues


def test_source_roles_distinguish_authority_from_question_signals():
    assert _source_role("https://www.fda.gov/radiation-emitting-products") == "official_or_research"
    assert _source_role("https://www.reddit.com/r/lasers/") == "community_signal"
    assert _source_role("https://lightpaintingphotography.com/guide") == "industry_or_other"
    material = _external_material_record(
        {
            "evidence_id": "external:1:serp",
            "evidence_type": "serp_snapshot",
            "source_ref": "serpapi",
            "payload_json": json.dumps(
                {
                    "results": [
                        {"url": "https://www.fda.gov/radiation-emitting-products"},
                        {"url": "https://www.reddit.com/r/lasers/"},
                    ]
                }
            ),
            "limitations_json": "[]",
            "captured_at": "2026-07-15T00:00:00+00:00",
        }
    )
    assert (
        material["source_roles"]["https://www.fda.gov/radiation-emitting-products"]
        == "official_or_research"
    )
    assert material["source_roles"]["https://www.reddit.com/r/lasers/"] == "community_signal"


class FakeNewArticleAI:
    def __init__(self):
        self.calls: list[str] = []
        self.internal = ""
        self.official = ""
        self.community = ""

    def response(self, stage: str, content: dict) -> AIResponse:
        return AIResponse("fake", "deepseek-v4-flash", content, stage[0] * 64)

    def skill_deliverable(self, user_payload):
        sources = user_payload["allowed_external_sources"]
        self.official = next(
            item["url"] for item in sources if item["source_role"] == "official_or_research"
        )
        self.community = next(
            item["url"] for item in sources if item["source_role"] == "community_signal"
        )
        internal_links = user_payload["allowed_internal_links"]
        self.internal = internal_links[0]["canonical_url"] if internal_links else ""

        intro_sentences = [
            "Camera sensor safety begins before a laser light painting camera is mounted or powered.",
            "A workable plan defines the beam path, camera position, reflective surfaces, and a safe exit.",
            "That preparation matters because an improvised warning cannot control a beam already moving through frame.",
            "The practical goal is not to guess an exposure threshold or promise that brief contact is harmless.",
            "The goal is to keep direct and reflected laser light from entering the lens during every take.",
            "Photographers can do that by separating setup decisions from creative movements and rehearsing both in order.",
            "This workflow starts with a map, continues with a dry rehearsal, and ends with explicit stop conditions.",
            "Each step gives the operator a visible decision instead of relying on memory in a dark location.",
            "It also keeps general safety guidance separate from claims about a particular camera, laser, or environment.",
            "Camera sensor safety therefore becomes a repeatable production task rather than a vague caution at the end.",
            "The following sections explain how to prepare, inspect, rehearse, monitor, and stop a planned shot.",
            "Use the stored sources for boundaries, then adapt the checklist to the real location without inventing facts.",
        ]
        intro = "\n\n".join(
            " ".join(intro_sentences[index : index + 3]) for index in range(0, 12, 3)
        )

        def section_text(subject):
            paragraphs = []
            for _ in range(6):
                paragraphs.append(
                    f"{subject} starts with a defined working zone, known reflective surfaces, and a clear beam exit. "
                    "Each setup decision identifies what could enter the lens and what must remain outside the camera field. "
                    "A written stop condition makes the operator pause whenever movement or reflection changes the planned path."
                )
            return "\n\n".join(paragraphs)

        conclusion_sentences = [
            "Camera sensor safety is strongest when the shoot is designed around controlled paths and visible decisions.",
            "Map the beam, inspect reflections, place the camera, and rehearse every movement before switching on.",
            "Those steps cannot create a universal safe exposure limit, and this article does not claim one.",
            "They do replace improvisation with a sequence that can be checked by everyone at the location.",
            "Keep the official guidance beside the plan for boundaries that need authoritative support.",
            "Use community reports only to recognize recurring concerns, never as proof of technical safety.",
            "During the take, watch for changed angles, moved equipment, unexpected people, and newly exposed reflective surfaces.",
            "Stop immediately when the planned path or exit is no longer clear and controlled.",
            "A delayed shot is easier to recover than a setup that continues after its assumptions have changed.",
            "Record the final arrangement so the next take begins from the same verified conditions.",
            "If the scene cannot meet the checklist, redesign the movement, camera position, or creative effect.",
            "That disciplined decision keeps the production goal clear without inventing confidence the evidence cannot provide.",
        ]
        conclusion = "\n\n".join(
            " ".join(conclusion_sentences[index : index + 3]) for index in range(0, 12, 3)
        )
        questions = [
            "Can a laser damage a camera sensor?",
            "How should reflections be checked before a shoot?",
            "When should a laser light painting setup be stopped?",
        ]
        faq_schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": answer,
                    },
                }
                for question, answer in zip(
                    questions,
                    [
                        "Direct or reflected exposure is the condition this workflow is designed to avoid.",
                        "Inspect every reflective surface during a dry rehearsal and repeat the check after equipment moves.",
                        "Stop whenever the beam path, reflection path, working zone, or exit is no longer controlled.",
                    ],
                    strict=True,
                )
            ],
        }
        internal_sentence = (
            f"Use the [Example Laser Guide]({self.internal}) only for related setup context, "
            "then keep this page focused on the camera-protection task."
            if self.internal
            else "Keep related setup information separate from this camera-protection task."
        )
        content = (
            "# Camera Sensor Safety During Laser Light Painting\n\n"
            f"{intro}\n\n"
            "## Key Takeaways\n\n"
            "- Map direct and reflected beam paths before placing the camera.\n"
            "- Rehearse every movement without the laser and define stop conditions.\n"
            "- Treat community reports as concern signals, not proof of safe exposure.\n"
            "- Stop and redesign the shot whenever the controlled path changes.\n\n"
            "## Why Camera Sensor Safety Starts With Beam Mapping\n\n"
            f"Follow [official laser safety guidance]({self.official}) for authoritative boundaries; "
            "this workflow does not replace those instructions.\n\n"
            f"{section_text('Beam mapping')}\n\n"
            "## Build a Camera Sensor Safety Setup Before Power-On\n\n"
            f"{internal_sentence}\n\n"
            f"{section_text('Camera placement')}\n\n"
            "## Control Reflections and Exit Paths\n\n"
            f"A [photographer discussion]({self.community}) records a recurring concern, "
            "but it does not establish a safe technical threshold.\n\n"
            f"{section_text('Reflection control')}\n\n"
            "## Use a Stop Checklist During the Shoot\n\n"
            f"{section_text('The stop checklist')}\n\n"
            "## FAQ\n\n"
            "### Can a laser damage a camera sensor?\n\n"
            "Direct or reflected exposure is the condition this workflow is designed to avoid.\n\n"
            "### How should reflections be checked before a shoot?\n\n"
            "Inspect every reflective surface during a dry rehearsal and repeat the check after equipment moves.\n\n"
            "### When should a laser light painting setup be stopped?\n\n"
            "Stop whenever the beam path, reflection path, working zone, or exit is no longer controlled.\n\n"
            "```json\n"
            f"{json.dumps(faq_schema)}\n"
            "```\n\n"
            "## Conclusion\n\n"
            f"{conclusion}"
        )
        return {
            "change_type": "new_article",
            "reason": "A source-bound camera protection workflow answers one distinct production task.",
            "article_tier": "Cluster Content",
            "primary_keyword": "camera sensor safety",
            "title": "Camera Sensor Safety During Laser Light Painting",
            "slug": "Camera Sensor Safety -- Laser Light Painting",
            "summary": "A source-bound setup and stop checklist for laser light-painting photographers.",
            "content": content,
            "tags": "laser light painting, camera safety",
            "seo_title": "Camera Sensor Safety for Laser Light Painting Shoots",
            "seo_description": "Plan beam paths, reflections, placement, and stop rules to improve camera sensor safety during laser light painting without claiming unsafe exposure limits.",
            "seo_keywords": "camera sensor safety, laser light painting",
            "internal_links": (
                [{"url": self.internal, "anchor": "Example Laser Guide", "placement": "setup"}]
                if self.internal
                else []
            ),
            "external_sources": [
                {"url": self.official, "supports": "authoritative laser safety boundary"},
                {"url": self.community, "supports": "recurring photographer concern"},
            ],
            "self_review": ["Kept claims inside the confirmed source boundaries."],
            "operator_note": "Review and paste all eight fields into the CMS.",
            "quality_gate": {"status": "pass", "issues": []},
        }

    def skill_plan(self, user_payload):
        sources = user_payload["allowed_external_sources"]
        official = next(
            item["url"] for item in sources if item["source_role"] == "official_or_research"
        )
        community = next(
            item["url"] for item in sources if item["source_role"] == "community_signal"
        )
        internal_links = user_payload["allowed_internal_links"]
        internal = internal_links[0]["canonical_url"] if internal_links else ""
        sections = [
            {
                "heading": "Why Camera Sensor Safety Starts With Beam Mapping",
                "purpose": "Define the controlled path before the camera is placed.",
                "key_points": ["Map direct and reflected paths."],
                "claims": [{"claim": "Use authoritative boundaries.", "source_url": official}],
                "internal_links": [],
                "external_links": [{"url": official, "anchor": "official laser safety guidance"}],
            },
            {
                "heading": "Build a Camera Sensor Safety Setup Before Power-On",
                "purpose": "Place and rehearse the camera without laser output.",
                "key_points": ["Separate setup from creative movement."],
                "claims": [],
                "internal_links": (
                    [{"url": internal, "anchor": "Example Laser Guide"}] if internal else []
                ),
                "external_links": [],
            },
            {
                "heading": "Control Reflections and Exit Paths",
                "purpose": "Use community concern only as a question signal.",
                "key_points": ["Inspect reflective surfaces."],
                "claims": [],
                "internal_links": [],
                "external_links": [{"url": community, "anchor": "photographer discussion"}],
            },
            {
                "heading": "Use a Stop Checklist During the Shoot",
                "purpose": "Define conditions that stop and reset the take.",
                "key_points": ["Stop when the controlled path changes."],
                "claims": [],
                "internal_links": [],
                "external_links": [],
            },
        ]
        return {
            "primary_task": "Protect a camera sensor during laser light painting.",
            "primary_keyword": "camera sensor safety",
            "title": "Camera Sensor Safety During Laser Light Painting",
            "slug": "camera-sensor-safety-laser-light-painting",
            "page_promise": "Prepare and run a controlled laser light-painting shoot.",
            "information_gain": "A setup, rehearsal, monitoring, and stop workflow.",
            "sections": sections,
            "faq_questions": [
                "Can a laser damage a camera sensor?",
                "How should reflections be checked before a shoot?",
                "When should a laser light painting setup be stopped?",
            ],
            "conclusion_job": "State when to stop and redesign the shot.",
            "content_boundaries": ["Do not invent a universal safe exposure threshold."],
            "plan_gate": {"status": "pass", "issues": []},
        }

    async def complete_json(self, system_prompt, user_payload):
        stage = user_payload["stage"]
        self.calls.append(stage)
        if stage == "skill_plan":
            return self.response(stage, self.skill_plan(user_payload))
        if stage in {"skill_draft", "skill_edit", "skill_revision"}:
            return self.response(stage, self.skill_deliverable(user_payload))
        if stage == "material_pack":
            sources = user_payload["available_external_sources"]
            self.official = next(
                item["url"] for item in sources if item["source_role"] == "official_or_research"
            )
            self.community = next(
                item["url"] for item in sources if item["source_role"] == "community_signal"
            )
            return self.response(
                stage,
                {
                    "primary_task": "Protect a camera sensor during laser light painting.",
                    "audience": "Laser light-painting photographers.",
                    "search_intent": "how_to",
                    "article_type": "how_to",
                    "core_answer": "Map and control the beam path before turning on the laser.",
                    "differentiation": "A pre-shoot decision and stop workflow.",
                    "pain_points": [
                        {
                            "insight": "Photographers worry about direct sensor exposure.",
                            "source_url": self.community,
                        }
                    ],
                    "competitor_gaps": ["Warnings often lack an actionable setup sequence."],
                    "authoritative_facts": [
                        {
                            "claim": "The beam path must be controlled.",
                            "url": self.official,
                            "source_role": "official_or_research",
                        }
                    ],
                    "product_fit": [],
                    "content_boundaries": ["Do not invent a safe exposure threshold."],
                    "source_plan": [
                        {"url": self.official, "supports": "beam-control guidance"},
                        {"url": self.community, "supports": "user uncertainty"},
                    ],
                    "limitations": ["No first-hand damage test is claimed."],
                    "material_gate": {"status": "pass", "reasons": []},
                },
            )
        if stage == "outline":
            self.internal = user_payload["available_internal_links"][0]["canonical_url"]
            sections = [
                ("Why beam path matters", self.official, ""),
                ("Build the camera setup", "", self.internal),
                ("Avoid common mistakes", self.community, ""),
                ("Use a pre-shoot checklist", "", ""),
            ]
            return self.response(
                stage,
                {
                    "title_candidates": [
                        "How to Protect a Camera Sensor During Laser Light Painting"
                    ],
                    "selected_title": "How to Protect a Camera Sensor During Laser Light Painting",
                    "slug": "Protect Camera Sensor -- Laser Light Painting",
                    "page_promise": "Prepare a safer shoot.",
                    "intro_approach": "Open with the direct-beam decision.",
                    "sections": [
                        {
                            "heading": heading,
                            "purpose": f"Explain {heading.casefold()}.",
                            "key_points": ["Practical decision"],
                            "claims": [],
                            "internal_links": (
                                [{"url": internal, "anchor": "Example laser guide"}]
                                if internal
                                else []
                            ),
                            "external_links": (
                                [{"url": external, "anchor": "supporting source"}]
                                if external
                                else []
                            ),
                        }
                        for heading, external, internal in sections
                    ],
                    "conclusion_job": "State when to stop and redesign the shot.",
                    "faq_questions": [
                        "Can a laser damage a camera sensor?",
                        "Should the laser ever point toward the lens?",
                        "How should reflections be checked?",
                        "When should the shot be stopped?",
                    ],
                    "optional_elements": ["checklist"],
                    "content_boundaries": ["Stay focused on camera protection."],
                },
            )
        if stage == "draft_first":
            return self.response(
                stage,
                {
                    "content": (
                        "# How to Protect a Camera Sensor During Laser Light Painting\n\n"
                        "Map the beam before the camera records.\n\n"
                        "## Why beam path matters\n\n"
                        f"Start with [official safety guidance]({self.official}).\n\n"
                        "## Build the camera setup\n\n"
                        f"Frame first and use this [Example laser guide]({self.internal}) for context."
                    ),
                    "used_links": [self.official, self.internal],
                    "terminology": ["beam path"],
                    "open_threads": ["mistakes", "checklist"],
                },
            )
        if stage == "draft_second":
            return self.response(
                stage,
                {
                    "stitching_notes": ["Terminology is consistent."],
                    "content": (
                        "## Avoid common mistakes\n\n"
                        f"A [photographer discussion]({self.community}) shows the user concern.\n\n"
                        "## Use a pre-shoot checklist\n\n"
                        "Stop when the path cannot be controlled."
                    ),
                    "used_links": [self.community],
                    "unresolved": [],
                },
            )
        if stage in {"self_review", "revision"}:
            intro = " ".join(
                [
                    "Plan the beam path before mounting the camera and rehearse every movement carefully."
                ]
                * 9
            )
            conclusion = " ".join(
                [
                    "Stop the session whenever direct or reflected light can enter the lens, then redesign the setup before continuing."
                ]
                * 5
            )
            content = (
                "# How to Protect a Camera Sensor During Laser Light Painting\n\n"
                f"{intro}\n\n"
                "## Why the beam path is the first decision\n\n"
                f"Use [official laser safety guidance]({self.official}) instead of guessing that a short exposure is harmless.\n\n"
                "## Build the camera setup before using the laser\n\n"
                f"Lock framing and focus first. This [Example laser guide]({self.internal}) supplies supporting setup context.\n\n"
                "## Avoid the mistakes photographers worry about\n\n"
                f"A [photographer discussion]({self.community}) captures the concern but does not prove a safe threshold.\n\n"
                "## Run a pre-shoot stop checklist\n\n"
                "Remove reflective surprises, rehearse without the laser, and redesign any uncontrolled shot.\n\n"
                "## FAQ\n\n"
                "### Can a laser damage a camera sensor?\n\n"
                "Direct exposure is the condition this workflow avoids.\n\n"
                "### Should the laser ever point toward the lens?\n\n"
                "No; redesign the beam path first.\n\n"
                "### How should reflections be checked?\n\n"
                "Inspect reflective surfaces before switching on the laser.\n\n"
                "### When should the shot be stopped?\n\n"
                "Stop whenever the beam path cannot be controlled.\n\n"
                "## Conclusion\n\n"
                f"{conclusion}"
            )
            return self.response(
                stage,
                {
                    "change_type": "new_article",
                    "reason": "One practical task with source roles kept separate.",
                    "title": "How to Protect a Camera Sensor During Laser Light Painting",
                    "slug": "Protect Camera Sensor -- Laser Light Painting",
                    "summary": "A source-bound setup checklist for laser light-painting photographers.",
                    "content": content,
                    "tags": "laser light painting, camera safety",
                    "seo_title": "Protect Camera Sensors During Laser Light Painting",
                    "seo_description": "Plan beam paths, camera setup, reflections, and stop conditions before a laser light-painting shoot.",
                    "seo_keywords": "laser light painting camera sensor safety",
                    "internal_links": [
                        {
                            "url": self.internal,
                            "anchor": "Example laser guide",
                            "placement": "camera setup",
                        }
                    ],
                    "external_sources": [
                        {"url": self.official, "supports": "beam-path safety boundary"},
                        {"url": self.community, "supports": "the recurring user concern"},
                    ],
                    "self_review": ["Separated authority from community voice."],
                    "operator_note": "Review and paste all eight fields into the CMS.",
                    "quality_gate": {"status": "pass", "issues": []},
                },
            )
        raise AssertionError(f"unexpected stage: {stage}")


def test_new_article_generation_returns_all_cms_fields_and_clean_slug(settings):
    _prepare(settings)
    with connection(settings) as conn:
        run_id = conn.execute("SELECT id FROM analysis_runs ORDER BY id DESC LIMIT 1").fetchone()[
            "id"
        ]
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
                run_id,
                "example camera sensor safety during laser light painting",
                "Example camera sensor safety during laser light painting",
                json.dumps(
                    {
                        "evidence_ids": ["external:test"],
                        "facts": ["operator-confirmed research fact"],
                        "source_urls": [
                            "https://www.fda.gov/radiation-emitting-products/laser-products-and-instruments",
                            "https://www.reddit.com/r/lasers/comments/example/camera_sensor_question",
                        ],
                    }
                ),
            ),
        )
        opportunity_id = int(cursor.lastrowid)

    outcome = record_opportunity_decision(
        opportunity_id,
        "accepted",
        settings=settings,
    )
    with connection(settings) as conn:
        baseline = json.loads(
            conn.execute(
                "SELECT baseline_json FROM actions WHERE id = ?",
                (outcome.action_id,),
            ).fetchone()["baseline_json"]
        )
    assert baseline["evidence_ids"] == ["external:test"]
    assert baseline["metrics"] == {}

    preview = build_material_preview(outcome.action_id, settings)
    assert preview["ready"] is False
    assert "G 真实搜索问题" in preview["missing"]
    fake_ai = FakeNewArticleAI()
    with pytest.raises(ContentProductionError, match="G 真实搜索问题"):
        asyncio.run(
            generate_content_deliverable(
                outcome.action_id,
                settings,
                provider=fake_ai,
            )
        )
    assert fake_ai.calls == []

    app = create_app(settings)
    with TestClient(app) as client:
        checkpoint_page = client.get("/actions")
    assert "所有任务先核对现有素材，再决定要不要手工补充" in checkpoint_page.text
    assert "生成搜索提示词" in checkpoint_page.text

    manual_text = (
        "A 用户痛点：Photographers need a repeatable stop rule. "
        "https://www.reddit.com/r/lasers/comments/example/camera_sensor_question\n"
        "E 权威引用：Use the official laser safety boundary. "
        "https://www.fda.gov/radiation-emitting-products/laser-products-and-instruments\n"
        "G 真实搜索问题：Can a laser damage a camera sensor? "
        "https://www.reddit.com/r/lasers/comments/example/camera_sensor_question"
    )
    message = save_manual_material(outcome.action_id, manual_text, settings)
    assert "没有调用 API" in message
    with connection(settings) as conn:
        run_count = conn.execute(
            "SELECT COUNT(*) FROM external_runs WHERE purpose = 'manual_material'"
        ).fetchone()[0]
    assert "不会重复写入" in save_manual_material(outcome.action_id, manual_text, settings)
    with connection(settings) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM external_runs WHERE purpose = 'manual_material'"
            ).fetchone()[0]
            == run_count
        )

    preview = build_material_preview(outcome.action_id, settings)
    assert preview["ready"] is True
    assert preview["confirmed"] is False
    with TestClient(app) as client:
        ready_page = client.get("/actions")
    assert "生成搜索提示词" in ready_page.text

    result = asyncio.run(
        generate_content_deliverable(
            outcome.action_id,
            settings,
            provider=fake_ai,
            confirm_materials=True,
        )
    )

    cms_fields = {
        "title",
        "slug",
        "summary",
        "content",
        "tags",
        "seo_title",
        "seo_description",
        "seo_keywords",
    }
    assert result["change_type"] == "new_article"
    assert result["slug"] == "camera-sensor-safety-laser-light-painting"
    assert all(isinstance(result[field], str) and result[field] for field in cms_fields)
    assert len(result["internal_links"]) == 1
    assert len(result["external_sources"]) == 2
    assert result["quality_report"]["status"] == "pass"
    assert result["quality_report"]["revision_used"] is False
    assert result["quality_report"]["ai_call_count"] == 3
    assert result["quality_report"]["ai_call_limit"] == 4
    assert result["quality_report"]["revision_count"] == 0
    assert len(result["production_stages"]) == 6
    assert fake_ai.calls == ["skill_plan", "skill_draft", "skill_edit"]

    with connection(settings) as conn:
        ai_runs = conn.execute(
            "SELECT purpose, status FROM ai_runs WHERE opportunity_id = ? ORDER BY id",
            (opportunity_id,),
        ).fetchall()
        action = conn.execute(
            "SELECT workflow_status FROM actions WHERE id = ?",
            (outcome.action_id,),
        ).fetchone()
    assert [(item["purpose"], item["status"]) for item in ai_runs] == [
        ("content_skill_plan", "success"),
        ("content_skill_draft", "success"),
        ("content_skill_edit", "success"),
    ]
    assert action["workflow_status"] == "in_progress"

    app = create_app(settings)
    with TestClient(app) as client:
        current_page = client.get("/actions")
        assert "Example camera sensor safety during laser light painting" in current_page.text
        assert "我已发布" in current_page.text
        publish = client.post(
            f"/actions/{outcome.action_id}/published",
            data={"published": "1"},
        )
        history_page = client.get("/actions?history=1")
    assert publish.status_code == 200
    assert "Example camera sensor safety during laser light painting" not in publish.text
    assert "Example camera sensor safety during laser light painting" not in history_page.text
    assert "重新打开任务" not in history_page.text

    assert "重新打开" in set_action_published(outcome.action_id, published=False, settings=settings)
    with TestClient(app) as client:
        reopened = client.get("/actions")
    assert "Example camera sensor safety during laser light painting" in reopened.text
