from __future__ import annotations

from datetime import datetime
import getpass
from pathlib import Path
import re
import shlex
import socket

from .command import CommandResult, command_exists, run_command
from .system_info import SystemInfo


REPORT_VERSION = 1
STATUS_LABELS = {
    "ok": "OK",
    "info": "INFO",
    "unknown": "НЕДОСТУПНО",
    "warning": "ПРЕДУПРЕЖДЕНИЕ",
    "critical": "КРИТИЧЕСКИ",
}

# The report is intentionally read-only. These commands never use sudo/pkexec
# and are limited to the areas surfaced by the System diagnostics page.
REPORT_COMMANDS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("Версия systemd", ("systemctl", "--version"), 5.0),
    ("Состояние systemd", ("systemctl", "is-system-running"), 6.0),
    ("Сбойные системные службы", ("systemctl", "--failed", "--no-pager"), 10.0),
    ("Сбойные пользовательские службы", ("systemctl", "--user", "--failed", "--no-pager"), 10.0),
    ("Корневая файловая система", ("findmnt", "-no", "SOURCE,TARGET,FSTYPE,OPTIONS", "/"), 5.0),
    ("Использование системного диска", ("df", "-hT", "/"), 5.0),
    ("Блочные устройства", ("lsblk", "-e7", "-o", "NAME,TYPE,SIZE,FSTYPE,FSVER,MOUNTPOINTS,MODEL"), 8.0),
    ("Btrfs: счётчики ошибок", ("btrfs", "device", "stats", "/"), 8.0),
    ("Btrfs: использование", ("btrfs", "filesystem", "usage", "-T", "/"), 10.0),
    ("Оперативная память", ("free", "-h"), 5.0),
    ("Swap", ("swapon", "--show", "--bytes"), 5.0),
    ("ZRAM", ("zramctl",), 5.0),
    ("Проверка /etc/fstab", ("findmnt", "--verify", "--verbose"), 10.0),
    ("Проверка базы pacman", ("pacman", "-Dk"), 20.0),
    ("Версии ключевых пакетов", ("pacman", "-Q", "systemd", "linux", "plasma-workspace"), 8.0),
    ("Загрузчик и Secure Boot", ("bootctl", "status", "--no-pager"), 8.0),
    ("TPM 2.0", ("systemd-analyze", "has-tpm2"), 6.0),
    ("Системное время", ("timedatectl", "status"), 6.0),
    ("Параметры ядра", ("uname", "-a"), 5.0),
    ("PCI-устройства и драйверы", ("lspci", "-nnk"), 12.0),
    ("USB-устройства", ("lsusb",), 8.0),
    ("Время загрузки", ("systemd-analyze", "time"), 8.0),
    ("Критическая цепочка загрузки", ("systemd-analyze", "critical-chain"), 10.0),
    (
        "Ошибки журнала текущей загрузки",
        ("journalctl", "-b", "-p", "err", "--no-pager", "--output=short-iso", "-n", "500"),
        18.0,
    ),
    (
        "Предупреждения ядра текущей загрузки",
        ("journalctl", "-b", "-k", "-p", "warning..alert", "--no-pager", "--output=short-iso", "-n", "500"),
        18.0,
    ),
)

REPORT_FILES: tuple[tuple[str, str], ...] = (
    ("/etc/os-release", "/etc/os-release"),
    ("Параметры загрузки ядра", "/proc/cmdline"),
    ("Сведения о памяти", "/proc/meminfo"),
    ("PSI памяти", "/proc/pressure/memory"),
    ("/etc/fstab", "/etc/fstab"),
    ("Kernel taint", "/proc/sys/kernel/tainted"),
)


def _redact(text: str) -> str:
    """Remove common personal/device identifiers while keeping diagnostic value."""
    if not text:
        return text

    redacted = text
    home = str(Path.home())
    if home and home != "/":
        redacted = redacted.replace(home, "~")

    try:
        username = getpass.getuser().strip()
    except OSError:  # pragma: no cover - unusual NSS failure
        username = ""
    if username and len(username) >= 2:
        redacted = re.sub(rf"(?<![\w.-]){re.escape(username)}(?![\w.-])", "<user>", redacted)

    try:
        hostname = socket.gethostname().strip()
    except OSError:  # pragma: no cover - unusual platform failure
        hostname = ""
    if hostname and len(hostname) >= 2:
        redacted = redacted.replace(hostname, "<host>")

    # Device serials and network addresses are not needed for this diagnostics section.
    redacted = re.sub(
        r"(?im)^(\s*(?:Serial Number|WWN Device Id|LU WWN Device Id|EUI-64|NGUID)\s*:\s*).+$",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "<mac>", redacted)
    redacted = re.sub(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        "<ipv4>",
        redacted,
    )
    return redacted


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"[недоступно: {exc}]"


