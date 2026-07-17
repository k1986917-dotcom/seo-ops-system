from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from seo_ops.config import RESEARCH_BUDGET_LIMITS, Settings, get_settings
from seo_ops.db import connection
from seo_ops.opportunities.engine import rebalance_site_portfolio
from seo_ops.repositories import get_opportunity, latest_analysis_run
from seo_ops.rules.research_workflow import (
    MULTI_SOURCE_TOPIC_RESEARCH_RULE,
    RESEARCH_METHOD_VERSION,
    assess_discovered_topic,
    current_topic_policy_block,
    normalize_topic,
    topic_matches_research_branch,
)
from seo_ops.services.ai import AIProvider, AIUnavailable, build_ai_provider
from seo_ops.services.external_evidence import (
    EvidenceCollectionUnavailable,
    ExternalRunResult,
    collect_research_query_evidence,
    collect_topic_query_evidence,
    execute_firecrawl_scrape,
    execute_tavily_search,
)
from seo_ops.services.topic_graph import sync_topic_graph, topic_tree
from seo_ops.utils import json_dumps, json_loads, utc_now


class ResearchUnavailable(RuntimeError):
    """Raised when a governed multi-source research run cannot start."""


class ResearchDecisionError(RuntimeError):
    """Raised when a research candidate decision is invalid."""


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    run_id: int
    status: str
    message: str
    candidate_count: int
    usage: dict[str, Any]


RESEARCH_SYSTEM_PROMPT = """你是 SEO 运营系统中的证据整理器，不是自由关键词生成器。
只能使用输入 evidence 中的内容，不能补充模型记忆、虚构来源、作者体验、实测或数据。
所有 summary、topic、intent、rationale、facts 和 inference 的值必须使用自然英语；
包含中文或混合语言的候选会被程序丢弃。
外部发现只能产生待核验主题；不得声称搜索量、收入、查询级转化、稳定排名或必然效果。
不得修改既有机会分数、资格门槛或推荐组合，也不得建议自动发布。
每个候选只回答一个可以独立验证的主要用户任务；不得用 FAQ、常见问题、终极指南或
大而全汇总词把多个任务包装成一篇文章。主题身份以标题/H1 和核心任务为主；安全、
功率、波长只有在本身是核心问题时才算主要文章主题，否则只是可共享的辅助知识。
输出严格 JSON：
{
  "summary": "One-sentence English summary of this evidence set",
  "topics": [
    {
      "topic": "A clear English topic with one primary user task",
      "intent": "English description of the intent to verify",
      "rationale": "English explanation of why this is worth verification",
      "evidence_ids": ["只能使用输入中存在的 evidence ID"],
      "facts": ["English fact line that explicitly includes its evidence ID"],
      "inference": ["Limited English inference kept separate from facts"]
    }
  ]
}
最多输出 8 个主题；没有足够材料时允许 topics 为空。"""

BOUNDARY_DIMENSIONS = (
    ("audience", "新受众与角色", "users audiences roles experience levels"),
    ("journey", "场景、目标与生命周期", "before during after use tasks goals conditions"),
    (
        "failure",
        "故障、限制、误用与替代",
        "problems limitations mistakes alternatives when not to use",
    ),
    ("decision", "选择、购买与验证标准", "choose compare verify specifications total cost risks"),
    (
        "ecosystem",
        "产品、技术、兼容与生态变化",
        "new technology compatibility accessories ecosystem",
    ),
    ("rules", "法规、安全、地区与行业变化", "safety laws regulations regional industry changes"),
    ("adjacent", "相邻站内问题", "related tools adjacent questions within laser pointer use"),
)

RESEARCH_LABELS = {
    "use-cases": "laser pointer use cases",
    "use-astronomy": "laser pointer astronomy stargazing",
    "use-outdoor": "laser pointer camping hiking emergency signaling",
    "use-presentations": "laser pointer presentations classrooms",
    "use-photography": "laser pointer light painting photography",
    "use-fishing-birds": "laser pointer fishing bird deterrence",
    "use-pets": "laser pointer pets cats",
    "use-professional": "laser pointer construction landscaping professional work",
    "buying": "choosing and buying laser pointers",
    "buy-budget": "laser pointer budgets and prices",
    "buy-fit": "choosing laser pointer performance for a use",
    "buy-quality": "laser pointer quality seller claims specification verification",
    "buy-models": "laser pointer models types reviews",
    "performance": "laser pointer performance principles",
    "tech-power": "laser pointer power measurement mW",
    "tech-wavelength": "laser pointer wavelength color visibility",
    "tech-optics": "laser pointer beam optics divergence focus",
    "tech-electronics": "laser diode driver electronics",
    "tech-thermal": "laser pointer thermal design reliability",
    "operation": "laser pointer operation maintenance troubleshooting",
    "ops-cleaning": "laser pointer lens cleaning beam problems",
    "ops-battery": "laser pointer batteries charging voltage",
    "ops-storage": "laser pointer storage carrying protection",
    "ops-accessories": "laser pointer mounts accessories beam tools",
    "ops-lifespan": "laser pointer lifespan duty cycle maintenance",
    "safety-law": "laser pointer safety laws",
    "safety-eye": "laser pointer eye people safety",
    "safety-class": "laser classes labels protective eyewear",
    "safety-laws": "laser pointer laws regulations by region",
    "safety-travel": "laser pointer travel airline customs",
}


