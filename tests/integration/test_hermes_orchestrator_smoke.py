"""End-to-end smoke test for the seo-ops-orchestrator Hermes skill.

Exercises the 7 Legacy stages (r0, r1, r3, w0, w1b, w2, w3) using
synthetic fixtures and an isolated temporary database. No real network
calls are made; the SEO Ops AI helpers are monkey-patched to return
deterministic fixtures, so the entire pipeline runs end-to-end without
calling DeepSeek, SerpAPI, Firecrawl, or Tavily.

Verifies (per the user's smoke-test checklist):

1. action creation and state reads
2. R0 -> R1 -> R3 -> W0 -> W1b -> W2 -> W3
3. interrupt and resume (restart TestClient mid-flow)
4. re-run a stage invalidates downstream artifacts
5. two actions remain isolated (no shared files)
6. failures don't reuse stale success artifacts

Requires: pytest, fastapi.testclient (already in dev deps).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seo_ops.config import Settings
from seo_ops.db import connection as db_connection
from seo_ops.db import init_db
from seo_ops.web.app import create_app

from tests.legacy_workflow_helpers import (
    ai_text,
    install_synthetic_workspace,
    read_legacy_stage_from_db,
    shell_skill_script,
)


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "legacy_pipeline"
SKILL_DIR = FIXTURE_DIR / "skill_source" / "seo-ops-orchestrator"
ACTION_SLUG = "example-topic-for-testing-the-legacy-pipeline"


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_path=data_dir / "seo_ops.db",
        snapshots_dir=data_dir / "snapshots",
        host="127.0.0.1",
        port=8787,
        timezone="UTC",
        ai_provider="openai-compatible",
        ai_base_url=None,
        ai_api_key=None,
        ai_model=None,
    )


def _bootstrap_db(settings: Settings) -> int:
    """Apply schema + sample fixture actions.sqlite3.sql, return action_id."""
    init_db(settings)
    fixture_sql = (FIXTURE_DIR / "action_sample" / "actions.sqlite3.sql").read_text()
    with db_connection(settings) as conn:
        conn.executescript(fixture_sql)
        conn.commit()
        action_id = conn.execute(
            "SELECT id FROM actions WHERE workflow_status='planned'"
        ).fetchone()[0]
    return action_id


def _install_workspace(tmp_path: Path, monkeypatch) -> Path:
    # Point LEGACY_MODULES_DIR to the temp fixture dir so the subprocess
    # scripts resolve. LEGACY_WS itself is derived from active_settings.data_dir
    # inside create_app(), which already points to tmp_path/data.
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.PROJECT_ROOT", tmp_path
    )
    workspace = tmp_path / "data" / "legacy_workflow" / "laserpointerhub"
    install_synthetic_workspace(workspace)
    return workspace


def _stage_routes(app, action_id: int):
    """Return (client, routes-by-stage) for an in-process SEO Ops."""
    client = TestClient(app)
    routes = {
        "r0": f"/actions/{action_id}/legacy/stage/r0",
        "r1": f"/actions/{action_id}/legacy/stage/r1",
        "r3": f"/actions/{action_id}/legacy/stage/r3",
        "w0": f"/actions/{action_id}/legacy/stage/w0",
        "w1b": f"/actions/{action_id}/legacy/stage/w1b",
        "w2": f"/actions/{action_id}/legacy/stage/w2",
        "w3": f"/actions/{action_id}/legacy/stage/w3",
    }
    return client, routes


def _action_workspace(workspace_root: Path, action_id: int) -> Path:
    """Resolve the persistent per-action workspace path."""
    return (
        workspace_root
        / "runs"
        / f"action-{action_id}"
        / "current"
        / "laserpointerhub"
    )


def _run_stage(client, route: str, *, form: dict | None = None) -> int:
    """POST a stage; return HTTP status."""
    response = client.post(route, data=form or {}, follow_redirects=False)
    return response.status_code


# ── 1. action creation and state reads ──────────────────────────────


def test_action_creation_and_state_read(tmp_path):
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)

    app = create_app(settings)
    client, _ = _stage_routes(app, action_id)

    # State read via /api/health (most reliable read endpoint).
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # State read via DB (canonical source of truth).
    stage = read_legacy_stage_from_db(settings, action_id)
    assert stage == "r0_pending"

    # State read via /actions page (the way operators see it).
    page = client.get("/actions")
    assert page.status_code == 200
    assert "尚未开始" in page.text  # Chinese label for r0_pending


# ── 2. R0 -> R1 -> R3 -> W0 -> W1b -> W2 -> W3 ────────────────────────


def test_full_pipeline_runs_all_seven_stages(tmp_path, monkeypatch):
    """Run all 7 stages in sequence with synthetic AI/text fixtures."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    workspace = _install_workspace(tmp_path, monkeypatch)

    # Patch AI helpers and scripts to return deterministic synthetic output.
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow._run_ai_text",
        ai_text,
    )

    # Skip the run_sync shim: write_collector.py and write_pre_check.py
    # are real scripts; we monkey-patch them to return canned JSON/MD.
    def fake_run_sync(self, script, args):
        name = Path(script).name
        if name == "research_collector.py":
            cmd = args[0] if args else ""
            if cmd == "collect":
                # Mimic the script writing research-data-{slug}-{date}.{md,json}
                slug = args[args.index("--topic") + 1].lower().replace(" ", "-")[:80]
                from datetime import UTC, datetime
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                research_dir = self.workspace / "research"
                research_dir.mkdir(parents=True, exist_ok=True)
                (research_dir / f"research-data-{slug}-{today}.md").write_text(
                    "# research-data (synthetic)\n\nfake content\n",
                    encoding="utf-8",
                )
                (research_dir / f"research-data-{slug}-{today}.json").write_text(
                    '{"topic": "fake", "slug": "' + slug + '"}',
                    encoding="utf-8",
                )
                return ("", "", 0)
            return ("", "unknown command", 1)
        if name == "research_scorer.py":
            output_path = Path(args[args.index("--output") + 1])
            output_path.write_text(
                "deterministic opportunity score: 0.78\nfactors: demand=0.6 "
                "rank=0.5 competition=0.8 intent=0.9 gap=0.9 link=0.7\n",
                encoding="utf-8",
            )
            return ("", "", 0)
        if name == "write_collector.py":
            cmd = args[0] if args else ""
            if cmd == "validate":
                return ("mandatory_ok: True\n", "", 0)
            if cmd == "post-process":
                return (
                    "## 质量评分\n- 总分: 84.5 → ✅ 通过\n\n## 🚦 总门控\n- ✅ 通过",
                    "",
                    0,
                )
            if cmd == "register":
                # Mimic register appending ## 回溯链接候选 to draft
                draft = Path(args[args.index("--draft") + 1])
                if draft.exists():
                    existing = draft.read_text(encoding="utf-8")
                    draft.write_text(
                        existing
                        + "\n\n## 回溯链接候选（AI 请读取旧文章后写清单）\n\n**#1 — Existing**\n",
                        encoding="utf-8",
                    )
                return ("✅ 注册完成", "", 0)
            return ("", "unknown command", 1)
        if name == "write_pre_check.py":
            return (
                json.dumps(
                    {
                        "word_count": 1842,
                        "warn_count": 0,
                        "checks": [
                            {
                                "item": "字数",
                                "pass": True,
                                "level": "ok",
                                "detail": "1842 ≥ 1200",
                            },
                            {
                                "item": "H2 含主关键词",
                                "pass": True,
                                "level": "ok",
                                "detail": "2 hits",
                            },
                        ],
                    }
                ),
                "",
                0,
            )
        return ("", f"unknown script {script}", 1)

    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        fake_run_sync,
    )

    # Build a real SEO Ops app + test client.
    app = create_app(settings)
    client, routes = _stage_routes(app, action_id)

    # R0
    assert _run_stage(client, routes["r0"]) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "r0_prompt"
    action_workspace = (
        workspace / "runs" / f"action-{action_id}" / "current" / "laserpointerhub"
    )
    prompt_files = list((action_workspace / "research").glob("search-prompt-*.md"))
    assert len(prompt_files) == 1, "R0 should produce exactly one search-prompt"

    # R1 (with synthetic search results on stdin via form-encoded body)
    search_results_md = (FIXTURE_DIR / "workflow_run" / "02_search-results.md").read_text()
    assert _run_stage(client, routes["r1"], form={"search_results": search_results_md}) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "r2_collect"
    research_data = list((action_workspace / "research").glob("research-data-*.md"))
    assert len(research_data) == 1, "R1 should produce exactly one research-data file"

    # R3 (deterministic score + AI material-pack)
    assert _run_stage(client, routes["r3"]) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "r5_write_ready"
    material_pack = list((action_workspace / "material-packs").glob("*-*-*-*-*.md"))
    assert len(material_pack) == 1, "R3 should produce exactly one material-pack file"
    brief = list((action_workspace / "research").glob("brief-*-*-*-*-*.md"))
    assert len(brief) == 1, "R3 should produce exactly one brief file"

    # W0 (validate + AI draft)
    assert _run_stage(client, routes["w0"], form={"author": "TestAuthor"}) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "w1_draft"
    drafts = list((action_workspace / "drafts").glob("*-*-*-*-*.md"))
    assert len(drafts) == 1, "W0 should produce exactly one draft"

    # W1b (15-item pre-check)
    assert _run_stage(client, routes["w1b"], form={"tier": "Cluster Content"}) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "w1b_pre_check"
    pre_check = list((action_workspace / "reports").glob("pre-check-*.md"))
    assert len(pre_check) >= 1, "W1b should produce at least one pre-check report"

    # W2 (post-process + apply)
    assert _run_stage(client, routes["w2"], form={"apply": "1"}) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "w2_post_process"
    w2_state = json.loads((action_workspace / "reports" / f"w2-state-{ACTION_SLUG}.json").read_text())
    assert w2_state["gate_passed"] is True
    assert w2_state["applied"] is True
    assert w2_state["score"] == 84.5

    # W3 (register)
    assert _run_stage(client, routes["w3"]) == 303
    assert read_legacy_stage_from_db(settings, action_id) == "w3_register"
    assert "## 回溯链接候选" in (action_workspace / "drafts" / drafts[0].name).read_text()


