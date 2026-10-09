from pathlib import Path
from tests.source_bundles import recovery_engine_source, recovery_helper_source, recovery_page_source
import json

from src.core.command import CommandResult
from src.core.recovery_usb import collect_recovery_usb_devices


ROOT = Path(__file__).resolve().parents[1]


def test_usb_discovery_excludes_running_system_disk_and_marks_recovery_media():
    payload = {
        "blockdevices": [
            {
                "name": "nvme0n1",
                "path": "/dev/nvme0n1",
                "type": "disk",
                "size": 500_000_000_000,
                "model": "System Disk",
                "tran": "nvme",
                "rm": False,
                "ro": False,
                "label": None,
                "children": [
                    {"path": "/dev/nvme0n1p4", "type": "part", "label": None}
                ],
            },
            {
                "name": "sdb",
                "path": "/dev/sdb",
                "type": "disk",
                "size": 32_000_000_000,
                "model": "DataTraveler",
                "tran": "usb",
                "rm": False,
                "ro": False,
                "label": None,
                "children": [
                    {"path": "/dev/sdb1", "type": "part", "label": "AM_RECOVERY"}
                ],
            },
        ]
    }

    def runner(args, *, timeout):
        if args[0] == "lsblk":
            return CommandResult(tuple(args), 0, json.dumps(payload), "")
        if args[0] == "findmnt":
            return CommandResult(tuple(args), 0, "/dev/nvme0n1p4[/@]\n", "")
        raise AssertionError(args)

    devices = collect_recovery_usb_devices(
        runner=runner,
        identity_builder=lambda _raw: "k1:" + "a" * 64,
    )
    assert len(devices) == 1
    assert devices[0].path == "/dev/sdb"
    assert devices[0].recovery_media
    assert "Arch Manager Recovery" in devices[0].display_name


def test_privileged_usb_writer_has_strict_whole_disk_boundary_and_hash_verification():
    helper = recovery_helper_source(ROOT)
    assert "validate_usb_disk()" in helper
    assert '[[ "$type" == disk && "$ro_flag" == 0 ]]' in helper
    assert '[[ "$rm_flag" == 1 || "$transport" == usb ]]' in helper
    assert '"$device" != "$root_disk"' in helper
    assert '/usr/bin/dd if="$ISO" of="$device" bs=4M oflag=direct conv=fsync status=none &' in helper
    assert '/usr/bin/head -c "$iso_size" -- "$device"' in helper
    assert "usb_verify_failed" in helper


def test_usb_reboot_uses_verified_bootnext_without_changing_bootorder_or_usb_bytes():
    helper = recovery_helper_source(ROOT)
    reboot_part = helper[helper.index("reboot_recovery_usb() {") : helper.index("\nvalidate_entry() {")]
    assert "create_usb_boot_entry()" in helper
    assert "set_bootnext_verified()" in helper
    assert "matching_usb_boot_entries()" in helper
    assert "generic_usb_boot_entry()" in helper
    assert "USB_BOOT_LOG=/var/log/arch-manager/recovery-usb-boot.log" in helper
    assert "log_efibootmgr -C -d" in helper
    assert "log_efibootmgr -n" in helper
    assert "bootnext_value" in helper
    assert "-C -w" not in helper
    assert "\\EFI\\BOOT\\BOOTX64.EFI" in helper
    assert '"$normalized" == 0xef' in helper
    assert '"$normalized" == ef' in helper
    assert "wait_for_usb_esp()" in helper
    assert " -o " not in reboot_part
    assert "usb_matches_iso" in reboot_part
    assert "cleanup_usb_boot_entries_except" in reboot_part


def test_usb_reboot_prefers_firmware_generic_removable_entry_before_custom_hd_file_entry():
    helper = recovery_helper_source(ROOT)
    reboot_part = helper[helper.index("reboot_recovery_usb() {") : helper.index("\nvalidate_entry() {")]
    generic_pos = reboot_part.index('generic=$(generic_usb_boot_entry "$device"')
    matching_pos = reboot_part.index('bootnum=$(matching_usb_boot_entries "$device"')
    create_pos = reboot_part.index('bootnum=$(create_usb_boot_entry "$device"')
    assert generic_pos < matching_pos < create_pos
    assert 'cleanup_usb_boot_entries_except ""' in reboot_part
    assert "persistent BootOrder" in reboot_part


