from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess

from .maintenance import discover_trash_roots
from .maintenance_actions import (
    MaintenanceAction,
    MaintenanceCleanupRequest,
    build_cleanup_request,
)


PKEXEC_PATH = Path("/usr/bin/pkexec")
HELPER_PATH = Path("/usr/local/libexec/arch-manager/manage-maintenance")
DEFAULT_TIMEOUT_SECONDS = 600.0


class MaintenanceExecutionError(RuntimeError):
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


class MaintenanceCancelled(MaintenanceExecutionError):
    pass


class MaintenanceAuthorizationError(MaintenanceExecutionError):
    pass


class MaintenanceHelperUnavailable(MaintenanceExecutionError):
    pass


class MaintenanceActionFailed(MaintenanceExecutionError):
    pass


class MaintenanceTimedOut(MaintenanceExecutionError):
    pass


@dataclass(frozen=True)
class MaintenanceCleanupResult:
    completed_actions: tuple[MaintenanceAction, ...]
    cache_keep_versions: int


def _ensure_runtime_ready() -> None:
    if not PKEXEC_PATH.is_file() or not os.access(PKEXEC_PATH, os.X_OK):
        raise MaintenanceHelperUnavailable(
            "Не найден системный механизм авторизации Polkit.",
            detail=str(PKEXEC_PATH),
        )

    try:
        if HELPER_PATH.is_symlink():
            raise MaintenanceHelperUnavailable(
                "Системный helper обслуживания установлен небезопасно.",
                detail="helper must not be a symbolic link",
            )
        info = HELPER_PATH.stat()
    except FileNotFoundError as exc:
        raise MaintenanceHelperUnavailable(
            "Системный helper обслуживания Arch Manager не установлен.",
            detail=str(HELPER_PATH),
        ) from exc
    except OSError as exc:
        raise MaintenanceHelperUnavailable(
            "Не удалось проверить системный helper обслуживания.",
            detail=str(exc),
        ) from exc

    if not stat.S_ISREG(info.st_mode):
        raise MaintenanceHelperUnavailable(
            "Системный helper обслуживания установлен небезопасно.",
            detail="helper is not a regular file",
        )
    if info.st_uid != 0 or info.st_gid != 0:
        raise MaintenanceHelperUnavailable(
            "Системный helper обслуживания имеет неправильного владельца.",
            detail=f"uid={info.st_uid} gid={info.st_gid}",
        )
    if info.st_mode & 0o022:
        raise MaintenanceHelperUnavailable(
            "Системный helper обслуживания доступен на запись не только root.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )
    if not info.st_mode & 0o111:
        raise MaintenanceHelperUnavailable(
            "Системный helper обслуживания не является исполняемым.",
            detail=f"mode={stat.S_IMODE(info.st_mode):04o}",
        )


def _run_privileged(
    actions: tuple[MaintenanceAction, ...],
    *,
    cache_keep_versions: int,
    timeout: float,
) -> tuple[MaintenanceAction, ...]:
    if not actions:
        return ()
    _ensure_runtime_ready()

    command = (
        str(PKEXEC_PATH),
        "--user",
        "root",
        str(HELPER_PATH),
        "clean",
        "--cache-keep",
        str(cache_keep_versions),
        *(action.value for action in actions),
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
        raise MaintenanceTimedOut(
            "Очистка не завершилась вовремя.",
            returncode=124,
            detail=detail[:700],
        ) from exc
    except OSError as exc:
        raise MaintenanceHelperUnavailable(
            "Не удалось запустить системный helper обслуживания.",
            detail=str(exc),
        ) from exc

    detail = completed.stderr.strip()[:700]
    if completed.returncode == 126:
        raise MaintenanceCancelled(
            "Авторизация отменена.",
            returncode=126,
            detail=detail,
        )
    if completed.returncode == 127:
        raise MaintenanceAuthorizationError(
            "Не удалось получить административное разрешение.",
            returncode=127,
            detail=detail,
        )
    if completed.returncode != 0:
        raise MaintenanceActionFailed(
            "Системная часть очистки завершилась с ошибкой. Состояние будет проверено повторно.",
            returncode=completed.returncode,
            detail=detail,
        )

    seen: list[MaintenanceAction] = []
    done_marker = False
    for raw in completed.stdout.splitlines():
        fields = raw.strip().split("\t")
        if fields == ["OK", "done"]:
            done_marker = True
            continue
        if len(fields) >= 2 and fields[0] == "OK":
            try:
                action = MaintenanceAction(fields[1])
            except ValueError as exc:
                raise MaintenanceActionFailed(
                    "Privileged helper вернул неожиданный результат.",
                    detail=raw[:500],
                ) from exc
            if action in actions and action not in seen:
                seen.append(action)

    if not done_marker or set(seen) != set(actions):
        raise MaintenanceActionFailed(
            "Privileged helper вернул неполный результат.",
            detail=completed.stdout.strip()[:700],
        )
    return tuple(seen)


def _clear_directory_contents(path: Path) -> None:
    """Clear one fixed user-owned directory without following a root symlink."""
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise MaintenanceActionFailed(
            "Каталог пользовательской очистки имеет неожиданный тип.",
            detail=str(path),
        )
    try:
        if path.stat().st_uid != os.getuid():
            raise MaintenanceActionFailed(
                "Каталог пользовательской очистки принадлежит другому пользователю.",
                detail=str(path),
            )
    except OSError as exc:
        raise MaintenanceActionFailed(
            "Не удалось проверить каталог пользовательской очистки.",
            detail=f"{path}: {exc}",
        ) from exc

    try:
        for entry in path.iterdir():
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
    except OSError as exc:
        raise MaintenanceActionFailed(
            "Не удалось полностью очистить пользовательские данные.",
            detail=f"{path}: {exc}",
        ) from exc


def _run_user_action(action: MaintenanceAction) -> None:
    home = Path.home()
    if action is MaintenanceAction.THUMBNAILS:
        _clear_directory_contents(home / ".cache" / "thumbnails")
        return
    if action is MaintenanceAction.TRASH:
        for root in discover_trash_roots(home=home):
            _clear_directory_contents(root / "files")
            _clear_directory_contents(root / "info")
        return
    raise MaintenanceActionFailed(
        "Получено неподдерживаемое пользовательское действие.",
        detail=action.value,
    )


def execute_maintenance_cleanup(
    request: MaintenanceCleanupRequest,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> MaintenanceCleanupResult:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not isinstance(request, MaintenanceCleanupRequest):
        raise TypeError("request must be MaintenanceCleanupRequest")

    # Rebuild through the validator so manually-constructed requests cannot bypass
    # the finite action/parameter contract.
    request = build_cleanup_request(
        request.actions,
        cache_keep_versions=request.cache_keep_versions,
    )
    completed: list[MaintenanceAction] = []

    privileged = request.privileged_actions
    if privileged:
        completed.extend(
            _run_privileged(
                privileged,
                cache_keep_versions=request.cache_keep_versions,
                timeout=timeout,
            )
        )

    # User-owned data is changed only after the privileged batch succeeded. If
    # authentication is cancelled nothing else is silently deleted afterwards.
    for action in request.user_actions:
        _run_user_action(action)
        completed.append(action)

    return MaintenanceCleanupResult(
        tuple(completed),
        cache_keep_versions=request.cache_keep_versions,
    )
