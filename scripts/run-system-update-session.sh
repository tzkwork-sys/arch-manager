#!/usr/bin/env bash
set -u -o pipefail

export PATH=/usr/bin:/bin
# Keep the user's terminal locale and capabilities. The update now runs in a
# real terminal, so pacman/yay prompts and ANSI output are rendered correctly.

SUDO=/usr/bin/sudo
PACMAN=/usr/bin/pacman
YAY=/usr/bin/yay
RESTORE_HELPER=/usr/local/libexec/arch-manager/manage-restore-points
OFFICIAL=yes
CREATE_RESTORE=yes
STATUS_FILE=""
AUR_PACKAGES=()
RESTORE_POINT=""
KEEPALIVE_PID=""
SUDO_WRAPPER_DIR=""
PAUSE_ON_EXIT=${ARCH_MANAGER_PAUSE_ON_EXIT:-0}

usage() {
    printf 'Usage: %s --official <yes|no> --restore-point <yes|no> --status-file <path> [--aur-package <name>]...\n' "$0" >&2
    exit 2
}

valid_yes_no() {
    [[ "$1" == yes || "$1" == no ]]
}

valid_package_name() {
    [[ "$1" =~ ^[A-Za-z0-9@._+:-]+$ ]]
}

while (( $# > 0 )); do
    case "$1" in
        --official)
            (( $# >= 2 )) || usage
            OFFICIAL="$2"
            shift 2
            ;;
        --restore-point)
            (( $# >= 2 )) || usage
            CREATE_RESTORE="$2"
            shift 2
            ;;
        --status-file)
            (( $# >= 2 )) || usage
            STATUS_FILE="$2"
            shift 2
            ;;
        --aur-package)
            (( $# >= 2 )) || usage
            valid_package_name "$2" || usage
            AUR_PACKAGES+=("$2")
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

valid_yes_no "$OFFICIAL" || usage
valid_yes_no "$CREATE_RESTORE" || usage
[[ -n "$STATUS_FILE" ]] || usage
[[ "$STATUS_FILE" != *$'\n'* && "$STATUS_FILE" != *$'\r'* ]] || usage

write_status() {
    local state="$1"
    local detail="${2:-}"
    local point="${RESTORE_POINT:-}"
    detail=${detail//$'\t'/ }
    detail=${detail//$'\n'/ }
    detail=${detail//$'\r'/ }
    printf '%s\t%s\t%s\n' "$state" "$detail" "$point" >"$STATUS_FILE"
}

cleanup() {
    if [[ -n "$KEEPALIVE_PID" ]]; then
        kill "$KEEPALIVE_PID" >/dev/null 2>&1 || true
        wait "$KEEPALIVE_PID" 2>/dev/null || true
    fi
    if [[ -n "$SUDO_WRAPPER_DIR" ]]; then
        /usr/bin/rm -rf -- "$SUDO_WRAPPER_DIR" 2>/dev/null || true
    fi
    if [[ "$PAUSE_ON_EXIT" == 1 && -t 0 ]]; then
        printf '\nНажмите Enter, чтобы закрыть терминал…'
        IFS= read -r _ || true
    fi
}
trap cleanup EXIT
trap 'write_status interrupted "Операция прервана"; exit 130' HUP INT TERM

[[ -x "$SUDO" ]] || { write_status failed "sudo не найден"; exit 10; }
[[ -x "$PACMAN" ]] || { write_status failed "pacman не найден"; exit 11; }

printf '\033]0;Arch Manager — обновление системы\007'
printf '%s\n' '============================================================'
printf '%s\n' ' Arch Manager — обновление системы'
printf '%s\n' '============================================================'
printf '\n'
printf '%s\n' 'Введите административный пароль один раз в этом терминале.'
printf '%s\n' 'На вопросы pacman/yay ([Y/n], [y/N] и т. п.) отвечайте здесь же.'
printf '%s\n' 'Arch Manager не получает и не сохраняет пароль.'
printf '\n'

write_status running "authorization"
# Start a fresh terminal-bound sudo session. Because this is a real terminal,
# sudo handles the password itself and package-manager prompts remain fully
# interactive. No password ever passes through the GUI.
"$SUDO" -k
"$SUDO" -v
sudo_rc=$?
if (( sudo_rc != 0 )); then
    write_status cancelled "Не получено подтверждение администратора"
    printf '\nНе удалось получить права администратора. Код: %s\n' "$sudo_rc"
    exit "$sudo_rc"
fi

# Keep the single sudo ticket alive for the whole update. Privileged child
# calls use -n, so a second password prompt can never appear unexpectedly.
(
    while true; do
        /usr/bin/sleep 45
        "$SUDO" -n -v >/dev/null 2>&1 || exit 0
    done
) &
KEEPALIVE_PID=$!

if [[ "$CREATE_RESTORE" == yes ]]; then
    write_status running "restore-point"
    printf '[1] Создаю защитную точку восстановления…\n'
    if [[ ! -x "$RESTORE_HELPER" ]]; then
        write_status failed "helper точек восстановления не установлен"
        printf 'Не найден системный helper точек восстановления: %s\n' "$RESTORE_HELPER"
        exit 12
    fi

    restore_output=$("$SUDO" -n "$RESTORE_HELPER" create yes 'Arch Manager: перед обновлением системы' 2>&1)
    restore_rc=$?
    printf '%s\n' "$restore_output"
    if (( restore_rc != 0 )); then
        write_status restore-point-failed "Не удалось создать защитную точку"
        exit "$restore_rc"
    fi
    IFS=$'\t' read -r ok created RESTORE_POINT <<<"$restore_output"
    if [[ "$ok" != OK || "$created" != created || ! "$RESTORE_POINT" =~ ^[1-9][0-9]*$ ]]; then
        write_status restore-point-failed "helper вернул неожиданный результат"
        exit 13
    fi
    printf '[OK] Защитная точка создана.\n\n'
fi

if [[ "$OFFICIAL" == yes ]]; then
    write_status running "official"
    printf '[2] Официальные обновления Arch\n\n'
    "$SUDO" -n "$PACMAN" -Syu
    pacman_rc=$?
    if (( pacman_rc != 0 )); then
        write_status official-failed "pacman:$pacman_rc"
        printf '\nОфициальное обновление завершилось ошибкой. Код: %s\n' "$pacman_rc"
        printf '%s\n' 'AUR-пакеты после ошибки официального этапа не устанавливаются.'
        exit "$pacman_rc"
    fi
    printf '\n[OK] Официальные пакеты обновлены.\n\n'
else
    printf '[2] Официальных обновлений по последней проверке нет — этап пропущен.\n\n'
fi

if (( ${#AUR_PACKAGES[@]} > 0 )); then
    if [[ ! -x "$YAY" ]]; then
        write_status aur-skipped "yay не найден"
        printf '[AUR] yay не найден. Выбранные AUR-пакеты пропущены.\n'
        exit 0
    fi

    # Confirm the cached credential before starting yay. If it unexpectedly
    # expired, fail instead of asking for a second password.
    if ! "$SUDO" -n -v >/dev/null 2>&1; then
        write_status aur-failed "истёк единый сеанс авторизации"
        printf '[AUR] Сеанс авторизации истёк. Повторный пароль не запрашивается.\n'
        exit 14
    fi

    # yay normally resolves sudo through PATH. Force those calls to be
    # non-interactive so Arch Manager can guarantee one password prompt.
    SUDO_WRAPPER_DIR=$(/usr/bin/mktemp -d -t arch-manager-sudo.XXXXXX)
    cat >"$SUDO_WRAPPER_DIR/sudo" <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/sudo -n "$@"
EOF
    /usr/bin/chmod 0700 "$SUDO_WRAPPER_DIR/sudo"

    write_status running "aur"
    printf '[3] Выбранные AUR-пакеты: %s\n\n' "${#AUR_PACKAGES[@]}"
    PATH="$SUDO_WRAPPER_DIR:/usr/bin:/bin" "$YAY" -S --needed --aur -- "${AUR_PACKAGES[@]}"
    aur_rc=$?
    if (( aur_rc != 0 )); then
        write_status aur-failed "yay:$aur_rc"
        printf '\nОфициальный этап завершён, но выбранные AUR-пакеты завершились ошибкой. Код: %s\n' "$aur_rc"
        exit "$aur_rc"
    fi
    printf '\n[OK] Выбранные AUR-пакеты обновлены.\n'
else
    printf '[3] AUR-пакеты не выбраны — этап пропущен.\n'
fi

write_status success "Обновление завершено"
printf '\nГотово. Обновление системы завершено.\n'
exit 0
