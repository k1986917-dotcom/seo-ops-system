"""Legacy Research + Write workflow service.

Wraps the frozen old scripts in data_sources/modules/ following the old
research/SKILL.md and write/SKILL.md instructions.

Stages (file-system state machine, advanced by actions.legacy_stage):
  r0_pending  → r0_prompt → r1_results → r2_collect → r3_ai_analyze
  → r4_score → r5_write_ready → w0_validate → w1_draft
  → w1b_pre_check → w2_post_process → w3_register

The old scripts resolve their workspace as <SEO_SITES_DIR>/<website>. This
service sets SEO_SITES_DIR to data/legacy_workflow so they read and write the
seo-ops workspace directly — the old project at /home/laoma/seo-workflow is
never touched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from data_sources.modules import seo_common

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LEGACY_MODULES_DIR = PROJECT_ROOT / "data_sources" / "modules"
WEBSITE = "laserpointerhub"

SCRIPT_TIMEOUT_SECONDS = 300

# Prompt input budgets. The old skill fed whole files to a chat agent; here the
# limits are explicit so a long material pack does not silently lose Part 3.
_PACK_CHAR_LIMIT = 40000
_RESEARCH_DATA_CHAR_LIMIT = 40000
_CONTEXT_CHAR_LIMIT = 6000
_REPORT_CHAR_LIMIT = 12000
_OLD_ARTICLE_CHAR_LIMIT = 8000

# write/SKILL.md 段2: "最多 2 轮"
MAX_REVISION_ROUNDS = 2


# ── Utilities ──────────────────────────────────────────────────────────

def _slugify(topic: str) -> str:
    return seo_common.slugify(topic)


def _today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _latest_file(glob_pattern: str, directory: Path) -> Path | None:
    candidates = sorted(
        directory.glob(glob_pattern),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_text(path: Path | str | None, limit: int | None = None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit] if limit else text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_dir(workspace: Path, action_id: int) -> Path:
    """Per-action directory under workspace/runs/.

    Each action has exactly one persistent workspace; reopening or
    restarting the service returns the same path. State (search-prompt,
    material pack, draft, w2 state, reports) lives here and persists
    on disk across restarts; the database stage (actions.legacy_stage)
    provides the floor of progress.
    """
    if action_id <= 0:
        raise ValueError("action_id must be positive")
    return Path(workspace) / "runs" / f"action-{action_id}"


def _ensure_action_workspace(workspace: Path, action_id: int) -> Path:
    """Return the persistent workspace for an action, creating it if needed.

    Layout under <workspace>/runs/action-<id>/current/laserpointerhub/:
      - research/, material-packs/, drafts/, reports/  (private, action-only)
      - context/, published/, products/                 (symlinks to shared)
    """
    workspace = Path(workspace)
    action_dir = _action_dir(workspace, action_id)
    run_workspace = action_dir / "current" / WEBSITE

    # Private directories: create if missing, leave existing files in place.
    for name in ("research", "material-packs", "drafts", "reports"):
        (run_workspace / name).mkdir(parents=True, exist_ok=True)

    # Shared snapshots stay under the parent workspace and are symlinked in.
    for name in ("context", "published", "products"):
        shared = (workspace / name).resolve()
        shared.mkdir(parents=True, exist_ok=True)
        link_path = run_workspace / name
        if link_path.is_symlink() or link_path.exists():
            # Replace stale link so a target rename is reflected.
            if link_path.is_symlink() or link_path.is_dir():
                try:
                    link_path.unlink()
                except IsADirectoryError:
                    pass
        link_path.symlink_to(shared, target_is_directory=True)
    return run_workspace


def action_workspace(workspace: Path, action_id: int) -> Path:
    """Resolve the persistent workspace for an action (creates it lazily)."""
    return _ensure_action_workspace(workspace, action_id)


# Stage order used for invalidation. Earlier stages invalidate all later ones.
# Each entry is a glob relative to the action workspace.
STAGE_FILES: dict[str, tuple[str, ...]] = {
    "r0": ("research/search-prompt-{slug}-*.md",
           "research/topic-context-{slug}.json"),
    "r1": ("research/search-results-{slug}-*.md",
           "research/research-data-{slug}-*.md",
           "research/research-data-{slug}-*.json"),
    "r3": ("research/research-score-{slug}-*.md",
           "research/brief-{slug}-*.md",
           "research/backlink-suggestions-{slug}-*.md",
           "material-packs/{slug}-*.md"),
    "w0": ("drafts/{slug}-*.md",
           "reports/w2-state-{slug}.json",
           "reports/pre-check-{slug}-*.md",
           "reports/post-process-{slug}-*.md"),
    "w1b": ("reports/pre-check-{slug}-*.md",
            "reports/post-process-{slug}-*.md"),
    "w2": ("reports/post-process-{slug}-*.md",),
    "w3": ("reports/register-{slug}-*.md",
           "research/backlink-suggestions-{slug}-*.md"),
}

# Stage order for invalidation lookups.
STAGE_ORDER_FOR_CLEAR = ["r0", "r1", "r3", "w0", "w1b", "w2", "w3"]


def _stage_globs_from(stage_key: str) -> list[str]:
    """Return all glob patterns for stages at or after stage_key."""
    if stage_key not in STAGE_ORDER_FOR_CLEAR:
        return []
    idx = STAGE_ORDER_FOR_CLEAR.index(stage_key)
    out: list[str] = []
    for s in STAGE_ORDER_FOR_CLEAR[idx:]:
        out.extend(STAGE_FILES[s])
    return out


def clear_stage_artifacts(workspace: Path, slug: str, after_stage: str) -> int:
    """Remove all artifacts produced at or after `after_stage` for this slug.

    Returns the number of files removed. Used when re-running a stage so a
    downstream success cannot masquerade as the new run's result.
    """
    removed = 0
    for pattern in _stage_globs_from(after_stage):
        concrete = pattern.format(slug=slug)
        for path in workspace.glob(concrete):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def clear_all_action_artifacts(workspace: Path, slug: str) -> int:
    """Wipe every stage artifact for this slug (full reset before R0)."""
    return clear_stage_artifacts(workspace, slug, "r0")


# ── Report persistence ─────────────────────────────────────────────────
#
# Every script run keeps its full stdout on disk. Routes redirect after a POST,
# so anything held only in memory is lost before the operator can read it.

def _reports_dir(workspace: Path) -> Path:
    d = workspace / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_report(workspace: Path, kind: str, slug: str, content: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = (
        _reports_dir(workspace)
        / f"{kind}-{slug}-{_today_str()}-{timestamp}-{uuid4().hex[:8]}.md"
    )
    path.write_text(content, encoding="utf-8")
    return path


def load_report(workspace: Path, kind: str, slug: str) -> str:
    latest = _latest_file(f"reports/{kind}-{slug}-*.md", workspace)
    return _read_text(latest)


def _w2_state_path(workspace: Path, slug: str) -> Path:
    return _reports_dir(workspace) / f"w2-state-{slug}.json"


def load_w2_state(workspace: Path, slug: str) -> dict[str, Any]:
    raw = _read_text(_w2_state_path(workspace, slug))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"rounds": 0, "gate_passed": False, "applied": False}


def save_w2_state(workspace: Path, slug: str, state: dict[str, Any]) -> None:
    _w2_state_path(workspace, slug).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Stage Detection ────────────────────────────────────────────────────

STAGE_ORDER = [
    "r0_pending", "r0_prompt", "r1_results", "r2_collect",
    "r3_ai_analyze", "r4_score", "r5_write_ready",
    "w0_validate", "w1_draft", "w1b_pre_check",
    "w2_post_process", "w3_register",
]

STAGE_NAMES = {
    "r0_pending":    "尚未开始",
    "r0_prompt":     "搜索提示词已生成",
    "r1_results":    "搜索结果已保存",
    "r2_collect":    "数据已收集，等待 AI 分析",
    "r3_ai_analyze": "素材包不完整，等待 AI 分析",
    "r4_score":      "素材包完成，等待评分",
    "r5_write_ready":"Research 完成，等待写作",
    "w0_validate":   "素材包已校验，等待生成草稿",
    "w1_draft":      "草稿已生成，等待预检",
    "w1b_pre_check": "预检完成，等待后处理",
    "w2_post_process":"后处理通过，等待注册",
    "w3_register":   "已注册，全部完成",
}

# Files a stage cannot exist without. Guards against a stale legacy_stage in
# the database claiming progress whose artifacts have since been removed.
_STAGE_REQUIRES = {
    "r0_prompt": "search_prompt",
    "r1_results": "search_results",
    "r2_collect": "research_data",
    "r3_ai_analyze": "material_pack",
    "r4_score": "material_pack",
    "r5_write_ready": "material_pack",
    "w0_validate": "material_pack",
    "w1_draft": "draft",
    "w1b_pre_check": "draft",
    "w2_post_process": "draft",
    "w3_register": "draft",
}


def _collect_files(topic: str, workspace: Path) -> dict[str, Any]:
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    rd = _latest_file(f"research/research-data-{slug}-*.md", workspace)
    sr = _latest_file(f"research/search-results-{slug}-*.md", workspace)
    sp = _latest_file(f"research/search-prompt-{slug}-*.md", workspace)
    rs = _latest_file(f"research/research-score-{slug}-*.md", workspace)
    br = _latest_file(f"research/brief-{slug}-*.md", workspace)
    bl = _latest_file(f"research/backlink-suggestions-{slug}-*.md", workspace)
    return {
        "slug": slug, "today": _today_str(),
        "search_prompt": str(sp) if sp else None,
        "search_results": str(sr) if sr else None,
        "research_data": str(rd) if rd else None,
        "research_score": str(rs) if rs else None,
        "brief": str(br) if br else None,
        "material_pack": str(mp) if mp else None,
        "draft": str(draft) if draft else None,
        "backlinks": str(bl) if bl else None,
    }


def _detect_file_stage(files: dict[str, Any]) -> str:
    """Stage implied purely by which artifacts exist on disk."""

    if files["draft"]:
        # register appends this heading to the draft itself, so its presence is
        # exact evidence that 段3 ran. (The old check looked for the 内链: field,
        # which post-process --apply writes long before register.)
        if "## 回溯链接候选" in _read_text(files["draft"]):
            return "w3_register"
        return "w1_draft"

    if files["material_pack"]:
        if "Part 3" in _read_text(files["material_pack"]):
            return "r5_write_ready" if files["research_score"] else "r4_score"
        return "r3_ai_analyze"

    if files["research_data"]:
        return "r2_collect"
    if files["search_results"]:
        return "r1_results"
    if files["search_prompt"]:
        return "r0_prompt"
    return "r0_pending"


def detect_stage(topic: str, workspace: Path,
                 db_stage: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return (stage_key, files_info).

    The on-disk artifacts set the floor. `db_stage` (actions.legacy_stage) can
    push past it — pre-check and post-process produce no new artifact of their
    own, so without it the UI can never leave w1_draft — but only when the
    artifacts that stage depends on are still present.
    """

    files = _collect_files(topic, workspace)
    file_stage = _detect_file_stage(files)

    if db_stage and db_stage in STAGE_ORDER:
        if STAGE_ORDER.index(db_stage) > STAGE_ORDER.index(file_stage):
            required = _STAGE_REQUIRES.get(db_stage)
            if not required or files.get(required):
                return db_stage, files

    return file_stage, files


