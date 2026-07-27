#!/usr/bin/env python3
"""RESEARCH scorer — deterministic opportunity score for a chosen topic.

Replaces the AI-subjective 6-factor scoring in research/SKILL.md (where the weight
table was decorative and nothing computed it). Reads the JSON sidecar emitted by
research_collector (`research-data-{slug}-{date}.json`) and outputs a reproducible
0–1 opportunity score with a transparent factor breakdown.

Factors (mirrors the SKILL table; weights here are the single source of truth):
  - demand        20%  GSC impressions (log-scaled) or Suggest presence (no paid KD)
  - current_rank  20%  already ranking 8–20 = quick win; ranking 1–7 = lower upside
  - competition   20%  SERP authority heuristic from parsed search results (degrades)
  - intent_match  15%  topic intent vs SERP signal (degrades to neutral if no SERP)
  - content_gap   15%  must-cover topics not yet covered by published set
  - link_fit      10%  token overlap with published articles (internal-link potential)

Honesty: every factor that lacks data reports `data=low` and falls back to a neutral
0.5 rather than fabricating a number. The AI's job becomes explaining the score, not
inventing it.

Usage:
  python3 research_scorer.py --website laserpointerhub --slug duty-cycle-laser-pointer
  python3 research_scorer.py --website laserpointerhub --data path/to/research-data.json
  python3 research_scorer.py --website laserpointerhub --slug ... --json
"""

import argparse
import json
import math
import re
import sys
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
    from seo_config import RESEARCH_WEIGHTS as WEIGHTS
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    _sc_cfg = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_sc_cfg)
    WEIGHTS = _sc_cfg.RESEARCH_WEIGHTS


# verdict thresholds for polish; intentionally kept local to this scorer (not
# site-wide cannibalization thresholds), so they live here, not in seo_config.

AUTHORITY_DOMAINS = ('wikipedia.org', 'reddit.com', 'youtube.com', 'amazon.',
                     'ehs.', '.edu', '.gov', 'researchgate', 'sciencedirect')


def _tok(s: str) -> set:
    return seo_common.match_tokens(s)


def f_demand(topic: str, gsc_opps: List[dict]) -> Tuple[float, str, str]:
    ttoks = _tok(topic)
    max_impr = max([o.get('impressions', 0) or 0 for o in gsc_opps], default=0)
    best = 0
    for o in gsc_opps:
        if len(_tok(o.get('keyword', '')) & ttoks) >= 2:
            best = max(best, o.get('impressions', 0) or 0)
    if best > 0 and max_impr > 0:
        return (round(min(math.log10(best + 1) / math.log10(max_impr + 1), 1.0), 3),
                f'GSC展示{best}', 'high')
    return (0.45, 'GSC无直接匹配（按中性需求处理）', 'low')


def f_current_rank(topic: str, gsc_opps: List[dict]) -> Tuple[float, str, str]:
    ttoks = _tok(topic)
    pos = None
    for o in gsc_opps:
        if len(_tok(o.get('keyword', '')) & ttoks) >= 2:
            try:
                pos = float(o.get('position', 99))
            except (TypeError, ValueError):
                pos = None
            break
    if pos is None:
        return (0.6, '未排名（全新机会，上升空间大）', 'low')
    if 8 <= pos <= 20:
        return (1.0, f'排名{pos}（快速赢家，最易上首页）', 'high')
    if pos < 8:
        return (0.5, f'排名{pos}（已在首页，提升空间有限）', 'high')
    return (0.7, f'排名{pos}（第3页外，需发力）', 'high')


def f_competition(search: Dict) -> Tuple[float, str, str]:
    serp = (search or {}).get('serp', '') if search else ''
    if not serp:
        return (0.5, '无SERP数据（中性）', 'low')
    urls = re.findall(r'https?://[^\s)\]]+', serp)
    if not urls:
        return (0.5, 'SERP无URL（中性）', 'low')
    auth = sum(1 for u in urls if any(d in u.lower() for d in AUTHORITY_DOMAINS))
    ratio = auth / len(urls)
    # more authority domains = harder = lower score
    score = round(1.0 - min(ratio, 1.0), 3)
    return (score, f'SERP权威域占比{int(ratio*100)}%（越高越难）', 'high')


def f_intent_match(topic_ctx: Dict, search: Dict) -> Tuple[float, str, str]:
    intent = (topic_ctx or {}).get('intent', '')
    serp = (search or {}).get('serp', '').lower() if search else ''
    if not serp or not intent:
        return (0.5, '无足够SERP/意图数据（中性）', 'low')
    commercial_serp = sum(serp.count(w) for w in ['best', 'review', 'buy', 'price', 'top '])
    info_serp = sum(serp.count(w) for w in ['how', 'what', 'guide', 'why', 'explained'])
    serp_intent = '转化型' if commercial_serp > info_serp else '信息型'
    match = (intent == serp_intent) or (intent == '混合型')
    return (1.0 if match else 0.4,
            f'文章意图={intent} / SERP={serp_intent} → {"匹配" if match else "错配"}', 'high')


