from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")


class PackageAction(str, Enum):
    INSTALL = "install"
    REMOVE = "remove"


class PackageActionValidationError(ValueError):
    """The requested package action is outside the App Store contract."""


@dataclass(frozen=True, slots=True)
class PackageActionRequest:
    action: PackageAction
    package_name: str

    def helper_arguments(self) -> tuple[str, str]:
        return (self.action.value, self.package_name)


def validate_package_name(value: str) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 128
        or name.startswith("-")
        or not _PACKAGE_NAME_RE.fullmatch(name)
    ):
        raise PackageActionValidationError("Некорректное имя пакета.")
    return name


def build_install_request(package_name: str) -> PackageActionRequest:
    return PackageActionRequest(PackageAction.INSTALL, validate_package_name(package_name))


def build_remove_request(package_name: str) -> PackageActionRequest:
    return PackageActionRequest(PackageAction.REMOVE, validate_package_name(package_name))
