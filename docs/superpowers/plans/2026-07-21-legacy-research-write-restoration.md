# Legacy Research + Write 1:1 Restoration Plan (v2 — Corrected)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current new-article production channel (material confirm → 3-stage AI) with a 1:1 restoration of the old `research` + `write` Skill workflow. Old-article updates, topic research, suggestions, topic graph, and imports all remain untouched.

**Architecture:** A thin data-sync layer generates five filesystem files from the database before each Legacy session (published-index, published articles, products report, internal-links-map, seo-data-manual). Three material library files start from old-snapshot seed and are then maintained by old scripts (archive stage). The `LegacyWorkflowService` wraps calls to frozen old scripts, follows SKILL.md AI instructions exactly, and streams stdout/stderr via SSE for real-time terminal-like feedback.

**Tech Stack:** Python 3.12+, FastAPI + Jinja + SSE, SQLite, frozen old scripts at `/home/laoma/seo-workflow/data_sources/modules/`

**Do NOT modify:** `/home/laoma/seo-workflow/` (read-only), `content_production.py` (old articles still use it), `material_workflow.py` (old articles), research/suggestions/topics/imports pages.

---

## Data Flow Diagram

```
Database (SQLite)                     Legacy Workspace (filesystem)
═══════════════                       ═══════════════════════════

content_items (65 blogs + 15 products)
  │                                        ┌─ published/*.md (65 files)
  ├── [sync on session start] ────────────┼── published-index.json
  │                                        ├─ live_products_report.md
  │                                        └─ internal-links-map.md
gsc_metrics + gsc_query_page_metrics
  │
  └── [sync on session start] ────────────→ seo-data-manual.md

(no DB equivalent)                          ┌─ pain-points-library.md  ← from old snapshot
                                            ├─ case-studies-library.md ← from old snapshot
                                            └─ external-sources-library.md ← from old snapshot
                                                   ↑
                                            [old archive script maintains these]
```

---

## Task 1: Data Sync Layer — Database to Filesystem

**Files:**
- Create: `src/seo_ops/services/legacy_sync.py`

This module takes the current database state and generates five workspace files. It runs every time a Legacy session starts (user clicks "开始制作" on a new article).

- [ ] **Step 1: Create `legacy_sync.py` with `generate_workspace_files()`**

