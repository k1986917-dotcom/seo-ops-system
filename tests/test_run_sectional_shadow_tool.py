import argparse
import hashlib
import json
from pathlib import Path

import pytest

from tools import run_sectional_shadow as runner


def test_child_environment_is_shadow_only(monkeypatch):
    monkeypatch.setenv("SEO_OPS_SECTIONAL_WRITING_MODE", "off")
    monkeypatch.setenv("SEO_OPS_SECTIONAL_ACTION_ALLOWLIST", "3")

    child = runner._child_env(8791, 24)

    assert child["SEO_OPS_SECTIONAL_WRITING_MODE"] == "shadow"
    assert child["SEO_OPS_SECTIONAL_ACTION_ALLOWLIST"] == ""
    assert child["SEO_OPS_SECTIONAL_AI_CALL_LIMIT"] == "24"
    assert child["SEO_OPS_PORT"] == "8791"


def test_post_once_parses_redirect_without_retry(monkeypatch):
    class Response:
        status = 303

        @staticmethod
        def getheaders():
            return [
                (
                    "Location",
                    "/actions?message=%E7%AB%A0%E8%8A%82%E5%8C%96+shadow+%E5%B7%B2%E5%AE%8C%E6%88%90&level=success",
                )
            ]

        @staticmethod
        def read():
            return b""

    class Connection:
        def __init__(self, *args, **kwargs):
            self.requests = 0

        def request(self, *args, **kwargs):
            self.requests += 1

        def getresponse(self):
            assert self.requests == 1
            return Response()

        def close(self):
            return None

    monkeypatch.setattr(runner.http.client, "HTTPConnection", Connection)

    result = runner._post_once(3, "LaserPointerHub", 8791, 30)

    assert result["status"] == 303
    assert result["level"] == "success"
    assert "shadow 已完成" in result["message"]
    assert result["post_attempts"] == 1


def test_formal_hashes_cover_pair_state_and_env(tmp_path):
    paths = {}
    for name in ("draft", "claim", "state"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        paths[name] = path
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=hidden\n", encoding="utf-8")

    hashes = runner._formal_hashes(paths, env_file)

    assert set(hashes) == {"draft", "claim", "state", "env"}
    assert all(len(value) == 64 for value in hashes.values())


def test_paths_refuses_missing_formal_draft(tmp_path):
    with pytest.raises(runner.ShadowRunnerError, match="formal Legacy draft"):
        runner._paths(tmp_path, 3, "Example Topic")


def test_slugify_matches_legacy_canonical_rule():
    assert runner._slugify("Laser Pointer: Above Ceilings") == ("laser-pointer-above-ceilings")
    assert runner._slugify("中文") == "topic-72726d8818f6"


def test_invalid_action_id_fails_before_starting_server(monkeypatch):
    args = argparse.Namespace(
        action_id=0,
        author="LaserPointerHub",
        port=8791,
        ai_call_limit=24,
        startup_timeout=60,
        request_timeout=3600,
        resume_existing=False,
        log_file=Path("/tmp/unused.log"),
    )
    with pytest.raises(runner.ShadowRunnerError, match="positive"):
        runner.run(args)


def test_health_payload_requires_ai_enabled():
    assert runner._health_is_ready({"status": "ok", "ai_enabled": True}) is True
    assert runner._health_is_ready({"status": "ok", "ai_enabled": False}) is False


def test_resumable_root_accepts_known_checkpoint_files(tmp_path):
    root = tmp_path / "sectional-root"
    checkpoint = root / "checkpoints" / "section-901f779818.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")

    assert runner._validate_resumable_root(root, 3) == ["checkpoints/section-901f779818.json"]


