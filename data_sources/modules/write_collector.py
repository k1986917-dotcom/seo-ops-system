#!/usr/bin/env python3
"""WRITE data collector — three-stage pipeline for article writing.

Stage 0 (validate): Check material pack, load context files.
Stage 1 (post-process): Scrub watermarks, extract & validate links, 
                         generate frontmatter, score quality.
Stage 2 (register): Register in internal-links-map, generate retroactive
                     linking checklist, cleanup material pack.

Usage:
  python3 write_collector.py validate --website <w> --topic <t> [--pack <path>]
  python3 write_collector.py post-process --website <w> --draft <path> [--pack <path>]
  python3 write_collector.py register --website <w> --draft <path> --pack <path> \
      --new-url <url> --title <title> --keyword <keyword>
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]

# Where the per-website workspaces ({website}/context, /drafts, ...) live.
# Defaults to BASE_DIR so standalone CLI use is unchanged; seo-ops sets
# SEO_SITES_DIR because its Legacy workspace sits under data/legacy_workflow/.
# BASE_DIR stays the repo root — it still resolves sibling module scripts.
SITES_DIR = Path(os.environ.get('SEO_SITES_DIR') or BASE_DIR)

# Shared pipeline primitives (token matching, cannibalization, freshness)
try:
    import seo_common  # available when run as a script from modules dir
    import research_collector  # available when run as a script from modules dir
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        'seo_common', Path(__file__).resolve().parent / 'seo_common.py')
    seo_common = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_common)
    _spec2 = _ilu.spec_from_file_location(
        'research_collector', Path(__file__).resolve().parent / 'research_collector.py')
    research_collector = _ilu.module_from_spec(_spec2)
    _spec2.loader.exec_module(research_collector)

# Centralized thresholds/weights
try:
    import seo_config
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        'seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    seo_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_config)


def _slugify(topic: str) -> str:
    return seo_common.slugify(topic)


# _date_str / _extract_section were local duplicates of seo_common.today_str /
# seo_common.extract_section. Aliased here so existing internal call sites keep
# working without a wide rename.
_date_str = seo_common.today_str
_extract_section = seo_common.extract_section


# ═══════════════════════════════════════════════════════════
# Stage 0: Validate material pack + load context
# ═══════════════════════════════════════════════════════════

def validate(website: str, topic: str, pack_path: Optional[str] = None) -> str:
    website_dir = SITES_DIR / website
    if not website_dir.exists():
        print(f'Error: website directory not found: {website_dir}', file=sys.stderr)
        sys.exit(1)

    slug = _slugify(topic)
    date_str = _date_str()

    # ── Find material pack ──
    pack_file = None
    if pack_path:
        pack_file = Path(pack_path)
    else:
        packs_dir = website_dir / 'material-packs'
        if packs_dir.exists():
            candidates = sorted(packs_dir.glob(f'{slug}-*.md'), reverse=True)
            if not candidates:
                candidates = sorted(packs_dir.glob('*.md'),
                                    key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                pack_file = candidates[0]

    # Phase B: inherit topic-context (intent/tier/signals) if RESEARCH/PLAN left one
    topic_ctx = seo_common.read_topic_context(website_dir, slug)

    report = {
        'website': website, 'topic': topic, 'slug': slug, 'date': date_str,
        'pack_found': False, 'pack_path': '', 'pack_sections': {},
        'mandatory_ok': False, 'missing_fields': [],
        'context_files': {}, 'domain': '',
        'topic_context': topic_ctx,
        'link_candidates_blog': [], 'link_candidates_product': [],
        'link_candidates_external': [],
    }

    # ── Link candidates: always compute, even without material pack ──
    _compute_link_candidates(website_dir, topic, slug, report)

    if not pack_file or not pack_file.exists():
        report['missing_fields'] = ['material pack file not found']
        return _format_validate_report(report)

    report['pack_found'] = True
    report['pack_path'] = str(pack_file)
    pack_content = pack_file.read_text(encoding='utf-8')

    # ── Extract material pack E section URLs (fresh from user search) ──
    e_section = _extract_section(pack_content, '## E.', '\n## ')
    report['pack_external_urls'] = re.findall(r'https?://[^\s)\]]+', e_section)
    report['pack_external_titles'] = re.findall(r'\*\*\[\d+\]\*\*\s*\[([^\]]+)\]', e_section)

    # ── Extract material pack sections ──
    for section in ['A. 用户痛点', 'B. 实时市场数据', 'C. 案例素材', 'D. 信息增量',
                     'E. 权威引用', 'F. 竞品内容分析', 'G. PAA 真问句', 'H. 作者']:
        sec_content = _extract_section(pack_content, f'## {section}', '\n## ')
        report['pack_sections'][section[0]] = bool(sec_content.strip())

    # Count items in sections
    a_count = len(re.findall(r'痛点\d+', pack_content)) + len(re.findall(r'\[search\]|\[library\]', _extract_section(pack_content, '## A.', '\n## ')))
    e_urls = re.findall(r'https?://\S+', _extract_section(pack_content, '## E.', '\n## '))
    report['pack_sections']['A_count'] = min(a_count, 99)
    report['pack_sections']['E_count'] = len(e_urls)

    # Mandatory check
    mandatory = []
    if not report['pack_sections'].get('A'):
        mandatory.append('A (用户痛点)')
    if not report['pack_sections'].get('E'):
        mandatory.append('E (权威引用)')
    kw_match = re.search(r'\*\*目标主关键词\*\*:\s*(.+)', pack_content)
    if not kw_match or not kw_match.group(1).strip():
        mandatory.append('目标主关键词')
    report['missing_fields'] = mandatory
    report['mandatory_ok'] = len(mandatory) == 0

    # ── Load context files ──
    context_dir = website_dir / 'context'
    context_files = {
        'brand_voice': 'brand-voice.md',
        'writing_examples': 'writing-examples.md',
        'style_guide': 'style-guide.md',
        'seo_guidelines': 'seo-guidelines.md',
        'target_keywords': 'target-keywords.md',
    }

    for key, fname in context_files.items():
        fpath = context_dir / fname
        if fpath.exists():
            report['context_files'][key] = fpath.read_text(encoding='utf-8')[:3000]

    # Internal links map
    ilm_path = context_dir / 'internal-links-map.md'
    if ilm_path.exists():
        ilm = ilm_path.read_text(encoding='utf-8')
        report['context_files']['internal_links_map'] = ilm[:5000]

    # Live products
    lpr_path = website_dir / 'products' / 'live_products_report.md'
    if lpr_path.exists():
        report['context_files']['live_products'] = lpr_path.read_text(encoding='utf-8')[:3000]

    # Domain
    ilm = report['context_files'].get('internal_links_map', '')
    domain_m = re.search(r'(https?://[^/]+)/blog/', ilm)
    if domain_m:
        report['domain'] = domain_m.group(1)
    else:
        report['domain'] = f'https://{website}.com'

    return _format_validate_report(report)


def _format_validate_report(report: Dict) -> str:
    lines = []
    lines.append(f"# WRITE 校验报告 — {report['topic']}")
    lines.append(f"> 网站: {report['website']} | Slug: {report['slug']} | 日期: {report['date']}")
    lines.append('')

    # Phase B: topic-context (intent/tier carried from PLAN/RESEARCH)
    ctx = report.get('topic_context')
    if ctx:
        src = ctx.get('source', '?')
        tag = '🎯 PLAN' if src == 'plan' else '🔧 启发式'
        lines.append(f'## 主题上下文（{tag}）')
        lines.append(f"- 意图: {ctx.get('intent','?')} | 层级: {ctx.get('tier','?')}"
                     + (f" | PLAN分: {ctx.get('plan_score')}" if ctx.get('plan_score') is not None else ''))
        if ctx.get('signals'):
            sig = ctx['signals']
            lines.append(f"- 信号: 痛点 {sig.get('pain','—')} | 缺口 {sig.get('cluster_gap','—')} | 需求 {sig.get('demand','—')}")
        if ctx.get('cannibal_risk'):
            lines.append(f"- 蚕食预判: {ctx['cannibal_risk']}（段2 会做正文级复核）")
        if ctx.get('guidance'):
            lines.append(f"- ✍️ 写作指引: {ctx['guidance']}")
        lines.append('')

    # Link candidates (always shown, even without material pack)
    blog_cands = report.get('link_candidates_blog', [])
    prod_cands = report.get('link_candidates_product', [])
    ext_cands = report.get('link_candidates_external', [])
    pack_urls = report.get('pack_external_urls', [])
    pack_titles = report.get('pack_external_titles', [])
    if blog_cands or prod_cands or ext_cands or pack_urls:
        lines.append('## 🔗 推荐链接（AI 从中选，不用读全量地图）')
        # External: pack first (freshly searched), then library as fallback
        if pack_urls:
            lines.append('### 🔥 外链 — 素材包（你搜回来的，最高优先，必选 ≥2 条）')
            for i, url in enumerate(pack_urls[:6]):
                title = pack_titles[i] if i < len(pack_titles) else url[:55]
                lines.append(f"- [{title[:55]}]({url})")
        if ext_cands:
            tag = '外链 — 素材库补充（上面不够再从这选）' if pack_urls else '外链 — 素材库（选 ≥2 条）'
            lines.append(f'### {tag}')
            for c in ext_cands[:3]:
                lines.append(f"- [{c.get('title','')[:50]}]({c['url']}) (得分{c['score']})")
        if blog_cands:
            lines.append('### 博客内链候选（~1000词/条，按字数动态）')
            for c in blog_cands:
                tags_str = ', '.join(c.get('tags', [])[:3]) if c.get('tags') else ''
                lines.append(f"- [{c['title'][:50]}]({c['url']}) — `{c['keyword']}` "
                             f"(得分{c['score']}) {tags_str}")
        if prod_cands:
            lines.append('### 产品内链候选（选 1-3 条）')
            for c in prod_cands:
                lines.append(f"- {c['url']}")
        lines.append('')

    # Material pack status
    if not report['pack_found']:
        lines.append('## ❌ 素材包未找到')
        lines.append(f'> 路径: {report["website"]}/material-packs/{report["slug"]}-*.md')
        lines.append('')
        return '\n'.join(lines)

    lines.append(f'## 素材包校验')
    lines.append(f'- 路径: {report["pack_path"]}')
    lines.append(f'- 必填项: {"✅ 通过" if report["mandatory_ok"] else "❌ 缺失"}')
    if report['missing_fields']:
        for mf in report['missing_fields']:
            lines.append(f'  - 缺失: {mf}')
    lines.append(f'- A 用户痛点: {"✅" if report["pack_sections"].get("A") else "❌"} ({report["pack_sections"].get("A_count", 0)}条)')
    lines.append(f'- E 权威引用: {"✅" if report["pack_sections"].get("E") else "❌"} ({report["pack_sections"].get("E_count", 0)}条)')
    lines.append('')

    # Context files
    lines.append('## 已加载 Context')
    for key in ['brand_voice', 'writing_examples', 'style_guide', 'seo_guidelines', 'target_keywords']:
        has = key in report['context_files'] and len(report['context_files'][key]) > 100
        lines.append(f'- {key}: {"✅" if has else "❌"}')
    lines.append(f'- internal_links_map: {"✅" if "internal_links_map" in report["context_files"] else "❌"}')
    lines.append(f'- live_products: {"✅" if "live_products" in report["context_files"] else "❌"}')
    lines.append(f'- domain: {report["domain"]}')
    lines.append('')

    # Context content (appended for AI to read)
    if report['context_files']:
        lines.append('---')
        lines.append('')
        lines.append('## Context 文件内容')
        for key, content in report['context_files'].items():
            if content:
                lines.append(f'### {key}')
                lines.append('```')
                lines.append(content[:3000])
                lines.append('```')
                lines.append('')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
# Stage 1: Post-process draft
# ═══════════════════════════════════════════════════════════

def _parse_frontmatter(content: str) -> Tuple[Dict, str]:
    if not content.startswith('---'):
        return {}, content
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content
    body = parts[2]
    meta = {}
    for line in parts[1].strip().split('\n'):
        line = line.strip()
        # Key may contain spaces (e.g. "SEO Title"); split on the FIRST colon only.
        # The old r'^(\S+?)\s*:\s*(.+)' failed to match multi-word keys entirely,
        # making every "SEO Title/Description/Keywords" field report as missing.
        m = re.match(r'^([^:]+?)\s*:\s*(.+)', line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key in meta:
                if isinstance(meta[key], list):
                    meta[key].append(val)
                else:
                    meta[key] = [meta[key], val]
            else:
                meta[key] = val
    return meta, body


def _extract_body_links(body: str, domain: str) -> Tuple[List[str], List[str], List[str]]:
    """Extract links from body, classify as blog / product / external."""
    blog_links = []
    product_links = []
    external_links = []
    seen = set()

    for m in re.finditer(r'\[([^\]]*)\]\(([^)]+)\)', body):
        url = m.group(2)
        text = m.group(1)
        if url in seen:
            continue
        seen.add(url)

        if domain and domain in url:
            if '/blog/' in url:
                blog_links.append({'url': url, 'text': text})
            elif '/p-' in url or '/product' in url:
                product_links.append({'url': url, 'text': text})
            else:
                blog_links.append({'url': url, 'text': text})
        else:
            external_links.append({'url': url, 'text': text})

    return blog_links, product_links, external_links


def _load_valid_internal_urls(website_dir: Path) -> Tuple[set, set]:
    """Parse internal-links-map.md + live_products_report.md → valid blog & product URLs.
    Excludes draft-status blog URLs (only published articles are safe to link to)."""
    ilm = website_dir / 'context' / 'internal-links-map.md'
    blog_urls = set()
    product_urls = set()
    draft_slugs = set()

    # Blog URLs from ILM (published only — exclude draft URLs to prevent 404)
    if ilm.exists():
        content = ilm.read_text(encoding='utf-8')
        # Collect draft URLs from metadata blocks
        draft_urls = set()
        for block in re.split(r'\n(?=### #\d+)', content):
            if '- 状态: draft' in block:
                for m in re.finditer(r'- URL:\s*(https?://\S+)', block):
                    draft_urls.add(m.group(1))
        for m in re.finditer(r'https?://[^)\s`]+', content):
            url = m.group(0)
            if '/blog/' in url and url not in draft_urls:
                blog_urls.add(url)

    # Product URLs from live_products_report.md (authoritative source)
    lpr = website_dir / 'products' / 'live_products_report.md'
    if lpr.exists():
        for url in re.findall(r'\[链接\]\((https?://[^)]+)\)', lpr.read_text(encoding='utf-8')):
            product_urls.add(url)

    return blog_urls, product_urls


def _load_valid_external_urls(material_pack: str, website_dir: Path) -> set:
    """Load external URLs from material pack Section E + external-sources-library."""
    valid = set()

    if material_pack:
        e_section = _extract_section(material_pack, '## E.', '\n## ')
        valid.update(re.findall(r'(https?://[^\s\|\)]+)', e_section))

    lib_path = website_dir / 'context' / 'external-sources-library.md'
    if lib_path.exists():
        valid.update(re.findall(r'(https?://[^\s\|\)]+)', lib_path.read_text(encoding='utf-8')))

    return valid


def post_process(website: str, draft_path: str, pack_path: Optional[str] = None, apply: bool = False, force: bool = False) -> tuple:
    website_dir = SITES_DIR / website
    draft_file = Path(draft_path)
    if not draft_file.exists():
        draft_file = website_dir / 'drafts' / draft_path
    if not draft_file.exists():
        print(f'Error: draft file not found: {draft_path}', file=sys.stderr)
        sys.exit(1)

    original = draft_file.read_text(encoding='utf-8')

# ── Step 1: Scrub ──
    print('[1/5] scrubbing AI watermarks...', file=sys.stderr)
    try:
        import importlib.util
        scrubber_path = BASE_DIR / 'data_sources' / 'modules' / 'content_scrubber.py'
        spec = importlib.util.spec_from_file_location('content_scrubber', str(scrubber_path))
        scrub_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scrub_module)
        scrubbed = scrub_module.scrub_content(original)
    except Exception as e:
        print(f'       ⚠️ scrub failed: {e}, using original', file=sys.stderr)
        scrubbed = original

    # ── Parse frontmatter and body ──
    meta, body = _parse_frontmatter(scrubbed)

    # ── Step 1b: Deduplicate URLs in body (keep first, remove rest) ──
    seen_urls = set()
    dedup_count = 0

    def _dedup_url(match):
        nonlocal dedup_count
        url = match.group(2)
        if url in seen_urls:
            dedup_count += 1
            return match.group(1)  # keep text, drop link markup
        seen_urls.add(url)
        return match.group(0)

    # Skip retroactive linking section (it contains intentional self-links)
    retro_pos = body.find('## 回溯链接')
    if retro_pos > 0:
        pre_retro = body[:retro_pos]
        post_retro = body[retro_pos:]
        pre_retro = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _dedup_url, pre_retro)
        body = pre_retro + post_retro
    else:
        body = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _dedup_url, body)

    if dedup_count > 0:
        print(f'       🔗 去重 {dedup_count} 条重复URL', file=sys.stderr)

    # Analyze the scrubbed/deduplicated candidate without touching the operator's
    # draft. write/SKILL.md defines the first post-process run as "不改文件";
    # the real draft is only replaced after every hard gate passes with --apply.
    fm_text = ''
    if scrubbed.startswith('---'):
        parts = scrubbed.split('---', 2)
        if len(parts) >= 2:
            fm_text = parts[1]
    final_content = f'---{fm_text}---\n\n{body}'
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f'.{draft_file.stem}-post-process-',
        suffix='.md',
        dir=draft_file.parent,
    )
    os.close(temp_fd)
    analysis_file = Path(temp_name)
    analysis_file.write_text(final_content, encoding='utf-8')

    # ── Step 2: Extract links ──
    print('[2/5] extracting & validating links...', file=sys.stderr)

    ilm = website_dir / 'context' / 'internal-links-map.md'
    ilm_content = ilm.read_text(encoding='utf-8') if ilm.exists() else ''
    domain_m = re.search(r'(https?://[^/]+)/blog/', ilm_content)
    domain = domain_m.group(1) if domain_m else f'https://{website}.com'

    blog_links, product_links, external_links = _extract_body_links(body, domain)

    # Load valid URLs
    valid_blogs, valid_products = _load_valid_internal_urls(website_dir)
    pack_content = ''
    if pack_path:
        p = Path(pack_path)
        if not p.exists():
            p = website_dir / 'material-packs' / pack_path
        if p.exists():
            pack_content = p.read_text(encoding='utf-8')
    valid_externals = _load_valid_external_urls(pack_content, website_dir)

    # Validate internal blog links
    link_issues = []
    for link in blog_links:
        if link['url'] not in valid_blogs:
            link_issues.append({'type': 'invalid_blog_url', 'url': link['url'], 'severity': 'error'})

    # Product links validated against live_products_report.md
    for link in product_links:
        if link['url'] not in valid_products:
            link_issues.append({'type': 'invalid_product_url', 'url': link['url'], 'severity': 'warn',
                               'msg': '产品URL不在 live_products_report.md 中，可能已下架或链接错误'})

    # Validate external links
    for link in external_links:
        if link['url'] not in valid_externals:
            link_issues.append({'type': 'invalid_external_url', 'url': link['url'], 'severity': 'warn'})

    # ── Step 3: Compute dynamic link limits (by word count, main body only) ──
    print('[3/5] checking link limits (word-count-based)...', file=sys.stderr)
    main_body = body[:retro_pos] if retro_pos > 0 else body
    total_chars = len(main_body)
    body_wc = len(re.findall(r'\b\w+\b', main_body))

    # Blog links: ~1 per 1000 words (LINK_RATIO_BLOG)
    blog_floor = max(2, min(8, round(body_wc / seo_config.LINK_RATIO_BLOG)))
    blog_ceil = blog_floor + 2
    # Product links: ~1 per 1300 words (LINK_RATIO_PRODUCT)
    prod_floor = max(1, min(4, round(body_wc / seo_config.LINK_RATIO_PRODUCT)))
    prod_ceil = prod_floor + 1
    # External links: ~1 per 800 words (LINK_RATIO_EXTERNAL)
    ext_floor = max(2, min(8, round(body_wc / seo_config.LINK_RATIO_EXTERNAL)))
    ext_ceil = ext_floor + 2

    blog_count = len(blog_links)
    product_count = len(product_links)
    ext_count = len(external_links)

    if blog_count > blog_ceil:
        link_issues.append({'type': 'too_many_blog_links', 'current': blog_count,
                            'max': blog_ceil, 'severity': 'error',
                            'msg': f'{body_wc}词 → 博客内链上限{blog_ceil}'})
    if blog_count > 0 and blog_count < blog_floor:
        link_issues.append({'type': 'too_few_blog_links', 'current': blog_count,
                            'min': blog_floor, 'severity': 'error',
                            'msg': f'{body_wc}词 → 博客内链下限{blog_floor}'})
    if product_count > prod_ceil:
        link_issues.append({'type': 'too_many_product_links', 'current': product_count,
                            'max': prod_ceil, 'severity': 'error',
                            'msg': f'{body_wc}词 → 产品内链上限{prod_ceil}'})
    if product_count > 0 and product_count < prod_floor:
        link_issues.append({'type': 'too_few_product_links', 'current': product_count,
                            'min': prod_floor, 'severity': 'error',
                            'msg': f'{body_wc}词 → 产品内链下限{prod_floor}'})
    if ext_count < ext_floor:
        link_issues.append({'type': 'too_few_external_links', 'current': ext_count,
                            'min': ext_floor, 'severity': 'error',
                            'msg': f'{body_wc}词 → 外链下限{ext_floor}'})
    if ext_count > ext_ceil:
        link_issues.append({'type': 'too_many_external_links', 'current': ext_count,
                            'max': ext_ceil, 'severity': 'error',
                            'msg': f'{body_wc}词 → 外链上限{ext_ceil}'})

    # ── Step 3b: Position checks ──
    total_chars = len(body)

    # Self-link check (main body only, not retro section)
    draft_slug = meta.get('Slug', '')
    self_links = []
    for bl in blog_links:
        if draft_slug and draft_slug in bl['url'] and bl['url'] in main_body:
            self_links.append(bl['url'])
    if self_links:
        link_issues.append({'type': 'self_link', 'severity': 'error',
                            'msg': f'文章链接了自己（{self_links[0][:60]}），这没有SEO价值'})

    # Blog links: should be spread across 25%/50%/75% zones
    zones = {'前25%':0,'25-50%':0,'50-75%':0,'后25%':0}
    for bl in blog_links:
        burl = bl['url'].rstrip('/')
        for m in re.finditer(r'\[([^\]]*)\]\((' + re.escape(burl) + r')', body):
            zone = int(m.start() / total_chars * 4)
            zname = ['前25%','25-50%','50-75%','后25%'][min(zone,3)]
            zones[zname] += 1
            break
    empty_zones = [z for z,c in zones.items() if c == 0]
    if len(empty_zones) >= 2:
        zone_detail = ', '.join(f'{z}:{c}' for z, c in zones.items())
        link_issues.append({'type': 'links_not_spread', 'severity': 'warn',
                            'msg': f'博客内链分布不均（{zone_detail}），{len(empty_zones)}个区域无链接'})

    # Product links: must NOT appear in first 20%
    early_products = []
    for prod in product_links:
        purl = prod['url'].rstrip('/')
        for m in re.finditer(r'\[([^\]]*)\]\((' + re.escape(purl) + r')', body):
            pct = m.start() / total_chars
            if pct < 0.2:
                early_products.append((int(pct*100), purl[:50]))
    if early_products:
        link_issues.append({'type': 'product_link_too_early', 'severity': 'error',
                            'msg': f'{len(early_products)}个产品链接在前20%区域（位置{early_products[0][0]}%）。'
                                   f'产品链接禁止出现在文章前20%，应放在解决方案部分或FAQ之前。'})

    # ── Check SEO fields ──
    seo_issues = []
    for field in ['SEO Title', 'SEO Description', 'SEO Keywords']:
        if not meta.get(field):
            seo_issues.append(f'❌ Missing {field}')
    if seo_issues:
        link_issues.extend([{'type': 'missing_seo_field', 'url': s, 'severity': 'error'}
                           for s in seo_issues])

    # ── Step 4: Score quality ──
    print('[4/5] scoring quality...', file=sys.stderr)
    scorer = BASE_DIR / 'data_sources' / 'modules' / 'content_scorer.py'
    score = None
    scorer_output = ''
    if scorer.exists():
        try:
            result = subprocess.run(
                ['python3', str(scorer), str(analysis_file), '--json'],
                capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR)
            )
            scorer_output = result.stdout.strip()
            score_json = json.loads(scorer_output)
            score = float(score_json.get('composite_score', 0))
            # Keep a short text snapshot for the report so humans still see a summary
            passed_str = 'PASS' if score_json.get('passed') else 'FAIL'
            scorer_output = f"Composite Score: {score}/100 ({passed_str})"
        except Exception as e:
            scorer_output = f'⚠️ scoring failed: {e}'

    # ── Step 4b: Cannibalization gate (NEW draft vs published) ──
    # This is the guard the pipeline was missing. The body now exists, so we can
    # actually answer "will this article cannibalize an existing one?".
    # Block threshold = CANNIBAL_THRESHOLD_HIGH (was 0.65, lowered to 0.55 per
    # SEO review — same-vertical terms inflate TF-IDF).
    print('[4b/5] cannibalization gate...', file=sys.stderr)
    cannibal = {'max_sim': 0.0, 'top': []}
    cannibal_block = False
    cannibal_error = ''
    try:
        cannibal = seo_common.cannibal_new_article(
            website, str(analysis_file), threshold=seo_config.CANNIBAL_THRESHOLD_LOW)
        if cannibal['max_sim'] >= seo_config.CANNIBAL_THRESHOLD_HIGH:
            cannibal_block = True
    except Exception as e:
        cannibal_error = str(e)
        cannibal = {'max_sim': 0.0, 'top': [], 'note': f'check failed: {e}'}

    # ── Step 5: Generate frontmatter ──
    print('[5/5] generating frontmatter...', file=sys.stderr)
    fm = _generate_frontmatter(meta, body, blog_links, product_links, external_links, domain)

    # ── Build report ──
    lines = []
    lines.append('# POST-PROCESS 报告')
    lines.append(f'> 文件: {draft_file.name} | 日期: {_date_str()}')
    lines.append('')

    # Link report
    lines.append('## 链接校验')
    lines.append(f'- 文章内链: {blog_count} ({body_wc}词 → 下限{blog_floor}上限{blog_ceil}) → {"✅" if blog_floor <= blog_count <= blog_ceil else "❌"}')
    lines.append(f'- 产品内链: {product_count} ({body_wc}词 → 下限{prod_floor}上限{prod_ceil}) → {"✅" if prod_floor <= product_count <= prod_ceil else "❌"}')
    lines.append(f'- 外链: {ext_count} ({body_wc}词 → 下限{ext_floor}上限{ext_ceil}) → {"✅" if ext_floor <= ext_count <= ext_ceil else "❌"}')
    lines.append('')

    if blog_links:
        lines.append('### 文章内链')
        for l in blog_links:
            valid = '✅' if l['url'] in valid_blogs else '❌'
            lines.append(f'- {valid} [{l["text"]}]({l["url"]})')
        lines.append('')

    if product_links:
        lines.append('### 产品内链')
        for l in product_links:
            valid = '✅' if l['url'] in valid_products else '⚠️'
            lines.append(f'- {valid} [{l["text"]}]({l["url"]})')
        lines.append('')

    if external_links:
        lines.append('### 外链')
        for l in external_links:
            valid = '✅' if l['url'] in valid_externals else '⚠️'
            lines.append(f'- {valid} [{l["text"]}]({l["url"]})')
        lines.append('')

    if link_issues:
        lines.append('## ⚠️ 链接问题')
        for issue in link_issues:
            lines.append(f'- [{issue["severity"]}] {issue["type"]}: {issue.get("url", "")}')
            if 'current' in issue and 'max' in issue:
                lines.append(f'  (当前 {issue["current"]}, 上限 {issue["max"]})')
            elif 'current' in issue and 'min' in issue:
                lines.append(f'  (当前 {issue["current"]}, 下限 {issue["min"]})')
        lines.append('')

    # Generated frontmatter
    lines.append('## 生成的 Frontmatter')
    lines.append('```yaml')
    lines.append(fm)
    lines.append('```')
    lines.append('')

    # Cannibalization gate
    lines.append('## 🔪 蚕食门控（新文 vs 已发布）')
    ms = cannibal.get('max_sim', 0.0)
    if cannibal_error:
        lines.append(f'- ❌ **检查失败**：{cannibal_error}')
    elif cannibal_block:
        lines.append(f'- ❌ **阻塞**：最高相似度 {ms} ≥ {seo_config.CANNIBAL_THRESHOLD_HIGH} — 与已发布文章重度重叠，必须换角度或合并')
    elif ms >= seo_config.CANNIBAL_THRESHOLD_MID:
        lines.append(f'- ⚠️ 注意：最高相似度 {ms}（{seo_config.CANNIBAL_THRESHOLD_MID}-{seo_config.CANNIBAL_THRESHOLD_HIGH}）— 需明确差异化')
    else:
        lines.append(f'- ✅ 安全：最高相似度 {ms} < {seo_config.CANNIBAL_THRESHOLD_MID}')
    for t in cannibal.get('top', [])[:3]:
        lines.append(f'  - {seo_common.risk_label(t["similarity"])} {t["similarity"]} vs `{t["existing_slug"]}`')
    lines.append('')

    # Score
    lines.append('## 质量评分')
    if score is not None:
        passed = score >= seo_config.PASS_SCORE
        lines.append(f'- 总分: {score} → {"✅ 通过" if passed else f"❌ 未达标 (需 ≥{seo_config.PASS_SCORE})"}')
    else:
        lines.append('- ⚠️ 评分失败，请手动检查')
    lines.append('')

    # Overall gate verdict (score + cannibalization both must pass)
    lines.append('## 🚦 总门控')
    score_ok = (score is not None and score >= seo_config.PASS_SCORE)
    gate_passed = score_ok and not cannibal_error and (not cannibal_block or force)
    if cannibal_error:
        lines.append('- ❌ **不可进入段3**：蚕食检查运行失败，必须修复检查器并重跑。')
    elif cannibal_block and not force:
        lines.append(f'- ❌ **不可进入段3**：蚕食 ≥{seo_config.CANNIBAL_THRESHOLD_HIGH}。修正正文差异化后重跑段2。')
    elif not score_ok:
        lines.append(f'- ❌ **不可进入段3**：评分 <{seo_config.PASS_SCORE}。')
    else:
        lines.append('- ✅ 通过，可进入段3 register。')
    lines.append('')
    lines.append('```')
    lines.append(scorer_output[:2000])
    lines.append('```')
    lines.append('')

    lines.append('')

    # ── Actionable fix summary (AI reads this to know exactly what to edit) ──
    fix_items = []
    for issue in link_issues:
        t = issue.get('type', '')
        if t == 'too_few_blog_links':
            fix_items.append(f'博客内链不足：打开 validate 报告的"博客内链候选"，从中选 {blog_floor - blog_count} 条，自然嵌入正文')
        elif t == 'too_many_blog_links':
            fix_items.append(f'博客内链超标：删除 {blog_count - blog_ceil} 条，保留最相关的')
        elif t == 'too_few_product_links':
            fix_items.append(f'产品内链不足：打开 validate 报告的"产品内链候选"，从中选 {prod_floor - product_count} 条，放在解决方案部分或FAQ前')
        elif t == 'too_many_product_links':
            fix_items.append(f'产品内链超标：删除 {product_count - prod_ceil} 条，保留最相关的')
        elif t == 'too_few_external_links':
            fix_items.append(f'外链不足：打开 validate 报告，从"🔥 素材包外链"或"素材库"中选 {ext_floor - ext_count} 条')
        elif t == 'too_many_external_links':
            fix_items.append(f'外链超标：删除 {ext_count - ext_ceil} 条')
        elif t == 'self_link':
            fix_items.append('删除自链接（文章链向了自己的URL），没有SEO价值')
        elif t == 'product_link_too_early':
            fix_items.append('产品链接出现在前20%区域，移到解决方案部分或FAQ之前')
        elif t == 'links_not_spread':
            fix_items.append('博客内链分布不均，在空白的区域（25-50%/50-75%）补充链接')
    if cannibal_block and '蚕食' not in str(fix_items):
        cslug = ''
        if cannibal.get('top'):
            cslug = cannibal['top'][0].get('existing_slug', '')
        fix_items.insert(0, f'蚕食阻塞(≥{seo_config.CANNIBAL_THRESHOLD_HIGH})：找到与「{cslug or "已发布文章"}」的差异化角度后重写，或换备选主题')
    if fix_items:
        lines.append('## 🔧 怎么修')
        for i, item in enumerate(fix_items, 1):
            lines.append(f'{i}. {item}')
        lines.append('')
        lines.append(f'修完后重跑：`python3 write_collector.py post-process --website {website} --draft "[draft路径]"`')
        lines.append('')

    # ── Apply: write back to draft file if requested ──
    if apply and gate_passed:
        # Keep existing frontmatter, only update link fields and word count
        existing_fm = ''
        if original.startswith('---'):
            parts = original.split('---', 2)
            if len(parts) >= 3:
                existing_fm = parts[1]

        # Rebuild frontmatter: keep existing fields, add link fields
        fm_lines = existing_fm.rstrip().split('\n') if existing_fm else []
        # Remove existing link fields AND their indented list items
        cleaned = []
        skip_indented = False
        for l in fm_lines:
            if l.strip().startswith(('内链:', '外链:', '内链产品:', '字数:')):
                skip_indented = True
                continue
            if skip_indented and l.strip().startswith('-'):
                continue
            skip_indented = False
            cleaned.append(l)
        fm_lines = cleaned
        # Add new link fields
        fm_lines.append('内链:')
        for url in [l['url'] for l in blog_links]:
            fm_lines.append(f' - {url}')
        if product_links:
            fm_lines.append('内链产品:')
            for url in [l['url'] for l in product_links]:
                fm_lines.append(f' - {url}')
        fm_lines.append('外链:')
        for url in [l['url'] for l in external_links]:
            fm_lines.append(f' - {url}')
        fm_lines.append(f'字数: ~{len(re.findall(r"\b\w+\b", body))}')

        final = f'---\n' + '\n'.join(fm_lines) + f'\n---\n\n{body}'
        draft_file.write_text(final, encoding='utf-8')
        lines.append('---')
        lines.append(f'✅ Frontmatter 已更新 (仅链接字段): {draft_file.name}')
        lines.append('')
    elif apply:
        lines.append('⛔ 门控未通过，未修改 draft。')
        lines.append('')

    if force and cannibal_block:
        lines.append('')
        lines.append('⚡ --force：已跳过蚕食门控（人工确认差异化足够）')
    analysis_file.unlink(missing_ok=True)
    return '\n'.join(lines), gate_passed


def _generate_frontmatter(meta: Dict, body: str,
                           blog_links: List, product_links: List,
                           external_links: List, domain: str) -> str:
    """Generate frontmatter from body content and link analysis."""

    # Count words (approximate)
    word_count = len(re.findall(r'\b\w+\b', body))

    # Collect unique URLs
    内链 = [l['url'] for l in blog_links]
    内链产品 = [l['url'] for l in product_links]
    外链 = [l['url'] for l in external_links]

    lines = []
    for key in ['Title', 'Slug', 'Summary', 'Tags', 'SEO Title', 'SEO Description',
                'SEO Keywords']:
        val = meta.get(key, '')
        if isinstance(val, list):
            val = ', '.join(val)
        if val:
            lines.append(f'{key}: {val}')

    lines.append('内链:')
    for url in 内链:
        lines.append(f' - {url}')

    if 内链产品:
        lines.append('内链产品:')
        for url in 内链产品:
            lines.append(f' - {url}')

    lines.append('外链:')
    for url in 外链:
        lines.append(f' - {url}')

    lines.append(f'字数: ~{word_count}')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
# Stage 2: Register + Retroactive Linking
# ═══════════════════════════════════════════════════════════

def register(website: str, draft_path: str, pack_path: str,
             new_url: str, article_title: str, primary_keyword: str) -> str:
    website_dir = SITES_DIR / website
    draft_file = Path(draft_path)
    if not draft_file.exists():
        draft_file = website_dir / 'drafts' / draft_path

    if not draft_file.exists():
        print(f'Error: draft file not found: {draft_path}', file=sys.stderr)
        sys.exit(1)

    ilm = website_dir / 'context' / 'internal-links-map.md'
    if not ilm.exists():
        print(f'Error: internal-links-map.md not found', file=sys.stderr)
        sys.exit(1)

    ilm_content = ilm.read_text(encoding='utf-8')
    draft_content = draft_file.read_text(encoding='utf-8')

    # ── Step A: Register new article (skip if already registered) ──
    print('[1/3] registering in internal-links-map...', file=sys.stderr)
    if new_url in ilm_content:
        print(f'       ⚠️ URL 已注册，跳过 ILM 登记', file=sys.stderr)
        next_num = 0
    else:
        # Find the last article number
        existing_nums = re.findall(r'### #(\d+) —', ilm_content)
        next_num = max(int(n) for n in existing_nums) + 1 if existing_nums else 1

        # Find the "已发布文章" table and append new row
        lines_list = ilm_content.split('\n')
        in_article_table = False
        table_header_found = False
        last_data_row_idx = -1
        table_separator_idx = -1

        for i, line in enumerate(lines_list):
            if '## 已发布文章' in line:
                in_article_table = True
            if in_article_table and line.startswith('|') and '---' in line:
                table_separator_idx = i
                table_header_found = True
            if table_header_found and line.startswith('|') and i > table_separator_idx:
                last_data_row_idx = i
            if in_article_table and line.startswith('## ') and '文章元数据' in line:
                break

        if last_data_row_idx >= 0 and table_separator_idx >= 0:
            new_row = f'| {next_num} | {article_title} | {new_url} | {primary_keyword} |'
            lines_list.insert(last_data_row_idx + 1, new_row)

        # Append metadata block at end of 文章元数据 section
        meta_start = -1
        for i, line in enumerate(lines_list):
            if '## 文章元数据' in line:
                meta_start = i
                break

        new_meta_block = [
            '',
            f'### #{next_num} — {_slugify(article_title)}',
            f'- URL: {new_url}',
            f'- 话题标签: {primary_keyword}',
            '- 内链文章: []',
            '- 内链产品: []',
            '- 状态: draft',
        ]

        if meta_start >= 0:
            lines_list.append('')
            lines_list.extend(new_meta_block)

        ilm.write_text('\n'.join(lines_list) + '\n', encoding='utf-8')
        print(f'       ✅ 已登记: #{next_num}', file=sys.stderr)

    # ── Step B: Generate retroactive linking CANDIDATES (AI picks) ──
    print('[2/3] generating retroactive link candidates...', file=sys.stderr)

    # Read link_counts from published-index.json
    pi_path = website_dir / 'published' / 'published-index.json'
    link_counts_map = {}
    if pi_path.exists():
        try:
            pi_data = json.loads(pi_path.read_text(encoding='utf-8'))
            for entry in (pi_data if isinstance(pi_data, list) else []):
                lc = entry.get('link_counts', {})
                link_counts_map[entry.get('slug', '')] = {
                    'blog': lc.get('blog', 0),
                    'product': lc.get('product', 0),
                    'tags': entry.get('tags', []),
                    '#': entry.get('#', 0),
                    'url': f"https://{website}.com/blog/{entry.get('slug', '')}",
                }
        except Exception:
            pass

    # If published-index.json has no data, fallback to internal-links-map metadata
    if not link_counts_map:
        for m in re.finditer(r'### #(\d+) — (\S+)\n- URL: (https?://\S+)\n- 话题标签: ([^\n]+)\n- 内链文章: ([^\n]*)\n- 状态: ([^\n]+)',
                              ilm_content, re.MULTILINE):
            num = int(m.group(1))
            slug = m.group(2)
            if slug == _slugify(article_title) or new_url in m.group(3):
                continue
            tags = [t.strip().lower() for t in m.group(4).split(',')]
            inner = len(re.findall(r'#\d+', m.group(5).strip()))
            link_counts_map[slug] = {
                'blog': inner, 'product': 0,
                'tags': tags, '#': num, 'url': m.group(3),
            }

    # Build candidates
    kw_words = set(primary_keyword.lower().replace('$', '').replace('-', ' ').split())
    kw_words = {w for w in kw_words if len(w) > 2}

    all_candidates = []
    published_dir = website_dir / 'published'

    for slug, info in link_counts_map.items():
        tags = info.get('tags', [])
        overlap = sum(1 for t in tags if any(kw in t for kw in kw_words))
        if overlap < 2:
            continue

        old_file = published_dir / f'{slug}.md'
        old_body = old_file.read_text(encoding='utf-8') if old_file.exists() else ''

        # W11: saturated check — dynamic by word count (~1 per LINK_RATIO_BLOG words, same as blog floor)
        old_wc = len(re.findall(r'\b\w+\b', old_body))
        old_blog_ceil = max(2, min(8, round(old_wc / seo_config.LINK_RATIO_BLOG))) + 2  # blog ceiling
        live_blog_count = len(set(re.findall(r'\]\((https?://[^)]+/blog/[^)]+)\)', old_body)))
        if live_blog_count >= old_blog_ceil:
            continue

        already_linked = new_url in old_body if old_body else False
        h2s = re.findall(r'^## (.+)', old_body, re.MULTILINE)[:3] if old_body else []
        # Contextual anchor per old article (old topic → new angle)
        old_kw = (info.get('tags', [''])[0] if info.get('tags') else slug.replace('-', ' '))[:25]
        new_angle = primary_keyword.split(' ')[0:3] if ' ' in primary_keyword else primary_keyword[:15]
        new_angle_str = ' '.join(new_angle) if isinstance(new_angle, list) else new_angle
        anchor_suggestion = f'{old_kw} — see our {new_angle_str}'

        all_candidates.append({
            'num': info['#'],
            'slug': slug,
            'url': info['url'],
            'tags': tags,
            'overlap': overlap,
            'blog_count': live_blog_count,
            'already_linked': already_linked,
            'h2s': h2s,
            'anchor': anchor_suggestion,
        })

    # Sort and take top 2 (no priority tiers needed)
    all_candidates.sort(key=lambda c: (-c['overlap'], c['blog_count']))
    top_candidates = all_candidates[:2]

    # ── Format candidates for AI selection ──
    lines = []
    lines.append('')
    lines.append('## 回溯链接候选（AI 请读取旧文章后写清单）')
    lines.append(f'> 新文章: [{article_title}]({new_url})')
    lines.append('')
    lines.append('以下旧文章需要加回溯链接（最多 2 篇）：')
    lines.append('')

    for c in top_candidates:
        lines.append(f'**#{c["num"]} — {c["slug"].replace("-", " ").title()}**')
        lines.append(f'- URL: {c["url"]}')
        lines.append(f'- 当前内链: {c["blog_count"]} 条')
        if c['already_linked']:
            lines.append(f'- ⚠️ 已链接此文章，跳过')
        if c['h2s']:
            lines.append(f'- 可选插入位置: {", ".join(c["h2s"])}')
        lines.append(f'- 建议锚文本: `[{c["anchor"]}]({new_url})`')
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('**AI 任务**：读取上面每篇文章的正文 → 写自然锚文本 → 输出回溯链接清单（不要直接编辑旧文章）。')

    candidates_section = '\n'.join(lines)

    # Append candidates to draft for AI review
    if '## 回溯链接候选' not in draft_content:
        draft_content += '\n' + candidates_section
        draft_file.write_text(draft_content, encoding='utf-8')

    top_count = len(top_candidates)
    print(f'       🔗 回溯候选: {top_count} 篇', file=sys.stderr)

    # ── Step C: Archive search materials to libraries, then clean up material pack
    archive_note = ''
    print('[3/3] archiving materials + cleaning up material pack...', file=sys.stderr)
    if pack_path:
        p = Path(pack_path)
        if not p.exists():
            p = website_dir / 'material-packs' / pack_path
        if p.exists():
            try:
                report, has_issues = research_collector.archive_materials(website, str(p))
                print(report, file=sys.stderr)
                archive_note = '\n- 素材已归档到素材库'
                if has_issues:
                    print('⚠️ 归档有格式问题，请检查素材包格式', file=sys.stderr)
            except Exception as e:
                print(f'⚠️ 归档失败: {e}，继续清理', file=sys.stderr)
            p.unlink()
            print(f'       ✅ 已清理: {p.name}', file=sys.stderr)

    # ── Phase F: close the loop — record acceptance back to PLAN feedback ──
    fb_note = ''
    try:
        meta_fm, _ = _parse_frontmatter(draft_content)
        ctx_slug = meta_fm.get('Slug') or _slugify(article_title)
        ctx = seo_common.read_topic_context(website_dir, ctx_slug)
        score = ctx.get('plan_score') if ctx else None
        cmd = ['python3', str(Path(__file__).resolve().parent / 'plan_feedback.py'),
               '--website', website, 'accept', '--slug', ctx_slug]
        if score is not None:
            cmd += ['--score', str(score)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR))
        if r.returncode == 0:
            fb_note = f'\n- 已回流 PLAN 反馈: accept {ctx_slug}'
            print(f'       ✅ 回流反馈: accept {ctx_slug}', file=sys.stderr)
    except Exception as e:
        print(f'       ⚠️ 反馈回流失败: {e}', file=sys.stderr)

    # ── Summary ──
    report = f"""✅ 注册完成

