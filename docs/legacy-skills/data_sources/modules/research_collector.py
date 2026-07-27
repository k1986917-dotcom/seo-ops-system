#!/usr/bin/env python3
"""RESEARCH data collector — three-stage pipeline for SEO research.

Stage 0 (generate-prompt): Read context files, identify data gaps,
  generate a search AI prompt tailored to the topic + gaps.
  Output: search-prompt-[slug]-[date].md

Stage 1 (collect): Parse search results + seo-data-manual + material
  libraries + cannibalization check → structured data package.
  Output: research-data-[slug]-[date].md

Stage 2 (archive): Read the final material pack, extract new [search]
  items, deduplicate by URL, append to libraries, update usage counts.
  Output: archive report to stdout.

Usage:
  python3 research_collector.py generate-prompt --website laserpointerhub --topic "best laser pointer under $200"
  python3 research_collector.py collect --website laserpointerhub --topic "best laser pointer under $200"
  python3 research_collector.py archive --website laserpointerhub --pack "material-packs/best-laser-pointer-under-200-2026-06-02.md"
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parents[2]

# Shared pipeline primitives (topic-context handoff, etc.)
try:
    import seo_common
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_common', Path(__file__).resolve().parent / 'seo_common.py')
    seo_common = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_common)

try:
    import seo_config
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    seo_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_config)


# _date_str / _extract_section / _extract_table were local duplicates of
# seo_common.today_str / extract_section / extract_table. Aliased here so the
# existing internal call sites keep working without a wide rename.
_date_str = seo_common.today_str
_extract_section = seo_common.extract_section
_extract_table = seo_common.extract_table


# ═══════════════════════════════════════════════════════════
# Stage 0: Generate Search Prompt
# ═══════════════════════════════════════════════════════════

def _detect_intent(keyword: str) -> str:
    """Return Chinese intent label (consistent with seo_common.classify_value)."""
    return seo_common.classify_value(keyword)[0]


def _identify_gaps(website_dir: Path) -> Dict:
    manual_path = website_dir / 'context' / 'seo-data-manual.md'
    gaps = {
        'a1_empty': True, 'a2_empty': True,
        'published_count': 0,
    }

    if manual_path.exists():
        content = manual_path.read_text(encoding='utf-8')
        a1 = _extract_table(content, '### A1.', '### A2.')
        gaps['a1_empty'] = len(a1) < 3
        a2 = _extract_table(content, '### A2.', '### A3.')
        gaps['a2_empty'] = len(a2) < 3

    index_path = website_dir / 'published' / 'published-index.json'
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding='utf-8'))
            gaps['published_count'] = len(data) if isinstance(data, list) else 0
        except Exception:
            pass

    research_dir = website_dir / 'research'
    if research_dir.exists():
        for f in research_dir.glob('search-results-*.md'):
            gaps['search_results_exist'] = True
            break

    return gaps


def generate_prompt(website: str, topic: str) -> str:
    website_dir = BASE_DIR / website
    if not website_dir.exists():
        print(f'Error: website directory not found: {website_dir}', file=sys.stderr)
        sys.exit(1)

    slug = seo_common.slugify(topic)
    intent = _detect_intent(topic)
    date_str = _date_str()
    topic_tags = [w.lower() for w in re.split(r'[\s\-_,;/]+', topic) if len(w) > 2]

    # ── Gather context: what do we already know? ──
    seo_data = _read_seo_manual(website_dir)
    gsc_kw = seo_data.get('gsc_opportunities', []) if seo_data.get('exists') else []

    # Published articles on similar topics
    pi_path = website_dir / 'published' / 'published-index.json'
    published_titles = []
    published_tags = set()
    if pi_path.exists():
        try:
            data = json.loads(pi_path.read_text(encoding='utf-8'))
            for entry in (data if isinstance(data, list) else []):
                title = entry.get('title', '') or entry.get('slug', '')
                published_titles.append(title[:80])
                for t in entry.get('tags', []):
                    published_tags.add(t.lower())
        except Exception:
            pass

    # Material library summaries — what pain points / cases / citations already exist
    def _lib_summary(lib_path, label):
        if not lib_path.exists():
            return ''
        content = lib_path.read_text(encoding='utf-8')
        entries = re.findall(r'\n## (.+)', content)
        skip_words = ['标签索引', '填写规则', '外链库', '常见权威', '索引', '规则']
        real_entries = [e.strip() for e in entries if not any(s in e.lower() for s in skip_words)]
        if not real_entries:
            return ''
        sample = [e[:60] for e in real_entries[:5]]
        return f'### Existing {label} ({len(real_entries)} total)\n' + '\n'.join(f'- {s}' for s in sample)

    # External sources use a different format (table-based), handle separately
    def _source_summary():
        lib_path = website_dir / 'context' / 'external-sources-library.md'
        if not lib_path.exists():
            return ''
        content = lib_path.read_text(encoding='utf-8')
        # Count table rows with URLs
        urls = re.findall(r'\|\s*\d+\s*\|\s*(https?://\S+)', content)
        if not urls:
            return ''
        # Extract some sample descriptions
        samples = re.findall(r'\|\s*(\d+)\s*\|\s*(https?://\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+)\s*\|', content)
        lines = [f'### Existing Authoritative Citations ({len(urls)} total)']
        for s in samples[:3]:
            lines.append(f'- [{s[2]}] {s[3].strip()[:50]}')
        return '\n'.join(lines)

    pain_summary = _lib_summary(website_dir / 'context' / 'pain-points-library.md', 'User Pain Points')
    case_summary = _lib_summary(website_dir / 'context' / 'case-studies-library.md', 'Case Studies')
    source_summary = _source_summary()

    # Brand context
    brand_info = ''
    bv_path = website_dir / 'context' / 'brand-voice.md'
    if bv_path.exists():
        brand_info = bv_path.read_text(encoding='utf-8')[:500]

    # ── Build a HIGHLY SPECIFIC prompt ──

    # What we know section
    what_we_know = []
    if gsc_kw:
        top_3 = [o['keyword'] for o in gsc_kw[:3] if o.get('impressions', 0) > 100]
        if top_3:
            what_we_know.append(f"GSC keywords we rank for: {', '.join(top_3)}")
    if published_titles:
        what_we_know.append(f"Published articles: {len(published_titles)} total. Topics include: {', '.join(published_titles[:5])}")
    if published_tags:
        what_we_know.append(f"Content tags already covered: {', '.join(list(published_tags)[:10])}")

    what_we_need = []
    if not gsc_kw or not any(o.get('impressions', 0) > 100 for o in gsc_kw):
        what_we_need.append('⚠️ No GSC ranking data — need SERP analysis to understand what ranks')
    else:
        what_we_need.append('GSC data shows strong search demand. Need to understand what TOPIC ANGLE will differentiate our article from existing top 5 results.')
    what_we_need.append('Focus on finding information gaps — what do the top 5 articles NOT cover?')
    if intent == 'commercial':
        what_we_need.append('Commercial article — prioritize spec comparisons, buyer decision factors, and red flags (do NOT rely on pricing data from search AI, prices are usually inaccurate).')
    else:
        what_we_need.append('Informational article — prioritize real user pain points, practical how-to advice, authority sources.')

    prompt = f"""You are helping me research for an SEO article about "{topic}" on {website}. I need comprehensive, real, verifiable data. Do NOT fabricate anything — say "not found" if you cannot find it.

## What We Already Know

{"\n".join(what_we_know)}

## What We Need You To Find

{"\n".join(what_we_need)}

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
- Common H2 topics ALL top 5 cover (must-have sections — we CANNOT skip these):
- Topics only 1-2 cover (differentiation opportunities for us):
- Topics NO ONE covers (our unique angle — THIS is where we win):
- Average word count of top 5:
- SERP composition (brand blogs vs independent reviews vs forums vs government sites):

---

## Section 2: User Pain Points

Find 8-10 REAL frustrations/complaints/questions about {topic}. Search Reddit, Quora, forums, Amazon reviews.

**Important:** 
{pain_summary if pain_summary else 'We have a pain point library. Find NEW, different pain points — NOT the same ones we already know about.'}

