---
name: research
description: "SEO 关键词调研 + 素材包生成。三段式流程：生成搜索提示词 → 收集数据+AI分析 → 素材归档。"
compatibility: "opencode"
license: "MIT"
metadata:
  version: "1.0"
  last_updated: "2026-06-02"
---

# RESEARCH Skill — SEO 关键词调研 + 素材包生成

> **触发**：用户输入 `/research [topic] [website]` 或 `/research [topic]`
> **依赖**：`research_collector.py`（数据收集 + 素材库匹配 + 归档）
> **产出**：写作指令包（给 `/write` 用）+ 调研简报（审计归档用）

---

## 流程总览

```
/research [topic] [website]
│
├─ 段0（脚本）：生成搜索提示词
│  └─ python3 generate-prompt → 等用户粘贴搜索结果
│
├─ 段1（脚本）：数据收集
│  └─ python3 collect --search-file "xxx" → research-data-[slug]-[date].md
│
├─ 段2（AI）：分析 + 写素材包
│  └─ 读数据包 → 选关键词 → 分析竞品 → 定大纲 → 打分 → 确认素材
│  └─ 写：material-packs/[slug]-[date].md + research/brief-[slug]-[date].md
│
└─ 段3（脚本）：素材归档
   └─ python3 archive → 新素材入库 + 使用次数 +1
```

---

## ⛔ 硬规则（AI 必须遵守，不可跳过）

### 素材包 [search] 格式规范

段3归档脚本通过正则解析 A/C/E 段中的 `[search]` 条目入库。**格式不对 = 0 条入库 + exit 1。**

每个 `[search]` 条目必须用以下格式（`- ` 前缀 + 英文字段名 + `: ` 分隔）：

**A. 用户痛点**:
```
- **[search] Pain point title**
- Source: [URL label](https://real-url.com)
- Quote: "actual user words"
```

**C. 案例素材**:
```
- **[search] Case title**
- Source: [URL label](https://real-url.com)
- Summary: detailed story description
- Use in article: what point this illustrates
```

**E. 权威引用**:
```
- **[search] Citation title**
- Source: [URL label](https://real-url.com)
- Key finding: specific data point or statistic
- Type: .gov / .edu / industry standard / ...
```

**G. PAA 真问句**:
```
- **[search] PAA question text**
- Source: [where this question was found](URL or "search AI")
- Answer hint: brief answer context if available
```

> G 类只能放真实问句（Google PAA 框 / Reddit / Quora 原文），**不可用模板问句**填充。FAQ 段至少 1 个问句必须命中 G 类。

**注意事项**：
- 每行必须以 `- ` 开头（Markdown 无序列表格式）
- 字段名必须用英文（`Source:` `Quote:` `Summary:` `Key finding:` `Type:`），**不要写 `来源:` `引语:` `故事:`**
- URL 必须用 Markdown 链接格式 `[label](url)`

---

## 启动规则（文件系统判断，不靠上下文记忆）

首次启动或对话断开重连时，检查文件系统决定从哪步继续：

```
search-prompt-{slug}-*.md  ─?  不存在 → 从段0开始
search-results-{slug}-*.md ─?  不存在 → 提示用户粘贴搜索结果
search-results-{slug}-*.md ─?  存在   → 跳过段0，从段1开始
material-packs/{slug}-*.md ─?  存在且含 Part 3 → 跳过段2，从段3开始
material-packs/{slug}-*.md ─?  存在但缺 Part 3 → 续写段2（读取已有内容，从断点继续）
```

每篇文章都走：段0 → 等粘贴 → 段1 → 段2 → 段3。无分支，无跳过。

---

## 段0：生成搜索提示词（脚本）

### 执行

```bash
python3 data_sources/modules/research_collector.py generate-prompt \
  --website {website} --topic "{topic}"
```

### 输出

脚本生成 `{website}/research/search-prompt-[slug]-[date].md`。

读取文件，向用户展示提示词：

```
📋 搜索提示词已生成。

请复制到 Perplexity / ChatGPT / Kimi 搜索，把返回结果粘贴回来。
```

### 用户粘贴搜索结果后

1. 识别粘贴内容（含 Section 1~8 结构的即为搜索结果，旧结果缺 Section 8 也可解析，G 类标空）
2. 保存为 `{website}/research/search-results-[slug]-[date].md`
3. 自动续跑段1

---

## 段1：数据收集（脚本）

### 执行

```bash
python3 data_sources/modules/research_collector.py collect \
  --website {website} --topic "{topic}" \
  --search-file "{website}/research/search-results-[slug]-[date].md"
```

### 脚本做什么

