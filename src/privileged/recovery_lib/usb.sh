# Arch Manager Recovery privileged helper module.
# Sourced only by manage_recovery.sh from a fixed local directory.

ensure_usb_runtime_dir() {
    local owner mode
    if [[ -e /run/arch-manager && ( ! -d /run/arch-manager || -L /run/arch-manager ) ]]; then
        return 1
    fi
    /usr/bin/install -d -o root -g root -m 0755 /run/arch-manager || return 1
    owner=$(/usr/bin/stat -Lc '%u:%g' /run/arch-manager 2>/dev/null) || return 1
    mode=$(/usr/bin/stat -Lc '%a' /run/arch-manager 2>/dev/null) || return 1
    [[ "$owner" == '0:0' && "$mode" == '755' ]]
}
set_usb_progress_tenths() {
    local tenths=$1 message=$2 tmp
    [[ "$tenths" =~ ^[0-9]+$ ]] || return 0
    (( tenths > 1000 )) && tenths=1000
    ensure_usb_runtime_dir || return 1
    tmp="${USB_PROGRESS_FILE}.$$"
    /usr/bin/printf '%s\t%s\n' "$tenths" "$message" >"$tmp"
    /usr/bin/chmod 0644 "$tmp"
    /usr/bin/mv -f -- "$tmp" "$USB_PROGRESS_FILE"
}
set_usb_progress() {
    local percent=$1 message=$2
    set_usb_progress_tenths "$((percent * 10))" "$message"
}
entry_value() {
    local key=$1
    /usr/bin/sed -n "s/.*${key}=\([^ ]*\).*/\1/p" "$ENTRY" | /usr/bin/head -n1
}
usb_identity() {
    local device=$1 majmin sys_path sectors removable ro_flag
    majmin=$(/usr/bin/lsblk -dnro MAJ:MIN -- "$device" 2>/dev/null | /usr/bin/head -n1 | /usr/bin/xargs) || return 1
    [[ "$majmin" =~ ^[0-9]+:[0-9]+$ ]] || return 1
    sys_path=$(/usr/bin/readlink -f -- "/sys/dev/block/$majmin" 2>/dev/null) || return 1
    [[ "$sys_path" == /sys/* && -d "$sys_path" ]] || return 1
    sectors=$(/usr/bin/cat -- "$sys_path/size" 2>/dev/null) || return 1
    removable=$(/usr/bin/cat -- "$sys_path/removable" 2>/dev/null) || return 1
    ro_flag=$(/usr/bin/cat -- "$sys_path/ro" 2>/dev/null) || return 1
    [[ "$sectors" =~ ^[0-9]+$ && "$removable" =~ ^[01]$ && "$ro_flag" =~ ^[01]$ ]] || return 1
    /usr/bin/printf '%s\t%s\t%s\t%s\t%s' \
        "$majmin" "$sys_path" "$sectors" "$removable" "$ro_flag" \
        | /usr/bin/sha256sum | /usr/bin/awk '{print $1}'
}
legacy_usb_identity_1812() {
    local device=$1
    /usr/bin/lsblk -J -b -o PATH,TYPE,SIZE,MODEL,TRAN,RM,RO -- "$device" 2>/dev/null \
        | /usr/bin/python -c '
import hashlib, json, sys
payload = json.load(sys.stdin)
devices = payload.get("blockdevices") or []
if len(devices) != 1 or str(devices[0].get("type") or "") != "disk":
    raise SystemExit(1)
raw = devices[0]
def clean(value):
    return " ".join(str(value or "").split())
try:
    size = max(int(raw.get("size") or 0), 0)
except (TypeError, ValueError):
    size = 0
parts = (
    str(size), clean(raw.get("model")),
    str(raw.get("tran") or "").strip().lower(),
    "1" if bool(raw.get("rm")) else "0", "1" if bool(raw.get("ro")) else "0",
)
print(hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest())
'
}
legacy_usb_identity_1811() {
    local device=$1
    /usr/bin/lsblk -J -b -o PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RM,RO -- "$device" 2>/dev/null \
        | /usr/bin/python -c '
import hashlib, json, sys
payload = json.load(sys.stdin)
devices = payload.get("blockdevices") or []
if len(devices) != 1 or str(devices[0].get("type") or "") != "disk":
    raise SystemExit(1)
raw = devices[0]
def clean(value):
    return " ".join(str(value or "").split())
try:
    size = max(int(raw.get("size") or 0), 0)
except (TypeError, ValueError):
    size = 0
parts = (
    str(size), clean(raw.get("model")), clean(raw.get("serial")),
    clean(raw.get("wwn")), str(raw.get("tran") or "").strip().lower(),
    "1" if bool(raw.get("rm")) else "0", "1" if bool(raw.get("ro")) else "0",
)
print(hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest())
'
}
verify_usb_identity() {
    local device=$1 expected_identity=$2 actual_identity legacy_identity
    if [[ "$expected_identity" =~ ^k1:[0-9a-f]{64}$ ]]; then
        actual_identity=$(usb_identity "$device" 2>/dev/null || true)
        [[ -n "$actual_identity" && "k1:$actual_identity" == "$expected_identity" ]] || fail usb_device_changed
        return 0
    fi

    # Compatibility for a GUI process that was already running while Recovery
    # 1.8.13 was installed.  Old processes send an unversioned 1.8.11/1.8.12
    # token.  If that exact legacy token is still reproducible, finish safely;
    # otherwise explain that the application itself must be restarted instead
    # of pretending that the physical USB stick changed.
    if [[ "$expected_identity" =~ ^[0-9a-f]{64}$ ]]; then
        legacy_identity=$(legacy_usb_identity_1812 "$device" 2>/dev/null || true)
        [[ -n "$legacy_identity" && "$legacy_identity" == "$expected_identity" ]] && return 0
        legacy_identity=$(legacy_usb_identity_1811 "$device" 2>/dev/null || true)
        [[ -n "$legacy_identity" && "$legacy_identity" == "$expected_identity" ]] && return 0
        fail usb_identity_restart_required
    fi
    fail usb_device_invalid
}
top_disk_for_node() {
    local node parent
    node=$(/usr/bin/readlink -f -- "$1" 2>/dev/null) || return 1
    [[ -b "$node" ]] || return 1
    while true; do
        parent=$(/usr/bin/lsblk -dnro PKNAME -- "$node" 2>/dev/null | /usr/bin/head -n1)
        [[ -n "$parent" ]] || break
        node="/dev/$parent"
    done
    /usr/bin/readlink -f -- "$node"
}
VALIDATED_USB_DEVICE=''
validate_usb_disk() {
    local requested=$1 expected_identity=$2 device type rm_flag transport ro_flag
    local root_dev root_disk iso_dev iso_disk actual_identity node target swap_node swap_disk
    [[ "$requested" =~ ^/dev/[A-Za-z0-9._+-]+$ ]] || fail usb_device_invalid
    [[ "$expected_identity" =~ ^k1:[0-9a-f]{64}$ || "$expected_identity" =~ ^[0-9a-f]{64}$ ]] || fail usb_device_invalid
    device=$(/usr/bin/readlink -f -- "$requested" 2>/dev/null) || fail usb_device_invalid
    [[ "$device" == /dev/* && -b "$device" ]] || fail usb_device_invalid

    read -r type rm_flag transport ro_flag < <(
        /usr/bin/lsblk -dnro TYPE,RM,TRAN,RO -- "$device" 2>/dev/null | /usr/bin/head -n1
    ) || fail usb_device_invalid
    [[ "$type" == disk && "$ro_flag" == 0 ]] || fail usb_device_invalid
    [[ "$rm_flag" == 1 || "$transport" == usb ]] || fail usb_device_invalid

    verify_usb_identity "$device" "$expected_identity"

    root_dev=$(root_source 2>/dev/null || true)
    root_disk=$(top_disk_for_node "$root_dev" 2>/dev/null || true)
    [[ -z "$root_disk" || "$device" != "$root_disk" ]] || fail usb_device_invalid

    if [[ -e "$ISO" ]]; then
        iso_dev=$(iso_source 2>/dev/null || true)
        iso_disk=$(top_disk_for_node "$iso_dev" 2>/dev/null || true)
        [[ -z "$iso_disk" || "$device" != "$iso_disk" ]] || fail usb_device_invalid
    fi

    # Ordinary desktop automounts are safe to unmount before writing.  Any
    # other mount (for example /home, /boot or a service mount) means that the
    # selected disk participates in the running system and must never be wiped.
    while IFS= read -r node; do
        [[ -n "$node" ]] || continue
        while IFS= read -r target; do
            [[ -n "$target" ]] || continue
            case "$target" in
                /run/media/*|/media/*|/mnt/*) ;;
                *) fail usb_device_system_mount ;;
            esac
        done < <(/usr/bin/findmnt -rn -S "$node" -o TARGET 2>/dev/null || true)
    done < <(/usr/bin/lsblk -nrpo NAME -- "$device" 2>/dev/null)

    while IFS= read -r swap_node; do
        [[ -n "$swap_node" ]] || continue
        swap_disk=$(top_disk_for_node "$swap_node" 2>/dev/null || true)
        [[ -z "$swap_disk" || "$swap_disk" != "$device" ]] || fail usb_device_swap
    done < <(/usr/bin/swapon --show=NAME --noheadings --raw 2>/dev/null || true)

    VALIDATED_USB_DEVICE=$device
}
unmount_usb_disk() {
    local device=$1 node target
    local -a nodes=()
    mapfile -t nodes < <(/usr/bin/lsblk -nrpo NAME -- "$device" 2>/dev/null | /usr/bin/tac)
    for node in "${nodes[@]}"; do
        while IFS= read -r target; do
            [[ -n "$target" ]] || continue
            /usr/bin/umount -- "$target" || fail usb_device_busy
        done < <(/usr/bin/findmnt -rn -S "$node" -o TARGET 2>/dev/null || true)
    done
}
usb_matches_iso() {
    local device=$1 iso_size expected actual
    cache_valid || return 1
    iso_size=$(/usr/bin/stat -c %s -- "$ISO" 2>/dev/null) || return 1
    expected=$(iso_hash 2>/dev/null) || return 1
    actual=$(/usr/bin/head -c "$iso_size" -- "$device" 2>/dev/null \
        | /usr/bin/sha256sum | /usr/bin/awk '{print $1}') || return 1
    [[ "$actual" == "$expected" ]]
}
find_usb_esp() {
    local device=$1 node parttype normalized
    while read -r node parttype; do
        [[ -n "$node" ]] || continue
        normalized=${parttype,,}
        # Archiso hybrid media exposes the UEFI El Torito image as an MBR
        # partition of type 0xEF. A normally partitioned GPT stick instead uses
        # the standard EFI System Partition GUID. Accept both representations.
        if [[ "$normalized" == "$ESP_PARTTYPE" \
            || "$normalized" == 0xef \
            || "$normalized" == ef ]]; then
            printf '%s' "$node"
            return 0
        fi
    done < <(/usr/bin/lsblk -nrpo NAME,PARTTYPE -- "$device" 2>/dev/null | /usr/bin/tail -n +2)
    return 1
}
wait_for_usb_esp() {
    local device=$1 esp attempt
    for attempt in {1..40}; do
        esp=$(find_usb_esp "$device" 2>/dev/null || true)
        if [[ -n "$esp" && -b "$esp" ]]; then
            printf '%s' "$esp"
            return 0
        fi
        /usr/bin/blockdev --rereadpt "$device" >/dev/null 2>&1 || true
        /usr/bin/udevadm settle --timeout=1 >/dev/null 2>&1 || true
        /usr/bin/sleep 0.25
    done
    return 1
}
usb_boot_entries() {
    # Parse only entries created by Arch Manager.  Do not depend on the exact
    # amount of whitespace emitted by efibootmgr.
    /usr/bin/efibootmgr 2>/dev/null | /usr/bin/awk -v label="$USB_BOOT_LABEL" '
        /^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]\*?[[:space:]]+/ {
            token=$1
            id=substr(token,5,4)
            line=$0
            sub(/^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]\*?[[:space:]]+/, "", line)
            sub(/[[:space:]]+$/, "", line)
            if (line == label) print id
        }
    '
}
bootnext_value() {
    /usr/bin/efibootmgr 2>/dev/null | /usr/bin/awk '
        /^BootNext:[[:space:]]+[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]$/ {
            print $2
            exit
        }
    '
}
prepare_usb_boot_log() {
    /usr/bin/install -d -o root -g root -m 0755 /var/log/arch-manager || return 1
    : >"$USB_BOOT_LOG" || return 1
    /usr/bin/chmod 0600 "$USB_BOOT_LOG" || return 1
}
log_efibootmgr() {
    {
        printf '\n$ efibootmgr'
        printf ' %q' "$@"
        printf '\n'
    } >>"$USB_BOOT_LOG"
    /usr/bin/efibootmgr "$@" >>"$USB_BOOT_LOG" 2>&1
}
entry_matches_usb() {
    local bootnum=$1 device=$2 partnum=$3 pttype ptuuid partuuid line expected
    [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || return 1
    pttype=$(/usr/bin/lsblk -dnro PTTYPE -- "$device" 2>/dev/null | /usr/bin/head -n1)
    line=$(/usr/bin/efibootmgr -v 2>/dev/null | /usr/bin/grep -i -m1 "^Boot${bootnum}\\*\\?[[:space:]]" || true)
    [[ -n "$line" ]] || return 1
    line=${line,,}
    case "${pttype,,}" in
        dos)
            ptuuid=$(/usr/bin/lsblk -dnro PTUUID -- "$device" 2>/dev/null | /usr/bin/head -n1)
            ptuuid=${ptuuid#0x}
            [[ -n "$ptuuid" ]] || return 1
            expected="hd(${partnum},mbr,0x${ptuuid,,},"
            ;;
        gpt)
            partuuid=$(/usr/bin/lsblk -dnro PARTUUID -- "$(wait_for_usb_esp "$device")" 2>/dev/null | /usr/bin/head -n1)
            [[ -n "$partuuid" ]] || return 1
            expected="hd(${partnum},gpt,${partuuid,,},"
            ;;
        *)
            return 1
            ;;
    esac
    [[ "$line" == *"$expected"* && "$line" == *'\efi\boot\bootx64.efi'* ]]
}
matching_usb_boot_entries() {
    local device=$1 partnum=$2 bootnum
    while IFS= read -r bootnum; do
        [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || continue
        if entry_matches_usb "$bootnum" "$device" "$partnum"; then
            printf '%s\n' "$bootnum"
        fi
    done < <(usb_boot_entries)
}
set_bootnext_verified() {
    local bootnum=$1 current
    [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || return 1
    log_efibootmgr -n "$bootnum" || return 1
    current=$(bootnext_value)
    [[ "${current^^}" == "${bootnum^^}" ]]
}
all_boot_ids() {
    /usr/bin/efibootmgr 2>/dev/null | /usr/bin/sed -nE 's/^Boot([0-9A-Fa-f]{4})\*?[[:space:]].*/\1/p'
}
new_boot_id_after_create() {
    local before=$1 bootnum
    while IFS= read -r bootnum; do
        [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || continue
        if ! /usr/bin/grep -Fqx -- "$bootnum" <<< "$before"; then
            printf '%s' "$bootnum"
            return 0
        fi
    done < <(all_boot_ids)
    return 1
}
create_usb_boot_entry() {
    local device=$1 partnum=$2 before bootnum
    before=$(all_boot_ids)

    # The ISO already carries a valid MBR signature.  Do not use efibootmgr -w:
    # it is allowed to write a new signature and would invalidate the exact
    # SHA-256 image verification performed by Arch Manager.
    if log_efibootmgr -C -d "$device" -p "$partnum" -L "$USB_BOOT_LABEL" \
        -l '\EFI\BOOT\BOOTX64.EFI'; then
        bootnum=$(new_boot_id_after_create "$before" 2>/dev/null || true)
        if [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]]; then
            printf '%s' "$bootnum"
            return 0
        fi
    fi
    return 1
}
cleanup_usb_boot_entries_except() {
    local keep=${1:-} bootnum
    while IFS= read -r bootnum; do
        [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || continue
        [[ -n "$keep" && "${bootnum^^}" == "${keep^^}" ]] && continue
        log_efibootmgr -b "$bootnum" -B || true
    done < <(usb_boot_entries)
}
generic_usb_boot_entry() {
    # Some firmware exposes a built-in one-shot "EFI USB Device" entry
    # (commonly Boot2001).  It is a safe fallback only when the selected
    # Recovery flash drive is the sole USB/removable whole disk.
    local device=$1 count bootnum
    count=$(/usr/bin/lsblk -dnro TYPE,TRAN,RM 2>/dev/null | /usr/bin/awk '
        $1 == "disk" && ($2 == "usb" || $3 == "1") { count++ }
        END { print count + 0 }
    ')
    [[ "$count" == 1 ]] || return 1
    bootnum=$(/usr/bin/efibootmgr 2>/dev/null | /usr/bin/awk '
        /^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]\*?[[:space:]]+/ {
            line=tolower($0)
            if (line ~ /efi usb device/ || line ~ /usb device/) {
                id=substr($1,5,4)
                print id
                exit
            }
        }
    ')
    [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] || return 1
    printf '%s' "$bootnum"
}
USB_DD_PID=''
USB_VERIFY_DD_PID=''
USB_VERIFY_SHA_PID=''
USB_VERIFY_DIR=''
cleanup_usb_create() {
    local pid
    for pid in "$USB_DD_PID" "$USB_VERIFY_DD_PID" "$USB_VERIFY_SHA_PID"; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        /usr/bin/kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    if [[ -n "$USB_VERIFY_DIR" && "$USB_VERIFY_DIR" == /run/arch-manager/recovery-verify.* ]]; then
        /usr/bin/rm -rf -- "$USB_VERIFY_DIR"
    fi
    USB_DD_PID=''
    USB_VERIFY_DD_PID=''
    USB_VERIFY_SHA_PID=''
    USB_VERIFY_DIR=''
}
abort_usb_create() {
    local status=$1
    trap - EXIT INT TERM HUP
    cleanup_usb_create
    exit "$status"
}
create_recovery_usb() {
    [[ $# -eq 2 ]] || fail usb_device_invalid
    require_root
    ensure_usb_runtime_dir || fail usb_write_failed
    trap cleanup_usb_create EXIT
    trap 'abort_usb_create 130' INT
    trap 'abort_usb_create 143' TERM HUP

    set_usb_progress 2 "2.0% — Проверяю систему и Recovery-профиль…"
    check_layout
    set_usb_progress 5 "5.0% — Подготавливаю актуальный Recovery-образ…"
    build_iso_if_stale
    cache_valid || fail iso_missing

    local requested_identity=$2 device iso_size device_size expected actual
    local dd_pid dd_status bytes tenths display
    local verify_fifo verify_hash verify_dd_pid verify_sha_pid verify_dd_status verify_sha_status

    validate_usb_disk "$1" "$requested_identity"
    device=$VALIDATED_USB_DEVICE
    iso_size=$(/usr/bin/stat -c %s -- "$ISO") || fail iso_missing
    device_size=$(/usr/bin/blockdev --getsize64 "$device" 2>/dev/null) || fail usb_device_invalid
    [[ "$device_size" =~ ^[0-9]+$ && "$device_size" -ge "$iso_size" ]] || fail usb_device_too_small

    set_usb_progress 15 "15.0% — Recovery-образ готов. Подготавливаю флешку…"
    unmount_usb_disk "$device"
    # Close the remaining selection-to-write race as much as a shell helper can:
    # re-check the physical fingerprint after unmounting and immediately before dd.
    validate_usb_disk "$device" "$requested_identity"
    device=$VALIDATED_USB_DEVICE
    # Final kernel-object check immediately before opening the destructive
    # output path. This catches a detach/re-attach after unmounting without
    # relying on udev presentation fields such as MODEL/SERIAL/TRAN.
    verify_usb_identity "$device" "$requested_identity"

    # Direct I/O is intentional here. The old buffered implementation watched
    # /proc/$pid/io:wchar, which measures bytes accepted from dd by the kernel,
    # not bytes actually sent to the USB device. That made the GUI jump to
    # 79.9% and then wait for a long final fsync. With O_DIRECT + write_bytes,
    # the heavy write stage follows real block-device I/O much more closely.
    set_usb_progress 20 "20.0% — Записываю Recovery-образ на флешку…"
    /usr/bin/dd if="$ISO" of="$device" bs=4M oflag=direct conv=fsync status=none &
    dd_pid=$!
    USB_DD_PID=$dd_pid

    while /usr/bin/kill -0 "$dd_pid" 2>/dev/null; do
        /usr/bin/sleep 0.25
        [[ -r "/proc/$dd_pid/io" ]] || continue
        bytes=$(/usr/bin/awk '$1 == "write_bytes:" {print $2; exit}' "/proc/$dd_pid/io" 2>/dev/null || true)
        [[ "$bytes" =~ ^[0-9]+$ ]] || continue
        (( bytes > iso_size )) && bytes=$iso_size

        # Real USB write occupies 20.0–79.0%. A small tail is reserved for the
        # device flush performed by dd/conv=fsync before the process exits.
        tenths=$((200 + (bytes * 590 / iso_size)))
        (( tenths > 790 )) && tenths=790
        display=$(/usr/bin/awk -v p="$tenths" 'BEGIN { printf "%.1f", p / 10 }')
        set_usb_progress_tenths "$tenths" \
            "${display}% — Записываю Recovery-образ на флешку…"
    done

    if wait "$dd_pid"; then
        dd_status=0
    else
        dd_status=$?
    fi
    USB_DD_PID=''
    (( dd_status == 0 )) || fail usb_write_failed

    set_usb_progress 80 "80.0% — Запись завершена. Синхронизирую устройство…"
    /usr/bin/sync
    /usr/bin/blockdev --flushbufs "$device" >/dev/null 2>&1 || true
    /usr/bin/blockdev --rereadpt "$device" >/dev/null 2>&1 || true
    /usr/bin/udevadm settle --timeout=10 >/dev/null 2>&1 || true

    set_usb_progress 83 "83.0% — Подготавливаю контрольную SHA-256 проверку…"
    expected=$(iso_hash) || fail usb_verify_failed

    # Read the just-written bytes back from the USB device with O_DIRECT and
    # hash that stream. This makes the verification stage measurable too,
    # instead of leaving the GUI frozen at one percentage for minutes.
    USB_VERIFY_DIR=$(/usr/bin/mktemp -d /run/arch-manager/recovery-verify.XXXXXX) || fail usb_verify_failed
    verify_fifo="$USB_VERIFY_DIR/stream.fifo"
    verify_hash="$USB_VERIFY_DIR/result.sha256"
    /usr/bin/mkfifo -m 0600 "$verify_fifo" || fail usb_verify_failed

    /usr/bin/sha256sum <"$verify_fifo" >"$verify_hash" &
    verify_sha_pid=$!
    USB_VERIFY_SHA_PID=$verify_sha_pid
    /usr/bin/dd if="$device" of="$verify_fifo" bs=4M iflag=direct,count_bytes \
        count="$iso_size" status=none &
    verify_dd_pid=$!
    USB_VERIFY_DD_PID=$verify_dd_pid

    set_usb_progress 85 "85.0% — Проверяю записанную флешку по SHA-256…"
    while /usr/bin/kill -0 "$verify_dd_pid" 2>/dev/null; do
        /usr/bin/sleep 0.25
        [[ -r "/proc/$verify_dd_pid/io" ]] || continue
        bytes=$(/usr/bin/awk '$1 == "read_bytes:" {print $2; exit}' "/proc/$verify_dd_pid/io" 2>/dev/null || true)
        [[ "$bytes" =~ ^[0-9]+$ ]] || continue
        (( bytes > iso_size )) && bytes=$iso_size

        # Read-back verification occupies 85.0–97.5%.
        tenths=$((850 + (bytes * 125 / iso_size)))
        (( tenths > 975 )) && tenths=975
        display=$(/usr/bin/awk -v p="$tenths" 'BEGIN { printf "%.1f", p / 10 }')
        set_usb_progress_tenths "$tenths" \
            "${display}% — Проверяю записанную флешку по SHA-256…"
    done

    if wait "$verify_dd_pid"; then
        verify_dd_status=0
    else
        verify_dd_status=$?
    fi
    if wait "$verify_sha_pid"; then
        verify_sha_status=0
    else
        verify_sha_status=$?
    fi

    USB_VERIFY_DD_PID=''
    USB_VERIFY_SHA_PID=''
    actual=$(/usr/bin/awk '{print $1; exit}' "$verify_hash" 2>/dev/null || true)
    /usr/bin/rm -rf -- "$USB_VERIFY_DIR"
    USB_VERIFY_DIR=''

    (( verify_dd_status == 0 && verify_sha_status == 0 )) || fail usb_verify_failed
    [[ "$actual" == "$expected" ]] || fail usb_verify_failed

    set_usb_progress 98 "98.0% — Контрольная проверка пройдена. Завершаю…"
    /usr/bin/sync
    set_usb_progress 100 "100.0% — Recovery-флешка записана и проверена."
    trap - EXIT INT TERM HUP
    cleanup_usb_create
    printf 'OK\tusb-written\t%s\n' "$device"
}
reboot_recovery_usb() {
    [[ $# -eq 2 ]] || fail usb_device_invalid
    require_root
    [[ -d /sys/firmware/efi/efivars && -x /usr/bin/efibootmgr ]] || fail usb_uefi_unavailable
    cache_valid || fail stale_preparation

    local device esp partnum bootnum generic created_bootnum=''
    local requested_identity=$2
    validate_usb_disk "$1" "$requested_identity"
    device=$VALIDATED_USB_DEVICE
    usb_matches_iso "$device" || fail usb_image_mismatch
    validate_usb_disk "$device" "$requested_identity"
    device=$VALIDATED_USB_DEVICE
    /usr/bin/blockdev --rereadpt "$device" >/dev/null 2>&1 || true
    /usr/bin/udevadm settle --timeout=10 >/dev/null 2>&1 || true
    esp=$(wait_for_usb_esp "$device") || fail usb_esp_missing
    partnum=$(/usr/bin/lsblk -dnro PARTN -- "$esp" 2>/dev/null | /usr/bin/head -n1)
    [[ "$partnum" =~ ^[1-9][0-9]*$ ]] || fail usb_esp_missing
    prepare_usb_boot_log || fail usb_boot_entry_failed

    {
        printf 'Arch Manager Recovery USB BootNext diagnostics\n'
        printf 'device=%s\nesp=%s\npartnum=%s\n' "$device" "$esp" "$partnum"
        /usr/bin/efibootmgr -v 2>&1 || true
    } >>"$USB_BOOT_LOG"

    # Prefer the firmware's own removable-media path when it is available.
    # Insyde and some other firmware can accept a custom HD()/File() BootNext
    # entry but fail it at boot with a "Boot File"/"Boot Failed" screen, while
    # the same stick boots correctly through the built-in "EFI USB Device"
    # entry or the F12 menu. With exactly one USB/removable disk attached the
    # generic entry is unambiguous and follows the UEFI removable path.
    generic=$(generic_usb_boot_entry "$device" 2>/dev/null || true)
    if [[ "$generic" =~ ^[0-9A-Fa-f]{4}$ ]] && set_bootnext_verified "$generic"; then
        bootnum=$generic
        cleanup_usb_boot_entries_except ""
    else
        # Firmware without a usable generic USB entry falls back to an exact
        # Arch Manager entry for this USB ESP. Reuse it before creating a new
        # NVRAM variable, and never touch the persistent BootOrder.
        bootnum=$(matching_usb_boot_entries "$device" "$partnum" | /usr/bin/head -n1)
        if [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] && set_bootnext_verified "$bootnum"; then
            cleanup_usb_boot_entries_except "$bootnum"
        else
            bootnum=$(create_usb_boot_entry "$device" "$partnum" 2>/dev/null || true)
            created_bootnum=$bootnum
            if [[ "$bootnum" =~ ^[0-9A-Fa-f]{4}$ ]] && set_bootnext_verified "$bootnum"; then
                cleanup_usb_boot_entries_except "$bootnum"
            else
                if [[ "$created_bootnum" =~ ^[0-9A-Fa-f]{4}$ ]]; then
                    log_efibootmgr -b "$created_bootnum" -B || true
                fi
                /usr/bin/efibootmgr -N >>"$USB_BOOT_LOG" 2>&1 || true
                fail usb_boot_entry_failed
            fi
        fi
    fi

    printf 'selected_bootnext=%s\n' "$bootnum" >>"$USB_BOOT_LOG"
    if ! /usr/bin/systemctl reboot; then
        /usr/bin/efibootmgr -N >>"$USB_BOOT_LOG" 2>&1 || true
        fail usb_boot_entry_failed
    fi
    printf 'OK\tusb-rebooting\t%s\n' "$device"
}
