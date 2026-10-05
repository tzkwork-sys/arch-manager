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
    assert '"Все приложения", "catalog"' in source
    assert '"Установленные", "installed"' in source
    assert '"Обновления", "updates"' in source
    assert 'header = QListWidgetItem("КАТЕГОРИИ")' in source
