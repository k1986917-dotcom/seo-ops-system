## [Unreleased] — Make the installed entrypoint runnable outside the checkout

### Fixed

- `seo-ops` now bootstraps the repository root before importing the Legacy workflow, so launching
  from another directory no longer fails with `ModuleNotFoundError: data_sources`.
- Added a subprocess regression test that imports the full Web application from a temporary working
  directory.

## [Unreleased] — Require safety context for high-power product copy

### Fixed

- `laserpointerhub` 继续把高功率视为排序中性条件，不因功率扣分或淘汰商品。目录支持的
  wattage/output 可以中性写入商品文案，不再全局隐藏；但高于站点阈值（当前 5mW）、使用
  `high-power` 措辞或写出 Class 3R/3A/3B/4 时，同一 PRODUCT 段必须包含完整安全提醒。
- 高功率安全提醒必须说明使用与激光波长匹配、且 optical density 适合输出的激光防护
  眼镜，并提醒避免直接眼部暴露和反射面、不得指向车辆或航空器。普通“戴护目镜”不足以
  通过；也禁止暗示护目镜会使高功率激光变得安全，或弱化文章其他位置的风险说明。
- 产品句仍只能使用目录支持的属性，禁止无目录依据的 `ceiling-focused`、专为施工设计、
  合规、认证或安全结论。安全/Class/合规章节继续保持 product gate=`none`。
- `support_basis=quote` 不再允许作为直接引语或“某人说/指出/写道”的归因式转述发布；
  仅 `verified_quote` 可保留逐字引语。新增 bounded content-provenance repair，把未核验
  引语改成自然概括，并为缺少安全上下文的高功率商品段补充受控提醒。
- PRODUCT binding 新增服务器生成的目录 provenance：目录标题、目录事实与内层 SHA；
  assembly 将每个产品 URL 绑定到唯一 canonical sentence，并记录 candidate/product、
  sentence ID、catalog SHA 和事实字段。内层 provenance 被改写时，即使外层 delivery SHA
  被重算也会 fail-closed。
- Comparison 新增 `product_provenance_count`，产品链接数与 provenance 数不一致时加入
  `product_copy_provenance_missing` blocker；Pipeline 新增
  `section_content_provenance_retries` 审计指标。
- 验证：Sectional 非 Web `240 passed`（排除 1 个已知 `/etc/mime.types` Landlock Web
  用例）；Legacy/W1b 当前范围 `234 passed`；Ruff、format、compileall、
  `git diff --check` 全部通过。

## [Unreleased] — Anchor reviewed promotion to the formal claim ledger hash

### Fixed

- Shadow comparison 现在在 `old.claim_ledger_sha256` 记录正式 claim ledger 的精确文件
  字节 SHA-256（由生产调用从原始 bytes 计算），不再仅依赖 ledger 内部 `draft_sha256`
  字段间接校验。
- `promote_sectional_assembly()` 在创建任何 promotion 产物前自行校验正式 draft/claim
  与 comparison.old 锚点一致，再校验调用者确认的 expected SHA；operator-reviewed
  promotion 拒绝缺少 claim 锚点的旧 comparison（普通历史 comparison 仍可读取）。
- 受控 CLI 增加 `--refresh-comparison-anchor`：原子重建仅 shadow-comparison 文件，
  添加 claim 锚点；与 `--execute` 互斥，必须提供正式 draft/claim 双 SHA 确认。

## [Unreleased] — Operator-reviewed promotion override for retry-only blocker

### Added

- `promote_sectional_assembly()` 新增可选 `operator_review` 参数：默认 fail-closed 行为
  不变；仅当 comparison `blockers` 精确等于 `["excessive_ai_retries"]` 且 recommendation
  为 keep_legacy、assembly audit 通过且零 blockers、产品 binding/provenance 计数一致、
  formal pair 未变化、rollout 为 action 模式且 allowlist 仅含当前 Action、人工 review
  六字段完整匹配（approved=true、Action ID 匹配、reviewer/reason 非空、comparison 与
  assembly SHA 严格一致）时，才允许人工审核 promotion。
- Promotion manifest 记录 operator_override、operator_reviewer、operator_reason、
  operator_reviewed_comparison_sha256、operator_reviewed_assembly_sha256 与
  overridden_blockers，全部纳入 manifest SHA 计算与校验。
- 新增受控 CLI `tools/promote_sectional_reviewed.py`：默认只读 preflight；实际写入必须
  显式 `--execute` 并同时提供 comparison/assembly 双 SHA 确认，缺任一即停止。

## [Unreleased] — Sectional shadow end-to-end under the final content rules

### Added

- 真实 Action #3 sectional shadow 在 final content rules（provenance / high-power safety
  context / H2 与 gate 修复）下首次端到端成功：六节正文 + frame + 10 ledger 全部落盘，
  终态产物齐全；B025 产品段含完整安全提醒与 catalog provenance，安全/Class 章节无商品，
  b92 无未核验直接引语。comparison 因 excessive_ai_retries 保守返回 keep_legacy，
  promotion 仍由运营者决定。

## [Unreleased] — Final content rebuild for link/word-count oscillation

### Fixed

- 当某 section 的首次 repair 修复自身问题后暴露出另一类内容 gate 错误（如 required
  link 与 word count 交替失败）时，final 修复现在从干净 Section Package 重建完整两块
  响应，并显式列出全部 gate（H2、字数区间、段落数、三类链接 state/allowed/min/max、
  禁止未核验直接引语与虚构事实），避免模型在相互冲突的中间状态间振荡。
- Pipeline 新增 `section_content_rebuild_retries` 审计指标。既有 final 路径（response
  format、candidate rebuild、deletion-only word-count trim、content provenance）保持不变。

## [Unreleased] — Repair sections whose heading deviates from the approved H2

### Fixed

- Section 首行不是精确批准的 `## <H2>`，或正文中出现第二个 H1/H2 时，不再立即终止整个
  Shadow：两类错误并入 response-format envelope 修复，从干净 Section Package 重建完整
  两块响应；仍保持一次 repair + 一次 final repair 的调用上限，并计入
  `section_response_format_retries`。

## [Unreleased] — Route post-format word-count misses to a bounded final repair

### Fixed

- RESPONSE FORMAT REPAIR（含 FINAL）现在在 system prompt 中显式写入目标可见词数区间与
  2-5 段契约，从 clean Section Package 重建时不再容易产出过短正文。
- 格式修订已修复 marker 但正文 miss word-count 契约时，不再立即终止 Shadow：final repair
  选择器把该错误路由到标准 WORD COUNT REPAIR（第三次 AI），最终仍需通过全部内容与 link
  gate，仍保持一次 repair + 一次 final repair 的调用上限。
- 纯 word-count 路径的 final 分支扩展为双向：正文过短时允许一次 final expand（与过长的
  deletion-only trim 对称）；仍严格限制为最多两次 word-count 修订。
- 修正 final 循环审计计数：`word_count` 类型的 final repair 现在计入
  `section_word_count_retries`，不再误计入 evidence-strength retries。
- 真实 Action #3 普通 resume 首次全程成功（POST=1、12 次 AI）：b92 一次生成即通过，六个
  正文 + frame + 10 个 ledger 全部落盘，终态产物齐全；全篇仅 B025 一个产品链接。

## [Unreleased] — Retry malformed sectional response envelopes

### Fixed

- Section body AI 返回缺失/重复 `===SECTION_MARKDOWN===`、在首 marker 前输出额外文字，或
  任一响应块为空时，不再立即终止整个 Shadow。新增一次从 clean Section Package 重新
  输出的 response-format repair；不回灌、猜测或宽松解析损坏响应。
- 若第一次格式修订仍损坏，允许一次 final response-format repair。两次修订都必须重新
  通过原有 H2、字数、段落、证据、ARTICLE/PRODUCT/CITE candidate 与 gate 校验；再次
  失败继续 fail-closed。
- Pipeline 新增 `section_response_format_retries` 审计指标。格式修订禁止 code fence、说明
  文字和重复 marker，要求两个 marker 各出现一次，decisions 后不得再有文本。

## [Unreleased] — Repair unapproved sectional link candidates

### Fixed

- Section writer 生成了 gate 外的 ARTICLE、PRODUCT 或 CITE candidate 时，validator 仍然
  fail-closed，但不再立即终止整个 Shadow。新增一次受控 approved-candidate repair：明确
  列出被拒 ID 与当前 `selected_ids`，要求只从干净 Section Package 中改用批准 candidate。
- 第一次修订仍是最小改动合同：不能改变另外两类链接的 placeholder inventory 或顺序。
  若该修订漂移或仍使用集外 ID，允许一次不回灌失败正文的 final repair，从干净 package
  重建 ARTICLE/PRODUCT/CITE 三类 inventory；所有 ID 和数量仍逐类经过当前 gate 校验，
  再次失败则停止。decisions JSON 继续由最终 Markdown inventory 规范化。
- Pipeline 新增 `section_candidate_selection_retries` 审计指标。该修复不扩大 candidate
  gate，也不允许模型从 manifest 的审计候选池、其他 section 或旧 checkpoint 复用产品。

## [Unreleased] — Site-aware sectional product recommendations

### Changed

- 新增版本化 Sectional 站点配置入口。Web 路由从 Action 的 `site_id` 解析 `sites.slug`，
  再经 Legacy Adapter 与 Sectional Pipeline 传入共享推荐引擎；站点规则从打包的
  `site_profiles/<slug>.json` 加载并严格校验，未知站点继续使用原通用配置，站点选择、
  配置版本与来源写入 shadow/context/pipeline 产物供审计。
