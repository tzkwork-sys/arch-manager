from __future__ import annotations

from pathlib import Path

from src.app_store.cache import CatalogCache, _MEMORY_CATALOGS
from src.app_store.media import MediaCache
from src.app_store.models import Application


ROOT = Path(__file__).resolve().parents[1]


def _app(index: int) -> Application:
    return Application(
        app_id=f"org.demo.App{index}",
        package_name=f"demo-{index}",
        name=f"Demo {index}",
        summary=f"Summary {index}",
    )


def test_stage6_catalog_cache_keeps_in_process_memory_copy(tmp_path: Path):
    _MEMORY_CATALOGS.clear()
    path = tmp_path / "catalog.json"
    cache = CatalogCache(path)
    fingerprint = "fp-1"
    applications = (_app(1), _app(2))
    stats = {"applications_total": 2}
    cache.save(fingerprint, applications, stats)

    path.unlink()
    reloaded = CatalogCache(path).load(fingerprint)
    assert reloaded is not None
    restored_apps, restored_stats = reloaded
    assert [item.app_id for item in restored_apps] == ["org.demo.App1", "org.demo.App2"]
    assert restored_stats == stats


def test_stage6_media_cache_retry_repairs_stale_entry(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    payload = b"PNG-DATA"

    class Response:
        headers = {"Content-Type": "image/png", "Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            assert limit > len(payload)
            return payload

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return Response()

    import src.app_store.media as media_module

    monkeypatch.setattr(media_module, "urlopen", fake_urlopen)

    url = "https://example.test/broken-cache.png"
    cache = MediaCache(tmp_path / "media", timeout=1.0)
    stale_path = cache._cache_path(url)
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_bytes(b"BROKEN")

    first = cache.load(url)
    assert first.source == "disk"
    assert first.data == b"BROKEN"

    repaired = cache.load(url, retry=True)
    assert repaired.source == "network"
    assert repaired.data == payload
    assert stale_path.read_bytes() == payload
    assert calls == [url]


def test_stage6_media_cache_enforces_memory_and_disk_limits(monkeypatch, tmp_path: Path):
    chunk = 40000
    responses = {
        "https://example.test/one.png": b"A" * chunk,
        "https://example.test/two.png": b"B" * chunk,
        "https://example.test/three.png": b"C" * chunk,
    }
    calls: list[str] = []

    class Response:
        def __init__(self, url: str) -> None:
            self._data = responses[url]
            self.headers = {"Content-Type": "image/png", "Content-Length": str(len(self._data))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            assert limit > len(self._data)
            return self._data

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return Response(request.full_url)

    import src.app_store.media as media_module

    monkeypatch.setattr(media_module, "urlopen", fake_urlopen)

    cache = MediaCache(
        tmp_path / "media",
        timeout=1.0,
        max_bytes=65536,
        max_memory_bytes=65536,
        max_disk_bytes=65536,
    )

    assert cache.load("https://example.test/one.png").source == "network"
    assert cache.load("https://example.test/two.png").source == "network"
    # The first item no longer fits in memory or on disk and must be fetched again.
    assert cache.load("https://example.test/one.png").source == "network"
    # The most recent item is still cached on disk.
    assert cache.load("https://example.test/one.png").source == "memory"
    assert cache.load("https://example.test/three.png").source == "network"

    files = list((tmp_path / "media").glob("*.img"))
    assert len(files) == 1
    assert sum(path.stat().st_size for path in files) <= 65536
    assert calls == [
        "https://example.test/one.png",
        "https://example.test/two.png",
        "https://example.test/one.png",
        "https://example.test/three.png",
    ]


def test_stage6_release_guards_are_present_in_page_and_details_sources():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs/APP_STORE_STAGE6.md").read_text(encoding="utf-8")

    assert 'self.reload_button.setText("Обновляю…")' in page
    assert "archlinux-appstream-data" in page
    assert "_friendly_media_error" in details
    assert 'self._start_media_load(url, retry=True)' in details
    assert "in-process memory cache" in docs
    assert "Media cache" in docs
