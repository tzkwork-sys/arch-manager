"""Portable settings backups are narrow, validated and never execute imported code."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from src.core.preferences import (
    AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY as BEFORE,
    MAINTENANCE_PACKAGE_CACHE_KEEP_KEY as KEEP,
)
from src.core.restore_point_policy import RestorePointPolicyState
from src.core.settings_backup import (
    MAX_BYTES, MAX_NUMBER, MAX_LIMIT, apply_preferences, load_backup, make_backup,
    policy_helper_arguments, policy_values, save_backup, validate_backup,
)

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src/privileged/manage_restore_points.sh"
GUI = ROOT / "src/gui/settings_page.py"
EXECUTOR = ROOT / "src/core/settings_backup_executor.py"


class FakeSettings:
    def __init__(self):
        self._values = {}
        self.synced = False

    def setValue(self, key, value):
        self._values[key] = value

    def sync(self):
        self.synced = True

    def status(self):
        return 0


def _policy():
    return RestorePointPolicyState(
        configured=True, readable=True, checked_at=datetime.now(timezone.utc),
        number_cleanup=True, number_min_age=1800,
        number_limit=10, important_limit=5,
        timeline_create=False, timeline_cleanup=True, timeline_min_age=1800,
        timeline_hourly=5, timeline_daily=7, timeline_weekly=4,
        timeline_monthly=3, timeline_yearly=0,
        cleanup_timer_enabled=True, timeline_timer_enabled=False,
    )


def _backup():
    return make_backup({BEFORE: False, KEEP: 2}, policy_values(_policy()))


def test_full_roundtrip_and_helper_argument_order(tmp_path):
    backup = _backup()
    path = tmp_path / "settings.json"
    save_backup(path, backup)
    restored = load_backup(path)
    assert restored == backup
    assert path.stat().st_mode & 0o777 == 0o600
    assert restored["preferences"] == {BEFORE: False, KEEP: 2}
    args = policy_helper_arguments(restored["snapper"])
    assert args == (
        "policy-restore", "yes", "1800", "10", "5", "no", "yes",
        "1800", "5", "7", "4", "3", "0", "yes", "no",
    )
    settings = FakeSettings()
    apply_preferences(settings, restored["preferences"])
    assert settings._values == {BEFORE: False, KEEP: 2}
    assert settings.synced


def test_user_only_backup_when_snapper_inaccessible(tmp_path):
    state = _policy()
    state = RestorePointPolicyState(configured=True, readable=False, checked_at=state.checked_at)
    assert policy_values(state) is None
    backup = make_backup({BEFORE: True, KEEP: 3}, None)
    path = tmp_path / "backup.json"
    save_backup(path, backup)
    assert load_backup(path)["snapper"] is None


@pytest.mark.parametrize("change", [
    lambda b: b.update(version=True),
    lambda b: b.update(format="unexpected"),
    lambda b: b.update(unexpected_key="evil"),
    lambda b: b["preferences"].update({"root/command": "rm -rf /"}),
    lambda b: b["preferences"].update({BEFORE: "no"}),
    lambda b: b["preferences"].update({KEEP: True}),
    lambda b: b["preferences"].update({KEEP: 999999}),
    lambda b: b["snapper"].update({"NUMBER_LIMIT": "10;id"}),
    lambda b: b["snapper"].update({"NUMBER_LIMIT": True}),
    lambda b: b["snapper"].update({"NUMBER_LIMIT": MAX_LIMIT + 1}),
    lambda b: b["snapper"].update({"NUMBER_MIN_AGE": MAX_NUMBER + 1}),
    lambda b: b["snapper"].update({"extra_key": "value"}),
    lambda b: b["snapper"].pop("NUMBER_CLEANUP"),
    lambda b: b["snapper"].update({"timeline_timer_enabled": "true"}),
])
def test_reject_invalid_backup_values(change):
    backup = _backup()
    change(backup)
    with pytest.raises(ValueError):
        validate_backup(backup)


def test_reject_large_or_malformed_files_and_symlinks(tmp_path):
    original = tmp_path / "original.json"
    original.write_bytes(b"x" * (MAX_BYTES + 1))
    with pytest.raises(ValueError, match="64"):
        load_backup(original)
    other = tmp_path / "broken.json"
    other.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_backup(other)
    link = tmp_path / "link.json"
    link.symlink_to(original)
    with pytest.raises((OSError, ValueError)):
        load_backup(link)


def test_root_boundary_is_fixed_and_rejects_bad_input_before_root():
    text = HELPER.read_text(encoding="utf-8")
    assert "policy-restore)" in text
    assert "run_restore_saved_policy" in text
    assert "NUMBER_LIMIT=$number_limit" in text
    assert "settings-policy-backups" in text
    assert "eval " not in text
    assert "bash -c" not in text
    assert "shell=True" not in EXECUTOR.read_text(encoding="utf-8")
    assert "_ensure_runtime_ready" in EXECUTOR.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(HELPER)], capture_output=True).returncode == 0
    assert subprocess.run([str(HELPER), "--self-test"], capture_output=True).returncode == 0
    args = list(policy_helper_arguments(_backup()["snapper"]))
    args[3] = "4;id"
    result = subprocess.run([str(HELPER), *args], capture_output=True, text=True)
    assert result.returncode != 0
    assert "policy value" in result.stderr


def test_gui_exposes_two_buttons_and_keeps_polkit_off_ui_thread():
    gui = GUI.read_text(encoding="utf-8")
    assert '"settingsExportButton"' in gui
    assert '"settingsImportButton"' in gui
    assert "class _BackupPolicyWorker(QRunnable)" in gui
    assert "apply_saved_snapper_policy(self.policy)" in gui
    assert "QFileDialog.getOpenFileName" in gui
    assert "QFileDialog.getSaveFileName" in gui
    assert "self.general_scroll.setWidgetResizable(True)" in gui
