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
 13. 素材包实体证据覆盖（material-pack entities with evidence）。
     不再读 target-keywords.md 固定集群，不要求 80% 覆盖率，不强行让
     AI 填补与本文意图无关的术语。缺什么、补不补，取决于实体是否同时
     满足"intent_relevance ∈ {core, supporting}" + "有 material-pack
     Source 证据"。core + 有证据的缺失才会 blocking；supporting + 有
     证据的缺失只是 warning；没有 evidence 的实体不进入缺失清单。

Usage:
  python3 write_pre_check.py --draft path/to/draft.md
  python3 write_pre_check.py --draft draft.md --tier "Pillar Page" --pack path/to/material-pack.md
"""

import argparse
import re
import sys
from pathlib import Path

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


def parse_draft(path: str) -> tuple[dict, str, str, str, str]:
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


# ── material-pack entity coverage helpers (check 13) ────────────────────
#
# Material pack entries are research support material, NOT a required-
# coverage checklist. Each entry carries a Source URL (evidence). The
# check only flags entries that BOTH have evidence AND are reasonably
# related to the article.
#
# Severity rules:
#   [search] / [library] — never blocking; warning if missing + evidence
#   [required] — blocking only if ALL four conditions met:
#     1. has evidence URL
#     2. has Quote or Key finding text
#     3. intent_relevance = supporting (i.e. related to article)
#     4. tagged as [required] in the pack
#
# No evidence → never in missing list. Generic word overlap (laser/
# pointer/construction) is supporting, NOT core — it does not make an
# entity automatically required. Intent_relevance is binary: supporting
# (related) or unrelated (skip).

# Words that describe article *form* (review/guide) and trivial article-
# task verbs. These don't make an entity "related" — they're ignored
# in token matching. Subject words like "laser"/"pointer"/"construction"
# are also too generic to single-handedly make something required.
_DOMAIN_STOP_WORDS = {
    "review", "guide", "best", "buy", "comparison", "use", "using",
    "how", "what", "why", "tips", "tip", "top", "vs",
    "price", "cheap", "expensive",
    "just", "need", "look", "way", "time",
}


def _tokenize(s: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
        if len(t) >= 3
    ]


def _entity_supporting(entity_text: str, article_intent_words: list[str]) -> bool:
    """True if the entity shares at least one meaningful word with the
    article's intent, beyond generic stopwords.

    Returns False (unrelated) when the entity is clearly about a
    different domain — telescope stargazing for a construction laser
    pointer article.
    """
    if not entity_text or not article_intent_words:
        return False
    entity_tokens = _tokenize(entity_text)
    if not entity_tokens:
        return False
    entity_lower = entity_text.lower()
    for intent_word in article_intent_words:
        if intent_word in entity_lower or intent_word in entity_tokens:
            return True
    return False


def _intent_words(title: str, h1: str, keywords: list[str]) -> list[str]:
    """Extract meaningful tokens from the article's title+H1+keywords,
    dropping meaningless stopwords so they don't match everything."""
    parts: list[str] = []
    if title:
        parts.append(title)
    if h1:
        parts.append(h1)
    for kw in keywords or []:
        parts.append(kw)
    tokens = _tokenize(" ".join(parts))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen and t not in _DOMAIN_STOP_WORDS:
            seen.add(t)
            out.append(t)
    return out


# Material-pack entry line patterns. Sub-entry lines are indented by
# 2 spaces in the canonical pack layout, so we accept any leading
# whitespace.
_PACK_ENTRY_RE = re.compile(
    r'^\s*-\s*\*\*\[(?P<tag>required|search|library)(?:\s+(?P<tag_section>[A-Z]))?\]\s*'
    r'(?P<title>[^*]+?)\*\*'
)
_PACK_SOURCE_RE = re.compile(r'^\s*-\s*Source:\s*(?P<url>.+?)\s*$')
_PACK_KEYFINDING_RE = re.compile(r'^\s*-\s*Key finding:\s*(?P<text>.+?)\s*$')
_PACK_QUOTE_RE = re.compile(r'^\s*-\s*Quote:\s*"(?P<text>.+)"\s*$')
_PACK_SUMMARY_RE = re.compile(r'^\s*-\s*Summary:\s*(?P<text>.+?)\s*$')


