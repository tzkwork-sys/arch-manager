from __future__ import annotations

import logging
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import (
    QTabBar,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.recovery import RecoveryReadiness
from src.core.recovery_executor import recovery_helper_available
from src.core.recovery_usb import RecoveryUsbDevice, uefi_usb_bootnext_available

from .broken_roots_dialog import BrokenRootsDialog
from .page_base import NavigablePage
from .recovery_workers import (
    _BrokenRootsDeleteWorker,
    _RecoveryActionWorker,
    _RecoveryFingerprintWorker,
    _RecoveryUsbActionWorker,
    _RecoveryUsbScanWorker,
    _RecoveryWorker,
)
from .recovery_usb_mixin import RecoveryUsbMixin
from .details_dialog import DetailsDialog
from .theme import CARD_MARGINS, card_frame, muted_text

LOGGER = logging.getLogger(__name__)


class RecoveryPage(RecoveryUsbMixin, NavigablePage):
    """Compact centre for local and emergency USB Recovery paths."""

    def __init__(self, parent: QWidget | None = None, *, embedded: bool = False) -> None:
        super().__init__(parent)
        self._embedded = embedded
        self._thread_pool = QThreadPool.globalInstance()
        self._worker: _RecoveryWorker | None = None
        self._fingerprint_worker: _RecoveryFingerprintWorker | None = None
        self._action_worker: _RecoveryActionWorker | None = None
        self._usb_scan_worker: _RecoveryUsbScanWorker | None = None
        self._usb_action_worker: _RecoveryUsbActionWorker | None = None
        self._broken_delete_worker: _BrokenRootsDeleteWorker | None = None
        self._usb_devices: dict[str, RecoveryUsbDevice] = {}
        self._readiness: RecoveryReadiness | None = None
        self._state_fingerprint: str | None = None
        self._helper_available = False
        self._auto_prepare_after_check = False
        self._technical_text = "Проверка ещё не выполнялась."
        self._usb_progress_timer = QTimer(self)
        self._usb_progress_timer.setInterval(100)
        self._usb_progress_timer.timeout.connect(self._poll_usb_progress)

        if embedded:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(6)
            outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        else:
            outer = self.create_page_layout(
                "Восстановление",
                "Среда проверяется и при необходимости подготавливается автоматически. "
                "Точку восстановления вы выберете уже после перезагрузки.",
            )

        self._active_mode = "local"
        self.mode_tabs = QTabBar()
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.addTab("Локальное восстановление")
        self.mode_tabs.addTab("Аварийная USB-флешка")
        self.mode_tabs.setTabToolTip(0, "Однократная загрузка Recovery-среды с внутреннего диска")
        self.mode_tabs.setTabToolTip(1, "Создание Recovery-флешки и однократная загрузка с неё")
        self.mode_tabs.currentChanged.connect(lambda index: self._set_recovery_mode("local" if index == 0 else "usb"))
        outer.addWidget(self.mode_tabs)

        self.recovery_stack = QStackedWidget()
        self.recovery_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        outer.addWidget(self.recovery_stack, 0, Qt.AlignmentFlag.AlignTop)

        self.local_mode_page = QWidget()
        local_mode_layout = QVBoxLayout(self.local_mode_page)
        local_mode_layout.setContentsMargins(0, 0, 0, 0)
        local_mode_layout.setSpacing(0)

        self.status_card = card_frame()
        card = QVBoxLayout(self.status_card)
        card.setContentsMargins(*CARD_MARGINS)
        card.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        label = QLabel("Локальное восстановление")
        label_font = label.font()
        label_font.setBold(True)
        top.addWidget(label)
        top.addStretch(1)

        self.old_roots_button = QToolButton()
        self.old_roots_button.setText("⚙")
        self.old_roots_button.setToolTip("Сохранённые прежние системы")
        self.old_roots_button.setAutoRaise(True)
        self.old_roots_button.setFixedSize(28, 28)
        self.old_roots_button.setEnabled(False)
        self.old_roots_button.clicked.connect(self._manage_broken_roots)
        top.addWidget(self.old_roots_button)

        self.info_button = QToolButton()
        self.info_button.setText("ⓘ")
        self.info_button.setToolTip("Техническая информация")
        self.info_button.setAutoRaise(True)
        self.info_button.setFixedSize(28, 28)
        self.info_button.clicked.connect(self._show_technical_info)
        top.addWidget(self.info_button)
        card.addLayout(top)

        self.summary_title = QLabel("Проверяю готовность…")
        title_font = self.summary_title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 3)
        self.summary_title.setFont(title_font)
        card.addWidget(self.summary_title)

        self.summary_text = QLabel(
            "Arch Manager проверяет общую Recovery-среду. Выбирать точку сейчас не нужно."
        )
        self.summary_text.setWordWrap(True)
        muted_text(self.summary_text)
        card.addWidget(self.summary_text)

        self.problem_text = QLabel("")
        self.problem_text.setWordWrap(True)
        self.problem_text.setVisible(False)
        card.addWidget(self.problem_text)

        self.old_roots_label = QLabel("")
        self.old_roots_label.setWordWrap(True)
        self.old_roots_label.setVisible(False)
        muted_text(self.old_roots_label)
        card.addWidget(self.old_roots_label)

        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        muted_text(self.progress_label)
        self.progress_label.setVisible(False)
        card.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        card.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.reboot_button = QPushButton("Перезагрузить в режим восстановления")
        self.reboot_button.clicked.connect(self._reboot)
        self.reboot_button.setEnabled(False)
        actions.addWidget(self.reboot_button)

        self.refresh_button = QPushButton("Проверить снова")
        self.refresh_button.clicked.connect(self._manual_refresh)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        card.addLayout(actions)

        self.checked_label = self.make_checked_label()
        card.addWidget(self.checked_label)

        self.status_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        local_mode_layout.addWidget(
            self.status_card, 0, Qt.AlignmentFlag.AlignTop
        )
        local_mode_layout.addStretch(1)
        self.recovery_stack.addWidget(self.local_mode_page)

        self.usb_mode_page = QWidget()
        usb_mode_layout = QVBoxLayout(self.usb_mode_page)
        usb_mode_layout.setContentsMargins(0, 0, 0, 0)
        usb_mode_layout.setSpacing(0)

        self.usb_card = card_frame()
        usb = QVBoxLayout(self.usb_card)
        usb.setContentsMargins(*CARD_MARGINS)
        usb.setSpacing(8)

        usb_top = QHBoxLayout()
        usb_top.setSpacing(8)
        usb_label = QLabel("Аварийная USB-флешка")
        usb_label_font = usb_label.font()
        usb_label_font.setBold(True)
        usb_label.setFont(usb_label_font)
        usb_top.addWidget(usb_label)
        usb_top.addStretch(1)

        self.usb_refresh_button = QToolButton()
        self.usb_refresh_button.setText("↻")
        self.usb_refresh_button.setToolTip("Обновить список USB-накопителей")
        self.usb_refresh_button.setAutoRaise(True)
        self.usb_refresh_button.setFixedSize(28, 28)
        self.usb_refresh_button.clicked.connect(self._refresh_usb_devices)
        usb_top.addWidget(self.usb_refresh_button)
        usb.addLayout(usb_top)

        self.usb_summary_title = QLabel("Подключите USB-флешку")
        usb_title_font = self.usb_summary_title.font()
        usb_title_font.setBold(True)
        usb_title_font.setPointSize(usb_title_font.pointSize() + 2)
        self.usb_summary_title.setFont(usb_title_font)
        usb.addWidget(self.usb_summary_title)

        self.usb_summary_text = QLabel(
            "На флешку записывается та же автономная Recovery-среда, что используется "
            "для локального режима. Все данные на выбранной флешке будут удалены."
        )
        self.usb_summary_text.setWordWrap(True)
        muted_text(self.usb_summary_text)
        usb.addWidget(self.usb_summary_text)

        usb_device_row = QHBoxLayout()
        usb_device_row.setSpacing(8)
        usb_device_label = QLabel("Накопитель:")
        usb_device_row.addWidget(usb_device_label)
        self.usb_combo = QComboBox()
        self.usb_combo.setMinimumWidth(300)
        self.usb_combo.currentIndexChanged.connect(self._usb_selection_changed)
        usb_device_row.addWidget(self.usb_combo, 1)
        usb.addLayout(usb_device_row)

        self.usb_status_label = QLabel("")
        self.usb_status_label.setWordWrap(True)
        muted_text(self.usb_status_label)
        usb.addWidget(self.usb_status_label)

        self.usb_progress = QProgressBar()
        self.usb_progress.setRange(0, 1000)
        self.usb_progress.setValue(0)
        self.usb_progress.setFormat("0.0%")
        self.usb_progress.setTextVisible(True)
        self.usb_progress.setVisible(False)
        usb.addWidget(self.usb_progress)

        usb_actions = QHBoxLayout()
        usb_actions.setSpacing(8)
        self.usb_create_button = QPushButton("Создать / обновить загрузочную флешку")
        self.usb_create_button.clicked.connect(self._create_usb)
        self.usb_create_button.setEnabled(False)
        usb_actions.addWidget(self.usb_create_button)

        self.usb_reboot_button = QPushButton("Перезагрузиться с флешки")
        self.usb_reboot_button.clicked.connect(self._reboot_usb)
        self.usb_reboot_button.setEnabled(False)
        usb_actions.addWidget(self.usb_reboot_button)
        usb_actions.addStretch(1)
        usb.addLayout(usb_actions)

        self.usb_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        usb_mode_layout.addWidget(
            self.usb_card, 0, Qt.AlignmentFlag.AlignTop
        )
        usb_mode_layout.addStretch(1)
        self.recovery_stack.addWidget(self.usb_mode_page)
        self.recovery_stack.setCurrentWidget(self.local_mode_page)
        QTimer.singleShot(0, self._fit_recovery_stack)

        if not embedded:
            outer.addStretch(1)

    def _fit_recovery_stack(self) -> None:
        """Size the stack to the active card instead of its tallest hidden page."""
        page = self.recovery_stack.currentWidget()
        if page is None:
            return
        if page.layout() is not None:
            page.layout().activate()
        wanted = max(1, page.sizeHint().height())
        self.recovery_stack.setFixedHeight(wanted)

    def ensure_loaded(self) -> None:
        """Full-check once; USB probing starts only when that recovery path is selected."""
        if (
            self._active_mode == "usb"
            and self._usb_scan_worker is None
            and self._usb_action_worker is None
            and not self._usb_devices
        ):
            self._refresh_usb_devices()
        if (
            self._worker is not None
            or self._action_worker is not None
            or self._usb_action_worker is not None
            or self._broken_delete_worker is not None
        ):
            return
        if self._readiness is None:
            self.refresh(auto_prepare=True)
            return
        if self._fingerprint_worker is None:
            self._check_for_changes()

    def _set_recovery_mode(self, mode: str) -> None:
        if mode not in {"local", "usb"}:
            return
        self._active_mode = mode
        self.mode_tabs.blockSignals(True)
        self.mode_tabs.setCurrentIndex(0 if mode == "local" else 1)
        self.mode_tabs.blockSignals(False)
        if mode == "local":
            self.recovery_stack.setCurrentWidget(self.local_mode_page)
        else:
            self.recovery_stack.setCurrentWidget(self.usb_mode_page)
            if (
                self._usb_scan_worker is None
                and self._usb_action_worker is None
                and not self._usb_devices
            ):
                self._refresh_usb_devices()
        self._set_action_availability()
        QTimer.singleShot(0, self._fit_recovery_stack)

    def _manual_refresh(self) -> None:
        self.refresh(auto_prepare=True)

    def _set_busy(self, message: str = "", visible: bool = False) -> None:
        self.progress_label.setText(message)
        self.progress_label.setVisible(visible and bool(message))
        self.progress_bar.setVisible(visible)
        QTimer.singleShot(0, self._fit_recovery_stack)

    def _show_technical_info(self) -> None:
        DetailsDialog(
            "Техническая информация Recovery",
            self._technical_text,
            heading="Техническая информация Recovery",
            note="Подробные данные о состоянии локальной и USB Recovery-среды.",
            parent=self,
        ).exec()














    def _check_for_changes(self) -> None:
        """Check only cheap metadata; run the slow validation only after a change."""
        if self._fingerprint_worker is not None:
            return
        self._fingerprint_worker = _RecoveryFingerprintWorker()
        self._fingerprint_worker.signals.completed.connect(self._fingerprint_checked)
        self._fingerprint_worker.signals.failed.connect(self._fingerprint_failed)
        self._thread_pool.start(self._fingerprint_worker)

    def _fingerprint_checked(self, fingerprint: object) -> None:
        self._fingerprint_worker = None
        value = fingerprint if isinstance(fingerprint, str) and fingerprint else None
        if value is None:
            return
        if self._state_fingerprint is None:
            self._state_fingerprint = value
            return
        if value != self._state_fingerprint:
            self.refresh(auto_prepare=True)

    def _fingerprint_failed(self, _message: str) -> None:
        # Cached readiness stays usable. The explicit “Проверить снова” button
        # remains the authoritative fallback if the lightweight probe is unavailable.
        self._fingerprint_worker = None

    def refresh(self, *, auto_prepare: bool = False) -> None:
        if (
            self._worker is not None
            or self._action_worker is not None
            or self._usb_action_worker is not None
            or self._broken_delete_worker is not None
        ):
            return
        self._auto_prepare_after_check = auto_prepare
        self.refresh_button.setEnabled(False)
        self.reboot_button.setEnabled(False)
        self.summary_title.setText("Проверяю Btrfs, systemd-boot и Recovery-среду…")
        self.summary_text.setVisible(False)
        self.problem_text.setVisible(False)
        self._set_busy("", True)

        self._worker = _RecoveryWorker()
        self._worker.signals.completed.connect(self._loaded)
        self._worker.signals.failed.connect(self._failed)
        self._thread_pool.start(self._worker)

    def _failed(self, message: str) -> None:
        self._worker = None
        self._auto_prepare_after_check = False
        self.summary_title.setText("Не удалось проверить восстановление")
        self.summary_text.setVisible(True)
        self.summary_text.setText("Повторите проверку. Система не изменялась.")
        self.problem_text.setText(message or "Неизвестная ошибка проверки.")
        self.problem_text.setVisible(True)
        self._technical_text = self.problem_text.text()
        self._set_busy("", False)
        self._set_action_availability()

    def _loaded(self, readiness: RecoveryReadiness) -> None:
        auto_prepare = self._auto_prepare_after_check
        self._auto_prepare_after_check = False
        self._worker = None
        self._readiness = readiness
        self._state_fingerprint = readiness.state_fingerprint
        self._helper_available = recovery_helper_available()
        self.summary_text.setVisible(True)
        self.set_checked_at(self.checked_label, readiness.checked_at)
        self._set_busy("", False)
        self._update_technical_text(readiness)

        # The main card shows only actionable problems. Informational notes and
        # previous-restore details live behind the ⓘ / ⚙ controls instead of
        # permanently expanding the screen.
        visible_issues = [
            issue.message
            for issue in readiness.issues
            if issue.code != "recovery-environment" and issue.blocking
        ]

        if not self._helper_available:
            self.summary_title.setText("Компонент восстановления не установлен")
            self.summary_text.setText(
                "Установите системный компонент Stage 7 и повторите проверку."
            )
            visible_issues.insert(
                0,
                "Root-owned helper Arch Manager недоступен.",
            )
        elif readiness.can_boot_recovery:
            self.summary_title.setText("Среда восстановления готова")
            self.summary_text.setText(
                "Готово к перезагрузке. Точку восстановления выберете уже в автономном режиме."
            )
        elif readiness.can_prepare:
            if readiness.previous_restore.marker_found or readiness.previous_restore.report_found:
                self.summary_title.setText("Обновляю Recovery-среду после восстановления…")
                self.summary_text.setText(
                    "После восстановления среда синхронизируется автоматически."
                )
            else:
                self.summary_title.setText("Подготавливаю среду восстановления…")
                self.summary_text.setText(
                    "Arch Manager автоматически подготовит актуальную Recovery-среду."
                )
        elif readiness.partial:
            self.summary_title.setText("Готовность проверена не полностью")
            self.summary_text.setText(
                "Подготовка не запускается, пока не будут подтверждены обязательные условия."
            )
        else:
            self.summary_title.setText("Восстановление пока недоступно")
            self.summary_text.setText(
                "Ниже указано, что мешает безопасно подготовить Recovery-среду."
            )

        if visible_issues:
            self.problem_text.setText("\n".join("• " + item for item in visible_issues))
            self.problem_text.setVisible(True)
        else:
            self.problem_text.setVisible(False)

        self.old_roots_label.setVisible(False)
        if readiness.broken_roots:
            count = len(readiness.broken_roots)
            self.old_roots_button.setToolTip(
                f"Сохранённые прежние системы: {count}. Открыть управление."
            )
        else:
            self.old_roots_button.setToolTip("Сохранённых прежних систем нет")

        self._set_action_availability()

        if (
            auto_prepare
            and self._helper_available
            and readiness.can_prepare
            and not readiness.environment_prepared
        ):
            QTimer.singleShot(0, self._prepare_automatically)

    def _update_technical_text(self, readiness: RecoveryReadiness) -> None:
        def yes_no_unknown(value: bool | None) -> str:
            return "да" if value is True else "нет" if value is False else "не определено"

        technical = [
            f"Корень Btrfs: {yes_no_unknown(readiness.root_is_btrfs)}",
            f"Корневой subvolume @: {yes_no_unknown(readiness.root_layout_ok)}",
            f"Отдельный @snapshots: {yes_no_unknown(readiness.snapshots_layout_ok)}",
            f"systemd-boot: {yes_no_unknown(readiness.systemd_boot_detected)}",
            f"Recovery ISO актуален: {yes_no_unknown(readiness.iso_ready)}",
            f"Boot-файлы Recovery актуальны: {yes_no_unknown(readiness.boot_files_ready)}",
            f"Общая Recovery-среда готова: {yes_no_unknown(readiness.environment_ready)}",
            f"Доступных точек сейчас: {len(readiness.points)}",
        ]
        if readiness.previous_restore.marker_found or readiness.previous_restore.report_found:
            technical.append("Есть сведения о предыдущем восстановлении.")
        if readiness.broken_roots:
            technical.append(
                "Сохранённые прежние корни: " + ", ".join(readiness.broken_roots)
            )
        for note in readiness.notes:
            technical.append(f"Примечание: {note}")
        for issue in readiness.issues:
            if issue.technical:
                technical.append(f"{issue.message} — {issue.technical}")
        self._technical_text = "\n".join(technical)

    def _set_action_availability(self) -> None:
        busy = (
            self._worker is not None
            or self._action_worker is not None
            or self._usb_action_worker is not None
            or self._broken_delete_worker is not None
        )
        readiness = self._readiness
        self.mode_tabs.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.info_button.setEnabled(not busy)
        self.old_roots_button.setEnabled(
            bool(not busy and readiness and readiness.broken_roots and self._helper_available)
        )
        self.reboot_button.setEnabled(
            bool(
                not busy
                and self._helper_available
                and readiness
                and readiness.can_boot_recovery
            )
        )

        usb_scan_busy = self._usb_scan_worker is not None
        device = self._selected_usb_device()
        self.usb_refresh_button.setEnabled(not busy and not usb_scan_busy)
        self.usb_combo.setEnabled(bool(not busy and not usb_scan_busy and self._usb_devices))
        self.usb_create_button.setEnabled(
            bool(
                not busy
                and not usb_scan_busy
                and self._helper_available
                and device is not None
                and self._usb_source_ready()
            )
        )
        self.usb_reboot_button.setEnabled(
            bool(
                not busy
                and not usb_scan_busy
                and self._helper_available
                and device is not None
                and device.recovery_media
                and uefi_usb_bootnext_available()
            )
        )
        QTimer.singleShot(0, self._fit_recovery_stack)

    def _manage_broken_roots(self) -> None:
        readiness = self._readiness
        if readiness is None or not readiness.broken_roots:
            QMessageBox.information(
                self,
                "Сохранённые прежние системы",
                "Сохранённых @.broken-копий сейчас нет.",
            )
            return
        dialog = BrokenRootsDialog(readiness.broken_roots, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        roots = dialog.selected_roots()
        if not roots:
            return
        names = "\n".join(f"• {name}" for name in roots)
        answer = QMessageBox.warning(
            self,
            "Удалить прежнюю систему",
            "Будут безвозвратно удалены выбранные страховочные копии прежнего корня:\n\n"
            f"{names}\n\n"
            "Точки восстановления в @snapshots затронуты не будут. "
            "Продолжайте только если восстановленная система уже несколько раз нормально загружалась и работает.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._delete_broken_roots(roots)

    def _delete_broken_roots(self, roots: tuple[str, ...]) -> None:
        if (
            self._broken_delete_worker is not None
            or self._worker is not None
            or self._action_worker is not None
            or self._usb_action_worker is not None
        ):
            return
        self.summary_title.setText("Удаляю сохранённую прежнюю систему…")
        self.summary_text.setVisible(False)
        self._set_busy(
            "Удаление выполняется только для явно выбранных @.broken-копий. Точки восстановления не изменяются.",
            True,
        )
        self._broken_delete_worker = _BrokenRootsDeleteWorker(roots)
        self._broken_delete_worker.signals.completed.connect(self._broken_roots_deleted)
        self._broken_delete_worker.signals.failed.connect(self._broken_roots_delete_failed)
        self._set_action_availability()
        self._thread_pool.start(self._broken_delete_worker)

    def _broken_roots_deleted(self, result: object) -> None:
        deleted = tuple(getattr(result, "deleted", ()))
        self._broken_delete_worker = None
        self._set_busy("", False)
        QMessageBox.information(
            self,
            "Сохранённая система удалена",
            f"Удалено страховочных копий: {len(deleted)}. Точки восстановления не изменялись.",
        )
        self.refresh(auto_prepare=False)

    def _broken_roots_delete_failed(self, message: str) -> None:
        self._broken_delete_worker = None
        self._set_busy("", False)
        QMessageBox.critical(
            self,
            "Не удалось удалить прежнюю систему",
            message or "Удаление остановлено. Данные не удалялись дальше.",
        )
        self._set_action_availability()

    def _prepare_automatically(self) -> None:
        if (
            self._worker is not None
            or self._action_worker is not None
            or self._usb_action_worker is not None
            or self._broken_delete_worker is not None
        ):
            return
        readiness = self._readiness
        if (
            readiness is None
            or readiness.environment_prepared
            or not readiness.can_prepare
            or not self._helper_available
        ):
            return
        self._run_action("prepare")

    def _reboot(self) -> None:
        readiness = self._readiness
        if readiness is None or not readiness.can_boot_recovery:
            return
        answer = QMessageBox.warning(
            self,
            "Перезагрузить в режим восстановления",
            "Следующая загрузка будет однократно направлена в Arch Manager Recovery.\n\n"
            "Уже после перезагрузки Recovery покажет актуальные точки восстановления. "
            "Вы выберете нужную точку там и отдельно подтвердите применение.\n\n"
            "Перезагрузить сейчас?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_action("reboot")

    def _run_action(self, action: str) -> None:
        if (
            self._action_worker is not None
            or self._worker is not None
            or self._usb_action_worker is not None
            or self._broken_delete_worker is not None
        ):
            return

        if action == "prepare":
            self.summary_title.setText("Подготавливаю Recovery-среду при необходимости…")
            self.summary_text.setVisible(False)
            self._set_busy("", True)
        else:
            self._set_busy(
                "Проверяю общую Recovery-среду и настраиваю однократную загрузку…",
                True,
            )

        self._action_worker = _RecoveryActionWorker(action)
        self._action_worker.signals.completed.connect(self._action_done)
        self._action_worker.signals.failed.connect(self._action_failed)
        self._set_action_availability()
        self._thread_pool.start(self._action_worker)

    def _action_done(self, result: object) -> None:
        action = getattr(result, "action", "")
        self._action_worker = None
        if action == "prepare":
            self._set_busy("", True)
            # Do not auto-prepare again from this verification pass: this prevents
            # a loop if an external condition changes immediately after prepare.
            self.refresh(auto_prepare=False)
        else:
            self._set_busy("", False)
            self._set_action_availability()

    def _action_failed(self, message: str) -> None:
        self._action_worker = None
        self.summary_title.setText("Не удалось подготовить Recovery-среду")
        self.summary_text.setVisible(True)
        self.summary_text.setText(
            "Система не восстанавливалась и не перезагружалась. "
            "Устраните причину и нажмите «Проверить снова»."
        )
        self.problem_text.setText(message)
        self.problem_text.setVisible(True)
        self._set_busy("", False)
        self._set_action_availability()
