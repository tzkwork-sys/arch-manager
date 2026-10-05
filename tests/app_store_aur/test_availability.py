from src.app_store.aur.availability import AurAvailabilityProbe
from src.core.command import CommandResult


def command_result(args, stdout="", rc=0):
    return CommandResult(tuple(args), rc, stdout, "")


def test_capability_probe_reports_ready_user_environment():
    present = {"yay", "git", "makepkg", "vercmp", "pacman"}

    def which(name):
        return f"/usr/bin/{name}" if name in present else None

    def runner(args, **kwargs):
        if args == ["yay", "--version"]:
            return command_result(args, "yay v13.0.1 - libalpm v15\n")
        if args == ["pacman", "-Qq", "base-devel"]:
            return command_result(args, "base-devel\n")
        raise AssertionError(args)

    report = AurAvailabilityProbe(runner=runner, which=which, geteuid=lambda: 1000).probe()

    assert report.supported is True
    assert report.base_devel_complete is True
    assert report.yay_version.startswith("yay v13")
    assert report.issues == ()


def test_capability_probe_detects_root_and_missing_base_devel():
    def which(name):
        return f"/usr/bin/{name}"

    def runner(args, **kwargs):
        if args[0] == "yay":
            return command_result(args, "yay 1\n")
        if args == ["pacman", "-Qq", "base-devel"]:
            return command_result(args, rc=1)
        raise AssertionError(args)

    report = AurAvailabilityProbe(runner=runner, which=which, geteuid=lambda: 0).probe()

    assert report.supported is False
    assert report.missing_base_devel == ("base-devel",)
    assert "running-as-root" in report.issues
    assert "base-devel-incomplete" in report.issues
