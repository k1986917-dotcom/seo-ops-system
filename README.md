# SEO Ops System

面向单人网站运营者的本地网页系统。它读取第一方数据，审核数据可信度，识别当前最值得执行的 SEO/内容动作，并把合格机会交给外部研究与 AI 辅助层。

当前首个站点是 **LaserPointerHub**。系统按多站点设计，但不会为了通用性牺牲第一版的可用性。

> **本仓库的当前分支 `codex/legacy-skill-integration` 是为 Codex / AI 助手准备的代码整合基线**，包含：
> - 完整 SEO Ops 后端 + 前端（commit `409fd10` 起）
> - 3 个旧 SKILL 的原件（`docs/legacy-skills/{plan,research,write}/SKILL.md`）
> - 9 个冻结脚本（`docs/legacy-skills/data_sources/modules/`）
> - Hermes 接入说明（`docs/hermes/`）
> - 完整数据库 schema + 脱敏种子（`docs/db/`）
> - 已知问题清单（`docs/KNOWN_ISSUES.md`）
> - 整合指南（`docs/integration/README.md`）
>
> **核心已知问题**：plan → research → write 的链断在第一步（详见 `docs/KNOWN_ISSUES.md` P0-1）。

---

## 1. 项目状态

| 项 | 值 |
|----|----|
| 版本 | `0.10.9` |
| Python | 3.12+ |
| Node.js | 不需要 |
| 主分支 | `main` |
| Codex 工作分支 | `codex/legacy-skill-integration`（基于 `fix/research-tight-gate`，HEAD `409fd10`） |
| 服务端口 | `127.0.0.1:8787`（默认） |
| 数据库 | 单个 SQLite（默认 `data/seo_ops.db`，gitignored） |

## 当前状态

当前源码版本为 `0.10.9`，业务仍是“调研决定做什么、制作把文章做好”两阶段，五步主流程没有重构：

- GSC 可通过本机只读 OAuth 一键同步最终数据，同时保存查询、页面和真实的查询 + 页面联合行；Blog JSON 与 Product JSON 继续手工导入，Excel 仅作后备。
- OAuth 同步从运营者确认的 `2026-06-22` 起请求，绝不读取更早数据；客户端 JSON 与 token 只保存在受 Git 忽略的本地文件，不进入 SQLite。
- 页面汇总异常默认只是“需要诊断”；没有当前查询—页面证据不得生成旧文章修改稿，点击损失还要求上一窗口联合证据。
- 调研候选及读者可见文章字段统一为英语；中文/混合语言主题、当前批次内重复、历史候选重复和 CMS 主主题强重叠会被阻断。
- 每站点只使用一个当前 GSC 批次。运营者可在导入记录中明确删除某次错误 GSC 批次，系统会物理删除该快照、指标及只依赖它的派生分析。
- 主题图谱以树状界面展示现有内容、主要文章主题和辅助知识关联，并保存“要做、不再推荐、暂时跳过”的决定。
- 外部调研有 GSC 信号、主题缺口、边界扩展三个入口；每轮上限为 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20。
- 主题缺口/边界调研同时扫描十二类开放来源入口，不被自动分支词收窄；所有未与当前文章主意图/正文重复的来源种子保留在折叠主题池，只选 5 个继续深挖。末端合并同意图换说法，但保留任务、症状、决策、受众、地区、条件或结果不同的细化主题。
- 当前建议最多为 2 篇旧文章 + 2 篇新文章；任何一类没有合格信号时允许少于 2 篇或为 0，绝不凑数。
- 泛 FAQ、大而全指南、现有主意图重叠和已拒绝主题会被拦截；安全、功率、波长等内容只有在本身是页面核心问题时才算主要文章主题。
- 接受建议后进入一张文章任务卡：旧文章先起草、再由独立编辑审校并通过确定性质量门，始终锁定 Slug 和原主题；新文章先查看现有素材，选择是否粘贴手工搜索结果，确认后才开始写作。
- 新文章正常经过文章方案、完整初稿和编辑定稿三次 AI 调用，并采用原 skill 的文章类型篇幅、导语、Key Takeaways、FAQ、内外链和确定性自审规则。
- 外链只能来自本轮存证材料，并标注官方/研究、社区线索或行业/其他来源角色；AI 不得虚构实测、作者、法规、数据或来源。
- 系统不会自动发布，最终核对和粘贴仍由运营者完成。

## 最短使用方法

1. **数据导入**：首次点击“连接 Google Search Console”完成只读授权，之后点击“一键同步”；Blog JSON 和 Product JSON 仍手工导入。同步成功后系统自动更新内部旧文章分析。
2. **主题调研**：按 GSC、主题缺口或突破瓶颈选择一次调研；本页只显示运行概况，具体主题留到下一页。
3. **文章建议**：左边选旧文章、右边选新文章；每张卡只有开始执行、暂时跳过、不再推荐。
4. **文章制作**：旧文章生成修改稿；新文章先看素材、决定是否手工补充，再生成 CMS 八字段。
5. **主题图谱**：查看真实导入文章与产品的覆盖。CMS 发布后先点击“我已发布”，再在下次导入时同步为真实覆盖。

