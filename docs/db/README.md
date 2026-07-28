# Database Schema (SQLite)

本仓库使用单个 SQLite 文件，路径为 `data/seo_ops.db`（默认；可由 `SEO_OPS_DATA_DIR` 环境变量覆盖）。
**生产数据库已被 Git 忽略**（`.gitignore`：`data/*.db`），本仓库不包含任何真实业务数据。

本目录提供：
- `schema.sql` — 完整 schema（13 个 migration 的最终状态）
- `seed.sql` — 一个脱敏的最小测试数据库（一个 site、几篇假文章、一份假 GSC 批次）

---

## 表清单

按迁移顺序：

### MIGRATION 1（SCHEMA，初始）

| 表 | 作用 |
|----|------|
| `sites` | 多站点支持；当前只有 laserpointerhub 一个 |
| `imports` | 每次数据导入的记录（GSC OAuth、Blog JSON、Product JSON、Excel） |
| `gsc_metrics` | GSC 指标（query × period 的 impressions/ctr/position） |
| `content_items` | 内容主表（blog、product 两种类型） |
| `content_snapshots` | 每次导入时内容快照（保留 body 用于全文 TF-IDF） |
| `analysis_runs` | 分析运行（一次机会扫描） |
| `opportunities` | 扫描出的机会（type: old/new；gate: passed/needs_evidence/blocked） |
| `actions` | 接受的机会转成的 action；`legacy_stage` 字段记录 Legacy 工作流进度 |
| `measurements` | action 发布后的效果观察（7/28/56 天窗口） |
| `rule_versions` | 规则版本注册表（每条规则必须有 evidence_level + review_after） |
| `ai_runs` | 每次 AI 调用的审计记录（prompt SHA、input refs、status） |

### MIGRATION 2

无新表；只设 `PRAGMA user_version = 2`。

### MIGRATION 3

| 表 | 作用 |
|----|------|
| `source_connections` | 各 provider 的连接状态（connected/error） |
| `external_runs` | 每次外部 API 调用的审计（request/response SHA、usage、status） |
| `evidence_items` | 外部证据条目（带 evidence_id 引用 external_run） |
| `action_steps` | action 内的子步骤（旧版任务卡，current Legacy 工作流不在这里） |

并为 `actions` 表加列：`workflow_status`、`plan_version`、`updated_at`、`completed_at`。

### MIGRATION 4

| 表 | 作用 |
|----|------|
| `research_runs` | 一次外部调研（GSC/主题缺口/边界） |
| `research_run_items` | 调研包含的外部调用 |
| `research_candidates` | 调研产出的候选主题（含 qualification_status） |

### MIGRATION 5/6

加 `analysis_active` 和 `quality_eligible` 列；建部分索引保证每站只有一个 active GSC 批次。

### MIGRATION 7

主题图谱：
| 表 | 作用 |
|----|------|
| `topic_nodes` | 树状主题节点（root / branch / article_topic / knowledge / candidate） |
| `topic_aliases` | 同义词 |
| `topic_relations` | 节点关系 |
| `topic_content_links` | 主题 ↔ 内容映射 |
| `topic_decisions` | "要做/不再推荐/暂时跳过" 决定 |
| `topic_research_memory` | 调研记忆（防重复查询） |

### MIGRATION 8

为 `research_runs` 加 `seed_type / topic_id / dimension_key / filters_json`；
为 `research_candidates` 加 `decision / decision_reason / decided_at`。

### MIGRATION 9

GSC 同步深化：
| 表 | 作用 |
|----|------|
| `gsc_connections` | 站点 OAuth 连接信息（含 trusted_start_date） |
| `gsc_sync_runs` | 每次同步审计 |
| `gsc_query_page_metrics` | 真正的查询 × 页面联合行（带 data_date） |

### MIGRATION 10

为 `research_candidates` 加 qualification 系列列：
`qualification_status / qualification_version / cms_fingerprint / evidence_fingerprint /
closest_existing_json / evidence_demand_json / evidence_gap_json / evidence_material_json /
recommended_disposition / human_review_reason / human_reviewed_at / qualified_at /
stale_after / previous_assessment_json`

### MIGRATION 11

不在这里，是 Python 函数做的（把旧的 `/p-{SKU}.html` canonical_url 改成 `/products/{slug}`）。

### MIGRATION 12

| 表 | 作用 |
|----|------|
| `research_seed_observations` | 调研期间发现的原始观察（Reddit、论坛、新闻等） |

### MIGRATION 13

为 `actions` 加 `legacy_stage` 列（当前最常用）。

---

## 关键不变量

- **每站只能有一个 active GSC 批次** — `idx_imports_active_gsc` 唯一索引保证
- **action 的 legacy_stage 只能单向推进** — 由 `legacy_workflow.py` 维护，没有回退路径
- **每次 AI 调用都留痕** — `ai_runs` 表存 prompt SHA + input refs（**绝不存原始 prompt/response 内容**，那是 `external_runs` 的事）
- **规则不能"无中生有"** — `rule_versions` 每条都必须有 evidence_level + review_after + known_failures
- **删除 GSC 批次会物理删除依赖它的派生数据** — 不是只删 `imports` 行

---

## 脱敏测试数据库

`seed.sql` 提供：
- 1 个 site（laserpointerhub）
- 4 篇假 article（slug/title 都是占位符，不是真实业务）
- 1 份假 GSC 批次（imports + gsc_metrics 全空行）

**不包含**：
- 任何真实的 URL
- 任何真实的产品
- 任何真实的搜索词或展示量
- 任何真实的素材包/草稿/AI 调用记录
- 任何真实用户的 GSC token

启动方式见仓库根 `README.md` 的「本地启动」一节。