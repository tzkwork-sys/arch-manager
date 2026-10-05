from __future__ import annotations

from pathlib import Path

import pytest

from src.app_store.aur.availability import AurCapabilityReport
from src.app_store.aur.errors import (
    AurHelperUnavailable,
    AurPackageNotAur,
    AurPackageNotInstalled,
    AurTransactionBusy,
)
from src.app_store.aur.models import AurPackage
from src.app_store.aur.planner import AurRemovePlanner
from src.core.command import CommandResult


ROOT = Path(__file__).resolve().parents[2]


def _package(name: str = "google-chrome") -> AurPackage:
    return AurPackage(
        name=name,
        package_base=name,
        version="140.0.7339.207-1",
        description="Browser",
        maintainer="maintainer",
        installed=True,
        installed_version="139.0-1",
    )


def _report(**overrides) -> AurCapabilityReport:
    values = dict(
        running_as_root=False,
        yay_available=True,
        yay_version="yay v13.0.1",
        git_available=False,
        makepkg_available=False,
        vercmp_available=True,
        pacman_available=True,
        base_devel_complete=False,
        missing_base_devel=("base-devel",),
        issues=(),
    )
    values.update(overrides)
    return AurCapabilityReport(**values)


class FakeAurService:
    def __init__(self, report: AurCapabilityReport | None = None):
        self.report = report or _report()
        self.info_calls = 0

    def capabilities(self):
        return self.report

    def info(self, *_args, **_kwargs):
        self.info_calls += 1
        raise AssertionError("Stage 7.4 removal must not require aurweb")


class RemoveRunner:
    def __init__(self, *, installed: bool = True, foreign: bool = True, version: str = "139.0-1"):
        self.installed = installed
        self.foreign = foreign
        self.version = version
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, args, *, timeout=30):
        command = tuple(args)
        self.commands.append(command)
        if command[:2] == ("pacman", "-Q"):
            if self.installed:
                return CommandResult(command, 0, f"{command[-1]} {self.version}\n", "")
            return CommandResult(command, 1, "", "error: package not found")
        if command[:2] == ("pacman", "-Qm"):
            if self.installed and self.foreign:
                return CommandResult(command, 0, f"{command[-1]} {self.version}\n", "")
            return CommandResult(command, 1, "", "")
        raise AssertionError(command)


def test_stage74_remove_plan_is_local_only_and_refreshes_installed_version(tmp_path):
    service = FakeAurService()
    runner = RemoveRunner(version="140.0.1-2")
    planner = AurRemovePlanner(service, runner=runner, lock_path=tmp_path / "db.lck")

    plan = planner.plan(_package())

    assert plan.command_preview == ("yay", "-Rns", "--", "google-chrome")
    assert plan.package.installed
    assert plan.package.installed_version == "140.0.1-2"
    assert ("pacman", "-Q", "--", "google-chrome") in runner.commands
    assert ("pacman", "-Qm", "--", "google-chrome") in runner.commands
    assert service.info_calls == 0


def test_stage74_remove_does_not_require_build_toolchain_or_system_update_gate(tmp_path):
    runner = RemoveRunner()
    planner = AurRemovePlanner(FakeAurService(_report()), runner=runner, lock_path=tmp_path / "db.lck")

    planner.plan(_package())

    assert not any(command[:2] == ("pacman", "-Qu") for command in runner.commands)


