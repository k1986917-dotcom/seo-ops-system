from __future__ import annotations

from pathlib import Path

import pytest

from seo_ops.config import Settings
from seo_ops.db import init_db


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    value = Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_path=data_dir / "test.db",
        snapshots_dir=data_dir / "snapshots",
        host="127.0.0.1",
        port=8787,
        timezone="Asia/Shanghai",
        ai_provider="openai-compatible",
        ai_base_url=None,
        ai_api_key=None,
        ai_model=None,
    )
    init_db(value)
    return value
