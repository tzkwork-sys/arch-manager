from __future__ import annotations

from concurrent.futures import Future
import importlib.util
import io
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from src.app_store.catalog import CatalogLoadResult, CatalogStats
from src.app_store.models import Application
from src.app_store.popularity import (
    PkgstatsPopularityClient,
    PkgstatsPopularityService,
    PopularityCache,
    PopularityNetworkError,
    PopularitySnapshot,
)

HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_pkgstats_client_paginates_and_validates_rows():
    calls: list[int] = []

    def opener(request, *, timeout):
        assert timeout >= 0.5
        query = parse_qs(urlparse(request.full_url).query)
        offset = int(query["offset"][0])
        calls.append(offset)
        rows = [
            {
                "name": "firefox",
                "popularity": 69.1,
                "count": 691,
                "samples": 1000,
                "startMonth": 202509,
                "endMonth": 202609,
            },
            {
                "name": "chromium",
                "popularity": 40.0,
                "count": 400,
                "samples": 1000,
                "startMonth": 202509,
                "endMonth": 202609,
            },
            {
                "name": "google-chrome",
                "popularity": 17.0,
                "count": 170,
                "samples": 1000,
                "startMonth": 202509,
                "endMonth": 202609,
            },
        ]
        page = rows[offset : offset + 2]
        payload = {
            "count": len(page),
            "limit": 2,
            "offset": offset,
            "packagePopularities": page,
            "query": None,
            "total": len(rows),
        }
        return _Response(json.dumps(payload).encode("utf-8"))

    snapshot = PkgstatsPopularityClient(page_limit=2, opener=opener).fetch_all()

    assert calls == [0, 2]
    assert snapshot.scores["firefox"] == pytest.approx(69.1)
    assert snapshot.scores["google-chrome"] == pytest.approx(17.0)
    assert snapshot.counts["chromium"] == 400
    assert snapshot.start_month == 202509
    assert snapshot.end_month == 202609


def test_popularity_service_uses_daily_cache_and_stale_fallback(tmp_path):
    now = [1_000.0]
    cache = PopularityCache(tmp_path / "popularity.json", ttl=100.0, clock=lambda: now[0])

    class Client:
        def __init__(self):
            self.calls = 0
            self.fail = False

        def fetch_all(self):
            self.calls += 1
            if self.fail:
                raise PopularityNetworkError("offline")
            return PopularitySnapshot.build({"firefox": 50.0}, {"firefox": 500})

    client = Client()
    service = PkgstatsPopularityService(client=client, cache=cache)

    first = service.load()
    second = service.load()
    assert client.calls == 1
    assert not first.cache_hit
    assert second.cache_hit
    assert second.scores["firefox"] == 50.0

    now[0] += 101.0
    client.fail = True
    stale = service.load()
    assert client.calls == 2
    assert stale.cache_hit
    assert stale.stale_cache
    assert stale.scores["firefox"] == 50.0


class _CatalogService:
    def load_catalog_async(self, *, use_cache: bool = True):
        del use_cache
        future: Future[CatalogLoadResult] = Future()
        future.set_result(
            CatalogLoadResult(
                applications=(
                    Application(
                        app_id="org.example.Alpha",
                        package_name="alpha",
                        name="Alpha",
                        categories=("utility",),
                        repository="extra",
                    ),
                    Application(
                        app_id="org.example.Zulu",
                        package_name="zulu",
                        name="Zulu",
                        categories=("utility",),
                        repository="extra",
                    ),
                ),
                stats=CatalogStats(metadata_files=1, applications_built=2),
                cache_hit=True,
            )
        )
        return future


class _PopularityService:
    def load_async(self, *, use_cache: bool = True):
        del use_cache
        future: Future[PopularitySnapshot] = Future()
        future.set_result(
            PopularitySnapshot.build(
                {"alpha": 2.0, "zulu": 80.0},
                {"alpha": 20, "zulu": 800},
                cache_hit=True,
            )
        )
        return future


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_popular_sidebar_and_sort_combo_sort_official_apps_by_pkgstats():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage(
        service=_CatalogService(),
        popularity_service=_PopularityService(),
    )
    selections: list[tuple[str, object]] = []
    page.sidebar_selection_changed.connect(lambda entry, category: selections.append((entry, category)))

    page.ensure_loaded()
    app.processEvents()
    assert page.sort_combo.currentData() == "popularity"
    assert [item.name for item in page._filtered] == ["Zulu", "Alpha"]
    assert page.sidebar_selection() == ("catalog", None)

    page.select_sidebar_entry("popular")
    app.processEvents()

    assert page.sort_combo.currentData() == "popularity"
    assert [item.name for item in page._filtered] == ["Zulu", "Alpha"]
    assert selections[-1] == ("popular", None)
    assert page.popularity_status_label.text() == "Популярность: pkgstats"

    page.select_sidebar_entry("catalog")
    assert page.sort_combo.currentData() == "popularity"
    assert [item.name for item in page._filtered] == ["Zulu", "Alpha"]
    assert page.sidebar_selection() == ("catalog", None)
    assert selections[-1] == ("catalog", None)
    page.deleteLater()


def test_main_window_popular_sidebar_source_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/gui/main_window.py").read_text(encoding="utf-8")
    assert '"Популярные", "popular"' in source
    assert 'entry not in {"catalog", "popular", "installed", "updates", "system-packages", "category"}' in source
