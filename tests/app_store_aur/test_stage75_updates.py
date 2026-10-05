from pathlib import Path

import pytest

from src.app_store.aur.availability import AurCapabilityReport
from src.app_store.aur.errors import AurSystemUpdateRequired, AurUnavailable
from src.app_store.aur.models import AurPackage
from src.app_store.aur.planner import AurUpdateAllPlan, AurUpdatePlan, AurUpdatePlanner
from src.core.command import CommandResult


ROOT = Path(__file__).resolve().parents[2]


def _result(argv, *, rc=0, stdout="", stderr=""):
    return CommandResult(tuple(argv), rc, stdout, stderr, True, False)


def _report():
    return AurCapabilityReport(
        yay_available=True,
        yay_version="yay 13",
        pacman_available=True,
        git_available=True,
        makepkg_available=True,
        vercmp_available=True,
        base_devel_complete=True,
        missing_base_devel=(),
        running_as_root=False,
    )


class FakeService:
    version_runner = staticmethod(lambda argv, timeout=10: _result(argv, rc=0, stdout="-1\n"))

    def capabilities(self):
        return _report()

    def info(self, names, *, use_cache=False):
        return tuple(
            AurPackage(name=name, package_base=name, version="2.0-1", installed=True,
                       installed_version="1.0-1", update_available=True)
            for name in names
        )


class Runner:
    def __init__(self, *, official_updates=""):
        self.official_updates = official_updates
        self.calls = []

    def __call__(self, argv, timeout=10):
        self.calls.append(tuple(argv))
        if argv[:2] == ["pacman", "-Qu"]:
            return _result(argv, rc=0, stdout=self.official_updates)
        if argv[:2] == ["pacman", "-Q"]:
            return _result(argv, stdout=f"{argv[-1]} 1.0-1\n")
        if argv[:2] == ["pacman", "-Qm"]:
            return _result(argv, rc=0, stdout=f"{argv[-1]} 1.0-1\n")
        raise AssertionError(argv)


def _package():
    return AurPackage(
        name="demo-aur",
        package_base="demo-aur",
        version="2.0-1",
        installed=True,
        installed_version="1.0-1",
        update_available=True,
    )


def test_stage75_single_update_plan_and_system_update_gate(tmp_path):
    runner = Runner()
    planner = AurUpdatePlanner(FakeService(), runner=runner, lock_path=tmp_path / "db.lck")
    plan = planner.plan(_package())
    assert isinstance(plan, AurUpdatePlan)
    assert plan.command_preview == ("yay", "-S", "--aur", "--", "demo-aur")

    blocked = AurUpdatePlanner(
        FakeService(), runner=Runner(official_updates="linux 1 -> 2\n"), lock_path=tmp_path / "other.lck"
    )
    with pytest.raises(AurSystemUpdateRequired):
        blocked.plan(_package())


def test_stage75_update_all_plan_is_aur_only(tmp_path):
    planner = AurUpdatePlanner(FakeService(), runner=Runner(), lock_path=tmp_path / "db.lck")
    plan = planner.plan_all(("demo-aur", "other-aur", "demo-aur"))
    assert isinstance(plan, AurUpdateAllPlan)
    assert plan.package_names == ("demo-aur", "other-aur")
    assert plan.command_preview == ("yay", "-Sua")
    with pytest.raises(AurUnavailable):
        planner.plan_all(())


def test_stage75_runner_and_gui_contracts():
    runner = (ROOT / "scripts/run-aur-operation.sh").read_text(encoding="utf-8")
    terminal = (ROOT / "src/gui/app_store/aur/terminal.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")

    assert '"$YAY" -Sua' in runner
    assert '"$YAY" -S --aur -- "$PACKAGE"' in runner
    assert 'update-all' in terminal
    assert 'appStoreAurUpdateAllButton' in page
    assert 'AurUpdateAllPlan' in page
    assert 'item.update_available' in page
    assert 'AurUpdatePlan' in details
    assert 'Обновить через yay' in details
    assert 'self.remove_button.setVisible(True)' in details
    assert '--noconfirm' not in runner

@pytest.mark.skipif(__import__("importlib.util").util.find_spec("PySide6") is None, reason="PySide6 is not installed in the artifact test environment")
def test_stage75_aur_details_buttons_use_semantic_colors():
    from PySide6.QtWidgets import QApplication
    from src.gui.app_store.aur.details import AurPackageDetailsDialog

    app = QApplication.instance() or QApplication([])

    available = AurPackage(name="demo-aur", package_base="demo-aur", version="2.0-1")
    available_dialog = AurPackageDetailsDialog(available, aur_service=FakeService())
    assert "#2f6feb" in available_dialog.install_button.styleSheet()
    available_dialog.close()
    available_dialog.deleteLater()

    updating = AurPackage(
        name="demo-aur",
        package_base="demo-aur",
        version="2.0-1",
        installed=True,
        installed_version="1.0-1",
        update_available=True,
    )
    updating_dialog = AurPackageDetailsDialog(updating, aur_service=FakeService())
    assert "#c69026" in updating_dialog.install_button.styleSheet()
    assert "#d05c5c" in updating_dialog.remove_button.styleSheet()
    updating_dialog.close()
    updating_dialog.deleteLater()

    installed = AurPackage(
        name="demo-aur",
        package_base="demo-aur",
        version="2.0-1",
        installed=True,
        installed_version="2.0-1",
        update_available=False,
    )
    installed_dialog = AurPackageDetailsDialog(installed, aur_service=FakeService())
    assert "#d05c5c" in installed_dialog.install_button.styleSheet()
    installed_dialog.close()
    installed_dialog.deleteLater()
    app.processEvents()

