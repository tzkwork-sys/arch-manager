from datetime import datetime, timezone
from pathlib import Path
from tests.source_bundles import recovery_engine_source, recovery_helper_source, recovery_page_source
import re
import subprocess

import pytest

from src.core.command import CommandResult
from src.core.recovery import (
    RecoveryPaths,
    RecoveryRequest,
    RecoveryValidationError,
    _parse_recovery_fingerprint,
    _parse_recovery_state,
    build_recovery_plan,
    collect_recovery_fingerprint,
    collect_recovery_readiness,
)
from src.core.restore_points import RestorePoint, RestorePointsListResult


def _point(number: int = 7) -> RestorePoint:
    return RestorePoint(
        number,
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        "Перед обновлением",
        True,
        None,
        "root",
        "number",
        "important=yes",
    )


def _points(points=(_point(),), readable=True):
    return RestorePointsListResult(
        tuple(points),
        True,
        readable,
        datetime.now(timezone.utc),
        "нет доступа" if not readable else None,
    )


def _runner(values):
    def run(args, *, timeout):
        key = (args[3], args[-1]) if args and args[0] == "findmnt" else (args[0], "")
        if key in values:
            return CommandResult(tuple(args), 0, values[key], "")
        return CommandResult(tuple(args), 1, "", "unavailable")

    return run


def _ready_paths(tmp_path: Path) -> RecoveryPaths:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    state = snapshots / ".arch-manager-recovery"
    state.mkdir()
    iso = tmp_path / "Arch-Manager-Recovery.iso"
    iso.write_text("iso")
    local = tuple(tmp_path / name for name in ("vmlinuz", "initramfs", "entry"))
    local[0].write_text("x")
    local[1].write_text("x")
    local[2].write_text(
        "title Arch Manager Recovery\n"
        "options arch_manager_local=1 arch_manager_root_uuid=test\n"
    )
    return RecoveryPaths(snapshots, state, iso, local, tmp_path / "top")


def _ready_values(paths: RecoveryPaths):
    return {
        ("FSTYPE", "/"): "btrfs\n",
        ("SOURCE", "/"): "/dev/test[/@]\n",
        ("OPTIONS", "/"): "rw,subvol=/@\n",
        ("FSTYPE", str(paths.snapshots)): "btrfs\n",
        ("SOURCE", str(paths.snapshots)): "/dev/test[/@snapshots]\n",
        ("OPTIONS", str(paths.snapshots)): "rw,subvol=/@snapshots\n",
        ("bootctl", ""): "Product: systemd-boot\n",
    }


def test_ready_state_is_shared_and_plan_is_not_preparation_binding(tmp_path):
    paths = _ready_paths(tmp_path)
    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=_points,
    )
    assert result.ready
    assert result.environment_prepared
    assert result.can_boot_recovery
    assert not hasattr(result, "prepared_snapshot_id")

    plan = build_recovery_plan(RecoveryRequest(7), result)
    assert plan.restore_point.number == 7
    assert plan.executor_action == "recovery-mode"
    assert any("после перезагрузки" in step for step in plan.procedure)


def test_new_snapshot_does_not_invalidate_shared_environment(tmp_path):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)

    first = collect_recovery_readiness(
        paths=paths,
        runner=_runner(values),
        points_loader=lambda: _points((_point(7),)),
    )
    second = collect_recovery_readiness(
        paths=paths,
        runner=_runner(values),
        points_loader=lambda: _points((_point(7), _point(8), _point(14))),
    )

    assert first.environment_prepared
    assert second.environment_prepared
    assert first.can_boot_recovery
    assert second.can_boot_recovery


def test_non_btrfs_is_blocking(tmp_path):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)
    values[("FSTYPE", "/")] = "ext4\n"
    result = collect_recovery_readiness(paths=paths, runner=_runner(values), points_loader=_points)
    assert result.root_is_btrfs is False
    assert not result.can_prepare
    assert any(issue.code == "root-not-btrfs" for issue in result.issues)


