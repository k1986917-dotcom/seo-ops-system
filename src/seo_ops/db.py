from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from seo_ops.config import Settings, get_settings
from seo_ops.utils import json_dumps, utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    blog_path_template TEXT NOT NULL DEFAULT '/blog/{slug}',
    product_path_template TEXT NOT NULL DEFAULT '/products/{slug}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    original_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('processing','success','failed')),
    row_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    imported_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(site_id, source_type, sha256)
);

CREATE TABLE IF NOT EXISTS gsc_metrics (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    dimension_value TEXT NOT NULL,
    period TEXT NOT NULL CHECK(period IN ('current','previous')),
    clicks REAL,
    impressions REAL,
    ctr REAL,
    position REAL,
    sheet_name TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    UNIQUE(import_id, dimension, dimension_value, period)
);

CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL CHECK(content_type IN ('blog','product')),
    external_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    status TEXT NOT NULL,
    source_created_at TEXT,
    source_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(site_id, content_type, external_id)
);

CREATE TABLE IF NOT EXISTS content_snapshots (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    content_item_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    seo_title TEXT,
    seo_description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL,
    UNIQUE(import_id, content_item_id)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    method_version TEXT NOT NULL,
    source_import_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    rule_key TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    gate_status TEXT NOT NULL CHECK(gate_status IN ('passed','needs_evidence','blocked')),
    gate_reasons_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL,
    strength REAL NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    confidence_weight REAL NOT NULL,
    effort REAL NOT NULL,
    priority REAL NOT NULL,
    method_version TEXT NOT NULL,
    portfolio_slot TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','accepted','rejected','in_progress','done','cancelled')),
    ai_explanation_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected','manual')),
    decision_reason TEXT,
    planned_change TEXT,
    actual_change TEXT,
    baseline_json TEXT NOT NULL DEFAULT '{}',
    decided_at TEXT NOT NULL,
    executed_at TEXT,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY,
    action_id INTEGER NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
    window_days INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    confounders_json TEXT NOT NULL DEFAULT '[]',
    interpretation TEXT,
    UNIQUE(action_id, window_days)
);

CREATE TABLE IF NOT EXISTS rule_versions (
    id INTEGER PRIMARY KEY,
    rule_key TEXT NOT NULL,
    version TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','active','deprecated')),
    evidence_level TEXT NOT NULL,
    rationale TEXT NOT NULL,
    config_json TEXT NOT NULL,
    source_urls_json TEXT NOT NULL,
    known_failures_json TEXT NOT NULL DEFAULT '[]',
    valid_from TEXT NOT NULL,
    review_after TEXT NOT NULL,
    UNIQUE(rule_key, version)
);

CREATE TABLE IF NOT EXISTS ai_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    input_refs_json TEXT NOT NULL,
    output_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('success','failed')),
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_imports_site_date ON imports(site_id, imported_at DESC);
CREATE INDEX IF NOT EXISTS idx_gsc_import_dimension ON gsc_metrics(import_id, dimension, period);
CREATE INDEX IF NOT EXISTS idx_content_site_type ON content_items(site_id, content_type);
CREATE INDEX IF NOT EXISTS idx_analysis_site_date ON analysis_runs(site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_run_priority ON opportunities(analysis_run_id, priority DESC);

PRAGMA user_version = 1;
"""


DEFAULT_RULES = [
    {
        "rule_key": "protect_click_loss",
        "version": "0.1.0",
        "rule_type": "heuristic",
        "evidence_level": "A",
        "rationale": "优先检查已有页面的明确点击损失；阈值为冷启动值，后续按站点历史校准。",
        "config": {
            "min_previous_clicks": 3,
            "min_previous_impressions": 50,
            "max_current_ratio": 0.7,
        },
        "sources": [
            "https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops"
        ],
        "known_failures": ["季节性变化", "品牌词变化", "算法更新", "导出窗口不完整"],
    },
    {
        "rule_key": "site_relative_ctr",
        "version": "0.1.0",
        "rule_type": "site_empirical",
        "evidence_level": "A",
        "rationale": "使用本站同排名区间页面 CTR 中位数，避免套用统一行业 CTR 表。",
        "config": {"min_impressions": 100, "baseline_ratio": 0.5, "min_bucket_size": 3},
        "sources": ["https://support.google.com/webmasters/answer/7042828?hl=en"],
        "known_failures": ["页面混合多个查询意图", "设备/国家结构变化", "AI 搜索外观变化"],
    },
    {
        "rule_key": "striking_distance_page",
        "version": "0.1.0",
        "rule_type": "heuristic",
        "evidence_level": "A",
        "rationale": "对已有且可编辑页面识别 8–20 位的站内机会；不因此自动创建新文章。",
        "config": {"min_impressions": 50, "position_min": 8, "position_max": 20},
        "sources": ["https://support.google.com/webmasters/answer/7042828?hl=en"],
        "known_failures": ["平均排名掩盖查询差异", "查询—页面联合关系缺失"],
    },
    {
        "rule_key": "query_page_evidence_gap",
        "version": "0.1.0",
        "rule_type": "official_fact",
        "evidence_level": "B",
        "rationale": "普通 GSC 查询表和网页表不能被当作联合维度，先补证据再决定新建或更新。",
        "config": {"min_impressions": 100, "max_candidates": 5},
        "sources": ["https://support.google.com/webmasters/answer/7042828?hl=en"],
        "known_failures": [],
    },
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    database_path = path or settings.database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connection(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    active_settings = settings or get_settings()
    conn = connect(active_settings.database_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    with connection(active_settings) as conn:
        conn.executescript(SCHEMA)
        now = utc_now()
        conn.execute(
            """
            INSERT INTO sites(slug, name, domain, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO NOTHING
            """,
            ("laserpointerhub", "LaserPointerHub", "laserpointerhub.com", now, now),
        )
        for rule in DEFAULT_RULES:
            conn.execute(
                """
                INSERT INTO rule_versions(
                    rule_key, version, rule_type, status, evidence_level, rationale,
                    config_json, source_urls_json, known_failures_json, valid_from, review_after
                ) VALUES(?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_key, version) DO NOTHING
                """,
                (
                    rule["rule_key"],
                    rule["version"],
                    rule["rule_type"],
                    rule["evidence_level"],
                    rule["rationale"],
                    json_dumps(rule["config"]),
                    json_dumps(rule["sources"]),
                    json_dumps(rule["known_failures"]),
                    "2026-07-14",
                    "2026-10-14",
                ),
            )
