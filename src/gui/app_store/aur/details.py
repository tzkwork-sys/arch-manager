from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app_store.aur.errors import (
    AurBuildToolsUnavailable,
    AurHelperUnavailable,
    AurPackageAlreadyInstalled,
    AurPackageNotAur,
    AurPackageNotFound,
    AurPackageNotInstalled,
    AurSystemUpdateRequired,
    AurTransactionBusy,
    AurUnavailable,
)
from src.app_store.aur.models import AurPackage
from src.app_store.aur.planner import (
    AurInstallPlan,
    AurInstallPlanner,
    AurRemovePlan,
    AurRemovePlanner,
    AurUpdatePlan,
    AurUpdatePlanner,
)
from src.app_store.aur.service import AurService
from src.core.activity_log import append_activity
from src.core.update_state import get_update_state_service

from ...theme import muted_text, style_semantic_button
from .terminal import AurTerminalProcess, AurTerminalResult
from .widgets import AUR_BADGE_COLOR, AUR_WARNING_COLOR


class _AurActionSignals(QObject):
    planned = Signal(object, int)
    plan_failed = Signal(object, int)
    local_refreshed = Signal(object, int)
    local_refresh_failed = Signal(object, int)


def _date_text(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp).astimezone().strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return None


def _safe_url(value: str | None) -> QUrl | None:
    if not value:
        return None
    url = QUrl(value)
    if not url.isValid() or url.scheme().lower() not in {"http", "https"} or not url.host():
        return None
    return url