def test_bad_root_layout_is_blocking(tmp_path):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)
    values[("OPTIONS", "/")] = "rw,subvol=/root\n"
    values[("SOURCE", "/")] = "/dev/test[/root]\n"
    result = collect_recovery_readiness(paths=paths, runner=_runner(values), points_loader=_points)
    assert result.root_layout_ok is False
    assert not result.can_prepare
    assert any(issue.code == "root-layout" for issue in result.issues)


@pytest.mark.parametrize("subvolume", ["/@home", "/@snapshots", "/@old", "/@something"])
def test_root_subvolume_is_exact_not_substring(tmp_path, subvolume):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)
    values[("OPTIONS", "/")] = f"rw,subvol={subvolume}\n"
    values[("SOURCE", "/")] = f"/dev/test[{subvolume}]\n"
    assert (
        collect_recovery_readiness(
            paths=paths,
            runner=_runner(values),
            points_loader=_points,
        ).root_layout_ok
        is False
    )


@pytest.mark.parametrize("subvolume", ["/@snapshots-old", "/@mysnapshots", "/@home"])
def test_snapshots_subvolume_is_exact_not_substring(tmp_path, subvolume):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)
    values[("OPTIONS", str(paths.snapshots))] = f"rw,subvol={subvolume}\n"
    values[("SOURCE", str(paths.snapshots))] = f"/dev/test[{subvolume}]\n"
    assert (
        collect_recovery_readiness(
            paths=paths,
            runner=_runner(values),
            points_loader=_points,
        ).snapshots_layout_ok
        is False
    )


def test_no_restore_points_does_not_block_environment_preparation(tmp_path):
    paths = _ready_paths(tmp_path)
    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=lambda: _points(()),
    )
    assert result.can_prepare
    assert result.environment_prepared
    assert result.can_boot_recovery
    issue = next(issue for issue in result.issues if issue.code == "no-points")
    assert issue.blocking is False


def test_missing_environment_still_allows_automatic_prepare(tmp_path):
    paths = _ready_paths(tmp_path)
    paths.legacy_iso.unlink()
    for path in paths.local_files:
        path.unlink()

    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=_points,
    )
    assert result.can_prepare
    assert not result.environment_prepared
    assert not result.can_boot_recovery
    assert any(issue.code == "recovery-environment" and not issue.blocking for issue in result.issues)


def test_partial_probe_failure_does_not_crash(tmp_path):
    paths = _ready_paths(tmp_path)
    values = _ready_values(paths)
    del values[("OPTIONS", str(paths.snapshots))]
    result = collect_recovery_readiness(paths=paths, runner=_runner(values), points_loader=_points)
    assert result.partial
    assert any(issue.code == "snapshots-layout-unknown" for issue in result.issues)


def test_legacy_marker_and_old_broken_root_are_read_only_detected(tmp_path):
    paths = _ready_paths(tmp_path)
    paths.top_level.mkdir()
    (paths.top_level / "@.broken-20260901-120000").mkdir()
    (paths.legacy_state / "last-restore.env").write_text(
        "version=1.8.4\n"
        "date=2026-09-01\n"
        "snapshot=7\n"
        "backup=@.broken-20260901-120000\n"
    )
    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=_points,
    )
    assert result.previous_restore.marker_found
    assert result.previous_restore.snapshot_number == 7
    assert result.broken_roots == ("@.broken-20260901-120000",)


def test_legacy_text_report_is_detected(tmp_path):
    paths = _ready_paths(tmp_path)
    (paths.legacy_state / "restore-old.txt").write_text("report")
    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=_points,
    )
    assert result.previous_restore.report_found


@pytest.mark.parametrize("value", [0, -1, True, "7"])
def test_optional_preselection_plan_rejects_invalid_restore_point_id(tmp_path, value):
    paths = _ready_paths(tmp_path)
    readiness = collect_recovery_readiness(
        paths=paths,
        runner=_runner(_ready_values(paths)),
        points_loader=_points,
    )
    with pytest.raises(RecoveryValidationError):
        build_recovery_plan(RecoveryRequest(value), readiness)


