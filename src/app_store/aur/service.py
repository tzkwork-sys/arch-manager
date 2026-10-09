from __future__ import annotations

from concurrent.futures import Executor, Future
from collections.abc import Callable, Iterable

from src.core.command import CommandResult, run_command

from .availability import AurAvailabilityProbe, AurCapabilityReport
from .cache import AurCache
from .errors import AurError
from .local_state import ForeignPackageReader
from .models import AurInstalledSnapshot, AurPackage, ForeignPackage
from .rpc import AurRpcClient
from src.app_store.search import aur_search_queries, search_candidate_rank
from .versioning import update_available


from ..background import create_background_executor

_AUR_EXECUTOR = create_background_executor(max_workers=3, thread_name_prefix="arch-manager-aur")


class AurService:
    """Read-only AUR facade used by later GUI stages.

    Stage 7.1 deliberately exposes no install/remove/update methods.
    """

    def __init__(
        self,
        *,
        rpc: AurRpcClient | None = None,
        cache: AurCache | None = None,
        foreign_reader: ForeignPackageReader | None = None,
        availability_probe: AurAvailabilityProbe | None = None,
        version_runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.rpc = rpc or AurRpcClient()
        self.cache = cache or AurCache()
        self.foreign_reader = foreign_reader or ForeignPackageReader()
        self.availability_probe = availability_probe or AurAvailabilityProbe()
        self.version_runner = version_runner

    def search(self, query: str, *, use_cache: bool = True) -> tuple[AurPackage, ...]:
        term = (query or "").strip()
        if len(term) < 2:
            raise ValueError("AUR search query must contain at least 2 characters")
        packages: tuple[AurPackage, ...]
        if use_cache:
            cached = self.cache.get_search(term)
            if cached is not None:
                packages = tuple(cached)
            else:
                packages = self._search_remote_variants(term)
                try:
                    # Cache only the merged remote metadata for the original user
                    # query. Installed/update state is local and must be refreshed
                    # on every search, including cache hits.
                    self.cache.set_search(term, packages)
                except OSError:
                    pass
        else:
            packages = self._search_remote_variants(term)
        return self._decorate_search_with_local_state(packages)

    def _search_remote_variants(self, term: str) -> tuple[AurPackage, ...]:
        queries = aur_search_queries(term) or (term,)
        merged: dict[str, AurPackage] = {}
        first_error: Exception | None = None
        for query in queries:
            try:
                found = self.rpc.search(query)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                continue
            for package in found:
                merged.setdefault(package.name, package)

        if not merged and first_error is not None:
            raise first_error

        indexed = list(enumerate(merged.values()))
        indexed.sort(
            key=lambda pair: (
                search_candidate_rank((pair[1].name, pair[1].package_base), term),
                pair[0],
            )
        )
        return tuple(package for _index, package in indexed)

    def _decorate_search_with_local_state(
        self,
        packages: Iterable[AurPackage],
    ) -> tuple[AurPackage, ...]:
        """Add best-effort local state to already confirmed AUR search hits.

        A package returned by aurweb is already confirmed as an AUR package, so an
        exact name match from ``pacman -Qm`` is enough to mark that search card as
        installed. Local probing is intentionally non-fatal: missing pacman/vercmp
        must never turn a successful network search into an AUR search failure.
        """

        items = tuple(packages)
        if not items:
            return items
        try:
            foreign = self.foreign_reader.read()
        except AurError:
            return tuple(package.with_unknown_local_state() for package in items)

        installed_versions = {item.name: item.installed_version for item in foreign}

        decorated: list[AurPackage] = []
        for package in items:
            installed_version = installed_versions.get(package.name)
            if installed_version is None:
                # ``package`` can itself carry stale local state (for example a
                # details dialog object created before an interactive removal).
                # Local pacman state is authoritative: an unmatched AUR package
                # must be explicitly reset instead of returned unchanged.
                decorated.append(
                    package.with_local_state(
                        installed_version=None,
                        update_available=False,
                    )
                )
                continue
            try:
                has_update = update_available(
                    installed_version,
                    package.version,
                    runner=self.version_runner,
                )
            except (AurError, ValueError):
                has_update = False
            decorated.append(
                package.with_local_state(
                    installed_version=installed_version,
                    update_available=has_update,
                )
            )
        return tuple(decorated)

    def info(self, package_names: Iterable[str], *, use_cache: bool = True) -> tuple[AurPackage, ...]:
        names = tuple(dict.fromkeys(name.strip() for name in package_names if name and name.strip()))
        if not names:
            return ()
        found: dict[str, AurPackage] = {}
        missing: list[str] = []
        for name in names:
            cached = self.cache.get_info(name) if use_cache else None
            if cached is None:
                missing.append(name)
            else:
                found[name] = cached
        if missing:
            fresh = self.rpc.info(missing)
            found.update((package.name, package) for package in fresh)
            if use_cache:
                try:
                    self.cache.set_info(fresh)
                except OSError:
                    pass
        return tuple(found[name] for name in names if name in found)

    def installed(self, *, use_cache: bool = True) -> AurInstalledSnapshot:
        foreign = self.foreign_reader.read()
        if not foreign:
            return AurInstalledSnapshot()
        confirmed = {package.name: package for package in self.info((item.name for item in foreign), use_cache=use_cache)}
        aur_packages: list[AurPackage] = []
        unknown: list[ForeignPackage] = []
        for local in foreign:
            package = confirmed.get(local.name)
            if package is None:
                unknown.append(local)
                continue
            is_update = update_available(
                local.installed_version,
                package.version,
                runner=self.version_runner,
            )
            aur_packages.append(
                package.with_local_state(
                    installed_version=local.installed_version,
                    update_available=is_update,
                )
            )
        return AurInstalledSnapshot(tuple(aur_packages), tuple(unknown))

    def refresh_local_state(self, package: AurPackage) -> AurPackage:
        """Refresh only local installed/update state for one confirmed AUR package.

        No network request is performed.  This is used after an interactive
        terminal transaction so the GUI can reflect the real pacman state even
        when aurweb is temporarily unavailable.
        """

        if not isinstance(package, AurPackage):
            raise TypeError("package must be AurPackage")
        return self._decorate_search_with_local_state((package,))[0]

    def refresh_local_state_async(
        self,
        package: AurPackage,
        *,
        executor: Executor | None = None,
    ) -> Future[AurPackage]:
        return (executor or _AUR_EXECUTOR).submit(self.refresh_local_state, package)

    def capabilities(self) -> AurCapabilityReport:
        return self.availability_probe.probe()

    def search_async(
        self,
        query: str,
        *,
        use_cache: bool = True,
        executor: Executor | None = None,
    ) -> Future[tuple[AurPackage, ...]]:
        return (executor or _AUR_EXECUTOR).submit(self.search, query, use_cache=use_cache)

    def info_async(
        self,
        package_names: Iterable[str],
        *,
        use_cache: bool = True,
        executor: Executor | None = None,
    ) -> Future[tuple[AurPackage, ...]]:
        names = tuple(package_names)
        return (executor or _AUR_EXECUTOR).submit(self.info, names, use_cache=use_cache)

    def installed_async(
        self,
        *,
        use_cache: bool = True,
        executor: Executor | None = None,
    ) -> Future[AurInstalledSnapshot]:
        return (executor or _AUR_EXECUTOR).submit(self.installed, use_cache=use_cache)

    def capabilities_async(
        self,
        *,
        executor: Executor | None = None,
    ) -> Future[AurCapabilityReport]:
        return (executor or _AUR_EXECUTOR).submit(self.capabilities)
