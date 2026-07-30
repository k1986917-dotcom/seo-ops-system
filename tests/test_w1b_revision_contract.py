import asyncio
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

        async def fake_precheck(topic, tier, workspace):
            return {
                "success": True,
                "fail_count": 1,
                "report": "# still failing",
            }

        monkeypatch.setattr(lw, "_run_ai_text", fake_body_ai)
        monkeypatch.setattr(lw, "_generate_claim_ledger_for_draft", fake_ledger)
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
