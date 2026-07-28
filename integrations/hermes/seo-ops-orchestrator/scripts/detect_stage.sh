#!/usr/bin/env bash
# Verify SEO Ops is reachable and report service version.
#
# This skill does not maintain its own state. The current stage of each
# action lives in SEO Ops's database and is shown on the /actions page.
# This script is a connectivity probe, useful before running other scripts.
#
# Usage: detect_stage.sh [--base-url URL]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
detect_stage.sh — verify SEO Ops is reachable

Usage:
    detect_stage.sh [--base-url URL] [--timeout SECONDS]

Outputs JSON like {"status":"ok","version":"0.10.9",...} on success.
Use /actions in a browser (or curl + jq) to inspect per-action stage.

Why this script doesn't return "the current stage":
    The 7 stage buttons (r0, r1, r3, w0, w1b, w2, w3) are operator-driven.
    Hermes can read the page, but should not "auto-detect" a stage and try
    to advance it without operator intent. The skill's design principle is:
    the operator decides, this skill executes.
EOF
}

parse_flags "$@"

http_get "/api/health"