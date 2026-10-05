#!/usr/bin/env bash
# Fixed root boundary for one shared Arch Manager Recovery environment.
set -euo pipefail

PATH=/usr/bin:/bin
LC_ALL=C
LANG=C
export PATH LC_ALL LANG

BOOT=/boot
ISO=/data/Arch-Recovery/Arch-Manager-Recovery.iso
ISO_METADATA=/data/Arch-Recovery/Arch-Manager-Recovery.iso.metadata
LOCAL_DIR=/boot/arch-manager-local
ENTRY_DIR=/boot/loader/entries
ENTRY=$ENTRY_DIR/arch-manager-recovery.conf
ENTRY_ID=arch-manager-recovery.conf
RESOURCE_DIR=/usr/local/share/arch-manager/recovery
PROFILE=$RESOURCE_DIR/archiso
RECOVERY_ENGINE=$RESOURCE_DIR/arch-manager-recovery
PROFILE_PACKAGES=$PROFILE/packages.x86_64
BUILD_LOG=/var/log/arch-manager/recovery-build.log
USB_BOOT_LOG=/var/log/arch-manager/recovery-usb-boot.log
USB_PROGRESS_FILE=/run/arch-manager/recovery-usb-progress
USB_BOOT_LABEL="Arch Manager Recovery USB"
ESP_PARTTYPE="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RECOVERY_LIB_DIR="$SCRIPT_DIR/recovery_lib"

load_recovery_module() {
    local module=$1 path="$RECOVERY_LIB_DIR/$1"
    [[ -f "$path" && ! -L "$path" ]] || {
        printf 'ERROR\thelper_unavailable\n'
        exit 2
    }
    # shellcheck source=/dev/null
    source "$path"
}

load_recovery_module common.sh
load_recovery_module iso.sh
load_recovery_module usb.sh
load_recovery_module local.sh

action=${1:-}
shift || true
case "$action" in
    --self-test) self_test "$@" ;;
    prepare|reboot|cancel|delete-broken|usb-create|usb-reboot)
        acquire_recovery_lock
        case "$action" in
            prepare) prepare "$@" ;;
            reboot) reboot_recovery "$@" ;;
            cancel) cancel "$@" ;;
            delete-broken) delete_broken_roots "$@" ;;
            usb-create) create_recovery_usb "$@" ;;
            usb-reboot) reboot_recovery_usb "$@" ;;
        esac
        ;;
    *) fail boot_entry_invalid ;;
esac