def test_v2_privileged_state_parser_has_no_snapshot_field():
    parsed = _parse_recovery_state(
        "version=2\n"
        "systemd_boot=1\n"
        "iso_ready=1\n"
        "boot_files_ready=1\n"
        "environment_ready=1\n"
    )
    assert parsed == (True, True, True, True)

    assert _parse_recovery_state(
        "version=1\n"
        "systemd_boot=1\n"
        "iso_ready=1\n"
        "local_files_ready=1\n"
        "prepared_snapshot_id=14\n"
    ) is None




def test_v2_fingerprint_parser_and_lightweight_probe(tmp_path):
    fingerprint = "a" * 64
    assert _parse_recovery_fingerprint(
        f"version=2\nfingerprint={fingerprint}\n"
    ) == fingerprint
    assert _parse_recovery_fingerprint("version=2\nfingerprint=bad\n") is None

    reader = tmp_path / "read-recovery-state"
    reader.write_text("probe", encoding="utf-8")
    paths = RecoveryPaths(state_reader=reader)
    seen = []

    def runner(args, *, timeout):
        seen.append((tuple(args), timeout))
        return CommandResult(tuple(args), 0, f"version=2\nfingerprint={fingerprint}\n", "")

    assert collect_recovery_fingerprint(paths=paths, runner=runner) == fingerprint
    assert seen == [(("sudo", "-n", str(reader), "--fingerprint"), 8)]

def test_privileged_read_probe_recognizes_shared_state_when_boot_is_protected(tmp_path):
    base = _ready_paths(tmp_path)
    reader = tmp_path / "read-recovery-state"
    reader.write_text("probe", encoding="utf-8")
    for path in base.local_files:
        path.unlink()
    base.legacy_iso.unlink()

    paths = RecoveryPaths(
        base.snapshots,
        base.legacy_state,
        base.legacy_iso,
        base.local_files,
        base.top_level,
        base.legacy_helper,
        reader,
    )
    values = _ready_values(paths)
    values[("sudo", "")] = (
        "version=2\n"
        "systemd_boot=1\n"
        "iso_ready=1\n"
        "boot_files_ready=1\n"
        "environment_ready=1\n"
        f"fingerprint={'b' * 64}\n"
    )
    result = collect_recovery_readiness(
        paths=paths,
        runner=_runner(values),
        points_loader=_points,
    )
    assert result.environment_prepared
    assert result.can_boot_recovery
    assert result.iso_ready
    assert result.boot_files_ready
    assert result.state_fingerprint == "b" * 64


def test_stage7_readiness_never_invokes_mutating_privileged_executor():
    code = Path("src/core/recovery.py").read_text(encoding="utf-8")
    assert "pkexec" not in code
    assert "shell=True" not in code
    assert '["sudo", "-n", str(reader)]' in code
    assert "manage-recovery" not in code


def test_recovery_page_is_not_placeholder():
    code = recovery_page_source(Path("."))
    assert "class RecoveryPage(RecoveryUsbMixin, NavigablePage):" in code
    assert "PlaceholderPage" not in code


def test_stage7_helper_and_polkit_are_strictly_scoped():
    helper = Path("src/privileged/manage_recovery.sh")
    policy = Path("packaging/polkit/org.archmanager.manage-recovery.policy")
    installer = Path("scripts/install-stage7-recovery-helper.sh")
    assert helper.is_file() and policy.is_file() and installer.is_file()

    code = recovery_helper_source(Path("."))
    assert 'prepare() {' in code and '[[ $# -eq 0 ]] || fail boot_entry_invalid' in code
    assert 'reboot_recovery() {' in code
    assert 'cancel() {' in code
    assert "eval" not in code and "bash -c" not in code
    assert '/.snapshots/$id/snapshot' not in code
    assert "set-oneshot" in code

    prepare_part = code[code.index("prepare() {"):code.index("\nreboot_recovery() {")]
    assert "set-oneshot" not in prepare_part
    assert "arch_manager_snapshot=" not in next(
        line for line in prepare_part.splitlines() if "options archisobasedir=arch" in line
    )

    policy_text = policy.read_text(encoding="utf-8")
    assert 'id="org.archmanager.manage-recovery"' in policy_text
    assert "auth_admin" in policy_text and "auth_admin_keep" not in policy_text
    assert "/usr/local/libexec/arch-manager/manage-recovery" in policy_text


