from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import os

import pytest

from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application
from src.app_store.popularity import PopularitySnapshot


HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeCatalogService:
    def __init__(self, applications: tuple[Application, ...] = ()) -> None:
        self.applications = applications or (
            Application(
                app_id="org.example.Demo",
                package_name="demo",
                package_names=("demo",),
                name="Demo",
                repository="extra",
            ),
        )

    def load_catalog_async(self, *, use_cache: bool = True):
        future: Future[CatalogLoadResult] = Future()
        future.set_result(
            CatalogLoadResult(
                applications=self.applications,
                stats=CatalogStats(metadata_files=1, applications_built=len(self.applications)),
                cache_hit=use_cache,
            )
        )
        return future


class FakePopularityService:
    def load_async(self, *, use_cache: bool = True):
        del use_cache
        future: Future[PopularitySnapshot] = Future()
        future.set_result(PopularitySnapshot.build({}, {}))
        return future


class FakeSystemPackageService:
    def __init__(self, result: tuple[Application, ...]) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    def invalidate(self) -> None:
        pass

    def search_async(self, query: str, *, use_cache: bool = True):
        self.calls.append((query, use_cache))
        future: Future[tuple[Application, ...]] = Future()
        future.set_result(self.result)
        return future


def _github_cli_application() -> Application:
    return Application(
        app_id="system-package:github-cli",
        package_name="github-cli",
        package_names=("github-cli",),
        name="github-cli",
        summary="The GitHub CLI",
        repository="extra",
        available_version="2.102.0-1",
        metadata_source="pacman-system-package",
    )


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_catalog_search_merges_official_system_package_result():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    system_service = FakeSystemPackageService((_github_cli_application(),))
    page = AppStorePage(
        service=FakeCatalogService(),
        popularity_service=FakePopularityService(),
        system_package_service=system_service,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("github-cli")
    page._queue_system_package_search(immediate=True)
    app.processEvents()

    assert page._active_view() == "catalog"
    assert system_service.calls
    assert any(
        isinstance(item, Application) and item.package_name == "github-cli"
        for item in page._filtered
    )
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_global_official_search_deduplicates_appstream_package():
    from src.gui.app_store.page import AppStorePage

    appstream = Application(
        app_id="org.mozilla.firefox",
        package_name="firefox",
        package_names=("firefox",),
        name="Firefox",
        repository="extra",
    )
    pacman = Application(
        app_id="system-package:firefox",
        package_name="firefox",
        package_names=("firefox",),
        name="firefox",
        repository="extra",
        metadata_source="pacman-system-package",
    )

    merged = AppStorePage._merge_official_search_results((appstream,), (pacman,))
    assert merged == (appstream,)


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_equal_relevance_prefers_official_over_more_popular_aur():
    from PySide6.QtWidgets import QApplication
    from src.app_store.aur.models import AurPackage
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(
        service=FakeCatalogService(),
        popularity_service=FakePopularityService(),
        system_package_service=FakeSystemPackageService((_github_cli_application(),)),
    )

    official = _github_cli_application()
    aur = AurPackage(
        name="github-cli-git",
        package_base="github-cli-git",
        version="2.18.1.r33.g9ea76237",
        description="The GitHub CLI tool",
        popularity=999.0,
        votes=999999,
    )

    page.search_edit.blockSignals(True)
    page.search_edit.setText("github")
    page.search_edit.blockSignals(False)
    page._popularity_scores = {"github-cli": 0.01, "github-cli-git": 99.0}
    page._popularity_counts = {"github-cli": 1, "github-cli-git": 99999}

    ordered = page._sort_results((aur, official))
    assert ordered[0] is official
    assert ordered[1] is aur
    page.deleteLater()

class DeferredSystemPackageService:
    def __init__(self) -> None:
        self.future: Future[tuple[Application, ...]] = Future()
        self.calls: list[tuple[str, bool]] = []

    def invalidate(self) -> None:
        pass

    def search_async(self, query: str, *, use_cache: bool = True):
        self.calls.append((query, use_cache))
        return self.future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_unified_search_surfaces_official_package_search_failure():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    system_service = DeferredSystemPackageService()
    page = AppStorePage(
        service=FakeCatalogService(),
        popularity_service=FakePopularityService(),
        system_package_service=system_service,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("github-cli")
    page._search_timer.stop()
    page._system_package_timer.stop()
    page._start_system_package_search()
    system_service.future.set_exception(RuntimeError("pacman unavailable"))
    app.processEvents()
    app.processEvents()

    assert page.filtered_count == 0
    assert "официальные пакеты" in page.aur_status_label.text().lower()
    assert "недоступен" in page.aur_status_label.text().lower()
    assert "недоступен" in page.empty_text.text().lower()
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_unified_search_ignores_stale_system_result_after_source_switch():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    system_service = DeferredSystemPackageService()
    page = AppStorePage(
        service=FakeCatalogService(),
        popularity_service=FakePopularityService(),
        system_package_service=system_service,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("github-cli")
    page._search_timer.stop()
    page._system_package_timer.stop()
    page._start_system_package_search()
    assert system_service.calls

    page.source_combo.setCurrentIndex(page.source_combo.findData("aur"))
    app.processEvents()
    assert page._system_package_loading is False
    assert page._system_package_results == ()

    system_service.future.set_result((_github_cli_application(),))
    app.processEvents()
    app.processEvents()

    assert page._system_package_results == ()
    assert all(
        not isinstance(item, Application) or item.package_name != "github-cli"
        for item in page._filtered
    )
    page.deleteLater()