始终先看 [HANDOFF.md](HANDOFF.md) 了解当前进度和真实数据状态。

## 系统闭环

```text
GSC / CMS 当前数据 + 主题图谱 + 历史决定
        ↓
数据质量门槛与三入口外部调研
        ↓
最多 2 篇旧文章 + 2 篇新文章（不足不补位）
        ↓
按文章类型生成修改稿或新文章 CMS 内容包
        ↓
人工核对并发布
        ↓
7/28/56 天观察 → 规则校准
```

## 外部数据与 AI

浏览器不直接调用供应商：

```text
浏览器 → SEO Ops 后端 → SerpAPI / Firecrawl / Tavily / AI Provide
```

- SerpAPI：验证指定市场当时的 SERP，并接入 Google Trends。
- Google Trends：判断相对兴趣和季节性，不当作绝对搜索量。
- Firecrawl：抓取已经选定的页面，不当作需求或排名信号。
- Tavily：发现待核验资料，不当作 Google 排名或权威性证明。
- AI：综合带 evidence ID 的材料，不改指标、门槛或分数。

详见 [外部数据源与 GSC 可信度基线](docs/EXTERNAL_DATA_SOURCES.md) 和 [方法治理](docs/METHOD_GOVERNANCE.md)。

## 密钥设置

打开 `http://127.0.0.1:8787/settings`。只输入重新生成、未在聊天或工单中暴露的新密钥。

- 密钥框永不回显；留空表示保留，勾选后可清除。
- 密钥写入本机项目 `.env`，权限为 `0600`，Git 已忽略。
- `.env` 是本地明文环境文件，不是加密保险库；当前服务只能监听本机，不应开放到局域网或公网。
- GSC 客户端 JSON 与 OAuth token 位于 `.secrets/google/`，目录 0700、文件 0600；`.env` 只保存文件路径、回调地址和可信起始日。
- “连接成功”只表示账户接口有效。只有用户点击查询补证据或外部主题调研后才会产生证据；外部结果不会重算第一方分数。
- 设置页可调整每轮外部调研 API 上限；调研页在运行前会显示本轮最大值，预算为 0 即禁用该来源。
- “AI 助手 → 文章制作调用保护”可将每篇文章 AI 调用上限设为 3–10，默认 4。正常新文章 3 次、旧文章通常 2 次（起草 + 编辑审校）；只有检查失败才继续修订，且不与外部调研预算混算。

## 本地启动

要求 Python 3.12+。

```bash
cd /home/laoma/seo-ops-system
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
seo-ops
```

打开 `http://127.0.0.1:8787`。运行检查：

```bash
ruff check src tests tools
pytest
```

当前测试基线为 51 项完整执行成功；本轮自动测试使用假 Google、假外部服务与假 AI，没有消耗真实 API 额度。

## 项目资料

- [HANDOFF.md](HANDOFF.md)：当前状态、已知问题、下一步，接手时先读。
- [AGENTS.md](AGENTS.md)：开发与维护约束。
- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)：业务背景、权限边界与旧系统关系。
- [docs/CONTENT_WORKFLOW.md](docs/CONTENT_WORKFLOW.md)：已确认并上线的关键词调研与文章制作流程。
- [docs/TOPIC_GRAPH.md](docs/TOPIC_GRAPH.md)：主题树、底层概念图、去重记忆和分阶段实现方案。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：技术结构和数据流。
- [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md)：输入、连接状态与数据库约定。
- [docs/METHOD_GOVERNANCE.md](docs/METHOD_GOVERNANCE.md)：怎样保证方法可审计、可更新。
- [docs/EXTERNAL_DATA_SOURCES.md](docs/EXTERNAL_DATA_SOURCES.md)：外部来源分工、GSC 异常与凭据边界。
- [docs/ROADMAP.md](docs/ROADMAP.md)：里程碑与明确不做的范围。
- [docs/WORKLOG.md](docs/WORKLOG.md)：按时间记录实际工作。
- [docs/decisions](docs/decisions)：架构决策记录（ADR）。

## 与旧项目的关系

旧项目位于 `/home/laoma/seo-workflow`，只作为只读迁移来源：

- 原 `plan` 的数据压缩、关键词发现与打分方式不直接继承。
- 原 `research` 的素材分类、来源约束和手工补充思路已重构为任务卡中的素材确认。
- 原 `write` 的文章类型、稳定结构、动态链接规划、事实约束、链接校验和人工门控已适配后复用。
- 虚构作者、强制亲测话术、未知 URL、AI 检测门控和机械塞链接不继承。

新项目不得修改或删除旧项目文件。

---

## 9. 目录结构（完整）

