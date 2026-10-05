from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess

from src.app_store.actions import (
    PackageAction,
    PackageActionRequest,
    build_install_request,
    build_remove_request,
)


PKEXEC_PATH = Path("/usr/bin/pkexec")
HELPER_PATH = Path("/usr/local/libexec/arch-manager/manage-applications")
DEFAULT_TIMEOUT_SECONDS = 1800.0
HELPER_SELF_TEST = "OK\tself-test\tapp-store-v1"


class PackageActionExecutionError(RuntimeError):
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


class PackageActionCancelled(PackageActionExecutionError):
    pass


class PackageAuthorizationError(PackageActionExecutionError):
    pass


class PackageHelperUnavailable(PackageActionExecutionError):
    pass


class PackageActionFailed(PackageActionExecutionError):
    pass


class PackageActionTimedOut(PackageActionExecutionError):
    pass


class PackageActionBusy(PackageActionFailed):
    pass


class PackageActionUpdateRequired(PackageActionFailed):
    pass


class PackageActionUnavailable(PackageActionFailed):
    pass


@dataclass(frozen=True, slots=True)
class PackageActionResult:
    action: PackageAction
    package_name: str


def _validated_request(request: PackageActionRequest) -> PackageActionRequest:
    if not isinstance(request, PackageActionRequest):
        raise TypeError("request must be PackageActionRequest")
    if request.action is PackageAction.INSTALL:
        return build_install_request(request.package_name)
    if request.action is PackageAction.REMOVE:
        return build_remove_request(request.package_name)
    raise PackageActionFailed("Неподдерживаемое пакетное действие.")


def _ensure_runtime_ready() -> None:
    if not PKEXEC_PATH.is_file() or not os.access(PKEXEC_PATH, os.X_OK):
        raise PackageHelperUnavailable(
            "Не найден системный механизм авторизации Polkit.",
            detail=str(PKEXEC_PATH),
        )
    try:
        if HELPER_PATH.is_symlink():
            raise PackageHelperUnavailable(
                "Системный helper магазина установлен небезопасно.",
                detail="helper must not be a symbolic link",
            )
        info = HELPER_PATH.stat()
    except FileNotFoundError as exc:
        raise PackageHelperUnavailable(
            "Системный helper магазина приложений не установлен.",
            detail=str(HELPER_PATH),
        ) from exc
    except OSError as exc:
        raise PackageHelperUnavailable(
            "Не удалось проверить системный helper магазина приложений.",
            detail=str(exc),
        ) from exc

    if not stat.S_ISREG(info.st_mode):
        raise PackageHelperUnavailable(
            "Системный helper магазина установлен небезопасно.",
            detail="helper is not a regular file",
        )
    if info.st_uid != 0 or info.st_gid != 0:
        raise PackageHelperUnavailable(
            "Системный helper магазина имеет неправильного владельца.",
            detail=f"uid={info.st_uid} gid={info.st_gid}",
        )
    if info.st_mode & 0o022:
        raise PackageHelperUnavailable(
            "Системный helper магазина доступен на запись не только root.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )
    if not info.st_mode & 0o111:
        raise PackageHelperUnavailable(
            "Системный helper магазина не является исполняемым.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )

    try:
        probe = subprocess.run(
            (str(HELPER_PATH), "--self-test"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackageHelperUnavailable(
            "Не удалось проверить версию системного helper магазина.",
            detail=str(exc),
        ) from exc
    if probe.returncode != 0 or probe.stdout.strip() != HELPER_SELF_TEST:
        raise PackageHelperUnavailable(
            "Системный helper магазина устарел или несовместим.",
            detail=(probe.stderr or probe.stdout).strip()[:1000],
        )


def _parse_success(request: PackageActionRequest, stdout: str) -> PackageActionResult:
    fields = stdout.strip().split("\t")
    expected = "installed" if request.action is PackageAction.INSTALL else "removed"
    if fields != ["OK", expected, request.package_name]:
        raise PackageActionFailed(
            "Privileged helper вернул неожиданный результат.",
            detail=stdout.strip()[:1000],
        )
    return PackageActionResult(request.action, request.package_name)


def execute_package_action(
    request: PackageActionRequest,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PackageActionResult:
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
        raise PackageActionTimedOut(
            "Пакетная операция не завершилась вовремя.",
            returncode=124,
            detail=detail[:2000],
        ) from exc
    except OSError as exc:
        raise PackageHelperUnavailable(
            "Не удалось запустить системный helper магазина приложений.",
            detail=str(exc),
        ) from exc

    detail = completed.stderr.strip()[:4000]
    code = completed.returncode
    if code == 126:
        raise PackageActionCancelled("Авторизация отменена.", returncode=code, detail=detail)
    if code == 127:
        raise PackageAuthorizationError(
            "Не удалось получить административное разрешение.",
            returncode=code,
            detail=detail,
        )
    if code == 73:
        raise PackageActionBusy(
            "Менеджер пакетов сейчас занят другой операцией.",
            returncode=code,
            detail=detail,
        )
    if code == 74:
        raise PackageActionUpdateRequired(
            "Перед установкой приложения требуется полное обновление системы.",
            returncode=code,
            detail=detail,
        )
    if code == 75:
        raise PackageActionUnavailable(
            "Пакет недоступен в официальных репозиториях Arch Linux.",
            returncode=code,
            detail=detail,
        )
    if code != 0:
        raise PackageActionFailed(
            "Пакетная операция не выполнена.",
            returncode=code,
            detail=detail,
        )
    return _parse_success(request, completed.stdout)
