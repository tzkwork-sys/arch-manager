#!/usr/bin/env bash
set -euo pipefail

export PATH=/usr/bin:/bin
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUNNER="$SCRIPT_DIR/run-aur-operation.sh"
TITLE='Arch Manager — AUR'

[[ -x "$RUNNER" ]] || {
    printf 'Arch Manager: AUR runner не найден: %s\n' "$RUNNER" >&2
    exit 11
}

export ARCH_MANAGER_PAUSE_ON_EXIT=1

if command -v konsole >/dev/null 2>&1; then
    exec konsole --separate -e "$RUNNER" "$@"
elif command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal --wait --title="$TITLE" -- "$RUNNER" "$@"
elif command -v xfce4-terminal >/dev/null 2>&1; then
    exec xfce4-terminal --disable-server --title="$TITLE" -x "$RUNNER" "$@"
elif command -v kitty >/dev/null 2>&1; then
    exec kitty --title "$TITLE" "$RUNNER" "$@"
elif command -v alacritty >/dev/null 2>&1; then
    exec alacritty --title "$TITLE" -e "$RUNNER" "$@"
elif command -v foot >/dev/null 2>&1; then
    exec foot --title="$TITLE" "$RUNNER" "$@"
elif command -v xterm >/dev/null 2>&1; then
    exec xterm -T "$TITLE" -e "$RUNNER" "$@"
fi

printf 'Arch Manager: не найден поддерживаемый терминал. Установите Konsole.\n' >&2
exit 12