def stage_label(stage_key: str) -> str:
    return STAGE_NAMES.get(stage_key, stage_key)


def stage_step(stage_key: str) -> int:
    try:
        return STAGE_ORDER.index(stage_key)
    except ValueError:
        return 0


# ── Enriched Topic-Context from Research Data ──────────────────────────

def generate_topic_context_from_research(topic: str, workspace: Path,
                                          opportunity_evidence: dict | None = None,
                                          action_id: int | None = None) -> dict:
    """Create an enriched topic-context.json from the new system's research data.

    This replaces the bare heuristic topic-context the old script would generate,
    giving the Legacy Research AI richer signals than source=heuristic.
    """

    slug = _slugify(topic)
    today = _today_str()

    ctx = {
        "slug": slug,
        "topic": topic,
        "source": "research",
        "intent": "混合型",
        "tier": "Cluster Content",
        "primary_keyword": topic,
        "cluster": "",
        "cannibal_risk": "",
        "signals": {},
        "guidance": "",
        "updated": today,
    }

    if not opportunity_evidence or not action_id:
        # Fallback: heuristic
        ctx["source"] = "heuristic"
        ctx["intent"] = _detect_intent(topic)
        ctx["tier"] = _detect_tier(topic)
    else:
        # Use research data to enrich
        intent = (opportunity_evidence.get("intent") or "").strip()
        if intent:
            ctx["intent"] = intent
        ctx["tier"] = _detect_tier(topic)

        facts = opportunity_evidence.get("facts") or []
        if facts:
            ctx["signals"]["facts"] = len(facts)
            ctx["guidance"] = "; ".join(str(f) for f in facts[:3])[:300]

        inference = (opportunity_evidence.get("program_inference") or {})
        if isinstance(inference, dict):
            research_inf = inference.get("research_inference") or []
            overlap = inference.get("content_overlap") or {}
            ctx["signals"]["inference_count"] = len(research_inf) if isinstance(research_inf, list) else 0
            if isinstance(overlap, dict):
                ctx["cannibal_risk"] = str(overlap.get("risk") or overlap.get("relationship") or "")

        source_urls = opportunity_evidence.get("source_urls") or []
        if isinstance(source_urls, list):
            ctx["signals"]["source_count"] = len(source_urls)

        limitations = opportunity_evidence.get("limitations") or ""
        if limitations and str(limitations).strip():
            ctx["guidance"] = (ctx.get("guidance", "") + " | 局限: " + str(limitations))[:500]

        # Try to get richer data from research_candidates table
        candidate_id = opportunity_evidence.get("research_candidate_id")
        if candidate_id:
            try:
                from seo_ops.db import connection as db_conn
                with db_conn() as conn:
                    rc = conn.execute(
                        """SELECT rationale, qualification_status, recommended_disposition,
                                  closest_existing_json, evidence_demand_json,
                                  evidence_gap_json, evidence_material_json
                           FROM research_candidates WHERE id = ?""",
                        (int(candidate_id),),
                    ).fetchone()
                    if rc:
                        rc = dict(rc)
                        ctx["signals"]["qualification"] = rc.get("qualification_status") or ""
                        ctx["signals"]["disposition"] = rc.get("recommended_disposition") or ""
                        rationale = rc.get("rationale") or ""
                        if rationale and str(rationale).strip():
                            ctx["guidance"] = (ctx.get("guidance", "") + " | " + str(rationale))[:500]

                        closest = rc.get("closest_existing_json") or ""
                        if closest:
                            try:
                                closest_obj = json.loads(str(closest)) if isinstance(closest, str) else closest
                                if isinstance(closest_obj, dict):
                                    ctx["cannibal_risk"] = ("最接近旧文: " +
                                        str(closest_obj.get("title") or closest_obj.get("url") or "") +
                                        " (关系: " + str(closest_obj.get("relationship") or "?") + ")")
                            except Exception:
                                pass
            except Exception:
                pass

    # Write to workspace
    tc_path = workspace / "research" / f"topic-context-{slug}.json"
    tc_path.parent.mkdir(parents=True, exist_ok=True)
    tc_path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    return ctx


