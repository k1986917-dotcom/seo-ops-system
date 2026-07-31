# 章节化证据写作实施计划

最后更新：2026-07-31

## 目标

把现有“整篇正文 → 全文 claim ledger → 全文修订”升级为：

```text
Article Blueprint
→ Section Contracts
→ Section Evidence/Link Context
→ 逐章节正文与 ledger
→ 服务端链接绑定
→ 确定性全文组装
→ 全文 W1b/W2/W3 门禁
→ 仅修失败章节
```

用户界面保持现有 R0→R1→R3→W0→W1b→W2→W3。所有拆分发生在服务内部。

## 核心成功指标

1. 长正文和 claim-ledger AI 调用不再因上下文/推理预算耗尽而返回空正文。
2. 每个章节只接收完成该节任务所需的证据和链接候选。
3. 有明确链接机会时，模型不能用 0 链接偷懒；无有效机会时不强塞链接。
4. 产品推荐必须在售、属性匹配、出现位置自然，并有正文理由。
5. 任何失败不产生 draft/ledger 混合版本，不绕过现有正式门禁。
6. 站点真实产品目录优先于旧 brief；系统不得根据单篇文章备注重新定义网站卖什么。
7. 正式内容固定为 English；非英文历史备注只进入审计，不进入写作上下文。
8. 产品标题、目录表格和详情互相冲突时，必须生成可操作的数据质量错误报告。

## Link Opportunity Gate

### 状态

| 状态 | 条件 | 最低值 | AI 义务 |
|---|---|---:|---|
| `required` | 高相关有效候选，且本节职责需要继续学习或完成选择 | 1 | 必须使用至少一个批准 ID |
| `recommended` | 有相关候选，但不是完成本节任务的必要步骤 | 0 | 必须采用或明确拒绝并给 reason code |
| `none` | 无有效候选或该节禁止此类链接 | 0 | 不得输出该类链接，保存 reason code |

### 文章内链机会

优先条件：

- 候选 URL 有效且不是当前文章；
- 标题/概念与章节 `must_answer` 有明确重合；
- 目标文章提供当前章节不应展开的补充知识；
- 同一目标未在更合适的章节使用。

### 产品内链机会

必须同时满足：

- 章节阶段为 `select`、`apply` 或明确解决方案；
- 产品在售且 URL 有效；
- 产品属性满足章节中的 required attributes；
- 正文能先解释选择标准和适配理由；
- 章节不是法规、安全警告或事故分析；
- 没有更合适的分类页替代具体 SKU。

### 防偷懒与可调节机制

- 服务端决定 `min_required`，不是模型决定；
- 少于最低值时触发局部 Link Repair，一次上限；
- `min=0` 时也必须输出决策清单，不能静默省略；
- 持久化候选、评分、采用、拒绝和原因；
- 阈值与候选上限集中配置；
- shadow 报告统计“有机会但输出 0”的比例，便于快速调整。

## 分阶段实施

### Phase 0 — 架构与安全基线

状态：**完成**

- [x] 撤回未完成的 claim-ledger 试验补丁，恢复干净 HEAD。
- [x] 新增 ADR-0026。
- [x] 建立本实施计划。
- [x] 明确 feature flag、shadow mode、原子提交和回滚原则。

验收：工作区仅包含文档修改；生产行为不变。

### Phase 1 — 纯数据合同与确定性构建器

状态：**完成**

已新增且暂不接管 W0：

- `article-blueprint-{slug}.json`
- `section-contracts-{slug}.json`
- `section-link-contracts-{slug}.json`
- JSON schema/typed validation
- 旧 action 的无外部调用重建逻辑

已完成：

- 稳定 section ID 不依赖章节顺序；
- Article/Section/Link 三份合同严格对齐；
- 未评估链接机会强制使用 `unassessed + min_required=null`；
- `required` 必须存在候选且最低值至少为 1；
- 安全/法规章节产品机会直接标记为 `none`；
- 三文件持久化发生部分替换失败时恢复旧 bundle；
- 10 项专项测试、Ruff、compileall、`git diff --check` 通过。
- MCP 沙箱全量 pytest 在收集阶段被既有 Landlock 限制阻断：`openpyxl` 初始化读取
  `/etc/mime.types` 触发 `PermissionError`；需由本机环境完成全量验证后提交。

回滚：删除新模块和 shadow 产物即可；正式 W0 不受影响。

### Phase 2 — 候选注册与链接机会评分（shadow mode）

状态：**完成并推送**

- [x] 文章候选来自 internal-links-map，过滤 self-link、重复 URL 和非法 URL；
- [x] 产品候选来自任意包含 ID/SKU、Title/Name、URL 的 Markdown 产品表；核心解析器
  不依赖 Power、Wavelength 等激光专属列，喷码机和园林工具 fixtures 已覆盖；
