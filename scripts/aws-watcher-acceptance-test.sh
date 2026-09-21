#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-watcher-common.sh
source "$SCRIPT_DIR/aws-watcher-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Uses the setup script environment except for its apply confirmation.' \
    'Requires MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM=ACCEPT-DISPOSABLE-RESOURCES:218375352108.' \
    'The setup script must already have completed successfully.'
  exit 0
fi
[[ $# -eq 0 ]] || watcher_die "no arguments are accepted; use --help"

watcher_require_command aws
watcher_require_command python3
watcher_require_command timeout
watcher_require_command uv
watcher_require_environment

umask 077
mkdir -p "$WATCHER_ARTIFACT_DIR"
watcher_capture_identity "$WATCHER_ARTIFACT_DIR/test-identity.json"
watcher_load_runtime

timeout "$WATCHER_TIMEOUT_SECONDS" uv run pytest \
  -m aws tests/aws --strict-markers --maxfail=1
