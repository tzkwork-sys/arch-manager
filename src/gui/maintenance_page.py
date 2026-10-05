from __future__ import annotations

import logging
from pathlib import Path
import subprocess

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.activity_log import append_activity
from src.core.dashboard import DashboardSummary
from src.core.formatting import format_bytes
from src.core.maintenance import (
    DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS,
    MaintenanceSummary,
    collect_maintenance,
    validate_cache_keep_versions,
)
from src.core.maintenance_actions import MaintenanceAction, build_cleanup_request
from src.core.maintenance_executor import (
    MaintenanceActionFailed,
    MaintenanceAuthorizationError,
    MaintenanceCancelled,
    MaintenanceCleanupResult,
    MaintenanceExecutionError,
    MaintenanceHelperUnavailable,
    MaintenanceTimedOut,
    execute_maintenance_cleanup,
)
from src.core.preferences import (
    MAINTENANCE_PACKAGE_CACHE_KEEP_DEFAULT,
    MAINTENANCE_PACKAGE_CACHE_KEEP_KEY,
)

from .page_base import NavigablePage
from .theme import card_frame, muted_text

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACDIFF_RUNNER = PROJECT_ROOT / "scripts" / "run-pacdiff-session.sh"
KONSOLE = Path("/usr/bin/konsole")


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class _ScanWorker(QRunnable):
    def __init__(self, cache_keep_versions: int) -> None:
        super().__init__()
        self.cache_keep_versions = cache_keep_versions
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(collect_maintenance(self.cache_keep_versions))
        except Exception as exc:  # pragma: no cover - OS boundary
            LOGGER.exception("Maintenance scan failed")
            self.signals.failed.emit(exc)


class _CleanupWorker(QRunnable):
    def __init__(
        self,
        actions: tuple[MaintenanceAction, ...],
        cache_keep_versions: int,
    ) -> None:
        super().__init__()
        self.actions = actions
        self.cache_keep_versions = cache_keep_versions
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = execute_maintenance_cleanup(
                build_cleanup_request(
                    self.actions,
                    cache_keep_versions=self.cache_keep_versions,
                )
            )
        except Exception as exc:  # pragma: no cover - OS boundary
            LOGGER.exception("Maintenance cleanup failed")
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(result)


