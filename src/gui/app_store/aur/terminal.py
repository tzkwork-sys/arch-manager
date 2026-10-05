from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, QProcess, Signal, Slot

from src.app_store.aur.errors import AurTransactionBusy
from src.app_store.aur.planner import validate_aur_package_name
from src.app_store.package_coordinator import (
    PackageCoordinatorBusy,
    PackageOperationLease,
    PackageTransactionCoordinator,
    get_package_transaction_coordinator,
)


_ALLOWED_ACTIONS = {"install", "remove", "update", "update-all"}


@dataclass(frozen=True, slots=True)
class AurTerminalResult:
    action: str
    state: str
    detail: str
    return_code: int


class AurTerminalProcess(QObject):
    """Launch the fixed Stage 7.3/7.4 AUR runner in a real terminal."""

    completed = Signal(object)
    failed = Signal(str)
    running_changed = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        coordinator: PackageTransactionCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self.coordinator = coordinator or get_package_transaction_coordinator()
        self._lease: PackageOperationLease | None = None
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)
        self._status_path: Path | None = None
        self._running = False
        self._failure_reported = False
        self._diagnostic_tail = ""
        self._action = "install"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def action(self) -> str | None:
        return self._action if self._running else None

    def start(
        self,
        terminal_launcher: Path,
        *,
        package_name: str | None = None,
        action: str = "install",
    ) -> None:
        if self._running:
            raise AurTransactionBusy("AUR-операция уже выполняется.")
        operation = (action or "").strip().lower()
        if operation not in _ALLOWED_ACTIONS:
            raise ValueError(f"unsupported AUR terminal action: {action}")
        package = None if operation == "update-all" else validate_aur_package_name(package_name or "")
        if not terminal_launcher.is_file():
            raise RuntimeError(f"AUR terminal launcher not found: {terminal_launcher}")
        if not os.access(terminal_launcher, os.X_OK):
            raise RuntimeError(f"AUR terminal launcher is not executable: {terminal_launcher}")

        try:
            self._lease = self.coordinator.acquire(f"aur-{operation}")
        except PackageCoordinatorBusy as exc:
            raise AurTransactionBusy(str(exc)) from exc

        fd, status_name = tempfile.mkstemp(prefix="arch-manager-aur-", suffix=".status")
        os.close(fd)
        self._status_path = Path(status_name)
        self._status_path.unlink(missing_ok=True)
        self._failure_reported = False
        self._diagnostic_tail = ""
        self._action = operation

        self._process.setProgram(str(terminal_launcher))
        arguments = ["--action", operation]
        if package is not None:
            arguments.extend(["--package", package])
        arguments.extend(["--status-file", status_name])
        self._process.setArguments(arguments)
        self._running = True
        self.running_changed.emit(True)
        self._process.start()
        if not self._process.waitForStarted(5000):
            self._running = False
            self.running_changed.emit(False)
            self._cleanup_status_file()
            self._release_lease()
            raise RuntimeError(self._process.errorString() or "AUR terminal did not start")

    def terminate(self) -> None:
        if self._running:
            self._process.terminate()

    @Slot()
    def _read_output(self) -> None:
        raw = bytes(self._process.readAllStandardOutput())
        if not raw:
            return
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            self._diagnostic_tail = (self._diagnostic_tail + "\n" + text)[-3000:]

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, return_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        action = self._action
        self._running = False
        self.running_changed.emit(False)
        raw = ""
        if self._status_path is not None:
            try:
                raw = self._status_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                raw = ""
        self._cleanup_status_file()
        self._release_lease()

        if not raw:
            if not self._failure_reported:
                detail = self._diagnostic_tail.strip() or (
                    f"Терминал AUR закрылся без итогового статуса (код {return_code})."
                )
                self.failed.emit(detail)
            return

        fields = raw.split("\t", 1)
        state = fields[0].strip() if fields else "failed"
        detail = fields[1].strip() if len(fields) > 1 else ""
        if state == "running":
            state = "interrupted"
            detail = "Терминал был закрыт до завершения AUR-операции."
        self.completed.emit(AurTerminalResult(action, state, detail, return_code))

    @Slot(QProcess.ProcessError)
    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.Crashed and self._running:
            return
        if self._failure_reported:
            return
        self._failure_reported = True
        if not self._running:
            self._release_lease()
        self.failed.emit(self._process.errorString() or "Ошибка запуска AUR-терминала")

    def _cleanup_status_file(self) -> None:
        if self._status_path is not None:
            self._status_path.unlink(missing_ok=True)
        self._status_path = None

    def _release_lease(self) -> None:
        if self._lease is None:
            return
        lease = self._lease
        self._lease = None
        self.coordinator.release(lease)
