"""Generate Legacy workspace files from the database.

Before each Legacy session, these files must be fresh so old scripts
(running from the filesystem) have the latest article, product, and
GSC data.

Files generated:
  published/published-index.json  – article index with slugs, titles, tags, URLs
  published/{slug}.md             – per-article Markdown with frontmatter + body
  products/live_products_report.md – product table + detail sections
  context/internal-links-map.md   – site-wide link map
  context/seo-data-manual.md      – GSC keyword and quick-win tables
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seo_ops.db import connection as _db_connection

WORKSPACE_ROOT = Path("data/legacy_workflow/laserpointerhub")
SNAPSHOT_SQL = """(
    SELECT id FROM content_snapshots
    WHERE content_item_id = ci.id ORDER BY captured_at DESC, id DESC LIMIT 1
)"""


def _today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _to_fm(value: Any) -> str:
    s = str(value or "").replace("\\", "\\\\")
    if any(c in s for c in ":#{[\n") and not (s.startswith('"') and s.endswith('"')):
        return f'"{s}"'
    return s


# ── published/published-index.json ──────────────────────────────────────

def _gen_published_index(conn: sqlite3.Connection, workspace: Path) -> dict:
    cursor = conn.execute(f"""
        SELECT ci.slug, ci.title, ci.canonical_url, ci.source_updated_at,
               cs.metadata_json
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = {SNAPSHOT_SQL}
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    articles: list[dict[str, Any]] = []
    for slug, title, url, updated, meta_json in cursor.fetchall():
        tags: list[str] = []
        if meta_json:
            try:
                meta = json.loads(meta_json)
                raw = meta.get("tags") or []
                tags = [t.strip() for t in raw if isinstance(t, str) and t.strip()]
            except Exception:
                pass
        articles.append({
            "slug": slug, "title": title, "tags": tags, "url": url,
            "updated": updated, "link_counts": {"blog": 0, "product": 0},
        })
    out = workspace / "published" / "published-index.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"file": str(out), "count": len(articles)}


# ── published/{slug}.md ─────────────────────────────────────────────────

