from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .command import command_exists, run_command


KIB = 1024
MIB = 1024 ** 2
GIB = 1024 ** 3

STATUS_ORDER = {
    "ok": 0,
    "info": 1,
    "unknown": 2,
    "warning": 3,
    "critical": 4,
}


@dataclass(frozen=True)
class DiagnosticCheck:
    id: str
    category: str
    title: str
    status: str
    summary: str
    details: tuple[str, ...] = ()
    extended_only: bool = False

    @property
    def severity(self) -> int:
        return STATUS_ORDER.get(self.status, STATUS_ORDER["unknown"])


@dataclass(frozen=True)
class MemorySnapshot:
    total_bytes: int | None
    available_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
    zram_active: bool


def _detail_lines(text: str, *, limit: int = 30) -> tuple[str, ...]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return tuple(lines[:limit])


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def check_root_mount() -> DiagnosticCheck:
    result = run_command(["findmnt", "-no", "OPTIONS", "/"], timeout=5)
    if result.returncode != 0 or not result.stdout.strip():
        return DiagnosticCheck(
            "root_mount",
            "storage",
            "Корневая файловая система",
            "unknown",
            "Не удалось определить режим монтирования",
            _detail_lines(result.stderr),
        )
    options = {part.strip() for part in result.stdout.strip().split(",") if part.strip()}
    if "ro" in options and "rw" not in options:
        return DiagnosticCheck(
            "root_mount",
            "storage",
            "Корневая файловая система",
            "critical",
            "Смонтирована только для чтения",
            (result.stdout.strip(),),
        )
    return DiagnosticCheck(
        "root_mount",
        "storage",
        "Корневая файловая система",
        "ok",
        "Доступна для записи",
        (result.stdout.strip(),),
    )


def check_btrfs_device_stats(filesystem: str) -> DiagnosticCheck:
    if filesystem.lower() != "btrfs":
        return DiagnosticCheck(
            "btrfs_errors",
            "storage",
            "Btrfs",
            "info",
            "Корень использует другую файловую систему",
        )

    result = run_command(["btrfs", "device", "stats", "/"], timeout=8)
    if not result.available:
        return DiagnosticCheck(
            "btrfs_errors",
            "storage",
            "Btrfs",
            "unknown",
            "Утилита btrfs недоступна",
        )
    if result.returncode != 0:
        details = _detail_lines(result.stderr or result.stdout)
        summary = "Счётчики ошибок недоступны"
        if "permission" in (result.stderr or "").lower() or "operation not permitted" in (result.stderr or "").lower():
            summary = "Счётчики ошибок недоступны без дополнительных прав"
        return DiagnosticCheck(
            "btrfs_errors",
            "storage",
            "Btrfs",
            "unknown",
            summary,
            details,
        )

    counters: dict[str, int] = {}
    pattern = re.compile(
        r"\.(write_io_errs|read_io_errs|flush_io_errs|corruption_errs|generation_errs)\s+(\d+)"
    )
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        counters[match.group(1)] = counters.get(match.group(1), 0) + int(match.group(2))

    if not counters:
        return DiagnosticCheck(
            "btrfs_errors",
            "storage",
            "Btrfs",
            "unknown",
            "Не удалось разобрать счётчики ошибок",
            _detail_lines(result.stdout or result.stderr, limit=20),
        )

    nonzero = {name: value for name, value in counters.items() if value > 0}
    if nonzero:
        label = ", ".join(f"{name}={value}" for name, value in sorted(nonzero.items()))
        return DiagnosticCheck(
            "btrfs_errors",
            "storage",
            "Btrfs",
            "critical",
            "Обнаружены накопленные ошибки устройства",
            (label,) + _detail_lines(result.stdout, limit=20),
        )
    return DiagnosticCheck(
        "btrfs_errors",
        "storage",
        "Btrfs",
        "ok",
        "Ошибок устройств не обнаружено",
        _detail_lines(result.stdout, limit=10),
    )


