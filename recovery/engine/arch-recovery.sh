#!/usr/bin/env bash
set -Eeuo pipefail

export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export TZ=Europe/Moscow

APP_NAME="Arch Manager Recovery"
APP_VERSION="1.9.0"
ROOT_MNT="/mnt/arch-manager-recovery-root"
PROBE_MNT="/mnt/arch-manager-recovery-probe"
BOOT_MNT="/mnt/arch-manager-recovery-boot"
CHROOT_MNT="/mnt/arch-manager-restored-root"
TARGET_DEVICE=""
ROOT_MOUNTED=0
PROBE_MOUNTED=0
BOOT_MOUNTED=0
CHROOT_MOUNTED=0
CHROOT_BOOT_MOUNTED=0
BOOT_DEVICE=""
BOOT_POINT=""
BOOT_BACKUP=""
BOOT_CHANGED=0
BACKUP_NAME=""
NEW_ROOT_CREATED=0
TRANSACTION_STARTED=0
EMERGENCY_HANDLER_ACTIVE=0
RESTORE_LOG=""
KDE_SESSION_PREFS_PRESERVED=0
declare -a MOVED_NESTED=()
declare -a TOP_NESTED=()
PRESELECT_SNAPSHOT=""
PRESELECT_ROOT_UUID=""
LOCAL_BOOT_MODE=0
USB_BOOT_MODE=0
BOOTLOG_HELPER="/usr/local/libexec/arch-manager/recovery-bootlog"
BOOTLOG_RUNTIME="/run/arch-manager/recovery-boot.log"
BOOTLOG_READY="/run/arch-manager/recovery-ready"
RECOVERY_UI_LOCK="/run/arch-manager/recovery-ui.lock"
RECOVERY_TTY="/dev/tty1"

parse_startup_options() {
    local arg token

    while (($#)); do
        case "$1" in
            --snapshot)
                shift
                [[ $# -gt 0 ]] || die "--snapshot requires a restore point ID."
                PRESELECT_SNAPSHOT="$1"
                ;;
            --root-uuid)
                shift
                [[ $# -gt 0 ]] || die "--root-uuid requires the system partition UUID."
                PRESELECT_ROOT_UUID="$1"
                ;;
            --local)
                LOCAL_BOOT_MODE=1
                ;;
            --version)
                printf '%s %s\n' "$APP_NAME" "$APP_VERSION"
                exit 0
                ;;
            *)
                die "Unknown startup option: $1"
                ;;
        esac
        shift
    done

    # Local and USB modes are marked by kernel command-line parameters.
    # A direct manual start remains a separate fallback path.
    if [[ -r /proc/cmdline ]]; then
        for token in $(cat /proc/cmdline); do
            case "$token" in
                arch_manager_local=1)
                    LOCAL_BOOT_MODE=1
                    ;;
                arch_manager_usb=1)
                    USB_BOOT_MODE=1
                    ;;
                arch_manager_snapshot=*)
                    PRESELECT_SNAPSHOT="${token#arch_manager_snapshot=}"
                    ;;
                arch_manager_root_uuid=*)
                    PRESELECT_ROOT_UUID="${token#arch_manager_root_uuid=}"
                    ;;
            esac
        done
    fi

    if [[ -n "$PRESELECT_SNAPSHOT" && ! "$PRESELECT_SNAPSHOT" =~ ^[1-9][0-9]*$ ]]; then
        die "Invalid restore point ID: $PRESELECT_SNAPSHOT"
    fi
    if [[ -n "$PRESELECT_ROOT_UUID" && ! "$PRESELECT_ROOT_UUID" =~ ^[A-Za-z0-9._:-]+$ ]]; then
        die "Invalid system partition UUID."
    fi
}

say() { printf '%s\n' "$*"; }
hr() { printf '%s\n' '------------------------------------------------------------'; }
bootlog_call() {
    [[ -x "$BOOTLOG_HELPER" ]] || return 0
    "$BOOTLOG_HELPER" "$@" >/dev/null 2>&1 || true
}
boot_stage() {
    bootlog_call stage "$*"
}
boot_target_device() {
    [[ -n "${1:-}" ]] || return 0
    bootlog_call target "$1"
}
boot_ready() {
    bootlog_call ready
}
snapshot_epoch_utc() {
    local raw="$1"
    [[ -n "$raw" ]] || return 1

    # Snapper stores info.xml timestamps in UTC without an explicit timezone.
    # Parse them as UTC first. If a future Snapper version includes an explicit
    # offset, GNU date honours that offset even with TZ=UTC set here.
    TZ=UTC date -d "$raw" '+%s' 2>/dev/null
}

format_moscow_datetime() {
    local raw="$1" epoch converted
    [[ -n "$raw" ]] || return 1
    epoch=$(snapshot_epoch_utc "$raw") || return 1
    converted=$(TZ=Europe/Moscow date -d "@$epoch" '+%d.%m.%Y %H:%M:%S MSK' 2>/dev/null) || return 1
    printf '%s' "$converted"
}

recovery_boot_mode() {
    if (( USB_BOOT_MODE )); then
        printf 'usb'
    elif (( LOCAL_BOOT_MODE )); then
        printf 'local'
    else
        printf 'manual'
    fi
}

recovery_boot_mode_text() {
    case "$(recovery_boot_mode)" in
        usb) printf 'recovery USB drive' ;;
        local) printf 'local recovery environment' ;;
        *) printf 'manual start' ;;
    esac
}

die() {
    printf '\nERROR: %s\n' "$*" >&2
    exit 1
}

