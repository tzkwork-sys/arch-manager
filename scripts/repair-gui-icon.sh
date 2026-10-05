#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
LAUNCHER="$HOME/.local/bin/arch-manager-gui"
DESKTOP_FILE="$APP_DIR/arch-manager-gui.desktop"
ICON_FILE="$ICON_DIR/arch-manager-gui.svg"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"

mkdir -p "$APP_DIR" "$ICON_DIR"
install -m 0644 "$PROJECT_DIR/src/assets/arch-manager.svg" "$ICON_FILE"

cat > "$DESKTOP_FILE" <<EOF2
[Desktop Entry]
Type=Application
Version=1.0
Name=Arch Manager — новая версия
GenericName=Управление системой Arch Linux
Comment=Обновления, точки восстановления и обслуживание Arch Linux
Exec=$LAUNCHER
TryExec=$LAUNCHER
Icon=$ICON_FILE
Terminal=false
Categories=System;Settings;
Keywords=Arch;updates;restore;maintenance;system;
StartupNotify=true
StartupWMClass=arch-manager-gui
EOF2
chmod +x "$DESKTOP_FILE"

if [[ -n "$DESKTOP_DIR" && -d "$DESKTOP_DIR" ]]; then
    install -m 0755 "$DESKTOP_FILE" "$DESKTOP_DIR/Arch Manager — новая версия.desktop"
    touch "$DESKTOP_DIR/Arch Manager — новая версия.desktop"
fi

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
command -v kbuildsycoca6 >/dev/null 2>&1 \
    && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

echo "Иконка новой версии восстановлена: $ICON_FILE"
