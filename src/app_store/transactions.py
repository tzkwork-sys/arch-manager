from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from collections.abc import Callable

from src.core.app_store_executor import (
    PackageActionBusy,
    PackageActionCancelled,
    PackageActionExecutionError,
    PackageActionFailed,
    PackageActionTimedOut,
    PackageActionUnavailable,
    PackageActionUpdateRequired,
    PackageAuthorizationError,
    PackageHelperUnavailable,
    execute_package_action,
)

from .actions import PackageActionRequest
from .launcher import launch_application
from .models import Application
from .package_actions import PacmanPackageActionPlanner, PackageBusyError, PackageTransactionPlan
from .package_coordinator import (
    PackageCoordinatorBusy,
    PackageTransactionCoordinator,
    get_package_transaction_coordinator,
)


class PackageTransactionBusy(RuntimeError):
    pass


class PackageActionService:
    """Coordinate read-only planning and one mutating package transaction at a time."""

    def __init__(
        self,
        *,
        planner: PacmanPackageActionPlanner | None = None,
        executor: Callable[[PackageActionRequest], object] = execute_package_action,
        launcher: Callable[[Application], None] = launch_application,
        coordinator: PackageTransactionCoordinator | None = None,
    ) -> None:
        self.planner = planner or PacmanPackageActionPlanner()
        self.executor = executor
        self.launcher = launcher
        self.coordinator = coordinator or get_package_transaction_coordinator()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="arch-manager-app-actions")
        self._state_lock = threading.Lock()
        self._transaction_active = False

    @property
    def busy(self) -> bool:
        with self._state_lock:
            local_busy = self._transaction_active
        return local_busy or self.coordinator.busy

    def plan_async(self, request: PackageActionRequest) -> Future[PackageTransactionPlan]:
        if self.busy:
            future: Future[PackageTransactionPlan] = Future()
            future.set_exception(PackageTransactionBusy("Другая пакетная операция уже выполняется."))
            return future
        return self._pool.submit(self.planner.plan, request)

    def execute_async(self, request: PackageActionRequest) -> Future[object]:
        with self._state_lock:
            if self._transaction_active:
                future: Future[object] = Future()
                future.set_exception(PackageTransactionBusy("Другая пакетная операция уже выполняется."))
                return future
            self._transaction_active = True

        try:
            lease = self.coordinator.acquire("official-app-store")
        except PackageCoordinatorBusy as exc:
            with self._state_lock:
                self._transaction_active = False
            future = Future()
            future.set_exception(PackageTransactionBusy(str(exc)))
            return future

        def run():
            try:
                return self.executor(request)
            finally:
                self.coordinator.release(lease)
                with self._state_lock:
                    self._transaction_active = False

        return self._pool.submit(run)

    def launch_async(self, application: Application) -> Future[None]:
        return self._pool.submit(self.launcher, application)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=False)
