from concurrent.futures import ThreadPoolExecutor

from src.app_store.aur.models import AurPackage, ForeignPackage
from src.app_store.aur.service import AurService
from src.core.command import CommandResult


class MemoryCache:
    def __init__(self):
        self.searches = {}
        self.infos = {}

    def get_search(self, query):
        return self.searches.get(query.casefold())

    def set_search(self, query, packages):
        self.searches[query.casefold()] = tuple(packages)

    def get_info(self, name):
        return self.infos.get(name)

    def set_info(self, packages):
        for package in packages:
            self.infos[package.name] = package


class Rpc:
    def __init__(self):
        self.search_calls = []
        self.info_calls = []

    def search(self, term):
        self.search_calls.append(term)
        return (AurPackage(name="demo-git", package_base="demo-git", version="2.0-1"),)

    def info(self, names):
        names = tuple(names)
        self.info_calls.append(names)
        return tuple(
            AurPackage(name=name, package_base=name, version="2.0-1")
            for name in names
            if name != "local-only"
        )


class ForeignReader:
    def read(self):
        return (
            ForeignPackage("demo-git", "1.0-1"),
            ForeignPackage("local-only", "9-1"),
        )


class Probe:
    def probe(self):
        return "capabilities"


def vercmp_runner(args, **kwargs):
    return CommandResult(tuple(args), 0, "-1\n", "")


def test_service_search_cache_does_not_mix_with_official_catalog():
    rpc = Rpc()
    cache = MemoryCache()
    service = AurService(rpc=rpc, cache=cache, foreign_reader=ForeignReader(), availability_probe=Probe())

    assert service.search("demo")[0].name == "demo-git"
    assert service.search("demo")[0].name == "demo-git"
    assert rpc.search_calls == ["demo"]




def test_search_marks_matching_foreign_package_as_installed_without_polluting_cache():
    rpc = Rpc()
    cache = MemoryCache()
    service = AurService(
        rpc=rpc,
        cache=cache,
        foreign_reader=ForeignReader(),
        availability_probe=Probe(),
        version_runner=vercmp_runner,
    )

    first = service.search("demo")
    second = service.search("demo")

    assert first[0].installed is True
    assert first[0].installed_version == "1.0-1"
    assert first[0].update_available is True
    assert second[0].installed is True
    assert rpc.search_calls == ["demo"]
    # Search cache stores remote metadata only; local state is re-applied after reads.
    assert cache.searches["demo"][0].installed is False


def test_search_local_state_probe_failure_does_not_break_remote_results():
    class BrokenForeignReader:
        def read(self):
            from src.app_store.aur.errors import AurUnavailable

            raise AurUnavailable("pacman unavailable")

    service = AurService(
        rpc=Rpc(),
        cache=MemoryCache(),
        foreign_reader=BrokenForeignReader(),
        availability_probe=Probe(),
    )

    result = service.search("demo", use_cache=False)

    assert result[0].name == "demo-git"
    assert result[0].installed is False
    assert result[0].local_state_known is False

def test_foreign_package_requires_aur_info_confirmation():
    rpc = Rpc()
    service = AurService(
        rpc=rpc,
        cache=MemoryCache(),
        foreign_reader=ForeignReader(),
        availability_probe=Probe(),
        version_runner=vercmp_runner,
    )

    snapshot = service.installed(use_cache=False)

    assert [package.name for package in snapshot.aur_packages] == ["demo-git"]
    assert snapshot.aur_packages[0].installed is True
    assert snapshot.aur_packages[0].update_available is True
    assert [package.name for package in snapshot.foreign_unknown] == ["local-only"]


def test_service_exposes_async_read_only_api():
    service = AurService(rpc=Rpc(), cache=MemoryCache(), foreign_reader=ForeignReader(), availability_probe=Probe())
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = service.search_async("demo", executor=executor)
        assert future.result(timeout=2)[0].name == "demo-git"

    assert not hasattr(service, "install")
    assert not hasattr(service, "remove")
    assert not hasattr(service, "update")


def test_search_result_is_not_promoted_to_full_info_cache():
    rpc = Rpc()
    cache = MemoryCache()
    service = AurService(rpc=rpc, cache=cache, foreign_reader=ForeignReader(), availability_probe=Probe())

    service.search("demo")
    result = service.info(["demo-git"])

    assert result[0].name == "demo-git"
    assert rpc.info_calls == [("demo-git",)]


class EmptyForeignReader:
    def read(self):
        return ()


class OtherForeignReader:
    def read(self):
        return (ForeignPackage("another-aur-package", "1.0-1"),)


def test_refresh_local_state_clears_stale_installed_flag_after_last_foreign_package_is_removed():
    service = AurService(
        rpc=Rpc(),
        cache=MemoryCache(),
        foreign_reader=EmptyForeignReader(),
        availability_probe=Probe(),
    )
    stale = AurPackage(
        name="tetris-terminal-git",
        package_base="tetris-terminal-git",
        version="2.0-1",
        installed=True,
        installed_version="1.0-1",
        update_available=True,
    )

    refreshed = service.refresh_local_state(stale)

    assert refreshed.installed is False
    assert refreshed.installed_version is None
    assert refreshed.update_available is False


def test_refresh_local_state_clears_stale_flag_when_other_foreign_packages_remain():
    service = AurService(
        rpc=Rpc(),
        cache=MemoryCache(),
        foreign_reader=OtherForeignReader(),
        availability_probe=Probe(),
    )
    stale = AurPackage(
        name="tetris-terminal-git",
        package_base="tetris-terminal-git",
        version="2.0-1",
        installed=True,
        installed_version="1.0-1",
    )

    refreshed = service.refresh_local_state(stale)

    assert refreshed.installed is False
    assert refreshed.installed_version is None



def test_search_expands_human_yandex_disk_query_and_merges_aur_results():
    class YandexRpc:
        def __init__(self):
            self.search_calls = []

        def search(self, term):
            self.search_calls.append(term)
            if term == "yandex-disk":
                return (
                    AurPackage(
                        name="yandex-disk",
                        package_base="yandex-disk",
                        version="0.1.6.1080-2",
                        description="Yandex.Disk keeps your files with you",
                    ),
                )
            if term == "yandex disk":
                return (
                    AurPackage(
                        name="ydisk_commander",
                        package_base="ydisk_commander",
                        version="1.0-2",
                        description="Yandex Disk plugin for Double Commander",
                    ),
                )
            return ()

        def info(self, names):
            return ()

    class NoForeignPackages:
        def read(self):
            return ()

    rpc = YandexRpc()
    service = AurService(
        rpc=rpc,
        cache=MemoryCache(),
        foreign_reader=NoForeignPackages(),
        availability_probe=Probe(),
    )

    result = service.search("Яндекс диск", use_cache=False)

    assert rpc.search_calls == ["yandex-disk", "yandex_disk", "yandex disk", "yandexdisk"]
    assert [package.name for package in result] == ["yandex-disk", "ydisk_commander"]
