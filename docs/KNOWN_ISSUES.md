# Known Issues — SEO Ops System

> 最近更新：2026-07-28（基于 commit `409fd10`）

## P0 — 必须修复

### 1. 研究到写入不通：plan → research → write 的链断在第一步

**症状**：plan SKILL 推荐的主题**进不到** research SKILL，research SKILL 产出的素材包**进不到** write SKILL。

**根因**：
- 旧 `plan_collector.py` 输出的是 `plan-brief-{date}.md` / `plan-candidates-{date}.md`，存到 `{website}/research/`
- 新系统把这些路径当成"运行时产物"，全部 gitignore 了
- 新系统的"接受 plan 推荐"按钮生成 `actions` 行，但 action 没有 `target_ref` 真正对应到 plan 的 `plan-candidates` 里
- research 阶段调用的是新系统的 `topic-context-{slug}.json`（schema 是 enrichment JSON），但旧 `research_collector.py` 读的是 `seo-data-manual.md`（不同格式）
- write 阶段调用旧 `write_collector.py post-process`，但前置的素材包由新 AI 写，格式跟旧 collector 期望的不同

**实际后果**：
- 运营者看到 plan 推荐 → 点"接受" → 看到 action 列表 → 点 R0 → 生成搜索提示词
- 把提示词复制到搜索 AI → 粘贴回 R1 → collect 脚本跑 → 部分能产出 research-data，部分报错
- R3 AI 分析用的是新格式的 topic-context，不是旧期望的 schema
- W0 用 AI 写正文时给的素材包格式跟 write_collector.py validate 期望的可能不一致

**修复方向**（待 GPT/Codex 决定）：
1. **方案 A（保守）**：保留 plan/research/write 三套脚本各自独立运行，运营者手动切换；新系统的 action UI 只显示当前 R 阶段产物，不假装集成
2. **方案 B（中等）**：在 SEO Ops 里新增 `/plan` 路由，把 plan_collector.py 包成 web 操作，输出 `{website}/research/plan-brief-*.md` 到新系统的 data/legacy_workflow/laserpointerhub/research/
3. **方案 C（激进）**：用新系统的 research_candidates / opportunities 表替代 plan_candidates.md；plan_scorer.py 重写为数据库查询；plan_feedback.py 重写为 action decision

**优先级**：高 — 这是为什么新系统里 "/actions → R0" 之后流程会卡住的核心原因。

### 2. R0 重跑的提示词内容不变

**症状**：再点 R0（如果 UI 加了这个按钮的话），清空旧产物后重新生成的搜索提示词**和上次几乎一样**。

**根因**：`_build_search_prompt` 是纯函数，输入只依赖 `workspace/context/`、`workspace/published/`、`workspace/products/`（同步数据）。同一天、同样的 DB 内容 → 输出几乎相同。

**影响**：用户感知不到"重来"的价值；只有"清掉下游产物"的副作用。

**修复方向**：
- 在 R0 提示词里**显式标注生成日期 + workspace 路径 + DB 快照 ID**，让用户知道是新的
- 或者把提示词"硬分两段"：上半是固定模板（来自 context），下半是"基于当前已发布文章 + GSC 词 + 痛点库"动态拼装，让动态部分每次同步后能看到变化

**优先级**：中 — UX 问题，不阻塞功能。

---

## P1 — 应该修复

### 3. 后端启动必须设 PYTHONPATH

**症状**：从其他目录启动 `.venv/bin/seo-ops` 报 `ModuleNotFoundError: No module named 'data_sources'`。

**根因**：`legacy_workflow.py` 用 `from data_sources.modules import seo_common`，需要项目根目录在 `sys.path`。editable install 只把 `src/` 加到 sys.path。

**当前 workaround**：
```bash
PYTHONPATH=/home/laoma/seo-ops-system nohup setsid .venv/bin/seo-ops ...
```

**修复方向**：在 `src/seo_ops/__main__.py` 开头加：
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

**优先级**：高 — 每次新环境部署都会踩。

### 4. Legacy 工作流的 state 持久化字段不规范

**症状**：`reports/w2-state-{slug}.json` 是自由格式 JSON，缺字段时 `load_w2_state` 静默回退到 `{"rounds": 0, ...}`。

**根因**：为快速实现加的临时文件，没用 schema 校验。

**修复方向**：定义 `W2State` dataclass + JSON schema，用 pydantic 或 jsonschema 校验。

**优先级**：中。