Format each as:
- Pain point: [describe in user's language]
- Source: [URL]
- Quote: "[actual user words]"

Prioritize specific, emotional complaints with actual numbers/experiences.

---

## Section 3: Market Data

Current market landscape for {topic} as of {datetime.now().strftime('%B %Y')}:

- Product models, key specs, and features — list specific models with what differentiates them
- Pricing tiers (budget, mid-range, premium) — approximate ranges only, do NOT rely on exact prices
- Recent trends, new products, technology shifts
- Include source URLs for all claims

---

## Section 4: Case Studies / Real Stories

Find 3-5 real user stories. 

{case_summary if case_summary else 'We have a case study library. Find DIFFERENT, fresh cases we have not collected yet.'}

Format:
- Story: [what happened — specific details, names, numbers, results]
- Source: [URL]
- Use in article: [what point this illustrates]

---

## Section 5: Authoritative Citations

Find 3-5 authoritative sources (.gov, .edu, academic journals, industry standards, official reports). 

{source_summary if source_summary else 'We have a pre-verified citation library. Find NEW citations we have not collected yet.'}

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
- Unique angle we can bring (what do WE know or can do that they can't?):
- What single piece of information, if included, would make our article the definitive resource:

No generic advice like "go deeper" or "add more detail." Name specific facts, comparisons, or angles.

---

## Section 7: PAA Questions

List 15+ "People Also Ask" questions related to {topic}. Group by subtopic.

---

## Section 8: People Also Ask (Structured)

Section 7 listed PAA questions briefly. Now turn them into STRUCTURED, sourceable entries.

For "{topic}" and 5-8 closely related queries, capture REAL "People Also Ask" boxes
that appear in Google search results. Use verbatim phrasing from the SERP — do NOT paraphrase.
Provide 10-15 PAA questions in this exact format:

- **[search] <PAA question text verbatim from Google's PAA box>**
- Source: [where this question was found](URL or "search AI")
- Answer hint: brief answer context if available

Requirements:
- Each question MUST come from a real Google "People Also Ask" box (or Reddit/Quora real user question if PAA unavailable). NO template/generic questions like "What is X?", "How does X work?" unless they literally appear in the SERP.
- Include the SERP source URL (the page where you observed the PAA question) when possible; otherwise use "search AI" as the source label.
- "Answer hint" should be 1-2 sentences of factual context — NOT the full answer for the article.
- Deduplicate identical questions across queries; keep the most authoritative source URL.
- Group by subtopic if helpful, but each entry must still follow the bullet format above.
"""

    output_path = website_dir / 'research' / f'search-prompt-{slug}-{date_str}.md'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding='utf-8')

    print(f'✅ 搜索提示词: {output_path}', file=sys.stderr)
    return str(output_path)


# ═══════════════════════════════════════════════════════════
# Stage 1: Collect Data
# ═══════════════════════════════════════════════════════════

def _parse_search_results(filepath: Path) -> Dict:
    """Best-effort parsing of search AI results into structured sections."""
    content = filepath.read_text(encoding='utf-8')
    result = {
        'filepath': str(filepath),
        'serp': '', 'pain_points': '', 'market_data': '',
        'cases': '', 'citations': '', 'gaps': '', 'paa': '', 'paa_data': '',
        'parsed_sections': [],
        '_parse_warning': ''
    }

    section_map = {
        # More-specific "Section N:" patterns first so they outrank keyword aliases
        'section 1': 'serp', 'serp': 'serp', 'serp analysis': 'serp',
        'section 2': 'pain_points', 'pain point': 'pain_points', 'user pain': 'pain_points',
        'section 3': 'market_data', 'market data': 'market_data', 'market': 'market_data',
        'section 4': 'cases', 'case stud': 'cases', 'real stor': 'cases',
        'section 5': 'citations', 'authoritative': 'citations', 'citation': 'citations',
        'section 6': 'gaps', 'information gap': 'gaps', 'info gap': 'gaps',
        'section 7': 'paa', 'paa question': 'paa',
        'section 8': 'paa_data', 'paa structured': 'paa_data',
        'paa': 'paa', 'people also ask': 'paa',
    }

    sections = re.split(r'\n(?=##\s)', content)
    for section in sections:
        header_line = section.split('\n')[0].lower()
        matched_key = None
        for pattern, key in section_map.items():
            if pattern in header_line:
                matched_key = key
                break
        if matched_key:
            result[matched_key] = section.strip()
            result['parsed_sections'].append(matched_key)

    parsed = set(result['parsed_sections'])
    expected = {'serp', 'pain_points', 'cases', 'citations', 'gaps', 'paa'}
    missing = expected - parsed
    if missing and len(content) > 500:
        result['_parse_warning'] = f'Sections not parsed: {", ".join(sorted(missing))}. Check format matches "## Section N:" pattern.'

    if not result['parsed_sections'] and len(content) > 200:
        result['serp'] = content
        result['parsed_sections'] = ['serp']
        result['_parse_warning'] = 'No section headers found — entire content treated as SERP. Check if search results follow the Section 1-7 format.'

    return result


def _read_seo_manual(website_dir: Path) -> Dict:
    """Extract key data from seo-data-manual.md."""
    manual_path = website_dir / 'context' / 'seo-data-manual.md'
    if not manual_path.exists():
        return {'exists': False}

    content = manual_path.read_text(encoding='utf-8')

    data = {'exists': True}

    # Data freshness
    m = re.search(r'20\d{2}-\d{2}-\d{2}', content[:500])
    data['updated'] = m.group(0) if m else '未知'

    # GSC data from A sections
    gsc_opp = []
    a1 = _extract_table(content, '### A1.', '### A2.')
    for row in a1[:20]:
        if len(row) >= 5:
            try:
                imp = int(row[3].replace(',', '')) if row[3].replace(',', '').isdigit() else 0
                gsc_opp.append({
                    'keyword': row[1], 'impressions': imp,
                    'ctr': row[4], 'position': row[5] if len(row) > 5 else '—',
                })
            except (ValueError, IndexError):
                pass

    a2 = _extract_table(content, '### A2.', '### A3.')
    for row in a2[:10]:
        if len(row) >= 3:
            try:
                pos = float(row[2]) if row[2] and row[2] != '—' else 99
                gsc_opp.append({
                    'keyword': row[1], 'impressions': int(row[3].replace(',', '')) if len(row) > 3 else 0,
                    'position': pos, 'type': 'quick_win',
                })
            except (ValueError, IndexError):
                pass

    data['gsc_opportunities'] = gsc_opp

    # E1: derive from A section — keywords with real GSC impressions
    e1_keywords = []
    for row in a1[:20]:
        if len(row) >= 4:
            try:
                imp = int(row[3].replace(',', '')) if row[3].replace(',', '').isdigit() else 0
                if imp > 0:
                    e1_keywords.append(row[1])
            except (ValueError, IndexError):
                pass
    data['e1_keywords'] = e1_keywords[:20]

    # E2: manual-verified keywords (now under ## E 部分)
    e2_section = _extract_section(content, '### E2.', '')
    data['e2_keywords'] = [kw.strip() for kw in re.findall(r'\|\s*([^|]+?)\s*\|', e2_section)
                           if kw.strip() and '关键词' not in kw and '—' not in kw][:30]

    # Data sufficiency: only check A section now
    has_a1 = len(a1) >= 3
    missing = []
    if not has_a1:
        missing.append('A1 (GSC Top 20)')
    data['missing_sections'] = missing
    data['sufficiency'] = 'high' if not missing else 'low'

    return data


def _match_library(library_path: Path, topic_tags: List[str], max_items: int = 10) -> List[Dict]:
    """Match items from a material library by topic tags, sorted by usage count (ascending)."""
    if not library_path.exists():
        return []

    content = library_path.read_text(encoding='utf-8')
    items = []

    # ── Parse legacy table rows (external-sources-library.md uses | # | url | label | desc |) ──
    # S3: table-format entries must also enter the candidate pool so they can be
    # served as [library] candidates and avoid duplicate re-archival.
    for row_m in re.finditer(
        r'^\|\s*\d+\s*\|\s*(https?://[^|\s]+)\s*\|\s*([^|]+)\s*\|\s*([^|\n]*)\|',
        content,
        re.MULTILINE,
    ):
        url = _normalize_url(row_m.group(1))
        label = row_m.group(2).strip()
        desc = row_m.group(3).strip()
        tags_fb = re.findall(r'`([^`]+)`', desc)
        tags = [t.strip().lower() for t in tags_fb] if tags_fb else [w.lower() for w in re.split(r'[\s\-_,;/]+', label) if len(w) > 2]
        match_score = 0
        for t in (x.lower() for x in topic_tags):
            for lt in tags:
                if t in lt or lt in t:
                    match_score += 1
                    break
        if match_score > 0 or not topic_tags:
            items.append({
                'header': f'{label}',
                'url': url,
                'tags': tags,
                'usage_count': 0,
                'match_score': match_score,
                'full_entry': row_m.group(0).strip(),
            })

    # Parse library entries (## heading pattern)
    entries = re.split(r'\n(?=##\s)', content)
    for entry in entries:
        if not entry.strip().startswith('##'):
            continue

        header = entry.split('\n')[0].replace('##', '').strip()
        url_match = re.search(r'\*\*来源\*\*:\s*\[([^\]]*)\]\((https?://[^)]+)\)', entry)
        if url_match:
            url = url_match.group(2)
        else:
            url_plain = re.search(r'\*\*来源(?:\s*URL)?\*\*:\s*(https?://\S+)', entry)
            url = url_plain.group(1) if url_plain else ''
        tags_raw = re.search(r'\*\*标签\*\*:\s*(.+)', entry)
        if tags_raw:
            tags = [t.strip().strip('`').lower() for t in re.findall(r'`([^`]+)`', tags_raw.group(1))]
            if not tags:
                tags = [t.strip().lower() for t in tags_raw.group(1).split(',')]
        else:
            tags_fb = re.search(r'\*\*适用文章\*\*:\s*(.+)', entry)
            tags = [t.strip().lower() for t in tags_fb.group(1).split(',')] if tags_fb else []
        count_match = re.search(r'\*\*使用次数\*\*:\s*(\d+)', entry)
        usage_count = int(count_match.group(1)) if count_match else 0

        # Check topic tag overlap
        topic_lower = [t.lower() for t in topic_tags]
        match_score = 0
        for t in topic_lower:
            for lt in tags:
                if t in lt or lt in t:
                    match_score += 1
                    break

        if match_score > 0 or not topic_tags:
            items.append({
                'header': header,
                'url': url,
                'tags': tags,
                'usage_count': usage_count,
                'match_score': match_score,
                'full_entry': entry.strip(),
            })

    # Sort: match_score desc, then usage_count asc (prefer relevant but less-used)
    items.sort(key=lambda x: (-x['match_score'], x['usage_count']))

    # Filter out items with usage_count >= 3 (unless all items are used up)
    filtered = [it for it in items if it['usage_count'] < 3]
    if not filtered:
        filtered = items

    return filtered[:max_items]


def _run_cannibalization(website: str, tags: List[str]) -> str:
    """Run the cannibalization checker in-process via seo_common.cannibal_pairs.

    Was a subprocess shell-out to cannibalization_checker.py with --tags filter.
    Unified to the single seo_common entry point so thresholds stay consistent
    (seo_config.CANNIBAL_THRESHOLD_LOW) and the published index is cached.
    """
    tag_set = set(t.strip().lower() for t in tags[:10] if t.strip())
    try:
        data = seo_common.cannibal_pairs(website, threshold=seo_config.CANNIBAL_THRESHOLD_LOW,
                                         top_n=10_000)
        pairs = data.get('all_pairs', data.get('pairs', []))
        if tag_set:
            pairs = [p for p in pairs
                     if any(t in p.get('slug_a', '').lower() or t in p.get('slug_b', '').lower()
                            for t in tag_set)]
        if not pairs:
            return '✅ No cannibalization risks detected.'
        # Format mirrors cannibalization_checker.format_report so downstream
        # parsers (which read the report text) are unaffected.
        lines = [
            '=' * 60,
            'CANNIBALIZATION CHECK — TF-IDF Semantic Analysis',
            '=' * 60,
            '',
            f"Threshold: {seo_config.CANNIBAL_THRESHOLD_LOW}  |  Pairs flagged: {len(pairs)}",
            '',
        ]
        for r in pairs:
            lines.append(f"{r['risk']}  similarity={r['similarity']}")
            lines.append(f"    A: {r['slug_a']}")
            lines.append(f"    B: {r['slug_b']}")
            lines.append('')
        return '\n'.join(lines)[:2000]
    except Exception as e:
        return f'⚠️ 蚕食检测失败: {e}'


def collect_data(website: str, topic: str, search_file: Optional[str] = None) -> str:
    website_dir = BASE_DIR / website
    if not website_dir.exists():
        print(f'Error: website directory not found: {website_dir}', file=sys.stderr)
        sys.exit(1)

    slug = seo_common.slugify(topic)
    date_str = _date_str()
    topic_tags = [w.lower() for w in re.split(r'[\s\-_,;/]+', topic) if len(w) > 2]

    # ── Phase B: topic-context handoff ──
    # Inherit PLAN's decision if it exists; otherwise create a heuristic one so the
    # rest of the pipeline still has intent/tier. RESEARCH can run standalone
    # (no PLAN), so missing context must NEVER error — it degrades to heuristic.
    # Try exact slug first, then fallback to plan scorer's slug (which uses the
    # shorter seed form — e.g. 'buying-guide' vs 'handheld-inkjet-printer-buying-guide').
    ctx = seo_common.read_topic_context(website_dir, slug)
    if not ctx or ctx.get('source') != 'plan':
        # Also try the last N tokens of the topic (shorter slug form)
        if len(topic.split()) > 3:
            short_slug = seo_common.slugify(' '.join(topic.split()[-3:]))
            if short_slug != slug:
                ctx = seo_common.read_topic_context(website_dir, short_slug)
    if ctx and ctx.get('source') == 'plan':
        print(f"       ✅ 继承 PLAN context: intent={ctx.get('intent')} tier={ctx.get('tier')}", file=sys.stderr)
    else:
        ctx = {
            'slug': slug, 'topic': topic, 'source': 'heuristic',
            'intent': _detect_intent(topic), 'tier': seo_common.detect_tier(topic),
            'primary_keyword': topic, 'signals': {}, 'cannibal_risk': '',
        }
        seo_common.write_topic_context(
            website_dir, slug, topic=topic, source='heuristic',
            intent=ctx['intent'], tier=ctx['tier'], primary_keyword=topic)
        print('       ℹ️ 无 PLAN context，使用启发式推断（source=heuristic）', file=sys.stderr)

    # ── Step 1: Data readiness check ──
    print('[1/6] 数据就绪检查...', file=sys.stderr)

    readiness = {
        'topic': topic,
        'slug': slug,
        'website': website,
        'date': date_str,
        'topic_context': ctx,
    }

    seo_data = _read_seo_manual(website_dir)
    readiness['seo_data'] = seo_data

    search_results = None
    search_path = None
    if search_file:
        search_path = Path(search_file)
        if not search_path.exists():
            search_path = website_dir / 'research' / search_file
        if not search_path.exists():
            print(f'⚠️ 搜索结果文件不存在: {search_file}', file=sys.stderr)
            search_path = None

    if search_path and search_path.exists():
        search_results = _parse_search_results(search_path)
        readiness['search_results'] = search_results
        readiness['search_file'] = str(search_path)
        print(f'       ✅ 搜索结果: {search_path.name}', file=sys.stderr)
        if search_results.get('_parse_warning'):
            print(f'       ⚠️ 解析警告: {search_results["_parse_warning"]}', file=sys.stderr)
    else:
        readiness['search_results'] = None
        readiness['search_file'] = None
        print('       ℹ️ 无搜索结果，A/C/E 将从素材库匹配，B/D/F 需手动填写', file=sys.stderr)

    # ── Step 2: Read context files ──
    print('[2/6] 读取上下文文件...', file=sys.stderr)

    # Target keywords
    tk_path = website_dir / 'context' / 'target-keywords.md'
    readiness['target_keywords'] = tk_path.read_text(encoding='utf-8')[:3000] if tk_path.exists() else ''

    # Published index
    pi_path = website_dir / 'published' / 'published-index.json'
    published_articles = []
    published_tags = {}
    if pi_path.exists():
        try:
            data = json.loads(pi_path.read_text(encoding='utf-8'))
            for entry in data if isinstance(data, list) else []:
                tags = entry.get('tags', [])
                published_articles.append({
                    'slug': entry.get('slug', ''),
                    'title': entry.get('title', ''),
                    'tags': tags,
                })
                for t in tags:
                    published_tags[t] = published_tags.get(t, 0) + 1
        except Exception:
            pass
    readiness['published'] = {
        'total': len(published_articles),
        'articles': published_articles[:20],
        'top_tags': sorted(published_tags.items(), key=lambda x: x[1], reverse=True)[:15],
    }

    # ── Step 3: Cannibalization check ──
    print('[3/6] 蚕食预检...', file=sys.stderr)
    cannibal = ''
    if published_articles:
        cannibal = _run_cannibalization(website, topic_tags)
    else:
        cannibal = '✅ 无已发布文章，跳过蚕食检测'
    readiness['cannibalization'] = cannibal

    # ── Step 4: Material library matching ──
    print('[4/6] 素材库标签匹配...', file=sys.stderr)

    pain_items = _match_library(website_dir / 'context' / 'pain-points-library.md', topic_tags, max_items=10)
    case_items = _match_library(website_dir / 'context' / 'case-studies-library.md', topic_tags, max_items=8)
    source_items = _match_library(website_dir / 'context' / 'external-sources-library.md', topic_tags, max_items=8)

    readiness['library_matches'] = {
        'pain_points': [{'header': it['header'], 'url': it['url'], 'usage_count': it['usage_count'],
                          'match_score': it['match_score'], 'source': 'library'} for it in pain_items],
        'case_studies': [{'header': it['header'], 'url': it['url'], 'usage_count': it['usage_count'],
                          'match_score': it['match_score'], 'source': 'library'} for it in case_items],
        'external_sources': [{'header': it['header'], 'url': it['url'], 'usage_count': it['usage_count'],
                              'match_score': it['match_score'], 'source': 'library'} for it in source_items],
    }

    # ── Step 5: Pre-fill material pack ──
    print('[5/6] 预填素材包...', file=sys.stderr)

    material_pack = _build_material_pack(topic, slug, date_str, website, readiness, search_results)

    # ── Step 6: Build data package ──
    print('[6/6] 组装数据包...', file=sys.stderr)

    data_package = _format_data_package(readiness, material_pack)

    output_path = website_dir / 'research' / f'research-data-{slug}-{date_str}.md'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(data_package, encoding='utf-8')

    # JSON sidecar for deterministic scoring (research_scorer.py) — mirrors PLAN
    json_path = website_dir / 'research' / f'research-data-{slug}-{date_str}.json'
    try:
        json_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2, default=str),
                             encoding='utf-8')
        print(f'✅ JSON: {json_path}', file=sys.stderr)
    except Exception as e:
        print(f'⚠️ JSON sidecar 写入失败: {e}', file=sys.stderr)

    print(f'✅ 数据包: {output_path}', file=sys.stderr)
    return str(output_path)


def _build_material_pack(topic: str, slug: str, date_str: str, website: str,
                          readiness: Dict, search_results: Optional[Dict]) -> str:
    """Build a pre-filled material pack template."""
    # Phase B: inherit intent/tier from topic-context (PLAN or heuristic) instead of
    # re-guessing here. Keeps the three stages consistent (S1/R7).
    ctx = readiness.get('topic_context', {}) if readiness else {}
    intent = ctx.get('intent') or _detect_intent(topic)
    tier = ctx.get('tier')
    if not tier:
        tier = 'Pillar Page' if len(topic.split()) <= 2 else 'Cluster Content'
        if any(w in topic.lower() for w in ['best', 'top', 'review', 'vs', 'comparison']):
            tier = 'Product Roundup'

    lines = []
    lines.append(f'# 写作素材包 — {topic}')
    lines.append(f'> 保存路径: `{website}/material-packs/{slug}-{date_str}.md`')
    lines.append(f'> 生成时间: {date_str}')
    lines.append('')

    # Basic info
    lines.append('## 基本信息')
    lines.append(f'- **文章 slug**: `{slug}`')
    lines.append(f'- **目标主关键词**: {topic}')
    lines.append(f'- **文章意图**: {intent}')
    lines.append(f'- **页面层级**: {"📌 PILLAR PAGE" if tier == "Pillar Page" else "🔗 CLUSTER CONTENT" if tier == "Cluster Content" else "📋 PRODUCT ROUNDUP"}')
    lines.append(f'- **搜索结果**: {"有" if search_results else "无"}')
    lines.append('')

    # A: Pain points
    lines.append('## A. 用户痛点 (≥3)')
    lib_a = readiness.get('library_matches', {}).get('pain_points', [])
    if search_results and search_results.get('pain_points'):
        lines.append('> 来源: [search] + [library]')
        lines.append(search_results['pain_points'][:1500])
        lines.append('')
    if lib_a:
        lines.append('> 素材库匹配:')
        for it in lib_a[:5]:
            lines.append(f'- [library] {it["header"]} | 使用次数:{it["usage_count"]} | {it["url"]}')
        lines.append('')
    if not lib_a and not (search_results and search_results.get('pain_points')):
        lines.append('> ⚠️ 无搜索结果且素材库无匹配，请手动填写')
        lines.append('- 痛点1: [描述] 来源: [URL]')
        lines.append('- 痛点2: [描述] 来源: [URL]')
        lines.append('- 痛点3: [描述] 来源: [URL]')
        lines.append('')

    # B: Market data
    lines.append('## B. 实时市场数据')
    if search_results and search_results.get('market_data'):
        lines.append('> 来源: [search]')
        lines.append(search_results['market_data'][:2000])
        lines.append('')
    else:
        lines.append('> ⚠️ 需要手动填写价格/规格/竞品数据')
        lines.append('- 目标产品当前价格区间: 来源: [URL]')
        lines.append('- 主流竞品规格对比: 来源: [URL]')
        lines.append('')

    # C: Case studies
    lines.append('## C. 案例素材 (≥2)')
    lib_c = readiness.get('library_matches', {}).get('case_studies', [])
    if search_results and search_results.get('cases'):
        lines.append('> 来源: [search] + [library]')
        lines.append(search_results['cases'][:1500])
        lines.append('')
    if lib_c:
        lines.append('> 素材库匹配:')
        for it in lib_c[:4]:
            lines.append(f'- [library] {it["header"]} | 使用次数:{it["usage_count"]} | {it["url"]}')
        lines.append('')
    if not lib_c and not (search_results and search_results.get('cases')):
        lines.append('> ⚠️ 无搜索结果且素材库无匹配，请手动填写')
        lines.append('- 案例1: [描述] 来源: [URL]')
        lines.append('- 案例2: [描述] 来源: [URL]')
        lines.append('')

    # D: Information gaps
    lines.append('## D. 信息增量')
    if search_results and search_results.get('gaps'):
        lines.append('> 来源: [search]')
        lines.append(search_results['gaps'][:1500])
        lines.append('')
    else:
        lines.append('> ⚠️ 需要手动填写独特切入点')
        lines.append('- 信息1: [描述] 来源: [URL]')
        lines.append('- 信息2: [描述] 来源: [URL]')
        lines.append('')

    # E: Authoritative citations
    lines.append('## E. 权威引用 (≥2)')
    lib_e = readiness.get('library_matches', {}).get('external_sources', [])
    if search_results and search_results.get('citations'):
        lines.append('> 来源: [search] + [library]')
        lines.append(search_results['citations'][:1500])
        lines.append('')
    if lib_e:
        lines.append('> 素材库匹配:')
        for it in lib_e[:4]:
            lines.append(f'- [library] {it["header"]} | 使用次数:{it["usage_count"]} | {it["url"]}')
        lines.append('')
    if not lib_e and not (search_results and search_results.get('citations')):
        lines.append('> ⚠️ 需要手动填写权威引用')
        lines.append('- 数据1: [描述] 来源: [URL]')
        lines.append('- 数据2: [描述] 来源: [URL]')
        lines.append('')

    # F: Competitor analysis
    lines.append('## F. 竞品内容分析')
    if search_results and search_results.get('serp'):
        lines.append('> 来源: [search]')
        lines.append(search_results['serp'][:2000])
        lines.append('')
    else:
        lines.append('> ⚠️ 需要手动填写或运行 /research-serp')
        lines.append('- 排名第1文章: [摘要] URL: [URL]')
        lines.append('- 排名第2-3文章: [摘要]')
        lines.append('- 未覆盖的内容空白: [描述]')
        lines.append('')

    # G. PAA 真问句 (from search results Section 8)
    lines.append('## G. PAA 真问句 (≥1)')
    lines.append('> FAQ 段至少 1 个问句必须命中 G 类。命中 G 类 = 来自 Google 真实 "People Also Ask" 框或 Reddit/Quora 真实提问（非模板问句）')
    paa_content = ''
    if search_results:
        paa_content = search_results.get('paa_data') or ''
        if not paa_content and search_results.get('paa'):
            paa_content = search_results.get('paa', '')  # fallback: old Section 7 simple list
    if paa_content:
        lines.append('> 来源: [search]')
        lines.append(paa_content[:2500])
        lines.append('')
    else:
        lines.append('> ⚠️ 搜索结果无 Section 8 PAA 数据 → 手动补真实问句并标 `[search]`（不可用模板问句填充）')
        lines.append('- **[search] <PAA question text>**')
        lines.append('- Source: [来源页](URL or "search AI")')
        lines.append('- Answer hint: 简要答案上下文 (1-2 句)')
        lines.append('')

    lines.append('## H. 作者/品牌信息')
    lines.append('> 手动填写')
    lines.append('- 作者名: [填写]')
    lines.append('- 相关经验: [1-2句]')
    lines.append('')

    # Priority order
    lines.append('## 素材包填写建议')
    if intent == 'commercial':
        lines.append('> 商业调研型: B实时价格 > C案例 > F竞品分析 > E权威引用')
    else:
        lines.append('> 信息型: A用户痛点 > D信息增量 > E权威引用 > G PAA真问句')

    return '\n'.join(lines)


def _format_data_package(readiness: Dict, material_pack: str) -> str:
    """Format the complete data package as Markdown for AI consumption."""
    lines = []
    r = readiness
    seo = r.get('seo_data', {})
    pub = r.get('published', {})

    lines.append(f"# RESEARCH 数据包 — {r['topic']}")
    lines.append(f"> 网站: {r['website']} | 日期: {r['date']} | Slug: {r['slug']}")
    lines.append('')

    # Data readiness
    lines.append('## 📊 数据就绪状态')
    sufficiency = seo.get('sufficiency', 'unknown') if seo.get('exists') else 'none'
    missing = seo.get('missing_sections', [])
    lines.append(f'- SEO数据: {"✅" if seo.get("exists") else "❌"} (完整度: {sufficiency})')
    if missing:
        lines.append(f'- 缺失: {", ".join(missing)}')
    lines.append(f'- 搜索结果: {"✅ " + r.get("search_file", "") if r.get("search_results") else "❌ 无"}')
    lines.append(f'- 已发布文章: {pub.get("total", 0)}篇')
    lines.append('')

    # GSC opportunities
    opps = seo.get('gsc_opportunities', [])
    if opps:
        lines.append('## 📈 GSC 机会信号')
        lines.append('')
        lines.append('| 关键词 | 展示量 | CTR | 排名 | 类型 |')
        lines.append('|---|---:|---:|---:|---|')
        for o in opps[:15]:
            kw = o.get('keyword', '')[:40]
            imp = o.get('impressions', '—')
            ctr = o.get('ctr', '—')
            pos = o.get('position', '—')
            typ = o.get('type', '')
            lines.append(f'| {kw} | {imp} | {ctr} | {pos} | {typ} |')
        lines.append('')

    # E1/E2 keywords
    e1 = seo.get('e1_keywords', [])
    e2 = seo.get('e2_keywords', [])
    if e1 or e2:
        lines.append('## 🔑 关键词池')
        lines.append('')
        if e1:
            lines.append('### ⭐⭐⭐ E1 GSC实测词')
            for kw in e1[:10]:
                lines.append(f'- {kw}')
            lines.append('')
        if e2:
            lines.append('### ⭐⭐ E2 人工验证词')
            for kw in e2[:15]:
                lines.append(f'- {kw}')
            lines.append('')

    # Search results parsed (if any)
    sr = r.get('search_results')
    if sr:
        lines.append('## 🔍 搜索结果解析')
        lines.append(f'- 已解析 Section: {", ".join(sr.get("parsed_sections", []))}')
        for key in ['serp', 'pain_points', 'market_data', 'cases', 'citations', 'gaps', 'paa', 'paa_data']:
            if sr.get(key):
                lines.append(f'- ✅ {key}')
            else:
                lines.append(f'- ❌ {key}')
        lines.append('')

    # Published coverage
    lines.append(f'## 📝 已发布覆盖（{pub.get("total", 0)}篇）')
    tags = pub.get('top_tags', [])
    if tags:
        lines.append('')
        lines.append('**高频标签:**')
        for tag, cnt in tags[:10]:
            lines.append(f'- `{tag}` ×{cnt}')
    lines.append('')

    # Cannibalization
    can = r.get('cannibalization', '')
    if can:
        lines.append('## ⚠️ 蚕食检测结果')
        lines.append('```')
        lines.append(can[:2000])
        lines.append('```')
        lines.append('')

    # Library matches
    lib = r.get('library_matches', {})
    for lib_type, label in [('pain_points', '痛点'), ('case_studies', '案例'), ('external_sources', '引用')]:
        items = lib.get(lib_type, [])
        if items:
            lines.append(f'## 📚 素材库匹配 — {label} ({len(items)}条)')
            for it in items:
                lines.append(f'- {it["header"]} | 次数:{it["usage_count"]} | 匹配:{it["match_score"]} | {it["url"]}')
            lines.append('')

    # Topic context (PLAN handoff)
    ctx = r.get('topic_context')
    if ctx:
        lines.append('## 🎯 PLAN 上下文（继承自选题阶段）')
        lines.append(f"- 意图: {ctx.get('intent','?')} | 层级: {ctx.get('tier','?')}"
                     + (f" | PLAN分: {ctx.get('plan_score')}" if ctx.get('plan_score') is not None else ''))
        if ctx.get('cannibal_risk'):
            lines.append(f"- 蚕食预判: {ctx['cannibal_risk']}")
        if ctx.get('guidance'):
            lines.append(f"- ✍️ 写作指引: {ctx['guidance']}")
        lines.append('')

    # Target keywords (raw text for AI context)
    if r.get('target_keywords'):
        lines.append('## 🎯 目标关键词上下文')
        lines.append('```')
        lines.append(r['target_keywords'][:2000])
        lines.append('```')
        lines.append('')

    # Material pack
    lines.append('---')
    lines.append('')
    lines.append('## 📦 预填素材包')
    lines.append('')
    lines.append(material_pack)

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
# Stage 2: Archive materials back to libraries
# ═══════════════════════════════════════════════════════════

def archive_materials(website: str, pack_path: str):
    """Read final material pack, extract [search] items, append to libraries.
    Returns (report_str, has_format_issues_bool)."""
    website_dir = BASE_DIR / website
    pack_file = Path(pack_path)
    if not pack_file.exists():
        alt = website_dir / 'material-packs' / pack_path
        if alt.exists():
            pack_file = alt
        else:
            print(f'Error: material pack not found: {pack_path}', file=sys.stderr)
            sys.exit(1)

    content = pack_file.read_text(encoding='utf-8')
    topic_tags_raw = re.search(r'\*\*目标主关键词\*\*:\s*(.+)', content)
    topic_tags = [w.lower() for w in re.split(r'[\s\-_,;/]+', topic_tags_raw.group(1)) if len(w) > 2] if topic_tags_raw else []

    stats = {'pain_added': 0, 'case_added': 0, 'source_added': 0,
             'pain_skipped': 0, 'case_skipped': 0, 'source_skipped': 0,
             'pain_updated': 0, 'case_updated': 0, 'source_updated': 0}

    # ── Build cross-library URL sets (S5: avoid archiving URL already in another library) ──
    pain_lib_path = website_dir / 'context' / 'pain-points-library.md'
    case_lib_path = website_dir / 'context' / 'case-studies-library.md'
    source_lib_path = website_dir / 'context' / 'external-sources-library.md'
    pain_urls = _extract_urls(pain_lib_path.read_text(encoding='utf-8')) if pain_lib_path.exists() else set()
    case_urls = _extract_urls(case_lib_path.read_text(encoding='utf-8')) if case_lib_path.exists() else set()
    source_urls = _extract_urls(source_lib_path.read_text(encoding='utf-8')) if source_lib_path.exists() else set()

    # ── Archive pain points to library ──
    new_pain_items = _extract_search_items(content, 'A. 用户痛点')
    if new_pain_items:
        stats.update(_archive_to_library(
            pain_lib_path, new_pain_items, topic_tags,
            template='## {title}\n- **来源**: [{source_label}]({url})\n- **原话**: "{quote}"\n- **适用文章**: {tags}\n- **标签**: {auto_tags}\n- **使用次数**: 0\n',
            stats_prefix='pain', stats=stats,
            cross_lib_urls=case_urls | source_urls,
        ))

    # ── Archive case studies to library ──
    new_case_items = _extract_search_items(content, 'C. 案例素材')
    if new_case_items:
        stats.update(_archive_to_library(
            case_lib_path, new_case_items, topic_tags,
            template='## {title}\n- **来源**: [{source_label}]({url})\n- **故事**: {text}\n- **适用文章**: {tags}\n- **标签**: {auto_tags}\n- **使用次数**: 0\n',
            stats_prefix='case', stats=stats,
            cross_lib_urls=pain_urls | source_urls,
        ))

    # ── Archive external sources to library ──
    new_source_items = _extract_search_items(content, 'E. 权威引用')
    if new_source_items:
        stats.update(_archive_to_library(
            source_lib_path, new_source_items, topic_tags,
            template='## {title}\n- **来源**: [{source_label}]({url})\n- **关键发现**: {quote}\n- **类型**: {source_type}\n- **适用文章**: {tags}\n- **标签**: {auto_tags}\n- **使用次数**: 0\n',
            stats_prefix='source', stats=stats,
            cross_lib_urls=pain_urls | case_urls,
        ))

    # ── Update usage counts for [library] items ──
    _update_library_usage(website_dir / 'context' / 'pain-points-library.md', content, 'A. 用户痛点')
    _update_library_usage(website_dir / 'context' / 'case-studies-library.md', content, 'C. 案例素材')
    _update_library_usage(website_dir / 'context' / 'external-sources-library.md', content, 'E. 权威引用')

    # ── Format validation: warn if [search] tags exist but nothing was extracted ──
    format_warnings = []
    for label, section_label, extracted in [
        ('痛点', 'A. 用户痛点', new_pain_items),
        ('案例', 'C. 案例素材', new_case_items),
        ('引用', 'E. 权威引用', new_source_items),
    ]:
        escaped = re.escape(section_label)
        sec_m = re.search(rf'^#{{2,3}}\s+{escaped}', content, re.MULTILINE)
        if sec_m:
            sec_start = sec_m.end()
            next_sec = re.search(r'\n#{2,3}\s+[A-H]\.\s', content[sec_start:])
            section_body = content[sec_m.start():sec_start + next_sec.start()] if next_sec else content[sec_m.start():]
        else:
            section_body = ''
        search_count = len(re.findall(r'\[search\]', section_body))
        if search_count > 0 and len(extracted) == 0:
            format_warnings.append(f'⚠️ {label}段有 {search_count} 个 [search] 标记但一条也没提取到 — 请检查格式是否为 `- **Title**` + `- Field: value`（英文字段名 + `- ` 前缀）')

    # Collect library totals
    for lib_path_key, stats_key in [('pain-points-library.md', 'pain_total'), ('case-studies-library.md', 'case_total'), ('external-sources-library.md', 'source_total')]:
        lib_path = website_dir / 'context' / lib_path_key
        if lib_path.exists():
            stats[stats_key] = len(re.findall(r'^## ', lib_path.read_text(encoding='utf-8'), re.MULTILINE))

    report = f"""📦 素材归档完成:

- 痛点: +{stats['pain_added']} 条 (跳过重复 {stats['pain_skipped']}), 使用次数更新 {stats['pain_updated']} 条
- 案例: +{stats['case_added']} 条 (跳过重复 {stats['case_skipped']}), 使用次数更新 {stats['case_updated']} 条
- 引用: +{stats['source_added']} 条 (跳过重复 {stats['source_skipped']}), 使用次数更新 {stats['source_updated']} 条

素材库总计: 痛点 {stats.get('pain_total', '?')} / 案例 {stats.get('case_total', '?')} / 引用 {stats.get('source_total', '?')}"""

    if format_warnings:
        report += '\n\n' + '\n'.join(format_warnings)

    return report, bool(format_warnings)


def _extract_search_items(content: str, section_label: str) -> List[Dict]:
    """Extract structured items from a material pack section.

    section_label: short label like 'A. 用户痛点' that matches headers in
    ##/### format (e.g. '### A. 用户痛点 (≥3) ✅ 已确认').

    Handles two formats:
    1. Structured search results with **Bold Headers** / - Source: / - Quote: patterns
    2. Flat [search]-tagged or URL-bearing bullet lines (legacy fallback)
    """
    items = []

    # Match section header like "## A. 用户痛点" or "### A. 用户痛点 (…)"  — allow 2 or 3 #
    escaped = re.escape(section_label)
    section_m = re.search(rf'^#{{2,3}}\s+{escaped}', content, re.MULTILINE)
    if not section_m:
        return items

    # Search from the end of the matched header for the next section
    search_start = section_m.end()
    section_end_m = re.search(r'\n#{2,3}\s+[A-H]\.\s', content[search_start:])
    if section_end_m:
        section_end = search_start + section_end_m.start()
        section = content[section_m.start():section_end]
    else:
        section = content[section_m.start():]

    # ── Pass 1: Structured search results (Pain Point / Story / Citation blocks) ──
    # Pattern: **Title: description** followed by - Key: value lines
    block_pattern = re.compile(
        r'\*\*(.+?)\*\*\s*\n'                          # **Title line**
        r'((?:^\s*-(?!\s*\*\*\[search\]\s)[^\n]+\n?)*)',  # bullet lines, stop before next **[search]
        re.MULTILINE
    )
    for block_m in block_pattern.finditer(section):
        title = block_m.group(1).strip()
        bullets = block_m.group(2)
        item = {'title': title, 'tags': []}

        # Parse bullet lines into key-value fields
        for bline in bullets.split('\n'):
            bline = bline.strip()
            if not bline.startswith('- '):
                continue
            field_text = bline[2:].strip()

            # Check for Markdown link: [label](url) — extract label and URL first
            md_link_m = re.search(r'\[([^\]]+)\]\((https?://[^)]+)\)', field_text)
            link_label = md_link_m.group(1) if md_link_m else ''
            link_url = md_link_m.group(2).rstrip('.,|') if md_link_m else ''

            if link_url:
                item.setdefault('source_label', link_label)
                item.setdefault('url', link_url)

            # Try bold key: **Key**: value
            kv_bold_m = re.match(r'\*\*([^*]+)\*\*:\s*(.+)', field_text)
            if kv_bold_m:
                key = kv_bold_m.group(1).strip().lower()
                value = kv_bold_m.group(2).strip()
                item[key] = value
                continue

            # Try plain key: value (e.g. - Quote: ..., - Type: ..., - Use in article: ...)
            # Skip lines that are primarily URLs or link lines already handled
            is_url_key = field_text.lower().startswith(('source:', 'source url:', 'url:'))
            if is_url_key and link_url:
                continue  # Already extracted via Markdown link

            kv_plain_m = re.match(r'([A-Za-z\u4e00-\u9fff][A-Za-z\s\u4e00-\u9fff]+):\s*(.+)', field_text)
            if kv_plain_m:
                key = kv_plain_m.group(1).strip().lower()
                value = kv_plain_m.group(2).strip()
                if key in item:
                    item[key] = item[key] + ' ' + value
                else:
                    item[key] = value
                continue

            # Fallback: plain URL
            if not link_url:
                url_m = re.search(r'(https?://\S+)', field_text)
                if url_m:
                    item.setdefault('url', url_m.group(1).rstrip('.,|'))

        # Normalize field names from search results format
        field_aliases = {
            'source': 'url', 'source url': 'url', 'url': 'url',
            '来源': 'url',
            'quote': 'quote', '原话': 'quote', '引语': 'quote',
            'key finding': 'quote', 'finding': 'quote',
            '关键发现': 'quote',
            'story': 'text', 'summary': 'text',
            '故事': 'text',
            'use in article': 'text', '文章用途': 'text',
            'type': 'source_type', '类型': 'source_type',
            'source type': 'source_type',
        }
        normalized = {}
        for k, v in item.items():
            target = field_aliases.get(k)
            if target:
                if target in normalized:
                    normalized[target] = normalized[target] + '\n' + v
                else:
                    normalized[target] = v
            else:
                normalized[k] = v

        # Ensure we have a URL
        if 'url' not in normalized:
            for bline in bullets.split('\n'):
                url_m = re.search(r'(https?://\S+)', bline)
                if url_m:
                    normalized['url'] = url_m.group(1).rstrip('.,|')
                    break
            url_m2 = re.search(r'\[([^\]]+)\]\((https?://\S+)\)', bullets)
            if url_m2 and 'url' not in normalized:
                normalized['url'] = url_m2.group(2).rstrip('.,|')

        if 'url' not in normalized:
            continue  # Skip blocks without any URL

        normalized['url'] = normalized['url'].rstrip('|').rstrip(',').rstrip('.')

        # Auto-detect tags from title + fields
        all_text = (normalized.get('title', '') + ' ' +
                    normalized.get('quote', '') + ' ' +
                    normalized.get('text', '')).lower()
        normalized['tags'] = _detect_tags(all_text)

        # Store raw section for archive template flexibility
        normalized['_raw_bullets'] = bullets.strip()
        items.append(normalized)

    # ── Pass 2: Flat bullet lines (legacy [search] / [library] / URL-only format) ──
    # Only process lines that aren't already captured by structured blocks
    def _clean_url(u):
        return u.rstrip('|').rstrip(',').rstrip('.')

    structured_urls = {_clean_url(i.get('url', '')) for i in items}
    for line in section.split('\n'):
        line = line.strip()
        if not line.startswith('- '):
            continue
        if '[search]' in line.lower():
            url_m = re.search(r'(https?://\S+)', line)
            text_m = re.search(r'\[search\]\s*(.+?)(?:\s*\||$)', line, re.IGNORECASE)
            if url_m:
                clean = _clean_url(url_m.group(1))
                if clean not in structured_urls:
                    items.append({
                        'url': url_m.group(1).rstrip('|').rstrip(',').rstrip('.'),
                        'title': text_m.group(1).strip() if text_m else line.lstrip('- '),
                        'text': text_m.group(1).strip() if text_m else '',
                        'tags': _detect_tags(text_m.group(1) if text_m else ''),
                        'full_line': line,
                    })
        elif '[library]' not in line.lower():
            # Skip lines that look like structured-block fields — they belong to Pass 1
            # blocks and must NOT be captured as standalone items (S2). English keys
            # and Chinese-bold prefixes (\*\*来源\*\*: / \*\*原话\*\*: / …) are both covered.
            if re.match(
                r'^- (?:Source|Quote|Summary|Key finding|Type|Use in article'
                r'|\*\*(?:来源|原话|引语|关键发现|故事|文章用途|类型|适用文章|标签|使用次数)\*\*)\s*[:：]\s',
                line,
                re.IGNORECASE,
            ):
                continue
            url_m = re.search(r'(https?://\S+)', line)
            if url_m:
                clean = _clean_url(url_m.group(1))
                if clean not in structured_urls:
                    items.append({
                        'url': url_m.group(1).rstrip('|').rstrip(',').rstrip('.'),
                        'title': line.lstrip('- ').split('|')[0].strip()[:100],
                        'text': line.lstrip('- ').split('|')[0].strip()[:100],
                        'tags': _detect_tags(line),
                        'full_line': line,
                    })

    return items


# ── Tag auto-detection vocabulary ──
_TAG_KEYWORDS = {
    'battery': ['battery', 'batteries', '18650', '26650', '16340', 'cr123', 'li-ion', 'lithium',
                'rechargeable', 'voltage', 'mah', 'capacity', 'cell'],
    'charger': ['charger', 'charging', 'overcharge', 'usb-c', 'cc/cv'],
    'case': ['case', 'storage', 'holster', 'carry', 'carrying', 'pelican', 'foam', 'sleeve', 'pouch'],
    'mount': ['mount', 'tripod', 'bracket', 'picatinny', 'clamp', 'telescope', 'diy'],
    'beam optics': ['beam', 'divergence', 'expander', 'collimat', 'focus', 'lens', 'diffraction', 'kaleidoscope',
                     'thread', 'm9', 'cap', 'spot'],
    'goggles': ['goggle', 'glasses', 'eyewear', 'optical density', 'eye protection', 'eye damage'],
    'travel': ['tsa', 'travel', 'airline', 'flight', 'carry-on', 'luggage', 'airport', 'customs', 'confiscat'],
    'safety': ['safety', 'safe', 'danger', 'hazard', 'risk', 'fire', 'burn', 'blind', 'injury', 'damage',
               'class 4', 'class 3', 'fda', 'ansi', 'ir leak', 'ir filter', 'infrared'],
    'quality': ['quality', 'counterfeit', 'fake', 'scam', 'cheap', 'build', 'durable', 'reliable',
                'die', 'dead', 'broke', 'fail', 'failure', 'overheat', 'heat sink', 'thermal'],
    'power': ['power', ' mw', ' watt', 'wattage', 'output', 'bright', 'dim', 'lpm'],
    'price': ['price', 'cost', 'budget', 'expensive', 'cheap', '$', 'dollar', 'worth'],
    'astronomy': ['astronomy', 'stargaz', 'telescope', 'star', 'night sky'],
    'outdoor': ['outdoor', 'cold', 'weather', 'winter', 'temperature', 'freez'],
    'legal': ['legal', 'law', 'regulation', 'faa', 'fda', 'compliance', 'restrict', 'prohibit'],
    'shipping': ['shipping', 'delivery', 'import', 'customs'],
    'support': ['support', 'customer', 'warranty', 'return', 'refund', 'service'],
}


def _detect_tags(text: str) -> List[str]:
    """Auto-detect up to 4 tags from text content using keyword vocabulary."""
    text_lower = text.lower()
    scored = []
    for tag, keywords in _TAG_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scored.append((score, tag))
    scored.sort(key=lambda x: -x[0])
    return [tag for _, tag in scored[:4]]


def _normalize_url(raw: str) -> str:
    """Normalize URL for dedup: strip trailing punctuation, drop fragment, lowercase host."""
    if not raw:
        return ''
    # Strip surrounding angle-bracket Markdown autolink delimiters first.
    url = raw.strip('<>')
    # Strip trailing punctuation commonly captured by greedy URL regexes.
    url = url.rstrip('|,.);')
    while url.endswith(')'):
        url = url[:-1]
    url = url.rstrip('|,.);')
    if '#' in url:
        url = url.split('#')[0]
    if '://' in url:
        scheme, rest = url.split('://', 1)
        if '/' in rest:
            host, path = rest.split('/', 1)
            url = f"{scheme}://{host.lower()}/{path}"
        else:
            url = f"{scheme}://{rest.lower()}"
    return url


def _extract_urls(content: str) -> set:
    """Extract all URLs from content, preferring Markdown link targets."""
    urls = set()
    for m in re.finditer(r'\]\((https?://[^)]+)\)', content):
        urls.add(_normalize_url(m.group(1)))
    for m in re.finditer(r'(?<!\()(https?://[^\s\)\]\|,]+)', content):
        urls.add(_normalize_url(m.group(1)))
    return urls


def _archive_to_library(lib_path: Path, items: List[Dict], topic_tags: List[str],
                        template: str, stats_prefix: str, stats: Dict,
                        cross_lib_urls: Optional[set] = None) -> Dict:
    """Append new items to a material library, deduplicating by URL.

    Template placeholders:
      {title} — auto-extracted from structured search results or URL text
      {url} — source URL
      {source_label} — extracted source name (e.g. "Reddit r/lasers")
      {quote} — user quote or key finding
      {text} — long-form story/description text
      {source_type} — citation type (.gov / .edu / etc.)
      {tags} — topic-level tags from material pack keywords
      {auto_tags} — auto-detected tags from content analysis
    """
    if not items:
        return stats

    lib_path.parent.mkdir(parents=True, exist_ok=True)

    existing_urls = set()
    base_content = ''
    if lib_path.exists():
        base_content = lib_path.read_text(encoding='utf-8')
        existing_urls = _extract_urls(base_content)

    cross_lib_urls = cross_lib_urls or set()
    topic_tag_str = ', '.join(topic_tags[:5])
    new_entries = []

    for item in items:
        # Re-extract URLs from the item's raw content so dedup normalization is
        # applied consistently even when Pass 1 / Pass 2 left trailing punctuation
        # or Markdown fragments on the URL field (S1).
        raw_blob = (item.get('_raw_bullets') or item.get('full_line') or '')
        if not raw_blob and item.get('url'):
            raw_blob = str(item.get('url'))
        item_urls = _extract_urls(raw_blob)
        orig_norm = _normalize_url(item.get('url', ''))
        if orig_norm and orig_norm in item_urls:
            url = orig_norm
        elif item_urls:
            url = next(iter(item_urls))
        else:
            url = orig_norm
        if not url:
            continue
        if url in existing_urls:
            stats[f'{stats_prefix}_skipped'] += 1
            continue
        if url in cross_lib_urls:
            stats[f'{stats_prefix}_skipped'] += 1
            stats['cross_library_skipped'] = stats.get('cross_library_skipped', 0) + 1
            continue

        # Build template kwargs from item, with per-type defaults
        title = item.get('title', '')
        # Clean title: strip [search]/[library] tags then "Pain point N:" / "Story X —" / "Citation Y —" prefixes
        title = re.sub(r'\[(?:search|library)\]\s*', '', title).strip()
        title = re.sub(r'^(?:Pain point|Story|Citation)\s+[A-Z\d]+[\s:—–-]+', '', title).strip()
        if not title:
            title = item.get('text', item.get('quote', item.get('url', '')))[:80]
        if not title:
            title = 'Untitled'

        entry_text = item.get('text', item.get('quote', item.get('full_line', '')))
        quote = item.get('quote', '')
        if not quote:
            quote = entry_text[:200] if entry_text else '[see source]'

        source_label = item.get('source_label', '')
        if not source_label:
            source_label_m = re.search(r'\[([^\]]+)\]', item.get('_raw_bullets', ''))
            if source_label_m:
                source_label = source_label_m.group(1)
            else:
                source_label = re.sub(r'^https?://(?:www\.)?', '', url).split('/')[0][:30]

        auto_tags = ' '.join(f'`{t}`' for t in item.get('tags', []))

        fmt_kwargs = {
            'url': url,
            'title': title,
            'source_label': source_label,
            'quote': quote[:300],
            'text': entry_text[:500],
            'source_type': item.get('source_type', item.get('type', '')),
            'tags': topic_tag_str,
            'auto_tags': auto_tags,
        }

        new_entry = '\n' + template.format(**fmt_kwargs)
        new_entries.append(new_entry)
        existing_urls.add(url)
        cross_lib_urls.add(url)
        stats[f'{stats_prefix}_added'] += 1

    if new_entries:
        tmp_path = lib_path.with_suffix('.tmp')
        tmp_path.write_text(base_content + '\n'.join(new_entries), encoding='utf-8')
        tmp_path.replace(lib_path)

    if lib_path.exists():
        content = lib_path.read_text(encoding='utf-8')
        total = len(re.findall(r'\n##\s', content))
        stats[f'{stats_prefix}_total'] = max(total, stats.get(f'{stats_prefix}_added', 0))

    return stats


def _update_library_usage(lib_path: Path, pack_content: str, section_label: str):
    """Find [library] items in the material pack and increment their usage count in the library."""
    if not lib_path.exists():
        return

    escaped = re.escape(section_label)
    sec_m = re.search(rf'^#{{2,3}}\s+{escaped}', pack_content, re.MULTILINE)
    if not sec_m:
        return

    search_start = sec_m.end()
    section_end_m = re.search(r'\n#{2,3}\s+[A-H]\.\s', pack_content[search_start:])
    if section_end_m:
        section_end = search_start + section_end_m.start()
        section = pack_content[sec_m.start():section_end]
    else:
        section = pack_content[sec_m.start():]

    # Find [library] items - extract their headers/URLs
    referenced_urls = set()
    for line in section.split('\n'):
        if '[library]' in line.lower():
            url_m = re.search(r'(https?://\S+)', line)
            if url_m:
                referenced_urls.add(_normalize_url(url_m.group(1)))

    if not referenced_urls:
        return

    # Update usage counts in the library file
    content = lib_path.read_text(encoding='utf-8')
    updated = content

    # Locate every entry ## block that contains one of the referenced URLs.
    # Iterate ALL occurrences (S4: previously only the first match was bumped,
    # leaving duplicate entries at unit zero). Collect (entry_start, entry_end)
    # ranges — possibly multiple URL positions per entry — then dedupe so each
    # referenced entry is incremented at most once.
    matched_ranges = []
    for url in referenced_urls:
        start = 0
        while True:
            url_pos = updated.find(url, start)
            if url_pos < 0:
                break
            entry_start = updated.rfind('\n## ', 0, url_pos)
            if entry_start < 0:
                entry_start = 0
            else:
                entry_start += 1  # skip the leading \n
            entry_end = updated.find('\n## ', url_pos)
            if entry_end < 0:
                entry_end = len(updated)
            matched_ranges.append((entry_start, entry_end))
            start = entry_end  # resume after this entry

    # Deduplicate identical ranges (same URL appearing twice in one entry,
    # or multiple referenced URLs in the same library entry).
    matched_ranges = sorted(set(matched_ranges))

    # Apply increments from the LAST range backwards so earlier offsets stay valid.
    for entry_start, entry_end in reversed(matched_ranges):
        entry = updated[entry_start:entry_end]
        new_entry = re.sub(
            r'\*\*使用次数\*\*:\s*(\d+)',
            lambda m: f'**使用次数**: {int(m.group(1)) + 1}',
            entry
        )
        if new_entry != entry:
            updated = updated[:entry_start] + new_entry + updated[entry_end:]

    if updated != content:
        tmp = lib_path.with_suffix('.md.tmp')
        tmp.write_text(updated, encoding='utf-8')
        tmp.replace(lib_path)


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='RESEARCH data collector — three-stage pipeline')
    subparsers = parser.add_subparsers(dest='command', help='Stage to run')

    # Stage 0: generate-prompt
    p_prompt = subparsers.add_parser('generate-prompt', help='Generate search AI prompt')
    p_prompt.add_argument('--website', required=True, help='Website name (laserpointerhub / mobilemarking)')
    p_prompt.add_argument('--topic', required=True, help='Research topic/keyword')

    # Stage 1: collect
    p_collect = subparsers.add_parser('collect', help='Collect and structure data')
    p_collect.add_argument('--website', required=True, help='Website name')
    p_collect.add_argument('--topic', required=True, help='Research topic/keyword')
    p_collect.add_argument('--search-file', help='Path to search results file (optional, auto-detected if not specified)')

    # Stage 2: archive
    p_archive = subparsers.add_parser('archive', help='Archive materials back to libraries')
    p_archive.add_argument('--website', required=True, help='Website name')
    p_archive.add_argument('--pack', required=True, help='Path to material pack file')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'generate-prompt':
        output = generate_prompt(args.website, args.topic)
        print(output)

    elif args.command == 'collect':
        output = collect_data(args.website, args.topic, args.search_file)
        print(output)

    elif args.command == 'archive':
        report, has_issues = archive_materials(args.website, args.pack)
        print(report)
        if has_issues:
            sys.exit(1)


if __name__ == '__main__':
    main()