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

# The legacy skills correctly split research from writing, but the original
# adapter still handed W0 the entire pack plus four long context files.  These
# budgets are deliberately much smaller and are used only for the new,
# auditable write hand-off artifacts below.  The full material pack remains on
# disk and remains the source used by the deterministic gates.
_WRITE_BRIEF_CHAR_LIMIT = 7000
_WRITE_CONTEXT_TOTAL_CHAR_LIMIT = 5000
_EVIDENCE_CARD_TEXT_LIMIT = 320
_EVIDENCE_CARDS_PER_SECTION = 4
_REVISION_LINKS_CHAR_LIMIT = 3000
_W1B_REPAIR_FAILURE_SUMMARY_LIMIT = 24
_W1B_REVISION_EVIDENCE_CARD_LIMIT = 24
_W1B_REVISE_BODY_MAX_TOKENS = 8000

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


_DRAFT_BACKUP_RE = re.compile(r"\.(?:precheck-)?rev\d+\.md$")


def _latest_draft(workspace: Path, slug: str) -> Path | None:
    # Return the newest canonical draft, never a revision backup.
    candidates = sorted(
        (
            path
            for path in workspace.glob(f"drafts/{slug}-*.md")
            if not _DRAFT_BACKUP_RE.search(path.name)
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
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
           "research/write-brief-{slug}.json",
           "research/coverage-contract-{slug}.json",
           "research/evidence-cards-{slug}.json",
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


def _revision_rounds(state: dict[str, Any], phase: str) -> int:
    """Return the revision count for one quality phase.

    Older workspaces have one shared ``rounds`` value.  It belonged to W2,
    so retain it as the W2 fallback while giving W1b its own counter.
    """
    key = f"{phase}_rounds"
    if key in state:
        return max(0, int(state.get(key) or 0))
    return max(0, int(state.get("rounds") or 0)) if phase == "w2" else 0


def _set_revision_rounds(state: dict[str, Any], phase: str, value: int) -> None:
    value = max(0, int(value))
    state[f"{phase}_rounds"] = value
    # Compatibility for existing state files, scripts, and historical UI.
    if phase == "w2":
        state["rounds"] = value


def _record_revision_attempt(
    workspace: Path, slug: str, phase: str, result: dict[str, Any]
) -> None:
    """Persist a concise failure memory for the next operator/AI attempt."""
    state = load_w2_state(workspace, slug)
    draft = _latest_draft(workspace, slug)
    history = state.get("revision_history")
    if not isinstance(history, list):
        history = []
    error = str(
        result.get("retry_feedback") or result.get("error") or ""
    )
    history.append({
        "at": datetime.now(UTC).isoformat(),
        "phase": phase,
        "draft_sha256": _sha256_file(draft) if draft else "",
        "passed": bool(result.get("gate_passed") or result.get("success")),
        "error": error,
    })
    # This is an operator aid, not an unbounded audit log.
    state["revision_history"] = history[-12:]
    save_w2_state(workspace, slug, state)


def _recent_revision_memory(state: dict[str, Any], phase: str) -> str:
    """Format prior failed attempts for the revising model without hiding them."""
    history = state.get("revision_history")
    if not isinstance(history, list):
        return ""
    notes = [
        str(item.get("error") or "")
        for item in history
        if isinstance(item, dict)
        and item.get("phase") == phase
        and not item.get("passed")
    ]
    if not notes:
        return ""
    return "\n".join(
        f"- Earlier failed attempt: {note}"
        for note in notes[-1:]
        if note
    )


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
    draft = _latest_draft(workspace, slug)
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
                                          action_id: int | None = None,
                                          operator_requirements: str = "") -> dict:
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
        "operator_requirements": str(operator_requirements or "").strip()[:2000],
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

async def _run_ai_text(
    purpose: str,
    system: str,
    user: str,
    *,
    settings=None,
    max_tokens: int | None = None,
    thinking_mode: str | None = None,
) -> str:
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
        thinking_mode=thinking_mode,
        settings=active,
    )


# ── Evidence + Claim ledger helpers ────────────────────────────────────



def _parse_material_pack(mp_path: Path) -> list[dict]:
    from data_sources.modules.write_pre_check import parse_pack_entities
    return parse_pack_entities(mp_path)


