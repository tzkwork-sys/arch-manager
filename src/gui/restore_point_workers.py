from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from src.core.restore_point_actions import RestorePointActionRequest
from src.core.restore_point_executor import RestorePointActionExecutionError, execute_restore_point_action
from src.core.restore_points import collect_restore_points_list

LOGGER = logging.getLogger(__name__)

class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _RestorePointsWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_restore_points_list())
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Restore-point check failed")
            self.signals.failed.emit(str(exc))


class _RestorePointActionSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class _CreateRestorePointWorker(QRunnable):
    def __init__(self, request: RestorePointActionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _RestorePointActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_restore_point_action(self.request)
        except RestorePointActionExecutionError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Unexpected restore-point create failure")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class _RenameRestorePointWorker(QRunnable):
    def __init__(self, request: RestorePointActionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _RestorePointActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_restore_point_action(self.request)
        except RestorePointActionExecutionError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Unexpected restore-point rename failure")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class _SetImportanceWorker(QRunnable):
    def __init__(self, request: RestorePointActionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _RestorePointActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_restore_point_action(self.request)
        except RestorePointActionExecutionError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Unexpected restore-point importance failure")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class _DeleteRestorePointWorker(QRunnable):
    def __init__(self, request: RestorePointActionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _RestorePointActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_restore_point_action(self.request)
        except RestorePointActionExecutionError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Unexpected restore-point delete failure")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class _AccessWorkerSignals(QObject):
    completed = Signal(bool, str)


class _AccessSetupWorker(QRunnable):
    def __init__(self, script: Path, uid: int) -> None:
        super().__init__()
        self.script = script
        self.uid = uid
        self.signals = _AccessWorkerSignals()

    @Slot()
    def run(self) -> None:
        if not self.script.is_file():
            self.signals.completed.emit(False, "setup script is missing")
            return

        env = os.environ.copy()
        try:
            completed = subprocess.run(
                [str(self.script), str(self.uid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.signals.completed.emit(False, "timeout")
            return
        except OSError as exc:
            self.signals.completed.emit(False, str(exc))
            return

        details = "\n".join((completed.stdout, completed.stderr)).strip()
        if completed.returncode == 0:
            self.signals.completed.emit(True, details)
        else:
            self.signals.completed.emit(False, f"rc={completed.returncode}\n{details}")


