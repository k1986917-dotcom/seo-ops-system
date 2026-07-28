#!/usr/bin/env bash
# R0 — Generate search prompt.
#
# POST /actions/{action_id}/legacy/stage/r0
#
# Usage: r0.sh [--base-url URL] [--timeout SECONDS] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
r0.sh — run R0 (generate search prompt) for an action

Usage:
    r0.sh [--base-url URL] [--timeout SECONDS] <action_id>

Effects (handled by SEO Ops):
    1. Syncs DB → workspace (legacy_sync_all)
    2. Creates runs/action-{id}/current/laserpointerhub/ if missing
    3. Clears all prior artifacts for this action's slug
    4. Generates 8-section search prompt from context + published + products
    5. Writes research/search-prompt-{slug}-{date}.md
    6. Updates actions.legacy_stage = 'r0_prompt'
EOF
}

parse_flags "$@"
require_action_id "$@"
ACTION_ID="$1"

http_post_form_no_body "/actions/${ACTION_ID}/legacy/stage/r0"
summarize "${ACTION_ID}" r0