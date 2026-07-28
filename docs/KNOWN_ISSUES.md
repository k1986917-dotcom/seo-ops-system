# Known Issues — SEO Ops System

> 最近更新：2026-07-28（基于 commit `4811b9d`）
> 分支：`codex/legacy-skill-integration`

## 整合目标（已确认）

```
SEO Ops          → 历史、上下文、主题调研、主题选择（已有，可工作）
   ↓
旧 research Skill → 深度研究 + 素材包（R0-R3，已有，可工作）
   ↓
旧 write Skill    → 写作 + 检查 + 注册（W0-W3，已有，可工作）
   ↓
Hermes           → 统一调用、恢复任务状态、向运营者汇报（**待集成**）

旧 plan Skill    → 暂时作为业务规则参考和备用方案（**第一阶段不接入**）
```

`docs/integration/README.md` 详细描述这条目标链。

---

## P0 — 必须修复

### 1. Hermes 没有 SEO Ops 的 orchestrator skill

**症状**：Hermes Agent v0.19.0 已安装并运行，但没有 skill 知道怎么调用 SEO Ops
的 6 个 stage 端点。

**根因**：
- `docs/hermes/README.md` 写了 Hermes 的接入位置和模式
- `docs/hermes/RUNTIME_REPORT.md` 记录了实际安装环境
- 但没有 `~/.hermes/skills/software-development/seo-ops-orchestrator/SKILL.md`
- 没有 scripts/ 下的 6 个 stage 脚本（`stage_r0.sh`、`stage_r1.sh` …）

**修复方向**：
1. 在 `~/.hermes/skills/software-development/seo-ops-orchestrator/` 下创建
2. 写 SKILL.md（frontmatter + 工作流步骤）
3. 写 6 个 scripts/ 调用 `POST /actions/{id}/legacy/stage/{r0,r1,r3,w0,w1b,w2,w3}`
4. SKILL.md 中明确：**所有状态变更通过 HTTP API，不直接写文件**

**优先级**：高 — 这是当前唯一阻塞整合目标的缺口。

### 2. 旧 SKILL 文件不是 Hermes 原生格式

**症状**：`docs/legacy-skills/{plan,research,write}/SKILL.md` 是 Markdown
格式，但没有 Hermes 期望的 SKILL.md frontmatter + 配套 scripts 目录。

**根因**：旧 SKILL 是给 `opencode` 兼容的，Hermes 用自己的 skill 协议
（frontmatter + `~/.hermes/skills/<category>/<name>/{SKILL.md, scripts/, helpers/}`）。

**修复方向**：
- 在 Hermes skill 目录下创建包装版本（task #1 的伴随工作）
- 或者用 SKILL.md frontmatter 兼容 Hermes 的最小改造

**优先级**：中 — 与 #1 合并实施。

---

## P1 — 应该修复

### 3. plan skill 没接入第一阶段

**症状**：`docs/legacy-skills/plan/SKILL.md` 和 3 个 plan 脚本存在，但
SEO Ops 没有 `/legacy/plan` 路由调用它们。

**根因**：当前目标是 Hermes 驱动 research + write；plan 由 SEO Ops 自己的
opportunity/research_candidates 机制取代，**第一阶段不做 plan 接入**。

**修复方向**：
- 短期：**不修复**；plan SKILL 作历史参考保留
- 长期：如果运营者决定全量迁移到 Hermes plan，再做 `/legacy/plan` 路由

**优先级**：低 — 这是设计决策，不是 bug。

### 4. R0 重跑的提示词内容不变（同上一份 commit 409fd10 的 P0-2）

**症状**：再点 R0 重新生成的搜索提示词**和上次几乎一样**。

**根因**：`_build_search_prompt` 是纯函数，输入只依赖同步数据；同一天同样
DB → 输出几乎相同。

**修复方向**：
- 在 R0 提示词里**显式标注生成日期 + workspace 路径 + DB 快照 ID**
- 或者把提示词硬分两段：固定模板 + 动态拼装，让动态部分能看到变化

**优先级**：中 — UX 问题，不阻塞功能。

### 5. 后端启动必须设 PYTHONPATH（同上一份 commit 409fd10 的 P1-3）

**症状**：从其他目录启动 `.venv/bin/seo-ops` 报 `ModuleNotFoundError: No module
named 'data_sources'`。

**根因**：`legacy_workflow.py` 用 `from data_sources.modules import seo_common`，
需要项目根目录在 `sys.path`。editable install 只把 `src/` 加到 sys.path。

**修复方向**：在 `src/seo_ops/__main__.py` 开头加：
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

**优先级**：高 — 每次新环境部署都会踩。

### 6. Legacy 工作流的 state 持久化字段不规范（同上一份 commit 409fd10 的 P1-4）

