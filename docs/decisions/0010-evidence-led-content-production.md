# ADR-0010：证据驱动、按文章类型分流的内容制作

- 状态：Accepted；新文章固定组件、篇幅、链接与生成阶段由 ADR-0011 部分取代
- 日期：2026-07-15

## 背景

旧 `research`/`write` 工作流已经具备素材包、SERP 意图、权威引用、链接验证、正文检查和回溯内链等能力，但也把 APP 开头、Key Takeaways、FAQ、视频、CTA、字数、关键词位置和链接密度强制到几乎每篇文章。它还存在化名作者、未经证实的实测资历、固定 FAQ Schema、单一 TF-IDF 蚕食阈值和“Scrub 去 AI 水印”等不可靠做法。

`action-plan-0.3.0` 又把用户暴露在六步通用清单和 evidence ID 中，没有直接交付用户真正需要的修改稿或新文章。运营者希望第二阶段对第一阶段接受的文章完成大纲、素材、内外链、写作、自审、去模板化和重复检查，并输出可复制到 CMS 的字段。

## 决定

1. 第二阶段命名为“文章任务”。用户只从任务卡制作内容并确认发布，内部检查不变成逐项人工任务。
2. 每篇文章建立独立且保留的专项素材包，包含意图、页面职责、核心答案、SERP、用户痛点、事实声明、原始来源、产品事实、图谱边界和链接计划。
3. 大纲根据文章类型选择；不强制全文总字数、视频或 CTA，但为稳定输出保留 100–150 词导语、4–5 个真实 FAQ 和 80–120 词结论。
4. 旧文章分为元数据/极小修改、局部更新和原主题重写。slug 永不修改，主要意图不得偏离原 URL；若必须改变意图则转为新文章。
5. 内链先从全站索引筛选相关候选，正文目标 2–3 个且不强塞；外链目标 2–6 个，只能来自本任务素材并逐条追溯具体声明。
6. 重复检查分为文字重复、意图/页面职责重复和允许共享的辅助知识。换文案不能解决意图重复。
7. “去 AI 味”改为编辑去模板化：检查空泛语言、跨文章模板、虚构经验、缺少具体证据和重复 CTA；AI 检测器不作为门槛。
8. 自审区分确定性检查和 AI/编辑判断。AI 可以提出修订，但不能制造事实、作者、实测、案例或来源。
9. 新文章和旧文重写交付 Title、Slug、Summary、Content Markdown、Tags、SEO Title、SEO Description、SEO Keywords；小修改只突出实际变化并提供完整预览。
10. 发布始终由运营者完成；点击“我已发布”后任务退出当前列表并进入观察历史。
11. 7/28/56 天自动提醒仍是后续增强。

## 已确认的实现选择

- 接受建议只建立文章制作任务，不自动消耗 AI；运营者在执行页点击一次“生成修改稿/制作新文章”。
- 第一版直接生成完整 CMS 交付包，不强制增加一次大纲确认；后续可按真实使用反馈增加可选调整。
- 小修改允许输出精确替换区块；元数据修改会保留原正文，旧文章 Slug 始终由程序锁定。
- CMS 交付只包含运营者确认的八个字段，不额外强制 AI 制作说明。
- 外链仅保留本轮存证 URL，并标注官方/研究、社区线索或行业/其他来源角色；社区来源不能单独支持安全、法规或规格事实。
- 主要主题由标题/H1 与核心用户任务共同判断。安全、功率、波长作为辅助知识时允许共享，本身是核心问题时才作为主要文章主题参与查重。

## 实现后果

复杂检查保留在系统内部，运营者直接获得可执行稿；不设全文机械字数，FAQ、导语和结论作为运营者要求的稳定交付规格。系统仍不自动发布，7/28/56 天自动提醒尚未实现。

## 方法依据

- Google people-first：https://developers.google.com/search/docs/fundamentals/creating-helpful-content
- Google 生成式 AI 内容指南：https://developers.google.com/search/docs/fundamentals/using-gen-ai-content
- Google 高质量评测：https://developers.google.com/search/docs/specialty/ecommerce/write-high-quality-reviews
- Google 链接最佳实践：https://developers.google.com/search/docs/crawling-indexing/links-crawlable
- Google SEO Starter Guide：https://developers.google.com/search/docs/fundamentals/seo-starter-guide
- Ahrefs content outline：https://ahrefs.com/blog/content-outline/
- Content Marketing Institute 事实核查：https://contentmarketinginstitute.com/content-creation-distribution/fact-checking-for-accuracy-in-human-and-ai-generated-content-checklist
- AI 检测工具同行评审研究：https://link.springer.com/article/10.1007/s40979-023-00146-z
