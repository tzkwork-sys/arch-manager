from pathlib import Path

from src.core.maintenance import (
    MaintenanceSummary,
    discover_trash_roots,
    parse_size_to_bytes,
    validate_cache_keep_versions,
)
from src.core.maintenance_actions import MaintenanceAction, build_cleanup_request


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "core" / "maintenance.py"
ACTIONS = ROOT / "src" / "core" / "maintenance_actions.py"
EXECUTOR = ROOT / "src" / "core" / "maintenance_executor.py"
PAGE = ROOT / "src" / "gui" / "maintenance_page.py"
HELPER = ROOT / "src" / "privileged" / "manage_maintenance.sh"
POLICY = ROOT / "packaging" / "polkit" / "org.archmanager.manage-maintenance.policy"
INSTALLER = ROOT / "scripts" / "install-stage6-helper.sh"
PACDIFF_RUNNER = ROOT / "scripts" / "run-pacdiff-session.sh"
DOC = ROOT / "docs" / "STAGE_6.md"
VERSION = ROOT / "src" / "__init__.py"


def test_stage6_files_exist_and_scripts_are_executable():
    for path in (CORE, ACTIONS, EXECUTOR, PAGE, HELPER, POLICY, INSTALLER, PACDIFF_RUNNER, DOC):
        assert path.is_file(), path
    for path in (HELPER, INSTALLER, PACDIFF_RUNNER):
        assert path.stat().st_mode & 0o111


def test_stage6_readonly_scan_has_extended_safe_categories():
    source = CORE.read_text(encoding="utf-8")
    assert '["paccache", "-dv", "-k", str(keep_versions), "--nocolor"]' in source
    assert '["pacman", "-Qtdq"]' in source
    assert '["journalctl", "--disk-usage", "--no-pager"]' in source
    assert '["pacdiff", "-o", "--nocolor"]' in source
    assert 'home / ".cache" / "thumbnails"' in source
    assert 'home / ".local" / "share"' in source or 'home / ".local" / "share"' in source
    assert "sudo " not in source
    assert "pkexec" not in source


def test_known_reclaimable_size_does_not_promise_full_journal_size():
    summary = MaintenanceSummary(
        reclaimable_cache_bytes=100,
        orphan_packages=2,
        journal_usage_bytes=999_999,
        config_attention_files=0,
        trash_bytes=200,
        thumbnail_cache_bytes=300,
        orphan_installed_bytes=400,
    )
    assert summary.known_reclaimable_bytes == 1000


def test_action_contract_is_finite_and_deduplicated():
    request = build_cleanup_request(
        [
            MaintenanceAction.TRASH,
            MaintenanceAction.PACKAGE_CACHE,
            MaintenanceAction.TRASH,
        ]
    )
    assert request.actions == (
        MaintenanceAction.PACKAGE_CACHE,
        MaintenanceAction.TRASH,
    )
    assert request.privileged_actions == (MaintenanceAction.PACKAGE_CACHE,)
    assert request.user_actions == (MaintenanceAction.TRASH,)


def test_executor_uses_fixed_polkit_helper_and_no_shell():
    source = EXECUTOR.read_text(encoding="utf-8")
    assert 'Path("/usr/bin/pkexec")' in source
    assert 'Path("/usr/local/libexec/arch-manager/manage-maintenance")' in source
    assert '"--user",' in source
    assert '"root",' in source
    assert "shell=True" not in source
    assert "bash -c" not in source
    assert "eval(" not in source


def test_privileged_helper_is_narrow_and_keeps_package_cache_rollback_versions():
    source = HELPER.read_text(encoding="utf-8")
    assert "package-cache|orphans|journal" in source
    assert '"$PACCACHE" -r -k "$keep" --nocolor' in source
    assert '"$PACMAN" -Qtdq' in source
    assert '"$PACMAN" -R --noconfirm --' in source
    assert '"$JOURNALCTL" --vacuum-time=30d --no-pager' in source
    assert "rm -rf" not in source
    assert "eval " not in source
    assert "bash -c" not in source


def test_gui_exposes_selective_cleanup_and_manual_config_review():
    source = PAGE.read_text(encoding="utf-8")
    for text in (
        "Кэш обновлений",
        "Ненужные зависимости",
        "Системный журнал",
        "Кэш миниатюр",
        "Корзина",
        "Файлы настроек после обновлений",
        "Очистить выбранное",
        "Проверить вручную…",
    ):
        assert text in source
    assert "default_checked=False" in source
    assert "build_cleanup_request" in source
    assert "execute_maintenance_cleanup" in source


def test_pacdiff_remains_interactive_in_separate_runner():
    source = PACDIFF_RUNNER.read_text(encoding="utf-8")
    assert 'PACDIFF=/usr/bin/pacdiff' in source
    assert '"$PACDIFF" -s' in source
    assert "--noconfirm" not in source
    assert "rm -f" not in source