# ── 3. Interrupt and resume ─────────────────────────────────────────


def test_interrupt_after_w0_resumes_from_w1b(tmp_path, monkeypatch):
    """Service restart between stages must not lose progress or break the pipeline."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    _install_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow._run_ai_text", ai_text
    )
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        _make_run_sync_stub(),
    )

    # --- First session: R0, R1, R3, W0 ---
    app1 = create_app(settings)
    client1, routes = _stage_routes(app1, action_id)
    _run_stage(client1, routes["r0"])
    _run_stage(
        client1,
        routes["r1"],
        form={
            "search_results": (
                FIXTURE_DIR / "workflow_run" / "02_search-results.md"
            ).read_text()
        },
    )
    _run_stage(client1, routes["r3"])
    _run_stage(client1, routes["w0"], form={"author": "TestAuthor"})
    assert read_legacy_stage_from_db(settings, action_id) == "w1_draft"

    # Simulate service restart: drop the TestClient, build a new one. The DB
    # and the workspace on disk persist.
    del client1, app1

    # --- Second session: W1b, W2, W3 ---
    app2 = create_app(settings)
    client2, _ = _stage_routes(app2, action_id)
    _run_stage(client2, routes["w1b"], form={"tier": "Cluster Content"})
    _run_stage(client2, routes["w2"], form={"apply": "1"})
    _run_stage(client2, routes["w3"])
    assert read_legacy_stage_from_db(settings, action_id) == "w3_register"

    # Verify the artifacts from the first session are still on disk and were
    # picked up by the second session (no re-creation of R0..W0 files).
    workspace = settings.data_dir / "legacy_workflow" / "laserpointerhub"
    action_ws = _action_workspace(workspace, action_id)
    research_prompts = list((action_ws / "research").glob("search-prompt-*.md"))
    assert len(research_prompts) == 1, "service restart must not duplicate R0 output"
    drafts = list((action_ws / "drafts").glob("*-*-*-*-*.md"))
    assert len(drafts) == 1, "service restart must not duplicate W0 output"


# ── 4. Re-run invalidates downstream ────────────────────────────────


def test_rerun_r1_invalidates_downstream_artifacts(tmp_path, monkeypatch):
    """Re-running R1 must delete material-pack, brief, draft, w2-state."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    _install_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr("seo_ops.services.legacy_workflow._run_ai_text", ai_text)
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        _make_run_sync_stub(),
    )

    app = create_app(settings)
    client, routes = _stage_routes(app, action_id)
    search_results_md = (FIXTURE_DIR / "workflow_run" / "02_search-results.md").read_text()

    # Run the full pipeline.
    for stage in ("r0", "r1", "r3", "w0", "w1b", "w2", "w3"):
        form = _stage_form(stage, search_results_md)
        assert _run_stage(client, routes[stage], form=form) == 303

    workspace = settings.data_dir / "legacy_workflow" / "laserpointerhub"
    action_ws = _action_workspace(workspace, action_id)

    # Sanity: every artifact exists.
    assert list(action_ws.glob("material-packs/*.md"))
    assert list(action_ws.glob("drafts/*.md"))
    assert (action_ws / "reports" / f"w2-state-{ACTION_SLUG}.json").exists()

    # Now re-run R1.
    assert _run_stage(
        client, routes["r1"], form={"search_results": search_results_md}
    ) == 303

    # Downstream artifacts should be gone.
    assert not list(action_ws.glob("material-packs/*.md")), (
        "re-running R1 must delete material-pack files"
    )
    assert not list(action_ws.glob("research/brief-*.md")), (
        "re-running R1 must delete brief files"
    )
    assert not list(action_ws.glob("drafts/*.md")), (
        "re-running R1 must delete draft files"
    )
    assert not (action_ws / "reports" / f"w2-state-{ACTION_SLUG}.json").exists(), (
        "re-running R1 must delete w2-state"
    )

    # DB stage should now be r2_collect, not w3_register.
    assert read_legacy_stage_from_db(settings, action_id) == "r2_collect"


