import asyncio
import json

from seo_ops.services import legacy_workflow as lw


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_w1b_mixed_word_and_fact_failures_cleanup_before_retry(
    tmp_path,
    monkeypatch,
):
    topic = "Mixed Failure Topic"
    slug = "mixed-failure-topic"
    workspace = tmp_path / "workspace"
    draft_path = _write(
        workspace / "drafts" / f"{slug}-2026-07-30.md",
        "---\n"
        "Title: Mixed Failure Topic\n"
        "SEO Keywords: mixed failure topic\n"
        "---\n\n"
        "# Mixed Failure Topic\n\n"
        "The formal draft must remain unchanged.\n",
    )
    formal_snapshot = draft_path.read_text(encoding="utf-8")
    _write(
        workspace / "reports" / f"pre-check-{slug}-2026-07-30.md",
        "existing pre-check failure",
    )
    _write(
        workspace / "reports" / f"w2-state-{slug}.json",
        json.dumps(
            {
                "rounds": 0,
                "gate_passed": False,
                "applied": False,
                "precheck_passed": False,
            }
        ),
    )

    unsupported_sentence = (
        "The answer is a Class 2 green 532 nm laser pointer."
    )
    ai_candidate = (
        "---\n"
        "Title: Mixed Failure Topic\n"
        "SEO Keywords: mixed failure topic\n"
        "---\n\n"
        "# Mixed Failure Topic\n\n"
        f"{unsupported_sentence}\n"
    )
    cleaned_candidate = ai_candidate.replace(unsupported_sentence, "").strip()

    original_precheck = {
        "fail_count": 2,
        "checks": [
            {
                "item": "字数 vs tier 下限 硬门控 (Cluster Content)",
                "pass": False,
                "detail": "1038词 < 1200",
            },
            {
                "item": "事实校验",
                "pass": False,
                "detail": "1 个 blocking",
                "fact_issues": [
                    {
                        "reason": "uncovered_factual_sentence",
                        "sentence": unsupported_sentence,
                    }
                ],
            },
        ],
    }
    mixed_candidate_precheck = original_precheck
    cleaned_candidate_precheck = {
        "fail_count": 1,
        "checks": [
            {
                "item": "字数 vs tier 下限 硬门控 (Cluster Content)",
                "pass": False,
                "detail": "1028词 < 1200",
            }
        ],
    }
    candidate_results = iter(
        [mixed_candidate_precheck, cleaned_candidate_precheck]
    )
    cleanup_calls = []

    monkeypatch.setattr(
        lw,
        "repair_draft_frontmatter",
        lambda *args, **kwargs: {"repaired": False},
    )
    monkeypatch.setattr(
        lw,
        "_w1b_precheck_data",
        lambda *args, **kwargs: original_precheck,
    )
    monkeypatch.setattr(
        lw,
        "_revision_context_contracts",
        lambda *args, **kwargs: ({"cards": {}}, ""),
    )
    monkeypatch.setattr(lw, "_w1b_repair_contract", lambda *args: "")
    monkeypatch.setattr(
        lw,
        "_normalize_w1b_candidate",
        lambda draft_md, primary_keyword: draft_md.strip(),
    )

    async def fake_run_ai_text(*args, **kwargs):
        return ai_candidate

    async def fake_generate_claim_ledger(*args, **kwargs):
        return {"version": 1, "claims": []}

    async def fake_supplement(
        draft_md,
        claim_data,
        fact_issues,
        evidence_cards,
        *,
        settings=None,
    ):
        return claim_data, len(fact_issues), 0

    def fake_candidate_precheck(*args, **kwargs):
        return next(candidate_results)

    def fake_remove(draft_md, fact_issues):
        cleanup_calls.append(fact_issues)
        return cleaned_candidate, 1

    monkeypatch.setattr(lw, "_run_ai_text", fake_run_ai_text)
    monkeypatch.setattr(
        lw,
        "_generate_claim_ledger_for_draft",
        fake_generate_claim_ledger,
    )
    monkeypatch.setattr(
        lw,
        "_supplement_claim_ledger_fact_gaps",
        fake_supplement,
    )
    monkeypatch.setattr(
        lw,
        "_candidate_w1b_precheck",
        fake_candidate_precheck,
    )
    monkeypatch.setattr(
        lw,
        "_remove_uncovered_fact_sentences",
        fake_remove,
    )

    result = asyncio.run(
        lw.stage_w1b_revise(
            topic,
            "Cluster Content",
            workspace,
            carry_candidate=True,
        )
    )

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0] == [
        {
            "reason": "uncovered_factual_sentence",
            "sentence": unsupported_sentence,
        }
    ]
    assert result["candidate_rejected"] is True
    assert result["candidate_fail_count"] == 1
    assert result["unsupported_fact_cleanup_requested"] == 1
    assert result["unsupported_fact_cleanup_removed"] == 1
    assert result["candidate_checks"] == [
        {
            "item": "字数 vs tier 下限 硬门控 (Cluster Content)",
            "detail": "1028词 < 1200",
        }
    ]
    assert result["_candidate_draft_md"] == cleaned_candidate
    assert (
        draft_path.read_text(encoding="utf-8")
        == formal_snapshot
    )
