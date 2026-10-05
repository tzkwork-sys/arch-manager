# Arch Manager Recovery privileged helper module.
# Sourced only by manage_recovery.sh from a fixed local directory.

validate_entry() {
    local root_uuid=$1 iso_uuid=$2 loop_path=$3 expected_profile=$4 expected_iso=$5
    local kernel_sum initramfs_sum

    [[ -r "$ENTRY" && ! -L "$ENTRY" ]] || fail stale_preparation
    /usr/bin/grep -qx 'linux   /arch-manager-local/vmlinuz-linux' "$ENTRY" || fail boot_entry_invalid
    /usr/bin/grep -qx 'initrd  /arch-manager-local/initramfs-linux.img' "$ENTRY" || fail boot_entry_invalid
    /usr/bin/grep -Eq '(^|[[:space:]])arch_manager_local=1([[:space:]]|$)' "$ENTRY" || fail boot_entry_invalid

    # A prepared environment is generic. Snapshot preselection remains supported
    # by the Recovery engine, but the persistent normal-GUI entry never carries it.
    if /usr/bin/grep -Eq '(^|[[:space:]])arch_manager_snapshot=' "$ENTRY"; then
        fail stale_preparation
    fi

    [[ "$(entry_value arch_manager_root_uuid)" == "$root_uuid" ]] || fail stale_preparation
    [[ "$(entry_value img_dev)" == "/dev/disk/by-uuid/$iso_uuid" ]] || fail stale_preparation
    [[ "$(entry_value img_loop)" == "$loop_path" ]] || fail stale_preparation
    [[ "$(entry_value arch_manager_profile_sha256)" == "$expected_profile" ]] || fail stale_preparation
    [[ "$(entry_value arch_manager_iso_sha256)" == "$expected_iso" ]] || fail stale_preparation

    [[ -s "$LOCAL_DIR/vmlinuz-linux" && ! -L "$LOCAL_DIR/vmlinuz-linux" ]] || fail stale_preparation
    [[ -s "$LOCAL_DIR/initramfs-linux.img" && ! -L "$LOCAL_DIR/initramfs-linux.img" ]] || fail stale_preparation

    kernel_sum=$(/usr/bin/sha256sum "$LOCAL_DIR/vmlinuz-linux" | /usr/bin/awk '{print $1}')
    initramfs_sum=$(/usr/bin/sha256sum "$LOCAL_DIR/initramfs-linux.img" | /usr/bin/awk '{print $1}')
    [[ "$(entry_value arch_manager_kernel_sha256)" == "$kernel_sum" ]] || fail stale_preparation
    [[ "$(entry_value arch_manager_initramfs_sha256)" == "$initramfs_sum" ]] || fail stale_preparation

    /usr/bin/bootctl --esp-path="$BOOT" --no-pager list 2>/dev/null \
        | /usr/bin/grep -qF "$ENTRY_ID" || fail boot_entry_invalid
}
remove_environment_files() {
    /usr/bin/rm -f -- "$ENTRY"
    /usr/bin/rm -rf -- "$LOCAL_DIR"
}
cancel() {
    [[ $# -eq 0 ]] || fail boot_entry_invalid
    require_root
    /usr/bin/bootctl --esp-path="$BOOT" set-oneshot '' >/dev/null 2>&1 || true
    remove_environment_files
    printf 'OK\tcancelled\n'
}
prepare() {
    [[ $# -eq 0 ]] || fail boot_entry_invalid
    require_root
    check_layout
    check_boot
    build_iso_if_stale
    cache_valid || fail iso_missing
    inspect_iso

    local root_dev root_uuid iso_dev iso_uuid loop_path profile_sum iso_sum
    local kernel_tmp initramfs_tmp stage need available kernel_sum initramfs_sum

    root_dev=$(root_source) || fail invalid_btrfs_layout
    root_uuid=$(/usr/bin/blkid -s UUID -o value "$root_dev") || fail invalid_btrfs_layout
    iso_dev=$(iso_source) || fail iso_filesystem_invalid
    iso_uuid=$(/usr/bin/blkid -s UUID -o value "$iso_dev") || fail iso_filesystem_invalid
    loop_path=$(iso_loop_path)
    profile_sum=$(profile_hash) || fail iso_missing
    iso_sum=$(iso_hash) || fail iso_missing

    kernel_tmp=$(/usr/bin/mktemp) || fail boot_write_failed
    initramfs_tmp=$(/usr/bin/mktemp) || {
        /usr/bin/rm -f -- "$kernel_tmp"
        fail boot_write_failed
    }
    stage=$(/usr/bin/mktemp -d "$BOOT/.arch-manager-recovery.XXXXXX") || {
        /usr/bin/rm -f -- "$kernel_tmp" "$initramfs_tmp"
        fail insufficient_boot_space
    }
    trap '/usr/bin/rm -f -- "$kernel_tmp" "$initramfs_tmp"; /usr/bin/rm -rf -- "$stage"' EXIT

    /usr/bin/bsdtar -xOf "$ISO" "$ISO_KERNEL" > "$kernel_tmp" || fail iso_missing
    /usr/bin/bsdtar -xOf "$ISO" "$ISO_INITRAMFS" > "$initramfs_tmp" || fail iso_missing
    kernel_sum=$(/usr/bin/sha256sum "$kernel_tmp" | /usr/bin/awk '{print $1}')
    initramfs_sum=$(/usr/bin/sha256sum "$initramfs_tmp" | /usr/bin/awk '{print $1}')

    need=$((
        $(/usr/bin/stat -c %s "$kernel_tmp")
        + $(/usr/bin/stat -c %s "$initramfs_tmp")
        + 16777216
    ))
    available=$(
        /usr/bin/df -B1 --output=avail "$BOOT" \
            | /usr/bin/tail -n1 \
            | /usr/bin/tr -d ' '
    )
    [[ "$available" =~ ^[0-9]+$ && "$available" -ge "$need" ]] || fail insufficient_boot_space

    /usr/bin/install -d -o root -g root -m 0755 "$stage/local" "$ENTRY_DIR" || fail boot_write_failed
    /usr/bin/install -o root -g root -m 0644 "$kernel_tmp" "$stage/local/vmlinuz-linux" || fail boot_write_failed
    /usr/bin/install -o root -g root -m 0644 "$initramfs_tmp" "$stage/local/initramfs-linux.img" || fail boot_write_failed

    /usr/bin/printf '%s\n' \
        'title   Arch Manager Recovery' \
        'linux   /arch-manager-local/vmlinuz-linux' \
        'initrd  /arch-manager-local/initramfs-linux.img' \
        "options archisobasedir=arch img_dev=/dev/disk/by-uuid/$iso_uuid img_loop=$loop_path earlymodules=loop quiet loglevel=3 rd.systemd.show_status=false systemd.show_status=false rd.udev.log_level=3 udev.log_level=3 arch_manager_local=1 arch_manager_root_uuid=$root_uuid arch_manager_profile_sha256=$profile_sum arch_manager_iso_sha256=$iso_sum arch_manager_kernel_sha256=$kernel_sum arch_manager_initramfs_sha256=$initramfs_sum" \
        > "$stage/entry" || fail boot_write_failed

    # Do not touch one-shot state while merely preparing/updating the shared
    # environment. Only the explicit reboot action sets a one-shot entry.
    remove_environment_files
    /usr/bin/mv "$stage/local" "$LOCAL_DIR" || fail boot_write_failed
    /usr/bin/mv "$stage/entry" "$ENTRY" || {
        remove_environment_files
        fail boot_write_failed
    }

    validate_entry "$root_uuid" "$iso_uuid" "$loop_path" "$profile_sum" "$iso_sum"
    /usr/bin/sync

    trap - EXIT
    /usr/bin/rm -f -- "$kernel_tmp" "$initramfs_tmp"
    /usr/bin/rm -rf -- "$stage"
    printf 'OK\tprepared\n'
}
reboot_recovery() {
    [[ $# -eq 0 ]] || fail boot_entry_invalid
    require_root
    check_layout
    check_boot

    # Reboot never rebuilds silently. If anything became stale after the GUI
    # check, fail closed and let the GUI run preparation again.
    cache_valid || fail stale_preparation
    inspect_iso

    local root_dev root_uuid iso_dev iso_uuid loop_path profile_sum iso_sum
    root_dev=$(root_source) || fail stale_preparation
    root_uuid=$(/usr/bin/blkid -s UUID -o value "$root_dev") || fail stale_preparation
    iso_dev=$(iso_source) || fail stale_preparation
    iso_uuid=$(/usr/bin/blkid -s UUID -o value "$iso_dev") || fail stale_preparation
    loop_path=$(iso_loop_path)
    profile_sum=$(profile_hash) || fail stale_preparation
    iso_sum=$(iso_hash) || fail stale_preparation

    validate_entry "$root_uuid" "$iso_uuid" "$loop_path" "$profile_sum" "$iso_sum"

    /usr/bin/bootctl --esp-path="$BOOT" set-oneshot "$ENTRY_ID" || fail boot_not_ready
    /usr/bin/systemctl reboot || {
        /usr/bin/bootctl --esp-path="$BOOT" set-oneshot '' >/dev/null 2>&1 || true
        fail boot_not_ready
    }
    printf 'OK\trebooting\n'
}
delete_broken_roots() {
    [[ $# -ge 1 && $# -le 20 ]] || fail broken_root_invalid
    require_root
    check_layout

    local root_dev top_mnt name path top_id nested deleted_csv=""
    local -a names=("$@")

    for name in "${names[@]}"; do
        [[ "$name" =~ ^@\.broken-[0-9]{8}-[0-9]{6}$ ]] || fail broken_root_invalid
    done

    root_dev=$(root_source) || fail invalid_btrfs_layout
    [[ -b "$root_dev" ]] || fail invalid_btrfs_layout
    top_mnt=$(/usr/bin/mktemp -d /run/arch-manager-broken.XXXXXX) || fail broken_root_delete_failed
    trap '/usr/bin/umount -- "$top_mnt" >/dev/null 2>&1 || true; /usr/bin/rmdir -- "$top_mnt" >/dev/null 2>&1 || true' EXIT

    /usr/bin/mount -o subvolid=5 "$root_dev" "$top_mnt" || fail broken_root_delete_failed

    # Validate every requested name before deleting anything. We intentionally
    # refuse recursive deletion: an unexpected nested subvolume requires manual
    # investigation instead of broadening this privileged boundary.
    for name in "${names[@]}"; do
        path="$top_mnt/$name"
        [[ -d "$path" && ! -L "$path" ]] || fail broken_root_missing
        /usr/bin/btrfs subvolume show "$path" >/dev/null 2>&1 || fail broken_root_invalid
        top_id=$(/usr/bin/btrfs subvolume show "$path" 2>/dev/null \
            | /usr/bin/awk '/Top level ID:/ {print $4; exit}')
        [[ "$top_id" == 5 ]] || fail broken_root_invalid
        nested=$(/usr/bin/btrfs subvolume list -o "$path" 2>/dev/null || true)
        [[ -z "$nested" ]] || fail broken_root_nested
    done

    for name in "${names[@]}"; do
        /usr/bin/btrfs subvolume delete "$top_mnt/$name" >/dev/null \
            || fail broken_root_delete_failed
        if [[ -n "$deleted_csv" ]]; then
            deleted_csv+=","$name
        else
            deleted_csv=$name
        fi
    done
    /usr/bin/sync
    /usr/bin/umount -- "$top_mnt" || fail broken_root_delete_failed
    /usr/bin/rmdir -- "$top_mnt" || true
    trap - EXIT
    printf 'OK\tdeleted-broken\t%s\n' "$deleted_csv"
}
self_test() {
    [[ $# -eq 0 ]] || fail boot_entry_invalid
    printf 'OK\n'
}
