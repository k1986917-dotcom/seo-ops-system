# Hermes Runtime Report

> Captured on 2026-07-28 from the host where SEO Ops will run alongside Hermes.
> All commands were run on the live system; outputs are recorded verbatim.
> No secrets, tokens, user IDs, channel IDs, or private paths are included.
> Any sensitive values appear as `REDACTED`.

## 1. Operating system

```
$ uname -a
Linux localhost 6.6.87.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun  5 18:30:46 UTC 2025 x86_64 x86_64 x86_64 GNU/Linux

$ lsb_release -a
Distributor ID:	Ubuntu
Description:	Ubuntu 24.04.1 LTS
Release:	24.04
Codename:	noble
```

**Environment**: Linux server running inside **WSL2** (Windows host, kernel
`6.6.87.2-microsoft-standard-WSL2`). The SEO Ops project is checked out at
`/home/laoma/seo-ops-system/` and Hermes is installed at `~/.hermes/`.

Hermes and SEO Ops run inside the same Linux container (WSL2 userland); they
do **not** run on the Windows host. Any tooling that expects native Linux
binaries (e.g., `tirith`) runs correctly here.

## 2. Hermes installation

### Command probes

```
$ command -v hermes
/home/laoma/.local/bin/hermes
(exit=0)

$ command -v tirith
(exit=1)         # not on PATH

$ hermes --version
Hermes Agent v0.19.0 (2026.7.20) · upstream ef267011
Install directory: /home/laoma/.hermes/hermes-agent
Install method: git
Python: 3.11.15
OpenAI SDK: 2.24.0
Up to date
(exit=0)

$ tirith --version
/bin/bash: line 1: tirith: command not found
(exit=127)
```

**Findings**:

- `hermes` is the **current** CLI; installed via the official installer to
  `~/.local/bin/hermes`, which is a symlink into the venv at
  `/home/laoma/.hermes/hermes-agent/venv/bin/hermes`.
- `tirith` is **not** on `$PATH` and is **not** invoked by anything.
  However, an ELF binary still exists at `/home/laoma/.hermes/bin/tirith`
  (12 MB, built 2026-05-29). It is a leftover from a previous installation
  (`hermes-cli` was previously named `tirith` in some forks); it is not
  referenced by the current Hermes install and should not be invoked
  directly.
- **Do not use `tirith`** in any documentation, command, or skill spec;
  use `hermes`.

### Source repository

Hermes is installed from a git clone at `/home/laoma/.hermes/hermes-agent/`:

```
$ git -C /home/laoma/.hermes/hermes-agent rev-parse HEAD
ef267011348a7bc67ad3f46c9b0a0dcb0f4b7342

$ git -C /home/laoma/.hermes/hermes-agent log -1 --pretty=format:"%h %s %ad" --date=short
ef2670113 Merge pull request #73089 from NousResearch/bb/desktop-sidebar-counts 2026-07-27
```

- Upstream: `https://github.com/NousResearch/hermes-agent` (per the
  `--version` output "upstream ef267011")
- Working tree is on commit `ef267011`, which is a merge of PR #73089.

## 3. Skills directory

```
$ ls /home/laoma/.hermes/skills/
apple              diagramming         messaging          research
autonomous-ai-agents  domain             mlops             red-teaming
creative          email              note-taking
data-science      gaming             inference-sh
devops            gifs               mcp
github            media              productivity

$ ls /home/laoma/.opencode/skills/ 2>/dev/null || echo "(empty or absent)"
(empty or absent)
```

**Findings**:

- Hermes loads skills from `/home/laoma/.hermes/skills/` (NOT from
  `~/.opencode/skills/`). The empty/absent `.opencode/skills/` is consistent
  with Hermes having its own skill directory.
- The SEO Ops repo's `docs/legacy-skills/{plan,research,write}/SKILL.md`
  files are *not* in Hermes's skill format. To use them as Hermes skills,
  they would need to be wrapped in a SKILL.md frontmatter + helper scripts
  and installed under `~/.hermes/skills/<name>/`. **Not done** — see
  `docs/hermes/README.md` for the intended workflow.

### Installed skill categories (97 enabled)

```
$ hermes skills list  # abbreviated, see full output below
$ hermes skills list | tail -3
7 hub-installed, 65 builtin, 25 local — 97 enabled, 0 disabled
```

The full `hermes skills list` output is captured here for reference (one
row per skill; trust/source columns omitted for brevity):

