# Handoff — 当前接手状态

最后更新：2026-08-01（Europe/Paris）

## 2026-08-01 — 第十五次普通 resume 完成，但独立质量验收拒绝 promotion

- `be710d3` 已由本机推送并核验本地/远端一致；随后只执行一次普通
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing --ai-call-limit 36`，POST=1、
  无外层重试、未 refresh、未 promotion。Runner 返回 success，生成 22 个 active 文件，
  其中新增 resolved delivery、assembled draft/ledger、assembly report 和 comparison；
  `ai_runs 190→206`，DB SHA 变为
  `4a8246e8cd2d5ca7c4e70a657404a2c5a3d057431514cc03bad05da9c83047e9`。
  正式 draft、claim ledger、w2-state、`.env`、Action 和 Git 均未变化；archive=1，
  promotion manifest 不存在。
- 旧 assembly/comparison 门禁报告 `blockers=[]`，并给出
  `eligible_for_single_action_promotion`；MCP 独立阅读实际终态后拒绝 promotion：
  1. assembled frontmatter 继承正式旧 metadata，Summary/SEO Description 含
     `best` 与 `OSHA compliant options`，属于无 verified evidence 的推广/合规结论；
  2. `section-7bb01ba089` 正文 S095 写成 `520nm blue laser / 450nm green laser`，颜色与
     波长明确颠倒；该句未进入仅覆盖约 19.5% 句子的 claim ledger，因此 ledger 通过不能
     代表全文事实正确；
  3. 根因来自真实产品报告：B020 表格与 detail 的 Wavelength 都写成
     `520nm blue / 450nm green`，但其 Features 又正确写成 `450nm blue / 520nm green`；
     LP40 写成 `1064nm red laser`，而产品实际是 infrared beam + red positioning beam；
     B303 原有 title 532nm 与 attribute 650nm 冲突继续存在。
- 本次修复不编辑任何候选或正式文件，而是在三层 fail-closed：
  1. 新增共享技术一致性模块，识别常见激光波长与颜色的确定性矛盾；产品 registry 将
     这类内部矛盾记为 `wavelength_color_mismatch`，沿用现有
     `product_attribute_conflict` 路径阻止自动链接与写作；
  2. Section parser、checkpoint loader 和 output validator 都重新检查正文；冲突时只允许
     一次 `TECHNICAL CONSISTENCY REPAIR`，只能基于干净 package 最小修正或删除无支持
     细节。Pipeline 新增兼容可选计数 `section_technical_consistency_retries`；
  3. Assembly metadata validator 拒绝 Summary/SEO Title/SEO Description 中未经支持的
     authority/compliance 文案；Legacy metadata adapter 不再继承 `best`、`OSHA compliant`
     等值，而使用中性 topic-derived fallback。Assembly audit 也对全文再次执行波长/颜色
     矛盾硬门禁，防止绕过 section checkpoint。
- 对 Action #3 做真实只读预检：B020、LP40、B303 均被判为 conflicted；目录仍有 15 个
  product candidates，两个 required product section 各保留 2 个无冲突候选，最低产品链接
  总数 2 仍可满足。当前候选为 B017USB/B016，fit_level=`related_catalog`；下轮必须核验
  模型只把它们写成相关目录选项，不能声称适合、合规、安全或专为该场景设计。
- 当前完整候选在新 validator 下已明确无效，首先被
  `metadata.summary contains unsupported authority or compliance language` 拒绝；不能 promotion。
- 验证：sectional 非 Web 201/201 passed；Legacy/W1b 非 Web 237/237 passed；Ruff lint、
  新增/直接改动文件 format check、compileall、`git diff --check` passed。完整旧文件的
  `ruff format --check` 会重排大量历史代码，未为本次修复制造无关格式 diff；已知 Web
  `/etc/mime.types` Landlock 用例继续排除。
- 当前真实基线：Action #3=`w1b_pre_check/in_progress`，`ai_runs=206`，active=22，archive=1，
  promotion manifest 不存在，正式四个 SHA 不变。由于 active root 已有完整终态，普通
  `--resume-existing` 会被 runner 拒绝；提交并推送后，下一步必须只运行一次经审查的
  `--resume-existing --refresh-complete --ai-call-limit 36`，归档这份不合格完整候选后重建。
  不得第二次 runner、不得 promotion、不得手工编辑或删除 checkpoint。

## 2026-08-01 — 第十四次普通 resume：429 推进到内链占位符排版门禁

- `a15d69b` 已由本机成功推送并核验本地/远端一致；随后只执行一次普通
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing --ai-call-limit 36`，POST=1、
  无外层重试、未 refresh、未 promotion。正式 draft、claim ledger、w2-state、`.env` 和
  Action 全部不变；`ai_runs 188→190`，DB SHA 变为
  `7ce1514bf81bba018c8ab590819fbe3998bf6f8efae79a429a2a6227f56e9b51`。
- 901 合法恢复；049 在新 package 下重新生成并持久化。429 的新 H2
  `How to Compare Class 2 and Class 3R for Above-Ceiling Pointing` 生效，且本轮已不再触发
  authority/safety 结论门禁。真实停止点推进为严格格式错误：模型在一个段落内放置超过
  一个 ARTICLE/PRODUCT 占位符，现有确定性 paragraph normalizer 无法安全处理，修订后
  仍报 `a paragraph may contain at most one internal link placeholder`。429 未覆盖旧 checkpoint，
  后三节未到达；active=17、archive=1、promotion manifest 不存在。
- MCP 独立确认旧代码只会为首个字数或 evidence-strength 错误构建修订。此次首轮先暴露
  evidence 问题，第二次输出通过 evidence gate 后才暴露 link-layout 错误，因此没有对应的
  最终修订路径而直接停止。本次修复保持严格门禁，不允许同段多内链：
  1. 继续优先使用服务端 `_normalize_section_paragraphs`，仅在既有句界可以无语义拆段时
     自动重排，不增加 AI 调用；
  2. 同一句内含多个内链或结构化 Markdown 无法安全自动拆分时，新增一次 formatting-only
     `INTERNAL LINK LAYOUT REPAIR`，要求保留 H2、事实含义、placeholder IDs/顺序和 decisions；
  3. 每种 repair kind 最多执行一次；直接 link-layout 错误只修一次。仅当
     evidence-strength 修订后新暴露 link-layout 错误时，同一个 POST 才可使用第三次、
     也是最后一次格式修订；仍无外层重试；
  4. Pipeline 新增可选兼容字段 `section_link_layout_retries`，旧 version-1 结果缺失该字段
     仍可验证；最终失败会精确报告 `link_layout repair failed`。
- 回归：sectional 非 Web 196 项通过；Legacy/W1b 非 Web 237 项通过；Ruff、format、
  compileall、`git diff --check` 通过。已知 Web `/etc/mime.types` Landlock 用例继续排除。
- 下一步：提交并推送本次修复；核验真实基线后只运行一次普通 `--resume-existing`。继续
  禁止 `--refresh-complete`、第二次 runner、promotion、rollback 和手工改 checkpoint。

## 2026-08-01 — 第十三次普通 resume：从机构归因推进到技术等级结论

- `97f6f6e` 已由本机成功推送并核验本地/远端一致；随后只执行一次普通
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing --ai-call-limit 36`，POST=1、
  无外层重试、未 refresh、未 promotion。正式 draft、claim ledger、w2-state、`.env` 和
  Action 全部不变；`ai_runs 183→188`，DB SHA 变为
  `51fb5e32d286a642bccea06142d6106354e1a895e05beb6d4982c8e1586dcbb0`。
- 901 与 049 已在新 authority-free 合同下成功生成并持久化；049 的模型 H2 正确变为
  `How to Verify Applicable Laser Requirements...`。真实停止点推进到
  `section-42931f5db4`：旧合同仍问 `Which Laser Class Works...`，模型在初稿、普通修订和
  authority-free 修订中继续写出 Class 2 “generally considered safe / blink response offers
  protection”等无 verified quote 支撑的安全与技术选择结论。429 失败输出未持久化，后 3
  节未到达；active root、archive 和 promotion 状态均保持安全。
- MCP 独立确认根因不是机构名残留，而是零 verified quote 时仍给模型“选出更适合的技术
  等级/波长”的任务，并在最终修订中重新附带失败正文，持续诱导复制错误结论。本次在
  合同层统一修复：
  1. `Class 2 vs Class 3R — Which Laser Class Works...` 改为
     `How to Compare Class 2 and Class 3R...`；
  2. `Why Green (532nm) Is A Practical Choice...` 改为
     `How to Evaluate Green (532nm)...`；稳定 section ID 均不变；
  3. 零 verified quote 的初稿与两级修订明确禁止为 class、波长、输出或产品下安全、合规、
     preferred/best/practical/balanced 等结论，也禁止用 blink reflex/response 证明安全；
  4. 最终 authority-free 修订只接收干净 package，不再回灌前两次失败正文；
  5. validator 新增对 `generally considered safe`、blink-protection 和技术对象选择结论的
     严格识别，同时保留中性句式 `the practical choice depends on...`，避免误报。
- 对真实 Action #3 的 6 个模型 package 做只读预检：429 H2 为
  `How to Compare Class 2 and Class 3R for Above-Ceiling Pointing`，2d96 H2 为
  `How to Evaluate Green (532nm) for Ceiling Pointing`；全部 forbidden payload hits=[]、
  unsafe support IDs=[]，authority-free rule 生效，required citation minima 未降低。
- 回归：sectional 非 Web 组合 193 项通过；Legacy/W1b 非 Web 237 项通过；Ruff、format、
  compileall、`git diff --check` 通过。唯一未执行的 Web 测试仍是 MCP Landlock 阻止
  `openpyxl -> /etc/mime.types` 的已知环境限制，不是业务回归。
- 下一步：推送本次新提交；核验真实基线后只运行一次普通 `--resume-existing`。继续禁止
  `--refresh-complete`、第二次 runner、promotion、rollback 和手工修改 checkpoint。

## 2026-08-01 — 第十二次普通 resume 后改为整链路预检与双层受控修订

- `f993868` 推送并由 MCP 独立核验后，本机只执行一次普通
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing`；POST=1、无外层重试、
  未 refresh，archive 仍只有上一轮 1 份。正式 draft、claim ledger、w2-state、`.env`、
  Action 和 Git 均未变化；`ai_runs 180→183`，DB SHA 变为
  `f0b692118fe3f345c7118d116a366357d873bd97eaf0b04a61c156fce31ed9b0`。
- 危险 evidence support 过滤确认生效；901 重生成并落盘为 351 词。049 新 H2 正确，但模型
  仍自行写出 `OSHA publishes standards...`，初稿和唯一 evidence-strength 修订均被门禁
  拒绝，准确停止在 `section-04981e6fc7`；后四节未到达。
- 为保证质量同时减少“修一处→推送→再实跑”的循环，本次不增加外层重跑，也不放宽
  validator，而是一次性强化完整生成链：
  1. 当 section package 没有 verified quote 时，初稿 prompt 明确禁止正文出现 OSHA/FDA/
     EPA/FTC/CDC/NIOSH、完整机构名、regulator/agency 及任何 publishes/governs/requires/
     recommends 等机构归因；
  2. 模型可见的 heading、reader question、goal、must-answer、must-not-repeat、previous/
     next heading 和 previous summary 同步去除机构指向；049 最终模型 H2 为
     `The Critical Safety Line: How to Verify Applicable Laser Requirements on Construction Sites`，
     section ID 仍为 `section-04981e6fc7`；
  3. 第一次 evidence-strength 修订仍失败时，同一 POST 内允许一次最终 authority-free 重写；
     所有结果仍经过原 validator，第二次仍失败立即停止；
  4. Introduction/Takeaways/Conclusion/FAQ 同样禁止 named authority，并允许一次最终 bounded
     authority-free frame 修订；pipeline 新增精确 frame retry 计数。
- 对当前 Action 的 6 个 section package 做了真实只读整链路预检：全部
  `authority_in_user_payload=[]`、`unsafe_support_ids_still_visible=[]`，required citation
  minima 均保持；不存在因过滤而提前耗尽 required evidence 的章节。预检脚本已删除，
  未修改任何 data/正式产物。
- 下一步：完成回归、提交并 push。独立核验后只运行一次普通 `--resume-existing`；不得
  refresh、手工删 checkpoint 或 promotion。

## 2026-08-01 — 第十一次普通 resume：049 被危险未核验 support 反复诱导

- `2851982` 推送并由 MCP 独立核验后，本机只执行一次普通
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing`；POST=1、无外层重试，
  未使用 `--refresh-complete`，archive 目录仍只有上一轮那 1 份。
- 正式 draft、claim ledger、w2-state、`.env`、Action 和 Git 均未变化；数据库只新增
  4 条 `legacy_write_sectional_body` 审计，`ai_runs 176→180`，DB SHA 变为
  `1fa9d1c3d744a4e7af496be0e799aa6931fb3358349b3fe55822ec259f3b4dbe`。
- 901 在新合同下成功重生成并落盘为 439 词；049 使用新标题
  `How to Verify OSHA Requirements...`，但初稿和唯一 evidence-strength 修订仍写出
  `OSHA guidance states... Class 2 and Class 3R ... safer`，因此准确停止在
  `section-04981e6fc7`。049 旧 checkpoint 未被覆盖；后四节未到达，不能算 resumed。
- MCP 独立检查发现持续诱因不是 H2，而是写作包仍把两条危险 support 原样送给模型：
  一条未核验 quote 写着 Class 2/3R 更适合施工空间，另一条 key finding 写着 Class 2
  可防止视网膜损伤且是推荐等级。门禁虽然禁止模型采用这些结论，prompt 却同时把它们
  定义成“唯一事实来源”，形成输入层矛盾。
- 修复只改变模型可见写作上下文，不改 evidence ledger、candidate registry 或正式材料：
  任何 `support_basis != verified_quote` 且 support 自身含监管归因、推荐/合规结论、绝对
  安全措辞或技术等级偏好时，从 section package 和 external-citation selected IDs 中剔除，
  并记录 `unverified_support_contains_strong_claim`。Required `min_required` 永不下调；过滤
  后不足时在 AI 调用前 fail-closed。
- 当前 Action 真实只读重建确认：049 模型包只剩 `ev_342a1c3c35f7`，危险 OSHA/Class 2
  support 均不再出现；external citation 仍为 required min=1/max=1；049 新 package SHA
  为 `056d8a5d82bebca6e7481a1cc53570d1b06de87308b9dec87aff03963ad9c26e`。
- 下一步：提交并 push；独立核验后仍只运行一次普通 `--resume-existing`。不得 refresh、
  手工删 checkpoint 或 promotion。

## 2026-08-01 — 第十次 refresh 已归档旧候选，049 语义修订仍失败

- `b4912c2` 推送并由 MCP 独立核验后，本机只执行一次
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing --refresh-complete`；
  runner 实际发送 1 个正式 POST，未外层重试。旧完成候选的
  `resolved-delivery.json`、assembled pair、assembly report 和 comparison 已由 runner
  事务归档到 `drafts/sectional-archive/.../20260801T044604Z-271094745-e960d376b609/`，
  manifest SHA=`9b1187c26729966f685365340621ff81e0892dc75700a7a47f6a4224b6960d0d`，
  5 个归档文件 SHA 全部匹配；active root 只剩 7 个 checkpoint + 10 个 ledger
  checkpoint，无 `.tmp-*`、promotion manifest 或终态产物。
