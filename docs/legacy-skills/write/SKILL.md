---
name: write
description: "SEO 文章写作。四段式流程：校验素材包 → AI写文章 → 脚本后处理（去水印+链接校验+打分+frontmatter） → 脚本注册归档。"
compatibility: "opencode"
license: "MIT"
metadata:
  version: "1.0"
  last_updated: "2026-06-03"
---

# WRITE Skill — SEO 文章写作

> **触发**：用户输入 `/write [topic] [website]` 或 `/write [topic]`
> **前置依赖**：`/research` 产出 material-pack（素材包必须在 material-packs/ 目录下）
> **依赖**：`write_collector.py`（素材包校验 + 链接后处理 + 注册归档）
> **产出**：`drafts/[slug]-[date].md`（最终版文章）

---

## 流程总览

```
/research 产出 material-pack
        ↓
    /write [topic] [website]
        │
段0 ── python3 write_collector.py validate
        │  校验素材包 + 加载 context → 校验报告
        │
段1 ── AI 写文章
        │  读素材包 + context → 写正文 → 自然嵌链接
        │  保存 drafts/[slug]-[date].md
        │
段2 ── python3 write_collector.py post-process
        │  scrub → 提取链接 → 验来源 → 验数量(blog推荐2-3上限4/prod≤3/ext≥2)
        │  验 SEO 字段(空→❌) → 生成frontmatter(仅链接字段) → scorer 打分
        │  有错 → 打回 AI 修 → 重跑段2(最多2轮)
        │  通过后加 --apply → 链接字段写回 draft
        │
段3 ── python3 write_collector.py register
        │  注册 internal-links-map → 读 published-index.json link_counts
        │  生成回溯链候选人 → AI 挑+写锚文本 → 归档素材到素材库 → 清理 material-pack
```

---

## 段0：校验素材包 + 加载 Context（脚本）

### 执行

```bash
python3 data_sources/modules/write_collector.py validate \
  --website {website} --topic "{topic}"
```

如果知道素材包路径：

```bash
python3 data_sources/modules/write_collector.py validate \
  --website {website} --topic "{topic}" \
  --pack "{website}/material-packs/[slug]-[date].md"
```

### 脚本做什么

1. 查找素材包文件
2. 验证必填项：主旨关键词、A 用户痛点（≥1条）、E 权威引用（≥1条）
3. 加载 6 个 context 文件：brand-voice、writing-examples、style-guide、seo-guidelines、target-keywords、internal-links-map
4. 加载 live_products_report（产品规格）
5. 检测网站域名（用于后续链接分类）
6. **读取 topic-context**（若 PLAN/RESEARCH 留有 `research/topic-context-{slug}.json`）：继承 intent/tier/信号，报告头部显示来源（🎯 PLAN / 🔧 启发式）。**不要再自己重新推断 tier**——用继承的

### 产出

校验报告（Markdown），包含素材包状态 + 所有 context 文件内容。

### 阻塞规则

素材包必填项缺失 → 停止，提示用户补齐后重试。

---

## 段1：AI 写文章

读校验报告中的 context 和素材包，从素材包的 A-H 类别提取所有事实数据（痛点、价格、案例、引用等）。

必须在素材包中找到每个事实断言的依据。如果素材包中缺少数据点，标注 `[data not in material pack]` 而不是编造。

### 文章结构（严格按顺序）

#### 1. Frontmatter
先写 frontmatter 基本信息。链接字段留空——段2 脚本在最后自动生成。

```yaml
---
Title: [H1 标题，含主关键词]
Slug: [URL slug]
Author: Marcus Chen
Summary: [2-3句摘要]
Tags: [3-5个逗号分隔标签]
SEO Title: [50-60字符]
SEO Description: [150-160字符]
SEO Keywords: [逗号分隔，主关键词在前]
---
```

#### 2. H1 标题
与 frontmatter 的 Title 一致。

