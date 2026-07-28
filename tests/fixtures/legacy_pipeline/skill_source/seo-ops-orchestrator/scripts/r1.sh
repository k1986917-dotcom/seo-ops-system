#!/usr/bin/env bash
# R1 — Save operator-pasted search results + run collect.
#
# POST /actions/{action_id}/legacy/stage/r1
# Body: form-urlencoded with `search_results=<stdin content>`
#
# Usage: r1.sh [--base-url URL] [--timeout SECONDS] <action_id> < search-results.md

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
r1.sh — run R1 (save search results + collect) for an action

Usage:
    r1.sh [--base-url URL] [--timeout SECONDS] <action_id>

Reads the search results text from stdin. Pass via:
    r1.sh 99 < path/to/search-results.md

Effects (handled by SEO Ops):
    1. Writes research/search-results-{slug}-{date}.md
    2. Clears downstream artifacts (score, brief, material-pack, draft)
    3. Invokes research_collector.py collect
    4. Generates research-data-{slug}-{date}.{md,json}
    5. Updates actions.legacy_stage = 'r2_collect' on success

The synthetic fixture workflow_run/02_search-results.md can be piped in for
end-to-end smoke tests.
EOF
}

parse_flags "$@"
set -- "${REMAINING_ARGS[@]}"
require_action_id "$@"
ACTION_ID="$1"

if [[ -t 0 ]]; then
    echo "r1.sh requires search_results on stdin (got a terminal)" >&2
    usage >&2
    exit 2
fi

# Build a curl-compatible form file in /tmp.
TMP_FORM=$(mktemp)
trap 'rm -f "${TMP_FORM}"' EXIT
{
    printf 'search_results='
    # urlencode the stdin content using python (always available with seo-ops venv).
    python3 -c '
import sys, urllib.parse
sys.stdout.write(urllib.parse.quote(sys.stdin.read(), safe=""))
'
} > "${TMP_FORM}"

http_post_form "/actions/${ACTION_ID}/legacy/stage/r1" "${TMP_FORM}"
summarize_response "${ACTION_ID}" r1