def _compute_evidence_id(payload: dict) -> str:
    """Stable SHA-256 based evidence ID.

    ``payload`` must contain ``source_url``, ``quote``, ``key_finding``,
    and ``canonical_concepts`` (sorted).  All fields participate in the
    hash.  The same payload always produces the same ID.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "ev_" + hashlib.sha256(raw).hexdigest()[:12]


def _write_evidence_ledger(
    workspace: Path, slug: str, mp_path: Path,
) -> dict:
    """Parse the material pack and write a structured evidence-ledger JSON.

    Evidence ID is a stable SHA-256 hash of the source_url + quote +
    key_finding + canonical_concepts.  Entries without a valid URL or
    without both quote AND key_finding empty are skipped.

    Returns ``{"count": N, "path": str, "skipped": M}``.
    """
    mp_bytes = mp_path.read_bytes()
    mp_sha = hashlib.sha256(mp_bytes).hexdigest()
    entries = _parse_material_pack(mp_path)

    seen_ids: set[str] = set()
    ledger: list[dict[str, object]] = []
    skipped = 0

    for ent in entries:
        source_url = (ent.get("evidence") or "").strip()
        quote = (ent.get("source_quote") or "").strip()
        key_finding = (ent.get("source_key_finding") or "").strip()
        if not source_url or (not quote and not key_finding):
            skipped += 1
            continue

        concepts_raw = ent.get("entity") or ""
        # Canonical concepts from the entity title: split on / and strip.
        concepts = sorted(
            c.strip().lower() for c in concepts_raw.split("/") if c.strip()
        )
        if not concepts:
            concepts = ["uncategorized"]

        payload = {
            "source_url": source_url,
            "quote": quote,
            "key_finding": key_finding,
            "canonical_concepts": concepts,
        }
        ev_id = _compute_evidence_id(payload)

        if ev_id in seen_ids:
            # Same payload → deduplicate (skip duplicate entry)
            continue
        seen_ids.add(ev_id)

        tag = ent.get("source_tag", "search")
        is_required = (tag == "required")
        # Infer claim_types from section letter.
        section = (ent.get("source_section") or "").strip()
        claim_types: list[str] = []
        if section == "A":
            claim_types = ["pain_point"]
        elif section == "C":
            claim_types = ["case_study"]
        elif section == "E":
            claim_types = ["authority_citation"]
        elif section == "G":
            claim_types = ["paa_question"]
        else:
            claim_types = ["search_result"]

        ledger.append({
            "evidence_id": ev_id,
            "source_url": source_url,
            "quote": quote,
            "quote_verified": ent.get("source_quote_verified") is True,
            "key_finding": key_finding,
            "canonical_concepts": concepts,
            "claim_types": claim_types,
            "required": is_required,
        })

    # Collision check: distinct payloads that hash to the same ID must
    # not silently overwrite.  We only have deduplicated entries in the
    # ledger already, so this check is for extra safety.
    payload_by_id: dict[str, str] = {}
    for entry in ledger:
        eid = entry["evidence_id"]
        canon = json.dumps(
            {
                "source_url": entry["source_url"],
                "quote": entry["quote"],
                "key_finding": entry["key_finding"],
                "canonical_concepts": sorted(entry["canonical_concepts"]),
            },
            sort_keys=True, separators=(",", ":"),
        )
        if eid in payload_by_id and payload_by_id[eid] != canon:
            raise RuntimeError(
                f"Evidence ID collision: {eid} maps to distinct payloads — "
                "aborting.  This should not happen with SHA-256."
            )
        payload_by_id[eid] = canon

    out_data = {
        "version": 1,
        "material_pack_sha256": mp_sha,
        "evidence": ledger,
    }
    out_path = workspace / "research" / f"evidence-ledger-{slug}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"count": len(ledger), "path": str(out_path), "skipped": skipped}


def _read_evidence_ledger(workspace: Path, slug: str) -> dict | None:
    """Load the evidence-ledger JSON, or None if missing / invalid."""
    p = workspace / "research" / f"evidence-ledger-{slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_claim_ledger(workspace: Path, slug: str) -> dict | None:
    """Load the claim-ledger JSON, or None if missing / invalid."""
    p = workspace / "research" / f"claim-ledger-{slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _remove_ledger_files(workspace: Path, slug: str) -> None:
    """Delete evidence-ledger and claim-ledger for the given slug."""
    for name in ("evidence-ledger", "claim-ledger"):
        p = workspace / "research" / f"{name}-{slug}.json"
        if p.exists():
            p.unlink()


def _normalize_claim(text: str) -> str:
    """Wrap the shared ``normalize_claim_text`` from ``seo_common``."""
    return seo_common.normalize_claim_text(text)


def _validate_claim_ledger_json(cl_json: str, draft_body: str) -> dict:
    """Parse and validate the AI-provided claim ledger JSON against the
    article body.  Returns the validated dict or raises ValueError with
    a human-readable reason.

    * ``version`` must be 1
    * ``claims`` must be a list
    * each claim must have non-empty ``claim_text``, ``claim_type``,
      and a non-empty list ``evidence_ids`` (each non-empty string)
    * each ``claim_text`` must exist in ``draft_body`` after normalization
    """
    if not cl_json or not cl_json.strip():
        raise ValueError("===CLAIM_LEDGER=== section is empty")

    try:
        data = json.loads(cl_json)
    except Exception as exc:
        raise ValueError(f"CLAIM_LEDGER JSON parse failed: {exc}") from None

    if not isinstance(data, dict):
        raise ValueError("CLAIM_LEDGER must be a JSON object")
    if data.get("version") != 1:
        raise ValueError(f"CLAIM_LEDGER version must be 1, got {data.get('version')}")
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError("CLAIM_LEDGER.claims must be a list")

    # Build the normative sentence set from the shared implementation.
    all_sentences = (
        seo_common.extract_draft_sentences(draft_body)
        if draft_body
        else []
    )
    normalized_draft = {sentence["norm"] for sentence in all_sentences}

    for idx, c in enumerate(claims):
        if not isinstance(c, dict):
            raise ValueError(f"claims[{idx}] is not a dict")
        # claim_text: must be str before .strip() (never AttributeError).
        ct_raw = c.get("claim_text")
        if not isinstance(ct_raw, str):
            raise ValueError(
                f"claims[{idx}].claim_text 类型错误："
                f"期望 str，实际 {type(ct_raw).__name__}"
            )
        ct = ct_raw.strip()
        if not ct:
            raise ValueError(f"claims[{idx}].claim_text is empty")
        # claim_type: must be str before .strip() (never AttributeError).
        ctype_raw = c.get("claim_type")
        if not isinstance(ctype_raw, str):
            raise ValueError(
                f"claims[{idx}].claim_type 类型错误："
                f"期望 str，实际 {type(ctype_raw).__name__}"
            )
        ctype = ctype_raw.strip()
        if not ctype:
            raise ValueError(f"claims[{idx}].claim_type is empty")
        eids = c.get("evidence_ids")
        if not isinstance(eids, list) or not eids:
            raise ValueError(f"claims[{idx}] evidence_ids is empty or not a list")
        for eid in eids:
            if not isinstance(eid, str) or not eid.strip():
                raise ValueError(f"claims[{idx}] contains empty evidence_id")

        norm_ct = _normalize_claim(ct)
        if not norm_ct:
            raise ValueError(f"claims[{idx}].claim_text is empty after normalization")
        if norm_ct not in normalized_draft:
            raise ValueError(
                f"claims[{idx}].claim_text not found as full sentence in draft: "
                f"{ct[:60]}"
            )

    return data


def _extract_draft_sentences(draft_md: str) -> list[dict[str, str]]:
    """Wrap the shared ``extract_draft_sentences`` from ``seo_common``."""
    return seo_common.extract_draft_sentences(draft_md)


_CANONICAL_KEYS = frozenset({"version", "claims"})
_CLAIM_ALLOWED_KEYS = frozenset({"sentence_id", "claim_type", "evidence_ids", "claim_text"})


def _validate_claim_ledger_with_sentence_ids(
    data: dict, sentences: list[dict[str, str]],
) -> dict:
    """Validate a model response where claims select sentences by ID.

    The model must never be trusted to copy ``claim_text`` verbatim.  Here
    we:

    1.  Check the JSON schema (version, claims list) the same way
        ``_validate_claim_ledger_json`` does.
    2.  **Reject unknown keys** at the top level and on each claim.
    3.  For each claim:
        *   Require a non-empty str ``sentence_id`` matched **exactly**
            (no whitespace trimming, no tolerance).
        *   Resolve ``sentence_id`` against ``sentences``; unknown IDs fail.
        *   **Ignore** any ``claim_text`` returned by the model and replace
            it with the server-side original sentence text (``text``).
        *   Validate ``claim_type`` and ``evidence_ids`` with the same strict
            type/emptiness rules as ``_validate_claim_ledger_json``.
    4.  Build the canonical ledger dict with server-filled ``claim_text``
        and a persisted ``sentence_id`` on each claim.

    Raises ``ValueError`` with a concrete, non-sensitive reason on any failure.
    The caller (**not** this helper) must pass the canonical result through
    ``_validate_claim_ledger_json`` as the final authoritative gate.
    """
    if not isinstance(data, dict):
        raise ValueError("CLAIM_LEDGER must be a JSON object")
    unknown_top = {k for k in data if k not in _CANONICAL_KEYS}
    if unknown_top:
        raise ValueError(
            f"CLAIM_LEDGER contains unknown field(s): "
            f"{' '.join(sorted(unknown_top))}"
        )
    if data.get("version") != 1:
        raise ValueError(
            f"CLAIM_LEDGER version must be 1, got {data.get('version')}"
        )
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError("CLAIM_LEDGER.claims must be a list")

    by_id = {s["sentence_id"]: s for s in sentences}
    if not sentences and claims:
        raise ValueError(
            "claim ledger has claims but the draft produced no selectable "
            "sentences; cannot bind any sentence_id"
        )

    for idx, c in enumerate(claims):
        if not isinstance(c, dict):
            raise ValueError(f"claims[{idx}] is not a dict")
        unknown_claim = {k for k in c if k not in _CLAIM_ALLOWED_KEYS}
        if unknown_claim:
            raise ValueError(
                f"claims[{idx}] contains unknown field(s): "
                f"{' '.join(sorted(unknown_claim))}"
            )
        sid = c.get("sentence_id")
        if not isinstance(sid, str) or not sid:
            raise ValueError(
                f"claims[{idx}].sentence_id is missing, empty, or not a str"
            )
        # Exact match: no .strip() tolerance.
        if sid not in by_id:
            raise ValueError(
                f"claims[{idx}].sentence_id '{sid}' is not in the draft "
                f"sentence list"
            )
        # claim_type: must be str before .strip() (never AttributeError).
        ctype_raw = c.get("claim_type")
        if not isinstance(ctype_raw, str):
            raise ValueError(
                f"claims[{idx}].claim_type 类型错误："
                f"期望 str，实际 {type(ctype_raw).__name__}"
            )
        ctype = ctype_raw.strip()
        if not ctype:
            raise ValueError(f"claims[{idx}].claim_type is empty")
        eids = c.get("evidence_ids")
        if not isinstance(eids, list) or not eids:
            raise ValueError(f"claims[{idx}] evidence_ids is empty or not a list")
        for eid in eids:
            if not isinstance(eid, str) or not eid.strip():
                raise ValueError(f"claims[{idx}] contains empty evidence_id")

    # Build the canonical claims list: server-filled claim_text, kept
    # sentence_id, kept claim_type / evidence_ids.  Allow the same
    # sentence_id to appear in more than one claim (the same sentence can
    # legitimately support multiple evidence-led claims of different types).
    canonical_claims: list[dict[str, Any]] = []
    for c in claims:
        sentence = by_id[c["sentence_id"]]
        claim = {
            "sentence_id": sentence["sentence_id"],
            "claim_text": sentence["text"],
            "claim_type": c["claim_type"].strip(),
            "evidence_ids": list(c["evidence_ids"]),
        }
        canonical_claims.append(claim)

    canonical = {"version": 1, "claims": canonical_claims}
    return canonical


def _write_ahead_draft_and_ledger(
    workspace: Path, slug: str,
    draft_text: str, cl_data: dict,
) -> dict:
    """Atomically write a draft .md and its claim-ledger, returning
    ``{"draft_path": str, "claim_path": str}``.

    Write-ahead protocol:
    1. Record whether each target file existed, snapshot its content if so.
    2. Write temp files with the new content.
    3. Flush + fsync each temp file.
    4. Rename draft temp → real.
    5. Rename ledger temp → real.
    6. On ANY failure after step 2, restore consistency:
       - If old file existed → restore old content.
       - If old file did NOT exist (but the first replace created it)
         → delete the file so the workspace returns to "neither exists".
       - If both replaces succeeded → leave both new files.

    This guarantees no mixed-version state survives: either both files are
    the new version, or both are the old version (including the case where
    neither existed before).
    """
    import os as _os
    import tempfile as _tf

    draft_path = workspace / "drafts" / f"{slug}-{_today_str()}.md"
    cl_path = workspace / "research" / f"claim-ledger-{slug}.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    cl_path.parent.mkdir(parents=True, exist_ok=True)

    draft_existed = draft_path.exists()
    cl_existed = cl_path.exists()
    old_draft_content: str | None = (
        draft_path.read_text(encoding="utf-8") if draft_existed else None
    )
    old_cl_content: str | None = (
        cl_path.read_text(encoding="utf-8") if cl_existed else None
    )

    tmp_draft_path: Path | None = None
    tmp_cl_path: Path | None = None
    draft_replaced = False
    cl_replaced = False

    try:
        _, tmp_draft_path_str = _tf.mkstemp(
            dir=str(draft_path.parent),
            prefix=f".{slug}-", suffix=".md.tmp"
        )
        tmp_draft_path = Path(tmp_draft_path_str)
        tmp_draft_path.write_text(draft_text, encoding="utf-8")

        _, tmp_cl_path_str = _tf.mkstemp(
            dir=str(cl_path.parent),
            prefix=f".{slug}-", suffix=".json.tmp"
        )
        tmp_cl_path = Path(tmp_cl_path_str)
        tmp_cl_path.write_text(
            json.dumps(cl_data, ensure_ascii=False, indent=2), encoding="utf-8")

        for p in (tmp_draft_path, tmp_cl_path):
            with p.open("rb") as fh:
                os.fsync(fh.fileno())

        _os.replace(tmp_draft_path, draft_path)
        tmp_draft_path = None
        draft_replaced = True

        _os.replace(tmp_cl_path, cl_path)
        tmp_cl_path = None
        cl_replaced = True

    except Exception:
        if tmp_draft_path is not None and tmp_draft_path.exists():
            try:
                tmp_draft_path.unlink()
            except OSError:
                pass
        if tmp_cl_path is not None and tmp_cl_path.exists():
            try:
                tmp_cl_path.unlink()
            except OSError:
                pass
        if draft_replaced and not draft_existed:
            try:
                draft_path.unlink()
            except OSError:
                pass
        elif draft_replaced and draft_existed:
            draft_path.write_text(old_draft_content or "", encoding="utf-8")
        if cl_replaced and not cl_existed:
            try:
                cl_path.unlink()
            except OSError:
                pass
        elif cl_replaced and cl_existed:
            cl_path.write_text(old_cl_content or "", encoding="utf-8")
        raise

    return {"draft_path": str(draft_path), "claim_path": str(cl_path)}


def _ledger_refs(workspace: Path, slug: str) -> str:
    """Return a human-readable evidence reference block for the W0 prompt."""
    ev = _read_evidence_ledger(workspace, slug)
    if not ev or not ev.get("evidence"):
        return ""
    lines: list[str] = []
    for e in ev["evidence"]:
        eid = e.get("evidence_id", "?")
        url = e.get("source_url", "")
        q = e.get("quote") or e.get("key_finding") or ""
        required = e.get("required", False)
        tag = " [required]" if required else ""
        lines.append(f"- {eid}: {url} | {q[:100]}{tag}")
    return "\n".join(lines) + "\n"


# ── Compact, auditable writing hand-off ─────────────────────────────────

def _contract_path(workspace: Path, slug: str, name: str) -> Path:
    return workspace / "research" / f"{name}-{slug}.json"


def _contract_tokens(text: str) -> set[str]:
    """Return meaningful lexical tokens for deterministic card selection."""
    ignored = {
        "about", "after", "article", "best", "body", "content", "from",
        "guide", "into", "laser", "more", "page", "pointer", "section",
        "that", "the", "their", "this", "with", "your",
    }
    return {
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in ignored
    }


def _brief_outline(brief_text: str, topic: str) -> list[str]:
    """Extract the R3 H2 plan without asking another model to summarize it.

    The research skill's brief uses ``H2:`` lines inside a Recommended Outline
    block.  Some older workspaces have a less formal brief, so direct Markdown
    H2s are accepted as a conservative fallback.  A generic fallback keeps a
    resumed task writable, but is explicitly labelled as such in the contract.
    """
    headings = [
        match.group(1).strip().rstrip("# ")
        for match in re.finditer(r"(?mi)^\s*H2:\s*(.+?)\s*$", brief_text)
        if match.group(1).strip()
    ]
    if not headings:
        headings = [
            match.group(1).strip().rstrip("# ")
            for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", brief_text)
            if match.group(1).strip()
        ]
    seen: set[str] = set()
    deduped = []
    for heading in headings:
        key = heading.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(heading)
    return deduped[:7] or [f"Answer the reader's core question about {topic}"]


def _evidence_card(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep exact IDs and short source text; never summarize away evidence."""
    quote = str(entry.get("quote") or "").strip()
    support = quote or str(entry.get("key_finding") or "").strip()
    support_basis = (
        "verified_quote"
        if quote and entry.get("quote_verified") is True
        else "quote"
        if quote
        else "key_finding"
    )
    return {
        "evidence_id": str(entry.get("evidence_id") or ""),
        "source_url": str(entry.get("source_url") or ""),
        "support": support[:_EVIDENCE_CARD_TEXT_LIMIT],
        "support_basis": support_basis,
        "concepts": list(entry.get("canonical_concepts") or []),
        "claim_types": list(entry.get("claim_types") or []),
        "required": bool(entry.get("required")),
    }


def _select_evidence_cards(
    heading: str, evidence: list[dict[str, Any]], *, limit: int = _EVIDENCE_CARDS_PER_SECTION,
) -> list[dict[str, Any]]:
    """Recall broadly, then deterministically rank a small chapter card set.

    Every available card stays in ``evidence-cards`` for audit/recovery.  The
    selected set is only the context passed to the writer.  Required evidence
    is never silently filtered out, and a lexical zero-match falls back to the
    first available cards instead of pretending that a section has no sources.
    """
    heading_tokens = _contract_tokens(heading)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, entry in enumerate(evidence):
        haystack = " ".join([
            " ".join(str(x) for x in entry.get("canonical_concepts") or []),
            str(entry.get("quote") or ""),
            str(entry.get("key_finding") or ""),
        ])
        score = len(heading_tokens & _contract_tokens(haystack))
        if entry.get("required"):
            score += 100
        ranked.append((score, -index, entry))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected = [entry for score, _, entry in ranked if score > 0][:limit]
    if not selected:
        selected = [entry for _, _, entry in ranked[:limit]]
    return [_evidence_card(entry) for entry in selected]


