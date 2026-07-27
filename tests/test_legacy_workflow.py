import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestStageDetection:
    def test_slugify(self):
        from seo_ops.services.legacy_workflow import _slugify
        assert _slugify("Best Laser Pointer 2026") == "best-laser-pointer-2026"
        assert _slugify("What! is this?") == "what-is-this"
        assert _slugify("a--b") == "a-b"

    def test_stage_labels_and_steps(self):
        from seo_ops.services.legacy_workflow import stage_label, stage_step, STAGE_ORDER
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

    def test_missing_report_is_empty(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_report
        assert load_report(tmp_path, "post-process", "nope") == ""

    def test_w2_state_defaults_and_roundtrip(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_w2_state, save_w2_state
        assert load_w2_state(tmp_path, "s")["rounds"] == 0
        save_w2_state(tmp_path, "s", {"rounds": 2, "gate_passed": False})
        assert load_w2_state(tmp_path, "s")["rounds"] == 2

    def test_corrupt_w2_state_falls_back(self, tmp_path):
        from seo_ops.services.legacy_workflow import load_w2_state, _reports_dir
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


class TestRevisionLoop:
    def _prepare(self, tmp_path):
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T\n\nbody")
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")

    def test_revision_refused_after_cap(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        lw.save_w2_state(tmp_path, "test-topic", {"rounds": lw.MAX_REVISION_ROUNDS})
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
            return ("## 质量评分\n- 总分: 88.0 → ✅ 通过\n\n"
                    "## 🚦 总门控\n- ✅ 通过，可进入段3 register。", "", 0)

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

    def test_post_process_flags_human_review_at_cap(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
        lw.save_w2_state(tmp_path, "test-topic", {"rounds": lw.MAX_REVISION_ROUNDS})

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
        lw.save_w2_state(tmp_path, "test-topic", {"rounds": lw.MAX_REVISION_ROUNDS})

        async def fake_run(self, script, args):
            return (_POST_PROCESS_REPORT, "", 1)

        monkeypatch.setattr(lw.LegacyRunner, "run", fake_run)
        result = asyncio.run(lw.stage_w2_post_process("test topic", tmp_path))
        assert result["needs_force_confirmation"] is True

    def test_apply_flag_reaches_the_script(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        self._prepare(tmp_path)
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
        _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T")
        result = asyncio.run(lw.stage_w3_register("test topic", tmp_path))
        assert result["success"] is False
        assert "素材包" in result["error"]

    def test_register_writes_checklist(self, tmp_path, monkeypatch):
        from seo_ops.services import legacy_workflow as lw
        draft = _write(tmp_path / "drafts" / "test-topic-2026-01-01.md", "Title: T")
        _write(tmp_path / "material-packs" / "test-topic-2026-01-01.md", "pack")
        _write(tmp_path / "published" / "laser-pointer-battery-guide.md", "old body")

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
    def test_sync_all_returns_expected_keys(self):
        from seo_ops.services.legacy_sync import sync_all
        report = sync_all()
        assert "published_index" in report
        assert "published_articles" in report
        assert "products" in report
        assert "internal_links_map" in report
        assert "seo_data_manual" in report

    def test_published_index_valid_json(self, tmp_path):
        from seo_ops.services.legacy_sync import sync_all
        report = sync_all(tmp_path)
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
