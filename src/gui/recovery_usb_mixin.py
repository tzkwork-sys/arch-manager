from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.core.recovery_usb import RecoveryUsbDevice, uefi_usb_bootnext_available

from .recovery_workers import _RecoveryUsbActionWorker, _RecoveryUsbScanWorker


class RecoveryUsbMixin:
    """USB Recovery UI behaviour kept separate from the main Recovery page."""
    def _selected_usb_device(self) -> RecoveryUsbDevice | None:
        path = self.usb_combo.currentData()
        if not isinstance(path, str):
            return None
        return self._usb_devices.get(path)
    def _usb_source_ready(self) -> bool:
        readiness = self._readiness
        return bool(
            readiness
            and readiness.root_is_btrfs is True
            and readiness.root_layout_ok is True
            and readiness.snapshots_available is True
            and readiness.snapshots_layout_ok is True
        )
    def _refresh_usb_devices(self) -> None:
        if self._usb_scan_worker is not None or self._usb_action_worker is not None:
            return
        self.usb_refresh_button.setEnabled(False)
        self.usb_combo.setEnabled(False)
        self.usb_status_label.setText("Ищу подключённые USB-накопители…")
        self._usb_scan_worker = _RecoveryUsbScanWorker()
        self._usb_scan_worker.signals.completed.connect(self._usb_devices_loaded)
        self._usb_scan_worker.signals.failed.connect(self._usb_scan_failed)
        self._thread_pool.start(self._usb_scan_worker)
    def _usb_devices_loaded(self, devices: object) -> None:
        previous = self.usb_combo.currentData()
        values = (
            tuple(item for item in devices if isinstance(item, RecoveryUsbDevice))
            if isinstance(devices, (tuple, list))
            else ()
        )
        self._usb_scan_worker = None
        self._usb_devices = {item.path: item for item in values}
        self.usb_combo.blockSignals(True)
        self.usb_combo.clear()
        for item in values:
            self.usb_combo.addItem(item.display_name, item.path)
        if isinstance(previous, str) and previous in self._usb_devices:
            index = self.usb_combo.findData(previous)
            if index >= 0:
                self.usb_combo.setCurrentIndex(index)
        self.usb_combo.blockSignals(False)
        self.usb_combo.setEnabled(bool(values))
        self.usb_refresh_button.setEnabled(True)
        self._update_usb_card()
    def _usb_scan_failed(self, message: str) -> None:
        self._usb_scan_worker = None
        self._usb_devices = {}
        self.usb_combo.clear()
        self.usb_combo.setEnabled(False)
        self.usb_refresh_button.setEnabled(True)
        self.usb_summary_title.setText("Не удалось проверить USB-накопители")
        self.usb_status_label.setText(message or "Повторите поиск USB-накопителей.")
        self._set_action_availability()
    def _usb_selection_changed(self, _index: int) -> None:
        self._update_usb_card()
    def _update_usb_card(self) -> None:
        device = self._selected_usb_device()
        if device is None:
            self.usb_summary_title.setText("Подключите USB-флешку")
            self.usb_status_label.setText(
                "Arch Manager показывает только съёмные диски и USB-накопители, не затрагивая системный диск."
            )
        elif device.recovery_media:
            self.usb_summary_title.setText("Recovery-флешка обнаружена")
            if uefi_usb_bootnext_available():
                self.usb_status_label.setText(
                    "На накопителе обнаружена Arch Manager Recovery. Можно обновить образ или выполнить однократную UEFI-загрузку с этой флешки."
                )
            else:
                self.usb_status_label.setText(
                    "Recovery-флешка готова. Автоматическая загрузка одной кнопкой требует UEFI и efibootmgr; иначе используйте Boot Menu прошивки."
                )
        else:
            self.usb_summary_title.setText("Флешка готова к записи Recovery")
            self.usb_status_label.setText(
                "При создании Recovery-флешки все разделы и данные на выбранном накопителе будут удалены."
            )
        self._set_action_availability()
    def _create_usb(self) -> None:
        device = self._selected_usb_device()
        if device is None or not self._usb_source_ready():
            return
        answer = QMessageBox.warning(
            self,
            "Создать загрузочную Recovery-флешку",
            f"Выбран накопитель:\n\n{device.display_name}\n\n"
            "ВСЕ данные на нём будут безвозвратно удалены. Затем Arch Manager запишет и проверит актуальную автономную Recovery-среду.\n\n"
            "Продолжить?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_usb_action("usb-create", device.path)
    def _reboot_usb(self) -> None:
        device = self._selected_usb_device()
        if device is None or not device.recovery_media or not uefi_usb_bootnext_available():
            return
        answer = QMessageBox.warning(
            self,
            "Перезагрузиться с Recovery-флешки",
            f"Следующая загрузка будет однократно направлена на:\n\n{device.display_name}\n\n"
            "Arch Manager дополнительно проверит, что на флешке записан текущий доверенный Recovery-образ, и только после этого установит UEFI BootNext.\n\n"
            "Оставьте флешку подключённой. Перезагрузить сейчас?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_usb_action("usb-reboot", device.path)
    def _poll_usb_progress(self) -> None:
        try:
            raw = Path("/run/arch-manager/recovery-usb-progress").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return
        if not raw:
            return

        value_text, separator, message = raw.partition("\t")
        if not separator:
            return
        try:
            tenths = max(0, min(1000, int(value_text)))
        except ValueError:
            return

        self.usb_progress.setRange(0, 1000)
        self.usb_progress.setValue(tenths)
        self.usb_progress.setFormat(f"{tenths / 10:.1f}%")
        if message:
            self.usb_status_label.setText(message)
    def _run_usb_action(self, action: str, device: str) -> None:
        if (
            self._usb_action_worker is not None
            or self._action_worker is not None
            or self._worker is not None
            or self._broken_delete_worker is not None
        ):
            return

        self.usb_progress.setVisible(True)
        self.usb_combo.setEnabled(False)
        if action == "usb-create":
            self.usb_progress.setRange(0, 1000)
            self.usb_progress.setValue(0)
            self.usb_progress.setFormat("0.0%")
            self.usb_progress.setTextVisible(True)
            self._usb_progress_timer.start()
            self.usb_summary_title.setText("Создаю Recovery-флешку…")
            self.usb_status_label.setText(
                "0.0% — Подготавливаю актуальный Recovery-образ. Не отключайте флешку."
            )
        else:
            self._usb_progress_timer.stop()
            self.usb_progress.setRange(0, 0)
            self.usb_progress.setTextVisible(False)
            self.usb_summary_title.setText("Готовлю однократную загрузку с USB…")
            self.usb_status_label.setText(
                "Проверяю образ на флешке и настраиваю UEFI BootNext без изменения постоянного порядка загрузки."
            )

        selected = self._usb_devices.get(device)
        if selected is None:
            self._usb_action_failed("Флешка больше не найдена. Обновите список накопителей.")
            return
        self._usb_action_worker = _RecoveryUsbActionWorker(action, device, selected.identity)
        self._usb_action_worker.signals.completed.connect(self._usb_action_done)
        self._usb_action_worker.signals.failed.connect(self._usb_action_failed)
        self._set_action_availability()
        self._thread_pool.start(self._usb_action_worker)
    def _usb_action_done(self, result: object) -> None:
        action = getattr(result, "action", "")
        self._usb_action_worker = None
        self._usb_progress_timer.stop()
        if action == "usb-create":
            self._poll_usb_progress()
            self.usb_progress.setRange(0, 1000)
            self.usb_progress.setValue(1000)
            self.usb_progress.setFormat("100.0%")
            self.usb_status_label.setText(
                "100.0% — Recovery-флешка записана и проверена."
            )
            QMessageBox.information(
                self,
                "Recovery-флешка готова",
                "Загрузочная флешка Arch Manager Recovery создана и прошла контрольную проверку. Её можно использовать как аварийный носитель независимо от локальной Recovery-записи на диске.",
            )
            self.usb_progress.setVisible(False)
            self._refresh_usb_devices()
        else:
            self.usb_progress.setVisible(False)
            self._set_action_availability()
    def _usb_action_failed(self, message: str) -> None:
        self._usb_action_worker = None
        self._usb_progress_timer.stop()
        self.usb_progress.setVisible(False)
        self.usb_summary_title.setText("Операция с Recovery-флешкой остановлена")
        self.usb_status_label.setText(message or "Флешка и система не изменялись дальше.")
        QMessageBox.critical(
            self,
            "Recovery USB",
            message or "Не удалось выполнить действие с Recovery-флешкой.",
        )
        self._refresh_usb_devices()
