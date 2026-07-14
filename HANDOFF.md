# Handoff — 当前接手状态

最后更新：2026-07-14（Asia/Shanghai）

## 一句话状态

`0.2.0` 已运行：首个网页闭环、真实 GSC/CMS 数据、机会分析、统一 AI/外部来源设置和 GSC 稳定性门槛均已贯通；外部供应商目前完成“安全配置与连接检测”，尚未进入自动 SERP/研究执行层。

## 已确认的业务目标

- 用户是单人网站运营者。
- 可执行权限主要是发布、修改、删除文章和产品页面，以及自行决定文章主题。
- 第一方输入是 GSC Excel、博客 JSON、产品 JSON。
- 系统输出不是“尽量多写文章”，而是每天/每周最值得做的少量动作及其证据。
- 人工保留发布、删除及风险内容的最终决定权。

## 当前实现范围

- [x] 新项目与可交接文档体系
- [x] FastAPI + Jinja + SQLite 本地网页
- [x] 不可变文件快照、SHA-256 与幂等导入
- [x] GSC 标准/对比工作簿和 CMS blogs/products JSON
- [x] 资格门槛优先的机会引擎与 Top 3
- [x] OpenAI-compatible 可选 AI Provider
- [x] `/settings` 统一 AI、SerpAPI、Firecrawl、Tavily 与 Trends 口径
- [x] 密钥不回显、`.env` 0600、跨站设置提交拦截、连接结果脱敏
- [x] 免费账户/用量/模型列表连接测试
- [x] GSC 独立观察窗口、官方异常与置信权重门槛
- [x] SQLite v1→v2 无损迁移（`source_connections`）
- [x] 12 项自动测试与真实页面响应验收
- [ ] 外部 SERP/Trends/网页响应的不可变快照与证据对象
- [ ] 浏览器截图式视觉验收（用户未明确授权接管当前浏览器）

## 已核实的真实输入与当前状态

旧项目只读路径：`/home/laoma/seo-workflow/laserpointerhub/raw`

- GSC 标准导出：922 条标准化指标。
- GSC 对比导出：2,220 条标准化指标。
- Blog：65 条；Product：15 条。
- 首轮旧方法分析：32 个候选，2 个进入组合。
- 当前真实 GSC 质量状态：`provisional / 观察窗口不足`。两个导入属于同一观察时点，不算两个独立时间窗口。
- 可信度门槛加入后，单窗口的高置信 GSC 候选会降为中置信；日期命中官方异常时，曝光相关项改为 `needs_evidence`。
- 当前没有把用户在聊天中提供的任何密钥写入项目或发起调用；这些密钥必须先撤销并重新生成。

## 外部来源的准确状态

已实现：设置、立即生效、重启持久化、清除、连接测试、状态记录与方法边界展示。

未实现：SerpAPI/Trends/Firecrawl/Tavily 的真实结果还没有参与机会分数或 AI 解释。下一步必须先新增 `external_runs`、原始响应快照、参数、哈希、用量和 evidence ID，禁止直接把供应商 JSON 塞进分数。

角色边界见 `docs/EXTERNAL_DATA_SOURCES.md` 与 ADR-0004。

## 关键方法边界

- GSC 是核心第一方证据，但不是无误差真相；聚合、顶部行限制、不完整数据和官方日志异常必须进入门槛。
- “Google 沙盒”不是自动诊断。先查官方数据异常、排名更新、技术问题、季节性和 SERP 变化。
- SerpAPI 证明某时某口径的 SERP；Trends 证明相对兴趣；Firecrawl 证明页面公开内容；Tavily 只发现资料。
- 多个工具重复同一网页或摘要，不增加独立置信度。
- 普通 GSC 查询表与网页表不是联合维度；需要定向导出或 GSC API。
- 没有订单/GA4 数据时，不输出预期收入或查询级转化。
- AI 不修改指标、规则、资格门槛或分数，不自动发布。

## 怎样运行

```bash
cd /home/laoma/seo-ops-system
source .venv/bin/activate
seo-ops
```

当前后台服务：`http://127.0.0.1:8787`。测试：`pytest`；质量检查：`ruff check src tests tools`。

设置页：`http://127.0.0.1:8787/settings`。只输入重新生成后的新密钥；保存后再点对应“测试连接”。

## 下一位接手者的第一步

1. 先运行 12 项测试并打开 `/settings`、`/method`、`/opportunities`。
2. 用户轮换密钥后，由用户在设置页输入；不要从聊天或历史日志复制。
3. 实现 `external_runs` + 不可变响应快照。
4. 只对 Top 候选调用 SerpAPI SERP 与 Trends，再定向调用 Firecrawl；Tavily 只补来源发现。
5. 同时实现 GSC 定向 query→page 导入/API 与周期快照，逐步把 `provisional` 升为可用状态。
6. 然后再把合格机会转换为 research brief；不要先扩展 AI 长文写作。

## 已知风险

- `.env` 是权限为 0600 的本地明文文件，不是加密保险库；不要把应用开放到局域网或公网。
- SerpAPI Account API 的原始响应包含 API Key；当前代码只保留白名单字段，维护时不能改为保存整个响应。
- 当前 GSC 导出还没有足够独立历史窗口，页面机会只能视为冷启动诊断。
- 旧博客正文中存在历史编码异常字符，导入按 UTF-8 原样保留。
- 商品 JSON 没有完整 canonical URL，目前按 `/products/{slug}` 推导，允许人工修正。
- 浏览器技能因用户未明确要求接管浏览器而未执行点击/截图；HTTP 与模板安全测试已完成。
