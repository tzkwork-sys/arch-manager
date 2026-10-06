from __future__ import annotations

from concurrent.futures import Future
import logging
from pathlib import Path
from math import floor

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app_store.aur.errors import AurInvalidResponse, AurNetworkError, AurRpcError
from src.app_store.aur.models import AurInstalledSnapshot, AurPackage
from src.app_store.aur.service import AurService
from src.app_store.aur.planner import AurUpdateAllPlan, AurUpdatePlanner
from src.app_store.catalog import AppCatalogService, CatalogLoadResult
from src.app_store.categories import CATEGORY_LABELS_RU
from src.app_store.installed import InstalledApplications
from src.app_store.integration import (
    current_update_details,
    invalidate_update_state,
    last_app_store_activity,
    record_aur_bulk_update,
)
from src.app_store.local_installed import LocalInstalledApplicationReader
from src.app_store.media import MediaCache
from src.app_store.models import Application
from src.app_store.presentation import ApplicationIndex, CatalogQuery
from src.app_store.search import search_candidate_rank
from src.app_store.popularity import (
    PkgstatsPopularityService,
    PopularitySnapshot,
)
from src.app_store.system_packages import SystemPackageSearchService
from src.app_store.transactions import PackageActionService
from src.app_store.update_mapping import ApplicationUpdateMapper

from ..page_base import NavigablePage
from ..theme import BusySpinner, muted_text
from .aur.details import AurPackageDetailsDialog
from .aur.integration import (
    filter_installed_aur_packages,
    merge_store_results,
    prioritize_exact_aur_match,
)
from .aur.widgets import AurPackageCard
from .aur.terminal import AurTerminalProcess, AurTerminalResult
from .details import ApplicationDetailsDialog
from .widgets import ApplicationCard, CARD_MAX_WIDTH, CARD_MIN_WIDTH

LOGGER = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 180
AUR_SEARCH_DEBOUNCE_MS = 360
SYSTEM_PACKAGE_SEARCH_DEBOUNCE_MS = 280
GRID_SPACING = 14
INITIAL_BATCH_SIZE = 36
NEXT_BATCH_SIZE = 30


class _CatalogSignals(QObject):
    loaded = Signal(object, int)
    failed = Signal(str, int)


class _AurSearchSignals(QObject):
    loaded = Signal(object, str, int)
    failed = Signal(object, str, int)


class _AurInstalledSignals(QObject):
    loaded = Signal(object, int)
    failed = Signal(object, int)


class _AurUpdateAllSignals(QObject):
    planned = Signal(object, int)
    failed = Signal(object, int)


class _PopularitySignals(QObject):
    loaded = Signal(object, int)
    failed = Signal(object, int)


class _SystemPackageSignals(QObject):
    loaded = Signal(object, str, int)
    failed = Signal(object, str, int)