class _PacdiffWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        if not KONSOLE.is_file():
            self.signals.failed.emit(RuntimeError("Konsole не найден"))
            return
        if not PACDIFF_RUNNER.is_file():
            self.signals.failed.emit(RuntimeError("runner проверки настроек не найден"))
            return
        try:
            completed = subprocess.run(
                [str(KONSOLE), "--separate", "-e", str(PACDIFF_RUNNER)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4 * 60 * 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.signals.failed.emit(exc)
            return
        self.signals.completed.emit(completed.returncode)


class _MaintenanceOption(QFrame):
    changed = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        *,
        default_checked: bool,
        cautious: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        del cautious
        template = card_frame()
        self.setFrameShape(template.frameShape())
        self.setFrameShadow(template.frameShadow())
        self.setLineWidth(template.lineWidth())
        self.setBackgroundRole(template.backgroundRole())
        self.setAutoFillBackground(True)
        self.setProperty("archManagerCard", True)

        self.default_checked = default_checked
        self._initial_selection_applied = False
        self._description_text = description
        self._details_text = "—"

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(7)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.checkbox = QCheckBox(title)
        title_font = self.checkbox.font()
        title_font.setBold(True)
        self.checkbox.setFont(title_font)
        self.checkbox.stateChanged.connect(lambda _state: self.changed.emit())

        self.value = QLabel("Проверяется…")
        value_font = self.value.font()
        value_font.setBold(True)
        self.value.setFont(value_font)

        self.info_button = QToolButton()
        self.info_button.setText("ⓘ")
        self.info_button.setToolTip(f"Подробнее: {title}")
        self.info_button.setAutoRaise(True)
        self.info_button.setFixedSize(28, 28)
        self.info_button.clicked.connect(self._show_info)

        heading.addWidget(self.checkbox, 1)
        heading.addWidget(self.value, 0, Qt.AlignmentFlag.AlignRight)
        heading.addWidget(self.info_button, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(heading)

    def add_control(self, label: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(label)
        muted_text(caption)
        row.addWidget(caption)
        row.addWidget(widget, 0)
        row.addStretch(1)
        layout = self.layout()
        if isinstance(layout, QVBoxLayout):
            layout.addLayout(row)

    def _show_info(self) -> None:
        text = self._description_text
        if self._details_text and self._details_text != "—":
            text += "\n\n" + self._details_text
        QMessageBox.information(self, self.checkbox.text(), text)

    def set_state(
        self,
        *,
        enabled: bool,
        value: str,
        details: str,
    ) -> None:
        self.value.setText(value)
        self._details_text = details
        self.checkbox.setEnabled(enabled)
        if not enabled:
            self.checkbox.setChecked(False)
        elif not self._initial_selection_applied:
            self.checkbox.setChecked(self.default_checked)
            self._initial_selection_applied = True

    def set_busy(self, busy: bool) -> None:
        self.checkbox.setEnabled(not busy and self.checkbox.property("hasData") is not False)


class MaintenancePage(NavigablePage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = QSettings()
        self._thread_pool = QThreadPool.globalInstance()
        self._scan_worker: _ScanWorker | None = None
        self._cleanup_worker: _CleanupWorker | None = None
        self._active_cleanup_actions: tuple[MaintenanceAction, ...] = ()
        self._pacdiff_worker: _PacdiffWorker | None = None
        self._summary: MaintenanceSummary | None = None
        self._loaded_once = False

        stored_keep = self.settings.value(
            MAINTENANCE_PACKAGE_CACHE_KEEP_KEY,
            MAINTENANCE_PACKAGE_CACHE_KEEP_DEFAULT,
            type=int,
        )
        try:
            self._cache_keep_versions = validate_cache_keep_versions(stored_keep)
        except ValueError:
            self._cache_keep_versions = DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS

        layout = self.create_page_layout("Обслуживание", spacing=10)

        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        self.refresh_button = QPushButton("Проверить снова")
        self.refresh_button.clicked.connect(self.refresh)
        self.add_header_action(self.refresh_button)

        self.checked_label = self.make_checked_label()
        layout.addWidget(self.checked_label)

        summary_card = card_frame()
        summary_layout = QHBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 11, 14, 11)
        summary_layout.setSpacing(8)
        self.summary_value = QLabel("Анализ ещё не выполнен")
        summary_font = self.summary_value.font()
        summary_font.setPointSize(summary_font.pointSize() + 2)
        summary_font.setBold(True)
        self.summary_value.setFont(summary_font)
        summary_layout.addWidget(self.summary_value, 1)
        self.summary_info_button = QToolButton()
        self.summary_info_button.setText("ⓘ")
        self.summary_info_button.setToolTip("Как считается объём очистки")
        self.summary_info_button.setAutoRaise(True)
        self.summary_info_button.setFixedSize(28, 28)
        self.summary_info_button.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "Объём очистки",
                "Объём старых системных журналов не включается в общую оценку: "
                "journalctl сообщает размер активных и архивных журналов вместе, а очистка "
                "удаляет только архивы старше 30 дней.",
            )
        )
        summary_layout.addWidget(self.summary_info_button)
        layout.addWidget(summary_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        self.options: dict[MaintenanceAction, _MaintenanceOption] = {
            MaintenanceAction.PACKAGE_CACHE: _MaintenanceOption(
                "Кэш обновлений",
                "Старые загруженные пакеты. Число сохраняемых версий можно выбрать ниже.",
                default_checked=True,
            ),
            MaintenanceAction.ORPHANS: _MaintenanceOption(
                "Ненужные зависимости",
                "Пакеты, которые были установлены как зависимости и сейчас больше никому не нужны.",
                default_checked=False,
                cautious=True,
            ),
            MaintenanceAction.JOURNAL: _MaintenanceOption(
                "Системный журнал",
                "Удаляет только архивные записи старше 30 дней. Текущий активный журнал сохраняется.",
                default_checked=True,
            ),
            MaintenanceAction.THUMBNAILS: _MaintenanceOption(
                "Кэш миниатюр",
                "Предпросмотры изображений и видео. При необходимости приложения создадут их заново.",
                default_checked=True,
            ),
            MaintenanceAction.TRASH: _MaintenanceOption(
                "Корзина",
                "Окончательно удаляет файлы из корзин текущего пользователя, включая корзины на других подключённых дисках.",
                default_checked=False,
                cautious=True,
            ),
        }

        self.cache_keep_combo = QComboBox()
        self.cache_keep_combo.addItem("3 версии · рекомендуется", 3)
        self.cache_keep_combo.addItem("2 версии", 2)
        self.cache_keep_combo.addItem("1 версия", 1)
        self.cache_keep_combo.addItem("0 версий · очистить полностью", 0)
        for index in range(self.cache_keep_combo.count()):
            if self.cache_keep_combo.itemData(index) == self._cache_keep_versions:
                self.cache_keep_combo.setCurrentIndex(index)
                break
        self.cache_keep_combo.setToolTip(
            "0 удаляет весь кэш загруженных пакетов. Установленные пакеты остаются установленными."
        )
        self.cache_keep_combo.currentIndexChanged.connect(self._cache_keep_changed)
        self.options[MaintenanceAction.PACKAGE_CACHE].add_control(
            "Оставлять в кэше:", self.cache_keep_combo
        )

        for option in self.options.values():
            option.changed.connect(self._update_selection_summary)
            body_layout.addWidget(option)

        config_card = card_frame()
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(14, 11, 14, 11)
        config_layout.setSpacing(6)
        config_heading = QHBoxLayout()
        config_title = QLabel("Файлы настроек после обновлений")
        config_title_font = config_title.font()
        config_title_font.setBold(True)
        config_title.setFont(config_title_font)
        self.config_value = QLabel("Проверяется…")
        config_value_font = self.config_value.font()
        config_value_font.setBold(True)
        self.config_value.setFont(config_value_font)
        config_heading.addWidget(config_title, 1)
        config_heading.addWidget(self.config_value)
        self._config_details_text = (
            "Такие файлы нельзя безопасно удалить автоматически: иногда в них находятся новые важные настройки пакетов."
        )
        self.config_info_button = QToolButton()
        self.config_info_button.setText("ⓘ")
        self.config_info_button.setToolTip("Подробнее о файлах настроек")
        self.config_info_button.setAutoRaise(True)
        self.config_info_button.setFixedSize(28, 28)
        self.config_info_button.clicked.connect(self._show_config_info)
        config_heading.addWidget(self.config_info_button)
        self.pacdiff_button = QPushButton("Проверить вручную…")
        self.pacdiff_button.setEnabled(False)
        self.pacdiff_button.setVisible(False)
        self.pacdiff_button.clicked.connect(self._start_pacdiff)
        config_layout.addLayout(config_heading)
        config_layout.addWidget(self.pacdiff_button, 0, Qt.AlignmentFlag.AlignLeft)
        body_layout.addWidget(config_card)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # The primary action stays visible even when the list itself scrolls.
        action_card = card_frame()
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(14, 11, 14, 11)
        self.selection_label = QLabel("Выберите категории для очистки.")
        self.selection_label.setWordWrap(True)
        self.clean_button = QPushButton("Очистить выбранное")
        self.clean_button.setEnabled(False)
        self.clean_button.clicked.connect(self._confirm_cleanup)
        action_layout.addWidget(self.selection_label, 1)
        action_layout.addWidget(self.clean_button)
        layout.addWidget(action_card)

    def _set_config_details(self, text: str) -> None:
        self._config_details_text = text

    def _show_config_info(self) -> None:
        QMessageBox.information(
            self,
            "Файлы настроек после обновлений",
            self._config_details_text,
        )

    def ensure_loaded(self) -> None:
        if not self._loaded_once and self._scan_worker is None:
            self.refresh()

    def set_summary(self, summary: DashboardSummary) -> None:
        if self._scan_worker is not None or self._cleanup_worker is not None:
            return
        if summary.maintenance.cache_keep_versions != self._cache_keep_versions:
            # Dashboard uses the default retention. A custom user preference is
            # scanned when this page is opened so the displayed estimate matches it.
            self._loaded_once = False
            return
        self._loaded_once = True
        self._apply_summary(summary.maintenance)
        self.set_checked_at(self.checked_label, summary.checked_at)

    @Slot(int)
    def _cache_keep_changed(self, _index: int) -> None:
        value = self.cache_keep_combo.currentData()
        try:
            keep = validate_cache_keep_versions(int(value))
        except (TypeError, ValueError):
            return
        if keep == self._cache_keep_versions:
            return
        self._cache_keep_versions = keep
        self.settings.setValue(MAINTENANCE_PACKAGE_CACHE_KEEP_KEY, keep)
        self.settings.sync()
        self._loaded_once = False
        if self._scan_worker is None and self._cleanup_worker is None:
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        if self._scan_worker is not None or self._cleanup_worker is not None:
            return
        self.summary_value.setText("Проверяю, что можно безопасно очистить…")
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Проверяю…")
        self.cache_keep_combo.setEnabled(False)
        worker = _ScanWorker(self._cache_keep_versions)
        self._scan_worker = worker
        worker.signals.completed.connect(self._scan_completed)
        worker.signals.failed.connect(self._scan_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _scan_completed(self, summary: MaintenanceSummary) -> None:
        from datetime import datetime

        self._scan_worker = None
        self._loaded_once = True
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Проверить снова")
        self.cache_keep_combo.setEnabled(True)
        self._apply_summary(summary)
        self.set_checked_at(self.checked_label, datetime.now().astimezone())

    @Slot(object)
    def _scan_failed(self, error: Exception) -> None:
        self._scan_worker = None
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Проверить снова")
        self.cache_keep_combo.setEnabled(True)
        self.summary_value.setText("Анализ не завершён")
        QMessageBox.warning(self, "Обслуживание", str(error) or "Не удалось выполнить анализ.")

    def _apply_summary(self, summary: MaintenanceSummary) -> None:
        self._summary = summary
        known = summary.known_reclaimable_bytes
        if known is None:
            self.summary_value.setText("Анализ выполнен частично")
        elif known == 0 and (summary.journal_usage_bytes or 0) == 0:
            self.summary_value.setText("✓ Очистка сейчас почти не требуется")
        elif known == 0:
            self.summary_value.setText("Можно очистить старые системные журналы")
        else:
            self.summary_value.setText(
                f"Без учёта журналов можно освободить примерно {format_bytes(known)}"
            )

        cache = self.options[MaintenanceAction.PACKAGE_CACHE]
        cache_bytes = summary.reclaimable_cache_bytes
        cache_enabled = cache_bytes is not None and cache_bytes > 0
        cache.checkbox.setProperty("hasData", cache_enabled)
        if summary.cache_keep_versions == 0:
            cache_details = (
                "Будет удалён весь кэш пакетов. Установленные пакеты не удаляются, "
                "но для отката старую версию придётся загружать заново."
            )
        elif summary.cache_keep_versions == 1:
            cache_details = "После очистки останется одна последняя версия каждого пакета."
        else:
            cache_details = (
                f"После очистки останутся {summary.cache_keep_versions} последние версии каждого пакета."
            )
        if cache_bytes == 0:
            cache_details = "Для выбранного режима хранения старых версий для удаления не найдено."
        cache.set_state(
            enabled=cache_enabled,
            value=("Нет данных" if cache_bytes is None else format_bytes(cache_bytes)),
            details=cache_details,
        )

        orphans = self.options[MaintenanceAction.ORPHANS]
        orphan_count = summary.orphan_packages
        orphan_enabled = orphan_count is not None and orphan_count > 0
        orphans.checkbox.setProperty("hasData", orphan_enabled)
        orphan_value = "Нет данных" if orphan_count is None else f"{orphan_count} шт."
        if summary.orphan_installed_bytes is not None and summary.orphan_installed_bytes > 0:
            orphan_value += f" · ~{format_bytes(summary.orphan_installed_bytes)}"
        names = ", ".join(summary.orphan_names[:12])
        if len(summary.orphan_names) > 12:
            names += f" и ещё {len(summary.orphan_names) - 12}"
        orphans.set_state(
            enabled=orphan_enabled,
            value=orphan_value,
            details=(
                f"Будут удалены только найденные сейчас ненужные зависимости: {names}"
                if names
                else "Ненужных зависимостей не найдено."
            ),
        )

        journal = self.options[MaintenanceAction.JOURNAL]
        journal_bytes = summary.journal_usage_bytes
        journal_enabled = journal_bytes is not None and journal_bytes > 0
        journal.checkbox.setProperty("hasData", journal_enabled)
        journal.set_state(
            enabled=journal_enabled,
            value=("Нет данных" if journal_bytes is None else f"{format_bytes(journal_bytes)} всего"),
            details=(
                "Это общий размер активных и архивных журналов. Очистка затронет только архивы старше 30 дней."
                if journal_enabled
                else "Журнал пуст или его размер определить не удалось."
            ),
        )

        thumbnails = self.options[MaintenanceAction.THUMBNAILS]
        thumb_bytes = summary.thumbnail_cache_bytes
        thumb_enabled = thumb_bytes is not None and thumb_bytes > 0
        thumbnails.checkbox.setProperty("hasData", thumb_enabled)
        thumbnails.set_state(
            enabled=thumb_enabled,
            value=("Нет данных" if thumb_bytes is None else format_bytes(thumb_bytes)),
            details=(
                f"Файлов миниатюр: {summary.thumbnail_items or 0}. Они будут создаваться заново по мере использования."
                if thumb_bytes is not None
                else "Не удалось прочитать пользовательский кэш миниатюр."
            ),
        )

        trash = self.options[MaintenanceAction.TRASH]
        trash_bytes = summary.trash_bytes
        trash_enabled = trash_bytes is not None and trash_bytes > 0
        trash.checkbox.setProperty("hasData", trash_enabled)
        location_suffix = ""
        if summary.trash_locations is not None and summary.trash_locations > 1:
            location_suffix = f" · мест хранения: {summary.trash_locations}"
        trash.set_state(
            enabled=trash_enabled,
            value=("Нет данных" if trash_bytes is None else format_bytes(trash_bytes)),
            details=(
                f"Объектов в корзине: {summary.trash_items or 0}{location_suffix}. "
                "После очистки восстановить их из корзины будет нельзя."
                if trash_bytes is not None
                else "Не удалось прочитать содержимое корзины."
            ),
        )

        config_count = summary.config_attention_files
        if config_count is None:
            self.config_value.setText("Нет данных")
            self._set_config_details(
                "Не удалось проверить .pacnew/.pacsave/.pacorig. Никакие файлы не изменялись."
            )
            self.pacdiff_button.setEnabled(False)
            self.pacdiff_button.setVisible(False)
        elif config_count == 0:
            self.config_value.setText("Всё проверено")
            self._set_config_details("Файлов настроек, требующих ручного решения, не найдено.")
            self.pacdiff_button.setEnabled(False)
            self.pacdiff_button.setVisible(False)
        else:
            self.config_value.setText(f"Найдено: {config_count}")
            preview = "\n".join(summary.config_attention_paths[:5])
            if len(summary.config_attention_paths) > 5:
                preview += f"\n…и ещё {len(summary.config_attention_paths) - 5}"
            self._set_config_details(
                "Arch Manager не удаляет их автоматически. Нажмите «Проверить вручную…», "
                "чтобы принять решение по каждому файлу.\n" + preview
            )
            self.pacdiff_button.setEnabled(True)
            self.pacdiff_button.setVisible(True)

        self._update_selection_summary()

    def _selected_actions(self) -> tuple[MaintenanceAction, ...]:
        return tuple(
            action for action, option in self.options.items() if option.checkbox.isChecked()
        )

    @Slot()
    def _update_selection_summary(self) -> None:
        selected = self._selected_actions()
        self.clean_button.setEnabled(bool(selected) and self._cleanup_worker is None)
        if not selected or self._summary is None:
            self.selection_label.setText("Выберите категории для очистки.")
            return

        selected_bytes = 0
        known_any = False
        mapping = {
            MaintenanceAction.PACKAGE_CACHE: self._summary.reclaimable_cache_bytes,
            MaintenanceAction.ORPHANS: self._summary.orphan_installed_bytes,
            MaintenanceAction.THUMBNAILS: self._summary.thumbnail_cache_bytes,
            MaintenanceAction.TRASH: self._summary.trash_bytes,
        }
        for action in selected:
            value = mapping.get(action)
            if value is not None:
                selected_bytes += value
                known_any = True

        suffix = ""
        if MaintenanceAction.JOURNAL in selected:
            suffix = " + старые журналы"
        if known_any:
            self.selection_label.setText(
                f"Выбрано: {len(selected)} · ожидается около {format_bytes(selected_bytes)}{suffix}"
            )
        else:
            self.selection_label.setText(f"Выбрано: {len(selected)}{suffix}")

    @Slot()
    def _confirm_cleanup(self) -> None:
        if self._summary is None:
            return
        selected = self._selected_actions()
        if not selected:
            return

        cache_title = "Кэш обновлений"
        if MaintenanceAction.PACKAGE_CACHE in selected:
            if self._cache_keep_versions == 0:
                cache_title += " — удалить весь кэш"
            else:
                cache_title += f" — оставить {self._cache_keep_versions} верс."
        titles = {
            MaintenanceAction.PACKAGE_CACHE: cache_title,
            MaintenanceAction.ORPHANS: "Ненужные зависимости",
            MaintenanceAction.JOURNAL: "Системный журнал старше 30 дней",
            MaintenanceAction.THUMBNAILS: "Кэш миниатюр",
            MaintenanceAction.TRASH: "Корзина",
        }
        lines = "\n".join(f"• {titles[action]}" for action in selected)
        risky = {
            MaintenanceAction.ORPHANS,
            MaintenanceAction.TRASH,
        }.intersection(selected)
        extra_parts: list[str] = []
        if risky:
            extra_parts.append(
                "В выбранных категориях есть действия, которые нельзя отменить через Arch Manager."
            )
        if (
            MaintenanceAction.PACKAGE_CACHE in selected
            and self._cache_keep_versions == 0
        ):
            extra_parts.append(
                "Кэш пакетов будет очищен полностью. Установленные пакеты останутся, "
                "но локальные копии для быстрого отката исчезнут."
            )
        extra = ""
        if extra_parts:
            extra = "\n\n" + "\n".join(extra_parts) + "\nПроверьте список ещё раз."

        answer = QMessageBox.question(
            self,
            "Подтверждение очистки",
            "Будут очищены только выбранные категории:\n\n"
            f"{lines}{extra}\n\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_cleanup(selected)

    def _set_busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        self.cache_keep_combo.setEnabled(not busy)
        self.clean_button.setEnabled(not busy and bool(self._selected_actions()))
        self.pacdiff_button.setEnabled(
            not busy
            and self._summary is not None
            and (self._summary.config_attention_files or 0) > 0
        )
        for option in self.options.values():
            has_data = bool(option.checkbox.property("hasData"))
            option.checkbox.setEnabled(not busy and has_data)

    def _start_cleanup(self, actions: tuple[MaintenanceAction, ...]) -> None:
        if self._cleanup_worker is not None:
            return
        self._set_busy(True)
        self._active_cleanup_actions = actions
        self.selection_label.setText("Выполняю выбранную очистку…")
        worker = _CleanupWorker(actions, self._cache_keep_versions)
        self._cleanup_worker = worker
        worker.signals.completed.connect(self._cleanup_completed)
        worker.signals.failed.connect(self._cleanup_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _cleanup_completed(self, result: MaintenanceCleanupResult) -> None:
        self._cleanup_worker = None
        self._active_cleanup_actions = ()
        self._set_busy(False)
        append_activity(
            "maintenance",
            "cleanup",
            "success",
            f"Выполнено категорий: {len(result.completed_actions)}",
            data={
                "actions": [action.value for action in result.completed_actions],
                "package_cache_keep_versions": result.cache_keep_versions,
            },
        )
        QMessageBox.information(
            self,
            "Обслуживание завершено",
            f"Выполнено категорий: {len(result.completed_actions)}. Arch Manager сейчас перепроверит состояние.",
        )
        self.refresh()

    @Slot(object)
    def _cleanup_failed(self, error: Exception) -> None:
        self._cleanup_worker = None
        actions = self._active_cleanup_actions
        self._active_cleanup_actions = ()
        self._set_busy(False)
        if isinstance(error, MaintenanceCancelled):
            title = "Очистка отменена"
            text = "Авторизация отменена. Ничего из пользовательских категорий после этого не удалялось."
        elif isinstance(error, MaintenanceHelperUnavailable):
            title = "Не установлен системный компонент"
            text = (
                f"{error}\n\nЗапустите из папки проекта: ./scripts/install-stage6-helper.sh"
            )
        elif isinstance(error, MaintenanceAuthorizationError):
            title = "Нет административного разрешения"
            text = str(error)
        elif isinstance(error, MaintenanceTimedOut):
            title = "Очистка прервана по времени"
            text = str(error)
        elif isinstance(error, (MaintenanceActionFailed, MaintenanceExecutionError)):
            title = "Очистка завершилась с ошибкой"
            detail = getattr(error, "detail", "").strip()
            text = str(error) + (f"\n\nПодробность: {detail}" if detail else "")
        else:
            title = "Ошибка обслуживания"
            text = str(error) or "Не удалось выполнить очистку."
        result_name = "cancelled" if isinstance(error, MaintenanceCancelled) else "failed"
        append_activity(
            "maintenance",
            "cleanup",
            result_name,
            str(error) or title,
            data={
                "actions": [action.value for action in actions],
                "package_cache_keep_versions": self._cache_keep_versions,
            },
        )
        QMessageBox.warning(self, title, text)
        self.refresh()

    @Slot()
    def _start_pacdiff(self) -> None:
        if self._pacdiff_worker is not None:
            return
        answer = QMessageBox.information(
            self,
            "Ручная проверка настроек",
            "Откроется отдельное окно Konsole. Для каждого файла решение принимаете вы; "
            "Arch Manager не будет автоматически заменять или удалять конфигурацию.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        self._set_busy(True)
        self.selection_label.setText("Открыта ручная проверка файлов настроек…")
        worker = _PacdiffWorker()
        self._pacdiff_worker = worker
        worker.signals.completed.connect(self._pacdiff_completed)
        worker.signals.failed.connect(self._pacdiff_failed)
        self._thread_pool.start(worker)

    @Slot(object)
    def _pacdiff_completed(self, _returncode: int) -> None:
        self._pacdiff_worker = None
        self._set_busy(False)
        self.refresh()

    @Slot(object)
    def _pacdiff_failed(self, error: Exception) -> None:
        self._pacdiff_worker = None
        self._set_busy(False)
        QMessageBox.warning(
            self,
            "Проверка файлов настроек",
            f"Не удалось открыть интерактивную проверку: {error}",
        )
