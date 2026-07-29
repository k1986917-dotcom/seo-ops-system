#!/usr/bin/env bash
# Continue a Hermes-managed action through every safe remaining stage.
#
# Usage:
#   continue.sh [--base-url URL] [--timeout SECONDS] [--use-existing] <action_id>
#   continue.sh [--base-url URL] [--timeout SECONDS] --search-file FILE <action_id>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
continue.sh — continue a Hermes-managed SEO Ops action

Usage:
    continue.sh [--base-url URL] [--timeout SECONDS] [--use-existing] <action_id>
    continue.sh [--base-url URL] [--timeout SECONDS] --search-file FILE <action_id>

Without a search option this resumes an action already at R2 or later.
--use-existing confirms that Hermes may submit the already-synced workspace
materials as the R1 input when automatic external search was unavailable.
--search-file submits verbatim operator-provided external search results.

The server, not this script, performs every stage and enforces W1b/W2/W3 gates.
EOF
}

USE_EXISTING="false"
SEARCH_FILE=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url)
            SEO_OPS_BASE_URL="$2"; shift 2 ;;
        --timeout)
            SEO_OPS_TIMEOUT="$2"; shift 2 ;;
        --use-existing)
            USE_EXISTING="true"; shift ;;
        --search-file)
            SEARCH_FILE="$2"; shift 2 ;;
        --help|-h)
            usage; exit 0 ;;
        *)
            ARGS+=("$1"); shift ;;
    esac
done

require_action_id "${ARGS[@]}"
ACTION_ID="${ARGS[0]}"
if [[ "${USE_EXISTING}" == "true" && -n "${SEARCH_FILE}" ]]; then
    echo "Choose only one of --use-existing or --search-file" >&2
    exit 2
fi
if [[ -n "${SEARCH_FILE}" && ! -f "${SEARCH_FILE}" ]]; then
    echo "Search file does not exist: ${SEARCH_FILE}" >&2
    exit 2
fi

TMP_JSON=$(mktemp)
trap 'rm -f "${TMP_JSON}"' EXIT
python3 - "${USE_EXISTING}" "${SEARCH_FILE}" > "${TMP_JSON}" <<'PY'
import json
import pathlib
import sys

use_existing, search_file = sys.argv[1:]
payload = {}
if use_existing == "true":
    payload["search_decision"] = "use_existing"
elif search_file:
    payload["search_decision"] = "manual_search"
    payload["search_results"] = pathlib.Path(search_file).read_text(encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
PY

url="${SEO_OPS_BASE_URL}/api/hermes/runs/${ACTION_ID}/continue"
if ! response=$(curl --silent --show-error --max-time "${SEO_OPS_TIMEOUT}" \
    --request POST --header 'Content-Type: application/json' \
    --data-binary "@${TMP_JSON}" --write-out $'\n%{http_code}' "${url}"); then
    echo "curl failed for ${url}" >&2
    exit 1
fi
code="${response##*$'\n'}"
body="${response%$'\n'*}"
echo "${body}"
echo "[continue] action=${ACTION_ID} http_status=${code}"
[[ "${code}" =~ ^2 ]]
