from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/gui/system_page.py").read_text(encoding="utf-8")


def test_system_identity_is_compact_two_column_grid():
    assert '(("Операционная система", "os"), ("Корневая ФС", "filesystem"))' in SOURCE
    assert '(("Ядро Linux", "kernel"), ("Системный диск", "disk"))' in SOURCE
    assert 'column=2' in SOURCE
    assert 'grid.addWidget(label, row, 0, 1, 4)' in SOURCE


def test_system_page_can_generate_report_to_downloads():
    assert 'QPushButton("Сформировать полный отчёт")' in SOURCE
    assert 'build_system_report' in SOURCE
    assert 'QStandardPaths.StandardLocation.DownloadLocation' in SOURCE
    assert 'Arch_Manager_System_Report_' in SOURCE
