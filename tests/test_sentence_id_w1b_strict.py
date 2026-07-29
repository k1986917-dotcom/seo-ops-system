import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_fact_check(tmp_path, ev_text, cl_text, draft_text="Draft sentence.\n"):
    from data_sources.modules.write_pre_check import _run_fact_check

    ev_f = tmp_path / "ev.json"
    cl_f = tmp_path / "cl.json"
    mp_f = tmp_path / "mp.md"
    dr_f = tmp_path / "draft.md"
    mp_f.write_text("mp", encoding="utf-8")
    mp_sha = hashlib.sha256(b"mp").hexdigest()
    dr_f.write_text(draft_text, encoding="utf-8")
    draft_body = draft_text.strip()
    dr_sha = hashlib.sha256(draft_body.encode("utf-8")).hexdigest()
    ev_f.write_text(
        ev_text.replace("__MP_SHA__", mp_sha),
        encoding="utf-8",
    )
    cl_f.write_text(
        cl_text.replace("__DR_SHA__", dr_sha),
        encoding="utf-8",
    )
    results = []
    def grade(level, msg, detail=''):
        results.append({'item': msg, 'level': level, 'pass': level != 'fail', 'detail': str(detail)})
    _run_fact_check(results, grade, str(ev_f), str(cl_f), str(mp_f), str(dr_f))
    return [r for r in results if '事实校验' in r['item']]


def _valid_ev():
    return '{"version":1,"material_pack_sha256":"__MP_SHA__",' \
           '"evidence":[{"evidence_id":"ev_001","source_url":"https://ex.com/a",' \
           '"quote":"data","canonical_concepts":["x"],"claim_types":["spec"],"required":false}]}'


def _valid_cl(claims_json: str = "") -> str:
    if not claims_json:
        claims_json = '[{"sentence_id":"S001","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]'
    return '{"version":1,"claims":' + claims_json + ',"draft_sha256":"__DR_SHA__"}'