#### 3. Introduction（150-250词）
1. **Direct Answer（必选）**：前 1-2 句必须直接回答搜索查询
2. **Hook（5 选 1）**：Provocative Question / Specific Scenario / Surprising Statistic / Bold Statement / Counterintuitive Claim
3. **APP 公式**：Agree（认同读者感受）→ Promise（告诉读者会学到什么）→ Preview（概述文章内容）
4. 主关键词出现在前 100 词

#### 4. Key Takeaways 块（Introduction 之后，H2 之前）
```
> **Key Takeaways**
> - [具体结论1，含数字/名称/结果]
> - [具体结论2]
> - [具体结论3-5]
```
3-5 条，每条是独立结论，不是目录。

#### 5. Quick Specs 块（仅商业型文章）
```
> ### Quick Specs: [产品名称]
> - **Wavelength**: [450nm blue / 520nm green]
> - **Output Power**: [规格]
> - **Battery**: [规格]
> - **Build**: [材质]
> - **Price**: [价格]
> - **Key Differentiator**: [核心差异]
```
5-7 个键值对，所有数值取自 live_products_report.md。

#### 6. 正文（H2/H3 层级）
- Pillar Page：2500-4000 词正文 | Cluster Content：1200-2500 词正文 | Product Roundup：2000-3500 词正文
- 4-7 个 H2，逻辑递进
- 每个段落自包含（可脱离上下文独立理解）
- 段落 2-4 句，关键结论独立成段
- 至少嵌入 1 个 YouTube 视频

#### 7. E-E-A-T 增强（融入正文，不单独成段）
- **Experience**：引用素材包 C/E 中的真实用户场景或权威测试数据。若素材包无实测场景，直接进入分析——不编造 "In our test"/"We found" 等第一人称信号
- **Expertise**：引用 1-2 个权威来源支撑技术论断
- **Authoritativeness**：如素材包 G 有东西 → 插入 `[Insert Calculator Placeholder]` 或 `[Insert PDF Checklist Link]`
- **Trustworthiness**：文末区域链接联系页面和退换政策。frontmatter 的 `Author` 字段（化名 Marcus Chen）会用于 Article schema 的 `author`，与 brand-voice.md 的 Author Persona 一致，强化 E-E-A-T 作者信号

#### 8. Soft-Sell 红线（严格遵守）
- 前 40% 文章不得硬推产品
- 产品推荐自然出现在读者已有购买意图之后

#### 9. 真实素材引用（替代 Mini-Stories）
- **不要编故事**。AI 生成的"Sara runs a small company"式故事千篇一律，降低可信度。
- 从素材包的 A/C/E 部分提取真实的用户场景、测试数据、竞品引用。
- 如果素材包有真实案例：直接引用，标注来源。
- 如果素材包没有真实场景：直接进入分析，不编故事，不编数据。

#### 10. Contextual CTAs（2-3 个，分布全文）
- **信息型文章**（Pillar / Cluster）：CTA 指向站内相关博客内链
- **商业型文章**（Product Roundup / 评测）：CTA 指向产品页链接
- 前 500 词内最多 1 个 soft CTA，且仅在上下文已出现购买相关关键词时；前 40% 篇幅禁止任何硬推产品的 CTA
- 中段、结尾各 1 个 CTA，全文合计 2-3 个
- 不用 "click here" / "read more" 锚文本
- **去模板化**：5 篇文章内不得复用同一 CTA 锚句模板，必须根据具体上下文写自然过渡句

#### 11. Conclusion（150-200 词）
- Recap 3-5 个关键要点
- 清晰的下一步动作
- CTA + 鼓励性收尾

#### 12. FAQ Section
- H2: "Frequently Asked Questions"
- 每个问题措辞得像真人会问的自然语言
- 答案从文章正文节选 2-3 句

#### 13. FAQ Schema JSON-LD
```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "[问题1 — 自然语言]",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "[答案1 — 2-3句，~150-300字符]"
      }
    }
  ]
}
</script>
```
3-4 个 Q&A 对。

