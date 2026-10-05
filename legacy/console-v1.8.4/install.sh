#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$APP_DIR"
RECOMMENDED_DIR="/data/Projects/Скрипты/Arch Manager"

if [[ ! -f /etc/arch-release ]]; then
    echo "Ошибка: Arch Linux не обнаружен."
    exit 1
fi

echo "Arch Manager v1.8.4 — установка"
echo "Проект: $TARGET_DIR"
if [[ "$TARGET_DIR" != "$RECOMMENDED_DIR" ]]; then
    echo "Рекомендуемое место: $RECOMMENDED_DIR"
    echo "Установщик продолжит работу и из текущей папки."
fi

echo
missing=()
command -v dialog >/dev/null 2>&1 || missing+=(dialog)
command -v checkupdates >/dev/null 2>&1 || missing+=(pacman-contrib)
command -v konsole >/dev/null 2>&1 || missing+=(konsole)
command -v xdg-user-dir >/dev/null 2>&1 || missing+=(xdg-user-dirs)
command -v bsdtar >/dev/null 2>&1 || missing+=(libarchive)
command -v btrfs >/dev/null 2>&1 || missing+=(btrfs-progs)
command -v snapper >/dev/null 2>&1 || missing+=(snapper)

if ((${#missing[@]})); then
    echo "Не хватает официальных пакетов: ${missing[*]}"
    echo "Они будут установлены через pacman."
    echo
    sudo pacman -S --needed "${missing[@]}"
else
    echo "Официальные зависимости уже установлены."
fi

if ! command -v yay >/dev/null 2>&1; then
    echo
    echo "ВНИМАНИЕ: yay не найден."
    echo "Основные функции Arch Manager будут работать, но AUR — нет."
fi

chmod +x "$TARGET_DIR/arch-manager.sh" "$TARGET_DIR/check-env.sh" "$TARGET_DIR/install.sh"
[[ -f "$TARGET_DIR/arch-recovery.sh" ]] && chmod +x "$TARGET_DIR/arch-recovery.sh"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[[ -n "$DESKTOP_DIR" ]] || DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR" "$HOME/.local/share/applications"

DESKTOP_FILE="$HOME/.local/share/applications/arch-manager.desktop"
cat >"$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Arch Manager
Comment=Обновления, обслуживание и снапшоты Arch Linux
Exec=konsole -e "$TARGET_DIR/arch-manager.sh"
Icon=$TARGET_DIR/assets/arch-manager.svg
Terminal=false
Categories=System;Settings;
Keywords=Arch;pacman;yay;updates;AUR;cleanup;maintenance;Btrfs;Snapper;snapshot;
StartupNotify=true
DESKTOP
chmod +x "$DESKTOP_FILE"

cp -f "$DESKTOP_FILE" "$DESKTOP_DIR/Arch Manager.desktop"
chmod +x "$DESKTOP_DIR/Arch Manager.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 >/dev/null 2>&1 || true
fi

# Подготавливаем аварийный помощник на /data и /boot, если новый файл присутствует.
if [[ -f "$TARGET_DIR/arch-recovery.sh" ]]; then
    echo
    echo "Подготовка аварийного помощника восстановления..."
    if sudo -v; then
        DATA_DEV="$(findmnt -rn -M /data -o SOURCE 2>/dev/null || true)"
        DATA_DEV="${DATA_DEV%%[*}"
        BOOT_DEV="$(findmnt -rn -M /boot -o SOURCE 2>/dev/null || true)"
        BOOT_DEV="${BOOT_DEV%%[*}"
        RECOVERY_NOTE="$(mktemp)"
        trap 'rm -f "$RECOVERY_NOTE"' EXIT
        cat >"$RECOVERY_NOTE" <<NOTE
ARCH MANAGER — ЗАПАСНОЕ АВАРИЙНОЕ ВОССТАНОВЛЕНИЕ

В v1.8.4 доступны два безопасных способа:
1) локальное восстановление без USB;
2) отдельная аварийная флешка.
Оба доступны через Arch Manager → Снапшоты → Восстановление системы.

Если специальной флешки нет, загрузитесь с установочной флешки Arch Linux
и выполните одну строку:
${DATA_DEV:+Через /data: mkdir -p /mnt/recovery && mount $DATA_DEV /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh}
${BOOT_DEV:+Через /boot: mkdir -p /mnt/recovery && mount $BOOT_DEV /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh}
NOTE
        if findmnt -rn -M /data >/dev/null 2>&1; then
            sudo mkdir -p /data/Arch-Recovery
            sudo install -m 0755 "$TARGET_DIR/arch-recovery.sh" /data/Arch-Recovery/restore.sh
            sudo install -m 0755 "$TARGET_DIR/arch-recovery.sh" /data/RESTORE-ARCH.sh
            sudo install -m 0644 "$RECOVERY_NOTE" /data/Arch-Recovery/README.txt
            sudo install -m 0644 "$RECOVERY_NOTE" /data/RESTORE-ARCH.txt
        fi
        if findmnt -rn -M /boot >/dev/null 2>&1; then
            sudo install -m 0755 "$TARGET_DIR/arch-recovery.sh" /boot/RESTORE-ARCH.sh
            sudo install -m 0644 "$RECOVERY_NOTE" /boot/RESTORE-ARCH.txt
        fi
    else
        echo "Не удалось получить права администратора. Копии помощника можно подготовить позже из Arch Manager."
    fi
fi

echo
echo "Готово."
echo "Проект: $TARGET_DIR"
echo "Ярлык:  $DESKTOP_DIR/Arch Manager.desktop"
echo "Меню KDE: Arch Manager"
echo

if [[ "$(findmnt -no FSTYPE / 2>/dev/null || true)" == "btrfs" ]]; then
    echo "Btrfs обнаружен на /."
    if [[ -f /etc/snapper/configs/root ]]; then
        echo "Snapper уже настроен для корня /."
    else
        echo "Snapper установлен, но конфигурация root ещё не создана."
        echo "Откройте Arch Manager → Снапшоты Btrfs / Snapper → Первичная настройка Snapper для /."
    fi
else
    echo "ВНИМАНИЕ: / сейчас не Btrfs — раздел снапшотов будет недоступен."
fi

echo
echo "Восстановление системы:"
echo "  Arch Manager → Снапшоты Btrfs / Snapper → Восстановление системы"
echo "  → Восстановить систему БЕЗ флешки"
echo "  или → Создать / обновить аварийную флешку"
echo
echo "Проверка окружения:"
"$TARGET_DIR/check-env.sh"
