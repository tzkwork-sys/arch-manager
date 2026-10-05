from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_1817_has_persistent_bootlog_and_60_second_startup_watchdog():
    base = ROOT / "recovery/archiso/airootfs"
    bootlog = (base / "usr/local/libexec/arch-manager/recovery-bootlog").read_text(encoding="utf-8")
    watchdog = (base / "usr/local/libexec/arch-manager/recovery-watchdog").read_text(encoding="utf-8")
    watchdog_service = (base / "etc/systemd/system/arch-manager-recovery-watchdog.service").read_text(encoding="utf-8")
    service = (base / "etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    profile = (ROOT / "recovery/archiso/profiledef.sh").read_text(encoding="utf-8")
    engine = (ROOT / "recovery/engine/arch-recovery.sh").read_text(encoding="utf-8")

    assert 'APP_VERSION="1.9.0"' in engine
    assert 'iso_version="15"' in profile
    assert 'TIMEOUT_SECONDS=60' in watchdog
    assert '/run/arch-manager/recovery-ready' in watchdog
    assert 'ExecStartPre=/usr/local/libexec/arch-manager/recovery-bootlog init' in watchdog_service
    assert 'Before=arch-manager-recovery.service' in watchdog_service
    assert 'arch-manager-recovery-watchdog.service' in service

    assert '/run/arch-manager/recovery-boot.log' in bootlog
    assert "@snapshots/.arch-manager-recovery/boot" in bootlog
    assert 'last-boot.log' in bootlog
    assert 'journalctl -b --no-pager -n 250' in bootlog
    assert 'dmesg' in bootlog
    assert 'timeout -k 2 8 /usr/bin/mount' in bootlog


def test_watchdog_disarms_before_user_choice_but_after_real_recovery_startup():
    engine = (ROOT / "recovery/engine/arch-recovery.sh").read_text(encoding="utf-8")
    main = engine[engine.index("main() {") :]
    find_start = main.index('boot_stage "find-system-start"')
    find_done = main.index('boot_stage "find-system-done device=$TARGET_DEVICE"')
    mount_done = main.index('boot_stage "mount-system-top-done"')
    list_done = main.index('boot_stage "list-restore-points-done count=${#SNAPSHOT_IDS[@]}"')
    ready = main.index("boot_ready", list_done)
    prompt = main.index("Enter a restore point number", ready)
    assert find_start < find_done < mount_done < list_done < ready < prompt


def test_watchdog_timeout_returns_local_boot_and_powers_off_usb_to_avoid_boot_loop():
    watchdog = (ROOT / "recovery/archiso/airootfs/usr/local/libexec/arch-manager/recovery-watchdog").read_text(encoding="utf-8")
    assert 'if [[ "$mode" == local ]]' in watchdog
    assert '/usr/bin/systemctl --no-block reboot' in watchdog
    assert '/usr/bin/systemctl --no-block poweroff' in watchdog
    assert 'Recovery boot loop' in watchdog


def test_watchdog_and_bootlog_are_executable_in_archiso_profile():
    profile = (ROOT / "recovery/archiso/profiledef.sh").read_text(encoding="utf-8")
    customize = (ROOT / "recovery/archiso/airootfs/root/customize_airootfs.sh").read_text(encoding="utf-8")
    wants = ROOT / "recovery/archiso/airootfs/etc/systemd/system/multi-user.target.wants/arch-manager-recovery-watchdog.service"
    assert '["/usr/local/libexec/arch-manager/recovery-bootlog"]="0:0:755"' in profile
    assert '["/usr/local/libexec/arch-manager/recovery-watchdog"]="0:0:755"' in profile
    assert 'systemctl enable arch-manager-recovery-watchdog.service' in customize
    assert wants.is_symlink()
    assert wants.readlink() == Path("../arch-manager-recovery-watchdog.service")
