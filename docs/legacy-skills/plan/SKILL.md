---
name: plan
description: "SEO 选题推荐。脚本收集情报 → 脚本确定性打分（双管线）→ AI 包装角度。输出下一篇写什么。"
compatibility: "opencode"
license: "MIT"
metadata:
  version: "2.0"
  last_updated: "2026-06-03"
---

# PLAN Skill — SEO 选题推荐 v2

> **触发**：用户输入 `/plan` 或 `/plan laserpointerhub`
> **依赖**：`plan_collector.py`（收集情报）→ `plan_scorer.py`（确定性打分）→ `plan_feedback.py`（闭环）

## 设计原则（v2 重构）

1. **职责分离**：脚本做确定性的「收集 + 计数 + 匹配 + 打分 + 排序」，AI 只做「把 Top-N 候选包装成有信息增量的文章角度 + 品牌判断」。AI 不做算术。
2. **双管线**：
   - 🔧 **优化管线**（GSC 驱动）：已有页有展示但 CTR 低/在第2页 → 改标题/Meta，**不写新文**。
   - ✍️ **发现管线**（缺口驱动）：写新文。需求代理 + 集群缺口 + 痛点热度 + 商业价值打分。
3. **打破 GSC 路径依赖**：GSC 从 40% 主权重降为「需求代理」之一；已饱和的商业查询（best/under $X 且集群已有 ≥5 篇）自动路由到优化管线，不再污染发现管线。

---

## 执行步骤

### Step 1：收集情报（脚本）

```bash
python3 data_sources/modules/plan_collector.py --website {website} --tavily-key {TAVILY_KEY}
```

产出：
- `{website}/research/plan-brief-{date}.md`（人读简报）
- `{website}/research/plan-brief-{date}.json`（机读，喂给 scorer）
- `{website}/research/cannibalization-report.txt`（完整蚕食报告）

Tavily key 从 `{website}/data/plan-config.json` 或参数读取，未配置则跳过外部信号。

### Step 2：确定性打分（脚本）

```bash
python3 data_sources/modules/plan_scorer.py --website {website}
```

产出 `{website}/research/plan-candidates-{date}.md`，含两张表：优化管线 Top 6 + 发现管线 Top 8，每行带分项信号（需求/缺口/痛点/价值/蚕食风险）。

发现管线评分公式（权重写死在脚本，可回测）：

```
总分 = 需求代理×0.30 + 集群缺口×0.25 + 痛点热度×0.25 + 商业价值×0.20
命中历史拒绝模式 → 总分 ×0.20
```

- **需求代理**：GSC 展示量（log 归一，最强）> Suggest 验证（0.45）> 弱先验（0.2）。无付费搜索量 API。
- **集群缺口**：1 − 该集群已覆盖文章数 / 最大集群覆盖数。
- **痛点热度**：关联痛点标签条数 / 最大痛点条数。
- **商业价值**：转化型(best/buy/under $)=1.0 ｜ 信息型(how/what/safe)=0.7 ｜ 混合=0.85。

### Step 3：AI 包装（这一步才需要 AI）

读 `plan-candidates-{date}.md` + `plan-brief-{date}.md`，对发现管线 Top 候选：

0. **正文级蚕食复核（必做）**：选定首推方向后，快速扫一眼已发布文章正文，
   搜索候选的核心 token（如 buying guide / comparison / TCO）。
   tag 级检查（已由 scorer 完成）**不可替代正文级检查**——tag 是粗筛，
   full-body 才有最终发言权。正文级 TF-IDF 精筛在 WRITE 段2 补救，但 PLAN
   阶段发现提前调整角度更省事。

