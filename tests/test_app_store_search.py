from __future__ import annotations

from src.app_store.models import Application
from src.app_store.presentation import ApplicationIndex, CatalogQuery
from src.app_store.search import (
    aur_search_queries,
    english_search_phrase,
    normalize_search_text,
    search_candidate_rank,
    search_matches,
)


def _yandex_app() -> Application:
    return Application(
        app_id="ru.yandex.Disk",
        package_name="yandex-disk",
        package_names=("yandex-disk",),
        name="Yandex Disk",
        summary="Cloud storage client",
    )


def test_search_normalization_treats_spaces_hyphens_underscores_and_dots_as_separators():
    expected = "yandex disk"
    assert normalize_search_text("yandex disk") == expected
    assert normalize_search_text("yandex-disk") == expected
    assert normalize_search_text("yandex_disk") == expected
    assert normalize_search_text("Yandex.Disk") == expected


def test_russian_software_query_gets_local_english_form_without_network_translation():
    assert english_search_phrase("Яндекс диск") == "yandex disk"
    assert english_search_phrase("Гугл Хром") == "google chrome"
    assert english_search_phrase("Визуал Студио Код") == "visual studio code"
    assert english_search_phrase("вебкит") == "webkit"


def test_official_catalog_search_accepts_russian_and_separator_variants():
    app = _yandex_app()
    index = ApplicationIndex((app,))

    for query in ("yandex disk", "yandex-disk", "yandex_disk", "Яндекс диск"):
        assert index.filter(CatalogQuery(text=query)) == (app,)


def test_search_rank_promotes_package_name_for_russian_or_space_query():
    for query in ("yandex disk", "Яндекс диск"):
        assert search_candidate_rank(("yandex-disk",), query) == 0
        assert search_candidate_rank(("ydisk_commander",), query) > 0


def test_local_match_uses_aliases_but_keeps_original_text_searchable():
    assert search_matches(("google-chrome", "Web browser"), "Гугл Хром")
    assert search_matches(("Русское описание редактора",), "редактора")


def test_aur_queries_try_package_separator_spellings_for_human_query():
    assert aur_search_queries("Яндекс диск") == (
        "yandex-disk",
        "yandex_disk",
        "yandex disk",
        "yandexdisk",
    )
    assert aur_search_queries("yandex disk") == aur_search_queries("Яндекс диск")
