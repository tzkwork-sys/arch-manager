"""Non-root Flatpak actions using QProcess and KDE's normal Polkit agent."""
from __future__ import annotations

import subprocess
from dataclasses import replace

from PySide6.QtCore import QObject, QProcess, Signal, Slot, Qt, QSize, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app_store.flatpak import FlatpakAction, FlatpakService
from src.app_store.flathub_metadata import FlathubMetadata, FlathubMetadataService
from src.app_store.media import MediaCache
from src.gui.app_store.details import ApplicationDetailsDialog
from src.gui.theme import muted_text
from src.app_store.models import Application
from src.app_store.package_coordinator import get_package_transaction_coordinator
from src.core.activity_log import append_activity
from src.core.update_state import get_update_state_service
from src.gui.app_store.widgets import _application_icon


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


class _FlathubSignals(QObject):
    metadata_loaded = Signal(object, int)
    metadata_failed = Signal(str, int)
    icon_loaded = Signal(object, int)


class FlatpakPackageDetailsDialog(ApplicationDetailsDialog):
    """Flatpak-specific actions atop the same rich details gallery as Official.

    Only metadata rendering is shared. Flatpak operations never enter the
    official/pacman planner or privileged helpers.
    """

    def __init__(self, application: Application, parent=None, *,
                 media_cache: MediaCache | None = None,
                 metadata_service: FlathubMetadataService | None = None):
        self._rich_ready = False
        self._flatpak_running = False
        self._flatpak_completed = False
        self._metadata_requested = False
        self._metadata_generation = 0
        self._icon_future = None
        super().__init__(application, parent, media_cache=media_cache)
        self.setWindowTitle(f"{application.name} — Flatpak — Arch Manager")
        self.setObjectName("appStoreFlatpakDetailsDialog")

        # Keep the Flatpak-only Update action from the original details dialog.
        header_layout = self.body_layout.itemAt(0).layout()
        action_layout = header_layout.itemAt(header_layout.count() - 1).layout()
        self.update_button = QPushButton("Обновить")
        self.update_button.setToolTip("Обновить это приложение через Flatpak")
        self.update_button.clicked.connect(self._update_requested)
        action_layout.insertWidget(1, self.update_button)

        self._runner = FlatpakOperationProcess(self)
        self._runner.completed.connect(self._completed)
        self._runner.failed.connect(self._failed)
        self._runner.running_changed.connect(self._busy)

        self._metadata_service = metadata_service or FlathubMetadataService()
        self._flathub_signals = _FlathubSignals(self)
        self._flathub_signals.metadata_loaded.connect(self._metadata_loaded)
        self._flathub_signals.metadata_failed.connect(self._metadata_failed)
        self._flathub_signals.icon_loaded.connect(self._icon_loaded)

        self._metadata_status = QLabel("Подробные сведения Flathub будут загружены при открытии окна…")
        muted_text(self._metadata_status)
        self._metadata_status.setWordWrap(True)
        self.body_layout.insertWidget(1, self._metadata_status)

        scope = self.application.metadata_source.removeprefix("flatpak:")
        scope_name = "общесистемная" if scope == "system" else "пользовательская"
        self._scope_label = QLabel(
            f"Источник: {self.application.repository or 'Flatpak'}  ·  "
            f"{('Установка' if self.application.installed else 'Предлагаемая установка')}: {scope_name}"
        )
        self._scope_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._scope_label.setWordWrap(True)
        muted_text(self._scope_label)
        self.body_layout.insertWidget(1, self._scope_label)
        self._refresh_package_controls()

    def _build_header(self) -> None:
        super()._build_header()
        # Reuse the existing header icon QLabel; remote icon is optional.
        header = self.body_layout.itemAt(0).layout()
        self._header_icon = header.itemAt(0).widget() if header is not None else None

    def _build_screenshots(self) -> None:
        if self._rich_ready:
            super()._build_screenshots()

    def _build_description(self) -> None:
        if self._rich_ready:
            super()._build_description()

    def _build_details(self) -> None:
        if self._rich_ready:
            super()._build_details()

    def _build_links(self) -> None:
        if self._rich_ready:
            super()._build_links()

    def _refresh_package_controls(self) -> None:
        if self._flatpak_completed:
            self.status_label.setText("Операция завершена. Обновите каталог для актуального состояния.")
        else:
            self.status_label.setText("✓ Установлено" if self.application.installed else "Не установлено")
        self.action_button.setText("Удалить" if self.application.installed else "Установить")
        self.action_button.setToolTip("Операция Flatpak, без использования pacman или yay")
        self.action_button.setEnabled(not self._flatpak_running and not self._flatpak_completed)
        self.launch_button.setVisible(self.application.installed)
        self.launch_button.setEnabled(self.application.installed and not self._flatpak_running)
        if hasattr(self, "update_button"):
            self.update_button.setVisible(self.application.installed)
            self.update_button.setEnabled(self.application.installed and not self._flatpak_running and not self._flatpak_completed)
        self.package_progress.setVisible(self._flatpak_running)
        if hasattr(self, "close_button"):
            self.close_button.setEnabled(not self._flatpak_running)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._metadata_requested:
            self._metadata_requested = True
            QTimer.singleShot(0, self._request_metadata)

    def _request_metadata(self) -> None:
        if not self.isVisible():
            return
        self._metadata_generation += 1
        generation = self._metadata_generation
        self._metadata_status.setText("Получаю описание, скриншоты и ссылки из Flathub…")
        try:
            future = self._metadata_service.load_async(self.application.package_name)
        except Exception as exc:
            self._flathub_signals.metadata_failed.emit(str(exc), generation)
            return
        future.add_done_callback(lambda f, g=generation: self._metadata_future_finished(f, g))

    def _metadata_future_finished(self, future, generation: int) -> None:
        try:
            result = future.result()
        except Exception as exc:
            try:
                self._flathub_signals.metadata_failed.emit(str(exc), generation)
            except RuntimeError:
                pass
        else:
            try:
                self._flathub_signals.metadata_loaded.emit(result, generation)
            except RuntimeError:
                pass

    @Slot(object, int)
    def _metadata_loaded(self, metadata: object, generation: int) -> None:
        if generation != self._metadata_generation or not isinstance(metadata, FlathubMetadata):
            return
        if metadata.app_id != self.application.package_name:
            self._metadata_failed("Получены данные другого приложения", generation)
            return
        self._metadata_status.setText("")
        self._metadata_status.setVisible(False)
        self.application = replace(
            self.application,
            description_html=metadata.description_html or self.application.description_html,
            screenshots=metadata.screenshots,
            homepage=metadata.homepage or self.application.homepage,
            bugtracker=metadata.bugtracker or self.application.bugtracker,
            help_url=metadata.help_url or self.application.help_url,
            license=metadata.license or self.application.license,
            publisher=metadata.publisher or self.application.publisher,
        )
        self._rich_ready = True
        # Initially the base dialog built only the Flatpak header. Insert the
        # now-available rich sections before the pre-existing stretch.
        last = self.body_layout.takeAt(self.body_layout.count() - 1)
        del last
        self._build_screenshots()
        self._build_description()
        self._build_details()
        self._build_links()
        self.body_layout.addStretch(1)
        if self._tiles:
            QTimer.singleShot(0, self._load_all_screenshots)
        if metadata.icon_url and self._header_icon is not None:
            future = self.media_cache.load_async(metadata.icon_url)
            self._icon_future = future
            future.add_done_callback(lambda f, g=generation: self._icon_future_finished(f, g))

    def _icon_future_finished(self, future, generation: int) -> None:
        try:
            value = future.result().data
        except Exception:
            return
        try:
            self._flathub_signals.icon_loaded.emit(value, generation)
        except RuntimeError:
            pass

    @Slot(object, int)
    def _icon_loaded(self, value: object, generation: int) -> None:
        if generation != self._metadata_generation or self._header_icon is None:
            return
        if not isinstance(value, (bytes, bytearray)):
            return
        icon = QPixmap()
        if icon.loadFromData(value):
            self._header_icon.setText("")
            self._header_icon.setPixmap(icon.scaled(
                QSize(88, 88), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    @Slot(str, int)
    def _metadata_failed(self, message: str, generation: int) -> None:
        if generation != self._metadata_generation:
            return
        self._metadata_status.setText(
            "Расширенные сведения Flathub сейчас недоступны. "
            "Основные данные и установка Flatpak продолжают работать."
        )
        self._metadata_status.setToolTip(message)

    @Slot()
    def _package_action_requested(self) -> None:
        self._run_flatpak_action("remove" if self.application.installed else "install")

    @Slot()
    def _update_requested(self) -> None:
        self._run_flatpak_action("update")

    def _run_flatpak_action(self, op: str) -> None:
        if self._flatpak_running or self._flatpak_completed:
            return
        if op == "update" and not self.application.installed:
            return
        verb = {"install": "Установить", "remove": "Удалить", "update": "Обновить"}[op]
        question = (f"{verb} {self.application.name}?\n"
                    f"Flatpak ID: {self.application.package_name}\n"
                    "Операция выполняется через Flatpak, не через pacman.")
        answer = QMessageBox.question(self, "Подтвердить Flatpak", question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        scope = self.application.metadata_source.removeprefix("flatpak:")
        try:
            self._runner.start((FlatpakAction(op, self.application.package_name, scope),))
        except Exception as exc:
            QMessageBox.warning(self, "Flatpak", str(exc))

    @Slot()
    def _launch_requested(self) -> None:
        if not self.application.installed or self._flatpak_running:
            return
        try:
            subprocess.Popen(
                ["flatpak", "run", f"--{self.application.metadata_source.removeprefix('flatpak:')}",
                 self.application.package_name],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Запуск Flatpak", str(exc))

    @Slot(bool)
    def _busy(self, running: bool) -> None:
        self._flatpak_running = running
        self._refresh_package_controls()
        if running:
            self.status_label.setText("Flatpak выполняет операцию…")

    @Slot()
    def _completed(self) -> None:
        self.package_state_changed = True
        self._flatpak_completed = True
        self._refresh_package_controls()

    @Slot(str)
    def _failed(self, error: str) -> None:
        self._refresh_package_controls()
        self.status_label.setText("Операция Flatpak не выполнена")
        QMessageBox.warning(self, "Ошибка Flatpak", error)

    def accept(self) -> None:
        if not self._flatpak_running:
            super().accept()

    def reject(self) -> None:
        if not self._flatpak_running:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._flatpak_running:
            event.ignore()
            return
        self._metadata_generation += 1
        super().closeEvent(event)
