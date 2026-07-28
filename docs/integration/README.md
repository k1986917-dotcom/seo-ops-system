# Integration Guide — plan → research → write 三个 Skill 接入

本仓库维护了 3 个旧 SKILL 的原件 + 它们依赖的冻结脚本，供 Codex/AI 助手参考并接入到新 SEO Ops 系统。

---

## Skill 列表

| Skill | 触发 | 依赖脚本 | 输出 |
|-------|------|----------|------|
| [plan](../legacy-skills/plan/SKILL.md) | `/plan` 或 `/plan laserpointerhub` | `plan_collector.py` → `plan_scorer.py` → `plan_feedback.py` | 主题推荐（首推 + 备选 + 优化） |
| [research](../legacy-skills/research/SKILL.md) | `/research [topic]` | `research_collector.py` → `research_scorer.py` → AI | 素材包 + 调研简报 |
| [write](../legacy-skills/write/SKILL.md) | `/write [topic]` | `write_collector.py` → `write_pre_check.py` → `content_scorer.py` → AI | 草稿 → 后处理 → 注册 |

## 文件结构

```
docs/legacy-skills/
├── MANIFEST.md                      # 12 个文件的 SHA-256 + 来源说明
├── plan/SKILL.md                    # 6.7 KB
├── research/SKILL.md                # 12.6 KB
├── write/SKILL.md                   # 14.2 KB
└── data_sources/modules/
    ├── plan_collector.py            # 46.8 KB  (Step 1 收集)
    ├── plan_scorer.py               # 21.5 KB  (Step 2 确定性打分)
    ├── plan_feedback.py             # 5.6 KB   (Step 5 闭环)
    ├── research_collector.py        # 68.4 KB  (段0/1/3 数据收集)
    ├── research_scorer.py           # 10.6 KB  (Step 5 评分)
    ├── cannibalization_checker.py   # 10.2 KB  (Step 1 蚕食预检)
    ├── write_collector.py           # 53.2 KB  (段0/2/3 后处理)
    ├── write_pre_check.py           # 19.9 KB  (段1b 15 项机械检查)
    └── content_scorer.py            # 39.9 KB  (段2 质量评分)
```

---

## Skill 之间的输入输出接口

### plan → research

```
plan 产出:
  {website}/research/plan-brief-{date}.md     ← 人读
  {website}/research/plan-brief-{date}.json   ← 机读
  {website}/research/plan-candidates-{date}.md ← Top 6 优化 + Top 8 发现
  {website}/research/cannibalization-report.txt

research 接收:
  /research [topic] 或 /research [topic] [website]
  从 plan-candidates 选出首推，slug 作为 topic-context 来源
  自动读 seo-data-manual.md（GSC 数据）+ published-index.json（已发布列表）
```

### research → write

```
research 产出:
  {website}/material-packs/[slug]-[date].md   ← 唯一给 write 的输入
  {website}/research/brief-[slug]-[date].md   ← 审计归档
  {website}/research/research-score-[slug]-[date].md ← 6 因子评分

write 接收:
  /write [topic] 或 /write [topic] [website]
  自动定位 material-packs/{slug}-{date}.md
  加载 6 个 context 文件（brand-voice、writing-examples、style-guide、seo-guidelines、target-keywords、internal-links-map）
```

### write → 发布

```
write 产出:
  {website}/drafts/[slug]-[date].md         ← 最终文章
  reports/post-process-{slug}-*.md          ← 后处理报告
  reports/register-{slug}-*.md             ← 注册报告
  research/backlink-suggestions-{slug}-{date}.md ← 回溯链接清单

副作用:
  internal-links-map.md       ← 新增条目
  context/{new_article}.md   ← published/ 同步更新
  素材包 material-pack 被删除
  素材库 archive（pain-points / case-studies / external-sources）追加新条目
```

---

## 接入新系统的路径

新系统的 SEO Ops（`src/seo_ops/services/legacy_workflow.py`）**已经接管了** R/R3/W0/W1b/W2/W3 的所有产物管理。但**plan** 完全独立 —— plan_collector.py、plan_scorer.py、plan_feedback.py **没有任何代码调用它们**。

### 当前实际情况

| 阶段 | 旧 SKILL 入口 | 新系统入口 | 集成状态 |
|------|-------------|-----------|---------|
| plan | `/plan` | 无 | ❌ 未集成 |
| R0-R3 | `/research [topic]` | `/actions/{id}/legacy/stage/r0..r3` | ✅ 已集成 |
| W0-W3 | `/write [topic]` | `/actions/{id}/legacy/stage/w0..w3` | ✅ 已集成 |

### 集成方案

**最简方案（推荐 Codex 实施）**：
1. 在 `src/seo_ops/web/app.py` 新增 `POST /plan` 路由
2. 调用 `plan_collector.py` → 把输出写到 `data/legacy_workflow/laserpointerhub/research/plan-brief-{date}.md`
3. 调用 `plan_scorer.py` → 把候选写到 `plan-candidates-{date}.md`
4. UI 显示候选列表，运营者选 → 创建 `actions` 行（`action_type='create'`、`target_ref=slug`、`legacy_stage='r0_pending'`）
5. 现有的 `/actions/{id}/legacy/stage/r0..r3` 自动接管后续

**注意事项**：
- `plan_collector.py` 需要 `TAVILY_KEY` 环境变量（从 `data/plan-config.json` 或参数读）
- 新系统的 action 没有 `plan_id` 字段，要加 `actions.plan_run_id` 列（MIGRATION_14？）
- `plan_feedback.py` 的 accept/reject 需要写 `topic_decisions` 表，新系统已有

---

## 给 Codex 的任务提示

如果你（AI 助手）正在做 plan 集成，请按以下顺序：

1. **先读**：
   - `docs/legacy-skills/plan/SKILL.md`
   - `docs/legacy-skills/data_sources/modules/plan_collector.py` 的 docstring 和 Step 1 实现
   - `docs/legacy-skills/data_sources/modules/plan_scorer.py` 的 Step 2 实现
   - `src/seo_ops/services/action_workflow.py` 了解新系统的 action 创建流程
   - `src/seo_ops/services/research_workflow.py` 了解 research 阶段如何被触发

2. **再改**：
   - 在 `src/seo_ops/web/app.py` 加 `/plan` 路由（POST）
   - 复用 `legacy_sync_all(LEGACY_WS)` 确保 GSC/已发布文章已同步
   - 把 plan 的输出从 `{website}/research/` 重定向到 `data/legacy_workflow/laserpointerhub/research/` （新系统已 gitignore 这条路径，运行时产物本就该在这里）
   - 不要改 `plan_collector.py` / `plan_scorer.py` / `plan_feedback.py`（冻结脚本）

3. **测试**：
   - 加 `tests/test_plan_integration.py`
   - 不调真实 Tavily，用 monkeypatch 注入假的 brief
   - 验证：plan 输出 → action 创建 → R0 自动可用

4. **不要做**：
   - 不要把 plan 跟 opportunities 表混合（plan 推荐是新文章的种子，opportunities 是分析运行的结果）
   - 不要让 AI 重算 plan_scorer.py 已经确定性算出的分数
   - 不要把 plan 的 SKILL.md 写到 templates/ 目录（它是 AI 的指令，不是用户的 UI）