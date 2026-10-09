from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.core.formatting import format_datetime

from .theme import BUTTON_HEIGHT, PAGE_SPACING, PAGE_STYLESHEET, card_frame, muted_text


class NavigablePage(QWidget):
    """Base for main pages with one predictable title/action header."""

    # Kept for compatibility with older pages/shortcuts. The visible navigation
    # back button was removed because the permanent sidebar already provides it.
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(PAGE_STYLESHEET)
        self.header_actions: QHBoxLayout | None = None

    def create_page_layout(
        self,
        title: str | None,
        description: str | None = None,
        *,
        spacing: int = PAGE_SPACING,
    ) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 18, 32, 24)
        layout.setSpacing(max(PAGE_SPACING, spacing))

        self.header_actions = QHBoxLayout()
        self.header_actions.setContentsMargins(0, 0, 0, 0)
        self.header_actions.setSpacing(8)
        if title is not None:
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(10)
            heading = QLabel(title)
            heading.setObjectName("pageTitle")
            font = heading.font()
            font.setPointSize(font.pointSize() + 7)
            font.setBold(True)
            heading.setFont(font)
            title_row.addWidget(heading)
            title_row.addStretch(1)
            title_row.addLayout(self.header_actions)
            layout.addLayout(title_row)

        if description:
            text = QLabel(description)
            text.setWordWrap(True)
            text.setMaximumWidth(1050)
            muted_text(text)
            layout.addWidget(text)

        return layout

    def add_header_action(self, widget: QWidget) -> None:
        if self.header_actions is None:
            raise RuntimeError("create_page_layout() must be called before add_header_action()")
        self.header_actions.addWidget(widget)
        widget.setMinimumHeight(BUTTON_HEIGHT)

    @staticmethod
    def make_checked_label() -> QLabel:
        label = QLabel("Последняя проверка: ещё не выполнялась")
        muted_text(label)
        return label

    @staticmethod
    def set_checked_at(label: QLabel, checked_at: datetime) -> None:
        label.setText(f"Последняя проверка: {format_datetime(checked_at)}")


class PlaceholderPage(NavigablePage):
    """A calm user-facing placeholder for features planned for later versions."""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = self.create_page_layout(title, description, spacing=10)

        state_card = card_frame()
        state_card.setMaximumWidth(920)
        state_layout = QVBoxLayout(state_card)
        state_layout.setContentsMargins(18, 16, 18, 16)
        state_layout.setSpacing(6)

        state_title = QLabel("Функция готовится")
        state_title_font = state_title.font()
        state_title_font.setBold(True)
        state_title.setFont(state_title_font)

        state_text = QLabel(
            "Она появится в одной из следующих версий. Сейчас этот раздел ничего не "
            "изменяет в системе."
        )
        state_text.setWordWrap(True)
        muted_text(state_text)

        state_layout.addWidget(state_title)
        state_layout.addWidget(state_text)

        layout.addSpacing(4)
        layout.addWidget(state_card)
        layout.addStretch(1)
