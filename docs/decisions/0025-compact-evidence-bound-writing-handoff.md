# ADR-0025：紧凑且可审计的写作上下文交接

- 状态：Accepted
- 日期：2026-07-29
- 适用范围：Legacy R3→W0、W1b/W2 AI 修订与 claim ledger 生成

## 背景

Legacy Research + Write 已分开研究、写作和检查，但 W0 仍把完整 material pack、
四份长上下文、长结构规则和 claim ledger 格式要求放进一次模型调用。真实 DeepSeek
运行曾在正文完成后漏掉 ledger，说明小模型容易在长提示中忽略中部或末尾的交卷要求。

不能通过放松 evidence ledger、claim ledger、W0 原子写或 W1b/W2/W3 gate 解决。

## 决定

1. R3 在原始 material pack、research brief 和 evidence ledger 之外，确定性地产生三份
   action 私有、可重建的 JSON 交接产物：
   - `write-brief-{slug}.json`：意图、层级、差异化指导和 R3 大纲摘录；
   - `coverage-contract-{slug}.json`：每个计划 H2 都是必须覆盖的读者问题，并列出
     可供该节使用的候选 evidence ID；
   - `evidence-cards-{slug}.json`：保留所有短证据卡，并按章节用确定性词项召回和
     required evidence 保留规则选择最多四张卡。
2. 这些文件不是新的事实源，也不是发布门。完整 material pack、evidence ledger 和
   原有检查器仍是权威来源。旧 action 缺少新文件时，W0/W1b/W2 从既有 brief 和 ledger
   无外部调用地重建它们。
3. W0 分为两个 AI 任务：正文任务只接收紧凑 hand-off；ledger 任务只接收最终正文和
   evidence cards，并只能返回 JSON。服务仍使用原有 parser 校验逐句 claim，并由服务端
   写入 draft SHA 后使用原有原子写协议。
4. W1b/W2 修订同样先生成正文；其上下文只包含当前草稿、失败报告、紧凑 brief、
   相关证据卡及已被当前 claim ledger 使用的证据卡。随后独立重建并严格验证 ledger。
5. 卡片选择采取“召回优先、再精排”：词项匹配为零时保留最靠前的可用卡，`required`
   evidence 永不静默丢弃；未被本次选中的完整证据仍在 ledger 和 cards 文件中可审计。

## 后果

模型不再同时承担文章、证据 JSON 与全部背景消化三项高负荷任务；它仍不能写出没有
来源支持的事实。覆盖合同让“精简后是否漏掉必写点”成为可检查的交接对象，而不是隐含
在长 prompt 中的要求。

这不保证任何模型的真实文章质量；必须以相同主题完成 R3→W3 端到端验收，并比较事实
覆盖、结构、链接与 gate 结果。ledger 缺失、非法或证据不足仍会暂停，绝不自动补造。

## 失效与复查条件

- 真实文章显示确定性词项卡片选择系统性漏掉某类关键证据；
- section 覆盖合同与实际读者意图长期不一致；
- 模型在独立 ledger 任务中仍频繁失败，或完整 pack 的事实检查经常发现漏引；
- 真实质量对比显示紧凑 hand-off 低于旧长上下文流程。

计划复查日期：2026-10-29。
