# 工作日志

## 2026-07-15 — GSC 活动批次链路复核与旧行动归档修复

### 完成

- 逐表核对 imports、gsc_metrics、analysis_runs、opportunities、actions 与 research_runs，追踪当前工作台、机会、外部调研和执行任务的 GSC 来源。
- 修复当前分析选择：不再只取时间上最新的成功运行，必须匹配唯一活动 GSC 导入；换入新批次但尚未重跑时返回空状态。
- 外部调研复用同一当前分析选择器，防止导入新批次后的空档继续从旧 GSC 查询启动 API 调研。
- 修正真实行动 #1：取消原因此前已写入但 workflow_status 仍误为 in_progress；现已通过正式工作流设为 cancelled。
- 进一步发现 v5 把“当前机会批次”和“可信历史窗口”混为 analysis_active，导致未来正常导入后独立窗口仍永远只有 1。
- 新增无损 v6 quality_eligible：当前机会只看唯一活动批次，质量稳定性累计后续可信历史窗口；明确异常旧批次不参与任何一边。
- 执行页默认隐藏已取消行动，并提供一个“查看已归档”入口保留恢复与审计能力。

### 真实数据核验

- 唯一活动且可信的 GSC 导入为 #5，日期范围 2026-06-22 至 2026-07-14；#1/#2 均为“已排除 · 历史保留”，#5 没有 previous 周期或 2026-06-22 之前的指标。
- 当前分析为 #8，metadata.gsc_import_id=5；17 个当前候选中引用旧 GSC #1/#2 的数量为 0。
- 行动 #1 与旧机会 #169 均为 cancelled；默认执行页不显示旧 $300 任务，归档视图仍可追溯。

### 验证

- 针对 v6 迁移、GSC 导入、质量、机会、调研和行动的 15 项回归测试通过。
- `ruff check src tests tools`：通过。
- `pytest -q`：26 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- 真实数据库无损迁移到 v6，迁移前备份为 `data/backups/seo_ops-pre-v6-trusted-gsc-20260715.db`；#5 标记为当前且可信，#1/#2 均为已排除。
- 真实页面复核：导入页显示当前/排除标签，设置页显示 1 个可信窗口与 2 个已排除；机会、调研、行动和归档页均正常。
- 本单元没有调用 SerpAPI、Firecrawl、Tavily 或 AI，不消耗外部额度。

## 2026-07-15 — 边界扩展七维度补全

### 完成

- 按运营者确认保留七个一级扩展维度，不再增加树的复杂度；将内部检查项补全为受众/角色、场景/目标/生命周期、故障/限制/替代、决策/验证、产品/技术/兼容、法规/安全/地区和相邻站内问题。
- 增加横向筛选器：搜索意图、地区/语言、经验角色、季节/环境条件和内容形式。筛选器只细化研究问题、检索口径和适用边界，不能单独生成图谱节点或文章候选。
- 调研历史目标契约增加横向筛选器快照，使下一轮能够识别相同口径，避免用不同修饰词重复研究同一核心主题。
- 补充 HBR 用户任务地图、Digital.gov 用户旅程以及 Baymard 兼容性和替代/配套产品研究作为方法依据。
- 同步 `docs/CONTENT_WORKFLOW.md`、`docs/TOPIC_GRAPH.md`、`docs/METHOD_GOVERNANCE.md`、ADR-0009、路线图与 Handoff。
- 本单元只调整方法文档；没有修改应用代码、数据库或界面，也没有调用外部搜索或 AI API。

### 验证

- `git diff --check`：通过。
- `ruff check src tests tools`：通过。
- `pytest -q`：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。

### 遗留问题/下一步

- 七个维度、横向筛选器和筛选器快照仍是已确认但未实现的目标；后续随主题图谱和三入口调研一起开发。

## 2026-07-15 — 两阶段内容工作流与边界扩展方法定稿

### 完成

- 根据运营者确认，把第一阶段正式定义为“关键词与主题调研”：内部数据、外部调研和主题图谱联合工作，每轮最多交付 2 篇新文章与 2 篇旧文章建议；没有合格项时不强行补位。
- 将调研入口拆为 GSC 信号、主题图谱缺口和边界扩展三类。查询—页面联合数据仍约束旧页面归因，但不再成为所有新主题研究的前置条件。
- 定义边界扩展引擎：围绕现有分支按新受众、场景/生命周期、故障/限制、决策标准、产品/技术变化、法规/安全变化和相邻站内问题轮换研究，并保存查询、来源、重复及无结果记忆。
- 确认每轮目标预算范围为 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20；预算是上限，缓存不计真实请求，满足停止条件时提前结束。
- 将主题覆盖拆为一个主要文章主题和多个辅助知识主题。安全、波长、功率等只有在构成页面核心任务时才作为主要主题参与完整查重。
- 审查旧 `/home/laoma/seo-workflow` 的 plan、research、write 与写作上下文：保留素材包、SERP 意图、用户痛点、真实来源、链接检查和回溯内链；取消统一 APP/FAQ/视频/CTA、固定字数和链接密度、机械关键词位置、虚构作者/实测及 AI 检测器门槛。
- 形成第二阶段“文章制作”提案：专项素材包、文章专属大纲、草稿/修改稿、自动自审和 CMS 交付包；旧文章分极小修改、局部更新和原主题重写，slug 始终不变。
- 明确内链同时检查本文出链和已有文章回链；外链支持具体事实并优先一手来源；文字重复、意图重复和允许共享的辅助知识分开判断。
- 新增 `docs/CONTENT_WORKFLOW.md`、ADR-0009 与 ADR-0010，确认 ADR-0007；同步主题图谱、架构、数据契约、方法治理、外部来源、项目背景、路线图、README 和 Handoff。
- 修复 ADR-0006 的乱码内容，并明确 ADR-0009 只替代其 GSC-only 种子限制，预算、审计、缓存、来源分工和引用校验继续有效。
- 本单元没有修改应用代码、数据库或用户可见功能，也没有调用 SerpAPI、Firecrawl、Tavily 或 AI。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 当前运行版本仍为 `0.4.1`；文档已明确区分“当前实现”和“已确认但未上线的方法”。

