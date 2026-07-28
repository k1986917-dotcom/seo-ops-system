#!/usr/bin/env bash
# W2-revise — AI revises the draft, then re-runs W1b + W2.
#
# POST /actions/{action_id}/legacy/stage/w2-revise
#
# Usage: w2-revise.sh [--base-url URL] [--timeout SECONDS] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
w2-revise.sh — AI-revise and re-run pre-check + post-process

Usage:
    w2-revise.sh [--base-url URL] [--timeout SECONDS] <action_id>

Effects (handled by SEO Ops):
    1. Backs up the current draft to {slug}-{date}.rev{N}.md
    2. Increments w2-state.rounds
    3. If rounds >= MAX_REVISION_ROUNDS (2), refuses
    4. Invokes AI to revise the draft
    5. Re-runs W1b
    6. If W1b still fails, stops with a failure report
    7. Otherwise, re-runs W2
EOF
}

parse_flags "$@"
require_action_id "$@"
ACTION_ID="$1"

http_post_form_no_body "/actions/${ACTION_ID}/legacy/stage/w2-revise"
summarize "${ACTION_ID}" w2-revise