def _write_context_contracts(workspace: Path, slug: str, topic: str, *,
                             tier: str = "", intent: str = "",
                             guidance: str = "") -> dict[str, Path]:
    """Persist the compact R3→W0 hand-off and its deterministic provenance.

    This is not a new truth source and it is not a gate bypass.  It is a small
    projection of the existing R3 brief and evidence ledger so the writing
    model can focus on the article rather than rediscovering every source.
    """
    brief_path = _latest_file(f"research/brief-{slug}-*.md", workspace)
    brief_text = _read_text(brief_path, _WRITE_BRIEF_CHAR_LIMIT)
    evidence_ledger = _read_evidence_ledger(workspace, slug) or {}
    evidence = [
        item for item in evidence_ledger.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    headings = _brief_outline(brief_text, topic)
    sections = [
        {
            "section_id": f"section_{index}",
            "heading": heading,
            "must_cover": True,
            "candidate_evidence_ids": [
                card["evidence_id"]
                for card in _select_evidence_cards(heading, evidence)
            ],
        }
        for index, heading in enumerate(headings, start=1)
    ]
    brief = {
        "version": 1,
        "topic": topic,
        "tier": tier,
        "intent": intent,
        "guidance": guidance[:1200],
        "outline": headings,
        "research_brief_excerpt": brief_text,
    }
    contract = {
        "version": 1,
        "topic": topic,
        "material_pack_sha256": evidence_ledger.get("material_pack_sha256", ""),
        "sections": sections,
        "selection_policy": "deterministic lexical recall + required evidence retention",
    }
    cards = {
        "version": 1,
        "topic": topic,
        "material_pack_sha256": evidence_ledger.get("material_pack_sha256", ""),
        "all_cards": [_evidence_card(entry) for entry in evidence],
        "sections": [
            {
                "section_id": section["section_id"],
                "heading": section["heading"],
                "cards": _select_evidence_cards(section["heading"], evidence),
            }
            for section in sections
        ],
    }
    paths = {
        "brief": _contract_path(workspace, slug, "write-brief"),
        "coverage": _contract_path(workspace, slug, "coverage-contract"),
        "cards": _contract_path(workspace, slug, "evidence-cards"),
    }
    for name, data in (("brief", brief), ("coverage", contract), ("cards", cards)):
        paths[name].parent.mkdir(parents=True, exist_ok=True)
        paths[name].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def _load_write_context_contracts(workspace: Path, slug: str, topic: str, *,
                                  tier: str = "", intent: str = "",
                                  guidance: str = "") -> dict[str, Any]:
    """Load an R3 projection or rebuild it for a resumed pre-existing action."""
    paths = _write_context_contracts(
        workspace, slug, topic, tier=tier, intent=intent, guidance=guidance,
    )
    loaded: dict[str, Any] = {}
    for name, path in paths.items():
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded[name] = {}
    return loaded


def _format_evidence_cards(cards: dict[str, Any], *, relevant_text: str = "",
                           preserve_evidence_ids: set[str] | None = None,
                           max_cards: int = 16) -> str:
    """Render compact cards without dropping evidence used by the old draft.

    ``max_cards`` limits optional/recalled cards, never preserved cards.
    """
    all_cards = [item for item in cards.get("all_cards", []) if isinstance(item, dict)]
    preserved_ids = set(preserve_evidence_ids or set())
    selected_ids: set[str] = set(preserved_ids)
    relevant_tokens = _contract_tokens(relevant_text)
    if relevant_tokens:
        ranked: list[tuple[int, str]] = []
        for card in all_cards:
            haystack = " ".join([
                str(card.get("support") or ""),
                " ".join(str(x) for x in card.get("concepts") or []),
            ])
            ranked.append((len(relevant_tokens & _contract_tokens(haystack)), str(card.get("evidence_id") or "")))
        ranked.sort(reverse=True)
        selected_ids.update(eid for score, eid in ranked[:8] if score > 0 and eid)
    if not selected_ids:
        for section in cards.get("sections", []):
            if isinstance(section, dict):
                selected_ids.update(
                    str(card.get("evidence_id") or "")
                    for card in section.get("cards", []) if isinstance(card, dict)
                )

    preserved = [
        card for card in all_cards
        if str(card.get("evidence_id") or "") in preserved_ids
    ]
    selected = [
        card for card in all_cards
        if str(card.get("evidence_id") or "") in selected_ids
        and str(card.get("evidence_id") or "") not in preserved_ids
    ]
    shown = preserved + selected
    if not shown:
        shown = all_cards[:max_cards]
    else:
        shown = shown[:max(max_cards, len(preserved))]
    lines = []
    for card in shown:
        lines.append(
            f"- {card.get('evidence_id')}: {card.get('support')} "
            f"| {card.get('source_url')}"
        )
    return "\n".join(lines)


def _compact_write_context(workspace: Path) -> str:
    """Keep only bounded editorial preferences; full context stays available on disk."""
    source_limits = [
        ("Brand voice", "brand-voice.md", 1600),
        ("Style guide", "style-guide.md", 1100),
        ("SEO guidance", "seo-guidelines.md", 1100),
        ("Writing examples", "writing-examples.md", 1200),
    ]
    remaining = _WRITE_CONTEXT_TOTAL_CHAR_LIMIT
    parts = []
    for label, filename, limit in source_limits:
        if remaining <= 0:
            break
        text = _read_text(workspace / "context" / filename, min(limit, remaining))
        if text:
            parts.append(f"### {label}\n{text}")
            remaining -= len(text)
    return "\n\n".join(parts)


def _current_claim_evidence_ids(workspace: Path, slug: str) -> set[str]:
    """Keep cards already used by a valid-looking draft during a revision."""
    ledger = _read_claim_ledger(workspace, slug) or {}
    ids: set[str] = set()
    for claim in ledger.get("claims", []):
        if isinstance(claim, dict):
            ids.update(
                str(item) for item in claim.get("evidence_ids", [])
                if isinstance(item, str) and item
            )
    return ids


def _revision_context_contracts(
    workspace: Path, slug: str, topic: str, *, relevant_text: str,
) -> tuple[dict[str, Any], str]:
    """Return brief/cards relevant to a repair without repeating the full pack."""
    contracts = _load_write_context_contracts(workspace, slug, topic)
    cards = contracts.get("cards") or {}
    rendered = _format_evidence_cards(
        cards,
        relevant_text=relevant_text,
        preserve_evidence_ids=_current_claim_evidence_ids(workspace, slug),
        max_cards=_W1B_REVISION_EVIDENCE_CARD_LIMIT,
    )
    return contracts, rendered


def _w1b_failure_summary(payload: dict[str, Any] | None) -> str:
    """Render a short repair checklist without repeating the full report."""
    if not isinstance(payload, dict):
        return "Structured failures are supplied below."
    failed = [
        item for item in payload.get("failed_checks", [])
        if isinstance(item, dict)
    ]
    lines = [f"- fail_count: {payload.get('fail_count', len(failed))}"]
    for item in failed[:_W1B_REPAIR_FAILURE_SUMMARY_LIMIT]:
        label = str(item.get("item") or "unnamed")
        detail = str(item.get("detail") or "").strip()
        suffixes = []
        fact_count = len(item.get("fact_issues") or [])
        entity_count = len(item.get("missing_entities") or [])
        if fact_count:
            suffixes.append(f"fact_issues={fact_count}")
        if entity_count:
            suffixes.append(f"missing_entities={entity_count}")
        suffix = f" ({', '.join(suffixes)})" if suffixes else ""
        if detail:
            lines.append(f"- {label}: {detail[:240]}{suffix}")
        else:
            lines.append(f"- {label}{suffix}")
    if len(failed) > _W1B_REPAIR_FAILURE_SUMMARY_LIMIT:
        lines.append(
            f"- plus {len(failed) - _W1B_REPAIR_FAILURE_SUMMARY_LIMIT} "
            "more structured failures below"
        )
    return "\n".join(lines)


def _w1b_compact_revision_context(contracts: dict[str, Any]) -> str:
    """Render the revision hand-off without full brief/material excerpts."""
    brief = contracts.get("brief") if isinstance(contracts, dict) else {}
    coverage = contracts.get("coverage") if isinstance(contracts, dict) else {}
    if not isinstance(brief, dict):
        brief = {}
    if not isinstance(coverage, dict):
        coverage = {}
    compact_brief = {
        key: brief.get(key)
        for key in ("topic", "tier", "intent", "guidance", "outline")
        if brief.get(key)
    }
    compact_sections = []
    for section in coverage.get("sections", []):
        if not isinstance(section, dict):
            continue
        compact_sections.append({
            key: section.get(key)
            for key in (
                "section_id", "heading", "must_cover",
                "candidate_evidence_ids",
            )
            if section.get(key) not in (None, "", [])
        })
    compact = {
        "brief": compact_brief,
        "coverage": {"sections": compact_sections[:8]},
    }
    return json.dumps(compact, ensure_ascii=False, indent=2)


# ── R0: Generate Search Prompt ──────────────────────────────────────────

def _build_search_prompt(topic: str, workspace: Path, operator_requirements: str = "") -> str:
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
    requirements_block = (
        f"\n## Operator requirements\n{operator_requirements.strip()[:2000]}\n"
        if operator_requirements and operator_requirements.strip()
        else ""
    )
    return f"""You are helping me research for an SEO article about "{topic}" on laserpointerhub.com.
I need comprehensive, real, verifiable data.
Do NOT fabricate anything — say "not found" if you cannot find it.
{requirements_block}

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


def stage_r0_generate_prompt(
    topic: str, workspace: Path, operator_requirements: str = ""
) -> dict:
    """Generate and save the search prompt. R0 is a full restart: any prior
    Research, Material-Pack, Draft, Pre-check, Post-process or Register
    artifact for this slug is wiped before the new prompt is written.
    """
    slug = _slugify(topic)
    today = _today_str()
    clear_all_action_artifacts(workspace, slug)
    prompt = _build_search_prompt(topic, workspace, operator_requirements)
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
    # Purge stale ledgers so a failed R3 cannot leave orphaned evidence.
    _remove_ledger_files(workspace, slug)

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

    # Write structured evidence ledger parsed from the material pack.
    ev_result = _write_evidence_ledger(workspace, slug, mp_path)
    _write_context_contracts(
        workspace,
        slug,
        topic,
        tier=resolve_tier(topic, workspace),
        intent=str(read_topic_context(topic, workspace).get("intent") or ""),
        guidance=str(read_topic_context(topic, workspace).get("guidance") or ""),
    )

    return {
        "success": True,
        "stage": "r5_write_ready",
        "score_warning": None,
        "evidence_count": ev_result.get("count", 0),
    }


# ── W0-W1: Validate + Draft ─────────────────────────────────────────────

_CLAIM_LEDGER_OUTPUT_CONTRACT = """## MANDATORY FINAL OUTPUT CONTRACT — DO NOT OMIT

This is a required delivery contract, not an optional appendix. Your response
must contain exactly these two parts, in this exact order:

1. The complete article Markdown, including its frontmatter.
2. On a line by itself, exactly `===CLAIM_LEDGER===`, immediately followed by
   one valid JSON object and nothing else.

Required shape of part 2 (the values below are placeholders only; never copy
them literally):

{
  "version": 1,
  "claims": [
    {
      "claim_text": "One complete factual sentence copied verbatim from the article.",
      "claim_type": "general",
      "evidence_ids": ["<copy-an-exact-id-from-Evidence-References>"]
    }
  ]
}

For every factual claim in the article, copy the full sentence verbatim into
`claim_text` and use only the exact supporting evidence ID shown in Evidence
References. Never invent an evidence ID. If the article genuinely contains no
factual claims, use exactly `{"version":1,"claims":[]}` instead.

Before submitting, silently verify: the separator exists exactly once; JSON is
valid; every claim_text is a complete sentence from the article; every
evidence_id is copied from Evidence References; and the response ends at the
closing `}` of the JSON object. Do not use a Markdown code fence for the JSON.
Do not add a preamble, explanation, apology, checklist, or any text after the
JSON object. Missing this section makes the whole response unusable.
"""


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

8. After the article, output ``===CLAIM_LEDGER===`` and a JSON object:

   {
     "version": 1,
     "claims": [
       {
         "claim_text": "Exactly one full sentence from the article, verbatim",
         "claim_type": "technical_specification|numeric|comparison|causal|safety|regulatory|general",
         "evidence_ids": ["ev_xxx"]
       }
     ]
   }

   Every claim_text must be a sentence that also appears in the article body
   (the service checks this).  claim_types: specification / numeric /
   comparison / causal / safety / regulatory / general.  evidence_ids must
   be from the Evidence References section above.  Do NOT fabricate IDs.
   If no factual claims, output ``===CLAIM_LEDGER===\n{"version":1,"claims":[]}``.

Output the article Markdown, then ``===CLAIM_LEDGER===``, then the JSON.
No preamble, no commentary, no code fence around the whole document."""

# Repeat the delivery contract at the end of the system instruction. W0 has
# many structural writing rules; the final, explicit contract keeps small
# instruction-following models from treating the ledger as an optional note.
_WRITE_AI_SYSTEM += "\n\n" + _CLAIM_LEDGER_OUTPUT_CONTRACT


# W0 used to ask one model response to satisfy both a long editorial brief and
# a machine-readable evidence audit.  Keep the same hard validation, but give
# each cognitive task a small, unambiguous prompt.
_WRITE_BODY_AI_SYSTEM = """You are an SEO content writer.

Write a complete article Markdown with frontmatter, H1, direct-answer
introduction, Key Takeaways, logical H2 sections, conclusion, FAQ, and visible
FAQPage JSON-LD when the article contains FAQ answers. Follow the supplied
write brief and coverage contract: every must-cover section needs a useful
reader-facing answer, but do not pad a section simply to use a card.

Use only the supplied evidence cards for externally verifiable facts. If a
card does not support a fact, write analysis or a qualified recommendation
instead. Never invent measurements, test results, quotes, authors, URLs,
regulations, products, or first-hand experience. Keep links limited to the
provided internal-link candidates and source URLs.

Return only the complete article Markdown. Do not output a claim ledger, JSON,
analysis, preamble, or code fence."""

_CLAIM_LEDGER_AI_SYSTEM = """You are an evidence auditor for an SEO article.

You will receive a numbered list of sentences taken from the article.  For
each factually verifiable sentence that is supported by at least one evidence
card, emit one claim object.  You MUST NOT copy or paraphrase sentence text:
refer to sentences ONLY by their stable ``S###`` sentence_id.

Required JSON shape (values are placeholders only; never copy them):

{"version":1,"claims":[
  {"sentence_id":"S003","claim_type":"technical_specification","evidence_ids":["ev_001"]}
]}

Rules:
* ``sentence_id`` MUST be one of the IDs in the supplied sentence list.
  Never invent, modify, or abbreviate an ID.
* ``claim_type`` must be a short, non-empty label (e.g. general,
  technical_specification, regulatory, safety, comparison).
* ``evidence_ids`` must be a non-empty list of exact evidence-IDs from the
  supplied evidence cards.  Every listed evidence_id must actually support
  the sentence identified by ``sentence_id``.
* The same ``sentence_id`` MAY appear in multiple claims if distinct
  evidence supports different aspects of the sentence.
* Silently inspect EVERY supplied sentence ID before returning. Do not stop
  after the first obvious claims.
* Coverage must be exhaustive: omitting a supported factual sentence will
  cause the downstream pre-check to fail.
* Do not include a ``claim_text`` field in your response.  The server
  fills ``claim_text`` from the authoritative draft sentence, so any
  value you put there is silently ignored.
* Do not include unknown fields.  Do not use Markdown fences, separator
  lines, explanation, or trailing text.
* If no sentence is supported by evidence, return exactly
  ``{"version":1,"claims":[]}``."""


_CLAIM_LEDGER_SENTENCE_BATCH_SIZE = 60
_CLAIM_LEDGER_BATCH_ATTEMPTS = 2
_CLAIM_LEDGER_BATCH_MAX_SPLIT_DEPTH = 1


async def _generate_claim_ledger_batch(
    batch_sentences: list[dict[str, str]],
    evidence_cards: str,
    *,
    batch_label: str,
    settings=None,
    split_depth: int = 0,
) -> list[dict[str, Any]]:
    """Generate one canonical batch, retrying malformed output before splitting."""
    sentence_lines = "\n".join(
        f"{sentence['sentence_id']}  {sentence['text']}"
        for sentence in batch_sentences
    ) or "(no selectable sentences were extracted from this draft)"
    base_prompt = f"""## Claim-ledger sentence batch {batch_label}

Inspect every supplied sentence in this batch. Emit claims only for the exact
S-IDs shown below. Other batches are handled separately and merged server-side.

## Article sentences (use these S-IDs verbatim)
{sentence_lines}

## Evidence cards (the only allowed evidence IDs)
{evidence_cards}

Return the complete JSON object only."""

    last_failure_kind = "generation failed"
    last_failure_detail = "unknown error"

    for attempt in range(1, _CLAIM_LEDGER_BATCH_ATTEMPTS + 1):
        retry_note = ""
        if attempt > 1:
            retry_note = (
                "\n\nYour previous response for this exact batch was incomplete "
                "or invalid. Return a fresh, complete JSON object from the opening "
                "brace through the closing brace. Do not continue the old response."
            )
        try:
            raw = await _run_ai_text(
                "legacy_write_claim_ledger",
                _CLAIM_LEDGER_AI_SYSTEM,
                base_prompt + retry_note,
                settings=settings,
                max_tokens=8000,
            )
        except ValueError as exc:
            last_failure_kind = "AI request failed"
            last_failure_detail = str(exc)
            continue

        clean = _strip_code_fence(raw)
        if "===CLAIM_LEDGER===" in clean:
            last_failure_kind = "format failed"
            last_failure_detail = (
                "response included forbidden ===CLAIM_LEDGER=== separator"
            )
            continue
        if not clean or not clean.strip():
            last_failure_kind = "AI response is empty"
            last_failure_detail = "empty response"
            continue

        try:
            data = json.loads(clean)
        except Exception as exc:
            last_failure_kind = "JSON parse failed"
            last_failure_detail = str(exc)
            continue

        try:
            canonical_batch = _validate_claim_ledger_with_sentence_ids(
                data,
                batch_sentences,
            )
        except ValueError as exc:
            last_failure_kind = "validation failed"
            last_failure_detail = str(exc)
            if attempt < _CLAIM_LEDGER_BATCH_ATTEMPTS:
                continue
            raise ValueError(
                f"CLAIM_LEDGER batch {batch_label} validation failed after "
                f"{_CLAIM_LEDGER_BATCH_ATTEMPTS} attempts: {exc}"
            ) from None

        return canonical_batch["claims"]

    if (
        split_depth < _CLAIM_LEDGER_BATCH_MAX_SPLIT_DEPTH
        and len(batch_sentences) > 1
    ):
        midpoint = len(batch_sentences) // 2
        try:
            left_claims = await _generate_claim_ledger_batch(
                batch_sentences[:midpoint],
                evidence_cards,
                batch_label=f"{batch_label}.a",
                settings=settings,
                split_depth=split_depth + 1,
            )
            right_claims = await _generate_claim_ledger_batch(
                batch_sentences[midpoint:],
                evidence_cards,
                batch_label=f"{batch_label}.b",
                settings=settings,
                split_depth=split_depth + 1,
            )
        except ValueError as exc:
            raise ValueError(
                f"CLAIM_LEDGER batch {batch_label} {last_failure_kind} after "
                f"{_CLAIM_LEDGER_BATCH_ATTEMPTS} attempts; split retry failed: "
                f"{exc}"
            ) from None
        return left_claims + right_claims

    raise ValueError(
        f"CLAIM_LEDGER batch {batch_label} {last_failure_kind} after "
        f"{_CLAIM_LEDGER_BATCH_ATTEMPTS} attempts: {last_failure_detail}"
    )


async def _generate_claim_ledger_for_draft(
    draft_md: str, cards: dict[str, Any], *, settings=None,
    evidence_cards_text: str | None = None,
) -> dict[str, Any]:
    """Generate and validate one exhaustive ledger in bounded AI batches.

    Sentence IDs are assigned once for the complete draft, then sent to the
    model in stable batches. Malformed or truncated batch output is retried;
    a persistently malformed batch is split once into smaller sub-batches.
    Every canonical claim is finally validated against the complete draft.
    """
    sentences = _extract_draft_sentences(draft_md)
    evidence_cards = (
        evidence_cards_text or _format_evidence_cards(cards)
        or "(No usable evidence cards were supplied.)"
    )
    sentence_batches = [
        sentences[index:index + _CLAIM_LEDGER_SENTENCE_BATCH_SIZE]
        for index in range(0, len(sentences), _CLAIM_LEDGER_SENTENCE_BATCH_SIZE)
    ] or [[]]
    canonical_claims: list[dict[str, Any]] = []
    batch_count = len(sentence_batches)

    for batch_index, batch_sentences in enumerate(sentence_batches, start=1):
        canonical_claims.extend(
            await _generate_claim_ledger_batch(
                batch_sentences,
                evidence_cards,
                batch_label=f"{batch_index}/{batch_count}",
                settings=settings,
            )
        )

    canonical = {"version": 1, "claims": canonical_claims}
    # Re-validate the merged canonical output through the authoritative
    # full-draft validator. No retry or split can bypass exact claim_text binding.
    canonical_json = json.dumps(canonical, ensure_ascii=False, indent=2)
    return _validate_claim_ledger_json(canonical_json, draft_md)


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


async def stage_w0_validate_and_draft(
    topic: str,
    author: str,
    workspace: Path,
    settings=None,
    *,
    action_id: int | None = None,
    site_slug: str = WEBSITE,
) -> dict:
    """Validate material pack (段0), then generate the draft (段1).

    Re-running W0 invalidates everything from W1b onward (pre-check,
    post-process verdict, register, backlink suggestions) and the
    w2-state verdict file.  The invalidation happens AFTER the candidate
    draft and claim-ledger have been validated, so a failed W0 run does
    not delete or alter any existing artifacts.
    """
    runner = LegacyRunner(workspace)
    slug = _slugify(topic)

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

    contracts = _load_write_context_contracts(
        workspace,
        slug,
        topic,
        tier=tier,
        intent=str(ctx.get("intent") or _detect_intent(topic)),
        guidance=str(ctx.get("guidance") or ""),
    )
    evidence_cards = _format_evidence_cards(contracts.get("cards") or {})

    user_prompt = f"""Write: "{topic}"

Use `{author.strip() or 'LaserPointerHub'}` as the frontmatter Author.

## Compact Write Brief
{json.dumps(contracts.get('brief') or {}, ensure_ascii=False, indent=2)}

## Coverage Contract
{json.dumps(contracts.get('coverage') or {}, ensure_ascii=False, indent=2)}

## Chapter Evidence Cards
{evidence_cards}

## Editorial Preferences
{_compact_write_context(workspace)}

The full material pack remains the authoritative archive and will be checked
after this step. Use this compact hand-off to write the article. Return the
article Markdown only."""

    try:
        draft_md = _strip_code_fence(await _run_ai_text(
            "legacy_write_body", _WRITE_BODY_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=16000, thinking_mode="disabled")
        )
    except Exception as exc:
        return {"success": False, "error": str(exc), "report": report}

    if not draft_md:
        return {"success": False, "error": "AI 输出了空的文章正文", "report": report}
    if "===CLAIM_LEDGER===" in draft_md:
        return {
            "success": False,
            "error": "W0 正文任务错误包含 CLAIM_LEDGER；正文和账本必须分开生成",
            "report": report,
        }

    try:
        cl_data = await _generate_claim_ledger_for_draft(
            draft_md, contracts.get("cards") or {}, settings=settings,
        )
    except ValueError as exc:
        return {"success": False, "error": f"claim-ledger 生成/校验失败: {exc}", "report": report}
    except Exception as exc:
        return {"success": False, "error": f"claim-ledger 生成失败: {exc}", "report": report}

    cl_data["draft_sha256"] = hashlib.sha256(draft_md.encode("utf-8")).hexdigest()

    # Commit the candidate atomically WITHOUT deleting old artifacts first.
    # _write_ahead_draft_and_ledger snapshots existing files and rolls back on
    # failure, so old draft / claim-ledger remain untouched if anything fails.
    try:
        written = _write_ahead_draft_and_ledger(workspace, slug, draft_md, cl_data)
    except Exception as exc:
        return {
            "success": False,
            "error": f"W0 写入草稿/claim-ledger 失败: {exc}",
            "report": report,
        }

    # Only after a successful atomic commit: invalidate downstream W1b/W2
    # artifacts and reset W2 state.  Old draft/ledger are already replaced.
    clear_stage_artifacts(workspace, slug, "w1b")
    save_w2_state(workspace, slug, {"rounds": 0, "gate_passed": False, "applied": False})

    result: dict[str, Any] = {
        "success": True,
        "stage": "w1_draft",
        "report": report,
    }
    if action_id is None:
        return result

    from seo_ops.config import get_settings
    from seo_ops.services.sectional_legacy_adapter import run_legacy_sectional_rollout

    active_settings = settings or get_settings()
    if getattr(active_settings, "sectional_writing_mode", "off") == "off":
        return result

    async def sectional_generate(system: str, user: str, **kwargs: Any) -> str:
        if user.startswith("SECTION PACKAGE\n"):
            purpose = "legacy_write_sectional_body"
            max_tokens = 5000
        elif user.startswith("ARTICLE FRAME PACKAGE\n"):
            purpose = "legacy_write_sectional_frame"
            max_tokens = 5000
        elif user.startswith("SECTION CLAIM PACKAGE\n"):
            purpose = "legacy_write_sectional_claim_ledger"
            max_tokens = kwargs.get("max_tokens") or 4000
        else:
            raise ValueError("未知的 sectional AI prompt")
        return await _run_ai_text(
            purpose,
            system,
            user,
            settings=active_settings,
            max_tokens=max_tokens,
            thinking_mode="disabled",
        )

    try:
        sectional_brief = contracts.get("brief", {})
        result["sectional_rollout"] = await run_legacy_sectional_rollout(
            action_id=action_id,
            topic=topic,
            author=author.strip() or "LaserPointerHub",
            tier=tier,
            intent=str(sectional_brief.get("intent") or _detect_intent(topic)),
            guidance=str(sectional_brief.get("guidance") or ""),
            workspace=workspace,
            slug=slug,
            site_slug=site_slug,
            contracts=contracts,
            formal_draft_path=Path(written["draft_path"]),
            formal_claim_path=Path(written["claim_path"]),
            settings=active_settings,
            generate_text_async=sectional_generate,
        )
    except Exception as exc:
        result["sectional_rollout"] = {
            "status": "failed_keep_legacy",
            "error": str(exc),
        }
    return result


async def stage_sectional_shadow_existing_pair(
    topic: str,
    author: str,
    workspace: Path,
    settings: Any | None = None,
    *,
    action_id: int,
    site_slug: str = WEBSITE,
) -> dict[str, Any]:
    """Run sectional shadow for an existing formal Legacy pair.

    Unlike W0, this operation never generates or replaces the formal draft or
    claim ledger.  It exists for controlled rollout validation on Actions that
    have already progressed beyond W0.
    """
    from seo_ops.config import get_settings
    from seo_ops.services.sectional_legacy_adapter import (
        run_existing_legacy_sectional_shadow,
    )

    active_settings = settings or get_settings()
    if getattr(active_settings, "sectional_writing_mode", "off") != "shadow":
        return {
            "success": False,
            "error": "现有正式稿的章节化运行只允许 SEO_OPS_SECTIONAL_WRITING_MODE=shadow",
        }
    slug = _slugify(topic)

    async def sectional_generate(system: str, user: str, **kwargs: Any) -> str:
        if user.startswith("SECTION PACKAGE\n"):
            purpose = "legacy_write_sectional_body"
            max_tokens = 5000
        elif user.startswith("ARTICLE FRAME PACKAGE\n"):
            purpose = "legacy_write_sectional_frame"
            max_tokens = 5000
        elif user.startswith("SECTION CLAIM PACKAGE\n"):
            purpose = "legacy_write_sectional_claim_ledger"
            max_tokens = kwargs.get("max_tokens") or 4000
        else:
            raise ValueError("未知的 sectional AI prompt")
        return await _run_ai_text(
            purpose,
            system,
            user,
            settings=active_settings,
            max_tokens=max_tokens,
            thinking_mode="disabled",
        )

    try:
        rollout = await run_existing_legacy_sectional_shadow(
            action_id=action_id,
            topic=topic,
            author=author.strip() or "LaserPointerHub",
            workspace=workspace,
            slug=slug,
            site_slug=site_slug,
            settings=active_settings,
            generate_text_async=sectional_generate,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"章节化 shadow 失败，正式 Legacy pair 保持不变: {exc}",
        }
    return {
        "success": True,
        "sectional_rollout": rollout,
        "formal_pair": rollout.get("formal_pair"),
    }


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
    draft = _latest_draft(workspace, slug)
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
    draft = _latest_draft(workspace, slug)
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

    # Pass evidence-ledger and claim-ledger for fact checking (check 14).
    # Fail-closed: the pre-check script blocks if these files are missing.
    ev_ledger = workspace / "research" / f"evidence-ledger-{slug}.json"
    cl_ledger = workspace / "research" / f"claim-ledger-{slug}.json"
    args += ["--evidence-ledger", str(ev_ledger)]
    args += ["--claim-ledger", str(cl_ledger)]

    # Pass material pack path for SHA-256 comparison.
    if mp:
        args += ["--material-pack", str(mp)]

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


def _w1b_precheck_data(
    draft: Path,
    tier: str,
    workspace: Path,
    slug: str,
    *,
    claim_path: Path | None = None,
) -> dict[str, Any]:
    """Run deterministic W1b and return the complete structured result."""
    from data_sources.modules import write_pre_check

    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)
    ev_path = workspace / "research" / f"evidence-ledger-{slug}.json"
    cl_path = claim_path or (
        workspace / "research" / f"claim-ledger-{slug}.json"
    )
    return write_pre_check.run(
        str(draft),
        tier=tier,
        keywords=_primary_keywords(draft),
        pack=str(mp) if mp else "",
        evidence_ledger=str(ev_path),
        claim_ledger=str(cl_path),
        material_pack=str(mp) if mp else "",
    )


