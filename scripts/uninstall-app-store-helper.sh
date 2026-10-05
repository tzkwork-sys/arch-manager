#!/usr/bin/env bash
set -euo pipefail

HELPER='/usr/local/libexec/arch-manager/manage-applications'
POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-applications.policy'
RULE='/etc/polkit-1/rules.d/00-arch-manager-applications.rules'

sudo rm -f -- "$HELPER" "$POLICY" "$RULE"
printf 'Removed Arch Manager App Store helper and Polkit policy.\n'
