# ADR-0026：章节合同写作与链接机会门禁

- 状态：Accepted
- 日期：2026-07-31
- 适用范围：Legacy R3→W0、W1b/W2 修订、文章/产品内链、外部证据引用
- 取代方式：扩展 ADR-0025，不废除其紧凑 evidence hand-off 和严格门禁

## 背景

当前 W0/W1b/W2 已将正文与 claim ledger 拆成不同 AI 任务，但正文仍按整篇生成，
claim ledger 仍可能一次处理大量全文句子和重复证据上下文。真实 Action #3 已证明：
正文修订在关闭 DeepSeek thinking 后能够成功，而后续 claim-ledger 请求仍可能把
8000 个输出 token 全部消耗在 reasoning 中，最终 `content` 为空。

链接规则目前主要依赖字数比例、全文位置和数量上下限。这能发现明显堆砌，却不能判断：

- 一个章节是否真的存在文章内链或产品内链机会；
- 产品链接是否出现在读者已完成需求判断之后；
- 链接候选是否与当前章节、库存和产品属性匹配；
- AI 返回 0 个链接是合理选择，还是因为上下文无效或模型偷懒。

不能通过简单增加 token、放宽 evidence/claim ledger、强制全文平均分布链接，或允许 AI
自由生成 URL 来解决。

## 决定

### 1. 使用全局蓝图与章节合同

R3 确定性地产生可重建的 Article Blueprint。每个主体 H2 对应一个 Section Contract，
至少包含：

- 章节 ID、标题、读者问题、章节职责和目标字数；
- 必须回答、禁止重复、前后章节关系；
- 允许的 evidence IDs；
- 文章内链、产品分类、具体产品候选；
- 章节读者阶段：`discover`、`understand`、`compare`、`select`、`apply`、`verify`；
- 是否允许商业链接及理由。

正文按 H2 章节分别生成。每次只传入精简全局结论、当前章节合同、当前章节证据、少量
链接候选、上一节短摘要和下一节目标。Introduction、Key Takeaways、Conclusion 和 FAQ
在主体章节完成后依据实际正文生成。article-frame 的 canonical 数量边界为 Key
Takeaways 3–5 条、FAQ 3–4 条；生成 prompt、package validator 与最终 assembly 必须使用
同一边界，禁止前序允许而后序确定性拒绝。

### 2. 链接机会先于链接数量

链接最低值不固定为 0，也不按字数机械计算。服务端先进行 Link Opportunity Gate：

- `required`：存在通过验证的高相关候选，且章节职责需要读者继续学习或完成选择；
  本节对应链接类型最低值为 1；
- `recommended`：候选相关但不是完成本节任务的必要步骤；最低值可为 0，但 AI 必须
  返回采用或拒绝原因；
- `none`：没有有效候选，或章节明确禁止该类链接；最低值为 0，并保存机器可读
  `reason_code`。

文章内链与产品内链分别计算，不能互相替代。

### 3. 文章内链和产品内链承担不同职责

- 文章内链回答“还需要了解什么”，适用于背景、比较、安全、使用和故障等扩展知识；
- 产品内链回答“有哪些相关商品可继续查看”，只允许出现在 `compare`、`select`、
  `apply` 或明确的解决方案章节；
- 产品分类页用于需求已明确但不能唯一确定 SKU 的场景；
- 具体产品页必须在售、数据无冲突并与文章主题或当前章节内容相关；不要求每个商品都与
  细分用途完全一致，否则小目录网站会长期没有产品可推荐；
- 产品候选分为 `strong`、`contextual`、`related_catalog` 和
  `approved_constraint`。前三者均可用于商业阶段链接；只有 `strong` 或明确结构化约束
  才能表述为具体场景匹配，`related_catalog` 只能称为相关目录选项，不得宣称专为该
  场景设计、经过该场景验证或符合某项法规；
- 在 `compare|select|apply` 章节，只要存在数据有效且内容相关的产品候选，产品链接门禁
  使用 `required + min_required=1`，防止模型长期以 0 链接偷懒；
- 安全警告、法规说明、事故分析等章节默认禁止具体产品链接；
- 零产品链接可以是合法结果，但必须来自机会门禁，而不是模型自行省略。

### 3.1 产品目录优先于旧 brief

产品策略的权威顺序固定为：

```text
站点配置与真实在售目录
→ 已批准的结构化产品约束
→ 文章主题和章节职责
→ 旧 Research Brief 建议
```

旧 brief 的自然语言不得自动升级为产品过滤规则。若旧 brief 写着“只推荐某规格”，
但站点真实目录没有该规格，系统必须产生 `brief_catalog_alignment_pending` 或
`prescriptive_brief_catalog_mismatch`，要求调整文章角度；不得据此排除网站主营商品。

只有来源为 `site_policy`、`catalog_policy` 或 `operator_approved` 的结构化约束，才可
过滤产品候选。核心候选注册器只要求产品 ID、标题/名称和 URL；喷码机、园林工具、
激光产品等其他属性一律作为通用 catalog attributes 保存，不能在核心代码中硬编码
某个行业的 Power、Wavelength、Battery Platform 等字段。

