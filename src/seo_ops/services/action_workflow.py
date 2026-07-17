from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.repositories import get_opportunity
from seo_ops.rules.evidence_workflow import PLAN_VERSION
from seo_ops.utils import json_dumps, json_loads, utc_now


class ActionWorkflowError(ValueError):
    """Raised when an action transition would bypass a workflow boundary."""


@dataclass(frozen=True, slots=True)
class ActionDecisionOutcome:
    action_id: int
    decision: str
    created: bool
    message: str


def _step(
    key: str,
    phase: str,
    title: str,
    instructions: str,
    *,
    requires_human: bool = True,
) -> dict[str, Any]:
    return {
        "step_key": key,
        "phase": phase,
        "title": title,
        "instructions": instructions,
        "requires_human": requires_human,
    }


def _evidence_plan(opportunity: dict[str, Any]) -> list[dict[str, Any]]:
    if opportunity["target_kind"] == "query":
        return [
            _step(
                "collect_external_evidence",
                "补证据",
                "采集当前 SERP 与 Trends",
                "回到机会页点击“自动补 SERP + Trends”。该动作会明确消耗 SerpAPI 调用；24 小时内同口径复用。",
            ),
            _step(
                "export_query_page",
                "补证据",
                "导出定向 query→page 数据",
                "在 GSC 中筛选该查询，再导出网页维度；不得用标题或关键词重合替代联合数据。",
            ),
            _step(
                "review_overlap_and_intent",
                "门槛复核",
                "复核现有内容与搜索意图",
                "对照 SERP、CMS 相似内容和定向 GSC 页面，判断应更新、新建还是忽略。",
            ),
            _step(
                "record_evidence_decision",
                "门槛复核",
                "记录结论并重跑分析",
                "把新增证据导入或登记后重跑分析；门槛未通过时不得进入写作或发布。",
            ),
        ]
    return [
        _step(
            "collect_next_gsc_window",
            "补证据",
            "补充独立 GSC 观察窗口",
            "等待并导入新的完整观察窗口；不要把同一时点的重复导出算作独立窗口。",
        ),
        _step(
            "export_page_queries",
            "补证据",
            "导出目标页的查询维度",
            "在 GSC 中筛选目标页面后导出查询，确认实际查询结构和设备/国家差异。",
        ),
        _step(
            "review_confounds",
            "门槛复核",
            "核对异常与外部干扰",
            "核对官方数据异常、排名更新、季节性和 SERP 变化，不使用“沙盒”作为自动诊断。",
        ),
        _step(
            "rerun_analysis",
            "门槛复核",
            "导入证据并重跑分析",
            "只有新一轮门槛通过后，才建立页面修改或内容执行任务。",
        ),
    ]


def _page_change_plan(opportunity: dict[str, Any]) -> list[dict[str, Any]]:
    diagnose_title = (
        "诊断点击损失构成"
        if opportunity["opportunity_type"] == "protect"
        else "确认实际查询与修改范围"
    )
    return [
        _step(
            "confirm_scope",
            "诊断",
            diagnose_title,
            "用定向 GSC 查询、当前页面和已有证据确认问题范围；先区分展示、排名与 CTR。",
        ),
        _step(
            "draft_change",
            "准备",
            "形成最小修改清单",
            "只列运营者有权限完成的标题、描述、正文或内链改动，并说明每项对应的 evidence ID。",
        ),
        _step(
            "verify_facts",
            "准备",
            "核对事实、来源与重叠风险",
            "确认作者、实测、案例和引用真实可追溯；检查是否与现有内容重复。",
        ),
        _step(
            "apply_cms_change",
            "执行",
            "人工修改 CMS",
            "由运营者在 CMS 中执行已确认的改动；系统不会自动发布、删除或修改站点。",
        ),
        _step(
            "qa_and_publish",
            "执行",
            "预览、检查并人工发布",
            "检查链接、移动端阅读、标题和页面状态后，由运营者做最终发布决定。",
        ),
        _step(
            "schedule_observation",
            "复盘",
            "保留基线并安排观察",
            "记录实际修改与发布日期，准备 7/28/56 天观察；不要用单日变化判断成败。",
        ),
    ]


