# 工作日志

## 2026-07-29 — 证据驱动事实校验最终验收追加修复 (W0 原子写异常路径)

### 背景
287cfd3 修复后，W0 在候选 draft/ledger 校验成功后仍会先 `clear_stage_artifacts('w0')` 删除旧 draft / claim-ledger / w2-state，再调用 `_write_ahead_draft_and_ledger`。若后者的第二次 `os.replace` 失败，旧文件已提前删除，回滚无法恢复，最终丢失旧产物并抛出未处理 `OSError`。

### 完成
- **Fix W0 原子写异常路径**：`stage_w0_validate_and_draft` 改为先调用 `_write_ahead_draft_and_ledger`（该函数内部会 snapshot 旧文件并在失败时 rollback），调用成功后再清理 `w1b` 产物（旧预检/后处理报告）并重置 w2-state。`_write_ahead_draft_and_ledger` 任何异常都被捕获并转为 `success=False` + 明确错误信息，不再向调用方抛未处理异常。
- **测试**：新增 `TestW0AtomicWriteFailure::test_w0_second_replace_failure_preserves_old_artifacts`，预置旧 draft / claim-ledger / w2-state，mock 第二次 `os.replace` 抛 `OSError`，断言 W0 不抛异常、`success=False`、三份旧文件字节级不变。

### 验证
- `pytest tests/ -q`：301 passed（较前次 +1）
- `ruff check src/seo_ops/services/legacy_workflow.py data_sources/modules/write_pre_check.py tests/test_legacy_workflow.py`：5 个 pre-existing F841，无新增

### 未完成 / 遗留
- 未在 action-2 生产数据上验证（按要求）
- W2 revise 备份路径使用原 draft 后缀；与 `_today_str()` 自动滚动后可能错位，需在 UI 提示

## 2026-07-29 — 证据驱动事实校验最终验收修复 (2 项)

### 背景
最终验收发现 f5d46b1 仍有两项未通过：`_run_fact_check` 在 evidence-ledger / claim-ledger 根 JSON 类型非 dict 时会在错误分支调用 `.get()`，导致 AttributeError；`stage_w0_validate_and_draft` 在 AI 输出验证前即清空旧 draft / claim-ledger / w2-state，使 W0 失败时丢失已有产物。

### 完成
- **Fix1 根 JSON 类型校验分离**：`data_sources/modules/write_pre_check.py` 的 `_run_fact_check` 对 `ev_data` / `cl_data` 先单独 `isinstance(..., dict)` 判断；非 dict 时产生结构化 blocking 并立即 `return`，该分支不再调用 `.get()`。dict 但 `version != 1` 时再用 `.get("version")` 构造错误信息。
- **Fix1 测试**（4 个）：`test_evidence_ledger_root_list_blocked` / `test_evidence_ledger_root_null_blocked` / `test_claim_ledger_root_list_blocked` / `test_claim_ledger_root_null_blocked`，分别注入 `[]` / `null` 根类型，断言返回 fail、不抛异常、detail 含 ledger 类型错误。
- **Fix2 W0 失败不删旧产物**：`src/seo_ops/services/legacy_workflow.py` 的 `stage_w0_validate_and_draft` 把 `clear_stage_artifacts(..., "w0")` 与 `claim-ledger-{slug}.json` 的删除移到 claim-ledger 校验成功之后。验证失败时直接返回错误，draft / claim-ledger / w2-state 保持原样。
- **Fix2 测试**（1 修改 + 1 新增）：重写 `test_w0_with_numeric_claim_text_fails_no_file_change` 为断言旧 draft / claim-ledger / w2-state 字节级不变；新增 `test_w0_with_numeric_claim_type_fails_no_file_change` 覆盖 `claim_type` 数字类型场景。

### 验证
- `pytest tests/ -q`：300 passed（较前次 +5：4 个根类型 + 1 个 W0 claim_type）
- `ruff check src/seo_ops/services/legacy_workflow.py data_sources/modules/write_pre_check.py tests/test_legacy_workflow.py`：5 个 pre-existing F841，无新增（较上次少 1 个 `today`）

### 未完成 / 遗留
- 未在 action-2 生产数据上验证（按要求）
- W2 revise 备份路径使用原 draft 后缀；与 `_today_str()` 自动滚动后可能错位，需在 UI 提示

## 2026-07-28 — 证据驱动事实校验第三轮修订 (1 项 fail-closed)

### 背景
第三轮复测发现一处 fail-closed 漏洞：`source_url` / `quote` / `key_finding` 字段类型校验只在 claim 引用 evidence 时执行，未被引用的 evidence 若字段为 `int` / `null` / 其他非法类型，W1b 会错误通过。

### 完成
- **第三轮 Fix evidence-ledger 预校验**：`_run_fact_check` 在 evidence index 构建循环中、加入 `ev_map` 之前，先校验每条 evidence 的 `source_url` / `quote` / `key_finding` 类型与内容：`source_url` 必须 str 且非空；`quote` / `key_finding` 类型必须 str（None 允许）；二者至少一项非空。任一非法即产生结构化 blocking（即使没有 claim 引用该 evidence_id）。
- **第三轮测试**（3 个）：`test_unreferenced_evidence_bad_source_url_blocked` / `..._bad_quote_blocked` / `..._bad_key_finding_blocked`，每个用一份干净且被引用的 `ev_001` + 一个未引用的 `ev_002` 携带 `123` 类型字段，断言 W1b 全部 blocking。

### 验证
- `pytest tests/ -q`：285 passed（较前次 +3）
- `ruff check` 6 个 pre-existing F841，无新增
- HEAD: `c40b823b6853984af2daf08b3670461784352b17`

### 未完成 / 遗留
- 未在 action-2 生产数据上验证（按要求）
- W2 revise 备份路径使用原 draft 后缀；与 `_today_str()` 自动滚动后可能错位，需在 UI 提示

## 2026-07-28 — 证据驱动事实校验第二轮修订 (2 项必修)

### 背景
第二轮复测发现两处必修漏洞：原子写回滚未处理「旧文件原本不存在」场景（旧文件缺失时只能 restore 有内容，无法 unlink 第一次已替换进去的新文件）；`_run_fact_check` 在 `strip`/`[:12]` 之前没有对 ledger 字段做类型校验，非 str 类型（如 `evidence_id=123`）会直接 AttributeError。

### 完成
- **第二轮 Fix1 原子写回滚补齐**：`_write_ahead_draft_and_ledger` 增加 `draft_existed` / `cl_existed` 与 `draft_replaced` / `cl_replaced` 标志；rollback 区分三种情况：旧文件存在 → restore 旧内容；旧文件不存在但被 replace 创建 → unlink；旧文件不存在且 replace 未发生 → 不动。保证 rollback 后状态永远是「两个旧版本（含都不存在）」或「两个新版本」，杜绝「new draft + 无/旧 ledger」。
- **第二轮 Fix2 fact check 字段类型校验**：`_run_fact_check` 中所有 ledger 字段在 strip / slice 之前先 `isinstance(..., str)`：evidence_id、source_url、quote、key_finding、claim_text、claim_type、material_pack_sha256、draft_sha256、evidence_ids 每项。非 str 产生结构化 blocking 项（含字段名 + 实际类型），绝不 AttributeError；evidence_ids 错误信息新增条目索引便于定位。
- **第二轮 Fix1 测试**：`test_second_replace_failure_removes_both_when_neither_existed`：初始 draft + claim ledger 都不存在，第二次 replace 失败后两者必须都不存在（杜绝新 draft 泄漏）。
- **第二轮 Fix2 测试**：7 个字段类型错误测试 `test_evidence_id_int_blocked` / `test_source_url_int_blocked` / `test_claim_text_int_blocked` / `test_claim_type_int_blocked` / `test_material_pack_sha_int_blocked` / `test_draft_sha_int_blocked` / `test_evidence_ids_entry_int_blocked`，每个用 `123` 整型注入对应字段，断言产生结构化 blocking 且 detail 含字段名 + 类型错误字样。

### 验证
- `pytest tests/ -q`：282 passed（较前次 +8：1 个原子写 + 7 个 fact check 字段类型）
- `ruff check` 6 个 pre-existing F841，无新增
- HEAD: `7c351aa97663e008e192fc85e4cb72832f5c4cf6`

### 未完成 / 遗留
- 未在 action-2 生产数据上验证（按要求）
- W2 revise 备份路径使用原 draft 后缀；与 `_today_str()` 自动滚动后可能错位，需在 UI 提示

## 2026-07-28 — 证据驱动事实校验复测修订 (6 项必修)

### 背景
验收复测发现 6 处必修漏洞：W2 revise 未走证据闭环（不解析 claim ledger、不注入 SHA、不原子写）；`_write_ahead_draft_and_ledger` 第二次 replace 失败会留 NEW DRAFT + OLD LEDGER；W0/W1b 句子提取不一致；`_run_fact_check` 非 dict 元素会抛 AttributeError；通过预检/已发布后仍可 revise；缺少真正端到端测试。

### 完成
- **Fix1 W2 revise 走证据闭环**：`stage_w2_revise` prompt 加入 `Evidence References` 段；AI 输出必须含 `===CLAIM_LEDGER===`；复用 `_validate_claim_ledger_json`；服务端注入 `draft_sha256`；原子写 draft + claim ledger；非法输出不修改 draft/ledger/state。备份写在前以免 `_latest_file` 错拿。
- **Fix2 真正原子写**：`_write_ahead_draft_and_ledger` 改为「snapshot 旧内容 → 写 temp → fsync → replace」协议；任一步失败 restore 旧 draft 和旧 ledger。新增故障注入测试 `test_second_replace_failure_rolls_back_both_files`。
- **Fix3 W0/W1b 句子提取统一**：`_validate_claim_ledger_json` 改为段落 + 句子二级拆分，与 W1b `_claim_in_draft` 行为对齐。段落内部的完整句可被 W0/W1b 一致接受，不再允许子串匹配。
- **Fix4 fact check schema fail-closed**：`_run_fact_check` 对 evidence / claims 每项显式校验 `isinstance(item, dict)`；`evidence_ids` 必须为 list；非 dict 元素产生结构化 blocking 项而非 AttributeError；`blocking_items` 移至 evidence 循环前以正确初始化。
- **Fix5 通过后拒绝 revise**：`stage_w2_revise` 顶部新增 `gate_passed` / `applied` 检查；任一为真直接返回 `success=False, error="草稿已通过预检或已发布，不得再修订"`。
- **Fix6 端到端 + 注入测试**：新增 5 个测试类共 14 个测试覆盖 W2 拒绝（gate_passed / applied）、非法 ledger 不改文件、原子写 rollback、段落内句子、非法 evidence / claim 对象、W0→W1b→W2→W1b 真实流程。同步更新两处旧 W2 测试使用新的 claim ledger 协议。

### 验证
- `pytest tests/ -q`：275 passed
- `ruff check src/seo_ops/services/legacy_workflow.py data_sources/modules/write_pre_check.py tests/test_legacy_workflow.py`：6 个 pre-existing F841，无新增

### 未完成 / 遗留
- 未在 action-2 生产数据上验证（按要求）
- W2 revise 备份路径使用原 draft 后缀；与 `_today_str()` 自动滚动后可能错位，需在 UI 提示

## 2026-07-21 — Legacy Research + Write 1:1 复原首次实施

### 背景
运营者确认将当前新文章制作通道（素材确认 → 三次 AI 自动写）替换为旧 research + write Skill 的 1:1 复原。旧文章更新通道不变。作者自动填 `LaserPointerHub`（组织名）。搜索提示词恢复旧 8 段格式，Section 3 从"市场数据"改为"常见误区与真实教训"。

