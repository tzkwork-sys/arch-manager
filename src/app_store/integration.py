from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.activity_log import append_activity, read_activity_entries
from src.core.update_state import get_update_state_service
from src.core.updates import UpdateDetails


@dataclass(frozen=True, slots=True)
class AppStoreActivity:
    timestamp: datetime
    action: str
    result: str
    application_name: str
    package_name: str


def current_update_details() -> UpdateDetails | None:
    snapshot = get_update_state_service().current()
    return snapshot.details if snapshot is not None else None


def last_app_store_activity() -> AppStoreActivity | None:
    entries = read_activity_entries(limit=1, category="app-store")
    if not entries:
        return None
    entry = entries[0]
    return AppStoreActivity(
        timestamp=entry.timestamp,
        action=entry.action,
        result=entry.result,
        application_name=str(entry.data.get("application_name", "")).strip(),
        package_name=str(entry.data.get("package_name", "")).strip(),
    )


def record_aur_bulk_update(*, result: str, message: str) -> None:
    append_activity(
        "app-store",
        "update",
        result,
        message,
        data={"application_name": "AUR", "package_name": "*", "source": "aur"},
    )


def invalidate_update_state() -> None:
    get_update_state_service().invalidate()