def test_resumable_root_rejects_complete_or_unknown_artifacts(tmp_path):
    root = tmp_path / "sectional-root"
    root.mkdir()
    (root / "assembled-draft.md").write_text("draft", encoding="utf-8")

    with pytest.raises(runner.ShadowRunnerError, match="complete or promotion"):
        runner._validate_resumable_root(root, 3)

    (root / "assembled-draft.md").unlink()
    (root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(runner.ShadowRunnerError, match="unknown resume artifacts"):
        runner._validate_resumable_root(root, 3)


def _write_complete_candidate(root: Path, action_id: int = 3) -> dict[str, bytes]:
    payloads = {
        "resolved-delivery.json": b'{"version": 1}\n',
        "assembled-draft.md": b"# Reviewed candidate\n",
        "assembled-claim-ledger.json": b'{"version": 1, "claims": []}\n',
        "assembly-report.json": b'{"version": 1, "blockers": []}\n',
        f"shadow-comparison-action-{action_id}.json": b'{"recommendation": "eligible"}\n',
    }
    root.mkdir(parents=True)
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)
    checkpoint = root / "checkpoints" / "section-901f779818.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text("{}\n", encoding="utf-8")
    return payloads


def test_refresh_complete_archives_terminal_candidate_and_keeps_checkpoints(tmp_path):
    root = tmp_path / "drafts" / "sectional" / "guide"
    payloads = _write_complete_candidate(root)

    archived = runner._archive_complete_candidate(root, 3, "a" * 40)

    assert runner._validate_resumable_root(root, 3) == ["checkpoints/section-901f779818.json"]
    archive = Path(archived["archive_path"])
    assert archive.parent == tmp_path / "drafts" / "sectional-archive" / "guide"
    for name, payload in payloads.items():
        assert not (root / name).exists()
        assert (archive / name).read_bytes() == payload
        assert archived["artifacts"][name] == hashlib.sha256(payload).hexdigest()
    manifest = json.loads((archive / "archive-manifest.json").read_text(encoding="utf-8"))
    assert manifest["action_id"] == 3
    assert manifest["git_head"] == "a" * 40
    assert manifest["artifacts"] == archived["artifacts"]
    assert len(archived["manifest_sha256"]) == 64


def test_refresh_complete_refuses_promotion_manifest_without_moving_files(tmp_path):
    root = tmp_path / "drafts" / "sectional" / "guide"
    payloads = _write_complete_candidate(root)
    (root / "promotion-manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(runner.ShadowRunnerError, match="promotion manifest"):
        runner._archive_complete_candidate(root, 3, "b" * 40)

    for name, payload in payloads.items():
        assert (root / name).read_bytes() == payload


def test_refresh_complete_refuses_incomplete_terminal_set_without_moving_files(tmp_path):
    root = tmp_path / "drafts" / "sectional" / "guide"
    payloads = _write_complete_candidate(root)
    missing = root / "assembly-report.json"
    missing.unlink()

    with pytest.raises(runner.ShadowRunnerError, match="incomplete terminal candidate"):
        runner._archive_complete_candidate(root, 3, "c" * 40)

    for name, payload in payloads.items():
        if name != "assembly-report.json":
            assert (root / name).read_bytes() == payload


def test_refresh_complete_rolls_back_if_archive_move_fails(tmp_path, monkeypatch):
    root = tmp_path / "drafts" / "sectional" / "guide"
    payloads = _write_complete_candidate(root)
    real_replace = runner.os.replace
    calls = 0

    def fail_second_move(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated archive failure")
        return real_replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_second_move)

    with pytest.raises(runner.ShadowRunnerError, match="active files restored"):
        runner._archive_complete_candidate(root, 3, "d" * 40)

    for name, payload in payloads.items():
        assert (root / name).read_bytes() == payload


def test_refresh_complete_preserves_recovery_files_if_rollback_also_fails(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "drafts" / "sectional" / "guide"
    payloads = _write_complete_candidate(root)
    real_replace = runner.os.replace
    calls = 0

    def fail_archive_and_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError(f"simulated failure {calls}")
        return real_replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_archive_and_rollback)

    with pytest.raises(runner.ShadowRunnerError, match="rollback was incomplete"):
        runner._archive_complete_candidate(root, 3, "e" * 40)

    recovery_dirs = list((tmp_path / "drafts" / "sectional-archive" / "guide").glob(".tmp-*"))
    assert len(recovery_dirs) == 1
    preserved = recovery_dirs[0] / "assembled-claim-ledger.json"
    assert preserved.read_bytes() == payloads["assembled-claim-ledger.json"]
    assert not (root / "assembled-claim-ledger.json").exists()
    for name, payload in payloads.items():
        if name != "assembled-claim-ledger.json":
            assert (root / name).read_bytes() == payload