class TestStrictSentenceIdMode:
    """Strict sentence_id verification during W1b fact-checks."""

    def test_correct_sentence_id_passes(self, tmp_path):
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        assert all(r['pass'] for r in fact), f"Should pass: {fact}"

    def test_fake_s999_blocked(self, tmp_path):
        cl = _valid_cl('[{"sentence_id":"S999","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]')
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "在当前草稿中不存在" in joined, joined

    def test_correct_id_wrong_claim_text_blocked(self, tmp_path):
        cl = _valid_cl('[{"sentence_id":"S001","claim_text":"Wrong text.","claim_type":"spec","evidence_ids":["ev_001"]}]')
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim_text 与草稿原句不一致" in joined, joined

    def test_sentence_id_null_blocked(self, tmp_path):
        cl = '[{"sentence_id":null,"claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]'
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(cl),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "sentence_id 是 null" in joined, joined

    def test_sentence_id_empty_string_blocked(self, tmp_path):
        cl = '[{"sentence_id":"","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]'
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(cl),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "空字符串" in joined, joined

    def test_sentence_id_whitespace_blocked(self, tmp_path):
        cl = '[{"sentence_id":"   ","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]'
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(cl),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "纯空白字符串" in joined, joined

    @pytest.mark.parametrize("sentence_id", [" S001", "S001 ", " S001 "])
    def test_sentence_id_edge_whitespace_blocked(
        self, tmp_path, sentence_id
    ):
        claims = json.dumps([{
            "sentence_id": sentence_id,
            "claim_text": "The 5mW green laser has a wavelength of 532nm.",
            "claim_type": "spec",
            "evidence_ids": ["ev_001"],
        }])
        fact = _run_fact_check(
            tmp_path,
            _valid_ev(),
            _valid_cl(claims),
            "The 5mW green laser has a wavelength of 532nm.\n",
        )
        joined = "\n".join(r["detail"] for r in fact)
        assert "含首尾空白" in joined, joined

    def test_sentence_id_int_blocked(self, tmp_path):
        cl = '[{"sentence_id":123,"claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}]'
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(cl),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "类型错误" in joined, joined

    def test_missing_sentence_id_field_in_strict_mode_blocked(self, tmp_path):
        cl = '[{"sentence_id":"S001","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]},'
        cl += '{"claim_text":"Second sentence without ID.","claim_type":"general","evidence_ids":["ev_001"]}]'
        fact = _run_fact_check(tmp_path, _valid_ev(), _valid_cl(cl),
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "缺 sentence_id 字段" in joined, joined

    def test_mixed_old_and_new_mode_forced_strict(self, tmp_path):
        cl = _valid_cl('[{"sentence_id":"S001","claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]},'
                       '{"claim_text":"Old style","claim_type":"general","evidence_ids":["ev_001"]}]')
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        # Strict mode forces every claim to have sentence_id.
        joined = "\n".join(r['detail'] for r in fact)
        assert "缺 sentence_id 字段" in joined, joined

    def test_claim_text_extra_whitespace_blocked(self, tmp_path):
        cl = _valid_cl('[{"sentence_id":"S001","claim_text":"  The 5mW green laser has a wavelength of 532nm.  ","claim_type":"spec","evidence_ids":["ev_001"]}]')
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim_text 与草稿原句不一致" in joined, joined


class TestLegacyMode:
    """When no claim carries sentence_id, the old claim_text matching path
    must still work."""

    def test_legacy_mode_passes(self, tmp_path):
        cl = '{"version":1,"claims":[{"claim_text":"The 5mW green laser has a wavelength of 532nm.","claim_type":"spec","evidence_ids":["ev_001"]}],"draft_sha256":"__DR_SHA__"}'
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        assert all(r['pass'] for r in fact), f"Legacy should pass: {fact}"

    def test_legacy_mode_rejects_bad_claim_text(self, tmp_path):
        cl = '{"version":1,"claims":[{"claim_text":"This text is NOT in the draft.","claim_type":"spec","evidence_ids":["ev_001"]}],"draft_sha256":"__DR_SHA__"}'
        fact = _run_fact_check(tmp_path, _valid_ev(), cl,
                               "The 5mW green laser has a wavelength of 532nm.\n")
        joined = "\n".join(r['detail'] for r in fact)
        assert "claim_text 不在草稿正文中" in joined, joined


class TestSharedExtractorConsistency:
    """W0 and W1b must see the same sentence table for the same draft."""

    def test_frontmatter_excluded(self):
        from data_sources.modules.seo_common import extract_draft_sentences
        draft = "---\nTitle: T\nSlug: s\n---\n\nReal sentence.\n"
        sents = extract_draft_sentences(draft)
        ids = [s["sentence_id"] for s in sents]
        texts = [s["text"] for s in sents]
        assert "S001" in ids
        assert "Real sentence." in texts
        assert not any("Title" in t for t in texts), "frontmatter leaked"

    def test_heading_handling_is_identical(self):
        from data_sources.modules.write_pre_check import _draft_sentence_table
        from src.seo_ops.services.legacy_workflow import _extract_draft_sentences

        draft = "---\nTitle: T\n---\n\n## A heading\n\nBody.\n"
        w0_sentences = _extract_draft_sentences(draft)
        w1b_table = _draft_sentence_table(draft)
        w1b_sentences = [
            {
                "sentence_id": sentence_id,
                "text": sentence["text"],
                "norm": sentence["norm"],
            }
            for sentence_id, sentence in w1b_table.items()
        ]

        assert w0_sentences == w1b_sentences
        assert [sentence["text"] for sentence in w0_sentences] == [
            "## A heading",
            "Body.",
        ]

    def test_markdown_link(self):
        from data_sources.modules.seo_common import extract_draft_sentences
        draft = "---\nTitle: T\n---\n\n[OSHA](http://osha.gov) says Class 3R is safe.\n"
        sents = extract_draft_sentences(draft)
        assert len(sents) >= 1
        assert "[OSHA](http://osha.gov) says Class 3R is safe." in [s["text"] for s in sents]

    def test_multiple_paragraphs_and_sentences(self):
        from data_sources.modules.seo_common import extract_draft_sentences
        draft = "First para sentence one. Sentence two.\n\nSecond para.\n"
        sents = extract_draft_sentences(draft)
        ids = [s["sentence_id"] for s in sents]
        assert ids == ["S001", "S002", "S003"]
        texts = [s["text"] for s in sents]
        assert "First para sentence one." in texts
        assert "Sentence two." in texts
        assert "Second para." in texts

    def test_sentence_id_sequential_order(self):
        from data_sources.modules.seo_common import extract_draft_sentences
        draft = "A. B.\n\nC.\n\nD. E."
        sents = extract_draft_sentences(draft)
        ids = [s["sentence_id"] for s in sents]
        assert ids == ["S001", "S002", "S003", "S004", "S005"]

    def test_empty_draft(self):
        from data_sources.modules.seo_common import extract_draft_sentences
        assert extract_draft_sentences("") == []
        assert extract_draft_sentences("---\n---\n") == []

    def test_w0_and_w1b_share_implementation(self):
        from data_sources.modules.seo_common import extract_draft_sentences as eds
        from data_sources.modules.seo_common import normalize_claim_text as nct
        from src.seo_ops.services.legacy_workflow import _extract_draft_sentences, _normalize_claim
        draft = "---\nTitle: T\n---\n\nReal content with facts. Another sentence here."
        s1 = eds(draft)
        s2 = _extract_draft_sentences(draft)
        assert s1 == s2
        assert nct("Test.") == _normalize_claim("Test.")


class TestFactualSentenceCoverage:
    """Factual sentences not covered by the claim ledger still block."""

    def test_uncovered_factual_sentence_blocks(self, tmp_path):
        draft = "The laser emits 5mW of power. It has a 532nm wavelength.\n"
        # Only cover the first sentence in the ledger.
        cl = _valid_cl('[{"sentence_id":"S001","claim_type":"spec","evidence_ids":["ev_001"]}]')
        fact = _run_fact_check(tmp_path, _valid_ev(), cl, draft)
        joined = "\n".join(r['detail'] for r in fact)
        # The second factual sentence "It has a 532nm wavelength." is not covered.
        assert "文章含事实句但 ledger 未覆盖" in joined, joined


class TestStandaloneScript:
    def test_write_pre_check_standalone_runs_fact_check(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "data_sources/modules/write_pre_check.py"
        material_pack = tmp_path / "material-pack.md"
        evidence_ledger = tmp_path / "evidence-ledger.json"
        claim_ledger = tmp_path / "claim-ledger.json"
        draft = tmp_path / "draft.md"

        material_pack.write_text("mp", encoding="utf-8")
        draft_text = "The 5mW green laser has a wavelength of 532nm.\n"
        draft.write_text(draft_text, encoding="utf-8")
        material_pack_sha = hashlib.sha256(material_pack.read_bytes()).hexdigest()
        draft_sha = hashlib.sha256(
            draft_text.strip().encode("utf-8")
        ).hexdigest()

        evidence_ledger.write_text(
            json.dumps({
                "version": 1,
                "material_pack_sha256": material_pack_sha,
                "evidence": [{
                    "evidence_id": "ev_001",
                    "source_url": "https://example.com/source",
                    "quote": "supporting source text",
                    "canonical_concepts": ["laser"],
                    "claim_types": ["spec"],
                    "required": False,
                }],
            }),
            encoding="utf-8",
        )
        claim_ledger.write_text(
            json.dumps({
                "version": 1,
                "claims": [{
                    "sentence_id": "S001",
                    "claim_text": draft_text.strip(),
                    "claim_type": "spec",
                    "evidence_ids": ["ev_001"],
                }],
                "draft_sha256": draft_sha,
            }),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--draft",
                str(draft),
                "--tier",
                "Cluster Content",
                "--pack",
                str(material_pack),
                "--evidence-ledger",
                str(evidence_ledger),
                "--claim-ledger",
                str(claim_ledger),
                "--material-pack",
                str(material_pack),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = proc.stdout + proc.stderr

        # The short fixture intentionally fails unrelated SEO checks, so the
        # CLI exits 1. It must still complete normally and run fact checking.
        assert proc.returncode == 1, combined
        assert "Traceback" not in combined
        assert "ModuleNotFoundError" not in combined
        assert "事实校验" in proc.stdout


class TestExistingTestsStillPass:
    """Smoke: confirm the existing sentence-id binding tests still hold."""

    def test_import_extract_draft_sentences(self):
        from data_sources.modules.seo_common import extract_draft_sentences as eds
        draft = "---\nTitle: T\n---\n\nSentence one. Sentence two."
        sents = eds(draft)
        assert sents[0]["sentence_id"] == "S001"
        assert sents[1]["sentence_id"] == "S002"