- `laserpointerhub` 不再因商品功率高而扣分或淘汰商品；产品排序改为按用途属性加权：
  远距离/天花板指示优先 520/532nm 绿光、可见度、single-beam、focus 与 distance，
  精确指向、便携和燃烧用途分别使用自己的属性偏好。
- 激光笔的 Class 3R/3B/4 比较章节以及 safety/compliance/legal/hazard 等章节禁止产品
  内链。候选池保留最多 5 个供审计，但每个商业 section 最多链接 1 个产品，同一商品
  整篇最多分配一次；仅 `related_catalog` 匹配时不再强制商品链接。
- Section Package 向写作模型明确传递站点政策：不得只因高功率拒绝已批准商品，但也不得
  生成安全、合规、批准或普遍适用结论；多变体商品只能描述当前匹配变体，禁止混合规格。
- product gate 为 `none` 的 section 不写入站点商品 profile，保持既有非产品 checkpoint
  的 package SHA 兼容；只有实际受商品策略影响的 section 自动失效并重算。

### Fixed

- 修复通用 `laser/pointer` 词命中压过真实商品用途属性的问题；被中文历史 brief 拒绝进入
  英文正文的条目，其可验证 ASCII 技术词（如 `532nm`、`single beam`、`focus`）仍可作为
  只读排序信号，不会进入写作事实上下文。
- 修复同一产品被多个 section 重复分配、delivery 去重后留下病句的问题。B020 等颜色/
  波长数据自相矛盾的商品继续 fail-closed，且明确不是因为高功率被排除。

## [Unreleased] — Sectional frame authority-free repair (frame-only)

### Changed

- 收窄章节化修复范围为 article frame：validator 保持 fail-closed，错误现在携带被检测到
  的具体违规句；第一次 frame repair 的 prompt 包含服务端检测的 offending passage。
- final authority-free frame repair 只使用 ARTICLE FRAME PACKAGE 中的 topic 与已完成
  section summaries 从头生成 Introduction/Takeaways/Conclusion/FAQ，不再回灌上一版失败
  frame 全文；最终仍违规时继续 fail-closed 停止，不降级为确定性 fallback。
- 移除上一轮过宽逻辑：related_catalog 语言门禁、`product_fit` repair、确定性
  authority-free frame fallback，以及 `section_product_fit_retries` /
  `frame_authority_free_fallback_applied` 指标。正文生成、链接与产品逻辑不变。

# Changelog

所有用户可见变化记录在此。版本遵循语义化版本。

## [Unreleased] — 紧凑证据写作交接

### Changed

- Sectional Cluster Content now targets 3,000–4,200 visible words, with default
  body-section contracts raised from 220–360 to 350–500 words. This change is
  scoped to the sectional shadow/assembly path; the formal Legacy pair remains
  untouched until an explicitly approved promotion.
- Visible external citations now use a shared full-article hard cap of roughly
  one unique source link per 600 visible words, with a minimum allowance of
  three. Evidence may still support claim-ledger entries without repeating the
  same visible URL throughout the article.
- 章节化产品推荐改为“内容相关即可推荐”：`compare|select|apply` 中只要存在在售、数据
  无冲突且与文章主题或章节相关的商品，就要求至少 1 个产品链接；不再要求小目录网站的
  每个 SKU 都与细分用途完全匹配。弱匹配商品只能写成 related catalog option，不能
  虚构专用场景、验证结果或合规属性。
- 官方 DeepSeek V4 的 W0、W1b、W2 长正文任务现在显式使用 non-thinking 模式，
  避免复杂修订把输出预算消耗在 `reasoning_content` 后没有最终文章正文；其他
  OpenAI-compatible 服务不会收到 DeepSeek 专用参数。
- AI 返回 HTTP 成功但正文为空时，现在显示 `finish_reason`、completion/reasoning token
  用量、reasoning 字符数和实际响应模型等安全诊断；完整 reasoning 内容不会写入日志。
- W1b 空正文只在供应商资源不足、正常停止却空正文或缺少终止原因时有限重试；
  长度耗尽、内容过滤和工具调用结果不会重复提交同一请求。
- R3→W0 现在生成可恢复的紧凑写作 Brief、覆盖合同和章节证据卡。正文模型只接收
  与本篇相关的编辑要求和短证据卡，完整 material pack 仍留作权威归档与后续检查。
- W0、W1b 与 W2 现在将“写完整正文”和“生成 claim ledger JSON”分为两个独立 AI
  任务；ledger 仍由原有严格 parser 验证、服务端注入草稿 SHA、并使用原子写入。
- W1b/W2 修订只传递失败报告、当前草稿、紧凑 hand-off、相关证据卡和原草稿已使用的
  证据，避免反复把完整素材包塞回模型。
- W1b 的“AI 修订并重跑预检”现在一次点击最多连续修订、复检两轮，任一轮通过即停止；W2 同样使用受控批次，且 W2 修订后仍必须重新通过 W1b。
- W2 的“两轮”现在是一次明确点击的上限而不是永久锁死；下一批仍保留前次失败记忆。`--force` 的范围不变：只允许已完成至少一批、评分正常、检查器正常且仅剩蚕食阻塞时由人工确认写回。
- W1b 与 W2 修订轮次分开保存；每次失败会保存阶段、草稿 SHA 和失败摘要，并把近期失败摘要传入下一次 AI 修订，降低重复犯同一错误的概率。
- 原有蚕食误报人工确认按钮改为红色警戒文案“接受当前蚕食问题并继续”；它的后端条件未放宽，不能绕过事实、评分或检查器失败。
- 网页 Legacy 卡在任何已开始阶段都提供警戒色 `R0 重新开始此任务`；只重置当前 action，不影响其他任务。

### Fixed

- Required-link repairs now have an atomic server-side postcondition: every
  existing placeholder token and order must remain unchanged, and the repair may
  add only the exact minimum approved IDs selected by the server. Adding a second
  approved product is rejected. `related_catalog` products now have a dedicated
  semantic gate and one bounded repair; their paragraphs must remain neutral
  catalog navigation and may not contain product specifications, performance,
  visibility, feature, ergonomic, safety, compliance, preference, or exact-use
  suitability claims. After all three bounded article-frame AI attempts, an
  authority-only failure may use a deterministic, fully validated neutral frame
  without a fourth AI call; structural or protocol failures still stop closed.
  New metrics expose product-fit retries and deterministic frame fallback use.
- Missing required sectional placeholders now receive one bounded repair instead
  of failing immediately. The server identifies the exact missing ARTICLE,
  PRODUCT, or CITE gate, selects only approved IDs needed to satisfy
  `min_required`, and instructs the model to add those placeholders without
  changing existing placeholder tokens or inventing claims. Product repairs are
  restricted to neutral catalog navigation—especially for `related_catalog`
  candidates—and all existing allowed/max/required/prohibited, technical,
  evidence, word-count, and paragraph gates still run. A dedicated
  `section_required_link_retries` metric distinguishes this repair from layout
  and evidence retries.
- Section decisions now treat validated Markdown placeholders as the sole
  authoritative `used_ids` inventory on every generation path, not only after a
  formatting repair. Model-supplied decisions must still be valid JSON with the
  required shape and an unused reason, but ARTICLE, PRODUCT, and CITE IDs are
  normalized from the actual body before the existing allowed/min/max/required
  gates run. A decisions copy error can no longer block valid content, while a
  decisions claim cannot fabricate a missing required placeholder.
- Formatting-only sectional link-layout recovery now treats Markdown
  placeholders as the authoritative inventory and decisions JSON as redundant
  metadata. A repaired response is accepted only when every exact ARTICLE,
  PRODUCT, and CITE token—including anchor text and global order—matches the
  response that triggered the layout repair. The server then regenerates and
  validates canonical decisions from that immutable inventory. A model may no
  longer fail an otherwise valid paragraph split by miscopying `used_ids`, while
  any added, removed, reordered, or changed placeholder still fails closed.
- Sectional word-count recovery now distinguishes normal content adjustment from
  a final deletion-only trim. The first repair receives an exact add/delete
  budget instead of a vague expand/trim instruction. If an over-limit response
  remains too long, the same POST may use one final bounded trim that forbids
  adding or paraphrasing text, preserves placeholders/citations/decisions, and
  targets a safe buffer below the hard maximum. Under-length sections still get
  only one repair so the system does not repeatedly invent additional content.
- Sectional promotion eligibility now fails closed on deterministic wavelength/
  color contradictions and unsupported compliance metadata. Product registry
  parsing marks combinations such as `520nm blue`, `450nm green`, and
  `1064nm red` as catalog attribute conflicts, so those products cannot enter
  automatic link or writing context. Section responses and the final assembly
  independently reject the same contradictions, with one bounded technical-
  consistency repair available before stopping. Inherited Legacy summary and
  SEO-description values containing unsupported authority/compliance or
  superlative claims are replaced with neutral topic-derived metadata rather
  than copied into the sectional candidate.
- Sectional body generation now recovers from internal-link layout collisions
  without relaxing the one-internal-link-per-paragraph validator. Existing
  sentence boundaries are still normalized deterministically first; only
  unresolved same-sentence or structured collisions receive one bounded
  formatting-only repair. When an evidence-strength repair exposes the layout
  error, the same POST may use one final link-layout repair, with an explicit
  retry count in the pipeline result. Placeholder IDs, order, decisions and all
  content validators remain fail-closed.
- Zero-verified-quote sectional contracts now neutralize unsupported technical
  winner selection as well as named-authority attribution. Prompts such as
  `Which Laser Class Works...` and `...Is A Practical Choice...` become
  non-conclusive compare/evaluate tasks while preserving stable section IDs.
  The validator now catches unsupported blink-response safety claims and
  class/wavelength preference conclusions, but still permits neutral language
  such as “the practical choice depends on...”. The final bounded repair uses
  only the clean package and no longer feeds failed prose back to the model.
