from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Callable

from .updates import (
    UpdateDetails,
    UpdateItem,
    UpdatesSummary,
    collect_update_details,
    summary_from_details,
)


@dataclass(frozen=True, slots=True)
class UpdateStateSnapshot:
    """Immutable shared view of the latest system update check."""

    details: UpdateDetails
    generation: int

    @property
    def summary(self) -> UpdatesSummary:
        return summary_from_details(self.details)

    @property
    def official_items(self) -> tuple[UpdateItem, ...]:
        return self.details.official.items


class UpdateStateService:
    """Single owner of update availability used by Overview, Updates and Apps.

    ``refresh`` is synchronous by design; GUI callers already invoke it from
    worker threads. Concurrent callers are coalesced so a dashboard refresh and
    an Apps/Updates refresh cannot start independent package checks and publish
    contradictory states.
    """

    def __init__(self, collector: Callable[[], UpdateDetails] = collect_update_details) -> None:
        self._collector = collector
        self._condition = threading.Condition()
        self._snapshot: UpdateStateSnapshot | None = None
        self._refreshing = False
        self._generation = 0
        self._last_error: BaseException | None = None

    def current(self) -> UpdateStateSnapshot | None:
        with self._condition:
            return self._snapshot

    def current_details(self) -> UpdateDetails | None:
        snapshot = self.current()
        return snapshot.details if snapshot is not None else None

    def current_summary(self) -> UpdatesSummary | None:
        snapshot = self.current()
        return snapshot.summary if snapshot is not None else None

    def refresh(self, *, force: bool = True) -> UpdateStateSnapshot:
        """Refresh the shared state, coalescing concurrent checks.

        ``force=False`` returns the already published snapshot when available.
        It is useful for consumers that only need a coherent view and should not
        cause another network/package check.
        """

        with self._condition:
            if not force and self._snapshot is not None:
                return self._snapshot
            if self._refreshing:
                while self._refreshing:
                    self._condition.wait()
                if self._snapshot is not None:
                    return self._snapshot
                if self._last_error is not None:
                    raise RuntimeError("Не удалось обновить общее состояние обновлений.") from self._last_error
            self._refreshing = True
            self._last_error = None

        try:
            details = self._collector()
        except BaseException as exc:
            with self._condition:
                self._last_error = exc
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._generation += 1
            self._snapshot = UpdateStateSnapshot(details, self._generation)
            self._refreshing = False
            self._last_error = None
            self._condition.notify_all()
            return self._snapshot

    def publish(self, details: UpdateDetails) -> UpdateStateSnapshot:
        """Publish already-collected details (primarily for integration/tests)."""

        with self._condition:
            self._generation += 1
            self._snapshot = UpdateStateSnapshot(details, self._generation)
            self._last_error = None
            self._condition.notify_all()
            return self._snapshot

    def invalidate(self) -> None:
        """Mark cached update state stale after a package-changing operation."""

        with self._condition:
            self._snapshot = None
            self._last_error = None
            self._condition.notify_all()


_DEFAULT_UPDATE_STATE = UpdateStateService()


def get_update_state_service() -> UpdateStateService:
    return _DEFAULT_UPDATE_STATE
