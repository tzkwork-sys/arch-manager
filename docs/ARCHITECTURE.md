# Arch Manager — архитектура

## Принцип

Проект разделён по ответственности. GUI не выполняет привилегированные команды напрямую; системные изменения проходят через узко ограниченные helper-скрипты и Polkit. Recovery engine остаётся автономным shell-файлом, чтобы аварийная среда имела минимум внутренних зависимостей.

## GUI

Крупные экраны хранят разметку и координацию, а отдельные рабочие процессы вынесены в небольшие модули:

- `src/gui/recovery_page.py` — основной экран Recovery;
- `src/gui/recovery_usb_mixin.py` — USB Recovery UI и progress;
- `src/gui/recovery_workers.py` — фоновые workers Recovery;
- `src/gui/restore_points_page.py` — таблица и состояние экрана точек;
- `src/gui/restore_point_actions_mixin.py` — create/rename/important/delete workflows;
- `src/gui/restore_point_workers.py` — фоновые workers точек восстановления.
- `src/gui/log_page.py` — журнал действий как встраиваемая панель внутри вкладки «Настройки → Журнал действий».
- `src/gui/page_base.py` — единый компактный header основных страниц: заголовок и область основных действий; возврат выполняется постоянной боковой навигацией;
- `src/core/updates.py` — кроме проверки обновлений, содержит read-only чтение локального описания установленного пакета через `pacman -Qi` для кнопок `ⓘ` на странице обновлений.
- `src/gui/update_session.py` — асинхронная QProcess-граница для фиксированного update-сеанса: валидирует AUR package names, запускает только project-owned terminal launcher и читает итоговый status-файл; пароль через GUI не проходит.
- `scripts/run-system-update-terminal.sh` — фиксированный launcher реального терминала. В KDE предпочитает отдельное окно Konsole, для других окружений имеет безопасные fallback-варианты; shell-команды GUI не строятся.
- `scripts/run-system-update-session.sh` — последовательность защитная точка → полный official update → выбранные AUR-пакеты; пароль sudo вводится один раз непосредственно в терминале, единый ticket поддерживается до конца сеанса, а вопросы `pacman`/`yay` остаются нативно интерактивными.

## Магазин приложений — изолированное ядро

Этап 1 магазина приложений находится в отдельном feature-пакете `src/app_store/`. Он не импортирует Qt и не подключён к `src/gui/main_window.py`: GUI-интеграция начинается только на Этапе 2.

Внутри пакета разделены ответственности:

- `models.py` — toolkit-independent модель приложения;
- `appstream.py` — чтение `/usr/share/swcatalog/xml/{core,extra,multilib}.xml.gz` и связанных локальных AppStream-иконок;
- `package_state.py` — только read-only запросы `pacman -Sl`, `pacman -Q`, `pacman -Qu`, `pacman -Si`;
- `catalog.py` — объединение AppStream metadata с состоянием пакетов и фоновый API через `Future`;
- `cache.py` — JSON-cache в XDG user cache, инвалидируемый при изменении AppStream metadata, pacman local/sync DB, `pacman.conf` или формата cache;
- `categories.py` и `diagnostics.py` — нормализация каталога и ранняя диагностика.

Единственная общая внутренняя зависимость на существующий core — безопасный `src.core.command.run_command`; собственные subprocess/helper/Polkit-механизмы магазин на Этапе 1 не создаёт. Установка, удаление и другие изменения системы отсутствуют.

## Privileged Recovery helper

`src/privileged/manage_recovery.sh` — только фиксированный диспетчер. Он загружает четыре локальных модуля из `recovery_lib/`:

- `common.sh` — общие проверки и Btrfs layout;
- `iso.sh` — профиль ArchISO, кэш, сборка и проверка образа;
- `usb.sh` — запись USB, реальный progress, SHA-256 и UEFI BootNext; перед destructive write повторно проверяет fingerprint физического диска и отказывается работать с системными mount/swap;
- `local.sh` — локальная Recovery-среда, boot entry и удаление `@.broken-*`.

Recovery engine сам отвечает за транзакционную замену Btrfs `@`: после начала изменения неожиданная ошибка или сигнал включает аварийный rollback. Источником восстановления может быть только проверенный read-only Btrfs snapshot. Изменяющие команды `manage-recovery` сериализованы root-owned `flock`, поэтому параллельные GUI-процессы не могут одновременно менять Recovery/USB/BootNext состояние.

Установщик копирует эти модули как `root:root 0644` в фиксированный каталог `/usr/local/libexec/arch-manager/recovery_lib`.

## Recovery engine

Активный Recovery engine расположен в `recovery/engine/arch-recovery.sh`. Старый путь `legacy/console-v1.8.4/arch-recovery.sh` сохранён только как короткая совместимая обёртка.

Engine намеренно остаётся одним автономным файлом: для аварийной загрузки это надёжнее, чем цепочка собственных модулей.

## Проверки перед принятием изменений

- `python -m compileall -q src tests`;
- `bash -n` для всех изменённых shell-файлов;
- `src/privileged/manage_recovery.sh --self-test`;
- полный `pytest`;
- архитектурные regression-тесты на границы файлов и установку модулей.

## Полный системный отчёт

`src/core/system_report.py` отвечает за расширенный read-only отчёт раздела «Система». GUI запускает сбор в `QThreadPool`, сохраняет готовый текст в системную папку загрузок и показывает путь к файлу. Основной модуль отчёта не повышает привилегии и использует фиксированный набор диагностических команд. Для защищённых SMART/NVMe-данных `src/core/system_privileged_diagnostics.py` отдельно вызывает через Polkit установленный root-helper `/usr/local/libexec/arch-manager/read-system-diagnostics`; helper поддерживает только фиксированную read-only операцию и не принимает произвольные пути/команды от GUI. Перед сохранением отчёта выполняется базовая редактировка персональных идентификаторов.

## Привилегированная read-only диагностика

Граница привилегий для SMART/NVMe вынесена из обычного диагностического кода. `src/core/system_info.py` по-прежнему не запускает `sudo`/`pkexec`: он умеет принять уже готовый `DiagnosticCheck` для SMART. Получение такого результата находится в `src/core/system_privileged_diagnostics.py`. Root-helper `src/privileged/read_system_diagnostics.py` устанавливается отдельно, проверяется на владельца/права перед запуском, не использует shell и выдаёт структурированный JSON. Polkit action — `org.archmanager.read-system-diagnostics`.

