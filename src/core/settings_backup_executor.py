"""Restore the fixed Snapper settings subset via the existing Polkit boundary."""
from __future__ import annotations

import subprocess

from .restore_point_executor import (
    HELPER_PATH, PKEXEC_PATH, _ensure_runtime_ready,
    RestorePointActionCancelled, RestorePointActionFailed,
    RestorePointActionTimedOut, RestorePointAuthorizationError,
    RestorePointHelperUnavailable,
)
from .settings_backup import policy_helper_arguments


def apply_saved_snapper_policy(policy: object, *, timeout: float = 120.0) -> None:
    arguments = policy_helper_arguments(policy)
    _ensure_runtime_ready()
    try:
        result = subprocess.run(
            [str(PKEXEC_PATH), "--user", "root", str(HELPER_PATH), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RestorePointActionTimedOut("Восстановление системной политики превысило время ожидания.") from exc
    except OSError as exc:
        raise RestorePointHelperUnavailable("Не удалось запустить системный helper.") from exc
    detail = result.stderr.strip()[:500]
    if result.returncode == 126:
        raise RestorePointActionCancelled("Авторизация отменена.", detail=detail)
    if result.returncode == 127:
        raise RestorePointAuthorizationError("Не удалось получить административные права.", detail=detail)
    if result.returncode != 0 or result.stdout.strip() != "OK\tpolicy\trestored":
        raise RestorePointActionFailed(
            "Системная политика не восстановлена полностью.",
            returncode=result.returncode, detail=detail,
        )
