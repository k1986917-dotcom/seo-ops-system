-- SEO Ops System — full schema for a fresh database (PRAGMA user_version = 13)
-- Generated from src/seo_ops/db.py.
-- Applies the initial SCHEMA plus all migrations in order. The ALTER TABLE
-- statements for `actions.workflow_status / plan_version / updated_at /
-- completed_at / legacy_stage` come from the Python _apply_migrations() helper,
-- which checks for column existence before adding. They are required before
-- MIGRATION_3 / MIGRATION_13, so they are inlined here.

-- ========== MIGRATION 1 (initial) ==========
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

-- ========== MIGRATION 2.5 (Python pre-m3 ALTERs on actions) ==========
ALTER TABLE actions ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'planned'
    CHECK(workflow_status IN ('planned','in_progress','completed','cancelled'));
ALTER TABLE actions ADD COLUMN plan_version TEXT;
ALTER TABLE actions ADD COLUMN updated_at TEXT;
ALTER TABLE actions ADD COLUMN completed_at TEXT;

-- ========== MIGRATION 2 ==========
CREATE TABLE IF NOT EXISTS source_connections (
    provider TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('connected','error')),
    last_checked_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT
);

PRAGMA user_version = 2;

-- ========== MIGRATION 3 ==========
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

-- ========== MIGRATION 4 ==========
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

-- ========== MIGRATION 5 ==========
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

-- ========== MIGRATION 6 ==========
UPDATE imports
SET quality_eligible = CASE
    WHEN source_type <> 'gsc' OR (status = 'success' AND analysis_active = 1) THEN 1
    ELSE 0
END;

CREATE INDEX IF NOT EXISTS idx_imports_quality_gsc
    ON imports(site_id, imported_at DESC)
    WHERE source_type = 'gsc' AND status = 'success' AND quality_eligible = 1;

PRAGMA user_version = 6;

-- ========== MIGRATION 7 ==========
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

-- ========== MIGRATION 8 ==========
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

-- ========== MIGRATION 9 ==========
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

-- ========== MIGRATION 10 ==========
ALTER TABLE research_candidates ADD COLUMN qualification_status TEXT
    CHECK(qualification_status IN
        ('qualified','needs_evidence','needs_human_review','blocked','stale'));
ALTER TABLE research_candidates ADD COLUMN qualification_version TEXT;
ALTER TABLE research_candidates ADD COLUMN cms_fingerprint TEXT;
ALTER TABLE research_candidates ADD COLUMN evidence_fingerprint TEXT;
ALTER TABLE research_candidates ADD COLUMN closest_existing_json TEXT;
ALTER TABLE research_candidates ADD COLUMN evidence_demand_json TEXT;
ALTER TABLE research_candidates ADD COLUMN evidence_gap_json TEXT;
ALTER TABLE research_candidates ADD COLUMN evidence_material_json TEXT;
ALTER TABLE research_candidates ADD COLUMN recommended_disposition TEXT
    CHECK(recommended_disposition IS NULL OR recommended_disposition IN
        ('new_article','update_existing','covered_existing','insufficient_evidence'));
ALTER TABLE research_candidates ADD COLUMN human_review_reason TEXT;
ALTER TABLE research_candidates ADD COLUMN human_reviewed_at TEXT;
ALTER TABLE research_candidates ADD COLUMN qualified_at TEXT;
ALTER TABLE research_candidates ADD COLUMN stale_after TEXT;
ALTER TABLE research_candidates ADD COLUMN previous_assessment_json TEXT;

CREATE INDEX IF NOT EXISTS idx_research_candidates_qual
    ON research_candidates(site_id, qualification_status, id);
CREATE INDEX IF NOT EXISTS idx_research_candidates_decision_qual
    ON research_candidates(site_id, decision, qualification_status);

PRAGMA user_version = 10;

-- ========== MIGRATION 12 ==========
CREATE TABLE IF NOT EXISTS research_seed_observations (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    topic_id INTEGER REFERENCES topic_nodes(id) ON DELETE SET NULL,
    dimension_key TEXT,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    source_title TEXT,
    observed_text TEXT NOT NULL,
    source_quote TEXT,
    evidence_ref TEXT,
    task_card_json TEXT NOT NULL DEFAULT '{}',
    follow_up_query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    provisional_topic TEXT,
    semantic_cluster TEXT NOT NULL DEFAULT 'emerging',
    anchor_fit TEXT NOT NULL DEFAULT 'core'
        CHECK(anchor_fit IN ('core','adjacent','off_anchor')),
    status TEXT NOT NULL DEFAULT 'observed'
        CHECK(status IN ('observed','consumed','discarded')),
    consumed_by_run_id INTEGER REFERENCES research_runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE(site_id, research_run_id, evidence_ref, normalized_query)
);

CREATE INDEX IF NOT EXISTS idx_research_seed_observations_frontier
    ON research_seed_observations(
        site_id, status, semantic_cluster, topic_id, dimension_key, id DESC
    );
CREATE INDEX IF NOT EXISTS idx_research_seed_observations_source
    ON research_seed_observations(site_id, source_kind, research_run_id DESC);

PRAGMA user_version = 12;

-- ========== MIGRATION 13 ==========
ALTER TABLE actions ADD COLUMN legacy_stage TEXT;

PRAGMA user_version = 13;

-- ========== MIGRATION 11 (Python: rewrites product canonical_url) ==========
-- Updates content_items.canonical_url for products from /p-{SKU}.html to
-- https://{domain}/products/{slug}. See _apply_migration_11() in src/seo_ops/db.py.

PRAGMA user_version = 13;