- 正式 draft、claim ledger、w2-state、`.env`、Action 和 Git 均未变化；数据库只新增
  4 条 `legacy_write_sectional_body` 审计，`ai_runs 172→176`，DB SHA 变为
  `8f35e6cc6381bb5243dc3dde80d0f8d97ff445c6c10c06ea26267dc0fec40138`。
- 本机报告把新落盘的 901 误判为“修订后仍失败”，并把未覆盖的后续旧 checkpoint
  误判为 resumed。MCP 逐节重建确认：901 的新 checkpoint（489 词）已通过；其新摘要
  改变了后续 package chain，049/4293/2d96/7bb0/b92c 的当前 package SHA 全部与旧
  checkpoint 不匹配。真实停止点是 049 初稿 + 一次 evidence-strength 修订仍失败，
  因此旧 049 文件没有被覆盖。
- 根因是合同自身矛盾：证据分布为 0 verified quote / 4 unverified quote / 8 key
  finding，门禁禁止无 verified quote 的 OSHA/FDA 权威归因，但原 H2 仍要求模型回答
  “What OSHA Says…”。修复把 `What <authority> Says About <subject>` 中和为
  `How to Verify <authority> Requirements for <subject>`，并保持原 stable section ID；
  `the only choice` → `A Practical Choice` 的稳定 ID 映射继续保留。
- Evidence-strength 错误现在携带服务器检测到的具体违规段落；唯一修订提示会明确列出
  offending passage、当前 verified evidence IDs，并在 0 verified quote 时要求删除
  权威归因本身、改写为现场核验和中性安全控制，而不是只加模糊限定词。第二次仍失败时，
  错误包含 `section_id + heading + repair kind`，避免再次误判停止位置。
- 真实只读重建确认 6 个 section ID 不变，049 和 2d96 标题均已中和，当前 6 个旧 body
  checkpoint 都会由 package SHA/heading 校验自动失效。下一次 active root 已是 partial
  状态，只能用普通 `--resume-existing`；不得再次使用 `--refresh-complete`，不得手工删除
  checkpoint，也不得 promotion。

## 2026-08-01 — 第九次调用在 POST 前被 complete-artifact 守卫拒绝

- `5bb0c50` 推送并由 MCP 独立核验后，本机只调用一次
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing`。Runner 在启动服务前
  发现 active sectional root 仍含上轮的 `assembled-draft.md`、
  `assembled-claim-ledger.json`、`assembly-report.json` 和
  `shadow-comparison-action-3.json`，按原设计拒绝继续。
- 本轮真实结果为 `http.post_attempts=0`，没有启动服务、没有发送正式 POST、没有新增
  ai_runs；正式四个 SHA、Action、数据库、Git 和 22 个 sectional 文件全部未变。
  本机报告结尾“只发送了一个正式 POST”是固定模板残留，不能覆盖 runner JSON 的 0。
- 原守卫防止误覆盖一个完成候选，本身正确；缺口是没有正式机制在新代码合同下保留旧
  完成候选并开始新一轮。禁止手工删除或移动 active root 文件。
- 新 runner 参数 `--refresh-complete` 必须和 `--resume-existing` 一起使用。它在 active
  root 外的 sibling `sectional-archive/<slug>/<timestamp>-<comparison-sha>/` 中事务归档：
  `resolved-delivery.json` 加四个终态产物，并写 `archive-manifest.json`，记录 Action、
  Git HEAD 和每个文件 SHA。所有 checkpoint/ledger checkpoint 保留在 active root，
  由正式 package SHA 逻辑决定复用或重生成。
- Promotion manifest 仍一律拒绝；终态集合不完整、存在未知文件或归档中途失败均 fail
  closed。归档失败会把已移动文件恢复到 active root。真实 Action 当前 22 文件集合已
  只读确认完全符合新入口条件，没有 promotion manifest 或未知文件。若归档失败后的
  回滚本身也失败，未恢复文件会保留在 `.tmp-*` 恢复目录并报告路径，绝不会清理丢失。
- 下一步：提交并 push runner 修复；独立核验后，本机只运行一次
  `--resume-existing --refresh-complete`。不得手工整理文件，也不得 promotion。

## 2026-08-01 — 第八次 shadow 结构成功，但语义验收拒绝 promotion

- 基线 `a2b19e88c92483e46d273e5f9053c2b22e1ced80` 下，Action #3 在非沙箱主机
  只执行一次 `--resume-existing`：POST=1、未外层重试，结果 `success`，新增
  `assembled-draft.md`、`assembled-claim-ledger.json`、`assembly-report.json` 和
  `shadow-comparison-action-3.json`。正式 draft、claim ledger、w2-state、`.env`、
  Action 和 Git 均未变化；`ai_runs 157→172`，promotion manifest 不存在，rollout
  最终 off。
- 自动结构门禁通过：comparison blockers=[]，推荐值为
  `eligible_for_single_action_promotion`；新稿约 3380 词、41 claims、12 个链接，
  全部 URL 唯一。该值只是机器结构资格，不是最终推广批准。
- MCP 人工语义验收拒绝 promotion。正文把空 quote 的合成 key finding 写成
  “OSHA/FDA 推荐或允许 Class 3R”“Class 2 是 safest default”“532nm 是 the only
  choice”等权威归因、合规结论和绝对安全措辞。当前 12 条证据有 4 条 quote 文本、
  8 条只有 key finding，但 0 条带 `quote_verified=true`；旧 assembled claim ledger
  至少 8 条高风险 claim
  只依赖空 quote 的研究摘要。正式候选不得推广。
- 修复将 `support_basis=verified_quote|quote|key_finding` 从 evidence ledger 恢复到旧 Action 的
  内存 cards，不改源文件；检索 concepts 不再进入写作事实上下文。权威归因、监管/
  合规推荐、绝对安全和 only/best/go-to 等绝对措辞必须有 verified-quote evidence，
  否则正文或 frame 只允许一次受控中和改写；合并后的 claim ledger 在 assembly 前
  再次 fail-closed。
- Legacy brief 中 `the only choice` 会变为 `A Practical Choice`，但 stable section ID
  映射保持不变。真实只读验证确认 6 个 section ID 未变，旧 checkpoint 因新的 package
  SHA 全部自动失效；三档证据门禁把旧成功候选的首个 blocker 提前捕获为 S005。
  不得手工删除 checkpoint。
- 下一步：完成测试、提交并 push；push 独立核验后，再运行一次单独 shadow。即使下一次
  机器 comparison 仍显示 eligible，也必须再次逐条验收正文、claim ledger、assembly 和
  comparison，禁止自动 promotion。

## 2026-08-01 — 第七次 shadow：章节仅差 4 词时受控修订

- `8922636` 推送后，Action #3 在非沙箱主机只执行一次
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing`；正式 POST=1，未重试，
  正式 draft、claim ledger、w2-state、`.env`、Action 和 Git 均保持不变。
- `ai_runs 153→157`，四条均为成功返回的 `legacy_write_sectional_body`。前三节按新的
  350–500 合同重新生成并保存为 372、413、426 词；第四节 “Why Green (532nm)…”
  返回 346 词，被严格 350 下限拒绝，因此没有覆盖其旧 checkpoint，流程立即停止。
- 本次失败发生在 Phase 3，尚未生成新的 resolved delivery、assembly 或 comparison。
  目录中的 `resolved-delivery.json` 仍是上一次运行留下的旧文件；其中 2525 词、重复
  内链和 0 个外部引用等统计不能用来判断 `8922636` 的全文链接协调是否生效。
- 根因是模型对字数的近似计数反复落在下限外 2–4 词，而生成序列原先对任何字数偏差都
  直接中止，没有局部修订机会。修复保留 350–500 严格门禁，只在唯一异常精确匹配
  `section word count N is outside MIN-MAX` 时允许同一节一次受控 AI 修订；其他错误不重试，
  第二次仍越界也立即失败。修订必须保留 H2、事实含义、占位符、链接决策和段落结构，
  不得引入新事实、URL、产品、规格、法律、统计或 evidence ID。
- 新增三项回归：一次修订成功并原子保存、非字数错误不重试、第二次字数失败不再重试。
  Sectional generation 专项 34 项通过；Sectional 联合组排除已知 MCP
  `/etc/mime.types` Landlock Web 导入限制后 173 项通过；Legacy/W1b 非 Web 兼容组通过。
  尚未运行下一次真实 shadow，也未 promotion。

## 2026-07-31 — 第六次 shadow 全文链接协调与字数合同修复

- `fc1b142` 推送后，Action #3 在非沙箱主机环境执行一次
  `tools/run_sectional_shadow.py --action-id 3 --resume-existing`；只发送 1 次正式 POST，
  HTTP 303 返回 error，没有重试。
- 流程成功恢复已有 2 个正文 checkpoint，并继续生成全部 6 个正文章节、article frame、
  10 个 ledger checkpoints 和 `resolved-delivery.json`，共保留 18 个中间产物；数据库
  `ai_runs` 从 138 增至 153。正式 draft、claim ledger、w2-state、`.env` 和 Action 状态
  全部不变，promotion manifest 不存在，rollout 最终为 off。
- 第六个 blocker 为 Phase 5 全文门禁：同一 Professional Use Guide 出现 3 次，B020 与
  LP40 各出现 2 次，形成 4 个 `duplicate_internal_link_target`；外部引用为 18 个，而
  2770 词旧候选按新运营要求最多应保留约 5 个。
- 根因不是全文 gate 过严，而是 Phase 3 各章节独立选择链接，Phase 4 绑定时没有全文
  唯一目标和总额度协调。旧 brief 还把全文目标写成 1800–2500，默认单节合同仅
  220–360，导致旧候选只有约 2770 词。
- 修复方向已落地：Sectional Cluster Content 全文目标改为 3000–4200，默认正文单节
  350–500；外部可见引用约每 600 词 1 个。Phase 4 先满足 required 章节，再按全文
  URL 唯一性和硬上限分配链接；重复 ARTICLE/PRODUCT 降级为普通锚文本，重复或超额
  CITE 只移除可见链接，approved evidence 仍保留给 claim ledger。
- 对当前真实 7 个 checkpoint 做只读离线重组：旧候选得到 ARTICLE=5、PRODUCT=2、
  external citation=5，三类 URL 均无重复；可见正文约 2608 词，因此会被新的 3000
  最低门禁正确拒绝并要求重新生成更完整章节。
- Phase 1–7 sectional、adapter、repair、rollout 和 runner 联合测试在排除一个已知 MCP
  `/etc/mime.types` Landlock 导入限制后全部通过；Ruff 与 compileall 通过。尚未运行
  新的真实 shadow，也未 promotion。

## 2026-07-31 — 章节化写作与链接机会门禁正式规划

- 已确认整体方向：全局 Article Blueprint 保证逻辑，H2 Section Contract 控制每节完整
  上下文，Evidence Contract 绑定事实，Link Contract 分开处理文章内链、产品内链和外部
  引用，最后由服务端确定性组装并运行全文门禁。
- 链接最低值不统一设为 0。服务端先输出 `required|recommended|none` 机会状态；
  `required` 最低 1，`recommended` 可为 0 但必须返回拒绝原因，`none` 才允许无决策地
  不放该类链接。产品和文章内链分别计算，不能互相替代。
- 产品链接只要求章节已进入 `compare|select|apply` 等商业阶段、商品在售、数据无冲突且
  与文章主题或本节内容相关；小目录网站不再要求每个商品都与细分用途完全匹配。
  候选分为 `strong|contextual|related_catalog|approved_constraint`，匹配强度只决定
  推荐措辞，不决定是否可链接。商业阶段存在有效候选时最低要求 1 个；安全警告、法规、
  常见错误和 FAQ 章节默认禁止或不要求具体产品链接。
- 写作模型只输出 ARTICLE/PRODUCT/CITE 占位符；URL、库存、ID、锚文本和重复目标由
  服务端验证和绑定。
- 已撤回本会话未完成的 claim-ledger 试验补丁，恢复干净 `71e6c91`，避免在旧整篇架构
  上继续堆叠临时修复。
- 新增 ADR-0026 和 `docs/SECTIONAL_WRITING_IMPLEMENTATION_PLAN.md`。正式实施按 Phase 1–7
  原子推进，每步更新计划、Handoff、Worklog 和测试结果。

### Phase 1 已完成

- 新增 `src/seo_ops/services/sectional_writing.py`，提供 Article Blueprint、Section
  Contract、Section Link Contract 的确定性构建、严格校验、加载和带回滚持久化。
- Section ID 只取决于规范化标题，不因章节重排而变化；重复标题 fail-closed。
- 未经 Phase 2 评估的链接机会只能是 `unassessed + min_required=null`，不能静默写成 0。
- `required` 状态必须存在候选且最低值至少为 1；安全/法规章节产品链接为明确 `none`。
- 10 项专项测试、Ruff、compileall 和 `git diff --check` 通过；新模块未被正式 Legacy
  workflow 导入，W0/W1b/W2 行为不变。
- MCP 沙箱无法完成全量 pytest：测试收集导入 `openpyxl` 时读取 `/etc/mime.types`
  被 Landlock 拒绝。这是已知执行环境限制，不是测试断言失败；提交前仍须由本机运行
  完整 `.venv/bin/python -m pytest -q`。

