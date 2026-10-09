from pathlib import Path
from tests.source_bundles import restore_points_page_source


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "gui" / "restore_points_page.py"
README = ROOT / "README.md"
STAGE4 = ROOT / "docs" / "STAGE_4.md"
STATE = ROOT / "docs" / "CURRENT_STATE.md"
VERSION = ROOT / "src" / "__init__.py"


def test_stage4_6_toolbar_places_create_next_to_refresh():
    source = restore_points_page_source(ROOT)
    assert 'header.addWidget(self.create_button)' in source
    assert 'header.addWidget(self.refresh_button)' in source
    assert 'page_actions = QHBoxLayout()' not in source


def test_stage4_6_progress_card_exists_for_long_running_create():
    source = restore_points_page_source(ROOT)
    assert 'self.operation_card = card_frame()' in source
    assert 'self.operation_progress = QProgressBar()' in source
    assert 'Шаг 1 из 4' in source
    assert 'Шаг 2 из 4' in source
    assert 'Шаг 3 из 4' in source


def test_stage4_6_display_numbers_are_ui_order_not_snapper_ids():
    source = restore_points_page_source(ROOT)
    assert 'QTableWidget(0, 6)' in source
    assert '["№", "Название", "Дата", "Причина", "Тип", ""]' in source
    assert "self.table.setColumnHidden(3, True)" in source
    assert 'str(row + 1)' in source
    assert 'def _display_index_for_point_number' in source
    assert 'Технический Snapper ID:' in source


def test_stage4_6_success_message_uses_inline_feedback_not_modal_popup():
    source = restore_points_page_source(ROOT)
    assert 'Технический Snapper ID:' in source
    assert 'QMessageBox.information' not in source
    assert 'self._set_operation_progress_visible(False)' in source


def test_stage4_6_docs_explain_visual_numbering_policy():
    readme = (ROOT / "docs/README_HISTORY.md").read_text(encoding="utf-8")
    stage4 = STAGE4.read_text(encoding="utf-8")
    state = STATE.read_text(encoding="utf-8")
    combined = readme + "\n" + stage4 + "\n" + state
    assert 'визуальную нумерацию' in combined
    assert 'Snapper ID' in combined


def test_stage4_6_version_bumped():
    assert '__version__ = "0.7.0-beta.1"' in VERSION.read_text(encoding="utf-8")
    assert '0.7.0-beta.1' in README.read_text(encoding="utf-8")