def _gen_published_articles(conn: sqlite3.Connection, workspace: Path) -> dict:
    cursor = conn.execute(f"""
        SELECT ci.slug, ci.title, ci.canonical_url,
               cs.body, cs.seo_title, cs.seo_description, cs.metadata_json, cs.summary
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = {SNAPSHOT_SQL}
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    d = workspace / "published"
    d.mkdir(parents=True, exist_ok=True)
    count = 0
    for slug, title, _url, body, seo_t, seo_d, meta_json, summary in cursor.fetchall():
        if not body:
            continue
        fm: list[str] = [f"Title: {_to_fm(title)}", f"Slug: {slug}"]
        if summary:
            fm.append(f"Summary: {_to_fm(summary)}")
        tags_str = ""
        seo_kw_str = ""
        if meta_json:
            try:
                m = json.loads(meta_json)
                tags = (m.get("tags") or [])
                tags_str = ", ".join(str(t).strip() for t in tags if t and str(t).strip())
                seo_kw_str = str(m.get("seoKeywords") or "").strip()
            except Exception:
                pass
        if tags_str:
            fm.append(f"Tags: {_to_fm(tags_str)}")
        if seo_t:
            fm.append(f"SEO Title: {_to_fm(seo_t)}")
        if seo_d:
            fm.append(f"SEO Description: {_to_fm(seo_d)}")
        if seo_kw_str:
            fm.append(f"SEO Keywords: {_to_fm(seo_kw_str)}")
        (d / f"{slug}.md").write_text(
            "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n", encoding="utf-8")
        count += 1
    return {"dir": str(d), "count": count}


# ── products/live_products_report.md ─────────────────────────────────────

def _gen_live_products(conn: sqlite3.Connection, workspace: Path) -> dict:
    cursor = conn.execute(f"""
        SELECT ci.slug, ci.title, ci.canonical_url,
               cs.metadata_json, cs.summary
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = {SNAPSHOT_SQL}
        WHERE ci.site_id = 1 AND ci.content_type = 'product' AND ci.status = 'active'
        ORDER BY ci.slug
    """)
    rows = cursor.fetchall()
    lines: list[str] = [
        "# Live Products Report — LaserPointerHub",
        f"> Auto-generated from database: {_today_str()}", "",
    ]
    if not rows:
        lines.append("*(no active products)*")
        out = workspace / "products" / "live_products_report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        return {"file": str(out), "count": 0}

    lines += ["| SKU | Title | URL | Price | Power | Wavelength |",
              "|---|---|---|---|---|---|"]
    for _s, title, url, meta_json, _summ in rows:
        sku = price = power = wavelength = ""
        if meta_json:
            try:
                m = json.loads(meta_json)
                sku = str(m.get("sku") or "")
                price = str(m.get("price") or "")
                power = str(m.get("internalPower") or "")
                attrs = m.get("attributes") or {}
                wavelength = str(attrs.get("wavelength") or "")
            except Exception:
                pass
        lines.append(f"| {sku} | {title} | {url} | {price} | {power} | {wavelength} |")

    lines += ["", "## Product Details", ""]
    for _slug, title, url, meta_json, summary in rows:
        sku = price = power = wavelength = ""
        features: list[str] = []
        if meta_json:
            try:
                m = json.loads(meta_json)
                sku = str(m.get("sku") or "")
                price = str(m.get("price") or "")
                power = str(m.get("internalPower") or "")
                attrs = m.get("attributes") or {}
                wavelength = str(attrs.get("wavelength") or "")
                features = [str(f) for f in (m.get("features") or [])]
            except Exception:
                pass
        lines.append(f"### {sku} — {title}")
        lines.append(f"- **URL**: {url}")
        if price:
            lines.append(f"- **Price**: ${price}")
        if power:
            lines.append(f"- **Power**: {power}")
        if wavelength:
            lines.append(f"- **Wavelength**: {wavelength}")
        if summary:
            lines.append(f"- **Summary**: {summary}")
        if features:
            lines.append(f"- **Features**: {'; '.join(features[:6])}")
        lines.append("")

    out = workspace / "products" / "live_products_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(out), "count": len(rows)}


# ── context/internal-links-map.md ────────────────────────────────────────

def _gen_internal_links_map(conn: sqlite3.Connection, workspace: Path) -> dict:
    blog_rows = conn.execute("""
        SELECT ci.slug, ci.title, ci.canonical_url
        FROM content_items ci
        WHERE ci.site_id = 1 AND ci.content_type = 'blog' AND ci.status = 'active'
        ORDER BY ci.slug
    """).fetchall()
    prod_rows = conn.execute(f"""
        SELECT ci.slug, ci.title, ci.canonical_url, cs.metadata_json
        FROM content_items ci
        LEFT JOIN content_snapshots cs ON cs.id = {SNAPSHOT_SQL}
        WHERE ci.site_id = 1 AND ci.content_type = 'product' AND ci.status = 'active'
        ORDER BY ci.slug
    """).fetchall()

    lines: list[str] = [
        "# Internal Links Map — LaserPointerHub",
        f"> Auto-generated from database: {_today_str()}", "",
        "## 产品列表", "",
    ]
    for _s, title, url, meta_json in prod_rows:
        sku = ""
        if meta_json:
            try:
                sku = str(json.loads(meta_json).get("sku") or "")
            except Exception:
                pass
        lines.append(f"- [{sku or title}]({url})")

    lines += ["", "## 已发布文章", "", "| # | Title | URL | Primary Keyword |",
              "|---|---|---|---|"]
    for i, (slug, title, url) in enumerate(blog_rows, 1):
        kw = slug.replace("-", " ")[:45]
        lines.append(f"| {i} | {(title or slug)[:60]} | {url} | {kw} |")

    lines += ["", "## 文章元数据", ""]
    for i, (slug, _title, url) in enumerate(blog_rows, 1):
        lines.append(f"### #{i} — {slug}")
        lines.append(f"- URL: {url}")
        lines.append(f"- 话题标签: {slug.replace('-', ' ')[:50]}")
        lines.append("- 内链文章: []")
        lines.append("- 内链产品: []")
        lines.append("- 状态: published")
        lines.append("")

    out = workspace / "context" / "internal-links-map.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(out), "blogs": len(blog_rows), "products": len(prod_rows)}


# ── context/seo-data-manual.md ───────────────────────────────────────────

def _gen_seo_data_manual(conn: sqlite3.Connection, workspace: Path) -> dict:
    imp_row = conn.execute("""
        SELECT id, imported_at FROM imports
        WHERE site_id = 1 AND source_type = 'gsc'
          AND analysis_active = 1 AND quality_eligible = 1
        ORDER BY imported_at DESC LIMIT 1
    """).fetchone()

    out = workspace / "context" / "seo-data-manual.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    if not imp_row:
        out.write_text("# SEO Data Manual\n\n> 无有效 GSC 数据批次\n", encoding="utf-8")
        return {"file": str(out), "gsc_rows": 0}

    import_id = imp_row[0]
    qrows = conn.execute("""
        SELECT dimension_value, SUM(impressions), AVG(ctr), AVG(position)
        FROM gsc_metrics
        WHERE import_id = ? AND dimension = 'query' AND period = 'current'
        GROUP BY dimension_value ORDER BY SUM(impressions) DESC LIMIT 20
    """, (import_id,)).fetchall()

    lines: list[str] = [
        "# SEO Data Manual — LaserPointerHub",
        f"> GSC OAuth 同步 · 批次 #{import_id} · {_today_str()}", "",
        "## A1. GSC Top 20 Queries", "",
        "| # | Keyword | Impressions | CTR | Position |",
        "|---|---:|---:|---:|",
    ]
    for i, (kw, imp, ctr, pos) in enumerate(qrows, 1):
        lines.append(
            f"| {i} | {kw} | {int(imp)} | "
            f"{f'{round(ctr * 100, 1)}%' if ctr else '—'} | "
            f"{f'{round(pos, 1)}' if pos else '—'} |")

    qw = [r for r in qrows if r[3] and 8 <= r[3] <= 20 and r[1] and r[1] > 100]
    lines += ["", "## A2. Quick Win Opportunities (Position 8–20, Impressions > 100)", ""]
    if qw:
        lines += ["| # | Keyword | Position | Impressions |", "|---|---:|---:|"]
        for i, (kw, imp, _c, pos) in enumerate(qw, 1):
            lines.append(f"| {i} | {kw} | {round(pos, 1)} | {int(imp)} |")
    else:
        lines.append("*(none)*")

    lines += ["", "## E1. GSC Measured Keywords ⭐⭐⭐", ""]
    for kw, _i, _c, _p in qrows[:15]:
        lines.append(f"- {kw}")

    lines += ["", "## E2. Human-Verified Keywords ⭐⭐", "",
              "*(Hand-fill with manually verified target keywords)*", ""]

    out.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(out), "gsc_rows": len(qrows)}


# ── Top-level sync ───────────────────────────────────────────────────────

def sync_all(workspace: Path | str | None = None) -> dict[str, Any]:
    if workspace is None:
        workspace = WORKSPACE_ROOT
    elif isinstance(workspace, str):
        workspace = Path(workspace)

    with _db_connection() as conn:
        return {
            "published_index": _gen_published_index(conn, workspace),
            "published_articles": _gen_published_articles(conn, workspace),
            "products": _gen_live_products(conn, workspace),
            "internal_links_map": _gen_internal_links_map(conn, workspace),
            "seo_data_manual": _gen_seo_data_manual(conn, workspace),
        }