def _create_plan() -> list[dict[str, Any]]:
    return [
        _step(
            "build_research_brief",
            "研究",
            "建立带 evidence ID 的 research brief",
            "明确受众问题、页面形式、已有内容边界和仍缺少的事实；不把 AI 摘要当来源。",
        ),
        _step(
            "verify_primary_sources",
            "研究",
            "核验官方或一手来源",
            "逐项回到原始来源，记录作者、发布日期、URL 和可支持的具体事实。",
        ),
        _step(
            "draft_content",
            "制作",
            "形成原创草稿",
            "围绕用户任务提供独特价值；按已确认文章类型使用相应篇幅范围，不得虚构实测、案例或作者经历。",
        ),
        _step(
            "content_review",
            "审核",
            "检查事实、引用、重复和权限",
            "核对每个重要事实、链接与声明，复查现有页面重叠，并删除无法追溯的内容。",
        ),
        _step(
            "human_publish",
            "发布",
            "人工预览并发布",
            "由运营者在 CMS 中完成最终预览和发布；系统不自动发布。",
        ),
        _step(
            "schedule_observation",
            "复盘",
            "安排 7/28/56 天观察",
            "冻结发布前基线，记录同期算法、季节性和站点级变化，避免把相关性写成因果。",
        ),
    ]


def build_action_plan(opportunity: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        opportunity["gate_status"] == "needs_evidence"
        or opportunity["opportunity_type"] == "evidence"
    ):
        return _evidence_plan(opportunity)
    if opportunity["opportunity_type"] == "create":
        return _create_plan()
    return _page_change_plan(opportunity)


def _baseline_metrics(evidence: dict[str, Any]) -> dict[str, Any]:
    current = evidence.get("current")
    if isinstance(current, dict):
        return current
    facts = evidence.get("facts")
    if isinstance(facts, dict):
        gsc_query = facts.get("gsc_query")
        if isinstance(gsc_query, dict):
            return gsc_query
    return {}


