import copy
import hashlib
import json
from pathlib import Path

import pytest

from seo_ops.services.sectional_assembly import assemble_sectional_article
from seo_ops.services.sectional_rollout import (
    SectionalRolloutError,
    build_rollout_policy,
    build_shadow_comparison,
    load_shadow_comparison,
    persist_shadow_comparison,
    promote_sectional_assembly,
    rollback_sectional_promotion,
    rollout_decision,
    validate_rollout_policy,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _delivery():
    from tests.test_sectional_assembly import _build_delivery

    return _build_delivery()


def _metadata():
    from tests.test_sectional_assembly import _metadata

    return _metadata()


def _assembly():
    delivery = _delivery()
    ledger = {"version": 1, "draft_sha256": delivery["draft_sha256"], "claims": []}
    return assemble_sectional_article(delivery, _metadata(), ledger)


def _legacy_pair(assembly):
    draft = assembly["delivery"]["draft_markdown"]
    ledger = {
        "version": 1,
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "claims": [],
    }
    return draft, ledger


def test_rollout_defaults_fail_closed_and_action_requires_allowlist():
    off = build_rollout_policy("off")
    shadow = build_rollout_policy("shadow", [3])
    action = build_rollout_policy("action", [3, 3, 7])

    assert rollout_decision(off, 3)["shadow_enabled"] is False
    assert rollout_decision(shadow, 3) == {
        "version": 1,
        "action_id": 3,
        "mode": "shadow",
        "shadow_enabled": True,
        "promotion_allowed": False,
        "reason_code": "shadow_only",
        "policy_sha256": shadow["policy_sha256"],
    }
    assert rollout_decision(action, 3)["promotion_allowed"] is True
    assert rollout_decision(action, 4)["reason_code"] == "action_not_allowlisted"
    assert action["allowed_action_ids"] == [3, 7]


def test_settings_parse_sectional_mode_and_action_allowlist(tmp_path, monkeypatch):
    from seo_ops import config

    monkeypatch.setenv("SEO_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEO_OPS_SECTIONAL_WRITING_MODE", "ACTION")
    monkeypatch.setenv("SEO_OPS_SECTIONAL_ACTION_ALLOWLIST", "7,3,bad,3,0,-1")
    monkeypatch.setenv("SEO_OPS_SECTIONAL_AI_CALL_LIMIT", "2")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.sectional_writing_mode == "action"
        assert settings.sectional_action_allowlist == (3, 7)
        assert settings.sectional_ai_call_limit == 8
    finally:
        config.get_settings.cache_clear()

    monkeypatch.setenv("SEO_OPS_SECTIONAL_WRITING_MODE", "unsafe-all")
    settings = config.get_settings()
    assert settings.sectional_writing_mode == "off"
    config.get_settings.cache_clear()


def test_policy_rejects_invalid_mode_ids_and_sha_tampering():
    with pytest.raises(SectionalRolloutError, match="mode"):
        build_rollout_policy("all")
    with pytest.raises(SectionalRolloutError, match="positive"):
        build_rollout_policy("action", [0])
    tampered = build_rollout_policy("action", [3])
    tampered["allowed_action_ids"] = [4]
    with pytest.raises(SectionalRolloutError, match="SHA"):
        validate_rollout_policy(tampered)


def test_shadow_comparison_is_deterministic_and_persists(tmp_path):
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
        run_metrics={"ai_calls": 8, "prompt_chars": 24000},
    )

    assert report["recommendation"] == "eligible_for_single_action_promotion"
    assert report["blockers"] == []
    assert report == build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
        run_metrics={"ai_calls": 8, "prompt_chars": 24000},
    )
    path = persist_shadow_comparison(tmp_path, "ceiling-marking", report)
    assert load_shadow_comparison(
        tmp_path,
        "ceiling-marking",
        3,
        expected_report_sha256=report["report_sha256"],
    ) == report
    Path(path).write_text("{}", encoding="utf-8")
    assert load_shadow_comparison(tmp_path, "ceiling-marking", 3) is None


