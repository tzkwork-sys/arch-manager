# Stage 7 — Recovery

Текущее состояние: minimal Arch Manager Recovery-среда работает и является **одной общей средой**, не привязанной к конкретному snapshot. Она загружается двумя путями: локально с диска или с аварийной USB-флешки.

## Архитектура readiness

Обычный Arch Manager проверяет четыре независимых компонента:

1. Btrfs-схема: текущий корень `@` и отдельный `@snapshots`.
2. systemd-boot для локальной one-shot загрузки.
3. Recovery ISO на отдельной файловой системе `/data`.
4. Подготовленные kernel/initramfs + `arch-manager-recovery.conf`.

`prepared_snapshot_id` больше не является частью состояния. Основной критерий — `environment_ready`.

Read-only helper версии 2 возвращает:

- `systemd_boot`;
- `iso_ready`;
- `boot_files_ready`;
- `environment_ready`;
- `fingerprint` — быстрый read-only токен изменения состояния.

Полный режим helper проверяет SHA-256 ISO/kernel/initramfs. Режим `--fingerprint` не читает содержимое больших образов: он использует только stat/metadata, Btrfs mount metadata и состояние systemd-boot.

ISO считается актуальным только при совпадении metadata с текущими Recovery profile/engine и SHA-256 образа. Boot entry дополнительно привязан к UUID системного раздела, UUID/пути ISO, profile SHA-256, ISO SHA-256 и SHA-256 локальных kernel/initramfs.

## Обычный Arch

Страница «Восстановление» объединяет управление snapshots и локальную Recovery-среду: таблица точек находится сверху, а компактная карточка среды — ниже. Snapshot для самой Recovery-загрузки заранее не выбирается.

При первом открытии страницы за запуск Arch Manager:

- выполняет полную read-only проверку;
- если общей Recovery-среды нет или она устарела — автоматически запускает её подготовку через ограниченный Polkit-helper;
- если среда готова и есть хотя бы одна точка — активирует кнопку «Перезагрузить в режим восстановления».

При повторном переходе на страницу и после перезапуска GUI выполняется быстрый fingerprint-check. Если fingerprint не изменился, проверенное readiness-состояние берётся из локального cache и тяжёлые SHA-256 проверки не повторяются. Fingerprint общей Recovery-среды отслеживает ISO/profile/engine/boot-файлы и Btrfs layout, но не состав `@snapshots`, поэтому создание или удаление точки не заставляет заново хэшировать 851-МБ ISO. При изменении fingerprint автоматически выполняется новая полная проверка.

Подготовка не выполняет восстановление, не заменяет `@` и не перезагружает компьютер. Новые snapshots не требуют пересборки Recovery ISO.

Кнопка отдельной ручной подготовки и большая панель служебной информации удалены. Редкие технические сведения доступны через маленькую кнопку `ⓘ`.

## Когда пересобирается ISO

ISO пересобирается только если:

- его нет;
- отсутствует или не совпадает metadata;
- изменился Recovery profile;
- изменился Recovery engine;
- SHA-256 ISO не совпадает с metadata.

Если ISO актуален, повторная подготовка только проверяет/обновляет общие boot-файлы при необходимости.

## Boot entry

Постоянная локальная запись больше не содержит обязательный `arch_manager_snapshot=<id>`.

Основные параметры:

- `arch_manager_local=1`;
- `arch_manager_root_uuid=...`;
- `arch_manager_profile_sha256=...`;
- `arch_manager_iso_sha256=...`;
- hashes локальных kernel/initramfs;
- `img_dev=...` + `img_loop=...`.

`arch_manager_snapshot=<id>` остаётся поддерживаемым Recovery engine как optional preselection для будущих сценариев, но основной GUI его не использует.

## Recovery mode

После загрузки автономный `arch-recovery.sh`:

1. находит системный Btrfs-раздел;
2. монтирует top-level;
3. читает **актуальный** список `@snapshots`;
4. показывает точки на чёрном Recovery-экране с компактной визуальной нумерацией `1…N`;
5. визуальный номер переводится во внутренний Snapper ID, поэтому удаление старых snapshots не оставляет дыр в списке;
6. пользователь выбирает нужную точку уже там;
7. сразу после выбора Recovery выполняет preflight и восстановление автоматически — второго подтверждения нет;
8. progress-bar не используется: показываются только короткие текстовые этапы;
9. после успешного восстановления остаётся один финальный запрос: любая клавиша — перезагрузка, `0` — выход без перезагрузки.

