from __future__ import annotations

from pathlib import Path

import pytest

from src.app_store.aur.availability import AurCapabilityReport
from src.app_store.aur.errors import (
    AurBuildToolsUnavailable,
    AurHelperUnavailable,
    AurPackageAlreadyInstalled,
    AurSystemUpdateRequired,
    AurTransactionBusy,
)
from src.app_store.aur.models import AurPackage
from src.app_store.aur.planner import AurInstallPlanner, validate_aur_package_name
from src.app_store.package_coordinator import (
    PackageCoordinatorBusy,
    PackageTransactionCoordinator,
)
from src.core.command import CommandResult


ROOT = Path(__file__).resolve().parents[2]


def _package(name: str = "google-chrome") -> AurPackage:
    return AurPackage(
        name=name,
        package_base=name,
        version="140.0.7339.207-1",
        description="Browser",
        maintainer="maintainer",
    )


def _report(**overrides) -> AurCapabilityReport:
    values = dict(
        running_as_root=False,
        yay_available=True,
        yay_version="yay v13.0.1",
        git_available=True,
        makepkg_available=True,
        vercmp_available=True,
        pacman_available=True,
        base_devel_complete=True,
        missing_base_devel=(),
        issues=(),
    )
    values.update(overrides)
    return AurCapabilityReport(**values)


class FakeAurService:
    def __init__(self, package: AurPackage | None = None, report: AurCapabilityReport | None = None):
        self.package = package or _package()
        self.report = report or _report()
        self.info_calls: list[tuple[tuple[str, ...], bool]] = []

    def capabilities(self):
        return self.report

    def info(self, names, *, use_cache: bool = True):
        requested = tuple(names)
        self.info_calls.append((requested, use_cache))
        if self.package.name in requested:
            return (self.package,)
        return ()


class FakeRunner:
    def __init__(self, *, installed: bool = False, pending: str = ""):
        self.installed = installed
        self.pending = pending
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, args, *, timeout=30):
        command = tuple(args)
        self.commands.append(command)
        if command[:2] == ("pacman", "-Qq"):
            return CommandResult(command, 0 if self.installed else 1, "", "")
        if command[:2] == ("pacman", "-Qu"):
            return CommandResult(command, 0 if self.pending else 1, self.pending, "")
        raise AssertionError(command)


def test_stage73_package_name_validation_is_strict():
    assert validate_aur_package_name("google-chrome") == "google-chrome"
    assert validate_aur_package_name("libfoo++") == "libfoo++"
    for invalid in ("", "-S", "foo bar", "foo;bar", "x" * 129):
        with pytest.raises(ValueError):
            validate_aur_package_name(invalid)


def test_stage73_planner_reconfirms_exact_package_without_cache(tmp_path):
    service = FakeAurService()
    runner = FakeRunner()
    planner = AurInstallPlanner(service, runner=runner, lock_path=tmp_path / "db.lck")

    plan = planner.plan("google-chrome")

    assert plan.package.name == "google-chrome"
    assert plan.command_preview == ("yay", "-S", "--aur", "--", "google-chrome")
    assert service.info_calls == [(('google-chrome',), False)]
    assert ("pacman", "-Qq", "--", "google-chrome") in runner.commands
    assert ("pacman", "-Qu", "--color", "never") in runner.commands


def test_stage73_planner_blocks_pending_full_system_upgrade(tmp_path):
    service = FakeAurService()
    planner = AurInstallPlanner(
        service,
        runner=FakeRunner(pending="linux 6.17 -> 6.18\n"),
        lock_path=tmp_path / "db.lck",
    )

    with pytest.raises(AurSystemUpdateRequired):
        planner.plan("google-chrome")
    assert service.info_calls == []


