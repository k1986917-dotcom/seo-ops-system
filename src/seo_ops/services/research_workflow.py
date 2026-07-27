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
    DISPOSITION_UPDATE_EXISTING,
    MULTI_SOURCE_TOPIC_RESEARCH_RULE,
    QUALIFICATION_RULE_VERSION,
    RELATIONSHIP_COVERED_SUBTOPIC,
    RELATIONSHIP_DISTINCT,
    RESEARCH_METHOD_VERSION,
    assess_discovered_topic,
    compute_cms_fingerprint,
    compute_evidence_fingerprint,
    normalize_topic,
    qualify_candidate,
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
	SERP/PAA/Trends 只承担需求信号角色；source_discovery 只承担资料发现角色；
	page_capture 才是页面级写作材料。候选只需引用输入中存在的 evidence ID，并明确把事实与推断分开。缺少页面材料时仍可输出独立的研究方向，但不得把它说成已可直接成稿。existing_content 中的 headings 表示旧文已结构化回答的范围，
	不得把相同主意图或已覆盖小标题换一种说法后再次建议为新文章。evidence 中的
	input_refs 表示来源发现和页面采集来自哪个需求信号；优先组合具备同一 lineage 的证据。
seed_context 是本轮已经选择好的研究前沿，evidence 只能扩展和验证它，不能悄悄换掉它。
seed_context 中可出现 task_card。它是来自旧 Plan 前沿、公开第三方语言或竞品问题的计划假设，
不是需求事实、本站第一方反馈或角色真实使用产品的证明。若你根据 task_card 输出主题，必须忠实保留
其中的角色、单一任务与条件；不能自行加入未被卡片或 evidence 支持的风险、用途、维修、改装、测量、
产品替换或法律主张。证据不能忠实支持某卡时，宁可不输出该卡的主题。
anchor_fit=core 表示激光笔仍是候选用户任务所使用或讨论的核心工具，即使受众、场景或任务已经
跨出原分支词项；不要仅因候选没有复述 selected_branch 或 boundary_dimension 就标成 adjacent。
anchor_fit=adjacent 表示激光笔仍不可缺少、但在更大的相邻工作流中承担辅助作用；anchor_fit=off_anchor
只用于证据已经把激光笔替换成激光水平仪、切割机等其他核心产品，或仅有偶然词项关联的情况。
相邻任务仍可输出并保留，不得因跨分支自行降权。小众市场允许单一但具体的
弱信号形成低置信候选；不得因为缺少搜索量或第二来源而自行丢弃。feedback 只影响包装偏好，
不能覆盖证据、执行硬过滤或把被拒绝词项当成绝对禁区。PAA、Related Searches 和资料标题
可以形成待人工筛选的文章假设，但只能证明“这个问题或说法出现过”，不能证明其中的技术主张、
推荐结论或需求规模。不要照抄生硬查询；应整理成主要意图明确的候选题。只有“最佳/推荐/品牌/
型号/兼容性”等具体决策主张仍需要页面材料支持。
同属一个大方向不是重复：只要主要用户问题不同，细化主题必须分别保留。不要把不同问题合并成
一篇大而全文章，也不要因为它们共享电池、光学、场景、角色等上位词而删除。
expansion_pass 只是本次整理的注意角度，不是允许范围或固定分类；证据中出现的其他独立角度也可
输出。already_proposed_topics 只用于避免主要意图重复，不得阻止同方向的不同细化意图。
只包装有独立信息增益且核心对象仍是手持激光笔的角度，不要为了填满数组输出偏题。
标为 public_third_party_language_probe 的种子只是其他网站、论坛、评论或问答中的公开语言代理，
不得把它描述为本站第一方用户反馈。
除 topics 外，可以返回 source_observations 作为下一轮可复查的种子。每条必须引用一个存在的
evidence_id，并提供 source_quote：它必须是该 evidence 中连续、可逐字核对的公开英语摘录；不能
把你的概括、模型记忆或推断写成 quote。follow_up_query 可整理这段语言，但不得虚构角色、用途、
风险、品牌/型号推荐或技术事实。若没有这样的原文，返回空数组。source_observations 只是下一轮
搜索种子，不是文章建议、本站反馈或需求结论。
若 topic 是“最佳/推荐/品牌/型号/兼容性”之类的具体推荐，只有页面级 evidence 直接支持该主张时
才可把 source_support 标为 page_supported；PAA、相关搜索和结果标题本身只能是 question_only 或
snippet_supported，这类具体决策题应作为原始线索。非推荐类的明确问题可以保留为低置信成型假设。
输出严格 JSON：
{
  "summary": "One-sentence English summary of this evidence set",
  "topics": [
    {
      "topic": "A clear English topic with one primary user task",
      "intent": "English description of the intent to verify",
      "rationale": "English explanation of why this is worth verification",
      "anchor_fit": "core | adjacent | off_anchor",
      "anchor_note": "English explanation of how the angle relates to the seed core object",
      "semantic_cluster": "A short lower_snake_case scheduling label, or emerging",
      "source_support": "page_supported | snippet_supported | question_only",
      "evidence_ids": ["只能使用输入中存在的 evidence ID"],
      "facts": ["English fact line that explicitly includes its evidence ID"],
      "inference": ["Limited English inference kept separate from facts"]
    }
  ],
  "source_observations": [
    {
      "evidence_id": "one input evidence ID",
      "source_quote": "verbatim public English wording from that evidence",
      "role": "optional role explicitly present in the quote, otherwise empty",
      "task": "optional single task explicitly supported by the quote, otherwise empty",
      "condition": "optional condition explicitly supported by the quote, otherwise empty",
      "follow_up_query": "a concise English query to verify next round",
      "provisional_topic": "optional English topic only when the quote supports it",
      "semantic_cluster": "short lower_snake_case label or emerging",
      "anchor_fit": "core | adjacent | off_anchor"
    }
  ]
}
围绕 seed_context 中当前要深入的来源种子，输出所有真正成型、主要意图互不重复的主题；弱但具体的
单一公开信号足以形成低置信主题假设。不要追求固定数量，也不得把不同细化意图过早合并。"""


OPEN_SCHEDULER_FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        "principles",
        "principles, phenomena, terminology, and myths",
        'laser pointer unusual beam dot phenomenon myths "why" forum',
    ),
    (
        "audiences",
        "audiences, roles, and communities",
        "laser pointer specialist professional work real use questions",
    ),
    (
        "tasks",
        "jobs, use cases, and desired outcomes",
        "laser pointer unusual practical task use case site:reddit.com",
    ),
    (
        "contexts",
        "settings, environments, seasons, and operating conditions",
        "laser pointer weather environment surface screen distance problems forum",
    ),
    (
        "lifecycle",
        "selection, setup, use, storage, maintenance, and disposal",
        "laser pointer setup storage maintenance user complaints reviews",
    ),
    (
        "failures",
        "failures, limitations, symptoms, and troubleshooting",
        "laser pointer strange symptom troubleshooting site:laserpointerforums.com",
    ),
    (
        "decisions",
        "comparison, verification, trust, and buying decisions",
        "laser pointer verify seller claims specification buyer complaints reviews",
    ),
    (
        "ecosystem",
        "accessories, compatibility, integration, and alternatives",
        "laser pointer compatibility mount camera telescope accessory problems",
    ),
    (
        "safety_rules",
        "safety, regulation, ethics, and regional differences",
        "laser pointer workplace public safety regulation questions official",
    ),
    (
        "commerce",
        "wholesale, shipping, returns, service, and marketplace rules",
        "laser pointer wholesale shipping return marketplace seller buyer problems",
    ),
    (
        "changes",
        "new technology, product, policy, and market changes",
        '"handheld laser pointer" new diode feature product policy changes 2025 2026 '
        "-cutting -engraving -level",
    ),
    (
        "emerging",
        "source-derived direction that does not fit the current map",
        '"laser pointer" unexpected question real use problem site:youtube.com OR site:reddit.com',
    ),
)


OPEN_SCHEDULER_SEED_PROMPT = """You organize a broad, evidence-bound seed pool for an SEO topic
research system. Use only the supplied public-source observations and legacy Plan task cards. Never
invent a source, quote, user, fact, product capability, search volume, or demand claim.

The family map is an attention checklist, never an eligibility whitelist. Preserve emerging ideas.
Several proposals may belong to the same broad direction when their primary user intent differs.
Do not merge distinct subtopics into an omnibus guide. The core product must remain a handheld laser
pointer; reject laser levels, cutters, engravers, weapon sights, and other object drift.

Public-source observations are market-proxy language, not first-party feedback. A single specific weak
signal may create a low-confidence seed. Legacy Plan cards are planning hypotheses and may create a
search seed even before public proof, but must retain the card's role, task, and condition.

The operator entry context is metadata for the legacy Plan frontier, not a filter for this open source
pool. Do not force every proposal into that entry context. A previous-run cooldown changes later seed
priority once; it never removes a supported seed from this output.

Return strict JSON:
{
  "seed_proposals": [
    {
      "query": "concise English follow-up query",
      "topic": "clear English candidate angle with one primary intent",
      "intent": "the single user intent",
      "family": "one supplied family key or emerging",
      "semantic_cluster": "short lower_snake_case scheduling label",
      "shape": "phenomenon | role_task_condition | failure | decision | lifecycle | compatibility | rules | commerce | change | emerging",
      "anchor_fit": "core | adjacent | off_anchor",
      "evidence_ids": ["exact supplied evidence IDs only"],
      "rationale": "limited English inference explaining the seed"
    }
  ]
}

Return every independent supported seed you can find. There is no target count and no penalty for
multiple different intents inside the same broad direction."""


OPEN_SCHEDULER_INTENT_CLUSTER_PROMPT = """You deduplicate an SEO topic pool by primary user
intent. You do not qualify topics, judge demand, or create ideas. Merge only current candidates that
one focused article could satisfy for the same reader job and outcome. A broad shared direction is
never enough to merge.

Keep candidates separate when they differ in the primary question, task, symptom, decision, audience,
jurisdiction, lifecycle stage, operating condition, or desired result. Fine-grained subtopics remain
separate when their search intent is independently useful. Do not merge merely because nouns overlap.

