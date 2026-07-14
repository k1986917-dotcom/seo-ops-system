from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from seo_ops.config import Settings


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    provider: str
    ok: bool
    message: str
    details: dict[str, Any]


SOURCE_CATALOG = [
    {
        "key": "gsc",
        "name": "Google Search Console",
        "role": "本站实际搜索表现",
        "proves": "本站在 Google 中的点击、展示、CTR、平均位置与维度变化",
        "does_not_prove": "查询级订单、未来流量或变化原因",
        "level": "A",
        "status": "已导入",
    },
    {
        "key": "cms",
        "name": "CMS 内容与产品",
        "role": "本站可执行资产",
        "proves": "现有文章、产品、正文、更新时间与可编辑目标",
        "does_not_prove": "搜索需求、排名难度或商业转化",
        "level": "A",
        "status": "已导入",
    },
    {
        "key": "serpapi",
        "name": "SerpAPI",
        "role": "SERP 与搜索外观验证",
        "proves": "指定国家、语言、设备下当时的结果页、竞争页面和 SERP 功能",
        "does_not_prove": "稳定搜索量、本站转化或内容质量",
        "level": "C",
        "status": "可配置",
    },
    {
        "key": "google_trends",
        "name": "Google Trends",
        "role": "相对热度与季节性",
        "proves": "同一口径下的相对兴趣、方向、地区差异与相关查询",
        "does_not_prove": "绝对搜索量；0 也不等于无人搜索",
        "level": "C",
        "status": "通过 SerpAPI 或 CSV",
    },
    {
        "key": "firecrawl",
        "name": "Firecrawl",
        "role": "指定页面内容采集",
        "proves": "被抓取页面当时公开写了什么、页面结构和可提取字段",
        "does_not_prove": "需求、排名、权威性或事实真实性",
        "level": "C",
        "status": "可配置",
    },
    {
        "key": "tavily",
        "name": "Tavily",
        "role": "资料发现与时效研究",
        "proves": "可供进一步核验的网页、来源和相关内容线索",
        "does_not_prove": "Google 排名、搜索量或来源本身可信",
        "level": "E→C",
        "status": "可配置",
    },
    {
        "key": "bing",
        "name": "Bing Webmaster Tools",
        "role": "第二搜索引擎与 AI 引用",
        "proves": "Bing 搜索表现及受支持 AI 场景中的引用、页面和抽样 grounding queries",
        "does_not_prove": "Google 表现、AI 答案中的权威排名或位置",
        "level": "A/B",
        "status": "后续接入",
    },
    {
        "key": "pagespeed",
        "name": "PageSpeed Insights / CrUX",
        "role": "页面体验诊断",
        "proves": "实验室诊断及有数据时的 28 天真实用户体验分布",
        "does_not_prove": "修复后必然提升排名或点击",
        "level": "B/C",
        "status": "可选",
    },
]


def configured_sources(settings: Settings) -> dict[str, bool]:
    return {
        "ai": settings.ai_enabled,
        "serpapi": bool(settings.serpapi_api_key),
        "firecrawl": bool(settings.firecrawl_api_key),
        "tavily": bool(settings.tavily_api_key),
    }


def _http_failure(provider: str, status_code: int) -> ConnectionResult:
    if status_code in {401, 403}:
        message = "凭据无效或没有权限"
    elif status_code == 429:
        message = "凭据有效，但账户额度或调用频率受限"
    else:
        message = f"供应商返回 HTTP {status_code}"
    return ConnectionResult(provider, False, message, {"http_status": status_code})


async def test_source_connection(
    provider: str,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionResult:
    """Use account/usage endpoints only; never spend a search or model token."""

    configured = configured_sources(settings)
    if provider not in configured:
        return ConnectionResult(provider, False, "不支持的连接类型", {})
    if not configured[provider]:
        return ConnectionResult(provider, False, "尚未配置完整", {})

    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            if provider == "serpapi":
                response = await client.get(
                    "https://serpapi.com/account.json",
                    params={"api_key": settings.serpapi_api_key},
                )
                if response.status_code != 200:
                    return _http_failure(provider, response.status_code)
                payload = response.json()
                details = {
                    "account_status": payload.get("account_status"),
                    "plan_name": payload.get("plan_name"),
                    "searches_left": payload.get("total_searches_left"),
                }
                return ConnectionResult(provider, True, "SerpAPI 已连接", details)

            if provider == "firecrawl":
                base = settings.firecrawl_base_url.rstrip("/")
                response = await client.get(
                    f"{base}/team/credit-usage",
                    headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                )
                if response.status_code != 200:
                    return _http_failure(provider, response.status_code)
                payload = response.json().get("data", {})
                details = {
                    "remaining_credits": payload.get("remainingCredits"),
                    "plan_credits": payload.get("planCredits"),
                }
                return ConnectionResult(provider, True, "Firecrawl 已连接", details)

            if provider == "tavily":
                base = settings.tavily_base_url.rstrip("/")
                response = await client.get(
                    f"{base}/usage",
                    headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
                )
                if response.status_code != 200:
                    return _http_failure(provider, response.status_code)
                payload = response.json()
                key_usage = payload.get("key", {})
                details = {
                    "usage": key_usage.get("usage"),
                    "limit": key_usage.get("limit"),
                }
                return ConnectionResult(provider, True, "Tavily 已连接", details)

            base = (settings.ai_base_url or "").rstrip("/")
            if base.endswith("/chat/completions"):
                base = base.removesuffix("/chat/completions")
            headers: dict[str, str] = {}
            if settings.ai_api_key:
                headers["Authorization"] = f"Bearer {settings.ai_api_key}"
            response = await client.get(f"{base}/models", headers=headers)
            if response.status_code != 200:
                return _http_failure(provider, response.status_code)
            return ConnectionResult(
                provider,
                True,
                "AI Provider 已连接",
                {"model": settings.ai_model, "provider": settings.ai_provider},
            )
    except (httpx.HTTPError, ValueError, TypeError):
        # Exception strings may contain a request URL. SerpAPI authenticates in the
        # query string, so returning the raw exception could leak a credential.
        return ConnectionResult(provider, False, "网络连接或响应解析失败", {})