### 遗留问题/下一步

- ADR-0010 的四个运营选择仍待确认：接受后何时开始专项调研、大纲是否可选调整、小修改的默认交付形式、是否需要 AI/自动化说明字段。
- 实现顺序应先做只读主题树和 65 篇文章的主要/辅助主题映射，再做三入口调研、研究记忆、简化卡片和目标预算范围。
- 当前 `/research` 仍只从明确 GSC 查询开始，接受机会仍进入六步 `action-plan-0.3.0`；界面不得把目标方法显示为已经上线。

## 2026-07-14 — 0.4.1 当前 GSC 批次与主题图谱方案

### 完成

- 检查运营者新导入的 GSC 文件：659 条标准化指标，日期范围 2026-06-22 至 2026-07-14；新旧文件在 2026-06-22 至 2026-07-10 的重叠区间一致。
- 按不可变审计原则没有物理删除旧导入；新增 SQLite v5 的 analysis_active，每站点只允许一个 GSC 批次进入当前分析，新导入成功时原子切换。
- 机会引擎、数据质量、首页与调研列表统一使用当前 GSC 批次 / 最新分析，修复旧对比导入被机会引擎优先选择的问题。
- 导入历史新增“当前分析 / 历史保留”用途标记；重复上传旧文件不会把它重新激活。
- 真实数据库迁移到 v5，备份为 data/backups/seo_ops-pre-active-gsc-20260714.db；当前导入为 #5，旧 #1/#2 保留但不参与当前分析。
- 只用当前 GSC 批次创建分析运行 #7：17 个候选，1 个进入组合；旧运行的 32 个候选、2 个组合退出当前工作台。
- 基于旧批次且尚未完成的行动 #1 已取消，机会、基线、执行历史和取消原因仍保留。
- 完成主题图谱方案：界面使用可折叠树，底层使用主父级加 related/别名的概念图；采用“先稳定骨架、后按真实信号增量扩展”，人工操作可选。
- 主题下规划可点击文章、覆盖状态、待确认队列、规范主题去重、接受/拒绝/延后记忆与分支饱和；第一版明确不做自由拖拽的完整图编辑器。
- 新增 docs/TOPIC_GRAPH.md、ADR-0007 与 ADR-0008，并同步架构、数据契约、方法治理、路线图、README、Changelog 和 Handoff。

### 验证

- pytest -q：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- ruff check src tests tools：通过。
- git diff --check：通过。
- 真实新导入 23 个日期行合计 61 点击、9,063 曝光；2026-07-14 只有 1 曝光，按未完整日对待。
- 重启真实服务后 /api/health 返回 version=0.4.1；首页、导入、机会、调研和执行方案页面均为 200。
- 本单元没有调用 SerpAPI、Firecrawl、Tavily 或 AI，不消耗外部额度。

### 遗留问题/下一步

- 主题图谱目前是设计，不是已上线功能。先实现只读树和 65 篇文章映射，再做待确认队列，最后增加可选人工细化。
- 当前只有一个活动 GSC 观察批次，且导出末日未完整，质量状态继续为 provisional；不得用它制造高置信长期结论。
- GSC query→page 定向导入/API、周期快照、正文意图复核和 7/28/56 天观察仍待实现。
- 用户提出的简化日常预算范围 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20 尚未调整；当前界面仍是 0.4.0 的默认 4/3/5/1 与旧硬上限，应在下一次简化调研流程时一并处理。

## 2026-07-14 — 0.4.0 预算化多来源主题调研

### 完成

- 新增每轮可配置预算：SerpAPI/Firecrawl/Tavily/AI 默认 4/3/5/1，硬上限 50/50/100/1；预算、真实请求、成功、失败与 24 小时复用分开记录。
- 新增显式“外部主题调研”流程：从最新分析中的明确 GSC 查询开始，SerpAPI 保存 SERP/Trends，Tavily 发现来源，Firecrawl 定向采集公开页面，Flash 只整理已存证材料。
- 新增 SQLite v4 `research_runs/research_run_items/research_candidates` 和 `multi-source-research-0.4.0`；最多保存 8 个 `needs_evidence/blocked` 候选，不改原分数，不自动发布。
- AI 完整引用校验支持唯一运行号缩写和 `external:<运行号>:<数字序号>`；未知、歧义、伪造引用及无 ID 事实不落入候选。
- 工作台把第一方导入 3/3 与外部连接 4/4 分开，显示已存外部证据；新增 `/research` 页面和导航。
- 新增 ADR-0006，并同步 README、架构、数据契约、方法治理、外部来源、路线图、Changelog 与 Handoff。

### 真实 API 验证

