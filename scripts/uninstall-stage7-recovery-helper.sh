#!/usr/bin/env bash
set -euo pipefail
/usr/bin/sudo -v
/usr/bin/sudo /usr/bin/rm -f -- /etc/polkit-1/rules.d/00-arch-manager-recovery.rules /usr/share/polkit-1/actions/org.archmanager.manage-recovery.policy /usr/local/libexec/arch-manager/manage-recovery /usr/local/libexec/arch-manager/recovery_lib /usr/local/libexec/arch-manager/arch-recovery.sh /usr/lib/arch-manager/read-recovery-state
/usr/bin/sudo /usr/bin/rm -f -- /etc/sudoers.d/arch-manager-read-recovery-*
/usr/bin/sudo /usr/bin/rm -rf -- /usr/local/share/arch-manager/recovery
/usr/bin/sudo /usr/bin/rmdir --ignore-fail-on-non-empty /usr/local/libexec/arch-manager 2>/dev/null || true
/usr/bin/sudo /usr/bin/rmdir --ignore-fail-on-non-empty /usr/lib/arch-manager 2>/dev/null || true
printf 'Arch Manager Recovery helper integration removed.\n'