- Sectional generation now performs an authority-free preflight whenever a
  section has no source-verified quote. Named authorities are removed from the
  model-facing contract and prior-summary context, the initial prompt prohibits
  named-authority attribution, and one final bounded authority-free repair is
  allowed after the normal evidence-strength repair. Article framing uses the
  same bounded strategy, with exact retry counts exposed in the pipeline result.
  All repaired outputs continue through the unchanged fail-closed validators.
- Unverified evidence support that already contains regulatory attribution,
  recommendation/compliance conclusions, absolute safety language, or a
  technical-class preference is now withheld from model-facing sectional
  packages. The evidence remains in the audit registry, the package records a
  deterministic rejection reason, and required citation minima remain strict;
  generation fails before an AI call when too little writing-safe evidence
  remains.
- Legacy sectional headings that directly command unsupported authority
  attribution, such as `What OSHA Says About ...`, are now softened to a
  verification-oriented contract while preserving the original stable section
  ID. Evidence-strength failures include the server-detected offending passage
  and the single repair prompt explicitly distinguishes zero verified quotes
  from verified evidence IDs. A second failure now reports the exact section
  ID, heading, and repair kind instead of a generic pipeline error.
- The controlled sectional shadow runner now supports an explicit
  `--resume-existing --refresh-complete` path after a reviewed complete shadow
  candidate has become stale under a newer code/contract revision. The runner
  transactionally moves the old resolved delivery and four terminal artifacts into
  a SHA-recorded sibling archive, while preserving checkpoints for normal
  package-SHA reuse decisions. Promotion manifests, incomplete terminal sets,
  unknown files, and archive failures still fail closed; an interrupted archive
  restores every moved active file. If that rollback itself is incomplete, the
  remaining recovery files are preserved and their path is reported instead of
  being deleted.
- Sectional shadow generation now preserves whether evidence support came from
  a source-verified quote, an unverified quote field, or only a synthesized
  research key finding. Concepts
  remain available for retrieval ranking but are no longer exposed to the
  writer as factual support. Authority attributions, regulatory/compliance
  recommendations, absolute safety claims, and unsupported superlatives now
  require source-verified quote evidence; otherwise the affected section or article
  frame gets at most one constrained neutral-language repair and then fails
  closed. The merged claim ledger is checked again before assembly. Legacy
  headings containing “the only choice” are softened to “A Practical Choice”
  without changing their stable section IDs.
- Section generation now permits exactly one constrained AI repair when an
  otherwise parseable section misses only its word-count contract. The retry
  must preserve the approved H2, factual meaning, placeholder IDs, link
  decisions, and 2-5 paragraph structure while aiming inside the existing
  range; the 350-500 gate is not lowered. Non-length failures still stop
  immediately, and a second length miss also fails closed.
- Phase 4 now performs deterministic full-article link allocation before URL
  binding. Required section minima are allocated first; duplicate ARTICLE and
  PRODUCT targets keep one canonical link while later occurrences remain plain
  anchor text, and duplicate/excess CITE placeholders are removed from visible
  Markdown without removing their approved evidence from section claim
  packages. The strict Phase 5 duplicate and density gates remain unchanged.
- Section generation now repairs a narrow formatting-only failure when an AI
  puts multiple ARTICLE/PRODUCT placeholders in one paragraph. The server may
  split that paragraph only at existing sentence boundaries, preserving every
  visible word and placeholder. Multiple internal links in the same sentence
  still fail closed.
- Section paragraph normalization now also keeps validated prose within the
  canonical 2-5 paragraph range. It may merge adjacent prose paragraphs or
  split one long prose paragraph at an existing sentence boundary while
  preserving wording, order and placeholders. Structured Markdown and
  impossible link layouts remain fail-closed, and paragraph-count errors now
  report the observed count. Conservative sentence splitting protects common
  abbreviations and initialisms such as `e.g.` and `U.S.`.
- Section decisions no longer fail because the model repeats an inconsistent
  `reason_code` for a candidate that is visibly used. ARTICLE/PRODUCT/CITE
  placeholders and `used_ids` remain strictly matched and allowlisted; once
  usage is proven, the server deterministically stores
  `used_approved_candidate`. Unused gates still require a non-empty reason.
- Legacy frontmatter used by sectional shadow now decodes simple YAML single-
  and double-quoted scalars and rejects malformed quoting at the adapter
  boundary. Sparse older drafts may reuse `description` for summary/SEO
  description, derive only the missing minimum tags/keywords from the Action
  topic, and deterministically compact SEO fields into canonical length ranges
  without rewriting the formal draft.
- Existing-pair sectional shadow now supports pre-existing Actions whose compact
  write brief contains an empty `tier`. It may read `precheck_tier` from the
  existing W1b state only when `precheck_draft_sha256` exactly matches the
  current formal draft; stale, unsupported or conflicting tiers still fail
  closed. No Action file is rewritten, and the selected source is recorded in
  `existing_pair_inputs.tier_source`.
- 新增已有正式稿的安全 sectional shadow 入口：
  `POST /actions/{action_id}/legacy/stage/sectional-shadow` 只允许 `mode=shadow`，直接读取
  现有 draft、claim ledger 和 R3 写作合同，不再为了 shadow 重跑 W0。运行前后校验正式
  pair 字节与 SHA；任何异常修改都会原子恢复，且不会更新 Legacy 阶段或触发 promotion。
- 对齐 article-frame 与最终 assembly 的硬合同：Key Takeaways 统一为 3–5 条，FAQ 统一为
  3–4 条。生成 prompt、package 校验和 Phase 5 门禁不再出现“生成层允许、交付层必拒绝”
  的确定性冲突。
- article-frame package 的数量要求改为 canonical 固定值；即使重新计算 package SHA，
  也不能把 FAQ 上限篡改回 5 或把 Takeaways 上限篡改回 6。
- 产品候选 `fit_level` 仅允许
  `strong|approved_constraint|contextual|related_catalog`；未知值 fail-closed。
- 项目 metadata 与运行时 `__version__` 统一为 `0.11.5`。
- shadow 指标在供应商未返回 token usage 时明确记录 `completion_tokens=null` 和
  `completion_tokens_known=false`，不再把“未知”伪装成 0。

### Added

- 新增 `tools/run_sectional_shadow.py`：通过隔离本地服务和正式 Web POST 对单个 Action
  执行一次 existing-pair shadow。工具拒绝脏工作区、未同步分支、非 `off` 默认 rollout、
  未经确认的 sectional 目录和占用端口；`--resume-existing` 只接受已知 checkpoint、
  ledger checkpoint 或 resolved-delivery 中间文件，完整、promotion 或未知产物一律拒绝；
  允许 `ai_runs` 审计记录正常写入，但要求 Action 行、
  正式 draft/claim ledger、w2-state、`.env`、Git 状态和 promotion 状态保持不变。
- 新增章节化写作 Phase 7 controlled rollout：默认 `off`，支持只读 `shadow` 和显式
  Action ID 白名单 `action`；旧 W0 始终先成功，sectional 失败保留 Legacy 正式 pair。
- 新增完整 shadow candidate 与旧/新比较报告，覆盖 claim 覆盖、重复句、链接、AI 调用、
  重试和空响应；退化或不稳定时禁止 promotion。
- 新增 SHA 绑定 promotion/rollback 协议、prepared/promoted manifest、旧 pair 备份和
  最终 manifest 失败恢复；新增独立 AI 调用预算及 decision/rollback 运维命令。
- 新增章节化写作 Phase 6 局部修订层：将 W1b/W2 失败映射到正文 section、article frame、
  link-only 或 ledger-only 动作；不可定位的评分、蚕食和系统失败保持 global blocker。
- 多章节修订按顺序传递新摘要；未目标正文保持不变。全局 S-ID 变化后，未改 claims 通过
  唯一 claim_text 重映射，不安全时只重审对应单元。
- Link Repair 的即时响应、checkpoint 写入和 checkpoint 恢复均强制读者可见文字不变；
  修复结果重新通过 Phase 4 delivery/ledger 和 Phase 5 assembly 全局门禁，最多两轮。
- 新增章节化写作 Phase 5 canonical assembly：确定性生成 frontmatter、可见 FAQ 对应的
  FAQPage JSON-LD、最终 draft、最终 claim ledger 和 assembly report；目前仍为 shadow-only。
- 新增全局交付门禁：目标词数、H1/H2 顺序、Takeaways/FAQ 数量、跨章节重复、未登记
  URL、重复文章/产品目标、泛化锚文本和链接硬上限均可 fail-closed。
- assembled draft、claim ledger 和 report 使用三文件事务写入与回滚；损坏或 SHA 不匹配
  的 bundle 不会恢复。
- 新增章节化写作 Phase 4 delivery/ledger 层：ARTICLE/PRODUCT/CITE 由服务端按最终
  Section Link Contract 白名单绑定真实 URL，registry SHA、章节授权、冲突商品、未知 ID
  和残留占位符全部 fail-closed。
- 最终可见文章先确定性组装 H1、Introduction、Key Takeaways、正文 H2、Conclusion 和
  FAQ，再统一分配全局 S-ID；旧 brief 中的 FAQ/Introduction/Takeaways/Conclusion 不再
  重复成为正文 Section Contract。
- 新增正文与 article-frame 单元的独立 claim-ledger package、non-thinking AI 调用、
  原子 checkpoint、中断续跑和严格 evidence 白名单；claim_text 由服务端从最终 Markdown
  注入，合并结果兼容现有 Legacy claim-ledger 权威校验器。
- 新增 resolved delivery 的 SHA 绑定原子持久化；损坏、过期、上下文或 registry 变化后
  不得恢复。frontmatter 后置添加不会改变正文 S-ID。