### Phase 2 已完成、测试并推送

- 新增 `src/seo_ops/services/sectional_context.py`：只读解析站内文章地图、产品目录和
  evidence cards，构建候选注册表、目录画像、章节候选评分、Link Opportunity Gate、
  Context Manifest 和 shadow report。
- 产品解析器是跨行业的：只要求 ID/SKU、Title/Name 和 URL；其他列作为通用
  attributes。测试已覆盖激光产品、喷码机和园林工具，不在核心逻辑中硬编码波长、功率、
  打印高度或电池平台。
- 产品目录是商业事实基线。旧 brief 的自然语言不会变成硬过滤条件；只有明确来源为
  `site_policy`、`catalog_policy` 或 `operator_approved` 的结构化约束才可过滤商品。
- 正式合同固定 `content_language=en`。旧中文 brief 条目保存到
  `brief_points_rejected`，不进入 AI 写作上下文、产品评分或链接决策；混合 FAQ 标题可
  删除中文括号，仍包含中文的 H2 fail-closed。
- 新增 Catalog Data Quality Report 逻辑区段并随 sectional shadow report 持久化：产品
  标题、目录表格或详情字段互相冲突时，报告
  产品 ID、冲突值、严重程度、修复建议和 `blocking_for_auto_link=true`。修正前只阻止
  该 SKU 自动链接，不影响其他产品。
- 新增只读工具 `tools/sectional_shadow_preview.py`。默认只打印摘要，不写 Action 文件，
  不调用 AI。
- Action #3 只读 shadow 已成功读取 65 篇文章、15 个在售产品、12 张 evidence cards。
  B303 被识别为标题 `532nm` 与目录属性 `650nm` 冲突，输出
  `catalog_attribute_conflict` 并从自动产品链接候选排除。
- Phase 1+2 共 31 项专项测试通过；Ruff、compileall、`git diff --check` 通过。
  正式 W0/W1b/W2 仍未导入新模块，draft、claim ledger、w2-state、数据库和真实 Action
  未修改。
- 本机完整 `pytest -q`：`456 passed, 1 warning`；Phase 1+2 专项 `31 passed`；Ruff、
  compileall、`git diff --check` 全部通过。
- Phase 2 已提交并推送：`666b3cd4a107ac8a3a3a5eed9b91a5eedfd831cc`
  (`feat: add site-aware sectional context shadowing`)；本地与远端一致，工作区干净。

### Phase 3 已完成全量验证并形成本地提交

- 新增 `src/seo_ops/services/sectional_generation.py`，提供完全独立、shadow-only、可注入
  generator 的逐 H2 生成引擎；没有导入或替换正式 Legacy W0。
- 每节只接收当前 Section Contract、相关 evidence/文章/产品候选、前一节短摘要和下一节
  标题；真实 Action #3 的正文合同现为 6 节，单节 user prompt 约为 3.6K–6.5K 字符，
  远小于整篇上下文。旧 brief 的 FAQ 已延后到 article frame，不再重复生成正文 H2。
- AI 必须返回精确 H2、2–5 个完整段落、ARTICLE/PRODUCT/CITE 占位符和机器可读链接
  决策；原始 URL、HTML/Markdown 链接、越权 ID、中文正文、重复 H2、词数越界和链接
  最低值不足全部 fail-closed。
- 产品候选支持 `strong`、`contextual`、`related_catalog` 和 `approved_constraint`。
  `related_catalog` 允许作为相关商品推荐，但提示词明确禁止宣称其专为该用途设计、经过
  该场景验证或符合未提供的法规。`compare|select|apply` 有有效产品时使用
  `required + min_required=1`，避免模型长期输出 0 产品链接。
- 每节生成后原子保存独立 checkpoint；重新运行会校验 package SHA、正文、链接决策、
  语言、词数和摘要后续跑。上下文变化、损坏 checkpoint 或部分写失败都不会被误恢复。
- Introduction、Key Takeaways、Conclusion 和 FAQ 在主体完成后通过独立短输入生成，并有
  单独 checkpoint；不得引入新事实、链接、产品或引用。
- 兼容旧 Action 的编号加粗格式 `1. **H2: ...** — note`；只提取英文 H2，中文历史说明
  保留审计，不进入正式写作上下文。编号大纲最后一节不会再误吸收后续链接策略列表。
- 文章和产品候选都动态识别站点高频品类词。文章链接仍要求章节具体相关，避免把鸟类
  驱赶文章塞进“施工常见错误”；产品端则保留相关目录 fallback，适合产品数量较少的网站。
- Action #3 只读预览结果：开头、安全、常见错误、FAQ 不放产品；Class 对比和选型章节
  产品门禁为 `required + min_required=1`；B303 仍因 532nm/650nm 冲突被排除。未调用
  AI，未写 checkpoint，未修改正式 Action。
- Phase 1–3 联合专项：54 passed；Ruff、compileall、`git diff --check` 全部通过。
- 本机完整 `pytest -q`：`479 passed, 1 warning`；唯一 warning 为既有
  Starlette/httpx 弃用提示。Phase 1–3 专项 `54 passed`；Ruff、compileall、
  `git diff --check` 全部通过。
- MCP 完整 pytest 仍会在收集阶段被既有 Landlock 权限阻止：`openpyxl -> mimetypes`
  读取 `/etc/mime.types` 返回 `PermissionError`；这不是代码回归。
- Phase 3 已提交并由本机认证环境成功推送：
  `7894080fe944fbb2ad96905bbeca73c809caf011`
  (`feat: add resumable sectional generation`)；本地与远端一致。

### Phase 4 已完成全量验证并形成本地提交

- 新增 `src/seo_ops/services/sectional_delivery.py`，服务端只按最终 Link Contract 白名单
  绑定 ARTICLE/PRODUCT/CITE 占位符；全站 registry 中存在但未获本节授权的 ID 仍被拒绝。
- registry SHA、上下文 topic/order、候选 ID、冲突商品、残留占位符和 URL 绑定均
  fail-closed；B303 等目录冲突商品无法被绑定。
- H1、Introduction、Key Takeaways、正文 H2、Conclusion、FAQ 全部确定性组装后才分配
  全局 S-ID；后续添加 frontmatter 不改变正文句子表，避免 claim ledger 与最终草稿漂移。
- 正文章节和 `frame-introduction|takeaways|conclusion|faq` 四个单元分别生成 claim ledger；
  每次只传本单元全局 S-ID 和批准 evidence，模型不得返回 claim_text 或越权 evidence。
- claim_text 由服务端从最终 Markdown 注入；thinking 显式关闭，输出预算 4000；不可重试
  `length/content_filter/tool_calls` 立即停止，非法 JSON 最多重试一次。
- resolved delivery 和每单元 ledger 使用 SHA 绑定、原子 checkpoint、损坏/过期恢复拒绝。
- 合并结果已通过现有 Legacy `_validate_claim_ledger_json` 权威校验器。
- Phase 1–4 联合专项 `72 passed`；Ruff、compileall、`git diff --check` 通过。
- 本机完整 `pytest -q`：`497 passed, 1 warning`；唯一 warning 为既有
  Starlette/httpx 弃用提示，无其他 warning。
- Action #3 只读合同重建确认正文 6 节；未调用 AI、未写真实 Action 文件，正式
  W0/W1b/W2 仍未导入新模块。
- Phase 4 已提交并由本机认证环境成功推送：
  `347fb832d51db5ebfb3332b006970487bc5810af`
  (`feat: add sectional delivery and ledgers`)；本地与远端一致。

### Phase 5 shadow 代码与全量验证已完成，已本地提交待推送

- 新增 `src/seo_ops/services/sectional_assembly.py`：不调用 AI，确定性生成 canonical
  frontmatter、可见 FAQ 对应的 FAQPage JSON-LD、最终 draft 与重新绑定最终 draft SHA
  的 claim ledger。
- frontmatter 和 FAQ JSON-LD 不得改变正文 S-ID。共享权威分句器现明确忽略 fenced code
  与 `application/ld+json` script；新增回归测试锁定添加 metadata 后句子表字节级不变。
- Key Takeaways 改为旧 W1b 可识别的 blockquote 格式：`> **Key Takeaways**` + 3–5 条。
- 全局门禁检查目标字数、H1/H2 顺序、FAQ/Takeaways 数量、跨章节重复句/段、未登记 URL、
  重复文章/产品目标、泛化锚文本和硬性链接上限。
- 原有 blog/product/external 按字数比例仅保留为 advisory metrics/warnings；不会为了比例
  强塞链接。产品是否相关仍由 Phase 2–3 的 Link Contract 决定，Phase 5 不重新卡用途场景。
- 外部引用允许使用域名锚文本；描述性锚文本硬门禁只适用于文章与产品链接。
- assembled draft、assembled claim ledger、assembly report 三文件事务写入；部分 replace
  失败会恢复旧版本，损坏或 SHA 不一致的 bundle 不会恢复。
- Phase 1–5 sectional 专项 `84 passed`；额外 sentence-ID 与旧 W1b/FAQ/修订兼容回归通过；
  Ruff、compileall、`git diff --check` 通过。
- 本机完整 `pytest -q`：`510 passed, 1 warning`；唯一 warning 为既有
  Starlette/httpx 弃用提示，无其他 warning。
- 兼容回归 `78 passed`；覆盖 sentence-ID、FAQ schema、W1b 修订、mixed fact cleanup 和
  SEO Title normalization。
- MCP 完整 pytest 仍因既有 Landlock 限制无法读取 `/etc/mime.types`，不是代码回归。
- 正式 W0/W1b/W2 尚未导入 Phase 5；未调用真实 API、未运行真实 Action、未写正式产物。
- Phase 5 已提交并由本机认证环境成功推送：
  `287235eb5e18f2bac47ab2164206b4f2fc5d178c`
  (`feat: add deterministic sectional assembly`)；本地与远端一致。

### Phase 6 shadow 局部修订已完成并本地提交，待推送

- 新增 `src/seo_ops/services/sectional_repair.py`，把 W1b/W2 的 checks、fact issues、
  fix items、link issues 和布尔 gate/error 字段规范化为可审计 repair plan。
- 失败可通过 `section_id`、全局 S-ID、完整 sentence text、绑定 URL 和 H1/Introduction/
  Takeaways/Conclusion/FAQ 别名定位。评分器/蚕食检查器异常、不可定位评分失败和蚕食阻塞
  保持 global blocker，绝不让 AI 猜测修复位置。
- 修复动作分为 `section_rewrite`、`link_repair`、`ledger_repair` 和 `frame_rewrite`；
  单次最多两轮。Link Repair 必须保持全部读者可见文字不变，只能调整批准占位符和链接
  decision；恶意或损坏 checkpoint 会被拒绝恢复。
- 多章节同轮修复按原顺序执行，后一节收到前一节修复后的新摘要。未目标正文输出保持
  字节级不变；正文改写后刷新 article frame，FAQ-only/frame-only 修复不调用正文生成器。
- 重新组装后的全局 S-ID 发生移动时，未改单元的旧 claims 按唯一规范化 `claim_text`
  映射到新 S-ID；映射不唯一、证据不再批准或 package 不一致时，只对该单元重新审计。
- 修复结果重新经过 Phase 4 delivery/ledger 合并和 Phase 5 assembly 全局门禁；result
  持久化完整 repair plan、section run、article frame、delivery、ledger run 和最终 assembly，
  并重新验证嵌套 SHA、单元覆盖、计数和 changed/unchanged 声明。
- 本机完整 `pytest -q`：`534 passed, 1 warning`；唯一 warning 为既有
  Starlette/httpx 弃用提示，无其他 warning。
- Phase 1–6 sectional 专项 `108 passed`；Phase 6 新增 `24 passed`。
- Legacy/W1b 兼容回归未排除 `TestPrecheckGateDisplay`：`263 passed, 1 warning`。
- MCP 完整 pytest 仍会在收集阶段被 `/etc/mime.types` Landlock 权限阻止；这是插件
  沙箱限制，不是代码回归，本机完整套件已证明全部通过。
- Ruff、compileall、`git diff --check` 全部通过。正式 W0/W1b/W2 尚未导入 Phase 6；
  未运行真实 AI/API 或 Action，未写正式 draft、ledger、state 或数据库。
- Phase 6 已本地提交：`358929afac5d4f420b0c288f3a75ad8095762c59`
  (`feat: add sectional repair workflow`)；插件环境无法完成远端认证，当前分支相对远端
  `ahead=1`。

### Phase 7 已本地提交；外部审计 hotfix 已完成专项验证，待本机全量验证

- 新增 `off|shadow|action` 三态 feature flag，默认 `off`。`shadow` 只生成 sectional
  候选和比较报告；`action` 只有显式 Action ID 白名单才能提升，其他 Action 继续旧 W0。
- W0 仍先按原路径生成并原子写入 Legacy draft/claim ledger；sectional 在其后运行。
  sectional 任何异常都返回 `failed_keep_legacy`，不撤销旧 W0 成功结果。
- 当前接线状态必须准确理解：`legacy_workflow.py` 已在 W0 成功写入旧正式 pair 后调用
  sectional rollout adapter；默认 `off` 时立即短路。W1b/W2 未导入 sectional repair
  自动接管路径，正式后续 gate 仍是原 Legacy gate。
- 已补已有正式 pair 的受控 shadow Web 入口：
  `POST /actions/{action_id}/legacy/stage/sectional-shadow`。它只允许 `mode=shadow`，读取现有
  write brief、coverage contract、evidence cards、draft 和 claim ledger，不重跑 W0、
  不更新数据库阶段、不允许 promotion。运行前后字节级校验正式 pair；异常改动会原子恢复。
- 新增完整 shadow pipeline：合同、候选、逐节正文、article frame、章节 ledger、
  delivery 和 assembly 均使用原有 checkpoint 与全局门禁，正式 pair 仅在 promotion
  协议通过后替换。
- shadow comparison 对比字数、链接数、claim 覆盖、重复句、AI 调用、重试和空响应。
  claim 覆盖退化、重复增加、出现空响应或重试超过两次时保持 Legacy。
- promotion 要求 Action 白名单、比较报告批准、assembly SHA 匹配、正式 pair SHA 未变化。
  旧 pair、prepared manifest 和 promoted manifest 均写入后才完成提升；任一步失败恢复旧 pair。
