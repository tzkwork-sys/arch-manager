from __future__ import annotations

from PySide6.QtGui import QGuiApplication
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

from .theme import muted_text


class DetailsDialog(QDialog):
    """Large reusable read-only dialog for diagnostics and technical details."""

    def __init__(
        self,
        title: str,
        text: str,
        *,
        heading: str | None = None,
        note: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(720, 480)
        self._set_comfortable_size()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        if heading:
            heading_label = QLabel(heading)
            font = heading_label.font()
            font.setPointSize(font.pointSize() + 2)
            font.setBold(True)
            heading_label.setFont(font)
            heading_label.setWordWrap(True)
            layout.addWidget(heading_label)

        if note:
            note_label = QLabel(note)
            note_label.setWordWrap(True)
            muted_text(note_label)
            layout.addWidget(note_label)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setPlainText(text)
        layout.addWidget(self.text_edit, 1)

        buttons_row = QHBoxLayout()
        copy_button = QPushButton("Копировать")
        copy_button.clicked.connect(self._copy)
        buttons_row.addWidget(copy_button)
        buttons_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    def _set_comfortable_size(self) -> None:
        screen = self.parentWidget().screen() if self.parentWidget() else QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(900, 620)
            return
        available = screen.availableGeometry()
        width = min(1000, max(720, int(available.width() * 0.68)))
        height = min(720, max(480, int(available.height() * 0.70)))
        self.resize(width, height)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text)