- 新增章节化写作 Phase 3 生成引擎：逐 H2 精简上下文、严格英文 Markdown/链接占位符
  协议、条件最低链接门禁、章节级原子 checkpoint 和中断续跑。
- 新增主体完成后的 Introduction、Key Takeaways、Conclusion、FAQ 独立短调用与
  checkpoint；这些组件只能基于章节摘要生成，不得引入新事实、产品、链接或引用。
- 新增旧编号加粗 H2 大纲兼容、站点高频品类词动态降权、产品匹配强度
  `strong|contextual|related_catalog|approved_constraint` 和每节 prompt 大小预览。
- 新增章节化写作 Phase 1 合同层：稳定 Article Blueprint、Section Contract 和 Section
  Link Contract，可严格校验、确定性重建并以三文件 bundle 带回滚保存；目前未接管正式
  W0。
- 新增链接机会四态合同。未评估机会必须使用 `unassessed + min_required=null`；只有经过
  判断的 `none` 才允许最低值为 0，`required` 必须有候选且最低值至少为 1。
- 新增章节化写作 Phase 2 shadow 候选层：从站内文章地图、通用产品目录和 evidence
  cards 构建每节候选、机会状态、Context Manifest 和只读 shadow report；正式 W0 尚未
  切换。
- 新增跨行业产品目录解析：核心只要求产品 ID、标题/名称和 URL，其余字段作为通用
  attributes；喷码机、园林工具和激光产品使用同一候选逻辑。
- 新增 Catalog Data Quality Report 逻辑区段，随持久化 sectional shadow report 保存。
  同一产品的标题、目录表格和详情属性冲突时，显示产品 ID、冲突值和修复建议，并在
  修正前阻止该 SKU 被自动链接。
- 新增只读 `tools/sectional_shadow_preview.py`，可预览候选和链接机会而不调用 AI、不修改
  Action 文件。
- 新增 `POST /api/hermes/runs`、`GET /api/hermes/sites`、
  `GET /api/hermes/runs/{action_id}` 和 `/prompt`，支持 Hermes 以站点、
  选题和可选要求启动/恢复持久文章任务。
- 新增 Hermes `scripts/start.sh`：通过 HTTP 完成 R0、已配置 SerpAPI/Tavily
  外部检索、搜索结果适配和 Legacy R1，并在门禁通过时继续托管 R3→W3；无可用供应商时停在 `r0_prompt`，不伪造外部资料。
- 新增 Hermes `POST /api/hermes/runs/{id}/continue`、`GET .../{id}/materials`
  与 `scripts/continue.sh`：无搜索凭据时先汇报已同步资料和提示词，再由用户选择“使用已有资料”或“人工粘贴结果”；选择后由服务端继续安全流程。
- 新增 Hermes 全流程控制器回归测试，覆盖 R3→W3 的确定顺序与后端阶段更新。
- R0 搜索提示词和 topic-context 可携带运营者要求；新增自动化桥接回归测试。

### Unchanged

- W0/W1b/W2 evidence/claim ledger 事实校验链路和严格 gate 未修改。

### Safety and compatibility

- 站点真实在售目录优先于旧 Research Brief；brief 自然语言不会自动成为产品过滤规则。
- 正式章节合同固定输出语言为 English；旧中文备注只进入审计字段，不进入写作上下文、
  产品评分或链接决策。
- frontmatter、FAQ JSON-LD 和 fenced delivery metadata 不参与 canonical sentence-ID；
  assembled draft 的 claim ledger 只重绑定最终 draft SHA，不改变正文 claim 决策。
- 旧的按字数最低链接比例只作为 advisory 诊断，不会覆盖章节 Link Contract 或强塞商品；
  URL/ID 授权、重复目标和垃圾锚文本仍保持硬阻塞。

## [0.11.5] - 2026-07-29

### Fixed

- **ledger 根 JSON 类型错误 fail-closed**：`_run_fact_check` 在 `json.loads` 后先单独判断 `evidence-ledger` / `claim-ledger` 是否为 dict；`list`、`null`、`str`、`int` 等非法根类型会生成结构化 blocking 并立即返回，错误分支不再调用 `.get()`，彻底避免 AttributeError。
- **W0 失败不删除已有产物**：`stage_w0_validate_and_draft` 改为先验证候选 draft 与 claim-ledger，只有验证成功后才清空旧产物并原子写入新版本；若 AI 返回非法 ledger，旧 draft、旧 claim-ledger、旧 W2 state 均保持字节级不变。
- **W0 原子写异常路径保护**：`_write_ahead_draft_and_ledger` 在 W0 中先执行、后清理；若其第二次 `os.replace` 失败，内部 rollback 会恢复旧 draft/ledger，W0 捕获异常并返回 `success=False` 与明确错误信息，不向页面抛未处理异常；预检/后处理报告与 w2-state 只在写入成功后清理/重置。

### 测试覆盖新增

- 4 个测试覆盖 evidence-ledger / claim-ledger 根 JSON 为 `[]` / `null` 场景，断言不抛异常且返回结构化 blocking。
- 重写 W0 numeric `claim_text` 测试，新增 W0 numeric `claim_type` 测试，预置旧 draft / claim-ledger / W2 state 后断言失败时三者字节级不变。
- 新增 `test_w0_second_replace_failure_preserves_old_artifacts`：mock 第二次 `os.replace` 抛 `OSError`，断言 W0 返回 `success=False` 且旧 draft / claim-ledger / w2-state 字节级不变。
- 完整测试：301 passed；`ruff check` 5 个 pre-existing F841，无新增。

## [0.11.4] - 2026-07-28

### Fixed

- **evidence-ledger 预校验 fail-closed**：`_run_fact_check` 在构建 evidence index 时即校验每条 evidence 的 `source_url` / `quote` / `key_finding` 类型与内容——`source_url` 必须 str 且非空；`quote` / `key_finding` 类型必须 str（None 允许）；二者至少一项非空。任一非法即产生结构化 blocking，即使该 evidence 没有被任何 claim 引用，杜绝 W1b 错误通过脏 ledger。

### 测试覆盖新增

- 3 个测试覆盖「未引用的 evidence 字段类型错误」：clean `ev_001` + 被引用 + 脏 `ev_002`（`source_url=123` / `quote=123` / `key_finding=123`）且不被任何 claim 引用，断言 W1b 全部 blocking。
- 完整测试：285 passed；`ruff check` 6 个 pre-existing F841，无新增。

## [0.11.3] - 2026-07-28

### Fixed

- **原子写回滚处理旧文件缺失**：`_write_ahead_draft_and_ledger` rollback 现在区分「旧文件存在 → restore 旧内容」与「旧文件不存在 → unlink 第一次已替换进去的新文件」。杜绝新 draft 单独泄漏，保证 rollback 后永远是「两个旧版本（含都不存在）」或「两个新版本」。
- **`_run_fact_check` 字段类型校验前置**：所有 ledger 字段（evidence_id、source_url、quote、key_finding、claim_text、claim_type、material_pack_sha256、draft_sha256、evidence_ids 每项）在任何 `strip` / `[:12]` 操作之前先做 `isinstance(..., str)` 校验；非 str 类型产生结构化 blocking 项（含字段名 + 实际类型），绝不抛 AttributeError。

### 测试覆盖新增

- `test_second_replace_failure_removes_both_when_neither_existed`：初始 draft + claim ledger 都不存在 → 第二次 replace 失败 → 两者必须都不存在。
- 7 个字段类型错误测试（evidence_id / source_url / claim_text / claim_type / material_pack_sha256 / draft_sha256 / evidence_ids 每项）注入整型 123，断言产生结构化 blocking 且 detail 含字段名 + 类型错误字样。
- 完整测试：282 passed；`ruff check` 6 个 pre-existing F841，无新增。

## [0.11.2] - 2026-07-28

### Fixed

- **W2 revise 接入证据闭环**：现在 AI prompt 含 Evidence References、输出必须含 `===CLAIM_LEDGER===`、复用 W0 解析/验证逻辑、服务端注入 `draft_sha256`、原子写 draft + claim ledger。非法 AI 输出不修改任何文件。
- **真正原子写**：`_write_ahead_draft_and_ledger` 改为「snapshot → 写 temp → fsync → replace」协议；任一步失败 restore 旧 draft 和旧 ledger，杜绝 NEW DRAFT + OLD LEDGER 混合态。
- **W0/W1b 句子提取统一**：`_validate_claim_ledger_json` 改为段落 + 句子二级拆分，与 W1b `_claim_in_draft` 行为一致；段落内完整句可被两端一致接受。
- **`_run_fact_check` schema 异常结构化 blocking**：evidence / claims 每项显式校验 `isinstance(item, dict)`；`evidence_ids` 必须为 list；非 dict 元素产生结构化 blocking 项而非 AttributeError。
- **通过后拒绝 revise**：`stage_w2_revise` 在 `gate_passed=True` 或 `applied=True` 时直接返回失败，不再修改文件。

### 测试覆盖新增

- 5 个测试类共 14 个测试覆盖：W2 gate_passed/applied 拒绝、非法 ledger 不改文件、原子写 rollback、段落内句子、非法 evidence/claim 对象、W0→W1b→W2→W1b 真实流程。
- 完整测试：275 passed；无业务逻辑回归。

## [0.11.1] - 2026-07-28

### Fixed

