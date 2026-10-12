"""On-demand Flathub AppStream detail parser / cache / GUI integration."""
from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import json
import os
import time

import pytest

from src.app_store.flathub_metadata import (
    FlathubMetadataError, FlathubMetadataService, parse_flathub_metadata,
    sanitize_description,
)
from src.app_store.models import Application

APP_ID = "io.github.mhogomchungu.media-downloader"
IMAGE = "https://dl.flathub.org/repo/screenshots/example/624x351/example.png"
ICON = "https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/example.png"


def sample():
    return {
        "id": APP_ID,
        "name": "Media Downloader", "summary": "A GUI to yt-dlp, gallery-dl and others",
        "description": "<p>Download <b>video</b> files.</p><script>alert(42)</script>",
        "project_license": "GPL-2.0-or-later",
        "developer_name": "Francis Banyikwa",
        "icon": ICON,
        "screenshots": [{"624x351": IMAGE}, {"624x351": "file:///etc/passwd"}],
        "urls": {"homepage": "https://mhogomchungu.github.io/media-downloader",
                 "bugtracker": "https://github.com/mhogomchungu/media-downloader/issues",
                 "help": "https://github.com/mhogomchungu/media-downloader/wiki"},
    }


def test_parser_extracts_rich_flathub_fields_without_remote_images():
    model = parse_flathub_metadata(sample(), APP_ID)
    assert model.name == "Media Downloader"
    assert model.summary.startswith("A GUI")
    assert model.publisher == "Francis Banyikwa"
    assert model.license == "GPL-2.0-or-later"
    assert model.screenshots == (IMAGE,)
    assert model.homepage.endswith("media-downloader")
    assert model.help_url.endswith("/wiki")
    assert model.icon_url == ICON
    assert "<b>video</b>" in model.description_html
    assert "script" not in model.description_html
    assert "alert(42)" not in model.description_html


def test_wrong_id_cannot_be_used_to_install_or_display_another_app():
    with pytest.raises(FlathubMetadataError):
        parse_flathub_metadata(sample(), "org.example.Editor")
    with pytest.raises(ValueError):
        parse_flathub_metadata(sample(), "../secrets")


def test_unsafe_urls_stripped_and_unknown_fields_ignored():
    payload = sample()
    payload["icon"] = "http://127.0.0.1/private"
    payload["urls"] = {"homepage": "javascript:alert(1)", "bugtracker": "https://localhost/foo"}
    payload["screenshots"] = [{"624x351": "https://127.0.0.1/private"}, {"624x351": IMAGE}]
    model = parse_flathub_metadata(payload, APP_ID)
    assert model.icon_url is None
    assert model.homepage is None
    assert model.bugtracker is None
    assert model.screenshots == (IMAGE,)
    assert sanitize_description('<img src="https://evil.test/x" onerror="alert(1)">') == ""


def test_metadata_is_fetched_only_when_requested_and_cached(tmp_path):
    calls = []
    now = [time.time()]
    def fetch(app_id):
        calls.append(app_id)
        return json.dumps(sample()).encode()
    service = FlathubMetadataService(cache_dir=tmp_path, fetcher=fetch, clock=lambda: now[0])
    assert calls == []
    assert service.load(APP_ID).screenshots == (IMAGE,)
    assert service.load(APP_ID).screenshots == (IMAGE,)
    assert calls == [APP_ID]
    now[0] += 24 * 60 * 60 + 1
    def offline(app_id):
        calls.append("offline")
        raise FlathubMetadataError("offline")
    service.fetcher = offline
    assert service.load(APP_ID).homepage is not None
    assert calls == [APP_ID, "offline"]


def test_no_cache_fails_gracefully_and_does_not_write_invalid_json(tmp_path):
    service = FlathubMetadataService(cache_dir=tmp_path, fetcher=lambda app_id: b"not json")
    with pytest.raises(FlathubMetadataError):
        service.load(APP_ID)
    assert not list(tmp_path.glob("*.json"))


def test_screenshot_count_is_bounded():
    data = sample()
    data["screenshots"] = [
        {"624x351": f"https://dl.flathub.org/repo/screenshots/a/{n}.png"}
        for n in range(40)
    ]
    assert len(parse_flathub_metadata(data, APP_ID).screenshots) == 8


HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.skipif(not HAS_QT, reason="PySide6 unavailable")
def test_flatpak_dialog_requests_rich_metadata_only_after_show():
    from PySide6.QtWidgets import QApplication
    from src.app_store.media import MediaCache
    from src.gui.app_store.flatpak_ui import FlatpakPackageDetailsDialog

    class FakeMetadataService:
        def __init__(self):
            self.calls = []
        def load_async(self, app_id):
            self.calls.append(app_id)
            f = Future()
            f.set_result(parse_flathub_metadata(sample(), APP_ID))
            return f

    class FakeMediaCache(MediaCache):
        def __init__(self):
            self.calls = []
        def load_async(self, url, *, retry=False, executor=None):
            del retry, executor
            self.calls.append(url)
            return Future()  # Never access the network during tests.

    app = QApplication.instance() or QApplication([])
    metadata = FakeMetadataService()
    media = FakeMediaCache()
    product = Application(
        app_id=f"flatpak:system:{APP_ID}", package_name=APP_ID,
        name="Media Downloader", summary="A GUI to yt-dlp",
        metadata_source="flatpak:system", repository="Flatpak · flathub",
        installed=False, available_version="5.6.6",
    )
    dialog = FlatpakPackageDetailsDialog(product, metadata_service=metadata, media_cache=media)
    assert metadata.calls == []
    assert media.calls == []
    dialog.show()
    for _ in range(5):
        app.processEvents()
    assert metadata.calls == [APP_ID]
    assert dialog.application.homepage.endswith("media-downloader")
    assert dialog.application.publisher == "Francis Banyikwa"
    assert IMAGE in dialog._tiles
    assert IMAGE in media.calls
    assert "https://" in str(media.calls)
    assert dialog.action_button.text() == "Установить"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
