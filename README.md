# SEO Ops System

面向单人网站运营者的本地网页系统。它读取第一方数据，审核数据可信度，识别当前最值得执行的 SEO/内容动作，并把合格机会交给外部研究与 AI 辅助层。

当前首个站点是 **LaserPointerHub**。系统按多站点设计，但不会为了通用性牺牲第一版的可用性。

## 当前状态

项目处于 `0.2.0`：

- FastAPI + Jinja + SQLite 网页已经运行。
- GSC Excel 与 CMS JSON 以不可变快照导入。
- 机会引擎先做资格/可信度门槛，再排序和组合 Top 3。
- `/settings` 可统一配置 AI、SerpAPI、Firecrawl、Tavily 与 Google Trends 口径。
- 外部供应商目前完成安全配置与免费连接检测，尚未参与自动 SERP/研究执行。
- AI 是可选解释与内容辅助层，不参与确定性计算，也不能创造证据。

始终先看 [HANDOFF.md](HANDOFF.md) 了解当前进度和下一步。

## 系统闭环

```text
GSC / CMS 不可变快照
        ↓
数据质量与官方异常门槛
        ↓
资格门槛 → 机会排序 → Top 3
        ↓
SERP / Trends / 定向网页研究（下一实现单元）
        ↓
AI 解释 / Research brief / 修改任务
        ↓
人工发布
        ↓
7/28/56 天观察 → 规则校准
```

## 外部数据与 AI

浏览器不直接调用供应商：

```text
浏览器 → SEO Ops 后端 → SerpAPI / Firecrawl / Tavily / AI Provider
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
- “连接成功”只表示账户接口有效，不表示该来源已经影响机会分数。

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

当前测试基线：12 passed。

## 项目资料

- [HANDOFF.md](HANDOFF.md)：当前状态、已知问题、下一步，接手时先读。
- [AGENTS.md](AGENTS.md)：开发与维护约束。
- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)：业务背景、权限边界与旧系统关系。
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
- 原 `research` 的素材包、来源约束和归档思路会被重构后复用。
- 原 `write` 的事实约束、链接校验和人工门控会被保留。
- 虚构作者、强制亲测话术、固定 FAQ/YouTube/字数密度等规则不继承。

新项目不得修改或删除旧项目文件。
