#!/usr/bin/env python3
"""PLAN feedback recorder — structured, back-testable closed loop.

Turns one-off "全部不满意" notes into a structured history so the DISCOVER pipeline
can (a) avoid rejected directions and (b) eventually learn from published outcomes
(was the recommendation adopted? did it rank?).

Schema ({website}/research/plan-feedback.json):
{
  "avoid_topics":     ["..."],     # active tokens/titles the scorer downweights ×0.2
  "prefer_clusters":  ["..."],     # clusters to bias toward (surfaced in brief)
  "notes":            "...",
  "history": [
    {"date":"YYYY-MM-DD","action":"reject","items":["title", ...],"reason":"..."},
    {"date":"YYYY-MM-DD","action":"accept","slug":"...","score":0.49},
    {"date":"YYYY-MM-DD","action":"rank","slug":"...","position":7}
  ]
}

Usage:
  # record a rejection (also adds to avoid_topics)
  python3 plan_feedback.py --website laserpointerhub reject "Best Under $1000" "USB-C Guide" --reason "全部不满意"

  # record an adopted recommendation
  python3 plan_feedback.py --website laserpointerhub accept --slug duty-cycle-laser-pointer --score 0.49

  # later, log a measured ranking for back-testing
  python3 plan_feedback.py --website laserpointerhub rank --slug duty-cycle-laser-pointer --position 7

  # prefer/clear a cluster bias
  python3 plan_feedback.py --website laserpointerhub prefer "thermal" "optics"

  # show adoption / hit-rate summary
  python3 plan_feedback.py --website laserpointerhub stats
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _path(website: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / website / 'research' / 'plan-feedback.json'


def _load(p: Path) -> dict:
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            data = {}
    else:
        data = {}
    data.setdefault('avoid_topics', [])
    data.setdefault('prefer_clusters', [])
    data.setdefault('notes', '')
    data.setdefault('history', [])
    # migrate legacy flat fields into history (one-time, idempotent)
    if data.get('rejected_recommendations'):
        items = data.pop('rejected_recommendations')
        if not any(h.get('action') == 'reject' and h.get('items') == items
                   for h in data['history']):
            data['history'].append({
                'date': data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'action': 'reject', 'items': items,
                'reason': data.get('reason', '') or data.get('notes', ''),
            })
        for it in items:
            if it not in data['avoid_topics']:
                data['avoid_topics'].append(it)
    data.pop('result', None)
    data.pop('reason', None)
    data.pop('date', None)
    return data


def _save(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def cmd_reject(data, args):
    today = datetime.now().strftime('%Y-%m-%d')
    data['history'].append({'date': today, 'action': 'reject',
                            'items': args.items, 'reason': args.reason or ''})
    for it in args.items:
        if it not in data['avoid_topics']:
            data['avoid_topics'].append(it)
    print(f'记录拒绝 {len(args.items)} 项，已加入 avoid_topics')


def cmd_accept(data, args):
    today = datetime.now().strftime('%Y-%m-%d')
    entry = {'date': today, 'action': 'accept', 'slug': args.slug}
    if args.score is not None:
        entry['score'] = args.score
    data['history'].append(entry)
    print(f'记录采纳: {args.slug}')


def cmd_rank(data, args):
    today = datetime.now().strftime('%Y-%m-%d')
    data['history'].append({'date': today, 'action': 'rank',
                            'slug': args.slug, 'position': args.position})
    print(f'记录排名: {args.slug} → 位置 {args.position}')


def cmd_prefer(data, args):
    data['prefer_clusters'] = args.clusters
    print(f'优先集群设为: {args.clusters}')


def cmd_stats(data, args):
    h = data['history']
    rejects = sum(len(e.get('items', [])) for e in h if e.get('action') == 'reject')
    accepts = [e for e in h if e.get('action') == 'accept']
    ranks = [e for e in h if e.get('action') == 'rank']
    print('=== PLAN 反馈统计 ===')
    print(f'累计拒绝条目: {rejects}')
    print(f'采纳文章数:  {len(accepts)}')
    if ranks:
        avg = sum(e['position'] for e in ranks) / len(ranks)
        print(f'已测排名数:  {len(ranks)} | 平均位置: {avg:.1f}')
    print(f'当前 avoid_topics: {len(data["avoid_topics"])} 项')
    print(f'优先集群: {data["prefer_clusters"] or "无"}')


def main():
    ap = argparse.ArgumentParser(description='PLAN feedback recorder')
    ap.add_argument('--website', required=True)
    sub = ap.add_subparsers(dest='cmd', required=True)

    pr = sub.add_parser('reject'); pr.add_argument('items', nargs='+'); pr.add_argument('--reason')
    pa = sub.add_parser('accept'); pa.add_argument('--slug', required=True); pa.add_argument('--score', type=float)
    pk = sub.add_parser('rank'); pk.add_argument('--slug', required=True); pk.add_argument('--position', type=int, required=True)
    pp = sub.add_parser('prefer'); pp.add_argument('clusters', nargs='+')
    sub.add_parser('stats')

    args = ap.parse_args()
    p = _path(args.website)
    data = _load(p)

    {'reject': cmd_reject, 'accept': cmd_accept, 'rank': cmd_rank,
     'prefer': cmd_prefer, 'stats': cmd_stats}[args.cmd](data, args)

    if args.cmd != 'stats':
        _save(p, data)
        print(f'✅ {p}')


if __name__ == '__main__':
    main()
