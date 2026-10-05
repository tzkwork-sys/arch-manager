# Arch Manager — App Store Stage 7.3: установка AUR

**Дата:** 1 октября 2026  
**Статус:** реализовано; установка AUR включена через интерактивный terminal runner. Удаление и отдельные AUR updates остаются Stage 7.4/7.5.

## Что реализовано

Stage 7.3 добавляет установку найденного AUR-пакета, не превращая официальный магазин в смешанный pacman/yay backend.

Добавлены:

- `src/app_store/aur/planner.py` — read-only planner установки;
- свежая точная перепроверка package name через aurweb перед запуском операции; AUR cache не является источником истины для установки;
- capability gate для `yay`, `git`, `makepkg`, `base-devel`, `pacman` и запрет AUR workflow при запуске Arch Manager от root;
- проверка `/var/lib/pacman/db.lck`;
- gate полного системного обновления: при ожидающих официальных обновлениях установка AUR блокируется и GUI предлагает перейти на страницу системных обновлений;
- общий `PackageTransactionCoordinator`, который сериализует AUR install, операции официального App Store и системный update-session внутри одного процесса Arch Manager;
- `scripts/run-aur-terminal.sh` — фиксированный launcher реального терминала с приоритетом Konsole;
- `scripts/run-aur-operation.sh` — whitelist runner, который на Stage 7.3 принимает только `install` и валидированное package name;
- интерактивный запуск `yay -S --aur -- <package>` без `--noconfirm`, `--skipchecksums`, `--skippgpcheck` и без shell evaluation;
- дополнительный пользовательский `flock` для защиты от параллельных AUR runners;
- runner отказывается работать от root;
- итог операции передаётся GUI через короткий status-file; пароль sudo GUI не получает и не сохраняет;
- состояние «Установка открыта в терминале»;
- после завершения терминала локальное состояние перечитывается через `pacman -Qm`, карточка/детали обновляются, а AUR search запускается заново без cache;
- activity log для success/cancel/failure;
- инвалидируется общий update-state после успешной установки;
- installed AUR package больше не предлагает установку; удаление намеренно оставлено на Stage 7.4.

## Safety contract

Stage 7.3 сохраняет следующие ограничения:

- `yay` и `makepkg` Arch Manager сам никогда не запускает от root;
- GUI не собирает shell command string и не использует `shell=True`;
- terminal runner имеет фиксированный whitelist action;
- package name проходит отдельную строгую проверку;
- официальный `/usr/local/libexec/arch-manager/manage-applications` не знает про AUR и не изменён;
- AUR install не запускает скрытый `yay -Syu` и не подменяет полный системный update;
- вопросы `yay`, review PKGBUILD/diff, providers/conflicts и sudo остаются видимыми пользователю в настоящем терминале;
- package database mutation сериализуется внутри Arch Manager, а нативный pacman lock остаётся последней защитой между процессами.

## UX

В AUR details для неустановленного пакета появилась кнопка **«Установить через yay»**.

Перед открытием терминала Arch Manager:

1. проверяет локальные возможности AUR;
2. проверяет, что пакет с таким именем ещё не установлен;
3. проверяет отсутствие ожидающего полного системного обновления;
4. заново подтверждает точное имя пакета через aurweb;
5. показывает подтверждение с предупреждением о community-maintained AUR и отмечает out-of-date/orphan состояние;
6. только после подтверждения открывает терминал.

Пока terminal process активен, окно деталей нельзя случайно закрыть: оно отслеживает итог операции и освобождает coordinator после завершения.

## Что намеренно не входит в Stage 7.3

- удаление AUR (`Stage 7.4`);
- обновление одного/всех AUR-пакетов (`Stage 7.5`);
- AUR в режимах общего списка «Установленные»/«Обновления»;
- unattended/silent AUR installation;
- автоматические ответы на вопросы yay;
- собственный dependency/build resolver;
- stale-lock/cross-instance hardening сверх native pacman lock и отдельного AUR flock — финальная доводка остаётся Stage 7.6.

## Tests

Добавлен `tests/app_store_aur/test_stage73_install.py`.

Проверяются:

- строгая валидация package name;
- fresh aurweb confirmation без cache;
- system-update gate;
- already-installed gate;
- root/yay/base-devel capability gates;
- pacman lock;
- общий coordinator;
- whitelist terminal runner;
- отсутствие опасных non-interactive flags и shell evaluation;
- GUI routing через planner + terminal process;
- неизменность официального privileged helper;
- общий coordinator для official App Store и system update-session.
