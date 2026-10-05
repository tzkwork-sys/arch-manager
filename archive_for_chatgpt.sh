#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

# Определяем рабочий стол через XDG, чтобы это работало и при русской локализации.
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -z "$DESKTOP_DIR" || ! -d "$DESKTOP_DIR" ]]; then
    if [[ -d "$HOME/Рабочий стол" ]]; then
        DESKTOP_DIR="$HOME/Рабочий стол"
    else
        DESKTOP_DIR="$HOME/Desktop"
    fi
fi

mkdir -p "$DESKTOP_DIR"

ARCHIVE_NAME="Arch_Manager_For_ChatGPT.tar.gz"
ARCHIVE_PATH="$DESKTOP_DIR/$ARCHIVE_NAME"
TMP_ARCHIVE="${ARCHIVE_PATH}.tmp"

notify() {
    local title="$1"
    local message="$2"

    if command -v notify-send >/dev/null 2>&1; then
        notify-send "$title" "$message" >/dev/null 2>&1 || true
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --passivepopup "$message" 4 --title "$title" >/dev/null 2>&1 || true
    fi
}

cleanup() {
    rm -f -- "$TMP_ARCHIVE"
}
trap cleanup EXIT

notify "Arch Manager" "Собираю архив для ChatGPT…"

rm -f -- "$TMP_ARCHIVE"

# Архивируем проект целиком, но исключаем то, что не нужно для анализа кода:
# резервные копии, Git-метаданные, сборки, кэши, виртуальные окружения,
# временные файлы, логи и ранее созданные архивы/образы.
tar \
    --create \
    --gzip \
    --file="$TMP_ARCHIVE" \
    --directory="$(dirname "$PROJECT_DIR")" \
    --exclude="$PROJECT_NAME/.git" \
    --exclude="$PROJECT_NAME/.git/*" \
    --exclude="$PROJECT_NAME/.chatgpt-backups" \
    --exclude="$PROJECT_NAME/.chatgpt-backups/*" \
    --exclude="$PROJECT_NAME/.idea" \
    --exclude="$PROJECT_NAME/.idea/*" \
    --exclude="$PROJECT_NAME/.vscode" \
    --exclude="$PROJECT_NAME/.vscode/*" \
    --exclude="$PROJECT_NAME/build" \
    --exclude="$PROJECT_NAME/build/*" \
    --exclude="$PROJECT_NAME/build-*" \
    --exclude="$PROJECT_NAME/cmake-build-*" \
    --exclude="$PROJECT_NAME/dist" \
    --exclude="$PROJECT_NAME/dist/*" \
    --exclude="$PROJECT_NAME/.cache" \
    --exclude="$PROJECT_NAME/.cache/*" \
    --exclude="$PROJECT_NAME/__pycache__" \
    --exclude="$PROJECT_NAME/**/__pycache__" \
    --exclude="$PROJECT_NAME/.pytest_cache" \
    --exclude="$PROJECT_NAME/**/.pytest_cache" \
    --exclude="$PROJECT_NAME/.mypy_cache" \
    --exclude="$PROJECT_NAME/**/.mypy_cache" \
    --exclude="$PROJECT_NAME/.ruff_cache" \
    --exclude="$PROJECT_NAME/**/.ruff_cache" \
    --exclude="$PROJECT_NAME/.venv" \
    --exclude="$PROJECT_NAME/.venv/*" \
    --exclude="$PROJECT_NAME/venv" \
    --exclude="$PROJECT_NAME/venv/*" \
    --exclude="$PROJECT_NAME/node_modules" \
    --exclude="$PROJECT_NAME/**/node_modules" \
    --exclude="$PROJECT_NAME/*.tar" \
    --exclude="$PROJECT_NAME/*.tar.gz" \
    --exclude="$PROJECT_NAME/*.tgz" \
    --exclude="$PROJECT_NAME/*.zip" \
    --exclude="$PROJECT_NAME/*.7z" \
    --exclude="$PROJECT_NAME/*.iso" \
    --exclude="$PROJECT_NAME/*.img" \
    --exclude="$PROJECT_NAME/*.log" \
    --exclude="$PROJECT_NAME/**/*.log" \
    --exclude="$PROJECT_NAME/*~" \
    --exclude="$PROJECT_NAME/**/*.pyc" \
    --exclude="$PROJECT_NAME/**/*.pyo" \
    --exclude="$PROJECT_NAME/.DS_Store" \
    --exclude="$PROJECT_NAME/**/.DS_Store" \
    "$PROJECT_NAME"

# Перезаписываем предыдущий архив только после успешной сборки нового.
mv -f -- "$TMP_ARCHIVE" "$ARCHIVE_PATH"

SIZE="$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"
notify "Arch Manager" "Архив для ChatGPT готов: $ARCHIVE_NAME ($SIZE)"

echo
echo "Готово."
echo "Архив:"
echo "  $ARCHIVE_PATH"
echo "Размер: $SIZE"
