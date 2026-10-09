#!/usr/bin/env bash
set -u -o pipefail

export PATH=/usr/bin:/bin
YAY=/usr/bin/yay
PACMAN=/usr/bin/pacman
FLOCK=/usr/bin/flock
SUDO=/usr/bin/sudo
KEEPALIVE_PID=""
ACTION=""
PACKAGE=""
STATUS_FILE=""
PAUSE_ON_EXIT=${ARCH_MANAGER_PAUSE_ON_EXIT:-0}

usage() {
    printf 'Usage: %s --action <install|remove|update|update-all> [--package <name>] --status-file <path>\n' "$0" >&2
    exit 2
}

valid_package_name() {
    local name="$1"
    [[ -n "$name" && ${#name} -le 128 ]] || return 1
    [[ "$name" != -* ]] || return 1
    [[ "$name" =~ ^[A-Za-z0-9@._+:-]+$ ]]
}

while (( $# > 0 )); do
    case "$1" in
        --action)
            (( $# >= 2 )) || usage
            ACTION="$2"
            shift 2
            ;;
        --package)
            (( $# >= 2 )) || usage
            PACKAGE="$2"
            shift 2
            ;;
        --status-file)
            (( $# >= 2 )) || usage
            STATUS_FILE="$2"
            shift 2
            ;;
        *) usage ;;
    esac
done

# Historical contracts kept documented for regression traceability:
# [[ "$ACTION" == install ]] || usage
# [[ "$ACTION" == install || "$ACTION" == remove ]] || usage
# Stage 7.3+ compatibility contract. Stage 7.5 adds update/update-all; update-all has no package argument.
[[ "$ACTION" == install || "$ACTION" == remove || "$ACTION" == update || "$ACTION" == update-all ]] || usage
if [[ "$ACTION" != update-all ]]; then
    valid_package_name "$PACKAGE" || usage
fi
[[ -n "$STATUS_FILE" ]] || usage
[[ "$STATUS_FILE" != *$'\n'* && "$STATUS_FILE" != *$'\r'* && "$STATUS_FILE" != *$'\t'* ]] || usage

write_status() {
    local state="$1"
    local detail="${2:-}"
    detail=${detail//$'\t'/ }
    detail=${detail//$'\n'/ }
    detail=${detail//$'\r'/ }
    printf '%s\t%s\n' "$state" "$detail" >"$STATUS_FILE"
}

start_sudo_keepalive() {
    [[ -x "$SUDO" ]] || return 0
    [[ -z "$KEEPALIVE_PID" ]] || return 0
    # yay remains responsible for the first authentication, only when needed.
    # Refresh an existing ticket during a long build and subsequent orphan
    # cleanup. -n prevents this background worker from ever asking for a password.
    (
        sleep_pid=""
        trap '[[ -z "$sleep_pid" ]] || kill "$sleep_pid" 2>/dev/null; exit 0' HUP INT TERM
        while true; do
            /usr/bin/sleep 45 &
            sleep_pid=$!
            wait "$sleep_pid" || exit 0
            sleep_pid=""
            "$SUDO" -n -v >/dev/null 2>&1 || true
        done
    ) &
    KEEPALIVE_PID=$!
}

stop_sudo_keepalive() {
    if [[ -n "$KEEPALIVE_PID" ]]; then
        kill "$KEEPALIVE_PID" >/dev/null 2>&1 || true
        wait "$KEEPALIVE_PID" 2>/dev/null || true
        KEEPALIVE_PID=""
    fi
}

cleanup() {
    # Stop renewing privileges before the optional terminal-close prompt.
    stop_sudo_keepalive
    if [[ "$PAUSE_ON_EXIT" == 1 && -t 0 ]]; then
        printf '\nНажмите Enter, чтобы закрыть терминал…'
        IFS= read -r _ || true
    fi
}
trap cleanup EXIT
trap 'write_status cancelled "Операция прервана"; exit 130' HUP INT TERM

if (( EUID == 0 )); then
    write_status failed "AUR runner refuses root"
    printf 'Arch Manager: AUR-операции не запускаются от root.\n' >&2
    exit 70
fi
[[ -x "$YAY" ]] || { write_status failed "yay не найден"; exit 71; }
[[ -x "$PACMAN" ]] || { write_status failed "pacman не найден"; exit 72; }
[[ -x "$FLOCK" ]] || { write_status failed "flock не найден"; exit 73; }

if [[ -e /var/lib/pacman/db.lck ]]; then
    write_status busy "pacman database is locked"
    printf 'Arch Manager: менеджер пакетов занят другой операцией.\n' >&2
    exit 74
fi

runtime_dir=${XDG_RUNTIME_DIR:-/tmp}
if [[ "$runtime_dir" == /tmp ]]; then
    lock_file="/tmp/arch-manager-aur-${UID}.lock"
else
    lock_file="$runtime_dir/arch-manager-aur.lock"
fi
exec 9>"$lock_file"
if ! "$FLOCK" -n 9; then
    write_status busy "another AUR transaction is running"
    printf 'Arch Manager: другая AUR-операция уже выполняется.\n' >&2
    exit 75
fi

read_orphans() {
    local target_name="$1"
    local output="" rc=0
    local -n target_ref="$target_name"
    target_ref=()
    output=$("$PACMAN" -Qtdq 2>/dev/null) || rc=$?
    (( rc == 0 || rc == 1 )) || return "$rc"
    if [[ -n "$output" ]]; then
        mapfile -t target_ref <<<"$output"
    fi
    return 0
}

new_orphans_since() {
    local baseline_name="$1"
    local current_name="$2"
    local result_name="$3"
    local -n baseline_ref="$baseline_name"
    local -n current_ref="$current_name"
    local -n result_ref="$result_name"
    local package
    local -A baseline_map=()

    result_ref=()
    for package in "${baseline_ref[@]}"; do
        [[ -n "$package" ]] && baseline_map["$package"]=1
    done
    for package in "${current_ref[@]}"; do
        [[ -n "$package" ]] || continue
        valid_package_name "$package" || return 2
        [[ -n "${baseline_map[$package]+x}" ]] || result_ref+=("$package")
    done
    return 0
}

format_package_list() {
    local -a packages=("$@")
    local IFS=', '
    printf '%s' "${packages[*]}"
}

clean_yay_cache_for_package() {
    local package_name="$1"
    local yay_cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/yay"
    local direct_path="$yay_cache_root/$package_name"
    local cache_dir cache_path
    local removed_count=0
    local -a cache_targets=()

    # yay keeps AUR build trees under ~/.cache/yay/<pkgbase>. For the common
    # case pkgbase == pkgname the direct path is enough. Split packages can
    # have a different pkgbase, so also inspect one level of .SRCINFO files
    # and remove only build trees that explicitly declare this pkgname.
    [[ -d "$yay_cache_root" ]] || {
        printf '0'
        return 0
    }

    if [[ -e "$direct_path" || -L "$direct_path" ]]; then
        cache_targets+=("$direct_path")
    fi

    while IFS= read -r -d '' cache_dir; do
        [[ "$cache_dir" == "$direct_path" ]] && continue
        [[ -f "$cache_dir/.SRCINFO" ]] || continue
        if awk -v wanted="$package_name" '
            $1 == "pkgname" && $2 == "=" && $3 == wanted { found = 1 }
            END { exit(found ? 0 : 1) }
        ' "$cache_dir/.SRCINFO"; then
            cache_targets+=("$cache_dir")
        fi
    done < <(find "$yay_cache_root" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)

    for cache_path in "${cache_targets[@]}"; do
        # Every target comes either from the validated package name below the
        # yay cache root or from an immediate child discovered by find.
        case "$cache_path" in
            "$yay_cache_root"/*) ;;
            *) return 2 ;;
        esac
        rm -rf -- "$cache_path" || return 1
        ((removed_count += 1))
    done

    printf '%s' "$removed_count"
}

if [[ "$ACTION" == update-all ]]; then
    printf '\033]0;Arch Manager — обновление AUR\007'
    printf '%s\n' '============================================================'
    printf '%s\n' ' Arch Manager — обновление всех AUR-пакетов'
    printf '%s\n' '============================================================'
    printf '%s\n' 'Будет запущено: yay -Sua'
    printf '%s\n' 'Системные пакеты из официальных репозиториев этой командой не обновляются.'
    printf '%s\n\n' 'Проверяйте вопросы yay, PKGBUILD/diff и подтверждения прямо в терминале.'
    write_status running "yay-update-all"
    start_sudo_keepalive
    "$YAY" -Sua
    rc=$?
    if (( rc != 0 )); then
        write_status failed "yay:$rc"
        printf '\nОбновление AUR завершилось с ошибкой или было отменено. Код yay: %s\n' "$rc"
        exit "$rc"
    fi
    write_status success "updated-all"
    printf '%s\n' '[OK] Все доступные AUR-обновления установлены.'
    exit 0
fi

if [[ "$ACTION" == update ]]; then
    if ! "$PACMAN" -Qq -- "$PACKAGE" >/dev/null 2>&1; then
        write_status not-installed "package is not installed"
        printf 'Arch Manager: пакет %s уже не установлен.\n' "$PACKAGE" >&2
        exit 77
    fi
    if ! "$PACMAN" -Qm -- "$PACKAGE" >/dev/null 2>&1; then
        write_status source-changed "package is no longer foreign/AUR"
        printf 'Arch Manager: пакет %s больше не определяется как foreign/AUR.\n' "$PACKAGE" >&2
        exit 78
    fi
    before_version=$("$PACMAN" -Q -- "$PACKAGE" 2>/dev/null | awk 'NR==1 {print $2}')
    printf '\033]0;Arch Manager — обновление AUR\007'
    printf '%s\n' '============================================================'
    printf '%s\n' ' Arch Manager — обновление пакета из AUR'
    printf '%s\n' '============================================================'
    printf '\nПакет: %s%s\n' "$PACKAGE" "${before_version:+ ($before_version)}"
    printf '%s\n\n' 'Проверяйте вопросы yay, PKGBUILD/diff и подтверждения прямо в терминале.'
    write_status running "yay-update"
    start_sudo_keepalive
    "$YAY" -S --aur -- "$PACKAGE"
    rc=$?
    if (( rc != 0 )); then
        write_status failed "yay:$rc"
        printf '\nОбновление завершилось с ошибкой или было отменено. Код yay: %s\n' "$rc"
        exit "$rc"
    fi
    after_version=$("$PACMAN" -Q -- "$PACKAGE" 2>/dev/null | awk 'NR==1 {print $2}')
    [[ -n "$after_version" ]] || { write_status failed "package missing after update"; exit 79; }
    write_status success "${before_version:-?}->${after_version}"
    printf '\n[OK] Пакет %s обновлён до %s.\n' "$PACKAGE" "$after_version"
    exit 0
fi

if [[ "$ACTION" == install ]]; then
    printf '\033]0;Arch Manager — установка AUR\007'
    printf '%s\n' '============================================================'
    printf '%s\n' ' Arch Manager — установка пакета из AUR'
    printf '%s\n' '============================================================'
    printf '\nПакет: %s\n' "$PACKAGE"
    printf '%s\n' 'Сборка выполняется от текущего пользователя.'
    printf '%s\n' 'yay запросит пароль sudo, если это требуется для установки зависимостей/готового пакета.'
    printf '%s\n' 'Действующая авторизация поддерживается только на время этой операции.'
    printf '%s\n' 'Проверяйте вопросы yay, PKGBUILD/diff и подтверждения прямо в этом терминале.'
    printf '%s\n\n' 'Arch Manager не подтверждает вопросы автоматически и не получает ваш пароль.'

    write_status running "yay-install"
    start_sudo_keepalive
    "$YAY" -S --aur -- "$PACKAGE"
    rc=$?
    if (( rc != 0 )); then
        write_status failed "yay:$rc"
        printf '\nУстановка завершилась с ошибкой или была отменена. Код yay: %s\n' "$rc"
        exit "$rc"
    fi

    if ! "$PACMAN" -Qq -- "$PACKAGE" >/dev/null 2>&1; then
        write_status failed "package is not installed after yay success"
        printf '\nyay завершился успешно, но пакет не найден в локальной базе pacman.\n' >&2
        exit 76
    fi

    installed_version=$("$PACMAN" -Q -- "$PACKAGE" 2>/dev/null | awk 'NR==1 {print $2}')
    write_status success "${installed_version:-installed}"
    printf '\n[OK] Пакет %s установлен%s.\n' "$PACKAGE" "${installed_version:+ ($installed_version)}"
    exit 0
fi

# Stage 7.4 removal is intentionally local-only. Re-check immediately before
# invoking yay so a stale GUI card cannot remove an absent or now-official package.
if ! "$PACMAN" -Qq -- "$PACKAGE" >/dev/null 2>&1; then
    write_status not-installed "package is not installed"
    printf 'Arch Manager: пакет %s уже не установлен.\n' "$PACKAGE" >&2
    exit 77
fi
if ! "$PACMAN" -Qm -- "$PACKAGE" >/dev/null 2>&1; then
    write_status source-changed "package is no longer foreign/AUR"
    printf 'Arch Manager: пакет %s больше не определяется как foreign/AUR.\n' "$PACKAGE" >&2
    exit 78
fi

declare -a baseline_orphans=()
if ! read_orphans baseline_orphans; then
    write_status failed "could not query orphan baseline"
    printf 'Arch Manager: не удалось получить исходный список ненужных зависимостей. Удаление не начато.\n' >&2
    exit 80
fi

installed_version=$("$PACMAN" -Q -- "$PACKAGE" 2>/dev/null | awk 'NR==1 {print $2}')
printf '\033]0;Arch Manager — удаление AUR\007'
printf '%s\n' '============================================================'
printf '%s\n' ' Arch Manager — удаление пакета из AUR'
printf '%s\n' '============================================================'
printf '\nПакет: %s%s\n' "$PACKAGE" "${installed_version:+ ($installed_version)}"
printf '%s\n' 'Сначала yay удалит выбранный пакет через -Rns.'
printf '%s\n' 'После этого Arch Manager проверит, появились ли НОВЫЕ ненужные зависимости именно из-за этой операции.'
printf '%s\n' 'Если такие пакеты появятся (например, отдельный *-debug), будет показано отдельное подтверждение их удаления.'
printf '%s\n' 'Ненужные пакеты, существовавшие до этой операции, автоматически не затрагиваются.'
printf '%s\n\n' 'Arch Manager не подтверждает удаление автоматически и не получает ваш пароль.'

write_status running "yay-remove"
start_sudo_keepalive
"$YAY" -Rns -- "$PACKAGE"
rc=$?
if (( rc != 0 )); then
    write_status failed "yay:$rc"
    printf '\nУдаление завершилось с ошибкой или было отменено. Код yay: %s\n' "$rc"
    exit "$rc"
fi

if "$PACMAN" -Qq -- "$PACKAGE" >/dev/null 2>&1; then
    write_status failed "package is still installed after yay success"
    printf '\nyay завершился успешно, но пакет всё ещё установлен.\n' >&2
    exit 79
fi

# The target is gone from pacman, so its per-user yay build cache is now safe
# to remove. Do this before orphan cleanup so the cache is still cleaned even
# if a later optional orphan-removal confirmation is cancelled.
yay_cache_cleanup_failed=0
yay_cache_cleanup_count=0
if cache_cleanup_output=$(clean_yay_cache_for_package "$PACKAGE"); then
    yay_cache_cleanup_count=${cache_cleanup_output:-0}
    if (( yay_cache_cleanup_count > 0 )); then
        printf '[OK] Удалён кэш сборки yay для пакета %s (%d каталог(а)).\n' "$PACKAGE" "$yay_cache_cleanup_count"
    else
        printf '[OK] Кэш сборки yay для пакета %s не найден.\n' "$PACKAGE"
    fi
else
    yay_cache_cleanup_failed=1
    printf '[WARN] Пакет удалён, но очистить его кэш сборки yay не удалось.\n' >&2
fi

# pacman -Rns removes dependencies of the target, but a separately installed
# companion/split package (for example <name>-debug) can become an orphan only
# after the target disappears. Clean only the orphan delta created by this
# transaction; never sweep pre-existing system orphans here.
declare -a current_orphans=() new_orphans=() cleaned_orphans=()
cleanup_round=0
while (( cleanup_round < 4 )); do
    if ! read_orphans current_orphans; then
        write_status cleanup-incomplete "target removed; orphan recheck failed"
        printf '\n[WARN] Пакет удалён, но повторно проверить ненужные зависимости не удалось.\n' >&2
        exit 81
    fi
    if ! new_orphans_since baseline_orphans current_orphans new_orphans; then
        write_status cleanup-incomplete "target removed; invalid orphan name"
        printf '\n[WARN] Пакет удалён, но список новых ненужных зависимостей содержит неожиданное имя.\n' >&2
        exit 82
    fi
    ((${#new_orphans[@]} > 0)) || break

    printf '\nПосле удаления появились новые ненужные зависимости (%d):\n' "${#new_orphans[@]}"
    printf '  - %s\n' "${new_orphans[@]}"
    printf '%s\n' 'Они не были ненужными до этой операции. yay сейчас предложит удалить только этот новый список.'

    write_status running "yay-remove-orphan-cleanup"
    "$YAY" -Rns -- "${new_orphans[@]}"
    rc=$?
    if (( rc != 0 )); then
        remaining=$(format_package_list "${new_orphans[@]}")
        write_status cleanup-incomplete "target removed; cleanup cancelled/failed: ${remaining}"
        printf '\n[WARN] Основной пакет удалён, но очистка новых ненужных зависимостей была отменена или завершилась ошибкой.\n' >&2
        printf 'Остались: %s\n' "$remaining" >&2
        exit 83
    fi
    cleaned_orphans+=("${new_orphans[@]}")
    ((cleanup_round += 1))
done

if ! read_orphans current_orphans; then
    write_status cleanup-incomplete "target removed; final orphan verification failed"
    printf '\n[WARN] Пакет удалён, но финальная проверка ненужных зависимостей не удалась.\n' >&2
    exit 84
fi
if ! new_orphans_since baseline_orphans current_orphans new_orphans; then
    write_status cleanup-incomplete "target removed; final orphan list invalid"
    printf '\n[WARN] Пакет удалён, но финальный список ненужных зависимостей некорректен.\n' >&2
    exit 85
fi
if ((${#new_orphans[@]} > 0)); then
    remaining=$(format_package_list "${new_orphans[@]}")
    write_status cleanup-incomplete "target removed; new orphans remain: ${remaining}"
    printf '\n[WARN] Основной пакет удалён, но после нескольких проходов остались новые ненужные зависимости: %s\n' "$remaining" >&2
    exit 86
fi

cleanup_count=${#cleaned_orphans[@]}
if (( yay_cache_cleanup_failed != 0 )); then
    write_status cleanup-incomplete "target removed; yay cache cleanup failed; orphan-cleanup=${cleanup_count}"
else
    write_status success "removed; orphan-cleanup=${cleanup_count}; yay-cache-cleanup=${yay_cache_cleanup_count}"
fi
printf '\n[OK] Пакет %s удалён через yay -Rns.\n' "$PACKAGE"
if (( cleanup_count > 0 )); then
    printf '[OK] Дополнительно удалены новые ненужные зависимости: %s\n' "$(format_package_list "${cleaned_orphans[@]}")"
else
    printf '%s\n' '[OK] Новых ненужных зависимостей после удаления не появилось.'
fi
if (( yay_cache_cleanup_failed == 0 )); then
    printf '[OK] Кэш сборки yay выбранного пакета очищен (удалено каталогов: %d).\n' "$yay_cache_cleanup_count"
else
    printf '%s\n' '[WARN] Кэш сборки yay выбранного пакета остался; основной пакет при этом уже удалён.' >&2
fi
printf '%s\n' 'Ненужные зависимости, существовавшие ДО удаления, намеренно не затрагивались.'
printf '%s\n' 'Пользовательские данные в домашней папке не удаляются автоматически.'
if (( yay_cache_cleanup_failed != 0 )); then
    exit 87
fi
exit 0
