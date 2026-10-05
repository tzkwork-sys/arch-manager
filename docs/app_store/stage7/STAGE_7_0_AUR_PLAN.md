# Arch Manager — App Store Stage 7.0: план интеграции AUR

**Дата:** 1 октября 2026  
**Статус:** подготовительный этап; runtime-код AUR ещё не внедряется  
**Цель следующего цикла:** добавить в магазин Arch Manager поиск, установку, обновление и удаление приложений из AUR, не ломая уже завершённый магазин официальных репозиториев.

---

## 1. Главный архитектурный принцип Stage 7

Stage 7 реализуется **максимально изолированно от существующего кода официального магазина**.

Нельзя превращать текущие `catalog.py`, `transactions.py`, `manage_applications.sh` и GUI официального магазина в смесь pacman/AUR-логики.

Правило:

> Официальный магазин остаётся отдельным стабильным контуром. AUR подключается как второй источник и второй backend через небольшое количество явных интеграционных точек.

Это означает:

- существующий каталог AppStream + pacman продолжает работать как раньше;
- существующий root-helper для официальных пакетов не превращается в AUR-helper;
- AUR получает собственные модели, сетевой клиент, определение состояния, planner, runner и ошибки;
- AUR GUI получает собственные widgets/dialogs/workers там, где логика заметно отличается;
- существующие `page.py` / `details.py` получают только минимальный adapter/route-код;
- тесты AUR хранятся отдельно от regression-тестов официального магазина;
- отключение/поломка AUR не должно мешать открытию и работе официального магазина.

---

## 2. Что уже есть в Arch Manager и что нельзя ломать

По состоянию архива перед Stage 7:

- официальный магазин строится из AppStream metadata и состояния pacman;
- `Application` является toolkit-independent моделью официального приложения;
- каталог строится через `AppCatalogService`;
- установка/удаление официальных приложений проходит через `PackageActionService`;
- привилегированная часть ограничена `/usr/local/libexec/arch-manager/manage-applications`;
- helper принимает только фиксированные действия и проверенные package names;
- helper дополнительно проверяет, что пакет действительно находится в официальном репозитории;
- установка официального пакета блокируется, если требуется полное системное обновление;
- одновременно допускается только одна пакетная операция Arch Manager;
- GUI не выполняет pacman напрямую;
- полное обновление официальных пакетов остаётся на отдельной странице «Обновления»;
- Stage 6 добавил финальные cache/offline/retry механизмы и завершил официальный магазин как самостоятельную функциональность.

### Инварианты Stage 7

Stage 7 не должен менять эти правила для официальных пакетов.

В частности, **запрещено**:

- запускать `yay -Syu` вместо существующего системного обновления Arch Manager;
- разрешать AUR-коду устанавливать официальный пакет в обход текущего helper-а;
- ослаблять проверку официального `manage-applications` helper-а;
- запускать `makepkg` или `yay` от root;
- добавлять `--noconfirm` к AUR install/update как поведение по умолчанию;
- выполнять shell-команды, собранные строковой конкатенацией из имени пакета;
- объединять AUR и официальный каталог в одну огромную кешируемую базу, которую невозможно независимо отключить или восстановить.

---

## 3. Почему AUR нельзя реализовать как ещё один «тихий pacman»

AUR принципиально отличается от официального репозитория Arch:

1. В AUR хранятся инструкции сборки (`PKGBUILD`), а не только доверенные готовые пакеты официальных репозиториев.
2. Сборка через `makepkg` должна выполняться от обычного пользователя, а не от root.
3. `PKGBUILD` может выполнять произвольные команды во время сборки; пользователю должна оставаться возможность его проверить.
4. AUR helper (`yay`) может задавать интерактивные вопросы: clean build, diff/PKGBUILD review, providers, PGP keys, conflicts и т.п.
5. AUR helper может использовать pacman для repo-зависимостей, но это не должно превращаться в скрытый второй механизм системного обновления.

