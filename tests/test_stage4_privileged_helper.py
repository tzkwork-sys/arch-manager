from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src" / "privileged" / "manage_restore_points.sh"


def helper_source() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_stage4_privileged_helper_exists_and_is_executable():
    assert HELPER.is_file()
    assert HELPER.stat().st_mode & 0o111


def test_stage4_privileged_helper_has_valid_shell_syntax():
    result = subprocess.run(
        ["/usr/bin/bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_stage4_privileged_helper_self_test_is_unprivileged_and_harmless():
    result = subprocess.run(
        [str(HELPER), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_stage4_privileged_helper_exposes_only_fixed_actions():
    source = helper_source()
    for action in ("create)", "rename)", "set-importance)", "delete)"):
        assert action in source

    for forbidden in ("eval ", "bash -c", "sh -c", "sudo ", "pkexec", "\nsource "):
        assert forbidden not in source


def test_stage4_privileged_helper_uses_fixed_system_paths():
    source = helper_source()
    assert "SNAPPER=/usr/bin/snapper" in source
    assert "PYTHON=/usr/bin/python" in source
    assert "export PATH=/usr/bin:/bin" in source


def test_stage4_privileged_helper_requires_root_before_real_snapper_actions():
    source = helper_source()
    for function_name in (
        "run_create()",
        "run_rename()",
        "run_set_importance()",
        "run_delete()",
    ):
        start = source.index(function_name)
        body = source[start : source.index("\n}", start)]
        assert "require_root" in body
        assert "require_runtime" in body


def test_stage4_privileged_helper_preserves_userdata_when_changing_importance():
    source = helper_source()
    assert "--jsonout list --columns number,userdata" in source
    assert 'clean["important"] = wanted' in source
    assert 'modify --userdata "$userdata"' in source
