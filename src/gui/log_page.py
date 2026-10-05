from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.activity_log import ActivityEntry, read_activity_entries
from src.core.formatting import format_datetime

from .page_base import NavigablePage
from .theme import card_frame, muted_text


_RESULT_LABELS = {
    "success": "Успешно",
    "partial": "Частично",
    "aur-skipped": "AUR пропущен",
    "restore-point-failed": "Защитная точка не создана",
    "official-failed": "Ошибка официального обновления",
    "aur-failed": "Ошибка обновления AUR",
    "cancelled": "Отменено",
    "interrupted": "Прервано",
    "launch-failed": "Не запущено",
    "failed": "Ошибка",
}

_ACTION_LABELS = {
    "update-session": "Обновление системы",
    "cleanup": "Очистка системы",
}

_CATEGORY_LABELS = {
    "updates": "Обновления",
    "maintenance": "Обслуживание",
}

_MAINTENANCE_ACTION_LABELS = {
    "package-cache": "Кэш обновлений",
    "orphans": "Ненужные зависимости",
    "journal": "Системный журнал",
    "thumbnails": "Кэш миниатюр",
    "trash": "Корзина",
}


class LogPage(NavigablePage):
    """Activity log usable both as a page and as the Settings journal tab."""

    def __init__(self, parent: QWidget | None = None, *, embedded: bool = False):
        super().__init__(parent)
        self._entries: tuple[ActivityEntry, ...] = ()
        self._loaded_once = False
        self._embedded = embedded

        if embedded:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            toolbar = QHBoxLayout()
            toolbar.setSpacing(8)
            title = QLabel("История действий Arch Manager")
            title_font = title.font()
            title_font.setBold(True)
            title.setFont(title_font)
            toolbar.addWidget(title)
            toolbar.addStretch(1)
            self.status_label = QLabel("—")
            muted_text(self.status_label)
            toolbar.addWidget(self.status_label)
            self.refresh_button = QPushButton("Обновить журнал")
            self.refresh_button.clicked.connect(self.refresh)
            toolbar.addWidget(self.refresh_button)
            layout.addLayout(toolbar)
        else:
            layout = self.create_page_layout("Журнал")
            self.status_label = QLabel("—")
            muted_text(self.status_label)
            self.add_header_action(self.status_label)
            self.refresh_button = QPushButton("Обновить журнал")
            self.refresh_button.clicked.connect(self.refresh)
            self.add_header_action(self.refresh_button)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Время", "Раздел", "Действие", "Результат"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.details_card = card_frame()
        details_layout = QVBoxLayout(self.details_card)
        details_layout.setContentsMargins(16, 12, 16, 12)
        details_layout.setSpacing(5)
        title = QLabel("Подробности выбранного события")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self.detail_label = QLabel("Выберите запись в журнале.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        muted_text(self.detail_label)
        details_layout.addWidget(title)
        details_layout.addWidget(self.detail_label)
        layout.addWidget(self.details_card)

    def ensure_loaded(self) -> None:
        if not self._loaded_once:
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        self._entries = read_activity_entries(limit=250)
        self._loaded_once = True
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = (
                format_datetime(entry.timestamp),
                _CATEGORY_LABELS.get(entry.category, entry.category),
                _ACTION_LABELS.get(entry.action, entry.action),
                _RESULT_LABELS.get(entry.result, entry.result),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        count = len(self._entries)
        self.status_label.setText("Нет записей" if count == 0 else f"Записей: {count}")
        if count:
            self.table.selectRow(0)
        else:
            self.detail_label.setText("Arch Manager ещё не записывал событий в журнал.")

    @Slot()
    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._entries):
            self.detail_label.setText("Выберите запись в журнале.")
            return
        entry = self._entries[row]
        lines = [
            f"Время: {format_datetime(entry.timestamp)}",
            f"Раздел: {_CATEGORY_LABELS.get(entry.category, entry.category)}",
            f"Действие: {_ACTION_LABELS.get(entry.action, entry.action)}",
            f"Результат: {_RESULT_LABELS.get(entry.result, entry.result)}",
        ]
        if entry.detail:
            lines.append(f"Деталь: {entry.detail}")
        if entry.data:
            point = entry.data.get("restore_point")
            if point is not None:
                lines.append(f"Защитная точка: Snapper ID {point}")
            official = entry.data.get("official_count")
            aur = entry.data.get("aur_count")
            if official is not None or aur is not None:
                lines.append(f"План обновления: официальных {official or 0}, AUR {aur or 0}")
            reboot = entry.data.get("reboot_summary")
            if reboot:
                lines.append(f"Перезагрузка: {reboot}")
            packages = entry.data.get("packages")
            if isinstance(packages, list) and packages:
                lines.append("Пакеты: " + ", ".join(str(item) for item in packages[:50]))
            actions = entry.data.get("actions")
            if isinstance(actions, list) and actions:
                labels = [_MAINTENANCE_ACTION_LABELS.get(str(item), str(item)) for item in actions[:20]]
                lines.append("Категории очистки: " + ", ".join(labels))
            cache_keep = entry.data.get("package_cache_keep_versions")
            if cache_keep is not None and isinstance(actions, list) and "package-cache" in actions:
                if cache_keep == 0:
                    lines.append("Кэш пакетов: очищен полностью")
                else:
                    lines.append(f"Кэш пакетов: оставлено версий — {cache_keep}")
        self.detail_label.setText("\n".join(lines))
