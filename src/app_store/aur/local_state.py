from __future__ import annotations

from collections.abc import Callable
import re

from src.core.command import CommandResult, run_command

from .errors import AurUnavailable
from .models import ForeignPackage


_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")


def valid_package_name(name: str) -> bool:
    return bool(name) and not name.startswith("-") and bool(_PACKAGE_NAME_RE.fullmatch(name))


def parse_foreign_packages(text: str) -> tuple[ForeignPackage, ...]:
    packages: list[ForeignPackage] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        parts = raw_line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        name, version = parts[0], parts[1].strip()
        if not valid_package_name(name) or not version or name in seen:
            continue
        seen.add(name)
        packages.append(ForeignPackage(name=name, installed_version=version))
    return tuple(packages)


class ForeignPackageReader:
    """Read packages not present in sync databases. This never mutates pacman state."""

    def __init__(self, *, runner: Callable[..., CommandResult] = run_command) -> None:
        self.runner = runner

    def read(self) -> tuple[ForeignPackage, ...]:
        result = self.runner(["pacman", "-Qm"], timeout=20, accepted_returncodes={0, 1})
        if not result.available:
            raise AurUnavailable("pacman is unavailable")
        if result.timed_out:
            raise AurUnavailable("pacman -Qm timed out")
        if result.returncode not in {0, 1}:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise AurUnavailable(f"pacman -Qm failed: {detail}")
        return parse_foreign_packages(result.stdout)
