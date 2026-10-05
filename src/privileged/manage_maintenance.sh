#!/usr/bin/env bash
set -euo pipefail

PATH=/usr/bin:/bin
export PATH
export LC_ALL=C
export LANG=C

PACCACHE=/usr/bin/paccache
PACMAN=/usr/bin/pacman
JOURNALCTL=/usr/bin/journalctl

fail() {
    printf 'Arch Manager maintenance helper: %s\n' "$*" >&2
    exit 2
}

require_root() {
    [[ ${EUID:-$(/usr/bin/id -u)} -eq 0 ]] || fail 'must run as root'
}

require_runtime() {
    [[ -x "$PACCACHE" ]] || fail 'paccache is missing'
    [[ -x "$PACMAN" ]] || fail 'pacman is missing'
    [[ -x "$JOURNALCTL" ]] || fail 'journalctl is missing'
}

validate_action() {
    case "$1" in
        package-cache|orphans|journal) ;;
        *) fail "unsupported action: $1" ;;
    esac
}

validate_cache_keep() {
    case "$1" in
        0|1|2|3) ;;
        *) fail 'cache keep must be 0, 1, 2 or 3' ;;
    esac
}

clean_package_cache() {
    local keep="$1"
    "$PACCACHE" -r -k "$keep" --nocolor >/dev/null
    printf 'OK\tpackage-cache\tkeep=%s\n' "$keep"
}

clean_orphans() {
    local -a packages=()
    local output="" rc=0
    output=$("$PACMAN" -Qtdq 2>/dev/null) || rc=$?
    (( rc == 0 || rc == 1 )) || fail "could not query orphan packages (pacman rc=$rc)"
    if [[ -n "$output" ]]; then
        mapfile -t packages <<<"$output"
    fi
    if ((${#packages[@]} > 0)); then
        "$PACMAN" -R --noconfirm -- "${packages[@]}" >/dev/null
    fi
    printf 'OK\torphans\t%d\n' "${#packages[@]}"
}

clean_journal() {
    "$JOURNALCTL" --vacuum-time=30d --no-pager >/dev/null
    printf 'OK\tjournal\n'
}

self_test() {
    validate_action package-cache
    validate_action orphans
    validate_action journal
    validate_cache_keep 0
    validate_cache_keep 3
    printf 'OK\n'
}

run_clean() {
    local cache_keep=3
    if [[ "${1:-}" == "--cache-keep" ]]; then
        (($# >= 3)) || fail 'missing cache keep value or cleanup action'
        cache_keep="$2"
        validate_cache_keep "$cache_keep"
        shift 2
    fi

    (($# >= 1 && $# <= 3)) || fail 'clean expects between one and three actions'
    require_root
    require_runtime

    local action
    local -A seen=()
    for action in "$@"; do
        validate_action "$action"
        [[ -z "${seen[$action]+x}" ]] || fail "duplicate action: $action"
        seen[$action]=1
    done

    # Fixed order keeps behavior deterministic regardless of GUI selection order.
    [[ -n "${seen[package-cache]+x}" ]] && clean_package_cache "$cache_keep"
    [[ -n "${seen[orphans]+x}" ]] && clean_orphans
    [[ -n "${seen[journal]+x}" ]] && clean_journal
    printf 'OK\tdone\n'
}

case "${1:-}" in
    --self-test)
        [[ $# -eq 1 ]] || fail '--self-test does not accept arguments'
        self_test
        ;;
    clean)
        shift
        run_clean "$@"
        ;;
    '')
        fail 'missing action'
        ;;
    *)
        fail 'unsupported command'
        ;;
esac
