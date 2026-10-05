from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess

from .restore_point_actions import (
    RestorePointAction,
    RestorePointActionRequest,
    RestorePointActionValidationError,
    build_create_request,
    build_delete_request,
    build_delete_many_request,
    build_rename_request,
    build_set_importance_request,
)


PKEXEC_PATH = Path("/usr/bin/pkexec")
HELPER_PATH = Path("/usr/local/libexec/arch-manager/manage-restore-points")
DEFAULT_TIMEOUT_SECONDS = 120.0


class RestorePointActionExecutionError(RuntimeError):
    """Base class for failures while executing a privileged restore-point action."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.detail = detail


class RestorePointActionCancelled(RestorePointActionExecutionError):
    """The user dismissed the Polkit authentication dialog."""


class RestorePointAuthorizationError(RestorePointActionExecutionError):
    """Polkit could not obtain authorization."""


class RestorePointHelperUnavailable(RestorePointActionExecutionError):
    """The installed privileged helper is missing or unsafe to execute."""


class RestorePointActionFailed(RestorePointActionExecutionError):
    """The privileged helper or Snapper rejected the requested action."""


class RestorePointActionTimedOut(RestorePointActionExecutionError):
    """The action did not finish within the bounded execution window."""


@dataclass(frozen=True)
class RestorePointActionResult:
    action: RestorePointAction
    point_number: int
    important: bool | None = None
    point_numbers: tuple[int, ...] = ()


def _validated_request(request: RestorePointActionRequest) -> RestorePointActionRequest:
    if not isinstance(request, RestorePointActionRequest):
        raise TypeError("request must be RestorePointActionRequest")

    if request.action is RestorePointAction.CREATE:
        return build_create_request(
            request.description,
            important=request.important,
        )

    if request.action is RestorePointAction.RENAME:
        return build_rename_request(request.point_number, request.description)

    if request.action is RestorePointAction.SET_IMPORTANCE:
        return build_set_importance_request(request.point_number, request.important)

    if request.action is RestorePointAction.DELETE:
        return build_delete_request(request.point_number)

    if request.action is RestorePointAction.DELETE_MANY:
        return build_delete_many_request(request.point_numbers)

    raise RestorePointActionValidationError("unsupported restore-point action")


def _ensure_runtime_ready() -> None:
    if not PKEXEC_PATH.is_file() or not os.access(PKEXEC_PATH, os.X_OK):
        raise RestorePointHelperUnavailable(
            "Не найден системный механизм авторизации Polkit.",
            detail=str(PKEXEC_PATH),
        )

    try:
        if HELPER_PATH.is_symlink():
            raise RestorePointHelperUnavailable(
                "Системный helper Arch Manager установлен небезопасно.",
                detail="helper must not be a symbolic link",
            )
        info = HELPER_PATH.stat()
    except FileNotFoundError as exc:
        raise RestorePointHelperUnavailable(
            "Системный helper Arch Manager не установлен.",
            detail=str(HELPER_PATH),
        ) from exc
    except OSError as exc:
        raise RestorePointHelperUnavailable(
            "Не удалось проверить системный helper Arch Manager.",
            detail=str(exc),
        ) from exc

    if not stat.S_ISREG(info.st_mode):
        raise RestorePointHelperUnavailable(
            "Системный helper Arch Manager установлен небезопасно.",
            detail="helper is not a regular file",
        )
    if info.st_uid != 0 or info.st_gid != 0:
        raise RestorePointHelperUnavailable(
            "Системный helper Arch Manager имеет неправильного владельца.",
            detail=f"uid={info.st_uid} gid={info.st_gid}",
        )
    if info.st_mode & 0o022:
        raise RestorePointHelperUnavailable(
            "Системный helper Arch Manager доступен на запись не только root.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )
    if not info.st_mode & 0o111:
        raise RestorePointHelperUnavailable(
            "Системный helper Arch Manager не является исполняемым.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )


def _parse_success(
    request: RestorePointActionRequest,
    stdout: str,
) -> RestorePointActionResult:
    line = stdout.strip()
    fields = line.split("\t")

    try:
        if request.action is RestorePointAction.CREATE:
            if len(fields) != 3 or fields[:2] != ["OK", "created"]:
                raise ValueError
            number = int(fields[2])
            if number <= 0:
                raise ValueError
            return RestorePointActionResult(request.action, number, request.important)

        if request.action is RestorePointAction.DELETE_MANY:
            if len(fields) != 3 or fields[:2] != ["OK", "deleted-many"]:
                raise ValueError
            numbers = tuple(int(value) for value in fields[2].split(",") if value)
            if numbers != request.point_numbers or not numbers:
                raise ValueError
            return RestorePointActionResult(request.action, numbers[0], point_numbers=numbers)

        expected_number = request.point_number
        if expected_number is None:
            raise ValueError

        if request.action is RestorePointAction.RENAME:
            if fields != ["OK", "renamed", str(expected_number)]:
                raise ValueError
            return RestorePointActionResult(request.action, expected_number)

        if request.action is RestorePointAction.SET_IMPORTANCE:
            expected_flag = "yes" if request.important else "no"
            if fields != ["OK", "importance", str(expected_number), expected_flag]:
                raise ValueError
            return RestorePointActionResult(
                request.action,
                expected_number,
                request.important,
            )

        if request.action is RestorePointAction.DELETE:
            if fields != ["OK", "deleted", str(expected_number)]:
                raise ValueError
            return RestorePointActionResult(request.action, expected_number)
    except (TypeError, ValueError) as exc:
        raise RestorePointActionFailed(
            "Privileged helper вернул неожиданный результат.",
            detail=line[:500],
        ) from exc

    raise RestorePointActionFailed(
        "Privileged helper вернул неожиданный результат.",
        detail=line[:500],
    )


def execute_restore_point_action(
    request: RestorePointActionRequest,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RestorePointActionResult:
    """Execute one validated restore-point action through the installed Polkit helper.

    This function is the only Stage 4 Python boundary for privilege escalation.
    It never invokes a shell and always uses fixed absolute paths for both
    ``pkexec`` and the installed helper.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    request = _validated_request(request)
    _ensure_runtime_ready()

    command = (
        str(PKEXEC_PATH),
        "--user",
        "root",
        str(HELPER_PATH),
        *request.helper_arguments(),
    )

    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = exc.stderr if isinstance(exc.stderr, str) else ""
        raise RestorePointActionTimedOut(
            "Операция с точкой восстановления не завершилась вовремя.",
            returncode=124,
            detail=detail[:500],
        ) from exc
    except OSError as exc:
        raise RestorePointHelperUnavailable(
            "Не удалось запустить системный helper Arch Manager.",
            detail=str(exc),
        ) from exc

    detail = completed.stderr.strip()[:500]
    if completed.returncode == 126:
        raise RestorePointActionCancelled(
            "Авторизация отменена.",
            returncode=126,
            detail=detail,
        )
    if completed.returncode == 127:
        raise RestorePointAuthorizationError(
            "Не удалось получить административное разрешение.",
            returncode=127,
            detail=detail,
        )
    if completed.returncode != 0:
        raise RestorePointActionFailed(
            "Операция с точкой восстановления не выполнена.",
            returncode=completed.returncode,
            detail=detail,
        )

    return _parse_success(request, completed.stdout)
