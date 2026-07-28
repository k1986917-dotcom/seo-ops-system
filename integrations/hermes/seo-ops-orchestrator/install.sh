#!/usr/bin/env bash
# Install seo-ops-orchestrator into ~/.hermes/skills/software-development/.
#
# Usage:
#   ./install.sh              refuse to overwrite existing install
#   ./install.sh --force      overwrite existing install
#   ./install.sh --uninstall  remove the installed skill
#
# This script only touches ~/.hermes/skills/software-development/seo-ops-orchestrator/.
# It never reads or writes ~/.hermes/.env, ~/.hermes/auth.json, or the
# SEO Ops repository. Run from anywhere; the script resolves its own location.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.hermes/skills/software-development/seo-ops-orchestrator"

usage() {
    cat <<'EOF'
install.sh — install / remove the seo-ops-orchestrator Hermes skill

Usage:
    install.sh              install (refuse to overwrite existing)
    install.sh --force      overwrite existing install
    install.sh --uninstall  remove the installed skill

Files copied on install:
    SKILL.md            → ~/.hermes/skills/software-development/seo-ops-orchestrator/SKILL.md
    README.md           → ~/.hermes/skills/software-development/seo-ops-orchestrator/README.md
    scripts/*.sh        → ~/.hermes/skills/software-development/seo-ops-orchestrator/scripts/

Does NOT touch:
    ~/.hermes/.env          (Hermes's API keys)
    ~/.hermes/auth.json     (Hermes's credential pool)
    ~/.hermes/SOUL.md       (Hermes's system prompt)
    /home/laoma/seo-ops-system/  (the SEO Ops repo)
EOF
}

FORCE=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE=1
            ;;
        --uninstall|-u)
            UNINSTALL=1
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "${UNINSTALL}" == "1" ]]; then
    if [[ -d "${TARGET_DIR}" ]]; then
        rm -rf "${TARGET_DIR}"
        echo "Removed ${TARGET_DIR}"
        echo "Run 'hermes skills list' to confirm."
    else
        echo "Not installed: ${TARGET_DIR} does not exist"
    fi
    exit 0
fi

if [[ -d "${TARGET_DIR}" && "${FORCE}" != "1" ]]; then
    echo "Already installed at ${TARGET_DIR}" >&2
    echo "Use --force to overwrite, or --uninstall to remove first." >&2
    exit 1
fi

mkdir -p "${TARGET_DIR}/scripts"

# Copy SKILL.md, README.md, and scripts/.
for f in SKILL.md README.md; do
    if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
        cp "${SCRIPT_DIR}/${f}" "${TARGET_DIR}/${f}"
    fi
done

for f in "${SCRIPT_DIR}/scripts/"*.sh; do
    bn=$(basename "${f}")
    cp "${f}" "${TARGET_DIR}/scripts/${bn}"
    chmod +x "${TARGET_DIR}/scripts/${bn}"
done

# Ensure scripts/ is executable in source too, in case the user copies it later.
chmod +x "${SCRIPT_DIR}/scripts/"*.sh 2>/dev/null || true

echo "Installed to ${TARGET_DIR}"
echo ""
echo "Next steps:"
echo "  1. Run 'hermes skills list' to confirm the skill is discovered"
echo "     (you should see 'seo-ops-orchestrator' in the software-development category)"
echo "  2. From Hermes, ask 'use seo-ops-orchestrator to run R0 on action 99'"
echo "  3. Provide search results via stdin:  scripts/r1.sh 99 < search-results.md"
echo "  4. Verify end-to-end: pytest tests/integration/test_hermes_orchestrator_smoke.py"
echo ""
echo "Configuration (optional):"
echo "  export SEO_OPS_BASE_URL=http://127.0.0.1:8787   # default"