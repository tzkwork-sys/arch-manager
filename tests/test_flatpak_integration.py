"""Flatpak backend and cross-source identity regression tests."""
from __future__ import annotations

from src.app_store.flatpak import FlatpakAction, FlatpakService
from src.core.command import CommandResult
from src.core.updates import UpdateDetails, UpdateSourceDetails, UpdatesSummary, UpdateSourceSummary
from src.app_store.models import Application
from datetime import datetime, timezone
import pytest


def result(argv, text="", code=0):
    return CommandResult(tuple(argv), code, text, "")


def fake_runner(argv, *, timeout=45):
    del timeout
    command = " ".join(argv)
    if "list --app --system" in command:
        return result(argv, "io.github.mhogomchungu.media-downloader\tMedia Downloader\t5.6.6\tstable\tflathub\n")
    if "list --app --user" in command:
        return result(argv, "")
    if "remote-ls --updates --app --system" in command:
        return result(argv, "io.github.mhogomchungu.media-downloader\t5.6.7\tstable\n")
    if "remotes --system" in command:
        return result(argv, "flathub\n")
    if "search --columns=" in command:
        return result(argv, "io.github.mhogomchungu.media-downloader\tMedia Downloader\tDownload video\t5.6.7\tflathub\n")
    return result(argv, "")


def test_installed_flatpak_is_not_pacman_or_aur():
    service = FlatpakService(runner=fake_runner)
    installed = service.installed()
    assert len(installed) == 1
    app = installed[0]
    assert app.package_name == "io.github.mhogomchungu.media-downloader"
    assert app.metadata_source == "flatpak:system"
    assert app.installed is True
    assert app.installed_version == "5.6.6"


def test_flathub_search_returns_separate_source():
    service = FlatpakService(runner=fake_runner)
    result_apps = service.search("media")
    assert len(result_apps) == 1
    assert result_apps[0].package_name == service.installed()[0].package_name
    assert result_apps[0].metadata_source == "flatpak:system"


def test_flatpak_update_has_own_installation_scope():
    details = FlatpakService(runner=fake_runner).update_details()
    assert details.available
    assert len(details.items) == 1
    item = details.items[0]
    assert item.source == "flatpak"
    assert item.installation == "system"
    assert item.current_version == "5.6.6"
    assert item.new_version == "5.6.7"


@pytest.mark.parametrize("bad_id", ["--delete", "a;rm -rf /", "foo", "", "org.example.App/branch"])
def test_action_rejects_invalid_references(bad_id):
    with pytest.raises(ValueError):
        FlatpakAction("remove", bad_id, "system").argv()


def test_action_never_calls_shell_or_pacman():
    action = FlatpakAction("install", "io.github.mhogomchungu.media-downloader", "user")
    assert action.argv() == ("flatpak", "install", "--assumeyes", "--noninteractive", "--user", "flathub", action.app_id)
    assert FlatpakAction("remove", action.app_id, "system").argv()[-2:] == ("--system", action.app_id)


def test_update_summaries_aggregate_three_sources():
    official = UpdateSourceDetails((), True)
    aur = UpdateSourceDetails((), True)
    flatpak = FlatpakService(runner=fake_runner).update_details()
    details = UpdateDetails(official, aur, datetime.now(timezone.utc), flatpak=flatpak)
    assert details.total == 1
    assert details.all_items[0].source == "flatpak"
    from src.core.updates import summary_from_details
    assert summary_from_details(details).total == 1


def test_same_name_in_multiple_sources_keeps_distinct_cards():
    from src.gui.app_store.aur.integration import merge_store_results
    official = Application(app_id="official:editor", package_name="editor", name="Editor")
    flatpak = Application(app_id="flatpak:system:org.example.Editor", package_name="org.example.Editor", name="Editor", metadata_source="flatpak:system")
    combined = merge_store_results((official,), (), source="all", flatpak=(flatpak,))
    assert len(combined) == 2
    assert {app.app_id for app in combined} == {official.app_id, flatpak.app_id}
