from datetime import datetime
import importlib.util
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed"
)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("total", [0, 1, 6, 7, 13, 100])
def test_available_updates_are_not_errors(total):
    from src.core.updates import UpdateSourceSummary, UpdatesSummary
    from src.gui.dashboard import _update_state
    from src.gui.updates_page import _status_for_count

    summary = UpdatesSummary(UpdateSourceSummary(total, True), UpdateSourceSummary(0, True))
    expected = "ok" if total == 0 else "warning"
    assert _update_state(summary)[1] == expected
    assert _status_for_count(total, False)[1] == expected


def test_cards_share_semantic_colors_and_radius(app):
    from src.app_store.models import Application
    from src.gui.app_store.widgets import ApplicationCard, CARD_HIGHLIGHT_RADIUS, INSTALLED_GREEN, UPDATE_AMBER
    from src.gui.dashboard import SummaryCard
    from src.gui.theme import CARD_RADIUS, STATUS_COLORS

    card = ApplicationCard(Application(app_id="example", package_name="example", name="Example", installed=True))
    assert INSTALLED_GREEN == STATUS_COLORS["ok"]
    assert UPDATE_AMBER == STATUS_COLORS["warning"]
    assert CARD_HIGHLIGHT_RADIUS == CARD_RADIUS
    assert STATUS_COLORS["ok"].name() in card.styleSheet()
    summary = SummaryCard("Обновления", "13 обновлений")
    summary.set_content("13 обновлений", "warning")
    assert STATUS_COLORS["warning"].name() in summary.styleSheet()
    assert f"border-radius: {CARD_RADIUS}px" in summary.styleSheet()
    assert summary.title_label.font().bold()
    assert not summary.open_label.font().bold()


def test_overview_shares_header_and_timestamp_position(app, monkeypatch):
    from PySide6.QtCore import QTimer
    from src.gui.dashboard import DashboardPage
    from src.gui.page_base import NavigablePage
    from src.gui.theme import BUTTON_HEIGHT

    monkeypatch.setattr(QTimer, "singleShot", lambda *args: None)
    page = DashboardPage()
    assert isinstance(page, NavigablePage)
    assert page.layout().itemAt(1).widget() is page.checked_label
    assert page.header_actions.itemAt(0).widget() is page.check_button
    assert page.check_button.minimumHeight() == BUTTON_HEIGHT
    assert page.check_button.property("archManagerPrimary") is True
    page.resize(1280, 800)
    page.show()
    # Activate only this layout; processing the global queue can start delayed
    # OS inspections left by other GUI tests.
    page.layout().activate()
    assert all(card.height() >= 142 for card in page._cards.values())
    assert page.updates_card.value_label.isVisible()
    page.hide()
    page.set_checked_at(page.checked_label, datetime(2026, 10, 9, 10, 50))
    assert "09.10.2026, 10:50" in page.checked_label.text()


def test_main_pages_share_spacing_and_primary_actions(app):
    from src.gui.maintenance_page import MaintenancePage
    from src.gui.recovery_center_page import RecoveryCenterPage
    from src.gui.settings_page import SettingsPage
    from src.gui.system_page import SystemPage
    from src.gui.updates_page import UpdatesPage
    from src.gui.theme import PAGE_SPACING

    pages = [UpdatesPage(), RecoveryCenterPage(), MaintenancePage(), SystemPage(), SettingsPage()]
    for page in pages:
        assert page.layout().spacing() == PAGE_SPACING
        assert page.layout().itemAt(1).widget() is page.checked_label
    assert pages[0].install_button.property("archManagerPrimary") is True
    assert pages[0].refresh_button.property("archManagerPrimary") is None
    assert pages[1].restore_points.create_button.property("archManagerPrimary") is True
    assert pages[2].clean_button.property("archManagerPrimary") is True
    assert pages[3].full_button.property("archManagerPrimary") is True
    assert pages[4].apply_policy_button.property("archManagerPrimary") is True