def read_topic_context(topic: str, workspace: Path) -> dict[str, Any]:
    raw = _read_text(workspace / "research" / f"topic-context-{_slugify(topic)}.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_tier(topic: str, workspace: Path, submitted: str = "") -> str:
    """Operator choice wins; otherwise inherit the tier RESEARCH already decided."""

    if submitted and submitted.strip():
        return submitted.strip()
    ctx_tier = str(read_topic_context(topic, workspace).get("tier") or "").strip()
    return ctx_tier or _detect_tier(topic)


def _detect_intent(topic: str) -> str:
    topic_lower = topic.lower()
    if any(w in topic_lower for w in {"best", "buy", "review", "vs", "comparison", "top", "price", "budget", "cheap", "under", "roundup"}):
        return "转化型"
    if any(w in topic_lower for w in {"how", "what", "why", "guide", "safe", "use", "work", "mean", "does"}):
        return "信息型"
    return "混合型"


def _detect_tier(topic: str) -> str:
    topic_lower = topic.lower()
    if any(w in topic_lower for w in {"best", "top", "review", "vs", "comparison", "roundup"}):
        return "Product Roundup"
    if len(topic.split()) <= 2:
        return "Pillar Page"
    return "Cluster Content"


# ── Legacy Runner (subprocess wrapper) ──────────────────────────────────

class LegacyRunner:
    """Runs the frozen old scripts against the seo-ops Legacy workspace."""

    def __init__(self, workspace: Path, website: str = WEBSITE):
        self.workspace = Path(workspace)
        self.website = website

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # The scripts resolve <SEO_SITES_DIR>/<website>; the workspace itself is
        # .../legacy_workflow/laserpointerhub, so hand them its parent. Child
        # processes the scripts spawn (content_scorer, plan_feedback) inherit it.
        env["SEO_SITES_DIR"] = str(self.workspace.resolve().parent)
        return env

    def run_sync(self, script_name: str, args: list[str]) -> tuple[str, str, int]:
        """Run an old script; return (stdout, stderr, exit_code)."""
        cmd = [sys.executable, str(LEGACY_MODULES_DIR / script_name), *args]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=SCRIPT_TIMEOUT_SECONDS,
                cwd=str(PROJECT_ROOT), env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return "", f"脚本超时（>{SCRIPT_TIMEOUT_SECONDS}s）: {script_name}", 124
        return result.stdout or "", result.stderr or "", result.returncode

    async def run(self, script_name: str, args: list[str]) -> tuple[str, str, int]:
        """Async wrapper — keeps the event loop free while a script runs."""
        return await asyncio.to_thread(self.run_sync, script_name, args)


def _combined_output(stdout: str, stderr: str) -> str:
    if stderr.strip():
        return f"{stdout}\n\n---\n### 脚本日志 (stderr)\n```\n{stderr.strip()}\n```"
    return stdout


# ── AI helper ───────────────────────────────────────────────────────────

async def _run_ai_text(purpose: str, system: str, user: str, *,
                       settings=None, max_tokens: int | None = None) -> str:
    from seo_ops.config import get_settings
    from seo_ops.services.ai import build_ai_provider, complete_text_logged

    active = settings or get_settings()
    if not active.ai_enabled:
        raise RuntimeError("AI 未配置，请在设置页配置 AI")

    provider = build_ai_provider(active)
    return await complete_text_logged(
        provider,
        purpose=purpose,
        system_prompt=system,
        user_prompt=user,
        max_tokens=max_tokens,
        settings=active,
    )


# ── R0: Generate Search Prompt ──────────────────────────────────────────

def _build_search_prompt(topic: str, workspace: Path) -> str:
    """Generate an 8-section search prompt using the old format, fed from workspace data.

    Section 3 replaces old "Market Data" with "Common Misconceptions and
    Real-World Lessons" to avoid AI-fabricated price/trend content.
    """

    # ── Read synced GSC data ──
    seo_path = workspace / "context" / "seo-data-manual.md"
    seo_text = seo_path.read_text(encoding="utf-8") if seo_path.exists() else ""
    gsc_kws: list[str] = []
    for m in re.finditer(r'\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([\d,]+)', seo_text):
        kw = m.group(1).strip()
        try:
            if int(m.group(2).replace(",", "")) > 100:
                gsc_kws.append(kw)
        except ValueError:
            pass

    # ── Read published index ──
    pub_path = workspace / "published" / "published-index.json"
    pub_titles: list[str] = []
    pub_tags: set[str] = set()
    if pub_path.exists():
        try:
            data = json.loads(pub_path.read_text(encoding="utf-8"))
            for entry in (data if isinstance(data, list) else []):
                pub_titles.append((entry.get("title") or "")[:80])
                for t in (entry.get("tags") or []):
                    pub_tags.add(t.lower())
        except Exception:
            pass

    # ── Read library summaries (so search AI knows what we already have) ──
    def _lib_summary(lib_path: Path, label: str) -> str:
        if not lib_path.exists():
            return ""
        text = lib_path.read_text(encoding="utf-8")
        entries = re.findall(r'\n## (.+)', text)
        skip = {"标签索引", "填写规则", "外链库", "常见权威", "索引", "规则"}
        real = [e.strip() for e in entries if not any(s in e.lower() for s in skip)]
        if not real:
            return ""
        return f"### Existing {label} ({len(real)} total)\n" + "\n".join(
            f"- {e[:60]}" for e in real[:5])

    pain_summ = _lib_summary(workspace / "context" / "pain-points-library.md", "User Pain Points")
    case_summ = _lib_summary(workspace / "context" / "case-studies-library.md", "Case Studies")

    def _source_summ() -> str:
        p = workspace / "context" / "external-sources-library.md"
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        urls = re.findall(r'\|\s*\d+\s*\|\s*(https?://\S+)', text)
        samples = re.findall(
            r'\|\s*(\d+)\s*\|\s*(https?://\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+)\s*\|', text)
        if not urls:
            return ""
        lines = [f"### Existing Authoritative Citations ({len(urls)} total)"]
        for s in samples[:3]:
            lines.append(f"- [{s[2]}] {s[3].strip()[:50]}")
        return "\n".join(lines)

    source_summ = _source_summ()

    # ── Intent detection ──
    topic_lower = topic.lower()
    commercial = {"best", "buy", "review", "vs", "comparison", "top", "price", "budget", "cheap",
                  "under", "roundup"}
    informational = {"how", "what", "why", "guide", "safe", "use", "work", "mean", "does"}
    if any(w in topic_lower for w in commercial):
        intent = "转化型"
    elif any(w in topic_lower for w in informational):
        intent = "信息型"
    else:
        intent = "混合型"

    # ── What We Know ──
    know: list[str] = []
    if gsc_kws:
        know.append(f"GSC keywords we rank for: {', '.join(gsc_kws[:5])}")
    if pub_titles:
        know.append(
            f"Published articles: {len(pub_titles)} total. "
            f"Topics include: {', '.join(pub_titles[:5])}")
    if pub_tags:
        know.append(f"Content tags already covered: {', '.join(sorted(pub_tags)[:10])}")

    # ── What We Need ──
    need: list[str] = []
    if not gsc_kws:
        need.append("No GSC ranking data — need SERP analysis to understand what ranks.")
    else:
        need.append("GSC shows search demand. Find what TOPIC ANGLE differentiates from top 5.")
    need.append("Focus on information gaps — what the top 5 articles do NOT cover.")
    if intent == "转化型":
        need.append("Commercial article — prioritize spec comparisons, buyer decision factors, "
                     "and red flags. Pricing data from search AI may be inaccurate; "
                     "focus on product capabilities, not market pricing.")
    else:
        need.append("Informational article — prioritize real user pain points, practical how-to, "
                     "authority sources.")

    # ── BUILD ──
    return f"""You are helping me research for an SEO article about "{topic}" on laserpointerhub.com.
I need comprehensive, real, verifiable data.
Do NOT fabricate anything — say "not found" if you cannot find it.

## What We Already Know
{chr(10).join(know)}

## What We Need You To Find
{chr(10).join(need)}

Search intent: {intent}

---

## Section 1: SERP Analysis

Search for "{topic}" and analyze the top 5 ranking pages.

| # | URL | Title | Est. Words | Content Type | H2 Sections |
|---|-----|-------|-----------|--------------|-------------|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

After the table, answer:
- Common H2 topics ALL top 5 cover (must-have — we CANNOT skip these):
- Topics only 1-2 cover (differentiation opportunities for us):
- Topics NO ONE covers (our unique angle — THIS is where we win):
- Average word count of top 5:
- SERP composition (brand blogs vs independent reviews vs forums vs gov sites):

---

## Section 2: User Pain Points

Find 8-10 REAL frustrations/complaints/questions about {topic}.
Search Reddit, Quora, forums, Amazon reviews.

{pain_summ if pain_summ else 'Important: We have a pain point library. Find NEW, different pain points — NOT the same ones we already know about.'}

Format each as:
- Pain point: [in user's own language]
- Source: [URL]
- Quote: "[actual user words]"

Prioritize specific, emotional complaints with actual numbers/experiences.

---

## Section 3: Common Misconceptions and Real-World Lessons

Find misconceptions, myths, and hard-learned lessons about {topic} from forums,
Reddit, comments, and review sections. This is NOT about market data or pricing.

Focus on:
- What beginners consistently get WRONG
- Dangerous or costly mistakes people make (with real stories)
- Advice from experienced users that contradicts common wisdom
- "I wish I knew this before I started" type of lessons

Format each as:
- Misconception/Lesson: [describe the wrong assumption or hard lesson]
- Source: [URL to the specific comment/post]
- Quote: "[actual user words showing the mistake or lesson]"
- Why it matters: [how this changes what we should tell readers]

---

## Section 4: Case Studies / Real Stories

Find 3-5 real user stories.

{case_summ if case_summ else 'We have a case study library. Find DIFFERENT, fresh cases not yet collected.'}

Format:
- Story: [what happened — specific details, names, numbers, results]
- Source: [URL]
- Use in article: [what point this illustrates]

---

## Section 5: Authoritative Citations

Find 3-5 authoritative sources (.gov, .edu, academic journals, industry standards,
official reports).

{source_summ if source_summ else 'We have a pre-verified citation library. Find NEW citations not yet collected.'}

Format:
- Type: [source type]
- Key finding: [specific statistic or data point]
- Source URL:
- Why authoritative: [one sentence]

---

## Section 6: Information Gaps

**This is the most critical section.** Our article must be DIFFERENT.

Be SPECIFIC — name which competitor article misses which topic:
- Topics the top 5 all miss or cover poorly:
- Outdated or wrong information in current top results:
- Unique angle we can bring:
- What single piece of information would make our article definitive:

No generic "go deeper" or "add more detail." Name specific facts or angles.

---

## Section 7: PAA Questions

List 15+ "People Also Ask" questions related to {topic}. Group by subtopic.

---

## Section 8: People Also Ask (Structured)

For "{topic}" and 5-8 closely related queries, capture REAL PAA boxes.
Use VERBATIM phrasing — do NOT paraphrase. 10-15 entries:

- **[search] <PAA question verbatim from SERP>**
- Source: [URL or "search AI"]
- Answer hint: brief answer context (1-2 sentences)

Each question from real PAA box, Reddit, or Quora.
NO template questions like "What is X?" unless literally in SERP.
Include SERP source URL when possible.
Deduplicate identical questions across queries."""


def stage_r0_generate_prompt(topic: str, workspace: Path) -> dict:
    """Generate and save the search prompt. R0 is a full restart: any prior
    Research, Material-Pack, Draft, Pre-check, Post-process or Register
    artifact for this slug is wiped before the new prompt is written.
    """
    slug = _slugify(topic)
    today = _today_str()
    clear_all_action_artifacts(workspace, slug)
    prompt = _build_search_prompt(topic, workspace)
    out = workspace / "research" / f"search-prompt-{slug}-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    return {
        "success": True, "stage": "r0_prompt",
        "prompt_file": str(out), "prompt_content": prompt,
    }


