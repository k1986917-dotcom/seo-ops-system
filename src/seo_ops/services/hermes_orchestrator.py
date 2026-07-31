"""Hermes-facing orchestration for the Legacy Research + Write pipeline.

The service owns the persistent action and all workspace writes.  Hermes only
uses the HTTP surface exposed by :mod:`seo_ops.web.app`: it never needs to
guess a stage, edit a draft, or touch SQLite directly.

Hermes must not write the SQLite database or Legacy workspace itself. The
HTTP route calls these functions, while this module is the only place that
translates new-system external search payloads into the old R1 text format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.repositories import get_site
from seo_ops.services.external_evidence import (
    EvidenceCollectionUnavailable,
    ExternalRunResult,
    collect_topic_query_evidence,
    execute_tavily_search,
)
from seo_ops.services.legacy_sync import sync_all as legacy_sync_all
from seo_ops.services.legacy_workflow import (
    action_workspace,
    generate_topic_context_from_research,
    get_legacy_display_data,
    stage_r0_generate_prompt,
    stage_r1_save_and_collect,
    stage_r3_ai_analyze,
    stage_w0_validate_and_draft,
    stage_w1b_pre_check,
    stage_w1b_revise_batch,
    stage_w2_post_process,
    stage_w2_revise_batch,
    stage_w3_register,
)
from seo_ops.utils import json_dumps, json_loads, utc_now


class HermesOrchestrationError(ValueError):
    """Raised when Hermes intake cannot be accepted safely."""


@dataclass(frozen=True, slots=True)
class HermesAction:
    action_id: int
    site_id: int
    topic: str
    requirements: str
    reused: bool = False


@dataclass(frozen=True, slots=True)
class HermesSearchOutcome:
    status: str
    query: str
    markdown: str
    providers: tuple[dict[str, Any], ...]
    message: str


@dataclass(frozen=True, slots=True)
class HermesBootstrapOutcome:
    action_id: int
    status: str
    stage: str
    message: str
    search: HermesSearchOutcome
    r1: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class HermesPipelineOutcome:
    """The truthful stopping point of one Hermes-controlled run."""

    action_id: int
    status: str
    stage: str
    message: str
    steps: tuple[dict[str, Any], ...]
    waiting_for: str | None = None


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _resolve_site(site: Any, settings: Settings) -> dict[str, Any]:
    if isinstance(site, bool):
        raise HermesOrchestrationError("site 必须是站点 ID 或 slug")
    with connection(settings) as conn:
        if isinstance(site, int) or (isinstance(site, str) and site.strip().isdigit()):
            row = get_site(conn, int(site))
        else:
            value = _clean_text(site, 120)
            row = conn.execute("SELECT * FROM sites WHERE slug = ?", (value,)).fetchone()
            row = dict(row) if row else None
    if not row:
        raise HermesOrchestrationError("站点不存在；请提供有效的 site_id 或站点 slug")
    return dict(row)


def create_or_resume_hermes_action(
    *,
    site: Any,
    topic: str,
    requirements: str = "",
    restart: bool = False,
    settings: Settings | None = None,
) -> HermesAction:
    """Create an accepted create-action, or reuse its current active action.

    ``restart=True`` deliberately reuses the action ID and lets R0 clear its
    persistent workspace. This preserves the existing "R0 is the only reset"
    behavior and avoids creating hidden attempts for a single user task.
    """

    active_settings = settings or get_settings()
    resolved_site = _resolve_site(site, active_settings)
    cleaned_topic = _clean_text(topic, 300)
    if not cleaned_topic:
        raise HermesOrchestrationError("topic 不能为空")
    cleaned_requirements = _clean_text(requirements, 2000)
    now = utc_now()
    baseline = {
        "source": "hermes",
        "requirements": cleaned_requirements,
        "intake_version": "hermes-bootstrap-0.1.0",
        "captured_at": now,
    }
    with connection(active_settings) as conn:
        existing = conn.execute(
            """
            SELECT id, site_id, target_ref, baseline_json
            FROM actions
            WHERE site_id = ? AND action_type = 'create'
              AND target_ref = ? AND decision = 'accepted'
              AND workflow_status IN ('planned','in_progress')
            ORDER BY id DESC LIMIT 1
            """,
            (int(resolved_site["id"]), cleaned_topic),
        ).fetchone()
        if existing and not restart:
            stored = json_loads(existing["baseline_json"], {})
            return HermesAction(
                int(existing["id"]),
                int(existing["site_id"]),
                str(existing["target_ref"]),
                _clean_text(stored.get("requirements"), 2000),
                reused=True,
            )
        if existing:
            action_id = int(existing["id"])
            conn.execute(
                """
                UPDATE actions
                SET decision_reason = ?, planned_change = ?, baseline_json = ?,
                    workflow_status = 'in_progress', legacy_stage = 'r0_pending',
                    updated_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (
                    "Hermes automatic intake (restarted)",
                    "Hermes R0 → external search → R1 bootstrap",
                    json_dumps(baseline),
                    now,
                    action_id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO actions(
                    opportunity_id, site_id, action_type, target_ref, decision,
                    decision_reason, planned_change, baseline_json, decided_at,
                    workflow_status, plan_version, legacy_stage, updated_at
                ) VALUES(NULL, ?, 'create', ?, 'accepted', ?, ?, ?, ?,
                         'in_progress', 'hermes-bootstrap-0.1.0',
                         'r0_pending', ?)
                """,
                (
                    int(resolved_site["id"]),
                    cleaned_topic,
                    "Hermes automatic intake",
                    "Hermes R0 → external search → R1 bootstrap",
                    json_dumps(baseline),
                    now,
                    now,
                ),
            )
            action_id = int(cursor.lastrowid)
        conn.commit()
    return HermesAction(
        action_id,
        int(resolved_site["id"]),
        cleaned_topic,
        cleaned_requirements,
        reused=bool(existing),
    )


def _bounded_query(topic: str, requirements: str) -> str:
    query = _clean_text(" ".join(part for part in (topic, requirements) if part), 400)
    if len(query) <= 100:
        return query
    shortened = query[:101].rsplit(" ", 1)[0].strip()
    return shortened or query[:100]


def _result_lines(result: ExternalRunResult) -> list[str]:
    payload = result.payload or {}
    lines: list[str] = [
        f"### {result.purpose} ({result.evidence_id or 'no evidence ID'})",
        f"- Provider status: {result.status}",
        f"- Message: {result.message}",
    ]
    organic = payload.get("organic_results") or []
    for item in organic:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), 300)
        link = _clean_text(item.get("link"), 500)
        if title or link:
            lines.append(f"- [{title or link}]({link})")
    for question in payload.get("related_questions") or []:
        value = _clean_text(question, 500)
        if value:
            lines.append(f"- Related question: {value}")
    for query in payload.get("related_searches") or []:
        value = _clean_text(query, 500)
        if value:
            lines.append(f"- Related search: {value}")
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), 300)
        url = _clean_text(item.get("url") or item.get("link"), 500)
        content = _clean_text(item.get("content") or item.get("snippet"), 1200)
        if title or url:
            lines.append(f"- [{title or url}]({url})")
        if content:
            lines.append(f"  - Snippet: {content}")
    return lines


async def collect_hermes_search_results(
    *,
    site_id: int,
    topic: str,
    requirements: str = "",
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HermesSearchOutcome:
    """Collect currently configured new-system search evidence for Legacy R1."""

    active_settings = settings or get_settings()
    query = _bounded_query(topic, requirements)
    with connection(active_settings) as conn:
        site = get_site(conn, site_id)
    if not site:
        raise HermesOrchestrationError("站点不存在")

    results: list[ExternalRunResult] = []
    errors: list[str] = []
    if active_settings.serpapi_api_key:
        try:
            outcome = await collect_topic_query_evidence(
                site_id,
                query,
                1,
                active_settings,
                transport=transport,
            )
            results.extend(outcome.runs)
        except EvidenceCollectionUnavailable as exc:
            errors.append(f"SerpAPI: {exc}")
    if active_settings.tavily_api_key:
        try:
            results.append(
                await execute_tavily_search(
                    active_settings,
                    site=site,
                    opportunity_id=None,
                    query=query,
                    input_refs=[],
                    transport=transport,
                )
            )
        except EvidenceCollectionUnavailable as exc:
            errors.append(f"Tavily: {exc}")

    successful = [item for item in results if item.status == "success" and item.payload]
    providers = tuple(
        {
            "provider": "serpapi" if item.purpose in {"serp_snapshot", "trends_timeseries"} else "tavily",
            "purpose": item.purpose,
            "status": item.status,
            "reused": item.reused,
            "evidence_id": item.evidence_id,
            "message": item.message,
        }
        for item in results
    )
    if not successful:
        message = (
            "没有可用的自动搜索结果；请由 Hermes 报告现状并询问是否粘贴外部搜索结果。"
        )
        if errors:
            message += " " + "；".join(errors)
        return HermesSearchOutcome("needs_manual_search", query, "", providers, message)

    lines = [
        "# Hermes automatic search results",
        f"- Query: {query}",
        "- These are provider snapshots used as R1 input; snippets are discovery aids, not final facts.",
        "",
    ]
    for result in successful:
        lines.extend(_result_lines(result))
        lines.append("")
    if errors:
        lines.extend(["## Provider warnings", *[f"- {error}" for error in errors], ""])
    return HermesSearchOutcome(
        "success" if not errors else "partial",
        query,
        "\n".join(lines).strip() + "\n",
        providers,
        "已取得自动搜索结果，可交给 Legacy R1 收集；外部摘要仍需后续来源核验。",
    )


async def run_hermes_bootstrap(
    action: HermesAction,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HermesBootstrapOutcome:
    """Run the first real automated slice: R0 -> search -> R1."""

    active_settings = settings or get_settings()
    workspace_root = active_settings.data_dir / "legacy_workflow" / "laserpointerhub"
    with connection(active_settings) as conn:
        current_row = conn.execute(
            "SELECT legacy_stage FROM actions WHERE id = ?", (action.action_id,)
        ).fetchone()
    current_stage = str(current_row["legacy_stage"] or "") if current_row else ""
    # An idempotent retry must not wipe a task that has already reached a later
    # stage. Explicit restart resets legacy_stage to r0_pending above.
    if action.reused and current_stage not in {"", "r0_pending", "r0_prompt", "r1_results"}:
        empty_search = HermesSearchOutcome(
            "not_needed",
            "",
            "",
            (),
            "任务已有后续阶段产物，保留现状并返回当前阶段。",
        )
        return HermesBootstrapOutcome(
            action.action_id,
            "resumed",
            current_stage,
            "已恢复现有任务状态，没有重置后续产物。",
            empty_search,
            None,
        )
    legacy_sync_all(settings=active_settings, site_id=action.site_id)
    workspace = action_workspace(workspace_root, action.action_id)
    if current_stage in {"", "r0_pending"}:
        stage_r0_generate_prompt(action.topic, workspace, action.requirements)
        generate_topic_context_from_research(
            action.topic,
            workspace,
            action_id=action.action_id,
            operator_requirements=action.requirements,
        )
        with connection(active_settings) as conn:
            conn.execute(
                "UPDATE actions SET legacy_stage = 'r0_prompt', updated_at = ? WHERE id = ?",
                (utc_now(), action.action_id),
            )
            conn.commit()

    search = await collect_hermes_search_results(
        site_id=action.site_id,
        topic=action.topic,
        requirements=action.requirements,
        settings=active_settings,
        transport=transport,
    )
    if not search.markdown:
        return HermesBootstrapOutcome(
            action.action_id,
            "needs_manual_search",
            "r0_prompt",
            search.message,
            search,
            None,
        )

    r1 = await stage_r1_save_and_collect(action.topic, search.markdown, workspace)
    stage = str(r1.get("stage") or "r1_results")
    with connection(active_settings) as conn:
        conn.execute(
            "UPDATE actions SET legacy_stage = ?, updated_at = ? WHERE id = ?",
            (stage, utc_now(), action.action_id),
        )
        conn.commit()
    if not r1.get("success"):
        return HermesBootstrapOutcome(
            action.action_id,
            "failed",
            stage,
            str(r1.get("error") or "Legacy R1 收集失败"),
            search,
            r1,
        )
    return HermesBootstrapOutcome(
        action.action_id,
        search.status,
        stage,
        "R0 已完成；自动搜索结果已交给 Legacy R1。下一步可继续 R3。",
        search,
        r1,
    )


def _action_for_pipeline(action_id: int, settings: Settings) -> dict[str, Any]:
    with connection(settings) as conn:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise HermesOrchestrationError("action 不存在")
    item = dict(row)
    if item.get("action_type") != "create" or item.get("decision") != "accepted":
        raise HermesOrchestrationError("该 action 不是可继续的已接受新文章任务")
    if not _clean_text(item.get("target_ref"), 300):
        raise HermesOrchestrationError("action 缺少文章主题")
    return item


def _update_pipeline_stage(action_id: int, stage: str, settings: Settings) -> None:
    with connection(settings) as conn:
        conn.execute(
            "UPDATE actions SET legacy_stage = ?, updated_at = ? WHERE id = ?",
            (stage, utc_now(), action_id),
        )
        conn.commit()


def _existing_material_summary(workspace: Path, topic: str) -> dict[str, Any]:
    """Describe synced material without pretending it is fresh web research.

    This is deliberately a report for a human decision.  It never promotes a
    library item to a verified fact, and it only becomes R1 input after the
    operator explicitly selects ``use_existing``.
    """
    files = {
        "topic_context": workspace / "context" / "topic-context.json",
        "pain_points": workspace / "context" / "pain-points-library.md",
        "case_studies": workspace / "context" / "case-studies-library.md",
        "sources": workspace / "context" / "external-sources-library.md",
        "published": workspace / "published" / "published-index.md",
    }
    available: list[dict[str, Any]] = []
    excerpts: list[str] = []
    for name, path in files.items():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        available.append({"kind": name, "characters": len(text)})
        # The source libraries are already the user's synced evidence. Keep
        # only a bounded excerpt in the decision response.
        excerpts.append(f"## Existing {name}\n{text[:1800]}")
    return {
        "topic": topic,
        "available": available,
        "source_count": len(available),
        "summary": "\n\n".join(excerpts)[:7000],
        "can_continue": bool(available),
        "notice": (
            "这些是已同步的站内上下文/资料库，不是新搜索结果；选择继续不会伪造外部资料。"
            "后续 W1b/W2 的证据门仍会照常拦截资料不足的草稿。"
        ),
    }


def existing_material_summary(action_id: int, settings: Settings | None = None) -> dict[str, Any]:
    active_settings = settings or get_settings()
    action = _action_for_pipeline(action_id, active_settings)
    workspace = action_workspace(
        active_settings.data_dir / "legacy_workflow" / "laserpointerhub", action_id
    )
    return _existing_material_summary(workspace, str(action["target_ref"]))


def _existing_material_r1_input(summary: dict[str, Any]) -> str:
    """Create an explicitly labelled R1 input from already-synced material."""
    return (
        "# Hermes existing-material continuation\n\n"
        "This input contains only material already present in the SEO Ops workspace. "
        "It is not a substitute for a live SERP or new external evidence.\n\n"
        f"Topic: {summary['topic']}\n\n"
        f"{summary['summary']}\n"
    )


async def continue_hermes_run(
    action_id: int,
    *,
    search_decision: str | None = None,
    search_results: str = "",
    author: str = "LaserPointerHub",
    settings: Settings | None = None,
) -> HermesPipelineOutcome:
    """Resume one action through all safe automatic stages.

    One call may execute R3→W3.  A quality failure triggers exactly one
    two-round AI batch for the affected gate, then returns a persisted pause.
    A later explicit call starts a new batch; this avoids an invisible infinite
    loop while retaining the prior failure memory for the next revision prompt.
    """
    active_settings = settings or get_settings()
    action = _action_for_pipeline(action_id, active_settings)
    topic = str(action["target_ref"])
    workspace = action_workspace(
        active_settings.data_dir / "legacy_workflow" / "laserpointerhub", action_id
    )
    steps: list[dict[str, Any]] = []

    def record(name: str, result: dict[str, Any]) -> None:
        steps.append({
            "step": name,
            "success": bool(result.get("success", result.get("gate_passed", False))),
            "stage": str(result.get("stage") or ""),
            "error": str(result.get("error") or ""),
            "batch_attempts": result.get("batch_attempts"),
        })

    stage = str(action.get("legacy_stage") or "r0_pending")
    if stage == "r0_prompt":
        if search_decision == "manual_search":
            if not _clean_text(search_results, 100000):
                return HermesPipelineOutcome(
                    action_id, "waiting", stage,
                    "已等待人工搜索结果；请粘贴完整结果后继续。", tuple(steps), "manual_search",
                )
            r1_input = search_results
        elif search_decision == "use_existing":
            summary = _existing_material_summary(workspace, topic)
            if not summary["can_continue"]:
                return HermesPipelineOutcome(
                    action_id, "waiting", stage,
                    "当前没有足以提交给 Legacy R1 的已同步材料；请人工粘贴搜索结果。",
                    tuple(steps), "manual_search",
                )
            r1_input = _existing_material_r1_input(summary)
        else:
            return HermesPipelineOutcome(
                action_id, "waiting", stage,
                "自动搜索不可用。请明确选择使用已有资料继续，或人工粘贴搜索结果。",
                tuple(steps), "search_decision",
            )
        r1 = await stage_r1_save_and_collect(topic, r1_input, workspace)
        record("r1", r1)
        _update_pipeline_stage(action_id, str(r1.get("stage") or "r1_results"), active_settings)
        if not r1.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(r1.get("stage") or "r1_results"),
                str(r1.get("error") or "Legacy R1 收集失败"), tuple(steps), "repair_input",
            )
        stage = "r2_collect"

    if stage == "r2_collect":
        r3 = await stage_r3_ai_analyze(topic, workspace, active_settings)
        record("r3", r3)
        _update_pipeline_stage(action_id, str(r3.get("stage") or "r2_collect"), active_settings)
        if not r3.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(r3.get("stage") or "r2_collect"),
                str(r3.get("error") or "R3 AI 分析失败"), tuple(steps), "repair_research",
            )
        stage = "r5_write_ready"

    if stage == "r5_write_ready":
        w0 = await stage_w0_validate_and_draft(
            topic,
            author or "LaserPointerHub",
            workspace,
            active_settings,
            action_id=action_id,
        )
        record("w0", w0)
        _update_pipeline_stage(action_id, str(w0.get("stage") or "r5_write_ready"), active_settings)
        if not w0.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(w0.get("stage") or "r5_write_ready"),
                str(w0.get("error") or "W0 写作失败"), tuple(steps), "repair_draft",
            )
        stage = "w1_draft"

    if stage == "w1_draft":
        w1b = await stage_w1b_pre_check(topic, "", workspace)
        record("w1b", w1b)
        _update_pipeline_stage(action_id, str(w1b.get("stage") or "w1_draft"), active_settings)
        if not w1b.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(w1b.get("stage") or "w1_draft"),
                str(w1b.get("error") or "W1b 预检无法完成"), tuple(steps), "repair_draft",
            )
        stage = "w1b_pre_check"

    if stage == "w1b_pre_check":
        display = get_legacy_display_data(topic, workspace, db_stage="w1b_pre_check")
        if not display.get("precheck_passed"):
            w1b_batch = await stage_w1b_revise_batch(topic, "", workspace, active_settings)
            record("w1b_revise", w1b_batch)
            _update_pipeline_stage(action_id, str(w1b_batch.get("stage") or "w1b_pre_check"), active_settings)
            if not w1b_batch.get("gate_passed"):
                return HermesPipelineOutcome(
                    action_id, "paused", "w1b_pre_check",
                    str(w1b_batch.get("error") or "W1b 两轮自动修订后仍未通过"),
                    tuple(steps), "w1b_retry_or_restart",
                )

        w2 = await stage_w2_post_process(topic, workspace)
        record("w2", w2)
        _update_pipeline_stage(action_id, str(w2.get("stage") or "w1b_pre_check"), active_settings)
        if not w2.get("gate_passed"):
            if w2.get("error"):
                return HermesPipelineOutcome(
                    action_id, "paused", "w1b_pre_check", str(w2["error"]), tuple(steps), "w2_review",
                )
            w2_batch = await stage_w2_revise_batch(topic, workspace, active_settings)
            record("w2_revise", w2_batch)
            _update_pipeline_stage(action_id, str(w2_batch.get("stage") or "w1b_pre_check"), active_settings)
            if not w2_batch.get("gate_passed"):
                return HermesPipelineOutcome(
                    action_id, "paused", str(w2_batch.get("stage") or "w1b_pre_check"),
                    str(w2_batch.get("error") or "W2 两轮自动修订后仍未通过"),
                    tuple(steps), "w2_retry_or_restart",
                )

        apply = await stage_w2_post_process(topic, workspace, apply=True)
        record("w2_apply", apply)
        _update_pipeline_stage(action_id, str(apply.get("stage") or "w1b_pre_check"), active_settings)
        if not apply.get("gate_passed") or not apply.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(apply.get("stage") or "w1b_pre_check"),
                str(apply.get("error") or "W2 写回失败"), tuple(steps), "w2_review",
            )
        stage = "w2_post_process"

    if stage == "w2_post_process":
        w3 = await stage_w3_register(topic, workspace, active_settings)
        record("w3", w3)
        _update_pipeline_stage(action_id, str(w3.get("stage") or "w2_post_process"), active_settings)
        if not w3.get("success"):
            return HermesPipelineOutcome(
                action_id, "failed", str(w3.get("stage") or "w2_post_process"),
                str(w3.get("error") or "W3 注册失败"), tuple(steps), "repair_registration",
            )
        return HermesPipelineOutcome(
            action_id, "completed", "w3_register", "文章已通过全部门禁并完成 W3 注册。", tuple(steps), None,
        )

    if stage == "w3_register":
        return HermesPipelineOutcome(
            action_id, "completed", stage, "任务此前已完成 W3 注册。", tuple(steps), None,
        )
    return HermesPipelineOutcome(
        action_id, "paused", stage, "当前阶段需要先修复输入或从 R0 重新开始。", tuple(steps), "review",
    )


def hermes_status(
    action_id: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise HermesOrchestrationError("action 不存在")
    item = dict(row)
    baseline = json_loads(item.get("baseline_json"), {})
    item["requirements"] = _clean_text(baseline.get("requirements"), 2000)
    item.pop("baseline_json", None)
    item.pop("decision_reason", None)
    item.pop("planned_change", None)
    return {
        "action_id": action_id,
        "site_id": int(item["site_id"]),
        "topic": str(item["target_ref"]),
        "workflow_status": item.get("workflow_status"),
        "legacy_stage": item.get("legacy_stage"),
        "requirements": item["requirements"],
        "next_stage": {
            "r0_prompt": "继续外部搜索或确认是否粘贴人工搜索结果",
            "r2_collect": "运行 R3 AI 分析",
            "r1_results": "修复搜索输入后重跑 R1",
        }.get(str(item.get("legacy_stage")), "按当前阶段报告继续"),
    }


def hermes_prompt(action_id: int, settings: Settings | None = None) -> dict[str, str]:
    """Return the generated R0 prompt without exposing workspace paths."""

    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        row = conn.execute(
            "SELECT site_id, target_ref, legacy_stage FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    if not row:
        raise HermesOrchestrationError("action 不存在")
    workspace = action_workspace(
        active_settings.data_dir / "legacy_workflow" / "laserpointerhub",
        action_id,
    )
    display = get_legacy_display_data(
        str(row["target_ref"]),
        workspace,
        db_stage=str(row["legacy_stage"] or ""),
    )
    prompt = str(display.get("prompt_content") or "")
    if not prompt:
        raise HermesOrchestrationError("该任务尚未生成 R0 搜索提示词")
    return {
        "action_id": str(action_id),
        "topic": str(row["target_ref"]),
        "legacy_stage": str(row["legacy_stage"] or ""),
        "prompt": prompt,
    }
