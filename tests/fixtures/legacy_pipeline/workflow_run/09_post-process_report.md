<!--
SYNTHETIC TEST FIXTURE — see tests/fixtures/legacy_pipeline/README.md
Format produced by data_sources/modules/write_collector.py post-process --apply.
-->
# POST-PROCESS 报告
> 文件: example-topic-for-testing-the-legacy-pipeline-2026-01-15.md | 日期: 2026-01-15

## 链接校验
- 文章内链: 2 (1842词 → 下限2上限4) → ✅
- 产品内链: 1 (1842词 → 下限1上限2) → ✅
- 外链: 2 (1842词 → 下限2上限4) → ✅

### 文章内链
- ✅ [Example Article One](https://example.com/blog/example-article-1)
- ✅ [Example Article Three](https://example.com/blog/example-article-3)

### 产品内链
- ✅ [EXAMPLE-001](https://example.com/p-EXAMPLE-001.html)

### 外链
- ✅ [W3C Standards](https://www.w3.org/standards/)
- ✅ [NIST](https://www.nist.gov/)

## ⚠️ 链接问题
- (none — all links verified)

## 生成的 Frontmatter
```yaml
Title: "Example Topic for Testing the Legacy Pipeline: A Practical Integration Guide"
Slug: example-topic-for-testing-the-legacy-pipeline
Summary: Synthetic placeholder article that exercises the SEO Ops Legacy workflow's draft schema. Use as a fixture when testing W0..W3; do not index or link from production pages.
Tags: test fixture, pipeline, CI integration, schema validation, examplesite
SEO Title: "Example Topic for Testing the Legacy Pipeline: A Guide"
SEO Description: A synthetic article used to test the SEO Ops Legacy workflow's draft schema. Not real content; do not index.
SEO Keywords: example topic, legacy pipeline, test fixture, CI integration
内链:
 - https://example.com/blog/example-article-1
 - https://example.com/blog/example-article-3
内链产品:
 - https://example.com/p-EXAMPLE-001.html
外链:
 - https://www.w3.org/standards/
 - https://www.nist.gov/
字数: ~1842
```

## 🔪 蚕食门控（新文 vs 已发布）
- ✅ 通过：最高相似度 0.18 < 0.55 阻塞阈值
- 候选 0.18 vs Example Article Two: A Practical Checklist（slug 相似度 0.32）

## 质量评分
- 总分: 84.5 → ✅ 通过 (≥70)

## 🚦 总门控
- ✅ 通过，可进入段3 register。
- score=84.5, cannibal=0.18, all link checks ✅, entity coverage=78% (warning only, not blocking)