- 已登记到: internal-links-map.md (文章 #{next_num})
- 回溯链接: {top_count} 篇候选
- 素材包已清理{archive_note}{fb_note}"""
    if pack_path and Path(pack_path).exists():
        report += f"\n- 素材包已清理: {pack_path}"

    return report


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _compute_link_candidates(website_dir, topic, self_slug, report):
    """Pre-compute internal/external link candidates matched to topic context."""
    ilm_path = website_dir / 'context' / 'internal-links-map.md'
    topic_tags = set(seo_common.match_tokens(topic))
    for t in re.split(r'[\s\-_]', topic.lower()):
        if len(t) > 2:
            topic_tags.add(t)

    # ── Blog candidates: title/keyword overlap + published tag overlap ──
    # Load published tags for richer matching
    pub_tags = {}
    pi_path = website_dir / 'published' / 'published-index.json'
    if pi_path.exists():
        try:
            pi = json.loads(pi_path.read_text(encoding='utf-8'))
            for entry in (pi if isinstance(pi, list) else []):
                pub_tags[entry.get('slug', '')] = entry.get('tags', [])
        except Exception:
            pass

    if ilm_path.exists():
        ilm = ilm_path.read_text(encoding='utf-8')
        cands = re.findall(
            r'\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(https?://[^|]+?/blog/[^|]+?)\s*\|\s*([^|\n]+)', ilm)
        for num, title, url, kw in cands[:30]:
            title_kw_tokens = seo_common.match_tokens(title + ' ' + kw)
            slug = url.rstrip('/').split('/')[-1] if url else ''
            if slug == self_slug:
                continue  # skip self-link candidate
            article_tags = pub_tags.get(slug, [])
            tag_tokens = set()
            for t in article_tags:
                tag_tokens |= seo_common.match_tokens(t)
            score = len(topic_tags & title_kw_tokens) + len(topic_tags & tag_tokens) * 1.5
            if score >= 1:
                report['link_candidates_blog'].append({
                    '#': num, 'title': title.strip(), 'url': url.strip(),
                    'keyword': kw.strip(), 'tags': article_tags[:4],
                    'score': round(score, 1),
                })
    report['link_candidates_blog'].sort(key=lambda x: -x['score'])
    report['link_candidates_blog'] = report['link_candidates_blog'][:8]

    # ── Product links: from ILM product URLs ──
    for match in re.findall(r'(https?://[^/]+/product(?:s)?/[^)\s]+)',
                            (ilm_path.read_text(encoding='utf-8') if ilm_path.exists() else '')):
        if len(report['link_candidates_product']) < 5:
            report['link_candidates_product'].append({'url': match.strip()})

    # ── External sources: match by 适用文章/标签 ──
    ext_lib = website_dir / 'context' / 'external-sources-library.md'
    if ext_lib.exists():
        ext_content = ext_lib.read_text(encoding='utf-8')
        entries = re.split(r'\n(?=##\s)', ext_content)
        for entry in entries:
            if not entry.strip().startswith('##') or '来源' not in entry:
                continue
            header = entry.split('\n')[0].replace('##', '').strip()
            url_m = re.search(r'\*\*来源 URL\*\*:\s*(\S+)', entry)
            tags_m = re.search(r'\*\*(?:适用文章|标签)\*\*:\s*([^\n]+)', entry)
            url = url_m.group(1) if url_m else ''
            tag_text = tags_m.group(1).replace('`', '').strip() if tags_m else ''
            entry_tags = set()
            for t in re.split(r'[,]\s*', tag_text):
                entry_tags |= seo_common.match_tokens(t)
            score = len(topic_tags & entry_tags)
            if score >= 1:
                report['link_candidates_external'].append({
                    'title': header[:60], 'url': url, 'score': score,
                })
        report['link_candidates_external'].sort(key=lambda x: -x['score'])
        report['link_candidates_external'] = report['link_candidates_external'][:8]


def main():
    parser = argparse.ArgumentParser(description='WRITE data collector — three-stage pipeline')
    subparsers = parser.add_subparsers(dest='command', help='Stage to run')

    p_val = subparsers.add_parser('validate', help='Validate material pack + load context')
    p_val.add_argument('--website', required=True)
    p_val.add_argument('--topic', required=True)
    p_val.add_argument('--pack', help='Material pack path (auto-detected if not set)')

    p_post = subparsers.add_parser('post-process', help='Scrub, validate links, score')
    p_post.add_argument('--website', required=True)
    p_post.add_argument('--draft', required=True, help='Path to draft file')
    p_post.add_argument('--pack', help='Material pack path (for external link validation)')
    p_post.add_argument('--apply', action='store_true', help='Write generated frontmatter back to draft file')
    p_post.add_argument('--force', action='store_true', help='Skip cannibal gate (only if you have confirmed angle differentiation)')

    p_reg = subparsers.add_parser('register', help='Register + retroactive links + cleanup')
    p_reg.add_argument('--website', required=True)
    p_reg.add_argument('--draft', required=True, help='Path to final draft')
    p_reg.add_argument('--pack', required=True, help='Material pack file to clean')
    p_reg.add_argument('--new-url', required=True, help='New article full URL')
    p_reg.add_argument('--title', required=True, help='Article title')
    p_reg.add_argument('--keyword', required=True, help='Primary keyword')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'validate':
        print(validate(args.website, args.topic, args.pack))
    elif args.command == 'post-process':
        report, ok = post_process(args.website, args.draft, args.pack, args.apply, args.force)
        print(report)
        if not ok:
            print('\n🚫 门控未通过，不可进入段3 register。修完重跑本命令。', file=sys.stderr)
            sys.exit(1)
    elif args.command == 'register':
        print(register(args.website, args.draft, args.pack,
                       args.new_url, args.title, args.keyword))


if __name__ == '__main__':
    main()
