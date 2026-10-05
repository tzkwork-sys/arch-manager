from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.formatting import format_datetime
from src.core.restore_points import RestorePoint

from .theme import card_frame, muted_text


class DeleteRestorePointDialog(QDialog):
    """Explicit destructive confirmation for deleting one restore point."""

    def __init__(self, point: RestorePoint, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Удалить точку восстановления")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel("Удалить эту точку восстановления?")
        font = heading.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        heading.setFont(font)
        heading.setWordWrap(True)
        layout.addWidget(heading)

        intro = QLabel(
            "Это удалит выбранный snapshot Snapper. Операцию нельзя отменить из Arch Manager."
        )
        intro.setWordWrap(True)
        muted_text(intro)
        layout.addWidget(intro)

        summary = card_frame()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(14, 12, 14, 12)
        summary_layout.setSpacing(6)

        name = QLabel(point.display_name)
        name_font = name.font()
        name_font.setBold(True)
        name.setFont(name_font)
        name.setWordWrap(True)
        summary_layout.addWidget(name)

        details = QLabel(
            f"Дата: {format_datetime(point.created_at)}\n"
            f"Snapper ID: {point.number}\n"
            f"Статус: {'★ Важная' if point.important else 'Обычная'}"
        )
        details.setWordWrap(True)
        muted_text(details)
        summary_layout.addWidget(details)
        layout.addWidget(summary)

        warning = QLabel(
            "После удаления список будет перечитан, а видимая нумерация №1, №2, №3… "
            "перестроится автоматически. Системные Snapper ID не перенумеровываются."
        )
        warning.setWordWrap(True)
        muted_text(warning)
        layout.addWidget(warning)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("Отмена")
        self.delete_button = QPushButton("Удалить точку")
        self.buttons.addButton(
            self.delete_button,
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        self.delete_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
