#!/usr/bin/env python3
"""WRITE pre-check — mechanical checklist that AI tends to skip.

Runs AFTER the AI writes the draft, BEFORE 段2 post-process.
Catches mechanical violations so the AI can fix them before submitting
to the TF-IDF gate + scorer. AI no longer needs to self-check 25 items
in one bloated context window.

Checks:
  1. Word count ≥ tier minimum
  2. Primary keyword in H1 / first 100 words / 2+ H2s
  3. Meta Title 50-60 chars / Description 150-160 / Keywords present
  4. Key Takeaways 3-5 bullet items
  5. Quick Specs presence (commercial-type only)
  6. Contextual CTAs: 2-3 total (first 40% no hard product push)
  7. No "click here" / "read more" anchor text
  8. FAQ: 3+ questions + FAQPage schema present
  9. Paragraph cap: no paragraph > 4 sentences
  10. E-E-A-T signals: 2+ experience patterns present
  11. H2/H3 hierarchy: no level jumping (H2→H4)
  12. 字数 vs tier 下限 硬门控（三档 ✅/⚠️/❌，close-to-min 报黄）
  13. 关键实体覆盖率（cluster Must-mention LSI，target-keywords.md）

Usage:
  python3 write_pre_check.py --draft path/to/draft.md
  python3 write_pre_check.py --draft draft.md --tier "Pillar Page"
  python3 write_pre_check.py --draft draft.md --keywords "handheld printer, buy guide" --tier "Cluster Content"
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from seo_common import classify_value
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_common', Path(__file__).resolve().parent / 'seo_common.py')
    seo_common = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_common)
    classify_value = seo_common.classify_value

try:
    from seo_config import TIER_MIN_WORDS
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    _sc_cfg = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_sc_cfg)
    TIER_MIN_WORDS = _sc_cfg.TIER_MIN_WORDS

EEAT_PATTERNS = [
    r'\bin our (?:hands-on )?(?:test|testing|evaluation|review|experience|trial|lab)\b',
    r'\bwe (?:tested|measured|found|discovered|observed|noticed|ran|conducted|compared|'
    r'evaluated|benchmarked|examined|verified|confirmed|tried|put|were)\b',
    r'\bduring our\b', r'\bafter \d+ (?:days?|weeks?|months?) of\b',
    r'\bin the lab\b', r'\bour (?:bench|side-by-side|head-to-head)\b',
]

CTA_INDICATORS = r'(?i)(?:browse|shop|buy now|get started|learn more|see our|check out|'
CTA_INDICATORS += r'explore|try|contact us|request|get yours|view|find your)'


def parse_draft(path: str) -> Tuple[Dict, str, str, str, str]:
    """Return (meta, body, clean, prose, raw).

    body / clean  — as before (frontmatter stripped, links/html tags flattened).
    prose         — like clean but additionally strips fenced code blocks and
                   JSON-LD <script> blocks; used for tier word-count gate and
                   entity coverage so JSON schema tokens don't inflate counts.
    raw           — original file content (includes frontmatter).
    """
    content = Path(path).read_text(encoding='utf-8')
    meta = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split('\n'):
                m = re.match(r'^([^:]+?)\s*:\s*(.+)', line.strip())
                if m:
                    meta[m.group(1).strip().lower()] = m.group(2).strip()
            body = parts[2]
    # Clean markdown for checks
    clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', body)  # links → text
    clean = re.sub(r'<\/?[a-zA-Z][^>]*>', '', clean)            # html tags (letter-started only, not <number)
    # prose: clean minus fenced code blocks minus JSON-LD script bodies
    prose = re.sub(r'```[\s\S]*?```', '', clean)
    prose = re.sub(r'<script[^>]*type="application/ld\+json"[^>]*>[\s\S]*?</script>',
                   '', prose, flags=re.IGNORECASE)
    return meta, body, clean, prose, content


def word_count(text: str) -> int:
    return len(re.findall(r'[a-z0-9]+', text.lower()))


def sentence_count(text: str) -> int:
    return len(re.findall(r'[.!?]+(?:\s+|$)', text))


def words_before_n(text: str, n: int) -> str:
    return ' '.join(re.findall(r'\b[a-z0-9]+\b', text.lower())[:n])


# ── cluster entity coverage helpers (check 13) ─────────────────────────

def _entity_variants(entity: str) -> List[str]:
    """Expand an LSI entity string into searchable variants.

    "NOHD (Nominal Ocular Hazard Distance)"  →  ["NOHD"]
    "Nichia NDB4916 / NUGM01 diode"          →  ["Nichia NDB4916", "NUGM01 diode"]
    "21 CFR 1040.10"                          →  ["21 CFR 1040.10", "21 CFR 1040"]
    """
    ent = re.sub(r'\s*\([^)]*\)\s*', ' ', entity).strip()
    out = []
    for v in ent.split('/'):
        v = v.strip()
        if not v:
            continue
        out.append(v)
        m = re.match(r'^(.+?\b\w+)(\.\d+)\b', v)
        if m:
            out.append(m.group(1))
    return out


def _entity_in_text(entity: str, text_lower: str) -> bool:
    """True if any variant of `entity` appears in `text_lower`.

    Short alnum-only acronyms (DPSS, NOHD, OD, IR) use word-boundary matching
    to avoid noisy false positives inside other words. Multi-token strings
    (with a space) use straight substring matching so "21 cfr 1040" matches
    "21 cfr 1040.10" written either way.
    """
    for v in _entity_variants(entity):
        v_n = v.lower().strip()
        if not v_n:
            continue
        if ' ' not in v_n and len(v_n) <= 5 and v_n.isalnum():
            if re.search(r'\b' + re.escape(v_n) + r'\b', text_lower):
                return True
        else:
            if v_n in text_lower:
                return True
    return False


def _find_cluster_entities(keywords: List[str], project_root: Path
                           ) -> Tuple[str, List[str]]:
    """Match the given keywords against target-keywords.md clusters.

    Returns (cluster_header, entity_list). For each cluster builds a term
    pool from Primary + Secondary + first tokens of each Must-mention entity,
    then scores +1 per keyword that substring-matches any term (in either
    direction). Highest-scoring cluster wins; ties resolve to first-seen
    (which prefers more specific clusters).
    """
    target = project_root / 'laserpointerhub' / 'context' / 'target-keywords.md'
    if not target.exists() or not keywords:
        return '', []
    content = target.read_text(encoding='utf-8')
    parts = re.split(r'\n(?=## Topic Cluster\s+\d+[:：])', content)
    best_body = best_header = ''
    best_score = 0
    for part in parts:
        if not part.startswith('## Topic Cluster'):
            continue
        hdr_m = re.match(r'^## Topic Cluster\s+\d+[:：]\s*(.+)$', part, re.MULTILINE)
        primary_m = re.search(r'\*\*Primary:\*\*\s*(.+?)$', part, re.MULTILINE)
        secondary_m = re.search(r'\*\*Secondary:\*\*\s*(.+?)$', part, re.MULTILINE)
        entities_m = re.search(r'\*\*Must-mention Entities\s*/\s*LSI:\*\*\s*(.+?)$',
                               part, re.MULTILINE)
        terms = []
        if primary_m:
            terms.append(primary_m.group(1).strip().lower())
        if secondary_m:
            terms += [t.strip().lower()
                      for t in secondary_m.group(1).split(',') if t.strip()]
        if entities_m:
            for ent in entities_m.group(1).split(','):
                head = ent.split('(')[0].strip()
                if not head:
                    continue
                head_tokens = head.split('/')[:2]
                for frag in head_tokens:
                    frag = frag.strip().lower()
                    if frag and len(frag) < 40 and not frag.startswith('按'):
                        terms.append(frag)
        terms = list(dict.fromkeys(t for t in terms if t))
        score = 0
        for kw in keywords:
            kw_l = kw.lower().strip()
            if not kw_l:
                continue
            for term in terms:
                if kw_l in term or term in kw_l:
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_body = part
            best_header = hdr_m.group(1).strip() if hdr_m else ''
    if not best_body or best_score == 0:
        return '', []
    em = re.search(r'\*\*Must-mention Entities\s*/\s*LSI:\*\*\s*(.+?)$',
                   best_body, re.MULTILINE)
    if not em:
        return best_header, []
    raw = em.group(1).strip()
    return best_header, [e.strip() for e in raw.split(',') if e.strip()]


def run(draft: str, tier: str = '', keywords: str = '') -> Dict:
    meta, body, clean, prose, raw = parse_draft(draft)
    results = []
    tier = tier or meta.get('page type', meta.get('tier', ''))
    if not tier:
        tier = 'Pillar Page' if len(meta.get('title', '').split()) < 4 else 'Cluster Content'
    kws = [k.strip().lower() for k in (keywords or meta.get('seo keywords', '')).split(',') if k.strip()]
    primary = kws[0] if kws else meta.get('title', '').lower()
    wc = word_count(clean)
    wc_prose = word_count(prose)
    sc = sentence_count(clean)
    h1 = re.search(r'^# (.+)', body, re.MULTILINE)
    h2s = re.findall(r'^## (.+)', body, re.MULTILINE)
    h3s = re.findall(r'^### (.+)', body, re.MULTILINE)
    h4s = re.findall(r'^#### (.+)', body, re.MULTILINE)
    paragraphs = [p.strip() for p in re.split(r'\n\n+', clean) if len(p.split()) > 20]
    first_100 = words_before_n(clean, 100)

    def ok(msg, cond, detail=''):
        results.append({'item': msg, 'level': 'ok' if cond else 'fail',
                        'pass': bool(cond), 'detail': str(detail)})

    def grade(level, msg, detail=''):
        # level: 'ok' | 'warn' | 'fail'   (warn counts as pass for exit code)
        results.append({'item': msg, 'level': level,
                        'pass': level != 'fail', 'detail': str(detail)})

    # 1. Word count
    min_w = TIER_MIN_WORDS.get(tier, 1200)
    ok(f'字数 ≥{min_w} ({tier})', wc >= min_w, f'当前 {wc}')

    # 2. H1 has primary keyword (check both body H1 and frontmatter Title)
    raw_h1 = h1.group(1).strip() if h1 else ''
    fm_title = meta.get('title', '')
    h1_ok = primary and (primary in raw_h1.lower() or primary in fm_title.lower())
    ok(f'H1/Title 含主关键词 "{primary}"', h1_ok,
       f'H1={raw_h1[:40]} Title={fm_title[:40]}')

    # 3. Primary keyword in first 100 words
    ok(f'主关键词在前100词', primary and primary in first_100, first_100[:80])

    # 4. Primary keyword in 2+ H2s
    h2_hits = sum(1 for h in h2s if primary and primary in h.lower())
    ok(f'H2 含主关键词 ≥2次', h2_hits >= 2, f'{h2_hits} hits in {len(h2s)} H2s')

    # 5. Meta Title 50-60
    mt = meta.get('seo title', meta.get('title', ''))
    ok('SEO Title 50-60字符', 50 <= len(mt) <= 60, f'{len(mt)}字符: {mt[:60]}')

    # 6. Meta Description 150-160
    md = meta.get('seo description', meta.get('summary', ''))
    ok('SEO Description 150-160字符', 150 <= len(md) <= 160, f'{len(md)}字符')

    # 7. Key Takeaways 3-5
    kts = re.findall(r'^> \*\*Key Takeaways\*\*.*?\n((?:> .+\n?)+)', body, re.MULTILINE)
    kt_count = len(re.findall(r'^> -', kts[0], re.MULTILINE)) if kts else 0
    ok('Key Takeaways 3-5条', 3 <= kt_count <= 5, f'{kt_count}条')

    # 8. Quick Specs (commercial only)
    vlabel = classify_value(meta.get('title', ''))[0] if meta.get('title') else ''
    is_commercial = vlabel == '转化型' or 'buying' in (meta.get('title', '') + ' '.join(kws)).lower()
    has_specs = bool(re.search(r'Quick Specs', body))
    if is_commercial:
        ok('Quick Specs 块（商业型文章）', has_specs, '缺失' if not has_specs else '✅')

    # 9. CTA count 2-3, first soft CTA OK in first 500 words only if purchase context.
    #    Hard product push in first 40% is forbidden (SKILL rule). Pre-check only
    #    counts total CTAs; the 40% rule is enforced by content_scorer.
    ctas = [m for m in re.finditer(CTA_INDICATORS, clean)]
    ok('CTA ≥2个', len(ctas) >= 2, f'{len(ctas)}个')

    # 10. No "click here"/"read more" anchor
    bad_anchors = re.findall(r'\[(click here|read more|here|click|more)\s*\]', raw, re.IGNORECASE)
    ok('无 "click here"/"read more" 锚文本', len(bad_anchors) == 0,
       f'{len(bad_anchors)}个: {bad_anchors}' if bad_anchors else '✅')

    # 11. FAQ 3+ questions (look for FAQ heading then count subsequent H3s or numbered items)
    faq_count = 0
    in_faq = False
    for line in body.split('\n'):
        if re.match(r'^##\s*(?:FAQ|Frequently Asked)', line, re.I):
            in_faq = True
            continue
        if in_faq and re.match(r'^## ', line):
            break
        if in_faq and re.match(r'^(?:### |\*\*|\d+\.\s|-\s)', line):
            faq_count += 1
    ok('FAQ ≥3个问题', faq_count >= 3, f'{faq_count}个')

    # 12. FAQ Schema present
    has_schema = '<script type="application/ld+json">' in raw and 'FAQPage' in raw
    ok('FAQ Schema 存在', has_schema, '✅' if has_schema else '缺失')

    # 13. Paragraph length (≤4 sentences)
    long_paras = []
    for i, p in enumerate(paragraphs):
        sc_in_p = len(re.findall(r'[.!?]+(?:\s+|$)', p))
        if sc_in_p > 4:
            long_paras.append(i)
    ok(f'段落≤4句 (共{len(paragraphs)}段)', len(long_paras) == 0,
       f'{len(long_paras)}段超标: #{long_paras[:5]}' if long_paras else '✅')

    # 14. E-E-A-T signals (warn only — material pack may not have test data)
    eeat_count = sum(len(re.findall(p, clean, re.I)) for p in EEAT_PATTERNS)
    ok('EEAT 信号 ≥2处', True, f'{eeat_count}处{" ⚠️ 偏低" if eeat_count < 2 else ""}')  # always green, advisor

    # 15. H2/H3 hierarchy — no jumps (H2→H4)
    headings = re.findall(r'^(#{2,4}) ', body, re.MULTILINE)
    jumps = 0
    prev = 2
    for h in headings:
        level = len(h)
        if level > prev + 1:
            jumps += 1
        prev = level
    ok('H2/H3层级无跳跃', jumps == 0, f'{jumps}处跳跃' if jumps else '✅')

    # 16. 字数 vs tier 下限 硬门控（三档：✅ ≥115% / ⚠️ 接近下限 / ❌ 不足）
    #     prose 字数排除 frontmatter、HTML/JSON-LD 代码块、Markdown 标记。
    min_w_gate = TIER_MIN_WORDS.get(tier, 1200)
    close_w = int(min_w_gate * 1.15)
    if wc_prose >= close_w:
        grade('ok', f'字数 vs tier 下限 硬门控 ({tier})',
              f'{wc_prose}词 ≥ {close_w} (115%×{min_w_gate})')
    elif wc_prose >= min_w_gate:
        grade('warn', f'字数 vs tier 下限 硬门控 ({tier})',
              f'{wc_prose}词 接近下限 {min_w_gate} (<115%={close_w}), 建议扩写')
    else:
        grade('fail', f'字数 vs tier 下限 硬门控 ({tier})',
              f'{wc_prose}词 < 下限 {min_w_gate}, 不足')

    # 17. 关键实体覆盖率（cluster Must-mention LSI）
    #     从 --keywords 或 frontmatter Tags 推断所属集群，对照
    #     laserpointerhub/context/target-keywords.md 的 Must-mention Entities。
    kw_for_cluster = kws
    if not kw_for_cluster:
        tag_str = meta.get('tags', '')
        kw_for_cluster = [t.strip().lower() for t in tag_str.split(',') if t.strip()]
    cluster_header, entities = _find_cluster_entities(
        kw_for_cluster, Path(draft).resolve().parents[2])
    if not entities:
        grade('warn', '关键实体覆盖率',
              '未找到集群实体配置，跳过 (cluster=' + (cluster_header or 'N/A') + ')')
    else:
        prose_lower = prose.lower()
        matched = [e for e in entities if _entity_in_text(e, prose_lower)]
        missing = [e for e in entities if e not in matched]
        cov = len(matched) / len(entities) if entities else 0.0
        if cov >= 0.80:
            cls = 'ok'
        elif cov >= 0.30:
            cls = 'warn'
        else:
            cls = 'fail'
        miss_show = ', '.join(missing[:6]) + (f' (+{len(missing)-6})' if len(missing) > 6 else '')
        grade(cls, f'关键实体覆盖率 ≥80% (cluster: {cluster_header})',
              f'{cov*100:.0f}% ({len(matched)}/{len(entities)}); 缺: {miss_show}')

    return {'draft': draft, 'tier': tier, 'word_count': wc, 'checks': results,
            'pass_count': sum(1 for r in results if r['pass']),
            'warn_count': sum(1 for r in results if r.get('level') == 'warn'),
            'fail_count': sum(1 for r in results if not r['pass'])}


def _fix_hint(item: str) -> str:
    hints = {
        '字数': '扩写到目标字数（补正文内容，不要灌水）',
        '字数 vs tier 下限': '补正文正文字数使达到 tier 下限（Pillar 2500 / Cluster 1200 / Roundup 2000），115% 以上为优',
        '主关键词': '在对应位置（H1/前100词/H2s）自然插入主关键词',
        'SEO Title': '调整 SEO Title frontmatter 为 50-60 字符',
        'SEO Description': '调整 SEO Description frontmatter 为 150-160 字符',
        'Key Takeaways': '在 Introduction 后添加 > Key Takeaways 块，3-5 条',
        'Quick Specs': '在 Introduction 后添加 > Quick Specs 块',
        'CTA': '在正文中自然嵌入 CTA 段落（browse/learn more/get started 等）',
        '锚文本': '替换为描述性锚文本（不用 click here/read more）',
        'FAQ': '补充 FAQ 至 3+ 个问题，每个含自然语言问句+2-3句答案',
        'Schema': '在文末添加 FAQPage JSON-LD schema 块',
        '段落': '将超标段落拆分为 2-4 句的短段落',
        'EEAT': '若素材包 C/E 有真实测试/场景数据，引用到正文中（非阻塞警告）',
        '层级': '修复标题层级跳跃（H2→H3 递进，不可越级）',
        '关键实体覆盖率': '对照 laserpointerhub/context/target-keywords.md 对应集群 Must-mention Entities，在正文补充缺失的 LSI 实体',
    }
    for k, v in hints.items():
        if k in item:
            return v
    return '编辑 draft 修复此项'


def render(report: Dict) -> str:
    L = []
    L.append(f"# WRITE 预检报告 — {Path(report['draft']).name}")
    L.append(f"> 层级: {report['tier']} | 字数: {report['word_count']} | "
             f"通过: {report['pass_count']}/{len(report['checks'])}"
             f" | 警告: {report.get('warn_count', 0)}")
    L.append('')
    L.append('| 检查项 | 结果 | 详情 |')
    L.append('|--------|------|------|')
    for r in report['checks']:
        lvl = r.get('level', 'ok' if r['pass'] else 'fail')
        icon = {'ok': '✅', 'warn': '⚠️', 'fail': '❌'}[lvl]
        L.append(f"| {r['item']} | {icon} | {r['detail'][:80]} |")
    L.append('')
    fails = [r for r in report['checks'] if not r['pass']]
    warns = [r for r in report['checks'] if r.get('level') == 'warn']
    if fails:
        L.append(f"## 需修复 ({len(fails)}项)")
        for r in fails:
            hint = _fix_hint(r['item'])
            L.append(f"- ❌ **{r['item']}**: {r['detail']}.  *{hint}*")
    if warns:
        L.append(f"## 警告 ({len(warns)}项, 非阻塞)")
        for r in warns:
            L.append(f"- ⚠️ **{r['item']}**: {r['detail']}")
    if not fails and not warns:
        L.append('## ✅ 全部通过，可进入段2 post-process')
    elif not fails:
        L.append('## ✅ 硬门控全部通过，可进入段2 post-process（含警告项建议修复）')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='WRITE pre-check — mechanical checklist')
    ap.add_argument('--draft', required=True, help='Path to draft .md file')
    ap.add_argument('--tier', help='Page tier (Pillar Page / Cluster Content / Product Roundup)')
    ap.add_argument('--keywords', help='Comma-separated primary keywords')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    report = run(args.draft, args.tier or '', args.keywords or '')
    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    sys.exit(0 if report['fail_count'] == 0 else 1)


if __name__ == '__main__':
    main()
