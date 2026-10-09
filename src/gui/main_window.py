from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.update_state import get_update_state_service
from src.core.updates import summary_from_details

from .app_store import AppStorePage
from .dashboard import DashboardPage
from .maintenance_page import MaintenancePage
from .recovery_center_page import RecoveryCenterPage
from .settings_page import SettingsPage
from .system_page import SystemPage
from .updates_page import UpdatesPage
from .lifecycle import package_operation_running
from .theme import PAGE_STYLESHEET

NAV_ITEMS = [
    ("Обзор", "go-home-symbolic"),
    ("Обновления", "system-software-update-symbolic"),
    ("Восстановление", "edit-undo-symbolic"),
    ("Обслуживание", "applications-system-symbolic"),
    ("Система", "computer-symbolic"),
    ("Приложения", "system-software-install-symbolic"),
    ("Настройки", "settings-configure-symbolic"),
]

NAV_LAYOUT_VERSION = 4


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self.setWindowTitle("Arch Manager")
        self.setMinimumSize(860, 560)

        icon_path = Path(__file__).resolve().parents[1] / "assets" / "arch-manager.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.navigation = QListWidget()
        self.navigation.setObjectName("mainNavigation")
        self.navigation.setFixedWidth(238)
        self.navigation.setSpacing(2)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        for text, icon_name in NAV_ITEMS:
            item = QListWidgetItem(QIcon.fromTheme(icon_name), text)
            item.setSizeHint(QSize(220, 43))
            self.navigation.addItem(item)

        self.stack = QStackedWidget()
        self.update_state = get_update_state_service()
        dashboard = DashboardPage(update_state=self.update_state)
        dashboard.navigate_requested.connect(self.set_page)
        updates_page = UpdatesPage(update_state=self.update_state)
        recovery_center_page = RecoveryCenterPage()
        maintenance_page = MaintenancePage()
        system_page = SystemPage()
        app_store_page = AppStorePage()
        self.app_store_page = app_store_page
        app_store_page.updates_requested.connect(lambda: self.set_page(1))
        app_store_page.package_state_changed.connect(
            lambda: self._package_state_changed(dashboard, updates_page)
        )
        dashboard.update_state_changed.connect(app_store_page.apply_update_details)
        dashboard.update_state_changed.connect(updates_page.apply_shared_details)
        updates_page.update_state_changed.connect(app_store_page.apply_update_details)
        updates_page.update_state_changed.connect(
            lambda details: dashboard.apply_updates_summary(summary_from_details(details))
        )
        updates_page.system_update_finished.connect(app_store_page.refresh_after_system_update)
        updates_page.system_update_finished.connect(dashboard.refresh)
        self.pages = [
            dashboard,
            updates_page,
            recovery_center_page,
            maintenance_page,
            system_page,
            app_store_page,
            SettingsPage(),
        ]
        dashboard.data_updated.connect(updates_page.set_summary)
        dashboard.data_updated.connect(recovery_center_page.set_summary)
        dashboard.data_updated.connect(maintenance_page.set_summary)
        dashboard.data_updated.connect(system_page.set_summary)
        for page in self.pages:
            self.stack.addWidget(page)
            if hasattr(page, "back_requested"):
                page.back_requested.connect(lambda: self.set_page(0))

        self.back_to_overview_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.back_to_overview_shortcut.activated.connect(lambda: self.set_page(0))

        sidebar = QWidget()
        sidebar.setStyleSheet(
            PAGE_STYLESHEET
            + "QListWidget { background-color: palette(base); }"
            "QListWidget::item { border-radius: 6px; padding: 0px 6px; }"
            "QListWidget::item:selected { background-color: palette(highlight); color: palette(highlighted-text); }"
        )
        sidebar.setFixedWidth(258)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 14, 10, 12)
        sidebar_layout.setSpacing(10)

        self.sidebar_brand = QLabel("Arch Manager")
        self.sidebar_brand.setWordWrap(True)
        brand_font = self.sidebar_brand.font()
        brand_font.setPointSize(brand_font.pointSize() + 3)
        brand_font.setBold(True)
        self.sidebar_brand.setFont(brand_font)

        sidebar_layout.addWidget(self.sidebar_brand)
        sidebar_layout.addSpacing(6)

        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.setObjectName("sidebarContextStack")
        self.sidebar_stack.addWidget(self.navigation)
        self.sidebar_stack.addWidget(self._build_app_store_sidebar())
        sidebar_layout.addWidget(self.sidebar_stack, 1)

        app_store_page.sidebar_categories_changed.connect(self._set_app_store_categories)
        app_store_page.sidebar_selection_changed.connect(self._sync_app_store_sidebar_selection)
        self._set_app_store_categories(app_store_page.sidebar_categories())

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.navigation.currentRowChanged.connect(self.set_page)
        self.restore_window_state()

    def _build_app_store_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        back_button = QPushButton("←  Arch Manager")
        back_button.setObjectName("appStoreSidebarBack")
        back_button.setToolTip("Вернуться к обзору Arch Manager")
        back_button.setMinimumHeight(38)
        back_button.clicked.connect(lambda: self.set_page(0))
        layout.addWidget(back_button)

        self.app_store_navigation = QListWidget()
        self.app_store_navigation.setObjectName("appStoreNavigation")
        self.app_store_navigation.setSpacing(1)
        self.app_store_navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.app_store_navigation.currentItemChanged.connect(
            self._app_store_sidebar_item_changed
        )
        layout.addWidget(self.app_store_navigation, 1)
        return panel

    def _set_app_store_categories(self, categories: object) -> None:
        current_entry, current_category = self._app_store_sidebar_state()
        self.app_store_navigation.blockSignals(True)
        self.app_store_navigation.clear()

        self._add_app_store_section_header("МОЯ СИСТЕМА")
        self._add_app_store_nav_item(
            "Установленные", "installed",
            icon_name="emblem-installed-symbolic",
            icon_fallbacks=("dialog-ok-apply", "emblem-default-symbolic", "package-x-generic"),
        )
        self._add_app_store_nav_item(
            "Системные пакеты", "system-packages",
            icon_name="package-x-generic",
            icon_fallbacks=("applications-system",),
        )

        self._add_app_store_section_separator()
        self._add_app_store_section_header("КАТАЛОГ")
        self._add_app_store_nav_item(
            "Все приложения", "catalog", icon_name="view-grid-symbolic"
        )

        for entry in categories or ():
            try:
                key, label = entry
            except (TypeError, ValueError):
                continue
            if not isinstance(key, str) or not isinstance(label, str):
                continue
            self._add_app_store_nav_item(label, "category", category=key)

        self.app_store_navigation.blockSignals(False)
        self._sync_app_store_sidebar_selection(current_entry, current_category)

    def _add_app_store_section_header(self, text: str) -> None:
        item = QListWidgetItem(text)
        self._style_app_store_section_header(item)
        self.app_store_navigation.addItem(item)

    def _add_app_store_section_separator(self) -> None:
        """Separate local-system actions from catalog browsing without a selectable row."""
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, "separator")
        item.setSizeHint(QSize(220, 28))
        self.app_store_navigation.addItem(item)

        separator = QWidget()
        layout = QVBoxLayout(separator)
        layout.setContentsMargins(10, 13, 10, 14)
        line = QFrame()
        line.setObjectName("appStoreSectionSeparator")
        line.setFixedHeight(1)
        # Match the section headings using the current theme's accent.
        palette = self.app_store_navigation.palette()
        divider = palette.color(QPalette.ColorRole.Highlight).lighter(125)
        line.setStyleSheet(f"background-color: {divider.name()}; border: none;")
        layout.addWidget(line)
        self.app_store_navigation.setItemWidget(item, separator)

    def _style_app_store_section_header(self, item: QListWidgetItem) -> None:
        """Make section names the visual separators, not the rows inside them."""
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, "header")
        item.setSizeHint(QSize(220, 34))
        font = item.font()
        font.setBold(True)
        font.setPointSize(max(9, font.pointSize()))
        item.setFont(font)

        palette = self.app_store_navigation.palette()
        accent = palette.color(QPalette.ColorRole.Highlight)
        text = palette.color(QPalette.ColorRole.Text)
        if accent.isValid():
            tint = QColor(accent)
            tint.setAlpha(34)
            item.setBackground(QBrush(tint))
            header_text = QColor(accent).lighter(125)
            if header_text.isValid():
                item.setForeground(QBrush(header_text))
        elif text.isValid():
            item.setForeground(QBrush(text))

    @staticmethod
    def _app_store_theme_icon(primary: str | None, fallbacks: tuple[str, ...]) -> QIcon:
        for name in ((primary,) if primary else ()) + tuple(fallbacks):
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
        return QIcon()

    def _add_app_store_nav_item(
        self,
        text: str,
        entry: str,
        *,
        category: str | None = None,
        icon_name: str | None = None,
        icon_fallbacks: tuple[str, ...] = (),
        emphasized: bool = False,
    ) -> None:
        icon = self._app_store_theme_icon(icon_name, icon_fallbacks)
        item = QListWidgetItem(icon, text)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setData(Qt.ItemDataRole.UserRole + 1, category)
        item.setSizeHint(QSize(220, 39))
        # Section headers carry the visual grouping. Navigation rows stay neutral
        # so only the currently selected row receives the standard KDE highlight.
        del emphasized
        self.app_store_navigation.addItem(item)

    def _app_store_sidebar_state(self) -> tuple[str, str | None]:
        if not hasattr(self, "app_store_navigation"):
            return "catalog", None
        item = self.app_store_navigation.currentItem()
        if item is None:
            return "catalog", None
        entry = item.data(Qt.ItemDataRole.UserRole)
        category = item.data(Qt.ItemDataRole.UserRole + 1)
        return (entry if isinstance(entry, str) else "catalog", category if isinstance(category, str) else None)

    def _app_store_sidebar_item_changed(
        self, item: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry not in {"catalog", "installed", "system-packages", "category"}:
            return
        category = item.data(Qt.ItemDataRole.UserRole + 1)
        self.app_store_page.select_sidebar_entry(
            entry, category if isinstance(category, str) else None
        )

    def _sync_app_store_sidebar_selection(
        self, entry: str, category: object = None
    ) -> None:
        if not hasattr(self, "app_store_navigation"):
            return
        wanted_category = category if isinstance(category, str) else None
        target_row = -1
        for row in range(self.app_store_navigation.count()):
            item = self.app_store_navigation.item(row)
            if item.data(Qt.ItemDataRole.UserRole) != entry:
                continue
            item_category = item.data(Qt.ItemDataRole.UserRole + 1)
            if entry != "category" or item_category == wanted_category:
                target_row = row
                break
        if target_row < 0 and entry == "category":
            entry = "catalog"
            for row in range(self.app_store_navigation.count()):
                if self.app_store_navigation.item(row).data(Qt.ItemDataRole.UserRole) == entry:
                    target_row = row
                    break
        if target_row >= 0 and self.app_store_navigation.currentRow() != target_row:
            self.app_store_navigation.blockSignals(True)
            self.app_store_navigation.setCurrentRow(target_row)
            self.app_store_navigation.blockSignals(False)

    def _sync_app_store_sidebar_selection_from_page(self) -> None:
        entry, category = self.app_store_page.sidebar_selection()
        self._sync_app_store_sidebar_selection(entry, category)

    def _package_state_changed(
        self,
        dashboard: DashboardPage,
        updates_page: UpdatesPage,
    ) -> None:
        """Synchronize all package/update surfaces after install or removal."""

        self.update_state.invalidate()
        updates_page.refresh_if_idle()
        dashboard.refresh()

    def set_page(self, index: int) -> None:
        if not 0 <= index < self.stack.count():
            index = 0
        if self.navigation.currentRow() != index:
            self.navigation.setCurrentRow(index)
        self.stack.setCurrentIndex(index)
        self.sidebar_brand.setText("Диспетчер приложений" if index == 5 else "Arch Manager")
        if hasattr(self, "sidebar_stack"):
            self.sidebar_stack.setCurrentIndex(1 if index == 5 else 0)
            if index == 5:
                self._sync_app_store_sidebar_selection_from_page()
        page = self.pages[index]
        if hasattr(page, "ensure_loaded"):
            page.ensure_loaded()
        self.settings.setValue("window/currentPage", index)
        self.settings.setValue("window/navigationLayoutVersion", NAV_LAYOUT_VERSION)

    def restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1120, 720)

        state = self.settings.value("window/state")
        if state is not None:
            self.restoreState(state)

        # Always start from Overview. DashboardPage schedules a fresh check on
        # construction, so every launch opens on a predictable page with current
        # update/system/maintenance/recovery state instead of restoring a stale tab.
        self.settings.setValue("window/navigationLayoutVersion", NAV_LAYOUT_VERSION)
        self.set_page(0)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API name)
        if package_operation_running():
            event.ignore()
            QMessageBox.information(
                self, "Операция ещё выполняется",
                "Дождитесь завершения установки, удаления или обновления пакетов перед закрытием Arch Manager.",
            )
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.settings.sync()
        super().closeEvent(event)
