from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import QObject, QProcess, Signal, Slot

from src.app_store.package_coordinator import (
    PackageCoordinatorBusy,
    PackageOperationLease,
    PackageTransactionCoordinator,
    get_package_transaction_coordinator,
)


PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")


@dataclass(frozen=True)
class UpdateSessionResult:
    state: str
    detail: str
    restore_point: int | None = None


class UpdateSessionProcess(QObject):
    """Launch the fixed update runner in a real terminal window.

    Arch package managers are intentionally interactive.  A real terminal is the
    most reliable place for administrator password entry, pacman confirmations and yay
    questions.  The GUI never receives the password and never constructs a shell
    command: validated package names are passed as argv to the fixed launcher.
    """

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

    @property
    def running(self) -> bool:
        return self._running

    def start(
        self,
        terminal_launcher: Path,
        *,
        official_updates: bool,
        aur_packages: tuple[str, ...],
        create_restore_point: bool,
    ) -> None:
        if self._running:
            raise RuntimeError("update session is already running")
        if not terminal_launcher.is_file():
            raise RuntimeError(f"update terminal launcher not found: {terminal_launcher}")

        validated: list[str] = []
        for name in aur_packages:
            if not PACKAGE_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid package name: {name!r}")
            validated.append(name)

        try:
            self._lease = self.coordinator.acquire("system-update")
        except PackageCoordinatorBusy as exc:
            raise RuntimeError(str(exc)) from exc

        fd, status_name = tempfile.mkstemp(prefix="arch-manager-update-", suffix=".status")
        os.close(fd)
        self._status_path = Path(status_name)
        self._status_path.unlink(missing_ok=True)
        self._failure_reported = False
        self._diagnostic_tail = ""

        arguments = [
            "--official",
            "yes" if official_updates else "no",
            "--restore-point",
            "yes" if create_restore_point else "no",
            "--status-file",
            status_name,
        ]
        for name in validated:
            arguments.extend(("--aur-package", name))

        self._process.setProgram(str(terminal_launcher))
        self._process.setArguments(arguments)

        self._running = True
        self.running_changed.emit(True)
        self._process.start()
        if not self._process.waitForStarted(5000):
            self._running = False
            self.running_changed.emit(False)
            self._cleanup_status_file()
            self._release_lease()
            raise RuntimeError(self._process.errorString() or "update terminal did not start")

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
            self._diagnostic_tail = (self._diagnostic_tail + "\n" + text)[-2000:]

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, return_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
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
                diagnostic = self._diagnostic_tail.strip()
                if diagnostic:
                    self.failed.emit(diagnostic)
                else:
                    self.failed.emit(
                        "Терминал обновления закрылся без итогового статуса "
                        f"(код {return_code})."
                    )
            return

        fields = raw.split("\t", 2)
        state = fields[0].strip() if fields else "failed"
        detail = fields[1].strip() if len(fields) > 1 else ""
        if state == "running":
            state = "interrupted"
            detail = "Терминал был закрыт до завершения операции."
        restore_point: int | None = None
        if len(fields) > 2 and fields[2].strip():
            try:
                candidate = int(fields[2].strip())
            except ValueError:
                candidate = 0
            if candidate > 0:
                restore_point = candidate
        self.completed.emit(UpdateSessionResult(state, detail, restore_point))

    @Slot(QProcess.ProcessError)
    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.Crashed and self._running:
            return
        if self._failure_reported:
            return
        self._failure_reported = True
        if not self._running:
            self._release_lease()
        self.failed.emit(self._process.errorString() or "Ошибка запуска терминала обновления")

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
