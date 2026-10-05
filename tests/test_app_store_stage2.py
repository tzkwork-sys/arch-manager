from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import os
from pathlib import Path

import pytest

from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application
from src.app_store.presentation import ApplicationIndex, CatalogQuery


ROOT = Path(__file__).resolve().parents[1]
HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app(
    index: int,
    *,
    name: str | None = None,
    categories: tuple[str, ...] = ("office",),
    installed: bool = False,
    summary: str = "Удобное приложение",
    keywords: tuple[str, ...] = (),
) -> Application:
    return Application(
        app_id=f"org.example.App{index}",
        package_name=f"example-app-{index}",
        name=name or f"Приложение {index:04d}",
        summary=summary,
        categories=categories,
        keywords=keywords,
        installed=installed,
        repository="extra",
        available_version="1.0-1",
    )


class FakeService:
    def __init__(self, result: CatalogLoadResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[bool] = []

    def load_catalog_async(self, *, use_cache: bool = True):
        self.calls.append(use_cache)
        future: Future[CatalogLoadResult] = Future()
        if self.error is not None:
            future.set_exception(self.error)
        else:
            assert self.result is not None
            future.set_result(self.result)
        return future


def _result(applications, *, cache_hit: bool = False, errors: tuple[str, ...] = ()) -> CatalogLoadResult:
    items = tuple(applications)
    return CatalogLoadResult(
        applications=items,
        stats=CatalogStats(
            metadata_files=1,
            applications_built=len(items),
            installed_applications=sum(1 for item in items if item.installed),
            read_errors=errors,
        ),
        cache_hit=cache_hit,
    )


def _qt_types():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage, INITIAL_BATCH_SIZE, SEARCH_DEBOUNCE_MS

    app = QApplication.instance() or QApplication([])
    return app, AppStorePage, INITIAL_BATCH_SIZE, SEARCH_DEBOUNCE_MS


def test_stage2_search_index_covers_required_fields_and_filters():
    editor = _app(
        1,
        name="Редактор Текст",
        categories=("office", "development"),
        installed=True,
        summary="Работа с документами",
        keywords=("markdown", "заметки"),
    )
    browser = _app(2, name="Web Surfer", categories=("internet",), summary="Браузер")
    index = ApplicationIndex([editor, browser])

    assert index.filter(CatalogQuery(text="текст")) == (editor,)
    assert index.filter(CatalogQuery(text="example-app-1")) == (editor,)
    assert index.filter(CatalogQuery(text="org.example.app1")) == (editor,)
    assert index.filter(CatalogQuery(text="документ")) == (editor,)
    assert index.filter(CatalogQuery(text="markdown")) == (editor,)
    assert index.filter(CatalogQuery(category="internet")) == (browser,)
    assert index.filter(CatalogQuery(installed_only=True)) == (editor,)
    assert index.filter(CatalogQuery(text="редактор markdown", installed_only=True)) == (editor,)


def test_stage2_category_index_reports_only_present_categories():
    index = ApplicationIndex([
        _app(1, categories=("office", "development")),
        _app(2, categories=("internet",)),
    ])
    assert index.available_categories() == ("development", "internet", "office")


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_app_store_page_loads_from_cache_and_reopening_does_not_reload():
    app, AppStorePage, _, _ = _qt_types()
    service = FakeService(_result([_app(1), _app(2, installed=True)], cache_hit=True))
    page = AppStorePage(service=service)
    page.ensure_loaded()
    app.processEvents()

    assert page.filtered_count == 2
    assert page.body_stack.currentIndex() == page.STATE_CATALOG
    assert page.cache_label.text() == "Из кэша"
    assert service.calls == [True]

    page.ensure_loaded()
    page.ensure_loaded()
    app.processEvents()
    assert service.calls == [True]
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_app_store_page_search_category_installed_and_empty_state():
    app, AppStorePage, _, SEARCH_DEBOUNCE_MS = _qt_types()
    applications = [
        _app(1, name="Writer", categories=("office",), installed=True),
        _app(2, name="Browser", categories=("internet",)),
        _app(3, name="IDE", categories=("development",), installed=True),
    ]
    page = AppStorePage(service=FakeService(_result(applications)))
    page.ensure_loaded()
    app.processEvents()

    assert page._search_timer.interval() == SEARCH_DEBOUNCE_MS

    page.search_edit.setText("Browser")
    page._search_timer.stop()
    page._apply_filters()
    assert page.filtered_count == 1
    assert page._filtered[0].name == "Browser"

    page.search_edit.clear()
    assert page.filtered_count == 3  # clear is intentionally immediate

    office_index = page.category_combo.findData("office")
    assert office_index >= 0
    page.category_combo.setCurrentIndex(office_index)
    assert page.filtered_count == 1
    assert page._filtered[0].name == "Writer"

    page.category_combo.setCurrentIndex(0)
    page.installed_button.setChecked(True)
    assert {item.name for item in page._filtered} == {"Writer", "IDE"}

    page.search_edit.setText("definitely-not-present")
    page._search_timer.stop()
    page._apply_filters()
    assert page.filtered_count == 0
    assert page.body_stack.currentIndex() == page.STATE_EMPTY
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_app_store_page_handles_metadata_error_and_retry():
    app, AppStorePage, _, _ = _qt_types()
    service = FakeService(_result([], errors=("broken metadata",)))
    page = AppStorePage(service=service)
    page.ensure_loaded()
    app.processEvents()

    assert page.body_stack.currentIndex() == page.STATE_ERROR
    assert "AppStream" in page.error_label.text()
    assert page.search_edit.isEnabled() is False
    assert page.reload_button.isEnabled() is True
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_app_store_grid_is_adaptive_and_lazy_for_large_catalog():
    app, AppStorePage, INITIAL_BATCH_SIZE, _ = _qt_types()
    applications = [_app(index, categories=(("office",) if index % 2 else ("internet",))) for index in range(1200)]
    page = AppStorePage(service=FakeService(_result(applications)))
    page.resize(1180, 720)
    page.show()
    page.ensure_loaded()
    app.processEvents()
    page._relayout_cards()

    assert page.filtered_count == 1200
    assert page.visible_card_count >= INITIAL_BATCH_SIZE
    assert page.visible_card_count < 1200
    wide_columns = page._columns
    assert wide_columns >= 4
    assert page.scroll.horizontalScrollBarPolicy().name == "ScrollBarAlwaysOff"

    page.resize(620, 720)
    app.processEvents()
    page._relayout_cards()
    assert page._columns < wide_columns
    assert page._columns >= 1

    # Visual regression guards: preserve the compact five-column Stage 2
    # geometry while keeping the improved text handling inside each card.
    from src.gui.app_store.widgets import CARD_HEIGHT, CARD_MIN_WIDTH

    assert CARD_MIN_WIDTH == 238
    assert CARD_HEIGHT == 154
    first_card = page._cards[0]
    assert first_card.title_label.full_text == applications[0].name
    assert first_card.summary_label.full_text == applications[0].summary
    assert first_card.title_label.toolTip() == applications[0].name

    page.close()
    page.deleteLater()


def test_app_store_navigation_and_isolation_are_wired_without_privileged_actions():
    main = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    widgets = (ROOT / "src/gui/app_store/widgets.py").read_text(encoding="utf-8")

    assert '("Приложения", "system-software-install-symbolic")' in main
    assert "AppStorePage()" in main
    assert "NAV_LAYOUT_VERSION = 4" in main
    assert "src.core." not in page
    assert "install_package" not in page
    assert "remove_package" not in page
    assert "subprocess" not in page
    assert "package_name" not in widgets  # technical package details stay off catalog cards
