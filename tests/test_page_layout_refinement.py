from datetime import datetime
import importlib.util
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def update_page():
    from src.core.updates import UpdateDetails, UpdateItem, UpdateSourceDetails
    from src.gui.updates_page import UpdatesPage
    page = UpdatesPage()
    items = (UpdateItem("alpha", "1", "2", "official", 1024), UpdateItem("beta", "3", "4", "aur"))
    page.apply_shared_details(UpdateDetails(UpdateSourceDetails(items[:1], True), UpdateSourceDetails(items[1:], True), datetime.now()))
    return page, items


def test_package_details_are_lazy_and_follow_selection(app):
    from src.core.updates import PackageInfo
    from PySide6.QtCore import Qt
    page, items = update_page()
    workers = []
    page._thread_pool = SimpleNamespace(start=workers.append)
    page.table.setCurrentCell(0, 1)
    assert workers == []
    assert page.table.columnCount() == 6
    assert not page.package_details_card.isVisible()
    page.package_details_toggle.setChecked(True)
    assert workers[0].item == items[0]
    page.table.setCurrentCell(1, 1)
    info = PackageInfo("alpha", "Описание alpha", "1", "1 МиБ", "example.test", "MIT")
    page._package_info_loaded((items[0], info))
    assert workers[-1].item == items[1]
    assert "Описание alpha" not in page.package_details_text.toPlainText()
    page._package_info_loaded((items[1], PackageInfo("beta", "Описание beta", "3", "2 МиБ", "example.test", "GPL")))
    assert "Описание beta" in page.package_details_text.toPlainText()
    assert page.package_details_text.isReadOnly()
    assert page.package_details_text.maximumHeight() == 180
    page.table.setCurrentCell(0, 1)
    assert "Описание alpha" in page.package_details_text.toPlainText()
    assert len(workers) == 2
    # Source filtering must remove any details for a row no longer present.
    page.source_header._set_source_filter("official")
    assert page._info_item is None or page._info_item.source == "official"
    assert page.table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert not page.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_failed_old_package_request_does_not_replace_new_selection(app):
    page, items = update_page()
    workers = []
    page._thread_pool = SimpleNamespace(start=workers.append)
    page.table.setCurrentCell(0, 1)
    page.package_details_toggle.setChecked(True)
    page.table.setCurrentCell(1, 1)
    page._package_info_failed("old error")
    assert workers[-1].item == items[1]
    assert "old error" not in page.package_details_text.toPlainText()


def test_maintenance_options_are_one_list_with_inline_controls(app):
    from src.core.maintenance_actions import MaintenanceAction
    from src.gui.maintenance_page import MaintenancePage
    page = MaintenancePage()
    option = page.options[MaintenanceAction.PACKAGE_CACHE]
    assert option.parentWidget() is page.options_card
    assert page.cache_keep_combo.parentWidget() is option.details_body
    assert option.details_body.isHidden()
    option.info_button.setChecked(True)
    assert not option.details_body.isHidden()
    option.set_state(enabled=True, value="1 МиБ", details="Останутся 3 версии")
    assert "Останутся 3 версии" in option.details_label.text()
    option.info_button.setChecked(False)
    assert option.details_body.isHidden()
    assert option.checkbox.isChecked()  # Collapsing does not change cleanup selection.


def test_restore_table_keeps_ids_and_full_descriptions(app):
    from PySide6.QtCore import Qt
    from src.core.restore_points import RestorePoint
    from src.gui.restore_points_page import RestorePointsPage
    page = RestorePointsPage(embedded=True)
    point = RestorePoint(42, datetime.now(), "Arch Manager: перед обновлением системы", True, None, "root", "number", "important=yes")
    page._all_points = (point,)
    page._rebuild_table()
    assert page.table.isColumnHidden(3)
    assert page.table.item(0, 1).text() == "перед обновлением системы"
    assert point.description in page.table.item(0, 1).toolTip()
    assert page.table.item(0, 1).data(Qt.ItemDataRole.UserRole) == 42
    assert page.table.item(0, 4).text() == "★ Важная"


def test_recovery_tabs_switch_only_the_visible_mode(app, monkeypatch):
    from src.gui.recovery_page import RecoveryPage
    page = RecoveryPage(embedded=True)
    monkeypatch.setattr(page, "_refresh_usb_devices", lambda: None)
    page.mode_tabs.setCurrentIndex(1)
    assert page._active_mode == "usb"
    assert page.recovery_stack.currentWidget() is page.usb_mode_page
    page.mode_tabs.setCurrentIndex(0)
    assert page.recovery_stack.currentWidget() is page.local_mode_page
