from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from seo_ops.config import RESEARCH_BUDGET_LIMITS
from seo_ops.rules.evidence_workflow import content_overlap_assessment

RESEARCH_METHOD_VERSION = "multi-source-research-0.15.0"
RESEARCH_PLAN_VERSION = "topic-hypothesis-0.13.0"
QUALIFICATION_RULE_VERSION = "candidate-qualification-1.1.3"

CANDIDATE_QUALIFICATION_RULE = {
    "rule_key": "candidate_qualification",
    "version": "1.1.3",
    "rule_type": "qualification_gate",
    "evidence_level": "C+program_inference",
    "rationale": (
        "候选是否进入新文章建议只取决于它是否与活动博客的主意图重复，或旧文正文"
        "已经覆盖该子题；相邻、细化、跨分支或暂时无法明确归类的方向都应保留给运营者。"
        "范围、意图表达、需求、来源和页面材料分别保存为提示与写作准备度，制作前必须复核，"
        "但不作为发现阶段的资格硬门；只有重复证据足够时才阻断。"
        "规则版本或 CMS 指纹变化后，已合格候选必须重新判定。"
    ),
    "config": {
        "same_intent_identity_score": 0.85,
        "covered_subtopic_identity_score": 0.55,
        "covered_subtopic_coverage_score": 0.5,
        "covered_subtopic_strong_coverage_score": 0.72,
        "ambiguous_min_score": 0.3,
        "demand_min_real_facts": 1,
        "gap_min_score": 0.20,
        "material_min_unique_urls": 2,
        "material_min_distinct_evidence": 2,
        "material_min_page_captures": 1,
        "demand_evidence_roles": ["gsc", "serp_snapshot", "trends_timeseries"],
        "sensitive_requires_official_or_research": True,
    },
    "sources": ["https://developers.google.com/search/docs/fundamentals/creating-helpful-content"],
    "known_failures": [
        "token 重叠不能单独决定意图关系，必须配 H2 与正文匹配",
        "关系不确定不等于重复；没有足够重复证据时会保留候选并显示警告",
        "缺少官方来源的安全或法规主题直接判为材料不足",
        "资料发现结果和页面摘要不能冒充真实需求证据",
        "泛化的激光、功率或安全词项不能替代技术对象的正文比对",
        "站内词项、H2 和正文比对只能降低重复风险，不能保证最终页面一定符合搜索引擎质量要求",
    ],
    "review_after": "2026-10-17",
}

QUALIFICATION_STATUS_QUALIFIED = "qualified"
QUALIFICATION_STATUS_NEEDS_EVIDENCE = "needs_evidence"
QUALIFICATION_STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"
QUALIFICATION_STATUS_BLOCKED = "blocked"
QUALIFICATION_STATUS_STALE = "stale"

RELATIONSHIP_SAME_INTENT = "same_intent"
RELATIONSHIP_COVERED_SUBTOPIC = "covered_subtopic"
RELATIONSHIP_ADJACENT = "adjacent"
RELATIONSHIP_DISTINCT = "distinct"
RELATIONSHIP_UNCERTAIN = "uncertain"

DISPOSITION_NEW_ARTICLE = "new_article"
DISPOSITION_UPDATE_EXISTING = "update_existing"
DISPOSITION_COVERED_EXISTING = "covered_existing"
DISPOSITION_INSUFFICIENT = "insufficient_evidence"