- rollback 验证 promoted pair 和备份 SHA；最终 rollback manifest 写失败时恢复 promoted pair。
- 独立 AI 调用预算 `SEO_OPS_SECTIONAL_AI_CALL_LIMIT` 默认 24、范围 8–40；耗尽时立即停止。
- `tools/sectional_rollout_control.py` 可查看 Action 决策；回滚需要 manifest 和确认词
  `ROLLBACK`。
- Phase 1–7 sectional 专项 `136 passed`；W0/Phase 7 聚焦回归 `40 passed`；Ruff、
  compileall、`git diff --check` 通过。
- 本机完整 pytest：`563 passed, 1 warning`；唯一 warning 为既有 Starlette/httpx
  弃用提示，无其他 warning。
- 本机 Legacy/W1b 完整兼容回归：`264 passed, 1 warning`，未排除
  `TestPrecheckGateDisplay`。MCP 兼容回归排除无法导入 Web app 的 5 项后为 `259 passed`。
- Phase 6 已推送：本地与远端 HEAD 均为
  `358929afac5d4f420b0c288f3a75ad8095762c59`，不再 `ahead 1`。
- Phase 7 已本地提交为 `123b14199ac343bb2e1490af7f6c5d466c75bb54`，尚未推送；
  当前分支相对远端 `ahead=1`。
- 外部静态审计确认两个真实跨阶段冲突并已修复：生成层 Takeaways 3–6 与 assembly 3–5、
  FAQ 3–5 与 assembly 3–4。现统一为 Takeaways 3–5、FAQ 3–4，并锁定 canonical package
  requirements。另补 `fit_level` 白名单、Markdown evidence URL 覆盖、版本一致性及
  completion token 未知值语义。Phase 1–7 sectional 加版本测试共 `143 passed`；
  Legacy/W1b 全口径（含 5 个 Web gate 测试）为 `279 passed, 1 warning`。MCP 通过临时、
  已删除的测试运行器仅在测试进程内跳过系统 MIME 文件读取，分批覆盖全部测试文件，
  最终合计 `570 passed, 1 warning`。Ruff、compileall、`git diff --check` 全部通过。
- existing-pair shadow 新增专项 `13 passed`；与 rollout/pipeline 合并 `32 passed`；
  当前 Phase 1–7 sectional 加版本测试合计 `149 passed`；Legacy/W1b 全口径为
  `264 passed, 1 warning`。Ruff、compileall 和 diff check 通过。Action #3 当前仍为
  `w1b_pre_check/in_progress`；正式 draft SHA 为
  `a09e790329469c4fc035562d4c31d1712b7c0ea9781920c76edd1c037e6621e1`，claim ledger SHA 为
  `d118d10cf88faa29c81bf19176501120c2cd53eeb542a9fc1c4b6978f8e664a6`。
- `e729eff` 已推送且本地/远端一致。第一次真实 existing-pair shadow 请求到达服务，因
  Action #3 旧 `write-brief` 的 `tier=""` 被安全拒绝；HTTP 303 redirect 为 error，未调用
  sectional pipeline，未生成 sectional 目录，正式 pair/state/DB/.env SHA 全部不变。
- 已补旧 Action 兼容：write brief tier 为空时，仅可使用与当前正式 draft SHA 完全绑定的
  w2-state `precheck_tier`；过期、冲突或未知 tier 继续 fail-closed，不写回任何 Action
  文件。真实 Action #3 只读解析为 `Cluster Content / matching_w1b_state`。
- 新增后 adapter 专项 `17 passed`，sectional 加版本 `153 passed`，Legacy/W1b
  `264 passed, 1 warning`。正式 rollout 仍默认 `off`。
- 第二次真实 existing-pair shadow 已通过空 tier 门禁，但被旧正式 draft frontmatter 中
  带 YAML 双引号的 `title` 安全拒绝：adapter 读取到的是包含字面引号的值，严格 metadata
  校验因此判定不等于 Action topic。正式 pair/state/DB/.env SHA 仍全部不变，未生成
  sectional 目录，也未重试。
- 已修复 adapter 的简单 YAML 标量读取：成对双引号使用 JSON-compatible 转义解码，成对
  单引号按 YAML 规则折叠 `''`；不成对或畸形引号在 adapter 边界直接拒绝。
- 同时补齐旧稀疏 frontmatter：`description` 可作为 summary/SEO Description 来源；只有在
  tags/SEO Keywords 缺失最低数量时才从 Action topic 派生；SEO Title/Description 采用
  确定性语义压缩满足 canonical 长度，不修改正式 draft。
- 真实 Action #3 的完整 metadata 已通过最终 validator：title 等于 topic，summary 163
  字符，3 个 tags，SEO Title 57 字符，SEO Description 158 字符且句子完整。
- 修复后 adapter 专项 `21 passed`，sectional 加版本 `157 passed`，Legacy/W1b/FAQ
  `264 passed, 1 warning`。未把 MCP 丢失结果的长时完整套件记为通过。
- `2eb008e` 已推送。第三次真实 existing-pair shadow 首次通过 tier 与 metadata 门禁并进入
  AI 正文生成；`ai_runs #134`（`legacy_write_sectional_body`）成功写入后，section parser
  因 `external_citations used candidates require a used reason_code` 安全停止。正式
  draft/claim ledger/w2-state/`.env` 与 Action 行保持不变；数据库文件 SHA 变化仅来自合法
  `ai_runs` 审计新增，不能再把“整个 DB SHA 不变”作为真实 AI shadow 的验收条件。
- 已修复 decisions 合同：placeholder 与 `used_ids` 仍须逐项一致、候选仍须 allowlisted；
  当使用事实已由服务端验证后，reason code 确定性规范化为
  `used_approved_candidate`。未使用 gate 仍必须给出非空原因，门禁未放宽。
- 新增受测运维工具 `tools/run_sectional_shadow.py`，后续真实 shadow 使用短命令
  `.venv/bin/python tools/run_sectional_shadow.py --action-id 3`，不再内嵌巨型 Python，且
  push 与 shadow 必须分成两个原子阶段。
- 本轮最终验证：sectional 加版本及 runner `167 passed`；Legacy/W1b/sentence-ID/FAQ
  `264 passed, 1 warning`；唯一 warning 仍为既有 Starlette/httpx。
- 第四次真实 existing-pair shadow 通过 reason-code 修复并恢复第一节生成，随后第二节 AI
  正文因一个段落包含多个 internal link placeholder 被严格拒绝。`ai_runs` 从 134 增至
  136；正式 pair/w2-state/`.env` 与 Action 行不变。只留下一个已验证 checkpoint：
  `checkpoints/section-901f779818.json`，无 assembly/comparison/promotion 产物。
- 已新增窄范围格式规范化：同一段的多个 ARTICLE/PRODUCT 若位于不同句子，只在原句界
  插入段落边界，保留全部可见文字、候选 ID 与 decisions；同一句内多个内链继续
  fail-closed。生成 prompt 同步声明每段最多一个内部链接。
- runner 新增显式 `--resume-existing`；仅允许系统已知的可验证中间文件继续，完整、未知或
  promotion 产物一律拒绝。真实 Action #3 只读预检确认当前 root 可恢复且只有上述 checkpoint。
- 修复后 sectional 加版本及 runner `171 passed`；Legacy/W1b/sentence-ID/FAQ 仍为
  `264 passed, 1 warning`。完整项目 pytest 的 MCP 长会话结果再次丢失，未计为通过。
- 第五次真实 resume shadow 成功恢复第一节并生成第二节有效 checkpoint
  `checkpoints/section-04981e6fc7.json`；第三节 AI 正文返回后因
  `section must contain 2-5 coherent paragraphs` 停止。`ai_runs` 从 136 增至 138；正式
  pair/w2-state/`.env` 与 Action 行不变，无 assembly/comparison/promotion 产物。
- 已把段落格式修复扩展为统一规范化：同段多内链仍只在既有句界拆分；拆分后超过 5 段时，
  只合并相邻纯文本段且每段仍最多一个内部链接；AI 只返回 1 个纯文本段时，可在既有句界
  拆成 2 段。全部可见文字、顺序、candidate ID、placeholder 和 decisions 保持不变。
  structured Markdown、同一句双内链或无法满足 2-5 段的布局继续 fail-closed。
- 句界识别已收紧：常见缩写和 initialism（例如 `e.g.`、`U.S.`）后不拆段，避免格式修复
  破坏英文句子。
- 本轮验证：generation/repair/runner `64 passed`；sectional、版本和 runner
  `173 passed`；Legacy/W1b/sentence-ID/FAQ `264 passed, 1 warning`。完整项目 pytest
  的 MCP 长会话再次丢失，未计为通过。

### 当前下一步

1. 提交并推送 2-5 段统一规范化修复。
2. MCP 核验远端同步后，单独运行
   `.venv/bin/python tools/run_sectional_shadow.py --action-id 3 --resume-existing`；不得删除
   checkpoint、重跑 W0 或启用 promotion，也不得与 push 放进同一执行块。
3. 只有 shadow 报告无 blocker，才把 Action #3 加入 `action` 白名单进行单 Action验收。
4. B303 产品页/目录修正后重新生成产品报告，确认 catalog data issues 自动清零。

## 2026-07-31 — DeepSeek V4 长正文空响应兼容修复（待真实 API 验收）

- 真实 Action #3 在 `4b3c914` 上再次验收：一次 W1b 批次内部两次
  `legacy_write_revise_body` 均为 HTTP 成功但 `message.content` 为空；正式 draft、
  claim-ledger、w2-state 和数据库业务状态未被手工修改，W2/W3 未运行。
- 根因定位到 OpenAI-compatible 适配层与 DeepSeek V4 默认 thinking 模式的交互：
  旧代码只读取 `message.content`，不读取 `reasoning_content`、`finish_reason` 或
  reasoning token 用量；复杂长正文任务可能把输出预算消耗在 thinking 阶段后没有最终正文。
- `complete_text` 新增可选 `thinking_mode`。只有官方 `api.deepseek.com` 会收到
  `{"thinking":{"type":"disabled"}}`；其他 OpenAI-compatible 服务不会被发送
  DeepSeek 专用参数。
- W0 初稿、W1b 正文修订和 W2 正文修订现均显式关闭 DeepSeek thinking；研究分析、
  claim-ledger 等其他任务未被改变。
- 新增 `AIEmptyTextError`：空正文时安全记录 `finish_reason`、completion tokens、
  reasoning tokens、reasoning 字符数和实际响应模型，但绝不保存完整
  `reasoning_content`。
- W1b 只对缺少终止原因、`stop` 但空正文、或
  `insufficient_system_resource` 允许批次内再试一次；`length`、`content_filter`
  和 `tool_calls` 立即停止，避免重复完全相同且已确定无效的请求。
- evidence/claim ledger、事实校验、原子写和 W1b/W2/W3 gate 均未放宽。

### 已完成验证

- 相关 AI/W1b/W0/W2 专项与回归测试全部通过：
  `tests/test_ai.py`、`tests/test_w1b_revision_contract.py`、
  `tests/test_w1b_mixed_fact_cleanup.py`、`tests/test_w1b_seo_title_normalization.py`、
  `tests/test_faq_schema_repair.py`，以及 W0/W2 两个定向合同测试。
- `ruff check` 对本次生产代码和专项测试通过；`tests/test_legacy_workflow.py`
  忽略两处既有 F841 后通过。本次没有新增 Ruff 问题。
- `compileall` 与 `git diff --check` 通过。
- 本机首次完整 `pytest -q` 得到 `5 failed, 399 passed`；5 项均来自
  `tests/integration/test_hermes_orchestrator_smoke.py`，共同原因不是 W0 业务逻辑，
  而是共享测试替身 `tests/legacy_workflow_helpers.py::ai_text` 仍使用旧
  `_run_ai_text` 签名，不接受新增的 `thinking_mode` 关键字。该测试替身已补齐
  `thinking_mode=None`，生产代码未为测试放宽或回退。
- `tests/legacy_workflow_helpers.py` 的 Ruff、compileall 与 `git diff --check` 已通过；
  MCP 内直接收集 Hermes integration 测试仍被既有 Landlock 系统路径读取限制阻断，
  因此修复后的完整测试需由本机环境复跑。
- 未调用真实 API，未运行真实 Web workflow，未修改 `data/`、正式 Action 文件或数据库。

### 下一步

1. 本机运行一次完整 `.venv/bin/python -m pytest -q`。
2. 全量通过后提交并推送当前补丁。
3. 在新 HEAD 上只运行一次真实 Action #3 W1b 批次；重点核对是否产出候选稿，
   若仍为空，读取新的 `finish_reason` / token 诊断后停止。W1b 通过前不得运行 W2/W3。

## 2026-07-31 — W1b 修订上下文精简（待本机完整验证）

- 真实 Action #3 在 `0307a3b` 上已验收：一次正式 W1b POST 内部按设计执行两次
  AI 修订尝试，二者均返回 `AI 返回空文本`；正式 draft、claim-ledger、w2-state、
  数据库和 Git 工作区未被手工修改，W2/W3 未运行。
- 本次修复补齐 ADR-0025 的 W1b 实现：W1b 正文修订 prompt 不再重复携带完整
  human-readable pre-check Markdown，也不再把 `write-brief` 的完整
  `research_brief_excerpt` 塞入修订上下文。
- W1b prompt 现在保留：完整结构化失败 JSON、短失败摘要、当前 draft、精简
  brief/coverage（topic/tier/intent/guidance/outline/sections）、失败相关 evidence cards、
  当前 claim ledger 已使用的 evidence cards 和受限内链表。事实门禁、claim ledger
  校验、原子写和 W1b/W2/W3 gate 未放宽。
- W1b 正文修订的可选 evidence card 上限从 64 降到 24；已被当前 claim ledger
  使用的 evidence ID 仍不会因该上限被截断。正文修订 `max_tokens` 从 16000 降到 8000，
  与 1350+ checker-visible body word 要求匹配，减少供应商空响应风险。
- 新增回归测试锁定：完整 pre-check 报告 sentinel 不进入 prompt、完整 brief excerpt
  sentinel 不进入 prompt、结构化事实句仍进入 prompt、AI 输出预算为 8000。

### 已完成验证

