"""Shared helpers for Legacy workflow tests.

Used by:
- tests/test_legacy_workflow.py
- tests/integration/test_hermes_orchestrator_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from seo_ops.config import Settings
from seo_ops.db import connection as db_connection

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "legacy_pipeline"
ACTION_SLUG = "example-topic-for-testing-the-legacy-pipeline"


async def ai_text(purpose: str, system: str, user: str, *, settings=None, max_tokens=None):
    """Deterministic async AI replacement.

    Mirrors the signature of seo_ops.services.legacy_workflow._run_ai_text so
    it can be monkeypatched in place of the real function (which is also async).
    Returns a small deterministic string that satisfies the AI caller's parser.
    """
    return _synthetic_ai_response(purpose)


def _synthetic_ai_response(purpose: str) -> str:
    """Return synthetic AI text that satisfies post-process parsers."""
    if purpose == "legacy_research_analyze":
        return (
            "Part 1\n\n# SEO Instructions\n\n"
            "Use the primary keyword naturally.\n\n"
            "Part 2\n\n"
            "## A. Pain Points\n- **[search] synthetic pain point**\n"
            "  - Source: [synthetic](https://example.com/blog/synthetic)\n"
            "  - Quote: \"synthetic user quote\"\n"
            "## C. Case Studies\n- **[search] synthetic case study**\n"
            "  - Source: [synthetic](https://example.com/blog/synthetic)\n"
            "  - Summary: synthetic case summary\n"
            "  - Use in article: synthetic illustration\n"
            "## E. Citations\n- **[search] W3C**\n"
            "  - Source: [W3C](https://www.w3.org/standards/)\n"
            "  - Key finding: synthetic finding\n"
            "  - Type: standards body\n"
            "## G. PAA\n- **[search] synthetic PAA question**\n"
            "  - Source: search AI\n"
            "  - Answer hint: synthetic answer hint\n\n"
            "Part 3\n\n## Quality gates\n- All checks pass.\n\n"
            "===BRIEF===\n"
            "## 1. SEO Foundation\n- Primary: synthetic\n"
            "## 2. Competitive Landscape\n- competitor A\n"
            "## 3. Recommended Outline\n- H2 A\n- H2 B\n"
            "## 4. Supporting Elements\n- data points\n"
            "## 5. Opportunity Score\n- 0.78\n"
            "## 6. Objectivity Checklist\n- [x] all good\n"
        )
    if purpose == "legacy_write_draft":
        return (
            "---\n"
            "Title: \"Synthetic Draft Title\"\n"
            "Slug: example-topic-for-testing-the-legacy-pipeline\n"
            "Author: TestAuthor\n"
            "Summary: Synthetic summary.\n"
            "Tags: tag1, tag2, tag3\n"
            "SEO Title: \"Synthetic SEO Title That Is Long Enough\"\n"
            "SEO Description: " + ("A" * 152) + "\n"
            "SEO Keywords: synthetic, test, fixture\n"
            "---\n\n"
            "# Synthetic Draft Title\n\n"
            "This is a synthetic draft used as a test fixture. " * 60
            + "\n\n"
            "> **Key Takeaways**\n"
            "> - Synthetic takeaway 1\n"
            "> - Synthetic takeaway 2\n"
            "> - Synthetic takeaway 3\n"
            "> - Synthetic takeaway 4\n"
            "> - Synthetic takeaway 5\n\n"
            "## Section A\n\nBody. " * 30 + "\n\n"
            "## Section B\n\nBody. " * 30 + "\n\n"
            "## Section C\n\nBody. " * 30 + "\n\n"
            "## Frequently Asked Questions\n\n"
            "### Q: synthetic question 1?\n\n"
            "A: synthetic answer 1.\n\n"
            "### Q: synthetic question 2?\n\n"
            "A: synthetic answer 2.\n\n"
            "### Q: synthetic question 3?\n\n"
            "A: synthetic answer 3.\n\n"
            '<script type="application/ld+json">\n'
            '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}\n'
            "</script>\n"
        )
    if purpose == "legacy_write_revise":
        return (
            "---\n"
            "Title: \"Synthetic Revised Title\"\n"
            "Slug: example-topic-for-testing-the-legacy-pipeline\n"
            "Author: TestAuthor\n"
            "Summary: Synthetic revised summary.\n"
            "Tags: tag1, tag2, tag3\n"
            "SEO Title: \"Synthetic Revised SEO Title Here\"\n"
            "SEO Description: " + ("B" * 152) + "\n"
            "SEO Keywords: synthetic, revised, fixture\n"
            "---\n\n"
            "# Synthetic Revised Title\n\n"
            "This is a revised synthetic draft. " * 60 + "\n\n"
            "> **Key Takeaways**\n"
            "> - Revised takeaway 1\n"
            "> - Revised takeaway 2\n"
            "> - Revised takeaway 3\n\n"
            "## Section A\n\nBody. " * 30 + "\n\n"
            "## Section B\n\nBody. " * 30 + "\n\n"
            "## Frequently Asked Questions\n\n"
            "### Q: revised question 1?\n\n"
            "A: revised answer 1.\n\n"
            "### Q: revised question 2?\n\n"
            "A: revised answer 2.\n\n"
            "### Q: revised question 3?\n\n"
            "A: revised answer 3.\n"
        )
    if purpose == "legacy_backlink_select":
        return (
            "→ 以下旧文章加回溯链接\n"
            "#1, Example Article One\n"
            "URL: https://example.com/blog/example-article-1\n"
            "锚文本: `[synthetic](https://example.com/blog/example-topic-for-testing-the-legacy-pipeline)`\n"
            "插入位置: Section 1 段落后\n"
        )
    return f"# synthetic {purpose} output\n"


def install_synthetic_workspace(workspace: Path) -> None:
    """Copy tests/fixtures/legacy_pipeline/ into a fresh workspace.

    Layout produced:
        <workspace>/
            context/                    (7 shared files, real format)
            published/                  (index + 1 article)
            products/                   (live_products_report.md)
            runs/action-N/current/examplesite/  (empty; created on R0)

    Safety: this function NEVER deletes an existing directory. It uses
    `shutil.copytree(..., dirs_exist_ok=True)` to overlay fixtures on top of
    whatever is there, and only writes files that exist in the fixtures.
    If the destination workspace already has files the fixtures do not, those
    files are left untouched. This protects production workspaces from
    accidental data loss if a test bug ever bypasses the temp_path isolation.

    The action directory is intentionally NOT created here; R0 creates it.
    """
    workspace.mkdir(parents=True, exist_ok=True)

    for sub in ("context", "published", "products"):
        src = FIXTURE_DIR / sub
        dst = workspace / sub
        dst.mkdir(parents=True, exist_ok=True)
        # Overlay fixture files onto destination; do not delete extras.
        for src_file in src.rglob("*"):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(src)
            dst_file = dst / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)


def read_legacy_stage_from_db(settings: Settings, action_id: int) -> str:
    """Read actions.legacy_stage (or None if the column is unset)."""
    with db_connection(settings) as conn:
        row = conn.execute(
            "SELECT legacy_stage FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
    return row[0] if row and row[0] else "r0_pending"


def shell_skill_script(name: str, *args: str, base_url: str | None = None) -> tuple[int, str]:
    """Run a seo-ops-orchestrator script; return (exit_code, stdout)."""
    script = FIXTURE_DIR / "skill_source" / "seo-ops-orchestrator" / "scripts" / name
    if not script.exists():
        raise FileNotFoundError(script)

    env = {"PATH": "/usr/bin:/bin"}
    if base_url:
        env["SEO_OPS_BASE_URL"] = base_url

    cmd = [str(script), *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=120
    )
    return result.returncode, (result.stdout + result.stderr).strip()


# Patch the async AI helper to be the sync one (test fixture).
def install_async_ai_patch(monkeypatch) -> None:
    """Make _run_ai_text return synthetic text in async context too."""

    async def fake_async_ai(purpose, system, user, **kwargs):
        return _synthetic_ai_response(purpose)

    monkeypatch.setattr(
        "seo_ops.services.legacy_workflow._run_ai_text", fake_async_ai
    )