def test_rerun_w0_invalidates_w1b_w2_w3(tmp_path, monkeypatch):
    """Re-running W0 must delete pre-check, post-process, register, w2-state."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    _install_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr("seo_ops.services.legacy_workflow._run_ai_text", ai_text)
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        _make_run_sync_stub(),
    )

    app = create_app(settings)
    client, routes = _stage_routes(app, action_id)
    search_results_md = (FIXTURE_DIR / "workflow_run" / "02_search-results.md").read_text()
    for stage in ("r0", "r1", "r3", "w0", "w1b", "w2", "w3"):
        form = _stage_form(stage, search_results_md)
        assert _run_stage(client, routes[stage], form=form) == 303

    workspace = settings.data_dir / "legacy_workflow" / "laserpointerhub"
    action_ws = _action_workspace(workspace, action_id)

    # Re-run W0.
    assert _run_stage(client, routes["w0"], form={"author": "TestAuthor"}) == 303

    # W1b, W2, W3 artifacts must be gone.
    assert not list(action_ws.glob("reports/pre-check-*.md"))
    assert not list(action_ws.glob("reports/post-process-*.md"))
    assert not list(action_ws.glob("reports/register-*.md"))

    # w2-state.json is RESET to fresh verdict (gate_passed=false, applied=false)
    # by the W0 function; the file should exist but with cleared fields.
    w2_state_path = action_ws / "reports" / f"w2-state-{ACTION_SLUG}.json"
    assert w2_state_path.exists(), "W0 must reset w2-state.json to fresh verdict"
    w2_state = json.loads(w2_state_path.read_text())
    assert w2_state["gate_passed"] is False
    assert w2_state["applied"] is False


# ── 5. Action isolation ──────────────────────────────────────────────


def test_two_actions_do_not_share_files(tmp_path, monkeypatch):
    """Two actions with the same topic must use separate workspaces."""
    settings = _settings(tmp_path)
    _bootstrap_db(settings)  # ensure actions table exists
    _install_workspace(tmp_path, monkeypatch)

    # Create two actions for the same topic.
    with db_connection(settings) as conn:
        for i in range(2):
            conn.execute(
                """INSERT INTO actions(
                    site_id, action_type, target_ref, decision,
                    workflow_status, plan_version, legacy_stage,
                    baseline_json, decided_at, updated_at
                ) VALUES (
                    1, 'create', ?, 'accepted', 'planned', '0.2.0',
                    'r0_pending', '{}', '2026-01-15T00:00:00+00:00',
                    '2026-01-15T00:00:00+00:00'
                )""",
                ("Example Topic for Testing the Legacy Pipeline",),
            )
        conn.commit()
        action_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM actions ORDER BY id DESC LIMIT 2"
            ).fetchall()
        ]

    monkeypatch.setattr("seo_ops.services.legacy_workflow._run_ai_text", ai_text)
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        _make_run_sync_stub(),
    )

    app = create_app(settings)
    with TestClient(app) as client:
        # Run R0 on both actions.
        for aid in action_ids:
            r = client.post(
                f"/actions/{aid}/legacy/stage/r0", follow_redirects=False
            )
            assert r.status_code == 303

    # Each action must have its own workspace directory under runs/.
    runs_root = settings.data_dir / "legacy_workflow" / "laserpointerhub" / "runs"
    action_dirs = sorted(p.name for p in runs_root.iterdir() if p.is_dir())
    assert len(action_dirs) == 2
    for aid in action_ids:
        assert f"action-{aid}" in action_dirs

    # The two workspaces must not share the draft, material-pack, etc.
    a1 = runs_root / f"action-{action_ids[0]}" / "current" / "laserpointerhub"
    a2 = runs_root / f"action-{action_ids[1]}" / "current" / "laserpointerhub"
    assert a1 != a2
    assert (a1 / "research").is_dir()
    assert (a2 / "research").is_dir()

    # Each action's search prompt lives in its own directory; the slugs may
    # be the same but the file names include today's date, so they don't
    # collide. Verify each action wrote exactly one prompt.
    assert len(list((a1 / "research").glob("search-prompt-*.md"))) == 1
    assert len(list((a2 / "research").glob("search-prompt-*.md"))) == 1


# ── 6. Failure does not reuse stale success ─────────────────────────


def test_failed_r1_does_not_create_partial_state(tmp_path, monkeypatch):
    """If R1 fails, no partial artifacts remain and DB does not advance past R1."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    _install_workspace(tmp_path, monkeypatch)

    # Make research_collector.py fail.
    def failing_run_sync(self, script, args):
        return ("", "scraper error", 2)

    # Run R0 successfully.
    app = create_app(settings)
    client, routes = _stage_routes(app, action_id)
    assert _run_stage(client, routes["r0"]) == 303

    # Now switch to a failing run_sync for R1.
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        failing_run_sync,
    )

    # R1 should fail (303 to error page).
    response = client.post(
        routes["r1"],
        data={"search_results": "fake search results"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "error" in location.lower()

    # The DB stage advances to "r1_results" (the failure-side label), NOT
    # to "r2_collect" (the success-side label). Either way, no downstream
    # artifacts must exist.
    stage = read_legacy_stage_from_db(settings, action_id)
    assert stage in {"r1_results", "r0_prompt"}, (
        f"R1 failure must not advance to r2_collect (got {stage})"
    )

    # No partial research-data file should exist.
    workspace = settings.data_dir / "legacy_workflow" / "laserpointerhub"
    action_ws = _action_workspace(workspace, action_id)
    assert not list(action_ws.glob("research/research-data-*.md"))
    assert not list(action_ws.glob("research/research-data-*.json"))


def test_failed_w3_does_not_advance_state(tmp_path, monkeypatch):
    """If W3 fails (e.g., draft changed after apply), state must not advance."""
    settings = _settings(tmp_path)
    action_id = _bootstrap_db(settings)
    _install_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr("seo_ops.services.legacy_workflow._run_ai_text", ai_text)
    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow.LegacyRunner.run_sync",
        _make_run_sync_stub(),
    )

    app = create_app(settings)
    client, routes = _stage_routes(app, action_id)
    search_results_md = (FIXTURE_DIR / "workflow_run" / "02_search-results.md").read_text()
    for stage in ("r0", "r1", "r3", "w0", "w1b", "w2"):
        form = _stage_form(stage, search_results_md)
        assert _run_stage(client, routes[stage], form=form) == 303

    workspace = settings.data_dir / "legacy_workflow" / "laserpointerhub"
    action_ws = _action_workspace(workspace, action_id)
    draft = next(action_ws.glob("drafts/*.md"))

    # Mutate the draft AFTER W2's apply (changing its sha256). W3 must reject.
    draft.write_text(draft.read_text() + "\n\ntampered\n", encoding="utf-8")

    response = client.post(routes["w3"], follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "error" in location.lower()

    # DB stage must NOT be w3_register.
    assert read_legacy_stage_from_db(settings, action_id) != "w3_register"


# ── helpers ──────────────────────────────────────────────────────────


def _stage_form(stage: str, search_results_md: str) -> dict:
    """Return the form body for a stage."""
    if stage in {"r1"}:
        return {"search_results": search_results_md}
    if stage in {"w0"}:
        return {"author": "TestAuthor"}
    if stage in {"w1b"}:
        return {"tier": "Cluster Content"}
    if stage in {"w2"}:
        return {"apply": "1"}
    return {}


def _make_run_sync_stub():
    """Return a LegacyRunner.run_sync stub that mimics the legacy scripts."""
    def fake_run_sync(self, script, args):
        name = Path(script).name
        if name == "research_collector.py":
            if args and args[0] == "collect":
                from datetime import UTC, datetime
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                slug = args[args.index("--topic") + 1].lower().replace(" ", "-")[:80]
                research_dir = self.workspace / "research"
                research_dir.mkdir(parents=True, exist_ok=True)
                (research_dir / f"research-data-{slug}-{today}.md").write_text(
                    "# research-data (synthetic)\n", encoding="utf-8"
                )
                (research_dir / f"research-data-{slug}-{today}.json").write_text(
                    '{"topic": "fake", "slug": "' + slug + '"}', encoding="utf-8"
                )
                return ("", "", 0)
            return ("", "unknown", 1)
        if name == "research_scorer.py":
            output_path = Path(args[args.index("--output") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                "deterministic score: 0.78\n", encoding="utf-8"
            )
            return ("", "", 0)
        if name == "write_collector.py":
            cmd = args[0] if args else ""
            if cmd == "validate":
                return ("mandatory_ok: True\n", "", 0)
            if cmd == "post-process":
                return (
                    "## 质量评分\n- 总分: 84.5 → ✅ 通过\n\n## 🚦 总门控\n- ✅ 通过",
                    "",
                    0,
                )
            if cmd == "register":
                draft = Path(args[args.index("--draft") + 1])
                if draft.exists():
                    txt = draft.read_text(encoding="utf-8")
                    draft.write_text(
                        txt + "\n\n## 回溯链接候选\n\n**#1 — Existing**\n",
                        encoding="utf-8",
                    )
                return ("✅ 注册完成", "", 0)
            return ("", "unknown", 1)
        if name == "write_pre_check.py":
            return (
                json.dumps(
                    {
                        "word_count": 1842,
                        "warn_count": 0,
                        "checks": [
                            {
                                "item": "字数",
                                "pass": True,
                                "level": "ok",
                                "detail": "1842 ≥ 1200",
                            }
                        ],
                    }
                ),
                "",
                0,
            )
        return ("", f"unknown script {script}", 1)

    return fake_run_sync