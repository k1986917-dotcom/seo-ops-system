from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seo_ops.config import Settings, get_settings
from seo_ops.db import connection


class WorkflowResetError(RuntimeError):
    """Raised when a derived snapshot cannot be cleared safely."""


@dataclass(frozen=True, slots=True)
class WorkflowResetResult:
    deleted: dict[str, int]
    preserved_imports: int
    preserved_content_items: int
    preserved_never_recommend: int
    deleted_snapshot_files: int


def _safe_derived_path(settings: Settings, stored_path: str) -> Path:
    path = Path(stored_path)
    if not path.is_absolute():
        path = settings.project_root / path
    resolved = path.resolve()
    root = settings.snapshots_dir.resolve()
    if not resolved.is_relative_to(root):
        raise WorkflowResetError("派生快照路径不在系统快照目录中，已停止清理")
    return resolved


def workflow_reset_preview(site_id: int, settings: Settings | None = None) -> dict[str, Any]:
    active = settings or get_settings()
    tables = (
        "analysis_runs",
        "opportunities",
        "actions",
        "research_runs",
        "research_seed_observations",
        "external_runs",
        "evidence_items",
        "ai_runs",
    )
    with connection(active) as conn:
        counts: dict[str, int] = {}
        for table in tables:
            counts[table] = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE site_id = ?", (site_id,)
                ).fetchone()[0]
            )
        counts["candidate_topic_nodes"] = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM topic_nodes
                WHERE site_id = ? AND node_type = 'candidate'
                """,
                (site_id,),
            ).fetchone()[0]
        )
        preserved = {
            "imports": int(
                conn.execute(
                    "SELECT COUNT(*) FROM imports WHERE site_id = ?", (site_id,)
                ).fetchone()[0]
            ),
            "content_items": int(
                conn.execute(
                    "SELECT COUNT(*) FROM content_items WHERE site_id = ?", (site_id,)
                ).fetchone()[0]
            ),
            "never_recommend": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM topic_decisions
                    WHERE site_id = ? AND decision = 'dont_recommend'
                    """,
                    (site_id,),
                ).fetchone()[0]
            ),
            "research_memory": int(
                conn.execute(
                    "SELECT COUNT(*) FROM topic_research_memory WHERE site_id = ?", (site_id,)
                ).fetchone()[0]
            ),
        }
        snapshot_paths = [
            str(row["response_snapshot_path"])
            for row in conn.execute(
                """
                SELECT response_snapshot_path FROM external_runs
                WHERE site_id = ? AND response_snapshot_path IS NOT NULL
                """,
                (site_id,),
            ).fetchall()
        ]
    return {
        "delete": counts,
        "preserve": preserved,
        "derived_snapshot_files": len(set(snapshot_paths)),
    }


def reset_workflow_state(site_id: int, settings: Settings | None = None) -> WorkflowResetResult:
    """Delete workflow-derived state while preserving source imports and decision memory."""

    active = settings or get_settings()
    preview = workflow_reset_preview(site_id, active)
    with connection(active) as conn:
        raw_paths = [
            str(row["response_snapshot_path"])
            for row in conn.execute(
                """
                SELECT response_snapshot_path FROM external_runs
                WHERE site_id = ? AND response_snapshot_path IS NOT NULL
                """,
                (site_id,),
            ).fetchall()
        ]
    paths = list(dict.fromkeys(_safe_derived_path(active, value) for value in raw_paths))

    with connection(active) as conn:
        conn.execute("DELETE FROM ai_runs WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM research_runs WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM external_runs WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM actions WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM opportunities WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM analysis_runs WHERE site_id = ?", (site_id,))
        conn.execute(
            """
            DELETE FROM topic_decisions
            WHERE site_id = ? AND decision != 'dont_recommend'
            """,
            (site_id,),
        )
        candidate_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM topic_nodes
                WHERE site_id = ? AND node_type = 'candidate'
                """,
                (site_id,),
            ).fetchall()
        ]
        if candidate_ids:
            marks = ",".join("?" for _ in candidate_ids)
            conn.execute(
                f"UPDATE topic_decisions SET topic_id = NULL WHERE topic_id IN ({marks})",
                candidate_ids,
            )
            conn.execute(
                f"DELETE FROM topic_nodes WHERE id IN ({marks})",
                candidate_ids,
            )

    deleted_files = 0
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                deleted_files += 1
        except OSError as exc:
            raise WorkflowResetError(f"派生快照未能删除：{path.name}") from exc

    return WorkflowResetResult(
        deleted=dict(preview["delete"]),
        preserved_imports=int(preview["preserve"]["imports"]),
        preserved_content_items=int(preview["preserve"]["content_items"]),
        preserved_never_recommend=int(preview["preserve"]["never_recommend"]),
        deleted_snapshot_files=deleted_files,
    )