def parse_pack_entities(pack_path: Path) -> list[dict[str, str]]:
    """Walk a material pack and extract candidate entities with evidence.

    Each entry in the A/C/E/G sections typically looks like::

        - **[search] Laser pointer button gets pressed accidentally**
          - Source: [Reddit](https://...)
          - Quote: "..."

    We pull the entry title as the candidate entity, the Source URL as
    the evidence, and the section letter as the origin tag. Entries with
    no Source line are still returned but with empty `evidence` — the
    caller must treat them as un-sourced and exclude from the missing list.

    Returns a list of dicts with keys:
        entity, evidence, source_tag, source_section, source_quote,
        source_key_finding, source_summary
    """
    if not pack_path or not pack_path.exists():
        return []
    lines = pack_path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        m_entry = _PACK_ENTRY_RE.match(line)
        if m_entry:
            if current and current.get("entity"):
                out.append(current)
            current = {
                "entity": m_entry.group("title").strip(),
                "evidence": "",
                "source_tag": m_entry.group("tag"),
                "source_section": m_entry.group("tag_section") or "",
                "source_quote": "",
                "source_key_finding": "",
                "source_summary": "",
            }
            continue
        if current is None:
            continue
        m_src = _PACK_SOURCE_RE.match(line)
        if m_src:
            current["evidence"] = m_src.group("url").strip()
            continue
        m_kf = _PACK_KEYFINDING_RE.match(line)
        if m_kf and not current.get("source_key_finding"):
            current["source_key_finding"] = m_kf.group("text").strip()
            continue
        m_q = _PACK_QUOTE_RE.match(line)
        if m_q and not current.get("source_quote"):
            current["source_quote"] = m_q.group("text").strip()
            continue
        m_sum = _PACK_SUMMARY_RE.match(line)
        if m_sum and not current.get("source_summary"):
            current["source_summary"] = m_sum.group("text").strip()
            continue
    if current and current.get("entity"):
        out.append(current)
    return out


def _entity_present(entity: str, text_lower: str) -> bool:
    """True if the entity (or a recognizable token of it) appears in the
    draft text. We deliberately allow substring matches for multi-word
    entities and word-boundary matches for short alnum tokens so the check
    doesn't claim a term is missing when its acronym or hyphenated form
    is present."""
    if not entity:
        return False
    ent = entity.lower()
    head = re.sub(r"\s*\([^)]*\)\s*", " ", ent).strip()
    head = head.split("/")[0].strip()
    if not head:
        return False
    tokens = _tokenize(head)
    if not tokens:
        return False
    if head in text_lower:
        return True
    for tok in tokens:
        if len(tok) <= 4 and tok.isalnum():
            if not re.search(r"\b" + re.escape(tok) + r"\b", text_lower):
                return False
        else:
            if tok not in text_lower:
                return False
    return True


def check_pack_entity_coverage(
    draft_text_lower: str,
    article_intent_words: list[str],
    pack_entities: list[dict[str, str]],
) -> dict[str, object]:
    """Compare the draft to material-pack entries and return the missing
    list plus an aggregate pass/fail verdict.

    Verdict rules (severity):
      - [search]/[library]: always warning (never blocking) if related + evidence
      - [required]: blocking only if ALL FOUR conditions met:
        1. has non-empty evidence URL
        2. has a Quote or Key finding text
        3. tag is ``required``
        4. entity is related to article intent (not unrelated)
      - unrelated entities: never in missing list
      - no evidence: never in missing list
    """
    missing: list[dict[str, str]] = []
    if not pack_entities:
        return {"level": "ok", "missing": missing, "matched": [], "skipped_unsourced": 0}
    matched: list[str] = []
    skipped_unsourced = 0
    for ent in pack_entities:
        if not ent.get("evidence"):
            skipped_unsourced += 1
            continue
        if _entity_present(ent["entity"], draft_text_lower):
            matched.append(ent["entity"])
            continue
        if not _entity_supporting(ent["entity"], article_intent_words):
            continue
        tag = ent.get("source_tag", "search")
        has_required_text = bool(ent.get("source_quote") or ent.get("source_key_finding"))
        if tag == "required" and has_required_text:
            severity = "blocking"
        else:
            severity = "warning"
        missing.append({
            "entity": ent["entity"],
            "intent_relevance": "supporting",
            "evidence": ent.get("evidence", ""),
            "severity": severity,
            "source_tag": tag,
            "source_section": ent.get("source_section", ""),
            "source_quote": ent.get("source_quote", ""),
            "source_key_finding": ent.get("source_key_finding", ""),
            "source_summary": ent.get("source_summary", ""),
        })
    has_blocking = any(m["severity"] == "blocking" for m in missing)
    has_warning = any(m["severity"] == "warning" for m in missing)
    if has_blocking:
        level = "fail"
    elif has_warning:
        level = "warn"
    else:
        level = "ok"
    return {
        "level": level,
        "missing": missing,
        "matched": matched,
        "skipped_unsourced": skipped_unsourced,
    }