- `.venv/bin/python -m pytest -q tests/test_w1b_revision_contract.py::TestW1bRevisionContract::test_revision_prompt_omits_full_precheck_and_brief_excerpt`：通过。
- `.venv/bin/python -m pytest -q tests/test_w1b_revision_contract.py`：通过。
- `.venv/bin/python -m pytest -q tests/test_w1b_revision_contract.py tests/test_w1b_mixed_fact_cleanup.py tests/test_w1b_seo_title_normalization.py tests/test_faq_schema_repair.py`：通过。
- `.venv/bin/ruff check src/seo_ops/services/legacy_workflow.py tests/test_w1b_revision_contract.py`：通过。
- `.venv/bin/python -m compileall -q src/seo_ops/services/legacy_workflow.py tests/test_w1b_revision_contract.py`：通过。
- `git diff --check`：通过。
- `.venv/bin/python -m pytest -q` 在 coding-tools-mcp 沙箱内被 Landlock 阻止：
  `openpyxl -> mimetypes` 读取 `/etc/mime.types` 触发 `PermissionError`；需由本机环境
  跑全量测试，不得把该沙箱权限问题当作代码回归。

### 下一步

1. 本机运行全量 `pytest -q`，确认没有跨模块回归。
2. 全量通过后提交并推送本补丁。
3. 再只运行一次真实 Action #3 W1b 批次；若仍为空或出现供应商错误，停止并保留现场，
   不得运行 W2/W3。

## 2026-07-31 — W1b 正文 AI 空响应安全重试（待本机验证）

- 真实 Action #3 在 `legacy_write_revise_body` 调用收到 HTTP 成功但正文为空，
  状态正确记录 `AI 返回空文本`，正式 draft/claim-ledger SHA 未变化，W2/W3 未运行。
- `stage_w1b_revise` 现在只把这一种明确的空文本错误标记为 `retryable`，让现有
  两次上限批次安全使用第二次尝试；HTTP、鉴权、超时和其他供应商错误仍立即停止，
  不隐藏故障或无限增加 API 调用。
- 新增回归测试，要求空响应时不创建备份、不替换正式 draft/claim ledger，并返回
  `revised=false`、`retryable=true`。
- 当前仅完成代码审查、补丁和 `git diff --check`；本机专项/全量测试、提交和推送待执行。

### 下一步

1. 本机运行新增专项测试、W1b 合同回归、Ruff、compileall、全量 pytest。
2. 全部通过后提交并推送；随后只运行一次真实 Action #3 W1b 批次。
3. 若第二次仍为空或出现其他 AI/API 错误，保留正式产物并停止报告；不得运行 W2/W3。

## 2026-07-31 — W1b 事实清理安全加固

- W1b 的无证据事实清理现在只作用于正文：代码围栏和 FAQ JSON-LD 会先被保护，
  不会因为正文中同文句子的出现顺序而误删交付元数据或示例内容。
- 无证据事实清理进一步改为按 W1b checker 的正文分句跨度删除，并使用相同的
  claim 文本规范化匹配；列表、加粗片段和换行差异不会再让已识别的正文事实句漏删。
- 修复 W1b 修订合同中的换行转义错误；模型现在会收到真正分开的第 10、11 条要求。
- 新增正文/代码/JSON-LD 同文句，以及加粗列表事实句的回归测试。
- 目标专项测试、Ruff、compileall 和 `git diff --check` 已通过；真实 Action #3
  仍未在本次会话触发 W1b/API，正式 draft/ledger/state/database 未修改。

### 下一步

1. 将本次代码提交同步到本机环境。
2. 在当前 HEAD 上只运行一次真实 Action #3 `stage_w1b_revise_batch`；返回后审计
   新 draft、claim ledger、SHA、`precheck_passed`、事实 blocking 和备份成对性。
3. W1b 未通过前绝不运行 W2/W3；若本地服务或 AI 调用失败，保留原正式产物并报告原始错误。

## 一句话状态

源码 `0.11.5`、SQLite v13。Legacy 写作已从“长上下文一次写正文和 ledger”升级为
可审计的紧凑 hand-off：R3/W0 保存 Brief、覆盖合同和章节证据卡；W0/W1b/W2 分开
生成正文与 claim ledger。严格 evidence/claim 验证、W0 原子写和 W1b/W2/W3 gate 不变。

## 2026-07-29 — 紧凑证据写作交接（当前工作单元）

- R3 使用既有 research brief 与 evidence ledger 确定性写入 `write-brief`、
  `coverage-contract` 与 `evidence-cards` 三个 JSON；它们可由旧 action 的既有产物
  重建，不调用外部 API，也不成为新的事实源或放行条件。
- W0 正文任务只读取紧凑 hand-off；独立 ledger 任务只读取最终正文与证据卡，并用
  原有严格 parser、服务端 SHA 与原子写协议处理。旧的“单条 prompt 里要求分隔符”
  方案被该更稳定的两任务协议取代。
- W1b/W2 修订只带失败项相关卡和当前 ledger 已使用的卡，完整 pack 继续由既有
  预检/事实校验读取；required evidence 不会被卡片选择静默过滤。
- 架构决定：`docs/decisions/0025-compact-evidence-bound-writing-handoff.md`。

### 下一步

1. 导入本次完整更新包后，使用 `PYTHONPATH=. .venv/bin/python -m seo_ops` 启动，
   从 action #3 重新运行 W0；不手改 draft、ledger、数据库或状态文件。
2. 对同一真实主题完成 R3→W3 验收，记录覆盖合同各节、事实 ledger、W1b/W2/W3
   结果，并与旧长上下文文章比较质量；未完成前不得宣称实际质量已完全证明。
3. 若 ledger 任务仍失败，保留真实正文、错误和 evidence cards 后停止；不得放松
   parser、伪造 ledger 或绕过 gate。

## 2026-07-29 — Hermes 第一段真实自动化（当前工作单元）

- 新增 `POST /api/hermes/runs`：Hermes 只需提交 `site`、`topic` 和可选
  `requirements`；服务创建/恢复一个持久 action，按 R0 → 自动外部搜索 → R1
  顺序运行。
- 新增 `GET /api/hermes/sites`、`GET /api/hermes/runs/{action_id}` 与
  `/prompt`，让 Hermes 能汇报站点、阶段、下一步和完整 R0 搜索提示词。
- 新系统已配置的 SerpAPI/Tavily 结果会保存为不可变外部快照，并转换成
  Legacy R1 可消费的搜索结果文本；没有可用供应商时停在 `r0_prompt`，
  返回“是否人工粘贴外部结果”的明确决定点，不伪造资料。
- Hermes skill 新增 `scripts/start.sh` 与最小苏格拉底式 intake 规则；
  W0/W1b/W2 evidence/claim ledger 与 gate 未改动。
- 新增 3 个回归测试；Legacy/HTTP 集成回归和全量测试 `304 passed, 1 warning`。

### 下一步

1. Hermes 已具备 R0→R1→R3→W0→W1b→W2→W3 总控制器：有可用搜索提供商时
   一次 `start.sh` 即继续所有通过 gate 的阶段；W1b/W2 失败只自动跑一批最多两轮
   修订，仍失败则持久化暂停并报告，下一次明确继续才开新批次。
2. 无搜索凭据时，`/api/hermes/runs/{id}/materials` 和 `continue.sh` 实现正式确认点：
   先汇报同步资料与 R0 提示词，运营者选择“使用已有资料”或提交人工搜索结果；
   不伪造外部资料，后续 evidence/claim 与 W1b/W2/W3 gate 未改动。
3. 网页在每个已开始的 Legacy 阶段显示警戒色 `R0 重新开始此任务`；原红色
   “接受当前蚕食问题并继续”仍只在至少一批 W2 后、评分通过、检查器正常且仅蚕食
   阻塞时可用，不能放行事实、评分或检查器问题。
4. 下一步只剩本地真实主题端到端验收与旧/新文章质量对比；不要继续放松或扩展
   evidence/claim ledger、W0 原子写、W1b/W2 事实校验或 W3 gate。

## 2026-07-29 — 证据驱动事实校验最终验收追加修复 (0.11.5)

- **W0 原子写异常路径保护**：`stage_w0_validate_and_draft` 先调用 `_write_ahead_draft_and_ledger`（失败时内部 rollback），成功后再清理 `w1b` 预检/后处理报告并重置 w2-state；`_write_ahead_draft_and_ledger` 异常被捕获转为 `success=False` 与明确错误信息，绝不向页面抛未处理异常。
- 完整 `pytest -q` 301 passed（新增 1 个测试），`ruff check` 5 个 pre-existing F841 无新增。

## 2026-07-29 — 证据驱动事实校验最终验收修复 (0.11.5)

- **根 JSON 类型校验分离**：`evidence-ledger` / `claim-ledger` 在 `json.loads` 后先单独判断 `isinstance(..., dict)`；`list` / `null` / `str` / `int` 等根类型产生结构化 blocking 后 `return`，错误分支绝不调用 `.get()`。
- **W0 失败不删旧产物**：`stage_w0_validate_and_draft` 先验证候选 draft + claim-ledger，通过后才清空旧产物并原子写入新版本；验证失败时旧 draft / claim-ledger / w2-state 字节级不变。
- 完整 `pytest -q` 300 passed（新增 5 个测试），`ruff check` 5 个 pre-existing F841 无新增。

## 2026-07-28 — 证据驱动事实校验第三轮修订 (0.11.4)

- **evidence-ledger 预校验 fail-closed**：构建 evidence index 时即校验每条 evidence 的 `source_url` / `quote` / `key_finding`：`source_url` 必须 str 且非空；`quote` / `key_finding` 必须 str（None 允许）；二者至少一项非空。任一非法即产生结构化 blocking，即使该 evidence 未被任何 claim 引用。
- 完整 `pytest -q` 285 passed（新增 3 个测试），`ruff check` 6 个 pre-existing F841 无新增。

## 2026-07-28 — 证据驱动事实校验第二轮修订 (0.11.3)

- **原子写回滚处理旧文件缺失**：`_write_ahead_draft_and_ledger` 增加 `draft_existed` / `cl_existed` 与 `draft_replaced` / `cl_replaced` 标志；rollback 区分「旧存在 → restore」「旧不存在 → unlink 新文件」「未 replace → 不动」。杜绝新 draft 单独泄漏。
- **`_run_fact_check` 字段类型校验前置**：所有字段（evidence_id、source_url、quote、key_finding、claim_text、claim_type、material_pack_sha256、draft_sha256、evidence_ids 每项）在 strip/slice 前先 `isinstance(..., str)`；非 str 产生结构化 blocking（含字段名 + 实际类型）。
- 完整 `pytest -q` 282 passed（新增 8 个测试：1 个原子写 + 7 个字段类型），`ruff check` 6 个 pre-existing F841 无新增。

## 2026-07-28 — 证据驱动事实校验复测修订 (0.11.2)

- W2 revise 接入证据闭环：prompt 含 Evidence References、输出必须含 `===CLAIM_LEDGER===`、复用 W0 解析/验证、服务端注入 `draft_sha256`、原子写 draft + claim ledger。
- 真正原子写：`_write_ahead_draft_and_ledger` 改为「snapshot → temp → fsync → replace」协议，任一步失败 restore 旧内容。
- W0/W1b 句子提取统一：`_validate_claim_ledger_json` 段落 + 句子二级拆分，与 W1b `_claim_in_draft` 一致。
- fact check schema fail-closed：`_run_fact_check` 显式校验每项为 dict，非 dict 产生结构化 blocking 项而非 AttributeError。
- 通过后拒绝 revise：`stage_w2_revise` 在 `gate_passed=True` 或 `applied=True` 时直接返回失败。
- 完整 `pytest -q` 275 passed（新增 14 个测试），`ruff check` 6 个 pre-existing F841，无新增。

## 2026-07-28 — LEGACY_WS 动态化 + sync_all 多站点修复

- `app.py`：`LEGACY_WS` 从 `<PROJECT_ROOT>/data/legacy_workflow/laserpointerhub` 改为 `active_settings.data_dir / "legacy_workflow" / "laserpointerhub"`，按运行时 settings 自动推导。
- `legacy_sync.py`：所有 `_gen_*` 函数 SQL 从 `site_id = 1` 改为参数化 `site_id = ?`；`sync_all()` 新增 `settings` 和 `site_id` 参数。
- `legacy_r0` 路由：改为 `legacy_sync_all(settings=active_settings, site_id=action["site_id"])`。
- 新增 3 个 `sync_all(settings=, site_id=)` 回归测试；集成测试适配动态 LEGACY_WS。
- 当前验证：完整 `pytest -q` 185 passed，`ruff check` 通过，生产工作区测试后 clean。

## 2026-07-28 — Legacy action/attempt 产物隔离

- R0 为当前 action 创建新的 attempt 与 run ID；Research、素材包、草稿、报告和 W2 状态只存在于该 attempt。网页后续阶段只解析与 action 和原始主题同时匹配的 current manifest。
- 共享 context/published/products 在 R0 前由数据库同步，运行目录只通过同构链接读取；旧 `/home/laoma/seo-workflow` 仍不写入。
- `legacy_workflow`、`research_collector`、`research_scorer` 和 `write_collector` 统一使用 `seo_common.slugify`；纯非拉丁主题改用稳定 SHA-256 摘要。
- 报告文件加入 UTC 微秒和随机后缀；collect 重试必须生成本次 Markdown + JSON，失败时删除当前 attempt 的不完整派生文件。
- published 同步删除不再 active 的文章 Markdown；修复 `cannibalization_checker.SITES_DIR` 缺失，检查器能实际读取当前同步快照。
- 架构决定见 `docs/decisions/0012-legacy-action-attempt-workspaces.md`。
- 当前验证：完整 `pytest -q` 172 passed，1 条既有 Starlette/httpx 弃用警告；compileall、F821、相关 Ruff 与 diff 检查通过。

## 2026-07-27 — Legacy 第一批规则对齐与安全门

- Research 严格执行“确定性 scorer → AI 解释分数”；scorer 使用本次明确的 JSON 输入和独立临时输出，失败时停止，不能复用旧同 slug 评分。
- W1b 改读结构化 JSON 结果；脚本崩溃、非法输出与正常业务失败分开处理，不再通过统计 `❌` 字符判断。
- W2 只接受与当前草稿 SHA-256 一致且全部通过的预检；AI 修订后重新预检。检查模式使用临时草稿，只有全部硬门通过并显式 `--apply` 才修改真实 draft。
- 蚕食检查器或质量评分器失败时 fail-closed。`--force` 只允许在两轮修订用满、仍为蚕食阻塞、评分合格且人工最终写回时使用。
- W3 注册要求 W2 已通过、已 apply，且当前草稿 SHA-256 与通过时完全一致；直接构造请求不能越级注册。
- 当前验证：完整 `pytest -q` 165 passed，1 条既有 Starlette/httpx 弃用警告。

