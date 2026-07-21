"""Legacy Research + Write workflow service.

Wraps calls to frozen old scripts at /home/laoma/seo-workflow/data_sources/modules/
following the old research/SKILL.md and write/SKILL.md instructions exactly.

Stages (file-system state machine):
  r0_pending  → r0_prompt → r1_results → r2_collect → r3_ai_analyze
  → r4_score → r5_write_ready → w0_validate → w1_draft
  → w1b_pre_check → w2_post_process → w3_register

Each stage checks for the existence of specific workspace files.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

LEGACY_MODULES_DIR = Path("/home/laoma/seo-workflow/data_sources/modules")
LEGACY_PROJECT_ROOT = Path("/home/laoma/seo-workflow")


# ── Utilities ──────────────────────────────────────────────────────────

def _slugify(topic: str) -> str:
    s = topic.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    return re.sub(r"-+", "-", s)[:80]


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _latest_file(glob_pattern: str, directory: Path) -> Optional[Path]:
    candidates = sorted(directory.glob(glob_pattern),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _copy_research_products(workspace: Path):
    """After old scripts run in LEGACY_PROJECT_ROOT, copy generated files to workspace."""
    old = LEGACY_PROJECT_ROOT / "laserpointerhub"
    ws = workspace
    ws.mkdir(parents=True, exist_ok=True)
    for sub in ["research", "material-packs", "drafts"]:
        src = old / sub
        if not src.exists():
            continue
        dst = ws / sub
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                (dst / f.name).write_text(f.read_text(encoding="utf-8"))
    # Copy updated internal-links-map
    ilm = old / "context" / "internal-links-map.md"
    if ilm.exists():
        ws_ctx = ws / "context"
        ws_ctx.mkdir(parents=True, exist_ok=True)
        (ws_ctx / "internal-links-map.md").write_text(ilm.read_text(encoding="utf-8"))


# ── Stage Detection ────────────────────────────────────────────────────

STAGE_ORDER = [
    "r0_pending", "r0_prompt", "r1_results", "r2_collect",
    "r3_ai_analyze", "r4_score", "r5_write_ready",
    "w0_validate", "w1_draft", "w1b_pre_check",
    "w2_post_process", "w3_register",
]

STAGE_NAMES = {
    "r0_pending":    "尚未开始",
    "r0_prompt":     "搜索提示词已生成",
    "r1_results":    "搜索结果已保存",
    "r2_collect":    "数据已收集，等待 AI 分析",
    "r3_ai_analyze": "素材包不完整，等待 AI 分析",
    "r4_score":      "素材包完成，等待评分",
    "r5_write_ready":"Research 完成，等待写作",
    "w0_validate":   "素材包已校验，等待生成草稿",
    "w1_draft":      "草稿已生成，等待预检",
    "w1b_pre_check": "预检完成",
    "w2_post_process":"后处理完成，等待注册",
    "w3_register":   "已注册，全部完成",
}


def detect_stage(topic: str, workspace: Path) -> tuple[str, dict[str, Any]]:
    """Return (stage_key, files_info) by checking file existence."""
    slug = _slugify(topic)

    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    rd = _latest_file(f"research/research-data-{slug}-*.md", workspace)
    sr = _latest_file(f"research/search-results-{slug}-*.md", workspace)
    sp = _latest_file(f"research/search-prompt-{slug}-*.md", workspace)
    rs = _latest_file(f"research/research-score-{slug}-*.md", workspace)
    br = _latest_file(f"research/brief-{slug}-*.md", workspace)

    files = {
        "slug": slug, "today": _today_str(),
        "search_prompt": str(sp) if sp else None,
        "search_results": str(sr) if sr else None,
        "research_data": str(rd) if rd else None,
        "research_score": str(rs) if rs else None,
        "brief": str(br) if br else None,
        "material_pack": str(mp) if mp else None,
        "draft": str(draft) if draft else None,
    }

    # Order matters — check from most complete to least
    if draft and not mp:
        dc = draft.read_text(encoding="utf-8")
        if "内链:" in dc or "外链:" in dc:
            return "w3_register", files
        return "w1_draft", files

    if draft:
        return "w1_draft", files

    if mp:
        pc = mp.read_text(encoding="utf-8")
        if "Part 3" in pc:
            if rs:
                return "r5_write_ready", files
            return "r4_score", files
        return "r3_ai_analyze", files

    if rd:
        return "r2_collect", files

    if sr:
        return "r1_results", files

    if sp:
        return "r0_prompt", files

    return "r0_pending", files


def stage_label(stage_key: str) -> str:
    return STAGE_NAMES.get(stage_key, stage_key)


def stage_step(stage_key: str) -> int:
    try:
        return STAGE_ORDER.index(stage_key)
    except ValueError:
        return 0


# ── Legacy Runner (subprocess wrapper) ──────────────────────────────────

class LegacyRunner:
    """Runs old scripts via subprocess. Offers sync and SSE-streaming modes."""

    def __init__(self, workspace: Path, website: str = "laserpointerhub"):
        self.workspace = workspace
        self.website = website

    def run_sync(self, script_name: str, args: list[str]) -> tuple[str, str, int]:
        """Run old script, return (stdout, stderr, exit_code)."""
        script_path = LEGACY_MODULES_DIR / script_name
        cmd = [sys.executable, str(script_path)] + args
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=str(LEGACY_PROJECT_ROOT),
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return stdout, stderr, result.returncode

    async def run_stream(self, script_name: str,
                          args: list[str]) -> AsyncGenerator[str, None]:
        """Run old script, yield stdout/stderr lines as SSE events."""
        script_path = LEGACY_MODULES_DIR / script_name
        cmd = [sys.executable, str(script_path)] + args

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(LEGACY_PROJECT_ROOT),
        )

        async def _read(stream, prefix=""):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    yield f"data: {prefix}{text}\n\n"

        async for line in _read(proc.stdout):
            yield line
        async for line in _read(proc.stderr, prefix=""):
            yield line

        await proc.wait()
        yield f"data: [EXIT:{proc.returncode}]\n\n"


# ── R0: Generate Search Prompt ──────────────────────────────────────────

def _build_search_prompt(topic: str, workspace: Path) -> str:
    """Generate an 8-section search prompt using the old format, fed from workspace data.

    Section 3 replaces old "Market Data" with "Common Misconceptions and
    Real-World Lessons" to avoid AI-fabricated price/trend content.
    """
    slug = _slugify(topic)
    today = _today_str()

    # ── Read synced GSC data ──
    seo_path = workspace / "context" / "seo-data-manual.md"
    seo_text = seo_path.read_text(encoding="utf-8") if seo_path.exists() else ""
    gsc_kws: list[str] = []
    for m in re.finditer(r'\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([\d,]+)', seo_text):
        kw = m.group(1).strip()
        try:
            if int(m.group(2).replace(",", "")) > 100:
                gsc_kws.append(kw)
        except ValueError:
            pass

    # ── Read published index ──
    pub_path = workspace / "published" / "published-index.json"
    pub_titles: list[str] = []
    pub_tags: set[str] = set()
    if pub_path.exists():
        try:
            data = json.loads(pub_path.read_text(encoding="utf-8"))
            for entry in (data if isinstance(data, list) else []):
                pub_titles.append((entry.get("title") or "")[:80])
                for t in (entry.get("tags") or []):
                    pub_tags.add(t.lower())
        except Exception:
            pass

    # ── Read library summaries (so search AI knows what we already have) ──
    def _lib_summary(lib_path: Path, label: str) -> str:
        if not lib_path.exists():
            return ""
        text = lib_path.read_text(encoding="utf-8")
        entries = re.findall(r'\n## (.+)', text)
        skip = {"标签索引", "填写规则", "外链库", "常见权威", "索引", "规则"}
        real = [e.strip() for e in entries if not any(s in e.lower() for s in skip)]
        if not real:
            return ""
        return f"### Existing {label} ({len(real)} total)\n" + "\n".join(
            f"- {e[:60]}" for e in real[:5])

    pain_summ = _lib_summary(workspace / "context" / "pain-points-library.md", "User Pain Points")
    case_summ = _lib_summary(workspace / "context" / "case-studies-library.md", "Case Studies")

    def _source_summ() -> str:
        p = workspace / "context" / "external-sources-library.md"
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        urls = re.findall(r'\|\s*\d+\s*\|\s*(https?://\S+)', text)
        samples = re.findall(
            r'\|\s*(\d+)\s*\|\s*(https?://\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+)\s*\|', text)
        if not urls:
            return ""
        lines = [f"### Existing Authoritative Citations ({len(urls)} total)"]
        for s in samples[:3]:
            lines.append(f"- [{s[2]}] {s[3].strip()[:50]}")
        return "\n".join(lines)

    source_summ = _source_summ()

    # ── Intent detection ──
    topic_lower = topic.lower()
    commercial = {"best", "buy", "review", "vs", "comparison", "top", "price", "budget", "cheap",
                  "under", "roundup"}
    informational = {"how", "what", "why", "guide", "safe", "use", "work", "mean", "does"}
    if any(w in topic_lower for w in commercial):
        intent = "转化型"
    elif any(w in topic_lower for w in informational):
        intent = "信息型"
    else:
        intent = "混合型"

    # ── What We Know ──
    know: list[str] = []
    if gsc_kws:
        know.append(f"GSC keywords we rank for: {', '.join(gsc_kws[:5])}")
    if pub_titles:
        know.append(
            f"Published articles: {len(pub_titles)} total. "
            f"Topics include: {', '.join(pub_titles[:5])}")
    if pub_tags:
        know.append(f"Content tags already covered: {', '.join(sorted(pub_tags)[:10])}")

    # ── What We Need ──
    need: list[str] = []
    if not gsc_kws:
        need.append("No GSC ranking data — need SERP analysis to understand what ranks.")
    else:
        need.append("GSC shows search demand. Find what TOPIC ANGLE differentiates from top 5.")
    need.append("Focus on information gaps — what the top 5 articles do NOT cover.")
    if intent == "转化型":
        need.append("Commercial article — prioritize spec comparisons, buyer decision factors, "
                     "and red flags. Pricing data from search AI may be inaccurate; "
                     "focus on product capabilities, not market pricing.")
    else:
        need.append("Informational article — prioritize real user pain points, practical how-to, "
                     "authority sources.")

    # ── BUILD ──
    return f"""You are helping me research for an SEO article about "{topic}" on laserpointerhub.com.
