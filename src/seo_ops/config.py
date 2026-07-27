from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESEARCH_BUDGET_LIMITS = {
    "serpapi": 10,
    "firecrawl": 10,
    "tavily": 20,
    "ai": 20,
}

CONTENT_AI_CALL_LIMIT_MIN = 3
CONTENT_AI_CALL_LIMIT_MAX = 10
CONTENT_AI_CALL_LIMIT_DEFAULT = 4
GSC_TRUSTED_START_DATE_DEFAULT = "2026-06-22"


def _bounded_env_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(0, min(maximum, value))


def _optional_env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _gsc_trusted_start_date() -> str:
    raw = os.getenv("SEO_OPS_GSC_TRUSTED_START_DATE", GSC_TRUSTED_START_DATE_DEFAULT).strip()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return GSC_TRUSTED_START_DATE_DEFAULT


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
    research_serpapi_budget: int = 2
    research_firecrawl_budget: int = 7
    research_tavily_budget: int = 17
    research_ai_budget: int = 13
    content_ai_call_limit: int = CONTENT_AI_CALL_LIMIT_DEFAULT
    gsc_oauth_client_file: Path | None = None
    gsc_oauth_token_file: Path | None = None
    gsc_trusted_start_date: str = GSC_TRUSTED_START_DATE_DEFAULT
    gsc_redirect_uri: str = "http://127.0.0.1:8787"

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
    port = int(os.getenv("SEO_OPS_PORT", "8787"))
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        database_path=data_dir / "seo_ops.db",
        snapshots_dir=data_dir / "snapshots",
        host=os.getenv("SEO_OPS_HOST", "127.0.0.1"),
        port=port,
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
        research_serpapi_budget=_bounded_env_int(
            "SEO_OPS_RESEARCH_SERPAPI_BUDGET", 2, RESEARCH_BUDGET_LIMITS["serpapi"]
        ),
        research_firecrawl_budget=_bounded_env_int(
            "SEO_OPS_RESEARCH_FIRECRAWL_BUDGET", 7, RESEARCH_BUDGET_LIMITS["firecrawl"]
        ),
        research_tavily_budget=_bounded_env_int(
            "SEO_OPS_RESEARCH_TAVILY_BUDGET", 17, RESEARCH_BUDGET_LIMITS["tavily"]
        ),
        research_ai_budget=_bounded_env_int(
            "SEO_OPS_RESEARCH_AI_BUDGET", 13, RESEARCH_BUDGET_LIMITS["ai"]
        ),
        content_ai_call_limit=max(
            CONTENT_AI_CALL_LIMIT_MIN,
            _bounded_env_int(
                "SEO_OPS_CONTENT_AI_CALL_LIMIT",
                CONTENT_AI_CALL_LIMIT_DEFAULT,
                CONTENT_AI_CALL_LIMIT_MAX,
            ),
        ),
        gsc_oauth_client_file=_optional_env_path("SEO_OPS_GSC_OAUTH_CLIENT_FILE"),
        gsc_oauth_token_file=_optional_env_path("SEO_OPS_GSC_OAUTH_TOKEN_FILE"),
        gsc_trusted_start_date=_gsc_trusted_start_date(),
        gsc_redirect_uri=os.getenv("SEO_OPS_GSC_REDIRECT_URI", f"http://127.0.0.1:{port}").rstrip(
            "/"
        ),
    )
    settings.ensure_directories()
    return settings
