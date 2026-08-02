from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_project_root_on_path() -> None:
    """Expose the repository-owned legacy modules to the installed entry point.

    ``seo-ops`` is installed from ``src/`` while the audited legacy scripts live
    in the repository-level ``data_sources/`` directory.  When the command is
    launched outside the checkout, Python otherwise sees ``seo_ops`` but not
    ``data_sources``.
    """
    project_root = Path(__file__).resolve().parents[2]
    legacy_modules = project_root / "data_sources" / "modules"
    if not legacy_modules.is_dir():
        return
    root = os.fspath(project_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _ensure_project_root_on_path()
    _load_local_env()
    import uvicorn

    from seo_ops.config import get_settings

    settings = get_settings()
    uvicorn.run("seo_ops.web.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