def _flatten_topic_options(
    nodes: list[dict[str, Any]], path: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for node in nodes:
        current_path = (*path, str(node["preferred_name"]))
        if node["node_type"] == "branch" and node["topic_key"] in RESEARCH_LABELS:
            options.append(
                {
                    "id": int(node["id"]),
                    "topic_key": str(node["topic_key"]),
                    "name": str(node["preferred_name"]),
                    "path": " › ".join(current_path),
                    "article_count": int(node["article_count"]),
                    "research_label": RESEARCH_LABELS[str(node["topic_key"])],
                }
            )
        options.extend(_flatten_topic_options(node["children"], current_path))
    return options


def research_topic_options(site_id: int, settings: Settings | None = None) -> list[dict[str, Any]]:
    active_settings = settings or get_settings()
    sync_topic_graph(site_id, active_settings)
    return _flatten_topic_options(topic_tree(site_id, active_settings))


def boundary_dimensions() -> list[dict[str, str]]:
    return [
        {"key": key, "label": label, "query_hint": hint} for key, label, hint in BOUNDARY_DIMENSIONS
    ]


def configured_research_budgets(settings: Settings) -> dict[str, int]:
    raw = {
        "serpapi": settings.research_serpapi_budget,
        "firecrawl": settings.research_firecrawl_budget,
        "tavily": settings.research_tavily_budget,
        "ai": settings.research_ai_budget,
    }
    return {
        provider: max(0, min(RESEARCH_BUDGET_LIMITS[provider], int(value)))
        for provider, value in raw.items()
    }


def _usage_template(budgets: dict[str, int]) -> dict[str, dict[str, int]]:
    return {
        provider: {
            "budget": budget,
            "actual_requests": 0,
            "successful": 0,
            "failed": 0,
            "reused": 0,
        }
        for provider, budget in budgets.items()
    }


def _record_external_result(
    settings: Settings,
    research_run_id: int,
    provider: str,
    result: ExternalRunResult,
    usage: dict[str, dict[str, int]],
) -> None:
    if result.reused:
        usage[provider]["reused"] += 1
    else:
        usage[provider]["actual_requests"] += 1
    if result.status == "success":
        usage[provider]["successful"] += 1
    else:
        usage[provider]["failed"] += 1
    if result.run_id is None:
        return
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_run_items(
                research_run_id, external_run_id, provider, purpose, reused
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                research_run_id,
                result.run_id,
                provider,
                result.purpose,
                1 if result.reused else 0,
            ),
        )