def test_stage74_remove_blocks_absent_package(tmp_path):
    planner = AurRemovePlanner(
        FakeAurService(),
        runner=RemoveRunner(installed=False),
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(AurPackageNotInstalled):
        planner.plan(_package())


def test_stage74_remove_blocks_package_that_is_no_longer_foreign(tmp_path):
    planner = AurRemovePlanner(
        FakeAurService(),
        runner=RemoveRunner(installed=True, foreign=False),
        lock_path=tmp_path / "db.lck",
    )
    with pytest.raises(AurPackageNotAur):
        planner.plan(_package())


def test_stage74_remove_rejects_unconfirmed_aur_model(tmp_path):
    package = AurPackage(
        name="local-custom",
        package_base="local-custom",
        version="1-1",
        installed=True,
        installed_version="1-1",
        aur_confirmed=False,
        foreign_unknown=True,
    )
    planner = AurRemovePlanner(FakeAurService(), runner=RemoveRunner(), lock_path=tmp_path / "db.lck")
    with pytest.raises(AurPackageNotAur):
        planner.plan(package)


def test_stage74_remove_respects_pacman_lock_and_runtime_capabilities(tmp_path):
    lock = tmp_path / "db.lck"
    lock.write_text("busy")
    planner = AurRemovePlanner(FakeAurService(), runner=RemoveRunner(), lock_path=lock)
    with pytest.raises(AurTransactionBusy):
        planner.plan(_package())

    no_yay = AurRemovePlanner(
        FakeAurService(_report(yay_available=False)),
        runner=RemoveRunner(),
        lock_path=tmp_path / "other.lock",
    )
    with pytest.raises(AurHelperUnavailable):
        no_yay.plan(_package())


def test_stage74_terminal_runner_whitelists_remove_and_verifies_actual_state():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")
    terminal = (ROOT / "src/gui/app_store/aur/terminal.py").read_text(encoding="utf-8")

    assert 'install || "$ACTION" == remove || "$ACTION" == update || "$ACTION" == update-all' in runner
    assert '"$YAY" -Rns -- "$PACKAGE"' in runner
    assert '"$PACMAN" -Qm -- "$PACKAGE"' in runner
    assert '"$PACMAN" -Qq -- "$PACKAGE"' in runner
    assert 'package is still installed after yay success' in runner
    assert '--noconfirm' not in runner
    assert 'eval ' not in runner
    assert 'bash -c' not in runner
    assert '_ALLOWED_ACTIONS = {"install", "remove", "update", "update-all"}' in terminal
    assert 'acquire(f"aur-{operation}")' in terminal
    assert 'arguments = ["--action", operation]' in terminal
    assert 'arguments.extend(["--package", package])' in terminal


def test_stage74_gui_exposes_remove_and_refreshes_store_state():
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")

    assert "AurRemovePlanner" in details
    assert 'setText("Удалить через yay")' in details
    assert '"yay", "-Rns", "--", name' in (ROOT / "src/app_store/aur/planner.py").read_text(encoding="utf-8")
    assert 'action=action' in details
    assert 'append_activity(' in details
    assert 'get_update_state_service().invalidate()' in details
    assert 'refresh_local_state_async' in details
    assert 'if dialog.package_state_changed:' in page
    assert 'self._queue_aur_installed(use_cache=False)' in page
    assert 'self._queue_aur_search(use_cache=False)' in page


def test_stage74_official_remove_backend_remains_separate():
    helper = (ROOT / "src/privileged/manage_applications.sh").read_text(encoding="utf-8")
    transactions = (ROOT / "src/app_store/transactions.py").read_text(encoding="utf-8")
    official_details = (ROOT / "src/gui/app_store/details.py").read_text(encoding="utf-8")

    assert "remove) run_remove" in helper
    assert "yay" not in helper
    assert 'acquire("official-app-store")' in transactions
    assert "build_remove_request" in official_details



def test_stage741_stale_removed_package_is_reconciled_and_installed_reload_refreshes_aur():
    service = (ROOT / "src/app_store/aur/service.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")

    assert "package.with_local_state(" in service
    assert "installed_version=None" in service
    assert 'if self._active_view() == "installed":' in page
    assert "self._queue_aur_installed(use_cache=result.cache_hit)" in page
    assert "AurPackageNotInstalled, AurPackageNotAur" in details
    assert "Обновляю фактическое состояние пакета" in details
    assert "пользовательские данные в домашней папке" in details
    assert "Пользовательские данные в домашней папке" in runner



def test_stage742_remove_cleans_only_orphans_created_by_this_transaction():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    planner = (ROOT / "src/app_store/aur/planner.py").read_text(encoding="utf-8")
    log_page = (ROOT / "src/gui/log_page.py").read_text(encoding="utf-8")

    assert 'output=$("$PACMAN" -Qtdq 2>/dev/null)' in runner
    assert 'declare -a baseline_orphans=()' in runner
    assert 'new_orphans_since baseline_orphans current_orphans new_orphans' in runner
    assert 'baseline_map["$package"]=1' in runner
    assert '[[ -n "${baseline_map[$package]+x}" ]] || result_ref+=("$package")' in runner
    assert '"$YAY" -Rns -- "${new_orphans[@]}"' in runner
    assert 'write_status cleanup-incomplete' in runner
    assert 'Ненужные зависимости, существовавшие ДО удаления, намеренно не затрагивались.' in runner
    assert 'например, отдельный *-debug' in details
    assert 'state == "cleanup-incomplete" and action == "remove"' in details
    assert '"partial",' in details
    assert '"partial": "Частично"' in log_page
    assert 'только новые orphan-пакеты' in planner


def test_stage742_orphan_baseline_is_taken_before_target_remove_and_cleanup_is_afterwards():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")

    baseline = runner.index('if ! read_orphans baseline_orphans; then')
    target_remove = runner.index('"$YAY" -Rns -- "$PACKAGE"')
    delta = runner.index('new_orphans_since baseline_orphans current_orphans new_orphans')
    cleanup = runner.index('"$YAY" -Rns -- "${new_orphans[@]}"')

    assert baseline < target_remove < delta < cleanup
    assert '--noconfirm' not in runner
    assert 'pacman -Qtdq |' not in runner



def test_stage743_remove_cleans_only_selected_yay_build_cache():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")

    assert 'clean_yay_cache_for_package()' in runner
    assert 'local yay_cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/yay"' in runner
    assert 'local direct_path="$yay_cache_root/$package_name"' in runner
    assert '[[ -f "$cache_dir/.SRCINFO" ]] || continue' in runner
    assert '$1 == "pkgname" && $2 == "=" && $3 == wanted' in runner
    assert 'rm -rf -- "$cache_path"' in runner
    assert 'clean_yay_cache_for_package "$PACKAGE"' in runner
    assert 'yay-cache-cleanup=${yay_cache_cleanup_count}' in runner
    assert 'Пользовательские данные в домашней папке не удаляются автоматически.' in runner
    assert 'кэш сборки yay не удаляются автоматически' not in runner


def test_stage743_yay_cache_cleanup_happens_after_target_is_removed_and_before_orphan_cleanup():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")

    target_remove = runner.index('"$YAY" -Rns -- "$PACKAGE"')
    target_verify = runner.index('package is still installed after yay success')
    cache_cleanup = runner.index('clean_yay_cache_for_package "$PACKAGE"')
    orphan_cleanup = runner.index('"$YAY" -Rns -- "${new_orphans[@]}"')

    assert target_remove < target_verify < cache_cleanup < orphan_cleanup


def test_stage743_gui_explains_automatic_yay_cache_cleanup_and_partial_state():
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    planner = (ROOT / "src/app_store/aur/planner.py").read_text(encoding="utf-8")

    assert "Кэш сборки yay именно удаляемого AUR-пакета будет очищен автоматически." in planner
    assert "кэш сборки yay выбранного пакета очищается автоматически" in details
    assert "AUR-пакет, его кэш yay и новые ненужные зависимости удалены" in details
    assert "дополнительная очистка orphan-зависимостей или кэша yay" in details
