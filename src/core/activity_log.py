from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_RETAINED_LINES = 500


@dataclass(frozen=True)
class ActivityEntry:
    timestamp: datetime
    category: str
    action: str
    result: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def activity_log_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return root / "arch-manager" / "activity.jsonl"


def _serialize(entry: ActivityEntry) -> str:
    return json.dumps(
        {
            "timestamp": entry.timestamp.astimezone().isoformat(),
            "category": entry.category,
            "action": entry.action,
            "result": entry.result,
            "detail": entry.detail,
            "data": entry.data,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def append_activity(
    category: str,
    action: str,
    result: str,
    detail: str = "",
    *,
    data: dict[str, Any] | None = None,
) -> None:
    entry = ActivityEntry(
        timestamp=datetime.now().astimezone(),
        category=str(category).strip() or "system",
        action=str(action).strip() or "event",
        result=str(result).strip() or "info",
        detail=str(detail).strip(),
        data=dict(data or {}),
    )
    path = activity_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_serialize(entry) + "\n")
        _trim_if_needed(path)
    except OSError:
        # Activity history must never break the actual operation.
        return


def _trim_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size <= MAX_LOG_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = lines[-MAX_RETAINED_LINES:]
        temp = path.with_suffix(".tmp")
        temp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        temp.replace(path)
    except OSError:
        return


def _parse_line(line: str) -> ActivityEntry | None:
    try:
        raw = json.loads(line)
        timestamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        data = {}
    return ActivityEntry(
        timestamp=timestamp,
        category=str(raw.get("category", "system")),
        action=str(raw.get("action", "event")),
        result=str(raw.get("result", "info")),
        detail=str(raw.get("detail", "")),
        data=data,
    )


def read_activity_entries(
    *,
    limit: int = 250,
    category: str | None = None,
) -> tuple[ActivityEntry, ...]:
    if limit <= 0:
        return ()
    path = activity_log_path()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()

    entries: list[ActivityEntry] = []
    for line in reversed(lines):
        entry = _parse_line(line)
        if entry is None:
            continue
        if category is not None and entry.category != category:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return tuple(entries)
