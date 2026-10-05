#!/usr/bin/env bash
set -euo pipefail
/usr/bin/sudo /usr/bin/rm -f -- \
  /usr/local/libexec/arch-manager/read-system-diagnostics \
  /usr/share/polkit-1/actions/org.archmanager.read-system-diagnostics.policy \
  /etc/polkit-1/rules.d/00-arch-manager-system-diagnostics.rules
printf 'Arch Manager diagnostics helper removed.\n'