def _content_search_items(settings: Settings, site_id: int) -> list[dict[str, Any]]:
    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT ci.id, ci.content_type, ci.title, ci.slug, ci.canonical_url,
                   cs.summary, cs.body, cs.seo_title, cs.seo_description, cs.metadata_json
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT cs2.id FROM content_snapshots cs2
                WHERE cs2.content_item_id = ci.id
                ORDER BY cs2.captured_at DESC, cs2.id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.status = 'active'
            ORDER BY ci.id
            """,
            (site_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        metadata = json_loads(item.pop("metadata_json"), {})
        body = str(item.pop("body") or "")
        headings = re.findall(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", body)
        metadata_text = json_dumps(metadata)[:4000]
        item["search_text"] = " ".join(
            str(value or "")
            for value in (
                item.get("title"),
                item.get("slug"),
                item.get("summary"),
                item.get("seo_title"),
                item.get("seo_description"),
                metadata_text,
            )
        )
        item["coverage_text"] = " ".join(
            str(value or "")
            for value in (
                item.get("title"),
                item.get("slug"),
                item.get("summary"),
                item.get("seo_title"),
                item.get("seo_description"),
                *headings,
            )
        )
        items.append(item)
    return items


def _host(value: str) -> str:
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _evidence_urls(evidence: dict[str, dict[str, Any]], refs: list[str]) -> list[str]:
    urls: list[str] = []
    for ref in refs:
        item = evidence.get(ref) or {}
        payload = item.get("payload") or {}
        direct = payload.get("url")
        if direct:
            urls.append(str(direct))
        for result in payload.get("organic_results") or payload.get("results") or []:
            if isinstance(result, dict):
                url = result.get("link") or result.get("url")
                if url:
                    urls.append(str(url))
    return _unique(urls, 12)


def _compact_ai_evidence(evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    total_chars = 0
    for evidence_id, item in evidence.items():
        payload = item.get("payload") or {}
        value = {
            "evidence_id": evidence_id,
            "provider": item.get("provider"),
            "purpose": item.get("purpose"),
            "payload": payload,
        }
        encoded = json_dumps(value)
        if len(encoded) > 12000:
            value["payload"] = {
                "query": payload.get("query"),
                "title": payload.get("title"),
                "url": payload.get("url"),
                "related_questions": payload.get("related_questions"),
                "related_searches": payload.get("related_searches"),
                "organic_results": (payload.get("organic_results") or [])[:5],
                "results": (payload.get("results") or [])[:5],
                "markdown_excerpt": str(payload.get("markdown_excerpt") or "")[:3000],
            }
            encoded = json_dumps(value)
        if total_chars + len(encoded) > 80000:
            break
        compact.append(value)
        total_chars += len(encoded)
        if len(compact) >= 40:
            break
    return compact


async def _ai_topics(
    settings: Settings,
    *,
    site_id: int,
    research_run_id: int,
    evidence: dict[str, dict[str, Any]],
    content_items: list[dict[str, Any]],
    provider: AIProvider | None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    input_refs = list(evidence)
    payload = {
        "method_version": RESEARCH_METHOD_VERSION,
        "evidence": _compact_ai_evidence(evidence),
        "existing_content": [
            {"title": item.get("title"), "url": item.get("canonical_url")}
            for item in content_items[:100]
        ],
    }
    try:
        active_provider = provider or build_ai_provider(settings)
        response = await active_provider.complete_json(RESEARCH_SYSTEM_PROMPT, payload)
        raw_topics = response.content.get("topics")
        if not isinstance(raw_topics, list):
            raise ValueError("AI 输出缺少 topics 数组")
        known_refs = set(input_refs)

        def resolve_ref(value: Any) -> tuple[str | None, str]:
            raw_ref = str(value).strip()
            if raw_ref in known_refs:
                return raw_ref, raw_ref
            matches = [known for known in known_refs if known.startswith(f"{raw_ref}:")]
            if len(matches) == 1:
                return matches[0], raw_ref
            # Some OpenAI-compatible models preserve the external run id but
            # replace the purpose with a positional numeric suffix, for example
            # ``external:13:1``. Resolve that form only when the run id maps to
            # exactly one evidence item in this prompt. Ambiguous or invented
            # run ids remain rejected.
            parts = raw_ref.split(":")
            if (
                len(parts) == 3
                and parts[0] == "external"
                and parts[1].isdigit()
                and parts[2].isdigit()
            ):
                run_matches = [
                    known for known in known_refs if known.startswith(f"external:{parts[1]}:")
                ]
                if len(run_matches) == 1:
                    return run_matches[0], raw_ref
            return None, raw_ref

        topics: list[dict[str, Any]] = []
        for raw in raw_topics[
            : int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["max_topic_candidates"])
        ]:
            if not isinstance(raw, dict):
                continue
            topic = " ".join(str(raw.get("topic") or "").split()).strip()[:300]
            resolved_pairs = [resolve_ref(ref) for ref in raw.get("evidence_ids") or []]
            if current_topic_policy_block(topic):
                continue
            refs = [resolved for resolved, _ in resolved_pairs if resolved]
            refs = _unique(refs, 12)
            if not topic or not refs:
                continue
            facts = [str(value)[:1000] for value in raw.get("facts") or []]
            normalized_facts = []
            for fact in facts:
                normalized = fact
                matched = False
                for resolved, raw_ref in resolved_pairs:
                    if resolved and raw_ref in normalized:
                        normalized = normalized.replace(raw_ref, resolved)
                        matched = True
                if matched or any(ref in normalized for ref in refs):
                    normalized_facts.append(normalized)
            facts = normalized_facts[:8]
            intent = str(raw.get("intent") or "Intent requires operator review")[:300]
            rationale = str(raw.get("rationale") or "Limited synthesis of the stored evidence")[
                :1000
            ]
            inference = [str(value)[:1000] for value in raw.get("inference") or []][:8]
            semantic_text = " ".join([topic, intent, rationale, *facts, *inference])
            if re.search(r"[\u3400-\u9fff]", semantic_text):
                continue
            topics.append(
                {
                    "topic": topic,
                    "intent": intent,
                    "rationale": rationale,
                    "evidence_refs": refs,
                    "facts": facts,
                    "inference": inference,
                }
            )
        with connection(settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, output_json, status, created_at
                ) VALUES(?, NULL, 'topic_research', ?, ?, ?, ?, ?, 'success', ?)
                """,
                (
                    site_id,
                    response.provider,
                    response.model,
                    response.prompt_sha256,
                    json_dumps(input_refs),
                    json_dumps(response.content),
                    utc_now(),
                ),
            )
        return topics, response.model, True
    except AIUnavailable:
        return [], settings.ai_model, False
    except Exception:
        prompt_hash = hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()
        with connection(settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, status, error_message, created_at
                ) VALUES(?, NULL, 'topic_research', ?, ?, ?, ?, 'failed', ?, ?)
                """,
                (
                    site_id,
                    settings.ai_provider,
                    settings.ai_model or "unconfigured",
                    prompt_hash,
                    json_dumps(input_refs),
                    "AI 主题整理失败",
                    utc_now(),
                ),
            )
        return [], settings.ai_model, False


def _insert_candidates(
    settings: Settings,
    *,
    research_run_id: int,
    site_id: int,
    candidates: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    content_items: list[dict[str, Any]],
    suggested_parent_topic_id: int | None,
    research_label: str | None,
) -> int:
    inserted = 0
    seen: set[str] = set()
    limit = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["max_topic_candidates"])
    with connection(settings) as conn:
        prior_topics = conn.execute(
            """
            SELECT id, topic FROM research_candidates
            WHERE site_id = ? AND research_run_id <> ?
              AND decision IN ('pending','do','dont_recommend')
            ORDER BY id
            """,
            (site_id, research_run_id),
        ).fetchall()
    comparison_items = list(content_items)
    comparison_items.extend(
        {
            "id": -int(row["id"]),
            "content_type": "candidate",
            "title": str(row["topic"]),
            "slug": str(row["topic"]),
            "seo_title": str(row["topic"]),
            "search_text": str(row["topic"]),
        }
        for row in prior_topics
    )
    for candidate in candidates:
        topic = " ".join(str(candidate.get("topic") or "").split()).strip()[:300]
        if current_topic_policy_block(topic):
            continue
        if research_label and not topic_matches_research_branch(topic, research_label):
            continue
        normalized = normalize_topic(topic)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        with connection(settings) as conn:
            remembered = conn.execute(
                """
                SELECT decision, updated_at FROM topic_decisions
                WHERE site_id = ? AND normalized_topic = ?
                """,
                (site_id, normalized),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT decision FROM research_candidates
                WHERE site_id = ? AND normalized_topic = ?
                  AND research_run_id <> ?
                ORDER BY id DESC LIMIT 1
                """,
                (site_id, normalized, research_run_id),
            ).fetchone()
        skip_cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        if remembered and remembered["decision"] == "dont_recommend":
            continue
        if (
            remembered
            and remembered["decision"] == "skip"
            and str(remembered["updated_at"]) >= skip_cutoff
        ):
            continue
        if prior and prior["decision"] in {"pending", "do", "dont_recommend"}:
            continue
        refs = [ref for ref in _unique(candidate.get("evidence_refs") or [], 12) if ref in evidence]
        if not refs:
            continue
        assessment = assess_discovered_topic(topic, comparison_items)
        if assessment.gate_status == "blocked":
            continue
        urls = _evidence_urls(evidence, refs)
        with connection(settings) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO research_candidates(
                    research_run_id, site_id, topic, normalized_topic, intent, rationale,
                    recommended_next_step, gate_status, evidence_refs_json,
                    source_urls_json, facts_json, inference_json, overlap_json,
                    limitations_json, created_at, suggested_parent_topic_id
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id,
                    site_id,
                    topic,
                    normalized,
                    str(candidate.get("intent") or "Intent requires operator review")[:300],
                    str(candidate.get("rationale") or "External topic lead requiring verification")[
                        :1000
                    ],
                    assessment.next_step,
                    assessment.gate_status,
                    json_dumps(refs),
                    json_dumps(urls),
                    json_dumps(candidate.get("facts") or []),
                    json_dumps(candidate.get("inference") or []),
                    json_dumps(assessment.overlap),
                    json_dumps(assessment.limitations),
                    utc_now(),
                    suggested_parent_topic_id,
                ),
            )
            was_inserted = cursor.rowcount > 0
            inserted += int(was_inserted)
            if was_inserted:
                comparison_items.append(
                    {
                        "id": -int(cursor.lastrowid),
                        "content_type": "candidate",
                        "title": topic,
                        "slug": topic,
                        "seo_title": topic,
                        "search_text": topic,
                    }
                )
        if inserted >= limit:
            break
    return inserted