# ── R1: Save Search Results + Run Collect ───────────────────────────────

async def stage_r1_save_and_collect(topic: str, search_text: str,
                                     workspace: Path) -> dict:
    """Save operator-pasted search results and run the old collect script.

    Re-running R1 invalidates everything from R3 onward (score, brief,
    material pack, draft, post-process verdict) so a stale downstream success
    cannot appear to belong to the new run.
    """
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    today = _today_str()

    out = workspace / "research" / f"search-results-{slug}-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(search_text, encoding="utf-8")

    clear_stage_artifacts(workspace, slug, "r3")

    rd_file = workspace / "research" / f"research-data-{slug}-{today}.md"
    rd_json = rd_file.with_suffix(".json")
    rd_file.unlink(missing_ok=True)
    rd_json.unlink(missing_ok=True)

    stdout, stderr, rc = await runner.run(
        "research_collector.py",
        ["collect", "--website", WEBSITE, "--topic", topic,
         "--search-file", str(out)],
    )
    save_report(workspace, "collect", slug, _combined_output(stdout, stderr))

    ok = (
        rc == 0
        and rd_file.is_file()
        and rd_json.is_file()
        and bool(_read_text(rd_file).strip())
    )
    if not ok:
        rd_file.unlink(missing_ok=True)
        rd_json.unlink(missing_ok=True)
    return {
        "success": ok,
        "stage": "r2_collect" if ok else "r1_results",
        "research_data_file": str(rd_file) if ok else None,
        "error": None if ok else (stderr.strip() or "数据收集失败，请查看收集报告"),
        "report": stdout,
    }


# ── R3-R4: AI Analysis + Scorer ─────────────────────────────────────────

_RESEARCH_AI_SYSTEM = """You are an SEO research analyst. Follow this exact methodology.

## Step 0: Context Inheritance
Read the topic-context if present. If source=plan or source=research: inherit intent, tier,
signals, guidance — DO NOT re-judge.
If source=heuristic: intent/tier are guessed; you may refine in analysis.

## Step 0.5: Keyword Priority
⭐⭐⭐ E1 — GSC measured (highest priority)
⭐⭐   E2 — Human-verified
⭐     E3 — Six-circle expansion (long-tail supplement)

## Step 1: Keyword Analysis
- Primary keyword from data package.
- Search intent verified from SERP (Section 1). If SERP contradicts PLAN, use SERP and note.
- Current ranking from A section.
- NEVER fabricate search volume or KD numbers. Use GSC impressions as demand proxy.

## Step 2: Competitive SERP Analysis
From Section 1:
- Top 5 content types and structures
- Must-cover topics ALL cover
- Differentiation opportunities (1-2 cover)
- Unique angle (NO ONE covers)

## Step 3: Objectivity Check
1. SERP intent matches what you plan? Adjust if not.
2. Each content gap confirmed by 2+ competitors? Otherwise not valid.
3. Already top 10? Different strategy than starting fresh.
4. High demand / low competition first.

## Step 4: Content Planning
- Pillar Page: 1-2 word, high volume, 3000-5000 words, 2-3 mini-stories
- Cluster Content: 3+ word, specific question, 1500-3000 words, 1-2 mini-stories
- Product Roundup: best/review/vs, 2500-4000 words, 2 mini-stories

Output: H2 outline, target word count, internal link strategy (3-4 blog + 2-3 product pages),
meta data (Title 50-60c, Desc 150-160c), FAQ questions (≥3, at least 1 from G category).

## Step 5: Scoring
The scorer runs separately. You explain the score — don't recalculate.

## Step 6: Finalize Material Pack
Check A-H pre-filled items. Mark [search] for new, [library] for confirmed.
Fill B/D/F if extractable. Check G (PAA) — supplement from SERP if Section 8 empty.
H: mark for manual fill.

### [search] Format (CRITICAL — wrong format = 0 items archived)
A. Pain Points:
```
- **[search] Pain point title**
- Source: [label](https://url)
- Quote: "actual user words"
```

C. Case Studies:
```
- **[search] Case title**
- Source: [label](https://url)
- Summary: detailed story
- Use in article: what point this illustrates
```

E. Citations:
```
- **[search] Citation title**
- Source: [label](https://url)
- Key finding: specific data or stat
- Type: .gov / .edu / standard / ...
```

G. PAA Questions:
```
- **[search] PAA question verbatim**
- Source: [URL or "search AI"]
- Answer hint: 1-2 sentence context
```

Rules:
- English field names (Source: Quote: Key finding:) — NOT Chinese
- Each line starts with "- "
- URLs in Markdown [label](url)
- G category: real PAA/Reddit/Quora only, NO template questions

### Fill Priority
Commercial: B > C > F > E
Informational: A > D > E > G

## Output Format
Output the Material Pack (Part 1 + Part 2 + Part 3), then `===BRIEF===`, then the Research Brief:

1. SEO Foundation (keywords with tier labels)
2. Competitive Landscape (rivals + gaps)
3. Recommended Outline (H2 structure)
4. Supporting Elements (data/stories/visuals)
5. Opportunity Score (explain, not recalculate)
6. Objectivity Checklist"""


async def stage_r3_ai_analyze(topic: str, workspace: Path, settings=None) -> dict:
    """AI analysis following old Research Skill Step 0-6.

    Re-running R3 invalidates everything from W0 onward (draft, pre-check,
    post-process verdict, register) so a downstream success cannot appear to
    belong to the new R3 run.
    """
    slug = _slugify(topic)
    today = _today_str()

    rd = _latest_file(f"research/research-data-{slug}-*.md", workspace)
    if not rd:
        return {"success": False, "stage": "r2_collect",
                "error": "research-data 文件不存在，请先粘贴搜索结果"}

    # research/SKILL.md Step 5 is explicit: the deterministic scorer runs
    # before AI writes the brief, and AI may only explain that existing score.
    # Pass exact input/output paths so a stale same-slug artifact cannot make a
    # failed scorer run look successful.
    rd_json = rd.with_suffix(".json")
    if not rd_json.exists():
        return {
            "success": False,
            "stage": "r2_collect",
            "error": "research-data JSON 不存在，无法执行确定性评分",
        }

    clear_stage_artifacts(workspace, slug, "w0")

    score_path = workspace / "research" / f"research-score-{slug}-{today}.md"
    score_run_path = (
        workspace / "research" / f".research-score-{slug}-{uuid4().hex}.md"
    )
    runner = LegacyRunner(workspace)
    stdout, stderr, rc = await runner.run(
        "research_scorer.py",
        [
            "--website", WEBSITE,
            "--data", str(rd_json),
            "--output", str(score_run_path),
        ],
    )
    score_report = _combined_output(stdout, stderr)
    save_report(workspace, "score", slug, score_report)
    current_score = _read_text(score_run_path)
    if rc != 0 or not current_score.strip():
        score_run_path.unlink(missing_ok=True)
        return {
            "success": False,
            "stage": "r2_collect",
            "error": stderr.strip() or "评分脚本失败或未产出本次评分报告",
            "report": score_report,
        }
    score_run_path.replace(score_path)

    data_text = _read_text(rd, _RESEARCH_DATA_CHAR_LIMIT)
    score_text = _read_text(score_path, _REPORT_CHAR_LIMIT)

    tc_raw = _read_text(workspace / "research" / f"topic-context-{slug}.json")
    tc_text = f"\n## Topic Context\n```json\n{tc_raw}\n```\n" if tc_raw else ""
    bv_text = _read_text(workspace / "context" / "brand-voice.md", _CONTEXT_CHAR_LIMIT)

    user_prompt = f"""Analyze: "{topic}"

{tc_text}

## Brand Voice
{bv_text}

## Research Data
{data_text}

## Deterministic Opportunity Score
{score_text}

Follow the system instructions exactly. Output Material Pack, then ===BRIEF===, then Brief."""

    try:
        content = await _run_ai_text(
            "legacy_research_analyze", _RESEARCH_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=8000)
    except Exception as exc:
        return {"success": False, "stage": "r2_collect", "error": str(exc)}

    parts = content.split("===BRIEF===", 1)
    mp_text = parts[0].strip()
    brief_text = parts[1].strip() if len(parts) > 1 else ""

    mp_path = workspace / "material-packs" / f"{slug}-{today}.md"
    mp_path.parent.mkdir(parents=True, exist_ok=True)
    mp_path.write_text(mp_text, encoding="utf-8")

    if brief_text:
        (workspace / "research" / f"brief-{slug}-{today}.md").write_text(
            brief_text, encoding="utf-8")

    return {
        "success": True,
        "stage": "r5_write_ready",
        "score_warning": None,
    }