def record_opportunity_decision(
    opportunity_id: int,
    decision: str,
    reason: str = "",
    settings: Settings | None = None,
) -> ActionDecisionOutcome:
    active_settings = settings or get_settings()
    if decision not in {"accepted", "rejected"}:
        raise ActionWorkflowError("不支持的决定")
    with connection(active_settings) as conn:
        opportunity = get_opportunity(conn, opportunity_id)
        if not opportunity:
            raise ActionWorkflowError("机会不存在")
        if decision == "accepted" and opportunity["gate_status"] == "blocked":
            raise ActionWorkflowError("该主题已阻塞，不能执行；请直接忽略或拒绝")
        if decision == "accepted" and opportunity["gate_status"] == "needs_evidence":
            raise ActionWorkflowError("这只是分析线索，还不能执行；请继续调研或直接忽略")
        existing = conn.execute(
            """
            SELECT id FROM actions
            WHERE opportunity_id = ? AND decision = ?
            ORDER BY id DESC LIMIT 1
            """,
            (opportunity_id, decision),
        ).fetchone()
        if existing:
            if decision == "accepted":
                step_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM action_steps WHERE action_id = ?",
                    (existing["id"],),
                ).fetchone()["count"]
                if not step_count:
                    now = utc_now()
                    evidence_refs = [
                        str(item) for item in opportunity["evidence"].get("evidence_ids", [])
                    ]
                    for index, step in enumerate(build_action_plan(opportunity), start=1):
                        conn.execute(
                            """
                            INSERT INTO action_steps(
                                action_id, step_order, step_key, phase, title, instructions,
                                requires_human, evidence_refs_json, updated_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                existing["id"],
                                index,
                                step["step_key"],
                                step["phase"],
                                step["title"],
                                step["instructions"],
                                int(step["requires_human"]),
                                json_dumps(evidence_refs),
                                now,
                            ),
                        )
                    conn.execute(
                        """
                        UPDATE actions
                        SET plan_version = ?, workflow_status = 'planned', updated_at = ?
                        WHERE id = ?
                        """,
                        (PLAN_VERSION, now, existing["id"]),
                    )
                    return ActionDecisionOutcome(
                        int(existing["id"]),
                        decision,
                        False,
                        "已为旧决定补建当前执行方案",
                    )
            label = "执行方案已存在" if decision == "accepted" else "拒绝记录已存在"
            return ActionDecisionOutcome(int(existing["id"]), decision, False, label)

        now = utc_now()
        baseline = {
            "captured_at": now,
            "method_version": opportunity["method_version"],
            "evidence_ids": opportunity["evidence"].get("evidence_ids", []),
            "metrics": _baseline_metrics(opportunity["evidence"]),
        }
        workflow_status = "planned" if decision == "accepted" else "cancelled"
        cursor = conn.execute(
            """
            INSERT INTO actions(
                opportunity_id, site_id, action_type, target_ref, decision,
                decision_reason, planned_change, baseline_json, decided_at,
                workflow_status, plan_version, updated_at, completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                opportunity["site_id"],
                opportunity["opportunity_type"],
                opportunity["target_ref"],
                decision,
                reason.strip() or None,
                opportunity["recommended_action"],
                json_dumps(baseline),
                now,
                workflow_status,
                PLAN_VERSION if decision == "accepted" else None,
                now,
                now if decision == "rejected" else None,
            ),
        )
        action_id = int(cursor.lastrowid)
        if decision == "accepted":
            evidence_refs = [str(item) for item in baseline["evidence_ids"]]
            for index, step in enumerate(build_action_plan(opportunity), start=1):
                conn.execute(
                    """
                    INSERT INTO action_steps(
                        action_id, step_order, step_key, phase, title, instructions,
                        requires_human, evidence_refs_json, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        index,
                        step["step_key"],
                        step["phase"],
                        step["title"],
                        step["instructions"],
                        int(step["requires_human"]),
                        json_dumps(evidence_refs),
                        now,
                    ),
                )
            conn.execute(
                "UPDATE opportunities SET status = 'accepted' WHERE id = ?",
                (opportunity_id,),
            )
            message = "已移到第 4 步“文章制作”"
        else:
            conn.execute(
                "UPDATE opportunities SET status = 'rejected' WHERE id = ?",
                (opportunity_id,),
            )
            message = "已拒绝并记录原因"
        return ActionDecisionOutcome(action_id, decision, True, message)


def update_action_step(
    action_id: int,
    step_id: int,
    *,
    completed: bool,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        action = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not action or action["decision"] != "accepted":
            raise ActionWorkflowError("执行方案不存在")
        if action["workflow_status"] == "cancelled":
            raise ActionWorkflowError("已取消的方案不能更新步骤；请先恢复方案")
        step = conn.execute(
            "SELECT * FROM action_steps WHERE id = ? AND action_id = ?",
            (step_id, action_id),
        ).fetchone()
        if not step:
            raise ActionWorkflowError("执行步骤不存在")
        now = utc_now()
        if completed:
            if step["step_key"] == "collect_external_evidence":
                evidence_row = conn.execute(
                    "SELECT evidence_json FROM opportunities WHERE id = ?",
                    (action["opportunity_id"],),
                ).fetchone()
                evidence = json_loads(evidence_row["evidence_json"], {}) if evidence_row else {}
                external = evidence.get("external_evidence") or {}
                if "serp_snapshot" not in external:
                    raise ActionWorkflowError(
                        "请先回到机会页完成 SERP 补证据；没有 evidence ID 不能跳过该步骤"
                    )
                evidence_refs = json_loads(step["evidence_refs_json"], [])
                for item in external.values():
                    evidence_id = item.get("evidence_id") if isinstance(item, dict) else None
                    if evidence_id and evidence_id not in evidence_refs:
                        evidence_refs.append(evidence_id)
                conn.execute(
                    "UPDATE action_steps SET evidence_refs_json = ? WHERE id = ?",
                    (json_dumps(evidence_refs), step_id),
                )
            unfinished_before = conn.execute(
                """
                SELECT COUNT(*) AS count FROM action_steps
                WHERE action_id = ? AND step_order < ? AND status != 'done'
                """,
                (action_id, step["step_order"]),
            ).fetchone()["count"]
            if unfinished_before:
                raise ActionWorkflowError("请按顺序完成前面的步骤")
            conn.execute(
                """
                UPDATE action_steps
                SET status = 'done', completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, step_id),
            )
        else:
            conn.execute(
                """
                UPDATE action_steps
                SET status = 'pending', completed_at = NULL, updated_at = ?
                WHERE action_id = ? AND step_order >= ?
                """,
                (now, action_id, step["step_order"]),
            )

        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done
            FROM action_steps WHERE action_id = ?
            """,
            (action_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        done = int(counts["done"] or 0)
        if total and done == total:
            workflow_status = "completed"
            opportunity_status = "done"
            completed_at = now
        elif done:
            workflow_status = "in_progress"
            opportunity_status = "in_progress"
            completed_at = None
        else:
            workflow_status = "planned"
            opportunity_status = "accepted"
            completed_at = None
        publish_done = conn.execute(
            """
            SELECT COUNT(*) AS count FROM action_steps
            WHERE action_id = ? AND step_key IN ('human_publish','qa_and_publish')
              AND status = 'done'
            """,
            (action_id,),
        ).fetchone()["count"]
        execution_done = conn.execute(
            """
            SELECT COUNT(*) AS count FROM action_steps
            WHERE action_id = ?
              AND step_key IN (
                  'apply_cms_change','human_publish',
                  'record_evidence_decision','rerun_analysis'
              )
              AND status = 'done'
            """,
            (action_id,),
        ).fetchone()["count"]
        executed_at = (action["executed_at"] or now) if execution_done else None
        published_at = (action["published_at"] or now) if publish_done else None
        conn.execute(
            """
            UPDATE actions
            SET workflow_status = ?, updated_at = ?, executed_at = ?,
                completed_at = ?, published_at = ?
            WHERE id = ?
            """,
            (
                workflow_status,
                now,
                executed_at,
                completed_at,
                published_at,
                action_id,
            ),
        )
        if action["opportunity_id"]:
            conn.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?",
                (opportunity_status, action["opportunity_id"]),
            )
        return f"步骤进度已更新：{done}/{total}"


def set_action_cancelled(
    action_id: int,
    *,
    cancelled: bool,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        action = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not action or action["decision"] != "accepted":
            raise ActionWorkflowError("执行方案不存在")
        now = utc_now()
        if cancelled:
            workflow_status = "cancelled"
            opportunity_status = "cancelled"
            completed_at = now
            message = "执行方案已取消；已有快照和决定记录仍保留"
        else:
            counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done
                FROM action_steps WHERE action_id = ?
                """,
                (action_id,),
            ).fetchone()
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            if total and done == total:
                workflow_status = "completed"
                opportunity_status = "done"
                completed_at = action["completed_at"] or now
            elif done:
                workflow_status = "in_progress"
                opportunity_status = "in_progress"
                completed_at = None
            else:
                workflow_status = "planned"
                opportunity_status = "accepted"
                completed_at = None
            message = "执行方案已恢复"
        conn.execute(
            """
            UPDATE actions SET workflow_status = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (workflow_status, now, completed_at, action_id),
        )
        if action["opportunity_id"]:
            conn.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?",
                (opportunity_status, action["opportunity_id"]),
            )
        return message


def set_action_published(
    action_id: int,
    *,
    published: bool,
    settings: Settings | None = None,
) -> str:
    """Close or reopen an article task without exposing the internal step list."""

    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        action = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not action or action["decision"] != "accepted":
            raise ActionWorkflowError("文章任务不存在")
        if action["workflow_status"] == "cancelled":
            raise ActionWorkflowError("已取消的任务不能标记发布；请先恢复任务")
        if published and not action["actual_change"]:
            raise ActionWorkflowError("请先生成并核对文章内容，再标记为已发布")

        now = utc_now()
        if published:
            conn.execute(
                """
                UPDATE action_steps
                SET status = 'done', completed_at = COALESCE(completed_at, ?), updated_at = ?
                WHERE action_id = ?
                """,
                (now, now, action_id),
            )
            conn.execute(
                """
                UPDATE actions
                SET workflow_status = 'completed', executed_at = COALESCE(executed_at, ?),
                    published_at = COALESCE(published_at, ?), completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, now, now, action_id),
            )
            opportunity_status = "done"
            message = "已记录发布；任务已从制作页移除，重新导入 CMS 后图谱会按真实页面更新"
        else:
            publish_keys = (
                "apply_cms_change",
                "human_publish",
                "qa_and_publish",
                "schedule_observation",
            )
            placeholders = ",".join("?" for _ in publish_keys)
            conn.execute(
                f"""
                UPDATE action_steps
                SET status = 'pending', completed_at = NULL, updated_at = ?
                WHERE action_id = ? AND step_key IN ({placeholders})
                """,
                (now, action_id, *publish_keys),
            )
            conn.execute(
                """
                UPDATE actions
                SET workflow_status = 'in_progress', executed_at = NULL,
                    published_at = NULL, completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, action_id),
            )
            opportunity_status = "in_progress"
            message = "文章任务已重新打开"

        if action["opportunity_id"]:
            conn.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?",
                (opportunity_status, action["opportunity_id"]),
            )
        return message
