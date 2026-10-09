from __future__ import annotations

from concurrent.futures import Future
import subprocess
from pathlib import Path
import threading

import pytest

from src.app_store.actions import (
    PackageActionValidationError,
    build_install_request,
    build_remove_request,
)
from src.app_store.package_actions import (
    PacmanPackageActionPlanner,
    PackageUpdateRequired,
)
from src.app_store.package_state import PackageState
from src.app_store.transactions import PackageActionService, PackageTransactionBusy
from src.core.command import CommandResult
import src.core.app_store_executor as executor


ROOT = Path(__file__).resolve().parents[1]


def _result(command, returncode=0, stdout="", stderr="") -> CommandResult:
    return CommandResult(tuple(command), returncode, stdout, stderr)


def test_stage4_action_contract_rejects_argument_and_path_injection():
    invalid = (
        "",
        "-Syu",
        "../../etc/passwd",
        "pkg;rm",
        "pkg && whoami",
        "pkg name",
        "pkg$(id)",
        "pkg/other",
    )
    for value in invalid:
        with pytest.raises(PackageActionValidationError):
            build_install_request(value)
        with pytest.raises(PackageActionValidationError):
            build_remove_request(value)

    assert build_install_request("qt6-base").helper_arguments() == ("install", "qt6-base")
    assert build_remove_request("libreoffice-fresh").helper_arguments() == (
        "remove",
        "libreoffice-fresh",
    )


def test_stage4_helper_is_strictly_whitelisted_and_never_syncs_databases():
    source = (ROOT / "src/privileged/manage_applications.sh").read_text(encoding="utf-8")
    assert "case \"$action\" in" in source
    assert "install) run_install" in source
    assert "remove) run_remove" in source
    assert "core|extra|multilib" in source
    assert '"$repository/$package_name"' in source
    assert '"$PACMAN" -S --needed --noconfirm' in source
    assert '"$PACMAN" -Rs --noconfirm' in source
    assert "-Sy" not in source
    assert "--nodeps" not in source
    assert "-Rdd" not in source
    for forbidden in ("eval ", "bash -c", "sh -c", "sudo ", "pkexec"):
        assert forbidden not in source


def test_stage4_polkit_policy_and_installer_are_bound_to_exact_helper():
    policy = (ROOT / "packaging/polkit/org.archmanager.manage-applications.policy").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts/install-app-store-helper.sh").read_text(encoding="utf-8")
    assert 'action id="org.archmanager.manage-applications"' in policy
    assert "/usr/local/libexec/arch-manager/manage-applications" in policy
    assert "<allow_any>no</allow_any>" in policy
    assert "<allow_inactive>no</allow_inactive>" in policy
    assert "<allow_active>auth_admin</allow_active>" in policy
    assert "polkit.Result.AUTH_ADMIN" in installer
    assert "polkit.Result.AUTH_ADMIN_KEEP" not in installer
    assert "action.lookup(\"program\")" in installer
    assert "action.lookup(\"user\") != \"root\"" in installer
    assert "root:root 755" in installer
    assert "root:polkitd 640" in installer
    helper = (ROOT / "src/privileged/manage_applications.sh").read_text(encoding="utf-8")
    assert r"OK\tself-test\tapp-store-v1" in helper


def test_stage4_install_plan_uses_official_repo_and_previews_dependencies(tmp_path):
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        command = tuple(command)
        calls.append(command)
        if command == ("pacman", "-Sl"):
            return _result(command, stdout="custom demo 9\nextra demo 2.0\nextra dep 1.0\n")
        if command == ("pacman", "-Qu", "--color", "never"):
            return _result(command, returncode=1)
        if command[:2] == ("pacman", "-Sp"):
            return _result(command, stdout="demo|2.0\ndep|1.0\n")
        raise AssertionError(command)

    class Provider:
        def collect(self, names):
            return {
                "demo": PackageState(
                    "demo",
                    repository="extra",
                    available_version="2.0",
                    installed=False,
                    available=True,
                )
            }

    planner = PacmanPackageActionPlanner(runner=runner, lock_path=tmp_path / "db.lck")
    planner.state_provider = Provider()
    plan = planner.plan(build_install_request("demo"))

    assert [change.package_name for change in plan.changes] == ["demo", "dep"]
    preview = next(call for call in calls if len(call) > 1 and call[1] == "-Sp")
    assert preview[-1] == "extra/demo"
    assert all("-Sy" not in part for call in calls for part in call)