#### 14. 链接嵌入
- 在正文中自然嵌入 `[锚文本](URL)`
- **博客内链**：~1000词/条，下限2上限8。3000词文章 ≥3条 ≤5条。
- **产品内链**：~1300词/条。3000词文章 ≥2条 ≤3条。
- **外链 ≥ 2 条**：~800词/条，下限2上限8。3000词文章 ≥4条 ≤6条。
- 不编造 URL。段2 脚本会验证来源和数量

### 写作风格

- 使用 brand-voice.md 的语气。对于 laserpointerhub：严谨专业、数据驱动、敢于揭露
- 遵循 style-guide.md 的格式规则（标题大小写、列表格式等）
- 遵循 seo-guidelines.md 的自包含段落规则
- 以 writing-examples.md 为风格参考

### 保存

```bash
# 保存到
{website}/drafts/[slug]-[date].md
```

---

## 段2：后处理（脚本）

### 执行

首次检查（不改文件）：

```bash
python3 data_sources/modules/write_collector.py post-process \
  --website {website} \
  --draft "{website}/drafts/[slug]-[date].md" \
  --pack "{website}/material-packs/[slug]-[date].md"
```

修复完成后，最后一次运行，加 `--apply` 覆写 frontmatter：

```bash
python3 data_sources/modules/write_collector.py post-process \
  --website {website} \
  --draft "{website}/drafts/[slug]-[date].md" \
  --pack "{website}/material-packs/[slug]-[date].md" \
  --apply
```

⚠️ **蚕食阻塞**：2 轮后蚕食仍 ≥0.55，如果你确认新文和已有文章角度不同（同站术语重叠导致的 TF-IDF 误报），加 `--force` 跳过：

```bash
... --apply --force
```

**不要偷偷加。用 opencode `question` 工具弹出选项让用户确认后再加 `--force`。**

### 脚本做什么

1. **Scrub**：去除 AI 水印
2. **提取链接**：从正文提取所有 `[text](URL)` 并分类
3. **验证来源**：内链验 internal-links-map、外链验 material-pack E
4. **检查限制**：博客内链 ~1000词/条 | 产品内链 ~1300词/条 | 外链 ~800词/条，上下限脚本自动算
5. **检查 SEO 字段**：SEO Title、SEO Description、SEO Keywords 为空 → ❌
6. **🔪 蚕食门控（新文 vs 已发布）**：`seo_common.cannibal_new_article`。最高相似度 **≥0.55 → 阻塞**，0.50–0.55 → 警告需差异化，<0.50 → 安全
7. **质量评分**：content_scorer ≥ 70 通过（scorer 现已正确读取 frontmatter 的 SEO 字段）
8. **🚦 总门控**：评分 ≥70 **且** 蚕食 <0.55 才可进段3。任一不过 → AI 修订正文 → 重跑段2（最多 2 轮）
9. **`--apply`**（最终通过后）：将链接字段追加到已有 frontmatter，不覆盖原有字段

### 如果后处理发现问题

1. 查看报告中的「链接问题」和评分
2. 修复链接（替换无效 URL、补充外链）、补充文章内容
3. 编辑 draft 文件后 → 重新运行段2
4. **最多 2 轮**。2 轮后：
   - 链接仍有问题 → 标出来，继续段3（链接问题不阻塞发布）
   - 评分仍 < 70 → **停止**，标出 `⚠️ 需人工审阅`，不继续段3

---

## 段3：注册 + 回溯链接（脚本）

### 执行

文章通过段2（评分 ≥ 70）后，运行：

```bash
python3 data_sources/modules/write_collector.py register \
  --website {website} \
  --draft "{website}/drafts/[slug]-[date].md" \
  --pack "{website}/material-packs/[slug]-[date].md" \
  --new-url "https://{domain}/blog/{slug}" \
  --title "{article title}" \
  --keyword "{primary keyword}"
```

### 脚本做什么