def f_content_gap(search: Dict, published: Dict) -> Tuple[float, str, str]:
    gaps = (search or {}).get('gaps', '') if search else ''
    must = (search or {}).get('serp', '') if search else ''
    blob = (gaps + ' ' + must).strip()
    if not blob:
        return (0.5, '无竞品/缺口数据（中性）', 'low')
    # candidate subtopics = capitalized phrases / bullet items
    items = re.findall(r'(?:^|\n)[\-\*\d\.]+\s*([A-Za-z][^\n]{6,60})', blob)
    if not items:
        return (0.5, '未解析出子话题（中性）', 'low')
    pub_tokens = set()
    for a in (published or {}).get('articles', []):
        pub_tokens |= _tok(a.get('slug', '').replace('-', ' '))
        for t in a.get('tags', []):
            pub_tokens |= _tok(t)
    uncovered = sum(1 for it in items if not (_tok(it) & pub_tokens))
    ratio = uncovered / len(items)
    return (round(ratio, 3), f'{uncovered}/{len(items)} 子话题未覆盖', 'high')


def f_link_fit(topic: str, published: Dict) -> Tuple[float, str, str]:
    ttoks = _tok(topic)
    if not ttoks:
        return (0.3, '主题无有效token', 'high')
    related = 0
    for a in (published or {}).get('articles', []):
        atoks = _tok(a.get('slug', '').replace('-', ' '))
        for t in a.get('tags', []):
            atoks |= _tok(t)
        if len(ttoks & atoks) >= 2:
            related += 1
    # 3+ related articles = good internal-link potential
    return (round(min(related / 3, 1.0), 3), f'{related} 篇相关可内链', 'high')


def score_topic(data: Dict) -> Dict:
    topic = data.get('topic', '')
    seo = data.get('seo_data', {}) or {}
    gsc_opps = seo.get('gsc_opportunities', []) or []
    search = data.get('search_results') or {}
    published = data.get('published', {}) or {}
    topic_ctx = data.get('topic_context', {}) or {}

    factors = {
        'demand': f_demand(topic, gsc_opps),
        'current_rank': f_current_rank(topic, gsc_opps),
        'competition': f_competition(search),
        'intent_match': f_intent_match(topic_ctx, search),
        'content_gap': f_content_gap(search, published),
        'link_fit': f_link_fit(topic, published),
    }
    composite = sum(WEIGHTS[k] * v[0] for k, v in factors.items())
    low_data = [k for k, v in factors.items() if v[2] == 'low']
    return {
        'topic': topic,
        'opportunity_score': round(composite, 3),
        'verdict': ('🟢 强机会' if composite >= 0.65 else
                    ('🟡 中等' if composite >= 0.45 else '🔴 偏弱')),
        'factors': {k: {'score': v[0], 'why': v[1], 'data': v[2], 'weight': WEIGHTS[k]}
                    for k, v in factors.items()},
        'low_confidence_factors': low_data,
        'cannibal_risk': topic_ctx.get('cannibal_risk', ''),
    }


def render(result: Dict) -> str:
    L = []
    L.append(f"# RESEARCH 机会评分 — {result['topic']}")
    L.append(f"> 综合机会分: **{result['opportunity_score']}** → {result['verdict']}  "
             f"| 日期: {datetime.now():%Y-%m-%d}")
    L.append('')
    L.append('> 脚本确定性打分。AI 的职责：解释这个分数、结合素材决定切入角度，而不是重新打分。')
    L.append('')
    L.append('| 因子 | 权重 | 得分 | 依据 | 数据 |')
    L.append('|------|-----:|-----:|------|------|')
    for k, f in result['factors'].items():
        flag = '⚠️低' if f['data'] == 'low' else '✅'
        L.append(f"| {k} | {int(f['weight']*100)}% | {f['score']} | {f['why']} | {flag} |")
    L.append('')
    if result['low_confidence_factors']:
        L.append(f"⚠️ 数据不足、按中性处理的因子：{', '.join(result['low_confidence_factors'])}"
                 "（多为缺 SERP，建议补全搜索结果后重评）")
    if result.get('cannibal_risk'):
        L.append(f"\n蚕食预判（来自 topic-context）：{result['cannibal_risk']}")
    return '\n'.join(L)


def find_latest(website_dir: Path, slug: str) -> Optional[Path]:
    research = website_dir / 'research'
    if not research.exists():
        return None
    cands = sorted(research.glob(f'research-data-{slug}-*.json'))
    return cands[-1] if cands else None


def main():
    ap = argparse.ArgumentParser(description='RESEARCH deterministic opportunity scorer')
    ap.add_argument('--website', required=True)
    ap.add_argument('--slug', help='topic slug (finds latest research-data JSON)')
    ap.add_argument('--data', help='explicit path to research-data JSON')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--output')
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    website_dir = project_root / args.website

    data_path = Path(args.data) if args.data else (
        find_latest(website_dir, args.slug) if args.slug else None)
    if not data_path or not data_path.exists():
        print('Error: research-data JSON not found. Run research_collector collect first.',
              file=sys.stderr)
        sys.exit(1)

    data = json.loads(data_path.read_text(encoding='utf-8'))
    result = score_topic(data)
    output = json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result)

    slug = args.slug or seo_common.slugify(result['topic'])
    out_path = Path(args.output) if args.output else (
        website_dir / 'research' / f'research-score-{slug}-{datetime.now():%Y-%m-%d}.md')
    if not args.json:
        out_path.write_text(output, encoding='utf-8')
    print(output)
    if not args.json:
        print(f'\n✅ 机会评分: {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
