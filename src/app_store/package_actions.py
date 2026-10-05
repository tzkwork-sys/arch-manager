from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from src.core.command import CommandResult, run_command

from .actions import (
    PackageAction,
    PackageActionRequest,
    build_install_request,
    build_remove_request,
)
from .package_state import OFFICIAL_REPOSITORIES, PacmanPackageStateProvider


PACMAN_LOCK = Path("/var/lib/pacman/db.lck")


class PackagePlanError(RuntimeError):
    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class PackageBusyError(PackagePlanError):
    pass


class PackageUnavailableError(PackagePlanError):
    pass


class PackageUpdateRequired(PackagePlanError):
    pass


@dataclass(frozen=True, slots=True)
class PackageChange:
    package_name: str
    version: str = ""


@dataclass(frozen=True, slots=True)
class PackageTransactionPlan:
    request: PackageActionRequest
    changes: tuple[PackageChange, ...]

    @property
    def additional_changes(self) -> tuple[PackageChange, ...]:
        return tuple(
            change for change in self.changes if change.package_name != self.request.package_name
        )


class PacmanPackageActionPlanner:
    """Build a read-only package transaction preview from existing pacman databases."""

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_command,
        lock_path: Path = PACMAN_LOCK,
    ) -> None:
        self.runner = runner
        self.lock_path = lock_path
        self.state_provider = PacmanPackageStateProvider(runner=runner)

    def plan(self, request: PackageActionRequest) -> PackageTransactionPlan:
        if not isinstance(request, PackageActionRequest):
            raise TypeError("request must be PackageActionRequest")
        if request.action is PackageAction.INSTALL:
            request = build_install_request(request.package_name)
            return self._plan_install(request)
        if request.action is PackageAction.REMOVE:
            request = build_remove_request(request.package_name)
            return self._plan_remove(request)
        raise PackagePlanError("Неподдерживаемое пакетное действие.")

    def _ensure_not_busy(self) -> None:
        if self.lock_path.exists():
            raise PackageBusyError(
                "Менеджер пакетов сейчас занят другой операцией.",
                detail=str(self.lock_path),
            )

    def _official_repository(self, package_name: str) -> str:
        result = self.runner(["pacman", "-Sl"], timeout=30)
        if not result.available or result.timed_out or result.returncode != 0:
            raise PackagePlanError(
                "Не удалось прочитать официальные базы пакетов Arch Linux.",
                detail=(result.stderr or result.stdout).strip(),
            )
        for raw_line in result.stdout.splitlines():
            fields = raw_line.split()
            if len(fields) >= 3 and fields[0] in OFFICIAL_REPOSITORIES and fields[1] == package_name:
                return fields[0]
        raise PackageUnavailableError(
            "Пакет не найден в официальных репозиториях Arch Linux.",
            detail=package_name,
        )

    def _state(self, package_name: str):
        official_repository = self._official_repository(package_name)
        states = self.state_provider.collect((package_name,))
        state = states.get(package_name)
        if state is None or not state.available:
            raise PackageUnavailableError(
                "Пакет не найден в официальных репозиториях Arch Linux.",
                detail=package_name,
            )
        return state, official_repository

    def _ensure_no_pending_system_upgrade(self) -> None:
        result = self.runner(["pacman", "-Qu", "--color", "never"], timeout=30)
        if not result.available:
            raise PackagePlanError("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise PackagePlanError(
                "Проверка состояния обновлений заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode not in {0, 1}:
            raise PackagePlanError(
                "Не удалось проверить целостность текущего состояния пакетов.",
                detail=(result.stderr or result.stdout).strip(),
            )
        if result.stdout.strip():
            raise PackageUpdateRequired(
                "Перед установкой приложения требуется полное обновление системы.",
                detail=result.stdout.strip(),
            )

    def _preview(self, args: list[str]) -> tuple[PackageChange, ...]:
        result = self.runner(args, timeout=60)
        if not result.available:
            raise PackagePlanError("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise PackagePlanError(
                "Расчёт пакетной операции занял слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode != 0:
            raise PackagePlanError(
                "Не удалось подготовить безопасный план пакетной операции.",
                detail=(result.stderr or result.stdout).strip(),
            )
        changes: list[PackageChange] = []
        seen: set[str] = set()
        for raw_line in result.stdout.splitlines():
            fields = raw_line.rstrip("\n").split("|", 1)
            if not fields:
                continue
            name = fields[0].strip()
            if not name or name in seen:
                continue
            version = fields[1].strip() if len(fields) > 1 else ""
            seen.add(name)
            changes.append(PackageChange(name, version))
        return tuple(changes)

    def _plan_install(self, request: PackageActionRequest) -> PackageTransactionPlan:
        self._ensure_not_busy()
        state, repository = self._state(request.package_name)
        if state.installed:
            return PackageTransactionPlan(
                request,
                (PackageChange(request.package_name, state.installed_version or ""),),
            )
        self._ensure_no_pending_system_upgrade()
        changes = self._preview(
            [
                "pacman",
                "-Sp",
                "--needed",
                "--print-format",
                "%n|%v",
                f"{repository}/{request.package_name}",
            ]
        )
        if not changes:
            changes = (PackageChange(request.package_name, state.available_version or ""),)
        return PackageTransactionPlan(request, changes)

    def _installed_version(self, package_name: str) -> str | None:
        result = self.runner(["pacman", "-Q", "--", package_name], timeout=15)
        if not result.available:
            raise PackagePlanError("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise PackagePlanError(
                "Проверка установленного пакета заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise PackagePlanError(
                "Не удалось проверить установленный пакет.",
                detail=(result.stderr or result.stdout).strip(),
            )
        parts = result.stdout.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0] != package_name:
            raise PackagePlanError(
                "Pacman вернул неожиданное состояние установленного пакета.",
                detail=result.stdout.strip(),
            )
        return parts[1].strip() or None

    def _plan_remove(self, request: PackageActionRequest) -> PackageTransactionPlan:
        self._ensure_not_busy()
        installed_version: str | None
        try:
            state, _repository = self._state(request.package_name)
        except PackageUnavailableError:
            # Local/foreign packages are not present in official sync databases,
            # but pacman can still remove them safely by exact installed name.
            installed_version = self._installed_version(request.package_name)
            if installed_version is None:
                return PackageTransactionPlan(request, ())
        else:
            if not state.installed:
                return PackageTransactionPlan(request, ())
            installed_version = state.installed_version

        changes = self._preview(
            [
                "pacman",
                "-Rsp",
                "--print-format",
                "%n|%v",
                request.package_name,
            ]
        )
        if not changes:
            changes = (PackageChange(request.package_name, installed_version or ""),)
        return PackageTransactionPlan(request, changes)
