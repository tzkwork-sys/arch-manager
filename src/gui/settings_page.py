from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.preferences import (
    AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT,
    AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
)
from src.core.restore_point_executor import (
    RestorePointActionCancelled,
    RestorePointActionExecutionError,
    RestorePointActionFailed,
    RestorePointActionTimedOut,
    RestorePointAuthorizationError,
    RestorePointHelperUnavailable,
)
from src.core.restore_point_policy import (
    RestorePointPolicyState,
    collect_restore_point_policy,
)
from src.core.restore_point_policy_actions import (
    RestorePointPolicyRequest,
    build_apply_recommended_policy_request,
    build_set_timeline_request,
)
from src.core.restore_point_policy_executor import (
    RestorePointPolicyActionResult,
    execute_restore_point_policy_action,
)

from .log_page import LogPage
from .page_base import NavigablePage
from .theme import CARD_MARGINS, card_frame, emphasize_primary_button, muted_text

LOGGER = logging.getLogger(__name__)


class _PolicySignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class _PolicyReadWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _PolicySignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_restore_point_policy())
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Restore-point policy read failed")
            self.signals.failed.emit(exc)


class _PolicyActionWorker(QRunnable):
    def __init__(self, request: RestorePointPolicyRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _PolicySignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_restore_point_policy_action(self.request)
        except RestorePointActionExecutionError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("Restore-point policy action failed")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class SettingsPage(NavigablePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings()
        self._thread_pool = QThreadPool.globalInstance()
        self._policy_worker: _PolicyReadWorker | None = None
        self._action_worker: _PolicyActionWorker | None = None
        self._policy_state: RestorePointPolicyState | None = None
        self._loaded_once = False
        self._ignore_timeline_toggle = False
        self._pending_feedback: str | None = None

        layout = self.create_page_layout("Настройки")

        self.refresh_button = QPushButton("Обновить состояние")
        self.refresh_button.clicked.connect(self.refresh_policy)
        self.add_header_action(self.refresh_button)

        self.checked_label = self.make_checked_label()
        layout.addWidget(self.checked_label)

        self.tabs = QTabWidget()
        self.general_tab = QWidget()
        general_layout = QVBoxLayout(self.general_tab)
        general_layout.setContentsMargins(0, 4, 0, 0)
        general_layout.setSpacing(10)
        self._build_update_protection_card(general_layout)
        self._build_timeline_card(general_layout)
        self._build_storage_card(general_layout)
        self._build_status_row(general_layout)
        general_layout.addStretch(1)

        self.log_page = LogPage(self, embedded=True)
        self.tabs.addTab(self.general_tab, "Основные")
        self.tabs.addTab(self.log_page, "Журнал действий")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)

    def _build_update_protection_card(self, layout: QVBoxLayout) -> None:
        card = card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(*CARD_MARGINS)
        card_layout.setSpacing(8)

        title = QLabel("Защита перед обновлением")
        font = title.font()
        font.setBold(True)
        title.setFont(font)

        self.auto_restore = QCheckBox(
            "Создавать важную точку восстановления перед обновлением — рекомендуется"
        )
        self.auto_restore.setChecked(
            self.settings.value(
                AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
                AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT,
                type=bool,
            )
        )
        self.auto_restore.toggled.connect(self._save_auto_before_update)

        note = QLabel(
            "Применяется только к обновлениям через Arch Manager. "
            "Перед установкой автоматически создаётся важная точка восстановления."
        )
        note.setWordWrap(True)
        muted_text(note)

        card_layout.addWidget(title)
        card_layout.addWidget(self.auto_restore)
        card_layout.addWidget(note)
        layout.addSpacing(4)
        layout.addWidget(card)

    def _build_timeline_card(self, layout: QVBoxLayout) -> None:
        card = card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(*CARD_MARGINS)
        card_layout.setSpacing(8)

        title = QLabel("Автоматические точки по времени")
        font = title.font()
        font.setBold(True)
        title.setFont(font)

        self.timeline_check = QCheckBox("Создавать точки автоматически по расписанию")
        self.timeline_check.setEnabled(False)
        self.timeline_check.toggled.connect(self._timeline_toggled)

        self.timeline_status = QLabel("Состояние ещё не проверено.")
        self.timeline_status.setWordWrap(True)
        muted_text(self.timeline_status)

        note = QLabel(
            "При включении используется стандартный системный таймер Snapper "
            "(обычно запуск раз в час). Отключение не удаляет уже созданные точки."
        )
        note.setWordWrap(True)
        muted_text(note)

        card_layout.addWidget(title)
        card_layout.addWidget(self.timeline_check)
        card_layout.addWidget(self.timeline_status)
        card_layout.addWidget(note)
        layout.addWidget(card)

    def _build_storage_card(self, layout: QVBoxLayout) -> None:
        card = card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(*CARD_MARGINS)
        card_layout.setSpacing(8)

        title = QLabel("Хранение и автоочистка")
        font = title.font()
        font.setBold(True)
        title.setFont(font)

        self.storage_status = QLabel("Состояние ещё не проверено.")
        self.storage_status.setWordWrap(True)

        note = QLabel(
            "Рекомендуемая политика Arch Manager: до 10 обычных и 5 важных точек, "
            "автоочистка включена; для точек по времени — 5 почасовых, 7 ежедневных, "
            "4 еженедельных и 3 ежемесячных. Применение политики не меняет ваш выбор "
            "«точки по времени: вкл/выкл» и не удаляет существующие точки немедленно."
        )
        note.setWordWrap(True)
        muted_text(note)

        actions = QHBoxLayout()
        self.apply_policy_button = QPushButton("Применить рекомендуемую политику")
        emphasize_primary_button(self.apply_policy_button)
        self.apply_policy_button.setEnabled(False)
        self.apply_policy_button.clicked.connect(self._apply_recommended_policy)
        actions.addWidget(self.apply_policy_button)
        actions.addStretch(1)

        card_layout.addWidget(title)
        card_layout.addWidget(self.storage_status)
        card_layout.addWidget(note)
        card_layout.addLayout(actions)
        layout.addWidget(card)

    def _build_status_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.policy_progress = QProgressBar()
        self.policy_progress.setMaximumWidth(300)
        self.policy_progress.setVisible(False)
        self.policy_progress.setTextVisible(False)

        self.policy_feedback = QLabel("Откройте настройки, чтобы проверить текущую политику.")
        self.policy_feedback.setWordWrap(True)
        muted_text(self.policy_feedback)

        row.addWidget(self.policy_progress)
        row.addWidget(self.policy_feedback, 1)
        layout.addLayout(row)

    @Slot(int)
    def _tab_changed(self, index: int) -> None:
        journal_open = index == 1
        self.refresh_button.setVisible(not journal_open)
        if journal_open:
            self.log_page.ensure_loaded()

    def ensure_loaded(self) -> None:
        if not self._loaded_once and self._policy_worker is None and self._action_worker is None:
            self.refresh_policy()

    @Slot(bool)
    def _save_auto_before_update(self, checked: bool) -> None:
        self.settings.setValue(AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY, checked)
        self.settings.sync()

    @Slot()
    def refresh_policy(self) -> None:
        if self._policy_worker is not None or self._action_worker is not None:
            return
        self._set_busy(True, "Проверяю политику точек восстановления…")
        worker = _PolicyReadWorker()
        self._policy_worker = worker
        worker.signals.completed.connect(self._policy_loaded)
        worker.signals.failed.connect(self._policy_load_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _policy_loaded(self, state: RestorePointPolicyState) -> None:
        self._policy_worker = None
        self._policy_state = state
        self._loaded_once = True
        self._set_busy(False)
        self.set_checked_at(self.checked_label, datetime.now().astimezone())

        if not state.configured:
            self.timeline_check.setEnabled(False)
            self.apply_policy_button.setEnabled(False)
            self.timeline_status.setText("Snapper для системного раздела пока не настроен.")
            self.storage_status.setText("Политика хранения недоступна до настройки Snapper.")
            self.policy_feedback.setText("Точки восстановления не настроены.")
            return

        if not state.readable:
            self.timeline_check.setEnabled(False)
            self.apply_policy_button.setEnabled(False)
            self.timeline_status.setText("Не удалось прочитать текущие системные настройки.")
            self.storage_status.setText("Текущие лимиты хранения недоступны.")
            self.policy_feedback.setText(state.note or "Не удалось прочитать политику.")
            return

        self._ignore_timeline_toggle = True
        self.timeline_check.setChecked(state.timeline_enabled)
        self._ignore_timeline_toggle = False
        self.timeline_check.setEnabled(True)
        self.apply_policy_button.setEnabled(True)

        if state.timeline_enabled:
            timeline_text = "Включено: точки создаются по расписанию системного таймера."
        elif state.timeline_create or state.timeline_timer_enabled:
            timeline_text = (
                "Настройка частично включена: параметры Snapper и состояние таймера не совпадают. "
                "Переключите режим здесь, чтобы привести их к согласованному состоянию."
            )
        else:
            timeline_text = "Выключено: остаются ручные точки и точки перед обновлениями Arch Manager."
        self.timeline_status.setText(timeline_text)

        normal = state.number_limit if state.number_limit is not None else "—"
        important = state.important_limit if state.important_limit is not None else "—"
        cleanup = "включена" if state.cleanup_timer_enabled and state.number_cleanup else "не полностью включена"
        if state.recommended_storage:
            self.storage_status.setText(
                f"Рекомендуемая политика активна: обычные до {normal}, важные до {important}, автоочистка {cleanup}."
            )
        else:
            self.storage_status.setText(
                f"Текущая политика отличается от рекомендуемой: обычные до {normal}, "
                f"важные до {important}, автоочистка {cleanup}."
            )

        if self._pending_feedback:
            self.policy_feedback.setText(self._pending_feedback)
            self._pending_feedback = None
        else:
            self.policy_feedback.setText(
                "Системная политика прочитана. Изменения требуют отдельного подтверждения администратора."
            )

    @Slot(object)
    def _policy_load_failed(self, error: object) -> None:
        self._policy_worker = None
        self._set_busy(False)
        LOGGER.error("Restore-point policy UI read failed: %s", error)
        self.timeline_check.setEnabled(False)
        self.apply_policy_button.setEnabled(False)
        self.policy_feedback.setText("Не удалось проверить системную политику.")

    @Slot(bool)
    def _timeline_toggled(self, checked: bool) -> None:
        if self._ignore_timeline_toggle:
            return
        state = self._policy_state
        if state is None or not state.readable or self._action_worker is not None:
            self._restore_timeline_checkbox()
            return
        if checked == state.timeline_enabled:
            return

        if checked:
            title = "Включить автоматические точки?"
            text = (
                "Arch Manager включит создание точек по расписанию системного таймера Snapper "
                "и автоочистку старых точек. Существующие точки не изменяются."
            )
        else:
            title = "Выключить автоматические точки?"
            text = (
                "Новые точки по времени создаваться не будут. Ручные точки и защита перед "
                "обновлениями останутся доступны. Существующие точки не удаляются."
            )

        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._restore_timeline_checkbox()
            return

        self._start_policy_action(
            build_set_timeline_request(checked),
            "Изменяю расписание автоматических точек…",
        )

    @Slot()
    def _apply_recommended_policy(self) -> None:
        if self._action_worker is not None or self._policy_worker is not None:
            return
        answer = QMessageBox.question(
            self,
            "Применить рекомендуемую политику?",
            "Будут установлены лимиты хранения Arch Manager и включена системная автоочистка.\n\n"
            "Обычные точки: до 10\n"
            "Важные точки: до 5\n"
            "По времени: 5 почасовых, 7 ежедневных, 4 еженедельных, 3 ежемесячных\n\n"
            "Текущий выбор «автоматические точки по времени» не изменится. "
            "Существующие точки не удаляются немедленно — очистка выполняется Snapper по своим правилам.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_policy_action(
            build_apply_recommended_policy_request(),
            "Применяю рекомендуемую политику хранения…",
        )

    def _start_policy_action(self, request: RestorePointPolicyRequest, message: str) -> None:
        self._set_busy(True, message)
        worker = _PolicyActionWorker(request)
        self._action_worker = worker
        worker.signals.completed.connect(self._policy_action_succeeded)
        worker.signals.failed.connect(self._policy_action_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _policy_action_succeeded(self, result: RestorePointPolicyActionResult) -> None:
        self._action_worker = None
        if result.enabled is True:
            self._pending_feedback = "Автоматические точки по времени включены."
        elif result.enabled is False:
            self._pending_feedback = "Автоматические точки по времени выключены."
        else:
            self._pending_feedback = "Рекомендуемая политика хранения применена."
        self._policy_state = None
        self._set_busy(False)
        self.refresh_policy()

    @Slot(object)
    def _policy_action_failed(self, error: object) -> None:
        self._action_worker = None
        self._set_busy(False)
        self._restore_timeline_checkbox()

        if isinstance(error, RestorePointActionCancelled):
            self.policy_feedback.setText("Изменение настроек отменено.")
            return
        if isinstance(error, RestorePointAuthorizationError):
            message = "Не удалось получить административное разрешение."
        elif isinstance(error, RestorePointHelperUnavailable):
            message = "Системный helper Arch Manager недоступен или установлен небезопасно."
        elif isinstance(error, RestorePointActionTimedOut):
            message = "Изменение системной политики не завершилось вовремя."
        elif isinstance(error, RestorePointActionFailed):
            message = "Snapper или systemd не смогли применить выбранную политику."
        else:
            message = "Не удалось изменить политику точек восстановления."

        detail = getattr(error, "detail", "")
        LOGGER.error("Restore-point policy action failed: %s; detail=%s", error, detail)
        self.policy_feedback.setText(message)
        QMessageBox.warning(
            self,
            "Настройки не изменены",
            message + "\n\nПроверьте состояние системы и повторите попытку.",
        )

    def _restore_timeline_checkbox(self) -> None:
        state = self._policy_state
        self._ignore_timeline_toggle = True
        self.timeline_check.setChecked(bool(state and state.timeline_enabled))
        self._ignore_timeline_toggle = False

    def _set_busy(self, busy: bool, text: str | None = None) -> None:
        self.refresh_button.setEnabled(not busy)
        self.timeline_check.setEnabled(not busy and bool(self._policy_state and self._policy_state.readable))
        self.apply_policy_button.setEnabled(not busy and bool(self._policy_state and self._policy_state.readable))
        self.policy_progress.setVisible(busy)
        if busy:
            self.policy_progress.setRange(0, 0)
            if text:
                self.policy_feedback.setText(text)
        else:
            self.policy_progress.setRange(0, 100)
            self.policy_progress.setValue(0)
