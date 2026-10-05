from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v1820_recovery_finalizes_utf8_console_once_before_first_menu_draw():
    engine = (ROOT / "recovery/engine/arch-recovery.sh").read_text(encoding="utf-8")
    profile = (ROOT / "recovery/archiso/profiledef.sh").read_text(encoding="utf-8")
    customize = (ROOT / "recovery/archiso/airootfs/root/customize_airootfs.sh").read_text(encoding="utf-8")
    service = (ROOT / "recovery/archiso/airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    bootlog = (ROOT / "recovery/archiso/airootfs/usr/local/libexec/arch-manager/recovery-bootlog").read_text(encoding="utf-8")
    main = engine[engine.index("main() {"):]

    assert 'APP_VERSION="1.9.0"' in engine
    assert 'iso_version="15"' in profile
    assert 'find_system_device >/dev/null' in main
    assert "prepare_recovery_console()" in engine
    assert 'LatArCyrHeb-16.psfu.gz' in engine
    assert 'setfont -C "$tty" "$font_file"' in engine
    assert 'Could not load the Recovery console font.' not in engine
    assert engine.count("printf '\\033%%G'") == 1
    assert "kbd_mode" not in engine
    assert "udevadm settle --timeout=15" in engine
    assert "sleep 1" not in engine[engine.index("prepare_recovery_console()") : engine.index("require_live_environment()") ]
    assert "systemd-vconsole-setup.service" in customize
    assert "systemd-vconsole-setup.service" not in service
    assert "Environment=LANG=C.UTF-8" in service
    assert "Environment=LC_ALL=C.UTF-8" in service
    assert 'bootlog-init version=1.9.0' in bootlog

    mount_done = main.index('boot_stage "mount-system-top-done"')
    stability = main.index("wait_for_recovery_console_stable", mount_done)
    console = main.index("prepare_recovery_console", stability)
    clear = main.index("clear_recovery_console", console)
    banner = main.index("print_recovery_header", clear)
    list_pos = main.index("list_restore_points", banner)
    ready = main.index("boot_ready", list_pos)
    assert mount_done < stability < console < clear < banner < list_pos < ready
    assert "systemd-udev-settle.service" in service
    assert "activate_recovery_console" not in engine
    assert "chvt" not in engine


def test_v1820_preserves_utf8_restore_point_descriptions_without_ascii_placeholder():
    engine = (ROOT / "recovery/engine/arch-recovery.sh").read_text(encoding="utf-8")
    assert "[non-ASCII description]" not in engine
    assert '"${desc:0:80}"' not in engine

    result = subprocess_run_utf8_description()
    assert result == "Перед обновлением системы"


def subprocess_run_utf8_description():
    import subprocess
    result = subprocess.run(
        [
            "bash",
            "-lc",
            "source <(sed '$d' recovery/engine/arch-recovery.sh); "
            "display_description 'Перед обновлением системы'",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8")


def test_v1820_keeps_bootlog_and_watchdog_disarm_after_restore_point_list():
    engine = (ROOT / "recovery/engine/arch-recovery.sh").read_text(encoding="utf-8")
    bootlog = (ROOT / "recovery/archiso/airootfs/usr/local/libexec/arch-manager/recovery-bootlog").read_text(encoding="utf-8")
    watchdog = (ROOT / "recovery/archiso/airootfs/usr/local/libexec/arch-manager/recovery-watchdog").read_text(encoding="utf-8")
    main = engine[engine.index("main() {"):]

    list_done = main.index('boot_stage "list-restore-points-done count=${#SNAPSHOT_IDS[@]}"')
    ready = main.index("boot_ready", list_done)
    prompt = main.index("Enter a restore point number", ready)
    assert list_done < ready < prompt
    assert 'bootlog-init version=1.9.0' in bootlog
    assert 'TIMEOUT_SECONDS=60' in watchdog
