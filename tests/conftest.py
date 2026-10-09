"""Keep Qt alive until background producers and test widgets are destroyed."""
import importlib.util

import pytest


@pytest.fixture(scope="session", autouse=True)
def qt_session_lifecycle():
    if importlib.util.find_spec("PySide6") is None:
        yield
        return
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.gui.lifecycle import shutdown_application_widgets

    app = QApplication.instance() or QApplication([])
    yield app
    shutdown_application_widgets()
