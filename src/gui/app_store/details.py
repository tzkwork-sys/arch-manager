from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import logging
import math
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.app_store.actions import build_install_request, build_remove_request
from src.app_store.launcher import can_launch_application
from src.app_store.media import MediaCache, MediaLoadResult
from src.app_store.models import Application
from src.app_store.package_actions import (
    PackageBusyError,
    PackagePlanError,
    PackageTransactionPlan,
    PackageUnavailableError,
    PackageUpdateRequired,
)
from src.app_store.transactions import (
    PackageActionBusy,
    PackageActionCancelled,
    PackageActionExecutionError,
    PackageActionFailed,
    PackageActionService,
    PackageActionTimedOut,
    PackageActionUnavailable,
    PackageActionUpdateRequired,
    PackageAuthorizationError,
    PackageHelperUnavailable,
    PackageTransactionBusy,
)
from src.core.activity_log import append_activity

from ..theme import muted_text, style_semantic_button
from .widgets import INSTALLED_GREEN, _application_icon

LOGGER = logging.getLogger(__name__)
SCREENSHOT_WIDTH = 420
SCREENSHOT_HEIGHT = 250
SCREENSHOT_GAP = 12


def _human_size(value: int | None) -> str | None:
    if value is None or value < 0:
        return None
    amount = float(value)
    units = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")
    unit = units[0]
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            break
        amount /= 1024.0
    if unit == "Б":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _safe_external_url(value: str | None) -> QUrl | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return QUrl(value)


def _friendly_media_error(message: str) -> str:
    detail = (message or "").strip().lower()
    if not detail:
        return "Не удалось загрузить скриншот"
    offline_markers = (
        "offline",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname",
        "connection refused",
        "connection reset",
        "timed out",
        "network is unreachable",
        "no route to host",
        "failed to establish a new connection",
    )
    if any(marker in detail for marker in offline_markers):
        return "Нет сети или источник недоступен"
    if "too large" in detail:
        return "Скриншот слишком большой"
    if "unexpected media type" in detail:
        return "Источник вернул не изображение"
    return "Не удалось загрузить скриншот"


class _MediaSignals(QObject):
    loaded = Signal(str, object, int)
    failed = Signal(str, str, int)


class _PackageSignals(QObject):
    planned = Signal(object, int)
    completed = Signal(object, int)
    failed = Signal(str, object, int)
    launch_completed = Signal(int)
    launch_failed = Signal(object, int)


