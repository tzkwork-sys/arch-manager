from src.core.system_diagnostics import DiagnosticCheck, build_disk_space_check, build_service_checks
from src.core.system_info import DiskInfo, SystemInfo


def _disk(*, free_gib: int = 80, used_percent: float = 20.0) -> DiskInfo:
    total = 100 * 1024**3
    free = free_gib * 1024**3
    return DiskInfo(
        total_bytes=total,
        used_bytes=max(0, total - free),
        free_bytes=free,
        percent_used=used_percent,
        filesystem="btrfs",
    )


def test_disk_thresholds_are_consistent():
    assert build_disk_space_check(20 * 1024**3, 70.0).status == "ok"
    assert build_disk_space_check(8 * 1024**3, 91.0).status == "warning"
    assert build_disk_space_check(4 * 1024**3, 96.0).status == "critical"


def test_richer_system_info_uses_diagnostic_severity():
    warning = DiagnosticCheck("x", "integrity", "X", "warning", "review")
    critical = DiagnosticCheck("y", "integrity", "Y", "critical", "broken")
    base = dict(
        os_name="Arch Linux",
        kernel="test",
        plasma_version="test",
        disk=_disk(),
        failed_system_services=0,
        failed_user_services=0,
    )
    assert SystemInfo(**base, diagnostics=(warning,)).needs_attention is False
    assert SystemInfo(**base, diagnostics=(warning,)).has_warnings is True
    assert SystemInfo(**base, diagnostics=(critical,)).needs_attention is True


def test_degraded_systemd_with_only_advisories_is_not_critical():
    checks = build_service_checks(
        systemd_state="degraded",
        critical_system=0,
        advisory_system=2,
        failed_user=0,
        critical_names=(),
        advisory_names=("systemd-pcrproduct.service", "systemd-pcrlogin@1000.service"),
        user_names=(),
    )
    state = next(check for check in checks if check.id == "systemd_state")
    assert state.status == "warning"
    assert not any(check.status == "critical" for check in checks)


def test_system_info_still_supports_legacy_callers_without_diagnostics():
    info = SystemInfo(
        os_name="Arch Linux",
        kernel="test",
        plasma_version="test",
        disk=_disk(free_gib=4, used_percent=96.0),
        failed_system_services=0,
        failed_user_services=0,
    )
    assert info.needs_attention is True