def _memory_snapshot() -> MemorySnapshot:
    text = _read_text("/proc/meminfo") or ""
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        match = re.search(r"(\d+)", raw)
        if match:
            values[key] = int(match.group(1)) * KIB

    swaps = _read_text("/proc/swaps") or ""
    zram_active = any("/zram" in line for line in swaps.splitlines()[1:])
    return MemorySnapshot(
        total_bytes=values.get("MemTotal"),
        available_bytes=values.get("MemAvailable"),
        swap_total_bytes=values.get("SwapTotal"),
        swap_free_bytes=values.get("SwapFree"),
        zram_active=zram_active,
    )


def check_memory_available() -> DiagnosticCheck:
    snapshot = _memory_snapshot()
    if snapshot.total_bytes is None or snapshot.available_bytes is None or snapshot.total_bytes <= 0:
        return DiagnosticCheck(
            "memory_available",
            "memory",
            "Оперативная память",
            "unknown",
            "Не удалось прочитать /proc/meminfo",
        )
    available_pct = snapshot.available_bytes / snapshot.total_bytes * 100.0
    gib = snapshot.available_bytes / GIB
    if available_pct < 4.0 and snapshot.available_bytes < 1 * GIB:
        status = "critical"
    elif available_pct < 10.0 and snapshot.available_bytes < 2 * GIB:
        status = "warning"
    else:
        status = "ok"
    return DiagnosticCheck(
        "memory_available",
        "memory",
        "Оперативная память",
        status,
        f"Доступно {gib:.1f} ГиБ ({available_pct:.0f}%)",
    )


def check_swap() -> DiagnosticCheck:
    snapshot = _memory_snapshot()
    if snapshot.swap_total_bytes is None:
        return DiagnosticCheck("swap", "memory", "Swap / ZRAM", "unknown", "Не удалось определить")
    if snapshot.swap_total_bytes <= 0:
        return DiagnosticCheck("swap", "memory", "Swap / ZRAM", "info", "Swap не настроен")
    used = snapshot.swap_total_bytes - (snapshot.swap_free_bytes or 0)
    suffix = " · ZRAM активен" if snapshot.zram_active else ""
    return DiagnosticCheck(
        "swap",
        "memory",
        "Swap / ZRAM",
        "ok",
        f"Используется {used / GIB:.1f} из {snapshot.swap_total_bytes / GIB:.1f} ГиБ{suffix}",
    )


def check_memory_pressure() -> DiagnosticCheck:
    text = _read_text("/proc/pressure/memory")
    if not text:
        return DiagnosticCheck(
            "memory_pressure",
            "memory",
            "Давление памяти",
            "unknown",
            "PSI недоступен",
        )
    full_line = next((line for line in text.splitlines() if line.startswith("full ")), "")
    match = re.search(r"avg10=([0-9.]+)", full_line)
    if not match:
        return DiagnosticCheck(
            "memory_pressure",
            "memory",
            "Давление памяти",
            "unknown",
            "Не удалось разобрать PSI",
            _detail_lines(text),
        )
    avg10 = float(match.group(1))
    if avg10 >= 30.0:
        status = "critical"
    elif avg10 >= 10.0:
        status = "warning"
    else:
        status = "ok"
    return DiagnosticCheck(
        "memory_pressure",
        "memory",
        "Давление памяти",
        status,
        f"PSI full avg10: {avg10:.2f}%",
        _detail_lines(text),
    )


def check_fstab() -> DiagnosticCheck:
    result = run_command(["findmnt", "--verify", "--verbose"], timeout=10)
    details = _detail_lines((result.stdout + "\n" + result.stderr).strip(), limit=30)
    if not result.available:
        return DiagnosticCheck("fstab", "integrity", "/etc/fstab", "unknown", "findmnt недоступен")
    if result.returncode == 0:
        return DiagnosticCheck("fstab", "integrity", "/etc/fstab", "ok", "Ошибок конфигурации не обнаружено", details)
    return DiagnosticCheck("fstab", "integrity", "/etc/fstab", "warning", "Обнаружены проблемы проверки", details)