async def run_topic_research(
    site_id: int,
    settings: Settings | None = None,
    *,
    seed_type: str = "auto",
    topic_id: int | None = None,
    dimension_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    ai_provider: AIProvider | None = None,
) -> ResearchOutcome:
    active_settings = settings or get_settings()
    budgets = configured_research_budgets(active_settings)
    if not any(budgets.values()):
        raise ResearchUnavailable("本轮所有 API 预算均为 0，请先在设置页配置")

    with connection(active_settings) as conn:
        site_row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        running = conn.execute(
            """
            SELECT id FROM research_runs
            WHERE site_id = ? AND status = 'running'
            ORDER BY id DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
        latest = latest_analysis_run(conn, site_id)
    if running:
        raise ResearchUnavailable(f"调研 #{running['id']} 仍在运行，请勿重复提交")
    if not site_row:
        raise ResearchUnavailable("站点不存在")
    site = dict(site_row)
    analysis_run_id = int(latest["id"]) if latest else None
    requested_type = seed_type if seed_type in {"auto", "gsc", "topic_gap", "boundary"} else "auto"
    resolved_type = requested_type
    seeds: list[dict[str, Any]] = []
    selected_topic: dict[str, Any] | None = None
    filters = {
        "language": active_settings.serpapi_language,
        "country": active_settings.serpapi_country,
        "intent": "mixed",
        "experience": "mixed",
        "season": "all",
    }

    if requested_type in {"auto", "gsc"} and analysis_run_id is not None:
        with connection(active_settings) as conn:
            seed_rows = conn.execute(
                """
                SELECT id FROM opportunities
                WHERE analysis_run_id = ? AND rule_key = 'query_page_evidence_gap'
                  AND target_kind = 'query'
                  AND status IN ('proposed','accepted','in_progress')
                ORDER BY CASE WHEN portfolio_slot = '补证据' THEN 0 ELSE 1 END,
                         priority DESC, id
                """,
                (analysis_run_id,),
            ).fetchall()
            seeds = [
                opportunity
                for row in seed_rows
                if (opportunity := get_opportunity(conn, int(row["id"])))
            ]
    if requested_type == "gsc" and not seeds:
        raise ResearchUnavailable("本轮没有可用的 GSC 查询线索；可改用“补主题缺口”或“突破主题瓶颈”")
    if requested_type == "auto":
        resolved_type = "gsc" if seeds else "topic_gap"

    if resolved_type in {"topic_gap", "boundary"}:
        options = research_topic_options(site_id, active_settings)
        by_id = {item["id"]: item for item in options}
        if topic_id is not None and topic_id not in by_id:
            raise ResearchUnavailable("所选主题方向不存在或不适合继续调研")
        with connection(active_settings) as conn:
            memory_rows = conn.execute(
                """
                SELECT topic_id, COUNT(*) AS count FROM topic_research_memory
                WHERE site_id = ? GROUP BY topic_id
                """,
                (site_id,),
            ).fetchall()
        memory_counts = {
            int(row["topic_id"]): int(row["count"]) for row in memory_rows if row["topic_id"]
        }
        candidates = list(options)
        if resolved_type == "boundary":
            candidates = [item for item in candidates if item["article_count"] > 0] or candidates
        candidates.sort(
            key=lambda item: (
                memory_counts.get(item["id"], 0),
                item["article_count"] if resolved_type == "topic_gap" else -item["article_count"],
                item["id"],
            )
        )
        selected_topic = (
            by_id.get(topic_id) if topic_id is not None else (candidates[0] if candidates else None)
        )
        if not selected_topic:
            raise ResearchUnavailable("主题图谱还没有可调研的方向，请先导入 CMS 内容")
        label = str(selected_topic["research_label"])
        if resolved_type == "topic_gap":
            dimension_key = None
            seed_queries = [
                f"{label} common questions",
                f"{label} user problems mistakes",
                f"{label} forum discussions alternatives",
                f"{label} comparison guide",
            ]
        else:
            dimension_map = {key: (name, hint) for key, name, hint in BOUNDARY_DIMENSIONS}
            if dimension_key not in dimension_map:
                with connection(active_settings) as conn:
                    dimension_counts = {
                        str(row["dimension_key"]): int(row["count"])
                        for row in conn.execute(
                            """
                            SELECT dimension_key, COUNT(*) AS count
                            FROM topic_research_memory
                            WHERE site_id = ? AND topic_id = ? AND seed_type = 'boundary'
                            GROUP BY dimension_key
                            """,
                            (site_id, selected_topic["id"]),
                        ).fetchall()
                    }
                dimension_key = min(
                    dimension_map,
                    key=lambda key: (dimension_counts.get(key, 0), list(dimension_map).index(key)),
                )
            dimension_name, hint = dimension_map[dimension_key]
            filters["dimension_label"] = dimension_name
            seed_queries = [f"{label} {hint}", f"{label} common questions {hint}"]
        seeds = [
            {"id": None, "target_ref": query, "evidence": {"evidence_ids": []}}
            for query in _unique(seed_queries)
        ]

    seed_queries = [str(seed["target_ref"]) for seed in seeds]
    with connection(active_settings) as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_runs(
                site_id, analysis_run_id, status, budgets_json, seed_queries_json,
                ai_model, started_at, seed_type, topic_id, dimension_key, filters_json
            ) VALUES(?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site_id,
                analysis_run_id,
                json_dumps(budgets),
                json_dumps(seed_queries),
                active_settings.ai_model,
                utc_now(),
                resolved_type,
                selected_topic["id"] if selected_topic else None,
                dimension_key,
                json_dumps(filters),
            ),
        )
        research_run_id = int(cursor.lastrowid)

    usage = _usage_template(budgets)
    evidence: dict[str, dict[str, Any]] = {}
    deterministic_candidates: list[dict[str, Any]] = []
    provider_failures = 0

    try:
        for seed in seeds:
            remaining = budgets["serpapi"] - usage["serpapi"]["actual_requests"]
            if remaining <= 0:
                break
            if not active_settings.serpapi_api_key:
                provider_failures += 1
                break
            seed_opportunity_id = int(seed["id"]) if seed.get("id") is not None else None
            if seed_opportunity_id is not None:
                outcome = await collect_research_query_evidence(
                    seed_opportunity_id,
                    min(2, remaining),
                    active_settings,
                    transport=transport,
                )
            else:
                outcome = await collect_topic_query_evidence(
                    site_id,
                    str(seed["target_ref"]),
                    min(2, remaining),
                    active_settings,
                    transport=transport,
                )
            for result in outcome.runs:
                _record_external_result(active_settings, research_run_id, "serpapi", result, usage)
                if result.status == "success" and result.evidence_id:
                    evidence[result.evidence_id] = {
                        "provider": "serpapi",
                        "purpose": result.purpose,
                        "payload": result.payload or {},
                        "opportunity_id": seed_opportunity_id,
                    }
                    if result.purpose == "serp_snapshot":
                        payload = result.payload or {}
                        for topic in payload.get("related_questions") or []:
                            deterministic_candidates.append(
                                {
                                    "topic": topic,
                                    "intent": "Question intent requiring operator review",
                                    "rationale": "The current SERP exposed this related question",
                                    "evidence_refs": [result.evidence_id],
                                    "facts": [
                                        f"{result.evidence_id} contains this related question"
                                    ],
                                    "inference": [
                                        "This may justify verification as an adjacent topic"
                                    ],
                                }
                            )
                        for topic in payload.get("related_searches") or []:
                            deterministic_candidates.append(
                                {
                                    "topic": topic,
                                    "intent": "Related-search intent requiring operator review",
                                    "rationale": "The current SERP exposed this related search",
                                    "evidence_refs": [result.evidence_id],
                                    "facts": [f"{result.evidence_id} contains this related search"],
                                    "inference": [
                                        "This may justify verification as an adjacent topic"
                                    ],
                                }
                            )
                elif result.status != "success":
                    provider_failures += 1

        discovery_queries = list(seed_queries)
        for item in evidence.values():
            if item["provider"] != "serpapi" or item["purpose"] != "serp_snapshot":
                continue
            payload = item["payload"]
            discovery_queries.extend(payload.get("related_questions") or [])
            discovery_queries.extend(payload.get("related_searches") or [])
        discovery_queries = _unique(discovery_queries, 150)
        for query in discovery_queries:
            if usage["tavily"]["actual_requests"] >= budgets["tavily"]:
                break
            if not active_settings.tavily_api_key:
                provider_failures += 1
                break
            seed = next(
                (item for item in seeds if str(item["target_ref"]).casefold() == query.casefold()),
                seeds[0],
            )
            input_refs = [str(ref) for ref in seed["evidence"].get("evidence_ids", [])]
            for evidence_id, item in evidence.items():
                payload = item.get("payload") or {}
                if query in (payload.get("related_questions") or []) or query in (
                    payload.get("related_searches") or []
                ):
                    input_refs.append(evidence_id)
            result = await execute_tavily_search(
                active_settings,
                site=site,
                opportunity_id=(int(seed["id"]) if seed.get("id") is not None else None),
                query=query,
                input_refs=_unique(input_refs, 20),
                transport=transport,
            )
            _record_external_result(active_settings, research_run_id, "tavily", result, usage)
            if result.status == "success" and result.evidence_id:
                evidence[result.evidence_id] = {
                    "provider": "tavily",
                    "purpose": result.purpose,
                    "payload": result.payload or {},
                    "opportunity_id": (int(seed["id"]) if seed.get("id") is not None else None),
                }
                if query.casefold() not in {value.casefold() for value in seed_queries}:
                    deterministic_candidates.append(
                        {
                            "topic": query,
                            "intent": "External discovery lead requiring operator review",
                            "rationale": "Tavily source discovery was completed for this related query",
                            "evidence_refs": [result.evidence_id],
                            "facts": [
                                f"{result.evidence_id} stores source discovery for this query"
                            ],
                            "inference": [
                                "The results can support a decision to continue verification"
                            ],
                        }
                    )
            else:
                provider_failures += 1

        url_refs: dict[str, list[str]] = {}
        url_opportunity: dict[str, int | None] = {}
        for evidence_id, item in evidence.items():
            payload = item.get("payload") or {}
            for result in payload.get("organic_results") or payload.get("results") or []:
                if not isinstance(result, dict):
                    continue
                url = str(result.get("link") or result.get("url") or "").strip()
                if not url or _host(url) == _host(str(site["domain"])):
                    continue
                url_refs.setdefault(url, []).append(evidence_id)
                value = item.get("opportunity_id")
                url_opportunity.setdefault(url, int(value) if value is not None else None)
        diverse_urls: list[str] = []
        deferred: list[str] = []
        seen_hosts: set[str] = set()
        for url in url_refs:
            host = _host(url)
            if host and host not in seen_hosts:
                diverse_urls.append(url)
                seen_hosts.add(host)
            else:
                deferred.append(url)
        diverse_urls.extend(deferred)
        for url in diverse_urls:
            if usage["firecrawl"]["actual_requests"] >= budgets["firecrawl"]:
                break
            if not active_settings.firecrawl_api_key:
                provider_failures += 1
                break
            try:
                result = await execute_firecrawl_scrape(
                    active_settings,
                    site=site,
                    opportunity_id=url_opportunity[url],
                    url=url,
                    input_refs=_unique(url_refs[url], 20),
                    transport=transport,
                )
            except EvidenceCollectionUnavailable:
                continue
            _record_external_result(active_settings, research_run_id, "firecrawl", result, usage)
            if result.status == "success" and result.evidence_id:
                evidence[result.evidence_id] = {
                    "provider": "firecrawl",
                    "purpose": result.purpose,
                    "payload": result.payload or {},
                    "opportunity_id": url_opportunity[url],
                }
            else:
                provider_failures += 1

        content_items = _content_search_items(active_settings, site_id)
        ai_candidates: list[dict[str, Any]] = []
        ai_model = active_settings.ai_model
        ai_ok = True
        if budgets["ai"] > 0 and (active_settings.ai_enabled or ai_provider is not None):
            usage["ai"]["actual_requests"] = 1
            ai_candidates, ai_model, ai_ok = await _ai_topics(
                active_settings,
                site_id=site_id,
                research_run_id=research_run_id,
                evidence=evidence,
                content_items=content_items,
                provider=ai_provider,
            )
            usage["ai"]["successful" if ai_ok else "failed"] = 1
            if not ai_ok:
                provider_failures += 1
        elif budgets["ai"] > 0:
            ai_ok = False
            provider_failures += 1

        candidate_count = _insert_candidates(
            active_settings,
            research_run_id=research_run_id,
            site_id=site_id,
            candidates=[*ai_candidates, *deterministic_candidates],
            evidence=evidence,
            content_items=content_items,
            suggested_parent_topic_id=selected_topic["id"] if selected_topic else None,
            research_label=(str(selected_topic["research_label"]) if selected_topic else None),
        )
        external_successes = sum(
            usage[name]["successful"] for name in ("serpapi", "firecrawl", "tavily")
        )
        external_attempts = sum(
            usage[name]["actual_requests"] for name in ("serpapi", "firecrawl", "tavily")
        )
        if external_successes == 0 and external_attempts > 0:
            status = "failed"
        elif provider_failures or candidate_count == 0:
            status = "partial"
        else:
            status = "success"
        with connection(active_settings) as conn:
            memory_status = (
                "found" if candidate_count else "insufficient" if evidence else "no_result"
            )
            for query in seed_queries:
                conn.execute(
                    """
                    INSERT INTO topic_research_memory(
                        site_id, topic_id, seed_type, dimension_key, filters_json,
                        query_text, normalized_query, result_status, result_summary,
                        research_run_id, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        site_id,
                        selected_topic["id"] if selected_topic else None,
                        resolved_type,
                        dimension_key,
                        json_dumps(filters),
                        query,
                        normalize_topic(query),
                        memory_status,
                        f"候选 {candidate_count}，外部成功 {external_successes}",
                        research_run_id,
                        utc_now(),
                    ),
                )
            conn.execute(
                """
                UPDATE research_runs
                SET status = ?, usage_json = ?, candidate_count = ?, ai_model = ?,
                    error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json_dumps(usage),
                    candidate_count,
                    ai_model,
                    "部分供应商失败或没有形成候选" if status == "partial" else None,
                    utc_now(),
                    research_run_id,
                ),
            )
        message = (
            f"外部调研完成：实际请求 SerpAPI {usage['serpapi']['actual_requests']}、"
            f"Firecrawl {usage['firecrawl']['actual_requests']}、"
            f"Tavily {usage['tavily']['actual_requests']}、AI {usage['ai']['actual_requests']}；"
            f"生成 {candidate_count} 个待核验主题。"
        )
        return ResearchOutcome(research_run_id, status, message, candidate_count, usage)
    except Exception as exc:
        with connection(active_settings) as conn:
            conn.execute(
                """
                UPDATE research_runs
                SET status = 'failed', usage_json = ?, error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (json_dumps(usage), "调研流程中断", utc_now(), research_run_id),
            )
        if isinstance(exc, ResearchUnavailable):
            raise
        raise ResearchUnavailable("外部调研中断；已保留成功快照和失败记录") from exc


