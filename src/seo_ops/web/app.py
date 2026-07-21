from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from seo_ops import __version__
from seo_ops.config import (
    CONTENT_AI_CALL_LIMIT_MAX,
    CONTENT_AI_CALL_LIMIT_MIN,
    RESEARCH_BUDGET_LIMITS,
    Settings,
    get_settings,
)
from seo_ops.db import connection, init_db
from seo_ops.ingest import (
    ImportDeletionError,
    delete_gsc_import,
    import_cms_bytes,
    import_gsc_bytes,
)
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import (
    get_opportunity,
    get_site,
    list_actions,
    list_imports,
    list_research_runs,
    list_rules,
    list_sites,
)
from seo_ops.services.action_workflow import (
    ActionWorkflowError,
    record_opportunity_decision,
    set_action_cancelled,
    set_action_published,
    update_action_step,
)
from seo_ops.services.ai import AIUnavailable, explain_opportunity
from seo_ops.services.article_suggestions import (
    ArticleSuggestionError,
    decide_new_article_suggestion,
    decide_old_article_suggestion,
    list_article_suggestions,
    review_new_article_candidate,
)
from seo_ops.services.content_production import (
    ContentProductionError,
    generate_content_deliverable,
)
from seo_ops.services.data_quality import assess_gsc_quality
from seo_ops.services.external_evidence import (
    EvidenceCollectionUnavailable,
    collect_query_evidence,
)
from seo_ops.services.external_sources import (
    SOURCE_CATALOG,
    configured_sources,
    test_source_connection,
)
from seo_ops.services.gsc_oauth import (
    GSCError,
    OAuthStateStore,
    build_authorization_url,
    complete_authorization,
    gsc_connection_status,
    sync_gsc,
)
from seo_ops.services.legacy_sync import sync_all as legacy_sync_all
from seo_ops.services.legacy_workflow import (
    get_legacy_display_data,
    stage_r0_generate_prompt,
    stage_r1_save_and_collect,
    stage_r3_ai_analyze,
    stage_w0_validate_and_draft,
    stage_w1b_pre_check,
    stage_w2_post_process,
    stage_w3_register,
)
from seo_ops.services.material_workflow import (
    MaterialWorkflowError,
    build_material_preview,
    save_manual_material,
)
from seo_ops.services.research_workflow import (
    ResearchUnavailable,
    boundary_dimensions,
    configured_research_budgets,
    research_topic_options,
    run_topic_research,
)
from seo_ops.services.settings_store import (
    NON_SECRET_FIELDS,
    SECRET_FIELDS,
    update_local_settings,
)
from seo_ops.services.topic_graph import sync_topic_graph, topic_tree
from seo_ops.utils import json_dumps, utc_now

PACKAGE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# OAuth authorization codes arrive in the root callback query string.
# Uvicorn's default access log includes that query string, so disable it.
logging.getLogger("uvicorn.access").disabled = True


def _percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _number(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.0f}"


def _redirect(path: str, message: str, level: str = "success") -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{path}{separator}message={quote_plus(message)}&level={quote_plus(level)}",
        status_code=303,
    )