- [x] 产品表其余列作为通用 attributes 保存；只有 `site_policy`、`catalog_policy` 或
  `operator_approved` 的结构化约束可以过滤候选；
- [x] 旧 brief 条目只作为 advisory points。英文条目若与目录明显冲突则降级为
  alignment pending；非英文条目进入 `brief_points_rejected`，不进入写作上下文；
- [x] 正式合同固定 `content_language=en`；混合 FAQ 标题可移除中文括号，仍含中文的 H2
  fail-closed；
- [x] 外部引用兼容纯 URL 与 Markdown `[title](URL)` evidence card；
- [x] 候选评分区分 section match 与 topic match。全站品类词只能帮助召回，不能单独
  把链接升级为 `required`；
- [x] 独立计算文章、产品和外部引用的 `required|recommended|none`；
- [x] 生成 Catalog Data Quality Report。产品标题、表格和详情字段冲突时，报告产品 ID、
  冲突值、修复建议，并将该 SKU 标记 `blocking_for_auto_link=true`；
- [x] 保存 Context Manifest 和 shadow report 的原子持久化与回滚逻辑；
- [x] 新增只读 `tools/sectional_shadow_preview.py`，默认不写文件、不调用 AI；
- [x] 正式 draft、claim ledger、w2-state、数据库和 W0/W1b/W2 均未修改。

专项验收：

- 31 项 Phase 1+2 测试通过；
- Ruff、compileall、`git diff --check` 通过；
- 激光产品、喷码机、园林工具三类 catalog fixture 通过；
- Action #3 只读 shadow 成功读取 65 篇文章、15 个产品、12 张 evidence cards；
- 真实 B303 被识别为标题 `532nm` 与目录属性 `650nm` 冲突，输出
  `catalog_attribute_conflict`，修正前不参与自动链接；
- 未调用 AI，未写入任何正式或 shadow Action 文件。

最终验收：完整测试 `456 passed, 1 warning`；31 项 Phase 1+2 专项测试通过；
提交 `666b3cd4a107ac8a3a3a5eed9b91a5eedfd831cc` 已推送，工作区干净，正式
W0/W1b/W2 未改变。

### Phase 3 — 章节生成引擎与 checkpoint

状态：**已完成本机全量验证并形成本地原子提交；远端推送待本机认证环境完成**

- [x] 逐 H2 生成，每节包含 2–5 个完整段落而非逐自然段调用；
- [x] 输入仅包含当前合同、相关证据/链接、前一节短摘要和下一节提示；
- [x] 输出精确 H2、章节 Markdown、ARTICLE/PRODUCT/CITE 占位符和链接决策；
- [x] 原始 URL、越权 ID、非英文输出、重复标题、词数和链接门禁 fail-closed；
- [x] 每节独立原子 checkpoint，可在 provider 中断后只续跑未完成章节；
- [x] checkpoint 绑定 package SHA；损坏、过期或内容不一致时不得恢复；
- [x] Introduction/Takeaways/Conclusion/FAQ 在主体完成后单独生成并 checkpoint；
- [x] article-frame canonical 数量合同固定为 Takeaways 3–5、FAQ 3–4；生成 prompt、
  package validator 与 Phase 5 assembly 使用同一边界；
- [x] 兼容旧编号加粗 H2 大纲，不把后续链接策略误当 FAQ 要点；
- [x] 产品推荐区分 `strong|contextual|related_catalog|approved_constraint`；
- [x] `compare|select|apply` 有内容相关且数据有效商品时最低要求 1 个产品链接；
- [x] `related_catalog` 只能称为相关目录选项，禁止虚构具体用途适配或合规；
- [x] 只读 preview 可输出每节 prompt 大小和候选 ID，不调用 AI、不写 checkpoint。

feature flag 默认关闭。旧 W0 仍为正式路径。

专项验收：Phase 1–3 联合 `54 passed`；Ruff、compileall、`git diff --check` 通过。
Action #3 只读构建的正文合同现为 6 个 generation packages；旧 brief 的 FAQ 已延后到
article frame，不再重复成为正文 H2。单节 user prompt 约 3.6K–6.5K 字符；对比/选型
章节产品最低 1，安全/错误产品 0，B303 继续被排除。

本机完整 pytest：`479 passed, 1 warning`；唯一 warning 为既有 Starlette/httpx 弃用提示。
Phase 1–3 专项：`54 passed`；Ruff、compileall、`git diff --check` 全部通过。
MCP 完整 pytest 仍因既有 Landlock 限制无法读取 `/etc/mime.types`，不是代码失败。
Phase 3 本地提交：`7894080fe944fbb2ad96905bbeca73c809caf011`
(`feat: add resumable sectional generation`)；已由本机认证环境推送，本地与远端一致。

