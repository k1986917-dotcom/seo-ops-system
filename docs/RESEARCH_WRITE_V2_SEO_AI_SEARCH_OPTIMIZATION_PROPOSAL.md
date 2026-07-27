# Research + Write V2：SEO 与 AI 搜索优化建议

状态：独立建议，未实施，未批准为现行规则

建立日期：2026-07-19（Asia/Shanghai）

适用项目：seo-ops-system / LaserPointerHub

关系文档：docs/LEGACY_RESEARCH_WRITE_RESTORATION_HANDOFF.md

## 1. 本文档与旧流程的关系

本文档不是旧 Research + Write 的复原说明，也不取代复原说明。

必须遵守以下边界：

1. 旧 Research + Write 继续按 1:1 复原方案实施和验收。
2. 旧两个 Skill、旧脚本、旧 Context、旧产物协议和旧链接装配链不因本文档发生任何修改。
3. 本文档只能在旧流程完成复原验收后，作为一个可选的 V2 增强模式单独实施。
4. V2 必须写入独立目录、独立版本和独立运行记录，不覆盖旧 material pack、brief、draft、score 或 map。
5. 同一篇文章只能在运营者选定最终版本后执行一次 register，避免重复归档、重复 PLAN feedback 或重复登记链接。
6. V2 不是默认开启的替代流程。旧流程必须始终可以独立运行和回放。

建议采用双模式：

- Legacy mode：原样运行旧 Research R0–R7 与 Write W0–W3。
- Optimized mode：读取 Legacy 的只读快照，在独立工作区生成增强后的证据账、答案简报、草稿和质量报告。

示意：

    Accepted article
          |
          +-- Legacy mode ------> 旧 Research ------> 旧 Write ------> 旧产物
          |
          +-- Optimized mode ---> 旧产物只读快照
                                      |
                                      +--> V2 Research sidecar
                                      +--> V2 Write sidecar
                                      +--> 人工选择最终版本
                                      +--> 单次 register

## 2. 结论先行

如果由我在旧流程基础上优化，优先级不是增加更多关键词、更多提示词或更多固定组件，而是：

1. 把每个事实声明变成可追溯、可复查、带时效与适用条件的 claim。
2. 要求每篇文章具有真实的信息增量，避免只是重新总结现有 SERP。
3. 用用户任务和页面职责控制重复与蚕食，不再主要依赖词项或 TF-IDF。
4. 把文章组织成对人清楚、对搜索与 AI 也容易理解和引用的答案单元。
5. 把内链、产品链接和外链从数量装配升级为关系与声明装配。
6. 增加 Google 生成式搜索、Bing AI 引用和 ChatGPT 引荐的真实测量闭环。
7. 把抓取、结构化数据、产品 feed 与 agent 可访问性列为技术审计项，不冒充运营者可直接完成的动作。

其中，真实信息增量、来源可信度和页面职责清晰度的优先级最高。所谓 AEO、GEO 或 AI 搜索格式技巧只能排在这些基础之后。

## 3. 2026-07-19 官方方法基线

以下内容是官方事实，不是本项目自行推测。

### 3.1 Google

- Google 生成式搜索仍以核心 Search 排名与质量系统为基础；Google 将 AEO/GEO 视为 SEO，而不是一套独立排名机制。
- AI Overviews 和 AI Mode 会使用 RAG 与 query fan-out，但不应因此为每个 fan-out 变体分别制造页面。
- 对生成式搜索最重要的是独特、有用、可靠、非同质化的内容，尤其是第一手经验、独特观点和超出常识的分析。
- 页面要可抓取、可索引、可生成 snippet；正文重要信息应以可见文本存在。
- Google 不要求 llms.txt、AI 专用文本文件、特殊 AI Schema，也不要求把正文机械切成微小区块。
- 结构化数据仍有常规 SEO 价值，但必须与页面可见内容一致，不保证富媒体或 AI 展示。
- Google 的新指南已提供 Search Console Generative AI performance report 作为生成式搜索表现观察入口。

官方来源：

