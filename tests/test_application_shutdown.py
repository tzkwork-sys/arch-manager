import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_window_cannot_close_during_package_operation(monkeypatch):
    from unittest.mock import Mock
    from src.gui import main_window

    monkeypatch.setattr(main_window, "package_operation_running", lambda: True)
    notification = Mock()
    monkeypatch.setattr(main_window.QMessageBox, "information", notification)
    event = Mock()
    main_window.MainWindow.closeEvent(None, event)
    event.ignore.assert_called_once()
    notification.assert_called_once()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_shutdown_waits_for_producers_and_destroys_widgets_before_python_exit(tmp_path):
    script = r'''
import time
from unittest.mock import patch
import shiboken6
from PySide6.QtCore import QTimer, QRunnable, QThreadPool
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow
from src.gui.dashboard import DashboardPage
from src.gui.lifecycle import shutdown_application_widgets
from src.app_store.background import create_background_executor
app = QApplication([])
with patch.object(DashboardPage, 'refresh', lambda self: None):
    window = MainWindow()
    window.show()
    pool = create_background_executor(max_workers=1)
    future = pool.submit(lambda: (time.sleep(0.05), 'finished')[1])
    class Task(QRunnable):
        def run(self):
            time.sleep(0.05)
    QThreadPool.globalInstance().start(Task())
    QTimer.singleShot(10, window.close)
    app.exec()
    shutdown_application_widgets()
    assert future.result() == 'finished'
    assert not shiboken6.isValid(window)
    assert QThreadPool.globalInstance().activeThreadCount() == 0
    shutdown_application_widgets()  # Repeated teardown is safe.
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    for _ in range(3):
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
