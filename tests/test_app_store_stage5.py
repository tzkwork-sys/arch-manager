from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import threading
import time

from src.app_store.appstream import AppStreamComponent
from src.app_store.catalog import AppCatalogService
from src.app_store.installed import InstalledApplications
from src.app_store.models import Application
from src.app_store.package_state import PackageState
from src.app_store.update_mapping import ApplicationUpdateMapper
from src.core.update_state import UpdateStateService
from src.core.updates import (
    UpdateDetails,
    UpdateItem,
    UpdateSourceDetails,
    summary_from_details,
)


ROOT = Path(__file__).resolve().parents[1]


def _details(*official: UpdateItem) -> UpdateDetails:
    return UpdateDetails(
        official=UpdateSourceDetails(tuple(official), True),
        aur=UpdateSourceDetails((), True),
        checked_at=datetime.now().astimezone(),
    )


def test_stage5_regular_installed_badge_does_not_show_version():
    source = (ROOT / "src/gui/app_store/widgets.py").read_text(encoding="utf-8")
    assert 'status_text = "✓ Установлено"' in source
    assert 'status_text += f" · {application.installed_version}"' not in source
    assert 'status_text = f"↑ {old_version} → {new_version}"' in source


def test_stage5_installed_view_contains_only_installed_apps_and_tracks_remove():
    apps = (
        Application("a", "a", "A", installed=True, installed_version="1"),
        Application("b", "b", "B", installed=False),
    )
    assert [a.app_id for a in InstalledApplications.filter(apps)] == ["a"]

    after_remove = tuple(
        Application.from_dict({**app.to_dict(), "installed": False})
        if app.app_id == "a"
        else app
        for app in apps
    )
    assert InstalledApplications.filter(after_remove) == ()


def test_stage5_install_becomes_visible_in_installed_view():
    app = Application("a", "pkg", "A", installed=False)
    state = PackageState(
        "pkg",
        repository="extra",
        available_version="2",
        installed_version="2",
        installed=True,
        available=True,
    )
    installed = InstalledApplications.with_states((app,), {"pkg": state})
    assert len(installed) == 1
    assert installed[0].installed is True
    assert installed[0].installed_version == "2"


def test_stage5_package_update_maps_to_every_visible_component_for_same_package():
    apps = (
        Application("org.demo.Editor", "demo", "Editor", installed=True),
        Application("org.demo.Viewer", "demo", "Viewer", installed=True),
    )
    item = UpdateItem("demo", "1.0", "1.1", "official")
    mapped = ApplicationUpdateMapper.map_updates(apps, (item,))
    assert [app.app_id for app in mapped] == ["org.demo.Editor", "org.demo.Viewer"]
    assert all(app.installed_version == "1.0" for app in mapped)
    assert all(app.available_version == "1.1" for app in mapped)
    assert all(app.update_available for app in mapped)


def test_stage5_component_with_multiple_package_metadata_maps_installed_alternative():
    app = Application(
        "org.demo.App",
        "demo-runtime",
        "Demo",
        package_names=("demo-runtime", "demo-full"),
        installed=True,
    )
    item = UpdateItem("demo-full", "4", "5", "official")
    mapped = ApplicationUpdateMapper.map_updates((app,), (item,))
    assert len(mapped) == 1
    assert mapped[0].package_name == "demo-full"
    assert mapped[0].installed_version == "4"
    assert mapped[0].available_version == "5"


def test_stage5_catalog_prefers_installed_package_when_component_has_alternatives():
    component = AppStreamComponent(
        app_id="org.demo.App",
        name="Demo",
        summary="Demo app",
        description="",
        description_html="",
        categories=(),
        raw_categories=(),
        keywords=(),
        icon=None,
        icon_type=None,
        screenshots=(),
        homepage=None,
        bugtracker=None,
        help_url=None,
        license=None,
        package_names=("demo-runtime", "demo-full"),
        desktop_entry=None,
        launchable_binary=None,
        source_repository="extra",
        source_file="test.xml",
        metadata_complete=True,
        metadata_issues=(),
    )
    states = {
        "demo-runtime": PackageState("demo-runtime", available=True, installed=False),
        "demo-full": PackageState("demo-full", available=True, installed=True),
    }
    selected = AppCatalogService._select_package_state(component, states)
    assert selected is states["demo-full"]


def test_stage5_shared_update_state_coalesces_concurrent_checks():
    calls = 0
    entered = threading.Event()

    def collector() -> UpdateDetails:
        nonlocal calls
        calls += 1
        entered.set()
        time.sleep(0.05)
        return _details(UpdateItem("firefox", "1", "2", "official"))

    service = UpdateStateService(collector)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.refresh)
        assert entered.wait(timeout=1)
        second = pool.submit(service.refresh)
        one = first.result(timeout=2)
        two = second.result(timeout=2)

    assert calls == 1
    assert one.generation == two.generation == 1
    assert one.details == two.details


def test_stage5_shared_summary_is_derived_from_same_details():
    details = _details(
        UpdateItem("firefox", "1", "2", "official"),
        UpdateItem("vlc", "3", "4", "official"),
    )
    service = UpdateStateService(lambda: details)
    snapshot = service.refresh()
    assert snapshot.details is details
    assert snapshot.summary == summary_from_details(details)
    assert snapshot.summary.official.count == 2


def test_stage5_store_has_installed_and_updates_views_but_no_partial_update_executor():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    mapper = (ROOT / "src/app_store/update_mapping.py").read_text(encoding="utf-8")
    main = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")

    assert 'setText("Установленные")' in page
    assert 'setText("Обновления")' in page
    assert 'QPushButton("Перейти к обновлениям")' not in page
    assert 'QPushButton("Перейти к системным обновлениям")' in page
    assert '"Доступны системные обновления"' in page
    assert '"Также доступны системные обновления"' in page
    assert "ApplicationUpdateMapper.map_updates" in page
    assert "updates_requested.emit" in page
    assert "pacman" not in page
    assert "pkexec" not in page
    assert "subprocess" not in page
    assert "execute_package_action" not in mapper
    assert "app_store_page.updates_requested.connect(lambda: self.set_page(1))" in main


def test_stage5_pages_share_update_state_and_resync_after_mutations():
    updates = (ROOT / "src/gui/updates_page.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "src/gui/dashboard.py").read_text(encoding="utf-8")
    main = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")
    provider = (ROOT / "src/app_store/package_state.py").read_text(encoding="utf-8")

    assert "get_update_state_service" in updates
    assert "self.update_state.refresh().details" in updates
    assert "get_update_state_service" in dashboard
    assert '("updates", self.update_state.refresh)' in dashboard
    assert "update_state_changed.emit(result.details)" in dashboard
    assert "update_state_changed" in updates
    assert "system_update_finished" in updates
    assert "package_state_changed" in main
    assert "self.update_state.invalidate()" in main
    assert "updates_page.refresh_if_idle()" in main
    assert "update_state_service.refresh(force=False)" in provider


def test_stage5_system_updates_prompt_is_contextual_and_uses_shared_official_state():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")

    assert "def _system_update_count" in page
    assert 'getattr(official, "items", ())' in page
    assert 'self._active_view() == "updates" and system_count > 0' in page
    assert 'has_app_updates=app_update_count > 0' in page
    assert 'button.clicked.connect(self.updates_requested.emit)' in page
    assert 'self.open_updates_button' not in page
