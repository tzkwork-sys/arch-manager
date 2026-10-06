from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable, Iterable

from src.core.command import CommandResult, run_command
from src.core.update_state import UpdateStateService, get_update_state_service


OFFICIAL_REPOSITORIES = frozenset({"core", "extra", "multilib"})
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")
_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PackageState:
    package_name: str
    repository: str | None = None
    available_version: str | None = None
    installed_version: str | None = None
    installed: bool = False
    update_available: bool = False
    download_size: int | None = None
    installed_size: int | None = None
    available: bool = False
    metadata_complete: bool = True
    issues: tuple[str, ...] = ()
    installed_state_known: bool = True


def _valid_package_name(name: str) -> bool:
    return bool(name) and not name.startswith("-") and bool(_PACKAGE_NAME_RE.fullmatch(name))


def _parse_size(value: str) -> int | None:
    match = _SIZE_RE.match(value.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    factors = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
    }
    factor = factors.get(unit)
    return round(number * factor) if factor is not None else None


def _parse_sync_list(text: str, repositories: frozenset[str]) -> dict[str, tuple[str, str]]:
    packages: dict[str, tuple[str, str]] = {}
    for raw_line in text.splitlines():
        parts = raw_line.split()
        if len(parts) < 3:
            continue
        repository, name, version = parts[:3]
        if repository not in repositories or not _valid_package_name(name):
            continue
        packages.setdefault(name, (repository, version))
    return packages


def _parse_installed(text: str) -> dict[str, str]:
    installed: dict[str, str] = {}
    for raw_line in text.splitlines():
        parts = raw_line.split(maxsplit=1)
        if len(parts) == 2 and _valid_package_name(parts[0]):
            installed[parts[0]] = parts[1].strip()
    return installed


def _parse_updates(text: str) -> set[str]:
    updates: set[str] = set()
    for raw_line in text.splitlines():
        name = raw_line.strip().split(maxsplit=1)[0] if raw_line.strip() else ""
        if _valid_package_name(name):
            updates.add(name)
    return updates


def _parse_info_blocks(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    current_key: str | None = None

    def finish() -> None:
        nonlocal current, current_key
        name = current.get("Name")
        if name and _valid_package_name(name):
            result[name] = current
        current = {}
        current_key = None

    for raw_line in text.splitlines() + [""]:
        if not raw_line.strip():
            if current:
                finish()
            continue
        if not raw_line[:1].isspace() and ":" in raw_line:
            key, value = raw_line.split(":", 1)
            current_key = key.strip()
            current[current_key] = value.strip()
            continue
        if current_key is not None and raw_line[:1].isspace():
            continuation = raw_line.strip()
            if continuation:
                current[current_key] = f"{current[current_key]} {continuation}".strip()
    return result


class PacmanPackageStateProvider:
    """Read package state from pacman databases. Never refreshes or modifies them."""

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_command,
        repositories: Iterable[str] = OFFICIAL_REPOSITORIES,
        info_chunk_size: int = 150,
        update_state_service: UpdateStateService | None = None,
    ) -> None:
        self.runner = runner
        self.repositories = frozenset(repositories)
        self.info_chunk_size = max(1, int(info_chunk_size))
        # Production uses the same update-state owner as Overview and Updates.
        # A custom runner keeps the Stage 1 read-only contract convenient for
        # isolated unit tests and alternate providers.
        self.update_state_service = (
            update_state_service
            if update_state_service is not None
            else (get_update_state_service() if runner is run_command else None)
        )

    def collect(self, package_names: Iterable[str]) -> dict[str, PackageState]:
        requested = tuple(sorted({name for name in package_names if _valid_package_name(name)}))
        if not requested:
            return {}

        sync_result = self.runner(["pacman", "-Sl"], timeout=30)
        if not sync_result.available:
            return {
                name: PackageState(
                    name,
                    metadata_complete=False,
                    issues=("pacman-unavailable",),
                    installed_state_known=False,
                )
                for name in requested
            }
        if sync_result.timed_out or sync_result.returncode != 0:
            return {
                name: PackageState(
                    name,
                    metadata_complete=False,
                    issues=("sync-database-unavailable",),
                    installed_state_known=False,
                )
                for name in requested
            }
        available = _parse_sync_list(sync_result.stdout, self.repositories)

        installed_result = self.runner(["pacman", "-Q"], timeout=30)
        installed = _parse_installed(installed_result.stdout) if installed_result.ok else {}
        installed_state_known = installed_result.ok

        if self.update_state_service is not None:
            try:
                snapshot = self.update_state_service.refresh(force=False)
            except Exception:
                updates = set()
                update_state_known = False
            else:
                updates = {item.name for item in snapshot.official_items}
                update_state_known = (
                    snapshot.details.official.available
                    and snapshot.details.official.error is None
                )
        else:
            updates_result = self.runner(["pacman", "-Qu", "--color", "never"], timeout=30)
            updates = (
                _parse_updates(updates_result.stdout)
                if updates_result.returncode in {0, 1}
                and updates_result.available
                and not updates_result.timed_out
                else set()
            )
            update_state_known = (
                updates_result.available
                and not updates_result.timed_out
                and updates_result.returncode in {0, 1}
            )

        requested_available = [name for name in requested if name in available]
        info = self._collect_package_info(requested_available)

        states: dict[str, PackageState] = {}
        for name in requested:
            repository, available_version = available.get(name, (None, None))
            package_info = info.get(name, {})
            issues: list[str] = []
            if name not in available:
                issues.append("not-in-official-repositories")
            if not installed_state_known:
                issues.append("installed-state-unavailable")
            if not update_state_known:
                issues.append("update-state-unavailable")
            if name in available and not package_info:
                issues.append("package-details-unavailable")

            installed_version = installed.get(name)
            states[name] = PackageState(
                package_name=name,
                repository=package_info.get("Repository") or repository,
                available_version=package_info.get("Version") or available_version,
                installed_version=installed_version,
                installed=installed_version is not None,
                update_available=name in updates,
                download_size=_parse_size(package_info["Download Size"]) if package_info.get("Download Size") else None,
                installed_size=_parse_size(package_info["Installed Size"]) if package_info.get("Installed Size") else None,
                available=name in available,
                metadata_complete=not any(
                    issue in issues
                    for issue in (
                        "sync-database-unavailable",
                        "installed-state-unavailable",
                        "update-state-unavailable",
                        "package-details-unavailable",
                    )
                ),
                issues=tuple(issues),
                installed_state_known=installed_state_known,
            )
        return states

    def _collect_package_info(self, package_names: list[str]) -> dict[str, dict[str, str]]:
        info: dict[str, dict[str, str]] = {}
        for index in range(0, len(package_names), self.info_chunk_size):
            chunk = package_names[index : index + self.info_chunk_size]
            result = self.runner(["pacman", "-Si", "--", *chunk], timeout=45)
            if result.available and not result.timed_out and result.stdout:
                info.update(_parse_info_blocks(result.stdout))
        return info