Выход `0` на экране выбора точки по-прежнему отменяет восстановление и в local/USB Recovery mode автоматически возвращает компьютер в обычный Arch. Ручной fallback-запуск скрипта без маркеров local/USB сохраняет обычное завершение без принудительной перезагрузки.


## Аварийная USB-флешка

На странице «Восстановление» есть единый переключатель **«Способ восстановления»** с вариантами **«Локальное восстановление»** и **«Аварийная USB-флешка»**. Одновременно показывается только выбранная панель. USB работает с тем же Recovery ISO и тем же автономным `arch-recovery.sh`, что и локальная one-shot загрузка, поэтому выбор точек, проверки и само переключение Btrfs `@` не дублируются во второй реализации.

### Создание / обновление

GUI read-only сканирует `lsblk` и предлагает только whole-disk устройства, которые помечены как removable или имеют transport `usb`. Диск текущего корня исключается ещё на стороне GUI. Перед записью отдельное предупреждение показывает модель, размер и `/dev/...` выбранного носителя.

Изменяющая операция проходит через root-owned `manage-recovery`:

1. повторно убеждается, что аргумент — целый USB/removable disk, а не раздел;
2. исключает системный диск и диск, на котором хранится Recovery ISO;
3. при необходимости пересобирает trusted Recovery ISO из project-owned profile/engine;
4. размонтирует разделы целевого USB;
5. записывает ISO на весь диск;
6. выполняет `sync` и перечитывает таблицу разделов;
7. читает с флешки ровно размер ISO и сверяет SHA-256 с trusted образом.

Метка ISO — `AM_RECOVERY`; по ней обычный read-only USB scan показывает, что накопитель уже является Arch Manager Recovery media.

### «Перезагрузиться с флешки»

Для one-click boot требуется UEFI и `/usr/bin/efibootmgr`. Перед перезагрузкой helper ещё раз проверяет, что байты выбранной флешки совпадают с текущим trusted Recovery ISO. Если firmware публикует встроенную запись **EFI USB Device** и подключён ровно один USB/removable диск, Arch Manager **сначала** направляет `BootNext` на эту firmware-managed запись. Это повторяет рабочий путь выбора флешки через F12 и обходит firmware, которое принимает custom HD()/File() запись, но затем показывает `Boot File`/`Boot Failed`. Если встроенная USB-запись недоступна, helper переиспользует точную запись **Arch Manager Recovery USB** для текущей partition signature либо создаёт её для `\EFI\BOOT\BOOTX64.EFI` через `efibootmgr -C` **без `-w`**. После `efibootmgr -n` значение `BootNext` всегда перечитывается и проверяется. Постоянный `BootOrder` не переписывается. Диагностика UEFI-операций сохраняется в `/var/log/arch-manager/recovery-usb-boot.log`.

USB boot configuration передаёт `arch_manager_usb=1`. Recovery engine использует этот маркер только для поведения самой автономной сессии: при отмене он корректно перезагружает компьютер обратно в обычную систему. Сам алгоритм поиска `@snapshots` и восстановления полностью общий с local mode.

Если система загружена не в UEFI или `efibootmgr` отсутствует, создание флешки остаётся доступным. Загрузиться с неё можно обычным Boot Menu прошивки.

## Надёжность сборки ISO

Project-owned `packages.x86_64` обязательно содержит `mkinitcpio`, `mkinitcpio-archiso` и `syslinux`: первые два нужны для Archiso initramfs hooks, а `syslinux` требуется выбранному `bios.syslinux` boot mode. Helper валидирует это до запуска `mkarchiso`; подробный вывод неудачной сборки сохраняется в `/var/log/arch-manager/recovery-build.log`.

## Safety boundary

GUI не выполняет shell-команды напрямую. Изменяющие операции проходят через фиксированный root-owned helper. Readiness helper остаётся строго read-only.

