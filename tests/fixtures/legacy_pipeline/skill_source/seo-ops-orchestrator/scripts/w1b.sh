#!/usr/bin/env bash
# W1b — 15-item pre-check.
#
# POST /actions/{action_id}/legacy/stage/w1b
# Body: form-urlencoded with `tier=...` (optional)
#
# Usage: w1b.sh [--base-url URL] [--timeout SECONDS] [--tier TIER] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
w1b.sh — run W1b (15-item pre-check) for an action

Usage:
    w1b.sh [--base-url URL] [--timeout SECONDS] [--tier TIER] <action_id>

TIER values: Pillar Page | Cluster Content | Product Roundup (default: inherits
from topic-context.json).

Effects (handled by SEO Ops):
    1. Invokes write_pre_check.py --json
    2. Writes reports/pre-check-{slug}-*.md
    3. Clears downstream (post-process, register, w2-state)
    4. Sets w2-state.precheck_passed = (fail_count == 0)
    5. Updates actions.legacy_stage = 'w1b_pre_check' on success

Note: re-running W1b after editing the draft is how you "invalidate" a stale
gate verdict without touching any other stage.
EOF
}

TIER=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            TIER="$2"
            shift 2
            ;;
        --base-url)
            SEO_OPS_BASE_URL="$2"
            shift 2
            ;;
        --timeout)
            SEO_OPS_TIMEOUT="$2"
            shift 2
            ;;
        --)
            shift
            ARGS+=("$@")
            break
        ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#ARGS[@]} -ne 1 ]]; then
    usage >&2
    exit 2
fi
ACTION_ID="${ARGS[0]}"
if ! [[ "${ACTION_ID}" =~ ^[0-9]+$ ]]; then
    echo "action_id must be a positive integer, got: ${ACTION_ID}" >&2
    exit 2
fi

TMP_FORM=$(mktemp)
trap 'rm -f "${TMP_FORM}"' EXIT
if [[ -n "${TIER}" ]]; then
    printf 'tier=%s' "$(python3 -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.quote(sys.argv[1]))' "${TIER}")" > "${TMP_FORM}"
else
    : > "${TMP_FORM}"
fi

http_post_form "/actions/${ACTION_ID}/legacy/stage/w1b" "${TMP_FORM}"
summarize_response "${ACTION_ID}" w1b