- [Google：Optimizing for generative AI search](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)
- [Google：AI features and your website](https://developers.google.com/search/docs/appearance/ai-features)
- [Google：Helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Google：Using generative AI content](https://developers.google.com/search/docs/fundamentals/using-gen-ai-content)

### 3.2 Microsoft Bing 与 Copilot

- Bing Webmaster Tools 的 AI Performance public preview 可提供总引用次数、日均被引用页面、grounding queries、URL 级引用活动和趋势。
- Microsoft 官方建议继续保证传统 SEO 的抓取、metadata、内链与外部权威基础，同时提升标题、H1、H2/H3、问答、列表、表格和事实表达的清晰度。
- Bing 明确把证据、时效、结构清晰度和跨文本、图片、视频的实体一致性列为 AI 引用可见性改进方向。
- IndexNow 可以帮助 Bing 及参与方更快发现新增、更新或删除的 URL，但它不保证索引、排名或引用。

官方来源：

- [Bing：AI Performance in Bing Webmaster Tools](https://blogs.bing.com/webmaster/February-2026/Introducing-AI-Performance-in-Bing-Webmaster-Tools-Public-Preview)
- [Microsoft：Optimizing Your Content for Inclusion in AI Search Answers](https://about.ads.microsoft.com/en/blog/post/october-2025/optimizing-your-content-for-inclusion-in-ai-search-answers)

### 3.3 OpenAI / ChatGPT Search

- 任何公开网站都有可能出现在 ChatGPT Search，但 OpenAI 不保证排名或展示。
- 若希望正文进入 ChatGPT 的 summary 与 snippet，不应阻止 OAI-SearchBot；站点或 CDN 也需要允许 OpenAI 公布的抓取 IP。
- OAI-SearchBot 与 GPTBot 的职责不同。允许 Search 抓取不等于必须允许模型训练，训练选择应单独决定。
- ChatGPT Search 引荐链接会附带 utm_source=chatgpt.com，可用于观察实际引荐流量。
- ChatGPT shopping 会结合产品和商家 metadata；直接产品 feed 与 Agentic Commerce Protocol 属于另一个商务接入面，必须先通过产品政策、法律、安全和商家资格核查。

官方来源：

- [OpenAI：ChatGPT Search](https://help.openai.com/en/articles/9237897-chatgpt-search/)
- [OpenAI：Publishers and Developers FAQ](https://help.openai.com/en/articles/12627856-publishers-and-developers-faq)
- [OpenAI：Shopping with ChatGPT Search](https://help.openai.com/en/articles/11128490-improved-shopping-results-from-chatgpt-search)
- [OpenAI：Powering Product Discovery in ChatGPT](https://openai.com/index/powering-product-discovery-in-chatgpt/)
- [OpenAI：Commerce policies](https://openai.com/policies/commerce-policies/)

## 4. 旧流程中应保留的优势

V2 不应推倒重来。旧流程中以下部分仍然很有价值：

- Research 与 Write 分离，先证据后写作。
- material pack 作为写作事实边界。
- A/C/E/G 的来源、痛点、案例、引用与真实问题协议。
- GSC、SERP、published index、产品资料和 Context 的合并。
- 不编搜索量、KD、事实、URL、实测或案例。
- 先做蚕食预检，再做成稿终检。
- 内链、产品链接和外链在 validate、draft、post-process、register 中形成完整装配链。
- pre-check、scrubber、scorer、链接验证和人工发布门。
- register 生成回溯内链候选，而不是自动修改旧文章。
- 文件系统断点续跑和可审计产物。

V2 应以这些能力为底座，只对质量判断、答案结构、证据粒度和效果反馈做增强。

## 5. 旧流程的主要优化空间

### 5.1 Research 层

1. Top 5 竞品摘要很容易得到同质化内容，不能自动等于信息增量。
2. 搜索结果中的 URL 与段落有记录，但事实声明与来源之间还不是稳定的一对一或多对一关系。
3. 来源只做 URL 精确去重时，同一底层材料经不同工具发现可能被误当成多份证据。
4. 来源缺少统一的发布日期、抓取日期、适用地区、产品型号、版本和复查日期。
5. query fan-out 目前主要用于找材料，没有形成“一个页面应回答哪些相邻问题、哪些应属于其他页面”的明确边界。
6. 词项相似和 TF-IDF 无法可靠区分同义意图、共享辅助知识与真正重复页面。

### 5.2 Write 层

1. 固定字数、固定组件、固定链接密度和关键词位置容易让不同文章产生相同外形。
2. FAQ、YouTube、Quick Specs、CTA 和 FAQPage JSON-LD 不应对所有文章默认强制。
3. 单一 content score 容易把格式合规误读为内容有价值。
4. 真实来源虽然存在，但最终草稿缺少机器可核对的 claim → evidence 映射。
5. 固定化名作者或任何无法核实的作者、实测和经验信号不能进入可发布的优化稿。
6. “自包含段落”有价值，但不应演变成机械短段落或为 AI 专门切碎内容。

### 5.3 测量层

1. 只看传统 GSC 时，无法单独观察 Bing/Copilot 引用和 ChatGPT 引荐。
2. AI 搜索人工测试若没有固定 prompt、国家、语言、日期和引用 URL，只能算印象，不能算结果。
3. 没有订单或 GA4 时，不能把点击、引用或被推荐推断成收入和转化。

## 6. V2 的不可破坏原则

### 6.1 事实与证据

- 每个可验证事实都要绑定 claim ID 和 evidence ID。
- AI 只能使用本轮传入的 claim/evidence。
- 同一底层网页不因被多个工具发现而增加独立性。
- 高风险安全、法规、产品规格和合规声明优先使用官方或一手来源。
- 事实、来源原文、程序推断、AI 建议和运营者决定分开保存、分开展示。
- 来源冲突时不自动选边，保留冲突与适用条件。

### 6.2 内容与作者

- 不虚构作者、测评者、专家、用户、案例、照片、测量或购买经历。
- 作者与 reviewer 必须是真实个人或真实组织，并能由站点页面或运营者确认。
- 没有第一手测试时，可以写证据型解释或规格型比较，但不能模拟实测口吻。
- 没有独特信息增量时，允许停止、缩小范围或更新旧文，不为填充字数生成通用内容。

### 6.3 页面与主题

- 一个 URL 保持一个主要页面职责。
- fan-out queries 用于补齐同一任务的覆盖，不用于批量制造换说法页面。
- 新页面前先检查 CMS 标题、H1、摘要、小标题、正文和历史决定。
- 共享安全、波长、功率等辅助知识不自动等于重复；主要任务相同则优先回流旧文。

### 6.4 自动化边界

- AI 不重新计算确定性分数，不改资格，不编来源，不自动发布。
- V2 自动检查通过不等于排名、引用或转化保证。
- 技术 SEO 问题可以发现并生成交接项，但在运营者无站点代码权限时不得冒充已修复。
- 任何 OpenAI product feed、Google Merchant Center、IndexNow、robots、canonical 或 schema 模板改动都需要对应权限和单独确认。

## 7. V2 Research 建议流程

### V2-R0：建立只读基线

输入：

- Legacy research-data
- Legacy material pack
- Legacy brief 与 score
- 当前 CMS 快照
- 当前 GSC query + page 证据
- 当前 internal links map 与 product report

输出：

- baseline manifest
- 输入文件路径与 SHA-256
- Legacy 规则版本
- V2 规则版本
- 运行时间、国家、语言和设备口径

V2 不直接编辑这些输入。

### V2-R1：页面资格与技术可见性审计

检查：

- 当前 URL 或计划 URL 是否可索引。
- Googlebot、Bingbot、OAI-SearchBot 是否被 robots/CDN/WAF 阻挡。
- canonical、noindex、snippet controls 和 sitemap 状态。
- 重要正文是否为可见 HTML 文本。
- 重要页面是否至少有一个可抓取站内入口。
- 结构化数据是否与页面可见内容一致。

输出必须分成：

- 运营者可做：标题、正文、链接、可见表格、CMS 字段。
- 技术负责人可做：robots、WAF、canonical、schema 模板、IndexNow、sitemap、ARIA。
- 仅观察：当前无法验证或无权限的项目。

本阶段不得自动修改站点。

### V2-R2：查询、任务与 fan-out map

每篇文章建立一个页面任务图，而不是关键词堆：

- primary user task
- primary search intent
- user state 或决策阶段
- 对象、动作、条件、地区和结果
- 已知 GSC query + page
- PAA、相关搜索和真实社区问题
- Google/Bing/ChatGPT 可观察的相邻问题
- 比较维度、限制条件和后续问题
- 应由本页回答的问题
- 应由现有其他 URL 回答的问题
- 仍缺证据、不应回答的问题

fan-out map 只用于覆盖与边界检查。它不能直接创建多个新页面。

### V2-R3：SERP 与 AI 引用版图

在经典 SERP 之外，保存以下观察：

- AI Overview / AI Mode 可见引用 URL，若当前地区和账户可见。
- Bing AI Performance 的 grounding queries 与被引用 URL。
- ChatGPT Search 固定 prompt 样本中的引用 URL。
- 不同平台是否反复引用同一原始来源。
- 哪些问题当前没有可靠来源或答案存在冲突。

必须记录平台、国家、语言、日期、prompt、是否登录、引用 URL 与截图/原文快照。

这些观察属于时点样本，不得称为稳定排名或全量份额。

### V2-R4：Claim Ledger

建议每条 claim 至少包含：

| 字段 | 含义 |
|---|---|
| claim_id | 本篇唯一事实 ID |
| statement | 可发布的最小事实声明 |
| evidence_ids | 支持它的一个或多个证据 |
| source_url | 原始来源 URL |
| source_role | 官方、研究、厂商、社区线索或其他 |
| source_excerpt | 支持该事实的原文片段 |
| published_at | 来源发布日期，若有 |
| captured_at | 本次抓取时间 |
| jurisdiction | 国家或法规适用范围 |
| product_scope | 型号、版本、波长、功率或产品范围 |
| confidence | 证据质量、直接性、独立性和新鲜度 |
| contradiction | 是否存在冲突来源 |
| allowed_use | 可用于正文、表格、FAQ、产品页或仅研究 |
| review_at | 何时复查 |

禁止只保存 AI 摘要而没有原始来源与原文片段。

### V2-R5：Information Gain Gate

每篇 V2 新文章至少要回答：

“这篇文章提供了什么是现有结果、通用模型或本站旧文章没有直接提供的？”

可接受的信息增量包括：

- 真实第一方测量和可复现方法。
- 运营者实际使用、维修、拍摄或比较的证据。
- 带来源和统一口径的产品规格比较。
- 法规或安全要求的地区、型号、功率和日期矩阵。
- 原创照片、标注图、视频或失败诊断流程。
- 基于真实数据构建的计算器、检查表或决策表。
- 多个可靠来源之间的冲突解释与适用边界。
- 对本站既有文章和产品关系的独特综合。

不算信息增量：

- 改写 Top 5。
- 汇总常识。
- 把同一问题换成长尾标题。
- 增加没有证据的建议。
- 用更长篇幅重复同一答案。
- AI 自动生成但无法验证的经验和案例。

没有信息增量时，V2 应选择：

1. 更新已有页面；
2. 缩小问题范围；
3. 等待运营者补真实材料；
4. 暂不制作。

### V2-R6：页面职责与重复判断

重复判断分三层：

1. 文字层：近重复段落或模板重复。
2. 主题层：对象、任务、条件和结果是否相同。
3. 页面职责层：用户完成这项任务时是否需要另一个独立页面。

只有页面职责不同才支持新建。关键词不同、角色称呼不同或辅助知识不同不能单独证明新页面资格。

AI 可以给出相似页面候选和理由，最终资格仍由程序规则与运营者确认。

### V2-R7：Answer Brief

V2 brief 不只列 H2，还应包含：

- 页面唯一职责。
- 一句话核心答案。
- 适用对象与不适用边界。
- 必答问题与后续问题。
- claim/evidence 映射。
- 独特信息增量。
- 推荐表格、步骤、图片、视频或计算器。
- 内链关系。
- 产品链接使用条件。
- 外部来源对应的具体声明。
- 作者/reviewer 的真实身份要求。
- 更新时间与复查触发器。
- 适用的结构化数据类型。

大纲结构按用户任务决定，不预设每篇都要 FAQ、视频、CTA 或相同 H2 数量。

## 8. V2 Write 建议流程

### V2-W0：输入与身份校验

写作前确认：

- Answer Brief 完整。
- Claim Ledger 无未知或伪造 evidence ID。
- 作者或组织身份可核验。
- 涉及安全、法规和技术规格的声明有适用的一手来源。
- 产品链接的目标产品与本篇任务相符。
- 选定 URL 不与现有页面职责冲突。

任一阻塞项失败则停止写作。

### V2-W1：任务型大纲

按文章任务选择结构：

- 定义/解释：直接答案、边界、原理、误区、下一步。
- How-to：前置条件、步骤、检查点、故障分支、安全限制。
- 比较/选择：适用场景、统一维度表、取舍、排除条件、建议。
- 故障诊断：症状、原因树、验证步骤、何时停止、升级路径。
- 法规/安全：适用地区、日期、定义、限制、官方来源与免责声明。
- 产品评测：方法、样本、结果、限制、适用人群；没有实测则不得称为 review。

### V2-W2：写作与答案单元

正文仍以人类阅读体验为主，但应做到：

- Title、SEO description、H1 和首段表达同一个页面职责。
- 在适合的位置先给明确答案，再给理由、证据和边界。
- H2/H3 使用具体问题或任务名称，避免 Learn More 一类模糊标题。
- 比较使用统一口径的表格；步骤使用编号；条件和例外不藏在长段落中。
- 每个重要事实附近能找到支持来源。
- 句子脱离上下文后仍不歪曲事实，但不为 AI 机械切碎文章。
- 产品型号、单位、地区、时间和术语在正文、图片、视频、表格中保持一致。
- 重要信息不能只存在于图片、视频、折叠面板或 PDF。
- 不为覆盖 fan-out 变体机械重复关键词。

### V2-W3：真实信任信号

可发布稿必须：

- 使用真实 byline 或准确的 Organization。
- 有实际测试时说明测试对象、日期、方法、样本和限制。
- 有 AI 辅助且读者合理需要知道时，提供适合该站点的制作说明。
- 显示原始发布日期与真实修改日期；没有实质更新时不伪造新鲜日期。
- 对高风险内容显示适用边界和官方来源。
- 不把第三方社区语言写成本站用户反馈。

### V2-W4：结构化数据建议

结构化数据只在与页面可见内容一致时生成：

- 博客文章：BlogPosting 或 Article。
- 面包屑：BreadcrumbList。
- 产品购买页：符合条件时使用 Product / Merchant listing。
- 真实视频：符合条件时使用 VideoObject。
- FAQPage：只有页面存在真实可见 FAQ 且当前政策适用时才考虑。
- 作者：Person 或 Organization 必须与页面真实身份一致。

V2 不创建 AI 专用 Schema，也不把 llms.txt 当作 Google 优化任务。

### V2-W5：双层质量检查

确定性检查：

- URL、canonical 和链接可达性。
- HTML 标题层级。
- Title、H1、description 与页面职责的一致性。
- claim 是否都有允许使用的 evidence。
- 表格单位与产品型号一致。
- schema 与可见正文一致。
- 作者、日期和图片字段存在且真实。
- 内链、产品链接和外链均来自批准候选。
- 是否残留占位符、未知数据或旧化名。

编辑/AI 检查：

- 是否真正回答用户任务。
- 是否有可识别的信息增量。
- 是否遗漏关键限制或反例。
- 是否把建议写成事实。
- 是否过度模板化、重复 CTA 或泛化表达。
- 是否与现有 URL 发生页面职责冲突。

V2 不使用一个总分代替这些门槛。可以展示 readiness 状态，但不得把它解释为排名预测。

### V2-W6：交付包

最终交付包括：

- CMS 必填字段。
- 最终 Markdown。
- Claim → Evidence 清单。
- 内链、产品链接和外链清单。
- 图片、视频与 alt text 建议。
- 适用结构化数据建议。
- 修改说明。
- 需技术负责人处理的项目。
- 发布后观察计划。

发布仍由运营者人工完成。

## 9. 内链、产品链接与外链的 V2 优化建议

本节只描述 Optimized mode。Legacy mode 继续使用旧动态上下限和原装配链。

### 9.1 总原则

V2 不把链接数量当排名公式。Google 官方明确表示不存在神奇的理想链接数量。

每条链接必须回答两个问题：

1. 读者为什么在这里需要这个目标页面？
2. 这条链接帮助搜索系统理解什么页面关系或事实来源？

### 9.2 内链

Research 阶段为每个候选保存：

- target URL
- target page responsibility
- source page responsibility
- relationship：hub、cluster、prerequisite、next step、comparison 或 product support
- reader need
- suggested context
- canonical status
- existing anchor examples

Draft 阶段：

- 只在上下文真实需要时嵌入。
- 使用简洁、描述性、自然锚文本。
- 不使用 click here、read more 或机械关键词锚文本。
- 不把多个链接并排堆放。
- 每个重要页面至少应从站内另一可抓取页面获得入口；孤儿页单独报告。

Register 阶段：

- 继续生成回溯候选。
- V2 候选按页面关系、上下文适配、当前链接数量和是否已包含目标 URL 排序。
- AI 读取旧文后给出自然插入句和位置。
- 不自动编辑旧文。

### 9.3 产品链接

只有满足以下条件才插入：

- 产品确实帮助用户完成本篇任务。
- 型号、规格、可用性和目标 URL 可核验。
- 正文已解释选择条件，不是无上下文 CTA。
- 安全、法律或地区限制已显示。
- 不把产品页当作事实来源，除非该声明确实是厂商/商家的第一方产品事实。

产品链接数量由读者任务决定，不为达到密度强塞。

### 9.4 外链

外链应绑定具体 claim：

- 优先原始官方、标准、研究或第一方产品资料。
- 链接周围说明该来源支持什么事实。
- 社区、论坛和评价只支持用户语言、问题或市场线索，不单独支持法规、安全和规格事实。
- 记录抓取时间、来源日期、适用地区和失效条件。
- 定期检查 404、重定向、内容替换和过期版本。

### 9.5 链接验收

- 不存在编造 URL。
- 不存在泛锚文本。
- 不存在与页面任务无关的产品推销。
- 事实外链能回到 Claim Ledger。
- 内链目标职责明确且不是重复 URL。
- 回溯链接仍由人工批准。
- 链接不足可以是正常结果；来源不足则是证据阻塞，不用无关链接补数量。

## 10. AI 搜索可见性的技术建议

这些建议需按权限分流。

### 10.1 运营者当前可执行

- 提升正文答案、标题、H1、表格和链接清晰度。
- 保持真实作者、日期、产品型号和来源。
- 在 CMS 能力范围内补充可见文本、图片 alt、内部链接和外部引用。
- 导出或记录 GSC 生成式搜索表现。
- 记录 ChatGPT 引荐和 Bing AI Performance 数据，前提是账户可用。

### 10.2 需要技术负责人

- 核查 Googlebot、Bingbot、OAI-SearchBot 的 robots、CDN 与 WAF。
- 核查 sitemap、canonical、noindex、snippet controls。
- 配置 Article、Product、Breadcrumb 等适用 schema。
- 配置 IndexNow。
- 保证关键内容存在于服务端或可渲染 HTML。
- 改进 ARIA、表单和交互元素，支持浏览器 agent 正确理解页面。

系统只能登记这些问题和生成交接清单，不能声称已经执行。

### 10.3 暂不列为默认任务

- llms.txt。
- AI 专用 Schema。
- 为每个 conversational query 建独立页面。
- 购买虚假品牌提及或外链。
- 批量重写旧文以制造“AI 友好”措辞。
- 未完成政策核查的 OpenAI product feed 或 agentic commerce 接入。

LaserPointerHub 涉及安全和地区法规。任何 Commerce feed 必须先核查具体产品是否满足 OpenAI Commerce policies、目标地区法律、平台条款和产品安全要求；不得默认全站商品都具备资格。

## 11. 效果测量建议

### 11.1 传统 SEO

- GSC query + page impressions、clicks、CTR、position。
- 索引状态与 canonical。
- 新旧页面之间的查询重叠变化。
- 7/28/56 天窗口。
- 同期站点级、季节性、算法更新和数据异常。

### 11.2 Google 生成式搜索

- Search Console Generative AI performance report 中的曝光、点击、页面和查询维度，以实际可导出字段为准。
- 不从普通 Web 报告反推某次点击一定来自 AI Overview。
- 不把“可见于 AI 功能”解释为稳定排名。

### 11.3 Bing / Copilot

- Total citations。
- Average cited pages。
- Grounding queries。
- Page-level citation activity。
- 引用趋势。

Bing 当前将该功能标为 public preview，数据覆盖与账户可用性需要如实记录。

### 11.4 ChatGPT

- utm_source=chatgpt.com 引荐会话。
- 引荐 URL、目标页和日期。
- 固定 prompt 观察集中的引用 URL。
- OAI-SearchBot 抓取是否成功，前提是有日志或技术方协助。

手工 prompt 观察必须记录国家、语言、日期、登录状态和完整 prompt，只作为时点样本。

### 11.5 不允许的结论

- 没有订单数据时，不输出收入或销售提升。
- 没有 GA4 时，不输出参与度或转化结论。
- 引用次数不等于排名、权威或推荐强度。
- 单次 ChatGPT、Copilot 或 Google AI 回答不代表稳定可见性。
- 发布、抓取、索引和被引用是四个不同阶段。

## 12. 建议实施顺序

### Phase 0：先完成 Legacy 复原

- 完成原复原方案 A–F。
- 用三类黄金样例通过 Legacy 验收。
- 冻结 Legacy 版本、hash 与产物。

未完成 Phase 0，不开始 V2 代码实施。

### Phase 1：只做 V2 数据协议

- 定义 baseline manifest。
- 定义 Claim Ledger。
- 定义 fan-out map。
- 定义 Answer Brief。
- 定义 V2 quality report。
- 定义实际效果字段。

本阶段不接网页、不自动调用 AI、不修改旧脚本。

### Phase 2：命令行 Research sidecar

- 从 Legacy 产物生成 V2 baseline。
- 运行资格审计、fan-out map、Claim Ledger、information gain gate、职责查重和 Answer Brief。
- 选择三类黄金样例做对照。

### Phase 3：命令行 Write sidecar

- 生成独立 V2 draft。
- 执行 claim、身份、链接、schema 和页面职责 QA。
- 输出 V2 交付包。
- 不运行 register，直到运营者选择 Legacy 或 V2。

### Phase 4：网页可选模式

- 页面明确显示 Legacy 与 Optimized 两种模式。
- 默认保持 Legacy。
- V2 所有产物使用单独目录和版本。
- 运营者可对比两稿、选择一稿。
- 只允许选中的一稿执行一次 register。

### Phase 5：真实试点

至少选择：

- 一个 Cluster。
- 一个 Pillar。
- 一个 Product Roundup 或比较页。

逐篇人工核对：

- 事实与来源。
- 信息增量。
- 页面职责。
- 链接装配。
- 作者与经验。
- 结构化数据建议。
- 发布后 7/28/56 天结果。

### Phase 6：根据真实结果决定是否扩大

只有当 V2 稳定提高材料可信度、编辑可用性或实际搜索/AI 可见性，才考虑扩大。不得仅因 V2 产物更长或检查更多就认定更好。

## 13. V2 验收标准

- [ ] Legacy 复原文档、旧 Skill 和旧脚本未被修改。
- [ ] Legacy mode 可独立运行并产出原格式结果。
- [ ] V2 只读取 Legacy 快照，产物写入独立目录。
- [ ] 每个事实 claim 可追溯到原始 evidence。
- [ ] 同一底层来源不会被多工具重复计为独立证据。
- [ ] 每篇新文明确记录真实信息增量；无增量时允许停止。
- [ ] fan-out questions 未被机械拆成多个 URL。
- [ ] 新建/更新判断以用户任务和页面职责为主。
- [ ] 优化稿不存在虚假作者、经验、测量、案例或日期。
- [ ] 内链、产品链接和外链均有明确关系或声明依据。
- [ ] 没有为达到链接数量强塞无关链接。
- [ ] schema 与可见内容一致，且不承诺富媒体或 AI 展示。
- [ ] OAI-SearchBot 与 GPTBot 的选择分开记录。
- [ ] 技术 SEO 建议与运营者可执行动作分开。
- [ ] Google、Bing 和 ChatGPT 指标按真实数据源分别记录。
- [ ] 最终仍由运营者人工发布。
- [ ] 同一任务只执行一次 register。
- [ ] 真实试点完成 7/28/56 天观察后再决定扩展。

## 14. 建议登记但暂不激活的规则

以下只是 proposed 规则，不进入当前 rule_versions：

| rule_key | 类型 | 主要证据 | 作用 | 建议复查 |
|---|---|---|---|---|
| ai_search_technical_eligibility 0.1-proposal | official + governance | Google/OpenAI/Bing | 区分可抓取、可索引、可引用 | 2026-10-19 |
| claim_level_traceability 0.1-proposal | governance | 官方质量指南 + 项目证据原则 | 事实绑定原始证据 | 2026-10-19 |
| non_commodity_information_gain 0.1-proposal | official + editorial | Google AI optimization/helpful content | 阻止同质化新文 | 2026-10-19 |
| page_responsibility_dedup 0.1-proposal | program inference | CMS + GSC + SERP | 新建与更新分流 | 2026-10-19 |
| relationship_based_links 0.1-proposal | official + editorial | Google link guidance | 由关系与声明决定链接 | 2026-10-19 |
| ai_visibility_measurement 0.1-proposal | official fact | GSC/Bing/OpenAI | 分平台观察真实结果 | 2026-10-19 |
| commerce_feed_policy_gate 0.1-proposal | policy | OpenAI commerce policies + applicable law | 阻止未经核查的 feed 接入 | 2026-09-19 |

采用任何 proposed 规则前，必须按 METHOD_GOVERNANCE 登记版本、证据等级、理由、失效条件、来源、生效日期与复查日期。

## 15. 下一位 AI 的执行提示

若运营者未来批准实施 V2，下一位 AI 应：

1. 先确认 Legacy 复原是否已经全部验收。
2. 重新读取 AGENTS.md、HANDOFF.md、METHOD_GOVERNANCE、Legacy 复原文档和本建议。
3. 不修改旧 Skill、旧脚本和 Legacy 产物协议。
4. 先提交 Phase 1 数据协议与三类黄金样例，不先接网页。
5. 明确列出哪些建议是官方事实、程序推断、编辑策略和实验假设。
6. 若实现会触发 register、归档、PLAN feedback、产品 feed、robots 或外部发布，必须先核对权限和单次副作用。
7. 用真实文章试点和真实结果决定是否继续，不以检查数量或生成长度自证有效。

## 16. 最终定位

Legacy mode 的目标是“忠实复原旧工作流”。

Optimized mode 的目标是：

- 让内容拥有真实、独特、可验证的信息增量；
- 让搜索引擎更清楚地理解页面职责与站内关系；
- 让 AI 搜索更容易准确提取、引用和链接具体答案；
- 让运营者能从 Google、Bing 和 ChatGPT 的真实数据判断是否有效；
- 同时不牺牲事实边界、作者真实性、人工发布和项目方法治理。

这才是本项目中 SEO 与 AI 搜索优化应追求的升级方向，而不是增加一套面向 AI 的关键词模板。