- **LEGACY_WS 不再硬编码**：改为从 `active_settings.data_dir` 动态推导，使多站点配置或临时目录测试时工作区路径自动适配。
- **legacy_sync.sync_all() 支持多站点**：所有 SQL 查询从 `site_id = 1` 改为绑定参数 `site_id = ?`；新增 `settings` 和 `site_id` 参数，可从网页路由传递正确的站点 ID（`a7323f0`）。
- **注册 Legacy 工作流不再污染生产路径**：`legacy_r0` 路由改为 `legacy_sync_all(settings=active_settings, site_id=...)`，使用运行时 settings 而非硬编码路径。

### 测试覆盖新增

- 新增 3 个 `sync_all(settings=, site_id=)` 回归测试，覆盖 settings/临时 DB/data_dir 派生和不同 site_id。
- 集成测试 `test_hermes_orchestrator_smoke.py` 已适配新的动态 LEGACY_WS 推导。

### 验证

- 完整测试：185 passed；无业务逻辑回归。

## [0.11.0] - 2026-07-21

### 新增：Legacy Research + Write 工作流（新文章制作通道）

- **新文章制作页替换为 Legacy 10 步向导**：点击"开始制作"后，新文章进入 Research → Write 全流程，不再使用原来的素材确认 → 三次 AI 自动写。
- **增强搜索提示词**：8 段完整提示词（SERP 分析、用户痛点、常见误区与教训、案例、权威引用、信息缺口、PAA 问题），替代原来的 4 行简化提示词。
- **数据库自动同步**：Legacy 工作区的文章、产品、GSC 数据每次启动前从数据库自动刷新。
- **素材库累积**：痛点库、案例库、外链库由旧 archive 脚本自动维护，长期累积。
- **作者自动填 `LaserPointerHub`**：不再使用化名。
- **旧文章制作通道不变**：旧文章更新继续使用原有流程。

### 技术变更
- SQLite v13：`actions.legacy_stage` 字段
- 新增模块：`legacy_sync.py`（数据同步）、`legacy_workflow.py`（阶段管理 + AI 集成）
- 旧模块不受影响：`content_production.py`、`material_workflow.py` 仍由旧文章通道使用

### 修复：Legacy 规则对齐与后端硬门

- Research 改为先运行本次确定性评分，再让 AI 读取并解释该分；评分脚本失败时停止，旧评分文件不能冒充本次成功。
- W1b 使用脚本 JSON 结果计算失败项；脚本崩溃不再显示为“0 项失败”。
- 后处理检查不再修改真实草稿；只有硬门全部通过并显式写回时才更新文件。蚕食检查器或质量评分器失败会停止流程。
- 后端强制执行“预检通过 → 后处理通过并写回 → 注册”，并用草稿 SHA-256 防止把旧检查结果套到修改后的草稿。
- `--force` 只允许在两轮修订用满、仍为蚕食阻塞且评分合格时经人工确认使用，不能跳过评分或检查器错误。

### 修复：Legacy 运行产物隔离

- 每个文章任务和每次重新开始都有独立运行目录与 manifest；同主题文章、重试和并行任务不再共享 Research、草稿、报告或 W2 状态。
- Research、Write 和共享脚本统一使用同一套稳定 slug；非拉丁主题不再受 Python 随机 `hash()` 影响。
- 同一天重复执行的报告使用唯一文件名保存；collect 失败不能复用本次 attempt 中此前留下的数据文件。
- DB 同步会删除 inactive/已删除文章留下的发布快照，避免它们继续参与蚕食判断。
- 修复蚕食检查器缺失工作区根路径的问题，使正常检查可以实际读取同步后的文章。

### 验证

- 完整测试：172 passed；仅 1 条既有 Starlette/httpx 弃用警告。

## [0.10.9] - 2026-07-19

### Changed

- 主题缺口与边界调研恢复成功原型的 12 路开放入口：现象、受众、任务、环境、生命周期、故障、决策、生态、安全/规则、交易、变化与 emerging 均搜索整个手持激光笔空间；自动选中的图谱分支只给旧 Plan 任务卡提供上下文，不再被拼入每一路查询形成隐性收口。
- 来源支持且未与当前 CMS 主意图/正文重复的种子全部留在折叠候选池；从中选 5 个继续调用 API 深挖只是额度控制，不是候选数量门槛。
- 恢复成功原型末端的主意图聚类：只合并同一页面职责的换说法，并与已有候选池去重；同方向下任务、症状、决策、受众、地区、条件或结果不同的细化主题继续保留。
- 调研页仅保留最近一轮概况；只以可展开的实际对象账汇报本轮的开放搜源、来源 URL/原话、种子、CMS 重复拦截及匹配文章、深挖、意图核对、候选路由和供应商失败，不再显示无数据的固定流程说明、历史运行列表或重复的“查看文章建议”按钮。旧运行不补造历史明细。入口名称和按钮文案已按运营者定义交换，未改变实际调研路径。
- 文章建议页现在只保留可执行的两栏最终结果：左侧旧文章更新、右侧独立新文章。两栏各直接展示两项优先建议，其余候选逐条按标题折叠且不截断；新文章栏显示“累计待选 N（本轮新增 M）”。
- 原始搜索线索不再伪装成需要运营者选择的文章卡：其 URL、原话与后续查询继续保存在来源观察续池，供下一轮自动选作种子。同主意图的调研候选自动生成左侧旧文更新建议；确认正文已覆盖的候选保留审计但不创建选择项。

### Fixed

- 修复把“本轮方向”强行加入 12 类入口导致 60 条真实观察只抽出 5 个同方向种子的额外语义限流。
- 修复店铺可信度/评分/评价、犬只激光追逐综合征等同意图变体在同一轮或下一轮重复占卡的问题；聚类只可引用输入候选 ID，不能发明主题、改资格或绕过 CMS 重复门。
- 修复旧规则留下的 `needs_evidence` / `needs_human_review` 候选在隐藏后没有去向的问题：打开文章建议页会无外部调用地按当前“只拦重复”规则重判，分别进入新文栏、旧文栏或重复审计；已有的 `update_existing` 结果也会补齐旧文建议。

### Validated

- 在生产 SQLite 副本连续完成两轮不指定主题、不模拟前序的正常调研。SerpAPI 账户显示 52→48；生产库 SHA-256 前后均为 `8181b15b5f5cd1c0747acebf3b3323a9004c471a215ac08b7cfabec77a403c1f`。
- 第一轮从 58 条初始观察实际得到 15 个种子、14 个未被 CMS 重复拦截的可见种子和 13 条聚类前成型主题；第二轮实际得到 48 个 AI 原始种子、45 个有效种子、37 个可见种子和 37 条聚类前成型主题。两轮入口方向自然从户外轮到天文，最终标题无完全重复。
- 对两轮真实候选池做两次不调用搜索 API 的最终聚类复验：完整代码路径分别合并 3 个和 10 个本轮换说法，并在第二轮识别 14 个上一轮同意图变体；聚类后仍保留约 10 个与 14 个可进入最终资格检查的独立成型方向，而非收缩回 1–3 个。

## [0.10.8] - 2026-07-19

### Changed

- 主题调研新增“来源观察续池”：PAA/相关搜索、商业/竞争候选页、论坛/社区、评价、社媒/视频与官方/参考页的公开语言会连同 URL、摘录和 evidence ID 保存；下一轮优先从这些可追溯观察中选种子，旧 Plan 的任务卡与问题链继续补足。
- 增加 SQLite v12 `research_seed_observations`，并在调研概况中显示本轮继承的软冷却、保存的来源观察与已续用数量。
- 语义簇只作为透明调度信息：若上一轮主攻某簇，下一轮仅对该簇软冷却一次，不会永久排除电池或任何固定方向。
- 候选卡改为折叠标题优先显示；原始搜索线索与成型角度仍分开，展开后才显示意图、准备度、理由与操作。

### Fixed

- 生成查询含有 `laser pointer` 不再把 LightBurn、激光瞄具、切割/雕刻等来源错误升级为核心对象；真实核心来源优先于相邻/偏移来源占用有限种子位。
- 候选池增加“对象 + 动作 + 场景”的强重复去重，避免下一轮把已经保留的同一主意图换个标题再次推荐；不同主要任务的细化方向仍保留。
- 无页面直接支持的品牌/型号/推荐性表述会保留为原始线索，而非直接进入可执行文章建议。

### Validated

- 在生产 SQLite 副本连续完成两轮真实外部调研，并在第一轮前模拟 `battery_charging` 已主攻：两轮均为 `success`，每轮记录 SerpAPI 3、Tavily 8、Firecrawl 4、AI 1；生产数据库 SHA-256 前后相同。
- 第一轮保存 24 条多来源观察并得到树艺师从地面指示修枝位置的成型方向；第二轮实际续用其中 3 条，得到“户外光束看似突然停止”及“天线/机械对准”两个不同成型方向。修复后未让 LightBurn 或激光瞄具进入核心种子位。

## [0.10.7] - 2026-07-18

### Changed

- 主题调研加入旧 Plan 的“前沿先行”完整做法：公开第三方痛点、竞品问题、集群缺口和角色前沿被表达为可审计的“角色 × 单一任务 × 条件”任务卡；每轮优先从不同来源选取最多三个未尝试卡，而非反复搜索同一宽泛词。
- 任务卡先仅用活动 CMS 的主意图、H2 和正文做重复预筛；只有已确认同主意图或正文已覆盖的卡不消耗外部调用。需求、范围、材料、弱信号和关系不确定仍不是卡或候选的硬门。
- 研究运行保存任务卡、来源类型和预筛结果，并将其传给 AI。AI 必须忠实保留卡中的角色、任务和条件；不能把“画面过曝”扩成“相机损坏”，或把“连续使用需求”扩成“改装方案”。
- 自动主题缺口入口优先选择尚有未尝试任务卡的分支和维度；若该分支只剩一两张卡，会保留精确卡作为首个种子，并用旧 Plan 的独立问题链补足剩余探索预算；卡耗尽后才回退到既有自然假设或问题链。