1. **包装成完整文章主题**：把种子（如 `thermal laser pointer problems`）包装成有信息增量的标题与角度（如 "Laser Pointer Duty Cycle Guide: Why Your Laser Dims, Overheats, and Dies"）。结合外部信号（Reddit/论坛在问什么）找差异化切入点。
2. **品牌判断**：剔除违反品牌定位/不适合的候选（脚本不懂品牌，这是 AI 的活）。
3. **蚕食复核**：脚本已给出轻量 token 蚕食风险；🔴 高风险的候选需找差异化角度或换题。
  - topic-context 的 `guidance` 字段已由 scorer 自动写入基线（如 "body-probe flagged overlap with handheld-inkjet-printer-guide"）。
  - AI 如有更精细的差异化建议，用下面命令覆盖：
  ```bash
  python3 data_sources/modules/plan_scorer.py --website {website} --slug {slug} --guidance "避免重复[具体话题]，聚焦[差异化方向]"
  ```
  - 不覆盖也可以——基线 guidance 已经够 RESEARCH 和 WRITE 用。
4. **确认外部信号**：发现管线的弱需求候选，用 brief 里的 Tavily 结果佐证「真有人在搜/在问」。

### Step 4：输出推荐并保存

输出到终端 + 保存 `{website}/research/plan-recommendation-{date}.md`：

```markdown
## PLAN 推荐：下一篇写什么 — {website}

### 🥇 首推
**主题**: [包装后的完整标题]
**主关键词**: [...]
**候选来源**: [发现管线 #N，总分 X | 信号: 痛点Y / 缺口Z]
**理由**: [结合脚本分项信号 + 外部信号 + 信息增量]
**页面类型**: [Pillar / Cluster / Roundup]
**预估字数**: [...]
**蚕食风险**: [脚本给出的风险 + AI 复核]
**下一步**: /research [slug]

### 🥈 备选（2-3 个，来自候选表次高分）
| # | 主题 | 关键词 | 来源/分数 | 风险 |

### 🔧 顺带优化（可选，来自优化管线）
| 文章 | 关键词 | 展示/CTR | 建议动作 |

### ⚠️ 数据状态
**从 `plan-brief-{date}.json` 的 `meta` 字段直接读，不要自己猜。**
- GSC 更新日期：`meta.gsc_data_updated`。日期 → 超过 7 天标 ⚠️。
- 已发布篇数：`meta.published_articles`。
- 痛点库更新：`meta.pain_points_updated`。
- 外部信号：`external_signals.queries_used` 存在且非空 → 已启用，否则标 "未配置 Key"。
  如果 `external_signals.note` 存在 → 显示 note。**不要从文件名或 shell 命令推断。**
```

### Step 5：记录反馈（闭环，必须执行）

输出推荐后，**必须用 opencode `question` 工具弹出选择**让用户表态，然后调用脚本记录。

```text
question: "对这次 PLAN 推荐，你觉得？"
options:
  - "✅ 采纳首推，开始 /research"
  - "❌ 全部不满意，下次避开这些方向"
  - "⏭️ 跳过，下次再说"
```

按照用户选择执行：

```
✅ 采纳首推 → python3 plan_feedback.py --website {website} accept --slug {slug} --score {score}
❌ 全部不满意 → python3 plan_feedback.py --website {website} reject "首推标题" "备选1" "备选2" --reason "用户全部不满意"
⏭️ 跳过 → 不记录，告知用户下次直接输入主题重新 /plan
```

---

## 注意事项

1. **脚本已做的，AI 不要重做**：覆盖匹配、集群计数、痛点计数、打分排序、蚕食初筛——全在脚本里且确定性。AI 重算只会引入不一致。
2. **AI 的核心价值在 Step 3**：种子 → 有增量的角度、品牌过滤、外部信号佐证。
3. **优先发现管线**：发现管线 = 写新文，是 PLAN 主线。优化管线是顺带产出。
4. **GSC 数据 >7 天未更新**：在输出开头红色警告。
5. **一次只推 1 首推 + 2-3 备选**，不要列十几个。
6. **首推尽量带外部信号佐证**：纯脚本高分但无人讨论的方向，谨慎推。