### Решение для первой версии

**AUR install/update/remove выполняются отдельным пользовательским terminal runner через `yay`.**

GUI:

- планирует операцию;
- показывает понятное подтверждение;
- открывает терминал;
- runner запускает `yay` как обычный пользователь;
- пользователь видит все вопросы `yay` и может проверить изменения;
- после завершения GUI перечитывает состояние пакетов и обновляет карточки.

Для первой версии это безопаснее и прозрачнее, чем попытка полностью автоматизировать интерактивный AUR workflow внутри Qt.

---

## 4. Выбранный backend: yay

Stage 7 v1 ориентируется на **yay** как единственный поддерживаемый AUR helper.

Причины:

- уже используется на целевой системе;
- умеет искать AUR, строить зависимости, скачивать PKGBUILD, собирать и устанавливать пакеты;
- умеет AUR-only режим;
- умеет отдельно обновлять установленные AUR-пакеты;
- сохраняет нормальный интерактивный workflow проверки;
- не требует писать собственный dependency/build resolver в Arch Manager.

### Что Arch Manager не должен делать

Arch Manager не должен пытаться стать собственным AUR helper-ом и самостоятельно:

- source-ить PKGBUILD;
- разрешать дерево AUR-зависимостей;
- выполнять `makepkg` за `yay`;
- реализовывать импорт PGP ключей;
- автоматически выбирать providers/conflicts без участия пользователя.

### Проверка совместимости yay

Вместо жёсткой привязки к номеру версии AUR subsystem должен выполнить capability probe:

- найден ли `/usr/bin/yay` или `yay` в PATH;
- выполняется ли `yay --version`;
- поддерживаются ли необходимые операции текущей установленной версией;
- доступны ли `git`, `makepkg` и `base-devel`;
- приложение запущено не от root.

Если `yay` отсутствует, официальный магазин продолжает работать, а AUR UI показывает отдельное состояние «AUR недоступен» и инструкцию по включению поддержки.

---

## 5. Источники данных AUR

### 5.1. Поиск и metadata — aurweb RPC v5

Для read-only поиска и карточек не нужно парсить цветной CLI output `yay -Ss`.

Основной источник: **aurweb RPC v5 JSON**.

Используем:

- `search` по `name-desc`;
- `info` для точного получения metadata;
- HTTP timeout;
- User-Agent Arch Manager;
- ограниченный размер ответа;
- JSON validation;
- короткий cache;
- graceful offline mode.

Основные данные:

- package name;
- package base;
- version;
- description;
- upstream URL;
- maintainer;
- votes;
- popularity;
- out-of-date flag;
- first submitted;
- last modified;
- depends / makedepends / optdepends / checkdepends;
- conflicts / provides / replaces;
- licenses;
- keywords.

### 5.2. Состояние локальной системы

Для локального состояния используются штатные pacman read-only запросы.

Важно:

`pacman -Qm` означает **foreign package**, а не гарантированно «пакет из AUR».

Поэтому алгоритм:

1. получить foreign packages;
2. сгруппировать их имена;
3. выполнить batched AUR `info` запрос;
4. пакет есть в AUR → `aur_confirmed=true`;
5. пакета в AUR нет → `foreign_unknown`, и магазин не выдаёт его за AUR-пакет.

Это защищает от неверной классификации вручную собранных/локальных пакетов.

### 5.3. Сравнение версий

Для сравнения установленной и AUR версии использовать штатную Arch-совместимую семантику версии (`vercmp`/libalpm-compatible comparison), а не Python string comparison.

Для `-git` / VCS пакетов обычное сравнение AUR version может быть недостаточным. В Stage 7 v1 они показываются, но отдельная devel-check логика включается только на специальном подэтапе и через возможности `yay --devel`.

---

## 6. Модель данных: AUR отдельно от Application

Не расширять текущий `Application` десятками AUR-only полей.

Ввести отдельную модель, например:

`src/app_store/aur/models.py`