### 5. legacy_sync 测试仍依赖真实数据库 fixture

**症状**：`tests/test_legacy_workflow.py::TestLegacySync` 5 个测试中部分依赖 `data/seo_ops.db` 的内容。

**根因**：MIGRATION_A3 修复批次（GPT 审计）未完整覆盖；目前 5 个 TestLegacySync 测试都通过但路径硬编码。

**修复方向**：用 `tmp_path` + 自建 DB fixture，5 个测试改为完全独立。

**优先级**：低 — 实际跑测试都通过，只是测试隔离不够干净。

---

## P2 — 知道但接受

### 6. 报告文件名带时间戳，可能累积

每次 W1b/W2/W3 跑都生成新报告文件（带 UTC 微秒 + UUID 后缀），旧的留在磁盘上。`load_report` 用 `_latest_file` 找最新，所以 UI 不会乱；但磁盘会慢慢长大。

**当前策略**：保留所有历史（审计需要）；未来加 retention 策略。

### 7. topic-context-{slug}.json 旧格式残留

`docs/legacy-skills/research/SKILL.md` 描述的 topic-context 格式（source/intent/tier/primary_keyword/cluster/cannibal_risk/signals/guidance/updated）是新系统的 schema。

旧 SKILL 不写 topic-context（旧 research 阶段没有这步），所以旧版本 → 新系统迁移时**没有可继承的 topic-context**，AI 必须从头推断。

**当前策略**：新系统自动生成 `source=heuristic` 的 topic-context 作为兜底；运营者接受新格式。

### 8. _build_search_prompt 不调用 AI

8-section 提示词完全本地拼装（读 GSC/已发布文章/痛点库），不调 AI。

**后果**：提示词质量依赖 GSC + 已发布文章 + 痛点库的质量。如果 DB 空，提示词很简陋。

**当前策略**：接受。

### 9. 没有自动化测试覆盖 legacy_sync 的真实数据库场景

`test_legacy_sync.py` 5 个测试都用 `_db_connection()` 但 mock 了游标，没测真实的 SQL。

**当前策略**：手动跑一次 sync 验证。CI 没有覆盖。

---

## 已修复（commit 409fd10 之前的工作）

| Issue | Fix |
|-------|-----|
| `cannibalization_checker.py:70` SITES_DIR 未定义 → NameError | 加 BASE_DIR/SITES_DIR fallback |
| `apply=False` 也会写 draft 文件 | 用 tempfile.mkstemp + 仅 gate_passed 时 apply |
| W3 注册没有后端 guard | 强制要求 `gate_passed + applied` |
| `--force` 只有前端校验 | 后端验证 rounds_left==0 + 蚕食 confirmed |
| 草稿改后 apply 还能注册 | 注册时校验 applied_draft_sha256 |
| slug 三套不一致 | 统一用 `seo_common.slugify`（SHA-256 fallback） |
| scorer 在 AI 之后跑 | 改为先跑评分，AI 拿到分数再解释 |
| 旧报告冒充成功 | 必须本次 rc=0 + 本次文件存在 |
| 脚本崩溃算通过 | 结构化 JSON 计数 |
| 同主题重试读错文件 | 每个 action 一个持久工作区 + R0 全清 |

---

## 不在本仓库范围

- **真实 AI/SerpAPI/Firecrawl/Tavily/GSC 凭据** — 全部存运营者本机 `.env`，不进 Git
- **生产数据库** — `data/*.db` gitignored，运营者自备份
- **飞书/微信接入** — Hermes 配置文件，不进 SEO Ops 仓库
- **运营者的 GSC OAuth token** — `.secrets/google/`，权限 0600，gitignored

---

## Codex/AI 协助时的边界

GPT Work、Codex 等 AI 助手可以改：
- `src/seo_ops/**/*.py` — Python 业务代码
- `src/seo_ops/web/templates/*.html`、`*.css`、`*.js` — 前端
- `data_sources/modules/**/*.py` — 冻结脚本（须保留业务规则，只修 bug）
- `docs/**/*.md` — 文档
- `tests/**/*.py` — 测试

不可以改：
- `.env`、`.env.example`（保留 schema 模板）
- `.secrets/` 任何文件
- `data/*.db`（生产数据）
- `data/legacy_workflow/laserpointerhub/context/` 下的真实种子文件（除非运营者授权）
- `data/legacy_workflow/laserpointerhub/published/` 下的真实文章
- `.gitignore`（除非确实需要排除新东西）