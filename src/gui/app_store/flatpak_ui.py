"""Non-root Flatpak actions using QProcess and KDE's normal Polkit agent."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, QProcess, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app_store.flatpak import FlatpakAction, FlatpakService
from src.app_store.models import Application
from src.app_store.package_coordinator import get_package_transaction_coordinator
from src.core.activity_log import append_activity
from src.core.update_state import get_update_state_service


class FlatpakOperationProcess(QObject):
    completed = Signal()
    failed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        self._queue: list[FlatpakAction] = []
        self._lease = None
        self._current = None
        self._output = ""
        self._running = False
        self._coordinator = get_package_transaction_coordinator()
        self._process.readyReadStandardOutput.connect(self._read_output)

    @property
    def running(self) -> bool:
        return self._running

    def start(self, actions: tuple[FlatpakAction, ...]) -> None:
        if self._running or not actions:
            raise RuntimeError("Нет операций или сеанс уже выполняется")
        # Validate the entire batch before running the first command.
        for action in actions:
            action.argv()
        self._lease = self._coordinator.acquire("flatpak")
        self._queue = list(actions)
        self._running = True
        self.running_changed.emit(True)
        self._next()

    def _next(self) -> None:
        if not self._queue:
            self._finish()
            get_update_state_service().invalidate()
            self.completed.emit()
            return
        self._current = self._queue.pop(0)
        self._output = ""
        command = self._current.argv()
        self._process.setProgram(command[0])
        self._process.setArguments(list(command[1:]))
        self._process.start()

    def _read_output(self) -> None:
        chunk = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._output = (self._output + chunk)[-4000:]

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        if not self._running:
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            message = self._output.strip() or f"Flatpak завершился с кодом {code}"
            self._fail(message)
            return
        action = self._current
        if action is not None:
            append_activity("app-store", action.operation, "success", f"Flatpak: {action.app_id}", data={"application_name": action.app_id, "package_name": action.app_id, "source": "flatpak"})
        self._next()

    @Slot(QProcess.ProcessError)
    def _error(self, error: QProcess.ProcessError) -> None:
        if self._running and error == QProcess.ProcessError.FailedToStart:
            self._fail(self._process.errorString() or "Не удалось запустить Flatpak")

    def _fail(self, message: str) -> None:
        if not self._running:
            return
        self._finish()
        get_update_state_service().invalidate()
        self.failed.emit(message)

    def _finish(self) -> None:
        self._running = False
        self._queue.clear()
        if self._lease is not None:
            self._coordinator.release(self._lease)
            self._lease = None
        self.running_changed.emit(False)


class FlatpakPackageDetailsDialog(QDialog):
    def __init__(self, application: Application, parent=None):
        super().__init__(parent)
        self.application = application
        self.package_state_changed = False
        self._runner = FlatpakOperationProcess(self)
        self.setWindowTitle(f"{application.name} — Flatpak")
        self.setMinimumWidth(490)
        root = QVBoxLayout(self)
        heading = QLabel(application.name)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        heading.setWordWrap(True)
        root.addWidget(heading)
        description = QLabel(application.summary or "Приложение из Flathub")
        description.setWordWrap(True)
        root.addWidget(description)
        scope = application.metadata_source.removeprefix("flatpak:")
        root.addWidget(QLabel(f"Источник: {application.repository or 'Flatpak'}\nID: {application.package_name}\nУстановка: {scope}\nВерсия: {application.installed_version or application.available_version or '—'}"))
        self.status = QLabel("Установлено" if application.installed else "Не установлено")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.action = QPushButton("Удалить" if application.installed else "Установить")
        self.action.clicked.connect(self._act)
        buttons.addWidget(self.action)
        if application.installed:
            self.update_button = QPushButton("Обновить")
            self.update_button.clicked.connect(lambda: self._act("update"))
            buttons.addWidget(self.update_button)
            self.launch_button = QPushButton("Запустить")
            self.launch_button.clicked.connect(self._launch)
            buttons.addWidget(self.launch_button)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)
        self._runner.completed.connect(self._completed)
        self._runner.failed.connect(self._failed)
        self._runner.running_changed.connect(self._busy)

    def _act(self, operation: str | bool = False) -> None:
        op = operation if isinstance(operation, str) else ("remove" if self.application.installed else "install")
        answer = QMessageBox.question(self, "Подтвердить Flatpak", f"{op}: {self.application.name}\n{self.application.package_name}\nОперация выполняется через Flatpak, без pacman.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        scope = self.application.metadata_source.removeprefix("flatpak:")
        try:
            self._runner.start((FlatpakAction(op, self.application.package_name, scope),))
        except Exception as exc:
            QMessageBox.warning(self, "Flatpak", str(exc))

    def _launch(self) -> None:
        try:
            subprocess.Popen(["flatpak", "run", f"--{self.application.metadata_source.removeprefix('flatpak:')}", self.application.package_name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            QMessageBox.warning(self, "Запуск Flatpak", str(exc))

    @Slot(bool)
    def _busy(self, running: bool) -> None:
        self.action.setEnabled(not running)
        self.close_button.setEnabled(not running)
        for field in ("update_button", "launch_button"):
            if hasattr(self, field):
                getattr(self, field).setEnabled(not running)
        if running:
            self.status.setText("Flatpak выполняет операцию…")

    @Slot()
    def _completed(self) -> None:
        self.package_state_changed = True
        self.status.setText("Операция завершена. Обновите каталог для актуального состояния.")
        self.action.setEnabled(False)

    @Slot(str)
    def _failed(self, error: str) -> None:
        self.status.setText("Не удалось выполнить операцию")
        QMessageBox.warning(self, "Ошибка Flatpak", error)

    def reject(self) -> None:
        if not self._runner.running:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._runner.running:
            event.ignore()
        else:
            super().closeEvent(event)
