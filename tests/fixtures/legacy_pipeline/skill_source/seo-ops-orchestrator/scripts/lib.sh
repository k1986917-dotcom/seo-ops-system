#!/usr/bin/env bash
# Shared helpers for seo-ops-orchestrator stage scripts.
# All scripts source this file and call http_get / http_post_form[_no_body].
#
# HTTP semantics: SEO Ops returns 303 See Other for both success and
# business failure. The Location query string carries `level=success|error|warning`
# and a URL-encoded `message=...`. Scripts must read the Location header,
# not just the status code, to decide exit code.

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
: "${SEO_OPS_BASE_URL:=http://127.0.0.1:8787}"
: "${SEO_OPS_TIMEOUT:=60}"

# ── Flag parsing ─────────────────────────────────────────────────────────
#
# parse_flags <args...>
#   Consumes the script's own positional parameters. After this call,
#   REMAINING_ARGS holds the non-flag arguments and SEO_OPS_BASE_URL /
#   SEO_OPS_TIMEOUT are set.
#
# Usage in a script:
#     parse_flags "$@"; set -- "${REMAINING_ARGS[@]}"
#     require_action_id "$@"; ACTION_ID="$1"
#
# Each script must use this two-line pattern. parse_flags cannot mutate
# the caller's "$@" directly (Bash function $@ is local).

parse_flags() {
    SEO_OPS_BASE_URL="${SEO_OPS_BASE_URL:-http://127.0.0.1:8787}"
    SEO_OPS_TIMEOUT="${SEO_OPS_TIMEOUT:-60}"
    REMAINING_ARGS=()
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
                if declare -F usage > /dev/null; then
                    usage
                else
                    echo "parse_flags: --help but no usage() defined" >&2
                fi
                exit 0
                ;;
            --)
                shift
                REMAINING_ARGS+=("$@")
                return 0
                ;;
            -*)
                echo "Unknown flag: $1" >&2
                return 2
                ;;
            *)
                REMAINING_ARGS+=("$1")
                shift
                ;;
        esac
    done
}

# Require exactly one positional argument (action_id) in the given array.
require_action_id() {
    local args=("$@")
    if [[ ${#args[@]} -lt 1 ]]; then
        if declare -F usage > /dev/null; then
            usage >&2
        fi
        echo "Missing action_id" >&2
        exit 2
    fi
    if [[ ${#args[@]} -gt 1 ]]; then
        echo "Too many arguments: ${args[*]}" >&2
        exit 2
    fi
    if ! [[ "${args[0]}" =~ ^[0-9]+$ ]]; then
        echo "action_id must be a positive integer, got: ${args[0]}" >&2
        exit 2
    fi
}

# ── URL decoding helper (for Location query strings) ─────────────────────

# Decode a URL-encoded string and write the result to stdout. Uses Python
# so we don't have to depend on GNU/BSD-specific shell URL decoders.
urldecode() {
    python3 -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.unquote(sys.argv[1]))' "$1"
}

# ── HTTP layer ──────────────────────────────────────────────────────────
#
# All three http_* helpers follow the same protocol:
#   - Set globals: HTTP_CODE, HTTP_BODY, HTTP_LOCATION
#   - Print HTTP_BODY to stdout
#   - Return 0 on transport success, 1 on transport failure
#
# They do NOT decide exit code from HTTP_CODE alone — that is the caller's
# job (typically via summarize_response). A 303 with level=error should
# still exit non-zero; a 303 with level=success exits 0.

http_post_form() {
    local path="$1"
    local data_file="${2:-}"
    _do_curl POST "${path}" "${data_file}"
}

http_post_form_no_body() {
    local path="$1"
    _do_curl POST "${path}" ""
}

http_get() {
    local path="$1"
    _do_curl GET "${path}" ""
}

_do_curl() {
    local method="$1"
    local path="$2"
    local data_file="$3"

    local url="${SEO_OPS_BASE_URL}${path}"
    local body_file
    body_file=$(mktemp)
    local curl_args=(
        --silent
        --show-error
        --max-time "${SEO_OPS_TIMEOUT}"
        --request "${method}"
        --output "${body_file}"
        --write-out '%{http_code}\n%{redirect_url}'
    )

    if [[ -n "${data_file}" ]]; then
        curl_args+=(--data-binary "@${data_file}")
    fi

    local trailer
    if ! trailer=$(curl "${curl_args[@]}" "${url}" 2>&1); then
        rm -f "${body_file}"
        echo "curl failed for ${url}" >&2
        return 1
    fi

    HTTP_BODY=$(cat "${body_file}")
    rm -f "${body_file}"

    # trailer = "code\nlocation"
    HTTP_CODE="${trailer%%$'\n'*}"
    local rest="${trailer#*$'\n'}"
    HTTP_LOCATION="${rest}"

    echo "${HTTP_BODY}"
    return 0
}

# ── 303 response handling ───────────────────────────────────────────────
#
# summarize_response <action_id> <stage>
#   Read HTTP_CODE and HTTP_LOCATION; print a human-readable summary
#   and return the right exit code.
#   - level=success → exit 0
#   - level=warning → exit 0 (warning is informational)
#   - level=error   → exit 1
#   - non-303, non-2xx → exit 1
#   - no level in Location → exit 0 only if 2xx, else 1

summarize_response() {
    local action_id="$1"
    local stage="$2"

    echo "[${stage}] action=${action_id} base_url=${SEO_OPS_BASE_URL}"
    echo "[${stage}] http_status=${HTTP_CODE:-?} location=${HTTP_LOCATION:-<none>}"

    # Parse level= and message= from the Location query string. We use Python
    # because the URL may have the parameter as the first one (preceded by
    # `?` not `&`), and sed's greedy matching is fragile in that case.
    local level="unknown"
    local message=""
    if [[ -n "${HTTP_LOCATION:-}" ]]; then
        level=$(python3 -c '
import sys, urllib.parse
q = urllib.parse.urlparse(sys.argv[1]).query
params = urllib.parse.parse_qs(q)
print(params.get("level", ["unknown"])[0])
' "${HTTP_LOCATION}")
        message=$(python3 -c '
import sys, urllib.parse
q = urllib.parse.urlparse(sys.argv[1]).query
params = urllib.parse.parse_qs(q)
print(params.get("message", [""])[0])
' "${HTTP_LOCATION}")
    fi

    case "${level}" in
        success)
            echo "[${stage}] level=success message=$(urldecode "${message}")"
            return 0
            ;;
        warning)
            echo "[${stage}] level=warning message=$(urldecode "${message}")" >&2
            return 0
            ;;
        error)
            echo "[${stage}] level=error message=$(urldecode "${message}")" >&2
            return 1
            ;;
    esac

    # No level in Location — fall back to status code
    if [[ "${HTTP_CODE:-}" =~ ^2 ]]; then
        echo "[${stage}] level=unknown (2xx without level=)"
        return 0
    fi
    if [[ "${HTTP_CODE:-}" =~ ^3 ]]; then
        echo "[${stage}] level=unknown (3xx without level=) — treating as failure" >&2
        return 1
    fi
    echo "[${stage}] level=unknown http_status=${HTTP_CODE:-?}" >&2
    return 1
}

# Backwards-compat alias for the original summarize() that always said "ok".
# New code should call summarize_response instead.
summarize() {
    summarize_response "$@"
}
