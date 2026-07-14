from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


@dataclass(frozen=True, slots=True)
class CMSItem:
    external_id: str
    content_type: str
    slug: str
    title: str
    canonical_url: str
    status: str
    summary: str | None
    body: str
    body_sha256: str
    seo_title: str | None
    seo_description: str | None
    source_created_at: str | None
    source_updated_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CMSParseResult:
    kind: str
    exported_at: str | None
    declared_count: int | None
    excluded_fields: list[str]
    items: list[CMSItem]


def _base_url(domain: str) -> str:
    domain = domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return f"{domain}/"


def _canonical(base: str, template: str, slug: str) -> str:
    path = template.format(slug=slug).lstrip("/")
    return urljoin(base, path)


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _product_body(item: dict[str, Any]) -> str:
    selected = {
        "description": item.get("description"),
        "features": item.get("features"),
        "packageList": item.get("packageList"),
        "attributes": item.get("attributes"),
        "translations": item.get("translations"),
    }
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, indent=2)


def parse_cms_export(
    content: bytes,
    *,
    domain: str,
    blog_path_template: str = "/blog/{slug}",
    product_path_template: str = "/products/{slug}",
) -> CMSParseResult:
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("CMS JSON 必须包含顶层 items 数组")

    raw_kind = str(payload.get("type") or "").strip().lower()
    if raw_kind in {"blogs", "blog", "articles"}:
        kind = "blog"
    elif raw_kind in {"products", "product"}:
        kind = "product"
    else:
        raise ValueError(f"不支持的 CMS 导出类型: {raw_kind or 'missing'}")

    base = _base_url(domain)
    parsed_items: list[CMSItem] = []
    for index, item in enumerate(payload["items"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"CMS items 第 {index} 项不是对象")
        external_id = str(item.get("id") or "").strip()
        slug = str(item.get("slug") or "").strip()
        title = str(item.get("title") or item.get("titleEn") or "").strip()
        if not external_id or not slug or not title:
            raise ValueError(f"CMS items 第 {index} 项缺少 id/slug/title")

        active = bool(item.get("active", True))
        status = "active" if active else "inactive"
        if kind == "blog":
            body = str(item.get("content") or "")
            summary = str(item.get("summary") or "") or None
            seo_title = str(item.get("seoTitle") or "") or None
            seo_description = str(item.get("seoDescription") or "") or None
            canonical_url = _canonical(base, blog_path_template, slug)
            metadata = {
                "pinned": item.get("pinned"),
                "tags": item.get("tags") or [],
                "seoKeywords": item.get("seoKeywords") or [],
                "publishedAt": item.get("publishedAt"),
                "active": active,
            }
        else:
            body = _product_body(item)
            summary = str(item.get("description") or "") or None
            seo_title = str(item.get("metaTitle") or "") or None
            seo_description = str(item.get("metaDescription") or "") or None
            canonical_url = _canonical(base, product_path_template, slug)
            metadata = {
                "sku": item.get("sku"),
                "titleEn": item.get("titleEn"),
                "categoryIds": item.get("categoryIds") or [],
                "price": item.get("price"),
                "inventory": item.get("inventory"),
                "attributes": item.get("attributes") or {},
                "features": item.get("features") or [],
                "packageList": item.get("packageList") or [],
                "keywords": item.get("keywords") or [],
                "searchKeywords": item.get("searchKeywords") or [],
                "internalPower": item.get("internalPower"),
                "active": active,
            }

        parsed_items.append(
            CMSItem(
                external_id=external_id,
                content_type=kind,
                slug=slug,
                title=title,
                canonical_url=canonical_url,
                status=status,
                summary=summary,
                body=body,
                body_sha256=_body_hash(body),
                seo_title=seo_title,
                seo_description=seo_description,
                source_created_at=item.get("createdAt"),
                source_updated_at=item.get("updatedAt"),
                metadata=metadata,
            )
        )

    declared_count = payload.get("count")
    if declared_count is not None and int(declared_count) != len(parsed_items):
        raise ValueError(
            f"CMS 声明 count={declared_count}，实际 items={len(parsed_items)}，为避免静默缺失已停止导入"
        )

    return CMSParseResult(
        kind=kind,
        exported_at=payload.get("exportedAt"),
        declared_count=int(declared_count) if declared_count is not None else None,
        excluded_fields=[str(value) for value in payload.get("excludedFields") or []],
        items=parsed_items,
    )
