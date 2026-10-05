from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QMessageBox

from src.core.restore_point_actions import (
    RestorePointActionRequest,
    RestorePointActionValidationError,
    build_delete_request,
    build_delete_many_request,
    build_set_importance_request,
)
from src.core.restore_point_executor import (
    RestorePointActionCancelled,
    RestorePointActionFailed,
    RestorePointActionResult,
    RestorePointActionTimedOut,
    RestorePointAuthorizationError,
    RestorePointHelperUnavailable,
)
from src.core.restore_points import RestorePoint

from .create_restore_point_dialog import CreateRestorePointDialog
from .delete_restore_point_dialog import DeleteRestorePointDialog
from .rename_restore_point_dialog import RenameRestorePointDialog
from .restore_point_workers import (
    _CreateRestorePointWorker,
    _DeleteRestorePointWorker,
    _RenameRestorePointWorker,
    _SetImportanceWorker,
)

LOGGER = logging.getLogger(__name__)


class RestorePointActionsMixin:
    """Create/rename/importance/delete workflow for RestorePointsPage."""
    @Slot()
    def _open_create_dialog(self) -> None:
        if (
            self._create_worker is not None
            or self._rename_worker is not None
            or self._importance_worker is not None
            or self._delete_worker is not None
            or self._worker is not None
            or self._access_worker is not None
        ):
            return

        dialog = CreateRestorePointDialog(self)
        if dialog.exec() != CreateRestorePointDialog.DialogCode.Accepted:
            return

        try:
            request = dialog.build_request()
        except RestorePointActionValidationError as exc:
            QMessageBox.warning(
                self,
                "Arch Manager",
                f"Проверьте название точки восстановления.\n\n{exc}",
            )
            return

        self._start_create_request(request)
    def _start_create_request(self, request: RestorePointActionRequest) -> None:
        self._pending_created_point_name = request.description
        self._pending_created_point_id = None
        self._refreshing_after_create = False
        self._set_create_busy(True)
        self._set_operation_progress(
            title="Создаю точку восстановления…",
            note=(
                "Шаг 1 из 4. Подготавливаю запрос. "
                "Далее система может попросить пароль администратора."
            ),
            value=15,
        )
        worker = _CreateRestorePointWorker(request)
        self._create_worker = worker
        worker.signals.completed.connect(self._create_succeeded)
        worker.signals.failed.connect(self._create_failed)
        self._thread_pool.start(worker)
        self._set_operation_progress(
            title="Создаю точку восстановления…",
            note=(
                "Шаг 2 из 4. Ожидаю подтверждение администратора и завершение операции Snapper."
            ),
            value=45,
        )
    def _set_create_busy(self, busy: bool) -> None:
        self.create_button.setEnabled(not busy)
        self.create_button.setText("Создание…" if busy else "Создать точку")
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(not busy)
        if hasattr(self, "delete_selected_button"):
            self.delete_selected_button.setEnabled(False if busy else bool(self.table.selectionModel().selectedRows()))
        if hasattr(self, "setup_access_button"):
            self.setup_access_button.setEnabled(not busy)
        if hasattr(self, "empty_create_button"):
            self.empty_create_button.setEnabled(not busy)
        if hasattr(self, "table"):
            self.table.setEnabled(not busy)
    def _set_operation_progress(self, *, title: str, note: str, value: int) -> None:
        self.operation_title.setText(title)
        self.operation_note.setText(note)
        self.operation_progress.setValue(max(0, min(100, value)))
        self._set_operation_progress_visible(True)
    def _set_operation_progress_visible(self, visible: bool) -> None:
        self.operation_card.setVisible(visible)
        if not visible:
            self.operation_progress.setValue(0)
            self.operation_note.setText("—")
    @Slot(object)
    def _create_succeeded(self, result: RestorePointActionResult) -> None:
        self._create_worker = None
        self._current_point_id = result.point_number
        self._pending_created_point_id = result.point_number
        self._loaded_once = False
        self._refreshing_after_create = True
        self._set_operation_progress(
            title="Создаю точку восстановления…",
            note="Шаг 3 из 4. Точка создана. Обновляю список в интерфейсе.",
            value=80,
        )
        self.refresh()
    @Slot(object)
    def _create_failed(self, error: object) -> None:
        self._create_worker = None
        self._refreshing_after_create = False
        self._pending_created_point_id = None
        self._pending_created_point_name = None
        self._set_create_busy(False)
        self._set_operation_progress_visible(False)

        if isinstance(error, RestorePointActionCancelled):
            self._show_action_feedback(
                "Создание отменено. Никаких изменений не выполнено."
            )
            return

        if isinstance(error, RestorePointAuthorizationError):
            message = "Не удалось получить административное разрешение."
        elif isinstance(error, RestorePointHelperUnavailable):
            message = (
                "Системный helper Arch Manager недоступен или установлен небезопасно. "
                "Повторно установите интеграцию Stage 4."
            )
        elif isinstance(error, RestorePointActionTimedOut):
            message = "Создание точки восстановления не завершилось вовремя."
        elif isinstance(error, RestorePointActionFailed):
            message = "Snapper не смог создать точку восстановления."
        else:
            message = "Не удалось создать точку восстановления из-за непредвиденной ошибки."

        detail = getattr(error, "detail", "")
        LOGGER.error("Restore-point creation failed: %s; detail=%s", error, detail)
        QMessageBox.warning(
            self,
            "Точка восстановления не создана",
            message + "\n\nСуществующие точки восстановления не изменялись.",
        )
    def _show_action_feedback(self, text: str) -> None:
        if self.body_stack.currentWidget() is self.workspace_page:
            self.state_label.setText(text)
        else:
            self.message_text.setText(text)
    def _select_point_in_table(self, point_number: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == point_number:
                self.table.clearSelection()
                self.table.selectRow(row)
                return
    def _open_rename_dialog(self, point: RestorePoint) -> None:
        if (
            self._rename_worker is not None
            or self._create_worker is not None
            or self._importance_worker is not None
            or self._delete_worker is not None
            or self._worker is not None
            or self._access_worker is not None
        ):
            return

        self._current_point_id = point.number
        self._select_point_in_table(point.number)

        dialog = RenameRestorePointDialog(point, self)
        if dialog.exec() != RenameRestorePointDialog.DialogCode.Accepted:
            return

        try:
            request = dialog.build_request()
        except RestorePointActionValidationError as exc:
            QMessageBox.warning(
                self,
                "Arch Manager",
                f"Проверьте новое название точки восстановления.\n\n{exc}",
            )
            return

        self._pending_renamed_point_name = request.description
        self._set_rename_busy(True)
        self._set_operation_progress(
            title="Переименовываю точку восстановления…",
            note=(
                "Шаг 1 из 3. Подготавливаю запрос. "
                "Системный Snapper ID точки останется прежним."
            ),
            value=20,
        )

        worker = _RenameRestorePointWorker(request)
        self._rename_worker = worker
        worker.signals.completed.connect(self._rename_succeeded)
        worker.signals.failed.connect(self._rename_failed)
        self._thread_pool.start(worker)
        self._set_operation_progress(
            title="Переименовываю точку восстановления…",
            note="Шаг 2 из 3. Ожидаю подтверждение администратора и ответ Snapper.",
            value=55,
        )
    def _set_point_action_busy(self, busy: bool) -> None:
        self.create_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.delete_selected_button.setEnabled(False if busy else bool(self.table.selectionModel().selectedRows()))
        self.empty_create_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
    def _set_rename_busy(self, busy: bool) -> None:
        self._set_point_action_busy(busy)
    @Slot(object)
    def _rename_succeeded(self, result: RestorePointActionResult) -> None:
        self._rename_worker = None
        self._current_point_id = result.point_number
        self._loaded_once = False
        self._refreshing_after_rename = True
        self._set_operation_progress(
            title="Переименовываю точку восстановления…",
            note="Шаг 3 из 3. Название изменено. Обновляю список в интерфейсе.",
            value=85,
        )
        self.refresh()
    @Slot(object)
    def _rename_failed(self, error: object) -> None:
        self._rename_worker = None
        self._refreshing_after_rename = False
        self._set_rename_busy(False)
        self._set_operation_progress_visible(False)

        if isinstance(error, RestorePointActionCancelled):
            self._show_action_feedback(
                "Переименование отменено. Название точки не изменилось."
            )
            self._pending_renamed_point_name = None
            return

        if isinstance(error, RestorePointAuthorizationError):
            message = "Не удалось получить административное разрешение."
        elif isinstance(error, RestorePointHelperUnavailable):
            message = (
                "Системный helper Arch Manager недоступен или установлен небезопасно. "
                "Повторно установите интеграцию Stage 4."
            )
        elif isinstance(error, RestorePointActionTimedOut):
            message = "Переименование точки восстановления не завершилось вовремя."
        elif isinstance(error, RestorePointActionFailed):
            message = "Snapper не смог переименовать точку восстановления."
        else:
            message = "Не удалось переименовать точку из-за непредвиденной ошибки."

        detail = getattr(error, "detail", "")
        LOGGER.error("Restore-point rename failed: %s; detail=%s", error, detail)
        self._pending_renamed_point_name = None
        QMessageBox.warning(
            self,
            "Точка восстановления не переименована",
            message + "\n\nДругие точки восстановления не изменялись.",
        )
    def _toggle_point_importance(self, point: RestorePoint) -> None:
        if self._any_mutation_running():
            return

        self._current_point_id = point.number
        self._select_point_in_table(point.number)
        wanted = not point.important
        try:
            request = build_set_importance_request(point.number, wanted)
        except RestorePointActionValidationError as exc:
            QMessageBox.warning(self, "Arch Manager", f"Не удалось подготовить действие.\n\n{exc}")
            return

        self._pending_importance_name = point.display_name
        self._pending_importance_value = wanted
        self._set_point_action_busy(True)
        self._set_operation_progress(
            title="Изменяю статус точки…",
            note=(
                "Шаг 1 из 3. Подготавливаю изменение статуса. "
                "Остальные userdata Snapper будут сохранены."
            ),
            value=20,
        )

        worker = _SetImportanceWorker(request)
        self._importance_worker = worker
        worker.signals.completed.connect(self._importance_succeeded)
        worker.signals.failed.connect(self._importance_failed)
        self._thread_pool.start(worker)
        self._set_operation_progress(
            title="Изменяю статус точки…",
            note="Шаг 2 из 3. Ожидаю подтверждение администратора и ответ Snapper.",
            value=55,
        )
    @Slot(object)
    def _importance_succeeded(self, result: RestorePointActionResult) -> None:
        self._importance_worker = None
        self._current_point_id = result.point_number
        self._loaded_once = False
        self._refreshing_after_importance = True
        self._set_operation_progress(
            title="Изменяю статус точки…",
            note="Шаг 3 из 3. Статус изменён. Обновляю список и счётчики.",
            value=85,
        )
        self.refresh()
    @Slot(object)
    def _importance_failed(self, error: object) -> None:
        self._importance_worker = None
        self._refreshing_after_importance = False
        self._set_point_action_busy(False)
        self._set_operation_progress_visible(False)

        if isinstance(error, RestorePointActionCancelled):
            self._show_action_feedback("Изменение статуса отменено. Точка не изменена.")
            self._pending_importance_name = None
            self._pending_importance_value = None
            return

        message = self._mutation_error_message(error, "изменить статус точки восстановления")
        detail = getattr(error, "detail", "")
        LOGGER.error("Restore-point importance change failed: %s; detail=%s", error, detail)
        self._pending_importance_name = None
        self._pending_importance_value = None
        QMessageBox.warning(
            self,
            "Статус точки не изменён",
            message + "\n\nДругие точки восстановления не изменялись.",
        )
    def _open_delete_dialog(self, point: RestorePoint) -> None:
        if self._any_mutation_running():
            return

        self._current_point_id = point.number
        self._select_point_in_table(point.number)
        dialog = DeleteRestorePointDialog(point, self)
        if dialog.exec() != DeleteRestorePointDialog.DialogCode.Accepted:
            return

        try:
            request = build_delete_request(point.number)
        except RestorePointActionValidationError as exc:
            QMessageBox.warning(self, "Arch Manager", f"Не удалось подготовить удаление.\n\n{exc}")
            return

        self._pending_deleted_name = point.display_name
        self._pending_delete_count = 1
        self._pending_deleted_row = self.table.currentRow()
        self._set_point_action_busy(True)
        self._set_operation_progress(
            title="Удаляю точку восстановления…",
            note=(
                "Шаг 1 из 3. Подготавливаю удаление. "
                "После подтверждения Snapper удалит выбранный снимок."
            ),
            value=20,
        )

        worker = _DeleteRestorePointWorker(request)
        self._delete_worker = worker
        worker.signals.completed.connect(self._delete_succeeded)
        worker.signals.failed.connect(self._delete_failed)
        self._thread_pool.start(worker)
        self._set_operation_progress(
            title="Удаляю точку восстановления…",
            note="Шаг 2 из 3. Ожидаю подтверждение администратора и завершение удаления.",
            value=55,
        )
    @Slot(object)
    def _delete_succeeded(self, result: RestorePointActionResult) -> None:
        self._delete_worker = None
        self._current_point_id = None
        self._loaded_once = False
        self._refreshing_after_delete = True
        self._set_operation_progress(
            title="Удаляю точку восстановления…",
            note="Шаг 3 из 3. Точка удалена. Перечитываю список и пересчитываю нумерацию.",
            value=85,
        )
        self.refresh()
    @Slot(object)
    def _delete_failed(self, error: object) -> None:
        self._delete_worker = None
        self._refreshing_after_delete = False
        self._set_point_action_busy(False)
        self._set_operation_progress_visible(False)

        if isinstance(error, RestorePointActionCancelled):
            self._show_action_feedback("Удаление отменено. Точка восстановления сохранена.")
            self._pending_deleted_name = None
            self._pending_deleted_row = None
            self._pending_delete_count = 0
            return

        message = self._mutation_error_message(error, "удалить точку восстановления")
        detail = getattr(error, "detail", "")
        LOGGER.error("Restore-point delete failed: %s; detail=%s", error, detail)
        delete_count = self._pending_delete_count
        self._pending_deleted_name = None
        self._pending_deleted_row = None
        self._pending_delete_count = 0
        if delete_count > 1:
            QMessageBox.warning(
                self,
                "Не все точки могли быть удалены",
                message
                + "\n\nСписок будет перечитан: при пакетной операции часть точек могла быть удалена до ошибки.",
            )
            self._loaded_once = False
            self.refresh()
            return
        QMessageBox.warning(
            self,
            "Точка восстановления не удалена",
            message + "\n\nДругие точки восстановления не изменялись.",
        )
    def _any_mutation_running(self) -> bool:
        return any(
            worker is not None
            for worker in (
                self._create_worker,
                self._rename_worker,
                self._importance_worker,
                self._delete_worker,
            )
        ) or self._worker is not None or self._access_worker is not None
    @staticmethod
    def _mutation_error_message(error: object, action_text: str) -> str:
        if isinstance(error, RestorePointAuthorizationError):
            return "Не удалось получить административное разрешение."
        if isinstance(error, RestorePointHelperUnavailable):
            return (
                "Системный helper Arch Manager недоступен или установлен небезопасно. "
                "Повторно установите интеграцию Stage 4."
            )
        if isinstance(error, RestorePointActionTimedOut):
            return f"Не удалось вовремя {action_text}."
        if isinstance(error, RestorePointActionFailed):
            return f"Snapper не смог {action_text}."
        return f"Не удалось {action_text} из-за непредвиденной ошибки."
    def _delete_selected_points(self) -> None:
        if self._any_mutation_running():
            return
        points = self._selected_points()
        if not points:
            return
        count = len(points)
        preview = "\n".join(f"• {point.display_name}" for point in points[:6])
        if count > 6:
            preview += f"\n• …ещё {count - 6}"
        answer = QMessageBox.warning(
            self,
            "Удалить выбранные точки?",
            f"Будет удалено точек восстановления: {count}.\n\n{preview}\n\n"
            "Операцию нельзя отменить из Arch Manager.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            request = build_delete_many_request([point.number for point in points])
        except RestorePointActionValidationError as exc:
            QMessageBox.warning(self, "Arch Manager", f"Не удалось подготовить удаление.\n\n{exc}")
            return

        self._pending_deleted_name = None
        self._pending_delete_count = count
        self._pending_deleted_row = None
        self._set_point_action_busy(True)
        self._set_operation_progress(
            title="Удаляю выбранные точки восстановления…",
            note="Ожидаю одно подтверждение администратора и завершение удаления Snapper.",
            value=45,
        )
        worker = _DeleteRestorePointWorker(request)
        self._delete_worker = worker
        worker.signals.completed.connect(self._delete_succeeded)
        worker.signals.failed.connect(self._delete_failed)
        self._thread_pool.start(worker)