```text
AurPackage
  name
  package_base
  version
  description
  upstream_url
  aur_url
  maintainer
  votes
  popularity
  out_of_date
  first_submitted
  last_modified
  depends
  make_depends
  opt_depends
  check_depends
  conflicts
  provides
  replaces
  licenses
  keywords
  installed
  installed_version
  update_available
  aur_confirmed
  foreign_unknown
```

Для GUI вводится небольшой presentation adapter, который умеет отрисовать:

- официальный `Application`;
- `AurPackage`.

Не делать наследование Qt-моделей от core-моделей.

---

## 7. Предлагаемая структура файлов

Stage 7 должен по возможности жить в собственных каталогах.

Основной core-код AUR размещается в `src/app_store/aur/`, а специализированный GUI AUR — в `src/gui/app_store/aur/`.

```text
src/
  app_store/
    aur/
      __init__.py
      models.py
      errors.py
      rpc.py
      cache.py
      local_state.py
      versioning.py
      availability.py
      planner.py
      runner.py
      service.py
      diagnostics.py

  gui/
    app_store/
      aur/
        __init__.py
        widgets.py
        details.py
        dialogs.py
        workers.py
        integration.py

scripts/
  install-aur-support.sh          # только если понадобится инфраструктурная установка
  uninstall-aur-support.sh

tests/
  app_store_aur/
    test_rpc.py
    test_models.py
    test_local_state.py
    test_versioning.py
    test_planner.py
    test_runner.py
    test_service.py
    test_gui_integration.py
    test_security.py
    test_regressions.py

docs/
  app_store/
    stage7/
      STAGE_7_0_AUR_PLAN.md
      STAGE_7_1_AUR_FOUNDATION.md
      STAGE_7_2_AUR_SEARCH.md
      STAGE_7_3_AUR_INSTALL.md
      STAGE_7_4_AUR_REMOVE.md
      STAGE_7_5_AUR_UPDATES.md
      STAGE_7_6_AUR_RELEASE.md
```

### Файлы существующего магазина, которые разрешено менять

Только как интеграционные точки и в минимальном объёме:

```text
src/gui/app_store/page.py
src/gui/app_store/details.py
src/app_store/integration.py
src/app_store/transactions.py       # только если нужен общий coordinator
src/gui/main_window.py              # только если понадобится новый route/state
```

Изменения в них должны быть маленькими и покрыты regression-тестами.

### Файлы, которые Stage 7 не должен перерабатывать без крайней необходимости

```text
src/app_store/catalog.py
src/app_store/appstream.py
src/app_store/package_state.py
src/app_store/package_actions.py
src/core/app_store_executor.py
src/privileged/manage_applications.sh
packaging/polkit/org.archmanager.manage-applications.policy
```

---

## 8. AUR UI в магазине

### 8.1. Основной принцип

Пользователь должен сразу понимать источник пакета.

У карточек/результатов AUR:

- заметный badge `AUR`;
- отдельный визуальный стиль badge, но без агрессивного предупреждающего дизайна;
- при открытии подробностей — строка «Источник: AUR»;
- maintainer;
- версия;
- out-of-date status;
- ссылка на страницу AUR;
- пометка о community-maintained package.

### 8.2. Поиск

Не загружать весь AUR каталог при запуске магазина.

Поиск AUR — **on demand**:

- пользователь вводит минимум 2–3 символа;
- debounce ~300–500 ms;
- официальный локальный поиск работает мгновенно;
- AUR search идёт асинхронно;
- результаты объединяются на presentation-уровне;
- можно включить filter `Официальные / AUR / Все`;
- network failure не очищает официальные результаты.

### 8.3. Категории

В AUR RPC нет полноценного AppStream category/icon/screenshot набора.

Поэтому Stage 7 v1 **не пытается искусственно притворить каждый AUR package полноценной AppStream-карточкой**.

AUR результаты могут иметь более компактную карточку:

- generic AUR icon;
- name;
- description;
- version;
- maintainer;
- AUR badge;
- installed/update state.

Если после установки пакет предоставляет `.desktop`/AppStream metadata, локально установленное приложение можно обогатить этими данными отдельным подэтапом.

### 8.4. Рейтинги

`Votes` и `Popularity` можно показывать как AUR metadata, но нельзя выдавать их за «рейтинг качества» Arch Manager.

Не создавать звёзды 1–5 и не вычислять собственный рейтинг пакета.

---

## 9. Операции AUR и команды backend

Все package names валидируются так же строго, как в официальном магазине.

Команды передаются subprocess как argv-массивы. Никакого `shell=True`.

### Установка

Концептуально:

```text
yay --aur -S <package>
```

или эквивалентный синтаксис текущего yay.

### Обновление одного AUR приложения

Для конкретного установленного пакета:

```text
yay --aur -S <package>
```

с предварительной проверкой, что AUR version действительно новее.

### Обновление всех AUR пакетов

Отдельная AUR-only операция:

```text
yay -Sua
```

Она не должна подменять системное `pacman -Syu` / существующую страницу обновлений Arch Manager.

### Удаление

Концептуально:

```text
yay -Rns <package>
```

Для Stage 7 v1 удаление тоже можно проводить в terminal runner, чтобы поведение было единообразным и пользователь видел план pacman.

### Запрещённые defaults

Не использовать по умолчанию:

```text
--noconfirm
--answerclean All
--answerdiff None
--skipchecksums
--skippgpcheck
```

Пользователь не должен быть лишён проверки AUR build recipe ради «красивой тихой установки».

---

## 10. Terminal runner

Нужен отдельный runner, принадлежащий Stage 7.

Задачи runner-а:

- принять только whitelist action (`install`, `update`, `update-all`, `remove`);
- принять только валидированное package name;
- не принимать произвольную команду;
- не использовать shell evaluation;
- проверить, что процесс не root;
- проверить `yay`;
- поставить локальный AUR transaction lock;
- запустить yay в настоящем TTY;
- вернуть понятный exit code;
- сохранить короткий operation result для GUI;
- не хранить пароль sudo;
- не перехватывать пароль пользователя;
- не писать секреты в log.

### Терминал

На KDE приоритетный frontend — Konsole.

Runner должен быть отделён от конкретного terminal emulator. Например:

```text
TerminalLauncher -> Konsole implementation
```

Если Konsole отсутствует, показать понятную ошибку или использовать заранее определённый fallback после отдельного теста.

---

## 11. Общая координация package transactions

AUR и официальный магазин не должны одновременно менять пакетную базу.

### Уровень 1 — существующий pacman lock

`/var/lib/pacman/db.lck` остаётся последней защитой от параллельной записи.

### Уровень 2 — Arch Manager coordinator

Ввести небольшой общий coordinator, не перенося AUR код в `transactions.py`.

Например:

```text
src/app_store/package_coordinator.py
```

Он знает только о факте package operation:

- idle;
- official transaction active;
- AUR transaction active.

Он не знает, как работает pacman или yay.

AUR subsystem и официальный `PackageActionService` используют coordinator через небольшой adapter.

Если для terminal child процесса понадобится межпроцессная блокировка, она реализуется отдельным безопасным lock protocol и тестируется на stale-lock/crash случаи.

---

## 12. Правило полного системного обновления

Перед AUR install/update Arch Manager должен проверять, что система не находится в состоянии, где сначала требуется официальный full upgrade.

Причина: AUR package собирается против текущих системных библиотек, а Arch не поддерживает partial upgrades.

UX:

> «Перед установкой/обновлением AUR-пакетов сначала обновите систему.»

Кнопка:

> `Перейти к системным обновлениям`

Stage 7 не запускает полный system upgrade скрыто.

---

## 13. Состояния и ошибки

AUR subsystem имеет собственную иерархию ошибок, например:

```text
AurError
  AurUnavailable
  AurNetworkError
  AurRpcError
  AurInvalidResponse
  AurHelperUnavailable
  AurBuildToolsUnavailable
  AurPackageNotFound
  AurPackageOutOfDate
  AurTransactionBusy
  AurTransactionCancelled
  AurTransactionFailed
  AurSystemUpdateRequired
```

Ошибки не должны протекать в GUI как traceback.

GUI показывает короткое сообщение + разворачиваемые технические детали.

---

## 14. Cache и offline behavior

AUR cache отдельный от official catalog cache.

Пример:

```text
~/.cache/arch-manager/app-store/aur/
  search/
  info/
  state.json
```

Правила:

- короткий TTL поиска;
- info cache может жить дольше;
- atomic writes;
- размер ограничен;
- повреждённый cache удаляется/восстанавливается;
- offline: официальный магазин полностью работает;
- offline AUR search показывает понятное сообщение;
- установленное AUR состояние можно показать по локальным данным + последнему cache, с пометкой о невозможности проверить актуальность.

Не использовать AUR cache как источник истины для package transaction.

---

## 15. Логирование

Использовать существующий activity log Arch Manager, но action namespace сделать явным:

```text
app-store/aur-search
app-store/aur-install
app-store/aur-update
app-store/aur-remove
```

Логировать:

- action;
- package name;
- start/end;
- exit status;
- user cancellation;
- безопасный краткий error detail.

Не логировать:

- sudo password;
- environment целиком;
- arbitrary terminal contents;
- токены/credentials.

---

# 16. Этапность реального внедрения

## Stage 7.0 — архитектура и план

**Этот документ.**

Результат:

- зафиксированы границы AUR subsystem;
- выбран `yay`;
- выбран aurweb RPC для read-only metadata;
- выбран terminal workflow для mutating actions;
- запрещено смешивать AUR с текущим privileged helper;
- определена файловая структура;
- определены подэтапы 7.1–7.6.

Runtime-поведение Arch Manager на Stage 7.0 не меняется.

---

## Stage 7.1 — AUR Foundation / read-only core

### Цель

Создать полностью изолированный AUR core без кнопок установки.

### Реализовать

- `src/app_store/aur/` package;
- `AurPackage` model;
- aurweb RPC v5 client;
- network timeout / validation / error mapping;
- отдельный AUR cache;
- installed foreign package reader;
- подтверждение foreign package через AUR info;
- version comparison;
- `yay` / `git` / `makepkg` / `base-devel` capability diagnostics;
- async service API;
- unit tests без реальной установки пакетов.

### Acceptance criteria

- официальный магазин работает без изменений;
- при отсутствии сети AUR core падает контролируемо;
- `pacman -Qm` local-only package не объявляется AUR без подтверждения;
- ни один Stage 7.1 test не требует root;
- ни одна mutating command не выполняется.

---

## Stage 7.2 — поиск AUR и карточки

**Статус на 01.10.2026: реализовано.** См. `STAGE_7_2_AUR_GUI.md`.

### Цель

Пользователь может находить AUR приложения в существующем магазине.

### Реализовать

- filter `Все / Официальные / AUR`;
- debounce AUR search;
- async search;
- AUR result cards;
- AUR badge;
- отдельные AUR details;
- maintainer/version/out-of-date/votes/popularity/upstream/AUR page;
- network/error/loading states;
- объединение official+AUR results только в presentation layer.

### Не реализовывать пока

- install;
- remove;
- update;
- comments;
- voting;
- full AUR catalog preload.

### Acceptance criteria

- AUR сеть не блокирует GUI;
- официальный поиск не ждёт AUR;
- ошибка AUR API не ломает страницу;
- одинаковые package names из official и AUR имеют явно различимый source.

---

## Stage 7.3 — установка AUR

### Цель

Надёжно установить выбранный AUR package.

### Реализовать

