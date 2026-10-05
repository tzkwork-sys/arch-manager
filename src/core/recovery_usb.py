"""Read-only discovery helpers for Arch Manager Recovery USB media.

The destructive write and UEFI BootNext operations live behind the root-owned
Recovery helper.  This module only enumerates candidate removable disks and
reports whether an Arch Manager Recovery image appears to be present.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import os
import re
from typing import Any

from .command import CommandResult, run_command


RECOVERY_USB_LABEL = "AM_RECOVERY"
EFI_VARS = Path("/sys/firmware/efi/efivars")
EFIBOOTMGR = Path("/usr/bin/efibootmgr")


@dataclass(frozen=True)
class RecoveryUsbDevice:
    path: str
    size_bytes: int
    model: str
    transport: str
    removable: bool
    read_only: bool
    recovery_media: bool
    identity: str

    @property
    def display_name(self) -> str:
        parts: list[str] = []
        if self.model:
            parts.append(self.model)
        if self.size_bytes > 0:
            parts.append(_format_size(self.size_bytes))
        suffix = " · ".join(parts) if parts else "USB-накопитель"
        if self.recovery_media:
            suffix += " · Arch Manager Recovery"
        return f"{suffix} ({self.path})"


def _format_size(value: int) -> str:
    size = float(max(value, 0))
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    unit = units[0]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0
    if unit in {"Б", "КБ", "МБ"}:
        return f"{size:.0f} {unit}"
    return f"{size:.1f} {unit}"


def _clean_source(value: str) -> str:
    return value.split("[", 1)[0].strip()


def _find_root_source(runner=run_command) -> str:
    result = runner(["findmnt", "-rn", "-o", "SOURCE", "--target", "/"], timeout=5)
    if not result.ok:
        return ""
    return _clean_source(result.stdout.splitlines()[0]) if result.stdout.splitlines() else ""


def _descendant_paths(node: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    path = str(node.get("path") or "").strip()
    if path:
        values.add(path)
    for child in node.get("children") or ():
        if isinstance(child, dict):
            values.update(_descendant_paths(child))
    return values


def _usb_identity(raw: dict[str, Any]) -> str:
    """Return a privilege-independent kernel token for the selected block disk.

    The earlier safety token was built from a second ``lsblk`` view of model,
    transport and flags.  Those presentation fields can legitimately differ
    between the desktop user's udev view and the later root helper view on some
    USB bridges.  That produced false ``usb_device_changed`` failures.

    ``MAJ:MIN`` plus the canonical ``/sys/dev/block`` target and block-level
    kernel attributes come from the same kernel objects for every privilege
    level.  A detach/re-attach that replaces the selected block object changes
    this token, while simply crossing the Polkit boundary does not.
    """
    maj_min = str(raw.get("maj:min") or "").strip()
    if not re.fullmatch(r"\d+:\d+", maj_min):
        return ""

    sys_entry = Path("/sys/dev/block") / maj_min
    try:
        sys_path = str(sys_entry.resolve(strict=True))
        sectors = (sys_entry / "size").read_text(encoding="ascii").strip()
        removable = (sys_entry / "removable").read_text(encoding="ascii").strip()
        read_only = (sys_entry / "ro").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""

    if not sectors.isdigit() or removable not in {"0", "1"} or read_only not in {"0", "1"}:
        return ""
    parts = (maj_min, sys_path, sectors, removable, read_only)
    digest = hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest()
    return f"k1:{digest}"


def _tree_has_recovery_label(node: dict[str, Any]) -> bool:
    label = str(node.get("label") or "").strip()
    if label == RECOVERY_USB_LABEL:
        return True
    return any(
        _tree_has_recovery_label(child)
        for child in node.get("children") or ()
        if isinstance(child, dict)
    )


def collect_recovery_usb_devices(
    *, runner=run_command, identity_builder=_usb_identity
) -> tuple[RecoveryUsbDevice, ...]:
    """Return writable removable whole disks, excluding the running system disk."""
    result = runner(
        [
            "lsblk",
            "-J",
            "-b",
            "-o",
            "NAME,PATH,TYPE,SIZE,MODEL,TRAN,RM,RO,MAJ:MIN,LABEL,FSTYPE,MOUNTPOINTS",
        ],
        timeout=8,
    )
    if not result.ok:
        return ()
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return ()

    root_source = _find_root_source(runner)
    devices: list[RecoveryUsbDevice] = []
    for raw in payload.get("blockdevices") or ():
        if not isinstance(raw, dict) or str(raw.get("type") or "") != "disk":
            continue
        path = str(raw.get("path") or "").strip()
        if not path.startswith("/dev/"):
            continue
        descendants = _descendant_paths(raw)
        if root_source and root_source in descendants:
            continue

        transport = str(raw.get("tran") or "").strip().lower()
        removable = bool(raw.get("rm"))
        read_only = bool(raw.get("ro"))
        # Some USB bridges incorrectly report RM=0, therefore TRAN=usb is also
        # accepted. Internal SATA/NVMe disks are never offered by this picker.
        if not (removable or transport == "usb") or read_only:
            continue

        try:
            size_bytes = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        model = " ".join(str(raw.get("model") or "").split())
        identity = identity_builder(raw)
        if not identity:
            # Without a kernel selection token the destructive helper cannot
            # prove that it is still acting on the disk the user selected.
            continue
        devices.append(
            RecoveryUsbDevice(
                path=path,
                size_bytes=max(size_bytes, 0),
                model=model,
                transport=transport,
                removable=removable,
                read_only=read_only,
                recovery_media=_tree_has_recovery_label(raw),
                identity=identity,
            )
        )

    return tuple(sorted(devices, key=lambda item: item.path))


def uefi_usb_bootnext_available() -> bool:
    """Whether the host can request a one-shot UEFI boot from the USB."""
    return EFI_VARS.is_dir() and EFIBOOTMGR.is_file() and os.access(EFIBOOTMGR, os.X_OK)