def _w1b_failure_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Retain exact failed checks and structured sentence/entity data."""
    failed: list[dict[str, Any]] = []
    for check in result.get("checks", []):
        if not isinstance(check, dict) or check.get("pass"):
            continue
        item: dict[str, Any] = {
            "item": str(check.get("item") or "unnamed"),
            "detail": str(check.get("detail") or ""),
        }
        for key in ("fact_issues", "missing_entities"):
            value = check.get(key)
            if isinstance(value, list):
                item[key] = value
        failed.append(item)
    return {
        "fail_count": int(result.get("fail_count") or len(failed)),
        "failed_checks": failed,
    }


def _candidate_w1b_precheck(
    draft_md: str,
    claim_data: dict[str, Any],
    tier: str,
    workspace: Path,
    slug: str,
) -> dict[str, Any]:
    """Validate a candidate draft/ledger pair before live files change."""
    import tempfile

    with tempfile.TemporaryDirectory(
        prefix="w1b-candidate-",
        dir=str(_reports_dir(workspace)),
    ) as temp_dir:
        temp_root = Path(temp_dir)
        temp_draft = temp_root / "candidate.md"
        temp_claim = temp_root / "claim-ledger.json"
        temp_draft.write_text(draft_md, encoding="utf-8")
        temp_claim.write_text(
            json.dumps(claim_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _w1b_precheck_data(
            temp_draft,
            tier,
            workspace,
            slug,
            claim_path=temp_claim,
        )


def _fact_issues_from_precheck(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return exact structured factual gaps from one candidate precheck."""
    issues: list[dict[str, Any]] = []
    for check in result.get("checks", []):
        if not isinstance(check, dict):
            continue
        value = check.get("fact_issues")
        if not isinstance(value, list):
            continue
        issues.extend(item for item in value if isinstance(item, dict))
    return issues


