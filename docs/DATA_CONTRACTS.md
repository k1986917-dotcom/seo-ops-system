# 数据契约

## 1. 通用导入契约

每个导入文件先创建 SHA-256，再原样复制到：

`data/snapshots/{site_slug}/{source_type}/{timestamp}-{hash8}-{safe_name}`

数据库 `imports` 保存站点、来源类型、原文件名、哈希、快照路径、状态、行数、元数据和错误。相同站点、来源类型和哈希只导入一次。

GSC 导入有两个独立派生状态：`analysis_active` 每站点仅一个，用于当前机会和调研；`quality_eligible` 可有多个，用于跨批次稳定性判断。正常新导入只切换状态，不删除或覆盖旧批次。运营者可在导入记录上明确删除一个错误 GSC 批次；此时物理删除所选快照、指标及只依赖它的派生分析，但不会自动恢复更旧批次。

## 2. GSC Excel

支持当前中文 Search Console 导出：

- 标准：`图表 / 查询数 / 网页 / 国家_地区 / 设备 / 搜索结果呈现 / 过滤器`
- 对比：除图表外，各维度包含“过去 28 天”和“先前 28 天”列。

| 字段 | 说明 |
|---|---|
| dimension | date/query/page/country/device/search_appearance |
| value | 对应日期、查询、URL 或维度值 |
| period | current/previous |
| clicks | 点击数 |
| impressions | 展示数 |
| ctr | 0–1 小数，绝不转成百分数存储 |
| position | 平均排名 |

查询 Sheet 与网页 Sheet 是独立聚合维度，不能建立 query→page 关系。需要联合关系时，导入页面/查询筛选后的数据或使用 GSC API。

### 2.1 GSC 只读 OAuth API（SQLite v9）

- OAuth 只请求 `webmasters.readonly`，使用本机 loopback + PKCE；API Key 不属于本契约。
- `gsc_connections` 只保存站点属性 URI、权限等级、可信起始日/理由、连接/同步时间和安全错误；不保存任何 OAuth 凭据。
- `gsc_sync_runs` 保存请求日期、状态、汇总/联合行数、请求次数、是否复用、错误码和关联导入；删除导入后审计保留，`import_id` 置空。
- `gsc_query_page_metrics` 保存 `import_id/site_id/data_date/query/page/search_type/clicks/impressions/ctr/position/source_row`，唯一键为一次导入中的日期 + 查询 + 页面 + 搜索类型。
- API 原始响应按不可变 GSC 快照保存，只包含请求 JSON 与响应白名单字段，不包含 Header、access token、refresh token 或授权码。
- 所有请求的开始日不得早于 `2026-06-22`，`dataState=final`，`type=web`。当前窗口为最后最终日期向前最多 28 天；只有可信范围内存在完整前一 28 天时才保存 previous 汇总。
- 新 API 批次的快照、汇总行和联合行全部保存成功后才切换 `analysis_active`；失败时旧活动批次不变。

页面机会读取 `imports.metadata_json` 中的实际窗口日期，对联合行按页面、查询聚合。页面汇总只证明诊断对象：

- 当前联合行缺失：striking-distance/CTR 为 `needs_evidence`。
- 当前或上一联合行缺失：click-loss 为 `needs_evidence`。
- 内容生产必须再次检查同一 evidence，不能只信机会状态。

Blog/Product JSON 仍使用手工导入，不受 OAuth 同步影响。Excel 后备导入没有真实联合表，因此不能让 GSC 驱动的旧文章直接进入制作。

## 3. GSC 质量派生

`services/data_quality.py` 不修改原始指标，只派生：

- 当前批次、可信历史批次、已排除批次及独立观察窗口数。
- 只有 `quality_eligible=1` 的不同日期窗口参与稳定性计数。
- 所选导入的日期范围（存在 date 维度时）。
- 与已登记官方异常的重叠。
- `missing / provisional / usable / known_anomaly` 状态、理由和决策限制。

缺少日期维度时，独立窗口只能用导入日期近似，必须作为限制展示。派生状态进入分析运行元数据和机会 evidence，不写回 GSC 原始行。

## 4. CMS Blog JSON

顶层：`type/exportedAt/count/excludedFields/items`。

关键字段：`id/title/slug/summary/content/tags/seoTitle/seoDescription/seoKeywords/active/publishedAt/createdAt/updatedAt`。

正文按原始 UTF-8 保留。清洗和质量诊断在派生层完成，不修改快照。

## 5. CMS Product JSON

顶层结构与 Blog 相同。关键字段：`id/sku/title/titleEn/slug/categoryIds/price/inventory/attributes/description/features/packageList/metaTitle/metaDescription/keywords/active/createdAt/updatedAt/translations/searchKeywords/internalPower`。

产品 canonical URL 由站点路径模板派生；派生 URL 属于 inference，允许人工修正。

## 6. 设置与连接状态

- 普通供应商密钥只在 `.env`；GSC 客户端 JSON 与 token 只在 `.secrets/google/`，都不属于数据库数据契约。
- `.env` 对 GSC 只保存客户端/token 文件路径、可信起始日和 loopback 回调。客户端与 token 文件为 0600，目录为 0700，全部由 Git 忽略。
- `source_connections` 只保存 `provider/status/last_checked_at/details_json/error_message`。
- `details_json` 必须使用供应商专属白名单，禁止保存整个账户响应。
- 错误消息必须安全归一化，禁止保存异常对象、请求 URL、Header 或响应正文。
- SQLite v2 迁移创建该表；后续版本继续增量迁移，用户无需删除数据库。

