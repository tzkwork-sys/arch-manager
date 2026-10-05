#!/usr/bin/env bash
set -euo pipefail

ln -sf /usr/share/zoneinfo/Europe/Moscow /etc/localtime
if command -v systemd-machine-id-setup >/dev/null 2>&1; then
    systemd-machine-id-setup >/dev/null 2>&1 || true
fi
for unit in systemd-firstboot.service firstboot-ask-password-console.service firstboot-ask-password-wall.service systemd-vconsole-setup.service getty@tty1.service console-getty.service; do
    systemctl mask "$unit" >/dev/null 2>&1 || true
done
systemctl enable arch-manager-recovery-watchdog.service
systemctl enable arch-manager-recovery.service
