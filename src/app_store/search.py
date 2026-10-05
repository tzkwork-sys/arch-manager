from __future__ import annotations

import re
from collections.abc import Iterable


_SPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[\s\-_.:/\\]+")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

# Local, deliberately small vocabulary for application/package search. It is
# not a general-purpose translator: these are common software names and terms
# users naturally type in Russian while Arch/AUR metadata is mostly English.
_PHRASE_ALIASES = {
    "визуал студио код": "visual studio code",
    "вс код": "visual studio code",
    "яндекс диск": "yandex disk",
    "яндекс браузер": "yandex browser",
    "гугл хром": "google chrome",
    "гугл диск": "google drive",
    "гугл драйв": "google drive",
    "мозилла файрфокс": "mozilla firefox",
    "либр офис": "libreoffice",
    "либре офис": "libreoffice",
    "дабл командер": "double commander",
}

_WORD_ALIASES = {
    # Brands / well-known application names.
    "яндекс": "yandex",
    "гугл": "google",
    "хром": "chrome",
    "хромиум": "chromium",
    "мозилла": "mozilla",
    "файрфокс": "firefox",
    "телеграм": "telegram",
    "телеграмм": "telegram",
    "дискорд": "discord",
    "скайп": "skype",
    "зум": "zoom",
    "ватсап": "whatsapp",
    "вотсап": "whatsapp",
    "вайбер": "viber",
    "спотифай": "spotify",
    "стим": "steam",
    "влс": "vlc",
    "обс": "obs",
    "либреофис": "libreoffice",
    "опенофис": "openoffice",
    "блендер": "blender",
    "гимп": "gimp",
    "инкскейп": "inkscape",
    "кденлив": "kdenlive",
    "аудасити": "audacity",
    "тимс": "teams",
    "тимвьюер": "teamviewer",
    "анидеск": "anydesk",
    "битварден": "bitwarden",
    "кипас": "keepass",
    "пичарм": "pycharm",
    "файлзилла": "filezilla",
    "дропбокс": "dropbox",
    "ондрайв": "onedrive",
    "некстклауд": "nextcloud",
    "сигнал": "signal",
    "слак": "slack",
    "зотеро": "zotero",
    "калибр": "calibre",
    "вебкит": "webkit",
    # Common software/search nouns.
    "диск": "disk",
    "браузер": "browser",
    "почта": "mail",
    "календарь": "calendar",
    "облако": "cloud",
    "клиент": "client",
    "менеджер": "manager",
    "файл": "file",
    "файлы": "files",
    "редактор": "editor",
    "текст": "text",
    "текстовый": "text",
    "видео": "video",
    "аудио": "audio",
    "музыка": "music",
    "фото": "photo",
    "изображение": "image",
    "изображения": "images",
    "просмотр": "viewer",
    "проигрыватель": "player",
    "плеер": "player",
    "мессенджер": "messenger",
    "чат": "chat",
    "терминал": "terminal",
    "консоль": "console",
    "архиватор": "archiver",
    "архив": "archive",
    "торрент": "torrent",
    "загрузчик": "downloader",
    "загрузка": "download",
    "загрузки": "downloads",
    "пароль": "password",
    "пароли": "passwords",
    "заметки": "notes",
    "офис": "office",
    "таблицы": "spreadsheet",
    "презентации": "presentation",
    "рисование": "drawing",
    "скриншот": "screenshot",
    "запись": "recorder",
    "камера": "camera",
    "удаленный": "remote",
    "удалённый": "remote",
    "сеть": "network",
}

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def has_cyrillic(value: str) -> bool:
    return bool(_CYRILLIC_RE.search(value or ""))


def normalize_search_text(value: str) -> str:
    """Case-fold text and make common package-name separators equivalent."""

    text = str(value or "").casefold().replace("ё", "е")
    text = _SEPARATOR_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def compact_search_text(value: str) -> str:
    return "".join(ch for ch in normalize_search_text(value) if ch.isalnum())


