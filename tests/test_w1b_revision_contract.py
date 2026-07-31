import asyncio
import hashlib
import json
import re


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

    def test_revision_prompt_omits_full_precheck_and_brief_excerpt(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        topic = "compact prompt topic"
        slug = lw._slugify(topic)
        workspace = tmp_path / "ws"
        for name in ("drafts", "research", "reports", "context"):
            (workspace / name).mkdir(parents=True, exist_ok=True)
        draft_text = (
            "---\n"
            "SEO Keywords: compact prompt keyword\n"
            "---\n\n"
            "# Existing article\n\nExisting practical analysis.\n"
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
        lw.save_report(
            workspace,
            "pre-check",
            slug,
            "# failed\n\nFULL_PRECHECK_REPORT_SENTINEL\n" + ("x" * 3000),
        )
        lw.save_w2_state(
            workspace,
            slug,
            {"w1b_rounds": 0, "gate_passed": False},
        )

        captured: dict[str, str | int] = {}

        monkeypatch.setattr(
            lw,
            "resolve_tier",
            lambda *args, **kwargs: "Cluster Content",
        )

        def fake_context_contracts(*args, **kwargs):
            captured["relevant_text"] = kwargs["relevant_text"]
            return (
                {
                    "brief": {
                        "topic": topic,
                        "tier": "Cluster Content",
                        "guidance": "Use concise practical guidance.",
                        "outline": ["Compact Prompt H2"],
                        "research_brief_excerpt": (
                            "FULL_BRIEF_EXCERPT_SENTINEL" + ("y" * 3000)
                        ),
                    },
                    "coverage": {
                        "sections": [{
                            "section_id": "section_1",
                            "heading": "Compact Prompt H2",
                            "must_cover": True,
                            "candidate_evidence_ids": ["ev_compact"],
                        }],
                    },
                },
                "- ev_compact: direct support | https://example.com/source",
            )

        monkeypatch.setattr(
            lw, "_revision_context_contracts", fake_context_contracts
        )
        monkeypatch.setattr(
            lw,
            "_w1b_precheck_data",
            lambda *args, **kwargs: {
                "fail_count": 2,
                "checks": [{
                    "item": "事实校验",
                    "pass": False,
                    "detail": "STRUCTURED_DETAIL_SENTINEL",
                    "fact_issues": [{
                        "reason": "uncovered_factual_sentence",
                        "sentence": "FACT_SENTENCE_SENTINEL",
                        "sentence_id": "s001",
                    }],
                }],
            },
        )

        async def stop_after_prompt(purpose, system_prompt, user_prompt, **kwargs):
            captured["purpose"] = purpose
            captured["prompt"] = user_prompt
            captured["max_tokens"] = kwargs["max_tokens"]
            raise RuntimeError("stop after prompt capture")

        monkeypatch.setattr(lw, "_run_ai_text", stop_after_prompt)

        result = asyncio.run(
            lw.stage_w1b_revise(topic, "Cluster Content", workspace)
        )

        prompt = str(captured["prompt"])
        assert result["error"] == "stop after prompt capture"
        assert captured["purpose"] == "legacy_write_revise_body"
        assert captured["max_tokens"] == 8000
        assert "STRUCTURED_DETAIL_SENTINEL" in prompt
        assert "FACT_SENTENCE_SENTINEL" in prompt
        assert "Repair focus summary" in prompt
        assert "Compact Prompt H2" in prompt
        assert "ev_compact" in prompt
        assert "FULL_PRECHECK_REPORT_SENTINEL" not in prompt
        assert "FULL_BRIEF_EXCERPT_SENTINEL" not in prompt
        assert "Pre-check report (human-readable context)" not in prompt
        assert "FULL_PRECHECK_REPORT_SENTINEL" not in str(
            captured["relevant_text"]
        )

    def test_candidate_normalizer_fixes_mechanical_rules_without_dropping_content(
        self, tmp_path
    ):
        import re

        from data_sources.modules import write_pre_check
        from seo_ops.services import legacy_workflow as lw

        keyword = "ceiling laser pointer"
        original_sentences = [
            "The first original sentence gives practical context for the reader.",
            "The second original sentence keeps the existing discussion intact.",
            "The third original sentence remains part of the same paragraph.",
            "The fourth original sentence must still be present after normalization.",
            "The fifth original sentence proves that paragraph splitting is lossless.",
        ]
        draft = (
            "---\n"
            "Title: Practical Ceiling Guide\n"
            "SEO Title: Practical Ceiling Laser Pointer Planning Guide 2026\n"
            f"SEO Description: {'D' * 184}\n"
            f"SEO Keywords: {keyword}, green laser\n"
            "---\n\n"
            "# Practical Ceiling Guide\n\n"
            "Opening guidance without the required phrase appears here.\n\n"
            "## Selection Criteria\n\n"
            + " ".join(original_sentences)
            + "\n\n## Safe Setup\n\n"
            "Use the checklist to organize the work.\n"
        )

        normalized = lw._normalize_w1b_candidate(draft, keyword)
        path = tmp_path / "candidate.md"
        path.write_text(normalized, encoding="utf-8")
        meta, body, clean, _, _ = write_pre_check.parse_draft(str(path))

        assert 150 <= len(meta["seo description"]) <= 160
        assert keyword in write_pre_check.words_before_n(clean, 100)
        h2s = re.findall(r"^## (.+)", body, re.MULTILINE)
        assert sum(keyword in heading.lower() for heading in h2s) >= 2

        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\n+", clean)
            if len(paragraph.split()) > 20
        ]
        assert all(
            write_pre_check.sentence_count(paragraph) <= 4
            for paragraph in paragraphs
        )
        for sentence in original_sentences:
            assert sentence in normalized
        assert lw._normalize_w1b_candidate(normalized, keyword) == normalized


    def test_candidate_normalizer_aligns_candidate_keyword_and_mixed_blocks(
        self, tmp_path
    ):
        from data_sources.modules import write_pre_check
        from seo_ops.services import legacy_workflow as lw

        keyword = "ceiling laser pointer"
        long_sentences = [
            "Sentence one gives enough practical context for the reader.",
            "Sentence two preserves the original discussion without deletion.",
            "Sentence three remains inside the same markdown block.",
            "Sentence four must stay visible after deterministic normalization.",
            "Sentence five proves that a heading does not exempt a long paragraph.",
        ]
        draft = (
            "---\n"
            "Title: Practical Ceiling Guide\n"
            "SEO Title: Practical Ceiling Laser Pointer Planning Guide 2026\n"
            "SEO Description: Too short.\n"
            "SEO Keywords: unrelated candidate phrase, green laser\n"
            "---\n\n"
            "# Practical Ceiling Guide\n\n"
            "Opening guidance without the canonical phrase.\n\n"
            "## Selection Criteria\n"
            + " ".join(long_sentences)
            + "\n\n## Safe Setup\n"
            + " ".join(long_sentences)
            + "\n"
        )

        normalized = lw._normalize_w1b_candidate(draft, keyword)
        path = tmp_path / "candidate-mismatch.md"
        path.write_text(normalized, encoding="utf-8")
        meta, body, clean, _, _ = write_pre_check.parse_draft(str(path))

        assert meta["seo keywords"].split(",", 1)[0].strip() == keyword
        assert keyword in write_pre_check.words_before_n(clean, 100)
        h2s = re.findall(r"^## (.+)", body, re.MULTILINE)
        assert sum(keyword in heading.lower() for heading in h2s) >= 2
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\n+", clean)
            if len(paragraph.split()) > 20
        ]
        assert all(
            write_pre_check.sentence_count(paragraph) <= 4
            for paragraph in paragraphs
        )
        for sentence in long_sentences:
            assert normalized.count(sentence) == 2
        assert lw._normalize_w1b_candidate(normalized, keyword) == normalized

    def test_focused_fact_gap_audit_adds_only_validated_missing_ids(
        self, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        draft = (
            "The device uses a 532 nm wavelength.\n\n"
            "Qualified planning advice only."
        )
        sentences = lw._extract_draft_sentences(draft)
        factual = sentences[0]
        captured = {}

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            captured["prompt"] = user_prompt
            return json.dumps({
                "version": 1,
                "claims": [{
                    "sentence_id": factual["sentence_id"],
                    "claim_type": "technical_specification",
                    "evidence_ids": ["ev_001"],
                }],
            })

        monkeypatch.setattr(lw, "_run_ai_text", fake_ai)

        canonical, requested, added = asyncio.run(
            lw._supplement_claim_ledger_fact_gaps(
                draft,
                {"version": 1, "claims": []},
                [{
                    "reason": "uncovered_factual_sentence",
                    "sentence": factual["text"],
                }],
                "ev_001 evidence card",
            )
        )

        assert requested == 1
        assert added == 1
        assert factual["sentence_id"] in captured["prompt"]
        assert canonical["claims"][0]["sentence_id"] == factual["sentence_id"]
        assert canonical["claims"][0]["claim_text"] == factual["text"]


    def test_non_prose_blocks_do_not_create_paragraph_or_fact_failures(
        self, tmp_path
    ):
        from data_sources.modules import write_pre_check

        draft = (
            "---\n"
            "Title: Ceiling Laser Pointer Guide\n"
            "SEO Title: Ceiling Laser Pointer Planning and Safety Guide 2026\n"
            "SEO Description: "
            + ("Practical ceiling laser pointer planning guidance for safer "
               "selection, setup, alignment, and everyday project decisions. "
               "Review the key considerations before use.")
            + "\n"
            "SEO Keywords: ceiling laser pointer, alignment guide\n"
            "---\n\n"
            "# Ceiling Laser Pointer Guide\n\n"
            "A 532 nm wavelength is one documented option for this example. "
            "The surrounding explanation gives readers practical context. "
            "The third sentence remains ordinary reader-facing prose. "
            "The fourth sentence keeps this paragraph within the limit.\n\n"
            "```text\n"
            "A coded example says the beam uses 650 nm. "
            "It has five sentences. It should not count. "
            "It remains metadata. It is not article prose.\n"
            "```\n\n"
            "<script type='application/ld+json'>\n"
            '{"@context":"https://schema.org","@type":"FAQPage",'
            '"text":"FDA Class 2 appears here. Sentence two. Sentence three. '
            'Sentence four. Sentence five. Sentence six."}\n'
            "</script>\n"
        )
        path = tmp_path / "non-prose-blocks.md"
        path.write_text(draft, encoding="utf-8")

        _, _, clean, prose, _ = write_pre_check.parse_draft(str(path))
        assert "FAQPage" in clean
        assert "650 nm" in clean
        assert "FAQPage" not in prose
        assert "650 nm" not in prose

        result = write_pre_check.run(str(path), tier="Cluster Content")
        paragraph_check = next(
            check for check in result["checks"]
            if check["item"].startswith("段落≤4句")
        )
        assert paragraph_check["pass"] is True

        facts = write_pre_check._extract_factual_sentences(draft)
        sentences = [item["sentence"] for item in facts]
        assert any("532 nm" in sentence for sentence in sentences)
        assert all("650 nm" not in sentence for sentence in sentences)
        assert all("FDA Class 2" not in sentence for sentence in sentences)


    def test_candidate_normalizer_adds_only_missing_reader_visible_ctas(
        self, tmp_path
    ):
        from data_sources.modules import write_pre_check
        from seo_ops.services import legacy_workflow as lw

        draft = (
            "---\n"
            "SEO Keywords: ceiling laser pointer\n"
            "---\n\n"
            "# Guide\n\n"
            "Explore the available options before deciding.\n\n"
            "```text\nContact us inside code must not count.\n```\n"
        )

        normalized = lw._normalize_w1b_candidate(
            draft,
            "ceiling laser pointer",
        )
        candidate = tmp_path / "candidate-with-ctas.md"
        candidate.write_text(normalized, encoding="utf-8")
        _, _, _, prose, _ = write_pre_check.parse_draft(str(candidate))

        assert len(
            list(re.finditer(write_pre_check.CTA_INDICATORS, prose))
        ) >= 2
        assert normalized.count(
            "## ceiling laser pointer: Next Steps"
        ) == 1
        assert lw._normalize_w1b_candidate(
            normalized,
            "ceiling laser pointer",
        ) == normalized

    def test_fact_extraction_skips_headings_but_keeps_bullets_and_prose(
        self
    ):
        from data_sources.modules import write_pre_check

        draft = (
            "## IEC 60825 Compliance Overview\n\n"
            "- IEC 60825 applies to this documented requirement.\n\n"
            "The device uses a documented 532 nm wavelength."
        )

        facts = write_pre_check._extract_factual_sentences(draft)
        sentences = [item["sentence"] for item in facts]

        assert all("Compliance Overview" not in item for item in sentences)
        assert any("IEC 60825 applies" in item for item in sentences)
        assert any("532 nm wavelength" in item for item in sentences)

    def test_focused_fact_gap_audit_rechecks_valid_omissions(
        self, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        draft = (
            "The device uses a documented 532 nm wavelength.\n\n"
            "The second documented option uses a 650 nm wavelength."
        )
        sentences = lw._extract_draft_sentences(draft)
        calls = []

        async def fake_batch(
            batch_sentences,
            evidence_cards,
            *,
            batch_label,
            settings=None,
            split_depth=0,
        ):
            calls.append([
                sentence["sentence_id"]
                for sentence in batch_sentences
            ])
            selected = batch_sentences[:1]
            return [
                {
                    "sentence_id": sentence["sentence_id"],
                    "claim_text": sentence["text"],
                    "claim_type": "technical_specification",
                    "evidence_ids": ["ev_001"],
                }
                for sentence in selected
            ]

        monkeypatch.setattr(
            lw,
            "_generate_claim_ledger_batch",
            fake_batch,
        )

        canonical, requested, added = asyncio.run(
            lw._supplement_claim_ledger_fact_gaps(
                draft,
                {"version": 1, "claims": []},
                [
                    {
                        "reason": "uncovered_factual_sentence",
                        "sentence": sentence["text"],
                    }
                    for sentence in sentences
                ],
                "ev_001 evidence card",
            )
        )

        assert [len(batch) for batch in calls] == [2, 1]
        assert requested == 2
        assert added == 2
        assert {
            claim["sentence_id"] for claim in canonical["claims"]
        } == {
            sentence["sentence_id"]
            for sentence in sentences
        }


    def test_remove_uncovered_fact_sentences_is_exact_and_conservative(
        self
    ):
        from seo_ops.services import legacy_workflow as lw

        draft = (
            "---\n"
            "SEO Keywords: ceiling laser pointer\n"
            "---\n\n"
            "# Guide\n\n"
            "Keep this qualified recommendation.\n\n"
            "The device uses a documented 532 nm wavelength.\n\n"
            "- The second option uses a documented 650 nm wavelength.\n\n"
            "Keep this closing advice.\n"
        )
        issues = [
            {
                "reason": "uncovered_factual_sentence",
                "sentence": (
                    "The device uses a documented 532 nm wavelength."
                ),
            },
            {
                "reason": "uncovered_factual_sentence",
                "sentence": (
                    "- The second option uses a documented 650 nm wavelength."
                ),
            },
            {
                "reason": "other_problem",
                "sentence": "Keep this qualified recommendation.",
            },
        ]

        cleaned, removed = lw._remove_uncovered_fact_sentences(
            draft,
            issues,
        )

        assert removed == 2
        assert cleaned.startswith("---\nSEO Keywords:")
        assert "532 nm wavelength" not in cleaned
        assert "650 nm wavelength" not in cleaned
        assert "Keep this qualified recommendation." in cleaned
        assert "Keep this closing advice." in cleaned

    def test_remove_uncovered_fact_sentences_no_match_is_noop(self):
        from seo_ops.services import legacy_workflow as lw

        draft = "# Guide\n\nQualified recommendation only.\n"
        cleaned, removed = lw._remove_uncovered_fact_sentences(
            draft,
            [{
                "reason": "uncovered_factual_sentence",
                "sentence": "A sentence that is not present.",
            }],
        )

        assert removed == 0
        assert cleaned == draft

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


    def test_claim_ledger_retries_truncated_batch_then_succeeds(
        self, monkeypatch
    ):
        import re

        from seo_ops.services import legacy_workflow as lw

        draft = "\n\n".join(
            f"Sentence {index:03d} states verifiable fact {index:03d}."
            for index in range(1, 118)
        )
        sentences = lw._extract_draft_sentences(draft)
        seen_batches = []
        second_batch_calls = 0

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            nonlocal second_batch_calls
            ids = re.findall(r"^(S\d{3})  ", user_prompt, flags=re.MULTILINE)
            seen_batches.append(ids)
            if ids and ids[0] == "S061":
                second_batch_calls += 1
                if second_batch_calls == 1:
                    return '{"version":1,"claims":[{"sentence_id":"S061"'
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
        assert [len(batch) for batch in seen_batches] == [60, 57, 57]
        assert [claim["sentence_id"] for claim in result["claims"]] == expected_ids

    def test_claim_ledger_splits_persistently_truncated_batch(
        self, monkeypatch
    ):
        import re

        from seo_ops.services import legacy_workflow as lw

        draft = "\n\n".join(
            f"Sentence {index:03d} states verifiable fact {index:03d}."
            for index in range(1, 118)
        )
        sentences = lw._extract_draft_sentences(draft)
        seen_batches = []

        async def fake_ai(purpose, system_prompt, user_prompt, **kwargs):
            ids = re.findall(r"^(S\d{3})  ", user_prompt, flags=re.MULTILINE)
            seen_batches.append(ids)
            if ids and ids[0] == "S061" and len(ids) == 57:
                return '{"version":1,"claims":[{"sentence_id":"S061"'
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
        assert [len(batch) for batch in seen_batches] == [60, 57, 57, 28, 29]
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
            captured["ledger_draft"] = draft_md
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
        assert "This guide focuses on paired backup keyword." in captured["ledger_draft"]
        description_match = re.search(
            r"^SEO Description:\s*(.+)$",
            captured["ledger_draft"],
            re.MULTILINE,
        )
        assert description_match is not None
        assert 150 <= len(description_match.group(1)) <= 160


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

    def test_empty_revision_body_is_retryable_without_touching_live_pair(
        self, tmp_path, monkeypatch
    ):
        topic = "empty revision response"
        lw, workspace, _slug, draft, claim = self._workspace(
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
        monkeypatch.setattr(
            lw,
            "_w1b_precheck_data",
            lambda *args, **kwargs: {
                "fail_count": 1,
                "checks": [{
                    "item": "事实校验",
                    "pass": False,
                    "detail": "repair this",
                }],
            },
        )

        async def empty_ai(*args, **kwargs):
            raise ValueError("AI 返回空文本")

        monkeypatch.setattr(lw, "_run_ai_text", empty_ai)

        result = asyncio.run(
            lw.stage_w1b_revise(
                topic,
                "Cluster Content",
                workspace,
            )
        )

        assert result == {
            "success": False,
            "revised": False,
            "retryable": True,
            "error": "AI 返回空文本",
        }
        assert draft.read_bytes() == old_draft
        assert claim.read_bytes() == old_claim
        assert not list((workspace / "drafts").glob("*.precheck-rev*.md"))
        assert not list(
            (workspace / "research").glob(
                "claim-ledger-*.precheck-rev*.json"
            )
        )

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


    def test_batch_second_attempt_uses_rejected_candidate_not_live_draft(
        self, tmp_path, monkeypatch
    ):
        from seo_ops.services import legacy_workflow as lw

        topic = "cumulative candidate topic"
        slug = lw._slugify(topic)
        calls = []

        async def fake_revise(
            topic,
            tier,
            workspace,
            settings=None,
            *,
            candidate_seed_md=None,
            candidate_seed_feedback=None,
            carry_candidate=False,
        ):
            calls.append({
                "candidate_seed_md": candidate_seed_md,
                "candidate_seed_feedback": candidate_seed_feedback,
                "carry_candidate": carry_candidate,
            })
            if len(calls) == 1:
                assert candidate_seed_md is None
                assert candidate_seed_feedback is None
                assert carry_candidate is True
                return {
                    "success": False,
                    "revised": False,
                    "retryable": True,
                    "candidate_rejected": True,
                    "retry_feedback": "EXACT_FIRST_FAILURE",
                    "_candidate_draft_md": "FIRST_REJECTED_CANDIDATE",
                    "_candidate_retry_feedback": "EXACT_FIRST_FAILURE",
                    "error": "candidate failed",
                }

            assert candidate_seed_md == "FIRST_REJECTED_CANDIDATE"
            assert candidate_seed_feedback == "EXACT_FIRST_FAILURE"
            assert carry_candidate is True
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

        assert len(calls) == 2
        assert result["gate_passed"] is True
        assert result["batch_attempts"] == 2
        assert result["candidate_chain_count"] == 1
        assert "_candidate_draft_md" not in result
        assert "_candidate_retry_feedback" not in result

        state = lw.load_w2_state(tmp_path, slug)
        serialized = json.dumps(state, ensure_ascii=False)
        assert "FIRST_REJECTED_CANDIDATE" not in serialized
        assert "EXACT_FIRST_FAILURE" in serialized

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
