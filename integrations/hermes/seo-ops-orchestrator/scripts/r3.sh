#!/usr/bin/env bash
# R3 — Deterministic score + AI material-pack + brief.
#
# POST /actions/{action_id}/legacy/stage/r3
#
# Usage: r3.sh [--base-url URL] [--timeout SECONDS] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
r3.sh — run R3 (score + AI material-pack + brief) for an action

Usage:
    r3.sh [--base-url URL] [--timeout SECONDS] <action_id>

Effects (handled by SEO Ops):
    1. Invokes research_scorer.py (deterministic 6-factor score)
    2. If score failed, returns error and aborts
    3. Invokes AI to write material-packs/{slug}-{date}.md
    4. Invokes AI to write research/brief-{slug}-{date}.md
    5. Clears downstream artifacts (draft, pre-check, post-process, register)
    6. Updates actions.legacy_stage = 'r5_write_ready' on success

Calls external AI (real network + budget consumption). For testing, use
the synthetic test mode in tests/integration/test_hermes_orchestrator_smoke.py.
EOF
}

parse_flags "$@"
set -- "${REMAINING_ARGS[@]}"
require_action_id "$@"
ACTION_ID="$1"

http_post_form_no_body "/actions/${ACTION_ID}/legacy/stage/r3"
summarize_response "${ACTION_ID}" r3
