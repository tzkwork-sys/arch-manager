from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "recovery/engine/arch-recovery.sh"
ISO = ROOT / "src/privileged/recovery_lib/iso.sh"
USB = ROOT / "src/privileged/recovery_lib/usb.sh"


def test_recovery_menu_has_one_blank_line_before_restore_point_prompt():
    engine = ENGINE.read_text(encoding="utf-8")
    main = engine[engine.index("main() {") :]
    ready = main.index("boot_ready")
    spacer = main.index("\n    say\n", ready)
    prompt = main.index("Enter a restore point number", spacer)
    assert ready < spacer < prompt


def test_usb_creation_builds_current_iso_from_same_recovery_engine():
    iso = ISO.read_text(encoding="utf-8")
    usb = USB.read_text(encoding="utf-8")
    assert '"$RECOVERY_ENGINE"' in iso
    assert '"$temp/profile/airootfs/usr/local/bin/arch-manager-recovery"' in iso
    assert "build_iso_if_stale" in usb
    assert 'dd if="$ISO" of="$device"' in usb