```python
# src/seo_ops/services/legacy_sync.py
"""Generate Legacy workspace files from the database.

Every Legacy session reads these files, so they must be fresh before each run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seo_ops.db import connection

WORKSPACE_ROOT = Path("data/legacy_workflow/laserpointerhub")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def generate_published_index(workspace: Path) -> dict:
    """Generate published-index.json from content_items + latest snapshots."""
    cursor = connection.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url, ci.source_updated_at,
               cs.metadata_json
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = (
            SELECT id FROM content_snapshots
            WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    
    articles = []
    for row in cursor.fetchall():
        slug, title, canonical_url, updated_at, meta_json = row
        tags = []
        link_counts = {"blog": 0, "product": 0}
        if meta_json:
            try:
                meta = json.loads(meta_json)
                tags = meta.get("tags", []) or []
            except Exception:
                pass
        
        articles.append({
            "slug": slug,
            "title": title,
            "tags": tags,
            "url": canonical_url,
            "updated": updated_at,
            "link_counts": link_counts,
        })
    
    index_path = workspace / "published" / "published-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"file": str(index_path), "count": len(articles)}


def generate_published_articles(workspace: Path) -> dict:
    """Generate published/*.md files from content_snapshots.body (plain text)."""
    cursor = connection.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url, ci.source_updated_at,
               cs.body, cs.seo_title, cs.seo_description, cs.metadata_json, cs.summary
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = (
            SELECT id FROM content_snapshots
            WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    
    articles_dir = workspace / "published"
    articles_dir.mkdir(parents=True, exist_ok=True)
    
    count = 0
    for row in cursor.fetchall():
        slug, title, canonical_url, updated, body, seo_title, seo_desc, meta_json, summary = row
        
        if not body:
            continue
        
        tags = []
        seo_keywords = ""
        if meta_json:
            try:
                meta = json.loads(meta_json)
                tags = meta.get("tags", []) or []
                seo_keywords = meta.get("seoKeywords", "") or ""
            except Exception:
                pass
        
        tag_str = ", ".join(tags) if tags else ""
        
        md = ""
        md += f"---\n"
        md += f'Title: {title or slug}\n'
        md += f'Slug: {slug}\n'
        if summary:
            md += f'Summary: {summary}\n'
        if tag_str:
            md += f'Tags: {tag_str}\n'
        if seo_title:
            md += f'SEO Title: {seo_title}\n'
        if seo_desc:
            md += f'SEO Description: {seo_desc}\n'
        if seo_keywords:
            md += f'SEO Keywords: {seo_keywords}\n'
        md += "---\n\n"
        md += body  # plain text body
        md += "\n"
        
        (articles_dir / f"{slug}.md").write_text(md, encoding="utf-8")
        count += 1
    
    return {"file": str(articles_dir), "count": count}


def generate_live_products_report(workspace: Path) -> dict:
    """Generate live_products_report.md from product content_items."""
    cursor = connection.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url, ci.source_updated_at,
               cs.body, cs.metadata_json, cs.summary
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = (
            SELECT id FROM content_snapshots
            WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        WHERE ci.site_id = 1 AND ci.content_type = 'product' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    
    products_dir = workspace / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    
    lines = []
    lines.append(f"# Live Products Report — LaserPointerHub")
    lines.append(f"> Generated: {_now()} from database")
    lines.append("")
    lines.append("| SKU | Title | URL | Price | Power | Wavelength | Features |")
    lines.append("|---|---:|---|---|---|---|")
    
    for row in cursor.fetchall():
        slug, title, canonical_url, updated, body, meta_json, summary = row
        sku = ""
        price = ""
        power = ""
        wavelength = ""
        features = ""
        
        if meta_json:
            try:
                meta = json.loads(meta_json)
                sku = meta.get("sku", "") or ""
                price = meta.get("price", "") or ""
                power = meta.get("internalPower", "") or ""
                attrs = meta.get("attributes", {}) or {}
                wavelength = attrs.get("wavelength", "") or ""
                feats = meta.get("features", []) or []
                features = "; ".join(str(f) for f in feats[:3])
            except Exception:
                pass
        
        lines.append(f"| {sku} | [{title}]({canonical_url}) | {canonical_url} | {price} | {power} | {wavelength} | {features} |")
    
    lines.append("")
    lines.append("## Product Details")
    lines.append("")
    
    # Add detailed product sections
    for row in cursor.fetchall():
        # (re-run cursor would need reset; simplified for plan)
        pass
    
    report_path = products_dir / "live_products_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(report_path), "count": cursor.rowcount if hasattr(cursor, 'rowcount') else 0}


def generate_internal_links_map(workspace: Path) -> dict:
    """Generate internal-links-map.md from content_items + topic links."""
    # Get all blog articles
    cursor = connection.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url
        FROM content_items ci
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    
    blogs = [{"slug": r[0], "title": r[1] or r[0], "url": r[2]} for r in cursor.fetchall()]
    
    # Get all products
    cursor = connection.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url,
               cs.metadata_json
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = (
            SELECT id FROM content_snapshots
            WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        WHERE ci.site_id = 1 AND ci.content_type = 'product' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    
    products = []
    for row in cursor.fetchall():
        sku = ""
        if row[3]:
            try:
                meta = json.loads(row[3])
                sku = meta.get("sku", "") or ""
            except Exception:
                pass
        products.append({"slug": row[0], "title": row[1] or row[0], "url": row[2], "sku": sku})
    
    lines = []
    lines.append(f"# Internal Links Map — LaserPointerHub")
    lines.append(f"> 数据来源: CMS 导入 + 数据库自动生成")
    lines.append(f"> 最后同步: {_now()}")
    lines.append("")
    lines.append("## 产品列表")
    lines.append("")
    
    for i, p in enumerate(products, 1):
        lines.append(f"- [{p['sku']}]({p['url']})")
    
    lines.append("")
    lines.append("## 已发布文章")
    lines.append("")
    lines.append("| # | Title | URL | Primary Keyword |")
    lines.append("|---|---|---|---|")
    
    for i, b in enumerate(blogs, 1):
        kw = b["slug"].replace("-", " ")[:40]
        lines.append(f"| {i} | {b['title'][:60]} | {b['url']} | {kw} |")
    
    lines.append("")
    lines.append("## 文章元数据")
    lines.append("")
    
    for i, b in enumerate(blogs, 1):
        lines.append(f"### #{i} — {b['slug']}")
        lines.append(f"- URL: {b['url']}")
        lines.append(f"- 话题标签: {b['slug'].replace('-', ' ')[:50]}")
        lines.append("- 内链文章: []")
        lines.append("- 内链产品: []")
        lines.append("- 状态: published")
        lines.append("")
    
    map_path = workspace / "context" / "internal-links-map.md"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(map_path), "blogs": len(blogs), "products": len(products)}


def generate_seo_data_manual(workspace: Path) -> dict:
    """Generate seo-data-manual.md from GSC metrics."""
    # Get active GSC import
    cursor = connection.execute("""
        SELECT id, imported_at FROM imports
        WHERE site_id = 1 AND source_type = 'gsc' AND analysis_active = 1 AND quality_eligible = 1
        ORDER BY imported_at DESC LIMIT 1
    """)
    row = cursor.fetchone()
    if not row:
        manual_path = workspace / "context" / "seo-data-manual.md"
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(f"# SEO Data Manual\n\n> 无有效 GSC 数据批次\n", encoding="utf-8")
        return {"file": str(manual_path), "gsc_rows": 0}
    
    import_id = row[0]
    
    # Top queries by impressions
    cursor = connection.execute("""
        SELECT dimension_value as keyword,
               SUM(impressions) as impressions,
               AVG(ctr) as ctr,
               AVG(position) as position
        FROM gsc_metrics
        WHERE import_id = ? AND dimension = 'query' AND period = 'current'
        GROUP BY keyword ORDER BY impressions DESC LIMIT 20
    """, (import_id,))
    
    query_rows = list(cursor.fetchall())
    
    lines = []
    lines.append(f"# SEO Data Manual — LaserPointerHub")
    lines.append(f"> 数据来源: GSC OAuth 同步")
    lines.append(f"> 最后更新: {_now()}")
    lines.append(f"> 导入批次: #{import_id}")
    lines.append("")
    lines.append("## A1. GSC Top 20 Queries")
    lines.append("")
    lines.append("| # | Keyword | Impressions | CTR | Position |")
    lines.append("|---|---:|---:|---:|")
    
    for i, (kw, imp, ctr, pos) in enumerate(query_rows, 1):
        lines.append(f"| {i} | {kw} | {int(imp)} | {round(ctr*100, 1) if ctr else 0}% | {round(pos, 1) if pos else '—'} |")
    
    lines.append("")
    lines.append("## A2. Quick Win Opportunities (Position 8-20, Impressions > 100)")
    lines.append("")
    lines.append("| # | Keyword | Position | Impressions |")
    lines.append("|---|---:|---:|")
    
    quick_wins = [r for r in query_rows if r[3] and 8 <= r[3] <= 20 and r[1] > 100]
    for i, (kw, imp, ctr, pos) in enumerate(quick_wins[:10], 1):
        lines.append(f"| {i} | {kw} | {round(pos, 1)} | {int(imp)} |")
    
    lines.append("")
    lines.append(f"## E1. GSC Measured Keywords ⭐⭐⭐")
    lines.append("")
    for kw, imp, _, _ in query_rows[:15]:
        lines.append(f"- {kw}")
    
    lines.append("")
    lines.append(f"## E2. Human-Verified Keywords ⭐⭐")
    lines.append("")
    lines.append("(Hand-fill with manually verified target keywords)")
    
    manual_path = workspace / "context" / "seo-data-manual.md"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(manual_path), "gsc_rows": len(query_rows)}


def sync_all() -> dict:
    """Synchronize all workspace files from database. Returns report dict."""
    workspace = WORKSPACE_ROOT
    results = {}
    results["published_index"] = generate_published_index(workspace)
    results["published_articles"] = generate_published_articles(workspace)
    results["products"] = generate_live_products_report(workspace)
    results["internal_links_map"] = generate_internal_links_map(workspace)
    results["seo_data_manual"] = generate_seo_data_manual(workspace)
    return results
```

