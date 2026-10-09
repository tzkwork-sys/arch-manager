from __future__ import annotations

from datetime import datetime
import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.dashboard import DashboardSummary
from src.core.maintenance import MaintenanceSummary, collect_maintenance
from src.core.restore_points import RestorePointsSummary, collect_restore_points
from src.core.formatting import format_bytes
from src.core.system_info import GIB, SYSTEM_WARNING_BADGE_THRESHOLD, SystemInfo, collect_system_info
from src.core.update_state import (
    UpdateStateService,
    UpdateStateSnapshot,
    get_update_state_service,
)
from src.core.updates import UpdatesSummary

from .page_base import NavigablePage
from .theme import CARD_MARGINS, CARD_RADIUS, BusySpinner, STATUS_COLORS, StatusBadge, emphasize_primary_button, muted_text

LOGGER = logging.getLogger(__name__)


class SummaryCard(QPushButton):
    """Large clickable Overview card with semantic status outline."""

    activated = Signal()

    def __init__(
        self,
        title: str,
        value: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("overviewSummaryButton")
        self.setAutoDefault(False)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(142)
        self.clicked.connect(self.activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*CARD_MARGINS)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.title_label = QLabel(title)
        title_font = self.title_label.font()
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.status_stack = QStackedWidget()
        self.status_stack.setFixedSize(30, 30)
        self.status_badge = StatusBadge()
        self.spinner = BusySpinner()
        self.status_stack.addWidget(self.status_badge)
        self.status_stack.addWidget(self.spinner)
        self.status_stack.setCurrentWidget(self.status_badge)
        header.addWidget(self.status_stack)

        self.value_label = QLabel(value)
        value_font = self.value_label.font()
        value_font.setPointSize(value_font.pointSize() + 1)
        value_font.setBold(True)
        self.value_label.setFont(value_font)
        self.value_label.setWordWrap(True)

        self.open_label = QLabel("Открыть раздел  →")
        muted_text(self.open_label)

        layout.addLayout(header)
        layout.addWidget(self.value_label)
        layout.addStretch(1)
        layout.addWidget(self.open_label, 0, Qt.AlignmentFlag.AlignLeft)

        # Child labels are presentation-only: the whole card remains one button.
        for widget in (
            self.title_label,
            self.value_label,
            self.open_label,
            self.status_stack,
            self.status_badge,
            self.spinner,
        ):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._apply_status_style("unknown")

    def _apply_status_style(self, status: str) -> None:
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
        self.setStyleSheet(
            "QPushButton#overviewSummaryButton {"
            "text-align: left;"
            "background-color: palette(base);"
            f"border: 2px solid {color.name()};"
            f"border-radius: {CARD_RADIUS}px;"
            "padding: 0px;"
            "min-height: 142px;"
            "}"
            "QPushButton#overviewSummaryButton:hover {"
            "background-color: palette(alternate-base);"
            f"border: 2px solid {color.name()};"
            "}"
            "QPushButton#overviewSummaryButton:pressed {"
            "background-color: palette(midlight);"
            "}"
            "QPushButton#overviewSummaryButton:focus {"
            "background-color: palette(alternate-base);"
            "border: 2px dashed palette(highlight);"
            "}"
        )

    def set_content(self, value: str, status: str, tooltip: str = "") -> None:
        self.value_label.setText(value)
        self.status_badge.set_status(status, tooltip)
        self.setToolTip(tooltip)
        self._apply_status_style(status)

    def set_busy(self, busy: bool) -> None:
        self.setEnabled(not busy)
        if busy:
            self.value_label.setText("Проверяю…")
            self._apply_status_style("unknown")
            self.spinner.start()
            self.status_stack.setCurrentWidget(self.spinner)
        else:
            self.spinner.stop()
            self.status_stack.setCurrentWidget(self.status_badge)


class _WorkerSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class _DashboardWorker(QRunnable):
    def __init__(self, key: str, collector: Callable[[], object]) -> None:
        super().__init__()
        self.key = key
        self.collector = collector
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(self.key, self.collector())
        except Exception as exc:  # pragma: no cover - defensive boundary around OS inspection
            LOGGER.exception("Dashboard check failed: %s", self.key)
            self.signals.failed.emit(self.key, str(exc))


def _update_state(updates: UpdatesSummary) -> tuple[str, str, str]:
    total = updates.total
    if total is None or updates.partial:
        if total:
            return f"Доступно обновлений: {total}", "warning", "Не все источники удалось проверить."
        return "Не удалось проверить все источники", "warning", "Проверка обновлений неполная."
    if total == 0:
        return "Обновлений нет", "ok", "Система актуальна."
    return f"Доступно обновлений: {total}", "warning", "Есть доступные обновления."


def _restore_state(points: RestorePointsSummary) -> tuple[str, str, str]:
    count = points.count
    if count is None:
        return "Не удалось прочитать точки", "critical", "Список точек восстановления недоступен."
    if count == 0:
        return "Точек восстановления нет", "critical", "Рекомендуется создать точку восстановления."
    if count == 1:
        return "1 точка восстановления", "warning", "Есть только одна точка восстановления."
    return f"Точек восстановления: {count}", "ok", "Точки восстановления доступны."


MAINTENANCE_WARNING_BYTES = 512 * 1024 * 1024


def _maintenance_state(summary: MaintenanceSummary) -> tuple[str, str, str]:
    reclaimable = summary.known_reclaimable_bytes
    unknown = any(
        value is None
        for value in (
            summary.reclaimable_cache_bytes,
            summary.orphan_packages,
            summary.config_attention_files,
            summary.trash_bytes,
            summary.thumbnail_cache_bytes,
        )
    )
    review_count = (summary.orphan_packages or 0) + (summary.config_attention_files or 0)
    if reclaimable is None or unknown:
        return "Проверка выполнена не полностью", "warning", "Часть категорий обслуживания недоступна."

    if reclaimable <= 0 and review_count == 0:
        if (summary.journal_usage_bytes or 0) > 0:
            return (
                "Есть что очистить (старые журналы)",
                "ok",
                "Размер удаляемой части журнала заранее точно не определяется.",
            )
        return "Очистка не требуется", "ok", "Обслуживание системы не требуется."

    amount = format_bytes(reclaimable) if reclaimable > 0 else "без оценки объёма"
    label = f"Есть что очистить ({amount})"

    # Small routine cleanup is informational, not a warning. A yellow marker
    # appears only from 512 MiB of known reclaimable data or when packages/configs
    # require manual review.
    if reclaimable < MAINTENANCE_WARNING_BYTES and review_count == 0:
        return label, "ok", "Небольшой объём обычной очистки; срочных действий не требуется."
    return label, "warning", "Накопился заметный объём данных или есть пункты для ручной проверки."


def _system_state(system: SystemInfo) -> tuple[str, str, str]:
    if system.diagnostics:
        critical = system.critical_checks
        warnings = system.warning_checks
        unknown = system.unknown_checks
        if critical:
            names = ", ".join(check.title for check in critical[:3])
            suffix = "" if len(critical) <= 3 else f" и ещё {len(critical) - 3}"
            return "Требуется внимание", "critical", f"Критические проверки: {names}{suffix}."
        if warnings:
            names = ", ".join(check.title for check in warnings[:3])
            suffix = "" if len(warnings) <= 3 else f" и ещё {len(warnings) - 3}"
            if len(warnings) < SYSTEM_WARNING_BADGE_THRESHOLD:
                return (
                    f"Критических проблем нет · замечаний: {len(warnings)}",
                    "ok",
                    f"Некритические замечания: {names}{suffix}. Жёлтый статус появляется с {SYSTEM_WARNING_BADGE_THRESHOLD} предупреждений.",
                )
            return "Есть предупреждения", "warning", f"Стоит проверить: {names}{suffix}."
        if unknown:
            return (
                "Проверка выполнена не полностью",
                "warning",
                f"Не удалось получить данные для {len(unknown)} проверок.",
            )
        info_count = len(system.info_checks)
        tooltip = f"Успешно проверено параметров: {len(system.diagnostics)}."
        if info_count:
            tooltip += f" Информационных заметок: {info_count}."
        return "Всё в порядке", "ok", tooltip

    # Compatibility path for summaries created without the richer diagnostics.
    critical_system = system.critical_failed_system_services
    failed_user = system.failed_user_services
    if (
        (critical_system is not None and critical_system > 0)
        or (failed_user is not None and failed_user > 0)
    ):
        return "Требуется внимание", "critical", "Обнаружены сбойные службы."
    if system.disk.free_bytes < 5 * GIB or system.disk.percent_used >= 95.0:
        return "Мало места на диске", "critical", "На системном диске критически мало свободного места."
    if system.disk.free_bytes < 10 * GIB or system.disk.percent_used >= 90.0:
        return "Мало места на диске", "warning", "Свободное место на системном диске заканчивается."
    if system.has_service_advisories:
        count = system.advisory_system_services
        if count < SYSTEM_WARNING_BADGE_THRESHOLD:
            return (
                f"Критических проблем нет · замечаний: {count}",
                "ok",
                "Есть некритические служебные замечания; критических проблем не обнаружено.",
            )
        return f"TPM/NvPCR: {count}", "warning", "Вспомогательные TPM/NvPCR-службы требуют внимания."
    if system.failed_system_services is None or system.failed_user_services is None:
        return "Не всё удалось проверить", "warning", "Состояние части служб не удалось определить."
    return "Всё в порядке", "ok", "Система работает без обнаруженных проблем."


class DashboardPage(NavigablePage):
    navigate_requested = Signal(int)
    data_updated = Signal(object)
    update_state_changed = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        update_state: UpdateStateService | None = None,
    ) -> None:
        super().__init__(parent)
        self.update_state = update_state or get_update_state_service()
        self._thread_pool = QThreadPool.globalInstance()
        self._workers: dict[str, _DashboardWorker] = {}
        self._results: dict[str, object] = {}
        self._refresh_started_at: datetime | None = None

        root = self.create_page_layout("Обзор")

        self.check_button = QPushButton("Проверить сейчас")
        self.check_button.setAutoDefault(False)
        self.check_button.clicked.connect(self.refresh)
        self.add_header_action(self.check_button)
        emphasize_primary_button(self.check_button)

        self.checked_label = self.make_checked_label()
        root.addWidget(self.checked_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        self.updates_card = SummaryCard("Обновления", "Ожидание проверки")
        self.restore_card = SummaryCard("Восстановление", "Ожидание проверки")
        self.maintenance_card = SummaryCard("Обслуживание", "Ожидание проверки")
        self.system_card = SummaryCard("Система", "Ожидание проверки")

        self._cards = {
            "updates": self.updates_card,
            "restore": self.restore_card,
            "maintenance": self.maintenance_card,
            "system": self.system_card,
        }
        cards = [
            (self.updates_card, 1),
            (self.restore_card, 2),
            (self.maintenance_card, 3),
            (self.system_card, 4),
        ]
        for index, (card, nav_index) in enumerate(cards):
            card.activated.connect(
                lambda checked=False, i=nav_index: self.navigate_requested.emit(i)
            )
            grid.addWidget(card, index // 2, index % 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)
        root.addStretch(1)

        self._refresh_pending = False
        QTimer.singleShot(250, self.refresh)

    @Slot()
    def refresh(self) -> None:
        if self._workers:
            # Package-changing actions may finish while a manual Overview check is
            # still running. Remember the request so the post-action state is not
            # lost and every dependent page is refreshed afterwards.
            self._refresh_pending = True
            return

        self._refresh_pending = False
        self._results.clear()
        self._refresh_started_at = datetime.now().astimezone()
        self.check_button.setEnabled(False)
        self.check_button.setText("Проверяю…")

        collectors: tuple[tuple[str, Callable[[], object]], ...] = (
            ("updates", self.update_state.refresh),
            ("restore", collect_restore_points),
            ("maintenance", collect_maintenance),
            ("system", collect_system_info),
        )
        for key, collector in collectors:
            self._cards[key].set_busy(True)
            worker = _DashboardWorker(key, collector)
            self._workers[key] = worker
            worker.signals.completed.connect(self._section_completed)
            worker.signals.failed.connect(self._section_failed)
            self._thread_pool.start(worker)

    def apply_updates_summary(self, summary: UpdatesSummary) -> None:
        """Synchronize the Overview counter with the shared update state."""

        self._results["updates"] = summary
        self.updates_card.set_content(*_update_state(summary))

    @Slot(str, object)
    def _section_completed(self, key: str, result: object) -> None:
        self._workers.pop(key, None)
        self._cards[key].set_busy(False)

        if key == "updates" and isinstance(result, UpdateStateSnapshot):
            summary = result.summary
            self._results[key] = summary
            self.updates_card.set_content(*_update_state(summary))
            self.update_state_changed.emit(result.details)
        elif key == "restore" and isinstance(result, RestorePointsSummary):
            self._results[key] = result
            self.restore_card.set_content(*_restore_state(result))
        elif key == "maintenance" and isinstance(result, MaintenanceSummary):
            self._results[key] = result
            self.maintenance_card.set_content(*_maintenance_state(result))
        elif key == "system" and isinstance(result, SystemInfo):
            self._results[key] = result
            self.system_card.set_content(*_system_state(result))
        else:
            self._cards[key].set_content("Не удалось проверить", "critical", "Получен неожиданный результат проверки.")

        self._finish_refresh_if_ready()

    @Slot(str, str)
    def _section_failed(self, key: str, error: str) -> None:
        self._workers.pop(key, None)
        self._cards[key].set_busy(False)
        self._cards[key].set_content("Не удалось проверить", "critical", error or "Ошибка проверки")
        self._finish_refresh_if_ready()

    def _finish_refresh_if_ready(self) -> None:
        if self._workers:
            return

        self.check_button.setEnabled(True)
        self.check_button.setText("Проверить сейчас")
        checked_at = datetime.now().astimezone()
        self.set_checked_at(self.checked_label, checked_at)

        required = {"updates", "restore", "maintenance", "system"}
        if required.issubset(self._results):
            summary = DashboardSummary(
                checked_at=checked_at,
                system=self._results["system"],
                updates=self._results["updates"],
                restore_points=self._results["restore"],
                maintenance=self._results["maintenance"],
            )
            self.data_updated.emit(summary)

        if self._refresh_pending:
            self._refresh_pending = False
            QTimer.singleShot(0, self.refresh)
