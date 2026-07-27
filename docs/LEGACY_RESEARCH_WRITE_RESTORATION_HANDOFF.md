# 旧 Research + Write Skill 1:1 复原交接方案

状态：运营者已确认方案，尚未实施  
确认日期：2026-07-19（Asia/Shanghai）  
适用范围：运营者在“文章建议”接受一个新文章主题之后的专项 `research + write` 制作链  
不在范围：旧 `plan` Skill 的重新实现、旧文章更新工作流、对旧流程的优化或方法重写

## 1. 运营者的明确要求

本任务是**复原**，不是改进、删减、重构或重新设计。

后续 AI 或开发者必须按以下含义理解“复原”：

- 旧 `research`、`write` Skill 是 AI 行为的原始规范。
- 旧 Python 脚本是确定性行为和副作用的原始规范。
- 旧站点 Context、素材库、产品报告和发布索引是站点输入规范。
- 旧文件名、目录、阶段顺序、暂停/续跑条件、链接装配、评分、注册、归档和清理行为都属于复原范围。
- 当前项目的简化素材提示词、当前 `material_workflow.py`、当前 `content_production.py` 不能被当作复原基线。
- 不得用“更合理”“更安全”“更现代”作为理由，静默替换旧步骤、阈值、产物或副作用。
- 如果旧行为与本项目不可破坏原则发生真实冲突，必须暂停并把冲突逐项交给运营者决定；不得自行删除旧步骤，也不得暗中启用违规行为。

最终判定标准不是“能生成一篇文章”，而是相同主题、相同搜索结果和相同站点输入能够重新走完旧工作流，并产生同类阶段、文件、链接、报告和副作用。

## 2. 唯一事实源与优先级

### 2.1 旧 Skill

1. `/home/laoma/seo-workflow/.opencode/skills/research/SKILL.md`
2. `/home/laoma/seo-workflow/.opencode/skills/write/SKILL.md`

### 2.2 旧主脚本

1. `/home/laoma/seo-workflow/data_sources/modules/research_collector.py`
2. `/home/laoma/seo-workflow/data_sources/modules/research_scorer.py`
3. `/home/laoma/seo-workflow/data_sources/modules/write_collector.py`
4. `/home/laoma/seo-workflow/data_sources/modules/write_pre_check.py`

### 2.3 必须一起复制和锁定的脚本依赖

至少包括以下旧模块；实施前必须再按 import、动态加载和 subprocess 做完整依赖闭包审计：

- `seo_common.py`
- `seo_config.py`
- `content_scrubber.py`
- `content_scorer.py`
- `plan_feedback.py`
- `research_collector.py`（由 `write_collector.py` 动态加载并负责素材归档）

不能只复制四个入口文件后重新实现缺失依赖。

### 2.4 站点 Context 与状态输入

必须原样纳入复原运行环境：

- `/home/laoma/seo-workflow/laserpointerhub/context/brand-voice.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/writing-examples.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/style-guide.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/seo-guidelines.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/target-keywords.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/internal-links-map.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/seo-data-manual.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/pain-points-library.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/case-studies-library.md`
- `/home/laoma/seo-workflow/laserpointerhub/context/external-sources-library.md`
- `/home/laoma/seo-workflow/laserpointerhub/products/live_products_report.md`
- `/home/laoma/seo-workflow/laserpointerhub/published/published-index.json`
- 对应的 `published/*.md` 正文和 `research/topic-context-*.json`

旧项目路径只读。复原实现必须先复制到新项目自己的兼容工作区，不得直接修改 `/home/laoma/seo-workflow`。

### 2.5 冲突时的解释顺序

旧资料内部如果存在描述差异，按以下顺序处理：

1. 旧 Python 脚本的实际执行结果。
2. 旧 Skill 的 AI 指令与交互规则。
3. 旧 Context 的站点约束。
4. 历史真实产物的结构和副作用。
5. 当前项目文档只能解释接入位置，不能改写旧行为。

例如：旧 `write` Skill 对回溯链接数量有概括描述，但当前旧 `write_collector.py` 实际排序后只取 `top 2`；1:1 复原必须先遵循脚本的实际行为。

## 3. 接入边界

当前五步主流程保留到“文章建议”为止：

```text
数据导入 → 主题调研 → 文章建议 → 接受新文章
                                      ↓
                         旧 Research → 旧 Write
                                      ↓
                           草稿、链接、注册结果
```