- 完整运行 #1 实际调用 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1：SERP 2 次成功，Trends 2 次无结果；Firecrawl 2 次成功、Reddit 1 次 403；Tavily 5 次成功；Flash 1 次成功。部分失败被标记为 `partial`，成功快照保留。
- 修正 Flash 数字尾缀引用后又做两轮最小复测，每轮 SerpAPI 1、AI 1，Firecrawl/Tavily 0；缓存分别复用 2 和 3 份 SERP，再把剩余 1 次预算用于新的明确查询。
- 三轮合计：SerpAPI 6、Firecrawl 3、Tavily 5、AI 3，均低于用户授权的 50/50/100 搜索上限；AI 三次均为 `deepseek-v4-flash`。
- 最终运行 #3 为 `success`，8 个候选中 6 个来自 AI 的证据整理；持久化引用全部是完整 evidence ID，伪造引用为 0，快照密钥扫描泄漏记录为 0。
- 临时最小预算已恢复为日常 4/3/5/1。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：21 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 真实数据库迁移前备份为 `data/backups/seo_ops-pre-v4-20260714T150803.db`；迁移后 `PRAGMA user_version=4`，原导入与机会数据保留。
- 真实服务 `/api/health` 返回 `version=0.4.0`，AI/SerpAPI/Firecrawl/Tavily 均已配置。
- 中断前已在真实 `/research` 完成桌面交互验收；最终复验时六个页面均为 200、预算值/硬上限正确、560px 响应式规则存在。Windows 浏览器沙箱无法重连，因此 390px 新调研页需后续再做一次真实视觉重跑。

### 遗留问题/下一步

- 仍需 GSC query→page 定向导入/API 和独立周期快照；外部主题只是假设，不能自动过新文章门槛。
- research brief、来源人工评级、正文/意图蚕食门槛和 7/28/56 天观察仍未完成。
- Firecrawl 对部分站点可能返回目标站/套餐权限 403；该失败保留但不产生证据。
- 当前只记录请求单位，不把套餐折算成未知货币成本。

## 2026-07-14 — 0.3.0 自动补证据、新文章候选与执行方案

### 完成

- 新增 SQLite v3 无损迁移：`external_runs`、`evidence_items`、`action_steps`，以及行动工作流状态、方案版本和完成时间。
- 新增用户触发的 SerpAPI Google SERP + Google Trends 时间序列采集；页面候选不能用标题猜查询。
- 每次外部运行保存输入 evidence ID、无密钥参数、请求哈希、脱敏不可变响应快照、SHA-256、大小、调用单位、UTC 时间和安全错误。
- 相同站点/来源/purpose/参数哈希的成功响应在 24 小时内复用，避免重复点击重复消费。
- 新增 `external_query_review 0.3.0` 与 `new_article_candidate 0.3.0`。
- 新文章候选把 GSC/SERP/Trends 事实、CMS 词项重叠程序推断和缺失证据分开；没有 query→page 与人工意图复核时保持补证据，SERP 已有本站页面或强重叠时阻塞。
- 新增“执行方案”页面和 `action-plan-0.3.0`：接受时冻结 evidence ID/指标基线，步骤只能按顺序完成，外部补证据步骤必须找到真实 SERP evidence ID，支持重新打开、取消和恢复，仍不自动发布。
- 静态资源增加版本参数；窄屏导航隐藏原生滚动条并保留横向滑动。
- 新增 ADR-0005，并更新架构、数据契约、方法治理、路线图、外部来源说明、README、Changelog 与 Handoff。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：18 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 真实服务重启后 `/api/health` 返回 `version=0.3.0`；`/`、`/settings`、`/method`、`/opportunities`、`/actions` 全部 200。
- 真实数据库迁移前备份为 `data/backups/seo_ops-pre-v3-20260714T1307.db`；迁移后 `PRAGMA user_version=3`，原数据保留。
- 迁移后真实库 `external_runs=0`、`evidence_items=0`、`action_steps=0`，确认开发验收没有调用已配置供应商或改动真实运营决定。
- 使用独立临时数据库在 1280px 与 390px 浏览器验收机会页和执行方案页：无页面级横向溢出、行动步骤正常折行、窄屏导航可滑动且无可见滚动条、控制台无错误。

### 遗留问题/下一步

- GSC query→page 定向导入/API 和独立周期快照仍未实现；当前新文章候选不会因此自动过门槛。
- Firecrawl/Tavily 尚未从已保存 SERP 定向建立 research pack；AI 尚未生成结构化 research brief。
- 行动实际修改、发布日期与 7/28/56 天测量/干扰录入页面仍待实现。
- 当前只记录 SerpAPI 请求单位，实际货币成本未知，禁止用套餐推算虚构金额。

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

## 2026-07-15 — 主题调研与执行流程 0.5.0

- 物理删除错误 GSC 导入 #1/#2、3,142 条指标、旧分析/机会/调研派生数据与相关快照；当前保留 CMS #3/#4 和干净 GSC #5（659 条）。
- 新增 SQLite v7/v8 主题图谱、研究入口、候选决定与记忆；真实数据同步 119 个节点、80 项主要内容、348 个辅助知识关联，未映射 0。
- 外部调研改为 GSC、主题缺口、七维边界扩展三个独立入口；预算硬上限 10/10/20/20。
- 候选支持值得做、不再推荐、暂时跳过；30 天内不重复推荐暂缓主题。
- 执行页隐藏六步清单，新增一键生成旧文章修改稿或新文章 CMS 内容包；旧文章 Slug 强制锁定。
- 验证：ruff 全量通过；三入口、迁移、外部 API、设置和网页组合 16 passed；执行页专项 3 passed。
- 浏览器视觉自动化因 Windows sandbox helper 初始化失败未能启动；FastAPI 页面集成测试正常。

遗留：

