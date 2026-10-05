from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RestorePointPolicyAction(str, Enum):
    APPLY_RECOMMENDED = "policy-apply-recommended"
    SET_TIMELINE = "timeline-set"


class RestorePointPolicyValidationError(ValueError):
    """Raised when a restore-point policy request is invalid."""


@dataclass(frozen=True)
class RestorePointPolicyRequest:
    action: RestorePointPolicyAction
    enabled: bool | None = None

    def helper_arguments(self) -> tuple[str, ...]:
        if self.action is RestorePointPolicyAction.APPLY_RECOMMENDED:
            return (self.action.value,)
        if self.action is RestorePointPolicyAction.SET_TIMELINE:
            assert self.enabled is not None
            return (self.action.value, "yes" if self.enabled else "no")
        raise AssertionError(f"Unsupported restore-point policy action: {self.action!r}")


def build_apply_recommended_policy_request() -> RestorePointPolicyRequest:
    return RestorePointPolicyRequest(RestorePointPolicyAction.APPLY_RECOMMENDED)


def build_set_timeline_request(enabled: bool) -> RestorePointPolicyRequest:
    if not isinstance(enabled, bool):
        raise RestorePointPolicyValidationError("timeline flag must be boolean")
    return RestorePointPolicyRequest(
        RestorePointPolicyAction.SET_TIMELINE,
        enabled=enabled,
    )
