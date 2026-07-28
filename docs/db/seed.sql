-- SEO Ops System — minimal anonymized seed data
-- Apply AFTER schema.sql. Creates one site and a few placeholder rows
-- so the UI can boot and tests have something to render.
--
-- This file contains NO real business data:
--   - Slugs, titles, URLs are placeholders ("example-article-1")
--   - GSC metrics are all-zero rows
--   - No tokens, no real keywords, no real products

-- ========== Default site ==========
INSERT INTO sites(
    slug, name, domain, blog_path_template, product_path_template,
    created_at, updated_at
) VALUES(
    'laserpointerhub', 'LaserPointerHub', 'example.com',
    '/blog/{slug}', '/products/{slug}',
    '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00'
);

-- ========== Sample rule versions (mirrors DEFAULT_RULES in src/seo_ops/db.py) ==========
INSERT INTO rule_versions(
    rule_key, version, rule_type, status, evidence_level, rationale,
    config_json, source_urls_json, known_failures_json, valid_from, review_after
) VALUES
('protect_click_loss', '0.2.0', 'heuristic', 'active', 'A',
 '优先诊断已有页面的明确点击损失',
 '{"min_previous_clicks":3,"min_previous_impressions":50,"max_current_ratio":0.7}',
 '[]', '[]', '2026-07-14', '2026-10-14'),
('qualify_new_candidate', '0.2.0', 'heuristic', 'active', 'A',
 '新文章候选必须满足最低证据门槛',
 '{"min_evidence_level":"B","require_topic_context":true}',
 '[]', '[]', '2026-07-14', '2026-10-14');

-- ========== Empty GSC import (placeholder) ==========
INSERT INTO imports(
    site_id, source_type, original_name, sha256, snapshot_path,
    status, row_count, metadata_json, imported_at, completed_at,
    analysis_active, quality_eligible
) VALUES(
    1, 'gsc', 'example-gsc-export.xlsx', 'placeholder00000000000000000000000000000000000000000000000000000000000000',
    'data/snapshots/placeholder/snapshot.xlsx',
    'success', 0, '{}', '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00',
    1, 1
);

-- ========== Sample published articles (placeholder) ==========
INSERT INTO content_items(
    site_id, content_type, external_id, slug, title, canonical_url, status,
    source_created_at, source_updated_at, first_seen_at, last_seen_at
) VALUES
(1, 'blog', 'blog-1', 'example-article-1', 'Example Article One',
 'https://example.com/blog/example-article-1', 'active',
 '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00',
 '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00'),
(1, 'blog', 'blog-2', 'example-article-2', 'Example Article Two',
 'https://example.com/blog/example-article-2', 'active',
 '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00',
 '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00');

INSERT INTO content_snapshots(
    import_id, content_item_id, title, summary, body, body_sha256,
    seo_title, seo_description, metadata_json, captured_at
) VALUES
(1, 1, 'Example Article One', 'Placeholder summary.',
 'Placeholder body. Replace with real content for actual use.',
 'placeholder', 'Example Article One Title', 'Placeholder SEO description.',
 '{"tags":["demo"]}', '2026-07-01T00:00:00+00:00'),
(1, 2, 'Example Article Two', 'Placeholder summary.',
 'Placeholder body. Replace with real content for actual use.',
 'placeholder', 'Example Article Two Title', 'Placeholder SEO description.',
 '{"tags":["demo"]}', '2026-07-01T00:00:00+00:00');