from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import os
from pathlib import Path

import pytest

from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application
from src.app_store.system_packages import SystemPackageSearchService
from src.core.command import CommandResult


ROOT = Path(__file__).resolve().parents[1]
HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _result(command, returncode=0, stdout="", stderr="") -> CommandResult:
    return CommandResult(tuple(command), returncode, stdout, stderr)


def test_system_package_search_fuzzy_name_finds_webkitgtk_and_exposes_details():
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        command = tuple(command)
        calls.append(command)
        if command == ("pacman", "-Sl"):
            return _result(
                command,
                stdout=(
                    "core bash 5.3.3-1\n"
                    "extra webkit2gtk-4.1 2.50.1-1\n"
                    "extra webkit2gtk-6.0 2.50.1-1\n"
                ),
            )
        if command == ("pacman", "-Q"):
            return _result(command, stdout="bash 5.3.3-1\nwebkit2gtk-4.1 2.50.1-1\n")
        if command[:4] == ("pacman", "-Ss", "--color", "never"):
            # Deliberately no direct match: the fuzzy package-name index must
            # still map WebKitGTK to webkit2gtk-4.1.
            return _result(command, returncode=1)
        if command[:3] == ("pacman", "-Si", "--"):
            names = command[3:]
            blocks = []
            for name in names:
                blocks.append(
                    "Repository      : extra\n"
                    f"Name            : {name}\n"
                    "Version         : 2.50.1-1\n"
                    "Description     : Web content engine for GTK\n"
                    "Architecture    : x86_64\n"
                    "URL             : https://webkitgtk.org/\n"
                    "Licenses        : LGPL-2.1-or-later\n"
                    "Depends On      : glib2  gtk3  libsoup3\n"
                    "Optional Deps   : gst-plugins-base: media support\n"
                    "Provides        : None\n"
                    "Conflicts With  : None\n"
                    "Download Size   : 27.50 MiB\n"
                    "Installed Size  : 120.00 MiB\n"
                )
            return _result(command, stdout="\n\n".join(blocks) + "\n")
        if command[:3] == ("pacman", "-Qi", "--"):
            return _result(
                command,
                stdout=(
                    "Name            : webkit2gtk-4.1\n"
                    "Version         : 2.50.1-1\n"
                    "Installed Size  : 119.50 MiB\n"
                    "Required By     : vpnus\n"
                    "Optional For    : None\n"
                ),
            )
        raise AssertionError(command)

    service = SystemPackageSearchService(runner=runner, result_limit=20)
    results = service.search("WebKitGTK")

    assert results
    package = next(item for item in results if item.package_name == "webkit2gtk-4.1")
    assert package.metadata_source == "pacman-system-package"
    assert package.installed is True
    assert package.repository == "extra"
    assert package.summary == "Web content engine for GTK"
    assert package.dependencies_text == "glib2  gtk3  libsoup3"
    assert package.required_by_text == "vpnus"
    assert package.download_size == round(27.5 * 1024**2)
    assert package.installed_size == round(119.5 * 1024**2)
    assert all("-Sy" not in part for call in calls for part in call)


def test_system_package_search_cache_avoids_reloading_full_indexes():
    counts = {"sl": 0, "q": 0}

    def runner(command, **_kwargs):
        command = tuple(command)
        if command == ("pacman", "-Sl"):
            counts["sl"] += 1
            return _result(command, stdout="extra cmake 4.1.1-1\n")
        if command == ("pacman", "-Q"):
            counts["q"] += 1
            return _result(command, stdout="")
        if command[:4] == ("pacman", "-Ss", "--color", "never"):
            return _result(command, returncode=1)
        if command[:3] == ("pacman", "-Si", "--"):
            return _result(
                command,
                stdout=(
                    "Repository      : extra\n"
                    "Name            : cmake\n"
                    "Version         : 4.1.1-1\n"
                    "Description     : Cross-platform build system\n"
                ),
            )
        raise AssertionError(command)

    service = SystemPackageSearchService(runner=runner, cache_ttl_seconds=300)
    assert service.search("cmake")
    assert service.search("cmak")
    assert counts == {"sl": 1, "q": 1}


class FakeCatalogService:
    def load_catalog_async(self, *, use_cache: bool = True):
        del use_cache
        future: Future[CatalogLoadResult] = Future()
        future.set_result(
            CatalogLoadResult(
                applications=(
                    Application(
                        app_id="org.example.App",
                        package_name="demo",
                        name="Demo",
                        repository="extra",
                    ),
                ),
                stats=CatalogStats(metadata_files=1, applications_built=1),
                cache_hit=True,
            )
        )
        return future


class FakeSystemPackageService:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def invalidate(self):
        pass

    def search_async(self, query: str, *, use_cache: bool = True):
        self.calls.append((query, use_cache))
        future: Future[tuple[Application, ...]] = Future()
        future.set_result(
            (
                Application(
                    app_id="system-package:webkit2gtk-4.1",
                    package_name="webkit2gtk-4.1",
                    package_names=("webkit2gtk-4.1",),
                    name="webkit2gtk-4.1",
                    summary="Web content engine for GTK",
                    repository="extra",
                    available_version="2.50.1-1",
                    installed_version="2.50.1-1",
                    installed=True,
                    metadata_source="pacman-system-package",
                    dependencies_text="glib2 gtk3",
                ),
            )
        )
        return future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_system_packages_sidebar_mode_uses_dedicated_search_and_hides_app_filters():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    system_service = FakeSystemPackageService()
    page = AppStorePage(
        service=FakeCatalogService(),
        system_package_service=system_service,
    )
    page.ensure_loaded()
    app.processEvents()

    page.search_edit.setText("WebKitGTK")
    page.select_sidebar_entry("system-packages")
    page._start_system_package_search()
    app.processEvents()

    assert page._active_view() == "system-packages"
    assert page.search_edit.placeholderText() == "Поиск системных пакетов…"
    assert page.source_combo.isHidden()
    assert page.sort_combo.isHidden()
    assert page.filtered_count == 1
    assert page._filtered[0].package_name == "webkit2gtk-4.1"
    assert "пакет" in page.count_label.text()
    page.deleteLater()


def test_main_window_exposes_system_packages_in_context_sidebar():
    source = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")
    assert '"Системные пакеты", "system-packages"' in source
    assert '"package-x-generic"' in source