接受新文章只负责建立兼容任务和传入主题，不得直接调用当前文章生成链。

以下当前行为不属于复原链：

- 当前只要求 A/E/G 的简化搜索提示词。
- 当前按已存 URL 数量判断“素材够用”。
- 当前 `现有素材够用，开始写作` 的直达分支。
- 当前三次 AI 加自动多轮返工的 `content_skill_*` 流程。
- 当前自行构造的 material preview、outline 或 link plan。

旧 Skill 的启动规则明确要求每篇新文章走完整 `research → write`；只有文件系统中已经存在对应旧产物时，才按原断点规则续跑。

## 4. 兼容工作区

第一版复原不应把旧文件协议立即翻译成新数据库模型。应在当前项目内建立一个与旧项目同构的站点工作区，例如：

```text
data/legacy_workflow/laserpointerhub/
├─ context/
├─ products/
├─ published/
├─ raw/
├─ research/
├─ material-packs/
└─ drafts/
```

要求：

- 从旧项目复制初始 Context、素材库、产品报告、发布索引、历史正文和所需原始输入。
- 记录复制来源、时间和 SHA-256。
- 原始导入副本保持不可变；旧脚本正常会变化的库、map、feedback 和工作产物在兼容工作区内变化。
- 网页数据库只保存当前 action 与兼容工作区、topic、slug、阶段和产物路径的映射，不重写旧产物内容。
- 所有旧命令都以兼容工作区的项目根目录执行，保持旧相对路径假设。

## 5. Research 逐阶段复原

### 5.1 R0：启动与断点判断

旧 Skill 通过文件系统决定从哪里继续：

```text
没有 search-prompt       → 生成搜索提示词
有 prompt、没有 results  → 等待运营者粘贴
有 search-results        → 从 collect 继续
material pack 有 Part 3  → Research 完成，进入 Write/Archive
material pack 缺 Part 3  → 读取已有内容，继续 AI 分析
```

不得用聊天记忆、数据库推测或当前 action step 覆盖这套判断。

### 5.2 R1：生成完整搜索提示词

原命令：

```bash
python3 data_sources/modules/research_collector.py generate-prompt \
  --website {website} \
  --topic "{topic}"
```

原产物：

```text
{website}/research/search-prompt-{slug}-{date}.md
```

要求：

- 使用旧 `generate-prompt` 的完整提示词和 Section 1–8 协议。
- 页面只负责完整展示和复制。
- 等待运营者粘贴搜索结果；此处不允许“直接写作”。

### 5.3 R2：保存搜索结果

运营者粘贴的原始结果保存为：

```text
{website}/research/search-results-{slug}-{date}.md
```

要求：

- 原文保存，不先由当前系统整理或摘要。
- 保留旧解析容错：旧结果缺 Section 8 时 G 类可为空，不把整次输入判死。
- 保存成功后自动进入 `collect`。

### 5.4 R3：运行旧数据收集

原命令：

```bash
python3 data_sources/modules/research_collector.py collect \
  --website {website} \
  --topic "{topic}" \
  --search-file "{website}/research/search-results-{slug}-{date}.md"
```

必须保留旧脚本的完整行为：

1. 数据就绪检查。
2. 解析搜索结果 Section 1–8。
3. 读取 `seo-data-manual.md` 中的 GSC 机会和 E1/E2 关键词。
4. 读取 `published-index.json`。
5. 执行旧蚕食预检。
6. 按标签和使用次数匹配素材库。
7. 预填 A/C/E，并保留 B/D/F/G/H 待 AI 处理的旧协议。
8. 输出旧结构化数据包和 JSON sidecar。

原产物至少包括：

```text
{website}/research/research-data-{slug}-{date}.md
{website}/research/research-data-{slug}-{date}.json
```

### 5.5 R4：运行旧 Research AI 分析

AI 必须读取旧数据包，并按旧 Skill Step 0–6 执行：

1. 继承 `topic-context`；`source=plan` 时沿用 intent、tier、信号和 guidance。
2. 按 E1、E2、E3 三级选择关键词。
3. 分析主关键词、SERP 意图和当前排名，不编搜索量或 KD。
4. 分析 Top 5 的内容类型、must-cover、差异化机会和独特切入点。
5. 执行旧客观性检查。
6. 按旧文章类型和旧规格生成大纲、FAQ、Meta 与链接策略。
7. 审核并补齐旧 A–H material pack。