### Fixed

- 将“激光水平仪/旋转激光”等明确替换手持激光笔的主题保留为软性优先级诊断，而非重新引入范围或意图硬拦截。

### Validated

- 在生产数据库副本上，以真实 SerpAPI、Tavily、Firecrawl 和 AI 完成 10 个单种子对照、3 个多种子稳定性批次、3 个自动种子池批次及 1 个任务忠实度批次；生产 SQLite SHA-256 在每次前后相同。
- 自动种子池一次生成 28 个待验证卡，其中 14 个经 CMS 重复预筛保留；三个自动多种子批次分别得到 3、2、3 个独立成型主题。任务忠实度批次得到建筑检查、镜头/TV 画面表现、连续对准三个与卡一致的主题。
- 正式接入后的三轮最小预算真实复测全部为 `success`：每轮各一次 SerpAPI、Tavily、Firecrawl 与 AI，稳定得到树艺师指示修枝位置、Laser 303 模式跳变/变暗诊断、相机/TV 中绿点过亮或 bloom 三个合格且不重复的 AI 成型主题；生产 SQLite 未被写入。
- `pytest tests/test_research_workflow.py tests/test_simplified_workflow.py -q` 通过；新增任务卡优先、正文重复预筛、单卡补充问题链、审计保存和相邻产品软诊断覆盖。

## [0.10.6] - 2026-07-18

### Changed

- 调研升级为旧 Plan 风格的“确定性种子 → 证据扩展 → AI 成型角度”：边界问题链继续负责探索，第二条种子增加论坛、评论和问答公开语言探针，并明确标注为第三方市场代理而非本站第一方反馈。
- 新文章资格改为纯重复判定：仅同主意图或现有 CMS 正文已覆盖的子题会阻断；范围、表达、弱需求、少材料、关系不确定和跨分支仅作为诊断或制作准备度。
- 种子不再把最终角度锁死。只要激光笔仍是核心对象，跨受众/场景的新任务不降权；只有候选实际替换为其他核心激光产品时才软降权。
- PAA、Related Searches 和 Tavily 直接返回的问句/标题移到“原始搜索线索”，与 AI 综合出的成型文章角度分栏；旧 Plan 的接受/拒绝记忆进入软排序。

### Fixed

- 修复树艺师用激光笔指示修枝位置等有效细化任务因未复述所选分支词而被误判偏移、降低优先级。
- 修复 AI 已返回 8 条 inference 时种子锚点元数据可能被截断，导致重新资格判定后丢失锚点诊断。
- 更新网页与迁移测试中的旧文案和旧研究规则版本断言。

### Validated

- 在生产数据库副本上完成三轮真实外部调研：SerpAPI 6/6、Tavily 9/9、AI 3/3；Firecrawl 5 次成功、2 次失败事件、1 次复用。10 个成型角度中 7 个通过非重复判定，3 个回流现有文章。
- 最新规则对三轮真实证据本地重放后，树艺师角度仍为独立新文章方向，优先级从旧误罚的 71.6 恢复为 91.6；原始搜索线索与成型角度保持分栏。
- 三条不含站点私有上下文的公开语言种子实际检出建筑检查、围栏施工、望远镜安装架、外接开关、卖家功率虚标与退货等具体问题。
- 完整 `pytest` 80 项通过；Ruff 格式、静态检查与 Git 差异检查通过。

## [0.10.5] - 2026-07-18

### Changed

- 调研候选改为“意图去重优先”：仅同主意图或 CMS 正文已覆盖的子题会被拦截；相邻但承担不同用户任务的细化方向可进入新文章建议。需求与页面材料继续保存为写作准备度，并在文章制作前复核。
- 没有结构化假设的主题分支现在按旧 plan 的多视角方法轮换失败、决策、旅程、兼容性、规则和相邻任务等检索种子；它们只产生待验证搜索问题，不能凭分支标签伪造文章主题。

## [0.10.4] - 2026-07-18

### Changed

- 文章制作的素材确认扩展到旧文章：旧稿除原有查询—页面联合证据外，也必须先有 A、E、G 和至少两个可追溯来源的已确认素材，才会调用 AI。
- 旧稿提示词和确定性检查改为证据边界写作：保留原 Slug 与主要意图，拦截泛化链接锚文本、标题跳级、长段落、虚构第一手经验及市场/价格/促销内容。
- 新旧文章均不再要求市场、价格、零售商比较、折扣、排行榜、Quick Specs 或 CTA；产品链接仅在确有助于完成读者任务时保留。

### Fixed

- 当前 CMS 正文已覆盖铜/铝壳散热比较，撤回该自然语言新主题假设，避免把已有正文再次报为待补证据的新 URL。

## [0.10.3] - 2026-07-18

### Changed

- 调研范围硬拦截收窄为学校/课堂和未成年人；高功率、技术、户外、专业及其他成人主题不再因预设词自动阻断。需求、缺口、材料、英语和重复资格门保持不变。
- 记录产品决策隔离复验的路由原则：已覆盖需求应进入旧文更新或产品内链；不为保持发文数量新建重复 URL。

### Fixed

- 修复未登记自然语言假设的分支把标签与占位后缀拼成候选标题的问题。此类检索现在仅作为发现线索，不能自动变成文章候选。

## [0.10.2] - 2026-07-17

### Changed

- 主题缺口和边界调研先使用内容覆盖地图、历史查询和自然语言假设提出待验证问题；假设本身不构成需求证据，仍须通过 SERP、资料发现、页面正文和重复资格门。
- 学校、课堂和未成年人场景不再进入候选；高功率主题不因功率字样自动阻断。
- 同主要意图且证据齐全的调研线索现在会建立旧文章优化建议；该映射明确标注为 CMS 意图比对，不声称 GSC 查询归属页面。

### Fixed

- 修复分支标签要求标题机械复述原词，导致高功率光束终端、405nm 荧光矿物等有效站内方向在资格前被误拦截。
- 修复仅凭 high-power、laser 或 safety 等泛词就将技术子题判为旧文已覆盖；旧文必须命中特定技术对象，否则进入人工复核。

## [0.10.1] - 2026-07-17

### Changed

- `multi_source_topic_research 0.7.3` 将有限 SERP 预算分散到多个具体前沿；图谱/边界入口先逐条补前沿来源，GSC 入口优先补真实 PAA，资料查询和正文 URL 均按组轮询，避免第一组结果独占预算。
- Tavily 与 Firecrawl 保存明确 `input_refs`；确定性 PAA 和 AI 候选只附加其引用的资料查询所产生的页面正文。待补证据候选取得更强材料后可让旧判断失效并升级。
- `candidate_qualification 0.9.4` 的现有覆盖只对照活动博客正文；产品页继续用于主题图谱和内链，不再以规格文本冒充旧文章已回答某个信息意图。

### Fixed

- 修复边界查询超过供应商 100 字符上限、单一 PAA 列表占满 Tavily、同一资料查询占满 Firecrawl、Title Case 的 `Laser` 被分词为 `aser`，以及 0.50 分支阈值误删只命中一个明确场景词的候选。
- 修复泛“presentation mistakes”因只命中场景词而离开激光站点仍通过分支门；候选现在必须保留 `laser` 站点锚点，并受控归一 educator/teaching/school 场景。
- 修复 `alternatives` 中的 `na` 被误认成 `N/A` 占位 intent，以及 AI 使用 `External:45` 大小写时真实 evidence fact 被丢弃。

### Validated

- 真实 RUN #8 发现课堂/演示替代工具但因正文不匹配而保持 `needs_evidence`；RUN #9 证明来源与正文分散成功并定位分支误删；RUN #10 保存 8 个候选供严格重判。
- 最终真实库重判为 1 个合格新主题、5 个待补证据和 13 个重复/偏题结果。合格主题 `Why are laser pointers not allowed in school?` 同时有 PAA、精确来源、页面正文和未覆盖博客缺口；泛演示错误被阻断，PowerPoint 虚拟指针保留待补需求事实。
- 真实主题图谱仍为 65 篇博客 + 15 个产品，产品链接继续使用 `/p-{SKU}.html`；全量收集 67 项测试，Ruff 与 Git 差异检查通过。

## [0.10.0] - 2026-07-17

### Added

- SQLite schema v10 新增候选资格状态、规则/CMS/证据指纹、最接近旧文、三类证据检查、建议去向和人工复核审计；旧候选迁移后默认失效，不能沿用旧结论。
- 文章建议页新增“待人工核对的灰色主题”和“已归入旧文章判断”；只有 `qualified` 候选进入新文章栏，证据不足或失效结果不会进入制作。

### Changed

- `multi_source_topic_research 0.7.0` 与 `candidate_qualification 0.9.2` 实施“宽进严出”：GSC/SERP/PAA/Trends 才能承担需求角色，资料发现只找线索，至少一份页面级采集才能承担写作材料。
- 候选必须对照最接近的当前 CMS 正文；同意图或强正文覆盖归入旧文章，关系不明确时由人工复核页面职责，人工不能补证据或直接放行。
- CMS 重新导入后自动按当前正文重跑待处理候选资格；规则、CMS 或证据指纹变化会让旧资格失效。

### Fixed

- 修复 `based / use / case` 等通用表达把颜色主题错误匹配到功率文章的问题；核心意图词和常见词形现在独立比较。
- SQLite schema v11 将 LaserPointerHub 产品 canonical 与主题图谱链接改为真实 SKU 路由 `/p-{SKU}.html`，并迁移已有 15 个产品链接。

### Data maintenance

