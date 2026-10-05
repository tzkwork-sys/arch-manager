# Arch Manager — App Store Stage 7.2: AUR Search & GUI

**Дата:** 1 октября 2026  
**Статус:** реализовано; read-only GUI, без AUR package transactions.

## Что добавлено

Stage 7.2 подключает уже готовый read-only `AurService` к существующей странице «Приложения», не смешивая модели и backend официального магазина с AUR.

Реализованы:

- фильтр источника `Все / Официальные / AUR`;
- отдельный debounce для AUR-поиска (`360 ms`);
- AUR поиск только через `AurService.search_async()`;
- официальный поиск и отрисовка не ждут сеть AUR;
- generation guard: поздний ответ старого AUR-запроса игнорируется;
- отдельные AUR result cards с явным badge `AUR`, версией и maintainer;
- отдельный read-only `AurPackageDetailsDialog`;
- сведения AUR: строка `Источник: AUR`, community-maintained note, версия, maintainer, votes, popularity, package base, даты, out-of-date, лицензии и package relations;
- ссылки на upstream и страницу пакета в AUR;
- независимые loading/error states для AUR;
- ошибка AUR API не переводит всю страницу магазина в error state;
- official + AUR объединяются только в `src/gui/app_store/aur/integration.py` на presentation boundary;
- одинаковые имена не дедуплицируются между источниками: AUR вариант остаётся явно помеченным;
- AUR не preload-ится целиком: сетевой запрос начинается только для поисковой строки длиной минимум 2 символа;
- категория AppStream применяется только к официальному каталогу; при выборе `AUR` категория сбрасывается в `Все категории` и отключается;
- режимы `Установленные` и `Обновления` остаются официальными до Stage 7.4/7.5.

## Новые GUI-файлы

```text
src/gui/app_store/aur/
  __init__.py
  integration.py
  widgets.py
  details.py
```

## Изменённые интеграционные точки

Только `src/gui/app_store/page.py` получает routing-код:

- source filter;
- отдельный AUR timer/state/signals;
- запуск `AurService.search_async()`;
- merge official/AUR результата для текущего представления;
- выбор `ApplicationCard` или `AurPackageCard`;
- выбор official или AUR details dialog.

Существующие:

- `src/gui/app_store/widgets.py`;
- `src/gui/app_store/details.py`;
- `src/app_store/catalog.py`;
- `src/app_store/transactions.py`;
- `src/privileged/manage_applications.sh`

не превращались в AUR-aware backend и сохраняют прежний контракт.

## Что намеренно не реализовано

Stage 7.2 **не выполняет**:

- `yay` install;
- `yay` remove;
- AUR update;
- `makepkg`;
- terminal runner;
- package mutation через root/helper;
- comments/voting;
- полный preload каталога AUR.

Следующий mutating этап — **Stage 7.3: установка AUR через интерактивный terminal runner**.

## Acceptance criteria

- AUR сеть не блокирует GUI: выполнено архитектурно через `search_async()`;
- официальный поиск не ждёт AUR: official filter отрисовывается отдельным таймером раньше AUR и не зависит от future AUR;
- ошибка AUR API не ломает страницу: AUR имеет собственный error state/status;
- одинаковые package names различимы по source: AUR card всегда содержит badge `AUR`;
- AUR package transactions отсутствуют: подтверждено source-contract тестами.

## Исправление рендеринга AUR-карточек (01.10.2026)

После первого реального GUI-прогона обнаружена ошибка на границе отрисовки: RPC-поиск AUR успешно возвращал результаты и счётчик обновлялся, но исключение во время построения/обработки AUR-карточек могло прервать Qt-slot и оставить сетку пустой.

Исправлено:

- AUR-карточка больше не использует приватный `_CompactTextLabel` официального AppStream GUI;
- удалён сложный font/resize fitting path для удалённых AUR metadata;
- строки AUR перед отображением нормализуются, ограничиваются по размеру и принудительно отображаются как plain text;
- lazy renderer теперь имеет отдельный cursor и не зацикливается на одной проблемной записи;
- ошибка одной карточки журналируется и не вызывает глобальное окно «непредвиденная ошибка» для всей страницы;
- официальный `ApplicationCard` не изменён.

Отсутствие карточек сразу после выбора фильтра `AUR` остаётся штатным поведением Stage 7.2: полный каталог AUR намеренно не загружается. Карточки появляются после поискового запроса длиной не менее двух символов.

## Финальная доводка локального состояния AUR (01.10.2026)

После реальной проверки с установленным `google-chrome` Stage 7.2 дополнен read-only локальным состоянием без перехода к пакетным операциям:

- результаты AUR-поиска сопоставляются с `pacman -Qm` по точному имени уже после подтверждения пакета ответом aurweb;
- установленный AUR-пакет получает `installed=True`, локальную версию и best-effort признак доступного обновления через `vercmp`;
- локальный probe не является обязательным для поиска: ошибка `pacman`/`vercmp` не ломает успешный AUR RPC result;
- AUR search cache по-прежнему хранит только удалённые metadata, локальное состояние перечитывается после cache hit;
- на карточке показывается зелёное `✓ Установлено` либо янтарное `↑ Обновление`, в details добавлены состояние и установленная версия;
- точное совпадение имени пакета поднимается на первое место AUR-выдачи (`google-chrome` выше соседних `chromedriver`/beta/dev результатов), остальной порядок aurweb сохраняется;
- режимы `Установленные` и `Обновления` остаются официальными в Stage 7.2. При входе в них source selector теперь явно показывает `Официальные`, а прежний `Все/AUR` выбор восстанавливается после возврата в обычный каталог. Это устраняет ложное впечатление, что `AUR + Установленные` уже реализован.

Полноценный список установленных AUR-пакетов и AUR package transactions по-прежнему не входят в Stage 7.2.