class AurPackageDetailsDialog(QDialog):
    """AUR metadata plus the Stage 7.3 interactive install workflow."""

    updates_requested = Signal()

    def __init__(
        self,
        package: AurPackage,
        parent: QWidget | None = None,
        *,
        aur_service: AurService | None = None,
        planner: AurInstallPlanner | None = None,
        remove_planner: AurRemovePlanner | None = None,
        update_planner: AurUpdatePlanner | None = None,
    ) -> None:
        super().__init__(parent)
        self.package = package
        self.aur_service = aur_service or AurService()
        self.planner = planner or AurInstallPlanner(self.aur_service)
        self.remove_planner = remove_planner or AurRemovePlanner(self.aur_service)
        self.update_planner = update_planner or AurUpdatePlanner(self.aur_service)
        self.package_state_changed = False
        self._busy = False
        self._generation = 0
        self._pending_action: str | None = None
        self._last_terminal_result: AurTerminalResult | None = None

        self._signals = _AurActionSignals(self)
        self._signals.planned.connect(self._action_plan_ready)
        self._signals.plan_failed.connect(self._action_plan_failed)
        self._signals.local_refreshed.connect(self._local_state_refreshed)
        self._signals.local_refresh_failed.connect(self._local_state_refresh_failed)

        self._terminal = AurTerminalProcess(self)
        self._terminal.completed.connect(self._terminal_completed)
        self._terminal.failed.connect(self._terminal_failed)

        self.setObjectName("appStoreAurDetailsDialog")
        self.setWindowTitle(f"{package.name} — AUR")
        self.setModal(True)
        self.resize(800, 680)
        self.setMinimumSize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(15)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._build_header()
        self._build_summary()
        self._build_details()
        self._build_dependencies()
        self._build_links()
        self.body_layout.addStretch(1)

        self.action_note = QLabel("")
        self.action_note.setObjectName("appStoreAurInstallNotice")
        self.action_note.setWordWrap(True)
        muted_text(self.action_note)
        root.addWidget(self.action_note)

        self.operation_label = QLabel("")
        self.operation_label.setObjectName("appStoreAurOperationStatus")
        self.operation_label.setWordWrap(True)
        muted_text(self.operation_label)
        root.addWidget(self.operation_label)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.accept)
        close_row.addWidget(self.close_button)
        root.addLayout(close_row)
        self._refresh_action_controls()

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        label.setFont(font)
        return label

    def _build_header(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(14)

        icon_label = QLabel()
        icon_label.setFixedSize(84, 84)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QIcon.fromTheme("package-x-generic")
        if icon.isNull():
            icon = QIcon.fromTheme("application-x-executable")
        pixmap = icon.pixmap(76, 76)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap)
        else:
            icon_label.setText("AUR")
        row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        title = QLabel(self.package.name)
        title.setWordWrap(True)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 6)
        title.setFont(title_font)
        text.addWidget(title)

        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        source = QLabel("Источник: AUR")
        source.setObjectName("appStoreAurDetailsBadge")
        source.setStyleSheet(f"color: {AUR_BADGE_COLOR.name()}; font-weight: 700;")
        badges.addWidget(source)
        self.version_label = QLabel(self.package.version)
        muted_text(self.version_label)
        badges.addWidget(self.version_label)
        badges.addStretch(1)
        text.addLayout(badges)

        if self.package.out_of_date is not None:
            warning = QLabel("⚠ Пакет помечен устаревшим в AUR")
            warning.setStyleSheet(f"color: {AUR_WARNING_COLOR.name()}; font-weight: 600;")
            text.addWidget(warning)
        elif self.package.orphaned:
            warning = QLabel("Сопровождающий не назначен (orphan)")
            muted_text(warning)
            text.addWidget(warning)

        row.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(7)
        self.install_button = QPushButton()
        self.install_button.setObjectName("appStoreAurInstallButton")
        self.install_button.setMinimumWidth(130)
        self.install_button.clicked.connect(self._package_action_requested)
        actions.addWidget(self.install_button)
        self.remove_button = QPushButton("Удалить")
        self.remove_button.setObjectName("appStoreAurRemoveButton")
        self.remove_button.setMinimumWidth(130)
        self.remove_button.clicked.connect(self._remove_requested)
        self.remove_button.setVisible(False)
        actions.addWidget(self.remove_button)

        style_semantic_button(self.remove_button, "remove")
        actions.addStretch(1)
        row.addLayout(actions)

        self.body_layout.addLayout(row)

        community = QLabel(
            "Пакет поддерживается сообществом AUR и не является пакетом официальных репозиториев Arch Linux."
        )
        community.setObjectName("appStoreAurCommunityNote")
        community.setWordWrap(True)
        muted_text(community)
        self.body_layout.addWidget(community)

    def _build_summary(self) -> None:
        if not self.package.description:
            return
        self.body_layout.addWidget(self._section_title("Описание"))
        text = QLabel(self.package.description)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_layout.addWidget(text)

    def _detail_rows(self) -> list[tuple[str, str]]:
        package = self.package
        details: list[tuple[str, str]] = [
            ("Пакет", package.name),
            ("Package base", package.package_base),
            ("Версия", package.version),
            ("Сопровождающий", package.maintainer or "Не назначен"),
            ("Голоса", str(package.votes)),
            ("Популярность", f"{package.popularity:.2f}"),
        ]
        if package.licenses:
            details.append(("Лицензия", ", ".join(package.licenses)))
        first = _date_text(package.first_submitted)
        modified = _date_text(package.last_modified)
        outdated = _date_text(package.out_of_date)
        if first:
            details.append(("Впервые опубликован", first))
        if modified:
            details.append(("Последнее изменение", modified))
        if outdated:
            details.append(("Помечен устаревшим", outdated))
        if not package.local_state_known:
            details.append(("Состояние", "Локальное состояние недоступно"))
        elif package.installed:
            state = "Установлено"
            if package.update_available:
                state += " · доступно обновление"
            details.append(("Состояние", state))
            if package.installed_version:
                details.append(("Установленная версия", package.installed_version))
        return details

    def _build_details(self) -> None:
        self.body_layout.addWidget(self._section_title("Сведения AUR"))
        self.details_grid = QGridLayout()
        self.details_grid.setHorizontalSpacing(24)
        self.details_grid.setVerticalSpacing(8)
        self._rebuild_details_grid()
        self.details_grid.setColumnStretch(1, 1)
        self.body_layout.addLayout(self.details_grid)

    def _rebuild_details_grid(self) -> None:
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for row, (key, value) in enumerate(self._detail_rows()):
            key_label = QLabel(key)
            muted_text(key_label)
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.details_grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            self.details_grid.addWidget(value_label, row, 1)

    def _build_dependencies(self) -> None:
        sections = (
            ("Зависимости", self.package.depends),
            ("Зависимости сборки", self.package.make_depends),
            ("Зависимости проверки", self.package.check_depends),
            ("Необязательные зависимости", self.package.opt_depends),
            ("Предоставляет", self.package.provides),
            ("Конфликтует", self.package.conflicts),
        )
        populated = [(title, values) for title, values in sections if values]
        if not populated:
            return
        self.body_layout.addWidget(self._section_title("Пакетные связи"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        for row, (title, values) in enumerate(populated):
            key_label = QLabel(title)
            muted_text(key_label)
            value_label = QLabel(", ".join(values))
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
        grid.setColumnStretch(1, 1)
        self.body_layout.addLayout(grid)

    def _build_links(self) -> None:
        links = (
            ("Сайт проекта", self.package.upstream_url),
            ("Страница в AUR", self.package.aur_url),
        )
        valid = [(label, value) for label, value in links if _safe_url(value) is not None]
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

    def _refresh_action_controls(self) -> None:
        self.remove_button.setVisible(False)
        if not self.package.local_state_known:
            self.install_button.setText("Состояние недоступно")
            self.install_button.setEnabled(False)
            style_semantic_button(self.install_button, "update")
            self.install_button.setToolTip(
                "Не удалось прочитать локальное состояние pacman. Обновите каталог и повторите проверку."
            )
            self.action_note.setText(
                "AUR-метаданные доступны, но локальное состояние пакета сейчас определить не удалось. "
                "Установка и удаление временно заблокированы, чтобы не показывать ложное состояние."
            )
        elif self.package.installed and self.package.update_available:
            self.install_button.setText("Перейти к обновлениям")
            self.install_button.setEnabled(not self._busy)
            style_semantic_button(self.install_button, "update")
            self.install_button.setToolTip("Открыть общий раздел обновлений Arch Manager")
            self.remove_button.setVisible(True)
            self.remove_button.setEnabled(not self._busy)
            self.remove_button.setToolTip("Удалить установленный AUR-пакет")
            self.action_note.setText(
                "Доступна новая версия AUR. Установить её можно в общем разделе «Обновления»."
            )
        elif self.package.installed:
            self.install_button.setText("Удалить через yay")
            self.install_button.setEnabled(not self._busy)
            style_semantic_button(self.install_button, "remove")
            self.install_button.setToolTip("Открыть интерактивное удаление AUR в терминале")
            self.action_note.setText(
                "Удаление AUR выполняется через yay -Rns. После основного удаления Arch Manager "
                "очистит кэш сборки yay этого пакета, проверит новые orphan-зависимости и отдельно "
                "предложит очистить только их."
            )
        else:
            self.install_button.setText("Установить через yay")
            self.install_button.setEnabled(not self._busy)
            style_semantic_button(self.install_button, "install")
            self.install_button.setToolTip("Открыть интерактивную установку AUR в терминале")
            self.action_note.setText(
                "Установка AUR выполняется в отдельном терминале через yay. "
                "Вы увидите вопросы yay и сможете проверить PKGBUILD/diff перед продолжением."
            )
        self.close_button.setEnabled(not self._terminal.running)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        if text:
            self.operation_label.setText(text)
        self._refresh_action_controls()

    @Slot()
    def _package_action_requested(self) -> None:
        if self._busy or not self.package.local_state_known:
            return
        if self.package.installed and self.package.update_available:
            self.accept()
            self.updates_requested.emit()
            return
        self._generation += 1
        generation = self._generation
        if self.package.installed:
            self._pending_action = "remove"
            self._set_busy(True, "Проверяю установленный AUR-пакет и состояние менеджера пакетов…")
            future = self.remove_planner.plan_async(self.package)
        else:
            self._pending_action = "install"
            self._set_busy(True, "Проверяю AUR-пакет и состояние системы…")
            future = self.planner.plan_async(self.package.name)
        future.add_done_callback(lambda done, g=generation: self._plan_future_done(done, g))

    @Slot()
    def _remove_requested(self) -> None:
        if self._busy or not self.package.local_state_known or not self.package.installed:
            return
        self._generation += 1
        generation = self._generation
        self._pending_action = "remove"
        self._set_busy(True, "Проверяю установленный AUR-пакет и состояние менеджера пакетов…")
        future = self.remove_planner.plan_async(self.package)
        future.add_done_callback(lambda done, g=generation: self._plan_future_done(done, g))

    # Kept as a small compatibility shim for Stage 7.3 tests/callers.
    @Slot()
    def _install_requested(self) -> None:
        if not self.package.installed:
            self._package_action_requested()

    def _plan_future_done(self, future: Future, generation: int) -> None:
        try:
            plan = future.result()
        except Exception as exc:
            self._signals.plan_failed.emit(exc, generation)
        else:
            self._signals.planned.emit(plan, generation)

    @Slot(object, int)
    def _action_plan_ready(self, plan: object, generation: int) -> None:
        if generation != self._generation:
            return
        if isinstance(plan, AurInstallPlan):
            action = "install"
            confirmed = self._confirm_install(plan)
        elif isinstance(plan, AurRemovePlan):
            action = "remove"
            confirmed = self._confirm_remove(plan)
        elif isinstance(plan, AurUpdatePlan):
            action = "update"
            confirmed = self._confirm_update(plan)
        else:
            return

        if not confirmed:
            labels = {"install": "Установка", "remove": "Удаление", "update": "Обновление"}
            text = f"{labels.get(action, 'Операция')} отменено до запуска терминала."
            self._pending_action = None
            self._set_busy(False, text)
            return

        launcher = Path(__file__).resolve().parents[4] / "scripts" / "run-aur-terminal.sh"
        try:
            self._terminal.start(
                launcher,
                package_name=plan.package.name,
                action=action,
            )
        except Exception as exc:
            self._set_busy(False)
            self._show_action_error(exc, action=action)
            self._pending_action = None
            return
        self.package = plan.package
        if action == "install":
            text = "Установка открыта в терминале. Завершите вопросы yay в окне терминала."
        elif action == "update":
            text = "Обновление открыто в терминале. Проверьте PKGBUILD/diff и вопросы yay."
        else:
            text = "Удаление открыто в терминале. Проверьте список пакетов и ответьте на вопрос yay."
        self._set_busy(True, text)

    def _confirm_install(self, plan: AurInstallPlan) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Установка из AUR")
        box.setText(f"Установить «{plan.package.name}» из AUR?")
        info = [
            "AUR-пакет собирается по PKGBUILD, опубликованному сообществом.",
            "Установка откроется в терминале: проверяйте вопросы yay и изменения перед подтверждением.",
        ]
        info.extend(plan.warnings)
        box.setInformativeText("\n".join(info))
        box.setDetailedText("Команда: " + " ".join(plan.command_preview))
        accept = box.addButton("Открыть терминал и установить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _confirm_update(self, plan: AurUpdatePlan) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Обновление AUR-пакета")
        old = plan.package.installed_version or "—"
        box.setText(f"Обновить «{plan.package.name}» {old} → {plan.package.version}?")
        info = [
            "Обновление выполняется из AUR в отдельном терминале.",
            "Проверяйте вопросы yay, PKGBUILD/diff и изменения перед подтверждением.",
        ]
        info.extend(plan.warnings)
        box.setInformativeText("\n".join(info))
        box.setDetailedText("Команда: " + " ".join(plan.command_preview))
        accept = box.addButton("Открыть терминал и обновить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _confirm_remove(self, plan: AurRemovePlan) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Удаление AUR-пакета")
        box.setText(f"Удалить «{plan.package.name}»?")
        info = [
            "Удаление откроется в терминале через yay -Rns.",
            "После удаления Arch Manager сравнит orphan-пакеты с состоянием до операции. Если именно из-за удаления появились новые ненужные пакеты (например, отдельный *-debug), yay покажет второй список и попросит подтверждение их очистки.",
            "Старые orphan-пакеты, существовавшие до этой операции, автоматически не затрагиваются.",
            "Пакетные файлы удаляются через pacman/yay; кэш сборки yay выбранного пакета очищается автоматически; пользовательские данные в домашней папке не удаляются.",
            "Если любой список выглядит неожиданно, ответьте No.",
        ]
        info.extend(plan.warnings)
        box.setInformativeText("\n".join(info))
        box.setDetailedText("Команда: " + " ".join(plan.command_preview))
        accept = box.addButton("Открыть терминал и удалить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    @Slot(object, int)
    def _action_plan_failed(self, error: object, generation: int) -> None:
        if generation != self._generation or not isinstance(error, Exception):
            return
        action = self._pending_action or "install"
        self._pending_action = None
        self._set_busy(False)
        self._show_action_error(error, action=action)
        if isinstance(error, (AurPackageNotInstalled, AurPackageNotAur)):
            # A planner mismatch means the card is stale compared with pacman.
            # Reconcile immediately instead of asking the user to refresh a
            # stale list manually.
            self._set_busy(True, "Обновляю фактическое состояние пакета…")
            self._refresh_local_state()

    def _show_action_error(self, error: Exception, *, action: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        updates_button = None
        if isinstance(error, AurSystemUpdateRequired):
            box.setWindowTitle("Сначала обновите систему")
            verb = "обновлением" if action == "update" else "установкой"
            box.setText(f"Перед {verb} AUR-пакетов сначала выполните полное системное обновление.")
            updates_button = box.addButton("Перейти к системным обновлениям", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurHelperUnavailable):
            box.setWindowTitle("AUR недоступен")
            box.setText("yay не найден или не запускается. Установите/исправьте yay и повторите попытку.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurBuildToolsUnavailable):
            box.setWindowTitle("Не хватает инструментов AUR")
            box.setText("Для сборки AUR нужны makepkg, git и установленный base-devel.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurTransactionBusy):
            box.setWindowTitle("Менеджер пакетов занят")
            verb = {"remove": "удаление", "update": "обновление"}.get(action, "установку")
            box.setText(f"Другая пакетная операция уже выполняется. Завершите её и повторите {verb}.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurPackageNotFound):
            box.setWindowTitle("Пакет больше не найден")
            box.setText("Точный пакет больше не подтверждается в AUR. Обновите поиск и выберите пакет заново.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurPackageAlreadyInstalled):
            box.setWindowTitle("Пакет уже установлен")
            box.setText(
                "Пакет с таким именем уже установлен в системе. "
                "Arch Manager не заменяет существующий пакет AUR-версией автоматически."
            )
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurPackageNotInstalled):
            box.setWindowTitle("Пакет уже не установлен")
            box.setText("Пакет больше не найден в локальной базе pacman. Arch Manager обновит состояние карточки автоматически.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurPackageNotAur):
            box.setWindowTitle("Источник пакета изменился")
            box.setText(
                "Пакет установлен, но больше не определяется как foreign/AUR. "
                "Arch Manager не будет удалять его через AUR workflow; состояние карточки будет обновлено автоматически."
            )
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        elif isinstance(error, AurUnavailable):
            box.setWindowTitle("AUR-операция недоступна")
            box.setText(str(error))
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        else:
            box.setWindowTitle("Не удалось начать AUR-операцию")
            box.setText(str(error) or "Не удалось открыть AUR-операцию.")
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        detail = getattr(error, "detail", "")
        if detail:
            box.setDetailedText(str(detail)[:4000])
        box.exec()
        if updates_button is not None and box.clickedButton() is updates_button:
            self.accept()
            self.updates_requested.emit()

    # Stage 7.3 compatibility wrapper.
    def _show_install_error(self, error: Exception) -> None:
        self._show_action_error(error, action="install")

    @Slot(object)
    def _terminal_completed(self, result: object) -> None:
        if not isinstance(result, AurTerminalResult):
            return
        self._last_terminal_result = result
        self._pending_action = result.action
        action = result.action
        state = result.state
        action_ru = {"remove": "Удаление", "update": "Обновление"}.get(action, "Установка")
        if state == "success":
            append_activity(
                "app-store",
                action,
                "success",
                f"{self.package.name} ({self.package.name}) · AUR",
                data={
                    "application_name": self.package.name,
                    "package_name": self.package.name,
                    "source": "aur",
                },
            )
            get_update_state_service().invalidate()
            self.operation_label.setText(f"{action_ru} завершено. Проверяю локальное состояние…")
        elif state == "cleanup-incomplete" and action == "remove":
            append_activity(
                "app-store",
                action,
                "partial",
                result.detail or "AUR-пакет удалён, но дополнительная очистка orphan-зависимостей или кэша yay не завершена",
                data={
                    "application_name": self.package.name,
                    "package_name": self.package.name,
                    "source": "aur",
                },
            )
            get_update_state_service().invalidate()
            self.operation_label.setText(
                "Пакет удалён, но дополнительная очистка orphan-зависимостей или кэша yay завершена не полностью. Проверяю состояние…"
            )
        elif state in {"cancelled", "interrupted"}:
            append_activity(
                "app-store",
                action,
                "cancelled",
                result.detail or f"{action_ru} AUR отменено",
                data={
                    "application_name": self.package.name,
                    "package_name": self.package.name,
                    "source": "aur",
                },
            )
            self.operation_label.setText(f"{action_ru} отменено или терминал был закрыт.")
        elif state == "busy":
            self.operation_label.setText(f"Менеджер пакетов оказался занят. {action_ru} не запускалось.")
        elif state == "not-installed":
            self.operation_label.setText("Пакет уже не установлен. Обновляю локальное состояние…")
        elif state == "source-changed":
            self.operation_label.setText("Источник пакета изменился. Обновляю локальное состояние…")
        else:
            append_activity(
                "app-store",
                action,
                "failed",
                result.detail or f"yay:{result.return_code}",
                data={
                    "application_name": self.package.name,
                    "package_name": self.package.name,
                    "source": "aur",
                },
            )
            self.operation_label.setText(
                f"{action_ru} завершилось ошибкой или было отклонено в yay. Проверяю фактическое состояние пакета…"
            )
        self._refresh_local_state()

    @Slot(str)
    def _terminal_failed(self, message: str) -> None:
        action = self._pending_action or "install"
        append_activity(
            "app-store",
            action,
            "failed",
            message,
            data={
                "application_name": self.package.name,
                "package_name": self.package.name,
                "source": "aur",
            },
        )
        self._pending_action = None
        self._set_busy(False, "Не удалось открыть или отследить AUR-терминал.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Ошибка AUR-терминала")
        box.setText("Не удалось завершить терминальный workflow AUR-операции.")
        if message:
            box.setDetailedText(message[:4000])
        box.exec()

    def _refresh_local_state(self) -> None:
        generation = self._generation
        future = self.aur_service.refresh_local_state_async(self.package)
        future.add_done_callback(lambda done, g=generation: self._local_future_done(done, g))

    def _local_future_done(self, future: Future, generation: int) -> None:
        try:
            package = future.result()
        except Exception as exc:
            self._signals.local_refresh_failed.emit(exc, generation)
        else:
            self._signals.local_refreshed.emit(package, generation)

    @Slot(object, int)
    def _local_state_refreshed(self, package: object, generation: int) -> None:
        if generation != self._generation or not isinstance(package, AurPackage):
            return
        if not package.local_state_known:
            self.package = package
            if (
                self._last_terminal_result is not None
                and self._last_terminal_result.state in {"success", "cleanup-incomplete"}
            ):
                self.package_state_changed = True
            self.version_label.setText(package.version)
            self._rebuild_details_grid()
            self._pending_action = None
            local_state_message = (
                "Операция завершена, но локальное состояние пакета сейчас недоступно. Обновите каталог."
                if (
                    self._last_terminal_result is not None
                    and self._last_terminal_result.state in {"success", "cleanup-incomplete"}
                )
                else "Не удалось перечитать локальное состояние пакета. Обновите каталог."
            )
            self._set_busy(False, local_state_message)
            return

        previously_installed = self.package.installed
        self.package = package
        if package.installed != previously_installed:
            self.package_state_changed = True

        result = self._last_terminal_result
        if result is not None and result.action == "install":
            if result.state == "success" and package.installed:
                version = package.installed_version or package.version
                self.operation_label.setText(f"✓ Установлено: {version}")
                self.package_state_changed = True
            elif result.state == "success" and not package.installed:
                self.operation_label.setText(
                    "yay сообщил об успехе, но пакет не найден в локальном состоянии. Обновите каталог и проверьте терминал."
                )
        elif result is not None and result.action == "update":
            if result.state == "success" and package.installed:
                version = package.installed_version or package.version
                if not package.update_available:
                    self.operation_label.setText(f"✓ Обновлено: {version}")
                else:
                    self.operation_label.setText(
                        f"Обновление завершено ({version}), но AUR всё ещё сообщает более новую версию."
                    )
                self.package_state_changed = True
            elif not package.installed:
                self.operation_label.setText(
                    "После обновления пакет больше не найден локально. Обновите каталог и проверьте терминал."
                )
                self.package_state_changed = True

        elif result is not None and result.action == "remove":
            if not package.installed:
                if result.state == "success":
                    self.operation_label.setText("✓ AUR-пакет, его кэш yay и новые ненужные зависимости удалены")
                elif result.state == "cleanup-incomplete":
                    self.operation_label.setText(
                        "✓ AUR-пакет удалён. Дополнительная очистка завершена не полностью; подробности есть в журнале и терминале."
                    )
                else:
                    self.operation_label.setText(
                        "Пакет больше не определяется как установленный AUR-пакет. Каталог будет обновлён."
                    )
                self.package_state_changed = True
            elif result.state == "success":
                self.operation_label.setText(
                    "yay сообщил об успехе, но пакет всё ещё установлен. Проверьте вывод терминала."
                )
            else:
                version = package.installed_version or package.version
                self.operation_label.setText(
                    f"Пакет остался установлен ({version}). Удаление не отмечено как успешное."
                )

        self.version_label.setText(package.version)
        self._rebuild_details_grid()
        self._pending_action = None
        self._set_busy(False)

    @Slot(object, int)
    def _local_state_refresh_failed(self, error: object, generation: int) -> None:
        if generation != self._generation:
            return
        self._pending_action = None
        self._set_busy(False)
        if self._last_terminal_result is not None and self._last_terminal_result.state == "success":
            self.package_state_changed = True
            action = {"remove": "Удаление", "update": "Обновление"}.get(self._last_terminal_result.action, "Установка")
            self.operation_label.setText(
                f"{action} завершено, но Arch Manager не смог сразу перечитать локальное состояние. Обновите каталог."
            )

    @Slot()
    def _open_url(self, value: str | None) -> None:
        url = _safe_url(value)
        if url is not None:
            QDesktopServices.openUrl(url)

    def reject(self) -> None:
        if self._terminal.running:
            self.operation_label.setText(
                "Сначала завершите AUR-операцию в терминале. Это окно отслеживает её итог."
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._terminal.running:
            event.ignore()
            self.operation_label.setText(
                "Сначала завершите AUR-операцию в терминале. Это окно отслеживает её итог."
            )
            return
        super().closeEvent(event)
