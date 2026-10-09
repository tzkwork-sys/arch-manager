#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
LAUNCHER="$LAUNCHER_DIR/arch-manager-gui"
DESKTOP_FILE="$APP_DIR/arch-manager-gui.desktop"
ICON_FILE="$ICON_DIR/arch-manager-gui.svg"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
DESKTOP_SHORTCUT="${DESKTOP_DIR:+$DESKTOP_DIR/Arch Manager.desktop}"

if ! command -v python >/dev/null 2>&1; then
    echo "ОШИБКА: Python не найден."
    exit 1
fi

if ! python -c 'import PySide6' >/dev/null 2>&1; then
    if [[ -f /etc/arch-release ]] && command -v pacman >/dev/null 2>&1; then
        echo "PySide6 не установлен. Устанавливаю официальный пакет pyside6..."
        sudo pacman -Syu --needed pyside6
    else
        echo "ОШИБКА: PySide6 не найден. Установите PySide6 для Python 3."
        exit 1
    fi
fi

mkdir -p "$LAUNCHER_DIR" "$APP_DIR" "$ICON_DIR"
install -m 0644 "$PROJECT_DIR/src/assets/arch-manager.svg" "$ICON_FILE"

printf '#!/usr/bin/env bash\nexec python %q "$@"\n' "$PROJECT_DIR/main.py" > "$LAUNCHER"
chmod +x "$LAUNCHER"

# Используем абсолютный путь к отдельной иконке GUI-версии. Это надёжнее для
# ярлыков Plasma на рабочем столе и не конфликтует с иконкой старой версии.
cat > "$DESKTOP_FILE" <<EOF2
[Desktop Entry]
Type=Application
Version=1.0
Name=Arch Manager
GenericName=Управление системой Arch Linux
Comment=Обновления, точки восстановления и обслуживание Arch Linux
Exec=$LAUNCHER
TryExec=$LAUNCHER
Icon=$ICON_FILE
Terminal=false
Categories=System;
Keywords=Arch;updates;restore;maintenance;system;
StartupNotify=true
StartupWMClass=arch-manager-gui
EOF2
chmod +x "$DESKTOP_FILE"

# Новый GUI получает отдельный ярлык и не заменяет ярлык старой консольной версии.
if [[ -n "$DESKTOP_DIR" && -d "$DESKTOP_DIR" ]]; then
    install -m 0755 "$DESKTOP_FILE" "$DESKTOP_SHORTCUT"
    touch "$DESKTOP_SHORTCUT"
fi

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
command -v kbuildsycoca6 >/dev/null 2>&1 \
    && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

printf '\nГотово. Новая GUI-версия Arch Manager установлена.\n'
printf 'Иконка GUI: %s\n' "$ICON_FILE"
printf 'Запуск из терминала: %s\n' "$LAUNCHER"
printf 'В меню KDE: «Arch Manager».\n'
if [[ -n "$DESKTOP_DIR" && -d "$DESKTOP_DIR" ]]; then
    printf 'Ярлык на рабочем столе: %s\n' "$DESKTOP_SHORTCUT"
fi
