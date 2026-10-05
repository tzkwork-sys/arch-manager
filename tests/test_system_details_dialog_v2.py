from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (ROOT / "src/gui/system_page.py").read_text(encoding="utf-8")
RECOVERY = (ROOT / "src/gui/recovery_page.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "src/gui/details_dialog.py").read_text(encoding="utf-8")
RESTORE_DETAILS = (ROOT / "src/gui/restore_point_technical_dialog.py").read_text(encoding="utf-8")


def test_system_page_removes_redundant_top_lines():
    assert "Расширенная диагностика состояния Arch Linux без изменения системы." not in SYSTEM
    assert "self.checked_label = self.make_checked_label()" not in SYSTEM
    assert "self.set_checked_at(self.checked_label" not in SYSTEM
    assert 'self.create_page_layout("Система", spacing=8)' in SYSTEM


def test_system_technical_details_use_large_reusable_dialog():
    assert "from .details_dialog import DetailsDialog" in SYSTEM
    assert "DetailsDialog(" in SYSTEM
    assert "dialog.setDetailedText" not in SYSTEM


def test_recovery_technical_details_use_large_reusable_dialog():
    assert "from .details_dialog import DetailsDialog" in RECOVERY
    assert 'DetailsDialog(' in RECOVERY
    assert '"Техническая информация Recovery"' in RECOVERY


def test_large_details_dialog_has_comfortable_size_and_copy():
    assert "self.setMinimumSize(720, 480)" in DETAILS
    assert "available.width() * 0.68" in DETAILS
    assert "available.height() * 0.70" in DETAILS
    assert 'QPushButton("Копировать")' in DETAILS
    assert "QPlainTextEdit" in DETAILS


def test_restore_point_details_are_not_tiny():
    assert "self.setMinimumSize(720, 480)" in RESTORE_DETAILS
    assert "self.resize(820, 560)" in RESTORE_DETAILS
