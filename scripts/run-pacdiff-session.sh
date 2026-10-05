#!/usr/bin/env bash
set -u

PACDIFF=/usr/bin/pacdiff

printf '%s\n' '============================================================'
printf '%s\n' ' Arch Manager — проверка файлов настроек'
printf '%s\n' ' Для каждого .pacnew/.pacsave/.pacorig решение принимаете вы.'
printf '%s\n' ' Arch Manager ничего не удаляет автоматически.'
printf '%s\n' '============================================================'
printf '\n'

if [[ ! -x "$PACDIFF" ]]; then
    printf 'pacdiff не найден. Установите пакет pacman-contrib.\n' >&2
    rc=127
else
    "$PACDIFF" -s
    rc=$?
fi

printf '\nПроверка завершена (код %d).\n' "$rc"
read -r -p 'Нажмите Enter, чтобы закрыть окно…' _
exit "$rc"