Prior topics are already visible in the pool. Mark a current candidate as a prior duplicate only when
it has the same primary page responsibility; adjacent detail is not a duplicate. Use exact supplied
IDs. Do not invent or rewrite titles. Return strict JSON:
{
  "same_intent_clusters": [
    {
      "representative_id": "one current candidate ID",
      "member_ids": ["representative and every same-intent current ID"],
      "reason": "short primary-intent reason"
    }
  ],
  "duplicates_of_prior": [
    {
      "candidate_id": "current candidate ID",
      "prior_id": "prior topic ID",
      "reason": "short primary-intent reason"
    }
  ]
}
Omit singleton clusters. It is correct to return empty lists."""

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

# Old Plan question-chain lenses. They create search seeds, not article titles,
# and deliberately preserve the site core object while exploring a new task.
PLAN_QUESTION_CHAIN_LENSES: dict[str, tuple[str, str]] = {
    "audience": ("beginner problems", "professional constraints"),
    "journey": ("before use setup", "after use maintenance"),
    "failure": ("problems symptoms solutions", "mistakes limitations alternatives"),
    "decision": ("comparison tradeoffs", "verification before buying"),
    "ecosystem": ("compatibility problems", "adjacent equipment comparison"),
    "rules": ("safety law regional questions", "compliance risks updates"),
    "adjacent": ("related tasks tools", "alternatives when not to use"),
}

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

# Navigation labels sometimes contain generic words that broaden a SERP. These
# concise overrides are deterministic cluster frontiers, mirroring the old Plan
# scorer's use of a focused cluster/pain seed before any external expansion.
RESEARCH_SEED_FOCUS_OVERRIDES = {
    "use-professional": "construction landscaping",
    "buy-quality": "seller claims verification",
    "ops-accessories": "accessories",
}

# The useful part of the legacy Plan was frontier selection before it asked an
# external search tool anything.  These cards make a frontier concrete as
# ``role × task × condition``.  They are deliberately planning hypotheses,
# never demand evidence, user quotes, or article titles.  The public-language
# and competitor bases below are retained as source labels only; the current
# run still has to collect evidence and the CMS duplicate check still wins.
#
# Cards are intentionally small and source-diverse.  Once a card is used its
# query is remembered, then the next card or the normal question-chain fallback
# takes over.  This avoids repeatedly asking one broad query until it returns
# the same two ideas.
TASK_COMPOSITION_SEED_CARDS: dict[tuple[str, str], tuple[dict[str, str], ...]] = {
    ("use-professional", "audience"): (
        {
            "query": '"laser pointer" arborist indicate pruning locations ground',
            "role": "arborist or tree-care professional",
            "task": "indicate a proposed pruning location to a crew or client",
            "condition": "from ground level when the branch is out of reach",
            "source_type": "old_plan_role_task_frontier",
            "source_basis": "legacy Plan professional-use frontier narrowed to a concrete pointing task",
            "provisional_topic": "Using a Laser Pointer to Mark Pruning Locations From the Ground",
            "intent": "Help an arborist communicate a proposed pruning location from ground level.",
        },
        {
            "query": '"laser pointer" building inspection facade crack indication',
            "role": "building inspector",
            "task": "indicate a visible facade crack or defect to another person",
            "condition": "from ground level when the defect is out of reach",
            "source_type": "old_plan_role_task_frontier",
            "source_basis": "legacy Plan professional-use frontier narrowed to a distant visual-indication task",
            "provisional_topic": "Using a Handheld Laser Pointer to Indicate Facade Cracks During Building Inspection",
            "intent": "Help an inspector point out a distant visible facade defect without claiming measurement or code assessment.",
        },
        {
            "query": '"laser pointer" continuous on duty cycle alignment',
            "role": "alignment technician",
            "task": "assess whether a handheld pointer can satisfy a continuous visual alignment task",
            "condition": "the required session lasts longer than momentary pointing",
            "source_type": "old_plan_public_constraint",
            "source_basis": "legacy Plan public third-party duty-cycle constraint language",
            "provisional_topic": "Assessing Handheld Laser Pointers for Continuous Visual Alignment Tasks Under Duty Cycle Constraints",
            "intent": "Help an alignment technician assess a pointer's stated duty-cycle limits without recommending modification or substitution.",
        },
    ),
    ("operation", "failure"): (
        {
            "query": '"laser pointer" Laser 303 mode hopping dimming',
            "role": "budget laser owner",
            "task": "distinguish mode hopping from a dimming fault",
            "condition": "the beam is initially bright and then changes during use",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party Laser 303 dimming and mode-hopping reports",
            "provisional_topic": "Laser 303 Mode Hopping and Dimming: A Symptom-Based Diagnosis",
            "intent": "Help an owner distinguish observable dimming and mode-hopping symptoms before assuming a repair.",
        },
        {
            "query": '"laser pointer" stopped working after drop diagnosis',
            "role": "laser owner",
            "task": "identify what to check after a pointer stops working following a drop",
            "condition": "the failure follows a single accidental impact",
            "source_type": "old_plan_competitor_problem",
            "source_basis": "legacy Plan competitor-problem frontier narrowed from broad failure coverage",
            "provisional_topic": "Diagnosing a Laser Pointer That Stops Working After a Drop",
            "intent": "Help an owner assess a post-drop failure without promising a safe DIY repair.",
        },
        {
            "query": '"laser pointer" green circle dots dirty lens focus',
            "role": "laser owner",
            "task": "separate a patterned or scattered beam symptom from ordinary focus behavior",
            "condition": "a green beam shows circles or dots instead of one clean spot",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party beam-pattern and lens-condition reports",
            "provisional_topic": "Diagnosing a Green Laser Pointer That Shows a Circle or Dot Pattern",
            "intent": "Help an owner describe a distorted beam symptom before deciding whether it is contamination, focus, or a defect.",
        },
    ),
    ("ops-battery", "decision"): (
        {
            "query": '"laser pointer" protected 18650 button top battery fit',
            "role": "high-power laser owner",
            "task": "check whether a protected button-top 18650 will physically fit a laser host",
            "condition": "the host has tight length or terminal tolerances",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party battery length, protection-board, and terminal-fit reports",
            "provisional_topic": "Checking Protected 18650 Battery Fit in a Tight Laser-Pointer Compartment",
            "intent": "Help an owner compare physical battery fit constraints before inserting a cell.",
        },
    ),
    ("use-astronomy", "journey"): (
        {
            "query": '"laser pointer" telescope mount winter keep warm',
            "role": "astronomy observer",
            "task": "assess a mounted pointer's cold-weather operating limitation",
            "condition": "the pointer remains on a telescope mount during winter observing",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party cold-weather mounted-pointer reports",
            "provisional_topic": "Cold-Weather Limits for a Laser Pointer Left on a Telescope Mount",
            "intent": "Help an observer understand cold-weather operating limits for a mounted pointer without asserting a universal fix.",
        },
    ),
    ("use-outdoor", "journey"): (
        {
            "query": '"laser pointer" fog rain beam visibility emergency signal',
            "role": "outdoor user",
            "task": "assess whether a beam remains visible enough for an emergency signal",
            "condition": "fog, rain, or spray reduces normal visibility",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party outdoor visibility constraint language",
            "provisional_topic": "How Fog and Rain Affect Laser Pointer Beam Visibility for Emergency Signaling",
            "intent": "Help an outdoor user understand environmental visibility limits before relying on a beam as a signal.",
        },
    ),
    ("use-presentations", "journey"): (
        {
            "query": '"green laser pointer" bright bloom camera TV presentation',
            "role": "presenter",
            "task": "explain why a green dot appears overly bright or blooms on a camera or TV image",
            "condition": "the pointer may appear normal to the human eye",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party camera and TV appearance reports",
            "provisional_topic": "Why a Green Laser Pointer Dot Appears Overly Bright or Blooms on Camera or TV Images",
            "intent": "Help a presenter distinguish a camera or display appearance issue from an unsupported damage claim.",
        },
    ),
    ("safety-class", "decision"): (
        {
            "query": '"laser safety goggles" verify wavelength OD laser pointer',
            "role": "laser user",
            "task": "verify whether goggle markings correspond to a specific laser wavelength",
            "condition": "the goggles make broad multi-wavelength protection claims",
            "source_type": "old_plan_public_pain",
            "source_basis": "legacy Plan public third-party concerns about generic goggle markings",
            "provisional_topic": "Verifying Whether Laser Safety Goggles Match a Laser Pointer Wavelength",
            "intent": "Help a user interpret goggle markings and recognise when professional verification is needed.",
        },
    ),
}

# These are testable search hypotheses, not claims about demand. They use the
# coverage map to start with a concrete user task instead of concatenating a
# branch label with generic keyword fragments. The normal evidence gates still
# decide whether any hypothesis becomes a new page, an old-page update, or no
# recommendation at all.
NATURAL_RESEARCH_HYPOTHESES: dict[str, tuple[dict[str, str], ...]] = {
    "buying": (
        {
            "query": "usb rechargeable vs replaceable battery laser pointer",
            "topic": "Choosing USB-Rechargeable vs Replaceable-Battery Laser Pointers",
            "intent": "Help buyers choose between built-in USB charging and replaceable battery platforms based on runtime, travel, and replacement needs.",
        },
    ),
    "use-presentations": (
        {
            "query": "laser pointer LED presentation screen visibility",
            "topic": "Why a Laser Pointer Can Disappear on an LED Presentation Screen",
            "intent": "Help adult presenters diagnose why a visible beam is difficult to see on an LED presentation display.",
        },
        {
            "query": "laser pointer presentation screen failure causes",
            "topic": "Why a Laser Pointer Fails on a Presentation Screen",
            "intent": "Help adult presenters identify environmental and display conditions that make a pointer hard to see.",
        },
    ),
    "tech-wavelength": (
        {
            "query": "405nm vs 450nm laser pointer fluorescence visibility",
            "topic": "405nm vs 450nm Laser Pointers: Fluorescence, Visibility, and Use",
            "intent": "Help buyers compare violet and blue handheld lasers for visible beam appearance and fluorescence-related use.",
        },
    ),
    "tech-optics": (
        {
            "query": "G2 versus three element laser pointer lens beam divergence",
            "topic": "G2 vs Three-Element Lenses for Laser Pointer Beam Divergence",
            "intent": "Help owners compare two common lens designs when beam divergence and output trade-offs matter.",
        },
        {
            "query": "fixed focus vs adjustable focus laser pointer",
            "topic": "Fixed-Focus vs Adjustable-Focus Laser Pointers",
            "intent": "Help buyers decide whether an adjustable-focus mechanism solves their distance and beam-size needs better than a fixed-focus design.",
        },
    ),
    "operation": (
        {
            "query": "what to do when a laser pointer gets wet",
            "topic": "What to Do When a Laser Pointer Gets Wet",
            "intent": "Help an owner assess immediate next steps after water exposure without making unsupported repair claims.",
        },
    ),
    "safety-law": (
        {
            "query": "high power handheld laser beam termination safety",
            "topic": "How to Safely Terminate a High-Power Laser Pointer Beam",
            "intent": "Help high-power laser pointer owners identify an appropriate beam stop and recognise when an ordinary surface is not a safe terminus.",
        },
    ),
    "safety-eye": (
        {
            "query": "laser pointer diffuse reflection eye safety",
            "topic": "Laser Pointer Diffuse Reflections: What Changes the Eye-Safety Risk?",
            "intent": "Help owners distinguish a direct beam from a diffuse reflection when assessing eye-safety risk.",
        },
    ),
    "ops-battery": (
        {
            "query": "laser pointer battery voltage sag symptoms",
            "topic": "How to Recognise Battery Voltage Sag in a Laser Pointer",
            "intent": "Help owners distinguish battery voltage sag from an optical or diode problem.",
        },
        {
            "query": "21700 vs 18650 laser pointer battery",
            "topic": "21700 vs 18650 Batteries for Laser Pointers",
            "intent": "Help owners compare battery fit, runtime, and size limits before choosing a 21700 or 18650 powered laser pointer.",
        },
    ),
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
        item["_h2_sections"] = [h.strip() for h in headings]
        item["_body_head"] = body[:3000]
        item["_body_text"] = body
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


def _round_robin(groups: list[list[str]], limit: int | None = None) -> list[str]:
    """Interleave result groups so one noisy query cannot consume the budget."""

    result: list[str] = []
    max_length = max((len(group) for group in groups), default=0)
    for index in range(max_length):
        for group in groups:
            if index < len(group):
                result.append(group[index])
    return _unique(result, limit)


def _research_query(*parts: str) -> str:
    """Build a provider-safe query without cutting through the last word."""

    value = " ".join(" ".join(parts).split())
    if len(value) <= 100:
        return value
    shortened = value[:101].rsplit(" ", 1)[0].strip()
    return shortened or value[:100]


# This is deliberately a coverage checklist, not a taxonomy gate.  New wording
# that does not fit a known cluster remains ``emerging`` and can still become a
# seed or a candidate.  The labels only make one-run cooldown auditable.
SEMANTIC_CLUSTER_CHECKLIST: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "safety_regulation",
        (
            "safety",
            "law",
            "legal",
            "regulation",
            "goggle",
            "eye",
            "hazard",
            "minor",
            "airport",
            "airline",
            "customs",
        ),
    ),
    (
        "battery_charging",
        (
            "battery",
            "charging",
            "recharge",
            "18650",
            "21700",
            "cell",
            "voltage",
            "runtime",
            "usb-c",
        ),
    ),
    (
        "beam_optics",
        (
            "beam",
            "wavelength",
            "nm",
            "optics",
            "optical",
            "lens",
            "divergence",
            "focus",
            "speckle",
            "bloom",
            "fluorescence",
            "green dot",
        ),
    ),
    (
        "thermal_reliability",
        (
            "thermal",
            "heat",
            "hot",
            "overheat",
            "duty cycle",
            "lifespan",
            "reliability",
        ),
    ),
    (
        "operation_troubleshooting",
        (
            "stopped working",
            "dimming",
            "dim",
            "mode hopping",
            "troubleshoot",
            "repair",
            "wet",
            "drop",
            "cleaning",
            "storage",
        ),
    ),
    (
        "presentation_display",
        (
            "presentation",
            "presenter",
            "screen",
            "camera",
            "tv",
            "display",
            "projector",
        ),
    ),
    (
        "use_case_tasks",
        (
            "arborist",
            "pruning",
            "construction",
            "landscaping",
            "inspection",
            "astronomy",
            "telescope",
            "camping",
            "emergency",
            "photography",
            "pet",
            "dog",
            "cat",
        ),
    ),
    (
        "purchase_verification",
        (
            "buy",
            "buying",
            "choose",
            "comparison",
            "compare",
            "seller",
            "claim",
            "review",
            "brand",
            "model",
            "specification",
        ),
    ),
    (
        "accessories_compatibility",
        (
            "accessor",
            "mount",
            "holder",
            "case",
            "compatib",
            "fit",
            "attachment",
        ),
    ),
    (
        "power_components",
        (
            "power",
            "mw",
            "watt",
            "diode",
            "driver",
            "output",
            "current",
        ),
    ),
)

_SOURCE_KIND_BY_HOST: dict[str, str] = {
    "reddit.com": "forum_community",
    "quora.com": "forum_community",
    "laserpointerforums.com": "forum_community",
    "physicsforums.com": "forum_community",
    "stackoverflow.com": "forum_community",
    "youtube.com": "social_or_video",
    "youtu.be": "social_or_video",
    "facebook.com": "social_or_video",
    "x.com": "social_or_video",
    "twitter.com": "social_or_video",
    "instagram.com": "social_or_video",
    "tiktok.com": "social_or_video",
    "amazon.com": "review",
    "amazon.co.uk": "review",
    "trustpilot.com": "review",
}

_SOURCE_KIND_LABELS = {
    "paa_related": "PAA / related search",
    "competitor_or_commercial": "competitor or commercial page",
    "forum_community": "forum or community",
    "review": "review language",
    "social_or_video": "social or video",
    "official_or_reference": "official or reference",
    "other_public_page": "other public page",
}


def _semantic_cluster(*values: Any) -> str:
    """Return a transparent soft-scheduling label without excluding novelty."""

    text = " ".join(str(value or "") for value in values).casefold()
    if not text:
        return "emerging"
    for cluster, signals in SEMANTIC_CLUSTER_CHECKLIST:
        if any(signal in text for signal in signals):
            return cluster
    return "emerging"


def _source_kind(url: str, title: str = "") -> str:
    host = _host(url)
    for suffix, kind in _SOURCE_KIND_BY_HOST.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return kind
    if host.endswith((".gov", ".edu", ".ac.uk")) or any(
        token in host for token in ("iso.org", "ansi.org", "nist.gov", "nih.gov")
    ):
        return "official_or_reference"
    lowered = f"{host} {title}".casefold()
    if any(token in lowered for token in ("review", "ratings", "customer feedback")):
        return "review"
    if host and host not in {"laserpointerhub.com", "www.laserpointerhub.com"}:
        return "competitor_or_commercial"
    return "other_public_page"


def _anchor_fit_for_text(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if re.search(
        r"\b(?:laser\s+levels?|rotary\s+lasers?|laser\s+cutters?|laser\s+engravers?|"
        r"lightburn|cnc|laser\s+marking\s+machine|laser\s+welding|laser\s+sights?|"
        r"pistol|firearm|rifle|handgun)\b",
        text,
    ):
        return "off_anchor"
    if "laser pointer" in text or "handheld laser" in text:
        return "core"
    if "laser" in text:
        return "adjacent"
    return "off_anchor"


def _anchor_fit_for_source_observation(observed_text: str, follow_up_query: str) -> str:
    """Do not let a generated ``laser pointer`` query upgrade a source's object.

    Discovery queries often add the site core object to keep a web search
    focused.  That is useful for retrieval but it is not evidence that a page
    about LightBurn, a facade PDF, or a laser level is actually about a
    handheld pointer.  The source wording therefore wins for scheduling.
    """

    source_fit = _anchor_fit_for_text(observed_text)
    if source_fit != "off_anchor":
        return source_fit
    return "off_anchor"


def _source_probe_specs(seed_queries: list[str]) -> list[dict[str, str]]:
    """Build one first-pass query per public-language family.

    Tavily is the discovery transport here; the result URL, not the query
    label, determines the stored source kind.  These probes are intentionally
    parallel inputs to one mixed pool rather than a fixed source checklist.
    """

    def focus(query: str) -> str:
        value = re.sub(r'"?laser\s+pointer"?', "", query, flags=re.IGNORECASE)
        value = re.sub(r"\b(?:site|forum|reviews?|questions?)\b:?\S*", "", value)
        words = re.findall(r"[a-z0-9-]+", value.casefold())[:7]
        return " ".join(words) or "use"

    focal = [focus(query) for query in seed_queries] or ["use"]
    families = (
        ("forum_community", f'site:reddit.com "laser pointer" {focal[0]}'),
        (
            "social_or_video",
            f'site:youtube.com "laser pointer" {focal[min(1, len(focal) - 1)]}',
        ),
        ("review", f'"laser pointer" {focal[min(2, len(focal) - 1)]} review'),
        (
            "competitor_or_commercial",
            f'"laser pointer" {focal[0]} product guide',
        ),
        (
            "official_or_reference",
            f'"laser pointer" {focal[min(1, len(focal) - 1)]} guidance',
        ),
    )
    return [
        {"query": _research_query(query), "planned_source_kind": kind} for kind, query in families
    ]


def _open_scheduler_family_specs(_direction: str) -> list[dict[str, str]]:
    """Reproduce the successful broad entry map without direction narrowing."""

    return [
        {
            "family": key,
            "description": description,
            "query": _research_query(query),
            "planned_source_kind": f"open_scheduler_{key}",
        }
        for key, description, query in OPEN_SCHEDULER_FAMILIES
    ]


def _semantic_seed(seed: dict[str, Any]) -> str:
    explicit = str(seed.get("semantic_cluster") or "").strip()
    if explicit:
        return explicit
    card = seed.get("task_card") if isinstance(seed.get("task_card"), dict) else {}
    return _semantic_cluster(
        seed.get("target_ref"),
        seed.get("hypothesis", {}).get("topic") if isinstance(seed.get("hypothesis"), dict) else "",
        card.get("provisional_topic"),
        card.get("task"),
    )


def _previous_run_cooldown(settings: Settings, site_id: int) -> dict[str, Any]:
    """Read only the immediately preceding completed run's soft cooldown.

    A prior cluster is never accumulated indefinitely.  If the last completed
    run has no dominant cluster, the older run is intentionally ignored.  This
    is the important distinction between a one-run nudge and a permanent
    keyword blacklist.
    """

    with connection(settings) as conn:
        row = conn.execute(
            """
            SELECT id, filters_json, completed_at FROM research_runs
            WHERE site_id = ? AND status IN ('success', 'partial')
              AND completed_at IS NOT NULL
            ORDER BY completed_at DESC, id DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
    if not row:
        return {
            "status": "none",
            "source_run_id": None,
            "clusters": [],
            "policy": "previous_completed_run_only_soft_priority",
        }
    previous_filters = json_loads(row["filters_json"], {})
    raw_clusters = previous_filters.get("dominant_semantic_clusters") or []
    clusters = _unique([str(value) for value in raw_clusters], 8)
    return {
        "status": "active" if clusters else "no_dominant_cluster",
        "source_run_id": int(row["id"]),
        "source_completed_at": str(row["completed_at"]),
        "clusters": clusters,
        "policy": "previous_completed_run_only_soft_priority",
    }


