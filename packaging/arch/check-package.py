#!/usr/bin/env python3
"""Read-only package/layout checks and isolated Qt startup, without transactions."""
from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HELPERS = ("manage-applications", "manage-maintenance", "manage-restore-points", "read-system-diagnostics")


def check(root: Path, *, source: bool = False) -> None:
    app = root if source else root / "usr/share/arch-manager"
    assert (app / "main.py").is_file()
    for name, module in zip(HELPERS, ("app_store_executor", "maintenance_executor", "restore_point_executor", "system_privileged_diagnostics")):
        text = (app / f"src/core/{module}.py").read_text()
        assert f'/usr/lib/arch-manager/{name}' in text
        policy = (root / f"packaging/polkit/org.archmanager.{name}.policy" if source else
                  root / f"usr/share/polkit-1/actions/org.archmanager.{name}.policy")
        action = ET.parse(policy).find("action")
        assert action is not None and action.attrib["id"] == f"org.archmanager.{name}"
        assert action.findtext("defaults/allow_active") == "auth_admin"
        assert action.findtext("defaults/allow_any") == "no"
        assert action.findtext("defaults/allow_inactive") == "no"
        assert action.findtext("annotate") == f"/usr/lib/arch-manager/{name}"
    if not source:
        assert not (root / "usr/local").exists()
        assert not (root / "etc/sudoers.d").exists()
        assert not (root / "usr/lib/arch-manager/manage-recovery").exists()
        assert not (root / "usr/share/polkit-1/actions/org.archmanager.manage-recovery.policy").exists()
        assert not (app / "src/privileged").exists()
        assert not list(app.rglob("__pycache__"))
        for name in (*HELPERS, "read-restore-points"):
            path = root / "usr/lib/arch-manager" / name
            assert stat.S_IMODE(path.stat().st_mode) == 0o755 and not path.is_symlink()
            flag = "--probe" if name == "read-restore-points" else "--self-test"
            subprocess.run([str(path), flag], check=True, capture_output=True, timeout=10)
        assert (root / "usr/bin/arch-manager-gui").is_file()
        assert (root / "usr/share/licenses/arch-manager/COPYING").is_file()
        subprocess.run(["desktop-file-validate", str(root / "usr/share/applications/arch-manager-gui.desktop")], check=True)

    # Patch all process starts before constructing the real window. No host
    # inspection, sudo, package operations, ISO builds or disk writes can run.
    script = r'''
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from src import __version__
from src.app import create_application
from src.gui.dashboard import DashboardPage
from src.gui.main_window import MainWindow
from src.gui.lifecycle import shutdown_application_widgets
assert __version__ == "0.7.0-beta.1"
def forbidden(*args, **kwargs):
    raise AssertionError("System subprocess is forbidden in package smoke tests")
with patch('subprocess.Popen', forbidden), patch('os.system', forbidden), patch.object(DashboardPage, 'refresh', lambda self: None):
    app = create_application([])
    window = MainWindow()
    window.show()
    assert window.windowTitle() == 'Arch Manager'
    shutdown_application_widgets()
'''
    with tempfile.TemporaryDirectory(prefix="arch-manager-package-check-") as temp:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "XDG_CONFIG_HOME": temp,
               "XDG_CACHE_HOME": temp, "XDG_STATE_HOME": temp, "PYTHONDONTWRITEBYTECODE": "1"}
        # -I ignores PYTHONDONTWRITEBYTECODE, so -B must be explicit as well.
        subprocess.run([sys.executable, "-I", "-B", "-c", script, str(app)], env=env, cwd=temp, check=True, timeout=30)
    if not source:
        assert not list(app.rglob("__pycache__"))
    print("Package layout, Polkit paths and isolated Qt startup: OK")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve(), source="--source" in sys.argv[2:])
