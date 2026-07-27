from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection
from seo_ops.services.ai import AIProvider, AIResponse, AIUnavailable, build_ai_provider
from seo_ops.services.material_workflow import (
    MaterialWorkflowError,
    build_material_preview,
)
from seo_ops.services.material_workflow import (
    confirm_materials as confirm_content_materials,
)
from seo_ops.utils import json_dumps, json_loads, utc_now


class ContentProductionError(RuntimeError):
    pass


CONTENT_WORKFLOW_VERSION = "evidence-bound-content-production-0.9.0"
MAX_INTERNAL_CANDIDATES = 12
MAX_FINAL_INTERNAL_LINKS = 10
MAX_FINAL_EXTERNAL_LINKS = 12
FAQ_MIN = 3
FAQ_MAX = 4
INTRO_WORD_RANGE = (150, 250)
CONCLUSION_WORD_RANGE = (150, 200)
ARTICLE_WORD_RANGES = {
    "Pillar Page": (2500, 4000),
    "Cluster Content": (1200, 2500),
    "Product Roundup": (2000, 3500),
}
ARTICLE_AI_NORMAL_CALLS = 3
ARTICLE_AI_HARD_CAP = 10

GENERIC_LINK_ANCHORS = {
    "click here",
    "learn more",
    "more information",
    "read more",
    "related guide",
    "this article",
    "this guide",
}
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
PUBLIC_URL_RE = re.compile(r"https?://[^\s)\]>]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


SYSTEM_PROMPT = """You are the senior editor for an existing LaserPointerHub page. Produce a
practical English CMS update from the supplied query-page evidence, confirmed material pack and
current page.
Every reader-facing CMS field and every passage of article copy must be English. Never translate
an English site topic into Chinese or mix languages.
Never invent tests, ownership, authors, quotes, laws, measurements, or product facts.
Keep the original topic, intent, URL and slug. Choose the smallest
change that solves the diagnosed problem: metadata_only, partial_update, or same_topic_rewrite.
Open with a direct answer or the evidenced reader problem. Preserve useful current sections
outside the diagnosed gap. Use natural prose, specific headings, useful contextual internal links,
and only source URLs present in the input. Do not introduce market sizing, prices, retailer
comparisons, discounts, product rankings or promotional calls to action. Avoid filler, generic
AI phrasing and repeated safety boilerplate unless safety is necessary to the user task.
Treat the title/H1 and the page's main user task as its primary-topic identity.
Safety, power and wavelength may recur as supporting knowledge; treat them as duplicates only
when they are themselves the page's main task. Communities and forums are question or
experience signals, not authority for laws, safety or specifications. Prefer official,
regulatory, standards, research and named industry primary sources. Treat the confirmed material
pack as a claim boundary: every changed factual statement must be traceable to it or to the
supplied query-page evidence. If evidence is insufficient, choose the smallest safe change and
state the limitation instead of inventing a factual rewrite.
Return strict JSON:
{
"change_type":"metadata_only|partial_update|same_topic_rewrite",
 "reason":"plain explanation",
 "title":"CMS Title",
 "slug":"slug",
 "summary":"CMS Summary",
 "content":"complete Markdown or exact replacement blocks",
 "tags":"comma separated",
 "seo_title":"SEO Title",
 "seo_description":"SEO Description",
 "seo_keywords":"comma separated",
 "internal_links":[{"url":"site URL","anchor":"natural anchor","placement":"where"}],
 "external_sources":[{"url":"provided source URL","supports":"specific fact","source_role":"input role"}],
 "self_review":["checks completed"],
 "operator_note":"what to paste or replace"
}"""


EXISTING_REVIEW_PROMPT = """You are the final editor for an existing LaserPointerHub page update.
Audit the proposed change against the current page, the page's actual GSC query-page evidence,
the confirmed material pack, and the supplied URL whitelist. Return a complete corrected CMS
deliverable, not comments.

Keep the original topic, intent, URL and slug. Use the smallest justified change:
metadata_only, partial_update, or same_topic_rewrite. A page-level metric alone is not a reason
to rewrite; use the supplied query-page rows to identify the reader task. All reader-facing CMS
fields and copy must be natural English. Remove Chinese text, generic AI filler, repeated safety
boilerplate, unsupported claims, fabricated experience and unapproved URLs. Preserve useful
existing material when the evidence does not justify replacing it. For partial_update, provide
unambiguous replacement sections and exact operator instructions. For same_topic_rewrite,
provide a complete, substantial Markdown article that does not become a different topic.
Do not add market or price content, retailer comparisons, rankings, discounts, promotional CTAs,
or a claim that an author tested or used a product.

Return the same strict JSON schema requested in the draft prompt. Do not add sources or facts."""


EXISTING_REVISION_PROMPT = """Revise an existing-page CMS deliverable that failed deterministic
quality checks. Fix every supplied blocking issue while preserving the current page's topic,
original slug, query-page evidence and URL whitelist. Use the smallest justified change and
English only. Do not invent evidence, experience, sources or factual claims. Return the same
strict JSON schema with the complete corrected deliverable."""


MATERIAL_PROMPT = """You are the research editor for LaserPointerHub. Build the article-specific
material pack before any drafting. Use only the supplied evidence, source inventory, current
site content and product records. Do not turn a community comment into proof of safety, law,
specifications or performance. Do not invent first-hand testing, quotes, numbers or sources.
Find the primary user task, one-sentence answer, real user friction, SERP/competitor gaps,
information gain, content boundaries, product fit and a claim-to-source plan. Select only URLs
from available_external_sources. Prefer official/research or named primary industry sources
for factual claims. A source count is not a quality score.
Return strict JSON:
{
 "primary_task":"one concrete user task",
 "audience":"specific reader",
 "search_intent":"informational|how_to|comparison|purchase|safety_or_regulatory",
 "article_type":"explanation|how_to|comparison|purchase|safety_or_regulatory",
 "core_answer":"direct answer",
 "differentiation":"specific information gain",
 "pain_points":[{"insight":"paraphrased problem","source_url":"allowed URL or empty"}],
 "competitor_gaps":["verifiable missing or weak coverage"],
 "authoritative_facts":[{"claim":"fact to use","url":"allowed URL","source_role":"input role"}],
 "product_fit":[{"product_url":"allowed internal product URL","reason":"natural relevance"}],
 "content_boundaries":["what this page owns or must not duplicate"],
 "source_plan":[{"url":"allowed URL","supports":"specific claim or reader need"}],
 "limitations":["remaining uncertainty"],
 "material_gate":{"status":"pass|fail","reasons":["plain reasons"]}
}"""


OUTLINE_PROMPT = """You are the planning editor for LaserPointerHub. Turn the approved material pack
into an article-specific outline. Preserve one primary user task. Always plan 4-5 genuine FAQ
questions from the evidence or search intent. Do not force JSON-LD or a product link. Plan 4-8
useful H2 sections and
H3 only when needed. Put factual claims beside their allowed sources. Choose contextual links
while planning, not after drafting: normally 2-3 internal links and 2-6 external citations when
the material supports them, never more than the supplied limits, and never repeat a URL.
Return strict JSON:
{
 "title_candidates":["three specific H1 options"],
 "selected_title":"best H1",
 "slug":"short lowercase slug",
 "page_promise":"what the reader can accomplish",
 "intro_approach":"specific opening situation or direct answer",
 "sections":[{
   "heading":"H2 heading",
   "purpose":"job of this section",
   "key_points":["points to cover"],
   "claims":[{"claim":"supported claim","source_url":"allowed external URL"}],
   "internal_links":[{"url":"allowed internal URL","anchor":"descriptive anchor"}],
   "external_links":[{"url":"allowed external URL","anchor":"descriptive anchor"}]
 }],
 "faq_questions":["four or five genuine questions from evidence and search intent"],
 "conclusion_job":"decision or next action, not a generic recap",
 "optional_elements":["table/checklist only if useful"],
 "content_boundaries":["overlap to avoid"]
}"""


DRAFT_FIRST_PROMPT = """You are the writer for LaserPointerHub. Write the H1, a direct non-generic
introduction of 100-150 English words and the first half of the approved outline in natural
English Markdown. Use only the material pack and planned links. Embed links where they support
a claim. Put no link in the introduction, and never use generic anchor text such as click here,
read more, this guide or related guide. Do not add unplanned URLs, fabricated experience,
empty expert claims, boilerplate safety paragraphs or a premature conclusion. Keep terminology
consistent.
Return strict JSON:
{
 "content":"first-half Markdown",
 "used_links":["URLs actually embedded"],
 "terminology":["terms whose wording must stay consistent"],
 "open_threads":["points the second half must finish"]
}"""


DRAFT_SECOND_PROMPT = """You are continuing the same LaserPointerHub article. First inspect the supplied
first half for terminology, repetition, logic and link distribution. Then write only the remaining
H2/H3 sections, an FAQ section with exactly 4-5 supplied genuine questions, and a conclusion
of 80-120 English words in natural English Markdown. Do not repeat the H1, introduction or
earlier sections. Resolve every open thread. Use only planned URLs and never repeat a URL.
Links must be contextual and distributed across useful body sections; do not introduce a new
link in the conclusion.
Return strict JSON:
{
 "stitching_notes":["issues checked or corrected"],
 "content":"second-half Markdown",
 "used_links":["URLs actually embedded"],
 "unresolved":["anything that still lacks evidence"]
}"""


REVIEW_PROMPT = """You are the final editor for LaserPointerHub. Audit the complete draft against the
material pack, outline, source roles and existing-site boundary. Correct the article instead of
merely listing problems. Check the primary intent, factual traceability, heading flow, contextual
link placement, descriptive anchors, repeated URLs, unsupported claims, template-like prose and
overlap with existing pages. Enforce a 100-150 word introduction, 4-5 genuine FAQ questions and
an 80-120 word conclusion. Never invent evidence or first-hand experience. Do not add URLs
outside the allowed plans, the introduction or the conclusion. Keep every external URL identical
to the stored source and map it to the specific claim it supports. Return a clean CMS deliverable
in strict JSON:
{
 "change_type":"new_article",
 "reason":"why this article and structure are justified",
 "title":"CMS Title",
 "slug":"slug",
 "summary":"CMS Summary",
 "content":"complete final Markdown including one H1",
 "tags":"comma separated",
 "seo_title":"SEO Title",
 "seo_description":"SEO Description",
 "seo_keywords":"comma separated",
 "internal_links":[{"url":"used internal URL","anchor":"actual anchor","placement":"section"}],
 "external_sources":[{"url":"used external URL","supports":"specific claim","source_role":"input role"}],
 "self_review":["specific checks completed"],
 "operator_note":"what the operator should do",
 "quality_gate":{"status":"pass|fail","issues":["remaining blocking issues"]}
}"""


REVISION_PROMPT = """You are revising a LaserPointerHub CMS deliverable that failed deterministic
quality checks. Fix every supplied issue while preserving the approved material pack, outline
and allowed URLs. Do not evade a check by deleting useful evidence or inventing replacements.
Return the same strict JSON schema as the final review, with the full corrected article."""


SKILL_WRITE_PROMPT = """You are the senior editor and writer for LaserPointerHub. Use only the
supplied confirmed writing material pack,
current CMS pages and allowed URLs. Produce one complete English article in one pass.

Non-negotiable boundaries:
- Never invent ownership, testing, first-hand experience, authors, quotes, cases, measurements,
  product specifications, laws or sources. A community page is a user-question/case signal, not
  authority for safety, law or specifications.
- Keep one primary user task. The H1/main task defines duplication; recurring support about
  safety, wavelength or power is not a duplicate unless it becomes the page's primary task.
- Use exactly one H1, a direct 150-250 word introduction, 3-5 Key Takeaways, 4-7 useful main H2
  sections, a 150-200 word conclusion, and 3-4 visible FAQ questions followed by matching
  FAQPage JSON-LD. Use the supplied article tier and word range.
- Choose links while structuring the article. Use only allowed URLs, descriptive natural anchors,
  no repeated URL, no link in the introduction or conclusion, and distribute links across the
  body. Approximate density: one related blog per 1000 words, one product per 1300 words only
  when commercially relevant, and one factual external citation per 800 words. Never force an
  unrelated link merely to meet a count. Product promotion must not appear in the first 40%.
- Do not introduce market size, price ranges, retailer comparisons, discounts or product ranking
  tables. Link a product only where it helps the reader complete the stated task.
- Open with the reader's concrete situation or a direct answer, not a stock challenge, a seller
  comparison, or a claim that the author tested something. Avoid generic AI filler, repetitive
  safety boilerplate and paragraphs longer than four sentences. Do not jump from H2 to H4.
- SEO title must be 50-60 characters and SEO description 150-160 characters.

Return strict JSON:
{
 "change_type":"new_article",
 "reason":"why this page has a distinct user task and information gain",
 "article_tier":"Pillar Page|Cluster Content|Product Roundup",
 "primary_keyword":"one natural primary phrase",
 "title":"CMS Title",
 "slug":"short lowercase slug",
 "summary":"CMS Summary",
 "content":"complete Markdown with visible FAQ and a fenced FAQPage JSON-LD block",
 "tags":"comma separated",
 "seo_title":"50-60 characters",
 "seo_description":"150-160 characters",
 "seo_keywords":"comma separated",
 "internal_links":[{"url":"used allowed URL","anchor":"actual anchor","placement":"section"}],
 "external_sources":[{"url":"used allowed URL","supports":"specific claim","source_role":"supplied role"}],
 "self_review":["specific completed checks"],
 "operator_note":"what the operator should review and paste",
 "quality_gate":{"status":"pass|fail","issues":["remaining issue"]}
}"""


SKILL_REVISION_PROMPT = """Revise the complete LaserPointerHub CMS deliverable once. Fix every
deterministic blocking issue supplied by the system while preserving the confirmed material pack,
article tier, primary user task and URL whitelist. Do not invent or add evidence. Return the same
strict JSON schema with the full corrected article. This is the only permitted AI revision."""


SKILL_PLAN_PROMPT = """You are the planning editor for LaserPointerHub. The operator has already
confirmed the deterministic writing-material inventory. Build an article-specific plan before writing.
Use only supplied facts and allowed URLs. Keep one primary user task, state the information gain,
plan 4-7 main H2 sections, 3-4 genuine FAQ questions, the primary keyword, source-bound claims,
and contextual internal/external links. Use the supplied article tier and word range. Never invent
experience, sources, specifications or safety thresholds. Return strict JSON:
{
 "primary_task":"one reader task",
 "primary_keyword":"one natural phrase",
 "title":"specific H1",
 "slug":"short slug",
 "page_promise":"what the reader can do",
 "information_gain":"why this is not a duplicate",
 "sections":[{
   "heading":"H2",
   "purpose":"job of this section",
   "key_points":["specific points"],
   "claims":[{"claim":"supported claim","source_url":"allowed URL"}],
   "internal_links":[{"url":"allowed URL","anchor":"descriptive anchor"}],
   "external_links":[{"url":"allowed URL","anchor":"descriptive anchor"}]
 }],
 "faq_questions":["three or four sourced/search-intent questions"],
 "conclusion_job":"decision or next action",
 "content_boundaries":["overlap and unsupported claims to avoid"],
 "plan_gate":{"status":"pass|fail","issues":["blocking issue"]}
}"""


SKILL_EDIT_PROMPT = """You are the final editor for LaserPointerHub. Review the complete draft
against the confirmed material pack, approved plan, article tier, allowed URLs and CMS fields.
Return a complete corrected CMS deliverable, not a list of suggestions. Enforce the approved
word range, 150-250 word introduction, 3-5 Key Takeaways, 4-7 main H2 sections, 150-200 word
conclusion, 3-4 visible FAQ questions with matching FAQPage JSON-LD, natural link distribution,
50-60 character SEO title, 150-160 character SEO description, short paragraphs and valid heading
hierarchy. Remove unsupported facts, fake first-hand experience, generic AI filler and duplicate
coverage. Do not add any URL or factual claim outside the confirmed material. Return the same
strict JSON schema as the complete draft."""


def _extract_public_urls(value: Any) -> set[str]:
    urls: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            urls.update(_extract_public_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.update(_extract_public_urls(item))
    elif isinstance(value, str):
        candidate = value.strip()
        parsed = urlsplit(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            urls.add(candidate)
    return urls


def _source_role(value: Any) -> str:
    urls = _extract_public_urls(value)
    hosts = {(urlsplit(url).hostname or "").lower().removeprefix("www.") for url in urls}
    official_suffixes = (
        ".gov",
        ".gov.uk",
        ".gc.ca",
        ".edu",
        ".ac.uk",
        ".europa.eu",
        ".int",
    )
    official_domains = {"iso.org", "iec.ch"}
    if any(host in official_domains or host.endswith(official_suffixes) for host in hosts):
        return "official_or_research"
    community_domains = {
        "reddit.com",
        "quora.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "youtube.com",
    }
    if any(
        host == domain or host.endswith(f".{domain}")
        for host in hosts
        for domain in community_domains
    ):
        return "community_signal"
    return "industry_or_other"


def _external_material_record(item: Any) -> dict[str, Any]:
    payload = json_loads(item["payload_json"], {})
    limitations = json_loads(item["limitations_json"], [])
    source_ref = str(item["source_ref"] or "")
    source_urls = sorted(_extract_public_urls({"source_ref": source_ref, "payload": payload}))
    return {
        "evidence_id": item["evidence_id"],
        "evidence_type": item["evidence_type"],
        "source_ref": source_ref,
        "captured_at": item["captured_at"],
        "payload": payload,
        "limitations": limitations,
        "source_urls": source_urls,
        "source_roles": {url: _source_role(url) for url in source_urls},
    }


def _clean_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not cleaned:
        raise ContentProductionError("AI 没有生成可用的 Slug")
    return cleaned


def _normalized_title(value: str) -> str:
    return " ".join(
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in {"a", "an", "and", "for", "of", "the", "to"}
    )


STOPWORDS = {
    "about",
    "after",
    "best",
    "from",
    "guide",
    "have",
    "laser",
    "pointer",
    "that",
    "the",
    "this",
    "under",
    "what",
    "when",
    "with",
}


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _select_internal_candidates(
    links: list[Any],
    topic: str,
    *,
    target_url: str | None = None,
) -> list[dict[str, Any]]:
    topic_tokens = _tokens(topic)
    commercial = bool(topic_tokens & {"buy", "choice", "choose", "compare", "product"})
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for raw in links:
        item = dict(raw)
        url = str(item.get("canonical_url") or "")
        if not url or url == target_url:
            continue
        title_tokens = _tokens(item.get("title"))
        summary_tokens = _tokens(item.get("summary"))
        score = 4 * len(topic_tokens & title_tokens) + len(topic_tokens & summary_tokens)
        if commercial and item.get("content_type") == "product":
            score += 2
        compact = {
            "title": item.get("title"),
            "slug": item.get("slug"),
            "canonical_url": url,
            "content_type": item.get("content_type"),
            "summary": str(item.get("summary") or "")[:360],
        }
        scored.append((score, str(item.get("title") or "").casefold(), compact))
    scored.sort(key=lambda value: (-value[0], value[1]))
    selected = [item for score, _, item in scored if score > 0][:MAX_INTERNAL_CANDIDATES]
    return selected[:MAX_INTERNAL_CANDIDATES]


def _clip_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, str):
        return value[:1800]
    if isinstance(value, list):
        return [_clip_value(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        return {
            str(key): _clip_value(item, depth=depth + 1) for key, item in list(value.items())[:28]
        }
    return value


def _external_source_inventory(
    evidence: dict[str, Any],
    external_material: list[dict[str, Any]],
    internal_urls: set[str],
) -> list[dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    evidence_ids = [str(value) for value in evidence.get("evidence_ids", []) if value]
    for material in external_material:
        evidence_id = str(material.get("evidence_id") or "")
        for url in material.get("source_urls") or []:
            if url in internal_urls:
                continue
            record = inventory.setdefault(
                str(url),
                {
                    "url": str(url),
                    "source_role": _source_role(url),
                    "evidence_ids": [],
                },
            )
            role = (material.get("source_roles") or {}).get(url)
            if role:
                record["source_role"] = role
            if evidence_id and evidence_id not in record["evidence_ids"]:
                record["evidence_ids"].append(evidence_id)
    for url in sorted(_extract_public_urls(evidence)):
        if url in internal_urls:
            continue
        record = inventory.setdefault(
            url,
            {"url": url, "source_role": _source_role(url), "evidence_ids": []},
        )
        for evidence_id in evidence_ids:
            if evidence_id not in record["evidence_ids"]:
                record["evidence_ids"].append(evidence_id)
    role_order = {"official_or_research": 0, "industry_or_other": 1, "community_signal": 2}
    return sorted(
        inventory.values(),
        key=lambda item: (role_order.get(str(item["source_role"]), 3), str(item["url"])),
    )


def _is_sensitive_topic(value: str) -> bool:
    tokens = _tokens(value)
    return bool(
        tokens
        & {
            "class",
            "eye",
            "legal",
            "law",
            "power",
            "radiation",
            "regulation",
            "safety",
            "sensor",
            "standard",
            "wavelength",
        }
    )


def _require_text(content: dict[str, Any], key: str, label: str | None = None) -> str:
    value = content.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContentProductionError(f"AI 返回的{label or key}不完整")
    return value.strip()


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return (
        [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    )


def _text_list(value: Any) -> list[str]:
    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _word_count(value: str) -> int:
    without_urls = PUBLIC_URL_RE.sub("", value)
    return len(re.findall(r"\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*\b", without_urls))


def _validate_material_pack(
    content: dict[str, Any],
    *,
    source_inventory: list[dict[str, Any]],
    sensitive: bool,
) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ContentProductionError("AI 没有返回结构化素材包")
    result = dict(content)
    for key, label in (
        ("primary_task", "主要用户任务"),
        ("audience", "目标读者"),
        ("search_intent", "搜索意图"),
        ("article_type", "文章类型"),
        ("core_answer", "一句话答案"),
        ("differentiation", "差异化价值"),
    ):
        result[key] = _require_text(result, key, label)

    result["pain_points"] = _dict_list(result.get("pain_points"))
    result["competitor_gaps"] = _text_list(result.get("competitor_gaps"))
    result["authoritative_facts"] = _dict_list(result.get("authoritative_facts"))
    result["product_fit"] = _dict_list(result.get("product_fit"))
    result["content_boundaries"] = _text_list(result.get("content_boundaries"))
    result["limitations"] = _text_list(result.get("limitations"))
    if not result["pain_points"]:
        raise ContentProductionError("专项素材包缺少真实用户问题，已停止生成空泛文章")
    if not result["competitor_gaps"]:
        raise ContentProductionError("专项素材包没有形成可验证的内容缺口")

    allowed = {str(item["url"]): str(item["source_role"]) for item in source_inventory}
    source_plan: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _dict_list(result.get("source_plan")):
        url = str(item.get("url") or "").strip()
        supports = str(item.get("supports") or "").strip()
        if not url or url not in allowed or url in seen or not supports:
            continue
        source_plan.append({"url": url, "supports": supports, "source_role": allowed[url]})
        seen.add(url)
    result["source_plan"] = source_plan

    gate = result.get("material_gate") if isinstance(result.get("material_gate"), dict) else {}
    gate_reasons = _text_list(gate.get("reasons"))
    if str(gate.get("status") or "").casefold() == "fail":
        reason = "；".join(gate_reasons[:3]) or "素材门槛未通过"
        raise ContentProductionError(f"专项素材不足：{reason}")
    if len(source_plan) < 2:
        raise ContentProductionError("专项素材至少需要两个可核实来源，当前不足")
    if not any(item["source_role"] != "community_signal" for item in source_plan):
        raise ContentProductionError("当前素材只有社区线索，不能据此制作事实性文章")
    if sensitive and not any(item["source_role"] == "official_or_research" for item in source_plan):
        raise ContentProductionError("该主题涉及安全、法规或技术事实，但素材中没有官方/研究来源")
    result["material_gate"] = {"status": "pass", "reasons": gate_reasons}
    return result


def _validate_outline(
    content: dict[str, Any],
    *,
    internal_urls: set[str],
    external_urls: set[str],
) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ContentProductionError("AI 没有返回结构化大纲")
    result = dict(content)
    for key, label in (
        ("selected_title", "选定标题"),
        ("slug", "Slug"),
        ("page_promise", "页面承诺"),
        ("intro_approach", "开头方式"),
        ("conclusion_job", "结尾任务"),
    ):
        result[key] = _require_text(result, key, label)
    sections = _dict_list(result.get("sections"))
    if not 4 <= len(sections) <= 8:
        raise ContentProductionError("文章大纲应包含 4–8 个与任务直接相关的 H2")

    faq_questions = _text_list(result.get("faq_questions"))
    if not FAQ_MIN <= len(faq_questions) <= FAQ_MAX:
        raise ContentProductionError("文章大纲必须规划 4–5 个真实 FAQ 问题")
    result["faq_questions"] = faq_questions

    normalized_sections: list[dict[str, Any]] = []
    seen_internal: set[str] = set()
    seen_external: set[str] = set()
    for raw in sections:
        section = dict(raw)
        section["heading"] = _require_text(section, "heading", "H2 标题")
        section["purpose"] = _require_text(section, "purpose", "章节目的")
        section["key_points"] = _text_list(section.get("key_points"))
        section["claims"] = _dict_list(section.get("claims"))
        internal_plan: list[dict[str, str]] = []
        for item in _dict_list(section.get("internal_links")):
            url = str(item.get("url") or "").strip()
            if (
                url in internal_urls
                and url not in seen_internal
                and len(seen_internal) < MAX_FINAL_INTERNAL_LINKS
            ):
                internal_plan.append({"url": url, "anchor": str(item.get("anchor") or "").strip()})
                seen_internal.add(url)
        external_plan: list[dict[str, str]] = []
        for item in _dict_list(section.get("external_links")):
            url = str(item.get("url") or "").strip()
            if (
                url in external_urls
                and url not in seen_external
                and len(seen_external) < MAX_FINAL_EXTERNAL_LINKS
            ):
                external_plan.append({"url": url, "anchor": str(item.get("anchor") or "").strip()})
                seen_external.add(url)
        section["internal_links"] = internal_plan
        section["external_links"] = external_plan
        normalized_sections.append(section)
    required_internal = min(2, len(internal_urls))
    if len(seen_internal) < required_internal:
        raise ContentProductionError("大纲没有规划足够的相关站内链接")
    required_external = min(3, len(external_urls))
    if len(seen_external) < required_external:
        raise ContentProductionError("大纲没有把已核实来源安排到足够的具体章节")
    result["sections"] = normalized_sections
    result["planned_internal_urls"] = sorted(seen_internal)
    result["planned_external_urls"] = sorted(seen_external)
    result["title_candidates"] = _text_list(result.get("title_candidates"))
    result["optional_elements"] = _text_list(result.get("optional_elements"))
    result["content_boundaries"] = _text_list(result.get("content_boundaries"))
    return result


def _validate_draft_part(content: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ContentProductionError(f"AI 没有返回{label}")
    result = dict(content)
    result["content"] = _require_text(result, "content", label)
    result["used_links"] = _text_list(result.get("used_links"))
    return result


def _safe_stage_error(exc: Exception) -> str:
    if isinstance(exc, AIUnavailable):
        return "AI 未配置"
    if isinstance(exc, httpx.TimeoutException):
        return "AI 请求超时"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"AI HTTP {exc.response.status_code}"
    if isinstance(exc, (json.JSONDecodeError, KeyError, ValueError)):
        return "AI 返回格式无效"
    if isinstance(exc, ContentProductionError):
        return str(exc)[:240]
    return f"{type(exc).__name__}: 文章制作阶段失败"[:240]


def _record_ai_stage(
    settings: Settings,
    task: dict[str, Any],
    *,
    purpose: str,
    provider_name: str,
    model: str,
    prompt_sha256: str,
    input_refs: list[str],
    status: str,
    output: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    with connection(settings) as conn:
        conn.execute(
            """
            INSERT INTO ai_runs(
                site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                input_refs_json, output_json, status, error_message, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["site_id"],
                task["opportunity_id"],
                purpose,
                provider_name,
                model,
                prompt_sha256,
                json_dumps(input_refs),
                json_dumps(output) if output is not None else None,
                status,
                error_message,
                utc_now(),
            ),
        )


async def _complete_stage(
    settings: Settings,
    task: dict[str, Any],
    provider: AIProvider,
    *,
    purpose: str,
    prompt: str,
    payload: dict[str, Any],
    input_refs: list[str],
    validator,
) -> dict[str, Any]:
    fallback_hash = hashlib.sha256(
        json_dumps({"system": prompt, "user": payload}).encode("utf-8")
    ).hexdigest()
    try:
        response: AIResponse = await provider.complete_json(prompt, payload)
    except Exception as exc:
        _record_ai_stage(
            settings,
            task,
            purpose=purpose,
            provider_name=settings.ai_provider,
            model=settings.ai_model or "unconfigured",
            prompt_sha256=fallback_hash,
            input_refs=input_refs,
            status="failed",
            error_message=_safe_stage_error(exc),
        )
        if isinstance(exc, AIUnavailable):
            raise
        if isinstance(exc, httpx.TimeoutException):
            message = "AI 生成超时，任务和已完成阶段均已保留，可以安全重试"
        elif isinstance(exc, httpx.HTTPStatusError):
            message = f"AI 服务返回 HTTP {exc.response.status_code}；任务已保留，可以安全重试"
        elif isinstance(exc, (json.JSONDecodeError, KeyError, ValueError)):
            message = "AI 返回格式无效；失败阶段已记录，任务已保留，可以安全重试"
        else:
            message = "文章制作阶段失败；失败类型已记录，任务已保留，可以安全重试"
        raise ContentProductionError(message) from exc
    try:
        validated = validator(response.content)
    except Exception as exc:
        _record_ai_stage(
            settings,
            task,
            purpose=purpose,
            provider_name=response.provider,
            model=response.model,
            prompt_sha256=response.prompt_sha256,
            input_refs=input_refs,
            status="failed",
            output=response.content,
            error_message=_safe_stage_error(exc),
        )
        if isinstance(exc, ContentProductionError):
            raise
        raise ContentProductionError("AI 返回内容未通过本阶段质量门槛") from exc
    _record_ai_stage(
        settings,
        task,
        purpose=purpose,
        provider_name=response.provider,
        model=response.model,
        prompt_sha256=response.prompt_sha256,
        input_refs=input_refs,
        status="success",
        output=validated,
    )
    return validated


def _validate_skill_plan(
    content: dict[str, Any],
    *,
    internal_inventory: dict[str, dict[str, Any]],
    source_inventory: dict[str, dict[str, Any]],
    article_tier: str,
    word_min: int,
) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ContentProductionError("AI 没有返回结构化文章计划")
    result = dict(content)
    for key, label in (
        ("primary_task", "主要用户任务"),
        ("primary_keyword", "主关键词"),
        ("title", "H1"),
        ("slug", "Slug"),
        ("page_promise", "页面承诺"),
        ("information_gain", "信息增量"),
        ("conclusion_job", "结论任务"),
    ):
        result[key] = _require_text(result, key, label)
    result["slug"] = _clean_slug(result["slug"])
    sections = _dict_list(result.get("sections"))
    if not 4 <= len(sections) <= 7:
        raise ContentProductionError("文章计划必须包含 4–7 个主要 H2")
    normalized_sections: list[dict[str, Any]] = []
    selected_internal: list[str] = []
    selected_external: list[str] = []
    for raw in sections:
        section = dict(raw)
        section["heading"] = _require_text(section, "heading", "H2 标题")
        section["purpose"] = _require_text(section, "purpose", "章节目的")
        section["key_points"] = _text_list(section.get("key_points"))
        section["claims"] = _dict_list(section.get("claims"))
        internal_links = []
        for item in _dict_list(section.get("internal_links")):
            url = str(item.get("url") or "").strip()
            if url in internal_inventory and url not in selected_internal:
                internal_links.append({"url": url, "anchor": str(item.get("anchor") or "").strip()})
                selected_internal.append(url)
        external_links = []
        for item in _dict_list(section.get("external_links")):
            url = str(item.get("url") or "").strip()
            if url in source_inventory and url not in selected_external:
                external_links.append({"url": url, "anchor": str(item.get("anchor") or "").strip()})
                selected_external.append(url)
        section["internal_links"] = internal_links
        section["external_links"] = external_links
        normalized_sections.append(section)

    faq_questions = _text_list(result.get("faq_questions"))
    if not FAQ_MIN <= len(faq_questions) <= FAQ_MAX:
        raise ContentProductionError("文章计划必须包含 3–4 个真实 FAQ 问题")
    gate = result.get("plan_gate") if isinstance(result.get("plan_gate"), dict) else {}
    if str(gate.get("status") or "pass").casefold() == "fail":
        reasons = _text_list(gate.get("issues"))
        raise ContentProductionError("文章计划未通过：" + ("；".join(reasons[:3]) or "计划不完整"))

    blog_urls = [
        url for url, item in internal_inventory.items() if item.get("content_type") != "product"
    ]
    product_urls = [
        url for url, item in internal_inventory.items() if item.get("content_type") == "product"
    ]
    required_blogs = min(len(blog_urls), max(2, round(word_min / 1000)))
    required_products = (
        min(len(product_urls), max(1, round(word_min / 1300)))
        if article_tier == "Product Roundup"
        else 0
    )
    required_external = min(len(source_inventory), max(2, round(word_min / 800)))
    for url in blog_urls:
        if len([item for item in selected_internal if item in blog_urls]) >= required_blogs:
            break
        if url not in selected_internal:
            selected_internal.append(url)
    for url in product_urls:
        if len([item for item in selected_internal if item in product_urls]) >= required_products:
            break
        if url not in selected_internal:
            selected_internal.append(url)
    for url in source_inventory:
        if len(selected_external) >= required_external:
            break
        if url not in selected_external:
            selected_external.append(url)

    result["sections"] = normalized_sections
    result["faq_questions"] = faq_questions
    result["content_boundaries"] = _text_list(result.get("content_boundaries"))
    result["planned_internal_urls"] = selected_internal
    result["planned_external_urls"] = selected_external
    result["article_tier"] = article_tier
    result["word_range"] = list(ARTICLE_WORD_RANGES[article_tier])
    result["plan_gate"] = {"status": "pass", "issues": []}
    return result


def _normalize_new_deliverable(
    content: dict[str, Any],
    *,
    outline: dict[str, Any],
    internal_inventory: dict[str, dict[str, Any]],
    source_inventory: dict[str, dict[str, Any]],
    existing_titles: set[str],
    existing_slugs: set[str],
    sensitive: bool,
    article_tier: str = "Cluster Content",
) -> tuple[dict[str, Any], list[str]]:
    result = dict(content) if isinstance(content, dict) else {}
    issues: list[str] = []
    required = (
        "title",
        "slug",
        "summary",
        "content",
        "tags",
        "seo_title",
        "seo_description",
        "seo_keywords",
    )
    for key in required:
        if not isinstance(result.get(key), str) or not str(result.get(key)).strip():
            issues.append(f"CMS 字段 {key} 缺失")
            result[key] = str(result.get(key) or "")
    result["change_type"] = "new_article"
    try:
        result["slug"] = _clean_slug(str(result.get("slug") or ""))
    except ContentProductionError:
        issues.append("Slug 无效")
        result["slug"] = ""
    normalized_title = _normalized_title(str(result.get("title") or ""))
    if normalized_title and normalized_title in existing_titles:
        issues.append("标题与现有文章重复")
    if str(result.get("slug") or "").casefold() in existing_slugs:
        issues.append("Slug 与现有内容重复")

    body = str(result.get("content") or "")
    json_blocks = re.findall(r"```(?:json)?\s*(.*?)```", body, flags=re.IGNORECASE | re.DOTALL)
    faq_schema_blocks = [block for block in json_blocks if "FAQPage" in block]
    prose_body = re.sub(r"```(?:json)?\s*.*?```", "", body, flags=re.IGNORECASE | re.DOTALL)

    result["article_tier"] = (
        article_tier if article_tier in ARTICLE_WORD_RANGES else "Cluster Content"
    )
    word_min, word_max = ARTICLE_WORD_RANGES.get(
        result["article_tier"], ARTICLE_WORD_RANGES["Cluster Content"]
    )
    word_count = _word_count(prose_body)
    if not word_min <= word_count <= word_max:
        issues.append(
            f"{result['article_tier']} 正文应为 {word_min}–{word_max} 个英文词，当前约 {word_count}"
        )

    h1_count = len(re.findall(r"^#\s+\S", prose_body, flags=re.MULTILINE))
    h2_headings = re.findall(r"^##\s+(.+)$", prose_body, flags=re.MULTILINE)
    if h1_count != 1:
        issues.append("正文必须只有一个 H1")
    special_h2 = {"key takeaways", "quick specs", "faq", "frequently asked questions", "conclusion"}
    main_h2 = [heading for heading in h2_headings if heading.casefold().strip() not in special_h2]
    if not 4 <= len(main_h2) <= 7:
        issues.append(f"正文需要 4–7 个主要 H2，当前为 {len(main_h2)} 个")
    duplicate_headings = [
        heading
        for heading, count in Counter(h.casefold().strip() for h in h2_headings).items()
        if count > 1
    ]
    if duplicate_headings:
        issues.append("正文存在重复 H2")
    heading_levels = [
        len(match.group(1))
        for match in re.finditer(r"^(#{1,6})\s+\S", prose_body, flags=re.MULTILINE)
    ]
    if any(
        previous == 2 and current >= 4
        for previous, current in zip(heading_levels, heading_levels[1:], strict=False)
    ):
        issues.append("标题层级从 H2 跳到了 H4 或更深")

    h1_match = re.search(r"^#\s+.+$", prose_body, flags=re.MULTILINE)
    first_h2_match = re.search(r"^##\s+.+$", prose_body, flags=re.MULTILINE)
    if h1_match and first_h2_match:
        intro_words = _word_count(prose_body[h1_match.end() : first_h2_match.start()])
        if not INTRO_WORD_RANGE[0] <= intro_words <= INTRO_WORD_RANGE[1]:
            issues.append("导语必须为 150–250 个英文词")
    else:
        issues.append("无法识别 H1 后的导语")

    takeaways_match = re.search(
        r"^##\s+Key Takeaways\s*$", prose_body, flags=re.IGNORECASE | re.MULTILINE
    )
    if takeaways_match:
        tail = prose_body[takeaways_match.end() :]
        next_h2 = re.search(r"^##\s+.+$", tail, flags=re.MULTILINE)
        block = tail[: next_h2.start()] if next_h2 else tail
        takeaway_count = len(re.findall(r"^\s*[-*]\s+\S", block, flags=re.MULTILINE))
        if not 3 <= takeaway_count <= 5:
            issues.append("Key Takeaways 必须包含 3–5 条")
    else:
        issues.append("正文缺少 Key Takeaways")

    faq_match = re.search(
        r"^##\s+(?:FAQ|Frequently Asked Questions)\s*$",
        prose_body,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    visible_questions: list[str] = []
    if faq_match:
        faq_tail = prose_body[faq_match.end() :]
        next_h2 = re.search(r"^##\s+.+$", faq_tail, flags=re.MULTILINE)
        faq_block = faq_tail[: next_h2.start()] if next_h2 else faq_tail
        visible_questions = re.findall(r"^###\s+(.+\?)\s*$", faq_block, flags=re.MULTILINE)
        if not FAQ_MIN <= len(visible_questions) <= FAQ_MAX:
            issues.append("FAQ 必须包含 3–4 个真实问题")
    else:
        issues.append("正文缺少 FAQ 章节")

    if not faq_schema_blocks:
        issues.append("正文缺少与可见 FAQ 对应的 FAQPage JSON-LD")
    else:
        schema_questions: list[str] = []
        valid_schema = False
        for block in faq_schema_blocks:
            try:
                schema = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(schema, dict) and schema.get("@type") == "FAQPage":
                valid_schema = True
                for item in schema.get("mainEntity") or []:
                    if isinstance(item, dict) and item.get("name"):
                        schema_questions.append(str(item["name"]).strip())
        if not valid_schema:
            issues.append("FAQPage JSON-LD 不是有效 JSON")
        elif {" ".join(item.casefold().split()) for item in visible_questions} != {
            " ".join(item.casefold().split()) for item in schema_questions
        }:
            issues.append("FAQPage JSON-LD 与页面可见问题不一致")

    conclusion_match = re.search(
        r"^##\s+Conclusion\s*$", prose_body, flags=re.IGNORECASE | re.MULTILINE
    )
    if conclusion_match:
        conclusion_words = _word_count(prose_body[conclusion_match.end() :])
        if not CONCLUSION_WORD_RANGE[0] <= conclusion_words <= CONCLUSION_WORD_RANGE[1]:
            issues.append("结论必须为 150–200 个英文词")
    else:
        issues.append("正文缺少 Conclusion 章节")

    primary_keyword = str(result.get("primary_keyword") or "").strip()
    result["primary_keyword"] = primary_keyword
    if not primary_keyword:
        issues.append("缺少文章主关键词")
    else:
        keyword = " ".join(primary_keyword.casefold().split())
        h1_text = h1_match.group(0).casefold() if h1_match else ""
        first_100_words = " ".join(
            re.findall(r"\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*\b", prose_body)[:100]
        ).casefold()
        if keyword not in str(result.get("title") or "").casefold() or keyword not in h1_text:
            issues.append("主关键词没有自然出现在 Title 和 H1")
        if keyword not in first_100_words:
            issues.append("主关键词没有自然出现在正文前 100 个词")
        if sum(keyword in heading.casefold() for heading in main_h2) < 2:
            issues.append("主关键词没有自然出现在至少两个主要 H2")

    if not 50 <= len(str(result.get("seo_title") or "").strip()) <= 60:
        issues.append("SEO Title 应为 50–60 个字符")
    if not 150 <= len(str(result.get("seo_description") or "").strip()) <= 160:
        issues.append("SEO Description 应为 150–160 个字符")

    for paragraph in re.split(r"\n\s*\n", prose_body):
        compact = paragraph.strip()
        if not compact or compact.startswith(("#", "-", "*", "|", ">")):
            continue
        if len(re.findall(r"[.!?](?:[\"')\]]?)(?:\s|$)", compact)) > 4:
            issues.append("正文存在超过 4 句的长段落")
            break

    if CHINESE_RE.search(prose_body):
        issues.append("英文正文夹杂中文")
    if re.search(
        r"\b(?:market size|price range|discount|retailer|cheapest|best price)\b",
        prose_body,
        flags=re.IGNORECASE,
    ):
        issues.append("正文不应加入市场或价格内容")
    banned = (
        "it is important to note that",
        "in conclusion, it can be said that",
        "as experts, we believe",
        "in the ever-evolving world of",
        "it goes without saying that",
        "it is worth mentioning that",
    )
    if any(phrase in prose_body.casefold() for phrase in banned):
        issues.append("正文仍包含旧 skill 标记的通用 AI 套话")
    if re.search(
        r"\b(?:we|i|our team)\s+(?:tested|measured|used|found|observed)\b",
        prose_body,
        flags=re.IGNORECASE,
    ):
        issues.append("正文声称了素材包中没有核实的第一手经验")

    planned_internal = set(outline.get("planned_internal_urls") or [])
    planned_external = set(outline.get("planned_external_urls") or [])
    markdown_links = MARKDOWN_LINK_RE.findall(body)
    used_urls = [url.rstrip(".,;") for _, url in markdown_links]
    raw_urls = [url.rstrip(".,;") for url in PUBLIC_URL_RE.findall(prose_body)]
    unknown = {
        url for url in raw_urls if url not in planned_internal and url not in planned_external
    }
    if unknown:
        issues.append("正文包含未经过素材或大纲批准的链接")
    repeated = [url for url, count in Counter(used_urls).items() if count > 1]
    if repeated:
        issues.append("正文重复使用了同一个链接")

    for anchor, url in markdown_links:
        normalized_anchor = " ".join(anchor.casefold().split())
        clean_url = url.rstrip(".,;")
        position = body.find(f"]({url})")
        if normalized_anchor in GENERIC_LINK_ANCHORS and "正文仍有泛化链接锚文本" not in issues:
            issues.append("正文仍有泛化链接锚文本")
        if clean_url in internal_inventory:
            target_tokens = _tokens(internal_inventory[clean_url].get("title"))
            if target_tokens and not (_tokens(anchor) & target_tokens):
                if "内链锚文本没有说明目标页面主题" not in issues:
                    issues.append("内链锚文本没有说明目标页面主题")
        if first_h2_match and position < first_h2_match.start():
            if "导语中不能放链接" not in issues:
                issues.append("导语中不能放链接")
        if conclusion_match and position > conclusion_match.start():
            if "结论中不能新增链接" not in issues:
                issues.append("结论中不能新增链接")
    used_internal = [url for url in used_urls if url in internal_inventory]
    used_external = [url for url in used_urls if url in source_inventory]
    if len(set(used_internal)) > MAX_FINAL_INTERNAL_LINKS:
        issues.append("正文内链超过任务卡允许的可读性上限")
    if len(set(used_external)) > MAX_FINAL_EXTERNAL_LINKS:
        issues.append("正文外链超过任务卡允许的可读性上限")
    available_blog_urls = {
        url
        for url in planned_internal
        if url in internal_inventory and internal_inventory[url].get("content_type") != "product"
    }
    available_product_urls = {
        url
        for url in planned_internal
        if url in internal_inventory and internal_inventory[url].get("content_type") == "product"
    }
    used_blog_urls = set(used_internal) & available_blog_urls
    used_product_urls = set(used_internal) & available_product_urls
    required_blogs = min(len(available_blog_urls), max(2, round(word_count / 1000)))
    if len(used_blog_urls) < required_blogs:
        issues.append(f"正文需要约 {required_blogs} 个真正相关的文章内链")
    required_products = (
        min(len(available_product_urls), max(1, round(word_count / 1300)))
        if result["article_tier"] == "Product Roundup"
        else 0
    )
    if len(used_product_urls) < required_products:
        issues.append(f"商业文章需要约 {required_products} 个真正相关的产品内链")
    required_external = min(len(planned_external), max(2, round(word_count / 800)))
    if len(set(used_external)) < required_external:
        issues.append(f"正文需要约 {required_external} 个可核实的事实来源")
    if used_external and not any(
        source_inventory[url]["source_role"] != "community_signal" for url in set(used_external)
    ):
        issues.append("正文引用全部来自社区，不能支撑事实性文章")
    if sensitive and not any(
        source_inventory[url]["source_role"] == "official_or_research" for url in set(used_external)
    ):
        issues.append("安全、法规或技术主题没有在正文中引用官方/研究来源")
    if any(body.find(f"]({url})") < len(body) * 0.4 for url in used_product_urls):
        issues.append("产品推广出现得过早，应放在正文后 60%")
    link_positions = [body.find(f"]({url})") for url in dict.fromkeys(used_urls)]
    if len(link_positions) >= 3 and body:
        buckets = Counter(
            min(2, max(0, int(position / max(1, len(body)) * 3))) for position in link_positions
        )
        if max(buckets.values()) / len(link_positions) > 0.8:
            issues.append("链接过度集中在文章同一区域")

    declared_internal = {
        str(item.get("url") or ""): item for item in _dict_list(result.get("internal_links"))
    }
    result["internal_links"] = [
        {
            "url": url,
            "anchor": next(
                (anchor for anchor, link in markdown_links if link.rstrip(".,;") == url),
                str(declared_internal.get(url, {}).get("anchor") or ""),
            ),
            "placement": str(declared_internal.get(url, {}).get("placement") or "正文中"),
        }
        for url in dict.fromkeys(used_internal)
    ]
    declared_external = {
        str(item.get("url") or ""): item for item in _dict_list(result.get("external_sources"))
    }
    external_records: list[dict[str, Any]] = []
    for url in dict.fromkeys(used_external):
        supports = str(declared_external.get(url, {}).get("supports") or "").strip()
        if not supports:
            issues.append(f"外部来源缺少具体声明映射：{url}")
        external_records.append(
            {
                "url": url,
                "supports": supports,
                "source_role": str(source_inventory[url]["source_role"]),
                "evidence_ids": list(source_inventory[url].get("evidence_ids") or []),
            }
        )
    result["external_sources"] = external_records
    result["reason"] = str(result.get("reason") or "依据专项素材包和文章大纲制作。")
    result["operator_note"] = str(
        result.get("operator_note") or "核对后复制到 CMS；系统不会自动发布。"
    )
    result["self_review"] = list(
        dict.fromkeys(
            [
                *_text_list(result.get("self_review")),
                "已按主要用户任务检查与现有文章的边界",
                "已核对素材来源角色与正文链接白名单",
                "已检查标题层级、重复链接、中文夹杂和通用 AI 套话",
            ]
        )
    )
    quality_gate = (
        result.get("quality_gate") if isinstance(result.get("quality_gate"), dict) else {}
    )
    if str(quality_gate.get("status") or "").casefold() == "fail":
        issues.extend(_text_list(quality_gate.get("issues")) or ["AI 编辑自审未通过"])
    result["quality_gate"] = {
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }
    return result, list(dict.fromkeys(issues))


def _normalize_existing_deliverable(
    content: dict[str, Any],
    *,
    existing: dict[str, Any],
    allowed_urls: set[str],
) -> tuple[dict[str, Any], list[str]]:
    result = dict(content) if isinstance(content, dict) else {}
    issues: list[str] = []
    required = (
        "title",
        "slug",
        "summary",
        "content",
        "tags",
        "seo_title",
        "seo_description",
        "seo_keywords",
    )
    for key in required:
        if not isinstance(result.get(key), str):
            issues.append(f"CMS 字段 {key} 缺失")
            result[key] = str(result.get(key) or "")

    change_type = str(result.get("change_type") or "")
    if change_type not in {"metadata_only", "partial_update", "same_topic_rewrite"}:
        issues.append("旧文章改动类型无效")
        change_type = "partial_update"
    result["change_type"] = change_type
    result["slug"] = str(existing.get("slug") or "")

    reader_fields = " ".join(
        str(result.get(key) or "")
        for key in (
            "title",
            "summary",
            "content",
            "tags",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
    )
    if CHINESE_RE.search(reader_fields):
        issues.append("旧文章交付包的读者可见字段必须全部为英语")

    old_title_tokens = _tokens(existing.get("title"))
    new_title_tokens = _tokens(result.get("title"))
    if old_title_tokens and (
        len(old_title_tokens & new_title_tokens) / len(old_title_tokens) < 0.5
    ):
        issues.append("新标题偏离了原文章主题")

    body = str(result.get("content") or "")
    if change_type == "metadata_only":
        result["content"] = str(existing.get("body") or "")
        body = result["content"]
    elif change_type == "partial_update":
        if _word_count(body) < 40:
            issues.append("局部修改内容过短，至少需要约 40 个英文词或改为元数据修改")
        if not str(result.get("operator_note") or "").strip():
            issues.append("局部修改缺少明确的 CMS 替换说明")
    else:
        existing_prose = re.sub(r"<[^>]+>", " ", str(existing.get("body") or ""))
        minimum_words = max(700, int(_word_count(existing_prose) * 0.7))
        if _word_count(body) < minimum_words:
            issues.append(f"同主题重写过短，至少需要约 {minimum_words} 个英文词")
        if len(re.findall(r"^##\s+\S", body, flags=re.MULTILINE)) < 3:
            issues.append("同主题重写至少需要 3 个有实际作用的 H2")
        if len(re.findall(r"^#\s+\S", body, flags=re.MULTILINE)) > 1:
            issues.append("同主题重写不能包含多个 H1")

    if change_type != "metadata_only":
        raw_urls = {url.rstrip(".,;") for url in PUBLIC_URL_RE.findall(body)}
        if raw_urls - allowed_urls:
            issues.append("旧文章正文包含未由本任务批准的 URL")
        markdown_urls = [url.rstrip(".,;") for _, url in MARKDOWN_LINK_RE.findall(body)]
        if any(count > 1 for count in Counter(markdown_urls).values()):
            issues.append("旧文章正文重复使用了同一个链接")
        if any(
            " ".join(anchor.casefold().split()) in GENERIC_LINK_ANCHORS
            for anchor, _ in MARKDOWN_LINK_RE.findall(body)
        ):
            issues.append("旧文章正文仍有泛化链接锚文本")
        if re.search(r"^#{1,2}\s+[^\n]+\n+####\s+", body, flags=re.MULTILINE):
            issues.append("旧文章正文标题层级跳级")
        for paragraph in re.split(r"\n\s*\n", body):
            compact = paragraph.strip()
            if compact and not compact.startswith(("#", "-", "*", "|", ">")):
                if len(re.findall(r"[.!?](?:[\"')\]]?)(?:\s|$)", compact)) > 4:
                    issues.append("旧文章正文存在超过 4 句的长段落")
                    break

    banned = (
        "it is important to note that",
        "in conclusion, it can be said that",
        "as experts, we believe",
        "in the ever-evolving world of",
        "it goes without saying that",
        "it is worth mentioning that",
    )
    if change_type != "metadata_only" and any(phrase in body.casefold() for phrase in banned):
        issues.append("旧文章正文仍包含通用 AI 套话")
    if change_type != "metadata_only" and re.search(
        r"\b(?:we|i|our team)\s+(?:tested|measured|used|found|observed)\b",
        body,
        flags=re.IGNORECASE,
    ):
        issues.append("旧文章正文声称了未核实的第一手经验")
    if change_type != "metadata_only" and re.search(
        r"\b(?:market size|price range|discount|retailer|cheapest|best price)\b",
        body,
        flags=re.IGNORECASE,
    ):
        issues.append("旧文章修改稿不应加入市场或价格内容")
    if not str(result.get("seo_title") or "").strip():
        issues.append("SEO Title 不能为空")
    if not str(result.get("seo_description") or "").strip():
        issues.append("SEO Description 不能为空")

    ai_gate = result.get("quality_gate")
    if isinstance(ai_gate, dict) and str(ai_gate.get("status") or "").casefold() == "fail":
        issues.extend(_text_list(ai_gate.get("issues")) or ["AI 编辑自审未通过"])
    issues = list(dict.fromkeys(issues))
    result["quality_gate"] = {"status": "pass" if not issues else "fail", "issues": issues}
    return result, issues


def _store_deliverable(
    settings: Settings,
    action_id: int,
    result: dict[str, Any],
) -> None:
    now = utc_now()
    with connection(settings) as conn:
        conn.execute(
            """
            UPDATE actions SET actual_change = ?, workflow_status = 'in_progress',
                updated_at = ? WHERE id = ?
            """,
            (json_dumps(result), now, action_id),
        )
        conn.execute(
            """
            UPDATE action_steps
            SET status = 'done', completed_at = COALESCE(completed_at, ?), updated_at = ?
            WHERE action_id = ? AND step_key IN (
                'build_research_brief','verify_primary_sources','draft_content',
                'content_review','confirm_scope','draft_change','verify_facts'
            )
            """,
            (now, now, action_id),
        )


async def _generate_legacy_content_deliverable(
    action_id: int,
    settings: Settings | None = None,
    *,
    provider: AIProvider | None = None,
    material_preview: dict[str, Any],
) -> dict[str, Any]:
    active = settings or get_settings()
    with connection(active) as conn:
        row = conn.execute(
            """
            SELECT a.*, o.rule_key, o.gate_status, o.title opportunity_title, o.recommended_action,
                   o.evidence_json, o.gate_reasons_json
            FROM actions a JOIN opportunities o ON o.id = a.opportunity_id
            WHERE a.id = ? AND a.decision = 'accepted'
            """,
            (action_id,),
        ).fetchone()
        if not row:
            raise ContentProductionError("执行任务不存在")
        page = conn.execute(
            """
            SELECT ci.*, cs.summary, cs.body, cs.seo_title, cs.seo_description,
                   cs.metadata_json
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT id FROM content_snapshots
                WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.canonical_url = ?
            """,
            (row["site_id"], row["target_ref"]),
        ).fetchone()
        links = conn.execute(
            """
            SELECT ci.title, ci.slug, ci.canonical_url, ci.content_type, cs.summary
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT id FROM content_snapshots
                WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.status = 'active'
            ORDER BY ci.content_type, ci.id
            """,
            (row["site_id"],),
        ).fetchall()
        evidence = json_loads(row["evidence_json"], {})
        evidence_refs = list(material_preview["evidence_ids"])
        external_material: list[dict[str, Any]] = []
        if evidence_refs:
            placeholders = ",".join("?" for _ in evidence_refs)
            material_rows = conn.execute(
                f"""
                SELECT evidence_id, evidence_type, source_ref, payload_json,
                       limitations_json, captured_at
                FROM evidence_items
                WHERE site_id = ? AND evidence_id IN ({placeholders})
                ORDER BY captured_at, evidence_id
                """,
                (row["site_id"], *evidence_refs),
            ).fetchall()
            external_material = [_external_material_record(item) for item in material_rows]
    existing = dict(page) if page else None
    if not existing:
        raise ContentProductionError("旧文章任务找不到当前 CMS 内容；请重新导入文章 JSON")
    if str(row["gate_status"]) != "passed":
        raise ContentProductionError("该页面目前只有诊断信号，尚未达到文章制作门槛")
    query_page = evidence.get("query_page") if isinstance(evidence.get("query_page"), dict) else {}
    if not query_page.get("current"):
        raise ContentProductionError("缺少查询—页面联合证据，不得生成旧文章修改稿")
    if row["rule_key"] == "protect_click_loss" and not query_page.get("previous"):
        raise ContentProductionError("点击损失任务缺少上一窗口的查询—页面联合证据")
    link_records = [dict(item) for item in links]
    relevant_links = _select_internal_candidates(
        link_records,
        " ".join(
            (
                str(row["opportunity_title"] or ""),
                str(row["recommended_action"] or ""),
                str(row["target_ref"] or ""),
            )
        ),
        target_url=str(row["target_ref"] or ""),
    )
    internal_inventory = {
        str(item["canonical_url"]): item for item in relevant_links if item["canonical_url"]
    }
    allowed_external_urls = _extract_public_urls(evidence)
    allowed_external_urls.update(_extract_public_urls(external_material))
    allowed_urls = set(internal_inventory) | allowed_external_urls
    payload = {
        "task": {
            "type": "update_existing" if existing else "new_article",
            "opportunity": row["opportunity_title"],
            "recommended_action": row["recommended_action"],
            "target": row["target_ref"],
        },
        "existing_page": existing,
        "evidence": evidence,
        "confirmed_materials": {
            "categories": material_preview["categories"],
            "category_values": material_preview["category_values"],
            "source_inventory": material_preview["sources"],
            "limitations": [item.get("limitations", []) for item in material_preview["materials"]],
        },
        "gate_reasons": json_loads(row["gate_reasons_json"], []),
        "available_internal_links": relevant_links,
        "external_material": external_material,
        "required_cms_fields": [
            "title",
            "slug",
            "summary",
            "content",
            "tags",
            "seo_title",
            "seo_description",
            "seo_keywords",
        ],
    }
    active_provider = provider or build_ai_provider(active)

    def validate_legacy(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContentProductionError("AI 没有返回结构化文章交付包")
        required = payload["required_cms_fields"]
        if any(not isinstance(value.get(key), str) for key in required):
            raise ContentProductionError("AI 返回的 CMS 字段不完整")
        change = str(value.get("change_type") or "")
        if existing and change not in {
            "metadata_only",
            "partial_update",
            "same_topic_rewrite",
        }:
            raise ContentProductionError("旧文章只能做元数据、小范围修改或同主题重写")
        if not existing and change != "new_article":
            raise ContentProductionError("新文章任务必须返回完整的新文章内容包")
        return dict(value)

    legacy_task = {
        "site_id": row["site_id"],
        "opportunity_id": row["opportunity_id"],
        "action_id": action_id,
    }
    result = await _complete_stage(
        active,
        legacy_task,
        active_provider,
        purpose="content_existing_update",
        prompt=SYSTEM_PROMPT,
        payload=payload,
        input_refs=evidence_refs,
        validator=validate_legacy,
    )
    result = await _complete_stage(
        active,
        legacy_task,
        active_provider,
        purpose="content_existing_review",
        prompt=EXISTING_REVIEW_PROMPT,
        payload={
            **payload,
            "stage": "existing_review",
            "proposed_deliverable": result,
            "allowed_urls": sorted(allowed_urls),
        },
        input_refs=evidence_refs,
        validator=validate_legacy,
    )
    result, issues = _normalize_existing_deliverable(
        result,
        existing=existing,
        allowed_urls=allowed_urls,
    )
    ai_call_limit = max(2, min(ARTICLE_AI_HARD_CAP, int(active.content_ai_call_limit)))
    ai_call_count = 2
    revision_count = 0
    while issues and ai_call_count < ai_call_limit:
        revision_count += 1
        ai_call_count += 1
        result = await _complete_stage(
            active,
            legacy_task,
            active_provider,
            purpose="content_existing_revision",
            prompt=EXISTING_REVISION_PROMPT,
            payload={
                **payload,
                "stage": "existing_revision",
                "revision_attempt": revision_count,
                "blocking_issues": issues,
                "previous_deliverable": result,
                "allowed_urls": sorted(allowed_urls),
            },
            input_refs=evidence_refs,
            validator=validate_legacy,
        )
        result, issues = _normalize_existing_deliverable(
            result,
            existing=existing,
            allowed_urls=allowed_urls,
        )
    if issues:
        _record_ai_stage(
            active,
            legacy_task,
            purpose="content_existing_validation",
            provider_name="deterministic",
            model=CONTENT_WORKFLOW_VERSION,
            prompt_sha256=hashlib.sha256(json_dumps(result).encode("utf-8")).hexdigest(),
            input_refs=evidence_refs,
            status="failed",
            output=result,
            error_message="；".join(issues[:4])[:240],
        )
        raise ContentProductionError(
            f"旧文章达到本篇 AI 调用上限 {ai_call_limit} 次，仍有阻塞问题：" + "；".join(issues[:3])
        )
    result["quality_report"] = {
        "status": "pass",
        "deterministic_checks": [
            "存在当前查询—页面联合证据",
            "原主题和 Slug 保持不变",
            "读者可见字段为英语",
            "篇幅、CMS 字段、链接白名单和虚构声明已检查",
        ],
        "ai_call_count": ai_call_count,
        "ai_call_limit": ai_call_limit,
        "revision_count": revision_count,
    }
    change_type = str(result.get("change_type") or "")
    if existing:
        if change_type not in {"metadata_only", "partial_update", "same_topic_rewrite"}:
            raise ContentProductionError("旧文章只能做元数据、小范围修改或同主题重写")
        if result["slug"] != existing["slug"]:
            result["slug"] = existing["slug"]
            result["operator_note"] = (
                str(result.get("operator_note") or "") + " Slug 已由系统锁定为原地址。"
            ).strip()
        if change_type == "metadata_only" and not result["content"].strip():
            result["content"] = str(existing.get("body") or "")
    else:
        if change_type != "new_article":
            raise ContentProductionError("新文章任务必须返回完整的新文章内容包")
        result["slug"] = _clean_slug(result["slug"])
        proposed_title = _normalized_title(result["title"])
        existing_titles = {_normalized_title(str(item["title"] or "")) for item in link_records}
        existing_slugs = {str(item["slug"] or "").casefold() for item in link_records}
        if proposed_title and proposed_title in existing_titles:
            raise ContentProductionError("新文章标题与现有内容重复，请回到主题调研换一个主意图")
        if result["slug"].casefold() in existing_slugs:
            raise ContentProductionError("新文章 Slug 与现有内容重复，请重新生成")

    internal_inventory = {
        str(item["canonical_url"]): item for item in relevant_links if item["canonical_url"]
    }
    internal_links: list[dict[str, str]] = []
    for item in result.get("internal_links") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url not in internal_inventory:
            continue
        internal_links.append(
            {
                "url": url,
                "anchor": str(item.get("anchor") or "").strip(),
                "placement": str(item.get("placement") or "").strip(),
            }
        )
    result["internal_links"] = internal_links

    allowed_external_urls = _extract_public_urls(evidence)
    allowed_external_urls.update(_extract_public_urls(external_material))
    allowed_external_urls.difference_update(internal_inventory)
    external_source_roles = {
        str(url): str(role)
        for material in external_material
        for url, role in (material.get("source_roles") or {}).items()
    }
    external_sources: list[dict[str, str]] = []
    for item in result.get("external_sources") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url not in allowed_external_urls:
            continue
        external_sources.append(
            {
                "url": url,
                "supports": str(item.get("supports") or "").strip(),
                "source_role": external_source_roles.get(url, _source_role(url)),
            }
        )
    result["external_sources"] = external_sources
    result["reason"] = str(result.get("reason") or "依据当前机会生成最小充分改动。")
    result["operator_note"] = str(
        result.get("operator_note") or "核对后复制到 CMS；系统不会自动发布。"
    )
    raw_self_review = result.get("self_review")
    self_review = (
        [str(value) for value in raw_self_review if str(value).strip()]
        if isinstance(raw_self_review, list)
        else []
    )
    self_review.extend(
        [
            "Slug 已按任务类型校验",
            "内链只保留当前 CMS 中存在的页面",
            "外链只保留本次已存证来源",
            "社区来源只作问题线索，不得单独支撑安全、法规或规格事实",
            "新文章标题和 Slug 已做精确重复拦截",
        ]
    )
    result["self_review"] = list(dict.fromkeys(self_review))
    _store_deliverable(active, action_id, result)
    return result


async def _generate_staged_content_deliverable(
    action_id: int,
    settings: Settings | None = None,
    *,
    provider: AIProvider | None = None,
) -> dict[str, Any]:
    active = settings or get_settings()
    with connection(active) as conn:
        task_row = conn.execute(
            """
            SELECT a.*, o.title opportunity_title, o.recommended_action,
                   o.evidence_json, o.gate_reasons_json
            FROM actions a JOIN opportunities o ON o.id = a.opportunity_id
            WHERE a.id = ? AND a.decision = 'accepted'
            """,
            (action_id,),
        ).fetchone()
        if not task_row:
            raise ContentProductionError("文章任务不存在")
        existing_page = conn.execute(
            "SELECT id FROM content_items WHERE site_id = ? AND canonical_url = ?",
            (task_row["site_id"], task_row["target_ref"]),
        ).fetchone()
    if existing_page:
        preview = build_material_preview(action_id, active)
        if not preview["ready"] or not preview["confirmed"]:
            raise ContentProductionError("旧文章也必须先确认可追溯写作素材")
        return await _generate_legacy_content_deliverable(
            action_id,
            active,
            provider=provider,
            material_preview=preview,
        )

    row = dict(task_row)
    if row["workflow_status"] == "cancelled":
        raise ContentProductionError("文章任务已取消；请先恢复任务")
    with connection(active) as conn:
        link_rows = conn.execute(
            """
            SELECT ci.title, ci.slug, ci.canonical_url, ci.content_type, cs.summary
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT id FROM content_snapshots
                WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.status = 'active'
            ORDER BY ci.content_type, ci.id
            """,
            (row["site_id"],),
        ).fetchall()
        evidence = json_loads(row["evidence_json"], {})
        evidence_refs = [str(value) for value in evidence.get("evidence_ids", []) if value]
        external_material: list[dict[str, Any]] = []
        if evidence_refs:
            placeholders = ",".join("?" for _ in evidence_refs)
            material_rows = conn.execute(
                f"""
                SELECT evidence_id, evidence_type, source_ref, payload_json,
                       limitations_json, captured_at
                FROM evidence_items
                WHERE site_id = ? AND evidence_id IN ({placeholders})
                ORDER BY captured_at, evidence_id
                """,
                (row["site_id"], *evidence_refs),
            ).fetchall()
            external_material = [_external_material_record(item) for item in material_rows]

    all_links = [dict(item) for item in link_rows]
    topic_text = " ".join(
        (
            str(row["opportunity_title"] or ""),
            str(row["recommended_action"] or ""),
            str(row["target_ref"] or ""),
        )
    )
    internal_candidates = _select_internal_candidates(all_links, topic_text)
    internal_inventory = {str(item["canonical_url"]): item for item in internal_candidates}
    all_internal_urls = {
        str(item["canonical_url"]) for item in all_links if item.get("canonical_url")
    }
    source_items = _external_source_inventory(
        evidence,
        external_material,
        all_internal_urls,
    )
    source_inventory = {str(item["url"]): item for item in source_items}
    compact_material = [
        {
            "evidence_id": item["evidence_id"],
            "evidence_type": item["evidence_type"],
            "source_ref": item["source_ref"],
            "captured_at": item["captured_at"],
            "payload": _clip_value(item["payload"]),
            "limitations": item["limitations"],
            "source_urls": item["source_urls"],
            "source_roles": item["source_roles"],
        }
        for item in external_material[:12]
    ]
    task = {
        "site_id": row["site_id"],
        "opportunity_id": row["opportunity_id"],
        "action_id": action_id,
        "type": "new_article",
        "opportunity": row["opportunity_title"],
        "recommended_action": row["recommended_action"],
        "target": row["target_ref"],
    }
    sensitive = _is_sensitive_topic(topic_text)
    if len(source_items) < 2:
        raise ContentProductionError(
            "当前新主题还没有至少两个可核实来源；请先在外部调研中补足该主题素材"
        )
    if sensitive and not any(
        item["source_role"] == "official_or_research" for item in source_items
    ):
        raise ContentProductionError(
            "该新主题涉及安全、法规或技术事实，但当前调研没有官方/研究来源"
        )
    active_provider = provider or build_ai_provider(active)

    material_pack = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_material_pack",
        prompt=MATERIAL_PROMPT,
        payload={
            "stage": "material_pack",
            "workflow_version": CONTENT_WORKFLOW_VERSION,
            "task": task,
            "first_stage_evidence": _clip_value(evidence),
            "evidence_material": compact_material,
            "available_external_sources": source_items,
            "related_existing_pages": internal_candidates,
            "product_records": [
                item for item in internal_candidates if item.get("content_type") == "product"
            ],
        },
        input_refs=evidence_refs,
        validator=lambda value: _validate_material_pack(
            value,
            source_inventory=source_items,
            sensitive=sensitive,
        ),
    )
    approved_external = {str(item["url"]) for item in material_pack["source_plan"]}
    outline = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_outline",
        prompt=OUTLINE_PROMPT,
        payload={
            "stage": "outline",
            "workflow_version": CONTENT_WORKFLOW_VERSION,
            "task": task,
            "material_pack": material_pack,
            "available_internal_links": internal_candidates,
            "allowed_external_sources": [
                item for item in source_items if item["url"] in approved_external
            ],
            "limits": {
                "internal_links": MAX_FINAL_INTERNAL_LINKS,
                "external_links": MAX_FINAL_EXTERNAL_LINKS,
                "h2_sections": "4-8",
            },
        },
        input_refs=evidence_refs,
        validator=lambda value: _validate_outline(
            value,
            internal_urls=set(internal_inventory),
            external_urls=approved_external,
        ),
    )

    split_at = max(2, (len(outline["sections"]) + 1) // 2)
    first_half = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_draft_first",
        prompt=DRAFT_FIRST_PROMPT,
        payload={
            "stage": "draft_first",
            "workflow_version": CONTENT_WORKFLOW_VERSION,
            "task": task,
            "material_pack": material_pack,
            "outline": {**outline, "sections": outline["sections"][:split_at]},
            "planned_internal_urls": outline["planned_internal_urls"],
            "planned_external_urls": outline["planned_external_urls"],
        },
        input_refs=evidence_refs,
        validator=lambda value: _validate_draft_part(value, "文章前半部分"),
    )
    second_half = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_draft_second",
        prompt=DRAFT_SECOND_PROMPT,
        payload={
            "stage": "draft_second",
            "workflow_version": CONTENT_WORKFLOW_VERSION,
            "task": task,
            "material_pack": material_pack,
            "remaining_outline": outline["sections"][split_at:],
            "conclusion_job": outline["conclusion_job"],
            "first_half": first_half,
            "planned_internal_urls": outline["planned_internal_urls"],
            "planned_external_urls": outline["planned_external_urls"],
            "faq_questions": outline["faq_questions"],
        },
        input_refs=evidence_refs,
        validator=lambda value: _validate_draft_part(value, "文章后半部分"),
    )
    complete_draft = f"{first_half['content'].rstrip()}\n\n{second_half['content'].lstrip()}"

    def validate_review(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContentProductionError("AI 没有返回完整 CMS 交付包")
        return dict(value)

    required_fields = [
        "title",
        "slug",
        "summary",
        "content",
        "tags",
        "seo_title",
        "seo_description",
        "seo_keywords",
    ]
    reviewed = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_self_review",
        prompt=REVIEW_PROMPT,
        payload={
            "stage": "self_review",
            "workflow_version": CONTENT_WORKFLOW_VERSION,
            "task": task,
            "material_pack": material_pack,
            "outline": outline,
            "complete_draft": complete_draft,
            "allowed_internal_links": [
                internal_inventory[url] for url in outline["planned_internal_urls"]
            ],
            "allowed_external_sources": [
                source_inventory[url] for url in outline["planned_external_urls"]
            ],
            "required_cms_fields": required_fields,
        },
        input_refs=evidence_refs,
        validator=validate_review,
    )
    existing_titles = {_normalized_title(str(item.get("title") or "")) for item in all_links}
    existing_slugs = {str(item.get("slug") or "").casefold() for item in all_links}
    result, issues = _normalize_new_deliverable(
        reviewed,
        outline=outline,
        internal_inventory=internal_inventory,
        source_inventory=source_inventory,
        existing_titles=existing_titles,
        existing_slugs=existing_slugs,
        sensitive=sensitive,
    )
    revision_used = bool(issues)
    if issues:
        revised = await _complete_stage(
            active,
            task,
            active_provider,
            purpose="content_revision",
            prompt=REVISION_PROMPT,
            payload={
                "stage": "revision",
                "workflow_version": CONTENT_WORKFLOW_VERSION,
                "task": task,
                "blocking_issues": issues,
                "material_pack": material_pack,
                "outline": outline,
                "previous_deliverable": result,
                "allowed_internal_urls": outline["planned_internal_urls"],
                "allowed_external_urls": outline["planned_external_urls"],
            },
            input_refs=evidence_refs,
            validator=validate_review,
        )
        result, issues = _normalize_new_deliverable(
            revised,
            outline=outline,
            internal_inventory=internal_inventory,
            source_inventory=source_inventory,
            existing_titles=existing_titles,
            existing_slugs=existing_slugs,
            sensitive=sensitive,
        )
    if issues:
        _record_ai_stage(
            active,
            task,
            purpose="content_validation",
            provider_name="deterministic",
            model=CONTENT_WORKFLOW_VERSION,
            prompt_sha256=hashlib.sha256(json_dumps(result).encode("utf-8")).hexdigest(),
            input_refs=evidence_refs,
            status="failed",
            output=result,
            error_message="；".join(issues[:4])[:240],
        )
        raise ContentProductionError("自动自审修订后仍有阻塞问题：" + "；".join(issues[:3]))

    result["workflow_version"] = CONTENT_WORKFLOW_VERSION
    result["production_stages"] = [
        "专项素材包",
        "文章专属大纲与链接计划",
        "前半篇写作",
        "上下文缝合与后半篇",
        "自动自审与修订",
    ]
    result["material_pack"] = material_pack
    result["outline"] = outline
    result["quality_report"] = {
        "status": "pass",
        "deterministic_checks": [
            "CMS 八字段齐全",
            "单一 H1 与大纲覆盖",
            "正文无未知或重复 URL",
            "站内链接来自当前 CMS",
            "事实来源来自本任务存证材料",
            "安全/技术主题包含官方或研究来源",
            "无中文夹杂与通用 AI 套话",
            "导语、FAQ 与结论符合原 skill 的稳定结构",
        ],
        "revision_used": revision_used,
    }
    _store_deliverable(active, action_id, result)
    return result


async def _generate_skill_new_content_deliverable(
    action_id: int,
    active: Settings,
    *,
    provider: AIProvider | None = None,
    preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    material_preview = preview or build_material_preview(action_id, active)
    if not material_preview["ready"]:
        raise ContentProductionError(
            "素材还缺少：" + "、".join(material_preview["missing"]) + "；请先在任务卡补充"
        )
    if not material_preview["confirmed"]:
        raise ContentProductionError("请先在任务卡确认是否需要补充手工素材，再开始写作")

    with connection(active) as conn:
        row = conn.execute(
            """
            SELECT a.*, o.title opportunity_title, o.recommended_action,
                   o.evidence_json, o.gate_reasons_json
            FROM actions a JOIN opportunities o ON o.id = a.opportunity_id
            WHERE a.id = ? AND a.decision = 'accepted'
            """,
            (action_id,),
        ).fetchone()
        if not row:
            raise ContentProductionError("文章任务不存在")
        content_rows = conn.execute(
            """
            SELECT ci.title, ci.slug, ci.canonical_url, ci.content_type, cs.summary
            FROM content_items ci
            LEFT JOIN content_snapshots cs ON cs.id = (
                SELECT id FROM content_snapshots
                WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
            )
            WHERE ci.site_id = ? AND ci.status = 'active'
            ORDER BY ci.content_type, ci.id
            """,
            (row["site_id"],),
        ).fetchall()

    task = {
        "site_id": int(row["site_id"]),
        "opportunity_id": int(row["opportunity_id"]),
        "action_id": action_id,
        "type": "new_article",
        "opportunity": row["opportunity_title"],
        "recommended_action": row["recommended_action"],
        "target": row["target_ref"],
    }
    all_links = [dict(item) for item in content_rows]
    internal_candidates = list(material_preview["internal_candidates"])
    internal_inventory = {
        str(item["canonical_url"]): item
        for item in internal_candidates
        if item.get("canonical_url")
    }
    source_items = list(material_preview["sources"])
    source_inventory = {str(item["url"]): item for item in source_items}
    evidence_refs = list(material_preview["evidence_ids"])
    compact_material = [
        {
            "evidence_id": item["evidence_id"],
            "evidence_type": item["evidence_type"],
            "source_ref": item["source_ref"],
            "captured_at": item["captured_at"],
            "payload": _clip_value(item["payload"]),
            "limitations": item["limitations"],
            "source_urls": item["source_urls"],
        }
        for item in material_preview["materials"][:16]
    ]
    material_pack = {
        "primary_task": material_preview["topic"],
        "core_answer": material_preview["recommended_action"],
        "differentiation": (
            material_preview["category_values"]["D"][0]
            if material_preview["category_values"]["D"]
            else "Solve the accepted topic as one distinct user task without duplicating existing pages."
        ),
        "article_tier": material_preview["tier"],
        "word_range": [material_preview["word_min"], material_preview["word_max"]],
        "categories": {
            item["key"]: {
                "label": item["label"],
                "items": item["items"],
                "required": item["required"],
            }
            for item in material_preview["categories"]
        },
        "sources": source_items,
        "limitations": [
            "Only stored evidence and operator-confirmed material may support factual claims.",
            "No fixed author persona or unverified first-hand experience is permitted.",
        ],
        "material_gate": {"status": "pass", "confirmed_by_operator": True},
    }
    required_fields = [
        "title",
        "slug",
        "summary",
        "content",
        "tags",
        "seo_title",
        "seo_description",
        "seo_keywords",
    ]
    ai_call_limit = max(
        ARTICLE_AI_NORMAL_CALLS,
        min(ARTICLE_AI_HARD_CAP, int(active.content_ai_call_limit)),
    )
    article_rules = {
        "tier": material_preview["tier"],
        "word_range": [material_preview["word_min"], material_preview["word_max"]],
        "introduction_words": list(INTRO_WORD_RANGE),
        "conclusion_words": list(CONCLUSION_WORD_RANGE),
        "faq_questions": [FAQ_MIN, FAQ_MAX],
        "main_h2_sections": [4, 7],
        "key_takeaways": [3, 5],
        "normal_ai_calls": ARTICLE_AI_NORMAL_CALLS,
        "maximum_ai_calls": ai_call_limit,
        "system_hard_cap": ARTICLE_AI_HARD_CAP,
    }
    base_payload = {
        "workflow_version": CONTENT_WORKFLOW_VERSION,
        "task": task,
        "confirmed_material_pack": material_pack,
        "stored_evidence_material": compact_material,
        "allowed_internal_links": internal_candidates,
        "allowed_external_sources": source_items,
        "related_existing_pages": internal_candidates,
        "product_records": [
            item for item in internal_candidates if item.get("content_type") == "product"
        ],
        "required_cms_fields": required_fields,
        "article_rules": article_rules,
    }
    active_provider = provider or build_ai_provider(active)

    def validate_payload(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContentProductionError("AI 没有返回完整 CMS 内容包")
        return dict(value)

    plan = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_skill_plan",
        prompt=SKILL_PLAN_PROMPT,
        payload={**base_payload, "stage": "skill_plan"},
        input_refs=evidence_refs,
        validator=lambda value: _validate_skill_plan(
            value,
            internal_inventory=internal_inventory,
            source_inventory=source_inventory,
            article_tier=str(material_preview["tier"]),
            word_min=int(material_preview["word_min"]),
        ),
    )
    outline_policy = plan
    planned_internal = [
        internal_inventory[url]
        for url in plan["planned_internal_urls"]
        if url in internal_inventory
    ]
    planned_sources = [
        source_inventory[url] for url in plan["planned_external_urls"] if url in source_inventory
    ]
    draft = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_skill_draft",
        prompt=SKILL_WRITE_PROMPT,
        payload={
            **base_payload,
            "stage": "skill_draft",
            "approved_article_plan": plan,
            "allowed_internal_links": planned_internal,
            "allowed_external_sources": planned_sources,
        },
        input_refs=evidence_refs,
        validator=validate_payload,
    )
    reviewed = await _complete_stage(
        active,
        task,
        active_provider,
        purpose="content_skill_edit",
        prompt=SKILL_EDIT_PROMPT,
        payload={
            **base_payload,
            "stage": "skill_edit",
            "approved_article_plan": plan,
            "complete_draft": draft,
            "allowed_internal_links": planned_internal,
            "allowed_external_sources": planned_sources,
        },
        input_refs=evidence_refs,
        validator=validate_payload,
    )
    existing_titles = {_normalized_title(str(item.get("title") or "")) for item in all_links}
    existing_slugs = {str(item.get("slug") or "").casefold() for item in all_links}
    result, issues = _normalize_new_deliverable(
        reviewed,
        outline=outline_policy,
        internal_inventory=internal_inventory,
        source_inventory=source_inventory,
        existing_titles=existing_titles,
        existing_slugs=existing_slugs,
        sensitive=bool(material_preview["sensitive"]),
        article_tier=str(material_preview["tier"]),
    )
    revision_count = 0
    while issues and ARTICLE_AI_NORMAL_CALLS + revision_count < ai_call_limit:
        revision_count += 1
        revised = await _complete_stage(
            active,
            task,
            active_provider,
            purpose="content_skill_revision",
            prompt=SKILL_REVISION_PROMPT,
            payload={
                "stage": "skill_revision",
                "revision_attempt": revision_count,
                "ai_call_limit": ai_call_limit,
                "workflow_version": CONTENT_WORKFLOW_VERSION,
                "task": task,
                "blocking_issues": issues,
                "confirmed_material_pack": material_pack,
                "approved_article_plan": plan,
                "previous_deliverable": result,
                "allowed_internal_links": planned_internal,
                "allowed_external_sources": planned_sources,
                "article_rules": article_rules,
            },
            input_refs=evidence_refs,
            validator=validate_payload,
        )
        result, issues = _normalize_new_deliverable(
            revised,
            outline=outline_policy,
            internal_inventory=internal_inventory,
            source_inventory=source_inventory,
            existing_titles=existing_titles,
            existing_slugs=existing_slugs,
            sensitive=bool(material_preview["sensitive"]),
            article_tier=str(material_preview["tier"]),
        )
    revision_used = revision_count > 0
    if issues:
        _record_ai_stage(
            active,
            task,
            purpose="content_validation",
            provider_name="deterministic",
            model=CONTENT_WORKFLOW_VERSION,
            prompt_sha256=hashlib.sha256(json_dumps(result).encode("utf-8")).hexdigest(),
            input_refs=evidence_refs,
            status="failed",
            output=result,
            error_message="；".join(issues[:4])[:240],
        )
        raise ContentProductionError(
            f"已达到本篇 AI 调用上限 {ai_call_limit} 次，仍有阻塞问题：" + "；".join(issues[:3])
        )

    result["workflow_version"] = CONTENT_WORKFLOW_VERSION
    result["production_stages"] = [
        "系统整理已存写作素材（0 次 API、0 次 AI）",
        "运营者确认是否补充手工搜索素材",
        "AI 形成文章专属大纲、事实与内外链计划",
        "AI 按批准计划写完整初稿",
        "AI 编辑自审并形成最终 CMS 稿",
        "程序检查未通过时才继续修订，达到设置上限立即停止",
    ]
    result["material_pack"] = material_pack
    result["outline"] = {
        "sections": plan["sections"],
        "faq_questions": plan["faq_questions"],
        "primary_keyword": plan["primary_keyword"],
        "information_gain": plan["information_gain"],
    }
    result["quality_report"] = {
        "status": "pass",
        "deterministic_checks": [
            "CMS 八字段齐全",
            "文章类型、总字数、导语、Key Takeaways、H2、结论均符合确认规则",
            "3–4 个可见 FAQ 与 FAQPage JSON-LD 一致",
            "SEO Title 与 SEO Description 长度已检查",
            "正文无未知或重复 URL，内外链按篇幅动态检查",
            "事实来源来自本任务存证材料，社区信号未冒充权威来源",
            "无中文夹杂、通用 AI 套话或未经核实的第一手经验",
            "段落句数与标题层级已检查",
        ],
        "ai_call_count": ARTICLE_AI_NORMAL_CALLS + revision_count,
        "ai_call_limit": ai_call_limit,
        "revision_count": revision_count,
        "revision_used": revision_used,
    }
    _store_deliverable(active, action_id, result)
    return result


async def generate_content_deliverable(
    action_id: int,
    settings: Settings | None = None,
    *,
    provider: AIProvider | None = None,
    confirm_materials: bool = False,
) -> dict[str, Any]:
    """Generate an old-page update or a confirmed-material new article."""

    active = settings or get_settings()
    with connection(active) as conn:
        row = conn.execute(
            """
            SELECT a.site_id, a.target_ref, a.workflow_status
            FROM actions a
            WHERE a.id = ? AND a.decision = 'accepted'
            """,
            (action_id,),
        ).fetchone()
        if not row:
            raise ContentProductionError("文章任务不存在")
        existing_page = conn.execute(
            "SELECT id FROM content_items WHERE site_id = ? AND canonical_url = ?",
            (row["site_id"], row["target_ref"]),
        ).fetchone()
    if row["workflow_status"] == "cancelled":
        raise ContentProductionError("文章任务已取消；请先恢复任务")
    try:
        preview = (
            confirm_content_materials(action_id, active)
            if confirm_materials
            else build_material_preview(action_id, active)
        )
    except MaterialWorkflowError as exc:
        raise ContentProductionError(str(exc)) from exc
    if not preview["ready"]:
        raise ContentProductionError(
            "素材还缺少：" + "、".join(preview["missing"]) + "；请先在任务卡补充"
        )
    if not preview["confirmed"]:
        raise ContentProductionError("请先在任务卡确认是否需要补充手工素材，再开始写作")
    if existing_page:
        return await _generate_legacy_content_deliverable(
            action_id,
            active,
            provider=provider,
            material_preview=preview,
        )
    return await _generate_skill_new_content_deliverable(
        action_id,
        active,
        provider=provider,
        preview=preview,
    )
