# Arch Manager — App Store Stage 7.1: AUR Foundation

**Дата:** 1 октября 2026  
**Статус:** реализовано в исходниках; read-only core, без GUI и пакетных операций.

## Что добавлено

Stage 7.1 создан отдельным пакетом `src/app_store/aur/` и не меняет стабильный контур официального магазина.

Реализованы:

- `AurPackage` и модели локального foreign/AUR состояния;
- aurweb RPC v5 client для `search` и batched `info`;
- timeout, ограничение размера ответа, JSON/schema validation и собственные AUR errors;
- отдельный атомарный AUR cache с TTL и ограничением размера;
- read-only `pacman -Qm` reader;
- обязательное подтверждение foreign package через AUR `info` до классификации как AUR;
- сравнение версий через системный `vercmp`;
- read-only capability probe для `yay`, `git`, `makepkg`, `vercmp`, `pacman` и полноты `base-devel`;
- toolkit-independent `AurService` с sync/async API;
- CLI diagnostics `python -m src.app_store.aur.diagnostics`;
- отдельные unit/regression/security tests в `tests/app_store_aur/`.

## Что намеренно не добавлено

Stage 7.1 не содержит:

- AUR GUI;
- кнопок install/remove/update;
- terminal runner;
- `yay` package transactions;
- `makepkg` запусков;
- изменений official `Application`, `AppCatalogService`, `PackageActionService` или privileged helper.

## Read-only команды Stage 7.1

Локальные probes используют только:

- `pacman -Qm`;
- `pacman -Qq base-devel`;
- `pacman -Qq`;
- `vercmp <installed> <available>`;
- `yay --version`.

Сетевой слой выполняет только HTTP GET к aurweb RPC v5.

## Критерий готовности к Stage 7.2

Stage 7.2 может подключать поиск/карточки к `AurService`, не меняя его базовый контракт и не добавляя AUR-логику в официальный каталог.