```
seo-ops-system/
├── AGENTS.md                              # 开发与维护约束（必读）
├── HANDOFF.md                             # 当前状态、已知问题、下一步（必读）
├── CHANGELOG.md                           # 版本变更历史
├── README.md                              # 本文件
├── pyproject.toml                         # 项目配置 + 依赖
├── .env.example                           # 环境变量模板（无密钥）
│
├── src/seo_ops/                           # SEO Ops 主代码
│   ├── __main__.py                        # seo-ops 入口
│   ├── config.py                          # 配置加载（读 .env）
│   ├── db.py                              # SQLite schema + migrations
│   ├── repositories.py                    # 通用 CRUD
│   ├── utils.py                           # 工具函数（UTC 时间、JSON）
│   ├── ingest/                            # GSC OAuth + JSON/Excel 导入
│   ├── opportunities/                     # 机会引擎（rule + evidence）
│   ├── rules/                             # 规则（research/content/gsc/evidence）
│   ├── services/                          # 业务服务
│   │   ├── action_workflow.py             # 任务卡管理
│   │   ├── ai.py                          # AI provider 封装
│   │   ├── article_suggestions.py         # 文章建议
│   │   ├── content_production.py          # CMS 八字段产出
│   │   ├── external_evidence.py           # 外部证据整合
│   │   ├── external_sources.py            # 外部源配置
│   │   ├── gsc_oauth.py                   # GSC OAuth 流程
│   │   ├── legacy_sync.py                 # 把 DB 同步到 Legacy workspace 文件
│   │   ├── legacy_workflow.py             # ★ Legacy R0-W3 状态机（核心）
│   │   ├── material_workflow.py            # 素材确认流程
│   │   ├── research_workflow.py            # 外部主题调研
│   │   ├── settings_store.py              # 设置页读写
│   │   ├── topic_graph.py                 # 主题图谱
│   │   └── workflow_reset.py              # 流程重置工具
│   └── web/                               # FastAPI web 层
│       ├── app.py                         # 路由 + 业务逻辑
│       ├── templates/                     # Jinja 模板（13 个 HTML）
│       └── static/                        # CSS + JS
│
├── data_sources/modules/                  # 冻结脚本（与旧系统同源）
│   ├── cannibalization_checker.py         # 蚕食检查（TF-IDF）
│   ├── content_scorer.py                  # 内容质量评分（≥70 gate）
│   ├── content_scrubber.py                # AI 水印去除
│   ├── plan_collector.py                  # ★ plan 阶段 1
│   ├── plan_feedback.py                   # ★ plan 阶段 5（闭环）
│   ├── plan_scorer.py                     # ★ plan 阶段 2（确定性打分）
│   ├── readability_scorer.py              # 可读性评分（未用）
│   ├── research_collector.py              # ★ research 段0/1/3
│   ├── research_scorer.py                 # ★ research Step 5 评分
│   ├── seo_common.py                      # 共享工具（slug、cannibal API）
│   ├── seo_config.py                      # 阈值常量
│   ├── seo_quality_rater.py               # 高级评分（未用）
│   └── write_pre_check.py                 # ★ write 段1b 15 项预检
│
├── tests/                                 # 单元测试（177 个）
├── docs/                                  # 文档
│   ├── ARCHITECTURE.md                    # 技术架构
│   ├── CONTENT_WORKFLOW.md                # 已上线的工作流
│   ├── DATA_CONTRACTS.md                  # 数据契约
│   ├── EXTERNAL_DATA_SOURCES.md           # 外部源与 GSC 可信度
│   ├── METHOD_GOVERNANCE.md               # 方法治理
│   ├── PROJECT_CONTEXT.md                 # 业务背景
│   ├── ROADMAP.md                         # 路线图
│   ├── TOPIC_GRAPH.md                     # 主题图谱
│   ├── USABILITY_ISSUES.md                # UI 问题
│   ├── WORKLOG.md                         # 工作日志
│   ├── KNOWN_ISSUES.md                    # ★ 已知问题清单（含 P0/P1/P2）
│   ├── decisions/                         # ADR（架构决策记录）
│   ├── superpowers/                        # 工作流规格 + 实施计划
│   ├── legacy-skills/                     # ★ 旧 SKILL 原件 + 冻结脚本（snapshot）
│   │   ├── MANIFEST.md                    #   12 文件 SHA-256 清单
│   │   ├── plan/SKILL.md
│   │   ├── research/SKILL.md
│   │   ├── write/SKILL.md
│   │   └── data_sources/modules/          #   9 个冻结脚本
│   ├── hermes/                            # ★ Hermes AI 助手接入说明
│   │   ├── README.md
│   │   ├── SOUL.md                        #   Hermes 系统提示副本
│   │   └── config.example.yaml            #   配置 schema（无密钥）
│   ├── db/                                # ★ 数据库 schema（脱敏）
│   │   ├── README.md
│   │   ├── schema.sql                     #   13 个 migration 完整 SQL
│   │   └── seed.sql                       #   1 site + 2 fake articles
│   └── integration/                       # ★ plan → research → write 整合指南
│       └── README.md
│
├── data/                                  # 运行时数据（gitignored）
│   ├── seo_ops.db                         #   SQLite 数据库
│   ├── _test_migration_13.db              #   测试遗留
│   ├── raw/                               #   原始导入文件
│   ├── snapshots/                         #   历史快照
│   ├── backups/                           #   备份
│   └── legacy_workflow/laserpointerhub/   #   Legacy workspace
│       ├── context/                       #     同步生成的 7 个 context 文件
│       ├── published/                     #     已发布文章 MD + index
│       ├── products/                      #     产品报告
│       └── runs/action-{id}/current/      #     ★ 每个 action 一个持久工作区
│           └── laserpointerhub/
│               ├── context → ../context  (符号链接)
│               ├── published → ../published
│               ├── products → ../products
│               ├── research/              (action 私有)
│               ├── material-packs/
│               ├── drafts/
│               └── reports/
│
└── logs/                                  # 运行日志（gitignored）
```