def _command_block(title: str, result: CommandResult) -> str:
    command = shlex.join(result.command)
    meta: list[str] = [f"### {title}", f"Команда: {command}"]
    if not result.available:
        meta.append("Статус: команда недоступна")
    elif result.timed_out:
        meta.append("Статус: превышено время ожидания")
    else:
        meta.append(f"Код возврата: {result.returncode}")

    output_parts: list[str] = []
    stdout = _redact(result.stdout.strip())
    stderr = _redact(result.stderr.strip())
    if stdout:
        output_parts.append(stdout)
    if stderr:
        output_parts.append("[stderr]\n" + stderr)
    if not output_parts:
        output_parts.append("[вывод отсутствует]")
    return "\n".join(meta + ["", "\n".join(output_parts)])


def _diagnostic_summary(system: SystemInfo) -> str:
    counts = {
        "critical": len(system.critical_checks),
        "warning": len(system.warning_checks),
        "unknown": len(system.unknown_checks),
        "info": len(system.info_checks),
        "ok": sum(1 for check in system.diagnostics if check.status == "ok"),
    }
    lines = [
        f"Проверок: {len(system.diagnostics)}",
        f"OK: {counts['ok']}",
        f"Информационных: {counts['info']}",
        f"Недоступно: {counts['unknown']}",
        f"Предупреждений: {counts['warning']}",
        f"Критических: {counts['critical']}",
    ]
    return "\n".join(lines)


def _diagnostic_details(system: SystemInfo) -> str:
    lines: list[str] = []
    for check in system.diagnostics:
        label = STATUS_LABELS.get(check.status, check.status.upper())
        lines.append(f"[{label}] {check.category} / {check.title}: {check.summary}")
        for detail in check.details:
            lines.append(f"    {detail}")
    return _redact("\n".join(lines))


def _smart_blocks() -> list[str]:
    if not command_exists("smartctl"):
        return ["### SMART / NVMe: расширенный вывод\nСтатус: smartctl недоступен"]

    devices_result = run_command(["lsblk", "-dn", "-o", "PATH,TYPE"], timeout=6)
    if devices_result.returncode != 0:
        return [_command_block("SMART / NVMe: список физических дисков", devices_result)]

    devices: list[str] = []
    physical_device = re.compile(r"^/dev/(?:nvme\d+n\d+|sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|mmcblk\d+)$")
    for line in devices_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk" and physical_device.fullmatch(parts[0]):
            devices.append(parts[0])
    if not devices:
        return ["### SMART / NVMe: расширенный вывод\nФизические диски не обнаружены."]

    blocks: list[str] = []
    for device in devices:
        result = run_command(["smartctl", "-H", "-A", device], timeout=15)
        blocks.append(_command_block(f"SMART / NVMe: {device}", result))
    return blocks


def build_system_report(
    system: SystemInfo,
    *,
    generated_at: datetime | None = None,
    privileged_smart_report: str | None = None,
) -> str:
    """Build a shareable, read-only diagnostic report for the System page."""
    generated_at = generated_at or datetime.now().astimezone()
    timestamp = generated_at.astimezone().strftime("%d.%m.%Y %H:%M:%S %z")
    gib = 1024 ** 3

    sections: list[str] = [
        "ARCH MANAGER — ПОЛНЫЙ ОТЧЁТ РАЗДЕЛА «СИСТЕМА»",
        f"Версия формата: {REPORT_VERSION}",
        f"Сформирован: {timestamp}",
        "",
        "Отчёт создан для диагностики и загрузки в ChatGPT. Все проверки только читают состояние системы.",
        "SMART/NVMe в полной диагностике может читаться отдельным root-helper Arch Manager через Polkit; helper не изменяет накопители.",
        "Имена пользователя/хоста, серийные номера и сетевые адреса по возможности скрываются автоматически.",
        "",
        "## Краткая сводка",
        _diagnostic_summary(system),
        "",
        "## Сведения о системе",
        f"Операционная система: {system.os_name}",
        f"Ядро Linux: {system.kernel}",
        f"KDE Plasma: {system.plasma_version or 'не определена'}",
        f"Корневая ФС: {system.disk.filesystem}",
        f"Диск: {system.disk.free_bytes / gib:.1f} ГиБ свободно из {system.disk.total_bytes / gib:.1f} ГиБ "
        f"({system.disk.percent_used:.1f}% занято)",
        f"Сбойные systemd-службы: {system.failed_system_services if system.failed_system_services is not None else 'не определено'}",
        f"Сбойные пользовательские службы: {system.failed_user_services if system.failed_user_services is not None else 'не определено'}",
        f"Режим диагностики: {'полный' if system.extended_diagnostics else 'обычный'}",
        "",
        "## Результаты диагностических проверок",
        _diagnostic_details(system) or "Проверки отсутствуют.",
        "",
        "## Диагностические файлы",
    ]

    for title, path in REPORT_FILES:
        sections.extend((f"### {title}", _redact(_read_file(path)), ""))

    sections.append("## Расширенный вывод команд")
    for title, args, timeout in REPORT_COMMANDS:
        sections.extend((_command_block(title, run_command(list(args), timeout=timeout)), ""))

    if privileged_smart_report:
        sections.append(privileged_smart_report)
    else:
        sections.extend(_smart_blocks())
    sections.append("")
    sections.append("КОНЕЦ ОТЧЁТА")
    return "\n".join(sections).rstrip() + "\n"
