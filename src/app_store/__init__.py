"""Application catalog and package-action domain for Arch Manager.

Qt presentation code intentionally lives under ``src.gui.app_store`` so the
catalog/domain layer stays reusable and isolated from widgets.
"""

from .actions import PackageAction, PackageActionRequest
from .catalog import AppCatalogService, CatalogLoadResult, CatalogStats
from .models import Application

__all__ = [
    "Application",
    "AppCatalogService",
    "CatalogLoadResult",
    "CatalogStats",
    "PackageAction",
    "PackageActionRequest",
]