def _select_diverse_seed_frontier(
    candidates: list[dict[str, Any]],
    *,
    cooled_clusters: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Select broad source families before returning to a cooled cluster.

    The selector does *not* remove cooled candidates.  It makes one pass over
    other viable source families first, then uses the cooled candidates when
    that is all the frontier has.  ``anchor_fit`` is likewise a tie-breaker,
    never an eligibility test.
    """

    if limit <= 0:
        return []
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    used_sources: set[str] = set()
    anchor_rank = {"core": 0, "adjacent": 1, "off_anchor": 2}

    def candidate_key(item: dict[str, Any]) -> tuple[int, int, int]:
        # Existing order is the final stable tie-breaker, so task-card order
        # remains predictable and source observations can be ranked upstream.
        return (
            1 if str(item.get("semantic_cluster") or "emerging") in cooled_clusters else 0,
            anchor_rank.get(str(item.get("anchor_fit") or "core"), 2),
            int(item.get("_frontier_order") or 0),
        )

    ordered = sorted(enumerate(candidates), key=lambda pair: (*candidate_key(pair[1]), pair[0]))
    for cooled in (False, True):
        # Core evidence should exhaust its viable frontier before a merely
        # adjacent/off-anchor source wins a slot solely for source diversity.
        # That remains a soft rank: if only adjacent evidence exists, it still
        # gets selected and stays visible to the operator.
        for fit in ("core", "adjacent", "off_anchor"):
            for require_new_source in (True, False):
                for index, candidate in ordered:
                    if index in selected_ids:
                        continue
                    is_cooled = (
                        str(candidate.get("semantic_cluster") or "emerging") in cooled_clusters
                    )
                    if is_cooled != cooled or str(candidate.get("anchor_fit") or "core") != fit:
                        continue
                    source = str(
                        candidate.get("source_family") or candidate.get("seed_source") or "other"
                    )
                    if require_new_source and source in used_sources:
                        continue
                    selected.append(candidate)
                    selected_ids.add(index)
                    used_sources.add(source)
                    if len(selected) >= limit:
                        return selected
    return selected


def _evidence_text(item: dict[str, Any]) -> str:
    """Flatten stored public evidence for quote validation; never invent text."""

    payload = item.get("payload") or {}
    parts: list[str] = []
    for key in (
        "query",
        "title",
        "url",
        "content",
        "markdown_excerpt",
        "markdown",
        "snippet",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    for group_key in ("related_questions", "related_searches", "organic_results", "results"):
        for value in payload.get(group_key) or []:
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict):
                parts.extend(
                    str(value.get(key) or "")
                    for key in ("question", "query", "title", "snippet", "content", "link", "url")
                )
    return " ".join(" ".join(parts).split())[:60000]


def _quote_is_supported(quote: str, item: dict[str, Any]) -> bool:
    normalized_quote = " ".join(quote.split()).casefold()
    if len(normalized_quote) < 12:
        return False
    return normalized_quote in _evidence_text(item).casefold()


def _source_url(item: dict[str, Any]) -> str | None:
    payload = item.get("payload") or {}
    direct = str(payload.get("url") or "").strip()
    if direct:
        return direct
    for result in payload.get("results") or payload.get("organic_results") or []:
        if isinstance(result, dict):
            value = str(result.get("url") or result.get("link") or "").strip()
            if value:
                return value
    return None


def _cms_duplicate_seed_audit_entry(topic: str, assessment: Any) -> dict[str, Any]:
    """Keep the CMS pre-check transparent without turning it into a new gate."""

    overlap = assessment.overlap if isinstance(assessment.overlap, dict) else {}
    matches = []
    for match in overlap.get("matches") or []:
        if not isinstance(match, dict):
            continue
        matches.append(
            {
                "title": str(match.get("title") or "")[:300],
                "canonical_url": str(match.get("canonical_url") or "")[:500] or None,
                "score": match.get("score"),
                "matched_tokens": [str(token) for token in match.get("matched_tokens") or []],
            }
        )
    return {
        "topic": topic[:300],
        "reason": "程序按当前 CMS 主主题和正文覆盖预筛为强重叠，未送去深挖。",
        "kind": "program_inference",
        "method": str(overlap.get("method") or "cms_primary_topic_and_structured_coverage_v3"),
        "max_score": overlap.get("max_score"),
        "matches": matches,
    }


def _raw_source_observations(evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist raw PAA and discovered-page language for a later mixed frontier."""

    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_observation(
        *,
        evidence_ref: str,
        source_kind: str,
        source_url: str | None,
        source_title: str,
        observed_text: str,
        source_quote: str,
        follow_up_query: str,
    ) -> None:
        query = _research_query(follow_up_query)
        normalized = normalize_topic(query)
        key = (evidence_ref, normalized)
        if not query or not normalized or key in seen:
            return
        seen.add(key)
        observations.append(
            {
                "evidence_ref": evidence_ref,
                "source_kind": source_kind,
                "source_url": source_url,
                "source_title": source_title[:300],
                "observed_text": observed_text[:3000],
                "source_quote": source_quote[:1000],
                "follow_up_query": query,
                "provisional_topic": "",
                "semantic_cluster": _semantic_cluster(query, observed_text),
                "anchor_fit": _anchor_fit_for_source_observation(
                    f"{source_title} {observed_text}", query
                ),
                "task_card": {
                    "role": "",
                    "task": "",
                    "condition": "",
                    "source_type": f"observed_{source_kind}",
                    "source_basis": "current public-source observation; not first-party feedback",
                    "provisional_topic": "",
                    "intent": "",
                },
            }
        )

    for evidence_ref, item in evidence.items():
        payload = item.get("payload") or {}
        if item.get("purpose") == "serp_snapshot":
            for raw in payload.get("related_questions") or []:
                question = str(raw.get("question") if isinstance(raw, dict) else raw or "").strip()
                if question:
                    append_observation(
                        evidence_ref=evidence_ref,
                        source_kind="paa_related",
                        source_url=None,
                        source_title="SERP related question",
                        observed_text=question,
                        source_quote=question,
                        follow_up_query=question,
                    )
            for raw in payload.get("related_searches") or []:
                query = str(raw.get("query") if isinstance(raw, dict) else raw or "").strip()
                if query:
                    append_observation(
                        evidence_ref=evidence_ref,
                        source_kind="paa_related",
                        source_url=None,
                        source_title="SERP related search",
                        observed_text=query,
                        source_quote=query,
                        follow_up_query=query,
                    )
        if item.get("purpose") != "source_discovery":
            continue
        for raw in payload.get("results") or []:
            if not isinstance(raw, dict):
                continue
            title = " ".join(str(raw.get("title") or "").split())
            source_url = str(raw.get("url") or raw.get("link") or "").strip() or None
            snippet = " ".join(str(raw.get("content") or raw.get("snippet") or "").split())
            observed = " ".join(value for value in (title, snippet) if value)
            if not observed:
                continue
            append_observation(
                evidence_ref=evidence_ref,
                source_kind=_source_kind(source_url or "", title),
                source_url=source_url,
                source_title=title or "discovered public page",
                observed_text=observed,
                source_quote=snippet or title,
                follow_up_query=(
                    title
                    if "laser" in title.casefold()
                    else _research_query('"laser pointer"', title)
                ),
            )
    return observations


def _diverse_source_observations(
    observations: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Interleave source families so a long PAA block cannot consume the pool."""

    if limit <= 0:
        return []
    families = list(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["source_observation_families"])
    family_rank = {str(value): index for index, value in enumerate(families)}
    unique_observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for observation in observations:
        query = normalize_topic(str(observation.get("follow_up_query") or ""))
        quote = normalize_topic(str(observation.get("source_quote") or ""))
        key = (
            str(observation.get("evidence_ref") or ""),
            query,
            quote,
        )
        if not query or key in seen:
            continue
        seen.add(key)
        unique_observations.append(observation)

    result: list[dict[str, Any]] = []
    for anchor_fit in ("core", "adjacent", "off_anchor"):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for observation in unique_observations:
            if str(observation.get("anchor_fit") or "off_anchor") != anchor_fit:
                continue
            family = str(observation.get("source_kind") or "other_public_page")
            grouped.setdefault(family, []).append(observation)
        ordered_families = sorted(
            grouped,
            key=lambda family: (family_rank.get(family, len(family_rank)), family),
        )
        max_length = max((len(grouped[family]) for family in ordered_families), default=0)
        for index in range(max_length):
            for family in ordered_families:
                group = grouped[family]
                if index >= len(group):
                    continue
                result.append(group[index])
                if len(result) >= limit:
                    return result
    return result


def _store_source_observations(
    settings: Settings,
    *,
    site_id: int,
    research_run_id: int,
    topic_id: int | None,
    dimension_key: str | None,
    observations: list[dict[str, Any]],
) -> int:
    """Store source language separately from candidates so it can seed one later run."""

    stored = 0
    max_items = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["source_observation_max_per_run"])
    selected_observations = _diverse_source_observations(observations, max_items)
    with connection(settings) as conn:
        for observation in selected_observations:
            query = _research_query(str(observation.get("follow_up_query") or ""))
            normalized = normalize_topic(query)
            if not query or not normalized:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO research_seed_observations(
                    site_id, research_run_id, topic_id, dimension_key, source_kind,
                    source_url, source_title, observed_text, source_quote, evidence_ref,
                    task_card_json, follow_up_query, normalized_query, provisional_topic,
                    semantic_cluster, anchor_fit, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site_id,
                    research_run_id,
                    topic_id,
                    dimension_key,
                    str(observation.get("source_kind") or "other_public_page"),
                    str(observation.get("source_url") or "") or None,
                    str(observation.get("source_title") or "")[:300] or None,
                    str(observation.get("observed_text") or "")[:3000],
                    str(observation.get("source_quote") or "")[:1000] or None,
                    str(observation.get("evidence_ref") or "") or None,
                    json_dumps(observation.get("task_card") or {}),
                    query,
                    normalized,
                    str(observation.get("provisional_topic") or "")[:300] or None,
                    str(observation.get("semantic_cluster") or _semantic_cluster(query)),
                    str(observation.get("anchor_fit") or _anchor_fit_for_text(query)),
                    utc_now(),
                ),
            )
            stored += int(cursor.rowcount > 0)
    return stored


def _observed_source_hypotheses(
    settings: Settings,
    *,
    site_id: int,
    topic_id: int | None,
    dimension_key: str | None,
    prior_queries: set[str],
    content_items: list[dict[str, Any]],
    cooled_clusters: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Load unconsumed source observations as auditable next-run seeds."""

    if limit <= 0:
        return []
    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT o.* FROM research_seed_observations o
            JOIN research_runs r ON r.id = o.research_run_id
            WHERE o.site_id = ? AND o.status = 'observed'
              AND r.status IN ('success', 'partial')
            ORDER BY o.id DESC LIMIT 120
            """,
            (site_id,),
        ).fetchall()
    candidates: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for order, row in enumerate(rows):
        item = dict(row)
        query = str(item["follow_up_query"])
        normalized = str(item["normalized_query"])
        if normalized in prior_queries or normalized in seen_queries:
            continue
        card = json_loads(item.get("task_card_json"), {})
        if not isinstance(card, dict):
            card = {}
        card = {
            key: str(card.get(key) or "").strip()
            for key in (
                "role",
                "task",
                "condition",
                "source_type",
                "source_basis",
                "provisional_topic",
                "intent",
            )
        }
        card.update(
            {
                "source_type": card["source_type"] or f"observed_{item['source_kind']}",
                "source_basis": card["source_basis"]
                or "current public-source observation; not first-party feedback",
                "provisional_topic": card["provisional_topic"]
                or str(item.get("provisional_topic") or ""),
                "source_url": item.get("source_url"),
                "source_quote": item.get("source_quote"),
                "evidence_ref": item.get("evidence_ref"),
                "semantic_cluster": str(item.get("semantic_cluster") or "emerging"),
            }
        )
        # A source-faithful card may have no provisional article title.  In
        # that case it stays a raw search seed instead of being force-fitted
        # through an artificial duplicate preflight title.
        if card["provisional_topic"]:
            preflight = _task_card_preflight(card, content_items)
            if preflight["status"] == "blocked":
                continue
            card["preflight"] = preflight
        candidate = {
            "id": None,
            "target_ref": query,
            "evidence": {"evidence_ids": []},
            "hypothesis": {"query": query, "topic": "", "intent": ""},
            "discovery_only": True,
            "seed_source": f"observed_{item['source_kind']}",
            "source_family": str(item["source_kind"]),
            "task_card": card,
            "source_observation_id": int(item["id"]),
            "semantic_cluster": str(item.get("semantic_cluster") or "emerging"),
            "anchor_fit": str(item.get("anchor_fit") or "core"),
            "_frontier_order": (0 if item.get("topic_id") == topic_id else 10)
            + (0 if item.get("dimension_key") == dimension_key else 1)
            + order,
        }
        candidates.append(candidate)
        seen_queries.add(normalized)
    return _select_diverse_seed_frontier(candidates, cooled_clusters=cooled_clusters, limit=limit)


def _task_card_count(topic_key: str) -> int:
    """Return the number of task-card hypotheses available for a topic branch."""

    return sum(
        len(cards)
        for (card_topic_key, _dimension_key), cards in TASK_COMPOSITION_SEED_CARDS.items()
        if card_topic_key == topic_key
    )


def _available_task_card_dimensions(topic_key: str, prior_queries: set[str]) -> set[str]:
    """Return dimensions with at least one untried task card before CMS preflight."""

    return {
        dimension_key
        for (card_topic_key, dimension_key), cards in TASK_COMPOSITION_SEED_CARDS.items()
        if card_topic_key == topic_key
        and any(
            normalize_topic(str(card.get("query") or "")) not in prior_queries for card in cards
        )
    }


def _task_card_preflight(
    card: dict[str, Any], content_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run duplicate-only CMS preflight for a planning card.

    It deliberately ignores demand and material readiness.  The result only
    prevents spending a live search call on an angle already proven to be the
    same intent or a covered body-level subtopic.  All other constraints remain
    diagnostics for the later candidate, just like ordinary research output.
    """

    qualification = qualify_candidate(
        str(card.get("provisional_topic") or ""),
        str(card.get("intent") or ""),
        [],
        [],
        [],
        content_items,
    )
    closest = qualification.closest_existing
    return {
        "status": qualification.qualification_status,
        "relationship": qualification.relationship,
        "closest_existing_title": closest.get("title"),
        "identity_score": round(float(closest.get("identity_score") or 0.0), 3),
        "coverage_score": round(float(closest.get("coverage_score") or 0.0), 3),
    }


def _task_composition_hypotheses(
    topic_key: str,
    dimension_key: str | None,
    prior_queries: set[str],
    content_items: list[dict[str, Any]],
    *,
    limit: int,
    cooled_clusters: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select untried, non-duplicate task cards with source diversity.

    This is a seed-quality step, not a new qualification gate.  A card skipped
    here already has affirmative duplicate evidence in the current CMS; weak
    demand, unclear scope, missing materials, and adjacent intent never remove
    it.  The selected card is persisted with the run as a planning hypothesis.
    """

    if not dimension_key or limit <= 0:
        return []
    candidates: list[dict[str, Any]] = []
    for raw_card in TASK_COMPOSITION_SEED_CARDS.get((topic_key, dimension_key), ()):
        query = _research_query(str(raw_card.get("query") or ""))
        if not query or normalize_topic(query) in prior_queries:
            continue
        card: dict[str, Any] = {
            key: str(raw_card.get(key) or "").strip()
            for key in (
                "role",
                "task",
                "condition",
                "source_type",
                "source_basis",
                "provisional_topic",
                "intent",
            )
        }
        card["semantic_cluster"] = _semantic_cluster(
            query,
            card["provisional_topic"],
            card["task"],
        )
        preflight = _task_card_preflight(card, content_items)
        if preflight["status"] == "blocked":
            continue
        card["preflight"] = preflight
        candidates.append(
            {
                "query": query,
                "topic": "",
                "intent": "",
                "seed_source": card["source_type"] or "old_plan_task_composition",
                "source_family": card["source_type"] or "old_plan_task_composition",
                "task_card": card,
                "semantic_cluster": card["semantic_cluster"],
                "anchor_fit": "core",
                "_frontier_order": len(candidates),
            }
        )
    return _select_diverse_seed_frontier(
        candidates,
        cooled_clusters=cooled_clusters or set(),
        limit=limit,
    )


def _question_chain_hypotheses(
    topic_key: str,
    branch_label: str,
    dimension_key: str | None,
) -> list[dict[str, str]]:
    """Build supplementary public-language probes for an unexhausted frontier.

    A task card gives a precise first query.  Where a branch only has one or
    two non-duplicate cards, the old Plan's question chain supplies the
    remaining differently worded discovery probes.  It broadens evidence
    collection, never the card's claimed task or the qualification rule.
    """

    core_anchor = '"laser pointer"'
    focus = RESEARCH_SEED_FOCUS_OVERRIDES.get(topic_key)
    if not focus:
        focus = re.sub(r"\blaser pointers?\b", "", branch_label, flags=re.IGNORECASE)
        focus = " ".join(focus.split())
    lenses = PLAN_QUESTION_CHAIN_LENSES.get(
        dimension_key or "",
        ("problems solutions", "review comparison questions"),
    )
    return [
        {
            "query": _research_query(
                core_anchor,
                focus,
                lens,
                "forum reviews questions" if index == 1 else "",
            ),
            # A plan lens is only a search prompt. It does not assert demand
            # and must never become a candidate title by itself.
            "topic": "",
            "intent": "",
            "seed_source": (
                "public_third_party_language_probe" if index == 1 else "plan_question_chain"
            ),
            "source_family": (
                "public_third_party_language_probe" if index == 1 else "plan_question_chain"
            ),
            "semantic_cluster": _semantic_cluster(branch_label, lens),
            "anchor_fit": "core",
        }
        for index, lens in enumerate(lenses)
    ]


def _natural_hypothesis_seeds(
    topic_key: str,
    branch_label: str,
    *,
    dimension_key: str | None,
    prior_queries: set[str],
    content_items: list[dict[str, Any]] | None = None,
    max_seed_count: int = 3,
    settings: Settings | None = None,
    site_id: int | None = None,
    topic_id: int | None = None,
    cooled_clusters: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return untried, natural-language hypotheses for a topic-graph branch."""

    cooled = cooled_clusters or set()
    observed: list[dict[str, Any]] = []
    if settings is not None and site_id is not None:
        observed = _observed_source_hypotheses(
            settings,
            site_id=site_id,
            topic_id=topic_id,
            dimension_key=dimension_key,
            prior_queries=prior_queries,
            content_items=content_items or [],
            cooled_clusters=cooled,
            limit=max_seed_count,
        )
    if len(observed) >= max_seed_count:
        return observed

    task_cards = _task_composition_hypotheses(
        topic_key,
        dimension_key,
        prior_queries,
        content_items or [],
        limit=max_seed_count - len(observed),
        cooled_clusters=cooled,
    )
    static_hypotheses = list(NATURAL_RESEARCH_HYPOTHESES.get(topic_key, ()))
    if task_cards:
        hypotheses: list[dict[str, Any]] = [*observed, *task_cards]
        # A card does not suppress the old Plan's independent question-chain
        # probes. This keeps a one-card niche branch from depending on one
        # search result while keeping the precise card first.
        if len(hypotheses) < max_seed_count:
            hypotheses.extend(
                _question_chain_hypotheses(topic_key, branch_label, dimension_key)[
                    : max_seed_count - len(hypotheses)
                ]
            )
        discovery_only = True
    elif static_hypotheses:
        hypotheses = [*observed, *static_hypotheses]
        discovery_only = False
    else:
        hypotheses = [
            *observed,
            *_question_chain_hypotheses(topic_key, branch_label, dimension_key),
        ]
        discovery_only = True
    result: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for hypothesis in hypotheses:
        query = str(hypothesis.get("target_ref") or hypothesis["query"])
        normalized_query = normalize_topic(query)
        if normalized_query in prior_queries or normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        result.append(
            {
                "id": hypothesis.get("id"),
                "target_ref": query,
                "evidence": dict(hypothesis.get("evidence") or {"evidence_ids": []}),
                "hypothesis": dict(hypothesis.get("hypothesis") or hypothesis),
                "discovery_only": bool(hypothesis.get("discovery_only", discovery_only)),
                "seed_source": str(
                    hypothesis.get("seed_source")
                    or ("plan_question_chain" if discovery_only else "coverage_map_hypothesis")
                ),
                "source_family": str(
                    hypothesis.get("source_family")
                    or hypothesis.get("seed_source")
                    or "coverage_map_hypothesis"
                ),
                "task_card": (
                    dict(hypothesis["task_card"])
                    if isinstance(hypothesis.get("task_card"), dict)
                    else None
                ),
                "source_observation_id": hypothesis.get("source_observation_id"),
                "semantic_cluster": str(
                    hypothesis.get("semantic_cluster") or _semantic_cluster(query)
                ),
                "anchor_fit": str(hypothesis.get("anchor_fit") or "core"),
            }
        )
        if len(result) >= max_seed_count:
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
            "input_refs": list(item.get("input_refs") or []),
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


def _expand_candidate_lineage(
    candidates: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]
) -> None:
    """Attach page captures collected from sources explicitly cited by a candidate."""

    for candidate in candidates:
        candidate.pop("expand_lineage", None)
        refs = set(str(ref) for ref in candidate.get("evidence_refs") or [])
        source_refs = {
            ref for ref in refs if (evidence.get(ref) or {}).get("purpose") == "source_discovery"
        }
        for evidence_id, item in evidence.items():
            input_refs = {str(ref) for ref in item.get("input_refs") or []}
            if item.get("purpose") == "page_capture" and source_refs & input_refs:
                refs.add(evidence_id)
        ordered_refs = [ref for ref in evidence if ref in refs]
        candidate["evidence_refs"] = ordered_refs
        facts = list(candidate.get("facts") or [])
        for ref in ordered_refs:
            if str(ref).endswith("page_capture"):
                facts.append(f"{ref} stores page-level material collected for this question")
        candidate["facts"] = _unique(facts, 8)


async def _ai_topics(
    settings: Settings,
    *,
    site_id: int,
    research_run_id: int,
    evidence: dict[str, dict[str, Any]],
    content_items: list[dict[str, Any]],
    seed_context: dict[str, Any],
    provider: AIProvider | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, bool]:
    input_refs = list(evidence)
    payload = {
        "method_version": RESEARCH_METHOD_VERSION,
        "seed_context": seed_context,
        "evidence": _compact_ai_evidence(evidence),
        "existing_content": [
            {
                "title": item.get("title"),
                "url": item.get("canonical_url"),
                "headings": list(item.get("_h2_sections") or [])[:12],
            }
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
                    raw_pattern = re.compile(re.escape(raw_ref), re.IGNORECASE)
                    if resolved and raw_pattern.search(normalized):
                        normalized = raw_pattern.sub(resolved, normalized)
                        matched = True
                if matched or any(ref in normalized for ref in refs):
                    normalized_facts.append(normalized)
            facts = normalized_facts[:8]
            intent = str(raw.get("intent") or "Intent requires operator review")[:300]
            rationale = str(raw.get("rationale") or "Limited synthesis of the stored evidence")[
                :1000
            ]
            inference = [str(value)[:1000] for value in raw.get("inference") or []][:8]
            seed_anchor_fit = str(raw.get("anchor_fit") or "core").strip().casefold()
            if seed_anchor_fit not in {"core", "adjacent", "off_anchor"}:
                seed_anchor_fit = "off_anchor"
            seed_anchor_note = str(raw.get("anchor_note") or "")[:300]
            anchor_inference = f"Seed anchor fit: {seed_anchor_fit}."
            if seed_anchor_note:
                anchor_inference += f" {seed_anchor_note}"
            semantic_cluster = str(raw.get("semantic_cluster") or "").strip().casefold()
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", semantic_cluster):
                semantic_cluster = _semantic_cluster(topic, intent, rationale, *facts)
            default_support = (
                "page_supported"
                if any((evidence.get(ref) or {}).get("purpose") == "page_capture" for ref in refs)
                else "question_only"
            )
            source_support = str(raw.get("source_support") or default_support).strip().casefold()
            if source_support not in {"page_supported", "snippet_supported", "question_only"}:
                source_support = "question_only"
            # Keep the structured anchor judgment even when the model already
            # returned eight free-form inference lines. Requalification relies
            # on this metadata and must not silently lose it.
            inference = _unique(
                [
                    anchor_inference,
                    f"Semantic cluster: {semantic_cluster}.",
                    f"Source support: {source_support}.",
                    *inference,
                ],
                8,
            )
            semantic_text = " ".join(
                [topic, intent, rationale, seed_anchor_note, *facts, *inference]
            )
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
                    "seed_anchor_fit": seed_anchor_fit,
                    "seed_anchor_note": seed_anchor_note,
                    "semantic_cluster": semantic_cluster,
                    "source_support": source_support,
                    "candidate_stage": "synthesized_angle",
                }
            )
        source_observations: list[dict[str, Any]] = []
        raw_observations = response.content.get("source_observations")
        if isinstance(raw_observations, list):
            stopwords = {
                "a",
                "an",
                "and",
                "for",
                "from",
                "in",
                "of",
                "on",
                "the",
                "to",
                "with",
            }

            def overlap(value: str, quote: str, minimum: int) -> bool:
                words = {
                    word
                    for word in re.findall(r"[a-z0-9]+", value.casefold())
                    if len(word) > 2 and word not in stopwords
                }
                quoted_words = set(re.findall(r"[a-z0-9]+", quote.casefold()))
                return len(words & quoted_words) >= min(minimum, len(words)) if words else False

            for raw in raw_observations[:12]:
                if not isinstance(raw, dict):
                    continue
                evidence_ref, _raw_ref = resolve_ref(raw.get("evidence_id"))
                if not evidence_ref or evidence_ref not in evidence:
                    continue
                source_item = evidence[evidence_ref]
                source_quote = " ".join(str(raw.get("source_quote") or "").split())[:1000]
                if not _quote_is_supported(source_quote, source_item):
                    continue
                follow_up_query = _research_query(str(raw.get("follow_up_query") or ""))
                if not follow_up_query or not overlap(follow_up_query, source_quote, 2):
                    follow_up_query = _research_query('"laser pointer"', source_quote)
                if not follow_up_query:
                    continue
                role = str(raw.get("role") or "")[:240]
                task = str(raw.get("task") or "")[:300]
                condition = str(raw.get("condition") or "")[:300]
                # Keep a field blank rather than preserving an AI-added role or
                # condition that cannot be tied back to the quoted wording.
                if role and not overlap(role, source_quote, 1):
                    role = ""
                if task and not overlap(task, source_quote, 2):
                    task = ""
                if condition and not overlap(condition, source_quote, 2):
                    condition = ""
                provisional_topic = str(raw.get("provisional_topic") or "")[:300]
                if provisional_topic and not overlap(provisional_topic, source_quote, 2):
                    provisional_topic = ""
                source_url = _source_url(source_item)
                source_kind = (
                    "paa_related"
                    if source_item.get("purpose") == "serp_snapshot"
                    else _source_kind(
                        source_url or "", str((source_item.get("payload") or {}).get("title") or "")
                    )
                )
                semantic_cluster = str(raw.get("semantic_cluster") or "").strip().casefold()
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", semantic_cluster):
                    semantic_cluster = _semantic_cluster(follow_up_query, source_quote)
                anchor_fit = str(raw.get("anchor_fit") or "").strip().casefold()
                source_anchor_fit = _anchor_fit_for_source_observation(
                    f"{(source_item.get('payload') or {}).get('title') or ''} {source_quote}",
                    follow_up_query,
                )
                if source_anchor_fit == "off_anchor":
                    anchor_fit = "off_anchor"
                elif anchor_fit not in {"core", "adjacent", "off_anchor"}:
                    anchor_fit = source_anchor_fit
                source_observations.append(
                    {
                        "evidence_ref": evidence_ref,
                        "source_kind": source_kind,
                        "source_url": source_url,
                        "source_title": str((source_item.get("payload") or {}).get("title") or "")[
                            :300
                        ],
                        "observed_text": source_quote,
                        "source_quote": source_quote,
                        "follow_up_query": follow_up_query,
                        "provisional_topic": provisional_topic,
                        "semantic_cluster": semantic_cluster,
                        "anchor_fit": anchor_fit,
                        "task_card": {
                            "role": role,
                            "task": task,
                            "condition": condition,
                            "source_type": f"observed_{source_kind}",
                            "source_basis": (
                                "AI extracted from a quoted current public source; "
                                "not first-party feedback"
                            ),
                            "provisional_topic": provisional_topic,
                            "intent": "",
                        },
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
        return topics, source_observations, response.model, True
    except AIUnavailable:
        return [], [], settings.ai_model, False
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
        return [], [], settings.ai_model, False


def _topic_pool_round_robin(groups: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """Interleave AI passes so no single lens consumes the visible pool."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_length = max((len(group) for group in groups), default=0)
    for index in range(max_length):
        for group in groups:
            if index >= len(group):
                continue
            candidate = group[index]
            normalized = normalize_topic(str(candidate.get("topic") or ""))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(candidate)
            if len(result) >= limit:
                return result
    return result


def _observation_ai_context(observation: dict[str, Any]) -> dict[str, Any]:
    card = observation.get("task_card") if isinstance(observation.get("task_card"), dict) else {}
    return {
        "evidence_id": str(observation.get("evidence_ref") or ""),
        "source_kind": str(observation.get("source_kind") or "other_public_page"),
        "source_url": str(observation.get("source_url") or "") or None,
        "source_title": str(observation.get("source_title") or "")[:300],
        "source_quote": str(observation.get("source_quote") or "")[:1000],
        "follow_up_query": str(observation.get("follow_up_query") or "")[:300],
        "provisional_topic": str(observation.get("provisional_topic") or "")[:300],
        "semantic_cluster": str(observation.get("semantic_cluster") or "emerging"),
        "anchor_fit": str(observation.get("anchor_fit") or "off_anchor"),
        "role": str(card.get("role") or "")[:240],
        "task": str(card.get("task") or "")[:300],
        "condition": str(card.get("condition") or "")[:300],
    }


async def _ai_seed_proposals(
    settings: Settings,
    *,
    site_id: int,
    direction: str,
    source_observations: list[dict[str, Any]],
    plan_seeds: list[dict[str, Any]],
    content_items: list[dict[str, Any]],
    cooldown: dict[str, Any],
    provider: AIProvider | None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Turn the successful open-family observation pool into searchable seeds."""

    observation_limit = int(
        MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["source_observation_max_per_run"]
    )
    diverse_observations = _diverse_source_observations(source_observations, observation_limit)
    input_refs = _unique(
        [str(item.get("evidence_ref") or "") for item in diverse_observations], 200
    )
    payload = {
        "method": "open_scheduler_prototype_v2_plus_previous_run_soft_cooldown",
        "core_scope": "handheld laser pointer",
        "operator_entry_context": direction,
        "operator_entry_context_policy": (
            "legacy Plan scheduling context only; never restrict the open source seed pool"
        ),
        "family_map": [
            {"key": key, "description": description}
            for key, description, _query_suffix in OPEN_SCHEDULER_FAMILIES
        ],
        "cooldown": cooldown,
        "source_observations": [_observation_ai_context(item) for item in diverse_observations],
        "legacy_plan_task_cards": [
            {
                "query": str(seed.get("target_ref") or ""),
                "source_type": str(seed.get("seed_source") or ""),
                "task_card": seed.get("task_card"),
            }
            for seed in plan_seeds
            if isinstance(seed.get("task_card"), dict)
        ],
        "existing_content": [
            {
                "title": item.get("title"),
                "url": item.get("canonical_url"),
                "headings": list(item.get("_h2_sections") or [])[:12],
            }
            for item in content_items[:100]
        ],
    }
    try:
        active_provider = provider or build_ai_provider(settings)
        response = await active_provider.complete_json(OPEN_SCHEDULER_SEED_PROMPT, payload)
        raw_proposals = response.content.get("seed_proposals")
        if not isinstance(raw_proposals, list):
            raise ValueError("AI output is missing seed_proposals")
        known_refs = set(input_refs)
        family_keys = {key for key, _description, _query_suffix in OPEN_SCHEDULER_FAMILIES}
        observation_by_ref = {
            str(item.get("evidence_ref") or ""): item for item in diverse_observations
        }
        proposals: list[dict[str, Any]] = []
        seen: set[str] = set()
        safety_limit = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["max_topic_candidates"])
        for order, raw in enumerate(raw_proposals[:safety_limit]):
            if not isinstance(raw, dict):
                continue
            query = _research_query(str(raw.get("query") or ""))
            topic = " ".join(str(raw.get("topic") or "").split())[:300]
            intent = " ".join(str(raw.get("intent") or "").split())[:300]
            normalized = normalize_topic(topic)
            if not query or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            refs: list[str] = []
            for value in raw.get("evidence_ids") or []:
                raw_ref = str(value).strip()
                resolved = raw_ref if raw_ref in known_refs else None
                if not resolved:
                    matches = [ref for ref in known_refs if ref.startswith(f"{raw_ref}:")]
                    resolved = matches[0] if len(matches) == 1 else None
                if resolved:
                    refs.append(resolved)
            refs = _unique(refs, 20)
            if not refs or re.search(r"[\u3400-\u9fff]", f"{topic} {intent}"):
                continue
            declared_anchor = str(raw.get("anchor_fit") or "").casefold()
            text_anchor = _anchor_fit_for_text(topic, intent, query)
            anchor_fit = (
                "off_anchor" if "off_anchor" in {declared_anchor, text_anchor} else text_anchor
            )
            if anchor_fit == "off_anchor":
                continue
            family = str(raw.get("family") or "emerging").casefold()
            if family not in family_keys:
                family = "emerging"
            semantic_cluster = str(raw.get("semantic_cluster") or "").casefold()
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", semantic_cluster):
                semantic_cluster = _semantic_cluster(topic, intent, query)
            primary_observation = next(
                (observation_by_ref[ref] for ref in refs if ref in observation_by_ref), {}
            )
            proposals.append(
                {
                    "id": None,
                    "target_ref": query,
                    "seed_source": "open_scheduler_source_seed",
                    "source_family": family,
                    "source_kind": str(
                        primary_observation.get("source_kind") or "other_public_page"
                    ),
                    "semantic_cluster": semantic_cluster,
                    "anchor_fit": anchor_fit,
                    "_frontier_order": order,
                    "evidence": {"evidence_ids": refs},
                    "hypothesis": {"topic": topic, "intent": intent},
                    "task_card": None,
                    "discovery_only": True,
                    "shape": str(raw.get("shape") or "emerging")[:48],
                    "rationale": str(raw.get("rationale") or "")[:1000],
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
        return proposals, response.model, True
    except Exception:
        return [], settings.ai_model, False


async def _ai_selected_seed_pool(
    settings: Settings,
    *,
    site_id: int,
    research_run_id: int,
    evidence: dict[str, dict[str, Any]],
    content_items: list[dict[str, Any]],
    seed_context: dict[str, Any],
    seeds: list[dict[str, Any]],
    provider: AIProvider | None,
    max_requests: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, dict[str, int]]:
    """Deep-package selected seeds while retaining every seed proposal separately."""

    request_count = min(max(0, int(max_requests)), len(seeds))
    stats = {"actual_requests": 0, "successful": 0, "failed": 0}
    candidate_groups: list[list[dict[str, Any]]] = []
    observation_results: list[dict[str, Any]] = []
    proposed_topics: list[dict[str, str]] = []
    ai_model = settings.ai_model
    for seed in seeds[:request_count]:
        seed_refs = set(str(ref) for ref in (seed.get("evidence") or {}).get("evidence_ids", []))
        relevant_refs = set(seed_refs)
        query = normalize_topic(str(seed.get("target_ref") or ""))
        for evidence_id, item in evidence.items():
            payload_query = normalize_topic(str((item.get("payload") or {}).get("query") or ""))
            input_refs = {str(ref) for ref in item.get("input_refs") or []}
            if payload_query == query or seed_refs & input_refs:
                relevant_refs.add(evidence_id)
        relevant_evidence = {
            evidence_id: item
            for evidence_id, item in evidence.items()
            if evidence_id in relevant_refs
        }
        if not relevant_evidence:
            relevant_evidence = evidence
        pass_context = dict(seed_context)
        pass_context["seeds"] = [
            {
                "query": str(seed.get("target_ref") or ""),
                "source_type": str(seed.get("seed_source") or ""),
                "family": str(seed.get("source_family") or "emerging"),
                "semantic_cluster": str(seed.get("semantic_cluster") or "emerging"),
                "anchor_fit": str(seed.get("anchor_fit") or "core"),
                "hypothesis": seed.get("hypothesis"),
                "task_card": seed.get("task_card"),
            }
        ]
        pass_context["already_proposed_topics"] = proposed_topics[-80:]
        stats["actual_requests"] += 1
        candidates, observations, model, ok = await _ai_topics(
            settings,
            site_id=site_id,
            research_run_id=research_run_id,
            evidence=relevant_evidence,
            content_items=content_items,
            seed_context=pass_context,
            provider=provider,
        )
        ai_model = model or ai_model
        stats["successful" if ok else "failed"] += 1
        if not ok:
            candidate_groups.append([])
            continue
        for candidate in candidates:
            candidate["candidate_stage"] = "synthesized_angle"
        candidate_groups.append(candidates)
        observation_results.extend(observations)
        proposed_topics.extend(
            {
                "topic": str(candidate.get("topic") or "")[:300],
                "intent": str(candidate.get("intent") or "")[:300],
            }
            for candidate in candidates
        )

    safety_limit = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["max_topic_candidates"])
    return (
        _topic_pool_round_robin(candidate_groups, safety_limit),
        _diverse_source_observations(observation_results, safety_limit),
        ai_model,
        stats,
    )


async def _ai_intent_deduplicate_pool(
    settings: Settings,
    *,
    site_id: int,
    research_run_id: int,
    candidates: list[dict[str, Any]],
    provider: AIProvider | None,
) -> tuple[list[dict[str, Any]], str | None, bool, dict[str, Any]]:
    """Restore the prototype's final same-intent clustering without topic quotas.

    The model may only group supplied IDs.  It cannot add a topic, qualify one,
    or override the deterministic CMS gate.  Raw source leads remain outside
    this pass because they are discovery material rather than article angles.
    """

    synthesized: list[tuple[int, dict[str, Any]]] = [
        (index, candidate)
        for index, candidate in enumerate(candidates)
        if str(candidate.get("candidate_stage") or "synthesized_angle") != "raw_lead"
        and not str(candidate.get("rationale") or "").startswith("Raw source lead:")
    ]
    audit = {
        "input_synthesized": len(synthesized),
        "same_intent_clusters": 0,
        "merged_current_variants": 0,
        "prior_pool_duplicates": 0,
        "output_candidates": len(candidates),
        "current_variant_merges": [],
        "prior_pool_duplicate_audit": [],
    }
    if len(synthesized) < 2:
        return candidates, settings.ai_model, True, audit

    current_items = [
        {
            "id": f"current-{index}",
            "topic": str(candidate.get("topic") or "")[:300],
            "intent": str(candidate.get("intent") or "")[:300],
            "rationale": str(candidate.get("rationale") or "")[:600],
        }
        for index, candidate in synthesized
    ]
    with connection(settings) as conn:
        prior_rows = conn.execute(
            """
            SELECT id, topic, intent FROM research_candidates
            WHERE site_id = ? AND research_run_id <> ?
              AND decision IN ('pending','do','dont_recommend')
              AND qualification_status = 'qualified'
              AND recommended_disposition = 'new_article'
              AND rationale NOT LIKE 'Raw source lead:%'
              AND inference_json NOT LIKE '%Seed anchor fit: off_anchor.%'
            ORDER BY id
            """,
            (site_id, research_run_id),
        ).fetchall()
    prior_items = [
        {
            "id": f"prior-{int(row['id'])}",
            "topic": str(row["topic"])[:300],
            "intent": str(row["intent"] or "")[:300],
        }
        for row in prior_rows
    ]
    payload = {
        "current_candidates": current_items,
        "prior_topic_pool": prior_items,
    }
    input_refs = _unique(
        [
            str(ref)
            for _index, candidate in synthesized
            for ref in candidate.get("evidence_refs") or []
        ],
        200,
    )
    try:
        active_provider = provider or build_ai_provider(settings)
        response = await active_provider.complete_json(
            OPEN_SCHEDULER_INTENT_CLUSTER_PROMPT,
            payload,
        )
        known_current = {item["id"] for item in current_items}
        known_prior = {item["id"] for item in prior_items}
        candidate_by_id = {f"current-{index}": candidate for index, candidate in synthesized}
        original_index_by_id = {f"current-{index}": index for index, _item in synthesized}
        prior_by_id = {str(item["id"]): item for item in prior_items}

        clustered_ids: set[str] = set()
        cluster_by_member: dict[str, tuple[str, list[str], str]] = {}
        for raw in response.content.get("same_intent_clusters") or []:
            if not isinstance(raw, dict):
                continue
            members = _unique(
                [str(value) for value in raw.get("member_ids") or [] if str(value) in known_current]
            )
            representative = str(raw.get("representative_id") or "")
            if representative not in members or len(members) < 2:
                continue
            if any(member in clustered_ids for member in members):
                continue
            clustered_ids.update(members)
            reason = str(raw.get("reason") or "same primary user intent")[:500]
            cluster = (representative, members, reason)
            for member in members:
                cluster_by_member[member] = cluster

        prior_duplicate_ids: set[str] = set()
        prior_duplicate_details: dict[str, tuple[str, str]] = {}
        for raw in response.content.get("duplicates_of_prior") or []:
            if not isinstance(raw, dict):
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            prior_id = str(raw.get("prior_id") or "")
            if candidate_id in known_current and prior_id in known_prior:
                if candidate_id in prior_duplicate_ids:
                    continue
                prior_duplicate_ids.add(candidate_id)
                prior_duplicate_details[candidate_id] = (
                    prior_id,
                    str(raw.get("reason") or "same primary user intent")[:500],
                )

        merged_candidates: dict[str, dict[str, Any]] = {}
        for member_id, (representative, members, reason) in cluster_by_member.items():
            if member_id != representative or representative in merged_candidates:
                continue
            retained_members = [member for member in members if member not in prior_duplicate_ids]
            if not retained_members:
                continue
            if representative not in retained_members:
                representative = min(
                    retained_members,
                    key=lambda value: original_index_by_id[value],
                )
            merged = dict(candidate_by_id[representative])
            merged["evidence_refs"] = _unique(
                [
                    str(ref)
                    for member in retained_members
                    for ref in candidate_by_id[member].get("evidence_refs") or []
                ],
                20,
            )
            merged["facts"] = _unique(
                [
                    str(value)
                    for member in retained_members
                    for value in candidate_by_id[member].get("facts") or []
                ],
                20,
            )
            merged["inference"] = _unique(
                [
                    *list(merged.get("inference") or []),
                    (
                        f"Same-intent clustering merged {len(retained_members)} source-backed "
                        f"wording variants: {reason}"
                    ),
                ],
                12,
            )
            merged_candidates[representative] = merged

        output: list[dict[str, Any]] = []
        emitted_clusters: set[str] = set()
        for index, candidate in enumerate(candidates):
            candidate_id = f"current-{index}"
            if candidate_id not in known_current:
                output.append(candidate)
                continue
            if candidate_id in prior_duplicate_ids:
                continue
            cluster = cluster_by_member.get(candidate_id)
            if not cluster:
                output.append(candidate)
                continue
            representative = cluster[0]
            retained_representative = (
                representative
                if representative in merged_candidates
                else next(
                    (member for member in cluster[1] if member in merged_candidates),
                    None,
                )
            )
            if retained_representative and retained_representative not in emitted_clusters:
                output.append(merged_candidates[retained_representative])
                emitted_clusters.add(retained_representative)

        valid_clusters = {id(cluster) for cluster in cluster_by_member.values()}
        current_variant_merges: list[dict[str, Any]] = []
        audited_clusters: set[int] = set()
        for cluster in cluster_by_member.values():
            cluster_id = id(cluster)
            if cluster_id in audited_clusters:
                continue
            audited_clusters.add(cluster_id)
            representative, members, reason = cluster
            retained_members = [member for member in members if member not in prior_duplicate_ids]
            if len(retained_members) < 2:
                continue
            retained_representative = (
                representative
                if representative in retained_members
                else min(retained_members, key=lambda value: original_index_by_id[value])
            )
            current_variant_merges.append(
                {
                    "retained_topic": str(
                        candidate_by_id[retained_representative].get("topic") or ""
                    )[:300],
                    "merged_topics": [
                        str(candidate_by_id[member].get("topic") or "")[:300]
                        for member in retained_members
                        if member != retained_representative
                    ],
                    "reason": reason,
                }
            )
        audit.update(
            {
                "same_intent_clusters": len(valid_clusters),
                "merged_current_variants": max(
                    0,
                    len(synthesized)
                    - len(prior_duplicate_ids)
                    - sum(
                        1
                        for item in output
                        if str(item.get("candidate_stage") or "synthesized_angle") != "raw_lead"
                        and not str(item.get("rationale") or "").startswith("Raw source lead:")
                    ),
                ),
                "prior_pool_duplicates": len(prior_duplicate_ids),
                "output_candidates": len(output),
                "current_variant_merges": current_variant_merges,
                "prior_pool_duplicate_audit": [
                    {
                        "candidate_topic": str(candidate_by_id[candidate_id].get("topic") or "")[
                            :300
                        ],
                        "prior_topic": str(prior_by_id[prior_id].get("topic") or "")[:300],
                        "reason": reason,
                    }
                    for candidate_id, (prior_id, reason) in prior_duplicate_details.items()
                ],
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
        return output, response.model, True, audit
    except Exception as exc:
        with connection(settings) as conn:
            conn.execute(
                """
                INSERT INTO ai_runs(
                    site_id, opportunity_id, purpose, provider, model, prompt_sha256,
                    input_refs_json, output_json, status, error_message, created_at
                ) VALUES(?, NULL, 'topic_research', ?, ?, ?, ?, NULL, 'failed', ?, ?)
                """,
                (
                    site_id,
                    settings.ai_provider,
                    settings.ai_model or "unknown",
                    hashlib.sha256(
                        OPEN_SCHEDULER_INTENT_CLUSTER_PROMPT.encode("utf-8")
                    ).hexdigest(),
                    json_dumps(input_refs),
                    f"{type(exc).__name__}: intent clustering failed",
                    utc_now(),
                ),
            )
        return candidates, settings.ai_model, False, audit


_SEED_ANCHOR_INFERENCE_RE = re.compile(
    r"^Seed anchor fit: (core|adjacent|off_anchor)\.\s*(.*)$", re.IGNORECASE
)


def _seed_anchor_from_candidate(candidate: dict[str, Any]) -> tuple[str | None, str | None]:
    """Recover the AI anchor judgment from the separately stored inference."""

    for value in json_loads(candidate.get("inference_json"), []):
        match = _SEED_ANCHOR_INFERENCE_RE.match(str(value).strip())
        if match:
            return match.group(1).casefold(), match.group(2).strip() or None
    return None, None


_SPECIFIC_RECOMMENDATION_RE = re.compile(
    r"\b(?:best|recommended?|top\s+\d+|brands?\s+and\s+models?|which\s+.*\b(?:buy|choose)|"
    r"compatible\s+(?:brands?|models?|products?))\b",
    re.IGNORECASE,
)
_PACKAGING_STOPWORDS = {
    "a",
    "an",
    "and",
    "best",
    "brands",
    "for",
    "how",
    "laser",
    "model",
    "models",
    "of",
    "pointer",
    "pointers",
    "recommended",
    "the",
    "to",
    "top",
    "use",
    "using",
    "what",
    "which",
}
_TASK_TOKEN_ALIASES = {
    "arborists": "arborist",
    "communicate": "indicate",
    "communication": "indicate",
    "indicating": "indicate",
    "indicate": "indicate",
    "inspection": "inspect",
    "inspector": "inspect",
    "mark": "indicate",
    "marking": "indicate",
    "point": "indicate",
    "pointing": "indicate",
    "pruning": "prune",
    "recommendations": "recommend",
    "show": "indicate",
    "showing": "indicate",
    "trees": "tree",
    "uses": "use",
    "using": "use",
}
_TASK_ACTION_TOKENS = {
    "buy",
    "choose",
    "compare",
    "diagnose",
    "indicate",
    "inspect",
    "repair",
    "use",
    "verify",
}
_TASK_GENERIC_TOKENS = {
    "blue",
    "green",
    "handheld",
    "high",
    "laser",
    "pointer",
    "power",
    "red",
    "specific",
}


def _candidate_task_signature(topic: str, intent: str) -> set[str]:
    """Small conservative signature used only to suppress a repeated pool item."""

    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", f"{topic} {intent}".casefold()):
        token = _TASK_TOKEN_ALIASES.get(raw, raw)
        if len(token) > 2 and token not in _PACKAGING_STOPWORDS:
            tokens.add(token)
    return tokens


def _repeats_prior_candidate_primary_task(
    topic: str, intent: str, prior_candidates: list[dict[str, Any]]
) -> bool:
    """Reject only a clearly repeated primary task from the visible topic pool.

    This is deliberately narrower than CMS qualification.  It does not decide
    whether a neighbouring task merits a new page; it only stops a later run
    from presenting the same ``object + action + context`` as a second choice
    after that idea already exists in the pending/accepted candidate pool.
    """

    signature = _candidate_task_signature(topic, intent)
    if not signature:
        return False
    for prior in prior_candidates:
        previous = _candidate_task_signature(
            str(prior.get("title") or ""), str(prior.get("intent") or "")
        )
        shared = signature & previous
        shared_actions = shared & _TASK_ACTION_TOKENS
        shared_context = shared - _TASK_ACTION_TOKENS - _TASK_GENERIC_TOKENS
        if len(shared) >= 3 and shared_actions and len(shared_context) >= 2:
            return True
    return False


def _candidate_has_direct_page_support(
    candidate: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> bool:
    """Avoid upgrading a title/snippet-only product recommendation to an angle."""

    page_texts = [
        _evidence_text(evidence[ref]).casefold()
        for ref in candidate.get("evidence_refs") or []
        if ref in evidence and (evidence[ref].get("purpose") == "page_capture")
    ]
    if not page_texts:
        return False
    topic = str(candidate.get("topic") or "")
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", topic.casefold())
        if len(term) > 2 and term not in _PACKAGING_STOPWORDS
    ]
    for page_text in page_texts:
        # A recommendation needs a page about the core object, not merely a
        # result for a laser level/cutter or a generic flashlight product.
        if "laser pointer" not in page_text and "handheld laser" not in page_text:
            continue
        if len({term for term in terms if term in page_text}) >= min(2, len(terms)):
            return True
    return False


def _guard_candidate_packaging(
    candidate: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Keep weak source language visible without promoting unsupported claims."""

    guarded = dict(candidate)
    topic = str(guarded.get("topic") or "")
    should_be_raw = str(guarded.get("candidate_stage") or "").casefold() == "raw_lead"
    if _SPECIFIC_RECOMMENDATION_RE.search(topic) and not _candidate_has_direct_page_support(
        guarded, evidence
    ):
        should_be_raw = True
    if not should_be_raw:
        return guarded
    rationale = str(guarded.get("rationale") or "External source lead requiring verification")
    if not rationale.startswith("Raw source lead:"):
        guarded["rationale"] = f"Raw source lead: {rationale}"[:1000]
    guarded["inference"] = _unique(
        [
            *list(guarded.get("inference") or []),
            "Packaging restriction: retained as a raw lead until a page directly supports the claim.",
        ],
        8,
    )
    return guarded


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
    resolution_audit: list[dict[str, Any]] | None = None,
) -> int:
    inserted = 0
    seen: set[str] = set()
    limit = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["max_topic_candidates"])
    with connection(settings) as conn:
        prior_topics = conn.execute(
            """
            SELECT id, topic, intent FROM research_candidates
            WHERE site_id = ? AND research_run_id <> ?
              AND decision IN ('pending','do','dont_recommend')
              AND NOT (
                  decision = 'pending'
                  AND (
                      qualification_status IN ('stale','needs_evidence','needs_human_review')
                      OR qualification_version IS NULL
                      OR qualification_version <> ?
                  )
              )
            ORDER BY id
            """,
            (site_id, research_run_id, QUALIFICATION_RULE_VERSION),
        ).fetchall()
    prior_candidate_items = [
        {
            "id": -int(row["id"]),
            "content_type": "candidate",
            "title": str(row["topic"]),
            "intent": str(row["intent"] or ""),
            "slug": str(row["topic"]),
            "seo_title": str(row["topic"]),
            "search_text": str(row["topic"]),
        }
        for row in prior_topics
    ]

    def record_resolution(
        topic: str,
        reason_key: str,
        reason: str,
        *,
        kind: str = "program_inference",
        related_topic: str | None = None,
    ) -> None:
        if resolution_audit is None:
            return
        resolution_audit.append(
            {
                "topic": topic[:300],
                "reason_key": reason_key,
                "reason": reason,
                "kind": kind,
                "related_topic": related_topic[:300] if related_topic else None,
            }
        )

    for candidate in candidates:
        candidate = _guard_candidate_packaging(candidate, evidence)
        semantic_cluster = str(candidate.get("semantic_cluster") or "").strip()
        if not semantic_cluster:
            semantic_cluster = _semantic_cluster(
                candidate.get("topic"), candidate.get("intent"), candidate.get("rationale")
            )
        candidate["inference"] = _unique(
            [
                f"Semantic cluster: {semantic_cluster}.",
                *list(candidate.get("inference") or []),
            ],
            8,
        )
        topic = " ".join(str(candidate.get("topic") or "").split()).strip()[:300]
        intent_text = str(candidate.get("intent") or "")[:300]
        declared_anchor = str(candidate.get("seed_anchor_fit") or "").casefold()
        if (
            declared_anchor == "off_anchor"
            or _anchor_fit_for_text(topic, intent_text) == "off_anchor"
        ):
            if topic:
                record_resolution(
                    topic,
                    "off_anchor",
                    "核心对象偏离手持激光笔，未进入候选池。",
                )
            continue
        normalized = normalize_topic(topic)
        if not normalized:
            continue
        if normalized in seen:
            record_resolution(
                topic,
                "duplicate_in_current_pool",
                "与本轮已处理的相同规范化候选重复，未重复保存。",
            )
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
                SELECT id, topic, decision, qualification_status, qualification_version,
                       evidence_fingerprint
                FROM research_candidates
                WHERE site_id = ? AND normalized_topic = ?
                  AND research_run_id <> ?
                ORDER BY id DESC LIMIT 1
                """,
                (site_id, normalized, research_run_id),
            ).fetchone()
        skip_cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        if remembered and remembered["decision"] == "dont_recommend":
            record_resolution(
                topic,
                "operator_dont_recommend",
                "运营者此前标为不适合，本轮不重复推荐。",
                kind="operator_decision",
            )
            continue
        if (
            remembered
            and remembered["decision"] == "skip"
            and str(remembered["updated_at"]) >= skip_cutoff
        ):
            record_resolution(
                topic,
                "recent_operator_skip",
                "运营者近期选择跳过，本轮不重复推荐。",
                kind="operator_decision",
            )
            continue
        if prior and (
            prior["decision"] in {"do", "dont_recommend"}
            or (
                prior["decision"] == "pending"
                and str(prior["qualification_version"] or "") == QUALIFICATION_RULE_VERSION
                and str(prior["qualification_status"] or "")
                not in {"stale", "needs_evidence", "needs_human_review"}
            )
        ):
            record_resolution(
                topic,
                "already_decided_prior",
                "此前候选已在有效池中处理，本轮不重复保存。",
                related_topic=str(prior["topic"] or "") or None,
            )
            continue
        refs = [ref for ref in _unique(candidate.get("evidence_refs") or [], 12) if ref in evidence]
        if not refs:
            record_resolution(
                topic,
                "missing_evidence",
                "没有可追溯的本轮证据引用，未保存为候选。",
            )
            continue
        if _repeats_prior_candidate_primary_task(topic, intent_text, prior_candidate_items):
            record_resolution(
                topic,
                "repeats_prior_primary_task",
                "程序判断其主要任务与已有候选重复，未重复保存。",
            )
            continue
        prior_assessment = assess_discovered_topic(topic, prior_candidate_items)
        if prior_candidate_items and prior_assessment.gate_status == "blocked":
            match = next(iter(prior_assessment.overlap.get("matches") or []), {})
            record_resolution(
                topic,
                "prior_candidate_overlap",
                "程序按已有候选的主主题与正文覆盖预筛为强重叠，未重复保存。",
                related_topic=str(match.get("title") or "") or None,
            )
            continue
        assessment = assess_discovered_topic(topic, content_items)
        urls = _evidence_urls(evidence, refs)
        facts = list(candidate.get("facts") or [])
        qualification = qualify_candidate(
            topic=topic,
            intent=intent_text,
            facts=facts,
            source_urls=urls,
            evidence_ids=refs,
            content_items=content_items,
            research_label=research_label,
            seed_anchor_fit=str(candidate.get("seed_anchor_fit") or "") or None,
            seed_anchor_note=str(candidate.get("seed_anchor_note") or "") or None,
        )
        cms_fingerprint = compute_cms_fingerprint(content_items)
        evidence_fingerprint = compute_evidence_fingerprint(refs, urls)
        superseded_candidate_id: int | None = None
        if (
            prior
            and prior["decision"] == "pending"
            and (
                str(prior["qualification_status"] or "")
                in {"stale", "needs_evidence", "needs_human_review"}
                or str(prior["qualification_version"] or "") != QUALIFICATION_RULE_VERSION
            )
        ):
            superseded_candidate_id = int(prior["id"])
        # The pre-v10 audit column only supports needs_evidence/blocked. The
        # authoritative decision is qualification_status; preserve the old
        # deterministic assessment here instead of silently widening history.
        legacy_gate_status = assessment.gate_status
        with connection(settings) as conn:
            if superseded_candidate_id is not None:
                conn.execute(
                    """
                    UPDATE research_candidates
                    SET qualification_status = 'stale', stale_after = ?,
                        previous_assessment_json = ?
                    WHERE id = ? AND decision = 'pending'
                    """,
                    (
                        utc_now(),
                        json_dumps(
                            {
                                "reason": "superseded_by_current_qualification",
                                "qualification_status": prior["qualification_status"],
                                "qualification_version": prior["qualification_version"],
                                "evidence_fingerprint": prior["evidence_fingerprint"],
                            }
                        ),
                        superseded_candidate_id,
                    ),
                )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO research_candidates(
                    research_run_id, site_id, topic, normalized_topic, intent, rationale,
                    recommended_next_step, gate_status, evidence_refs_json,
                    source_urls_json, facts_json, inference_json, overlap_json,
                    limitations_json, created_at, suggested_parent_topic_id,
                    qualification_status, qualification_version, cms_fingerprint,
                    evidence_fingerprint, closest_existing_json, evidence_demand_json,
                    evidence_gap_json, evidence_material_json, recommended_disposition,
                    qualified_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id,
                    site_id,
                    topic,
                    normalized,
                    intent_text,
                    str(candidate.get("rationale") or "External topic lead requiring verification")[
                        :1000
                    ],
                    qualification.next_step,
                    legacy_gate_status,
                    json_dumps(refs),
                    json_dumps(urls),
                    json_dumps(facts),
                    json_dumps(candidate.get("inference") or []),
                    json_dumps(assessment.overlap),
                    json_dumps(qualification.limitations),
                    utc_now(),
                    suggested_parent_topic_id,
                    qualification.qualification_status,
                    QUALIFICATION_RULE_VERSION,
                    cms_fingerprint,
                    evidence_fingerprint,
                    json_dumps(qualification.closest_existing),
                    json_dumps(qualification.evidence_demand),
                    json_dumps(qualification.evidence_gap),
                    json_dumps(qualification.evidence_material),
                    qualification.recommended_disposition,
                    utc_now() if qualification.qualification_status == "qualified" else None,
                ),
            )
            was_inserted = cursor.rowcount > 0
            inserted += int(was_inserted)
            if was_inserted:
                prior_candidate_items.append(
                    {
                        "id": -int(cursor.lastrowid),
                        "content_type": "candidate",
                        "title": topic,
                        "intent": intent_text,
                        "slug": topic,
                        "seo_title": topic,
                        "search_text": topic,
                    }
                )
                if qualification.recommended_disposition == DISPOSITION_UPDATE_EXISTING:
                    inserted_row = conn.execute(
                        "SELECT * FROM research_candidates WHERE id = ?", (cursor.lastrowid,)
                    ).fetchone()
                    if inserted_row:
                        _upsert_research_existing_opportunity(conn, inserted_row, utc_now())
                if qualification.recommended_disposition == DISPOSITION_UPDATE_EXISTING:
                    closest_existing = qualification.closest_existing or {}
                    record_resolution(
                        topic,
                        "routed_to_existing_content",
                        "最终资格判断为与现有内容同主意图或正文已覆盖，已路由为旧文更新线索，而非新文章主题。",
                        related_topic=str(closest_existing.get("title") or "") or None,
                    )
            else:
                record_resolution(
                    topic,
                    "database_unique_duplicate",
                    "与本轮已保存的相同规范化候选重复，未重复保存。",
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
    seed_content_items: list[dict[str, Any]] = []
    max_seed_count = max(1, min(3, int(budgets["serpapi"])))
    cooldown = _previous_run_cooldown(active_settings, site_id)
    cooled_clusters = set(str(value) for value in cooldown["clusters"])
    filters = {
        "language": active_settings.serpapi_language,
        "country": active_settings.serpapi_country,
        "intent": "mixed",
        "experience": "mixed",
        "season": "all",
        "cooldown": cooldown,
        "semantic_cluster_checklist": [cluster for cluster, _signals in SEMANTIC_CLUSTER_CHECKLIST],
        "source_observation_policy": {
            "families": list(
                MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["source_observation_families"]
            ),
            "public_language_is": "market proxy only; never first-party feedback",
            "continuation": "unconsumed source observations are preferred as next-run seeds",
        },
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
                0
                if (
                    _task_card_count(str(item["topic_key"]))
                    and memory_counts.get(item["id"], 0) < _task_card_count(str(item["topic_key"]))
                )
                else 1,
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
        with connection(active_settings) as conn:
            prior_query_rows = conn.execute(
                """
                SELECT normalized_query FROM topic_research_memory
                WHERE site_id = ? AND topic_id = ?
                ORDER BY id DESC LIMIT 80
                """,
                (site_id, selected_topic["id"]),
            ).fetchall()
        prior_queries = {str(row["normalized_query"]) for row in prior_query_rows}
        label = str(selected_topic["research_label"])
        seed_content_items = _content_search_items(active_settings, site_id)
        dimension_map = {key: (name, hint) for key, name, hint in BOUNDARY_DIMENSIONS}
        available_task_dimensions = _available_task_card_dimensions(
            str(selected_topic["topic_key"]), prior_queries
        )
        if resolved_type == "topic_gap":
            if dimension_key not in available_task_dimensions:
                dimension_key = next(
                    (
                        key
                        for key, _name, _hint in BOUNDARY_DIMENSIONS
                        if key in available_task_dimensions
                    ),
                    None,
                )
            if dimension_key:
                filters["dimension_label"] = dimension_map[dimension_key][0]
            seeds = _natural_hypothesis_seeds(
                str(selected_topic["topic_key"]),
                label,
                dimension_key=dimension_key,
                prior_queries=prior_queries,
                content_items=seed_content_items,
                max_seed_count=max_seed_count,
                settings=active_settings,
                site_id=site_id,
                topic_id=int(selected_topic["id"]),
                cooled_clusters=cooled_clusters,
            )
        else:
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
                    key=lambda key: (
                        0 if key in available_task_dimensions else 1,
                        dimension_counts.get(key, 0),
                        list(dimension_map).index(key),
                    ),
                )
            dimension_name, hint = dimension_map[dimension_key]
            filters["dimension_label"] = dimension_name
            seeds = _natural_hypothesis_seeds(
                str(selected_topic["topic_key"]),
                label,
                dimension_key=dimension_key,
                prior_queries=prior_queries,
                content_items=seed_content_items,
                max_seed_count=max_seed_count,
                settings=active_settings,
                site_id=site_id,
                topic_id=int(selected_topic["id"]),
                cooled_clusters=cooled_clusters,
            )
        if not seeds:
            raise ResearchUnavailable("该方向近期已验证过；请选择其他主题或边界维度")

    open_scheduler_required_tavily = len(OPEN_SCHEDULER_FAMILIES) + int(
        MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["open_scheduler_selected_seed_count"]
    )
    open_scheduler_active = bool(
        resolved_type in {"topic_gap", "boundary"}
        and selected_topic
        and budgets["tavily"] >= open_scheduler_required_tavily
        and budgets["ai"] >= 2
        and active_settings.tavily_api_key
        and (active_settings.ai_enabled or ai_provider is not None)
    )
    filters["open_scheduler"] = {
        "active": open_scheduler_active,
        "baseline": "open_scheduler_prototype_v2",
        "only_change": "previous_completed_run_soft_cooldown",
        "family_count": len(OPEN_SCHEDULER_FAMILIES),
        "selected_for_deep_expansion": int(
            MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["open_scheduler_selected_seed_count"]
        ),
        "all_distinct_seed_proposals_remain_visible": True,
    }

    for seed in seeds:
        seed["semantic_cluster"] = _semantic_seed(seed)
        seed["anchor_fit"] = str(seed.get("anchor_fit") or "core")
        seed["cooldown_status"] = (
            "cooled_soft_priority"
            if str(seed["semantic_cluster"]) in cooled_clusters
            else "normal_priority"
        )
    seed_queries = [str(seed["target_ref"]) for seed in seeds]
    filters["seed_method"] = (
        "open_scheduler_v2_family_observations_then_all_seed_proposals_then_five_deep_expansions"
        if open_scheduler_active
        else "source_observation_frontier_then_old_plan_task_cards_then_evidence_then_ai_packaging"
    )
    filters["seed_sources"] = [
        str(
            seed.get("seed_source")
            or ("gsc_gap" if resolved_type == "gsc" else "operator_selected")
        )
        for seed in seeds
    ]
    filters["seed_frontier"] = [
        {
            "query": str(seed["target_ref"]),
            "source_type": str(seed.get("seed_source") or ""),
            "semantic_cluster": str(seed["semantic_cluster"]),
            "anchor_fit": str(seed["anchor_fit"]),
            "cooldown_status": str(seed["cooldown_status"]),
            "source_observation_id": seed.get("source_observation_id"),
            "source_url": (
                str((seed.get("task_card") or {}).get("source_url") or "") or None
                if isinstance(seed.get("task_card"), dict)
                else None
            ),
            "source_quote": (
                str((seed.get("task_card") or {}).get("source_quote") or "") or None
                if isinstance(seed.get("task_card"), dict)
                else None
            ),
        }
        for seed in seeds
    ]
    filters["seed_cards"] = [
        {
            "query": str(seed["target_ref"]),
            "source_type": str(seed.get("seed_source") or ""),
            "card": dict(seed["task_card"]),
        }
        for seed in seeds
        if isinstance(seed.get("task_card"), dict)
    ]
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
    seed_pool_candidates: list[dict[str, Any]] = []
    provider_failures = 0

    try:
        if open_scheduler_active and selected_topic:
            direction = str(selected_topic["research_label"])
            family_specs = _open_scheduler_family_specs(direction)
            for spec in family_specs:
                result = await execute_tavily_search(
                    active_settings,
                    site=site,
                    opportunity_id=None,
                    query=str(spec["query"]),
                    input_refs=_unique(
                        [
                            str(ref)
                            for seed in seeds
                            for ref in (seed.get("evidence") or {}).get("evidence_ids", [])
                        ],
                        20,
                    ),
                    transport=transport,
                )
                _record_external_result(active_settings, research_run_id, "tavily", result, usage)
                if result.status == "success" and result.evidence_id:
                    evidence[result.evidence_id] = {
                        "provider": "tavily",
                        "purpose": result.purpose,
                        "payload": result.payload or {},
                        "opportunity_id": None,
                        "input_refs": [],
                        "planned_source_kind": str(spec["planned_source_kind"]),
                        "open_scheduler_family": str(spec["family"]),
                    }
                else:
                    provider_failures += 1

            initial_observations = _raw_source_observations(evidence)
            usage["ai"]["actual_requests"] += 1
            source_seed_proposals, proposal_model, proposal_ok = await _ai_seed_proposals(
                active_settings,
                site_id=site_id,
                direction=direction,
                source_observations=initial_observations,
                plan_seeds=seeds,
                content_items=seed_content_items,
                cooldown=cooldown,
                provider=ai_provider,
            )
            usage["ai"]["successful" if proposal_ok else "failed"] += 1
            if proposal_model:
                active_ai_model = proposal_model
            else:
                active_ai_model = active_settings.ai_model
            if not proposal_ok:
                provider_failures += 1

            viable_proposals: list[dict[str, Any]] = []
            cms_duplicate_count = 0
            cms_duplicate_seed_audit: list[dict[str, Any]] = []
            for proposal in source_seed_proposals:
                hypothesis = proposal.get("hypothesis") or {}
                topic = str(hypothesis.get("topic") or "")
                assessment = assess_discovered_topic(topic, seed_content_items)
                if assessment.gate_status == "blocked":
                    cms_duplicate_count += 1
                    cms_duplicate_seed_audit.append(
                        _cms_duplicate_seed_audit_entry(topic, assessment)
                    )
                    continue
                viable_proposals.append(proposal)
                refs = list((proposal.get("evidence") or {}).get("evidence_ids", []))
                seed_pool_candidates.append(
                    {
                        "topic": topic,
                        "intent": str(hypothesis.get("intent") or ""),
                        "rationale": (
                            "Open-scheduler source seed: "
                            f"{str(proposal.get('rationale') or 'public-source observation')[:900]}"
                        ),
                        "evidence_refs": refs,
                        "facts": [
                            f"{ref} contains the public-source observation used for this seed"
                            for ref in refs
                        ],
                        "inference": [
                            f"Open scheduler family: {proposal.get('source_family') or 'emerging'}.",
                            "This source seed remains visible even when it is not selected for deeper API expansion.",
                        ],
                        "seed_anchor_fit": str(proposal.get("anchor_fit") or "core"),
                        "semantic_cluster": str(proposal.get("semantic_cluster") or "emerging"),
                        "source_support": "snippet_supported",
                        "candidate_stage": "synthesized_angle",
                    }
                )

            plan_candidates = []
            for order, seed in enumerate(seeds, start=len(viable_proposals)):
                plan_seed = dict(seed)
                plan_seed["_frontier_order"] = order
                plan_candidates.append(plan_seed)
            selected_limit = int(
                MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["open_scheduler_selected_seed_count"]
            )
            expanded_seeds = _select_diverse_seed_frontier(
                [*viable_proposals, *plan_candidates],
                cooled_clusters=cooled_clusters,
                limit=selected_limit,
            )
            if expanded_seeds:
                seeds = expanded_seeds
                for seed in seeds:
                    seed["semantic_cluster"] = _semantic_seed(seed)
                    seed["anchor_fit"] = str(seed.get("anchor_fit") or "core")
                    seed["cooldown_status"] = (
                        "cooled_soft_priority"
                        if str(seed["semantic_cluster"]) in cooled_clusters
                        else "normal_priority"
                    )
                seed_queries = [str(seed["target_ref"]) for seed in seeds]
                filters["seed_frontier"] = [
                    {
                        "query": str(seed["target_ref"]),
                        "source_type": str(seed.get("seed_source") or ""),
                        "source_family": str(seed.get("source_family") or "emerging"),
                        "semantic_cluster": str(seed["semantic_cluster"]),
                        "anchor_fit": str(seed["anchor_fit"]),
                        "cooldown_status": str(seed["cooldown_status"]),
                        "source_observation_id": seed.get("source_observation_id"),
                    }
                    for seed in seeds
                ]
            filters["open_scheduler"].update(
                {
                    "initial_observation_count": len(initial_observations),
                    "seed_proposal_count": len(source_seed_proposals),
                    "cms_duplicate_seed_count": cms_duplicate_count,
                    "cms_duplicate_seed_audit": cms_duplicate_seed_audit,
                    "visible_distinct_seed_count": len(seed_pool_candidates),
                    "selected_seed_count": len(seeds),
                    "families": [str(spec["family"]) for spec in family_specs],
                }
            )
        else:
            active_ai_model = active_settings.ai_model

        calls_per_seed = max(
            1,
            min(2, budgets["serpapi"] // max(1, len(seeds))),
        )
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
                    min(calls_per_seed, remaining),
                    active_settings,
                    transport=transport,
                )
            else:
                outcome = await collect_topic_query_evidence(
                    site_id,
                    str(seed["target_ref"]),
                    min(calls_per_seed, remaining),
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
                        hypothesis = seed.get("hypothesis")
                        if isinstance(hypothesis, dict) and not seed.get("discovery_only"):
                            deterministic_candidates.append(
                                {
                                    "topic": str(hypothesis.get("topic") or ""),
                                    "intent": str(hypothesis.get("intent") or ""),
                                    "rationale": "A coverage-map hypothesis is being tested against current search evidence",
                                    "evidence_refs": [result.evidence_id],
                                    "seed_query": str(seed["target_ref"]),
                                    "expand_lineage": True,
                                    "facts": [
                                        f"{result.evidence_id} stores the current SERP for this hypothesis"
                                    ],
                                    "inference": [
                                        "This is a testable hypothesis, not a demand claim until the evidence gate passes"
                                    ],
                                    "semantic_cluster": str(seed["semantic_cluster"]),
                                    "source_support": "question_only",
                                    "candidate_stage": "synthesized_angle",
                                }
                            )
                        for raw_topic in payload.get("related_questions") or []:
                            topic = str(
                                raw_topic.get("question")
                                if isinstance(raw_topic, dict)
                                else raw_topic
                            ).strip()
                            if not topic:
                                continue
                            deterministic_candidates.append(
                                {
                                    "topic": topic,
                                    "intent": (
                                        "Answer this specific user question without broadening it: "
                                        f"{topic}"
                                    ),
                                    "rationale": "The current SERP exposed this related question",
                                    "evidence_refs": [result.evidence_id],
                                    "expand_lineage": True,
                                    "facts": [
                                        f"{result.evidence_id} contains this related question"
                                    ],
                                    "inference": [
                                        "This may justify verification as an adjacent topic"
                                    ],
                                    "semantic_cluster": _semantic_cluster(
                                        topic, seed["target_ref"]
                                    ),
                                    "source_support": "question_only",
                                    "candidate_stage": "raw_lead",
                                }
                            )
                        for raw_topic in payload.get("related_searches") or []:
                            topic = str(
                                raw_topic.get("query") if isinstance(raw_topic, dict) else raw_topic
                            ).strip()
                            if not topic:
                                continue
                            deterministic_candidates.append(
                                {
                                    "topic": topic,
                                    "intent": (
                                        "Explore the specific user task expressed by this related search: "
                                        f"{topic}"
                                    ),
                                    "rationale": "The current SERP exposed this related search",
                                    "evidence_refs": [result.evidence_id],
                                    "facts": [f"{result.evidence_id} contains this related search"],
                                    "inference": [
                                        "This may justify verification as an adjacent topic"
                                    ],
                                    "semantic_cluster": _semantic_cluster(
                                        topic, seed["target_ref"]
                                    ),
                                    "source_support": "question_only",
                                    "candidate_stage": "raw_lead",
                                }
                            )
                elif result.status != "success":
                    provider_failures += 1

        demand_query_groups: list[list[str]] = []
        for item in evidence.values():
            if item["provider"] != "serpapi" or item["purpose"] != "serp_snapshot":
                continue
            payload = item["payload"]
            demand_query_groups.append(
                _unique(
                    [
                        *[
                            str(value.get("question") if isinstance(value, dict) else value)
                            for value in (payload.get("related_questions") or [])
                        ],
                        *[
                            str(value.get("query") if isinstance(value, dict) else value)
                            for value in (payload.get("related_searches") or [])
                        ],
                    ]
                )
            )
        demand_queries = _round_robin(demand_query_groups, 150)
        discovery_queries = _unique(
            (
                [*demand_queries, *seed_queries]
                if resolved_type == "gsc"
                else [*seed_queries, *demand_queries]
            ),
            150,
        )
        discovery_specs: list[dict[str, str]] = []
        # The open scheduler already collected all public-language families.
        # Its remaining Tavily budget follows the selected seeds directly;
        # unselected seed proposals stay visible and are not discarded.
        if not open_scheduler_active:
            source_probes = _source_probe_specs(seed_queries)
            if budgets["tavily"] >= len(source_probes):
                discovery_specs.extend(source_probes)
        discovery_specs.extend(
            {"query": query, "planned_source_kind": "paa_related_or_seed"}
            for query in discovery_queries
        )
        seen_discovery_queries: set[str] = set()
        for spec in discovery_specs:
            query = str(spec["query"])
            normalized_query = normalize_topic(query)
            if normalized_query in seen_discovery_queries:
                continue
            seen_discovery_queries.add(normalized_query)
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
                if (
                    query in (payload.get("related_questions") or [])
                    or query in (payload.get("related_searches") or [])
                    or query.casefold() == str(payload.get("query") or "").casefold()
                ):
                    input_refs.append(evidence_id)
            input_refs = _unique(input_refs, 20)
            result = await execute_tavily_search(
                active_settings,
                site=site,
                opportunity_id=(int(seed["id"]) if seed.get("id") is not None else None),
                query=query,
                input_refs=input_refs,
                transport=transport,
            )
            _record_external_result(active_settings, research_run_id, "tavily", result, usage)
            if result.status == "success" and result.evidence_id:
                evidence[result.evidence_id] = {
                    "provider": "tavily",
                    "purpose": result.purpose,
                    "payload": result.payload or {},
                    "opportunity_id": (int(seed["id"]) if seed.get("id") is not None else None),
                    "input_refs": input_refs,
                    "planned_source_kind": str(spec["planned_source_kind"]),
                }
                for candidate in deterministic_candidates:
                    candidate_query = str(
                        candidate.get("seed_query") or candidate.get("topic") or ""
                    )
                    if normalize_topic(candidate_query) != normalize_topic(query):
                        continue
                    candidate.setdefault("evidence_refs", []).append(result.evidence_id)
                    candidate.setdefault("facts", []).append(
                        f"{result.evidence_id} stores source discovery for this exact question"
                    )
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
                            "semantic_cluster": _semantic_cluster(query, seed["target_ref"]),
                            "source_support": "question_only",
                            "candidate_stage": "raw_lead",
                        }
                    )
            else:
                provider_failures += 1

        url_refs: dict[str, list[str]] = {}
        url_opportunity: dict[str, int | None] = {}
        url_groups: dict[int, list[list[str]]] = {0: [], 1: [], 2: []}
        for evidence_id, item in evidence.items():
            payload = item.get("payload") or {}
            lineage_refs = _unique([evidence_id, *(item.get("input_refs") or [])], 20)
            has_demand_lineage = any(
                str(ref).endswith(("serp_snapshot", "trends_timeseries", "gsc"))
                for ref in lineage_refs
            )
            priority = (
                0
                if item.get("purpose") == "source_discovery" and has_demand_lineage
                else 1
                if item.get("purpose") == "source_discovery"
                else 2
            )
            item_urls: list[str] = []
            for result in payload.get("organic_results") or payload.get("results") or []:
                if not isinstance(result, dict):
                    continue
                url = str(result.get("link") or result.get("url") or "").strip()
                if not url or _host(url) == _host(str(site["domain"])):
                    continue
                item_urls.append(url)
                url_refs.setdefault(url, []).extend(lineage_refs)
                value = item.get("opportunity_id")
                url_opportunity.setdefault(url, int(value) if value is not None else None)
            if item_urls:
                url_groups[priority].append(_unique(item_urls))
        ordered_urls = _unique(
            [url for priority in (0, 1, 2) for url in _round_robin(url_groups[priority])]
        )
        diverse_urls: list[str] = []
        deferred: list[str] = []
        seen_hosts: set[str] = set()
        for url in ordered_urls:
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
                    "input_refs": _unique(url_refs[url], 20),
                }
            else:
                provider_failures += 1

        _expand_candidate_lineage(deterministic_candidates, evidence)
        content_items = seed_content_items or _content_search_items(active_settings, site_id)
        with connection(active_settings) as conn:
            feedback_rows = conn.execute(
                """
                SELECT normalized_topic, decision FROM topic_decisions
                WHERE site_id = ? AND decision IN ('do', 'dont_recommend')
                ORDER BY updated_at DESC LIMIT 40
                """,
                (site_id,),
            ).fetchall()
        seed_context = {
            "core_object": "laser pointer",
            "seed_type": resolved_type,
            "selected_branch": (
                {
                    "topic_key": str(selected_topic["topic_key"]),
                    "label": str(selected_topic["research_label"]),
                }
                if selected_topic
                else None
            ),
            "boundary_dimension": (
                {"key": dimension_key, "label": filters.get("dimension_label")}
                if dimension_key
                else None
            ),
            "seeds": [
                {
                    "query": str(seed["target_ref"]),
                    "source_type": str(
                        seed.get("seed_source")
                        or ("gsc_gap" if resolved_type == "gsc" else "operator_selected")
                    ),
                    "semantic_cluster": str(seed["semantic_cluster"]),
                    "cooldown_status": str(seed["cooldown_status"]),
                    "anchor_fit": str(seed["anchor_fit"]),
                    "source_family": str(seed.get("source_family") or "emerging"),
                    "hypothesis": seed.get("hypothesis"),
                    **(
                        {
                            "source_observation": {
                                "id": seed.get("source_observation_id"),
                                "url": (seed.get("task_card") or {}).get("source_url"),
                                "quote": (seed.get("task_card") or {}).get("source_quote"),
                                "evidence_id": (seed.get("task_card") or {}).get("evidence_ref"),
                            }
                        }
                        if seed.get("source_observation_id") is not None
                        else {}
                    ),
                    **(
                        {"task_card": dict(seed["task_card"])}
                        if isinstance(seed.get("task_card"), dict)
                        else {}
                    ),
                }
                for seed in seeds
            ],
            "method": (
                "open scheduler v2 family observations -> all distinct source seeds remain visible "
                "-> source/cluster-balanced deep expansion -> evidence-bound topic packaging; "
                "legacy Plan task cards remain in the same frontier"
                if open_scheduler_active
                else "deterministic seed selection from source observations + old Plan task cards/question chains "
                "-> SERP/PAA/source/page expansion -> evidence-bound topic packaging"
            ),
            "signal_policy": (
                "Sparse niche-market signals remain eligible; signal strength affects confidence "
                "and priority, never duplicate-only qualification."
            ),
            "source_policy": (
                "public_third_party_language_probe is public market-proxy language from other "
                "sites, forums, reviews, social/video, commercial pages or Q&A; it is never "
                "first-party site feedback. Source observations carry their URL, quote and evidence "
                "ID into the next seed pool. A task_card is a planning hypothesis, never proof of "
                "role use, demand, or writing readiness."
            ),
            "cooldown": cooldown,
            "feedback": {
                "prefer_topics": [
                    str(row["normalized_topic"]) for row in feedback_rows if row["decision"] == "do"
                ][:12],
                "avoid_topics": [
                    str(row["normalized_topic"])
                    for row in feedback_rows
                    if row["decision"] == "dont_recommend"
                ][:12],
            },
        }
        ai_candidates: list[dict[str, Any]] = []
        ai_source_observations: list[dict[str, Any]] = []
        ai_model = active_ai_model
        raw_source_observations = _raw_source_observations(evidence)
        remaining_ai_requests = max(0, budgets["ai"] - usage["ai"]["actual_requests"])
        if remaining_ai_requests > 0 and (active_settings.ai_enabled or ai_provider is not None):
            (
                ai_candidates,
                ai_source_observations,
                ai_model,
                ai_stats,
            ) = await _ai_selected_seed_pool(
                active_settings,
                site_id=site_id,
                research_run_id=research_run_id,
                evidence=evidence,
                content_items=content_items,
                seed_context=seed_context,
                seeds=seeds,
                provider=ai_provider,
                max_requests=remaining_ai_requests,
            )
            for key, value in ai_stats.items():
                usage["ai"][key] += value
            provider_failures += ai_stats["failed"]
        elif remaining_ai_requests > 0:
            provider_failures += 1
            usage["ai"]["failed"] += 1
        _expand_candidate_lineage(ai_candidates, evidence)

        # Raw PAA/result language is retained even if AI is unavailable; when
        # AI did return a quote-validated task card, store it first so the
        # richer audit record wins over a duplicate raw discovery observation.
        source_observation_count = _store_source_observations(
            active_settings,
            site_id=site_id,
            research_run_id=research_run_id,
            topic_id=selected_topic["id"] if selected_topic else None,
            dimension_key=dimension_key,
            observations=[*ai_source_observations, *raw_source_observations],
        )

        candidate_pool = [*ai_candidates, *seed_pool_candidates, *deterministic_candidates]
        clusterable_count = sum(
            1
            for candidate in candidate_pool
            if str(candidate.get("candidate_stage") or "synthesized_angle") != "raw_lead"
            and not str(candidate.get("rationale") or "").startswith("Raw source lead:")
        )
        remaining_ai_requests = max(0, budgets["ai"] - usage["ai"]["actual_requests"])
        if open_scheduler_active and remaining_ai_requests > 0 and clusterable_count >= 2:
            usage["ai"]["actual_requests"] += 1
            (
                candidate_pool,
                cluster_model,
                cluster_ok,
                cluster_audit,
            ) = await _ai_intent_deduplicate_pool(
                active_settings,
                site_id=site_id,
                research_run_id=research_run_id,
                candidates=candidate_pool,
                provider=ai_provider,
            )
            usage["ai"]["successful" if cluster_ok else "failed"] += 1
            if cluster_model:
                ai_model = cluster_model
            if not cluster_ok:
                provider_failures += 1
            filters["open_scheduler"]["intent_clustering"] = cluster_audit
        elif open_scheduler_active:
            filters["open_scheduler"]["intent_clustering"] = {
                "input_synthesized": clusterable_count,
                "same_intent_clusters": 0,
                "merged_current_variants": 0,
                "prior_pool_duplicates": 0,
                "output_candidates": len(candidate_pool),
                "status": "not_run_no_remaining_ai_budget_or_too_few_candidates",
            }

        candidate_resolution_audit: list[dict[str, Any]] = []
        candidate_count = _insert_candidates(
            active_settings,
            research_run_id=research_run_id,
            site_id=site_id,
            candidates=candidate_pool,
            evidence=evidence,
            content_items=content_items,
            suggested_parent_topic_id=selected_topic["id"] if selected_topic else None,
            research_label=(str(selected_topic["research_label"]) if selected_topic else None),
            resolution_audit=candidate_resolution_audit,
        )
        external_successes = sum(
            usage[name]["successful"] for name in ("serpapi", "firecrawl", "tavily")
        )
        external_attempts = sum(
            usage[name]["actual_requests"] for name in ("serpapi", "firecrawl", "tavily")
        )
        with connection(active_settings) as conn:
            formed_topic_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM research_candidates
                    WHERE research_run_id = ?
                      AND qualification_status = 'qualified'
                      AND recommended_disposition = 'new_article'
                      AND rationale NOT LIKE 'Raw source lead:%'
                      AND inference_json NOT LIKE '%Seed anchor fit: off_anchor.%'
                    """,
                    (research_run_id,),
                ).fetchone()[0]
            )
            raw_lead_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM research_candidates
                    WHERE research_run_id = ? AND rationale LIKE 'Raw source lead:%'
                    """,
                    (research_run_id,),
                ).fetchone()[0]
            )
        if external_successes == 0 and external_attempts > 0:
            status = "failed"
        elif provider_failures or candidate_count == 0:
            status = "partial"
        else:
            status = "success"
        selected_clusters = [str(seed["semantic_cluster"]) for seed in seeds if seed]
        cluster_counts = {
            cluster: selected_clusters.count(cluster) for cluster in _unique(selected_clusters)
        }
        minimum = int(MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["cooldown_dominant_seed_minimum"])
        minimum_share = float(
            MULTI_SOURCE_TOPIC_RESEARCH_RULE["config"]["cooldown_dominant_seed_share"]
        )
        dominant_clusters = [
            cluster
            for cluster, count in cluster_counts.items()
            if count >= minimum
            and bool(selected_clusters)
            and count / len(selected_clusters) >= minimum_share
        ]
        selected_observation_ids = [
            int(seed["source_observation_id"])
            for seed in seeds
            if seed.get("source_observation_id") is not None
        ]
        filters["dominant_semantic_clusters"] = dominant_clusters
        filters["dominant_cluster_basis"] = {
            "selected_seed_count": len(selected_clusters),
            "counts": cluster_counts,
            "minimum_seed_count": minimum,
            "minimum_share": minimum_share,
        }
        filters["source_observations"] = {
            "stored": source_observation_count,
            "selected_from_prior_runs": len(selected_observation_ids),
            "selected_ids": selected_observation_ids,
        }
        filters["topic_pool"] = {
            "formed_independent_topics": formed_topic_count,
            "raw_leads": raw_lead_count,
            "policy": "same-direction detail is retained when primary intent differs",
        }
        filters["candidate_resolution_audit"] = candidate_resolution_audit
        with connection(active_settings) as conn:
            if selected_observation_ids and external_successes:
                marks = ",".join("?" for _ in selected_observation_ids)
                conn.execute(
                    f"""
                    UPDATE research_seed_observations
                    SET status = 'consumed', consumed_by_run_id = ?, consumed_at = ?
                    WHERE site_id = ? AND status = 'observed' AND id IN ({marks})
                    """,
                    (research_run_id, utc_now(), site_id, *selected_observation_ids),
                )
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
                    error_message = ?, completed_at = ?, filters_json = ?, seed_queries_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    json_dumps(usage),
                    candidate_count,
                    ai_model,
                    "部分供应商失败或没有形成候选" if status == "partial" else None,
                    utc_now(),
                    json_dumps(filters),
                    json_dumps(seed_queries),
                    research_run_id,
                ),
            )
        message = (
            f"外部调研完成：实际请求 SerpAPI {usage['serpapi']['actual_requests']}、"
            f"Firecrawl {usage['firecrawl']['actual_requests']}、"
            f"Tavily {usage['tavily']['actual_requests']}、AI {usage['ai']['actual_requests']}；"
            f"形成 {formed_topic_count} 个独立可选主题、{raw_lead_count} 个原始线索；"
            f"共保存 {candidate_count} 个候选和 {source_observation_count} 条可续用来源观察。"
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
        ) VALUES(?, 'topic-research-0.7.1', ?, 'success', ?, ?, ?)
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
        "主意图不同，且 CMS 正文未覆盖该任务；需求与材料状态仅作写作准备提示",
    ]
    recommended_action = (
        "制作一篇与现有文章主意图不同的新文章；写作前先补齐可追溯素材，"
        "再完成自然内链、可核验外链和发布前自审。"
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
                    priority = ?, method_version = ?
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
                    RESEARCH_METHOD_VERSION,
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
                     'passed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                RESEARCH_METHOD_VERSION,
                now,
            ),
        )
        opportunity_id = int(cursor.lastrowid)
    rebalance_site_portfolio(conn, int(row["site_id"]), analysis_run_id)
    return opportunity_id