## 7. 外部执行与证据契约（SQLite v3）

`external_runs` 每次保存：

- `site_id/opportunity_id/provider/purpose`。
- 输入 evidence ID、无密钥规范化参数和 `request_sha256`。
- `running/success/failed`、开始/完成 UTC 时间。
- 响应快照路径、SHA-256、字节大小、调用单位和安全归一化错误。

供应商响应可能在元数据中回显认证信息，因此落盘前只移除与当前密钥相同的字节及其 URL 编码形式；其余响应字节保持不变。脱敏后的响应先进入不可变快照，再派生 `evidence_items`。失败响应能安全落盘时也保留快照，但不会生成 evidence ID。

`evidence_items` 保存稳定 `evidence_id`、外部运行、证据类型/等级、原始来源 URL、结构化 payload、局限和采集时间。结构化 payload 是便于规则和界面使用的派生物，不能替代响应快照。AI 摘要也不能替代两者。

相同站点、provider、purpose 和请求哈希的成功结果在 24 小时内复用；复用不创建虚假的新来源，也不重复计费记录。

## 8. 行动执行契约（SQLite v3）

- `actions` 新增 `workflow_status/plan_version/updated_at/completed_at`。
- 接受机会时把当时的 evidence ID、指标与方法版本冻结进 `baseline_json`。
- `action_steps` 保存顺序、稳定 step key、阶段、说明、人工确认标记、状态和完成时间。
- 步骤只能按顺序完成；重新打开前序步骤会同时重新打开所有后序步骤。
- `collect_external_evidence` 必须在机会 evidence 中找到真实 SERP evidence ID 才能完成，不能只点勾跳过。
- `planned/in_progress/completed/cancelled` 与机会状态同步，但不会触发 CMS 发布或删除。
- 拒绝记录没有执行步骤，历史记录与证据不会因取消方案而删除。

## 9. 新文章候选契约

`new_article_candidate` 把三类内容分开保存：

- `facts`：GSC 查询指标与 SERP/Trends evidence ID。
- `program_inference`：CMS 标题/元数据词项重叠；明确不等于 query→page 归属。
- `missing/limitations`：定向 query→page、人工搜索意图复核、季节性缺口等。

当前没有 query→page 联合数据时，新文章候选最多为 `needs_evidence`；SERP 前十出现本站页面或命中强重叠阈值时为 `blocked`。不输出收入、成功率或查询级转化。

## 10. 多来源调研契约（SQLite v4）

- `research_runs` 保存站点、所用分析、状态、四个预算、精确用量、种子查询、候选数、AI 模型、安全错误和 UTC 时间。
- `research_run_items` 把本轮关联到 `external_runs`，记录 provider/purpose 以及是否复用；同一外部运行可被多轮复用，但不复制底层证据。
- `research_candidates` 分开保存 topic/intent/rationale、完整 evidence ID、原始来源 URL、facts、inference、CMS overlap、limitations 和 gate_status。
- 预算只计算真实供应商请求；复用次数单独记录。成功、失败和复用都不能从候选数量反推。
- AI 原始结构化输出继续进入 `ai_runs`；只有引用可解析到本轮输入 evidence ID 的主题和事实才能进入 `research_candidates`。
- 候选最多 8 个，状态只能为 `needs_evidence` 或 `blocked`。它不是 opportunity，也不修改 GSC、机会强度或行动基线。

这些表始于 SQLite v4，并在 v8 增加种子类型、图谱分支、扩展维度和研究历史；当前数据库整体为 v9。图谱缺口或边界扩展候选不因缺少 GSC query→page 自动阻塞，但仍必须通过意图独立、正文覆盖、站点边界和来源质量门槛。用户界面只显示“要做 / 不再推荐 / 暂时跳过”，详细证据状态保留在内部。

## 11. 主题图谱计划契约

- `topic_concepts` 保存稳定概念、首选名称、说明、类型和状态；关键词只是别名或证据，不直接等于概念。
- 每个概念最多有一个主父级用于树状展示；跨分支关系另存为 `related`，不得复制概念来伪造树结构。
- 文章与概念通过显式关联表连接，保存覆盖角色、置信度、依据和人工确认状态；文章标题相似不自动等于同一主题。
- 新发现先保存为待确认候选和建议父级；通过重复检查后才能成为正式概念。
- 接受、拒绝、延后、合并到已有概念的决定都保留原因和证据，避免以后重复推荐。
- 分支饱和是派生判断：没有合格未覆盖概念，且近期调研只返回已覆盖、别名、已拒绝或证据不足项；它不代表整个站点永久穷尽。
- 图谱不自动创建文章、不自动扩展站点边界、不自动发布；人工编辑是可选能力。

## 12. 时间与缺失值

- 数据库存 UTC ISO 8601。
- 导出没有的字段使用 NULL，不用 0 或乐观默认值代替未知。
- CTR 为 0 可以是真实值；缺失 CTR 必须是 NULL。
- 缺少排名、需求、转化或独立观察窗口会降低置信度，不能自动获得中性高分。
