#!/usr/bin/python
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any


PROTOCOL_VERSION = 1
SMARTCTL = "/usr/bin/smartctl"
LSBLK = "/usr/bin/lsblk"
ALLOWED_DEVICE = re.compile(r"^/dev/(?:nvme\d+n\d+|sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|mmcblk\d+)$")
ATA_ATTRIBUTE_IDS = {5, 187, 188, 197, 198, 199}


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _run(args: list[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin", "LC_ALL": "C", "LANG": "C"}
    return subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )


def _physical_devices() -> list[str]:
    result = _run([LSBLK, "-dnpo", "NAME,TYPE"], timeout=8.0)
    if result.returncode != 0:
        fail("lsblk failed: " + result.stderr.strip()[:300])

    devices: list[str] = []
    for raw in result.stdout.splitlines():
        parts = raw.split()
        if len(parts) != 2 or parts[1] != "disk":
            continue
        path = parts[0]
        if not ALLOWED_DEVICE.fullmatch(path):
            continue
        if path not in devices:
            devices.append(path)
    return devices


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    if isinstance(value, dict):
        for key in ("value", "raw"):
            parsed = _integer(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _selected_ata_attributes(payload: dict[str, Any]) -> dict[str, int]:
    selected: dict[str, int] = {}
    table = payload.get("ata_smart_attributes", {}).get("table", [])
    if not isinstance(table, list):
        return selected
    for item in table:
        if not isinstance(item, dict):
            continue
        attr_id = _integer(item.get("id"))
        if attr_id not in ATA_ATTRIBUTE_IDS:
            continue
        raw_value = _integer(item.get("raw"))
        if raw_value is not None:
            selected[str(attr_id)] = raw_value
    return selected


def _safe_model(payload: dict[str, Any]) -> str:
    for key in ("model_name", "model_family"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return ""


def _smart_device(path: str) -> dict[str, Any]:
    try:
        result = _run([SMARTCTL, "-j", "-H", "-A", path], timeout=20.0)
    except subprocess.TimeoutExpired:
        return {"path": path, "error": "smartctl timeout"}

    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        return {
            "path": path,
            "error": "smartctl returned invalid JSON",
            "exit_status": result.returncode,
            "stderr": result.stderr.strip()[:300],
        }

    if not isinstance(payload, dict):
        return {"path": path, "error": "smartctl returned unexpected data"}

    smart_status = payload.get("smart_status")
    passed = smart_status.get("passed") if isinstance(smart_status, dict) else None
    if not isinstance(passed, bool):
        passed = None

    device_info = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    protocol = device_info.get("protocol") if isinstance(device_info.get("protocol"), str) else ""

    nvme = payload.get("nvme_smart_health_information_log")
    if not isinstance(nvme, dict):
        nvme = {}

    temperature = payload.get("temperature")
    if isinstance(temperature, dict):
        temperature_c = _integer(temperature.get("current"))
    else:
        temperature_c = None
    if temperature_c is None:
        temperature_c = _integer(nvme.get("temperature"))

    smartctl_meta = payload.get("smartctl") if isinstance(payload.get("smartctl"), dict) else {}
    exit_status = _integer(smartctl_meta.get("exit_status"))
    if exit_status is None:
        exit_status = result.returncode

    item: dict[str, Any] = {
        "path": path,
        "model": _safe_model(payload),
        "protocol": protocol,
        "smart_passed": passed,
        "exit_status": exit_status,
        "temperature_c": temperature_c,
        "critical_warning": _integer(nvme.get("critical_warning")),
        "available_spare": _integer(nvme.get("available_spare")),
        "available_spare_threshold": _integer(nvme.get("available_spare_threshold")),
        "percentage_used": _integer(nvme.get("percentage_used")),
        "power_cycles": _integer(nvme.get("power_cycles")),
        "power_on_hours": _integer(nvme.get("power_on_hours")),
        "unsafe_shutdowns": _integer(nvme.get("unsafe_shutdowns")),
        "media_errors": _integer(nvme.get("media_errors")),
        "num_err_log_entries": _integer(nvme.get("num_err_log_entries")),
        "ata_attributes": _selected_ata_attributes(payload),
    }
    if result.stderr.strip():
        item["stderr"] = result.stderr.strip()[:300]
    return item


def self_test() -> None:
    if not os.path.isabs(SMARTCTL) or not os.path.isabs(LSBLK):
        fail("command paths must be absolute")
    if ALLOWED_DEVICE.fullmatch("/dev/zram0"):
        fail("zram must never be accepted")
    if not ALLOWED_DEVICE.fullmatch("/dev/nvme0n1"):
        fail("NVMe device pattern is broken")
    print("OK")


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        self_test()
        return 0
    if argv != ["smart-json"]:
        fail("usage: read-system-diagnostics smart-json")
    if os.geteuid() != 0:
        fail("this helper must run as root")
    if not os.path.isfile(SMARTCTL) or not os.access(SMARTCTL, os.X_OK):
        print(json.dumps({"version": PROTOCOL_VERSION, "error": "smartctl is not installed", "devices": []}))
        return 0
    if not os.path.isfile(LSBLK) or not os.access(LSBLK, os.X_OK):
        fail("lsblk is not available")

    devices = _physical_devices()
    result = {
        "version": PROTOCOL_VERSION,
        "devices": [_smart_device(path) for path in devices],
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
