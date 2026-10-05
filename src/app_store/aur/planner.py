from __future__ import annotations

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import re
from collections.abc import Callable

from src.core.command import CommandResult, run_command

from .availability import AurCapabilityReport
from .errors import (
    AurError,
    AurBuildToolsUnavailable,
    AurHelperUnavailable,
    AurPackageAlreadyInstalled,
    AurPackageNotAur,
    AurPackageNotFound,
    AurPackageNotInstalled,
    AurSystemUpdateRequired,
    AurTransactionBusy,
    AurUnavailable,
)
from .models import AurPackage
from .service import AurService
from .versioning import update_available


PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")
_AUR_PLAN_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="arch-manager-aur-plan")


def validate_aur_package_name(value: str) -> str:
    name = (value or "").strip()
    if not name or len(name) > 128 or name.startswith("-") or not PACKAGE_NAME_RE.fullmatch(name):
        raise ValueError("Некорректное имя AUR-пакета.")
    return name


@dataclass(frozen=True, slots=True)
class AurInstallPlan:
    package: AurPackage
    command_preview: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AurRemovePlan:
    package: AurPackage
    command_preview: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AurUpdatePlan:
    package: AurPackage
    command_preview: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AurUpdateAllPlan:
    package_names: tuple[str, ...]
    command_preview: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class _AurPlannerBase:
    def __init__(
        self,
        aur_service: AurService,
        *,
        runner: Callable[..., CommandResult] = run_command,
        lock_path: Path = PACMAN_LOCK,
    ) -> None:
        self.aur_service = aur_service
        self.runner = runner
        self.lock_path = lock_path

    def _ensure_not_busy(self) -> None:
        if self.lock_path.exists():
            raise AurTransactionBusy(
                "Менеджер пакетов сейчас занят другой операцией.",
                detail=str(self.lock_path),
            )

    @staticmethod
    def _require_runtime_capabilities(report: AurCapabilityReport) -> None:
        if report.running_as_root:
            raise AurUnavailable("AUR-операции не запускаются от root.")
        if not report.yay_available:
            raise AurHelperUnavailable("yay не найден или не запускается.")
        if not report.pacman_available:
            raise AurUnavailable("pacman недоступен.")

    @staticmethod
    def _require_build_capabilities(report: AurCapabilityReport) -> None:
        _AurPlannerBase._require_runtime_capabilities(report)
        missing: list[str] = []
        if not report.git_available:
            missing.append("git")
        if not report.makepkg_available:
            missing.append("makepkg")
        if report.base_devel_complete is not True:
            missing.extend(report.missing_base_devel or ("base-devel",))
        if missing:
            unique = tuple(dict.fromkeys(missing))
            raise AurBuildToolsUnavailable(
                "Не хватает инструментов для сборки AUR-пакетов.",
                detail=", ".join(unique),
            )

    def _query_installed_version(self, package_name: str) -> str | None:
        result = self.runner(["pacman", "-Q", "--", package_name], timeout=10)
        if not result.available:
            raise AurUnavailable("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise AurUnavailable(
                "Проверка установленного пакета заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise AurUnavailable(
                "Не удалось проверить локальное состояние пакета.",
                detail=(result.stderr or result.stdout).strip(),
            )
        parts = result.stdout.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0] != package_name or not parts[1].strip():
            raise AurUnavailable(
                "pacman вернул неожиданное состояние установленного пакета.",
                detail=result.stdout.strip(),
            )
        return parts[1].strip()

    def _ensure_no_pending_system_upgrade(self, *, verb: str = "обновлением") -> None:
        result = self.runner(["pacman", "-Qu", "--color", "never"], timeout=30)
        if not result.available:
            raise AurUnavailable("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise AurUnavailable(
                "Проверка системных обновлений заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode not in {0, 1}:
            raise AurUnavailable(
                "Не удалось проверить состояние системных обновлений.",
                detail=(result.stderr or result.stdout).strip(),
            )
        if result.stdout.strip():
            raise AurSystemUpdateRequired(
                f"Перед {verb} AUR-пакетов сначала обновите систему.",
                detail=result.stdout.strip(),
            )

    def _is_foreign_package(self, package_name: str) -> bool:
        result = self.runner(["pacman", "-Qm", "--", package_name], timeout=10)
        if not result.available:
            raise AurUnavailable("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise AurUnavailable(
                "Проверка источника пакета заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise AurUnavailable(
            "Не удалось проверить источник установленного пакета.",
            detail=(result.stderr or result.stdout).strip(),
        )


class AurInstallPlanner(_AurPlannerBase):
    """Prepare a safe, read-only plan for Stage 7.3 AUR installation."""

    def plan(self, package_name: str) -> AurInstallPlan:
        name = validate_aur_package_name(package_name)
        self._ensure_not_busy()
        report = self.aur_service.capabilities()
        self._require_build_capabilities(report)
        self._ensure_not_installed(name)
        self._ensure_no_pending_system_upgrade()

        # Installation never trusts cached AUR metadata as its authority. The
        # exact package name is re-confirmed through aurweb immediately before
        # the terminal workflow is offered to the user.
        fresh = self.aur_service.info((name,), use_cache=False)
        package = next((item for item in fresh if item.name == name), None)
        if package is None:
            raise AurPackageNotFound(f"Пакет {name!r} больше не найден в AUR.")

        warnings: list[str] = []
        if package.out_of_date is not None:
            warnings.append("Пакет помечен устаревшим в AUR.")
        if package.orphaned:
            warnings.append("У пакета нет назначенного сопровождающего.")
        return AurInstallPlan(
            package=package,
            command_preview=("yay", "-S", "--aur", "--", name),
            warnings=tuple(warnings),
        )

    def plan_async(
        self,
        package_name: str,
        *,
        executor: Executor | None = None,
    ) -> Future[AurInstallPlan]:
        return (executor or _AUR_PLAN_EXECUTOR).submit(self.plan, package_name)

    def _ensure_not_installed(self, package_name: str) -> None:
        result = self.runner(["pacman", "-Qq", "--", package_name], timeout=10)
        if not result.available:
            raise AurUnavailable("pacman недоступен.", detail=result.stderr)
        if result.timed_out:
            raise AurUnavailable(
                "Проверка установленного пакета заняла слишком много времени.",
                detail=result.stderr,
            )
        if result.returncode == 0:
            raise AurPackageAlreadyInstalled(
                f"Пакет {package_name!r} уже установлен в системе."
            )
        if result.returncode != 1:
            raise AurUnavailable(
                "Не удалось проверить локальное состояние пакета.",
                detail=(result.stderr or result.stdout).strip(),
            )

    def _ensure_no_pending_system_upgrade(self) -> None:
        super()._ensure_no_pending_system_upgrade(verb="установкой")


class AurRemovePlanner(_AurPlannerBase):
    """Prepare a local-only Stage 7.4 removal plan for a confirmed AUR package."""

    def plan(self, package: AurPackage) -> AurRemovePlan:
        if not isinstance(package, AurPackage):
            raise TypeError("package must be a confirmed AurPackage")
        name = validate_aur_package_name(package.name)
        if not package.aur_confirmed or package.foreign_unknown:
            raise AurPackageNotAur(f"Пакет {name!r} не подтверждён как AUR-пакет.")
        self._ensure_not_busy()
        report = self.aur_service.capabilities()
        self._require_runtime_capabilities(report)

        installed_version = self._query_installed_version(name)
        if installed_version is None:
            raise AurPackageNotInstalled(f"Пакет {name!r} уже не установлен в системе.")
        if not self._is_foreign_package(name):
            raise AurPackageNotAur(
                f"Пакет {name!r} больше не определяется как AUR/foreign-пакет."
            )

        planned_package = package.with_local_state(
            installed_version=installed_version,
            update_available=package.update_available,
        )

        return AurRemovePlan(
            package=planned_package,
            command_preview=("yay", "-Rns", "--", name),
            warnings=(
                "После удаления Arch Manager сравнит список ненужных зависимостей с состоянием до операции и предложит удалить только новые orphan-пакеты.",
                "Кэш сборки yay именно удаляемого AUR-пакета будет очищен автоматически.",
                "Ненужные зависимости, существовавшие до удаления, автоматически не затрагиваются.",
            ),
        )

    def plan_async(
        self,
        package: AurPackage,
        *,
        executor: Executor | None = None,
    ) -> Future[AurRemovePlan]:
        return (executor or _AUR_PLAN_EXECUTOR).submit(self.plan, package)


class AurUpdatePlanner(_AurPlannerBase):
    """Prepare Stage 7.5 single/all AUR update plans without mutating the system."""

    def plan(self, package: AurPackage) -> AurUpdatePlan:
        if not isinstance(package, AurPackage):
            raise TypeError("package must be a confirmed AurPackage")
        name = validate_aur_package_name(package.name)
        if not package.aur_confirmed or package.foreign_unknown:
            raise AurPackageNotAur(f"Пакет {name!r} не подтверждён как AUR-пакет.")
        self._ensure_not_busy()
        self._require_build_capabilities(self.aur_service.capabilities())
        self._ensure_no_pending_system_upgrade()

        installed_version = self._query_installed_version(name)
        if installed_version is None:
            raise AurPackageNotInstalled(f"Пакет {name!r} уже не установлен в системе.")
        if not self._is_foreign_package(name):
            raise AurPackageNotAur(
                f"Пакет {name!r} больше не определяется как AUR/foreign-пакет."
            )

        fresh = self.aur_service.info((name,), use_cache=False)
        remote = next((item for item in fresh if item.name == name), None)
        if remote is None:
            raise AurPackageNotFound(f"Пакет {name!r} больше не найден в AUR.")
        try:
            has_update = update_available(
                installed_version, remote.version, runner=self.aur_service.version_runner
            )
        except (AurError, ValueError):
            has_update = bool(package.update_available)
        if not has_update:
            raise AurUnavailable(f"Для пакета {name!r} обновление AUR больше не требуется.")

        planned = remote.with_local_state(
            installed_version=installed_version, update_available=True
        )
        warnings: list[str] = []
        if planned.out_of_date is not None:
            warnings.append("Пакет помечен устаревшим в AUR.")
        if planned.orphaned:
            warnings.append("У пакета нет назначенного сопровождающего.")
        return AurUpdatePlan(
            package=planned,
            command_preview=("yay", "-S", "--aur", "--", name),
            warnings=tuple(warnings),
        )

    def plan_async(self, package: AurPackage, *, executor: Executor | None = None) -> Future[AurUpdatePlan]:
        return (executor or _AUR_PLAN_EXECUTOR).submit(self.plan, package)

    def plan_all(self, package_names: tuple[str, ...]) -> AurUpdateAllPlan:
        names = tuple(dict.fromkeys(validate_aur_package_name(name) for name in package_names))
        if not names:
            raise AurUnavailable("AUR-обновлений больше нет.")
        self._ensure_not_busy()
        self._require_build_capabilities(self.aur_service.capabilities())
        self._ensure_no_pending_system_upgrade()
        return AurUpdateAllPlan(
            package_names=names,
            command_preview=("yay", "-Sua"),
            warnings=(
                "yay может показать PKGBUILD/diff и запросить подтверждения в терминале.",
            ),
        )

    def plan_all_async(self, package_names: tuple[str, ...], *, executor: Executor | None = None) -> Future[AurUpdateAllPlan]:
        return (executor or _AUR_PLAN_EXECUTOR).submit(self.plan_all, package_names)
