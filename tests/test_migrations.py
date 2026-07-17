from __future__ import annotations

import sqlite3
from dataclasses import replace

from seo_ops.db import MIGRATION_2, SCHEMA, init_db


def test_v2_database_migrates_without_losing_actions(settings, tmp_path):
    legacy_path = tmp_path / "legacy-v2.db"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO sites(id, slug, name, domain, created_at, updated_at)
        VALUES(1, 'legacy', 'Legacy', 'example.com', '2026-07-14T00:00:00+00:00',
               '2026-07-14T00:00:00+00:00')
        """
    )
    for decision in ("accepted", "rejected"):
        conn.execute(
            """
            INSERT INTO actions(
                site_id, action_type, target_ref, decision, planned_change,
                baseline_json, decided_at
            ) VALUES(1, 'evidence', ?, ?, 'legacy plan', '{}',
                     '2026-07-14T00:00:00+00:00')
            """,
            (decision, decision),
        )
    conn.execute("PRAGMA user_version = 1")
    conn.executescript(MIGRATION_2)
    conn.commit()
    conn.close()

    migrated_settings = replace(settings, database_path=legacy_path)
    init_db(migrated_settings)

    conn = sqlite3.connect(legacy_path)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    rows = conn.execute(
        "SELECT decision, workflow_status, plan_version FROM actions ORDER BY id"
    ).fetchall()
    assert [row["decision"] for row in rows] == ["accepted", "rejected"]
    assert rows[0]["workflow_status"] == "planned"
    assert rows[0]["plan_version"] == "legacy-unplanned"
    assert rows[1]["workflow_status"] == "cancelled"
    for table in (
        "external_runs",
        "evidence_items",
        "action_steps",
        "research_runs",
        "research_run_items",
        "research_candidates",
        "topic_nodes",
        "topic_aliases",
        "topic_relations",
        "topic_content_links",
        "topic_decisions",
        "topic_research_memory",
        "gsc_connections",
        "gsc_sync_runs",
        "gsc_query_page_metrics",
    ):
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    conn.close()


def test_v4_database_migration_activates_and_trusts_only_latest_gsc_import(settings, tmp_path):
    legacy_path = tmp_path / "legacy-v4.db"
    legacy_settings = replace(settings, database_path=legacy_path)
    init_db(legacy_settings)

    conn = sqlite3.connect(legacy_path)
    conn.execute("DROP INDEX idx_imports_active_gsc")
    conn.execute("ALTER TABLE imports DROP COLUMN analysis_active")
    for name, sha256, imported_at in (
        ("old.xlsx", "a" * 64, "2026-07-13T00:00:00+00:00"),
        ("new.xlsx", "b" * 64, "2026-07-14T00:00:00+00:00"),
    ):
        conn.execute(
            """
            INSERT INTO imports(
                site_id, source_type, original_name, sha256, snapshot_path,
                status, row_count, metadata_json, imported_at, completed_at
            ) VALUES(1, 'gsc', ?, ?, ?, 'success', 1, '{}', ?, ?)
            """,
            (name, sha256, f"data/snapshots/{name}", imported_at, imported_at),
        )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    init_db(legacy_settings)

    conn = sqlite3.connect(legacy_path)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    rows = conn.execute(
        """
        SELECT original_name, analysis_active, quality_eligible
        FROM imports WHERE source_type = 'gsc'
        ORDER BY imported_at
        """
    ).fetchall()
    assert [
        (row["original_name"], row["analysis_active"], row["quality_eligible"]) for row in rows
    ] == [
        ("old.xlsx", 0, 0),
        ("new.xlsx", 1, 1),
    ]
    assert (
        conn.execute(
            """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_imports_active_gsc'
        """
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            """
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_imports_quality_gsc'
        """
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_init_db_deprecates_prior_active_rule_version(settings):
    conn = sqlite3.connect(settings.database_path)
    conn.execute(
        """
        INSERT INTO rule_versions(
            rule_key, version, rule_type, status, evidence_level, rationale,
            config_json, source_urls_json, known_failures_json, valid_from, review_after
        ) VALUES(
            'multi_source_topic_research', '0.4.9', 'evidence_discovery', 'active',
            'E', 'old rule', '{}', '[]', '[]', '2026-07-14', '2026-08-14'
        )
        """
    )
    conn.commit()
    conn.close()

    init_db(settings)

    conn = sqlite3.connect(settings.database_path)
    statuses = dict(
        conn.execute(
            """
            SELECT version, status FROM rule_versions
            WHERE rule_key = 'multi_source_topic_research'
            """
        ).fetchall()
    )
    conn.close()

    assert statuses["0.4.9"] == "deprecated"
    assert statuses["0.6.1"] == "active"
