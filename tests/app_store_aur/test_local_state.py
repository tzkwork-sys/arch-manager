import pytest

from src.app_store.aur.errors import AurUnavailable
from src.app_store.aur.local_state import ForeignPackageReader, parse_foreign_packages
from src.core.command import CommandResult


def result(command, rc=0, stdout="", stderr="", available=True, timed_out=False):
    return CommandResult(tuple(command), rc, stdout, stderr, available=available, timed_out=timed_out)


def test_parse_foreign_packages_ignores_invalid_lines_and_names():
    packages = parse_foreign_packages("demo-git 1.0-1\n-bad 2\nlocal.pkg 3.4\nbroken\n")
    assert [(item.name, item.installed_version) for item in packages] == [
        ("demo-git", "1.0-1"),
        ("local.pkg", "3.4"),
    ]


def test_foreign_reader_uses_only_pacman_qm():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return result(args, stdout="demo-git 1.0-1\n")

    packages = ForeignPackageReader(runner=runner).read()
    assert packages[0].name == "demo-git"
    assert calls[0][0] == ["pacman", "-Qm"]


def test_foreign_reader_fails_controlled_when_pacman_missing():
    def runner(args, **kwargs):
        return result(args, rc=127, available=False)

    with pytest.raises(AurUnavailable, match="pacman"):
        ForeignPackageReader(runner=runner).read()