- AUR action planner;
- проверка system-up-to-date gate;
- capability gate;
- transaction coordinator;
- terminal launcher;
- whitelist AUR terminal runner;
- interactive `yay --aur -S <package>`;
- состояние `Установка открыта в терминале`;
- refresh local state после завершения terminal process;
- activity log;
- понятные cancel/failure states.

### Ключевой safety criterion

`yay`/`makepkg` никогда не запускается от root самим Arch Manager.

---

## Stage 7.4 — удаление AUR

**Статус на 01.10.2026: реализовано.** См. `STAGE_7_4_AUR_REMOVE.md`.

### Цель

Удалять установленный AUR package с тем же уровнем прозрачности.

### Реализовать

- remove plan;
- dependency/removal review в терминале;
- `yay -Rns <package>`;
- refresh store/update state;
- защита от параллельных package transactions;
- activity log;
- regression официального remove.

### Acceptance criteria

- удалить можно только реально установленный пакет;
- source/state перечитывается после операции;
- отмена в terminal не отображается как successful removal.

---

## Stage 7.5 — обновления AUR

**Статус: реализовано.** Подробности: `STAGE_7_5_AUR_UPDATES.md`.

### Цель

Интегрировать AUR updates, не ломая правило полного обновления Arch.

### Реализовать

- read-only определение AUR updates;
- отдельная группа `Обновления AUR` в магазине;
- update одного AUR package;
- `Обновить все AUR` через `yay -Sua`;
- запрет запуска AUR update при требуемом официальном system update;
- после официального system update — повторная проверка AUR updates;
- optional devel package detection как отдельная настройка/подэтап.

### Важно

Не смешивать кнопку `Обновить все AUR` с существующей кнопкой полного system upgrade.

---

## Stage 7.6 — hardening, UX, release

### Цель

Довести AUR до повседневного использования.

### Реализовать

- полный regression official store Stage 1–6;
- stale lock recovery;
- crash/restart behavior;
- offline cache limits;
- cancellation;
- timeout handling;
- terminal missing behavior;
- broken yay behavior;
- missing base-devel behavior;
- orphan/out-of-date package states;
- AUR package removed from AUR after local install;
- local foreign package not in AUR;
- package moves AUR ↔ official repository;
- package name collision official/AUR;
- source transition after refresh;
- final documentation and diagnostics.

### Release criterion

AUR subsystem можно отключить/сломать/оставить без сети, и официальный магазин Stage 1–6 всё равно должен работать полностью.

---

# 17. Test strategy

## Unit tests

Все внешние процессы и сеть мокируются.

Проверить:

- RPC URL construction;
- JSON validation;
- missing fields;
- API error response;
- timeout;
- cache hit/miss/corruption;
- package-name validation;
- `pacman -Qm` mapping;
- AUR confirmation;
- local foreign fallback;
- version comparison;
- command argv construction;
- forbidden shell usage;
- forbidden `--noconfirm` defaults;
- runner refuses root;
- runner refuses unknown action;
- runner refuses malformed package name.

## Integration tests

Без установки реальных AUR packages:

- fake yay executable;
- fake terminal launcher;
- fake RPC server/client responses;
- transaction coordinator;
- cancellation/exit codes;
- state refresh.

## GUI tests

- source badges;
- filters;
- async loading;
- official results survive AUR failure;
- install/update/remove button state;
- system-update-required route;
- terminal started exactly once;
- no duplicate operation after double click.

## Regression

После каждого подэтапа:

```bash
pytest -q
python -m compileall -q src tests
```

Плюс targeted AUR tests.

---

# 18. Security checklist

Перед завершением каждого Stage 7.x проверить:

- [ ] AUR subprocess не root.
- [ ] `makepkg` не root.
- [ ] Нет `shell=True`.
- [ ] Имя пакета валидируется.
- [ ] Runner принимает whitelist action.
- [ ] Нет произвольных команд из GUI.
- [ ] Нет `--noconfirm` в default install/update.
- [ ] Нет `--skipchecksums` / `--skippgpcheck`.
- [ ] Нет скрытого `yay -Syu`.
- [ ] Official helper остался official-only.
- [ ] AUR network failure не ломает official catalog.
- [ ] AUR cache не является источником истины для установки.
- [ ] Package DB mutation сериализована.
- [ ] GUI не хранит sudo password.
- [ ] Logs не содержат credentials.
- [ ] Система должна быть полностью обновлена перед AUR install/update.