## 正在实施：旧 Research + Write 1:1 复原 — 替换新文章制作通道

### 已冻结资产

- Python 3.12.3，旧项目 Git HEAD `c12d236`（workspace dirty：6 个 context 文件修改自 HEAD）
- 两个 Skill：`research/SKILL.md` (a2e9416b...) | `write/SKILL.md` (bbaccea1...)
- 九个活动模块全部隔离导入 OK：seo_common, seo_config, content_scrubber, content_scorer, plan_feedback, research_collector, research_scorer, write_collector, write_pre_check
- 四个 CLI --help 全部 exit 0
- 旧资产清单 + SHA-256 + 依赖闭包见修订版第一项交付（会话中提交）
- 三类历史结构样例：light-painting（Cluster）、power-testing（Pillar）、best-astronomy（Product Roundup）
- 最终勘误附录已提交

### 运营者确认的关键决定

1. **新文章制作全切 Legacy**，旧文章更新不变
2. **作者**：自动填 `LaserPointerHub`（组织名），不阻塞流程
3. **旧项目**：`/home/laoma/seo-workflow` 绝对只读，不修改
4. **数据同步**：启动 Legacy 前从数据库刷新 5 个文件（文章索引/正文/产品报告/内链地图/SEO 数据手册）
5. **素材库**：三个文件（痛点/案例/外链）从旧快照做种子，由旧 archive 脚本自己累积维护。先文件模式，以后评估是否迁移数据库
6. **搜索提示词**：恢复旧 8 段格式，Section 3 "市场数据"替换为"常见误区与真实教训"
7. **实时日志**：SSE 推送脚本 stdout/stderr
8. **Plan → Research 链**：无旧 Plan 时用 heuristic topic-context，后续可从新系统数据 enrich

### 未被修改的内容

- `content_production.py`、`material_workflow.py` — 仍由旧文章更新使用
- 主题调研、文章建议、主题图谱、数据导入页 — 不动
- 设置页 AI 上限配置 — 保留给旧文章通道
- `/home/laoma/seo-workflow/` — 绝不写入

### 实施计划

保存于 `docs/superpowers/plans/2026-07-21-legacy-research-write-restoration.md`。

任务顺序：
1. 数据同步层 (`legacy_sync.py`) — DB → filesystem bridge
2. Legacy 工作流服务 (`legacy_workflow.py`) — 阶段管理 + 脚本包装 + AI 集成 + 搜索提示词
3. Legacy 工作区建立 — 静态文件复制 + 素材库种子 + 数据库同步初运行
4. 数据库迁移 MIGRATION_13 — `actions.legacy_stage` 列
5. 网页集成 — 路由 + SSE + 模板 + CSS
6. 测试
7. 日志更新

### 跳过旧文章制作通道的完整交接方案

详见 `docs/LEGACY_RESEARCH_WRITE_RESTORATION_HANDOFF.md`，运营者确认可跳过对旧文章通道的改动。

## 独立后续建议：Research + Write V2 SEO 与 AI 搜索优化

- 独立建议见 docs/RESEARCH_WRITE_V2_SEO_AI_SEARCH_OPTIMIZATION_PROPOSAL.md。
- 该文件只是复原完成后的可选 V2 方案，未实施、未批准为现行规则，也不修改或取代旧 Research + Write 复原方案。
- 顺序必须是先完成 Legacy 1:1 复原与验收，再另行决定是否实施 V2；Legacy mode 必须始终保留并可独立回放。
- V2 建议重点为 claim 级证据、真实信息增量、页面职责去重、关系型链接装配、AI 可提取答案结构，以及 Google/Bing/ChatGPT 分平台效果观察。
- V2 若获批准，必须使用独立产物目录和规则版本；同一文章只能在人工选定最终版本后执行一次 register。

## 0.10.9 接手状态（开放入口与主意图聚类已完整接入）

- `multi_source_topic_research 0.15.0`、`topic-hypothesis 0.13.0`：12 路开放入口恢复为成功原型的原始全行业查询，不再把自动选中的“户外”“天文”“散热”等分支词拼进每一路。所选分支只保留给旧 Plan 的角色 × 任务 × 条件卡和运行审计。
- 种子没有 24/17/6 等目标或业务上限；这些数字都是历史实跑结果。所有来源支持、手持激光笔锚定且未被 CMS 重复门拦截的种子继续显示；选择 5 个深挖只是供应商额度分配。程序保险丝提高到 200 个候选、500 条来源观察，本次实测均未触发候选保险丝。
- 新增最终主意图聚类：AI 只能用输入候选 ID 建议同意图分组或上一轮重复，程序合并 evidence/facts 后再执行原有 CMS 资格判定。不同任务、症状、决策、受众、地区、条件与结果必须保留；AI 不能新增主题、改资格、改分或自动发布。
- 两轮正常真实测试均使用生产 SQLite 副本，不指定主题、不模拟上一轮。第一轮自然选择 `use-outdoor`，58 条观察得到 15 个有效前种子、14 个 CMS 去重后可见种子和 13 条聚类前成型主题；第二轮自然轮到 `use-astronomy`，AI 原始返回 48 个、程序验证保留 45 个、CMS 去重后可见 37 个，并形成 37 条聚类前主题。
- 两轮搜索实际共用 SerpAPI 4 次，账户 52→48；生产库 SHA-256 前后均为 `8181b15b5f5cd1c0747acebf3b3323a9004c471a215ac08b7cfabec77a403c1f`。Firecrawl 两轮分别失败 1/2 页，因此状态诚实记录为 `partial`，其余 SerpAPI/Tavily/AI 调用成功。
- 在上述两轮真实候选上另做两次 AI-only 最终代码路径复验，不再调用搜索 API：第一轮合并 3 个本轮换说法；第二轮合并 10 个本轮换说法并识别 14 个上一轮同意图变体。扣除原本已路由旧文的候选后，仍约有 10 个与 14 个独立成型方向进入最终资格检查。
- 调研页只显示最近一轮：结果区只用可折叠的实际对象账汇报来源观察、种子、CMS 重复预筛/匹配文章、深挖、主意图核对、候选路由和供应商异常；不再显示没有本轮数据的固定流程说明。旧运行仍保留在数据库审计中，但因未保存对象明细会明确标记“历史明细缺失”，不再以“查看以前的调研概况”显示。覆盖较少方向的入口显示为“突破主题瓶颈”，已有方向加维度的入口显示为“补主题缺口”；仅交换文案，未交换实际调度路径。
- 文章建议页只显示最终可选择的两栏结果：所有 pending 的旧文/成型新主题均列出，前两项直接展开，其余逐张折叠；右栏当前显示“累计待选 23（本轮新增 13）”。原始 PAA/相关搜索/资料发现线索不是被丢弃或留给人工审核，而是已保存进 `research_seed_observations`，供下一轮自动续用。`update_existing` 自动进入左栏；正文已覆盖的重复只保留审计。旧 `needs_evidence` / `needs_human_review` 打开页面时自动重判为这三种明确去向，不能再形成看不见的待办。

### 下一步

1. 本地 `seo-ops` 常驻服务已在 `8788` 重启并加载 0.10.9；下一次只需从主题调研页正常点击，无需手选电池或散热。
2. 在文章建议页用折叠标题浏览两栏最终主题池并人工挑选品牌适配方向；raw lead 不需要人工处理，会在后续调研中自动续用。
3. 不要再把 5 个深挖种子、12 类入口或历史 24/17/6 输出解释为主题上限。若未来聚类误合并不同意图，先调聚类提示与回归样例，不能重新把分支词塞回全部入口。

## 0.10.8 接手状态（来源观察续池与单轮冷却已落地）

- `multi_source_topic_research 0.13.0`、`topic-hypothesis 0.11.0`：每轮混合 SERP/PAA/相关搜索、商业/竞争候选页、论坛/社区、评价、社媒/视频和官方/参考页的公开语言；实际 URL 决定来源类别。下一轮优先消费未使用的来源观察，旧 Plan 任务卡和问题链继续补足。
- SQLite v12 新增 `research_seed_observations`，保存 URL、摘录、evidence ID、后续查询、任务卡字段、语义簇、核心对象诊断和已消费运行。公开第三方语言只是市场代理，绝不是本站第一方反馈、需求事实或可直接成稿证明。
- 冷却只读取立即上一条已完成运行的主攻簇，并仅影响下一轮排序一次；它不是任何主题的永久禁词。核心来源优先于相邻/偏移来源；生成查询带有 `laser pointer` 不能把 LightBurn、激光瞄具、切割/雕刻页面升级为核心。
- 候选池增加保守的“对象 + 动作 + 场景”强重复去重；不同主要任务仍保留。无直接页面支持的推荐/型号/标题材料进入 `raw_lead`，不能直接执行。文章建议页按折叠标题展示详情。
- 最终隔离真实复测：模拟前一轮 `battery_charging` 后连续两轮均为 `success`；每轮记录 SerpAPI 3、Tavily 8、Firecrawl 4、AI 1。第一轮保存 24 条观察并得到树艺师指示修枝位置；第二轮实际续用 3 条观察，得到“户外光束看似突然停止”和“天线/机械对准”两个不同成型方向。生产数据库 SHA-256 前后均为 `ec2cdd03c85918dd1b014c3446c011d148533b00a2ef2d34357209f50f45b0e6`。

### 下一步

1. 重启本地 `seo-ops` 服务后，再从主题调研页发起一轮正常运营调研；打开页面本身不会消耗额度。
2. 在文章建议页展开候选，人工确认品牌方向、写作价值和最终资料；`raw_lead` 只能用于继续调研，不能直接制作。
3. 不要承诺每轮都有无限新文章。若连续运行只产生已覆盖内容或低质量 raw lead，应如实报告，而不是降低 CMS 重复判断或制造主题。

## 0.10.7 接手状态（历史：静态卡接入；非完整续池方案）

### 2026-07-18 后续审计结论

- 早先“三轮最小预算成功”只证明了三张预先存在的卡可被下游调研消费，不能证明种子会自动、稳定地补充；不得把它当作完整流程验收。
- 对旧痛点库 156 条公开线索的语义簇原型机械保留了 7 张“未重复”卡，但与当前 CMS 正文逐篇复核时，收纳、充电器指示灯、望远镜支架和 TV 屏幕等至少四张已被现有文章直接覆盖；这暴露了当前词面型 CMS 去重无法可靠判断主意图。
- 一个不依赖旧痛点库的实时对照（生产 SQLite 副本）实际调用了 SerpAPI、Tavily、Firecrawl 和 AI 各一次：前两者成功、Reddit 页面被 Firecrawl 拒绝。AI 从 14 个实时结果抽出 10 张卡，但其中出现了来源未支持的“建筑师/教师/天文”等角色；即使程序预筛留下若干卡，也不能证明来源忠实或主题独立。
- 结论：不要为凑满五轮继续调用，也不要继续增加静态卡。下一步必须先在隔离环境验证“带原文片段和 URL 的来源观察 → 来源忠实卡 → 正文主意图比对 → 多样化选择”这一闭环；若达不到五轮均有独立主题，就不落地。

- `multi_source_topic_research 0.12.0`、`topic-hypothesis 0.10.2`：内置版本化任务卡，按来源多样性一次选择至多 3 个种子；若只剩 1–2 张卡，则用旧 Plan 的两条独立问题链补足剩余探索预算。自动主题缺口入口优先仍有未尝试卡的分支/维度。卡先仅做 CMS 同主意图/正文覆盖预筛，需求、范围、材料与弱信号均不阻断。
- 任务卡的 `role`、`task`、`condition`、`source_type`、`source_basis` 和 CMS 预筛结果保存到 `research_runs.filters_json`，并传给 AI。提示词把卡明确为计划假设，要求无法忠实支持时留空而不是编造新用途或风险。
- `candidate_qualification 1.1.3` 仍只有 `same_intent` / `covered_subtopic` 硬阻断。候选若明确以激光水平仪、旋转激光、切割机等替代手持激光笔，仅显示软性优先级诊断，不改变资格。
- 隔离真实验证：10 个单卡对照中 8 个产生至少一个非重复成型主题；3 个多卡稳定性批次均至少产生 1 个，自动种子池的 3 个批次分别产生 3、2、3 个；任务忠实度批次产生建筑检查、镜头/TV 画面表现和连续对准三项，且不再把画面现象夸大为相机损坏或把需求夸大为改装方案。每次生产库 SHA-256 不变；有少数 Firecrawl 页面采集失败，均如实记录。
- 正式接入后的再验：三轮 `1/1/1/1`（SerpAPI/Tavily/Firecrawl/AI）均为 `success`，分别把树艺师指示修枝位置、Laser 303 模式跳变/变暗诊断、相机/TV 中绿点过亮或 bloom 打包为合格的独立新主题；三轮的任务卡与 CMS 预筛均写入运行审计，生产库 SHA-256 前后为同一值。
- 本地服务已重启至 `0.10.7`：`GET /api/health`、`/research`、`/opportunities` 均返回 HTTP 200（端口 `8788`）。

### 下一步

1. 不要把当前静态卡当作已完成的种子续池，也不要为维持每轮数量绕过重复判断或制造主题。
2. 在任何新实现前，先做隔离的来源观察原型：每张候选必须保存当前 URL、采集时间、原文片段以及角色/任务/条件与片段的可核对对应关系；公开第三方语言只能是市场代理，不能写成本站用户反馈。
3. 重新验证时用实际 CMS 标题、H2 和正文核对“对象 × 任务/结果”，角色不同但主要任务相同仍应回流旧文；只有五轮均经人工语义复核为独立且来源忠实时，才允许落地代码。

## 0.10.6 接手状态

