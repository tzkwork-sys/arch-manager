from __future__ import annotations

from datetime import datetime


_UNITS = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(max(value, 0))
    unit_index = 0
    while amount >= 1024 and unit_index < len(_UNITS) - 1:
        amount /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{amount:.0f} {_UNITS[unit_index]}"
    if amount >= 100:
        return f"{amount:.0f} {_UNITS[unit_index]}"
    if amount >= 10:
        return f"{amount:.1f} {_UNITS[unit_index]}"
    return f"{amount:.2f} {_UNITS[unit_index]}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "нет данных"
    local = value.astimezone() if value.tzinfo is not None else value
    return local.strftime("%d.%m.%Y, %H:%M")
