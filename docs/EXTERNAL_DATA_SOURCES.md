# 外部数据源与 GSC 可信度基线

最后核实：2026-07-14；建议复查：2026-08-14。

## 1. 当前结论

外部数据源需要保留，但必须按“它能证明什么”分工。多个工具返回相似文本，不自动构成多个独立证据。

| 数据源 | 系统角色 | 能支持的结论 | 不能支持的结论 |
|---|---|---|---|
| GSC | 本站第一方表现 | 点击、展示、CTR、平均位置及维度变化 | 查询级订单、变化原因、未来流量 |
| CMS | 本站内容资产 | 现有文章/产品、正文、更新时间、可编辑目标 | 搜索需求、排名难度、转化 |
| SerpAPI Google Search | 指定市场的 SERP 快照 | 当时的自然结果、竞争页面、相关问题和 SERP 功能 | 稳定搜索量、内容质量、商业价值 |
| Google Trends | 季节性与方向 | 相对兴趣、地区差异、相关查询与趋势方向 | 绝对搜索量；0 不等于无人搜索 |
| Firecrawl | 指定网页采集 | 页面公开内容、结构、产品字段与抓取时间 | 排名、需求、来源权威或事实真实性 |
| Tavily | 研究发现 | 候选来源、近期网页与需进一步核验的线索 | Google 排名、搜索量或来源本身可信 |
| Bing Webmaster Tools | 第二搜索引擎/AI 可见性 | Bing 表现、受支持 AI 场景中的引用和抽样 grounding queries | Google 表现、AI 答案中的权威排名或位置 |
| PageSpeed Insights / CrUX | 页面体验诊断 | 实验室问题和有数据时的 28 天真实体验分布 | 修复后必然提升排名或点击 |

## 2. Google Trends 接入选择

当前优先级：

1. 已有 SerpAPI 时，通过其 `google_trends` 引擎获取时间序列、地区、相关主题和相关查询。
2. 保留 Google Trends 网页 CSV 手动导入作为不依赖第三方接口的后备。
3. 用户若获得 Google 官方 Trends API Alpha 资格，再切换官方接口。
4. 不使用已经归档的 pytrends 作为生产核心。

Google 官方说明 Trends 是实际搜索的抽样、匿名化、分类和聚合数据，并按时间和地区归一化至 0–100；它反映相对兴趣，不是绝对量。官方 API 在 2025-07 公布时仍只向极少 Alpha 测试者开放。

官方/供应商资料：

- https://support.google.com/trends/answer/4365533?hl=en-uk
- https://developers.google.com/search/blog/2025/07/trends-api
- https://serpapi.com/google-trends-api

## 3. GSC 高曝光、零点击、后来消失怎样处理

“Google 沙盒”不是 Google 官方故障诊断。Google 官方流量下降排查列出的主要方向是算法更新、技术问题、安全/垃圾内容问题、季节性/兴趣变化和站点迁移；因此系统不得用“沙盒”作为自动结论。

尤其要先排除数据本身：Google 在 2026-04-03 的异常记录中说明，一项日志错误使 2025-05-13 至 2026-04-27 的曝光报告不准确；修复后可能看到曝光下降，受影响的是曝光、CTR 与平均位置，点击不受该错误影响。

当前门槛：

1. 不完整的最近数据不进入正式比较。
2. 高曝光零点击至少跨两个完整观察窗口仍存在，才进入 SERP 验证。
3. 至少积累三个独立窗口，才把整体信号标为“可用于决策”；之前只输出诊断任务并降低置信权重。
4. 日期命中官方 GSC 异常时，曝光相关候选自动变成“补证据”。
5. 先拆查询、页面、国家、设备和搜索外观；平均位置与页面聚合 CTR 不直接解释原因。
6. 用 SerpAPI 检查目标市场当时的结果页和 SERP 功能，用 Trends 区分全市场季节性；两者都不能替代 GSC。
7. 突变必须核对 Search Console 数据异常页和 Google Search Status/Ranking 更新。

资料：

- https://support.google.com/webmasters/answer/6211453?hl=en
- https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops?hl=en
- https://developers.google.com/search/help/status-dashboard?hl=en
- https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data?hl=en

## 4. 连接层与执行层

当前 `0.2.0` 已实现：

- `/settings` 统一配置 AI、SerpAPI、Firecrawl、Tavily 和 Trends 口径。
- 密钥框永不回显；空值表示保留；可显式清除。
- 连接测试只使用账户、用量或模型列表接口，不消耗一次搜索或模型生成。
- 连接结果只保存状态、检查时间和经过白名单筛选的用量字段。
- GSC 稳定性状态已经进入机会引擎的置信权重和异常门槛。

尚未实现：

- SerpAPI SERP/Trends 结果还没有进入不可变外部快照和候选复核。
- Firecrawl/Tavily 还没有由 Opportunity 自动创建研究任务。
- 外部调用成本、查询参数、响应哈希和证据 ID 尚未形成完整 `external_runs` 契约。

因此当前页面中的“已连接”只代表凭据和账户接口有效，不代表该数据已经影响现有机会分数。

## 5. 凭据安全

- 密钥只保存在项目根目录 `.env`，运行时权限设为 `0600`，并由 Git 忽略。
- 密钥不进入 SQLite、HTML、重定向消息、异常文本或访问日志。
- SerpAPI Account API 响应会返回 API Key；系统只提取账户状态、套餐和剩余额度，丢弃其余字段。
- 网络异常不得原样返回，因为异常对象可能包含带查询参数的 URL。
- `.env` 是本地明文环境文件，不是加密凭据库；项目若离开单人本机 WSL，必须先增加认证、HTTPS 和真正的密钥管理。
- 任何曾粘贴到聊天、工单或公开文本的密钥都应撤销并重新生成，不能直接复制进设置页。

连接测试官方接口：

- SerpAPI Account API：https://serpapi.com/account-api
- Firecrawl Credit Usage：https://docs.firecrawl.dev/api-reference/endpoint/credit-usage
- Tavily Usage：https://docs.tavily.com/documentation/api-reference/endpoint/usage

## 6. 下一实现单元

下一步新增 `external_runs` 与不可变响应快照，然后只对 Top 候选执行：

1. SerpAPI SERP 快照（固定国家、语言、设备、位置）。
2. Google Trends 12 个月与 5 年相对兴趣。
3. 根据 SERP 中真正出现的竞争页面，使用 Firecrawl 定向抓取。
4. Tavily 仅补充官方/一手来源发现。
5. AI 读取这些带 evidence ID 的材料生成解释或 research brief，不修改门槛和分数。
