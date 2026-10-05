import pytest

from src.app_store.aur.errors import AurVersionError
from src.app_store.aur.versioning import compare_versions, update_available
from src.core.command import CommandResult


def test_versioning_uses_vercmp_argv_without_shell():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return CommandResult(tuple(args), 0, "-1\n", "")

    assert compare_versions("1:1.0-1", "2:0.1-1", runner=runner) == -1
    assert update_available("1.0-1", "1.1-1", runner=runner) is True
    assert calls[0][0] == ["vercmp", "1:1.0-1", "2:0.1-1"]


def test_versioning_rejects_invalid_output():
    def runner(args, **kwargs):
        return CommandResult(tuple(args), 0, "wat", "")

    with pytest.raises(AurVersionError, match="invalid"):
        compare_versions("1", "2", runner=runner)
