# Arch Manager GUI

Текущая версия: **0.6.1-stage6**; разработка Stage 7 Recovery продолжается.

Arch Manager использует собственную minimal Recovery-среду для локального восстановления без USB и будущей аварийной флешки.

## Recovery

Локальная Recovery-среда теперь одна общая и не привязана к конкретной точке.

На единой странице **«Восстановление»** сверху находится компактный список точек восстановления, а ниже — состояние локальной Recovery-среды. Arch Manager автоматически проверяет среду и при необходимости подготавливает/обновляет её. Пользователь не выбирает snapshot для Recovery boot в обычной системе: нужная точка выбирается уже после перезагрузки. Когда среда готова, доступна кнопка **«Перезагрузить в режим восстановления»**.

После перезагрузки автономный Recovery UI сам читает актуальный `@snapshots` и предлагает выбрать нужную точку уже вне работающего корня `@`. Видимые номера всегда идут подряд `1…N`, а реальные Snapper ID с возможными пропусками остаются внутренними идентификаторами. После выбора точки восстановление запускается автоматически без повторных подтверждений; процентных progress-bar нет. После успешного завершения остаётся один финальный запрос: любая клавиша — перезагрузить систему, `0` — выйти без перезагрузки.

Список точек использует отдельный быстрый fingerprint Snapper и локальный cache в `~/.cache/arch-manager`: если metadata snapshots не менялась, таблица восстанавливается из cache без повторного полного чтения. Поле эксклюзивного размера убрано, поэтому фоновый `btrfs filesystem du` для каждой точки больше не выполняется. Recovery readiness также хранит локальный cache по дешёвому fingerprint; тяжёлая SHA-256 проверка повторяется только после реального изменения boot/ISO/profile/engine состояния.

ISO хранится отдельно в `/data/Arch-Recovery/Arch-Manager-Recovery.iso`. Его cache проверяется по Recovery profile/engine metadata и SHA-256. Новые snapshots не вызывают пересборку ISO.

Для установки/обновления ограниченного Stage 7 helper:

```bash
./scripts/install-stage7-recovery-helper.sh
```

Запуск GUI из корня проекта:

```bash
./scripts/run-gui.sh
```

Подробности: `docs/STAGE_7.md` и `docs/CURRENT_STATE.md`.

Stage 6 содержит центр обслуживания с очисткой package cache, orphan packages, journal, thumbnails и корзин. Stage 5 содержит центр обновлений и защитные точки восстановления.

Раздел **«Приложения»** (магазин официальных desktop-приложений Arch Linux) доведён до Stage 6: каталог работает поверх локальных AppStream metadata, использует безопасный install/remove helper, имеет offline-friendly cache и интегрирован с общей страницей системных обновлений без partial upgrades.

Интерфейс основных разделов использует компактный общий header: возврат к обзору, заголовок и основные действия выровнены по одной сетке. На странице обновлений поиск пакета убран; кнопка `ⓘ` напротив пакета показывает его локальное описание, а технический отчёт открывается в отдельном прокручиваемом окне.

Список точек восстановления сохраняет визуальную нумерацию отдельно от технического Snapper ID и поддерживает выделение нескольких строк (`Ctrl`/`Shift`) с одним подтверждением пакетного удаления.

## Архитектурный рефакторинг

Крупные GUI и Recovery-helper разделены по ответственности без изменения пользовательской функциональности. Активный Recovery engine перенесён из `legacy` в `recovery/engine/`. Подробности: `docs/ARCHITECTURE.md`.

### Recovery 1.8.18 — безопасный порядок запуска TTY + boot-log/watchdog
Исправлена регрессия 1.8.17: в обычном local/USB режиме поиск системного Btrfs выполняется до позднего переключения tty1 в UTF-8, а его ранний пользовательский вывод подавляется. После успешного поиска и монтирования tty1 один раз переводится в UTF-8, экран очищается и только затем рисуется русскоязычный интерфейс. Это сохраняет защиту от KMS/TTY-зависания и одновременно не возвращает русский транслит. Для ручного режима консоль готовится отдельной обёрткой перед возможными интерактивными вопросами. Постоянный boot-log и 60-секундный watchdog сохранены. Версия Recovery 1.8.18, iso_version=9.



### Recovery 1.8.21: resilient Cyrillic console font fallback

Recovery no longer aborts when a single hard-coded console font name cannot be loaded. Immediately before the first menu draw it resolves an installed Cyrillic-capable PSF file from a deterministic kbd fallback list (LatArCyrHeb/LatGrkCyr/Cyr/UniCyr), attempts a font load, enables Linux-console UTF-8 mode, and continues even if the kernel rejects all font changes. Built-in menu labels remain English/ASCII, while UTF-8 restore-point descriptions are preserved unchanged. This removes the fatal `Could not load the Recovery console font` regression while retaining a single menu draw. Engine version is 1.8.21 and `iso_version` is 12.

### Recovery 1.8.22: single stable menu and correct Cyrillic descriptions

Recovery now decodes Snapper XML numeric character references into UTF-8 bytes without depending on the process locale, including legacy double-escaped `&amp;#xNNNN;` descriptions. The recovery UI takes a single-instance lock, and systemd no longer resets or hangs up tty1 around the wizard; the engine itself performs the only screen clear immediately before the final menu draw. `iso_version` is 13 so the cached Recovery ISO is forced to rebuild.

### Recovery 1.9.0: one visible draw and full Cyrillic descriptions

- Recovery UI is prepared off-screen on dedicated `/dev/tty2` and activated exactly once after the table is complete.
- `arch-manager-recovery.service` uses `Type=idle`; tty2 getty is masked/conflicted to prevent later console takeover.
- Known legacy `Arch Manager: pered obnov...` automatic descriptions are normalized for display to Cyrillic without modifying snapshot metadata.
- Description text is no longer truncated with ellipsis. Compact table widths fit the standard automatic description in 80 columns; longer text is wrapped on continuation lines.
- Recovery version is 1.9.0 and Archiso profile version is 14 to invalidate the previous cached ISO.

### Recovery 1.9.0: clean console-menu rewrite

The accumulated hidden-tty2/chvt startup path was removed. Recovery now has one systemd owner for `/dev/tty1`, one console setup pass, one screen clear and one menu render. There is no hidden VT switch and no second copy of the table. Restore-point descriptions keep full UTF-8 text; the known legacy `pered obnov...` automatic description is repaired only at display time. `iso_version=15` invalidates the previous cached ISO.