不得把这一步替换成当前项目的 `MATERIAL_PROMPT`、`SKILL_PLAN_PROMPT` 或当前素材分类逻辑。

### 5.6 R5：运行旧确定性评分

原命令：

```bash
python3 data_sources/modules/research_scorer.py \
  --website {website} \
  --slug {slug}
```

保留旧 0–1 总分及六因子：

- demand：20%
- current_rank：20%
- competition：20%
- intent_match：15%
- content_gap：15%
- link_fit：10%

缺数据按旧脚本标记和处理中性分；AI 只能解释，不能重算。

### 5.7 R6：生成旧 Research 产物

必须产生：

```text
{website}/material-packs/{slug}-{date}.md
{website}/research/brief-{slug}-{date}.md
{website}/research/research-score-{slug}-{date}.md
```

material pack 必须保持：

- Part 1：SEO 指令。
- Part 2：A–H 素材。
- Part 3：质量门控。
- A/C/E/G 中 `[search]`、`[library]` 的旧格式和英文字段名。

brief 必须保持六个旧章节：

1. SEO Foundation
2. Competitive Landscape
3. Recommended Outline
4. Supporting Elements
5. Opportunity Score
6. 客观性检查清单

完成后按旧提示进入 `/write [topic]`；不增加新的 material-pack 审批模型。

### 5.8 R7：旧 Archive 行为

仅在跳过 Write、直接归档时运行：

```bash
python3 data_sources/modules/research_collector.py archive \
  --website {website} \
  --pack "{website}/material-packs/{slug}-{date}.md"
```

保留：

- 从 A/C/E 解析 `[search]`。
- URL 精确去重。
- 追加 pain-points、case-studies、external-sources 三个素材库。
- 更新 `[library]` 使用次数。
- 存在 `[search]` 但提取 0 条时按旧脚本失败。

正常 Research → Write 链不在这里提前归档；旧 Write `register` 会执行归档。

## 6. Write 逐阶段复原

### 6.1 W0：校验 material pack 与加载 Context

原命令：

```bash
python3 data_sources/modules/write_collector.py validate \
  --website {website} \
  --topic "{topic}" \
  --pack "{website}/material-packs/{slug}-{date}.md"
```

必须保留：

- 查找 material pack。
- 校验主关键词。
- 校验 A 用户痛点至少 1 条。
- 校验 E 权威引用至少 1 条。
- 加载六份 Context。
- 加载 `live_products_report.md`。
- 检测网站域名。
- 读取并继承 `topic-context`。
- 生成旧校验报告以及原链接候选段落。

校验失败必须停在 W0。

### 6.2 W1：按旧 Write Skill 生成草稿

AI 读取 W0 校验报告、material pack 和 Context，按旧 Skill 原样生成：

1. Frontmatter 基础字段。
2. H1。
3. 150–250 词 Introduction，包含 Direct Answer、五选一 Hook 和 APP。
4. 3–5 条 Key Takeaways。
5. 商业文章的 Quick Specs。
6. 按旧 Pillar、Cluster、Product Roundup 规格生成正文和 4–7 个 H2。
7. 至少一个 YouTube 视频。
8. 旧 E-E-A-T 增强步骤。
9. 前 40% Soft-Sell 红线。
10. 真实素材引用。
11. 2–3 个 Contextual CTA。
12. 150–200 词 Conclusion。
13. FAQ。
14. FAQPage JSON-LD。
15. 旧链接密度和关键词位置要求。

原产物：

```text
{website}/drafts/{slug}-{date}.md
```

不得用当前三阶段 `content_skill_plan/draft/edit` 代替。

### 6.3 W1b：运行旧机械预检

原命令：

```bash
python3 data_sources/modules/write_pre_check.py \
  --draft "{website}/drafts/{slug}-{date}.md" \
  --tier "Pillar Page|Cluster Content|Product Roundup" \
  --keywords "主关键词,次关键词"
```

恢复旧脚本的 15 项检查。出现红项时修改 draft 后重跑；全部通过才进入 post-process。

### 6.4 W2：运行旧 Post-process

首次检查不改文件：

```bash
python3 data_sources/modules/write_collector.py post-process \
  --website {website} \
  --draft "{website}/drafts/{slug}-{date}.md" \
  --pack "{website}/material-packs/{slug}-{date}.md"
```