### Phase 4 — 章节 claim ledger 与链接绑定

状态：**已完成全量验证并形成本地原子提交，待推送**

- [x] ARTICLE/PRODUCT/CITE 只按本节最终 Link Contract 白名单绑定真实 URL；
- [x] registry SHA、候选 ID、冲突产品、章节授权和残留占位符全部 fail-closed；
- [x] H1、Introduction、Key Takeaways、正文 H2、Conclusion、FAQ 先确定性组装，再分配
  全局 S-ID，避免后插内容导致 ledger 句子编号漂移；
- [x] frontmatter 后置添加不会改变正文 S-ID；
- [x] 正文 H2 和四个 article-frame 单元分别生成 claim ledger checkpoint；
- [x] 每个单元只带最终 Link Contract 允许的 evidence IDs；frame 单元使用正文已批准
  evidence 的确定性并集，不读取完整 evidence ledger；
- [x] claim-ledger 调用显式 `thinking_mode=disabled`，输出预算 4000；
- [x] 非重试型 `length/content_filter/tool_calls` 空响应立即停止，非法 JSON 最多重试一次；
- [x] 模型只能返回 sentence_id、claim_type、evidence_ids；claim_text 由服务端从最终
  Markdown 注入；
- [x] resolved delivery 与每单元 ledger 均使用 SHA 绑定、原子 checkpoint 和损坏恢复拒绝；
- [x] 合并 ledger 已通过现有 Legacy `_validate_claim_ledger_json` 权威校验器。

专项验收：Phase 1–4 联合 `72 passed`；Ruff、compileall、`git diff --check` 通过。
本机完整 pytest：`497 passed, 1 warning`；唯一 warning 为既有 Starlette/httpx 弃用提示。
Action #3 只读合同重建确认正文 6 节，未调用 AI、未写入 Action 文件。正式 W0/W1b/W2
仍未导入 Phase 4 模块。Phase 4 提交
`347fb832d51db5ebfb3332b006970487bc5810af` 已推送，本地与远端一致。

### Phase 5 — 确定性组装与全局编辑门禁

状态：**shadow-only 代码与全量验证完成，已本地提交待推送**

- [x] 确定性组装 canonical frontmatter、Phase 4 body 和 FAQPage JSON-LD；
- [x] FAQ schema 只从可见 FAQ 问答生成，问题与答案逐项一致；
- [x] frontmatter、FAQ JSON-LD、fenced metadata 不改变正文 S-ID；
- [x] Key Takeaways 输出为旧 W1b 可识别的 blockquote；
- [x] 检查目标词数、H1/H2 顺序、Takeaways/FAQ 数量、跨章节重复句和重复段落；
- [x] Markdown URL 必须与 Phase 4 bindings 完全一致，未登记链接 fail-closed；
- [x] 文章/产品重复目标、泛化锚文本和链接硬上限阻塞；外部域名锚文本允许；
- [x] blog/product/external 按字数比例只生成 advisory metrics/warnings，不强塞链接；
- [x] product link 过早仅警告；是否内容相关沿用 Phase 2–3 Link Contract；
- [x] final claim ledger 只重绑定最终 draft SHA，句子与 evidence 决策保持 Phase 4 结果；
- [x] assembled draft/ledger/report 三文件事务写入与损坏恢复拒绝。

专项验收：Phase 1–5 sectional 联合 `84 passed`；sentence-ID 与旧 W1b/FAQ/修订兼容
回归 `78 passed`；Ruff、compileall、`git diff --check` 通过。本机完整 pytest：
`510 passed, 1 warning`；唯一 warning 为既有 Starlette/httpx 弃用提示。MCP 完整 pytest
仍因既有 Landlock 限制无法读取 `/etc/mime.types`，不是代码失败。正式 W0/W1b/W2
未导入 Phase 5。

Phase 5 提交 `287235eb5e18f2bac47ab2164206b4f2fc5d178c`
(`feat: add deterministic sectional assembly`) 已由本机认证环境推送，本地与远端一致。

### Phase 6 — W1b/W2 章节定位与局部修订

状态：**shadow-only 代码与全量验证完成，待原子提交**

- [x] 规范化 W1b/W2 checks、fact issues、fix items、link issues 和布尔 gate/error；
- [x] 通过 section ID、S-ID、完整 sentence text、绑定 URL、heading/frame alias 定位单元；
- [x] 评分器/蚕食检查器错误、蚕食阻塞和不可定位失败保持 global blocker；
- [x] 区分 `section_rewrite|link_repair|ledger_repair|frame_rewrite`，最多两轮；
- [x] Link Repair 保持读者可见文字字节语义不变，只能调整批准占位符与 decisions；
- [x] 多章节同轮按顺序修复，后一节使用前一节修复后的摘要；
- [x] 未目标正文输出保持不变；正文变更后刷新 article frame；frame-only 不调用正文模型；
- [x] 新全局 S-ID 生成后，未改 claims 通过唯一 claim_text 映射；不安全时仅重审该单元；
- [x] repair checkpoint 绑定完整 repair package；损坏/越权/可见文字变化时拒绝恢复；
- [x] 修复后重新运行 Phase 4 delivery/ledger 与 Phase 5 assembly 全局门禁；
- [x] result 持久化 repair plan、section/frame/delivery/ledger/assembly，并验证嵌套一致性。

