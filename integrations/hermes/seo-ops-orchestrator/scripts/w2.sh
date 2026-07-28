#!/usr/bin/env bash
# W2 — Post-process (link + cannibal + score + apply).
#
# POST /actions/{action_id}/legacy/stage/w2
# Body: form-urlencoded with optional `apply=1` and `force=1`
#
# Usage: w2.sh [--base-url URL] [--timeout SECONDS] [--apply] [--force] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
w2.sh — run W2 (post-process) for an action

Usage:
    w2.sh [--base-url URL] [--timeout SECONDS] [--apply] [--force] <action_id>

--apply    writes the link fields back into the draft frontmatter
           (required before W3)
--force    bypasses the cannibalization block IF the revision cap is hit
           and the operator has manually confirmed the angle is different

Effects (handled by SEO Ops):
    1. Invokes write_collector.py post-process --apply [--force]
    2. Writes reports/post-process-{slug}-*.md
    3. Sets w2-state.{gate_passed, applied, score, cannibal}
    4. Updates actions.legacy_stage = 'w2_post_process' on success
EOF
}

parse_flags "$@"

APPLY="0"
FORCE="0"
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)
            APPLY="1"
            shift
            ;;
        --force)
            FORCE="1"
            shift
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
python3 -c '
import sys, urllib.parse
parts = []
if sys.argv[1] == "1":
    parts.append("apply=1")
if sys.argv[2] == "1":
    parts.append("force=1")
sys.stdout.write("&".join(parts))
' "${APPLY}" "${FORCE}" > "${TMP_FORM}"

http_post_form "/actions/${ACTION_ID}/legacy/stage/w2" "${TMP_FORM}"
summarize "${ACTION_ID}" w2