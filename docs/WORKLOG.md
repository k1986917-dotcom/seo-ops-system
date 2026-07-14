# 工作日志

## 2026-07-14 — 0.2.0 外部连接与 GSC 可信度门槛

### 完成

- 核实 SerpAPI、Firecrawl、Tavily、Google Trends、GSC API/异常、Google Search Status、Bing AI Performance 和 PageSpeed/CrUX 的官方能力边界。
- 建立外部来源角色矩阵：SerpAPI=SERP/Trends，Firecrawl=页面采集，Tavily=来源发现，AI=证据综合。
- 新增 `/settings`，统一 AI 与外部数据源设置；原 `/ai` 兼容跳转。
- 密钥输入框不回显；空值保留、显式清除；写入 `.env` 并设置 0600。
- 设置 POST 拒绝非 localhost Origin。
- 新增 SerpAPI Account、Firecrawl Credit Usage、Tavily Usage 与 AI Models 连接测试；不消耗搜索或生成额度。
- 对连接响应只保留白名单字段；网络异常不返回原始 URL，防止 SerpAPI 查询参数泄密。
- 新增 SQLite v2 `source_connections` 无损迁移。
- 新增 `gsc_signal_stability 0.2.0` 规则与 GSC 质量摘要。
- 机会引擎升级为 `cold-start-0.2.0`：独立窗口不足时降低置信权重；日期命中官方异常时曝光相关候选改为补证据。
- 新增 `docs/EXTERNAL_DATA_SOURCES.md` 和 ADR-0004。

### 官方核实中的关键发现

- Google 官方记录：日志错误使 2025-05-13 至 2026-04-27 的 GSC 曝光不准确；修复后曝光可能下降，点击不受该错误影响。
- Google Trends 是归一化的相对兴趣，非绝对搜索量；官方 API 公布时仍为极少测试者的 Alpha。
- GSC Search Analytics API 可组合 query/page/country/device 等维度，但顶部数据和每日行数仍有限制。
- Bing AI Performance 的引用次数不表示排名、权威或在答案中的位置。
- Google 官方流量下降排查未把“沙盒”列为诊断项；系统改用可验证原因树。

### 验证

- `ruff check src tests tools`：通过。
- `pytest`：12 passed；另有第三方 TestClient/httpx 弃用提示，不影响本次结果。
- 真实后台服务重启成功；`/api/health`、`/settings`、`/method`、`/ai` 均正常。
- `/settings` 可读取迁移后的连接状态表；`/method` 显示 `gsc_signal_stability`。
- 真实 GSC 当前被判为 `provisional / 观察窗口不足`，符合两个导入来自同一观察时点的事实。
- Git 状态未包含 `.env`、SQLite 数据库、快照或任何密钥。

### 未完成/下一步

- 尚未创建外部调用与响应的不可变快照、参数哈希、成本和 evidence ID。
- 外部结果尚未参与现有机会分数；连接成功只代表凭据可用。
- 用户需要撤销已在聊天中暴露的旧密钥，再自行在设置页输入新密钥。
- 下一单元优先实现 Top 候选的 SerpAPI SERP + Trends 复核与 `external_runs` 数据契约。
- 用户未明确要求接管当前浏览器；按浏览器技能边界只做 HTTP/模板验收，未截图或点击。

## 2026-07-14 — 项目启动与 M0 可运行里程碑

### 完成

- 在 `/home/laoma/seo-ops-system` 建立独立项目。
- 审核旧 `plan/research/write` Skill、评分脚本、65 篇文章、15 个产品和真实 GSC/CMS 导出。
- 确定新系统是网页形式的决策中枢，不是第四个 Skill。
- 选择 FastAPI + Jinja + SQLite，以适应单人本地运营。
- 建立 README、Handoff、AGENTS、架构、数据契约、方法治理、路线图和 ADR 体系。
- 实现不可变快照、导入批次、GSC 全维度指标、CMS 内容快照、规则版本、机会、行动、观察和 AI 调用表。
- 实现 GSC 标准/对比工作簿解析，CTR 统一以 0–1 小数保存。
- 实现 Blog/Product JSON 解析与幂等导入。
- 实现首版机会规则：点击损失、站内相对 CTR、8–20 位临界页面、查询—页面证据缺口。
- 实现先资格门槛、后排序以及防损/最高价值/补证据组合；允许少于 3 项。
- 实现网页首页、导入、机会、方法与 AI 页面。
- 实现 OpenAI-compatible AI Provider；AI 只解释已给证据，不改指标、门槛和分数。

### 真实数据验收

- GSC 对比工作簿：2,220 条标准化指标。
- GSC 标准工作簿：922 条标准化指标。
- CMS Blog：65 条。
- CMS Product：15 条。
- 首轮分析：32 个候选，2 个进入组合。
- 同一文件重复导入不重复写入。
- 原始旧文件保持不变；新系统保存自己的快照与 SHA-256。

### 验证

- WSL Python：3.12.3。
- `pytest`：7 passed。
- `/api/health`：200，返回 `status=ok`。
- `/`、`/imports`、`/opportunities`、`/method`、`/ai` 与 `/static/app.css`：全部 200，页面标题正确。
- 内置浏览器连接连续两次因桌面沙箱初始化错误中断，未完成截图式视觉验收；服务和页面响应正常。

### 下一步

- 定向 query→page 数据导入。
- 行动执行与 7/28/56 天观察 UI。
- 正文/意图级内容蚕食门槛。
- Opportunity → Research brief 的结构化任务契约。

### 风险/备注

- 不修改旧项目。
- 查询与网页 Sheet 不是联合维度。
- AI API Key 未配置，当前只验证 disabled 模式和适配接口。
- 用户未提供订单/GA4 数据，不输出收入预测。
