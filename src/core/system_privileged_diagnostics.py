from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from .system_diagnostics import DiagnosticCheck


PKEXEC_PATH = Path("/usr/bin/pkexec")
HELPER_PATH = Path("/usr/local/libexec/arch-manager/read-system-diagnostics")
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class SmartDiagnosticResult:
    check: DiagnosticCheck
    report_text: str


class SmartDiagnosticsError(RuntimeError):
    pass


class SmartDiagnosticsCancelled(SmartDiagnosticsError):
    pass


class SmartDiagnosticsUnavailable(SmartDiagnosticsError):
    pass


def _unknown(summary: str, *details: str) -> SmartDiagnosticResult:
    clean_details = tuple(detail for detail in details if detail)
    return SmartDiagnosticResult(
        DiagnosticCheck("smart", "storage", "SMART / NVMe", "unknown", summary, clean_details, True),
        "### SMART / NVMe: защищённая проверка\n" + summary + ("\n" + "\n".join(clean_details) if clean_details else ""),
    )


def _ensure_runtime_ready() -> None:
    if not PKEXEC_PATH.is_file() or not os.access(PKEXEC_PATH, os.X_OK):
        raise SmartDiagnosticsUnavailable("Не найден Polkit/pkexec.")
    try:
        if HELPER_PATH.is_symlink():
            raise SmartDiagnosticsUnavailable("Системный helper диагностики установлен небезопасно.")
        info = HELPER_PATH.stat()
    except FileNotFoundError as exc:
        raise SmartDiagnosticsUnavailable(
            "Системный helper SMART/NVMe не установлен. Запустите scripts/install-system-diagnostics-helper.sh."
        ) from exc
    except OSError as exc:
        raise SmartDiagnosticsUnavailable(f"Не удалось проверить helper SMART/NVMe: {exc}") from exc

    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0:
        raise SmartDiagnosticsUnavailable("Системный helper SMART/NVMe имеет небезопасный тип или владельца.")
    if info.st_mode & 0o022 or not info.st_mode & 0o111:
        raise SmartDiagnosticsUnavailable("Системный helper SMART/NVMe имеет небезопасные права доступа.")