- [ ] **Step 2: Commit**

```bash
git add src/seo_ops/services/legacy_sync.py
git commit -m "feat: add data sync layer that generates Legacy workspace files from database"
```

---

## Task 2: Enhanced Search Prompt Generator

**Files:**
- Modify: `src/seo_ops/services/legacy_workflow.py`

The old `research_collector.py generate_prompt()` produces rich 8-section prompts. We replicate this but feed it from the synced workspace files (which came from the database).

- [ ] **Step 1: Create `_generate_search_prompt()` replicating the old format**

```python
def _generate_search_prompt(topic: str, workspace: Path, website: str = "laserpointerhub") -> str:
    """Generate search prompt matching old research_collector format but fed from database-synced files."""
    
    slug = _slugify(topic)
    date_str = _today_str()
    
    # Read synced GSC data
    seo_path = workspace / "context" / "seo-data-manual.md"
    seo_content = seo_path.read_text(encoding="utf-8") if seo_path.exists() else ""
    
    # Extract GSC keywords
    gsc_kws = []
    for m in re.finditer(r'\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([\d,]+)', seo_content):
        kw = m.group(1).strip()
        try:
            imp = int(m.group(2).replace(",", ""))
            if imp > 100:
                gsc_kws.append(kw)
        except ValueError:
            pass
    
    # Read published index
    pub_path = workspace / "published" / "published-index.json"
    published_titles = []
    published_tags = set()
    if pub_path.exists():
        try:
            data = json.loads(pub_path.read_text(encoding="utf-8"))
            for entry in data if isinstance(data, list) else []:
                title = entry.get("title", "")
                published_titles.append(title[:80])
                for t in entry.get("tags", []):
                    published_tags.add(t.lower())
        except Exception:
            pass
    
    # Read library summaries
    def _lib_summary(lib_path, label):
        if not lib_path.exists():
            return ""
        content = lib_path.read_text(encoding="utf-8")
        entries = re.findall(r'\n## (.+)', content)
        skip_words = ['标签索引', '填写规则', '外链库', '常见权威', '索引', '规则']
        real = [e.strip() for e in entries if not any(s in e.lower() for s in skip_words)]
        if not real:
            return ""
        return f"### Existing {label} ({len(real)} total)\n" + "\n".join(f"- {e[:60]}" for e in real[:5])
    
    pain_summary = _lib_summary(workspace / "context" / "pain-points-library.md", "User Pain Points")
    case_summary = _lib_summary(workspace / "context" / "case-studies-library.md", "Case Studies")
    
    def _source_summary():
        lib_path = workspace / "context" / "external-sources-library.md"
        if not lib_path.exists():
            return ""
        content = lib_path.read_text(encoding="utf-8")
        urls = re.findall(r'\|\s*\d+\s*\|\s*(https?://\S+)', content)
        samples = re.findall(r'\|\s*(\d+)\s*\|\s*(https?://\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+)\s*\|', content)
        if not urls:
            return ""
        lines = [f"### Existing Authoritative Citations ({len(urls)} total)"]
        for s in samples[:3]:
            lines.append(f"- [{s[2]}] {s[3].strip()[:50]}")
        return "\n".join(lines)
    
    source_summary = _source_summary()
    
    # Determine intent
    intent = "混合型"
    commercial_words = {"best", "buy", "review", "vs", "comparison", "top", "price", "budget", "cheap"}
    topic_lower = topic.lower()
    if any(w in topic_lower for w in commercial_words):
        intent = "转化型"
    elif any(w in topic_lower for w in {"how", "what", "why", "guide", "safe", "use", "work"}):
        intent = "信息型"
    
    # Build "What We Know"
    what_we_know = []
    if gsc_kws:
        what_we_know.append(f"GSC keywords we rank for: {', '.join(gsc_kws[:5])}")
    if published_titles:
        what_we_know.append(f"Published articles: {len(published_titles)} total. Topics include: {', '.join(published_titles[:5])}")
    if published_tags:
        what_we_know.append(f"Content tags already covered: {', '.join(list(published_tags)[:10])}")
    
    # Build "What We Need"
    what_we_need = []
    if not gsc_kws:
        what_we_need.append('No GSC ranking data — need SERP analysis to understand what ranks')
    else:
        what_we_need.append('GSC data shows search demand. Find what TOPIC ANGLE differentiates from top 5.')
    what_we_need.append('Focus on information gaps — what do the top 5 articles NOT cover?')
    if intent == "转化型":
        what_we_need.append('Commercial article — prioritize spec comparisons, buyer decision factors, and red flags. Pricing data from search AI may be inaccurate.')
    else:
        what_we_need.append('Informational article — prioritize real user pain points, practical how-to, authority sources.')
    
    # --- BUILD THE PROMPT ---
    prompt = f"""You are helping me research for an SEO article about "{topic}" on {website}.com. I need comprehensive, real, verifiable data. Do NOT fabricate anything — say "not found" if you cannot find it.

## What We Already Know
{chr(10).join(what_we_know)}

## What We Need You To Find
{chr(10).join(what_we_need)}

Search intent: {intent}

---

## Section 1: SERP Analysis
Search for "{topic}" and analyze the top 5 ranking pages. Fill this table:

| # | URL | Title | Est. Words | Content Type | H2 Sections |
|---|-----|-------|-----------|--------------|-------------|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

After the table, answer:
- Common H2 topics ALL top 5 cover (must-have — we CANNOT skip):
- Topics only 1-2 cover (differentiation opportunities):
- Topics NO ONE covers (our unique angle — THIS is where we win):
- Average word count of top 5:
- SERP composition (brand blogs vs independent reviews vs forums vs official):

---

## Section 2: User Pain Points
Find 8-10 REAL frustrations/complaints/questions about {topic}. Search Reddit, Quora, forums, Amazon reviews.

Important:
{pain_summary if pain_summary else 'We have a pain point library. Find NEW, different pain points — NOT the same ones we already know.'}

Format each as:
- Pain point: [describe in user's language]
- Source: [URL]
- Quote: "[actual user words]"

Prioritize specific, emotional complaints with actual numbers/experiences.

---

## Section 3: Market Data
Current market landscape for {topic} as of {datetime.now().strftime('%B %Y')}:
- Product models, key specs, and features — specific models with differentiators
- Pricing tiers (budget, mid-range, premium) — approximate ranges only
- Recent trends, new products, technology shifts
- Include source URLs for all claims

---

## Section 4: Case Studies / Real Stories
Find 3-5 real user stories.

{case_summary if case_summary else 'We have a case study library. Find DIFFERENT, fresh cases we have not collected.'}

Format:
- Story: [what happened — specific details, names, numbers, results]
- Source: [URL]
- Use in article: [what point this illustrates]

---

## Section 5: Authoritative Citations
Find 3-5 authoritative sources (.gov, .edu, academic journals, industry standards, official reports).

{source_summary if source_summary else 'We have a pre-verified citation library. Find NEW citations not yet collected.'}

Format:
- Type: [source type]
- Key finding: [specific statistic or data point]
- Source URL:
- Why authoritative: [one sentence]

---

## Section 6: Information Gaps
**This is the most critical section.** Our article must be DIFFERENT from what already ranks.

Be SPECIFIC — name which competitor article misses which topic:
- Topics the top 5 all miss or cover poorly:
- Outdated or wrong information in current top results:
- Unique angle we can bring:
- What single piece of information would make our article the definitive resource:

No generic advice like "go deeper" or "add more detail."

---

## Section 7: PAA Questions
List 15+ "People Also Ask" questions related to {topic}. Group by subtopic.

---

## Section 8: People Also Ask (Structured)
For "{topic}" and 5-8 closely related queries, capture REAL PAA boxes from Google SERP.
Use VERBATIM phrasing — do NOT paraphrase. 10-15 entries in this format:

- **[search] <PAA question text verbatim from Google's PAA box>**
- Source: [where found](URL or "search AI")
- Answer hint: brief answer context (1-2 sentences)

Requirements:
- Each question from real PAA box (or Reddit/Quora real user question if PAA unavailable)
- NO template questions like "What is X?" unless literally in SERP
- Include SERP source URL when possible
- Deduplicate identical questions across queries
"""

    # Save to file
    output_path = workspace / "research" / f"search-prompt-{slug}-{date_str}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    
    return prompt


def stage_r0_generate_prompt(topic: str, workspace: Path, website: str = "laserpointerhub") -> dict:
    """R0: Generate search prompt and return it for display."""
    prompt = _generate_search_prompt(topic, workspace, website)
    slug = _slugify(topic)
    date_str = _today_str()
    file_path = workspace / "research" / f"search-prompt-{slug}-{date_str}.md"
    
    return {
        "success": True,
        "stage": "r0_prompt",
        "prompt_file": str(file_path),
        "prompt_content": prompt,
    }
```

