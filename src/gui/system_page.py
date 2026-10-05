from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QStandardPaths, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.dashboard import DashboardSummary
from src.core.formatting import format_bytes
from src.core.system_diagnostics import DiagnosticCheck
from src.core.system_info import SYSTEM_WARNING_BADGE_THRESHOLD, SystemInfo, collect_system_info
from src.core.system_report import build_system_report
from src.core.system_privileged_diagnostics import collect_privileged_smart

from .details_dialog import DetailsDialog
from .page_base import NavigablePage
from .theme import STATUS_COLORS, StatusBadge, card_frame, muted_text

LOGGER = logging.getLogger(__name__)


class _SystemSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _SystemWorker(QRunnable):
    def __init__(self, *, extended: bool) -> None:
        super().__init__()
        self.extended = extended
        self.signals = _SystemSignals()

    @Slot()
    def run(self) -> None:
        try:
            smart = collect_privileged_smart() if self.extended else None
            self.signals.completed.emit(
                collect_system_info(
                    include_extended=self.extended,
                    extended_smart_check=smart.check if smart else None,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("System diagnostics failed")
            self.signals.failed.emit(str(exc))


class _SystemReportWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _SystemSignals()

    @Slot()
    def run(self) -> None:
        try:
            smart = collect_privileged_smart()
            system = collect_system_info(include_extended=True, extended_smart_check=smart.check)
            report = build_system_report(system, privileged_smart_report=smart.report_text)
            self.signals.completed.emit((system, report))
        except Exception as exc:  # pragma: no cover - defensive OS boundary
            LOGGER.exception("System report generation failed")
            self.signals.failed.emit(str(exc))


class SystemPage(NavigablePage):
    CATEGORY_TITLES = {
        "storage": "Диски и Btrfs",
        "memory": "Память",
        "services": "Службы",
        "boot": "Загрузка и защита",
        "journal": "Системный журнал",
        "integrity": "Целостность системы",
    }

    CATEGORY_ORDER = ("storage", "memory", "services", "boot", "journal", "integrity")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread_pool = QThreadPool.globalInstance()
        self._worker: _SystemWorker | None = None
        self._report_worker: _SystemReportWorker | None = None
        self._last_system: SystemInfo | None = None

        layout = self.create_page_layout("Система", spacing=8)

        self.full_button = QPushButton("Полная диагностика")
        self.full_button.setAutoDefault(False)
        self.full_button.setToolTip("Расширенная диагностика; для SMART/NVMe может потребоваться авторизация Polkit")
        self.full_button.clicked.connect(lambda: self.refresh(True))
        self.add_header_action(self.full_button)

        self.report_button = QPushButton("Сформировать полный отчёт")
        self.report_button.setAutoDefault(False)
        self.report_button.setToolTip("Собрать полный отчёт; SMART/NVMe читается через защищённый Polkit-helper")
        self.report_button.clicked.connect(self.generate_report)
        self.add_header_action(self.report_button)

        self.state_card = card_frame()
        state_row = QHBoxLayout(self.state_card)
        state_row.setContentsMargins(14, 9, 14, 9)
        state_row.setSpacing(10)
        self.state_badge = StatusBadge()
        state_row.addWidget(self.state_badge)
        state_texts = QVBoxLayout()
        state_texts.setSpacing(2)
        self.state = QLabel("Данные появятся после первой проверки.")
        state_font = self.state.font()
        state_font.setBold(True)
        self.state.setFont(state_font)
        self.state_note = QLabel("Основная проверка запускается автоматически; расширенная — по кнопке выше.")
        muted_text(self.state_note)
        state_texts.addWidget(self.state)
        state_texts.addWidget(self.state_note)
        state_row.addLayout(state_texts, 1)
        self.details_button = QPushButton("Технические детали")
        self.details_button.setAutoDefault(False)
        self.details_button.setVisible(False)
        self.details_button.clicked.connect(self._show_diagnostic_details)
        state_row.addWidget(self.details_button)
        layout.addWidget(self.state_card)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        self.cards = QGridLayout(scroll_content)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setHorizontalSpacing(10)
        self.cards.setVerticalSpacing(8)
        self.cards.setColumnStretch(0, 1)
        self.cards.setColumnStretch(1, 1)
        self.scroll.setWidget(scroll_content)
        layout.addWidget(self.scroll, 1)

        self.values: dict[str, QLabel] = {}
        self._category_cards: dict[str, QVBoxLayout] = {}
        self._category_info_buttons: dict[str, QToolButton] = {}
        self._category_checks: dict[str, tuple[DiagnosticCheck, ...]] = {}
        self._build_identity_card()
        for index, category in enumerate(self.CATEGORY_ORDER, start=1):
            self._build_category_card(category, index)

    def _build_identity_card(self) -> None:
        card = card_frame()
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(5)
        self._add_title(grid, "Сведения о системе", 0)
        rows = [
            (("Операционная система", "os"), ("Корневая ФС", "filesystem")),
            (("Ядро Linux", "kernel"), ("Системный диск", "disk")),
            (("KDE Plasma", "plasma"), None),
        ]
        for row, (left, right) in enumerate(rows, start=1):
            self._add_value_row(grid, row, left[0], left[1], column=0)
            if right is not None:
                self._add_value_row(grid, row, right[0], right[1], column=2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.cards.addWidget(card, 0, 0, 1, 2)

    def _build_category_card(self, category: str, index: int) -> None:
        card = card_frame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel(self.CATEGORY_TITLES[category])
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch(1)

        info_button = QToolButton()
        info_button.setText("ⓘ")
        info_button.setAutoRaise(True)
        info_button.setFixedSize(24, 24)
        info_button.setToolTip(f"Подробности: {self.CATEGORY_TITLES[category]}")
        info_button.setVisible(False)
        info_button.clicked.connect(lambda _checked=False, c=category: self._show_category_details(c))
        header.addWidget(info_button)
        layout.addLayout(header)
        layout.addStretch(1)

        row = (index - 1) // 2 + 1
        col = (index - 1) % 2
        self.cards.addWidget(card, row, col)
        self._category_cards[category] = layout
        self._category_info_buttons[category] = info_button

    @staticmethod
    def _add_title(grid: QGridLayout, text: str, row: int) -> None:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        grid.addWidget(label, row, 0, 1, 4)

    def _add_value_row(
        self,
        grid: QGridLayout,
        row: int,
        caption: str,
        key: str,
        *,
        column: int = 0,
    ) -> None:
        caption_label = QLabel(caption)
        muted_text(caption_label)
        value = QLabel("—")
        value.setWordWrap(True)
        self.values[key] = value
        grid.addWidget(caption_label, row, column)
        grid.addWidget(value, row, column + 1)

    def set_summary(self, summary: DashboardSummary) -> None:
        self._apply_system(summary.system, summary.checked_at)

    def refresh(self, extended: bool = False) -> None:
        if self._worker is not None or self._report_worker is not None:
            return
        self.full_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.state.setText("Выполняется полная диагностика…" if extended else "Проверяю систему…")
        self.state_note.setText(
            "Для SMART/NVMe может появиться запрос авторизации Polkit. Все проверки только читают состояние системы."
            if extended
            else "Проверки только читают состояние системы и ничего не изменяют."
        )
        self.state_badge.set_status("unknown", "Выполняется проверка")

        worker = _SystemWorker(extended=extended)
        self._worker = worker
        worker.signals.completed.connect(self._refresh_completed)
        worker.signals.failed.connect(self._refresh_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _refresh_completed(self, result: object) -> None:
        self._worker = None
        self.full_button.setEnabled(True)
        self.report_button.setEnabled(True)
        if not isinstance(result, SystemInfo):
            self._refresh_failed("Получен неожиданный результат проверки")
            return
        self._apply_system(result, datetime.now().astimezone())

    @Slot(str)
    def _refresh_failed(self, error: str) -> None:
        self._worker = None
        self.full_button.setEnabled(True)
        self.report_button.setEnabled(True)
        self.state.setText("Не удалось выполнить диагностику")
        self.state_note.setText(error or "Неизвестная ошибка")
        self.state_badge.set_status("critical", error or "Ошибка диагностики")

    def generate_report(self) -> None:
        if self._worker is not None or self._report_worker is not None:
            return
        self.full_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.state.setText("Формируется полный отчёт…")
        self.state_note.setText("Собираю расширенные read-only сведения; для SMART/NVMe может появиться запрос Polkit.")
        self.state_badge.set_status("unknown", "Формируется диагностический отчёт")

        worker = _SystemReportWorker()
        self._report_worker = worker
        worker.signals.completed.connect(self._report_completed)
        worker.signals.failed.connect(self._report_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _report_completed(self, result: object) -> None:
        self._report_worker = None
        self.full_button.setEnabled(True)
        self.report_button.setEnabled(True)

        if not isinstance(result, tuple) or len(result) != 2:
            self._report_failed("Получен неожиданный результат формирования отчёта")
            return
        system, report = result
        if not isinstance(system, SystemInfo) or not isinstance(report, str):
            self._report_failed("Получен неожиданный результат формирования отчёта")
            return

        generated_at = datetime.now().astimezone()
        self._apply_system(system, generated_at)
        try:
            report_path = self._save_report(report, generated_at)
        except OSError as exc:
            self._report_failed(str(exc))
            return

        QMessageBox.information(
            self,
            "Полный отчёт готов",
            "Отчёт сохранён в папку загрузок:\n\n"
            f"{report_path}\n\n"
            "Файл можно загрузить в ChatGPT для подробного разбора проблемы.",
        )

    @Slot(str)
    def _report_failed(self, error: str) -> None:
        self._report_worker = None
        self.full_button.setEnabled(True)
        self.report_button.setEnabled(True)
        self.state.setText("Не удалось сформировать полный отчёт")
        self.state_note.setText(error or "Неизвестная ошибка")
        self.state_badge.set_status("critical", error or "Ошибка формирования отчёта")

    @staticmethod
    def _save_report(report: str, generated_at: datetime) -> Path:
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        destination_dir = Path(downloads) if downloads else Path.home() / "Downloads"
        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = f"Arch_Manager_System_Report_{generated_at.strftime('%Y%m%d_%H%M%S')}.txt"
        destination = destination_dir / filename
        destination.write_text(report, encoding="utf-8")
        return destination

    def _apply_system(self, system: SystemInfo, checked_at: datetime) -> None:
        self._last_system = system

        critical = system.critical_checks
        warnings = system.warning_checks
        unknown = system.unknown_checks
        infos = system.info_checks
        if critical:
            self.state.setText("Требуется внимание")
            self.state_badge.set_status("critical", "Обнаружены критические результаты диагностики.")
        elif warnings:
            count = len(warnings)
            if count % 10 == 1 and count % 100 != 11:
                warning_word = "предупреждение"
            elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
                warning_word = "предупреждения"
            else:
                warning_word = "предупреждений"
            self.state.setText(f"Система работает нормально. Есть {count} {warning_word}")
            if count < SYSTEM_WARNING_BADGE_THRESHOLD:
                self.state_badge.set_status(
                    "ok",
                    f"Критических проблем нет. Жёлтый общий статус появляется с {SYSTEM_WARNING_BADGE_THRESHOLD} предупреждений.",
                )
            else:
                self.state_badge.set_status(
                    "warning",
                    "Критических проблем нет; накопилось несколько пунктов, которые стоит проверить.",
                )
        elif unknown:
            self.state.setText("Система работает. Часть проверок недоступна")
            self.state_badge.set_status("warning", "Некоторые данные получить не удалось.")
        elif infos:
            self.state.setText("Система в норме. Есть информационные заметки")
            self.state_badge.set_status("ok", "Критических проблем и предупреждений не обнаружено.")
        else:
            self.state.setText("Всё в порядке")
            self.state_badge.set_status("ok", "Критических проблем не обнаружено.")

        total = len(system.diagnostics)
        note_parts = [f"Проверок: {total}", f"критических: {len(critical)}"]
        if warnings:
            note_parts.append(f"предупреждений: {len(warnings)}")
        if unknown:
            note_parts.append(f"недоступно: {len(unknown)}")
        if infos:
            note_parts.append(f"информационных: {len(infos)}")
        if system.extended_diagnostics:
            note_parts.append("полная диагностика")
        self.state_note.setText(" · ".join(note_parts))
        self.details_button.setVisible(bool(system.diagnostics))

        self.values["os"].setText(system.os_name)
        self.values["kernel"].setText(system.kernel)
        self.values["plasma"].setText(system.plasma_version or "не определена")
        self.values["filesystem"].setText(system.disk.filesystem)
        self.values["disk"].setText(
            f"{format_bytes(system.disk.free_bytes)} свободно из {format_bytes(system.disk.total_bytes)} "
            f"({system.disk.percent_used:.0f}% занято)"
        )

        for category in self.CATEGORY_ORDER:
            checks = [check for check in system.diagnostics if check.category == category]
            self._populate_category(category, checks)

        # After a fresh check the page should always reopen at a predictable top
        # position instead of showing the lower half of the identity card.
        self.scroll.verticalScrollBar().setValue(0)

    def _populate_category(self, category: str, checks: list[DiagnosticCheck]) -> None:
        layout = self._category_cards[category]
        self._category_checks[category] = tuple(checks)
        self._category_info_buttons[category].setVisible(any(check.details for check in checks))
        # Remove previous generated rows but keep title (index 0) and stretch.
        while layout.count() > 2:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not checks:
            placeholder = QLabel("Нет данных")
            muted_text(placeholder)
            layout.insertWidget(1, placeholder)
            return
        for check in checks:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            badge = QLabel(self._status_symbol(check.status))
            badge.setFixedWidth(18)
            badge.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            badge.setToolTip(check.status)
            color = STATUS_COLORS.get(check.status, STATUS_COLORS["unknown"])
            badge.setStyleSheet(f"color: {color.name()}; font-weight: 700;")
            text = QLabel(f"<b>{check.title}</b><br>{check.summary}")
            text.setWordWrap(True)
            row_layout.addWidget(badge)
            row_layout.addWidget(text, 1)
            layout.insertWidget(layout.count() - 1, row)

    @staticmethod
    def _status_symbol(status: str) -> str:
        return {
            "ok": "✓",
            "info": "ⓘ",
            "warning": "!",
            "critical": "!",
            "unknown": "?",
        }.get(status, "?")

    def _show_category_details(self, category: str) -> None:
        checks = self._category_checks.get(category, ())
        if not checks:
            return
        lines: list[str] = []
        for check in checks:
            lines.append(f"{self._status_symbol(check.status)} {check.title}: {check.summary}")
            for detail in check.details[:16]:
                lines.append(f"    {detail}")
            lines.append("")

        DetailsDialog(
            self.CATEGORY_TITLES.get(category, "Подробности"),
            "\n".join(lines).strip(),
            heading=self.CATEGORY_TITLES.get(category, "Подробности диагностики"),
            note="Полные технические сведения по выбранному блоку диагностики.",
            parent=self,
        ).exec()

    def _show_diagnostic_details(self) -> None:
        system = self._last_system
        if system is None or not system.diagnostics:
            return
        sections: list[str] = []
        for category in self.CATEGORY_ORDER:
            checks = [check for check in system.diagnostics if check.category == category]
            if not checks:
                continue
            lines = [self.CATEGORY_TITLES[category]]
            for check in checks:
                lines.append(f"{self._status_symbol(check.status)} {check.title}: {check.summary}")
                for detail in check.details[:12]:
                    lines.append(f"    {detail}")
            sections.append("\n".join(lines))

        DetailsDialog(
            "Подробности диагностики",
            "\n\n".join(sections),
            heading="Результаты проверки системы",
            note=(
                "Полная диагностика дополнительно проверяет SMART/NVMe там, где эти данные "
                "доступны без повышения прав. Недоступность таких данных сама по себе не считается неисправностью."
            ),
            parent=self,
        ).exec()
