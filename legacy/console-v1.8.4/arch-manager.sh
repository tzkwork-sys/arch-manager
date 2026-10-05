#!/usr/bin/env bash
set -u

APP_NAME="Arch Manager"
APP_VERSION="1.8.4"
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export DIALOGRC="$APP_DIR/dialogrc"
export LC_ALL="${LC_ALL:-C.UTF-8}"

TMP_DIR="${XDG_RUNTIME_DIR:-/tmp}/arch-manager-${UID}"
mkdir -p "$TMP_DIR"
chmod 700 "$TMP_DIR" 2>/dev/null || true

# Размеры интерфейса рассчитываются от текущего размера Konsole.
# На развёрнутом окне основные экраны занимают почти всю доступную площадь,
# а на небольшом терминале автоматически уменьшаются и не выходят за границы.
dialog_init_geometry() {
    local lines cols
    lines=$(tput lines 2>/dev/null || printf '40')
    cols=$(tput cols 2>/dev/null || printf '120')

    [[ "$lines" =~ ^[0-9]+$ ]] || lines=40
    [[ "$cols" =~ ^[0-9]+$ ]] || cols=120
    (( lines >= 18 )) || lines=18
    (( cols >= 60 )) || cols=60

    DLG_LARGE_H=$(( lines - 6 ))
    DLG_LARGE_W=$(( cols - 8 ))

    DLG_MENU_H=$(( lines * 3 / 4 ))
    DLG_MENU_W=$(( cols * 4 / 5 ))
    (( DLG_MENU_H < 18 )) && DLG_MENU_H=18
    (( DLG_MENU_W < 76 )) && DLG_MENU_W=76
    (( DLG_MENU_H > lines - 5 )) && DLG_MENU_H=$(( lines - 5 ))
    (( DLG_MENU_W > cols - 6 )) && DLG_MENU_W=$(( cols - 6 ))

    DLG_MED_H=$(( lines / 2 ))
    DLG_MED_W=$(( cols * 2 / 3 ))
    (( DLG_MED_H < 14 )) && DLG_MED_H=14
    (( DLG_MED_W < 72 )) && DLG_MED_W=72
    (( DLG_MED_H > lines - 5 )) && DLG_MED_H=$(( lines - 5 ))
    (( DLG_MED_W > cols - 6 )) && DLG_MED_W=$(( cols - 6 ))

    DLG_INFO_H=8
    DLG_INFO_W=$(( cols / 2 ))
    (( DLG_INFO_W < 64 )) && DLG_INFO_W=64
    (( DLG_INFO_W > cols - 8 )) && DLG_INFO_W=$(( cols - 8 ))
}

dialog_init_geometry

USER_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/arch-manager"
USER_CONFIG_FILE="$USER_CONFIG_DIR/config"
SNAPSHOT_AUTO_BEFORE_UPDATE=1
LAST_UPDATE_SNAPSHOT_ID=""

cleanup() {
    rm -rf "$TMP_DIR"
    clear 2>/dev/null || true
}
trap cleanup EXIT INT TERM

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

dlg() {
    dialog \
        --clear \
        --no-shadow \
        --backtitle "$APP_NAME v$APP_VERSION" \
        "$@"
}

msg() {
    dialog_init_geometry
    dlg --title "$1" --msgbox "$2" "$DLG_MED_H" "$DLG_MED_W"
}

error_msg() {
    dialog_init_geometry
    dlg --title "Ошибка" --msgbox "$1" "$DLG_MED_H" "$DLG_MED_W"
}

confirm() {
    dialog_init_geometry
    dlg --title "$1" \
        --yes-label "Да" \
        --no-label "Нет" \
        --yesno "$2" "$DLG_MED_H" "$DLG_MED_W"
}

show_text_file() {
    local title="$1"
    local file="$2"
    dialog_init_geometry
    dlg --title "$title" --exit-label "Назад" --textbox "$file" "$DLG_LARGE_H" "$DLG_LARGE_W"
}

pause_terminal() {
    echo
    read -r -p "Нажмите Enter, чтобы вернуться в Arch Manager..." _
}