I need comprehensive, real, verifiable data.
Do NOT fabricate anything — say "not found" if you cannot find it.

## What We Already Know
{chr(10).join(know)}

## What We Need You To Find
{chr(10).join(need)}

Search intent: {intent}

---

## Section 1: SERP Analysis

Search for "{topic}" and analyze the top 5 ranking pages.

| # | URL | Title | Est. Words | Content Type | H2 Sections |
|---|-----|-------|-----------|--------------|-------------|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

After the table, answer:
- Common H2 topics ALL top 5 cover (must-have — we CANNOT skip these):
- Topics only 1-2 cover (differentiation opportunities for us):
- Topics NO ONE covers (our unique angle — THIS is where we win):
- Average word count of top 5:
- SERP composition (brand blogs vs independent reviews vs forums vs gov sites):

---

## Section 2: User Pain Points

Find 8-10 REAL frustrations/complaints/questions about {topic}.
Search Reddit, Quora, forums, Amazon reviews.

{pain_summ if pain_summ else 'Important: We have a pain point library. Find NEW, different pain points — NOT the same ones we already know about.'}

Format each as:
- Pain point: [in user's own language]
- Source: [URL]
- Quote: "[actual user words]"

Prioritize specific, emotional complaints with actual numbers/experiences.

---

## Section 3: Common Misconceptions and Real-World Lessons

Find misconceptions, myths, and hard-learned lessons about {topic} from forums,
Reddit, comments, and review sections. This is NOT about market data or pricing.

Focus on:
- What beginners consistently get WRONG
- Dangerous or costly mistakes people make (with real stories)
- Advice from experienced users that contradicts common wisdom
- "I wish I knew this before I started" type of lessons

Format each as:
- Misconception/Lesson: [describe the wrong assumption or hard lesson]
- Source: [URL to the specific comment/post]
- Quote: "[actual user words showing the mistake or lesson]"
- Why it matters: [how this changes what we should tell readers]

---

## Section 4: Case Studies / Real Stories

Find 3-5 real user stories.

{case_summ if case_summ else 'We have a case study library. Find DIFFERENT, fresh cases not yet collected.'}

Format:
- Story: [what happened — specific details, names, numbers, results]
- Source: [URL]
- Use in article: [what point this illustrates]

---

## Section 5: Authoritative Citations

Find 3-5 authoritative sources (.gov, .edu, academic journals, industry standards,
official reports).

{source_summ if source_summ else 'We have a pre-verified citation library. Find NEW citations not yet collected.'}

Format:
- Type: [source type]
- Key finding: [specific statistic or data point]
- Source URL:
- Why authoritative: [one sentence]

---

## Section 6: Information Gaps

**This is the most critical section.** Our article must be DIFFERENT.

Be SPECIFIC — name which competitor article misses which topic:
- Topics the top 5 all miss or cover poorly:
- Outdated or wrong information in current top results:
- Unique angle we can bring:
- What single piece of information would make our article definitive:

No generic "go deeper" or "add more detail." Name specific facts or angles.

---

## Section 7: PAA Questions

List 15+ "People Also Ask" questions related to {topic}. Group by subtopic.

---

## Section 8: People Also Ask (Structured)

For "{topic}" and 5-8 closely related queries, capture REAL PAA boxes.
Use VERBATIM phrasing — do NOT paraphrase. 10-15 entries:

- **[search] <PAA question verbatim from SERP>**
- Source: [URL or "search AI"]
- Answer hint: brief answer context (1-2 sentences)

Each question from real PAA box, Reddit, or Quora.
NO template questions like "What is X?" unless literally in SERP.
Include SERP source URL when possible.
Deduplicate identical questions across queries."""


def stage_r0_generate_prompt(topic: str, workspace: Path) -> dict:
    """Generate and save the search prompt."""
    slug = _slugify(topic)
    today = _today_str()
    prompt = _build_search_prompt(topic, workspace)
    out = workspace / "research" / f"search-prompt-{slug}-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    return {
        "success": True, "stage": "r0_prompt",
        "prompt_file": str(out), "prompt_content": prompt,
    }


# ── R1: Save Search Results + Run Collect ───────────────────────────────

def stage_r1_save_and_collect(topic: str, search_text: str,
                               workspace: Path) -> dict:
    """Save user-pasted search results and run old collect script."""
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    today = _today_str()

    out = workspace / "research" / f"search-results-{slug}-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(search_text, encoding="utf-8")

    stdout, stderr, rc = runner.run_sync(
        "research_collector.py",
        ["collect", "--website", "laserpointerhub", "--topic", topic,
         "--search-file", str(out)],
    )
    _copy_research_products(workspace)

    rd_file = _latest_file(f"research/research-data-{slug}-*.md", workspace)
    return {
        "success": rc == 0 and rd_file is not None,
        "stage": "r2_collect" if rc == 0 else "r1_results",
        "research_data_file": str(rd_file) if rd_file else None,
        "stdout": stdout, "stderr": stderr, "exit_code": rc,
    }


# ── R3-R4: AI Analysis + Scorer ─────────────────────────────────────────

_RESEARCH_AI_SYSTEM = """You are an SEO research analyst. Follow this exact methodology.

## Step 0: Context Inheritance
Read the topic-context if present. If source=plan: inherit intent, tier, signals, guidance — DO NOT re-judge.
If source=heuristic: intent/tier are guessed; you may refine in analysis.

## Step 0.5: Keyword Priority
⭐⭐⭐ E1 — GSC measured (highest priority)
⭐⭐   E2 — Human-verified
⭐     E3 — Six-circle expansion (long-tail supplement)

## Step 1: Keyword Analysis
- Primary keyword from data package.
- Search intent verified from SERP (Section 1). If SERP contradicts PLAN, use SERP and note.
- Current ranking from A section.
- NEVER fabricate search volume or KD numbers. Use GSC impressions as demand proxy.

## Step 2: Competitive SERP Analysis
From Section 1:
- Top 5 content types and structures
- Must-cover topics ALL cover
- Differentiation opportunities (1-2 cover)
- Unique angle (NO ONE covers)

## Step 3: Objectivity Check
1. SERP intent matches what you plan? Adjust if not.
2. Each content gap confirmed by 2+ competitors? Otherwise not valid.
3. Already top 10? Different strategy than starting fresh.
4. High demand / low competition first.

## Step 4: Content Planning
- Pillar Page: 1-2 word, high volume, 3000-5000 words, 2-3 mini-stories
- Cluster Content: 3+ word, specific question, 1500-3000 words, 1-2 mini-stories
- Product Roundup: best/review/vs, 2500-4000 words, 2 mini-stories

Output: H2 outline, target word count, internal link strategy (3-4 blog + 2-3 product pages),
meta data (Title 50-60c, Desc 150-160c), FAQ questions (≥3, at least 1 from G category).

## Step 5: Scoring
The scorer runs separately. You explain the score — don't recalculate.

## Step 6: Finalize Material Pack
Check A-H pre-filled items. Mark [search] for new, [library] for confirmed.
Fill B/D/F if extractable. Check G (PAA) — supplement from SERP if Section 8 empty.
H: mark for manual fill.

### [search] Format (CRITICAL — wrong format = 0 items archived)
A. Pain Points:
```
- **[search] Pain point title**
- Source: [label](https://url)
- Quote: "actual user words"
```

C. Case Studies:
```
- **[search] Case title**
- Source: [label](https://url)
- Summary: detailed story
- Use in article: what point this illustrates
```

E. Citations:
```
- **[search] Citation title**
- Source: [label](https://url)
- Key finding: specific data or stat
- Type: .gov / .edu / standard / ...
```

G. PAA Questions:
```
- **[search] PAA question verbatim**
- Source: [URL or "search AI"]
- Answer hint: 1-2 sentence context
```

Rules:
- English field names (Source: Quote: Key finding:) — NOT Chinese
- Each line starts with "- "
- URLs in Markdown [label](url)
- G category: real PAA/Reddit/Quora only, NO template questions

### Fill Priority
Commercial: B > C > F > E
Informational: A > D > E > G

## Output Format
Output the Material Pack (Part 1 + Part 2 + Part 3), then `===BRIEF===`, then the Research Brief:

1. SEO Foundation (keywords with tier labels)
2. Competitive Landscape (rivals + gaps)
3. Recommended Outline (H2 structure)
4. Supporting Elements (data/stories/visuals)
5. Opportunity Score (explain, not recalculate)
6. Objectivity Checklist"""


def stage_r3_ai_analyze(topic: str, workspace: Path,
                         settings=None) -> dict:
    """AI analysis following old Research Skill Step 0-6."""
    slug = _slugify(topic)
    today = _today_str()

    rd = _latest_file(f"research/research-data-{slug}-*.md", workspace)
    if not rd:
        return {"success": False, "stage": "r2_collect",
                "error": "research-data 文件不存在，请先粘贴搜索结果"}

    data_text = rd.read_text(encoding="utf-8")

    # Build user prompt with topic-context if available
    tc_path = workspace / "research" / f"topic-context-{slug}.json"
    tc_text = ""
    if tc_path.exists():
        tc_text = f"\n## Topic Context\n```json\n{tc_path.read_text(encoding='utf-8')}\n```\n"

    bv_path = workspace / "context" / "brand-voice.md"
    bv_text = bv_path.read_text(encoding="utf-8")[:3000] if bv_path.exists() else ""

    user_prompt = f"""Analyze: "{topic}"

{tc_text}

## Brand Voice
{bv_text}

## Research Data
{data_text[:12000]}

Follow the system instructions exactly. Output Material Pack, then ===BRIEF===, then Brief."""

    try:
        from seo_ops.services.ai import build_ai_provider
        from seo_ops.config import get_settings

        s = settings or get_settings()
        if not s.ai_enabled:
            return {"success": False, "error": "AI 未配置，请在设置页配置 AI"}

        provider = build_ai_provider(s)
        resp = provider.chat(
            system=_RESEARCH_AI_SYSTEM, user=user_prompt, temperature=0.3)

        parts = resp.content.split("===BRIEF===", 1)
        mp_text = parts[0].strip()
        brief_text = parts[1].strip() if len(parts) > 1 else ""

        # Save material pack
        mp_path = workspace / "material-packs" / f"{slug}-{today}.md"
        mp_path.parent.mkdir(parents=True, exist_ok=True)
        mp_path.write_text(mp_text, encoding="utf-8")

        # Save brief
        if brief_text:
            (workspace / "research" / f"brief-{slug}-{today}.md").write_text(
                brief_text, encoding="utf-8")

        # Run scorer
        runner = LegacyRunner(workspace)
        runner.run_sync("research_scorer.py",
                         ["--website", "laserpointerhub", "--slug", slug])
        _copy_research_products(workspace)

        return {"success": True, "stage": "r5_write_ready"}
    except Exception as e:
        return {"success": False, "stage": "r2_collect", "error": str(e)}


# ── W0-W1: Validate + Draft ─────────────────────────────────────────────

_WRITE_AI_SYSTEM = """You are an SEO content writer. Follow these exact structural rules.

## Article Structure (in order):

1. FRONTMATTER (links left empty — post-process fills them):
---
Title: [H1 with primary keyword]
Slug: [URL slug]
Author: LaserPointerHub
Summary: [2-3 sentence summary]
Tags: [3-5 comma-separated]
SEO Title: [50-60 chars]
SEO Description: [150-160 chars]
SEO Keywords: [comma-separated, primary first]
---

2. H1 — same as frontmatter Title.

3. INTRODUCTION (150-250 words):
- First 1-2 sentences: DIRECT ANSWER to the search query
- Hook: ONE of (Provocative Question / Specific Scenario / Surprising Statistic / Bold Statement / Counterintuitive Claim)
- APP: Agree → Promise → Preview
- Primary keyword in first 100 words

4. KEY TAKEAWAYS (after intro):
> **Key Takeaways**
> - [Specific conclusion 1, with numbers/names/results]
> - [Specific conclusion 2-5]
3-5 standalone conclusions, not a table of contents.

5. QUICK SPECS (commercial articles only):
> ### Quick Specs: [Product]
> - **Wavelength**: [spec]
> - **Output Power**: [spec]
> - **Battery**: [spec]
> - **Build**: [material]
> - **Price**: [price]
> - **Key Differentiator**: [difference]
5-7 key-value pairs from live_products_report.

6. BODY (H2/H3):
- Pillar 2500-4000 / Cluster 1200-2500 / Roundup 2000-3500 words
- 4-7 H2s, logical progression
- Each paragraph 2-4 sentences, self-contained
- Embed at least 1 YouTube video

7. E-E-A-T (integrated):
- Experience: real user scenarios/test data from material pack C/E
- If NO real test data: go to analysis, do NOT invent "In our test"/"We found"
- Expertise: 1-2 authority sources for technical claims
- Trustworthiness: link contact page, return policy

8. SOFT-SELL: First 40% — NO hard product push. Products appear after intent.

9. REAL MATERIALS (instead of mini-stories):
- Extract real scenarios from A/C/E
- If material pack has cases: quote, mark source
- If no real scenarios: go to analysis, don't invent stories

10. CTAs (2-3, distributed):
- Informational → internal blog links
- Commercial → product page links
- Max 1 soft CTA in first 500 words
- No "click here"/"read more"

11. CONCLUSION (150-200 words): recap 3-5 points, next action, CTA.

12. FAQ (H2 "Frequently Asked Questions"):
- Natural language questions
- 2-3 sentence answers from body
- At least 1 from G category (real PAA)

13. FAQPage JSON-LD (3-4 Q&A pairs):
<script type="application/ld+json">
{ "@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [...] }
</script>

14. LINKS in body:
- Blog ~1/1000 words, Product ~1/1300 words, External ~1/800 words (min 2)
- NEVER fabricate URLs

## Core Rules:
1. Material pack = only source of truth. Missing data → "[data not in material pack]"
2. Primary keyword: H1, first 100 words, 2+ H2s
3. First 40%: no hard product push
4. First 1-2 sentences: direct answer
5. Self-contained paragraphs (2-4 sentences)
6. No generic link anchors
7. No fabricated numbers"""


def stage_w0_validate_and_draft(topic: str, author: str, workspace: Path,
                                 settings=None) -> dict:
    """Validate material pack, then generate draft via AI."""
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    today = _today_str()

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    if not mp:
        return {"success": False, "error": "material pack 不存在"}

    # Validate
    stdout, stderr, rc = runner.run_sync(
        "write_collector.py",
        ["validate", "--website", "laserpointerhub", "--topic", topic,
         "--pack", str(mp)],
    )
    if rc != 0 or "material pack file not found" in stdout.lower():
        return {"success": False, "error": "素材包校验失败", "report": stdout}

    # Write draft via AI
    pack_text = mp.read_text(encoding="utf-8")

    ctx_parts = []
    for key, fname in [("brand_voice", "brand-voice.md"),
                        ("writing_examples", "writing-examples.md"),
                        ("style_guide", "style-guide.md"),
                        ("seo_guidelines", "seo-guidelines.md")]:
        fp = workspace / "context" / fname
        if fp.exists():
            ctx_parts.append(f"### {key}\n```\n{fp.read_text(encoding='utf-8')[:2000]}\n```")

    user_prompt = f"""Write: "{topic}"

## Material Pack
{pack_text[:10000]}

## Context
{chr(10).join(ctx_parts)}

Follow the system instructions. Output full Markdown with frontmatter."""

    try:
        from seo_ops.services.ai import build_ai_provider
        from seo_ops.config import get_settings

        s = settings or get_settings()
        if not s.ai_enabled:
            return {"success": False, "error": "AI 未配置"}

        provider = build_ai_provider(s)

        # Patch author into system prompt
        sys_prompt = _WRITE_AI_SYSTEM
        if author and author.strip():
            sys_prompt = sys_prompt.replace("Author: LaserPointerHub",
                                             f"Author: {author.strip()}")

        resp = provider.chat(system=sys_prompt, user=user_prompt, temperature=0.3)

        draft_path = workspace / "drafts" / f"{slug}-{today}.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(resp.content, encoding="utf-8")

        return {"success": True, "stage": "w1_draft", "validate_report": stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── W1b: Pre-Check ──────────────────────────────────────────────────────

def stage_w1b_pre_check(topic: str, tier: str, workspace: Path) -> dict:
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    args = ["--draft", str(draft)]
    if tier:
        args += ["--tier", tier]
    stdout, stderr, rc = runner.run_sync("write_pre_check.py", args)
    fails = stdout.count("❌")
    return {
        "success": True, "stage": "w1b_pre_check",
        "report": stdout, "fail_count": fails,
    }


# ── W2: Post-Process ────────────────────────────────────────────────────

def stage_w2_post_process(topic: str, apply: bool = False, force: bool = False,
                           workspace: Path = None) -> dict:
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    args = ["post-process", "--website", "laserpointerhub", "--draft", str(draft)]
    if mp:
        args += ["--pack", str(mp)]
    if apply:
        args.append("--apply")
    if force:
        args.append("--force")
    stdout, stderr, rc = runner.run_sync("write_collector.py", args)
    gate_passed = rc == 0
    return {
        "success": gate_passed, "stage": "w2_post_process",
        "report": stdout, "gate_passed": gate_passed,
    }


# ── W3: Register ────────────────────────────────────────────────────────

def stage_w3_register(topic: str, workspace: Path) -> dict:
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    dc = draft.read_text(encoding="utf-8")
    title_m = re.search(r"^Title:\s*(.+)$", dc, re.MULTILINE)
    kw_m = re.search(r"^SEO Keywords:\s*(.+)$", dc, re.MULTILINE)
    article_title = title_m.group(1).strip() if title_m else topic
    primary_kw = (kw_m.group(1).split(",")[0].strip()
                  if kw_m else topic)
    new_url = f"https://laserpointerhub.com/blog/{slug}"

    args = [
        "register", "--website", "laserpointerhub",
        "--draft", str(draft),
        "--pack", str(mp) if mp else "",
        "--new-url", new_url,
        "--title", article_title,
        "--keyword", primary_kw,
    ]
    stdout, stderr, rc = runner.run_sync("write_collector.py", args)
    _copy_research_products(workspace)
    return {"success": rc == 0, "stage": "w3_register", "report": stdout}


# ── Display helpers ────────────────────────────────────────────────────

def get_legacy_display_data(topic: str, workspace: Path) -> dict[str, Any]:
    """Collect all display data for the Legacy workflow UI."""
    stage, files = detect_stage(topic, workspace)
    slug = _slugify(topic)

    data: dict[str, Any] = {
        "stage": stage, "stage_name": stage_label(stage),
        "stage_step": stage_step(stage), "files": files,
    }

    # Load prompt content
    sp = files.get("search_prompt")
    if sp and Path(sp).exists():
        try:
            text = Path(sp).read_text(encoding="utf-8")
            data["prompt_content"] = text
            data["prompt_preview"] = text[:500]
        except Exception:
            pass

    # Load draft preview
    draft = files.get("draft")
    if draft and Path(draft).exists():
        try:
            dc = Path(draft).read_text(encoding="utf-8")
            data["draft_preview"] = dc[:2000]
        except Exception:
            pass

    # Load research score
    rs = files.get("research_score")
    if rs and Path(rs).exists():
        try:
            data["score_preview"] = Path(rs).read_text(encoding="utf-8")[:800]
        except Exception:
            pass

    # Load brief preview
    br = files.get("brief")
    if br and Path(br).exists():
        try:
            data["brief_preview"] = Path(br).read_text(encoding="utf-8")[:500]
        except Exception:
            pass

    return data