- 推荐组合仍需继续校准到最多 2 新 + 2 旧并保持不足不补位；当前不会强凑四项。
- 内容生成服务已接通与字段校验，但尚未对真实任务消耗 AI 额度生成样稿；首次点击应人工复核事实、链接与 Markdown。

## 2026-07-15 — 0.5.0 推荐与文章制作最终验收

### 完成

- 推荐组合统一为最多 2 篇旧文章 + 2 篇新文章，产品页和诊断线索不占文章席位；任一类信号不足时保持少于 2 篇或为 0，并同步分析摘要中的实际席位数。
- 研究候选经运营者确认“值得做”后才进入新文章席位；泛 FAQ、常见问题集合和大而全指南由 0.5.0 当前规则动态拦截，旧规则版本自动标记 deprecated。
- 文章制作载入真实机会证据、当前页面和站内链接清单；旧文章限制为元数据/局部/同主题重写并锁定 Slug，新文章生成 CMS 八字段并拦截精确标题或 Slug 重复。
- 内链只保留现有站内 URL，外链只保留已存证 URL；每个外部 URL 独立标注官方/研究、社区线索或行业/其他角色，社区来源不得冒充安全、法规或规格权威。
- 工作台、机会页和文章制作页保留简化操作，技术诊断折叠显示，不再要求运营者处理 evidence ID 或六步人工清单。

### 真实数据与额度核验

- 真实库为 schema v8，只保留 CMS #3/#4 与 GSC #5；错误 GSC #1/#2、3,142 条指标、快照和专属派生分析已物理删除。
- GSC #5 有 659 条指标，日期为 2026-06-22 至 2026-07-14；末日不完整且只有一个可信窗口，因此继续标记 provisional。
- 主题树同步 119 个节点、80 项主要内容和 348 个辅助知识关联；当前真实推荐为 2 篇旧文章、0 篇新文章，没有凑数。
- 真实最小调研仅各调用 SerpAPI、Firecrawl、Tavily 和 DeepSeek Flash 1 次，全部成功；模型为 deepseek-v4-flash，本次最终验收没有继续消耗真实 API。
- 真实调研运行 #1 产生 8 个候选，其中泛 FAQ 候选已被当前规则派生为 blocked，其余候选等待运营者决定。

### 模拟流程与判断

- 错误 GSC 场景：可按导入记录单独物理删除；删除后当前分析只读 #5，不再被错误曝光误导，符合预期。
- GSC 信号弱场景：仍可从主题缺口或七维边界扩展启动调研，不形成 GSC-only 死循环，符合预期。
- 无合格机会场景：组合允许 0–4 项并保持 2 旧 + 2 新上限；真实结果 2 旧 + 0 新证明未强填，符合预期。
- 重复/宽泛主题场景：泛 FAQ 无法点击加入建议；具体的功率、波长或安全核心问题不会仅因属于通用知识被误杀，符合主要/辅助主题边界。
- 接受候选场景：只有 passed 机会能建立文章制作任务；接受新主题后席位数同步，旧文与新文分别走正确内容类型，符合预期。
- 内容交付场景：旧 Slug 被程序恢复为原值，虚构内外链被过滤；新文章返回全部八个 CMS 字段和清洗后的 Slug，符合预期。

### 验证

- `.venv/bin/ruff format --check src tests tools`：45 个文件均已格式化。
- `.venv/bin/ruff check src tests tools`：通过。
- `.venv/bin/pytest -q`：40 passed；仅有 Starlette TestClient/httpx 第三方弃用提示。
- 关键页面与业务组合专项测试：18 passed；覆盖导入删除、迁移、三入口、2+2、简化页面、候选拦截和新旧文章内容包。
- `git diff --check`：通过。
- Windows 浏览器沙箱仍无法启动截图式视觉自动化；页面 HTTP、模板内容和窄屏响应式规则已有自动测试，未把截图验收冒充为已完成。

### 遗留增强

- GSC query→page API/定向联合数据与更多独立可信窗口。
- 发布后 7/28/56 天自动提醒、观察录入和规则效果学习。
- 全文语义/文字重复、自动回溯内链、来源人工评级、同源归并与声明—来源逐条校验。

## 2026-07-15 — 真实操作验收暴露的问题

- 运营者访问时发现 8787 仍运行 0.4.1；已停止旧进程并由 Windows Native 隐藏进程托管 0.5.0，health 已确认 0.5.0。该问题说明此前自动测试没有覆盖真实部署版本。
- 运营者运行 RUN #2“主题缺口”：系统自动选择“工程与专业工作”，SerpAPI 2/4、Tavily 5/5、Firecrawl 2/3、AI 1/1，生成 8 个候选；“部分完成”标签没有解释具体失败。
- 运营者在行动 #2 点击“生成修改稿”后失败。AI 调研和连接检测均成功，不能直接归因于连接。
- 该任务输入包含 24,169 字符旧文、80 个站内页面及 54,024 字符链接摘要，固定 60 秒超时存在明显风险。
- content_production 失败不写 ai_runs，网页又吞掉异常并统一提示检查连接，因此本次精确错误不可追溯。
- 本单元没有重试文章生成，也没有继续消耗 AI 额度；问题已登记到 `docs/USABILITY_ISSUES.md`，在真实闭环通过前保持 Open。

## 2026-07-15 — 机会 #367 新文章入口 500 修复

### 完成

