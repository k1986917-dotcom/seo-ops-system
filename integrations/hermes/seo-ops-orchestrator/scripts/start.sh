#!/usr/bin/env bash
# Start the first automatic Hermes slice: intake -> R0 -> external search -> R1.
#
# Usage:
#   start.sh [--base-url URL] [--timeout SECONDS] [--restart] \
#     --site SITE --topic TOPIC [--requirements TEXT]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
start.sh — start a Hermes-managed Legacy task

Usage:
    start.sh [--base-url URL] [--timeout SECONDS] [--restart]
             --site SITE --topic TOPIC [--requirements TEXT]

SITE may be a numeric site_id or a site slug. The endpoint creates/resumes one
accepted create action, runs R0, calls configured SerpAPI/Tavily providers, and
feeds their immutable search snapshot into Legacy R1. If no provider is
configured, the command exits 0 with status=needs_manual_search so Hermes can
ask whether the operator wants to paste results.
EOF
}

SITE=""
TOPIC=""
REQUIREMENTS=""
RESTART="false"
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url)
            SEO_OPS_BASE_URL="$2"
            shift 2
            ;;
        --timeout)
            SEO_OPS_TIMEOUT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --site)
            SITE="$2"
            shift 2
            ;;
        --topic)
            TOPIC="$2"
            shift 2
            ;;
        --requirements)
            REQUIREMENTS="$2"
            shift 2
            ;;
        --restart)
            RESTART="true"
            shift
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#ARGS[@]} -ne 0 || -z "${SITE}" || -z "${TOPIC}" ]]; then
    usage >&2
    exit 2
fi

TMP_JSON=$(mktemp)
trap 'rm -f "${TMP_JSON}"' EXIT
python3 - "${SITE}" "${TOPIC}" "${REQUIREMENTS}" "${RESTART}" > "${TMP_JSON}" <<'PY'
import json
import sys

site, topic, requirements, restart = sys.argv[1:]
value = {
    "site": site,
    "topic": topic,
    "requirements": requirements,
    "restart": restart == "true",
}
print(json.dumps(value, ensure_ascii=False))
PY

http_post_json() {
    local path="$1"
    local data_file="$2"
    local url="${SEO_OPS_BASE_URL}${path}"
    local body_file
    body_file=$(mktemp)
    local trailer
    if ! trailer=$(curl --silent --show-error --max-time "${SEO_OPS_TIMEOUT}" \
        --request POST --header 'Content-Type: application/json' \
        --data-binary "@${data_file}" --output "${body_file}" \
        --write-out '%{http_code}' "${url}" 2>&1); then
        rm -f "${body_file}"
        echo "curl failed for ${url}" >&2
        return 1
    fi
    HTTP_CODE="${trailer}"
    HTTP_BODY=$(cat "${body_file}")
    rm -f "${body_file}"
    echo "${HTTP_BODY}"
}

http_post_json "/api/hermes/runs" "${TMP_JSON}"
echo "[start] action/status response=${HTTP_CODE:-?}"
if [[ "${HTTP_CODE:-}" =~ ^2 ]]; then
    exit 0
fi
exit 1