## 10. 三个旧 Skill 的输入输出

### plan SKILL

**触发**：`/plan [website]`

**Step 1（plan_collector.py）输入**：
- GSC 数据（`data/legacy_workflow/laserpointerhub/context/seo-data-manual.md`）
- 已发布文章（`data/legacy_workflow/laserpointerhub/published/published-index.json`）
- 痛点库（`data/legacy_workflow/laserpointerhub/context/pain-points-library.md`）
- 案例库（`data/legacy_workflow/laserpointerhub/context/case-studies-library.md`）
- 引用库（`data/legacy_workflow/laserpointerhub/context/external-sources-library.md`）
- 蚕食检查（`cannibalization_checker.py`）
- 外部：Tavily API（`TAVILY_KEY`，可选）

**Step 1 输出**：
- `research/plan-brief-{date}.md`（人读）
- `research/plan-brief-{date}.json`（机读）
- `research/cannibalization-report.txt`

**Step 2（plan_scorer.py）输入**：plan-brief JSON
**Step 2 输出**：
- `research/plan-candidates-{date}.md`（Top 6 优化 + Top 8 发现，含分项信号）

**Step 3（AI）输入**：plan-candidates + plan-brief + 已发布文章正文（按需）
**Step 3 输出**：包装后的"首推主题 + 备选 + 优化"

**Step 4（写入）**：`research/plan-recommendation-{date}.md`

**Step 5（plan_feedback.py）输入**：`accept | reject "..." --reason "..." | skip`
**Step 5 输出**：写 `topic_decisions` 表

### research SKILL

**触发**：`/research [topic] [website]`

**段0（research_collector.py generate-prompt）输入**：
- 同 plan 的 GSC + 已发布文章 + 3 个库
- topic（来自 plan 推荐或手动输入）

**段0 输出**：`research/search-prompt-{slug}-{date}.md`（8 Section 提示词）

**段1（research_collector.py collect）输入**：
- 搜索结果（运营者粘贴到 search-results-{slug}-{date}.md）

**段1 输出**：
- `research/research-data-{slug}-{date}.md`（人读）
- `research/research-data-{slug}-{date}.json`（机读，给 scorer）

**段2（AI）输入**：
- research-data-{slug}-{date}.json
- topic-context-{slug}.json（intelligence）
- brand-voice.md
- deterministic scorer 输出（在 Step 5 之前先跑）

**段2 输出**：
- `material-packs/{slug}-{date}.md`（Part 1 指令 + Part 2 素材 + Part 3 质检）
- `research/brief-{slug}-{date}.md`（审计归档）

**段3（research_collector.py archive）输入**：material-pack
**段3 输出**：追加到 pain-points / case-studies / external-sources 三个库

### write SKILL

**触发**：`/write [topic] [website]`

**段0（write_collector.py validate）输入**：material-pack
**段0 输出**：校验报告（控制台 + 6 个 context 文件全文回显）

**段1（AI）输入**：
- 素材包
- topic-context（继承 tier / guidance）
- 6 个 context 文件（brand-voice, writing-examples, style-guide, seo-guidelines, target-keywords, internal-links-map）
- live_products_report.md（产品规格）

**段1 输出**：`drafts/{slug}-{date}.md`（完整文章，含 frontmatter）

**段1b（write_pre_check.py）输入**：draft + tier + keywords
**段1b 输出**：`reports/pre-check-{slug}-*.md`（15 项机械检查）

**段2（write_collector.py post-process）输入**：draft + material-pack
**段2 步骤**：
1. scrub（去 AI 水印）
2. 抽链接 + 验来源（ILM + E 类）
3. 蚕食门控（≥0.55 阻塞）
4. 质量评分（≥70 通过）
5. `--apply` 时写回 frontmatter（链接字段）

**段2 输出**：
- `reports/post-process-{slug}-*.md`
- 更新 `reports/w2-state-{slug}.json`（gate_passed / applied / applied_draft_sha256）
- 可选：更新 draft（apply=true 且 gate_passed）

