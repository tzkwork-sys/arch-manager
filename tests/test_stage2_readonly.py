from pathlib import Path

from src.core.formatting import format_bytes
from src.core.maintenance import parse_size_to_bytes
from src.core.restore_points import _parse_snapper_datetime


ROOT = Path(__file__).resolve().parents[1]


def test_required_stage2_backend_exists():
    required = [
        ROOT / "src" / "core" / "command.py",
        ROOT / "src" / "core" / "dashboard.py",
        ROOT / "src" / "core" / "system_info.py",
        ROOT / "src" / "core" / "updates.py",
        ROOT / "src" / "core" / "restore_points.py",
        ROOT / "src" / "core" / "maintenance.py",
        ROOT / "docs" / "STAGE_2.md",
    ]
    assert all(path.is_file() for path in required)


def test_stage2_runtime_has_no_privilege_escalation_or_mutating_package_commands():
    # Stage 4 intentionally has a separate Polkit executor.  Keep the original
    # Stage 2 read-only backends protected without forbidding later modules.
    stage2_files = (
        "command.py",
        "dashboard.py",
        "system_info.py",
        "updates.py",
        "restore_points.py",
        "maintenance.py",
    )
    source = "\n".join(
        (ROOT / "src" / "core" / filename).read_text(encoding="utf-8")
        for filename in stage2_files
    )
    assert "sudo " not in source
    assert "pkexec" not in source
    assert "pacman -S" not in source
    assert "pacman -R" not in source


def test_human_size_parser():
    assert parse_size_to_bytes("1.5 GiB") == int(1.5 * 1024**3)
    assert parse_size_to_bytes("368.0M") == 368_000_000
    assert parse_size_to_bytes("0 B") == 0
    assert parse_size_to_bytes("n/a") is None


def test_byte_formatter_is_user_friendly():
    assert format_bytes(0) == "0 Б"
    assert format_bytes(1024**3) == "1.00 ГиБ"


def test_snapper_date_parser_accepts_iso_output():
    parsed = _parse_snapper_datetime("2026-09-21 11:42:03 +0300")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 21


def _summary_for_status(
    *,
    failed_system=0,
    failed_user=0,
    official_updates=0,
    aur_updates=0,
    restore_count=1,
    restore_readable=True,
    cache=0,
    orphans=0,
    journal=1000,
    config_files=0,
):
    from datetime import datetime

    from src.core.dashboard import DashboardSummary
    from src.core.maintenance import MaintenanceSummary
    from src.core.restore_points import RestorePointsSummary
    from src.core.system_info import DiskInfo, SystemInfo
    from src.core.updates import UpdateSourceSummary, UpdatesSummary

    return DashboardSummary(
        checked_at=datetime.now().astimezone(),
        system=SystemInfo(
            os_name="Arch Linux",
            kernel="test",
            plasma_version="test",
            disk=DiskInfo(
                total_bytes=100 * 1024**3,
                used_bytes=20 * 1024**3,
                free_bytes=80 * 1024**3,
                percent_used=20.0,
                filesystem="btrfs",
            ),
            failed_system_services=failed_system,
            failed_user_services=failed_user,
        ),
        updates=UpdatesSummary(
            UpdateSourceSummary(official_updates, True),
            UpdateSourceSummary(aur_updates, True),
        ),
        restore_points=RestorePointsSummary(
            restore_count,
            None,
            True,
            restore_readable,
            None,
        ),
        maintenance=MaintenanceSummary(cache, orphans, journal, config_files),
    )


def test_overall_status_distinguishes_ok_review_and_attention():
    assert _summary_for_status().overall_status == "Всё в порядке"
    assert _summary_for_status(restore_count=None, restore_readable=False).overall_status == "Есть что проверить"
    assert _summary_for_status(official_updates=2).overall_status == "Есть что проверить"
    assert _summary_for_status(failed_system=1).overall_status == "Требуется внимание"


def test_stage2_gui_has_predictable_return_to_overview_and_no_internal_stage_wording():
    gui_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "gui").glob("*.py")
    )
    assert "Назад к обзору" not in gui_source
    assert "Alt+Left" in gui_source
    assert "На этапе 2" not in gui_source
    assert "Этап 2" not in gui_source


def test_stage2_internal_summary_pages_show_last_check_time():
    for filename in (
        "updates_page.py",
        "restore_points_page.py",
        "maintenance_page.py",
        "system_page.py",
    ):
        source = (ROOT / "src" / "gui" / filename).read_text(encoding="utf-8")
        assert "checked" in source.lower() or "Последняя проверка" in source


def test_tpm_nvpcr_failures_are_advisory_not_critical():
    from src.core.system_info import DiskInfo, SystemInfo

    system = SystemInfo(
        os_name="Arch Linux",
        kernel="test",
        plasma_version="test",
        disk=DiskInfo(
            total_bytes=100 * 1024**3,
            used_bytes=20 * 1024**3,
            free_bytes=80 * 1024**3,
            percent_used=20.0,
            filesystem="btrfs",
        ),
        failed_system_services=4,
        failed_user_services=0,
        failed_system_unit_names=(
            "systemd-pcrlogin@1000.service",
            "systemd-pcrlogin@965.service",
            "systemd-pcrproduct.service",
            "systemd-tpm2-setup-early.service",
        ),
    )

    assert system.advisory_system_services == 4
    assert system.critical_failed_system_services == 0
    assert system.has_service_advisories is True
    assert system.needs_attention is False


def test_real_failed_system_service_remains_critical_alongside_tpm_warning():
    from src.core.system_info import DiskInfo, SystemInfo

    system = SystemInfo(
        os_name="Arch Linux",
        kernel="test",
        plasma_version="test",
        disk=DiskInfo(
            total_bytes=100 * 1024**3,
            used_bytes=20 * 1024**3,
            free_bytes=80 * 1024**3,
            percent_used=20.0,
            filesystem="btrfs",
        ),
        failed_system_services=2,
        failed_user_services=0,
        failed_system_unit_names=(
            "systemd-pcrproduct.service",
            "NetworkManager.service",
        ),
    )

    assert system.advisory_system_services == 1
    assert system.critical_failed_system_services == 1
    assert system.needs_attention is True

