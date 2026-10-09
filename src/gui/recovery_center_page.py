from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from src.core.dashboard import DashboardSummary

from .page_base import NavigablePage
from .recovery_page import RecoveryPage
from .restore_points_page import RestorePointsPage
from .theme import emphasize_primary_button


class RecoveryCenterPage(NavigablePage):
    """One screen for restore points plus local and USB Recovery paths."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = self.create_page_layout("Восстановление", spacing=8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.checked_label = self.make_checked_label()
        layout.addWidget(self.checked_label, 0, Qt.AlignmentFlag.AlignTop)

        self.restore_points = RestorePointsPage(self, embedded=True)
        self.restore_points.checked_at_changed.connect(
            lambda checked_at: self.set_checked_at(self.checked_label, checked_at)
        )
        if self.header_actions is not None:
            self.restore_points.attach_header_actions(self.header_actions)
        emphasize_primary_button(self.restore_points.create_button)
        self.restore_points.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.restore_points, 0, Qt.AlignmentFlag.AlignTop)
        layout.addSpacing(8)

        recovery_heading = QLabel("Способ восстановления")
        font = recovery_heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        recovery_heading.setFont(font)
        layout.addWidget(recovery_heading, 0, Qt.AlignmentFlag.AlignTop)

        self.recovery = RecoveryPage(self, embedded=True)
        self.recovery.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.recovery, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)

    def ensure_loaded(self) -> None:
        self.restore_points.ensure_loaded()
        self.recovery.ensure_loaded()

    def set_summary(self, summary: DashboardSummary) -> None:
        self.set_checked_at(self.checked_label, summary.checked_at)
        self.restore_points.set_summary(summary)