require_runtime() {
    local missing=()
    for cmd in sudo dialog checkupdates paccache pacdiff pacman systemctl journalctl df uname; do
        need_cmd "$cmd" || missing+=("$cmd")
    done

    if ((${#missing[@]})); then
        printf 'Arch Manager: отсутствуют необходимые команды: %s\n' "${missing[*]}" >&2
        printf 'Запустите: %s/install.sh\n' "$APP_DIR" >&2
        exit 1
    fi
}

snapshot_load_settings() {
    SNAPSHOT_AUTO_BEFORE_UPDATE=1

    if [[ -r "$USER_CONFIG_FILE" ]]; then
        local value
        value=$(sed -n 's/^AUTO_SNAPSHOT_BEFORE_UPDATE=//p' "$USER_CONFIG_FILE" 2>/dev/null | tail -n 1)
        case "$value" in
            0) SNAPSHOT_AUTO_BEFORE_UPDATE=0 ;;
            1) SNAPSHOT_AUTO_BEFORE_UPDATE=1 ;;
        esac
    fi
}

snapshot_save_settings() {
    mkdir -p "$USER_CONFIG_DIR"
    chmod 700 "$USER_CONFIG_DIR" 2>/dev/null || true
    printf 'AUTO_SNAPSHOT_BEFORE_UPDATE=%s\n' "$SNAPSHOT_AUTO_BEFORE_UPDATE" >"$USER_CONFIG_FILE"
    chmod 600 "$USER_CONFIG_FILE" 2>/dev/null || true
}

snapshot_root_is_btrfs() {
    [[ "$(findmnt -no FSTYPE / 2>/dev/null || true)" == "btrfs" ]]
}

snapshot_root_source() {
    findmnt -no SOURCE / 2>/dev/null || true
}

snapshot_root_device() {
    local source
    source=$(snapshot_root_source)
    printf '%s\n' "${source%%[*}"
}

snapshot_root_subvol_name() {
    local source inner
    source=$(snapshot_root_source)
    if [[ "$source" == *"["*"]"* ]]; then
        inner="${source#*[}"
        inner="${inner%]}"
        inner="${inner#/}"
        printf '%s\n' "$inner"
    fi
}

snapshot_root_top_level_is_5() {
    local line
    line=$(btrfs subvolume get-default / 2>/dev/null || true)
    [[ "$line" == ID\ 5* ]]
}

snapshot_store_source() {
    findmnt -rn -M /.snapshots -o SOURCE 2>/dev/null || true
}

snapshot_store_is_separate() {
    local source
    source=$(snapshot_store_source)
    [[ "$source" == *"[/@snapshots]"* ]]
}

snapshot_store_label() {
    local source
    source=$(snapshot_store_source)
    if [[ -z "$source" ]]; then
        printf 'не смонтировано'
    elif [[ "$source" == *"[/@snapshots]"* ]]; then
        printf '@snapshots (отдельный подтом)'
    else
        printf '%s' "$source"
    fi
}

snapshot_layout_supported_for_safe_store() {
    [[ "$(snapshot_root_subvol_name)" == "@" ]]
}

snapshot_config_exists() {
    [[ -f /etc/snapper/configs/root ]]
}

snapshot_ready() {
    snapshot_root_is_btrfs && need_cmd snapper && snapshot_config_exists
}

snapshot_auto_label() {
    if (( SNAPSHOT_AUTO_BEFORE_UPDATE )); then
        printf 'ВКЛ'
    else
        printf 'ВЫКЛ'
    fi
}

snapshot_timeline_label() {
    if systemctl is-enabled --quiet snapper-timeline.timer 2>/dev/null; then
        printf 'ВКЛ'
    else
        printf 'ВЫКЛ'
    fi
}

snapshot_cleanup_label() {
    if systemctl is-enabled --quiet snapper-cleanup.timer 2>/dev/null; then
        printf 'ВКЛ'
    else
        printf 'ВЫКЛ'
    fi
}

snapshot_create_impl() {
    local description="$1"
    local important="${2:-0}"
    local id
    local -a args=(
        --type single
        --cleanup-algorithm number
        --description "$description"
    )

    # Snapper считает снапшот "важным", если userdata содержит important=yes.
    # Такие точки учитываются отдельным лимитом NUMBER_LIMIT_IMPORTANT.
    if [[ "$important" == "1" ]]; then
        args+=(--userdata "important=yes")
    fi

    id=$(sudo snapper -c root create \
        "${args[@]}" \
        --print-number 2>"$TMP_DIR/snapshot-create.err") || return $?

    printf '%s\n' "$id"
}

maybe_create_pre_update_snapshot() {
    local description="$1"
    LAST_UPDATE_SNAPSHOT_ID=""

    (( SNAPSHOT_AUTO_BEFORE_UPDATE )) || return 0

    # Если Snapper ещё не настроен, обновления остаются доступными. Пользователь
    # может включить снапшоты позже из отдельного раздела Arch Manager.
    snapshot_ready || return 0

    dlg --title "Снапшот перед обновлением" \
        --infobox "Создаю точку восстановления перед изменением системы..." "$DLG_INFO_H" "$DLG_INFO_W"

    if ! sudo -v; then
        if confirm "Снапшот не создан" \
            "Не удалось получить права администратора для создания снапшота.\n\nПродолжить обновление БЕЗ точки восстановления?"; then
            return 0
        fi
        return 1
    fi

    local id rc=0
    id=$(snapshot_create_impl "$description" 1) || rc=$?
    if (( rc == 0 )) && [[ "$id" =~ ^[0-9]+$ ]]; then
        LAST_UPDATE_SNAPSHOT_ID="$id"
        return 0
    fi

    local details=""
    [[ -s "$TMP_DIR/snapshot-create.err" ]] && details=$(tail -n 10 "$TMP_DIR/snapshot-create.err")

    if confirm "Снапшот не создан" \
        "Snapper не смог создать точку восстановления.\n\n${details:-Код ошибки: $rc}\n\nПродолжить обновление БЕЗ снапшота?"; then
        return 0
    fi

    return 1
}

snapshot_is_important() {
    local userdata="${1:-}"
    [[ ",$userdata," == *",important=yes,"* ]]
}

snapshot_userdata_with_importance() {
    local userdata="${1:-}"
    local wanted="${2:-yes}"
    local part
    local -a parts=()
    local -a kept=()

    IFS=',' read -r -a parts <<<"$userdata"
    for part in "${parts[@]}"; do
        part="${part#"${part%%[![:space:]]*}"}"
        part="${part%"${part##*[![:space:]]}"}"
        [[ -n "$part" ]] || continue
        [[ "$part" == important=* ]] && continue
        kept+=("$part")
    done
    kept+=("important=$wanted")

    local IFS=,
    printf '%s' "${kept[*]}"
}

snapshot_csv_field_clean() {
    local value="${1:-}"
    if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
        value="${value:1:${#value}-2}"
        value="${value//\"\"/\"}"
    fi
    printf '%s' "$value"
}

snapshot_format_bytes() {
    local bytes="${1:-0}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || { printf 'н/д'; return; }

    awk -v b="$bytes" 'BEGIN {
        split("Б КиБ МиБ ГиБ ТиБ", u, " ");
        i=1; v=b+0;
        while (v >= 1024 && i < 5) { v/=1024; i++ }
        if (i == 1) printf "%.0f %s", v, u[i];
        else if (v >= 100) printf "%.0f %s", v, u[i];
        else if (v >= 10) printf "%.1f %s", v, u[i];
        else printf "%.2f %s", v, u[i];
    }'
}

snapshot_exclusive_bytes() {
    local id="$1"
    local path="/.snapshots/$id/snapshot"
    local line exclusive

    [[ "$id" =~ ^[1-9][0-9]*$ ]] || return 1
    sudo test -d "$path" 2>/dev/null || return 1

    line=$(sudo btrfs filesystem du -s --raw "$path" 2>/dev/null | tail -n 1) || return 1
    exclusive=$(awk '{print $2}' <<<"$line")
    [[ "$exclusive" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "$exclusive"
}

snapshot_help() {
    local report="$TMP_DIR/snapshot-help.txt"

    cat >"$report" <<'HELP'
СНАПШОТЫ — КАК ПОЛЬЗОВАТЬСЯ
============================

Что это
-------
Снапшот Btrfs — это точка состояния системы. При создании он не копирует
всю систему целиком: неизменённые блоки остаются общими с текущей системой.
Дополнительное место начинает расходоваться по мере изменения файлов.

Когда создавать
---------------
• перед крупным обновлением системы — Arch Manager делает это автоматически;
• перед удалением большого числа пакетов;
• перед заменой драйверов, загрузчика или важных системных настроек;
• перед экспериментами, после которых может понадобиться возврат.

Когда обычно не нужен
---------------------
Для обычного скачивания файлов, смены обоев, работы в браузере и других
повседневных действий отдельный системный снапшот обычно не требуется.

Важные снапшоты
---------------
Точка с меткой important=yes учитывается Snapper отдельно. Arch Manager
автоматически помечает важными снапшоты перед обновлением. Вручную созданную
точку можно сразу сделать важной или изменить её важность позже.

Место на диске
--------------
В списке Arch Manager показывает «Эксклюзивно» — объём блоков, которые
принадлежат только конкретному снапшоту. Это не полный логический размер
системы: большая часть данных обычно разделяется между текущим состоянием
и несколькими снапшотами.

Чем старше снапшот и чем больше система изменилась после его создания,
тем больше дополнительного места он может удерживать.

Что входит в снапшот
--------------------
Системная точка восстановления относится к корневому подтому Btrfs @.
На проверенной схеме пользователя отдельный /data (ext4) в неё не входит.
/home входит в @, кроме вложенных отдельных подтомов Btrfs.

Важно
-----
Снапшот не заменяет резервную копию на другом диске. При физической
поломке SSD снапшоты на том же диске также могут быть потеряны.

Автоматический откат одной кнопкой намеренно не выполняется: текущая
схема загружает subvol=/@, поэтому восстановление должно учитывать fstab
и загрузочную схему. Раздел «Восстановление системы» позволяет выбрать
конкретную точку и показывает безопасный аварийный порядок действий.
HELP

    show_text_file "Как пользоваться снапшотами" "$report"
}

snapshot_status_report() {
    local report="$TMP_DIR/snapshot-status.txt"
    local list_file="$TMP_DIR/snapshot-status-list.csv"
    local root_source root_fs root_options config_status auto_status timeline_status cleanup_status
    local root_used root_free root_percent
    local id date userdata description safe_description exclusive exclusive_h important_label total_exclusive_label
    local snapshot_count=0 important_count=0 measured_count=0 total_exclusive=0
    local -a rows=()

    root_source=$(findmnt -no SOURCE / 2>/dev/null || printf 'не определён')
    root_fs=$(findmnt -no FSTYPE / 2>/dev/null || printf 'не определена')
    root_options=$(findmnt -no OPTIONS / 2>/dev/null || printf 'не определены')
    root_used=$(df -hP / 2>/dev/null | awk 'NR==2 {print $3}')
    root_free=$(df -hP / 2>/dev/null | awk 'NR==2 {print $4}')
    root_percent=$(df -hP / 2>/dev/null | awk 'NR==2 {print $5}')

    snapshot_config_exists && config_status="настроен (/etc/snapper/configs/root)" || config_status="не настроен"
    auto_status=$(snapshot_auto_label)
    timeline_status=$(snapshot_timeline_label)
    cleanup_status=$(snapshot_cleanup_label)

    {
        printf 'ARCH MANAGER — СНАПШОТЫ BTRFS\n'
        printf '===============================\n\n'
        printf 'Корень /:\n'
        printf '  источник:               %s\n' "$root_source"
        printf '  файловая система:       %s\n' "$root_fs"
        printf '  занято / свободно:      %s / %s (%s занято)\n' "${root_used:-н/д}" "${root_free:-н/д}" "${root_percent:-н/д}"
        printf '  параметры монтирования: %s\n\n' "$root_options"
        printf 'Snapper:                  %s\n' "$(need_cmd snapper && snapper --version 2>/dev/null | head -n1 || printf 'не установлен')"
        printf 'Конфигурация root:        %s\n' "$config_status"
        printf 'Перед обновлением:        %s\n' "$auto_status"
        printf 'Снапшоты по времени:      %s\n' "$timeline_status"
        printf 'Автоочистка Snapper:      %s\n' "$cleanup_status"
        printf 'Хранилище снапшотов:      %s\n' "$(snapshot_store_label)"
        printf '\n'
    } >"$report"

    if snapshot_ready; then
        dlg --title "Снапшоты" --infobox "Получаю список и измеряю эксклюзивные данные снапшотов..." "$DLG_INFO_H" "$DLG_INFO_W"

        if ! sudo -v; then
            printf 'Не удалось получить права администратора для чтения списка и размера снапшотов.\n' >>"$report"
            show_text_file "Состояние снапшотов" "$report"
            return
        fi

        if ! sudo snapper -c root --csvout --no-headers --separator '|' --iso \
            list --columns number,date,userdata,description \
            >"$list_file" 2>"$TMP_DIR/snapshot-list.err"; then
            {
                printf 'Не удалось прочитать список снапшотов.\n'
                [[ -s "$TMP_DIR/snapshot-list.err" ]] && tail -n 10 "$TMP_DIR/snapshot-list.err"
            } >>"$report"
            show_text_file "Состояние снапшотов" "$report"
            return
        fi

        while IFS='|' read -r id date userdata description; do
            id=$(snapshot_csv_field_clean "$id")
            date=$(snapshot_csv_field_clean "$date")
            userdata=$(snapshot_csv_field_clean "$userdata")
            description=$(snapshot_csv_field_clean "$description")

            [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
            ((snapshot_count+=1))

            if snapshot_is_important "$userdata"; then
                important_label="★ важный"
                ((important_count+=1))
            else
                important_label="обычный"
            fi

            if exclusive=$(snapshot_exclusive_bytes "$id"); then
                exclusive_h=$(snapshot_format_bytes "$exclusive")
                ((total_exclusive+=exclusive))
                ((measured_count+=1))
            else
                exclusive_h="н/д"
            fi

            [[ -n "$date" ]] || date="дата не указана"
            safe_description=$(snapshot_safe_description "$description")
            rows+=("$id"$'\t'"$date"$'\t'"$important_label"$'\t'"$exclusive_h"$'\t'"$safe_description")
        done <"$list_file"

        if (( snapshot_count == 0 )); then
            total_exclusive_label="0 Б"
        elif (( measured_count == 0 )); then
            total_exclusive_label="н/д"
        elif (( measured_count < snapshot_count )); then
            total_exclusive_label="$(snapshot_format_bytes "$total_exclusive") (измерено $measured_count из $snapshot_count)"
        else
            total_exclusive_label="$(snapshot_format_bytes "$total_exclusive")"
        fi

        {
            printf 'СВОДКА\n'
            printf '------\n'
            printf 'Снапшотов:                 %d\n' "$snapshot_count"
            printf 'Важных:                    %d\n' "$important_count"
            printf 'Сумма эксклюзивных данных: %s\n' "$total_exclusive_label"
            printf '\n'
            printf 'Пояснение: «Эксклюзивно» — блоки, принадлежащие только конкретному\n'
            printf 'снапшоту. Общие с системой или другими снапшотами блоки здесь не\n'
            printf 'дублируются, поэтому это корректнее, чем показывать «полный размер».\n\n'
            printf '%-6s %-25s %-11s %-12s %s\n' '№' 'Дата' 'Статус' 'Эксклюзивно' 'Описание'
            printf '%s\n' '----------------------------------------------------------------------------------------------------'
            if ((${#rows[@]} == 0)); then
                printf 'Снапшотов пока нет.\n'
            else
                local row rid rdate rstatus rspace rdesc
                for row in "${rows[@]}"; do
                    IFS=$'\t' read -r rid rdate rstatus rspace rdesc <<<"$row"
                    printf '%-6s %-25s %-11s %-12s %s\n' "$rid" "$rdate" "$rstatus" "$rspace" "$rdesc"
                done
            fi
        } >>"$report"
    else
        {
            printf 'Список снапшотов недоступен.\n'
            if [[ "$root_fs" != "btrfs" ]]; then
                printf 'Причина: корневая файловая система не Btrfs.\n'
            elif ! need_cmd snapper; then
                printf 'Причина: пакет snapper не установлен.\n'
            else
                printf 'Причина: Snapper ещё не настроен для /.\n'
            fi
        } >>"$report"
    fi

    show_text_file "Состояние снапшотов" "$report"
}

snapshot_safe_description() {
    local text="${1:-}"

    # Старые версии Arch Manager могли случайно сохранить в Description
    # управляющие последовательности terminal/dialog. Сам снапшот при этом
    # исправен — повреждены только метаданные описания. В интерфейсе не
    # показываем такие байты как "кракозябры".
    if [[ -z "$text" ]]; then
        printf 'без описания'
        return
    fi

    if [[ "$text" == *$'\\e'* || "$text" == *$'\\r'* || "$text" == *$'\\n'* || "$text" == *'�'* ]]; then
        printf 'повреждённое описание — можно переименовать'
        return
    fi

    # На случай, если ESC уже потерялся, но остался характерный хвост ANSI.
    if [[ "$text" == *'[?1049h'* || "$text" == *'[?25l'* || "$text" == *'[?1h'* ]]; then
        printf 'повреждённое описание — можно переименовать'
        return
    fi

    printf '%s' "$text"
}

snapshot_manual_create() {
    if ! snapshot_root_is_btrfs; then
        error_msg "Корень / сейчас не является Btrfs.\n\nСоздание Btrfs-снапшотов недоступно."
        return
    fi
    if ! need_cmd snapper; then
        error_msg "Команда snapper не найдена.\n\nЗапустите install.sh Arch Manager — он установит пакет snapper."
        return
    fi
    if ! snapshot_config_exists; then
        error_msg "Snapper ещё не настроен для /.\n\nСначала выберите «Первичная настройка Snapper»."
        return
    fi

    local description rc id important_choice important_label
    dialog_init_geometry
    description=$(dlg --title "Создать снапшот" \
        --inputbox "Описание точки восстановления:" "$DLG_MED_H" "$DLG_MED_W" \
        "Ручной снапшот Arch Manager" \
        3>&1 1>&2 2>&3) || return
    [[ -n "$description" ]] || description="Ручной снапшот Arch Manager"

    important_choice=$(dlg \
        --title "Важность снапшота" \
        --ok-label "Создать" \
        --cancel-label "Назад" \
        --menu "Обычная точка подходит для большинства случаев. Важная хранится по отдельному лимиту Snapper и полезна перед рискованными изменениями." \
        "$DLG_MED_H" "$DLG_MED_W" 2 \
        0 "Обычный снапшот" \
        1 "★ Важный снапшот" \
        3>&1 1>&2 2>&3) || return

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    id=$(snapshot_create_impl "$description" "$important_choice")
    rc=$?
    if (( rc == 0 )) && [[ "$id" =~ ^[0-9]+$ ]]; then
        if [[ "$important_choice" == "1" ]]; then
            important_label="важный"
        else
            important_label="обычный"
        fi
        msg "Снапшот создан" "Создан снапшот №$id ($important_label).\n\n$description"
    else
        local details=""
        [[ -s "$TMP_DIR/snapshot-create.err" ]] && details=$(tail -n 10 "$TMP_DIR/snapshot-create.err")
        error_msg "Не удалось создать снапшот.\n\n${details:-snapper завершился с кодом $rc.}"
    fi
}

snapshot_delete() {
    snapshot_ready || {
        error_msg "Удаление недоступно: Snapper не установлен или не настроен для /."
        return
    }

    # Для удаления больше не нужно вручную вводить номер. Получаем только
    # реальные снапшоты из Snapper и показываем их как обычный список выбора.
    # Машиночитаемый CSV нужен, чтобы интерфейс не зависел от локализованных
    # заголовков и оформления таблицы snapper list.
    local list_file="$TMP_DIR/snapshot-delete-list.csv"
    local list_err="$TMP_DIR/snapshot-delete-list.err"
    local id date description label selected
    local menu_height max_label
    local -a items=()
    local -A dates=()
    local -A descriptions=()

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    : >"$list_file"
    : >"$list_err"
    if ! sudo snapper -c root --csvout --no-headers --separator '|' --iso \
        list --columns number,date,description \
        >"$list_file" 2>"$list_err"; then
        error_msg "Не удалось получить список снапшотов.\n\n$(tail -n 10 "$list_err")"
        return
    fi

    while IFS='|' read -r id date description; do
        # CSV при необходимости заключает поля в двойные кавычки. Для наших
        # полей достаточно снять внешние кавычки и восстановить удвоенные.
        for _var in id date description; do
            local _value="${!_var:-}"
            if [[ "$_value" == \"*\" && ${#_value} -ge 2 ]]; then
                _value="${_value:1:${#_value}-2}"
                _value="${_value//\"\"/\"}"
            fi
            printf -v "$_var" '%s' "$_value"
        done

        [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
        [[ -n "$date" ]] || date="дата не указана"
        [[ -n "$description" ]] || description="без описания"

        dates["$id"]="$date"
        descriptions["$id"]="$description"

        label="$date — $(snapshot_safe_description "$description")"
        items+=("$id" "$label")
    done <"$list_file"

    if ((${#items[@]} == 0)); then
        msg "Удаление снапшота" "Удаляемых снапшотов нет.\n\nТекущее состояние №0 удалять нельзя."
        return
    fi

    dialog_init_geometry
    max_label=$(( DLG_LARGE_W - 18 ))
    (( max_label < 40 )) && max_label=40

    # Ограничиваем слишком длинные описания, чтобы список оставался аккуратным.
    local i
    for ((i=1; i<${#items[@]}; i+=2)); do
        if (( ${#items[i]} > max_label )); then
            items[i]="${items[i]:0:max_label-1}…"
        fi
    done

    menu_height=$(( ${#items[@]} / 2 ))
    (( menu_height < 3 )) && menu_height=3
    (( menu_height > DLG_LARGE_H - 8 )) && menu_height=$(( DLG_LARGE_H - 8 ))

    selected=$(dlg \
        --title "Удаление снапшота" \
        --ok-label "Выбрать" \
        --cancel-label "Назад" \
        --menu "Выберите точку восстановления для удаления.\n\nСтрелки — выбор, Enter — продолжить. Текущее состояние №0 здесь не показывается." \
        "$DLG_LARGE_H" "$DLG_LARGE_W" "$menu_height" \
        "${items[@]}" \
        3>&1 1>&2 2>&3) || return

    [[ "$selected" =~ ^[1-9][0-9]*$ ]] || return

    if ! confirm "Подтверждение удаления" \
        "Удалить снапшот №$selected?\n\nДата: ${dates[$selected]}\nОписание: $(snapshot_safe_description "${descriptions[$selected]}")\n\nЭто действие нельзя отменить."; then
        return
    fi

    dlg --title "Удаление снапшота" \
        --infobox "Удаляю снапшот №$selected..." "$DLG_INFO_H" "$DLG_INFO_W"

    if sudo snapper -c root delete "$selected" >"$TMP_DIR/snapshot-delete.log" 2>&1; then
        msg "Готово" \
            "Снапшот №$selected удалён.\n\nДата: ${dates[$selected]}\nОписание: $(snapshot_safe_description "${descriptions[$selected]}")"
    else
        error_msg "Не удалось удалить снапшот №$selected.\n\n$(tail -n 10 "$TMP_DIR/snapshot-delete.log")"
    fi
}

snapshot_rename() {
    snapshot_ready || {
        error_msg "Переименование недоступно: Snapper не установлен или не настроен для /."
        return
    }

    local list_file="$TMP_DIR/snapshot-rename-list.csv"
    local list_err="$TMP_DIR/snapshot-rename-list.err"
    local id date description label selected new_description
    local menu_height max_label
    local -a items=()
    local -A dates=()
    local -A descriptions=()

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    : >"$list_file"
    : >"$list_err"
    if ! sudo snapper -c root --csvout --no-headers --separator '|' --iso \
        list --columns number,date,description \
        >"$list_file" 2>"$list_err"; then
        error_msg "Не удалось получить список снапшотов.\n\n$(tail -n 10 "$list_err")"
        return
    fi

    while IFS='|' read -r id date description; do
        for _var in id date description; do
            local _value="${!_var:-}"
            if [[ "$_value" == \"*\" && ${#_value} -ge 2 ]]; then
                _value="${_value:1:${#_value}-2}"
                _value="${_value//\"\"/\"}"
            fi
            printf -v "$_var" '%s' "$_value"
        done

        [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
        [[ -n "$date" ]] || date="дата не указана"

        dates["$id"]="$date"
        descriptions["$id"]="$description"
        label="$date — $(snapshot_safe_description "$description")"
        items+=("$id" "$label")
    done <"$list_file"

    if ((${#items[@]} == 0)); then
        msg "Переименование снапшота" "Снапшотов для переименования нет."
        return
    fi

    dialog_init_geometry
    max_label=$(( DLG_LARGE_W - 18 ))
    (( max_label < 40 )) && max_label=40

    local i
    for ((i=1; i<${#items[@]}; i+=2)); do
        if (( ${#items[i]} > max_label )); then
            items[i]="${items[i]:0:max_label-1}…"
        fi
    done

    menu_height=$(( ${#items[@]} / 2 ))
    (( menu_height < 3 )) && menu_height=3
    (( menu_height > DLG_LARGE_H - 8 )) && menu_height=$(( DLG_LARGE_H - 8 ))

    selected=$(dlg \
        --title "Переименование снапшота" \
        --ok-label "Выбрать" \
        --cancel-label "Назад" \
        --menu "Выберите точку восстановления.\n\nМожно исправить старое или повреждённое описание — содержимое снапшота при этом не меняется." \
        "$DLG_LARGE_H" "$DLG_LARGE_W" "$menu_height" \
        "${items[@]}" \
        3>&1 1>&2 2>&3) || return

    [[ "$selected" =~ ^[1-9][0-9]*$ ]] || return

    local current_safe
    current_safe=$(snapshot_safe_description "${descriptions[$selected]}")
    [[ "$current_safe" == "повреждённое описание — можно переименовать" ]] && current_safe=""
    [[ "$current_safe" == "без описания" ]] && current_safe=""

    dialog_init_geometry
    new_description=$(dlg \
        --title "Переименовать снапшот №$selected" \
        --inputbox "Дата: ${dates[$selected]}\n\nВведите новое понятное описание:" \
        "$DLG_MED_H" "$DLG_MED_W" "$current_safe" \
        3>&1 1>&2 2>&3) || return

    # Убираем переводы строк: Description в интерфейсе должен быть одной строкой.
    new_description=${new_description//$'\\r'/ }
    new_description=${new_description//$'\\n'/ }
    while [[ "$new_description" == *"  "* ]]; do
        new_description=${new_description//  / }
    done

    if [[ -z "${new_description// /}" ]]; then
        error_msg "Описание не изменено: пустое название использовать не будем."
        return
    fi

    if [[ "$new_description" == *$'\\e'* ]]; then
        error_msg "Описание содержит недопустимый управляющий символ."
        return
    fi

    if ! confirm "Подтверждение" \
        "Изменить только описание снапшота №$selected?\n\nНовое описание:\n$new_description\n\nСодержимое точки восстановления не изменяется."; then
        return
    fi

    if sudo snapper -c root modify --description "$new_description" "$selected" \
        >"$TMP_DIR/snapshot-rename.log" 2>&1; then
        msg "Готово" "Снапшот №$selected переименован.\n\n$new_description"
    else
        error_msg "Не удалось переименовать снапшот №$selected.\n\n$(tail -n 10 "$TMP_DIR/snapshot-rename.log")"
    fi
}

snapshot_apply_recommended_policy() {
    snapshot_config_exists || {
        error_msg "Сначала выполните первичную настройку Snapper."
        return 1
    }

    sudo -v || { error_msg "Не удалось получить права администратора."; return 1; }

    if sudo snapper -c root set-config \
        'NUMBER_CLEANUP=yes' \
        'NUMBER_MIN_AGE=1800' \
        'NUMBER_LIMIT=10' \
        'NUMBER_LIMIT_IMPORTANT=5' \
        'TIMELINE_CREATE=no' \
        'TIMELINE_CLEANUP=yes' \
        'TIMELINE_MIN_AGE=1800' \
        'TIMELINE_LIMIT_HOURLY=5' \
        'TIMELINE_LIMIT_DAILY=7' \
        'TIMELINE_LIMIT_WEEKLY=4' \
        'TIMELINE_LIMIT_MONTHLY=3' \
        'TIMELINE_LIMIT_YEARLY=0' \
        >"$TMP_DIR/snapper-policy.log" 2>&1; then
        sudo systemctl enable --now snapper-cleanup.timer >/dev/null 2>&1 || true
        # Рекомендуемый режим Arch Manager: точки перед обновлениями + ручные.
        # Почасовой timeline по умолчанию не нужен, поэтому держим его выключенным.
        sudo systemctl disable --now snapper-timeline.timer >/dev/null 2>&1 || true
        return 0
    fi

    error_msg "Не удалось применить политику хранения.\n\n$(tail -n 10 "$TMP_DIR/snapper-policy.log")"
    return 1
}

snapshot_setup_separate_store() {
    local source device uuid root_opts snap_opts timestamp fstab_backup top_mount
    local setup_log="$TMP_DIR/snapper-layout-setup.log"

    source=$(snapshot_root_source)
    device=$(snapshot_root_device)
    root_opts=$(findmnt -no OPTIONS / 2>/dev/null || true)

    if [[ -z "$device" || ! -b "$device" ]]; then
        printf 'Не удалось определить блочное устройство корневой Btrfs: %s\n' "$source" >"$setup_log"
        return 1
    fi

    uuid=$(findmnt -no UUID / 2>/dev/null || true)
    if [[ -z "$uuid" ]]; then
        uuid=$(sudo blkid -s UUID -o value "$device" 2>/dev/null || true)
    fi
    if [[ -z "$uuid" ]]; then
        printf 'Не удалось определить UUID устройства %s.\n' "$device" >"$setup_log"
        return 1
    fi

    if grep -Eq '^[[:space:]]*[^#].*[[:space:]]/\.snapshots[[:space:]]' /etc/fstab; then
        printf 'В /etc/fstab уже есть запись для /.snapshots. Автоматическая первичная настройка остановлена.\n' >"$setup_log"
        return 1
    fi

    if [[ -e /.snapshots ]]; then
        printf 'Путь /.snapshots уже существует до настройки. Автоматическая первичная настройка остановлена.\n' >"$setup_log"
        return 1
    fi

    timestamp=$(date +%Y%m%d_%H%M%S)
    fstab_backup="/etc/fstab.arch-manager-before-snapper-${timestamp}.bak"
    top_mount="/run/arch-manager-btrfs-top-${UID}-$$"

    : >"$setup_log"
    {
        printf 'Источник /: %s\n' "$source"
        printf 'Устройство: %s\n' "$device"
        printf 'UUID: %s\n' "$uuid"
        printf 'Root options: %s\n' "$root_opts"
        printf 'Backup fstab: %s\n' "$fstab_backup"
    } >>"$setup_log"

    if ! sudo cp -a /etc/fstab "$fstab_backup" >>"$setup_log" 2>&1; then
        return 1
    fi

    if ! sudo mkdir -p "$top_mount" >>"$setup_log" 2>&1; then
        return 1
    fi
    if ! sudo mount -t btrfs -o subvolid=5 "$device" "$top_mount" >>"$setup_log" 2>&1; then
        sudo rmdir "$top_mount" >/dev/null 2>&1 || true
        return 1
    fi

    if ! sudo btrfs subvolume show "$top_mount/@snapshots" >/dev/null 2>&1; then
        if ! sudo btrfs subvolume create "$top_mount/@snapshots" >>"$setup_log" 2>&1; then
            sudo umount "$top_mount" >/dev/null 2>&1 || true
            sudo rmdir "$top_mount" >/dev/null 2>&1 || true
            return 1
        fi
    fi

    sudo umount "$top_mount" >>"$setup_log" 2>&1 || {
        sudo rmdir "$top_mount" >/dev/null 2>&1 || true
        return 1
    }
    sudo rmdir "$top_mount" >/dev/null 2>&1 || true

    if ! sudo snapper -c root create-config / >>"$setup_log" 2>&1; then
        return 1
    fi

    if ! sudo btrfs subvolume delete /.snapshots >>"$setup_log" 2>&1; then
        return 1
    fi
    if ! sudo mkdir -p /.snapshots >>"$setup_log" 2>&1; then
        return 1
    fi

    snap_opts=$(printf '%s\n' "$root_opts" | tr ',' '\n' | \
        grep -Ev '^(subvol|subvolid)=' | paste -sd, -)
    [[ -n "$snap_opts" ]] || snap_opts="rw,relatime"
    snap_opts="${snap_opts},subvol=/@snapshots"

    if ! printf 'UUID=%s\t/.snapshots\tbtrfs\t%s\t0 0\n' "$uuid" "$snap_opts" | \
        sudo tee -a /etc/fstab >>"$setup_log"; then
        return 1
    fi

    if ! sudo mount /.snapshots >>"$setup_log" 2>&1; then
        return 1
    fi
    sudo chown root:root /.snapshots >>"$setup_log" 2>&1 || true
    sudo chmod 750 /.snapshots >>"$setup_log" 2>&1 || true

    if ! snapshot_store_is_separate; then
        printf 'Проверка не пройдена: /.snapshots не смонтирован из @snapshots.\n' >>"$setup_log"
        return 1
    fi

    return 0
}

snapshot_setup() {
    if ! snapshot_root_is_btrfs; then
        error_msg "Первичная настройка невозможна: / не является Btrfs."
        return
    fi
    if ! need_cmd snapper; then
        error_msg "Пакет snapper не установлен.\n\nЗапустите install.sh Arch Manager и повторите настройку."
        return
    fi

    if snapshot_config_exists; then
        if confirm "Snapper уже настроен" \
            "Конфигурация root уже существует.\n\nХранилище: $(snapshot_store_label)\n\nПрименить рекомендуемую политику хранения Arch Manager и включить служебный таймер очистки?"; then
            if snapshot_apply_recommended_policy; then
                msg "Готово" "Политика хранения обновлена.\n\nСнапшоты по времени выключены.\nТаймер очистки Snapper включён."
            fi
        fi
        return
    fi

    if ! snapshot_layout_supported_for_safe_store; then
        error_msg "Arch Manager обнаружил Btrfs, но текущая разметка не совпадает с проверенной схемой:\n\nкорень должен быть подтомом @, а верхний уровень Btrfs — ID 5.\n\nАвтоматическая настройка остановлена, чтобы не менять Btrfs-разметку вслепую."
        return
    fi

    if ! confirm "Первичная настройка Snapper" \
        "Обнаружена проверенная схема:\n\n/ → подтом Btrfs @\nверхний уровень Btrfs → ID 5\n\nArch Manager создаст отдельный подтом @snapshots на том же уровне, смонтирует его как /.snapshots, добавит запись в /etc/fstab и создаст конфигурацию Snapper для корня /.\n\nПеред изменением /etc/fstab будет сохранена резервная копия.\n\nПродолжить?"; then
        return
    fi

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    dlg --title "Настройка Snapper" \
        --infobox "Создаю отдельный @snapshots и конфигурацию root..." "$DLG_INFO_H" "$DLG_INFO_W"

    if ! snapshot_setup_separate_store; then
        error_msg "Первичная настройка Snapper не завершена.\n\nПодробный журнал:\n$TMP_DIR/snapper-layout-setup.log\n\nПоследние строки:\n$(tail -n 12 "$TMP_DIR/snapper-layout-setup.log" 2>/dev/null)\n\nАвтоматический откат не выполнялся."
        return
    fi

    if ! snapshot_apply_recommended_policy; then
        return
    fi

    sudo systemctl enable --now snapper-cleanup.timer >/dev/null 2>&1 || true

    local first_id=""
    first_id=$(snapshot_create_impl "Arch Manager: после настройки Snapper" 2>/dev/null || true)

    msg "Snapper настроен" \
        "Готово.\n\nХранилище: @snapshots → /.snapshots\nКонфигурация: root\nПолитика хранения применена\nTimeline по умолчанию выключен\nТаймер очистки включён\n\nПервый снапшот: ${first_id:-не создан}.\n\nСнапшоты по времени включаются отдельным пунктом меню."
}

snapshot_toggle_preupdate() {
    if (( SNAPSHOT_AUTO_BEFORE_UPDATE )); then
        SNAPSHOT_AUTO_BEFORE_UPDATE=0
    else
        SNAPSHOT_AUTO_BEFORE_UPDATE=1
    fi
    snapshot_save_settings
    msg "Снапшот перед обновлением" "Режим теперь: $(snapshot_auto_label)."
}

snapshot_toggle_timeline() {
    snapshot_config_exists || {
        error_msg "Сначала выполните первичную настройку Snapper."
        return
    }

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    if systemctl is-enabled --quiet snapper-timeline.timer 2>/dev/null; then
        if sudo snapper -c root set-config 'TIMELINE_CREATE=no' >"$TMP_DIR/timeline-toggle.log" 2>&1 && \
           sudo systemctl disable --now snapper-timeline.timer >>"$TMP_DIR/timeline-toggle.log" 2>&1; then
            msg "Автоматические снапшоты" \
                "Снапшоты по времени выключены.\n\nОстаются:\n• снапшоты перед обновлениями Arch Manager\n• ручные снапшоты\n• автоматическая очистка старых снапшотов."
        else
            error_msg "Не удалось выключить снапшоты по времени.\n\n$(tail -n 10 "$TMP_DIR/timeline-toggle.log")"
        fi
    else
        if sudo snapper -c root set-config \
            'TIMELINE_CREATE=yes' \
            'TIMELINE_CLEANUP=yes' \
            'TIMELINE_MIN_AGE=1800' \
            'TIMELINE_LIMIT_HOURLY=5' \
            'TIMELINE_LIMIT_DAILY=7' \
            'TIMELINE_LIMIT_WEEKLY=4' \
            'TIMELINE_LIMIT_MONTHLY=3' \
            'TIMELINE_LIMIT_YEARLY=0' \
            >"$TMP_DIR/timeline-toggle.log" 2>&1 && \
           sudo systemctl enable --now snapper-timeline.timer >>"$TMP_DIR/timeline-toggle.log" 2>&1; then
            msg "Автоматические снапшоты" \
                "Снапшоты по времени включены.\n\nХранение: до 5 почасовых, 7 ежедневных, 4 еженедельных и 3 ежемесячных."
        else
            error_msg "Не удалось включить снапшоты по времени.\n\n$(tail -n 10 "$TMP_DIR/timeline-toggle.log")"
        fi
    fi
}

snapshot_root_subvol_id() {
    local opts
    opts=$(findmnt -no OPTIONS / 2>/dev/null || true)

    if [[ "$opts" =~ (^|,)subvolid=([0-9]+)(,|$) ]]; then
        printf '%s\n' "${BASH_REMATCH[2]}"
    fi
}

snapshot_fstab_root_options() {
    awk '
        /^[[:space:]]*#/ { next }
        NF >= 4 && $2 == "/" { print $4; exit }
    ' /etc/fstab 2>/dev/null || true
}

snapshot_default_subvol_summary() {
    local line id path

    line=$(btrfs subvolume get-default / 2>/dev/null || true)
    if [[ -z "$line" ]] && sudo -n true 2>/dev/null; then
        line=$(sudo btrfs subvolume get-default / 2>/dev/null || true)
    fi

    if [[ -z "$line" ]]; then
        printf 'не удалось прочитать; для вашей загрузки это не критично'
        return
    fi

    if [[ "$line" =~ ID[[:space:]]+([0-9]+) ]]; then
        id="${BASH_REMATCH[1]}"
    fi

    if [[ "$line" =~ path[[:space:]]+(.+)$ ]]; then
        path="${BASH_REMATCH[1]}"
    fi

    if [[ "${id:-}" == "5" ]]; then
        printf 'ID 5 — верхний уровень Btrfs'
    elif [[ -n "${id:-}" && -n "${path:-}" ]]; then
        printf 'ID %s — %s' "$id" "$path"
    elif [[ -n "${id:-}" ]]; then
        printf 'ID %s' "$id"
    else
        printf '%s' "$line"
    fi
}

snapshot_restore_overview() {
    local report="$TMP_DIR/snapshot-restore-overview.txt"
    local root_name root_id root_source root_opts fstab_opts store_source default_summary
    local root_ok="НЕТ" root_name_ok="НЕТ" store_ok="НЕТ" fstab_ok="НЕТ" snapper_ok="НЕТ"
    local ready="НЕТ"

    root_name=$(snapshot_root_subvol_name)
    root_id=$(snapshot_root_subvol_id)
    root_source=$(snapshot_root_source)
    root_opts=$(findmnt -no OPTIONS / 2>/dev/null || printf 'не определены')
    fstab_opts=$(snapshot_fstab_root_options)
    store_source=$(snapshot_store_source)
    default_summary=$(snapshot_default_subvol_summary)

    snapshot_root_is_btrfs && root_ok="ДА"
    [[ "$root_name" == "@" ]] && root_name_ok="ДА"
    snapshot_store_is_separate && store_ok="ДА"
    [[ ",$fstab_opts," == *",subvol=/@,"* || ",$fstab_opts," == *",subvol=@,"* ]] && fstab_ok="ДА"
    snapshot_config_exists && snapper_ok="ДА"

    if [[ "$root_ok" == "ДА" && "$root_name_ok" == "ДА" && "$store_ok" == "ДА" && "$fstab_ok" == "ДА" && "$snapper_ok" == "ДА" ]]; then
        ready="ДА"
    fi

    {
        printf 'ВОССТАНОВЛЕНИЕ СИСТЕМЫ\n'
        printf '======================\n\n'

        printf 'Что сейчас используется\n'
        printf '-----------------------\n'
        printf 'Корневая файловая система: Btrfs — %s\n' "$root_ok"
        printf 'Текущий корневой подтом:   %s' "${root_name:-не удалось определить}"
        [[ -n "$root_id" ]] && printf ' (ID %s)' "$root_id"
        printf '\n'
        printf 'Источник корня:            %s\n' "${root_source:-не определён}"
        printf 'Хранилище точек:           %s\n' "${store_source:-не смонтировано}"
        printf 'Подтом по умолчанию:       %s\n\n' "$default_summary"

        printf 'Проверка готовности\n'
        printf '-------------------\n'
        printf 'Корень работает из @:                  %s\n' "$root_name_ok"
        printf 'Точки хранятся отдельно в @snapshots:  %s\n' "$store_ok"
        printf '/etc/fstab явно загружает @:            %s\n' "$fstab_ok"
        printf 'Snapper настроен для корня /:           %s\n' "$snapper_ok"
        printf '\n'
        printf 'Готовность к аварийному восстановлению: %s\n\n' "$ready"

        printf 'Пояснение простыми словами\n'
        printf '--------------------------\n'
        printf 'Подтом Btrfs — это отдельная область внутри одной файловой системы.\n'
        printf 'У вас рабочая Arch Linux находится в подтоме @, а точки восстановления\n'
        printf 'хранятся отдельно в @snapshots. Поэтому сохранённые точки не исчезнут,\n'
        printf 'если при аварийном восстановлении потребуется заменить @.\n\n'

        printf '«Подтом по умолчанию» — служебная настройка Btrfs. В вашей схеме она\n'
        printf 'не выбирает, что загружать: /etc/fstab явно указывает подтом @.\n'
        printf 'Поэтому даже если это значение не удаётся прочитать, обычная загрузка\n'
        printf 'системы от этого не ломается.\n\n'

        printf 'Почему восстановление всё равно требует перезагрузки\n'
        printf '--------------------------------------------------\n'
        printf 'Работающая система сама находится внутри @. Безопасно заменить этот\n'
        printf 'подтом, пока он используется, нельзя. Поэтому Arch Manager не меняет\n'
        printf 'корень «на ходу», а перезагружает компьютер в автономную среду.\n\n'

        printf 'Теперь доступны два безопасных пути: локальное восстановление без USB\n'
        printf 'и отдельная аварийная флешка. Оба запускают один и тот же проверенный\n'
        printf 'arch-recovery.sh, сохраняют старый @ и учитывают отдельный /boot.\n\n'

        printf 'Служебные параметры корня:\n%s\n' "$root_opts"
    } >"$report"

    show_text_file "Проверка восстановления" "$report"
}

snapshot_restore_select() {
    local action="${1:-menu}"
    snapshot_ready || {
        error_msg "Выбор точки восстановления недоступен.\n\nSnapper не установлен или ещё не настроен для корня /."
        return
    }

    local list_file="$TMP_DIR/snapshot-restore-list.csv"
    local list_err="$TMP_DIR/snapshot-restore-list.err"
    local id date userdata description label selected status
    local menu_height max_label
    local -a items=()
    local -A dates=()
    local -A descriptions=()
    local -A statuses=()

    sudo -v || {
        error_msg "Не удалось получить права администратора.\n\nСписок точек восстановления не был прочитан."
        return
    }

    : >"$list_file"
    : >"$list_err"

    if ! sudo snapper -c root --csvout --no-headers --separator '|' --iso \
        list --columns number,date,userdata,description \
        >"$list_file" 2>"$list_err"; then
        error_msg "Не удалось получить список точек восстановления.\n\n$(tail -n 10 "$list_err")"
        return
    fi

    while IFS='|' read -r id date userdata description; do
        id=$(snapshot_csv_field_clean "$id")
        date=$(snapshot_csv_field_clean "$date")
        userdata=$(snapshot_csv_field_clean "$userdata")
        description=$(snapshot_csv_field_clean "$description")

        [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
        [[ -n "$date" ]] || date="дата не указана"

        if snapshot_is_important "$userdata"; then
            status="★ важная"
        else
            status="обычная"
        fi

        dates["$id"]="$date"
        descriptions["$id"]="$description"
        statuses["$id"]="$status"

        label="$date — $status — $(snapshot_safe_description "$description")"
        items+=("$id" "$label")
    done <"$list_file"

    if ((${#items[@]} == 0)); then
        msg "Восстановление системы" \
            "Сохранённых точек восстановления пока нет.\n\nЭто не связано с наличием или отсутствием изменений: если точка существует, она должна появиться здесь."
        return
    fi

    dialog_init_geometry
    max_label=$(( DLG_LARGE_W - 18 ))
    (( max_label < 40 )) && max_label=40

    local i
    for ((i=1; i<${#items[@]}; i+=2)); do
        if (( ${#items[i]} > max_label )); then
            items[i]="${items[i]:0:max_label-1}…"
        fi
    done

    menu_height=$(( ${#items[@]} / 2 ))
    (( menu_height < 3 )) && menu_height=3
    (( menu_height > DLG_LARGE_H - 9 )) && menu_height=$(( DLG_LARGE_H - 9 ))

    selected=$(dlg \
        --title "Выбор точки восстановления" \
        --ok-label "Выбрать" \
        --cancel-label "Назад" \
        --menu "Выберите состояние системы, к которому хотите вернуться.\n\nПоказываются все сохранённые точки — даже если между ними почти нет изменений. Сам выбор сейчас ничего в системе не меняет." \
        "$DLG_LARGE_H" "$DLG_LARGE_W" "$menu_height" \
        "${items[@]}" \
        3>&1 1>&2 2>&3) || return

    [[ "$selected" =~ ^[1-9][0-9]*$ ]] || return

    if [[ "$action" == "local" ]]; then
        snapshot_recovery_local_start \
            "$selected" \
            "${dates[$selected]}" \
            "${descriptions[$selected]}" \
            "${statuses[$selected]}"
    else
        snapshot_restore_selected_menu \
            "$selected" \
            "${dates[$selected]}" \
            "${descriptions[$selected]}" \
            "${statuses[$selected]}"
    fi
}

snapshot_restore_selected_explain() {
    local id="$1"
    local date="$2"
    local description="$3"
    local status="$4"

    local report="$TMP_DIR/snapshot-restore-selected-explain.txt"

    {
        printf 'ЧТО ПРОИЗОЙДЁТ ПРИ ВОССТАНОВЛЕНИИ\n'
        printf '================================\n\n'
        printf 'Выбрана точка №%s\n' "$id"
        printf 'Дата:        %s\n' "$date"
        printf 'Тип:         %s\n' "$status"
        printf 'Описание:    %s\n\n' "$(snapshot_safe_description "$description")"

        printf '1. Текущая рабочая система @ не будет сразу удалена.\n'
        printf '   Её сначала нужно сохранить под запасным именем, например @.broken-...\n\n'

        printf '2. Из выбранной точки @snapshots/%s/snapshot будет создан новый\n' "$id"
        printf '   рабочий подтом @.\n\n'

        printf '3. /etc/fstab останется с указанием subvol=/@, поэтому при следующей\n'
        printf '   загрузке Arch Linux снова откроет @ — но это уже будет состояние\n'
        printf '   из выбранной точки восстановления.\n\n'

        printf '4. Сами сохранённые точки в @snapshots не удаляются и остаются доступны.\n\n'

        printf '5. Старый @.broken нужно удалять только после нескольких успешных\n'
        printf '   загрузок и проверки, что восстановленная система работает нормально.\n\n'

        printf 'Важно: отдельные вложенные подтома Btrfs не входят внутрь обычной\n'
        printf 'точки @. Поэтому перед заменой Arch Manager советует проверить их\n'
        printf 'отдельно и не выполняет восстановление вслепую одной кнопкой.\n'
    } >"$report"

    show_text_file "Что изменится" "$report"
}

snapshot_restore_why_not_live() {
    local report="$TMP_DIR/snapshot-restore-why-not-live.txt"

    {
        printf 'ПОЧЕМУ НУЖНА ПЕРЕЗАГРУЗКА ДЛЯ ВОССТАНОВЛЕНИЯ\n'
        printf '=======================================\n\n'

        printf 'Сейчас запущенная Arch Linux сама находится внутри подтома @.\n'
        printf 'Именно этот @ требуется заменить при полном восстановлении системы.\n\n'

        printf 'Менять корень, из которого в эту секунду работает система, небезопасно:\n'
        printf 'процессы продолжают читать и записывать файлы, а часть каталогов может\n'
        printf 'быть отдельными подтомами Btrfs.\n\n'

        printf 'Есть штатная команда Snapper для отката, но в вашей схеме /etc/fstab\n'
        printf 'явно указывает subvol=/@. Простая смена «подтома по умолчанию» Btrfs\n'
        printf 'не заставит загрузчик использовать другой путь вместо @.\n\n'

        printf 'Поэтому Arch Manager теперь предлагает локальную автономную загрузку:\n'
        printf 'он заранее выбирает точку, один раз перезагружает компьютер в среду\n'
        printf 'Arch Manager Recovery и только там заменяет @. USB-флешка для этого\n'
        printf 'не нужна. Отдельная аварийная флешка остаётся резервным вариантом.\n'
    } >"$report"

    show_text_file "Почему восстановление не запускается сразу" "$report"
}

snapshot_recovery_source_device() {
    local mountpoint="$1" source
    source=$(findmnt -rn -M "$mountpoint" -o SOURCE 2>/dev/null || true)
    printf '%s\n' "${source%%[*}"
}

snapshot_recovery_parent_disk() {
    local dev="$1" parent
    [[ -n "$dev" ]] || return 0
    dev="${dev%%[*}"
    [[ -b "$dev" ]] || { printf '%s\n' "$dev"; return 0; }
    parent=$(lsblk -no PKNAME "$dev" 2>/dev/null | head -n1 | xargs || true)
    if [[ -n "$parent" ]]; then
        printf '/dev/%s\n' "$parent"
    else
        printf '%s\n' "$dev"
    fi
}

snapshot_recovery_flash_forbidden_disks() {
    local src parent
    for mp in / /data /boot; do
        src=$(snapshot_recovery_source_device "$mp")
        [[ -n "$src" ]] || continue
        parent=$(snapshot_recovery_parent_disk "$src")
        [[ -n "$parent" ]] && printf '%s\n' "$parent"
    done | awk '!seen[$0]++'
}

snapshot_recovery_flash_find_connected() {
    local dev label type size resolved
    local -A seen=()

    # Не используем обычный blkid без root: для недавно записанного USB он
    # может читать только старый кэш и не увидеть новую метку ISO9660.
    # lsblk получает LABEL через udev и работает для обычного пользователя.
    while read -r dev label type; do
        [[ -n "$dev" && "$label" == "ARCH_MANAGER_RECOVERY" ]] || continue
        [[ "$type" == "disk" || "$type" == "part" ]] || continue
        [[ -z "${seen[$dev]+x}" ]] || continue
        seen["$dev"]=1
        size=$(lsblk -dnpo SIZE "$dev" 2>/dev/null | head -n1)
        printf '%s|%s\n' "$dev" "${size:-неизвестно}"
    done < <(lsblk -rpno NAME,LABEL,TYPE 2>/dev/null || true)

    # Дополнительный надёжный путь: udev создаёт ссылку /dev/disk/by-label/...
    # даже когда blkid-кэш текущего пользователя ещё не обновился.
    if [[ -e /dev/disk/by-label/ARCH_MANAGER_RECOVERY ]]; then
        resolved=$(readlink -f /dev/disk/by-label/ARCH_MANAGER_RECOVERY 2>/dev/null || true)
        if [[ -n "$resolved" && -b "$resolved" && -z "${seen[$resolved]+x}" ]]; then
            size=$(lsblk -dnpo SIZE "$resolved" 2>/dev/null | head -n1)
            printf '%s|%s\n' "$resolved" "${size:-неизвестно}"
        fi
    fi
}

snapshot_recovery_flash_status() {
    local report="$TMP_DIR/recovery-flash-status.txt"
    local iso="/data/Arch-Recovery/Arch-Manager-Recovery.iso"
    local meta="/data/Arch-Recovery/Arch-Manager-Recovery.meta"
    local connected
    connected=$(snapshot_recovery_flash_find_connected || true)

    {
        printf 'АВАРИЙНАЯ ФЛЕШКА ARCH MANAGER\n'
        printf '==============================\n\n'
        printf 'Назначение: отдельная загрузочная флешка только для восстановления.\n'
        printf 'После загрузки помощник запускается автоматически.\n\n'
        if need_cmd mkarchiso; then
            printf 'Средство создания: ГОТОВО (пакет archiso установлен).\n'
        else
            printf 'Средство создания: пока не установлено.\n'
            printf 'Arch Manager предложит установить официальный пакет archiso при создании.\n'
        fi
        if [[ -f "$iso" ]]; then
            printf 'Последний образ:    %s\n' "$iso"
            printf 'Размер образа:      %s\n' "$(du -h "$iso" 2>/dev/null | awk '{print $1}')"
        else
            printf 'Последний образ:    ещё не создан.\n'
        fi
        [[ -f "$meta" ]] && printf 'Сведения об образе: %s\n' "$meta"
        printf '\n'
        if [[ -n "$connected" ]]; then
            printf 'Подключённая аварийная флешка найдена:\n'
            while IFS='|' read -r dev size; do
                [[ -n "$dev" ]] || continue
                printf '  %s  размер %s\n' "$dev" "$size"
            done <<<"$connected"
            printf '\nМожно сразу загружаться с неё через меню загрузки компьютера.\n'
        else
            printf 'Сейчас флешка с меткой ARCH_MANAGER_RECOVERY не обнаружена.\n'
            printf 'Это нормально, если она не подключена.\n'
        fi
    } >"$report"

    show_text_file "Проверка аварийной флешки" "$report"
}

snapshot_recovery_flash_build_iso() {
    local helper="$APP_DIR/arch-recovery.sh"
    local releng_profile="/usr/share/archiso/configs/releng"
    local root="/data/Arch-Recovery"
    local build="$root/flash-build"
    local profile="$build/profile"
    local work="$build/work"
    local out="$build/out"
    local stable_iso="$root/Arch-Manager-Recovery.iso"
    local meta="$root/Arch-Manager-Recovery.meta"
    local build_log="$root/mkarchiso-last.log"
    local helper_hash helper_version archiso_version meta_version meta_hash meta_helper_version meta_archiso_version meta_iso_hash current_iso=""
    local profile_arch packages_file bootstrap_file profile_pacman bootmodes_dump
    local pkg mkarchiso_rc current_iso_hash

    [[ -f "$helper" ]] || { error_msg "Не найден $helper."; return 1; }
    bash -n "$helper" 2>"$TMP_DIR/recovery-helper-syntax.err" || {
        error_msg "Помощник восстановления содержит ошибку.\n\n$(cat "$TMP_DIR/recovery-helper-syntax.err")"
        return 1
    }

    # Не позволяем собрать флешку с устаревшим помощником. Раньше версия
    # Arch Manager могла уже измениться, а arch-recovery.sh остаться от
    # предыдущего выпуска. Внешне флешка создавалась успешно, но внутри был
    # старый код восстановления.
    helper_version=$(bash "$helper" --version 2>/dev/null | awk '{print $NF}' | tail -n1)
    if [[ -z "$helper_version" || "$helper_version" != "$APP_VERSION" ]]; then
        error_msg "Версии Arch Manager и аварийного помощника не совпадают.\n\nArch Manager: $APP_VERSION\narch-recovery.sh: ${helper_version:-не удалось определить}\n\nОбновите файлы проекта целиком и повторите создание флешки."
        return 1
    fi

    if ! need_cmd mkarchiso; then
        if ! confirm "Нужно установить archiso" \
            "Для создания собственной загрузочной флешки нужен официальный пакет Arch Linux «archiso».\n\nУстановить его сейчас через pacman?"; then
            return 1
        fi
        sudo pacman -S --needed archiso || { error_msg "Не удалось установить archiso."; return 1; }
    fi

    # Используем целиком официальный профиль releng той же версии archiso,
    # которая установлена на компьютере. Он уже согласован с текущим mkarchiso
    # и штатно использует Syslinux для BIOS и systemd-boot для UEFI.
    # Это надёжнее, чем смешивать baseline/releng и вручную переписывать
    # многострочный массив bootmodes.
    [[ -d "$releng_profile" ]] || {
        error_msg "Не найден официальный профиль archiso:\n$releng_profile\n\nПереустановите пакет archiso."
        return 1
    }
    [[ -f "$releng_profile/profiledef.sh" && -f "$releng_profile/pacman.conf" ]] || {
        error_msg "Профиль archiso неполный: отсутствует profiledef.sh или pacman.conf.\n\nПереустановите пакет archiso."
        return 1
    }
    [[ -d "$releng_profile/efiboot" && -d "$releng_profile/syslinux" ]] || {
        error_msg "В официальном профиле archiso не найдены файлы загрузки BIOS/UEFI.\n\nПереустановите пакет archiso."
        return 1
    }

    sudo -v || { error_msg "Не удалось получить права администратора."; return 1; }
    sudo mkdir -p "$root"
    sudo chown "$UID:$(id -g)" "$root" 2>/dev/null || true

    helper_hash=$(sha256sum "$helper" | awk '{print $1}')
    archiso_version=$(pacman -Q archiso 2>/dev/null | awk '{print $2}' | tail -n1)
    [[ -n "$archiso_version" ]] || archiso_version="unknown"

    meta_version=""
    meta_hash=""
    meta_helper_version=""
    meta_archiso_version=""
    meta_iso_hash=""
    if [[ -r "$meta" ]]; then
        meta_version=$(sed -n 's/^APP_VERSION=//p' "$meta" | tail -n1)
        meta_hash=$(sed -n 's/^HELPER_SHA256=//p' "$meta" | tail -n1)
        meta_helper_version=$(sed -n 's/^HELPER_VERSION=//p' "$meta" | tail -n1)
        meta_archiso_version=$(sed -n 's/^ARCHISO_VERSION=//p' "$meta" | tail -n1)
        meta_iso_hash=$(sed -n 's/^ISO_SHA256=//p' "$meta" | tail -n1)
    fi

    # Повторно используем готовый ISO только если совпадает всё, что влияет
    # на аварийную среду, а сам файл ISO не повреждён. Обновление archiso
    # теперь тоже автоматически приводит к пересборке образа.
    if [[ -f "$stable_iso" \
        && "$meta_version" == "$APP_VERSION" \
        && "$meta_hash" == "$helper_hash" \
        && "$meta_helper_version" == "$helper_version" \
        && "$meta_archiso_version" == "$archiso_version" \
        && -n "$meta_iso_hash" ]]; then
        current_iso_hash=$(sha256sum "$stable_iso" | awk '{print $1}')
        if [[ "$current_iso_hash" == "$meta_iso_hash" ]]; then
            RECOVERY_FLASH_ISO="$stable_iso"
            return 0
        fi
    fi

    dlg --title "Arch Manager Recovery" --infobox \
        "Создаю загрузочный образ восстановления.\n\nОн используется и для локального восстановления без USB, и для аварийной флешки. При первом создании это может занять несколько минут." \
        "$DLG_INFO_H" "$DLG_INFO_W"

    sudo rm -rf "$build"
    mkdir -p "$profile" "$work" "$out"

    # Копируем официальный releng-профиль без частичного смешивания с baseline.
    # Так profiledef.sh, загрузчики, список пакетов и служебные файлы всегда
    # соответствуют друг другу и установленной версии archiso.
    cp -a "$releng_profile/." "$profile/"

    if ! bash -n "$profile/profiledef.sh" 2>"$TMP_DIR/recovery-profile-syntax.err"; then
        error_msg "Официальный profiledef.sh не прошёл проверку Bash.\n\n$(cat "$TMP_DIR/recovery-profile-syntax.err")\n\nПереустановите пакет archiso."
        return 1
    fi

    # Читаем способы загрузки через Bash, а не разбираем многострочный массив
    # как обычный текст. Именно текстовый разбор в v1.7.5 мог удалить строки
    # после bootmodes и оставить pacman_conf пустым.
    bootmodes_dump=$(bash -c '
        set -u
        # profiledef.sh рассчитан на загрузку из mkarchiso. Перед source нужно
        # объявить те же типы переменных, которые mkarchiso создаёт заранее.
        # В частности file_permissions — ассоциативный массив. Без этого Bash
        # пытается трактовать ключ /etc/shadow как арифметический индекс.
        [[ -v SOURCE_DATE_EPOCH ]] || printf -v SOURCE_DATE_EPOCH "%(%s)T" -1
        export SOURCE_DATE_EPOCH
        buildmodes=()
        bootmodes=()
        airootfs_image_tool_options=()
        bootstrap_tarball_compression=""
        declare -A file_permissions=()
        source "$1"
        printf "%s\n" "${bootmodes[@]}"
    ' _ "$profile/profiledef.sh" 2>"$TMP_DIR/recovery-profile-read.err") || {
        error_msg "Не удалось безопасно проверить способы загрузки из profiledef.sh.\n\n$(cat "$TMP_DIR/recovery-profile-read.err")"
        return 1
    }

    if ! grep -qxF 'bios.syslinux' <<<"$bootmodes_dump" \
        || ! grep -qxF 'uefi.systemd-boot' <<<"$bootmodes_dump"; then
        error_msg "Установленная версия archiso использует неожиданную схему загрузки.\n\nДля безопасной записи ожидаются:\n• BIOS — Syslinux\n• UEFI — systemd-boot\n\nОбновите Arch Manager или archiso перед созданием флешки."
        return 1
    fi

    profile_pacman=$(bash -c '
        set -u
        [[ -v SOURCE_DATE_EPOCH ]] || printf -v SOURCE_DATE_EPOCH "%(%s)T" -1
        export SOURCE_DATE_EPOCH
        buildmodes=()
        bootmodes=()
        airootfs_image_tool_options=()
        bootstrap_tarball_compression=""
        declare -A file_permissions=()
        source "$1"
        printf "%s" "${pacman_conf:-}"
    ' _ "$profile/profiledef.sh" 2>"$TMP_DIR/recovery-profile-read.err") || {
        error_msg "Не удалось безопасно прочитать настройку pacman из profiledef.sh.\n\n$(cat "$TMP_DIR/recovery-profile-read.err")"
        return 1
    }
    if [[ -z "$profile_pacman" ]]; then
        error_msg "В profiledef.sh не указан pacman.conf. Сборка остановлена до запуска mkarchiso."
        return 1
    fi
    if [[ "$profile_pacman" == /* ]]; then
        [[ -f "$profile_pacman" ]] || {
            error_msg "Не найден pacman.conf, указанный профилем:\n$profile_pacman"
            return 1
        }
    else
        [[ -f "$profile/$profile_pacman" ]] || {
            error_msg "Не найден pacman.conf профиля:\n$profile/$profile_pacman"
            return 1
        }
    fi

    profile_arch=$(uname -m)
    if [[ -f "$profile/packages.$profile_arch" ]]; then
        packages_file="$profile/packages.$profile_arch"
    elif [[ -f "$profile/packages" ]]; then
        packages_file="$profile/packages"
    else
        error_msg "Не найден список пакетов archiso для архитектуры $profile_arch.\n\nСборка остановлена до запуска mkarchiso."
        return 1
    fi

    # mkarchiso v90 разрешает общий bootstrap_packages или вариант с архитектурой.
    # Проверяем его заранее, чтобы вместо непонятного «realpath: ''» показать
    # нормальную причину.
    if [[ -f "$profile/bootstrap_packages.$profile_arch" ]]; then
        bootstrap_file="$profile/bootstrap_packages.$profile_arch"
    elif [[ -f "$profile/bootstrap_packages" ]]; then
        bootstrap_file="$profile/bootstrap_packages"
    else
        error_msg "Не найден служебный список bootstrap_packages в профиле archiso.\n\nПереустановите пакет archiso."
        return 1
    fi

    # В releng большинство этих пакетов уже есть; недостающие добавляем без дублей.
    for pkg in btrfs-progs arch-install-scripts bash coreutils util-linux findutils grep sed gawk tar terminus-font; do
        grep -qxF "$pkg" "$packages_file" 2>/dev/null || printf '%s\n' "$pkg" >>"$packages_file"
    done

    mkdir -p "$profile/airootfs/usr/local/bin" "$profile/airootfs/etc"
    install -m 0755 "$helper" "$profile/airootfs/usr/local/bin/arch-manager-recovery"
    printf '%s\n' "$APP_VERSION" >"$profile/airootfs/etc/arch-manager-recovery-version"

    # Не полагаемся на login shell root. В официальном releng root может
    # запускаться не через Bash, поэтому .bash_profile не гарантирует автозапуск.
    # Вместо этого tty1 напрямую запускает наш консольный launcher через
    # drop-in стандартного getty@tty1.service. Это одинаково работает
    # независимо от того, какой shell указан у root в ArchISO.
    cat >"$profile/airootfs/usr/local/bin/arch-manager-recovery-console" <<'EOF_CONSOLE'
#!/usr/bin/env bash
set +e
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

while true; do
    clear 2>/dev/null || true
    printf '%s\n' '=============================================='
    printf '%s\n' '       ARCH MANAGER — ВОССТАНОВЛЕНИЕ'
    printf '%s\n' '=============================================='
    if grep -qw 'arch_manager_local=1' /proc/cmdline 2>/dev/null; then
        printf '%s\n\n' 'Локальная среда восстановления запущена без USB-флешки.'
    else
        printf '%s\n\n' 'Аварийная среда восстановления готова.'
    fi

    if [[ ! -r /usr/local/bin/arch-manager-recovery ]]; then
        printf '%s\n' 'ОШИБКА: /usr/local/bin/arch-manager-recovery не найден или не читается.'
        printf '%s\n' 'Открываю командную строку для диагностики.'
        exec /bin/bash -l
    fi

    # Запускаем явно через Bash. Это дополнительная страховка: даже если права
    # файла когда-либо снова будут сброшены до 0644, сам помощник всё равно
    # сможет запуститься. Нормальные права 0755 также задаются в profiledef.sh.
    /usr/bin/bash /usr/local/bin/arch-manager-recovery
    rc=$?
    printf '\nПомощник завершил работу (код %s).\n' "$rc"
    printf '%s\n' '1 — запустить помощник ещё раз'
    printf '%s\n' '2 — открыть командную строку'
    printf '%s\n' '3 — перезагрузить компьютер'
    read -r -p 'Выберите: ' answer || answer=2
    case "$answer" in
        1) continue ;;
        2) exec /bin/bash -l ;;
        3) systemctl reboot ;;
        *) continue ;;
    esac
done
EOF_CONSOLE
    chmod 0755 "$profile/airootfs/usr/local/bin/arch-manager-recovery-console"

    # ВАЖНО: mkarchiso задаёт пользовательским файлам из airootfs права 0644
    # по умолчанию, даже если исходный файл был создан как 0755. Поэтому права
    # обоих наших скриптов нужно явно задать через file_permissions профиля.
    # Иначе в готовом ISO zsh выдаёт «permission denied».
    if ! grep -qF '["/usr/local/bin/arch-manager-recovery"]="0:0:0755"' "$profile/profiledef.sh"; then
        sed -i '/^[[:space:]]*file_permissions=(/a\  ["/usr/local/bin/arch-manager-recovery"]="0:0:0755"\n  ["/usr/local/bin/arch-manager-recovery-console"]="0:0:0755"' "$profile/profiledef.sh"
    fi
    if ! grep -qF '["/usr/local/bin/arch-manager-recovery"]="0:0:0755"' "$profile/profiledef.sh" \
       || ! grep -qF '["/usr/local/bin/arch-manager-recovery-console"]="0:0:0755"' "$profile/profiledef.sh"; then
        error_msg "Не удалось задать исполняемые права Recovery-скриптам в profiledef.sh.\n\nСборка остановлена, чтобы не создавать ISO с ошибкой permission denied."
        return 1
    fi

    mkdir -p "$profile/airootfs/etc/systemd/system/getty@tty1.service.d"
    cat >"$profile/airootfs/etc/systemd/system/getty@tty1.service.d/arch-manager-recovery.conf" <<'EOF_GETTY'
[Service]
ExecStart=
ExecStart=-/usr/bin/bash /usr/local/bin/arch-manager-recovery-console
Type=idle
EOF_GETTY

    # Удаляем возможный старый вариант автологина из предыдущей сборки профиля.
    rm -f "$profile/airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf" 2>/dev/null || true

    # Fallback для ручного запуска login-shell на tty1. Основной автозапуск
    # выполняется systemd/getty выше, поэтому тип shell root больше не важен.
    mkdir -p "$profile/airootfs/root"
    cat >"$profile/airootfs/root/.bash_profile" <<'EOF_PROFILE'
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
EOF_PROFILE

    cat >"$profile/airootfs/etc/motd" <<'EOF_MOTD'
Arch Manager Recovery
Отдельная аварийная среда для восстановления Arch Linux из точек Btrfs.
EOF_MOTD

    cat >"$profile/airootfs/etc/vconsole.conf" <<'EOF_VCONSOLE'
KEYMAP=us
FONT=ter-v16n
EOF_VCONSOLE

    # Собственное имя образа и понятная подпись в меню загрузки.
    sed -i 's/^iso_name=.*/iso_name="arch-manager-recovery"/' "$profile/profiledef.sh" 2>/dev/null || true
    sed -i 's/^iso_label=.*/iso_label="ARCH_MANAGER_RECOVERY"/' "$profile/profiledef.sh" 2>/dev/null || true
    sed -i 's/^iso_publisher=.*/iso_publisher="Arch Manager Recovery"/' "$profile/profiledef.sh" 2>/dev/null || true
    sed -i 's/^iso_application=.*/iso_application="Arch Manager Recovery"/' "$profile/profiledef.sh" 2>/dev/null || true
    grep -RIl -- 'Arch Linux install medium' "$profile" 2>/dev/null | while read -r f; do
        sed -i 's/Arch Linux install medium/Arch Manager Recovery/g' "$f" || true
    done

    # Последняя проверка уже готового профиля перед mkarchiso.
    if ! bash -n "$profile/profiledef.sh"; then
        error_msg "После настройки профиль аварийной среды стал некорректным. Сборка остановлена."
        return 1
    fi

    clear 2>/dev/null || true
    printf 'Arch Manager: создаю аварийный загрузочный образ...\n'
    printf 'Основа: официальный профиль Arch Linux releng той же версии archiso.\n'
    printf 'Загрузка: BIOS через Syslinux, UEFI через systemd-boot. GRUB не требуется.\n'
    printf 'Это выполняется только при первом создании или после обновления помощника.\n\n'

    : >"$build_log"
    sudo mkarchiso -v -w "$work" -o "$out" "$profile" 2>&1 | tee "$build_log"
    mkarchiso_rc=${PIPESTATUS[0]}
    if (( mkarchiso_rc != 0 )); then
        pause_terminal
        error_msg "Не удалось собрать аварийный загрузочный образ.\n\nКод ошибки: $mkarchiso_rc\nЖурнал сборки:\n$build_log\n\nРабочие файлы оставлены в:\n$build"
        return 1
    fi

    current_iso=$(find "$out" -maxdepth 1 -type f -name '*.iso' -print | head -n1)
    [[ -n "$current_iso" && -f "$current_iso" ]] || {
        error_msg "Сборка завершилась, но файл ISO не найден."
        return 1
    }

    sudo install -m 0644 "$current_iso" "$stable_iso"
    sudo chown "$UID:$(id -g)" "$stable_iso" 2>/dev/null || true
    current_iso_hash=$(sha256sum "$stable_iso" | awk '{print $1}')
    {
        printf 'APP_VERSION=%s\n' "$APP_VERSION"
        printf 'HELPER_VERSION=%s\n' "$helper_version"
        printf 'HELPER_SHA256=%s\n' "$helper_hash"
        printf 'ARCHISO_VERSION=%s\n' "$archiso_version"
        printf 'ISO_SHA256=%s\n' "$current_iso_hash"
        printf 'CREATED=%s\n' "$(date -Is)"
        printf 'PROFILE=releng\n'
    } >"$meta"

    sudo rm -rf "$work" "$profile" "$out" 2>/dev/null || true
    RECOVERY_FLASH_ISO="$stable_iso"
    return 0
}

snapshot_recovery_flash_create() {
    local iso target iso_size target_size iso_hash target_hash
    local root_disk data_disk boot_disk dev size type rm tran label menu_h
    local -a forbidden=() items=()

    RECOVERY_FLASH_ISO=""
    snapshot_recovery_flash_build_iso || return
    iso="$RECOVERY_FLASH_ISO"
    [[ -f "$iso" ]] || { error_msg "Аварийный образ не найден: $iso"; return; }

    mapfile -t forbidden < <(snapshot_recovery_flash_forbidden_disks)

    while read -r dev size type rm tran; do
        [[ "$type" == "disk" ]] || continue
        [[ "$rm" == "1" || "$tran" == "usb" ]] || continue
        local skip=0 bad
        for bad in "${forbidden[@]}"; do
            [[ "$dev" == "$bad" ]] && { skip=1; break; }
        done
        (( skip )) && continue
        items+=("$dev" "$size — подключённый съёмный накопитель")
    done < <(lsblk -dpno NAME,SIZE,TYPE,RM,TRAN 2>/dev/null)

    if ((${#items[@]} == 0)); then
        error_msg "Не найдено подходящей флешки.\n\nПодключите USB-флешку и повторите. Системный диск, /data и /boot намеренно исключаются из списка."
        return
    fi

    dialog_init_geometry
    menu_h=$(( ${#items[@]} / 2 ))
    (( menu_h < 3 )) && menu_h=3
    (( menu_h > DLG_LARGE_H - 9 )) && menu_h=$(( DLG_LARGE_H - 9 ))
    target=$(dlg \
        --title "Создать / обновить аварийную флешку" \
        --ok-label "Выбрать" \
        --cancel-label "Назад" \
        --menu "Выберите флешку. В списке не показываются диски, на которых находятся /, /data и /boot.\n\nПосле записи компьютер сможет загрузиться прямо в Arch Manager Recovery — обычная установочная флешка Arch Linux больше не нужна." \
        "$DLG_LARGE_H" "$DLG_LARGE_W" "$menu_h" \
        "${items[@]}" \
        3>&1 1>&2 2>&3) || return

    [[ -b "$target" ]] || { error_msg "Устройство $target больше не найдено."; return; }
    for dev in "${forbidden[@]}"; do
        if [[ "$target" == "$dev" ]]; then
            error_msg "Защитная проверка остановила запись: $target относится к системному диску."
            return
        fi
    done

    size=$(lsblk -dnpo SIZE "$target" 2>/dev/null | head -n1)
    if ! confirm "ВСЕ ДАННЫЕ НА ФЛЕШКЕ БУДУТ УДАЛЕНЫ" \
        "Будет полностью перезаписана флешка:\n\n$target   ${size:-размер не определён}\n\nНа ней останется только автономная среда Arch Manager Recovery.\nВсе нынешние файлы и разделы на этой флешке будут удалены.\n\nПродолжить?"; then
        return
    fi

    iso_size=$(stat -c '%s' "$iso" 2>/dev/null || printf '0')
    target_size=$(sudo blockdev --getsize64 "$target" 2>/dev/null || printf '0')
    if [[ ! "$iso_size" =~ ^[0-9]+$ || ! "$target_size" =~ ^[0-9]+$ || "$iso_size" -le 0 || "$target_size" -lt "$iso_size" ]]; then
        error_msg "Флешка слишком мала для аварийного образа или не удалось определить размер."
        return
    fi

    # Снимаем все разделы выбранной флешки, если рабочий стол успел их подключить.
    while read -r dev label; do
        [[ -n "$label" ]] || continue
        sudo umount "$dev" 2>/dev/null || {
            error_msg "Не удалось отключить $dev. Закройте открытые файлы на флешке и повторите."
            return
        }
    done < <(lsblk -lnpo NAME,MOUNTPOINT "$target" 2>/dev/null)

    clear 2>/dev/null || true
    printf 'ARCH MANAGER — СОЗДАНИЕ АВАРИЙНОЙ ФЛЕШКИ\n'
    printf '========================================\n\n'
    printf 'Флешка: %s\n' "$target"
    printf 'Образ:  %s\n\n' "$iso"
    printf 'Записываю. Не извлекайте флешку...\n\n'

    sudo wipefs -a "$target" >/dev/null 2>&1 || true
    if ! sudo dd if="$iso" of="$target" bs=4M status=progress conv=fsync; then
        pause_terminal
        error_msg "Ошибка записи на $target."
        return
    fi
    sync

    printf '\nПроверяю записанные данные...\n'
    iso_hash=$(sha256sum "$iso" | awk '{print $1}')
    target_hash=$(sudo head -c "$iso_size" "$target" | sha256sum | awk '{print $1}')
    if [[ "$iso_hash" != "$target_hash" ]]; then
        pause_terminal
        error_msg "Проверка не совпала. Флешку нельзя считать готовой — повторите создание."
        return
    fi

    sudo blockdev --rereadpt "$target" 2>/dev/null || true
    if need_cmd udevadm; then
        # Просим udev перечитать новую ISO9660-метку сразу после dd, чтобы
        # пункт «Проверить аварийную флешку» увидел устройство без переподключения.
        sudo udevadm trigger --action=change "$target" 2>/dev/null || true
        udevadm settle 2>/dev/null || true
    fi

    pause_terminal
    msg "Аварийная флешка готова" \
        "Флешка $target успешно создана и проверена.\n\nТеперь аварийное восстановление выглядит так:\n\n1. Вставить эту флешку.\n2. Загрузить компьютер с неё.\n3. Arch Manager Recovery запустится сам.\n4. Выбрать точку восстановления и подтвердить.\n\nИнтернет и обычная установочная флешка Arch Linux для восстановления не нужны."
}

snapshot_recovery_systemd_boot_user_detect() {
    # Без sudo bootctl может не иметь права читать содержимое ESP, но сведения
    # о текущем загрузчике из EFI-переменных обычно доступны. Этого достаточно
    # для информационной проверки и это не должно давать ложное «не найден».
    local status
    need_cmd bootctl || return 1
    status=$(SYSTEMD_PAGER=cat bootctl --no-pager status 2>/dev/null || true)
    grep -qE '^[[:space:]]*Product:[[:space:]]+systemd-boot([[:space:]]|$)' <<<"$status"
}

snapshot_recovery_systemd_boot_root_ready() {
    # Для реальной подготовки локального запуска проверяем ESP уже с root-
    # правами. Явный --esp-path=/boot важен на системах, где bootctl не может
    # корректно исследовать защищённый /boot от обычного пользователя.
    need_cmd bootctl || return 1

    if sudo bootctl --esp-path=/boot is-installed >/dev/null 2>&1; then
        return 0
    fi

    # Резервная проверка нужна для конфигураций с UKI: текущая обычная запись
    # может быть Type #2 (*.efi), а локальный Recovery создаёт временную Type #1
    # запись. systemd-boot поддерживает оба варианта одновременно.
    local status
    status=$(sudo env SYSTEMD_PAGER=cat bootctl --esp-path=/boot --no-pager status 2>/dev/null || true)
    grep -qE '^[[:space:]]*Product:[[:space:]]+systemd-boot([[:space:]]|$)' <<<"$status" \
        && sudo test -f /boot/EFI/systemd/systemd-bootx64.efi
}

snapshot_recovery_local_start() {
    local id="$1" date="$2" description="$3" status="$4"
    local iso iso_dev iso_uuid iso_mount iso_loop root_dev root_uuid boot_dev
    local kernel_path initramfs_path kernel_tmp initramfs_tmp
    local needed_bytes avail_bytes
    local local_dir="/boot/arch-manager-local"
    local entry_dir="/boot/loader/entries"
    local entry_file="$entry_dir/arch-manager-recovery.conf"
    local entry_id="arch-manager-recovery.conf"

    snapshot_root_is_btrfs || {
        error_msg "Локальное восстановление доступно только для корневой Btrfs."
        return
    }
    [[ "$(snapshot_root_subvol_name)" == "@" ]] || {
        error_msg "Локальное восстановление остановлено: текущий корневой подтом не @."
        return
    }
    snapshot_store_is_separate || {
        error_msg "Локальное восстановление требует отдельного @snapshots, смонтированного в /.snapshots."
        return
    }

    for cmd in bootctl blkid bsdtar findmnt install stat df sync readlink; do
        need_cmd "$cmd" || {
            error_msg "Для локального восстановления не найдена команда: $cmd"
            return
        }
    done

    if ! confirm "Восстановить без флешки" \
        "Выбрана точка №$id\nДата: $date\nТип: $status\nОписание: $(snapshot_safe_description "$description")\n\nArch Manager подготовит локальную автономную среду и перезагрузит компьютер ОДИН РАЗ в режим восстановления.\n\nРабочий корень @ будет заменяться только после перезагрузки. Текущий @ сохранится как @.broken-...\n\nПродолжить подготовку?"; then
        return
    fi

    # /boot на этой машине защищён от чтения обычным пользователем. Поэтому
    # все проверки systemd-boot, каталога loader и дальнейшая запись выполняются
    # после получения root-прав, а не через bootctl от обычного пользователя.
    sudo -v || {
        error_msg "Не удалось получить права администратора."
        return
    }

    if ! snapshot_recovery_systemd_boot_root_ready; then
        error_msg "Локальное восстановление сейчас поддерживается для systemd-boot.\n\nПроверка с правами администратора не подтвердила systemd-boot на ESP /boot. Аварийная флешка продолжает работать как раньше."
        return
    fi

    boot_dev=$(findmnt -rn -T /boot -o SOURCE 2>/dev/null || true)
    boot_dev="${boot_dev%%[*}"
    [[ -n "$boot_dev" && -b "$boot_dev" ]] || {
        error_msg "Не удалось определить смонтированный загрузочный раздел /boot.\n\nЛокальное восстановление остановлено до изменения загрузочных файлов."
        return
    }

    # Каталог entries может отсутствовать на системе, которая обычно грузится
    # только через UKI (например arch-linux.efi). Это нормально: создаём его
    # сами. Текущая UKI-запись при этом не меняется.
    if ! sudo mkdir -p "$entry_dir"; then
        error_msg "Не удалось подготовить каталог записей systemd-boot: $entry_dir"
        return
    fi

    # Используем тот же ISO, который создаётся для аварийной флешки. Это важно:
    # локальное и USB-восстановление выполняют абсолютно один и тот же helper.
    snapshot_recovery_flash_build_iso || return
    iso="$RECOVERY_FLASH_ISO"
    [[ -f "$iso" ]] || {
        error_msg "Не найден локальный образ восстановления: $iso"
        return
    }

    root_dev=$(snapshot_root_device)
    [[ -n "$root_dev" && -b "$root_dev" ]] || {
        error_msg "Не удалось определить системный Btrfs-раздел."
        return
    }
    root_uuid=$(blkid -s UUID -o value "$root_dev" 2>/dev/null || true)
    [[ -n "$root_uuid" ]] || {
        error_msg "Не удалось определить UUID системного Btrfs-раздела $root_dev."
        return
    }

    iso_dev=$(findmnt -rn -T "$iso" -o SOURCE 2>/dev/null || true)
    iso_dev="${iso_dev%%[*}"
    iso_mount=$(findmnt -rn -T "$iso" -o TARGET 2>/dev/null || true)
    [[ -n "$iso_dev" && -b "$iso_dev" && -n "$iso_mount" ]] || {
        error_msg "Образ восстановления должен находиться на обычном локальном разделе.\n\nНе удалось определить устройство для:\n$iso"
        return
    }
    iso_uuid=$(blkid -s UUID -o value "$iso_dev" 2>/dev/null || true)
    [[ -n "$iso_uuid" ]] || {
        error_msg "Не удалось определить UUID раздела, на котором лежит аварийный ISO: $iso_dev"
        return
    }

    # Путь img_loop задаётся относительно корня файловой системы, на которой
    # хранится ISO. Для текущей схемы /data это /Arch-Recovery/....
    if [[ "$iso_mount" == "/" ]]; then
        iso_loop="$iso"
    elif [[ "$iso" == "$iso_mount/"* ]]; then
        iso_loop="${iso#"$iso_mount"}"
    else
        error_msg "Не удалось вычислить путь Recovery ISO относительно его раздела."
        return
    fi
    [[ "$iso_loop" == /* ]] || iso_loop="/$iso_loop"

    # ISO не должен лежать внутри того же Btrfs-раздела, корень которого будет
    # переименован. На текущей схеме /data — отдельный ext4-раздел, что идеально.
    if [[ "$(readlink -f "$iso_dev")" == "$(readlink -f "$root_dev")" ]]; then
        error_msg "Локальный образ восстановления находится на том же разделе, который требуется восстанавливать.\n\nДля безопасного локального запуска ISO должен лежать на отдельном разделе (например /data). Используйте аварийную флешку."
        return
    fi

    # Из ISO нужны только ядро и initramfs для временной Type #1 записи
    # systemd-boot. Наличие у обычной системы UKI/Type #2 этому не мешает.
    # Сам SquashFS остаётся на /data и подключается archiso через img_dev/img_loop.
    kernel_path=$(bsdtar -tf "$iso" 2>/dev/null | grep -E '^(\./)?arch/boot/[^/]+/vmlinuz-[^/]+$' | head -n1)
    initramfs_path=$(bsdtar -tf "$iso" 2>/dev/null | grep -E '^(\./)?arch/boot/[^/]+/initramfs-[^/]+\.img$' | head -n1)
    if [[ -z "$kernel_path" || -z "$initramfs_path" ]]; then
        error_msg "В аварийном ISO не найдены ядро или initramfs.\n\nПересоздайте Recovery ISO и повторите попытку."
        return
    fi

    kernel_tmp="$TMP_DIR/local-recovery-vmlinuz"
    initramfs_tmp="$TMP_DIR/local-recovery-initramfs.img"
    if ! bsdtar -xOf "$iso" "$kernel_path" >"$kernel_tmp" \
        || ! bsdtar -xOf "$iso" "$initramfs_path" >"$initramfs_tmp" \
        || [[ ! -s "$kernel_tmp" || ! -s "$initramfs_tmp" ]]; then
        error_msg "Не удалось извлечь ядро и initramfs из Recovery ISO."
        return
    fi

    needed_bytes=$(( $(stat -c '%s' "$kernel_tmp") + $(stat -c '%s' "$initramfs_tmp") + 16777216 ))
    avail_bytes=$(df -B1 --output=avail /boot 2>/dev/null | tail -n1 | tr -d ' ')
    if [[ ! "$avail_bytes" =~ ^[0-9]+$ || "$avail_bytes" -lt "$needed_bytes" ]]; then
        error_msg "На /boot недостаточно свободного места для локальной среды восстановления.\n\nНужно примерно: $((needed_bytes / 1024 / 1024)) MiB\nДоступно: $(( ${avail_bytes:-0} / 1024 / 1024 )) MiB\n\nАварийная флешка остаётся доступна."
        return
    fi

    if ! sudo mkdir -p "$local_dir" "$entry_dir" \
        || ! sudo install -m 0644 "$kernel_tmp" "$local_dir/vmlinuz-linux" \
        || ! sudo install -m 0644 "$initramfs_tmp" "$local_dir/initramfs-linux.img"; then
        error_msg "Не удалось скопировать файлы локальной среды восстановления в /boot."
        return
    fi

    # systemd-boot загружает ядро и initramfs с ESP, а сама ArchISO-среда
    # затем открывает ISO на отдельном разделе через img_dev/img_loop.
    if ! sudo tee "$entry_file" >/dev/null <<EOF_LOCAL_ENTRY
title   Arch Manager — локальное восстановление
linux   /arch-manager-local/vmlinuz-linux
initrd  /arch-manager-local/initramfs-linux.img
options archisobasedir=arch img_dev=/dev/disk/by-uuid/$iso_uuid img_loop=$iso_loop earlymodules=loop arch_manager_local=1 arch_manager_snapshot=$id arch_manager_root_uuid=$root_uuid
EOF_LOCAL_ENTRY
    then
        error_msg "Не удалось создать одноразовую запись systemd-boot."
        return
    fi

    # Проверяем, что systemd-boot действительно видит созданную Type #1 запись.
    # Это также ловит ошибки пути/прав до перезагрузки.
    if ! sudo env SYSTEMD_PAGER=cat bootctl --esp-path=/boot --no-pager list 2>/dev/null \
        | grep -qF "$entry_id"; then
        sudo rm -f "$entry_file" 2>/dev/null || true
        error_msg "systemd-boot не увидел подготовленную запись локального восстановления.\n\nОбычная загрузка не изменена; временная запись удалена."
        return
    fi

    sync

    if confirm "Всё готово" \
        "Локальная среда восстановления подготовлена.\n\nСледующая загрузка — ОДНОРАЗОВО в Arch Manager Recovery.\nТочка: №$id\nСистемный раздел: $root_dev\nISO: $iso\n\nТекущая обычная загрузка через UKI/arch-linux.efi не изменяется: Recovery добавлен отдельной временной записью.\n\nПосле запуска среда ещё раз покажет финальную проверку и попросит подтвердить восстановление пунктом 1.\n\nПерезагрузить компьютер сейчас?"; then
        if ! sudo bootctl --esp-path=/boot set-oneshot "$entry_id" >/dev/null; then
            error_msg "Не удалось назначить локальную среду восстановления для следующей загрузки.\n\nОбычная загрузка не изменена."
            return
        fi
        clear 2>/dev/null || true
        printf 'Arch Manager: перезагрузка в локальную среду восстановления...\n'
        if ! sudo systemctl reboot; then
            sudo bootctl --esp-path=/boot set-oneshot "" >/dev/null 2>&1 || true
            error_msg "Команда перезагрузки завершилась ошибкой. Одноразовая запись восстановления отменена, обычная загрузка сохранена."
        fi
        return
    fi

    msg "Перезагрузка отменена" \
        "Обычная загрузка не изменена. Файлы локальной среды уже подготовлены в /boot и будут переиспользованы при следующем запуске этого пункта.\n\nUSB-флешка не требуется."
}

snapshot_recovery_local_status() {
    local report="$TMP_DIR/local-recovery-status.txt"
    local boot_state="НЕТ" iso_state="НЕТ" iso_source="не определён"
    local current_entry="не определена"
    local root_dev root_uuid data_uuid boot_status

    if need_cmd bootctl; then
        boot_status=$(SYSTEMD_PAGER=cat bootctl --no-pager status 2>/dev/null || true)
        current_entry=$(sed -n 's/^[[:space:]]*Current Entry:[[:space:]]*//p' <<<"$boot_status" | head -n1)
        [[ -n "$current_entry" ]] || current_entry="не определена"

        if snapshot_recovery_systemd_boot_user_detect; then
            boot_state="ДА — systemd-boot"
        elif sudo -n true 2>/dev/null && snapshot_recovery_systemd_boot_root_ready; then
            boot_state="ДА — systemd-boot"
        else
            boot_state="НЕ ПОДТВЕРЖДЁН"
        fi
    fi

    [[ -f /data/Arch-Recovery/Arch-Manager-Recovery.iso ]] && iso_state="ДА"
    if [[ -f /data/Arch-Recovery/Arch-Manager-Recovery.iso ]] && need_cmd findmnt; then
        iso_source=$(findmnt -rn -T /data/Arch-Recovery/Arch-Manager-Recovery.iso -o SOURCE 2>/dev/null || true)
        iso_source="${iso_source%%[*}"
    fi
    root_dev=$(snapshot_root_device)
    if need_cmd blkid; then
        [[ -n "$root_dev" ]] && root_uuid=$(blkid -s UUID -o value "$root_dev" 2>/dev/null || true)
        [[ -n "$iso_source" && -b "$iso_source" ]] && data_uuid=$(blkid -s UUID -o value "$iso_source" 2>/dev/null || true)
    fi

    {
        printf 'ЛОКАЛЬНОЕ ВОССТАНОВЛЕНИЕ БЕЗ USB\n'
        printf '================================\n\n'
        printf 'Загрузчик:                 %s\n' "$boot_state"
        printf 'Текущая запись:            %s\n' "$current_entry"
        if [[ "$current_entry" == *.efi ]]; then
            printf 'Тип обычной загрузки:      UKI / Type #2 — совместимо\n'
        fi
        printf 'Аварийный ISO на /data:    %s\n' "$iso_state"
        printf 'Раздел с ISO:              %s\n' "${iso_source:-не определён}"
        printf 'UUID раздела с ISO:        %s\n' "${data_uuid:-не определён}"
        printf 'Системный Btrfs-раздел:    %s\n' "${root_dev:-не определён}"
        printf 'UUID системного раздела:   %s\n\n' "${root_uuid:-не определён}"
        printf 'Механизм:\n'
        printf '1. Arch Manager сохраняет/обновляет тот же Recovery ISO, что используется для USB.\n'
        printf '2. Ядро и initramfs из ISO копируются в /boot/arch-manager-local/.\n'
        printf '3. Для Recovery создаётся отдельная временная Type #1 запись systemd-boot.\n'
        printf '4. Обычная UKI-запись arch-linux.efi не заменяется и не переписывается.\n'
        printf '5. systemd-boot получает Recovery только как одноразовую следующую загрузку.\n'
        printf '6. Recovery ISO запускается с /data, поэтому рабочий @ не используется.\n'
        printf '7. После восстановления обычная загрузка снова остаётся основной.\n'
    } >"$report"

    show_text_file "Локальное восстановление" "$report"
}

snapshot_recovery_flash_howto() {
    msg "Как пользоваться аварийной флешкой" \
        "Обычный аварийный сценарий:\n\n1. Если Arch Linux перестал нормально загружаться — вставьте специальную аварийную флешку Arch Manager.\n2. В меню загрузки компьютера выберите эту флешку.\n3. Никаких команд вводить не нужно: Arch Manager Recovery запустится автоматически.\n4. Он сам найдёт системный Btrfs-раздел и покажет сохранённые точки.\n5. Выберите нужную точку по дате и описанию и подтвердите восстановление.\n6. После завершения перезагрузите компьютер.\n\nФлешка автономная: для восстановления интернет не нужен. Обычная установочная флешка Arch остаётся только запасным вариантом."
}

snapshot_recovery_legacy_menu() {
    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Запасной способ восстановления" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Этот раздел нужен только если специальная аварийная флешка ещё не создана. Здесь сохраняется прежний способ через обычную установочную флешку Arch Linux." \
            "$DLG_MENU_H" "$DLG_MENU_W" 4 \
            1 "Подготовить копии помощника на /data и /boot" \
            2 "Инструкция для обычной флешки Arch Linux" \
            3 "Проверить копии помощника" \
            4 "Назад" \
            3>&1 1>&2 2>&3) || return
        case "$choice" in
            1) snapshot_recovery_prepare ;;
            2) snapshot_recovery_howto ;;
            3) snapshot_recovery_status ;;
            4) return ;;
        esac
    done
}

snapshot_recovery_make_readme() {
    local out="$1"
    local data_dev boot_dev
    data_dev=$(snapshot_recovery_source_device /data)
    boot_dev=$(snapshot_recovery_source_device /boot)

    {
        printf 'ARCH MANAGER — АВАРИЙНОЕ ВОССТАНОВЛЕНИЕ\n'
        printf '======================================\n\n'
        printf 'Если Arch Linux перестала нормально загружаться:\n\n'
        printf '1. Загрузитесь с обычной установочной флешки Arch Linux.\n'
        printf '2. Запустите одну из команд ниже.\n'
        printf '3. Помощник сам найдёт систему, покажет точки восстановления и попросит выбрать нужную.\n\n'
        printf 'Копия на диске /data:\n'
        if [[ -n "$data_dev" ]]; then
            printf '  mkdir -p /mnt/recovery && mount %s /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n' "$data_dev"
        else
            printf '  /data сейчас не смонтирован — команда не определена.\n\n'
        fi
        printf 'Копия на загрузочном разделе:\n'
        if [[ -n "$boot_dev" ]]; then
            printf '  mkdir -p /mnt/recovery && mount %s /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n' "$boot_dev"
        else
            printf '  /boot сейчас не смонтирован — команда не определена.\n\n'
        fi
        printf 'После запуска больше не нужно вручную вводить команды Btrfs. Помощник:\n'
        printf -- '- найдёт системный Btrfs-раздел;\n'
        printf -- '- покажет все точки восстановления;\n'
        printf -- '- сохранит нынешний @ под запасным именем;\n'
        printf -- '- восстановит выбранную точку;\n'
        printf -- '- перенесёт отдельные вложенные подтома;\n'
        printf -- '- если /boot отдельный, сохранит его копию и синхронизирует ядро и initramfs;\n'
        printf -- '- при ошибке попытается автоматически вернуть исходное состояние.\n\n'
        printf 'Старый @.broken после успешного восстановления не удаляйте сразу.\n'
    } >"$out"
}

snapshot_recovery_prepare() {
    local helper="$APP_DIR/arch-recovery.sh"
    local readme="$TMP_DIR/arch-recovery-readme.txt"
    local data_ok=0 boot_ok=0 data_dev boot_dev

    [[ -f "$helper" ]] || {
        error_msg "В папке Arch Manager не найден файл arch-recovery.sh.\n\nПереустановите или обновите Arch Manager."
        return
    }

    bash -n "$helper" 2>"$TMP_DIR/arch-recovery-check.err" || {
        error_msg "Аварийный помощник не прошёл проверку Bash.\n\n$(cat "$TMP_DIR/arch-recovery-check.err")"
        return
    }

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }
    snapshot_recovery_make_readme "$readme"

    if findmnt -rn -M /data >/dev/null 2>&1; then
        if sudo mkdir -p /data/Arch-Recovery \
            && sudo install -m 0755 "$helper" /data/Arch-Recovery/restore.sh \
            && sudo install -m 0755 "$helper" /data/RESTORE-ARCH.sh \
            && sudo install -m 0644 "$readme" /data/Arch-Recovery/README.txt \
            && sudo install -m 0644 "$readme" /data/RESTORE-ARCH.txt; then
            data_ok=1
        fi
    fi

    if findmnt -rn -M /boot >/dev/null 2>&1; then
        if sudo install -m 0755 "$helper" /boot/RESTORE-ARCH.sh \
            && sudo install -m 0644 "$readme" /boot/RESTORE-ARCH.txt; then
            boot_ok=1
        fi
    fi

    data_dev=$(snapshot_recovery_source_device /data)
    boot_dev=$(snapshot_recovery_source_device /boot)

    local text="Аварийный помощник подготовлен.\n\n"
    if (( data_ok )); then
        text+="✓ Копия на /data: /data/RESTORE-ARCH.sh\n"
        text+="  С флешки Arch Linux:\n  mkdir -p /mnt/recovery && mount ${data_dev:-УСТРОЙСТВО_DATA} /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n"
    else
        text+="✗ Копию на /data создать не удалось или /data не смонтирован.\n\n"
    fi
    if (( boot_ok )); then
        text+="✓ Копия на /boot: /boot/RESTORE-ARCH.sh\n"
        text+="  С флешки Arch Linux:\n  mkdir -p /mnt/recovery && mount ${boot_dev:-УСТРОЙСТВО_BOOT} /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n"
    else
        text+="✗ Копию на /boot создать не удалось или /boot не смонтирован.\n\n"
    fi
    text+="После запуска помощника вручную вводить команды Btrfs уже не потребуется."

    msg "Аварийный помощник" "$text"
}

snapshot_recovery_status() {
    local helper="$APP_DIR/arch-recovery.sh"
    local report="$TMP_DIR/arch-recovery-status.txt"
    local project="НЕТ" data="НЕТ" data_full="НЕТ" boot="НЕТ"
    local data_dev boot_dev

    [[ -f "$helper" ]] && project="ДА"
    [[ -f /data/RESTORE-ARCH.sh ]] && data="ДА"
    [[ -f /data/Arch-Recovery/restore.sh ]] && data_full="ДА"
    [[ -f /boot/RESTORE-ARCH.sh ]] && boot="ДА"

    if [[ "$project" == "ДА" && "$data" == "ДА" ]] && cmp -s "$helper" /data/RESTORE-ARCH.sh 2>/dev/null; then
        data="ДА — актуальная копия"
    elif [[ "$data" == "ДА" ]]; then
        data="ДА — лучше обновить"
    fi

    if [[ "$project" == "ДА" && "$boot" == "ДА" ]] && cmp -s "$helper" /boot/RESTORE-ARCH.sh 2>/dev/null; then
        boot="ДА — актуальная копия"
    elif [[ "$boot" == "ДА" ]]; then
        boot="ДА — лучше обновить"
    fi

    data_dev=$(snapshot_recovery_source_device /data)
    boot_dev=$(snapshot_recovery_source_device /boot)

    {
        printf 'ГОТОВНОСТЬ АВАРИЙНОГО ПОМОЩНИКА\n'
        printf '================================\n\n'
        printf 'В проекте Arch Manager:          %s\n' "$project"
        printf 'Короткая копия на /data:         %s\n' "$data"
        printf 'Полная папка /data/Arch-Recovery: %s\n' "$data_full"
        printf 'Копия на /boot:                  %s\n\n' "$boot"
        printf 'Раздел /data: %s\n' "${data_dev:-не определён}"
        printf 'Раздел /boot: %s\n\n' "${boot_dev:-не определён}"

        if [[ "$data" == "ДА — актуальная копия" || "$boot" == "ДА — актуальная копия" ]]; then
            printf 'ИТОГ: аварийный помощник доступен.\n\n'
        else
            printf 'ИТОГ: выберите «Подготовить / обновить аварийный помощник».\n\n'
        fi

        printf 'Рекомендуется хранить две одинаковые копии: на /data и /boot.\n'
        printf 'Если одна из них окажется недоступна, останется вторая.\n'
    } >"$report"

    show_text_file "Проверка аварийного помощника" "$report"
}

snapshot_recovery_howto() {
    local report="$TMP_DIR/arch-recovery-howto.txt"
    local data_dev boot_dev
    data_dev=$(snapshot_recovery_source_device /data)
    boot_dev=$(snapshot_recovery_source_device /boot)

    {
        printf 'КАК ВОССТАНОВИТЬ СИСТЕМУ — 3 ШАГА\n'
        printf '=================================\n\n'
        printf 'ШАГ 1. Загрузитесь с обычной установочной флешки Arch Linux.\n\n'
        printf 'ШАГ 2. Запустите аварийный помощник одной строкой.\n\n'
        if [[ -n "$data_dev" ]]; then
            printf 'Вариант A — копия на /data:\n\n'
            printf '  mkdir -p /mnt/recovery && mount %s /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n' "$data_dev"
        fi
        if [[ -n "$boot_dev" ]]; then
            printf 'Вариант Б — копия на загрузочном разделе:\n\n'
            printf '  mkdir -p /mnt/recovery && mount %s /mnt/recovery && bash /mnt/recovery/RESTORE-ARCH.sh\n\n' "$boot_dev"
        fi
        printf 'ШАГ 3. Дальше ничего технического вводить не нужно.\n'
        printf 'Помощник сам найдёт систему и покажет список точек восстановления.\n'
        printf 'Выберите номер нужной точки, затем подтвердите восстановление пунктом 1.\n'
        printf 'После завершения можно перезагрузиться.\n\n'
        printf 'Что помощник делает сам\n'
        printf '-----------------------\n'
        printf '• проверяет Btrfs, @ и @snapshots;\n'
        printf '• сохраняет нынешний @ под запасным именем @.broken-ДАТА;\n'
        printf '• создаёт новый @ из выбранной точки;\n'
        printf '• сохраняет отдельные вложенные подтома;\n'
        printf '• учитывает отдельный /boot и пересобирает загрузочные файлы;\n'
        printf '• при ошибке пытается вернуть состояние, которое было до восстановления.\n\n'
        printf 'Старый @.broken не удаляйте сразу после первой успешной загрузки.\n'
    } >"$report"

    show_text_file "Восстановление за 3 шага" "$report"
}

snapshot_restore_point_with_helper() {
    local id="$1" date="$2" description="$3" status="$4"
    local report="$TMP_DIR/snapshot-restore-helper-point-$id.txt"

    {
        printf 'КАК ВОССТАНОВИТЬ ИМЕННО ЭТУ ТОЧКУ\n'
        printf '=================================\n\n'
        printf 'Точка №%s\n' "$id"
        printf 'Дата:     %s\n' "$date"
        printf 'Тип:      %s\n' "$status"
        printf 'Описание: %s\n\n' "$(snapshot_safe_description "$description")"
        printf 'Заранее запоминать команды Btrfs не нужно.\n\n'
        printf '1. Заранее создайте аварийную флешку Arch Manager.\n'
        printf '2. Если Arch Linux перестанет загружаться — загрузитесь с этой флешки.\n'
        printf '3. Помощник запустится автоматически — команды вводить не нужно.\n'
        printf '4. В появившемся списке выберите номер %s.\n' "$id"
        printf '5. Проверьте дату и описание, затем подтвердите восстановление.\n\n'
        printf 'Помощник сам сохранит текущую систему под запасным именем и выполнит\n'
        printf 'необходимые операции. Ручная техническая инструкция оставлена только\n'
        printf 'как запасной вариант, если сам помощник по какой-то причине недоступен.\n'
    } >"$report"

    show_text_file "Точка №$id через аварийный помощник" "$report"
}

snapshot_restore_instructions() {
    local id="$1"
    local date="$2"
    local description="$3"
    local status="$4"
    local device
    local report="$TMP_DIR/snapshot-restore-instructions-$id.txt"

    device=$(snapshot_root_device)
    [[ -n "$device" ]] || device="/dev/ВАШ_РАЗДЕЛ_BTRFS"

    {
        printf 'ТЕХНИЧЕСКАЯ ЗАПАСНАЯ ИНСТРУКЦИЯ\n'
        printf '================================\n\n'
        printf 'Обычно она НЕ нужна: используйте аварийный помощник RESTORE-ARCH.sh.\n'
        printf 'Эта инструкция оставлена только на случай, если помощник недоступен.\n\n'
        printf 'Выбрана точка №%s\n' "$id"
        printf 'Дата:        %s\n' "$date"
        printf 'Тип:         %s\n' "$status"
        printf 'Описание:    %s\n\n' "$(snapshot_safe_description "$description")"
        printf 'Ожидаемый системный раздел: %s\n\n' "$device"
        printf '1. Загрузитесь с установочной флешки Arch Linux.\n'
        printf '2. Смонтируйте верхний уровень Btrfs:\n\n'
        printf '   mount -o subvolid=5 %s /mnt\n\n' "$device"
        printf '3. Проверьте @, @snapshots и выбранную точку:\n\n'
        printf '   btrfs subvolume list /mnt\n'
        printf '   test -d /mnt/@snapshots/%s/snapshot && echo "Точка найдена"\n\n' "$id"
        printf '4. Проверьте вложенные подтома и отдельный /boot.\n'
        printf '   Именно поэтому ручной способ сложнее и не рекомендуется как основной.\n\n'
        printf 'Если RESTORE-ARCH.sh доступен, вернитесь назад и используйте его.\n'
    } >"$report"

    show_text_file "Запасная техническая инструкция" "$report"
}

snapshot_restore_selected_menu() {
    local id="$1"
    local date="$2"
    local description="$3"
    local status="$4"

    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Точка восстановления №$id" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Дата: $date\nТип: $status\nОписание: $(snapshot_safe_description "$description")\n\nЕсли Arch Linux сейчас загружается, точку можно восстановить без USB: Arch Manager один раз перезагрузит компьютер в локальную автономную среду." \
            "$DLG_MENU_H" "$DLG_MENU_W" 5 \
            1 "Восстановить эту точку БЕЗ флешки" \
            2 "Как восстановить эту точку через аварийную флешку" \
            3 "Что именно произойдёт при восстановлении" \
            4 "Запасная техническая инструкция" \
            5 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) snapshot_recovery_local_start "$id" "$date" "$description" "$status" ;;
            2) snapshot_restore_point_with_helper "$id" "$date" "$description" "$status" ;;
            3) snapshot_restore_selected_explain "$id" "$date" "$description" "$status" ;;
            4) snapshot_restore_instructions "$id" "$date" "$description" "$status" ;;
            5) return ;;
        esac
    done
}


snapshot_last_restore_read_field() {
    local file="$1" key="$2"
    sed -n "s/^${key}=//p" "$file" | head -n1
}

snapshot_last_restore_status() {
    local marker="/.snapshots/.arch-manager-recovery/last-restore.env"
    local tmp="$TMP_DIR/last-restore.env"
    local top="$TMP_DIR/last-restore-top"
    local version date device snapshot backup restore_log
    local root_dev root_subvol backup_exists="НЕТ" failed_count nested_count=0
    local safe_delete=0

    if sudo test -r "$marker"; then
        if ! sudo cat "$marker" >"$tmp"; then
            error_msg "Не удалось прочитать $marker"
            return
        fi

        version=$(snapshot_last_restore_read_field "$tmp" version)
        date=$(snapshot_last_restore_read_field "$tmp" date)
        device=$(snapshot_last_restore_read_field "$tmp" device)
        snapshot=$(snapshot_last_restore_read_field "$tmp" snapshot)
        backup=$(snapshot_last_restore_read_field "$tmp" backup)
        restore_log=$(snapshot_last_restore_read_field "$tmp" restore_log)
    else
        # Совместимость с восстановлением, выполненным версиями 1.8.0–1.8.3:
        # тогда last-restore.env ещё не создавался, но текстовый отчёт уже был.
        restore_log=$(sudo find /.snapshots/.arch-manager-recovery -maxdepth 1 -type f -name 'restore-*.txt' -printf '%T@ %p\n' 2>/dev/null \
            | sort -nr | head -n1 | cut -d' ' -f2-)
        if [[ -z "$restore_log" ]] || ! sudo test -r "$restore_log"; then
            msg "Последнее восстановление" \
                "Запись о последнем восстановлении пока не найдена.\n\nПосле следующего восстановления Arch Manager сохранит здесь состояние резервного старого @."
            return
        fi

        if ! sudo cat "$restore_log" >"$tmp"; then
            error_msg "Не удалось прочитать отчёт последнего восстановления."
            return
        fi
        version=$(sed -n '1s/.* \([0-9][0-9.]*\)$/\1/p' "$tmp")
        date=$(sed -n 's/^Дата:[[:space:]]*//p' "$tmp" | head -n1)
        device=$(sed -n 's/^Раздел:[[:space:]]*//p' "$tmp" | head -n1)
        snapshot=$(sed -n 's/^Точка:[[:space:]]*//p' "$tmp" | head -n1)
        backup=$(sed -n 's/^Старый корень:[[:space:]]*//p' "$tmp" | head -n1)
    fi

    [[ "$backup" =~ ^@\.broken-[0-9]{8}-[0-9]{6}$ ]] || {
        error_msg "Маркер последнего восстановления содержит неожиданное имя резервного корня.\n\n$backup\n\nАвтоматическое удаление заблокировано."
        return
    }

    root_dev=$(snapshot_root_device)
    root_subvol=$(snapshot_root_subvol_name)
    [[ -n "$device" ]] || device="$root_dev"

    rm -rf "$top"
    mkdir -p "$top"
    if sudo mount -o subvolid=5 "$device" "$top" 2>"$TMP_DIR/last-restore-mount.err"; then
        if sudo btrfs subvolume show "$top/$backup" >/dev/null 2>&1; then
            backup_exists="ДА"
            nested_count=$(sudo btrfs subvolume list -o "$top/$backup" 2>/dev/null | wc -l)
        fi
        sudo umount "$top" >/dev/null 2>&1 || true
    fi

    failed_count=$(systemctl --failed --no-legend --plain 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l)

    if [[ "$backup_exists" == "ДА" \
          && "$root_subvol" == "@" \
          && "$(readlink -f "$root_dev" 2>/dev/null)" == "$(readlink -f "$device" 2>/dev/null)" \
          && "$failed_count" -eq 0 \
          && "$nested_count" -eq 0 ]]; then
        safe_delete=1
    fi

    local status_text
    status_text="Последнее восстановление:\n\nВерсия Recovery: ${version:-не указана}\nДата: ${date:-не указана}\nТочка: №${snapshot:-?}\nСтарый корень: $backup\nСтарый корень найден: $backup_exists\nТекущий корень: / → ${root_subvol:-не определён}\nОшибок systemd: $failed_count\nВложенных подтомов внутри старого корня: $nested_count\n\nОтчёт Recovery: ${restore_log:-не указан}"

    if (( safe_delete )); then
        if confirm "Последнее восстановление успешно" \
            "$status_text\n\nТекущая система загружена из нового @, failed units нет, а старый $backup не содержит вложенных подтомов.\n\nЕсли вы уже убедились, что программы и данные работают, резервный старый корень можно удалить.\n\nУдалить $backup сейчас?"; then

            rm -rf "$top"
            mkdir -p "$top"
            if ! sudo mount -o subvolid=5 "$device" "$top"; then
                error_msg "Не удалось открыть верхний уровень Btrfs. Старый корень не удалён."
                return
            fi
            if sudo btrfs subvolume list -o "$top/$backup" 2>/dev/null | grep -q .; then
                sudo umount "$top" >/dev/null 2>&1 || true
                error_msg "Внутри $backup обнаружились вложенные подтомы. Автоматическое удаление остановлено."
                return
            fi
            if sudo btrfs subvolume delete "$top/$backup" >"$TMP_DIR/delete-broken-root.log" 2>&1; then
                sync
                sudo umount "$top" >/dev/null 2>&1 || true
                if sudo test -e "$marker"; then
                    printf '\nbackup_deleted=%s\n' "$(date -Is 2>/dev/null || date)" | sudo tee -a "$marker" >/dev/null || true
                fi
                msg "Старый корень удалён" \
                    "$backup успешно удалён.\n\nТочки восстановления в @snapshots не затронуты."
            else
                sudo umount "$top" >/dev/null 2>&1 || true
                error_msg "Не удалось удалить $backup.\n\n$(tail -n 20 "$TMP_DIR/delete-broken-root.log")"
            fi
        fi
    else
        msg "Последнее восстановление" \
            "$status_text\n\nArch Manager пока НЕ предлагает автоматическое удаление старого корня. Это нормально, если есть failed units, вложенные подтомы, система загружена не из @ или резервный корень уже удалён."
    fi
}

snapshot_restore_menu() {
    while true; do
        dialog_init_geometry
        local choice

        choice=$(dlg \
            --title "Восстановление системы" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Если Arch Linux ещё загружается, основной вариант — восстановление БЕЗ USB. Arch Manager один раз перезагрузит компьютер в автономную среду и там безопасно заменит корневой @. Аварийная флешка остаётся вариантом на случай, когда система уже не загружается." \
            "$DLG_MENU_H" "$DLG_MENU_W" 10 \
            1 "Восстановить систему БЕЗ флешки" \
            2 "Проверить локальный режим восстановления" \
            3 "Последнее восстановление / старый @.broken" \
            4 "Создать / обновить аварийную флешку" \
            5 "Проверить подключённую аварийную флешку" \
            6 "Как пользоваться аварийной флешкой" \
            7 "Запасной способ: обычная флешка Arch Linux  ›" \
            8 "Посмотреть точки восстановления" \
            9 "Проверить схему хранения" \
            10 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) snapshot_restore_select local ;;
            2) snapshot_recovery_local_status ;;
            3) snapshot_last_restore_status ;;
            4) snapshot_recovery_flash_create ;;
            5) snapshot_recovery_flash_status ;;
            6) snapshot_recovery_flash_howto ;;
            7) snapshot_recovery_legacy_menu ;;
            8) snapshot_restore_select ;;
            9) snapshot_restore_overview ;;
            10) return ;;
        esac
    done
}

snapshot_manage_menu() {
    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Управление снапшотами" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Здесь собраны действия с уже существующими точками восстановления." "$DLG_MENU_H" "$DLG_MENU_W" 4 \
            1 "Переименовать снапшот" \
            2 "Изменить важность (★ важный)" \
            3 "Удалить снапшот" \
            4 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) snapshot_rename ;;
            2) snapshot_change_importance ;;
            3) snapshot_delete ;;
            4) return ;;
        esac
    done
}

snapshot_change_importance() {
    snapshot_ready || {
        error_msg "Изменение важности недоступно: Snapper не установлен или не настроен для /."
        return
    }

    local list_file="$TMP_DIR/snapshot-importance-list.csv"
    local list_err="$TMP_DIR/snapshot-importance-list.err"
    local id date userdata description label selected new_value new_userdata
    local menu_height max_label
    local -a items=()
    local -A dates=()
    local -A userdatas=()
    local -A descriptions=()

    sudo -v || { error_msg "Не удалось получить права администратора."; return; }

    if ! sudo snapper -c root --csvout --no-headers --separator '|' --iso \
        list --columns number,date,userdata,description \
        >"$list_file" 2>"$list_err"; then
        error_msg "Не удалось получить список снапшотов.\n\n$(tail -n 10 "$list_err")"
        return
    fi

    while IFS='|' read -r id date userdata description; do
        id=$(snapshot_csv_field_clean "$id")
        date=$(snapshot_csv_field_clean "$date")
        userdata=$(snapshot_csv_field_clean "$userdata")
        description=$(snapshot_csv_field_clean "$description")

        [[ "$id" =~ ^[1-9][0-9]*$ ]] || continue
        [[ -n "$date" ]] || date="дата не указана"

        dates["$id"]="$date"
        userdatas["$id"]="$userdata"
        descriptions["$id"]="$description"

        if snapshot_is_important "$userdata"; then
            label="★ ВАЖНЫЙ — $date — $(snapshot_safe_description "$description")"
        else
            label="обычный — $date — $(snapshot_safe_description "$description")"
        fi
        items+=("$id" "$label")
    done <"$list_file"

    if ((${#items[@]} == 0)); then
        msg "Важность снапшота" "Снапшотов пока нет."
        return
    fi

    dialog_init_geometry
    max_label=$(( DLG_LARGE_W - 18 ))
    (( max_label < 40 )) && max_label=40

    local i
    for ((i=1; i<${#items[@]}; i+=2)); do
        if (( ${#items[i]} > max_label )); then
            items[i]="${items[i]:0:max_label-1}…"
        fi
    done

    menu_height=$(( ${#items[@]} / 2 ))
    (( menu_height < 3 )) && menu_height=3
    (( menu_height > DLG_LARGE_H - 8 )) && menu_height=$(( DLG_LARGE_H - 8 ))

    selected=$(dlg \
        --title "Изменить важность" \
        --ok-label "Выбрать" \
        --cancel-label "Назад" \
        --menu "Выберите снапшот. Важные точки учитываются отдельным лимитом хранения Snapper." \
        "$DLG_LARGE_H" "$DLG_LARGE_W" "$menu_height" \
        "${items[@]}" \
        3>&1 1>&2 2>&3) || return

    [[ "$selected" =~ ^[1-9][0-9]*$ ]] || return

    if snapshot_is_important "${userdatas[$selected]}"; then
        new_value="no"
        if ! confirm "Снять важность" \
            "Снапшот №$selected сейчас отмечен как важный.\n\nДата: ${dates[$selected]}\nОписание: $(snapshot_safe_description "${descriptions[$selected]}")\n\nСделать его обычным?"; then
            return
        fi
    else
        new_value="yes"
        if ! confirm "Отметить как важный" \
            "Снапшот №$selected сейчас обычный.\n\nДата: ${dates[$selected]}\nОписание: $(snapshot_safe_description "${descriptions[$selected]}")\n\nОтметить его как важный?"; then
            return
        fi
    fi

    new_userdata=$(snapshot_userdata_with_importance "${userdatas[$selected]}" "$new_value")

    if sudo snapper -c root modify --userdata "$new_userdata" "$selected" \
        >"$TMP_DIR/snapshot-importance.log" 2>&1; then
        if [[ "$new_value" == "yes" ]]; then
            msg "Готово" "Снапшот №$selected теперь отмечен как ★ важный."
        else
            msg "Готово" "Снапшот №$selected теперь обычный."
        fi
    else
        error_msg "Не удалось изменить важность снапшота №$selected.\n\n$(tail -n 10 "$TMP_DIR/snapshot-importance.log")"
    fi
}

snapshot_policy_menu_action() {
    snapshot_config_exists || {
        error_msg "Сначала выполните настройку Snapper."
        return
    }

    if ! confirm "Политика хранения" \
        "Применить рекомендуемую политику Arch Manager?\n\n• до 10 обычных снапшотов\n• до 5 важных (important=yes)\n• точки перед обновлением создаются важными\n• автоочистка включена\n• timeline по умолчанию выключен\n\nСуществующие снапшоты не удаляются немедленно — очистка выполняется Snapper по своим правилам."; then
        return
    fi

    if snapshot_apply_recommended_policy; then
        msg "Готово" \
            "Рекомендуемая политика хранения применена.\n\nОбычные: до 10\nВажные: до 5\nАвтоочистка: ВКЛ\nСнапшоты по времени: ВЫКЛ"
    fi
}

snapshot_settings_menu() {
    while true; do
        dialog_init_geometry
        local choice setup_label

        if snapshot_config_exists; then
            setup_label="Конфигурация Snapper: настроена"
        else
            setup_label="Настроить Snapper для /"
        fi

        choice=$(dlg \
            --title "Настройки снапшотов" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Автоматизация и системные параметры Snapper." "$DLG_MENU_H" "$DLG_MENU_W" 5 \
            1 "Снапшот перед обновлением: $(snapshot_auto_label)" \
            2 "Снапшоты по времени: $(snapshot_timeline_label)" \
            3 "Политика хранения и автоочистка" \
            4 "$setup_label" \
            5 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) snapshot_toggle_preupdate ;;
            2) snapshot_toggle_timeline ;;
            3) snapshot_policy_menu_action ;;
            4) snapshot_setup ;;
            5) return ;;
        esac
    done
}

snapshot_menu() {
    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Снапшоты Btrfs / Snapper" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Btrfs: $(snapshot_root_is_btrfs && printf 'OK' || printf 'нет') | Snapper: $(snapshot_config_exists && printf 'настроен' || printf 'не настроен') | перед обновлением: $(snapshot_auto_label) | по времени: $(snapshot_timeline_label)" "$DLG_MENU_H" "$DLG_MENU_W" 7 \
            1 "Состояние, место и список снапшотов" \
            2 "Создать точку восстановления" \
            3 "Управление снапшотами  ›" \
            4 "Восстановление системы  ›" \
            5 "Как пользоваться снапшотами" \
            6 "Настройки  ›" \
            7 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) snapshot_status_report ;;
            2) snapshot_manual_create ;;
            3) snapshot_manage_menu ;;
            4) snapshot_restore_menu ;;
            5) snapshot_help ;;
            6) snapshot_settings_menu ;;
            7) return ;;
        esac
    done
}

check_official_updates_to_file() {
    local out_file="$1"
    local err_file="$2"
    : >"$out_file"
    : >"$err_file"

    checkupdates --nocolor >"$out_file" 2>"$err_file"
    return $?
}

official_check() {
    local out="$TMP_DIR/official-updates.txt"
    local err="$TMP_DIR/official-updates.err"
    local rc

    dlg --title "Официальные репозитории" \
        --infobox "Проверяю обновления через checkupdates..." "$DLG_INFO_H" "$DLG_INFO_W"

    check_official_updates_to_file "$out" "$err"
    rc=$?

    case "$rc" in
        0)
            local count
            count=$(grep -cve '^[[:space:]]*$' "$out" || true)
            {
                printf 'Доступно обновлений: %s\n\n' "$count"
                cat "$out"
            } >"$TMP_DIR/official-display.txt"
            show_text_file "Обновления Arch ($count)" "$TMP_DIR/official-display.txt"
            ;;
        2)
            msg "Официальные репозитории" "Обновлений нет.\n\nСистема по официальным репозиториям актуальна."
            ;;
        *)
            local details=""
            [[ -s "$err" ]] && details="$(tail -n 12 "$err")"
            error_msg "Не удалось проверить официальные обновления.\n\n${details:-checkupdates завершился с кодом $rc.}"
            ;;
    esac
}

official_upgrade() {
    if ! confirm "Обновление Arch" \
        "Будет выполнена команда:\n\nsudo pacman -Syu\n\nПродолжить?"; then
        return
    fi

    if ! maybe_create_pre_update_snapshot "Arch Manager: перед pacman -Syu"; then
        return
    fi

    clear
    echo "=================================================="
    echo " Arch Manager — обновление официальных пакетов"
    echo " Команда: sudo pacman -Syu"
    echo "=================================================="
    echo

    if sudo pacman -Syu; then
        [[ -n "$LAST_UPDATE_SNAPSHOT_ID" ]] && echo "Снапшот перед обновлением: №$LAST_UPDATE_SNAPSHOT_ID"
        pause_terminal
        if [[ -n "$LAST_UPDATE_SNAPSHOT_ID" ]]; then
            msg "Готово" "Официальные пакеты успешно обновлены.

Точка восстановления перед обновлением: №$LAST_UPDATE_SNAPSHOT_ID."
        else
            msg "Готово" "Официальные пакеты успешно обновлены."
        fi
    else
        local rc=$?
        echo
        echo "ОШИБКА: pacman завершился с кодом $rc."
        pause_terminal
        error_msg "Обновление официальных пакетов не завершено.\n\npacman вернул код $rc."
    fi
}

check_aur_to_file() {
    local out_file="$1"
    local err_file="$2"
    : >"$out_file"
    : >"$err_file"

    yay -Qu --aur >"$out_file" 2>"$err_file"
    return $?
}

aur_check() {
    if ! need_cmd yay; then
        error_msg "Команда yay не найдена.\n\nУстановите yay и повторите проверку."
        return
    fi

    local out="$TMP_DIR/aur-updates.txt"
    local err="$TMP_DIR/aur-updates.err"
    local rc

    dlg --title "AUR" --infobox "Проверяю обновления AUR через yay..." "$DLG_INFO_H" "$DLG_INFO_W"

    check_aur_to_file "$out" "$err"
    rc=$?

    if [[ -s "$out" ]]; then
        local count
        count=$(grep -cve '^[[:space:]]*$' "$out" || true)
        {
            printf 'Доступно обновлений AUR: %s\n\n' "$count"
            cat "$out"
        } >"$TMP_DIR/aur-display.txt"
        show_text_file "Обновления AUR ($count)" "$TMP_DIR/aur-display.txt"
    elif [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
        msg "AUR" "Обновлений AUR нет."
    else
        local details=""
        [[ -s "$err" ]] && details="$(tail -n 12 "$err")"
        error_msg "Не удалось проверить AUR.\n\n${details:-yay завершился с кодом $rc.}"
    fi
}

aur_upgrade() {
    if ! need_cmd yay; then
        error_msg "Команда yay не найдена.\n\nAUR-обновление недоступно."
        return
    fi

    if ! confirm "Обновление AUR" \
        "Будет выполнена команда только для AUR:\n\nyay -Su --aur\n\nПродолжить?"; then
        return
    fi

    if ! maybe_create_pre_update_snapshot "Arch Manager: перед обновлением AUR"; then
        return
    fi

    clear
    echo "=================================================="
    echo " Arch Manager — обновление AUR"
    echo " Команда: yay -Su --aur"
    echo "=================================================="
    echo

    if yay -Su --aur; then
        [[ -n "$LAST_UPDATE_SNAPSHOT_ID" ]] && echo "Снапшот перед обновлением: №$LAST_UPDATE_SNAPSHOT_ID"
        pause_terminal
        if [[ -n "$LAST_UPDATE_SNAPSHOT_ID" ]]; then
            msg "Готово" "AUR-пакеты успешно обновлены.

Точка восстановления перед обновлением: №$LAST_UPDATE_SNAPSHOT_ID."
        else
            msg "Готово" "AUR-пакеты успешно обновлены."
        fi
    else
        local rc=$?
        echo
        echo "ОШИБКА: yay завершился с кодом $rc."
        pause_terminal
        error_msg "Обновление AUR не завершено.\n\nyay вернул код $rc."
    fi
}

aur_menu() {
    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Обновления AUR" \
            --ok-label "Выбрать" \
            --cancel-label "Назад" \
            --menu "Выберите действие:" "$DLG_MENU_H" "$DLG_MENU_W" 4 \
            1 "Проверить AUR-обновления" \
            2 "Установить AUR-обновления" \
            3 "Назад" \
            3>&1 1>&2 2>&3) || return

        case "$choice" in
            1) aur_check ;;
            2) aur_upgrade ;;
            3) return ;;
        esac
    done
}


check_all_updates() {
    local official_out="$TMP_DIR/all-official.txt"
    local official_err="$TMP_DIR/all-official.err"
    local aur_out="$TMP_DIR/all-aur.txt"
    local aur_err="$TMP_DIR/all-aur.err"
    local official_rc aur_rc official_count aur_count

    dlg --title "Проверка обновлений" \
        --infobox "1/2  Проверяю официальные репозитории Arch..." "$DLG_INFO_H" "$DLG_INFO_W"

    check_official_updates_to_file "$official_out" "$official_err"
    official_rc=$?

    dlg --title "Проверка обновлений" \
        --infobox "2/2  Проверяю обновления AUR..." "$DLG_INFO_H" "$DLG_INFO_W"

    : >"$aur_out"
    : >"$aur_err"

    if need_cmd yay; then
        check_aur_to_file "$aur_out" "$aur_err"
        aur_rc=$?
    else
        aur_rc=127
        printf 'yay не установлен\n' >"$aur_err"
    fi

    case "$official_rc" in
        0) official_count=$(grep -cve '^[[:space:]]*$' "$official_out" || true) ;;
        2) official_count=0 ;;
        *) official_count="?" ;;
    esac

    if [[ -s "$aur_out" ]]; then
        aur_count=$(grep -cve '^[[:space:]]*$' "$aur_out" || true)
    elif [[ "$aur_rc" -eq 0 || "$aur_rc" -eq 1 ]]; then
        aur_count=0
    else
        aur_count="?"
    fi

    {
        printf 'ОБНОВЛЕНИЯ ARCH MANAGER\n'
        printf '=======================\n\n'

        printf 'ОФИЦИАЛЬНЫЕ ПАКЕТЫ — pacman / checkupdates\n'
        printf '%s\n' '------------------------------------------'
        if [[ "$official_rc" -eq 0 ]]; then
            printf 'Доступно обновлений: %s\n\n' "$official_count"
            cat "$official_out"
        elif [[ "$official_rc" -eq 2 ]]; then
            printf 'Обновлений нет.\n'
        else
            printf 'Не удалось проверить обновления.\n'
            [[ -s "$official_err" ]] && {
                printf '\n'
                tail -n 10 "$official_err"
            }
        fi

        printf '\n\n'
        printf 'AUR-ПАКЕТЫ — yay\n'
        printf '%s\n' '-----------------'
        if [[ -s "$aur_out" ]]; then
            printf 'Доступно обновлений: %s\n\n' "$aur_count"
            cat "$aur_out"
        elif [[ "$aur_rc" -eq 0 || "$aur_rc" -eq 1 ]]; then
            printf 'Обновлений нет.\n'
        elif [[ "$aur_rc" -eq 127 ]]; then
            printf 'yay не найден — проверка AUR недоступна.\n'
        else
            printf 'Не удалось проверить AUR.\n'
            [[ -s "$aur_err" ]] && {
                printf '\n'
                tail -n 10 "$aur_err"
            }
        fi

        printf '\n\n'
        printf 'ИТОГО\n'
        printf '%s\n' '-----'
        printf 'Официальные пакеты: %s\n' "$official_count"
        printf 'AUR:                %s\n' "$aur_count"
    } >"$TMP_DIR/all-updates-display.txt"

    # Если обновления найдены хотя бы в одном источнике, показываем
    # кнопку установки прямо в окне результатов проверки. Так проверка и
    # установка остаются одним сценарием без возврата в главное меню.
    local updates_found=0
    if [[ "$official_count" =~ ^[0-9]+$ ]] && (( official_count > 0 )); then
        updates_found=1
    fi
    if [[ "$aur_count" =~ ^[0-9]+$ ]] && (( aur_count > 0 )); then
        updates_found=1
    fi

    if (( updates_found )); then
        local report_rc=0
        dialog_init_geometry
        dlg --title "Все обновления" \
            --exit-label "Назад" \
            --extra-button \
            --extra-label "Установить обновления" \
            --textbox "$TMP_DIR/all-updates-display.txt" "$DLG_LARGE_H" "$DLG_LARGE_W" || report_rc=$?

        # dialog возвращает код 3 при нажатии дополнительной кнопки.
        if [[ "$report_rc" -eq 3 ]]; then
            upgrade_all_updates 1
        fi
    else
        show_text_file "Все обновления" "$TMP_DIR/all-updates-display.txt"
    fi
}


upgrade_all_updates() {
    local already_confirmed="${1:-0}"

    if [[ "$already_confirmed" != "1" ]]; then
        if ! confirm "Установка обновлений" \
            "Arch Manager обновит систему в два этапа:\n\n1. Официальные пакеты — pacman\n2. AUR-пакеты — yay\n\nПродолжить?"; then
            return
        fi
    fi

    if ! maybe_create_pre_update_snapshot "Arch Manager: перед полным обновлением системы"; then
        return
    fi

    clear
    echo "============================================================"
    echo " Arch Manager — обновление системы"
    echo "============================================================"
    echo
    echo "[1/2] ОФИЦИАЛЬНЫЕ ПАКЕТЫ — pacman"
    echo "Команда: sudo pacman -Syu"
    echo

    local pacman_rc=0
    local aur_rc=0

    if sudo pacman -Syu; then
        echo
        echo "[OK] Официальные пакеты обновлены."
    else
        pacman_rc=$?
        echo
        echo "[ОШИБКА] pacman завершился с кодом $pacman_rc."
        echo "AUR-этап не запускается, чтобы не продолжать после ошибки pacman."
        pause_terminal
        error_msg "Обновление системы остановлено.\n\npacman вернул код $pacman_rc."
        return
    fi

    echo
    echo "------------------------------------------------------------"
    echo "[2/2] AUR-ПАКЕТЫ — yay"
    echo "Команда: yay -Su --aur"
    echo

    if need_cmd yay; then
        if yay -Su --aur; then
            echo
            echo "[OK] AUR-пакеты обновлены."
        else
            aur_rc=$?
            echo
            echo "[ОШИБКА] yay завершился с кодом $aur_rc."
            pause_terminal
            error_msg "Официальные пакеты обновлены,\nно обновление AUR завершилось ошибкой.\n\nyay вернул код $aur_rc."
            return
        fi
    else
        echo "[ПРОПУСК] yay не установлен."
        pause_terminal
        msg "Частично готово" "Официальные пакеты обновлены.\n\nAUR пропущен: команда yay не найдена."
        return
    fi

    pause_terminal
    msg "Готово" "Все доступные обновления успешно установлены:\n\n• официальные пакеты Arch\n• AUR-пакеты"
}

count_official_updates() {
    local out="$TMP_DIR/status-official.txt"
    local err="$TMP_DIR/status-official.err"
    local rc
    check_official_updates_to_file "$out" "$err"
    rc=$?
    case "$rc" in
        0) grep -cve '^[[:space:]]*$' "$out" || true ;;
        2) printf '0\n' ;;
        *) printf '?\n' ;;
    esac
}

count_aur_updates() {
    if ! need_cmd yay; then
        printf 'н/д\n'
        return
    fi

    local out="$TMP_DIR/status-aur.txt"
    local err="$TMP_DIR/status-aur.err"
    check_aur_to_file "$out" "$err" || true

    if [[ -s "$out" ]]; then
        grep -cve '^[[:space:]]*$' "$out" || true
    elif [[ -s "$err" ]]; then
        printf '?\n'
    else
        printf '0\n'
    fi
}

system_status() {
    dlg --title "Состояние системы" \
        --infobox "Собираю сведения о системе..." "$DLG_INFO_H" "$DLG_INFO_W"

    local kernel plasma official aur failed_system failed_user root_free root_used data_free data_used root_fs snapshot_state
    kernel=$(uname -r)

    if need_cmd plasmashell; then
        plasma=$(plasmashell --version 2>/dev/null | awk '{print $2}' | head -n1)
    else
        plasma=$(pacman -Q plasma-workspace 2>/dev/null | awk '{print $2}')
    fi
    [[ -n "${plasma:-}" ]] || plasma="не определена"

    official=$(count_official_updates)
    aur=$(count_aur_updates)

    failed_system=$(systemctl --failed --no-legend --plain 2>/dev/null | grep -c . || true)
    failed_user=$(systemctl --user --failed --no-legend --plain 2>/dev/null | grep -c . || true)

    root_free=$(df -hP / 2>/dev/null | awk 'NR==2 {print $4}')
    root_used=$(df -hP / 2>/dev/null | awk 'NR==2 {print $5}')
    root_fs=$(findmnt -no FSTYPE / 2>/dev/null || printf 'не определена')

    if snapshot_config_exists; then
        snapshot_state="Snapper настроен; перед обновлением: $(snapshot_auto_label)"
    elif [[ "$root_fs" == "btrfs" ]]; then
        snapshot_state="Btrfs есть; Snapper ещё не настроен"
    else
        snapshot_state="недоступны: / не Btrfs"
    fi

    if [[ -d /data ]]; then
        data_free=$(df -hP /data 2>/dev/null | awk 'NR==2 {print $4}')
        data_used=$(df -hP /data 2>/dev/null | awk 'NR==2 {print $5}')
    else
        data_free="нет /data"
        data_used="-"
    fi

    cat >"$TMP_DIR/status.txt" <<STATUS
ARCH MANAGER — СОСТОЯНИЕ СИСТЕМЫ

Ядро Linux:             $kernel
KDE Plasma:             $plasma

Обновления Arch:        $official
Обновления AUR:         $aur

Failed system services: $failed_system
Failed user services:   $failed_user

Свободно на /:          $root_free   (занято $root_used)
Свободно на /data:      $data_free   (занято $data_used)
Файловая система /:     $root_fs
Снапшоты:               $snapshot_state

Официальные обновления: checkupdates / pacman
AUR:                    yay --aur
STATUS

    show_text_file "Состояние системы" "$TMP_DIR/status.txt"
}



CLEANUP_STATUS="Данные обновлены. Выберите, что очистить."
CLEANUP_CACHE_INFO="—"
CLEANUP_ORPHAN_COUNT="0"
CLEANUP_JOURNAL_INFO="—"
CLEANUP_PACDIFF_COUNT="0"

cleanup_scan() {
    local show_progress="${1:-1}"
    local cache_file="$TMP_DIR/cleanup-paccache.txt"
    local orphan_file="$TMP_DIR/cleanup-orphans.txt"
    local orphan_names="$TMP_DIR/cleanup-orphan-names.txt"
    local pacdiff_file="$TMP_DIR/cleanup-pacdiff.txt"
    local journal_file="$TMP_DIR/cleanup-journal.txt"

    if [[ "$show_progress" == "1" ]]; then
        dlg --title "Очистка системы" \
            --infobox "Проверяю, что можно очистить..." "$DLG_INFO_H" "$DLG_INFO_W"
    fi

    : >"$cache_file"
    : >"$orphan_file"
    : >"$orphan_names"
    : >"$pacdiff_file"
    : >"$journal_file"

    paccache -dv --nocolor >"$cache_file" 2>&1 || true
    pacman -Qtd >"$orphan_file" 2>/dev/null || true
    pacman -Qtdq >"$orphan_names" 2>/dev/null || true
    pacdiff -o --nocolor >"$pacdiff_file" 2>/dev/null || true
    journalctl --disk-usage --no-pager >"$journal_file" 2>&1 || true

    local cache_summary cache_saved journal_summary journal_size
    cache_summary=$(tail -n 1 "$cache_file" 2>/dev/null || true)

    if grep -qi 'no candidate packages found' "$cache_file" 2>/dev/null; then
        CLEANUP_CACHE_INFO="нечего очищать"
    else
        cache_saved=$(printf '%s\n' "$cache_summary" | \
            sed -n 's/.*disk space saved: \([^)]*\)).*/\1/p')
        if [[ -n "$cache_saved" ]]; then
            CLEANUP_CACHE_INFO="$cache_saved"
        elif [[ -n "$cache_summary" ]]; then
            CLEANUP_CACHE_INFO="есть кандидаты"
        else
            CLEANUP_CACHE_INFO="не определено"
        fi
    fi

    CLEANUP_ORPHAN_COUNT=$(grep -cve '^[[:space:]]*$' "$orphan_names" 2>/dev/null || true)
    CLEANUP_PACDIFF_COUNT=$(grep -cve '^[[:space:]]*$' "$pacdiff_file" 2>/dev/null || true)

    journal_summary=$(head -n 1 "$journal_file" 2>/dev/null || true)
    journal_size=$(printf '%s\n' "$journal_summary" | \
        sed -n 's/.*take up \([^ ]*\) in.*/\1/p')
    if [[ -n "$journal_size" ]]; then
        CLEANUP_JOURNAL_INFO="$journal_size"
    elif [[ -n "$journal_summary" ]]; then
        CLEANUP_JOURNAL_INFO="определён"
    else
        CLEANUP_JOURNAL_INFO="не определено"
    fi
}

cleanup_get_sudo() {
    if sudo -v; then
        return 0
    fi
    CLEANUP_STATUS="Ошибка: не удалось получить права администратора."
    return 1
}

cleanup_pacman_cache_action() {
    local log="$TMP_DIR/cleanup-cache-run.log"

    if [[ "$CLEANUP_CACHE_INFO" == "нечего очищать" ]]; then
        CLEANUP_STATUS="Кэш Pacman: очищать нечего."
        return
    fi

    cleanup_get_sudo || return

    if sudo paccache -r >"$log" 2>&1; then
        CLEANUP_STATUS="Кэш Pacman очищен; 3 последние версии пакетов сохранены."
    else
        CLEANUP_STATUS="Ошибка очистки кэша Pacman. Подробности: $log"
    fi
}

cleanup_orphans_action() {
    local names_file="$TMP_DIR/cleanup-orphan-names.txt"
    local log="$TMP_DIR/cleanup-orphans-run.log"
    local -a orphans=()

    if [[ ! -s "$names_file" ]]; then
        CLEANUP_STATUS="Сиротских пакетов нет."
        return
    fi

    mapfile -t orphans <"$names_file"
    if ((${#orphans[@]} == 0)); then
        CLEANUP_STATUS="Сиротских пакетов нет."
        return
    fi

    cleanup_get_sudo || return

    if sudo pacman -R --noconfirm "${orphans[@]}" >"$log" 2>&1; then
        CLEANUP_STATUS="Удалено сиротских пакетов: ${#orphans[@]}."
    else
        CLEANUP_STATUS="Ошибка удаления сиротских пакетов. Подробности: $log"
    fi
}

journal_usage_now() {
    local line value
    line=$(journalctl --disk-usage --no-pager 2>/dev/null | head -n 1 || true)
    value=$(printf '%s\n' "$line" | sed -n 's/.*take up \([^ ]*\) in.*/\1/p')
    [[ -n "$value" ]] && printf '%s\n' "$value" || printf 'не определено\n'
}

cleanup_journal_action() {
    local log="$TMP_DIR/cleanup-journal-run.log"
    local before after

    before=$(journal_usage_now)
    cleanup_get_sudo || return

    if sudo journalctl --vacuum-time=30d >"$log" 2>&1; then
        after=$(journal_usage_now)
        if [[ "$before" == "$after" ]]; then
            CLEANUP_STATUS="Journal проверен повторно: $before → $after; архивов старше 30 дней для удаления нет."
        else
            CLEANUP_STATUS="Journal очищен и проверен повторно: $before → $after."
        fi
    else
        CLEANUP_STATUS="Ошибка очистки journal. Подробности: $log"
    fi
}

cleanup_pacdiff_action() {
    if (( CLEANUP_PACDIFF_COUNT == 0 )); then
        CLEANUP_STATUS=".pacorig/.pacnew/.pacsave: обрабатывать нечего."
        return
    fi

    cleanup_get_sudo || return

    clear
    echo "============================================================"
    echo " Arch Manager — pacdiff"
    echo " Найдено файлов для ручной проверки: $CLEANUP_PACDIFF_COUNT"
    echo " Для каждого файла решение принимаете вы."
    echo "============================================================"
    echo

    pacdiff -s
    local rc=$?

    if [[ "$rc" -eq 0 ]]; then
        CLEANUP_STATUS="pacdiff завершён."
    else
        CLEANUP_STATUS="pacdiff завершился с кодом $rc."
    fi
    pause_terminal
}

cleanup_all_action() {
    local cache_log="$TMP_DIR/cleanup-all-cache.log"
    local orphan_log="$TMP_DIR/cleanup-all-orphans.log"
    local journal_log="$TMP_DIR/cleanup-all-journal.log"
    local names_file="$TMP_DIR/cleanup-orphan-names.txt"
    local -a orphans=()
    local cache_result="пропуск"
    local orphan_result="0"
    local journal_result="ошибка"
    local journal_before journal_after

    journal_before=$(journal_usage_now)
    cleanup_get_sudo || return

    if [[ "$CLEANUP_CACHE_INFO" == "нечего очищать" ]]; then
        cache_result="нечего"
    elif sudo paccache -r >"$cache_log" 2>&1; then
        cache_result="OK"
    else
        cache_result="ошибка"
    fi

    if [[ -s "$names_file" ]]; then
        mapfile -t orphans <"$names_file"
    fi

    if ((${#orphans[@]} > 0)); then
        if sudo pacman -R --noconfirm "${orphans[@]}" >"$orphan_log" 2>&1; then
            orphan_result="${#orphans[@]} удалено"
        else
            orphan_result="ошибка"
        fi
    fi

    if sudo journalctl --vacuum-time=30d >"$journal_log" 2>&1; then
        journal_after=$(journal_usage_now)
        if [[ "$journal_before" == "$journal_after" ]]; then
            journal_result="OK ($journal_before → $journal_after; удалять нечего)"
        else
            journal_result="OK ($journal_before → $journal_after)"
        fi
    fi

    CLEANUP_STATUS="Всё автоматическое: кэш $cache_result; сироты $orphan_result; journal $journal_result."
}

cleanup_run_selected() {
    local -a selected=("$@")
    local item result combined=""
    local all_selected=0
    local pacdiff_selected=0

    for item in "${selected[@]}"; do
        [[ "$item" == "ALL" ]] && all_selected=1
        [[ "$item" == "PACDIFF" ]] && pacdiff_selected=1
    done

    if (( all_selected )); then
        cleanup_all_action
        combined="$CLEANUP_STATUS"
        if (( pacdiff_selected )); then
            cleanup_pacdiff_action
            combined="$combined | $CLEANUP_STATUS"
        fi
        CLEANUP_STATUS="$combined"
        return
    fi

    for item in "${selected[@]}"; do
        case "$item" in
            CACHE) cleanup_pacman_cache_action ;;
            ORPHANS) cleanup_orphans_action ;;
            JOURNAL) cleanup_journal_action ;;
            PACDIFF) cleanup_pacdiff_action ;;
        esac
        result="$CLEANUP_STATUS"
        if [[ -n "$combined" ]]; then
            combined="$combined | $result"
        else
            combined="$result"
        fi
    done

    CLEANUP_STATUS="$combined"
}

cleanup_menu() {
    cleanup_scan 1

    while true; do
        dialog_init_geometry
        local prompt selection
        local -a selected=()

        prompt="Всё найденное и все действия — на одном экране.\n\nОтметьте один пункт, несколько пунктов или «ВСЁ автоматически».\nSpace — отметить, Enter — выполнить.\n\nПоследний результат: $CLEANUP_STATUS"

        selection=$(dlg \
            --title "Очистка системы" \
            --ok-label "Очистить выбранное" \
            --cancel-label "Назад" \
            --separate-output \
            --checklist "$prompt" "$DLG_LARGE_H" "$DLG_LARGE_W" 5 \
            ALL     "ВСЁ автоматически — кэш + сироты + журналы старше 30 дней" off \
            CACHE   "Кэш Pacman — можно убрать: $CLEANUP_CACHE_INFO" off \
            ORPHANS "Сиротские пакеты — найдено: $CLEANUP_ORPHAN_COUNT" off \
            JOURNAL "Журнал systemd — всего сейчас: $CLEANUP_JOURNAL_INFO; удаляются архивы старше 30 дней" off \
            PACDIFF ".pacorig/.pacnew/.pacsave — найдено: $CLEANUP_PACDIFF_COUNT; вручную через pacdiff" off \
            3>&1 1>&2 2>&3) || return

        if [[ -z "$selection" ]]; then
            CLEANUP_STATUS="Ничего не выбрано."
            continue
        fi

        mapfile -t selected <<<"$selection"
        cleanup_run_selected "${selected[@]}"

        # После любого действия заново измеряем все показатели перед
        # повторным показом этого же экрана. Так таблица всегда содержит
        # фактическое состояние системы после очистки.
        cleanup_scan 0
    done
}

main_menu() {
    while true; do
        dialog_init_geometry
        local choice
        choice=$(dlg \
            --title "Главное меню" \
            --ok-label "Выбрать" \
            --cancel-label "Выход" \
            --menu "Выберите раздел:" "$DLG_MENU_H" "$DLG_MENU_W" 5 \
            1 "Проверить/Установить все обновления" \
            2 "Информация о системе" \
            3 "Очистка системы" \
            4 "Снапшоты Btrfs / Snapper" \
            5 "Выход" \
            3>&1 1>&2 2>&3) || break

        case "$choice" in
            1) check_all_updates ;;
            2) system_status ;;
            3) cleanup_menu ;;
            4) snapshot_menu ;;
            5) break ;;
        esac
    done
}

require_runtime
snapshot_load_settings
main_menu
