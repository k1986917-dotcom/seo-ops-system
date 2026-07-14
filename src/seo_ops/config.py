from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    snapshots_dir: Path
    host: str
    port: int
    timezone: str
    ai_provider: str
    ai_base_url: str | None
    ai_api_key: str | None
    ai_model: str | None

    @property
    def ai_enabled(self) -> bool:
        # Local OpenAI-compatible services may not require an API key.
        return bool(self.ai_base_url and self.ai_model)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.getenv("SEO_OPS_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        database_path=data_dir / "seo_ops.db",
        snapshots_dir=data_dir / "snapshots",
        host=os.getenv("SEO_OPS_HOST", "127.0.0.1"),
        port=int(os.getenv("SEO_OPS_PORT", "8787")),
        timezone=os.getenv("SEO_OPS_TIMEZONE", "Asia/Shanghai"),
        ai_provider=os.getenv("SEO_OPS_AI_PROVIDER", "openai-compatible"),
        ai_base_url=os.getenv("SEO_OPS_AI_BASE_URL") or None,
        ai_api_key=os.getenv("SEO_OPS_AI_API_KEY") or None,
        ai_model=os.getenv("SEO_OPS_AI_MODEL") or None,
    )
    settings.ensure_directories()
    return settings
