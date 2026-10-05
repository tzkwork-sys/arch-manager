from __future__ import annotations

import importlib.util
import os

import pytest

from src.app_store.models import Application
from src.app_store.presentation import ApplicationIndex, CatalogQuery

HAS_QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_publisher_is_searchable_in_official_catalog_index():
    app = Application(
        app_id="us.zoom.Zoom",
        package_name="zoom",
        name="Zoom",
        publisher="Zoom Video Communications, Inc.",
    )
    index = ApplicationIndex((app,))
    assert index.filter(CatalogQuery(text="communications")) == (app,)


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_name_sort_promotes_exact_search_hit_before_alphabetical_matches():
    from PySide6.QtWidgets import QApplication
    from src.app_store.aur.models import AurPackage
    from src.gui.app_store.page import AppStorePage

    app = QApplication.instance() or QApplication([])
    page = AppStorePage()
    page.search_edit.setText("zoom")
    page._set_sort_value("name")
    items = (
        AurPackage(name="aimp-skin-zoom", package_base="aimp-skin-zoom", version="1"),
        AurPackage(name="zoom", package_base="zoom", version="1"),
        AurPackage(name="zoom-citrix-plugin", package_base="zoom-citrix-plugin", version="1"),
    )
    sorted_items = page._sort_results(items)
    assert [item.name for item in sorted_items] == ["zoom", "aimp-skin-zoom", "zoom-citrix-plugin"]
    page.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_official_card_shows_publisher_without_growing_card_height():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.widgets import ApplicationCard, CARD_HEIGHT

    app = QApplication.instance() or QApplication([])
    card = ApplicationCard(
        Application(
            app_id="us.zoom.Zoom",
            package_name="zoom",
            name="Zoom",
            summary="Video Conferencing and Web Conferencing Service",
            publisher="Zoom Video Communications, Inc.",
        )
    )
    assert card.height() == CARD_HEIGHT
    assert not card.publisher_label.isHidden()
    assert "Zoom Video Communications" in card.publisher_label.full_text
    card.deleteLater()


@pytest.mark.skipif(not HAS_QT, reason="PySide6 is not installed in the artifact test environment")
def test_aur_card_hides_maintainer_but_keeps_out_of_date_warning():
    from PySide6.QtWidgets import QApplication, QLabel
    from src.app_store.aur.models import AurPackage
    from src.gui.app_store.aur.widgets import AurPackageCard

    app = QApplication.instance() or QApplication([])
    card = AurPackageCard(
        AurPackage(
            name="zoom",
            package_base="zoom",
            version="1.0-1",
            maintainer="example-maintainer",
            out_of_date=1,
        )
    )
    labels = [label.text() for label in card.findChildren(QLabel)]
    visible_text = " ".join(labels)
    assert "Сопровождает" not in visible_text
    assert "example-maintainer" not in visible_text
    assert "Устарел" in visible_text
    card.deleteLater()
