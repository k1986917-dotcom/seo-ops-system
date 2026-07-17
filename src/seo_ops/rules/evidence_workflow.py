from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

METHOD_VERSION = "evidence-workflow-0.4.1"
PLAN_VERSION = "action-plan-0.3.0"

EXTERNAL_QUERY_REVIEW_RULE = {
    "rule_key": "external_query_review",
    "version": "0.3.0",
    "rule_type": "evidence_gate",
    "evidence_level": "A+C",
    "rationale": (
        "只对明确的 GSC 查询候选采集固定国家、语言、设备和时间范围的 SERP/Trends "
        "快照；外部结果补充环境证据，不覆盖第一方指标或重新计算原始分数。"
    ),
    "config": {
        "reuse_hours": 24,
        "max_query_length": 100,
        "organic_result_depth": 10,
        "purposes": ["serp_snapshot", "trends_timeseries"],
    },
    "sources": [
        "https://serpapi.com/search-api",
        "https://serpapi.com/google-trends-api",
        "https://support.google.com/trends/answer/4365533?hl=en",
    ],
    "known_failures": [
        "SERP 是时点快照，不代表稳定排名",
        "Trends 是归一化相对兴趣，低值或零值不代表无人搜索",
        "供应商或网络失败时只能保留已成功的部分证据",
    ],
    "review_after": "2026-08-14",
}

NEW_ARTICLE_CANDIDATE_RULE = {
    "rule_key": "new_article_candidate",
    "version": "0.4.1",
    "rule_type": "eligibility_gate",
    "evidence_level": "A+C+inference",
    "rationale": (
        "新文章必须从真实 GSC 查询信号出发，并先检查当前 SERP、本站已有结果和 CMS "
        "内容重叠；主题身份使用英语规范词、标题和 slug 做程序重合检查。缺少 query→page "
        "联合数据或人工搜索意图复核时只能成为待补证据候选。"
    ),
    "config": {
        "strong_content_overlap": 0.68,
        "structured_coverage_min": 0.75,
        "structured_coverage_score": 0.72,
        "organic_result_depth": 10,
        "required_manual_checks": ["query_page_mapping", "search_intent_review"],
        "confidence_weight": 0.4,
        "effort": 4.0,
    },
    "sources": [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
        "https://support.google.com/webmasters/answer/7042828?hl=en",
    ],
    "known_failures": [
        "词项重叠只表示程序推断的内容相似风险，不证明查询属于某页面",
        "SERP 前十未出现本站不证明本站完全没有相关页面",
        "没有订单或 GA4 数据时不能推断收入或查询级转化",
    ],
    "review_after": "2026-08-14",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "article",
    "are",
    "best",
    "between",
    "can",
    "choose",
    "complete",
    "does",
    "for",
    "guide",
    "how",
    "in",
    "is",
    "laser",
    "lasers",
    "of",
    "on",
    "or",
    "pointer",
    "pointers",
    "right",
    "the",
    "to",
    "ultimate",
    "what",
    "with",
    "why",
    "you",
    "your",
}
_TOKEN_ALIASES = {
    "batteries": "battery",
    "basic": "base",
    "buying": "buy",
    "charger": "charge",
    "chargers": "charge",
    "charging": "charge",
    "choosing": "choose",
    "classrooms": "classroom",
    "cleaning": "clean",
    "fixing": "fix",
    "gratings": "grating",
    "laws": "law",
    "legal": "law",
    "problems": "problem",
    "purchase": "buy",
    "purchasing": "buy",
    "presentations": "presentation",
    "regulation": "law",
    "regulations": "law",
    "safety": "safe",
    "selecting": "choose",
    "sensors": "sensor",
    "settings": "setting",
    "troubleshooting": "troubleshoot",
    "uses": "use",
    "using": "use",
    "visibility": "visible",
}


@dataclass(frozen=True, slots=True)
class NewArticleAssessment:
    gate_status: str
    gate_reasons: list[str]
    recommended_action: str
    missing: list[str]
    overlap: dict[str, Any]
    site_results: list[dict[str, Any]]
    limitations: list[str]


def _tokens(value: str) -> set[str]:
    result: set[str] = set()
    for raw_token in _TOKEN_RE.findall(value or ""):
        token = _TOKEN_ALIASES.get(raw_token.lower(), raw_token.lower())
        if token not in _STOPWORDS:
            result.add(token)
    return result