必须恢复：

1. `content_scrubber.py`。
2. 从正文提取并分类博客内链、产品链接和外链。
3. 对照 `internal-links-map.md` 与 material pack E 验证来源。
4. 按旧脚本计算动态链接上下限。
5. 检查 SEO Title、Description、Keywords。
6. 执行旧新文蚕食门控。
7. 执行 `content_scorer.py`，总分至少 70。
8. 最多两轮 AI 修订。
9. 保留旧链接问题、评分失败和人工审阅分支。

通过后原样执行：

```bash
python3 data_sources/modules/write_collector.py post-process \
  --website {website} \
  --draft "{website}/drafts/{slug}-{date}.md" \
  --pack "{website}/material-packs/{slug}-{date}.md" \
  --apply
```

旧 `--force` 分支也属于复原范围，只能在旧 Skill 要求的明确人工确认后使用，不能自动添加。

### 6.5 W3：运行旧 Register

原命令：

```bash
python3 data_sources/modules/write_collector.py register \
  --website {website} \
  --draft "{website}/drafts/{slug}-{date}.md" \
  --pack "{website}/material-packs/{slug}-{date}.md" \
  --new-url "https://{domain}/blog/{slug}" \
  --title "{article title}" \
  --keyword "{primary keyword}"
```

必须保留脚本实际副作用：

1. 把新文章登记到 `internal-links-map.md`。
2. 从 `published-index.json` 读取 `link_counts`；无数据时回退解析 map。
3. 读取旧文章正文并检查当前真实博客内链数量。
4. 跳过已包含新 URL 或已达到动态上限的文章。
5. 按标签重叠和当前链接数排序。
6. 按当前旧脚本实际行为取前 2 个回溯候选。
7. 把“回溯链接候选”段追加到 draft，供 AI 读取旧文后写清单；不直接编辑旧文。
8. 调用 `research_collector.archive_materials` 归档素材。
9. 按旧脚本实际行为清理 material pack；当前脚本即使归档异常也会继续清理。
10. 调用 `plan_feedback.py accept` 回流 PLAN 反馈。

上述第 8–10 项即使涉及 Research 或 Plan，也是旧 Write `register` 的真实副作用，不能因本任务只命名为 Research + Write 而静默删除。

## 7. 内链、产品链接和外链的原始装配链

本节是验收重点。不得新增 `link-plan.json`，不得把装配改成当前系统的新模型。

### 7.1 Research 中的链接准备

旧 Research AI 内容规划负责：

- 从数据包 published 数据选择 3–4 篇相关文章。
- 选择 2–3 个产品页。
- 把内链策略写进 material pack Part 1。
- 把可用权威来源写进 material pack E。
- FAQ 至少命中一个 G 类真实问句。

### 7.2 Validate 中的链接候选

旧 `write_collector.py validate` 负责：

- 从 `internal-links-map.md` 读取站内候选。
- 结合 `published-index.json` 标签和当前 topic 计算匹配。
- 排除 self slug。
- 把博客候选、产品候选和外部引用信息写进校验报告。

AI 直接从该校验报告和 material pack 读取链接，不增加中间链接审批文件。

### 7.3 Draft 中的链接嵌入

旧 Write Skill 要求 AI 在正文中直接写 Markdown 链接，并遵守：

- 博客内链约每 1000 词 1 条。
- 产品链接约每 1300 词 1 条。
- 外链约每 800 词 1 条。
- 3000 词文章的原数量提示继续保留。
- 不编造 URL。
- 不使用 `click here`、`read more` 等泛锚文本。
- 产品推荐遵守旧 Soft-Sell 和前 40% 红线。

### 7.4 Post-process 中的实际链接门槛

以旧脚本为准，当前实际算法是：

```text
blog_floor = max(2, min(8, round(body_words / LINK_RATIO_BLOG)))
blog_ceil  = blog_floor + 2

prod_floor = max(1, min(4, round(body_words / LINK_RATIO_PRODUCT)))
prod_ceil  = prod_floor + 1

ext_floor  = max(2, min(8, round(body_words / LINK_RATIO_EXTERNAL)))
ext_ceil   = ext_floor + 2
```

实际判定细节也必须保留：