def test_comparison_rejects_old_ledger_mismatch_and_detects_regression():
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    broken = copy.deepcopy(old_ledger)
    broken["draft_sha256"] = "0" * 64
    with pytest.raises(SectionalRolloutError, match="old claim ledger"):
        build_shadow_comparison(
            action_id=3,
            topic=assembly["topic"],
            old_draft=old_draft,
            old_claim_ledger=broken,
            new_assembly=assembly,
        )

    old_with_claim = copy.deepcopy(old_ledger)
    sentence = assembly["delivery"]["sentences"][0]
    old_with_claim["claims"] = [{
        "sentence_id": sentence["sentence_id"],
        "claim_text": sentence["text"],
        "claim_type": "general",
        "evidence_ids": ["ev-old"],
    }]
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_with_claim,
        new_assembly=assembly,
    )
    assert report["recommendation"] == "keep_legacy"
    assert "claim_coverage_regression" in report["blockers"]


def test_comparison_blocks_empty_responses_and_excessive_retries():
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
        run_metrics={"empty_response_count": 1, "retry_count": 3},
    )
    assert report["recommendation"] == "keep_legacy"
    assert "empty_ai_response_observed" in report["blockers"]
    assert "excessive_ai_retries" in report["blockers"]


def test_promotion_requires_allowlist_approved_comparison_and_unchanged_pair(tmp_path):
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)

    with pytest.raises(SectionalRolloutError, match="not allowed"):
        promote_sectional_assembly(
            workspace=tmp_path,
            slug="ceiling-marking",
            action_id=3,
            policy=build_rollout_policy("shadow", [3]),
            comparison=report,
            assembly=assembly,
            formal_draft_path=draft_path,
            formal_claim_path=claim_path,
            expected_draft_sha256=_sha(old_draft_bytes),
            expected_claim_sha256=_sha(old_claim_bytes),
        )

    draft_path.write_text(old_draft + "changed", encoding="utf-8")
    with pytest.raises(SectionalRolloutError, match="draft changed"):
        promote_sectional_assembly(
            workspace=tmp_path,
            slug="ceiling-marking",
            action_id=3,
            policy=build_rollout_policy("action", [3]),
            comparison=report,
            assembly=assembly,
            formal_draft_path=draft_path,
            formal_claim_path=claim_path,
            expected_draft_sha256=_sha(old_draft_bytes),
            expected_claim_sha256=_sha(old_claim_bytes),
        )


def test_promotion_and_rollback_restore_exact_legacy_bytes(tmp_path):
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)

    manifest = promote_sectional_assembly(
        workspace=tmp_path,
        slug="ceiling-marking",
        action_id=3,
        policy=build_rollout_policy("action", [3]),
        comparison=report,
        assembly=assembly,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        expected_draft_sha256=_sha(old_draft_bytes),
        expected_claim_sha256=_sha(old_claim_bytes),
    )
    assert manifest["status"] == "promoted"
    assert draft_path.read_text(encoding="utf-8") == assembly["draft_markdown"]
    manifest_path = (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking"
        / "promotions"
        / "action-3"
        / "promotion-1"
        / "promotion-manifest.json"
    )
    rolled_back = rollback_sectional_promotion(manifest_path)
    assert rolled_back["status"] == "rolled_back"
    assert draft_path.read_bytes() == old_draft_bytes
    assert claim_path.read_bytes() == old_claim_bytes


