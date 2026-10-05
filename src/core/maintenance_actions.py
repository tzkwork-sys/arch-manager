from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .maintenance import (
    DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS,
    validate_cache_keep_versions,
)


class MaintenanceAction(str, Enum):
    PACKAGE_CACHE = "package-cache"
    ORPHANS = "orphans"
    JOURNAL = "journal"
    THUMBNAILS = "thumbnails"
    TRASH = "trash"


PRIVILEGED_ACTIONS = frozenset(
    {
        MaintenanceAction.PACKAGE_CACHE,
        MaintenanceAction.ORPHANS,
        MaintenanceAction.JOURNAL,
    }
)
USER_ACTIONS = frozenset({MaintenanceAction.THUMBNAILS, MaintenanceAction.TRASH})
_ACTION_ORDER = (
    MaintenanceAction.PACKAGE_CACHE,
    MaintenanceAction.ORPHANS,
    MaintenanceAction.JOURNAL,
    MaintenanceAction.THUMBNAILS,
    MaintenanceAction.TRASH,
)


class MaintenanceActionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MaintenanceCleanupRequest:
    actions: tuple[MaintenanceAction, ...]
    cache_keep_versions: int = DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS

    @property
    def privileged_actions(self) -> tuple[MaintenanceAction, ...]:
        return tuple(action for action in self.actions if action in PRIVILEGED_ACTIONS)

    @property
    def user_actions(self) -> tuple[MaintenanceAction, ...]:
        return tuple(action for action in self.actions if action in USER_ACTIONS)


def build_cleanup_request(
    actions: Iterable[MaintenanceAction | str],
    *,
    cache_keep_versions: int = DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS,
) -> MaintenanceCleanupRequest:
    normalized: set[MaintenanceAction] = set()
    for value in actions:
        try:
            action = value if isinstance(value, MaintenanceAction) else MaintenanceAction(value)
        except (TypeError, ValueError) as exc:
            raise MaintenanceActionValidationError("unsupported maintenance action") from exc
        normalized.add(action)

    if not normalized:
        raise MaintenanceActionValidationError("at least one maintenance action is required")

    try:
        cache_keep_versions = validate_cache_keep_versions(cache_keep_versions)
    except ValueError as exc:
        raise MaintenanceActionValidationError(str(exc)) from exc

    return MaintenanceCleanupRequest(
        tuple(action for action in _ACTION_ORDER if action in normalized),
        cache_keep_versions=cache_keep_versions,
    )
