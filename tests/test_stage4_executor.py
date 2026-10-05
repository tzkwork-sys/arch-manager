from pathlib import Path
import subprocess

import pytest

import src.core.restore_point_executor as executor
from src.core.restore_point_actions import (
    RestorePointAction,
    RestorePointActionRequest,
    build_create_request,
    build_delete_request,
    build_rename_request,
    build_set_importance_request,
)


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "src" / "core" / "restore_point_executor.py"


def _ready(monkeypatch):
    monkeypatch.setattr(executor, "_ensure_runtime_ready", lambda: None)


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_stage4_4_executor_exists_and_uses_fixed_paths():
    source = EXECUTOR.read_text(encoding="utf-8")
    assert 'Path("/usr/bin/pkexec")' in source
    assert 'Path("/usr/local/libexec/arch-manager/manage-restore-points")' in source
    assert "shell=True" not in source
    assert "sudo" not in source
    assert "bash -c" not in source


def test_stage4_4_executor_builds_exact_pkexec_command(monkeypatch):
    _ready(monkeypatch)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = tuple(command)
        seen["kwargs"] = kwargs
        return _completed(command, stdout="OK\trenamed\t41\n")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    result = executor.execute_restore_point_action(
        build_rename_request(41, "Перед обновлением")
    )

    assert seen["command"] == (
        "/usr/bin/pkexec",
        "--user",
        "root",
        "/usr/local/libexec/arch-manager/manage-restore-points",
        "rename",
        "41",
        "Перед обновлением",
    )
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert result.point_number == 41
    assert result.action is RestorePointAction.RENAME


def test_stage4_4_executor_parses_all_success_shapes(monkeypatch):
    _ready(monkeypatch)
    outputs = iter(
        (
            "OK\tcreated\t51\n",
            "OK\trenamed\t51\n",
            "OK\timportance\t51\tyes\n",
            "OK\tdeleted\t51\n",
        )
    )

    def fake_run(command, **kwargs):
        return _completed(command, stdout=next(outputs))

    monkeypatch.setattr(executor.subprocess, "run", fake_run)

    created = executor.execute_restore_point_action(
        build_create_request("Manual", important=False)
    )
    renamed = executor.execute_restore_point_action(build_rename_request(51, "Renamed"))
    important = executor.execute_restore_point_action(build_set_importance_request(51, True))
    deleted = executor.execute_restore_point_action(build_delete_request(51))

    assert created == executor.RestorePointActionResult(RestorePointAction.CREATE, 51, False)
    assert renamed == executor.RestorePointActionResult(RestorePointAction.RENAME, 51, None)
    assert important == executor.RestorePointActionResult(
        RestorePointAction.SET_IMPORTANCE, 51, True
    )
    assert deleted == executor.RestorePointActionResult(RestorePointAction.DELETE, 51, None)


def test_stage4_4_executor_revalidates_manually_constructed_requests(monkeypatch):
    _ready(monkeypatch)
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    request = RestorePointActionRequest(
        action=RestorePointAction.DELETE,
        point_number=-7,
    )

    with pytest.raises(ValueError):
        executor.execute_restore_point_action(request)
    assert called is False


def test_stage4_4_executor_distinguishes_cancel_and_authorization_failure(monkeypatch):
    _ready(monkeypatch)
    codes = iter((126, 127))

    def fake_run(command, **kwargs):
        code = next(codes)
        return _completed(command, returncode=code, stderr="polkit diagnostic")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)

    with pytest.raises(executor.RestorePointActionCancelled) as cancelled:
        executor.execute_restore_point_action(build_delete_request(7))
    assert cancelled.value.returncode == 126

    with pytest.raises(executor.RestorePointAuthorizationError) as denied:
        executor.execute_restore_point_action(build_delete_request(7))
    assert denied.value.returncode == 127


def test_stage4_4_executor_maps_helper_failure_and_malformed_success(monkeypatch):
    _ready(monkeypatch)
    results = iter(
        (
            _completed((), returncode=2, stderr="helper failed"),
            _completed((), stdout="unexpected output\n"),
        )
    )
    monkeypatch.setattr(executor.subprocess, "run", lambda command, **kwargs: next(results))

    with pytest.raises(executor.RestorePointActionFailed) as failed:
        executor.execute_restore_point_action(build_delete_request(8))
    assert failed.value.returncode == 2
    assert failed.value.detail == "helper failed"

    with pytest.raises(executor.RestorePointActionFailed):
        executor.execute_restore_point_action(build_delete_request(8))


def test_stage4_4_executor_maps_timeout_without_retry(monkeypatch):
    _ready(monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr="timeout")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)

    with pytest.raises(executor.RestorePointActionTimedOut):
        executor.execute_restore_point_action(build_delete_request(9), timeout=1.0)
    assert calls == 1


def test_stage4_4_runtime_check_rejects_missing_helper(monkeypatch, tmp_path):
    fake_pkexec = tmp_path / "pkexec"
    fake_pkexec.write_text("", encoding="utf-8")
    fake_pkexec.chmod(0o755)
    monkeypatch.setattr(executor, "PKEXEC_PATH", fake_pkexec)
    monkeypatch.setattr(executor, "HELPER_PATH", tmp_path / "missing-helper")

    with pytest.raises(executor.RestorePointHelperUnavailable):
        executor._ensure_runtime_ready()
