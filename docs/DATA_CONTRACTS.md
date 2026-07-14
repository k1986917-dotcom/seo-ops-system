# 数据契约

## 1. 通用导入契约

每个导入文件先创建 SHA-256，再原样复制到：

`data/snapshots/{site_slug}/{source_type}/{timestamp}-{hash8}-{safe_name}`

数据库 `imports` 保存站点、来源类型、原文件名、哈希、快照路径、状态、行数、元数据和错误。相同站点、来源类型和哈希只导入一次。

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

## 3. GSC 质量派生

`services/data_quality.py` 不修改原始指标，只派生：

- 成功快照数与独立观察窗口数。
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

- 密钥只在 `.env`，不属于数据库数据契约。
- `source_connections` 只保存 `provider/status/last_checked_at/details_json/error_message`。
- `details_json` 必须使用供应商专属白名单，禁止保存整个账户响应。
- 错误消息必须安全归一化，禁止保存异常对象、请求 URL、Header 或响应正文。
- SQLite `PRAGMA user_version=2` 创建该表，现有 v1 数据库增量迁移。

## 7. 计划中的外部执行契约

`external_runs` 尚未实现。实现时至少包含：站点、provider、purpose、输入机会/证据 ID、规范化参数、请求时间、响应快照路径、SHA-256、状态、计费单位和安全错误。供应商原始响应先快照，再生成结构化 evidence；AI 摘要不能替代原始响应。

## 8. 时间与缺失值

- 数据库存 UTC ISO 8601。
- 导出没有的字段使用 NULL，不用 0 或乐观默认值代替未知。
- CTR 为 0 可以是真实值；缺失 CTR 必须是 NULL。
- 缺少排名、需求、转化或独立观察窗口会降低置信度，不能自动获得中性高分。