1. **注册**：将新文章追加到 internal-links-map.md
2. **回溯链接候选人**（从 `published-index.json` 读 `link_counts`）：
   - 按标签重叠度排序
   - 跳过 `link_counts.blog ≥ 4` 的旧文章
   - 跳过旧文章正文已包含新文章 URL 的
   - 高优先（重叠 ≥4，≤5篇）+ 中优先（重叠≥2，≤5篇）
   - 生成候选人列表（含 H2 建议 + 初始锚文本）
3. **AI 任务（脚本做不了的最后一公里）**：从候选中选 1-3 篇 → 逐个**读旧文章正文** → 输出回溯链接清单：
    1. 打开 `published/{slug}.md`，读旧文章
    2. 在建议的 H2 段落附近，找一个自然插入点
    3. 写一句话的上下文锚文本（用旧文章自己的术语自然过渡）
    4. 输出清单，格式：
       ```
       → 以下旧文章加回溯链接
       #N, Title
       URL: https://...
       锚文本: `[自然过渡句]({new_url})`
       插入位置: [H2段落名] 段落后
       ```
    - **不要直接编辑旧文章**。只输出清单，由人工审批后执行。
    - **上限**：每篇旧文章最多 1 条回溯链接
    - **跳过**：已包含新 URL 的、饱和度已满的（脚本已标记）
4. **清理**：删除素材包文件

---

## 段1b：预检（脚本，段1 写完立即跑）

AI 写完 draft 后，**必须先跑这个脚本**——它检查 AI 最容易漏的机械项。脚本报错就修 draft，修完重跑，都绿了再进段2。

```bash
python3 data_sources/modules/write_pre_check.py \
  --draft "{website}/drafts/[slug]-[date].md" \
  --tier "Pillar Page|Cluster Content|Product Roundup" \
  --keywords "主关键词,次关键词"
```

检查 15 项：字数、关键词位置、Meta 长度、Key Takeaways、FAQ、CTA、段落长度、EEAT信号、层级跳跃。

**脚本红了就修 draft，不要找理由跳过。** 全部 ✅ 后进段2。

---

## 核心规则（8 条，其他全由脚本检查）

这 8 条是脚本无法判断、必须 AI 把握的：

1. **素材包为唯一真实来源**：所有事实声明必须来自材料包 A-H。数据缺失时标注 `[data not in material pack]`，不编造。
2. **前 40% 不得硬推产品**：产品推荐自然出现在读者已有购买意图之后。
3. **直接回答搜索意图**：前 1-2 句回答用户想知道的。不绕开头。
4. **自包含段落**：每段 2-4 句，脱离上下文也能理解。
5. **不用 "click here"/"read more" 锚文本**：链接锚文字必须描述链接目标。
6. **不编数据**：材料包没有的数字就写材料包没有，不要拍脑补。
7. **E-E-A-T 经验信号**：素材包 C/E 有真实测试/观察数据时引用（≥2 处）。素材包无实测场景时不强行编造。
8. **主关键词在 H1、前 100 词、2+ 个 H2 中**。

---

## 文件管理

| 文件 | 路径 | 说明 |
|------|------|------|
| 素材包 | `{website}/material-packs/` | 段3 清理 |
| 草稿 | `{website}/drafts/[slug]-[date].md` | 最终文章 |
| internal-links-map | `{website}/context/internal-links-map.md` | 段3 更新 |

---

## 注意事项

1. **材料包为唯一真实来源**：所有事实声明必须来自材料包。数据缺失时标注 `[data not in material pack]` 而不是编造
2. **链接由脚本验证**：AI 可以自由嵌入链接，段2 验证。不在验证源中的 URL 会被标记
3. **Frontmatter 自动生成**：AI 只写 Title/Slug/Summary/Tags/SEO 字段。链接字段和字数由段2 生成
4. **回溯链接由脚本生成**：AI 不需要考虑回溯链接，段3 处理
5. **评分门控**：评分 < 70 → 不进入段3，必须修订