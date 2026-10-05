from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import shutil

from .command import run_command
from .system_diagnostics import (
    DiagnosticCheck,
    build_disk_space_check,
    build_service_checks,
    check_boot_environment,
    check_boot_journal,
    check_btrfs_device_stats,
    check_fstab,
    check_kernel_taint,
    check_memory_available,
    check_memory_pressure,
    check_pacman_database,
    check_root_mount,
    check_smart_devices,
    check_swap,
    check_time_sync,
    check_tpm2,
    detect_known_nvpcr_regression,
)


GIB = 1024 ** 3
# Overall UI stays green for a small number of non-critical diagnostic warnings.
SYSTEM_WARNING_BADGE_THRESHOLD = 6


@dataclass(frozen=True)
class DiskInfo:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float
    filesystem: str


@dataclass(frozen=True)
class SystemInfo:
    os_name: str
    kernel: str
    plasma_version: str | None
    disk: DiskInfo
    failed_system_services: int | None
    failed_user_services: int | None
    failed_system_unit_names: tuple[str, ...] = ()
    failed_user_unit_names: tuple[str, ...] = ()
    diagnostics: tuple[DiagnosticCheck, ...] = ()
    extended_diagnostics: bool = False

    @property
    def failed_services_total(self) -> int | None:
        values = (self.failed_system_services, self.failed_user_services)
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    @property
    def advisory_system_unit_names(self) -> tuple[str, ...]:
        """Known auxiliary failures that should be shown as warnings, not as system breakage."""
        return tuple(
            unit
            for unit in self.failed_system_unit_names
            if _is_advisory_system_unit(unit)
        )

    @property
    def critical_failed_system_services(self) -> int | None:
        if self.failed_system_services is None:
            return None
        # If unit names are unavailable, keep the conservative legacy behaviour.
        if not self.failed_system_unit_names:
            return self.failed_system_services
        return max(0, self.failed_system_services - len(self.advisory_system_unit_names))

    @property
    def advisory_system_services(self) -> int:
        return len(self.advisory_system_unit_names)

    @property
    def has_service_advisories(self) -> bool:
        return self.advisory_system_services > 0

    @property
    def critical_checks(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(check for check in self.diagnostics if check.status == "critical")

    @property
    def warning_checks(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(check for check in self.diagnostics if check.status == "warning")

    @property
    def unknown_checks(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(check for check in self.diagnostics if check.status == "unknown")

    @property
    def info_checks(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(check for check in self.diagnostics if check.status == "info")

    @property
    def has_incomplete_checks(self) -> bool:
        return bool(self.unknown_checks)

    @property
    def needs_attention(self) -> bool:
        if self.diagnostics:
            return bool(self.critical_checks)

        # Backwards-compatible fallback for tests and callers that construct
        # SystemInfo directly without the richer diagnostic collection.
        critical_system = self.critical_failed_system_services
        failed_user = self.failed_user_services
        disk_critical = self.disk.free_bytes < 5 * GIB or self.disk.percent_used >= 95.0
        return bool(
            disk_critical
            or (critical_system is not None and critical_system > 0)
            or (failed_user is not None and failed_user > 0)
        )

    @property
    def has_warnings(self) -> bool:
        if self.diagnostics:
            return bool(self.warning_checks)
        disk_warning = self.disk.free_bytes < 10 * GIB or self.disk.percent_used >= 90.0
        return bool(disk_warning or self.has_service_advisories)

    def diagnostic(self, check_id: str) -> DiagnosticCheck | None:
        return next((check for check in self.diagnostics if check.id == check_id), None)


def _line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


_ADVISORY_SYSTEM_UNIT_EXACT = {
    "systemd-pcrproduct.service",
    "systemd-tpm2-setup-early.service",
    "systemd-tpm2-setup.service",
}
_ADVISORY_SYSTEM_UNIT_PREFIXES = (
    "systemd-pcrlogin@",
)


def _is_advisory_system_unit(unit: str) -> bool:
    """Classify known TPM/NvPCR helper units as non-critical advisories."""
    return (
        unit in _ADVISORY_SYSTEM_UNIT_EXACT
        or unit.startswith(_ADVISORY_SYSTEM_UNIT_PREFIXES)
    )


def _failed_service_names(user: bool = False) -> tuple[str, ...] | None:
    args = ["systemctl"]
    if user:
        args.append("--user")
    args.extend(["--failed", "--no-legend", "--plain"])
    result = run_command(args, timeout=10)
    if result.returncode != 0:
        return None

    names: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        names.append(stripped.split(None, 1)[0])
    return tuple(names)


def _failed_services(user: bool = False) -> int | None:
    names = _failed_service_names(user)
    return len(names) if names is not None else None


def _systemd_state() -> str | None:
    result = run_command(["systemctl", "is-system-running"], timeout=6)
    text = result.stdout.strip()
    # is-system-running intentionally uses non-zero return codes for states such
    # as degraded/maintenance. The textual state is what matters here.
    return text.splitlines()[0] if text else None


def _filesystem_type() -> str:
    result = run_command(["findmnt", "-no", "FSTYPE", "/"], timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    return "не определена"


def _plasma_version() -> str | None:
    result = run_command(["plasmashell", "--version"], timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        parts = result.stdout.strip().split()
        if parts:
            return parts[-1]

    result = run_command(["pacman", "-Q", "plasma-workspace"], timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        parts = result.stdout.strip().split()
        if len(parts) >= 2:
            return parts[1]
    return None


def _os_name() -> str:
    pretty_name = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    pretty_name = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass
    return pretty_name or platform.system()


def collect_system_info(
    *,
    include_extended: bool = False,
    extended_smart_check: DiagnosticCheck | None = None,
) -> SystemInfo:
    usage = shutil.disk_usage("/")
    percent_used = (usage.used / usage.total * 100.0) if usage.total else 0.0
    disk = DiskInfo(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        percent_used=percent_used,
        filesystem=_filesystem_type(),
    )
    failed_system_units = _failed_service_names(False)
    failed_user_units = _failed_service_names(True)
    failed_system_services = len(failed_system_units) if failed_system_units is not None else None
    failed_user_services = len(failed_user_units) if failed_user_units is not None else None
    advisory_system_unit_names = tuple(
        unit for unit in (failed_system_units or ()) if _is_advisory_system_unit(unit)
    )
    critical_system_unit_names = tuple(
        unit for unit in (failed_system_units or ()) if not _is_advisory_system_unit(unit)
    )
    critical_system_services = (
        None
        if failed_system_services is None
        else max(0, failed_system_services - len(advisory_system_unit_names))
    )
    known_nvpcr_regression = detect_known_nvpcr_regression(advisory_system_unit_names)

    diagnostics: list[DiagnosticCheck] = [
        build_disk_space_check(disk.free_bytes, disk.percent_used),
        check_root_mount(),
        check_btrfs_device_stats(disk.filesystem),
        check_memory_available(),
        check_swap(),
        check_memory_pressure(),
        check_fstab(),
        check_pacman_database(),
        check_time_sync(),
        check_kernel_taint(),
        check_tpm2(),
    ]
    diagnostics.extend(check_boot_environment())
    journal_check, oom_check = check_boot_journal(
        extended=include_extended,
        known_nvpcr_regression=known_nvpcr_regression,
    )
    diagnostics.extend((journal_check, oom_check))
    diagnostics.extend(
        build_service_checks(
            systemd_state=_systemd_state(),
            critical_system=critical_system_services,
            advisory_system=len(advisory_system_unit_names),
            failed_user=failed_user_services,
            critical_names=critical_system_unit_names,
            advisory_names=advisory_system_unit_names,
            user_names=failed_user_units or (),
            known_nvpcr_regression=known_nvpcr_regression,
        )
    )
    if include_extended:
        diagnostics.append(extended_smart_check or check_smart_devices())

    return SystemInfo(
        os_name=_os_name(),
        kernel=os.uname().release if hasattr(os, "uname") else platform.release(),
        plasma_version=_plasma_version(),
        disk=disk,
        failed_system_services=failed_system_services,
        failed_user_services=failed_user_services,
        failed_system_unit_names=failed_system_units or (),
        failed_user_unit_names=failed_user_units or (),
        diagnostics=tuple(diagnostics),
        extended_diagnostics=include_extended,
    )