专项验收：Phase 6 新增 `24 passed`；Phase 1–6 sectional 联合 `108 passed`；Ruff、
compileall、`git diff --check` 通过。本机完整 pytest 为 `534 passed, 1 warning`；唯一
warning 为既有 Starlette/httpx 弃用提示。Legacy/W1b 兼容回归未排除
`TestPrecheckGateDisplay`，结果为 `263 passed, 1 warning`。MCP 完整 pytest 仍会被
`/etc/mime.types` Landlock 权限阻止，但本机完整套件已通过。正式 W0/W1b/W2 尚未导入
Phase 6。

### Phase 7 — Shadow 对比、正式切换与清理

状态：**代码、本机全量验证与兼容回归完成，待真实单 Action shadow 验收**

- [x] `off|shadow|action` 三态，默认 `off`；Action 模式必须显式 ID 白名单；
- [x] 旧 W0 先成功，sectional 后置运行；失败保留旧 draft/ledger；
- [x] 完整 shadow candidate 支持 checkpoint 续跑，不写正式 pair；
- [x] 比较字数、链接、claim 覆盖、重复句、AI 调用、重试和空响应；
- [x] 空响应、重试过多、claim 覆盖退化或重复增加时禁止 promotion；
- [x] promotion 绑定 policy/comparison/assembly 和正式 pair SHA，并备份旧 pair；
- [x] promotion 与 rollback 的最终 manifest 写失败均恢复操作前 pair；
- [x] 独立 AI 调用硬预算，默认 24、可配置范围 8–40；
- [x] Web 与 Hermes 将稳定 Action ID 传入 W0；非白名单 Action 保持旧路径；
- [x] 提供显式 decision/rollback 运维命令；rollback 需要确认词；
- [x] 已有正式 pair 的 Action 提供 shadow-only Web POST；不重跑 W0，前后校验 SHA，
  异常修改原子恢复，不更新 Legacy 阶段；
- [x] 本机完整 pytest；
- [ ] Action #3 真实 shadow 对比；
- [ ] shadow 无 blocker 后单 Action promotion/rollback 验收；
- [ ] 稳定后逐步扩大，不在本阶段删除旧路径。

专项验收：Phase 1–7 sectional 联合 `136 passed`；W0/Phase 7 聚焦回归 `40 passed`；
Ruff、compileall、`git diff --check` 通过。本机完整 pytest 为 `563 passed, 1 warning`；
Legacy/W1b 完整兼容回归为 `264 passed, 1 warning`，未排除 `TestPrecheckGateDisplay`。
MCP 完整 pytest 仍在收集阶段被 `openpyxl -> mimetypes -> /etc/mime.types` 权限阻止。
默认 rollout 为 `off`，尚未运行真实 AI/API 或 Action。

外部静态审计后新增 hotfix：修复 Phase 3 允许 Takeaways 6 条/FAQ 5 条而 Phase 5 必然
拒绝的跨阶段合同冲突；锁定 canonical frame requirements，未知产品 `fit_level`
fail-closed；项目版本 metadata 对齐为 `0.11.5`；token usage 未提供时显式记为 unknown。
Phase 1–7 sectional 加版本测试共 `143 passed`；Legacy/W1b 全口径（含 5 个 Web gate
测试）`279 passed, 1 warning`。MCP 使用临时且已删除的测试运行器，仅在测试进程内将
系统 MIME 文件列表置空，分批覆盖全部测试文件，最终合计 `570 passed, 1 warning`。
Ruff、compileall、`git diff --check` 通过。hotfix 可提交；提交并推送后方可启动 shadow。

## 提交和文档规则

每个 Phase 至少一个独立 commit，提交前必须：

1. 目标专项测试通过；
2. 相关回归通过；
3. Ruff、compileall、`git diff --check` 通过；
4. 更新本文件的状态与测试结果；
5. 更新 `HANDOFF.md`、`docs/WORKLOG.md` 和必要的 `CHANGELOG.md`；
6. 真实 workflow 只在对应 Phase 代码提交并通过全量测试后运行。

## 当前下一步

本机完整 pytest 全绿后先推送 Phase 6，再提交/推送 Phase 7。随后以 `shadow` 对
Action #3 运行真实候选；比较无 blocker 后才启用单 Action `action` 模式并验收回滚。