def test_stage4_install_plan_refuses_partial_upgrade_when_updates_are_pending(tmp_path):
    def runner(command, **_kwargs):
        command = tuple(command)
        if command == ("pacman", "-Sl"):
            return _result(command, stdout="extra demo 2.0\n")
        if command == ("pacman", "-Qu", "--color", "never"):
            return _result(command, stdout="linux 6.1 -> 6.2\n")
        raise AssertionError(command)

    class Provider:
        def collect(self, names):
            return {"demo": PackageState("demo", available=True, installed=False)}

    planner = PacmanPackageActionPlanner(runner=runner, lock_path=tmp_path / "db.lck")
    planner.state_provider = Provider()
    with pytest.raises(PackageUpdateRequired):
        planner.plan(build_install_request("demo"))


def test_stage4_remove_plan_uses_recursive_safe_preview(tmp_path):
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        command = tuple(command)
        calls.append(command)
        if command == ("pacman", "-Sl"):
            return _result(command, stdout="extra demo 2.0\n")
        if command[:2] == ("pacman", "-Rsp"):
            return _result(command, stdout="demo|2.0\ndep|1.0\n")
        raise AssertionError(command)

    class Provider:
        def collect(self, names):
            return {
                "demo": PackageState(
                    "demo", available=True, installed=True, installed_version="2.0"
                )
            }

    planner = PacmanPackageActionPlanner(runner=runner, lock_path=tmp_path / "db.lck")
    planner.state_provider = Provider()
    plan = planner.plan(build_remove_request("demo"))
    assert [change.package_name for change in plan.changes] == ["demo", "dep"]
    assert any(call[1] == "-Rsp" for call in calls if len(call) > 1)


def test_stage4_executor_builds_exact_pkexec_command(monkeypatch):
    monkeypatch.setattr(executor, "_ensure_runtime_ready", lambda: None)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = tuple(command)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "OK\tinstalled\tdemo\n", "")

    monkeypatch.setattr(executor, "_run_package_process", fake_run)
    result = executor.execute_package_action(build_install_request("demo"))
    assert result.package_name == "demo"
    assert captured["command"] == (
        "/usr/bin/pkexec",
        "--user",
        "root",
        "/usr/local/libexec/arch-manager/manage-applications",
        "install",
        "demo",
    )
    assert "shell" not in captured["kwargs"]


def test_stage4_executor_maps_update_required(monkeypatch):
    monkeypatch.setattr(executor, "_ensure_runtime_ready", lambda: None)
    monkeypatch.setattr(
        executor,
        "_run_package_process",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 74, "", "upgrade required"),
    )
    with pytest.raises(executor.PackageActionUpdateRequired):
        executor.execute_package_action(build_install_request("demo"))


def test_stage4_service_blocks_concurrent_mutating_transactions():
    entered = threading.Event()
    release = threading.Event()

    def slow_executor(request):
        entered.set()
        assert release.wait(timeout=2)
        return object()

    service = PackageActionService(executor=slow_executor)
    try:
        first = service.execute_async(build_install_request("demo"))
        assert entered.wait(timeout=2)
        second = service.execute_async(build_remove_request("other"))
        with pytest.raises(PackageTransactionBusy):
            second.result(timeout=1)
        release.set()
        first.result(timeout=2)
    finally:
        release.set()
        service.shutdown()


def test_stage4_launch_is_unprivileged_and_gui_never_builds_shell_commands():
    launcher = (ROOT / "src/app_store/launcher.py").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")
    assert "/usr/share/applications" in launcher
    assert '"/usr/bin/gio"' in launcher
    assert "subprocess.Popen" in launcher
    assert "shell=True" not in launcher
    assert "pkexec" not in launcher
    assert "sudo " not in launcher
    assert "subprocess" not in details
    assert "QProgressBar" in details
    assert "_package_action_requested" in details
    assert "Перейти к обновлениям" in details


def test_stage4_main_window_routes_update_required_to_existing_updates_page():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    main = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")
    assert "updates_requested = Signal()" in page
    assert "dialog.updates_requested.connect(self.updates_requested.emit)" in page
    assert "app_store_page.updates_requested.connect(lambda: self.set_page(1))" in main
