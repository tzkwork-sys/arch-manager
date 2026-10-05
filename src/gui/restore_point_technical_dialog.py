from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.restore_point_technical import build_restore_point_technical_text
from src.core.restore_points import RestorePoint

from .theme import muted_text


class RestorePointTechnicalDialog(QDialog):
    """Compact read-only view of low-level metadata for one restore point."""

    def __init__(self, point: RestorePoint, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._technical_text = build_restore_point_technical_text(point)

        self.setWindowTitle("Техническая информация")
        self.setModal(True)
        self.setMinimumSize(720, 480)
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel(point.display_name)
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 2)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setWordWrap(True)
        layout.addWidget(heading)

        subtitle = QLabel(f"Системный Snapper ID: {point.number}")
        subtitle.setWordWrap(True)
        muted_text(subtitle)
        layout.addWidget(subtitle)

        note = QLabel(
            "Здесь показаны служебные данные Snapper. Для обычной работы с точкой "
            "вся необходимая информация уже есть в таблице."
        )
        note.setWordWrap(True)
        muted_text(note)
        layout.addWidget(note)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(self._technical_text)
        self.text_edit.setMinimumHeight(300)
        layout.addWidget(self.text_edit)

        buttons_row = QHBoxLayout()
        self.copy_button = QPushButton("Копировать")
        self.copy_button.clicked.connect(self._copy_to_clipboard)
        buttons_row.addWidget(self.copy_button)
        buttons_row.addStretch(1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        self.buttons.rejected.connect(self.reject)
        buttons_row.addWidget(self.buttons)
        layout.addLayout(buttons_row)

    def _copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self._technical_text)
        self.copy_button.setText("Скопировано")
        self.copy_button.setEnabled(False)
