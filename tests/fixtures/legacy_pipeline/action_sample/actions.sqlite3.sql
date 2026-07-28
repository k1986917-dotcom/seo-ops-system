-- SEO Ops DB sample — actions and topics table for one synthetic article
-- Apply with: sqlite3 data/seo_ops.db < actions.sqlite3.sql
--
-- This is what the SEO Ops DB looks like after the operator accepts a
-- recommended topic and the system creates one action. The legacy_stage
-- column (added in MIGRATION_13) tracks R0..W3 progress.

INSERT INTO sites(
    slug, name, domain, blog_path_template, product_path_template,
    created_at, updated_at
) VALUES(
    'examplesite', 'Example Site', 'example.com',
    '/blog/{slug}', '/products/{slug}',
    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
);

-- A research_runs row that produced the candidate below.
INSERT INTO research_runs(
    site_id, status, budgets_json, seed_queries_json,
    seed_type, filters_json, candidate_count, started_at
) VALUES(
    1, 'success', '{}', '["example query"]',
    'manual', '{}', 1, '2026-01-15T00:00:00+00:00'
);

-- A research_candidates row that was accepted → spawned an action.
-- This is what plan/research hands off to the Legacy workflow.
INSERT INTO research_candidates(
    research_run_id, site_id, topic, normalized_topic, intent,
    rationale, recommended_next_step, gate_status,
    evidence_refs_json, source_urls_json, facts_json, inference_json,
    overlap_json, limitations_json,
    qualification_status, qualification_version, cms_fingerprint,
    evidence_fingerprint, closest_existing_json, evidence_demand_json,
    evidence_gap_json, evidence_material_json,
    recommended_disposition, qualified_at,
    created_at
) VALUES(
    1, 1,
    'Example Topic for Testing the Legacy Pipeline',
    'example topic for testing the legacy pipeline',
    '混合型',
    'Sample candidate used as test fixture; demonstrates the schema that plan → research hands off to legacy.',
    'next_step: accept → create action',
    'needs_evidence',
    '[]', '[]', '[]', '{}', '{}', '[]',
    'qualified', '0.2.0',
    'sha256:placeholder0000000000000000000000000000000000000000000000000000000000',
    'sha256:placeholder0000000000000000000000000000000000000000000000000000000000',
    '{"title":"existing article about something else","relationship":"different"}',
    '{"demand":"low"}', '{"gap":"none"}', '{"material":"synthetic"}',
    'new_article', '2026-01-15T00:00:00+00:00',
    '2026-01-15T00:00:00+00:00'
);

-- The accepted action — what /actions page reads from.
INSERT INTO actions(
    opportunity_id, site_id, action_type, target_ref, decision,
    decision_reason, planned_change, baseline_json,
    workflow_status, plan_version, legacy_stage,
    decided_at, updated_at, completed_at
) VALUES(
    NULL, 1, 'create',
    'Example Topic for Testing the Legacy Pipeline',
    'accepted',
    'Sample fixture — accepted from a synthetic plan recommendation.',
    'New article covering the example topic for end-to-end pipeline testing.',
    '{}',
    'planned', '0.2.0',
    -- This is the column the Legacy workflow reads/writes.
    -- Valid values: r0_pending, r0_prompt, r1_results, r2_collect,
    --              r3_ai_analyze, r4_score, r5_write_ready, w0_validate,
    --              w1_draft, w1b_pre_check, w2_post_process, w3_register
    'r0_pending',
    '2026-01-15T00:00:00+00:00', '2026-01-15T00:00:00+00:00', NULL
);