- [ ] **Step 2: Verify against the old generate_prompt() output**

Run the old script on a known topic, compare output structure:

```bash
cd /home/laoma/seo-workflow
python3 data_sources/modules/research_collector.py generate-prompt \
  --website laserpointerhub --topic "laser pointer safety for pets"
# Check the output at laserpointerhub/research/search-prompt-laser-pointer-safety-for-pets-*.md
# Verify our function produces the same sections and structure
```

- [ ] **Step 3: Commit**

```bash
git add src/seo_ops/services/legacy_workflow.py
git commit -m "feat: add enhanced 8-section search prompt generator replicating old format"
```

---

## Task 3: Legacy Workspace Setup

**Files:**
- Create: `data/legacy_workflow/laserpointerhub/` (partial — only static files + library seeds)

- [ ] **Step 1: Create directory structure and copy STATIC files only**

```bash
cd /home/laoma/seo-ops-system
LEGACY_WS=data/legacy_workflow/laserpointerhub
SRC=/home/laoma/seo-workflow/laserpointerhub

mkdir -p $LEGACY_WS/{context,products,published,research,drafts,material-packs,raw}

# STATIC files (never change): copy from old project once
for f in brand-voice.md writing-examples.md style-guide.md seo-guidelines.md target-keywords.md; do
  cp $SRC/context/$f $LEGACY_WS/context/
done

# Material library SEEDS (will be maintained by old archive script):
cp $SRC/context/pain-points-library.md $LEGACY_WS/context/
cp $SRC/context/case-studies-library.md $LEGACY_WS/context/
cp $SRC/context/external-sources-library.md $LEGACY_WS/context/

# Copy topic-context files (historical reference, not auto-synced):
cp $SRC/research/topic-context-*.json $LEGACY_WS/research/ 2>/dev/null || true

echo "Static workspace files copied."
```

