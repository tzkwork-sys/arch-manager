#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
export LANG=C

PACMAN='/usr/bin/pacman'
FLOCK='/usr/bin/flock'
LOCK_FILE='/run/arch-manager-app-store.lock'
PACMAN_DB_LOCK='/var/lib/pacman/db.lck'

fail() {
    local code="$1"
    shift
    printf '%s\n' "$*" >&2
    exit "$code"
}

validate_package_name() {
    local package_name="${1:-}"
    [[ -n "$package_name" && ${#package_name} -le 128 ]] || fail 72 'invalid package name'
    [[ "$package_name" != -* ]] || fail 72 'invalid package name'
    [[ "$package_name" =~ ^[A-Za-z0-9@._+:-]+$ ]] || fail 72 'invalid package name'
}

require_root() {
    (( EUID == 0 )) || fail 72 'helper must run as root through Polkit'
}

require_runtime() {
    [[ -x "$PACMAN" ]] || fail 72 'pacman is unavailable'
    [[ -x "$FLOCK" ]] || fail 72 'flock is unavailable'
}

official_repository_for() {
    local wanted="$1"
    local repo name version rest
    while read -r repo name version rest; do
        [[ "$name" == "$wanted" ]] || continue
        case "$repo" in
            core|extra|multilib) printf '%s\n' "$repo"; return 0 ;;
        esac
    done < <("$PACMAN" -Sl 2>/dev/null || true)
    return 1
}

package_is_installed() {
    "$PACMAN" -Qq "$1" >/dev/null 2>&1
}

system_has_pending_upgrade() {
    local output rc
    set +e
    output=$("$PACMAN" -Qu --color never 2>/dev/null)
    rc=$?
    set -e
    [[ "$rc" == 0 || "$rc" == 1 ]] || fail 72 'could not inspect pending system upgrades'
    [[ -n "$output" ]]
}

require_idle_package_manager() {
    [[ ! -e "$PACMAN_DB_LOCK" ]] || fail 73 'pacman database is locked by another transaction'
}

run_install() {
    [[ $# -eq 1 ]] || fail 72 'install expects exactly one package name'
    local package_name="$1"
    local output repository
    validate_package_name "$package_name"
    require_root
    require_runtime
    require_idle_package_manager
    repository=$(official_repository_for "$package_name") \
        || fail 75 'package is not available from an official Arch repository'
    package_is_installed "$package_name" && {
        printf 'OK\tinstalled\t%s\n' "$package_name"
        return 0
    }
    if system_has_pending_upgrade; then
        fail 74 'a full system upgrade is required before installing applications'
    fi

    if ! output=$("$PACMAN" -S --needed --noconfirm "$repository/$package_name" 2>&1); then
        printf '%s\n' "$output" >&2
        fail 76 'pacman install failed'
    fi
    package_is_installed "$package_name" || fail 76 'package was not installed after pacman returned success'
    printf 'OK\tinstalled\t%s\n' "$package_name"
}

run_remove() {
    [[ $# -eq 1 ]] || fail 72 'remove expects exactly one package name'
    local package_name="$1"
    local output
    validate_package_name "$package_name"
    require_root
    require_runtime
    require_idle_package_manager
    # Removal is safe for any package already registered in pacman's local
    # database, including manually installed .pkg.tar.zst packages. Installation
    # remains restricted to official repositories in run_install().
    if ! package_is_installed "$package_name"; then
        printf 'OK\tremoved\t%s\n' "$package_name"
        return 0
    fi

    # -Rs removes dependencies that become unneeded, but keeps dependency
    # checks enabled and does not touch per-user configuration in $HOME.
    if ! output=$("$PACMAN" -Rs --noconfirm "$package_name" 2>&1); then
        printf '%s\n' "$output" >&2
        fail 76 'pacman remove failed'
    fi
    package_is_installed "$package_name" && fail 76 'package is still installed after pacman returned success'
    printf 'OK\tremoved\t%s\n' "$package_name"
}

self_test() {
    [[ "$PACMAN" == '/usr/bin/pacman' ]] || exit 1
    [[ "$FLOCK" == '/usr/bin/flock' ]] || exit 1
    printf 'OK\tself-test\tapp-store-v1\n'
}

main() {
    [[ $# -ge 1 ]] || fail 72 'missing action'
    local action="$1"
    shift

    if [[ "$action" == '--self-test' ]]; then
        [[ $# -eq 0 ]] || fail 72 '--self-test does not accept arguments'
        self_test
        return 0
    fi

    require_root
    require_runtime
    exec 9>"$LOCK_FILE"
    "$FLOCK" -n 9 || fail 73 'another Arch Manager package transaction is already running'

    case "$action" in
        install) run_install "$@" ;;
        remove) run_remove "$@" ;;
        *) fail 72 'unsupported action' ;;
    esac
}

main "$@"
