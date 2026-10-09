from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_large_gui_pages_are_split_into_cohesive_modules():
    recovery_page = ROOT / "src/gui/recovery_page.py"
    restore_page = ROOT / "src/gui/restore_points_page.py"
    assert len(recovery_page.read_text(encoding="utf-8").splitlines()) < 800
    assert len(restore_page.read_text(encoding="utf-8").splitlines()) < 800
    assert (ROOT / "src/gui/recovery_usb_mixin.py").is_file()
    assert (ROOT / "src/gui/recovery_workers.py").is_file()
    assert (ROOT / "src/gui/restore_point_actions_mixin.py").is_file()
    assert (ROOT / "src/gui/restore_point_workers.py").is_file()


def test_privileged_recovery_helper_is_a_small_dispatcher_with_fixed_modules():
    helper = (ROOT / "src/privileged/manage_recovery.sh").read_text(encoding="utf-8")
    assert len(helper.splitlines()) < 120
    assert 'RECOVERY_LIB_DIR="$SCRIPT_DIR/recovery_lib"' in helper
    for name in ("common.sh", "iso.sh", "usb.sh", "local.sh"):
        assert f"load_recovery_module {name}" in helper
        assert (ROOT / "src/privileged/recovery_lib" / name).is_file()


def test_installer_deploys_privileged_modules_and_active_recovery_engine():
    installer = (ROOT / "scripts/install-stage7-recovery-helper.sh").read_text(encoding="utf-8")
    assert 'SOURCE_RECOVERY="$PROJECT_DIR/recovery/engine/arch-recovery.sh"' in installer
    assert 'SOURCE_HELPER_LIB="$PROJECT_DIR/src/privileged/recovery_lib"' in installer
    assert "HELPER_LIB='/usr/local/libexec/arch-manager/recovery_lib'" in installer
    assert 'install -o root -g root -m 0644 "$SOURCE_HELPER_LIB/$module" "$HELPER_LIB_STAGE/$module"' in installer


def test_legacy_recovery_entry_is_only_a_compatibility_wrapper():
    active = ROOT / "recovery/engine/arch-recovery.sh"
    legacy = (ROOT / "legacy/console-v1.8.4/arch-recovery.sh").read_text(encoding="utf-8")
    assert active.is_file()
    assert len(active.read_text(encoding="utf-8").splitlines()) > 500
    assert "Compatibility entry point" in legacy
    assert "../../recovery/engine/arch-recovery.sh" in legacy
    assert len(legacy.splitlines()) < 20

def test_recovery_page_resolves_bootnext_capability_probe():
    source = (ROOT / "src/gui/recovery_page.py").read_text(encoding="utf-8")
    assert "and uefi_usb_bootnext_available()" in source
    assert (
        "from src.core.recovery_usb import "
        "RecoveryUsbDevice, uefi_usb_bootnext_available"
    ) in source
