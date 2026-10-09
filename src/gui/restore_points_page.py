from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.dashboard import DashboardSummary
from src.core.formatting import format_datetime
from src.core.restore_points import (
    RestorePoint,
    RestorePointsListResult,
)

from .restore_point_technical_dialog import RestorePointTechnicalDialog
from .page_base import NavigablePage
from .restore_point_workers import (
    _AccessSetupWorker,
    _CreateRestorePointWorker,
    _DeleteRestorePointWorker,
    _RenameRestorePointWorker,
    _RestorePointsWorker,
    _SetImportanceWorker,
)
from .restore_point_actions_mixin import RestorePointActionsMixin
from .theme import card_frame, muted_text

LOGGER = logging.getLogger(__name__)


def _count_text(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} точка восстановления"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} точки восстановления"
    return f"{count} точек восстановления"


class RestorePointsPage(RestorePointActionsMixin, NavigablePage):
    checked_at_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, embedded: bool = False):
        super().__init__(parent)
        self._embedded = embedded
        self._thread_pool = QThreadPool.globalInstance()
        self._worker: _RestorePointsWorker | None = None
        self._access_worker: _AccessSetupWorker | None = None
        self._create_worker: _CreateRestorePointWorker | None = None
        self._rename_worker: _RenameRestorePointWorker | None = None
        self._importance_worker: _SetImportanceWorker | None = None
        self._delete_worker: _DeleteRestorePointWorker | None = None
        self._loaded_once = False
        self._all_points: tuple[RestorePoint, ...] = ()
        self._current_point_id: int | None = None
        self._pending_created_point_name: str | None = None
        self._pending_created_point_id: int | None = None
        self._refreshing_after_create = False
        self._pending_renamed_point_name: str | None = None
        self._refreshing_after_rename = False
        self._pending_importance_name: str | None = None
        self._pending_importance_value: bool | None = None
        self._refreshing_after_importance = False
        self._pending_deleted_name: str | None = None
        self._pending_deleted_row: int | None = None
        self._refreshing_after_delete = False
        self._pending_delete_count = 0

        if embedded:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        else:
            layout = self.create_page_layout("Восстановление")

        self.create_button = QPushButton("Создать точку")
        self.create_button.setIcon(QIcon.fromTheme("list-add"))
        self.create_button.setAutoDefault(False)
        self.create_button.setToolTip(
            "Создать новую точку восстановления Snapper с подтверждением администратора"
        )
        self.create_button.clicked.connect(self._open_create_dialog)

        self.body_stack = QStackedWidget()
        if embedded:
            # A stable viewport prevents Qt from distributing spare window height
            # around the table.  Empty room stays inside the table, as intended.
            self.body_stack.setFixedHeight(306)
            self.body_stack.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
        layout.addWidget(self.body_stack, 1 if not embedded else 0)

        self._build_message_page()
        self._build_workspace_page()

        self.checked_label = self.make_checked_label()
        self.checked_label.setVisible(False)

        self._show_message(
            "Точки восстановления",
            "Список загрузится автоматически при открытии раздела.",
            retry=False,
            setup=False,
        )

    def _build_message_page(self) -> None:
        self.message_page = QWidget()
        message_page_layout = QVBoxLayout(self.message_page)
        message_page_layout.setContentsMargins(0, 6, 0, 0)
        if not self._embedded:
            message_page_layout.addStretch(1)

        self.message_card = card_frame()
        if not self._embedded:
            self.message_card.setMaximumWidth(650)
            self.message_card.setMinimumWidth(440)
        card_layout = QVBoxLayout(self.message_card)
        card_layout.setContentsMargins(26, 24, 26, 24)
        card_layout.setSpacing(12)

        self.message_title = QLabel()
        title_font = self.message_title.font()
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        self.message_title.setFont(title_font)
        self.message_title.setWordWrap(True)

        self.message_text = QLabel()
        self.message_text.setWordWrap(True)
        muted_text(self.message_text)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.setup_access_button = QPushButton("Настроить доступ")
        self.setup_access_button.setAutoDefault(False)
        self.setup_access_button.clicked.connect(self._setup_access)

        self.retry_button = QPushButton("Повторить")
        self.retry_button.setAutoDefault(False)
        self.retry_button.clicked.connect(self.refresh)

        self.empty_create_button = QPushButton("Создать точку")
        self.empty_create_button.setIcon(QIcon.fromTheme("list-add"))
        self.empty_create_button.setAutoDefault(False)
        self.empty_create_button.clicked.connect(self._open_create_dialog)
        self.empty_create_button.setVisible(False)

        actions.addWidget(self.setup_access_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.empty_create_button)
        actions.addStretch(1)

        card_layout.addWidget(self.message_title)
        card_layout.addWidget(self.message_text)
        card_layout.addSpacing(4)
        card_layout.addLayout(actions)

        if self._embedded:
            message_page_layout.addWidget(self.message_card)
            message_page_layout.addStretch(1)
        else:
            message_page_layout.addWidget(
                self.message_card,
                0,
                Qt.AlignmentFlag.AlignHCenter,
            )
            message_page_layout.addStretch(2)
        self.body_stack.addWidget(self.message_page)

    def _build_workspace_page(self) -> None:
        self.workspace_page = QWidget()
        workspace = QVBoxLayout(self.workspace_page)
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(6)

        self.summary_label = QLabel("Точки восстановления")
        summary_font = self.summary_label.font()
        summary_font.setBold(True)
        summary_font.setPointSize(summary_font.pointSize() + 2)
        self.summary_label.setFont(summary_font)

        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.refresh)

        self.delete_selected_button = QPushButton("Удалить выбранные")
        self.delete_selected_button.setIcon(QIcon.fromTheme("edit-delete"))
        self.delete_selected_button.setAutoDefault(False)
        self.delete_selected_button.setEnabled(False)
        self.delete_selected_button.clicked.connect(self._delete_selected_points)

        if not self._embedded:
            header = QHBoxLayout()
            header.addWidget(self.summary_label)
            header.addStretch(1)
            header.addWidget(self.create_button)
            header.addWidget(self.delete_selected_button)
            header.addWidget(self.refresh_button)
            workspace.addLayout(header)

        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        self.state_label.setVisible(False)
        muted_text(self.state_label)
        workspace.addWidget(self.state_label)

        self.operation_card = card_frame()
        self.operation_card.setVisible(False)
        operation_layout = QVBoxLayout(self.operation_card)
        operation_layout.setContentsMargins(16, 14, 16, 14)
        operation_layout.setSpacing(8)

        self.operation_title = QLabel("Подготовка операции")
        operation_title_font = self.operation_title.font()
        operation_title_font.setBold(True)
        self.operation_title.setFont(operation_title_font)

        self.operation_progress = QProgressBar()
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(0)
        self.operation_progress.setTextVisible(True)

        self.operation_note = QLabel("—")
        self.operation_note.setWordWrap(True)
        muted_text(self.operation_note)

        operation_layout.addWidget(self.operation_title)
        operation_layout.addWidget(self.operation_progress)
        operation_layout.addWidget(self.operation_note)
        workspace.addWidget(self.operation_card)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["№", "Название", "Дата", "Причина", "Тип", ""]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        # Keep the reason in the model/tooltips, without repeating it as a column.
        self.table.setColumnHidden(3, True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        if self._embedded:
            # Keep a little empty viewport below short lists instead of shrinking
            # the table to exactly the number of restore points.
            self.table.setFixedHeight(300)
            self.table.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )

        workspace.addWidget(self.table, 1 if not self._embedded else 0)
        self.body_stack.addWidget(self.workspace_page)

    def attach_header_actions(self, layout: QHBoxLayout) -> None:
        """Place embedded restore-point actions in the parent page title row."""
        if not self._embedded:
            return
        layout.addWidget(self.create_button)
        layout.addWidget(self.delete_selected_button)
        layout.addWidget(self.refresh_button)

    def _set_state_text(self, text: str) -> None:
        self.state_label.setText(text)
        self.state_label.setVisible(bool(text.strip()))

    def _show_message(
        self,
        title: str,
        text: str,
        *,
        retry: bool,
        setup: bool,
    ) -> None:
        self.message_title.setText(title)
        self.message_text.setText(text)
        self.retry_button.setVisible(retry)
        self.retry_button.setEnabled(True)
        self.setup_access_button.setVisible(setup)
        self.setup_access_button.setEnabled(True)
        if hasattr(self, "empty_create_button"):
            self.empty_create_button.setVisible(False)
            self.empty_create_button.setEnabled(True)
        self.body_stack.setCurrentWidget(self.message_page)

    def ensure_loaded(self) -> None:
        if (
            not self._loaded_once
            and self._worker is None
            and self._access_worker is None
            and self._create_worker is None
            and self._rename_worker is None
            and self._importance_worker is None
            and self._delete_worker is None
        ):
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        if (
            self._worker is not None
            or self._access_worker is not None
            or self._create_worker is not None
            or self._rename_worker is not None
            or self._importance_worker is not None
            or self._delete_worker is not None
        ):
            return

        if (
            self._refreshing_after_create
            or self._refreshing_after_rename
            or self._refreshing_after_importance
            or self._refreshing_after_delete
        ) and self.body_stack.currentWidget() is self.workspace_page:
            if self._refreshing_after_delete:
                self._set_state_text(
                    "Обновляю список после удаления точки восстановления…"
                )
            elif self._refreshing_after_importance:
                self._set_state_text(
                    "Обновляю статус важности точки восстановления…"
                )
            elif self._refreshing_after_rename:
                self._set_state_text(
                    "Обновляю список после переименования точки восстановления…"
                )
            else:
                self._set_state_text(
                    "Обновляю список после создания точки восстановления…"
                )
        else:
            self._show_message(
                "Загружаю точки восстановления…",
                "Получаю актуальный список.",
                retry=False,
                setup=False,
            )

        worker = _RestorePointsWorker()
        self._worker = worker
        worker.signals.completed.connect(self._apply_result)
        worker.signals.failed.connect(self._show_error)
        self._thread_pool.start(worker)

    @Slot(object)
    def _apply_result(self, result: RestorePointsListResult) -> None:
        self._worker = None
        self._loaded_once = True
        self.set_checked_at(self.checked_label, result.checked_at)
        self.checked_at_changed.emit(result.checked_at)

        if not result.readable:
            self._all_points = ()
            self._current_point_id = None
            self.table.setRowCount(0)
            self.table.clearSelection()

            if not result.configured:
                self._show_message(
                    "Точки восстановления не настроены",
                    "В системе пока нет настроенного хранилища точек восстановления. "
                    "Этот экран ничего не меняет и может только показать уже существующие точки.",
                    retry=True,
                    setup=False,
                )
            elif result.access_required or result.note == "список защищён правами доступа":
                self._show_message(
                    "Не удалось получить точки восстановления",
                    "Arch Manager пока не имеет права безопасно читать этот список. "
                    "Нажмите «Настроить доступ»: система один раз попросит подтверждение администратора, "
                    "после чего просмотр будет работать без запроса пароля.",
                    retry=True,
                    setup=True,
                )
            else:
                detail = result.note or "Сведения сейчас недоступны."
                detail = detail[0].upper() + detail[1:] if detail else detail
                self._show_message(
                    "Не удалось получить точки восстановления",
                    detail,
                    retry=True,
                    setup=False,
                )
            return

        self._all_points = result.points
        count = len(result.points)

        if count == 0:
            deleted_name = self._pending_deleted_name
            deletion_finished = self._refreshing_after_delete
            if deletion_finished:
                self._refreshing_after_delete = False
                self._set_point_action_busy(False)
                self._set_operation_progress_visible(False)
                self._pending_deleted_name = None
                self._pending_deleted_row = None
                self._pending_delete_count = 0
            text = "Доступ к списку работает, но сохранённых состояний системы ещё нет."
            if deletion_finished:
                text = (
                    f"Точка «{deleted_name or 'выбранная точка'}» удалена. "
                    "Список теперь пуст. Можно сразу создать новую точку восстановления."
                )
            self._show_message(
                "Точек восстановления пока нет",
                text,
                retry=True,
                setup=False,
            )
            self.empty_create_button.setVisible(True)
            return

        self._set_state_text("")

        self.body_stack.setCurrentWidget(self.workspace_page)
        self._rebuild_table()

        if self._refreshing_after_create:
            self._refreshing_after_create = False
            self._set_create_busy(False)
            self._set_operation_progress_visible(False)
            created_name = self._pending_created_point_name
            created_id = self._pending_created_point_id
            display_index = self._display_index_for_point_number(created_id)
            note = "Точка восстановления создана и добавлена в список."
            if created_name:
                note = f"Точка «{created_name}» создана и добавлена в список."
            if display_index is not None:
                note += f" Сейчас она отображается как №{display_index}."
            if created_id is not None:
                note += f" Технический Snapper ID: {created_id}."
            self._set_state_text(note)
            self._pending_created_point_name = None
            self._pending_created_point_id = None

        if self._refreshing_after_rename:
            self._refreshing_after_rename = False
            self._set_rename_busy(False)
            self._set_operation_progress_visible(False)
            renamed_name = self._pending_renamed_point_name
            note = "Точка восстановления переименована."
            if renamed_name:
                note = f"Точка переименована в «{renamed_name}»."
            self._set_state_text(note)
            self._pending_renamed_point_name = None

        if self._refreshing_after_importance:
            self._refreshing_after_importance = False
            self._set_point_action_busy(False)
            self._set_operation_progress_visible(False)
            name = self._pending_importance_name or "Точка восстановления"
            important = self._pending_importance_value
            if important is True:
                self._set_state_text(f"Точка «{name}» помечена как важная.")
            elif important is False:
                self._set_state_text(f"Точка «{name}» больше не помечена как важная.")
            self._pending_importance_name = None
            self._pending_importance_value = None

        if self._refreshing_after_delete:
            self._refreshing_after_delete = False
            self._set_point_action_busy(False)
            self._set_operation_progress_visible(False)
            deleted_name = self._pending_deleted_name or "Точка восстановления"
            deleted_count = self._pending_delete_count or 1
            if self.table.rowCount() > 0 and self._pending_deleted_row is not None and deleted_count == 1:
                row = min(self._pending_deleted_row, self.table.rowCount() - 1)
                self.table.selectRow(max(0, row))
            if deleted_count > 1:
                self._set_state_text(f"Удалено точек восстановления: {deleted_count}.")
            else:
                self._set_state_text(
                    f"Точка «{deleted_name}» удалена. Нумерация списка пересчитана автоматически."
                )
            self._pending_deleted_name = None
            self._pending_deleted_row = None
            self._pending_delete_count = 0

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self._worker = None
        action_completed = any(
            (
                self._refreshing_after_create,
                self._refreshing_after_rename,
                self._refreshing_after_importance,
                self._refreshing_after_delete,
            )
        )
        if self._refreshing_after_create:
            self._refreshing_after_create = False
            self._set_create_busy(False)
            self._set_operation_progress_visible(False)
        if self._refreshing_after_rename:
            self._refreshing_after_rename = False
            self._set_rename_busy(False)
            self._set_operation_progress_visible(False)
        if self._refreshing_after_importance:
            self._refreshing_after_importance = False
            self._set_point_action_busy(False)
            self._set_operation_progress_visible(False)
        if self._refreshing_after_delete:
            self._refreshing_after_delete = False
            self._set_point_action_busy(False)
            self._set_operation_progress_visible(False)
        LOGGER.error("Restore-point UI worker failed: %s", message)
        if action_completed:
            title = "Действие выполнено, но список не обновился"
            text = (
                "Изменение точки восстановления уже выполнено, но Arch Manager не смог "
                "перечитать список. Нажмите «Повторить», чтобы получить актуальное состояние. "
                "Не повторяйте само действие до обновления списка."
            )
        else:
            title = "Не удалось получить точки восстановления"
            text = "Проверка завершилась ошибкой. Никаких изменений в системе не выполнялось."
        self._show_message(
            title,
            text,
            retry=True,
            setup=False,
        )

    @Slot()
    def _setup_access(self) -> None:
        if (
            self._access_worker is not None
            or self._worker is not None
            or self._create_worker is not None
            or self._rename_worker is not None
            or self._importance_worker is not None
            or self._delete_worker is not None
        ):
            return

        script = Path(__file__).resolve().parents[2] / "scripts" / "setup-restore-points-read-access.sh"
        self._show_message(
            "Настраиваю безопасный доступ…",
            "Подтвердите действие в системном окне. Это требуется только один раз и даёт Arch Manager право только читать список точек.",
            retry=False,
            setup=False,
        )

        worker = _AccessSetupWorker(script, os.getuid())
        self._access_worker = worker
        worker.signals.completed.connect(self._access_setup_finished)
        self._thread_pool.start(worker)

    @Slot(bool, str)
    def _access_setup_finished(self, success: bool, details: str) -> None:
        self._access_worker = None
        if success:
            LOGGER.info("Restore-point read access configured")
            self._loaded_once = False
            self.refresh()
            return

        LOGGER.warning("Restore-point read access setup failed: %s", details)
        if "rc=126" in details or "dismiss" in details.casefold() or "cancel" in details.casefold():
            text = "Настройка была отменена. Можно повторить её в любой момент."
        else:
            text = (
                "Не удалось настроить доступ. Никакие точки восстановления не изменялись. "
                "Повторите попытку или закройте системное окно подтверждения и попробуйте снова."
            )
        self._show_message(
            "Доступ не настроен",
            text,
            retry=True,
            setup=True,
        )























    def _display_index_for_point_number(self, point_number: int | None) -> int | None:
        if point_number is None:
            return None
        for index, point in enumerate(self._filtered_sorted_points(), start=1):
            if point.number == point_number:
                return index
        return None

    def _filtered_sorted_points(self) -> list[RestorePoint]:
        points = list(self._all_points)
        points.sort(
            key=lambda point: point.created_at.timestamp() if point.created_at else float("-inf"),
            reverse=True,
        )
        return points

    @Slot()
    def _rebuild_table(self) -> None:
        selected_id = self._current_point_id
        points = self._filtered_sorted_points()

        self.table.blockSignals(True)
        self.table.setRowCount(len(points))
        row_to_select: int | None = None
        for row, point in enumerate(points):
            values = (
                str(row + 1),
                point.display_name.removeprefix("Arch Manager: "),
                format_datetime(point.created_at),
                point.reason,
                "★ Важная" if point.important else "Обычная",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, point.number)
                    item.setToolTip(f"{point.display_name}\nПричина: {point.reason}")
                if column == 4:
                    item.setToolTip("Важная точка: отдельный лимит хранения в политике Snapper." if point.important else "Обычная точка восстановления.")
                if point.important and column == 4:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, column, item)

            actions_button = QToolButton()
            actions_button.setText("⋯")
            actions_button.setAutoRaise(True)
            actions_button.setToolTip("Действия с точкой")
            actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            actions_menu = QMenu(actions_button)
            rename_action = actions_menu.addAction("Переименовать")
            rename_action.triggered.connect(
                lambda checked=False, current=point: self._open_rename_dialog(current)
            )
            importance_text = (
                "Убрать из важных" if point.important else "Пометить как важную"
            )
            importance_action = actions_menu.addAction(importance_text)
            importance_action.triggered.connect(
                lambda checked=False, current=point: self._toggle_point_importance(current)
            )
            technical_action = actions_menu.addAction("Техническая информация…")
            technical_action.setIcon(QIcon.fromTheme("dialog-information"))
            technical_action.triggered.connect(
                lambda checked=False, current=point: self._open_technical_dialog(current)
            )
            actions_menu.addSeparator()
            delete_action = actions_menu.addAction("Удалить…")
            delete_action.setIcon(QIcon.fromTheme("edit-delete"))
            delete_action.triggered.connect(
                lambda checked=False, current=point: self._open_delete_dialog(current)
            )
            actions_button.setMenu(actions_menu)
            self.table.setCellWidget(row, 5, actions_button)

            if selected_id == point.number:
                row_to_select = row

        self.table.blockSignals(False)

        if self._embedded:
            self.table.setFixedHeight(300)
            self.body_stack.setFixedHeight(306)

        if row_to_select is not None:
            self.table.selectRow(row_to_select)
        else:
            self._current_point_id = None
            self.table.clearSelection()

    @Slot()
    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        count = len(rows)
        busy = self._any_mutation_running()
        self.delete_selected_button.setEnabled(count > 0 and not busy)
        self.delete_selected_button.setText(
            f"Удалить выбранные ({count})" if count > 0 else "Удалить выбранные"
        )
        row = self.table.currentRow()
        if row < 0:
            self._current_point_id = None
            return
        item = self.table.item(row, 1)
        if item is None:
            return
        point_id = item.data(Qt.ItemDataRole.UserRole)
        self._current_point_id = point_id if isinstance(point_id, int) else None

    def _selected_points(self) -> list[RestorePoint]:
        ids: list[int] = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 1)
            if item is None:
                continue
            value = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, int):
                ids.append(value)
        wanted = set(ids)
        return [point for point in self._all_points if point.number in wanted]


    def _open_technical_dialog(self, point: RestorePoint) -> None:
        if self._any_mutation_running():
            return
        self._current_point_id = point.number
        self._select_point_in_table(point.number)
        RestorePointTechnicalDialog(point, self).exec()

    def set_summary(self, summary: DashboardSummary) -> None:
        # The list loads richer metadata than the dashboard summary.
        if not self._loaded_once:
            self.set_checked_at(self.checked_label, summary.checked_at)
            self.checked_at_changed.emit(summary.checked_at)