1. ✅ 数据就绪检查（GSC/SERP/素材库各有什么缺什么）
2. ✅ 解析搜索结果文件（Section 1-8 → 结构化数据，Section 8 PAA 缺失不报错）
3. ✅ 读取 seo-data-manual.md（GSC 机会 + E1/E2 关键词）
4. ✅ 读取 published-index.json（已发布覆盖）
5. ✅ 蚕食预检（调用 cannibalization_checker.py）
6. ✅ 素材库标签匹配（A/C/E 按使用次数升序）
7. ✅ 预填素材包（A/C/E auto-filled，B/D/F/G/H 待填）
8. ✅ 输出结构化数据包

### 产出

`{website}/research/research-data-[slug]-[date].md`

---

## 段2：AI 分析（你做）

读取数据包后，按以下步骤执行分析。**所有分析结论必须引用数据包中的具体数据点。**

### Step 0: 主题上下文 + 默认关键词策略

**topic-context 继承**：脚本会读 `research/topic-context-{slug}.json`。
- 若 `source=plan`：intent/tier/信号来自 PLAN，**直接沿用，不要重判**。把 PLAN 的信号（痛点/缺口）写进 brief 理由段。**`guidance` 字段含 PLAN 的差异化指引，写 brief 时必须引用。**
- 若不存在（如 `/research` 直接触发、跳过 PLAN）：脚本自动用启发式生成并标 `source=heuristic`。此时 intent/tier 可信度较低，AI 可在分析中修正。

本系统面向无季节性波动的产品，采用全年均衡策略：
- 商业词和信息词均衡分布
- 核心商业词做 Pillar Page + 长尾信息词做 Cluster Content

### Step 0.5: 关键词优先级

从数据包的 E1/E2 关键词池中，按三级优先级选择：

1. **⭐⭐⭐ E1** — GSC 实测词（最高优先级）
2. **⭐⭐ E2** — 人工验证词（第二优先级）
3. **⭐ E3** — 六圈扩展词（长尾补充）

在简报中为每个关键词标注层级。

### Step 1: 关键词分析

从数据包提取：
- **主关键词**：目标词（⚠️ 系统无搜索量/KD API，**不要编造搜索量或 KD 数字**。用 GSC 展示量作为需求代理）
- **搜索意图**：从 SERP 验证（数据包 Section 1）。若 SERP 意图与 PLAN context 矛盾，以 SERP 为准但在简报中标注偏差（如 "PLAN 判定信息型，SERP 显示转化型 → 采用转化型"）
- **当前排名**：数据包 A section 有没有已排在前 20 的？

### Step 2: 竞品 SERP 分析

从数据包的搜索结果 Section 1 或手动分析：
- Top 5 内容类型和结构
- **Must-cover 话题**：所有前5都覆盖的
- **差异化机会**：前5中1-2篇覆盖的
- **独特切入点**：没人覆盖的

### Step 3: 客观性校验

硬规则：
1. SERP 意图与你计划写的内容类型匹配吗？不匹配就调整。
2. 每个"内容缺口"至少 2 个竞品确认？否则不成立。
3. 已经排名前 10 的关键词，机会和从零开始的不同。
4. 高量低竞优先；低量高竞跳过。

### Step 4: 内容规划

**页面层级判定**：

| 特征 | Pillar Page | Cluster Content | Product Roundup |
|------|-------------|-----------------|-----------------|
| 关键词 | 1-2词，高量 | 3+词，特定问题 | best/review/vs |
| 字数 | 3000-5000 | 1500-3000 | 2500-4000 |
| Mini-Stories | 2-3个 | 1-2个 | 2个 |

基于规划输出：
- 推荐 H2 大纲（必须覆盖的 + 独特角度）
- 目标字数
- 内链策略（从数据包 published 数据中选 3-4 相关文章 + 2-3 产品页）
- 元素据（Title 50-60字符, Description 150-160字符）
- FAQ 问句（≥3 个）：**至少 1 个问句命中 G 类（PAA 真问句）**，不能全是模板问句。G 类来源见数据包「预填素材包 > G. PAA 真问句」段（从搜索结果 Section 8 预填）；若 G 段为空，从 SERP/Google PAA/Reddit/Quora 现抓真实问句并标 `[search]`

### Step 5: 机会打分（脚本，不再 AI 主观）

由脚本确定性计算，**AI 只解释分数、不重新打分**：

```bash
python3 data_sources/modules/research_scorer.py --website {website} --slug {slug}
```

读 `research-data-{slug}-{date}.json`，输出 0–1 综合机会分 + 6 因子明细（demand 20% / current_rank 20% / competition 20% / intent_match 15% / content_gap 15% / link_fit 10%）。

- 缺 SERP 数据的因子会标 `⚠️低` 并按中性 0.5 处理，**不编数字**。粘贴了高质量搜索结果后重跑，这些因子才变成真实值。
- AI 把脚本分写进 brief，并解释「为什么这个分」+ 据此定切入角度。无真实搜索量/KD API，demand 用 GSC 展示量代理。