- 定位到行动基线把外部调研候选的事实列表误当 GSC 对象读取，导致 `POST /opportunities/367/decision` 抛出 `AttributeError`。
- 新增类型安全的基线指标读取：旧文章继续保留已有 `current` 或 `facts.gsc_query`，新文章事实列表返回空指标。
- 在原有新文章内容包测试中加入真实的事实列表形状，并新增网页 POST 入口回归测试。
- 在当前真实数据库副本重放机会 #367，成功建立行动与 6 个步骤；只读核对真实库仍无 #367 行动，没有半成品。

### 验证

- 新文章服务专项和网页入口专项各 1 passed。
- `.venv/bin/pytest -q`：41 passed；`.venv/bin/ruff check src tests tools`：通过；`.venv/bin/ruff format --check src tests tools`：45 files already formatted。
- 已重启实际 8787 服务，`/api/health` 返回 `status=ok, version=0.5.0`；`git diff --check` 通过。尚需运营者在真实页面复点 #367；旧文章行动 #2 的 U-006 是另一个 P0，仍未解决。

## 2026-07-15 — 0.6.0 文章任务卡与分阶段内容制作（按要求停止测试）

### 完成

- 将“执行方案”收敛为文章任务卡。当前任务只呈现接受、制作、发布三种运营动作；确认发布后从当前列表移入“观察与历史”，并保留重新打开入口。
- 新文章制作改为素材包、大纲与链接计划、前半篇、携带上下文的后半篇、自审/一次修订五个内部阶段；生成物继续输出 CMS 八字段。
- 沿用原 skill 的稳定结构：导语 100–150 个英文词、FAQ 4–5 问、结论 80–120 个英文词；不设置整篇总字数，不虚构作者实测或第一手经验。
- 内链由完整站点索引先筛选最多 12 个真正相关候选，成稿最多 3 个且允许为 0；外链限定为素材包精确 URL，并记录声明、来源角色与 evidence ID。
- 旧文章制作保持原 Slug 与主题意图，也改用筛选后的内链候选；AI 长任务超时改为 150 秒，制作阶段失败会留下脱敏记录并可安全重试。
- 文档版本更新到 0.6.0，架构、内容工作流、ADR、README、CHANGELOG 和交接状态已同步。

### 实际验证状态

- 最终结构和链接规则加入前，相关专项测试曾为 `11 passed`。
- 取消无关内链兜底后，2 个旧测试假数据因假定“必有一个内链”而失败；假数据已调整，但没有复跑。
- 运营者随后明确要求停止测试。因此本单元没有运行最终 `pytest`、`ruff`、语法检查、浏览器、真实 API 或服务重启，不得把源码状态描述为通过验收或已经部署。

### 遗留

- 后续首先验证 `content_production.py` 与测试假数据，再做完整质量检查。
- 重启实际服务并确认 0.6.0；用行动 #2 和机会 #367 各跑一次真实闭环，核对发布后移入历史。
- 发布后 7/28/56 天自动观察提醒尚未实现。
- 旧文章目前仍为一次生成修改稿，没有采用新文章相同的五阶段内部生成。
## 2026-07-16 — 0.7.0 素材确认、可配置 AI 上限与旧写作方法适配

### 完成

- 新增 `material_workflow.py`，在新文章写作前从已有证据生成 A–H 素材清单；整理过程零外部请求、零 AI 调用。
- 关键材料门槛要求用户痛点、权威来源、真实问题、至少两个可追溯来源；敏感主题额外要求官方或研究来源。
- 新增手工素材搜索提示词、任务卡粘贴入口、不可变快照/evidence 记录和内容哈希幂等；未确认素材前禁止写作。
- 新文章改为文章方案、完整初稿、编辑定稿三个正常 AI 阶段；确定性检查不通过时才继续修订。
- 新增 `content_ai_call_limit` 设置：范围 3–10、默认 4、环境变量 `SEO_OPS_CONTENT_AI_CALL_LIMIT`；交付记录实际调用、上限和修订次数。
- 外部主题调研预算继续独立使用 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 整理 0–20，不计入文章调用上限。
- 适配原 `seo-workflow/write` 的文章类型篇幅、稳定结构、动态内外链、CMS 八字段和确定性自审；不迁移虚构作者、第一手经验、未知 URL 和机械填充。
- 旧文章继续走独立的一次生成路径，按元数据/极小修改、局部更新、同主题重写交付，锁定原 Slug 和主要意图。
- 当前任务页保留同卡操作；新文章只增加“是否补充手工素材”这一项必要决定，发布后仍移入观察与历史。

### 模拟与判断

- 素材不足：显示缺失分类，AI 调用保持 0，符合“先补素材再写”。
- 手工补充：保存快照但不调用供应商；同一文本再次提交不增加运行记录，符合幂等预期。
- 素材充足：运营者确认后正常调用 `skill_plan`、`skill_draft`、`skill_edit` 三次并生成 CMS 八字段。
- 自检返工：只在存在阻塞项且未达到上限时调用修订；默认 4 允许一次返工，设置 10 最多允许七次返工，不会为了用额度主动继续。
- 预算隔离：文章调用次数不减少外部调研四项预算，符合两条数据链路分别计量。
- 真实数据库只读检查：行动 #4 的素材预览 ready=true，56 个来源，A–H 均有内容；当前没有待生成的新文章行动，因此未进行真实新文生成。

### 验证