def check_pacman_database() -> DiagnosticCheck:
    result = run_command(["pacman", "-Dk"], timeout=20)
    details = _detail_lines((result.stdout + "\n" + result.stderr).strip(), limit=30)
    if not result.available:
        return DiagnosticCheck("pacman_db", "integrity", "База pacman", "unknown", "pacman недоступен")
    if result.returncode == 0:
        return DiagnosticCheck("pacman_db", "integrity", "База pacman", "ok", "Локальная база пакетов целостна", details)
    return DiagnosticCheck("pacman_db", "integrity", "База pacman", "warning", "Проверка базы выявила проблему", details)


def check_time_sync() -> DiagnosticCheck:
    result = run_command(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not value:
        return DiagnosticCheck("time_sync", "boot", "Системное время", "unknown", "Состояние синхронизации недоступно", _detail_lines(result.stderr))
    if value == "yes":
        return DiagnosticCheck("time_sync", "boot", "Системное время", "ok", "Синхронизировано")
    return DiagnosticCheck("time_sync", "boot", "Системное время", "warning", "NTP-синхронизация не подтверждена")


def _bootctl_current_product(status_text: str) -> str | None:
    marker = "Current Boot Loader:"
    if marker not in status_text:
        return None
    block = status_text.split(marker, 1)[1]
    if "Current Stub:" in block:
        block = block.split("Current Stub:", 1)[0]
    match = re.search(r"^\s*Product:\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def check_boot_environment() -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    uefi = Path("/sys/firmware/efi").exists()
    checks.append(
        DiagnosticCheck(
            "firmware",
            "boot",
            "Режим загрузки",
            "ok" if uefi else "info",
            "UEFI" if uefi else "Legacy/BIOS",
        )
    )

    if not command_exists("bootctl"):
        checks.extend(
            (
                DiagnosticCheck("bootloader", "boot", "systemd-boot", "unknown", "bootctl недоступен"),
                DiagnosticCheck("secure_boot", "boot", "Secure Boot", "unknown", "bootctl недоступен"),
            )
        )
        return tuple(checks)

    # bootctl status can still report the running loader through EFI variables even
    # when the ESP itself is mounted with restrictive permissions. Do not turn an
    # ESP Permission denied into a false "systemd-boot is not active" warning.
    status = run_command(["bootctl", "status", "--no-pager"], timeout=8)
    status_text = status.stdout or ""
    product = _bootctl_current_product(status_text)
    if product and "systemd-boot" in product.lower():
        current_entry = re.search(r"^\s*Current Entry:\s*(.+)$", status_text, re.MULTILINE)
        details = [f"Current Boot Loader: {product}"]
        if current_entry:
            details.append(f"Current Entry: {current_entry.group(1).strip()}")
        checks.append(
            DiagnosticCheck(
                "bootloader",
                "boot",
                "systemd-boot",
                "ok",
                f"Активен: {product}",
                tuple(details),
            )
        )
    elif product:
        checks.append(
            DiagnosticCheck(
                "bootloader",
                "boot",
                "systemd-boot",
                "info",
                f"Активный загрузчик: {product}",
            )
        )
    else:
        installed = run_command(["bootctl", "is-installed"], timeout=5)
        if installed.returncode == 0:
            checks.append(DiagnosticCheck("bootloader", "boot", "systemd-boot", "ok", "Установлен"))
        else:
            permission_denied = "permission denied" in ((installed.stderr or "") + (status.stderr or "")).lower()
            summary = (
                "Не удалось проверить файлы ESP из-за прав доступа"
                if permission_denied
                else "Активный загрузчик не определён"
            )
            checks.append(DiagnosticCheck("bootloader", "boot", "systemd-boot", "unknown", summary))

    match = re.search(r"Secure Boot:\s*([^\n]+)", status_text, re.IGNORECASE)
    if match:
        secure = match.group(1).strip()
        enabled = secure.lower().startswith("enabled")
        if enabled:
            secure_summary = "Включён"
        elif "setup" in secure.lower():
            secure_summary = "Выключен (режим настройки UEFI)"
        else:
            secure_summary = "Выключен"
        checks.append(
            DiagnosticCheck(
                "secure_boot",
                "boot",
                "Secure Boot",
                "ok" if enabled else "info",
                secure_summary,
                (f"bootctl: {secure}",) if enabled else (
                    f"bootctl: {secure}",
                    "Отключённый Secure Boot — выбранная настройка UEFI, а не неисправность Linux.",
                ),
            )
        )
    else:
        checks.append(DiagnosticCheck("secure_boot", "boot", "Secure Boot", "unknown", "Состояние не определено"))
    return tuple(checks)


def check_tpm2() -> DiagnosticCheck:
    if not command_exists("systemd-analyze"):
        return DiagnosticCheck("tpm2", "boot", "TPM 2.0", "unknown", "systemd-analyze недоступен")
    result = run_command(["systemd-analyze", "has-tpm2"], timeout=6)
    text = (result.stdout or result.stderr).strip()
    lowered = text.lower()
    summary = text.splitlines()[0].strip() if text else ""
    details = _detail_lines(text, limit=12)
    if result.returncode == 0 and any(token in lowered for token in ("yes", "full")):
        return DiagnosticCheck("tpm2", "boot", "TPM 2.0", "ok", "Доступен", details)
    if result.returncode == 0 and any(token in lowered for token in ("partial", "firmware", "system")):
        return DiagnosticCheck("tpm2", "boot", "TPM 2.0", "info", summary or "Доступен частично", details)
    return DiagnosticCheck("tpm2", "boot", "TPM 2.0", "info", summary or "Не обнаружен", details)


def check_kernel_taint() -> DiagnosticCheck:
    text = _read_text("/proc/sys/kernel/tainted")
    if text is None:
        return DiagnosticCheck("kernel_taint", "integrity", "Состояние ядра", "unknown", "Параметр tainted недоступен")
    try:
        value = int(text.strip())
    except ValueError:
        return DiagnosticCheck("kernel_taint", "integrity", "Состояние ядра", "unknown", "Не удалось разобрать tainted")
    if value == 0:
        return DiagnosticCheck("kernel_taint", "integrity", "Состояние ядра", "ok", "Признаков taint нет")

    details = [f"Kernel taint mask: {value}"]
    if value & 512:
        details.append("bit 9 (512), W: ядро выдало warning; это след события, а не отдельная неисправность.")
    remaining = value & ~512
    if remaining:
        details.append(f"Кроме W присутствуют другие taint-биты: {remaining}. Их стоит разбирать отдельно.")
    summary = "Предупреждение ядра (W)" if value == 512 else f"Диагностическая маска ядра: {value}"
    return DiagnosticCheck(
        "kernel_taint",
        "integrity",
        "Состояние ядра",
        "info",
        summary,
        tuple(details),
    )


_CRITICAL_JOURNAL_RE = re.compile(
    r"(?:BTRFS.*(?:error|corrupt|critical)|I/O error|blk_update_request|nvme.*(?:critical|I/O error)|"
    r"EXT4-fs error|XFS.*(?:corrupt|error)|kernel panic|\bOops:|\bBUG:|machine check|\bMCE\b)",
    re.IGNORECASE,
)
_OOM_RE = re.compile(r"(?:out of memory|oom-kill|killed process)", re.IGNORECASE)
_ACPI_RE = re.compile(r"(?:ACPI BIOS Error|ACPI Error:)", re.IGNORECASE)
_TDX_RE = re.compile(r"TDX not supported", re.IGNORECASE)
_NVPCR_RE = re.compile(
    r"(?:NvPCR|Early TPM SRK Setup|TPM NvPCR|systemd-tpm2-setup|systemd-pcrextend)",
    re.IGNORECASE,
)
_SMBUS_RE = re.compile(r"i801_smbus.*SMBus is busy", re.IGNORECASE)
_WIFI_CAPABILITY_RE = re.compile(r"wpa_supplicant.*multicast RX registrations are not supported", re.IGNORECASE)


def detect_known_nvpcr_regression(advisory_names: tuple[str, ...]) -> bool:
    names = set(advisory_names)
    has_core_units = {
        "systemd-pcrproduct.service",
        "systemd-tpm2-setup-early.service",
    }.issubset(names)
    has_login = any(name.startswith("systemd-pcrlogin@") for name in names)
    if not (has_core_units and has_login):
        return False

    # systemd itself normally lives in /usr/lib/systemd and is not guaranteed to
    # be in PATH. systemctl is always available in a functioning systemd session.
    version = run_command(["systemctl", "--version"], timeout=5)
    match = re.search(r"systemd\s+(\d+)", version.stdout or version.stderr, re.IGNORECASE)
    if not match:
        package = run_command(["pacman", "-Q", "systemd"], timeout=5)
        match = re.search(r"\bsystemd\s+(\d+)", package.stdout or package.stderr, re.IGNORECASE)
    if not match or int(match.group(1)) != 262:
        return False

    journal = run_command(
        ["journalctl", "-b", "-p", "err", "--no-pager", "-n", "220"],
        timeout=8,
    )
    journal_text = journal.stdout + "\n" + journal.stderr
    return "No such file or directory" in journal_text and bool(_NVPCR_RE.search(journal_text))


def check_boot_journal(
    *,
    extended: bool = False,
    known_nvpcr_regression: bool = False,
) -> tuple[DiagnosticCheck, DiagnosticCheck]:
    limit = "500" if extended else "180"
    result = run_command(
        ["journalctl", "-b", "-p", "err", "--no-pager", "--output=short-monotonic", "-n", limit],
        timeout=15 if extended else 8,
    )
    if not result.available or result.returncode != 0:
        details = _detail_lines(result.stderr or result.stdout)
        unknown = DiagnosticCheck("journal_errors", "journal", "Журнал текущей загрузки", "unknown", "Журнал недоступен", details)
        oom = DiagnosticCheck("oom_events", "memory", "OOM-события", "unknown", "Не удалось проверить журнал")
        return unknown, oom

    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and "-- No entries --" not in line
    ]
    critical_lines = [line for line in lines if _CRITICAL_JOURNAL_RE.search(line)]
    oom_lines = [line for line in lines if _OOM_RE.search(line)]
    acpi_lines = [line for line in lines if _ACPI_RE.search(line)]
    tdx_lines = [line for line in lines if _TDX_RE.search(line)]
    nvpcr_lines = [line for line in lines if _NVPCR_RE.search(line)]
    smbus_lines = [line for line in lines if _SMBUS_RE.search(line)]
    wifi_lines = [line for line in lines if _WIFI_CAPABILITY_RE.search(line)]

    known_lines = set(critical_lines + oom_lines + acpi_lines + tdx_lines + nvpcr_lines + smbus_lines + wifi_lines)
    other_lines = [line for line in lines if line not in known_lines]

    warning_categories = 0
    info_categories = 0
    details: list[str] = []

    if acpi_lines:
        warning_categories += 1
        details.append(f"ACPI / BIOS: {len(acpi_lines)} сообщений — сгруппировано как одно предупреждение прошивки.")
        details.extend(f"    {line}" for line in acpi_lines[:4])
    if nvpcr_lines:
        if known_nvpcr_regression:
            info_categories += 1
            details.append(
                f"TPM/NvPCR: {len(nvpcr_lines)} сообщений — известная группа systemd 262; не считается критической неисправностью."
            )
        else:
            warning_categories += 1
            details.append(f"TPM/NvPCR: {len(nvpcr_lines)} сообщений — причина не подтверждена как известная регрессия.")
        details.extend(f"    {line}" for line in nvpcr_lines[:4])
    if tdx_lines:
        info_categories += 1
        details.append(f"Intel TDX: {len(tdx_lines)} сообщений — неподдерживаемая функция, информационно.")
    if smbus_lines:
        info_categories += 1
        details.append(f"SMBus: {len(smbus_lines)} повторов busy — низкоприоритетное аппаратно-прошивочное сообщение.")
        details.extend(f"    {line}" for line in smbus_lines[:2])
    if wifi_lines:
        info_categories += 1
        details.append(f"Wi-Fi / nl80211: {len(wifi_lines)} сообщений о неподдерживаемой возможности драйвера.")
    if other_lines:
        warning_categories += 1
        details.append(f"Прочие err-сообщения: {len(other_lines)} — требуют просмотра, так как пока не классифицированы.")
        details.extend(f"    {line}" for line in other_lines[:8])

    if critical_lines:
        journal_status = "critical"
        details.insert(0, f"Критические сообщения: {len(critical_lines)}")
        details[1:1] = [f"    {line}" for line in critical_lines[:8]]
    elif warning_categories:
        journal_status = "warning"
    elif info_categories:
        journal_status = "info"
    else:
        journal_status = "ok"

    if not lines:
        summary = "Ошибок уровня err нет"
    elif critical_lines:
        summary = (
            f"Критических ошибок: {len(critical_lines)} · предупреждений: {warning_categories} · "
            f"информационных групп: {info_categories}"
        )
    elif warning_categories == 1 and acpi_lines and not other_lines and not (nvpcr_lines and not known_nvpcr_regression):
        summary = (
            f"Критических ошибок нет · 1 предупреждение BIOS/ACPI · "
            f"информационных групп: {info_categories}"
        )
    elif warning_categories:
        summary = (
            f"Критических ошибок нет · предупреждений: {warning_categories} · "
            f"информационных групп: {info_categories}"
        )
    else:
        summary = f"Критических ошибок нет · информационных групп: {info_categories}"
    journal = DiagnosticCheck(
        "journal_errors",
        "journal",
        "Журнал текущей загрузки",
        journal_status,
        summary,
        tuple(details[:40]),
    )

    if oom_lines:
        oom = DiagnosticCheck(
            "oom_events",
            "memory",
            "OOM-события",
            "warning",
            f"Обнаружено: {len(oom_lines)}",
            tuple(oom_lines[:20]),
        )
    else:
        oom = DiagnosticCheck("oom_events", "memory", "OOM-события", "ok", "После загрузки не обнаружены")
    return journal, oom


def check_smart_devices() -> DiagnosticCheck:
    if not command_exists("smartctl"):
        return DiagnosticCheck(
            "smart",
            "storage",
            "SMART / NVMe",
            "unknown",
            "smartmontools не установлен",
            extended_only=True,
        )
    devices_result = run_command(["lsblk", "-dn", "-o", "PATH,TYPE"], timeout=5)
    if devices_result.returncode != 0:
        return DiagnosticCheck("smart", "storage", "SMART / NVMe", "unknown", "Не удалось получить список дисков", extended_only=True)

    devices: list[str] = []
    for line in devices_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk":
            devices.append(parts[0])
    if not devices:
        return DiagnosticCheck("smart", "storage", "SMART / NVMe", "unknown", "Физические диски не обнаружены", extended_only=True)

    problems: list[str] = []
    good: list[str] = []
    unavailable: list[str] = []
    for device in devices:
        result = run_command(["smartctl", "-H", "-A", device], timeout=12)
        text = (result.stdout + "\n" + result.stderr).strip()
        lowered = text.lower()
        if "permission denied" in lowered or "operation not permitted" in lowered or "smartctl open device" in lowered and result.returncode != 0:
            unavailable.append(device)
            continue
        failed = any(
            token in lowered
            for token in (
                "overall-health self-assessment test result: failed",
                "smart health status: failed",
            )
        )
        warning_match = re.search(r"critical warning:\s*(0x[0-9a-f]+|\d+)", text, re.IGNORECASE)
        if warning_match:
            raw_warning = warning_match.group(1)
            base = 16 if raw_warning.lower().startswith("0x") else 10
            failed = failed or int(raw_warning, base) != 0
        media_match = re.search(r"media and data integrity errors:\s*([0-9,]+)", text, re.IGNORECASE)
        if media_match and int(media_match.group(1).replace(",", "")) > 0:
            failed = True
        if failed:
            problems.append(f"{device}: SMART/NVMe сообщает проблему")
        elif result.returncode == 0 or "passed" in lowered or "smart health status: ok" in lowered:
            good.append(f"{device}: состояние нормально")
        else:
            unavailable.append(device)

    details = tuple(good + problems + [f"{dev}: данные недоступны" for dev in unavailable])
    if problems:
        return DiagnosticCheck("smart", "storage", "SMART / NVMe", "critical", f"Проблемных устройств: {len(problems)}", details, True)
    if good and not unavailable:
        return DiagnosticCheck("smart", "storage", "SMART / NVMe", "ok", f"Проверено устройств: {len(good)}", details, True)
    if good:
        return DiagnosticCheck("smart", "storage", "SMART / NVMe", "unknown", f"Проверено {len(good)}, недоступно {len(unavailable)}", details, True)
    return DiagnosticCheck("smart", "storage", "SMART / NVMe", "unknown", "Данные недоступны без дополнительных прав", details, True)


def build_disk_space_check(free_bytes: int, percent_used: float) -> DiagnosticCheck:
    if free_bytes < 5 * GIB or percent_used >= 95.0:
        status = "critical"
        summary = f"Критически мало места · {percent_used:.0f}% занято"
    elif free_bytes < 10 * GIB or percent_used >= 90.0:
        status = "warning"
        summary = f"Свободное место заканчивается · {percent_used:.0f}% занято"
    else:
        status = "ok"
        summary = f"Свободного места достаточно · {percent_used:.0f}% занято"
    return DiagnosticCheck("disk_space", "storage", "Свободное место", status, summary)


def build_service_checks(
    *,
    systemd_state: str | None,
    critical_system: int | None,
    advisory_system: int,
    failed_user: int | None,
    critical_names: tuple[str, ...],
    advisory_names: tuple[str, ...],
    user_names: tuple[str, ...],
    known_nvpcr_regression: bool = False,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    normalized = (systemd_state or "").strip().lower()
    if not normalized:
        checks.append(DiagnosticCheck("systemd_state", "services", "Состояние systemd", "unknown", "Не удалось определить"))
    elif normalized == "running":
        checks.append(DiagnosticCheck("systemd_state", "services", "Состояние systemd", "ok", "running"))
    elif normalized == "degraded" and (critical_system or 0) == 0 and (failed_user or 0) == 0:
        if known_nvpcr_regression:
            checks.append(
                DiagnosticCheck(
                    "systemd_state",
                    "services",
                    "Состояние systemd",
                    "info",
                    "Работает; degraded вызван только TPM/NvPCR",
                    (
                        "Критических failed unit-ов нет.",
                        "Подтверждена известная группа NvPCR в systemd 262; это не считается общей неисправностью системы.",
                    ),
                )
            )
        else:
            checks.append(DiagnosticCheck("systemd_state", "services", "Состояние systemd", "warning", "degraded только из-за информационных unit-ов"))
    elif normalized in {"starting", "initializing", "stopping"}:
        checks.append(DiagnosticCheck("systemd_state", "services", "Состояние systemd", "warning", normalized))
    else:
        checks.append(DiagnosticCheck("systemd_state", "services", "Состояние systemd", "critical", normalized))

    if critical_system is None:
        checks.append(DiagnosticCheck("failed_system_services", "services", "Системные службы", "unknown", "Не удалось проверить"))
    elif critical_system:
        checks.append(DiagnosticCheck("failed_system_services", "services", "Системные службы", "critical", f"Сбойных: {critical_system}", critical_names))
    else:
        checks.append(DiagnosticCheck("failed_system_services", "services", "Системные службы", "ok", "Сбойных критических служб нет"))

    if advisory_system:
        if known_nvpcr_regression:
            summary = f"Известная проблема systemd 262 · служб: {advisory_system}"
            details = advisory_names + ("No such file or directory подтверждён в журнале текущей загрузки.",)
        else:
            summary = f"Информационных сбоев: {advisory_system}"
            details = advisory_names
        checks.append(DiagnosticCheck("service_advisories", "services", "TPM/NvPCR-службы", "info", summary, details))
    else:
        checks.append(DiagnosticCheck("service_advisories", "services", "TPM/NvPCR-службы", "ok", "Сбоев нет"))

    if failed_user is None:
        checks.append(DiagnosticCheck("failed_user_services", "services", "Пользовательские службы", "unknown", "Не удалось проверить"))
    elif failed_user:
        checks.append(DiagnosticCheck("failed_user_services", "services", "Пользовательские службы", "critical", f"Сбойных: {failed_user}", user_names))
    else:
        checks.append(DiagnosticCheck("failed_user_services", "services", "Пользовательские службы", "ok", "Сбойных служб нет"))
    return tuple(checks)