### Усиление безопасности Recovery 1.8.11

- новая точка считается успешно созданной только после проверки, что это реальный read-only Btrfs snapshot;
- Recovery engine показывает и принимает только read-only snapshots и при неожиданной ошибке/сигнале после начала замены `@` пытается автоматически откатить транзакцию;
- отдельный boot-раздел сверяется с восстановленным `/etc/fstab`, системным Btrfs-разделом и фактически смонтированным устройством до чтения/записи;
- имя `@.broken-*` проверяется на коллизию перед переименованием текущего корня;
- USB-накопитель получает физический fingerprint (размер/model/serial/WWN/transport), который повторно проверяется root-helper непосредственно перед записью; системные/служебные mount points и активный swap на выбранном диске блокируют `dd`;
- все изменяющие Recovery-действия сериализованы root-owned `flock`, поэтому два экземпляра GUI не могут одновременно готовить/переписывать одну Recovery-среду или запускать конкурирующие USB/BootNext операции;
- временная SHA-256 проверка USB использует приватный `mktemp -d`, а дочерние `dd`/hash процессы очищаются при прерывании;
- Recovery ISO сначала копируется во временный файл на той же файловой системе и только затем атомарно переименовывается; fingerprint профиля теперь учитывает и symlink targets;
- на автономном экране сохраняется русская UTF-8 локаль/кириллический шрифт, а regression-тест запрещает возврат русского транслита латиницей.

Тесты Stage 7 не выполняют:

- reboot;
- `bootctl set-oneshot`;
- реальное восстановление;
- замену/удаление `@`;
- destructive Btrfs operations.

`archiso_loop_mnt`, русская locale/font, отключение systemd-firstboot и non-blocking настройка консоли сохраняются. Консольный helper ждёт завершения udev/KMS, явно переключает `tty1` в UTF-8 и сначала пробует Unicode-font `LatArCyrHeb-16`, затем несколько Cyrillic fallback-fonts. Ошибка загрузки шрифта остаётся косметической и никогда не блокирует Recovery wizard.

## Чистый TTY Recovery

Boot options локальной, UEFI-USB и BIOS-USB Recovery-загрузки включают `quiet loglevel=3`, `systemd.show_status=false` и сниженный udev log level. Это не скрывает диагностику из `journalctl`/`dmesg`, но не позволяет несущественным аппаратным сообщениям (например, SOF audio firmware, который Recovery не использует) печататься поверх интерактивного мастера на `tty1`.

Recovery engine 1.8.5 fixes snapshot selection under `set -u`: local variables are initialized before the selected snapshot path is expanded, and the selected snapshot is revalidated before any restore preflight. The Recovery list remains newest-first.

## Управление сохранёнными прежними корнями

После успешного восстановления прежний рабочий `@` сохраняется как верхнеуровневый
`@.broken-YYYYMMDD-HHMMSS`. Arch Manager обнаруживает такие копии через root-owned
read-only probe и показывает управление ими через шестерёнку на карточке локального
восстановления. Удаление всегда ручное, требует административного подтверждения и
принимает только имена строгого формата `@.broken-*`. Helper предварительно проверяет,
что это верхнеуровневый Btrfs subvolume и что внутри нет неожиданных вложенных subvolume;
рекурсивное удаление намеренно не используется. `@snapshots` этим действием не затрагивается.

После реального отката Recovery-среда может стать устаревшей относительно восстановленной
версии системных компонентов. Это ожидаемое состояние: GUI распознаёт его и один раз
синхронизирует общую Recovery-среду автоматически.
Recovery 1.8.6 отображает время точек восстановления в Europe/Moscow (MSK, UTC+3) и сохраняет текущий `~/.config/ksmserverrc` при системном rollback, чтобы подтверждение выключения/перезагрузки Plasma не возвращалось к старому состоянию из snapshot.
Recovery 1.8.7 использует ту же пользовательскую нумерацию точек, что GUI: сортировка по дате создания от новых к старым и видимые номера 1..N. Технический Snapper ID сохраняется отдельно только для внутренних операций и диагностики.
Recovery 1.8.8 исторически добавлял процентный progress-bar. В Recovery 1.8.10 он удалён: этапы выполняются быстро, поэтому на TTY остаются только короткие текстовые статусы без процентов и анимации.

