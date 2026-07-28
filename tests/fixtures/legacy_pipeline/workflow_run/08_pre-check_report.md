<!--
SYNTHETIC TEST FIXTURE — see tests/fixtures/legacy_pipeline/README.md
Format produced by data_sources/modules/write_pre_check.py (--json output → formatted report).
-->
# WRITE 预检报告 — example-topic-for-testing-the-legacy-pipeline-2026-01-15.md
> 层级: Cluster Content | 字数: 1842 | 通过: 14/16 | 警告: 0

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 字数 ≥1200 (Cluster Content) | ✅ | 当前 1842 |
| H1/Title 含主关键词 | ✅ | H1=Example Topic for Testing Title=Example Topic for Testing |
| 主关键词在前100词 | ✅ | example topic for testing the legacy pipeline a practical integration guide most |
| H2 含主关键词 ≥2次 | ✅ | 2 hits in 6 H2s |
| SEO Title 50-60字符 | ✅ | 50 字符 |
| SEO Description 150-160字符 | ✅ | 152 字符 |
| Key Takeaways 3-5条 | ✅ | 5条 |
| CTA ≥2个 | ✅ | 3个 |
| 无 "click here"/"read more" 锚文本 | ✅ | ✅ |
| FAQ ≥3个问题 | ✅ | 6个 |
| FAQ Schema 存在 | ✅ | ✅ |
| 段落≤4句 (共24段) | ✅ | 24/24 段合规 |
| EEAT 信号 ≥2处 | ✅ | 3处 (NIST/W3C/ISO references) |
| H2/H3层级无跳跃 | ✅ | ✅ |
| 字数 vs tier 下限 硬门控 (Cluster Content) | ✅ | 1842词 ≥ 1380 (115%×1200) |
| 关键实体覆盖率 ≥80% | ⚠️ | 78% (synthetic partial coverage, 11/14 entities present) |

## 需修复 (1项)
- ⚠️ **关键实体覆盖率 ≥80%**: 78%; 缺: example entity B, placeholder metric ABC, synthetic standard XYZ.  *在正文补充缺失的 LSI 实体*

## 总结
- 通过 14/16 项
- 1 项 ⚠️（不阻塞 gate，但应在下一次 draft 修订时修复）
- 0 项 ❌
- 可进入段2（post-process）