- `.venv/bin/pytest tests/test_action_workflow.py tests/test_settings.py -q`：12 passed。
- `.venv/bin/pytest -q`：42 passed；只有 Starlette TestClient/httpx 第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`：通过。
- `.venv/bin/ruff check src tests tools`：通过。
- `git diff --check`：通过。
- 本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI；已启动 8787，health 返回 0.7.0，并实际核对设置页参数与文章任务页说明。

### 遗留

- 使用第一篇真实新文章核对耗时、模型返回质量、CMS 八字段、FAQ/JSON-LD、内外链和发布后移入历史。
- 7/28/56 天自动观察、GSC query→page/API、全文语义重复、自动回溯内链和声明—来源逐条校验仍待增强。

## 2026-07-16 — 0.8.0 五步顺序页面与单一卡片归属

### 完成

- 将日常界面重构为五步：数据导入、主题调研、文章建议、文章制作、主题图谱；根地址进入第 1 步，“今日工作台”退出主流程。
- 新增 188px 窄侧栏和响应式五步导航；文章建议、文章制作都按旧文章左列、新文章右列排列。
- 第 2 页只保留三种调研入口、本轮预算、运行用量与结果概况；候选标题和决定操作全部移到第 3 页。
- 第 3 页只读取已保存数据且不触发 API；每列最多突出 2 篇、折叠其后 5 篇，仅提供执行、暂时跳过、不再推荐。
- 第 4 页只显示已接受的当前任务；执行后卡片从第 3 页移入第 4 页，确认发布后立即退出当前制作页。
- 调研选择不再创建候选图谱节点；只有文章真实发布并随 CMS JSON 重新导入后，图谱才显示覆盖。
- 导入 GSC 或 CMS 成功后自动重算内部旧文章机会，不调用外部 API；同 URL 多条规则合并展示，已完成旧文等 CMS 与 GSC 都回流后才允许再次评估。
- 修复素材表单误绑定步骤路由；新增清理服务和页面流程回归测试。
- 新增 ADR-0012，并同步 README、架构、内容工作流、方法治理、CHANGELOG、交接与本问题清单。

### 真实清理与重建

- 执行前预览确认会删除 13 个分析运行、222 个机会、4 个行动、3 个调研运行、27 个派生外部运行、22 条证据、11 个 AI 运行、14 条研究记忆、1 个候选节点和 26 个派生快照。
- 实际按该范围完成物理清理；保留 3 个有效导入、80 项内容、659 条当前 GSC 指标、连接设置及永久“不再推荐”决定。
- 重建主题图谱得到 119 个节点、80 个主映射、348 个辅助关联、0 个未映射内容。
- 重新分析得到运行 #1、17 个内部候选；真实第 3 页为 2 篇优先旧文章、0 篇新文章。新文章为 0 是因为清理后尚未重新发起外部调研，不是错误。

### 验证

- `.venv/bin/pytest -q`：45 passed；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`：49 files already formatted。
- `.venv/bin/ruff check src tests tools`：All checks passed。
- `git diff --check`：通过。
- 重启真实 8787 服务后，`/api/health` 返回 0.8.0；根地址正确进入数据导入，五个主页面、设置、方法和样式均返回 200。
- 真实页面核对：文章建议显示 2 篇优先旧文和折叠候选，新文章栏明确为 0；文章制作两栏均为空；主题图谱显示 80 项真实内容映射；调研页没有旧候选或决定按钮。
- 应用内浏览器仍因 Windows 沙盒连接故障无法完成截图式复验；真实 HTTP、45 项页面/服务测试及 1080/820/560px 响应式规则已核对。
- 本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI。

### 遗留

- 用第一篇真实旧文章和第一篇新文章分别核对 AI 质量、CMS 八字段、FAQ/JSON-LD、内外链与发布后退出当前制作页。
- 增加运行前自动方向预览、暂时跳过/不再推荐的撤销入口，并修正主题缺口候选仍引用 query→page 的旧提示。
- 后续实现 7/28/56 天观察提醒、GSC query→page/API、全文语义重复、自动回溯内链和声明—来源逐条校验。

## 2026-07-16 — 0.8.1 侧栏文字换行修复

### 完成

- 定位到基础样式 `.nav-item span { width: 22px; }` 同时命中了图标和文字容器，导致五步中文标题只有约两个字的可用宽度。
- 在工作流侧栏用更精确的选择器把文字容器恢复为自适应宽度、左对齐和单行显示；188px 窄侧栏保持不变。
- 版本升至 0.8.1，使静态样式地址变化，普通刷新即可避开旧缓存。
- 在五步流程测试中加入实际 CSS 响应及 `width: auto`、`white-space: nowrap` 回归检查。

### 验证

