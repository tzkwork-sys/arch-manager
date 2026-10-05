from __future__ import annotations

from concurrent.futures import Future
import gzip
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from src.app_store.appstream import AppStreamReader
from src.app_store.media import MediaCache, MediaLoadError
from src.app_store.models import Application


ROOT = Path(__file__).resolve().parents[1]
HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_catalog(path: Path, component: str) -> None:
    payload = f'''<?xml version="1.0" encoding="UTF-8"?>
<components version="1.0" origin="archlinux-arch-extra" media_baseurl="https://cdn.example.test/media/">
{component}
</components>
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(payload)
    else:
        path.write_text(payload, encoding="utf-8")


def _rich_component() -> str:
    return '''
<component type="desktop-application">
  <id>org.example.Rich</id>
  <pkgname>rich-app</pkgname>
  <name>Очень длинное название приложения для проверки подробной страницы</name>
  <summary>Полезная программа с подробными сведениями</summary>
  <description>
    <p>Первый <strong>важный</strong> абзац &amp; текст.</p>
    <ul><li>Пункт <em>один</em></li><li>Пункт два<script>bad()</script></li></ul>
  </description>
  <categories><category>Utility</category></categories>
  <icon type="stock">application-x-executable</icon>
  <screenshots>
    <screenshot type="default"><image type="source">one.png</image></screenshot>
    <screenshot><image>https://images.example.test/two.jpg</image></screenshot>
  </screenshots>
  <url type="homepage">https://example.test/</url>
  <url type="bugtracker">https://example.test/bugs</url>
  <url type="help">https://example.test/help</url>
  <url type="homepage">javascript:alert(1)</url>
  <project_license>GPL-3.0-or-later</project_license>
  <launchable type="desktop-id">org.example.Rich.desktop</launchable>
</component>
'''


def test_stage3_appstream_preserves_safe_description_and_links(tmp_path: Path):
    catalog = tmp_path / "extra.xml.gz"
    _write_catalog(catalog, _rich_component())
    result = AppStreamReader([tmp_path], locale_preferences=("ru", "en")).read()
    component = result.components[0]

    assert "Первый важный абзац" in component.description
    assert "• Пункт один" in component.description
    assert "<strong>важный</strong>" in component.description_html
    assert "<ul>" in component.description_html
    assert "<script" not in component.description_html.lower()
    assert "javascript:" not in component.description_html.lower()
    assert component.homepage == "https://example.test/"
    assert component.bugtracker == "https://example.test/bugs"
    assert component.help_url == "https://example.test/help"
    assert component.screenshots == (
        "https://cdn.example.test/media/one.png",
        "https://images.example.test/two.jpg",
    )


def test_stage3_minimal_and_malformed_description_fall_back_without_crashing(tmp_path: Path):
    catalog = tmp_path / "extra.xml"
    _write_catalog(
        catalog,
        '''
<component type="desktop-application">
  <id>org.example.Minimal</id><pkgname>minimal</pkgname><name>Minimal</name>
  <description>Plain &amp; safe</description>
  <launchable type="desktop-id">org.example.Minimal.desktop</launchable>