def transliterate_russian(value: str) -> str:
    text = str(value or "").casefold()
    return "".join(ch.translate(_TRANSLIT) if "а" <= ch <= "я" or ch == "ё" else ch for ch in text)


def english_search_phrase(query: str) -> str:
    """Return a local English/package-oriented form of a user query.

    Known Russian software terms use curated aliases. Unknown Cyrillic words
    fall back to deterministic transliteration, so the feature keeps working
    without a network translator or external service.
    """

    normalized = normalize_search_text(query)
    if not normalized:
        return ""
    phrase_alias = _PHRASE_ALIASES.get(normalized)
    if phrase_alias:
        return phrase_alias

    translated: list[str] = []
    for token in normalized.split():
        alias = _WORD_ALIASES.get(token)
        if alias:
            translated.extend(alias.split())
        elif has_cyrillic(token):
            translated.append(transliterate_russian(token))
        else:
            translated.append(token)
    return " ".join(part for part in translated if part)


def search_query_variants(query: str) -> tuple[str, ...]:
    """Variants used by local catalog matching (OR between variants)."""

    normalized = normalize_search_text(query)
    if not normalized:
        return ()
    english = normalize_search_text(english_search_phrase(normalized))
    transliterated = normalize_search_text(transliterate_russian(normalized))
    # English/package form comes first because it is normally the most useful
    # form for Arch metadata; the original form still remains searchable.
    return _unique((english, normalized, transliterated))


def aur_search_queries(query: str, *, limit: int = 4) -> tuple[str, ...]:
    """Build a small set of literal aurweb queries for one user query.

    aurweb's name-desc search is literal enough that ``yandex disk`` does not
    reliably find ``yandex-disk``. Query package-like separator spellings as
    well, then merge/deduplicate results in :class:`AurService`.
    """

    english = english_search_phrase(query)
    normalized = normalize_search_text(english)
    if len(normalized) < 2:
        return ()
    tokens = normalized.split()
    variants: list[str] = []
    if len(tokens) > 1:
        variants.extend(("-".join(tokens), "_".join(tokens), " ".join(tokens), "".join(tokens)))
    else:
        # Preserve an already package-shaped single term where possible.
        literal = _SPACE_RE.sub(" ", str(query or "").strip().casefold())
        if literal and not has_cyrillic(literal) and " " not in literal:
            variants.append(literal)
        variants.append(normalized)
    return _unique(variants)[: max(1, int(limit))]


def search_matches(values: Iterable[str], query: str) -> bool:
    variants = search_query_variants(query)
    if not variants:
        return True
    haystack = normalize_search_text(" ".join(str(value or "") for value in values))
    compact_haystack = "".join(ch for ch in haystack if ch.isalnum())
    for variant in variants:
        terms = tuple(part for part in variant.split() if part)
        if terms and all(term in haystack for term in terms):
            return True
        compact = "".join(ch for ch in variant if ch.isalnum())
        if len(compact) >= 3 and compact in compact_haystack:
            return True
    return False


def search_candidate_rank(candidates: Iterable[str], query: str) -> int:
    """Relevance rank for package/application names; lower is better."""

    variants = search_query_variants(query)
    if not variants:
        return 3
    normalized_candidates = tuple(
        normalize_search_text(candidate) for candidate in candidates if str(candidate or "").strip()
    )
    compact_candidates = tuple("".join(ch for ch in value if ch.isalnum()) for value in normalized_candidates)

    for variant in variants:
        if variant in normalized_candidates:
            return 0
    for variant in variants:
        compact = "".join(ch for ch in variant if ch.isalnum())
        if compact and compact in compact_candidates:
            return 1
    for variant in variants:
        terms = tuple(part for part in variant.split() if part)
        compact = "".join(ch for ch in variant if ch.isalnum())
        for candidate, candidate_compact in zip(normalized_candidates, compact_candidates):
            if terms and all(term in candidate for term in terms):
                return 2
            if len(compact) >= 3 and compact in candidate_compact:
                return 2
    return 3