def test_minimal_recovery_profile_is_project_owned():
    profile = Path("recovery/archiso")
    packages = (profile / "packages.x86_64").read_text(encoding="utf-8")
    assert profile.is_dir() and "plasma" not in packages.lower() and "gnome" not in packages.lower()
    assert "btrfs-progs" in packages and "arch-install-scripts" in packages
    assert (profile / "airootfs/etc/systemd/system/arch-manager-recovery.service").is_file()
    helper = recovery_helper_source(Path("."))
    assert "/usr/share/archiso/configs/releng" not in helper


def test_recovery_profile_has_no_patch_artifacts_and_valid_package_lines():
    markers = ("*** Begin Patch", "*** Add File", "*** Update File", "*** Delete File", "*** End Patch")
    for path in Path("recovery").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(marker in text for marker in markers), path
    packages = (Path("recovery/archiso/packages.x86_64").read_text(encoding="utf-8")).splitlines()
    for package in packages:
        if package and not package.startswith("#"):
            assert re.fullmatch(r"[a-z0-9@._+:-]+", package), package


def test_recovery_profile_has_current_archiso_boot_and_autostart_layout():
    profile = Path("recovery/archiso")
    definition = (profile / "profiledef.sh").read_text(encoding="utf-8")
    assert "bios.syslinux" in definition and "uefi.systemd-boot" in definition
    assert "uefi-x64" not in definition
    assert (profile / "syslinux/syslinux.cfg").is_file()
    assert (profile / "syslinux/syslinux-linux.cfg").is_file()
    assert (profile / "efiboot/loader/loader.conf").is_file()
    assert (profile / "efiboot/loader/entries/01-arch-manager-recovery.conf").is_file()
    hooks = (profile / "airootfs/etc/mkinitcpio.conf.d/archiso.conf").read_text(encoding="utf-8")
    assert "archiso archiso_loop_mnt block" in hooks
    wants = profile / "airootfs/etc/systemd/system/multi-user.target.wants/arch-manager-recovery.service"
    assert wants.is_symlink() and wants.readlink() == Path("../arch-manager-recovery.service")


def test_installer_replaces_fixed_profile_tree_idempotently():
    installer = Path("scripts/install-stage7-recovery-helper.sh").read_text(encoding="utf-8")
    assert 'RECOVERY_STAGE=' in installer and '"$RECOVERY_STAGE/archiso"' in installer
    assert '"$RECOVERY_RESOURCES/archiso/archiso"' not in installer
    assert 'rm -rf -- "$RECOVERY_RESOURCES"' in installer


def test_helper_rejects_iso_on_root_filesystem():
    helper = recovery_helper_source(Path("."))
    assert "check_iso_separate" in helper
    assert "iso_filesystem_invalid" in helper
    assert "inspect_iso()" in helper


def test_helper_cache_rejects_stale_iso_by_profile_engine_and_iso_hash():
    helper = recovery_helper_source(Path("."))
    assert "ISO_METADATA=" in helper and "cache_valid()" in helper
    assert "profile_version" in helper
    assert "profile_sha256" in helper
    assert "iso_sha256" in helper
    assert "profile_hash" in helper
    assert '/usr/bin/sha256sum "$RECOVERY_ENGINE"' in helper
    assert '/usr/bin/sha256sum "$ISO"' in helper
    assert "build_iso_if_stale" in helper


