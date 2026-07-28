#!/usr/bin/env python3
"""PLAN data collector — gather internal & external signals into a structured brief.

Internal data (no API needed):
  - GSC opportunities from seo-data-manual.md (A1-A5, E1-E2)
  - Published article list from published-index.json
  - Pain point tag counts from pain-points-library.md
  - Brand constraints from brand-voice.md
  - Topic cluster coverage from target-keywords.md

External signals (needs Tavily API key):
  - Competitor topics (search gaps vs competitor content)
  - Reddit/forum discussions
  - PAA questions

Usage:
  python3 plan_collector.py --website laserpointerhub
  python3 plan_collector.py --website laserpointerhub --tavily-key tvly-xxx
  python3 plan_collector.py --website laserpointerhub --tavily-key tvly-xxx --json
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


# ── Config ──────────────────────────────────────────────

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
EXTERNAL_SEARCH_QUERIES = 10  # number of Tavily queries to run

# Reuse canonical shared primitives from seo_common (no local copies)
_match_tokens = seo_common.match_tokens
_extract_section = seo_common.extract_section
_extract_table = seo_common.extract_table


# ── Internal data readers ───────────────────────────────

def read_gsc_data(website_dir: str) -> Dict:
    """Parse seo-data-manual.md → extract GSC opportunity keywords."""
    manual_path = Path(website_dir) / 'context' / 'seo-data-manual.md'
    if not manual_path.exists():
        return {'opportunities': [], 'e1_keywords': [], 'e2_keywords': [], 'updated': None}

    content = manual_path.read_text(encoding='utf-8')

    # Extract data freshness from file modification time or explicit date
    updated = datetime.fromtimestamp(manual_path.stat().st_mtime).strftime('%Y-%m-%d')
    m = re.search(r'(?:GSC|最后更新|更新).*?(\d{4}-\d{2}-\d{2})', content)
    if m:
        updated = m.group(1)

    opportunities = []

    # ── A1: Top clicks ──
    a1 = _extract_table(content, '### A1.', '### A2.')
    if a1:
        for row in a1:
            if len(row) >= 5:
                try:
                    impressions = int(row[3].replace(',', ''))
                    ctr_str = row[4].replace('%', '').strip()
                    ctr = float(ctr_str) if ctr_str else 0
                    if impressions > 500 and ctr < 2:
                        opportunities.append({
                            'keyword': row[1], 'impressions': impressions,
                            'ctr': ctr, 'position': row[5] if len(row) > 5 else '—',
                            'type': 'high_impression_low_ctr', 'source': 'A1'
                        })
                except (ValueError, IndexError):
                    pass

    # ── A2: Quick wins (rank 8-20) ──
    a2 = _extract_table(content, '### A2.', '### A3.')
    if a2:
        for row in a2:
            if len(row) >= 4:
                try:
                    pos = float(row[2]) if row[2] else 99
                    impressions = int(row[3].replace(',', ''))
                    if 8 <= pos <= 20:
                        opportunities.append({
                            'keyword': row[1], 'impressions': impressions,
                            'position': pos, 'ctr': 0,
                            'type': 'quick_win', 'source': 'A2'
                        })
                except (ValueError, IndexError):
                    pass

    # ── A3: Rising trends ──
    a3 = _extract_table(content, '### A3.', '### A4.')
    if a3:
        for row in a3:
            if len(row) >= 5:
                try:
                    impressions = int(row[2].replace(',', ''))
                    change_str = row[4].replace('%', '').replace('+', '').strip()
                    change = float(change_str) if change_str else 0
                    if change > 500:
                        opportunities.append({
                            'keyword': row[1], 'impressions': impressions,
                            'trend_pct': change, 'type': 'rising_trend', 'source': 'A3'
                        })
                except (ValueError, IndexError):
                    pass

    # ── A4: Low CTR ──
    a4 = _extract_table(content, '### A4.', '### A5.')
    if a4:
        for row in a4:
            if len(row) >= 5:
                try:
                    impressions = int(row[2].replace(',', ''))
                    ctr_str = row[3].replace('%', '').strip()
                    ctr = float(ctr_str) if ctr_str else 0
                    if impressions > 100 and ctr < 3:
                        opportunities.append({
                            'keyword': row[1], 'impressions': impressions,
                            'ctr': ctr, 'position': row[4] if len(row) > 4 else '—',
                            'type': 'low_ctr', 'source': 'A4'
                        })
                except (ValueError, IndexError):
                    pass

    # ── E1: GSC-verified keywords ──
    e1 = _extract_table(content, '### E1.', '### E2.')
    e1_kw = [r[1] for r in e1 if len(r) >= 2 and r[1] and '关键词' not in r[1]]

    # ── E2: Manual-verified keywords ──
    e2_section = _extract_section(content, '### E2.', '### E3.')
    e2_kw = re.findall(r'\|\s*([^|]+?)\s*\|', e2_section) if e2_section else []

    return {
        'opportunities': _deduplicate_opportunities(opportunities),
        'e1_keywords': e1_kw,
        'e2_keywords': e2_kw[:30],
        'updated': updated
    }


def read_published_index(website_dir: str) -> Dict:
    """Read published-index.json → slugs + tags."""
    index_path = Path(website_dir) / 'published' / 'published-index.json'
    if not index_path.exists():
        return {'articles': [], 'slugs': set(), 'all_tags': {}}

    data = json.loads(index_path.read_text(encoding='utf-8'))
    articles = []
    slugs = set()
    tag_counts = {}

    for entry in data:
        slug = entry.get('slug', '')
        tags = entry.get('tags', [])
        articles.append({'#': entry.get('#', ''), 'slug': slug, 'tags': tags})
        slugs.add(slug)
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        'articles': articles,
        'slugs': slugs,
        'all_tags': tag_counts,
        'total': len(articles)
    }


def read_pain_point_tags(website_dir: str) -> Dict:
    """Read pain-points-library.md → count tags. Dual-format support."""
    lib_path = Path(website_dir) / 'context' / 'pain-points-library.md'
    if not lib_path.exists():
        return {'tags': {}, 'total_pain_points': 0, 'updated': None}

    content = lib_path.read_text(encoding='utf-8')

    # Format A: tag table (laserpointerhub — `| \`tag\` |  | #1 #2 ...`)
    tag_stats = {}
    for m in re.finditer(r'\|\s*`([^`]+)`\s*\|\s*[^|]*\s*\|\s*(#\d+(?:\s*#\d+)*)', content):
        tag = m.group(1).strip()
        count = len(re.findall(r'#\d+', m.group(2)))
        if count > 0:
            tag_stats[tag] = count

    # Format B: comma-separated tags (mobilemarking uses **适用文章**: value or **标签**: value, no backticks)
    if not tag_stats:
        for m in re.finditer(r'\*\*(?:标签|适用文章)\*\*:\s*([^\n]+)', content):
            raw = m.group(1).replace('`', '')
            for tag in re.split(r'[,]\s*', raw):
                tag = tag.strip().lower()
                if tag and len(tag) > 2:
                    tag_stats[tag] = tag_stats.get(tag, 0) + 1

    # Count total pain points in both formats
    total = max(len(re.findall(r'## 痛点 \d+[：:]', content)),
                len(re.findall(r'##\s*\d+[、.．]\s*', content)))
    if not total:
        total = len(re.findall(r'^\*\*标签\*\*:', content, re.MULTILINE))

    updated = None
    m = re.search(r'(?:更新|创建)日期.*?(\d{4}-\d{2}-\d{2})', content)
    if m:
        updated = m.group(1)

    return {'tags': tag_stats, 'total_pain_points': total, 'updated': updated}


def read_brand_constraints(website_dir: str) -> Dict:
    """Read brand-voice.md → extract constraints."""
    bv_path = Path(website_dir) / 'context' / 'brand-voice.md'
    if not bv_path.exists():
        return {'audience': [], 'forbidden': []}

    content = bv_path.read_text(encoding='utf-8')

    # Extract audience mentions
    audience = []
    audience_section = _extract_section(content, '受众', '\n\n')
    if audience_section:
        for line in audience_section.split('\n'):
            line = line.strip().lstrip('- ').strip()
            if line and len(line) > 2:
                audience.append(line)

    return {'audience': audience, 'raw': content[:500]}


def read_clusters(website_dir: str, published_articles: List[Dict] = None) -> Dict:
    """Read target-keywords.md → cluster coverage = DISTINCT articles, pollution-robust.

    Fixes the historical bug where coverage summed tag occurrences via substring,
    producing counts far above the total article count (e.g. 95 with only 44 articles).

    Method:
      1. Parse each cluster's Primary + Secondary keyword tokens.
      2. Auto-detect GENERIC tokens = tokens appearing across many clusters
         (cross-pollution like 'astronomy', 'safety' in secondaries).
      3. Distinctive set per cluster = Primary tokens (always kept, they are clean)
         ∪ (Secondary tokens − GENERIC).
      4. An article is covered by a cluster if its (slug+tags) tokens intersect the
         cluster's distinctive set. Count DISTINCT articles, capped at total.
    """
    tk_path = Path(website_dir) / 'context' / 'target-keywords.md'
    if not tk_path.exists():
        return {'clusters': []}

    content = tk_path.read_text(encoding='utf-8')
    raw_clusters = []  # (name, primary_tokens, secondary_tokens, raw_primary_str)

    # Unified format: ## Topic Cluster N: Name … **Primary:** … **Secondary:** …
    sections = re.split(r'\n(?=## Topic Cluster \d+[：:])', content)
    for section in sections:
        name_m = re.match(r'^##\s+Topic Cluster\s*\d+[：:]\s*(.+)', section)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        primary_tok, secondary_tok = set(), set()
        raw_primary = ''
        for line in section.split('\n'):
            pm = re.search(r'\*\*Primary:\*\*\s*(.+)', line)
            if pm:
                primary_tok |= _match_tokens(pm.group(1))
                if not raw_primary:
                    raw_primary = pm.group(1).strip()
            sm = re.search(r'\*\*Secondary:\*\*\s*(.+)', line)
            if sm:
                secondary_tok |= _match_tokens(sm.group(1))
        if primary_tok or secondary_tok:
            raw_clusters.append((name, primary_tok, secondary_tok, raw_primary))

    n = len(raw_clusters)
    # Auto-detect generic tokens via cross-cluster document frequency (primary+secondary)
    df = {}
    for _, p, s, _ in raw_clusters:
        for t in (p | s):
            df[t] = df.get(t, 0) + 1
    generic_cutoff = max(3, int(0.4 * n)) if n else 3
    generic = {t for t, c in df.items() if c >= generic_cutoff}

    clusters = []
    for name, primary_tok, secondary_tok, raw_primary in raw_clusters:
        distinctive = primary_tok | (secondary_tok - generic)
        # Primary tokens are clean even if flagged generic — keep them
        distinctive |= primary_tok

        article_count = 0
        matched_slugs = []
        if published_articles:
            for art in published_articles:
                art_tokens = _match_tokens(art.get('slug', '').replace('-', ' '))
                for tag in art.get('tags', []):
                    art_tokens |= _match_tokens(tag)
                if art_tokens & distinctive:
                    article_count += 1
                    if len(matched_slugs) < 6:
                        matched_slugs.append(art.get('slug', '')[:40])

        clusters.append({
            'name': name,
            'keywords': sorted(distinctive)[:8],
            'raw_primary': raw_primary,
            'article_count': article_count,           # distinct articles, <= total
            'matched_slugs': matched_slugs,
        })

    return {'clusters': clusters, 'count': len(clusters), 'generic_tokens': sorted(generic)}


# ── External signals (Tavily) ───────────────────────────

def search_tavily(query: str, api_key: str, max_results: int = 5) -> List[Dict]:
    """Execute a single Tavily search and return results."""
    data = json.dumps({
        'query': query,
        'api_key': api_key,
        'max_results': max_results,
        'search_depth': 'basic'
    }).encode('utf-8')

    req = urllib.request.Request(
        TAVILY_SEARCH_URL,
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get('results', [])
    except Exception as e:
        return [{'error': str(e), 'query': query}]


def generate_search_queries(gsc_data: Dict, pain_tags: Dict, clusters: Dict) -> List[str]:
    """Generate Tavily search queries based on identified gaps.

    Domain term is auto-detected from cluster primary keywords so queries are
    relevant to any website, not hardcoded to 'laser pointer'."""
    queries = []

    # Detect the site's primary topic from first cluster's Primary keyword (RAW, not token-stripped)
    domain = ''
    for c in clusters.get('clusters', [])[:1]:
        name = c.get('name', '')
        kws = c.get('raw_primary', '')  # populated by read_clusters
        if kws:
            domain = kws.split(',')[0].strip().split('/')[0].strip()
        if not domain and name:
            domain = name
    if not domain:
        domain = 'laser pointer'  # true fallback, should never trigger once clusters exist

    # From top pain tags — expand to top 4
    sorted_tags = sorted(pain_tags.get('tags', {}).items(), key=lambda x: x[1], reverse=True)
    for tag, count in sorted_tags[:4]:
        queries.append(f'{tag} {domain} problems solutions')

    # From GSC opportunities — expand to top 4, vary query style
    for i, opp in enumerate(gsc_data.get('opportunities', [])[:4]):
        kw = opp.get('keyword', '')
        if kw:
            if i % 2 == 0:
                queries.append(f'{kw} reddit forum')
            else:
                queries.append(f'{kw} review comparison')

    # Product attribute-based search — keep 2
    for cluster in clusters.get('clusters', [])[:2]:
        attrs = cluster.get('keywords', []) if isinstance(cluster, dict) else []
        if attrs:
            a = [a for a in attrs if not a.isdigit() and len(a) > 3]
            seed = a[0] if a else attrs[0]
            queries.append(f'{seed} {domain} review comparison' if 'best' not in seed else f'{seed} guide')

    # Fill remaining with varied angles
    fillers = [
        f'{domain} common questions problems',
        f'best {domain} 2026 review',
        f'{domain} buying guide tips',
    ]
    for fq in fillers:
        if len(queries) >= EXTERNAL_SEARCH_QUERIES:
            break
        if fq not in queries:
            queries.append(fq)

    return queries[:EXTERNAL_SEARCH_QUERIES]


def collect_external_signals(gsc_data: Dict, pain_tags: Dict, clusters: Dict,
                              api_key: str) -> Dict:
    """Run Tavily searches and aggregate results."""
    queries = generate_search_queries(gsc_data, pain_tags, clusters)
    all_results = []
    competitor_topics = []
    reddit_posts = []

    for q in queries:
        results = search_tavily(q, api_key)
        for r in results:
            title = r.get('title', '')
            url = r.get('url', '')
            content = r.get('content', '')[:200]

            all_results.append({'title': title, 'url': url, 'content': content})

            if 'reddit.com' in url or 'quora.com' in url:
                reddit_posts.append({'title': title, 'url': url})
            else:
                competitor_topics.append({'title': title, 'url': url})

    return {
        'total_results': len(all_results),
        'queries_used': queries,
        'competitor_topics': competitor_topics[:15],
        'reddit_discussions': reddit_posts[:10],
    }


# ── Output ───────────────────────────────────────────────

def build_brief(website: str, gsc_data: Dict, published: Dict, pain_tags: Dict,
                brand: Dict, clusters: Dict, external: Optional[Dict] = None,
                website_dir: str = None, discovered_kw: Dict = None) -> Dict:
    """Merge all data into a structured brief."""
    # ── Feedback ──
    feedback = {}
    if website_dir:
        feedback = _read_feedback(website_dir)

    return {
        'meta': {
            'website': website,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'gsc_data_updated': gsc_data.get('updated'),
            'pain_points_updated': pain_tags.get('updated'),
            'published_articles': published.get('total', 0)
        },
        'gsc_opportunities': gsc_data.get('opportunities', [])[:20],
        'keyword_pool': {
            'e1_gsc_verified': gsc_data.get('e1_keywords', [])[:20],
            'e2_manual_verified': gsc_data.get('e2_keywords', [])[:20],
            'newly_discovered': [
                {'keyword': k, **v} for k, v in list(discovered_kw.items())[:30]
            ] if discovered_kw else [],
            'total_discovered': len(discovered_kw) if discovered_kw else 0
        },
        'published_coverage': {
            'total': published.get('total', 0),
            'articles': published.get('articles', []),
            'slugs': list(published.get('slugs', set()))[:50],
            'top_tags': sorted(published.get('all_tags', {}).items(),
                               key=lambda x: x[1], reverse=True)[:15]
        },
        'pain_point_tags': pain_tags.get('tags', {}),
        'total_pain_points': pain_tags.get('total_pain_points', 0),
        'topic_clusters': clusters.get('clusters', []),
        'brand_constraints': {
            'audience': brand.get('audience', [])
        },
        'external_signals': external or {'note': '未提供Tavily API Key，外部信号未获取'},
        'cannibalization': _run_cannibalization_check._result,
        'feedback': feedback
    }


def format_brief_markdown(brief: Dict) -> str:
    """Convert brief dict to AI-readable Markdown."""
    lines = []
    meta = brief['meta']
    ext = brief['external_signals']

    lines.append(f"# PLAN 情报简报 — {meta['website']}")
    lines.append(f"> 生成时间：{meta['generated_at']} | GSC更新：{meta['gsc_data_updated'] or '未知'} | 已发布：{meta['published_articles']}篇")
    lines.append("")

    # ── GSC opportunities ──
    opps = brief['gsc_opportunities']
    published_articles = brief.get('published_coverage', {}).get('articles', [])
    if opps:
        lines.append("## 📊 GSC 机会信号（已比对去重）")
        lines.append("")
        lines.append("| 关键词 | 展示量 | CTR | 排名 | 类型 | 已覆盖 |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for o in opps[:15]:
            kw = o.get('keyword', '')[:50]
            imp = o.get('impressions', '—')
            ctr = f"{o.get('ctr', 0)}%"
            pos = o.get('position', '—')
            typ = o.get('type', '')
            # Coverage = articles sharing >=2 DISTINCTIVE tokens (domain stopwords removed),
            # so generic "laser pointer" no longer matches everything.
            kw_tokens = _match_tokens(kw)
            matched = []
            for art in published_articles:
                slug = art.get('slug', '')
                art_tokens = _match_tokens(slug.replace('-', ' '))
                for t in art.get('tags', []):
                    art_tokens |= _match_tokens(t)
                if len(kw_tokens & art_tokens) >= 2:
                    matched.append(str(art.get('#', slug[:20])))
            if matched:
                refs = ','.join(matched[:3]) + ('…' if len(matched) > 3 else '')
                matched_str = f'{len(matched)}篇({refs})'
            else:
                matched_str = '空缺'
            lines.append(f"| {kw} | {imp} | {ctr} | {pos} | {typ} | {matched_str} |")
        lines.append("")

    # ── Newly discovered keywords ──
    new_kw = brief.get('keyword_pool', {}).get('newly_discovered', [])
    total_kw = brief.get('keyword_pool', {}).get('total_discovered', 0)
    if new_kw:
        lines.append(f"## 🔍 关键词池（累计 {total_kw} 词 | 本批 {len(new_kw)} 词）")
        lines.append("")
        # Group by intent
        by_intent = {}
        for item in new_kw:
            kw = item.get('keyword', '')
            intent = item.get('intent', '信息型')
            source = item.get('source', '')
            by_intent.setdefault(intent, []).append(f"{kw} `{source}`")

        for intent, items in by_intent.items():
            lines.append(f"### {intent}")
            for item in items[:10]:
                lines.append(f"- {item}")
            lines.append("")
        lines.append("")

    # ── Cannibalization ──
    cannibal_result = brief.get('cannibalization', '')
    if cannibal_result:
        lines.append("## ⚠️ 蚕食检测")
        lines.append("")
        lines.append("```")
        lines.append(cannibal_result[:2000])
        lines.append("```")
        lines.append("")

    # ── Published coverage ──
    pc = brief['published_coverage']
    lines.append(f"## 📝 已发布覆盖（{pc['total']}篇）")
    lines.append("")
    if pc['top_tags']:
        lines.append("**高频标签：**")
        for tag, cnt in pc['top_tags'][:10]:
            lines.append(f"- `{tag}` ×{cnt}")
    lines.append("")

    # ── Pain point tags ──
    pt = brief['pain_point_tags']
    if pt:
        lines.append(f"## 🏷️ 痛点标签（共{brief['total_pain_points']}条痛点）")
        lines.append("")
        sorted_pt = sorted(pt.items(), key=lambda x: x[1], reverse=True)
        for tag, count in sorted_pt:
            lines.append(f"- `{tag}`: {count}条")
        lines.append("")

    # ── Topic clusters ──
    clusters = brief['topic_clusters']
    if clusters:
        lines.append("## 📂 主题集群覆盖")
        lines.append("")
        lines.append("| 集群 | 关键词 | 已覆盖文章 |")
        lines.append("|---|---:|---|")
        for c in clusters:
            name = c.get('name', c) if isinstance(c, dict) else c
            kws = ', '.join(c.get('keywords', [])[:3]) if isinstance(c, dict) else ''
            cnt = c.get('article_count', '?') if isinstance(c, dict) else '?'
            lines.append(f"| {name} | {kws} | {cnt} |")
        lines.append("")

    # ── External signals ──
    if ext.get('queries_used'):
        lines.append("## 🌐 外部信号（Tavily搜索）")
        lines.append(f"\n搜索了 {len(ext['queries_used'])} 个方向：")
        for q in ext['queries_used']:
            lines.append(f"- `{q}`")

        comp = ext.get('competitor_topics', [])
        if comp:
            lines.append(f"\n### 竞品/外部内容（{len(comp)}条）")
            for item in comp[:10]:
                lines.append(f"- [{item['title']}]({item['url']})")

        reddit = ext.get('reddit_discussions', [])
        if reddit:
            lines.append(f"\n### Reddit/论坛讨论（{len(reddit)}条）")
            for item in reddit[:8]:
                lines.append(f"- [{item['title']}]({item['url']})")
    else:
        lines.append(f"## 🌐 外部信号\n\n{ext.get('note', '未获取')}")
        lines.append("")

    # ── Brand ──
    audience = brief['brand_constraints'].get('audience', [])
    if audience:
        lines.append("## 🎯 目标受众")
        for a in audience[:5]:
            lines.append(f"- {a}")
        lines.append("")

    # ── Feedback ──
    fb = brief.get('feedback', {})
    if fb:
        lines.append("## 🔁 上次反馈（自动避开）")
        if fb.get('avoid_topics'):
            lines.append(f"- 避免主题：{', '.join(fb['avoid_topics'])}")
        if fb.get('prefer_clusters'):
            lines.append(f"- 优先集群：{', '.join(fb['prefer_clusters'])}")
        if fb.get('notes'):
            lines.append(f"- 备注：{fb['notes']}")
        lines.append("")

    return '\n'.join(lines)


# ── Raw export auto-processing ──────────────────────────

def _process_raw_exports(website_dir: Path, website: str):
    """Check for raw exports in {website}/raw/gsc/ and {website}/raw/cms/.
    Process then delete original files (no _processed/ cruft)."""
    import subprocess

    project_root = website_dir.parent
    modules_dir = Path(__file__).resolve().parent

    # ── GSC Excel ──
    gsc_dir = website_dir / 'raw' / 'gsc'
    if gsc_dir.exists():
        for f in sorted(gsc_dir.glob('*.xlsx')):
            print(f'[0a] 新 GSC Excel: {f.name} → 导入...', file=sys.stderr)
            try:
                result = subprocess.run(
                    ['python3', str(modules_dir / 'gsc_importer.py'), str(f), website],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(project_root)
                )
                if result.returncode == 0:
                    f.unlink()
                    print(f'      ✅ 导入完成，已删除原文件', file=sys.stderr)
                else:
                    err = result.stderr.strip() or result.stdout.strip()
                    print(f'      ⚠️ 失败: {err[:120]}', file=sys.stderr)
            except Exception as e:
                print(f'      ⚠️ 异常: {e}', file=sys.stderr)

    # ── CMS JSON ──
    cms_dir = website_dir / 'raw' / 'cms'
    if cms_dir.exists():
        for f in sorted(cms_dir.glob('*.json')):
            print(f'[0b] 新 CMS JSON: {f.name} → 同步...', file=sys.stderr)

            if 'blog' in f.name.lower():
                cmd = ['python3', str(modules_dir / 'sync_blogs.py'), str(f), website]
            elif 'product' in f.name.lower():
                cmd = ['python3', str(modules_dir / 'sync_products.py'), str(f), website]
            else:
                continue

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                       cwd=str(project_root))
                if result.returncode == 0:
                    f.unlink()
                    print(f'      ✅ 同步完成，已删除原文件', file=sys.stderr)
                else:
                    err = result.stderr.strip() or result.stdout.strip()
                    print(f'      ⚠️ 失败: {err[:120]}', file=sys.stderr)
            except Exception as e:
                print(f'      ⚠️ 异常: {e}', file=sys.stderr)


def _read_feedback(website_dir: str) -> Dict:
    """Read plan-feedback.json if it exists."""
    fb_path = Path(website_dir) / 'research' / 'plan-feedback.json'
    if fb_path.exists():
        return json.loads(fb_path.read_text(encoding='utf-8'))
    return {}


def _expand_six_circles(seed: str, context_keywords: list = None) -> dict:
    """六圈法扩展：种子词 × 6维度 → 系统生成组合关键词。
    Returns {keyword: source} like keyword_discovery style."""
    results = {}

    # 圈1 修饰词 (modifiers)
    modifiers = ['best', 'top', 'review', 'vs', 'buy', 'price', 'guide', '2026', 'comparison']
    for m in modifiers:
        results[f'{m} {seed}'] = '六圈-修饰词'

    # 圈2 属性 (attributes) — use domain keywords from target-keywords.md clusters.
    # If none available, skip (generic noise is worse than no expansion).
    if context_keywords:
        for a in context_keywords[:6]:
            results[f'{a} {seed}'] = '六圈-属性'

    # 圈3 受众 (audience) — domain-agnostic
    audiences = ['beginners', 'professionals', 'small business', 'manufacturing',
                 'warehouse', 'retail', 'industrial', 'home']
    for aud in audiences[:5]:
        results[f'{seed} for {aud}'] = '六圈-受众'

    # 圈4 场景 (use case) — domain-agnostic
    scenes = ['production line', 'packaging', 'quality control', 'shipping',
              'inventory', 'marking', 'labeling', 'coding']
    for scene in scenes[:5]:
        results[f'{seed} for {scene}'] = '六圈-场景'

    # 圈5 问题意图 (question intent)
    questions = ['how to use', 'how to choose', 'what is', 'why', 'can',
                 'does', 'is it safe to use', 'how much does cost']
    for q in questions[:5]:
        results[f'{q} {seed}'] = '六圈-问题'

    # 圈6 长尾组合 (cross-circle combinations)
    cross = [f'best {attrs[0] if attrs else "portable"} {seed} for {audiences[0] if audiences else "beginners"}',
             f'top {seed} {scenes[0] if scenes else "marking"} guide']
    for c in cross[:2]:
        results[c] = '六圈-长尾'

    return results


def _expand_question_chain(seed: str) -> dict:
    """问题链展开：从种子词推导后续问题序列 (定义→操作→安全→购买)。
    Returns {keyword: source}."""
    results = {}

    # 定义层
    results[f'what is {seed}'] = '问题链-定义'
    results[f'how does {seed} work'] = '问题链-定义'

    # 操作层
    results[f'how to use {seed}'] = '问题链-操作'
    results[f'how to fix {seed}'] = '问题链-操作'

    # 安全层
    results[f'is {seed} safe'] = '问题链-安全'
    results[f'{seed} safety tips'] = '问题链-安全'

    # 购买层
    results[f'best {seed} under 100'] = '问题链-购买'
    results[f'{seed} buying guide'] = '问题链-购买'

    return results


def _discover_new_keywords(website_dir: str):
    """三路关键词发现：Google Suggest + 六圈法 + 问题链。
    保存到 {website}/data/discovered-keywords.json，每次累积去重。"""
    import importlib.util
    kd = None
    try:
        kd_path = Path(__file__).resolve().parent / 'keyword_discovery.py'
        spec = importlib.util.spec_from_file_location('keyword_discovery', kd_path)
        kd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kd)
    except Exception as e:
        print(f'      ⚠️ 关键词发现模块加载失败: {e}', file=sys.stderr)

    data_dir = Path(website_dir) / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    kw_file = data_dir / 'discovered-keywords.json'

    # Load existing pool: {keyword: {source, intent, date}}
    pool = {}
    if kw_file.exists():
        try:
            pool = json.loads(kw_file.read_text())
        except Exception:
            print(f'      ⚠️ discovered-keywords.json 解析失败，从头开始', file=sys.stderr)

    existing_set = {kw.lower() for kw in pool}

    # Get seeds from GSC
    manual_path = Path(website_dir) / 'context' / 'seo-data-manual.md'
    seeds = []
    if manual_path.exists():
        content = manual_path.read_text(encoding='utf-8')
        for m in re.finditer(r'\|\s*\d+\s*\|\s*([a-z0-9\s$-]+?)\s*\|', content, re.IGNORECASE):
            kw = m.group(1).strip().lower()
            if len(kw.split()) >= 2 and kw not in seeds and '---' not in kw and '关键词' not in kw:
                seeds.append(kw)
            if len(seeds) >= 3:
                break

    # Seeds from product data (live_products_report.md)
    product_seeds = _extract_product_seeds(website_dir)
    seeds = (seeds + product_seeds)[:6]  # merge, max 6 total

    # Get context keywords from target-keywords.md for six-circle attributes
    context_kw = []
    tk_path = Path(website_dir) / 'context' / 'target-keywords.md'
    if tk_path.exists():
        tk_content = tk_path.read_text(encoding='utf-8')
        for m in re.finditer(r'\*\*(?:Primary|Secondary):\*\*\s*(.+)', tk_content):
            for w in re.split(r'[,/]', m.group(1)):
                w = w.strip().lower()
                if len(w) > 2:
                    context_kw.append(w)

    if not seeds:
        return pool

    print(f'[0c] 关键词发现 — 种子: {seeds[:3]}', file=sys.stderr)
    today = datetime.now().strftime('%Y-%m-%d')
    new_count = {'suggest': 0, 'sixcircle': 0, 'question': 0}

    for seed in seeds[:3]:
        # 方法1: Google Suggest（失败重试1次，搜索间隔1秒）
        if kd:
            if seeds.index(seed) > 0:
                time.sleep(1)  # rate limit
            for attempt in range(2):
                try:
                    expanded = kd.expand_keywords(seed, depth=1)
                    filtered = kd.filter_keywords(expanded, seed)
                    for kw in filtered:
                        kl = kw.lower()
                        if kl not in existing_set:
                            intent = kd.classify_intent(kw)
                            pool[kl] = {'source': 'Google Suggest', 'intent': intent, 'date': today}
                            existing_set.add(kl)
                            new_count['suggest'] += 1
                    break  # success
                except Exception:
                    if attempt == 0:
                        time.sleep(2)
                    continue

        # 方法2: 六圈法
        try:
            for kw, source in _expand_six_circles(seed, context_kw[:8]).items():
                kl = kw.lower()
                if kl not in existing_set:
                    intent = kd.classify_intent(kw) if kd else '信息型'
                    pool[kl] = {'source': source, 'intent': intent, 'date': today}
                    existing_set.add(kl)
                    new_count['sixcircle'] += 1
        except Exception:
            pass

        # 方法3: 问题链
        try:
            for kw, source in _expand_question_chain(seed).items():
                kl = kw.lower()
                if kl not in existing_set:
                    intent = kd.classify_intent(kw) if kd else '信息型'
                    pool[kl] = {'source': source, 'intent': intent, 'date': today}
                    existing_set.add(kl)
                    new_count['question'] += 1
        except Exception:
            pass

    # Limit pool size (keep 300 newest)
    if len(pool) > 300:
        sorted_items = sorted(pool.items(), key=lambda x: x[1].get('date', ''), reverse=True)
        pool = dict(sorted_items[:300])

    kw_file.write_text(json.dumps(pool, ensure_ascii=False, indent=2))
    print(f'      ✅ Suggest:{new_count["suggest"]} 六圈:{new_count["sixcircle"]} 问题链:{new_count["question"]} | 累计 {len(pool)} 词',
          file=sys.stderr)
    return pool


def _run_cannibalization_check(website_dir: str):
    """Detect keyword cannibalization across published articles.

    Brief shows only the TOP (highest-similarity) pairs at a stricter 0.50 threshold
    to cut same-domain noise. Full report (0.45) is saved to a sidecar file so it is
    not lost. Previously this kept the *tail* of stdout (lowest similarity) and used a
    noisy 0.45 threshold that flooded the brief — both fixed here.

    Now uses seo_common.cannibal_pairs (cached checker) instead of subprocess.
    """
    website = Path(website_dir).name
    TOP_PAIRS = 10

    try:
        import seo_config
        # Full report (LOW threshold) → sidecar file, complete record
        full_data = seo_common.cannibal_pairs(
            website, threshold=seo_config.CANNIBAL_THRESHOLD_LOW, top_n=10**9)
        report_dir = Path(website_dir) / 'research'
        report_dir.mkdir(parents=True, exist_ok=True)
        checker = full_data.get('checker')
        all_pairs = full_data.get('all_pairs', [])
        if checker and all_pairs:
            full_text = checker.format_report(all_pairs)
            (report_dir / 'cannibalization-report.txt').write_text(full_text, encoding='utf-8')

        # Brief excerpt: top N pairs at MID threshold (output already sorted desc)
        strict_pairs = [p for p in all_pairs
                        if p['similarity'] >= seo_config.CANNIBAL_THRESHOLD_MID]
        lines = []
        if checker and strict_pairs:
            brief_text = checker.format_report(strict_pairs)
            lines = brief_text.splitlines()
        body, pair_count = [], 0
        for ln in lines:
            if ln.startswith(('🔴', '🟡', '🟢')):
                pair_count += 1
                if pair_count > TOP_PAIRS:
                    break
            body.append(ln)
        excerpt = '\n'.join(body).strip()
        total_flagged = sum(1 for ln in lines if ln.startswith(('🔴', '🟡', '🟢')))
        if total_flagged > TOP_PAIRS:
            excerpt += f'\n\n… 共 {total_flagged} 对 ≥0.50，完整见 research/cannibalization-report.txt'
        _run_cannibalization_check._result = excerpt
    except Exception as e:
        print(f'      ⚠️ 蚕食检测失败: {e}', file=sys.stderr)
        _run_cannibalization_check._result = ''


def _auto_update_clusters(website_dir: str, published_tags: Dict, language: str = 'en'):
    """Auto-update target-keywords.md with new keywords from article tags.
    - Adds new tags to existing clusters if they match cluster keywords
    - Suggests new clusters for unassigned high-frequency tags"""
    tk_path = Path(website_dir) / 'context' / 'target-keywords.md'
    if not tk_path.exists():
        return

    content = tk_path.read_text(encoding='utf-8')
    if not published_tags:
        return

    updated = content

    # Process each cluster section
    sections = re.split(r'(?=## Topic Cluster \d+:)', updated)
    assigned_tags = set()

    for i, section in enumerate(sections):
        if '## Topic Cluster' not in section:
            continue

        # Distinctive tokens come from the PRIMARY keyword only (clean signal).
        # Matching on the polluted Secondary line is what created cross-cluster sprawl.
        primary_tokens = set()
        pm = re.search(r'\*\*Primary:\*\*\s*(.+)', section)
        if pm:
            primary_tokens = _match_tokens(pm.group(1))

        existing_secondaries = set()
        sm = re.search(r'\*\*Secondary:\*\*\s*(.+)', section)
        if sm:
            existing_secondaries = set(w.strip().lower() for w in re.split(r'[,/]', sm.group(1)))

        # Only assign a tag if it shares a DISTINCTIVE token with the primary keyword,
        # and the tag is not itself fully generic (e.g. bare "laser pointer").
        new_tags = set()
        for tag, count in published_tags.items():
            if count < 2:
                continue  # skip single-occurrence tags
            tag_tokens = _match_tokens(tag)
            if not tag_tokens:
                continue  # fully generic tag → never pollute clusters
            if primary_tokens and (tag_tokens & primary_tokens):
                if tag.lower() not in existing_secondaries:
                    new_tags.add(tag)
                assigned_tags.add(tag.lower())

        if new_tags and sm:
            additions = [t for t in new_tags if t.lower() not in existing_secondaries]
            if additions:
                new_line = sm.group(0).rstrip() + ', ' + ', '.join(additions[:5])
                updated = updated.replace(sm.group(0), new_line)

    # Find unassigned high-frequency tags → suggest new cluster
    unassigned = {}
    for tag, count in published_tags.items():
        if tag.lower() not in assigned_tags and count >= 2 and len(tag) > 5:
            unassigned[tag] = count

    if unassigned and '## 🤖 建议新集群' not in updated:
        suggestion = '\n## 🤖 建议新集群（自动检测）\n\n以下标签出现较多但未归入任何集群：\n\n'
        for tag, count in sorted(unassigned.items(), key=lambda x: x[1], reverse=True)[:10]:
            suggestion += f'- `{tag}` ×{count}\n'
        updated += '\n' + suggestion

    if updated != content:
        tk_path.write_text(updated, encoding='utf-8')
        print(f'      ✅ target-keywords.md 已自动更新', file=sys.stderr)


def _extract_product_seeds(website_dir: str) -> list:
    """Extract seed keywords from live_products_report.md product attributes."""
    prod_path = Path(website_dir) / 'products' / 'live_products_report.md'
    if not prod_path.exists():
        return []

    content = prod_path.read_text(encoding='utf-8')
    seeds = []

    # 规格表: wavelength/power/material
    for m in re.finditer(r'\|\s*\d+\s*\|\s*\w+\s*\|\s*([^|]+?)\s*\|', content):
        title = m.group(1).strip()
        if len(title) > 5:
            seeds.append(title.lower()[:60])

    # 卖点/features
    for m in re.finditer(r'(?:rechargeable|waterproof|portable|compact|durable|professional|adjustable|USB|copper|aluminium|stainless|infrared|focus|lanyard|safety|key\s?lock)[\w\s-]+', content, re.IGNORECASE):
        s = m.group(0).strip().lower()
        if 5 < len(s) < 60:
            seeds.append(s)

    # 去重、取代表性样本
    seen = set()
    unique = []
    for s in seeds:
        s = s.strip()
        if s not in seen and len(s.split()) >= 2:
            seen.add(s)
            unique.append(s)
        if len(unique) >= 20:
            break

    return unique[:10]


def _deduplicate_opportunities(opps: List[Dict]) -> List[Dict]:
    """Deduplicate opportunities by keyword, keeping higher priority type."""
    seen = {}
    for o in opps:
        kw = o['keyword'].lower()
        if kw not in seen:
            seen[kw] = o
        else:
            # Quick wins > rising trends > low CTR
            priority = {'quick_win': 3, 'rising_trend': 2, 'low_ctr': 1, 'high_impression_low_ctr': 1}
            if priority.get(o['type'], 0) > priority.get(seen[kw]['type'], 0):
                seen[kw] = o
    return list(seen.values())


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='PLAN data collector')
    parser.add_argument('--website', required=True, help='Website name (laserpointerhub / mobilemarking)')
    parser.add_argument('--tavily-key', help='Tavily API key (optional, enables external signals)')
    parser.add_argument('--json', action='store_true', help='Output JSON instead of Markdown')
    parser.add_argument('--output', help='Output file path (optional, prints to stdout if not set)')
    args = parser.parse_args()

    # Determine paths
    project_root = Path(__file__).resolve().parents[2]
    website_dir = project_root / args.website

    if not website_dir.exists():
        print(f'Error: website directory not found: {website_dir}')
        sys.exit(1)

    # ── Step 0a: 自动处理原始导出文件 ──
    _process_raw_exports(website_dir, args.website)

    # ── Step 0b: 关键词自动发现（Google Suggest，持久化累积）──
    discovered_kw = _discover_new_keywords(str(website_dir))

    # ── Step 0c: 蚕食检测 ──
    _run_cannibalization_check(str(website_dir))

    # Collect internal data
    if not (website_dir / 'context' / 'seo-data-manual.md').exists():
        print(f'⚠️  seo-data-manual.md 不存在，GSC数据为空', file=sys.stderr)
    if not (website_dir / 'published' / 'published-index.json').exists():
        print(f'⚠️  published-index.json 不存在，已发布清单为空', file=sys.stderr)

    print(f'[1/5] 读取 GSC 数据...', file=sys.stderr)
    gsc_data = read_gsc_data(str(website_dir))

    print(f'[2/5] 读取已发布清单...', file=sys.stderr)
    published = read_published_index(str(website_dir))

    print(f'[3/5] 分析痛点标签...', file=sys.stderr)
    pain_tags = read_pain_point_tags(str(website_dir))

    print(f'[4/5] 读取品牌约束和主题集群...', file=sys.stderr)
    brand = read_brand_constraints(str(website_dir))
    clusters = read_clusters(str(website_dir), published.get('articles', []))
    _auto_update_clusters(str(website_dir), published.get('all_tags', {}))

    # External signals (Tavily) — user approval required
    external = None
    if not args.tavily_key:
        # Tavily API key from project-root data/plan-config.json
        shared = project_root / 'data' / 'plan-config.json'
        if shared.exists():
            try:
                args.tavily_key = json.loads(shared.read_text()).get('tavily_api_key')
            except Exception:
                pass

    if args.tavily_key:
        print(f'[5/5] 搜索外部信号 (Tavily)...', file=sys.stderr)
        external = collect_external_signals(gsc_data, pain_tags, clusters, args.tavily_key)
        # Save for accumulation
        sig_file = website_dir / 'data' / 'external-signals.json'
        try:
            old_sigs = json.loads(sig_file.read_text()) if sig_file.exists() else {}
            merged = {**old_sigs, 'query_used': external.get('queries_used', []),
                      'competitor_topics': (old_sigs.get('competitor_topics', []) +
                                            external.get('competitor_topics', []))[:30],
                      'reddit_discussions': (old_sigs.get('reddit_discussions', []) +
                                             external.get('reddit_discussions', []))[:20]}
            sig_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
        except Exception:
            pass
    else:
        print(f'[5/5] 跳过外部信号 (Tavily key 未配置)', file=sys.stderr)

    # Build brief
    brief = build_brief(args.website, gsc_data, published, pain_tags, brand, clusters, external,
                        str(website_dir), discovered_kw)

    # Output
    if args.json:
        output = json.dumps(brief, ensure_ascii=False, indent=2)
    else:
        output = format_brief_markdown(brief)

    if args.output:
        output_path = Path(args.output)
    else:
        # Default: {website}/research/plan-brief-YYYY-MM-DD.md
        research_dir = website_dir / 'research'
        research_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        output_path = research_dir / f'plan-brief-{date_str}.md'

    output_path.write_text(output, encoding='utf-8')
    print(f'✅ 简报: {output_path}', file=sys.stderr)

    # ── Always write a JSON sidecar for downstream scripted scoring (plan_scorer.py) ──
    if not args.json:
        research_dir = website_dir / 'research'
        research_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        json_path = research_dir / f'plan-brief-{date_str}.json'
        json_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2, default=list),
                             encoding='utf-8')
        print(f'✅ JSON: {json_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
