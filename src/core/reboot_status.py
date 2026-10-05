from __future__ import annotations

from dataclasses import dataclass
import platform
from pathlib import Path


@dataclass(frozen=True)
class RebootStatus:
    state: str
    summary: str
    detail: str

    @property
    def recommended(self) -> bool:
        return self.state == "recommended"


def assess_reboot_status() -> RebootStatus:
    """Return a conservative, read-only reboot recommendation for Arch Linux.

    There is no universal reboot-required flag on Arch. We therefore only mark
    reboot as recommended for strong signals and otherwise explicitly report
    that no obvious kernel-level signal was detected.
    """
    explicit_flag = Path("/run/reboot-required")
    if explicit_flag.exists():
        return RebootStatus(
            "recommended",
            "Перезагрузка рекомендуется.",
            "В системе присутствует /run/reboot-required.",
        )

    running_kernel = platform.uname().release.strip()
    if not running_kernel:
        return RebootStatus(
            "unknown",
            "Не удалось определить необходимость перезагрузки.",
            "Не удалось определить версию запущенного ядра.",
        )

    modules_root = Path("/usr/lib/modules")
    if not modules_root.is_dir():
        return RebootStatus(
            "unknown",
            "Не удалось определить необходимость перезагрузки.",
            "/usr/lib/modules недоступен.",
        )

    running_modules = modules_root / running_kernel
    if not running_modules.exists():
        return RebootStatus(
            "recommended",
            "Перезагрузка рекомендуется: ядро системы было заменено.",
            (
                f"Запущено ядро {running_kernel}, но его каталог модулей уже отсутствует. "
                "Это типичный признак обновления ядра после текущей загрузки."
            ),
        )

    return RebootStatus(
        "not-detected",
        "Явных признаков обязательной перезагрузки не обнаружено.",
        (
            f"Запущенное ядро {running_kernel} всё ещё имеет свой каталог модулей. "
            "Это консервативная проверка: отдельные приложения или службы после крупных "
            "обновлений всё равно могут потребовать перезапуска."
        ),
    )