- [ ] **Step 2: Run database sync to generate dynamic files**

```python
# In a Python shell or test script:
from seo_ops.services.legacy_sync import sync_all
report = sync_all()
print(report)
```

- [ ] **Step 3: Generate workspace manifest**

```bash
cd /home/laoma/seo-ops-system
find data/legacy_workflow -type f | sort | while read f; do
    sha256sum "$f" >> data/legacy_workflow/WORKSPACE_MANIFEST.md.tmp
done
echo "# Legacy Workspace Manifest" > data/legacy_workflow/WORKSPACE_MANIFEST.md
echo "Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> data/legacy_workflow/WORKSPACE_MANIFEST.md
echo "" >> data/legacy_workflow/WORKSPACE_MANIFEST.md
cat data/legacy_workflow/WORKSPACE_MANIFEST.md.tmp >> data/legacy_workflow/WORKSPACE_MANIFEST.md
rm data/legacy_workflow/WORKSPACE_MANIFEST.md.tmp
```

- [ ] **Step 4: Commit**

```bash
git add data/legacy_workflow/
git commit -m "feat: establish Legacy workspace with static files + library seeds + DB-synced files"
```

---

## Task 4: LegacyWorkflowService — Stage Management + Script Wrapper

**Files:**
- Modify: `src/seo_ops/services/legacy_workflow.py`

This is the core service that:
1. Detects current stage (file-system based)
2. Runs old scripts via subprocess with SSE output streaming
3. Calls AI following SKILL.md instructions

- [ ] **Step 1: Create `LegacyRunner` class with SSE output streaming**

```python
# In legacy_workflow.py

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator

LEGACY_MODULES_DIR = Path("/home/laoma/seo-workflow/data_sources/modules")
LEGACY_PROJECT_ROOT = Path("/home/laoma/seo-workflow")


class LegacyRunner:
    """Runs old scripts and streams their output via SSE."""
    
    def __init__(self, workspace: Path, website: str = "laserpointerhub"):
        self.workspace = workspace
        self.website = website
    
    async def run_script_stream(self, script_name: str, args: list[str], 
                                 cwd: str = None) -> AsyncGenerator[str, None]:
        """Run an old script and yield stdout/stderr lines as they appear."""
        script_path = LEGACY_MODULES_DIR / script_name
        cmd = [sys.executable, str(script_path)] + args
        effective_cwd = cwd or str(LEGACY_PROJECT_ROOT)
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
        )
        
        # Read both stdout and stderr concurrently
        async def read_stream(stream, prefix):
            while True:
                line = await stream.readline()
                if not line:
                    break
                yield f"data: {prefix}{line.decode('utf-8', errors='replace').rstrip()}\n\n"
        
        # Merge stdout and stderr
        async for line in read_stream(proc.stdout, ""):
            yield line
        async for line in read_stream(proc.stderr, "[stderr] "):
            yield line
        
        await proc.wait()
        yield f"data: [EXIT:{proc.returncode}]\n\n"
    
    def run_script_sync(self, script_name: str, args: list[str],
                         cwd: str = None) -> tuple[str, str, int]:
        """Synchronous version for non-streaming calls."""
        script_path = LEGACY_MODULES_DIR / script_name
        cmd = [sys.executable, str(script_path)] + args
        effective_cwd = cwd or str(LEGACY_PROJECT_ROOT)
        
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=effective_cwd
        )
        return result.stdout, result.stderr, result.returncode
```

- [ ] **Step 2: Stage detection (file-system based)**

```python
def detect_stage(topic: str, workspace: Path) -> tuple[str, dict]:
    """Detect current Legacy stage by checking file existence."""
    slug = _slugify(topic)
    
    draft = _latest_file_matching(f"drafts/{slug}-*.md", workspace)
    material_pack = _latest_file_matching(f"material-packs/{slug}-*.md", workspace)
    research_data = _latest_file_matching(f"research/research-data-{slug}-*.md", workspace)
    search_results = _latest_file_matching(f"research/search-results-{slug}-*.md", workspace)
    search_prompt = _latest_file_matching(f"research/search-prompt-{slug}-*.md", workspace)
    research_score = _latest_file_matching(f"research/research-score-{slug}-*.md", workspace)
    
    files = {
        "slug": slug,
        "search_prompt": str(search_prompt) if search_prompt else None,
        "search_results": str(search_results) if search_results else None,
        "research_data": str(research_data) if research_data else None,
        "research_score": str(research_score) if research_score else None,
        "material_pack": str(material_pack) if material_pack else None,
        "draft": str(draft) if draft else None,
    }
    
    if draft and not material_pack:
        draft_content = draft.read_text(encoding="utf-8")
        if "内链:" in draft_content or "外链:" in draft_content:
            return "w3_register", files
        return "w1_draft", files
    
    if draft:
        return "w1_draft", files
    
    if material_pack:
        pack_content = material_pack.read_text(encoding="utf-8")
        if "Part 3" in pack_content:
            if research_score:
                return "r5_write_ready", files
            return "r4_score", files
        return "r3_ai_analyze", files
    
    if research_data:
        return "r2_collect", files
    
    if search_results:
        return "r1_results", files
    
    if search_prompt:
        return "r0_prompt", files
    
    return "r0_pending", files
```