Recovery 1.8.9 исправляет автономный экран локального и USB-восстановления: дата из Snapper `info.xml` теперь явно трактуется как UTC и затем переводится в `Europe/Moscow`, поэтому на экране показывается реальное московское время (МСК, UTC+3). Стартовые сообщения приведены к нормальному русскому тексту без транслита. Версия Recovery ISO-профиля повышена до 3, чтобы ранее собранный кэшированный ISO не считался актуальным и был пересобран перед следующей локальной подготовкой или записью USB-флешки.

Recovery 1.8.10 сокращает интерактивный сценарий local/USB до двух решений пользователя: выбор точки (или `0` для выхода) и финальный выбор после успешного восстановления. После выбора точки нет дополнительного вопроса «начать/отменить» — все preflight-проверки и транзакция выполняются автоматически. Финальный prompt одноклавишный: любая клавиша запускает reboot, `0` завершает Recovery без перезагрузки. Версия ISO-профиля повышена до 4 для обязательного обновления ранее собранного образа.


### Recovery 1.8.12 — USB error-path fix

USB validation no longer runs inside a command substitution that can swallow `ERROR\t<code>` output under `set -e`. The selected-device fingerprint now uses only fields consistently visible at both desktop-user and root privilege levels, avoiding false device-change failures caused by USB bridges exposing SERIAL/WWN differently. Unknown helper failures surface a return code and bounded stderr detail instead of an unhelpful generic dialog.

### Recovery 1.8.13 — kernel USB identity token

Повторная ложная ошибка «флешка изменилась или была переподключена» устранена архитектурно. GUI и root-helper больше не строят anti-race fingerprint из представления `lsblk`/udev (`MODEL`, `TRAN`, `RM`, `RO`), которое на отдельных USB-мостах может отличаться между пользовательским и root-контекстом. Вместо этого используется privilege-independent kernel token: `MAJ:MIN`, канонический объект `/sys/dev/block/<MAJ:MIN>`, число секторов, removable и read-only из sysfs. Token имеет версию `k1:`; helper повторно сверяет его после размонтирования и непосредственно перед destructive `dd`. Если helper уже обновился, а старый GUI всё ещё работает в памяти, helper распознаёт legacy 1.8.11/1.8.12 token и либо безопасно принимает его, либо явно просит перезапустить приложение вместо ложного сообщения о переподключении флешки. Системный диск, диск Recovery ISO, служебные mount points и swap по-прежнему блокируются независимо от token-проверки.


### Recovery 1.8.14 — принудительное обновление кириллической Recovery-среды

Стартовые сообщения аварийной среды должны быть только на русском кириллицей (технические названия Arch Linux, USB, Btrfs и UUID остаются как есть). После обнаружения старой Recovery-флешки со строками вида `vosstanovlenie sistemy` версия движка поднята до 1.8.14, а `iso_version` — до 5. Это намеренно инвалидирует кэш Recovery ISO: локальная среда будет пересобрана из текущего движка, а аварийную USB-флешку нужно один раз перезаписать, чтобы на неё попал новый образ. Regression-тест дополнен фразами со старого экрана, чтобы русская транслитерация больше не вернулась.


### Recovery 1.8.15 — стабилизация Unicode TTY перед выводом интерфейса

Причина строк вида `vosstanovlenie sistemy` оказалась не в тексте Recovery-скрипта: исходник уже содержал кириллицу. На части загрузок tty1 успевал получить раннюю таблицу глифов до окончательного переключения framebuffer → DRM/KMS, поэтому первые кириллические строки визуально превращались в латинские аналоги, а строки, выведенные немного позже, уже были нормальной кириллицей. Recovery теперь ждёт стабилизации udev/TTY, несколько раз повторно включает UTF-8 и загружает `UniCyr_8x16`, затем после обнаружения системного раздела повторяет настройку, очищает экран и только после этого рисует русский интерфейс. Версия движка поднята до 1.8.15, `iso_version` — до 6, чтобы локальный ISO гарантированно пересобрался. Аварийную USB-флешку после установки этой версии нужно один раз создать заново.


