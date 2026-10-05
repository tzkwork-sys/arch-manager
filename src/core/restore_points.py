from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
from datetime import datetime
from io import StringIO
from pathlib import Path
import re

from .command import run_command
from .maintenance import parse_size_to_bytes


PRIVILEGED_READER = Path("/usr/lib/arch-manager/read-restore-points")
_RESTORE_POINTS_CACHE_VERSION = 2
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RestorePointsSummary:
    count: int | None
    latest: datetime | None
    configured: bool
    readable: bool
    note: str | None = None


@dataclass(frozen=True)
class RestorePoint:
    number: int
    created_at: datetime | None
    description: str
    important: bool
    exclusive_size_bytes: int | None
    creator: str
    cleanup: str
    userdata: str
    pre_number: int | None = None

    @property
    def display_name(self) -> str:
        return safe_restore_point_description(self.description, self.number)

    @property
    def reason(self) -> str:
        return restore_point_reason(self.description, self.cleanup)


@dataclass(frozen=True)
class RestorePointsListResult:
    points: tuple[RestorePoint, ...]
    configured: bool
    readable: bool
    checked_at: datetime
    note: str | None = None
    access_required: bool = False

    @property
    def important_count(self) -> int:
        return sum(1 for point in self.points if point.important)


_CONTROL_OR_BROKEN_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\[\?1049h|\[\?25l|\[\?1h|�"
)


def _parse_snapper_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    candidates = [
        value,
        value.replace(" ", "T", 1),
    ]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _parse_snapshot_number(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", value)
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _parse_optional_number(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", value)
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _parse_used_space(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned or cleaned in ("-", "—"):
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return parse_size_to_bytes(cleaned)


def restore_point_is_important(userdata: str) -> bool:
    for item in userdata.split(","):
        key, sep, value = item.strip().partition("=")
        if sep and key.strip().lower() == "important":
            return value.strip().lower() in ("yes", "true", "1", "on")
    return False


def safe_restore_point_description(description: str, number: int | None = None) -> str:
    text = description.strip()
    if not text:
        return f"Точка восстановления №{number}" if number is not None else "Без названия"
    if "\r" in text or "\n" in text or "\x1b" in text or _CONTROL_OR_BROKEN_RE.search(text):
        return "Повреждённое описание"
    # Avoid an unexpectedly huge legacy description breaking the table layout.
    if len(text) > 300:
        return text[:297].rstrip() + "…"
    return text


def restore_point_reason(description: str, cleanup: str) -> str:
    text = description.casefold()
    cleanup_name = cleanup.strip().casefold()

    before_update = (
        re.search(r"\bперед\b.*\bобнов", text) is not None
        or re.search(r"\bbefore\b.*\bupdate", text) is not None
        or "pre-update" in text
    )
    if before_update:
        return "Перед обновлением системы"
    if "после настройки" in text or "after setup" in text:
        return "После настройки восстановления"
    if cleanup_name == "timeline":
        return "Создана автоматически по времени"
    if "ручн" in text or "manual" in text:
        return "Создана вручную"
    return "—"


def _permission_denied(result_text: str) -> bool:
    details = result_text.casefold()
    return any(
        word in details
        for word in (
            "permission",
            "access",
            "authorization",
            "not authorized",
            "not allowed",
            "org.freedesktop.dbus.error.accessdenied",
        )
    )


def _permission_note(result_text: str) -> str:
    if _permission_denied(result_text):
        return "список защищён правами доступа"
    return "не удалось прочитать список"


def _run_privileged_reader(*arguments: str, timeout: float):
    """Use the installed, root-owned read-only helper without prompting for a password."""
    if not PRIVILEGED_READER.is_file():
        return None
    return run_command(
        ["sudo", "-n", str(PRIVILEGED_READER), *arguments],
        timeout=timeout,
    )


def _cache_root() -> Path:
    override = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".cache"
    return base / "arch-manager"


def _restore_points_cache_path() -> Path:
    return _cache_root() / "restore-points.json"


def _parse_restore_points_fingerprint(text: str) -> str | None:
    version = ""
    fingerprint = ""
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "version":
            version = value.strip()
        elif key == "fingerprint":
            fingerprint = value.strip()
    if version != "1" or not _FINGERPRINT_RE.fullmatch(fingerprint):
        return None
    return fingerprint


def _restore_points_fingerprint() -> str | None:
    result = _run_privileged_reader("--fingerprint", timeout=5)
    if result is None or not result.ok:
        return None
    return _parse_restore_points_fingerprint(result.stdout)


def _load_restore_points_cache(fingerprint: str) -> tuple[RestorePoint, ...] | None:
    try:
        payload = json.loads(_restore_points_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("version") != _RESTORE_POINTS_CACHE_VERSION:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    csv_text = payload.get("csv")
    if not isinstance(csv_text, str):
        return None
    return parse_restore_points_csv(csv_text)


def _save_restore_points_cache(fingerprint: str, csv_text: str) -> None:
    path = _restore_points_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": _RESTORE_POINTS_CACHE_VERSION,
                    "fingerprint": fingerprint,
                    "csv": csv_text,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        # Cache is only a performance optimization. A read failure must never
        # make restore points unavailable.
        pass


def _snapper_configured() -> bool:
    return Path("/etc/snapper/configs/root").exists()


def collect_restore_points() -> RestorePointsSummary:
    """Fast summary used by the dashboard; never opens an authentication prompt."""
    configured = _snapper_configured()

    result = run_command(
        [
            "snapper",
            "-c",
            "root",
            "--csvout",
            "--no-headers",
            "--separator",
            "|",
            "--iso",
            "list",
            "--disable-used-space",
            "--columns",
            "number,date",
        ],
        timeout=20,
    )

    if not result.available:
        note = "Snapper не установлен" if not configured else "не удалось запустить Snapper"
        return RestorePointsSummary(None if configured else 0, None, configured, False, note)

    if result.returncode != 0:
        direct_text = "\n".join((result.stderr, result.stdout))
        if _permission_denied(direct_text):
            configured = True
        privileged = _run_privileged_reader(timeout=25)
        if privileged is not None and privileged.ok:
            result = privileged
            configured = True
        else:
            if not configured:
                return RestorePointsSummary(0, None, False, False, "точки восстановления не настроены")
            note = (
                "проверка заняла слишком много времени"
                if result.timed_out
                else _permission_note(direct_text)
            )
            return RestorePointsSummary(None, None, True, False, note)

    rows = csv.reader(StringIO(result.stdout), delimiter="|", quotechar='"')
    dates: list[datetime] = []
    count = 0
    for row in rows:
        if not row:
            continue
        number = _parse_snapshot_number(row[0])
        if number is None:
            continue
        count += 1
        if len(row) > 1:
            parsed = _parse_snapper_datetime(row[1])
            if parsed is not None:
                dates.append(parsed)

    latest = max(dates) if dates else None
    return RestorePointsSummary(count, latest, True, True)


def parse_restore_points_csv(text: str) -> tuple[RestorePoint, ...]:
    """Parse Stage 3 CSV returned by Snapper into UI-independent records."""
    points: list[RestorePoint] = []
    rows = csv.reader(StringIO(text), delimiter="|", quotechar='"')

    for row in rows:
        if not row:
            continue
        # Fast reads deliberately disable the expensive used-space column.
        # Current Snapper omits that field from CSV completely when
        # --disable-used-space is active, even if an older caller requested
        # used-space in --columns.  Accept both layouts to avoid shifting
        # cleanup/description/userdata and accidentally showing e.g.
        # "important=yes" as the restore-point name.
        if len(row) >= 8:
            number_text, date_text, creator, used_space, cleanup, description, userdata, pre_number = row[:8]
        else:
            padded = list(row) + [""] * max(0, 7 - len(row))
            number_text, date_text, creator, cleanup, description, userdata, pre_number = padded[:7]
            used_space = ""

        number = _parse_snapshot_number(number_text)
        if number is None:
            # Snapshot 0 is the current system state, not a restore point.
            continue

        points.append(
            RestorePoint(
                number=number,
                created_at=_parse_snapper_datetime(date_text),
                creator=creator.strip(),
                exclusive_size_bytes=_parse_used_space(used_space),
                cleanup=cleanup.strip(),
                description=description.strip(),
                userdata=userdata.strip(),
                important=restore_point_is_important(userdata),
                pre_number=_parse_optional_number(pre_number),
            )
        )

    return tuple(points)


def collect_restore_points_list() -> RestorePointsListResult:
    """Return restore-point metadata using a cheap fingerprint + local cache.

    Exclusive Btrfs size calculation is intentionally skipped here: it was the
    dominant source of minute-long page loads and is not required by the UI.
    """
    checked_at = datetime.now().astimezone()
    configured = _snapper_configured()

    fingerprint = _restore_points_fingerprint()
    if fingerprint is not None:
        cached = _load_restore_points_cache(fingerprint)
        if cached is not None:
            return RestorePointsListResult(cached, True, True, checked_at)

    result = _run_privileged_reader(timeout=15)
    if result is not None and result.ok:
        configured = True
    else:
        result = run_command(
            [
                "snapper",
                "-c",
                "root",
                "--csvout",
                "--no-headers",
                "--separator",
                "|",
                "--iso",
                "list",
                "--disable-used-space",
                "--columns",
                "number,date,user,cleanup,description,userdata,pre-number",
            ],
            timeout=15,
        )

    if not result.available:
        note = "Snapper не установлен" if not configured else "не удалось запустить Snapper"
        return RestorePointsListResult((), configured, False, checked_at, note)

    if result.returncode != 0:
        details = "\n".join((result.stderr, result.stdout))
        permission_denied = _permission_denied(details)
        if permission_denied:
            configured = True
        if not configured:
            return RestorePointsListResult(
                (), False, False, checked_at, "точки восстановления не настроены"
            )
        note = (
            "проверка заняла слишком много времени"
            if result.timed_out
            else _permission_note(details)
        )
        return RestorePointsListResult(
            (),
            True,
            False,
            checked_at,
            note,
            access_required=permission_denied or PRIVILEGED_READER.is_file(),
        )

    points = parse_restore_points_csv(result.stdout)
    if fingerprint is not None:
        _save_restore_points_cache(fingerprint, result.stdout)
    return RestorePointsListResult(points, True, True, checked_at)