- `multi_source_topic_research 0.11.2` 融合旧 Plan：确定性选择站内覆盖/边界前沿，使用问题链生成检索种子，再由 SERP、资料发现和页面采集扩展，最后让 AI 只包装 1–4 个有信息增益的角度。第二条种子明确标为 `public_third_party_language_probe`，它代表其他站点、论坛、评论或问答的公开市场代理，不是本站第一方用户语言。
- `candidate_qualification 1.1.2` 只把 `same_intent` 和 `covered_subtopic` 作为硬阻断。范围、语言、占位 intent、弱需求、少材料、关系不确定和跨分支都保留为诊断；安全/法规主题仍须在制作前补官方或研究来源。
- 种子锚点只约束核心产品对象，不要求候选复述分支词。激光笔仍是核心工具时，树艺师指枝、建筑检查、工业对准等细化任务正常排序；只有核心对象被激光水平仪、切割机等替代时才软降权。
- 文章建议页把 AI 成型角度与 PAA/Related/Tavily 原始搜索线索分开。原始线索继续可见、可作为下一轮种子，但不能直接“开始执行”；旧 Plan 的接受/拒绝记忆只做软排序，不做隐藏门。
- 三轮真实外部调研共完成 SerpAPI 6/6、Tavily 9/9、AI 3/3；Firecrawl 产生 5 次成功页面采集、2 次失败事件和 1 次复用。10 个 AI 成型角度中 7 个通过非重复判定、3 个因当前 CMS 已覆盖而回流旧文。最终规则在保存证据上本地重放后，树艺师角度优先级由误罚时的 71.6 恢复为 91.6。
- 后续四轮 `1/1/1/1` 隔离压力验收（每轮各 1 次 SerpAPI、Tavily、Firecrawl、AI）均成功：29 条候选中有 7 个 AI 成型角度、5 个非重复、2 个回流旧文、22 条原始线索。树艺师角度再次出现，但附件分支也产生了手枪激光瞄具和 CNC 安装等非重复却明显偏离本站手持激光笔意图的结果；当前方法能发现新意图，但不应把“非重复”误读成“品牌适配”。

### 下一步

1. 运营者可先从树艺师指枝、卖家虚标验证、建筑检查等角度挑选真正符合品牌方向的一项；弱信号允许进入选择，但制作前仍需补足可追溯材料。
2. 若要再次完整联网复验 `0.11.2`，须明确同意把生产副本中的站点标题、小标题和反馈上下文发送给已配置的 SerpAPI、Tavily、Firecrawl 与 AI；本轮执行环境拒绝了未单独披露这一数据外发风险的再次启动，未绕过策略。

## 0.10.5 接手状态

- 调研资格已由 `candidate_qualification-1.0.0` 接管：硬门只保留英语/范围、明确主要用户任务、同主意图与正文已覆盖子题；`adjacent` 和 `distinct` 方向即使尚缺需求或页面材料，也可作为新文章建议出现。需求、来源和页面材料仍保存为写作准备度，制作前不得绕过素材确认。
- 无结构化假设的主题分支采用旧 plan 的多视角探索种子，按边界维度轮换不同检索角度；它们只驱动 SERP 发现，必须由具体外部问题形成候选，不能把标签拼成标题。

### 下一步

1. 在 `/research` 实跑一个未登记自然假设的分支，检查多视角查询是否带来可读的相邻细化方向。
2. 从新建议中选一项主意图独立的方向，写作前按素材确认流程补齐来源并完成受控成稿复验。

## 已确认的业务目标

- 用户是单人网站运营者。
- 可执行权限主要是发布、修改、删除文章和产品页面，以及自行决定文章主题。
- 第一方输入是 GSC 只读 OAuth 同步，以及运营者手工导入的博客 JSON、产品 JSON；GSC Excel 只作后备。
- 系统输出不是“尽量多写文章”，而是每天/每周最值得做的少量动作及其证据。
- 人工保留发布、删除及风险内容的最终决定权。
- 第一阶段联合内部数据、外部调研和主题图谱；信号不足时允许继续外部调研、从图谱指定方向或等待新信号。
- 第二阶段的目标是直接交付新文章或旧文章修改稿及 CMS 字段，不把 evidence ID 和通用检查清单变成运营者的日常工作。

## 当前实现范围

- [x] 五步日常导航：数据导入 → 主题调研 → 文章建议 → 文章制作 → 主题图谱；设置和方法下沉到侧栏底部。
- [x] FastAPI + Jinja + SQLite 本地网页与 SQLite v1→v12 增量迁移。
- [x] GSC 只读 OAuth 一键同步；Blog JSON、Product JSON 继续手工幂等导入；GSC Excel 保留后备；错误批次可逐条物理删除。
- [x] 2026-06-22 可信起始日硬边界、final 数据、当前/上一窗口和 date + query + page 联合指标。
- [x] 主题树、65 篇博客与 15 个产品的主要主题/辅助知识映射，以及历史决定记忆。
- [x] GSC、主题缺口、七维边界扩展三个独立调研入口；24 小时复用和 10/10/20/20 硬预算。
- [x] SerpAPI、Firecrawl、Tavily、DeepSeek Flash 的显式调用、脱敏快照、失败隔离与精确用量。
- [x] 最多 2 篇旧文章 + 2 篇新文章的推荐组合；不足不补位，产品页不占旧文章席位。
- [x] 同主意图与正文覆盖的防重复门、旧文归类和失效重判；泛 FAQ、范围、需求、缺口与材料仅作诊断/准备度，历史接受或拒绝只作软排序。
- [x] 调研候选读者可见语义字段统一自然英文，并在写入前检查当前 CMS、历史候选和本轮候选。
- [x] 旧文章元数据/局部/同主题重写与新文章制作；旧 Slug 锁定，旧文采用草稿 + 独立编辑审查，保存前执行确定性质量闸门。
- [x] 新旧文章写作前 A、C–H 素材清单、关键材料门槛、手工搜索提示词、粘贴快照与明确确认；市场与价格材料不进入写作包。
- [x] 新文章正常 3 次 AI、失败时受控修订；每篇调用上限可在设置页配置为 3–10，默认 4，与外部调研预算分开。
- [x] 日常页面隐藏 evidence ID、技术分数和六步清单，保留折叠诊断供审计。
- [ ] 发布后 7/28/56 天自动提醒、全文级声明—来源逐条校验仍待增强。
- [ ] Windows 浏览器沙箱仍阻止本轮 390px 截图式视觉复验；HTTP、模板和响应式规则由自动测试覆盖。

## 0.10.4 接手状态

- 已加入 ADR-0017：除学校/课堂和未成年人以外不再设置主题范围黑名单；英语、清晰主意图、
  真实需求、未覆盖缺口和可信材料仍是不可绕过的质量门。无结构化假设的分支只能采集发现线索，
  不能将“分支标签 + 占位词”写成候选标题。
- 两轮隔离 SerpAPI 对照（8 个产品决策检索）证实：充电、光束发散、天文颜色、变暗故障、
  21700/18650、USB/可更换电池、固定/可调焦都有需求或 PAA，但与现有电池、充电、光学、
  寿命、天文文章同意图或已含子题，应路由为旧文更新或产品内链。随后回放当前 CMS 正文确认
  铜/铝热管理也已被多篇文章直接比较，撤回该假设，不再为它补证据或新建 URL。
- 文章制作不再只把新文章送入素材确认。旧文章必须同时具备当前 query + page 联合数据、确认的
  A/E/G 素材和至少两个可追溯来源；旧 Slug/主要意图仍锁定。新旧稿均禁止市场、价格、零售商、
  折扣、排行榜、Quick Specs 和 CTA 填充。
- 当前生产候选为历史审计结果，不因本次范围规则改动自动生成新 URL 或覆盖原始导入/CMS。
  后续重新资格判定会使用 `candidate_qualification-0.9.6`。

### 下一步

1. 将 21700/18650、USB/可更换电池、固定/可调焦等已验证需求排入旧文更新和对应产品内链，
   而不是重新测试为新 URL。
2. 用一篇有当前 query + page 数据的旧文和一篇已确认材料的新文章实际生成稿，人工核对事实、
   链接、英文质量和 CMS 粘贴效果；没有素材则停在任务卡，不得让 AI 补写。
3. 后续每轮按“产品能力 × 用户决策 × SERP/PAA × 现有正文”选种子；若持续没有独立缺口，
   输出产品/市场扩展机会或需求天花板判断，不降低资格门。

## 已核实的真实输入与当前状态

旧项目只读路径：`/home/laoma/seo-workflow/laserpointerhub/raw`

- 真实数据库 schema 11；CMS #3/#4、手工 GSC #5 和 OAuth API GSC #6 仍完整保留。意外测试导入 #7/#8/#9 及快照也保留审计，但其 3 项测试内容已设为非活动，GSC #9 不参与当前分析。
- API 导入 #6 是唯一活动 GSC 批次，属性为 `https://laserpointerhub.com/`，实际日期 2026-06-22 至 2026-07-14，含 542 条汇总行和 1,188 条 query + page 联合行。
- 手工 GSC #5 保留为非活动可信历史窗口，共 659 条指标，但没有联合行，不会替代 API 联合证据。
- OAuth 客户端与 token 只在 `.secrets/google/` 本地受限文件中；SQLite、Git、HTML 和快照不保存凭据。
- OAuth API GSC #6 已恢复为唯一活动批次；当前内部分析显示 16 个旧文候选，其中只突出 2 个优先项。
- 真实调研 RUN #1 为“摄影与光绘”，状态 partial：SerpAPI 3/4 成功、Firecrawl 2/3、Tavily 5/5、AI 1/1；调研概况和失败说明仍完整显示。
- 旧 RUN #1 结果已因新资格规则/CMS 指纹迁移为失效。RUN #5 的收纳线索归入现有指南；RUN #6 暴露查询长度错误；RUN #8 发现替代工具但材料不匹配；RUN #9 定位分支误删；RUN #10 在严格重判后保留 1 个合格新主题 `Why are laser pointers not allowed in school?`。
- 当前活动内容为 Blog 65、Product 15；主题图谱保留 80 项真实内容映射，产品 canonical 全部为 SKU 路由。
- 本轮另有备份 `data/backups/seo_ops-before-e2e-repair-20260717T045608Z.db` 和 `data/backups/seo_ops-before-candidate-filter-20260717T053729Z.db`。
- 当前研究规则为 `multi_source_topic_research 0.15.0`、`candidate_qualification 1.1.3`、`topic-hypothesis 0.13.0` 和 `new_article_candidate 0.4.1`；GSC/旧文质量规则仍为 0.9.0/0.8.0。
- 主题图谱只显示真实导入内容；调研候选与制作任务不会提前创建覆盖节点。

## 外部来源的准确状态

- 新主题调研可独立从 GSC 信号、主题图谱缺口或七维边界扩展启动，不再存在 GSC-only 死循环。
- GSC API 使用唯一只读 scope、PKCE、本机 loopback 回调和 `dataState=final`；客户端与 token 只保存在本地受限文件。
- 每次同步最多取当前 28 天及边界允许时的完整上一 28 天；任何请求都不得早于 2026-06-22。
- 每轮可设置范围和硬上限均为 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20；预算是上限，满足停止条件时不强行用完。
- SerpAPI 只提供时点 SERP/Trends，Tavily 只发现来源，Firecrawl 只采集选定公开页，AI 只整理本轮存证材料。
- 24 小时同口径复用不扣真实请求，也不增加证据独立性。实际请求、成功、失败和复用分别显示。
- AI 与内容制作优先官方、标准、研究和命名行业一手来源；社区/论坛只作问题和体验线索，不能单独证明法规、安全或规格。
- 更多 API 只扩大覆盖，不自动提高需求置信度；外部结果不修改 GSC 事实、原机会分数或查询—页面归属。
- 所有外部和 AI 调用都由运营者点击触发，不会在重新分析、打开页面或同步主题树时静默消费额度。

## 关键方法边界

- GSC 是核心第一方证据，但不是无误差真相；聚合、顶部行限制、不完整数据和官方日志异常必须进入门槛。
- “Google 沙盒”不是自动诊断。先查官方数据异常、排名更新、技术问题、季节性和 SERP 变化。
- SerpAPI 证明某时某口径的 SERP；Trends 证明相对兴趣；Firecrawl 证明页面公开内容；Tavily 只发现资料。
- 多个工具重复同一网页或摘要，不增加独立置信度。
- 普通 GSC 查询表与网页表不是联合维度；OAuth 同步的 query + page 行才是页面归属证据，但仍受匿名查询和顶部行限制影响。
- 没有订单/GA4 数据时，不输出预期收入或查询级转化。
- AI 不修改指标、规则、资格门槛或分数，不自动发布。

## 怎样运行

```bash
cd /home/laoma/seo-ops-system
source .venv/bin/activate
seo-ops
```

默认服务地址为 `http://127.0.0.1:8788`；当前源码为 `0.10.6`，本轮未重启或替换用户的持久服务进程。文章建议页已由模板测试验证成型角度/原始线索分栏；主题图谱仍有 15 个 `/p-{SKU}.html` 产品链接且无 `/products/` 残留。

外部调研预算可直接在 `/research` 调整；文章 AI 上限可直接在 `/actions` 调整；完整连接参数仍在 `/settings`。只有点击调研按钮或文章制作按钮才会产生真实调用，两种预算互不占用。

## 下一位接手者的第一步

1. 不需要重新授权或为了数量重跑调研；先在第 3 页核对 2 个优先旧文、1 个合格新文，以及待补证据/旧文归类是否分栏正确。
2. 可用当前合格的学校禁用原因主题复验第一篇新文章成稿；同时选择一篇有查询—页面联合证据的旧文复验旧文章成稿。
3. Blog JSON 和 Product JSON 仍只在 CMS 内容变化后手工导入；不要改成自动同步。
4. 需要新数据时再点第 1 页 GSC 一键同步；硬边界仍不得早于 2026-06-22。
5. 后续调研可指定分支；系统会优先覆盖多个前沿、保存精确 lineage，并拦截偏离站点对象及被现有博客正文覆盖的子题。

## 已知风险