### Recovery 1.8.16 — безопасная инициализация TTY без зависания загрузки

Версия 1.8.15 слишком агрессивно пыталась исправить кириллицу: перед запуском мастера выполнялись `udevadm settle`, повторные `setfont` и искусственные задержки во время перехода framebuffer → DRM/KMS, а затем похожая настройка повторялась из самого Recovery-движка. На части оборудования это может задерживать или подвешивать переход консоли. В 1.8.16 эти операции убраны: шрифт `UniCyr_8x16` загружает штатный `systemd-vconsole-setup`, а Arch Manager только один раз включает UTF-8 на tty1 непосредственно перед отрисовкой интерфейса. Также исправлен порядок запуска systemd-unit: убран `After=multi-user.target`, сервис запускается как `Type=simple` после `systemd-vconsole-setup.service`. `iso_version` поднят до 7, поэтому Recovery ISO будет пересобран.


### Recovery 1.8.17 — постоянный boot-log и защитный watchdog запуска

Recovery теперь ведёт ранний журнал запуска в `/run/arch-manager/recovery-boot.log` и после обнаружения системного Btrfs сохраняет его в `@snapshots/.arch-manager-recovery/boot/` (последняя копия — `last-boot.log`). В журнал пишутся стадии: старт engine, параметры режима, UTF-8 TTY, поиск системного раздела, mount верхнего уровня Btrfs и построение списка точек. При зависании старта отдельный `arch-manager-recovery-watchdog.service` ждёт 60 секунд. Если мастер не дошёл до готового списка точек, watchdog дописывает `systemctl`, journal, `lsblk`, `findmnt` и хвост `dmesg`, сохраняет лог и прекращает бесконечное ожидание: локальный Recovery возвращается обычной перезагрузкой в Arch Linux, USB/manual Recovery выключает компьютер, чтобы не получить цикл загрузки с флешки. Watchdog отключается до выбора точки и не участвует в самой операции восстановления. Одновременно сохранено исправление 1.8.16: нет `udevadm settle`, циклов `setfont` и искусственных задержек во время KMS/TTY handover; шрифт `UniCyr_8x16` загружает `systemd-vconsole-setup`. Версия engine — 1.8.17, `iso_version` — 8, поэтому локальный Recovery ISO должен быть пересобран, а USB Recovery — один раз перезаписан.


### Recovery 1.8.19: simplified ASCII console path

To remove console-font and UTF-8 handover instability, the autonomous Recovery UI now uses a deliberately simple ASCII-only English interface. The runtime locale is `C`, the console keymap is `us`, no custom `FONT` is loaded, and the separate `setup-recovery-console` helper plus `systemd-vconsole-setup.service` dependencies were removed from the Recovery services. Moscow timezone handling remains (`Europe/Moscow`, visible timestamps use `MSK`). Snapshot descriptions containing non-ASCII text are shown as `[non-ASCII description]` rather than risking unreadable glyphs. Safety-critical Btrfs, snapshot, boot-partition, rollback and transaction checks remain unchanged. Engine version is `1.8.19`; `iso_version` is `10`, forcing a fresh local Recovery ISO. Existing Recovery USB media should be recreated once after installing this version.


### Recovery 1.8.20: single console initialization with UTF-8 descriptions

Recovery keeps all built-in interface labels in English/ASCII, but restore-point descriptions are user data and are now passed through unchanged, so Russian/Cyrillic descriptions remain readable. The previous `[non-ASCII description]` replacement is removed and descriptions are no longer byte-truncated. To eliminate the visible two-stage menu redraw, the live image masks `systemd-vconsole-setup.service`; the Recovery engine becomes the only console owner. It performs exactly one `setfont UniCyr_8x16` plus one UTF-8 console-mode switch after the system Btrfs has been discovered and mounted, immediately before the first `clear` and first menu draw. There are no `udevadm settle` calls, repeated font loads, sleeps, or a separate console helper. Safety-critical Btrfs, snapshot, boot-partition, rollback, bootlog, and watchdog checks are unchanged. Engine version is `1.8.20`; `iso_version` is `11`, forcing a fresh local Recovery ISO. Existing Recovery USB media should be recreated once after installing this version.


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