cleanup_mounts() {
    if (( CHROOT_BOOT_MOUNTED )); then
        umount "$CHROOT_MNT$BOOT_POINT" 2>/dev/null || true
        CHROOT_BOOT_MOUNTED=0
    fi
    if (( CHROOT_MOUNTED )); then
        umount "$CHROOT_MNT" 2>/dev/null || true
        CHROOT_MOUNTED=0
    fi
    if (( BOOT_MOUNTED )); then
        umount "$BOOT_MNT" 2>/dev/null || true
        BOOT_MOUNTED=0
    fi
    if (( PROBE_MOUNTED )); then
        umount "$PROBE_MNT" 2>/dev/null || true
        PROBE_MOUNTED=0
    fi
    if (( ROOT_MOUNTED )); then
        umount "$ROOT_MNT" 2>/dev/null || true
        ROOT_MOUNTED=0
    fi
}
emergency_restore_abort() {
    local status=${1:-1} reason=${2:-"unexpected error"}
    (( EMERGENCY_HANDLER_ACTIVE == 0 )) || exit "$status"
    EMERGENCY_HANDLER_ACTIVE=1
    trap - ERR INT TERM HUP

    if (( TRANSACTION_STARTED )); then
        say
        say "Emergency stop: $reason. Attempting to restore the pre-recovery state..." >&2
        if rollback_transaction; then
            say "Emergency rollback completed." >&2
        else
            say "WARNING: emergency rollback was incomplete. Do not reboot until the system state is checked." >&2
        fi
    fi
    cleanup_mounts
    exit "$status"
}
trap cleanup_mounts EXIT
trap 'emergency_restore_abort $? "unexpected error"' ERR
trap 'emergency_restore_abort 130 "operation interrupted by the user"' INT
trap 'emergency_restore_abort 143 "process received a termination signal"' TERM HUP

