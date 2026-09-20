#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-phase0-common.sh
source "$SCRIPT_DIR/aws-phase0-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Uses the setup script environment except for its apply confirmation.' \
    'The setup script must already have completed successfully.'
  exit 0
fi
[[ $# -eq 0 ]] || phase0_die "no arguments are accepted; use --help"

phase0_require_command aws
phase0_require_command python3
phase0_require_command timeout
phase0_require_command uv
phase0_require_environment

umask 077
mkdir -p "$PHASE0_ARTIFACT_DIR"
phase0_capture_identity "$PHASE0_ARTIFACT_DIR/test-identity.json"
phase0_load_runtime

timeout "$PHASE0_TIMEOUT_SECONDS" uv run pytest \
  -m aws tests/aws --strict-markers --maxfail=1
