"""Portable, strictly validated Arch Manager settings backups (no secrets)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile

from .maintenance import validate_cache_keep_versions
from .preferences import (
    AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT,
    AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
    MAINTENANCE_PACKAGE_CACHE_KEEP_DEFAULT,
    MAINTENANCE_PACKAGE_CACHE_KEEP_KEY,
)
from .restore_point_policy import RestorePointPolicyState

FORMAT = "arch-manager-settings"
VERSION = 1
MAX_BYTES = 65536
BOOL_FIELDS = ("NUMBER_CLEANUP", "TIMELINE_CREATE", "TIMELINE_CLEANUP")
INT_FIELDS = (
    "NUMBER_MIN_AGE", "NUMBER_LIMIT", "NUMBER_LIMIT_IMPORTANT",
    "TIMELINE_MIN_AGE", "TIMELINE_LIMIT_HOURLY", "TIMELINE_LIMIT_DAILY",
    "TIMELINE_LIMIT_WEEKLY", "TIMELINE_LIMIT_MONTHLY", "TIMELINE_LIMIT_YEARLY",
)
TIMER_FIELDS = ("cleanup_timer_enabled", "timeline_timer_enabled")
POLICY_FIELDS = (
    "NUMBER_CLEANUP", "NUMBER_MIN_AGE", "NUMBER_LIMIT",
    "NUMBER_LIMIT_IMPORTANT", "TIMELINE_CREATE", "TIMELINE_CLEANUP",
    "TIMELINE_MIN_AGE", "TIMELINE_LIMIT_HOURLY", "TIMELINE_LIMIT_DAILY",
    "TIMELINE_LIMIT_WEEKLY", "TIMELINE_LIMIT_MONTHLY", "TIMELINE_LIMIT_YEARLY",
)
MAX_NUMBER = 86400000
MAX_LIMIT = 10000
AGE_FIELDS = ("NUMBER_MIN_AGE", "TIMELINE_MIN_AGE")


def preference_values(settings: object) -> dict[str, object]:
    """Copy only supported preferences, never arbitrary QSettings keys."""
    return {
        AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY: settings.value(
            AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
            AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT, type=bool,
        ),
        MAINTENANCE_PACKAGE_CACHE_KEEP_KEY: settings.value(
            MAINTENANCE_PACKAGE_CACHE_KEEP_KEY,
            MAINTENANCE_PACKAGE_CACHE_KEEP_DEFAULT, type=int,
        ),
    }


def _validated_preferences(values: object) -> dict[str, object]:
    if not isinstance(values, dict) or set(values) != {
        AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
        MAINTENANCE_PACKAGE_CACHE_KEEP_KEY,
    }:
        raise ValueError("Неверный набор параметров Arch Manager.")
    protected = values[AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY]
    keep = values[MAINTENANCE_PACKAGE_CACHE_KEEP_KEY]
    if not isinstance(protected, bool):
        raise ValueError("Настройка защиты обновлений должна быть логической.")
    try:
        keep = validate_cache_keep_versions(keep)
    except ValueError as exc:
        raise ValueError("Некорректное число сохраняемых версий кэша.") from exc
    return {
        AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY: protected,
        MAINTENANCE_PACKAGE_CACHE_KEEP_KEY: keep,
    }


def _validated_policy(values: object) -> dict[str, object] | None:
    if values is None:
        return None
    if not isinstance(values, dict) or set(values) != set(POLICY_FIELDS + TIMER_FIELDS):
        raise ValueError("В файле неполный или неизвестный набор параметров Snapper.")
    result: dict[str, object] = {}
    for key in BOOL_FIELDS:
        if type(values[key]) is not bool:
            raise ValueError(f"Неверный флаг Snapper: {key}.")
        result[key] = values[key]
    for key in INT_FIELDS:
        value = values[key]
        maximum = MAX_NUMBER if key in AGE_FIELDS else MAX_LIMIT
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"Недопустимое значение Snapper: {key}.")
        result[key] = value
    for key in TIMER_FIELDS:
        if type(values[key]) is not bool:
            raise ValueError(f"Неверное состояние таймера: {key}.")
        result[key] = values[key]
    return result


def policy_values(state: RestorePointPolicyState | None) -> dict[str, object] | None:
    if state is None or not state.configured or not state.readable:
        return None
    raw = {
        "NUMBER_CLEANUP": state.number_cleanup,
        "NUMBER_MIN_AGE": state.number_min_age,
        "NUMBER_LIMIT": state.number_limit,
        "NUMBER_LIMIT_IMPORTANT": state.important_limit,
        "TIMELINE_CREATE": state.timeline_create,
        "TIMELINE_CLEANUP": state.timeline_cleanup,
        "TIMELINE_MIN_AGE": state.timeline_min_age,
        "TIMELINE_LIMIT_HOURLY": state.timeline_hourly,
        "TIMELINE_LIMIT_DAILY": state.timeline_daily,
        "TIMELINE_LIMIT_WEEKLY": state.timeline_weekly,
        "TIMELINE_LIMIT_MONTHLY": state.timeline_monthly,
        "TIMELINE_LIMIT_YEARLY": state.timeline_yearly,
        "cleanup_timer_enabled": state.cleanup_timer_enabled,
        "timeline_timer_enabled": state.timeline_timer_enabled,
    }
    try:
        return _validated_policy(raw)
    except ValueError:
        # A system whose policy cannot be read in full must not produce a
        # misleading incomplete restore set.
        return None


def make_backup(preferences: object, system_policy: object) -> dict[str, object]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "preferences": _validated_preferences(preferences),
        "snapper": _validated_policy(system_policy),
    }


def validate_backup(data: object) -> dict[str, object]:
    if not isinstance(data, dict) or set(data) != {
        "format", "version", "saved_at", "preferences", "snapper"
    }:
        raise ValueError("Файл не является резервной копией настроек Arch Manager.")
    if data["format"] != FORMAT or type(data["version"]) is not int or data["version"] != VERSION:
        raise ValueError("Неподдерживаемый формат или версия резервной копии.")
    if not isinstance(data["saved_at"], str) or len(data["saved_at"]) > 64:
        raise ValueError("Некорректная дата сохранения настроек.")
    try:
        when = datetime.fromisoformat(data["saved_at"])
        if when.tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise ValueError("Некорректная дата сохранения настроек.") from exc
    return {
        "format": FORMAT,
        "version": VERSION,
        "saved_at": data["saved_at"],
        "preferences": _validated_preferences(data["preferences"]),
        "snapper": _validated_policy(data["snapper"]),
    }


def load_backup(path: Path) -> dict[str, object]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
        raise ValueError("Ожидается обычный JSON-файл настроек размером до 64 КиБ.")
    # Keep verification independent of Qt and disallow symlink path tricks.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Файл настроек слишком большой.")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Файл настроек повреждён или не является JSON.") from exc
    return validate_backup(raw)


def save_backup(path: Path, data: dict[str, object]) -> None:
    validated = validate_backup(data)
    encoded = (json.dumps(validated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Резервная копия превышает допустимый размер.")
    # Atomic write: a failed save never leaves a partially-written JSON.
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".arch-manager-settings-", suffix=".tmp", dir=path.parent,
            mode="wb", delete=False,
        ) as stream:
            name = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def apply_preferences(settings: object, preferences: object) -> None:
    values = _validated_preferences(preferences)
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()
    if hasattr(settings, "status") and settings.status() != 0:
        raise OSError("Не удалось записать настройки Arch Manager.")


def policy_helper_arguments(policy: object) -> tuple[str, ...]:
    values = _validated_policy(policy)
    if values is None:
        raise ValueError("Нет системной политики для восстановления.")
    args: list[str] = ["policy-restore"]
    for key in POLICY_FIELDS:
        value = values[key]
        args.append(("yes" if value else "no") if key in BOOL_FIELDS else str(value))
    for key in TIMER_FIELDS:
        args.append("yes" if values[key] else "no")
    return tuple(args)
