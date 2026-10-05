from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "packaging" / "polkit" / "org.archmanager.manage-restore-points.policy"
INSTALLER = ROOT / "scripts" / "install-stage4-helper.sh"
UNINSTALLER = ROOT / "scripts" / "uninstall-stage4-helper.sh"


def test_stage4_3_installation_files_exist():
    assert POLICY.is_file()
    assert INSTALLER.is_file()
    assert UNINSTALLER.is_file()
    assert INSTALLER.stat().st_mode & 0o111
    assert UNINSTALLER.stat().st_mode & 0o111


def test_stage4_3_policy_is_bound_to_exact_helper_path():
    source = POLICY.read_text(encoding="utf-8")
    assert 'action id="org.archmanager.manage-restore-points"' in source
    assert (
        '<annotate key="org.freedesktop.policykit.exec.path">'
        '/usr/local/libexec/arch-manager/manage-restore-points</annotate>'
        in source
    )
    assert "<allow_any>no</allow_any>" in source
    assert "<allow_inactive>no</allow_inactive>" in source
    assert "<allow_active>auth_admin</allow_active>" in source
    assert "auth_admin_keep" not in source
    assert "allow_gui" not in source


def test_stage4_3_installer_uses_fixed_local_system_paths():
    source = INSTALLER.read_text(encoding="utf-8")
    assert "INSTALLED_HELPER='/usr/local/libexec/arch-manager/manage-restore-points'" in source
    assert "INSTALLED_POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'" in source
    assert "INSTALLED_RULE='/etc/polkit-1/rules.d/00-arch-manager-restore-points.rules'" in source
    assert "/etc/sudoers" not in source
    assert "NOPASSWD" not in source


def test_stage4_3_rule_limits_subject_program_and_target_user():
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'action.lookup("program") != "${INSTALLED_HELPER}"' in source
    assert 'action.lookup("user") != "root"' in source
    assert "subject.user == ${TARGET_USER_JSON}" in source
    assert "subject.local == true" in source
    assert "subject.active == true" in source
    assert "polkit.Result.AUTH_ADMIN" in source
    assert "polkit.Result.AUTH_ADMIN_KEEP" not in source
    assert "polkit.Result.YES" not in source


def test_stage4_3_installed_files_are_root_owned_and_not_user_writable():
    source = INSTALLER.read_text(encoding="utf-8")
    assert "-o root -g root -m 0755" in source
    assert "-o root -g root -m 0644" in source
    assert "-o root -g polkitd -m 0640" in source
    assert "root:root 755" in source
    assert "root:polkitd 640" in source


def test_stage4_3_installer_never_runs_a_real_restore_point_action():
    source = INSTALLER.read_text(encoding="utf-8")
    assert '"$SOURCE_HELPER" --self-test' in source
    assert '"$INSTALLED_HELPER" --self-test' in source
    assert '/usr/bin/pkexec "$INSTALLED_HELPER"' not in source
    assert '/usr/bin/pkexec "$SOURCE_HELPER"' not in source
    assert " snapper " not in source
    for action in (" create ", " rename ", " set-importance ", " delete "):
        assert action not in source


def test_stage4_3_uninstaller_removes_only_fixed_integration_paths():
    source = UNINSTALLER.read_text(encoding="utf-8")
    assert "HELPER='/usr/local/libexec/arch-manager/manage-restore-points'" in source
    assert "POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'" in source
    assert "RULE='/etc/polkit-1/rules.d/00-arch-manager-restore-points.rules'" in source
    assert "LEGACY_POLICY='/usr/local/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'" in source
    assert "rm -rf" not in source
    assert "find " not in source


def test_stage4_3_scripts_have_valid_shell_syntax():
    import subprocess

    for script in (INSTALLER, UNINSTALLER):
        completed = subprocess.run(
            ["/usr/bin/bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
