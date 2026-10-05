from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from src.app_store.models import Application


ROOT = Path(__file__).resolve().parents[1]
HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_stage31_visual_contract_survives_stage4_package_actions():
    source = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    assert 'self.action_button.setText("Удалить")' in source
    assert 'self.action_button.setText("Установить")' in source
    assert "self.action_button.clicked.connect(self._package_action_requested)" in source
    assert "install_package" not in source
    assert "remove_package" not in source
    assert "subprocess" not in source


def test_stage31_source_contract_has_horizontal_wheel_and_large_viewer():
    source = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    assert "class _HorizontalScreenshotArea" in source
    assert "QEvent.Type.Wheel" in source
    assert "ScrollBarAlwaysOff" in source
    assert "class ScreenshotViewerDialog" in source
    assert "Qt.Key.Key_Left" in source
    assert "Qt.Key.Key_Right" in source
    assert "Qt.Key.Key_F11" in source
    assert "showFullScreen" in source


def test_stage31_source_contract_uses_one_vertical_scroller_for_description():
    source = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    assert "class _AutoHeightTextBrowser" in source
    assert "documentSizeChanged" in source
    assert "event.ignore()" in source
    assert "setMinimumHeight(130)" not in source
    assert "setMaximumHeight(360)" not in source


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage31_installed_action_is_remove_and_short_description_is_compact():
    from PySide6.QtWidgets import QApplication, QTextBrowser
    from src.gui.app_store.details import ApplicationDetailsDialog

    app = QApplication.instance() or QApplication([])
    installed = Application(
        app_id="org.example.Installed",
        package_name="installed",
        name="Installed",
        description="Короткое описание.",
        installed=True,
        installed_version="1.0",
    )
    dialog = ApplicationDetailsDialog(installed)
    dialog.show()
    app.processEvents()
    app.processEvents()

    assert dialog.action_button.text() == "Удалить"
    assert dialog.action_button.isEnabled() is True
    description = dialog.findChild(QTextBrowser, "appStoreDescription")
    assert description is not None
    assert description.verticalScrollBar().maximum() == 0
    assert description.height() < 130

    dialog.close()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage31_not_installed_action_is_install():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.details import ApplicationDetailsDialog

    app = QApplication.instance() or QApplication([])
    available = Application(
        app_id="org.example.Available",
        package_name="available",
        name="Available",
        installed=False,
    )
    dialog = ApplicationDetailsDialog(available)
    assert dialog.action_button.text() == "Установить"
    assert dialog.action_button.isEnabled() is True
    dialog.close()
    dialog.deleteLater()
    app.processEvents()

@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_stage31_action_buttons_use_semantic_colors():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.details import ApplicationDetailsDialog

    app = QApplication.instance() or QApplication([])

    installed = Application(
        app_id="org.example.Installed",
        package_name="installed",
        name="Installed",
        installed=True,
        installed_version="1.0",
    )
    installed_dialog = ApplicationDetailsDialog(installed)
    assert "#d05c5c" in installed_dialog.action_button.styleSheet()
    assert "#1f9d68" in installed_dialog.launch_button.styleSheet()
    installed_dialog.close()
    installed_dialog.deleteLater()

    available = Application(
        app_id="org.example.Available",
        package_name="available",
        name="Available",
        installed=False,
    )
    available_dialog = ApplicationDetailsDialog(available)
    assert "#2f6feb" in available_dialog.action_button.styleSheet()
    available_dialog.close()
    available_dialog.deleteLater()
    app.processEvents()

