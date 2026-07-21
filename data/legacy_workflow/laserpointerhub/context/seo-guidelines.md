# Laserpointerhub SEO 规范

> v1

## 内容长度

- **目标 3000–5000 字**（深度型内容）
- 不追求字数本身，用信息密度而非废话填充
- 关键页面（pillar）可接近 5000，cluster 文章可控制在 3000–4000

## 核心 SEO 原则

### 1. EEAT（Experience, Expertise, Authoritativeness, Trustworthiness）

- **Experience**：引用真实测试数据、社区实测记录（Laser Pointer Forums）、NIST/FAA 报告
- **Expertise**：展示对激光二极管、光束物理、热管理、安全标准的深度理解
- **Authoritativeness**：每个核心论断至少引用一个权威外部来源（.gov、.edu、行业标准）
- **Trustworthiness**：不虚标参数、公开披露产品测试方法、功率数据可验证

### 2. 信息增量（Information Gain）

- 每篇文章必须包含 SERP 前 5 名没有覆盖的内容
- 可来自：社区实测数据、独有对比测试、行业黑幕揭露、物理原理深入解释
- 不能只是「改写竞品的内容」，必须提供竞品没有的维度

### 3. 模块化自包含段落（Self-Contained Paragraphs）

每个段落是一个独立的知识单元，结构为：

> **核心观点** → **数据/术语支撑** → **结论/实际意义**

规则：
- 每个段落可以脱离上下文独立理解
- 不依赖「如前所述」「接下来我们讨论」这类过渡
- 技术术语在段落内给上下文解释，不依赖上文首次定义
- 每个 H2/H3 区块是一个可独立抓取的知识切片

示例：
```
❌ 错误（依赖前文）:
"This is why they fail." （what is "they"? what is "this"?）

✅ 正确（自包含）:
"Cheap laser pointers fail because resistor-limited drivers cannot maintain
stable current as the battery voltage drops. The result: output power decays
30-50% within the first 60 seconds of use."
```

## 内容结构

- 每篇文章至少 3 个内部链接，至少 1 个指向产品页
- 外部链接优先使用 material-pack E 部分或 external-sources-library.md 中的来源
- 产品对比用表格，方便爬虫解析结构化数据

## 标题规则

- 主标题含主关键词 + 年份（适用时）
- 格式：「Primary Keyword: Secondary Context | Brand-Specific Angle」
- H2/H3 用问题式或陈述式，自然包含变体关键词

## 禁止

- 不堆砌关键词
- 不编造数据或引用
- 不写只有 SEO 价值、对人类读者无用的段落
- 不用 AI 生成的通用废话（"in today's fast-paced world" 之类）