- `.venv/bin/pytest tests/test_simplified_workflow.py::test_five_step_navigation_and_one_card_one_home -q`：1 passed。
- `.venv/bin/pytest -q`：45 passed；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools`、`git diff --check`：通过。
- 实际服务已重启到 0.8.1；页面引用 `workflow.css?v=0.8.1`，线上样式包含自适应宽度和禁止换行规则。
- 应用内浏览器仍被 Windows 沙盒连接故障拦截；本轮通过真实 HTTP、CSS 响应和自动回归核对。

## 2026-07-17 — 0.9.0 GSC 只读 OAuth、联合证据与内容质量修复

### 背景与判断

- 运营者真实测试发现：主题调研给出中文候选 #7“绿色与红色激光笔在演示中的可视性对比”，而 CMS #46 已有 `Red vs. Green Laser for Presentations: Why Your “Brighter” Laser Isn’t Visible`；该候选仍进入新文章链路。
- 旧文章与新文章成稿质量均退化。核对后确认关键原因不是五步框架，而是页面汇总被过早解释为写作证据、旧文单次生成缺少独立审校、候选语言未约束、规范主题去重和保存前质量闸门不足。
- 运营者确认 GSC 可自动同步，但 Blog/Product JSON 继续手工导入；2026-06-22 以前的数据必须永久排除；实施前先备份并清理受影响的新旧文章派生数据。

### GSC OAuth 与数据契约

- 新增 installed-app OAuth 2.0 + PKCE + 本机 loopback 接入，唯一 scope 为 `webmasters.readonly`；不支持 API Key。
- 客户端 JSON 与 token 只从本地路径读取；SQLite 仅保存站点属性、权限、可信起始日与同步审计，不保存 client secret、access/refresh token 或授权码。
- 新增 schema v9：`gsc_connections`、`gsc_sync_runs`、`gsc_query_page_metrics`；删除 GSC 导入时联合行级联删除，同步审计保留且 `import_id` 置空。
- 同步只使用 `dataState=final`、`type=web`，按 25,000 行分页，并分别保存 date 汇总、当前最多 28 天、边界内完整上一 28 天和 `date + query + page` 联合行。
- `SEO_OPS_GSC_TRUSTED_START_DATE=2026-06-22` 是所有请求与存储窗口的代码级硬下界；首次同步前不会自动调用 Google。
- 数据导入页增加连接、回调、站点自动匹配、同步状态和一键同步；GSC Excel 明确为无联合证据的后备，Blog/Product JSON 保持手工入口。

### 机会、调研与内容规则

- 页面汇总只生成 `needs_evidence` 诊断；当前 query + page 联合行存在时才允许 CTR/striking-distance 进入执行，click-loss 同时要求当前与上一窗口联合行。
- 旧文制作前再次核对当前 CMS、通过的机会门槛和所需联合行；缺证据时不调用 AI，防止旧状态绕过规则。
- `multi_source_topic_research 0.6.0` 要求读者可见候选语义字段为自然英文；含中文/混合语言候选丢弃，并依次检查 CMS 主要主题、历史候选和本轮候选。
- 修正规范主题别名与停用词，并用真实红绿激光演示重复建立回归；topic_gap/boundary_expansion 不再被错误要求普遍补 query→page。
- 旧文章正常使用“草稿 + 独立编辑审查”，随后按允许修改类型做确定性检查；自然英文、slug/主题锁、内链白名单、URL 唯一、填充和虚构经验为阻塞项。
- 新旧文章最终仍有阻塞项时只在每篇调用上限内定向修订，耗尽后不保存交付包；元数据常见字符区间降为编辑提示，不冒充 Google 硬规则。

### 凭据移动、备份与真实数据清理

- OAuth JSON 从项目根移动到 `.secrets/google/gsc-client-secret.json`；`.secrets/` 与目录权限为 0700，client 文件和 `.env` 为 0600。
- `.gitignore` 覆盖 `.secrets/`、`client_secret_*.apps.googleusercontent.com.json` 和 `*:Zone.Identifier`；client 与未来 token 路径均通过 `git check-ignore -v`。
- 清理前用 SQLite backup API 创建 `data/backups/seo_ops-before-0.9.0-20260717T032918Z.db`；`quick_check=ok`，SHA-256 为 `f74f2fc21bf9dcbfc8d6c8b419cb9f1401eaa81cdf1ca066477226fd86865feb`。
- 预览后删除 1 个分析、18 个机会、2 个行动、1 个调研运行及 8 个候选、12 个外部运行、9 条证据、2 个 AI 运行、4 条研究记忆和 12 个派生快照。
- 保留 3 个不可变导入、659 条 GSC 指标、80 项内容及其原始快照；清理后联合指标、分析、机会、行动、调研、外部证据、AI 与研究记忆均为 0。
- 真实库迁移至 schema 9 后 `quick_check=ok`、外键违规 0；数据库字节检查未发现 OAuth client ID/secret。token 尚不存在，必须由运营者首次授权生成。

### 验证

- `.venv/bin/ruff format src tests tools`：53 files left unchanged。
- `.venv/bin/ruff check src tests tools`：All checks passed。
- `.venv/bin/pytest -q`：完整测试进程退出码 0；`.venv/bin/pytest --collect-only -q -o addopts=''` 收集 51 项。
- `.venv/bin/pytest -o addopts='' --disable-warnings -rA tests/test_gsc_oauth.py`：2 passed。
- 自动回归覆盖 readonly scope、PKCE、token 权限/数据库隔离、可信日期边界、final 数据、分页联合行、无联合证据拒绝旧文、中文成稿不保存、英文候选和真实重复拦截。
- 真实数据库复核：schema 9，`quick_check=ok`，外键违规 0；imports=3、gsc_metrics=659、content_items=80，其余本轮清理目标和 OAuth 表为 0。
- 本轮未调用真实 Google、SerpAPI、Firecrawl、Tavily 或 AI；随后因运营者反馈页面无法打开，启动 0.9.0 后台服务并核对 `/api/health` 为 0.9.0、`/imports` 为 200 且显示 Google 连接入口。首次浏览器授权、真实 GSC 同步和真实新旧文章质量仍需运营者执行。

### 遗留与下一步

- 启动 0.9.0 后在第 1 页完成 Google 授权，确认系统匹配的站点属性正确后再一键同步；确认请求不早于 2026-06-22。
- 用同步后的联合数据重新生成建议，再真实复验 U-006/U-009/U-010；自动测试不能关闭真实使用问题。
- 发布后 7/28/56 天自动观察、全文声明—来源逐条核验和跳过/不再推荐撤销入口仍待后续。

## 2026-07-17 — 0.9.0 真实 OAuth、根站点同步与调研规则二次修复

### 真实故障与判断

- 运营者完成 OAuth 后要求实际检查结果；页面可打开问题处理后，真实导入页显示连接属性为 `https://laserpointerhub.com/p-B017.html/`，最近同步只有 23 条汇总、0 条 query + page 联合行。
- Google Sites API 只读核对发现账号同时拥有根站点 `https://laserpointerhub.com/` 与单页 URL-prefix 属性；旧选择器把两者评为同分，再按字符串选择了单页属性。
- 运营者随后指出调研概况似乎消失且主题仍像课堂激光笔。实际 HTTP 证明调研概况仍在；重复课堂方向来自前次工作流清理误删 `topic_research_memory`，而 CMS 重叠规则只看标题/摘要，没有识别旧文章正文小标题已覆盖的子题。
- 指定“摄影与光绘”真实运行后得到 8 个英文候选，但 CMS 已有 `Laser Pointer Light Painting: The Complete Photography Guide`，其正文小标题明确覆盖相机传感器、基础设置、激光选择、衍射帽和长曝光；`20-60-20 rule` 又偏离激光光绘分支。这证明仅统一英语和标题去重仍不足以保证主题质量。