MULTI_SOURCE_TOPIC_RESEARCH_RULE = {
    "rule_key": "multi_source_topic_research",
    "version": "0.15.0",
    "rule_type": "evidence_discovery",
    "evidence_level": "A+C+E+inference",
    "rationale": (
        "从 GSC 信号、主题图谱缺口或边界扩展入口出发，在运营者明确点击后按预算采集"
        "SERP、网页原文与资料发现结果；外部材料只扩展待核验主题，不替代第一方需求或"
        "人工搜索意图判断。主题身份以 H1 和页面核心用户任务为主；安全、功率、波长等"
        "内容只有在本身是核心问题时才作为主要文章主题，否则只是可共享的辅助知识。"
        "候选主题的语言、范围、需求和材料只作为运营提示，不在发现阶段静默丢弃。发现阶段"
        "保留线索；只有与已有文章主意图相同或正文已覆盖的候选不进入新文章建议。"
        "调研先从内容覆盖地图、历史搜索记录和已定义的自然语言假设中选择未覆盖方向；"
        "旧 Plan 的有效部分被复原为多来源的角色×任务×条件任务卡：公开第三方痛点、竞争对手"
        "问题、集群缺口或角色前沿只形成待验证种子，先与 CMS 主意图/H2/正文做重复预筛，再按"
        "来源多样性一次选择多个未试种子。没有任务卡时才回退到问题链；第二条问题链叠加论坛、"
        "评论和问答词形成公开第三方语言探针，并明确标为市场代理而非本站第一方反馈。每轮也会"
        "把 PAA、相关搜索、竞争/商业页面、论坛、评价、社媒/视频及其他公开页面的可核对语言"
        "保存为来源观察；下一轮优先从这些带 URL、摘录和 evidence ID 的观察中续出种子，再由"
        "SERP、资料发现和页面采集验证。AI 必须接收任务卡、分支和原始种子上下文，忠实包装同一"
        "任务，不能擅自增加风险、用途、改装或测量主张。原始线索与成型角度分栏，假设只决定要"
        "验证的问题，不构成需求事实。若上一轮明显集中在同一语义簇，下一轮只对该簇做一次软性"
        "降序；它不是范围门、禁词或永久轮换，缺少其他合适种子时仍可继续使用。开放调度的十二"
        "类来源入口始终搜索整个手持激光笔空间，所选分支只给旧 Plan 任务卡提供上下文，不得被"
        "拼进每一路查询形成隐性收口。所有来源支持且未被 CMS 重复判定拦截的种子继续留在候选"
        "池，只有五个种子继续消耗 API 深挖。最后按主要用户意图合并换说法的同页主题，并与已"
        "存在候选池去重；同一方向下任务、症状、决策、受众、地区或结果不同的细化主题继续保留。"
    ),
    "config": {
        "hard_limits": RESEARCH_BUDGET_LIMITS,
        "reuse_hours": 24,
        "results_per_tavily_search": 5,
        "max_firecrawl_excerpt_chars": 12000,
        "max_topic_candidates": 200,
        "open_scheduler_selected_seed_count": 5,
        "strong_content_overlap": 0.68,
        "branch_relevance_min": 0.4,
        "source_observation_families": [
            "paa_related",
            "competitor_or_commercial",
            "forum_community",
            "review",
            "social_or_video",
            "official_or_reference",
        ],
        "source_observation_max_per_run": 500,
        "cooldown_previous_run_only": True,
        "cooldown_dominant_seed_minimum": 2,
        "cooldown_dominant_seed_share": 0.5,
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
        "外部发现的主题可能没有本站需求事实，只能视为待验证方向而非需求结论",
        "AI 只能整理传入 evidence ID，不能补来源、改分或自动发布",
        "FAQ、常见问题或终极指南等集合词可能把多个独立任务误包装成一篇文章",
        "翻译或混合语言会削弱英语站点的词项重合检查，因此必须显示去重局限",
        "自然语言假设可能没有需求或材料支持，只能作为待验证方向",
        "论坛、评论、问答和其他站点语言是公开第三方市场代理，不能冒充本站第一方反馈",
        "任务卡会耗尽、过时或被 CMS 正确判为重复；没有未重复卡时必须如实回退或报告不足，"
        "不能为了填满结果制造主题",
        "任务卡本身不证明角色实际使用产品、需求存在或内容可安全成稿；这些都要由本轮证据和"
        "人工写作复核承担",
        "来源观察只能证明公开页面在采集时出现了这段语言；它不能证明本站用户、搜索量、产品"
        "适配或事实正确性",
        "上一轮语义簇只能用于下一轮的软性排序。把它当成永久排除或按词硬过滤，会遗漏尚未写过"
        "的细化主题",
        "AI 主意图聚类可能误合并相邻细化主题；它只能合并输入候选 ID，不能新增主题或覆盖 CMS"
        "重复判定，并须保留不同任务、症状、决策、受众、地区和结果",
    ],
    "review_after": "2026-08-14",
}

