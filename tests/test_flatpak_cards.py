"""Flatpak card metadata, icons and correct empty/partial installed state."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import os

import pytest

from src.app_store.flatpak import FlatpakError, FlatpakService, find_flatpak_icon
from src.core.command import CommandResult


def response(args, text="", code=0, error=""):
    return CommandResult(tuple(args), code, text, error)


def test_empty_installed_requires_two_successful_scope_reads():
    scopes = []

    def runner(args, *, timeout=45):
        del timeout
        scopes.append(args[3])
        return response(args)

    assert FlatpakService(runner=runner).installed() == ()
    assert scopes == ["--system", "--user"]


def test_partial_failure_does_not_masquerade_as_zero():
    def runner(args, *, timeout=45):
        del timeout
        return response(args, code=1, error="permission denied") if "--system" in args else response(args)

    with pytest.raises(FlatpakError, match="system: permission denied"):
        FlatpakService(runner=runner).installed()


def test_installed_card_retains_version_description_origin_and_scope():
    def runner(args, *, timeout=45):
        del timeout
        if "--system" in args:
            return response(args, "io.github.mhogomchungu.media-downloader\tMedia Downloader\t5.6.6\tstable\tflathub\tA GUI to yt-dlp\n")
        return response(args)

    apps = FlatpakService(runner=runner).installed()
    assert len(apps) == 1
    app = apps[0]
    assert app.installed
    assert app.installed_version == "5.6.6"
    assert app.summary == "A GUI to yt-dlp"
    assert app.metadata_source == "flatpak:system"
    assert app.repository == "Flatpak · flathub"


def test_icon_export_directory_is_used_if_available(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    icon = tmp_path / "flatpak/exports/share/icons/hicolor/128x128/apps/org.example.Editor.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"not-a-real-png")
    assert find_flatpak_icon("org.example.Editor", scope="user") == str(icon)
    assert find_flatpak_icon("../etc/passwd", scope="user") is None
    assert find_flatpak_icon("org.example.Editor", scope="invalid") is None


HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.skipif(not HAS_QT, reason="PySide6 not available")
def test_flatpak_card_has_version_source_and_full_id_tooltip():
    from PySide6.QtWidgets import QApplication
    from src.app_store.models import Application
    from src.gui.app_store.widgets import ApplicationCard, CARD_HEIGHT

    app = QApplication.instance() or QApplication([])
    product = Application(
        app_id="flatpak:system:org.example.Editor", package_name="org.example.Editor",
        name="Editor", summary="A nice editor", repository="Flatpak · flathub",
        metadata_source="flatpak:system", available_version="2.3.1", installed=False,
    )
    widget = ApplicationCard(product)
    assert "org.example.Editor" in widget.toolTip()
    assert widget.height() > CARD_HEIGHT
    assert widget.category_label.full_text == "Flatpak · flathub"
    assert widget.installed_label.text() == "Доступно во Flathub"
    assert any(child.objectName() == "appStoreFlatpakVersion" and "2.3.1" in child.full_text
               for child in widget.findChildren(type(widget.category_label)))
    widget.deleteLater()
    app.processEvents()


def test_shared_catalog_card_does_not_expose_technical_package_fields():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/gui/app_store/widgets.py").read_text(encoding="utf-8")
    # The old Stage 2 privacy/isolation contract applies to the shared widget.
    assert "package_name" not in source
    assert "flatpak_card_tooltip(application)" in source


def test_flatpak_tooltip_comes_from_separate_metadata_formatter():
    from src.app_store.flatpak import flatpak_card_tooltip
    from src.app_store.models import Application

    application = Application(
        app_id="flatpak:system:org.example.Editor", package_name="org.example.Editor",
        name="Editor", metadata_source="flatpak:system", repository="Flatpak · flathub",
        available_version="2.3.1", installed=False,
    )
    lines = flatpak_card_tooltip(application)
    assert "Flatpak ID: org.example.Editor" in lines
    assert "Версия: 2.3.1" in lines
    assert "Предлагаемая установка: общесистемная" in lines


def test_flatpak_view_reports_errors_not_zero_in_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    assert 'Flatpak: проверка не удалась' in source
    assert 'Проверяю установленные приложения Flatpak' in source
    assert 'Во Flathub по этому запросу ничего не найдено.' in source
