from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .gui.main_window import MainWindow
from .gui.lifecycle import shutdown_application_widgets

APP_NAME = "Arch Manager"
ORG_NAME = "ArchManager"
DESKTOP_FILE_ID = "arch-manager-gui"


def _state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "arch-manager"


def configure_logging() -> Path:
    log_dir = _state_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "arch-manager.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return log_file


def install_exception_handler(log_file: Path) -> None:
    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        logging.getLogger(__name__).exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        app = QApplication.instance()
        if app is not None:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Arch Manager")
            box.setText("Произошла непредвиденная ошибка.")
            box.setInformativeText(
                "Приложение записало технические сведения в журнал. "
                "Опасные системные действия не выполнялись."
            )
            box.setDetailedText(f"Журнал: {log_file}\n\n{exc_type.__name__}: {exc_value}")
            box.exec()
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handle_exception


def _icon_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "arch-manager.svg"


def create_application(argv: list[str]) -> QApplication:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(__version__)

    app = QApplication(argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_FILE_ID)

    # Не задаём Fusion/собственную палитру: под KDE приложение получает
    # системный Qt/Breeze-стиль и светлую/тёмную схему автоматически.
    icon_path = _icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    return app


def main() -> int:
    log_file = configure_logging()
    app = create_application(sys.argv)
    install_exception_handler(log_file)

    logging.getLogger(__name__).info("Starting Arch Manager %s", __version__)
    window = MainWindow()
    window.show()
    try:
        return app.exec()
    finally:
        shutdown_application_widgets()