def test_stage6_polkit_policy_is_bound_to_exact_helper():
    source = POLICY.read_text(encoding="utf-8")
    assert 'action id="org.archmanager.manage-maintenance"' in source
    assert "/usr/local/libexec/arch-manager/manage-maintenance" in source
    assert "<allow_any>no</allow_any>" in source
    assert "<allow_inactive>no</allow_inactive>" in source
    assert "<allow_active>auth_admin</allow_active>" in source
    assert "auth_admin_keep" not in source


def test_stage6_installer_restricts_subject_and_program():
    source = INSTALLER.read_text(encoding="utf-8")
    assert "INSTALLED_HELPER='/usr/local/libexec/arch-manager/manage-maintenance'" in source
    assert 'action.lookup("program") != "${INSTALLED_HELPER}"' in source
    assert 'action.lookup("user") != "root"' in source
    assert "subject.local == true" in source
    assert "subject.active == true" in source
    assert "polkit.Result.AUTH_ADMIN" in source
    assert "AUTH_ADMIN_KEEP" not in source
    assert "NOPASSWD" not in source


def test_stage6_shell_scripts_have_valid_syntax():
    import subprocess

    for script in (HELPER, INSTALLER, PACDIFF_RUNNER):
        completed = subprocess.run(
            ["/usr/bin/bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_stage6_version_and_documentation():
    assert '__version__ = "0.7.0-beta.1"' in VERSION.read_text(encoding="utf-8")
    text = DOC.read_text(encoding="utf-8")
    assert "Кэш миниатюр" in text
    assert "Корзина" in text
    assert "0 версий" in text
    assert "других подключённых дисках" in text
    assert "не удаляются автоматически" in text


def test_size_parser_still_handles_pacman_installed_size_format():
    assert parse_size_to_bytes("12.34 MiB") == int(12.34 * 1024**2)


def test_stage6_cleanup_is_recorded_in_shared_activity_log_ui():
    page = PAGE.read_text(encoding="utf-8")
    log_page = (ROOT / "src" / "gui" / "log_page.py").read_text(encoding="utf-8")
    assert "from src.core.activity_log import append_activity" in page
    assert 'append_activity(' in page
    assert '"maintenance"' in page
    assert '"cleanup"' in page
    assert '"maintenance": "Обслуживание"' in log_page
    assert '"cleanup": "Очистка системы"' in log_page
    assert "Категории очистки:" in log_page



def test_cache_retention_is_bounded_and_exposed_to_gui_and_helper():
    assert validate_cache_keep_versions(0) == 0
    assert validate_cache_keep_versions(3) == 3
    import pytest
    with pytest.raises(ValueError):
        validate_cache_keep_versions(4)

    page = PAGE.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    executor = EXECUTOR.read_text(encoding="utf-8")
    assert '0 версий · очистить полностью' in page
    assert 'MAINTENANCE_PACKAGE_CACHE_KEEP_KEY' in page
    assert '"--cache-keep",' in executor
    assert '0|1|2|3' in helper


def test_trash_discovery_includes_separate_mount_trash(tmp_path):
    import os

    home = tmp_path / "home"
    home.mkdir()
    home_trash = home / ".local" / "share" / "Trash"
    (home_trash / "files").mkdir(parents=True)
    (home_trash / "info").mkdir()

    mount = tmp_path / "data"
    mount.mkdir()
    mounted_trash = mount / f".Trash-{os.getuid()}"
    (mounted_trash / "files").mkdir(parents=True)
    (mounted_trash / "info").mkdir()

    roots = discover_trash_roots(home=home, uid=os.getuid(), mount_points=(mount,))
    assert home_trash in roots
    assert mounted_trash in roots


def test_cleanup_bar_is_outside_scroll_and_journal_size_is_labeled_total():
    source = PAGE.read_text(encoding="utf-8")
    assert 'layout.addWidget(scroll, 1)' in source
    assert source.index('layout.addWidget(scroll, 1)') < source.index('layout.addWidget(action_card)')
    assert 'f"Общий размер: {format_bytes(journal_bytes)}"' in source
    assert 'Без учёта журналов можно освободить примерно' in source


def test_cleanup_request_carries_cache_retention():
    request = build_cleanup_request(
        [MaintenanceAction.PACKAGE_CACHE],
        cache_keep_versions=0,
    )
    assert request.cache_keep_versions == 0


def test_user_trash_cleanup_clears_every_discovered_trash(tmp_path, monkeypatch):
    import src.core.maintenance_executor as executor

    roots = []
    for name in ("home-trash", "data-trash"):
        root = tmp_path / name
        files = root / "files"
        info = root / "info"
        files.mkdir(parents=True)
        info.mkdir()
        (files / "example.txt").write_text("data", encoding="utf-8")
        (info / "example.txt.trashinfo").write_text("[Trash Info]\n", encoding="utf-8")
        roots.append(root)

    monkeypatch.setattr(executor, "discover_trash_roots", lambda *, home: tuple(roots))
    executor._run_user_action(MaintenanceAction.TRASH)

    for root in roots:
        assert list((root / "files").iterdir()) == []
        assert list((root / "info").iterdir()) == []
