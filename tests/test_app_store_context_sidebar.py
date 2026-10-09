from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import os

import pytest

from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application

HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeService:
    def load_catalog_async(self, *, use_cache: bool = True):
        del use_cache
        future: Future[CatalogLoadResult] = Future()
        future.set_result(
            CatalogLoadResult(
                applications=(
                    Application(
                        app_id="org.example.Writer",
                        package_name="writer",
                        name="Writer",
                        summary="Text editor",
                        categories=("office",),
                        installed=True,
                        repository="extra",
                        available_version="1.0-1",
                    ),
                    Application(
                        app_id="org.example.Game",
                        package_name="game",
                        name="Game",
                        summary="Game",
                        categories=("games",),
                        installed=False,
                        repository="extra",
                        available_version="1.0-1",
                    ),
                ),
                stats=CatalogStats(
                    metadata_files=1,
                    applications_built=2,
                    installed_applications=1,
                    read_errors=(),
                ),
                cache_hit=True,
            )
        )
        return future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_app_store_context_sidebar_contract():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(service=FakeService())
    categories_seen: list[tuple[tuple[str, str], ...]] = []
    selections: list[tuple[str, object]] = []
    page.sidebar_categories_changed.connect(lambda value: categories_seen.append(tuple(value)))
    page.sidebar_selection_changed.connect(lambda entry, category: selections.append((entry, category)))

    page.ensure_loaded()
    app.processEvents()

    assert page.category_combo.isHidden()
    assert page.installed_button.isHidden()
    assert page.updates_button.isHidden()
    assert ("office", "Офис") in page.sidebar_categories()
    assert ("games", "Игры") in page.sidebar_categories()
    assert categories_seen

    page.select_sidebar_entry("category", "office")
    assert page.category_combo.currentData() == "office"
    assert page.filtered_count == 1
    assert page._filtered[0].name == "Writer"
    assert selections[-1] == ("category", "office")

    page.select_sidebar_entry("installed")
    assert page._active_view() == "installed"
    assert selections[-1] == ("installed", None)

    page.select_sidebar_entry("catalog")
    assert page._active_view() == "catalog"
    assert page.category_combo.currentData() is None
    assert selections[-1] == ("catalog", None)
    page.deleteLater()


def test_main_window_contains_contextual_application_sidebar_source_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/gui/main_window.py").read_text(encoding="utf-8")
    assert 'self.sidebar_stack.addWidget(self._build_app_store_sidebar())' in source
    assert 'QPushButton("←  Arch Manager")' in source
    assert 'back_button.setToolTip("Вернуться к обзору Arch Manager")' in source
    assert 'appStoreSidebarTitle' not in source
    assert '"Все приложения", "catalog"' in source
    assert '"Установленные", "installed"' in source
    assert '"Обновления", "updates"' in source
    assert 'header = QListWidgetItem("КАТЕГОРИИ")' in source


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed")
def test_sidebar_title_identifies_current_context_and_resets_on_return():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.gui.main_window import MainWindow

    view = SimpleNamespace(
        stack=Mock(), navigation=Mock(), sidebar_brand=Mock(), sidebar_stack=Mock(),
        pages=[object() for _ in range(7)], settings=Mock(),
        _sync_app_store_sidebar_selection_from_page=Mock(),
    )
    view.stack.count.return_value = 7
    view.navigation.currentRow.return_value = 5
    MainWindow.set_page(view, 5)
    view.sidebar_brand.setText.assert_called_with("Диспетчер приложений")
    view.sidebar_stack.setCurrentIndex.assert_called_with(1)

    view.navigation.currentRow.return_value = 0
    MainWindow.set_page(view, 0)
    view.sidebar_brand.setText.assert_called_with("Arch Manager")
    view.sidebar_stack.setCurrentIndex.assert_called_with(0)


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed")
def test_catalog_starts_with_search_filters_and_actions_without_duplicate_title():
    from PySide6.QtWidgets import QApplication, QLabel
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(service=FakeService())
    assert page.findChild(QLabel, "pageTitle") is None
    top_row = page.layout().itemAt(0).layout()
    assert top_row.itemAt(0).widget() is page.search_edit
    assert top_row.itemAt(1).widget() is page.source_combo
    assert top_row.itemAt(2).widget() is page.sort_combo
    assert top_row.itemAt(3).layout() is page.header_actions
    assert page.header_actions.itemAt(0).widget() is page.reload_button
    assert page.layout().itemAt(1).widget() is page.checked_label
