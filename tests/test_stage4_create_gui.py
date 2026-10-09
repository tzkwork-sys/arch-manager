from pathlib import Path
from tests.source_bundles import restore_points_page_source


ROOT = Path(__file__).resolve().parents[1]
DIALOG = ROOT / "src" / "gui" / "create_restore_point_dialog.py"
PAGE = ROOT / "src" / "gui" / "restore_points_page.py"


def test_stage4_5_create_dialog_and_page_exist():
    assert DIALOG.is_file()
    assert PAGE.is_file()


def test_stage4_5_dialog_uses_existing_validated_action_contract():
    source = DIALOG.read_text(encoding="utf-8")
    assert "build_create_request" in source
    assert "MAX_DESCRIPTION_LENGTH" in source
    assert 'QLineEdit("Ручная точка")' in source
    assert 'QCheckBox("Пометить как важную")' in source
    assert 'self.create_button.setText("Создать")' in source


def test_stage4_5_gui_keeps_create_flow_through_its_dialog():
    source = restore_points_page_source(ROOT)
    assert "Создать точку" in source
    assert "CreateRestorePointDialog" in source
    assert "_CreateRestorePointWorker" in source


def test_stage4_5_gui_uses_executor_in_worker_not_system_commands_directly():
    source = restore_points_page_source(ROOT)
    assert "execute_restore_point_action(self.request)" in source
    assert "class _CreateRestorePointWorker(QRunnable)" in source
    forbidden = (
        "pkexec",
        "snapper create",
        "snapper delete",
        "snapper modify",
        "shell=True",
        "sudo ",
    )
    assert all(token not in source for token in forbidden)


def test_stage4_5_gui_handles_cancel_and_privilege_errors_separately():
    source = restore_points_page_source(ROOT)
    assert "RestorePointActionCancelled" in source
    assert "RestorePointAuthorizationError" in source
    assert "RestorePointHelperUnavailable" in source
    assert "RestorePointActionTimedOut" in source
    assert "RestorePointActionFailed" in source
    assert "Создание отменено" in source


def test_stage4_5_success_selects_created_point_and_refreshes_list():
    source = restore_points_page_source(ROOT)
    assert "self._current_point_id = result.point_number" in source
    assert "self._loaded_once = False" in source
    assert "self.refresh()" in source
    assert "Точка восстановления создана и добавлена в список" in source
    assert "QMessageBox.information" not in source


def test_stage4_5_restore_point_page_no_longer_claims_read_only_mode():
    source = restore_points_page_source(ROOT)
    assert "Удалить выбранные" in source
    assert "На этом экране Arch Manager только читает" not in source


def test_stage4_5_version_marks_first_mutating_stage4_gui():
    version_source = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '__version__ = "0.7.0-beta.1"' in version_source
    assert "0.7.0-beta.1" in readme
