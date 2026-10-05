from __future__ import annotations

from dataclasses import dataclass
import subprocess

from .restore_point_executor import (
    HELPER_PATH,
    PKEXEC_PATH,
    RestorePointActionCancelled,
    RestorePointActionFailed,
    RestorePointActionTimedOut,
    RestorePointAuthorizationError,
    RestorePointHelperUnavailable,
    _ensure_runtime_ready,
)
from .restore_point_policy_actions import (
    RestorePointPolicyAction,
    RestorePointPolicyRequest,
    RestorePointPolicyValidationError,
    build_apply_recommended_policy_request,
    build_set_timeline_request,
)


DEFAULT_POLICY_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RestorePointPolicyActionResult:
    action: RestorePointPolicyAction
    enabled: bool | None = None


def _validated_request(request: RestorePointPolicyRequest) -> RestorePointPolicyRequest:
    if not isinstance(request, RestorePointPolicyRequest):
        raise TypeError("request must be RestorePointPolicyRequest")
    if request.action is RestorePointPolicyAction.APPLY_RECOMMENDED:
        return build_apply_recommended_policy_request()
    if request.action is RestorePointPolicyAction.SET_TIMELINE:
        return build_set_timeline_request(request.enabled)
    raise RestorePointPolicyValidationError("unsupported restore-point policy action")


def _parse_success(
    request: RestorePointPolicyRequest,
    stdout: str,
) -> RestorePointPolicyActionResult:
    fields = stdout.strip().split("\t")

    if request.action is RestorePointPolicyAction.APPLY_RECOMMENDED:
        if fields == ["OK", "policy", "recommended"]:
            return RestorePointPolicyActionResult(request.action)
    elif request.action is RestorePointPolicyAction.SET_TIMELINE:
        expected = "yes" if request.enabled else "no"
        if fields == ["OK", "timeline", expected]:
            return RestorePointPolicyActionResult(request.action, request.enabled)

    raise RestorePointActionFailed(
        "Privileged helper вернул неожиданный результат.",
        detail=stdout.strip()[:500],
    )


def execute_restore_point_policy_action(
    request: RestorePointPolicyRequest,
    *,
    timeout: float = DEFAULT_POLICY_TIMEOUT_SECONDS,
) -> RestorePointPolicyActionResult:
    """Execute one fixed restore-point policy command through the Stage 4 helper."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    request = _validated_request(request)
    _ensure_runtime_ready()
    command = (
        str(PKEXEC_PATH),
        "--user",
        "root",
        str(HELPER_PATH),
        *request.helper_arguments(),
    )

    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = exc.stderr if isinstance(exc.stderr, str) else ""
        raise RestorePointActionTimedOut(
            "Изменение политики точек восстановления не завершилось вовремя.",
            returncode=124,
            detail=detail[:500],
        ) from exc
    except OSError as exc:
        raise RestorePointHelperUnavailable(
            "Не удалось запустить системный helper Arch Manager.",
            detail=str(exc),
        ) from exc

    detail = completed.stderr.strip()[:500]
    if completed.returncode == 126:
        raise RestorePointActionCancelled(
            "Авторизация отменена.",
            returncode=126,
            detail=detail,
        )
    if completed.returncode == 127:
        raise RestorePointAuthorizationError(
            "Не удалось получить административное разрешение.",
            returncode=127,
            detail=detail,
        )
    if completed.returncode != 0:
        raise RestorePointActionFailed(
            "Политика точек восстановления не изменена.",
            returncode=completed.returncode,
            detail=detail,
        )

    return _parse_success(request, completed.stdout)