def test_stage73_planner_blocks_if_package_already_installed(tmp_path):
    planner = AurInstallPlanner(
        FakeAurService(),
        runner=FakeRunner(installed=True),
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(AurPackageAlreadyInstalled):
        planner.plan("google-chrome")


def test_stage73_planner_blocks_root_and_missing_yay(tmp_path):
    runner = FakeRunner()
    root_planner = AurInstallPlanner(
        FakeAurService(report=_report(running_as_root=True)),
        runner=runner,
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(Exception, match="root"):
        root_planner.plan("google-chrome")

    yay_planner = AurInstallPlanner(
        FakeAurService(report=_report(yay_available=False)),
        runner=runner,
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(AurHelperUnavailable):
        yay_planner.plan("google-chrome")


def test_stage73_planner_requires_build_tooling(tmp_path):
    planner = AurInstallPlanner(
        FakeAurService(
            report=_report(
                base_devel_complete=False,
                missing_base_devel=("base-devel",),
            )
        ),
        runner=FakeRunner(),
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(AurBuildToolsUnavailable) as caught:
        planner.plan("google-chrome")
    assert "base-devel" in caught.value.detail


def test_stage73_planner_respects_native_pacman_lock(tmp_path):
    lock = tmp_path / "db.lck"
    lock.write_text("busy")
    planner = AurInstallPlanner(FakeAurService(), runner=FakeRunner(), lock_path=lock)
    with pytest.raises(AurTransactionBusy):
        planner.plan("google-chrome")


def test_stage73_coordinator_serializes_package_mutations():
    coordinator = PackageTransactionCoordinator()
    first = coordinator.acquire("aur-install")
    assert coordinator.busy
    assert coordinator.active_kind == "aur-install"
    with pytest.raises(PackageCoordinatorBusy):
        coordinator.acquire("official-app-store")
    coordinator.release(first)
    second = coordinator.acquire("official-app-store")
    assert coordinator.active_kind == "official-app-store"
    coordinator.release(second)
    assert not coordinator.busy


def test_stage73_terminal_runner_is_whitelisted_interactive_and_non_root():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/run-aur-terminal.sh").read_text(encoding="utf-8")
    terminal_py = (ROOT / "src/gui/app_store/aur/terminal.py").read_text(encoding="utf-8")

    assert '[[ "$ACTION" == install ]] || usage' in runner
    assert '(( EUID == 0 ))' in runner
    assert '"$YAY" -S --aur -- "$PACKAGE"' in runner
    assert '"$PACMAN" -Qq -- "$PACKAGE"' in runner
    assert 'flock' in runner.lower()
    assert 'eval ' not in runner
    assert 'bash -c' not in runner
    assert '--noconfirm' not in runner
    assert '--skipchecksums' not in runner
    assert '--skippgpcheck' not in runner
    assert 'konsole --separate' in launcher
    assert 'QProcess' in terminal_py
    assert 'setArguments' in terminal_py
    assert 'shell=True' not in terminal_py


def test_stage73_gui_contract_routes_install_only_through_terminal_runner():
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    official_helper = (ROOT / "src/privileged/manage_applications.sh").read_text(encoding="utf-8")

    assert "AurInstallPlanner" in details
    assert "AurTerminalProcess" in details
    assert 'setText("Установить через yay")' in details
    assert "Перейти к системным обновлениям" in details
    assert "package_state_changed" in details
    assert "refresh_local_state_async" in details
    assert "dialog.updates_requested.connect" in page
    assert "self._queue_aur_search(use_cache=False)" in page
    assert "yay" not in official_helper
    assert "AUR" not in official_helper


def test_stage73_official_and_system_update_paths_share_coordinator_contract():
    transactions = (ROOT / "src/app_store/transactions.py").read_text(encoding="utf-8")
    update_session = (ROOT / "src/gui/update_session.py").read_text(encoding="utf-8")

    assert "get_package_transaction_coordinator" in transactions
    assert 'acquire("official-app-store")' in transactions
    assert "get_package_transaction_coordinator" in update_session
    assert 'acquire("system-update")' in update_session
