from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRunnable, QSettings, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.activity_log import append_activity, read_activity_entries
from src.core.dashboard import DashboardSummary
from src.core.formatting import format_bytes
from src.core.preferences import (
    AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT,
    AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
)
from src.core.reboot_status import RebootStatus, assess_reboot_status
from src.core.update_state import UpdateStateService, get_update_state_service
from src.core.updates import (
    PackageInfo,
    UpdateDetails,
    UpdateItem,
    collect_installed_package_info,
)

from .page_base import NavigablePage
from .theme import BusySpinner, StatusBadge, card_frame, muted_text
from .update_session import UpdateSessionProcess, UpdateSessionResult

LOGGER = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class _UpdatesWorker(QRunnable):
    def __init__(self, update_state: UpdateStateService) -> None:
        super().__init__()
        self.update_state = update_state
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(self.update_state.refresh().details)
        except Exception as exc:  # pragma: no cover - OS boundary
            LOGGER.exception("Detailed update check failed")
            self.signals.failed.emit(exc)


class _PackageInfoWorker(QRunnable):
    def __init__(self, item: UpdateItem) -> None:
        super().__init__()
        self.item = item
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            info = collect_installed_package_info(self.item.name)
        except Exception as exc:  # pragma: no cover - OS boundary
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit((self.item, info))


