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
    # KWin controls exact half-screen tiling on Wayland, not Konsole geometry.
    # This temporary script affects only this update window.
    KWIN_DBUS=""
    for command in qdbus6 qdbus-qt6; do
        if command -v "$command" >/dev/null 2>&1; then
            KWIN_DBUS=$(command -v "$command")
            break
        fi
    done
    KWIN_SCRIPT=""
    KWIN_ID=""
    TOKEN="arch-manager-update-$$-$RANDOM"
    cleanup_kwin() {
        if [[ -n "$KWIN_ID" && -n "$KWIN_DBUS" ]]; then
            "$KWIN_DBUS" org.kde.KWin /Scripting \
                org.kde.kwin.Scripting.unloadScript "$TOKEN" >/dev/null 2>&1 || true
        fi
        if [[ -n "$KWIN_SCRIPT" ]]; then
            rm -f -- "$KWIN_SCRIPT"
        fi
    }
    trap cleanup_kwin EXIT
    if [[ -n "$KWIN_DBUS" && "${XDG_SESSION_TYPE:-}" == wayland ]]; then
        KWIN_SCRIPT=$(mktemp -t arch-manager-kwin-XXXXXXXX.js)
        cat >"$KWIN_SCRIPT" <<EOF
// Tile only the new Konsole window with this unique session marker.
const marker = "$TOKEN";
let placed = false;
function apply(window) {
    if (placed || !window) return;
    if (String(window.caption || "").indexOf(marker) < 0) return;
    placed = true;
    window.quickTileMode = 2; // KWin QuickTileFlag.Right
}
workspace.windowAdded.connect(function(window) {
    apply(window);
    if (!placed && window.captionChanged)
        window.captionChanged.connect(function() { apply(window); });
});
workspace.windowList().forEach(apply);
EOF
        KWIN_ID=$("$KWIN_DBUS" org.kde.KWin /Scripting \
            org.kde.kwin.Scripting.loadScript "$KWIN_SCRIPT" "$TOKEN" 2>/dev/null || true)
        if [[ "$KWIN_ID" =~ ^[0-9]+$ ]]; then
            "$KWIN_DBUS" org.kde.KWin "/Scripting/$KWIN_ID" \
                org.kde.kwin.Script.run >/dev/null 2>&1 || {
                echo 'Arch Manager: не удалось активировать KWin-размещение.' >&2
                KWIN_ID=""
            }
        else
            KWIN_ID=""
            echo 'Arch Manager: KWin-размещение недоступно.' >&2
        fi
    fi
    # Keep waiting for the real terminal, preserving QProcess lifecycle.
    konsole --separate --title "$TOKEN" -e "$RUNNER" "$@"
    exit $?
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
