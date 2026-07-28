#!/usr/bin/env python3
"""PLAN scorer — deterministic, reproducible Top-N candidate scoring.

Reads the structured brief produced by plan_collector.py
(`{website}/research/plan-brief-{date}.json`) and outputs two SEPARATE pipelines:

  🔧 OPTIMIZE  — GSC-driven. Existing pages that should be re-titled / re-meta'd to
                 convert impressions into clicks. Does NOT create new articles.

  ✍️ DISCOVER  — Gap-driven. New articles worth writing, scored on demand proxy +
                 cluster gap + pain-point heat + commercial value. GSC is demoted
                 from a 40% master weight to one of several demand proxies, breaking
                 the historical "GSC keeps recommending rejected red-ocean" loop.

Why a script (not the AI) does this:
  - Counting, matching, ranking must be deterministic & back-testable.
  - The AI's job is downstream: package the Top-N seeds into article angles and
    apply brand judgement. It should not be doing arithmetic.

Usage:
  python3 plan_scorer.py --website laserpointerhub
  python3 plan_scorer.py --website laserpointerhub --brief path/to/plan-brief.json
  python3 plan_scorer.py --website laserpointerhub --json
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
    from seo_config import PLAN_WEIGHTS, FEEDBACK_PENALTY as _FB_PENALTY
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    _sc_cfg = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_sc_cfg)
    PLAN_WEIGHTS = _sc_cfg.PLAN_WEIGHTS
    _FB_PENALTY = _sc_cfg.FEEDBACK_PENALTY


# ── Scoring weights (DISCOVER pipeline) — sourced from seo_config (single truth) ──
W_DEMAND = PLAN_WEIGHTS['demand']
W_CLUSTER_GAP = PLAN_WEIGHTS['cluster_gap']
W_PAIN = PLAN_WEIGHTS['pain_point']
W_VALUE = PLAN_WEIGHTS['value']

TOP_DISCOVER = 8
TOP_OPTIMIZE = 6

# Penalty multiplier applied when a candidate matches user-rejected feedback patterns.
FEEDBACK_PENALTY = _FB_PENALTY


# ── Reuse canonical shared primitives from seo_common (no local copies) ──
# match_tokens / classify_value previously duplicated here as _tokens / classify_value.
_tokens = seo_common.match_tokens
classify_value = seo_common.classify_value


# ── Demand proxy (Phase 4 — GSC impressions + Suggest presence, no paid API) ──

def demand_score(keyword: str, gsc_index: Dict[str, dict], max_impr: int,
                 suggest_set: set) -> Tuple[float, str]:
    """0..1 demand proxy.
      - In GSC with impressions  → log-scaled real demand (strongest signal)
      - Appears in Suggest pool   → validated existence (medium)
      - Neither                   → weak prior
    """
    kl = keyword.lower().strip()
    if kl in gsc_index and max_impr > 0:
        impr = gsc_index[kl].get('impressions', 0) or 0
        if impr > 0:
            score = math.log10(impr + 1) / math.log10(max_impr + 1)
            return (round(min(score, 1.0), 3), f'GSC展示{impr}')
    # token-level suggest presence
    ktoks = _tokens(keyword)
    if ktoks and any(ktoks <= _tokens(s) or ktoks & _tokens(s) for s in suggest_set):
        return (0.45, 'Suggest验证')
    return (0.2, '弱先验')


# ── Cluster gap & pain mapping ──

def cluster_gap_score(keyword: str, clusters: List[dict], max_count: int) -> Tuple[float, str]:
    """Map keyword to its nearest cluster by token overlap, score the gap (less = better)."""
    ktoks = _tokens(keyword)
    best, best_overlap = None, 0
    for c in clusters:
        ckw = set()
        for k in c.get('keywords', []):
            ckw |= _tokens(k)
        ov = len(ktoks & ckw)
        if ov > best_overlap:
            best_overlap, best = ov, c
    if best is None:
        return (0.5, '无匹配集群')
    count = best.get('article_count', 0)
    gap = 1.0 - (count / max_count if max_count else 0)
    return (round(gap, 3), f"{best['name']}({count}篇)")


def pain_score(keyword: str, pain_tags: Dict[str, int], max_pain: int) -> Tuple[float, str]:
    """Relate keyword to pain tags by substring/token; score by pain density."""
    kl = keyword.lower()
    hit_tag, hit_count = None, 0
    for tag, cnt in pain_tags.items():
        if tag.lower() in kl or any(tok in kl for tok in _tokens(tag)):
            if cnt > hit_count:
                hit_count, hit_tag = cnt, tag
    if not hit_tag:
        return (0.1, '无关联痛点')
    return (round(hit_count / max_pain if max_pain else 0, 3), f'{hit_tag}×{hit_count}')


# ── Pre-emptive cannibalization (Phase 1/3 — candidate vs published, lightweight) ──

def cannibal_risk(keyword: str, slugs: List[str]) -> Tuple[str, str]:
    """Lightweight token-overlap proxy (real TF-IDF needs a written article)."""
    ktoks = _tokens(keyword)
    if not ktoks:
        return ('—', '')
    best_ratio, best_slug = 0.0, ''
    for slug in slugs:
        stoks = _tokens(slug.replace('-', ' '))
        if not stoks:
            continue
        inter = len(ktoks & stoks)
        ratio = inter / len(ktoks)
        if ratio > best_ratio:
            best_ratio, best_slug = ratio, slug
    if best_ratio >= 0.75:
        return ('🔴 高', best_slug[:35])
    if best_ratio >= 0.5:
        return ('🟡 中', best_slug[:35])
    return ('🟢 低', best_slug[:35] if best_ratio else '')


# ── Feedback filter (Phase 5 hook) ──

def feedback_tokens(feedback: dict) -> set:
    """Collect rejected-pattern tokens so the scorer can downweight them.

    Supports both the legacy flat schema (rejected_recommendations) and the new
    structured schema (avoid_topics + history[action=reject]).
    """
    toks = set()
    for item in feedback.get('rejected_recommendations', []):  # legacy
        toks |= _tokens(item, drop_stop=False)
    for item in feedback.get('avoid_topics', []):
        toks |= _tokens(item, drop_stop=False)
    for entry in feedback.get('history', []):
        if entry.get('action') == 'reject':
            for item in entry.get('items', []):
                toks |= _tokens(item, drop_stop=False)
    return toks


def feedback_penalised(keyword: str, fb_toks: set) -> bool:
    if not fb_toks:
        return False
    ktoks = _tokens(keyword, drop_stop=False)  # keep all tokens for feedback matching
    if not ktoks:
        return False
    # penalise if the candidate is dominated by rejected tokens
    overlap = len(ktoks & fb_toks)
    return overlap >= 2 or (overlap >= 1 and overlap == len(ktoks))


# ── Pipelines ──

def build_optimize(brief: dict) -> List[dict]:
    """Existing pages with impressions but poor CTR / page-2 rank → retitle targets."""
    opps = brief.get('gsc_opportunities', [])
    articles = brief.get('published_coverage', {}).get('articles', [])
    out = []
    for o in opps:
        kw = o.get('keyword', '')
        ktoks = _tokens(kw)
        # find covering article (>=2 distinctive token overlap)
        target = None
        for a in articles:
            atoks = _tokens(a.get('slug', '').replace('-', ' '))
            for t in a.get('tags', []):
                atoks |= _tokens(t)
            if len(ktoks & atoks) >= 2:
                target = a.get('slug', '')
                break
        if not target:
            continue  # no existing page → belongs to DISCOVER, not OPTIMIZE
        impr = o.get('impressions', 0) or 0
        ctr = o.get('ctr', 0) or 0
        pos = o.get('position', 0)
        try:
            posf = float(pos)
        except (TypeError, ValueError):
            posf = 99
        # score: impressions matter most; reward low CTR and near-page-1 rank
        score = (math.log10(impr + 1) * 4) + max(0, (3 - ctr)) * 3
        if 4 <= posf <= 20:
            score += (20 - posf) * 0.5
        action = '改标题/Meta' if ctr < 2 else ('补内容上首页' if 8 <= posf <= 20 else '优化')
        out.append({
            'keyword': kw, 'target_slug': target, 'impressions': impr,
            'ctr': ctr, 'position': pos, 'type': o.get('type', ''),
            'score': round(score, 1), 'action': action,
        })
    out.sort(key=lambda x: -x['score'])
    return out[:TOP_OPTIMIZE]


def build_discover(brief: dict) -> Tuple[List[dict], List[str]]:
    """New-article candidates from GSC gaps + cluster gaps + pain themes."""
    opps = brief.get('gsc_opportunities', [])
    clusters = brief.get('topic_clusters', [])
    pain_tags = brief.get('pain_point_tags', {}) or {}
    articles = brief.get('published_coverage', {}).get('articles', [])
    slugs = [a.get('slug', '') for a in articles]
    feedback = brief.get('feedback', {}) or {}
    fb_toks = feedback_tokens(feedback)

    # indexes
    gsc_index = {o.get('keyword', '').lower(): o for o in opps}
    max_impr = max([o.get('impressions', 0) or 0 for o in opps], default=0)
    max_cluster = max([c.get('article_count', 0) for c in clusters], default=1) or 1
    max_pain = max(pain_tags.values(), default=1) or 1

    suggest_pool = brief.get('keyword_pool', {}).get('newly_discovered', [])
    suggest_set = {item.get('keyword', '') for item in suggest_pool}

    candidates = {}  # keyword/theme -> candidate dict (dedup)

    # cluster lookup for saturation routing
    def nearest_cluster_count(seed: str) -> int:
        ktoks = _tokens(seed)
        best, best_ov = 0, 0
        for c in clusters:
            ckw = set()
            for k in c.get('keywords', []):
                ckw |= _tokens(k)
            ov = len(ktoks & ckw)
            if ov > best_ov:
                best_ov, best = ov, c.get('article_count', 0)
        return best

    def add(seed: str, origin: str, route_saturated: bool = False):
        key = seed.lower().strip()
        if not key or key in candidates:
            return
        ktoks = _tokens(seed)
        if not ktoks:
            return  # fully generic seed
        # coverage guard: skip if an existing article already covers it (>=2 tokens)
        for a in articles:
            atoks = _tokens(a.get('slug', '').replace('-', ' '))
            for t in a.get('tags', []):
                atoks |= _tokens(t)
            if len(ktoks & atoks) >= 2:
                return  # covered → not a discover candidate
        # saturation routing: a commercial query whose cluster is already dense
        # (>=5 articles) is OPTIMIZE territory, not a new-article DISCOVER gap.
        # This is the core fix that stops GSC red-ocean from re-entering discover.
        if route_saturated:
            vlabel, _ = classify_value(seed)
            if vlabel == '转化型' and nearest_cluster_count(seed) >= 5:
                return
        candidates[key] = {'seed': seed, 'origin': origin}

    # 1) GSC opportunities with NO coverage AND not a saturated-commercial cluster
    for o in opps:
        add(o.get('keyword', ''), 'GSC缺口', route_saturated=True)

    # 2) Cluster gaps → readable seed from top alphabetic distinctive tokens.
    # Threshold scales with site size: 40% of max_coverage for large sites,
    # but at least 1 for small sites (3 articles → a 2-article cluster IS a gap).
    gap_threshold = max(1, int(max_cluster * 0.4))
    for c in sorted(clusters, key=lambda x: x.get('article_count', 0)):
        if c.get('article_count', 0) <= gap_threshold:
            alpha = [k for k in c.get('keywords', []) if not k.isdigit()][:2]
            seed = (' '.join(alpha) + ' laser pointer').strip() if alpha else c.get('name', '')
            add(seed, f"集群缺口:{c.get('name','')[:12]}")

    # 3) Pain themes → seed is the tag itself (AI repackages into article angle)
    for tag, cnt in sorted(pain_tags.items(), key=lambda x: -x[1])[:6]:
        add(tag, f'痛点:{tag}×{cnt}')

    # score every candidate
    scored = []
    notes = []
    for cand in candidates.values():
        seed = cand['seed']
        d, d_why = demand_score(seed, gsc_index, max_impr, suggest_set)
        g, g_why = cluster_gap_score(seed, clusters, max_cluster)
        p, p_why = pain_score(seed, pain_tags, max_pain)
        vlabel, vweight = classify_value(seed)
        total = W_DEMAND * d + W_CLUSTER_GAP * g + W_PAIN * p + W_VALUE * vweight
        penalised = feedback_penalised(seed, fb_toks)
        if penalised:
            total *= FEEDBACK_PENALTY
            notes.append(seed)
        risk, risk_slug = cannibal_risk(seed, slugs)
        # Body-level probe (heavier than slug check — reads published bodies).
        # Catches cases where tags are clean but the body content would collide
        # (e.g. "buying guide" vs a pillar with no 'buying guide' tag but heavy overlap).
        body_probe = {'max_score': 0, 'top_slug': '', 'severe': False}
        try:
            w = brief.get('meta', {}).get('website', '')
            if w:
                bp = seo_common.probe_topic_overlap(w, seed, top_n=3)
            if bp:
                body_probe['max_score'] = bp[0]['score']
                body_probe['top_slug'] = bp[0]['slug']
                body_probe['severe'] = bp[0]['hit_ratio'] >= 0.5 and bp[0]['score'] >= 2
                if body_probe['severe']:
                    # Upgrade risk if body-level overlap is significant
                    if risk == '🟢 低':
                        risk, risk_slug = '🟡 中(body)', bp[0]['slug']
                    elif risk == '🟡 中':
                        risk, risk_slug = '🔴 高(body)', bp[0]['slug']
        except Exception:
            pass

        scored.append({
            'seed': seed, 'origin': cand['origin'],
            'score': round(total, 4),
            'demand': d, 'demand_why': d_why,
            'cluster_gap': g, 'cluster_why': g_why,
            'pain': p, 'pain_why': p_why,
            'value': vlabel, 'value_weight': vweight,
            'cannibal_risk': risk, 'cannibal_slug': risk_slug,
            'feedback_penalised': penalised,
        })
    scored.sort(key=lambda x: -x['score'])
    return scored[:TOP_DISCOVER], notes


# ── Render ──

def render_markdown(website: str, brief: dict, optimize: List[dict],
                    discover: List[dict], penalised: List[str]) -> str:
    date = datetime.now().strftime('%Y-%m-%d')
    meta = brief.get('meta', {})
    L = []
    L.append(f'# PLAN 候选评分 — {website}  ({date})')
    L.append(f"> 已发布 {meta.get('published_articles','?')} 篇 | GSC {meta.get('gsc_data_updated','?')} | "
             f"权重 需求{W_DEMAND}/缺口{W_CLUSTER_GAP}/痛点{W_PAIN}/价值{W_VALUE}")
    L.append('')
    L.append('> **脚本只做确定性打分。AI 的职责：把下表 Top 候选包装成有信息增量的文章角度 + 品牌判断。**')
    L.append('')

    # OPTIMIZE
    L.append('## 🔧 优化管线（改已有页，不写新文）')
    L.append('')
    if optimize:
        L.append('| # | 关键词 | 目标文章 | 展示 | CTR | 排名 | 优化分 | 动作 |')
        L.append('|---|--------|----------|-----:|----:|----:|------:|------|')
        for i, o in enumerate(optimize, 1):
            L.append(f"| {i} | {o['keyword'][:40]} | `{o['target_slug'][:32]}` | {o['impressions']} | "
                     f"{o['ctr']}% | {o['position']} | {o['score']} | {o['action']} |")
    else:
        L.append('_无可优化的已覆盖高展示页_')
    L.append('')

    # DISCOVER
    L.append('## ✍️ 发现管线（写新文）')
    L.append('')
    has_gsc = any(o.get('impressions', 0) for o in brief.get('gsc_opportunities', []))
    if not has_gsc:
        L.append('> 💡 **GSC 数据未导入**（新站正常）。发现管线由痛点热度 + 集群缺口驱动，需求维度暂按中性处理。'
                 '导入 GSC Excel 后 `/plan` 重新运行，需求分将基于真实展示量计算。')
        L.append('')
    L.append('| # | 候选种子 | 来源 | 总分 | 需求 | 缺口 | 痛点 | 价值 | 蚕食 |')
    L.append('|---|----------|------|-----:|-----:|-----:|-----:|------|------|')
    for i, c in enumerate(discover, 1):
        L.append(f"| {i} | {c['seed'][:38]} | {c['origin']} | **{c['score']}** | "
                 f"{c['demand']} | {c['cluster_gap']} | {c['pain']} | {c['value']} | {c['cannibal_risk']} |")
    L.append('')
    # signal detail for the top 3 (so AI sees the 'why')
    L.append('### Top 3 信号明细')
    for c in discover[:3]:
        L.append(f"- **{c['seed']}** — 需求:{c['demand_why']} | 缺口:{c['cluster_why']} | "
                 f"痛点:{c['pain_why']} | 价值:{c['value']} | 蚕食:{c['cannibal_risk']} {c['cannibal_slug']}")
    L.append('')

    if penalised:
        L.append('## ⚠️ 反馈过滤（已降权 ×0.2）')
        L.append('以下候选命中用户历史拒绝模式：')
        for s in penalised:
            L.append(f'- {s}')
        L.append('')

    L.append('---')
    L.append('**下一步**：人工/AI 从发现管线 Top 候选选定 → `/research [slug]`；'
             '或从优化管线选 1 篇 → 改标题/Meta。')
    return '\n'.join(L)


def find_latest_brief(website_dir: Path) -> Optional[Path]:
    research = website_dir / 'research'
    if not research.exists():
        return None
    briefs = sorted(research.glob('plan-brief-*.json'))
    return briefs[-1] if briefs else None


def main():
    ap = argparse.ArgumentParser(description='PLAN deterministic scorer')
    ap.add_argument('--website', required=True)
    ap.add_argument('--brief', help='Path to plan-brief-*.json (default: latest)')
    ap.add_argument('--slug', help='Slug for guidance-only update (no scoring)')
    ap.add_argument('--guidance', help='AI differentiation notes — injects into topic-context')
    ap.add_argument('--json', action='store_true', help='Output JSON instead of Markdown')
    ap.add_argument('--output', help='Output file path')
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    website_dir = project_root / args.website
    if not website_dir.exists():
        print(f'Error: website dir not found: {website_dir}', file=sys.stderr)
        sys.exit(1)

    # ── Guidance-only mode: inject AI's differentiation notes ──
    if args.guidance and args.slug:
        p = seo_common.write_topic_context(
            website_dir, args.slug, topic='', source='plan', guidance=args.guidance)
        print(f'✅ guidance injected: {p}', file=sys.stderr)
        sys.exit(0)

    brief_path = Path(args.brief) if args.brief else find_latest_brief(website_dir)
    if not brief_path or not brief_path.exists():
        print('Error: no plan-brief JSON found. Run plan_collector.py first.', file=sys.stderr)
        sys.exit(1)

    brief = json.loads(brief_path.read_text(encoding='utf-8'))

    optimize = build_optimize(brief)
    discover, penalised = build_discover(brief)

    # Phase B: write a topic-context handoff for the top discover candidate so
    # RESEARCH/WRITE inherit intent/tier/signals instead of re-guessing (S1).
    if discover:
        try:
            import seo_common as _sc
        except ImportError:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location('seo_common', Path(__file__).resolve().parent / 'seo_common.py')
            _sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
        top = discover[0]
        seed = top['seed']
        c_slug = _sc.slugify(seed)
        vlabel = top.get('value', '')
        intent = '转化型' if vlabel == '转化型' else ('信息型' if vlabel == '信息型' else '混合型')

        # ── Auto guidance from body probe + cannibal signal ──
        auto_guidance = ''
        risk = top.get('cannibal_risk', '')
        risk_slug = top.get('cannibal_slug', '')
        if '(body)' in risk and risk_slug:
            auto_guidance = f'scorer body-probe flagged overlap with {risk_slug}. '
        elif '🔴' in risk and risk_slug:
            auto_guidance = f'tag-level overlap with {risk_slug}. Ensure differentiation. '
        if risk != '🟢 低' and not auto_guidance:
            auto_guidance = f'cannibal risk {risk}. Find a unique angle vs existing published articles.'
        auto_guidance = auto_guidance.strip()

        _sc.write_topic_context(
            website_dir, c_slug, topic=seed, source='plan',
            intent=intent, tier=_sc.detect_tier(seed),
            primary_keyword=seed, plan_score=top.get('score'),
            cluster=top.get('cluster_why', ''),
            cannibal_risk=top.get('cannibal_risk', ''),
            signals={'pain': top.get('pain_why', ''), 'cluster_gap': top.get('cluster_why', ''),
                     'demand': top.get('demand_why', ''), 'origin': top.get('origin', '')},
            guidance=auto_guidance,
        )
        print(f'✅ topic-context: research/topic-context-{c_slug}.json (source=plan)', file=sys.stderr)

    date = datetime.now().strftime('%Y-%m-%d')
    if args.json:
        output = json.dumps({
            'website': args.website, 'date': date,
            'weights': {'demand': W_DEMAND, 'cluster_gap': W_CLUSTER_GAP,
                        'pain': W_PAIN, 'value': W_VALUE},
            'optimize': optimize, 'discover': discover, 'penalised': penalised,
        }, ensure_ascii=False, indent=2)
    else:
        output = render_markdown(args.website, brief, optimize, discover, penalised)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = website_dir / 'research' / f'plan-candidates-{date}.md'
    out_path.write_text(output, encoding='utf-8')
    print(output)
    print(f'\n✅ 候选评分: {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
