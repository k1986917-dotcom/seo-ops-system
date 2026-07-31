import json

import pytest

from tools import sectional_rollout_control as control


def test_decision_command_prints_fail_closed_default(capsys, settings, monkeypatch):
    monkeypatch.setattr(control, "get_settings", lambda: settings)
    assert control.main(["decision", "--action-id", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "off"
    assert payload["promotion_allowed"] is False


def test_rollback_requires_exact_confirmation(tmp_path):
    with pytest.raises(SystemExit, match="exactly ROLLBACK"):
        control.main(
            [
                "rollback",
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--confirm",
                "yes",
            ]
        )


def test_rollback_command_prints_manifest(tmp_path, capsys, monkeypatch):
    manifest = tmp_path / "promotion-manifest.json"
    monkeypatch.setattr(
        control,
        "rollback_sectional_promotion",
        lambda path: {"status": "rolled_back", "path": str(path)},
    )
    assert control.main(
        [
            "rollback",
            "--manifest",
            str(manifest),
            "--confirm",
            "ROLLBACK",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"path": str(manifest), "status": "rolled_back"}