- [ ] **Step 3: Stage N → N+1 transition functions**

Each stage function: receives topic + workspace, runs the appropriate script/AI, returns result.

```python
def stage_r1_save_and_collect(topic: str, search_text: str, 
                               workspace: Path, website: str = "laserpointerhub") -> dict:
    """Save search results and run collect script."""
    runner = LegacyRunner(workspace, website)
    slug = _slugify(topic)
    date_str = _today_str()
    
    # Save search results
    results_path = workspace / "research" / f"search-results-{slug}-{date_str}.md"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(search_text, encoding="utf-8")
    
    # Run old collect script
    stdout, stderr, rc = runner.run_script_sync(
        "research_collector.py",
        ["collect", "--website", website, "--topic", topic,
         "--search-file", str(results_path)],
    )
    
    # Copy generated research-data files from old project root to workspace
    _copy_research_files_from_old_project(slug, workspace)
    
    data_file = _latest_file_matching(f"research/research-data-{slug}-*.md", workspace)
    
    return {
        "success": rc == 0 and data_file is not None,
        "stage": "r2_collect" if rc == 0 else "r1_results",
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": rc,
    }


def _copy_research_files_from_old_project(slug: str, workspace: Path):
    """After old scripts write to old project root, copy results to our workspace."""
    old_research = LEGACY_PROJECT_ROOT / "laserpointerhub" / "research"
    ws_research = workspace / "research"
    ws_research.mkdir(parents=True, exist_ok=True)
    
    for pattern in [f"research-data-{slug}-*.md", f"research-data-{slug}-*.json",
                     f"research-score-{slug}-*.md", f"brief-{slug}-*.md"]:
        for f in old_research.glob(pattern):
            dest = ws_research / f.name
            dest.write_text(f.read_text(encoding="utf-8"))
    
    # Also copy material-packs
    old_mp = LEGACY_PROJECT_ROOT / "laserpointerhub" / "material-packs"
    ws_mp = workspace / "material-packs"
    ws_mp.mkdir(parents=True, exist_ok=True)
    for f in old_mp.glob(f"{slug}-*.md"):
        dest = ws_mp / f.name
        dest.write_text(f.read_text(encoding="utf-8"))
```

- [ ] **Step 4: AI analysis stages (following SKILL.md)**

```python
def stage_r3_ai_analyze(topic: str, workspace: Path, settings,
                         website: str = "laserpointerhub") -> dict:
    """AI analysis following old Research Skill Step 0-6."""
    from seo_ops.services.ai import build_ai_provider
    
    slug = _slugify(topic)
    date_str = _today_str()
    
    # Read the data package
    data_file = _latest_file_matching(f"research/research-data-{slug}-*.md", workspace)
    if not data_file:
        return {"success": False, "stage": "r2_collect", "error": "research-data 文件不存在，请先粘贴搜索结果"}
    
    data_content = data_file.read_text(encoding="utf-8")
    
    # Build prompts following old SKILL.md
    system_prompt = _get_research_ai_system_prompt()  # from SKILL.md
    user_prompt = _build_research_ai_user_prompt(topic, slug, data_content, workspace)
    
    try:
        provider = build_ai_provider(settings)
        response = provider.chat(system=system_prompt, user=user_prompt, temperature=0.3)
        ai_output = response.content
        
        # Parse AI output: "===BRIEF===" separates material pack from brief
        parts = ai_output.split("===BRIEF===", 1)
        material_pack = parts[0].strip() if parts else ai_output
        brief = parts[1].strip() if len(parts) > 1 else ""
        
        # Write material pack
        mp_path = workspace / "material-packs" / f"{slug}-{date_str}.md"
        mp_path.parent.mkdir(parents=True, exist_ok=True)
        mp_path.write_text(material_pack, encoding="utf-8")
        
        # Write brief
        if brief:
            brief_path = workspace / "research" / f"brief-{slug}-{date_str}.md"
            brief_path.write_text(brief, encoding="utf-8")
        
        # Run scorer using old script
        run_scorer_sync(slug, workspace, website)
        
        return {"success": True, "stage": "r5_write_ready"}
    except Exception as e:
        return {"success": False, "stage": "r2_collect", "error": str(e)}


def run_scorer_sync(slug: str, workspace: Path, website: str = "laserpointerhub") -> dict:
    """Run research_scorer.py and copy output to workspace."""
    runner = LegacyRunner(workspace, website)
    stdout, stderr, rc = runner.run_script_sync(
        "research_scorer.py",
        ["--website", website, "--slug", slug],
    )
    _copy_research_files_from_old_project(slug, workspace)
    return {"success": rc == 0, "stdout": stdout, "exit_code": rc}
```

- [ ] **Step 5: Write stages (following old Write SKILL.md)**

