#!/usr/bin/env bash
# Compatibility entry point. Active Recovery engine lives in recovery/engine/.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$SCRIPT_DIR/../../recovery/engine/arch-recovery.sh" "$@"
