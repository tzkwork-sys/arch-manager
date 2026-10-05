from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
import json
from pathlib import Path

from .command import run_command
from .restore_points import PRIVILEGED_READER


POLICY_KEYS = (
    "NUMBER_CLEANUP",
    "NUMBER_MIN_AGE",
    "NUMBER_LIMIT",
    "NUMBER_LIMIT_IMPORTANT",
    "TIMELINE_CREATE",
    "TIMELINE_CLEANUP",
    "TIMELINE_MIN_AGE",
    "TIMELINE_LIMIT_HOURLY",
    "TIMELINE_LIMIT_DAILY",
    "TIMELINE_LIMIT_WEEKLY",
    "TIMELINE_LIMIT_MONTHLY",
    "TIMELINE_LIMIT_YEARLY",
)


@dataclass(frozen=True)
class RestorePointPolicyState:
    configured: bool
    readable: bool
    checked_at: datetime
    number_cleanup: bool = False
    number_min_age: int | None = None
    number_limit: int | None = None
    important_limit: int | None = None
    timeline_create: bool = False
    timeline_cleanup: bool = False
    timeline_min_age: int | None = None
    timeline_hourly: int | None = None
    timeline_daily: int | None = None
    timeline_weekly: int | None = None
    timeline_monthly: int | None = None
    timeline_yearly: int | None = None
    cleanup_timer_enabled: bool = False
    timeline_timer_enabled: bool = False
    note: str | None = None

    @property
    def timeline_enabled(self) -> bool:
        return self.timeline_create and self.timeline_timer_enabled

    @property
    def recommended_storage(self) -> bool:
        return (
            self.number_cleanup
            and self.number_limit == 10
            and self.important_limit == 5
            and self.timeline_cleanup
            and self.timeline_hourly == 5
            and self.timeline_daily == 7
            and self.timeline_weekly == 4
            and self.timeline_monthly == 3
            and self.timeline_yearly == 0
            and self.cleanup_timer_enabled
        )


def _to_bool(value: object) -> bool:
    return str(value or "").strip().casefold() in {"yes", "true", "1", "on", "enabled"}


def _to_int(value: object) -> int | None:
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _state_from_mapping(
    mapping: dict[str, object],
    *,
    checked_at: datetime,
    configured: bool = True,
    readable: bool = True,
    note: str | None = None,
) -> RestorePointPolicyState:
    return RestorePointPolicyState(
        configured=configured,
        readable=readable,
        checked_at=checked_at,
        number_cleanup=_to_bool(mapping.get("NUMBER_CLEANUP")),
        number_min_age=_to_int(mapping.get("NUMBER_MIN_AGE")),
        number_limit=_to_int(mapping.get("NUMBER_LIMIT")),
        important_limit=_to_int(mapping.get("NUMBER_LIMIT_IMPORTANT")),
        timeline_create=_to_bool(mapping.get("TIMELINE_CREATE")),
        timeline_cleanup=_to_bool(mapping.get("TIMELINE_CLEANUP")),
        timeline_min_age=_to_int(mapping.get("TIMELINE_MIN_AGE")),
        timeline_hourly=_to_int(mapping.get("TIMELINE_LIMIT_HOURLY")),
        timeline_daily=_to_int(mapping.get("TIMELINE_LIMIT_DAILY")),
        timeline_weekly=_to_int(mapping.get("TIMELINE_LIMIT_WEEKLY")),
        timeline_monthly=_to_int(mapping.get("TIMELINE_LIMIT_MONTHLY")),
        timeline_yearly=_to_int(mapping.get("TIMELINE_LIMIT_YEARLY")),
        cleanup_timer_enabled=bool(mapping.get("cleanup_timer_enabled", False)),
        timeline_timer_enabled=bool(mapping.get("timeline_timer_enabled", False)),
        note=note,
    )


def parse_restore_point_policy_json(
    text: str,
    *,
    checked_at: datetime | None = None,
) -> RestorePointPolicyState:
    when = checked_at or datetime.now().astimezone()
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return RestorePointPolicyState(
            configured=True,
            readable=False,
            checked_at=when,
            note="получен некорректный ответ helper",
        )

    if not isinstance(data, dict):
        return RestorePointPolicyState(
            configured=True,
            readable=False,
            checked_at=when,
            note="получен некорректный ответ helper",
        )

    configured = bool(data.get("configured", True))
    if not configured:
        return RestorePointPolicyState(False, True, when, note="Snapper не настроен")

    values = data.get("values")
    if not isinstance(values, dict):
        return RestorePointPolicyState(
            configured=True,
            readable=False,
            checked_at=when,
            note="не удалось прочитать параметры Snapper",
        )

    mapping: dict[str, object] = {str(key): value for key, value in values.items()}
    mapping["cleanup_timer_enabled"] = bool(data.get("cleanup_timer_enabled", False))
    mapping["timeline_timer_enabled"] = bool(data.get("timeline_timer_enabled", False))
    return _state_from_mapping(mapping, checked_at=when)


def _parse_get_config_csv(text: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for row in csv.reader(StringIO(text), delimiter="|", quotechar='"'):
        if len(row) < 2:
            continue
        key = row[0].strip()
        if key in POLICY_KEYS:
            values[key] = row[1].strip().strip('"')
    return values


def _timer_enabled(name: str) -> bool:
    result = run_command(["systemctl", "is-enabled", "--quiet", name], timeout=8)
    return result.available and not result.timed_out and result.returncode == 0


def collect_restore_point_policy() -> RestorePointPolicyState:
    """Read the current Snapper policy without opening an authentication dialog."""
    checked_at = datetime.now().astimezone()
    configured = Path("/etc/snapper/configs/root").exists()

    if PRIVILEGED_READER.is_file():
        privileged = run_command(
            ["sudo", "-n", str(PRIVILEGED_READER), "--policy"],
            timeout=25,
        )
        if privileged.ok:
            return parse_restore_point_policy_json(
                privileged.stdout,
                checked_at=checked_at,
            )

    direct = run_command(
        [
            "snapper",
            "-c",
            "root",
            "--csvout",
            "--no-headers",
            "--separator",
            "|",
            "get-config",
            "--columns",
            "key,value",
        ],
        timeout=20,
    )

    if not direct.available:
        return RestorePointPolicyState(
            configured=configured,
            readable=False,
            checked_at=checked_at,
            note="Snapper не установлен" if not configured else "не удалось запустить Snapper",
        )
    if direct.returncode != 0:
        return RestorePointPolicyState(
            configured=configured,
            readable=False,
            checked_at=checked_at,
            note=(
                "параметры защищены правами доступа"
                if configured
                else "Snapper не настроен"
            ),
        )

    values = _parse_get_config_csv(direct.stdout)
    values["cleanup_timer_enabled"] = _timer_enabled("snapper-cleanup.timer")
    values["timeline_timer_enabled"] = _timer_enabled("snapper-timeline.timer")
    return _state_from_mapping(
        values,
        checked_at=checked_at,
        configured=True,
        readable=True,
    )
