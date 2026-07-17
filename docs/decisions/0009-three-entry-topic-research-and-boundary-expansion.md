# ADR-0009：三入口主题调研与边界扩展

- 状态：Accepted
- 日期：2026-07-15
- 替代范围：ADR-0005/0006 中“新主题调研必须从明确 GSC 查询开始”的限制

## 背景

`0.4.1` 的预算化外部调研只从最新分析中的明确 GSC 查询开始。当站点较新、GSC 观察窗口不足，或现有主题接近饱和而没有查询达到门槛时，外部调研无法启动。旧 Plan 曾使用用户痛点、Google Suggest、竞品、场景扩展和问题链，因此能够在 GSC 弱时产生候选；但旧方法缺少稳定主题身份、拒绝记忆和可靠查重，最终不断重复发现宽泛主题。

运营者确认，第一阶段应由内部数据、外部调研和主题图谱共同工作，产生最多 2 篇新文章与 2 篇旧文章建议；信号不足时可以继续外部调研、从主题图谱指定方向或等待新信号。

## 决定

1. 第一阶段命名为“关键词与主题调研”，结束条件是产生通过资格门槛的文章建议，或明确本轮没有合格机会。
2. 主题调研有三个相互独立的入口：GSC 信号、主题图谱缺口和边界扩展。GSC 不再是新主题研究的唯一种子。
3. 查询—页面联合数据边界继续保持：没有联合数据时不能声称某查询属于某旧页面；该边界不禁止围绕图谱缺口或已确认分支发现新主题。
4. 边界扩展保持七个一级维度，并补全其内部检查项：受众/角色，场景/目标/生命周期，故障/限制/替代，决策/验证，产品/技术/兼容，法规/安全/地区，以及相邻站内问题。搜索意图、地区/语言、经验角色、季节/环境条件和内容形式作为横向筛选器，不单独生成节点或文章。
5. Google Suggest、PAA、用户痛点、论坛和竞品内容用于发现线索；SERP 用于判断时点意图；重要事实回到官方、一手、标准或研究来源。
6. 每次调研保存分支、维度、横向筛选器快照、查询、参数哈希、来源、发现概念、重复匹配和无结果记录。后续运行先检查历史，避免重复消费和重复推荐。
7. 每篇文章只指定一个主要文章主题，同时允许多个辅助知识主题。安全、波长和功率作为辅助知识时不主导重复判断；当它们是页面主要问题时正常作为主要主题参与查重。
8. 已确认边界内的细化可以进入自动候选判断；全新受众、明显不同用途或站点目的变化必须由运营者确认。
9. 每轮最多 2 篇新文章和 2 篇旧文章，不足时不补位。运营者只需“要做、不再推荐、暂时跳过”三个决定。
10. 每轮用户可设置预算范围为 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20。预算是上限，缓存复用不扣真实请求，满足停止条件时不强行用完。

## 后果

优点：

- 消除“没有 GSC 候选就不能外部调研”的死循环。
- 保留旧 Plan 的发现能力，同时用主题身份、正文覆盖和决定历史抑制重复。
- 网站接近饱和时可以有秩序地深入分支，而不是重复搜索宽泛购买词。
- 用户可以从主题树指定方向，但不需要维护复杂状态。

代价与风险：

- 需要为研究运行增加种子类型、分支、扩展维度和历史记忆。
- 图谱的初始文章映射若只靠标题/标签会重现旧集群污染，必须保存置信度并抽查正文。
- 外部线索更多只扩大覆盖，不自动提高置信度；不得用调用次数替代证据质量。
- 本决定已在 0.5.0 实现；研究候选仍需经过当前重复与边界门槛，并由运营者确认后才进入新文章建议。

## 方法依据

- Google people-first 内容指南：https://developers.google.com/search/docs/fundamentals/creating-helpful-content
- Google scaled content abuse：https://developers.google.com/search/docs/essentials/spam-policies#scaled-content
- Google 链接最佳实践：https://developers.google.com/search/docs/crawling-indexing/links-crawlable
- Ahrefs 主题聚类：https://ahrefs.com/blog/topic-clusters/
- Ahrefs 关键词蚕食：https://ahrefs.com/blog/keyword-cannibalization/
- HBR Customer-Centered Innovation Map：https://hbr.org/2008/05/the-customer-centered-innovation-map
- Digital.gov 用户旅程方法：https://digital.gov/guides/research-collaboration/user-needs/journeys
- Baymard 兼容性与替代/配套产品研究：https://baymard.com/blog/ecommerce-compatibility-databases 和 https://baymard.com/blog/product-page-suggestions
