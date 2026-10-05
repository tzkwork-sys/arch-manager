from __future__ import annotations

from PySide6.QtWidgets import (
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
    build_rename_request,
    normalize_restore_point_description,
)
from src.core.restore_points import RestorePoint

from .theme import muted_text


class RenameRestorePointDialog(QDialog):
    """Rename one existing restore point without exposing its system ID as editable."""

    def __init__(self, point: RestorePoint, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._point_number = point.number
        self._original_name = point.display_name

        self.setWindowTitle("Переименовать точку восстановления")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel("Переименовать точку")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 3)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        intro = QLabel(
            "Изменится только отображаемое название. Снимок Btrfs и технический "
            f"Snapper ID {point.number} останутся прежними."
        )
        intro.setWordWrap(True)
        muted_text(intro)
        layout.addWidget(intro)

        name_label = QLabel("Новое название")
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        layout.addWidget(name_label)

        self.name_edit = QLineEdit(point.display_name)
        self.name_edit.setMaxLength(MAX_DESCRIPTION_LENGTH)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        note = QLabel(
            "После нажатия «Сохранить» система попросит подтверждение администратора."
        )
        note.setWordWrap(True)
        muted_text(note)
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Сохранить")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.name_edit.textChanged.connect(self._update_save_enabled)
        self._update_save_enabled()

    def _update_save_enabled(self) -> None:
        try:
            candidate = normalize_restore_point_description(self.name_edit.text())
            original = normalize_restore_point_description(self._original_name)
        except ValueError:
            self.save_button.setEnabled(False)
            return
        self.save_button.setEnabled(candidate != original)

    def build_request(self) -> RestorePointActionRequest:
        return build_rename_request(self._point_number, self.name_edit.text())
