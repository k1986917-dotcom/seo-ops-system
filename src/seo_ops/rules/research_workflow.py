from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from seo_ops.config import RESEARCH_BUDGET_LIMITS
from seo_ops.rules.evidence_workflow import content_overlap_assessment

RESEARCH_METHOD_VERSION = "multi-source-research-0.6.1"
RESEARCH_PLAN_VERSION = "topic-hypothesis-0.6.1"

MULTI_SOURCE_TOPIC_RESEARCH_RULE = {
    "rule_key": "multi_source_topic_research",
    "version": "0.6.1",
    "rule_type": "evidence_discovery",
    "evidence_level": "A+C+E+inference",
    "rationale": (
        "从 GSC 信号、主题图谱缺口或边界扩展入口出发，在运营者明确点击后按预算采集"
        "SERP、网页原文与资料发现结果；外部材料只扩展待核验主题，不替代第一方需求或"
        "人工搜索意图判断。主题身份以 H1 和页面核心用户任务为主；安全、功率、波长等"
        "内容只有在本身是核心问题时才作为主要文章主题，否则只是可共享的辅助知识。"
        "候选主题及其语义字段统一为英语；非英语候选不进入运营列表。"
    ),
    "config": {
        "hard_limits": RESEARCH_BUDGET_LIMITS,
        "reuse_hours": 24,
        "results_per_tavily_search": 5,
        "max_firecrawl_excerpt_chars": 12000,
        "max_topic_candidates": 8,
        "strong_content_overlap": 0.68,
        "branch_relevance_min": 0.5,
    },
    "sources": [
        "https://serpapi.com/search-api",
        "https://serpapi.com/google-trends-api",
        "https://docs.firecrawl.dev/api-reference/endpoint/scrape",
        "https://docs.tavily.com/documentation/api-reference/endpoint/search",
        "https://api-docs.deepseek.com/api/create-chat-completion",
    ],
    "known_failures": [
        "更多调用会增加覆盖面、重复与噪声，不会线性提高主题质量",
        "同一网页被多个工具发现或摘要仍主要是一份底层证据",
        "外部发现的主题没有本站需求事实时只能保持 needs_evidence",
        "AI 只能整理传入 evidence ID，不能补来源、改分或自动发布",
        "FAQ、常见问题或终极指南等集合词可能把多个独立任务误包装成一篇文章",
        "翻译或混合语言会绕过英语站点的词项重合检查，因此直接阻断",
    ],
    "review_after": "2026-08-14",
}

_GENERIC_COLLECTION_RE = re.compile(
    r"\b(?:faqs?|frequently asked questions?|common questions?|everything you need(?: to know)?|"
    r"ultimate guide|complete guide)\b",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def current_topic_policy_block(topic: str) -> str | None:
    if _CJK_RE.search(topic):
        return "英语站点只接受英语主题；翻译或混合语言候选已阻断"
    if not re.search(r"[a-z]", topic, flags=re.IGNORECASE):
        return "候选主题没有可识别的英语主要任务"
    if _GENERIC_COLLECTION_RE.search(topic):
        return "该表述是泛 FAQ 或大而全内容集合，不是一个清晰的主要用户任务"
    return None


@dataclass(frozen=True, slots=True)
class TopicAssessment:
    gate_status: str
    overlap: dict[str, Any]
    limitations: list[str]
    next_step: str


def normalize_topic(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())[:300]


def topic_matches_research_branch(topic: str, branch_label: str) -> bool:
    """Return whether a candidate still addresses the selected research branch."""

    if not branch_label.strip():
        return True
    overlap = content_overlap_assessment(
        branch_label,
        [
            {
                "id": 0,
                "content_type": "research_candidate",
                "title": topic,
                "slug": topic,
                "seo_title": topic,
                "search_text": topic,
            }
        ],
    )
    threshold = float(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["branch_relevance_min"])
    return float(overlap.get("max_score") or 0) >= threshold


def assess_discovered_topic(topic: str, content_items: list[dict[str, Any]]) -> TopicAssessment:
    overlap = content_overlap_assessment(topic, content_items)
    strong = float(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["strong_content_overlap"])
    limitations = [
        "该主题来自外部资料发现或相关查询，只是待核验假设",
        "没有本站 query→page 联合数据时不能声称它属于或不属于某个页面",
        "没有订单或 GA4 数据时不能推断收入或查询级转化",
    ]
    policy_block = current_topic_policy_block(topic)
    if policy_block:
        return TopicAssessment(
            gate_status="blocked",
            overlap=overlap,
            limitations=[
                *limitations,
                policy_block,
            ],
            next_step="拆成一个可独立回答、可验证且与现有文章不同的具体用户任务。",
        )
    if float(overlap.get("max_score") or 0) >= strong:
        return TopicAssessment(
            gate_status="blocked",
            overlap=overlap,
            limitations=limitations,
            next_step="先人工复核最相似的现有内容；优先判断更新而不是新建。",
        )
    return TopicAssessment(
        gate_status="needs_evidence",
        overlap=overlap,
        limitations=limitations,
        next_step=(
            "人工复核搜索意图、现有正文边界和材料质量；只有候选来自 GSC 查询且需判断"
            "旧页归属时，才补 query→page 联合数据。"
        ),
    )
