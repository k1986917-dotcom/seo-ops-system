from __future__ import annotations

import os
from pathlib import Path


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
    _load_local_env()
    import uvicorn

    from seo_ops.config import get_settings

    settings = get_settings()
    uvicorn.run("seo_ops.web.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
