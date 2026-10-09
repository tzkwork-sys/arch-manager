import importlib.machinery
import importlib.util
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "packaging/arch"


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_package_recipe_syntax_and_immutable_source():
    subprocess.run(["bash", "-n", str(RECIPE / "PKGBUILD")], check=True)
    subprocess.run(["bash", "-n", str(RECIPE / "enable-snapper-read.sh")], check=True)
    recipe = (RECIPE / "PKGBUILD").read_text()
    assert "7116dcf211b5d722dcd0e877ca9cb75a9e8036724cff3f525c4d2e57d111169e" in recipe
    assert "SKIP" not in recipe and "PLACEHOLDER" not in recipe
    assert "pkgver=0.7.0beta1" in recipe
    assert "'xterm'" in recipe
    assert "install-stage" not in recipe
    assert "manage-recovery" not in (RECIPE / "arch-manager.rules").read_text()
    sums = re.findall(r"'([0-9a-f]{64})'", recipe)
    for name, digest in zip(("prepare-layout.py", "arch-manager-gui", "enable-snapper-read.sh",
                             "arch-manager.rules", "check-package.py", "README.md"), sums[1:]):
        assert hashlib.sha256((RECIPE / name).read_bytes()).hexdigest() == digest
    assert len(sums) == 7


def test_package_permission_setup_never_replaces_helper():
    source = (RECIPE / "enable-snapper-read.sh").read_text()
    assert "install -o root -g root -m 0440" in source
    assert "install -o root -g root -m 0755" not in source
    assert 'rm -f -- "$helper"' not in source
    for args in ([], ["invalid"], ["0"], ["1", "2"]):
        result = subprocess.run(["bash", str(RECIPE / "enable-snapper-read.sh"), *args], capture_output=True, text=True)
        assert result.returncode == 1


def test_packaged_layout_and_authorization_match(tmp_path):
    for name in ("src", "scripts", "packaging", "desktop"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "main.py", tmp_path / "main.py")
    prepare = load_module("arch_package_prepare", RECIPE / "prepare-layout.py")
    prepare.prepare(tmp_path)
    checker = load_module("arch_package_checker", RECIPE / "check-package.py")
    checker.check(tmp_path, source=True)
    assert "/usr/lib/arch-manager/manage-restore-points" in (tmp_path / "scripts/run-system-update-session.sh").read_text()
    assert (tmp_path / "src/privileged/read_system_diagnostics.py").read_text().startswith("#!/usr/bin/python -I\n")
    # ISO-internal paths stay untouched; the package must not silently rewire recovery.
    assert (tmp_path / "src/privileged/recovery_lib/iso.sh").read_bytes() == (ROOT / "src/privileged/recovery_lib/iso.sh").read_bytes()


def test_launcher_check_reports_missing_helpers_without_running_commands(monkeypatch, capsys, tmp_path):
    launcher = load_module("arch_package_launcher", RECIPE / "arch-manager-gui")
    monkeypatch.setattr(launcher, "APP_DIR", tmp_path)
    monkeypatch.setattr(launcher, "HELPER_DIR", tmp_path / "missing")
    assert launcher.check_environment() == 1
    assert "Required helper is missing" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("makepkg") is None, reason="makepkg is Arch-specific")
def test_srcinfo_matches_recipe():
    result = subprocess.run(["makepkg", "--printsrcinfo"], cwd=RECIPE, text=True, capture_output=True, check=True)
    assert result.stdout == (RECIPE / ".SRCINFO").read_text()
