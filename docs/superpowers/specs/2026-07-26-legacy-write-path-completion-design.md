# Legacy 写作通道补完 — 诊断与设计

日期：2026-07-26（Asia/Shanghai）
状态：运营者已批准，实施中
上游：`docs/superpowers/plans/2026-07-21-legacy-research-write-restoration.md`、`docs/LEGACY_RESEARCH_WRITE_RESTORATION_HANDOFF.md`

## 一句话

`0.11.0` 的 Legacy 移植停在"能 import、能过测试、但一点按钮就崩"的状态：调研部分勉强可用，写作部分从未真正跑通过。本文记录诊断证据、根因、以及补完设计。

## 背景

旧工作空间 `/home/laoma/seo-workflow` 有两个 opencode skill：

- `.opencode/skills/research/SKILL.md`（349 行）：段0 生成搜索提示词 → 段1 收集 → 段2 AI 分析写素材包 → 段3 素材归档
- `.opencode/skills/write/SKILL.md`（367 行）：段0 校验素材包 → 段1 AI 写文章 → 段1b 预检 → 段2 后处理（含修订循环）→ 段3 注册 + 回溯链接

移植目标是把这两条链接进 seo-ops-system 的网页界面，作为**新文章**的制作通道（旧文章更新通道不变）。旧项目 `/home/laoma/seo-workflow` 绝对只读。

已完成的部分：9 个脚本模块复制进 `data_sources/modules/`、`legacy_sync.py`（DB → 文件系统同步）、`legacy_workflow.py`（阶段机）、MIGRATION_13（`actions.legacy_stage` 列）、8 条路由、`legacy_production.html` 模板、17 个测试。

**测试全绿（114 passed），但没有一个测试碰过真正的执行路径**——它们只覆盖了 `_slugify`、`detect_stage`、`_build_search_prompt`、`sync_all` 这些纯函数。所以缺陷一直没暴露。

## 诊断证据

### P0-1：`LEGACY_PROJECT_ROOT` 未定义

`legacy_workflow.py` 第 51、296、311 行引用 `LEGACY_PROJECT_ROOT`，但模块里只定义了 `LEGACY_MODULES_DIR`、`SITE_DIR`、`PROJECT_ROOT`。实测：

```
$ .venv/bin/python -c "... LegacyRunner(...).run_sync('write_pre_check.py', ['--help'])"
NameError: name 'LEGACY_PROJECT_ROOT' is not defined
```

影响 r1(collect)、r3(scorer)、w0(validate)、w1b(pre-check)、w2(post-process)、w3(register)——即所有调脚本的阶段。路由没有 try/except 包裹 `run_sync`，所以网页直接 500。

### P0-2：AI 调用的方法根本不存在

`legacy_workflow.py:757` 与 `:937`：

```python
provider = build_ai_provider(s)
resp = provider.chat(system=..., user=..., temperature=0.3)
parts = resp.content.split("===BRIEF===", 1)
```

但 `services/ai.py` 里 `OpenAICompatibleProvider` 只有一个方法 `async complete_json(system_prompt, user_payload)`，返回 `AIResponse.content` 是 **dict**，而且强制 `response_format: {"type": "json_object"}`。

所以：`chat` → `AttributeError`；就算有，`.split()` 对 dict 也不成立；而且调用是同步写法、provider 是 async。两处 AI 调用都在 `try/except Exception` 里，异常被吞成页面上一行错误提示。

**这是"写作部分没走通"的直接原因**——它甚至不是配置问题，是接口从来没对上过。

### P0-3：工作区路径与脚本硬编码不一致

5 个脚本用 `Path(__file__).resolve().parents[2]` 推导站点根：

| 脚本 | 位置 |
|---|---|
| `research_collector.py` | 第 33 行 `BASE_DIR`，3 处 `BASE_DIR / website` |
| `write_collector.py` | 第 26 行 `BASE_DIR`，3 处 `BASE_DIR / website` |
| `research_scorer.py` | 第 228 行 `project_root / args.website` |
| `seo_common.py` | 第 310 行 `parents[2] / website` |
| `plan_feedback.py` | 第 45 行 `parents[2] / website` |

解析结果是 `<repo>/laserpointerhub`，但工作区在 `data/legacy_workflow/laserpointerhub`。脚本会打印 `Error: website directory not found` 并 exit 1。

注意 `write_pre_check.py:355` 是从 **draft 路径**反推（`Path(draft).resolve().parents[2]`），在新布局下自然指向 `data/legacy_workflow`，**不需要改**。

### P0-4：`_copy_research_products()` 方向反了

```python
old = LEGACY_PROJECT_ROOT / "laserpointerhub"   # 只读老项目
for sub in ["research", "material-packs", "drafts"]:
    ...拷贝到 workspace
```

这是**从只读老项目往新工作区拷**。老项目 `drafts/` 里有 25 篇历史草稿，一旦执行就会全部灌进工作区，污染阶段检测（`_latest_file` 会挑到老草稿）。脚本本来就应该直接写进工作区，这个函数从设计上就是多余的。