# ── W0-W1: Validate + Draft ─────────────────────────────────────────────

_WRITE_AI_SYSTEM = """You are an SEO content writer. Follow these exact structural rules.

## Article Structure (in order):

1. FRONTMATTER (links left empty — post-process fills them):
---
Title: [H1 with primary keyword]
Slug: [URL slug]
Author: LaserPointerHub
Summary: [2-3 sentence summary]
Tags: [3-5 comma-separated]
SEO Title: [50-60 chars]
SEO Description: [150-160 chars]
SEO Keywords: [comma-separated, primary first]
---

2. H1 — same as frontmatter Title.

3. INTRODUCTION (150-250 words):
- First 1-2 sentences: DIRECT ANSWER to the search query
- Hook: ONE of (Provocative Question / Specific Scenario / Surprising Statistic / Bold Statement / Counterintuitive Claim)
- APP: Agree → Promise → Preview
- Primary keyword in first 100 words

4. KEY TAKEAWAYS (after intro):
> **Key Takeaways**
> - [Specific conclusion 1, with numbers/names/results]
> - [Specific conclusion 2-5]
3-5 standalone conclusions, not a table of contents.

5. QUICK SPECS (commercial articles only):
> ### Quick Specs: [Product]
> - **Wavelength**: [spec]
> - **Output Power**: [spec]
> - **Battery**: [spec]
> - **Build**: [material]
> - **Price**: [price]
> - **Key Differentiator**: [difference]
5-7 key-value pairs from live_products_report.

6. BODY (H2/H3):
- Pillar 2500-4000 / Cluster 1200-2500 / Roundup 2000-3500 words
- 4-7 H2s, logical progression
- Each paragraph 2-4 sentences, self-contained
- Embed at least 1 YouTube video

7. E-E-A-T (integrated):
- Experience: real user scenarios/test data from material pack C/E
- If NO real test data: go to analysis, do NOT invent "In our test"/"We found"
- Expertise: 1-2 authority sources for technical claims
- Trustworthiness: link contact page, return policy

8. SOFT-SELL: First 40% — NO hard product push. Products appear after intent.

9. REAL MATERIALS (instead of mini-stories):
- Extract real scenarios from A/C/E
- If material pack has cases: quote, mark source
- If no real scenarios: go to analysis, don't invent stories

10. CTAs (2-3, distributed):
- Informational → internal blog links
- Commercial → product page links
- Max 1 soft CTA in first 500 words
- No "click here"/"read more"

11. CONCLUSION (150-200 words): recap 3-5 points, next action, CTA.

12. FAQ (H2 "Frequently Asked Questions"):
- Natural language questions
- 2-3 sentence answers from body
- At least 1 from G category (real PAA)

13. FAQPage JSON-LD (3-4 Q&A pairs):
<script type="application/ld+json">
{ "@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [...] }
</script>

14. LINKS in body:
- Blog ~1/1000 words, Product ~1/1300 words, External ~1/800 words (min 2)
- NEVER fabricate URLs

## Core Rules:
1. Material pack = only source of truth. Missing data → "[data not in material pack]"
2. Primary keyword: H1, first 100 words, 2+ H2s
3. First 40%: no hard product push
4. First 1-2 sentences: direct answer
5. Self-contained paragraphs (2-4 sentences)
6. No generic link anchors
7. No fabricated numbers

Output ONLY the article Markdown — no preamble, no commentary, no code fence around the whole document."""


def _write_context_block(workspace: Path) -> str:
    parts = []
    for key, fname in [("brand_voice", "brand-voice.md"),
                        ("writing_examples", "writing-examples.md"),
                        ("style_guide", "style-guide.md"),
                        ("seo_guidelines", "seo-guidelines.md")]:
        text = _read_text(workspace / "context" / fname, _CONTEXT_CHAR_LIMIT)
        if text:
            parts.append(f"### {key}\n```\n{text}\n```")
    return "\n".join(parts)


def _write_system_prompt(author: str) -> str:
    if author and author.strip():
        return _WRITE_AI_SYSTEM.replace("Author: LaserPointerHub",
                                         f"Author: {author.strip()}")
    return _WRITE_AI_SYSTEM


async def stage_w0_validate_and_draft(topic: str, author: str, workspace: Path,
                                       settings=None) -> dict:
    """Validate material pack (段0), then generate the draft (段1).

    Re-running W0 invalidates everything from W1b onward (pre-check,
    post-process verdict, register, backlink suggestions) and the
    w2-state verdict file. The invalidation runs even if W0 itself fails,
    so a re-run that errors out still cleans up the previous attempt.
    """
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    today = _today_str()

    # Always invalidate downstream on re-entry, before any early-return.
    # Use "w0" as the marker so the function also clears W0's own outputs
    # (drafts, w2-state); the explicit old-draft loop below re-creates the draft.
    clear_stage_artifacts(workspace, slug, "w0")

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    if not mp:
        return {"success": False, "error": "素材包不存在，请先完成 Research"}

    stdout, stderr, rc = await runner.run(
        "write_collector.py",
        ["validate", "--website", WEBSITE, "--topic", topic, "--pack", str(mp)],
    )
    report = _combined_output(stdout, stderr)
    save_report(workspace, "validate", slug, report)

    # 段0 阻塞规则：必填项缺失 → 停止，提示补齐后重试
    if rc != 0 or "material pack file not found" in stdout.lower():
        return {"success": False, "error": "素材包校验失败，请查看校验报告", "report": report}

    ctx = read_topic_context(topic, workspace)
    tier = resolve_tier(topic, workspace)
    user_prompt = f"""Write: "{topic}"

## Topic Context
- Page tier: {tier}
- Search intent: {ctx.get('intent') or _detect_intent(topic)}
- Differentiation guidance: {ctx.get('guidance') or '(none recorded)'}
- Cannibalization note: {ctx.get('cannibal_risk') or '(none recorded)'}

## Material Pack
{_read_text(mp, _PACK_CHAR_LIMIT)}

## Context
{_write_context_block(workspace)}

Follow the system instructions. Output the full article Markdown with frontmatter."""

    try:
        content = await _run_ai_text(
            "legacy_write_draft", _write_system_prompt(author), user_prompt,
            settings=settings, max_tokens=16000)
    except Exception as exc:
        return {"success": False, "error": str(exc), "report": report}

    draft_path = workspace / "drafts" / f"{slug}-{today}.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite any prior draft for this slug so re-running W0 cannot leave
    # an old draft sitting alongside the new one.
    for old in (workspace / "drafts").glob(f"{slug}-*.md"):
        if old.is_file():
            old.unlink()
    draft_path.write_text(_strip_code_fence(content), encoding="utf-8")

    # A fresh draft invalidates any previous post-process verdict.
    save_w2_state(workspace, slug, {"rounds": 0, "gate_passed": False, "applied": False})

    return {"success": True, "stage": "w1_draft", "report": report}


