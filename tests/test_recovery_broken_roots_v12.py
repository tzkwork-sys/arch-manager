from pathlib import Path
from tests.source_bundles import recovery_helper_source, recovery_page_source

from src.core.recovery import _parse_broken_roots
from src.core.recovery_executor import RecoveryValidationError, delete_broken_roots

ROOT = Path(__file__).resolve().parents[1]


def test_read_probe_reports_only_strict_broken_root_names_and_fingerprints_them():
    helper = (ROOT / "src/privileged/read_recovery_state.sh").read_text(encoding="utf-8")
    assert "list_broken_roots" in helper
    assert "broken_root=%s" in helper
    assert "@\\.broken-" in helper
    fingerprint = helper[helper.index("quick_fingerprint()") : helper.index('if [[ -d "$BOOT"')]
    assert "list_broken_roots" in fingerprint


def test_parser_accepts_only_expected_old_root_names():
    text = "\n".join(
        (
            "version=2",
            "broken_root=@.broken-20260923-202454",
            "broken_root=../../etc",
            "broken_root=@",
            "broken_root=@.broken-20260924-010203",
        )
    )
    assert _parse_broken_roots(text) == (
        "@.broken-20260924-010203",
        "@.broken-20260923-202454",
    )


def test_privileged_delete_boundary_is_non_recursive_and_top_level_only():
    helper = recovery_helper_source(ROOT)
    assert "delete_broken_roots" in helper
    assert "delete-broken)" in helper
    assert "^@\\.broken-[0-9]{8}-[0-9]{6}$" in helper
    assert "Top level ID:" in helper
    assert "btrfs subvolume list -o" in helper
    assert "broken_root_nested" in helper
    assert "subvolume delete" in helper
    assert "delete -R" not in helper and "subvolume delete -R" not in helper


def test_broken_root_management_is_contextual_in_recovery_card():
    page = recovery_page_source(ROOT)
    dialog = (ROOT / "src/gui/broken_roots_dialog.py").read_text(encoding="utf-8")
    assert 'setText("⚙")' in page
    assert "BrokenRootsDialog" in page
    assert "Удалить выбранные" in dialog
    assert "ExtendedSelection" in dialog
    assert "нескольких успешных загрузок" in dialog


def test_executor_rejects_invalid_broken_root_before_privilege_boundary():
    try:
        delete_broken_roots(("@",))
    except RecoveryValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("invalid old-root name was accepted")