**症状**：`reports/w2-state-{slug}.json` 是自由格式 JSON，缺字段时
`load_w2_state` 静默回退到 `{"rounds": 0, ...}`。

**修复方向**：定义 `W2State` dataclass + JSON schema，用 pydantic 或
jsonschema 校验。

**优先级**：中。

### 7. legacy_sync 测试仍依赖真实数据库 fixture（同上一份 commit 409fd10 的 P1-5）

**症状**：`tests/test_legacy_workflow.py::TestLegacySync` 5 个测试部分依赖
`data/seo_ops.db` 的内容。

**修复方向**：用 `tmp_path` + 自建 DB fixture。

**优先级**：低。

---

## P2 — 知道但接受

### 8. 报告文件名带时间戳，可能累积

每次 W1b/W2/W3 跑都生成新报告文件（带 UTC 微秒 + UUID 后缀）。磁盘慢慢长大。

**当前策略**：保留所有历史（审计需要）；未来加 retention 策略。

### 9. topic-context-{slug}.json 旧格式残留

旧 SKILL 不写 topic-context，新系统的 schema 是新格式。

**当前策略**：新系统自动生成 `source=heuristic` 的 topic-context 作为兜底。

### 10. _build_search_prompt 不调用 AI

8-section 提示词完全本地拼装。

**当前策略**：接受。

### 11. 没有自动化测试覆盖 legacy_sync 的真实数据库场景

`test_legacy_sync.py` 用 mock 游标。

**当前策略**：手动跑一次 sync 验证。CI 没有覆盖。

### 12. Hermes skill 包装缺少

`docs/legacy-skills/{plan,research,write}/SKILL.md` 是 opencode 兼容格式，
但不是 Hermes 原生格式（缺 frontmatter + scripts/）。

**当前策略**：等 task #1 实施时一并解决。

---

## 已修复（最近几次 commit）

| Issue | Fix |
|-------|-----|
| `cannibalization_checker.py:70` SITES_DIR 未定义 → NameError | 加 BASE_DIR/SITES_DIR fallback（commit 324ec79） |
| `apply=False` 也会写 draft 文件 | 用 tempfile.mkstemp + 仅 gate_passed 时 apply（324ec79） |
| W3 注册没有后端 guard | 强制要求 `gate_passed + applied`（324ec79） |
| `--force` 只有前端校验 | 后端验证 rounds_left==0 + 蚕食 confirmed（324ec79） |
| 草稿改后 apply 还能注册 | 注册时校验 applied_draft_sha256（324ec79） |
| slug 三套不一致 | 统一用 `seo_common.slugify`（324ec79） |
| scorer 在 AI 之后跑 | 改为先跑评分，AI 拿到分数再解释（324ec79） |
| 旧报告冒充成功 | 必须本次 rc=0 + 本次文件存在（324ec79） |
| 脚本崩溃算通过 | 结构化 JSON 计数（324ec79） |
| 同主题重试读错文件 | 每个 action 一个持久工作区 + R0 全清（409fd10） |
| 缺 plan SKILL 集成 | 决策：第一阶段不接入（4811b9d） |
| Hermes 文档不准（说 tirith 实际是 hermes） | 改用 hermes，RUNTIME_REPORT 实测（commit TBD） |

---

## 不在本仓库范围

- **真实 AI/SerpAPI/Firecrawl/Tavily/GSC 凭据** — 全部存运营者本机 `.env`
- **生产数据库** — `data/*.db` gitignored
- **Hermes 配置和聊天历史** — `~/.hermes/`（除脱敏的 `config.example.yaml` 和
  `SOUL.md` 副本外）
- **飞书/微信接入** — Hermes 配置文件，不进 SEO Ops 仓库
- **运营者的 GSC OAuth token** — `.secrets/google/`，gitignored

---

## AI 助手的工作边界

Hermes / GPT Work / Codex 等可以改：
- `src/seo_ops/**/*.py` — Python 业务代码
- `src/seo_ops/web/templates/*.html`、`*.css`、`*.js` — 前端
- `data_sources/modules/**/*.py` — 冻结脚本（须保留业务规则，只修 bug）
- `docs/**/*.md` — 文档（`docs/legacy-skills/` 下的原件除外）
- `tests/**/*.py` — 测试

不可以改：
- `.env`、`.env.example`（除非 schema 真的变了）
- `.secrets/` 任何文件
- `data/*.db`（生产数据）
- `data/legacy_workflow/laserpointerhub/context/` 下的真实种子文件
- `data/legacy_workflow/laserpointerhub/published/` 下的真实文章
- `docs/legacy-skills/` 下的任何文件（只读）
- `docs/hermes/SOUL.md`（与 Hermes 工具同步）
- `.gitignore`（除非确实需要排除新东西）