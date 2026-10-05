#!/usr/bin/env bash
set -euo pipefail

HELPER='/usr/local/libexec/arch-manager/manage-restore-points'
POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'
LEGACY_POLICY='/usr/local/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'
RULE='/etc/polkit-1/rules.d/00-arch-manager-restore-points.rules'

[[ -x /usr/bin/sudo ]] || { echo 'sudo is required' >&2; exit 2; }

/usr/bin/sudo -v
/usr/bin/sudo /usr/bin/rm -f -- "$RULE" "$POLICY" "$LEGACY_POLICY" "$HELPER"
/usr/bin/sudo /usr/bin/rmdir --ignore-fail-on-non-empty /usr/local/libexec/arch-manager 2>/dev/null || true

printf 'Arch Manager Stage 4 privileged helper integration removed.\n'
