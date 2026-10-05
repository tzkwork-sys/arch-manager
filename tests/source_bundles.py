from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def recovery_helper_source(root: Path) -> str:
    base = root / "src/privileged"
    parts = [base / "manage_recovery.sh"]
    parts.extend(base / "recovery_lib" / name for name in ("common.sh", "iso.sh", "usb.sh", "local.sh"))
    return "\n".join(_read(path) for path in parts)


def recovery_engine_source(root: Path) -> str:
    return _read(root / "recovery/engine/arch-recovery.sh")


def recovery_page_source(root: Path) -> str:
    base = root / "src/gui"
    return "\n".join(_read(base / name) for name in ("recovery_page.py", "recovery_usb_mixin.py", "recovery_workers.py"))


def restore_points_page_source(root: Path) -> str:
    base = root / "src/gui"
    return "\n".join(_read(base / name) for name in ("restore_points_page.py", "restore_point_actions_mixin.py", "restore_point_workers.py"))