---

# 19. UX checklist

- [ ] AUR всегда визуально отличим от official repo.
- [ ] Пользователь понимает, что AUR community-maintained.
- [ ] Установка не выглядит «зависшей», пока открыт терминал.
- [ ] После закрытия терминала карточка автоматически обновляется.
- [ ] Out-of-date package явно помечен.
- [ ] Orphan package явно помечен.
- [ ] При отсутствии yay есть понятная инструкция.
- [ ] При отсутствии сети official store остаётся нормальным.
- [ ] Ошибка сборки показывает краткий итог и предлагает открыть/скопировать технические детали.
- [ ] Кнопка AUR update не вводит пользователя в заблуждение относительно полного system upgrade.

---

# 20. Что сознательно не входит в Stage 7 v1

Чтобы не раздувать первый AUR цикл:

- AUR comments внутри Arch Manager;
- голосование за AUR package;
- login в AUR;
- submission/editing PKGBUILD;
- собственный AUR dependency solver;
- собственный build sandbox/container;
- автоматическое доверие PGP keys;
- автоматическое исправление broken PKGBUILD;
- полный offline mirror AUR;
- ratings/stars Arch Manager;
- silent unattended AUR upgrades;
- поддержка сразу нескольких AUR helpers (`paru`, `aura` и др.).

После стабилизации `yay` backend можно добавить абстракцию второго helper-а, не меняя GUI contract.

---

# 21. Порядок следующих чатов

Рекомендуемый рабочий порядок:

1. **Stage 7.1** — прислать актуальный полный архив после всех изменений Stage 6/7.0; реализовать только read-only AUR core.
2. **Stage 7.2** — поиск и GUI, без установки.
3. **Stage 7.3** — установка через terminal runner.
4. **Stage 7.4** — удаление.
5. **Stage 7.5** — AUR updates.
6. **Stage 7.6** — hardening + полный regression + финальная полировка.

Для каждого этапа сохраняется текущий workflow Arch Manager:

- один применяемый Bash script;
- backup перед изменениями;
- логически законченный шаг;
- pytest + syntax checks;
- отдельные команды запуска/проверки;
- не смешивать несколько больших подэтапов в один патч.

---

# 22. Критерий успеха всего Stage 7

Stage 7 считается завершённым, когда пользователь может в разделе «Приложения»:

1. найти приложение из официального репозитория или AUR;
2. однозначно увидеть источник;
3. открыть AUR metadata;
4. установить AUR application через прозрачный interactive yay workflow;
5. удалить его;
6. увидеть доступное AUR обновление;
7. обновить один или все AUR packages;
8. после операции увидеть актуальное состояние без перезапуска Arch Manager;
9. при любой ошибке AUR продолжить полноценно пользоваться официальным магазином.

Главный технический критерий:

> **AUR — подключаемый изолированный subsystem, а не переработка уже стабильного official App Store.**

---

## 23. Технические источники, на которых основан план

- ArchWiki — AUR helpers: `https://wiki.archlinux.org/title/AUR_helpers`
- ArchWiki — makepkg: `https://wiki.archlinux.org/title/Makepkg`
- ArchWiki — aurweb RPC interface: `https://wiki.archlinux.org/title/Aurweb_RPC_interface`
- yay manual / upstream: `https://github.com/Jguer/yay/blob/next/doc/yay.8`
- yay upstream README: `https://github.com/Jguer/yay`

Перед реализацией каждого mutating подэтапа сверять команды с установленной версией `yay --help` / `man yay`, а не полагаться на строку команды из старого документа.
