from pathlib import Path
from tests.source_bundles import restore_points_page_source


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "gui" / "restore_points_page.py"
DIALOG = ROOT / "src" / "gui" / "rename_restore_point_dialog.py"
VERSION = ROOT / "src" / "__init__.py"
README = ROOT / "README.md"
STAGE4 = ROOT / "docs" / "STAGE_4.md"


def test_stage4_7_adds_a_row_actions_menu():
    source = restore_points_page_source(ROOT)
    assert 'QTableWidget(0, 6)' in source
    assert 'actions_button.setText("⋯")' in source
    assert 'actions_menu = QMenu(actions_button)' in source
    assert 'actions_menu.addAction("Переименовать")' in source
    assert 'self.table.setCellWidget(row, 5, actions_button)' in source


def test_stage4_7_rename_remains_in_the_row_menu():
    source = restore_points_page_source(ROOT)
    row_menu_block = source[
        source.index('actions_menu = QMenu(actions_button)'):
        source.index('if selected_id == point.number')
    ]
    assert 'Переименовать' in row_menu_block
    assert '_open_rename_dialog' in source


def test_stage4_7_rename_dialog_uses_validated_contract():
    source = DIALOG.read_text(encoding="utf-8")
    assert 'build_rename_request' in source
    assert 'MAX_DESCRIPTION_LENGTH' in source
    assert 'Snapper ID' in source
    assert 'self.save_button.setText("Сохранить")' in source


def test_stage4_7_rename_runs_through_existing_executor_worker():
    source = restore_points_page_source(ROOT)
    assert 'class _RenameRestorePointWorker(QRunnable)' in source
    assert 'execute_restore_point_action(self.request)' in source
    assert 'self._rename_worker = worker' in source
    assert 'self._current_point_id = result.point_number' in source
    assert 'self._refreshing_after_rename = True' in source


def test_stage4_7_gui_still_does_not_run_privilege_commands_directly():
    source = restore_points_page_source(ROOT)
    forbidden = ('pkexec', 'snapper modify', 'shell=True', 'sudo ')
    assert all(token not in source for token in forbidden)


def test_stage4_7_fixes_broken_plus_glyph_with_theme_icon():
    source = restore_points_page_source(ROOT)
    assert 'QPushButton("Создать точку")' in source
    assert 'QIcon.fromTheme("list-add")' in source
    assert '＋  Создать точку' not in source


def test_stage4_7_docs_keep_delete_and_importance_for_later_steps():
    source = STAGE4.read_text(encoding="utf-8")
    assert 'Шаг 4.7' in source
    assert '## Следующий этап' in source
    assert 'важности' in source
    assert 'удаление' in source


def test_stage4_7_version_bumped():
    assert '__version__ = "0.6.1-stage6"' in VERSION.read_text(encoding="utf-8")
    assert '0.6.1-stage6' in README.read_text(encoding="utf-8")
