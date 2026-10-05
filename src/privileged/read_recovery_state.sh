#!/usr/bin/env bash
set -euo pipefail

# Root-owned, read-only Recovery state probe. It never writes /boot, EFI vars,
# snapshots, the ISO, or Arch Manager state.
#
# Default mode performs the authoritative hash validation. --fingerprint is a
# deliberately lightweight change detector used by the GUI between page visits;
# it hashes only metadata/stat output, never the ISO/kernel/initramfs contents.
PATH=/usr/bin:/bin
LC_ALL=C
LANG=C
export PATH LC_ALL LANG

BOOT=/boot
SNAPSHOTS=/.snapshots
ISO=/data/Arch-Recovery/Arch-Manager-Recovery.iso
ISO_METADATA=/data/Arch-Recovery/Arch-Manager-Recovery.iso.metadata
LOCAL_DIR=/boot/arch-manager-local
ENTRY=/boot/loader/entries/arch-manager-recovery.conf
ENTRY_ID=arch-manager-recovery.conf
RESOURCE_DIR=/usr/local/share/arch-manager/recovery
PROFILE=$RESOURCE_DIR/archiso
RECOVERY_ENGINE=$RESOURCE_DIR/arch-manager-recovery

fail() {
    printf 'Arch Manager Recovery read helper: %s\n' "$*" >&2
    exit 64
}