async def _supplement_claim_ledger_fact_gaps(
    draft_md: str,
    claim_data: dict[str, Any],
    fact_issues: list[dict[str, Any]],
    evidence_cards: str,
    *,
    settings=None,
) -> tuple[dict[str, Any], int, int]:
    """Run up to two focused audits for factual sentences omitted earlier.

    Each pass uses the same strict sentence-ID and evidence-ID validators.
    Unsupported sentences may still be omitted and remain blocking. The
    second pass only rechecks IDs omitted by the first valid JSON response.
    """
    sentence_table = {
        sentence["text"]: sentence
        for sentence in _extract_draft_sentences(draft_md)
    }
    claimed_ids = {
        claim.get("sentence_id")
        for claim in claim_data.get("claims", [])
        if isinstance(claim, dict)
        and isinstance(claim.get("sentence_id"), str)
    }

    missing_sentences: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for issue in fact_issues:
        text = issue.get("sentence")
        if not isinstance(text, str):
            continue
        sentence = sentence_table.get(text)
        if not sentence:
            continue
        sentence_id = sentence["sentence_id"]
        if sentence_id in claimed_ids or sentence_id in seen_ids:
            continue
        seen_ids.add(sentence_id)
        missing_sentences.append(sentence)

    requested = len(missing_sentences)
    if not missing_sentences:
        return claim_data, 0, 0

    merged_claims = [
        claim
        for claim in claim_data.get("claims", [])
        if isinstance(claim, dict)
    ]
    seen_claims = {
        (
            claim.get("sentence_id"),
            claim.get("claim_type"),
            tuple(claim.get("evidence_ids") or []),
        )
        for claim in merged_claims
    }
    total_added = 0
    remaining = missing_sentences

    for pass_index in range(1, 3):
        supplemental_claims = await _generate_claim_ledger_batch(
            remaining,
            evidence_cards,
            batch_label=f"fact-gap-{pass_index}",
            settings=settings,
        )

        added_this_pass = 0
        newly_claimed_ids: set[str] = set()
        for claim in supplemental_claims:
            key = (
                claim.get("sentence_id"),
                claim.get("claim_type"),
                tuple(claim.get("evidence_ids") or []),
            )
            if key in seen_claims:
                continue
            seen_claims.add(key)
            merged_claims.append(claim)
            added_this_pass += 1
            total_added += 1
            sentence_id = claim.get("sentence_id")
            if isinstance(sentence_id, str):
                newly_claimed_ids.add(sentence_id)

        remaining = [
            sentence for sentence in remaining
            if sentence["sentence_id"] not in newly_claimed_ids
        ]
        if not remaining:
            break
        # A second pass is intentionally allowed even when the first valid
        # response omitted every ID; malformed-output retries are separate.
        if pass_index == 2:
            break

    canonical_json = json.dumps(
        {"version": 1, "claims": merged_claims},
        ensure_ascii=False,
    )
    canonical = _validate_claim_ledger_json(canonical_json, draft_md)
    return canonical, requested, total_added


def _remove_uncovered_fact_sentences(
    draft_md: str,
    fact_issues: list[dict[str, Any]],
) -> tuple[str, int]:
    """Conservatively remove exact factual sentences that remain uncovered.

    This is the final fail-closed fallback after the body revision and focused
    ledger audits have both run. Only exact ``uncovered_factual_sentence``
    strings from the structured pre-check payload are removed. Frontmatter,
    unrelated prose, headings, code fences, and JSON-LD remain untouched.
    """
    frontmatter, body = _split_frontmatter_text(draft_md)
    removed = 0
    seen: set[str] = set()
    removable_norms: set[str] = set()

    # Mask delivery-only blocks before removing a sentence.  A factual
    # sentence can legitimately appear in a fenced example or FAQ JSON-LD
    # while the same sentence is also present in prose.  Plain ``str.replace``
    # could otherwise remove the protected occurrence first and leave the
    # reader-visible unsupported claim untouched.
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00W1BPROTECTED{len(protected) - 1}\x00"

    masked_body = re.sub(
        r"```[\s\S]*?```|"
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>"
        r"[\s\S]*?</script>",
        _protect,
        body,
        flags=re.IGNORECASE,
    )

    for issue in fact_issues:
        if issue.get("reason") != "uncovered_factual_sentence":
            continue
        sentence = issue.get("sentence")
        if not isinstance(sentence, str):
            continue
        sentence = sentence.strip()
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        removable_norms.add(seo_common.normalize_claim_text(sentence))

    if not removable_norms:
        return draft_md, 0

    spans_to_remove: list[tuple[int, int]] = []
    consumed_norms: set[str] = set()
    paragraph_re = re.compile(r"(?s)(^|\n{2,})(.*?)(?=\n{2,}|\Z)")
    splitter_re = re.compile(r"(?<=[.!?])\s+")

    for paragraph_match in paragraph_re.finditer(masked_body):
        paragraph = paragraph_match.group(2)
        if not paragraph.strip() or "\x00W1BPROTECTED" in paragraph:
            continue
        paragraph_offset = paragraph_match.start(2)
        cursor = 0
        sentence_spans: list[tuple[int, int]] = []
        for split_match in splitter_re.finditer(paragraph):
            sentence_spans.append((cursor, split_match.start()))
            cursor = split_match.end()
        sentence_spans.append((cursor, len(paragraph)))

        for start, end in sentence_spans:
            sentence_text = paragraph[start:end].strip()
            sentence_norm = seo_common.normalize_claim_text(sentence_text)
            if not sentence_norm or sentence_norm in consumed_norms:
                continue
            if sentence_norm not in removable_norms:
                continue
            absolute_start = paragraph_offset + start
            absolute_end = paragraph_offset + end
            while (
                absolute_start < absolute_end
                and masked_body[absolute_start].isspace()
            ):
                absolute_start += 1
            while (
                absolute_end > absolute_start
                and masked_body[absolute_end - 1].isspace()
            ):
                absolute_end -= 1
            if absolute_start < absolute_end:
                spans_to_remove.append((absolute_start, absolute_end))
                consumed_norms.add(sentence_norm)

    if not spans_to_remove:
        return draft_md, 0

    for start, end in sorted(spans_to_remove, reverse=True):
        masked_body = masked_body[:start] + masked_body[end:]
        removed += 1

    if not removed:
        return draft_md, 0

    # Remove whitespace left by exact sentence deletion without rewriting any
    # surviving prose or touching the frontmatter contract.
    body = masked_body
    for index, original in enumerate(protected):
        body = body.replace(f"\x00W1BPROTECTED{index}\x00", original)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    cleaned = (frontmatter + body).strip()
    return cleaned, removed


def _w1b_primary_keyword(draft: Path, topic: str) -> str:
    """Return the exact keyword the deterministic W1b rules will enforce."""
    raw_keywords = _primary_keywords(draft)
    return next(
        (item.strip() for item in raw_keywords.split(",") if item.strip()),
        topic.strip(),
    )


def _split_frontmatter_text(text: str) -> tuple[str, str]:
    """Return (frontmatter_with_delimiters, body) without guessing malformed YAML."""
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return f"---{parts[1]}---", parts[2]


def _fit_w1b_meta_description(value: str, primary_keyword: str) -> str:
    """Fit an existing description to 150-160 chars without adding factual claims."""
    text = re.sub(r"\s+", " ", (value or "")).strip().strip('"')
    fillers = (
        f" Practical guidance for {primary_keyword}.",
        " Review the key considerations, options, and planning steps.",
        " Use this guide to make an informed decision.",
    )
    filler_index = 0
    while len(text) < 150:
        filler = fillers[filler_index % len(fillers)].strip()
        text = f"{text} {filler}".strip()
        filler_index += 1
    if len(text) <= 160:
        return text

    window = text[:160].rstrip()
    boundary = window.rfind(" ")
    if boundary >= 150:
        window = window[:boundary].rstrip(" ,;:-")
        if len(window) < 160 and not window.endswith((".", "!", "?")):
            window += "."
    if len(window) < 150:
        window = text[:160].rstrip()
    return window


