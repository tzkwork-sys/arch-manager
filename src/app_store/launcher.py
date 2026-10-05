from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from .models import Application


_DESKTOP_ID_RE = re.compile(r"^[A-Za-z0-9._+-]+\.desktop$")
_BINARY_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
_GIO = Path("/usr/bin/gio")
_SYSTEM_APPLICATION_DIRS = (Path("/usr/share/applications"), Path("/usr/local/share/applications"))


class ApplicationLaunchError(RuntimeError):
    pass


def _desktop_file(desktop_id: str | None) -> Path | None:
    value = str(desktop_id or "").strip()
    if not _DESKTOP_ID_RE.fullmatch(value):
        return None
    for base in _SYSTEM_APPLICATION_DIRS:
        direct = base / value
        if direct.is_file() and not direct.is_symlink():
            return direct
        if base.is_dir():
            for candidate in base.rglob(value):
                if candidate.is_file() and not candidate.is_symlink():
                    return candidate
    return None


def can_launch_application(application: Application) -> bool:
    if _desktop_file(application.desktop_entry) is not None and _GIO.is_file():
        return True
    binary = str(application.launchable_binary or "").strip()
    if not _BINARY_RE.fullmatch(binary):
        return False
    candidate = Path("/usr/bin") / binary
    return candidate.is_file() and os.access(candidate, os.X_OK)


def launch_application(application: Application) -> None:
    """Launch an installed application as the desktop user without a shell/root."""
    desktop = _desktop_file(application.desktop_entry)
    if desktop is not None and _GIO.is_file() and os.access(_GIO, os.X_OK):
        command = (str(_GIO), "launch", str(desktop))
    else:
        binary = str(application.launchable_binary or "").strip()
        if not _BINARY_RE.fullmatch(binary):
            raise ApplicationLaunchError("Не найден безопасный способ запуска приложения.")
        candidate = Path("/usr/bin") / binary
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ApplicationLaunchError("Исполняемый файл приложения не найден.")
        command = (str(candidate),)

    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise ApplicationLaunchError("Не удалось запустить приложение.") from exc
