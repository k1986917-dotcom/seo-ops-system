from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from seo_ops import __version__
from seo_ops.config import Settings, get_settings
from seo_ops.db import connection, init_db
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.opportunities import run_analysis
from seo_ops.repositories import (
    dashboard_summary,
    get_opportunity,
    get_site,
    list_imports,
    list_opportunities,
    list_rules,
    list_sites,
)
from seo_ops.services.ai import AIUnavailable, explain_opportunity
from seo_ops.services.data_quality import assess_gsc_quality
from seo_ops.services.external_sources import (
    SOURCE_CATALOG,
    configured_sources,
    test_source_connection,
)
from seo_ops.services.settings_store import (
    NON_SECRET_FIELDS,
    SECRET_FIELDS,
    update_local_settings,
)
from seo_ops.utils import json_dumps, utc_now

PACKAGE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


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
        raise HTTPException(status_code=403, detail="设置只能从本机页面修改")


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

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
        return {
            "request": request,
            "version": __version__,
            "ai_enabled": active_settings.ai_enabled,
            "message": request.query_params.get("message"),
            "message_level": request.query_params.get("level", "success"),
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
    async def dashboard(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            summary = dashboard_summary(conn, site_id)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=page_context(
                request,
                page="dashboard",
                page_title="今日工作台",
                site=site,
                sites=sites,
                summary=summary,
            ),
        )

    @app.get("/imports")
    async def imports_page(request: Request):
        site_id = active_site_id(request)
        with connection(active_settings) as conn:
            site = get_site(conn, site_id)
            sites = list_sites(conn)
            history = list_imports(conn, site_id)
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
            ),
        )

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
        return _redirect("/imports", message, "error" if failures else "success")

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
            opportunities = list_opportunities(conn, site_id)
        return templates.TemplateResponse(
            request=request,
            name="opportunities.html",
            context=page_context(
                request,
                page="opportunities",
                page_title="机会与任务",
                site=site,
                sites=sites,
                opportunities=opportunities,
            ),
        )

    @app.post("/opportunities/{opportunity_id}/decision")
    async def decide_opportunity(
        opportunity_id: int,
        decision: str = Form(...),
        reason: str = Form(""),
    ):
        if decision not in {"accepted", "rejected"}:
            return _redirect("/opportunities", "不支持的决定", "error")
        with connection(active_settings) as conn:
            opportunity = get_opportunity(conn, opportunity_id)
            if not opportunity:
                return _redirect("/opportunities", "机会不存在", "error")
            conn.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?",
                (decision, opportunity_id),
            )
            conn.execute(
                """
                INSERT INTO actions(
                    opportunity_id, site_id, action_type, target_ref, decision,
                    decision_reason, planned_change, baseline_json, decided_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id,
                    opportunity["site_id"],
                    opportunity["opportunity_type"],
                    opportunity["target_ref"],
                    decision,
                    reason.strip() or None,
                    opportunity["recommended_action"],
                    json_dumps(opportunity["evidence"].get("current", {})),
                    utc_now(),
                ),
            )
        label = "已接受并建立基线" if decision == "accepted" else "已拒绝并记录原因"
        return _redirect("/opportunities", label)

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
                configured_count=sum(configured.values()),
                connections=connections,
                source_catalog=SOURCE_CATALOG,
                gsc_quality=gsc_quality,
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
