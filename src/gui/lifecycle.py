"""Destroy Qt widgets explicitly on the GUI thread, not during Python exit."""
from __future__ import annotations

import gc

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from src.app_store.background import shutdown_background_executors
from src.app_store.package_coordinator import get_package_transaction_coordinator


def package_operation_running() -> bool:
    return get_package_transaction_coordinator().busy


def shutdown_application_widgets() -> None:
    app = QApplication.instance()
    if app is None:
        return
    # Keep widgets and their signal receivers alive until producers finish.
    for widget in app.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
    QThreadPool.globalInstance().waitForDone()
    shutdown_background_executors()
    # No processEvents(): queued completions must not start new inspections.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for widget in app.topLevelWidgets():
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
    gc.collect()
