# Integration Guide — research + write Skill 接入 Hermes 工作流

本仓库维护了 3 个旧 SKILL 的原件 + 它们依赖的冻结脚本 + Hermes 接入说明，供
Codex/AI 助手把它们接入 Hermes 驱动的端到端工作流。

---

## 真实目标链（已确认）

```
SEO Ops（历史、上下文、主题调研、主题选择）
       │
       │  1. 主题调研 (GSC 信号 / 主题缺口 / 边界)
       │  2. 推荐候选 → 运营者接受
       │
       ▼
   actions 行（accepted / planned / target_ref = 主题）
       │
       │  Hermes 接收 action，根据 SKILL.md 调用旧 research Skill
       ▼
旧 research Skill（深度研究）
       │
       │  3. 搜索提示词（R0） → 运营者粘贴搜索结果
       │  4. 数据收集（R1）
       │  5. AI 分析 + 评分（R3）
       │  产出：material-pack + brief
       │
       ▼
旧 write Skill（写作 + 检查 + 注册）
       │
       │  6. AI 写正文（W0）
       │  7. 15 项机械预检（W1b）
       │  8. 后处理（链接 + 蚕食 + 评分 + apply）（W2）
       │  9. 注册到 internal-links-map + 回溯链接（W3）
       │
       ▼
   published/ 新文章 + material-pack 归档 + 素材库追加
       │
       │  Hermes 向运营者汇报
       ▼
   运营者确认发布
```

**关键边界**：

- SEO Ops 负责**第 1、2 步**（主题调研 + 主题选择 + 创建 action）
- 旧 research Skill 负责**第 3、4、5 步**（深度研究 + 素材包）
- 旧 write Skill 负责**第 6、7、8、9 步**（写作 + 检查 + 注册）
- Hermes 负责**统一调用、恢复任务状态、向运营者汇报**

---

## 旧 plan Skill 暂时不接入第一阶段

`docs/legacy-skills/plan/SKILL.md` 和 `plan_collector.py` / `plan_scorer.py` /
`plan_feedback.py` 目前**只作为业务规则参考和备用方案**，不作为整合目标。

理由：

1. SEO Ops 当前已经有自己的 opportunity / research_candidates / actions 机制做
   主题选择，与 plan_collector.py 的"plan-brief-*.md / plan-candidates-*.md"
   输出格式不兼容
2. plan 的"双管线"（优化 vs 发现）已经被 SEO Ops 的"2 篇旧 + 2 篇新"建议规则
   取代
3. Hermes 已经在 `~/.hermes/skills/software-development/plan/` 和
   `~/.hermes/skills/research/seo-content-gap-analyst/` 里有同类 skill

**未来可能的状态**：

- 如果运营者决定"全部迁移到 Hermes 驱动 plan"，再做 plan 集成
- 短期推荐：**保留** `docs/legacy-skills/plan/` 作历史参考，**不**写
  `POST /legacy/plan` 路由

---

## 三个 Skill 的输入输出接口

### research → write

```
research 产出:
  {website}/material-packs/[slug]-[date].md   ← write 的唯一输入
  {website}/research/brief-[slug]-[date].md   ← 审计归档
  {website}/research/research-score-[slug]-[date].md ← 6 因子评分

write 接收:
  自动定位 material-packs/{slug}-{date}.md
  加载 6 个 context 文件（brand-voice、writing-examples、style-guide、seo-guidelines、target-keywords、internal-links-map）
```

### write → 发布

```
write 产出:
  {website}/drafts/[slug]-[date].md
  reports/post-process-{slug}-*.md
  reports/register-{slug}-*.md
  research/backlink-suggestions-{slug]-[date}.md

副作用:
  internal-links-map.md       ← 新增条目
  context/{new_article}.md   ← published/ 同步更新
  素材包 material-pack 被删除
  素材库 archive（pain-points / case-studies / external-sources）追加新条目
```

---

## 集成路径

新系统的 SEO Ops（`src/seo_ops/services/legacy_workflow.py`）**已经接管了**
R0/R1/R3/W0/W1b/W2/W3 的所有产物管理和 HTTP 路由。

| 阶段 | 旧 SKILL 入口 | 新系统入口 | 集成状态 |
|------|-------------|-----------|---------|
| 主题调研 + 选择 | （SEO Ops 内部） | `/actions/{id}` + GSC 入口 | ✅ 已集成 |
| R0-R3 | `/research [topic]` | `/actions/{id}/legacy/stage/r0..r3` | ✅ 已集成 |
| W0-W3 | `/write [topic]` | `/actions/{id}/legacy/stage/w0..w3` | ✅ 已集成 |
| Hermes 统一调用 | （新增） | 同上 | ⚠️ 待 Hermes skill 包装 |
| plan | `/plan` | 无 | ❌ **第一阶段不做** |

### Hermes 集成的最小路径（推荐 Codex 实施）

1. 在 `~/.hermes/skills/software-development/` 下新建 `seo-ops-orchestrator/`：
   - `SKILL.md` — frontmatter + 工作流步骤
   - `scripts/` — `stage_r0.sh`、`stage_r1.sh`、`stage_r3.sh`、`stage_w0.sh`、
     `stage_w1b.sh`、`stage_w2.sh`、`stage_w3.sh`
   - 每个脚本用 `curl` POST 到 `http://127.0.0.1:8787/actions/{id}/legacy/stage/{stage}`
   - 脚本接受 `--action-id` 和（必要时）stage-specific 参数

2. SKILL.md 告诉 Hermes：
   - "操作 SEO Ops HTTP API"
   - "**绝不**直接写 `data/legacy_workflow/...` 或 `data/seo_ops.db`"
   - "所有状态变更通过 POST 端点"
   - "用 `GET /actions` 读当前状态"

3. 测试用 `tests/fixtures/legacy_pipeline/` 的合成数据：
   - 复制到 `data/legacy_workflow/examplesite/runs/action-99/current/laserpointerhub/`
   - UI 渲染应该能识别 R0..W3 各阶段

---

## 给 Hermes / Codex 的任务提示

如果你（AI 助手）正在做 Hermes 集成，请按以下顺序：

1. **先读**：
   - `docs/hermes/README.md`（当前版本是 `hermes` 而非 `tirith`）
   - `docs/hermes/RUNTIME_REPORT.md`（实际安装环境报告）
   - `docs/legacy-skills/research/SKILL.md` 和 `write/SKILL.md`
   - `src/seo_ops/services/legacy_workflow.py` 的 `action_workspace()` 和
     stage 函数（理解 R0..W3 行为）

2. **再改**：
   - 在 `~/.hermes/skills/software-development/seo-ops-orchestrator/` 下创建
     skill 目录
   - 写 SKILL.md + scripts/ 下的 6 个 stage 脚本
   - 脚本**只**用 `curl` HTTP 调用，**不**直接读写文件
   - SKILL.md 中要明确：所有状态变更通过 HTTP API

3. **测试**：
   - 加 `tests/test_hermes_skill_layout.py`（如果有对应的镜像在 repo）
   - 跑 `hermes skills list` 验证 skill 被发现
   - 手动触发一次 R0 看实际行为

4. **不要做**：
   - **不要**直接写 `data/legacy_workflow/laserpointerhub/runs/action-*/` 下的文件
   - **不要**修改 `data/seo_ops.db`（用 SQL 都不行）
   - **不要**修改 `docs/legacy-skills/` 下的原件
   - **不要**在第一阶段碰 `docs/legacy-skills/plan/` 或写 `/legacy/plan` 路由
   - **不要**修改 SEO Ops 的 `legacy_workflow.py`