- 修改真实库前创建 `data/backups/seo_ops-before-research-quality-20260717T132353Z.db`；原始导入和快照均保留。
- 恢复 OAuth GSC #6 为唯一活动分析批次，并把意外测试导入 #7/#8 产生的 3 项内容标为非活动；当前主题图谱重新映射 65 篇文章和 15 个产品。

### Validated

- 真实“storage carrying protection”调研实际调用 SerpAPI 2、Firecrawl 2、Tavily 3、AI 1；唯一 DIY 收纳线索缺少需求 evidence，并在正文回放后归入现有 `Laser Pointer Case & Storage Guide`，新文章结果为 0。
- 真实主题图谱 15 个产品链接全部为 `/p-{SKU}.html`，没有 `/products/{slug}` 残留。
- 资格门回归覆盖需求/材料硬门、正确旧文匹配、强正文覆盖、人工复核和旧结果失效边界；完整 `pytest` 61 项、Ruff 和 Git 差异检查通过。
- 0.10.0 服务在 8788 实际验收：health、文章建议、文章制作和主题图谱均为 200；文章建议显示 0 个新文和 1 个旧文归类。

## [0.9.0] - 2026-07-17

### Added

- 数据导入页新增 Google Search Console 本机只读 OAuth：首次连接后可一键同步最终化的日期、查询、页面及 `date + query + page` 联合数据；CMS Blog/Product JSON 继续手工导入，Excel 保留为后备。
- SQLite schema v9 新增 GSC 连接状态、同步审计和查询—页面事实表；OAuth token 与客户端 secret 不进入数据库。
- 旧文章新增查询—页面前置闸门、起草后独立编辑审校、英语/Slug/原主题/篇幅/链接白名单/虚构经验的确定性检查，以及调用上限内修订。
- 新增版本化规则 `gsc_query_page_sync 0.9.0`、`old_article_gsc_readiness 0.9.0` 和 `old_article_content_quality 0.8.0`。

### Changed

- 以 `2026-06-22` 为不可越过的 GSC 可信起始日；同步只请求 `dataState=final`，以最近最多 28 天为当前窗口，有完整上一 28 天时才生成对比。
- 页面汇总信号默认只诊断：当前 query + page 行缺失时，排名/CTR 机会不得进入旧文章制作；点击损失还要求上一窗口的联合行。
- 主题调研输出统一为英语；候选在写入前同时检查当前 CMS、历史批次候选和本轮已接收候选，规范化词形后命中强主主题重叠即丢弃。
- 主题缺口/边界扩展不再被统一要求补 query→page；只有候选源自 GSC 查询且需要判断旧页归属时才要求联合数据。
- SEO Title/Description 字符区间只作编辑建议，不作为 Google 硬门槛；局部修改采用最小充分改动，避免为凑长度填充。
- GSC 属性自动匹配只接受 domain property 或 URL-prefix 根路径，同步前再次拒绝子目录/单页属性。
- 主题去重升级为 `multi_source_topic_research 0.6.1` 与 `new_article_candidate 0.4.1`：增加所选分支相关性和 CMS 摘要/SEO 字段/Markdown 小标题的结构化覆盖检查。

### Fixed

- 修复账号同时拥有根站点和单页 URL-prefix 属性时错误选择单页，导致全站同步只有 23 条汇总、0 条联合行的问题。
- 工作流派生数据清理不再删除调研方向记忆，避免下一轮自动重复刚研究过的课堂/演示分支。
- 修复只检查 CMS 标题和摘要而遗漏正文已覆盖子题，以及外部相关问题偏离本轮分支仍进入新文章候选的问题。

### Security

- OAuth 客户端 JSON 移至 `.secrets/google/gsc-client-secret.json`；`.secrets` 目录为 0700，客户端与未来 token 为 0600，并由 Git 忽略。
- OAuth 固定且验证唯一的 `webmasters.readonly` scope，使用 PKCE 和本机 loopback 回调；授权码访问日志禁用，token 只写本地文件。

### Data reset

- 清理前以 SQLite backup API 创建 `data/backups/seo_ops-before-0.9.0-20260717T032918Z.db`，quick check 通过，SHA-256 为 `f74f2fc21bf9dcbfc8d6c8b419cb9f1401eaa81cdf1ca066477226fd86865feb`。
- 删除旧规则产生的 1 次分析、18 条建议、2 个任务、1 次调研、8 个候选、12 次外部运行、9 条证据、2 次 AI 运行、4 条调研记忆和 12 个派生快照。
- 保留 CMS #3/#4 与手工 GSC #5；完成根站点 OAuth 同步后新增活动导入 #6，日期 2026-06-22 至 2026-07-14，含 542 条汇总和 1,188 条 query + page 联合行。
- 删除错误单页属性同步及其 23 条汇总、专属分析和快照；同步审计保留。候选清理前另建 `seo_ops-before-candidate-filter-20260717T053729Z.db`，只删除 6 个无效 pending 候选，完整外部调研概况仍保留。

### Validated

- 真实 8787 服务为 0.9.0；Windows 侧 `/imports` 显示正确根属性和 1,188 条联合行，`/research` 显示完整摄影与光绘 RUN #1 概况，`/opportunities` 只显示 2 个英文新主题。
- 真实候选回放证明传感器、基础设置、选激光、衍射帽和长曝光被现有光绘文章结构化覆盖阻断，20-60-20 摄影规则因偏离分支被过滤。
- `pytest -q`：55 项全部通过；`ruff format --check src tests tools` 与 `ruff check src tests tools` 通过。
- 真实库 `quick_check=ok`、外键违规 0；OAuth 凭据未写入 SQLite、Git、HTML 或快照。

## [0.8.1] - 2026-07-16

### Fixed

- 修复五步侧栏文字容器被旧的 22px 图标宽度规则误伤，导致中文每两字换行的问题。
- 主标题与说明文字恢复横向完整显示，同时保留 188px 窄侧栏；窄屏仍使用横向步骤导航。
- 增加样式回归检查，并用补丁版本刷新静态资源缓存。

## [0.8.0] - 2026-07-16

### Changed

- 主导航改为五步顺序：数据导入、主题调研、文章建议、文章制作、主题图谱；移除没有独立动作的“今日工作台”。
- 桌面侧栏缩窄，文章建议与文章制作都固定为“旧文章在左、新文章在右”。
- 调研页只显示入口、预算和运行概况；具体新文章主题统一在文章建议页出现。
- 文章建议卡只保留“开始执行、暂时跳过、不再推荐”；执行后卡片只进入文章制作页。
- 发布后任务立即退出当前制作页面；主题图谱只在 CMS 内容真实重新导入后显示覆盖。
- 调研预算移到调研页，文章 AI 调用上限移到制作页；完整连接参数仍在设置页。

### Fixed

- 研究候选不再创建 candidate 图谱节点，清除了旧候选节点；实际导入内容才创建 article_topic。
- 修复素材表单误绑定步骤路由的问题。
- 导入 GSC 或 CMS 后自动重算内部旧文章机会，不再要求去第三页另点刷新；自动分析不调用外部 API。
- 同一旧文章的多条规则先按 URL 合并，再排序显示；完成任务在新 CMS 与 GSC 数据回流前不会重复推荐。

### Data reset

- 按运营者确认的范围清除 13 轮旧分析、222 个旧机会、4 个旧任务、3 轮旧调研、27 次派生外部运行、11 次 AI 运行、14 条研究记忆、1 个候选图谱节点及 26 个派生快照。
- 保留 3 次有效导入、80 项 CMS 内容、659 条当前 GSC 指标、API/AI 设置和永久“不再推荐”记忆；随后重建为 17 个内部候选、2 篇优先旧文章、0 篇新文章。

### Validated

- `pytest -q`：45 passed；`ruff format --check src tests tools`、`ruff check src tests tools` 与 `git diff --check` 全部通过。
- 真实 8787 服务已重启到 0.8.0；五个主页面、设置、方法与样式均返回 200。
- 真实数据库下第 3 页显示 2 篇优先旧文章、0 篇新文章，第 4 页为空；本轮没有调用真实搜索或 AI API。

## [0.7.0] - 2026-07-16

### Added

- 新文章任务卡新增 A–H 素材清单和写作前确认；整理现有材料不调用搜索 API 或 AI。
- 新增本篇手工搜索提示词与粘贴入口；手工材料保存为不可变快照，重复提交不重复写入。
- 设置页新增“每篇文章 AI 调用上限”，范围 3–10、默认 4，并在交付详情中显示实际调用、上限和修订次数。

### Changed

- 新文章正常使用三次 AI：文章专属方案、完整初稿、编辑定稿；只有确定性检查失败才在上限内继续修订。
- 迁移原 `seo-workflow/write` 的文章类型篇幅、导语、Key Takeaways、H2、结论、FAQ、动态内外链和自审规则。
- 旧文章继续独立按元数据/极小修改、局部更新、同主题重写处理，通常一次 AI，Slug 与主要意图保持不变。
- 当前任务页说明与按钮收敛为同卡流程；发布后任务移入“观察与历史”。

### Fixed

- 新文章未确认素材前不会提前写作；关键素材不足时明确显示缺少 A、E、G 或权威来源，而不是泛化为 AI 连接错误。
- AI 调用上限与 SerpAPI、Firecrawl、Tavily、AI 整理的外部调研预算分开计算，避免把一次文章制作误算成整套系统额度。

### Validated

- `pytest -q`：42 passed；`ruff format --check src tests tools`、`ruff check src tests tools` 与 `git diff --check` 通过。
- 自动测试使用假 AI，未调用真实搜索或 AI API；8787 已启动 0.7.0 并核对设置页与任务页，第一篇真实文章闭环仍待运营者执行。

## [0.6.0] - 2026-07-15

### Changed