def _strip_code_fence(text: str) -> str:
    """Some models wrap the whole document in a fence despite instructions."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


# ── Draft frontmatter repair ────────────────────────────────────────────
#
# Some AI drafts from W0 omit the closing `---` of the frontmatter. The
# pre-check parser then sees the whole file as frontmatter and reports
# 0-char SEO Title/Description, masking real content failures. Detect
# this case (starts with `---` but no second `---`) and insert the
# missing closer before the first Markdown heading.

def _has_closing_frontmatter(text: str) -> bool:
    """True if the first `---` is followed by a second `---` before any
    other content. We split on `---` and require at least 3 parts."""
    if not text.startswith("---"):
        return False
    parts = text.split("---", 2)
    return len(parts) >= 3


def repair_draft_frontmatter(workspace: Path, slug: str) -> dict:
    """If the latest draft has unterminated frontmatter, insert the
    missing closing `---` before the first Markdown heading. Returns:

        {"repaired": bool, "draft": str | None, "reason": str}

    Idempotent: if frontmatter is well-formed, returns repaired=False
    without writing the file.
    """
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"repaired": False, "draft": None, "reason": "no_draft"}
    text = draft.read_text(encoding="utf-8")
    if _has_closing_frontmatter(text):
        return {"repaired": False, "draft": str(draft), "reason": "already_well_formed"}
    if not text.startswith("---"):
        return {"repaired": False, "draft": str(draft), "reason": "no_frontmatter"}
    # Find the first Markdown heading line (after the opening ---).
    lines = text.splitlines(keepends=True)
    insert_at = None
    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            insert_at = i
            break
    if insert_at is None:
        return {"repaired": False, "draft": str(draft), "reason": "no_heading_found"}
    # Insert `\n---\n` immediately before the heading line, preserving it.
    new_lines = lines[:insert_at] + ["\n", "---\n"] + lines[insert_at:]
    new_text = "".join(new_lines)
    draft.write_text(new_text, encoding="utf-8")
    return {"repaired": True, "draft": str(draft), "reason": "inserted_closing"}


# ── W1b: Pre-Check ──────────────────────────────────────────────────────

async def stage_w1b_pre_check(topic: str, tier: str, workspace: Path) -> dict:
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    # Self-heal: if W0 produced a draft with unterminated frontmatter,
    # insert the missing closing `---` so the pre-check parser sees the
    # SEO Title / Description fields. This does NOT alter the post-W1b
    # gate; the pre-check still runs against the now-well-formed draft.
    repair_draft_frontmatter(workspace, slug)

    # Re-running pre-check invalidates everything from W2 onward so a stale
    # gate_passed/applied verdict cannot survive a re-run on the same draft.
    clear_stage_artifacts(workspace, slug, "w2")

    resolved_tier = resolve_tier(topic, workspace, tier)
    args = ["--draft", str(draft), "--tier", resolved_tier]

    keywords = _primary_keywords(draft)
    if keywords:
        args += ["--keywords", keywords]

    # Pass the material pack so the entity-coverage check can use the
    # pack's [search]/[library] entries (with their Source evidence) as
    # the candidate entity list. Without --pack the check skips coverage
    # and only warns.
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    if mp:
        args += ["--pack", str(mp)]

    args.append("--json")

    stdout, stderr, rc = await runner.run("write_pre_check.py", args)
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = None

    if not isinstance(result, dict) or not isinstance(result.get("checks"), list):
        report = _combined_output(stdout, stderr)
        save_report(workspace, "pre-check", slug, report)
        state = load_w2_state(workspace, slug)
        state["precheck_passed"] = False
        state["gate_passed"] = False
        state["applied"] = False
        state.pop("applied_draft_sha256", None)
        save_w2_state(workspace, slug, state)
        return {
            "success": False,
            "stage": "w1_draft",
            "error": stderr.strip() or "预检脚本未返回有效的结构化结果",
            "report": report,
        }

    fail_count = sum(1 for check in result["checks"] if not check.get("pass"))
    if rc not in (0, 1):
        report = _combined_output(stdout, stderr)
        save_report(workspace, "pre-check", slug, report)
        state = load_w2_state(workspace, slug)
        state["precheck_passed"] = False
        state["gate_passed"] = False
        state["applied"] = False
        state.pop("applied_draft_sha256", None)
        save_w2_state(workspace, slug, state)
        return {
            "success": False,
            "stage": "w1_draft",
            "error": stderr.strip() or f"预检脚本异常退出（exit {rc}）",
            "report": report,
        }

    lines = [
        f"# WRITE 预检报告 — {draft.name}",
        (
            f"> 层级: {resolved_tier} | 字数: {result.get('word_count', 0)}"
            f" | 失败: {fail_count} | 警告: {result.get('warn_count', 0)}"
        ),
        "",
        "| 检查项 | 结果 | 详情 |",
        "|---|---|---|",
    ]
    for check in result["checks"]:
        level = check.get("level", "ok" if check.get("pass") else "fail")
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(level, "❌")
        detail = str(check.get("detail", "")).replace("|", r"\|")
        lines.append(f"| {check.get('item', '未命名检查')} | {icon} | {detail} |")
    if stderr.strip():
        lines.extend(["", "## 脚本日志", "```", stderr.strip(), "```"])
    report = "\n".join(lines)
    save_report(workspace, "pre-check", slug, report)
    state = load_w2_state(workspace, slug)
    state["precheck_passed"] = fail_count == 0
    state["precheck_draft_sha256"] = _sha256_file(draft)
    state["precheck_tier"] = resolved_tier
    state["gate_passed"] = False
    state["applied"] = False
    state.pop("applied_draft_sha256", None)
    save_w2_state(workspace, slug, state)

    return {
        "success": True, "stage": "w1b_pre_check",
        "report": report, "fail_count": fail_count,
        "tier": resolved_tier,
    }


def _primary_keywords(draft: Path) -> str:
    m = re.search(r"^SEO Keywords:\s*(.+)$", _read_text(draft), re.MULTILINE)
    return m.group(1).strip() if m else ""


async def stage_w1b_revise(
    topic: str, tier: str, workspace: Path, settings=None
) -> dict:
    """AI revises a draft that failed W1b, then re-runs W1b.

    Unlike ``stage_w2_revise`` (which requires a post-process report and
    uses W2's failure list), this helper uses the W1b pre-check report
    as the failure list, so the operator can use AI to fix pre-check
    failures without first running W2. Capped at MAX_REVISION_ROUNDS.

    Frontmatter self-heal runs before AI so the AI sees a well-formed
    draft and the re-run W1b sees the SEO Title/Description fields.
    """
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    state = load_w2_state(workspace, slug)
    rounds = int(state.get("rounds", 0))
    if rounds >= MAX_REVISION_ROUNDS:
        return {
            "success": False,
            "error": (
                f"已用满 {MAX_REVISION_ROUNDS} 轮修订。按 skill 规则不再自动修改，"
                "请人工审阅草稿。"
            ),
            "rounds_used": rounds,
            "rounds_left": 0,
        }

    repair = repair_draft_frontmatter(workspace, slug)
    precheck_report = load_report(workspace, "pre-check", slug)
    if not precheck_report:
        return {"success": False, "error": "没有预检报告，请先运行 W1b"}

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    resolved_tier = resolve_tier(topic, workspace, tier)

    user_prompt = f"""Revise the article for: "{topic}"

The article failed the W1b pre-check. Fix the items listed below using
ONLY information from the material pack and the current draft. Do not
invent facts, numbers, quotes, or URLs.

## Pre-check report (what failed)
{precheck_report[:_REPORT_CHAR_LIMIT]}

## Current draft (frontmatter may have been auto-repaired)
{_read_text(draft)}

## Material pack (only source of truth)
{_read_text(mp, _PACK_CHAR_LIMIT) if mp else '(not available)'}

## Valid internal link targets
{_read_text(workspace / 'context' / 'internal-links-map.md', _CONTEXT_CHAR_LIMIT)}

Output the complete revised article Markdown with frontmatter."""

    try:
        revised = await _run_ai_text(
            "legacy_write_revise", _REVISE_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=16000,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    backup = draft.with_suffix(f".precheck-rev{rounds + 1}.md")
    backup.write_text(_read_text(draft), encoding="utf-8")
    draft.write_text(_strip_code_fence(revised), encoding="utf-8")

    state["rounds"] = rounds + 1
    save_w2_state(workspace, slug, state)

    precheck = await stage_w1b_pre_check(
        topic, resolved_tier, workspace
    )
    if not precheck.get("success") or precheck.get("fail_count", 0):
        return {
            "success": False,
            "stage": "w1b_pre_check",
            "gate_passed": False,
            "revised": True,
            "revision_round": rounds + 1,
            "rounds_used": rounds + 1,
            "rounds_left": MAX_REVISION_ROUNDS - (rounds + 1),
            "backup": str(backup),
            "frontmatter_repaired": repair.get("repaired", False),
            "precheck_report": precheck.get("report", ""),
            "error": precheck.get("error")
            or (
                f"第 {rounds + 1} 轮 AI 修订后预检仍有 "
                f"{precheck.get('fail_count', 0)} 项未通过"
            ),
        }
    return {
        "success": True,
        "stage": "w1b_pre_check",
        "gate_passed": True,
        "revised": True,
        "revision_round": rounds + 1,
        "rounds_used": rounds + 1,
        "rounds_left": MAX_REVISION_ROUNDS - (rounds + 1),
        "backup": str(backup),
        "frontmatter_repaired": repair.get("repaired", False),
    }


# ── W2: Post-Process (+ revision loop) ──────────────────────────────────

def _parse_post_process(report: str) -> dict[str, Any]:
    """Pull the gate verdict out of the script's Markdown report."""

    score = None
    m = re.search(r"-\s*总分:\s*([\d.]+)", report)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            score = None

    cannibal = None
    m = re.search(r"最高相似度\s*([\d.]+)", report)
    if m:
        try:
            cannibal = float(m.group(1))
        except ValueError:
            cannibal = None

    fix_items = ""
    m = re.search(r"## 🔧 怎么修\n(.+?)(?:\n## |\Z)", report, re.DOTALL)
    if m:
        fix_items = m.group(1).strip()

    link_issues = ""
    m = re.search(r"## ⚠️ 链接问题\n(.+?)(?:\n## |\Z)", report, re.DOTALL)
    if m:
        link_issues = m.group(1).strip()

    cannibal_error = "蚕食检查运行失败" in report
    score_error = "评分失败，请手动检查" in report
    return {
        "score": score,
        "cannibal": cannibal,
        "cannibal_block": (
            "**不可进入段3**：蚕食" in report and not cannibal_error
        ),
        "score_block": "**不可进入段3**：评分" in report,
        "cannibal_error": cannibal_error,
        "score_error": score_error,
        "fix_items": fix_items,
        "link_issues": link_issues,
    }


async def stage_w2_post_process(topic: str, workspace: Path, *,
                                 apply: bool = False, force: bool = False) -> dict:
    """Run 段2 once. Revision is a separate, operator-triggered action."""
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)

    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)

    state = load_w2_state(workspace, slug)
    rounds = int(state.get("rounds", 0))
    current_draft_sha256 = _sha256_file(draft)
    if (
        not state.get("precheck_passed")
        or state.get("precheck_draft_sha256") != current_draft_sha256
    ):
        return {
            "success": False,
            "error": "当前草稿尚未通过预检，或预检后草稿已改变；请重新运行预检",
        }
    if force:
        if not apply:
            return {
                "success": False,
                "error": "跳过蚕食门控只能在最终写回时使用",
            }
        if rounds < MAX_REVISION_ROUNDS or not state.get("cannibal_block"):
            return {
                "success": False,
                "error": (
                    f"只有用满 {MAX_REVISION_ROUNDS} 轮修订且蚕食仍被阻塞时，"
                    "才能经人工确认后跳过蚕食门控"
                ),
            }
        if state.get("cannibal_error") or state.get("score_error"):
            return {
                "success": False,
                "error": "检查器运行失败时不能跳过门控",
            }
        if state.get("score_block"):
            return {
                "success": False,
                "error": "评分未达标，不能用跳过蚕食门控进入注册",
            }

    args = ["post-process", "--website", WEBSITE, "--draft", str(draft)]
    if mp:
        args += ["--pack", str(mp)]
    if apply:
        args.append("--apply")
    if force:
        args.append("--force")

    stdout, stderr, rc = await runner.run("write_collector.py", args)
    report = _combined_output(stdout, stderr)
    save_report(workspace, "post-process", slug, report)

    metrics = _parse_post_process(stdout)
    gate_passed = rc == 0

    state["gate_passed"] = gate_passed
    state["score"] = metrics["score"]
    state["cannibal"] = metrics["cannibal"]
    state["cannibal_block"] = metrics["cannibal_block"]
    state["score_block"] = metrics["score_block"]
    state["cannibal_error"] = metrics["cannibal_error"]
    state["score_error"] = metrics["score_error"]
    if not gate_passed:
        state["applied"] = False
        state.pop("applied_draft_sha256", None)
    elif apply:
        state["applied"] = True
        state["force_confirmed"] = force
        state["applied_draft_sha256"] = _sha256_file(draft)
    save_w2_state(workspace, slug, state)

    rounds_left = max(0, MAX_REVISION_ROUNDS - int(state.get("rounds", 0)))

    result: dict[str, Any] = {
        "success": gate_passed,
        "stage": "w2_post_process" if (gate_passed and apply) else "w1b_pre_check",
        "report": report,
        "gate_passed": gate_passed,
        "rounds_used": int(state.get("rounds", 0)),
        "rounds_left": rounds_left,
        **metrics,
    }

    if not gate_passed and rounds_left == 0:
        # write/SKILL.md 段2: after 2 rounds — link problems do not block
        # publishing, but a score below the pass line stops the pipeline.
        result["needs_human_review"] = metrics["score_block"]
        result["needs_force_confirmation"] = metrics["cannibal_block"]
    if metrics["cannibal_error"]:
        result["error"] = "蚕食检查器运行失败；系统已停止，不能把失败当作安全"
    elif metrics["score_error"]:
        result["error"] = "质量评分器运行失败；系统已停止，不能进入注册"
    return result