- 博客内链为 0 时不会触发 `too_few_blog_links`；大于 0 但低于 floor 才报错。
- 产品链接为 0 时不会触发 `too_few_product_links`；大于 0 但低于 floor 才报错。
- 外链低于 floor 必定报错。
- 外链只能在 material pack E 的来源中通过验证。
- `--apply` 将最终链接字段写回旧 frontmatter。

不得按当前文档中的“软目标”重写这些实际门槛。

### 7.5 Register 中的回溯内链

旧脚本当前实际行为：

- 读取 `published-index.json` 的 `link_counts` 和 tags。
- 读取 `published/{slug}.md` 计算真实博客链接数。
- 动态上限与 Write 博客链接算法一致并再加 2。
- 关键词/标签重叠少于 2 的文章不进入候选。
- 排序依据为重叠度降序、当前链接数升序。
- 当前脚本只取前 2 个候选。
- 候选包含旧文章 URL、当前内链数、可选 H2 和初始锚文本。
- AI 必须读取旧文章正文后输出自然回溯链接清单。
- 不直接编辑旧文章。

## 8. 网页适配器的职责

网页层只允许做以下事情：

- 将当前 accepted 新文章 action 映射到 `{website}` 和 `{topic}`。
- 初始化或定位兼容工作区。
- 调用旧命令或旧 AI 阶段。
- 展示旧命令 stdout、stderr、退出码和产物。
- 接收并原样保存运营者粘贴的 search results。
- 按旧文件存在性恢复阶段。
- 展示 draft、post-process 报告、register 报告和回溯候选。

网页层不得：

- 重写旧 prompt。
- 把旧 Markdown 产物转换后再作为下一阶段的事实源。
- 用当前数据库素材数量跳过 Research。
- 自动补 URL、素材、作者、案例或评分。
- 自动调用 `--force`。
- 把当前内容质量检查混入旧 post-process。

## 9. 实施工作单元

### 单元 A：冻结与清单

- 为两个 Skill、所有脚本依赖、Context 和历史样例生成清单与 SHA-256。
- 记录旧 Python 版本和可导入依赖。
- 记录脚本动态加载、subprocess 和文件副作用。
- 不修改旧源文件。

完成标准：依赖闭包完整，四个旧 CLI 的 `--help` 和模块加载可在隔离副本运行。

### 单元 B：建立同构兼容工作区

- 在当前项目 `data/legacy_workflow/` 建立站点镜像。
- 复制 Context、素材库、产品报告、发布索引、正文和必要原始输入。
- 保留旧目录名和文件名协议。
- 建立 action → legacy workspace 映射。

完成标准：旧脚本无需修改核心逻辑即可读取全部相对路径。

### 单元 C：命令行复原 Research

- 完整运行 `generate-prompt`。
- 用固定历史 search results 运行 `collect`。
- 按旧 Skill 完成 AI 分析。
- 运行 `research_scorer.py`。
- 生成 material pack、brief 和 score。
- 测试断点续跑与 archive。

完成标准：未接网页前，命令行已产生旧格式全部 Research 产物。

### 单元 D：命令行复原 Write

- 运行 validate。
- 按旧 Skill 生成 draft。
- 运行 pre-check。
- 运行 post-process、修订和 `--apply`。
- 运行 register。
- 核对链接字段、map、回溯候选、素材归档、material pack 清理和 PLAN feedback。

完成标准：命令行从 material pack 到 register 全链通过，所有副作用可核对。

### 单元 E：接入网页

- 新文章接受后进入旧 Research 起点。
- 页面按旧文件断点显示下一步。
- 页面允许生成 prompt、粘贴 results、运行 Research、进入 Write、查看报告。
- 当前简化生成路由不得处理该兼容任务。

完成标准：网页只包装已通过的旧命令行链，网页与命令行产物一致。

### 单元 F：真实复验

- 先用一个已有完整历史产物的主题做黄金回放。
- 再用一个真实新主题从 R0 跑到 W3。
- 人工核对 prompt、research data、material pack、brief、draft、链接、score、register 和回溯候选。

完成标准：运营者确认工作体验和产物结构与旧 Skill 一致。

## 10. 黄金样例与对照方法

至少选择三类历史样例：

- Cluster Content：例如 light painting 或 power testing。
- Pillar Page：选择一个旧完整指南。
- Product Roundup：例如 best laser pointer 或 astronomy roundup。

每个样例固定：

