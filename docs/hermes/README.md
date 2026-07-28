# Hermes 接入说明

> **Status (2026-07-28)**: Hermes Agent v0.19.0 is installed at
> `~/.hermes/hermes-agent/`, source commit `ef267011`. The CLI command is
> **`hermes`** (not `tirith` — that's a leftover binary from a previous
> install; it is not on `$PATH` and should not be invoked). The gateway
> runs under systemd as user `laoma`. No messaging channels are bound
> yet. See `docs/hermes/RUNTIME_REPORT.md` for full command output.

## What Hermes does for the SEO Ops system

Hermes (Hermes Agent by Nous Research, https://nousresearch.com) is the
**runtime AI orchestrator** that:

- Loads SKILL definitions written by the SEO Ops team and exposes them as
  invocable commands inside an AI session
- Drives the SEO Ops HTTP API to advance each article through the Legacy
  research/write state machine
- Calls external APIs (SerpAPI, Firecrawl, Tavily, AI provider) using
  keys stored in `~/.hermes/.env`
- Reports progress and asks the operator to confirm decisions via its
  configured messaging channel

Hermes does **not** participate in SEO Ops's HTTP service. SEO Ops's
`seo-ops` process listens on `127.0.0.1:8787` independently; Hermes drives
it through that API.

## Version and file locations

| Item | Value |
|------|-------|
| Hermes CLI | `hermes` (installed at `/home/laoma/.local/bin/hermes`) |
| Source install | `~/.hermes/hermes-agent/` (git clone of NousResearch/hermes-agent) |
| Source commit | `ef267011348a7bc67ad3f46c9b0a0dcb0f4b7342` |
| Install method | git (per `hermes --version`) |
| Python runtime | 3.11.15 (Hermes's own venv) |
| Config dir | `~/.hermes/` |
| Main config | `~/.hermes/config.yaml` (YAML, no secrets) |
| Environment vars | `~/.hermes/.env` (CONTAINS SECRETS — do not commit) |
| Credential pool | `~/.hermes/auth.json` (fingerprints + base URLs only) |
| System prompt | `~/.hermes/SOUL.md` |
| Skills dir | `~/.hermes/skills/` (NOT `~/.opencode/skills/`) |
| Gateway binary | `/home/laoma/.hermes/hermes-agent/venv/bin/hermes` |

This repo provides reference copies of the configuration files:

- `docs/hermes/SOUL.md` — copy of the Hermes system prompt
- `docs/hermes/config.example.yaml` — full config schema with all keys
  REDACTED
- `docs/hermes/RUNTIME_REPORT.md` — actual `hermes --version` / `hermes
  skills list` / `systemctl status hermes-gateway.service` output captured
  on the live host

> **Do not commit `~/.hermes/.env` or `~/.hermes/auth.json`** — those contain
> real API keys and credentials. The schema-only versions live in this
> repo as `config.example.yaml`.

## Two integration modes (in priority order)

### Mode A: Hermes orchestrates SEO Ops through HTTP API (RECOMMENDED)

Hermes reads SKILL definitions written by SEO Ops, then drives the SEO Ops
state machine via HTTP:

```
1. SEO Ops exposes:
   GET  /actions/{id}/legacy/             → list R0..W3 buttons
   POST /actions/{id}/legacy/stage/r0     → generate search prompt
   POST /actions/{id}/legacy/stage/r1     → save search results + collect
   POST /actions/{id}/legacy/stage/r3     → AI analysis + score
   POST /actions/{id}/legacy/stage/w0     → validate + draft
   POST /actions/{id}/legacy/stage/w1b    → pre-check (15 items)
   POST /actions/{id}/legacy/stage/w2     → post-process (link + cannibal + score)
   POST /actions/{id}/legacy/stage/w2-revise → AI revision
   POST /actions/{id}/legacy/stage/w3     → register + backlink

2. The action's topic (target_ref) and DB state live in
   data/seo_ops.db (SQLite). Hermes reads but never writes the DB
   directly.

3. All workflow artifacts (search-prompt, material-pack, draft, reports)
   live under:
   data/legacy_workflow/laserpointerhub/runs/action-{id}/current/laserpointerhub/

   Hermes can READ these for situational awareness but must invoke
   stage HTTP endpoints to mutate them.

4. Hermes reports progress via its configured channel (Feishu by
   default — REDACTED in this repo).
```

This is the recommended mode. It keeps SEO Ops as the source of truth for
state and lets Hermes focus on AI-driven decisions.

### Mode B: Hermes invokes the legacy scripts directly (FALLBACK)

Hermes can also call `data_sources/modules/*.py` directly via shell, but
this bypasses SEO Ops's state machine and risks stale artifacts. Only use
Mode B for offline batch analysis (e.g., regenerating all topic-context
files for a content audit). Never use it for live article workflows.

## Status: Mode A is the recommendation; Mode B is documented for
backward compatibility only.

## Required environment variables

Hermes loads keys from `~/.hermes/.env`. SEO Ops loads keys from
`<repo>/.env`. **These two files are completely separate.**

| Variable (Hermes) | Purpose | Required? |
|--------------------|---------|-----------|
| `MINIMAX_CN_API_KEY` | Hermes's own AI provider | Hermes itself |
| `DEEPSEEK_API_KEY` | SEO Ops AI provider (forwarded to `SEO_OPS_AI_API_KEY`) | AI features |
| `OPENCODE_GO_API_KEY` | Backup AI provider | Optional |
| `FIRECRAWL_API_KEY` | Firecrawl external research | External research |
| `TAVILY_API_KEY` | Tavily external research | External research |
| `SERPAPI_API_KEY` | SerpAPI SERP verification | SERP features |
| `FEISHU_APP_ID` | Feishu channel app id | Feishu channel |
| `FEISHU_APP_SECRET` | Feishu channel app secret | Feishu channel |
| `FEISHU_HOME_CHANNEL` | Default channel id | Feishu channel |
| `SUDO_PASSWORD` | sudo helper (used by some install paths) | Optional |

SEO Ops uses its own `SEO_OPS_*` variables in `<repo>/.env` (see
`.env.example` for the full list).

> Hermes does **not** read `SEO_OPS_*` variables. SEO Ops does **not**
> read `MINIMAX_CN_API_KEY` / `DEEPSEEK_API_KEY` (uppercase form). If you
> want Hermes to invoke SEO Ops with a specific AI key, copy it to
> `<repo>/.env` as `SEO_OPS_AI_API_KEY` independently.

## How to install Hermes (for reference)

The host captured in `RUNTIME_REPORT.md` already has Hermes installed. For
a fresh install:

```bash
# Option A: official one-liner (installs hermes + venv)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/install.sh | bash

# Option B: from source
git clone https://github.com/NousResearch/hermes-agent.git ~/.hermes/hermes-agent
cd ~/.hermes/hermes-agent
python3 -m venv venv
./venv/bin/pip install -e .
```

After install, the `hermes` binary should be on `$PATH`:

```bash
hermes --version
# Hermes Agent v0.19.0 (date) · upstream <commit>
# Install directory: /home/laoma/.hermes/hermes-agent
```

## Skills directory layout (Hermes format)

Hermes expects skills at `~/.hermes/skills/<category>/<skill-name>/SKILL.md`.
The SEO Ops repo's `docs/legacy-skills/{plan,research,write}/SKILL.md` files
are **not** in Hermes's native format and would need to be wrapped:

```
~/.hermes/skills/
└── software-development/
    └── seo-ops-orchestrator/
        ├── SKILL.md                  # frontmatter + workflow steps
        ├── README.md                 # what this skill does
        ├── scripts/
        │   ├── stage_r0.sh           # POST /actions/{id}/legacy/stage/r0
        │   ├── stage_r1.sh           # POST with search_text
        │   └── ...
        └── helpers/
            └── detect_stage.sh       # GET /actions/{id} and read legacy.stage
```

This conversion is **planned** but **not done**. See
`docs/KNOWN_ISSUES.md` for the gap.

## Gateway status

The Hermes gateway runs under systemd. To check / restart:

```bash
systemctl status hermes-gateway.service
systemctl restart hermes-gateway.service
```

If `hermes-gateway.service` is not present (fresh install), start the
gateway with:

```bash
hermes gateway start --replace
```

## Common failures

| Symptom | Cause |
|---------|-------|
| `ModuleNotFoundError: data_sources` when starting SEO Ops | PYTHONPATH missing; add project root: `PYTHONPATH=/home/laoma/seo-ops-system nohup setsid .venv/bin/seo-ops ...` |
| Hermes says "AI not configured" | `~/.hermes/.env` missing `DEEPSEEK_API_KEY` or `MINIMAX_CN_API_KEY` |
| Feishu messages not arriving | `FEISHU_HOME_CHANNEL` wrong; or app not in channel |
| SEO Ops stages return 4xx | action's legacy_stage already past that stage; rerun R0 to reset |
| Hermes warns "Action blocked" | `data/seo_ops.db` row missing required fields (check `actions.workflow_status`) |
| `tirith: command not found` | Normal; use `hermes` instead |

## Security boundaries (from AGENTS.md)

- Hermes's `.env`, chat history, checkpoints **never** enter the SEO Ops
  repo
- SEO Ops's `.env`, OAuth token, SQLite **never** enter Hermes config dir
- Two systems share only `data/legacy_workflow/laserpointerhub/` and the
  `data/seo_ops.db` SQLite file
- Restarting one does not affect the other (separate processes)
- Both run as user `laoma` (verified in `RUNTIME_REPORT.md` §7)

## Updating this document

- Whenever Hermes is upgraded, re-capture `hermes --version` and
  `hermes skills list` into `RUNTIME_REPORT.md`
- Whenever the integration mode changes, update the relevant section here
- Whenever a new SKILL.md is added to `docs/legacy-skills/`, note it as
  "available, not yet wrapped as Hermes skill"