def _run_helper(timeout: float) -> dict[str, Any]:
    _ensure_runtime_ready()
    command = (str(PKEXEC_PATH), "--user", "root", str(HELPER_PATH), "smart-json")
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
        raise SmartDiagnosticsUnavailable("Проверка SMART/NVMe не завершилась вовремя.") from exc
    except OSError as exc:
        raise SmartDiagnosticsUnavailable(f"Не удалось запустить защищённую проверку SMART/NVMe: {exc}") from exc

    detail = completed.stderr.strip()[:500]
    if completed.returncode == 126:
        raise SmartDiagnosticsCancelled("Авторизация для SMART/NVMe отменена.")
    if completed.returncode == 127:
        raise SmartDiagnosticsUnavailable("Polkit не выдал административное разрешение для SMART/NVMe.")
    if completed.returncode != 0:
        raise SmartDiagnosticsUnavailable(
            "Защищённая проверка SMART/NVMe завершилась с ошибкой" + (f": {detail}" if detail else ".")
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmartDiagnosticsUnavailable("Helper SMART/NVMe вернул повреждённые данные.") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise SmartDiagnosticsUnavailable("Helper SMART/NVMe вернул неподдерживаемый формат данных.")
    return payload


def _ival(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _device_label(item: dict[str, Any]) -> str:
    path = item.get("path") if isinstance(item.get("path"), str) else "устройство"
    model = item.get("model") if isinstance(item.get("model"), str) else ""
    return f"{path} ({model})" if model else path


def _device_lines(item: dict[str, Any]) -> tuple[str, ...]:
    label = _device_label(item)
    lines: list[str] = [label]
    protocol = item.get("protocol") if isinstance(item.get("protocol"), str) else ""
    if protocol:
        lines.append(f"  Протокол: {protocol}")
    passed = item.get("smart_passed")
    if isinstance(passed, bool):
        lines.append(f"  SMART health: {'PASSED' if passed else 'FAILED'}")

    values = (
        ("critical_warning", "Критическое предупреждение NVMe", None),
        ("media_errors", "Ошибки целостности носителя", None),
        ("percentage_used", "Ресурс NVMe использован", "%"),
        ("available_spare", "Доступный резерв NVMe", "%"),
        ("temperature_c", "Температура", " °C"),
        ("power_on_hours", "Наработка", " ч"),
        ("power_cycles", "Циклы включения", None),
        ("unsafe_shutdowns", "Небезопасные отключения", None),
        ("num_err_log_entries", "Записи журнала ошибок NVMe", None),
    )
    for key, title, suffix in values:
        value = _ival(item, key)
        if value is not None:
            lines.append(f"  {title}: {value}{suffix or ''}")

    attrs = item.get("ata_attributes")
    if isinstance(attrs, dict) and attrs:
        names = {
            "5": "Reallocated Sector Count",
            "187": "Reported Uncorrectable Errors",
            "188": "Command Timeout",
            "197": "Current Pending Sector Count",
            "198": "Offline Uncorrectable",
            "199": "UDMA CRC Error Count",
        }
        for attr_id in sorted(attrs, key=lambda value: int(value) if value.isdigit() else 9999):
            raw = attrs.get(attr_id)
            if isinstance(raw, int):
                lines.append(f"  ATA {attr_id} {names.get(attr_id, '')}: {raw}".rstrip())
    return tuple(lines)


def _classify_device(item: dict[str, Any]) -> str:
    if item.get("error"):
        return "unknown"
    if item.get("smart_passed") is False:
        return "critical"
    if (_ival(item, "critical_warning") or 0) != 0:
        return "critical"
    if (_ival(item, "media_errors") or 0) > 0:
        return "critical"

    attrs = item.get("ata_attributes")
    if isinstance(attrs, dict):
        for attr_id in ("5", "187", "197", "198"):
            value = attrs.get(attr_id)
            if isinstance(value, int) and value > 0:
                return "warning"

    used = _ival(item, "percentage_used")
    if used is not None and used >= 100:
        return "warning"
    return "ok"


def _build_result(payload: dict[str, Any]) -> SmartDiagnosticResult:
    top_error = payload.get("error")
    if isinstance(top_error, str) and top_error:
        return _unknown(top_error)

    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list):
        return _unknown("Helper SMART/NVMe не вернул список накопителей.")
    devices = [item for item in raw_devices if isinstance(item, dict)]
    if not devices:
        return _unknown("Физические накопители для SMART/NVMe не обнаружены.")

    states = [_classify_device(item) for item in devices]
    details: list[str] = []
    report_lines = ["### SMART / NVMe: защищённая проверка", "Источник: root-helper Arch Manager через Polkit (только чтение)"]
    for item, state in zip(devices, states):
        device_lines = _device_lines(item)
        prefix = {"ok": "OK", "warning": "WARNING", "critical": "CRITICAL", "unknown": "UNAVAILABLE"}[state]
        details.append(f"[{prefix}] {device_lines[0]}")
        details.extend(device_lines[1:])
        if item.get("error"):
            details.append(f"  Ошибка чтения: {str(item.get('error'))[:240]}")
        report_lines.extend(("", f"[{prefix}]"))
        report_lines.extend(device_lines)
        if item.get("error"):
            report_lines.append(f"  Ошибка чтения: {str(item.get('error'))[:240]}")

    if "critical" in states:
        status = "critical"
        summary = f"Проблемных накопителей: {states.count('critical')}"
    elif "warning" in states:
        status = "warning"
        summary = f"Есть SMART-предупреждения: {states.count('warning')}"
    elif "unknown" in states:
        status = "unknown"
        summary = f"Проверено {states.count('ok')}, недоступно {states.count('unknown')}"
    else:
        status = "ok"
        summary = f"Проверено накопителей: {len(devices)} · состояние нормально"

    check = DiagnosticCheck("smart", "storage", "SMART / NVMe", status, summary, tuple(details), True)
    return SmartDiagnosticResult(check, "\n".join(report_lines))


def collect_privileged_smart(*, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> SmartDiagnosticResult:
    try:
        return _build_result(_run_helper(timeout))
    except SmartDiagnosticsCancelled as exc:
        return _unknown("Проверка SMART/NVMe отменена пользователем", str(exc))
    except SmartDiagnosticsUnavailable as exc:
        return _unknown("Защищённая проверка SMART/NVMe недоступна", str(exc))
