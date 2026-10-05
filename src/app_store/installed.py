from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .models import Application
from .package_state import PackageState


class InstalledApplications:
    """Build the user-facing installed-applications view.

    Only AppStream applications are considered; this deliberately does not
    expose every package from ``pacman -Q``.
    """

    @staticmethod
    def filter(applications: Iterable[Application]) -> tuple[Application, ...]:
        return tuple(app for app in applications if app.installed)

    @staticmethod
    def with_states(
        applications: Iterable[Application],
        states: dict[str, PackageState],
    ) -> tuple[Application, ...]:
        result: list[Application] = []
        for app in applications:
            names = tuple(dict.fromkeys((app.package_name, *app.package_names)))
            candidates = [states[name] for name in names if name in states]
            state = next((candidate for candidate in candidates if candidate.installed), None)
            if state is None:
                continue
            result.append(
                replace(
                    app,
                    package_name=state.package_name,
                    installed=True,
                    installed_version=state.installed_version,
                    available_version=state.available_version or app.available_version,
                    update_available=state.update_available,
                    repository=state.repository or app.repository,
                )
            )
        return tuple(result)
