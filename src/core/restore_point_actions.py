from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


MAX_DESCRIPTION_LENGTH = 300
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class RestorePointAction(str, Enum):
    CREATE = "create"
    RENAME = "rename"
    SET_IMPORTANCE = "set-importance"
    DELETE = "delete"
    DELETE_MANY = "delete-many"


class RestorePointActionValidationError(ValueError):
    """Raised when a restore-point action contains unsafe or invalid input."""


@dataclass(frozen=True)
class RestorePointActionRequest:
    action: RestorePointAction
    point_number: int | None = None
    description: str | None = None
    important: bool | None = None
    point_numbers: tuple[int, ...] = ()

    def helper_arguments(self) -> tuple[str, ...]:
        """Return the fixed argument contract for the future privileged helper."""
        if self.action is RestorePointAction.CREATE:
            assert self.description is not None
            assert self.important is not None
            return (
                self.action.value,
                "yes" if self.important else "no",
                self.description,
            )

        if self.action is RestorePointAction.RENAME:
            assert self.point_number is not None
            assert self.description is not None
            return (
                self.action.value,
                str(self.point_number),
                self.description,
            )

        if self.action is RestorePointAction.SET_IMPORTANCE:
            assert self.point_number is not None
            assert self.important is not None
            return (
                self.action.value,
                str(self.point_number),
                "yes" if self.important else "no",
            )

        if self.action is RestorePointAction.DELETE:
            assert self.point_number is not None
            return (self.action.value, str(self.point_number))

        if self.action is RestorePointAction.DELETE_MANY:
            assert self.point_numbers
            return (self.action.value, *(str(number) for number in self.point_numbers))

        raise AssertionError(f"Unsupported restore-point action: {self.action!r}")


def validate_restore_point_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RestorePointActionValidationError(
            "restore-point number must be a positive integer"
        )
    return value


def normalize_restore_point_description(value: str) -> str:
    if not isinstance(value, str):
        raise RestorePointActionValidationError("description must be text")

    # User-facing names are deliberately single-line. Converting ordinary
    # whitespace first keeps pasted text usable while still rejecting other
    # control characters below.
    text = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())

    if not text:
        raise RestorePointActionValidationError("description must not be empty")
    if _CONTROL_RE.search(text) or "\x1b" in text:
        raise RestorePointActionValidationError("description contains control characters")
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise RestorePointActionValidationError(
            f"description must be at most {MAX_DESCRIPTION_LENGTH} characters"
        )
    return text


def build_create_request(
    description: str,
    *,
    important: bool = False,
) -> RestorePointActionRequest:
    if not isinstance(important, bool):
        raise RestorePointActionValidationError("important flag must be boolean")
    return RestorePointActionRequest(
        action=RestorePointAction.CREATE,
        description=normalize_restore_point_description(description),
        important=important,
    )


def build_rename_request(
    point_number: int,
    description: str,
) -> RestorePointActionRequest:
    return RestorePointActionRequest(
        action=RestorePointAction.RENAME,
        point_number=validate_restore_point_number(point_number),
        description=normalize_restore_point_description(description),
    )


def build_set_importance_request(
    point_number: int,
    important: bool,
) -> RestorePointActionRequest:
    if not isinstance(important, bool):
        raise RestorePointActionValidationError("important flag must be boolean")
    return RestorePointActionRequest(
        action=RestorePointAction.SET_IMPORTANCE,
        point_number=validate_restore_point_number(point_number),
        important=important,
    )


def build_delete_request(point_number: int) -> RestorePointActionRequest:
    return RestorePointActionRequest(
        action=RestorePointAction.DELETE,
        point_number=validate_restore_point_number(point_number),
    )


def build_delete_many_request(point_numbers: tuple[int, ...] | list[int]) -> RestorePointActionRequest:
    if not isinstance(point_numbers, (tuple, list)):
        raise RestorePointActionValidationError("point numbers must be a sequence")
    validated = tuple(dict.fromkeys(validate_restore_point_number(value) for value in point_numbers))
    if not validated:
        raise RestorePointActionValidationError("at least one restore point is required")
    if len(validated) > 200:
        raise RestorePointActionValidationError("too many restore points requested")
    return RestorePointActionRequest(
        action=RestorePointAction.DELETE_MANY,
        point_numbers=validated,
    )
