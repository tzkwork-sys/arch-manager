from __future__ import annotations

from dataclasses import dataclass

from .models import Application
from .search import search_matches


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """Pure read-only filters used by the Qt page and unit tests."""

    text: str = ""
    category: str | None = None
    installed_only: bool = False


class ApplicationIndex:
    """Small in-memory search index for the already-built AppStream catalog.

    The expensive catalog build remains in :mod:`src.app_store.catalog`.  This
    class only performs local filtering over immutable ``Application`` values,
    so typing in the search field never invokes pacman or reparses AppStream.
    """

    def __init__(self, applications: tuple[Application, ...] | list[Application] = ()) -> None:
        self._applications: tuple[Application, ...] = ()
        self._search_text: dict[str, str] = {}
        self.set_applications(applications)

    @property
    def applications(self) -> tuple[Application, ...]:
        return self._applications

    def set_applications(self, applications: tuple[Application, ...] | list[Application]) -> None:
        self._applications = tuple(applications)
        self._search_text = {
            application.app_id: self._build_search_text(application)
            for application in self._applications
        }

    def filter(self, query: CatalogQuery) -> tuple[Application, ...]:
        category = query.category or None
        result: list[Application] = []
        for application in self._applications:
            if query.installed_only and not application.installed:
                continue
            if category and category not in application.categories:
                continue
            if query.text:
                haystack = self._search_text.get(application.app_id, "")
                if not search_matches((haystack,), query.text):
                    continue
            result.append(application)
        return tuple(result)

    def available_categories(self) -> tuple[str, ...]:
        categories = {category for app in self._applications for category in app.categories}
        return tuple(sorted(categories))

    @staticmethod
    def _build_search_text(application: Application) -> str:
        values = (
            application.name,
            application.package_name,
            application.app_id,
            application.summary,
            application.publisher or "",
            *application.keywords,
        )
        return "\n".join(value.casefold() for value in values if value)