def test_usb_and_local_recovery_share_the_same_single_utf8_console_path():
    profile = ROOT / "recovery/archiso"
    service = (profile / "airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    vconsole = (profile / "airootfs/etc/vconsole.conf").read_text(encoding="utf-8")
    setup = profile / "airootfs/usr/local/libexec/arch-manager/setup-recovery-console"
    assert "KEYMAP=us" in vconsole
    assert "FONT=" not in vconsole
    assert not setup.exists()
    assert "systemd-vconsole-setup.service" not in service
    assert "setup-recovery-console" not in service
    assert "Environment=LANG=C.UTF-8" in service
    assert "Environment=LC_ALL=C.UTF-8" in service
    engine = recovery_engine_source(ROOT)
    assert 'LatArCyrHeb-16.psfu.gz' in engine
    assert 'setfont -C "$tty" "$font_file"' in engine
    assert 'Could not load the Recovery console font.' not in engine
    assert "[non-ASCII description]" not in engine

def test_usb_boot_failure_message_points_to_diagnostic_log():
    executor = (ROOT / "src/core/recovery_executor.py").read_text(encoding="utf-8")
    assert "/var/log/arch-manager/recovery-usb-boot.log" in executor


def test_usb_boot_uses_same_recovery_engine_with_explicit_usb_marker():
    uefi = (ROOT / "recovery/archiso/efiboot/loader/entries/01-arch-manager-recovery.conf").read_text(encoding="utf-8")
    bios = (ROOT / "recovery/archiso/syslinux/syslinux-linux.cfg").read_text(encoding="utf-8")
    engine = recovery_engine_source(ROOT)
    assert "arch_manager_usb=1" in uefi
    assert "arch_manager_usb=1" in bios
    assert "USB_BOOT_MODE=0" in engine
    assert "arch_manager_usb=1)" in engine
    assert "Recovery started from the USB drive" in engine


def test_recovery_gui_has_usb_create_and_usb_reboot_controls():
    page = recovery_page_source(ROOT)
    executor = (ROOT / "src/core/recovery_executor.py").read_text(encoding="utf-8")
    assert 'QPushButton("Создать / обновить загрузочную флешку")' in page
    assert 'QPushButton("Перезагрузиться с флешки")' in page
    assert 'self._run_usb_action("usb-create", device.path)' in page
    assert 'self._run_usb_action("usb-reboot", device.path)' in page
    assert 'action not in {"usb-create", "usb-reboot"}' in executor


def test_recovery_profile_contains_current_archiso_boot_dependencies():
    packages = set(
        line.strip()
        for line in (ROOT / "recovery/archiso/packages.x86_64").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    # mkinitcpio-archiso provides the archiso/archiso_loop_mnt initramfs hooks.
    # syslinux is needed because this profile explicitly builds bios.syslinux.
    assert "mkinitcpio" in packages
    assert "mkinitcpio-archiso" in packages
    assert "syslinux" in packages
    assert "tzdata" in packages


def test_recovery_gui_uses_one_mode_selector_and_one_visible_stack():
    page = recovery_page_source(ROOT)
    center = (ROOT / "src/gui/recovery_center_page.py").read_text(encoding="utf-8")
    assert 'self.mode_tabs.addTab("Локальное восстановление")' in page
    assert 'self.mode_tabs.addTab("Аварийная USB-флешка")' in page
    assert "QTabBar" in page and "QStackedWidget" in page
    assert "self.mode_tabs.currentChanged.connect" in page
    assert "self.recovery_stack.setCurrentWidget(self.local_mode_page)" in page
    assert "self.recovery_stack.setCurrentWidget(self.usb_mode_page)" in page
    assert 'QLabel("Способ восстановления")' in center


def test_recovery_helper_validates_archiso_profile_before_build_and_keeps_log():
    helper = recovery_helper_source(ROOT)
    executor = (ROOT / "src/core/recovery_executor.py").read_text(encoding="utf-8")
    assert "validate_profile()" in helper
    assert "profile_has_package mkinitcpio-archiso" in helper
    assert "profile_has_package syslinux" in helper
    assert "BUILD_LOG=/var/log/arch-manager/recovery-build.log" in helper
    assert '>"$BUILD_LOG" 2>&1' in helper
    assert '"recovery_profile_invalid"' in executor
    assert '"iso_build_failed"' in executor



def test_recovery_console_uses_one_visible_tty1_without_vt_switching():
    service = (ROOT / "recovery/archiso/airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    engine = recovery_engine_source(ROOT)
    assert "TTYPath=/dev/tty1" in service
    assert "StandardInput=tty-force" in service
    assert "Type=simple" in service
    assert "setup-recovery-console" not in service
    assert "systemd-vconsole-setup.service" not in service
    assert "setup_unicode_console" not in engine
    assert "kbd_mode" not in engine
    assert 'LatArCyrHeb-16.psfu.gz' in engine
    assert 'setfont -C "$tty" "$font_file"' in engine
    assert 'Could not load the Recovery console font.' not in engine
    assert "udevadm settle --timeout=15" in engine
    assert "chvt" not in engine
    assert "RECOVERY_VT" not in engine
    main = engine[engine.index("main() {") :]
    discovery = main.index("find_system_device")
    console_pos = main.index("prepare_recovery_console")
    clear_pos = main.index("clear_recovery_console", console_pos)
    banner_pos = main.index("print_recovery_header", clear_pos)
    list_pos = main.index("list_restore_points", banner_pos)
    ready_pos = main.index("boot_ready", list_pos)
    assert discovery < console_pos < clear_pos < banner_pos < list_pos < ready_pos
    assert main.count("clear_recovery_console") == 1

def test_recovery_report_persists_boot_provenance_for_future_audits():
    engine = recovery_engine_source(ROOT)
    assert "recovery_boot_mode()" in engine
    assert "Boot mode: %s" in engine
    assert "boot_mode=%s" in engine
    assert "arch_manager_usb=1)" in engine



def test_usb_reboot_keeps_indeterminate_progress_because_it_has_no_byte_total():
    page = recovery_page_source(ROOT)
    assert 'self.usb_progress.setRange(0, 0)' in page
    assert 'self._usb_progress_timer.stop()' in page

def test_usb_creation_progress_bar_shows_real_percentages():
    page = recovery_page_source(ROOT)
    helper = recovery_helper_source(ROOT)

    assert 'self.usb_progress.setRange(0, 1000)' in page
    assert 'self._usb_progress_timer.setInterval(100)' in page
    assert 'self.usb_progress.setFormat(f"{tenths / 10:.1f}%")' in page
    assert 'USB_PROGRESS_FILE=/run/arch-manager/recovery-usb-progress' in helper
    assert 'set_usb_progress_tenths()' in helper


def test_usb_true_progress_tracks_direct_device_io_not_buffered_wchar():
    helper = recovery_helper_source(ROOT)
    assert 'oflag=direct conv=fsync' in helper
    assert '$1 == "write_bytes:"' in helper
    assert '$1 == "wchar:"' not in helper
    assert '/usr/bin/sleep 0.25' in helper
    assert 'tenths=$((200 + (bytes * 590 / iso_size)))' in helper
    assert 'set_usb_progress 80 "80.0% — Запись завершена. Синхронизирую устройство…"' in helper


def test_usb_sha256_readback_has_real_progress_too():
    helper = recovery_helper_source(ROOT)
    assert 'iflag=direct,count_bytes' in helper
    assert '$1 == "read_bytes:"' in helper
    assert 'tenths=$((850 + (bytes * 125 / iso_size)))' in helper
    assert 'set_usb_progress 85 "85.0% — Проверяю записанную флешку по SHA-256…"' in helper
    assert 'set_usb_progress 100 "100.0% — Recovery-флешка записана и проверена."' in helper