- “执行方案”改为单张文章任务卡；日常只保留“制作内容”和“我已发布”，发布后自动移入“观察与历史”。
- 新文章改为素材包、专属大纲与链接计划、前半篇、上下文缝合与后半篇、自动自审/一次修订的内部流程。
- 保留原 skill 的 100–150 词导语、4–5 个真实 FAQ、80–120 词结论和正文内嵌链接规格。
- 内链先由程序筛选最多 12 个相关候选，最终最多使用 3 个；找不到相关页面时不强塞。
- 外链只能来自本任务存证素材，并映射具体声明、来源角色和 evidence ID。
- AI 长任务超时提高到 150 秒；成功和失败按阶段记录，失败可安全重试。

### Validation status

- 按运营者要求，最终收尾后未继续运行自动测试、真实 API 或浏览器验收。
- 加入最终 FAQ、导语、结论和链接规则前，针对性测试为 11 passed；当前改动待后续复验。

## [0.5.0] - 2026-07-15

### Added

- 新增主题图谱：65 篇文章与 15 个产品各有一个主要主题，通用知识作为辅助关联。
- 外部调研新增 GSC 信号、主题缺口、七维边界扩展三个独立入口及研究记忆。
- 单轮预算上限改为 SerpAPI 10、Firecrawl 10、Tavily 20、AI 20。
- 候选支持“值得做、不再推荐、暂时跳过”；执行页一键生成修改稿或新文章 CMS 内容包，旧文章 Slug 锁定。
- 工作台与机会页统一为最多 2 篇旧文章 + 2 篇新文章；不足不补位，产品页与分析线索不占旧文章席位。
- 文章制作增加主要/辅助主题边界、权威来源角色、内外链白名单、精确标题/Slug 查重和自动自审。
- 导入页支持明确删除某次错误 GSC 批次及其专属派生数据。

### Fixed

- 删除错误 GSC 导入 #1/#2；当前真实库保留 CMS #3/#4 与干净 GSC #5。
- 修复无 GSC 查询候选时外部调研死循环，以及主题图谱重复同步的双重主主题风险。
- 六步技术清单不再作为日常执行界面。
- 泛 FAQ、frequently asked questions、common questions 和大而全指南不再进入新文章建议。
- 研究候选加入后会同步分析摘要中的当前席位数；旧规则版本自动标记为 deprecated。
- 修复外部调研新文章候选点击“开始制作”时，事实列表被误当 GSC 对象读取而返回 500 的问题。

### Validated

- 真实最小调研各调用 SerpAPI、Firecrawl、Tavily、DeepSeek Flash 1 次，四者均成功并产生 8 个待决定候选。
- 完整验收为 41 passed；Ruff 格式/规则检查和 Git 差异检查均通过。

- 自动测试覆盖错误 GSC 逐条删除、三入口调研、2+2 组合、旧文 Slug 锁定、新文章 CMS 八字段、来源角色和简化页面流程。
## [0.4.1] - 2026-07-14


### Added

- 每个站点现在只有一个“当前分析”GSC 批次；导入页明确区分当前分析、可信历史窗口与已排除历史。
- 新增 SQLite v5 的 imports.analysis_active 与唯一约束，旧批次和原始快照继续保留审计。
- 新增 SQLite v6 的 imports.quality_eligible：当前机会只看活动批次，稳定性判断可累计后续可信历史窗口，已确认异常的旧导入不计入。
- 新增 LaserPointerHub 主题图谱设计与 ADR：以稳定主题意图、同义标签、上下位关系、覆盖状态、拒绝记忆和新信号作为后续新文章入口。

### Changed

- 最新成功 GSC 导入取代旧批次生成当前机会；质量评估同时读取明确可信的历史窗口，不读取已排除批次。
- 外部调研页面和首页只把基于最新分析运行的调研视为当前结果；旧调研仍保存在数据库和证据快照中。
- 真实站点已仅用 2026-06-22 之后的新导入重建：17 个候选，1 个进入当前组合；旧 32 个机会退出当前工作台。

### Fixed
- 修复数据质量页与机会引擎选择不同 GSC 批次的问题；旧异常数据产生的进行中行动已取消并保留审计原因。
- “当前分析”现在必须明确绑定当前活动 GSC 导入；新批次导入后尚未重跑时不再回退展示旧分析、旧机会或用旧分析启动外部调研。
- 已取消行动默认移入“查看已归档”，不再混在当前执行队列；旧行动 #1 的真实状态已由误标的执行中修正为已取消。
- 修复单一活动批次导致独立观察窗口永远只能为 1、质量状态无法随未来正常导入积累的问题。

## [0.4.0] - 2026-07-14

### Added

- 新增“外部主题调研”页面：一次显式运行可组合 SerpAPI、Tavily、Firecrawl 和 AI，并逐项显示预算、真实请求、成功、失败与缓存复用。
- 新增可保存的单轮预算，默认 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1；界面硬限制分别为 50、50、100、1。
- 新增 SQLite v4 `research_runs`、`research_run_items`、`research_candidates`，保留运行口径、证据关联、候选门槛和局限。
- 新增 `multi-source-research-0.4.0` 规则与 ADR-0006；外部主题最多生成 8 个待核验/阻塞候选。
- 工作台拆分“第一方导入 3/3”和“外部连接 4/4”，并显示已保存外部证据数量。

### Changed

- 24 小时缓存复用不消耗真实请求预算；剩余预算会继续覆盖下一个尚未采集的明确 GSC 查询。
- Tavily 只做来源发现，Firecrawl 只采集选定公开页面，AI 只整理带 evidence ID 的本轮材料；任何来源都不重算原机会分数。
- AI 引用会归一化为完整 evidence ID；只接受唯一可解析的缩写/数字序号，未知、歧义或伪造引用被拒绝。
- 当前真实 AI 配置和运行记录使用 `deepseek-v4-flash`。

### Security

- 三种供应商快照继续在落盘前移除当前密钥及 URL 编码形式；真实快照扫描未发现配置密钥。
- 外部主题仍不能自动写作、发布或声称查询属于页面、预期收入或查询级转化。

## [0.3.0] - 2026-07-14

### Added

- 新增查询机会的一键 SERP + Google Trends 补证据；只有用户点击才调用，页面明确显示最多两次 SerpAPI 消耗。
- 新增 SQLite v3 `external_runs` 与 `evidence_items`：记录无密钥参数、请求哈希、输入 evidence ID、响应快照、SHA-256、大小、用量和安全错误。
- 新增 24 小时同口径快照复用，防止重复点击重复消费。
- 新增 `new_article_candidate 0.3.0`：从 GSC 查询事实派生新文章候选，缺少 query→page/意图复核时保持补证据，本站已有 SERP 结果或 CMS 强重叠时拦截。
- 新增“执行方案”页面；接受机会后冻结基线，生成有序步骤，支持完成、重新打开、取消和恢复。
- 新增 ADR-0005 与外部证据/人工执行数据契约。
- 自动测试增至 18 项，覆盖 v2→v3 数据保留、密钥脱敏快照、调用复用、文章候选门槛、证据步骤防绕过、执行/发布时间和取消恢复。

### Changed

- “接受”不再只改状态：现在建立 `action-plan-0.3.0`，且阻塞候选不能进入执行。
- 外部证据与原机会分数分离；SERP/Trends 不修改 GSC 指标、原候选强度或资格状态。
- 静态 CSS/JS URL 增加版本参数，升级后不会继续使用旧布局缓存；窄屏导航隐藏原生滚动条但保留横向滑动。

### Security

- 供应商响应中可能回显的密钥字节会在落盘前移除；参数、数据库、页面和错误消息均不保存凭据。
- 外部调用与执行状态表单只接受本机页面发起，系统仍不自动发布、删除或修改 CMS。

## [0.2.0] - 2026-07-14

### Added

- 新增“设置与数据连接”页面，统一配置 AI、SerpAPI、Firecrawl、Tavily 与 Google Trends 口径。
- 新增密钥留空保留、显式清除、本地 `.env` 0600 写入和跨站提交拦截。
- 新增不消耗搜索/模型额度的账户、用量和模型列表连接检测。
- 新增数据源角色矩阵，明确每个来源能与不能支持的结论。
- 新增 GSC 独立观察窗口和官方异常检测；观察不足时降低置信权重，命中异常时把曝光相关机会降为补证据。
- 新增 SQLite v2 可迁移 `source_connections` 状态表和 `gsc_signal_stability 0.2.0` 规则。
- 新增外部数据与 GSC 可信度基线文档、ADR-0004。
- 自动测试增至 12 项，覆盖密钥不回显、跨站表单拦截、错误脱敏和单窗口置信降级。

### Changed

- 原“AI 助手”导航合并为“设置与连接”；`/ai` 保留兼容跳转。
- 机会分析方法升级为 `cold-start-0.2.0`。

### Security

- 密钥不进入 SQLite、HTML、重定向错误、连接状态明细或 Git。
- 外部网络异常不再返回可能包含认证查询参数的原始异常文本。

## [0.1.0] - 2026-07-14

### Added

- 建立 SEO Ops System 独立项目和可交接文档体系。
- 增加 FastAPI + Jinja 本地网页：首页、数据导入、机会任务、方法证据、AI 页面。
- 增加 SQLite 数据模型，覆盖站点、导入、GSC、内容快照、规则、机会、行动、观察和 AI 运行。
- 增加不可变 SHA-256 原始快照和重复导入保护。
- 增加中文 GSC 标准/对比 Excel 全维度解析。
- 增加 Blog/Product CMS JSON 导入。
- 增加首版证据门槛机会引擎与允许不足三项的组合逻辑。
- 增加 OpenAI-compatible 可选 AI 适配器与禁用模式。
- 增加 7 项自动测试和真实 LaserPointerHub 数据验收。
