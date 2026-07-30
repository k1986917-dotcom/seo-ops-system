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
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from data_sources.modules import seo_common
except ImportError:
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "seo_common",
        Path(__file__).resolve().parent / "seo_common.py",
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("cannot load local seo_common.py") from None
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

_FENCED_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```')
_JSON_LD_SCRIPT_BLOCK_RE = re.compile(
    r"<script\b[^>]*\btype\s*=\s*([\"'])application/ld\+json\1[^>]*>"
    r"[\s\S]*?</script\s*>",
    re.IGNORECASE,
)


def _strip_non_prose_blocks(text: str) -> str:
    """Remove fenced code and JSON-LD before prose-only editorial checks."""
    without_code = _FENCED_CODE_BLOCK_RE.sub('', text or '')
    return _JSON_LD_SCRIPT_BLOCK_RE.sub('', without_code)


def _flatten_markdown_for_checks(text: str) -> str:
    """Flatten links and HTML tags while preserving reader-visible text."""
    flattened = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text or '')
    return re.sub(r'<\/?[a-zA-Z][^>]*>', '', flattened)


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
    # Keep ``clean`` backward-compatible for checks that intentionally inspect
    # the complete Markdown body. Build ``prose`` from the unflattened body so
    # JSON-LD tags still exist when the full script block is removed.
    clean = _flatten_markdown_for_checks(body)
    prose = _flatten_markdown_for_checks(_strip_non_prose_blocks(body))
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


# ── Fact-check helpers (check 14) ──────────────────────────────────────

def _load_json(path: str) -> list | dict:
    """Safely load a JSON file, returning [] for missing/unparseable."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding='utf-8')
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


# ── Factual sentence extraction ─────────────────────────────────────────

_FACTUAL_SIGNAL_RE = re.compile(
    r"(?i)"
    r"(?:\d+(?:\.\d+)?\s*(?:mW|nm|W|mw|m[cm]?m?|km|V|A|Hz|%|°C|F|W/cm))|"
    r"(?:Class\s+[234][A-Z]?|FDA|IEC|ISO\s+\d+|CFR\s+\d+|ANSI|EN\s+\d+|RoHS|CE\s+mark)|"
    r"(?:(?:is|are|was|were)\s+(?:better|stronger|faster|brighter|safer|more|less)\s+"
    r"(?:than|at|up\s+to|over\s+\d+))|"
    r"(?:because|therefore|causes?|leads?\s+to|results?\s+in|due\s+to|"
    r"can\s+(?:cause|lead|result|damage|harm|produce|deliver|output)|"
    r"not\s+(?:recommended|allowed|permitted|safe|legal|compliant|sufficient))|"
    r"(?:wavelength|beam\s*divergence|output\s*power|battery\s*life|beam\s+profile|"
    r"operating\s+voltage|power\s+consumption|luminous\s+flux|optical\s+power)|"
    r"(?:\b[A-Z]{2,6}-\d{3,}\b|NDB\d{4}|NUGM\d{4}|Nichia|Osram|Sharp)"
)


def _strip_frontmatter(body: str) -> str:
    """Remove YAML frontmatter delimited by ``---``."""
    return seo_common.strip_frontmatter(body)


def _draft_sentences_set(draft_body: str) -> set[str]:
    """Return the shared normalized draft-sentence inventory."""
    if not draft_body:
        return set()
    return {
        sentence["norm"]
        for sentence in seo_common.extract_draft_sentences(draft_body)
    }


def _draft_sentence_table(draft_body: str) -> dict[str, dict[str, str]]:
    """Return the shared sentence inventory keyed by exact sentence_id."""
    if not draft_body:
        return {}
    return {
        sentence["sentence_id"]: {
            "text": sentence["text"],
            "norm": sentence["norm"],
        }
        for sentence in seo_common.extract_draft_sentences(draft_body)
    }


def _extract_factual_sentences(draft_body: str) -> list[dict]:
    """Filter reader-visible factual assertions from the shared inventory.

    Fenced examples and FAQPage JSON-LD are delivery metadata, not article
    prose. They must not create paragraph failures or evidence obligations.
    Sentence IDs for actual draft claims remain unchanged because the
    authoritative full-draft sentence table is still used for ledger binding.
    """
    if not draft_body:
        return []
    audit_body = _strip_non_prose_blocks(_strip_frontmatter(draft_body))
    out: list[dict] = []
    for entry in seo_common.extract_draft_sentences(audit_body):
        text = entry["text"]
        if len(text) < 15:
            continue
        if _FACTUAL_SIGNAL_RE.search(text):
            out.append({
                "sentence": text,
                "sentence_sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })
    return out


# ── Normalized claim matching ───────────────────────────────────────────

def _normalize(text: str) -> str:
    """Delegate to the shared canonical claim normalizer."""
    return seo_common.normalize_claim_text(text)


def _claim_in_draft(claim_text: str, draft_body: str) -> bool:
    """True when claim_text matches one shared normalized draft sentence."""
    if not claim_text or not draft_body:
        return False
    norm_ct = seo_common.normalize_claim_text(claim_text)
    if not norm_ct:
        return False
    return norm_ct in _draft_sentences_set(draft_body)


# ── Fact check (check 14) ──────────────────────────────────────────────

def _run_fact_check(
    results: list,
    grade,
    evidence_path: str,
    claim_path: str,
    material_pack_path: str,
    draft_path: str,
) -> None:
    """Check 14: Fail-closed evidence-driven fact verification.

    Each failure outputs a structured entry with claim_text, claim_type,
    reason, evidence_id, URL, and quote/kf for use in the AI revision prompt.

    All ledger fields (evidence_id, source_url, quote, key_finding,
    claim_text, claim_type, material_pack_sha256, draft_sha256, and each
    evidence_ids entry) are type-validated BEFORE any strip/slice/format
    operation.  Non-string types produce structured blocking items rather
    than AttributeError.
    """
    # Helper to emit a structured-grade entry.
    def grade_detail(level: str, msg: str, detail: str) -> None:
        grade(level, msg, detail)

    # Helper: pull a string field with type check. Returns (value, ok).
    def _str_field(
        container: dict, key: str, field_label: str, errors: list[str]
    ) -> tuple[str, bool]:
        v = container.get(key)
        if v is None:
            return "", False
        if not isinstance(v, str):
            errors.append(
                f"  • [blocking] {field_label} 类型错误：期望 str，实际 {type(v).__name__}"
            )
            return "", False
        return v, True

    # 1. Evidence-ledger exists & is valid.
    if not evidence_path or not Path(evidence_path).exists():
        grade_detail("fail", "事实校验",
                     "evidence-ledger 文件不存在: 必须先生成 evidence-ledger（运行 R3）")
        return

    try:
        ev_data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as e:
        grade_detail("fail", "事实校验", f"evidence-ledger 解析失败: {e}")
        return

    if not isinstance(ev_data, dict):
        grade_detail("fail", "事实校验",
                     f"evidence-ledger 根类型错误：期望 dict，实际 {type(ev_data).__name__}")
        return
    if ev_data.get("version") != 1:
        grade_detail("fail", "事实校验",
                     f"evidence-ledger version 不为 1: {ev_data.get('version')}")
        return

    evidence_list = ev_data.get("evidence", [])
    if not isinstance(evidence_list, list):
        grade_detail("fail", "事实校验", "evidence-ledger.evidence 不是 list")
        return

    # 2. Material pack SHA-256 matches.
    if not material_pack_path or not Path(material_pack_path).exists():
        grade_detail("fail", "事实校验", "material pack 文件不存在，无法校验 SHA")
        return

    mp_sha = hashlib.sha256(
        Path(material_pack_path).read_bytes()
    ).hexdigest()
    # Type-check material_pack_sha256 BEFORE any strip/slice.
    ev_mp_sha_raw = ev_data.get("material_pack_sha256")
    if ev_mp_sha_raw is not None and not isinstance(ev_mp_sha_raw, str):
        grade_detail(
            "fail", "事实校验",
            f"evidence-ledger.material_pack_sha256 类型错误："
            f"期望 str，实际 {type(ev_mp_sha_raw).__name__}",
        )
        return
    ev_mp_sha = ev_mp_sha_raw or ""
    if ev_mp_sha != mp_sha:
        grade_detail(
            "fail", "事实校验",
            f"material_pack_sha256 不匹配 (ledger={ev_mp_sha[:12]}..."
            f" vs file={mp_sha[:12]}...)：material pack 已被修改，请重新运行 R3",
        )
        return

    # 3. Claim-ledger exists & is valid.
    if not claim_path or not Path(claim_path).exists():
        grade_detail("fail", "事实校验",
                     "claim-ledger 文件不存在: 必须先生成 claim-ledger（运行 W0）")
        return

    try:
        cl_data = json.loads(Path(claim_path).read_text(encoding="utf-8"))
    except Exception as e:
        grade_detail("fail", "事实校验", f"claim-ledger 解析失败: {e}")
        return

    if not isinstance(cl_data, dict):
        grade_detail("fail", "事实校验",
                     f"claim-ledger 根类型错误：期望 dict，实际 {type(cl_data).__name__}")
        return
    if cl_data.get("version") != 1:
        grade_detail("fail", "事实校验",
                     f"claim-ledger version 不为 1: {cl_data.get('version')}")
        return

    claims = cl_data.get("claims", [])
    if not isinstance(claims, list):
        grade_detail("fail", "事实校验", "claim-ledger.claims 不是 list")
        return

    # 4. Draft SHA matches.
    if not draft_path or not Path(draft_path).exists():
        grade_detail("fail", "事实校验", "draft 文件不存在")
        return

    draft_text = Path(draft_path).read_text(encoding="utf-8")
    # Only the article body (before ===CLAIM_LEDGER===) is hashed.
    draft_body = draft_text.split("===CLAIM_LEDGER===")[0].strip()
    actual_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
    # Type-check draft_sha256 BEFORE any strip/slice.
    expected_sha_raw = cl_data.get("draft_sha256")
    if expected_sha_raw is not None and not isinstance(expected_sha_raw, str):
        grade_detail(
            "fail", "事实校验",
            f"claim-ledger.draft_sha256 类型错误："
            f"期望 str，实际 {type(expected_sha_raw).__name__}",
        )
        return
    expected_sha = expected_sha_raw or ""
    if not expected_sha or actual_sha != expected_sha:
        grade_detail(
            "fail", "事实校验",
            f"claim-ledger draft_sha256 不匹配: ledger={expected_sha[:12]}..."
            f" actual={actual_sha[:12]}...；草稿已被修改，请重新运行 W0",
        )
        return

    # 5. Build evidence index and check for ID collisions.
    blocking_items: list[str] = []
    fact_issues: list[dict[str, str]] = []
    ev_map: dict[str, dict] = {}
    seen_ids: set[str] = set()
    for idx, ev in enumerate(evidence_list):
        if not isinstance(ev, dict):
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] 不是 dict，而是 {type(ev).__name__}"
            )
            continue
        # evidence_id: must be a non-empty str. null / missing / empty /
        # non-str all block — even if no claim references this evidence.
        if "evidence_id" not in ev:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] 缺 evidence_id 字段"
            )
            continue
        eid_raw = ev["evidence_id"]
        if eid_raw is None:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}].evidence_id 是 null"
            )
            continue
        if not isinstance(eid_raw, str):
            blocking_items.append(
                f"  • [blocking] evidence[{idx}].evidence_id 类型错误："
                f"期望 str，实际 {type(eid_raw).__name__}"
            )
            continue
        eid = eid_raw.strip()
        if not eid:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}].evidence_id 是空字符串"
            )
            continue
        if eid in seen_ids:
            blocking_items.append(
                f"  • [blocking] evidence-ledger 含重复 evidence_id={eid}"
            )
            continue
        # source_url: must be a non-empty str. null / missing / empty /
        # non-str all block — even if no claim references this evidence.
        if "source_url" not in ev:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) 缺 source_url 字段"
            )
            continue
        url_raw = ev["source_url"]
        if url_raw is None:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) source_url 是 null"
            )
            continue
        if not isinstance(url_raw, str):
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) source_url 类型错误："
                f"期望 str，实际 {type(url_raw).__name__}"
            )
            continue
        if not url_raw.strip():
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) source_url 是空字符串"
            )
            continue
        # quote / key_finding: must be str (None allowed); at least one
        # non-empty.
        quote_raw = ev.get("quote")
        kf_raw = ev.get("key_finding")
        if quote_raw is not None and not isinstance(quote_raw, str):
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) quote 类型错误："
                f"期望 str，实际 {type(quote_raw).__name__}"
            )
            continue
        if kf_raw is not None and not isinstance(kf_raw, str):
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) key_finding 类型错误："
                f"期望 str，实际 {type(kf_raw).__name__}"
            )
            continue
        quote_ok = isinstance(quote_raw, str) and bool(quote_raw.strip())
        kf_ok = isinstance(kf_raw, str) and bool(kf_raw.strip())
        if not quote_ok and not kf_ok:
            blocking_items.append(
                f"  • [blocking] evidence[{idx}] (id={eid}) 无 quote 也无 key_finding"
            )
            continue
        seen_ids.add(eid)
        ev_map[eid] = ev

    # Build authoritative sentence table from the current draft body.
    claim_sentence_table = _draft_sentence_table(draft_body)

    # Determine ledger mode: if ANY claim has a ``sentence_id`` field the
    # entire ledger is processed in strict mode (every claim must carry a
    # valid ID).  Only when NO claim has the field does legacy mode apply.
    strict_sentence_id_mode = any(
        isinstance(c, dict) and "sentence_id" in c for c in claims
    )

    # 6. Validate each claim.
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            blocking_items.append(
                f"  • [blocking] claims[{idx}] 不是 dict，而是 {type(claim).__name__}"
            )
            continue

        # Type-check claim_text BEFORE strip/slice.
        ct_raw = claim.get("claim_text")
        if ct_raw is None:
            blocking_items.append(f"  • [blocking] claims[{idx}].claim_text 为空")
            continue
        if not isinstance(ct_raw, str):
            blocking_items.append(
                f"  • [blocking] claims[{idx}].claim_text 类型错误："
                f"期望 str，实际 {type(ct_raw).__name__}"
            )
            continue
        ct = ct_raw.strip()
        if not ct:
            blocking_items.append(f"  • [blocking] claims[{idx}].claim_text 为空")
            continue

        # Type-check claim_type BEFORE strip/slice.
        ctype_raw = claim.get("claim_type")
        if ctype_raw is None:
            blocking_items.append(
                f"  • [blocking] claim '{ct[:40]}' 缺 claim_type"
            )
            continue
        if not isinstance(ctype_raw, str):
            blocking_items.append(
                f"  • [blocking] claims[{idx}].claim_type 类型错误："
                f"期望 str，实际 {type(ctype_raw).__name__}"
            )
            continue
        ctype = ctype_raw.strip()
        if not ctype:
            blocking_items.append(
                f"  • [blocking] claim '{ct[:40]}' 缺 claim_type"
            )
            continue

        eids = claim.get("evidence_ids")
        if not isinstance(eids, list):
            blocking_items.append(
                f"  • [blocking] claims[{idx}].evidence_ids 不是 list，而是 {type(eids).__name__}"
            )
            continue

        # ── Strict sentence_id mode (when at least one claim has the field) ──
        if strict_sentence_id_mode:
            if "sentence_id" not in claim:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}] 缺 sentence_id 字段"
                )
                continue
            sid_raw = claim["sentence_id"]
            if sid_raw is None:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id 是 null"
                )
                continue
            if not isinstance(sid_raw, str):
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id 类型错误："
                    f"期望 str，实际 {type(sid_raw).__name__}"
                )
                continue
            if sid_raw == "":
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id 是空字符串"
                )
                continue
            if not sid_raw.strip():
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id 是纯空白字符串"
                )
                continue
            if sid_raw != sid_raw.strip():
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id 含首尾空白"
                )
                continue
            sent_info = claim_sentence_table.get(sid_raw)
            if sent_info is None:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id '{sid_raw}' "
                    f"在当前草稿中不存在"
                )
                continue
            # Exact match between the raw claim_text and the server sentence.
            if ct_raw != sent_info["text"]:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id '{sid_raw}' "
                    f"的 claim_text 与草稿原句不一致"
                )
                continue
            if _normalize(ct_raw) != sent_info["norm"]:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].sentence_id '{sid_raw}' "
                    f"规范化后与草稿句子不匹配"
                )
                continue
        else:
            # ── Legacy mode (no sentence_id in any claim) ──
            if not _claim_in_draft(ct_raw, draft_body):
                blocking_items.append(
                    f"  • [blocking] claim_text 不在草稿正文中: '{ct[:60]}'"
                )
                continue

        if not eids:
            blocking_items.append(
                f"  • [blocking] claim '{ct[:40]}' 缺 evidence_ids"
            )
            continue

        for eid_idx, eid in enumerate(eids):
            # Type-check each evidence_ids entry BEFORE using.
            if not isinstance(eid, str):
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].evidence_ids[{eid_idx}] 类型错误："
                    f"期望 str，实际 {type(eid).__name__}"
                )
                continue
            if not eid:
                blocking_items.append(
                    f"  • [blocking] claims[{idx}].evidence_ids[{eid_idx}] 是空字符串"
                )
                continue
            ev = ev_map.get(eid)
            if not ev:
                blocking_items.append(
                    f"  • [blocking] claim '{ct[:40]}' 引用了不存在的 "
                    f"evidence_id={eid}"
                )
                continue
            # Type-check source_url BEFORE strip/slice.
            url_raw = ev.get("source_url")
            if url_raw is None:
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"缺 source_url"
                )
                continue
            if not isinstance(url_raw, str):
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"source_url 类型错误：期望 str，实际 {type(url_raw).__name__}"
                )
                continue
            url = url_raw.strip()
            if not url:
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"缺 source_url"
                )
                continue
            # Type-check quote and key_finding BEFORE strip/slice.
            quote_raw = ev.get("quote")
            kf_raw = ev.get("key_finding")
            if quote_raw is not None and not isinstance(quote_raw, str):
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"quote 类型错误：期望 str，实际 {type(quote_raw).__name__}"
                )
                continue
            if kf_raw is not None and not isinstance(kf_raw, str):
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"key_finding 类型错误：期望 str，实际 {type(kf_raw).__name__}"
                )
                continue
            quote = (quote_raw or "").strip()
            kf = (kf_raw or "").strip()
            if not quote and not kf:
                blocking_items.append(
                    f"  • [blocking] evidence_id={eid} (claim: {ct[:40]}) "
                    f"无 quote 也无 key_finding"
                )
                continue

    # 7. Extract factual sentences from draft and check coverage.
    extracted = _extract_factual_sentences(draft_body)
    claimed_texts = {
        _normalize(c.get("claim_text", ""))
        for c in claims
        if isinstance(c, dict)
        and isinstance(c.get("claim_text"), str)
        and c.get("claim_text")
    }
    for cand in extracted:
        norm = _normalize(cand["sentence"])
        if norm and norm not in claimed_texts:
            fact_issues.append({
                "reason": "uncovered_factual_sentence",
                "sentence": cand["sentence"],
                "sentence_sha": cand["sentence_sha"],
            })
            blocking_items.append(
                f"  • [blocking] 文章含事实句但 ledger 未覆盖: "
                f"'{cand['sentence'][:80]}'"
            )

    # 8. Verdict.
    if not blocking_items:
        grade_detail("ok", "事实校验",
                     f"全部 {len(claims)} 个 claim 通过 evidence 验证")
    else:
        detail_text = "\n".join(blocking_items)
        grade_detail("fail", "事实校验",
                     f"{len(blocking_items)} 个事实校验问题:\n{detail_text}")
    if fact_issues:
        results[-1]["fact_issues"] = fact_issues


def run(draft: str, tier: str = '', keywords: str = '', pack: str = '',
        evidence_ledger: str = '', claim_ledger: str = '',
        material_pack: str = '') -> dict:
    meta, body, clean, prose, raw = parse_draft(draft)
    results = []
    tier = tier or meta.get('page type', meta.get('tier', ''))
    if not tier:
        tier = 'Pillar Page' if len(meta.get('title', '').split()) < 4 else 'Cluster Content'
    kws = [k.strip().lower() for k in (keywords or meta.get('seo keywords', '')).split(',') if k.strip()]
    primary = kws[0] if kws else meta.get('title', '').lower()
    wc = word_count(clean)
    wc_prose = word_count(prose)
    h1 = re.search(r'^# (.+)', body, re.MULTILINE)
    h2s = re.findall(r'^## (.+)', body, re.MULTILINE)
    # Paragraph style applies to reader-visible prose, not fenced examples or
    # JSON-LD delivery metadata.
    paragraphs = [
        p.strip() for p in re.split(r'\n\n+', prose)
        if len(p.split()) > 20
    ]
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

    # 14. Claim-ledger fact check (evidence-driven)
    _run_fact_check(results, grade, evidence_ledger, claim_ledger,
                    material_pack, draft)

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
    ap.add_argument('--evidence-ledger', default='', help='Path to evidence-ledger JSON')
    ap.add_argument('--claim-ledger', default='', help='Path to claim-ledger JSON')
    ap.add_argument('--material-pack', default='', help='Path to material pack (for SHA check)')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    report = run(args.draft, args.tier or '', args.keywords or '',
                 args.pack or '', args.evidence_ledger or '',
                 args.claim_ledger or '', args.material_pack or '')
    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    sys.exit(0 if report['fail_count'] == 0 else 1)


if __name__ == '__main__':
    main()
