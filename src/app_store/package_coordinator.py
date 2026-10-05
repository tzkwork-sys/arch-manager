from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid


class PackageCoordinatorBusy(RuntimeError):
    """Raised when another Arch Manager package mutation is already active."""


@dataclass(frozen=True, slots=True)
class PackageOperationLease:
    token: str
    kind: str


class PackageTransactionCoordinator:
    """Small in-process coordinator shared by official and AUR transactions.

    This deliberately knows nothing about pacman or yay.  It only serializes
    package-changing workflows started by this Arch Manager process.  Native
    pacman locking remains the final cross-process protection.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: PackageOperationLease | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    @property
    def active_kind(self) -> str | None:
        with self._lock:
            return self._active.kind if self._active is not None else None

    def acquire(self, kind: str) -> PackageOperationLease:
        operation = (kind or "").strip()
        if not operation:
            raise ValueError("package operation kind must not be empty")
        with self._lock:
            if self._active is not None:
                raise PackageCoordinatorBusy(
                    "Другая пакетная операция Arch Manager уже выполняется."
                )
            lease = PackageOperationLease(uuid.uuid4().hex, operation)
            self._active = lease
            return lease

    def release(self, lease: PackageOperationLease) -> None:
        if not isinstance(lease, PackageOperationLease):
            raise TypeError("lease must be PackageOperationLease")
        with self._lock:
            if self._active is None:
                return
            if self._active.token != lease.token:
                raise RuntimeError("package operation lease does not own the coordinator")
            self._active = None


_DEFAULT_COORDINATOR = PackageTransactionCoordinator()


def get_package_transaction_coordinator() -> PackageTransactionCoordinator:
    return _DEFAULT_COORDINATOR