def test_promotion_restores_pair_when_second_replace_fails(tmp_path, monkeypatch):
    from seo_ops.services import sectional_rollout as rollout

    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    original_replace = rollout.os.replace

    def fail_formal_claim(source, destination):
        if Path(destination) == claim_path.resolve():
            raise OSError("simulated formal claim replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(rollout.os, "replace", fail_formal_claim)
    with pytest.raises(OSError, match="simulated formal claim"):
        promote_sectional_assembly(
            workspace=tmp_path,
            slug="ceiling-marking",
            action_id=3,
            policy=build_rollout_policy("action", [3]),
            comparison=report,
            assembly=assembly,
            formal_draft_path=draft_path,
            formal_claim_path=claim_path,
            expected_draft_sha256=_sha(old_draft_bytes),
            expected_claim_sha256=_sha(old_claim_bytes),
        )
    assert draft_path.read_bytes() == old_draft_bytes
    assert claim_path.read_bytes() == old_claim_bytes


def test_promotion_restores_pair_when_final_manifest_write_fails(tmp_path, monkeypatch):
    from seo_ops.services import sectional_rollout as rollout

    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    original_atomic_write = rollout._atomic_write
    manifest_writes = 0

    def fail_final_manifest(path, payload):
        nonlocal manifest_writes
        if Path(path).name == "promotion-manifest.json":
            manifest_writes += 1
            if manifest_writes == 2:
                raise OSError("simulated final manifest failure")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(rollout, "_atomic_write", fail_final_manifest)
    with pytest.raises(OSError, match="simulated final manifest"):
        promote_sectional_assembly(
            workspace=tmp_path,
            slug="ceiling-marking",
            action_id=3,
            policy=build_rollout_policy("action", [3]),
            comparison=report,
            assembly=assembly,
            formal_draft_path=draft_path,
            formal_claim_path=claim_path,
            expected_draft_sha256=_sha(old_draft_bytes),
            expected_claim_sha256=_sha(old_claim_bytes),
        )
    assert draft_path.read_bytes() == old_draft_bytes
    assert claim_path.read_bytes() == old_claim_bytes
    promotion_base = (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking"
        / "promotions"
        / "action-3"
    )
    assert list(promotion_base.glob("promotion-*")) == []


def test_manifest_rejects_paths_outside_workspace(tmp_path):
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    manifest = promote_sectional_assembly(
        workspace=tmp_path,
        slug="ceiling-marking",
        action_id=3,
        policy=build_rollout_policy("action", [3]),
        comparison=report,
        assembly=assembly,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        expected_draft_sha256=_sha(old_draft_bytes),
        expected_claim_sha256=_sha(old_claim_bytes),
    )
    tampered = copy.deepcopy(manifest)
    tampered["formal_draft_path"] = "/tmp/outside.md"
    tampered.pop("manifest_sha256")
    from seo_ops.services.sectional_rollout import validate_promotion_manifest

    tampered["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            tampered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(SectionalRolloutError, match="outside the workspace"):
        validate_promotion_manifest(tampered)


def test_rollback_stops_when_promoted_files_drift(tmp_path):
    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    promote_sectional_assembly(
        workspace=tmp_path,
        slug="ceiling-marking",
        action_id=3,
        policy=build_rollout_policy("action", [3]),
        comparison=report,
        assembly=assembly,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        expected_draft_sha256=_sha(old_draft_bytes),
        expected_claim_sha256=_sha(old_claim_bytes),
    )
    manifest_path = (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking"
        / "promotions"
        / "action-3"
        / "promotion-1"
        / "promotion-manifest.json"
    )
    draft_path.write_text("operator edit", encoding="utf-8")
    with pytest.raises(SectionalRolloutError, match="draft changed"):
        rollback_sectional_promotion(manifest_path)


def test_rollback_restores_promoted_pair_when_manifest_write_fails(tmp_path, monkeypatch):
    from seo_ops.services import sectional_rollout as rollout

    assembly = _assembly()
    old_draft, old_ledger = _legacy_pair(assembly)
    report = build_shadow_comparison(
        action_id=3,
        topic=assembly["topic"],
        old_draft=old_draft,
        old_claim_ledger=old_ledger,
        new_assembly=assembly,
    )
    draft_path = tmp_path / "drafts" / "ceiling-marking.md"
    claim_path = tmp_path / "research" / "claim-ledger-ceiling-marking.json"
    draft_path.parent.mkdir(parents=True)
    claim_path.parent.mkdir(parents=True)
    old_draft_bytes = old_draft.encode("utf-8")
    old_claim_bytes = (json.dumps(old_ledger, sort_keys=True) + "\n").encode("utf-8")
    draft_path.write_bytes(old_draft_bytes)
    claim_path.write_bytes(old_claim_bytes)
    promote_sectional_assembly(
        workspace=tmp_path,
        slug="ceiling-marking",
        action_id=3,
        policy=build_rollout_policy("action", [3]),
        comparison=report,
        assembly=assembly,
        formal_draft_path=draft_path,
        formal_claim_path=claim_path,
        expected_draft_sha256=_sha(old_draft_bytes),
        expected_claim_sha256=_sha(old_claim_bytes),
    )
    promoted_draft = draft_path.read_bytes()
    promoted_claim = claim_path.read_bytes()
    manifest_path = (
        tmp_path
        / "drafts"
        / "sectional"
        / "ceiling-marking"
        / "promotions"
        / "action-3"
        / "promotion-1"
        / "promotion-manifest.json"
    )
    original_atomic_write = rollout._atomic_write

    def fail_rollback_manifest(path, payload):
        if Path(path) == manifest_path:
            raise OSError("simulated rollback manifest failure")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(rollout, "_atomic_write", fail_rollback_manifest)
    with pytest.raises(OSError, match="simulated rollback manifest"):
        rollback_sectional_promotion(manifest_path)
    assert draft_path.read_bytes() == promoted_draft
    assert claim_path.read_bytes() == promoted_claim
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "promoted"
