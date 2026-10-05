from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
from collections.abc import Callable

from src.core.command import CommandResult, run_command


@dataclass(frozen=True, slots=True)
class AurCapabilityReport:
    running_as_root: bool
    yay_available: bool
    yay_version: str | None
    git_available: bool
    makepkg_available: bool
    vercmp_available: bool
    pacman_available: bool
    base_devel_complete: bool | None
    missing_base_devel: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        return (
            not self.running_as_root
            and self.yay_available
            and self.git_available
            and self.makepkg_available
            and self.vercmp_available
            and self.pacman_available
            and self.base_devel_complete is True
        )


class AurAvailabilityProbe:
    """Read-only capability probe for the future yay-backed AUR workflow."""

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_command,
        which: Callable[[str], str | None] = shutil.which,
        geteuid: Callable[[], int] = os.geteuid,
    ) -> None:
        self.runner = runner
        self.which = which
        self.geteuid = geteuid

    def probe(self) -> AurCapabilityReport:
        running_as_root = self.geteuid() == 0
        yay_available = self.which("yay") is not None
        git_available = self.which("git") is not None
        makepkg_available = self.which("makepkg") is not None
        vercmp_available = self.which("vercmp") is not None
        pacman_available = self.which("pacman") is not None
        issues: list[str] = []

        yay_version: str | None = None
        if yay_available:
            result = self.runner(["yay", "--version"], timeout=5)
            if result.ok and result.stdout.strip():
                yay_version = result.stdout.strip().splitlines()[0]
            else:
                yay_available = False
                issues.append("yay-version-probe-failed")

        base_devel_complete: bool | None = None
        missing_base_devel: tuple[str, ...] = ()
        if pacman_available:
            # base-devel has been a meta package (not a package group) since 2023.
            # Requiring the meta package mirrors Arch's documented AUR build prerequisite
            # and avoids probing it through the obsolete package-group query path.
            base_devel = self.runner(["pacman", "-Qq", "base-devel"], timeout=10)
            if base_devel.ok:
                base_devel_complete = True
            elif base_devel.available and not base_devel.timed_out and base_devel.returncode == 1:
                base_devel_complete = False
                missing_base_devel = ("base-devel",)
            else:
                issues.append("base-devel-state-unavailable")

        if running_as_root:
            issues.append("running-as-root")
        if not yay_available:
            issues.append("yay-unavailable")
        if not git_available:
            issues.append("git-unavailable")
        if not makepkg_available:
            issues.append("makepkg-unavailable")
        if not vercmp_available:
            issues.append("vercmp-unavailable")
        if not pacman_available:
            issues.append("pacman-unavailable")
        if base_devel_complete is False:
            issues.append("base-devel-incomplete")

        return AurCapabilityReport(
            running_as_root=running_as_root,
            yay_available=yay_available,
            yay_version=yay_version,
            git_available=git_available,
            makepkg_available=makepkg_available,
            vercmp_available=vercmp_available,
            pacman_available=pacman_available,
            base_devel_complete=base_devel_complete,
            missing_base_devel=missing_base_devel,
            issues=tuple(dict.fromkeys(issues)),
        )
