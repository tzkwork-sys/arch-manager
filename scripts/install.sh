#!/usr/bin/env bash
# User-level release installation; system helpers are explicitly optional.
set -euo pipefail
[[ $EUID -ne 0 ]] || { printf 'Run this installer as your desktop user, not root.\n' >&2; exit 2; }
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python -c 'import PySide6' || { printf 'Install dependencies as described in docs/INSTALL.md first.\n' >&2; exit 2; }
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/arch-manager/versions"
mkdir -p -- "$BASE"
DEST=$(mktemp -d "$BASE/release.XXXXXX")
# Copy only the application distribution, not Git metadata, caches or backups.
for entry in main.py src scripts recovery packaging desktop requirements.txt README.md LICENSE COPYING docs; do
    cp -a -- "$PROJECT_DIR/$entry" "$DEST/"
done
bash "$DEST/scripts/install-stage1.sh"
printf 'Application installed at: %s\nOld installations are retained for rollback.\n' "$DEST"
printf 'Optional system components: see %s/docs/INSTALL.md\n' "$DEST"
