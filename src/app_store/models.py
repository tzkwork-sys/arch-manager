from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Application:
    """Toolkit-independent application record exposed to the GUI layer."""

    app_id: str
    package_name: str
    name: str
    package_names: tuple[str, ...] = ()
    summary: str = ""
    description: str = ""
    description_html: str = ""
    categories: tuple[str, ...] = ()
    raw_categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    icon: str | None = None
    icon_type: str | None = None
    screenshots: tuple[str, ...] = ()
    homepage: str | None = None
    bugtracker: str | None = None
    help_url: str | None = None
    license: str | None = None
    repository: str | None = None
    available_version: str | None = None
    installed_version: str | None = None
    installed: bool = False
    update_available: bool = False
    download_size: int | None = None
    installed_size: int | None = None
    desktop_entry: str | None = None
    launchable_binary: str | None = None
    metadata_complete: bool = True
    metadata_issues: tuple[str, ...] = ()
    metadata_source: str | None = None
    dependencies_text: str = ""
    optional_dependencies_text: str = ""
    required_by_text: str = ""
    optional_for_text: str = ""
    provides_text: str = ""
    conflicts_text: str = ""
    publisher: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Application":
        allowed = {field.name for field in fields(cls)}
        values = {key: value for key, value in payload.items() if key in allowed}
        for key in (
            "package_names",
            "categories",
            "raw_categories",
            "keywords",
            "screenshots",
            "metadata_issues",
        ):
            if key in values:
                values[key] = tuple(values[key] or ())
        return cls(**values)