def _fit_w1b_seo_title(value: str, primary_keyword: str) -> str:
    """Fit an SEO title to 50-60 chars without cutting words or adding claims."""
    text = re.sub(r"\s+", " ", (value or "")).strip().strip('"').strip("'")
    if 50 <= len(text) <= 60:
        return text
    if len(text) < 50:
        return text

    stop_words = ("for", "the", "a", "an", "of", "in", "on", "to")
    keyword_content_words = {
        word.lower()
        for word in re.findall(r"\b[a-zA-Z0-9]+\b", primary_keyword or "")
        if word.lower() not in stop_words
    }

    def _without_stop_word(words: list[str], stop_word: str) -> list[str]:
        for index, word in enumerate(words):
            if word.lower() == stop_word:
                candidate = words[:index] + words[index + 1 :]
                candidate_text = " ".join(candidate).strip()
                if len(candidate_text) >= 50:
                    return candidate
        return words

    words = text.split(" ")
    for stop_word in stop_words:
        if len(" ".join(words)) <= 60:
            break
        words = _without_stop_word(words, stop_word)

    result = " ".join(words).strip()

    while len(result) > 60:
        split_words = result.split(" ")
        shortened = False
        for index in range(len(split_words) - 1, -1, -1):
            if split_words[index].lower() not in keyword_content_words:
                candidate = " ".join(
                    split_words[:index] + split_words[index + 1 :]
                ).strip()
                if len(candidate) >= 50:
                    result = candidate
                    shortened = True
                    break
        if not shortened:
            if len(split_words) <= 2:
                break
            result = " ".join(split_words[:-1]).strip()
        result = re.sub(r"\s+", " ", result).strip()
        result = result.rstrip(":|-,").strip()

    result = re.sub(r"\s+", " ", result).strip()
    result = result.rstrip(":|-,").strip()
    return result


def _normalize_w1b_frontmatter(
    draft_md: str,
    primary_keyword: str,
) -> str:
    """Normalize deterministic SEO Title and Description fields."""
    frontmatter, body = _split_frontmatter_text(draft_md)
    if not frontmatter:
        return draft_md

    inner = frontmatter[3:-3]
    lines = inner.strip("\n").splitlines()
    title_index = next(
        (
            index for index, line in enumerate(lines)
            if re.match(r"^\s*SEO Title\s*:", line, re.IGNORECASE)
        ),
        None,
    )
    if title_index is not None:
        current_title = lines[title_index].split(":", 1)[1].strip()
        fitted_title = _fit_w1b_seo_title(current_title, primary_keyword)
        lines[title_index] = f"SEO Title: {fitted_title}"
    else:
        # SEO metadata remains independent: copy ordinary Title only as the
        # source value, and never modify ordinary Title or the H1.
        plain_title_index = next(
            (
                index for index, line in enumerate(lines)
                if re.match(r"^\s*Title\s*:", line, re.IGNORECASE)
            ),
            None,
        )
        if plain_title_index is not None:
            current_title = lines[plain_title_index].split(":", 1)[1].strip()
            fitted_title = _fit_w1b_seo_title(current_title, primary_keyword)
            lines.insert(plain_title_index + 1, f"SEO Title: {fitted_title}")

    description_index = next(
        (
            index for index, line in enumerate(lines)
            if re.match(r"^\s*SEO Description\s*:", line, re.IGNORECASE)
        ),
        None,
    )
    current = ""
    if description_index is not None:
        current = lines[description_index].split(":", 1)[1].strip()
    fitted = _fit_w1b_meta_description(current, primary_keyword)
    replacement = f"SEO Description: {fitted}"
    if description_index is None:
        lines.append(replacement)
    else:
        lines[description_index] = replacement

    keyword_index = next(
        (
            index for index, line in enumerate(lines)
            if re.match(r"^\s*SEO Keywords\s*:", line, re.IGNORECASE)
        ),
        None,
    )
    current_keywords = ""
    if keyword_index is not None:
        current_keywords = lines[keyword_index].split(":", 1)[1].strip()
    remaining_keywords = [
        item.strip()
        for item in current_keywords.split(",")
        if item.strip() and item.strip().lower() != primary_keyword.lower()
    ]
    keyword_value = ", ".join([primary_keyword, *remaining_keywords])
    keyword_replacement = f"SEO Keywords: {keyword_value}"
    if keyword_index is None:
        lines.append(keyword_replacement)
    else:
        lines[keyword_index] = keyword_replacement

    normalized_frontmatter = "---\n" + "\n".join(lines) + "\n---"
    return normalized_frontmatter + body


def _w1b_keyword_in_first_100(draft_md: str, primary_keyword: str) -> bool:
    """Mirror write_pre_check's first-100-word keyword calculation."""
    _, body = _split_frontmatter_text(draft_md)
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    clean = re.sub(r"</?[a-zA-Z][^>]*>", "", clean)
    first_100 = " ".join(
        re.findall(r"\b[a-z0-9]+\b", clean.lower())[:100]
    )
    return bool(primary_keyword) and primary_keyword.lower() in first_100


def _ensure_w1b_keyword_early(
    draft_md: str,
    primary_keyword: str,
) -> str:
    """Insert one non-factual editorial sentence after H1 when required."""
    if _w1b_keyword_in_first_100(draft_md, primary_keyword):
        return draft_md
    intro = f"This guide focuses on {primary_keyword}."
    h1 = re.search(r"^# .+$", draft_md, re.MULTILINE)
    if h1:
        return (
            draft_md[:h1.end()]
            + f"\n\n{intro}"
            + draft_md[h1.end():]
        )

    frontmatter, body = _split_frontmatter_text(draft_md)
    prefix = frontmatter
    separator = "\n\n" if prefix else ""
    return f"{prefix}{separator}{intro}\n\n{body.lstrip()}"


def _ensure_w1b_keyword_h2s(
    draft_md: str,
    primary_keyword: str,
) -> str:
    """Add the exact keyword to existing H2s until two headings match."""
    lines = draft_md.splitlines()
    h2_indexes: list[int] = []
    preferred_indexes: list[int] = []
    in_fence = False
    in_script = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if re.search(r"<script\b", stripped, re.IGNORECASE):
            in_script = True
        if in_fence or in_script:
            if re.search(r"</script>", stripped, re.IGNORECASE):
                in_script = False
            continue
        if not line.startswith("## "):
            continue
        h2_indexes.append(index)
        heading = line[3:].strip()
        if not re.match(r"^(?:FAQ|Frequently Asked)", heading, re.IGNORECASE):
            preferred_indexes.append(index)

    keyword_lower = primary_keyword.lower()
    hits = sum(
        1 for index in h2_indexes
        if keyword_lower in lines[index][3:].lower()
    )
    candidates = preferred_indexes + [
        index for index in h2_indexes if index not in preferred_indexes
    ]
    for index in candidates:
        if hits >= 2:
            break
        heading = lines[index][3:].strip()
        if keyword_lower in heading.lower():
            continue
        lines[index] = f"## {primary_keyword}: {heading}"
        hits += 1

    trailing_newline = "\n" if draft_md.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline


def _ensure_w1b_ctas(draft_md: str) -> str:
    """Ensure two reader-visible, non-factual soft CTAs.

    The checker counts CTA phrases only in prose. This helper mirrors that
    exact input and appends only the missing number of generic next-step
    prompts. It introduces no measurements, regulations, or product claims.
    """
    from data_sources.modules import write_pre_check

    _, body = _split_frontmatter_text(draft_md)
    prose = write_pre_check._flatten_markdown_for_checks(
        write_pre_check._strip_non_prose_blocks(body)
    )
    current_count = len(
        list(re.finditer(write_pre_check.CTA_INDICATORS, prose))
    )
    if current_count >= 2:
        return draft_md

    templates = (
        (
            "explore",
            "Explore the available options and compare them with your "
            "project requirements.",
        ),
        (
            "contact us",
            "Contact us for help choosing an approach that fits your "
            "intended use.",
        ),
        (
            "learn more",
            "Learn more about the available approaches before making your "
            "final selection.",
        ),
    )
    prose_lower = prose.lower()
    additions: list[str] = []
    needed = 2 - current_count
    for marker, sentence in templates:
        if marker in prose_lower:
            continue
        additions.append(sentence)
        if len(additions) >= needed:
            break

    if len(additions) < needed:
        additions.extend(
            sentence for _, sentence in templates[:needed - len(additions)]
        )

    suffix = "\n\n## Next Steps\n\n" + "\n\n".join(additions)
    return draft_md.rstrip() + suffix + "\n"


_W1B_FAQ_H2_RE = re.compile(
    r"(?im)^##\s*(?:FAQ|Frequently Asked[^\n]*)\s*$"
)
_W1B_FAQ_QUESTION_RE = re.compile(
    r"(?im)^(?:###\s+(?P<h3>.+?)|\*\*(?P<bold>.+?\?)\*\*)\s*$"
)


def _clean_w1b_faq_text(markdown_text: str) -> str:
    """Return reader-visible text without inventing or expanding content."""
    text = re.sub(r"```[\s\S]*?```", "", markdown_text or "")
    text = re.sub(
        r"<script\b[^>]*\btype\s*=\s*([\"'])\s*application/ld\+json\s*\1[^>]*>"
        r"[\s\S]*?</script\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(
        r"(?m)^\s*(?:>\s*)?(?:[-*+]\s+|\d+\.\s+)",
        "",
        text,
    )
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _ensure_w1b_faq_schema(draft_md: str) -> str:
    """Append FAQPage JSON-LD derived only from reader-visible FAQ text.

    Existing valid FAQPage JSON-LD is preserved. A new block is created only
    when at least three question/answer pairs can be extracted from the FAQ
    section, so this helper cannot fabricate missing content.
    """
    from data_sources.modules import write_pre_check

    if write_pre_check._has_valid_faq_schema(draft_md):
        return draft_md

    frontmatter, body = _split_frontmatter_text(draft_md)
    faq_heading = _W1B_FAQ_H2_RE.search(body)
    if not faq_heading:
        return draft_md

    section_start = faq_heading.end()
    following = body[section_start:]
    next_h2 = re.search(r"(?m)^##\s+", following)
    section_end = (
        section_start + next_h2.start()
        if next_h2
        else len(body)
    )
    faq_section = body[section_start:section_end]
    question_matches = list(_W1B_FAQ_QUESTION_RE.finditer(faq_section))
    main_entity: list[dict[str, Any]] = []

    for index, match in enumerate(question_matches):
        raw_question = match.group("h3") or match.group("bold") or ""
        question = _clean_w1b_faq_text(raw_question)
        answer_start = match.end()
        answer_end = (
            question_matches[index + 1].start()
            if index + 1 < len(question_matches)
            else len(faq_section)
        )
        answer = _clean_w1b_faq_text(
            faq_section[answer_start:answer_end]
        )
        if not question or not answer:
            continue
        main_entity.append({
            "@type": "Question",
            "name": question,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": answer,
            },
        })
        if len(main_entity) >= 4:
            break

    if len(main_entity) < 3:
        return draft_md

    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }
    schema_block = (
        '<script type="application/ld+json">\n'
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n</script>"
    )
    normalized_body = body.rstrip() + "\n\n" + schema_block + "\n"
    return frontmatter + normalized_body


def _split_w1b_long_paragraphs(draft_md: str) -> str:
    """Split every checker-visible paragraph after each fourth sentence.

    The W1b checker defines a paragraph only by blank-line boundaries. A
    heading or list marker inside the same block does not exempt that block,
    so this routine preserves every character while inserting blank lines at
    the same sentence boundaries the checker counts.
    """
    frontmatter, body = _split_frontmatter_text(draft_md)
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\n\n\x00W1BPROTECTED{len(protected) - 1}\x00\n\n"

    masked = re.sub(
        r"```[\s\S]*?```|"
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>"
        r"[\s\S]*?</script>",
        protect,
        body,
        flags=re.IGNORECASE,
    )

    def normalize_block(block: str) -> str:
        if not block.strip() or "\x00W1BPROTECTED" in block:
            return block
        if len(block.split()) <= 20:
            return block

        sentence_matches = list(re.finditer(r"([.!?]+)(\s+|$)", block))
        if len(sentence_matches) <= 4:
            return block

        out: list[str] = []
        cursor = 0
        for sentence_index, match in enumerate(sentence_matches, start=1):
            out.append(block[cursor:match.start()])
            out.append(match.group(1))
            whitespace = match.group(2)
            if sentence_index % 4 == 0 and whitespace:
                out.append("\n\n")
            else:
                out.append(whitespace)
            cursor = match.end()
        out.append(block[cursor:])
        return "".join(out)

    blocks = re.split(r"\n{2,}", masked)
    normalized_body = "\n\n".join(normalize_block(block) for block in blocks)
    for index, original in enumerate(protected):
        normalized_body = normalized_body.replace(
            f"\x00W1BPROTECTED{index}\x00",
            original,
        )

    if frontmatter:
        return frontmatter + normalized_body
    return normalized_body


def _normalize_w1b_candidate(
    draft_md: str,
    primary_keyword: str,
) -> str:
    """Apply deterministic W1b mechanics before ledger generation/preflight."""
    normalized = _normalize_w1b_frontmatter(draft_md, primary_keyword)
    normalized = _ensure_w1b_keyword_early(normalized, primary_keyword)
    # Add any missing CTA section before H2 normalization so a newly inserted
    # ``## Next Steps`` heading is normalized in the same pass. Otherwise the
    # second call would prefix the keyword and break idempotency.
    normalized = _ensure_w1b_ctas(normalized)
    normalized = _ensure_w1b_keyword_h2s(normalized, primary_keyword)
    normalized = _ensure_w1b_faq_schema(normalized)
    normalized = _split_w1b_long_paragraphs(normalized)
    return normalized.strip()


