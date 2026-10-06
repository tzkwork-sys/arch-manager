from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import re
import threading
import time
from collections.abc import Callable

from src.core.command import CommandResult, run_command
from src.app_store.search import english_search_phrase

from .models import Application
from .package_state import OFFICIAL_REPOSITORIES


_SYSTEM_PACKAGE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="arch-manager-system-packages",
)
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")
_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)?$", re.IGNORECASE)
_SS_HEADER_RE = re.compile(r"^([^/\s]+)/([^\s]+)\s+([^\s]+)(?:\s+.*)?$")


def _valid_package_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and bool(_PACKAGE_NAME_RE.fullmatch(value))


def _parse_size(value: str) -> int | None:
    match = _SIZE_RE.match((value or "").strip())
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


def _parse_info_blocks(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    current_key: str | None = None

    def finish() -> None:
        nonlocal current, current_key
        name = current.get("Name", "").strip()
        if _valid_package_name(name):
            result[name] = dict(current)
        current = {}
        current_key = None

    for raw_line in list(text.splitlines()) + [""]:
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


def _parse_sync_list(text: str) -> dict[str, tuple[str, str]]:
    packages: dict[str, tuple[str, str]] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split()
        if len(fields) < 3:
            continue
        repository, name, version = fields[:3]
        if repository not in OFFICIAL_REPOSITORIES or not _valid_package_name(name):
            continue
        packages.setdefault(name, (repository, version))
    return packages


def _parse_installed(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split(maxsplit=1)
        if len(fields) == 2 and _valid_package_name(fields[0]):
            packages[fields[0]] = fields[1].strip()
    return packages


def _parse_search(text: str) -> dict[str, tuple[str, str, str]]:
    """Parse ``pacman -Ss`` into name -> (repository, version, description)."""

    results: dict[str, tuple[str, str, str]] = {}
    current_name: str | None = None
    current_repo = ""
    current_version = ""
    descriptions: list[str] = []

    def finish() -> None:
        nonlocal current_name, current_repo, current_version, descriptions
        if current_name is not None:
            results[current_name] = (
                current_repo,
                current_version,
                " ".join(part for part in descriptions if part).strip(),
            )
        current_name = None
        current_repo = ""
        current_version = ""
        descriptions = []

    for raw_line in list(text.splitlines()) + [""]:
        if not raw_line.strip():
            finish()
            continue
        if raw_line[:1].isspace():
            if current_name is not None:
                descriptions.append(raw_line.strip())
            continue
        finish()
        match = _SS_HEADER_RE.match(raw_line.strip())
        if match is None:
            continue
        repository, name, version = match.groups()
        if repository not in OFFICIAL_REPOSITORIES or not _valid_package_name(name):
            continue
        current_name = name
        current_repo = repository
        current_version = version
    return results


def _compact_alnum(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _compact_alpha(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalpha())


def _clean_info_value(value: str | None) -> str:
    text = str(value or "").strip()
    return "" if not text or text.casefold() == "none" else text


@dataclass(frozen=True, slots=True)
class _PackageIndexSnapshot:
    available: dict[str, tuple[str, str]]
    installed: dict[str, str]
    installed_state_known: bool
    created_monotonic: float


class SystemPackageSearchService:
    """Read-only search over pacman's official package databases.

    The service never refreshes repositories and never mutates the system. It
    combines a cheap local ``pacman -Sl`` index with ``pacman -Ss`` so package
    names can be matched fuzzily (for example ``WebKitGTK`` ->
    ``webkit2gtk-4.1``) while normal description searches still work.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_command,
        cache_ttl_seconds: float = 300.0,
        result_limit: int = 60,
    ) -> None:
        self.runner = runner
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.result_limit = max(10, int(result_limit))
        self._lock = threading.RLock()
        self._snapshot: _PackageIndexSnapshot | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None

    def search_async(self, query: str, *, use_cache: bool = True) -> Future[tuple[Application, ...]]:
        return _SYSTEM_PACKAGE_EXECUTOR.submit(self.search, query, use_cache=use_cache)

    def search(self, query: str, *, use_cache: bool = True) -> tuple[Application, ...]:
        term = str(query or "").strip()
        if len(term) < 2:
            return ()
        search_term = english_search_phrase(term) or term

        snapshot = self._load_index(use_cache=use_cache)
        direct = self._search_descriptions(search_term)
        names = self._candidate_names(search_term, snapshot.available, direct)
        if not names:
            return ()

        sync_info = self._collect_info("-Si", names)
        installed_names = [name for name in names if name in snapshot.installed]
        local_info = self._collect_info("-Qi", installed_names) if installed_names else {}

        applications: list[Application] = []
        for name in names:
            repository, fallback_version = snapshot.available[name]
            info = sync_info.get(name, {})
            installed_info = local_info.get(name, {})
            direct_repo, direct_version, direct_description = direct.get(name, ("", "", ""))
            description = _clean_info_value(info.get("Description")) or direct_description
            version = _clean_info_value(info.get("Version")) or direct_version or fallback_version
            installed_version = snapshot.installed.get(name)
            metadata_issues: list[str] = []
            if not snapshot.installed_state_known:
                metadata_issues.append("installed-state-unavailable")
            if not info:
                metadata_issues.append("system-package-details-unavailable")
            applications.append(
                Application(
                    app_id=f"system-package:{name}",
                    package_name=name,
                    package_names=(name,),
                    name=name,
                    summary=description or "Системный пакет Arch Linux",
                    description=description,
                    categories=("system-tools",),
                    raw_categories=("System",),
                    icon="package-x-generic",
                    icon_type="stock",
                    homepage=_clean_info_value(info.get("URL")) or None,
                    license=_clean_info_value(info.get("Licenses")) or None,
                    repository=_clean_info_value(info.get("Repository")) or direct_repo or repository,
                    available_version=version,
                    installed_version=installed_version,
                    installed=installed_version is not None,
                    update_available=False,
                    installed_state_known=snapshot.installed_state_known,
                    download_size=_parse_size(info.get("Download Size", "")),
                    installed_size=_parse_size(
                        installed_info.get("Installed Size") or info.get("Installed Size", "")
                    ),
                    metadata_complete=not metadata_issues,
                    metadata_issues=tuple(metadata_issues),
                    metadata_source="pacman-system-package",
                    dependencies_text=_clean_info_value(info.get("Depends On")),
                    optional_dependencies_text=_clean_info_value(info.get("Optional Deps")),
                    required_by_text=_clean_info_value(installed_info.get("Required By")),
                    optional_for_text=_clean_info_value(installed_info.get("Optional For")),
                    provides_text=_clean_info_value(info.get("Provides")),
                    conflicts_text=_clean_info_value(info.get("Conflicts With")),
                )
            )
        return tuple(applications)

    def _load_index(self, *, use_cache: bool) -> _PackageIndexSnapshot:
        now = time.monotonic()
        with self._lock:
            cached = self._snapshot
            if (
                use_cache
                and cached is not None
                and cached.installed_state_known
                and now - cached.created_monotonic <= self.cache_ttl_seconds
            ):
                return cached

        sync = self.runner(["pacman", "-Sl"], timeout=30)
        if not sync.available:
            raise RuntimeError("pacman недоступен")
        if sync.timed_out or sync.returncode != 0:
            raise RuntimeError("Не удалось прочитать базы официальных пакетов Arch Linux")
        available = _parse_sync_list(sync.stdout)

        installed_result = self.runner(["pacman", "-Q"], timeout=30)
        installed_state_known = installed_result.ok
        installed = _parse_installed(installed_result.stdout) if installed_state_known else {}
        snapshot = _PackageIndexSnapshot(available, installed, installed_state_known, now)
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _search_descriptions(self, term: str) -> dict[str, tuple[str, str, str]]:
        # pacman treats -Ss targets as regexes. Escape user input so this remains
        # a literal read-only search and cannot accidentally request a huge regex.
        result = self.runner(
            ["pacman", "-Ss", "--color", "never", re.escape(term)],
            timeout=30,
        )
        if not result.available or result.timed_out:
            return {}
        if result.returncode not in {0, 1}:
            return {}
        return _parse_search(result.stdout)

    def _candidate_names(
        self,
        term: str,
        available: dict[str, tuple[str, str]],
        direct: dict[str, tuple[str, str, str]],
    ) -> list[str]:
        folded = term.casefold()
        compact = _compact_alnum(term)
        alpha = _compact_alpha(term)
        direct_names = set(direct)
        ranked: list[tuple[tuple[int, int, str], str]] = []

        for name in available:
            name_folded = name.casefold()
            name_compact = _compact_alnum(name)
            name_alpha = _compact_alpha(name)
            score: int | None = None
            if name_folded == folded:
                score = 0
            elif name_compact == compact:
                score = 1
            elif len(alpha) >= 5 and name_alpha == alpha:
                score = 2
            elif name_folded.startswith(folded):
                score = 3
            elif compact and name_compact.startswith(compact):
                score = 4
            elif folded in name_folded:
                score = 5
            elif compact and compact in name_compact:
                score = 6
            elif len(alpha) >= 5 and alpha in name_alpha:
                score = 7
            elif name in direct_names:
                score = 8
            if score is None:
                continue
            ranked.append(((score, len(name), name_folded), name))

        # Direct description matches might not have matched a package name.
        for name in direct_names:
            if name not in available:
                continue
            if any(candidate == name for _rank, candidate in ranked):
                continue
            ranked.append(((8, len(name), name.casefold()), name))

        ranked.sort(key=lambda item: item[0])
        return [name for _rank, name in ranked[: self.result_limit]]

    def _collect_info(self, operation: str, names: list[str]) -> dict[str, dict[str, str]]:
        if not names:
            return {}
        result: dict[str, dict[str, str]] = {}
        for start in range(0, len(names), 30):
            chunk = names[start : start + 30]
            command = ["pacman", operation, "--", *chunk]
            response = self.runner(command, timeout=45)
            if response.available and not response.timed_out and response.stdout:
                result.update(_parse_info_blocks(response.stdout))
        return result
