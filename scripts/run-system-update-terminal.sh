#!/usr/bin/env bash
set -euo pipefail

export PATH=/usr/bin:/bin
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUNNER="$SCRIPT_DIR/run-system-update-session.sh"
TITLE='Arch Manager — обновление системы'

[[ -x "$RUNNER" ]] || {
    printf 'Arch Manager: update runner не найден: %s\n' "$RUNNER" >&2
    exit 11
}

# The runner pauses before exit only when it was launched in this user-visible
# terminal. That leaves enough time to read the final result, while automated
# checks can still invoke the runner non-interactively without hanging.
export ARCH_MANAGER_PAUSE_ON_EXIT=1

if command -v konsole >/dev/null 2>&1; then
    # --separate avoids delegating the tab to an already running Konsole process;
    # QProcess can therefore reliably wait for this exact update window.
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
