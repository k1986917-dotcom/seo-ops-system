import asyncio
import hashlib
import json


def _card(index: int) -> dict:
    return {
        "evidence_id": f"ev_{index:03d}",
        "support": f"supporting evidence {index}",
        "source_url": f"https://example.com/{index}",
        "concepts": [f"concept-{index}"],
    }


class TestW1bRevisionContract:
    def test_preserved_evidence_ids_are_never_truncated(self):
        from seo_ops.services import legacy_workflow as lw

        all_cards = [_card(index) for index in range(20)]
        rendered = lw._format_evidence_cards(
            {"all_cards": all_cards, "sections": []},
            preserve_evidence_ids={
                card["evidence_id"] for card in all_cards
            },
            max_cards=16,
        )

        for card in all_cards:
            assert card["evidence_id"] in rendered

    def test_hard_contract_contains_exact_mechanical_rules(self, tmp_path):
        from seo_ops.services import legacy_workflow as lw

        draft = tmp_path / "draft.md"
        draft.write_text(
            "---\nSEO Keywords: ceiling laser pointer, green laser\n"
            "---\n\n# H1\n",
            encoding="utf-8",
        )

        contract = lw._w1b_repair_contract(draft, "fallback topic")

        assert "ceiling laser pointer" in contract
        assert "150-160 characters" in contract
        assert "first 100 prose words" in contract
        assert "at least two `##` H2 headings" in contract
        assert "> **Key Takeaways**" in contract
        assert "at most four sentences" in contract
        assert "unsupported" in contract

    def test_claim_ledger_uses_exact_revision_evidence_block(
        self, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        captured = {}

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            captured["purpose"] = purpose
            captured["user_prompt"] = user_prompt
            return '{"version":1,"claims":[]}'

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        result = asyncio.run(
            lw._generate_claim_ledger_for_draft(
                "Qualified analysis only.",
                {"all_cards": [_card(1)], "sections": []},
                evidence_cards_text="EXACT_REVISION_EVIDENCE_BLOCK",
            )
        )

        assert result == {"version": 1, "claims": []}
        assert captured["purpose"] == "legacy_write_claim_ledger"
        assert "EXACT_REVISION_EVIDENCE_BLOCK" in captured["user_prompt"]
        assert "supporting evidence 1" not in captured["user_prompt"]

    def test_synthetic_claim_ledger_selects_id_from_current_batch(self):
        from tests.legacy_workflow_helpers import _synthetic_ai_response

        response = json.loads(
            _synthetic_ai_response(
                "legacy_write_claim_ledger",
                "## Article sentences (use these S-IDs verbatim)\n"
                "S061  Later sentence in the second batch.\n"
                "S062  Another later sentence.\n",
            )
        )

        assert response["claims"][0]["sentence_id"] == "S061"

    def test_claim_ledger_batches_all_sentences_without_dropping_ids(
        self, monkeypatch
    ):
        import re

        from seo_ops.services import legacy_workflow as lw

        draft = "\n\n".join(
            f"Sentence {index:03d} states verifiable fact {index:03d}."
            for index in range(1, 118)
        )
        sentences = lw._extract_draft_sentences(draft)
        assert len(sentences) == 117

        seen_batches = []

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            assert purpose == "legacy_write_claim_ledger"
            assert "EXACT_BATCH_EVIDENCE" in user_prompt
            ids = re.findall(r"^(S\d{3})  ", user_prompt, flags=re.MULTILINE)
            seen_batches.append(ids)
            return json.dumps({
                "version": 1,
                "claims": [
                    {
                        "sentence_id": sentence_id,
                        "claim_type": "general",
                        "evidence_ids": ["ev_001"],
                    }
                    for sentence_id in ids
                ],
            })

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        result = asyncio.run(
            lw._generate_claim_ledger_for_draft(
                draft,
                {"all_cards": [_card(1)], "sections": []},
                evidence_cards_text="EXACT_BATCH_EVIDENCE",
            )
        )

        expected_ids = [sentence["sentence_id"] for sentence in sentences]
        assert [len(batch) for batch in seen_batches] == [60, 57]
        assert [sid for batch in seen_batches for sid in batch] == expected_ids
        assert [claim["sentence_id"] for claim in result["claims"]] == expected_ids
        assert [
            claim["claim_text"] for claim in result["claims"]
        ] == [sentence["text"] for sentence in sentences]

    def test_w1b_revision_backs_up_draft_and_claim_ledger_together(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        topic = "paired backup topic"
        slug = lw._slugify(topic)
        workspace = tmp_path / "ws"
        for name in ("drafts", "research", "reports", "context"):
            (workspace / name).mkdir(parents=True, exist_ok=True)

        old_draft = (
            "---\n"
            "SEO Keywords: paired backup keyword\n"
            "---\n\n"
            "# Old article\n\nOld analysis.\n"
        )
        draft_path = workspace / "drafts" / f"{slug}-2026-01-01.md"
        draft_path.write_text(old_draft, encoding="utf-8")

        old_claim = {
            "version": 1,
            "claims": [],
            "draft_sha256": "old-sha",
        }
        claim_path = workspace / "research" / f"claim-ledger-{slug}.json"
        claim_path.write_text(json.dumps(old_claim), encoding="utf-8")
        lw.save_report(workspace, "pre-check", slug, "# failed pre-check")
        lw.save_w2_state(
            workspace,
            slug,
            {"w1b_rounds": 0, "gate_passed": False, "applied": False},
        )

        exact_cards = "EXACT_SHARED_EVIDENCE_BLOCK"
        captured = {}

        monkeypatch.setattr(
            lw,
            "_revision_context_contracts",
            lambda *args, **kwargs: (
                {"brief": {}, "coverage": {}, "cards": {}},
                exact_cards,
            ),
        )
        monkeypatch.setattr(
            lw,
            "resolve_tier",
            lambda *args, **kwargs: "Cluster Content",
        )

        async def fake_body_ai(purpose, system_prompt, user_prompt, **kwargs):
            assert purpose == "legacy_write_revise_body"
            assert "paired backup keyword" in user_prompt
            return (
                "---\n"
                "SEO Keywords: paired backup keyword\n"
                "---\n\n"
                "# Revised article\n\nQualified analysis only.\n"
            )

        async def fake_ledger(
            draft_md,
            cards,
            *,
            settings=None,
            evidence_cards_text=None,
        ):
            captured["evidence_cards_text"] = evidence_cards_text
            return {"version": 1, "claims": []}

        def fake_precheck_data(
            draft,
            tier,
            workspace,
            slug,
            *,
            claim_path=None,
        ):
            if claim_path is None:
                return {
                    "fail_count": 1,
                    "checks": [{
                        "item": "current failure",
                        "pass": False,
                        "detail": "repair this",
                    }],
                }
            return {"fail_count": 0, "checks": []}

        async def fake_precheck(topic, tier, workspace):
            return {
                "success": True,
                "fail_count": 1,
                "report": "# still failing",
            }

        monkeypatch.setattr(lw, "_run_ai_text", fake_body_ai)
        monkeypatch.setattr(lw, "_generate_claim_ledger_for_draft", fake_ledger)
        monkeypatch.setattr(lw, "_w1b_precheck_data", fake_precheck_data)
        monkeypatch.setattr(lw, "stage_w1b_pre_check", fake_precheck)

        result = asyncio.run(
            lw.stage_w1b_revise(
                topic,
                "Cluster Content",
                workspace,
            )
        )

        draft_backup = workspace / "drafts" / (
            f"{slug}-2026-01-01.precheck-rev1.md"
        )
        claim_backup = workspace / "research" / (
            f"claim-ledger-{slug}.precheck-rev1.json"
        )

        assert result["revised"] is True
        assert result["claim_backup"] == str(claim_backup)
        assert draft_backup.read_text(encoding="utf-8") == old_draft
        assert json.loads(claim_backup.read_text(encoding="utf-8")) == old_claim
        assert captured["evidence_cards_text"] == exact_cards


class TestCanonicalDraftSelection:
    def test_latest_draft_ignores_revision_backups_even_when_newer(
        self, tmp_path
    ):
        import os

        from seo_ops.services import legacy_workflow as lw

        topic = "canonical draft selector"
        slug = lw._slugify(topic)
        workspace = tmp_path / "ws"
        drafts = workspace / "drafts"
        drafts.mkdir(parents=True)

        canonical = drafts / f"{slug}-2026-07-30.md"
        precheck_backup = drafts / (
            f"{slug}-2026-07-30.precheck-rev3.md"
        )
        w2_backup = drafts / f"{slug}-2026-07-30.rev4.md"

        canonical.write_text("canonical", encoding="utf-8")
        precheck_backup.write_text("precheck backup", encoding="utf-8")
        w2_backup.write_text("w2 backup", encoding="utf-8")

        base_ns = 1_800_000_000_000_000_000
        os.utime(canonical, ns=(base_ns, base_ns))
        os.utime(
            precheck_backup,
            ns=(base_ns + 2_000_000_000, base_ns + 2_000_000_000),
        )
        os.utime(
            w2_backup,
            ns=(base_ns + 3_000_000_000, base_ns + 3_000_000_000),
        )

        assert lw._latest_draft(workspace, slug) == canonical

        collected = lw._collect_files(topic, workspace)
        assert collected["draft"] == str(canonical)

    def test_latest_draft_still_chooses_newest_canonical_file(
        self, tmp_path
    ):
        import os

        from seo_ops.services import legacy_workflow as lw

        slug = "canonical-dates"
        workspace = tmp_path / "ws"
        drafts = workspace / "drafts"
        drafts.mkdir(parents=True)

        older = drafts / f"{slug}-2026-07-29.md"
        newer = drafts / f"{slug}-2026-07-30.md"
        older.write_text("older", encoding="utf-8")
        newer.write_text("newer", encoding="utf-8")

        old_ns = 1_800_000_000_000_000_000
        new_ns = old_ns + 1_000_000_000
        os.utime(older, ns=(old_ns, old_ns))
        os.utime(newer, ns=(new_ns, new_ns))

        assert lw._latest_draft(workspace, slug) == newer


class TestW1bCandidateGate:
    def _workspace(self, tmp_path, topic):
        from seo_ops.services import legacy_workflow as lw

        slug = lw._slugify(topic)
        workspace = tmp_path / "ws"
        for name in (
            "drafts",
            "research",
            "reports",
            "context",
            "material-packs",
        ):
            (workspace / name).mkdir(parents=True, exist_ok=True)
        draft_text = (
            "---\n"
            "SEO Keywords: candidate gate keyword\n"
            "---\n\n"
            "# Existing article\n\nExisting analysis.\n"
        )
        draft = workspace / "drafts" / f"{slug}-2026-07-30.md"
        draft.write_text(draft_text, encoding="utf-8")
        claim = workspace / "research" / f"claim-ledger-{slug}.json"
        claim.write_text(
            json.dumps({
                "version": 1,
                "claims": [],
                "draft_sha256": hashlib.sha256(
                    draft_text.encode("utf-8")
                ).hexdigest(),
            }),
            encoding="utf-8",
        )
        lw.save_report(workspace, "pre-check", slug, "# failed")
        lw.save_w2_state(
            workspace,
            slug,
            {"w1b_rounds": 0, "gate_passed": False},
        )
        return lw, workspace, slug, draft, claim

    def test_rejected_candidate_never_replaces_live_pair(
        self, tmp_path, monkeypatch
    ):
        topic = "candidate rejection"
        lw, workspace, slug, draft, claim = self._workspace(
            tmp_path, topic
        )
        old_draft = draft.read_bytes()
        old_claim = claim.read_bytes()

        monkeypatch.setattr(
            lw,
            "resolve_tier",
            lambda *args, **kwargs: "Cluster Content",
        )
        monkeypatch.setattr(
            lw,
            "_revision_context_contracts",
            lambda *args, **kwargs: (
                {"brief": {}, "coverage": {}, "cards": {}},
                "ev_001: support | https://example.com",
            ),
        )

        async def fake_ai(*args, **kwargs):
            return (
                "---\nSEO Keywords: candidate gate keyword\n---\n\n"
                "# Candidate\n\nA 5mW laser is a factual sentence.\n"
            )

        async def fake_ledger(*args, **kwargs):
            return {"version": 1, "claims": []}

        def fake_precheck_data(
            draft_path,
            tier,
            workspace,
            slug,
            *,
            claim_path=None,
        ):
            if claim_path is None:
                return {
                    "fail_count": 1,
                    "checks": [{
                        "item": "事实校验",
                        "pass": False,
                        "detail": "old failure",
                        "fact_issues": [{
                            "reason": "uncovered_factual_sentence",
                            "sentence": "Old factual sentence.",
                            "sentence_sha": "old",
                        }],
                    }],
                }
            return {
                "fail_count": 1,
                "checks": [{
                    "item": "事实校验",
                    "pass": False,
                    "detail": "candidate still uncovered",
                    "fact_issues": [{
                        "reason": "uncovered_factual_sentence",
                        "sentence": "A 5mW laser is a factual sentence.",
                        "sentence_sha": "new",
                    }],
                }],
            }

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)
        monkeypatch.setattr(
            lw, "_generate_claim_ledger_for_draft", fake_ledger
        )
        monkeypatch.setattr(
            lw, "_w1b_precheck_data", fake_precheck_data
        )

        result = asyncio.run(
            lw.stage_w1b_revise(
                topic,
                "Cluster Content",
                workspace,
            )
        )

        assert result["candidate_rejected"] is True
        assert result["retryable"] is True
        assert result["candidate_fail_count"] == 1
        assert draft.read_bytes() == old_draft
        assert claim.read_bytes() == old_claim
        assert not list((workspace / "drafts").glob("*.precheck-rev*.md"))
        assert not list(
            (workspace / "research").glob(
                "claim-ledger-*.precheck-rev*.json"
            )
        )
        assert "A 5mW laser is a factual sentence." in (
            result["retry_feedback"]
        )

    def test_batch_retries_with_full_structured_feedback(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        topic = "retryable topic"
        slug = lw._slugify(topic)
        long_sentence = (
            "A factual sentence that must reach the second attempt: "
            + ("x" * 15000)
            + " FULL_STRUCTURED_TAIL"
        )
        retry_feedback = json.dumps(
            {
                "fail_count": 1,
                "failed_checks": [{
                    "item": "事实校验",
                    "detail": "candidate still uncovered",
                    "fact_issues": [{
                        "reason": "uncovered_factual_sentence",
                        "sentence": long_sentence,
                        "sentence_sha": "sha-full-feedback",
                    }],
                }],
            },
            ensure_ascii=False,
        )
        calls = 0

        async def fake_revise(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "success": False,
                    "revised": False,
                    "retryable": True,
                    "candidate_rejected": True,
                    "retry_feedback": retry_feedback,
                    "error": "candidate failed",
                }

            state = lw.load_w2_state(tmp_path, slug)
            memory = lw._recent_revision_memory(state, "w1b")
            assert retry_feedback in memory
            assert long_sentence in memory
            assert "FULL_STRUCTURED_TAIL" in memory
            return {
                "success": True,
                "revised": True,
                "gate_passed": True,
            }

        monkeypatch.setattr(lw, "stage_w1b_revise", fake_revise)

        result = asyncio.run(
            lw.stage_w1b_revise_batch(
                topic,
                "Cluster Content",
                tmp_path,
            )
        )

        assert calls == 2
        assert result["gate_passed"] is True
        assert result["batch_attempts"] == 2

    def test_fact_check_exposes_full_uncovered_sentence(
        self, tmp_path
    ):
        from data_sources.modules.write_pre_check import _run_fact_check

        sentence = (
            "A 5mW green laser operating at 532nm can produce a visible "
            "beam, and this deliberately long factual sentence must remain "
            "complete in the structured repair payload."
        )
        draft = tmp_path / "draft.md"
        draft.write_text(sentence, encoding="utf-8")
        material = tmp_path / "material.md"
        material.write_text("material", encoding="utf-8")
        evidence = tmp_path / "evidence.json"
        evidence.write_text(
            json.dumps({
                "version": 1,
                "material_pack_sha256": hashlib.sha256(
                    b"material"
                ).hexdigest(),
                "evidence": [],
            }),
            encoding="utf-8",
        )
        claim = tmp_path / "claim.json"
        claim.write_text(
            json.dumps({
                "version": 1,
                "claims": [],
                "draft_sha256": hashlib.sha256(
                    sentence.encode("utf-8")
                ).hexdigest(),
            }),
            encoding="utf-8",
        )

        results = []

        def grade(level, item, detail):
            results.append({
                "level": level,
                "item": item,
                "detail": detail,
                "pass": level != "fail",
            })

        _run_fact_check(
            results,
            grade,
            str(evidence),
            str(claim),
            str(material),
            str(draft),
        )

        fact = next(
            item for item in results if item["item"] == "事实校验"
        )
        assert fact["fact_issues"][0]["sentence"] == sentence
        assert len(fact["fact_issues"][0]["sentence"]) > 80
