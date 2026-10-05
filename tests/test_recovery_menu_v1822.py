from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "recovery/engine/arch-recovery.sh"
SERVICE = ROOT / "recovery/archiso/airootfs/etc/systemd/system/arch-manager-recovery.service"


def _xml_value(info: Path) -> str:
    command = (
        "export LANG=C LC_ALL=C; "
        "source <(sed '$d' recovery/engine/arch-recovery.sh); "
        f"xml_value {shlex.quote(str(info))} description"
    )
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8")


def test_v1822_numeric_xml_entities_decode_to_utf8_even_in_c_locale(tmp_path):
    info = tmp_path / "info.xml"
    info.write_text(
        "<description>Arch Manager: &#x43F;&#x435;&#x440;&#x435;&#x434; обновлением</description>\n",
        encoding="utf-8",
    )
    assert _xml_value(info) == "Arch Manager: перед обновлением"


def test_v1822_legacy_double_escaped_numeric_entities_are_repaired(tmp_path):
    info = tmp_path / "info.xml"
    info.write_text(
        "<description>Arch Manager: &amp;#x43F;&amp;#x435;&amp;#x440;&amp;#x435;&amp;#x434;</description>\n",
        encoding="utf-8",
    )
    assert _xml_value(info) == "Arch Manager: перед"


def test_v190_recovery_owns_one_visible_console_and_one_clear():
    engine = ENGINE.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    assert 'APP_VERSION="1.9.0"' in engine
    assert 'RECOVERY_TTY="/dev/tty1"' in engine
    assert engine.count("clear_recovery_console") == 2  # definition + one call
    assert "RECOVERY_UI_LOCK" in engine
    assert 'flock -n 9' in engine
    assert "TTYPath=/dev/tty1" in service
    assert "StandardInput=tty-force" in service
    assert "Type=simple" in service
    assert "TTYReset=yes" in service
    assert "TTYVHangup=no" in service
    assert "TTYVTDisallocate=no" in service
    assert "chvt" not in engine
    assert "RECOVERY_VT" not in engine
