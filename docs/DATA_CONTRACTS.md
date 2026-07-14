# 数据契约

## 1. 通用导入契约

每个导入文件先创建 SHA-256，再原样复制到：

`data/snapshots/{site_slug}/{source_type}/{timestamp}-{hash8}-{safe_name}`

数据库 `imports` 保存：站点、来源类型、原文件名、哈希、快照路径、状态、行数、元数据和错误。相同站点、来源类型和哈希只导入一次。

## 2. GSC Excel

支持当前中文 Search Console 导出：

- 标准工作簿：`图表 / 查询数 / 网页 / 国家_地区 / 设备 / 搜索结果呈现 / 过滤器`
- 对比工作簿：除图表外，各维度包含“过去 28 天”和“先前 28 天”列。

标准字段：

| 字段 | 说明 |
|---|---|
| dimension | date/query/page/country/device/search_appearance |
| value | 对应日期、查询、URL 或维度值 |
| period | current/previous |
| clicks | 点击数 |
| impressions | 展示数 |
| ctr | 0–1 小数，绝不转成百分数存储 |
| position | 平均排名 |

重要限制：查询 Sheet 与网页 Sheet 是两个独立聚合维度，不能据此建立 query→page 关系。需要联合关系时，必须导入页面筛选后的查询表或后续使用 GSC API。

## 3. CMS Blog JSON

顶层：`type/exportedAt/count/excludedFields/items`。

关键 item 字段：

`id/title/slug/summary/content/tags/seoTitle/seoDescription/seoKeywords/active/publishedAt/createdAt/updatedAt`

正文按原始 UTF-8 保留。清洗和质量诊断在独立派生层完成，不修改快照。

## 4. CMS Product JSON

顶层结构与 Blog 相同。

关键 item 字段：

`id/sku/title/titleEn/slug/categoryIds/price/inventory/attributes/description/features/packageList/metaTitle/metaDescription/keywords/active/createdAt/updatedAt/translations/searchKeywords/internalPower`

产品 canonical URL 由站点配置的路径模板派生；派生 URL 属于 inference，允许人工修正。

## 5. 时间与缺失值

- 数据库存 UTC ISO 8601。
- 导出本身没有的字段使用 NULL，不用 0 或乐观默认值代替未知。
- CTR 为 0 可以是真实值；缺失 CTR 必须是 NULL。
- 缺少排名、需求或转化数据会降低置信度，不能自动获得中性高分。

