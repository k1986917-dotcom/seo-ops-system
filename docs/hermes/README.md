# Hermes 接入说明

Hermes（[Nous Research](https://nousresearch.com) 的 AI 助手 CLI）是本 SEO Ops 系统的
**运行时 AI 框架**，负责：

- 加载 SEO Ops 提供的 SKILL 定义（`docs/legacy-skills/{plan,research,write}/SKILL.md`）
- 把外部 API（SerpAPI、Firecrawl、Tavily、AI provider）的真实密钥注入运行时环境
- 通过文件系统与 SEO Ops 服务交互（读取 `data/` 下的产物、写入新的草稿/评分）
- 维护工具集（`hermes-cli`）让 AI 能调用 shell、文件读写、浏览器

Hermes 本身是本机工具，**不参与 SEO Ops 的 HTTP 服务**。SEO Ops 的 `seo-ops` 进程
独立监听 `127.0.0.1:8787`；Hermes 通过同一台机器上的文件系统和 HTTP 调用与之协作。

---

## 版本与位置

| 项 | 值 |
|----|----|
| 工具 | `tirith`（Hermes 的命令行二进制） |
| 安装路径 | `~/.hermes/bin/tirith` |
| 配置目录 | `~/.hermes/` |
| 主配置 | `~/.hermes/config.yaml` |
| 环境变量 | `~/.hermes/.env` |
| 系统提示 | `~/.hermes/SOUL.md` |
| 凭据池 | `~/.hermes/auth.json` |

本仓库提供：
- `docs/hermes/SOUL.md` — Hermes 系统提示参考副本（与 `~/.hermes/SOUL.md` 一致）
- `docs/hermes/config.example.yaml` — 配置 schema 完整样例（不含任何密钥）

---

## 接入模式

Hermes 接入 SEO Ops 的方式有两种，按使用场景选择：

### 模式 A：作为 SKILL 解释者（推荐用于 plan/research/write）

1. Hermes 启动时加载 `~/.opencode/skills/{plan,research,write}/SKILL.md`
   （这些文件与 `docs/legacy-skills/` 一一对应，原件不动）
2. AI 按 SKILL.md 的步骤执行：调用 `data_sources/modules/` 下的脚本、读取
   `data/legacy_workflow/laserpointerhub/` 下的产物、生成新的素材包或草稿
3. Hermes 直接调用 SEO Ops 的 HTTP 接口（`POST /actions/{id}/legacy/stage/r0` 等）
   或直接写文件
4. AI 决定哪些产物要保留、哪些要废弃

**适用场景**：选题、研究、文章撰写 — 这些是 SKILL.md 直接定义的工作流。

### 模式 B：作为代码审计与重构助手（推荐用于 batch A/B/C/D 修复）

1. Hermes 加载本仓库的源码（`src/seo_ops/`、`data_sources/modules/`）
2. AI 阅读 `docs/legacy-skills/` 下的 SKILL.md 作为业务规则对照
3. AI 提出补丁、跑测试（`pytest`、`ruff`）、提 PR
4. 人工（或运营者）审批后合并

**适用场景**：本仓库当前的工作 — 把 GPT Work 出的审计报告转化为代码改动。

---

## 必需的环境变量

Hermes 通过 `~/.hermes/.env` 加载：

| 变量 | 用途 | 必需？ |
|------|------|--------|
| `DEEPSEEK_API_KEY` | AI provider 主密钥 | AI 功能必需 |
| `MINIMAX_CN_API_KEY` | Hermes 自身的 AI provider 密钥 | Hermes 启动必需 |
| `OPENCODE_GO_API_KEY` | 备用 AI provider | 可选 |
| `FIRECRAWL_API_KEY` | Firecrawl 外部调研 | 外部调研必需 |
| `TAVILY_API_KEY` | Tavily 外部调研 | 外部调研必需 |
| `SERPAPI_API_KEY` | SerpAPI SERP 验证 | SERP 必需 |
| `FEISHU_*` | 飞书消息通道 | 仅当用飞书接入时必需 |

Hermes 的所有密钥只存在 `~/.hermes/.env`，**绝不写入** SEO Ops 的 `.env`、SQLite、
HTML 响应或 Git。SEO Ops 的密钥走它自己的 `.env`。

---

## 凭据池（credential_pool）

`~/.hermes/auth.json` 是 Hermes 的凭据池，每个 provider 可以有多把密钥轮换：

```yaml
credential_pool:
  minimax-cn:
    - id: a9be28
      label: MINIMAX_CN_API_KEY
      base_url: https://api.minimaxi.com/anthropic
      secret_fingerprint: sha256:bfd44863351fffb6
  deepseek:
    - id: 542385
      label: DEEPSEEK_API_KEY
      base_url: https://api.deepseek.com/v1
      secret_fingerprint: sha256:f025cccc43029453
```

**注意**：`auth.json` 只存密钥的 SHA-256 指纹和 base_url，**不存明文密钥**。
明文密钥必须放在 `~/.hermes/.env` 里。运营者手动配置，不会自动同步到 SEO Ops。

---

## 飞书通道（可选）

如果运营者用飞书跟 Hermes 对话，需要：
1. 在飞书开放平台创建应用，获得 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
2. 设置 `FEISHU_HOME_CHANNEL`（消息主频道 ID）
3. `FEISHU_CONNECTION_MODE=websocket`（默认）或 `webhook`
4. `FEISHU_ALLOW_ALL_USERS=true` 或指定 `FEISHU_ALLOWED_USERS` 白名单

本机 SEO Ops 服务**不依赖飞书**，所以飞书配置出问题不会影响 `127.0.0.1:8787`。

---

## 工具集（toolset）

Hermes 默认启用 `hermes-cli` 工具集，提供：

- `shell` / `bash` — 执行 shell 命令（SEO Ops 启动、测试都靠这个）
- `read` / `write` / `edit` — 文件读写（与 `docs/legacy-skills/` 互动）
- `webfetch` / `curl` — 调 SEO Ops HTTP API
- `python` — 跑 `data_sources/modules/` 下的脚本

`tool_loop_guardrails` 配置防止 AI 死循环：
- 同工具失败 3 次警告、8 次硬停
- 幂等调用 2 次无进展警告、5 次硬停
- `exact_failure` 5 次硬停

---

## 接入失败的常见原因

| 现象 | 原因 |
|------|------|
| Hermes 启动报 `Missing API key` | `~/.hermes/.env` 没配 `DEEPSEEK_API_KEY` 或 `MINIMAX_CN_API_KEY` |
| 飞书收不到消息 | `FEISHU_HOME_CHANNEL` 错，或应用没加入频道 |
| AI 调 `seo-ops` HTTP 失败 | SEO Ops 服务没启；先 `seo-ops` 启起来 |
| 写 `data/legacy_workflow/` 没生效 | 路径权限；Hermes 与 SEO Ops 要在同一用户下运行 |
| Python `ModuleNotFoundError: data_sources` | `PYTHONPATH` 没设；启动时要 `PYTHONPATH=/path/to/repo nohup setsid .venv/bin/seo-ops ...` |

---

## 安全边界（来自 AGENTS.md）

- Hermes 的密钥、聊天历史、checkpoints **绝不**进入 SEO Ops 仓库
- SEO Ops 的密钥、OAuth token、SQLite **绝不**进入 Hermes 配置目录
- 两个系统的环境互不感知，只通过 `data/legacy_workflow/laserpointerhub/` 共享文件
- 重启一方不影响另一方（各自管各自的进程）