import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _state_for(draft: Path, **overrides) -> dict:
    digest = hashlib.sha256(draft.read_bytes()).hexdigest()
    state = {
        "rounds": 0,
        "gate_passed": False,
        "applied": False,
        "precheck_passed": True,
        "precheck_draft_sha256": digest,
        "precheck_tier": "Cluster Content",
    }
    state.update(overrides)
    if state.get("applied"):
        state.setdefault("applied_draft_sha256", digest)
    return state


class TestStageDetection:
    def test_slugify(self):
        from data_sources.modules import seo_common, write_collector
        from seo_ops.services.legacy_workflow import _slugify

        assert _slugify("Best Laser Pointer 2026") == "best-laser-pointer-2026"
        assert _slugify("What! is this?") == "what-is-this"
        assert _slugify("a--b") == "a-b"
        for topic in (
            "red/green laser",
            "user's laser & optics",
            "中文主题",
            "very " * 30 + "long topic",
        ):
            assert _slugify(topic) == seo_common.slugify(topic)
            assert _slugify(topic) == write_collector._slugify(topic)

    def test_non_latin_slug_is_stable_digest(self):
        from seo_ops.services.legacy_workflow import _slugify

        assert _slugify("中文主题") == "topic-eb4d7be8dc5d"

    def test_action_workspace_is_persistent_across_calls(self, tmp_path):
        from seo_ops.services.legacy_workflow import action_workspace

        _write(tmp_path / "context" / "brand-voice.md", "shared context")
        _write(tmp_path / "published" / "published-index.json", "[]")
        _write(tmp_path / "products" / "live_products_report.md", "# products")

        first = action_workspace(tmp_path, action_id=7)
        second = action_workspace(tmp_path, action_id=7)

        assert first == second
        assert (first / "context" / "brand-voice.md").read_text() == "shared context"
        assert (first / "research").is_dir()
        assert (first / "material-packs").is_dir()
        assert (first / "drafts").is_dir()
        assert (first / "reports").is_dir()
        assert (first / "context").is_symlink()
        assert (first / "published").is_symlink()
        assert (first / "products").is_symlink()

    def test_different_actions_have_separate_workspaces(self, tmp_path):
        from seo_ops.services.legacy_workflow import action_workspace

        a = action_workspace(tmp_path, action_id=7)
        b = action_workspace(tmp_path, action_id=8)
        _write(a / "drafts" / "only-on-a.md", "x")
        assert a != b
        assert (a / "drafts" / "only-on-a.md").exists()
        assert not (b / "drafts" / "only-on-a.md").exists()

    def test_r0_clears_all_stage_artifacts(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "test-topic"
        ws = lw.action_workspace(tmp_path, 1)
        # Lay down artifacts from every stage.
        _write(ws / "research" / f"search-prompt-{slug}-2026-01-01.md", "p")
        _write(ws / "research" / f"topic-context-{slug}.json", "{}")
        _write(ws / "research" / f"search-results-{slug}-2026-01-01.md", "r")
        _write(ws / "research" / f"research-data-{slug}-2026-01-01.md", "d")
        _write(ws / "research" / f"research-data-{slug}-2026-01-01.json", "{}")
        _write(ws / "research" / f"research-score-{slug}-2026-01-01.md", "s")
        _write(ws / "research" / f"brief-{slug}-2026-01-01.md", "b")
        _write(ws / "material-packs" / f"{slug}-2026-01-01.md", "mp")
        _write(ws / "drafts" / f"{slug}-2026-01-01.md", "dft")
        _write(ws / "reports" / f"pre-check-{slug}-2026-01-01.md", "pre")
        _write(ws / "reports" / f"post-process-{slug}-2026-01-01.md", "post")
        _write(ws / "reports" / f"register-{slug}-2026-01-01.md", "reg")
        _write(ws / "reports" / f"w2-state-{slug}.json", "{}")

        result = lw.stage_r0_generate_prompt("test topic", ws)

        assert result["success"] is True
        # Every stage artifact was wiped; only a fresh prompt for the slug remains.
        assert not (ws / "research" / f"search-results-{slug}-2026-01-01.md").exists()
        assert not (ws / "research" / f"research-data-{slug}-2026-01-01.md").exists()
        assert not (ws / "research" / f"research-score-{slug}-2026-01-01.md").exists()
        assert not (ws / "material-packs" / f"{slug}-2026-01-01.md").exists()
        assert not (ws / "drafts" / f"{slug}-2026-01-01.md").exists()
        assert not list((ws / "reports").glob(f"*-{slug}-*.md"))
        assert not (ws / "reports" / f"w2-state-{slug}.json").exists()
        prompts = list((ws / "research").glob(f"search-prompt-{slug}-*.md"))
        assert len(prompts) == 1

    def test_rerun_r1_invalidates_downstream_artifacts(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "test-topic"
        ws = lw.action_workspace(tmp_path, 1)
        # Stale downstream from a previous successful R3+W0+W1b run.
        _write(ws / "material-packs" / f"{slug}-2026-01-01.md", "old pack")
        _write(ws / "research" / f"research-score-{slug}-2026-01-01.md", "old score")
        _write(ws / "research" / f"brief-{slug}-2026-01-01.md", "old brief")
        _write(ws / "drafts" / f"{slug}-2026-01-01.md", "old draft")
        _write(ws / "reports" / f"w2-state-{slug}.json", '{"rounds":1}')

        asyncio.run(
            lw.stage_r1_save_and_collect("test topic", "new search", ws)
        )

        assert not (ws / "material-packs" / f"{slug}-2026-01-01.md").exists()
        assert not (ws / "research" / f"research-score-{slug}-2026-01-01.md").exists()
        assert not (ws / "research" / f"brief-{slug}-2026-01-01.md").exists()
        assert not (ws / "drafts" / f"{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"w2-state-{slug}.json").exists()

    def test_rerun_w0_invalidates_post_process_verdict(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "test-topic"
        ws = lw.action_workspace(tmp_path, 1)
        _write(ws / "material-packs" / f"{slug}-2026-01-01.md", "pack")
        _write(ws / "drafts" / f"{slug}-2026-01-01.md", "old draft")
        _write(ws / "reports" / f"pre-check-{slug}-2026-01-01.md", "pre")
        _write(ws / "reports" / f"post-process-{slug}-2026-01-01.md", "post")
        _write(ws / "reports" / f"register-{slug}-2026-01-01.md", "reg")
        _write(
            ws / "reports" / f"w2-state-{slug}.json",
            '{"rounds":1,"gate_passed":true,"applied":true}',
        )

        # Re-running W0 must invalidate W0 and everything after it, including
        # the post-process verdict and the w2-state file.
        removed = lw.clear_stage_artifacts(ws, slug, "w0")

        assert removed >= 5
        assert not (ws / "drafts" / f"{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"pre-check-{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"post-process-{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"register-{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"w2-state-{slug}.json").exists()

    def test_rerun_w1b_invalidates_w2_and_w3(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "test-topic"
        ws = lw.action_workspace(tmp_path, 1)
        _write(ws / "drafts" / f"{slug}-2026-01-01.md", "draft")
        _write(ws / "reports" / f"pre-check-{slug}-2026-01-01.md", "pre")
        _write(ws / "reports" / f"post-process-{slug}-2026-01-01.md", "post")
        _write(ws / "reports" / f"register-{slug}-2026-01-01.md", "reg")

        removed = lw.clear_stage_artifacts(ws, slug, "w2")

        assert removed >= 2
        assert (ws / "reports" / f"pre-check-{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"post-process-{slug}-2026-01-01.md").exists()
        assert not (ws / "reports" / f"register-{slug}-2026-01-01.md").exists()

    def test_state_persists_across_process_restart(self, tmp_path):
        """The workspace path is stable; files survive a fresh Python process."""
        from seo_ops.services import legacy_workflow as lw

        ws1 = lw.action_workspace(tmp_path, 42)
        _write(ws1 / "research" / "search-prompt-x-2026-01-01.md", "prompt")
        _write(ws1 / "reports" / "w2-state-x.json", '{"rounds":1,"gate_passed":true}')

        # Simulate a fresh import of the module in a new process by re-importing
        # in a child interpreter.
        code = (
            "import sys, json;"
            "sys.path.insert(0, " + repr(str(tmp_path)) + ");"
            "sys.path.insert(0, " + repr(str(REPO_ROOT)) + ");"
            "from seo_ops.services.legacy_workflow import action_workspace, "
            "load_w2_state, _collect_files;"
            "ws = action_workspace(__import__('pathlib').Path("
            + repr(str(tmp_path))
            + "), 42);"
            "print(json.dumps({'path': str(ws), "
            "'has_prompt': (ws / 'research' / 'search-prompt-x-2026-01-01.md').exists(), "
            "'rounds': load_w2_state(ws, 'x')['rounds']}))"
        )
        out = subprocess.run(  # noqa: S603 — controlled local subprocess for test
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
        )
        assert out.returncode == 0, out.stderr
        payload = json.loads(out.stdout.strip())
        assert payload["path"] == str(ws1)
        assert payload["has_prompt"] is True
        assert payload["rounds"] == 1

    def test_stage_labels_and_steps(self):
        from seo_ops.services.legacy_workflow import STAGE_ORDER, stage_label, stage_step
        assert stage_label("r0_pending") == "尚未开始"
        assert stage_label("w3_register") == "已注册，全部完成"
        assert stage_step("r0_pending") == 0
        assert stage_step("w3_register") == 11
        assert len(STAGE_ORDER) == 12

    def test_detect_stage_no_files(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        stage, files = detect_stage("nonexistent topic", tmp_path)
        assert stage == "r0_pending"
        assert files["search_prompt"] is None
        assert files["draft"] is None

    def test_detect_stage_r0_prompt(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        slug = "test-topic"
        (tmp_path / "research").mkdir(parents=True)
        (tmp_path / "research" / f"search-prompt-{slug}-2026-01-01.md").write_text("prompt")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "r0_prompt"
        assert files["search_prompt"] is not None

    def test_detect_stage_r1_results(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        slug = "test-topic"
        (tmp_path / "research").mkdir(parents=True)
        (tmp_path / "research" / f"search-results-{slug}-2026-01-01.md").write_text("results")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "r1_results"

    def test_detect_stage_r2_collect(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        slug = "test-topic"
        (tmp_path / "research").mkdir(parents=True)
        (tmp_path / "research" / f"research-data-{slug}-2026-01-01.md").write_text("data")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "r2_collect"

    def test_detect_stage_r5_write_ready(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        slug = "test-topic"
        (tmp_path / "research").mkdir(parents=True)
        (tmp_path / "material-packs").mkdir(parents=True)
        (tmp_path / "material-packs" / f"{slug}-2026-01-01.md").write_text("Part 1\nstuff\nPart 3\nchecks")
        (tmp_path / "research" / f"research-score-{slug}-2026-01-01.md").write_text("score")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "r5_write_ready"

    def test_detect_stage_w1_draft(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        slug = "test-topic"
        (tmp_path / "drafts").mkdir(parents=True)
        (tmp_path / "drafts" / f"{slug}-2026-01-01.md").write_text("draft content")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "w1_draft"
        assert files["draft"] is not None

    def test_detect_stage_w3_register(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md",
               "Title: Test\n\n## 回溯链接候选（AI 请读取旧文章后写清单）\n**#3 — Foo**")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "w3_register"

    def test_applied_draft_is_not_mistaken_for_registered(self, tmp_path):
        """post-process --apply writes 内链:/外链: long before register runs."""
        from seo_ops.services.legacy_workflow import detect_stage
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md",
               "Title: Test\n内链:\n - https://example.com/blog/a\n外链:\n - https://x.org")
        stage, _ = detect_stage("test topic", tmp_path)
        assert stage == "w1_draft"


class TestStageProgression:
    """Pre-check and post-process create no artifact of their own, so the
    database stage has to carry the UI past w1_draft."""

    def test_db_stage_advances_past_file_stage(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "draft")
        stage, _ = detect_stage("test topic", tmp_path, db_stage="w1b_pre_check")
        assert stage == "w1b_pre_check"

        stage, _ = detect_stage("test topic", tmp_path, db_stage="w2_post_process")
        assert stage == "w2_post_process"

    def test_db_stage_ignored_when_artifact_gone(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        stage, _ = detect_stage("test topic", tmp_path, db_stage="w2_post_process")
        assert stage == "r0_pending"

    def test_db_stage_cannot_regress_file_stage(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md",
               "Title: T\n## 回溯链接候选\n**#1 — A**")
        stage, _ = detect_stage("test topic", tmp_path, db_stage="w1_draft")
        assert stage == "w3_register"

    def test_unknown_db_stage_is_ignored(self, tmp_path):
        from seo_ops.services.legacy_workflow import detect_stage
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "draft")
        stage, _ = detect_stage("test topic", tmp_path, db_stage="not_a_stage")
        assert stage == "w1_draft"


class TestTierResolution:
    def test_operator_choice_wins(self, tmp_path):
        from seo_ops.services.legacy_workflow import resolve_tier
        _write(tmp_path / "research" / "test-topic.json", "{}")
        assert resolve_tier("test topic", tmp_path, "Pillar Page") == "Pillar Page"

    def test_inherits_topic_context_tier(self, tmp_path):
        from seo_ops.services.legacy_workflow import resolve_tier
        _write(tmp_path / "research" / "topic-context-test-topic.json",
               json.dumps({"tier": "Product Roundup"}))
        assert resolve_tier("test topic", tmp_path, "") == "Product Roundup"

    def test_falls_back_to_heuristic(self, tmp_path):
        from seo_ops.services.legacy_workflow import resolve_tier
        assert resolve_tier("best laser pointer", tmp_path, "") == "Product Roundup"
        assert resolve_tier("how to clean a laser lens", tmp_path, "") == "Cluster Content"


class TestReports:
    def test_report_roundtrip(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_report, save_report
        save_report(tmp_path, "pre-check", "slug-a", "报告正文 ❌")
        assert "报告正文" in load_report(tmp_path, "pre-check", "slug-a")

    def test_same_day_reports_do_not_overwrite(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_report, save_report

        first = save_report(tmp_path, "collect", "test-topic", "first")
        second = save_report(tmp_path, "collect", "test-topic", "second")

        assert first != second
        assert first.read_text(encoding="utf-8") == "first"
        assert second.read_text(encoding="utf-8") == "second"
        assert load_report(tmp_path, "collect", "test-topic") == "second"

    def test_missing_report_is_empty(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_report
        assert load_report(tmp_path, "post-process", "nope") == ""

    def test_w2_state_defaults_and_roundtrip(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_w2_state, save_w2_state
        assert load_w2_state(tmp_path, "s")["rounds"] == 0
        save_w2_state(tmp_path, "s", {"rounds": 2, "gate_passed": False})
        assert load_w2_state(tmp_path, "s")["rounds"] == 2

    def test_corrupt_w2_state_falls_back(self, tmp_path):
        from seo_ops.services.legacy_workflow import _reports_dir, load_w2_state
        _write(_reports_dir(tmp_path) / "w2-state-s.json", "{not json")
        assert load_w2_state(tmp_path, "s")["rounds"] == 0


_POST_PROCESS_REPORT = """# POST-PROCESS 报告

## 链接校验
- 文章内链: 1 (2000词 → 下限2上限4) → ❌

## ⚠️ 链接问题
- [error] too_few_blog_links:
  (当前 1, 下限 2)

## 🔪 蚕食门控（新文 vs 已发布）
- ❌ **阻塞**：最高相似度 0.61 ≥ 0.55 — 与已发布文章重度重叠，必须换角度或合并

## 质量评分
- 总分: 64.5 → ❌ 未达标 (需 ≥70)

## 🚦 总门控
- ❌ **不可进入段3**：蚕食 ≥0.55。修正正文差异化后重跑段2。

## 🔧 怎么修
1. 蚕食阻塞(≥0.55)：找到与「best-laser-pointer」的差异化角度后重写
2. 博客内链不足：从候选中选 1 条
"""


class TestPostProcessParsing:
    def test_extracts_score_and_cannibal(self):
        from seo_ops.services.legacy_workflow import _parse_post_process
        m = _parse_post_process(_POST_PROCESS_REPORT)
        assert m["score"] == 64.5
        assert m["cannibal"] == 0.61
        assert m["cannibal_block"] is True
        assert "博客内链不足" in m["fix_items"]
        assert "too_few_blog_links" in m["link_issues"]

    def test_score_block_detected(self):
        from seo_ops.services.legacy_workflow import _parse_post_process
        report = "## 🚦 总门控\n- ❌ **不可进入段3**：评分 <70。\n\n## 质量评分\n- 总分: 51.0 → ❌"
        m = _parse_post_process(report)
        assert m["score_block"] is True
        assert m["cannibal_block"] is False

    def test_passing_report_has_no_blocks(self):
        from seo_ops.services.legacy_workflow import _parse_post_process
        report = ("## 质量评分\n- 总分: 82.0 → ✅ 通过\n\n"
                  "## 🚦 总门控\n- ✅ 通过，可进入段3 register。")
        m = _parse_post_process(report)
        assert m["score"] == 82.0
        assert m["score_block"] is False
        assert m["cannibal_block"] is False

    def test_tolerates_report_without_sections(self):
        from seo_ops.services.legacy_workflow import _parse_post_process
        m = _parse_post_process("脚本崩了")
        assert m["score"] is None and m["cannibal"] is None
        assert m["fix_items"] == "" and m["link_issues"] == ""


class TestRunnerEnvironment:
    def test_sites_dir_points_at_workspace_parent(self, tmp_path):
        from seo_ops.services.legacy_workflow import LegacyRunner
        ws = tmp_path / "legacy_workflow" / "laserpointerhub"
        ws.mkdir(parents=True)
        env = LegacyRunner(ws)._env()
        assert env["SEO_SITES_DIR"] == str((tmp_path / "legacy_workflow").resolve())

    def test_modules_dir_exists(self):
        from seo_ops.services.legacy_workflow import LEGACY_MODULES_DIR
        assert (LEGACY_MODULES_DIR / "write_collector.py").exists()
        assert (LEGACY_MODULES_DIR / "research_collector.py").exists()


class TestResearchScoringOrder:
    def _prepare(self, tmp_path):
        _write(
            tmp_path / "research" / "research-data-test-topic-2026-01-01.md",
            "research data",
        )
        _write(
            tmp_path / "research" / "research-data-test-topic-2026-01-01.json",
            '{"topic": "test topic"}',
        )

    def test_score_runs_before_ai_and_is_in_prompt(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        calls = []

        async def fake_run(self, script, args):
            calls.append(script)
            output = Path(args[args.index("--output") + 1])
            output.write_text("deterministic score 0.72", encoding="utf-8")
            return ("deterministic score 0.72", "", 0)

        async def fake_ai(purpose, system, user, **kwargs):
            calls.append("ai")
            assert "deterministic score 0.72" in user
            return "Part 1\n\nPart 3\n\n===BRIEF===\nBrief with score 0.72"

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        result = asyncio.run(lw.stage_r3_ai_analyze("test topic", tmp_path))

        assert result["success"] is True
        assert result["stage"] == "r5_write_ready"
        assert calls == ["research_scorer.py", "ai"]
        assert (tmp_path / "research" / "write-brief-test-topic.json").exists()
        assert (tmp_path / "research" / "coverage-contract-test-topic.json").exists()
        assert (tmp_path / "research" / "evidence-cards-test-topic.json").exists()

    def test_failed_current_score_cannot_reuse_stale_report(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        _write(
            tmp_path / "research" / "research-score-test-topic-2026-01-01.md",
            "stale score",
        )
        ai_called = False

        async def fake_run(self, script, args):
            return ("", "scorer crashed", 2)

        async def fake_ai(*args, **kwargs):
            nonlocal ai_called
            ai_called = True
            return ""

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        result = asyncio.run(lw.stage_r3_ai_analyze("test topic", tmp_path))

        assert result["success"] is False
        assert result["stage"] == "r2_collect"
        assert "scorer crashed" in result["error"]
        assert ai_called is False
        assert not (tmp_path / "material-packs").exists()


class TestCompactWritingHandoff:
    def test_contracts_keep_required_evidence_and_cover_each_outline_section(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "construction-laser"
        _write(
            tmp_path / "research" / f"brief-{slug}-2026-01-01.md",
            "## 3. Recommended Outline (H2)\n\n"
            "H2: Ceiling layout and line-of-sight planning\n"
            "H2: Safe use around crews and reflective surfaces\n",
        )
        _write(
            tmp_path / "research" / f"evidence-ledger-{slug}.json",
            json.dumps({
                "version": 1,
                "material_pack_sha256": "pack-sha",
                "evidence": [
                    {
                        "evidence_id": "ev_layout",
                        "source_url": "https://example.test/layout",
                        "quote": "Line-of-sight planning reduces layout ambiguity.",
                        "key_finding": "",
                        "canonical_concepts": ["ceiling", "layout"],
                        "claim_types": ["authority_citation"],
                        "required": False,
                    },
                    {
                        "evidence_id": "ev_required",
                        "source_url": "https://example.test/safety",
                        "quote": "Use controls around reflective surfaces.",
                        "key_finding": "",
                        "canonical_concepts": ["safety", "reflective"],
                        "claim_types": ["authority_citation"],
                        "required": True,
                    },
                ],
            }),
        )

        paths = lw._write_context_contracts(
            tmp_path, slug, "Laser Pointer for Ceiling Layout", tier="Cluster Content",
        )
        brief = json.loads(paths["brief"].read_text(encoding="utf-8"))
        contract = json.loads(paths["coverage"].read_text(encoding="utf-8"))
        cards = json.loads(paths["cards"].read_text(encoding="utf-8"))

        assert brief["outline"] == [
            "Ceiling layout and line-of-sight planning",
            "Safe use around crews and reflective surfaces",
        ]
        assert all(section["must_cover"] for section in contract["sections"])
        assert all(section["candidate_evidence_ids"] for section in contract["sections"])
        assert "ev_required" in contract["sections"][0]["candidate_evidence_ids"]
        assert {card["evidence_id"] for card in cards["all_cards"]} == {
            "ev_layout", "ev_required"
        }

    def test_revision_cards_keep_existing_claim_evidence(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        slug = "repair-topic"
        _write(tmp_path / "research" / f"brief-{slug}-2026-01-01.md", "H2: Repair the article")
        _write(
            tmp_path / "research" / f"evidence-ledger-{slug}.json",
            json.dumps({"version": 1, "evidence": [
                {"evidence_id": "ev_keep", "source_url": "https://example.test/keep",
                 "quote": "Existing factual sentence support.", "key_finding": "",
                 "canonical_concepts": ["existing"], "claim_types": [], "required": False},
                {"evidence_id": "ev_other", "source_url": "https://example.test/other",
                 "quote": "Other source support.", "key_finding": "",
                 "canonical_concepts": ["other"], "claim_types": [], "required": False},
            ]}),
        )
        _write(
            tmp_path / "research" / f"claim-ledger-{slug}.json",
            json.dumps({"version": 1, "claims": [
                {"claim_text": "Existing factual sentence.", "claim_type": "general",
                 "evidence_ids": ["ev_keep"]}
            ]}),
        )

        _, rendered = lw._revision_context_contracts(
            tmp_path, slug, "Repair topic", relevant_text="a link failure",
        )
        assert "ev_keep" in rendered

    def test_collect_failure_cannot_reuse_previous_output(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        stale_md = _write(
            tmp_path / "research" / "research-data-test-topic-2026-01-01.md",
            "stale",
        )
        stale_json = _write(stale_md.with_suffix(".json"), "{}")

        async def fake_run(self, script, args):
            return "", "collector crashed", 2

        monkeypatch.setattr(lw, "_today_str", lambda: "2026-01-01")
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(
            lw.stage_r1_save_and_collect("test topic", "new search", tmp_path)
        )

        assert result["success"] is False
        assert result["research_data_file"] is None
        assert not stale_md.exists()
        assert not stale_json.exists()


class TestStructuredPreCheck:
    def _prepare(self, tmp_path):
        _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md",
            "SEO Keywords: test topic\n\nbody",
        )

    def test_business_failures_are_counted_from_json(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        payload = {
            "word_count": 100,
            "warn_count": 1,
            "checks": [
                {"item": "字数", "pass": False, "level": "fail", "detail": "100"},
                {
                    "item": "实体",
                    "pass": True,
                    "level": "warn",
                    "detail": "low",
                },
            ],
        }

        async def fake_run(self, script, args):
            assert "--json" in args
            return (json.dumps(payload, ensure_ascii=False), "", 1)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(
            lw.stage_w1b_pre_check("test topic", "Cluster Content", tmp_path)
        )

        assert result["success"] is True
        assert result["fail_count"] == 1
        assert "| 字数 | ❌ |" in result["report"]
        assert "| 实体 | ⚠️ |" in result["report"]

    def test_script_crash_is_not_reported_as_pass(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)

        async def fake_run(self, script, args):
            return ("Traceback without JSON", "boom", 2)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(
            lw.stage_w1b_pre_check("test topic", "Cluster Content", tmp_path)
        )

        assert result["success"] is False
        assert result["stage"] == "w1_draft"
        assert "boom" in result["error"]


class TestMaterialPackEntityCoverage:
    """W1b entity-coverage check uses material-pack entries as research
    suggestions, NOT required entities. Severity rules:
      - [search]/[library]: always warning, never blocking
      - [required]: blocking only if evidence + quote/kf + supporting intent
      - no evidence: never in missing list
    """

    def _pack(self, tmp_path, body: str) -> Path:
        p = tmp_path / "material-pack.md"
        _write(p, body)
        return p

    def _intent(self, title: str = "Commercial Construction Laser Pointer",
                h1: str = "# Commercial Construction Laser Pointer",
                keywords: list[str] | None = None) -> list[str]:
        from data_sources.modules.write_pre_check import _intent_words
        return _intent_words(title, h1, keywords or ["construction laser pointer"])

    # ── Parsing ──────────────────────────────────────────────────────

    def test_parse_pack_extracts_entries_with_source(self, tmp_path):
        from data_sources.modules.write_pre_check import parse_pack_entities
        pack = self._pack(tmp_path, """\
### A. 用户痛点 (≥3)

- **[search] Laser pointer button gets pressed accidentally**
  - Source: https://reddit.com/r/flashlight/comments/abc
  - Quote: "my pouch, but I have a problem with the button"

- **[library E] FDA Import Alert 89-01**
  - Source: https://www.fda.gov/radiation-emitting-products/laser-pointer
  - Key finding: Class 3R ≤5mW for pointing
""")
        ents = parse_pack_entities(pack)
        assert len(ents) == 2
        assert ents[0]["source_tag"] == "search"
        assert ents[0]["source_quote"].startswith("my pouch")
        assert ents[1]["source_tag"] == "library"
        assert ents[1]["source_key_finding"].startswith("Class 3R")

    def test_parse_required_tag(self, tmp_path):
        from data_sources.modules.write_pre_check import parse_pack_entities
        pack = self._pack(tmp_path, """\
- **[required] Must discuss beam divergence**
  - Source: https://example.com/beam
  - Quote: "beam divergence is critical"
""")
        ents = parse_pack_entities(pack)
        assert len(ents) == 1
        assert ents[0]["source_tag"] == "required"
        assert ents[0]["evidence"] == "https://example.com/beam"

    def test_unsourced_entries_are_not_required(self, tmp_path):
        from data_sources.modules.write_pre_check import parse_pack_entities
        pack = self._pack(tmp_path, """\
- **[search] No source line here**
""")
        ents = parse_pack_entities(pack)
        assert len(ents) == 1
        assert ents[0]["evidence"] == ""

    # ── Intent relevance (supporting / unrelated) ─────────────────────

    def test_supporting_when_entity_shares_meaningful_word(self):
        from data_sources.modules.write_pre_check import _entity_supporting
        intent = self._intent()
        # "construction" appears in both → supporting
        assert _entity_supporting("Construction electrician pointing", intent) is True
        # "laser" appears in both → supporting
        assert _entity_supporting("Laser pointer button gets pressed accidentally", intent) is True
        # "pointer" appears in both → supporting
        assert _entity_supporting("Pointer accessories and mounts", intent) is True

    def test_unrelated_when_no_overlap(self):
        from data_sources.modules.write_pre_check import _entity_supporting
        intent = self._intent()
        assert _entity_supporting("Telescope stargazing adapter", intent) is False
        assert _entity_supporting("Cat toy laser play", intent) is True  # "laser" shared

    def test_stopwords_alone_dont_make_supporting(self):
        from data_sources.modules.write_pre_check import _entity_supporting
        intent = self._intent()  # no "review"/"guide"/"best" in intent
        assert _entity_supporting("Best review guide comparison", intent) is False

    # ── Severity: [search]/[library] are always warning ──────────────

    def test_search_entity_with_evidence_is_warning_not_blocking(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[search] Construction electrician pointing at junction box**
  - Source: https://example.com/a
  - Quote: "I need a laser pointer for ceiling work"
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("no such terms", self._intent(), ents)
        assert result["missing"] == [] or result["level"] == "warn"
        for m in result["missing"]:
            assert m["severity"] == "warning"
            assert m["source_tag"] == "search"

    def test_library_entity_with_evidence_is_warning_never_blocking(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[library E] FDA rules for laser pointers**
  - Source: https://www.fda.gov/laser
  - Key finding: FDA limits pointing lasers to 5mW
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("no such terms", self._intent(), ents)
        for m in result["missing"]:
            assert m["severity"] == "warning"
            assert m["source_tag"] == "library"

    # ── Severity: [required] blocking only with evidence + quote ──────

    def test_required_with_evidence_and_quote_is_blocking(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[required] Construction laser pointer beam specs for ceiling pointing**
  - Source: https://example.com/beam
  - Quote: "beam divergence above 2 mrad is unacceptable for ceiling pointing"
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("no such terms", self._intent(), ents)
        blocking = [m for m in result["missing"] if m["severity"] == "blocking"]
        assert len(blocking) == 1
        assert result["level"] == "fail"

    def test_required_with_evidence_but_no_quote_is_warning(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[required] Construction laser pointer beam specs for ceiling work**
  - Source: https://example.com/beam
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("no such terms", self._intent(), ents)
        for m in result["missing"]:
            assert m["severity"] == "warning", f"{m} should be warning, has no quote"
        assert result["level"] == "warn"

    def test_required_with_evidence_and_key_finding_is_blocking(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[required] Construction laser pointer FDA class limit**
  - Source: https://www.fda.gov/laser
  - Key finding: Pointing lasers must not exceed 5mW (Class 3R)
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("no such terms", self._intent(), ents)
        blocking = [m for m in result["missing"] if m["severity"] == "blocking"]
        assert len(blocking) == 1

    # ── Unrelated / unsourced / already-present ─────────────────────────

    def test_unrelated_entity_never_appears_in_missing(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[search] Telescope stargazing**
  - Source: https://example.com/astro
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("any prose", self._intent(), ents)
        assert result["missing"] == []
        assert result["level"] == "ok"

    def test_unsourced_entity_never_appears_in_missing(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[search] Nd:YAG laser physics**
- **[search] Construction electrician**
  - Source: https://example.com/c
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage(
            "prose about construction", self._intent(), ents,
        )
        missing_entities = [m["entity"] for m in result["missing"]]
        assert "Nd:YAG laser physics" not in missing_entities
        assert result["skipped_unsourced"] >= 1

    def test_present_entity_not_in_missing(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[search] Junction box above ceiling pointing**
  - Source: https://example.com/c
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage(
            "A construction electrician pointing at a junction box above the ceiling.",
            self._intent(), ents,
        )
        assert result["missing"] == []
        assert any("junction" in m.lower() for m in result["matched"])

    def test_no_pack_returns_ok_and_skips(self):
        from data_sources.modules.write_pre_check import check_pack_entity_coverage
        result = check_pack_entity_coverage("any prose", [], [])
        assert result["level"] == "ok"
        assert result["missing"] == []

    def test_required_unsourced_never_in_missing(self, tmp_path):
        """Even a [required] entity MUST have a Source to be checked."""
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[required] Beam divergence must match ceiling work** (no source!)
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage("any prose", self._intent(), ents)
        assert result["missing"] == []
        assert result["skipped_unsourced"] == 1

    # ── Real regression: pack entry must NOT auto-block ──────────────

    def test_search_entry_does_not_auto_block(self, tmp_path):
        """Regression: a material pack search entry like "Laser pointer
        button gets pressed accidentally" must NOT become blocking for
        an article about "laser pointer for pointing above ceilings in
        commercial construction"."""
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = self._pack(tmp_path, """\
- **[search] Laser pointer button gets pressed accidentally**
  - Source: https://reddit.com/r/flashlight/1
  - Quote: "I keep pressing the button in my pouch"

- **[search] Construction worker needs surgical dot for ceiling grids**
  - Source: https://reddit.com/r/laserpointerforums/1
  - Quote: "I need a green beam to see against white ceiling tiles"
""")
        ents = parse_pack_entities(pack)
        result = check_pack_entity_coverage(
            "Prose about construction laser pointing ceiling work.",
            self._intent(), ents,
        )
        # All missing must be warning — search entries can't block.
        for m in result["missing"]:
            assert m["severity"] == "warning", (
                f"Entity '{m['entity']}' should be warning, not blocking"
            )
        assert result["level"] == "warn"

    # ── End-to-end: legacy_workflow passes --pack ─────────────────────

    def test_run_includes_missing_entities_in_check_payload(self, tmp_path, monkeypatch):
        """End-to-end: stage_w1b_pre_check passes --pack to the script, and
        the pre-check report contains the structured missing_entities payload."""
        from seo_ops.services import legacy_workflow as lw

        slug = "coverage-topic"
        ws = tmp_path
        _write(
            ws / "drafts" / f"{slug}-2026-01-15.md",
            "SEO Title: T\nSEO Description: x\nSEO Keywords: construction laser pointer\n\n"
            "# Construction Laser Pointer\n\nbody\n",
        )
        _write(
            ws / "material-packs" / f"{slug}-2026-01-15.md",
            "- **[required] Right beam divergence for ceiling work**\n"
            "  - Source: https://example.com/beam\n"
            "  - Quote: \"beam divergence above 2 mrad is too wide\"\n",
        )
        payload = {
            "word_count": 150,
            "warn_count": 0,
            "fail_count": 1,
            "checks": [
                {
                    "item": "关键实体覆盖（material pack）",
                    "pass": False,
                    "level": "fail",
                    "detail": "detail",
                    "missing_entities": [
                        {
                            "entity": "Right beam divergence for ceiling work",
                            "intent_relevance": "supporting",
                            "evidence": "https://example.com/beam",
                            "severity": "blocking",
                            "source_tag": "required",
                            "source_section": "",
                        }
                    ],
                }
            ],
        }

        async def fake_run(self, script, args):
            assert "--pack" in args
            return (json.dumps(payload, ensure_ascii=False), "", 1)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        result = asyncio.run(
            lw.stage_w1b_pre_check("coverage topic", "Cluster Content", ws)
        )
        assert result["fail_count"] == 1
        assert "关键实体覆盖" in result["report"]


class TestFactCheck:
    """Check 14: claim-ledger → evidence-ledger fact validation."""

    def tmp_dir(self):
        import tempfile
        from pathlib import Path as _P
        return _P(tempfile.mkdtemp())

    def test_search_entity_not_auto_blocked(self, tmp_path):
        """Regression: 'button gets pressed accidentally' — search entry
        with evidence must be warning, NOT blocking."""
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = tmp_path / "pack.md"
        pack.write_text(
            "- **[search] Laser pointer button gets pressed accidentally**\n"
            "  - Source: https://reddit.com\n"
            "  - Quote: \"the button pressed in my pouch\"\n",
            encoding="utf-8",
        )
        ents = parse_pack_entities(pack)
        intent = ['commercial', 'construction', 'laser', 'pointer']
        result = check_pack_entity_coverage("any prose", intent, ents)
        for m in result["missing"]:
            assert m["severity"] == "warning", f"Should be warning: {m}"

    def test_unsourced_technical_fact_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check
        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("fake material pack", encoding="utf-8")
        dr_f.write_text("draft", encoding="utf-8")
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"draft","claim_type":"test","evidence_ids":[]}],"draft_sha256":"' +
            hashlib.sha256(b"draft").hexdigest() + '"}',
            encoding="utf-8",
        )
        # Empty evidence ledger is invalid — should block.
        ev_f.write_text('{"version":1,"material_pack_sha256":"' +
                        hashlib.sha256(b"fake material pack").hexdigest() +
                        '","evidence":[]}', encoding="utf-8")
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        # claim has empty evidence_ids → blocking
        assert not all(r['pass'] for r in fact), f"Should block: {results}"

    def test_valid_evidence_pass(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check
        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("A green 5mW laser is bright.", encoding="utf-8")
        dr_sha = hashlib.sha256(b"A green 5mW laser is bright.").hexdigest()
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '","evidence":[{"evidence_id":"ev-001","source_url":"https://ex.com/a","quote":"data","canonical_concepts":["laser"],"claim_types":["spec"],"required":false}]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"A green 5mW laser is bright.","claim_type":"technical_specification","evidence_ids":["ev-001"]}],"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert all(r['pass'] for r in fact), f"Should all pass: {results}"

    def test_fake_evidence_id_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check
        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim text here", encoding="utf-8")
        dr_sha = hashlib.sha256(b"claim text here").hexdigest()
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '","evidence":[{"evidence_id":"ev-001","source_url":"https://ex.com/a","quote":"data","canonical_concepts":["x"],"claim_types":["y"],"required":false}]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"claim text here","claim_type":"tech","evidence_ids":["ev-999"]}],"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        fail = [r for r in fact if not r['pass']]
        assert len(fail) >= 1, f"Expected at least one fail: {results}"

    def test_required_core_missing_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = tmp_path / "pack.md"
        pack.write_text(
            "- **[required] Commercial laser pointer divergence for ceiling pointing**\n"
            "  - Source: https://example.com/beam\n"
            "  - Quote: \"beam divergence above 2 mrad is unacceptable\"\n",
            encoding="utf-8",
        )
        ents = parse_pack_entities(pack)
        intent = ['commercial', 'construction', 'laser', 'pointer']
        result = check_pack_entity_coverage("prose", intent, ents)
        blocking = [m for m in result["missing"] if m["severity"] == "blocking"]
        assert len(blocking) == 1

    def test_supporting_entity_warning_not_blocking(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = tmp_path / "pack.md"
        pack.write_text(
            "- **[search] Top reviews comparison guide**\n"
            "  - Source: https://example.com/a\n"
            "  - Quote: \"a great guide\"\n",
            encoding="utf-8",
        )
        ents = parse_pack_entities(pack)
        intent = ['commercial', 'construction', 'laser', 'pointer']
        result = check_pack_entity_coverage("prose", intent, ents)
        for m in result["missing"]:
            assert m["severity"] == "warning"
        assert result["level"] == "warn" or result["level"] == "ok"

    def test_unrelated_concept_warning_only(self, tmp_path):
        from data_sources.modules.write_pre_check import (
            check_pack_entity_coverage,
            parse_pack_entities,
        )
        pack = tmp_path / "pack.md"
        pack.write_text(
            "- **[search] Stargazing telescope**\n"
            "  - Source: https://example.com/astro\n",
            encoding="utf-8",
        )
        ents = parse_pack_entities(pack)
        intent = ['commercial', 'construction', 'laser', 'pointer']
        result = check_pack_entity_coverage("prose", intent, ents)
        assert result["missing"] == []
        assert result["level"] == "ok"


class TestRevisionLoop:
    def _prepare(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        draft = _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T\n\nbody"
        )
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        lw.save_w2_state(tmp_path, "test-topic", _state_for(draft))

    def test_revision_can_start_a_new_explicit_batch_after_prior_cap(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, rounds=lw.MAX_REVISION_ROUNDS),
        )
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)

        called = []

        async def no_model(*args, **kwargs):
            called.append(True)
            raise RuntimeError("synthetic model unavailable")

        monkeypatch.setattr(lw, "_run_ai_text", no_model)

        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))
        assert result["success"] is False
        assert called, "a later explicit batch must reach the reviser"
        assert "synthetic model unavailable" in result["error"]

    def test_w1b_batch_retries_once_then_stops_on_pass(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        calls = []

        async def fake_revise(*args, **kwargs):
            calls.append(1)
            return {
                "success": len(calls) == 2,
                "gate_passed": len(calls) == 2,
                "revised": True,
                "revision_round": len(calls),
            }

        monkeypatch.setattr(lw, "stage_w1b_revise", fake_revise)
        result = asyncio.run(lw.stage_w1b_revise_batch("test topic", "", tmp_path))

        assert result["gate_passed"] is True
        assert result["batch_attempts"] == 2
        assert len(calls) == 2
        history = lw.load_w2_state(tmp_path, "test-topic")["revision_history"]
        assert [entry["phase"] for entry in history] == ["w1b", "w1b"]

    def test_w2_batch_retries_once_then_stops_on_pass(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        calls = []

        async def fake_revise(*args, **kwargs):
            calls.append(1)
            return {
                "success": len(calls) == 2,
                "gate_passed": len(calls) == 2,
                "revised": True,
                "stage": "w1b_pre_check",
                "revision_round": len(calls),
            }

        monkeypatch.setattr(lw, "stage_w2_revise", fake_revise)
        result = asyncio.run(lw.stage_w2_revise_batch("test topic", tmp_path))

        # A W2 revision that lands back at W1b must stop there; it may not
        # bypass the pre-check just to use the second automatic attempt.
        assert result["gate_passed"] is False
        assert result["batch_attempts"] == 1
        assert len(calls) == 1

    def test_revision_requires_a_report(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))
        assert result["success"] is False
        assert "后处理报告" in result["error"]

    def test_revision_backs_up_and_reruns(self, tmp_path, monkeypatch):
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        old_draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        original = old_draft.read_text(encoding="utf-8")
        new_draft = tmp_path / "drafts" / f"test-topic-{today}.md"

        async def fake_ai(purpose, *args, **kwargs):
            if purpose == "legacy_write_revise_body":
                return "---\nTitle: T\n---\nrevised body line.\n"
            assert purpose == "legacy_write_claim_ledger"
            return (
                '{"version":1,"claims":[{"sentence_id":"S001","claim_type":"general","evidence_ids":["ev_x001"]}]}\n'
            )

        async def fake_run(self, script, args):
            if script == "write_pre_check.py":
                return (
                    json.dumps(
                        {
                            "word_count": 1000,
                            "warn_count": 0,
                            "checks": [
                                {
                                    "item": "mechanical",
                                    "pass": True,
                                    "level": "ok",
                                    "detail": "ok",
                                }
                            ],
                        }
                    ),
                    "",
                    0,
                )
            return (
                "## 质量评分\n- 总分: 88.0 → ✅ 通过\n\n"
                "## 🚦 总门控\n- ✅ 通过，可进入段3 register。",
                "",
                0,
            )

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))
        assert result["gate_passed"] is True
        assert result["revision_round"] == 1
        new_draft_text = new_draft.read_text(encoding="utf-8")
        assert "revised body" in new_draft_text
        backup = tmp_path / "drafts" / "test-topic-2026-01-01.rev1.md"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original
        assert lw.load_w2_state(tmp_path, "test-topic")["rounds"] == 1

    def test_revision_stops_when_new_draft_fails_precheck(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)
        scripts = []

        async def fake_ai(purpose, *args, **kwargs):
            if purpose == "legacy_write_revise_body":
                return "---\nTitle: T\n---\nrevised body line.\n"
            assert purpose == "legacy_write_claim_ledger"
            return (
                '{"version":1,"claims":[{"sentence_id":"S001","claim_type":"general","evidence_ids":["ev_x001"]}]}\n'
            )

        async def fake_run(self, script, args):
            scripts.append(script)
            return (
                json.dumps(
                    {
                        "word_count": 20,
                        "warn_count": 0,
                        "checks": [
                            {
                                "item": "字数",
                                "pass": False,
                                "level": "fail",
                                "detail": "20",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                "",
                1,
            )

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))

        assert result["success"] is False
        assert result["stage"] == "w1b_pre_check"
        assert "预检仍有 1 项" in result["error"]
        assert scripts == ["write_pre_check.py"]

    def test_post_process_flags_human_review_at_cap(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, rounds=lw.MAX_REVISION_ROUNDS),
        )

        async def fake_run(self, script, args):
            return ("## 质量评分\n- 总分: 55.0 → ❌\n\n"
                    "## 🚦 总门控\n- ❌ **不可进入段3**：评分 <70。", "", 1)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        result = asyncio.run(lw.stage_w2_post_process("test topic", tmp_path))
        assert result["gate_passed"] is False
        assert result["needs_human_review"] is True
        assert result["needs_force_confirmation"] is False

    def test_post_process_flags_force_confirmation_at_cap(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, rounds=lw.MAX_REVISION_ROUNDS),
        )

        async def fake_run(self, script, args):
            return (_POST_PROCESS_REPORT, "", 1)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        result = asyncio.run(lw.stage_w2_post_process("test topic", tmp_path))
        assert result["needs_force_confirmation"] is True

    def test_force_requires_capped_confirmed_cannibal_block(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(
                draft,
                rounds=lw.MAX_REVISION_ROUNDS,
                cannibal_block=True,
                score_block=False,
            ),
        )
        seen = {}

        async def fake_run(self, script, args):
            seen["args"] = args
            return ("## 🚦 总门控\n- ✅ 通过，可进入段3 register。", "", 0)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        result = asyncio.run(
            lw.stage_w2_post_process("test topic", tmp_path, apply=True, force=True))
        assert "--apply" in seen["args"] and "--force" in seen["args"]
        assert result["stage"] == "w2_post_process"
        assert lw.load_w2_state(tmp_path, "test-topic")["applied"] is True

    def test_force_is_rejected_before_revision_cap(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(
                draft, rounds=1, cannibal_block=True, score_block=False
            ),
        )

        result = asyncio.run(
            lw.stage_w2_post_process("test topic", tmp_path, apply=True, force=True)
        )

        assert result["success"] is False
        assert "用满 2 轮" in result["error"]

    def test_post_process_requires_current_precheck(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        draft = _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T\n\nbody"
        )
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, precheck_passed=False),
        )

        result = asyncio.run(lw.stage_w2_post_process("test topic", tmp_path))

        assert result["success"] is False
        assert "重新运行预检" in result["error"]


class TestPostProcessFileSafety:
    def _prepare(self, tmp_path):
        website = tmp_path / "testsite"
        draft = _write(
            website / "drafts" / "test-topic-2026-01-01.md",
            (
                "---\nTitle: Test\nSlug: test-topic\nSEO Title: "
                "Test Topic Complete Reference Guide for Operators Today\n"
                "SEO Description: "
                + ("A" * 150)
                + "\nSEO Keywords: test topic\n---\n\n# Test Topic\n\n"
                "Original draft with [one](https://example.com/a) and "
                "[duplicate](https://example.com/a)."
            ),
        )
        return website, draft

    def _mock_score(self, monkeypatch, module, score=80):
        monkeypatch.setattr(
            module.subprocess,
            "run",
            lambda *args, **kwargs: MagicMock(
                stdout=json.dumps({"composite_score": score, "passed": score >= 70})
            ),
        )

    def test_cannibal_checker_resolves_configured_workspace(
        self, tmp_path, monkeypatch
    ):
        from data_sources.modules import cannibalization_checker as checker

        monkeypatch.setattr(checker, "SITES_DIR", tmp_path)
        _write(
            tmp_path / "testsite" / "published" / "article.md",
            "word " * 120,
        )

        instance = checker.CannibalizationChecker()

        assert instance.load_published("testsite") == 1
        assert "article" in instance.documents

    def test_check_only_does_not_change_draft(self, tmp_path, monkeypatch):
        from data_sources.modules import write_collector as wc

        _, draft = self._prepare(tmp_path)
        original = draft.read_bytes()
        monkeypatch.setattr(wc, "SITES_DIR", tmp_path)
        self._mock_score(monkeypatch, wc)
        monkeypatch.setattr(
            wc.seo_common,
            "cannibal_new_article",
            lambda *args, **kwargs: {"max_sim": 0.1, "top": []},
        )

        _, passed = wc.post_process("testsite", str(draft), apply=False)

        assert passed is True
        assert draft.read_bytes() == original
        assert not list(draft.parent.glob(".*-post-process-*.md"))

    def test_checker_crash_fails_closed_and_does_not_apply(
        self, tmp_path, monkeypatch
    ):
        from data_sources.modules import write_collector as wc

        _, draft = self._prepare(tmp_path)
        original = draft.read_bytes()
        monkeypatch.setattr(wc, "SITES_DIR", tmp_path)
        self._mock_score(monkeypatch, wc)

        def crash(*args, **kwargs):
            raise RuntimeError("checker unavailable")

        monkeypatch.setattr(wc.seo_common, "cannibal_new_article", crash)

        report, passed = wc.post_process("testsite", str(draft), apply=True)

        assert passed is False
        assert "蚕食检查运行失败" in report
        assert "未修改 draft" in report
        assert draft.read_bytes() == original


_REGISTER_CANDIDATES = """Title: New Article

## 回溯链接候选（AI 请读取旧文章后写清单）
> 新文章: [New](https://laserpointerhub.com/blog/new)

以下旧文章需要加回溯链接（最多 2 篇）：

**#7 — Laser Pointer Battery Guide**
- URL: https://laserpointerhub.com/blog/laser-pointer-battery-guide
- 当前内链: 2 条
- 可选插入位置: Battery Types, Charging
- 建议锚文本: `[battery — see our new guide](https://laserpointerhub.com/blog/new)`

**#12 — Laser Pointer Optics Guide**
- URL: https://laserpointerhub.com/blog/laser-pointer-optics-guide
- 当前内链: 3 条
- ⚠️ 已链接此文章，跳过
- 建议锚文本: `[optics](https://laserpointerhub.com/blog/new)`
"""


class TestBacklinkParsing:
    def test_parses_candidates(self):
        from seo_ops.services.legacy_workflow import _parse_backlink_candidates
        cands = _parse_backlink_candidates(_REGISTER_CANDIDATES)
        assert len(cands) == 2
        assert cands[0]["num"] == "7"
        assert cands[0]["slug"] == "laser-pointer-battery-guide"
        assert cands[0]["already_linked"] is False
        assert cands[1]["already_linked"] is True

    def test_no_section_returns_empty(self):
        from seo_ops.services.legacy_workflow import _parse_backlink_candidates
        assert _parse_backlink_candidates("Title: T\n\njust a draft") == []

    def test_register_needs_material_pack(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        draft = _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T"
        )
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, gate_passed=True, applied=True),
        )
        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))
        assert result["success"] is False
        assert "素材包" in result["error"]

    def test_register_requires_passed_and_applied_gate(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T")
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")

        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))

        assert result["success"] is False
        assert "尚未通过后处理" in result["error"]

    def test_register_rejects_draft_changed_after_apply(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        draft = _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T"
        )
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, gate_passed=True, applied=True),
        )
        draft.write_text("Title: changed after apply", encoding="utf-8")

        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))

        assert result["success"] is False
        assert "又被修改" in result["error"]

    def test_register_writes_checklist(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        draft = _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T")
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        _write(tmp_path / "published" / "laser-pointer-battery-guide.md", "old body")
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, gate_passed=True, applied=True),
        )

        async def fake_run(self, script, args):
            # register appends the candidate block to the draft itself
            draft.write_text(_REGISTER_CANDIDATES, encoding="utf-8")
            return ("✅ 注册完成", "", 0)

        async def fake_ai(*args, **kwargs):
            return "→ 以下旧文章加回溯链接\n#7, Battery Guide\nURL: https://x\n锚文本: `[a](b)`\n插入位置: Battery Types 段落后"

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))
        assert result["success"] is True
        assert result["backlink_candidates"] == 1  # #12 already linked → skipped
        out = Path(result["backlink_file"])
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "系统不会自动修改旧文章" in text
        assert "以下旧文章加回溯链接" in text

    def test_register_survives_backlink_ai_failure(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        draft = _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T")
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, gate_passed=True, applied=True),
        )

        async def fake_run(self, script, args):
            draft.write_text(_REGISTER_CANDIDATES, encoding="utf-8")
            return ("✅ 注册完成", "", 0)

        async def boom(*args, **kwargs):
            raise RuntimeError("AI 未配置")

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        monkeypatch.setattr(lw, "_run_ai_text", boom)

        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))
        assert result["success"] is True
        assert "AI 未配置" in result["backlink_note"]


class TestDisplayData:
    def test_reports_are_surfaced(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T\n\nbody")
        lw.save_report(tmp_path, "pre-check", "test-topic", "1. ❌ 字数\n2. ❌ Meta")
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)
        lw.save_w2_state(tmp_path, "test-topic", {"rounds": 1, "gate_passed": False})

        data = lw.get_legacy_display_data("test topic", tmp_path, db_stage="w1b_pre_check")
        assert data["stage"] == "w1b_pre_check"
        assert data["fail_count"] == 2
        assert "POST-PROCESS" in data["post_process_report"]
        assert data["w2"]["score"] == 64.5
        assert data["w2"]["rounds_used"] == 1
        # The displayed allowance is for the next explicitly requested batch,
        # not a lifetime cap shared by every future operator retry.
        assert data["w2"]["rounds_left"] == 2

    def test_backlink_checklist_surfaced(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md",
               "Title: T\n## 回溯链接候选\n**#1 — A**")
        _write(tmp_path / "research" / "backlink-suggestions-test-topic-2026-01-01.md",
               "→ 以下旧文章加回溯链接")
        data = lw.get_legacy_display_data("test topic", tmp_path)
        assert data["stage"] == "w3_register"
        assert "回溯链接" in data["backlink_checklist"]

    def test_empty_workspace_is_safe(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        data = lw.get_legacy_display_data("nothing here", tmp_path)
        assert data["stage"] == "r0_pending"
        assert data["stage_step"] == 0
        assert "w2" not in data


class TestSearchPrompt:
    def test_generates_eight_sections(self, tmp_path):
        from seo_ops.services.legacy_workflow import _build_search_prompt
        (tmp_path / "context").mkdir(parents=True)
        (tmp_path / "context" / "seo-data-manual.md").write_text("# test\n")
        (tmp_path / "published").mkdir(parents=True)
        (tmp_path / "published" / "published-index.json").write_text("[]")
        (tmp_path / "context" / "pain-points-library.md").write_text("# test\n")
        (tmp_path / "context" / "case-studies-library.md").write_text("# test\n")
        (tmp_path / "context" / "external-sources-library.md").write_text("# test\n")

        prompt = _build_search_prompt("laser pointer safety", tmp_path)
        assert "Section 1" in prompt
        assert "Section 2" in prompt
        assert "Section 3" in prompt
        assert "Section 4" in prompt
        assert "Section 5" in prompt
        assert "Section 6" in prompt
        assert "Section 7" in prompt
        assert "Section 8" in prompt

    def test_section_3_is_misconceptions_not_market_data(self, tmp_path):
        from seo_ops.services.legacy_workflow import _build_search_prompt
        (tmp_path / "context").mkdir(parents=True)
        (tmp_path / "context" / "seo-data-manual.md").write_text("# test\n")
        (tmp_path / "published").mkdir(parents=True)
        (tmp_path / "published" / "published-index.json").write_text("[]")
        (tmp_path / "context" / "pain-points-library.md").write_text("# test\n")
        (tmp_path / "context" / "case-studies-library.md").write_text("# test\n")
        (tmp_path / "context" / "external-sources-library.md").write_text("# test\n")

        prompt = _build_search_prompt("best laser pointer 2026", tmp_path)
        assert "Misconceptions" in prompt
        assert "Market Data" not in prompt

    def test_includes_gsc_keywords_when_present(self, tmp_path):
        from seo_ops.services.legacy_workflow import _build_search_prompt
        (tmp_path / "context").mkdir(parents=True)
        seo_text = """## A1. GSC Top 20 Queries
| # | Keyword | Impressions | CTR | Position |
|---|---:|---:|---:|
| 1 | laser pointer safety | 500 | 3.2% | 12.0 |
| 2 | laser safety glasses | 300 | 2.1% | 8.0 |"""
        (tmp_path / "context" / "seo-data-manual.md").write_text(seo_text)
        (tmp_path / "published").mkdir(parents=True)
        (tmp_path / "published" / "published-index.json").write_text("[]")
        for f in ["pain-points-library.md", "case-studies-library.md", "external-sources-library.md"]:
            (tmp_path / "context" / f).write_text("# test\n")

        prompt = _build_search_prompt("laser pointer safety", tmp_path)
        assert "laser pointer safety" in prompt
        assert "GSC keywords we rank for" in prompt


class TestLegacySync:
    def test_sync_all_returns_expected_keys(self, tmp_path, settings):
        from seo_ops.services.legacy_sync import sync_all

        report = sync_all(tmp_path, settings=settings, site_id=1)
        assert "published_index" in report
        assert "published_articles" in report
        assert "products" in report
        assert "internal_links_map" in report
        assert "seo_data_manual" in report

    def test_published_index_valid_json(self, tmp_path, settings):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path, settings=settings, site_id=1)
        idx_path = tmp_path / "published" / "published-index.json"
        assert idx_path.exists()
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        if data:
            entry = data[0]
            assert "slug" in entry
            assert "title" in entry
            assert "tags" in entry
            assert "url" in entry

    def test_products_table_generated(self, tmp_path, settings):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path, settings=settings, site_id=1)
        prod_path = tmp_path / "products" / "live_products_report.md"
        assert prod_path.exists()
        text = prod_path.read_text(encoding="utf-8")
        assert "Live Products Report" in text

    def test_ilm_generated(self, tmp_path, settings):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path, settings=settings, site_id=1)
        ilm_path = tmp_path / "context" / "internal-links-map.md"
        assert ilm_path.exists()
        text = ilm_path.read_text(encoding="utf-8")
        assert "Internal Links Map" in text
        assert "已发布文章" in text

    def test_seo_manual_generated(self, tmp_path, settings):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path, settings=settings, site_id=1)
        manual_path = tmp_path / "context" / "seo-data-manual.md"
        assert manual_path.exists()
        text = manual_path.read_text(encoding="utf-8")
        assert "SEO Data Manual" in text

    def test_published_sync_removes_files_not_in_active_snapshot(self, tmp_path):
        from seo_ops.services.legacy_sync import _gen_published_articles

        class Cursor:
            def fetchall(self):
                return [
                    (
                        "active-article",
                        "Active Article",
                        "https://example.com/active",
                        "Current body",
                        "",
                        "",
                        "{}",
                        "",
                    )
                ]

        class Connection:
            def execute(self, _sql, _params=None):
                return Cursor()

        stale = _write(tmp_path / "published" / "inactive-article.md", "old body")
        report = _gen_published_articles(Connection(), tmp_path)

        assert not stale.exists()
        assert (tmp_path / "published" / "active-article.md").exists()
        assert report["removed"] == 1

    def test_sync_all_with_settings_and_site_id(self, settings):
        """sync_all(settings=, site_id=) uses the settings-specified DB and data_dir."""
        from seo_ops.services.legacy_sync import sync_all

        report = sync_all(settings=settings, site_id=1)
        assert "published_index" in report
        assert "published_articles" in report
        assert "products" in report
        assert "internal_links_map" in report
        assert "seo_data_manual" in report

        workspace_root = settings.data_dir / "legacy_workflow" / "laserpointerhub"
        assert (workspace_root / "published" / "published-index.json").exists()
        assert (workspace_root / "context" / "internal-links-map.md").exists()
        assert (workspace_root / "context" / "seo-data-manual.md").exists()

    def test_sync_all_with_settings_different_site_id(
        self, settings, tmp_path
    ):
        """Passing site_id=999 to an empty DB produces files with zero rows."""
        from seo_ops.services.legacy_sync import sync_all

        report = sync_all(settings=settings, site_id=999)
        idx = json.loads(
            (
                settings.data_dir
                / "legacy_workflow"
                / "laserpointerhub"
                / "published"
                / "published-index.json"
            ).read_text(encoding="utf-8")
        )
        assert idx == []  # no content_items with site_id=999
        assert report["published_articles"]["count"] == 0

    def test_sync_all_workspace_respects_settings_data_dir(self, settings):
        """When workspace is None and settings is given, workspace derives from data_dir."""
        from seo_ops.services.legacy_sync import sync_all

        sync_all(settings=settings)
        expected = settings.data_dir / "legacy_workflow" / "laserpointerhub"
        assert (expected / "published" / "published-index.json").exists()
        # Ensure it did NOT fall back to the hardcoded WORKSPACE_ROOT
        hardcoded = Path("data/legacy_workflow/laserpointerhub")
        assert not hardcoded.exists() or not (
            hardcoded / "published" / "published-index.json"
        ).exists() or (
            (expected / "published" / "published-index.json")
            != (hardcoded / "published" / "published-index.json")
        )


class TestDraftFrontmatterRepair:
    """Detect drafts that miss the closing `---` and self-heal so the
    pre-check parser can read SEO Title / Description.

    This is the parser-level fix for the production action where the
    W0 AI output a draft with only an opening `---` and no closer,
    causing the pre-check to report SEO Title/Description as 0 chars.
    """

    def test_well_formed_frontmatter_is_left_alone(self, tmp_path):
        from seo_ops.services.legacy_workflow import repair_draft_frontmatter

        slug = "well-formed-test"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        draft.write_text(
            "---\n"
            "Title: OK\n"
            "SEO Title: Commercial Ceiling Laser Pointer: 5mW Green\n"
            "SEO Description: A real SEO description that is over a hundred and fifty chars to pass the gate check easily.\n"
            "---\n\n"
            "# H1\n\nBody\n",
            encoding="utf-8",
        )
        result = repair_draft_frontmatter(ws, slug)
        assert result["repaired"] is False
        assert result["reason"] == "already_well_formed"
        # File unchanged (no double `---` injected).
        assert draft.read_text(encoding="utf-8").count("---") == 2

    def test_missing_closing_frontmatter_gets_inserted(self, tmp_path):
        from seo_ops.services.legacy_workflow import repair_draft_frontmatter

        slug = "missing-closer"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        seo_desc = (
            "A real SEO description that is well over a hundred and fifty chars "
            "to pass the gate check easily and demonstrate the parser now sees "
            "the full frontmatter after repair."
        )
        original = (
            "---\n"
            "Title: Needs Repair\n"
            "SEO Title: Commercial Ceiling Laser Pointer: 5mW Green\n"
            f"SEO Description: {seo_desc}\n"
            "\n"
            "# H1\n\nBody\n"
        )
        draft.write_text(original, encoding="utf-8")
        assert draft.read_text(encoding="utf-8").count("---") == 1

        result = repair_draft_frontmatter(ws, slug)
        assert result["repaired"] is True
        assert result["reason"] == "inserted_closing"

        new_text = draft.read_text(encoding="utf-8")
        # Now has 2 `---` and parser can read the frontmatter.
        assert new_text.count("---") == 2
        # The H1 is preserved.
        assert "\n# H1" in new_text

        # And the parser can read it.
        from data_sources.modules.write_pre_check import parse_draft
        meta, body, *_ = parse_draft(str(draft))
        assert meta.get("seo title", "").startswith("Commercial Ceiling")
        assert len(meta.get("seo description", "")) > 100

    def test_no_draft_returns_clean_result(self, tmp_path):
        from seo_ops.services.legacy_workflow import repair_draft_frontmatter

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        result = repair_draft_frontmatter(ws, "nope")
        assert result["repaired"] is False
        assert result["reason"] == "no_draft"

    def test_no_frontmatter_is_left_alone(self, tmp_path):
        from seo_ops.services.legacy_workflow import repair_draft_frontmatter

        slug = "no-fm"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        draft.write_text("# H1\n\nBody with no frontmatter\n", encoding="utf-8")
        result = repair_draft_frontmatter(ws, slug)
        assert result["repaired"] is False
        assert result["reason"] == "no_frontmatter"


class TestPrecheckGateDisplay:
    """Verify the display data and template gate the W2 button by the
    precheck_state field. The W2 button must NOT appear in the rendered
    HTML when precheck_passed is False or the draft SHA has changed."""

    def _make_action(self, tmp_path, *, action_id=1):

        from seo_ops.config import Settings
        from seo_ops.db import connection, init_db

        data_dir = tmp_path / "data"
        s = Settings(
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
        init_db(s)
        with connection(s) as conn:
            conn.execute(
                "INSERT INTO sites(slug, name, domain, created_at, updated_at) "
                "VALUES('s1', 'S1', 'example.com', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            site_id = conn.execute("SELECT id FROM sites").fetchone()["id"]
            conn.execute(
                "INSERT INTO analysis_runs(site_id, method_version, "
                "source_import_ids_json, status, started_at) "
                "VALUES(?, '1.0', '[]', 'success', '2026-01-01T00:00:00+00:00')",
                (site_id,),
            )
            analysis_run_id = conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO opportunities(analysis_run_id, site_id, rule_key, "
                "opportunity_type, target_kind, target_ref, title, recommended_action, "
                "gate_status, gate_reasons_json, evidence_json, strength, confidence, "
                "confidence_weight, effort, priority, method_version, status, "
                "created_at) "
                "VALUES(?, ?, 'topic_gap', 'new_article', 'topic', ?, 'Test', 'create', "
                "'passed', '[]', '{}', 0.0, 'low', 0.0, 0.0, 0.0, '1.0', 'accepted', "
                "'2026-01-01T00:00:00+00:00')",
                (analysis_run_id, site_id, "Precheck Gate Test Topic"),
            )
            opp_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.execute(
                "INSERT INTO actions(site_id, opportunity_id, action_type, target_ref, "
                "decision, baseline_json, decided_at, updated_at, legacy_stage) "
                "VALUES(?, ?, 'create', ?, 'accepted', '{}', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', "
                "'w1b_pre_check')",
                (site_id, opp_id, "Precheck Gate Test Topic"),
            )
            new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.commit()
        return s, new_id

    def _write_draft(self, settings, action_id, slug, body="Body\n"):
        from seo_ops.services.legacy_workflow import action_workspace
        ws = action_workspace(
            settings.data_dir / "legacy_workflow" / "laserpointerhub",
            action_id,
        )
        draft_dir = ws / "drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft = draft_dir / f"{slug}-2026-01-15.md"
        draft.write_text(
            "---\n"
            "Title: T\n"
            "SEO Title: A reasonable SEO title at fifty five chars long now\n"
            "SEO Description: " + ("x" * 160) + "\n"
            "---\n\n# H1\n\n" + body,
            encoding="utf-8",
        )
        return draft

    def _set_state(self, settings, action_id, slug, *, precheck_passed, sha, rounds=0):
        from seo_ops.services.legacy_workflow import action_workspace, save_w2_state
        ws = action_workspace(
            settings.data_dir / "legacy_workflow" / "laserpointerhub",
            action_id,
        )
        save_w2_state(ws, slug, {
            "rounds": rounds,
            "gate_passed": False,
            "applied": False,
            "precheck_passed": precheck_passed,
            "precheck_draft_sha256": sha,
        })

    def _get_display(self, settings, action_id, topic):
        from seo_ops.services.legacy_workflow import get_legacy_display_data
        ws = (
            settings.data_dir
            / "legacy_workflow"
            / "laserpointerhub"
            / "runs"
            / f"action-{action_id}"
            / "current"
            / "laserpointerhub"
        )
        return get_legacy_display_data(topic, ws, db_stage="w1b_pre_check")

    def test_display_state_failed_when_precheck_not_passed(self, tmp_path):

        s, action_id = self._make_action(tmp_path)
        slug = "precheck-gate-test-topic"
        draft = self._write_draft(s, action_id, slug)
        # Set state to "precheck ran but failed" (mismatched: real sha
        # vs a fake sha recorded in state).
        self._set_state(s, action_id, slug, precheck_passed=False, sha="0" * 64)

        display = self._get_display(s, action_id, "Precheck Gate Test Topic")
        assert display["precheck_state"] == "failed"
        assert display["precheck_passed"] is False
        assert display["precheck_blocker_message"]
        assert "draft_full" in display
        # W2 must not be enabled when precheck didn't pass.
        assert display["precheck_passed"] is False

    def test_display_state_stale_sha_when_draft_changed(self, tmp_path):
        from seo_ops.services.legacy_workflow import _sha256_file
        s, action_id = self._make_action(tmp_path)
        slug = "precheck-gate-test-topic"
        draft = self._write_draft(s, action_id, slug, body="Original body\n")
        original_sha = _sha256_file(draft)
        # Then mutate the draft.
        draft.write_text(draft.read_text(encoding="utf-8") + "EXTRA\n", encoding="utf-8")

        self._set_state(s, action_id, slug, precheck_passed=True, sha=original_sha)
        display = self._get_display(s, action_id, "Precheck Gate Test Topic")
        assert display["precheck_state"] == "stale_sha"
        assert "草稿已被修改" in display["precheck_blocker_message"]

    def test_display_state_passed_when_all_good(self, tmp_path):
        from seo_ops.services.legacy_workflow import _sha256_file
        s, action_id = self._make_action(tmp_path)
        slug = "precheck-gate-test-topic"
        draft = self._write_draft(s, action_id, slug)
        sha = _sha256_file(draft)
        self._set_state(s, action_id, slug, precheck_passed=True, sha=sha)
        display = self._get_display(s, action_id, "Precheck Gate Test Topic")
        assert display["precheck_state"] == "passed"
        assert display["precheck_passed"] is True
        assert display["precheck_blocker_message"] == ""

    def test_template_hides_w2_button_when_precheck_failed(self, tmp_path):
        import re

        from fastapi.testclient import TestClient

        from seo_ops.web.app import create_app

        s, action_id = self._make_action(tmp_path)
        slug = "precheck-gate-test-topic"
        draft = self._write_draft(s, action_id, slug)
        self._set_state(s, action_id, slug, precheck_passed=False, sha="0" * 64)

        app = create_app(s)
        client = TestClient(app)
        resp = client.get("/actions")
        assert resp.status_code == 200
        body = resp.text
        forms = re.findall(
            r'action="(/actions/\d+/legacy/stage/[\w-]+)"', body
        )
        # W2 must NOT be present in any legacy form action.
        assert not any(f.endswith("/legacy/stage/w2") for f in forms), (
            f"W2 button leaked into the page even though precheck failed: {forms}"
        )
        # Re-run W1b SHOULD be present.
        assert any(f.endswith("/legacy/stage/w1b") for f in forms), (
            f"Re-run W1b button missing: {forms}"
        )
        # AI-revise + rerun SHOULD be present.
        assert any(f.endswith("/legacy/stage/w1b-revise") for f in forms), (
            f"AI-revise + rerun W1b button missing: {forms}"
        )
        # R0 restart stays available after the task has started, even when a
        # later gate is blocking progress.
        assert any(f.endswith("/legacy/stage/r0") for f in forms), (
            f"R0 restart button missing from blocked stage: {forms}"
        )
        # Warning banner is shown.
        assert "legacy-warning" in body, "Warning banner missing"

    def test_template_shows_w2_button_when_precheck_passed(self, tmp_path):
        import re

        from fastapi.testclient import TestClient

        from seo_ops.services.legacy_workflow import (
            _sha256_file,
            action_workspace,
            get_legacy_display_data,
        )
        from seo_ops.web.app import create_app

        s, action_id = self._make_action(tmp_path)
        slug = "precheck-gate-test-topic"
        draft = self._write_draft(s, action_id, slug)
        sha = _sha256_file(draft)
        self._set_state(s, action_id, slug, precheck_passed=True, sha=sha)

        # Debug: check what the display data shows.
        ws = action_workspace(
            s.data_dir / "legacy_workflow" / "laserpointerhub",
            action_id,
        )
        disp = get_legacy_display_data(
            "Precheck Gate Test Topic", ws, db_stage="w1b_pre_check"
        )
        assert disp["precheck_state"] == "passed", (
            f"Test setup wrong: {disp.get('precheck_state')}, "
            f"precheck_passed={disp.get('precheck_passed')}, "
            f"draft_match={disp.get('precheck_draft_match')}"
        )

        app = create_app(s)
        client = TestClient(app)
        resp = client.get("/actions")
        assert resp.status_code == 200
        body = resp.text
        forms = re.findall(
            r'action="(/actions/\d+/legacy/stage/[\w-]+)"', body
        )
        # W2 button is visible.
        assert any(f.endswith("/legacy/stage/w2") for f in forms), (
            f"W2 button missing even though precheck passed: {forms}"
        )
        # Re-run W1b button must NOT be present (no need to fix).
        assert not any(f.endswith("/legacy/stage/w1b\"") for f in forms), (
            f"Rerun W1b button should be hidden when precheck passed: {forms}"
        )
        # No warning banner.
        assert "legacy-warning" not in body, (
            "Warning banner should be hidden when precheck passed"
        )


# ── End-to-end integration tests for evidence-ledger / claim-ledger ──────


class TestW2ReviseEvidence闭环:
    """Fix1 + Fix5: W2 must include evidence refs, parse+validate claim ledger,
    inject SHA, atomic write, reject after gate_passed/applied."""

    def _prepare_full(self, tmp_path):
        """Set up a minimal workspace with material pack + evidence + claim ledgers."""
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        slug = "test-topic"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        mp_path = ws / "material-packs" / f"{slug}-{today}.md"
        mp_path.write_text("The 5mW green laser pointer has a wavelength of 532nm.", encoding="utf-8")

        ev_path = ws / "research" / f"evidence-ledger-{slug}.json"
        ev_path.write_text(
            json.dumps({
                "version": 1,
                "material_pack_sha256": hashlib.sha256(
                    b"The 5mW green laser pointer has a wavelength of 532nm."
                ).hexdigest(),
                "evidence": [{
                    "evidence_id": "ev_001",
                    "source_url": "https://example.com/laser",
                    "quote": "532nm green laser",
                    "canonical_concepts": ["laser", "532nm"],
                    "claim_types": ["spec"],
                    "required": False,
                }],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        draft_path = ws / "drafts" / f"{slug}-{today}.md"
        draft_body = (
            "---\n"
            "Title: Test Topic\n"
            "Slug: test-topic\n"
            "Author: Test\n"
            "Summary: Summary.\n"
            "Tags: test\n"
            "SEO Title: Test Topic SEO Title Is Long Enough For Validation\n"
            "SEO Description: " + "A" * 152 + "\n"
            "SEO Keywords: test, topic\n"
            "---\n\n"
            "# Test Topic\n\n"
            "The 5mW green laser pointer has a wavelength of 532nm. "
            "It is used for presentations.\n\n"
            "> **Key Takeaways**\n"
            "> - Takeaway 1\n\n"
            "## Section A\n\n"
            "Body text for section A.\n\n"
            "## Frequently Asked Questions\n\n"
            "### Q: What is it?\n\n"
            "A: A laser.\n\n"
            '<script type="application/ld+json">\n{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}\n</script>\n'
        )
        draft_path.write_text(draft_body, encoding="utf-8")

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        cl_path.write_text(
            json.dumps({
                "version": 1,
                "claims": [{
                    "claim_text": "The 5mW green laser pointer has a wavelength of 532nm.",
                    "claim_type": "technical_specification",
                    "evidence_ids": ["ev_001"],
                }],
                "draft_sha256": dr_sha,
            }),
            encoding="utf-8",
        )

        lw.save_report(
            ws, "post-process", slug,
            "# POST-PROCESS 报告\n\n## 质量评分\n- 总分: 55 → ❌ 未达标\n\n## 🚦 总门控\n- ❌ **不可进入段3**\n",
        )

        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": False, "applied": False,
             "precheck_passed": True, "precheck_tier": "Cluster Content"},
        )

        return ws, slug, draft_path, cl_path, ev_path, mp_path

    def test_w2_rejects_after_gate_passed(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, _, _, _, _ = self._prepare_full(tmp_path)
        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": True, "applied": False,
             "precheck_passed": True},
        )

        async def fake_ai(*a, **kw):
            return "Title: Revised\n\nRevised body.\n"

        result = asyncio.run(lw.stage_w2_revise("test topic", ws))
        assert result["success"] is False
        assert "已通过预检" in result["error"]

    def test_w2_rejects_after_applied(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, _, _, _, _ = self._prepare_full(tmp_path)
        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": False, "applied": True,
             "precheck_passed": True},
        )

        async def fake_ai(*a, **kw):
            return "Title: Revised\n\nRevised body.\n"

        result = asyncio.run(lw.stage_w2_revise("test topic", ws))
        assert result["success"] is False
        assert "已发布" in result["error"]

    def test_w2_illegal_ledger_does_not_change_draft(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path, _, _ = self._prepare_full(tmp_path)
        original_draft = draft_path.read_text(encoding="utf-8")

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return "Title: Revised\n\nRevised body text here.\n"
            return "NOT JSON AT ALL"

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        result = asyncio.run(lw.stage_w2_revise("test topic", ws))
        assert result["success"] is False
        assert "claim-ledger 生成/校验失败" in result["error"]
        assert draft_path.read_text(encoding="utf-8") == original_draft

    def test_w2_no_separator_leaves_draft_unchanged(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, _, _, _ = self._prepare_full(tmp_path)
        original_draft = draft_path.read_text(encoding="utf-8")

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return "Title: Revised\n\nRevised body with no claim ledger."
            return "not json"

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        result = asyncio.run(lw.stage_w2_revise("test topic", ws))
        assert result["success"] is False
        assert "CLAIM_LEDGER batch 1/1 JSON parse failed" in result["error"]
        assert draft_path.read_text(encoding="utf-8") == original_draft

    def test_w2_end_to_end_revise_and_precheck(self, tmp_path, monkeypatch):
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path, _, _ = self._prepare_full(tmp_path)
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        precheck_called = []

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return (
                    "---\n"
                    "Title: Revised Topic\n"
                    "Slug: test-topic\n"
                    "Author: Test\n"
                    "Summary: Revised summary.\n"
                    "Tags: test\n"
                    "SEO Title: Revised Topic SEO Title Is Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: revised, test\n"
                    "---\n\n"
                    "# Revised Topic\n\n"
                    "The 5mW green laser pointer has a wavelength of 532nm. "
                    "It is used for presentations and stars.\n\n"
                    "> **Key Takeaways**\n"
                    "> - Revised takeaway 1\n\n"
                    "## Section A\n\n"
                    "Body text for section A with more detail.\n\n"
                    "## Frequently Asked Questions\n\n"
                    "### Q: What is it?\n\n"
                    "A: A laser.\n\n"
                    '<script type="application/ld+json">\n'
                    '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}\n'
                    "</script>\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return (
                    '{"version":1,"claims":[\n'
                    '{"sentence_id":"S001","claim_type":"technical_specification","evidence_ids":["ev_001"]},\n'
                    '{"sentence_id":"S002","claim_type":"general","evidence_ids":["ev_001"]}\n'
                    ']}\n'
                )
            raise AssertionError(f"unexpected purpose: {purpose}")

        async def fake_run(self, script, args):
            precheck_called.append(script)
            if script == "write_pre_check.py":
                return (
                    json.dumps({
                        "word_count": 500,
                        "warn_count": 0,
                        "checks": [{"item": "字数", "pass": True, "level": "ok", "detail": "500"}],
                    }),
                    "", 0,
                )
            return ("## 质量评分\n- 总分: 88.0 → ✅ 通过\n\n"
                    "## 🚦 总门控\n- ✅ 通过，可进入段3 register。", "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w2_revise("test topic", ws))
        assert result.get("success") is not False, f"W2 revise failed: {result}"
        assert "revised" in result or result.get("gate_passed") is True
        assert "write_pre_check.py" in precheck_called

        new_draft = draft_path.read_text(encoding="utf-8")
        assert "Revised Topic" in new_draft
        assert "stars" in new_draft

        backup = tmp_path / "ws" / "drafts" / f"test-topic-{today}.rev1.md"
        assert backup.exists(), f"Backup file missing: {backup}"
        assert "Test Topic" in backup.read_text(encoding="utf-8")


class TestAtomicWrite:
    """Fix2: _write_ahead_draft_and_ledger must be truly atomic."""

    def test_second_replace_failure_rolls_back_both_files(self, tmp_path, monkeypatch):
        import os as _os

        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)

        slug = "atomic-test"
        old_draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        old_cl = ws / "research" / f"claim-ledger-{slug}.json"
        old_draft.write_text("old draft content", encoding="utf-8")
        old_cl.write_text('{"version":1,"claims":[]}', encoding="utf-8")

        new_draft_text = "new draft content"
        new_cl_data = {
            "version": 1,
            "claims": [],
            "draft_sha256": hashlib.sha256(new_draft_text.encode()).hexdigest(),
        }

        original_replace = _os.replace
        replace_count = [0]

        def bad_replace(src, dst):
            replace_count[0] += 1
            if replace_count[0] == 2:
                raise OSError("simulated second replace failure")
            return original_replace(src, dst)

        monkeypatch.setattr(_os, "replace", bad_replace)

        with pytest.raises(OSError, match="simulated second replace failure"):
            lw._write_ahead_draft_and_ledger(ws, slug, new_draft_text, new_cl_data)

        assert old_draft.read_text(encoding="utf-8") == "old draft content"
        assert old_cl.read_text(encoding="utf-8") == '{"version":1,"claims":[]}'

    def test_second_replace_failure_removes_both_when_neither_existed(self, tmp_path, monkeypatch):
        """Reproduce: both files missing initially, second replace fails,
        result must be both files still missing (not new draft + nothing)."""
        import os as _os
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)

        slug = "atomic-both-missing"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        expected_draft = ws / "drafts" / f"{slug}-{today}.md"
        expected_cl = ws / "research" / f"claim-ledger-{slug}.json"

        assert not expected_draft.exists(), "precondition: draft must not exist"
        assert not expected_cl.exists(), "precondition: claim ledger must not exist"

        new_draft_text = "new draft content"
        new_cl_data = {
            "version": 1,
            "claims": [],
            "draft_sha256": hashlib.sha256(new_draft_text.encode()).hexdigest(),
        }

        original_replace = _os.replace
        replace_count = [0]

        def bad_replace(src, dst):
            replace_count[0] += 1
            if replace_count[0] == 2:
                raise OSError("simulated second replace failure")
            return original_replace(src, dst)

        monkeypatch.setattr(_os, "replace", bad_replace)

        with pytest.raises(OSError, match="simulated second replace failure"):
            lw._write_ahead_draft_and_ledger(ws, slug, new_draft_text, new_cl_data)

        assert not expected_draft.exists(), (
            f"draft leaked after rollback: {expected_draft}"
        )
        assert not expected_cl.exists(), (
            f"claim ledger leaked after rollback: {expected_cl}"
        )

    def test_write_creates_both_files_atomically(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)

        slug = "atomic-ok"
        result = lw._write_ahead_draft_and_ledger(
            ws, slug,
            "draft content here",
            {"version": 1, "claims": [], "draft_sha256": "abc123"},
        )

        draft_path = Path(result["draft_path"])
        cl_path = Path(result["claim_path"])
        assert draft_path.exists()
        assert cl_path.exists()
        assert draft_path.read_text(encoding="utf-8") == "draft content here"
        assert '"version": 1' in cl_path.read_text(encoding="utf-8")


class TestW0AtomicWriteFailure:
    """W0 must preserve old artifacts if _write_ahead_draft_and_ledger fails."""

    def _prep_workspace(self, tmp_path, slug):
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        today = datetime.now(UTC).strftime("%Y-%m-%d")

        mp_path = ws / "material-packs" / f"{slug}-{today}.md"
        mp_path.write_text("material pack content for AI.", encoding="utf-8")

        draft_path = ws / "drafts" / f"{slug}-{today}.md"
        draft_body = (
            "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
            "Summary: S.\nTags: t\n"
            "SEO Title: T SEO Title Long Enough For Validation\n"
            "SEO Description: " + "A" * 152 + "\n"
            "SEO Keywords: t\n"
            "---\n\n"
            "# T\n\n"
            "This is a body sentence.\n\n"
        )
        draft_path.write_text(draft_body, encoding="utf-8")

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        cl_path.write_text(json.dumps({
            "version": 1,
            "claims": [{
                "claim_text": "This is a body sentence.",
                "claim_type": "general",
                "evidence_ids": ["ev_001"],
            }],
            "draft_sha256": dr_sha,
        }), encoding="utf-8")

        lw.save_report(
            ws, "post-process", slug,
            "# POST-PROCESS\n## 质量评分\n- 总分: 55 → ❌\n",
        )
        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": False, "applied": False,
             "precheck_passed": True, "precheck_tier": "Cluster Content"},
        )

        return ws, slug, draft_path, cl_path

    def test_w0_second_replace_failure_preserves_old_artifacts(self, tmp_path, monkeypatch):
        import os as _os

        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path = self._prep_workspace(tmp_path, slug="w0-replace-fail")
        state_path = ws / "reports" / f"w2-state-{slug}.json"

        draft_snapshot = draft_path.read_bytes()
        cl_snapshot = cl_path.read_bytes()
        state_snapshot = state_path.read_bytes()

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_body":
                return (
                    "---\nTitle: T\nSlug: w0-replace-fail\nAuthor: T\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: t\n"
                    "---\n\n"
                    "# T\n\n"
                    "new body sentence.\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return (
                    '{"version":1,"claims":[{"sentence_id":"S001",'
                    '"claim_type":"spec","evidence_ids":["ev_001"]}]}\n'
                )
            raise AssertionError(f"unexpected purpose: {purpose}")

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        original_replace = _os.replace
        replace_count = [0]

        def bad_replace(src, dst):
            replace_count[0] += 1
            if replace_count[0] == 2:
                raise OSError("simulated second replace failure")
            return original_replace(src, dst)

        monkeypatch.setattr(_os, "replace", bad_replace)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("w0 replace fail", "Test", ws))

        assert result.get("success") is False
        assert "simulated second replace failure" in result.get("error", "")
        assert draft_path.read_bytes() == draft_snapshot, "old draft was modified"
        assert cl_path.read_bytes() == cl_snapshot, "old claim ledger was modified"
        assert state_path.read_bytes() == state_snapshot, "old w2 state was modified"

    def test_w0_uses_compact_body_and_separate_claim_ledger_prompts(self, tmp_path, monkeypatch):
        """W0 must split body writing from the strict ledger audit task."""
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "w0-contract"
        (ws / "material-packs").mkdir(parents=True)
        slug = "w0-output-contract"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        (ws / "material-packs" / f"{slug}-{today}.md").write_text(
            "Material pack content.", encoding="utf-8"
        )
        captured = {}

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            captured[purpose] = (system_prompt, user_prompt)
            if purpose == "legacy_write_body":
                return "# Draft\n\nUse the material pack as the source of truth.\n"
            assert purpose == "legacy_write_claim_ledger"
            return '{"version":1,"claims":[]}'

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("w0 output contract", "Test", ws))

        assert result["success"] is True
        body_system, body_user = captured["legacy_write_body"]
        ledger_system, ledger_user = captured["legacy_write_claim_ledger"]
        assert "Return only the complete article Markdown" in body_system
        assert "Compact Write Brief" in body_user
        assert "Coverage Contract" in body_user
        assert "Material Pack" not in body_user
        assert "You are an evidence auditor for an SEO article." in ledger_system
        assert "sentence_id" in ledger_system
        assert "Article sentences (use these S-IDs verbatim)" in ledger_user


class TestSentenceNormalizationUnified:
    """Fix3: W0/W1b must use the same sentence extraction logic."""

    def test_intra_paragraph_sentence_matched_by_w0_validation(self, tmp_path):
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        draft_body = (
            "The 5mW green laser has a wavelength of 532nm. "
            "It is widely used in presentations."
        )
        cl_json = json.dumps({
            "version": 1,
            "claims": [{
                "claim_text": "The 5mW green laser has a wavelength of 532nm.",
                "claim_type": "technical_specification",
                "evidence_ids": ["ev_001"],
            }],
        })

        result = _validate_claim_ledger_json(cl_json, draft_body)
        assert result["version"] == 1
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_text"] == "The 5mW green laser has a wavelength of 532nm."

    def test_multiline_sentence_matched_by_w0_validation(self, tmp_path):
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        draft_body = (
            "The 5mW green laser has a wavelength of 532nm and "
            "produces a beam divergence of less than 2mrad."
        )
        cl_json = json.dumps({
            "version": 1,
            "claims": [{
                "claim_text": "The 5mW green laser has a wavelength of 532nm and produces a beam divergence of less than 2mrad.",
                "claim_type": "technical_specification",
                "evidence_ids": ["ev_001"],
            }],
        })

        result = _validate_claim_ledger_json(cl_json, draft_body)
        assert result["version"] == 1


class TestFactCheckSchemaValidation:
    """Fix4: _run_fact_check must handle non-dict evidence/claim items gracefully."""

    def test_evidence_ledger_root_list_blocked(self, tmp_path):
        """Evidence ledger root JSON is a list → fail closed, no .get() call."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        dr_f.write_text("claim sentence here.", encoding="utf-8")

        ev_f.write_text("[]", encoding="utf-8")
        cl_f.write_text(
            '{"version":1,"claims":[],"draft_sha256":"' + "0" * 64 + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block root list evidence: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "evidence-ledger" in joined or "不是" in joined or "object" in joined.lower(), joined

    def test_evidence_ledger_root_null_blocked(self, tmp_path):
        """Evidence ledger root JSON is null → fail closed, no .get() call."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        dr_f.write_text("claim sentence here.", encoding="utf-8")

        ev_f.write_text("null", encoding="utf-8")
        cl_f.write_text(
            '{"version":1,"claims":[],"draft_sha256":"' + "0" * 64 + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block null evidence: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "evidence-ledger" in joined or "不是" in joined or "object" in joined.lower(), joined

    def test_claim_ledger_root_list_blocked(self, tmp_path):
        """Claim ledger root JSON is a list → fail closed, no .get() call."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim sentence here.", encoding="utf-8")

        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[]}',
            encoding="utf-8",
        )
        cl_f.write_text("[]", encoding="utf-8")
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block root list claim: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim-ledger" in joined or "不是" in joined or "object" in joined.lower(), joined

    def test_claim_ledger_root_null_blocked(self, tmp_path):
        """Claim ledger root JSON is null → fail closed, no .get() call."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim sentence here.", encoding="utf-8")

        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[]}',
            encoding="utf-8",
        )
        cl_f.write_text("null", encoding="utf-8")
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block null claim: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim-ledger" in joined or "不是" in joined or "object" in joined.lower(), joined

    def test_evidence_item_not_dict_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim sentence here.", encoding="utf-8")
        dr_sha = hashlib.sha256(b"claim sentence here.").hexdigest()

        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":["not a dict string", 123, null]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"claim sentence here.","claim_type":"general","evidence_ids":["ev_001"]}],"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block non-dict evidence: {fact}"

    def test_claim_item_not_dict_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim sentence here.", encoding="utf-8")
        dr_sha = hashlib.sha256(b"claim sentence here.").hexdigest()

        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com","quote":"data","canonical_concepts":[],"claim_types":["spec"],"required":false}]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":["not a dict", 123, null],"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block non-dict claim: {fact}"

    def test_evidence_ids_field_not_list_blocked(self, tmp_path):
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("claim sentence here.", encoding="utf-8")
        dr_sha = hashlib.sha256(b"claim sentence here.").hexdigest()

        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com","quote":"data","canonical_concepts":[],"claim_types":["spec"],"required":false}]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"claim sentence here.","claim_type":"general","evidence_ids":"ev_001"}],"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert not all(r['pass'] for r in fact), f"Should block string evidence_ids: {fact}"

    # ── Type-validation blocking tests (Fix4 round 2) ──

    def _fact_check_grade(self, tmp_path, ev_text, cl_text,
                          draft_text="claim sentence here."):
        """Helper: run _run_fact_check and return the fact-check grade entries."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"

        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text(draft_text, encoding="utf-8")
        dr_sha = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()

        # Allow caller to embed mp_sha/draft_sha as placeholders.
        ev_text = ev_text.replace("__MP_SHA__", mp_sha)
        cl_text = cl_text.replace("__DR_SHA__", dr_sha)

        ev_f.write_text(ev_text, encoding="utf-8")
        cl_f.write_text(cl_text, encoding="utf-8")

        results = []
        def grade(level, msg, detail=''):
            results.append({
                'item': msg, 'level': level,
                'pass': level != 'fail', 'detail': str(detail),
            })
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        return [r for r in results if '事实校验' in r['item']]

    def test_evidence_id_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":123,"source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int evidence_id: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "evidence_id" in joined and "类型错误" in joined, joined

    def test_source_url_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":"ev_001","source_url":123,'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int source_url: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "source_url" in joined and "类型错误" in joined, joined

    def test_claim_text_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":123,'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int claim_text: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim_text" in joined and "类型错误" in joined, joined

    def test_claim_type_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":123,"evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int claim_type: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim_type" in joined and "类型错误" in joined, joined

    def test_material_pack_sha_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":123,'
                '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int material_pack_sha256: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "material_pack_sha256" in joined and "类型错误" in joined, joined

    def test_draft_sha_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":123}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int draft_sha256: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "draft_sha256" in joined and "类型错误" in joined, joined

    def test_evidence_ids_entry_int_blocked(self, tmp_path):
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com",'
                '"quote":"data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}]}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":[123]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), f"Should block int evidence_ids entry: {fact}"
        joined = "\n".join(r['detail'] for r in fact)
        assert "evidence_ids" in joined and "类型错误" in joined, joined

    # ── Unreferenced evidence fail-closed (Fix4 round 3) ──
    # evidence-ledger fields must be pre-validated even when no claim
    # references them, otherwise W1b silently passes on bad ledgers.

    def test_unreferenced_evidence_bad_source_url_blocked(self, tmp_path):
        """ev_001: clean & referenced; ev_002: NOT referenced, source_url=123.
        W1b MUST block even though ev_002 is never used by any claim."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"ev_002","source_url":123,'
                '"quote":"bad url data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                ']}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with int source_url: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "source_url" in joined and "类型错误" in joined, joined

    def test_unreferenced_evidence_bad_quote_blocked(self, tmp_path):
        """ev_001: clean & referenced; ev_002: NOT referenced, quote=123."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"ev_002","source_url":"https://ex.com/2",'
                '"quote":123,"key_finding":"real kf",'
                '"canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                ']}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with int quote: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "quote" in joined and "类型错误" in joined, joined

    def test_unreferenced_evidence_bad_key_finding_blocked(self, tmp_path):
        """ev_001: clean & referenced; ev_002: NOT referenced, key_finding=123."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"ev_002","source_url":"https://ex.com/2",'
                '"quote":"real quote","key_finding":123,'
                '"canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                ']}'
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with int key_finding: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "key_finding" in joined and "类型错误" in joined, joined

    # ── Fix1 round 4: null / missing fields on unreferenced evidence ──────

    def test_unreferenced_evidence_source_url_null_blocked(self, tmp_path):
        """ev_001 clean & referenced; ev_002 unreferenced, source_url=null."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"ev_002","source_url":null,'
                '"quote":"some quote","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                "]}"
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with source_url=null: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "source_url" in joined and ("null" in joined or "None" in joined or "类型" in joined), joined

    def test_unreferenced_evidence_source_url_missing_blocked(self, tmp_path):
        """ev_001 clean & referenced; ev_002 unreferenced, no source_url key."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"ev_002",'
                '"quote":"some quote","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                "]}"
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with missing source_url: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "source_url" in joined and ("缺" in joined or "类型" in joined), joined

    def test_unreferenced_evidence_evidence_id_missing_blocked(self, tmp_path):
        """ev_001 clean & referenced; ev_002 unreferenced, no evidence_id key."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"source_url":"https://ex.com/2",'
                '"quote":"some quote","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                "]}"
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with missing evidence_id: {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "evidence_id" in joined, joined

    def test_unreferenced_evidence_evidence_id_empty_blocked(self, tmp_path):
        """ev_001 clean & referenced; ev_002 unreferenced, evidence_id=''."""
        fact = self._fact_check_grade(
            tmp_path,
            ev_text=(
                '{"version":1,"material_pack_sha256":"__MP_SHA__",'
                '"evidence":['
                '{"evidence_id":"ev_001","source_url":"https://ex.com/1",'
                '"quote":"good data","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false},'
                '{"evidence_id":"","source_url":"https://ex.com/2",'
                '"quote":"some quote","canonical_concepts":[],"claim_types":["spec"],'
                '"required":false}'
                "]}"
            ),
            cl_text=(
                '{"version":1,"claims":[{"claim_text":"claim sentence here.",'
                '"claim_type":"general","evidence_ids":["ev_001"]}],'
                '"draft_sha256":"__DR_SHA__"}'
            ),
        )
        assert not all(r['pass'] for r in fact), (
            f"Should block unreferenced evidence with evidence_id='': {fact}"
        )
        joined = "\n".join(r['detail'] for r in fact)
        assert "证据" in joined or "空字符串" in joined or "blocking" in joined, joined


class TestValidateClaimLedgerTypeCheck:
    """Fix2: _validate_claim_ledger_json must type-check before .strip()."""

    def test_claim_text_int_raises_value_error(self):
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        with pytest.raises(ValueError) as exc:
            _validate_claim_ledger_json(
                '{"version":1,"claims":[{"claim_text":123,"claim_type":"general","evidence_ids":["ev_001"]}]}',
                "body sentence.",
            )
        msg = str(exc.value)
        assert "claims[0]" in msg
        assert "claim_text" in msg
        assert "int" in msg

    def test_claim_type_int_raises_value_error(self):
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        with pytest.raises(ValueError) as exc:
            _validate_claim_ledger_json(
                '{"version":1,"claims":[{"claim_text":"body sentence.","claim_type":456,"evidence_ids":["ev_001"]}]}',
                "body sentence.",
            )
        msg = str(exc.value)
        assert "claims[0]" in msg
        assert "claim_type" in msg
        assert "int" in msg

    def test_claim_text_none_raises_value_error(self):
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        with pytest.raises(ValueError) as exc:
            _validate_claim_ledger_json(
                '{"version":1,"claims":[{"claim_text":null,"claim_type":"general","evidence_ids":["ev_001"]}]}',
                "body sentence.",
            )
        msg = str(exc.value)
        assert "claims[0]" in msg
        assert "claim_text" in msg
        assert "NoneType" in msg


class TestEntryPointNumericClaimRejection:
    """Fix3: W0 / W1b revise / W2 revise with numeric claim_text or
    claim_type must fail without changing draft, claim ledger, or state."""

    def _prep_workspace(self, tmp_path, slug):
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        today = datetime.now(UTC).strftime("%Y-%m-%d")

        mp_path = ws / "material-packs" / f"{slug}-{today}.md"
        mp_path.write_text("material pack content for AI.", encoding="utf-8")

        ev_path = ws / "research" / f"evidence-ledger-{slug}.json"
        ev_path.write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(
                b"material pack content for AI."
            ).hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001",
                "source_url": "https://ex.com/a",
                "quote": "data",
                "canonical_concepts": ["test"],
                "claim_types": ["spec"],
                "required": False,
            }],
        }), encoding="utf-8")

        draft_path = ws / "drafts" / f"{slug}-{today}.md"
        draft_body = (
            "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
            "Summary: S.\nTags: t\n"
            "SEO Title: T SEO Title Long Enough For Validation\n"
            "SEO Description: " + "A" * 152 + "\n"
            "SEO Keywords: t\n"
            "---\n\n"
            "# T\n\n"
            "This is a body sentence.\n\n"
        )
        draft_path.write_text(draft_body, encoding="utf-8")

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        cl_path.write_text(json.dumps({
            "version": 1,
            "claims": [{
                "claim_text": "This is a body sentence.",
                "claim_type": "general",
                "evidence_ids": ["ev_001"],
            }],
            "draft_sha256": dr_sha,
        }), encoding="utf-8")

        lw.save_report(
            ws, "post-process", slug,
            "# POST-PROCESS\n## 质量评分\n- 总分: 55 → ❌\n",
        )
        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": False, "applied": False,
             "precheck_passed": True, "precheck_tier": "Cluster Content"},
        )

        return ws, slug, draft_path, cl_path

    def test_w0_with_numeric_claim_text_fails_no_file_change(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path = self._prep_workspace(tmp_path, slug="numeric-w0")
        draft_snapshot = draft_path.read_bytes()
        cl_snapshot = cl_path.read_bytes()
        state_path = ws / "reports" / f"w2-state-{slug}.json"
        state_bytes_snapshot = state_path.read_bytes()

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_draft":
                return (
                    "---\nTitle: T\nSlug: numeric-w0\nAuthor: T\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: t\n"
                    "---\n\n"
                    "# T\n\n"
                    "claim body sentence.\n\n"
                    "===CLAIM_LEDGER===\n"
                    '{"version":1,"claims":[{"claim_text":999,'
                    '"claim_type":"spec","evidence_ids":["ev_001"]}]}\n'
                )
            return ""

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("numeric w0", "Test", ws))
        assert result.get("success") is not True

        # W0 must not delete or alter existing artifacts on failure.
        assert draft_path.read_bytes() == draft_snapshot, "old draft was modified"
        assert cl_path.read_bytes() == cl_snapshot, "old claim ledger was modified"
        assert state_path.read_bytes() == state_bytes_snapshot, "old w2 state was modified"

    def test_w0_with_numeric_claim_type_fails_no_file_change(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path = self._prep_workspace(tmp_path, slug="numeric-w0-ctype")
        draft_snapshot = draft_path.read_bytes()
        cl_snapshot = cl_path.read_bytes()
        state_path = ws / "reports" / f"w2-state-{slug}.json"
        state_bytes_snapshot = state_path.read_bytes()

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_draft":
                return (
                    "---\nTitle: T\nSlug: numeric-w0-ctype\nAuthor: T\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: t\n"
                    "---\n\n"
                    "# T\n\n"
                    "claim body sentence.\n\n"
                    "===CLAIM_LEDGER===\n"
                    '{"version":1,"claims":[{"claim_text":"claim body sentence.",'
                    '"claim_type":999,"evidence_ids":["ev_001"]}]}\n'
                )
            return ""

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("numeric w0 ctype", "Test", ws))
        assert result.get("success") is not True

        # W0 must not delete or alter existing artifacts on failure.
        assert draft_path.read_bytes() == draft_snapshot, "old draft was modified"
        assert cl_path.read_bytes() == cl_snapshot, "old claim ledger was modified"
        assert state_path.read_bytes() == state_bytes_snapshot, "old w2 state was modified"

    def test_w1b_revise_with_numeric_claim_type_fails_no_file_change(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path = self._prep_workspace(tmp_path, slug="numeric-w1b")
        cl_snapshot = cl_path.read_text(encoding="utf-8") if cl_path.exists() else None
        draft_snapshot = draft_path.read_text(encoding="utf-8")

        # stage_w1b_revise needs a pre-check report.
        lw.save_report(
            ws, "pre-check", slug,
            "# WRITE 预检报告 — " + slug + ".md\n"
            "| 字数 | ❌ | 20 (下限500) |\n",
        )

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return (
                    "---\nTitle: T\nSlug: numeric-w1b\nAuthor: T\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: t\n"
                    "---\n\n"
                    "# T\n\n"
                    "revised body sentence.\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return (
                    '{"version":1,"claims":[{"sentence_id":"S001",'
                    '"claim_type":456,"evidence_ids":["ev_001"]}]}\n'
                )
            raise AssertionError(f"unexpected purpose: {purpose}")

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w1b_revise("numeric w1b", "Cluster Content", ws))
        assert result.get("success") is not True
        assert "claim_type" in result.get("error", "") and "类型错误" in result.get("error", ""), (
            f"Expected type error in response: {result.get('error')}"
        )

        assert draft_path.read_text(encoding="utf-8") == draft_snapshot
        new_cl = cl_path.read_text(encoding="utf-8") if cl_path.exists() else None
        assert new_cl == cl_snapshot, "claim ledger changed after failed W1b revise"

    def test_w2_revise_with_numeric_claim_text_fails_no_file_change(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws, slug, draft_path, cl_path = self._prep_workspace(tmp_path, slug="numeric-w2")

        # Add evidence ledger with a matching evidence_id
        ev_path = ws / "research" / f"evidence-ledger-{slug}.json"
        ev_path.write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(
                b"material pack content for AI."
            ).hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001",
                "source_url": "https://ex.com/a",
                "quote": "data",
                "canonical_concepts": ["test"],
                "claim_types": ["spec"],
                "required": False,
            }],
        }), encoding="utf-8")

        cl_snapshot = cl_path.read_text(encoding="utf-8") if cl_path.exists() else None
        draft_snapshot = draft_path.read_text(encoding="utf-8")

        async def fake_ai(purpose, *a, **kw):
            return (
                "---\nTitle: T\nSlug: numeric-w2\nAuthor: T\n"
                "Summary: S.\nTags: t\n"
                "SEO Title: T SEO Title Long Enough\n"
                "SEO Description: " + "B" * 152 + "\n"
                "SEO Keywords: t\n"
                "---\n\n"
                "# T\n\n"
                "w2 revised body sentence.\n\n"
                "===CLAIM_LEDGER===\n"
                '{"version":1,"claims":[{"claim_text":789,"claim_type":"spec","evidence_ids":["ev_001"]}]}\n'
            )

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w2_revise("numeric w2", ws))
        assert result.get("success") is not True

        assert draft_path.read_text(encoding="utf-8") == draft_snapshot
        new_cl = cl_path.read_text(encoding="utf-8") if cl_path.exists() else None
        assert new_cl == cl_snapshot, "claim ledger changed after failed W2 revise"


class TestW2PostPassRejection:
    """Fix5: After gate_passed or applied, W2 revise must be rejected."""

    def test_w2_revise_blocked_when_gate_passed(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        slug = "postpass-topic"
        draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        draft.write_text("---\nTitle: T\n---\n# T\nBody.", encoding="utf-8")

        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": True, "applied": False,
             "precheck_passed": True},
        )
        lw.save_report(
            ws, "post-process", slug,
            "# POST-PROCESS\n## 质量评分\n- 总分: 88 → ✅\n",
        )

        async def fake_ai(*a, **kw):
            return "Title: Revised\n\nrevised body"

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        result = asyncio.run(lw.stage_w2_revise("postpass topic", ws))
        assert result["success"] is False
        assert "已通过预检" in result["error"]

    def test_w2_revise_blocked_when_applied(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        slug = "applied-topic"
        draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        draft.write_text("---\nTitle: T\n---\n# T\nBody.", encoding="utf-8")

        lw.save_w2_state(
            ws, slug,
            {"rounds": 0, "gate_passed": False, "applied": True,
             "precheck_passed": True},
        )
        lw.save_report(
            ws, "post-process", slug,
            "# POST-PROCESS\n## 质量评分\n- 总分: 88 → ✅\n",
        )

        async def fake_ai(*a, **kw):
            return "Title: Revised\n\nrevised body"

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        result = asyncio.run(lw.stage_w2_revise("applied topic", ws))
        assert result["success"] is False
        assert "已发布" in result["error"]


class TestSentenceIdBinding:
    """sentence_id-based claim_text binding: server authors claim_text from
    a stable ID selected by the model, never trusting model-supplied text."""

    @staticmethod
    def _draft_md() -> str:
        return (
            "---\nTitle: T\nSlug: s\nAuthor: A\n---\n\n"
            "# Title\n\n"
            "The 5mW green laser has a wavelength of 532nm. "
            "It is widely used for presentations.\n\n"
            "OSHA requires a Class 3B laser hazard analysis.\n"
        )

    def test_extract_draft_sentences_stable_ids(self):
        from seo_ops.services.legacy_workflow import _extract_draft_sentences

        sentences = _extract_draft_sentences(self._draft_md())
        # "#" without trailing punctuation is kept because _normalize_claim
        # strips only the suffix punctuation `.,;:!?`.  The title is excluded
        # by the paragraph tokenizer because it's a single-line paragraph
        # with no terminal punctuation, so # Title becomes S001.
        assert [s["sentence_id"] for s in sentences] == ["S001", "S002", "S003", "S004"]
        assert sentences[0]["text"] == "# Title"
        assert sentences[1]["text"].startswith("The 5mW green laser")
        assert sentences[2]["text"].startswith("It is widely used")
        assert sentences[3]["text"].startswith("OSHA")
        # norm is the _normalize_claim of text
        from seo_ops.services.legacy_workflow import _normalize_claim

        for s in sentences:
            assert s["norm"] == _normalize_claim(s["text"])

    def test_validate_with_sentence_id_server_fills_claim_text(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {
            "version": 1,
            "claims": [
                {"sentence_id": "S002", "claim_type": "technical_specification",
                 "evidence_ids": ["ev_001"]},
                {"sentence_id": "S004", "claim_type": "regulatory",
                 "evidence_ids": ["ev_007"]},
            ],
        }
        canonical = _validate_claim_ledger_with_sentence_ids(data, sentences)
        assert canonical["version"] == 1
        assert canonical["claims"][0]["sentence_id"] == "S002"
        assert canonical["claims"][0]["claim_text"] == sentences[1]["text"]
        assert canonical["claims"][1]["sentence_id"] == "S004"
        assert canonical["claims"][1]["claim_text"] == sentences[3]["text"]

    def test_model_claim_text_is_ignored_when_sentence_id_present(self):
        """Even if the model writes a wrong claim_text, server uses the
        sentence referenced by the sentence_id."""
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {
            "version": 1,
            "claims": [
                {"sentence_id": "S002", "claim_text": "WRONG PARAPHRASE BY MODEL",
                 "claim_type": "spec", "evidence_ids": ["ev_001"]},
            ],
        }
        canonical = _validate_claim_ledger_with_sentence_ids(data, sentences)
        assert canonical["claims"][0]["claim_text"] == sentences[1]["text"]
        # The wrong model-authored claim_text must not appear anywhere in
        # the canonical output.
        assert "WRONG PARAPHRASE BY MODEL" not in json.dumps(canonical)

    def test_top_level_unknown_field_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [], "extra_field": "should_fail"}
        with pytest.raises(ValueError, match="unknown field"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_claim_unknown_field_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "S002", "claim_type": "spec",
             "evidence_ids": ["ev_001"], "invalid_key": "nope"},
        ]}
        with pytest.raises(ValueError, match="unknown field"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_sentence_id_with_leading_space_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        for bad_id in (" S002", "S002 ", " S002 "):
            data = {"version": 1, "claims": [
                {"sentence_id": bad_id, "claim_type": "spec",
                 "evidence_ids": ["ev_001"]},
            ]}
            with pytest.raises(ValueError, match="sentence_id"):
                _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_unknown_sentence_id_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "S999", "claim_type": "spec",
             "evidence_ids": ["ev_001"]}]}
        with pytest.raises(ValueError, match="sentence_id"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_empty_sentence_id_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "", "claim_type": "spec",
             "evidence_ids": ["ev_001"]}]}
        with pytest.raises(ValueError, match="sentence_id"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_int_sentence_id_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": 123, "claim_type": "spec",
             "evidence_ids": ["ev_001"]}]}
        with pytest.raises(ValueError, match="sentence_id"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_missing_claim_type_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "S001", "evidence_ids": ["ev_001"]}]}
        with pytest.raises(ValueError, match="claim_type"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_empty_evidence_ids_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "S001", "claim_type": "spec", "evidence_ids": []}]}
        with pytest.raises(ValueError, match="evidence_ids"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_same_sentence_id_in_multiple_claims_allowed(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": [
            {"sentence_id": "S001", "claim_type": "spec",
             "evidence_ids": ["ev_001"]},
            {"sentence_id": "S001", "claim_type": "general",
             "evidence_ids": ["ev_002"]},
        ]}
        canonical = _validate_claim_ledger_with_sentence_ids(data, sentences)
        assert len(canonical["claims"]) == 2
        assert canonical["claims"][0]["claim_text"] == sentences[0]["text"]
        assert canonical["claims"][1]["claim_text"] == sentences[0]["text"]
        assert canonical["claims"][0]["claim_type"] == "spec"
        assert canonical["claims"][1]["claim_type"] == "general"

    def test_claims_but_no_extractable_sentences_rejected(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        # Empty body produces no sentences.
        sentences = _extract_draft_sentences("")
        assert sentences == []
        data = {"version": 1, "claims": [
            {"sentence_id": "S001", "claim_type": "spec",
             "evidence_ids": ["ev_001"]}]}
        with pytest.raises(ValueError, match="no selectable sentences"):
            _validate_claim_ledger_with_sentence_ids(data, sentences)

    def test_empty_claims_allowed(self):
        from seo_ops.services.legacy_workflow import (
            _extract_draft_sentences,
            _validate_claim_ledger_with_sentence_ids,
        )

        sentences = _extract_draft_sentences(self._draft_md())
        data = {"version": 1, "claims": []}
        canonical = _validate_claim_ledger_with_sentence_ids(data, sentences)
        assert canonical["claims"] == []

    def test_legacy_ledger_without_sentence_id_still_validates(self):
        """Historical claim-ledger JSON (no sentence_id) must still pass
        _validate_claim_ledger_json so existing downstream stages (read by
        write_pre_check.py:_run_fact_check) keep working."""
        from seo_ops.services.legacy_workflow import _validate_claim_ledger_json

        cl_json = json.dumps({
            "version": 1,
            "claims": [{
                "claim_text": "The 5mW green laser has a wavelength of 532nm.",
                "claim_type": "technical_specification",
                "evidence_ids": ["ev_001"],
            }],
        })
        result = _validate_claim_ledger_json(cl_json, self._draft_md())
        assert result["version"] == 1
        assert len(result["claims"]) == 1
        assert result["claims"][0].get("sentence_id") is None


class TestSentenceIdEntryPoints:
    """W0, W1b revise, W2 revise all use the shared sentence_id pipeline."""

    @staticmethod
    def _prep_w0_workspace(tmp_path, slug="sid-w0"):
        from datetime import UTC, datetime

        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        mp_path = ws / "material-packs" / f"{slug}-{today}.md"
        mp_path.write_text("mp body", encoding="utf-8")
        # Evidence ledger so _write_context_contracts has cards to emit.
        ev_path = ws / "research" / f"evidence-ledger-{slug}.json"
        ev_path.write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(b"mp body").hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001",
                "source_url": "https://ex.com/a",
                "quote": "data",
                "canonical_concepts": ["laser"],
                "claim_types": ["spec"],
                "required": False,
            }],
        }), encoding="utf-8")
        # Brief so _write_context_contracts has something to project.
        (ws / "research" / f"brief-{slug}-{today}.md").write_text(
            "# Brief\n\nH2: Safety\nH2: Usage\n", encoding="utf-8")
        return ws

    def test_w0_threads_sentence_id_through_to_canonical_ledger(self, tmp_path, monkeypatch):
        """W0 success → claim ledger on disk has server-filled claim_text
        AND the sentence_id used by the model is persisted for audit."""
        from seo_ops.services import legacy_workflow as lw

        ws = self._prep_w0_workspace(tmp_path, slug="sid-w0")
        slug = "sid-w0"
        draft_md_body = "The 5mW green laser has a wavelength of 532nm."

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_body":
                return (
                    "---\nTitle: T\nSlug: " + slug + "\nAuthor: T\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "B" * 152 + "\n"
                    "SEO Keywords: t\n"
                    "---\n\n# T\n\n" + draft_md_body + "\n"
                )
            if purpose == "legacy_write_claim_ledger":
                # Model deliberately returns nonsense claim_text; server must
                # ignore it and use S002 (the first factual sentence after
                # the H1 "# T") to pull the real sentence.
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S002",
                        "claim_text": "WRONG",
                        "claim_type": "spec",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("sid w0", "Test", ws))
        assert result.get("success") is True, f"W0 should succeed: {result}"

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        assert cl_path.exists()
        cl = json.loads(cl_path.read_text(encoding="utf-8"))
        assert cl["version"] == 1
        assert len(cl["claims"]) == 1
        # Persisted sentence_id for audit.
        assert cl["claims"][0]["sentence_id"] == "S002"
        # Server-filled claim_text is the real draft sentence.
        assert cl["claims"][0]["claim_text"] == draft_md_body
        # Model's WRONG text must not be in the persisted ledger.
        assert "WRONG" not in json.dumps(cl)

    def test_w0_rejects_unknown_sentence_id_without_writing(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        ws = self._prep_w0_workspace(tmp_path, slug="sid-w0-bad")
        slug = "sid-w0-bad"

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_body":
                return (
                    "---\nTitle: T\nSlug: " + slug + "\n---\n\n# T\n\nbody sentence.\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S999", "claim_type": "spec",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("sid w0 bad", "Test", ws))
        assert result.get("success") is False
        assert "sentence_id" in result.get("error", "")
        # No draft, no ledger, no state must be written.
        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        drafts = list(ws.glob("drafts/*.md"))
        assert not cl_path.exists()
        assert drafts == []

    def test_w1b_revise_uses_sentence_id_pipeline(self, tmp_path, monkeypatch):
        """When W1b revise is invoked, the AI is asked for sentence_ids;
        server fills claim_text from the new draft body."""
        from seo_ops.services import legacy_workflow as lw

        slug = "sid-w1b-topic"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        today_draft = (
            "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
            "Summary: S.\nTags: t\n"
            "SEO Title: T SEO Title Long Enough\n"
            "SEO Description: " + "A" * 152 + "\n"
            "SEO Keywords: t\n---\n\n# T\n\nOld sentence.\n"
        )
        (ws / "drafts" / f"{slug}-2026-07-29.md").write_text(today_draft, encoding="utf-8")

        # Pre-check report so stage_w1b_revise has a failure list to read.
        lw.save_report(
            ws, "pre-check", slug,
            "# WRITE 预检报告 — " + slug + ".md\n| 字数 | ❌ | 20 (下限500) |\n",
        )
        # Material pack + evidence ledger so _write_context_contracts can build cards.
        (ws / "material-packs" / f"{slug}-2026-07-29.md").write_text("mp", encoding="utf-8")
        (ws / "research" / f"evidence-ledger-{slug}.json").write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(b"mp").hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001",
                "source_url": "https://ex.com/a",
                "quote": "data",
                "canonical_concepts": ["x"], "claim_types": ["y"],
                "required": False,
            }],
        }), encoding="utf-8")
        (ws / "research" / f"brief-{slug}-2026-07-29.md").write_text(
            "# Brief\nH2: Revised H2\n", encoding="utf-8")

        new_body = "Revised sentence with a fact."

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return (
                    "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough\n"
                    "SEO Description: " + "A" * 152 + "\n"
                    "SEO Keywords: t\n---\n\n# T\n\n" + new_body + "\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S002",
                        "claim_text": "BREAKING THE NEW CONTRACT",
                        "claim_type": "general",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        def fake_precheck_data(
            draft_path,
            tier,
            workspace,
            checked_slug,
            *,
            claim_path=None,
        ):
            # This test isolates the sentence_id pipeline. The full candidate
            # gate is covered separately by TestW1bCandidateGate.
            assert checked_slug == slug
            assert Path(draft_path).exists()
            if claim_path is not None:
                assert Path(claim_path).exists()
            return {
                "fail_count": 0,
                "checks": [{
                    "item": "sentence_id fixture",
                    "pass": True,
                    "level": "ok",
                    "detail": "candidate accepted by isolated fixture",
                }],
            }

        async def fake_run(self, script, args):
            return (json.dumps({
                "word_count": 500, "warn_count": 0,
                "checks": [{"item": "字数", "pass": True, "level": "ok", "detail": "500"}],
            }), "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw, "_w1b_precheck_data", fake_precheck_data)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w1b_revise("sid w1b topic", "", ws))
        assert result.get("gate_passed") is True, f"W1b revise should pass: {result}"

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        cl = json.loads(cl_path.read_text(encoding="utf-8"))
        assert cl["claims"][0]["claim_text"] == new_body
        assert "BREAKING THE NEW CONTRACT" not in json.dumps(cl)

    def test_w2_revise_uses_sentence_id_pipeline(self, tmp_path, monkeypatch):
        """W2 revise depends on draft + state + post-process report;
        verify it goes through the shared sentence_id pipeline too."""
        from seo_ops.services import legacy_workflow as lw

        slug = "sid-w2-topic"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        draft_body = (
            "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
            "Summary: S.\nTags: t\n"
            "SEO Title: T SEO Title Long Enough For Validation\n"
            "SEO Description: " + "A" * 152 + "\n"
            "SEO Keywords: t\n---\n\n# T\n\nOld sentence.\n"
        )
        (ws / "drafts" / f"{slug}-2026-07-29.md").write_text(draft_body, encoding="utf-8")

        (ws / "material-packs" / f"{slug}-2026-07-29.md").write_text("mp", encoding="utf-8")
        (ws / "research" / f"evidence-ledger-{slug}.json").write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(b"mp").hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001",
                "source_url": "https://ex.com/a",
                "quote": "data",
                "canonical_concepts": ["x"], "claim_types": ["y"],
                "required": False,
            }],
        }), encoding="utf-8")
        (ws / "research" / f"brief-{slug}-2026-07-29.md").write_text(
            "# Brief\nH2: Revised H2\n", encoding="utf-8")

        lw.save_w2_state(ws, slug, {
            "rounds": 0, "gate_passed": False, "applied": False,
            "precheck_passed": True, "precheck_tier": "Cluster Content",
            "precheck_draft_sha256": hashlib.sha256(draft_body.encode()).hexdigest(),
        })
        lw.save_report(ws, "post-process", slug, _POST_PROCESS_REPORT_60)

        new_body_sentence = "W2 revised content covers a fact."

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_revise_body":
                return (
                    "---\nTitle: T\nSlug: " + slug + "\nAuthor: Test\n"
                    "Summary: S.\nTags: t\n"
                    "SEO Title: T SEO Title Long Enough For Validation\n"
                    "SEO Description: " + "A" * 152 + "\n"
                    "SEO Keywords: t\n---\n\n# T\n\n" + new_body_sentence + "\n"
                )
            if purpose == "legacy_write_claim_ledger":
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S002",
                        "claim_text": "ABUSE",
                        "claim_type": "general",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        async def fake_run(self, script, args):
            if script == "write_pre_check.py":
                return (json.dumps({
                    "word_count": 500, "warn_count": 0,
                    "checks": [{"item": "字数", "pass": True, "level": "ok", "detail": "500"}],
                }), "", 0)
            return ("## 质量评分\n- 总分: 88.0 → ✅ 通过\n\n"
                    "## 🚦 总门控\n- ✅ 通过，可进入段3 register。", "", 0)

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w2_revise("sid w2 topic", ws))
        assert result.get("gate_passed") is True, f"W2 revise should pass: {result}"

        cl_path = ws / "research" / f"claim-ledger-{slug}.json"
        cl = json.loads(cl_path.read_text(encoding="utf-8"))
        assert cl["claims"][0]["claim_text"] == new_body_sentence
        assert "ABUSE" not in json.dumps(cl)


class TestSentenceIdAtomicWrite:
    """When the atomic write fails after sentence_id validation, the old
    draft/ledger must survive byte-for-byte."""

    def test_w0_second_replace_failure_with_sentence_ids(
        self, tmp_path, monkeypatch
    ):
        import os as _os

        from seo_ops.services import legacy_workflow as lw

        slug = "sid-atomic"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        old_draft = ws / "drafts" / f"{slug}-2026-01-01.md"
        old_cl = ws / "research" / f"claim-ledger-{slug}.json"
        old_draft.write_text("OLD DRAFT", encoding="utf-8")
        old_cl.write_text('{"version":1,"claims":[]}', encoding="utf-8")

        (ws / "material-packs" / f"{slug}-2026-01-01.md").write_text("mp", encoding="utf-8")
        (ws / "research" / f"evidence-ledger-{slug}.json").write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(b"mp").hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001", "source_url": "https://ex.com/a",
                "quote": "data", "canonical_concepts": ["x"],
                "claim_types": ["y"], "required": False,
            }],
        }), encoding="utf-8")
        (ws / "research" / f"brief-{slug}-2026-01-01.md").write_text(
            "# Brief\nH2: Just One\n", encoding="utf-8")

        snapshot_draft = old_draft.read_bytes()
        snapshot_cl = old_cl.read_bytes()

        new_draft_body = "New body with a fact to claim."

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_body":
                return f"# Title\n\n{new_draft_body}\n"
            if purpose == "legacy_write_claim_ledger":
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S001", "claim_type": "spec",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        original_replace = _os.replace
        counter = [0]

        def bad_replace(src, dst):
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("simulated second replace")
            return original_replace(src, dst)

        monkeypatch.setattr(_os, "replace", bad_replace)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("sid atomic", "T", ws))
        assert result.get("success") is False
        assert "simulated second replace" in result.get("error", "")
        # Old draft/ledger must be byte-for-byte the same.
        assert old_draft.read_bytes() == snapshot_draft
        assert old_cl.read_bytes() == snapshot_cl

    def test_w0_replace_failure_preserves_same_date_old_draft(
        self, tmp_path, monkeypatch
    ):
        """When the old draft is on the SAME date as the target write path,
        the second replace failure must still leave it byte-for-byte intact
        (the rollback restores the content that was snapshot'd)."""
        import os as _os
        from datetime import UTC, datetime

        from seo_ops.services import legacy_workflow as lw

        slug = "sid-atomic-samedate"
        ws = tmp_path / "ws"
        (ws / "drafts").mkdir(parents=True)
        (ws / "material-packs").mkdir(parents=True)
        (ws / "research").mkdir(parents=True)
        (ws / "context").mkdir(parents=True)
        (ws / "reports").mkdir(parents=True)

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        # Old draft uses the SAME dated path that _write_ahead_draft_and_ledger
        # will target (draft_path = drafts/{slug}-{today}.md).
        old_draft = ws / "drafts" / f"{slug}-{today}.md"
        old_cl = ws / "research" / f"claim-ledger-{slug}.json"
        old_draft.write_text("OLD DRAFT SAME DATE", encoding="utf-8")
        old_cl.write_text('{"version":1,"claims":[]}', encoding="utf-8")

        (ws / "material-packs" / f"{slug}-{today}.md").write_text("mp", encoding="utf-8")
        (ws / "research" / f"evidence-ledger-{slug}.json").write_text(json.dumps({
            "version": 1,
            "material_pack_sha256": hashlib.sha256(b"mp").hexdigest(),
            "evidence": [{
                "evidence_id": "ev_001", "source_url": "https://ex.com/a",
                "quote": "data", "canonical_concepts": ["x"],
                "claim_types": ["y"], "required": False,
            }],
        }), encoding="utf-8")
        (ws / "research" / f"brief-{slug}-{today}.md").write_text(
            "# Brief\nH2: One\n", encoding="utf-8")

        snapshot_draft = old_draft.read_bytes()
        snapshot_cl = old_cl.read_bytes()

        async def fake_ai(purpose, *a, **kw):
            if purpose == "legacy_write_body":
                return "# Title\n\nNew sentence for replacement.\n"
            if purpose == "legacy_write_claim_ledger":
                return json.dumps({
                    "version": 1,
                    "claims": [{
                        "sentence_id": "S002", "claim_type": "spec",
                        "evidence_ids": ["ev_001"],
                    }],
                })
            raise AssertionError(purpose)

        async def fake_run(self, script, args):
            return (json.dumps({"word_count": 500, "warn_count": 0, "checks": []}), "", 0)

        original_replace = _os.replace
        counter = [0]

        def bad_replace(src, dst):
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("simulated second replace")
            return original_replace(src, dst)

        monkeypatch.setattr(_os, "replace", bad_replace)
        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)

        result = asyncio.run(lw.stage_w0_validate_and_draft("sid atomic samedate", "T", ws))
        assert result.get("success") is False
        assert "simulated second replace" in result.get("error", "")
        assert old_draft.read_bytes() == snapshot_draft
        assert old_cl.read_bytes() == snapshot_cl