**段3（write_collector.py register）输入**：applied draft + material-pack + URL
**段3 输出**：
- 注册到 internal-links-map.md
- AI 生成 `research/backlink-suggestions-{slug}-{date}.md`（人工审批清单）
- 删除 material-pack 文件
- 报告：`reports/register-{slug}-*.md`

## 11. 本地启动（完整）

### 必需依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 主语言 |
| pip | 24+ | 包管理 |
| SQLite | 3.40+ | 数据库（Python 内置） |
| Git | 2.30+ | 版本控制 |

### 安装

```bash
# 克隆（公开仓库）
git clone https://github.com/k1986917-dotcom/seo-ops-system.git
cd seo-ops-system
git checkout codex/legacy-skill-integration

# Python 环境
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# 启动 web 服务（必须设 PYTHONPATH，包含项目根）
PYTHONPATH=$(pwd) nohup setsid .venv/bin/seo-ops > /tmp/seo-ops.log 2>&1 < /dev/null &
disown

# 验证
sleep 4
curl -s http://127.0.0.1:8787/api/health
# {"status":"ok","version":"0.10.9","ai_enabled":true,...}

# 打开浏览器
# http://127.0.0.1:8787
```

### 验证测试

```bash
# 单元测试（177 个；不消耗真实 API）
.venv/bin/pytest

# 静态检查
.venv/bin/ruff check src tests tools data_sources/modules
.venv/bin/ruff check --select F821 src tests tools data_sources/modules
python3 -m compileall -q src tests data_sources/modules
```

### 注入脱敏测试数据（可选）

