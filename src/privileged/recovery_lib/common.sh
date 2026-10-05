# Arch Manager Recovery privileged helper module.
# Sourced only by manage_recovery.sh from a fixed local directory.

fail() {
    printf 'ERROR\t%s\n' "$1"
    exit 2
}
require_root() {
    [[ ${EUID:-$(/usr/bin/id -u)} -eq 0 ]] || fail helper_unavailable
}

acquire_recovery_lock() {
    require_root
    /usr/bin/install -d -o root -g root -m 0755 /run/arch-manager || fail helper_unavailable
    exec 9>/run/arch-manager/recovery-operation.lock || fail helper_unavailable
    /usr/bin/flock -n 9 || fail recovery_busy
}
subvol() {
    local options=$1 source=$2 item
    IFS=, read -ra items <<< "$options"
    for item in "${items[@]}"; do
        if [[ "$item" == subvol=* ]]; then
            printf '%s' "${item#subvol=}"
            return
        fi
    done
    if [[ "$source" =~ \[(/[^]]+)\]$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}
root_source() {
    /usr/bin/findmnt -rn -o SOURCE --target / | /usr/bin/sed 's/\[.*$//'
}
iso_source() {
    /usr/bin/findmnt -rn -o SOURCE --target "$ISO" | /usr/bin/sed 's/\[.*$//'
}
check_layout() {
    local fs options source

    fs=$(/usr/bin/findmnt -rn -o FSTYPE --target /) || fail invalid_btrfs_layout
    options=$(/usr/bin/findmnt -rn -o OPTIONS --target /) || fail invalid_btrfs_layout
    source=$(/usr/bin/findmnt -rn -o SOURCE --target /) || fail invalid_btrfs_layout
    [[ "$fs" == btrfs && "$(subvol "$options" "$source")" == /@ ]] || fail invalid_btrfs_layout

    [[ -d /.snapshots ]] || fail invalid_btrfs_layout
    fs=$(/usr/bin/findmnt -rn -o FSTYPE --target /.snapshots) || fail invalid_btrfs_layout
    options=$(/usr/bin/findmnt -rn -o OPTIONS --target /.snapshots) || fail invalid_btrfs_layout
    source=$(/usr/bin/findmnt -rn -o SOURCE --target /.snapshots) || fail invalid_btrfs_layout
    [[ "$fs" == btrfs && "$(subvol "$options" "$source")" == /@snapshots ]] || fail invalid_btrfs_layout
}
check_boot() {
    [[ -d "$BOOT" ]] || fail boot_not_ready
    /usr/bin/bootctl --esp-path="$BOOT" is-installed >/dev/null 2>&1 || fail boot_not_ready
}