MODE=full
if [[ $# -eq 1 && "$1" == "--fingerprint" ]]; then
    MODE=fingerprint
elif [[ $# -ne 0 ]]; then
    fail 'supported argument: --fingerprint'
fi
(( EUID == 0 )) || fail 'must run as root'

systemd_boot=0
iso_ready=0
boot_files_ready=0
environment_ready=0

profile_version() {
    /usr/bin/sed -n 's/^iso_version="\([^"]*\)"/\1/p' "$PROFILE/profiledef.sh" \
        | /usr/bin/head -n1
}

profile_hash() {
    [[ -d "$PROFILE" && -f "$RECOVERY_ENGINE" && ! -L "$RECOVERY_ENGINE" ]] || return 1
    {
        /usr/bin/find "$PROFILE" -type f -print0 \
            | /usr/bin/sort -z \
            | /usr/bin/xargs -0 /usr/bin/sha256sum
        /usr/bin/find "$PROFILE" -type l -printf 'L %p -> %l\n' \
            | /usr/bin/sort
        /usr/bin/sha256sum "$RECOVERY_ENGINE"
    } | /usr/bin/sha256sum | /usr/bin/awk '{print $1}'
}

metadata_value() {
    local key=$1
    /usr/bin/sed -n "s/^${key}=//p" "$ISO_METADATA" | /usr/bin/head -n1
}

entry_value() {
    local key=$1
    /usr/bin/sed -n "s/.*${key}=\([^ ]*\).*/\1/p" "$ENTRY" | /usr/bin/head -n1
}

root_source() {
    /usr/bin/findmnt -rn -o SOURCE --target / | /usr/bin/sed 's/\[.*$//'
}

iso_source() {
    /usr/bin/findmnt -rn -o SOURCE --target "$ISO" | /usr/bin/sed 's/\[.*$//'
}

iso_loop_path() {
    local mountpoint
    mountpoint=$(/usr/bin/findmnt -rn -o TARGET --target "$ISO") || return 1
    if [[ "$mountpoint" == / ]]; then
        printf '%s' "$ISO"
    else
        printf '/%s' "${ISO#"$mountpoint/"}"
    fi
}

list_broken_roots() {
    /usr/bin/btrfs subvolume list / 2>/dev/null \
        | /usr/bin/sed -n 's/.* path \(@\.broken-[0-9]\{8\}-[0-9]\{6\}\)$/\1/p' \
        | /usr/bin/sort -r
}

path_stamp() {
    local path=$1
    if [[ -L "$path" ]]; then
        printf 'link:%s' "$(/usr/bin/readlink -- "$path" 2>/dev/null || true)"
    elif [[ -e "$path" ]]; then
        /usr/bin/stat -Lc '%d:%i:%s:%y:%f' -- "$path" 2>/dev/null || printf 'unreadable'
    else
        printf 'missing'
    fi
}

quick_fingerprint() {
    {
        printf 'root_source='; root_source 2>/dev/null || true; printf '\n'
        printf 'root_options='; /usr/bin/findmnt -rn -o OPTIONS --target / 2>/dev/null || true; printf '\n'
        printf 'snapshots_source='; /usr/bin/findmnt -rn -o SOURCE --target "$SNAPSHOTS" 2>/dev/null || true; printf '\n'
        printf 'snapshots_options='; /usr/bin/findmnt -rn -o OPTIONS --target "$SNAPSHOTS" 2>/dev/null || true; printf '\n'
        printf 'systemd_boot=%s\n' "$systemd_boot"
        printf 'iso='; path_stamp "$ISO"; printf '\n'
        printf 'iso_metadata='; path_stamp "$ISO_METADATA"; printf '\n'
        printf 'kernel='; path_stamp "$LOCAL_DIR/vmlinuz-linux"; printf '\n'
        printf 'initramfs='; path_stamp "$LOCAL_DIR/initramfs-linux.img"; printf '\n'
        printf 'entry='; path_stamp "$ENTRY"; printf '\n'
        printf 'engine='; path_stamp "$RECOVERY_ENGINE"; printf '\n'
        if [[ -d "$PROFILE" ]]; then
            /usr/bin/find "$PROFILE" \( -type f -o -type l \) \
                -printf 'profile:%y:%P:%s:%T@:%l\n' 2>/dev/null \
                | /usr/bin/sort
        else
            printf 'profile:missing\n'
        fi
        while IFS= read -r broken_root; do
            [[ -n "$broken_root" ]] && printf 'broken_root=%s\n' "$broken_root"
        done < <(list_broken_roots)
    } | /usr/bin/sha256sum | /usr/bin/awk '{print $1}'
}

if [[ -d "$BOOT" ]] \
    && /usr/bin/bootctl --esp-path="$BOOT" is-installed >/dev/null 2>&1; then
    systemd_boot=1
fi

if [[ "$MODE" == fingerprint ]]; then
    printf 'version=2\n'
    printf 'fingerprint=%s\n' "$(quick_fingerprint)"
    exit 0
fi

current_profile_hash=$(profile_hash 2>/dev/null || true)
current_profile_version=$(profile_version 2>/dev/null || true)
current_iso_hash=""

if [[ -f "$ISO" && ! -L "$ISO" && -s "$ISO" \
    && -f "$ISO_METADATA" && ! -L "$ISO_METADATA" \
    && "$current_profile_hash" =~ ^[0-9a-f]{64}$ \
    && -n "$current_profile_version" ]]; then

    current_iso_hash=$(/usr/bin/sha256sum "$ISO" 2>/dev/null | /usr/bin/awk '{print $1}' || true)
    if [[ "$current_iso_hash" =~ ^[0-9a-f]{64}$ ]] \
        && [[ "$(metadata_value profile_version)" == "$current_profile_version" ]] \
        && [[ "$(metadata_value profile_sha256)" == "$current_profile_hash" ]] \
        && [[ "$(metadata_value iso_sha256)" == "$current_iso_hash" ]]; then
        iso_ready=1
    fi
fi

if (( systemd_boot == 1 && iso_ready == 1 )) \
    && [[ -s "$LOCAL_DIR/vmlinuz-linux" && ! -L "$LOCAL_DIR/vmlinuz-linux" ]] \
    && [[ -s "$LOCAL_DIR/initramfs-linux.img" && ! -L "$LOCAL_DIR/initramfs-linux.img" ]] \
    && [[ -r "$ENTRY" && ! -L "$ENTRY" ]]; then

    linux_ok=0
    initrd_ok=0
    marker_ok=0
    snapshot_token=0

    while IFS= read -r line; do
        case "$line" in
            'linux   /arch-manager-local/vmlinuz-linux') linux_ok=1 ;;
            'initrd  /arch-manager-local/initramfs-linux.img') initrd_ok=1 ;;
            options\ *)
                read -r -a tokens <<< "${line#options }"
                for token in "${tokens[@]}"; do
                    case "$token" in
                        arch_manager_local=1) marker_ok=1 ;;
                        arch_manager_snapshot=*) snapshot_token=1 ;;
                    esac
                done
                ;;
        esac
    done < "$ENTRY"

    root_dev=$(root_source 2>/dev/null || true)
    iso_dev=$(iso_source 2>/dev/null || true)
    root_uuid=""
    iso_uuid=""
    loop_path=""
    if [[ -n "$root_dev" ]]; then
        root_uuid=$(/usr/bin/blkid -s UUID -o value "$root_dev" 2>/dev/null || true)
    fi
    if [[ -n "$iso_dev" ]]; then
        iso_uuid=$(/usr/bin/blkid -s UUID -o value "$iso_dev" 2>/dev/null || true)
    fi
    loop_path=$(iso_loop_path 2>/dev/null || true)

    kernel_sum=$(
        /usr/bin/sha256sum "$LOCAL_DIR/vmlinuz-linux" 2>/dev/null \
            | /usr/bin/awk '{print $1}' || true
    )
    initramfs_sum=$(
        /usr/bin/sha256sum "$LOCAL_DIR/initramfs-linux.img" 2>/dev/null \
            | /usr/bin/awk '{print $1}' || true
    )

    if (( linux_ok == 1 && initrd_ok == 1 && marker_ok == 1 && snapshot_token == 0 )) \
        && [[ -n "$root_uuid" && -n "$iso_uuid" && -n "$loop_path" ]] \
        && [[ "$(entry_value arch_manager_root_uuid)" == "$root_uuid" ]] \
        && [[ "$(entry_value img_dev)" == "/dev/disk/by-uuid/$iso_uuid" ]] \
        && [[ "$(entry_value img_loop)" == "$loop_path" ]] \
        && [[ "$(entry_value arch_manager_profile_sha256)" == "$current_profile_hash" ]] \
        && [[ "$(entry_value arch_manager_iso_sha256)" == "$current_iso_hash" ]] \
        && [[ "$(entry_value arch_manager_kernel_sha256)" == "$kernel_sum" ]] \
        && [[ "$(entry_value arch_manager_initramfs_sha256)" == "$initramfs_sum" ]] \
        && /usr/bin/bootctl --esp-path="$BOOT" --no-pager list 2>/dev/null \
            | /usr/bin/grep -qF "$ENTRY_ID"; then
        boot_files_ready=1
    fi
fi

if (( systemd_boot == 1 && iso_ready == 1 && boot_files_ready == 1 )); then
    environment_ready=1
fi

printf 'version=2\n'
printf 'systemd_boot=%s\n' "$systemd_boot"
printf 'iso_ready=%s\n' "$iso_ready"
printf 'boot_files_ready=%s\n' "$boot_files_ready"
printf 'environment_ready=%s\n' "$environment_ready"
printf 'fingerprint=%s\n' "$(quick_fingerprint)"
while IFS= read -r broken_root; do
    [[ -n "$broken_root" ]] && printf 'broken_root=%s\n' "$broken_root"
done < <(list_broken_roots)