```bash
# 初始化空 DB + 默认 site
.venv/bin/python3 -c "
from pathlib import Path
from seo_ops.config import Settings, get_settings
from seo_ops.db import init_db
s = get_settings()
init_db(s)
"

# 可选：灌入 docs/db/seed.sql 的占位数据
.venv/bin/python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('data/seo_ops.db')
with open('docs/db/seed.sql') as f:
    conn.executescript(f.read())
print('seed loaded')
conn.close()
---

## 9. 目录结构（完整）

```
seo-ops-system/
├── AGENTS.md                              # 开发与维护约束（必读）
├── HANDOFF.md                             # 当前状态、已知问题、下一步（必读）
├── CHANGELOG.md                           # 版本变更历史
├── README.md                              # 本文件
├── pyproject.toml                         # 项目配置 + 依赖
├── .env.example                           # 环境变量模板（无密钥）
│
├── src/seo_ops/                           # SEO Ops 主代码
│   ├── __main__.py                        # seo-ops 入口
│   ├── config.py                          # 配置加载（读 .env）
│   ├── db.py                              # SQLite schema + migrations
│   ├── repositories.py                    # 通用 CRUD
│   ├── utils.py                           # 工具函数（UTC 时间、JSON）
│   ├── ingest/                            # GSC OAuth + JSON/Excel 导入
│   ├── opportunities/                     # 机会引擎（rule + evidence）
│   ├── rules/                             # 规则（research/content/gsc/evidence）
│   ├── services/                          # 业务服务
│   │   ├── action_workflow.py             # 任务卡管理
│   │   ├── ai.py                          # AI provider 封装
│   │   ├── article_suggestions.py         # 文章建议
│   │   ├── content_production.py          # CMS 八字段产出
│   │   ├── external_evidence.py           # 外部证据整合
│   │   ├── external_sources.py            # 外部源配置
│   │   ├── gsc_oauth.py                   # GSC OAuth 流程
│   │   ├── legacy_sync.py                 # 把 DB 同步到 Legacy workspace 文件
│   │   ├── legacy_workflow.py             # ★ Legacy R0-W3 状态机（核心）
│   │   ├── material_workflow.py            # 素材确认流程
│   │   ├── research_workflow.py            # 外部主题调研
│   │   ├── settings_store.py              # 设置页读写
│   │   ├── topic_graph.py                 # 主题图谱
│   │   └── workflow_reset.py              # 流程重置工具
│   └── web/                               # FastAPI web 层
│       ├── app.py                         # 路由 + 业务逻辑
│       ├── templates/                     # Jinja 模板（13 个 HTML）
│       └── static/                        # CSS + JS
│
├── data_sources/modules/                  # 冻结脚本（与旧系统同源）
│   ├── cannibalization_checker.py         # 蚕食检查（TF-IDF）
│   ├── content_scorer.py                  # 内容质量评分（≥70 gate）
│   ├── content_scrubber.py                # AI 水印去除
│   ├── plan_collector.py                  # ★ plan 阶段 1
│   ├── plan_feedback.py                   # ★ plan 阶段 5（闭环）
│   ├── plan_scorer.py                     # ★ plan 阶段 2（确定性打分）
│   ├── readability_scorer.py              # 可读性评分（未用）
│   ├── research_collector.py              # ★ research 段0/1/3
│   ├── research_scorer.py                 # ★ research Step 5 评分
│   ├── seo_common.py                      # 共享工具（slug、cannibal API）
│   ├── seo_config.py                      # 阈值常量
│   ├── seo_quality_rater.py               # 高级评分（未用）
│   ├── write_collector.py                 # ★ write 段0/2/3
│   └── write_pre_check.py                 # ★ write 段1b 15 项预检
│
├── tests/                                 # 单元测试（177 个）
├── docs/                                  # 文档
│   ├── ARCHITECTURE.md                    # 技术架构
│   ├── CONTENT_WORKFLOW.md                # 已上线的工作流
│   ├── DATA_CONTRACTS.md                  # 数据契约
│   ├── EXTERNAL_DATA_SOURCES.md           # 外部源与 GSC 可信度
│   ├── METHOD_GOVERNANCE.md               # 方法治理
│   ├── PROJECT_CONTEXT.md                 # 业务背景
│   ├── ROADMAP.md                         # 路线图
│   ├── TOPIC_GRAPH.md                     # 主题图谱
│   ├── USABILITY_ISSUES.md                # UI 问题
│   ├── WORKLOG.md                         # 工作日志
│   ├── KNOWN_ISSUES.md                    # ★ 已知问题清单（含 P0/P1/P2）
│   ├── decisions/                         # ADR（架构决策记录）
│   ├── superpowers/                        # 工作流规格 + 实施计划
│   ├── legacy-skills/                     # ★ 旧 SKILL 原件 + 冻结脚本（snapshot）
│   │   ├── MANIFEST.md                    #   12 文件 SHA-256 清单
│   │   ├── plan/SKILL.md
│   │   ├── research/SKILL.md
│   │   ├── write/SKILL.md
│   │   └── data_sources/modules/          #   9 个冻结脚本
│   ├── hermes/                            # ★ Hermes AI 助手接入说明
│   │   ├── README.md
│   │   ├── SOUL.md                        #   Hermes 系统提示副本
│   │   └── config.example.yaml            #   配置 schema（无密钥）
│   ├── db/                                # ★ 数据库 schema（脱敏）
│   │   ├── README.md
│   │   ├── schema.sql                     #   13 个 migration 完整 SQL
│   │   └── seed.sql                       #   1 site + 2 fake articles
│   └── integration/                       # ★ plan → research → write 整合指南
│       └── README.md
│
├── data/                                  # 运行时数据（gitignored）
│   ├── seo_ops.db                         #   SQLite 数据库
│   ├── _test_migration_13.db              #   测试遗留
│   ├── raw/                               #   原始导入文件
│   ├── snapshots/                         #   历史快照
│   ├── backups/                           #   备份
│   └── legacy_workflow/laserpointerhub/   #   Legacy workspace
│       ├── context/                       #     同步生成的 7 个 context 文件
│       ├── published/                     #     已发布文章 MD + index
│       ├── products/                      #     产品报告
│       └── runs/action-{id}/current/      #     ★ 每个 action 一个持久工作区
│           └── laserpointerhub/
│               ├── context → ../context  (符号链接)
│               ├── published → ../published
│               ├── products → ../products
│               ├── research/              (action 私有)
│               ├── material-packs/
│               ├── drafts/
│               └── reports/
│
└── logs/                                  # 运行日志（gitignored）
```

## 10. 三个旧 Skill 的输入输出

### plan SKILL

**触发**：`/plan [website]`

**Step 1（plan_collector.py）输入**：
- GSC 数据（`data/legacy_workflow/laserpointerhub/context/seo-data-manual.md`）
- 已发布文章（`data/legacy_workflow/laserpointerhub/published/published-index.json`）
- 痛点库（`data/legacy_workflow/laserpointerhub/context/pain-points-library.md`）
- 案例库（`data/legacy_workflow/laserpointerhub/context/case-studies-library.md`）
- 引用库（`data/legacy_workflow/laserpointerhub/context/external-sources-library.md`）
- 蚕食检查（`cannibalization_checker.py`）
- 外部：Tavily API（`TAVILY_KEY`，可选）

**Step 1 输出**：
- `research/plan-brief-{date}.md`（人读）
- `research/plan-brief-{date}.json`（机读）
- `research/cannibalization-report.txt`

**Step 2（plan_scorer.py）输入**：plan-brief JSON
**Step 2 输出**：
- `research/plan-candidates-{date}.md`（Top 6 优化 + Top 8 发现，含分项信号）

**Step 3（AI）输入**：plan-candidates + plan-brief + 已发布文章正文（按需）
**Step 3 输出**：包装后的"首推主题 + 备选 + 优化"

**Step 4（写入）**：`research/plan-recommendation-{date}.md`

**Step 5（plan_feedback.py）输入**：`accept | reject "..." --reason "..." | skip`
**Step 5 输出**：写 `topic_decisions` 表

### research SKILL

**触发**：`/research [topic] [website]`

**段0（research_collector.py generate-prompt）输入**：
- 同 plan 的 GSC + 已发布文章 + 3 个库
- topic（来自 plan 推荐或手动输入）

**段0 输出**：`research/search-prompt-{slug}-{date}.md`（8 Section 提示词）

**段1（research_collector.py collect）输入**：
- 搜索结果（运营者粘贴到 search-results-{slug}-{date}.md）

**段1 输出**：
- `research/research-data-{slug}-{date}.md`（人读）
- `research/research-data-{slug}-{date}.json`（机读，给 scorer）

**段2（AI）输入**：
- research-data-{slug}-{date}.json
- topic-context-{slug}.json（intelligence）
- brand-voice.md
- deterministic scorer 输出（在 Step 5 之前先跑）

**段2 输出**：
- `material-packs/{slug}-{date}.md`（Part 1 指令 + Part 2 素材 + Part 3 质检）
- `research/brief-{slug}-{date}.md`（审计归档）

**段3（research_collector.py archive）输入**：material-pack
**段3 输出**：追加到 pain-points / case-studies / external-sources 三个库

### write SKILL

**触发**：`/write [topic] [website]`

**段0（write_collector.py validate）输入**：material-pack
**段0 输出**：校验报告（控制台 + 6 个 context 文件全文回显）

**段1（AI）输入**：
- 素材包
- topic-context（继承 tier / guidance）
- 6 个 context 文件（brand-voice, writing-examples, style-guide, seo-guidelines, target-keywords, internal-links-map）
- live_products_report.md（产品规格）

**段1 输出**：`drafts/{slug}-{date}.md`（完整文章，含 frontmatter）

**段1b（write_pre_check.py）输入**：draft + tier + keywords
**段1b 输出**：`reports/pre-check-{slug}-*.md`（15 项机械检查）

**段2（write_collector.py post-process）输入**：draft + material-pack
**段2 步骤**：
1. scrub（去 AI 水印）
2. 抽链接 + 验来源（ILM + E 类）
3. 蚕食门控（≥0.55 阻塞）
4. 质量评分（≥70 通过）
5. `--apply` 时写回 frontmatter（链接字段）

**段2 输出**：
- `reports/post-process-{slug}-*.md`
- 更新 `reports/w2-state-{slug}.json`（gate_passed / applied / applied_draft_sha256）
- 可选：更新 draft（apply=true 且 gate_passed）

**段3（write_collector.py register）输入**：applied draft + material-pack + URL
**段3 输出**：
- 注册到 internal-links-map.md
- AI 生成 `research/backlink-suggestions-{slug}-{date}.md`（人工审批清单）
- 删除 material-pack 文件
- 报告：`reports/register-{slug}-*.md`

## 11. 本地启动（完整）

### 必需依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 主语言 |
| pip | 24+ | 包管理 |
| SQLite | 3.40+ | 数据库（Python 内置） |
| Git | 2.30+ | 版本控制 |

### 安装

```bash
# 克隆（公开仓库）
git clone https://github.com/k1986917-dotcom/seo-ops-system.git
cd seo-ops-system
git checkout codex/legacy-skill-integration

