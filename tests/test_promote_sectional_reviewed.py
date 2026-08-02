from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from tools import promote_sectional_reviewed as cli


def _build_workspace(settings):
    from seo_ops.services.sectional_assembly import persist_sectional_assembly
    from seo_ops.services.sectional_rollout import (
        build_shadow_comparison,
        persist_shadow_comparison,
    )
    from tests.test_sectional_rollout import _assembly, _legacy_pair

    assembly = _assembly()
    slug = assembly["metadata"]["slug"]
    topic = assembly["topic"]
    old_draft, old_ledger = _legacy_pair(assembly)
    workspace = (
        Path(settings.data_dir)
        / "legacy_workflow"
        / "laserpointerhub"
        / "runs"
        / "action-3"
        / "current"
        / "laserpointerhub"
    )
    (workspace / "drafts").mkdir(parents=True)
    (workspace / "research").mkdir(parents=True)
    draft_path = workspace / "drafts" / f"{slug}-2026-07-30.md"
    claim_path = workspace / "research" / f"claim-ledger-{slug}.json"
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    persist_sectional_assembly(workspace, assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=topic,
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
        run_metrics={"retry_count": 3},
    )
    persist_shadow_comparison(workspace, slug, report)
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO actions "
            "(id, site_id, action_type, target_ref, decision, decided_at) "
            "VALUES (3, 1, 'create', ?, 'accepted', '2026-08-02T00:00:00+00:00')",
            (topic,),
        )
        connection.commit()
    finally:
        connection.close()
    return workspace, slug, assembly, report, draft_path, claim_path


def _run_preflight(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_load_local_env", lambda: None)
    return cli.main(
        [
            "--action-id",
            "3",
            "--reviewer",
            "operator",
            "--reason",
            "Human review confirmed the candidate meets the published quality standard.",
        ]
    )


def test_preflight_is_read_only_and_reports_eligibility(tmp_path, settings, monkeypatch, capsys):
    workspace, slug, assembly, report, draft_path, claim_path = _build_workspace(settings)
    settings = dataclasses.replace(
        settings, sectional_writing_mode="action", sectional_action_allowlist=(3,)
    )
    before_draft = draft_path.read_bytes()
    before_claim = claim_path.read_bytes()

    assert _run_preflight(settings, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["execute"] is False
    assert payload["operator_override_eligible"] is True
    assert payload["blockers"] == ["excessive_ai_retries"]
    assert payload["comparison_report_sha256"] == report["report_sha256"]
    assert payload["assembly_sha256"] == assembly["assembly_sha256"]
    assert payload["formal_draft_sha256"] != ""
    assert payload["promotion_root"].endswith("promotion-1")
    assert draft_path.read_bytes() == before_draft
    assert claim_path.read_bytes() == before_claim
    promotions = workspace / "drafts" / "sectional" / slug / "promotions"
    assert not promotions.exists()
    assert not list((workspace / "drafts" / "sectional" / slug).glob("promotion-manifest.json"))


def test_preflight_reports_not_eligible_when_rollout_mode_off(
    tmp_path, settings, monkeypatch, capsys
):
    _build_workspace(settings)
    settings = dataclasses.replace(
        settings, sectional_writing_mode="off", sectional_action_allowlist=()
    )
    assert _run_preflight(settings, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operator_override_eligible"] is False
    assert payload["execute"] is False


def test_execute_requires_sha_confirmations(tmp_path, settings, monkeypatch, capsys):
    workspace, slug, assembly, report, draft_path, claim_path = _build_workspace(settings)
    settings = dataclasses.replace(
        settings, sectional_writing_mode="action", sectional_action_allowlist=(3,)
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_load_local_env", lambda: None)
    with pytest.raises(SystemExit, match="--confirm-comparison-sha"):
        cli.main(
            [
                "--action-id",
                "3",
                "--reviewer",
                "operator",
                "--reason",
                "Human review confirmed the candidate meets the published quality standard.",
                "--execute",
            ]
        )
    with pytest.raises(SystemExit, match="does not match"):
        cli.main(
            [
                "--action-id",
                "3",
                "--reviewer",
                "operator",
                "--reason",
                "Human review confirmed the candidate meets the published quality standard.",
                "--execute",
                "--confirm-comparison-sha",
                "0" * 64,
                "--confirm-assembly-sha",
                assembly["assembly_sha256"],
            ]
        )


def test_execute_promotes_with_review_manifest(tmp_path, settings, monkeypatch, capsys):
    workspace, slug, assembly, report, draft_path, claim_path = _build_workspace(settings)
    settings = dataclasses.replace(
        settings, sectional_writing_mode="action", sectional_action_allowlist=(3,)
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_load_local_env", lambda: None)
    before_draft = draft_path.read_bytes()

    assert (
        cli.main(
            [
                "--action-id",
                "3",
                "--reviewer",
                "operator",
                "--reason",
                "Human review confirmed the candidate meets the published quality standard.",
                "--execute",
                "--confirm-comparison-sha",
                report["report_sha256"],
                "--confirm-assembly-sha",
                assembly["assembly_sha256"],
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["execute"] is True
    assert payload["operator_override_eligible"] is True
    assert payload["promotion_manifest"]["status"] == "promoted"
    assert payload["promotion_manifest"]["operator_override"] is True
    assert payload["promotion_manifest"]["operator_reviewer"] == "operator"
    assert payload["promotion_manifest"]["overridden_blockers"] == ["excessive_ai_retries"]
    manifest_path = Path(payload["promotion_manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["operator_reviewed_comparison_sha256"] == report["report_sha256"]
    assert manifest["operator_reviewed_assembly_sha256"] == assembly["assembly_sha256"]
    assert draft_path.read_text(encoding="utf-8") == assembly["draft_markdown"]
    assert draft_path.read_bytes() != before_draft
    from seo_ops.services.sectional_rollout import validate_promotion_manifest

    validate_promotion_manifest(manifest)
