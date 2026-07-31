#!/usr/bin/env python3
"""seo_common — shared primitives for the PLAN → RESEARCH → WRITE pipeline.

Single source of truth for logic that was being copy-pasted across
plan_collector / plan_scorer / research_collector / write_collector:

  - DOMAIN_STOPWORDS + match_tokens   (pollution-robust token matching)
  - classify_value                    (commercial vs informational)
  - check_freshness                   (GSC / published-index staleness)
  - cannibal_new_article              (NEW draft vs published — the real guard)
  - cannibal_pairs                    (published-vs-published top pairs)

New code should import from here. Existing modules keep their local copies
working until migrated, but this is the canonical definition.
"""

import hashlib
import importlib.util
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Where the per-website workspaces live. Defaults to the repo root so standalone
# CLI use is unchanged; seo-ops sets SEO_SITES_DIR because its Legacy workspace
# sits under data/legacy_workflow/.
SITES_DIR = Path(os.environ.get('SEO_SITES_DIR') or Path(__file__).resolve().parents[2])

try:
    import seo_config
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    seo_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_config)


# ── Token matching ──────────────────────────────────────

DOMAIN_STOPWORDS = {
    'laser', 'pointer', 'pointers', 'lasers', 'the', 'for', 'to', 'a', 'an',
    'and', 'of', 'with', 'your', 'vs', 'how', 'what', 'is', 'are', 'in', 'on',
    'guide', 'best',
}


def match_tokens(text: str, drop_stop: bool = True) -> set:
    """Lowercase token set for coverage/cluster/library matching."""
    if not text:
        return set()
    raw = re.findall(r'[a-z0-9]+(?:nm)?', str(text).lower())
    out = set()
    for t in raw:
        if len(t) < 2 and not t.isdigit():
            continue
        if drop_stop and t in DOMAIN_STOPWORDS:
            continue
        out.add(t)
    return out


# ── Value classification ────────────────────────────────

COMMERCIAL_MARKERS = {'best', 'buy', 'price', 'cheap', 'review', 'reviews', 'top',
                      'under', 'deal', 'cost', 'sale', 'vs', 'comparison', 'budget'}
INFORMATIONAL_MARKERS = {'how', 'what', 'why', 'is', 'are', 'can', 'does', 'safe',
                         'safety', 'guide', 'explained', 'work', 'works'}


def classify_value(keyword: str) -> Tuple[str, float]:
    """Return (label, weight). Commercial converts; informational builds authority."""
    toks = set(re.findall(r'[a-z0-9$]+', keyword.lower()))
    if toks & COMMERCIAL_MARKERS or '$' in keyword:
        return ('转化型', 1.0)
    if toks & INFORMATIONAL_MARKERS:
        return ('信息型', 0.7)
    return ('混合型', 0.85)


def detect_tier(topic: str) -> str:
    """Heuristic page tier — used only as fallback when no topic-context exists."""
    t = topic.lower()
    if any(w in t for w in ['best', 'top', 'review', 'vs', 'comparison', 'roundup']):
        return 'Product Roundup'
    return 'Pillar Page' if len(topic.split()) <= 2 else 'Cluster Content'


# ── Shared markdown / file helpers ───────────────────────
# Previously copy-pasted across plan_collector / research_collector /
# write_collector as local _date_str / _extract_section / _extract_table plus
# inline frontmatter splitting. These are the canonical versions.

def today_str() -> str:
    """Return today's date as YYYY-MM-DD (replaces the local _date_str copies)."""
    return datetime.now().strftime('%Y-%m-%d')


def extract_section(content: str, start_marker: str, end_marker: str) -> str:
    """Extract text between two markers.

    Handles the empty end_marker (-> to end of content) which the original
    plan_collector version did not; callers that always pass a non-empty
    end_marker are unaffected.
    """
    start_idx = content.find(start_marker)
    if start_idx < 0:
        return ''
    start_idx += len(start_marker)
    if end_marker:
        end_idx = content.find(end_marker, start_idx)
        if end_idx < 0:
            end_idx = len(content)
    else:
        end_idx = len(content)
    return content[start_idx:end_idx]


