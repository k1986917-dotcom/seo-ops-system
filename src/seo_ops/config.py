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
    serpapi_api_key: str | None = None
    serpapi_country: str = "us"
    serpapi_language: str = "en"
    serpapi_device: str = "desktop"
    serpapi_location: str | None = None
    firecrawl_api_key: str | None = None
    firecrawl_base_url: str = "https://api.firecrawl.dev/v2"
    tavily_api_key: str | None = None
    tavily_base_url: str = "https://api.tavily.com"
    trends_provider: str = "serpapi"
    trends_geo: str = "US"
    trends_timeframe: str = "today 12-m"

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
        serpapi_api_key=os.getenv("SEO_OPS_SERPAPI_KEY") or None,
        serpapi_country=os.getenv("SEO_OPS_SERPAPI_COUNTRY", "us").lower(),
        serpapi_language=os.getenv("SEO_OPS_SERPAPI_LANGUAGE", "en").lower(),
        serpapi_device=os.getenv("SEO_OPS_SERPAPI_DEVICE", "desktop").lower(),
        serpapi_location=os.getenv("SEO_OPS_SERPAPI_LOCATION") or None,
        firecrawl_api_key=os.getenv("SEO_OPS_FIRECRAWL_API_KEY") or None,
        firecrawl_base_url=os.getenv(
            "SEO_OPS_FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v2"
        ).rstrip("/"),
        tavily_api_key=os.getenv("SEO_OPS_TAVILY_API_KEY") or None,
        tavily_base_url=os.getenv("SEO_OPS_TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/"),
        trends_provider=os.getenv("SEO_OPS_TRENDS_PROVIDER", "serpapi"),
        trends_geo=os.getenv("SEO_OPS_TRENDS_GEO", "US").upper(),
        trends_timeframe=os.getenv("SEO_OPS_TRENDS_TIMEFRAME", "today 12-m"),
    )
    settings.ensure_directories()
    return settings