class _SourceFilterHeader(QHeaderView):
    """Table header with an integrated source filter in the Source section."""

    filter_changed = Signal(str)

    def __init__(self, source_section: int, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._source_section = source_section
        self._source_filter = "all"
        self.setSectionsClickable(True)
        self.setHighlightSections(False)

    @property
    def source_filter(self) -> str:
        return self._source_filter

    def _header_text(self) -> str:
        labels = {
            "all": "Источник  ▾",
            "official": "Источник · Официальные  ▾",
            "aur": "Источник · AUR  ▾",
        }
        return labels.get(self._source_filter, labels["all"])

    def update_label(self, table: QTableWidget) -> None:
        item = table.horizontalHeaderItem(self._source_section)
        if item is not None:
            item.setText(self._header_text())
            item.setToolTip("Фильтр по источнику обновлений")

    def _set_source_filter(self, value: str) -> None:
        if value == self._source_filter:
            return
        self._source_filter = value
        table = self.parent()
        if isinstance(table, QTableWidget):
            self.update_label(table)
        self.filter_changed.emit(value)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        section = self.logicalIndexAt(event.position().toPoint())
        if section != self._source_section:
            super().mousePressEvent(event)
            return

        menu = QMenu(self)
        choices = (
            ("Все источники", "all"),
            ("Официальные", "official"),
            ("AUR", "aur"),
        )
        for text, value in choices:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(value == self._source_filter)
            action.triggered.connect(lambda _checked=False, v=value: self._set_source_filter(v))

        x = self.sectionViewportPosition(self._source_section)
        y = self.height()
        menu.exec(self.viewport().mapToGlobal(QPoint(x, y)))


def _source_label(source: str) -> str:
    return "Официальные" if source == "official" else "AUR"


def _status_for_count(total: int | None, partial: bool) -> tuple[str, str, str]:
    if total is None:
        return "Не удалось проверить обновления", "warning", "Один или несколько источников недоступны."
    if partial:
        if total > 0:
            return f"Доступно обновлений: {total}+", "warning", "Не все источники удалось проверить."
        return "Не все источники проверены", "warning", "Повторите проверку позже."
    if total == 0:
        return "Обновлений нет", "ok", "Система актуальна."
    if total < 7:
        return f"Доступно обновлений: {total}", "warning", "Есть доступные обновления."
    return f"Доступно обновлений: {total}", "critical", "Доступно много обновлений."


class UpdatesPage(NavigablePage):
    update_state_changed = Signal(object)
    system_update_finished = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        update_state: UpdateStateService | None = None,
    ):
        super().__init__(parent)
        self.settings = QSettings()
        self.update_state = update_state or get_update_state_service()
        self._thread_pool = QThreadPool.globalInstance()
        self._check_worker: _UpdatesWorker | None = None
        self._package_info_worker: _PackageInfoWorker | None = None
        self._details: UpdateDetails | None = None
        self._loaded_once = False
        self._aur_selection: dict[str, bool] = {}
        self._rebuilding_table = False

        self._session = UpdateSessionProcess(self)
        self._session.completed.connect(self._update_session_finished)
        self._session.failed.connect(self._update_session_failed)
        self._session.running_changed.connect(self._session_running_changed)

        self._session_started_at: datetime | None = None
        self._session_package_names: tuple[str, ...] = ()
        self._session_official_count = 0
        self._session_aur_count = 0
        self._pre_update_point_number: int | None = None

        self._last_report_exists = False
        self._report_result_text = ""
        self._report_time_text = ""
        self._report_summary_text = ""
        self._report_reboot_text = ""
        self._report_technical_text = ""

        layout = self.create_page_layout("Обновления")
        self._build_toolbar(layout)
        self.checked_label = self.make_checked_label()
        layout.addWidget(self.checked_label)
        self._build_status_row(layout)
        self._build_table(layout)
        self._load_last_report()

    def _build_toolbar(self, layout: QVBoxLayout) -> None:
        # PageBase owns the action area on the same line as the title.
        # Installation is the first action, followed by a manual re-check.
        # Both buttons deliberately use the native KDE QPushButton appearance:
        # an enabled install action must never look like a disabled control.
        del layout

        self.install_button = QPushButton("Установить обновления")
        self.install_button.setIcon(QIcon.fromTheme("system-software-update-symbolic"))
        self.install_button.setEnabled(False)
        self.install_button.setMinimumWidth(220)
        self.install_button.setMinimumHeight(36)
        self.install_button.clicked.connect(self._start_install)
        self.add_header_action(self.install_button)

        self.refresh_button = QPushButton("Проверить обновления")
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh-symbolic"))
        self.refresh_button.setMinimumWidth(185)
        self.refresh_button.setMinimumHeight(36)
        self.refresh_button.clicked.connect(self.refresh)
        self.add_header_action(self.refresh_button)

    def _build_status_row(self, layout: QVBoxLayout) -> None:
        # A compact full-width banner anchors the state directly below the page
        # title. The icon is no longer floating in empty space.
        self.status_card = card_frame()
        row = QHBoxLayout(self.status_card)
        row.setContentsMargins(12, 8, 10, 8)
        row.setSpacing(10)

        self.status_stack = QStackedWidget()
        self.status_stack.setFixedSize(30, 30)
        self.status_badge = StatusBadge()
        self.status_spinner = BusySpinner()
        self.status_stack.addWidget(self.status_badge)
        self.status_stack.addWidget(self.status_spinner)
        self.status_stack.setCurrentWidget(self.status_badge)
        row.addWidget(self.status_stack)

        self.status_label = QLabel("Ещё не проверено")
        status_font = self.status_label.font()
        status_font.setPointSize(status_font.pointSize() + 1)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label)

        self.status_note = QLabel("")
        self.status_note.setWordWrap(True)
        self.status_note.setVisible(False)
        muted_text(self.status_note)
        row.addWidget(self.status_note, 1)
        row.addStretch(1)

        self.history_button = QToolButton()
        self.history_button.setText("ⓘ")
        self.history_button.setToolTip("Подробности последнего сеанса обновления")
        self.history_button.setAutoRaise(True)
        self.history_button.setEnabled(False)
        self.history_button.clicked.connect(self._show_last_report)
        row.addWidget(self.history_button)

        layout.addWidget(self.status_card)

    def _set_status_note(self, text: str) -> None:
        self.status_note.setText(text)
        self.status_note.setVisible(bool(text.strip()))

    @staticmethod
    def _download_summary(details: UpdateDetails) -> str:
        official_items = details.official.items
        known_bytes = sum(item.download_size or 0 for item in official_items)
        known_count = sum(item.download_size is not None for item in official_items)
        official_unknown = len(official_items) - known_count
        aur_count = len(details.aur.items)

        parts: list[str] = []
        if official_items:
            if known_count:
                label = f"официальные: {format_bytes(known_bytes)}"
                if official_unknown:
                    label += f" + {official_unknown} без оценки"
                parts.append(label)
            else:
                parts.append("официальные: объём уточняется")
        if aur_count:
            parts.append("AUR: размер определяется при сборке")
        return "; ".join(parts)

    def _build_table(self, layout: QVBoxLayout) -> None:
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Выбор", "Пакет", "Установлено", "Доступно", "Скачать", "Источник  ▾", ""]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.itemChanged.connect(self._selection_changed)

        self.source_header = _SourceFilterHeader(5, self.table)
        self.table.setHorizontalHeader(self.source_header)
        self.source_header.filter_changed.connect(lambda _value: self._rebuild_table())
        self.source_header.update_label(self.table)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

    def ensure_loaded(self) -> None:
        if not self._loaded_once and not self._busy():
            self.refresh()

    def _busy(self) -> bool:
        return self._check_worker is not None or self._session.running

    @Slot()
    def refresh(self) -> None:
        if self._busy():
            return
        self._set_checking(True)
        worker = _UpdatesWorker(self.update_state)
        self._check_worker = worker
        worker.signals.completed.connect(self._updates_loaded)
        worker.signals.failed.connect(self._updates_failed)
        self._thread_pool.start(worker)

    def refresh_if_idle(self) -> None:
        """Refresh when another Arch Manager page changed package state."""

        if not self._busy():
            self.refresh()

    def apply_shared_details(self, details: UpdateDetails) -> None:
        """Accept a state already checked by another surface without rechecking."""

        if self._busy():
            return
        self._details = details
        self._loaded_once = True
        known_aur = {item.name for item in details.aur.items}
        self._aur_selection = {
            name: self._aur_selection.get(name, True)
            for name in known_aur
        }
        self._apply_details(details)

    def _set_checking(self, checking: bool) -> None:
        self.refresh_button.setEnabled(not checking and not self._session.running)
        self.source_header.setEnabled(not checking and not self._session.running)
        self.table.setEnabled(not checking and not self._session.running)
        if checking:
            self.status_label.setText("Проверяю обновления…")
            self._set_status_note("")
            self.status_spinner.start()
            self.status_stack.setCurrentWidget(self.status_spinner)
            self.install_button.setEnabled(False)
        else:
            self.status_spinner.stop()
            self.status_stack.setCurrentWidget(self.status_badge)
            self._update_install_button()

    @Slot(object)
    def _updates_loaded(self, details: UpdateDetails) -> None:
        self._check_worker = None
        self._details = details
        self._loaded_once = True

        known_aur = {item.name for item in details.aur.items}
        self._aur_selection = {
            name: self._aur_selection.get(name, True)
            for name in known_aur
        }

        self._set_checking(False)
        self._apply_details(details)
        self.update_state_changed.emit(details)

    @Slot(object)
    def _updates_failed(self, error: object) -> None:
        self._check_worker = None
        self._set_checking(False)
        LOGGER.error("Update page check failed: %s", error)
        self.status_badge.set_status("critical", str(error))
        self.status_label.setText("Не удалось проверить обновления")
        self._set_status_note("Повторите попытку.")

    def _apply_details(self, details: UpdateDetails) -> None:
        label, status, note = _status_for_count(details.total, details.partial)
        download_summary = self._download_summary(details)
        if details.total and download_summary:
            label = f"{label} ({download_summary})"
        self.status_badge.set_status(status, note)
        self.status_label.setText(label)
        self._set_status_note(note if details.partial else "")
        self.set_checked_at(self.checked_label, details.checked_at)
        self._rebuild_table()
        self._update_install_button()

    @Slot()
    def _rebuild_table(self) -> None:
        details = self._details
        self._rebuilding_table = True
        try:
            if details is None:
                self.table.setRowCount(0)
                return

            source_filter = self.source_header.source_filter
            items = [
                item
                for item in details.all_items
                if source_filter == "all" or item.source == source_filter
            ]
            items.sort(key=lambda item: (item.source != "official", item.name.casefold()))

            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                choice = QTableWidgetItem()
                if item.source == "aur":
                    choice.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    choice.setCheckState(
                        Qt.CheckState.Checked
                        if self._aur_selection.get(item.name, True)
                        else Qt.CheckState.Unchecked
                    )
                    choice.setData(Qt.ItemDataRole.UserRole, item.name)
                    choice.setToolTip("AUR-пакеты можно выбирать по одному")
                else:
                    choice.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    choice.setCheckState(Qt.CheckState.Checked)
                    choice.setToolTip("Официальные пакеты устанавливаются полным обновлением системы")
                choice.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 0, choice)

                download_text = (
                    format_bytes(item.download_size)
                    if item.download_size is not None
                    else ("при сборке" if item.source == "aur" else "—")
                )
                values = (
                    item.name,
                    item.current_version,
                    item.new_version,
                    download_text,
                    _source_label(item.source),
                )
                for column, value in enumerate(values, start=1):
                    cell = QTableWidgetItem(value)
                    if column == 4:
                        cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(row, column, cell)

                info_button = QToolButton()
                info_button.setText("ⓘ")
                info_button.setToolTip(f"Описание пакета {item.name}")
                info_button.setAutoRaise(True)
                info_button.clicked.connect(
                    lambda checked=False, package=item: self._request_package_info(package)
                )
                self.table.setCellWidget(row, 6, info_button)
            # Не ограничиваем таблицу искусственно десятью строками.
            # Она занимает всё доступное место страницы, а встроенная прокрутка
            # появляется только когда список действительно не помещается в окно.
            self.table.setMaximumHeight(16777215)
        finally:
            self._rebuilding_table = False

    @Slot(QTableWidgetItem)
    def _selection_changed(self, item: QTableWidgetItem) -> None:
        if self._rebuilding_table or item.column() != 0:
            return
        package_name = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(package_name, str) or not package_name:
            return
        self._aur_selection[package_name] = item.checkState() == Qt.CheckState.Checked
        self._update_install_button()

    def _selected_aur_packages(self) -> tuple[str, ...]:
        details = self._details
        if details is None:
            return ()
        return tuple(
            item.name
            for item in details.aur.items
            if self._aur_selection.get(item.name, True)
        )

    def _update_install_button(self) -> None:
        details = self._details
        if details is None or self._busy():
            self.install_button.setEnabled(False)
            return
        if details.official.count is None:
            # Never start an AUR-only transaction when the official Arch state
            # could not be checked; that would risk a partial/unsupported update.
            self.install_button.setEnabled(False)
            self.install_button.setText("Установить обновления")
            return
        official_count = details.official.count
        selected_aur = self._selected_aur_packages()
        enabled = official_count > 0 or bool(selected_aur)
        self.install_button.setEnabled(enabled)
        if details.aur.items:
            self.install_button.setText(
                f"Установить обновления ({official_count + len(selected_aur)})"
                if enabled
                else "Установить обновления"
            )
        else:
            self.install_button.setText(
                f"Установить обновления ({official_count})"
                if official_count
                else "Установить обновления"
            )

    @Slot()
    def _start_install(self) -> None:
        details = self._details
        if details is None or self._busy():
            return

        if details.official.count is None:
            QMessageBox.warning(
                self,
                "Обновление не начато",
                "Сначала нужно успешно проверить официальные обновления Arch.",
            )
            return
        official_count = details.official.count
        aur_packages = self._selected_aur_packages()
        if official_count == 0 and not aur_packages:
            return

        protected = self.settings.value(
            AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY,
            AUTO_RESTORE_POINT_BEFORE_UPDATE_DEFAULT,
            type=bool,
        )
        protection_text = (
            "Перед обновлением будет создана важная точка восстановления."
            if protected
            else "Автоматическая точка восстановления отключена в Настройках."
        )
        aur_text = (
            f"Выбрано AUR-пакетов: {len(aur_packages)} из {len(details.aur.items)}."
            if details.aur.items
            else "AUR-пакетов для обновления нет."
        )

        answer = QMessageBox.question(
            self,
            "Установить обновления?",
            f"Официальных обновлений: {official_count}.\n"
            f"{aur_text}\n\n"
            f"{protection_text}\n\n"
            "После подтверждения откроется отдельное окно терминала. "
            "Пароль администратора вводится там один раз; вопросы pacman/yay "
            "([Y/n], [y/N] и т. п.) также задаются и подтверждаются прямо в терминале.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._session_started_at = datetime.now().astimezone()
        self._session_official_count = official_count
        self._session_aur_count = len(aur_packages)
        official_names = tuple(item.name for item in details.official.items)
        self._session_package_names = official_names + aur_packages
        self._pre_update_point_number = None
        self._set_controls_enabled(False)

        launcher = Path(__file__).resolve().parents[2] / "scripts" / "run-system-update-terminal.sh"
        try:
            self._session.start(
                launcher,
                # Always run a full official sync before any selected AUR update.
                # This avoids unsupported partial-upgrade states on Arch Linux.
                official_updates=True,
                aur_packages=aur_packages,
                create_restore_point=protected,
            )
        except Exception as exc:
            LOGGER.exception("Update terminal could not start")
            self._set_controls_enabled(True)
            QMessageBox.warning(self, "Обновление не запущено", str(exc))

    @Slot(bool)
    def _session_running_changed(self, running: bool) -> None:
        if running:
            self.status_spinner.start()
            self.status_stack.setCurrentWidget(self.status_spinner)
            self.status_label.setText("Обновление выполняется…")
            self._set_status_note("Работа идёт в отдельном терминале. Отвечайте на запросы там и не закрывайте окно до завершения.")
        else:
            self.status_spinner.stop()
            self.status_stack.setCurrentWidget(self.status_badge)

    @Slot(object)
    def _update_session_finished(self, result: object) -> None:
        if not isinstance(result, UpdateSessionResult):
            self._update_session_failed("Runner вернул неожиданный результат.")
            return

        self._pre_update_point_number = result.restore_point
        self._set_controls_enabled(True)
        reboot = assess_reboot_status()
        self._store_update_report(result.state, result.detail, reboot)
        self._record_update_session(result.state, result.detail, reboot)

        if result.state == "success":
            self.update_state.invalidate()
            self.system_update_finished.emit()
            self.status_badge.set_status("ok", "Обновление завершено успешно.")
            self.status_label.setText("Обновление завершено")
            self._set_status_note("")
            self.refresh()
            return

        if result.state == "aur-skipped":
            self.update_state.invalidate()
            self.system_update_finished.emit()
            self.status_badge.set_status("warning", "Официальный этап завершён, AUR пропущен.")
            self.status_label.setText("AUR-пакеты пропущены")
            self._set_status_note("Официальные обновления установлены.")
            self.refresh()
            return

        messages = {
            "cancelled": "Авторизация отменена. Обновление не запускалось.",
            "restore-point-failed": "Не удалось создать защитную точку. Обновление не запускалось.",
            "official-failed": "Официальное обновление завершилось ошибкой. AUR не запускался.",
            "aur-failed": "Официальный этап завершён, но выбранные AUR-пакеты обновить не удалось.",
            "interrupted": "Сеанс обновления был прерван.",
            "failed": "Сеанс обновления завершился с ошибкой.",
        }
        message = messages.get(result.state, "Сеанс обновления завершился с ошибкой.")
        self.status_badge.set_status("critical", message)
        self.status_label.setText("Обновление завершено не полностью")
        self._set_status_note(message)
        QMessageBox.warning(self, "Обновление завершено не полностью", message)

        # A failed/interrupted package-manager stage can still have installed some
        # packages before the error. Re-read every dependent surface instead of
        # leaving stale counts until the next manual check.
        if result.state in {"official-failed", "aur-failed", "interrupted", "failed"}:
            self.update_state.invalidate()
            self.system_update_finished.emit()
            self.refresh()

    @Slot(str)
    def _update_session_failed(self, error: str) -> None:
        self._set_controls_enabled(True)
        LOGGER.error("Update session failed: %s", error)
        reboot = RebootStatus(
            "unknown",
            "Проверка перезагрузки не выполнялась: сеанс завершился аварийно.",
            str(error),
        )
        self._store_update_report("launch-failed", str(error), reboot)
        self._record_update_session("launch-failed", str(error), reboot)
        self.status_badge.set_status("critical", str(error))
        self.status_label.setText("Обновление не выполнено")
        self._set_status_note(str(error))
        QMessageBox.warning(self, "Обновление не выполнено", str(error))

    def _session_result_text(self, state: str) -> str:
        return {
            "success": "✓ Обновление завершено успешно",
            "aur-skipped": "Официальные пакеты обновлены, AUR пропущен",
            "restore-point-failed": "Защитная точка не создана",
            "official-failed": "Официальное обновление завершилось ошибкой",
            "aur-failed": "Выбранные AUR-пакеты завершились ошибкой",
            "cancelled": "Обновление отменено",
            "interrupted": "Обновление прервано",
            "launch-failed": "Сеанс обновления не запущен",
        }.get(state, "Сеанс обновления завершён с ошибкой")

    def _store_update_report(
        self,
        state: str,
        detail: str,
        reboot: RebootStatus,
        *,
        timestamp: datetime | None = None,
        package_names: tuple[str, ...] | None = None,
        official_count: int | None = None,
        aur_count: int | None = None,
        restore_point: int | None = None,
    ) -> None:
        finished = timestamp or datetime.now().astimezone()
        names = package_names if package_names is not None else self._session_package_names
        official = self._session_official_count if official_count is None else official_count
        aur = self._session_aur_count if aur_count is None else aur_count
        point = self._pre_update_point_number if restore_point is None else restore_point

        self._report_result_text = self._session_result_text(state)
        self._report_time_text = finished.strftime("%d.%m.%Y, %H:%M")
        parts = [f"Официальных пакетов: {official}. Выбрано AUR-пакетов: {aur}."]
        if point is not None:
            parts.append(f"Защитная точка: Snapper ID {point}.")
        self._report_summary_text = " ".join(parts)
        self._report_reboot_text = reboot.summary

        technical = [
            f"Состояние runner: {state}",
            f"Деталь: {detail or 'нет'}",
            f"Проверка перезагрузки: {reboot.state}",
            reboot.detail,
        ]
        if self._session_started_at is not None:
            technical.insert(0, f"Начало: {self._session_started_at.strftime('%d.%m.%Y, %H:%M:%S')}")
        if names:
            package_text = ", ".join(names[:80])
            if len(names) > 80:
                package_text += f" … (+{len(names) - 80})"
            technical.append(f"Пакеты по плану: {package_text}")
        self._report_technical_text = "\n".join(technical)
        self._last_report_exists = True
        self.history_button.setEnabled(True)

    def _record_update_session(self, state: str, detail: str, reboot: RebootStatus) -> None:
        append_activity(
            "updates",
            "update-session",
            state,
            detail,
            data={
                "official_count": self._session_official_count,
                "aur_count": self._session_aur_count,
                "packages": list(self._session_package_names),
                "restore_point": self._pre_update_point_number,
                "reboot_state": reboot.state,
                "reboot_summary": reboot.summary,
                "reboot_detail": reboot.detail,
            },
        )

    def _load_last_report(self) -> None:
        entries = read_activity_entries(limit=30, category="updates")
        entry = next((item for item in entries if item.action == "update-session"), None)
        if entry is None:
            return
        data = entry.data
        reboot = RebootStatus(
            str(data.get("reboot_state", "unknown")),
            str(data.get("reboot_summary", "Сведения о перезагрузке недоступны.")),
            str(data.get("reboot_detail", "Нет дополнительных сведений.")),
        )
        packages = data.get("packages", [])
        if not isinstance(packages, list):
            packages = []
        restore_value = data.get("restore_point")
        try:
            restore_point = int(restore_value) if restore_value is not None else None
        except (TypeError, ValueError):
            restore_point = None
        self._store_update_report(
            entry.result,
            entry.detail,
            reboot,
            timestamp=entry.timestamp,
            package_names=tuple(str(item) for item in packages),
            official_count=int(data.get("official_count", 0) or 0),
            aur_count=int(data.get("aur_count", 0) or 0),
            restore_point=restore_point,
        )

    @Slot()
    def _show_last_report(self) -> None:
        if not self._last_report_exists:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Последнее обновление")
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)

        result = QLabel(self._report_result_text)
        result_font = result.font()
        result_font.setPointSize(result_font.pointSize() + 2)
        result_font.setBold(True)
        result.setFont(result_font)
        result.setWordWrap(True)
        layout.addWidget(result)

        when = QLabel(self._report_time_text)
        muted_text(when)
        layout.addWidget(when)

        summary = QLabel(self._report_summary_text)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        reboot = QLabel(self._report_reboot_text)
        reboot.setWordWrap(True)
        layout.addWidget(reboot)

        technical_title = QLabel("Технические детали")
        technical_font = technical_title.font()
        technical_font.setBold(True)
        technical_title.setFont(technical_font)
        layout.addWidget(technical_title)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._report_technical_text or "Технические сведения отсутствуют.")
        layout.addWidget(text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _request_package_info(self, item: UpdateItem) -> None:
        if self._package_info_worker is not None:
            return
        worker = _PackageInfoWorker(item)
        self._package_info_worker = worker
        worker.signals.completed.connect(self._package_info_loaded)
        worker.signals.failed.connect(self._package_info_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _package_info_loaded(self, payload: object) -> None:
        self._package_info_worker = None
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        item, info = payload
        if not isinstance(item, UpdateItem) or not isinstance(info, PackageInfo):
            return
        source = _source_label(item.source)
        QMessageBox.information(
            self,
            f"Пакет: {info.name}",
            f"{info.description}\n\n"
            f"Источник обновления: {source}\n"
            f"Установлено: {item.current_version}\n"
            f"Доступно: {item.new_version}\n"
            f"Размер загрузки: {format_bytes(item.download_size) if item.download_size is not None else ('определяется при сборке' if item.source == 'aur' else '—')}\n"
            f"Размер установленного пакета: {info.installed_size}\n"
            f"Лицензия: {info.licenses}\n"
            f"Сайт: {info.url}",
        )

    @Slot(object)
    def _package_info_failed(self, error: object) -> None:
        self._package_info_worker = None
        QMessageBox.warning(
            self,
            "Описание пакета",
            f"Не удалось получить локальное описание пакета.\n\n{error}",
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled and self._check_worker is None)
        self.source_header.setEnabled(enabled)
        self.table.setEnabled(enabled)
        if enabled:
            self._update_install_button()
        else:
            self.install_button.setEnabled(False)

    def set_summary(self, summary: DashboardSummary) -> None:
        if self._loaded_once:
            return
        updates = summary.updates
        label, status, note = _status_for_count(updates.total, updates.partial)
        self.status_badge.set_status(status, note)
        self.status_label.setText(label)
        self._set_status_note(note if updates.partial else "")
        self.set_checked_at(self.checked_label, summary.checked_at)