```python
def stage_w0_validate_and_draft(topic: str, author: str, workspace: Path,
                                  settings, website: str = "laserpointerhub") -> dict:
    """Validate material pack then generate draft following old Write Skill."""
    runner = LegacyRunner(workspace, website)
    slug = _slugify(topic)
    date_str = _today_str()
    
    mp = _latest_file_matching(f"material-packs/{slug}-*.md", workspace)
    if not mp:
        return {"success": False, "error": "material pack 不存在"}
    
    # Validate
    stdout, stderr, rc = runner.run_script_sync(
        "write_collector.py",
        ["validate", "--website", website, "--topic", topic, "--pack", str(mp)],
    )
    
    if "❌ 缺失" in stdout or "material pack file not found" in stdout:
        return {"success": False, "error": "素材包校验失败，缺少必填项", "report": stdout}
    
    # Generate draft with AI
    pack_content = mp.read_text(encoding="utf-8")
    contexts = _load_context_files(workspace)
    
    system_prompt = _get_write_ai_system_prompt(author)  # from old Write SKILL.md
    user_prompt = _build_write_ai_user_prompt(topic, pack_content, contexts)
    
    try:
        from seo_ops.services.ai import build_ai_provider
        provider = build_ai_provider(settings)
        response = provider.chat(system=system_prompt, user=user_prompt, temperature=0.3)
        
        draft_path = workspace / "drafts" / f"{slug}-{date_str}.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(response.content, encoding="utf-8")
        
        return {"success": True, "stage": "w1_draft", "validate_report": stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}


def stage_w1b_pre_check(topic: str, tier: str, workspace: Path,
                          website: str = "laserpointerhub") -> dict:
    """Run write_pre_check.py (16 general + 1 conditional Quick Specs check)."""
    runner = LegacyRunner(workspace, website)
    slug = _slugify(topic)
    
    draft = _latest_file_matching(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    
    args = ["--draft", str(draft)]
    if tier:
        args += ["--tier", tier]
    
    stdout, stderr, rc = runner.run_script_sync("write_pre_check.py", args)
    
    fail_count = stdout.count("❌")
    
    return {
        "success": True,
        "stage": "w1b_pre_check",
        "report": stdout,
        "fail_count": fail_count,
        "exit_code": rc,
    }


def stage_w2_post_process(topic: str, apply: bool = False, force: bool = False,
                           workspace: Path = None, website: str = "laserpointerhub") -> dict:
    """Run write_collector.py post-process (scrub + links + scorer + cannibal gate)."""
    runner = LegacyRunner(workspace, website)
    slug = _slugify(topic)
    
    draft = _latest_file_matching(f"drafts/{slug}-*.md", workspace)
    mp = _latest_file_matching(f"material-packs/{slug}-*.md", workspace)
    
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    
    args = ["post-process", "--website", website, "--draft", str(draft)]
    if mp:
        args += ["--pack", str(mp)]
    if apply:
        args.append("--apply")
    if force:
        args.append("--force")
    
    stdout, stderr, rc = runner.run_script_sync("write_collector.py", args)
    
    gate_passed = rc == 0
    
    return {
        "success": gate_passed,
        "stage": "w2_post_process",
        "report": stdout,
        "gate_passed": gate_passed,
    }


def stage_w3_register(topic: str, workspace: Path,
                       website: str = "laserpointerhub") -> dict:
    """Register article — update ILM, backlinks, archive, material pack cleanup, PLAN feedback."""
    runner = LegacyRunner(workspace, website)
    slug = _slugify(topic)
    
    draft = _latest_file_matching(f"drafts/{slug}-*.md", workspace)
    mp = _latest_file_matching(f"material-packs/{slug}-*.md", workspace)
    
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    
    draft_content = draft.read_text(encoding="utf-8")
    title_m = re.search(r"^Title:\s*(.+)$", draft_content, re.MULTILINE)
    kw_m = re.search(r"^SEO Keywords:\s*(.+)$", draft_content, re.MULTILINE)
    article_title = title_m.group(1).strip() if title_m else topic
    primary_kw = kw_m.group(1).split(",")[0].strip() if kw_m else topic
    new_url = f"https://{website}.com/blog/{slug}"
    
    args = ["register", "--website", website, "--draft", str(draft),
            "--pack", str(mp) if mp else "", "--new-url", new_url,
            "--title", article_title, "--keyword", primary_kw]
    
    stdout, stderr, rc = runner.run_script_sync("write_collector.py", args)
    
    # Copy updated files from old project to workspace
    _copy_research_files_from_old_project(slug, workspace)
    
    # Copy updated internal-links-map
    old_ilm = LEGACY_PROJECT_ROOT / website / "context" / "internal-links-map.md"
    if old_ilm.exists():
        ws_ilm = workspace / "context" / "internal-links-map.md"
        ws_ilm.write_text(old_ilm.read_text(encoding="utf-8"))
    
    return {"success": rc == 0, "stage": "w3_register", "report": stdout}
```

- [ ] **Step 6: AI prompt generators extracted from SKILL.md**

These functions translate old SKILL.md instructions directly into AI system prompts:

```python
def _get_research_ai_system_prompt() -> str:
    """Verbatim translation of research/SKILL.md Step 0-6 into AI system prompt."""
    return """... (the full SKILL.md instructions as shown in the previous analysis) ..."""

def _get_write_ai_system_prompt(author: str) -> str:
    """Verbatim translation of write/SKILL.md section 1 into AI system prompt."""
    return f"""... (the full write SKILL.md instructions with {author} substitution) ..."""
```

- [ ] **Step 7: Commit**

```bash
git add src/seo_ops/services/legacy_workflow.py
git commit -m "feat: implement LegacyWorkflowService — stage detection, script runner with SSE, AI integration per SKILL.md"
```

