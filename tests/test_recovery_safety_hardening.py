from pathlib import Path

from tests.source_bundles import recovery_helper_source, recovery_engine_source


ROOT = Path('.')


def test_restore_point_creation_requires_real_readonly_btrfs_snapshot():
    source = (ROOT / 'src/privileged/manage_restore_points.sh').read_text(encoding='utf-8')
    assert 'verify_created_snapshot()' in source
    assert '"$BTRFS" subvolume show "$snapshot"' in source
    assert '"$BTRFS" property get -ts "$snapshot" ro' in source
    assert '[[ "$readonly" == \'ro=true\' ]]' in source
    assert '"$SNAPPER" -c root delete "$id"' in source


def test_recovery_engine_has_emergency_transaction_rollback_and_readonly_guard():
    engine = recovery_engine_source(ROOT)
    assert 'trap \'emergency_restore_abort $? "unexpected error"\' ERR' in engine
    assert 'trap \'emergency_restore_abort 130 "operation interrupted by the user"\' INT' in engine
    assert 'if (( TRANSACTION_STARTED )); then' in engine
    assert 'if rollback_transaction; then' in engine
    assert 'btrfs property get -ts "$snap" ro' in engine
    assert 'not a read-only Btrfs snapshot' in engine


def test_recovery_engine_refuses_live_root_and_verifies_boot_mount_identity():
    engine = recovery_engine_source(ROOT)
    assert '[[ ",$opts," == *,subvol=/@,* ]]' in engine
    assert '[[ "$boot_real" != "$target_real" ]]' in engine
    assert 'actual_fstype=$(blkid -s TYPE -o value -- "$BOOT_DEVICE"' in engine
    assert 'mounted_device_matches()' in engine
    assert 'mounted_device_matches "$CHROOT_MNT$BOOT_POINT" "$BOOT_DEVICE"' in engine


def test_recovery_backup_name_cannot_collide_with_existing_broken_root():
    engine = recovery_engine_source(ROOT)
    assert 'while [[ -e "$ROOT_MNT/$BACKUP_NAME" ]]; do' in engine
    assert 'sleep 1' in engine


def test_usb_selection_has_identity_token_on_both_gui_and_root_sides():
    source = (ROOT / 'src/core/recovery_usb.py').read_text(encoding='utf-8')
    executor = (ROOT / 'src/core/recovery_executor.py').read_text(encoding='utf-8')
    helper = recovery_helper_source(ROOT)
    assert 'identity: str' in source
    assert 'SIZE,MODEL,TRAN,RM,RO' in source
    assert 'hashlib.sha256' in source
    assert '_USB_IDENTITY_RE' in executor
    assert 'action, device, identity' in executor
    assert 'usb_identity()' in helper
    assert 'fail usb_device_changed' in helper


def test_usb_writer_rejects_system_mounts_and_swap_and_cleans_children():
    helper = recovery_helper_source(ROOT)
    assert 'fail usb_device_system_mount' in helper
    assert 'fail usb_device_swap' in helper
    assert 'cleanup_usb_create()' in helper
    assert "trap 'abort_usb_create 130' INT" in helper
    assert 'USB_DD_PID=$dd_pid' in helper
    assert '/usr/bin/mktemp -u ' not in helper
    assert '/usr/bin/mktemp -d /run/arch-manager/recovery-verify.XXXXXX' in helper


def test_recovery_iso_install_is_atomic_and_profile_symlinks_are_fingerprinted():
    helper = recovery_helper_source(ROOT)
    reader = (ROOT / "src/privileged/read_recovery_state.sh").read_text(encoding="utf-8")
    assert "-type l -printf 'L %p -> %l\\n'" in helper
    assert "-type l -printf 'L %p -> %l\\n'" in reader
    assert 'iso_stage=$(/usr/bin/mktemp "/data/Arch-Recovery/.Arch-Manager-Recovery.iso.XXXXXX")' in helper
    assert '/usr/bin/mv -f -- "$iso_stage" "$ISO"' in helper


def test_recovery_visible_text_has_no_common_russian_transliteration():
    # Technical English names such as Arch Linux, Btrfs, USB and UUID are fine.
    # What must not return is Russian UI text typed with Latin letters.
    engine = recovery_engine_source(ROOT).lower()
    forbidden = (
        'avariino', 'avarijno', 'vosstanov', 'fleshka', 'fleshki', "lokal'n",
        'lokaln', 'zagruzk', 'tochk', 'sistem', 'proverk', 'peregruz', 'vyber',
        'vvedite', 'nazhmi', 'oshibk', 'gotov', 'otmen', 'razdel', 'sohrani',
        'vostanov',
    )
    assert not [token for token in forbidden if token in engine]