def _content_overlap(query: str, content_items: list[dict[str, Any]]) -> dict[str, Any]:
    query_tokens = _tokens(query)
    matches: list[dict[str, Any]] = []
    if not query_tokens:
        return {
            "kind": "program_inference",
            "method": "cms_primary_topic_and_structured_coverage_v3",
            "max_score": 0.0,
            "candidate_tokens": [],
            "matches": [],
        }

    for item in content_items:
        identity_text = " ".join(str(item.get(key) or "") for key in ("title", "slug", "seo_title"))
        identity_tokens = _tokens(identity_text)
        support_tokens = _tokens(str(item.get("search_text") or identity_text))
        coverage_tokens = _tokens(str(item.get("coverage_text") or ""))
        identity_intersection = query_tokens & identity_tokens
        support_intersection = query_tokens & support_tokens
        coverage_intersection = query_tokens & coverage_tokens
        if not support_intersection and not coverage_intersection:
            continue
        identity_coverage = len(identity_intersection) / len(query_tokens)
        identity_union = query_tokens | identity_tokens
        identity_jaccard = (
            len(identity_intersection) / len(identity_union) if identity_union else 0.0
        )
        identity_score = identity_coverage * 0.8 + identity_jaccard * 0.2
        if query_tokens == identity_tokens:
            identity_score = 1.0
        elif query_tokens.issubset(identity_tokens):
            identity_score = max(identity_score, 0.9)
        support_coverage = len(support_intersection) / len(query_tokens)
        support_score = support_coverage * 0.6
        coverage_ratio = len(coverage_intersection) / len(query_tokens)
        config = NEW_ARTICLE_CANDIDATE_RULE["config"]
        if (
            coverage_ratio >= float(config["structured_coverage_min"])
            and len(coverage_intersection) >= 2
        ):
            coverage_score = float(config["structured_coverage_score"])
            if query_tokens.issubset(coverage_tokens):
                coverage_score = max(coverage_score, 0.85)
        else:
            coverage_score = coverage_ratio * 0.6
        score = round(max(identity_score, support_score, coverage_score), 3)
        matches.append(
            {
                "content_item_id": item.get("id"),
                "content_type": item.get("content_type"),
                "title": item.get("title"),
                "canonical_url": item.get("canonical_url"),
                "score": score,
                "identity_score": round(identity_score, 3),
                "support_score": round(support_score, 3),
                "coverage_score": round(coverage_score, 3),
                "matched_tokens": sorted(support_intersection),
                "identity_tokens": sorted(identity_tokens),
                "coverage_tokens": sorted(coverage_intersection),
            }
        )
    matches.sort(key=lambda item: (-float(item["score"]), int(item["content_item_id"] or 0)))
    return {
        "kind": "program_inference",
        "method": "cms_primary_topic_and_structured_coverage_v3",
        "max_score": matches[0]["score"] if matches else 0.0,
        "candidate_tokens": sorted(query_tokens),
        "matches": matches[:5],
    }


def content_overlap_assessment(topic: str, content_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose the governed CMS lexical-overlap inference to other workflows."""

    return _content_overlap(topic, content_items)


def _normalized_host(value: str) -> str:
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


def assess_new_article_candidate(
    *,
    query: str,
    site_domain: str,
    content_items: list[dict[str, Any]],
    serp_payload: dict[str, Any],
) -> NewArticleAssessment:
    config = NEW_ARTICLE_CANDIDATE_RULE["config"]
    overlap = _content_overlap(query, content_items)
    site_host = _normalized_host(site_domain if "://" in site_domain else f"https://{site_domain}")
    site_results = [
        item
        for item in serp_payload.get("organic_results", [])
        if _normalized_host(str(item.get("link") or "")) == site_host
    ]

    limitations = [
        "SERP 仅证明采集时、指定口径下看到的结果",
        "CMS 词项重叠是程序推断，不是 GSC 查询—页面归属证据",
        "候选不包含收入、成功率或查询级转化预测",
    ]
    missing = list(config["required_manual_checks"])

    if site_results:
        return NewArticleAssessment(
            gate_status="blocked",
            gate_reasons=[
                "GSC 查询提供第一方需求信号",
                f"SERP 前 {config['organic_result_depth']} 条中已出现本站页面",
                "新建相近文章存在内容重叠风险",
            ],
            recommended_action=(
                "暂不新建文章；先核对 SERP 中的本站页面及定向 GSC query→page 数据，"
                "再决定更新现有页面或保持不动。"
            ),
            missing=missing,
            overlap=overlap,
            site_results=site_results,
            limitations=limitations,
        )

    if float(overlap["max_score"]) >= float(config["strong_content_overlap"]):
        return NewArticleAssessment(
            gate_status="blocked",
            gate_reasons=[
                "GSC 查询提供第一方需求信号",
                "当前 SERP 前十未发现本站结果",
                "CMS 标题/元数据出现强词项重叠，需先排除蚕食",
            ],
            recommended_action=(
                "暂不新建文章；先人工复核最相似的现有内容，并导出该查询的网页维度。"
            ),
            missing=missing,
            overlap=overlap,
            site_results=[],
            limitations=limitations,
        )

    return NewArticleAssessment(
        gate_status="needs_evidence",
        gate_reasons=[
            "GSC 查询提供第一方需求信号",
            "已保存固定口径 SERP 快照，前十未发现本站结果",
            "CMS 标题/元数据未命中强重叠门槛",
            "仍缺少 query→page 联合数据与人工搜索意图复核",
        ],
        recommended_action=(
            "把它保留为新文章候选；先补定向 GSC query→page 导出并人工复核 SERP 意图，"
            "门槛通过后再建立 research brief。"
        ),
        missing=missing,
        overlap=overlap,
        site_results=[],
        limitations=limitations,
    )
