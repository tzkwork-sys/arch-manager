from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from src.app_store.categories import CATEGORY_LABELS_RU
from src.app_store.models import Application

from ..theme import muted_text


# Keep the original dense Stage 2 geometry: five cards fit in the normal
# Arch Manager window. Text handling is improved without enlarging the cards.
CARD_MIN_WIDTH = 238
CARD_MAX_WIDTH = 340
CARD_HEIGHT = 154
CARD_ICON_SIZE = 48
INSTALLED_GREEN = QColor("#55c878")
UPDATE_AMBER = QColor("#d8a847")
CARD_HIGHLIGHT_BORDER_WIDTH = 1
CARD_HIGHLIGHT_RADIUS = 4


def _card_highlight_stylesheet(object_name: str, *, highlighted: bool) -> str:
    if not highlighted:
        return ""
    return (
        f"QFrame#{object_name} {{"
        f"border: {CARD_HIGHLIGHT_BORDER_WIDTH}px solid {INSTALLED_GREEN.name()};"
        f"border-radius: {CARD_HIGHLIGHT_RADIUS}px;"
        "}"
    )


class _CompactTextLabel(QLabel):
    """Compact multiline text with optional small font reduction and ellipsis."""

    def __init__(
        self,
        text: str,
        *,
        max_lines: int,
        shrink_points: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._full_text = " ".join((text or "").split())
        self._max_lines = max(1, int(max_lines))
        self._shrink_points = max(0, int(shrink_points))
        self._base_font = self.font()
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setToolTip(self._full_text)
        self._refresh()

    @property
    def full_text(self) -> str:
        return self._full_text

    def set_base_font(self, font) -> None:
        self._base_font = font
        self.setFont(font)
        self._refresh()

    @staticmethod
    def _wrap(metrics: QFontMetrics, text: str, width: int, max_lines: int) -> tuple[list[str], bool]:
        if not text:
            return [], True
        words = text.split()
        lines: list[str] = []
        current = ""
        idx = 0
        while idx < len(words):
            word = words[idx]
            candidate = word if not current else f"{current} {word}"
            if metrics.horizontalAdvance(candidate) <= width:
                current = candidate
                idx += 1
                continue
            if current:
                lines.append(current)
                current = ""
                if len(lines) >= max_lines:
                    return lines, False
                continue
            # A single very long token cannot be wrapped naturally.
            lines.append(metrics.elidedText(word, Qt.TextElideMode.ElideRight, width))
            idx += 1
            if len(lines) >= max_lines and idx < len(words):
                return lines, False
        if current:
            lines.append(current)
        return lines[:max_lines], len(lines) <= max_lines

    def _refresh(self) -> None:
        width = self.contentsRect().width()
        if width <= 8:
            super().setText(self._full_text)
            return

        base = self._base_font
        base_size = base.pointSizeF()
        if base_size <= 0:
            base_size = float(base.pointSize() if base.pointSize() > 0 else 10)

        chosen_font = base
        chosen_lines: list[str] = []
        complete = False
        for reduction in range(self._shrink_points + 1):
            font = QFontCopy(base)
            font.setPointSizeF(max(7.5, base_size - reduction))
            metrics = QFontMetrics(font)
            lines, fits = self._wrap(metrics, self._full_text, width, self._max_lines)
            chosen_font = font
            chosen_lines = lines
            complete = fits
            if fits:
                break

        super().setFont(chosen_font)
        metrics = QFontMetrics(chosen_font)
        self.setFixedHeight(metrics.lineSpacing() * self._max_lines + 1)

        if not complete and chosen_lines:
            # Rebuild the final line from the unshown tail and elide it cleanly.
            prefix_words = " ".join(chosen_lines[:-1]).split()
            all_words = self._full_text.split()
            remaining = " ".join(all_words[len(prefix_words):])
            chosen_lines[-1] = metrics.elidedText(
                remaining or chosen_lines[-1],
                Qt.TextElideMode.ElideRight,
                width,
            )
        super().setText("\n".join(chosen_lines))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            # Ignore FontChange generated by our own fitting font; keep the
            # original logical base font for future resizes.
            if event.type() == QEvent.Type.StyleChange:
                self._base_font = self.font()
            self._refresh()


# QFont has no copy constructor name in Python, but constructing from QFont is
# supported. Keeping it behind a helper makes the fitting loop easier to read.
def QFontCopy(font):
    from PySide6.QtGui import QFont

    return QFont(font)


def _application_icon(application: Application) -> QIcon:
    if application.icon:
        candidate = Path(application.icon)
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
        if application.icon_type != "cached":
            icon = QIcon.fromTheme(application.icon)
            if not icon.isNull():
                return icon
    return QIcon.fromTheme("application-x-executable")


def _category_for_card(application: Application, preferred_category: str | None) -> str:
    if preferred_category and preferred_category in application.categories:
        return preferred_category
    if application.categories:
        return application.categories[0]
    return "other"


class ApplicationCard(QFrame):
    """Compact catalog card that opens a read-only Stage 3 details view."""

    activated = Signal(object)

    def __init__(
        self,
        application: Application,
        parent=None,
        *,
        preferred_category: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setLineWidth(1)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setAutoFillBackground(True)
        self.setProperty("archManagerCard", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.application = application
        self.setObjectName("appStoreCard")
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setMaximumWidth(CARD_MAX_WIDTH)
        self.setFixedHeight(CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        tooltip_parts = [application.name]
        if application.summary:
            tooltip_parts.append(application.summary)
        self.setToolTip("\n\n".join(tooltip_parts))

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 9)
        root.setSpacing(9)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = _application_icon(application)
        pixmap = icon.pixmap(QSize(46, 46))
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.setText("▦")
            placeholder_font = self.icon_label.font()
            placeholder_font.setPointSize(23)
            self.icon_label.setFont(placeholder_font)
        root.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        self.title_label = _CompactTextLabel(application.name, max_lines=1)
        self.title_label.setObjectName("appStoreCardTitle")
        title_font = self.title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        self.title_label.set_base_font(title_font)
        body.addWidget(self.title_label)

        summary_text = application.summary or "Описание отсутствует"
        self.summary_label = _CompactTextLabel(
            summary_text,
            max_lines=2,
            shrink_points=2,
        )
        self.summary_label.setObjectName("appStoreCardSummary")
        muted_text(self.summary_label)
        # muted_text may alter the palette but not the fitting base font.
        self.summary_label._base_font = self.summary_label.font()
        self.summary_label._refresh()
        body.addWidget(self.summary_label)

        publisher_text = f"Издатель: {application.publisher}" if application.publisher else ""
        self.publisher_label = _CompactTextLabel(publisher_text, max_lines=1)
        self.publisher_label.setObjectName("appStorePublisherLabel")
        self.publisher_label.setToolTip(
            f"Разработчик / издатель по данным AppStream: {application.publisher}"
            if application.publisher
            else ""
        )
        muted_text(self.publisher_label)
        self.publisher_label.setVisible(bool(publisher_text))
        body.addWidget(self.publisher_label)
        body.addStretch(1)

        if application.metadata_source == "pacman-system-package":
            version = application.installed_version or application.available_version or ""
            system_bits = [
                value
                for value in (application.repository or "Системный пакет", version)
                if value
            ]
            category_text = " · ".join(system_bits) or "Системный пакет"
        else:
            category_key = _category_for_card(application, preferred_category)
            category_text = CATEGORY_LABELS_RU.get(category_key, "Прочее")
        self.category_label = _CompactTextLabel(category_text, max_lines=1)
        self.category_label.setObjectName("appStoreCategoryLabel")
        muted_text(self.category_label)
        body.addWidget(self.category_label)

        if not application.installed_state_known:
            status_text = "⚠ Статус недоступен"
        elif application.installed and application.update_available:
            old_version = application.installed_version or "…"
            new_version = application.available_version or "…"
            status_text = f"↑ {old_version} → {new_version}"
        elif application.installed:
            status_text = "✓ Установлено"
        else:
            status_text = ""

        self.installed_label = QLabel(status_text)
        self.installed_label.setObjectName("appStoreInstalledBadge")
        installed_metrics = QFontMetrics(self.installed_label.font())
        self.installed_label.setFixedHeight(installed_metrics.lineSpacing() + 1)
        if not application.installed_state_known:
            self.installed_label.setStyleSheet(
                f"color: {UPDATE_AMBER.name()}; font-weight: 600;"
            )
            self.installed_label.setToolTip(
                "Не удалось определить, установлен ли пакет. Обновите каталог и повторите проверку."
            )
        elif application.installed:
            badge_color = UPDATE_AMBER if application.update_available else INSTALLED_GREEN
            self.installed_label.setStyleSheet(
                f"color: {badge_color.name()}; font-weight: 600;"
            )
            self.installed_label.setToolTip(
                "Доступно обновление приложения"
                if application.update_available
                else "Приложение установлено"
            )
        body.addWidget(self.installed_label)

        root.addLayout(body, 1)
        self.setStyleSheet(
            _card_highlight_stylesheet(
                self.objectName(),
                highlighted=application.installed_state_known and application.installed,
            )
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.application)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.activated.emit(self.application)
            event.accept()
            return
        super().keyPressEvent(event)
