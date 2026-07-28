<!--
SYNTHETIC TEST FIXTURE — see tests/fixtures/legacy_pipeline/README.md
-->
---

# Research Brief

## 1. SEO Foundation

| 要素 | 值 |
|------|-----|
| 主关键词 | Example Topic for Testing the Legacy Pipeline |
| 搜索意图 | 混合型 (informational + commercial) |
| 当前排名 | 未收录 |
| 需求代理 | 仅 E2 人工验证 (no GSC) |
| 竞争强度 | 低 — 4 placeholder results |

## 2. Competitive Landscape

| 竞品 | 类型 | 弱点/差距 |
|------|------|-----------|
| Example Article One | Walkthrough | 无 CI/CD 内容 |
| Example Article Two | Checklist | 无失败模式 |
| Example Article Three | Comparison | 无迁移路径 |
| Example Article Four | Buying guide | 无生产部署指南 |

**关键差距**:
- CI/CD 集成
- 从手动到自动的迁移路径
- 失败模式清单
- 安全审计 / SBOM

## 3. Recommended Outline (H2)

```
H1: Example Topic for Testing the Legacy Pipeline: A Practical Integration Guide

Introduction (150 words)
- Pipeline exists; users don't know how to integrate it
- Synthetic fixtures preserve shape; CI is the missing piece
- Goal: turn the example pipeline into a CI-runnable contract test

H2: Why a Test Pipeline Matters for SEO Content Operations (300 words)
- Compare to traditional manual review (slow, inconsistent)
- Compare to legacy snapshot tests (brittle)
- Position the example pipeline as a contract test: shape, not content

H2: CI/CD Integration Patterns (500 words)
- Pre-commit hook: schema validation
- PR check: lint frontmatter, verify links
- Nightly: full pipeline run against synthetic fixtures
- Release gate: schema-diff against previous run

H2: Failure Modes and Recovery (400 words)
- Schema drift: legacy_sync adds a new column → break test
- Slug collision: two articles with the same slug
- Frontmatter parse failure: malformed YAML
- Link validation: external URL returns 4xx

H2: Migration from Manual Workflows (300 words)
- Phase 1: shadow run (collect synthetic fixtures, no enforcement)
- Phase 2: opt-in enforcement (developers can ignore failures)
- Phase 3: required gate (pipeline failure blocks merge)

H2: Production Deployment Checklist (200 words)
- One-page checklist
- 10 specific items, each with 1-line action

Conclusion (150 words)
- The example pipeline is a contract test
- Real fixtures come from real production data
- Synthetic fixtures let you iterate on the schema

FAQ
- Q: Can synthetic fixtures replace real data?
- Q: How often should I run the pipeline?
- Q: What if my schema changes?
```

## 4. Supporting Elements

- Data points: 0.68 composite score from synthetic deterministic scorer
- Stories: 2 synthetic cases (team onboarded 5 articles, dev integrated CI)
- Visual suggestions: 1 decision matrix (CI integration patterns), 1 failure mode table

## 5. Opportunity Score

- **Composite: 0.68**
- Strongest factor: content_gap (0.85) — current SERP misses CI/CD, migration, failure modes
- Weakest factor: demand_proxy (0.40) — no GSC data on synthetic site
- Tier: Cluster Content (1500-3000 words)

## 6. Objectivity Checklist

- [x] SERP intent matches informational → OK
- [x] Each content gap confirmed by ≥2 competitors → 3 gaps confirmed by 4 placeholder articles
- [x] Already top 10? → No, new content
- [x] High volume / low competition → Low competition confirmed; volume is unknown (no GSC)
- [x] Tier fits: Cluster Content fits the 1500-word estimate