def test_boot_entry_is_bound_to_environment_hashes_not_snapshot():
    helper = recovery_helper_source(Path("."))
    prepare_part = helper[helper.index("prepare() {"):helper.index("\nreboot_recovery() {")]
    entry_line = next(
        line for line in prepare_part.splitlines() if "options archisobasedir=arch" in line
    )
    assert "arch_manager_local=1" in entry_line
    assert "arch_manager_root_uuid=" in entry_line
    assert "arch_manager_profile_sha256=" in entry_line
    assert "arch_manager_iso_sha256=" in entry_line
    assert "arch_manager_kernel_sha256=" in entry_line
    assert "arch_manager_initramfs_sha256=" in entry_line
    assert "arch_manager_snapshot=" not in entry_line


def test_read_recovery_state_helper_is_strictly_read_only_v2():
    helper = Path("src/privileged/read_recovery_state.sh")
    assert helper.is_file()
    code = helper.read_text(encoding="utf-8")
    assert 'bootctl --esp-path="$BOOT" is-installed' in code
    assert "arch_manager_local=1" in code
    assert "snapshot_token" in code
    assert "prepared_snapshot_id" not in code
    assert "version=2" in code
    assert "environment_ready=" in code
    assert "boot_files_ready=" in code
    assert "arch_manager_iso_sha256" in code
    assert '"--fingerprint"' in code
    assert "quick_fingerprint" in code
    assert "path_stamp" in code
    for forbidden in (
        "set-oneshot",
        "systemctl reboot",
        "mkarchiso",
        "/usr/bin/rm ",
        "/usr/bin/mv ",
        "/usr/bin/install ",
    ):
        assert forbidden not in code


