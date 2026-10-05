#!/usr/bin/env bash
set -euo pipefail

HELPER='/usr/local/libexec/arch-manager/manage-maintenance'
POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-maintenance.policy'
RULE='/etc/polkit-1/rules.d/00-arch-manager-maintenance.rules'

/usr/bin/sudo /usr/bin/rm -f -- "$HELPER" "$POLICY" "$RULE"
printf 'Arch Manager Stage 6 maintenance integration removed.\n'
