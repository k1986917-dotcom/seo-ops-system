import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock


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

    def test_action_attempt_workspaces_are_isolated(self, tmp_path):
        from seo_ops.services.legacy_workflow import (
            current_legacy_run,
            start_legacy_run,
        )

        _write(tmp_path / "context" / "brand-voice.md", "shared context")
        _write(tmp_path / "published" / "published-index.json", "[]")
        _write(tmp_path / "products" / "live_products_report.md", "# products")

        first = start_legacy_run(tmp_path, action_id=7, topic="same topic")
        other = start_legacy_run(tmp_path, action_id=8, topic="same topic")
        _write(first / "research" / "research-data-same-topic-2026-01-01.md", "first")

        assert first != other
        assert (first / "context" / "brand-voice.md").read_text() == "shared context"
        assert not list((other / "research").glob("research-data-*.md"))
        assert current_legacy_run(tmp_path, 7, "same topic") == first
        assert current_legacy_run(tmp_path, 8, "same topic") == other

        retry = start_legacy_run(tmp_path, action_id=7, topic="same topic")
        assert retry != first
        assert current_legacy_run(tmp_path, 7, "same topic") == retry
        assert (first / "research" / "research-data-same-topic-2026-01-01.md").exists()
        assert not list((retry / "research").glob("research-data-*.md"))

    def test_current_run_rejects_a_different_topic(self, tmp_path):
        from seo_ops.services.legacy_workflow import (
            current_legacy_run,
            start_legacy_run,
        )

        start_legacy_run(tmp_path, action_id=7, topic="first topic")

        assert current_legacy_run(tmp_path, 7, "different topic") is None

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


class TestRevisionLoop:
    def _prepare(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        draft = _write(
            tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T\n\nbody"
        )
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        lw.save_w2_state(tmp_path, "test-topic", _state_for(draft))

    def test_revision_refused_after_cap(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        lw.save_w2_state(
            tmp_path,
            "test-topic",
            _state_for(draft, rounds=lw.MAX_REVISION_ROUNDS),
        )
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)

        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))
        assert result["success"] is False
        assert "2 轮" in result["error"]
        assert result["rounds_left"] == 0

    def test_revision_requires_a_report(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        result = asyncio.run(lw.stage_w2_revise("test topic", tmp_path))
        assert result["success"] is False
        assert "后处理报告" in result["error"]

    def test_revision_backs_up_and_reruns(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)

        async def fake_ai(*args, **kwargs):
            return "Title: T\n\nrevised body"

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
        draft = tmp_path / "drafts" / "test-topic-2026-01-01.md"
        assert "revised body" in draft.read_text(encoding="utf-8")
        backup = tmp_path / "drafts" / "test-topic-2026-01-01.rev1.md"
        assert backup.exists() and "body" in backup.read_text(encoding="utf-8")
        assert lw.load_w2_state(tmp_path, "test-topic")["rounds"] == 1

    def test_revision_stops_when_new_draft_fails_precheck(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        self._prepare(tmp_path)
        lw.save_report(tmp_path, "post-process", "test-topic", _POST_PROCESS_REPORT)
        scripts = []

        async def fake_ai(*args, **kwargs):
            return "Title: T\n\nrevised body"

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
        assert data["w2"]["rounds_left"] == 1

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
    def test_sync_all_returns_expected_keys(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all

        report = sync_all(tmp_path)
        assert "published_index" in report
        assert "published_articles" in report
        assert "products" in report
        assert "internal_links_map" in report
        assert "seo_data_manual" in report

    def test_published_index_valid_json(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path)
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

    def test_products_table_generated(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path)
        prod_path = tmp_path / "products" / "live_products_report.md"
        assert prod_path.exists()
        text = prod_path.read_text(encoding="utf-8")
        assert "Live Products Report" in text

    def test_ilm_generated(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path)
        ilm_path = tmp_path / "context" / "internal-links-map.md"
        assert ilm_path.exists()
        text = ilm_path.read_text(encoding="utf-8")
        assert "Internal Links Map" in text
        assert "已发布文章" in text

    def test_seo_manual_generated(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all
        sync_all(tmp_path)
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
            def execute(self, _sql):
                return Cursor()

        stale = _write(tmp_path / "published" / "inactive-article.md", "old body")
        report = _gen_published_articles(Connection(), tmp_path)

        assert not stale.exists()
        assert (tmp_path / "published" / "active-article.md").exists()
        assert report["removed"] == 1