class AppStorePage(NavigablePage):
    """Application catalog with Stage 4 install/remove/launch actions.

    AppStream parsing, package reads and mutations stay behind dedicated
    services. The GUI never receives root privileges and never builds shell
    commands.
    """

    updates_requested = Signal()
    package_state_changed = Signal()
    sidebar_categories_changed = Signal(object)
    sidebar_selection_changed = Signal(str, object)

    STATE_LOADING = 0
    STATE_CATALOG = 1
    STATE_EMPTY = 2
    STATE_ERROR = 3

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        service: AppCatalogService | None = None,
        aur_service: AurService | None = None,
        popularity_service: PkgstatsPopularityService | None = None,
        system_package_service: SystemPackageSearchService | None = None,
    ) -> None:
        super().__init__(parent)
        official_service_injected = service is not None
        self.service = service or AppCatalogService()
        # A caller that injects an official catalog service (notably unit tests)
        # must opt in to a matching AUR service as well.  Normal production
        # construction creates both services.
        self._aur_service = (
            aur_service
            if aur_service is not None
            else (None if official_service_injected else AurService())
        )
        self._popularity_service = popularity_service or PkgstatsPopularityService()
        self._system_package_service = system_package_service or SystemPackageSearchService()
        self._index = ApplicationIndex()
        self._local_index = ApplicationIndex()
        self._local_reader = LocalInstalledApplicationReader()
        self._media_cache = MediaCache()
        self._package_actions = PackageActionService()
        self._filtered: tuple[Application | AurPackage, ...] = ()
        self._cards: list[QWidget] = []
        self._render_cursor = 0
        self._card_render_errors = 0
        self._loaded = False
        self._loading = False
        self._load_generation = 0
        self._columns = 1
        self._update_details: object | None = None
        self._view_switching = False
        # «Все приложения» и «Популярные» могут использовать одну и ту же
        # сортировку, поэтому выбранный пункт бокового меню храним явно.
        self._sidebar_catalog_entry = "catalog"
        self._catalog_source_before_view: str | None = None
        self._filters_enabled = False
        self._aur_results: tuple[AurPackage, ...] = ()
        self._aur_result_query = ""
        self._aur_loading = False
        self._aur_error: Exception | None = None
        self._aur_generation = 0
        self._aur_next_use_cache = True
        self._aur_installed_results: tuple[AurPackage, ...] = ()
        self._local_installed_results: tuple[Application, ...] = ()
        self._aur_installed_loading = False
        self._aur_installed_error: Exception | None = None
        self._aur_installed_generation = 0
        self._aur_update_generation = 0
        self._aur_update_all_busy = False
        self._popularity_scores: dict[str, float] = {}
        self._popularity_counts: dict[str, int] = {}
        self._popularity_loading = False
        self._popularity_error: Exception | None = None
        self._popularity_loaded = False
        self._popularity_stale = False
        self._popularity_generation = 0
        self._system_packages_mode = False
        self._system_package_results: tuple[Application, ...] = ()
        self._system_package_query = ""
        self._system_package_loading = False
        self._system_package_error: Exception | None = None
        self._system_package_generation = 0
        self._system_package_next_use_cache = True

        self._update_details = current_update_details()
        self._aur_update_planner = AurUpdatePlanner(self._aur_service) if self._aur_service is not None else None
        self._aur_terminal = AurTerminalProcess(self) if self._aur_service is not None else None

        self._signals = _CatalogSignals(self)
        self._signals.loaded.connect(self._catalog_loaded)
        self._signals.failed.connect(self._catalog_failed)
        self._aur_signals = _AurSearchSignals(self)
        self._aur_signals.loaded.connect(self._aur_search_loaded)
        self._aur_signals.failed.connect(self._aur_search_failed)
        self._aur_installed_signals = _AurInstalledSignals(self)
        self._aur_installed_signals.loaded.connect(self._aur_installed_loaded)
        self._aur_installed_signals.failed.connect(self._aur_installed_failed)
        self._aur_update_all_signals = _AurUpdateAllSignals(self)
        self._aur_update_all_signals.planned.connect(self._aur_update_all_planned)
        self._aur_update_all_signals.failed.connect(self._aur_update_all_failed)
        self._popularity_signals = _PopularitySignals(self)
        self._popularity_signals.loaded.connect(self._popularity_loaded_result)
        self._popularity_signals.failed.connect(self._popularity_failed)
        self._system_package_signals = _SystemPackageSignals(self)
        self._system_package_signals.loaded.connect(self._system_package_search_loaded)
        self._system_package_signals.failed.connect(self._system_package_search_failed)
        if self._aur_terminal is not None:
            self._aur_terminal.completed.connect(self._aur_update_all_completed)
            self._aur_terminal.failed.connect(self._aur_update_all_terminal_failed)

        layout = self.create_page_layout("Приложения", spacing=10)

        self.reload_button = QPushButton("Обновить каталог")
        self.reload_button.setObjectName("appStoreReloadCatalogButton")
        self.reload_button.setAutoDefault(False)
        self.reload_button.clicked.connect(self._reload_requested)
        self.add_header_action(self.reload_button)

        self.aur_update_all_button = QPushButton("Обновить все AUR")
        self.aur_update_all_button.setObjectName("appStoreAurUpdateAllButton")
        self.aur_update_all_button.setAutoDefault(False)
        self.aur_update_all_button.clicked.connect(self._update_all_aur_requested)
        self.aur_update_all_button.setVisible(False)
        self.add_header_action(self.aur_update_all_button)

        filters = QHBoxLayout()
        filters.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("appStoreSearch")
        self.search_edit.setPlaceholderText("Поиск приложений…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumHeight(36)
        self.search_edit.textChanged.connect(self._search_changed)
        filters.addWidget(self.search_edit, 1)

        self.source_combo = QComboBox()
        self.source_combo.setObjectName("appStoreSourceFilter")
        self.source_combo.setMinimumWidth(155)
        self.source_combo.setMinimumHeight(36)
        self.source_combo.addItem("Все", "all")
        self.source_combo.addItem("Официальные", "official")
        self.source_combo.addItem("AUR", "aur")
        self.source_combo.addItem("Локальные", "local")
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        filters.addWidget(self.source_combo)

        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("appStoreSortFilter")
        self.sort_combo.setMinimumWidth(175)
        self.sort_combo.setMinimumHeight(36)
        self.sort_combo.addItem("По популярности", "popularity")
        self.sort_combo.addItem("По названию", "name")
        self.sort_combo.setToolTip(
            "Популярность берётся из анонимной статистики pkgstats и кэшируется на 24 часа."
        )
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        filters.addWidget(self.sort_combo)

        self.category_combo = QComboBox()
        self.category_combo.setObjectName("appStoreCategoryFilter")
        self.category_combo.setMinimumWidth(190)
        self.category_combo.setMinimumHeight(36)
        self.category_combo.addItem("Все категории", None)
        self.category_combo.currentIndexChanged.connect(self._filters_changed)
        # Kept as an internal state control for compatibility with filtering/tests.
        # Category selection is exposed in the contextual application sidebar.
        self.category_combo.setVisible(False)

        self.installed_button = QToolButton()
        self.installed_button.setObjectName("appStoreInstalledFilter")
        self.installed_button.setText("Установленные")
        self.installed_button.setCheckable(True)
        self.installed_button.setMinimumHeight(36)
        self.installed_button.toggled.connect(
            lambda checked: self._view_changed("installed", checked)
        )
        self.installed_button.setVisible(False)

        self.updates_button = QToolButton()
        self.updates_button.setObjectName("appStoreUpdatesFilter")
        self.updates_button.setText("Обновления")
        self.updates_button.setCheckable(True)
        self.updates_button.setMinimumHeight(36)
        self.updates_button.toggled.connect(
            lambda checked: self._view_changed("updates", checked)
        )
        self.updates_button.setVisible(False)

        layout.addLayout(filters)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        self.activity_spinner = BusySpinner()
        self.activity_spinner.setObjectName("appStoreActivitySpinner")
        self.activity_spinner.setFixedSize(20, 20)
        self.activity_spinner.setToolTip("Arch Manager получает и обрабатывает данные…")
        status_row.addWidget(self.activity_spinner)
        self.count_label = QLabel("")
        self.count_label.setObjectName("appStoreCount")
        muted_text(self.count_label)
        status_row.addWidget(self.count_label)
        self.aur_status_label = QLabel("")
        self.aur_status_label.setObjectName("appStoreAurStatus")
        muted_text(self.aur_status_label)
        status_row.addWidget(self.aur_status_label)
        self.popularity_status_label = QLabel("")
        self.popularity_status_label.setObjectName("appStorePopularityStatus")
        muted_text(self.popularity_status_label)
        status_row.addWidget(self.popularity_status_label)
        status_row.addStretch(1)
        self.last_action_label = QLabel("")
        self.last_action_label.setObjectName("appStoreLastAction")
        muted_text(self.last_action_label)
        status_row.addWidget(self.last_action_label)
        self.cache_label = QLabel("")
        self.cache_label.setObjectName("appStoreCacheState")
        muted_text(self.cache_label)
        status_row.addWidget(self.cache_label)
        layout.addLayout(status_row)

        self.update_policy_note = QLabel(
            "Официальные приложения обновляются только вместе с полным системным обновлением Arch Linux. "
            "Для официального приложения отдельной кнопки «Обновить» нет. "
            "AUR-пакеты обновляются отдельно через yay; перед этим Arch Manager требует завершить "
            "ожидающее официальное системное обновление. Локальные пакеты показываются в магазине, "
            "но источник их обновлений Arch Manager не определяет автоматически."
        )
        self.update_policy_note.setObjectName("appStoreOfficialUpdatePolicyNote")
        self.update_policy_note.setWordWrap(True)
        muted_text(self.update_policy_note)
        self.update_policy_note.setVisible(False)
        layout.addWidget(self.update_policy_note)

        self.body_stack = QStackedWidget()
        self.body_stack.setObjectName("appStoreBody")
        self.body_stack.addWidget(self._build_loading_state())
        self.body_stack.addWidget(self._build_catalog_state())
        self.body_stack.addWidget(self._build_empty_state())
        self.body_stack.addWidget(self._build_error_state())
        layout.addWidget(self.body_stack, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filters)

        self._aur_search_timer = QTimer(self)
        self._aur_search_timer.setSingleShot(True)
        self._aur_search_timer.setInterval(AUR_SEARCH_DEBOUNCE_MS)
        self._aur_search_timer.timeout.connect(self._start_aur_search)

        self._system_package_timer = QTimer(self)
        self._system_package_timer.setSingleShot(True)
        self._system_package_timer.setInterval(SYSTEM_PACKAGE_SEARCH_DEBOUNCE_MS)
        self._system_package_timer.timeout.connect(self._start_system_package_search)

        self._set_filters_enabled(False)
        self._show_state(self.STATE_LOADING)

    def _build_loading_state(self) -> QWidget:
        state = QWidget()
        row = QHBoxLayout(state)
        row.setContentsMargins(0, 24, 0, 0)
        row.addStretch(1)
        self.loading_spinner = BusySpinner()
        row.addWidget(self.loading_spinner)
        label = QLabel("Загружаю каталог приложений…")
        row.addWidget(label)
        row.addStretch(1)
        return state

    def _build_catalog_state(self) -> QWidget:
        state = QWidget()
        state_layout = QVBoxLayout(state)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("appStoreScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.grid_host = QWidget()
        self.grid_host.setObjectName("appStoreGridHost")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(GRID_SPACING)
        self.grid.setVerticalSpacing(GRID_SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_append_batch)
        state_layout.addWidget(self.scroll, 1)

        self.catalog_system_updates_prompt = self._build_system_updates_prompt(
            "catalogSystemUpdatesPrompt"
        )
        state_layout.addWidget(self.catalog_system_updates_prompt)
        return state

    def _build_empty_state(self) -> QWidget:
        state = QWidget()
        layout = QVBoxLayout(state)
        layout.setContentsMargins(0, 38, 0, 0)
        layout.addStretch(1)
        title = QLabel("Ничего не найдено")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.empty_text = QLabel("Измените запрос или категорию.")
        self.empty_text.setWordWrap(True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        muted_text(self.empty_text)
        layout.addWidget(self.empty_text)

        self.empty_system_updates_prompt = self._build_system_updates_prompt(
            "emptySystemUpdatesPrompt"
        )
        layout.addWidget(self.empty_system_updates_prompt)
        layout.addStretch(2)
        return state

    def _build_system_updates_prompt(self, object_name: str) -> QWidget:
        prompt = QWidget()
        prompt.setObjectName(object_name)
        prompt_layout = QVBoxLayout(prompt)
        prompt_layout.setContentsMargins(0, 14, 0, 8)
        prompt_layout.setSpacing(8)

        label = QLabel("")
        label.setObjectName(f"{object_name}Label")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        muted_text(label)
        prompt_layout.addWidget(label)

        button = QPushButton("Перейти к системным обновлениям")
        button.setObjectName(f"{object_name}Button")
        button.setMinimumHeight(36)
        button.setMaximumWidth(280)
        button.clicked.connect(self.updates_requested.emit)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(button)
        button_row.addStretch(1)
        prompt_layout.addLayout(button_row)

        prompt._status_label = label
        prompt.setVisible(False)
        return prompt

    def _system_update_count(self) -> int:
        details = self._update_details
        official = getattr(details, "official", None) if details is not None else None
        if (
            official is None
            or not getattr(official, "available", False)
            or getattr(official, "error", None) is not None
        ):
            return 0
        return len(getattr(official, "items", ()) or ())

    @staticmethod
    def _system_updates_text(count: int, *, has_app_updates: bool) -> str:
        prefix = "Также доступны системные обновления" if has_app_updates else "Доступны системные обновления"
        return f"{prefix} ({count} шт.)"

    def _update_system_updates_prompts(self, *, app_update_count: int) -> None:
        system_count = self._system_update_count()
        visible = self._active_view() == "updates" and system_count > 0
        text = self._system_updates_text(
            system_count,
            has_app_updates=app_update_count > 0,
        )
        for prompt in (self.catalog_system_updates_prompt, self.empty_system_updates_prompt):
            prompt._status_label.setText(text if visible else "")
            prompt.setVisible(visible)

    def _build_error_state(self) -> QWidget:
        state = QWidget()
        layout = QVBoxLayout(state)
        layout.setContentsMargins(0, 38, 0, 0)
        layout.addStretch(1)
        title = QLabel("Каталог приложений недоступен")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setMaximumWidth(720)
        muted_text(self.error_label)
        error_row = QHBoxLayout()
        error_row.addStretch(1)
        error_row.addWidget(self.error_label)
        error_row.addStretch(1)
        layout.addLayout(error_row)
        retry = QPushButton("Повторить")
        retry.clicked.connect(lambda: self.reload_catalog(use_cache=False))
        retry_row = QHBoxLayout()
        retry_row.addStretch(1)
        retry_row.addWidget(retry)
        retry_row.addStretch(1)
        layout.addLayout(retry_row)
        layout.addStretch(2)
        return state

    @Slot()
    def _reload_requested(self) -> None:
        if self._active_view() == "system-packages":
            self._system_package_service.invalidate()
            self._queue_system_package_search(use_cache=False, immediate=True)
            return
        self.reload_catalog(use_cache=False)

    def ensure_loaded(self) -> None:
        if not self._loaded and not self._loading:
            self.reload_catalog(use_cache=True)

    def reload_catalog(self, *, use_cache: bool = True) -> None:
        if self._loading:
            return
        self._loading = True
        self._load_generation += 1
        generation = self._load_generation
        self.reload_button.setEnabled(False)
        self.reload_button.setText("Обновляю…")
        self._set_filters_enabled(False)
        self._show_state(self.STATE_LOADING)
        self.loading_spinner.start()
        self.count_label.setText("")
        self.cache_label.setText("")

        try:
            future = self.service.load_catalog_async(use_cache=use_cache)
        except Exception as exc:  # pragma: no cover - defensive service boundary
            LOGGER.exception("Unable to start application catalog load")
            self._signals.failed.emit(str(exc), generation)
            return
        future.add_done_callback(lambda completed, g=generation: self._future_done(completed, g))

    def _future_done(self, future: Future[CatalogLoadResult], generation: int) -> None:
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover - defensive background boundary
            LOGGER.exception("Application catalog load failed")
            self._signals.failed.emit(str(exc), generation)
        else:
            self._signals.loaded.emit(result, generation)

    @Slot(object, int)
    def _catalog_loaded(self, result: CatalogLoadResult, generation: int) -> None:
        if generation != self._load_generation:
            return
        self._loading = False
        self.loading_spinner.stop()
        self.reload_button.setEnabled(True)
        self.reload_button.setText("Обновить каталог")

        if not result.applications:
            self._loaded = False
            self._index.set_applications(())
            self._set_filters_enabled(False)
            if result.stats.read_errors:
                self._show_error(
                    "Не удалось прочитать локальные AppStream-метаданные. "
                    "Подробности записаны в журнал Arch Manager."
                )
            elif result.stats.metadata_files == 0:
                self._show_error(
                    "Локальные AppStream-метаданные не найдены. "
                    "Установите официальный пакет archlinux-appstream-data штатным системным способом "
                    "через обычное управление пакетами Arch Linux."
                )
            else:
                self._show_error("В локальных AppStream-данных не найдено доступных desktop-приложений.")
            return

        self._loaded = True
        self._update_details = current_update_details() or self._update_details
        applications = result.applications
        if (
            self._update_details is not None
            and getattr(self._update_details.official, "available", False)
            and getattr(self._update_details.official, "error", None) is None
        ):
            applications = ApplicationUpdateMapper.synchronize(
                applications,
                self._update_details.official.items,
            )
        self._index.set_applications(applications)
        self._populate_categories()
        self._set_filters_enabled(True)
        self.cache_label.setText("Из кэша" if result.cache_hit else "Каталог обновлён")
        self._refresh_last_action_label()
        if self._selected_sort() == "popularity":
            self._ensure_popularity_loaded(use_cache=True)
        self._apply_filters()
        if self._active_view() == "system-packages":
            self._queue_system_package_search(use_cache=result.cache_hit)
        elif self._active_view() in {"installed", "updates"}:
            self._queue_aur_installed(use_cache=result.cache_hit)
        elif self._active_view() == "catalog":
            # Local .pkg.tar.zst applications are classified from the same
            # foreign-package snapshot as AUR packages. Load it in background
            # even on the main catalog so locally installed desktop apps are
            # visible without switching to the Installed view first.
            if self._selected_source() in {"all", "local"}:
                self._queue_aur_installed(use_cache=result.cache_hit)
            self._queue_aur_search(use_cache=result.cache_hit)
            self._queue_system_package_search(use_cache=result.cache_hit)

    @Slot(str, int)
    def _catalog_failed(self, message: str, generation: int) -> None:
        if generation != self._load_generation:
            return
        self._loading = False
        self._loaded = False
        self.loading_spinner.stop()
        self.reload_button.setEnabled(True)
        self.reload_button.setText("Обновить каталог")
        self._set_filters_enabled(False)
        LOGGER.error("Application catalog unavailable: %s", message)
        self._show_error("Не удалось загрузить каталог. Подробности записаны в журнал Arch Manager.")

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.count_label.setText("")
        self.aur_status_label.setText("")
        self.popularity_status_label.setText("")
        self.cache_label.setText("")
        self._show_state(self.STATE_ERROR)

    def _populate_categories(self) -> None:
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("Все категории", None)
        available = set(self._index.available_categories())
        for key, label in CATEGORY_LABELS_RU.items():
            if key in available:
                self.category_combo.addItem(label, key)
        restore_index = self.category_combo.findData(current)
        self.category_combo.setCurrentIndex(max(0, restore_index))
        self.category_combo.blockSignals(False)
        self.sidebar_categories_changed.emit(
            tuple(
                (self.category_combo.itemData(index), self.category_combo.itemText(index))
                for index in range(1, self.category_combo.count())
            )
        )
        self._emit_sidebar_selection()

    def sidebar_categories(self) -> tuple[tuple[str, str], ...]:
        """Return currently available official catalog categories for the shell sidebar."""
        return tuple(
            (self.category_combo.itemData(index), self.category_combo.itemText(index))
            for index in range(1, self.category_combo.count())
            if isinstance(self.category_combo.itemData(index), str)
        )

    def select_sidebar_entry(self, entry: str, category: str | None = None) -> None:
        """Apply a selection coming from the contextual application sidebar."""
        if entry == "system-packages":
            self._view_switching = True
            try:
                self._system_packages_mode = True
                self.installed_button.setChecked(False)
                self.updates_button.setChecked(False)
            finally:
                self._view_switching = False
            self.category_combo.blockSignals(True)
            self.category_combo.setCurrentIndex(0)
            self.category_combo.blockSignals(False)
            self._system_package_timer.stop()
            self._aur_search_timer.stop()
            self._invalidate_aur_search(clear_results=False)
            self._sync_filter_controls()
            self._emit_sidebar_selection()
            if self._loaded:
                self._apply_filters()
                self._queue_system_package_search()
            return

        self._system_packages_mode = False
        if entry == "installed":
            self.installed_button.setChecked(True)
            return
        if entry == "updates":
            self.updates_button.setChecked(True)
            return
        if entry not in {"catalog", "popular", "category"}:
            return

        # Catalog/category/popular entries always leave special views first.
        self._view_switching = True
        try:
            self.installed_button.setChecked(False)
            self.updates_button.setChecked(False)
        finally:
            self._view_switching = False

        if entry in {"catalog", "popular"}:
            self._sidebar_catalog_entry = entry
            self._set_sort_value("popularity")
        elif entry == "category":
            self._sidebar_catalog_entry = "category"

        wanted = category if entry == "category" else None
        index = self.category_combo.findData(wanted)
        if index < 0:
            index = 0
        self.category_combo.blockSignals(True)
        self.category_combo.setCurrentIndex(index)
        self.category_combo.blockSignals(False)
        self._sync_filter_controls()
        # The sidebar is the visible category control now, so notify the shell
        # immediately after applying its selection.
        self._emit_sidebar_selection()
        if self._loaded:
            if self._selected_sort() == "popularity":
                self._ensure_popularity_loaded()
            self._apply_filters()
            self._queue_aur_search()
            self._queue_system_package_search()

    def sidebar_selection(self) -> tuple[str, str | None]:
        """Return the contextual sidebar item represented by the current page state."""
        view = self._active_view()
        if view in {"installed", "updates", "system-packages"}:
            return view, None
        category = self.category_combo.currentData()
        if isinstance(category, str):
            return "category", category
        if self._sidebar_catalog_entry == "popular" and self._selected_sort() == "popularity":
            return "popular", None
        return "catalog", None

    def _emit_sidebar_selection(self) -> None:
        entry, category = self.sidebar_selection()
        self.sidebar_selection_changed.emit(entry, category)

    def _set_filters_enabled(self, enabled: bool) -> None:
        self._filters_enabled = enabled
        self.search_edit.setEnabled(enabled)
        self.installed_button.setEnabled(enabled)
        self.updates_button.setEnabled(enabled)
        self.sort_combo.setEnabled(enabled)
        self._sync_filter_controls()

    def _sync_filter_controls(self) -> None:
        view = self._active_view()
        source = self._selected_source()
        system_mode = view == "system-packages"

        self.source_combo.setVisible(not system_mode)
        self.sort_combo.setVisible(not system_mode)
        self.search_edit.setPlaceholderText(
            "Поиск системных пакетов…" if system_mode else "Поиск приложений…"
        )
        self.reload_button.setText("Обновить пакеты" if system_mode else "Обновить каталог")

        # Stage 7.3.2 contract was: source_selectable = view in {"catalog", "installed"}
        # Stage 7.5 intentionally adds the updates view to source selection.
        source_selectable = view in {"catalog", "installed", "updates"}
        self.source_combo.setEnabled(self._filters_enabled and source_selectable)
        self.sort_combo.setEnabled(self._filters_enabled and not system_mode)
        self.category_combo.setEnabled(
            self._filters_enabled
            and not system_mode
            and (
                (view == "catalog" and source != "aur")
                or (view == "installed" and source == "official")
            )
        )
        if system_mode:
            self.source_combo.setToolTip("")
        elif view == "catalog":
            self.source_combo.setToolTip("Источник приложений: все, официальные, AUR или локальные")
        elif view == "installed":
            self.source_combo.setToolTip("Источник установленных приложений: все, официальные, AUR или локальные")
        else:
            self.source_combo.setToolTip("Источник обновлений: все, официальные или AUR; локальные пакеты показываются отдельно")

    def _set_source_value(self, source: str) -> None:
        index = self.source_combo.findData(source)
        if index < 0 or index == self.source_combo.currentIndex():
            return
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)

    def _selected_source(self) -> str:
        value = self.source_combo.currentData()
        return value if value in {"all", "official", "aur", "local"} else "all"

    def _set_sort_value(self, sort_mode: str) -> None:
        index = self.sort_combo.findData(sort_mode)
        if index < 0 or index == self.sort_combo.currentIndex():
            return
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentIndex(index)
        self.sort_combo.blockSignals(False)

    def _selected_sort(self) -> str:
        value = self.sort_combo.currentData()
        return value if value in {"name", "popularity"} else "name"

    @Slot()
    def _sort_changed(self, *_args) -> None:
        # «Популярные» — навигационный ярлык на сортировку по популярности.
        # Если в нём вручную выбрать другую сортировку, возвращаем состояние
        # обычного каталога. Но включение популярности в «Все приложения» не
        # должно самовольно переносить выделение на пункт «Популярные».
        if self._selected_sort() == "popularity":
            self._ensure_popularity_loaded()
        elif self._sidebar_catalog_entry == "popular":
            self._sidebar_catalog_entry = "catalog"
        self._emit_sidebar_selection()
        self._update_popularity_status()
        if self._loaded:
            self._apply_filters()

    def _ensure_popularity_loaded(self, *, use_cache: bool = True) -> None:
        if self._selected_sort() != "popularity":
            return
        if self._popularity_loaded or self._popularity_loading:
            self._update_popularity_status()
            return
        self._popularity_generation += 1
        generation = self._popularity_generation
        self._popularity_loading = True
        self._popularity_error = None
        self._update_popularity_status()
        try:
            future = self._popularity_service.load_async(use_cache=use_cache)
        except Exception as exc:  # pragma: no cover - defensive service boundary
            self._popularity_signals.failed.emit(exc, generation)
            return
        future.add_done_callback(
            lambda completed, g=generation: self._popularity_future_done(completed, g)
        )

    def _popularity_future_done(self, future: Future, generation: int) -> None:
        try:
            snapshot = future.result()
        except Exception as exc:  # pragma: no cover - defensive background boundary
            self._popularity_signals.failed.emit(exc, generation)
        else:
            self._popularity_signals.loaded.emit(snapshot, generation)

    @Slot(object, int)
    def _popularity_loaded_result(self, snapshot: object, generation: int) -> None:
        if generation != self._popularity_generation:
            return
        self._popularity_loading = False
        self._popularity_error = None
        if not isinstance(snapshot, PopularitySnapshot):
            self._popularity_loaded = False
            self._popularity_error = RuntimeError("Некорректные данные популярности")
        else:
            self._popularity_scores = dict(snapshot.scores)
            self._popularity_counts = dict(snapshot.counts)
            self._popularity_loaded = True
            self._popularity_stale = bool(snapshot.stale_cache)
        self._update_popularity_status()
        if self._loaded and self._selected_sort() == "popularity":
            self._apply_filters()

    @Slot(object, int)
    def _popularity_failed(self, error: object, generation: int) -> None:
        if generation != self._popularity_generation:
            return
        self._popularity_loading = False
        self._popularity_loaded = False
        self._popularity_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        LOGGER.warning("Unable to load pkgstats popularity: %s", error)
        self._update_popularity_status()
        if self._loaded and self._selected_sort() == "popularity":
            self._apply_filters()

    def _update_popularity_status(self) -> None:
        if self._active_view() == "system-packages":
            self.popularity_status_label.setText("")
            return
        if self._selected_sort() != "popularity":
            self.popularity_status_label.setText("")
        elif self._popularity_loading:
            self.popularity_status_label.setText("Популярность: загружаю…")
        elif self._popularity_loaded and self._popularity_stale:
            self.popularity_status_label.setText("Популярность: кэш (офлайн)")
        elif self._popularity_loaded:
            self.popularity_status_label.setText("Популярность: pkgstats")
        elif self._popularity_error is not None:
            if self._selected_source() == "aur":
                self.popularity_status_label.setText("pkgstats недоступен · рейтинг AUR")
            else:
                self.popularity_status_label.setText("Популярность недоступна · по названию")
        else:
            self.popularity_status_label.setText("")
        self._sync_activity_spinner()

    def _pkgstats_score(self, item: Application | AurPackage) -> tuple[float, int]:
        if isinstance(item, Application):
            names = tuple(dict.fromkeys((item.package_name, *item.package_names)))
        else:
            names = (item.name,)
        best_score = 0.0
        best_count = 0
        for name in names:
            score = float(self._popularity_scores.get(name, 0.0))
            count = int(self._popularity_counts.get(name, 0))
            if score > best_score or (score == best_score and count > best_count):
                best_score = score
                best_count = count
        return best_score, best_count

    @staticmethod
    def _name_sort_key(item: Application | AurPackage) -> tuple[str, int, str]:
        return (
            item.name.casefold(),
            0 if isinstance(item, Application) else 1,
            (item.package_name if isinstance(item, Application) else item.name).casefold(),
        )

    def _exact_name_match_rank(self, item: Application | AurPackage) -> int:
        if isinstance(item, Application):
            candidates = (item.name, item.package_name, *item.package_names)
        else:
            candidates = (item.name, item.package_base)
        return search_candidate_rank(candidates, self.search_edit.text())

    @staticmethod
    def _source_priority(item: Application | AurPackage) -> int:
        # Official Application objects first, then AUR, then local packages.
        if isinstance(item, AurPackage):
            return 1
        if item.repository == "Локальный пакет":
            return 2
        return 0

    def _sort_results(
        self,
        items: tuple[Application | AurPackage, ...],
    ) -> tuple[Application | AurPackage, ...]:
        if self._selected_sort() != "popularity":
            return tuple(
                sorted(
                    items,
                    key=lambda item: (
                        self._exact_name_match_rank(item),
                        self._source_priority(item),
                        *self._name_sort_key(item),
                    ),
                )
            )

        # Relevance and source safety come before popularity. This prevents a
        # popular AUR variant from hiding an exact official package match.
        if self._popularity_scores:
            def popularity_key(item: Application | AurPackage):
                score, count = self._pkgstats_score(item)
                aur_popularity = item.popularity if isinstance(item, AurPackage) else 0.0
                aur_votes = item.votes if isinstance(item, AurPackage) else 0
                return (
                    self._exact_name_match_rank(item),
                    self._source_priority(item),
                    -score,
                    -count,
                    -aur_popularity,
                    -aur_votes,
                    *self._name_sort_key(item),
                )

            return tuple(sorted(items, key=popularity_key))

        if items and all(isinstance(item, AurPackage) for item in items):
            return tuple(
                sorted(
                    items,
                    key=lambda item: (
                        self._exact_name_match_rank(item),
                        -item.popularity,
                        -item.votes,
                        *self._name_sort_key(item),
                    ),
                )
            )
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    self._exact_name_match_rank(item),
                    self._source_priority(item),
                    *self._name_sort_key(item),
                ),
            )
        )

    @Slot(str)
    def _search_changed(self, text: str) -> None:
        if not self._loaded:
            return
        if self._active_view() == "system-packages":
            self._search_timer.stop()
            self._aur_search_timer.stop()
            term = text.strip()
            if len(term) < 2:
                self._clear_system_package_search()
                self._apply_filters()
                return
            self._queue_system_package_search()
            return
        if self._active_view() == "installed":
            self._search_timer.stop()
            self._aur_search_timer.stop()
            self._clear_system_package_search()
            self._apply_filters()
            return
        term = text.strip()
        if not term:
            self._search_timer.stop()
            self._aur_search_timer.stop()
            self._invalidate_aur_search(clear_results=True)
            self._clear_system_package_search()
            self._apply_filters()
            return
        self._search_timer.start()
        self._queue_aur_search()
        self._queue_system_package_search()

    @Slot()
    def _source_changed(self, *_args) -> None:
        if not self._loaded:
            self._sync_filter_controls()
            return
        if self._active_view() == "system-packages":
            return
        source = self._selected_source()
        view = self._active_view()
        category_not_applicable = source == "aur" or (view == "installed" and source != "official")
        if category_not_applicable and self.category_combo.currentData() is not None:
            self.category_combo.blockSignals(True)
            self.category_combo.setCurrentIndex(0)
            self.category_combo.blockSignals(False)
        self._sync_filter_controls()
        self._emit_sidebar_selection()
        self._update_popularity_status()
        self._apply_filters()
        if view in {"installed", "updates"}:
            self._clear_system_package_search()
            self._queue_aur_installed()
        else:
            if source in {"all", "local"}:
                self._queue_aur_installed()
            self._queue_aur_search()
            self._queue_system_package_search()

    def _view_changed(self, view: str, checked: bool) -> None:
        if self._view_switching:
            return
        self._view_switching = True
        try:
            if checked:
                self._system_packages_mode = False
            if checked and view == "installed":
                self.updates_button.setChecked(False)
            elif checked and view == "updates":
                self.installed_button.setChecked(False)

            active_view = self._active_view()
            # Stage 7.2/7.3 historical behavior forced updates to official:
            # self._set_source_value("official")
            if active_view == "installed" and self._selected_source() != "official":
                if self.category_combo.currentData() is not None:
                    self.category_combo.blockSignals(True)
                    self.category_combo.setCurrentIndex(0)
                    self.category_combo.blockSignals(False)
        finally:
            self._view_switching = False
        self._system_package_timer.stop()
        self._sync_filter_controls()
        self._emit_sidebar_selection()
        if self._loaded:
            if self._active_view() != "catalog":
                self._aur_search_timer.stop()
                self._invalidate_aur_search(clear_results=False)
                if self._active_view() != "system-packages":
                    self._clear_system_package_search()
            self._apply_filters()
            if self._active_view() == "catalog":
                self._queue_aur_search()
                self._queue_system_package_search()
            elif self._active_view() in {"installed", "updates"}:
                self._queue_aur_installed()

    def _active_view(self) -> str:
        if self._system_packages_mode:
            return "system-packages"
        if self.updates_button.isChecked():
            return "updates"
        if self.installed_button.isChecked():
            return "installed"
        return "catalog"

    @Slot()
    def _filters_changed(self, *_args) -> None:
        self._emit_sidebar_selection()
        if self._loaded:
            self._apply_filters()
            if self._active_view() == "catalog":
                self._queue_aur_search()
                self._queue_system_package_search()

    def _system_package_search_allowed(self, query: str | None = None) -> bool:
        term = self.search_edit.text().strip() if query is None else str(query).strip()
        if len(term) < 2:
            return False
        view = self._active_view()
        if view == "system-packages":
            return True
        return (
            view == "catalog"
            and self._selected_source() in {"all", "official"}
            and self.category_combo.currentData() is None
        )

    def _clear_system_package_search(self, *, clear_results: bool = True) -> None:
        self._system_package_timer.stop()
        self._system_package_generation += 1
        self._system_package_loading = False
        self._system_package_error = None
        if clear_results:
            self._system_package_results = ()
            self._system_package_query = ""
        self._sync_activity_spinner()

    def _queue_system_package_search(
        self,
        *,
        use_cache: bool = True,
        immediate: bool = False,
    ) -> None:
        query = self.search_edit.text().strip()
        if not self._system_package_search_allowed(query):
            self._clear_system_package_search()
            if self._active_view() == "system-packages":
                self._apply_filters()
            return

        self._system_package_next_use_cache = use_cache
        self._system_package_loading = True
        self._system_package_error = None
        self._system_package_query = query
        self._system_package_results = ()
        self._apply_filters()
        if immediate:
            self._system_package_timer.stop()
            self._start_system_package_search()
        else:
            self._system_package_timer.start()

    @Slot()
    def _start_system_package_search(self) -> None:
        query = self.search_edit.text().strip()
        if not self._system_package_search_allowed(query):
            return
        self._system_package_generation += 1
        generation = self._system_package_generation
        self._system_package_loading = True
        self._system_package_error = None
        self._system_package_query = query
        self._system_package_results = ()
        self._apply_filters()
        try:
            future = self._system_package_service.search_async(
                query,
                use_cache=self._system_package_next_use_cache,
            )
        except Exception as exc:  # pragma: no cover - defensive service boundary
            self._system_package_signals.failed.emit(exc, query, generation)
            return
        future.add_done_callback(
            lambda completed, q=query, g=generation: self._system_package_future_done(
                completed, q, g
            )
        )

    def _system_package_future_done(
        self, future: Future, query: str, generation: int
    ) -> None:
        try:
            applications = tuple(future.result())
        except Exception as exc:  # pragma: no cover - defensive background boundary
            self._system_package_signals.failed.emit(exc, query, generation)
        else:
            self._system_package_signals.loaded.emit(applications, query, generation)

    @Slot(object, str, int)
    def _system_package_search_loaded(
        self, applications: object, query: str, generation: int
    ) -> None:
        if (
            generation != self._system_package_generation
            or not self._system_package_search_allowed(query)
            or query != self.search_edit.text().strip()
        ):
            return
        self._system_package_loading = False
        self._system_package_error = None
        self._system_package_query = query
        self._system_package_results = tuple(
            item for item in (applications or ()) if isinstance(item, Application)
        )
        self._apply_filters()

    @Slot(object, str, int)
    def _system_package_search_failed(
        self, error: object, query: str, generation: int
    ) -> None:
        if (
            generation != self._system_package_generation
            or not self._system_package_search_allowed(query)
            or query != self.search_edit.text().strip()
        ):
            return
        self._system_package_loading = False
        self._system_package_results = ()
        self._system_package_query = query
        self._system_package_error = (
            error if isinstance(error, Exception) else RuntimeError(str(error))
        )
        LOGGER.warning("System package search failed for %r: %s", query, error)
        self._apply_filters()

    def _update_system_package_status(self) -> None:
        query = self.search_edit.text().strip()
        if len(query) < 2:
            self.aur_status_label.setText("Системные пакеты: введите минимум 2 символа")
        elif self._system_package_loading:
            self.aur_status_label.setText("Системные пакеты: поиск…")
        elif self._system_package_error is not None:
            self.aur_status_label.setText("Системные пакеты: поиск недоступен")
        elif self._system_package_query == query:
            self.aur_status_label.setText(
                f"Системные пакеты: найдено {len(self._system_package_results)}"
            )
        else:
            self.aur_status_label.setText("")
        self._sync_activity_spinner()

    def _system_package_empty_text(self) -> str:
        query = self.search_edit.text().strip()
        if len(query) < 2:
            return "Введите минимум 2 символа для поиска системных пакетов."
        if self._system_package_loading:
            return "Ищу пакеты в локальных системных базах…"
        if self._system_package_error is not None:
            return "Не удалось прочитать локальные системные базы пакетов."
        return "Системных пакетов по этому запросу не найдено."

    def _system_package_results_for_current_query(self) -> tuple[Application, ...]:
        query = self.search_edit.text().strip()
        if (
            self._active_view() == "catalog"
            and self._system_package_search_allowed(query)
            and self._system_package_query == query
        ):
            return self._system_package_results
        return ()

    @staticmethod
    def _merge_official_search_results(
        catalog_items: tuple[Application, ...],
        package_items: tuple[Application, ...],
    ) -> tuple[Application, ...]:
        # Keep AppStream cards and add only official packages not represented there.
        def package_keys(item: Application) -> set[str]:
            return {
                name.casefold()
                for name in (item.package_name, *item.package_names)
                if str(name or "").strip()
            }

        merged = list(catalog_items)
        seen_packages: set[str] = set()
        for item in catalog_items:
            seen_packages.update(package_keys(item))

        for item in package_items:
            keys = package_keys(item)
            if keys and keys & seen_packages:
                continue
            merged.append(item)
            seen_packages.update(keys)
        return tuple(merged)

    def _aur_installed_allowed(self) -> bool:
        if self._aur_service is None:
            return False
        view = self._active_view()
        source = self._selected_source()
        if view == "catalog":
            return source in {"all", "local"}
        if view in {"installed", "updates"}:
            return source in {"all", "aur", "local"}
        return False

    def _queue_aur_installed(self, *, use_cache: bool = True) -> None:
        if not self._aur_installed_allowed() or self._aur_service is None:
            self._update_aur_status()
            return
        self._aur_installed_generation += 1
        generation = self._aur_installed_generation
        self._aur_installed_loading = True
        self._aur_installed_error = None
        self._update_aur_status()
        self._apply_filters()
        try:
            future = self._aur_service.installed_async(use_cache=use_cache)
        except Exception as exc:  # pragma: no cover - defensive service boundary
            self._aur_installed_signals.failed.emit(exc, generation)
            return
        future.add_done_callback(
            lambda completed, g=generation: self._aur_installed_future_done(completed, g)
        )

    def _aur_installed_future_done(self, future: Future, generation: int) -> None:
        try:
            snapshot = future.result()
            foreign_unknown = getattr(snapshot, "foreign_unknown", ()) or ()
            local_applications = self._local_reader.build(foreign_unknown)
        except Exception as exc:  # pragma: no cover - defensive background boundary
            self._aur_installed_signals.failed.emit(exc, generation)
        else:
            self._aur_installed_signals.loaded.emit((snapshot, local_applications), generation)

    @Slot(object, int)
    def _aur_installed_loaded(self, payload: object, generation: int) -> None:
        if generation != self._aur_installed_generation:
            return
        self._aur_installed_loading = False
        self._aur_installed_error = None

        local_applications: tuple[Application, ...] = ()
        snapshot = payload
        if isinstance(payload, tuple) and len(payload) == 2:
            snapshot, local_payload = payload
            local_applications = tuple(
                item for item in (local_payload or ()) if isinstance(item, Application) and item.installed
            )

        if isinstance(snapshot, AurInstalledSnapshot):
            packages = snapshot.aur_packages
        else:
            packages = getattr(snapshot, "aur_packages", ()) or ()
        self._aur_installed_results = tuple(
            item for item in packages if isinstance(item, AurPackage) and item.installed
        )
        self._local_installed_results = local_applications
        self._local_index.set_applications(local_applications)
        self._apply_filters()

    @Slot(object, int)
    def _aur_installed_failed(self, error: object, generation: int) -> None:
        if generation != self._aur_installed_generation:
            return
        self._aur_installed_loading = False
        self._aur_installed_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        LOGGER.warning("Unable to load installed AUR packages: %s", error)
        self._apply_filters()

    def _aur_search_allowed(self) -> bool:
        return (
            self._aur_service is not None
            and self._active_view() == "catalog"
            and self._selected_source() in {"all", "aur"}
            and self.category_combo.currentData() is None
            and len(self.search_edit.text().strip()) >= 2
        )

    def _queue_aur_search(self, *, use_cache: bool = True) -> None:
        self._aur_next_use_cache = use_cache
        if not self._aur_search_allowed():
            self._aur_search_timer.stop()
            if (
                self._active_view() != "catalog"
                or self._selected_source() == "official"
                or self.category_combo.currentData() is not None
                or len(self.search_edit.text().strip()) < 2
            ):
                self._invalidate_aur_search(clear_results=True)
                self._update_aur_status()
            return
        self._aur_search_timer.start()
        self._update_aur_status(queued=True)

    def _invalidate_aur_search(self, *, clear_results: bool) -> None:
        self._aur_generation += 1
        self._aur_loading = False
        self._aur_error = None
        if clear_results:
            self._aur_results = ()
            self._aur_result_query = ""

    @Slot()
    def _start_aur_search(self) -> None:
        if not self._aur_search_allowed() or self._aur_service is None:
            return
        query = self.search_edit.text().strip()
        self._aur_generation += 1
        generation = self._aur_generation
        self._aur_loading = True
        self._aur_error = None
        self._aur_results = ()
        self._aur_result_query = query
        self._update_aur_status()
        self._apply_filters()
        try:
            future = self._aur_service.search_async(
                query,
                use_cache=self._aur_next_use_cache,
            )
        except Exception as exc:  # pragma: no cover - defensive service boundary
            self._aur_signals.failed.emit(exc, query, generation)
            return
        future.add_done_callback(
            lambda completed, q=query, g=generation: self._aur_future_done(completed, q, g)
        )

    def _aur_future_done(self, future: Future, query: str, generation: int) -> None:
        try:
            packages = tuple(future.result())
        except Exception as exc:  # pragma: no cover - defensive background boundary
            self._aur_signals.failed.emit(exc, query, generation)
        else:
            self._aur_signals.loaded.emit(packages, query, generation)

    @Slot(object, str, int)
    def _aur_search_loaded(self, packages: object, query: str, generation: int) -> None:
        if generation != self._aur_generation or query != self.search_edit.text().strip():
            return
        self._aur_loading = False
        self._aur_error = None
        self._aur_result_query = query
        valid_packages = tuple(
            item for item in (packages or ()) if isinstance(item, AurPackage)
        )
        self._aur_results = prioritize_exact_aur_match(valid_packages, query)
        self._apply_filters()

    @Slot(object, str, int)
    def _aur_search_failed(self, error: object, query: str, generation: int) -> None:
        if generation != self._aur_generation or query != self.search_edit.text().strip():
            return
        self._aur_loading = False
        self._aur_results = ()
        self._aur_result_query = query
        self._aur_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        LOGGER.warning("AUR search failed for %r: %s", query, error)
        self._apply_filters()

    @staticmethod
    def _friendly_aur_error(error: Exception | None) -> str:
        if isinstance(error, AurNetworkError):
            return "AUR: нет связи"
        if isinstance(error, (AurInvalidResponse, AurRpcError)):
            return "AUR: ошибка сервиса"
        if error is not None:
            return "AUR: поиск недоступен"
        return ""

    def _catalog_system_package_status(self) -> str:
        query = self.search_edit.text().strip()
        if (
            self._active_view() == "catalog"
            and self._system_package_search_allowed(query)
            and self._system_package_query == query
            and self._system_package_error is not None
        ):
            return "Официальные пакеты: поиск недоступен"
        return ""

    def _update_aur_status(self, *, queued: bool = False) -> None:
        if self._active_view() == "system-packages":
            return
        view = self._active_view()
        source = self._selected_source()
        system_status = self._catalog_system_package_status()
        if self._aur_service is None:
            self.aur_status_label.setText(system_status)
            self._sync_activity_spinner()
            return
        if source == "local":
            if self._aur_installed_loading and not self._local_installed_results:
                self.aur_status_label.setText("Локальные: определяю…")
            elif self._aur_installed_error is not None and not self._local_installed_results:
                self.aur_status_label.setText("Локальные: недоступны")
            elif view == "updates":
                self.aur_status_label.setText("Локальные: обновления не проверяются")
            else:
                self.aur_status_label.setText(f"Локальные: {len(self._local_installed_results)}")
            self._sync_activity_spinner()
            return
        if view in {"installed", "updates"}:
            if source == "official":
                self.aur_status_label.setText("")
            elif self._aur_installed_loading:
                self.aur_status_label.setText("AUR: загружаю установленные…")
            elif self._aur_installed_error is not None:
                self.aur_status_label.setText(self._friendly_aur_error(self._aur_installed_error))
            else:
                if view == "updates":
                    count = sum(1 for item in self._aur_installed_results if item.update_available)
                    self.aur_status_label.setText(f"AUR: обновлений {count}")
                else:
                    self.aur_status_label.setText(f"AUR: установлено {len(self._aur_installed_results)}")
            self._sync_activity_spinner()
            return
        if view != "catalog":
            self.aur_status_label.setText("")
            self._sync_activity_spinner()
            return
        term = self.search_edit.text().strip()
        aur_status = ""
        if source == "official" or self.category_combo.currentData() is not None:
            aur_status = ""
        elif len(term) < 2:
            aur_status = "AUR: введите минимум 2 символа"
        elif self._aur_loading:
            aur_status = "AUR: поиск…"
        elif self._aur_error is not None:
            aur_status = self._friendly_aur_error(self._aur_error)
        elif queued:
            aur_status = "AUR: ожидает поиска…"
        elif self._aur_result_query == term:
            aur_status = f"AUR: {len(self._aur_results)}"
        self.aur_status_label.setText(
            " · ".join(part for part in (system_status, aur_status) if part)
        )
        self._sync_activity_spinner()

    def _aur_results_for_current_query(self) -> tuple[AurPackage, ...]:
        query = self.search_edit.text().strip()
        if (
            self._active_view() == "catalog"
            and self._selected_source() in {"all", "aur"}
            and self.category_combo.currentData() is None
            and self._aur_result_query == query
            and len(query) >= 2
        ):
            return self._aur_results
        return ()

    def _catalog_empty_text(self) -> str:
        source = self._selected_source()
        query = self.search_edit.text().strip()
        if source == "local":
            if self._aur_installed_loading and not self._local_installed_results:
                return "Определяю локально установленные приложения…"
            if self._aur_installed_error is not None and not self._local_installed_results:
                return "Не удалось определить локально установленные приложения."
            return "Локальных приложений по этому запросу не найдено."
        if source == "aur":
            if len(query) < 2:
                return "Введите минимум 2 символа для поиска в AUR."
            if self._aur_loading:
                return "Ищу пакеты в AUR…"
            if self._aur_error is not None:
                return "Не удалось выполнить поиск AUR. Официальный каталог остаётся доступен."
            return "В AUR по этому запросу ничего не найдено."

        official_package_search = (
            source in {"all", "official"}
            and len(query) >= 2
            and self.category_combo.currentData() is None
        )
        if official_package_search and self._system_package_loading:
            return "Ищу совпадения среди официальных пакетов Arch Linux…"
        if (
            source == "official"
            and official_package_search
            and self._system_package_error is not None
        ):
            return "В каталоге приложений совпадений нет; поиск официальных пакетов сейчас недоступен."
        if (
            source == "all"
            and official_package_search
            and self._system_package_error is not None
        ):
            if self._aur_loading:
                return "Поиск официальных пакетов недоступен. Поиск AUR ещё выполняется…"
            if self._aur_error is not None:
                return "Поиск официальных пакетов и AUR сейчас недоступен."
            return "Поиск официальных пакетов сейчас недоступен; в доступных источниках совпадений нет."
        if source == "all" and self._aur_loading:
            return "В официальных источниках совпадений пока нет. Поиск AUR ещё выполняется…"
        if source == "all" and self._aur_error is not None:
            return "В официальных источниках совпадений нет; поиск AUR сейчас недоступен."
        if source == "official" and official_package_search:
            return "В официальном каталоге и пакетах Arch Linux совпадений нет."
        return "Измените запрос или категорию."

    @Slot()
    def _apply_filters(self) -> None:
        self._sync_activity_spinner()
        view = self._active_view()
        if view == "system-packages":
            term = self.search_edit.text().strip()
            self.update_policy_note.setVisible(False)
            self._filtered = (
                self._system_package_results
                if len(term) >= 2 and self._system_package_query == term
                else ()
            )
            self.empty_text.setText(self._system_package_empty_text())
            self._update_system_package_status()
            self.popularity_status_label.setText("")
            self.aur_update_all_button.setVisible(False)
            self.aur_update_all_button.setEnabled(False)
            self._reset_cards()
            total = len(self._filtered)
            self.count_label.setText(self._package_count_text(total) if total else "")
            self._update_system_updates_prompts(app_update_count=0)
            if total == 0:
                self._show_state(self.STATE_EMPTY)
                return
            self._show_state(self.STATE_CATALOG)
            self._append_batch(INITIAL_BATCH_SIZE)
            QTimer.singleShot(0, self._maybe_fill_viewport)
            return

        query = CatalogQuery(
            text=self.search_edit.text(),
            category=self.category_combo.currentData(),
            installed_only=False,
        )
        base = self._index.filter(query)
        local_base = self._local_index.filter(query)
        self.update_policy_note.setVisible(view == "updates")
        if view == "installed":
            source = self._selected_source()
            official_installed = (
                InstalledApplications.filter(base) if source in {"all", "official"} else ()
            )
            aur_installed = (
                filter_installed_aur_packages(
                    self._aur_installed_results,
                    self.search_edit.text(),
                )
                if source in {"all", "aur"}
                else ()
            )
            local_installed = (
                InstalledApplications.filter(local_base)
                if source in {"all", "local"}
                else ()
            )
            self._filtered = merge_store_results(
                official_installed,
                aur_installed,
                source=source,
                local=local_installed,
            )
            if source in {"all", "aur", "local"} and self._aur_installed_loading and not self._filtered:
                self.empty_text.setText("Загружаю список установленных приложений…")
            elif source in {"all", "aur", "local"} and self._aur_installed_error is not None and not self._filtered:
                self.empty_text.setText("Не удалось получить список сторонних установленных приложений.")
            else:
                self.empty_text.setText("Установленных приложений по текущему фильтру не найдено.")
        elif view == "updates":
            source = self._selected_source()
            official_items = (
                self._update_details.official.items
                if self._update_details is not None
                else None
            )
            official_updates = (
                ApplicationUpdateMapper.map_updates(base, official_items)
                if source in {"all", "official"}
                else ()
            )
            aur_updates = (
                tuple(item for item in self._aur_installed_results if item.update_available)
                if source in {"all", "aur"}
                else ()
            )
            self._filtered = merge_store_results(
                official_updates,
                aur_updates,
                source=source,
                local=(),
            )
            if source in {"all", "aur"} and self._aur_installed_loading and not self._filtered:
                self.empty_text.setText("Проверяю установленные AUR-пакеты…")
            elif source in {"all", "aur"} and self._aur_installed_error is not None and not self._filtered:
                self.empty_text.setText("Не удалось получить AUR-обновления.")
            else:
                self.empty_text.setText("Обновлений приложений по текущему фильтру сейчас нет.")
        else:
            source = self._selected_source()
            aur = self._aur_results_for_current_query()
            system_packages = self._system_package_results_for_current_query()
            official = (
                self._merge_official_search_results(base, system_packages)
                if source in {"all", "official"}
                else ()
            )
            local = local_base if source in {"all", "local"} else ()
            self._filtered = merge_store_results(official, aur, source=source, local=local)
            self.empty_text.setText(self._catalog_empty_text())

        self._filtered = self._sort_results(self._filtered)
        self._update_aur_status()
        self._update_popularity_status()
        aur_update_count = sum(1 for item in self._aur_installed_results if item.update_available)
        self.aur_update_all_button.setVisible(
            view == "updates" and self._selected_source() in {"all", "aur"}
        )
        self.aur_update_all_button.setEnabled(
            aur_update_count > 0 and not self._aur_update_all_busy
        )
        self.aur_update_all_button.setText(
            f"Обновить все AUR ({aur_update_count})" if aur_update_count else "Обновить все AUR"
        )
        self._reset_cards()
        total = len(self._filtered)
        self.count_label.setText(self._count_text(total))
        self._update_system_updates_prompts(app_update_count=total if view == "updates" else 0)
        if total == 0:
            self._show_state(self.STATE_EMPTY)
            return
        self._show_state(self.STATE_CATALOG)
        self._append_batch(INITIAL_BATCH_SIZE)
        QTimer.singleShot(0, self._maybe_fill_viewport)

    @staticmethod
    def _count_text(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            noun = "приложение"
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            noun = "приложения"
        else:
            noun = "приложений"
        return f"{count} {noun}"

    @staticmethod
    def _package_count_text(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            noun = "пакет"
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            noun = "пакета"
        else:
            noun = "пакетов"
        return f"{count} {noun}"

    def _reset_cards(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        self._render_cursor = 0
        self._card_render_errors = 0
        self.scroll.verticalScrollBar().setValue(0)

    def _append_batch(self, count: int = NEXT_BATCH_SIZE) -> None:
        start = self._render_cursor
        stop = min(start + max(1, count), len(self._filtered))
        if start >= stop:
            return
        # Advance the cursor independently from successfully rendered cards.
        # A single malformed remote AUR entry must never make lazy loading retry
        # the same item forever or crash the whole application page.
        self._render_cursor = stop
        for item in self._filtered[start:stop]:
            try:
                if isinstance(item, Application):
                    card = ApplicationCard(
                        item,
                        self.grid_host,
                        preferred_category=self.category_combo.currentData(),
                    )
                elif isinstance(item, AurPackage):
                    card = AurPackageCard(item, self.grid_host)
                else:  # pragma: no cover - presentation boundary guard
                    continue
            except Exception:
                self._card_render_errors += 1
                LOGGER.exception(
                    "Unable to render application-store card for %s",
                    getattr(item, "name", "<unknown>"),
                )
                continue
            card.activated.connect(self._open_application_details)
            self._cards.append(card)
        if self._cards:
            self._relayout_cards()

    @Slot(object)
    def _open_application_details(self, item: object) -> None:
        if isinstance(item, AurPackage):
            dialog = AurPackageDetailsDialog(
                item,
                self,
                aur_service=self._aur_service,
            )
            dialog.updates_requested.connect(self.updates_requested.emit)
            dialog.exec()
            if dialog.package_state_changed:
                self._refresh_last_action_label()
                self.package_state_changed.emit()
                if self._active_view() == "installed":
                    self._queue_aur_installed(use_cache=False)
                else:
                    self._queue_aur_search(use_cache=False)
            return
        if not isinstance(item, Application):
            return
        dialog = ApplicationDetailsDialog(
            item,
            self,
            media_cache=self._media_cache,
            action_service=self._package_actions,
        )
        dialog.updates_requested.connect(self.updates_requested.emit)
        dialog.exec()
        if dialog.package_state_changed:
            self._refresh_last_action_label()
            self.package_state_changed.emit()
            if self._active_view() == "system-packages":
                self._system_package_service.invalidate()
                self._queue_system_package_search(use_cache=False, immediate=True)
            else:
                self.reload_catalog(use_cache=False)

    def apply_update_details(self, details: object) -> None:
        """Apply the shared update result without running another check."""

        self._update_details = details
        if self._loaded:
            official = getattr(details, "official", None)
            if (
                official is not None
                and getattr(official, "available", False)
                and getattr(official, "error", None) is None
            ):
                self._index.set_applications(
                    ApplicationUpdateMapper.synchronize(
                        self._index.applications,
                        official.items,
                    )
                )
            self._apply_filters()

    def refresh_after_system_update(self) -> None:
        self._update_details = None
        self._system_package_service.invalidate()
        if self._loaded and not self._loading:
            if self._active_view() == "system-packages":
                self._queue_system_package_search(use_cache=False, immediate=True)
            else:
                self.reload_catalog(use_cache=False)
                if self._active_view() in {"installed", "updates"}:
                    self._queue_aur_installed(use_cache=False)

    @Slot()
    def _update_all_aur_requested(self) -> None:
        if self._aur_update_all_busy or self._aur_update_planner is None:
            return
        names = tuple(item.name for item in self._aur_installed_results if item.update_available)
        if not names:
            self._apply_filters()
            return
        self._aur_update_generation += 1
        generation = self._aur_update_generation
        self._aur_update_all_busy = True
        self.aur_update_all_button.setEnabled(False)
        self.aur_update_all_button.setText("Проверяю AUR…")
        future = self._aur_update_planner.plan_all_async(names)
        future.add_done_callback(lambda done, g=generation: self._aur_update_all_future_done(done, g))

    def _aur_update_all_future_done(self, future: Future, generation: int) -> None:
        try:
            plan = future.result()
        except Exception as exc:
            self._aur_update_all_signals.failed.emit(exc, generation)
        else:
            self._aur_update_all_signals.planned.emit(plan, generation)

    @Slot(object, int)
    def _aur_update_all_planned(self, plan: object, generation: int) -> None:
        if generation != self._aur_update_generation or not isinstance(plan, AurUpdateAllPlan):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Обновить все AUR")
        box.setText(f"Обновить все найденные AUR-пакеты ({len(plan.package_names)} шт.)?")
        box.setInformativeText(
            "Откроется отдельный терминал для обновления всех AUR-пакетов. Официальные пакеты этой кнопкой не обновляются. "
            "Проверяйте PKGBUILD/diff и вопросы yay перед подтверждением."
        )
        box.setDetailedText("Команда: " + " ".join(plan.command_preview) + "\n\n" + "\n".join(plan.package_names))
        accept = box.addButton("Открыть терминал и обновить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not accept:
            self._aur_update_all_busy = False
            self._apply_filters()
            return
        launcher = Path(__file__).resolve().parents[3] / "scripts" / "run-aur-terminal.sh"
        try:
            assert self._aur_terminal is not None
            self._aur_terminal.start(launcher, action="update-all")
        except Exception as exc:
            self._aur_update_all_busy = False
            self._show_aur_update_all_error(exc)
            self._apply_filters()
            return
        self.aur_update_all_button.setText("AUR обновляется в терминале…")

    @Slot(object, int)
    def _aur_update_all_failed(self, error: object, generation: int) -> None:
        if generation != self._aur_update_generation:
            return
        self._aur_update_all_busy = False
        self._show_aur_update_all_error(error if isinstance(error, Exception) else RuntimeError(str(error)))
        self._apply_filters()

    def _show_aur_update_all_error(self, error: Exception) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        if error.__class__.__name__ == "AurSystemUpdateRequired":
            box.setWindowTitle("Сначала обновите систему")
            box.setText("Перед обновлением AUR сначала выполните полное системное обновление Arch Linux.")
            go = box.addButton("Перейти к системным обновлениям", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is go:
                self.updates_requested.emit()
            return
        box.setWindowTitle("AUR-обновление недоступно")
        box.setText(str(error) or "Не удалось начать обновление AUR.")
        detail = getattr(error, "detail", "")
        if detail:
            box.setDetailedText(str(detail)[:4000])
        box.exec()

    @Slot(object)
    def _aur_update_all_completed(self, result: object) -> None:
        if not isinstance(result, AurTerminalResult) or result.action != "update-all":
            return
        self._aur_update_all_busy = False
        if result.state == "success":
            record_aur_bulk_update(result="success", message="Обновлены все AUR-пакеты")
            invalidate_update_state()
            self.package_state_changed.emit()
        elif result.state not in {"cancelled", "interrupted"}:
            record_aur_bulk_update(
                result="failed", message=result.detail or f"yay:{result.return_code}"
            )
        self._queue_aur_installed(use_cache=False)
        self._update_details = current_update_details()
        self._apply_filters()

    @Slot(str)
    def _aur_update_all_terminal_failed(self, message: str) -> None:
        if not self._aur_update_all_busy:
            return
        self._aur_update_all_busy = False
        record_aur_bulk_update(result="failed", message=message)
        self._show_aur_update_all_error(RuntimeError(message))
        self._apply_filters()

    def _refresh_last_action_label(self) -> None:
        entry = last_app_store_activity()
        if entry is None:
            self.last_action_label.setText("")
            return
        label = entry.application_name or entry.package_name
        actions = {"install": "установлено", "remove": "удалено"}
        action_text = actions.get(entry.action, entry.action)
        result_text = "" if entry.result == "success" else f" · {entry.result}"
        when = entry.timestamp.astimezone().strftime("%H:%M")
        self.last_action_label.setText(
            f"Последнее: {action_text} {label or 'приложение'} · {when}{result_text}"
        )

    @Slot()
    def _maybe_append_batch(self, *_args) -> None:
        if self.body_stack.currentIndex() != self.STATE_CATALOG:
            return
        bar = self.scroll.verticalScrollBar()
        if self._render_cursor < len(self._filtered) and bar.value() >= max(0, bar.maximum() - 240):
            self._append_batch()

    @Slot()
    def _maybe_fill_viewport(self) -> None:
        if self.body_stack.currentIndex() != self.STATE_CATALOG:
            return
        bar = self.scroll.verticalScrollBar()
        while self._render_cursor < len(self._filtered) and bar.maximum() <= 0:
            before_cursor = self._render_cursor
            self._append_batch()
            if self._render_cursor == before_cursor:
                break
            self.grid_host.adjustSize()
            if self._render_cursor >= INITIAL_BATCH_SIZE + NEXT_BATCH_SIZE * 2:
                # Guard against platform plugins that do not calculate scroll
                # ranges until the event loop returns.
                break

    def _column_count(self) -> int:
        width = max(1, self.scroll.viewport().width())
        return max(1, floor((width + GRID_SPACING) / (CARD_MIN_WIDTH + GRID_SPACING)))

    def _card_width(self, columns: int) -> int:
        width = max(1, self.scroll.viewport().width())
        available = width - GRID_SPACING * max(0, columns - 1)
        return max(CARD_MIN_WIDTH, min(CARD_MAX_WIDTH, available // max(1, columns)))

    def _relayout_cards(self) -> None:
        columns = self._column_count()
        card_width = self._card_width(columns)
        self._columns = columns
        for index, card in enumerate(self._cards):
            self.grid.removeWidget(card)
            card.setFixedWidth(card_width)
            self.grid.addWidget(card, index // columns, index % columns)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        # A stacked page can become visible at its final width without receiving a
        # page-level resize event. Track the actual scroll viewport instead so the
        # catalog never stays stuck at the narrow, pre-layout column count.
        if (
            hasattr(self, "scroll")
            and watched is self.scroll.viewport()
            and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}
            and self._cards
        ):
            QTimer.singleShot(0, self._relayout_cards)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self._cards:
            QTimer.singleShot(0, self._relayout_cards)

    def _sync_activity_spinner(self) -> None:
        """Show one compact spinner whenever the store is doing background work."""
        if not hasattr(self, "activity_spinner"):
            return
        queued_aur = hasattr(self, "_aur_search_timer") and self._aur_search_timer.isActive()
        queued_system = hasattr(self, "_system_package_timer") and self._system_package_timer.isActive()
        busy = any((
            self._loading,
            self._aur_loading,
            self._aur_installed_loading,
            self._popularity_loading,
            self._system_package_loading,
            self._aur_update_all_busy,
            queued_aur,
            queued_system,
        ))
        if busy:
            self.activity_spinner.start()
        else:
            self.activity_spinner.stop()

    def _show_state(self, state: int) -> None:
        self.body_stack.setCurrentIndex(state)
        self._sync_activity_spinner()

    @property
    def visible_card_count(self) -> int:
        """Exposed for lightweight UI regression tests."""

        return len(self._cards)

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)