def test_recovery_profile_keeps_english_locale_and_disables_late_vconsole_reprogramming():
    profile = Path("recovery/archiso")
    locale_conf = (profile / "airootfs/etc/locale.conf").read_text(encoding="utf-8")
    locale_gen = (profile / "airootfs/etc/locale.gen").read_text(encoding="utf-8")
    vconsole = (profile / "airootfs/etc/vconsole.conf").read_text(encoding="utf-8")
    service = (profile / "airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    customize = (profile / "airootfs/root/customize_airootfs.sh").read_text(encoding="utf-8")
    assert "LANG=C.UTF-8" in locale_conf
    assert "ru_RU" not in locale_conf
    assert "no generated locale is required" in locale_gen
    assert "KEYMAP=us" in vconsole
    assert "FONT=" not in vconsole
    assert "setup-recovery-console" not in service
    assert "systemd-vconsole-setup.service" not in service
    assert "systemd-vconsole-setup.service" in customize
    assert "ExecStart=/usr/local/bin/arch-manager-recovery" in service
    assert "Environment=LANG=C.UTF-8" in service
    assert "Environment=LC_ALL=C.UTF-8" in service
    assert "Environment=TZ=Europe/Moscow" in service
    assert "arch-manager-recovery-watchdog.service" in service
    assert "locale-gen" not in customize
    assert "systemctl enable arch-manager-recovery-watchdog.service" in customize
    assert "systemd-machine-id-setup" in customize
    assert "systemd-firstboot.service" in customize
    assert "firstboot-ask-password-console.service" in customize
    assert "ln -sf /usr/share/zoneinfo/Europe/Moscow /etc/localtime" in customize


def test_recovery_console_has_one_in_engine_finalization_and_no_separate_helper():
    profile = Path("recovery/archiso")
    service = (profile / "airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    definition = (profile / "profiledef.sh").read_text(encoding="utf-8")
    setup = profile / "airootfs/usr/local/libexec/arch-manager/setup-recovery-console"
    assert not setup.exists()
    assert "setup-recovery-console" not in service
    assert "systemd-vconsole-setup.service" not in service
    assert "ExecStart=/usr/local/bin/arch-manager-recovery" in service
    assert "After=multi-user.target" not in service
    assert "Type=simple" in service
    assert "TimeoutStopSec=5s" in service
    assert 'setup-recovery-console' not in definition
    assert '["/usr/local/libexec/arch-manager/recovery-bootlog"]="0:0:755"' in definition
    assert '["/usr/local/libexec/arch-manager/recovery-watchdog"]="0:0:755"' in definition


def test_recovery_engine_keeps_optional_preselection_but_defaults_to_interactive_choice():
    engine = recovery_engine_source(Path("."))
    assert "arch_manager_snapshot=*" in engine
    assert 'if [[ -n "$PRESELECT_SNAPSHOT" ]]; then' in engine
    assert 'Enter a restore point number, or 0 to exit' in engine
    assert 'say "Arch Manager preselected restore point $selected_display."' in engine


def test_recovery_engine_uses_compact_visible_numbers_but_keeps_real_snapshot_ids():
    engine = recovery_engine_source(Path("."))
    assert 'print_restore_point_row "$((index + 1))"' in engine
    assert 'selected_id="${SNAPSHOT_IDS[selected - 1]}"' in engine
    assert 'perform_restore "$selected_id" "$selected_display"' in engine
    assert 'local id display_number snap' in engine
    assert 'id="$1"' in engine
    assert 'say "RESTORE POINT $display_number"' in engine
    assert 'snap="$ROOT_MNT/@snapshots/$id/snapshot"' in engine


def test_zero_at_snapshot_selection_returns_automatically_to_normal_arch():
    engine = recovery_engine_source(Path("."))
    assert "return_to_normal_boot()" in engine
    assert "/usr/bin/systemctl --no-block reboot" in engine
    assert 'say "Exit. The system was not changed."' in engine
    assert engine.count("return_to_normal_boot") >= 2
    assert "cleanup_mounts" in engine
    assert "trap - EXIT" in engine


def test_stage7_installer_adds_nopasswd_read_only_recovery_probe():
    installer = Path("scripts/install-stage7-recovery-helper.sh").read_text(encoding="utf-8")
    assert "read_recovery_state.sh" in installer
    assert "read-recovery-state" in installer
    assert "NOPASSWD:" in installer
    assert "visudo -cf" in installer


def test_recovery_selection_is_nounset_safe_before_real_restore():
    engine = recovery_engine_source(Path("."))
    assert 'APP_VERSION="1.9.0"' in engine
    assert "-printf '%f\\n' 2>/dev/null | sort -n" in engine
    assert 'local id display_number snap' in engine
    assert 'id="$1"' in engine
    assert 'snap="$ROOT_MNT/@snapshots/$id/snapshot"' in engine
    assert 'btrfs subvolume show "$snap"' in engine


def test_recovery_formats_restore_point_dates_in_moscow_time():
    engine = recovery_engine_source(Path("."))
    assert "snapshot_epoch_utc()" in engine
    assert 'TZ=UTC date -d "$raw"' in engine
    assert 'TZ=Europe/Moscow date -d "@$epoch"' in engine
    assert "MSK" in engine
    assert 'date=$(format_moscow_datetime "$date"' in engine
    assert 'sort_epoch=$(snapshot_epoch_utc "$raw_date"' in engine


def test_recovery_snapper_utc_timestamp_is_really_shifted_to_moscow():
    result = subprocess.run(
        [
            "bash",
            "-lc",
            "source <(sed '$d' recovery/engine/arch-recovery.sh); "
            "format_moscow_datetime '2026-09-24 16:38:29'",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "24.09.2026 19:38:29 MSK"


def test_recovery_visible_startup_text_is_ascii_only_and_iso_cache_is_bumped():
    engine = recovery_engine_source(Path("."))
    profile = Path("recovery/archiso/profiledef.sh").read_text(encoding="utf-8")
    service = Path("recovery/archiso/airootfs/etc/systemd/system/arch-manager-recovery.service").read_text(encoding="utf-8")
    assert 'APP_NAME="Arch Manager Recovery"' in engine
    assert 'say "Recovery started from the USB drive."' in engine
    assert 'say "The same recovery engine is used for local and USB Recovery."' in engine
    assert 'iso_version="15"' in profile
    assert "setup_unicode_console" not in engine
    assert "prepare_discovery_tty" not in engine
    assert "kbd_mode" not in engine
    assert "ru_RU" not in engine
    assert 'LatArCyrHeb-16.psfu.gz' in engine
    assert 'setfont -C "$tty" "$font_file"' in engine
    assert 'Could not load the Recovery console font.' not in engine
    assert "systemd-vconsole-setup.service" not in service
    assert "setup-recovery-console" not in service
    assert "[non-ASCII description]" not in engine
    main = engine[engine.index("main() {") :]
    discovery = main.index("find_system_device")
    console_pos = main.index("prepare_recovery_console")
    clear_pos = main.index("clear_recovery_console", console_pos)
    banner_pos = main.index("print_recovery_header", clear_pos)
    assert discovery < console_pos < clear_pos < banner_pos


def test_recovery_preserves_current_plasma_session_preferences_across_root_rollback():
    engine = recovery_engine_source(Path("."))
    assert "preserve_kde_session_preferences()" in engine
    assert "*/.config/ksmserverrc" in engine
    assert 'cp -a --reflink=auto -- "$source" "$target"' in engine
    assert "Current Plasma shutdown/reboot settings were preserved." in engine



def test_recovery_visible_order_matches_gui_creation_date_order():
    engine = recovery_engine_source(Path("."))
    assert 'sort_epoch=$(snapshot_epoch_utc "$raw_date"' in engine
    assert "sort -t'|' -k1,1nr -k2,2n" in engine
    assert 'print_restore_point_row "$((index + 1))"' in engine
    assert 'selected_id="${SNAPSHOT_IDS[selected - 1]}"' in engine


def test_recovery_report_keeps_visible_number_and_technical_snapper_id_separate():
    engine = recovery_engine_source(Path("."))
    assert 'local id="$1" display_number="$2" stamp="$3" log_dir' in engine
    assert "printf 'Restore point: %s\\n' \"$display_number\"" in engine
    assert "printf 'Snapper ID: %s\\n' \"$id\"" in engine
    assert "printf 'display_snapshot=%s\\n' \"$display_number\"" in engine
    assert 'write_restore_log "$id" "$display_number" "$stamp"' in engine



def test_recovery_has_no_progress_bars_or_second_confirmation_after_snapshot_choice():
    engine = recovery_engine_source(Path("."))
    assert "recovery_progress()" not in engine
    assert "recovery_progress " not in engine
    assert "ПРОВЕРКА ПЕРЕД ВОССТАНОВЛЕНИЕМ" not in engine
    assert "1 — начать восстановление" not in engine
    assert "2 — отменить и ничего не менять" not in engine
    assert 'read -r -p "Выберите 1 или 2: "' not in engine
    assert 'say "Selection accepted. No additional confirmation is required."' in engine
    assert 'say "Preparing restore..."' in engine
    assert 'say "Restoring the system root from point $display_number..."' in engine


def test_recovery_only_final_prompt_is_single_key_reboot_or_zero_exit():
    engine = recovery_engine_source(Path("."))
    prompt = 'read -r -s -n 1 -p "Press any key to reboot, or 0 to exit without rebooting: " answer || true'
    assert prompt in engine
    assert 'if [[ "$answer" == "0" ]]; then' in engine
    assert 'say "Exit without rebooting."' in engine
    assert '1 — перезагрузить сейчас, 2 — завершить без перезагрузки' not in engine
    write_pos = engine.index('write_restore_log "$id" "$display_number" "$stamp"')
    prompt_pos = engine.index(prompt)
    reboot_pos = engine.index("    reboot\n", prompt_pos)
    assert write_pos < prompt_pos < reboot_pos
