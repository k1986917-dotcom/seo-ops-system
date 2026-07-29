"""Verify the formal Hermes skill directory is complete and not just a copy.

The official seo-ops-orchestrator Hermes skill lives in
`integrations/hermes/seo-ops-orchestrator/` — its files must exist so
that `bash install.sh` from that directory actually installs something
discoverable. The tests/fixtures/legacy_pipeline/... copy is a historical
mirror kept for E2E fixtures; it is NOT the install source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "integrations" / "hermes" / "seo-ops-orchestrator"
SCRIPTS_DIR = SKILL_DIR / "scripts"

REQUIRED_FILES = ["SKILL.md", "README.md", "install.sh"]
REQUIRED_SCRIPTS = [
    "start.sh",
    "r0.sh",
    "r1.sh",
    "r3.sh",
    "w0.sh",
    "w1b.sh",
    "w2.sh",
    "w2-revise.sh",
    "w3.sh",
    "detect_stage.sh",
    "lib.sh",
]
STAGE_SCRIPTS = {"r0", "r1", "r3", "w0", "w1b", "w2", "w2-revise", "w3"}


def test_skill_directory_exists():
    assert SKILL_DIR.is_dir(), f"Missing skill dir: {SKILL_DIR}"


@pytest.mark.parametrize("filename", REQUIRED_FILES)
def test_required_top_level_files(filename):
    path = SKILL_DIR / filename
    assert path.is_file(), f"Missing {filename} in {SKILL_DIR}"


@pytest.mark.parametrize("filename", REQUIRED_SCRIPTS)
def test_required_scripts(filename):
    path = SCRIPTS_DIR / filename
    assert path.is_file(), f"Missing scripts/{filename}"


def test_all_seven_user_triggered_stage_scripts_present():
    """The 7 user-triggered stage scripts + helpers must all be present."""
    found = {p.stem for p in SCRIPTS_DIR.glob("*.sh")}
    missing = STAGE_SCRIPTS - found
    assert not missing, f"Missing stage scripts: {missing}"


def test_skill_md_references_hermes_metadata():
    """SKILL.md should declare name + description (Hermes requires frontmatter)."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "name:" in text, "SKILL.md missing 'name:' field"
    assert "description:" in text, "SKILL.md missing 'description:' field"


def test_install_sh_is_executable():
    import stat
    path = SKILL_DIR / "install.sh"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"install.sh not executable (mode={oct(mode)})"


def test_install_sh_references_correct_target_dir():
    """install.sh should install to ~/.hermes/skills/software-development/..."""
    text = (SKILL_DIR / "install.sh").read_text(encoding="utf-8")
    assert "software-development" in text, (
        "install.sh does not target software-development category"
    )
    assert "seo-ops-orchestrator" in text, (
        "install.sh does not reference seo-ops-orchestrator"
    )
