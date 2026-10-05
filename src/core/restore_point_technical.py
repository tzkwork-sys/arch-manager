from __future__ import annotations

from .restore_points import RestorePoint


def build_restore_point_technical_text(point: RestorePoint) -> str:
    """Return low-level restore-point metadata without duplicating table fields."""
    lines = [
        f"Snapper ID: {point.number}",
        f"Создатель: {point.creator or 'не указан'}",
        f"Политика очистки: {point.cleanup or 'не указана'}",
        f"Userdata: {point.userdata or 'нет'}",
    ]
    if point.pre_number is not None:
        lines.append(f"Связана с точкой до изменения: {point.pre_number}")
    return "\n".join(lines)
