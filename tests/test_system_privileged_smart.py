from pathlib import Path

from src.core.system_privileged_diagnostics import _build_result


ROOT = Path(__file__).resolve().parents[1]


def test_privileged_smart_nvme_ok_is_not_unknown():
    result = _build_result(
        {
            "version": 1,
            "devices": [
                {
                    "path": "/dev/nvme0n1",
                    "model": "Test NVMe",
                    "protocol": "NVMe",
                    "smart_passed": True,
                    "critical_warning": 0,
                    "media_errors": 0,
                    "percentage_used": 3,
                    "temperature_c": 41,
                    "power_on_hours": 1234,
                    "ata_attributes": {},
                }
            ],
        }
    )
    assert result.check.status == "ok"
    assert "Проверено накопителей: 1" in result.check.summary
    assert "Критическое предупреждение NVMe: 0" in "\n".join(result.check.details)
    assert "Test NVMe" in result.report_text


def test_privileged_smart_nvme_critical_warning_is_critical():
    result = _build_result(
        {
            "version": 1,
            "devices": [
                {
                    "path": "/dev/nvme0n1",
                    "smart_passed": True,
                    "critical_warning": 1,
                    "media_errors": 0,
                    "ata_attributes": {},
                }
            ],
        }
    )
    assert result.check.status == "critical"


def test_privileged_helper_excludes_zram_and_accepts_nvme():
    source = (ROOT / "src" / "privileged" / "read_system_diagnostics.py").read_text(encoding="utf-8")
    assert "zram must never be accepted" in source
    assert "nvme\\d+n\\d+" in source
    assert 'SMARTCTL = "/usr/bin/smartctl"' in source


def test_system_page_uses_privileged_smart_only_for_extended_paths():
    source = (ROOT / "src" / "gui" / "system_page.py").read_text(encoding="utf-8")
    assert "collect_privileged_smart() if self.extended else None" in source
    assert "extended_smart_check=smart.check" in source
    assert "privileged_smart_report=smart.report_text" in source


def test_polkit_policy_and_installer_exist():
    policy = ROOT / "packaging" / "polkit" / "org.archmanager.read-system-diagnostics.policy"
    installer = ROOT / "scripts" / "install-system-diagnostics-helper.sh"
    helper = ROOT / "src" / "privileged" / "read_system_diagnostics.py"
    assert policy.is_file() and installer.is_file() and helper.is_file()
    policy_text = policy.read_text(encoding="utf-8")
    assert "org.archmanager.read-system-diagnostics" in policy_text
    assert "/usr/local/libexec/arch-manager/read-system-diagnostics" in policy_text
