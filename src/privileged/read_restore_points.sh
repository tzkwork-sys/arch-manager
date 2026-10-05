#!/usr/bin/env bash
set -euo pipefail

# Root-owned helper installed by Arch Manager.
# Read-only jobs only: restore-point metadata/fingerprint or Snapper policy state.
export PATH=/usr/bin:/bin
export LC_ALL=C
export LANG=C

SNAPPER=/usr/bin/snapper
PYTHON=/usr/bin/python3
SYSTEMCTL=/usr/bin/systemctl

fail() {
    printf 'Arch Manager read helper: %s\n' "$*" >&2
    exit 64
}

require_common_runtime() {
    [[ -x "$SNAPPER" ]] || { printf 'snapper is not installed.\n' >&2; exit 127; }
}

read_points() {
    require_common_runtime
    "$SNAPPER" \
        -c root \
        --csvout \
        --no-headers \
        --separator '|' \
        --iso \
        list \
        --disable-used-space \
        --columns 'number,date,user,cleanup,description,userdata,pre-number'
}

read_fingerprint() {
    require_common_runtime
    local digest
    digest=$({
        if [[ -e /etc/snapper/configs/root ]]; then
            /usr/bin/stat -Lc 'config:%d:%i:%s:%Y:%f' /etc/snapper/configs/root 2>/dev/null || true
        else
            printf 'config:missing\n'
        fi
        if [[ -d /.snapshots ]]; then
            /usr/bin/find /.snapshots -mindepth 1 -maxdepth 2 \
                \( -type f -name info.xml -o -type d -name snapshot \) \
                -printf '%y:%P:%s:%T@:%m\n' 2>/dev/null | /usr/bin/sort
        else
            printf 'snapshots:missing\n'
        fi
    } | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')
    printf 'version=1\nfingerprint=%s\n' "$digest"
}

read_policy() {
    require_common_runtime
    [[ -x "$PYTHON" ]] || { printf 'python3 is not installed.\n' >&2; exit 127; }
    [[ -x "$SYSTEMCTL" ]] || { printf 'systemctl is not installed.\n' >&2; exit 127; }

    if [[ ! -r /etc/snapper/configs/root ]]; then
        printf '{"configured":false}\n'
        return 0
    fi

    local config_file cleanup_enabled timeline_enabled
    config_file="$(mktemp)"
    trap 'rm -f -- "$config_file"' RETURN

    "$SNAPPER" \
        -c root \
        --csvout \
        --no-headers \
        --separator '|' \
        get-config \
        --columns 'key,value' >"$config_file" \
        || { rm -f -- "$config_file"; trap - RETURN; exit 1; }

    cleanup_enabled=no
    timeline_enabled=no
    if "$SYSTEMCTL" is-enabled --quiet snapper-cleanup.timer 2>/dev/null; then
        cleanup_enabled=yes
    fi
    if "$SYSTEMCTL" is-enabled --quiet snapper-timeline.timer 2>/dev/null; then
        timeline_enabled=yes
    fi

    "$PYTHON" - "$config_file" "$cleanup_enabled" "$timeline_enabled" <<'PY'
import csv
import json
from pathlib import Path
import sys

wanted = {
    "NUMBER_CLEANUP",
    "NUMBER_MIN_AGE",
    "NUMBER_LIMIT",
    "NUMBER_LIMIT_IMPORTANT",
    "TIMELINE_CREATE",
    "TIMELINE_CLEANUP",
    "TIMELINE_MIN_AGE",
    "TIMELINE_LIMIT_HOURLY",
    "TIMELINE_LIMIT_DAILY",
    "TIMELINE_LIMIT_WEEKLY",
    "TIMELINE_LIMIT_MONTHLY",
    "TIMELINE_LIMIT_YEARLY",
}

values = {}
with Path(sys.argv[1]).open(encoding="utf-8", errors="replace", newline="") as handle:
    for row in csv.reader(handle, delimiter="|", quotechar='"'):
        if len(row) < 2:
            continue
        key = row[0].strip()
        if key in wanted:
            values[key] = row[1].strip().strip('"')

print(json.dumps({
    "configured": True,
    "values": values,
    "cleanup_timer_enabled": sys.argv[2] == "yes",
    "timeline_timer_enabled": sys.argv[3] == "yes",
}, ensure_ascii=False, separators=(",", ":")))
PY

    rm -f -- "$config_file"
    trap - RETURN
}

case "${1:-}" in
    --probe)
        [[ $# -eq 1 ]] || fail '--probe does not accept arguments'
        printf 'OK\n'
        ;;
    --fingerprint)
        [[ $# -eq 1 ]] || fail '--fingerprint does not accept arguments'
        read_fingerprint
        ;;
    --policy)
        [[ $# -eq 1 ]] || fail '--policy does not accept arguments'
        read_policy
        ;;
    '')
        [[ $# -eq 0 ]] || fail 'unsupported helper arguments'
        read_points
        ;;
    *)
        fail 'unsupported helper arguments'
        ;;
esac