class _ScreenshotImageLabel(QLabel):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clickable = False

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip("Открыть скриншот" if clickable else "")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            self._clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ScreenshotTile(QFrame):
    retry_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url = url
        self._source_pixmap = QPixmap()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedSize(SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.image_label = _ScreenshotImageLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setWordWrap(True)
        self.image_label.setText("Загрузка изображения…")
        self.image_label.clicked.connect(lambda: self.open_requested.emit(self.url))
        muted_text(self.image_label)
        root.addWidget(self.image_label, 1)

        self.retry_button = QPushButton("Повторить")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(lambda: self.retry_requested.emit(self.url))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.retry_button)
        row.addStretch(1)
        root.addLayout(row)

    def show_loading(self) -> None:
        self._source_pixmap = QPixmap()
        self.image_label.set_clickable(False)
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Загрузка изображения…")
        self.retry_button.setVisible(False)

    def show_error(self, message: str = "Не удалось загрузить скриншот") -> None:
        self._source_pixmap = QPixmap()
        self.image_label.set_clickable(False)
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(message)
        self.retry_button.setVisible(True)

    def show_image(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.show_error()
            return False
        self._source_pixmap = pixmap
        scaled = pixmap.scaled(
            QSize(SCREENSHOT_WIDTH - 18, SCREENSHOT_HEIGHT - 18),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setText("")
        self.image_label.setPixmap(scaled)
        self.image_label.set_clickable(True)
        self.retry_button.setVisible(False)
        return True

    def source_pixmap(self) -> QPixmap:
        return QPixmap(self._source_pixmap)


class _HorizontalScreenshotArea(QScrollArea):
    """Horizontal gallery where a normal mouse wheel moves screenshots sideways."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().installEventFilter(self)

    def watch_wheel_tree(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Wheel:
            bar = self.horizontalScrollBar()
            if bar.maximum() <= bar.minimum():
                # Nothing to move horizontally: let the outer page handle the wheel.
                return False
            pixel = event.pixelDelta()
            angle = event.angleDelta()
            if pixel.x():
                delta = pixel.x()
            elif pixel.y():
                delta = pixel.y()
            elif angle.x():
                delta = angle.x()
            else:
                delta = angle.y()
            if delta:
                bar.setValue(bar.value() - delta)
                event.accept()
                return True
        return super().eventFilter(watched, event)


class _AutoHeightTextBrowser(QTextBrowser):
    """Rich text view that grows with its document and never owns vertical scrolling."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._schedule_height_update()
        )

    def _schedule_height_update(self) -> None:
        QTimer.singleShot(0, self.recalculate_height)

    @Slot()
    def recalculate_height(self) -> None:
        width = max(80, self.viewport().width())
        document = self.document()
        document.setTextWidth(width)
        target = math.ceil(document.size().height()) + (2 * self.frameWidth()) + 2
        self.setFixedHeight(max(34, target))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._schedule_height_update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        # The outer details-page scroll area is the only vertical scroller.
        event.ignore()


class _ScalablePixmapLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 220)

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self._rescale()

    def _rescale(self) -> None:
        if self._source_pixmap.isNull():
            self.setPixmap(QPixmap())
            return
        target = self.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self.setPixmap(
            self._source_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._rescale()


class ScreenshotViewerDialog(QDialog):
    """Large screenshot viewer with keyboard navigation and optional fullscreen mode."""

    def __init__(
        self,
        application_name: str,
        screenshots: list[tuple[str, QPixmap]],
        start_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.application_name = application_name
        self.screenshots = screenshots
        self.index = max(0, min(start_index, len(screenshots) - 1))
        self.setModal(True)
        self.setMinimumSize(720, 480)
        self.resize(1200, 800)
        self.setObjectName("appStoreScreenshotViewer")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.image_label = _ScalablePixmapLabel()
        root.addWidget(self.image_label, 1)

        controls = QHBoxLayout()
        self.previous_button = QPushButton("‹")
        self.previous_button.setFixedWidth(44)
        self.previous_button.setToolTip("Предыдущий скриншот (←)")
        self.previous_button.clicked.connect(self._previous)
        controls.addWidget(self.previous_button)

        self.counter_label = QLabel()
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.counter_label)

        self.next_button = QPushButton("›")
        self.next_button.setFixedWidth(44)
        self.next_button.setToolTip("Следующий скриншот (→)")
        self.next_button.clicked.connect(self._next)
        controls.addWidget(self.next_button)

        controls.addStretch(1)
        hint = QLabel("←/→ — переключение · F11 — весь экран · Esc — закрыть")
        muted_text(hint)
        controls.addWidget(hint)

        self.fullscreen_button = QPushButton("Во весь экран")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        controls.addWidget(self.fullscreen_button)

        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        controls.addWidget(close_button)
        root.addLayout(controls)

        self._refresh()
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _refresh(self) -> None:
        if not self.screenshots:
            self.image_label.setText("Скриншот недоступен")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.counter_label.setText("")
            self.setWindowTitle(f"{self.application_name} — скриншот")
            return
        _url, pixmap = self.screenshots[self.index]
        self.image_label.set_source_pixmap(pixmap)
        total = len(self.screenshots)
        self.counter_label.setText(f"{self.index + 1} / {total}")
        self.previous_button.setEnabled(total > 1)
        self.next_button.setEnabled(total > 1)
        self.setWindowTitle(
            f"{self.application_name} — скриншот {self.index + 1} из {total}"
        )

    @Slot()
    def _previous(self) -> None:
        if len(self.screenshots) > 1:
            self.index = (self.index - 1) % len(self.screenshots)
            self._refresh()

    @Slot()
    def _next(self) -> None:
        if len(self.screenshots) > 1:
            self.index = (self.index + 1) % len(self.screenshots)
            self._refresh()

    @Slot()
    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showMaximized()
            self.fullscreen_button.setText("Во весь экран")
        else:
            self.showFullScreen()
            self.fullscreen_button.setText("Оконный режим")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Left:
            self._previous()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Right:
            self._next()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
            event.accept()
            return
        super().keyPressEvent(event)


class ApplicationDetailsDialog(QDialog):
    """Application details and safe Stage 4 package actions."""

    updates_requested = Signal()

    def __init__(
        self,
        application: Application,
        parent: QWidget | None = None,
        *,
        media_cache: MediaCache | None = None,
        action_service: PackageActionService | None = None,
    ) -> None:
        super().__init__(parent)
        self.application = application
        self.media_cache = media_cache or MediaCache()
        self.action_service = action_service or PackageActionService()
        self._owns_action_service = action_service is None
        self.package_state_changed = False
        self._pending_package_action: str | None = None
        self._package_busy = False
        self._package_generation = 0
        self._media_generation = 0
        self._tiles: dict[str, ScreenshotTile] = {}
        self._screenshot_urls: tuple[str, ...] = ()
        self._auto_retried_screenshots: set[str] = set()
        self._signals = _MediaSignals(self)
        self._signals.loaded.connect(self._media_loaded)
        self._signals.failed.connect(self._media_failed)
        self._package_signals = _PackageSignals(self)
        self._package_signals.planned.connect(self._package_plan_ready)
        self._package_signals.completed.connect(self._package_action_completed)
        self._package_signals.failed.connect(self._package_action_failed)
        self._package_signals.launch_completed.connect(self._launch_completed)
        self._package_signals.launch_failed.connect(self._launch_failed)

        self.setObjectName("appStoreDetailsDialog")
        self.setWindowTitle(application.name)
        self.setModal(True)
        self.resize(920, 720)
        self.setMinimumSize(680, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(16)
        self.page_scroll.setWidget(body)
        root.addWidget(self.page_scroll, 1)

        self._build_header()
        self._build_screenshots()
        self._build_description()
        self._build_details()
        self._build_links()
        self.body_layout.addStretch(1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.accept)
        close_row.addWidget(self.close_button)
        root.addLayout(close_row)

        # Network requests start only after a details dialog has actually been opened.
        if self._tiles:
            QTimer.singleShot(0, self._load_all_screenshots)

    def _build_header(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(14)
        icon_label = QLabel()
        icon_label.setFixedSize(96, 96)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = _application_icon(self.application)
        pixmap = icon.pixmap(QSize(88, 88))
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap)
        else:
            icon_label.setText("▦")
        row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        title = QLabel(self.application.name)
        title.setWordWrap(True)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 7)
        title.setFont(title_font)
        text.addWidget(title)
        if self.application.summary:
            summary = QLabel(self.application.summary)
            summary.setWordWrap(True)
            muted_text(summary)
            text.addWidget(summary)

        self.status_label = QLabel()
        text.addWidget(self.status_label)
        text.addStretch(1)
        row.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(7)
        self.action_button = QPushButton()
        self.action_button.setObjectName("appStorePackageActionButton")
        self.action_button.setMinimumWidth(130)
        self.action_button.clicked.connect(self._package_action_requested)
        actions.addWidget(self.action_button)

        self.launch_button = QPushButton("Запустить")
        self.launch_button.setObjectName("appStoreLaunchButton")
        self.launch_button.setMinimumWidth(130)
        self.launch_button.clicked.connect(self._launch_requested)
        actions.addWidget(self.launch_button)

        style_semantic_button(self.launch_button, "launch")

        self.package_progress = QProgressBar()
        self.package_progress.setObjectName("appStorePackageProgress")
        self.package_progress.setRange(0, 0)
        self.package_progress.setTextVisible(False)
        self.package_progress.setFixedHeight(4)
        self.package_progress.setVisible(False)
        actions.addWidget(self.package_progress)
        row.addLayout(actions)
        self.body_layout.addLayout(row)
        self._refresh_package_controls()

    def _refresh_package_controls(self) -> None:
        is_system_package = self.application.metadata_source == "pacman-system-package"
        if self.application.installed:
            self.status_label.setText("✓ Установлено")
            self.status_label.setStyleSheet(
                f"color: {INSTALLED_GREEN.name()}; font-weight: 600;"
            )
            self.action_button.setText("Удалить")
            self.action_button.setToolTip(
                "Удалить системный пакет через pacman"
                if is_system_package
                else "Удалить приложение безопасной пакетной операцией"
            )
            style_semantic_button(self.action_button, "remove")
        else:
            self.status_label.setText("Не установлено")
            self.status_label.setStyleSheet("")
            muted_text(self.status_label)
            self.action_button.setText("Установить")
            self.action_button.setToolTip(
                "Установить системный пакет из официального репозитория Arch Linux"
                if is_system_package
                else "Установить приложение из официального репозитория Arch Linux"
            )
            style_semantic_button(self.action_button, "install")

        # Пакетное действие доступно для объектов магазина и установленных пакетов.
        # Минимальные служебные записи без каталожного идентификатора остаются просмотром.
        has_catalog_identity = bool(
            self.application.package_name
            and (
                self.application.installed
                or self.application.repository
                or self.application.metadata_source
                or self.application.available_version
                or "." in self.application.app_id
            )
        )
        self.action_button.setEnabled(
            not self._package_busy and has_catalog_identity
        )

        launchable = self.application.installed and can_launch_application(self.application)
        self.launch_button.setVisible(launchable)
        self.launch_button.setEnabled(launchable and not self._package_busy)
        if hasattr(self, "close_button"):
            self.close_button.setEnabled(not self._package_busy)

    def _set_package_busy(self, busy: bool, text: str | None = None) -> None:
        self._package_busy = busy
        self.package_progress.setVisible(busy)
        if text:
            self.status_label.setStyleSheet("")
            self.status_label.setText(text)
        if busy:
            self.action_button.setEnabled(False)
            self.launch_button.setEnabled(False)
            if hasattr(self, "close_button"):
                self.close_button.setEnabled(False)
        else:
            self._refresh_package_controls()

    @Slot()
    def _package_action_requested(self) -> None:
        if self._package_busy:
            return
        request = (
            build_remove_request(self.application.package_name)
            if self.application.installed
            else build_install_request(self.application.package_name)
        )
        self._pending_package_action = request.action.value
        self._package_generation += 1
        generation = self._package_generation
        self._set_package_busy(True, "Проверяю пакетную операцию…")
        future = self.action_service.plan_async(request)
        future.add_done_callback(
            lambda done, g=generation: self._package_plan_future_done(done, g)
        )

    def _package_plan_future_done(self, future: Future, generation: int) -> None:
        try:
            plan = future.result()
        except Exception as exc:
            self._package_signals.failed.emit("plan", exc, generation)
        else:
            self._package_signals.planned.emit(plan, generation)

    @Slot(object, int)
    def _package_plan_ready(self, plan: object, generation: int) -> None:
        if generation != self._package_generation or not isinstance(plan, PackageTransactionPlan):
            return
        if not self._confirm_package_plan(plan):
            self._pending_package_action = None
            self._set_package_busy(False)
            return
        action_word = "Устанавливаю…" if plan.request.action.value == "install" else "Удаляю…"
        self._set_package_busy(True, action_word)
        future = self.action_service.execute_async(plan.request)
        future.add_done_callback(
            lambda done, g=generation: self._package_action_future_done(done, g)
        )

    def _confirm_package_plan(self, plan: PackageTransactionPlan) -> bool:
        request = plan.request
        names = [change.package_name for change in plan.changes]
        additional = [name for name in names if name != request.package_name]
        if request.action.value == "install" and not additional:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question if request.action.value == "install" else QMessageBox.Icon.Warning)
        is_system_package = self.application.metadata_source == "pacman-system-package"
        object_word = "пакет" if is_system_package else "приложение"
        if request.action.value == "install":
            box.setWindowTitle("Установка системного пакета" if is_system_package else "Установка приложения")
            box.setText(f"Установить {object_word} «{self.application.name}»?")
            box.setInformativeText(
                f"Дополнительно будет установлено пакетов: {len(additional)}."
            )
            detail_names = additional
            accept_text = "Установить"
        else:
            box.setWindowTitle("Удаление системного пакета" if is_system_package else "Удаление приложения")
            box.setText(f"Удалить {object_word} «{self.application.name}»?")
            dependencies = max(0, len(names) - (1 if request.package_name in names else 0))
            warning = (
                "Это системный пакет. Pacman сохранит проверку зависимостей и остановит операцию, "
                "если удаление нарушит зависимости. "
                if is_system_package
                else "Pacman сохранит проверку зависимостей. "
            )
            box.setInformativeText(
                warning + f"Вместе с ним будет удалено ненужных зависимостей: {dependencies}."
            )
            detail_names = names or [request.package_name]
            accept_text = "Удалить"

        if detail_names:
            shown = detail_names[:80]
            details = "\n".join(f"• {name}" for name in shown)
            if len(detail_names) > len(shown):
                details += f"\n… и ещё {len(detail_names) - len(shown)}"
            box.setDetailedText(details)
        accept = box.addButton(accept_text, QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _package_action_future_done(self, future: Future, generation: int) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self._package_signals.failed.emit("execute", exc, generation)
        else:
            self._package_signals.completed.emit(result, generation)

    @Slot(object, int)
    def _package_action_completed(self, result: object, generation: int) -> None:
        if generation != self._package_generation:
            return
        action = getattr(result, "action", None)
        action_value = getattr(action, "value", None)
        if action_value in {"install", "remove"}:
            append_activity(
                "app-store",
                action_value,
                "success",
                f"{self.application.name} ({self.application.package_name})",
                data={
                    "application_name": self.application.name,
                    "package_name": self.application.package_name,
                },
            )
        if action_value == "install":
            self.application = replace(
                self.application,
                installed=True,
                installed_version=self.application.available_version,
                update_available=False,
            )
        elif action_value == "remove":
            self.application = replace(
                self.application,
                installed=False,
                installed_version=None,
                update_available=False,
            )
        self.package_state_changed = True
        self._pending_package_action = None
        self._set_package_busy(False)

    @Slot(str, object, int)
    def _package_action_failed(self, stage: str, error: object, generation: int) -> None:
        if generation != self._package_generation or not isinstance(error, Exception):
            return
        if self._pending_package_action in {"install", "remove"}:
            result_name = "cancelled" if isinstance(error, PackageActionCancelled) else "failed"
            append_activity(
                "app-store",
                self._pending_package_action,
                result_name,
                str(error),
                data={
                    "application_name": self.application.name,
                    "package_name": self.application.package_name,
                },
            )
        self._pending_package_action = None
        self._set_package_busy(False)
        if isinstance(error, PackageActionCancelled):
            self.status_label.setText("Авторизация отменена")
            return

        title, message = self._friendly_package_error(error)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        detail = getattr(error, "detail", "")
        if detail:
            box.setDetailedText(str(detail)[:4000])
        updates_button = None
        if isinstance(error, (PackageUpdateRequired, PackageActionUpdateRequired)):
            updates_button = box.addButton("Перейти к обновлениям", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if updates_button is not None and box.clickedButton() is updates_button:
            self.accept()
            self.updates_requested.emit()

    @staticmethod
    def _friendly_package_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, (PackageUpdateRequired, PackageActionUpdateRequired)):
            return (
                "Сначала обновите систему",
                "Для безопасной установки сначала выполните полное обновление на странице «Обновления», затем повторите установку.",
            )
        if isinstance(error, (PackageBusyError, PackageTransactionBusy, PackageActionBusy)):
            return (
                "Менеджер пакетов занят",
                "Сейчас выполняется другая пакетная операция или системное обновление. Завершите её и повторите действие.",
            )
        if isinstance(error, (PackageUnavailableError, PackageActionUnavailable)):
            return (
                "Пакет недоступен",
                "Приложение больше не найдено в официальных репозиториях Arch Linux. Обновите каталог и повторите попытку.",
            )
        if isinstance(error, PackageHelperUnavailable):
            return (
                "Helper магазина не установлен",
                "Установите системный helper командой ./scripts/install-app-store-helper.sh из папки Arch Manager.",
            )
        if isinstance(error, PackageAuthorizationError):
            return ("Нет разрешения", "Polkit не выдал административное разрешение для пакетной операции.")
        if isinstance(error, PackageActionTimedOut):
            return ("Операция заняла слишком много времени", "Pacman не завершил операцию в установленный срок.")

        detail = str(getattr(error, "detail", "") or "").lower()
        if "unable to lock database" in detail or "could not lock database" in detail:
            return (
                "Менеджер пакетов занят",
                "Pacman уже используется другой операцией. Завершите её и повторите действие.",
            )
        if "no space left" in detail or "not enough free disk space" in detail:
            return ("Недостаточно места", "На диске недостаточно свободного места для этой операции.")
        if "signature" in detail or "invalid or corrupted package" in detail:
            return ("Ошибка подписи пакета", "Pacman отклонил пакет из-за ошибки подписи или проверки целостности.")
        if "could not resolve host" in detail or "failed retrieving file" in detail or "connection" in detail:
            return ("Ошибка сети или зеркала", "Не удалось получить пакет с зеркала Arch Linux. Проверьте сеть и повторите попытку.")
        if "conflict" in detail or "breaks dependency" in detail or "could not satisfy dependencies" in detail:
            return ("Конфликт пакетов", "Pacman обнаружил конфликт или нарушение зависимостей и безопасно остановил операцию.")
        if isinstance(error, (PackagePlanError, PackageActionFailed, PackageActionExecutionError)):
            return ("Пакетная операция не выполнена", str(error))
        return ("Ошибка пакетной операции", str(error))

    @Slot()
    def _launch_requested(self) -> None:
        if self._package_busy or not self.application.installed:
            return
        self._package_generation += 1
        generation = self._package_generation
        self.launch_button.setEnabled(False)
        future = self.action_service.launch_async(self.application)
        future.add_done_callback(lambda done, g=generation: self._launch_future_done(done, g))

    def _launch_future_done(self, future: Future, generation: int) -> None:
        try:
            future.result()
        except Exception as exc:
            self._package_signals.launch_failed.emit(exc, generation)
        else:
            self._package_signals.launch_completed.emit(generation)

    @Slot(int)
    def _launch_completed(self, generation: int) -> None:
        if generation == self._package_generation:
            self._refresh_package_controls()

    @Slot(object, int)
    def _launch_failed(self, error: object, generation: int) -> None:
        if generation != self._package_generation:
            return
        self._refresh_package_controls()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Не удалось запустить приложение")
        box.setText(str(error))
        box.exec()

    def reject(self) -> None:
        if self._package_busy:
            return
        super().reject()

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        label.setFont(font)
        return label

    def _build_screenshots(self) -> None:
        urls = tuple(
            url for url in self.application.screenshots if _safe_external_url(url) is not None
        )
        if not urls:
            return
        self._screenshot_urls = urls
        self.body_layout.addWidget(self._section_title("Скриншоты"))

        gallery_row = QHBoxLayout()
        gallery_row.setContentsMargins(0, 0, 0, 0)
        gallery_row.setSpacing(6)

        self.screenshot_previous_button = QPushButton("‹")
        self.screenshot_previous_button.setFixedWidth(32)
        self.screenshot_previous_button.setToolTip("Прокрутить скриншоты влево")
        self.screenshot_previous_button.clicked.connect(
            lambda: self._scroll_screenshots(-(SCREENSHOT_WIDTH + SCREENSHOT_GAP))
        )
        gallery_row.addWidget(self.screenshot_previous_button)

        self.screenshot_area = _HorizontalScreenshotArea()
        self.screenshot_area.setFixedHeight(SCREENSHOT_HEIGHT + 8)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SCREENSHOT_GAP)
        for url in urls:
            tile = ScreenshotTile(url, host)
            tile.retry_requested.connect(self._retry_screenshot)
            tile.open_requested.connect(self._open_screenshot_viewer)
            self._tiles[url] = tile
            row.addWidget(tile)
            self.screenshot_area.watch_wheel_tree(tile)
        host.adjustSize()
        self.screenshot_area.setWidget(host)
        self.screenshot_area.watch_wheel_tree(host)
        gallery_row.addWidget(self.screenshot_area, 1)

        self.screenshot_next_button = QPushButton("›")
        self.screenshot_next_button.setFixedWidth(32)
        self.screenshot_next_button.setToolTip("Прокрутить скриншоты вправо")
        self.screenshot_next_button.clicked.connect(
            lambda: self._scroll_screenshots(SCREENSHOT_WIDTH + SCREENSHOT_GAP)
        )
        gallery_row.addWidget(self.screenshot_next_button)

        bar = self.screenshot_area.horizontalScrollBar()
        bar.rangeChanged.connect(lambda _minimum, _maximum: self._update_screenshot_navigation())
        bar.valueChanged.connect(lambda _value: self._update_screenshot_navigation())
        self.body_layout.addLayout(gallery_row)
        QTimer.singleShot(0, self._update_screenshot_navigation)

    @Slot()
    def _update_screenshot_navigation(self) -> None:
        if not hasattr(self, "screenshot_area"):
            return
        bar = self.screenshot_area.horizontalScrollBar()
        has_overflow = bar.maximum() > bar.minimum()
        self.screenshot_previous_button.setVisible(has_overflow)
        self.screenshot_next_button.setVisible(has_overflow)
        self.screenshot_previous_button.setEnabled(has_overflow and bar.value() > bar.minimum())
        self.screenshot_next_button.setEnabled(has_overflow and bar.value() < bar.maximum())

    def _scroll_screenshots(self, amount: int) -> None:
        if not hasattr(self, "screenshot_area"):
            return
        bar = self.screenshot_area.horizontalScrollBar()
        bar.setValue(bar.value() + amount)

    @Slot(str)
    def _open_screenshot_viewer(self, url: str) -> None:
        loaded: list[tuple[str, QPixmap]] = []
        for candidate in self._screenshot_urls:
            tile = self._tiles.get(candidate)
            if tile is None:
                continue
            pixmap = tile.source_pixmap()
            if not pixmap.isNull():
                loaded.append((candidate, pixmap))
        if not loaded:
            return
        start_index = next(
            (index for index, (candidate, _pixmap) in enumerate(loaded) if candidate == url),
            0,
        )
        viewer = ScreenshotViewerDialog(
            self.application.name,
            loaded,
            start_index,
            self,
        )
        viewer.exec()

    def _build_description(self) -> None:
        if not self.application.description and not self.application.description_html:
            return
        self.body_layout.addWidget(self._section_title("Описание"))
        view = _AutoHeightTextBrowser()
        view.setObjectName("appStoreDescription")
        if self.application.description_html:
            view.setHtml(self.application.description_html)
        else:
            view.setPlainText(self.application.description)
        self.body_layout.addWidget(view)
        QTimer.singleShot(0, view.recalculate_height)

    def _build_details(self) -> None:
        details: list[tuple[str, str]] = []
        version = self.application.installed_version or self.application.available_version
        if version:
            details.append(("Версия", version))
        if self.application.publisher:
            details.append(("Издатель / разработчик", self.application.publisher))
        if self.application.repository:
            details.append(("Репозиторий", self.application.repository))
        download = _human_size(self.application.download_size)
        installed = _human_size(self.application.installed_size)
        if download:
            details.append(("Размер загрузки", download))
        if installed:
            details.append(("После установки", installed))
        if self.application.license:
            details.append(("Лицензия", self.application.license))
        if self.application.package_name:
            details.append(("Пакет", self.application.package_name))
        if self.application.dependencies_text:
            details.append(("Зависит от", self.application.dependencies_text))
        if self.application.optional_dependencies_text:
            details.append(("Опциональные зависимости", self.application.optional_dependencies_text))
        if self.application.required_by_text:
            details.append(("Требуется пакетам", self.application.required_by_text))
        if self.application.optional_for_text:
            details.append(("Опционален для", self.application.optional_for_text))
        if self.application.provides_text:
            details.append(("Предоставляет", self.application.provides_text))
        if self.application.conflicts_text:
            details.append(("Конфликтует с", self.application.conflicts_text))
        if self.application.update_available and self.application.available_version:
            details.append(("Доступно обновление", self.application.available_version))
        if not details:
            return

        self.body_layout.addWidget(self._section_title("Сведения"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        for row, (key, value) in enumerate(details):
            key_label = QLabel(key)
            muted_text(key_label)
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
        grid.setColumnStretch(1, 1)
        self.body_layout.addLayout(grid)

    def _build_links(self) -> None:
        links = [
            ("Сайт приложения", self.application.homepage),
            ("Сообщить об ошибке", self.application.bugtracker),
            ("Справка", self.application.help_url),
        ]
        valid = [(label, value) for label, value in links if _safe_external_url(value) is not None]
        if not valid:
            return
        self.body_layout.addWidget(self._section_title("Ссылки"))
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, value in valid:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, url=value: self._open_url(url))
            row.addWidget(button)
        row.addStretch(1)
        self.body_layout.addLayout(row)

    @Slot()
    def _load_all_screenshots(self) -> None:
        for url in self._tiles:
            self._start_media_load(url, retry=False)

    @Slot(str)
    def _retry_screenshot(self, url: str) -> None:
        self._start_media_load(url, retry=True)

    def _start_media_load(self, url: str, *, retry: bool) -> None:
        tile = self._tiles.get(url)
        if tile is None:
            return
        tile.show_loading()
        generation = self._media_generation
        try:
            future = self.media_cache.load_async(url, retry=retry)
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._signals.failed.emit(url, str(exc), generation)
            return
        future.add_done_callback(
            lambda done, u=url, g=generation: self._media_future_done(u, done, g)
        )

    def _media_future_done(
        self,
        url: str,
        future: Future[MediaLoadResult],
        generation: int,
    ) -> None:
        if generation != self._media_generation:
            return
        try:
            result = future.result()
        except Exception as exc:
            self._signals.failed.emit(url, str(exc), generation)
        else:
            self._signals.loaded.emit(url, result.data, generation)

    @Slot(str, object, int)
    def _media_loaded(self, url: str, data: object, generation: int) -> None:
        if generation != self._media_generation or not isinstance(data, (bytes, bytearray)):
            return
        tile = self._tiles.get(url)
        if tile is None:
            return
        if tile.show_image(bytes(data)):
            self._auto_retried_screenshots.discard(url)
            return
        LOGGER.warning("Invalid screenshot image for %s", url)
        if url not in self._auto_retried_screenshots:
            self._auto_retried_screenshots.add(url)
            self._start_media_load(url, retry=True)
            return
        tile.show_error("Не удалось прочитать скриншот")

    @Slot(str, str, int)
    def _media_failed(self, url: str, message: str, generation: int) -> None:
        if generation != self._media_generation:
            return
        LOGGER.info("App Store screenshot unavailable (%s): %s", url, message)
        tile = self._tiles.get(url)
        if tile is not None:
            tile.show_error(_friendly_media_error(message))

    def _open_url(self, value: str | None) -> None:
        url = _safe_external_url(value)
        if url is not None:
            QDesktopServices.openUrl(url)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._package_busy:
            event.ignore()
            return
        self._media_generation += 1
        if self._owns_action_service:
            self.action_service.shutdown()
        super().closeEvent(event)
