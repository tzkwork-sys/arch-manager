from pathlib import Path
from tests.source_bundles import recovery_helper_source, restore_points_page_source

from src.core.restore_point_actions import (
    RestorePointAction,
    build_delete_many_request,
)

ROOT = Path(__file__).resolve().parents[1]


def test_restore_points_and_recovery_are_one_navigation_page():
    main = (ROOT / "src/gui/main_window.py").read_text(encoding="utf-8")
    center = (ROOT / "src/gui/recovery_center_page.py").read_text(encoding="utf-8")
    assert '("Восстановление", "edit-undo-symbolic")' in main
    assert '("Точки восстановления", "document-save-symbolic")' not in main
    assert "RecoveryCenterPage" in main
    assert "RestorePointsPage(self, embedded=True)" in center
    assert "RecoveryPage(self, embedded=True)" in center


def test_restore_point_screen_removes_slow_size_search_and_summary_clutter():
    page = restore_points_page_source(ROOT)
    assert "Поиск по названию" not in page
    assert "Только важные" not in page
    assert '"Занимает"' not in page
    assert "важных:" not in page
    assert "Список загружен." not in page
    assert "ExtendedSelection" in page
    assert "Удалить выбранные" in page


def test_batch_delete_contract_uses_one_helper_invocation():
    request = build_delete_many_request([14, 8, 14, 6])
    assert request.action is RestorePointAction.DELETE_MANY
    assert request.point_numbers == (14, 8, 6)
    assert request.helper_arguments() == ("delete-many", "14", "8", "6")
    helper = (ROOT / "src/privileged/manage_restore_points.sh").read_text(encoding="utf-8")
    assert "run_delete_many" in helper
    assert "delete-many)" in helper


def test_restore_point_reader_uses_fast_metadata_and_cache_fingerprint():
    helper = (ROOT / "src/privileged/read_restore_points.sh").read_text(encoding="utf-8")
    core = (ROOT / "src/core/restore_points.py").read_text(encoding="utf-8")
    assert "--disable-used-space" in helper
    assert "btrfs" not in helper.split("read_points()", 1)[1].split("read_fingerprint()", 1)[0]
    assert "--fingerprint" in helper
    assert "restore-points.json" in core
    assert "_load_restore_points_cache" in core


def test_recovery_state_cache_ignores_snapshot_content_changes():
    helper = (ROOT / "src/privileged/read_recovery_state.sh").read_text(encoding="utf-8")
    core = (ROOT / "src/core/recovery.py").read_text(encoding="utf-8")
    fingerprint = helper[helper.index("quick_fingerprint()") : helper.index("if [[ -d \"$BOOT\"")]
    assert 'path_stamp "$SNAPSHOTS"' not in fingerprint
    assert "snapshots_source=" in fingerprint
    assert "snapshots_options=" in fingerprint
    assert "recovery-state.json" in core


def test_recovery_boot_hides_noncritical_kernel_and_systemd_noise():
    helper = recovery_helper_source(ROOT)
    uefi = (ROOT / "recovery/archiso/efiboot/loader/entries/01-arch-manager-recovery.conf").read_text(encoding="utf-8")
    bios = (ROOT / "recovery/archiso/syslinux/syslinux-linux.cfg").read_text(encoding="utf-8")
    for source in (helper, uefi, bios):
        assert "quiet loglevel=3" in source
        assert "systemd.show_status=false" in source
        assert "udev.log_level=3" in source
