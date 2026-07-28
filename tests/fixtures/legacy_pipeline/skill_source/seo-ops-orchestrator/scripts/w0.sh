#!/usr/bin/env bash
# W0 — Validate material pack + AI draft.
#
# POST /actions/{action_id}/legacy/stage/w0
# Body: form-urlencoded with `author=...`
#
# Usage: w0.sh [--base-url URL] [--timeout SECONDS] <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
w0.sh — run W0 (validate + AI draft) for an action

Usage:
    w0.sh [--base-url URL] [--timeout SECONDS] [--author NAME] <action_id>

Effects (handled by SEO Ops):
    1. Validates material-pack (write_collector.py validate)
    2. Invokes AI to write drafts/{slug}-{date}.md (full article with frontmatter)
    3. Clears downstream artifacts (pre-check, post-process, register, w2-state)
    4. Resets w2-state to {rounds:0, gate_passed:false, applied:false}
    5. Updates actions.legacy_stage = 'w1_draft' on success

Calls external AI (largest single AI call in the pipeline, ~16k tokens).
EOF
}

AUTHOR="LaserPointerHub"
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --author)
            AUTHOR="$2"
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
printf 'author=%s' "$(python3 -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.quote(sys.argv[1]))' "${AUTHOR}")" > "${TMP_FORM}"

http_post_form "/actions/${ACTION_ID}/legacy/stage/w0" "${TMP_FORM}"
summarize_response "${ACTION_ID}" w0