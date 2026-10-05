from __future__ import annotations

from collections.abc import Iterable

from src.app_store.aur.models import AurPackage
from src.app_store.models import Application
from src.app_store.search import search_candidate_rank, search_matches


StoreSearchResult = Application | AurPackage


def merge_store_results(
    official: Iterable[Application],
    aur: Iterable[AurPackage],
    *,
    source: str,
    local: Iterable[Application] = (),
) -> tuple[StoreSearchResult, ...]:
    """Merge source-specific results only at the GUI presentation boundary.

    Core official catalog objects and AUR objects remain unrelated models.  The
    mixed list exists only long enough to render the current search result.
    """

    official_items = tuple(official)
    aur_items = tuple(aur)
    local_items = tuple(local)
    if source == "official":
        return official_items
    if source == "aur":
        return aur_items
    if source == "local":
        return local_items
    if source != "all":
        raise ValueError(f"unsupported application source: {source}")

    combined: list[StoreSearchResult] = [*official_items, *aur_items, *local_items]
    combined.sort(
        key=lambda item: (
            item.name.casefold(),
            0 if isinstance(item, Application) else 1,
        )
    )
    return tuple(combined)

def prioritize_exact_aur_match(
    packages: Iterable[AurPackage],
    query: str,
) -> tuple[AurPackage, ...]:
    """Promote an exact package-name hit without disturbing aurweb order."""

    items = tuple(packages)
    if not (query or "").strip():
        return items
    exact = [
        item
        for item in items
        if search_candidate_rank((item.name, item.package_base), query) == 0
    ]
    if not exact:
        return items
    exact_ids = {id(item) for item in exact}
    return tuple(exact + [item for item in items if id(item) not in exact_ids])

def filter_installed_aur_packages(
    packages: Iterable[AurPackage],
    query: str,
) -> tuple[AurPackage, ...]:
    """Filter already-confirmed installed AUR packages locally.

    The installed view must never trigger broad AUR searches.  Its package set
    comes from ``AurService.installed()`` and this helper only applies the text
    search to that confirmed local snapshot.
    """

    items = tuple(package for package in packages if package.installed)
    if not (query or "").strip():
        return items

    return tuple(
        package
        for package in items
        if search_matches(
            (
                package.name,
                package.package_base,
                package.description,
                package.maintainer or "",
                *package.keywords,
            ),
            query,
        )
    )

