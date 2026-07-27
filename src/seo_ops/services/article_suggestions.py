from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.repositories import get_opportunity, latest_analysis_run
from seo_ops.services.action_workflow import (
    ActionWorkflowError,
    record_opportunity_decision,
)
from seo_ops.services.research_workflow import (
    ResearchDecisionError,
    record_human_review,
    record_research_candidate_decision,
    requalify_site_candidates,
)
from seo_ops.utils import json_loads, utc_now


@dataclass(frozen=True, slots=True)
class SuggestionDecisionOutcome:
    message: str
    action_id: int | None = None


class ArticleSuggestionError(ValueError):
    """Raised when a suggestion is no longer available for the requested decision."""


def old_article_decision_key(target_ref: str) -> str:
    return f"old::{target_ref.strip().casefold()}"[:300]


def _memory_hides(row: Any | None, skip_cutoff: str) -> bool:
    if not row:
        return False
    if row["decision"] == "dont_recommend":
        return True
    return row["decision"] == "skip" and str(row["updated_at"]) >= skip_cutoff


def _old_reason(rule_key: str) -> tuple[str, str]:
    if rule_key == "protect_click_loss":
        return "搜索点击较上一窗口明显下降", "先诊断，再决定局部修改或同主题重写"
    if rule_key == "site_relative_ctr":
        return "同类排名位置下，点击率相对偏低", "优先检查标题、描述和搜索意图"
    return "已有一定搜索表现，处于可继续提升的区间", "检查内容缺口、段落和内链"


def _split(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "featured": items[:2],
        "more": items[2:],
        "total": len(items),
    }


_RAW_RESEARCH_LEAD_PREFIXES = (
    "The current SERP exposed",
    "Tavily source discovery was completed",
    "Raw source lead:",
)
_SEMANTIC_CLUSTER_RE = re.compile(r"^Semantic cluster:\s*([a-z][a-z0-9_]{1,48})\.", re.I)

_FEEDBACK_STOPWORDS = {
    "a",
    "an",
    "and",
    "best",
    "for",
    "guide",
    "how",
    "laser",
    "pointer",
    "pointers",
    "the",
    "to",
    "use",
    "using",
    "versus",
    "vs",
    "what",
    "why",
    "with",
}


def _feedback_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in _FEEDBACK_STOPWORDS
    }


def _feedback_priority_adjustment(topic: str, decision_rows: list[Any]) -> float:
    """Apply old Plan-style preference memory as a soft rank signal only."""

    candidate_tokens = _feedback_tokens(topic)
    if not candidate_tokens:
        return 0.0
    preferred = 0.0
    rejected = 0.0
    for row in decision_rows:
        remembered = str(row["normalized_topic"] or "")
        if remembered.startswith("old::"):
            continue
        remembered_tokens = _feedback_tokens(remembered)
        shared = candidate_tokens & remembered_tokens
        if len(shared) < 2 or not remembered_tokens:
            continue
        similarity = len(shared) / min(len(candidate_tokens), len(remembered_tokens))
        if similarity < 0.5:
            continue
        if row["decision"] == "do":
            preferred = max(preferred, 8.0 * similarity)
        elif row["decision"] == "dont_recommend":
            rejected = max(rejected, 18.0 * similarity)
    return round(preferred - rejected, 1)


def _candidate_discovery_stage(item: dict[str, Any]) -> str:
    rationale = str(item.get("rationale") or "")
    if rationale.startswith(_RAW_RESEARCH_LEAD_PREFIXES):
        return "raw_lead"
    return "synthesized_angle"


def _candidate_semantic_cluster(item: dict[str, Any]) -> str:
    for value in item.get("inference") or []:
        match = _SEMANTIC_CLUSTER_RE.match(str(value).strip())
        if match:
            return match.group(1)
    return "emerging"


