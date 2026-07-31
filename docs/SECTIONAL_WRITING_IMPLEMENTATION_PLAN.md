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

状态：**待实施**

- 文章候选来自 internal-links-map；
- 产品候选来自 live_products_report；
- 外部引用来自 evidence cards；
- 验证 URL、库存、属性、self-link、重复目标；
- 独立计算文章/产品 `required|recommended|none`；
- 保存 Context Manifest 和 shadow 报告；
- 不修改正式 draft。

验收：用固定 fixtures 和 Action #3 的只读数据检查候选是否合理；人工抽查明显机会没有漏召回。

### Phase 3 — 章节生成引擎与 checkpoint

状态：**待实施**

- 逐 H2 生成，每节包含完整段落而非逐自然段调用；
- 输入仅包含精简全局 brief、当前合同、相关证据/链接、前后节提示；
- 输出章节 Markdown、链接占位符和链接决策；
- 每节独立 checkpoint，可中断恢复；
- Introduction/Takeaways/Conclusion/FAQ 后生成。

feature flag 默认关闭。旧 W0 仍为正式路径。

### Phase 4 — 章节 claim ledger 与链接绑定

状态：**待实施**

- claim-ledger 关闭 DeepSeek thinking；
- 只处理本节 W1b 事实句；
- 每节只带相关证据；
- 不可重试 `length/content_filter/tool_calls` 立即停止；
- 验证模型只能使用本节允许的 evidence IDs；
- 解析 ARTICLE/PRODUCT/CITE 占位符并注入真实 URL；
- 合并 ledger 后执行全文最终验证。

### Phase 5 — 确定性组装与全局编辑门禁

状态：**待实施**

- 组装 frontmatter、主体、FAQ JSON-LD；
- 检查章节顺序、重复、过渡、词数；
- 全局链接去重、商业密度、锚文本和位置；
- 将原有“按字数最低链接数”降为 shadow 指标或警告；
- 保留 URL 无效、下架产品、自链接、越权 ID 等硬阻塞。

### Phase 6 — W1b/W2 章节定位与局部修订

状态：**待实施**

- 将失败句映射到 section ID；
- 仅重写失败章节或只做 Link Repair；
- 其他章节保持不变；
- 重新组装后运行全部正式门禁；
- 保留现有批次上限和原子写。

### Phase 7 — Shadow 对比、正式切换与清理

状态：**待实施**

- 相同主题对比旧整篇路径与新章节路径；
- 比较事实覆盖、链接自然度、产品匹配、重复率、AI 调用失败率和成本；
- 全量测试通过；
- 先对单一 Action 启用 feature flag；
- 验收后逐步切换；
- 稳定后再删除旧路径，不在同一提交完成。

## 提交和文档规则

每个 Phase 至少一个独立 commit，提交前必须：

1. 目标专项测试通过；
2. 相关回归通过；
3. Ruff、compileall、`git diff --check` 通过；
4. 更新本文件的状态与测试结果；
5. 更新 `HANDOFF.md`、`docs/WORKLOG.md` 和必要的 `CHANGELOG.md`；
6. 真实 workflow 只在对应 Phase 代码提交并通过全量测试后运行。

## 当前下一步

实施 Phase 2：读取 internal-links-map、live_products_report 与 evidence cards，生成只读
候选注册表、机会评分和 Context Manifest；仍不接管正式 W0。
