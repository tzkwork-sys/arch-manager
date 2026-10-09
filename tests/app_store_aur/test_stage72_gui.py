from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import os
from pathlib import Path

import pytest

from src.app_store.aur.errors import AurNetworkError
from src.app_store.aur.models import AurInstalledSnapshot, AurPackage
from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application


ROOT = Path(__file__).resolve().parents[2]
HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_aur_integration():
    path = ROOT / "src/gui/app_store/aur/integration.py"
    spec = importlib.util.spec_from_file_location("arch_manager_stage72_integration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_merge_store_results():
    return _load_aur_integration().merge_store_results


def _official(name: str, package: str | None = None) -> Application:
    return Application(
        app_id=f"org.example.{name}",
        package_name=package or name.lower(),
        name=name,
        summary="Official application",
        categories=("office",),
        repository="extra",
        available_version="1.0-1",
    )


def _aur(name: str, *, description: str = "AUR application") -> AurPackage:
    return AurPackage(
        name=name,
        package_base=name,
        version="2.0-1",
        description=description,
        upstream_url="https://example.test/project",
        aur_url=f"https://aur.archlinux.org/packages/{name}",
        maintainer="maintainer",
        votes=42,
        popularity=3.5,
    )


def test_stage72_merge_happens_only_at_presentation_boundary_and_keeps_duplicates():
    merge_store_results = _load_merge_store_results()
    official = _official("Demo", package="demo")
    aur = _aur("Demo")

    mixed = merge_store_results((official,), (aur,), source="all")

    assert mixed == (official, aur)
    assert merge_store_results((official,), (aur,), source="official") == (official,)
    assert merge_store_results((official,), (aur,), source="aur") == (aur,)
    assert isinstance(mixed[0], Application)
    assert isinstance(mixed[1], AurPackage)


def test_stage72_exact_aur_package_name_is_promoted_without_reordering_other_hits():
    prioritize = _load_aur_integration().prioritize_exact_aur_match
    packages = (_aur("chromedriver"), _aur("google-chrome"), _aur("google-chrome-beta"))

    ranked = prioritize(packages, "google-chrome")

    assert [item.name for item in ranked] == [
        "google-chrome",
        "chromedriver",
        "google-chrome-beta",
    ]


def test_stage72_search_contract_remains_isolated_after_stage73():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    aur_widgets = (ROOT / "src/gui/app_store/aur/widgets.py").read_text(encoding="utf-8")
    aur_details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    official_widgets = (ROOT / "src/gui/app_store/widgets.py").read_text(encoding="utf-8")
    official_details = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")

    assert 'self.source_combo.addItem("Все", "all")' in page
    assert 'self.source_combo.addItem("Официальные", "official")' in page
    assert 'self.source_combo.addItem("AUR", "aur")' in page
    assert "AUR_SEARCH_DEBOUNCE_MS" in page
    assert "search_async" in page
    assert "merge_store_results" in page
    assert "AurPackageCard" in page
    assert "AurPackageDetailsDialog" in page
    assert "prioritize_exact_aur_match" in page
    assert 'source_selectable = view in {"catalog", "installed"}' in page
    assert 'self._set_source_value("official")' not in page
    assert "_render_cursor" in page
    assert "Unable to render application-store card" in page
    assert '_plain_label("AUR")' in aur_widgets
    assert "Qt.TextFormat.PlainText" in aur_widgets
    assert "_CompactTextLabel" not in aur_widgets
    assert "_safe_display_text" in aur_widgets
    assert "appStoreAurInstalledBadge" in aur_widgets
    assert "INSTALLED_GREEN" in aur_widgets
    assert "UPDATE_AMBER" in aur_widgets
    assert "Сопровождающий" in aur_details
    assert 'details.append(("Состояние", state))' in aur_details
    assert "Голоса" in aur_details
    assert "Популярность" in aur_details
    assert "Страница в AUR" in aur_details
    assert "Сайт проекта" in aur_details

    # Stage 7.3 adds install only behind the AUR details boundary.  Search cards
    # and the shared page still never execute package-manager commands directly.
    combined_search_surface = page + aur_widgets
    for forbidden in ("yay -S", "yay -R", "makepkg", "pkexec", "subprocess"):
        assert forbidden not in combined_search_surface

    # Stage 7.2/7.3 must not rewrite stable official cards/details into AUR-aware models.
    assert "AurPackage" not in official_widgets
    assert "AurPackage" not in official_details


class FakeCatalogService:
    def __init__(self, applications):
        self.applications = tuple(applications)

    def load_catalog_async(self, *, use_cache: bool = True):
        future = Future()
        future.set_result(
            CatalogLoadResult(
                applications=self.applications,
                stats=CatalogStats(
                    metadata_files=1,
                    applications_built=len(self.applications),
                    installed_applications=0,
                ),
                cache_hit=use_cache,
            )
        )
        return future


class FakeAurService:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []
        self.future: Future = Future()

    def search_async(self, query: str, *, use_cache: bool = True):
        self.calls.append((query, use_cache))
        return self.future

    def installed_async(self, *, use_cache: bool = True):
        future = Future()
        future.set_result(AurInstalledSnapshot())
        return future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage72_installed_aur_card_gets_green_highlight_border():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.aur.widgets import AurPackageCard

    app = QApplication.instance() or QApplication([])
    package = _aur("google-chrome").with_local_state(installed_version="1:1.0", update_available=False)
    card = AurPackageCard(package)
    assert "#55c878" in card.styleSheet()
    assert "QFrame#appStoreAurCard" in card.styleSheet()
    card.deleteLater()
    app.processEvents()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage72_official_results_render_without_waiting_for_aur():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage, AUR_SEARCH_DEBOUNCE_MS

    app = QApplication.instance() or QApplication([])
    aur = FakeAurService()
    page = AppStorePage(
        service=FakeCatalogService((_official("Demo", package="demo"),)),
        aur_service=aur,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("Demo")
    page._search_timer.stop()
    page._apply_filters()
    page._aur_search_timer.stop()
    page._start_aur_search()
    app.processEvents()

    assert page._aur_search_timer.interval() == AUR_SEARCH_DEBOUNCE_MS
    assert page.filtered_count == 1
    assert isinstance(page._filtered[0], Application)
    assert page.body_stack.currentIndex() == page.STATE_CATALOG
    assert "поиск" in page.aur_status_label.text().lower()
    assert aur.calls and aur.calls[-1][0] == "Demo"

    aur.future.set_result((_aur("Demo", description="Remote <b>plain</b> text & metadata"),))
    app.processEvents()
    app.processEvents()

    assert page.filtered_count == 2
    # The catalog now defaults to popularity sorting.  Once AUR results arrive,
    # their position relative to an official result may legitimately change,
    # so this Stage 7.2 async-rendering test must verify preservation of both
    # source models rather than the old alphabetical source order.
    assert sum(isinstance(item, Application) for item in page._filtered) == 1
    assert sum(isinstance(item, AurPackage) for item in page._filtered) == 1
    assert any(card.objectName() == "appStoreAurCard" for card in page._cards)
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage72_aur_failure_does_not_hide_official_results():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    aur = FakeAurService()
    page = AppStorePage(
        service=FakeCatalogService((_official("Demo", package="demo"),)),
        aur_service=aur,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("Demo")
    page._search_timer.stop()
    page._apply_filters()
    page._aur_search_timer.stop()
    page._start_aur_search()
    aur.future.set_exception(AurNetworkError("offline"))
    app.processEvents()
    app.processEvents()

    assert page.filtered_count == 1
    assert isinstance(page._filtered[0], Application)
    assert page.body_stack.currentIndex() == page.STATE_CATALOG
    assert "нет связи" in page.aur_status_label.text().lower()
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_installed_view_keeps_source_when_requesting_shared_updates():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(service=FakeCatalogService((_official("Demo"),)), aur_service=FakeAurService())
    page.ensure_loaded()
    app.processEvents()

    page.source_combo.setCurrentIndex(page.source_combo.findData("aur"))
    assert page.source_combo.currentData() == "aur"

    # Stage 7.3.2 makes the Installed view source-aware. AUR remains
    # selected and the source control stays available there.
    page.installed_button.setChecked(True)
    app.processEvents()
    app.processEvents()
    assert page.source_combo.currentData() == "aur"
    assert page.source_combo.isEnabled() is True

    requests = []
    page.updates_requested.connect(lambda: requests.append(True))
    page.select_sidebar_entry("updates")
    app.processEvents()
    app.processEvents()
    assert page.source_combo.currentData() == "aur"
    assert page.source_combo.isEnabled() is True
    assert requests == [True]
    assert page._active_view() == "installed"

    # Returning to the catalog preserves the user's source selection.
    page.select_sidebar_entry("catalog")
    app.processEvents()
    assert page.source_combo.currentData() == "aur"
    page.deleteLater()


def test_stage72_aur_card_does_not_depend_on_official_private_text_widget():
    aur_widgets = (ROOT / "src/gui/app_store/aur/widgets.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")

    assert "_CompactTextLabel" not in aur_widgets
    assert "Qt.TextFormat.PlainText" in aur_widgets
    assert 'replace("\\x00", " ")' in aur_widgets
    assert "self._render_cursor = stop" in page
    assert "except Exception:" in page
    assert "LOGGER.exception(" in page

@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage72_aur_unknown_local_state_is_not_presented_as_not_installed():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.aur.widgets import AurPackageCard

    app = QApplication.instance() or QApplication([])
    package = _aur("google-chrome").with_unknown_local_state()
    card = AurPackageCard(package)
    assert package.installed is False
    assert package.local_state_known is False
    assert "Статус недоступен" in card.installed_label.text()
    assert "#55c878" not in card.styleSheet()
    card.deleteLater()
    app.processEvents()
