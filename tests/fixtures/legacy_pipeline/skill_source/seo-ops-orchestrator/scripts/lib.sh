#!/usr/bin/env bash
# Shared helpers for seo-ops-orchestrator stage scripts.
# All scripts source this file and call stage_<name>().

set -euo pipefail

# Resolve the directory this lib.sh lives in, regardless of how the script is invoked.
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
: "${SEO_OPS_BASE_URL:=http://127.0.0.1:8787}"
: "${SEO_OPS_TIMEOUT:=60}"

# Parse flags. Each script accepts:
#   --base-url URL     override SEO_OPS_BASE_URL
#   --timeout SECONDS  override curl timeout
parse_flags() {
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
            --)
                shift
                break
                ;;
            -*)
                echo "Unknown flag: $1" >&2
                usage >&2
                exit 2
                ;;
            *)
                break
                ;;
        esac
    done
}

# Require exactly one positional argument (action_id).
require_action_id() {
    if [[ $# -lt 1 ]]; then
        echo "Usage: $0 [--base-url URL] [--timeout SECONDS] <action_id>" >&2
        echo "       Reads from stdin if the stage needs body input." >&2
        exit 2
    fi
    if [[ $# -gt 1 ]]; then
        echo "Too many arguments: $*" >&2
        exit 2
    fi
    if ! [[ "$1" =~ ^[0-9]+$ ]]; then
        echo "action_id must be a positive integer, got: $1" >&2
        exit 2
    fi
}

# HTTP wrapper. Sets HTTP_CODE on success.
# Usage: http_post_form <path> [<data-file>]
#        http_get <path>
http_post_form() {
    local path="$1"
    local data_file="${2:-}"

    local url="${SEO_OPS_BASE_URL}${path}"
    local curl_args=(
        --silent
        --show-error
        --max-time "${SEO_OPS_TIMEOUT}"
        --request POST
        --output /dev/stderr
        --write-out '%{http_code}'
    )
    curl_args+=(--write-out '\n%{http_code}')

    if [[ -n "${data_file}" ]]; then
        curl_args+=(--data-binary "@${data_file}")
    fi

    local response
    response=$(curl "${curl_args[@]}" "${url}" 2>&1) || {
        echo "curl failed for ${url}: $response" >&2
        return 1
    }

    # Last line is the HTTP code; everything before is the response body
    local code="${response##*$'\n'}"
    local body="${response%$'\n'*}"

    echo "${body}"
    [[ "${code}" =~ ^2 ]] || [[ "${code}" =~ ^3 ]] || {
        echo "HTTP ${code} from ${url}" >&2
        return 1
    }
    return 0
}

http_post_form_no_body() {
    local path="$1"
    local url="${SEO_OPS_BASE_URL}${path}"
    local response code body
    response=$(curl \
        --silent \
        --show-error \
        --max-time "${SEO_OPS_TIMEOUT}" \
        --request POST \
        --write-out '\n%{http_code}' \
        "${url}" 2>&1) || {
        echo "curl failed for ${url}: ${response}" >&2
        return 1
    }
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    echo "${body}"
    [[ "${code}" =~ ^2 ]] || [[ "${code}" =~ ^3 ]] || {
        echo "HTTP ${code} from ${url}" >&2
        return 1
    }
    return 0
}

http_get() {
    local path="$1"
    local url="${SEO_OPS_BASE_URL}${path}"
    local response code body
    response=$(curl \
        --silent \
        --show-error \
        --max-time "${SEO_OPS_TIMEOUT}" \
        --write-out '\n%{http_code}' \
        "${url}" 2>&1) || {
        echo "curl failed for ${url}: ${response}" >&2
        return 1
    }
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    echo "${body}"
    [[ "${code}" =~ ^2 ]] || {
        echo "HTTP ${code} from ${url}" >&2
        return 1
    }
    return 0
}

# Print a single-line summary of the HTTP exchange.
summarize() {
    local action_id="$1"
    local stage="$2"
    echo "[${stage}] action=${action_id} base_url=${SEO_OPS_BASE_URL} ok"
}