# Python 环境
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# 启动 web 服务（必须设 PYTHONPATH，包含项目根）
PYTHONPATH=$(pwd) nohup setsid .venv/bin/seo-ops > /tmp/seo-ops.log 2>&1 < /dev/null &
disown

# 验证
sleep 4
curl -s http://127.0.0.1:8787/api/health
# 预期：{"status":"ok","version":"0.10.9","ai_enabled":true,...}

# 打开浏览器
# http://127.0.0.1:8787
```

### 验证测试

```bash
# 单元测试（177 个；不消耗真实 API）
.venv/bin/pytest

# 静态检查
.venv/bin/ruff check src tests tools data_sources/modules
.venv/bin/ruff check --select F821 src tests tools data_sources/modules
python3 -m compileall -q src tests data_sources/modules
```

### 注入脱敏测试数据（可选）

```bash
# 初始化空 DB + 默认 site
.venv/bin/python3 -c "
from seo_ops.config import get_settings
from seo_ops.db import init_db
init_db(get_settings())
"

# 可选：灌入 docs/db/seed.sql 的占位数据
.venv/bin/python3 << 'PYEOF'
import sqlite3
conn = sqlite3.connect('data/seo_ops.db')
with open('docs/db/seed.sql') as f:
    conn.executescript(f.read())