return_to_normal_boot() {
    if (( ! LOCAL_BOOT_MODE && ! USB_BOOT_MODE )); then
        return 0
    fi
    if (( USB_BOOT_MODE )); then
        say "Leaving USB Recovery and returning to normal boot..."
    else
        say "Returning to normal Arch Linux boot..."
    fi
    cleanup_mounts
    trap - EXIT ERR INT TERM HUP
    sync
    if /usr/bin/systemctl --no-block reboot; then
        exit 0
    fi
    die "Automatic reboot could not be started. Press Ctrl+Alt+Del."
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

utf8_from_codepoint() {
    local code="$1" b1 b2 b3 b4 escaped

    if (( code <= 0x7F )); then
        printf -v escaped '\\x%02X' "$code"
    elif (( code <= 0x7FF )); then
        b1=$((0xC0 | (code >> 6)))
        b2=$((0x80 | (code & 0x3F)))
        printf -v escaped '\\x%02X\\x%02X' "$b1" "$b2"
    elif (( code <= 0xFFFF )); then
        b1=$((0xE0 | (code >> 12)))
        b2=$((0x80 | ((code >> 6) & 0x3F)))
        b3=$((0x80 | (code & 0x3F)))
        printf -v escaped '\\x%02X\\x%02X\\x%02X' "$b1" "$b2" "$b3"
    else
        b1=$((0xF0 | (code >> 18)))
        b2=$((0x80 | ((code >> 12) & 0x3F)))
        b3=$((0x80 | ((code >> 6) & 0x3F)))
        b4=$((0x80 | (code & 0x3F)))
        printf -v escaped '\\x%02X\\x%02X\\x%02X\\x%02X' "$b1" "$b2" "$b3" "$b4"
    fi
    printf '%b' "$escaped"
}

decode_xml_text() {
    local value="$1" previous entity number code char pass

    # Recovery runs with LC_ALL=C for deterministic shell tools. Bash Unicode
    # escapes are therefore unsuitable here: on some builds they remain as
    # literal text. Decode XML numeric references into UTF-8 bytes explicitly.
    # Two passes also repair legacy descriptions stored as &amp;#xNNNN;.
    for pass in 1 2; do
        previous="$value"
        while [[ "$value" =~ \&\#([xX][0-9a-fA-F]{1,6}|[0-9]{1,7})\; ]]; do
            entity=${BASH_REMATCH[0]}
            number=${BASH_REMATCH[1]}
            if [[ "$number" == [xX]* ]]; then
                code=$((16#${number:1}))
            else
                code=$((10#$number))
            fi
            char='?'
            if (( code >= 32 && code <= 1114111 && (code < 55296 || code > 57343) )); then
                char=$(utf8_from_codepoint "$code")
            fi
            value=${value/"$entity"/"$char"}
        done
        value=${value//&quot;/\"}
        value=${value//&lt;/<}
        value=${value//&gt;/>}
        value=${value//&apos;/\'}
        value=${value//&amp;/\&}
        [[ "$value" == "$previous" ]] && break
    done
    printf '%s' "$value"
}

xml_value() {
    local file="$1" tag="$2" value
    [[ -r "$file" ]] || return 0
    value=$(sed -n "s:.*<$tag>\\(.*\\)</$tag>.*:\\1:p" "$file" | head -n 1)
    decode_xml_text "$value"
}

display_description() {
    local text="${1:-}"
    if [[ -z "$text" ]]; then
        printf 'no description'
        return 0
    fi

    # Repair only the known legacy Arch Manager auto-description. User text is
    # otherwise shown exactly as stored, including real UTF-8 Cyrillic.
    if [[ "$text" == "Arch Manager: pered obnov"* ]]; then
        if [[ "$text" == *AUR* || "$text" == *aur* ]]; then
            text='Arch Manager: перед обновлением AUR'
        else
            text='Arch Manager: перед обновлением системы'
        fi
    fi

    text=${text//$'\r'/ }
    text=${text//$'\n'/ }
    text=${text//$'\t'/ }
    text=${text//$'\e'/ }
    text=${text//$'\177'/ }
    printf '%s' "$text"
}

terminal_columns() {
    local size cols
    size=$(stty -F "$RECOVERY_TTY" size 2>/dev/null || true)
    cols=${size##* }
    if [[ ! "$cols" =~ ^[0-9]+$ ]] || (( cols < 40 )); then
        cols=80
    fi
    printf '%s' "$cols"
}

print_restore_point_row() {
    local number="$1" date="$2" type="$3" description="$4"
    local cols prefix_width desc_width line remaining consumed first=1

    cols=$(terminal_columns)
    prefix_width=38
    desc_width=$((cols - prefix_width))
    (( desc_width >= 12 )) || desc_width=12

    remaining=${description:-no description}
    while [[ -n "$remaining" ]]; do
        if ((${#remaining} <= desc_width)); then
            line="$remaining"
            remaining=""
        else
            line="${remaining:0:desc_width}"
            consumed=$desc_width
            if [[ "$line" == *" "* ]]; then
                line="${line% *}"
                if [[ -n "$line" ]]; then
                    consumed=${#line}
                else
                    line="${remaining:0:desc_width}"
                fi
            fi
            remaining="${remaining:consumed}"
            while [[ "$remaining" == " "* ]]; do
                remaining="${remaining# }"
            done
        fi

        if (( first )); then
            printf '%-3s %-23s %-9s %s\n' "$number" "$date" "$type" "$line"
            first=0
        else
            printf '%*s%s\n' "$prefix_width" '' "$line"
        fi
    done
}

wait_for_recovery_console_stable() {
    # The native DRM/fbcon driver may take over tty1 shortly after userspace
    # starts. If the menu is drawn before that hand-off, it appears once on
    # the early framebuffer, disappears during the hand-off, then the same
    # tty buffer becomes visible again. Wait for udev to finish processing
    # device events before the one and only Recovery render.
    if command -v udevadm >/dev/null 2>&1; then
        boot_stage "console-udev-settle-start"
        if udevadm settle --timeout=15; then
            boot_stage "console-udev-settle-done"
        else
            boot_stage "console-udev-settle-timeout"
        fi
    fi
}

prepare_recovery_console() {
    local tty="$RECOVERY_TTY" font_file="" candidate
    [[ -c "$tty" ]] || tty=/dev/console
    [[ -c "$tty" ]] || return 0

    # One console, one setup pass. The menu labels are ASCII; a Cyrillic font
    # is best-effort so Cyrillic restore-point descriptions remain readable.
    for candidate in \
        LatArCyrHeb-16.psfu.gz \
        LatArCyrHeb-16.psfu \
        LatGrkCyr-8x16.psfu.gz \
        Cyr_a8x16.psfu.gz \
        UniCyr_8x16.psf.gz; do
        if [[ -r "/usr/share/kbd/consolefonts/$candidate" ]]; then
            font_file="/usr/share/kbd/consolefonts/$candidate"
            if setfont -C "$tty" "$font_file" >/dev/null 2>&1; then
                boot_stage "console-font-loaded file=$candidate"
                break
            fi
            font_file=""
        fi
    done

    printf '\033%%G' >"$tty" 2>/dev/null || true
    if [[ -z "$font_file" ]]; then
        boot_stage "console-font-warning no-cyrillic-font-loaded"
    fi
}

clear_recovery_console() {
    # This is the only Recovery screen clear. There is no hidden console switch
    # and no second render. systemd gives the wizard exclusive ownership of tty1.
    printf '\033[2J\033[H\033[?25h'
}

print_recovery_header() {
    say "$APP_NAME v$APP_VERSION"
    hr
    if (( LOCAL_BOOT_MODE )); then
        say "Local Recovery started without a USB drive."
        say "The installed Arch Linux system is offline, so the @ root can be replaced safely."
    elif (( USB_BOOT_MODE )); then
        say "Recovery started from the USB drive."
        say "The same recovery engine is used for local and USB Recovery."
    else
        say "This wizard is intended for the Arch Manager Recovery environment or an Arch Linux live USB."
        say "It finds the system, lists restore points, and restores the selected point."
    fi
    say "System found: $TARGET_DEVICE"
    say
}

acquire_single_recovery_ui() {
    /usr/bin/install -d -o root -g root -m 0755 /run/arch-manager 2>/dev/null || true
    exec 9>"$RECOVERY_UI_LOCK"
    if ! /usr/bin/flock -n 9; then
        boot_stage "duplicate-ui-instance-refused"
        exit 0
    fi
    boot_stage "single-ui-lock-acquired"
}

require_live_environment() {
    local fs src opts
    fs=$(findmnt -no FSTYPE / 2>/dev/null || true)
    src=$(findmnt -no SOURCE / 2>/dev/null || true)
    opts=$(findmnt -no OPTIONS / 2>/dev/null || true)

    if [[ "$fs" == "btrfs" ]] \
        && { [[ "$src" == *"[/@]"* ]] || [[ ",$opts," == *,subvol=/@,* ]]; }; then
        die "Recovery was started from the installed Arch Linux system.\n\nA full restore must run outside the active @ root. Start local Recovery from Arch Manager or boot from the Recovery USB drive."
    fi
}

require_root() {
    if (( EUID == 0 )); then
        return 0
    fi
    if command -v sudo >/dev/null 2>&1; then
        exec sudo bash "$0" "$@"
    fi
    die "Root privileges are required. In an Arch Linux live environment you normally already run as root."
}

probe_device() {
    local dev="$1" ok=1
    mkdir -p "$PROBE_MNT"
    if mount -o ro,subvolid=5 "$dev" "$PROBE_MNT" 2>/dev/null; then
        PROBE_MOUNTED=1
        if btrfs subvolume show "$PROBE_MNT/@" >/dev/null 2>&1 \
            && btrfs subvolume show "$PROBE_MNT/@snapshots" >/dev/null 2>&1; then
            ok=0
        fi
        umount "$PROBE_MNT" 2>/dev/null || true
        PROBE_MOUNTED=0
    fi
    return "$ok"
}

find_system_device() {
    local -a all=() candidates=()
    local dev answer i

    if [[ -n "$PRESELECT_ROOT_UUID" ]]; then
        dev=$(findfs "UUID=$PRESELECT_ROOT_UUID" 2>/dev/null || true)
        if [[ -n "$dev" && -b "$dev" ]] && probe_device "$dev"; then
            TARGET_DEVICE="$dev"
            return
        fi
        die "The Btrfs system partition with UUID $PRESELECT_ROOT_UUID was not found, or its @/@snapshots layout changed."
    fi

    mapfile -t all < <(lsblk -rpno NAME,FSTYPE 2>/dev/null | awk '$2=="btrfs" {print $1}' | awk '!seen[$0]++')

    for dev in "${all[@]}"; do
        [[ -b "$dev" ]] || continue
        if probe_device "$dev"; then
            candidates+=("$dev")
        fi
    done

    if ((${#candidates[@]} == 0)); then
        say
        say "The system partition was not found automatically."
        say "You can enter the device path manually."
        read -r -p "Btrfs device path (for example /dev/nvme0n1p4), or press Enter to exit: " answer
        [[ -n "$answer" ]] || exit 1
        [[ -b "$answer" ]] || die "Device $answer was not found."
        probe_device "$answer" || die "A valid @ root and @snapshots store were not found on $answer."
        TARGET_DEVICE="$answer"
        return
    fi

    if ((${#candidates[@]} == 1)); then
        TARGET_DEVICE="${candidates[0]}"
        return
    fi

    say
    say "Multiple suitable Btrfs partitions were found:"
    for i in "${!candidates[@]}"; do
        printf '  %d) %s\n' "$((i+1))" "${candidates[i]}"
    done
    while true; do
        read -r -p "Select a number: " answer
        [[ "$answer" =~ ^[0-9]+$ ]] || { say "Enter a number from the list."; continue; }
        (( answer >= 1 && answer <= ${#candidates[@]} )) || { say "That number is not available."; continue; }
        TARGET_DEVICE="${candidates[answer-1]}"
        return
    done
}

mount_system_top() {
    mkdir -p "$ROOT_MNT"
    mount -o subvolid=5 "$TARGET_DEVICE" "$ROOT_MNT" || die "Could not mount the Btrfs top level."
    ROOT_MOUNTED=1

    btrfs subvolume show "$ROOT_MNT/@" >/dev/null 2>&1 || die "Active @ root was not found."
    btrfs subvolume show "$ROOT_MNT/@snapshots" >/dev/null 2>&1 || die "@snapshots store was not found."
}

list_restore_points() {
    local -a records=() ids=()
    local dir id info raw_date sort_epoch date desc flag index

    # Same ordering as desktop GUI: newest creation date first.
    # Snapper ID is an internal identifier only.
    while IFS= read -r id; do
        [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
        dir="$ROOT_MNT/@snapshots/$id"
        [[ -d "$dir/snapshot" ]] || continue
        btrfs subvolume show "$dir/snapshot" >/dev/null 2>&1 || continue
        [[ "$(btrfs property get -ts "$dir/snapshot" ro 2>/dev/null || true)" == "ro=true" ]] || continue

        info="$dir/info.xml"
        raw_date=$(xml_value "$info" date)
        if [[ -n "$raw_date" ]]; then
            sort_epoch=$(snapshot_epoch_utc "$raw_date" 2>/dev/null || printf '%s' -1)
        else
            sort_epoch=-1
        fi
        records+=("$sort_epoch|$id")
    done < <(find "$ROOT_MNT/@snapshots" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -n)

    ((${#records[@]})) || die "No restore points were found."

    while IFS='|' read -r _ id; do
        [[ -n "$id" ]] && ids+=("$id")
    done < <(printf '%s\n' "${records[@]}" | sort -t'|' -k1,1nr -k2,2n)

    say
    hr
    say "AVAILABLE RESTORE POINTS"
    hr
    printf '%-3s %-23s %-9s %s\n' "No." "Date" "Type" "Description"
    hr

    # Visible numbers always match GUI: 1, 2, 3... in the same order.
    for index in "${!ids[@]}"; do
        id="${ids[index]}"
        info="$ROOT_MNT/@snapshots/$id/info.xml"
        date=$(xml_value "$info" date)
        desc=$(display_description "$(xml_value "$info" description)")
        flag=$(xml_value "$info" userdata)

        if [[ -n "$date" ]]; then
            date=$(format_moscow_datetime "$date" || printf '%s' "$date")
        else
            date="date unavailable"
        fi
        [[ -n "$desc" ]] || desc="no description"

        if [[ "$flag" == *"important=yes"* ]] || grep -Eq '<entry[[:space:]]+key="important">yes</entry>|important=yes' "$info" 2>/dev/null; then
            flag="IMPORTANT"
        else
            flag="normal"
        fi

        print_restore_point_row "$((index + 1))" "${date:0:23}" "$flag" "$desc"
    done

    SNAPSHOT_IDS=("${ids[@]}")
}

detect_nested_subvolumes() {
    local -a all=() sorted=()
    local path rel parent skip line
    TOP_NESTED=()

    while IFS= read -r line; do
        path=${line#* path }
        [[ "$path" == @/* ]] || continue
        rel=${path#@/}
        [[ -n "$rel" ]] && all+=("$rel")
    done < <(btrfs subvolume list -o "$ROOT_MNT/@" 2>/dev/null || true)

    ((${#all[@]})) || return 0

    mapfile -t sorted < <(
        for rel in "${all[@]}"; do
            printf '%d\t%s\n' "$(awk -F/ '{print NF}' <<<"$rel")" "$rel"
        done | sort -n -k1,1 -k2,2 | cut -f2-
    )

    for rel in "${sorted[@]}"; do
        skip=0
        for parent in "${TOP_NESTED[@]}"; do
            if [[ "$rel" == "$parent/"* ]]; then
                skip=1
                break
            fi
        done
        (( skip )) || TOP_NESTED+=("$rel")
    done
}

resolve_boot_from_snapshot() {
    local snap="$1" spec="" mp="" fstype="" opts="" target_real boot_real actual_fstype
    BOOT_DEVICE=""
    BOOT_POINT=""

    [[ -r "$snap/etc/fstab" ]] || die "The selected restore point has no /etc/fstab. Restore cancelled."

    read -r spec mp fstype opts < <(
        awk '
            /^[[:space:]]*#/ { next }
            NF >= 4 && ($2=="/boot" || $2=="/efi" || $2=="/boot/efi") {
                print $1, $2, $3, $4; exit
            }
        ' "$snap/etc/fstab"
    ) || true

    [[ -n "$spec" ]] || return 0

    if [[ "$spec" == /dev/* && -b "$spec" ]]; then
        BOOT_DEVICE="$spec"
    else
        BOOT_DEVICE=$(findfs "$spec" 2>/dev/null || true)
    fi
    [[ -n "$BOOT_DEVICE" && -b "$BOOT_DEVICE" ]] || die "/etc/fstab specifies a separate $mp, but device $spec could not be found. Restore cancelled before changes."
    BOOT_DEVICE=$(readlink -f -- "$BOOT_DEVICE") || die "Could not verify device $mp. Restore cancelled."
    target_real=$(readlink -f -- "$TARGET_DEVICE") || die "Could not verify the system Btrfs partition. Restore cancelled."
    boot_real=$(readlink -f -- "$BOOT_DEVICE") || die "Could not verify device $mp. Restore cancelled."
    [[ "$boot_real" != "$target_real" ]] || die "The boot partition in /etc/fstab resolves to the system Btrfs partition. Automatic restore stopped to protect data."

    actual_fstype=$(blkid -s TYPE -o value -- "$BOOT_DEVICE" 2>/dev/null || true)
    if [[ -n "$actual_fstype" && "${fstype,,}" != "auto" && "${fstype,,}" != "none" \
        && "${actual_fstype,,}" != "${fstype,,}" ]]; then
        die "Filesystem type for $mp does not match /etc/fstab ($fstype vs $actual_fstype). Restore stopped for layout verification."
    fi
    BOOT_POINT="$mp"

    command -v arch-chroot >/dev/null 2>&1 || die "A separate $BOOT_POINT requires arch-chroot. Use the Arch Manager Recovery USB drive or a standard Arch Linux live USB."
    [[ -x "$snap/usr/bin/mkinitcpio" ]] || die "mkinitcpio was not found in the selected restore point. Restore cancelled before changes."
}

preflight_nested_destinations() {
    local snap="$1" rel dest
    for rel in "${TOP_NESTED[@]}"; do
        dest="$snap/$rel"
        if btrfs subvolume show "$dest" >/dev/null 2>&1; then
            die "Path $rel is a separate subvolume inside the selected restore point. Automatic restore stopped before changes."
        fi
        if [[ -d "$dest" ]] && find "$dest" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
            die "Directory /$rel is not empty in the selected restore point while the current system uses a separate subvolume there. Automatic restore stopped to protect data."
        fi
    done
}

mounted_device_matches() {
    local mountpoint=$1 expected=$2 actual actual_real expected_real
    actual=$(findmnt -rn -o SOURCE --target "$mountpoint" 2>/dev/null | sed 's/\[.*$//' | head -n1)
    [[ -n "$actual" ]] || return 1
    actual_real=$(readlink -f -- "$actual" 2>/dev/null) || return 1
    expected_real=$(readlink -f -- "$expected" 2>/dev/null) || return 1
    [[ "$actual_real" == "$expected_real" ]]
}

backup_boot_partition() {
    local stamp="$1" save_dir
    [[ -n "$BOOT_DEVICE" ]] || return 0

    save_dir="$ROOT_MNT/@snapshots/.arch-manager-recovery"
    mkdir -p "$save_dir"
    BOOT_BACKUP="$save_dir/boot-before-$stamp.tar"

    mkdir -p "$BOOT_MNT"
    mount -o ro "$BOOT_DEVICE" "$BOOT_MNT" || die "Could not mount $BOOT_POINT for backup."
    BOOT_MOUNTED=1
    mounted_device_matches "$BOOT_MNT" "$BOOT_DEVICE" \
        || die "A different device appeared after mounting $BOOT_POINT. Restore stopped."

    say "Backing up the current $BOOT_POINT contents before changes..."
    tar -C "$BOOT_MNT" -cpf "$BOOT_BACKUP" . || die "Could not back up $BOOT_POINT. Restore has not started."
    tar -tf "$BOOT_BACKUP" >/dev/null 2>&1 || die "The $BOOT_POINT backup failed verification. Restore has not started."

    umount "$BOOT_MNT"
    BOOT_MOUNTED=0
}

restore_boot_backup() {
    [[ -n "$BOOT_DEVICE" && -n "$BOOT_BACKUP" && -f "$BOOT_BACKUP" ]] || return 0
    mkdir -p "$BOOT_MNT"
    mount "$BOOT_DEVICE" "$BOOT_MNT" || return 1
    BOOT_MOUNTED=1
    mounted_device_matches "$BOOT_MNT" "$BOOT_DEVICE" || return 1

    tar -tf "$BOOT_BACKUP" >/dev/null 2>&1 || return 1
    find "$BOOT_MNT" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + || return 1
    tar -C "$BOOT_MNT" -xpf "$BOOT_BACKUP" || return 1
    sync
    umount "$BOOT_MNT" || true
    BOOT_MOUNTED=0
}

move_nested_to_new_root() {
    local rel src dst
    MOVED_NESTED=()

    for rel in "${TOP_NESTED[@]}"; do
        src="$ROOT_MNT/$BACKUP_NAME/$rel"
        dst="$ROOT_MNT/@/$rel"

        btrfs subvolume show "$src" >/dev/null 2>&1 || continue
        mkdir -p "$(dirname "$dst")"
        if [[ -d "$dst" ]]; then
            rmdir "$dst" 2>/dev/null || return 1
        elif [[ -e "$dst" ]]; then
            return 1
        fi

        mv "$src" "$dst" || return 1
        MOVED_NESTED+=("$rel")
    done
}

preserve_kde_session_preferences() {
    local old_home="$ROOT_MNT/$BACKUP_NAME/home"
    local new_home="$ROOT_MNT/@/home"
    local source rel target parent

    KDE_SESSION_PREFS_PRESERVED=0
    [[ -d "$old_home" && ! -L "$old_home" && -d "$new_home" && ! -L "$new_home" ]] || return 0

    while IFS= read -r -d '' source; do
        [[ -f "$source" && ! -L "$source" ]] || continue
        rel="${source#"$old_home"/}"
        [[ "$rel" == */.config/ksmserverrc ]] || continue
        target="$new_home/$rel"
        parent=$(dirname "$target")
        [[ -d "$parent" && ! -L "$parent" && ! -L "$target" ]] || continue

        cp -a --reflink=auto -- "$source" "$target" || return 1
        ((KDE_SESSION_PREFS_PRESERVED += 1))
    done < <(find "$old_home" -mindepth 3 -maxdepth 3 -path '*/.config/ksmserverrc' -type f -print0 2>/dev/null)
}

move_nested_back_to_old_root() {
    local i rel src dst
    for (( i=${#MOVED_NESTED[@]}-1; i>=0; i-- )); do
        rel="${MOVED_NESTED[i]}"
        src="$ROOT_MNT/@/$rel"
        dst="$ROOT_MNT/$BACKUP_NAME/$rel"
        btrfs subvolume show "$src" >/dev/null 2>&1 || continue
        mkdir -p "$(dirname "$dst")"
        [[ -d "$dst" ]] && rmdir "$dst" 2>/dev/null || true
        mv "$src" "$dst" || return 1
    done
}

sync_boot_with_restored_root() {
    local module_dir pkgbase copied=0 rc=0
    [[ -n "$BOOT_DEVICE" ]] || return 0

    # $ROOT_MNT/@ is a subvolume directory inside the top-level mount, not
    # a standalone mount point. mkinitcpio/autodetect needs a real root mount,
    # otherwise it reports "root is not a mountpoint".
    mkdir -p "$CHROOT_MNT"
    if ! mount -o subvol=@ "$TARGET_DEVICE" "$CHROOT_MNT"; then
        return 1
    fi
    CHROOT_MOUNTED=1

    mkdir -p "$CHROOT_MNT$BOOT_POINT"
    if ! mount "$BOOT_DEVICE" "$CHROOT_MNT$BOOT_POINT"; then
        umount "$CHROOT_MNT" 2>/dev/null || true
        CHROOT_MOUNTED=0
        return 1
    fi
    CHROOT_BOOT_MOUNTED=1
    if ! mounted_device_matches "$CHROOT_MNT$BOOT_POINT" "$BOOT_DEVICE"; then
        umount "$CHROOT_MNT$BOOT_POINT" 2>/dev/null || true
        CHROOT_BOOT_MOUNTED=0
        umount "$CHROOT_MNT" 2>/dev/null || true
        CHROOT_MOUNTED=0
        return 1
    fi
    BOOT_CHANGED=1

    for module_dir in "$CHROOT_MNT"/usr/lib/modules/*; do
        [[ -d "$module_dir" && -r "$module_dir/pkgbase" && -f "$module_dir/vmlinuz" ]] || continue
        pkgbase=$(<"$module_dir/pkgbase")
        [[ "$pkgbase" =~ ^[A-Za-z0-9._+-]+$ ]] || continue
        if ! install -m 0644 "$module_dir/vmlinuz" "$CHROOT_MNT$BOOT_POINT/vmlinuz-$pkgbase"; then
            rc=1
            break
        fi
        copied=1
    done

    (( copied )) || rc=1

    if (( rc == 0 )); then
        if ! arch-chroot "$CHROOT_MNT" /usr/bin/mkinitcpio -P; then
            rc=1
        fi
    fi

    sync
    if (( CHROOT_BOOT_MOUNTED )); then
        umount "$CHROOT_MNT$BOOT_POINT" 2>/dev/null || rc=1
        CHROOT_BOOT_MOUNTED=0
    fi
    if (( CHROOT_MOUNTED )); then
        umount "$CHROOT_MNT" 2>/dev/null || rc=1
        CHROOT_MOUNTED=0
    fi

    return "$rc"
}

rollback_transaction() {
    local ok=1
    say
    say "Restoring the system state from before this restore attempt..."

    if (( CHROOT_BOOT_MOUNTED )); then
        umount "$CHROOT_MNT$BOOT_POINT" 2>/dev/null || true
        CHROOT_BOOT_MOUNTED=0
    fi
    if (( CHROOT_MOUNTED )); then
        umount "$CHROOT_MNT" 2>/dev/null || true
        CHROOT_MOUNTED=0
    fi

    if (( BOOT_MOUNTED )); then
        umount "$BOOT_MNT" 2>/dev/null || true
        umount "$ROOT_MNT/@$BOOT_POINT" 2>/dev/null || true
        BOOT_MOUNTED=0
    fi

    if (( BOOT_CHANGED )); then
        restore_boot_backup || ok=0
    fi

    if (( NEW_ROOT_CREATED )); then
        move_nested_back_to_old_root || ok=0
        btrfs subvolume delete "$ROOT_MNT/@" >/dev/null 2>&1 || ok=0
        NEW_ROOT_CREATED=0
    fi

    if [[ -n "$BACKUP_NAME" && -e "$ROOT_MNT/$BACKUP_NAME" && ! -e "$ROOT_MNT/@" ]]; then
        mv "$ROOT_MNT/$BACKUP_NAME" "$ROOT_MNT/@" || ok=0
    fi

    sync
    if (( ok )); then
        TRANSACTION_STARTED=0
        say "The original state was restored. It is safe to reboot without applying the selected point."
        return 0
    fi

    say "WARNING: automatic rollback was incomplete."
    say "Do not reboot until the state is checked. The Btrfs top level is mounted at $ROOT_MNT."
    return 1
}

write_restore_log() {
    local id="$1" display_number="$2" stamp="$3" log_dir
    log_dir="$ROOT_MNT/@snapshots/.arch-manager-recovery"
    mkdir -p "$log_dir"
    RESTORE_LOG="$log_dir/restore-$stamp.txt"
    {
        printf 'Arch Manager Recovery %s\n' "$APP_VERSION"
        printf 'Date: %s\n' "$(date -Is 2>/dev/null || date)"
        printf 'Boot mode: %s\n' "$(recovery_boot_mode_text)"
        printf 'Device: %s\n' "$TARGET_DEVICE"
        printf 'Restore point: %s\n' "$display_number"
        printf 'Snapper ID: %s\n' "$id"
        printf 'Previous root: %s\n' "$BACKUP_NAME"
        printf 'New root: @\n'
        printf 'Separate boot partition: %s\n' "${BOOT_DEVICE:-none}"
        if ((${#TOP_NESTED[@]})); then
            printf 'Preserved nested subvolumes:\n'
            printf '  %s\n' "${TOP_NESTED[@]}"
        fi
        if (( KDE_SESSION_PREFS_PRESERVED > 0 )); then
            printf 'Preserved current Plasma session settings: %s profile(s)\n' "$KDE_SESSION_PREFS_PRESERVED"
        fi
    } >"$RESTORE_LOG"

    # Marker of the most recent restore for the normal Arch Manager GUI.
    {
        printf 'version=%s\n' "$APP_VERSION"
        printf 'date=%s\n' "$(date -Is 2>/dev/null || date)"
        printf 'boot_mode=%s\n' "$(recovery_boot_mode)"
        printf 'device=%s\n' "$TARGET_DEVICE"
        printf 'snapshot=%s\n' "$id"
        printf 'display_snapshot=%s\n' "$display_number"
        printf 'backup=%s\n' "$BACKUP_NAME"
        printf 'restore_log=%s\n' "$RESTORE_LOG"
    } >"$log_dir/last-restore.env"
}

perform_restore() {
    local id display_number snap
    local stamp answer reboot_status

    id="$1"
    display_number="${2:-$1}"
    [[ "$id" =~ ^[1-9][0-9]*$ ]] || die "Invalid restore point ID. The system was not changed."
    snap="$ROOT_MNT/@snapshots/$id/snapshot"
    [[ -d "$snap" ]] && btrfs subvolume show "$snap" >/dev/null 2>&1 \
        || die "The selected restore point no longer exists. The system was not changed."
    [[ "$(btrfs property get -ts "$snap" ro 2>/dev/null || true)" == "ro=true" ]] \
        || die "The selected point is not a read-only Btrfs snapshot. Restore stopped for safety."
    stamp=$(date +%Y%m%d-%H%M%S)
    BACKUP_NAME="@.broken-$stamp"
    while [[ -e "$ROOT_MNT/$BACKUP_NAME" ]]; do
        sleep 1
        stamp=$(date +%Y%m%d-%H%M%S)
        BACKUP_NAME="@.broken-$stamp"
    done

    detect_nested_subvolumes
    resolve_boot_from_snapshot "$snap"
    preflight_nested_destinations "$snap"

    say
    hr
    say "RESTORE POINT $display_number"
    hr
    say "Selection accepted. No additional confirmation is required."
    say "Arch Manager will perform the restore automatically."
    say
    say "Preparing restore..."
    backup_boot_partition "$stamp"

    say "Preserving the current system state..."
    mv "$ROOT_MNT/@" "$ROOT_MNT/$BACKUP_NAME" || die "Could not rename the current @ root. No changes were made."
    TRANSACTION_STARTED=1

    say "Restoring the system root from point $display_number..."
    if ! btrfs subvolume snapshot "$snap" "$ROOT_MNT/@" >/dev/null; then
        if mv "$ROOT_MNT/$BACKUP_NAME" "$ROOT_MNT/@" 2>/dev/null; then
            TRANSACTION_STARTED=0
        fi
        die "Could not create the new @ root. The original root was restored."
    fi
    NEW_ROOT_CREATED=1

    say "Restoring separate system subvolumes..."
    if ! move_nested_to_new_root; then
        rollback_transaction || true
        die "A nested subvolume could not be restored safely. Restore cancelled."
    fi

    # A system rollback must not unexpectedly restore an old Plasma setting
    # for shutdown/reboot confirmation. If /home is a separate subvolume,
    # it was already preserved above; if /home is stored inside @, preserve
    # only the small ksmserverrc file from the current system.
    if ! preserve_kde_session_preferences; then
        say "WARNING: current Plasma session settings could not be preserved; system restore will continue."
    elif (( KDE_SESSION_PREFS_PRESERVED > 0 )); then
        say "   Current Plasma shutdown/reboot settings were preserved."
    fi

    [[ -r "$ROOT_MNT/@/etc/fstab" && -x "$ROOT_MNT/@/usr/bin/bash" ]] || {
        rollback_transaction || true
        die "The new @ root failed verification. The original state was restored."
    }

    say "Synchronizing boot files..."
    if ! sync_boot_with_restored_root; then
        rollback_transaction || true
        die "Boot files could not be updated safely. Restore attempt cancelled."
    fi

    say "Finalizing restore and writing the report..."
    sync
    write_restore_log "$id" "$display_number" "$stamp"
    TRANSACTION_STARTED=0
    say "Restore completed."

    say
    hr
    say "RESTORE COMPLETED"
    hr
    say "The system was restored from point $display_number and is ready to reboot."
    say "The previous state was preserved as: $BACKUP_NAME"
    say "Report: $RESTORE_LOG"
    say
    read -r -s -n 1 -p "Press any key to reboot, or 0 to exit without rebooting: " answer || true
    say
    if [[ "$answer" == "0" ]]; then
        say "Exit without rebooting."
        return 0
    fi

    cleanup_mounts
    trap - EXIT ERR INT TERM HUP
    set +e
    reboot
    reboot_status=$?
    set -e
    (( reboot_status == 0 )) \
        || die "Restore completed, but automatic reboot could not be started. Press Ctrl+Alt+Del."
    exit 0
}

main() {
    boot_stage "engine-start"
    parse_startup_options "$@"
    boot_stage "startup-options-parsed mode=$(recovery_boot_mode)"
    require_root "$@"
    boot_stage "root-privileges-ok"
    acquire_single_recovery_ui
    require_live_environment
    boot_stage "live-environment-ok"

    for c in btrfs lsblk mount umount findmnt awk sed grep sort cut find findfs tar install cp date sync blkid readlink head sleep stty; do
        need_cmd "$c"
    done
    boot_stage "required-commands-ok"

    # Discovery happens before the screen is drawn. On normal local/USB boots it
    # is non-interactive, so the user sees only the final menu once.
    boot_stage "find-system-start"
    if [[ -n "$PRESELECT_ROOT_UUID" ]]; then
        find_system_device >/dev/null
    else
        find_system_device
    fi
    boot_target_device "$TARGET_DEVICE"
    boot_stage "find-system-done device=$TARGET_DEVICE"

    boot_stage "mount-system-top-start"
    mount_system_top
    boot_stage "mount-system-top-done"

    boot_stage "console-stability-wait-start"
    wait_for_recovery_console_stable
    boot_stage "console-stability-wait-done"

    boot_stage "console-setup-start"
    prepare_recovery_console
    boot_stage "console-setup-done"

    clear_recovery_console
    print_recovery_header

    boot_stage "list-restore-points-start"
    list_restore_points
    boot_stage "list-restore-points-done count=${#SNAPSHOT_IDS[@]}"

    # The watchdog stops only after the system, Btrfs layout and restore-point
    # list are all ready. From this point tty1 is stable and interactive.
    boot_ready

    # Visually separate the restore-point table from the interactive prompt.
    # Local and USB Recovery share this same engine.
    say

    local selected selected_id selected_display valid id index
    if [[ -n "$PRESELECT_SNAPSHOT" ]]; then
        selected_id="$PRESELECT_SNAPSHOT"
        selected_display=""
        valid=0
        for index in "${!SNAPSHOT_IDS[@]}"; do
            id="${SNAPSHOT_IDS[index]}"
            if [[ "$selected_id" == "$id" ]]; then
                valid=1
                selected_display="$((index + 1))"
                break
            fi
        done
        (( valid )) || die "The requested restore point no longer exists. The system was not changed."
        say
        say "Arch Manager preselected restore point $selected_display."
    else
        while true; do
            read -r -p "Enter a restore point number, or 0 to exit: " selected
            if [[ "$selected" == "0" ]]; then
                say "Exit. The system was not changed."
                if (( LOCAL_BOOT_MODE || USB_BOOT_MODE )); then
                    return_to_normal_boot
                fi
                return 0
            fi
            if [[ "$selected" =~ ^[1-9][0-9]*$ ]] \
                && (( selected >= 1 && selected <= ${#SNAPSHOT_IDS[@]} )); then
                selected_display="$selected"
                selected_id="${SNAPSHOT_IDS[selected - 1]}"
                break
            fi
            say "That restore point is not available."
        done
    fi

    perform_restore "$selected_id" "$selected_display"
}
main "$@"
