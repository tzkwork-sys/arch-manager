from __future__ import annotations

from concurrent.futures import Executor, Future
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .appstream import AppStreamComponent, AppStreamReader
from .cache import CatalogCache, build_catalog_fingerprint
from .models import Application
from .package_state import PacmanPackageStateProvider, PackageState


from .background import create_background_executor

_BACKGROUND_EXECUTOR = create_background_executor(max_workers=1, thread_name_prefix="arch-manager-app-store")


@dataclass(frozen=True, slots=True)
class CatalogStats:
    metadata_files: int = 0
    components_found: int = 0
    desktop_components_found: int = 0
    applications_built: int = 0
    official_packages_linked: int = 0
    installed_applications: int = 0
    skipped_components: int = 0
    duplicate_app_ids: int = 0
    read_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CatalogStats":
        values = dict(payload)
        values["read_errors"] = tuple(values.get("read_errors") or ())
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(frozen=True, slots=True)
class CatalogLoadResult:
    applications: tuple[Application, ...]
    stats: CatalogStats
    cache_hit: bool = False


class AppCatalogService:
    """Join AppStream presentation metadata with read-only pacman package state."""

    def __init__(
        self,
        *,
        reader: AppStreamReader | None = None,
        package_state_provider: PacmanPackageStateProvider | None = None,
        cache: CatalogCache | None = None,
        pacman_local_dir: Path = Path("/var/lib/pacman/local"),
        pacman_sync_dir: Path = Path("/var/lib/pacman/sync"),
        pacman_config: Path = Path("/etc/pacman.conf"),
    ) -> None:
        self.reader = reader or AppStreamReader()
        self.package_state_provider = package_state_provider or PacmanPackageStateProvider()
        self.cache = cache or CatalogCache()
        self.pacman_local_dir = Path(pacman_local_dir)
        self.pacman_sync_dir = Path(pacman_sync_dir)
        self.pacman_config = Path(pacman_config)

    def load_catalog(self, *, use_cache: bool = True) -> CatalogLoadResult:
        metadata_files = self.reader.find_metadata_files()
        fingerprint = build_catalog_fingerprint(
            metadata_files,
            pacman_local_dir=self.pacman_local_dir,
            pacman_sync_dir=self.pacman_sync_dir,
            pacman_config=self.pacman_config,
        )
        if use_cache:
            cached = self.cache.load(fingerprint)
            if cached is not None:
                applications, stats_payload = cached
                return CatalogLoadResult(applications, CatalogStats.from_dict(stats_payload), cache_hit=True)

        read_result = self.reader.read(metadata_files)
        package_names = {
            package_name
            for component in read_result.components
            for package_name in component.package_names
        }
        package_states = (
            self.package_state_provider.collect(package_names)
            if package_names
            else {}
        )

        applications: list[Application] = []
        for component in read_result.components:
            state = self._select_package_state(component, package_states)
            if state is None or not state.available:
                continue
            applications.append(self._to_application(component, state))

        applications, duplicate_count = self._deduplicate(applications)
        applications.sort(key=lambda item: (item.name.casefold(), item.app_id.casefold(), item.package_name))
        stats = CatalogStats(
            metadata_files=len(read_result.metadata_files),
            components_found=read_result.components_seen,
            desktop_components_found=read_result.desktop_components_seen,
            applications_built=len(applications),
            official_packages_linked=len(applications),
            installed_applications=sum(1 for application in applications if application.installed),
            skipped_components=read_result.skipped_components,
            duplicate_app_ids=duplicate_count,
            read_errors=read_result.errors,
        )
        result = CatalogLoadResult(tuple(applications), stats, cache_hit=False)
        if use_cache:
            try:
                self.cache.save(fingerprint, result.applications, stats.to_dict())
            except OSError:
                # A read-only/full cache directory must never make the catalog unusable.
                pass
        return result

    def load_catalog_async(
        self,
        *,
        use_cache: bool = True,
        executor: Executor | None = None,
    ) -> Future[CatalogLoadResult]:
        """Build/load the catalog outside the GUI thread without depending on Qt."""

        pool = executor or _BACKGROUND_EXECUTOR
        return pool.submit(self.load_catalog, use_cache=use_cache)

    @staticmethod
    def _select_package_state(
        component: AppStreamComponent,
        package_states: Mapping[str, PackageState],
    ) -> PackageState | None:
        candidates = [
            package_states[package_name]
            for package_name in component.package_names
            if package_name in package_states and package_states[package_name].available
        ]
        if not candidates:
            return None
        # A component can name more than one Arch package. Prefer the package
        # actually installed on this system so Installed/Updates views cannot
        # accidentally bind the app to an unused alternative package.
        return next((state for state in candidates if state.installed), candidates[0])

    @staticmethod
    def _to_application(component: AppStreamComponent, state: PackageState) -> Application:
        issues = tuple(dict.fromkeys((*component.metadata_issues, *state.issues)))
        return Application(
            app_id=component.app_id,
            package_name=state.package_name,
            name=component.name,
            package_names=component.package_names,
            summary=component.summary,
            description=component.description,
            description_html=component.description_html,
            categories=component.categories,
            raw_categories=component.raw_categories,
            keywords=component.keywords,
            icon=component.icon,
            icon_type=component.icon_type,
            screenshots=component.screenshots,
            homepage=component.homepage,
            bugtracker=component.bugtracker,
            help_url=component.help_url,
            license=component.license,
            repository=state.repository or component.source_repository,
            available_version=state.available_version,
            installed_version=state.installed_version,
            installed=state.installed,
            update_available=state.update_available,
            installed_state_known=state.installed_state_known,
            download_size=state.download_size,
            installed_size=state.installed_size,
            desktop_entry=component.desktop_entry,
            launchable_binary=component.launchable_binary,
            metadata_complete=component.metadata_complete and state.metadata_complete,
            metadata_issues=issues,
            metadata_source=component.source_file,
            publisher=component.publisher,
        )

    @staticmethod
    def _deduplicate(applications: Iterable[Application]) -> tuple[list[Application], int]:
        by_id: dict[str, Application] = {}
        duplicates = 0
        for application in applications:
            key = application.app_id.casefold()
            previous = by_id.get(key)
            if previous is None:
                by_id[key] = application
                continue
            duplicates += 1
            if AppCatalogService._richness(application) > AppCatalogService._richness(previous):
                by_id[key] = application
        return list(by_id.values()), duplicates

    @staticmethod
    def _richness(application: Application) -> tuple[int, int, int]:
        populated = sum(
            value not in {None, "", (), False}
            for value in (
                application.summary,
                application.description,
                application.description_html,
                application.icon,
                application.screenshots,
                application.homepage,
                application.bugtracker,
                application.help_url,
                application.license,
                application.desktop_entry,
                application.launchable_binary,
            )
        )
        return (1 if application.metadata_complete else 0, populated, -len(application.metadata_issues))
