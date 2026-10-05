from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_stage1_files_exist():
    required = [
        ROOT / "main.py",
        ROOT / "src" / "app.py",
        ROOT / "src" / "gui" / "main_window.py",
        ROOT / "src" / "gui" / "dashboard.py",
        ROOT / "desktop" / "arch-manager-gui.desktop",
        ROOT / "src" / "assets" / "arch-manager.svg",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"Missing Stage 1 files: {missing}"


def test_gui_contains_no_privilege_escalation():
    gui_code = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "gui").glob("*.py")
    )
    assert "sudo " not in gui_code
    assert "pkexec" not in gui_code


def test_stage2_dashboard_replaces_stage1_placeholders():
    dashboard = (ROOT / "src" / "gui" / "dashboard.py").read_text(encoding="utf-8")
    assert "Проверить сейчас" in dashboard
    assert "Ещё не проверено" not in dashboard
    assert "data_updated" in dashboard


def test_new_gui_launcher_is_distinct_from_legacy_name():
    desktop = (ROOT / "desktop" / "arch-manager-gui.desktop").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install-stage1.sh").read_text(encoding="utf-8")
    assert "Name=Arch Manager — новая версия" in desktop
    assert "Arch Manager — новая версия.desktop" in installer


def test_gui_icon_installation_is_distinct_and_robust():
    installer = (ROOT / "scripts" / "install-stage1.sh").read_text(encoding="utf-8")
    repair = ROOT / "scripts" / "repair-gui-icon.sh"
    assert repair.is_file()
    assert "arch-manager-gui.svg" in installer
    assert "Icon=$ICON_FILE" in installer
    assert "Arch Manager — новая версия.desktop" in installer