### Step 6: 审核并确认素材包

检查数据包中预填的素材包（A-H）：
- A/C/E 中的 `[library]` 条目：确认相关性
- B/D/F 中标记为待填的部分：补充你能从数据包中提取的信息
- G（PAA 真问句）：检查数据包 Section 8 预填内容；缺失时从 SERP/Reddit/Quora 手动补真实问句并标 `[search]`（不可用模板问句）
- H：标注需要手动填写

#### 素材包格式

见顶部「⛔ 硬规则」区块。

#### 填写优先级

根据意图决定填写优先级：
- **Commercial** → B实时价格 > C案例 > F竞品分析 > E权威引用
- **Informational** → A用户痛点 > D信息增量 > E权威引用 > G PAA 真问句

### 输出

保存两个文件：

**1. 写作指令包**（给 `/write` 用的唯一输入）：

`{website}/material-packs/[slug]-[date].md`

内容结构：
- Part 1: SEO 指令（关键词/意图/层级/字数/大纲/H2结构/内链/元素据）
- Part 2: 素材 A-H（AI 已确认/调整过的）
- Part 3: 质量门控检查项

**2. 调研简报**（审计归档用）：

`{website}/research/brief-[slug]-[date].md`

内容结构：
- 1. SEO Foundation（主关键词 + 次关键词 + 层级标注）
- 2. Competitive Landscape（竞品分析 + 缺口）
- 3. Recommended Outline（H2 大纲）
- 4. Supporting Elements（数据/故事/视觉建议）
- 5. Opportunity Score（6因子打分）
- 6. 客观性检查清单

---

## 段3：素材归档（脚本）

> ⚠️ **注意**：`/write register` 已内置归档步骤，会在清理素材包前自动调用 `research_collector.py archive`。
> 如果你是从 `/write` 流程走下来的，段3不需要手动执行。只有跳过 `/write` 直接调用归档时才需要手动跑。

### 执行

```bash
python3 data_sources/modules/research_collector.py archive \
  --website {website} \
  --pack "{website}/material-packs/[slug]-[date].md"
```

### 脚本做什么

1. ✅ 读最终素材包
2. ✅ 提取 `[search]` 标记的新条目（自动去除 `[search]` 标签后入库）
3. ✅ URL 去重（精确匹配 → 跳过已存在的）
4. ✅ 追加到三个素材库（pain-points / case-studies / external-sources）
5. ✅ 更新 `[library]` 条目的使用次数 +1
6. ✅ 格式验证：若有 `[search]` 标记但提取 0 条，输出警告 + exit 1

### 输出

归档报告：
```
📦 素材归档完成:
- 痛点: +X 条 (跳过重复 Y), 使用次数更新 Z 条
- 案例: +X 条 (跳过重复 Y), 使用次数更新 Z 条
- 引用: +X 条 (跳过重复 Y), 使用次数更新 Z 条
```

---

## 文件管理

| 文件 | 保存位置 | 用途 |
|------|----------|------|
| 搜索提示词 | `{website}/research/search-prompt-[slug]-[date].md` | 给用户搜索用 |
| 搜索结果 | `{website}/research/search-results-[slug]-[date].md` | 用户粘贴保存 |
| 数据包 | `{website}/research/research-data-[slug]-[date].md` | 给 AI 分析用 |
| 写作指令包 | `{website}/material-packs/[slug]-[date].md` | 给 `/write` 用 |
| 调研简报 | `{website}/research/brief-[slug]-[date].md` | 审计归档 |

---

## 下一步

调研完成后提示用户：

```
✅ 调研完成！

📁 写作指令包: {website}/material-packs/[slug]-[date].md
📁 调研简报: {website}/research/brief-[slug]-[date].md

🎯 下一步: /write [topic]

💡 写作指令包已经包含了 /write 需要的所有信息，无需再读调研简报。
```

---

## 注意事项

1. **不编造数据**：素材包中标记为 `[ ] manual` 的字段由用户填写，AI 不填假数据
2. **数据包是唯一输入源**：AI 分析时只读数据包，不额外读 seo-data-manual（脚本已提取）
3. **搜索结果解析容错**：搜索AI返回的格式可能不完美，脚本做 best-effort 解析，解析失败的 section 标记为 `[parse-failed]`
4. **素材包 [search] 和 [library] 标记**：段3归档只处理 `[search]` 条目（新素材入库），`[library]` 条目只更新使用次数
5. **蚕食风险处理**：RESEARCH 阶段的蚕食是**预检**（基于标签/token 重叠），WRITE 阶段的正文 TF-IDF 才是**终检**。相似度 ≥ 0.55 → 建议调整角度；0.45-0.55 → 需要差异化；< 0.45 → 安全