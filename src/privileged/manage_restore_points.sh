#!/usr/bin/env bash
set -euo pipefail

# Root-owned Stage 4 helper. It accepts only the fixed restore-point and policy
# actions implemented below. No command names or paths are accepted from GUI.
export PATH=/usr/bin:/bin
if /usr/bin/locale -a 2>/dev/null | /usr/bin/grep -qx 'C\.utf8'; then
    export LC_ALL=C.UTF-8
else
    export LC_ALL=C
fi
export LANG="$LC_ALL"
umask 077

SNAPPER=/usr/bin/snapper
BTRFS=/usr/bin/btrfs
PYTHON=/usr/bin/python
SYSTEMCTL=/usr/bin/systemctl
MAX_DESCRIPTION_LENGTH=300

fail() {
    printf 'Arch Manager restore-point helper: %s\n' "$*" >&2
    exit 2
}

require_root() {
    (( EUID == 0 )) || fail 'administrator privileges are required'
}

require_runtime() {
    [[ -x "$SNAPPER" ]] || fail 'snapper is not installed'
    [[ -x "$BTRFS" ]] || fail 'btrfs is not available'
    [[ -x "$PYTHON" ]] || fail 'python is not available'
    [[ -r /etc/snapper/configs/root ]] || fail 'Snapper root configuration is not available'
}

verify_created_snapshot() {
    local point_number="$1"
    local snapshot="/.snapshots/$point_number/snapshot"
    local readonly

    [[ -d "$snapshot" && ! -L "$snapshot" ]] || return 1
    "$BTRFS" subvolume show "$snapshot" >/dev/null 2>&1 || return 1
    [[ -r "/.snapshots/$point_number/info.xml" ]] || return 1
    readonly=$("$BTRFS" property get -ts "$snapshot" ro 2>/dev/null || true)
    [[ "$readonly" == 'ro=true' ]]
}

require_policy_runtime() {
    require_runtime
    [[ -x "$SYSTEMCTL" ]] || fail 'systemctl is not available'
}

validate_point_number() {
    local value="${1:-}"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail 'restore-point number must be a positive integer'
}

validate_importance() {
    case "${1:-}" in
        yes|no) ;;
        *) fail 'importance must be yes or no' ;;
    esac
}

validate_yes_no() {
    case "${1:-}" in
        yes|no) ;;
        *) fail 'flag must be yes or no' ;;
    esac
}