def run(draft: str, tier: str = '', keywords: str = '', pack: str = '') -> dict:
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
    ok('主关键词在前100词', primary and primary in first_100, first_100[:80])

    # 4. Primary keyword in 2+ H2s
    h2_hits = sum(1 for h in h2s if primary and primary in h.lower())
    ok('H2 含主关键词 ≥2次', h2_hits >= 2, f'{h2_hits} hits in {len(h2s)} H2s')

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

    # 13. 素材包实体证据覆盖（material-pack entities with evidence）
    #     No fixed entity list, no fixed coverage threshold. Each missing
    #     entry carries entity + intent_relevance + evidence + severity.
    #     The hard rule is "don't write factual claims without source
    #     evidence" — entries with no Source are NEVER in the missing list.
    pack_path = Path(pack) if pack else None
    pack_entities = parse_pack_entities(pack_path) if pack_path else []
    primary_h1 = h1.group(1).strip() if h1 else ''
    article_intent_words = _intent_words(
        meta.get('title', ''), primary_h1, kws,
    )
    cov = check_pack_entity_coverage(
        prose.lower(),
        article_intent_words,
        pack_entities,
    )
    if not pack:
        grade('warn', '关键实体覆盖（素材包缺失）',
              '未提供 material pack，跳过覆盖检查（建议在 legacy_workflow 阶段传入）')
    elif not pack_entities:
        grade('warn', '关键实体覆盖（无候选）',
              'material pack 不含 [search]/[library] 条目，无实体可对照；'
              '若素材包有 A/C/E/G 节，请确认其格式包含 ** [search/library] Title ** + Source 行')
    elif cov['level'] == 'ok':
        detail_parts = [f'已覆盖 {len(cov["matched"])} 个有证据的实体']
        if cov.get('skipped_unsourced', 0):
            detail_parts.append(
                f'跳过 {cov["skipped_unsourced"]} 个无 Source 的实体（不会进入缺失清单）'
            )
        grade('ok', '关键实体覆盖（material pack）',
              '; '.join(detail_parts))
    else:
        # warn or fail — list each missing item with its evidence and severity
        miss = cov["missing"]
        if cov['level'] == 'fail':
            cls = 'fail'
        else:
            cls = 'warn'
        # Per-missing report payload
        per_item_lines = []
        for m in miss[:8]:
            tag = m.get("source_tag", "")
            sec = m.get("source_section", "")
            src = m.get("source_section") and f"{tag}{sec}" or tag
            per_item_lines.append(
                f"  • [{m['severity']}] {m['entity']} "
                f"(intent={m['intent_relevance']}; src={src}; "
                f"evidence={m['evidence']})"
            )
        if len(miss) > 8:
            per_item_lines.append(f"  • (+{len(miss) - 8} more)")
        miss_show = '\n'.join(per_item_lines)
        msg = (
            f"关键实体覆盖（material pack）— "
            f"{len(miss)} 个有证据的 {'核心' if cls == 'fail' else '支撑'} 实体未在正文中体现"
        )
        grade(cls, msg, miss_show)
        # Always attach the structured payload so the Legacy workflow UI
        # can show per-entity detail without re-parsing the report.
        results[-1]['missing_entities'] = [
            {
                'entity': m['entity'],
                'intent_relevance': m['intent_relevance'],
                'evidence': m['evidence'],
                'severity': m['severity'],
                'source_tag': m.get('source_tag', ''),
                'source_section': m.get('source_section', ''),
            }
            for m in miss
        ]

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
        '关键实体覆盖（material pack）': (
            '正文中缺少 material pack 中已有 Source 证据且与主意图直接相关的实体。'
            '若 evidence 不足，请在素材包补充证据；'
            '若 evidence 充分，请把该实体自然写入正文。'
            '绝不要为了过检而写入无证据的术语。'
        ),
        '关键实体覆盖（素材包缺失）': (
            'W1b 阶段未把 material pack 路径传给预检。'
            '请确认 legacy_workflow 在调用 write_pre_check.py 时附带 --pack 参数。'
        ),
        '关键实体覆盖（无候选）': (
            'material pack 解析后没有 [search/library] 条目。'
            '确认 A/C/E/G 节中的条目使用「- **[search/library] 标题**」格式，'
            '并紧跟「- Source: ...」行。'
        ),
    }
    for k, v in hints.items():
        if k in item:
            return v
    return '编辑 draft 修复此项'


def render(report: dict) -> str:
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
    ap.add_argument('--pack', default='', help='Path to material pack .md (used for entity-coverage check)')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    report = run(args.draft, args.tier or '', args.keywords or '', args.pack or '')
    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    sys.exit(0 if report['fail_count'] == 0 else 1)


if __name__ == '__main__':
    main()
