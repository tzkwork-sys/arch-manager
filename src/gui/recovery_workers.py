from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from src.core.recovery import collect_recovery_fingerprint, collect_recovery_readiness
from src.core.recovery_executor import (
    RecoveryExecutionError,
    delete_broken_roots,
    execute_recovery_action,
    execute_recovery_usb_action,
)
from src.core.recovery_usb import collect_recovery_usb_devices

LOGGER = logging.getLogger(__name__)

class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _RecoveryWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_recovery_readiness())
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            LOGGER.exception("Recovery readiness check failed")
            self.signals.failed.emit(str(exc))


class _RecoveryFingerprintWorker(QRunnable):
    """Cheap re-entry probe: compare metadata/stat fingerprint, not large file hashes."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_recovery_fingerprint())
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            LOGGER.debug("Recovery fingerprint check failed: %s", exc)
            self.signals.failed.emit(str(exc))


class _RecoveryActionWorker(QRunnable):
    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(execute_recovery_action(self.action))
        except RecoveryExecutionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:  # pragma: no cover - defensive UI boundary
            LOGGER.exception("Recovery action failed")
            self.signals.failed.emit("Не удалось выполнить действие восстановления.")


class _RecoveryUsbScanWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_recovery_usb_devices())
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            LOGGER.exception("Recovery USB scan failed")
            self.signals.failed.emit(str(exc))


class _RecoveryUsbActionWorker(QRunnable):
    def __init__(self, action: str, device: str, identity: str) -> None:
        super().__init__()
        self.action = action
        self.device = device
        self.identity = identity
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(
                execute_recovery_usb_action(self.action, self.device, self.identity)
            )
        except RecoveryExecutionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:  # pragma: no cover - defensive UI boundary
            LOGGER.exception("Recovery USB action failed")
            self.signals.failed.emit("Не удалось выполнить действие с Recovery-флешкой.")


class _BrokenRootsDeleteWorker(QRunnable):
    def __init__(self, roots: tuple[str, ...]) -> None:
        super().__init__()
        self.roots = roots
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(delete_broken_roots(self.roots))
        except RecoveryExecutionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:  # pragma: no cover - defensive UI boundary
            LOGGER.exception("Old root deletion failed")
            self.signals.failed.emit("Не удалось удалить сохранённую прежнюю систему.")


