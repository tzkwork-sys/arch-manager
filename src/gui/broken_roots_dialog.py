from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import muted_text


class BrokenRootsDialog(QDialog):
    """Choose old @.broken-* safety copies for explicit deletion."""

    def __init__(self, roots: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохранённые прежние системы")
        self.resize(610, 390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Страховочные копии прежнего корня")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        title.setFont(font)
        layout.addWidget(title)

        text = QLabel(
            "После успешного восстановления прежний корень @ сохраняется как @.broken-… . "
            "Это позволяет вручную вернуть файлы или систему, если после отката обнаружится проблема. "
            "Удаляйте такие копии только после нескольких успешных загрузок и проверки системы."
        )
        text.setWordWrap(True)
        muted_text(text)
        layout.addWidget(text)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for root in roots:
            self.list_widget.addItem(root)
        self.list_widget.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.list_widget, 1)

        hint = QLabel("Можно выбрать несколько строк через Ctrl или Shift.")
        muted_text(hint)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        self.delete_button = QPushButton("Удалить выбранные")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        buttons.addButton(self.delete_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        layout.addWidget(buttons)

    def _selection_changed(self) -> None:
        count = len(self.list_widget.selectedItems())
        self.delete_button.setEnabled(count > 0)
        self.delete_button.setText(
            f"Удалить выбранные ({count})" if count else "Удалить выбранные"
        )

    def selected_roots(self) -> tuple[str, ...]:
        selected = {item.text() for item in self.list_widget.selectedItems()}
        return tuple(
            self.list_widget.item(row).text()
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).text() in selected
        )
