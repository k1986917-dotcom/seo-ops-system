import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
        slug = "test-topic"
        (tmp_path / "drafts").mkdir(parents=True)
        (tmp_path / "drafts" / f"{slug}-2026-01-01.md").write_text("Title: Test\n内链:\n - https://example.com/blog/a")
        stage, files = detect_stage("test topic", tmp_path)
        assert stage == "w3_register"


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