### P1-1：UI 在预检之后死路

`detect_stage()` 只看文件存在性。只要 draft 和 material pack 同时存在，就恒定返回 `w1_draft`：

```python
if draft:
    return "w1_draft", files
```

`w1b_pre_check`、`w2_post_process` 这两个阶段**永远不可能被返回**。而 `actions.legacy_stage` 这列由 `_update_legacy_stage()` 写入，`get_legacy_display_data()` 却从来不读。结果：跑完预检、跑完后处理，页面刷新后又回到"运行预检"那一屏，**永远走不到注册**。

### P1-2：tier 永远为空

`app.py:659`：

```python
tier = request.query_params.get("tier", "")
```

但模板第 96-102 行是表单里的 `<select name="tier">`，POST body 传的。所以 tier 恒为空字符串，预检的字数门槛退化成默认值。

### P1-3：报告全部丢失

模板渲染 `l.get("pre_check_report")`、`l.get("post_process_report")`，但 `get_legacy_display_data()` 从来没写过这两个键。路由拿到脚本 stdout 后直接 `_redirect()`，报告随响应丢弃。运营者看不到预检哪 15 项红了、后处理为什么不过。

模板第 107、117 行还把 `fail_count` 当全局变量用，实际它在 `l` 里——即使有值也渲染不出来。

### P2：工作区静态资产被删

`git status` 显示工作目录里有 34 个已跟踪文件被删除（在 HEAD commit `833fea8` 里还在）：

- 8 个 context：`brand-voice.md`、`style-guide.md`、`seo-guidelines.md`、`writing-examples.md`、`target-keywords.md`、`pain-points-library.md`、`case-studies-library.md`、`external-sources-library.md`
- 26 个 `research/topic-context-*.json`

后果：写作 AI 失去品牌语气与风格规则；`write_collector.post_process` 的 `_load_valid_external_urls` 失去 `external-sources-library.md` 这个外链白名单来源；`research_collector.archive_materials` 没有归档目标。

`workflow_reset.py` 只删派生快照文件，不碰这些——删除来源不明，但 git 里有完整副本，直接恢复即可。

### P3：skill 里的步骤压根没移植

| skill 条目 | 现状 |
|---|---|
| write 段2 修订循环（评分<70 或蚕食≥0.55 → AI 改稿 → 重跑，最多 2 轮） | 完全没有。`stage_w2_post_process` 只回报失败 |
| write 段2 `--force` 蚕食跳过（skill 明写"不要偷偷加，弹窗让用户确认"） | 路由收 `force=1`，界面上没有任何入口 |
| write 段3 回溯链接 AI（读旧文正文 → 挑 1-3 篇 → 写锚文本清单） | 完全没有 |
| research 段3 素材归档 | ✅ 由 `write_collector.register` 内部调用 `research_collector.archive_materials`，无需另做 |

## 运营者决定（2026-07-26）

1. **范围**：完整补齐 P0 + P1 + P2 + P3，做到 write skill 的 1:1 复原
2. **冻结脚本可以改**：用环境变量开关，逻辑一行不动，新 SHA-256 记入文档

## 设计

### A. AI 文本通道

`services/ai.py` 新增：

- `AITextResponse(provider, model, text: str, prompt_sha256)`
- `OpenAICompatibleProvider.complete_text(system_prompt, user_prompt, *, temperature=0.3, max_tokens=None) -> AITextResponse`：不带 `response_format`，超时 600s（3000 词文章远超现有 150s），`max_tokens` 被供应商拒绝（400）时按现有 `response_format` 回退套路重试一次
- `DisabledAIProvider.complete_text` 抛 `AIUnavailable`
- `AIProvider` Protocol 补上该方法

**为什么不复用 `complete_json`**：长篇 Markdown 塞进 JSON 字符串既容易被转义和截断打断，也已知会拉低长文写作质量。JSON 模式适合结构化证据解释（现有用途），不适合成稿。

四次调用全部按现有惯例写入 `ai_runs`，purpose 分别为 `legacy_research_analyze`、`legacy_write_draft`、`legacy_write_revise`、`legacy_backlink_select`。

### B. 子进程管线

`legacy_workflow.py`：

- `PROJECT_ROOT = Path(__file__).resolve().parents[3]`（实测解析为 `/home/laoma/seo-ops-system`）
- `run_sync` 用 `cwd=PROJECT_ROOT`，`env = {**os.environ, "SEO_SITES_DIR": str(workspace.parent.resolve())}`
- 环境变量会自动传递给脚本再 spawn 的子进程（`write_collector` → `content_scorer` / `content_scrubber` / `plan_feedback`），无需额外处理
- 删除 `_copy_research_products`
- 删除 `run_stream`