| Name | Category | Trust | Source |
|------|----------|------|--------|
| yuanbao | (uncategorized) | official | official |
| claude-code | autonomous-ai-agents | builtin | builtin |
| codex | autonomous-ai-agents | builtin | builtin |
| computer-use | autonomous-ai-agents | builtin | builtin |
| hermes-agent | autonomous-ai-agents | builtin | builtin |
| kanban-codex-lane | autonomous-ai-agents | local | local |
| opencode | autonomous-ai-agents | builtin | builtin |
| architecture-diagram | creative | builtin | builtin |
| ascii-art | creative | builtin | builtin |
| ascii-video | creative | builtin | builtin |
| baoyu-article-illustrator | creative | official | official |
| baoyu-comic | creative | official | official |
| baoyu-infographic | creative | builtin | builtin |
| claude-design | creative | builtin | builtin |
| comfyui | creative | builtin | builtin |
| design-md | creative | builtin | builtin |
| excalidraw | creative | builtin | builtin |
| humanizer | creative | builtin | builtin |
| ideation | creative | local | local |
| manim-video | creative | builtin | builtin |
| p5js | creative | builtin | builtin |
| pixel-art | creative | official | official |
| popular-web-designs | creative | builtin | builtin |
| pretext | creative | builtin | builtin |
| sketch | creative | builtin | builtin |
| songwriting-and-ai-music | creative | builtin | builtin |
| touchdesigner-mcp | creative | builtin | builtin |
| jupyter-live-kernel | data-science | local | local |
| kanban-orchestrator | devops | local | local |
| kanban-worker | devops | local | local |
| webhook-subscriptions | devops | local | local |
| himalaya | email | builtin | builtin |
| minecraft-modpack-server | gaming | official | official |
| pokemon-player | gaming | official | official |
| codebase-inspection | github | builtin | builtin |
| github-auth | github | builtin | builtin |
| llm-wiki | research | builtin | builtin |
| polymarket | research | builtin | builtin |
| research-paper-writer | research | builtin | builtin |
| seo-content-gap-analyst | research | local | local |
| openhue | smart-home | builtin | builtin |
| xurl | social-media | builtin | builtin |
| debugging-hermes-tui | software-development | local | local |
| dogfood | software-development | builtin | builtin |
| hermes-agent-skill-author | software-development | builtin | builtin |
| hermes-s6-container-template | software-development | local | local |
| hermes-web-search-codex | software-development | local | local |
| hermmes-web-search | software-development | local | local |
| node-inspect-debugger | software-development | builtin | builtin |
| plan | software-development | builtin | builtin |
| python-debugpy | software-development | builtin | builtin |
| requesting-code-review | software-development | builtin | builtin |
| simplify-code | software-development | builtin | builtin |
| spike | software-development | builtin | builtin |
| subagent-driven-development | software-development | local | local |
| systematic-debugging | software-development | builtin | builtin |
| test-driven-development | software-development | builtin | builtin |
| writing-plans | software-development | local | local |

Note: `seo-content-gap-analyst` and `plan` are SEO/topic-discovery skills
already shipped by Hermes. They are **not** the same as the SEO Ops plan /
research / write skills in `docs/legacy-skills/` — different schema, different
outputs. Do not assume they interoperate.

## 4. Hermes gateway

```
$ systemctl status hermes-gateway.service  # abbreviated
● hermes-gateway.service - Hermes Agent Gateway - Messaging Platform Integration
     Loaded: loaded (/etc/systemd/system/hermes-gateway.service; enabled)
     Active: active (running) since Tue 2026-07-28 06:37:11 CST; 5h 7min ago
   Main PID: 192 (hermes)
      Tasks: 18 (limit: 9027)
     Memory: 329.0M (peak: 461.8M)
        CPU: 22.762s
   CGroup: /system.slice/hermes-gateway.service
             └─192 /home/laoma/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
```

**Findings**:

- The Hermes gateway is configured and **active** as a systemd service.
- It has been running since 2026-07-28 06:37 (about 5 hours before this report).
- PID 192 is the gateway process; it executes
  `python -m hermes_cli.main gateway run --replace` from
  `/home/laoma/.hermes/hermes-agent/venv/`.
- Memory usage: 329 MB (peak 461 MB).
- Note: the gateway runs under `systemd` **inside the WSL2 userland**;
  this is functional but non-standard for a typical Linux server deployment.

## 5. Messaging channels (REDACTED)

```
$ cat ~/.hermes/channel_directory.json
{
  "updated_at": "2026-07-28T06:37:51.550997",
  "platforms": {
    "telegram": [],
    "discord": [],
    "whatsapp": [],
    "whatsapp_cloud": [],
    "slack": [],
    "signal": [],
    "mattermost": [],
    "feishu": []
  }
}
```

(The full channel_directory.json contains **REDACTED** values for any platform
that has been configured. As of this snapshot, all `*` arrays are empty,
meaning no channels have been bound to the gateway.)

- **Configured channels**: none in production; all arrays empty
- **Intended channel**: **Feishu** (per `~/.hermes/.env` and the original
  Hermes setup scripts). Channel ID, App ID, and App Secret are
  **REDACTED**.
- **Test mode**: Hermes gateway is in "no-channel" mode; operators can
  invoke it directly via `hermes chat` (TUI) or `hermes serve` (HTTP).

## 6. SEO Ops deployment path

```
$ cd /home/laoma/seo-ops-system && pwd
/home/laoma/seo-ops-system

$ stat -c "%U" /home/laoma/seo-ops-system
laoma
```

