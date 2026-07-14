# 工作日志

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