- topic
- topic-context
- search-prompt
- search-results
- research-data md/json
- research-score
- brief
- draft
- 对应 Context 快照

对照分两类：

### 确定性部分

脚本输出、文件命名、解析结果、评分、链接分类、动态上下限、register 候选和文件副作用应逐字段或逐行对照；能字节一致的必须字节一致。

### AI 部分

模型输出不要求逐字一致，但必须满足：

- 接收相同旧 prompt 结构。
- 输入相同旧数据包和 Context。
- 输出相同章节、字段和 material-pack 协议。
- 不缺失旧 Skill 要求的组件。
- 后续旧脚本能够正常解析和通过相同门控。

## 11. 必须建立的回归测试

1. `generate-prompt` 文件名与 Section 结构。
2. search-results 缺 Section 8 的旧容错。
3. collect 的 research-data md/json 结构。
4. E1/E2/E3、topic-context 与 PLAN guidance 继承。
5. research scorer 六因子和缺失数据处理。
6. `[search]`、`[library]` 归档和格式失败。
7. Write validate 的必填素材和六份 Context。
8. pre-check 15 项。
9. 博客、产品、外链的分类和实际动态上下限。
10. 未登记内链和不在 material pack E 的外链。
11. content score 与 cannibalization 门。
12. 两轮修订和人工 `--force` 分支。
13. `--apply` 的 frontmatter 链接字段。
14. register 更新 internal-links-map。
15. register 当前 top 2 回溯候选。
16. register 归档、清理 material pack 和 PLAN feedback。
17. 文件存在性断点续跑。
18. 重复提交同一 search-results 不意外启动另一主题。
19. 网页产物与命令行产物一致。
20. 打开页面本身不启动任何旧命令或 AI。

## 12. 复原验收清单

只有全部满足，才可称为“旧 Research + Write 已复原”：

- [ ] 新文章接受后首先进入旧 Research，而不是当前直接写作。
- [ ] 使用旧完整 search prompt。
- [ ] 原样保存 search results。
- [ ] 旧 collect、scorer 实际运行。
- [ ] material pack Part 1–3 可被旧 archive 和 Write 解析。
- [ ] brief 六章节齐全。
- [ ] Write validate 加载六份 Context、产品报告和 topic-context。
- [ ] 草稿包含旧 Skill 要求的全部组件。
- [ ] 旧 pre-check 实际运行。
- [ ] 旧 post-process、scrubber、scorer、cannibalization 实际运行。
- [ ] 内链、产品链接、外链按旧装配链生成并校验。
- [ ] `--apply` 写回旧 frontmatter。
- [ ] register 更新 internal-links-map。
- [ ] register 生成当前脚本实际 top 2 回溯候选。
- [ ] register 执行素材归档、material pack 清理和 PLAN feedback。
- [ ] 文件系统断点续跑行为与旧 Skill 一致。
- [ ] 运营者完成一篇真实文章人工复验。

## 13. 明确禁止的偏航

以下行为都不能称为复原：

- 把旧 Skill 总结成一个新的大提示词。
- 只模仿旧页面结构，不运行旧脚本。
- 把 A–H 改成当前数据库字段后丢弃旧 Markdown 协议。
- 新增 `link-plan.json` 后替代旧 validate/draft/post-process 装配链。
- 把旧硬门改成软目标，或把旧脚本软行为改成硬门。
- 删除 Quick Specs、YouTube、CTA、FAQ Schema、Scrub、评分、`--force`、register 或 PLAN feedback。
- 修改旧链接密度、修订轮数、评分阈值或蚕食阈值。
- 用当前已存来源直接跳过旧搜索提示词和 search-results 阶段。
- 因为当前实现已有一部分相似功能，就混用当前和旧流程的中间产物。

任何优化都必须在本交接文档的复原验收完成后另建任务、另写方案，不得混入本任务。

## 14. 下一位 AI 的第一步

1. 重新阅读项目根 `AGENTS.md`、`HANDOFF.md`、项目上下文和方法治理。
2. 完整读取本文件列出的两个旧 Skill 和四个旧主脚本。
3. 审计完整依赖闭包与所有文件副作用，不能委托其他代理代读 Skill 或关键脚本。
4. 先提交“旧资产清单 + SHA-256 + 黄金样例选择”，不要先改当前内容生成代码。
5. 得到运营者确认后，按单元 A → F 顺序实施。