_REVISE_AI_SYSTEM = """You are revising an SEO article that failed its automated quality gate.

You receive: the current draft, the material pack it must be sourced from, and the
post-process report listing exactly what failed.

Rules:
1. Fix EVERY item in the report's 「怎么修」 and 「链接问题」 sections.
2. The material pack is the only source of truth. Never invent facts, numbers,
   test results, quotes, authors or regulations. Missing data → "[data not in material pack]".
3. NEVER fabricate URLs. Only use URLs that already appear in the draft, the material
   pack, or the internal links map given to you.
4. If the report shows a cannibalization block, change the ANGLE — cut or rewrite the
   sections that duplicate the named existing article; do not merely reword sentences.
5. If the report shows a low quality score, raise specificity: concrete numbers, named
   scenarios and conclusions from the material pack — not filler paragraphs.
6. Keep the frontmatter fields (Title, Slug, Author, Summary, Tags, SEO Title,
   SEO Description, SEO Keywords). Leave the link fields (内链/外链/字数) out — the
   post-process script generates those.
7. Preserve the required structure: Key Takeaways block, H2 body, FAQ section,
   FAQPage JSON-LD.

Output ONLY the complete revised article Markdown. No preamble, no commentary,
no explanation of what you changed, no code fence around the whole document."""


async def stage_w2_revise(topic: str, workspace: Path, settings=None) -> dict:
    """One AI revision round, then re-run 段2. Capped at MAX_REVISION_ROUNDS."""
    slug = _slugify(topic)
    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    state = load_w2_state(workspace, slug)
    rounds = int(state.get("rounds", 0))
    if rounds >= MAX_REVISION_ROUNDS:
        return {"success": False,
                "error": f"已用满 {MAX_REVISION_ROUNDS} 轮修订。按 skill 规则不再自动修改，请人工审阅草稿。",
                "rounds_used": rounds, "rounds_left": 0}

    report = load_report(workspace, "post-process", slug)
    if not report:
        return {"success": False, "error": "没有后处理报告，请先运行后处理检查"}

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    metrics = _parse_post_process(report)

    user_prompt = f"""Revise the article for: "{topic}"

## Post-Process Report (what failed)
{report[:_REPORT_CHAR_LIMIT]}

## Current Draft
{_read_text(draft)}

## Material Pack (only source of truth)
{_read_text(mp, _PACK_CHAR_LIMIT)}

## Valid internal link targets
{_read_text(workspace / 'context' / 'internal-links-map.md', _CONTEXT_CHAR_LIMIT)}

Output the complete revised article Markdown."""

    try:
        revised = await _run_ai_text(
            "legacy_write_revise", _REVISE_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=16000)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Keep the superseded draft so a bad revision is never a one-way door.
    backup = draft.with_suffix(f".rev{rounds + 1}.md")
    backup.write_text(_read_text(draft), encoding="utf-8")
    draft.write_text(_strip_code_fence(revised), encoding="utf-8")

    state["rounds"] = rounds + 1
    save_w2_state(workspace, slug, state)

    precheck = await stage_w1b_pre_check(
        topic, str(state.get("precheck_tier") or ""), workspace
    )
    if not precheck.get("success") or precheck.get("fail_count", 0):
        return {
            "success": False,
            "stage": "w1b_pre_check",
            "gate_passed": False,
            "revised": True,
            "revision_round": rounds + 1,
            "rounds_used": rounds + 1,
            "rounds_left": MAX_REVISION_ROUNDS - (rounds + 1),
            "backup": str(backup),
            "previous_metrics": metrics,
            "precheck_report": precheck.get("report", ""),
            "error": precheck.get("error")
            or (
                f"第 {rounds + 1} 轮修订后预检仍有 "
                f"{precheck.get('fail_count', 0)} 项未通过"
            ),
        }

    outcome = await stage_w2_post_process(topic, workspace)
    outcome["revised"] = True
    outcome["revision_round"] = rounds + 1
    outcome["backup"] = str(backup)
    outcome["previous_metrics"] = metrics
    return outcome


# ── W3: Register + retroactive backlinks ────────────────────────────────

_BACKLINK_AI_SYSTEM = """You pick retroactive internal links for a newly published article.

You receive the candidate list the script produced (already filtered for tag overlap,
link saturation and existing links) and the body of each candidate article.

Your task, per write/SKILL.md 段3:
1. Choose 1-3 candidates. Fewer is fine — only pick ones where the link genuinely helps
   the reader of the OLD article.
2. For each pick, find a natural insertion point near one of its existing H2 sections.
3. Write a one-sentence contextual anchor using the OLD article's own terminology.
   Never use "click here" or "read more".
4. Maximum ONE backlink per old article.
5. Skip any candidate already marked as linked or saturated.

Output EXACTLY this format and nothing else:

→ 以下旧文章加回溯链接
#N, Title
URL: https://...
锚文本: `[natural transition sentence](new_url)`
插入位置: [H2 section name] 段落后

Repeat the block for each pick. If no candidate is a good fit, output only:
→ 本次没有合适的回溯链接候选

Do NOT edit the old articles. This is a checklist for human approval."""