def _ensure_research_opportunity_run(conn, site_id: int, now: str) -> int:
    current = latest_analysis_run(conn, site_id)
    if current:
        return int(current["id"])
    source_rows = conn.execute(
        """
        SELECT i.id FROM imports i
        WHERE i.site_id = ? AND i.status = 'success'
          AND i.id = (
            SELECT i2.id FROM imports i2
            WHERE i2.site_id = i.site_id AND i2.source_type = i.source_type
              AND i2.status = 'success'
            ORDER BY i2.imported_at DESC, i2.id DESC LIMIT 1
          )
        ORDER BY i.source_type
        """,
        (site_id,),
    ).fetchall()
    source_ids = [int(item["id"]) for item in source_rows]
    cursor = conn.execute(
        """
        INSERT INTO analysis_runs(
            site_id, method_version, source_import_ids_json, status,
            metadata_json, started_at, completed_at
        ) VALUES(?, 'topic-research-0.6.1', ?, 'success', ?, ?, ?)
        """,
        (
            site_id,
            json_dumps(source_ids),
            json_dumps(
                {
                    "research_only": True,
                    "candidate_count": 0,
                    "top_count": 0,
                    "notes": "GSC 不足时仍可由主题图谱和外部调研形成新文章建议。",
                }
            ),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _upsert_research_opportunity(conn, row, now: str) -> int:
    refs = [str(value) for value in json_loads(row["evidence_refs_json"], []) if value]
    if not refs:
        raise ResearchDecisionError("该主题缺少可追溯材料，暂时不能加入新文章建议")
    urls = [str(value) for value in json_loads(row["source_urls_json"], []) if value]
    facts = json_loads(row["facts_json"], [])
    inference = json_loads(row["inference_json"], [])
    overlap = json_loads(row["overlap_json"], {})
    limitations = json_loads(row["limitations_json"], [])
    analysis_run_id = _ensure_research_opportunity_run(conn, int(row["site_id"]), now)
    confidence = "medium" if len(refs) >= 2 and len(urls) >= 2 else "low"
    confidence_weight = 0.7 if confidence == "medium" else 0.4
    strength = float(min(75, 50 + len(refs) * 4 + min(len(urls), 5) * 2))
    effort = 4.0
    priority = round(strength * confidence_weight / effort, 1)
    evidence = {
        "evidence_ids": refs,
        "research_run_id": int(row["research_run_id"]),
        "research_candidate_id": int(row["id"]),
        "normalized_topic": row["normalized_topic"],
        "intent": row["intent"],
        "facts": facts,
        "program_inference": {
            "content_overlap": overlap,
            "research_inference": inference,
            "strength_note": "强度只表示材料完整度，不是搜索量或收入预测。",
        },
        "source_urls": urls,
        "limitations": limitations,
    }
    gate_reasons = [
        "主题来自明确的 GSC、主题缺口或边界扩展调研",
        "CMS 重叠检查未达到阻塞门槛",
        "运营者已确认该主题值得做",
    ]
    recommended_action = (
        "制作一篇与现有文章主意图不同的新文章；使用已存证素材，"
        "补齐自然内链、可核验外链和发布前自审。"
    )
    existing = conn.execute(
        """
        SELECT id, status FROM opportunities
        WHERE site_id = ? AND rule_key = 'research_topic_candidate' AND target_ref = ?
        ORDER BY id DESC LIMIT 1
        """,
        (row["site_id"], row["topic"]),
    ).fetchone()
    if existing:
        opportunity_id = int(existing["id"])
        if existing["status"] == "proposed":
            conn.execute(
                """
                UPDATE opportunities
                SET analysis_run_id = ?, title = ?, recommended_action = ?,
                    gate_status = 'passed', gate_reasons_json = ?, evidence_json = ?,
                    strength = ?, confidence = ?, confidence_weight = ?, effort = ?,
                    priority = ?, method_version = 'topic-research-0.6.0'
                WHERE id = ?
                """,
                (
                    analysis_run_id,
                    row["topic"],
                    recommended_action,
                    json_dumps(gate_reasons),
                    json_dumps(evidence),
                    strength,
                    confidence,
                    confidence_weight,
                    effort,
                    priority,
                    opportunity_id,
                ),
            )
    else:
        cursor = conn.execute(
            """
            INSERT INTO opportunities(
                analysis_run_id, site_id, rule_key, opportunity_type, target_kind,
                target_ref, title, recommended_action, gate_status, gate_reasons_json,
                evidence_json, strength, confidence, confidence_weight, effort,
                priority, method_version, created_at
            ) VALUES(?, ?, 'research_topic_candidate', 'create', 'topic', ?, ?, ?,
                     'passed', ?, ?, ?, ?, ?, ?, ?, 'topic-research-0.6.0', ?)
            """,
            (
                analysis_run_id,
                row["site_id"],
                row["topic"],
                row["topic"],
                recommended_action,
                json_dumps(gate_reasons),
                json_dumps(evidence),
                strength,
                confidence,
                confidence_weight,
                effort,
                priority,
                now,
            ),
        )
        opportunity_id = int(cursor.lastrowid)
    rebalance_site_portfolio(conn, int(row["site_id"]), analysis_run_id)
    return opportunity_id


def record_research_candidate_decision(
    candidate_id: int,
    decision: str,
    reason: str = "",
    settings: Settings | None = None,
) -> str:
    active_settings = settings or get_settings()
    if decision not in {"do", "dont_recommend", "skip"}:
        raise ResearchDecisionError("主题决定无效")
    now = utc_now()
    with connection(active_settings) as conn:
        row = conn.execute(
            """
            SELECT c.*, r.seed_type
            FROM research_candidates c
            JOIN research_runs r ON r.id = c.research_run_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if not row:
            raise ResearchDecisionError("主题候选不存在")
        if row["decision"] != "pending":
            raise ResearchDecisionError("这个主题已经处理过，不需要重复操作")
        policy_block = current_topic_policy_block(str(row["topic"]))
        if decision == "do" and policy_block:
            raise ResearchDecisionError(f"{policy_block}；请先拆成一个具体用户任务。")
        if decision == "do" and row["gate_status"] == "blocked":
            raise ResearchDecisionError("该主题与已有内容明显重叠，不能直接标记为值得做")
        # A research choice is decision memory, not graph coverage. Associate
        if decision == "do":
            current_assessment = assess_discovered_topic(
                str(row["topic"]),
                _content_search_items(active_settings, int(row["site_id"])),
            )
            if current_assessment.gate_status == "blocked":
                raise ResearchDecisionError(
                    "该主题按当前 CMS 主主题重合规则已被阻断，请优先检查或更新现有文章"
                )
        # it with the selected existing branch when available, but never create
        # a candidate/planned node. Only a later CMS import creates coverage.
        topic_id = (
            int(row["suggested_parent_topic_id"])
            if row["suggested_parent_topic_id"] is not None
            else None
        )
        conn.execute(
            """
            UPDATE research_candidates
            SET decision = ?, decision_reason = ?, decided_at = ?
            WHERE id = ?
            """,
            (decision, reason.strip()[:500] or None, now, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO topic_decisions(
                site_id, topic_id, normalized_topic, decision, reason,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(site_id, normalized_topic) DO UPDATE SET
                topic_id = excluded.topic_id,
                decision = excluded.decision,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (
                row["site_id"],
                topic_id,
                row["normalized_topic"],
                decision,
                reason.strip()[:500] or None,
                now,
                now,
            ),
        )
        if decision == "do":
            _upsert_research_opportunity(conn, row, now)
    labels = {
        "do": "已确认这个新文章方向",
        "dont_recommend": "以后不再推荐",
        "skip": "已暂时跳过 30 天",
    }
    return labels[decision]
