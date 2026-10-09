from pathlib import Path

from src.core.updates import _parse_pacman_info, parse_update_line, parse_update_output


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "core" / "updates.py"
PAGE = ROOT / "src" / "gui" / "updates_page.py"
RUNNER = ROOT / "scripts" / "run-system-update-session.sh"
TERMINAL_RUNNER = ROOT / "scripts" / "run-system-update-terminal.sh"
VERSION = ROOT / "src" / "__init__.py"
STAGE5 = ROOT / "docs" / "STAGE_5.md"


def test_stage5_parses_standard_checkupdates_line():
    item = parse_update_line("linux 6.17.1.arch1-1 -> 6.17.2.arch1-1", source="official")
    assert item is not None
    assert item.name == "linux"
    assert item.current_version == "6.17.1.arch1-1"
    assert item.new_version == "6.17.2.arch1-1"
    assert item.source == "official"


def test_stage5_parser_keeps_unusual_nonempty_lines_visible():
    item = parse_update_line("unexpected-package-output", source="aur")
    assert item is not None
    assert item.name == "unexpected-package-output"
    assert item.current_version == "—"
    assert item.new_version == "—"


def test_stage5_parser_ignores_blank_lines():
    assert parse_update_line("   ", source="official") is None
    items = parse_update_output("a 1 -> 2\n\n b 3 -> 4\n", source="official")
    assert [item.name for item in items] == ["a", "b"]


def test_stage5_backend_checks_official_and_aur_without_privilege_escalation():
    source = CORE.read_text(encoding="utf-8")
    assert '["checkupdates", "--nocolor"]' in source
    assert '["yay", "-Qu", "--aur"]' in source
    assert "sudo" not in source
    assert "pkexec" not in source
    assert "shell=True" not in source


def test_stage5_gui_has_compact_selectable_table_info_and_refresh():
    source = PAGE.read_text(encoding="utf-8")
    assert '["Выбор", "Пакет", "Установлено", "Доступно", "Скачать", "Источник  ▾"]' in source
    assert "search_edit" not in source
    assert 'class _SourceFilterHeader(QHeaderView)' in source
    assert '("Официальные", "official")' in source
    assert '("AUR", "aur")' in source
    assert "self._build_package_details(layout)" in source
    assert "ItemIsUserCheckable" in source
    assert "collect_installed_package_info" in source
    assert 'self.refresh_button = QPushButton("Проверить обновления")' in source
    assert 'self.install_button.setMinimumWidth(220)' in source
    assert 'self.add_header_action(self.refresh_button)' in source
    assert 'self.add_header_action(self.install_button)' in source

def test_stage5_package_info_parser_handles_wrapped_pacman_fields():
    fields = _parse_pacman_info(
        "Name            : demo\n"
        "Version         : 1.2-3\n"
        "Description     : First part\n"
        "                  second part\n"
        "URL             : https://example.test/\n"
    )
    assert fields["Name"] == "demo"
    assert fields["Description"] == "First part second part"
    assert fields["URL"] == "https://example.test/"


def test_stage5_gui_keeps_preupdate_protection_setting_and_delegates_to_runner():
    source = PAGE.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    assert "AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY" in source
    assert "create_restore_point=protected" in source
    assert "Arch Manager: перед обновлением системы" in runner
    assert "manage-restore-points" in runner
    assert "Обновление не запускалось" in source

def test_stage5_gui_launches_fixed_runner_in_real_terminal_session():
    source = PAGE.read_text(encoding="utf-8")
    session = (ROOT / "src" / "gui" / "update_session.py").read_text(encoding="utf-8")
    terminal_runner = TERMINAL_RUNNER.read_text(encoding="utf-8")
    assert '"scripts" / "run-system-update-terminal.sh"' in source
    assert "UpdateSessionProcess" in source
    assert "QProcess" in session
    assert "shell=True" not in source + session
    assert "bash -c" not in source + session
    assert "konsole --separate" in terminal_runner
    assert "gnome-terminal --wait" in terminal_runner
    assert "ARCH_MANAGER_PAUSE_ON_EXIT=1" in terminal_runner

def test_stage5_runner_keeps_package_manager_interactive_selective_and_orders_stages():
    source = RUNNER.read_text(encoding="utf-8")
    assert 'PACMAN=/usr/bin/pacman' in source
    assert 'YAY=/usr/bin/yay' in source
    assert '"$SUDO" -n "$PACMAN" -Syu' in source
    assert '"$YAY" -S --needed --aur -- "${AUR_PACKAGES[@]}"' in source
    assert "--noconfirm" not in source
    assert "eval " not in source
    assert "bash -c" not in source
    assert "AUR-пакеты после ошибки официального этапа не устанавливаются." in source
    assert '"$SUDO" -k' not in source
    assert '"$SUDO" -v' in source
    assert "-S -p ''" not in source
    assert "AUTH_MARKER" not in source

def test_stage5_runner_never_runs_yay_through_sudo():
    source = RUNNER.read_text(encoding="utf-8")
    assert '"$SUDO" "$YAY"' not in source
    assert 'sudo yay' not in source


def test_stage5_version_and_documentation_exist():
    assert '__version__ = "0.7.0-beta.1"' in VERSION.read_text(encoding="utf-8")
    assert STAGE5.is_file()
    text = STAGE5.read_text(encoding="utf-8")
    assert "детальный список" in text
    assert "защитную точку" in text
    assert "терминал" in text.casefold()
    assert "один раз" in text.casefold()


def test_update_header_buttons_primary_style_and_order():
    source = PAGE.read_text(encoding="utf-8")
    install_add = "self.add_header_action(self.install_button)"
    refresh_add = "self.add_header_action(self.refresh_button)"
    assert install_add in source
    assert refresh_add in source
    assert source.index(install_add) < source.index(refresh_add)
    assert "emphasize_primary_button(self.install_button)" in source
    assert "emphasize_primary_button(self.refresh_button)" not in source
    assert 'self.refresh_button.setMinimumHeight(36)' in source
    assert 'self.install_button.setMinimumHeight(36)' in source
