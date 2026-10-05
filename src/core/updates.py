from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
import os
import re

from .command import CommandResult, run_command


@dataclass(frozen=True)
class UpdateItem:
    name: str
    current_version: str
    new_version: str
    source: str
    download_size: int | None = None


@dataclass(frozen=True)
class UpdateSourceDetails:
    items: tuple[UpdateItem, ...]
    available: bool
    error: str | None = None

    @property
    def count(self) -> int | None:
        return len(self.items) if self.available and self.error is None else None


@dataclass(frozen=True)
class UpdateDetails:
    official: UpdateSourceDetails
    aur: UpdateSourceDetails
    checked_at: datetime

    @property
    def total(self) -> int | None:
        counts = [source.count for source in (self.official, self.aur) if source.count is not None]
        return sum(counts) if counts else None

    @property
    def partial(self) -> bool:
        return self.official.count is None or self.aur.count is None

    @property
    def all_items(self) -> tuple[UpdateItem, ...]:
        return self.official.items + self.aur.items


@dataclass(frozen=True)
class UpdateSourceSummary:
    count: int | None
    available: bool
    error: str | None = None


@dataclass(frozen=True)
class UpdatesSummary:
    official: UpdateSourceSummary
    aur: UpdateSourceSummary

    @property
    def total(self) -> int | None:
        counts = [source.count for source in (self.official, self.aur) if source.count is not None]
        return sum(counts) if counts else None

    @property
    def partial(self) -> bool:
        return self.official.count is None or self.aur.count is None


ANSI_CSI_RE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")

_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)?$", re.IGNORECASE)


def _parse_size_bytes(value: str) -> int | None:
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


def _parse_pacman_info_blocks(text: str) -> dict[str, dict[str, str]]:
    """Parse one or more sync-package info records using stable LC_ALL=C output."""

    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    current_key: str | None = None

    def finish() -> None:
        nonlocal current, current_key
        name = current.get("Name")
        if name:
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


def _checkupdates_db_path() -> str:
    configured = os.environ.get("CHECKUPDATES_DB", "").strip()
    if configured:
        return configured
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return os.path.join(tmpdir, f"checkup-db-{os.getuid()}")


def _official_download_sizes(
    package_names: tuple[str, ...],
) -> dict[str, int]:
    """Read download sizes from the sync DB refreshed by ``checkupdates``.

    ``checkupdates`` intentionally uses a private pacman database so it can sync
    metadata without changing the system database. Query that same database first;
    if it is unavailable, fall back to the normal local sync database.
    """

    if not package_names:
        return {}

    info: dict[str, dict[str, str]] = {}
    db_path = _checkupdates_db_path()
    for index in range(0, len(package_names), 150):
        chunk = package_names[index : index + 150]
        result = run_command(
            ["pacman", "--dbpath", db_path, "-Si", "--", *chunk],
            timeout=45,
        )
        if not result.stdout.strip():
            result = run_command(["pacman", "-Si", "--", *chunk], timeout=45)
        if result.stdout.strip():
            info.update(_parse_pacman_info_blocks(result.stdout))

    sizes: dict[str, int] = {}
    for name, fields in info.items():
        raw = fields.get("Download Size")
        if not raw:
            continue
        parsed = _parse_size_bytes(raw)
        if parsed is not None:
            sizes[name] = parsed
    return sizes


def _with_official_download_sizes(items: tuple[UpdateItem, ...]) -> tuple[UpdateItem, ...]:
    sizes = _official_download_sizes(tuple(item.name for item in items))
    return tuple(replace(item, download_size=sizes.get(item.name)) for item in items)


def strip_terminal_control_sequences(text: str) -> str:
    return ANSI_CSI_RE.sub("", text).replace("\x1b", "")


def parse_update_line(line: str, *, source: str) -> UpdateItem | None:
    text = strip_terminal_control_sequences(line).strip()
    if not text:
        return None

    if " -> " in text:
        left, new_version = text.rsplit(" -> ", 1)
        left_parts = left.rsplit(maxsplit=1)
        if len(left_parts) == 2:
            name, current_version = left_parts
            return UpdateItem(name, current_version, new_version.strip(), source)

    # Keep an unusual line visible instead of silently dropping it.  The
    # detailed table can still show the package/text while versions stay unknown.
    name = text.split(maxsplit=1)[0]
    return UpdateItem(name, "—", "—", source)