def test_privileged_recovery_actions_are_serialized_with_root_lock():
    helper = recovery_helper_source(ROOT)
    assert 'acquire_recovery_lock()' in helper
    assert '/usr/bin/flock -n 9 || fail recovery_busy' in helper
    assert 'prepare|reboot|cancel|delete-broken|usb-create|usb-reboot)' in helper


def test_usb_validation_does_not_hide_fail_output_inside_command_substitution():
    helper = (ROOT / 'src/privileged/recovery_lib/usb.sh').read_text(encoding='utf-8')
    assert "VALIDATED_USB_DEVICE=''" in helper
    assert 'device=$(validate_usb_disk' not in helper
    assert 'validate_usb_disk "$1" "$requested_identity"' in helper
    assert 'device=$VALIDATED_USB_DEVICE' in helper


def test_usb_identity_uses_fields_stable_across_user_and_root_visibility():
    core = (ROOT / 'src/core/recovery_usb.py').read_text(encoding='utf-8')
    helper = (ROOT / 'src/privileged/recovery_lib/usb.sh').read_text(encoding='utf-8')
    identity_core = core[core.index('def _usb_identity'):core.index('def _tree_has_recovery_label')]
    identity_helper = helper[helper.index('usb_identity()'):helper.index('legacy_usb_identity_1812()')]
    code_only = '\n'.join(line for line in identity_core.splitlines() if not line.lstrip().startswith('#'))
    assert 'raw.get("serial")' not in code_only.lower()
    assert 'raw.get("wwn")' not in code_only.lower()
    assert 'SERIAL' not in identity_helper
    assert 'WWN' not in identity_helper


def test_usb_identity_is_bound_to_kernel_block_object_not_udev_presentation_fields():
    core = (ROOT / 'src/core/recovery_usb.py').read_text(encoding='utf-8')
    helper = (ROOT / 'src/privileged/recovery_lib/usb.sh').read_text(encoding='utf-8')
    identity_core = core[core.index('def _usb_identity'):core.index('def _tree_has_recovery_label')]
    identity_helper = helper[helper.index('usb_identity()'):helper.index('legacy_usb_identity_1812()')]
    assert 'raw.get("maj:min")' in identity_core
    assert 'Path("/sys/dev/block") / maj_min' in identity_core
    assert '(sys_entry / "size")' in identity_core
    assert '(sys_entry / "removable")' in identity_core
    assert '(sys_entry / "ro")' in identity_core
    assert 'MAJ:MIN' in identity_helper
    assert '"/sys/dev/block/$majmin"' in identity_helper
    assert '"$sys_path/size"' in identity_helper
    assert '"$sys_path/removable"' in identity_helper
    assert '"$sys_path/ro"' in identity_helper
    assert 'MODEL' not in identity_helper
    assert 'TRAN' not in identity_helper


def test_usb_identity_protocol_is_versioned_and_handles_running_old_gui_cleanly():
    core = (ROOT / 'src/core/recovery_usb.py').read_text(encoding='utf-8')
    executor = (ROOT / 'src/core/recovery_executor.py').read_text(encoding='utf-8')
    helper = (ROOT / 'src/privileged/recovery_lib/usb.sh').read_text(encoding='utf-8')
    assert 'return f"k1:{digest}"' in core
    assert r'^k1:[0-9a-f]{64}$' in executor
    assert 'legacy_usb_identity_1812()' in helper
    assert 'legacy_usb_identity_1811()' in helper
    assert 'fail usb_identity_restart_required' in helper
    assert '"usb_identity_restart_required"' in executor


def test_usb_kernel_identity_is_rechecked_immediately_before_destructive_write():
    helper = (ROOT / 'src/privileged/recovery_lib/usb.sh').read_text(encoding='utf-8')
    create = helper[helper.index('create_recovery_usb()'):helper.index('reboot_recovery_usb()')]
    unmount_at = create.index('unmount_usb_disk "$device"')
    verify_at = create.index('verify_usb_identity "$device" "$requested_identity"', unmount_at)
    dd_at = create.index('/usr/bin/dd if="$ISO" of="$device"')
    assert unmount_at < verify_at < dd_at


def test_executor_can_recover_known_error_from_noisy_helper_stdout():
    from src.core.recovery_executor import _helper_error_code
    assert _helper_error_code('notice\nERROR\tusb_device_changed\n') == 'usb_device_changed'
    assert _helper_error_code('') is None
