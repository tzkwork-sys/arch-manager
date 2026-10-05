from pathlib import Path

import pytest

from src.core.restore_point_actions import (
    MAX_DESCRIPTION_LENGTH,
    RestorePointAction,
    RestorePointActionValidationError,
    build_create_request,
    build_delete_request,
    build_rename_request,
    build_set_importance_request,
    normalize_restore_point_description,
    validate_restore_point_number,
)

ROOT = Path(__file__).resolve().parents[1]


def test_stage4_required_files_exist():
    assert (ROOT / "docs" / "STAGE_4.md").is_file()
    assert (ROOT / "src" / "core" / "restore_point_actions.py").is_file()


def test_stage4_action_contract_covers_only_planned_actions():
    assert {action.value for action in RestorePointAction} == {
        "create",
        "rename",
        "set-importance",
        "delete",
        "delete-many",
    }


def test_stage4_snapshot_number_must_be_positive_integer():
    assert validate_restore_point_number(1) == 1
    assert validate_restore_point_number(99999) == 99999

    for value in (0, -1, True, False, 1.5, "7", None):
        with pytest.raises(RestorePointActionValidationError):
            validate_restore_point_number(value)  # type: ignore[arg-type]


def test_stage4_description_is_single_line_and_bounded():
    assert normalize_restore_point_description("  После   настройки\nсистемы  ") == (
        "После настройки системы"
    )
    assert normalize_restore_point_description("A" * MAX_DESCRIPTION_LENGTH) == (
        "A" * MAX_DESCRIPTION_LENGTH
    )

    for value in ("", "   ", "bad\x00name", "bad\x1bname", "A" * (MAX_DESCRIPTION_LENGTH + 1)):
        with pytest.raises(RestorePointActionValidationError):
            normalize_restore_point_description(value)


def test_stage4_create_request_has_fixed_helper_arguments():
    normal = build_create_request("Перед ручной настройкой")
    important = build_create_request("Перед обновлением", important=True)

    assert normal.helper_arguments() == (
        "create",
        "no",
        "Перед ручной настройкой",
    )
    assert important.helper_arguments() == (
        "create",
        "yes",
        "Перед обновлением",
    )


def test_stage4_existing_point_requests_have_fixed_helper_arguments():
    assert build_rename_request(41, "Новое имя").helper_arguments() == (
        "rename",
        "41",
        "Новое имя",
    )
    assert build_set_importance_request(41, True).helper_arguments() == (
        "set-importance",
        "41",
        "yes",
    )
    assert build_set_importance_request(41, False).helper_arguments() == (
        "set-importance",
        "41",
        "no",
    )
    assert build_delete_request(41).helper_arguments() == ("delete", "41")


def test_stage4_action_contract_does_not_execute_privileged_commands():
    source = (ROOT / "src" / "core" / "restore_point_actions.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "subprocess",
        "os.system",
        "shell=True",
        "sudo ",
        "pkexec",
        "snapper ",
    )
    assert all(token not in source for token in forbidden)
