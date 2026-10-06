from __future__ import annotations

import gzip
import os
from pathlib import Path
import time

from src.app_store.appstream import AppStreamReader
from src.app_store.cache import CatalogCache, build_catalog_fingerprint
from src.app_store.catalog import AppCatalogService
from src.app_store.categories import normalize_categories
from src.app_store.models import Application
from src.app_store.package_state import PacmanPackageStateProvider, PackageState
from src.core.command import CommandResult


def _write_catalog(path: Path, components: str, *, origin: str = "archlinux-arch-extra") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f'''<?xml version="1.0" encoding="UTF-8"?>
<components version="1.0" origin="{origin}" media_baseurl="https://cdn.example.test/media/">
{components}
</components>
'''
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(payload)
    else:
        path.write_text(payload, encoding="utf-8")


def _full_component(*, app_id: str = "org.example.Editor", package: str = "example-editor") -> str:
    return f'''
  <component type="desktop-application">
    <id>{app_id}</id>
    <pkgname>{package}</pkgname>
    <name>Example Editor</name>
    <name xml:lang="ru">Редактор Пример</name>
    <summary>Small editor</summary>
    <summary xml:lang="ru">Небольшой редактор</summary>
    <developer id="org.example"><name>Example Project</name><name xml:lang="ru">Проект Пример</name></developer>
    <description>
      <p>Base description.</p>
      <p xml:lang="ru">Русское описание.</p>
      <ul xml:lang="ru"><li>Первый пункт</li><li>Второй пункт</li></ul>
    </description>
    <categories><category>Utility</category><category>Development</category></categories>
    <keywords><keyword>editor</keyword><keyword xml:lang="ru">редактор</keyword></keywords>
    <icon type="cached" width="64" height="64">example_icon.jxl</icon>
    <screenshots><screenshot type="default"><image type="source">shot.png</image></screenshot></screenshots>
    <url type="homepage">https://example.test/</url>
    <project_license>GPL-3.0-or-later</project_license>
    <launchable type="desktop-id">org.example.Editor.desktop</launchable>
    <provides><binary>example-editor</binary></provides>
  </component>
'''


def test_appstream_reader_parses_arch_catalog_and_localized_fields(tmp_path: Path):
    metadata_dir = tmp_path / "swcatalog" / "xml"
    catalog = metadata_dir / "extra.xml.gz"
    icon_root = tmp_path / "swcatalog" / "icons"
    icon = icon_root / "archlinux-arch-extra" / "64x64" / "example_icon.jxl"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"icon")
    _write_catalog(catalog, _full_component())

    reader = AppStreamReader(
        [metadata_dir],
        icon_root=icon_root,
        locale_preferences=("ru_RU", "ru"),
    )
    files = reader.find_metadata_files()
    assert files == (catalog.resolve(),)

    result = reader.read(files)
    assert result.components_seen == 1
    assert result.desktop_components_seen == 1
    assert result.errors == ()
    component = result.components[0]
    assert component.app_id == "org.example.Editor"
    assert component.package_names == ("example-editor",)
    assert component.name == "Редактор Пример"
    assert component.summary == "Небольшой редактор"
    assert component.publisher == "Проект Пример"
    assert "Русское описание." in component.description
    assert "• Первый пункт" in component.description
    assert component.categories == ("development", "system-tools")
    assert component.icon == str(icon)
    assert component.screenshots == ("https://cdn.example.test/media/shot.png",)
    assert component.homepage == "https://example.test/"
    assert component.desktop_entry == "org.example.Editor.desktop"
    assert component.launchable_binary == "example-editor"
    assert component.source_repository == "extra"