**偏离 HANDOFF 决定 #7（SSE 实时日志）**：`run_stream` 从未接线到任何路由，且 `AsyncGenerator` 连 import 都没有。这些脚本是确定性解析器，跑完只要几秒；真正慢的是 AI 调用，而 AI 调用在当前 provider 下无法流式。SSE 在这里换不来信息。改为**把每步 stdout 完整落盘到 `reports/` 并在页面上折叠展示**——比滚动日志更可回溯。运营者已知悉此偏离。

### C. 冻结脚本改动

5 个文件，各加一个常量：

```python
SITES_DIR = Path(os.environ.get('SEO_SITES_DIR') or BASE_DIR)
```

把 `BASE_DIR / website` 改成 `SITES_DIR / website`（共 9 处）。`BASE_DIR` 继续用于仓库内路径（`content_scrubber.py`、`content_scorer.py` 的位置，以及子进程的 `cwd`），不动。

不设 `SEO_SITES_DIR` 时行为与改动前完全一致，因此老项目里的用法不受影响（虽然我们本来也不会去跑它）。

### D. 恢复工作区资产

用 `git ls-files -d` 精确恢复被删的 34 个文件，**不碰**那 3 个 ` M` 状态的 DB 同步文件（`internal-links-map.md`、`seo-data-manual.md`、`live_products_report.md`——它们每次 r0 都由 `legacy_sync.sync_all` 重新生成，回滚无意义）。补建 `research/`、`material-packs/`、`drafts/`、`reports/` 四个目录。

### E. 阶段推进修复

`detect_stage(topic, workspace, db_stage=None)`：文件推导的阶段是**下限**；`db_stage` 只有在同时满足「比文件阶段更靠后」和「前置文件确实存在（W 阶段要求 draft 在）」时才允许覆盖。这样既让预检→后处理→注册的链条通起来，又不会因为 DB 里一条陈旧记录把已被删除的产物说成还在。

`get_legacy_display_data` 按当前阶段加载对应报告文件，并从预检报告解析 `fail_count`。`app.py` 的 `/actions` 把 `item["legacy_stage"]` 传进去。

同时修：w1b 路由改从表单读 tier（缺省回落到 `topic-context-{slug}.json` 的 `tier`）；模板里 `fail_count` 改成 `l.fail_count`。

### F. 段2 修订循环

`stage_w2_post_process` 增加 `revise` 路径：

1. 跑 post-process（不带 `--apply`），报告落盘
2. 门控不过且轮次 < 2 → 用 `complete_text` 让 AI 按报告改稿（旧稿备份为 `{slug}-{date}.rev{n}.md`）→ 重跑
3. 轮次记在 `reports/w2-state-{slug}.json`
4. 2 轮后按 skill 分流：**只剩链接问题 → 标出来，允许继续段3**；**评分仍 <70 → 停，标 ⚠️ 需人工审阅，不进段3**

`--force`（蚕食跳过）只在报告显示蚕食 ≥0.55 时才在界面出现，且带确认弹窗。skill 原文："不要偷偷加。用 question 工具弹出选项让用户确认后再加 `--force`。"

### G. 段3 回溯链接

`stage_w3_register` 跑完 register 后：

1. 从 register 报告解析回溯链接候选（脚本已按标签重叠排序、已跳过 `link_counts.blog >= 4` 和已含新 URL 的）
2. 读候选对应的 `published/{slug}.md` 正文
3. `complete_text` 让 AI 挑 1-3 篇，每篇 ≤1 条，写出自然锚句和插入位置
4. 存 `research/backlink-suggestions-{date}.md`（沿用老项目文件名惯例），页面展示

**绝不自动改旧文**——只出清单，等人工审批后手动执行。

### H. 测试

扩 `tests/test_legacy_workflow.py`，全部用假件（fake AI provider + fake subprocess），不打真实 API：

- `SEO_SITES_DIR` 解析（设与不设两种）
- 阶段合并逻辑（DB 靠后 / DB 陈旧 / 前置文件缺失三种）
- tier 从表单读、回落到 topic-context
- 报告落盘与回读
- 修订循环 2 轮封顶、两种分流结果
- 回溯候选解析
- `complete_text` 的 400 回退

## 已核实 vs 待核实

**已核实**（读代码或实跑确认）：NameError、`ai.py` 无 `chat`、5 个脚本的 `parents[2]` 推导、被删文件清单、`detect_stage` 死路、tier 参数错位、模板缺失字段、`write_pre_check` 从 draft 反推根目录、post-process exit 1 = 门控未过、`PROJECT_ROOT` 索引、114 个测试通过。

**待实施时核实**：post-process 报告里评分与蚕食值的确切文本格式（决定 F 的解析写法）；register 报告里候选列表的确切格式（决定 G 的解析写法）；供应商对 `max_tokens` 的实际接受情况。

## 不在本次范围

- 旧文章更新通道（`content_production.py`、`material_workflow.py`）——运营者已确认不动
- `docs/RESEARCH_WRITE_V2_SEO_AI_SEARCH_OPTIMIZATION_PROPOSAL.md` 的 V2 方案——必须先完成 1:1 复原与验收
- 素材库迁移进数据库——先保持文件模式
