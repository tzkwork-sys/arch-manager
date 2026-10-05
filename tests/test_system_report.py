from datetime import datetime, timezone

from src.core.command import CommandResult
from src.core.system_diagnostics import DiagnosticCheck
from src.core.system_info import DiskInfo, SystemInfo
import src.core.system_report as report


def _system() -> SystemInfo:
    return SystemInfo(
        os_name="Arch Linux",
        kernel="7.2.7-arch1-1",
        plasma_version="6.7.5",
        disk=DiskInfo(
            total_bytes=154 * 1024**3,
            used_bytes=38 * 1024**3,
            free_bytes=116 * 1024**3,
            percent_used=25.0,
            filesystem="btrfs",
        ),
        failed_system_services=0,
        failed_user_services=0,
        diagnostics=(
            DiagnosticCheck("disk", "storage", "Свободное место", "ok", "Достаточно"),
            DiagnosticCheck("journal", "journal", "Журнал", "warning", "1 предупреждение", ("detail",)),
        ),
        extended_diagnostics=True,
    )


def test_report_is_extended_read_only_and_contains_diagnostic_context(monkeypatch):
    calls = []

    def fake_run(args, *, timeout=30.0, accepted_returncodes=None):
        calls.append(tuple(args))
        return CommandResult(tuple(args), 0, "sample output\n", "", available=True)

    monkeypatch.setattr(report, "run_command", fake_run)
    monkeypatch.setattr(report, "command_exists", lambda name: False if name == "smartctl" else True)
    monkeypatch.setattr(report, "_read_file", lambda path: "sample file")

    text = report.build_system_report(
        _system(),
        generated_at=datetime(2026, 9, 28, 18, 40, tzinfo=timezone.utc),
    )

    assert "ПОЛНЫЙ ОТЧЁТ РАЗДЕЛА «СИСТЕМА»" in text
    assert "## Результаты диагностических проверок" in text
    assert "[ПРЕДУПРЕЖДЕНИЕ] journal / Журнал" in text
    assert "## Расширенный вывод команд" in text
    assert "Ошибки журнала текущей загрузки" in text
    assert calls
    assert all(command[0] not in {"sudo", "pkexec"} for command in calls)


def test_report_redacts_common_personal_identifiers(monkeypatch):
    monkeypatch.setattr(report.getpass, "getuser", lambda: "oleg")
    monkeypatch.setattr(report.socket, "gethostname", lambda: "Tzenzik")
    monkeypatch.setattr(report.Path, "home", classmethod(lambda cls: report.Path("/home/oleg")))

    raw = (
        "/home/oleg/test Tzenzik oleg\n"
        "Serial Number: ABC123\n"
        "MAC 00:11:22:33:44:55 IP 192.168.1.5"
    )
    redacted = report._redact(raw)

    assert "/home/oleg" not in redacted
    assert "Tzenzik" not in redacted
    assert "ABC123" not in redacted
    assert "00:11:22:33:44:55" not in redacted
    assert "192.168.1.5" not in redacted


def test_report_prefers_privileged_smart_section(monkeypatch):
    calls = []

    def fake_run(args, *, timeout=30.0, accepted_returncodes=None):
        calls.append(tuple(args))
        return CommandResult(tuple(args), 0, "sample output\n", "", available=True)

    monkeypatch.setattr(report, "run_command", fake_run)
    monkeypatch.setattr(report, "_read_file", lambda path: "sample file")
    text = report.build_system_report(
        _system(),
        privileged_smart_report="### SMART / NVMe: защищённая проверка\n[OK] /dev/nvme0n1",
    )
    assert "[OK] /dev/nvme0n1" in text
    assert "SMART/NVMe в полной диагностике может читаться" in text
    assert all(command[0] != "smartctl" for command in calls)
