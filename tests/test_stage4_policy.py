from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from src.core.restore_point_policy import parse_restore_point_policy_json
from src.core.restore_point_policy_actions import (
    RestorePointPolicyAction,
    RestorePointPolicyValidationError,
    build_apply_recommended_policy_request,
    build_set_timeline_request,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_CORE = ROOT / "src" / "core" / "restore_point_policy.py"
POLICY_ACTIONS = ROOT / "src" / "core" / "restore_point_policy_actions.py"
POLICY_EXECUTOR = ROOT / "src" / "core" / "restore_point_policy_executor.py"
MANAGE_HELPER = ROOT / "src" / "privileged" / "manage_restore_points.sh"
READ_HELPER = ROOT / "src" / "privileged" / "read_restore_points.sh"
SETTINGS_PAGE = ROOT / "src" / "gui" / "settings_page.py"
MAIN_WINDOW = ROOT / "src" / "gui" / "main_window.py"


def _recommended_payload() -> str:
    return '''{
        "configured": true,
        "cleanup_timer_enabled": true,
        "timeline_timer_enabled": false,
        "values": {
            "NUMBER_CLEANUP": "yes",
            "NUMBER_MIN_AGE": "1800",
            "NUMBER_LIMIT": "10",
            "NUMBER_LIMIT_IMPORTANT": "5",
            "TIMELINE_CREATE": "no",
            "TIMELINE_CLEANUP": "yes",
            "TIMELINE_MIN_AGE": "1800",
            "TIMELINE_LIMIT_HOURLY": "5",
            "TIMELINE_LIMIT_DAILY": "7",
            "TIMELINE_LIMIT_WEEKLY": "4",
            "TIMELINE_LIMIT_MONTHLY": "3",
            "TIMELINE_LIMIT_YEARLY": "0"
        }
    }'''


def test_stage4_9_policy_parser_reads_recommended_state():
    checked = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
    state = parse_restore_point_policy_json(_recommended_payload(), checked_at=checked)
    assert state.configured is True
    assert state.readable is True
    assert state.number_limit == 10
    assert state.important_limit == 5
    assert state.cleanup_timer_enabled is True
    assert state.timeline_enabled is False
    assert state.recommended_storage is True
    assert state.checked_at == checked


def test_stage4_9_policy_parser_detects_enabled_timeline():
    payload = _recommended_payload().replace(
        '"timeline_timer_enabled": false',
        '"timeline_timer_enabled": true',
    ).replace('"TIMELINE_CREATE": "no"', '"TIMELINE_CREATE": "yes"')
    state = parse_restore_point_policy_json(payload)
    assert state.timeline_enabled is True
    assert state.recommended_storage is True


def test_stage4_9_policy_parser_handles_unconfigured_and_invalid_payloads():
    unconfigured = parse_restore_point_policy_json('{"configured":false}')
    assert unconfigured.configured is False
    assert unconfigured.readable is True

    invalid = parse_restore_point_policy_json('not-json')
    assert invalid.readable is False
    assert invalid.note


def test_stage4_9_policy_action_contract_is_fixed():
    assert {item.value for item in RestorePointPolicyAction} == {
        "policy-apply-recommended",
        "timeline-set",
    }
    assert build_apply_recommended_policy_request().helper_arguments() == (
        "policy-apply-recommended",
    )
    assert build_set_timeline_request(True).helper_arguments() == ("timeline-set", "yes")
    assert build_set_timeline_request(False).helper_arguments() == ("timeline-set", "no")
    with pytest.raises(RestorePointPolicyValidationError):
        build_set_timeline_request(1)  # type: ignore[arg-type]


def test_stage4_9_policy_contract_does_not_execute_system_commands():
    source = POLICY_ACTIONS.read_text(encoding="utf-8")
    for token in ("subprocess", "pkexec", "sudo ", "snapper ", "systemctl "):
        assert token not in source


def test_stage4_9_policy_executor_uses_existing_fixed_polkit_boundary():
    source = POLICY_EXECUTOR.read_text(encoding="utf-8")
    assert "PKEXEC_PATH" in source
    assert "HELPER_PATH" in source
    assert "_ensure_runtime_ready" in source
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "sudo " not in source


def test_stage4_9_manage_helper_has_only_fixed_policy_commands():
    source = MANAGE_HELPER.read_text(encoding="utf-8")
    assert "policy-apply-recommended)" in source
    assert "timeline-set)" in source
    assert "SYSTEMCTL=/usr/bin/systemctl" in source
    assert "NUMBER_LIMIT=10" in source
    assert "NUMBER_LIMIT_IMPORTANT=5" in source
    assert "TIMELINE_LIMIT_HOURLY=5" in source
    assert "TIMELINE_LIMIT_DAILY=7" in source
    assert "TIMELINE_LIMIT_WEEKLY=4" in source
    assert "TIMELINE_LIMIT_MONTHLY=3" in source
    assert "TIMELINE_LIMIT_YEARLY=0" in source
    assert "eval " not in source
    assert "shell=True" not in source


def test_stage4_9_recommended_storage_policy_preserves_timeline_choice():
    source = MANAGE_HELPER.read_text(encoding="utf-8")
    start = source.index("run_apply_recommended_policy()")
    end = source.index("run_set_timeline()", start)
    body = source[start:end]
    assert "TIMELINE_CREATE=yes" not in body
    assert "TIMELINE_CREATE=no" not in body
    assert "snapper-timeline.timer" not in body
    assert "snapper-cleanup.timer" in body


def test_stage4_9_read_helper_exposes_read_only_policy_status():
    source = READ_HELPER.read_text(encoding="utf-8")
    assert "--policy)" in source
    assert "get-config" in source
    assert "systemctl" in source.lower()
    for forbidden in ("set-config", " enable --now ", " disable --now ", "pkexec", "sudo "):
        assert forbidden not in source


def test_stage4_9_helpers_have_valid_shell_syntax_and_safe_self_tests():
    for helper in (MANAGE_HELPER, READ_HELPER):
        completed = subprocess.run(
            ["/usr/bin/bash", "-n", str(helper)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [str(MANAGE_HELPER), "--self-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "OK"
    completed = subprocess.run(
        [str(READ_HELPER), "--probe"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "OK"


def test_stage4_9_settings_page_enables_persistent_preupdate_protection():
    source = SETTINGS_PAGE.read_text(encoding="utf-8")
    assert "AUTO_RESTORE_POINT_BEFORE_UPDATE_KEY" in source
    assert "self.auto_restore.setEnabled(False)" not in source
    assert "self.auto_restore.toggled.connect" in source
    assert "self.settings.setValue" in source


def test_stage4_9_settings_page_manages_timeline_and_retention_via_worker():
    source = SETTINGS_PAGE.read_text(encoding="utf-8")
    assert "class _PolicyReadWorker(QRunnable)" in source
    assert "class _PolicyActionWorker(QRunnable)" in source
    assert "build_set_timeline_request" in source
    assert "build_apply_recommended_policy_request" in source
    assert "execute_restore_point_policy_action" in source
    assert "QProgressBar" in source
    for forbidden in ("pkexec", "snapper set-config", "systemctl enable", "shell=True", "sudo "):
        assert forbidden not in source


def test_stage4_9_main_window_lazy_loads_any_page_that_supports_it():
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    assert "page = self.pages[index]" in source
    assert 'hasattr(page, "ensure_loaded")' in source
    assert "if index == 2" not in source


def test_stage4_9_policy_files_are_separate_from_point_crud_contract():
    assert POLICY_CORE.is_file()
    assert POLICY_ACTIONS.is_file()
    assert POLICY_EXECUTOR.is_file()
    point_actions = (ROOT / "src" / "core" / "restore_point_actions.py").read_text(
        encoding="utf-8"
    )
    assert "policy-apply-recommended" not in point_actions
    assert "timeline-set" not in point_actions


def test_stage4_9_installer_refreshes_existing_read_helper_without_creating_sudoers():
    installer = (ROOT / "scripts" / "install-stage4-helper.sh").read_text(encoding="utf-8")
    assert 'SOURCE_READ_HELPER="$PROJECT_DIR/src/privileged/read_restore_points.sh"' in installer
    assert "INSTALLED_READ_HELPER='/usr/lib/arch-manager/read-restore-points'" in installer
    assert 'if /usr/bin/sudo /usr/bin/test -f "$INSTALLED_READ_HELPER"' in installer
    assert '"$SOURCE_READ_HELPER" "$INSTALLED_READ_HELPER"' in installer
    assert "/etc/sudoers.d" not in installer


def test_stage4_9_remains_documented_as_complete_after_stage5():
    version = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    stage4 = (ROOT / "docs" / "STAGE_4.md").read_text(encoding="utf-8")
    state = (ROOT / "docs" / "CURRENT_STATE.md").read_text(encoding="utf-8")
    plan = (ROOT / "PROJECT_PLAN.md").read_text(encoding="utf-8")
    assert '__version__ = "0.7.0-beta.1"' in version
    assert "Статус: **завершён в 0.4.4-stage4**" in stage4
    assert "Этап 4: действия и автоматизация точек восстановления — завершён" in state
    assert "**Статус: реализовано в 0.4.4-stage4.**" in plan