def extract_table(content: str, start_marker: str, end_marker: str) -> List[List[str]]:
    """Extract a markdown table (list of cell-list rows) between two markers."""
    section = extract_section(content, start_marker, end_marker)
    rows: List[List[str]] = []
    for line in section.split('\n'):
        line = line.strip()
        if line.startswith('|') and '---' not in line:
            cells = [c.strip() for c in line.split('|')]
            cells = [c for c in cells if c]
            if cells and cells[0] not in ('#', '关键词', '查询词', ''):
                rows.append(cells)
    return rows


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Split a markdown file into (meta_dict_lowercase_keys, body).

    Returns ({}, content) when there is no YAML frontmatter.
    """
    if not content.startswith('---'):
        return {}, content
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content
    meta: Dict[str, str] = {}
    for line in parts[1].strip().split('\n'):
        m = re.match(r'^([^:]+?)\s*:\s*(.+)', line.strip())
        if m:
            meta[m.group(1).strip().lower()] = m.group(2).strip()
    return meta, parts[2]


def strip_frontmatter(content: str) -> str:
    """Return the body of a markdown file with YAML frontmatter removed."""
    if not content.startswith('---'):
        return content
    parts = content.split('---', 2)
    return parts[2] if len(parts) >= 3 else content


def load_published_index(website_dir) -> List[Dict]:
    """Load published-index.json for a website, tolerant of missing/bad files.

    Returns the list of entries (or [] on any failure). Never raises.
    """
    p = Path(website_dir) / 'published' / 'published-index.json'
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def find_latest(directory, pattern: str) -> Optional[Path]:
    """Glob a directory for pattern and return the lexicographically-last match.

    Used for "find the latest plan-brief / research-data / search-results file".
    Returns None if the directory or pattern matches nothing.
    """
    d = Path(directory)
    if not d.exists():
        return None
    cands = sorted(d.glob(pattern))
    return cands[-1] if cands else None


# ── Freshness check (S4) ────────────────────────────────

def check_freshness(website_dir, max_age_days: int = 7) -> Dict:
    """Inspect GSC manual + published-index recency. Returns warnings list."""
    website_dir = Path(website_dir)
    today = datetime.now()
    out = {'warnings': [], 'gsc_date': None, 'gsc_stale': False, 'published_count': 0}

    manual = website_dir / 'context' / 'seo-data-manual.md'
    if manual.exists():
        content = manual.read_text(encoding='utf-8')
        m = re.search(r'(?:GSC|最后更新|更新).*?(\d{4}-\d{2}-\d{2})', content)
        date_str = m.group(1) if m else datetime.fromtimestamp(
            manual.stat().st_mtime).strftime('%Y-%m-%d')
        out['gsc_date'] = date_str
        try:
            age = (today - datetime.strptime(date_str, '%Y-%m-%d')).days
            if age > max_age_days:
                out['gsc_stale'] = True
                out['warnings'].append(f'⚠️ GSC 数据 {age} 天未更新（{date_str}），结论可能过时')
        except ValueError:
            pass
    else:
        out['warnings'].append('⚠️ seo-data-manual.md 不存在，GSC 信号为空')

    pi = website_dir / 'published' / 'published-index.json'
    if pi.exists():
        try:
            out['published_count'] = len(json.loads(pi.read_text(encoding='utf-8')))
        except Exception:
            out['warnings'].append('⚠️ published-index.json 解析失败')
    return out


# ── Cannibalization (canonical, importable) ─────────────
#
# Single entry point for the whole pipeline. plan_collector / research_collector
# used to shell out (subprocess) to cannibalization_checker.py; write_collector
# already used these in-process helpers. Everything now funnels through here so
# thresholds stay consistent and the published-article index is cached.

# Cache: (website, threshold) -> CannibalizationChecker instance (index built).
# Avoids re-reading all published bodies on repeat calls within one run.
_CHECKER_CACHE: Dict[Tuple[str, float], 'object'] = {}


def _get_checker(website: str, threshold: float):
    """Return a cached CannibalizationChecker with its published index built."""
    key = (website, float(threshold))
    if key in _CHECKER_CACHE:
        return _CHECKER_CACHE[key]
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        'cannibalization_checker', here / 'cannibalization_checker.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    checker = mod.CannibalizationChecker(threshold=threshold)
    checker.load_published(website)
    checker.build_index()
    _CHECKER_CACHE[key] = checker
    return checker


def cannibal_new_article(website: str, draft: str, threshold: float = None,
                          top_n: int = 5) -> Dict:
    """Compare a NEW draft (path or raw body) against all published articles.

    This is the guard the pipeline was missing: it answers
    "will THIS article cannibalize an existing one?" — only possible once the
    body exists (WRITE stage). Returns max similarity + top offenders.
    """
    if threshold is None:
        threshold = seo_config.CANNIBAL_THRESHOLD_LOW
    body = draft
    p = Path(draft)
    if p.exists():
        body = p.read_text(encoding='utf-8')
        body = strip_frontmatter(body)

    checker = _get_checker(website, threshold)
    n = len(checker.documents)
    if n < 1:
        return {'max_sim': 0.0, 'top': [], 'note': 'no published articles'}
    results = checker.check_against_all('__new__', body)  # sorted desc
    top = results[:top_n]
    max_sim = top[0]['similarity'] if top else 0.0
    return {'max_sim': max_sim, 'top': top, 'count_published': n}


def cannibal_pairs(website: str, threshold: float = None, top_n: int = 10) -> Dict:
    """Top-N highest-similarity published-vs-published pairs (for PLAN/RESEARCH).

    Returns ALL flagged pairs (>= threshold) plus the total count; callers slice
    top_n as needed. To match the old plan_collector behaviour (full report at
    0.45 + brief excerpt at 0.50) call once at LOW and filter client-side.
    """
    if threshold is None:
        threshold = seo_config.CANNIBAL_THRESHOLD_MID
    checker = _get_checker(website, threshold)
    n = len(checker.documents)
    if n < 2:
        return {'pairs': [], 'total': 0}
    pairs = checker.check_all()  # sorted desc, already gated by checker.threshold
    return {'pairs': pairs[:top_n], 'total': len(pairs), 'all_pairs': pairs,
            'checker': checker}


def risk_label(sim: float) -> str:
    return ('🔴 高' if sim >= seo_config.CANNIBAL_THRESHOLD_HIGH else
            ('🟡 中' if sim >= seo_config.CANNIBAL_THRESHOLD_LOW else '🟢 低'))


# ── Lightweight body-overlap probe (PLAN stage, no draft body) ────
# Runs BEFORE the AI selects a winner — catches candidates whose tags are clean
# but whose body content would collide (e.g. "buying guide" vs a pillar that
# has no 'buying guide' tag but 100+ lines of purchase advice).

def probe_topic_overlap(website: str, seed: str, top_n: int = 5) -> List[Dict]:
    """Token-overlap probe: does a topic seed already appear densely in any
    published article body? Lightweight — no TF-IDF matrix, just counting.

    Returns list of {slug, score, hit_ratio} sorted by score desc.
    score = number of distinct seed tokens found in body (0..n).
    hit_ratio = tokens_found / total_seed_tokens.
    When hit_ratio >= 0.5 and score >= 3, the topic likely overlaps.
    """
    seed_tokens = match_tokens(seed, drop_stop=False)  # keep all tokens for body scan
    if not seed_tokens:
        return []
    site_dir = SITES_DIR / website
    published_dir = site_dir / 'published'
    if not published_dir.exists():
        return []
    results = []
    for f in sorted(published_dir.glob('*.md')):
        body = f.read_text(encoding='utf-8')
        if body.startswith('---'):
            parts = body.split('---', 2)
            body = parts[2] if len(parts) >= 3 else body
        body_lower = body.lower()
        found = sum(1 for t in seed_tokens if t in body_lower)
        if found >= 2:
            results.append({
                'slug': f.stem,
                'score': found,
                'hit_ratio': round(found / len(seed_tokens), 3),
            })
    results.sort(key=lambda x: -x['score'])
    top = results[:top_n]
    return top
# Carries PLAN's structured decision into RESEARCH and WRITE so intent/tier/signals
# aren't re-guessed at each stage. Degrades gracefully: when PLAN didn't run,
# RESEARCH creates one with source="heuristic".

def topic_context_path(website_dir, slug: str) -> Path:
    return Path(website_dir) / 'research' / f'topic-context-{slug}.json'


def read_topic_context(website_dir, slug: str) -> Optional[Dict]:
    p = topic_context_path(website_dir, slug)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def write_topic_context(website_dir, slug: str, *, topic: str, source: str,
                         intent: str = '', tier: str = '', primary_keyword: str = '',
                         plan_score: Optional[float] = None, cluster: str = '',
                         signals: Optional[Dict] = None,
                         cannibal_risk: str = '', guidance: str = '') -> Path:
    """source ∈ {'plan','heuristic'}. Writes/merges the handoff file.
    guidance = AI's differentiation notes from PLAN Step 3 (what to avoid, how to differentiate).
    """
    p = topic_context_path(website_dir, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = read_topic_context(website_dir, slug) or {}
    data.update({
        'slug': slug, 'topic': topic, 'source': source,
        'intent': intent or data.get('intent', ''),
        'tier': tier or data.get('tier', ''),
        'primary_keyword': primary_keyword or data.get('primary_keyword', ''),
        'cluster': cluster or data.get('cluster', ''),
        'cannibal_risk': cannibal_risk or data.get('cannibal_risk', ''),
        'signals': signals if signals is not None else data.get('signals', {}),
        'updated': datetime.now().strftime('%Y-%m-%d'),
    })
    if plan_score is not None:
        data['plan_score'] = plan_score
    if guidance:
        data['guidance'] = guidance
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def slugify(text: str) -> str:
    """Canonical slug shared by all stages (keep in sync with collectors)."""
    s = re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')
    s = re.sub(r'-+', '-', s)
    if s:
        return s
    # Python's hash() is randomized per process, so it cannot identify files
    # across CLI subprocesses or server restarts.
    digest = hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:12]
    return f'topic-{digest}'


# ── Shared claim-text / sentence extraction (single authoritative impl) ──

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _strip_sentence_id_non_prose(text: str) -> str:
    """Remove delivery metadata that must never affect canonical sentence IDs."""
    without_fences = re.sub(
        r"```[\s\S]*?```",
        "",
        text or "",
        flags=re.MULTILINE,
    )
    return re.sub(
        r"<script\b[^>]*\btype\s*=\s*([\"'])\s*application/ld\+json\s*\1[^>]*>"
        r"[\s\S]*?</script\s*>",
        "",
        without_fences,
        flags=re.IGNORECASE,
    )


def normalize_claim_text(text: str) -> str:
    """Normalize a claim_text or draft sentence for exact full-string match.

    - strip
    - collapse all runs of whitespace (including \\n) to single space
    - strip trailing punctuation ``.,;:!?``
    - strip again (trailing punctuation removal may leave space)
    """
    if not text:
        return ""
    text = text.strip()
    text = " ".join(text.split())
    text = text.rstrip(".,;:!?")
    return text.strip()


def extract_draft_sentences(draft_md: str) -> list[dict[str, str]]:
    """Split the draft body into stable, audit-able sentences using the
    same algorithm every consumer must use.

    1. Strip YAML frontmatter (``---...---``).
    2. Remove fenced code and FAQ/other JSON-LD ``<script>`` blocks. They are
       delivery metadata, not reader-visible factual prose, and therefore must
       never renumber body sentence IDs.
    3. Split by blank lines (``\\n\\n+``) into paragraphs.
    4. Within each paragraph split by ``(?<=[.!?])\\s+``.
    5. Strip each sentence, normalise via ``normalize_claim_text``, skip
       empty results.
    6. Assign sequential IDs ``S001``, ``S002``, …

    Returns ``[{"sentence_id": "S001", "text": <original>, "norm": <norm>}, …]``
    """
    body = _strip_sentence_id_non_prose(strip_frontmatter(draft_md or ""))
    sentences: list[dict[str, str]] = []
    for para in re.split(r"\n\n+", body):
        for sent in _SENTENCE_SPLIT_RE.split(para):
            text = sent.strip()
            norm = normalize_claim_text(text)
            if not norm:
                continue
            sentences.append({
                "sentence_id": f"S{len(sentences) + 1:03d}",
                "text": text,
                "norm": norm,
            })
    return sentences
