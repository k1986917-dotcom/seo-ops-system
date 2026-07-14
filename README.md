# SEO Ops System

面向单人网站运营者的本地网页系统。它读取第一方数据，识别当前最值得执行的 SEO/内容动作，把证据交给调研与写作环节，并在发布后记录结果。

当前首个站点是 **LaserPointerHub**。系统本身按多站点设计，但不会为了通用性牺牲第一版的可用性。

## 当前状态

项目处于 `0.1.0` 基础里程碑：

- 已确定数据、证据、规则、机会、行动和效果的边界。
- 网页端采用 FastAPI + Jinja，数据存储采用 SQLite。
- GSC Excel 与 CMS JSON 以不可变快照方式导入，原始文件永不由系统删除。
- AI 是可选的解释与内容辅助层，不参与确定性计算，也不能自行创造证据。

始终先看 [HANDOFF.md](HANDOFF.md) 了解当前进度和下一步。

## 系统闭环

```text
GSC / CMS 原始快照
        ↓
站点状态与证据
        ↓
资格门槛 → 机会排序 → Top 3
        ↓
Research 证据包 → Write/修改任务
        ↓
人工发布
        ↓
7/28/56 天观察 → 规则校准
```

## AI 如何接入

网页可以使用大模型，但浏览器不会直接持有 API Key：

```text
浏览器 → SEO Ops 后端 → AI Provider API
```

首版采用 OpenAI-compatible 接口，可连接 OpenAI、OpenRouter、DeepSeek 或兼容服务；本地 Ollama 可通过兼容端点接入。未配置 AI 时，导入、计算、机会发现、行动记录和效果追踪仍然可用。

AI 允许做：

- 把已有证据解释成易懂的建议。
- 基于证据生成 research brief。
- 基于已核验素材起草或修订正文。
- 指出证据缺口，但不能把猜测写成事实。

AI 不允许做：

- 修改原始指标、规则分数或资格门槛。
- 把模型记忆当成来源。
- 编造用户体验、测试、作者身份、销量或排名数据。
- 未经人工确认直接发布或删除内容。

详见 [docs/METHOD_GOVERNANCE.md](docs/METHOD_GOVERNANCE.md) 和 [docs/decisions/0002-ai-is-an-optional-advisor.md](docs/decisions/0002-ai-is-an-optional-advisor.md)。

## 本地启动

要求 Python 3.12+。

```bash
cd /home/laoma/seo-ops-system
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
seo-ops
```

打开 `http://127.0.0.1:8787`。

运行测试：

```bash
pytest
```

## 项目资料

- [HANDOFF.md](HANDOFF.md)：当前状态、已知问题、下一步，接手时先读。
- [AGENTS.md](AGENTS.md)：开发与维护约束。
- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)：业务背景、权限边界与旧系统关系。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：技术结构和数据流。
- [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md)：GSC/CMS 输入与数据库约定。
- [docs/METHOD_GOVERNANCE.md](docs/METHOD_GOVERNANCE.md)：怎样保证方法可审计、可更新。
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

