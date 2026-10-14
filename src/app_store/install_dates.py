from __future__ import annotations

"""Read-only installation history from the local pacman transaction journal.

The local pacman DB INSTALLDATE changes on upgrades, so it is deliberately not
used as proof of the original installation date. Missing journal history and
Flatpak entries are reported as unknown rather than given misleading dates.
"""

from datetime import datetime
from pathlib import Path
import re

from .models import Application
from .aur.models import AurPackage

_INSTALL = re.compile(r"^\[(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)[^]]*\] \[ALPM\] (?:installed|reinstalled) ([^ ]+) \(")


class PacmanInstallDates:
    def __init__(self, log_path: Path = Path("/var/log/pacman.log")) -> None:
        self.log_path = Path(log_path)
        self._signature: tuple[int, int] | None = None
        self._timestamps: dict[str, float] = {}

    def refresh(self) -> None:
        try:
            stat = self.log_path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if signature == self._signature:
            return
        dates: dict[str, float] = {}
        if signature is not None:
            try:
                with self.log_path.open(encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        match = _INSTALL.match(line)
                        if match:
                            try:
                                dates[match.group(2)] = datetime.fromisoformat(match.group(1)).timestamp()
                            except (ValueError, OverflowError):
                                continue
            except OSError:
                dates = {}
        self._timestamps = dates
        self._signature = signature

    def timestamp(self, package_name: str) -> float | None:
        return self._timestamps.get(package_name)


def installation_timestamp(item: Application | AurPackage, dates: PacmanInstallDates) -> float | None:
    if isinstance(item, AurPackage):
        return dates.timestamp(item.name) if item.installed else None
    if not item.installed or (item.metadata_source or "").startswith("flatpak:"):
        return None
    return dates.timestamp(item.package_name)