class TestUnifiedSentenceMatching:
    """_claim_in_draft and _extract_factual_sentences must use the same
    paragraph-first, frontmatter-aware split as the server-side
    _extract_draft_sentences / _validate_claim_ledger_json."""

    def test_rich_markdown_claim_text_matches(self):
        """Markdown formatting (bold, links, blockquotes) must not cause
        false negatives in _claim_in_draft."""
        from data_sources.modules.write_pre_check import _claim_in_draft, _extract_factual_sentences

        draft = (
            "---\nTitle: T\nSlug: s\n---\n\n"
            "## Safety\n\n"
            "- **Class 2 is safe:** The blink reflex prevents eye injury.\n"
            "- [OSHA states](http://osha.gov) that Class 3R lasers require controls.\n"
            "  This is a continuation of the same list item.\n"
        )
        # The server-filled claim_text is the exact extracted sentence.
        # Use _extract_draft_sentences from legacy_workflow.py to get the
        # canonical sentences, then verify _claim_in_draft finds each one.
        from seo_ops.services.legacy_workflow import _extract_draft_sentences
        sents = _extract_draft_sentences(draft)
        assert len(sents) >= 3, f"expected >=3 sentences, got {len(sents)}"
        for s in sents:
            assert _claim_in_draft(s["text"], draft), (
                f"extracted sentence not found by _claim_in_draft: {s['sentence_id']} '{s['text'][:60]}'"
            )
        # Factual extraction must not pick up frontmatter lines or
        # markdown link syntax as separate sentences.
        extracted = _extract_factual_sentences(draft)
        texts = {e["sentence"] for e in extracted}
        for bad in ("---", "Title: T", "Slug: s", "## Safety"):
            assert not any(bad in t for t in texts), f"frontmatter leaked: {bad}"

    def test_fake_sentence_id_blocked_even_with_correct_claim_text(self, tmp_path):
        """sentence_id=S999 (unknown ID) with correct draft claim_text
        must still be blocked — claim_text alone is insufficient."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_text = "The 5mW green laser has a wavelength of 532nm.\n"
        dr_f.write_text(dr_text, encoding="utf-8")
        draft_body = dr_text.strip()
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com/a",'
            '"quote":"data","canonical_concepts":["x"],"claim_types":["y"],"required":false}]}',
            encoding="utf-8",
        )
        # sentence_id=S999 does NOT exist in the draft sentence table
        # (which only has S001 for the one sentence).
        cl_f.write_text(
            '{"version":1,"claims":[{"sentence_id":"S999",'
            '"claim_text":"The 5mW green laser has a wavelength of 532nm.",'
            '"claim_type":"spec","evidence_ids":["ev_001"]}],'
            '"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        joined = "\n".join(r['detail'] for r in fact)
        assert "在当前草稿中不存在" in joined, (
            f"Fake S999 sentence_id must be blocked: {joined[:300]}"
        )

    def test_markdown_link_sentence_matches(self):
        """A claim_text that includes a Markdown link must be found by
        _claim_in_draft when the draft contains the identical link."""
        from data_sources.modules.write_pre_check import _claim_in_draft

        draft = (
            "---\nTitle: T\n---\n\n"
            "[OSHA states](http://osha.gov) that Class 3R lasers require "
            "controls.\n"
        )
        ct = "[OSHA states](http://osha.gov) that Class 3R lasers require controls."
        assert _claim_in_draft(ct, draft)

    def test_tampered_claim_text_blocked_by_fact_check(self, tmp_path):
        """sentence_id correct but claim_text is NOT the server-filled
        canonical sentence → _run_fact_check must block."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_text = "The 5mW green laser has a wavelength of 532nm.\n"
        dr_f.write_text(dr_text, encoding="utf-8")
        # _run_fact_check computes draft_body = draft_text.split('===CLAIM_LEDGER===')[0].strip()
        draft_body = dr_text.strip()
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com/a",'
            '"quote":"data","canonical_concepts":["x"],"claim_types":["y"],"required":false}]}',
            encoding="utf-8",
        )
        # claim ledger with sentence_id BUT tampered claim_text
        cl_f.write_text(
            '{"version":1,"claims":[{"sentence_id":"S001",'
            '"claim_text":"THE MODEL TAMPERED WITH THIS TEXT",'
            '"claim_type":"spec","evidence_ids":["ev_001"]}],'
            '"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        joined = "\n".join(r['detail'] for r in fact)
        assert any(
            "claim_text 与草稿原句不一致" in x or "claim_text 不在草稿正文中" in x
            for x in [r['detail'] for r in fact]
        ), (
            f"Tampered claim_text must be blocked: {joined[:300]}"
        )

    def test_draft_changed_after_ledger_blocks(self, tmp_path):
        """If the draft on disk has a different sha256 than the ledger,
        _run_fact_check must block at the SHA check (it never reaches
        _claim_in_draft)."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_f.write_text("Draft text, different from ledger sha.\n", encoding="utf-8")
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[]}',
            encoding="utf-8",
        )
        cl_f.write_text(
            '{"version":1,"claims":[],"draft_sha256":"0000000000000000000000000000000000000000"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        joined = "\n".join(r['detail'] for r in fact)
        assert "draft_sha256 不匹配" in joined, (
            f"SHA mismatch must be caught: {joined[:200]}"
        )

    def test_legacy_ledger_no_sentence_id_still_works(self, tmp_path):
        """A ledger without sentence_id must still pass _claim_in_draft
        if the claim_text matches a draft sentence exactly."""
        from data_sources.modules.write_pre_check import _run_fact_check

        ev_f = tmp_path / "ev.json"
        cl_f = tmp_path / "cl.json"
        mp_f = tmp_path / "mp.md"
        dr_f = tmp_path / "draft.md"
        mp_f.write_text("mp", encoding="utf-8")
        mp_sha = hashlib.sha256(b"mp").hexdigest()
        dr_text = "The 5mW green laser has a wavelength of 532nm.\n"
        dr_f.write_text(dr_text, encoding="utf-8")
        draft_body = dr_text.strip()
        dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
        ev_f.write_text(
            '{"version":1,"material_pack_sha256":"' + mp_sha + '",'
            '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com/a",'
            '"quote":"data","canonical_concepts":["x"],"claim_types":["y"],"required":false}]}',
            encoding="utf-8",
        )
        # No sentence_id, old-style claim_text
        cl_f.write_text(
            '{"version":1,"claims":[{"claim_text":"The 5mW green laser has a wavelength of 532nm.",'
            '"claim_type":"spec","evidence_ids":["ev_001"]}],'
            '"draft_sha256":"' + dr_sha + '"}',
            encoding="utf-8",
        )
        results = []
        def grade(level, msg, detail=''):
            results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
        _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
        fact = [r for r in results if '事实校验' in r['item']]
        assert all(r['pass'] for r in fact), (
            f"Legacy ledger should pass: {[r['detail'][:80] for r in fact]}"
        )


_POST_PROCESS_REPORT_60 = """# POST-PROCESS 报告

## 质量评分
- 总分: 60.00 → ❌ 未达标

## 🚦 总门控
- ❌ 需要修订

## 失败项目
- 字数不足
"""
