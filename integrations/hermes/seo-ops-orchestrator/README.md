# seo-ops-orchestrator

Hermes skill that drives the SEO Ops Legacy workflow through HTTP.

## Install

```bash
./install.sh                          # installs to ~/.hermes/skills/software-development/seo-ops-orchestrator/
hermes skills list                     # should show "seo-ops-orchestrator"
```

`install.sh` is idempotent — it refuses to overwrite an existing install
unless you pass `--force`.

To uninstall:

```bash
rm -rf ~/.hermes/skills/software-development/seo-ops-orchestrator/
```

## What this skill does

For a new task, `start.sh` creates/resumes the action, runs R0, calls the
configured SerpAPI/Tavily providers, and feeds the immutable search snapshot
into Legacy R1. The remaining stage scripts POST to the corresponding SEO Ops
endpoints for the existing deterministic pipeline.
The skill never writes to the workspace or the SQLite database directly;
all state changes go through HTTP.

## Configuration

Set these before invoking the scripts:

```bash
export SEO_OPS_BASE_URL=http://127.0.0.1:8787    # default
```

Override per-call with `--base-url`.

## Scripts

- `SKILL.md` — Hermes-format skill definition (frontmatter + workflow)
- `scripts/start.sh` — intake + R0 + automatic search + R1
- `scripts/detect_stage.sh` — read current stage
- `scripts/r0.sh` — generate search prompt
- `scripts/r1.sh` — save search results + run collect
- `scripts/r2-revise.sh` — AI revises draft (separate from W2)
- `scripts/r3.sh` — AI analysis + score
- `scripts/w0.sh` — validate + draft
- `scripts/w1b.sh` — pre-check (15 items)
- `scripts/w2.sh` — post-process
- `scripts/w3.sh` — register
- `scripts/lib.sh` — shared helpers (curl wrapper, error handling)
- `install.sh` — deploy this skill to `~/.hermes/skills/`

## Examples

```bash
# Read current stage for action 99
./scripts/detect_stage.sh 99

# Run R0
./scripts/r0.sh 99

# Run R1 with operator-pasted search results
./scripts/r1.sh 99 < /path/to/search-results.md

# Run all stages in sequence (only when fully synthetic data is in place)
for s in r0 r1 r3 w0 w1b w2 w3; do
    ./scripts/${s}.sh 99
done
```

## Exit codes

- `0` — HTTP 200, 303, or 4xx (operator-visible error)
- `1` — connection failed, timeout, or unexpected HTTP status

`curl` exit codes are translated: 0, 22, 7, 28 → all become 1.

## What this skill does NOT do

- It does NOT start, stop, or restart the SEO Ops service
- It does NOT write to `data/seo_ops.db` directly
- It does NOT write to `data/legacy_workflow/laserpointerhub/`
- It does NOT call legacy Python scripts directly (`write_collector.py`, etc.)
- It does NOT read or write `~/.hermes/.env` or `~/.hermes/auth.json`

If the service is down, the skill reports the failure and asks the
operator to restart it manually. See `docs/hermes/README.md` §"Common failures".
