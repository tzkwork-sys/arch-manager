from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "recovery/engine/arch-recovery.sh"
SERVICE = ROOT / "recovery/archiso/airootfs/etc/systemd/system/arch-manager-recovery.service"
CUSTOMIZE = ROOT / "recovery/archiso/airootfs/root/customize_airootfs.sh"


def _source_and(command: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-lc", f"source <(sed '$d' recovery/engine/arch-recovery.sh); {command}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def test_v1823_repairs_known_legacy_preupdate_transliteration():
    result = _source_and("display_description 'Arch Manager: pered obnovleniem sistemy'")
    assert result.stdout.decode("utf-8") == "Arch Manager: перед обновлением системы"


def test_v1823_description_is_not_truncated_with_ellipsis():
    text = "Очень длинное пользовательское описание точки восстановления без потери текста"
    result = _source_and(f"display_description '{text}'")
    assert result.stdout.decode("utf-8") == text
    engine = ENGINE.read_text(encoding="utf-8")
    assert 'text="${text:0:27}..."' not in engine


def test_v1823_table_uses_compact_columns_and_keeps_complete_description():
    result = _source_and(
        "RECOVERY_TTY=/dev/null; terminal_columns(){ printf 80; }; "
        "print_restore_point_row 1 '29.09.2026 13:33:06 MSK' normal "
        "'Arch Manager: перед обновлением системы'"
    )
    line = result.stdout.decode("utf-8").rstrip("\n")
    assert "Arch Manager: перед обновлением системы" in line
    assert "..." not in line
    assert len(line) <= 80


def test_v190_recovery_menu_is_drawn_once_directly_on_tty1():
    engine = ENGINE.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    customize = CUSTOMIZE.read_text(encoding="utf-8")
    main = engine[engine.index("main() {") :]

    assert 'APP_VERSION="1.9.0"' in engine
    assert 'RECOVERY_TTY="/dev/tty1"' in engine
    assert "RECOVERY_VT" not in engine
    assert "TTYPath=/dev/tty1" in service
    assert "StandardInput=tty-force" in service
    assert "Type=simple" in service
    assert "getty@tty1.service" in customize
    assert "getty@tty2.service" not in customize
    assert "activate_recovery_console" not in engine
    assert "chvt" not in engine

    clear_pos = main.index("clear_recovery_console")
    header_pos = main.index("print_recovery_header", clear_pos)
    list_pos = main.index("list_restore_points", header_pos)
    ready_pos = main.index("boot_ready", list_pos)
    prompt_pos = main.index("Enter a restore point number", ready_pos)
    assert clear_pos < header_pos < list_pos < ready_pos < prompt_pos
    assert main.count("clear_recovery_console") == 1
