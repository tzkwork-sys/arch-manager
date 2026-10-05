# Arch Manager Recovery privileged helper module.
# Sourced only by manage_recovery.sh from a fixed local directory.

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
        # Symlinks are part of an Archiso profile too (notably enabled systemd
        # units). Their target must participate in the cache fingerprint.
        /usr/bin/find "$PROFILE" -type l -printf 'L %p -> %l\n' \
            | /usr/bin/sort
        /usr/bin/sha256sum "$RECOVERY_ENGINE"
    } | /usr/bin/sha256sum | /usr/bin/awk '{print $1}'
}
iso_hash() {
    /usr/bin/sha256sum "$ISO" | /usr/bin/awk '{print $1}'
}
metadata_value() {
    local key=$1
    /usr/bin/sed -n "s/^${key}=//p" "$ISO_METADATA" | /usr/bin/head -n1
}
profile_has_package() {
    local package=$1
    [[ -f "$PROFILE_PACKAGES" && ! -L "$PROFILE_PACKAGES" ]] || return 1
    /usr/bin/grep -qx -- "$package" "$PROFILE_PACKAGES"
}
validate_profile() {
    [[ -f "$PROFILE/profiledef.sh" && ! -L "$PROFILE/profiledef.sh" ]] \
        || fail recovery_profile_invalid
    profile_has_package mkinitcpio || fail recovery_profile_invalid
    profile_has_package mkinitcpio-archiso || fail recovery_profile_invalid
    if /usr/bin/grep -q "bios.syslinux" "$PROFILE/profiledef.sh"; then
        profile_has_package syslinux || fail recovery_profile_invalid
    fi
}
cache_valid() {
    local version profile_sum iso_sum
    [[ -f "$ISO" && ! -L "$ISO" && -s "$ISO" ]] || return 1
    [[ -f "$ISO_METADATA" && ! -L "$ISO_METADATA" ]] || return 1

    version=$(profile_version) || return 1
    profile_sum=$(profile_hash) || return 1
    iso_sum=$(iso_hash) || return 1

    [[ "$(metadata_value profile_version)" == "$version" ]] \
        && [[ "$(metadata_value profile_sha256)" == "$profile_sum" ]] \
        && [[ "$(metadata_value iso_sha256)" == "$iso_sum" ]]
}
build_iso_if_stale() {
    cache_valid && return

    [[ -x /usr/bin/mkarchiso ]] || fail mkarchiso_missing
    [[ -d "$PROFILE" && -x "$RECOVERY_ENGINE" && ! -L "$RECOVERY_ENGINE" ]] || fail iso_missing
    validate_profile

    local temp built metadata iso_stage version profile_sum iso_sum
    temp=$(/usr/bin/mktemp -d /var/tmp/arch-manager-recovery.XXXXXX) || fail iso_missing
    metadata=""

    if ! /usr/bin/cp -a "$PROFILE" "$temp/profile"; then
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi
    if ! /usr/bin/install -D -o root -g root -m 0755 \
        "$RECOVERY_ENGINE" \
        "$temp/profile/airootfs/usr/local/bin/arch-manager-recovery"; then
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi
    /usr/bin/install -d -o root -g root -m 0755 /var/log/arch-manager || {
        /usr/bin/rm -rf -- "$temp"
        fail iso_build_failed
    }
    if ! /usr/bin/mkarchiso -w "$temp/work" -o "$temp/out" "$temp/profile" >"$BUILD_LOG" 2>&1; then
        /usr/bin/rm -rf -- "$temp"
        fail iso_build_failed
    fi

    built=$(/usr/bin/find "$temp/out" -maxdepth 1 -type f -name '*.iso' -print -quit)
    if [[ -z "$built" || ! -s "$built" ]]; then
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi

    /usr/bin/install -d -o root -g root -m 0755 /data/Arch-Recovery || {
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    }
    iso_stage=$(/usr/bin/mktemp "/data/Arch-Recovery/.Arch-Manager-Recovery.iso.XXXXXX") || {
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    }
    if ! /usr/bin/install -o root -g root -m 0644 "$built" "$iso_stage"; then
        /usr/bin/rm -f -- "$iso_stage"
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi
    # Rename on the same filesystem is atomic: an interrupted copy can never
    # leave a truncated ISO at the trusted final path.
    if ! /usr/bin/mv -f -- "$iso_stage" "$ISO"; then
        /usr/bin/rm -f -- "$iso_stage"
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi

    version=$(profile_version)
    profile_sum=$(profile_hash)
    iso_sum=$(iso_hash)
    metadata=$(/usr/bin/mktemp "${ISO_METADATA}.XXXXXX") || {
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    }

    if ! /usr/bin/printf \
        'profile_version=%s\nprofile_sha256=%s\niso_sha256=%s\n' \
        "$version" "$profile_sum" "$iso_sum" > "$metadata"; then
        /usr/bin/rm -f -- "$metadata"
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi
    if ! /usr/bin/install -o root -g root -m 0644 "$metadata" "$ISO_METADATA"; then
        /usr/bin/rm -f -- "$metadata"
        /usr/bin/rm -rf -- "$temp"
        fail iso_missing
    fi

    /usr/bin/rm -f -- "$metadata"
    /usr/bin/rm -rf -- "$temp"
}
check_iso_separate() {
    local root_dev iso_dev
    root_dev=$(root_source) || fail iso_filesystem_invalid
    iso_dev=$(iso_source) || fail iso_filesystem_invalid
    [[ -b "$root_dev" && -b "$iso_dev" ]] || fail iso_filesystem_invalid
    [[ "$(/usr/bin/readlink -f "$root_dev")" != "$(/usr/bin/readlink -f "$iso_dev")" ]] \
        || fail iso_filesystem_invalid
}
inspect_iso() {
    [[ -f "$ISO" && ! -L "$ISO" && -s "$ISO" ]] || fail iso_missing
    ISO_KERNEL=$(
        /usr/bin/bsdtar -tf "$ISO" \
            | /usr/bin/grep -E '^(.*/)?arch/boot/[^/]+/vmlinuz-[^/]+$' \
            | /usr/bin/head -n1 || true
    )
    ISO_INITRAMFS=$(
        /usr/bin/bsdtar -tf "$ISO" \
            | /usr/bin/grep -E '^(.*/)?arch/boot/[^/]+/initramfs-[^/]+\.img$' \
            | /usr/bin/head -n1 || true
    )
    [[ -n "$ISO_KERNEL" && -n "$ISO_INITRAMFS" ]] || fail iso_missing
    check_iso_separate
}
iso_loop_path() {
    local mountpoint
    mountpoint=$(/usr/bin/findmnt -rn -o TARGET --target "$ISO") || fail iso_filesystem_invalid
    if [[ "$mountpoint" == / ]]; then
        printf '%s' "$ISO"
    else
        printf '/%s' "${ISO#"$mountpoint/"}"
    fi
}
