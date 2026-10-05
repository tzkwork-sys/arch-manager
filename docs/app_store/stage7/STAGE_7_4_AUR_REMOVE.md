# Arch Manager — App Store Stage 7.4: удаление AUR

**Дата:** 1 октября 2026  
**Статус:** реализовано; установленный подтверждённый AUR-пакет можно удалить через интерактивный terminal workflow `yay -Rns`.

## Что реализовано

- Добавлен отдельный `AurRemovePlanner`; официальный backend магазина не изменён и не знает про `yay`.
- Удаление доступно только для `AurPackage`, уже подтверждённого AUR-подсистемой.
- Перед открытием терминала Arch Manager повторно проверяет:
  - отсутствие `/var/lib/pacman/db.lck`;
  - что Arch Manager не запущен от root;
  - наличие рабочих `yay` и `pacman`;
  - что пакет реально установлен;
  - что `pacman -Qm` всё ещё считает пакет foreign/AUR, а не пакетом официального репозитория.
- Для удаления не требуется `base-devel`, `git`, `makepkg` и gate полного системного обновления: они относятся к сборке/установке, а не к remove.
- Общий `AurTerminalProcess` теперь имеет whitelist `install/remove` и получает общий `PackageTransactionCoordinator` lease `aur-install`/`aur-remove`.
- `scripts/run-aur-operation.sh` принимает только `install|remove`, повторно валидирует package name и непосредственно перед удалением ещё раз проверяет installed/foreign state.
- Удаление выполняется интерактивно: `yay -Rns -- <package>` без `--noconfirm` и без shell evaluation.
- Перед подтверждением пользователь видит в терминале native review от `yay/pacman`, включая список удаляемых пакетов и зависимостей.
- После `yay` runner проверяет фактическое состояние через `pacman`: success возможен только если целевой пакет действительно исчез из локальной базы.
- GUI после любого результата перечитывает локальное состояние через существующий `AurService.refresh_local_state_async()` и обновляет текущую выдачу без перезапуска Arch Manager.
- Success/cancel/failure записываются в activity log; успешное удаление инвалидирует общий update-state.
- Если пользователь отвечает `No`/операция завершается ненулевым кодом, удаление не помечается успешным; после refresh GUI явно показывает, что пакет остался установлен.

## Safety boundary

- `yay` никогда не запускается Arch Manager от root.
- GUI не строит shell command string, не использует `shell=True`, `eval` или `bash -c`.
- Package name проходит строгую валидацию и передаётся отдельным argv-аргументом после `--`.
- `run-aur-operation.sh` имеет фиксированный whitelist действий.
- Нативный pacman lock остаётся внешней защитой между процессами, а in-process coordinator сериализует official/AUR/system-update операции внутри Arch Manager.
- Устаревшая AUR-карточка не может удалить отсутствующий пакет или пакет, который уже перестал быть foreign/AUR.
- Официальное удаление по-прежнему идёт через `manage_applications.sh`/pacman и не содержит AUR/yay логики.

## Что не входит

- отдельные AUR updates — Stage 7.5;
- массовое удаление AUR;
- unattended/silent remove;
- автоматические ответы на вопросы `yay`;
- обработка edge cases «пакет исчез из AUR после локальной установки» и миграций AUR ↔ official сверх текущей проверки source — финальная доводка Stage 7.6.

## Tests

Добавлен `tests/app_store_aur/test_stage74_remove.py`.

Проверяются:

- local-only remove plan без aurweb-зависимости;
- отсутствие build-tool/system-update gate для удаления;
- запрет удаления отсутствующего пакета;
- запрет удаления пакета, переставшего быть foreign/AUR;
- запрет unconfirmed foreign package;
- pacman lock и capability gate;
- whitelist `install/remove` terminal runner;
- точная команда `yay -Rns -- <package>`;
- отсутствие `--noconfirm`, `eval`, `bash -c`;
- post-remove verification фактического pacman state;
- GUI remove/refresh/activity contract;
- регрессия отдельного official remove backend.