### 3.2 产品目录错误必须显式报告

若同一产品的标题、目录表格和详情字段互相矛盾，系统必须产生独立逻辑区段的
Catalog Data Quality Report，并随持久化 sectional shadow report 保存，至少记录：

- 产品 ID、标题、URL；
- 冲突字段及各来源值；
- `severity=error`；
- `blocking_for_auto_link=true`；
- 可执行的修复建议。

该 SKU 在修正并重新同步前不得自动作为具体产品链接候选，但其他商品仍可正常使用。
数据错误与“文章想写的条件在目录中不存在”必须使用不同 reason code，避免运营者不清楚
应修产品页还是改文章策略。

### 3.3 正式内容语言固定为 English

当前站点合同固定 `content_language=en`。Article Blueprint、Section Contract、正文、
链接锚文本和最终输出都必须为英文。旧研究文档中的中文备注只可进入审计字段
`brief_points_rejected`，不得进入正式 AI 写作上下文、产品过滤或链接决策。

混合标题中纯注释性的中文括号可在重建时移除；仍包含中文的 H2 必须 fail-closed，先改写
成英文后才能进入章节合同。

### 4. AI 只选择占位符，服务端绑定真实性

写作模型只能输出批准的占位符，例如：

```text
[[ARTICLE:article_12|green and red beam visibility]]
[[PRODUCT:product_27|Model X green laser]]
[[CITE:ev_003]]
```

服务端负责验证 ID、库存、URL、章节授权、锚文本、重复目标和链接预算，然后注入真实 URL。
AI 不得创造 URL、产品 ID 或 evidence ID。

### 5. 防止 AI 用 0 链接偷懒

每个 Section Contract 持久化：

- `candidate_count`；
- `opportunity_state`；
- `min_required` / `max_allowed`；
- `selected_ids`；
- `rejected_ids` 与 `reason_code`。

若 `min_required=1` 而模型输出 0，章节门禁失败，只执行一次窄范围 Link Repair；不得为了
补链接重写整个章节。若 `min_required=0`，仍必须返回链接决策；缺失决策视为合同失败。

### 5.1 全局 assembly 不重新定义产品相关性

Phase 5 只审计前序已批准并已绑定的链接，不重新以用途场景精确度筛掉商品。只要
Phase 2–3 已判定产品与内容相关、目录数据无冲突且章节允许商业链接，Phase 5 接受该
产品；弱匹配仍必须使用 `related_catalog` 语义，禁止宣称专用、认证或未提供的兼容性。

旧系统的 blog/product/external “每 N 词一个”比例在新路径中只作为 advisory metric 或
warning。它不能把 `none` 变成 `required`，也不能为了数量要求模型添加不自然链接。
全局硬门禁仅保留：未知/未授权 URL、重复文章或产品目标、垃圾锚文本、frame 单元商业
链接、以及明显超过硬上限的链接堆砌。

### 5.2 交付 metadata 不得改变正文 S-ID

最终顺序固定为：Phase 4 先产生完整可见 Markdown 并分配 S-ID；Phase 5 只在其外层添加
frontmatter 和从可见 FAQ 确定性生成的 FAQPage JSON-LD。共享权威分句器必须忽略
frontmatter、fenced code 和 `application/ld+json` script。若添加这些 metadata 后正文
句子表发生任何变化，assembly 必须 fail-closed，不能重用原 claim ledger。

### 6. 上下文有效性必须可观测、可调节

每个章节保存 Context Manifest，记录：

- 输入字符数和各上下文块大小；
- evidence、文章候选、产品候选的输入 ID；
- 机会评分、入选/淘汰原因；
- 模型实际使用的 ID；
- 门禁失败和局部修复次数。

阈值、每节候选上限、链接预算和机会评分放在集中配置中。调整这些参数不应要求重写
prompt 或修改正式草稿，便于根据真实结果快速迭代。

### 7. 章节级 claim ledger 与修订

全局 S-ID 只能在最终可见 Markdown 确定后分配。固定顺序为：正文 H2 和 article frame
完成 → 服务端绑定 ARTICLE/PRODUCT/CITE → 组装 H1、Introduction、Key Takeaways、正文
H2、Conclusion、FAQ → 从该最终 Markdown 分配全局 S-ID → 分单元生成 ledger。不得先给
正文编号，再在前后插入 frame 内容，否则 claim ledger 会与正式草稿漂移。frontmatter
可在之后添加，因为权威分句器会先剥离 frontmatter。

每个正文 H2 和 article-frame 单元只对自己的全局句子生成 ledger。每批只携带最终 Link
Contract 允许的证据；frame 单元使用正文已批准 evidence 的确定性并集。模型只能返回
sentence_id、claim_type 和 evidence_ids，claim_text 由服务端从最终 Markdown 注入。
服务端合并章节 ledger 后，仍执行全文 draft SHA、claim/evidence ID 和逐句覆盖的最终门禁。