</component>
''',
    )
    component = AppStreamReader([tmp_path], locale_preferences=("en",)).read().components[0]
    assert component.description == "Plain & safe"
    assert component.description_html == "<p>Plain &amp; safe</p>"
    assert component.screenshots == ()
    assert component.homepage is None


def test_stage3_media_cache_network_disk_and_memory(monkeypatch, tmp_path: Path):
    calls = []

    class Response:
        headers = {"Content-Type": "image/png", "Content-Length": "8"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit > 8
            return b"PNG-DATA"

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    import src.app_store.media as media_module
    monkeypatch.setattr(media_module, "urlopen", fake_urlopen)

    url = "https://example.test/screenshot.png"
    cache = MediaCache(tmp_path / "media", timeout=1.25)
    first = cache.load(url)
    second = cache.load(url)
    assert first.data == b"PNG-DATA" and first.source == "network"
    assert second.data == b"PNG-DATA" and second.source == "memory"
    assert len(calls) == 1

    fresh_cache = MediaCache(tmp_path / "media")
    third = fresh_cache.load(url)
    assert third.data == b"PNG-DATA" and third.source == "disk"
    assert len(calls) == 1


def test_stage3_media_failure_and_invalid_url_do_not_poison_cache(monkeypatch, tmp_path: Path):
    import src.app_store.media as media_module
    monkeypatch.setattr(media_module, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")))
    cache = MediaCache(tmp_path / "media", timeout=0.5)
    with pytest.raises(MediaLoadError):
        cache.load("https://example.test/missing.png")
    with pytest.raises(MediaLoadError):
        cache.load("file:///etc/passwd")
    assert list((tmp_path / "media").glob("*")) == [] if (tmp_path / "media").exists() else True


def _qt_types():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.details import ApplicationDetailsDialog
    from src.gui.app_store.widgets import ApplicationCard

    app = QApplication.instance() or QApplication([])
    return app, ApplicationDetailsDialog, ApplicationCard


class FakeMediaCache:
    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, bool]] = []
        self.fail = fail

    def load_async(self, url: str, *, retry: bool = False):
        self.calls.append((url, retry))
        future = Future()
        if self.fail:
            future.set_exception(MediaLoadError("offline"))
        else:
            # A tiny invalid payload is enough to exercise the GUI fallback path.
            future.set_result(SimpleNamespace(data=b"not-an-image"))
        return future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage3_card_opens_details_contract_and_dialog_hides_missing_sections():
    app, ApplicationDetailsDialog, ApplicationCard = _qt_types()
    minimal = Application(app_id="a", package_name="pkg", name="Minimal")
    card = ApplicationCard(minimal)
    received = []
    card.activated.connect(received.append)
    card.activated.emit(minimal)
    assert received == [minimal]

    dialog = ApplicationDetailsDialog(minimal, media_cache=FakeMediaCache())
    assert dialog.action_button.isEnabled() is False
    assert dialog.findChildren(type(dialog.action_button), "") is not None
    assert "Скриншоты" not in [w.text() for w in dialog.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    dialog.close()
    card.deleteLater()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage3_details_loads_media_only_when_opened_and_retry_survives_failure():
    app, ApplicationDetailsDialog, _ = _qt_types()
    media = FakeMediaCache(fail=True)
    rich = Application(
        app_id="a",
        package_name="pkg",
        name="Rich",
        summary="Summary",
        description="Description",
        description_html="<p>Description</p>",
        screenshots=("https://example.test/shot.png",),
        homepage="https://example.test/",
        license="MIT",
        repository="extra",
        available_version="1.0",
    )
    dialog = ApplicationDetailsDialog(rich, media_cache=media)
    assert media.calls == []
    dialog.show()
    app.processEvents()
    # QTimer starts screenshot fetch only after dialog creation enters the event loop.
    assert media.calls == [("https://example.test/shot.png", False)]
    tile = dialog._tiles["https://example.test/shot.png"]
    tile.retry_requested.emit(tile.url)
    app.processEvents()
    assert media.calls[-1] == ("https://example.test/shot.png", True)
    assert tile.retry_button.isVisible() is True
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage3_installed_card_gets_green_highlight_border():
    app, _, ApplicationCard = _qt_types()
    installed = Application(
        app_id="a",
        package_name="pkg",
        name="Installed",
        installed=True,
        installed_version="1.0",
    )
    card = ApplicationCard(installed)
    assert "#55c878" in card.styleSheet()
    assert "QFrame#appStoreCard" in card.styleSheet()
    card.deleteLater()
    app.processEvents()


def test_stage3_gui_remains_read_only_and_stage2_card_geometry_is_preserved():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    widgets = (ROOT / "src/gui/app_store/widgets.py").read_text(encoding="utf-8")
    assert "ApplicationDetailsDialog" in page
    assert "install_package" not in page + details
    assert "remove_package" not in page + details
    assert "subprocess" not in page + details
    assert "CARD_MIN_WIDTH = 238" in widgets
    assert "CARD_HEIGHT = 154" in widgets