### 完成
- 数据同步层 `legacy_sync.py`：从 SQLite 生成 5 个 Legacy 工作区文件（published-index.json、65 篇 published/*.md、live_products_report.md、internal-links-map.md、seo-data-manual.md）。每次 Legacy 会话启动前自动刷新。
- Legacy 工作流服务 `legacy_workflow.py`：阶段检测（文件系统 12 状态机）、增强 8 段搜索提示词生成（含素材库总结、"what we know"上下文、Section 3 误区替换）、旧脚本 subprocess 包装（同步 + SSE 流式）、AI 集成（严格按 research/SKILL.md Step 0-6 和 write/SKILL.md 结构指令）、全部 R0-W3 阶段转移函数。
- Legacy 工作区建立：5 个静态文件 + 3 个素材库种子（旧项目快照）+ 26 个 topic-context 文件 + 5 个数据库同步文件。
- 数据库迁移 MIGRATION_13：`actions.legacy_stage TEXT`，safe-guard 防止重复添加。
- 网页集成：8 个 Legacy 阶段路由（`/actions/{id}/legacy/stage/r0–w3`），`legacy_production.html` 10 步向导模板，`legacy.css` 样式，`production.html` 修改——有 Legacy 数据的新文章显示向导卡片，旧文章和已生成内容的新文章保持原有 UI。
- 测试：17 个新测试通过（阶段检测 9、搜索提示词 3、数据同步 5），迁移断言更新到 v13，旧测试回归通过。

### 验证
- `ruff check src/seo_ops/services/legacy_sync.py src/seo_ops/services/legacy_workflow.py src/seo_ops/web/app.py`：通过
- `python -m pytest tests/test_legacy_workflow.py -q`：17 passed
- `python -m pytest tests/test_migrations.py -q`：4 passed
- 数据同步实测：65 blogs、15 products、20 GSC rows 正确生成
- 搜索提示词实测：5005 字符，8 段完整，Section 3 正确为"Misconceptions"
- 应用模块加载：OK

### 未完成 / 遗留
- Legacy 路由可访问但尚未真实端到端测试（需要实际触发新文章制作）
- SSE 流式日志端点已定义但未在模板中连接（后续迭代）
- Legacy 工作区文件 `.gitignore` 未配置（103 个文件当前 track 在 Git 中）
- `register` 后的 `--apply` 草稿同步（旧脚本改旧项目文件，复制到工作区）
- 旧项目脏状态可能导致未来 `_copy_research_products()` 行为不确定（建议后续仅从 workspace 同步文件到旧项目，或完全隔离）

### 下一步
1. 重启本地服务到当前源码，用真实新文章制作任务端到端验证 Legacy 全流程
2. 运营者确认 UI 体验和产物质量
3. 迭代：SSE 实时日志连接、作者 UI 改善、异常重试按钮

---

## 2026-07-15 — GSC 活动批次链路复核与旧行动归档修复

### 完成

- 逐表核对 imports、gsc_metrics、analysis_runs、opportunities、actions 与 research_runs，追踪当前工作台、机会、外部调研和执行任务的 GSC 来源。
- 修复当前分析选择：不再只取时间上最新的成功运行，必须匹配唯一活动 GSC 导入；换入新批次但尚未重跑时返回空状态。
- 外部调研复用同一当前分析选择器，防止导入新批次后的空档继续从旧 GSC 查询启动 API 调研。
- 修正真实行动 #1：取消原因此前已写入但 workflow_status 仍误为 in_progress；现已通过正式工作流设为 cancelled。
- 进一步发现 v5 把“当前机会批次”和“可信历史窗口”混为 analysis_active，导致未来正常导入后独立窗口仍永远只有 1。
- 新增无损 v6 quality_eligible：当前机会只看唯一活动批次，质量稳定性累计后续可信历史窗口；明确异常旧批次不参与任何一边。
- 执行页默认隐藏已取消行动，并提供一个“查看已归档”入口保留恢复与审计能力。

### 真实数据核验

- 唯一活动且可信的 GSC 导入为 #5，日期范围 2026-06-22 至 2026-07-14；#1/#2 均为“已排除 · 历史保留”，#5 没有 previous 周期或 2026-06-22 之前的指标。
- 当前分析为 #8，metadata.gsc_import_id=5；17 个当前候选中引用旧 GSC #1/#2 的数量为 0。
- 行动 #1 与旧机会 #169 均为 cancelled；默认执行页不显示旧 $300 任务，归档视图仍可追溯。

### 验证

- 针对 v6 迁移、GSC 导入、质量、机会、调研和行动的 15 项回归测试通过。
- `ruff check src tests tools`：通过。
- `pytest -q`：26 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- 真实数据库无损迁移到 v6，迁移前备份为 `data/backups/seo_ops-pre-v6-trusted-gsc-20260715.db`；#5 标记为当前且可信，#1/#2 均为已排除。
- 真实页面复核：导入页显示当前/排除标签，设置页显示 1 个可信窗口与 2 个已排除；机会、调研、行动和归档页均正常。
- 本单元没有调用 SerpAPI、Firecrawl、Tavily 或 AI，不消耗外部额度。

## 2026-07-15 — 边界扩展七维度补全

### 完成

- 按运营者确认保留七个一级扩展维度，不再增加树的复杂度；将内部检查项补全为受众/角色、场景/目标/生命周期、故障/限制/替代、决策/验证、产品/技术/兼容、法规/安全/地区和相邻站内问题。
- 增加横向筛选器：搜索意图、地区/语言、经验角色、季节/环境条件和内容形式。筛选器只细化研究问题、检索口径和适用边界，不能单独生成图谱节点或文章候选。
- 调研历史目标契约增加横向筛选器快照，使下一轮能够识别相同口径，避免用不同修饰词重复研究同一核心主题。
- 补充 HBR 用户任务地图、Digital.gov 用户旅程以及 Baymard 兼容性和替代/配套产品研究作为方法依据。
- 同步 `docs/CONTENT_WORKFLOW.md`、`docs/TOPIC_GRAPH.md`、`docs/METHOD_GOVERNANCE.md`、ADR-0009、路线图与 Handoff。
- 本单元只调整方法文档；没有修改应用代码、数据库或界面，也没有调用外部搜索或 AI API。

### 验证

- `git diff --check`：通过。
- `ruff check src tests tools`：通过。
- `pytest -q`：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。

### 遗留问题/下一步

- 七个维度、横向筛选器和筛选器快照仍是已确认但未实现的目标；后续随主题图谱和三入口调研一起开发。

## 2026-07-15 — 两阶段内容工作流与边界扩展方法定稿

### 完成

- 根据运营者确认，把第一阶段正式定义为“关键词与主题调研”：内部数据、外部调研和主题图谱联合工作，每轮最多交付 2 篇新文章与 2 篇旧文章建议；没有合格项时不强行补位。
- 将调研入口拆为 GSC 信号、主题图谱缺口和边界扩展三类。查询—页面联合数据仍约束旧页面归因，但不再成为所有新主题研究的前置条件。
- 定义边界扩展引擎：围绕现有分支按新受众、场景/生命周期、故障/限制、决策标准、产品/技术变化、法规/安全变化和相邻站内问题轮换研究，并保存查询、来源、重复及无结果记忆。
- 确认每轮目标预算范围为 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20；预算是上限，缓存不计真实请求，满足停止条件时提前结束。
- 将主题覆盖拆为一个主要文章主题和多个辅助知识主题。安全、波长、功率等只有在构成页面核心任务时才作为主要主题参与完整查重。
- 审查旧 `/home/laoma/seo-workflow` 的 plan、research、write 与写作上下文：保留素材包、SERP 意图、用户痛点、真实来源、链接检查和回溯内链；取消统一 APP/FAQ/视频/CTA、固定字数和链接密度、机械关键词位置、虚构作者/实测及 AI 检测器门槛。
- 形成第二阶段“文章制作”提案：专项素材包、文章专属大纲、草稿/修改稿、自动自审和 CMS 交付包；旧文章分极小修改、局部更新和原主题重写，slug 始终不变。
- 明确内链同时检查本文出链和已有文章回链；外链支持具体事实并优先一手来源；文字重复、意图重复和允许共享的辅助知识分开判断。
- 新增 `docs/CONTENT_WORKFLOW.md`、ADR-0009 与 ADR-0010，确认 ADR-0007；同步主题图谱、架构、数据契约、方法治理、外部来源、项目背景、路线图、README 和 Handoff。
- 修复 ADR-0006 的乱码内容，并明确 ADR-0009 只替代其 GSC-only 种子限制，预算、审计、缓存、来源分工和引用校验继续有效。
- 本单元没有修改应用代码、数据库或用户可见功能，也没有调用 SerpAPI、Firecrawl、Tavily 或 AI。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 当前运行版本仍为 `0.4.1`；文档已明确区分“当前实现”和“已确认但未上线的方法”。

### 遗留问题/下一步

- ADR-0010 的四个运营选择仍待确认：接受后何时开始专项调研、大纲是否可选调整、小修改的默认交付形式、是否需要 AI/自动化说明字段。
- 实现顺序应先做只读主题树和 65 篇文章的主要/辅助主题映射，再做三入口调研、研究记忆、简化卡片和目标预算范围。
- 当前 `/research` 仍只从明确 GSC 查询开始，接受机会仍进入六步 `action-plan-0.3.0`；界面不得把目标方法显示为已经上线。

## 2026-07-14 — 0.4.1 当前 GSC 批次与主题图谱方案

### 完成

- 检查运营者新导入的 GSC 文件：659 条标准化指标，日期范围 2026-06-22 至 2026-07-14；新旧文件在 2026-06-22 至 2026-07-10 的重叠区间一致。
- 按不可变审计原则没有物理删除旧导入；新增 SQLite v5 的 analysis_active，每站点只允许一个 GSC 批次进入当前分析，新导入成功时原子切换。
- 机会引擎、数据质量、首页与调研列表统一使用当前 GSC 批次 / 最新分析，修复旧对比导入被机会引擎优先选择的问题。
- 导入历史新增“当前分析 / 历史保留”用途标记；重复上传旧文件不会把它重新激活。
- 真实数据库迁移到 v5，备份为 data/backups/seo_ops-pre-active-gsc-20260714.db；当前导入为 #5，旧 #1/#2 保留但不参与当前分析。
- 只用当前 GSC 批次创建分析运行 #7：17 个候选，1 个进入组合；旧运行的 32 个候选、2 个组合退出当前工作台。
- 基于旧批次且尚未完成的行动 #1 已取消，机会、基线、执行历史和取消原因仍保留。
- 完成主题图谱方案：界面使用可折叠树，底层使用主父级加 related/别名的概念图；采用“先稳定骨架、后按真实信号增量扩展”，人工操作可选。
- 主题下规划可点击文章、覆盖状态、待确认队列、规范主题去重、接受/拒绝/延后记忆与分支饱和；第一版明确不做自由拖拽的完整图编辑器。
- 新增 docs/TOPIC_GRAPH.md、ADR-0007 与 ADR-0008，并同步架构、数据契约、方法治理、路线图、README、Changelog 和 Handoff。

### 验证

- pytest -q：24 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- ruff check src tests tools：通过。
- git diff --check：通过。
- 真实新导入 23 个日期行合计 61 点击、9,063 曝光；2026-07-14 只有 1 曝光，按未完整日对待。
- 重启真实服务后 /api/health 返回 version=0.4.1；首页、导入、机会、调研和执行方案页面均为 200。
- 本单元没有调用 SerpAPI、Firecrawl、Tavily 或 AI，不消耗外部额度。

### 遗留问题/下一步

- 主题图谱目前是设计，不是已上线功能。先实现只读树和 65 篇文章映射，再做待确认队列，最后增加可选人工细化。
- 当前只有一个活动 GSC 观察批次，且导出末日未完整，质量状态继续为 provisional；不得用它制造高置信长期结论。
- GSC query→page 定向导入/API、周期快照、正文意图复核和 7/28/56 天观察仍待实现。
- 用户提出的简化日常预算范围 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 0–20 尚未调整；当前界面仍是 0.4.0 的默认 4/3/5/1 与旧硬上限，应在下一次简化调研流程时一并处理。

## 2026-07-14 — 0.4.0 预算化多来源主题调研

### 完成

- 新增每轮可配置预算：SerpAPI/Firecrawl/Tavily/AI 默认 4/3/5/1，硬上限 50/50/100/1；预算、真实请求、成功、失败与 24 小时复用分开记录。
- 新增显式“外部主题调研”流程：从最新分析中的明确 GSC 查询开始，SerpAPI 保存 SERP/Trends，Tavily 发现来源，Firecrawl 定向采集公开页面，Flash 只整理已存证材料。
- 新增 SQLite v4 `research_runs/research_run_items/research_candidates` 和 `multi-source-research-0.4.0`；最多保存 8 个 `needs_evidence/blocked` 候选，不改原分数，不自动发布。
- AI 完整引用校验支持唯一运行号缩写和 `external:<运行号>:<数字序号>`；未知、歧义、伪造引用及无 ID 事实不落入候选。
- 工作台把第一方导入 3/3 与外部连接 4/4 分开，显示已存外部证据；新增 `/research` 页面和导航。
- 新增 ADR-0006，并同步 README、架构、数据契约、方法治理、外部来源、路线图、Changelog 与 Handoff。

### 真实 API 验证

- 完整运行 #1 实际调用 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1：SERP 2 次成功，Trends 2 次无结果；Firecrawl 2 次成功、Reddit 1 次 403；Tavily 5 次成功；Flash 1 次成功。部分失败被标记为 `partial`，成功快照保留。
- 修正 Flash 数字尾缀引用后又做两轮最小复测，每轮 SerpAPI 1、AI 1，Firecrawl/Tavily 0；缓存分别复用 2 和 3 份 SERP，再把剩余 1 次预算用于新的明确查询。
- 三轮合计：SerpAPI 6、Firecrawl 3、Tavily 5、AI 3，均低于用户授权的 50/50/100 搜索上限；AI 三次均为 `deepseek-v4-flash`。
- 最终运行 #3 为 `success`，8 个候选中 6 个来自 AI 的证据整理；持久化引用全部是完整 evidence ID，伪造引用为 0，快照密钥扫描泄漏记录为 0。
- 临时最小预算已恢复为日常 4/3/5/1。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：21 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 真实数据库迁移前备份为 `data/backups/seo_ops-pre-v4-20260714T150803.db`；迁移后 `PRAGMA user_version=4`，原导入与机会数据保留。
- 真实服务 `/api/health` 返回 `version=0.4.0`，AI/SerpAPI/Firecrawl/Tavily 均已配置。
- 中断前已在真实 `/research` 完成桌面交互验收；最终复验时六个页面均为 200、预算值/硬上限正确、560px 响应式规则存在。Windows 浏览器沙箱无法重连，因此 390px 新调研页需后续再做一次真实视觉重跑。

### 遗留问题/下一步

- 仍需 GSC query→page 定向导入/API 和独立周期快照；外部主题只是假设，不能自动过新文章门槛。
- research brief、来源人工评级、正文/意图蚕食门槛和 7/28/56 天观察仍未完成。
- Firecrawl 对部分站点可能返回目标站/套餐权限 403；该失败保留但不产生证据。
- 当前只记录请求单位，不把套餐折算成未知货币成本。

## 2026-07-14 — 0.3.0 自动补证据、新文章候选与执行方案

### 完成

- 新增 SQLite v3 无损迁移：`external_runs`、`evidence_items`、`action_steps`，以及行动工作流状态、方案版本和完成时间。
- 新增用户触发的 SerpAPI Google SERP + Google Trends 时间序列采集；页面候选不能用标题猜查询。
- 每次外部运行保存输入 evidence ID、无密钥参数、请求哈希、脱敏不可变响应快照、SHA-256、大小、调用单位、UTC 时间和安全错误。
- 相同站点/来源/purpose/参数哈希的成功响应在 24 小时内复用，避免重复点击重复消费。
- 新增 `external_query_review 0.3.0` 与 `new_article_candidate 0.3.0`。
- 新文章候选把 GSC/SERP/Trends 事实、CMS 词项重叠程序推断和缺失证据分开；没有 query→page 与人工意图复核时保持补证据，SERP 已有本站页面或强重叠时阻塞。
- 新增“执行方案”页面和 `action-plan-0.3.0`：接受时冻结 evidence ID/指标基线，步骤只能按顺序完成，外部补证据步骤必须找到真实 SERP evidence ID，支持重新打开、取消和恢复，仍不自动发布。
- 静态资源增加版本参数；窄屏导航隐藏原生滚动条并保留横向滑动。
- 新增 ADR-0005，并更新架构、数据契约、方法治理、路线图、外部来源说明、README、Changelog 与 Handoff。

### 验证

- `ruff check src tests tools`：通过。
- `pytest -q`：18 passed；仅有既有 TestClient/httpx 第三方弃用提示。
- `git diff --check`：通过。
- 真实服务重启后 `/api/health` 返回 `version=0.3.0`；`/`、`/settings`、`/method`、`/opportunities`、`/actions` 全部 200。
- 真实数据库迁移前备份为 `data/backups/seo_ops-pre-v3-20260714T1307.db`；迁移后 `PRAGMA user_version=3`，原数据保留。
- 迁移后真实库 `external_runs=0`、`evidence_items=0`、`action_steps=0`，确认开发验收没有调用已配置供应商或改动真实运营决定。
- 使用独立临时数据库在 1280px 与 390px 浏览器验收机会页和执行方案页：无页面级横向溢出、行动步骤正常折行、窄屏导航可滑动且无可见滚动条、控制台无错误。

### 遗留问题/下一步

- GSC query→page 定向导入/API 和独立周期快照仍未实现；当前新文章候选不会因此自动过门槛。
- Firecrawl/Tavily 尚未从已保存 SERP 定向建立 research pack；AI 尚未生成结构化 research brief。
- 行动实际修改、发布日期与 7/28/56 天测量/干扰录入页面仍待实现。
- 当前只记录 SerpAPI 请求单位，实际货币成本未知，禁止用套餐推算虚构金额。

## 2026-07-14 — 0.2.0 外部连接与 GSC 可信度门槛

### 完成

- 核实 SerpAPI、Firecrawl、Tavily、Google Trends、GSC API/异常、Google Search Status、Bing AI Performance 和 PageSpeed/CrUX 的官方能力边界。
- 建立外部来源角色矩阵：SerpAPI=SERP/Trends，Firecrawl=页面采集，Tavily=来源发现，AI=证据综合。
- 新增 `/settings`，统一 AI 与外部数据源设置；原 `/ai` 兼容跳转。
- 密钥输入框不回显；空值保留、显式清除；写入 `.env` 并设置 0600。
- 设置 POST 拒绝非 localhost Origin。
- 新增 SerpAPI Account、Firecrawl Credit Usage、Tavily Usage 与 AI Models 连接测试；不消耗搜索或生成额度。
- 对连接响应只保留白名单字段；网络异常不返回原始 URL，防止 SerpAPI 查询参数泄密。
- 新增 SQLite v2 `source_connections` 无损迁移。
- 新增 `gsc_signal_stability 0.2.0` 规则与 GSC 质量摘要。
- 机会引擎升级为 `cold-start-0.2.0`：独立窗口不足时降低置信权重；日期命中官方异常时曝光相关候选改为补证据。
- 新增 `docs/EXTERNAL_DATA_SOURCES.md` 和 ADR-0004。

### 官方核实中的关键发现

- Google 官方记录：日志错误使 2025-05-13 至 2026-04-27 的 GSC 曝光不准确；修复后曝光可能下降，点击不受该错误影响。
- Google Trends 是归一化的相对兴趣，非绝对搜索量；官方 API 公布时仍为极少测试者的 Alpha。
- GSC Search Analytics API 可组合 query/page/country/device 等维度，但顶部数据和每日行数仍有限制。
- Bing AI Performance 的引用次数不表示排名、权威或在答案中的位置。
- Google 官方流量下降排查未把“沙盒”列为诊断项；系统改用可验证原因树。

### 验证

- `ruff check src tests tools`：通过。
- `pytest`：12 passed；另有第三方 TestClient/httpx 弃用提示，不影响本次结果。
- 真实后台服务重启成功；`/api/health`、`/settings`、`/method`、`/ai` 均正常。
- `/settings` 可读取迁移后的连接状态表；`/method` 显示 `gsc_signal_stability`。
- 真实 GSC 当前被判为 `provisional / 观察窗口不足`，符合两个导入来自同一观察时点的事实。
- Git 状态未包含 `.env`、SQLite 数据库、快照或任何密钥。

### 未完成/下一步

- 尚未创建外部调用与响应的不可变快照、参数哈希、成本和 evidence ID。
- 外部结果尚未参与现有机会分数；连接成功只代表凭据可用。
- 用户需要撤销已在聊天中暴露的旧密钥，再自行在设置页输入新密钥。
- 下一单元优先实现 Top 候选的 SerpAPI SERP + Trends 复核与 `external_runs` 数据契约。
- 用户未明确要求接管当前浏览器；按浏览器技能边界只做 HTTP/模板验收，未截图或点击。

## 2026-07-14 — 项目启动与 M0 可运行里程碑

### 完成

- 在 `/home/laoma/seo-ops-system` 建立独立项目。
- 审核旧 `plan/research/write` Skill、评分脚本、65 篇文章、15 个产品和真实 GSC/CMS 导出。
- 确定新系统是网页形式的决策中枢，不是第四个 Skill。
- 选择 FastAPI + Jinja + SQLite，以适应单人本地运营。
- 建立 README、Handoff、AGENTS、架构、数据契约、方法治理、路线图和 ADR 体系。
- 实现不可变快照、导入批次、GSC 全维度指标、CMS 内容快照、规则版本、机会、行动、观察和 AI 调用表。
- 实现 GSC 标准/对比工作簿解析，CTR 统一以 0–1 小数保存。
- 实现 Blog/Product JSON 解析与幂等导入。
- 实现首版机会规则：点击损失、站内相对 CTR、8–20 位临界页面、查询—页面证据缺口。
- 实现先资格门槛、后排序以及防损/最高价值/补证据组合；允许少于 3 项。
- 实现网页首页、导入、机会、方法与 AI 页面。
- 实现 OpenAI-compatible AI Provider；AI 只解释已给证据，不改指标、门槛和分数。

### 真实数据验收

- GSC 对比工作簿：2,220 条标准化指标。
- GSC 标准工作簿：922 条标准化指标。
- CMS Blog：65 条。
- CMS Product：15 条。
- 首轮分析：32 个候选，2 个进入组合。
- 同一文件重复导入不重复写入。
- 原始旧文件保持不变；新系统保存自己的快照与 SHA-256。

### 验证

- WSL Python：3.12.3。
- `pytest`：7 passed。
- `/api/health`：200，返回 `status=ok`。
- `/`、`/imports`、`/opportunities`、`/method`、`/ai` 与 `/static/app.css`：全部 200，页面标题正确。
- 内置浏览器连接连续两次因桌面沙箱初始化错误中断，未完成截图式视觉验收；服务和页面响应正常。

### 下一步

- 定向 query→page 数据导入。
- 行动执行与 7/28/56 天观察 UI。
- 正文/意图级内容蚕食门槛。
- Opportunity → Research brief 的结构化任务契约。

### 风险/备注

- 不修改旧项目。
- 查询与网页 Sheet 不是联合维度。
- AI API Key 未配置，当前只验证 disabled 模式和适配接口。
- 用户未提供订单/GA4 数据，不输出收入预测。

## 2026-07-15 — 主题调研与执行流程 0.5.0

- 物理删除错误 GSC 导入 #1/#2、3,142 条指标、旧分析/机会/调研派生数据与相关快照；当前保留 CMS #3/#4 和干净 GSC #5（659 条）。
- 新增 SQLite v7/v8 主题图谱、研究入口、候选决定与记忆；真实数据同步 119 个节点、80 项主要内容、348 个辅助知识关联，未映射 0。
- 外部调研改为 GSC、主题缺口、七维边界扩展三个独立入口；预算硬上限 10/10/20/20。
- 候选支持值得做、不再推荐、暂时跳过；30 天内不重复推荐暂缓主题。
- 执行页隐藏六步清单，新增一键生成旧文章修改稿或新文章 CMS 内容包；旧文章 Slug 强制锁定。
- 验证：ruff 全量通过；三入口、迁移、外部 API、设置和网页组合 16 passed；执行页专项 3 passed。
- 浏览器视觉自动化因 Windows sandbox helper 初始化失败未能启动；FastAPI 页面集成测试正常。

遗留：

- 推荐组合仍需继续校准到最多 2 新 + 2 旧并保持不足不补位；当前不会强凑四项。
- 内容生成服务已接通与字段校验，但尚未对真实任务消耗 AI 额度生成样稿；首次点击应人工复核事实、链接与 Markdown。

## 2026-07-15 — 0.5.0 推荐与文章制作最终验收

### 完成

- 推荐组合统一为最多 2 篇旧文章 + 2 篇新文章，产品页和诊断线索不占文章席位；任一类信号不足时保持少于 2 篇或为 0，并同步分析摘要中的实际席位数。
- 研究候选经运营者确认“值得做”后才进入新文章席位；泛 FAQ、常见问题集合和大而全指南由 0.5.0 当前规则动态拦截，旧规则版本自动标记 deprecated。
- 文章制作载入真实机会证据、当前页面和站内链接清单；旧文章限制为元数据/局部/同主题重写并锁定 Slug，新文章生成 CMS 八字段并拦截精确标题或 Slug 重复。
- 内链只保留现有站内 URL，外链只保留已存证 URL；每个外部 URL 独立标注官方/研究、社区线索或行业/其他角色，社区来源不得冒充安全、法规或规格权威。
- 工作台、机会页和文章制作页保留简化操作，技术诊断折叠显示，不再要求运营者处理 evidence ID 或六步人工清单。

### 真实数据与额度核验

- 真实库为 schema v8，只保留 CMS #3/#4 与 GSC #5；错误 GSC #1/#2、3,142 条指标、快照和专属派生分析已物理删除。
- GSC #5 有 659 条指标，日期为 2026-06-22 至 2026-07-14；末日不完整且只有一个可信窗口，因此继续标记 provisional。
- 主题树同步 119 个节点、80 项主要内容和 348 个辅助知识关联；当前真实推荐为 2 篇旧文章、0 篇新文章，没有凑数。
- 真实最小调研仅各调用 SerpAPI、Firecrawl、Tavily 和 DeepSeek Flash 1 次，全部成功；模型为 deepseek-v4-flash，本次最终验收没有继续消耗真实 API。
- 真实调研运行 #1 产生 8 个候选，其中泛 FAQ 候选已被当前规则派生为 blocked，其余候选等待运营者决定。

### 模拟流程与判断

- 错误 GSC 场景：可按导入记录单独物理删除；删除后当前分析只读 #5，不再被错误曝光误导，符合预期。
- GSC 信号弱场景：仍可从主题缺口或七维边界扩展启动调研，不形成 GSC-only 死循环，符合预期。
- 无合格机会场景：组合允许 0–4 项并保持 2 旧 + 2 新上限；真实结果 2 旧 + 0 新证明未强填，符合预期。
- 重复/宽泛主题场景：泛 FAQ 无法点击加入建议；具体的功率、波长或安全核心问题不会仅因属于通用知识被误杀，符合主要/辅助主题边界。
- 接受候选场景：只有 passed 机会能建立文章制作任务；接受新主题后席位数同步，旧文与新文分别走正确内容类型，符合预期。
- 内容交付场景：旧 Slug 被程序恢复为原值，虚构内外链被过滤；新文章返回全部八个 CMS 字段和清洗后的 Slug，符合预期。

### 验证

- `.venv/bin/ruff format --check src tests tools`：45 个文件均已格式化。
- `.venv/bin/ruff check src tests tools`：通过。
- `.venv/bin/pytest -q`：40 passed；仅有 Starlette TestClient/httpx 第三方弃用提示。
- 关键页面与业务组合专项测试：18 passed；覆盖导入删除、迁移、三入口、2+2、简化页面、候选拦截和新旧文章内容包。
- `git diff --check`：通过。
- Windows 浏览器沙箱仍无法启动截图式视觉自动化；页面 HTTP、模板内容和窄屏响应式规则已有自动测试，未把截图验收冒充为已完成。

### 遗留增强

- GSC query→page API/定向联合数据与更多独立可信窗口。
- 发布后 7/28/56 天自动提醒、观察录入和规则效果学习。
- 全文语义/文字重复、自动回溯内链、来源人工评级、同源归并与声明—来源逐条校验。

## 2026-07-15 — 真实操作验收暴露的问题

- 运营者访问时发现 8787 仍运行 0.4.1；已停止旧进程并由 Windows Native 隐藏进程托管 0.5.0，health 已确认 0.5.0。该问题说明此前自动测试没有覆盖真实部署版本。
- 运营者运行 RUN #2“主题缺口”：系统自动选择“工程与专业工作”，SerpAPI 2/4、Tavily 5/5、Firecrawl 2/3、AI 1/1，生成 8 个候选；“部分完成”标签没有解释具体失败。
- 运营者在行动 #2 点击“生成修改稿”后失败。AI 调研和连接检测均成功，不能直接归因于连接。
- 该任务输入包含 24,169 字符旧文、80 个站内页面及 54,024 字符链接摘要，固定 60 秒超时存在明显风险。
- content_production 失败不写 ai_runs，网页又吞掉异常并统一提示检查连接，因此本次精确错误不可追溯。
- 本单元没有重试文章生成，也没有继续消耗 AI 额度；问题已登记到 `docs/USABILITY_ISSUES.md`，在真实闭环通过前保持 Open。

## 2026-07-15 — 机会 #367 新文章入口 500 修复

### 完成

- 定位到行动基线把外部调研候选的事实列表误当 GSC 对象读取，导致 `POST /opportunities/367/decision` 抛出 `AttributeError`。
- 新增类型安全的基线指标读取：旧文章继续保留已有 `current` 或 `facts.gsc_query`，新文章事实列表返回空指标。
- 在原有新文章内容包测试中加入真实的事实列表形状，并新增网页 POST 入口回归测试。
- 在当前真实数据库副本重放机会 #367，成功建立行动与 6 个步骤；只读核对真实库仍无 #367 行动，没有半成品。

### 验证

- 新文章服务专项和网页入口专项各 1 passed。
- `.venv/bin/pytest -q`：41 passed；`.venv/bin/ruff check src tests tools`：通过；`.venv/bin/ruff format --check src tests tools`：45 files already formatted。
- 已重启实际 8787 服务，`/api/health` 返回 `status=ok, version=0.5.0`；`git diff --check` 通过。尚需运营者在真实页面复点 #367；旧文章行动 #2 的 U-006 是另一个 P0，仍未解决。

## 2026-07-15 — 0.6.0 文章任务卡与分阶段内容制作（按要求停止测试）

### 完成

- 将“执行方案”收敛为文章任务卡。当前任务只呈现接受、制作、发布三种运营动作；确认发布后从当前列表移入“观察与历史”，并保留重新打开入口。
- 新文章制作改为素材包、大纲与链接计划、前半篇、携带上下文的后半篇、自审/一次修订五个内部阶段；生成物继续输出 CMS 八字段。
- 沿用原 skill 的稳定结构：导语 100–150 个英文词、FAQ 4–5 问、结论 80–120 个英文词；不设置整篇总字数，不虚构作者实测或第一手经验。
- 内链由完整站点索引先筛选最多 12 个真正相关候选，成稿最多 3 个且允许为 0；外链限定为素材包精确 URL，并记录声明、来源角色与 evidence ID。
- 旧文章制作保持原 Slug 与主题意图，也改用筛选后的内链候选；AI 长任务超时改为 150 秒，制作阶段失败会留下脱敏记录并可安全重试。
- 文档版本更新到 0.6.0，架构、内容工作流、ADR、README、CHANGELOG 和交接状态已同步。

### 实际验证状态

- 最终结构和链接规则加入前，相关专项测试曾为 `11 passed`。
- 取消无关内链兜底后，2 个旧测试假数据因假定“必有一个内链”而失败；假数据已调整，但没有复跑。
- 运营者随后明确要求停止测试。因此本单元没有运行最终 `pytest`、`ruff`、语法检查、浏览器、真实 API 或服务重启，不得把源码状态描述为通过验收或已经部署。

### 遗留

- 后续首先验证 `content_production.py` 与测试假数据，再做完整质量检查。
- 重启实际服务并确认 0.6.0；用行动 #2 和机会 #367 各跑一次真实闭环，核对发布后移入历史。
- 发布后 7/28/56 天自动观察提醒尚未实现。
- 旧文章目前仍为一次生成修改稿，没有采用新文章相同的五阶段内部生成。
## 2026-07-16 — 0.7.0 素材确认、可配置 AI 上限与旧写作方法适配

### 完成

- 新增 `material_workflow.py`，在新文章写作前从已有证据生成 A–H 素材清单；整理过程零外部请求、零 AI 调用。
- 关键材料门槛要求用户痛点、权威来源、真实问题、至少两个可追溯来源；敏感主题额外要求官方或研究来源。
- 新增手工素材搜索提示词、任务卡粘贴入口、不可变快照/evidence 记录和内容哈希幂等；未确认素材前禁止写作。
- 新文章改为文章方案、完整初稿、编辑定稿三个正常 AI 阶段；确定性检查不通过时才继续修订。
- 新增 `content_ai_call_limit` 设置：范围 3–10、默认 4、环境变量 `SEO_OPS_CONTENT_AI_CALL_LIMIT`；交付记录实际调用、上限和修订次数。
- 外部主题调研预算继续独立使用 SerpAPI 0–10、Firecrawl 0–10、Tavily 0–20、AI 整理 0–20，不计入文章调用上限。
- 适配原 `seo-workflow/write` 的文章类型篇幅、稳定结构、动态内外链、CMS 八字段和确定性自审；不迁移虚构作者、第一手经验、未知 URL 和机械填充。
- 旧文章继续走独立的一次生成路径，按元数据/极小修改、局部更新、同主题重写交付，锁定原 Slug 和主要意图。
- 当前任务页保留同卡操作；新文章只增加“是否补充手工素材”这一项必要决定，发布后仍移入观察与历史。

### 模拟与判断

- 素材不足：显示缺失分类，AI 调用保持 0，符合“先补素材再写”。
- 手工补充：保存快照但不调用供应商；同一文本再次提交不增加运行记录，符合幂等预期。
- 素材充足：运营者确认后正常调用 `skill_plan`、`skill_draft`、`skill_edit` 三次并生成 CMS 八字段。
- 自检返工：只在存在阻塞项且未达到上限时调用修订；默认 4 允许一次返工，设置 10 最多允许七次返工，不会为了用额度主动继续。
- 预算隔离：文章调用次数不减少外部调研四项预算，符合两条数据链路分别计量。
- 真实数据库只读检查：行动 #4 的素材预览 ready=true，56 个来源，A–H 均有内容；当前没有待生成的新文章行动，因此未进行真实新文生成。

### 验证

- `.venv/bin/pytest tests/test_action_workflow.py tests/test_settings.py -q`：12 passed。
- `.venv/bin/pytest -q`：42 passed；只有 Starlette TestClient/httpx 第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`：通过。
- `.venv/bin/ruff check src tests tools`：通过。
- `git diff --check`：通过。
- 本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI；已启动 8787，health 返回 0.7.0，并实际核对设置页参数与文章任务页说明。

### 遗留

- 使用第一篇真实新文章核对耗时、模型返回质量、CMS 八字段、FAQ/JSON-LD、内外链和发布后移入历史。
- 7/28/56 天自动观察、GSC query→page/API、全文语义重复、自动回溯内链和声明—来源逐条校验仍待增强。

## 2026-07-16 — 0.8.0 五步顺序页面与单一卡片归属

### 完成

- 将日常界面重构为五步：数据导入、主题调研、文章建议、文章制作、主题图谱；根地址进入第 1 步，“今日工作台”退出主流程。
- 新增 188px 窄侧栏和响应式五步导航；文章建议、文章制作都按旧文章左列、新文章右列排列。
- 第 2 页只保留三种调研入口、本轮预算、运行用量与结果概况；候选标题和决定操作全部移到第 3 页。
- 第 3 页只读取已保存数据且不触发 API；每列最多突出 2 篇、折叠其后 5 篇，仅提供执行、暂时跳过、不再推荐。
- 第 4 页只显示已接受的当前任务；执行后卡片从第 3 页移入第 4 页，确认发布后立即退出当前制作页。
- 调研选择不再创建候选图谱节点；只有文章真实发布并随 CMS JSON 重新导入后，图谱才显示覆盖。
- 导入 GSC 或 CMS 成功后自动重算内部旧文章机会，不调用外部 API；同 URL 多条规则合并展示，已完成旧文等 CMS 与 GSC 都回流后才允许再次评估。
- 修复素材表单误绑定步骤路由；新增清理服务和页面流程回归测试。
- 新增 ADR-0012，并同步 README、架构、内容工作流、方法治理、CHANGELOG、交接与本问题清单。

### 真实清理与重建

- 执行前预览确认会删除 13 个分析运行、222 个机会、4 个行动、3 个调研运行、27 个派生外部运行、22 条证据、11 个 AI 运行、14 条研究记忆、1 个候选节点和 26 个派生快照。
- 实际按该范围完成物理清理；保留 3 个有效导入、80 项内容、659 条当前 GSC 指标、连接设置及永久“不再推荐”决定。
- 重建主题图谱得到 119 个节点、80 个主映射、348 个辅助关联、0 个未映射内容。
- 重新分析得到运行 #1、17 个内部候选；真实第 3 页为 2 篇优先旧文章、0 篇新文章。新文章为 0 是因为清理后尚未重新发起外部调研，不是错误。

### 验证

- `.venv/bin/pytest -q`：45 passed；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`：49 files already formatted。
- `.venv/bin/ruff check src tests tools`：All checks passed。
- `git diff --check`：通过。
- 重启真实 8787 服务后，`/api/health` 返回 0.8.0；根地址正确进入数据导入，五个主页面、设置、方法和样式均返回 200。
- 真实页面核对：文章建议显示 2 篇优先旧文和折叠候选，新文章栏明确为 0；文章制作两栏均为空；主题图谱显示 80 项真实内容映射；调研页没有旧候选或决定按钮。
- 应用内浏览器仍因 Windows 沙盒连接故障无法完成截图式复验；真实 HTTP、45 项页面/服务测试及 1080/820/560px 响应式规则已核对。
- 本轮没有调用真实 SerpAPI、Firecrawl、Tavily 或 AI。

### 遗留

- 用第一篇真实旧文章和第一篇新文章分别核对 AI 质量、CMS 八字段、FAQ/JSON-LD、内外链与发布后退出当前制作页。
- 增加运行前自动方向预览、暂时跳过/不再推荐的撤销入口，并修正主题缺口候选仍引用 query→page 的旧提示。
- 后续实现 7/28/56 天观察提醒、GSC query→page/API、全文语义重复、自动回溯内链和声明—来源逐条校验。

## 2026-07-16 — 0.8.1 侧栏文字换行修复

### 完成

- 定位到基础样式 `.nav-item span { width: 22px; }` 同时命中了图标和文字容器，导致五步中文标题只有约两个字的可用宽度。
- 在工作流侧栏用更精确的选择器把文字容器恢复为自适应宽度、左对齐和单行显示；188px 窄侧栏保持不变。
- 版本升至 0.8.1，使静态样式地址变化，普通刷新即可避开旧缓存。
- 在五步流程测试中加入实际 CSS 响应及 `width: auto`、`white-space: nowrap` 回归检查。

### 验证

- `.venv/bin/pytest tests/test_simplified_workflow.py::test_five_step_navigation_and_one_card_one_home -q`：1 passed。
- `.venv/bin/pytest -q`：45 passed；仅有 Starlette TestClient/httpx 的第三方弃用提示。
- `.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools`、`git diff --check`：通过。
- 实际服务已重启到 0.8.1；页面引用 `workflow.css?v=0.8.1`，线上样式包含自适应宽度和禁止换行规则。
- 应用内浏览器仍被 Windows 沙盒连接故障拦截；本轮通过真实 HTTP、CSS 响应和自动回归核对。

## 2026-07-17 — 0.9.0 GSC 只读 OAuth、联合证据与内容质量修复

### 背景与判断

- 运营者真实测试发现：主题调研给出中文候选 #7“绿色与红色激光笔在演示中的可视性对比”，而 CMS #46 已有 `Red vs. Green Laser for Presentations: Why Your “Brighter” Laser Isn’t Visible`；该候选仍进入新文章链路。
- 旧文章与新文章成稿质量均退化。核对后确认关键原因不是五步框架，而是页面汇总被过早解释为写作证据、旧文单次生成缺少独立审校、候选语言未约束、规范主题去重和保存前质量闸门不足。
- 运营者确认 GSC 可自动同步，但 Blog/Product JSON 继续手工导入；2026-06-22 以前的数据必须永久排除；实施前先备份并清理受影响的新旧文章派生数据。

### GSC OAuth 与数据契约

- 新增 installed-app OAuth 2.0 + PKCE + 本机 loopback 接入，唯一 scope 为 `webmasters.readonly`；不支持 API Key。
- 客户端 JSON 与 token 只从本地路径读取；SQLite 仅保存站点属性、权限、可信起始日与同步审计，不保存 client secret、access/refresh token 或授权码。
- 新增 schema v9：`gsc_connections`、`gsc_sync_runs`、`gsc_query_page_metrics`；删除 GSC 导入时联合行级联删除，同步审计保留且 `import_id` 置空。
- 同步只使用 `dataState=final`、`type=web`，按 25,000 行分页，并分别保存 date 汇总、当前最多 28 天、边界内完整上一 28 天和 `date + query + page` 联合行。
- `SEO_OPS_GSC_TRUSTED_START_DATE=2026-06-22` 是所有请求与存储窗口的代码级硬下界；首次同步前不会自动调用 Google。
- 数据导入页增加连接、回调、站点自动匹配、同步状态和一键同步；GSC Excel 明确为无联合证据的后备，Blog/Product JSON 保持手工入口。

### 机会、调研与内容规则

- 页面汇总只生成 `needs_evidence` 诊断；当前 query + page 联合行存在时才允许 CTR/striking-distance 进入执行，click-loss 同时要求当前与上一窗口联合行。
- 旧文制作前再次核对当前 CMS、通过的机会门槛和所需联合行；缺证据时不调用 AI，防止旧状态绕过规则。
- `multi_source_topic_research 0.6.0` 要求读者可见候选语义字段为自然英文；含中文/混合语言候选丢弃，并依次检查 CMS 主要主题、历史候选和本轮候选。
- 修正规范主题别名与停用词，并用真实红绿激光演示重复建立回归；topic_gap/boundary_expansion 不再被错误要求普遍补 query→page。
- 旧文章正常使用“草稿 + 独立编辑审查”，随后按允许修改类型做确定性检查；自然英文、slug/主题锁、内链白名单、URL 唯一、填充和虚构经验为阻塞项。
- 新旧文章最终仍有阻塞项时只在每篇调用上限内定向修订，耗尽后不保存交付包；元数据常见字符区间降为编辑提示，不冒充 Google 硬规则。

### 凭据移动、备份与真实数据清理

- OAuth JSON 从项目根移动到 `.secrets/google/gsc-client-secret.json`；`.secrets/` 与目录权限为 0700，client 文件和 `.env` 为 0600。
- `.gitignore` 覆盖 `.secrets/`、`client_secret_*.apps.googleusercontent.com.json` 和 `*:Zone.Identifier`；client 与未来 token 路径均通过 `git check-ignore -v`。
- 清理前用 SQLite backup API 创建 `data/backups/seo_ops-before-0.9.0-20260717T032918Z.db`；`quick_check=ok`，SHA-256 为 `f74f2fc21bf9dcbfc8d6c8b419cb9f1401eaa81cdf1ca066477226fd86865feb`。
- 预览后删除 1 个分析、18 个机会、2 个行动、1 个调研运行及 8 个候选、12 个外部运行、9 条证据、2 个 AI 运行、4 条研究记忆和 12 个派生快照。
- 保留 3 个不可变导入、659 条 GSC 指标、80 项内容及其原始快照；清理后联合指标、分析、机会、行动、调研、外部证据、AI 与研究记忆均为 0。
- 真实库迁移至 schema 9 后 `quick_check=ok`、外键违规 0；数据库字节检查未发现 OAuth client ID/secret。token 尚不存在，必须由运营者首次授权生成。

### 验证

- `.venv/bin/ruff format src tests tools`：53 files left unchanged。
- `.venv/bin/ruff check src tests tools`：All checks passed。
- `.venv/bin/pytest -q`：完整测试进程退出码 0；`.venv/bin/pytest --collect-only -q -o addopts=''` 收集 51 项。
- `.venv/bin/pytest -o addopts='' --disable-warnings -rA tests/test_gsc_oauth.py`：2 passed。
- 自动回归覆盖 readonly scope、PKCE、token 权限/数据库隔离、可信日期边界、final 数据、分页联合行、无联合证据拒绝旧文、中文成稿不保存、英文候选和真实重复拦截。
- 真实数据库复核：schema 9，`quick_check=ok`，外键违规 0；imports=3、gsc_metrics=659、content_items=80，其余本轮清理目标和 OAuth 表为 0。
- 本轮未调用真实 Google、SerpAPI、Firecrawl、Tavily 或 AI；随后因运营者反馈页面无法打开，启动 0.9.0 后台服务并核对 `/api/health` 为 0.9.0、`/imports` 为 200 且显示 Google 连接入口。首次浏览器授权、真实 GSC 同步和真实新旧文章质量仍需运营者执行。

### 遗留与下一步

- 启动 0.9.0 后在第 1 页完成 Google 授权，确认系统匹配的站点属性正确后再一键同步；确认请求不早于 2026-06-22。
- 用同步后的联合数据重新生成建议，再真实复验 U-006/U-009/U-010；自动测试不能关闭真实使用问题。
- 发布后 7/28/56 天自动观察、全文声明—来源逐条核验和跳过/不再推荐撤销入口仍待后续。

## 2026-07-17 — 0.9.0 真实 OAuth、根站点同步与调研规则二次修复

### 真实故障与判断

- 运营者完成 OAuth 后要求实际检查结果；页面可打开问题处理后，真实导入页显示连接属性为 `https://laserpointerhub.com/p-B017.html/`，最近同步只有 23 条汇总、0 条 query + page 联合行。
- Google Sites API 只读核对发现账号同时拥有根站点 `https://laserpointerhub.com/` 与单页 URL-prefix 属性；旧选择器把两者评为同分，再按字符串选择了单页属性。
- 运营者随后指出调研概况似乎消失且主题仍像课堂激光笔。实际 HTTP 证明调研概况仍在；重复课堂方向来自前次工作流清理误删 `topic_research_memory`，而 CMS 重叠规则只看标题/摘要，没有识别旧文章正文小标题已覆盖的子题。
- 指定“摄影与光绘”真实运行后得到 8 个英文候选，但 CMS 已有 `Laser Pointer Light Painting: The Complete Photography Guide`，其正文小标题明确覆盖相机传感器、基础设置、激光选择、衍射帽和长曝光；`20-60-20 rule` 又偏离激光光绘分支。这证明仅统一英语和标题去重仍不足以保证主题质量。

### 限定范围内的修复

- 保持五步导航、页面归属和调研概况布局不变；只修改 GSC 属性选择、调研记忆、CMS 覆盖和候选方向规则。
- GSC 自动选择现在只接受 domain property 或 URL-prefix 根路径；子路径/单页属性既不能被自动选中，也不能在后续同步时被已保存连接绕过。
- 工作流派生数据清理不再删除 `topic_research_memory`；删除旧研究运行时仅把记忆的 `research_run_id` 置空，避免自动选择立刻重复同一方向。
- `new_article_candidate 0.4.1` 把标题、Slug、SEO 主身份与摘要/SEO 字段/Markdown H2–H6 小标题分开计算；结构化覆盖达到门槛时阻断新建，正文全文不进入 AI 的站点索引载荷。
- `multi_source_topic_research 0.6.1` 增加所选分支相关性门槛；完全偏离本轮分支的相关问题不会写入候选。
- 增加根站点优先、单页拒绝、结构化小标题覆盖、分支偏离、调研记忆保留和原调研概况仍显示的回归。

### 真实数据操作

- 在真实复测前创建 `data/backups/seo_ops-before-e2e-repair-20260717T045608Z.db`；删除错误 GSC 导入及其 23 条汇总、1 个专属分析和快照，保留同步失败审计。
- 将连接属性改回已经 Google Sites API 验证的根站点后真实同步：sync #2 / import #6，实际日期 2026-06-22 至 2026-07-14，5 次 Google 请求，542 条汇总、1,188 条 query + page 联合行。
- 用 #6 真实重算得到 analysis #1：18 个内部机会，第 3 页有 16 个可执行旧文候选并只突出 2 个优先项。
- 删除旧课堂调研运行及其派生外部/AI/候选，但保留 4 条方向记忆；随后通过真实 `POST /research/run` 指定摄影与光绘，RUN #1 实际调用 SerpAPI 4、Firecrawl 3、Tavily 5、AI 1，保存完整成功/失败概况。
- 用新规则离线回放 RUN #1 的 8 个候选：#2/#4/#5/#6/#7 被现有光绘正文覆盖，#8 偏离分支；仅 #1 和 #3 保持 `needs_evidence`。
- 清理候选前用 SQLite backup API 创建 `data/backups/seo_ops-before-candidate-filter-20260717T053729Z.db`；随后仅删除 6 个 pending 候选并把运行/记忆计数同步为 2，不重跑、不再次消费外部 API。

### 实际页面复验

- Windows 侧 `/api/health` 返回 `version=0.9.0`。
- `/imports` 返回 200，显示根属性 `https://laserpointerhub.com/`、可信起始日 2026-06-22、最近同步联合明细 1,188 条；Blog/Product JSON 仍为手工导入。
- `/research` 返回 200，保留 RUN #1、方向“摄影与光绘”、四个供应商用量、失败原因和“保存了 2 个主题线索”。
- `/opportunities` 返回 200，旧文栏 16 个可选/突出 2 个，新文栏只显示 `Common mistakes in laser light painting` 与 `Laser pointer alternatives for light painting`；没有课堂激光笔、中文或上述 6 个错误候选。

### 验证

- GSC/调研/五步流程针对性回归：15 passed。
- 第一次全量测试发现 2 个旧版本号断言仍期待 0.4.0/0.6.0；没有业务逻辑失败。同步为 0.4.1/0.6.1 后重新运行完整测试，55 项全部通过。
- `.venv/bin/ruff format --check src tests tools`：53 files already formatted；`.venv/bin/ruff check src tests tools`：All checks passed。
- 真实库最终复核为 `quick_check=ok`、外键违规 0；imports=4、联合行=1,188、机会=18、调研运行=1、候选=2、行动=0；根属性为 `https://laserpointerhub.com/`，凭据值未写入数据库。
- 应用内浏览器因 Windows 沙箱故障无法截图式自动化；没有改用未授权的浏览器框架，实际页面改由 Windows 侧 HTTP 和自动模板测试核对。

### 遗留与下一步

- 本轮没有再次生成真实 AI 新文或旧文修改稿，因此 U-006/U-009 仍不能关闭。
- U-010 的“中文/重复/偏题调研候选”已用真实 RUN #1 页面复验解决，但词形规则不是完整语义模型，后续仍需观察新的真实样本。
- 下一步只需从当前第 3 页各选一篇旧文和新文进行真实成稿质量复验；不要重新设计五步架构，也不需要重复当前 GSC 或调研调用。

## 2026-07-17 — 0.10.0 调研资格门、真实回放与产品链接修复

### 背景与范围

- 运营者要求不再处理旧数据细节，优先改善调研产生的新旧文章主题质量，并要求修改后真实调研验证；同时指出主题图谱产品链接错误。
- 五步主流程保持不变。本轮只收紧调研候选资格、重复/灰色归类、旧结果失效和产品 canonical；没有重新设计导航、GSC 或文章制作架构。

### 实现

- 新增 `candidate_qualification 0.9.2`：候选分开保存需求、缺口、页面材料和最接近旧文检查。只有三门同时通过且关系为独立/相邻未覆盖时才是 `qualified`。
- 需求只接受 GSC、SERP/PAA 和 Trends evidence；Tavily/论坛是资料发现，Firecrawl 是页面材料，二者不能冒充搜索需求。材料至少需要两个 evidence、两条 URL 和一份页面采集；敏感主题必须有官方或研究来源。
- 最接近旧文使用去除领域通用词后的核心意图词、词形归一、标题身份、结构化小标题和正文覆盖；修复“按使用场景选颜色”误匹配功率文章而不是颜色文章的反例。
- 同意图或强正文覆盖归入旧文章判断；灰色关系进入人工复核。人工只能确认页面职责，不能补证据；规则/CMS/证据指纹变化后旧资格失效。
- 失效的 pending 候选不再阻塞新一轮发现；只由失效候选产生且尚未开始的机会/行动在启动或 CMS 变化时取消。真实旧行动 #1 已移入 cancelled，历史和证据保留。
- SQLite v10 增加资格状态与审计字段；v11 将 LaserPointerHub 产品模板和既有 canonical 修复为 `/p-{sku}.html`。主题图谱只映射活动内容。
- 文章建议页只把 `qualified` 放入新文章栏，另显示灰色人工复核、旧文归类及证据不足/失效计数；打开页面不调用外部 API。

### 真实数据与调研

- 修改前用 SQLite backup API 创建 `data/backups/seo_ops-before-research-quality-20260717T132353Z.db`，原始导入与快照均未删除。
- 恢复 OAuth GSC #6 为唯一活动批次；意外测试导入 #7/#8 的 3 项测试内容设为 inactive，原记录和快照保留。重建后活动内容为 65 Blog + 15 Product，图谱映射 80 项。
- 沙箱内 RUN #4 因网络策略全部失败，系统正确保存失败记录且候选为 0；随后经授权真实运行 RUN #5，预算为 SerpAPI 2、Firecrawl 2、Tavily 3、AI 1。
- RUN #5 实际成功为 SerpAPI 1/2、Firecrawl 2/2、Tavily 3/3、AI 1/1，得到唯一主题 `DIY laser pointer storage cases: repurposing containers for protection`。
- 该线索有页面材料但没有引用 SERP/PAA/GSC 的需求事实；逐段对照现有 `Laser Pointer Case & Storage Guide` 后，正文已覆盖无盒、软袋、硬盒、泡棉、防尘、撞击与误触等页面职责。最终状态为 `blocked + covered_existing`，本轮新文章为 0，不创建新 URL。
- RUN #5 的 7 个外部响应快照逐字节检查当前四个 API 密钥，泄漏为 0。

### 产品链接与页面验收

- 真实库 15 个活动产品 canonical 全部为 `/p-{SKU}.html`；主题图谱实际 HTML 中有 15 个 SKU 产品链接，`/products/` 残留为 0。
- 8787 已被旧 0.9.0 进程占用，因此按端口占用规则在 `http://127.0.0.1:8788` 启动 0.10.0。
- 真实 HTTP：`/api/health`、`/opportunities`、`/actions`、`/topics` 均为 200；文章建议页显示 0 个合格新主题和 1 个旧文归类。

### 验证

- `.venv/bin/pytest -q`：61 项全部通过；仅有 FastAPI TestClient/httpx 的第三方弃用警告。
- `.venv/bin/ruff format --check src tests tools`：53 files already formatted。
- `.venv/bin/ruff check src tests tools`：All checks passed；`git diff --check` 通过。
- 真实库：schema 11，`quick_check=ok`，外键违规 0，活动 GSC 仅 #6，活动内容 65 + 15，错误产品链接 0。

### 遗留与下一步

- 当前新文章为 0 是资格门的预期结果，不应为了测试写作或维持发文量而降低门槛。下一步优先从 16 个真实旧文建议中选择一篇有 query→page 联合证据的页面，复验旧文成稿质量。
- U-006/U-009 的第一篇真实旧文/新文成稿质量仍未关闭；新文必须等待未来出现 `qualified` 主题。
- 8787 仍运行旧 0.9.0；本轮没有终止未知来源的旧进程，0.10.0 使用 8788。

## 2026-07-17 — 0.10.1 新主题发现前沿、证据 lineage 与真实复验

### 问题定位

- 运营者明确指出“现状无法发现新主题，理想状态必须能发现新主题”。保留 0.10.0 的严格
  资格门，单独检查发现阶段，没有用降低需求/缺口/材料门槛换数量。
- 真实执行显示四个独立瓶颈：SerpAPI 会把预算用于第一个查询的 SERP + Trends；Tavily
  被第一组泛化 PAA 占满；Firecrawl 连续抓同一资料查询的前三条；分支相关性要求同时
  命中 presentation + classroom，导致只命中一个明确场景词的有效候选在资格前被丢弃。
- 后续真实样本又暴露查询超过 100 字符、Title Case `Laser` 被解析成 `aser`、
  `alternatives` 内的 `na` 被误判为 `N/A`、`External:45` 大小写导致事实丢失，以及产品
  规格文本误判为旧文章正文覆盖。

### 实现

- `multi_source_topic_research 0.7.3` 先把 SerpAPI 预算分配给不同具体前沿；图谱/边界入口
  先逐条为前沿补 Tavily 来源，GSC 入口继续优先补真实 PAA；需求问题按 SERP 轮询，
  Firecrawl 按不同资料查询和结果名次轮询。
- Tavily 与 Firecrawl 的 `input_refs` 形成需求 → 资料发现 → 页面采集 lineage。确定性 PAA
  候选会在精确资料查询完成后附加 source evidence，再只附加该 source 产生的 page capture；
  AI 候选同样不能沿宽泛 SERP 混入无关正文。
- 待补证据候选取得更强证据后可以让旧 pending 判定标记 `stale` 并写入新结果；相同证据
  仍不足时不重复堆积候选。
- 分支门降为命中一个明确核心场景词，同时要求保留 `laser` 站点锚点；受控归一 educator、
  teaching、school 为 classroom。泛 presentation mistakes 因脱离激光对象被阻断。
- `candidate_qualification 0.9.4` 修正 intent 占位词边界和 evidence 大小写规范化；最接近
  旧文章只比较活动 Blog 正文，Product 继续用于图谱和内链但不再阻断信息文章。
- 新增 ADR-0015；源码版本升为 0.10.1，五步导航、GSC、文章制作和 SQLite schema 均未重构。

### 真实运行

- RUN #6 在第二个边界查询因超过供应商 100 字符上限中断；改为按词边界截断后保存回归。
- RUN #8：SerpAPI 实际 3/成功 4/复用 1，Tavily 5/5，Firecrawl 3 次中 2 成功，AI 1/1；
  发现 `Alternatives to Laser Pointers for Classroom and Presentation Use`，但正文只支持学校
  安全而非替代工具，正确保持 `needs_evidence`。
- RUN #9：4 个 SERP 全部复用成功，Tavily 实际 5、Firecrawl 3/3、AI 1/1；资料和正文已
  跨前沿分散，但 AI 的 3 个主题全部被旧 0.50 分支门误删，由此定位而非继续增加 API。
- RUN #10：4 个 SERP 复用、Tavily 实际 5、Firecrawl 实际 3、AI 1，保存上限 8 个候选。
  最终严格重判中，`Why are laser pointers not allowed in school?` 同时具备真实 PAA、精确
  source discovery、LIA 页面正文和未覆盖 Blog 缺口，状态为 `qualified + new_article`。
- RUN #10 的泛 `What are some common mistakes to avoid in presentations?` 被站点锚点阻断；
  PowerPoint 虚拟指针纠正为待补需求事实，不再被天文/SOS 产品规格误判为旧文覆盖。
- 当前真实 pending 候选汇总为 1 qualified、5 needs_evidence、13 blocked；没有为了数量
  接受证据不足、正文不匹配、旧文已覆盖或偏离站点对象的候选。

### 验证

- 调研与迁移针对性回归：22 项全部通过，覆盖多前沿分配、查询长度、跨资料查询抓取、
  无 AI 的 PAA 资格、证据升级失效、大小写 evidence、alternatives intent、Title Case
  分支、站点偏离和产品不冒充旧文章。
- `.venv/bin/pytest --collect-only -q -o addopts=''`：67 tests collected；全量 pytest 退出码 0。
- `.venv/bin/ruff format --check src tests tools`：53 files already formatted；
  `.venv/bin/ruff check src tests tools` 与 `git diff --check` 通过。
- 8788 已重启为 0.10.1；`/api/health`、`/opportunities`、`/actions`、`/topics` 均为 200，
  文章建议页实际包含合格学校禁用原因主题。主题图谱 HTML 有 15 个 `/p-{SKU}.html`
  产品链接且 `/products/` 为 0；8787 旧进程未改动。
- 真实库 `quick_check=ok`、外键违规 0、schema 11、活动内容 65 Blog + 15 Product；活动规则
  为 `multi_source_topic_research 0.7.3` 与 `candidate_qualification 0.9.4`。

### 遗留与下一步

- 当前已经有 1 个合格新主题，下一步可进入文章制作复验英文成稿；仍不得把 5 个
  `needs_evidence` 主题人工放行。
- PowerPoint 虚拟指针方向值得后续补一条能引用 PAA/SERP 的需求事实；现有资料和页面材料
  已保存，不需要把产品页误当文章覆盖。
- U-006/U-009 的新旧文章实际成稿质量仍需分别复验；本轮只验证到调研与文章建议边界。

## 2026-07-17 — 0.10.2 自然语言假设、运营禁区与真实复验

### 完成内容

- 将图谱/边界入口从“分支标签 + 通用词”改为自然语言验证假设；假设只决定 SERP 检索问题，
  不作为需求或文章事实。确定性候选保留该种子与 SERP、Tavily、Firecrawl 的 lineage。
- 新增学校/课堂/未成年人及明显伤害、武器化、破坏性用途的运营禁区；明确没有禁止高功率主题。
- 修正分支门：保留 laser 站点锚点和核心场景关系，但不要求标题机械重复标签；夜钓等跨分支
  内容仍会阻断。
- 技术子题覆盖需命中特定技术对象的旧文标题、小标题或正文；仅有 high-power、laser、safety
  等泛词时改为人工复核。
- 同意图且材料齐全的调研候选会生成 `research_existing_content_gap` 旧文优化机会，且明确标注
  为 CMS 意图比对，不声称 GSC 查询属于该页面。
- 新增 ADR-0016，规则更新为 `multi_source_topic_research 0.8.0` 与
  `candidate_qualification 0.9.5`；源码版本升为 0.10.2，SQLite schema 未变化。

### 真实验证

- 隔离数据库用途分支：SerpAPI 2/2、Tavily 2/2、Firecrawl 2/2，保存 8 个候选。只有
  `How to Use a 405nm Laser Pointer for Fluorescent Minerals` 与
  `Are Laser Pointers Safe for Dogs and Other Pets?` 为 `qualified + new_article`；其余 6 个
  因材料或意图关系不足保持 `needs_evidence`。
- 隔离数据库高功率安全分支：三个外部来源各成功 1 次。`How to Safely Terminate a High-Power
  Laser Pointer Beam` 为 `needs_human_review`，没有被禁区阻断；Class 3R 安全线索为
  `blocked + covered_existing`。
- 生产候选重判结果：`blocked: 19`、`needs_evidence: 4`；3 个学校主题全部阻断。`PRAGMA
  quick_check=ok`，外键违规 0。
- 随后在生产库实际运行用途分支 RUN #15：SerpAPI 2/2、Tavily 2/2、Firecrawl 2/2、AI 0。
  第 3 步现有 `qualified: 2`、`blocked: 19`、`needs_evidence: 10`；两个可见新文章建议为
  `How to Use a 405nm Laser Pointer for Fluorescent Minerals` 与
  `Are Laser Pointers Safe for Dogs and Other Pets?`。另 6 个狗相关长尾问题均保持待补证据。

### 验证与遗留

- `.venv/bin/pytest tests/test_research_workflow.py -q -o addopts=''`：21 passed。
- 新增回归覆盖学校/未成年人禁区、高功率未禁、泛词不得冒充技术覆盖、自然种子，以及同意图线索
  进入旧文优化机会；`.venv/bin/ruff format --check src tests tools`、
  `.venv/bin/ruff check src tests tools`、`git diff --check` 均通过。
- `pytest --collect-only -q -o addopts=''` 收集 70 项。此执行环境运行完整套件会在既有较慢的
  action/web 测试中被外层运行器提前终止，未得到完整退出码；本轮未把它误报为全量通过。
- 外层主机上既有 8788 仍返回旧 0.10.1；当前源码可通过 `.venv/bin/uvicorn seo_ops.web.app:app
  --host 127.0.0.1 --port 8788` 启动 0.10.2。下次在持久终端启动后复核 health 与五个页面。

## 2026-07-18 — 0.10.3 最小禁区与产品决策隔离复验

### 完成内容

- 根据运营者确认，主题范围硬拦截只保留学校/课堂和未成年人；移除对高功率、技术、户外、专业、
  燃烧、切割和自卫词项的自动范围阻断。英语、明确主意图、需求、缺口、页面材料和重复检查不变。
- 修复没有结构化自然语言假设的分支将“分支标签 + specific owner task”写入候选池的问题。
  该类种子现在仅作为 `discovery_only` 检索，不得生成确定性文章标题。
- 将隔离验证过的产品决策检索加入购买、电池、光学和散热分支：USB/可更换电池、21700/18650、
  固定/可调焦、铜/铝热管理。它们仍先经过正文重叠和三类证据门；新增回归保证铜/铝线索在
  缺页面材料时只能为 `needs_evidence`。
- 新增 ADR-0017；`candidate_qualification` 升为 `0.9.6`，源码升为 `0.10.3`，SQLite schema 未变化。

### 隔离真实复验

- 使用生产库副本、美国英语桌面 SERP 运行两轮共 8 次真实 SerpAPI 查询；没有写入生产数据库、
  原始导入或 CMS 快照。
- 第一轮：可充电电池充电、可调焦/发散、绿蓝天文可见性、光束变暗排障均获得 PAA/自然结果，
  但分别与现有充电、电池、光学、天文颜色、清洁或寿命文章同意图/已覆盖子题，正确去向是旧文
  更新或产品内链。
- 第二轮：21700/18650、固定/可调焦、铜/铝散热、USB/可更换电池。前、二、四项仍为旧文更新；
  铜/铝热管理有具体产品能力、PAA 和对手页面，但尚缺至少两份页面级写作材料，保持补证据而非
  直接产生新 URL。

### 验证与遗留

- `.venv/bin/pytest tests/test_research_workflow.py -q -o addopts=''`：22 passed。
- 下一步先采集铜/铝热管理的独立页面材料并对照现有“铜 vs 不锈钢”正文；只有确认独立主要意图
  后才允许新建。Semrush Keyword Gap 尚未接入，当前竞争对手结论仅来自采集时的自然 SERP。

## 2026-07-18 — 0.10.4 统一素材门与非市场化制作

### 完成内容

- 移除素材包 B（市场/产品）类别；手工搜索提示词、写作提示词和成稿检查不再收集或生成市场规模、
  价格、竞品报价、零售商比较、折扣、排行榜、Quick Specs 或 CTA 填充。
- 旧文章制作改为与新文章共用确认素材门：A、E、G、至少两个可追溯来源及敏感主题的官方/研究
  来源不足时不调用 AI；原有 query + page 联合证据和 click-loss 上一窗口门保持。
- 旧稿提示词现在传入确认材料、来源清单和限制；确定性检查新增泛化锚文本、标题跳级、长段落和
  市场/价格内容拦截。原 Slug、主要意图、英语和第一手经验限制保持。
- 回放当前 CMS 正文确认铜/铝壳散热比较已被多篇文章直接讨论，移除该研究自然语言假设及其回归，
  并新增 ADR-0018。
- 源码版本升为 `0.10.4`；SQLite schema 未变化。

### 验证

- `.venv/bin/pytest tests/test_action_workflow.py::test_content_generation_locks_slug_and_filters_invented_links tests/test_action_workflow.py::test_old_article_generation_rechecks_joint_evidence_before_ai tests/test_action_workflow.py::test_old_article_chinese_output_never_reaches_deliverable tests/test_action_workflow.py::test_old_article_delivery_rejects_market_and_price_filler -q -o addopts=''`：4 passed（仅第三方 TestClient 弃用警告）。
- `.venv/bin/pytest tests/test_research_workflow.py -q -o addopts=''`：22 passed；`.venv/bin/pytest tests/test_topic_graph.py -q -o addopts=''`：2 passed；`.venv/bin/python -m compileall -q src tests`、`.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools` 与 `git diff --check` 均通过。
- 全量 `tests/test_action_workflow.py` 与 `tests/test_research_workflow.py` 在此执行环境只输出首个进度点后被外层提前结束，未取得可靠退出码，不能视为全量通过。

### 遗留

- 仍需用一篇真实旧文和一篇真实新文完成受控 AI 成稿复验；不得把自动结构检查或历史成稿当作流量效果证据。

## 2026-07-18 — 运行时服务修复

- 用户反馈文章建议页无法打开。确认 `8788` 上仍运行 0.10.1 的遗留 `seo-ops` 进程；当前源码内
  `list_article_suggestions()` 和 ASGI `/opportunities` 均可正常返回。
- 停止实际监听 8788 的旧进程，显式设置 `SEO_OPS_PORT=8788` 启动当前服务。此前启动脚本默认
  端口为 8787，首次重启误落到 8787，已停止该进程。
- 验证：`GET /api/health` 返回 `version: 0.10.4`；`GET /opportunities` 返回 HTTP 200，渲染 HTML
  含 `v0.10.4` 和“文章建议”。

## 2026-07-18 — 0.10.5 意图去重优先与计划式探索

### 完成内容

- 将 `candidate_qualification` 升为 `1.0.0`：同主意图与正文已覆盖子题继续阻断；相邻或独立方向不再因尚缺需求/页面材料被拒绝，缺失信息作为写作准备提示保存。
- 将旧 workflow plan 的多视角探索方法接入无结构化假设分支：按边界维度产生多个不同的检索问题，仍只以 SERP 返回的具体问题生成候选。
- 更新文章建议文案、方法治理、数据契约、交接和变更记录；新增 ADR-0019。

### 验证

- 隔离规则原型：同意图与已覆盖子题被拦截，`adjacent`/`distinct` 方向放行，`uncertain` 转人工复核。
- `.venv/bin/python -m compileall -q src tests` 通过。
- `.venv/bin/pytest` 的 7 个定向调研回归用例通过：覆盖多视角探索不伪造主题、无材料的独立/相邻方向保留、同意图与正文覆盖仍阻断、以及无结构化分支边界。
- 全量 `tests/test_research_workflow.py` 在此执行器约 14 秒上限被中断，未将其记为通过。
- `.venv/bin/pytest tests/test_simplified_workflow.py -q -o addopts=''`：4 passed（有 1 条既有 TestClient/httpx 弃用警告）。

### 遗留

- 尚未用真实外部 API 对新的多视角种子做生产调研；需由运营者在界面主动触发，不能静默消耗额度。

## 2026-07-18 — 0.10.6 种子池、公开语言代理与纯重复资格

### 完成内容

- 将 `candidate_qualification` 升为 `1.1.2`：只有与活动博客同主意图或正文已覆盖子题会阻断。
  范围、英语、占位 intent、弱需求、少材料、关系不确定和跨分支均改为诊断/准备度；规则版本变化会
  触发旧候选重判。
- 将 `multi_source_topic_research` 升为 `0.11.2`、`topic-hypothesis` 升为 `0.10.1`。无结构化
  假设的分支先用旧 Plan 问题链构造确定性种子，第二条叠加论坛、评论、问答词并记录
  `public_third_party_language_probe`；提示词明确它是公开市场代理，不是本站第一方反馈。
- 种子锚点改为“核心对象”判断：激光笔仍是任务核心时允许跨受众、场景和分支，不自动降权；
  只有替换成激光水平仪、切割机等其他核心对象时软降权。AI 锚点元数据改为 inference 首项，
  避免 8 条推断时被截断并在重判后丢失。
- 文章建议把 AI 成型角度与 PAA/Related/Tavily 直接线索分栏；原始线索继续可见但不能开始制作。
  旧 Plan 的接受/拒绝记录加入软排序，不成为隐藏门。新增 ADR-0020，ADR-0019 标为已取代。

### 三轮真实外部测试与问题修复

- 所有真实测试均使用生产数据库副本，未写生产库。三轮分别为
  `use-professional × audience`、`buy-quality × failure`、`ops-accessories × ecosystem`：
  SerpAPI 6/6、Tavily 9/9、AI 3/3；Firecrawl 共 6 次实际请求，记录 5 次成功页面采集、
  2 次失败事件和 1 次 24 小时复用。三轮状态为 partial、success、partial。
- 10 个 AI 成型角度中 7 个通过非重复判定、3 个因 CMS 已覆盖回流旧文。有效样本包括树艺师
  指示具体树枝、卖家虚标功率的常见说法与验证、工业机械对准；原始 PAA 同时暴露航空法规、
  泛安全和相邻产品噪声。
- 第一版暴露三个问题：长分支标签导致搜索偏移、PAA/标题冒充推荐、分支词匹配误罚树艺师角度。
  随后缩短 focus、分离 raw lead、传入结构化 seed context，并按运营者确认取消核心对象未变时的
  跨分支降权。
- 额外真实定向复测 `ops-accessories × ecosystem` 完成 SerpAPI 2/2、Tavily 3/3、
  Firecrawl 2/2、AI 1/1，确认精简种子可正常完成；它仍产出 CO₂ 对准等相邻工作流，因此最终规则
  不把“跨分支”误当重复，而只在核心产品被替换时软降权。
- 最新 `1.1.2` 在三轮保存证据上本地重放：树艺师主题仍为 `distinct/new_article`，优先级由旧
  误罚的 71.6 恢复为 91.6；三轮本身的 10 个成型角度和 14 条原始搜索线索保持分栏。此前显示的
  18 条是隔离副本中连同历史待处理记录在内的站点总数，不属于本次三轮的计数。
- 三条不含站点私有上下文的公开语言种子另做真实网页抽查，检出建筑检查员指示高处违规点、
  围栏施工固定、望远镜安装架与外接开关兼容、卖家虚标/退货等具体语言。再次启动完整
  `0.11.2` 外部链路时，执行环境因会发送生产副本标题/H2/反馈上下文而拒绝；未绕过该策略。

### 验证结果

- `.venv/bin/pytest tests/test_research_workflow.py tests/test_simplified_workflow.py -q`：34 passed。
- `.venv/bin/pytest -q`：80 passed（仅 1 条既有 Starlette TestClient/httpx 弃用警告）。
- 全量测试首次发现 3 个旧断言：制作页旧文案、迁移测试写死 `0.7.3`、泛 FAQ 必须隐藏；
  已分别同步到当前文案、当前规则常量和“仅重复硬拦截”政策后重跑通过。
- `.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools` 与
  `git diff --check` 通过。

## 2026-07-18 — 0.10.6 四轮最小预算方法验收

### 目的与隔离

- 运营者要求验证方法效果，不新增功能、不清理生产记录。复制生产 SQLite 到
  `/tmp/seo-ops-method-check-lj98wZ` 后，测试副本仅清除派生调研状态并保留 GSC/CMS 输入；
  生产库 SHA-256 在测试前后均为 `ff16d78ecd68fc6568834531f27ee576c89e46869560213a58a22cbf2c7027f3`。
- 每轮严格限制 SerpAPI、Tavily、Firecrawl、AI 各 1 次；四轮交替覆盖旧 Plan 主问题链和
  `public_third_party_language_probe`，而不是只依赖一次偶然成功。

### 结果

- 四轮 `use-professional × audience`、`buy-quality × failure`、`ops-accessories × ecosystem`、
  `use-photography × failure` 均为 success；各服务实际/成功调用均为 4/4。
- 共得到 29 条候选：7 个 AI 成型角度、5 个非重复新方向、2 个正文已覆盖而回流旧文、22 条原始
  SERP/PAA/相关搜索线索。树艺师检查/园林使用再次出现，说明该角度并非单次偶然。
- 但附件分支产出了手枪激光瞄具兼容和 CNC 路由器安装；它们满足“非重复”而仍偏离本站手持激光笔
  的实际意图。公开语言探针也产生品牌词、论坛词和安全噪声。原始线索已正确分栏，但当前核心对象
  识别和排序不足以把“非重复”自动等同于“适合本站”。

### 结论

- 方法已证明能在极小预算下稳定发现少量新任务，但尚不宜无审查地据此清空历史并自动信任所有
  `qualified` 结果。若后续优化，应保留重复资格的宽松原则，同时将“手持激光笔仍是主要工具”作为
  可见的软相关性排序，而非重新引入范围硬门。

## 2026-07-18 — 0.10.7 任务卡正式接入与三轮最小预算复验

### 完成内容

- 将旧 Plan 的前沿选择落地为可审计的 `角色 × 单一任务 × 条件` 任务卡；卡先只做 CMS 同主意图/正文覆盖预筛，需求、范围、材料、意图表述与弱信号均不构成新硬门。
- 将卡的角色、任务、条件、来源基础和预筛结果写入调研运行并传给 AI；提示词要求忠实包装，不能把卡未声明的风险、改装、维修、测量、替代产品或法律主张补进去。
- 若某分支只剩一两张未重复任务卡，保留任务卡为首个精确种子，再用旧 Plan 的两条独立问题链补足至多三个探索种子；新增单元回归覆盖此路径。

### 正式接入后真实复验

- 所有轮次使用生产 SQLite 副本，保留 CMS/GSC 输入、清空副本内派生调研状态。源库 SHA-256 在三轮前后均为 `d9351b82dd2181a786bd4a7fee91cf00f6cff305ab7c7ebd27e703d3859e5cb6`，未写生产数据。
- 每轮限制 SerpAPI、Tavily、Firecrawl、AI 各 `1` 次，且三轮全部 `success`、各服务实际/成功均为 `1/1`：
  - `use-professional × audience`：`Using a Laser Pointer to Mark Pruning Locations From the Ground`，`qualified/new_article`；
  - `operation × failure`：`Laser 303 Mode Hopping vs Dimming Fault: Symptom Diagnosis for Owners`，`qualified/new_article`；
  - `use-presentations × journey`：`Why a Green Laser Pointer Dot Appears Overly Bright or Blooms on Camera or TV Images`，`qualified/new_article`。
- 三个运行的 `filters_json` 均保存了对应的任务卡和 `qualified` CMS 预筛；同时出现的 PAA/Related 原始线索仍被正常路由为旧文覆盖或原始线索，未冒充成型主题。

### 验证

- `.venv/bin/python -m compileall -q src/seo_ops`：通过。
- `.venv/bin/ruff check src/seo_ops/services/research_workflow.py tests/test_research_workflow.py`：通过。
- `.venv/bin/pytest tests/test_research_workflow.py -q`：31 passed。

### 最终工程验证与遗留

- `.venv/bin/pytest -q`：84 passed（仅 1 条既有 Starlette TestClient/httpx 弃用警告）。
- `.venv/bin/ruff format --check src tests tools`、`.venv/bin/ruff check src tests tools` 与 `git diff --check`：通过。
- 已停止唯一仍加载 `0.10.6` 的本地 `seo-ops` 进程并在同一端口启动当前源码；`GET /api/health` 返回 `version: 0.10.7`，`/research` 与 `/opportunities` 均为 HTTP 200。
- 任务卡不能保证已经饱和的市场无限产生可发布主题；若卡耗尽或连续两轮不产生独立成型主题，应补充可追溯的新前沿卡，不能放宽重复判定或制造主题。

## 2026-07-18 — 0.10.7 后续审计：静态任务卡未通过完整续池验收

### 审计范围与隔离

- 运营者要求先验证方法效果；因此没有新增生产功能、没有清理 GSC/JSON/CMS，也没有把本次外部调用写入生产 SQLite。所有试验使用 `/tmp` 下从生产库复制出的独立数据库和快照目录。
- 复核对象包括旧 Plan 的真实来源库、0.10.7 静态任务卡、当前 65 篇博客正文，以及 Google 与行业公开方法论。Google 的要求是人本、有原创附加价值的内容；它并不支持靠批量主题卡生成大量低价值页面。

### 实测结果

- 旧痛点库可解析出 156 条带 URL 的历史公开线索。直接按单条线索生成卡得到 8/24、7/26 等表面“合格”结果；语义簇版本得到 7/20。它们都没有达到可交付质量：把多个任务拼在一起，或将物流、泛收纳等旧文已覆盖内容误判成新文章。
- 当前 CMS 的人工正文复核显示，语义簇中“长期收纳”“充电器红绿灯排障”“望远镜固定”“TV 屏幕可见性”已分别由现有收纳、充电器、支架和演示文章的主标题或 H2 直接回答。现有词面型预筛把其中多张标成 `uncertain/qualified`，证明它不能独立承担主意图去重。
- 额外执行一次不依赖旧库的实时来源对照：SerpAPI、Tavily 各成功一次；选择 Reddit 结果的 Firecrawl 页面抓取因提供商拒绝而失败；AI 从 14 条结果抽出 10 个卡片，其中“建筑师、教师、天文”等至少部分角色并没有对应的来源片段。该轮只自然得到一个较清晰的新角色线索（博物馆讲解），不足以支撑五轮稳定产新主题的结论。

### 结论与未执行项

- 此方法未通过“每轮稳定找出新主题”的前置验收。为避免用偶然成功或 AI 补全伪造效果，没有继续花费 API 去跑五轮，也没有继续落地代码。
- 早先记录的三轮 0.10.7 成功，只能说明预先挑出的卡可以被下游证据链消费，不能证明“来源发现与卡池续期”完整有效；本条审计对该结论作出更正。
- 后续若继续，应先验证一个完整但隔离的来源观察原型：来源 URL + 采集时间 + 原话片段 + 可追溯卡字段；以 CMS 的“对象 × 主任务/结果”而非单词重合做重复判定；再进行五轮真实验证。若该原型仍不能每轮给出经人工复核的独立主题，则保持不实现。

## 2026-07-19 — 0.10.8 来源观察续池、单轮冷却与两轮真实复测

### 完成内容

- 新增 SQLite v12 `research_seed_observations`，将 PAA/相关搜索、资料发现和 AI 可核对摘录保存为独立观察；字段包括来源类别、URL、标题、摘录、evidence ID、后续查询、可选任务卡、语义簇、核心对象诊断和消费状态。
- 调研种子优先消费未使用的来源观察，旧 Plan 任务卡和独立问题链继续补足。来源类别覆盖商业/竞争候选页、论坛/社区、评价、社媒/视频、官方/参考以及 PAA/相关搜索；来源 URL 决定类别，不把查询标签写成事实。
- 加入立即上一轮主攻簇的单轮软冷却；冷却和相邻/偏移对象都只影响排序，不构成资格门。核心对象以原始标题/摘录判定，不能因生成查询含 `laser pointer` 被误升级。
- 将无页面直接支持的具体推荐降为 `raw_lead`，并新增候选池“对象 + 动作 + 场景”强重复去重；文章建议页把主题折叠为标题优先，详情按需展开。

### 实际验证

- 先进行单元/集成回归：`.venv/bin/python -m compileall -q src tests` 通过；`.venv/bin/pytest -q tests/test_research_workflow.py tests/test_migrations.py tests/test_simplified_workflow.py tests/test_web.py` 通过。新增测试覆盖：单轮冷却不变成禁词、冷却只读取立即前一轮、来源观察下一轮续用、生成查询不能升级偏移来源、核心来源优先和候选主任务强重复去重。
- 所有联网测试均复制生产 SQLite、保留导入 CMS/GSC、仅清理副本派生研究状态。最终副本路径为 `/tmp/seo-ops-source-continuation-xu1ntupv`；生产 SQLite SHA-256 前后均为 `ec2cdd03c85918dd1b014c3446c011d148533b00a2ef2d34357209f50f45b0e6`。
- 最终两轮前在副本写入一条仅供调度的已完成 `battery_charging` 记录。两轮均为 `success`：每轮 SerpAPI 实际记录 3、Tavily 8、Firecrawl 4、AI 1；第二轮复用 2 次 Firecrawl 页面。SerpAPI 账户端点在本次最终运行前后显示 55→54，和运行审计的逐请求计数不完全一致，未据此推断真实计费。
- 第一轮继承电池软冷却，保存 24 条来源观察，得到 `Using a Green Laser Pointer to Indicate Pruning Locations From the Ground as an Arborist`。第二轮继承第一轮 `use_case_tasks` 冷却，实际消费 3 条第一轮观察，得到 `Why Laser Beams Appear to Stop Suddenly Outdoors` 与 `Using a Laser Pointer for Antenna and Machinery Alignment` 两个不同的成型方向。
- 中途真实测试发现 LightBurn 连续框选和激光瞄具可能因生成查询被误升为核心、树艺师主题可能换标题重复；已改为来源标题/摘录锚定、核心优先排序与候选主任务去重，再完成上述最终两轮。最终种子中不再让这些偏移来源占核心位。

### 遗留

- 两轮成功证明来源观察闭环能稳定产生不同方向，但不证明小众市场无限有可发布主题；正常运营运行仍需由运营者在文章建议页核对品牌适配与制作材料。
- 本地常驻服务若仍加载 0.10.7，需要重启后才会使用 0.10.8 和 SQLite v12 迁移。

## 2026-07-19 — 0.10.9 开放入口还原、主意图聚类与两轮正常实测

### 问题定位与实现

- 对照成功原型报告 `/tmp/seo-ops-open-scheduler-v2-rlxu9lam/report.json`，确认其中 58 条观察、24 个种子、17 个包装候选和 6 个意图簇均是实际输出，不是预设限额；唯一有意的数量控制是选择 5 个种子继续深挖，其他种子不应消失。
- 正式接入的真实偏差是 `_open_scheduler_family_specs()` 把自动选中的分支词加入全部十二类查询。散热实测因此从 60 条观察只抽出 5 个同方向种子；这是一层原型不存在的语义限流。
- 将十二类入口恢复为成功原型的原始宽查询，分支仅保留给旧 Plan 任务卡与审计；AI 种子提示明确要求返回所有来源支持的独立意图，上一轮冷却只能影响排序，不能删除种子。
- 所有未被 CMS `same_intent` / `covered_subtopic` 拦截的种子继续进入折叠候选池；选择 5 个做 SerpAPI/Tavily/Firecrawl/AI 深挖只控制额度。候选工程保险丝提高到 200，来源观察保险丝提高到 500，删除未使用且容易误解的 `open_scheduler_initial_family_limit`。
- 恢复成功原型最后的主意图聚类。`_ai_intent_deduplicate_pool()` 只能分组输入 ID：合并本轮同页面职责换说法、标记已有候选池同意图，并合并 evidence/facts；不同问题、任务、症状、决策、受众、地区、条件和结果必须保留。失败时保留原池并记录失败，不让 AI 改资格或创造主题。

### 两轮真实外部测试

- 使用 `/tmp/seo-ops-open-cooldown-normal-rcXUao/seo_ops.db` 生产副本，保留真实 CMS/GSC，不指定 topic_id、不模拟前序。生产库 SHA-256 前后均为 `8181b15b5f5cd1c0747acebf3b3323a9004c471a215ac08b7cfabec77a403c1f`。
- 第一轮自然选择 `use-outdoor`：SerpAPI 2/2、Tavily 17/17、Firecrawl 6/7、AI 6/6；58 条初始观察，15 个种子提案，1 个 CMS 重复，14 个来源种子可见，聚类前形成 13 条主题与 14 条 raw lead，共 30 个候选。
- 第二轮自然轮到 `use-astronomy`：SerpAPI 2/2、Tavily 计划 17 且复用 12 个首轮入口、Firecrawl 5/7、AI 6/6；58 条初始观察，AI 原始返回 48 个种子、验证后保留 45 个，8 个 CMS 重复，37 个来源种子可见，聚类前形成 37 条主题与 9 条 raw lead，共 51 个候选。
- SerpAPI 账户端点本次显示 52→48，与两轮各 2 次吻合。两轮均因部分 Firecrawl 页面失败诚实记录为 `partial`；生产库未修改。

### 聚类前置验证与代码路径复验

- 先对两轮已存真实成型候选各调用一次 AI-only 聚类，不再调用搜索 API：第一轮 13→11；第二轮识别店铺评价、天文活动规则、校园规则等本轮簇，并识别 12 个与第一轮同意图变体，37→17。该结果证明数量收敛不必恢复分支限流。
- 落地后对两轮完整候选池调用正式代码路径：第一轮 16 个成型/路由候选合并 3 个换说法；第二轮 42 个成型/路由候选合并 10 个本轮换说法并识别 14 个上一轮同意图。扣除原已路由旧文项后，仍约有 10 与 14 个独立成型方向进入最终资格检查。

### 验证命令与遗留

- `.venv/bin/pytest -q tests/test_research_workflow.py`：40 passed。
- `.venv/bin/ruff check src/seo_ops/services/research_workflow.py tests/test_research_workflow.py`：通过；新增回归覆盖开放入口不受电池/散热方向收窄、所有来源种子可见且只深挖 5 个、同意图换说法合并且不同细化意图保留。
- `.venv/bin/python -m compileall -q src tests`、`.venv/bin/ruff format --check src tests tools`（53 files already formatted）、`.venv/bin/ruff check src tests tools` 与 `git diff --check`：全部通过。
- `.venv/bin/pytest -q`：109 passed，仅有 1 条既有 Starlette TestClient/httpx 弃用警告；所有临时测试脚本已删除。
- 已重启本地常驻服务；`GET /api/health` 返回 `version: 0.10.9`，`/research` 与 `/opportunities` 均为 HTTP 200。
- 使用应用内浏览器真实打开调研页和文章建议页：调研页显示 0.10.9 与当前预算，文章建议页渲染 17 个折叠条目，浏览器控制台无 warning/error；该页面检查没有触发外部调研或消耗搜索额度。

## 2026-07-19 — 调研页实际过程账与入口文案简化

### 完成

- 移除“查看以前的调研概况”折叠列表和结果区重复的“查看文章建议”按钮；历史 `research_runs` 与来源观察仍完整保存，未删除任何审计记录。
- 运营者复核后移除没有本轮数据的固定“八步流程”说明；过程账只保留可展开的实际记录。入口/冷却/深挖种子、来源 URL/原话、CMS 预筛、主意图去重/路由和异常均由本轮保存的对象直接渲染，缺失时明确说明缺失，不用模板话术补齐。
- 过程账逐项显示实际深挖种子、保存的来源 URL/原话、CMS 重复预筛及所匹配的现有文章、AI 归类的本轮换说法/历史同意图、保存阶段的路由或跳过理由，以及提供商错误代码/消息。
- 旧运行缺少这些对象时，页面明确说明不能事后回填或还原，不虚构数据；`partial`/`failed` 运行直接列出失败提供商和系统记录。新增审计只记录已经发生的调研动作，不改变种子选择、资格、去重或 API 调用。
- 同步调研入口名称、最新运行标题和按钮文案：覆盖较少方向显示为“突破主题瓶颈”，已有分支加维度显示为“补主题缺口”；未调整任何调度或 API 调用逻辑。

### 验证

- `.venv/bin/pytest -q tests/test_web.py tests/test_research_workflow.py`：通过（仅既有 Starlette TestClient/httpx 弃用提示）。
- `.venv/bin/ruff format --check src tests`、`.venv/bin/ruff check src tests`、`.venv/bin/pytest -q` 与 `git diff --check`：通过。
- 重启本地 `seo-ops` 服务以加载当前后端读取逻辑；`GET /api/health` 返回 200。应用内浏览器打开当前 `/research`：入口名称/按钮已按运营者定义显示，泛“8 步”说明不再出现，RUN #16 的 5 个实际深挖种子和 134 条保存的来源线索均能显示，浏览器控制台无警告或错误。本单元未触发新的外部 API 调用。

## 2026-07-19 — 文章建议结果池与非选择数据的完整去向

### 完成内容

- 将文章建议页收敛为两栏最终选择：左栏为可执行的旧文章更新，右栏为可执行的独立新文章。各栏前两项直接展开，其余候选逐条折叠，取消“最多 2 + 2”与隐藏低优先级条目的行为；右栏显示累计待选与最近成功/部分成功调研实际形成的独立新主题数。
- 从该页面移除原始搜索线索、已归旧文、人工复核、调研池计数和重复的“查看文章制作”入口。它们不再要求运营者逐个处理，也没有被删除：原始公开语言继续保存在 `research_seed_observations`，下轮会作为可续用种子；`covered_existing` 是最终重复结论，只留审计；`update_existing` 自动建成左栏旧文优化建议。
- 补齐旧状态生命周期：每次读取文章建议时，`needs_evidence` / `needs_human_review` 会无外部调用地按当前 `candidate_qualification 1.1.3` 的重复唯一规则重判。结果必须成为新文、旧文更新或正文已覆盖三者之一；同时回填所有既有 `update_existing` 的旧文建议，避免候选从右栏消失却未进入左栏。

### 验证与真实状态

- 新增回归覆盖原始线索保留在来源观察续池、旧人工复核自动进入新文栏、旧待证据同主意图自动进入旧文栏，以及页面仅含两栏最终结果。
- `.venv/bin/ruff format --check src/seo_ops/services/article_suggestions.py src/seo_ops/services/research_workflow.py tests/test_simplified_workflow.py tests/test_web.py` 与对应 `ruff check`：通过。
- `.venv/bin/pytest -q tests/test_simplified_workflow.py -rA`：9 passed；`.venv/bin/pytest -q tests/test_web.py tests/test_research_workflow.py -rA`：47 passed（均仅有既有 Starlette TestClient/httpx 弃用提示）。
- 重启已核实的本地服务到 `127.0.0.1:8788`；health 与 `/opportunities` 均返回 200。真实页面显示左栏累计待选 17、右栏“累计待选 23（本轮新增 13）”；全部 21 个其余新主题和 15 个其余旧文均逐条可展开，浏览器控制台无错误。为了补齐既有生命周期，系统在真实派生数据中新增了缺失的 1 条 `research_existing_content_gap` 旧文建议；没有调用任何外部调研或 AI API，也没有改动 GSC、JSON、CMS 原始导入或来源观察。

## 2026-07-19 — 旧 Research + Write 1:1 复原交接方案

### 完成内容

- 按运营者明确要求新增 `docs/LEGACY_RESEARCH_WRITE_RESTORATION_HANDOFF.md`，将下一开发任务锁定为旧 `research + write` Skill 的 1:1 复原，不允许把任务解释成优化、删减、重构或重新设计。
- 交接文档登记旧两个 Skill、四个主脚本、动态加载/子进程依赖、六份写作 Context、三类素材库、产品报告、发布索引、正文和 topic-context 的事实源层级；旧 Python 脚本的实际行为优先于概括性文字。
- 逐阶段登记旧 Research 的 prompt、search results、collect、AI Step 0–6、scorer、material pack/brief/score 和 archive，以及旧 Write 的 validate、draft、pre-check、post-process、`--apply`、人工 `--force` 与 register。
- 单独登记旧内链、产品链接和外链装配链，并按当前旧脚本记录实际动态上下限、外链必达门、frontmatter 写回和 register 实际 top 2 回溯候选；没有新增链接计划模型。
- 登记同构兼容工作区、命令行先行、网页只包装旧命令、黄金样例、20 项回归测试、完整验收清单、禁止偏航和下一位 AI 的第一步。
- 更新 `HANDOFF.md`，明确该方案已获确认但尚未实施；第一工作单元只能做旧资产清单、SHA-256、依赖闭包和黄金样例选择，不能先改当前内容生成代码。

### 验证

- `git diff --check -- HANDOFF.md docs/WORKLOG.md docs/LEGACY_RESEARCH_WRITE_RESTORATION_HANDOFF.md`：通过。
- 文档标题、状态、事实源、Research/Write 阶段、链接装配、实施单元、验收清单和下一位 AI 第一动作均由文本检查确认存在。
- 本单元只新增和更新交接文档；没有修改应用代码、数据库、原始导入、旧 `/home/laoma/seo-workflow`、常驻服务或外部 API/AI 用量。

### 下一步

1. 下一位 AI 完整读取两个旧 Skill、四个旧主脚本及依赖闭包。
2. 提交旧资产清单、SHA-256 和三类黄金样例给运营者确认。
3. 确认后按交接文档单元 A → F 实施，任何优化另开任务。

## 2026-07-19 — 独立 Research + Write V2 SEO 与 AI 搜索优化建议

### 完成内容

- 按运营者要求新增 docs/RESEARCH_WRITE_V2_SEO_AI_SEARCH_OPTIMIZATION_PROPOSAL.md；没有修改旧复原文档、旧 Research/Write Skill、旧脚本或旧工作流产物。
- 明确 Legacy 与 Optimized 双模式边界：先完成 1:1 复原验收，V2 才能作为可选增强层另行批准和实施；V2 使用独立目录、版本与运行记录，同一文章只允许在人工选稿后执行一次 register。
- 依据 2026-07-19 可核对的 Google、Microsoft Bing 与 OpenAI 官方资料，登记生成式搜索的当前方法基线、抓取资格、非同质化内容、结构清晰度、OAI-SearchBot、AI 引用/引荐测量和 Commerce policy 边界。
- 提出 V2 Research sidecar：只读基线、技术资格审计、query/fan-out map、AI 引用版图、Claim Ledger、Information Gain Gate、页面职责去重和 Answer Brief。
- 提出 V2 Write sidecar：真实身份门、任务型大纲、答案单元、信任信号、适用结构化数据、双层 QA 和完整 CMS 交付包。
- 单独设计 V2 内链、产品链接、外链与回溯链接装配；链接由页面关系、读者任务和事实声明决定，不把数量当排名公式。
- 登记 Google Search Console 生成式搜索、Bing AI Performance 与 ChatGPT utm_source=chatgpt.com 的分平台效果观察，以及 7/28/56 天验证边界。
- 登记 Phase 0–6 实施顺序、18 项 V2 验收标准及 7 条 proposed 规则；所有 proposed 规则均未激活。

### 验证与边界

- 本工作单元仅新增独立建议文档并更新交接记录；未修改应用代码、数据库、原始导入、旧 seo-workflow、复原方案、常驻服务或外部业务系统。
- OpenAI 官方文档 MCP 在当前会话不可用，按 openai-docs skill 要求尝试添加时因本机 codex.exe Access is denied 失败；随后仅使用 Google、Microsoft 与 OpenAI 官方网页作为资料来源，没有使用第三方 SEO 说法作为规则事实。
- 文档章节检查已确认 16 个一级章节存在，Claim Ledger、Information Gain Gate、链接装配、OAI-SearchBot 与 7/28/56 天观察等关键内容均可检索。
- git diff --check -- HANDOFF.md docs/WORKLOG.md：通过；新增 V2 文档未发现行尾空白。
- 原复原文档仍为 24,746 bytes，SHA-256 为 9d0967ab6d69e392b3aefdf5d33234527c3eaba30bf4a57414169d38d9483bde；本单元未修改该文件。
- 本单元为文档新增，无应用代码或行为变化，因此未运行 pytest。

### 下一步

1. 仍先执行 Legacy 复原方案单元 A → F；V2 不得混入复原提交。
2. 只有运营者另行批准 V2 后，才从 Phase 1 数据协议和三类黄金样例开始。
3. V2 实施前把 proposed 规则按 METHOD_GOVERNANCE 正式登记，并重新核对届时官方文档。

## 2026-07-27 — Legacy 旧 Skill 第一批规则对齐与安全门

### 完成内容

- 对照冻结的 `research/SKILL.md` 与 `write/SKILL.md` 修正 Research 顺序：`research_scorer.py` 使用本次明确的 JSON 输入和独立临时输出先计算分数，AI 随后读取并解释，不能重算。评分失败立即停止，不再以目录中旧评分文件是否存在判断成功。
- W1b 使用 `write_pre_check.py --json` 的结构化 checks 计算失败数，区分正常检查不通过、非法输出和脚本异常；不再统计 Markdown 中的 `❌` 字符。
- 预检结果绑定当前 draft SHA-256。W2 在预检未通过或草稿改变后拒绝运行；AI 修订后会重新执行预检。
- `write_collector.py post-process` 改为在同目录临时草稿上 scrub、URL 去重、评分与蚕食检查。无 `--apply`、硬门失败或检查器异常时真实 draft 保持字节级不变。
- 蚕食检查异常和评分异常改为 fail-closed。人工 `--force` 仅允许在两轮修订用满、前次仍为蚕食阻塞、评分没有失败且本次最终 apply 时使用。
- W3 注册在服务层验证 `gate_passed + applied + applied_draft_sha256`，阻止绕过网页按钮或把旧门控结果套到修改后的草稿。

### 验证结果

- `pytest -q tests/test_legacy_workflow.py -k 'not LegacySync'`：54 passed。
- 初始化空的本地测试数据库后运行完整 `pytest -q`：165 passed；仅 1 条既有 `StarletteDeprecationWarning`。
- `python -m compileall -q src tests data_sources/modules` 与 `git diff --check`：通过。
- 完整测试会按既有 LegacySync 测试刷新仓库内派生工作区文件；测试后已精确恢复这 4 个文件，未保留运行数据改动。

### 遗留问题

- register 多文件事务、归档失败回滚与并发写保护留待下一批。
- action/attempt 历史运行目录暂不自动清理；若实际积累过快，再单独制定可审计保留策略。

## 2026-07-28 — Legacy action/attempt 产物隔离

### 完成内容

- 新增 action/attempt 运行目录、current pointer 与 manifest。每次 R0 都创建新 attempt；后续网页阶段只使用与 action 和完整 topic 匹配的当前运行。
- 每个 attempt 独占 Research、素材包、草稿、报告和 W2 状态；共享 context、published 与 products 链接到 R0 前完成的数据库同步快照。
- 统一服务层、Research 与 Write 脚本的 slug 实现。纯非拉丁主题使用稳定 SHA-256 摘要，删除跨进程不稳定的 `hash()` fallback。
- 报告使用 UTC 微秒时间和随机后缀，不再按 `kind-slug-date` 覆盖。collect 重试只接受本次同时生成的 Markdown 和 JSON；失败或不完整输出立即清理。
- `legacy_sync._gen_published_articles()` 删除非 active 快照文件；修复 `cannibalization_checker.py` 未定义 `SITES_DIR`，让蚕食检查实际读取配置工作区。
- 新增 ADR-0012，并补回归测试覆盖相同主题跨 action、同 action 新 attempt、topic 不匹配拒绝、报告不覆盖、collect 失败不复用、稳定 slug、checker 路径和 inactive 文件清理。
- 将 LegacySync 默认路径测试改为临时目录，避免测试刷新仓库内跟踪的演示工作区文件。

### 验证结果

- `.venv/bin/pytest -q tests/test_web.py tests/test_legacy_workflow.py`：74 passed，1 条既有 Starlette/httpx 弃用警告。
- `.venv/bin/pytest -q`：172 passed，1 条既有 Starlette/httpx 弃用警告。
- `python -m compileall -q src tests data_sources/modules`：通过。
- `.venv/bin/ruff check --select F821 src tests tools data_sources/modules`：通过。
- `.venv/bin/ruff check src/seo_ops/services/legacy_sync.py src/seo_ops/services/legacy_workflow.py src/seo_ops/web/app.py tests/test_legacy_workflow.py`：通过。
- `git diff --check`：通过。
- 全量测试刷新过 4 个仓库内演示工作区文件；测试后已按 HEAD 精确恢复，未保留派生数据改动。

### 遗留问题

- register 对 internal-links-map、素材库归档、素材包清理和草稿追加仍不是单一事务；下一批需加入故障注入、回滚和并发保护。
- 历史 attempt 当前完整保留，没有自动清理策略。

## 2026-07-28 — LEGACY_WS 动态化 + sync_all 多站点修复 + 回归测试

### 完成内容

- `legacy_sync.py`：所有 `_gen_*` 函数的 SQL 查询原写死 `site_id = 1`，现改为 `WHERE site_id = ?` 并绑定 `(site_id,)` 参数；每个函数新增 `site_id` 关键字参数。
- `legacy_sync.sync_all()`：新增 `settings` 和 `site_id` 参数。传入 settings 时使用 settings 指定的 DB 连接和 data_dir；不传时保持向后兼容（默认 `_db_connection()` + `site_id=1`）。
- `app.py`：`LEGACY_WS` 从硬编码 `<PROJECT_ROOT>/data/legacy_workflow/laserpointerhub` 改为 `active_settings.data_dir / "legacy_workflow" / "laserpointerhub"`，按当前运行时 settings 自动推导。
- `legacy_r0` 路由：`legacy_sync_all(LEGACY_WS)` 改为 `legacy_sync_all(settings=active_settings, site_id=action["site_id"])`，传递正确设置和站点 ID。
- 移除 `app.py` 中已不再需要的 `LEGACY_PROJECT_ROOT` 导入。
- `_gen_published_index` 在 `sync_all()` 中遗漏 `site_id=effective_site_id` 已补上。
- `_gen_internal_links_map` 两个查询和 `_gen_seo_data_manual` 查询遗漏 `(site_id,)` 绑定参数已补上。

### 测试修复

- `test_hermes_orchestrator_smoke.py`：移除对已删除 `LEGACY_PROJECT_ROOT` 的 monkeypatch；LEGACY_WS 现在自动从 settings fixture 的 data_dir 推导。
- `test_legacy_workflow.py`：mock `Connection.execute` 改为 `execute(self, _sql, _params=None)` 以兼容双参数调用。
- 新增 3 个回归测试（`TestLegacySync`）：`test_sync_all_with_settings_and_site_id`、`test_sync_all_with_settings_different_site_id`、`test_sync_all_workspace_respects_settings_data_dir`。

### 验证

- `pytest -q`：185 passed（之前 185，无回归）；仅 1 条既有 Starlette/httpx 弃用警告。
- `ruff check src/seo_ops/services/legacy_sync.py src/seo_ops/web/app.py`：All checks passed。
- 生产工作区 `data/legacy_workflow/laserpointerhub/` 在测试后未受污染（git diff 为 clean）。
- 更新 `docs/KNOWN_ISSUES.md`：新增 9 条已修复记录到"已修复"表。