def _candidate_priority(item: dict[str, Any]) -> float:
    """Rank retained ideas by usefulness without turning readiness into a gate."""

    overlap = float((item.get("overlap") or {}).get("max_score") or 0)
    relationship = str((item.get("closest_existing") or {}).get("relationship") or "")
    demand_ready = bool((item.get("evidence_demand") or {}).get("ok"))
    material_ready = bool((item.get("evidence_material") or {}).get("ok"))
    synthesized = _candidate_discovery_stage(item) == "synthesized_angle"
    limitations_text = " ".join(str(value) for value in item.get("limitations") or [])
    if "偏离本轮核心对象" in limitations_text or "明确换成了相邻激光产品" in limitations_text:
        anchor_penalty = 20
    else:
        anchor_penalty = 0
    return round(
        35
        + min(len(item.get("evidence_refs") or []), 6) * 7
        + min(len(item.get("facts") or []), 5) * 3
        + min(len(item.get("source_urls") or []), 5) * 2
        - overlap * 20
        + (20 if demand_ready else 0)
        + (10 if material_ready else 0)
        + (10 if synthesized else 0)
        - (8 if relationship == "uncertain" else 0)
        - anchor_penalty,
        1,
    )


def list_article_suggestions(site_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """Refresh deterministic qualification and rank suggestions without external calls."""

    active = settings or get_settings()
    # Legacy evidence/review states are re-run through the current
    # duplicate-only rule here.  They must resolve into a final destination
    # (new article, existing article, or confirmed duplicate), rather than
    # becoming a hidden manual-review queue.
    requalify_site_candidates(site_id, active)
    skip_cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with connection(active) as conn:
        run = latest_analysis_run(conn, site_id)
        old_rows = []
        if run:
            old_rows = conn.execute(
                """
                SELECT * FROM opportunities
                WHERE analysis_run_id = ? AND site_id = ?
                  AND target_kind = 'blog'
                  AND opportunity_type IN ('protect','optimize')
                  AND gate_status = 'passed' AND status = 'proposed'
                ORDER BY priority DESC, id DESC
                """,
                (run["id"], site_id),
            ).fetchall()
        content_rows = conn.execute(
            """
            SELECT ci.canonical_url, ci.title, MAX(cs.captured_at) AS latest_content_at
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.content_item_id = ci.id
            WHERE ci.site_id = ? AND ci.content_type = 'blog'
            GROUP BY ci.id
            """,
            (site_id,),
        ).fetchall()
        content_by_url = {
            str(row["canonical_url"]).rstrip("/").casefold(): dict(row) for row in content_rows
        }
        decision_rows = conn.execute(
            "SELECT * FROM topic_decisions WHERE site_id = ?", (site_id,)
        ).fetchall()
        decisions = {str(row["normalized_topic"]): row for row in decision_rows}
        action_rows = conn.execute(
            """
            SELECT * FROM actions
            WHERE site_id = ? AND decision = 'accepted'
            ORDER BY id DESC
            """,
            (site_id,),
        ).fetchall()
        latest_action_by_target: dict[str, Any] = {}
        for row in action_rows:
            latest_action_by_target.setdefault(str(row["target_ref"]).rstrip("/").casefold(), row)
        active_gsc = conn.execute(
            """
            SELECT imported_at FROM imports
            WHERE site_id = ? AND source_type = 'gsc' AND status = 'success'
              AND analysis_active = 1 AND quality_eligible = 1
            ORDER BY imported_at DESC, id DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
        new_rows = conn.execute(
            """
            SELECT c.*, r.seed_type, r.topic_id AS research_topic_id,
                   r.dimension_key, r.completed_at AS researched_at
            FROM research_candidates c
            JOIN research_runs r ON r.id = c.research_run_id
            WHERE c.site_id = ? AND c.decision = 'pending'
              AND c.qualification_status = 'qualified'
              AND r.status IN ('success','partial')
            ORDER BY c.id DESC
            """,
            (site_id,),
        ).fetchall()
        latest_research_row = conn.execute(
            """
            SELECT id FROM research_runs
            WHERE site_id = ? AND status IN ('success','partial')
            ORDER BY completed_at DESC, id DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
        current_run_count = 0
        if latest_research_row:
            current_run_count = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT normalized_topic)
                    FROM research_candidates
                    WHERE site_id = ? AND research_run_id = ?
                      AND qualification_status = 'qualified'
                      AND recommended_disposition = 'new_article'
                      AND rationale NOT LIKE 'Raw source lead:%'
                      AND rationale NOT LIKE 'The current SERP exposed%'
                      AND rationale NOT LIKE 'Tavily source discovery was completed%'
                    """,
                    (site_id, int(latest_research_row["id"])),
                ).fetchone()[0]
            )

    old_items: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    active_gsc_at = str(active_gsc["imported_at"]) if active_gsc else ""
    for row in old_rows:
        item = dict(row)
        target_key = str(item["target_ref"]).rstrip("/").casefold()
        if target_key in seen_targets:
            continue
        if _memory_hides(decisions.get(old_article_decision_key(item["target_ref"])), skip_cutoff):
            continue
        latest_action = latest_action_by_target.get(target_key)
        if latest_action and latest_action["workflow_status"] in {"planned", "in_progress"}:
            continue
        content = content_by_url.get(target_key, {})
        if latest_action and latest_action["workflow_status"] == "completed":
            published_at = str(latest_action["published_at"] or "")
            content_at = str(content.get("latest_content_at") or "")
            if not published_at or not (content_at > published_at and active_gsc_at > published_at):
                continue
        item["evidence"] = json_loads(item.pop("evidence_json"), {})
        item["gate_reasons"] = json_loads(item.pop("gate_reasons_json"), [])
        item["content_title"] = content.get("title") or item["title"]
        item["why"], item["scope"] = _old_reason(str(item["rule_key"]))
        old_items.append(item)
        seen_targets.add(target_key)

    new_items: list[dict[str, Any]] = []
    seen_topics: set[str] = set()
    for row in new_rows:
        item = dict(row)
        normalized = str(item["normalized_topic"])
        if normalized in seen_topics:
            continue
        if _memory_hides(decisions.get(normalized), skip_cutoff):
            continue
        item["evidence_refs"] = json_loads(item.pop("evidence_refs_json"), [])
        item["source_urls"] = json_loads(item.pop("source_urls_json"), [])
        item["facts"] = json_loads(item.pop("facts_json"), [])
        item["inference"] = json_loads(item.pop("inference_json"), [])
        item["overlap"] = json_loads(item.pop("overlap_json"), {})
        item["closest_existing"] = json_loads(item.pop("closest_existing_json"), {})
        item["evidence_demand"] = json_loads(item.pop("evidence_demand_json"), {})
        item["evidence_gap"] = json_loads(item.pop("evidence_gap_json"), {})
        item["evidence_material"] = json_loads(item.pop("evidence_material_json"), {})
        item["limitations"] = json_loads(item.pop("limitations_json"), [])
        item["discovery_stage"] = _candidate_discovery_stage(item)
        item["semantic_cluster"] = _candidate_semantic_cluster(item)
        item["priority"] = round(
            _candidate_priority(item)
            + _feedback_priority_adjustment(str(item["topic"]), decision_rows),
            1,
        )
        # A raw lead is source material, not an article option.  Its URL,
        # quote and follow-up query were already saved as a source observation
        # by the research workflow, where a later run can consume it as a new
        # seed.  Keeping it out of this page does not discard it or require an
        # operator to make a faux article decision.
        if item["discovery_stage"] != "raw_lead":
            new_items.append(item)
        seen_topics.add(normalized)
    new_items.sort(key=lambda item: (float(item["priority"]), int(item["id"])), reverse=True)
    new_summary = _split(new_items)
    new_summary["current_run_count"] = current_run_count
    return {
        "old": _split(old_items),
        "new": new_summary,
    }


def decide_old_article_suggestion(
    opportunity_id: int,
    decision: str,
    reason: str = "",
    settings: Settings | None = None,
) -> SuggestionDecisionOutcome:
    active = settings or get_settings()
    if decision == "execute":
        try:
            outcome = record_opportunity_decision(opportunity_id, "accepted", reason, active)
        except ActionWorkflowError as exc:
            raise ArticleSuggestionError(str(exc)) from exc
        return SuggestionDecisionOutcome("已移到第 4 步“文章制作”", action_id=outcome.action_id)
    if decision not in {"skip", "reject"}:
        raise ArticleSuggestionError("旧文章决定无效")
    with connection(active) as conn:
        opportunity = get_opportunity(conn, opportunity_id)
        if (
            not opportunity
            or opportunity["target_kind"] != "blog"
            or opportunity["opportunity_type"] not in {"protect", "optimize"}
        ):
            raise ArticleSuggestionError("旧文章建议不存在")
        now = utc_now()
        memory_decision = "skip" if decision == "skip" else "dont_recommend"
        key = old_article_decision_key(str(opportunity["target_ref"]))
        conn.execute(
            """
            INSERT INTO topic_decisions(
                site_id, topic_id, normalized_topic, decision, reason,
                created_at, updated_at
            ) VALUES(?, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(site_id, normalized_topic) DO UPDATE SET
                topic_id = NULL, decision = excluded.decision,
                reason = excluded.reason, updated_at = excluded.updated_at
            """,
            (
                opportunity["site_id"],
                key,
                memory_decision,
                reason.strip()[:500] or None,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE opportunities SET status = ? WHERE id = ?",
            ("cancelled" if decision == "skip" else "rejected", opportunity_id),
        )
    message = "已暂时跳过，30 天内不再出现" if decision == "skip" else "已记录，以后不再推荐"
    return SuggestionDecisionOutcome(message)


def decide_new_article_suggestion(
    candidate_id: int,
    decision: str,
    reason: str = "",
    settings: Settings | None = None,
) -> SuggestionDecisionOutcome:
    active = settings or get_settings()
    mapped = {"execute": "do", "skip": "skip", "reject": "dont_recommend"}.get(decision)
    if not mapped:
        raise ArticleSuggestionError("新文章决定无效")
    try:
        message = record_research_candidate_decision(candidate_id, mapped, reason, active)
    except ResearchDecisionError as exc:
        raise ArticleSuggestionError(str(exc)) from exc
    if decision != "execute":
        return SuggestionDecisionOutcome(message)
    with connection(active) as conn:
        opportunity = conn.execute(
            """
            SELECT o.id FROM opportunities o
            JOIN research_candidates c
              ON c.site_id = o.site_id AND c.topic = o.target_ref
            WHERE c.id = ? AND o.rule_key = 'research_topic_candidate'
            ORDER BY o.id DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    if not opportunity:
        raise ArticleSuggestionError("新文章建议已保存，但未能建立制作任务")
    try:
        outcome = record_opportunity_decision(int(opportunity["id"]), "accepted", reason, active)
    except ActionWorkflowError as exc:
        raise ArticleSuggestionError(str(exc)) from exc
    return SuggestionDecisionOutcome("已移到第 4 步“文章制作”", action_id=outcome.action_id)


def review_new_article_candidate(
    candidate_id: int,
    review_decision: str,
    reason: str,
    settings: Settings | None = None,
) -> SuggestionDecisionOutcome:
    active = settings or get_settings()
    try:
        message = record_human_review(
            candidate_id,
            review_decision,
            reason,
            active,
        )
    except ResearchDecisionError as exc:
        raise ArticleSuggestionError(str(exc)) from exc
    return SuggestionDecisionOutcome(message)
