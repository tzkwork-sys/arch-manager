from __future__ import annotations

from collections.abc import Callable

from src.core.command import CommandResult, run_command

from .errors import AurVersionError


def compare_versions(
    version_a: str,
    version_b: str,
    *,
    runner: Callable[..., CommandResult] = run_command,
) -> int:
    """Compare versions with Arch's libalpm-compatible vercmp utility."""

    if not version_a or not version_b:
        raise ValueError("versions must not be empty")
    result = runner(["vercmp", version_a, version_b], timeout=5)
    if not result.available:
        raise AurVersionError("vercmp is unavailable")
    if result.timed_out or result.returncode != 0:
        detail = result.stderr.strip() or "vercmp failed"
        raise AurVersionError(detail)
    try:
        value = int(result.stdout.strip())
    except ValueError as exc:
        raise AurVersionError("invalid vercmp output") from exc
    return -1 if value < 0 else (1 if value > 0 else 0)


def update_available(
    installed_version: str,
    available_version: str,
    *,
    runner: Callable[..., CommandResult] = run_command,
) -> bool:
    return compare_versions(installed_version, available_version, runner=runner) < 0