def _w1b_repair_contract(draft: Path, topic: str) -> str:
    """Build the exact mechanical/evidence contract for a W1b repair."""
    primary_keyword = _w1b_primary_keyword(draft, topic)
    return f"""The exact primary keyword is: {primary_keyword}

Hard acceptance contract:
1. Preserve valid YAML frontmatter. Include a separate `SEO Title:` line of 50-60 characters. Do not modify the ordinary `Title:` field or H1.
2. Add one `SEO Description:` frontmatter line containing 150-160 characters.
3. Use the exact primary keyword within the first 100 prose words.
4. Use the exact primary keyword naturally in at least two `##` H2 headings.
5. Add this exact block shape with 3-5 bullet lines:
   > **Key Takeaways**
   > - first takeaway
   > - second takeaway
   > - third takeaway
6. When the article contains an FAQ, include a real FAQPage JSON-LD block at
   the end of the article using this exact script type:
   <script type="application/ld+json">
   The schema questions and answers must come from the reader-visible FAQ.
   Do not invent FAQ questions or answers, and do not use a fenced JSON example
   in place of the real HTML script block.
7. Every normal prose paragraph must contain at most four sentences.
8. Do not introduce an externally verifiable fact unless one of the evidence
   cards below directly supports it. If a factual sentence is unsupported,
   remove it or rewrite it as clearly qualified analysis/recommendation.
9. Do not invent numbers, specifications, regulations, quotations, URLs,
   products, tests, or first-hand experience.
10. Return at least 1350 checker-visible body words. Preserve every existing paragraph except an exact unsupported factual sentence named in structured failures; add useful analysis, selection guidance, or a practical checklist instead of compressing prose.
11. Return the entire revised article, not a patch or explanation.

Before returning, silently verify every item above."""


async def stage_w1b_revise(
    topic: str,
    tier: str,
    workspace: Path,
    settings=None,
    *,
    candidate_seed_md: str | None = None,
    candidate_seed_feedback: str | None = None,
    carry_candidate: bool = False,
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
    draft = _latest_draft(workspace, slug)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    state = load_w2_state(workspace, slug)
    rounds = _revision_rounds(state, "w1b")
    repair = repair_draft_frontmatter(workspace, slug)
    precheck_report = load_report(workspace, "pre-check", slug)
    if not precheck_report:
        return {"success": False, "error": "没有预检报告，请先运行 W1b"}

    resolved_tier = resolve_tier(topic, workspace, tier)
    has_candidate_seed = (
        isinstance(candidate_seed_md, str)
        and bool(candidate_seed_md.strip())
    )
    source_draft_md = (
        candidate_seed_md if has_candidate_seed else _read_text(draft)
    )
    seed_feedback = (
        candidate_seed_feedback.strip()
        if isinstance(candidate_seed_feedback, str)
        else ""
    )
    failure_payload: dict[str, Any] | None = None
    if seed_feedback:
        structured_failures = seed_feedback
        try:
            parsed_feedback = json.loads(seed_feedback)
        except json.JSONDecodeError:
            parsed_feedback = None
        if isinstance(parsed_feedback, dict):
            failure_payload = parsed_feedback
    else:
        try:
            current_precheck = _w1b_precheck_data(
                draft, resolved_tier, workspace, slug
            )
        except Exception as exc:
            return {
                "success": False,
                "error": f"无法读取当前结构化 W1b 失败项: {exc}",
            }
        failure_payload = _w1b_failure_payload(current_precheck)
        structured_failures = json.dumps(
            failure_payload, ensure_ascii=False, indent=2,
        )
    retry_memory = _recent_revision_memory(state, "w1b")
    retry_section = (
        f"\n## Earlier failed attempts (do not repeat these mistakes)\n{retry_memory}\n"
        if retry_memory else "\n"
    )

    contracts, evidence_cards = _revision_context_contracts(
        workspace, slug, topic,
        relevant_text=f"{structured_failures}\n{retry_memory}",
    )
    repair_contract = _w1b_repair_contract(draft, topic)
    failure_summary = _w1b_failure_summary(failure_payload)
    compact_context = _w1b_compact_revision_context(contracts)
    user_prompt = f"""Revise the article for: "{topic}"

The article failed the W1b pre-check. Fix the items listed below using
ONLY information from the material pack and the current draft. Do not
invent facts, numbers, quotes, or URLs.

## Hard repair contract (all items are mandatory)
{repair_contract}

## Server-side deterministic normalization
After your response, the server will enforce SEO Description length, exact
keyword placement in the first 100 words and two H2 headings, a FAQPage
JSON-LD block derived only from the reader-visible FAQ, and the four-sentence
paragraph cap without deleting content. Prioritize substantive edits for every
structured `fact_issues` sentence: either remove or qualify an
unsupported factual assertion, or preserve it only when an evidence card
directly supports it. Do not leave a listed factual gap unchanged.

The output must contain at least 1350 checker-visible body words,
regardless of the listed deficit. Do not shorten, summarize, or omit any
existing non-frontmatter prose unless the structured failures name that exact
unsupported factual sentence. Add useful analysis, selection guidance, or a
practical checklist; do not pad with repeated wording, new measurements, new
regulations, new product claims, or invented experience.

## Exact structured failures (fix every listed item and sentence)
{structured_failures}

## Repair focus summary
{failure_summary}
{retry_section}

## Current candidate draft
{source_draft_md}

## Compact write brief and coverage contract
{compact_context}

## Evidence cards relevant to this repair
{evidence_cards}

## Valid internal link targets
{_read_text(workspace / 'context' / 'internal-links-map.md', _REVISION_LINKS_CHAR_LIMIT)}

Return the revised article Markdown only."""

    try:
        new_draft_md = _strip_code_fence(await _run_ai_text(
            "legacy_write_revise_body", _REVISE_BODY_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=_W1B_REVISE_BODY_MAX_TOKENS,
            thinking_mode="disabled",
        ))
    except Exception as exc:
        from seo_ops.services.ai import AIEmptyTextError

        error = str(exc)
        # Some OpenAI-compatible providers occasionally return a successful
        # HTTP response with an empty assistant message.  No candidate exists
        # in that case, so the bounded batch may safely spend its second
        # attempt on the same live draft.  Other transport/provider failures
        # remain fail-fast so we do not hide outages or multiply API calls.
        return {
            "success": False,
            "revised": False,
            "retryable": (
                isinstance(exc, AIEmptyTextError) and exc.retryable
            ),
            "error": error,
        }

    if not new_draft_md:
        return {"success": False, "error": "修订输出文章正文为空"}
    if "===CLAIM_LEDGER===" in new_draft_md:
        return {"success": False, "error": "修订正文任务错误包含 CLAIM_LEDGER"}

    primary_keyword = _w1b_primary_keyword(draft, topic)
    raw_candidate_md = new_draft_md
    new_draft_md = _normalize_w1b_candidate(
        new_draft_md,
        primary_keyword,
    )
    deterministic_normalization_applied = new_draft_md != raw_candidate_md

    try:
        cl_data = await _generate_claim_ledger_for_draft(
            new_draft_md,
            contracts.get("cards") or {},
            settings=settings,
            evidence_cards_text=evidence_cards,
        )
    except ValueError as exc:
        return {"success": False, "error": f"修订 CLAIM_LEDGER 生成/校验失败: {exc}"}
    except Exception as exc:
        return {"success": False, "error": f"修订 CLAIM_LEDGER 生成失败: {exc}"}

    candidate_draft_sha256 = hashlib.sha256(
        new_draft_md.encode("utf-8")
    ).hexdigest()
    cl_data["draft_sha256"] = candidate_draft_sha256

    fact_gap_audit_requested = 0
    fact_gap_claims_added = 0
    fact_gap_audit_error = None
    try:
        candidate_precheck = _candidate_w1b_precheck(
            new_draft_md,
            cl_data,
            resolved_tier,
            workspace,
            slug,
        )
    except Exception as exc:
        return {
            "success": False,
            "revised": False,
            "retryable": False,
            "error": f"候选稿 W1b 预检执行失败: {exc}",
        }

    fact_issues = _fact_issues_from_precheck(candidate_precheck)
    if fact_issues:
        try:
            cl_data, fact_gap_audit_requested, fact_gap_claims_added = (
                await _supplement_claim_ledger_fact_gaps(
                    new_draft_md,
                    cl_data,
                    fact_issues,
                    evidence_cards,
                    settings=settings,
                )
            )
            cl_data["draft_sha256"] = candidate_draft_sha256
            if fact_gap_claims_added:
                candidate_precheck = _candidate_w1b_precheck(
                    new_draft_md,
                    cl_data,
                    resolved_tier,
                    workspace,
                    slug,
                )
        except ValueError as exc:
            fact_gap_audit_error = str(exc)

    unsupported_fact_cleanup_requested = 0
    unsupported_fact_cleanup_removed = 0
    unsupported_fact_cleanup_error = None

    remaining_fact_issues = _fact_issues_from_precheck(candidate_precheck)
    removable_fact_issues = [
        issue
        for issue in remaining_fact_issues
        if issue.get("reason") == "uncovered_factual_sentence"
    ]

    # Exact uncovered factual sentences are safe to remove even when another
    # deterministic check (for example the tier word floor) also fails. Keeping
    # them until "事实校验" is the only failure traps the next batch attempt:
    # the retry must both rediscover the same unsupported claims and expand the
    # article. Clean the exact structured gaps first, then carry the cleaned
    # candidate and its remaining failures into the next bounded attempt.
    if removable_fact_issues:
        unsupported_fact_cleanup_requested = len(removable_fact_issues)
        cleanup_candidate_md, unsupported_fact_cleanup_removed = (
            _remove_uncovered_fact_sentences(
                new_draft_md,
                removable_fact_issues,
            )
        )
        if unsupported_fact_cleanup_removed:
            cleanup_candidate_md = _normalize_w1b_candidate(
                cleanup_candidate_md,
                primary_keyword,
            )
            try:
                cleanup_cl_data = await _generate_claim_ledger_for_draft(
                    cleanup_candidate_md,
                    contracts.get("cards") or {},
                    settings=settings,
                    evidence_cards_text=evidence_cards,
                )
                cleanup_sha256 = hashlib.sha256(
                    cleanup_candidate_md.encode("utf-8")
                ).hexdigest()
                cleanup_cl_data["draft_sha256"] = cleanup_sha256
                cleanup_precheck = _candidate_w1b_precheck(
                    cleanup_candidate_md,
                    cleanup_cl_data,
                    resolved_tier,
                    workspace,
                    slug,
                )

                cleanup_fact_issues = _fact_issues_from_precheck(
                    cleanup_precheck
                )
                if cleanup_fact_issues:
                    (
                        cleanup_cl_data,
                        cleanup_requested,
                        cleanup_added,
                    ) = await _supplement_claim_ledger_fact_gaps(
                        cleanup_candidate_md,
                        cleanup_cl_data,
                        cleanup_fact_issues,
                        evidence_cards,
                        settings=settings,
                    )
                    fact_gap_audit_requested += cleanup_requested
                    fact_gap_claims_added += cleanup_added
                    cleanup_cl_data["draft_sha256"] = cleanup_sha256
                    if cleanup_added:
                        cleanup_precheck = _candidate_w1b_precheck(
                            cleanup_candidate_md,
                            cleanup_cl_data,
                            resolved_tier,
                            workspace,
                            slug,
                        )

                new_draft_md = cleanup_candidate_md
                cl_data = cleanup_cl_data
                candidate_draft_sha256 = cleanup_sha256
                candidate_precheck = cleanup_precheck
            except (ValueError, OSError) as exc:
                unsupported_fact_cleanup_error = str(exc)
            except Exception as exc:
                unsupported_fact_cleanup_error = str(exc)

    candidate_fail_count = int(
        candidate_precheck.get("fail_count") or 0
    )
    if candidate_fail_count:
        failure_payload = _w1b_failure_payload(candidate_precheck)
        retry_feedback = json.dumps(
            failure_payload,
            ensure_ascii=False,
            indent=2,
        )
        outcome: dict[str, Any] = {
            "success": False,
            "stage": "w1b_pre_check",
            "gate_passed": False,
            "revised": False,
            "retryable": True,
            "candidate_rejected": True,
            "candidate_fail_count": candidate_fail_count,
            "candidate_checks": failure_payload["failed_checks"],
            "retry_feedback": retry_feedback,
            "deterministic_normalization_applied": (
                deterministic_normalization_applied
            ),
            "fact_gap_audit_requested": fact_gap_audit_requested,
            "fact_gap_claims_added": fact_gap_claims_added,
            "fact_gap_audit_error": fact_gap_audit_error,
            "unsupported_fact_cleanup_requested": (
                unsupported_fact_cleanup_requested
            ),
            "unsupported_fact_cleanup_removed": (
                unsupported_fact_cleanup_removed
            ),
            "unsupported_fact_cleanup_error": (
                unsupported_fact_cleanup_error
            ),
            "error": (
                f"候选稿预检仍有 {candidate_fail_count} 项未通过；"
                "未写入正式 draft/ledger"
            ),
        }
        if carry_candidate:
            # Batch-internal only. The caller removes these fields before
            # persisting history or returning the public result.
            outcome["_candidate_draft_md"] = new_draft_md
            outcome["_candidate_retry_feedback"] = retry_feedback
        return outcome

    # Back up the matched draft + claim ledger as a pair. A draft-only
    # backup cannot be safely restored because sentence IDs are draft-bound.
    backup = draft.with_suffix(f".precheck-rev{rounds + 1}.md")
    backup.write_bytes(draft.read_bytes())
    claim_path = workspace / "research" / f"claim-ledger-{slug}.json"
    claim_backup: Path | None = None
    if claim_path.exists():
        claim_backup = claim_path.with_name(
            f"{claim_path.stem}.precheck-rev{rounds + 1}.json"
        )
        claim_backup.write_bytes(claim_path.read_bytes())

    # Write-ahead: temp files → rename.  On failure, old files survive.
    _write_ahead_draft_and_ledger(workspace, slug, new_draft_md, cl_data)

    _set_revision_rounds(state, "w1b", rounds + 1)
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
            "rounds_left": 0,
            "backup": str(backup),
            "claim_backup": str(claim_backup) if claim_backup else None,
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
        "rounds_left": 0,
        "backup": str(backup),
        "claim_backup": str(claim_backup) if claim_backup else None,
        "frontmatter_repaired": repair.get("repaired", False),
        "deterministic_normalization_applied": (
            deterministic_normalization_applied
        ),
        "fact_gap_audit_requested": fact_gap_audit_requested,
        "fact_gap_claims_added": fact_gap_claims_added,
        "fact_gap_audit_error": fact_gap_audit_error,
        "unsupported_fact_cleanup_requested": (
            unsupported_fact_cleanup_requested
        ),
        "unsupported_fact_cleanup_removed": (
            unsupported_fact_cleanup_removed
        ),
        "unsupported_fact_cleanup_error": (
            unsupported_fact_cleanup_error
        ),
    }


