from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from src.core.updates import UpdateItem

from .models import Application


class ApplicationUpdateMapper:
    """Map the shared official package-update state to AppStream applications.

    This module is intentionally read-only. It never owns an update command;
    the existing Arch Manager full-system update flow remains the only update
    action exposed by the Apps page.
    """


    @staticmethod
    def synchronize(
        applications: Iterable[Application],
        updates: Iterable[UpdateItem],
    ) -> tuple[Application, ...]:
        """Overlay one known official update snapshot onto the full catalog."""

        by_package = {
            item.name: item
            for item in updates
            if item.source == "official"
        }
        synchronized: list[Application] = []
        for app in applications:
            if not app.installed:
                synchronized.append(app)
                continue
            package_names = tuple(dict.fromkeys((app.package_name, *app.package_names)))
            match = next((by_package[name] for name in package_names if name in by_package), None)
            if match is None:
                synchronized.append(replace(app, update_available=False))
                continue
            synchronized.append(
                replace(
                    app,
                    package_name=match.name,
                    installed_version=(
                        match.current_version
                        if match.current_version != "—"
                        else app.installed_version
                    ),
                    available_version=(
                        match.new_version
                        if match.new_version != "—"
                        else app.available_version
                    ),
                    update_available=True,
                )
            )
        return tuple(synchronized)

    @staticmethod
    def map_updates(
        applications: Iterable[Application],
        updates: Iterable[UpdateItem] | None = None,
    ) -> tuple[Application, ...]:
        apps = tuple(applications)
        if updates is None:
            return tuple(app for app in apps if app.installed and app.update_available)

        by_package = {
            item.name: item
            for item in updates
            if item.source == "official"
        }
        mapped: list[Application] = []
        seen_ids: set[str] = set()
        for app in apps:
            if not app.installed:
                continue
            package_names = tuple(dict.fromkeys((app.package_name, *app.package_names)))
            match = next((by_package[name] for name in package_names if name in by_package), None)
            if match is None:
                continue
            key = app.app_id.casefold()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            mapped.append(
                replace(
                    app,
                    package_name=match.name,
                    installed=True,
                    installed_version=(
                        match.current_version
                        if match.current_version != "—"
                        else app.installed_version
                    ),
                    available_version=(
                        match.new_version
                        if match.new_version != "—"
                        else app.available_version
                    ),
                    update_available=True,
                )
            )
        return tuple(mapped)
