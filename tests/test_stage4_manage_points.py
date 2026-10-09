from pathlib import Path
from tests.source_bundles import restore_points_page_source


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "gui" / "restore_points_page.py"
DELETE_DIALOG = ROOT / "src" / "gui" / "delete_restore_point_dialog.py"
VERSION = ROOT / "src" / "__init__.py"
README = ROOT / "README.md"
STAGE4 = ROOT / "docs" / "STAGE_4.md"
STATE = ROOT / "docs" / "CURRENT_STATE.md"


def test_stage4_8_row_menu_completes_point_management_actions():
    source = restore_points_page_source(ROOT)
    block = source[source.index('actions_menu = QMenu(actions_button)'):source.index('if selected_id == point.number')]
    assert 'Переименовать' in block
    assert 'Пометить как важную' in block
    assert 'Убрать из важных' in block
    assert 'actions_menu.addSeparator()' in block
    assert 'Удалить…' in block


def test_stage4_8_importance_uses_validated_contract_and_worker():
    source = restore_points_page_source(ROOT)
    assert 'build_set_importance_request(point.number, wanted)' in source
    assert 'class _SetImportanceWorker(QRunnable)' in source
    assert 'self._importance_worker = worker' in source
    assert 'Остальные userdata Snapper будут сохранены' in source


def test_stage4_8_delete_uses_validated_contract_and_worker():
    source = restore_points_page_source(ROOT)
    assert 'build_delete_request(point.number)' in source
    assert 'class _DeleteRestorePointWorker(QRunnable)' in source
    assert 'self._delete_worker = worker' in source
    assert 'Перечитываю список и пересчитываю нумерацию' in source


def test_stage4_8_delete_requires_explicit_destructive_dialog():
    source = DELETE_DIALOG.read_text(encoding="utf-8")
    assert 'Удалить эту точку восстановления?' in source
    assert 'Удалить точку' in source
    assert 'ButtonRole.DestructiveRole' in source
    assert 'Операцию нельзя отменить из Arch Manager' in source
    assert 'Snapper ID' in source


def test_stage4_8_delete_reselects_neighbour_and_renumbers():
    source = restore_points_page_source(ROOT)
    assert 'self._pending_deleted_row = self.table.currentRow()' in source
    assert 'min(self._pending_deleted_row, self.table.rowCount() - 1)' in source
    assert 'Нумерация списка пересчитана автоматически' in source


def test_stage4_8_empty_state_can_create_first_point_after_last_delete():
    source = restore_points_page_source(ROOT)
    assert 'self.empty_create_button = QPushButton("Создать точку")' in source
    assert 'self.empty_create_button.clicked.connect(self._open_create_dialog)' in source
    assert 'self.empty_create_button.setVisible(True)' in source


def test_stage4_8_gui_still_never_runs_privilege_commands_directly():
    source = restore_points_page_source(ROOT)
    forbidden = ('pkexec', 'snapper modify', 'snapper delete', 'shell=True', 'sudo ')
    assert all(token not in source for token in forbidden)


def test_stage4_8_docs_mark_restore_point_crud_complete():
    combined = '\n'.join(
        p.read_text(encoding="utf-8") for p in (README, STAGE4, STATE)
    )
    assert 'CRUD-управление отдельными точками завершено' in combined
    for word in ('создание', 'переименование', 'важность', 'удаление'):
        assert word in combined


def test_stage4_8_version_bumped():
    assert '__version__ = "0.7.0-beta.1"' in VERSION.read_text(encoding="utf-8")
    assert '0.7.0-beta.1' in README.read_text(encoding="utf-8")


def test_stage4_8_refresh_failure_does_not_claim_successful_mutation_never_happened():
    source = restore_points_page_source(ROOT)
    assert 'action_completed = any(' in source
    assert 'Действие выполнено, но список не обновился' in source
    assert 'Не повторяйте само действие до обновления списка' in source
