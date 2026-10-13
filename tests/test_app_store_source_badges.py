from pathlib import Path
import importlib.util
import pytest
from src.app_store.models import Application


def test_shared_source_badge_mapping():
    from src.gui.app_store.widgets import source_badge_info
    cases = [
        (Application('a','a','A',repository='extra'), 'extra'),
        (Application('a','a','A',repository='core'), 'core'),
        (Application('a','a','A',repository='Flatpak · flathub',metadata_source='flatpak:system'), 'Flatpak'),
        (Application('a','a','A',repository='Локальный пакет'), 'Локальный'),
    ]
    for product, source in cases:
        assert source_badge_info(product)[0] == source


@pytest.mark.skipif(importlib.util.find_spec('PySide6') is None, reason='Requires Qt')
def test_all_card_sources_use_same_color():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.widgets import ApplicationCard, SOURCE_BADGE_COLOR
    from src.gui.app_store.aur.widgets import AurPackageCard
    from src.app_store.aur.models import AurPackage
    app = QApplication.instance() or QApplication([])
    records = (
        Application('org.example.App','app','App',repository='extra'),
        Application('flatpak:system:org.example.App','org.example.App','App',repository='Flatpak · flathub',metadata_source='flatpak:system'),
    )
    for record in records:
        card=ApplicationCard(record)
        assert SOURCE_BADGE_COLOR.name() in card.source_badge.styleSheet()
        assert card.source_badge.text() in {'extra','Flatpak'}
        card.deleteLater()
    aur=AurPackageCard(AurPackage(name='demo',package_base='demo',version='1'))
    assert SOURCE_BADGE_COLOR.name() in aur.source_badge.styleSheet()
    aur.deleteLater()
    app.processEvents()
