#!/usr/bin/env bash
# W3 — Register the article (write to internal-links-map + AI backlink checklist).
#
# POST /actions/{action_id}/legacy/stage/w3
#
# Usage: w3.sh [--base-url URL] [--timeout SECONDS] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
w3.sh — run W3 (register + backlink checklist) for an action

Usage:
    w3.sh [--base-url URL] [--timeout SECONDS] <action_id>

Effects (handled by SEO Ops):
    1. Verifies w2-state.{gate_passed, applied} are true (otherwise 4xx)
    2. Verifies the draft sha256 hasn't changed since apply (otherwise 4xx)
    3. Invokes write_collector.py register
    4. Updates internal-links-map.md with the new article
    5. Invokes AI to produce research/backlink-suggestions-{slug}-{date}.md
       (a checklist for operator approval; does NOT auto-edit old articles)
    6. Removes the material-pack file (it has been archived to libraries)
    7. Updates actions.legacy_stage = 'w3_register'
EOF
}

parse_flags "$@"
require_action_id "$@"
ACTION_ID="$1"

http_post_form_no_body "/actions/${ACTION_ID}/legacy/stage/w3"
summarize "${ACTION_ID}" w3