validate_description() {
    local value="${1-}"

    [[ -n "$value" ]] || fail 'description must not be empty'
    (( ${#value} <= MAX_DESCRIPTION_LENGTH )) || fail "description must be at most ${MAX_DESCRIPTION_LENGTH} characters"

    if printf '%s' "$value" | LC_ALL=C /usr/bin/grep -q '[[:cntrl:]]'; then
        fail 'description contains control characters'
    fi

    [[ -n "${value// /}" ]] || fail 'description must not be blank'
}

snapshot_userdata_with_importance() {
    local point_number="$1"
    local wanted="$2"
    local json

    json="$($SNAPPER -c root --jsonout list --columns number,userdata)" || fail 'could not read Snapper userdata'

    printf '%s' "$json" | "$PYTHON" -c '
import json
import sys

point_number = int(sys.argv[1])
wanted = sys.argv[2]
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(10)

rows = data.get("root")
if not isinstance(rows, list):
    raise SystemExit(11)

for row in rows:
    if not isinstance(row, dict) or row.get("number") != point_number:
        continue
    userdata = row.get("userdata") or {}
    if not isinstance(userdata, dict):
        raise SystemExit(12)
    clean = {}
    for key, value in userdata.items():
        key = str(key)
        value = str(value)
        if not key or "," in key or "=" in key or "," in value:
            raise SystemExit(13)
        clean[key] = value
    clean["important"] = wanted
    sys.stdout.write(",".join(f"{key}={value}" for key, value in clean.items()))
    raise SystemExit(0)

raise SystemExit(14)
' "$point_number" "$wanted" || fail 'could not safely preserve existing Snapper userdata'
}

run_create() {
    [[ $# -eq 2 ]] || fail 'create expects: create <yes|no> <description>'
    local important="$1"
    local description="$2"
    local id

    validate_importance "$important"
    validate_description "$description"
    require_root
    require_runtime

    if [[ "$important" == yes ]]; then
        id="$($SNAPPER -c root create \
            --type single \
            --cleanup-algorithm number \
            --description "$description" \
            --userdata important=yes \
            --print-number)" || fail 'Snapper could not create the restore point'
    else
        id="$($SNAPPER -c root create \
            --type single \
            --cleanup-algorithm number \
            --description "$description" \
            --print-number)" || fail 'Snapper could not create the restore point'
    fi

    [[ "$id" =~ ^[1-9][0-9]*$ ]] || fail 'Snapper returned an invalid restore-point number'
    if ! verify_created_snapshot "$id"; then
        # A point that cannot be proven to be a real immutable Btrfs snapshot is
        # not offered as a successful recovery point. Remove the incomplete point
        # best-effort so the UI cannot later present it as usable.
        "$SNAPPER" -c root delete "$id" >/dev/null 2>&1 || true
        fail 'Snapper created a restore point that failed the safety verification'
    fi
    printf 'OK\tcreated\t%s\n' "$id"
}

run_rename() {
    [[ $# -eq 2 ]] || fail 'rename expects: rename <number> <description>'
    local point_number="$1"
    local description="$2"

    validate_point_number "$point_number"
    validate_description "$description"
    require_root
    require_runtime

    "$SNAPPER" -c root modify --description "$description" "$point_number" >/dev/null \
        || fail 'Snapper could not rename the restore point'
    printf 'OK\trenamed\t%s\n' "$point_number"
}

run_set_importance() {
    [[ $# -eq 2 ]] || fail 'set-importance expects: set-importance <number> <yes|no>'
    local point_number="$1"
    local wanted="$2"
    local userdata

    validate_point_number "$point_number"
    validate_importance "$wanted"
    require_root
    require_runtime

    userdata="$(snapshot_userdata_with_importance "$point_number" "$wanted")"
    "$SNAPPER" -c root modify --userdata "$userdata" "$point_number" >/dev/null \
        || fail 'Snapper could not change restore-point importance'
    printf 'OK\timportance\t%s\t%s\n' "$point_number" "$wanted"
}

run_delete() {
    [[ $# -eq 1 ]] || fail 'delete expects: delete <number>'
    local point_number="$1"

    validate_point_number "$point_number"
    require_root
    require_runtime

    "$SNAPPER" -c root delete "$point_number" >/dev/null \
        || fail 'Snapper could not delete the restore point'
    printf 'OK\tdeleted\t%s\n' "$point_number"
}

run_delete_many() {
    (( $# >= 1 && $# <= 200 )) || fail 'delete-many expects one to 200 snapshot numbers'
    local point_number joined="" separator=""

    for point_number in "$@"; do
        validate_point_number "$point_number"
    done
    require_root
    require_runtime

    for point_number in "$@"; do
        "$SNAPPER" -c root delete "$point_number" >/dev/null \
            || fail 'Snapper could not delete all selected restore points'
        joined+="${separator}${point_number}"
        separator=,
    done
    printf 'OK\tdeleted-many\t%s\n' "$joined"
}

run_apply_recommended_policy() {
    [[ $# -eq 0 ]] || fail 'policy-apply-recommended does not accept arguments'
    require_root
    require_policy_runtime

    # Keep the user's timeline on/off choice intact. This command only applies
    # retention/cleanup limits and enables the cleanup timer.
    "$SNAPPER" -c root set-config \
        'NUMBER_CLEANUP=yes' \
        'NUMBER_MIN_AGE=1800' \
        'NUMBER_LIMIT=10' \
        'NUMBER_LIMIT_IMPORTANT=5' \
        'TIMELINE_CLEANUP=yes' \
        'TIMELINE_MIN_AGE=1800' \
        'TIMELINE_LIMIT_HOURLY=5' \
        'TIMELINE_LIMIT_DAILY=7' \
        'TIMELINE_LIMIT_WEEKLY=4' \
        'TIMELINE_LIMIT_MONTHLY=3' \
        'TIMELINE_LIMIT_YEARLY=0' >/dev/null \
        || fail 'Snapper could not apply the recommended retention policy'

    "$SYSTEMCTL" enable --now snapper-cleanup.timer >/dev/null 2>&1 \
        || fail 'could not enable the Snapper cleanup timer'

    printf 'OK\tpolicy\trecommended\n'
}

run_set_timeline() {
    [[ $# -eq 1 ]] || fail 'timeline-set expects: timeline-set <yes|no>'
    local enabled="$1"

    validate_yes_no "$enabled"
    require_root
    require_policy_runtime

    if [[ "$enabled" == yes ]]; then
        "$SNAPPER" -c root set-config \
            'TIMELINE_CREATE=yes' \
            'TIMELINE_CLEANUP=yes' \
            'TIMELINE_MIN_AGE=1800' \
            'TIMELINE_LIMIT_HOURLY=5' \
            'TIMELINE_LIMIT_DAILY=7' \
            'TIMELINE_LIMIT_WEEKLY=4' \
            'TIMELINE_LIMIT_MONTHLY=3' \
            'TIMELINE_LIMIT_YEARLY=0' >/dev/null \
            || fail 'Snapper could not enable timeline snapshots'
        "$SYSTEMCTL" enable --now snapper-cleanup.timer >/dev/null 2>&1 \
            || fail 'could not enable the Snapper cleanup timer'
        "$SYSTEMCTL" enable --now snapper-timeline.timer >/dev/null 2>&1 \
            || fail 'could not enable the Snapper timeline timer'
    else
        "$SNAPPER" -c root set-config 'TIMELINE_CREATE=no' >/dev/null \
            || fail 'Snapper could not disable timeline snapshots'
        "$SYSTEMCTL" disable --now snapper-timeline.timer >/dev/null 2>&1 \
            || fail 'could not disable the Snapper timeline timer'
    fi

    printf 'OK\ttimeline\t%s\n' "$enabled"
}

validate_policy_integer() {
    local value="${1:-}" maximum="${2:-86400000}"
    [[ "$value" =~ ^(0|[1-9][0-9]{0,7})$ ]] \
        || fail 'policy value is not a canonical non-negative integer'
    (( 10#$value <= maximum )) || fail 'policy value exceeds safe maximum'
}

run_restore_saved_policy() {
    [[ $# -eq 14 ]] || fail 'policy-restore expects 12 Snapper values and two timer flags'
    local number_cleanup="$1" number_min_age="$2" number_limit="$3"
    local important_limit="$4" timeline_create="$5" timeline_cleanup="$6"
    local timeline_min_age="$7" hourly="$8" daily="$9"
    local weekly="${10}" monthly="${11}" yearly="${12}"
    local cleanup_timer="${13}" timeline_timer="${14}"
    local backup_dir='/var/lib/arch-manager/settings-policy-backups'
    local backup_file

    validate_yes_no "$number_cleanup"
    validate_yes_no "$timeline_create"
    validate_yes_no "$timeline_cleanup"
    validate_yes_no "$cleanup_timer"
    validate_yes_no "$timeline_timer"
    local value
    for value in "$number_min_age" "$number_limit" "$important_limit" \
        "$timeline_min_age" "$hourly" "$daily" "$weekly" "$monthly" "$yearly"; do
        validate_policy_integer "$value"
    done
    require_root
    require_policy_runtime

    # Preserve the previous root config before any privileged changes.
    /usr/bin/install -d -o root -g root -m 0700 "$backup_dir"
    backup_file="$(/usr/bin/mktemp "$backup_dir/snapper-root-$(/usr/bin/date +%Y%m%d-%H%M%S)-XXXXXXXX.conf")"
    /usr/bin/cp -- /etc/snapper/configs/root "$backup_file"
    /usr/bin/chmod 0600 "$backup_file"

    "$SNAPPER" -c root set-config \
        "NUMBER_CLEANUP=$number_cleanup" \
        "NUMBER_MIN_AGE=$number_min_age" \
        "NUMBER_LIMIT=$number_limit" \
        "NUMBER_LIMIT_IMPORTANT=$important_limit" \
        "TIMELINE_CREATE=$timeline_create" \
        "TIMELINE_CLEANUP=$timeline_cleanup" \
        "TIMELINE_MIN_AGE=$timeline_min_age" \
        "TIMELINE_LIMIT_HOURLY=$hourly" \
        "TIMELINE_LIMIT_DAILY=$daily" \
        "TIMELINE_LIMIT_WEEKLY=$weekly" \
        "TIMELINE_LIMIT_MONTHLY=$monthly" \
        "TIMELINE_LIMIT_YEARLY=$yearly" >/dev/null \
        || fail 'Snapper could not restore the saved policy'

    if [[ "$cleanup_timer" == yes ]]; then
        "$SYSTEMCTL" enable --now snapper-cleanup.timer >/dev/null 2>&1 \
            || fail 'could not enable saved cleanup timer'
    else
        "$SYSTEMCTL" disable --now snapper-cleanup.timer >/dev/null 2>&1 \
            || fail 'could not disable saved cleanup timer'
    fi
    if [[ "$timeline_timer" == yes ]]; then
        "$SYSTEMCTL" enable --now snapper-timeline.timer >/dev/null 2>&1 \
            || fail 'could not enable saved timeline timer'
    else
        "$SYSTEMCTL" disable --now snapper-timeline.timer >/dev/null 2>&1 \
            || fail 'could not disable saved timeline timer'
    fi
    printf 'OK\tpolicy\trestored\n'
}

self_test() {
    validate_point_number 1
    validate_point_number 99999
    validate_importance yes
    validate_importance no
    validate_yes_no yes
    validate_yes_no no
    validate_description 'Arch Manager helper self-test'
    validate_policy_integer 0
    validate_policy_integer 86400000
    validate_policy_integer 10000 10000

    case "$(printf '%s' 'one two three' | /usr/bin/wc -w)" in
        *3) ;;
        *) fail 'self-test failed' ;;
    esac

    printf 'OK\n'
}

case "${1:-}" in
    --self-test)
        [[ $# -eq 1 ]] || fail '--self-test does not accept arguments'
        self_test
        ;;
    create)
        shift
        run_create "$@"
        ;;
    rename)
        shift
        run_rename "$@"
        ;;
    set-importance)
        shift
        run_set_importance "$@"
        ;;
    delete)
        shift
        run_delete "$@"
        ;;
    delete-many)
        shift
        run_delete_many "$@"
        ;;
    policy-apply-recommended)
        shift
        run_apply_recommended_policy "$@"
        ;;
    policy-restore)
        shift
        run_restore_saved_policy "$@"
        ;;
    timeline-set)
        shift
        run_set_timeline "$@"
        ;;
    '')
        fail 'missing action'
        ;;
    *)
        fail 'unsupported action'
        ;;
esac