def _parse_backlink_candidates(draft_text: str) -> list[dict[str, str]]:
    """Read the candidate block register appends to the draft."""

    section = ""
    m = re.search(r"## 回溯链接候选[^\n]*\n(.+)", draft_text, re.DOTALL)
    if m:
        section = m.group(1)
    if not section:
        return []

    candidates: list[dict[str, str]] = []
    for block in re.split(r"\n(?=\*\*#\d+)", section):
        head = re.match(r"\*\*#(\d+)\s*—\s*(.+?)\*\*", block.strip())
        if not head:
            continue
        url_m = re.search(r"- URL:\s*(\S+)", block)
        slug = ""
        if url_m:
            slug = url_m.group(1).rstrip("/").rsplit("/", 1)[-1]
        candidates.append({
            "num": head.group(1),
            "title": head.group(2).strip(),
            "url": url_m.group(1) if url_m else "",
            "slug": slug,
            "already_linked": "已链接此文章" in block,
            "block": block.strip(),
        })
    return candidates


async def stage_w3_register(topic: str, workspace: Path, settings=None) -> dict:
    """Run 段3 register, then produce the backlink checklist via AI."""
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)

    draft = _latest_file(f"drafts/{slug}-*.md", workspace)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    state = load_w2_state(workspace, slug)
    if not state.get("gate_passed") or not state.get("applied"):
        return {
            "success": False,
            "error": "草稿尚未通过后处理并写回链接字段，不能进入注册",
        }
    if state.get("applied_draft_sha256") != _sha256_file(draft):
        return {
            "success": False,
            "error": "草稿在后处理通过后又被修改，请重新预检并完成后处理",
        }

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    if not mp:
        return {"success": False,
                "error": "素材包不存在。若已注册过则无需重复注册；否则请重新完成 Research。"}

    draft_text = _read_text(draft)
    title_m = re.search(r"^Title:\s*(.+)$", draft_text, re.MULTILINE)
    kw_m = re.search(r"^SEO Keywords:\s*(.+)$", draft_text, re.MULTILINE)
    slug_m = re.search(r"^Slug:\s*(.+)$", draft_text, re.MULTILINE)

    article_title = title_m.group(1).strip() if title_m else topic
    primary_kw = kw_m.group(1).split(",")[0].strip() if kw_m else topic
    url_slug = slug_m.group(1).strip() if slug_m else slug
    new_url = f"https://{WEBSITE}.com/blog/{url_slug}"

    stdout, stderr, rc = await runner.run("write_collector.py", [
        "register", "--website", WEBSITE,
        "--draft", str(draft),
        "--pack", str(mp),
        "--new-url", new_url,
        "--title", article_title,
        "--keyword", primary_kw,
    ])
    report = _combined_output(stdout, stderr)
    save_report(workspace, "register", slug, report)

    if rc != 0:
        return {"success": False, "error": "注册脚本失败，请查看注册报告", "report": report}

    backlinks = await _generate_backlink_checklist(
        topic, workspace, draft, new_url, article_title, settings=settings)

    return {
        "success": True, "stage": "w3_register",
        "report": report, "new_url": new_url,
        **backlinks,
    }


async def _generate_backlink_checklist(topic: str, workspace: Path, draft: Path,
                                        new_url: str, article_title: str,
                                        settings=None) -> dict:
    slug = _slugify(topic)
    today = _today_str()

    candidates = _parse_backlink_candidates(_read_text(draft))
    actionable = [c for c in candidates if not c["already_linked"]]
    if not actionable:
        return {"backlink_note": "脚本未产出可用的回溯链接候选，跳过 AI 挑选",
                "backlink_candidates": len(candidates)}

    bodies = []
    for c in actionable:
        body = _read_text(workspace / "published" / f"{c['slug']}.md",
                          _OLD_ARTICLE_CHAR_LIMIT)
        if body:
            bodies.append(f"### #{c['num']} — {c['title']} ({c['url']})\n```\n{body}\n```")

    user_prompt = f"""New article: [{article_title}]({new_url})

## Candidates from the register script
{chr(10).join(c['block'] for c in actionable)}

## Bodies of the candidate articles
{chr(10).join(bodies) if bodies else '(no bodies available — judge from the candidate metadata only)'}

Produce the backlink checklist in the exact required format."""

    try:
        checklist = await _run_ai_text(
            "legacy_backlink_select", _BACKLINK_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=4000)
    except Exception as exc:
        return {"backlink_note": f"回溯链接 AI 未完成：{exc}",
                "backlink_candidates": len(actionable)}

    out = workspace / "research" / f"backlink-suggestions-{slug}-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# 回溯链接清单 — {article_title}\n"
        f"> 新文章: {new_url} | 生成于 {today}\n"
        f"> 由人工审批后手动执行，系统不会自动修改旧文章。\n\n"
        f"{checklist.strip()}\n",
        encoding="utf-8")

    return {
        "backlink_file": str(out),
        "backlink_checklist": checklist.strip(),
        "backlink_candidates": len(actionable),
    }


# ── Display helpers ────────────────────────────────────────────────────

def get_legacy_display_data(topic: str, workspace: Path,
                            db_stage: str | None = None) -> dict[str, Any]:
    """Collect all display data for the Legacy workflow UI."""
    stage, files = detect_stage(topic, workspace, db_stage)
    slug = files["slug"]

    data: dict[str, Any] = {
        "stage": stage, "stage_name": stage_label(stage),
        "stage_step": stage_step(stage), "files": files,
    }

    prompt = _read_text(files.get("search_prompt"))
    if prompt:
        data["prompt_content"] = prompt
        data["prompt_preview"] = prompt[:500]

    draft_text = _read_text(files.get("draft"))
    if draft_text:
        data["draft_preview"] = draft_text[:2000]

    score = _read_text(files.get("research_score"))
    if score:
        data["score_preview"] = score[:800]

    brief = _read_text(files.get("brief"))
    if brief:
        data["brief_preview"] = brief[:500]

    validate_report = load_report(workspace, "validate", slug)
    if validate_report:
        data["validate_report"] = validate_report

    pre_check = load_report(workspace, "pre-check", slug)
    if pre_check:
        data["pre_check_report"] = pre_check
        data["fail_count"] = pre_check.count("❌")

    # Precheck state for the UI gate. W2 is only allowed to run when
    # precheck_passed=True AND the draft on disk still has the same SHA
    # the pre-check recorded. Otherwise we surface a clear blocker so the
    # template can hide the W2 button and show "fix draft + rerun W1b".
    state = load_w2_state(workspace, slug)
    precheck_passed = bool(state.get("precheck_passed"))
    precheck_sha = str(state.get("precheck_draft_sha256", ""))
    draft_path_str = files.get("draft")
    current_sha = ""
    draft_text = ""
    if draft_path_str:
        draft_path_obj = Path(draft_path_str)
        if draft_path_obj.is_file():
            current_sha = _sha256_file(draft_path_obj)
            draft_text = draft_path_obj.read_text(encoding="utf-8")
    draft_matches = (precheck_sha == current_sha) if precheck_sha else True
    if not draft_path_str:
        precheck_state = "no_draft"
    elif not precheck_passed and not precheck_sha:
        precheck_state = "no_precheck"
    elif not precheck_passed:
        precheck_state = "failed"
    elif not draft_matches:
        precheck_state = "stale_sha"
    else:
        precheck_state = "passed"

    blocker_messages = {
        "no_draft": "草稿不存在，请先运行 W0 生成草稿。",
        "no_precheck": "尚未运行预检，请点击下方按钮运行 W1b。",
        "failed": (
            f"W1b 预检未通过（{data.get('fail_count', 0)} 项）。"
            "请先修复草稿后重新运行 W1b，不要直接进入 W2。"
        ),
        "stale_sha": "W1b 通过后草稿已被修改，请重新运行 W1b 预检。",
        "passed": "",
    }
    data["precheck_state"] = precheck_state
    data["precheck_passed"] = precheck_passed
    data["precheck_draft_match"] = draft_matches
    data["precheck_blocker_message"] = blocker_messages[precheck_state]
    data["draft_path"] = draft_path_str
    if draft_text:
        # Larger than draft_preview (which is 2 KB) so the user can see
        # the whole draft when fixing pre-check failures.
        data["draft_full"] = draft_text

    post_process = load_report(workspace, "post-process", slug)
    if post_process:
        data["post_process_report"] = post_process
        metrics = _parse_post_process(post_process)
        # state was already loaded above for the precheck gate check.
        rounds_used = int(state.get("rounds", 0))
        data["w2"] = {
            **metrics,
            "gate_passed": bool(state.get("gate_passed")),
            "applied": bool(state.get("applied")),
            "rounds_used": rounds_used,
            "rounds_left": max(0, MAX_REVISION_ROUNDS - rounds_used),
        }

    register_report = load_report(workspace, "register", slug)
    if register_report:
        data["register_report"] = register_report

    backlinks = _read_text(files.get("backlinks"))
    if backlinks:
        data["backlink_checklist"] = backlinks

    return data
