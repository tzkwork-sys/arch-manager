from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.restore_point_actions import (
    MAX_DESCRIPTION_LENGTH,
    RestorePointActionRequest,
    build_create_request,
)

from .theme import muted_text


class CreateRestorePointDialog(QDialog):
    """Small user-facing form for one manual restore point."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Создать точку восстановления")
        self.setModal(True)
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel("Новая точка восстановления")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 3)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        intro = QLabel(
            "Задайте понятное название. После нажатия «Создать» система попросит "
            "подтверждение администратора."
        )
        intro.setWordWrap(True)
        muted_text(intro)
        layout.addWidget(intro)

        name_label = QLabel("Название")
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        layout.addWidget(name_label)

        self.name_edit = QLineEdit("Ручная точка")
        self.name_edit.setMaxLength(MAX_DESCRIPTION_LENGTH)
        self.name_edit.setPlaceholderText("Например: Перед установкой драйвера")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        self.important_check = QCheckBox("Пометить как важную")
        self.important_check.setToolTip(
            "Важные точки Snapper учитывает отдельно в политике хранения."
        )
        layout.addWidget(self.important_check)

        note = QLabel(
            "Arch Manager создаст обычную single-точку Snapper. "
            "Восстановление системы этим действием не запускается."
        )
        note.setWordWrap(True)
        muted_text(note)
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.create_button.setText("Создать")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.name_edit.textChanged.connect(self._update_create_enabled)
        self._update_create_enabled()

    def _update_create_enabled(self) -> None:
        self.create_button.setEnabled(bool(self.name_edit.text().strip()))

    def build_request(self) -> RestorePointActionRequest:
        return build_create_request(
            self.name_edit.text(),
            important=self.important_check.isChecked(),
        )