def _upsert_research_existing_opportunity(conn, row, now: str) -> int | None:
    """Expose a proven same-intent research lead in the existing-article lane.

    This is an external-research-to-content mapping, not a claimed GSC
    query-to-page association. The target page comes from the documented CMS
    intent comparison stored on the candidate.
    """

    closest = json_loads(row["closest_existing_json"], {})
    target_ref = str(closest.get("url") or "").strip()
    if not target_ref:
        return None
    refs = [str(value) for value in json_loads(row["evidence_refs_json"], []) if value]
    urls = [str(value) for value in json_loads(row["source_urls_json"], []) if value]
    if not refs:
        return None
    analysis_run_id = _ensure_research_opportunity_run(conn, int(row["site_id"]), now)
    evidence = {
        "evidence_ids": refs,
        "research_run_id": int(row["research_run_id"]),
        "research_candidate_id": int(row["id"]),
        "topic": row["topic"],
        "intent": row["intent"],
        "facts": json_loads(row["facts_json"], []),
        "source_urls": urls,
        "closest_existing": closest,
        "limitations": [
            "该建议基于外部需求材料和 CMS 意图比对，不声称存在本站 GSC query-to-page 归属。"
        ],
    }
    action = "在保留原 Slug 和主要意图的前提下，补充该调研主题对应的准确段落、证据和自然内链。"
    gate_reasons = [
        "外部调研已提供需求和写作材料",
        "候选与现有文章为同一主要意图，应更新旧页而不是创建新 URL",
        "页面映射来自 CMS 内容比对，不是猜测 GSC 查询归属",
    ]
    existing = conn.execute(
        """
        SELECT id, status FROM opportunities
        WHERE site_id = ? AND rule_key = 'research_existing_content_gap' AND target_ref = ?
        ORDER BY id DESC LIMIT 1
        """,
        (row["site_id"], target_ref),
    ).fetchone()
    if existing:
        opportunity_id = int(existing["id"])
        if existing["status"] == "proposed":
            conn.execute(
                """
                UPDATE opportunities
                SET analysis_run_id = ?, title = ?, recommended_action = ?, gate_status = 'passed',
                    gate_reasons_json = ?, evidence_json = ?, strength = ?, confidence = ?,
                    confidence_weight = ?, effort = ?, priority = ?, method_version = ?
                WHERE id = ?
                """,
                (
                    analysis_run_id,
                    str(closest.get("title") or row["topic"]),
                    action,
                    json_dumps(gate_reasons),
                    json_dumps(evidence),
                    62.0,
                    "medium",
                    0.7,
                    3.0,
                    14.5,
                    RESEARCH_METHOD_VERSION,
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
            ) VALUES(?, ?, 'research_existing_content_gap', 'optimize', 'blog', ?, ?, ?,
                     'passed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_run_id,
                row["site_id"],
                target_ref,
                str(closest.get("title") or row["topic"]),
                action,
                json_dumps(gate_reasons),
                json_dumps(evidence),
                62.0,
                "medium",
                0.7,
                3.0,
                14.5,
                RESEARCH_METHOD_VERSION,
                now,
            ),
        )
        opportunity_id = int(cursor.lastrowid)
    rebalance_site_portfolio(conn, int(row["site_id"]), analysis_run_id)
    return opportunity_id