- `.env` 是权限为 0600 的本地明文文件，不是加密保险库；不要把应用开放到局域网或公网。
- SerpAPI Account API 的原始响应包含 API Key；当前代码只保留白名单字段，维护时不能改为保存整个响应。
- SerpAPI 搜索响应也可能在元数据中回显认证内容；`external_evidence.py` 会在快照前移除当前密钥及其 URL 编码形式，不能绕过该步骤。
- 单个机会的“自动补证据”最多发起两次 SerpAPI 搜索；独立调研页按每轮预算运行。两者都只能由用户点击，不得改为分析时静默批量调用。
- 独立调研现有 GSC 信号、主题缺口、边界扩展三个入口；预算硬上限为 10/10/20/20，满足停止条件时可以少用，不能为了耗完额度继续搜索。
- 外部来源更多只增加覆盖，不自动增加置信度；同一底层网页被 Tavily、Firecrawl 和 AI 重复处理仍主要是一份证据。
- OAuth 已连接并同步，但 GSC 顶部行限制、匿名查询与尚未积满完整上一 28 天窗口仍可能让部分页面没有可执行联合证据。
- 旧博客正文中存在历史编码异常字符，导入按 UTF-8 原样保留。
- 商品 JSON 没有完整 canonical URL；LaserPointerHub 已按导入 SKU 和已验证模板 `/p-{SKU}.html` 推导，其他站点模板仍需人工确认。
- 当前新文章候选不会仅凭关键词重合或 SERP 缺席通过；CMS 重叠始终显示为程序推断。

## 2026-07-15 交接更新

- 当前数据库 schema v8；错误 GSC #1/#2 已按运营者明确要求彻底删除，当前分析使用 #5。
- /topics 已同步 65 篇文章与 15 个产品；每项内容一个主主题，通用知识为辅助关联。
- /research 已有 GSC、主题缺口、边界扩展三个入口，硬预算 10/10/20/20，并保存研究记忆与三种人工决定。
- /actions 不再展示六步日常清单；点击后可生成 CMS 八字段内容包，更新旧文时 Slug 锁定。
- 推荐组合已经统一为最多 2 篇旧文章 + 2 篇新文章；真实当前为 2 旧 + 0 新，没有强凑四项。
- 最新验证：`ruff format --check`、`ruff check`、`git diff --check` 全部通过；`pytest -q` 为 41 passed。

下一步：

1. 用户从当前可用研究候选中确认一个真正值得写的方向，再用真实任务生成第一份新文章内容包并人工核对 CMS 粘贴效果。
2. 继续积累新的正常 GSC 窗口；在 query→page 联合数据完善前，不把页面聚合变化包装成高置信查询归因。
3. 后续补齐自动观察、全文语义重复、回溯内链发现和逐条事实来源校验。

## 当前真实使用阻塞（优先于上面的完成状态）

- OAuth 授权、根站点属性同步与查询—页面联合数据已经真实完成；错误的单页属性同步已删除并保留审计。
- 调研重复与偏题已按真实页面复验；文章质量规则已有自动回归，但尚未完成真实新旧文章成稿，因此 U-006/U-009 继续保持“待真实复验”。
- 应用内浏览器仍受 Windows 沙盒连接故障影响；本轮已用真实 HTTP、模板测试和响应式 CSS 规则核对页面，尚无截图式视觉复验。
- 其他真实使用问题统一记录在 `docs/USABILITY_ISSUES.md`。

## 2026-07-17 — OAuth 与调研规则真实闭环

- 首次 OAuth 自动选择曾错误命中单页属性 `/p-B017.html/`，只同步 23 条汇总且 0 条联合行；根因是 URL-prefix 属性同分后按字符串排序。
- 属性选择现只接受 domain property 或 URL-prefix 根路径；已拒绝所有子路径/单页属性，并把真实连接修复为 `https://laserpointerhub.com/`。
- 根站点真实同步生成导入 #6：542 条汇总、1,188 条联合行、5 次 Google 请求，日期不早于 2026-06-22。
- 真实摄影与光绘调研最初产生 8 个候选；现有光绘总指南的小标题已覆盖传感器、基础设置、选激光、衍射帽和长曝光，另有 20-60-20 摄影规则偏离分支。
- 规则现同时检查标题/Slug/SEO 主身份、摘要与正文 Markdown 小标题覆盖、所选调研分支相关性；最终只保留 2 个独立待核验角度。
- 当前 8787 实际页面已显示根站点、1,188 条联合行、完整 RUN #1 调研概况和 2 个新文章建议；五步导航和页面归属没有改动。
- 仍未执行真实 AI 成稿；下一步只复验一篇旧文和一篇新文的最终质量，不再重做整体流程。

## 2026-07-15 — 0.6.0 文章任务卡与 skill 写作骨架（未复验）

### 本轮已写入源码

- 旧文章和新文章统一为一张任务卡：接受任务后制作内容，运营者确认发布后移出当前任务，进入“观察与历史”；历史任务可重新打开。
- 新文章内部按“素材包 → 大纲与链接计划 → 前半篇 → 携带上下文续写后半篇 → 自审与一次修订”执行，日常页面不暴露这些技术步骤。
- 写作约束沿用原有 skill：导语 100–150 个英文词、FAQ 4–5 问、结论 80–120 个英文词；没有增加整篇总字数要求，也禁止虚构第一手经验。
- 内链先从完整站点索引筛出最多 12 个相关候选，成稿最多使用 3 个；没有真正相关页面时不强塞。外链只能使用本任务素材中的精确 URL，并保存声明、来源角色与 evidence ID。
- AI 长任务超时提高到 150 秒；各阶段成功或失败均记录，页面区分可重试的内容制作失败，不再一律误报为连接失败。
- 旧文章仍保持原 Slug 和原主题意图；生成时也只发送筛选后的相关内链候选。

### 验证状态（必须保留）

- 在最终补入导语、FAQ、结论和链接约束之前，针对性测试曾得到 `11 passed`。
- 移除“找不到相关内链也强行补一个”的旧行为后，有 2 个测试因假数据仍假定必有内链而失败；测试假数据已随新规则调整，但没有再次运行。
- 按运营者明确要求，本轮收尾不再运行 `pytest`、`ruff`、语法检查、浏览器、真实 API，也没有重启 8787 服务。因此不能声称 0.6.0 已通过或已部署。

### 下一位接手者优先事项

1. 先检查 `content_production.py` 与相应测试假数据，再运行格式、静态检查和完整测试。
2. 重启 8787 后确认 health 显示 0.6.0，并分别用真实旧文章行动 #2 与新文章机会 #367 完成一次制作、发布、移入历史的闭环。
3. 人工核对生成稿的 CMS 八字段、Slug 锁定、素材可追溯、FAQ 数量、导语/结论长度，以及内外链是否真正相关。
4. 发布后的 7/28/56 天观察提醒尚未实现；旧文章目前是一次生成修改稿，尚未拆成与新文章相同的五阶段内部流程。
## 2026-07-16 — 0.7.0 素材确认与旧写作方法适配

### 本轮已写入源码

- 新文章接受后先在同一任务卡显示 A–H 素材清单；程序整理不调用 API/AI，运营者可先决定是否手工补充。
- 任务卡生成本篇搜索提示词并接受粘贴材料；手工内容保存为不可变快照，重复提交幂等，未明确确认前不开始写作。
- 新文章正常按“文章方案 → 完整初稿 → 编辑定稿”调用 AI 3 次，随后由程序检查；只有不通过才在每篇上限内修订。
- 设置页新增每篇文章 AI 调用上限 3–10，默认 4；它与外部调研 10/10/20/20 预算分开，是防止无限返工的保险丝。
- 适配原 `seo-workflow/write` 的三类文章篇幅、导语、Key Takeaways、H2、结论、FAQ/JSON-LD、动态内外链和自审；不迁移虚构作者、实测、未知 URL 或机械塞链接。
- 旧文章仍独立按元数据/极小修改、局部更新、同主题重写处理，通常一次 AI，Slug 和主要意图锁定。

### 实际验证状态

- `.venv/bin/pytest tests/test_action_workflow.py tests/test_settings.py -q`：12 passed。
- `.venv/bin/pytest -q`：42 passed；Ruff 格式与规则检查通过；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- 自动测试证明未确认素材时 AI 调用为 0、手工材料不调用 API、重复粘贴不重复写入、正常新文章恰好调用 3 次并记录默认上限 4。
- 真实库只读检查：行动 #4 有 56 个可用来源且 A–H 均齐全；当前没有待生成的新文章行动。本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI。
- 8787 已启动 0.7.0；健康接口、设置页参数和文章任务页说明已实际核对。首篇真实文章的质量和完整页面闭环仍待运营者执行。

### 最短操作

1. 打开“设置 → AI 助手 → 文章制作调用保护”，把每篇上限设为 3–10；建议先保持默认 4，只有真实返工经常触顶时再提高。
2. 新文章进入“文章任务”后先看素材；需要时按提示手工搜索并粘贴，不需要时直接确认“现有素材够用，开始写作”。
3. 核对 CMS 八字段、事实、FAQ 与内外链，手工发布后点击“我已发布”，任务会移入“观察与历史”。

### 遗留

- 用第一篇真实新文章验证质量、耗时、真实模型调用、CMS 粘贴与发布后移入历史。
- GSC query→page/API、7/28/56 天自动观察、全文语义重复、自动回溯内链和声明—来源逐条校验仍待增强。

## 2026-07-16 — 0.8.0 五步单一归属工作流

### 本轮完成

- 移除“今日工作台”，根地址直接进入第 1 步；主导航固定为数据导入、主题调研、文章建议、文章制作、主题图谱。
- 侧栏保持 188px，但用更高优先级解除旧图标规则施加给文字的 22px 宽度；主标题和说明不再两字换行。文章建议和文章制作均为旧文章左列、新文章右列，1080px 以下改单列，窄屏改为横向步骤导航。
- 第 2 页只运行调研并显示本轮概况、实际用量和“部分完成”原因；不展示具体候选或决定按钮。
- 第 3 页只读取已保存结果，打开页面零外部调用；每列优先 2 篇，其后最多 5 篇折叠，仅保留开始执行、暂时跳过、不再推荐。
- 开始执行后卡片从第 3 页移到第 4 页；确认发布后退出当前制作页。旧文章完成后，必须等新 CMS 与新 GSC 都回流才允许再次评估。
- 调研选择只保存决定和所选分支，不创建候选图谱节点；只有 CMS 真实重新导入后才建立文章覆盖。
- GSC 或 CMS 成功导入后自动更新内部旧文章分析，不调用外部 API；修复素材表单绑定到错误步骤路由的问题。

### 真实数据清理与重建

- 按运营者确认清除 13 轮旧分析、222 个旧机会、4 个旧任务、3 轮旧调研、27 次派生外部运行、22 条证据、11 次 AI 运行、14 条研究记忆、1 个候选图谱节点和 26 个派生快照。
- 保留 3 次有效导入、80 项 CMS 内容、659 条当前 GSC 指标、连接设置和永久“不再推荐”决定。
- 重建后为 17 个内部候选、2 篇优先旧文章、0 篇新文章；主题图谱为 119 个节点、80 个主映射和 348 个辅助关联。

### 验证

- `.venv/bin/pytest -q`：45 passed；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools`、`git diff --check`：全部通过。
- 真实 8787 服务已重启到 0.8.0；五个主页面、设置、方法和样式均返回 200，真实第 3 页显示 2 篇优先旧文章与 0 篇新文章，第 4 页为空。
- 自动回归覆盖卡片第 3→4 页移动、发布后消失、候选不进入图谱、CMS 重导后才形成覆盖、清理保留导入/内容/永久拒绝，以及第 3 页零外部调用。
- 本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI；真实文章质量仍需首篇实际制作验证。

## 2026-07-17 — 0.9.0 GSC 联合证据与内容质量收口

### 本轮完成

- 五步主流程保持不变；第 1 页增加 Google Search Console 只读 OAuth 连接、站点自动匹配和一键同步。Blog JSON、Product JSON 仍手工导入，GSC Excel 只作后备。
- Search Analytics 只请求 final/web 数据，所有窗口和分页都受 2026-06-22 硬边界约束；保存 date、query、page 及 date + query + page 联合指标。
- OAuth 使用 PKCE 与本机 loopback；client JSON 和 token 只放 `.secrets/google/`，数据库只存站点与同步审计，access log 禁用。
- 页面机会默认只诊断；当前联合行满足后才允许 CTR/striking-distance 进入制作，click-loss 还要求上一窗口联合行；内容生产再次复查，缺证据时 AI 调用为 0。
- 调研候选统一自然英文，并与当前 CMS、历史候选和本轮候选三层去重；真实的红绿激光演示重复主题已加入回归。
- 旧文章改为草稿 + 独立编辑审查，再执行主题/slug、语言、修改范围、链接白名单、重复 URL、填充和虚构经验检查；最终未通过不保存。

### 真实数据与凭据

- OAuth 客户端与已生成 token 位于 `.secrets/google/`；目录 0700、文件 0600，Git 忽略规则已验证，凭据值未进入 SQLite。
- 数据库 schema 9；`quick_check=ok`、外键违规 0，当前为 4 个导入、18 个内部机会、1 个调研运行、2 个调研候选、0 个行动。
- 原始清理备份仍为 `data/backups/seo_ops-before-0.9.0-20260717T032918Z.db`；真实复测前后另有 e2e-repair 与 candidate-filter 两份备份。
- 错误单页属性导入及其 23 条汇总、专属分析和快照已删除；同步审计保留且不回退旧批次。
- 根站点 OAuth 导入 #6 为唯一活动 GSC：542 条汇总、1,188 条联合行，实际日期 2026-06-22 至 2026-07-14。
- analysis #1 有 18 个内部机会；文章建议页显示 16 个可执行旧文候选并只突出 2 个。
- 摄影与光绘 RUN #1 保留完整外部运行概况，按 0.6.1/0.4.1 规则只保留 2 个英文独立角度。

### 验证

- `.venv/bin/ruff format --check src tests tools`：53 files already formatted。
- `.venv/bin/ruff check src tests tools`：All checks passed。
- `.venv/bin/pytest -q`：55 项全部通过；针对性 GSC/调研/五步回归为 15 passed。
- 凭据权限、Git 忽略、真实库完整性、根属性和凭据未入库均已复核。
- 本轮真实调用 Google 5 次，并运行摄影与光绘调研 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1；没有为候选清理再次调用。Windows 侧三个实际页面均已核对。

### 运营者下一步

1. 不要重复当前授权、GSC 同步或摄影与光绘调研；先查看第 3 页当前 2 旧优先项与 2 个新主题。
2. 各选一篇旧文和新文完成真实成稿，人工核对事实、意图、英文、链接、修改范围和 CMS 字段。
3. U-010 已按真实页面复验解决；U-006/U-009 只有在真实新旧成稿质量通过后才能关闭。