def _require_local_form(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    hostname = urlsplit(origin).hostname
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=403, detail="该操作只能从本机页面发起")


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    oauth_states = OAuthStateStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db(active_settings)
        yield

    app = FastAPI(
        title="SEO Ops System",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters["percent"] = _percent
    templates.env.filters["number"] = _number

    def page_context(request: Request, **values):
        page = values.get("page")
        workflow_steps = {
            "imports": 1,
            "research": 2,
            "opportunities": 3,
            "actions": 4,
            "topics": 5,
        }
        return {
            "request": request,
            "version": __version__,
            "ai_enabled": active_settings.ai_enabled,
            "message": request.query_params.get("message"),
            "message_level": request.query_params.get("level", "success"),
            "workflow_step": workflow_steps.get(page),
            **values,
        }

    def active_site_id(request: Request) -> int:
        requested = request.query_params.get("site_id")
        if requested and requested.isdigit():
            return int(requested)
        with connection(active_settings) as conn:
            sites = list_sites(conn)
        if not sites:
            raise RuntimeError("数据库中没有站点")
        return int(sites[0]["id"])

    @app.get("/api/health", response_class=JSONResponse)
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "ai_enabled": active_settings.ai_enabled,
            "configured_sources": configured_sources(active_settings),
        }

    @app.get("/")
    async def workflow_start(request: Request):
        oauth_error = request.query_params.get("error")
        oauth_state = request.query_params.get("state")
        if oauth_error:
            oauth_states.discard(oauth_state)
            return _redirect(
                "/imports",
                "Google 授权未完成，请重新点击连接",
                "error",
            )
        code = request.query_params.get("code")
        if code or oauth_state:
            if not code or not oauth_state:
                return _redirect("/imports", "Google 授权回调不完整", "error")
            try:
                outcome = await complete_authorization(
                    code,
                    oauth_state,
                    active_settings,
                    oauth_states,
                )
                return _redirect(
                    "/imports",
                    f"{outcome.message}；已匹配 {outcome.property_uri}",
                )
            except GSCError as exc:
                return _redirect("/imports", str(exc), "error")
        return RedirectResponse("/imports", status_code=307)

    @app.get("/imports/gsc/oauth/start")
    async def start_gsc_oauth(site_id: int):
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
        if not site:
            return _redirect("/imports", "站点不存在", "error")
        try:
            authorization_url = build_authorization_url(
                site_id,
                active_settings,
                oauth_states,
            )
            return RedirectResponse(authorization_url, status_code=302)
        except GSCError as exc:
            return _redirect("/imports", str(exc), "error")

    @app.get("/imports")
    async def imports_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            history = list_imports(conn, site_id)
        gsc_status = gsc_connection_status(site_id, active_settings)
        return templates.TemplateResponse(
            request=request,
            name="imports.html",
            context=page_context(
                request,
                page="imports",
                page_title="数据导入",
                site=site,
                sites=sites,
                imports=history,
                gsc_status=gsc_status,
            ),
        )

    @app.get("/topics")
    async def topics_page(request: Request):
        site_id = active_site_id(request)
        summary = sync_topic_graph(site_id, active_settings)
        tree = topic_tree(site_id, active_settings)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
        return templates.TemplateResponse(
            request=request,
            name="topics.html",
            context=page_context(
                request,
                page="topics",
                page_title="主题图谱",
                site=site,
                sites=sites,
                topic_summary=summary,
                topic_tree=tree,
            ),
        )

    @app.post("/imports/gsc/sync")
    async def sync_gsc_data(
        request: Request,
        site_id: int = Form(...),
    ):
        _require_local_form(request)
        try:
            outcome = await sync_gsc(site_id, active_settings)
            message = (
                f"{outcome.message}；可信日期 {outcome.actual_start_date} 至 "
                f"{outcome.actual_end_date}"
            )
            try:
                analysis = run_analysis(site_id, active_settings)
                message += f"；内部旧文章分析已更新（{analysis.top_count} 项优先建议）"
            except ValueError:
                message += "；GSC 已保存，导入文章和产品 JSON 后会形成旧文章建议"
            return _redirect("/imports", message)
        except GSCError as exc:
            return _redirect("/imports", str(exc), "error")

    @app.post("/imports/gsc")
    async def upload_gsc(site_id: int = Form(...), files: list[UploadFile] = File(...)):
        outcomes = []
        for uploaded in files:
            content = await uploaded.read()
            if len(content) > MAX_UPLOAD_BYTES:
                return _redirect("/imports", f"{uploaded.filename} 超过 100MB 限制", "error")
            outcomes.append(
                import_gsc_bytes(site_id, uploaded.filename or "gsc.xlsx", content, active_settings)
            )
        failures = [outcome for outcome in outcomes if outcome.status == "failed"]
        message = "；".join(outcome.message for outcome in outcomes)
        if not failures:
            try:
                analysis = run_analysis(site_id, active_settings)
                message += f"；内部旧文章分析已自动更新（{analysis.top_count} 项优先建议）"
            except ValueError:
                message += "；GSC 已保存，导入 CMS 内容后会形成旧文章建议"
        return _redirect("/imports", message, "error" if failures else "success")

    @app.post("/imports/cms")
    async def upload_cms(site_id: int = Form(...), files: list[UploadFile] = File(...)):
        outcomes = []
        for uploaded in files:
            content = await uploaded.read()
            if len(content) > MAX_UPLOAD_BYTES:
                return _redirect("/imports", f"{uploaded.filename} 超过 100MB 限制", "error")
            outcomes.append(
                import_cms_bytes(site_id, uploaded.filename or "cms.json", content, active_settings)
            )
        failures = [outcome for outcome in outcomes if outcome.status == "failed"]
        message = "；".join(outcome.message for outcome in outcomes)
        if not failures:
            sync_topic_graph(site_id, active_settings)
            try:
                analysis = run_analysis(site_id, active_settings)
                message += f"；主题图谱和旧文章分析已自动更新（{analysis.top_count} 项优先建议）"
            except ValueError:
                message += "；主题图谱已更新，导入 GSC 后会自动分析旧文章"
        return _redirect("/imports", message, "error" if failures else "success")

    @app.post("/imports/{import_id}/delete")
    async def delete_import(
        import_id: int,
        request: Request,
        site_id: int = Form(...),
    ):
        _require_local_form(request)
        try:
            outcome = delete_gsc_import(site_id, import_id, active_settings)
            return _redirect("/imports", outcome.message)
        except ImportDeletionError as exc:
            return _redirect("/imports", str(exc), "error")

    @app.post("/analysis/run")
    async def analysis_run(site_id: int = Form(...)):
        try:
            outcome = run_analysis(site_id, active_settings)
            return _redirect(
                "/opportunities",
                outcome.message,
                "success" if outcome.status == "success" else "error",
            )
        except ValueError as exc:
            return _redirect("/imports", str(exc), "error")

    @app.get("/opportunities")
    async def opportunities_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
        suggestions = list_article_suggestions(site_id, active_settings)
        return templates.TemplateResponse(
            request=request,
            name="suggestions.html",
            context=page_context(
                request,
                page="opportunities",
                page_title="文章建议",
                site=site,
                sites=sites,
                suggestions=suggestions,
            ),
        )

    @app.post("/opportunities/{opportunity_id}/decision")
    async def decide_opportunity(
        opportunity_id: int,
        request: Request,
        decision: str = Form(...),
        reason: str = Form(""),
    ):
        _require_local_form(request)
        try:
            if decision in {"accepted", "rejected"}:
                outcome = record_opportunity_decision(
                    opportunity_id, decision, reason, active_settings
                )
                target = "/actions" if decision == "accepted" else "/opportunities"
                return _redirect(target, outcome.message)
            outcome = decide_old_article_suggestion(
                opportunity_id, decision, reason, active_settings
            )
        except (ActionWorkflowError, ArticleSuggestionError) as exc:
            return _redirect("/opportunities", str(exc), "error")
        target = "/actions" if outcome.action_id else "/opportunities"
        return _redirect(target, outcome.message)

    @app.post("/opportunities/{opportunity_id}/evidence/collect")
    async def collect_opportunity_evidence(opportunity_id: int, request: Request):
        _require_local_form(request)
        return_path = (
            "/actions" if request.query_params.get("return_to") == "/actions" else "/opportunities"
        )
        try:
            outcome = await collect_query_evidence(opportunity_id, active_settings)
        except EvidenceCollectionUnavailable as exc:
            target = "/settings" if "配置" in str(exc) else return_path
            return _redirect(target, str(exc), "warning")
        except Exception:
            return _redirect(
                return_path,
                "外部证据采集失败；未改变机会分数或资格门槛",
                "error",
            )
        level = "success" if outcome.status == "success" else "warning"
        if outcome.status == "failed":
            level = "error"
        return _redirect(return_path, outcome.message, level)

    @app.get("/actions")
    async def actions_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            all_actions = list_actions(conn, site_id)
        current_actions = [
            item
            for item in all_actions
            if item["decision"] == "accepted"
            and item["workflow_status"] in {"planned", "in_progress"}
        ]
        for item in current_actions:
            item["material_preview"] = None
            item["material_error"] = None
            item["legacy"] = None
            if item["action_type"] == "create" and not item["deliverable"]:
                try:
                    topic = item.get("target_ref", "") or ""
                    item["legacy"] = get_legacy_display_data(topic, LEGACY_WS)
                except Exception:
                    item["legacy"] = None
            if not item["deliverable"] and item["action_type"] != "create":
                try:
                    item["material_preview"] = build_material_preview(
                        int(item["id"]), active_settings
                    )
                except MaterialWorkflowError as exc:
                    item["material_error"] = str(exc)
        return templates.TemplateResponse(
            request=request,
            name="production.html",
            context=page_context(
                request,
                page="actions",
                page_title="文章制作",
                site=site,
                sites=sites,
                old_actions=[item for item in current_actions if item["action_type"] != "create"],
                new_actions=[item for item in current_actions if item["action_type"] == "create"],
                content_ai_call_limit=active_settings.content_ai_call_limit,
                content_ai_call_limits={
                    "min": CONTENT_AI_CALL_LIMIT_MIN,
                    "max": CONTENT_AI_CALL_LIMIT_MAX,
                },
            ),
        )

    @app.post("/actions/ai-limit")
    async def save_action_ai_limit(request: Request):
        nonlocal active_settings
        _require_local_form(request)
        form = await request.form()
        submitted = {"content_ai_call_limit": str(form.get("content_ai_call_limit", ""))}
        try:
            active_settings = update_local_settings(active_settings, submitted, set())
            app.state.settings = active_settings
        except (ValueError, OSError) as exc:
            return _redirect("/actions", f"文章 AI 上限未保存：{exc}", "error")
        return _redirect("/actions", "每篇文章 AI 调用上限已保存")

    @app.post("/actions/{action_id}/generate")
    async def generate_action_content(
        action_id: int,
        request: Request,
        confirm_materials: str = Form(""),
    ):
        _require_local_form(request)
        try:
            await generate_content_deliverable(
                action_id,
                active_settings,
                confirm_materials=confirm_materials == "1",
            )
        except (ContentProductionError, AIUnavailable) as exc:
            return _redirect("/actions", str(exc), "error")
        except Exception:
            return _redirect(
                "/actions",
                "文章制作失败；任务已保留，失败阶段已记录，可以安全重试",
                "error",
            )
        return _redirect("/actions", "已生成可直接粘贴到 CMS 的内容包")

    @app.post("/actions/{action_id}/materials")
    async def add_action_material(
        action_id: int,
        request: Request,
        material_text: str = Form(...),
    ):
        _require_local_form(request)
        try:
            message = save_manual_material(action_id, material_text, active_settings)
        except MaterialWorkflowError as exc:
            return _redirect("/actions", str(exc), "error")
        return _redirect("/actions", message)

    @app.post("/actions/{action_id}/steps/{step_id}")
    async def action_step(
        action_id: int,
        step_id: int,
        request: Request,
        completed: str = Form(...),
    ):
        _require_local_form(request)
        try:
            message = update_action_step(
                action_id,
                step_id,
                completed=completed == "1",
                settings=active_settings,
            )
        except ActionWorkflowError as exc:
            return _redirect("/actions", str(exc), "error")
        return _redirect("/actions", message)

    # ── Legacy workflow helpers ─────────────────────────────────────

    def _get_action_or_404(action_id: int):
        with connection(active_settings) as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action not found")
        return dict(row)

    def _update_legacy_stage(action_id: int, stage: str | None):
        with connection(active_settings) as conn:
            conn.execute(
                "UPDATE actions SET legacy_stage = ?, updated_at = ? WHERE id = ?",
                (stage, utc_now().isoformat(), action_id),
            )
            conn.commit()

    LEGACY_WS = Path("data/legacy_workflow/laserpointerhub")

    # ── Legacy stage routes ─────────────────────────────────────────

    @app.post("/actions/{action_id}/legacy/stage/r0")
    async def legacy_r0(action_id: int, request: Request):
        _require_local_form(request)
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        if not topic:
            return _redirect("/actions", "无法获取文章主题", "error")
        legacy_sync_all(LEGACY_WS)
        result = stage_r0_generate_prompt(topic, LEGACY_WS)
        _update_legacy_stage(action_id, result.get("stage"))
        return _redirect("/actions", "搜索提示词已生成")

    @app.post("/actions/{action_id}/legacy/stage/r1")
    async def legacy_r1(action_id: int, request: Request):
        _require_local_form(request)
        form = await request.form()
        search_text = form.get("search_results", "") or ""
        if not search_text.strip():
            return _redirect("/actions", "请粘贴搜索结果")
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_r1_save_and_collect(topic, search_text, LEGACY_WS)
        _update_legacy_stage(action_id, result.get("stage"))
        msg = result.get("error") or "数据已收集，等待 AI 分析"
        return _redirect("/actions", msg, "error" if not result.get("success") else "success")

    @app.post("/actions/{action_id}/legacy/stage/r3")
    async def legacy_r3(action_id: int, request: Request):
        _require_local_form(request)
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_r3_ai_analyze(topic, LEGACY_WS, active_settings)
        _update_legacy_stage(action_id, result.get("stage"))
        msg = result.get("error") or "AI 分析完成，素材包和简报已生成"
        return _redirect("/actions", msg, "error" if not result.get("success") else "success")

    @app.post("/actions/{action_id}/legacy/stage/w0")
    async def legacy_w0(action_id: int, request: Request):
        _require_local_form(request)
        form = await request.form()
        author = form.get("author", "") or "LaserPointerHub"
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_w0_validate_and_draft(topic, author, LEGACY_WS, active_settings)
        _update_legacy_stage(action_id, result.get("stage"))
        msg = result.get("error") or "草稿已生成，等待预检"
        return _redirect("/actions", msg, "error" if not result.get("success") else "success")

    @app.post("/actions/{action_id}/legacy/stage/w1b")
    async def legacy_w1b(action_id: int, request: Request):
        _require_local_form(request)
        tier = request.query_params.get("tier", "")
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_w1b_pre_check(topic, tier, LEGACY_WS)
        _update_legacy_stage(action_id, result.get("stage"))
        fail_count = result.get("fail_count", 0)
        if fail_count > 0:
            return _redirect("/actions", f"预检完成：{fail_count} 项需要修复", "error")
        return _redirect("/actions", "预检通过")

    @app.post("/actions/{action_id}/legacy/stage/w2")
    async def legacy_w2(action_id: int, request: Request):
        _require_local_form(request)
        do_apply = request.query_params.get("apply") == "1"
        do_force = request.query_params.get("force") == "1"
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_w2_post_process(topic, apply=do_apply, force=do_force, workspace=LEGACY_WS)
        _update_legacy_stage(action_id, result.get("stage"))
        if not result.get("gate_passed") and not do_apply:
            return _redirect("/actions", "后处理发现问题，请修复后重试", "error")
        msg = "链接字段已写回并注册" if do_apply else "后处理检查通过"
        return _redirect("/actions", msg)

    @app.post("/actions/{action_id}/legacy/stage/w3")
    async def legacy_w3(action_id: int, request: Request):
        _require_local_form(request)
        action = _get_action_or_404(action_id)
        topic = action.get("target_ref", "") or ""
        result = stage_w3_register(topic, LEGACY_WS)
        _update_legacy_stage(action_id, result.get("stage"))
        return _redirect("/actions", "注册完成" if result.get("success") else (result.get("error") or "注册失败"),
                          "error" if not result.get("success") else "success")

    @app.get("/research")
    async def research_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            runs = list_research_runs(
                conn,
                site_id,
                limit=1,
                include_candidates=False,
                include_audit=True,
            )
        return templates.TemplateResponse(
            request=request,
            name="research_flow.html",
            context=page_context(
                request,
                page="research",
                page_title="外部主题调研",
                site=site,
                sites=sites,
                research_runs=runs,
                research_budgets=configured_research_budgets(active_settings),
                research_budget_limits=RESEARCH_BUDGET_LIMITS,
                configured=configured_sources(active_settings),
                ai_model=active_settings.ai_model,
                topic_options=research_topic_options(site_id, active_settings),
                boundary_dimensions=boundary_dimensions(),
            ),
        )

    @app.post("/research/budgets")
    async def save_research_budgets(request: Request):
        nonlocal active_settings
        _require_local_form(request)
        form = await request.form()
        submitted = {
            field: str(form.get(field, ""))
            for field in (
                "research_serpapi_budget",
                "research_firecrawl_budget",
                "research_tavily_budget",
                "research_ai_budget",
            )
        }
        try:
            active_settings = update_local_settings(active_settings, submitted, set())
            app.state.settings = active_settings
        except (ValueError, OSError) as exc:
            return _redirect("/research", f"预算未保存：{exc}", "error")
        return _redirect("/research", "每轮调研预算已保存；它们是上限，不会强制用完")

    @app.post("/research/run")
    async def research_run(
        request: Request,
        site_id: int = Form(...),
        seed_type: str = Form("auto"),
        topic_id: str = Form(""),
        dimension_key: str = Form(""),
    ):
        _require_local_form(request)
        try:
            outcome = await run_topic_research(
                site_id,
                active_settings,
                seed_type=seed_type,
                topic_id=int(topic_id) if topic_id.isdigit() else None,
                dimension_key=dimension_key or None,
            )
        except ResearchUnavailable as exc:
            return _redirect("/research", str(exc), "error")
        level = "success" if outcome.status == "success" else "warning"
        if outcome.status == "failed":
            level = "error"
        return _redirect("/research", outcome.message, level)

    @app.post("/research/candidates/{candidate_id}/decision")
    async def research_candidate_decision(
        candidate_id: int,
        request: Request,
        decision: str = Form(...),
        reason: str = Form(""),
    ):
        _require_local_form(request)
        try:
            outcome = decide_new_article_suggestion(candidate_id, decision, reason, active_settings)
        except ArticleSuggestionError as exc:
            return _redirect("/opportunities", str(exc), "error")
        target = "/actions" if outcome.action_id else "/opportunities"
        return _redirect(target, outcome.message)

    @app.post("/research/candidates/{candidate_id}/review")
    async def research_candidate_review(
        candidate_id: int,
        request: Request,
        review_decision: str = Form(...),
        reason: str = Form(...),
    ):
        _require_local_form(request)
        try:
            outcome = review_new_article_candidate(
                candidate_id,
                review_decision,
                reason,
                active_settings,
            )
        except ArticleSuggestionError as exc:
            return _redirect("/opportunities", str(exc), "error")
        return _redirect("/opportunities", outcome.message)

    @app.post("/actions/{action_id}/published")
    async def action_published(
        action_id: int,
        request: Request,
        published: str = Form(...),
    ):
        _require_local_form(request)
        is_published = published == "1"
        try:
            message = set_action_published(
                action_id,
                published=is_published,
                settings=active_settings,
            )
        except ActionWorkflowError as exc:
            return _redirect("/actions", str(exc), "error")
        return _redirect("/actions", message)

    @app.post("/actions/{action_id}/status")
    async def action_status(
        action_id: int,
        request: Request,
        cancelled: str = Form(...),
    ):
        _require_local_form(request)
        try:
            message = set_action_cancelled(
                action_id,
                cancelled=cancelled == "1",
                settings=active_settings,
            )
        except ActionWorkflowError as exc:
            return _redirect("/actions", str(exc), "error")
        return _redirect("/actions", message)

    @app.post("/opportunities/{opportunity_id}/ai-explain")
    async def ai_explain(opportunity_id: int):
        with connection(active_settings) as conn:
            opportunity = get_opportunity(conn, opportunity_id)
        if not opportunity:
            return _redirect("/opportunities", "机会不存在", "error")
        try:
            await explain_opportunity(opportunity, active_settings)
            return _redirect("/opportunities", "AI 已依据现有证据生成解释")
        except AIUnavailable as exc:
            return _redirect("/settings", str(exc), "warning")
        except Exception:
            return _redirect("/opportunities", "AI 调用失败；请在设置页测试连接", "error")

    @app.get("/method")
    async def method_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            rules = list_rules(conn)
        return templates.TemplateResponse(
            request=request,
            name="method.html",
            context=page_context(
                request,
                page="method",
                page_title="方法与证据",
                site=site,
                sites=sites,
                rules=rules,
            ),
        )

    @app.get("/settings")
    async def settings_page(request: Request):
        site_id = active_site_id(request)
        configured = configured_sources(active_settings)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            run_count = conn.execute(
                "SELECT COUNT(*) AS count FROM ai_runs WHERE site_id = ?", (site_id,)
            ).fetchone()["count"]
            check_rows = conn.execute(
                "SELECT * FROM source_connections ORDER BY provider"
            ).fetchall()
            gsc_quality = assess_gsc_quality(conn, site_id)
        checks = {row["provider"]: dict(row) for row in check_rows}
        names = {
            "ai": "AI Provider",
            "serpapi": "SerpAPI",
            "firecrawl": "Firecrawl",
            "tavily": "Tavily",
        }
        connections = []
        for key, name in names.items():
            check = checks.get(key, {})
            last_status = check.get("status")
            if last_status == "connected":
                label = "连接正常"
            elif last_status == "error":
                label = "测试失败"
            elif configured[key]:
                label = "等待测试"
            else:
                label = "未配置"
            connections.append(
                {
                    "key": key,
                    "name": name,
                    "configured": configured[key],
                    "last_status": last_status,
                    "status_label": label,
                    "last_message": check.get("error_message")
                    or ("连接已验证" if last_status == "connected" else None),
                    "checked_at": check.get("last_checked_at"),
                }
            )

        status = {
            "ai": {
                "configured": configured["ai"],
                "content_call_limit": active_settings.content_ai_call_limit,
                "provider": active_settings.ai_provider,
                "base_url": active_settings.ai_base_url,
                "model": active_settings.ai_model,
                "has_key": bool(active_settings.ai_api_key),
                "run_count": run_count,
            },
            "serpapi": {
                "configured": configured["serpapi"],
                "has_key": configured["serpapi"],
                "country": active_settings.serpapi_country,
                "language": active_settings.serpapi_language,
                "device": active_settings.serpapi_device,
                "location": active_settings.serpapi_location,
            },
            "firecrawl": {
                "configured": configured["firecrawl"],
                "has_key": configured["firecrawl"],
                "base_url": active_settings.firecrawl_base_url,
            },
            "tavily": {
                "configured": configured["tavily"],
                "has_key": configured["tavily"],
                "base_url": active_settings.tavily_base_url,
            },
            "trends": {
                "provider": active_settings.trends_provider,
                "geo": active_settings.trends_geo,
                "timeframe": active_settings.trends_timeframe,
            },
            "research": configured_research_budgets(active_settings),
        }
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=page_context(
                request,
                page="settings",
                page_title="设置与数据连接",
                site=site,
                sites=sites,
                settings_status=status,
                content_ai_call_limits={
                    "min": CONTENT_AI_CALL_LIMIT_MIN,
                    "max": CONTENT_AI_CALL_LIMIT_MAX,
                },
                configured_count=sum(configured.values()),
                connections=connections,
                source_catalog=SOURCE_CATALOG,
                gsc_quality=gsc_quality,
                research_budget_limits=RESEARCH_BUDGET_LIMITS,
            ),
        )

    @app.post("/settings")
    async def save_settings(request: Request):
        nonlocal active_settings
        _require_local_form(request)
        form = await request.form()
        submitted = {
            field: str(form.get(field, ""))
            for field in (*NON_SECRET_FIELDS.keys(), *SECRET_FIELDS.keys())
            if field in form
        }
        clear = {field for field in SECRET_FIELDS if form.get(f"clear_{field}") == "1"}
        try:
            active_settings = update_local_settings(active_settings, submitted, clear)
            app.state.settings = active_settings
        except ValueError as exc:
            return _redirect("/settings", f"设置未保存：{exc}", "error")
        except OSError:
            return _redirect("/settings", "设置未保存：无法写入本地配置文件", "error")
        return _redirect("/settings", "设置已保存并立即生效；可继续测试连接")

    @app.post("/settings/test/{provider}")
    async def test_connection(provider: str, request: Request):
        _require_local_form(request)
        result = await test_source_connection(provider, active_settings)
        with connection(active_settings) as conn:
            conn.execute(
                """
                INSERT INTO source_connections(
                    provider, status, last_checked_at, details_json, error_message
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    status = excluded.status,
                    last_checked_at = excluded.last_checked_at,
                    details_json = excluded.details_json,
                    error_message = excluded.error_message
                """,
                (
                    provider,
                    "connected" if result.ok else "error",
                    utc_now(),
                    json_dumps(result.details),
                    None if result.ok else result.message,
                ),
            )
        return _redirect("/settings", result.message, "success" if result.ok else "error")

    @app.get("/ai")
    async def ai_page_redirect():
        return RedirectResponse("/settings#ai", status_code=307)

    return app


app = create_app()
