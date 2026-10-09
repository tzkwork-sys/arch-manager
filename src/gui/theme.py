from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget


CARD_RADIUS = 8
CARD_MARGINS = (18, 16, 18, 16)
PAGE_SPACING = 12
BUTTON_HEIGHT = 36

# Geometry is shared; colors continue to follow the active KDE palette.
PAGE_STYLESHEET = f"""
QFrame[archManagerCard="true"] {{
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: {CARD_RADIUS}px;
}}
QPushButton {{
    background-color: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background-color: palette(midlight); }}
QPushButton:pressed, QPushButton:checked {{ background-color: palette(mid); }}
QPushButton:focus {{ border-color: palette(highlight); }}
QPushButton:disabled {{ color: palette(mid); border-color: palette(mid); }}
"""


STATUS_COLORS = {
    "ok": QColor("#55c878"),
    "warning": QColor("#d9a400"),
    "critical": QColor("#f85149"),
    "unknown": QColor("#8b949e"),
}


SEMANTIC_BUTTON_COLORS = {
    "install": QColor("#2f6feb"),
    "launch": QColor("#1f9d68"),
    "remove": QColor("#d05c5c"),
    "update": QColor("#c69026"),
}


def _button_css_color(color: QColor) -> str:
    return color.name()


def _button_rgba(color: QColor, alpha: int) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(255, alpha))})"


def _semantic_button_stylesheet(role: str) -> str:
    base = QColor(SEMANTIC_BUTTON_COLORS.get(role, accent_color() or QColor("#4c78d8")))
    if not base.isValid():
        base = QColor("#4c78d8")
    hover = QColor(base).lighter(110)
    pressed = QColor(base).darker(112)
    border = QColor(base).darker(122)
    return (
        "QPushButton {"
        f"background-color: {_button_css_color(base)};"
        "color: #f5f7fa;"
        f"border: 1px solid {_button_css_color(border)};"
        "border-radius: 6px;"
        "padding: 6px 12px;"
        "font-weight: 600;"
        "}"
        "QPushButton:hover {"
        f"background-color: {_button_css_color(hover)};"
        "}"
        "QPushButton:pressed {"
        f"background-color: {_button_css_color(pressed)};"
        "}"
        "QPushButton:focus {"
        f"border: 1px solid {_button_css_color(border)};"
        "}"
        "QPushButton:disabled {"
        f"background-color: {_button_rgba(base, 60)};"
        "color: rgba(245, 247, 250, 0.72);"
        f"border: 1px solid {_button_rgba(border, 80)};"
        "}"
    )


def style_semantic_button(button: QPushButton, role: str) -> None:
    """Apply a semantic color treatment to a push button."""
    button.setMinimumHeight(max(button.minimumHeight(), BUTTON_HEIGHT))
    font = button.font()
    font.setBold(True)
    button.setFont(font)
    button.setStyleSheet(_semantic_button_stylesheet(role))


def muted_text(label: QLabel) -> None:
    palette = label.palette()
    color = palette.color(QPalette.ColorRole.PlaceholderText)
    if not color.isValid():
        color = palette.color(QPalette.ColorRole.Text)
        color.setAlphaF(0.65)
    palette.setColor(QPalette.ColorRole.WindowText, color)
    label.setPalette(palette)


def accent_color() -> QColor:
    app = QApplication.instance()
    if app is None:
        return QColor()
    return app.palette().color(QPalette.ColorRole.Highlight)


def card_frame() -> QFrame:
    """Create a subtle card using only colors from the active Qt/KDE palette."""
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    frame.setFrameShadow(QFrame.Shadow.Plain)
    frame.setLineWidth(1)
    frame.setBackgroundRole(QPalette.ColorRole.Base)
    frame.setAutoFillBackground(True)
    frame.setProperty("archManagerCard", True)
    return frame


class StatusBadge(QLabel):
    """Compact semantic state marker used by summary cards and page headers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(30, 30)
        font = self.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.setFont(font)
        self.set_status("unknown")

    def set_status(self, status: str, tooltip: str = "") -> None:
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
        symbol = "✓" if status == "ok" else ("!" if status in {"warning", "critical"} else "?")
        bg = QColor(color)
        bg.setAlpha(38)
        self.setText(symbol)
        self.setToolTip(tooltip)
        self.setStyleSheet(
            "QLabel {"
            f"color: {color.name()};"
            f"background-color: rgba({bg.red()}, {bg.green()}, {bg.blue()}, {bg.alpha()});"
            f"border: 1px solid {color.name()};"
            "border-radius: 15px;"
            "}"
        )


class BusySpinner(QWidget):
    """Small palette-aware indeterminate spinner without image assets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def start(self) -> None:
        self._phase = 0
        self.show()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % 10
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = self.rect().center()
        radius = min(self.width(), self.height()) * 0.32
        color = accent_color()
        if not color.isValid():
            color = self.palette().color(QPalette.ColorRole.Text)

        for index in range(10):
            alpha_rank = (index - self._phase) % 10
            segment = QColor(color)
            segment.setAlpha(max(45, 255 - alpha_rank * 22))
            pen = QPen(segment, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            angle = math.radians(index * 36 - 90)
            inner = radius * 0.62
            x1 = center.x() + math.cos(angle) * inner
            y1 = center.y() + math.sin(angle) * inner
            x2 = center.x() + math.cos(angle) * radius
            y2 = center.y() + math.sin(angle) * radius
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))


def emphasize_primary_button(button) -> None:
    """Make a primary action obvious while staying aligned with the active KDE palette."""
    button.setProperty("archManagerPrimary", True)
    button.setMinimumHeight(BUTTON_HEIGHT)
    font = button.font()
    font.setBold(True)
    button.setFont(font)
    button.setStyleSheet(
        "QPushButton {"
        "background-color: palette(highlight);"
        "color: palette(highlighted-text);"
        "border: 1px solid palette(highlight);"
        "padding: 6px 12px; border-radius: 6px;"
        "}"
        "QPushButton:hover { background-color: palette(highlight); border-color: palette(highlighted-text); }"
        "QPushButton:pressed { background-color: palette(mid); }"
        "QPushButton:focus { border: 2px solid palette(highlighted-text); padding: 5px 11px; }"
        "QPushButton:disabled { background-color: palette(button); color: palette(mid); border-color: palette(mid); }"
    )