_GENERIC_COLLECTION_RE = re.compile(
    r"\b(?:faqs?|frequently asked questions?|common questions?|everything you need(?: to know)?|"
    r"ultimate guide|complete guide)\b",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SCHOOL_OR_MINOR_RE = re.compile(
    r"\b(?:school|classroom|student|teacher|teaching|educat(?:e|ion|ional|or|ing)|child(?:ren)?|kid(?:s)?|"
    r"minor|underage|teen(?:ager)?s?)\b",
    re.IGNORECASE,
)


def current_topic_policy_block(topic: str) -> str | None:
    """Return a legacy scope/language diagnostic; it must not decide eligibility."""

    if _CJK_RE.search(topic):
        return "候选包含非英语文本，英文站点的词项去重可能不完整"
    if not re.search(r"[a-z]", topic, flags=re.IGNORECASE):
        return "候选没有可识别的英语任务，英文站点去重可信度较低"
    if _GENERIC_COLLECTION_RE.search(topic):
        return "题名是泛 FAQ 或大而全内容集合，制作时应拆成具体页面职责"
    if _SCHOOL_OR_MINOR_RE.search(topic):
        return "学校、课堂或未成年人场景偏离当前内容边界，运营者可自行决定是否采用"
    return None


@dataclass(frozen=True, slots=True)
class TopicAssessment:
    gate_status: str
    overlap: dict[str, Any]
    limitations: list[str]
    next_step: str


def normalize_topic(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())[:300]


_ADJACENT_PRODUCT_OBJECTS = frozenset(
    {"level", "cutter", "cutting", "engraver", "engraving", "projector", "scanner"}
)
_EXPLICIT_CORE_PRODUCT_REPLACEMENT_RE = re.compile(
    r"\b(?:laser\s+levels?|rotary\s+lasers?|laser\s+cutters?|laser\s+engravers?|"
    r"laser\s+scanners?|laser\s+projectors?)\b",
    re.IGNORECASE,
)


def _topic_replaces_core_product(topic: str, branch_label: str) -> bool:
    branch_words = set(re.findall(r"[a-z0-9]+", branch_label.casefold()))
    topic_words = set(re.findall(r"[a-z0-9]+", topic.casefold()))
    if "pointers" in branch_words:
        branch_words.add("pointer")
    if "pointers" in topic_words:
        topic_words.add("pointer")
    if "levels" in topic_words:
        topic_words.add("level")
    return (
        "pointer" in branch_words
        and "pointer" not in topic_words
        and bool(_ADJACENT_PRODUCT_OBJECTS & topic_words)
    )


def _explicit_core_product_replacement(topic: str) -> bool:
    """Identify an explicit adjacent-product swap for a soft priority diagnostic.

    This is deliberately not an eligibility rule. A candidate can be useful to
    an operator even when it compares or borders a neighbouring laser product;
    the diagnostic only keeps product drift visible in ranking and review.
    """

    return bool(_EXPLICIT_CORE_PRODUCT_REPLACEMENT_RE.search(topic))


def topic_matches_research_branch(topic: str, branch_label: str) -> bool:
    """Keep a candidate in the selected product domain without lexical overfitting.

    Topic-graph branch labels are navigation aids, not a closed keyword list.
    Requiring a proposed title to repeat words such as ``laws`` or ``visibility``
    incorrectly rejects valid sub-directions such as beam termination or
    fluorescence. Qualification and coverage gates still decide whether the
    result is a new page, an existing-page update, or insufficient evidence.
    """

    if not branch_label.strip():
        return True
    branch_words = set(re.findall(r"[a-z0-9]+", branch_label.casefold()))
    topic_words = set(re.findall(r"[a-z0-9]+", topic.casefold()))
    if "laser" in branch_words and "laser" not in topic_words:
        return False
    if {"use", "cases"}.issubset(branch_words):
        return True
    aliases = {
        "pointers": "pointer",
        "levels": "level",
        "presentations": "presentation",
        "classrooms": "classroom",
        "laws": "law",
        "batteries": "battery",
        "safely": "safety",
        "wet": "maintenance",
        "water": "maintenance",
        "dog": "pets",
        "dogs": "pets",
        "cat": "pets",
        "cats": "pets",
        "animal": "pets",
        "animals": "pets",
    }
    branch_words = {aliases.get(word, word) for word in branch_words}
    normalized_topic = {aliases.get(word, word) for word in topic_words}
    # A neighbouring laser product can share the selected use-case words while
    # silently replacing the core object. This is only a drift diagnostic; the
    # duplicate-only qualification rule still retains the candidate.
    if _topic_replaces_core_product(topic, branch_label):
        return False
    if re.search(r"\b\d{3,4}nm\b", topic, re.IGNORECASE):
        normalized_topic.add("wavelength")
    branch_context = branch_words - {"laser", "pointer", "pointers", "and", "for", "the"}
    return bool(branch_context & normalized_topic)


def assess_discovered_topic(topic: str, content_items: list[dict[str, Any]]) -> TopicAssessment:
    """Preserve the legacy overlap audit without using scope as a hard gate."""

    overlap = content_overlap_assessment(topic, content_items)
    strong = float(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["strong_content_overlap"])
    limitations = [
        "该主题来自外部资料发现或相关查询，只是待核验假设",
        "没有本站 query→page 联合数据时不能声称它属于或不属于某个页面",
        "没有订单或 GA4 数据时不能推断收入或查询级转化",
    ]
    policy_note = current_topic_policy_block(topic)
    if policy_note:
        limitations.append(f"运营提示（不阻断）：{policy_note}")
    if float(overlap.get("max_score") or 0) >= strong:
        return TopicAssessment(
            gate_status="blocked",
            overlap=overlap,
            limitations=limitations,
            next_step="先人工复核最相似的现有正文；若主要意图或正文答案重复，优先更新旧文。",
        )
    return TopicAssessment(
        gate_status="needs_evidence",
        overlap=overlap,
        limitations=limitations,
        next_step="该旧审计字段不决定资格；按主意图和正文覆盖结果判断是否重复。",
    )


_INTENT_PLACEHOLDER_RE = re.compile(
    r"(?:requires? operator review|needs? review|\bn/?a\b|\bnone\b|pending review)",
    re.IGNORECASE,
)

SENSITIVE_TOPIC_TOKENS = (
    "safety",
    "eye",
    "skin",
    "law",
    "legal",
    "regulation",
    "fda",
    "cdh",
    "iec",
    "power limit",
    "output power",
    "mw limit",
    "wavelength",
    "class i",
    "class ii",
    "class iii",
    "class iv",
)

COMMUNITY_HOST_PARTS = (
    "reddit.com",
    "quora.com",
    "facebook.com",
    "youtube.com",
    "laserpointerforums.com",
    "blogspot.com",
    "wordpress.com",
    "medium.com",
    "forums",
    "forum",
)

OFFICIAL_HOST_PARTS = (
    ".gov",
    ".mil",
    "law.cornell.edu",
    "fda.gov",
    "cdc.gov",
    "nist.gov",
    "ec.europa.eu",
    "iso.org",
    "iec.ch",
    "edu",
    "oshpd.ca.gov",
)

RESEARCH_HOST_PARTS = (
    "nih.gov",
    "pubmed.ncbi",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "mdpi.com",
    "nejm.org",
    "arxiv.org",
)


def is_sensitive_topic(topic: str, intent: str = "") -> bool:
    """Return True when the topic text engages safety/legal/power/wavelength concerns."""
    text = (str(topic) + " " + str(intent or "")).lower()
    return any(token in text for token in SENSITIVE_TOPIC_TOKENS)


def _placeholder_intent(intent: str) -> bool:
    """Return True when AI returned a placeholder intent instead of a real one."""
    if not intent:
        return True
    candidate = intent.strip().lower()
    if not candidate:
        return True
    if _INTENT_PLACEHOLDER_RE.search(candidate):
        return True
    return False


def _classify_source_role(url: str) -> str:
    """Classify an external URL into 'official_or_research', 'industry', or 'community'."""
    lowered = (url or "").lower()
    if not lowered or not lowered.startswith(("http://", "https://")):
        return "community"
    host = (urlsplit(lowered).hostname or "").removeprefix("www.")
    for part in OFFICIAL_HOST_PARTS:
        if part.lstrip(".") == host or host.endswith(part):
            return "official_or_research"
    for part in RESEARCH_HOST_PARTS:
        if part == host or host.endswith(f".{part}"):
            return "official_or_research"
    for part in COMMUNITY_HOST_PARTS:
        if part == host or host.endswith(f".{part}") or part in host:
            return "community"
    return "industry"


def _classify_source_urls(urls: list[str]) -> dict[str, list[str]]:
    """Group source URLs by their credibility role."""
    grouped: dict[str, list[str]] = {
        "official_or_research": [],
        "industry": [],
        "community": [],
    }
    for url in urls or []:
        role = _classify_source_role(url)
        grouped[role].append(url)
    return grouped


def _matched_h2_sections(topic_tokens: set[str], headings: list[str]) -> list[str]:
    """Return H2 headings whose significant tokens significantly overlap the topic."""
    matched: list[str] = []
    for heading in headings:
        head_tokens = set(_tokens_re(heading))
        if not head_tokens:
            continue
        intersection = topic_tokens & head_tokens
        if not intersection:
            continue
        if len(intersection) >= max(1, min(2, len(topic_tokens) // 2)):
            matched.append(heading)
    return matched


def _tokens_re(text: str) -> set[str]:
    """Core intent tokens used by candidate-to-CMS relationship checks."""
    aliases = {
        "batteries": "battery",
        "classification": "class",
        "classifications": "class",
        "classified": "class",
        "classes": "class",
        "colors": "color",
        "hazard": "safety",
        "hazards": "safety",
        "hazardous": "safety",
        "presentations": "presentation",
        "selecting": "choose",
        "settings": "setting",
        "uses": "use",
        "using": "use",
    }
    stopwords = {
        "and",
        "are",
        "article",
        "based",
        "best",
        "between",
        "case",
        "choose",
        "complete",
        "for",
        "from",
        "guide",
        "how",
        "into",
        "laser",
        "lasers",
        "pointer",
        "pointers",
        "right",
        "that",
        "the",
        "this",
        "use",
        "what",
        "when",
        "why",
        "with",
        "you",
        "your",
    }
    tokens: set[str] = set()
    for match in re.findall(r"[a-z0-9]+", (text or "").lower()):
        token = aliases.get(match, match)
        if len(token) > 2 and token not in stopwords:
            tokens.add(token)
    return tokens


def _distinctive_topic_tokens(topic_tokens: set[str]) -> set[str]:
    """Terms that must appear in old content before a technical topic is covered.

    ``laser``, ``high``, ``power`` and ``safety`` occur across many articles.
    They are useful for finding neighbours but cannot prove that an old article
    answers a task about, for example, beam termination or a specific wavelength.
    """

    generic = {
        "beam",
        "high",
        "power",
        "safe",
        "safely",
        "safety",
        "visible",
    }
    return {
        token
        for token in topic_tokens
        if token not in generic and (any(char.isdigit() for char in token) or len(token) >= 5)
    }


def _core_similarity(query_tokens: set[str], item_tokens: set[str]) -> float:
    if not query_tokens or not item_tokens:
        return 0.0
    intersection = query_tokens & item_tokens
    if not intersection:
        return 0.0
    coverage = len(intersection) / len(query_tokens)
    union = query_tokens | item_tokens
    score = coverage * 0.8 + (len(intersection) / len(union)) * 0.2
    if query_tokens == item_tokens:
        return 1.0
    if query_tokens.issubset(item_tokens):
        return max(score, 0.9)
    return score


@dataclass(frozen=True, slots=True)
class CandidateQualification:
    qualification_status: str
    recommended_disposition: str | None
    relationship: str
    closest_existing: dict[str, Any]
    evidence_demand: dict[str, Any]
    evidence_gap: dict[str, Any]
    evidence_material: dict[str, Any]
    limitations: list[str]
    next_step: str


def compute_closest_existing(topic: str, content_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the closest existing article and characterise the relationship.

    Returns a dict that matches the spec in the design lock point #1:
    closest_existing = { content_item_id, url, title, relationship,
                       identity_score, coverage_score, matched_sections,
                       gap_statement, rule_version, cms_fingerprint }
    """
    article_items = [item for item in content_items if item.get("content_type") == "blog"]
    overlap = content_overlap_assessment(topic, article_items)
    lexical_by_id = {
        int(match.get("content_item_id") or -1): match for match in overlap.get("matches", [])
    }
    topic_tokens = _tokens_re(topic)
    rule_cfg = CANDIDATE_QUALIFICATION_RULE["config"]
    candidate_record: dict[str, Any] = {
        "content_item_id": None,
        "url": None,
        "title": None,
        "relationship": RELATIONSHIP_DISTINCT,
        "identity_score": 0.0,
        "coverage_score": 0.0,
        "matched_sections": [],
        "gap_statement": None,
        "rule_version": QUALIFICATION_RULE_VERSION,
    }
    if not topic_tokens:
        return candidate_record
    content_by_id = {int(item.get("id") or -1): item for item in article_items}
    same_intent_cutoff = float(rule_cfg["same_intent_identity_score"])
    covered_identity_cutoff = float(rule_cfg["covered_subtopic_identity_score"])
    covered_coverage_cutoff = float(rule_cfg["covered_subtopic_coverage_score"])
    strong_coverage_cutoff = float(rule_cfg["covered_subtopic_strong_coverage_score"])
    ambiguous_cutoff = float(rule_cfg["ambiguous_min_score"])

    evaluated: list[dict[str, Any]] = []
    for content_item_id, item in content_by_id.items():
        lexical = lexical_by_id.get(content_item_id, {})
        identity_text = " ".join(str(item.get(key) or "") for key in ("title", "slug", "seo_title"))
        identity_tokens = _tokens_re(identity_text)
        support_tokens = _tokens_re(str(item.get("search_text") or identity_text))
        coverage_tokens = _tokens_re(str(item.get("coverage_text") or ""))
        body_tokens = _tokens_re(str(item.get("_body_text") or ""))
        matched_tokens = topic_tokens & support_tokens
        coverage_matches = topic_tokens & coverage_tokens
        distinctive_tokens = _distinctive_topic_tokens(topic_tokens)
        distinctive_matches = distinctive_tokens & (coverage_tokens | body_tokens)
        identity_score = _core_similarity(topic_tokens, identity_tokens)
        support_score = len(matched_tokens) / len(topic_tokens) * 0.6
        coverage_ratio = len(coverage_matches) / len(topic_tokens)
        if coverage_ratio >= 0.6 and len(coverage_matches) >= min(2, len(topic_tokens)):
            coverage_score = 0.72
            if topic_tokens.issubset(coverage_tokens):
                coverage_score = 0.85
        else:
            coverage_score = coverage_ratio * 0.6
        headings = list(item.get("_h2_sections") or [])
        matched_sections = _matched_h2_sections(topic_tokens, headings)
        effective_score = max(identity_score, support_score, coverage_score)
        if effective_score <= 0 and not matched_sections:
            continue
        if identity_score >= same_intent_cutoff:
            match_relationship = RELATIONSHIP_SAME_INTENT
        elif coverage_score >= strong_coverage_cutoff or (
            identity_score >= covered_identity_cutoff and coverage_score >= covered_coverage_cutoff
        ):
            match_relationship = RELATIONSHIP_COVERED_SUBTOPIC
        elif max(identity_score, coverage_score) >= ambiguous_cutoff and matched_sections:
            match_relationship = RELATIONSHIP_UNCERTAIN
        elif max(identity_score, coverage_score) >= ambiguous_cutoff * 0.5:
            match_relationship = RELATIONSHIP_ADJACENT
        else:
            match_relationship = RELATIONSHIP_DISTINCT
        if (
            match_relationship == RELATIONSHIP_COVERED_SUBTOPIC
            and distinctive_tokens
            and not distinctive_matches
        ):
            # Generic overlap found a neighbouring page, but its headings/body
            # do not contain the technical object that makes this task distinct.
            match_relationship = RELATIONSHIP_UNCERTAIN
        evaluated.append(
            {
                **lexical,
                "content_item_id": content_item_id,
                "content_type": item.get("content_type"),
                "title": item.get("title"),
                "canonical_url": item.get("canonical_url"),
                "score": round(effective_score, 3),
                "identity_score": round(identity_score, 3),
                "support_score": round(support_score, 3),
                "coverage_score": round(coverage_score, 3),
                "matched_tokens": sorted(matched_tokens),
                "distinctive_tokens": sorted(distinctive_tokens),
                "distinctive_matches": sorted(distinctive_matches),
                "lexical_identity_score": float(lexical.get("identity_score") or 0.0),
                "relationship": match_relationship,
                "matched_sections": matched_sections,
            }
        )

    if not evaluated:
        return candidate_record

    relationship_priority = {
        RELATIONSHIP_SAME_INTENT: 4,
        RELATIONSHIP_COVERED_SUBTOPIC: 3,
        RELATIONSHIP_UNCERTAIN: 2,
        RELATIONSHIP_ADJACENT: 1,
        RELATIONSHIP_DISTINCT: 0,
    }
    evaluated.sort(
        key=lambda item: (
            -relationship_priority[str(item["relationship"])],
            -float(item.get("identity_score") or 0.0),
            -float(item.get("coverage_score") or 0.0),
            0 if item.get("content_type") == "blog" else 1,
            int(item.get("content_item_id") or 0),
        )
    )
    best = evaluated[0]
    identity_score = float(best.get("identity_score") or 0.0)
    coverage_score = float(best.get("coverage_score") or 0.0)
    content_item_id = best.get("content_item_id")
    matched_sections = list(best.get("matched_sections") or [])
    relationship = str(best["relationship"])
    broad_matches = [
        match
        for match in evaluated
        if match.get("content_type") == "blog" and float(match.get("score") or 0.0) >= 0.68
    ]
    if len(broad_matches) >= 2 and relationship not in {
        RELATIONSHIP_SAME_INTENT,
        RELATIONSHIP_COVERED_SUBTOPIC,
    }:
        relationship = RELATIONSHIP_UNCERTAIN
    candidate_record.update(
        {
            "content_item_id": content_item_id,
            "url": best.get("canonical_url"),
            "title": best.get("title"),
            "relationship": relationship,
            "identity_score": round(identity_score, 3),
            "coverage_score": round(coverage_score, 3),
            "matched_sections": matched_sections,
            "gap_statement": (
                "该候选同时接近多篇现有文章，需人工判断是独立页面职责还是跨页重复。"
                if len(broad_matches) >= 2
                else None
            ),
            "rule_version": QUALIFICATION_RULE_VERSION,
            "related_existing": [
                {
                    "content_item_id": match.get("content_item_id"),
                    "url": match.get("canonical_url"),
                    "title": match.get("title"),
                    "relationship": match.get("relationship"),
                    "identity_score": match.get("identity_score"),
                    "coverage_score": match.get("coverage_score"),
                }
                for match in evaluated[:3]
            ],
        }
    )
    return candidate_record


def compute_cms_fingerprint(content_items: list[dict[str, Any]]) -> str:
    """Stable hash of the current CMS body used for stale detection."""
    import hashlib as _hashlib

    payload = []
    for item in content_items:
        item_id = int(item.get("id") or 0)
        title = str(item.get("title") or "")
        headings = list(item.get("_h2_sections") or [])
        body_head = str(item.get("_body_head") or "")
        payload.append(
            f"{item_id}|{title.lower()}|{'|'.join(h.lower() for h in headings)}|{body_head[:200].lower()}"
        )
    fingerprint_input = "\n".join(sorted(payload))
    return _hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:16]


def compute_evidence_fingerprint(evidence_ids: list[str], source_urls: list[str]) -> str:
    """Stable hash of the candidate evidence snapshot for stale detection."""
    import hashlib as _hashlib

    norm_ids = sorted({str(ref or "").strip().lower() for ref in evidence_ids if ref})
    norm_urls = sorted({str(url or "").strip().lower() for url in source_urls if url})
    fingerprint_input = f"ids={','.join(norm_ids)}\nurls={','.join(norm_urls)}"
    return _hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:16]


def _check_demand(facts: list[str], evidence_ids: list[str]) -> dict[str, Any]:
    """Real user demand: at least one fact backed by a real evidence_id."""
    rule_cfg = CANDIDATE_QUALIFICATION_RULE["config"]
    min_real = int(rule_cfg["demand_min_real_facts"])
    demand_roles = set(rule_cfg["demand_evidence_roles"])
    demand_ids = [
        evidence_id
        for evidence_id in evidence_ids or []
        if any(
            str(evidence_id).casefold().endswith(role.casefold())
            or f":{role.casefold()}:" in str(evidence_id).casefold()
            for role in demand_roles
        )
    ]
    real = []
    for fact in facts or []:
        text = str(fact or "")
        for ev_id in demand_ids:
            if ev_id and str(ev_id) in text:
                real.append(text)
                break
    if len(real) >= min_real:
        return {
            "ok": True,
            "evidence": real,
            "evidence_ids": demand_ids,
            "missing": [],
        }
    return {
        "ok": False,
        "evidence": real,
        "evidence_ids": demand_ids,
        "missing": ["缺少能引用真实搜索、SERP、PAA 或用户问句的需求证据"],
    }


def _check_material(
    source_urls: list[str],
    evidence_ids: list[str],
    topic: str,
    intent: str,
) -> dict[str, Any]:
    """Writing material quality: enough verified sources, and sensitive topics need official."""
    rule_cfg = CANDIDATE_QUALIFICATION_RULE["config"]
    grouped = _classify_source_urls(source_urls)
    unique_urls = sorted({u for u in source_urls if u})
    distinct_evidence = sorted({e for e in evidence_ids if e})
    page_captures = sorted(
        {
            evidence_id
            for evidence_id in distinct_evidence
            if str(evidence_id).casefold().endswith("page_capture")
        }
    )
    issues: list[str] = []
    if len(unique_urls) < int(rule_cfg["material_min_unique_urls"]):
        issues.append(
            f"存证来源 URL 少于 {rule_cfg['material_min_unique_urls']} 条，不足以独立写完"
        )
    if len(distinct_evidence) < int(rule_cfg["material_min_distinct_evidence"]):
        issues.append(f"独立 evidence ID 少于 {rule_cfg['material_min_distinct_evidence']} 条")
    if len(page_captures) < int(rule_cfg["material_min_page_captures"]):
        issues.append("缺少页面级原文采集；SERP 和资料发现只能用于发现，不能单独支撑写作")
    sensitive = is_sensitive_topic(topic, intent)
    if sensitive and not grouped["official_or_research"]:
        issues.append("安全、法规或功率等敏感主题必须至少有一条官方/研究来源，社区线索不能单独成立")
    if not grouped["official_or_research"] and not grouped["industry"]:
        issues.append("存证来源仅含社区线索，缺少行业或官方可核验来源")
    return {
        "ok": not issues,
        "official_or_research": grouped["official_or_research"],
        "industry": grouped["industry"],
        "community": grouped["community"],
        "sensitive_topic": sensitive,
        "page_capture_evidence": page_captures,
        "missing": issues,
    }


def _check_gap(closest_existing: dict[str, Any]) -> dict[str, Any]:
    """Content gap evidence: relationship must indicate a real uncovered area."""
    rule_cfg = CANDIDATE_QUALIFICATION_RULE["config"]
    relationship = closest_existing.get("relationship") or RELATIONSHIP_DISTINCT
    coverage = float(closest_existing.get("coverage_score") or 0.0)
    identity = float(closest_existing.get("identity_score") or 0.0)
    if relationship == RELATIONSHIP_SAME_INTENT:
        return {
            "ok": False,
            "relationship": relationship,
            "missing": ["现有文章主意图基本相同，主题应作为旧文更新而非新文章"],
            "gap_score": 0.0,
        }
    if relationship == RELATIONSHIP_COVERED_SUBTOPIC:
        return {
            "ok": False,
            "relationship": relationship,
            "missing": ["现有文章已包含该子主题，应在旧文内补段落而非另起一篇"],
            "gap_score": 0.0,
        }
    if relationship == RELATIONSHIP_DISTINCT:
        return {
            "ok": True,
            "relationship": relationship,
            "missing": [],
            "gap_score": max(1.0 - coverage, 0.0),
        }
    if relationship == RELATIONSHIP_ADJACENT:
        gap_score = min(1.0 - coverage, max(0.3, 1.0 - identity))
        return {
            "ok": gap_score >= float(rule_cfg["gap_min_score"]),
            "relationship": relationship,
            "missing": [],
            "gap_score": gap_score,
        }
    # Under duplicate-only qualification, uncertainty is not proof of
    # duplication. Keep the direction and expose the uncertainty as a warning.
    return {
        "ok": True,
        "relationship": relationship,
        "missing": [],
        "warning": "与最接近文章的关系不明确；未发现足够的重复证据，因此保留候选",
        "gap_score": max(0.0, min(1.0 - coverage, 0.4)),
    }


def qualify_candidate(
    topic: str,
    intent: str,
    facts: list[str],
    source_urls: list[str],
    evidence_ids: list[str],
    content_items: list[dict[str, Any]],
    *,
    human_review_reason: str | None = None,
    human_relationship: str | None = None,
    research_label: str | None = None,
    seed_anchor_fit: str | None = None,
    seed_anchor_note: str | None = None,
) -> CandidateQualification:
    """Apply a duplicate-only eligibility rule.

    The only blocking outcomes are:
      1. The active blog already has the same primary intent.
      2. The active blog body already answers the candidate as a covered subtopic.

    Scope, branch alignment, intent wording, demand and source readiness remain
    visible diagnostics. An uncertain relationship is retained because lack of
    certainty is not evidence of duplication.
    """
    limitations = [
        "主题候选来自外部资料发现或相关查询，仍属待验证假设",
        "无本站 query→page 联合数据时不能声称它属于或不属于某个页面",
        "无订单或 GA4 数据时不能推断收入或查询级转化",
    ]
    policy_note = current_topic_policy_block(topic)
    if policy_note:
        limitations.append(f"范围或表达提示（不阻断）：{policy_note}")
    core_product_replaced = _explicit_core_product_replacement(topic) or (
        bool(research_label) and _topic_replaces_core_product(topic, str(research_label))
    )
    if core_product_replaced:
        limitations.append(
            "种子锚点诊断（不阻断）：候选明确换成了相邻激光产品对象；"
            "这不等于与现有文章重复，但会降低优先级"
        )
    elif research_label and not topic_matches_research_branch(topic, research_label):
        limitations.append(
            "种子分支诊断（不阻断）：候选未复述本轮分支词项或场景；"
            "只要激光笔仍是核心对象，它可以是有效的新受众或细化任务，"
            "不因跨分支自动降权且不等于与现有文章重复"
        )
    normalized_anchor_fit = str(seed_anchor_fit or "").strip().casefold()
    if normalized_anchor_fit == "adjacent":
        limitations.append(
            "AI 种子锚点判断（不阻断）：候选跨到相邻任务；只要激光笔仍承担核心作用，"
            "保留且不因跨分支自动降权"
        )
    elif normalized_anchor_fit == "off_anchor":
        limitations.append("AI 种子锚点判断（不阻断）：候选偏离本轮核心对象；保留但降低优先级")
    if seed_anchor_note and normalized_anchor_fit in {"adjacent", "off_anchor"}:
        limitations.append(f"AI 锚点说明：{str(seed_anchor_note).strip()[:300]}")
    if _placeholder_intent(intent):
        limitations.append(
            "intent 为空或是占位文本；当前仅依据候选题名与 CMS 标题、H2 和正文做去重"
        )

    closest_existing = compute_closest_existing(topic, content_items)
    program_relationship = closest_existing.get("relationship") or RELATIONSHIP_DISTINCT
    if human_relationship == RELATIONSHIP_COVERED_SUBTOPIC:
        closest_existing["program_relationship"] = program_relationship
        closest_existing["human_relationship"] = RELATIONSHIP_COVERED_SUBTOPIC
        closest_existing["relationship"] = RELATIONSHIP_COVERED_SUBTOPIC
        closest_existing["gap_statement"] = "运营者已对照正文并确认旧文覆盖该候选。"
    elif (
        human_relationship == RELATIONSHIP_DISTINCT
        and program_relationship == RELATIONSHIP_UNCERTAIN
    ):
        closest_existing["program_relationship"] = program_relationship
        closest_existing["human_relationship"] = RELATIONSHIP_DISTINCT
        closest_existing["relationship"] = RELATIONSHIP_DISTINCT
        closest_existing["gap_statement"] = (
            "运营者已对照最接近页面并确认候选承担独立的主要用户任务。"
        )

    demand = _check_demand(facts, evidence_ids)
    material = _check_material(source_urls, evidence_ids, topic, intent)
    gap = _check_gap(closest_existing)
    relationship = closest_existing.get("relationship") or RELATIONSHIP_DISTINCT

    if relationship == RELATIONSHIP_SAME_INTENT:
        return CandidateQualification(
            qualification_status=QUALIFICATION_STATUS_BLOCKED,
            recommended_disposition=DISPOSITION_UPDATE_EXISTING,
            relationship=relationship,
            closest_existing=closest_existing,
            evidence_demand=demand,
            evidence_gap=gap,
            evidence_material=material,
            limitations=[*limitations, "与最接近文章的主要意图基本相同"],
            next_step=(
                "现有文章主意图已覆盖该主题；不要新建，请改写或局部更新现有文章 #"
                f"{closest_existing.get('content_item_id') or '?'}。"
            ),
        )
    if relationship == RELATIONSHIP_COVERED_SUBTOPIC:
        return CandidateQualification(
            qualification_status=QUALIFICATION_STATUS_BLOCKED,
            recommended_disposition=DISPOSITION_COVERED_EXISTING,
            relationship=relationship,
            closest_existing=closest_existing,
            evidence_demand=demand,
            evidence_gap=gap,
            evidence_material=material,
            limitations=[*limitations, "现有文章正文已包含该子主题"],
            next_step=(
                "应优先在现有文章 #"
                f"{closest_existing.get('content_item_id') or '?'} 中扩展，"
                "不要新建独立文章。"
            ),
        )

    readiness_notes = [
        *([] if demand["ok"] else demand["missing"]),
        *([] if material["ok"] else material["missing"]),
    ]
    if relationship == RELATIONSHIP_UNCERTAIN:
        readiness_notes.append(
            "程序无法确定与最接近文章的关系；因没有足够重复证据而保留，制作前应人工对照"
        )
    return CandidateQualification(
        qualification_status=QUALIFICATION_STATUS_QUALIFIED,
        recommended_disposition=DISPOSITION_NEW_ARTICLE,
        relationship=relationship,
        closest_existing=closest_existing,
        evidence_demand=demand,
        evidence_gap=gap,
        evidence_material=material,
        limitations=[*limitations, *readiness_notes],
        next_step=(
            "未发现同主意图或旧文正文已覆盖的充分证据，候选予以保留；"
            "写作前仍需补齐可追溯材料，并确保成稿提供独立答案。"
        ),
    )


def _empty_closest_existing() -> dict[str, Any]:
    return {
        "content_item_id": None,
        "url": None,
        "title": None,
        "relationship": RELATIONSHIP_DISTINCT,
        "identity_score": 0.0,
        "coverage_score": 0.0,
        "matched_sections": [],
        "gap_statement": None,
        "rule_version": QUALIFICATION_RULE_VERSION,
        "related_existing": [],
    }