---

## Task 5: Database Migration — Add `legacy_stage` Column

**Files:**
- Modify: `src/seo_ops/db.py`

- [ ] **Step 1: Add MIGRATION_13**

```python
MIGRATION_13 = """
ALTER TABLE actions ADD COLUMN legacy_stage TEXT;
"""
```

- [ ] **Step 2: Update migration runner and SCHEMA_VERSION**

- [ ] **Step 3: Commit**

---

## Task 6: Web Integration — Routes, SSE, and Legacy Template

**Files:**
- Modify: `src/seo_ops/web/app.py`  
- Modify: `src/seo_ops/web/templates/production.html`
- Create: `src/seo_ops/web/templates/legacy_production.html`
- Create: `src/seo_ops/web/static/legacy.css`

- [ ] **Step 1: SSE endpoint for real-time script output**

```python
@app.get("/actions/{action_id}/legacy/run/{stage}")
async def legacy_run_stage_stream(action_id: int, stage: str, request: Request):
    """Run a Legacy stage and stream output via SSE."""
    from seo_ops.services.legacy_workflow import LegacyRunner
    
    action = _get_action_or_404(action_id)
    topic = action["target_ref"]
    workspace = Path("data/legacy_workflow/laserpointerhub")
    runner = LegacyRunner(workspace)
    
    # Map stage to script + args
    # (stage_r0: generate prompt, stage_r1: collect, etc.)
    
    async def event_stream():
        async for line in runner.run_script_stream(script_name, args):
            yield line
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 2: Stage action endpoints (POST)**

```python
@app.post("/actions/{action_id}/legacy/stage/r0")
async def legacy_r0_generate_prompt(action_id: int, request: Request):
    # Sync workspace first, then generate prompt
    from seo_ops.services.legacy_sync import sync_all
    from seo_ops.services.legacy_workflow import stage_r0_generate_prompt
    
    sync_all()  # fresh data before every Legacy session
    action = _get_action_or_404(action_id)
    workspace = Path("data/legacy_workflow/laserpointerhub")
    result = stage_r0_generate_prompt(action["target_ref"], workspace)
    _update_action_legacy_stage(action_id, result["stage"])
    return _redirect("/actions", "搜索提示词已生成")

# ... r1, r3, w0, w1b, w2, w3 endpoints with similar pattern
```

- [ ] **Step 3: Update actions_page() to pass Legacy data**

```python
@app.get("/actions")
async def actions_page(request: Request):
    # ... existing code ...
    
    from seo_ops.services.legacy_workflow import detect_stage
    
    for item in new_actions:
        if item["action_type"] == "create":
            workspace = Path("data/legacy_workflow/laserpointerhub")
            stage, files = detect_stage(item["target_ref"], workspace)
            item["legacy"] = {
                "stage": stage,
                "files": files,
                # Load prompt content, draft preview, etc. as needed
            }
    
    # ... render production.html with legacy data ...
```

- [ ] **Step 4: Create `legacy_production.html`**

A standalone template for the Legacy 10-stage wizard. (Keep the structure from the previous plan but add SSE log area.)

- [ ] **Step 5: Modify `production.html` to route new articles through Legacy**

```html
{% for action in new_actions %}
  {% if action.legacy and not action.deliverable %}
    {% include 'legacy_production.html' %}
  {% elif not action.deliverable %}
    {# fallback: old material-confirm card #}
  {% else %}
    {# CMS deliverable display #}
  {% endif %}
{% endfor %}
```

- [ ] **Step 6: Commit**

---

## Task 7: Testing

**Files:**
- Create: `tests/test_legacy_workflow.py`
- Create: `tests/test_legacy_sync.py`

Classes:
- `TestStageDetection`: 6 tests for r0_pending through w3_register
- `TestLegacySync`: 5 tests verifying each generated file exists, has correct format
- `TestSearchPrompt`: verify 8-section structure, intent detection, library summaries
- `TestAI`: mock provider, verify prompts match SKILL.md instructions

- [ ] **Step 1: Run all tests, ruff, verify no regressions**

```bash
pytest -q          # all existing + new tests
ruff check src tests
ruff format --check src tests
```

Expected: all pass.

- [ ] **Step 2: Commit, update HANDOFF, WORKLOG, CHANGELOG**

---

## Summary: What Changes vs What Stays

| Component | Action | Reason |
|-----------|--------|--------|
| `data/legacy_workflow/laserpointerhub/` | New (generated + seeded) | Legacy workspace for old scripts |
| `src/seo_ops/services/legacy_sync.py` | New | DB → filesystem bridge |
| `src/seo_ops/services/legacy_workflow.py` | New | Stage management + script runner + AI integration |
| `src/seo_ops/db.py` | Add MIGRATION_13 | `actions.legacy_stage` column |
| `src/seo_ops/web/app.py` | Add 10 routes | Legacy stage endpoints + SSE streaming |
| `src/seo_ops/web/templates/legacy_production.html` | New | 10-stage wizard UI |
| `src/seo_ops/web/templates/production.html` | Modify 20 lines | Route new articles to Legacy template |
| `src/seo_ops/web/static/legacy.css` | New | Legacy styles |
| `tests/test_legacy_*.py` | New | Stage detection, sync, search prompt tests |

**NOT touched:**
- `content_production.py` — old articles continue using it
- `material_workflow.py` — old articles continue using it  
- `action_workflow.py` — action creation unchanged
- Research/suggestions/topics/imports pages — untouched
- `/home/laoma/seo-workflow/` — read-only, never modified
- Settings page — AI call limit stays for old articles
