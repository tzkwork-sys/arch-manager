from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Mapping


_TUPLE_FIELDS = (
    "depends",
    "make_depends",
    "opt_depends",
    "check_depends",
    "conflicts",
    "provides",
    "replaces",
    "licenses",
    "keywords",
)


@dataclass(frozen=True, slots=True)
class AurPackage:
    """Toolkit-independent AUR package metadata and optional local state."""

    name: str
    package_base: str
    version: str
    description: str = ""
    upstream_url: str | None = None
    aur_url: str | None = None
    maintainer: str | None = None
    votes: int = 0
    popularity: float = 0.0
    out_of_date: int | None = None
    first_submitted: int | None = None
    last_modified: int | None = None
    depends: tuple[str, ...] = ()
    make_depends: tuple[str, ...] = ()
    opt_depends: tuple[str, ...] = ()
    check_depends: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()
    licenses: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    installed: bool = False
    installed_version: str | None = None
    update_available: bool = False
    aur_confirmed: bool = True
    foreign_unknown: bool = False
    local_state_known: bool = True

    @property
    def orphaned(self) -> bool:
        return not bool(self.maintainer)

    def with_local_state(
        self,
        *,
        installed_version: str | None,
        update_available: bool = False,
    ) -> "AurPackage":
        return replace(
            self,
            installed=installed_version is not None,
            installed_version=installed_version,
            update_available=bool(update_available),
            local_state_known=True,
        )

    def with_unknown_local_state(self) -> "AurPackage":
        return replace(
            self,
            installed=False,
            installed_version=None,
            update_available=False,
            local_state_known=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AurPackage":
        allowed = {field.name for field in fields(cls)}
        values = {key: value for key, value in payload.items() if key in allowed}
        for key in _TUPLE_FIELDS:
            if key in values:
                values[key] = tuple(values[key] or ())
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ForeignPackage:
    """A package reported by pacman -Qm before AUR confirmation."""

    name: str
    installed_version: str


@dataclass(frozen=True, slots=True)
class AurInstalledSnapshot:
    """Confirmed AUR packages plus foreign packages that AUR did not confirm."""

    aur_packages: tuple[AurPackage, ...] = ()
    foreign_unknown: tuple[ForeignPackage, ...] = ()