def parse_update_output(text: str, *, source: str) -> tuple[UpdateItem, ...]:
    items: list[UpdateItem] = []
    for line in text.splitlines():
        item = parse_update_line(line, source=source)
        if item is not None:
            items.append(item)
    return tuple(items)


def _error_tail(result: CommandResult, fallback: str) -> str:
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else fallback


def _official_update_details() -> UpdateSourceDetails:
    result = run_command(["checkupdates", "--nocolor"], timeout=60)
    if not result.available:
        return UpdateSourceDetails((), False, "checkupdates не найден")
    if result.timed_out:
        return UpdateSourceDetails((), True, "проверка заняла слишком много времени")
    if result.returncode == 0:
        items = parse_update_output(result.stdout, source="official")
        return UpdateSourceDetails(
            _with_official_download_sizes(items),
            True,
        )
    if result.returncode == 2:
        return UpdateSourceDetails((), True)
    return UpdateSourceDetails(
        (),
        True,
        _error_tail(result, "ошибка проверки официальных обновлений"),
    )


def _aur_update_details() -> UpdateSourceDetails:
    result = run_command(["yay", "-Qu", "--aur"], timeout=60)
    if not result.available:
        return UpdateSourceDetails((), False, "yay не установлен")
    if result.timed_out:
        return UpdateSourceDetails((), True, "проверка заняла слишком много времени")
    if result.stdout.strip():
        return UpdateSourceDetails(
            parse_update_output(result.stdout, source="aur"),
            True,
        )
    if result.returncode in (0, 1):
        return UpdateSourceDetails((), True)
    return UpdateSourceDetails(
        (),
        True,
        _error_tail(result, "ошибка проверки дополнительных обновлений"),
    )


def collect_update_details() -> UpdateDetails:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="arch-manager-updates") as pool:
        official_future = pool.submit(_official_update_details)
        aur_future = pool.submit(_aur_update_details)
        return UpdateDetails(
            official=official_future.result(),
            aur=aur_future.result(),
            checked_at=datetime.now().astimezone(),
        )


def _to_summary(source: UpdateSourceDetails) -> UpdateSourceSummary:
    return UpdateSourceSummary(source.count, source.available, source.error)


def summary_from_details(details: UpdateDetails) -> UpdatesSummary:
    """Build the compact Overview summary from one shared detailed check."""

    return UpdatesSummary(
        official=_to_summary(details.official),
        aur=_to_summary(details.aur),
    )


def collect_updates() -> UpdatesSummary:
    # Compatibility helper for non-GUI callers. GUI surfaces use the shared
    # UpdateStateService so they cannot publish independently checked states.
    return summary_from_details(collect_update_details())


@dataclass(frozen=True)
class PackageInfo:
    name: str
    description: str
    installed_version: str
    installed_size: str
    url: str
    licenses: str


def _parse_pacman_info(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            current_key = None
            continue
        if not raw_line[:1].isspace() and ":" in raw_line:
            key, value = raw_line.split(":", 1)
            current_key = key.strip()
            fields[current_key] = value.strip()
            continue
        if current_key is not None and raw_line[:1].isspace():
            continuation = raw_line.strip()
            if continuation:
                fields[current_key] = (fields[current_key] + " " + continuation).strip()
    return fields


def collect_installed_package_info(name: str) -> PackageInfo:
    """Read local package metadata without network access or privilege escalation."""
    package_name = name.strip()
    if not package_name or any(ch.isspace() for ch in package_name):
        raise ValueError("Некорректное имя пакета")

    result = run_command(["pacman", "-Qi", "--", package_name], timeout=10)
    if not result.available:
        raise RuntimeError("pacman не найден")
    if result.timed_out:
        raise RuntimeError("Получение описания пакета заняло слишком много времени")
    if result.returncode != 0:
        raise RuntimeError(_error_tail(result, "Не удалось прочитать сведения о пакете"))

    fields = _parse_pacman_info(result.stdout)
    return PackageInfo(
        name=fields.get("Name", package_name),
        description=fields.get("Description", "Описание отсутствует."),
        installed_version=fields.get("Version", "—"),
        installed_size=fields.get("Installed Size", "—"),
        url=fields.get("URL", "—"),
        licenses=fields.get("Licenses", "—"),
    )
