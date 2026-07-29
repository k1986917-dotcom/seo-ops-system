---
name: seo-ops-orchestrator
description: Intake and drive the SEO Ops Legacy workflow through HTTP; never write to the workspace or DB directly.
version: 1.1.0
license: MIT
allowed-tools: bash, curl, jq
---

# seo-ops-orchestrator

Drive the SEO Ops Legacy workflow (Research + Write pipeline) by calling the
seven stage HTTP endpoints exposed by the `seo-ops` service. This skill is
the integration layer between Hermes (the AI orchestrator) and SEO Ops
(the deterministic state machine).

This skill must NEVER:

- Write directly to `data/seo_ops.db` (the SQLite database)
- Write, modify, or delete files under `data/legacy_workflow/laserpointerhub/`
  or any other workspace directory
- Invoke the legacy Python scripts directly (`write_collector.py`,
  `research_collector.py`, etc.) — they have no state machine of their own
- Restart or kill the `seo-ops` process
- Read or write `~/.hermes/.env` or `~/.hermes/auth.json`

All state changes go through HTTP. All reads go through HTTP. The skill is
a thin orchestrator that converts operator intent into POST requests.

## When to use

- For a new task, ask only the missing Socratic intake questions:
  - Which site, only when the user has more than one?
  - What is the exact article topic?
  - What reader/use case or article purpose is intended, if the topic does not
    make it clear?
  - Any must-include, must-avoid, language, country or source constraints that
    would change research; otherwise use site defaults.
  - Do not ask for an author unless the user requests a different byline;
    `LaserPointerHub` is the default.
- Run `scripts/start.sh --site ... --topic ... [--requirements ...]`. With
  configured providers, SEO Ops continues the full R0→W3 pipeline itself.
- If the response is `needs_manual_search`, report both the existing synced
  materials and the generated search prompt, then ask the operator to choose:
  use the existing materials, or paste external search results. Do not invent
  results or silently assume that the materials are sufficient.
- Resume with `scripts/continue.sh --use-existing <action_id>` or
  `scripts/continue.sh --search-file results.md <action_id>`. The server then
  performs all safe remaining stages and pauses after one two-round repair
  batch if W1b/W2 still fails.
- Operator accepts a research candidate and asks Hermes to start the article
- Operator pastes search results and asks Hermes to advance to AI analysis
- Operator wants to know the current stage of an article
- Operator asks to redo a stage because the result was unsatisfying
- A previous run was interrupted and the operator asks Hermes to resume

## Inputs the operator (or upstream skill) must supply

- `site` — numeric site ID or site slug
- `topic` — string, the article's topic
- `requirements` — optional constraints, audience or intended use
- For manual R1 fallback only: `search_results` — verbatim pasted text
- For W0 only: `author` — byline name (default `LaserPointerHub`)

## Configuration

The skill reads these environment variables:

- `SEO_OPS_BASE_URL` — base URL of the SEO Ops HTTP service
  (default `http://127.0.0.1:8787`)
- `SEO_OPS_COOKIE` — optional session cookie; required only if the
  `_require_local_form` middleware blocks the request (default: omit)

Override per-call via `--base-url` flag.

## Commands

| Script | HTTP call | Purpose |
|--------|-----------|---------|
| `scripts/start.sh` | `POST /api/hermes/runs` | Intake + R0 + automatic search + R1 |
| `scripts/continue.sh` | `POST /api/hermes/runs/{id}/continue` | Continue through R3→W3 or choose the manual-search branch |
| `scripts/detect_stage.sh` | `GET /actions` | Read current stage of an action |
| `scripts/r0.sh` | `POST /actions/{id}/legacy/stage/r0` | Generate search prompt |
| `scripts/r1.sh` | `POST /actions/{id}/legacy/stage/r1` | Save search results + run collect |
| `scripts/r3.sh` | `POST /actions/{id}/legacy/stage/r3` | AI analysis + score |
| `scripts/w0.sh` | `POST /actions/{id}/legacy/stage/w0` | Validate + draft |
| `scripts/w1b.sh` | `POST /actions/{id}/legacy/stage/w1b` | Pre-check (15 items) |
| `scripts/w2.sh` | `POST /actions/{id}/legacy/stage/w2` | Post-process (link + cannibal + score) |
| `scripts/w3.sh` | `POST /actions/{id}/legacy/stage/w3` | Register + backlink checklist |

All scripts print the HTTP status code and the redirect URL (303 → /actions)
on success, and exit 0. On failure they exit non-zero with the response body.

## Recommended workflow

```
1. scripts/start.sh --site <site> --topic <topic> [--requirements <text>]
   → creates/resumes one action, runs R0, searches configured providers, then
     automatically continues R1→R3→W0→W1b→W2→W3 when all gates pass

2. (only if start.sh returned needs_manual_search)
   → report the generated prompt + existing material summary and ask whether
     to use those materials or paste external search results

3. scripts/continue.sh --use-existing <action_id>
   or scripts/continue.sh --search-file results.md <action_id>
   → R1 through W3 run automatically. W1b/W2 use at most two revisions per
     batch, never bypass their evidence/quality gates, and pause truthfully if
     a batch still fails.
```

## Failure recovery

If a stage returns 303 with a redirect URL containing `?message=...&level=error`,
the operator (or this skill) must:

1. Read the message
2. Either fix the input and retry, OR
3. Roll back to an earlier stage by re-running it (re-running overwrites the
   old result and invalidates downstream stages automatically)

If the service is unreachable, do NOT attempt to repair files manually.
Instead, surface the error to the operator.

## What about r2 / r4 / r5 / w1?

**These do not exist as clickable stages.** They are internal state-machine
labels in SEO Ops's DB column `actions.legacy_stage`, set automatically
between button clicks. The seven clickable stages exposed by both the UI
and the HTTP API are: **r0, r1, r3, w0, w1b, w2, w3**.

The other labels are:

- `r2_collect` — set after R1 succeeds, before the operator clicks R3
- `r3_ai_analyze` / `r4_score` — set while R3 is running / score is incomplete
- `r5_write_ready` — set when R3 finishes with material-pack complete
- `w1_draft` — set after W0 succeeds, before the operator clicks W1b

These states are not exposed as buttons or endpoints. The skill only needs
to know the seven clickable stages. The `legacy_stage` column may briefly
hold an intermediate value between the operator's clicks, but the skill
should never POST to an endpoint that doesn't exist (`/legacy/stage/r2`,
`/legacy/stage/w1`, etc. all return 404).

## See also

- `install.sh` — copies this skill into `~/.hermes/skills/software-development/seo-ops-orchestrator/`
- `tests/fixtures/legacy_pipeline/README.md` — synthetic test data
- `tests/integration/test_hermes_orchestrator_smoke.py` — automated smoke test
- `docs/hermes/README.md` — Hermes integration overview
