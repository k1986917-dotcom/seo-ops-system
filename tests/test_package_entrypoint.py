from __future__ import annotations

import subprocess
import sys


def test_installed_entrypoint_can_load_legacy_modules_outside_checkout(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from seo_ops.__main__ import _ensure_project_root_on_path; "
                "_ensure_project_root_on_path(); "
                "from data_sources.modules import seo_common; "
                "print(seo_common.__file__)"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "data_sources/modules/seo_common.py" in result.stdout
