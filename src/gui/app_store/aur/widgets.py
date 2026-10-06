from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from src.app_store.aur.models import AurPackage

from ...theme import muted_text
from ..widgets import (
    CARD_HEIGHT,
    CARD_ICON_SIZE,
    CARD_MAX_WIDTH,
    CARD_MIN_WIDTH,
    INSTALLED_GREEN,
    UPDATE_AMBER,
    _card_highlight_stylesheet,
)


AUR_BADGE_COLOR = QColor("#d6a84b")
AUR_WARNING_COLOR = QColor("#d98b43")
_MAX_TITLE_CHARS = 180
_MAX_SUMMARY_CHARS = 1200
_MAX_META_CHARS = 320


def _safe_display_text(value: object, *, fallback: str = "", limit: int = 1000) -> str:
    """Return bounded plain text suitable for a Qt label.

    AUR metadata is remote input.  RPC validation already checks its types, but
    the GUI boundary stays defensive as well: control NULs are removed and
    unexpectedly large strings are bounded before they reach Qt text layout.
    """

    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = " ".join(text.replace("\x00", " ").split())
    if len(text) > max(1, int(limit)):
        text = text[: max(1, int(limit)) - 1].rstrip() + "…"
    return text or fallback


def _plain_label(text: str, *, parent=None) -> QLabel:
    label = QLabel(text, parent)
    # Force plain text.  AUR descriptions are community supplied and may
    # legitimately contain '<' / '>' sequences that QLabel would otherwise
    # auto-detect as rich text.
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class AurPackageCard(QFrame):
    """Compact read-only AUR result card used by Stage 7.2.

    Deliberately uses only stock QLabel widgets instead of the official-card
    private compact-label helper.  This keeps remote AUR metadata isolated from
    the more complex resize/font fitting path used by AppStream cards.
    """

    activated = Signal(object)

    def __init__(self, package: AurPackage, parent=None) -> None:
        super().__init__(parent)
        self.package = package
        self.setObjectName("appStoreAurCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setLineWidth(1)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setAutoFillBackground(True)
        self.setProperty("archManagerCard", True)
        self.setProperty("appStoreSource", "aur")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setMaximumWidth(CARD_MAX_WIDTH)
        self.setFixedHeight(CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        name = _safe_display_text(package.name, fallback="AUR package", limit=_MAX_TITLE_CHARS)
        version_text = _safe_display_text(package.version, fallback="—", limit=_MAX_META_CHARS)
        description = _safe_display_text(
            package.description,
            fallback="Описание отсутствует",
            limit=_MAX_SUMMARY_CHARS,
        )
        tooltip = [name, f"AUR · {version_text}"]
        if description and description != "Описание отсутствует":
            tooltip.append(description)
        self.setToolTip("\n\n".join(tooltip))

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 9)
        root.setSpacing(9)

        self.icon_label = _plain_label("")
        self.icon_label.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QIcon.fromTheme("package-x-generic")
        if icon.isNull():
            icon = QIcon.fromTheme("application-x-executable")
        pixmap = icon.pixmap(QSize(46, 46))
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.setText("A")
            font = self.icon_label.font()
            font.setBold(True)
            font.setPointSize(max(12, font.pointSize() + 8))
            self.icon_label.setFont(font)
        root.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        self.title_label = _plain_label(name)
        self.title_label.setObjectName("appStoreAurCardTitle")
        self.title_label.setWordWrap(False)
        title_font = self.title_label.font()
        title_font.setBold(True)
        if title_font.pointSize() > 0:
            title_font.setPointSize(title_font.pointSize() + 1)
        self.title_label.setFont(title_font)
        self.title_label.setToolTip(name)
        body.addWidget(self.title_label)

        self.summary_label = _plain_label(description)
        self.summary_label.setObjectName("appStoreAurCardSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        summary_metrics = QFontMetrics(self.summary_label.font())
        self.summary_label.setMaximumHeight(summary_metrics.lineSpacing() * 3 + 3)
        self.summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.summary_label.setToolTip(description)
        muted_text(self.summary_label)
        body.addWidget(self.summary_label)
        body.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)

        self.source_badge = _plain_label("AUR")
        self.source_badge.setObjectName("appStoreAurBadge")
        badge_font = self.source_badge.font()
        badge_font.setBold(True)
        self.source_badge.setFont(badge_font)
        self.source_badge.setStyleSheet(
            f"color: {AUR_BADGE_COLOR.name()}; font-weight: 700;"
        )
        self.source_badge.setToolTip("Источник: Arch User Repository")
        footer.addWidget(self.source_badge)

        self.version_label = _plain_label(version_text)
        self.version_label.setObjectName("appStoreAurVersion")
        self.version_label.setToolTip(version_text)
        muted_text(self.version_label)
        footer.addWidget(self.version_label, 1)
        body.addLayout(footer)

        state_row = QHBoxLayout()
        state_row.setContentsMargins(0, 0, 0, 0)
        state_row.setSpacing(8)

        # AUR maintainer is package metadata, not the application publisher.
        # Do not show it on compact cards. Keep the independent stale warning.
        warning_text = "⚠ Устарел" if package.out_of_date is not None else ""
        self.warning_label = _plain_label(warning_text)
        self.warning_label.setObjectName("appStoreAurOutOfDate")
        self.warning_label.setWordWrap(False)
        self.warning_label.setToolTip(
            "Пакет помечен в AUR как устаревший" if warning_text else ""
        )
        self.warning_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        if warning_text:
            self.warning_label.setStyleSheet(
                f"color: {AUR_WARNING_COLOR.name()}; font-weight: 600;"
            )
        state_row.addWidget(self.warning_label, 1)

        if not package.local_state_known:
            installed_text = "⚠ Статус недоступен"
            installed_color = UPDATE_AMBER
            installed_tooltip = (
                "Не удалось прочитать локальное состояние pacman. "
                "Карточка не считает пакет установленным или неустановленным до повторной проверки."
            )
        elif package.installed and package.update_available:
            installed_text = "↑ Обновление"
            installed_color = UPDATE_AMBER
            installed_tooltip = (
                f"Установлено {package.installed_version or '—'}; "
                f"в AUR доступно {version_text}"
            )
        elif package.installed:
            installed_text = "✓ Установлено"
            installed_color = INSTALLED_GREEN
            installed_tooltip = f"Установленная версия: {package.installed_version or version_text}"
        else:
            installed_text = ""
            installed_color = None
            installed_tooltip = ""

        self.installed_label = _plain_label(installed_text)
        self.installed_label.setObjectName("appStoreAurInstalledBadge")
        self.installed_label.setWordWrap(False)
        self.installed_label.setToolTip(installed_tooltip)
        if installed_color is not None:
            self.installed_label.setStyleSheet(
                f"color: {installed_color.name()}; font-weight: 600;"
            )
        state_row.addWidget(self.installed_label)
        body.addLayout(state_row)

        root.addLayout(body, 1)
        self.setStyleSheet(
            _card_highlight_stylesheet(
                self.objectName(),
                highlighted=package.local_state_known and package.installed,
            )
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit(self.package)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.activated.emit(self.package)
            event.accept()
            return
        super().keyPressEvent(event)
