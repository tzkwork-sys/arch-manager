from pathlib import Path

from src.app_store.aur.cache import AurCache
from src.app_store.aur.models import AurPackage


def pkg(name="demo"):
    return AurPackage(name=name, package_base=name, version="1-1")


def test_cache_hit_expiry_and_stale_read(tmp_path: Path):
    now = [1000.0]
    cache = AurCache(tmp_path / "aur", search_ttl=10, clock=lambda: now[0])
    cache.set_search("Demo", (pkg(),))

    assert cache.get_search("demo") == (pkg(),)
    now[0] += 11
    assert cache.get_search("demo") is None
    assert cache.get_search("demo", allow_stale=True) == (pkg(),)


def test_corrupt_cache_is_removed(tmp_path: Path):
    cache = AurCache(tmp_path / "aur")
    path = cache._path("search", "demo")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    assert cache.get_search("demo") is None
    assert not path.exists()


def test_info_cache_is_separate_per_package(tmp_path: Path):
    cache = AurCache(tmp_path / "aur")
    cache.set_info((pkg("one"), pkg("two")))

    assert cache.get_info("one").name == "one"
    assert cache.get_info("two").name == "two"
