from pathlib import Path
from tests.source_bundles import restore_points_page_source

from src.core.restore_point_technical import build_restore_point_technical_text
from src.core.restore_points import RestorePoint


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "gui" / "restore_points_page.py"
DIALOG = ROOT / "src" / "gui" / "restore_point_technical_dialog.py"


def test_stage5_3_removes_duplicate_restore_point_side_panel():
    source = restore_points_page_source(ROOT)
    assert "self.details_card" not in source
    assert "Выбранная точка" not in source
    assert "QSplitter" not in source
    assert "workspace.addWidget(self.table, 1 if not self._embedded else 0)" in source


def test_stage5_3_moves_technical_information_into_row_menu():
    source = restore_points_page_source(ROOT)
    assert 'actions_menu.addAction("Техническая информация…")' in source
    assert "RestorePointTechnicalDialog" in source
    assert "def _open_technical_dialog" in source
    assert "Техническая информация…" in source


def test_stage5_3_technical_text_contains_only_service_metadata():
    point = RestorePoint(
        number=17,
        created_at=None,
        description="Тест",
        important=True,
        exclusive_size_bytes=1024,
        creator="root",
        cleanup="number",
        userdata="important=yes,foo=bar",
        pre_number=16,
    )
    text = build_restore_point_technical_text(point)
    assert "Snapper ID: 17" in text
    assert "Создатель: root" in text
    assert "Политика очистки: number" in text
    assert "Userdata: important=yes,foo=bar" in text
    assert "Связана с точкой до изменения: 16" in text
    assert "Дата:" not in text
    assert "Занимает:" not in text


def test_stage5_3_technical_dialog_is_read_only_and_copyable():
    source = DIALOG.read_text(encoding="utf-8")
    assert "QPlainTextEdit" in source
    assert "setReadOnly(True)" in source
    assert 'QPushButton("Копировать")' in source
    assert "QApplication.clipboard()" in source


def test_stage5_3_preserves_safe_row_actions_and_full_width_table():
    source = restore_points_page_source(ROOT)
    assert 'actions_menu.addAction("Переименовать")' in source
    assert '"Убрать из важных" if point.important else "Пометить как важную"' in source
    assert 'actions_menu.addAction("Удалить…")' in source
    assert "Удалить выбранные" in source
    assert 'QTableWidget(0, 6)' in source


def test_stage5_3_create_tooltip_is_single_copy():
    source = restore_points_page_source(ROOT)
    phrase = "Создать новую точку восстановления Snapper с подтверждением администратора"
    assert source.count(phrase) == 1
