from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from seo_ops.config import Settings, get_settings
from seo_ops.rules.content_workflow import OLD_ARTICLE_CONTENT_QUALITY_RULE
from seo_ops.rules.evidence_workflow import (
    EXTERNAL_QUERY_REVIEW_RULE,
    NEW_ARTICLE_CANDIDATE_RULE,
)
from seo_ops.rules.gsc_workflow import (
    GSC_QUERY_PAGE_SYNC_RULE,
    OLD_ARTICLE_GSC_READINESS_RULE,
)
from seo_ops.rules.research_workflow import MULTI_SOURCE_TOPIC_RESEARCH_RULE
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
    analysis_active INTEGER NOT NULL DEFAULT 1 CHECK(analysis_active IN (0,1)),
    quality_eligible INTEGER NOT NULL DEFAULT 1 CHECK(quality_eligible IN (0,1)),
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
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS source_connections (
    provider TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('connected','error')),
    last_checked_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT
);

PRAGMA user_version = 2;
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS external_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    purpose TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    input_refs_json TEXT NOT NULL DEFAULT '[]',
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
    response_snapshot_path TEXT,
    response_sha256 TEXT,
    response_size INTEGER,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    external_run_id INTEGER NOT NULL REFERENCES external_runs(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    source_ref TEXT,
    payload_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL DEFAULT '[]',
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_steps (
    id INTEGER PRIMARY KEY,
    action_id INTEGER NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    step_key TEXT NOT NULL,
    phase TEXT NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done')),
    requires_human INTEGER NOT NULL DEFAULT 1 CHECK(requires_human IN (0,1)),
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(action_id, step_key),
    UNIQUE(action_id, step_order)
);

CREATE INDEX IF NOT EXISTS idx_external_runs_request
    ON external_runs(site_id, provider, purpose, request_sha256, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_runs_opportunity
    ON external_runs(opportunity_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_external_run
    ON evidence_items(external_run_id);
CREATE INDEX IF NOT EXISTS idx_actions_site_workflow
    ON actions(site_id, workflow_status, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_steps_action_order
    ON action_steps(action_id, step_order);

UPDATE actions SET workflow_status = 'cancelled', completed_at = COALESCE(completed_at, decided_at)
WHERE decision = 'rejected';
UPDATE actions SET plan_version = COALESCE(plan_version, 'legacy-unplanned')
WHERE decision = 'accepted';

PRAGMA user_version = 3;
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES analysis_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
    budgets_json TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    seed_queries_json TEXT NOT NULL DEFAULT '[]',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    ai_model TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS research_run_items (
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    external_run_id INTEGER NOT NULL REFERENCES external_runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    purpose TEXT NOT NULL,
    reused INTEGER NOT NULL DEFAULT 0 CHECK(reused IN (0,1)),
    PRIMARY KEY(research_run_id, external_run_id)
);

CREATE TABLE IF NOT EXISTS research_candidates (
    id INTEGER PRIMARY KEY,
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    normalized_topic TEXT NOT NULL,
    intent TEXT,
    rationale TEXT NOT NULL,
    recommended_next_step TEXT NOT NULL,
    gate_status TEXT NOT NULL CHECK(gate_status IN ('needs_evidence','blocked')),
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    source_urls_json TEXT NOT NULL DEFAULT '[]',
    facts_json TEXT NOT NULL DEFAULT '[]',
    inference_json TEXT NOT NULL DEFAULT '[]',
    overlap_json TEXT NOT NULL DEFAULT '{}',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(research_run_id, normalized_topic)
);

CREATE INDEX IF NOT EXISTS idx_research_runs_site_date
    ON research_runs(site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_items_run
    ON research_run_items(research_run_id, provider);
CREATE INDEX IF NOT EXISTS idx_research_candidates_run
    ON research_candidates(research_run_id, gate_status, id);

PRAGMA user_version = 4;
"""

MIGRATION_5 = """
UPDATE imports SET analysis_active = 0 WHERE source_type = 'gsc';
UPDATE imports
SET analysis_active = 1
WHERE id IN (
    SELECT latest.id
    FROM imports AS latest
    WHERE latest.source_type = 'gsc'
      AND latest.status = 'success'
      AND latest.id = (
          SELECT candidate.id
          FROM imports AS candidate
          WHERE candidate.site_id = latest.site_id
            AND candidate.source_type = 'gsc'
            AND candidate.status = 'success'
          ORDER BY candidate.imported_at DESC, candidate.id DESC
          LIMIT 1
      )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_imports_active_gsc
    ON imports(site_id)
    WHERE source_type = 'gsc' AND analysis_active = 1;

PRAGMA user_version = 5;
"""

MIGRATION_6 = """
UPDATE imports
SET quality_eligible = CASE
    WHEN source_type <> 'gsc' OR (status = 'success' AND analysis_active = 1) THEN 1
    ELSE 0
END;

CREATE INDEX IF NOT EXISTS idx_imports_quality_gsc
    ON imports(site_id, imported_at DESC)
    WHERE source_type = 'gsc' AND status = 'success' AND quality_eligible = 1;

PRAGMA user_version = 6;
"""

MIGRATION_7 = """
CREATE TABLE IF NOT EXISTS topic_nodes (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES topic_nodes(id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    preferred_name TEXT NOT NULL,
    description TEXT,
    node_type TEXT NOT NULL
        CHECK(node_type IN ('root','branch','article_topic','knowledge','candidate')),
    display_status TEXT NOT NULL DEFAULT 'no_opportunity'
        CHECK(display_status IN ('covered','expand','no_opportunity')),
    boundary_status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK(boundary_status IN ('confirmed','pending','outside')),
    source_type TEXT NOT NULL DEFAULT 'system'
        CHECK(source_type IN ('system','import','research','human')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(site_id, topic_key)
);

CREATE TABLE IF NOT EXISTS topic_aliases (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL REFERENCES topic_nodes(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(topic_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS topic_relations (
    source_topic_id INTEGER NOT NULL REFERENCES topic_nodes(id) ON DELETE CASCADE,
    target_topic_id INTEGER NOT NULL REFERENCES topic_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('related','broader_hint')),
    rationale TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_topic_id, target_topic_id, relation_type),
    CHECK(source_topic_id <> target_topic_id)
);

CREATE TABLE IF NOT EXISTS topic_content_links (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL REFERENCES topic_nodes(id) ON DELETE CASCADE,
    content_item_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    coverage_role TEXT NOT NULL CHECK(coverage_role IN ('primary','auxiliary')),
    mapping_basis TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    human_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(human_confirmed IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(topic_id, content_item_id, coverage_role)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_content_one_primary
    ON topic_content_links(content_item_id)
    WHERE coverage_role = 'primary';
CREATE INDEX IF NOT EXISTS idx_topic_nodes_parent
    ON topic_nodes(site_id, parent_id, preferred_name);
CREATE INDEX IF NOT EXISTS idx_topic_content_topic
    ON topic_content_links(topic_id, coverage_role);

CREATE TABLE IF NOT EXISTS topic_decisions (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    topic_id INTEGER REFERENCES topic_nodes(id) ON DELETE SET NULL,
    normalized_topic TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('do','dont_recommend','skip')),
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(site_id, normalized_topic)
);

CREATE TABLE IF NOT EXISTS topic_research_memory (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    topic_id INTEGER REFERENCES topic_nodes(id) ON DELETE SET NULL,
    seed_type TEXT NOT NULL CHECK(seed_type IN ('gsc','topic_gap','boundary','manual')),
    dimension_key TEXT,
    filters_json TEXT NOT NULL DEFAULT '{}',
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    result_status TEXT NOT NULL
        CHECK(result_status IN ('found','duplicate','no_result','insufficient')),
    result_summary TEXT,
    research_run_id INTEGER REFERENCES research_runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_research_memory_lookup
    ON topic_research_memory(site_id, topic_id, seed_type, dimension_key, normalized_query);

PRAGMA user_version = 7;
"""

MIGRATION_8 = """
ALTER TABLE research_runs ADD COLUMN seed_type TEXT NOT NULL DEFAULT 'gsc'
    CHECK(seed_type IN ('gsc','topic_gap','boundary','manual'));
ALTER TABLE research_runs ADD COLUMN topic_id INTEGER
    REFERENCES topic_nodes(id) ON DELETE SET NULL;
ALTER TABLE research_runs ADD COLUMN dimension_key TEXT;
ALTER TABLE research_runs ADD COLUMN filters_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE research_candidates ADD COLUMN suggested_parent_topic_id INTEGER
    REFERENCES topic_nodes(id) ON DELETE SET NULL;
ALTER TABLE research_candidates ADD COLUMN decision TEXT NOT NULL DEFAULT 'pending'
    CHECK(decision IN ('pending','do','dont_recommend','skip'));
ALTER TABLE research_candidates ADD COLUMN decision_reason TEXT;
ALTER TABLE research_candidates ADD COLUMN decided_at TEXT;

CREATE INDEX IF NOT EXISTS idx_research_runs_entry
    ON research_runs(site_id, seed_type, topic_id, dimension_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_candidates_decision
    ON research_candidates(site_id, decision, normalized_topic);

PRAGMA user_version = 8;
"""


MIGRATION_9 = """
CREATE TABLE IF NOT EXISTS gsc_connections (
    site_id INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    property_uri TEXT NOT NULL,
    permission_level TEXT NOT NULL,
    trusted_start_date TEXT NOT NULL,
    trusted_start_reason TEXT NOT NULL,
    connected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_sync_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT
);

CREATE TABLE IF NOT EXISTS gsc_sync_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    property_uri TEXT NOT NULL,
    requested_start_date TEXT NOT NULL,
    requested_end_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
    aggregate_row_count INTEGER NOT NULL DEFAULT 0,
    query_page_row_count INTEGER NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    reused INTEGER NOT NULL DEFAULT 0 CHECK(reused IN (0,1)),
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS gsc_query_page_metrics (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    data_date TEXT NOT NULL,
    query TEXT NOT NULL,
    page TEXT NOT NULL,
    search_type TEXT NOT NULL DEFAULT 'web',
    clicks REAL,
    impressions REAL,
    ctr REAL,
    position REAL,
    source_row INTEGER NOT NULL,
    UNIQUE(import_id, data_date, query, page, search_type)
);

CREATE INDEX IF NOT EXISTS idx_gsc_query_page_import_page
    ON gsc_query_page_metrics(import_id, page, impressions DESC);
CREATE INDEX IF NOT EXISTS idx_gsc_query_page_import_query
    ON gsc_query_page_metrics(import_id, query, impressions DESC);
CREATE INDEX IF NOT EXISTS idx_gsc_sync_runs_site_date
    ON gsc_sync_runs(site_id, started_at DESC);

PRAGMA user_version = 9;
"""


DEFAULT_RULES = [
    {
        "rule_key": "protect_click_loss",
        "version": "0.2.0",
        "rule_type": "heuristic",
        "evidence_level": "A",
        "rationale": "优先诊断已有页面的明确点击损失；只有当前与上一窗口均有真实查询—页面联合行时才可执行。阈值为冷启动值，后续按站点历史校准。",
        "config": {
            "min_previous_clicks": 3,
            "min_previous_impressions": 50,
            "max_current_ratio": 0.7,
            "requires_current_and_previous_query_page": True,
        },
        "sources": [
            "https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops"
        ],
        "known_failures": ["季节性变化", "品牌词变化", "算法更新", "联合行顶部数据限制"],
        "review_after": "2026-10-17",
    },
    {
        "rule_key": "site_relative_ctr",
        "version": "0.2.0",
        "rule_type": "site_empirical",
        "evidence_level": "A",
        "rationale": "使用本站同排名区间页面 CTR 中位数发现诊断对象；必须有该页当前查询—页面联合行才可进入内容动作。",
        "config": {
            "min_impressions": 100,
            "baseline_ratio": 0.5,
            "min_bucket_size": 3,
            "requires_current_query_page": True,
        },
        "sources": ["https://support.google.com/webmasters/answer/7042828?hl=en"],
        "known_failures": ["页面混合多个查询意图", "设备/国家结构变化", "AI 搜索外观变化"],
        "review_after": "2026-10-17",
    },
    {
        "rule_key": "striking_distance_page",
        "version": "0.2.0",
        "rule_type": "heuristic",
        "evidence_level": "A",
        "rationale": "对已有且可编辑页面识别 8–20 位的诊断对象；只有该页有当前查询—页面联合行时才可执行，且不因此自动创建新文章。",
        "config": {
            "min_impressions": 50,
            "position_min": 8,
            "position_max": 20,
            "requires_current_query_page": True,
        },
        "sources": ["https://support.google.com/webmasters/answer/7042828?hl=en"],
        "known_failures": ["平均排名掩盖查询差异", "查询—页面联合关系缺失"],
        "review_after": "2026-10-17",
    },
    {
        "rule_key": "query_page_evidence_gap",
        "version": "0.2.0",
        "rule_type": "official_fact",
        "evidence_level": "B",
        "rationale": "普通 GSC 查询表和网页表不能被当作联合维度；只为当前联合数据仍未映射的汇总查询生成补证据诊断。",
        "config": {"min_impressions": 100, "max_candidates": 5},
        "sources": ["https://developers.google.com/webmaster-tools/v1/searchanalytics/query"],
        "known_failures": [],
        "review_after": "2026-10-17",
    },
    {
        "rule_key": "gsc_signal_stability",
        "version": "0.2.0",
        "rule_type": "official_fact",
        "evidence_level": "A+B",
        "rationale": "GSC 是核心第一方信号，但会受聚合、顶部行限制、不完整数据和已知统计异常影响；先审计稳定性再行动。",
        "config": {
            "min_independent_windows": 3,
            "zero_click_signal_requires_windows": 2,
            "known_anomaly_start": "2025-05-13",
            "known_anomaly_end": "2026-04-27",
        },
        "sources": [
            "https://support.google.com/webmasters/answer/6211453?hl=en",
            "https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data?hl=en",
            "https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops?hl=en",
        ],
        "known_failures": ["没有日期维度时只能按导入日近似窗口", "历史异常清单需要定期复查"],
        "review_after": "2026-08-14",
    },
    EXTERNAL_QUERY_REVIEW_RULE,
    NEW_ARTICLE_CANDIDATE_RULE,
    MULTI_SOURCE_TOPIC_RESEARCH_RULE,
    GSC_QUERY_PAGE_SYNC_RULE,
    OLD_ARTICLE_GSC_READINESS_RULE,
    OLD_ARTICLE_CONTENT_QUALITY_RULE,
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


def _apply_migration_8(conn: sqlite3.Connection) -> None:
    run_columns = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
    }
    run_additions = {
        "seed_type": (
            "ALTER TABLE research_runs ADD COLUMN seed_type TEXT NOT NULL DEFAULT 'gsc' "
            "CHECK(seed_type IN ('gsc','topic_gap','boundary','manual'))"
        ),
        "topic_id": (
            "ALTER TABLE research_runs ADD COLUMN topic_id INTEGER "
            "REFERENCES topic_nodes(id) ON DELETE SET NULL"
        ),
        "dimension_key": "ALTER TABLE research_runs ADD COLUMN dimension_key TEXT",
        "filters_json": (
            "ALTER TABLE research_runs ADD COLUMN filters_json TEXT NOT NULL DEFAULT '{}'"
        ),
    }
    for name, statement in run_additions.items():
        if name not in run_columns:
            conn.execute(statement)
    candidate_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(research_candidates)").fetchall()
    }
    candidate_additions = {
        "suggested_parent_topic_id": (
            "ALTER TABLE research_candidates ADD COLUMN suggested_parent_topic_id INTEGER "
            "REFERENCES topic_nodes(id) ON DELETE SET NULL"
        ),
        "decision": (
            "ALTER TABLE research_candidates ADD COLUMN decision TEXT NOT NULL "
            "DEFAULT 'pending' CHECK(decision IN "
            "('pending','do','dont_recommend','skip'))"
        ),
        "decision_reason": "ALTER TABLE research_candidates ADD COLUMN decision_reason TEXT",
        "decided_at": "ALTER TABLE research_candidates ADD COLUMN decided_at TEXT",
    }
    for name, statement in candidate_additions.items():
        if name not in candidate_columns:
            conn.execute(statement)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_research_runs_entry
            ON research_runs(site_id, seed_type, topic_id, dimension_key, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_research_candidates_decision
            ON research_candidates(site_id, decision, normalized_topic);
        PRAGMA user_version = 8;
        """
    )


def _apply_migrations(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == 0:
        conn.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        conn.executescript(MIGRATION_2)
        version = 2
    if version < 3:
        action_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(actions)").fetchall()
        }
        if "workflow_status" not in action_columns:
            conn.execute(
                """
                ALTER TABLE actions ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'planned'
                CHECK(workflow_status IN ('planned','in_progress','completed','cancelled'))
                """
            )
        if "plan_version" not in action_columns:
            conn.execute("ALTER TABLE actions ADD COLUMN plan_version TEXT")
        if "updated_at" not in action_columns:
            conn.execute("ALTER TABLE actions ADD COLUMN updated_at TEXT")
        if "completed_at" not in action_columns:
            conn.execute("ALTER TABLE actions ADD COLUMN completed_at TEXT")
        conn.executescript(MIGRATION_3)
        version = 3
    if version < 4:
        conn.executescript(MIGRATION_4)
        version = 4
    if version < 5:
        import_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(imports)").fetchall()
        }
        if "analysis_active" not in import_columns:
            conn.execute(
                """
                ALTER TABLE imports ADD COLUMN analysis_active INTEGER NOT NULL DEFAULT 1
                CHECK(analysis_active IN (0,1))
                """
            )
        conn.executescript(MIGRATION_5)
        version = 5
    if version < 6:
        import_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(imports)").fetchall()
        }
        if "quality_eligible" not in import_columns:
            conn.execute(
                """
                ALTER TABLE imports ADD COLUMN quality_eligible INTEGER NOT NULL DEFAULT 1
                CHECK(quality_eligible IN (0,1))
                """
            )
        conn.executescript(MIGRATION_6)
        version = 6
    if version < 7:
        conn.executescript(MIGRATION_7)
        version = 7
    if version < 8:
        _apply_migration_8(conn)
        version = 8
    if version < 9:
        conn.executescript(MIGRATION_9)


def init_db(settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    with connection(active_settings) as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
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
                UPDATE rule_versions SET status = 'deprecated'
                WHERE rule_key = ? AND version <> ? AND status = 'active'
                """,
                (rule["rule_key"], rule["version"]),
            )
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
                    rule.get("valid_from", "2026-07-14"),
                    rule.get("review_after", "2026-10-14"),
                ),
            )