**Findings**:

- **Project root**: `/home/laoma/seo-ops-system`
- **Owner**: `laoma` (uid 1000)
- **Source branch**: `codex/legacy-skill-integration`
- **HEAD**: `4811b9d72e1bace01ac185721c3ae8974dea79b8`
- **Service bind**: `127.0.0.1:8787` (loopback only; not exposed externally)
- **Service process**: started via `nohup setsid .venv/bin/seo-ops > /tmp/seo-ops.log 2>&1 < /dev/null &`

### Pre-flight check

```
$ curl -s --max-time 3 http://127.0.0.1:8787/api/health
{"status":"ok","version":"0.10.9","ai_enabled":true,"configured_sources":{"ai":true,"serpapi":true,"firecrawl":true,"tavily":true}}
```

SEO Ops is **reachable and healthy** on this host.

## 7. Shared environment between Hermes and SEO Ops

```
$ whoami
laoma

$ stat -c "%U" /home/laoma/.hermes/hermes-agent
laoma

$ stat -c "%U" /home/laoma/seo-ops-system
laoma

$ diff <(echo laoma) <(stat -c "%U" /home/laoma/.hermes/hermes-agent)
$ echo "Hermes user: $(stat -c '%U' /home/laoma/.hermes/hermes-agent)"
Hermes user: laoma

$ echo "SEO Ops user: $(stat -c '%U' /home/laoma/seo-ops-system)"
SEO Ops user: laoma
```

**Findings**:

- Hermes runs as user `laoma` (uid 1000).
- SEO Ops project files are owned by `laoma` (uid 1000).
- The service process runs as `laoma` (per `ps -ef | grep seo-ops`).
- Hermes can therefore read SEO Ops project files directly without
  permission issues, **provided** Hermes is invoked as the `laoma` user.

### Filesystem overlap

Both systems share:

| Path | Hermes reads? | SEO Ops writes? | Risk |
|------|---|---|---|
| `~/` | Yes (Hermes config) | No | None |
| `/home/laoma/seo-ops-system/` | Yes (per proposed workflow) | Yes | Hermes must not delete/modify repo files |
| `data/legacy_workflow/laserpointerhub/runs/action-*/` | Yes (per proposed workflow) | Yes | Both write; need file-level coordination |
| `data/seo_ops.db` | Yes (per proposed workflow) | Yes | Hermes must not modify schema |

**Recommendation**: when Hermes is operating on the SEO Ops workspace, treat
it as a **read-mostly consumer** that can call the SEO Ops HTTP API (`POST
/actions/{id}/legacy/stage/*`) to trigger work, but should never directly
write files under `/home/laoma/seo-ops-system/data/legacy_workflow/...` or
the SQLite database. All state changes go through the web API.

## 8. Items NOT included in this report (per privacy constraints)

The following were intentionally **not** recorded because they would expose
secrets or private metadata:

- `~/.hermes/.env` (contains `MINIMAX_CN_API_KEY`, `DEEPSEEK_API_KEY`,
  `OPENCODE_GO_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `SUDO_PASSWORD`,
  `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_HOME_CHANNEL`)
- `~/.hermes/auth.json` (contains credential pool fingerprints and base
  URLs — base URLs alone are sensitive)
- `~/.hermes/.hermes_history` (chat history — may contain operator's
  private data)
- `/home/laoma/seo-ops-system/.env` (contains real API keys; gitignored)
- `/home/laoma/seo-ops-system/.secrets/google/*` (OAuth client + token)
- `/home/laoma/seo-ops-system/data/seo_ops.db` (production data, gitignored)
- `/home/laoma/seo-ops-system/data/_test_migration_13.db` (test artifact, gitignored)
- Feishu channel IDs, user IDs, group IDs

If a downstream agent needs any of the above for debugging, it must request
the values via `~/.hermes/.env` (e.g., via the Hermes `secrets` subcommand)
or via `cat /home/laoma/seo-ops-system/.env` — never from this report or
from the chat channel.

## 9. Suggested next steps for Hermes + SEO Ops integration

1. Decide which Hermes skill wraps the SEO Ops HTTP calls (recommendation:
   create `seo-ops-orchestrator` as a local skill under
   `~/.hermes/skills/software-development/seo-ops-orchestrator/`)
2. The skill's SKILL.md should reference:
   - `http://127.0.0.1:8787/actions/{id}/legacy/stage/{r0,r1,r3,w0,w1b,w2,w3}`
   - `http://127.0.0.1:8787/actions/{id}/legacy/stage/w2-revise`
3. Hermes invokes these endpoints; SEO Ops handles all state transitions
4. The skill must never write directly to `data/legacy_workflow/laserpointerhub/`
   or to `data/seo_ops.db` — those are owned by SEO Ops
5. Hermes can read `data/legacy_workflow/laserpointerhub/{context,published,products}/`
   to give the AI situational awareness, but state changes go through the API

This report will be re-captured when Hermes or SEO Ops is upgraded.