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
    assert not hasattr(page, "updates_button")
    assert not hasattr(page, "aur_update_all_button")
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
    assert '"Обновления", "updates"' not in source
    assert '"Популярные", "popular"' not in source
    assert '"КАТЕГОРИИ"' not in source


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed")
@pytest.mark.parametrize("background, foreground", [("#141618", "#eeeeee"), ("#ffffff", "#202020")])
def test_sidebar_groups_system_before_catalog_and_preserves_selection(background, foreground):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QFrame, QListWidget, QMainWindow
    from src.gui.main_window import MainWindow

    class SidebarWindow(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self.app_store_navigation = QListWidget(self)

    app = QApplication.instance() or QApplication([])
    window = SidebarWindow()
    palette = window.app_store_navigation.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(background))
    palette.setColor(QPalette.ColorRole.Mid, QColor(background))
    palette.setColor(QPalette.ColorRole.Text, QColor(foreground))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#35ace3"))
    window.app_store_navigation.setPalette(palette)
    categories = (("internet", "Интернет"), ("office", "Офис"), ("graphics", "Графика"))
    window._set_app_store_categories(categories)
    nav = window.app_store_navigation
    assert [nav.item(row).text() for row in range(nav.count())] == [
        "МОЯ СИСТЕМА", "Установленные", "Системные пакеты", "",
        "КАТАЛОГ", "Все приложения", "Интернет", "Офис", "Графика",
    ]
    separator = nav.item(3)
    assert separator.flags() == Qt.ItemFlag.NoItemFlags
    line = nav.itemWidget(separator).findChild(QFrame, "appStoreSectionSeparator")
    assert line is not None
    assert line.height() == 1
    assert separator.sizeHint().height() == 28
    divider = QColor(line.styleSheet().split("background-color: ")[1].split(";")[0])
    assert divider.isValid()
    assert divider.name() == palette.color(QPalette.ColorRole.Highlight).lighter(125).name()
    assert divider.name() == nav.item(4).foreground().color().name()
    assert window._app_store_sidebar_state() == ("catalog", None)

    nav.setCurrentRow(7)
    window._set_app_store_categories(categories)
    assert window._app_store_sidebar_state() == ("category", "office")
    nav.setCurrentRow(1)
    window._set_app_store_categories(categories)
    assert window._app_store_sidebar_state() == ("installed", None)
    window.deleteLater()


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
    assert page.header_actions.count() == 1
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed")
def test_old_updates_entry_requests_shared_page_without_changing_catalog():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(service=FakeService())
    requests = []
    page.updates_requested.connect(lambda: requests.append(True))
    page.ensure_loaded()
    app.processEvents()
    page.select_sidebar_entry("category", "office")
    page.search_edit.setText("Writer")
    page.select_sidebar_entry("updates")

    assert requests == [True]
    assert page._active_view() == "catalog"
    assert page.sidebar_selection() == ("category", "office")
    assert page.search_edit.text() == "Writer"
    assert page.filtered_count == 1
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed")
def test_shared_updates_page_selects_all_aur_updates_by_default():
    from datetime import datetime
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from src.core.updates import UpdateDetails, UpdateItem, UpdateSourceDetails
    from src.gui.updates_page import UpdatesPage

    app = QApplication.instance() or QApplication([])
    page = UpdatesPage()
    page.apply_shared_details(UpdateDetails(
        official=UpdateSourceDetails((UpdateItem("linux", "1", "2", "official"),), True),
        aur=UpdateSourceDetails((
            UpdateItem("demo-aur", "1", "2", "aur"),
            UpdateItem("other-aur", "3", "4", "aur"),
        ), True),
        checked_at=datetime.now().astimezone(),
    ))

    assert page.table.rowCount() == 3
    assert page.install_button.isEnabled()
    assert page._selected_aur_packages() == ("demo-aur", "other-aur")
    for row in range(page.table.rowCount()):
        if page.table.item(row, 1).text() == "demo-aur":
            page.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
            break
    assert page._selected_aur_packages() == ("other-aur",)
    page.deleteLater()