def test_appstream_reader_skips_non_desktop_and_survives_incomplete_component(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    _write_catalog(
        metadata_dir / "core.xml",
        '''
  <component type="addon"><id>org.example.Plugin</id><pkgname>plugin</pkgname></component>
  <component type="desktop-application"><name>No ID</name></component>
  <component type="desktop"><id>org.example.Minimal</id><pkgname>minimal</pkgname><name>Minimal</name></component>
''',
        origin="archlinux-arch-core",
    )
    result = AppStreamReader([metadata_dir], locale_preferences=("en",)).read()
    assert result.components_seen == 3
    assert result.desktop_components_seen == 2
    assert result.skipped_components == 1
    assert len(result.components) == 1
    assert result.components[0].metadata_complete is False
    assert "missing-desktop-entry" in result.components[0].metadata_issues


def test_appstream_reader_handles_malformed_file_without_crashing(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    good = metadata_dir / "extra.xml"
    bad = metadata_dir / "core.xml"
    _write_catalog(good, _full_component())
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("<components><component>", encoding="utf-8")

    result = AppStreamReader([metadata_dir], locale_preferences=("en",)).read()
    assert len(result.components) == 1
    assert len(result.errors) == 1
    assert "core.xml" in result.errors[0]


def test_category_normalization_is_stable_and_has_other_fallback():
    assert normalize_categories(["Network", "Chat", "Science"]) == (
        "internet",
        "science",
        "communication",
    )
    assert normalize_categories(["MadeUpCategory"]) == ("other",)
    assert normalize_categories([]) == ("other",)


def _command(args: list[str], returncode: int, stdout: str = "", stderr: str = "", *, available: bool = True):
    return CommandResult(tuple(args), returncode, stdout, stderr, available=available)


def test_package_state_provider_maps_official_installed_versions_updates_and_sizes():
    def runner(args, *, timeout):
        if args == ["pacman", "-Sl"]:
            return _command(args, 0, "extra example-editor 2.0-1\ncustom foreign-app 9-1\n")
        if args == ["pacman", "-Q"]:
            return _command(args, 0, "example-editor 1.5-1\nforeign-app 9-1\n")
        if args == ["pacman", "-Qu", "--color", "never"]:
            return _command(args, 0, "example-editor 1.5-1 -> 2.0-1\n")
        if args[:3] == ["pacman", "-Si", "--"]:
            return _command(
                args,
                0,
                "Repository      : extra\n"
                "Name            : example-editor\n"
                "Version         : 2.0-1\n"
                "Download Size   : 1.50 MiB\n"
                "Installed Size  : 4.00 MiB\n\n",
            )
        raise AssertionError(args)

    provider = PacmanPackageStateProvider(runner=runner)
    states = provider.collect(["example-editor", "foreign-app"])
    editor = states["example-editor"]
    assert editor.available is True
    assert editor.repository == "extra"
    assert editor.available_version == "2.0-1"
    assert editor.installed_version == "1.5-1"
    assert editor.installed is True
    assert editor.update_available is True
    assert editor.download_size == round(1.5 * 1024**2)
    assert editor.installed_size == 4 * 1024**2
    assert states["foreign-app"].available is False
    assert "not-in-official-repositories" in states["foreign-app"].issues


def test_package_state_provider_handles_missing_pacman_read_only():
    def runner(args, *, timeout):
        return _command(args, 127, stderr="pacman not found", available=False)

    state = PacmanPackageStateProvider(runner=runner).collect(["example"])["example"]
    assert state.available is False
    assert state.metadata_complete is False
    assert state.issues == ("pacman-unavailable",)


def test_catalog_service_joins_only_official_packages_and_deduplicates_app_ids(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    _write_catalog(
        metadata_dir / "extra.xml",
        _full_component(app_id="org.example.Editor", package="example-editor")
        + _full_component(app_id="org.example.Editor", package="example-editor")
        + _full_component(app_id="org.example.Foreign", package="foreign-app"),
    )

    class Provider:
        def collect(self, package_names):
            assert set(package_names) == {"example-editor", "foreign-app"}
            return {
                "example-editor": PackageState(
                    "example-editor",
                    repository="extra",
                    available_version="2.0-1",
                    installed_version="1.5-1",
                    installed=True,
                    update_available=True,
                    available=True,
                ),
                "foreign-app": PackageState("foreign-app", available=False),
            }

    service = AppCatalogService(
        reader=AppStreamReader([metadata_dir], locale_preferences=("en",)),
        package_state_provider=Provider(),
        cache=CatalogCache(tmp_path / "cache.json"),
        pacman_local_dir=tmp_path / "local",
        pacman_sync_dir=tmp_path / "sync",
        pacman_config=tmp_path / "pacman.conf",
    )
    result = service.load_catalog(use_cache=False)
    assert len(result.applications) == 1
    application = result.applications[0]
    assert application.package_name == "example-editor"
    assert application.installed is True
    assert application.update_available is True
    assert result.stats.duplicate_app_ids == 1
    assert result.stats.official_packages_linked == 1


def test_multiple_desktop_components_from_same_package_are_preserved(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    first = _full_component(app_id="org.example.Editor", package="suite")
    second = _full_component(app_id="org.example.Viewer", package="suite").replace(
        "Example Editor", "Example Viewer"
    )
    _write_catalog(metadata_dir / "extra.xml", first + second)

    class Provider:
        def collect(self, package_names):
            return {"suite": PackageState("suite", repository="extra", available_version="1", available=True)}

    service = AppCatalogService(
        reader=AppStreamReader([metadata_dir], locale_preferences=("en",)),
        package_state_provider=Provider(),
        cache=CatalogCache(tmp_path / "cache.json"),
        pacman_local_dir=tmp_path / "local",
        pacman_sync_dir=tmp_path / "sync",
        pacman_config=tmp_path / "pacman.conf",
    )
    result = service.load_catalog(use_cache=False)
    assert {app.app_id for app in result.applications} == {"org.example.Editor", "org.example.Viewer"}


def test_catalog_cache_round_trip_and_schema_types(tmp_path: Path):
    cache = CatalogCache(tmp_path / "catalog.json")
    application = Application(
        app_id="org.example.App",
        package_name="example",
        name="Example",
        categories=("office",),
        screenshots=("https://example.test/1.png",),
        metadata_issues=("missing-icon",),
    )
    cache.save("fingerprint", [application], {"applications_built": 1})
    loaded = cache.load("fingerprint")
    assert loaded is not None
    applications, stats = loaded
    assert applications == (application,)
    assert isinstance(applications[0].categories, tuple)
    assert stats["applications_built"] == 1
    assert cache.load("different") is None


def test_cache_fingerprint_changes_with_appstream_and_package_databases(tmp_path: Path):
    metadata = tmp_path / "extra.xml"
    local = tmp_path / "local"
    sync = tmp_path / "sync"
    config = tmp_path / "pacman.conf"
    local.mkdir()
    sync.mkdir()
    metadata.write_text("one", encoding="utf-8")
    (sync / "extra.db").write_text("db1", encoding="utf-8")
    config.write_text("[options]", encoding="utf-8")

    first = build_catalog_fingerprint(
        [metadata], pacman_local_dir=local, pacman_sync_dir=sync, pacman_config=config
    )
    time.sleep(0.002)
    metadata.write_text("two", encoding="utf-8")
    os.utime(metadata, None)
    second = build_catalog_fingerprint(
        [metadata], pacman_local_dir=local, pacman_sync_dir=sync, pacman_config=config
    )
    assert second != first

    time.sleep(0.002)
    (sync / "extra.db").write_text("db2-longer", encoding="utf-8")
    third = build_catalog_fingerprint(
        [metadata], pacman_local_dir=local, pacman_sync_dir=sync, pacman_config=config
    )
    assert third != second

    time.sleep(0.002)
    (local / "new-package-1").mkdir()
    fourth = build_catalog_fingerprint(
        [metadata], pacman_local_dir=local, pacman_sync_dir=sync, pacman_config=config
    )
    assert fourth != third


def test_catalog_service_uses_cache_without_requerying_provider(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    _write_catalog(metadata_dir / "extra.xml", _full_component())
    sync = tmp_path / "sync"
    local = tmp_path / "local"
    sync.mkdir()
    local.mkdir()
    (sync / "extra.db").write_text("stable", encoding="utf-8")
    config = tmp_path / "pacman.conf"
    config.write_text("[options]", encoding="utf-8")

    class Provider:
        calls = 0

        def collect(self, package_names):
            self.calls += 1
            return {
                "example-editor": PackageState(
                    "example-editor", repository="extra", available_version="2", available=True
                )
            }

    provider = Provider()
    service = AppCatalogService(
        reader=AppStreamReader([metadata_dir], locale_preferences=("en",)),
        package_state_provider=provider,
        cache=CatalogCache(tmp_path / "catalog.json"),
        pacman_local_dir=local,
        pacman_sync_dir=sync,
        pacman_config=config,
    )
    first = service.load_catalog()
    second = service.load_catalog()
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert provider.calls == 1
    assert second.applications == first.applications


def test_empty_catalog_and_missing_appstream_data_are_safe(tmp_path: Path):
    class Provider:
        def collect(self, package_names):
            raise AssertionError("provider should not be needed for empty input")

    service = AppCatalogService(
        reader=AppStreamReader([tmp_path / "missing"]),
        package_state_provider=Provider(),
        cache=CatalogCache(tmp_path / "cache.json"),
        pacman_local_dir=tmp_path / "local",
        pacman_sync_dir=tmp_path / "sync",
        pacman_config=tmp_path / "pacman.conf",
    )
    result = service.load_catalog(use_cache=False)
    assert result.applications == ()
    assert result.stats.metadata_files == 0
    assert result.stats.applications_built == 0


def test_application_domain_model_has_no_qt_dependency():
    source = (Path(__file__).resolve().parents[1] / "src/app_store/models.py").read_text(encoding="utf-8")
    assert "PySide" not in source
    assert "Qt" not in source


def test_package_state_provider_marks_official_package_not_installed():
    def runner(args, *, timeout):
        if args == ["pacman", "-Sl"]:
            return _command(args, 0, "extra example-viewer 3.1-2\n")
        if args == ["pacman", "-Q"]:
            return _command(args, 0, "other-installed 1-1\n")
        if args == ["pacman", "-Qu", "--color", "never"]:
            return _command(args, 0, "")
        if args[:3] == ["pacman", "-Si", "--"]:
            return _command(
                args,
                0,
                "Repository      : extra\n"
                "Name            : example-viewer\n"
                "Version         : 3.1-2\n"
                "Download Size   : 800.00 KiB\n"
                "Installed Size  : 2.00 MiB\n\n",
            )
        raise AssertionError(args)

    state = PacmanPackageStateProvider(runner=runner).collect(["example-viewer"])["example-viewer"]
    assert state.available is True
    assert state.available_version == "3.1-2"
    assert state.installed is False
    assert state.installed_version is None
    assert state.update_available is False


def test_catalog_async_api_builds_off_gui_thread(tmp_path: Path):
    metadata_dir = tmp_path / "xml"
    _write_catalog(metadata_dir / "extra.xml", _full_component())

    class Provider:
        def collect(self, package_names):
            return {
                "example-editor": PackageState(
                    "example-editor", repository="extra", available_version="2", available=True
                )
            }

    service = AppCatalogService(
        reader=AppStreamReader([metadata_dir], locale_preferences=("en",)),
        package_state_provider=Provider(),
        cache=CatalogCache(tmp_path / "cache.json"),
        pacman_local_dir=tmp_path / "local",
        pacman_sync_dir=tmp_path / "sync",
        pacman_config=tmp_path / "pacman.conf",
    )
    future = service.load_catalog_async(use_cache=False)
    result = future.result(timeout=2)
    assert result.stats.applications_built == 1


def test_app_store_stage1_is_isolated_from_gui_and_privileged_actions():
    root = Path(__file__).resolve().parents[1]
    app_store_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "src/app_store").glob("*.py")
    )
    main_window = (root / "src/gui/main_window.py").read_text(encoding="utf-8")
    assert "PySide6" not in app_store_source
    assert "pkexec" not in app_store_source
    assert "sudo " not in app_store_source
    # Stage 2 may wire the dedicated GUI package into MainWindow, but the main
    # window must not reach into the toolkit-independent catalog internals.
    assert "from src.app_store" not in main_window
    assert "AppCatalogService" not in main_window

def test_package_state_provider_does_not_turn_pacman_q_failure_into_known_not_installed():
    def runner(args, *, timeout):
        if args == ["pacman", "-Sl"]:
            return _command(args, 0, "extra example-viewer 3.1-2\n")
        if args == ["pacman", "-Q"]:
            return _command(args, 1, stderr="local database unavailable")
        if args == ["pacman", "-Qu", "--color", "never"]:
            return _command(args, 0, "")
        if args[:3] == ["pacman", "-Si", "--"]:
            return _command(
                args,
                0,
                "Repository      : extra\n"
                "Name            : example-viewer\n"
                "Version         : 3.1-2\n"
                "Download Size   : 800.00 KiB\n"
                "Installed Size  : 2.00 MiB\n\n",
            )
        raise AssertionError(args)

    state = PacmanPackageStateProvider(runner=runner).collect(["example-viewer"])["example-viewer"]
    assert state.installed is False
    assert state.installed_state_known is False
    assert state.metadata_complete is False
    assert "installed-state-unavailable" in state.issues


def test_package_state_provider_marks_missing_package_details_incomplete():
    def runner(args, *, timeout):
        if args == ["pacman", "-Sl"]:
            return _command(args, 0, "extra example-viewer 3.1-2\n")
        if args == ["pacman", "-Q"]:
            return _command(args, 0, "")
        if args == ["pacman", "-Qu", "--color", "never"]:
            return _command(args, 0, "")
        if args[:3] == ["pacman", "-Si", "--"]:
            return _command(args, 1, stderr="package details unavailable")
        raise AssertionError(args)

    state = PacmanPackageStateProvider(runner=runner).collect(["example-viewer"])["example-viewer"]
    assert state.available is True
    assert state.installed_state_known is True
    assert state.metadata_complete is False
    assert "package-details-unavailable" in state.issues