def _load_qualification_inputs(
    settings: Settings, candidate_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a candidate row plus the current CMS content items needed to qualify it."""
    with connection(settings) as conn:
        row = conn.execute(
            "SELECT * FROM research_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            raise ResearchDecisionError("主题候选不存在")
        candidate = dict(row)
        if candidate.get("suggested_parent_topic_id") is not None:
            topic_row = conn.execute(
                "SELECT topic_key FROM topic_nodes WHERE id = ?",
                (candidate["suggested_parent_topic_id"],),
            ).fetchone()
            if topic_row:
                candidate["_research_label"] = RESEARCH_LABELS.get(str(topic_row["topic_key"]))
    content_items = _content_search_items(settings, int(candidate["site_id"]))
    return candidate, content_items


def _write_qualification_result(
    conn,
    candidate_id: int,
    qualification: Any,
    *,
    cms_fingerprint: str,
    evidence_fingerprint: str,
    now: str,
) -> None:
    """Persist a freshly computed qualification result on the candidate row."""
    conn.execute(
        """
        UPDATE research_candidates
        SET qualification_status = ?,
            qualification_version = ?,
            cms_fingerprint = ?,
            evidence_fingerprint = ?,
            closest_existing_json = ?,
            evidence_demand_json = ?,
            evidence_gap_json = ?,
            evidence_material_json = ?,
            recommended_disposition = ?,
            recommended_next_step = ?,
            limitations_json = ?,
            qualified_at = ?,
            stale_after = NULL
        WHERE id = ?
        """,
        (
            qualification.qualification_status,
            QUALIFICATION_RULE_VERSION,
            cms_fingerprint,
            evidence_fingerprint,
            json_dumps(qualification.closest_existing),
            json_dumps(qualification.evidence_demand),
            json_dumps(qualification.evidence_gap),
            json_dumps(qualification.evidence_material),
            qualification.recommended_disposition,
            qualification.next_step,
            json_dumps(qualification.limitations),
            now if qualification.qualification_status == "qualified" else None,
            candidate_id,
        ),
    )


def requalify_candidate(
    candidate_id: int,
    settings: Settings | None = None,
    *,
    human_review_reason: str | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Recompute a candidate's qualification gate against the current CMS snapshot.

    The original gate_status column is preserved for audit. The new state lives in
    qualification_status. The previous assessment is saved in
    previous_assessment_json so the operator can see how the verdict changed.

    Returns (qualification_status, recommended_disposition, closest_existing).
    """
    active_settings = settings or get_settings()
    candidate, content_items = _load_qualification_inputs(active_settings, candidate_id)
    refs = [str(v) for v in json_loads(candidate["evidence_refs_json"], []) if v]
    urls = [str(v) for v in json_loads(candidate["source_urls_json"], []) if v]
    facts = json_loads(candidate["facts_json"], [])
    seed_anchor_fit, seed_anchor_note = _seed_anchor_from_candidate(candidate)
    qualification = qualify_candidate(
        topic=str(candidate["topic"]),
        intent=str(candidate["intent"] or ""),
        facts=facts,
        source_urls=urls,
        evidence_ids=refs,
        content_items=content_items,
        human_review_reason=human_review_reason,
        research_label=candidate.get("_research_label"),
        seed_anchor_fit=seed_anchor_fit,
        seed_anchor_note=seed_anchor_note,
    )
    cms_fingerprint = compute_cms_fingerprint(content_items)
    evidence_fingerprint = compute_evidence_fingerprint(refs, urls)
    now = utc_now()
    with connection(active_settings) as conn:
        previous_snapshot = {
            "qualification_status": candidate.get("qualification_status"),
            "qualification_version": candidate.get("qualification_version"),
            "cms_fingerprint": candidate.get("cms_fingerprint"),
            "evidence_fingerprint": candidate.get("evidence_fingerprint"),
            "closest_existing": json_loads(candidate.get("closest_existing_json"), {}),
            "recommended_disposition": candidate.get("recommended_disposition"),
        }
        conn.execute(
            """
            UPDATE research_candidates
            SET previous_assessment_json = ?
            WHERE id = ? AND previous_assessment_json IS NULL
            """,
            (json_dumps(previous_snapshot), candidate_id),
        )
        _write_qualification_result(
            conn,
            candidate_id,
            qualification,
            cms_fingerprint=cms_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
            now=now,
        )
    return (
        qualification.qualification_status,
        qualification.recommended_disposition,
        qualification.closest_existing,
    )


def promote_to_opportunity(
    candidate_id: int,
    settings: Settings | None = None,
) -> int:
    """Create the article suggestion only after qualification_status='qualified'.

    Per design lock point #3, promote_to_opportunity refuses candidates that
    are not qualified and refuses candidates whose CMS fingerprint or rule
    version has changed since qualification (i.e. stale). The opportunity
    insert and the state transition are committed in the same transaction
    so an old needs_evidence state cannot slip through to 'passed'.
    """
    active_settings = settings or get_settings()
    with connection(active_settings) as conn:
        row = conn.execute(
            "SELECT * FROM research_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            raise ResearchDecisionError("主题候选不存在")
        if row["decision"] != "pending":
            raise ResearchDecisionError("这个主题已经处理过，不需要重复操作")
        qualification_status = str(row["qualification_status"] or "stale")
        if qualification_status != "qualified":
            raise ResearchDecisionError(
                "候选尚未通过资格判定，不能直接加入新文章建议；当前状态: " + qualification_status
            )
        if str(row["qualification_version"] or "") != QUALIFICATION_RULE_VERSION:
            raise ResearchDecisionError("资格规则版本已变化，请先重新跑资格判定后再加入建议")
        # Verify the CMS fingerprint still matches the live snapshot; otherwise
        # the operator must requalify before promoting.
        content_items = _content_search_items(active_settings, int(row["site_id"]))
        live_fingerprint = compute_cms_fingerprint(content_items)
        if str(row["cms_fingerprint"] or "") != live_fingerprint:
            conn.execute(
                "UPDATE research_candidates SET qualification_status = 'stale', "
                "stale_after = ? WHERE id = ?",
                (utc_now(), candidate_id),
            )
            raise ResearchDecisionError(
                "站点内容自资格判定以来发生变化，请先重新跑资格判定后再加入建议"
            )
        now = utc_now()
        opportunity_id = _upsert_research_opportunity(conn, row, now)
        return opportunity_id


def record_human_review(
    candidate_id: int,
    review_decision: str,
    reason: str,
    settings: Settings | None = None,
) -> str:
    """Migrate a legacy ambiguity review through the current duplicate-only rule.

    A confirmed body-covered topic is blocked. Independent and indeterminate
    reviews are retained unless deterministic comparison proves duplication.
    """
    active_settings = settings or get_settings()
    valid_decisions = {"independent", "covered_existing", "cannot_determine"}
    if review_decision not in valid_decisions:
        raise ResearchDecisionError("人工复核决定必须是独立主题、旧文已覆盖或无法确定之一")
    reason_clean = (reason or "").strip()
    if not reason_clean:
        raise ResearchDecisionError("人工复核必须填写理由")
    candidate, content_items = _load_qualification_inputs(active_settings, candidate_id)
    if str(candidate.get("qualification_status") or "") != "needs_human_review":
        raise ResearchDecisionError("只有旧规则遗留的人工复核候选才需要此操作")

    refs = [str(v) for v in json_loads(candidate["evidence_refs_json"], []) if v]
    urls = [str(v) for v in json_loads(candidate["source_urls_json"], []) if v]
    facts = json_loads(candidate["facts_json"], [])
    seed_anchor_fit, seed_anchor_note = _seed_anchor_from_candidate(candidate)
    relationship_override = {
        "independent": RELATIONSHIP_DISTINCT,
        "covered_existing": RELATIONSHIP_COVERED_SUBTOPIC,
        "cannot_determine": None,
    }[review_decision]
    qualification = qualify_candidate(
        topic=str(candidate["topic"]),
        intent=str(candidate["intent"] or ""),
        facts=facts,
        source_urls=urls,
        evidence_ids=refs,
        content_items=content_items,
        human_review_reason=reason_clean,
        human_relationship=relationship_override,
        research_label=candidate.get("_research_label"),
        seed_anchor_fit=seed_anchor_fit,
        seed_anchor_note=seed_anchor_note,
    )
    cms_fingerprint = compute_cms_fingerprint(content_items)
    evidence_fingerprint = compute_evidence_fingerprint(refs, urls)
    now = utc_now()
    with connection(active_settings) as conn:
        _write_qualification_result(
            conn,
            candidate_id,
            qualification,
            cms_fingerprint=cms_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
            now=now,
        )
        conn.execute(
            """
            UPDATE research_candidates
            SET human_review_reason = ?, human_reviewed_at = ?
            WHERE id = ?
            """,
            (reason_clean[:500], now, candidate_id),
        )

    if review_decision == "covered_existing":
        return "已记录：现有文章正文已覆盖该主题，候选不会创建新文章"
    if review_decision == "cannot_determine":
        return "已记录：关系无法确定；因没有足够重复证据，候选按新规则予以保留"
    return "已记录：候选承担独立意图，按当前规则予以保留"


def mark_stale_due_to_cms_change(
    site_id: int,
    new_cms_fingerprint: str,
    settings: Settings | None = None,
) -> int:
    """Mark previously qualified/promoted candidates stale when CMS content changes.

    Per design lock point #6, only底层 CMS content指纹 changes trigger stale.
    sync_topic_graph alone does not. Also cancels planned actions for any
    opportunity derived from a stale candidate (published/kept work stays
    intact in history).
    """
    active_settings = settings or get_settings()
    now = utc_now()
    stale_count = 0
    with connection(active_settings) as conn:
        rows = conn.execute(
            """
            SELECT id, cms_fingerprint, qualification_status
            FROM research_candidates
            WHERE site_id = ?
              AND qualification_status = 'qualified'
              AND (cms_fingerprint IS NULL OR cms_fingerprint <> ?)
            """,
            (site_id, new_cms_fingerprint),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE research_candidates
                SET qualification_status = 'stale',
                    stale_after = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            stale_count += 1
        # Downgrade derived opportunities whose candidate is now stale
        conn.execute(
            """
            UPDATE opportunities
            SET status = 'cancelled'
            WHERE site_id = ?
              AND rule_key = 'research_topic_candidate'
              AND (
                  status = 'proposed'
                  OR (
                      status = 'accepted'
                      AND EXISTS (
                          SELECT 1 FROM actions
                          WHERE actions.opportunity_id = opportunities.id
                            AND actions.workflow_status = 'planned'
                      )
                  )
              )
              AND target_ref IN (
                  SELECT topic FROM research_candidates
                  WHERE site_id = ?
                    AND qualification_status = 'stale'
                    AND recommended_disposition = 'new_article'
              )
            """,
            (site_id, site_id),
        )
        # Cancel planned actions for the cancelled opportunities
        conn.execute(
            """
            UPDATE actions
            SET workflow_status = 'cancelled',
                decision_reason = COALESCE(decision_reason, '')
                    || ' | 候选资格失效，任务自动取消',
                completed_at = COALESCE(completed_at, ?)
            WHERE site_id = ?
              AND workflow_status = 'planned'
              AND opportunity_id IN (
                  SELECT id FROM opportunities
                  WHERE site_id = ?
                    AND rule_key = 'research_topic_candidate'
                    AND status = 'cancelled'
              )
            """,
            (now, site_id, site_id),
        )
        if stale_count:
            analysis = latest_analysis_run(conn, site_id)
            if analysis:
                rebalance_site_portfolio(conn, site_id, int(analysis["id"]))
    return stale_count


def requalify_site_candidates(
    site_id: int,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Re-run pending candidate gates after a CMS import without external calls."""

    active_settings = settings or get_settings()
    content_items = _content_search_items(active_settings, site_id)
    fingerprint = compute_cms_fingerprint(content_items)
    mark_stale_due_to_cms_change(site_id, fingerprint, active_settings)
    with connection(active_settings) as conn:
        rows = conn.execute(
            """
            SELECT id FROM research_candidates
            WHERE site_id = ? AND decision = 'pending'
              AND (
                  qualification_status = 'stale'
                  OR qualification_status IN ('needs_evidence', 'needs_human_review')
                  OR qualification_version IS NULL
                  OR qualification_version <> ?
                  OR cms_fingerprint IS NULL
                  OR cms_fingerprint <> ?
              )
            ORDER BY id
            """,
            (site_id, QUALIFICATION_RULE_VERSION, fingerprint),
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        status, _, _ = requalify_candidate(int(row["id"]), active_settings)
        counts[status] = counts.get(status, 0) + 1

    # A candidate may already have been classified as update_existing before
    # this call.  Backfill every pending one, not only candidates just
    # requalified above, so a result never falls between the two article lanes.
    with connection(active_settings) as conn:
        existing_routes = conn.execute(
            """
            SELECT * FROM research_candidates
            WHERE site_id = ? AND decision = 'pending'
              AND qualification_status = 'blocked'
              AND recommended_disposition = ?
            ORDER BY id
            """,
            (site_id, DISPOSITION_UPDATE_EXISTING),
        ).fetchall()
        for candidate in existing_routes:
            _upsert_research_existing_opportunity(conn, candidate, utc_now())
    return counts


def record_research_candidate_decision(
    candidate_id: int,
    decision: str,
    reason: str = "",
    settings: Settings | None = None,
) -> str:
    """Record operator intent only.

    Per design lock point #3, `decision='do'` is the operator saying 'I want
    to do this topic'. It no longer silently promotes the candidate to a
    `passed` opportunity. The promotion now happens in
    `promote_to_opportunity()`, which is invoked inside this function within
    the same transaction so the qualification check and opportunity insert
    cannot be split by a stale state. If the candidate is not qualified, the
    `do` decision is refused.
    """
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
        if decision == "do":
            qualification_status = str(row["qualification_status"] or "stale")
            if qualification_status == "blocked":
                raise ResearchDecisionError(
                    "候选已被阻断为与现有文章重叠，不能直接标记为值得做；"
                    "若仍想处理，请转旧文更新流程。"
                )
            if qualification_status == "stale":
                raise ResearchDecisionError(
                    "候选资格判定已过期，请先在调研页重新跑资格判定后再决定"
                )
            if qualification_status == "needs_evidence":
                raise ResearchDecisionError(
                    "候选尚无可判定的主要用户任务，请补充具体 intent 后再决定。"
                )
            if qualification_status == "needs_human_review":
                raise ResearchDecisionError(
                    "候选与最接近文章意图关系不明确，必须先完成人工复核再决定。"
                )
            if qualification_status != "qualified":
                raise ResearchDecisionError(
                    f"候选当前状态 {qualification_status} 不允许接受，请先跑资格判定"
                )
            if str(row["qualification_version"] or "") != QUALIFICATION_RULE_VERSION:
                raise ResearchDecisionError("资格规则版本已变化，请先重新跑资格判定后再决定")
            content_items = _content_search_items(active_settings, int(row["site_id"]))
            live_fingerprint = compute_cms_fingerprint(content_items)
            if str(row["cms_fingerprint"] or "") != live_fingerprint:
                conn.execute(
                    "UPDATE research_candidates SET qualification_status = 'stale', "
                    "stale_after = ? WHERE id = ?",
                    (now, candidate_id),
                )
                raise ResearchDecisionError(
                    "站点内容自资格判定以来发生变化，请先重新跑资格判定后再决定"
                )
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