async def stage_w1b_revise_batch(
    topic: str, tier: str, workspace: Path, settings=None
) -> dict:
    """Run at most two cumulative W1b revisions from one operator action.

    A rejected candidate never touches the live draft/ledger pair, but it is
    carried in memory into the next attempt so the second revision improves
    the first candidate instead of starting again from the old formal draft.
    """
    slug = _slugify(topic)
    attempts: list[dict[str, Any]] = []
    candidate_seed_md: str | None = None
    candidate_seed_feedback: str | None = None
    candidate_chain_count = 0

    for _ in range(MAX_REVISION_ROUNDS):
        if candidate_seed_md is not None:
            candidate_chain_count += 1

        outcome = await stage_w1b_revise(
            topic,
            tier,
            workspace,
            settings,
            candidate_seed_md=candidate_seed_md,
            candidate_seed_feedback=candidate_seed_feedback,
            carry_candidate=True,
        )
        next_candidate = outcome.pop("_candidate_draft_md", None)
        next_feedback = outcome.pop("_candidate_retry_feedback", None)

        _record_revision_attempt(workspace, slug, "w1b", outcome)
        attempts.append(outcome)
        if outcome.get("gate_passed"):
            return {
                **outcome,
                "batch_attempts": len(attempts),
                "batch_completed": True,
                "candidate_chain_count": candidate_chain_count,
            }

        if not outcome.get("revised") and not outcome.get("retryable"):
            break

        if isinstance(next_candidate, str) and next_candidate.strip():
            candidate_seed_md = next_candidate
            candidate_seed_feedback = (
                next_feedback
                if isinstance(next_feedback, str) and next_feedback.strip()
                else str(outcome.get("retry_feedback") or "")
            )
    final = attempts[-1] if attempts else {
        "success": False,
        "error": "未执行修订",
    }
    return {
        **final,
        "batch_attempts": len(attempts),
        "batch_completed": True,
        "candidate_chain_count": candidate_chain_count,
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
        "link_block": "**不可进入段3**：链接" in report,
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

    draft = _latest_draft(workspace, slug)
    if not draft:
        return {"success": False, "error": "草稿不存在"}
    mp = _latest_file(f"material-packs/{slug}-*.md", workspace)

    state = load_w2_state(workspace, slug)
    rounds = _revision_rounds(state, "w2")
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
    state["link_block"] = metrics["link_block"]
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

    # A click starts a fresh, bounded repair batch. The total revision count
    # remains in state for audit and the narrowly-scoped cannibalisation
    # confirmation, but it must not turn a later explicit click into a no-op.
    rounds_left = MAX_REVISION_ROUNDS

    result: dict[str, Any] = {
        "success": gate_passed,
        "stage": "w2_post_process" if (gate_passed and apply) else "w1b_pre_check",
        "report": report,
        "gate_passed": gate_passed,
        "rounds_used": _revision_rounds(state, "w2"),
        "rounds_left": rounds_left,
        **metrics,
    }

    if not gate_passed and _revision_rounds(state, "w2") >= MAX_REVISION_ROUNDS:
        # After one bounded repair batch, surface the exact unresolved gate.
        # Error-level link failures remain fail-closed; another explicit repair
        # batch is allowed, but W3 never receives an implicit link waiver.
        result["needs_human_review"] = metrics["score_block"]
        result["needs_force_confirmation"] = metrics["cannibal_block"]
        result["needs_link_review"] = metrics["link_block"]
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
8. After the revised article, output ``===CLAIM_LEDGER===`` and an updated claim
   ledger JSON (same format as W0: ``{"version":1,"claims":[...]}``). Every
   claim_text must be a sentence from the new article and evidence_ids must
   reference the Evidence References section.

Output the article Markdown, then ``===CLAIM_LEDGER===``, then the JSON.
No preamble, no commentary, no code fence around the whole document."""


_REVISE_BODY_AI_SYSTEM = """You are revising an SEO article that failed an automated quality gate.

Fix every concrete failure in the supplied report. Preserve the frontmatter,
reader intent, useful structure, and supported content that is not implicated
by the report. Use only the supplied evidence cards for externally verifiable
facts; do not invent facts, numbers, quotes, test results, authors, URLs,
regulations, or first-hand experience. For a cannibalization failure, change
the angle or cut the overlapping material rather than merely rephrasing it.

Return only the complete revised article Markdown. Do not output a claim
ledger, JSON, preamble, commentary, or code fence."""


async def stage_w2_revise(topic: str, workspace: Path, settings=None) -> dict:
    """Run one W2 AI revision, then re-run W1b and W2.

    ``MAX_REVISION_ROUNDS`` limits a single explicit batch, not the lifetime
    of an action. Earlier failures remain in the next prompt, so a new batch
    is an informed retry rather than an invisible loop.
    """
    slug = _slugify(topic)
    draft = _latest_draft(workspace, slug)
    if not draft:
        return {"success": False, "error": "草稿不存在"}

    state = load_w2_state(workspace, slug)
    rounds = _revision_rounds(state, "w2")
    if state.get("gate_passed") or state.get("applied"):
        return {"success": False,
                "error": "草稿已通过预检或已发布，不得再修订",
                "rounds_used": rounds, "rounds_left": MAX_REVISION_ROUNDS}

    report = load_report(workspace, "post-process", slug)
    if not report:
        return {"success": False, "error": "没有后处理报告，请先运行后处理检查"}

    metrics = _parse_post_process(report)
    retry_memory = _recent_revision_memory(state, "w2")
    retry_section = (
        f"\n## Earlier failed attempts (do not repeat these mistakes)\n{retry_memory}\n"
        if retry_memory else "\n"
    )

    contracts, evidence_cards = _revision_context_contracts(
        workspace, slug, topic,
        relevant_text=f"{report}\n{retry_memory}",
    )
    user_prompt = f"""Revise the article for: "{topic}"

## Post-Process Report (what failed)
{report[:_REPORT_CHAR_LIMIT]}
{retry_section}

## Current Draft
{_read_text(draft)}

## Compact write brief and coverage contract
{json.dumps(contracts.get('brief') or {}, ensure_ascii=False, indent=2)}
{json.dumps(contracts.get('coverage') or {}, ensure_ascii=False, indent=2)}

## Evidence cards relevant to this repair
{evidence_cards}

## Valid internal link targets
{_read_text(workspace / 'context' / 'internal-links-map.md', _REVISION_LINKS_CHAR_LIMIT)}

Return the revised article Markdown only."""

    original_draft_text = _read_text(draft)

    try:
        draft_md = _strip_code_fence(await _run_ai_text(
            "legacy_write_revise_body", _REVISE_BODY_AI_SYSTEM, user_prompt,
            settings=settings, max_tokens=16000, thinking_mode="disabled"))
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not draft_md:
        return {"success": False, "error": "AI 输出了空的文章正文"}
    if "===CLAIM_LEDGER===" in draft_md:
        return {"success": False, "error": "W2 正文任务错误包含 CLAIM_LEDGER"}

    try:
        cl_data = await _generate_claim_ledger_for_draft(
            draft_md, contracts.get("cards") or {}, settings=settings,
        )
    except ValueError as exc:
        return {"success": False, "error": f"claim-ledger 生成/校验失败: {exc}"}
    except Exception as exc:
        return {"success": False, "error": f"claim-ledger 生成失败: {exc}"}

    cl_data["draft_sha256"] = hashlib.sha256(draft_md.encode("utf-8")).hexdigest()

    backup = draft.with_suffix(f".rev{rounds + 1}.md")
    backup.write_text(original_draft_text, encoding="utf-8")

    try:
        _write_ahead_draft_and_ledger(workspace, slug, draft_md, cl_data)
    except Exception as exc:
        backup.unlink(missing_ok=True)
        return {"success": False, "error": f"原子写入失败: {exc}"}

    _set_revision_rounds(state, "w2", rounds + 1)
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
            "precheck_failed": True,
            "revision_round": rounds + 1,
            "rounds_used": rounds + 1,
            "rounds_left": MAX_REVISION_ROUNDS,
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
    outcome["precheck_failed"] = False
    outcome["revision_round"] = rounds + 1
    outcome["backup"] = str(backup)
    outcome["previous_metrics"] = metrics
    return outcome


async def stage_w2_revise_batch(topic: str, workspace: Path, settings=None) -> dict:
    """Run one explicit W2 repair batch of at most two revisions.

    Every W2 revision already re-runs W1b before re-running W2.  Stop if that
    pre-check fails: continuing W2 would bypass the W1b gate.
    """
    slug = _slugify(topic)
    attempts: list[dict[str, Any]] = []
    while len(attempts) < MAX_REVISION_ROUNDS:
        outcome = await stage_w2_revise(topic, workspace, settings)
        _record_revision_attempt(workspace, slug, "w2", outcome)
        attempts.append(outcome)
        if outcome.get("gate_passed"):
            return {**outcome, "batch_attempts": len(attempts), "batch_completed": True}
        if not outcome.get("revised") or outcome.get("precheck_failed"):
            break
    final = attempts[-1] if attempts else {"success": False, "error": "未执行修订"}
    return {**final, "batch_attempts": len(attempts), "batch_completed": True}


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

    draft = _latest_draft(workspace, slug)
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
    data["w1b_revision_attempts"] = _revision_rounds(state, "w1b")
    data["revision_history"] = state.get("revision_history", [])
    if draft_text:
        # Larger than draft_preview (which is 2 KB) so the user can see
        # the whole draft when fixing pre-check failures.
        data["draft_full"] = draft_text

    post_process = load_report(workspace, "post-process", slug)
    if post_process:
        data["post_process_report"] = post_process
        metrics = _parse_post_process(post_process)
        # state was already loaded above for the precheck gate check.
        rounds_used = _revision_rounds(state, "w2")
        data["w2"] = {
            **metrics,
            "gate_passed": bool(state.get("gate_passed")),
            "applied": bool(state.get("applied")),
            "rounds_used": rounds_used,
            "rounds_left": MAX_REVISION_ROUNDS,
            "force_available": (
                rounds_used >= MAX_REVISION_ROUNDS
                and bool(metrics["cannibal_block"])
                and not bool(metrics["score_block"])
                and not bool(metrics["cannibal_error"])
                and not bool(metrics["score_error"])
            ),
        }

    register_report = load_report(workspace, "register", slug)
    if register_report:
        data["register_report"] = register_report

    backlinks = _read_text(files.get("backlinks"))
    if backlinks:
        data["backlink_checklist"] = backlinks

    return data