### 限定范围内的修复

- 保持五步导航、页面归属和调研概况布局不变；只修改 GSC 属性选择、调研记忆、CMS 覆盖和候选方向规则。
- GSC 自动选择现在只接受 domain property 或 URL-prefix 根路径；子路径/单页属性既不能被自动选中，也不能在后续同步时被已保存连接绕过。
- 工作流派生数据清理不再删除 `topic_research_memory`；删除旧研究运行时仅把记忆的 `research_run_id` 置空，避免自动选择立刻重复同一方向。
- `new_article_candidate 0.4.1` 把标题、Slug、SEO 主身份与摘要/SEO 字段/Markdown H2–H6 小标题分开计算；结构化覆盖达到门槛时阻断新建，正文全文不进入 AI 的站点索引载荷。
- `multi_source_topic_research 0.6.1` 增加所选分支相关性门槛；完全偏离本轮分支的相关问题不会写入候选。
- 增加根站点优先、单页拒绝、结构化小标题覆盖、分支偏离、调研记忆保留和原调研概况仍显示的回归。

### 真实数据操作

- 在真实复测前创建 `data/backups/seo_ops-before-e2e-repair-20260717T045608Z.db`；删除错误 GSC 导入及其 23 条汇总、1 个专属分析和快照，保留同步失败审计。
- 将连接属性改回已经 Google Sites API 验证的根站点后真实同步：sync #2 / import #6，实际日期 2026-06-22 至 2026-07-14，5 次 Google 请求，542 条汇总、1,188 条 query + page 联合行。
- 用 #6 真实重算得到 analysis #1：18 个内部机会，第 3 页有 16 个可执行旧文候选并只突出 2 个优先项。
- 删除旧课堂调研运行及其派生外部/AI/候选，但保留 4 条方向记忆；随后通过真实 `POST /research/run` 指定摄影与光绘，RUN #1 实际调用 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1，保存完整成功/失败概况。
- 用新规则离线回放 RUN #1 的 8 个候选：#2/#4/#5/#6/#7 被现有光绘正文覆盖，#8 偏离分支；仅 #1 和 #3 保持 `needs_evidence`。
- 清理候选前用 SQLite backup API 创建 `data/backups/seo_ops-before-candidate-filter-20260717T053729Z.db`；随后仅删除 6 个 pending 候选并把运行/记忆计数同步为 2，不重跑、不再次消费外部 API。

### 实际页面复验

- Windows 侧 `/api/health` 返回 `version=0.9.0`。
- `/imports` 返回 200，显示根属性 `https://laserpointerhub.com/`、可信起始日 2026-06-22、最近同步联合明细 1,188 条；Blog/Product JSON 仍为手工导入。
- `/research` 返回 200，保留 RUN #1、方向“摄影与光绘”、四个供应商用量、失败原因和“保存了 2 个主题线索”。
- `/opportunities` 返回 200，旧文栏 16 个可选/突出 2 个，新文栏只显示 `Common mistakes in laser light painting` 与 `Laser pointer alternatives for light painting`；没有课堂激光笔、中文或上述 6 个错误候选。

### 验证

- GSC/调研/五步流程针对性回归：15 passed。
- 第一次全量测试发现 2 个旧版本号断言仍期待 0.4.0/0.6.0；没有业务逻辑失败。同步为 0.4.1/0.6.1 后重新运行完整测试，55 项全部通过。
- `.venv/bin/ruff format --check src tests tools`：53 files already formatted；`.venv/bin/ruff check src tests tools`：All checks passed。
- 真实库最终复核为 `quick_check=ok`、外键违规 0；imports=4、联合行=1,188、机会=18、调研运行=1、候选=2、行动=0；根属性为 `https://laserpointerhub.com/`，凭据值未写入数据库。
- 应用内浏览器因 Windows 沙箱故障无法截图式自动化；没有改用未授权的浏览器框架，实际页面改由 Windows 侧 HTTP 和自动模板测试核对。

### 遗留与下一步

- 本轮没有再次生成真实 AI 新文或旧文修改稿，因此 U-006/U-009 仍不能关闭。
- U-010 的“中文/重复/偏题调研候选”已用真实 RUN #1 页面复验解决，但词形规则不是完整语义模型，后续仍需观察新的真实样本。
- 下一步只需从当前第 3 页各选一篇旧文和新文进行真实成稿质量复验；不要重新设计五步架构，也不需要重复当前 GSC 或调研调用。