print('seed loaded')
conn.close()
PYEOF
```

## 12. 环境变量

`pyproject.toml` 列出所有依赖；`.env.example` 列出环境变量模板。**所有密钥留空也能启动**（"未配置 AI"模式正常运行）。

### SEO Ops .env（写入项目根）

| 变量 | 必需？ | 默认 | 说明 |
|------|--------|------|------|
| `SEO_OPS_HOST` | 否 | `127.0.0.1` | 监听地址 |
| `SEO_OPS_PORT` | 否 | `8787` | 监听端口 |
| `SEO_OPS_TIMEZONE` | 否 | `Asia/Shanghai` | UI 时区显示 |
| `SEO_OPS_DATA_DIR` | 否 | 项目内 `data/` | DB + 快照根目录 |
| `SEO_OPS_AI_BASE_URL` | 否（AI 必需时） | 空 | OpenAI-compatible base URL |
| `SEO_OPS_AI_API_KEY` | 否（AI 必需时） | 空 | AI provider 密钥 |
| `SEO_OPS_AI_MODEL` | 否 | 空 | 模型名（如 `gpt-4o`、`deepseek-v4-flash`） |
| `SEO_OPS_AI_PROVIDER` | 否 | `openai-compatible` | Provider 类型 |
| `SEO_OPS_FIRECRAWL_BASE_URL` | 否 | `https://api.firecrawl.dev/v2` | Firecrawl base URL |
| `SEO_OPS_FIRECRAWL_API_KEY` | 否 | 空 | Firecrawl 密钥 |
| `SEO_OPS_TAVILY_BASE_URL` | 否 | `https://api.tavily.com` | Tavily base URL |
| `SEO_OPS_TAVILY_API_KEY` | 否 | 空 | Tavily 密钥 |
| `SEO_OPS_SERPAPI_KEY` | 否 | 空 | SerpAPI 密钥 |
| `SEO_OPS_SERPAPI_BASE_URL` | 否 | `https://serpapi.com` | SerpAPI base URL |
| `SEO_OPS_SERPAPI_COUNTRY` | 否 | `us` | SERP 国家 |
| `SEO_OPS_SERPAPI_LANGUAGE` | 否 | `en` | SERP 语言 |
| `SEO_OPS_SERPAPI_LOCATION` | 否 | `United States` | SERP 地理位置 |
| `SEO_OPS_SERPAPI_DEVICE` | 否 | `desktop` | SERP 设备 |
| `SEO_OPS_TRENDS_PROVIDER` | 否 | `serpapi` | Trends 数据源 |
| `SEO_OPS_TRENDS_GEO` | 否 | `US` | Trends 地区 |
| `SEO_OPS_TRENDS_TIMEFRAME` | 否 | `today 12-m` | Trends 时间范围 |
| `SEO_OPS_RESEARCH_AI_BUDGET` | 否 | `17` | 调研每轮 AI 调用上限 |
| `SEO_OPS_RESEARCH_FIRECRAWL_BUDGET` | 否 | `7` | 调研每轮 Firecrawl 上限 |
| `SEO_OPS_RESEARCH_SERPAPI_BUDGET` | 否 | `5` | 调研每轮 SerpAPI 上限 |
| `SEO_OPS_RESEARCH_TAVILY_BUDGET` | 否 | `17` | 调研每轮 Tavily 上限 |
| `SEO_OPS_CONTENT_AI_CALL_LIMIT` | 否 | `10` | 每篇文章 AI 调用上限 |
| `SEO_OPS_GSC_OAUTH_CLIENT_FILE` | 否 | `.secrets/google/gsc-client-secret.json` | GSC 客户端 JSON 路径 |
| `SEO_OPS_GSC_OAUTH_TOKEN_FILE` | 否 | `.secrets/google/gsc-token.json` | GSC token 路径 |
| `SEO_OPS_GSC_REDIRECT_URI` | 否 | `http://127.0.0.1:8787` | OAuth 回调地址 |
| `SEO_OPS_GSC_TRUSTED_START_DATE` | 否 | 空 | 首次同步起始日期（运营者人工确认） |

### Hermes .env（写入 `~/.hermes/.env`，与 SEO Ops .env 完全分离）

Hermes 自身的 AI provider、飞书通道等。详见 `docs/hermes/README.md`。

### 关键不变量

- **SEO Ops .env 和 Hermes .env 永不交叉** — 两个系统完全独立
- **运营者手动配密钥** — 没有"自动同步"功能
- **密钥只从环境变量读** — 绝不写入 SQLite、HTML 响应、日志或 Git

## 13. Codex 工作流

如果你（AI 助手）正在做集成：

1. **先读**：[AGENTS.md](AGENTS.md)、[HANDOFF.md](HANDOFF.md)、[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)
2. **再读**：[docs/integration/README.md](docs/integration/README.md)
3. **再看**：相关 SKILL.md 和冻结脚本的 docstring
4. **再动**：根据 P0/P1/P2 优先级
5. **测试**：每个改动必须有对应单元测试，pytest 全过
6. **PR**：所有改动走 GitHub PR，merge 到 `fix/research-tight-gate` 或新分支

### Codex 不要做的事

- 不要修改 `.env`、`.env.example`（除非 schema 真的变了）
- 不要修改 `.secrets/` 任何文件
- 不要修改生产数据库
- 不要调用真实 AI/SerpAPI/Firecrawl/Tavily/GSC
- 不要修改 `docs/legacy-skills/` 下的原件（只读）
- 不要修改 `docs/hermes/SOUL.md` 副本（与 Hermes 工具同步）