W1b/W2 根据失败句定位章节，只重写失败章节；已通过章节保持不变。全文组装后必须重新
执行全部正式门禁。

### 8. 正式切换必须后置、白名单化并可逆

Legacy W0 始终先按原协议生成并原子写入正式 draft/claim ledger。sectional candidate
只能在旧 W0 成功后运行，且默认 feature flag 为 `off`。`shadow` 只生成独立候选与比较
报告；`action` 也只有显式 Action ID 白名单可以进入 promotion。

对于已经完成 W0、已有正式 pair 且处于 W1b/W2 的 Action，首次 shadow 不得通过重跑
W0 触发。系统必须提供 shadow-only 的正式 Web POST，直接读取现有 R3 合同与正式 pair；
该入口只允许 `mode=shadow`，不得更新 Legacy 阶段或 promotion。运行前后必须校验正式
draft/claim ledger 字节与 SHA；异常修改必须恢复原 pair 后 fail-closed。

promotion 必须同时满足：比较报告无 blocker、assembly SHA 与报告一致、正式 pair 自
shadow 开始后未变化、Action 仍在白名单。提升前保存旧 pair 和 prepared manifest；正式
pair 与 promoted manifest 任一步失败都恢复旧 pair。成功 promotion 保留旧 pair 备份供
rollback 与审计；失败 promotion 在恢复正式旧 pair 后可删除未完成的 promotion 临时目录。
rollback 同样验证当前 promoted pair、备份和 manifest SHA；rollback manifest 失败时恢复
promoted pair。

首次 rollout 对空 AI 响应、超过两次重试、claim 覆盖下降或重复句增加采取 fail-closed。
章节化调用使用独立硬预算，避免因逐节和逐单元 ledger 自然增加调用次数而无限消耗。

### 7.1 局部修订必须保留可证明的不变部分

W1b/W2 failure adapter 必须先把 report 规范化为 SHA 绑定 repair plan，再决定动作：

- `section_rewrite`：只替换被定位的正文 H2；
- `link_repair`：读者可见文字、段落和标点保持不变，只调整批准占位符与 decisions；
- `ledger_repair`：正文与 frame 完全不变，只重审目标单元 ledger；
- `frame_rewrite`：只刷新 Introduction/Takeaways/Conclusion/FAQ，不调用正文生成器。

定位只允许使用显式 section ID、全局 S-ID、完整 sentence text、已绑定 URL、正文 heading
或明确 frame alias。评分器/蚕食检查器异常、蚕食阻塞和其他不可安全定位的问题保持
global blocker，不得让模型猜测修复范围。单次自动修订最多两轮。

多章节同轮修订按正文顺序执行，后一节必须接收前一节修订后的摘要；但当前旧输出仍按
原始 generation package 校验，修订输出按更新后的 package 校验，禁止混用 package SHA。

重新组装导致全局 S-ID 移动时，未改单元的旧 claims 只能通过唯一规范化 claim_text 映射
到新 S-ID。匹配不唯一、证据不再批准或 package 不一致时，仅重审该单元。所有结果仍需
重新通过 URL binding、section ledger merge、claim ledger 权威校验和 Phase 5 全局门禁。

## 实施约束

1. 用户界面仍保持现有阶段按钮，不增加逐节手工操作。
2. 新流程先以 shadow mode 生成合同和诊断，不改变正式 W0 输出。
3. 每个阶段独立提交、独立测试、可单独关闭；正式切换由 feature flag 控制。
4. 不手工修改正式 draft、claim ledger、w2-state、数据库或真实 Action 文件。
5. 旧 action 缺少新合同文件时必须可确定性重建，不能要求重新研究。
6. 完整 material pack、evidence ledger、产品库存报告和站内链接地图仍是权威来源。
7. 站点目录和站点策略优先于旧 brief；旧 brief 不得定义站点产品范围。
8. 正式内容语言为 English；非英文历史备注只保留审计痕迹。

## 后果

优点：

- AI 每次只处理一个职责明确的章节，显著缩短上下文和 JSON 输出；
- 链接由读者阶段和真实候选决定，避免固定最低值导致硬塞，也避免统一 0 导致偷懒；
- 失败只重跑局部章节，降低费用、延迟和改坏已通过内容的风险；
- 上下文召回是否有效有可审计数据，阈值可快速调整。

代价：

- 需要新增章节合同、链接规划、checkpoint、占位符解析和全文组装模块；
- 必须处理章节拼接后的语气、重复、过渡和稳定句子映射；
- 真实切换前需要 shadow 对比和端到端验收。

## 失效与复查条件

- shadow mode 显示章节候选召回经常漏掉人工认为明显相关的链接或证据；
- 模型在 `required` 机会下仍频繁输出 0，且 Link Repair 无法稳定修复；
- 分节文章出现明显重复、语气断裂或逻辑跳跃；
- 产品推荐与库存、属性或安全边界不一致；
- 章节流程的成本或失败率高于整篇流程，且质量没有明显改善。

计划复查日期：2026-09-30。
