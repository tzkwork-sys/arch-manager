from src.core.command import CommandResult
import src.core.system_diagnostics as diagnostics


def _result(args, *, rc=0, out="", err=""):
    return CommandResult(tuple(args), rc, out, err, available=True)


def test_bootctl_running_product_is_used_even_if_esp_access_is_denied(monkeypatch):
    monkeypatch.setattr(diagnostics, "command_exists", lambda name: True)

    status = """System:\n  Secure Boot: disabled (setup)\nCurrent Boot Loader:\n        Product: systemd-boot 262-1-arch\n  Current Entry: arch-linux.efi\n\nCurrent Stub:\n        Product: systemd-stub 262-1-arch\n"""

    def fake_run(args, **kwargs):
        if args[:2] == ["bootctl", "status"]:
            return _result(args, rc=1, out=status, err='Failed to read "/boot/EFI/systemd": Permission denied')
        if args[:2] == ["bootctl", "is-installed"]:
            raise AssertionError("is-installed fallback must not be needed when current loader product is known")
        return _result(args)

    monkeypatch.setattr(diagnostics, "run_command", fake_run)
    checks = diagnostics.check_boot_environment()
    boot = next(check for check in checks if check.id == "bootloader")
    secure = next(check for check in checks if check.id == "secure_boot")
    assert boot.status == "ok"
    assert "systemd-boot 262-1-arch" in boot.summary
    assert "Permission denied" not in "\n".join(boot.details)
    assert secure.status == "info"


def test_known_nvpcr_regression_requires_systemd_262_and_signature(monkeypatch):
    names = (
        "systemd-pcrlogin@1000.service",
        "systemd-pcrproduct.service",
        "systemd-tpm2-setup-early.service",
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["systemctl", "--version"]:
            return _result(args, out="systemd 262 (262-1-arch)\n")
        if args[0] == "journalctl":
            return _result(args, out="systemd-tpm2-setup: Failed to initialize NvPCR index: No such file or directory\n")
        raise AssertionError(args)

    monkeypatch.setattr(diagnostics, "run_command", fake_run)
    assert diagnostics.detect_known_nvpcr_regression(names) is True


def test_known_nvpcr_degraded_is_information_not_warning():
    checks = diagnostics.build_service_checks(
        systemd_state="degraded",
        critical_system=0,
        advisory_system=4,
        failed_user=0,
        critical_names=(),
        advisory_names=(
            "systemd-pcrlogin@1000.service",
            "systemd-pcrlogin@965.service",
            "systemd-pcrproduct.service",
            "systemd-tpm2-setup-early.service",
        ),
        user_names=(),
        known_nvpcr_regression=True,
    )
    state = next(check for check in checks if check.id == "systemd_state")
    advisory = next(check for check in checks if check.id == "service_advisories")
    assert state.status == "info"
    assert advisory.status == "info"
    assert "systemd 262" in advisory.summary


def test_boot_journal_groups_repeated_known_messages(monkeypatch):
    text = "\n".join(
        [
            "kernel: virt/tdx: TDX not supported by the host platform",
            r"kernel: ACPI BIOS Error (bug): Could not resolve symbol [\\S7DE], AE_NOT_FOUND",
            r"kernel: ACPI Error: Aborting method \\_SB.PC00.LPCB.H_EC.SEN7._STA due to previous error",
            "systemd-tpm2-setup[405]: Failed to initialize NvPCR index: No such file or directory",
            "systemd-pcrextend[448]: Could not extend NvPCR: No such file or directory",
            "systemd[1]: Failed to start TPM NvPCR Product ID Measurement.",
            "kernel: i801_smbus 0000:00:1f.4: SMBus is busy, can't use it!",
            "wpa_supplicant[714]: wlp0s20f3: nl80211: kernel reports: multicast RX registrations are not supported",
        ]
    )
    monkeypatch.setattr(diagnostics, "run_command", lambda args, **kwargs: _result(args, out=text))
    journal, oom = diagnostics.check_boot_journal(known_nvpcr_regression=True)
    assert journal.status == "warning"  # ACPI is the one warning category.
    assert "Критических ошибок нет" in journal.summary
    assert "1 предупреждение BIOS/ACPI" in journal.summary
    assert "информационных групп: 4" in journal.summary
    details = "\n".join(journal.details)
    assert "ACPI / BIOS" in details
    assert "TPM/NvPCR" in details
    assert "Intel TDX" in details
    assert "SMBus" in details
    assert "Wi-Fi / nl80211" in details
    assert oom.status == "ok"


def test_kernel_taint_512_is_explained_as_warning_trace(monkeypatch):
    monkeypatch.setattr(diagnostics, "_read_text", lambda path: "512")
    check = diagnostics.check_kernel_taint()
    assert check.status == "info"
    assert check.summary == "Предупреждение ядра (W)"
    assert any("bit 9 (512)" in line for line in check.details)


def test_libinput_client_bug_is_not_a_kernel_bug(monkeypatch):
    text = "kwin_wayland[1027]: Libinput: event3 - Logitech: client bug: event processing lagging behind by 38ms"
    monkeypatch.setattr(diagnostics, "run_command", lambda args, **kwargs: _result(args, out=text))
    journal, _ = diagnostics.check_boot_journal()
    assert journal.status == "warning"
    assert "Критических ошибок нет" in journal.summary
    assert any("client bug:" in detail for detail in journal.details)


def test_real_kernel_bug_stays_critical(monkeypatch):
    for text in ("host kernel: BUG: unable to handle page fault", "host kernel: Oops: 0000", "kernel: I/O error, dev nvme0n1"):
        monkeypatch.setattr(diagnostics, "run_command", lambda args, **kwargs: _result(args, out=text))
        assert diagnostics.check_boot_journal()[0].status == "critical"


def test_failed_desktop_app_does_not_change_system_manager_nvpcr_assessment():
    checks = diagnostics.build_service_checks(
        systemd_state="degraded", critical_system=0, advisory_system=4,
        failed_user=1, critical_names=(), advisory_names=("systemd-pcrproduct.service",),
        user_names=("app-arch-manager.service",), known_nvpcr_regression=True,
    )
    by_id = {check.id: check for check in checks}
    assert by_id["systemd_state"].status == "info"
    assert by_id["failed_user_services"].status == "critical"


def test_unknown_or_real_system_failures_are_not_excused_as_nvpcr():
    for critical_count in (None, 1):
        checks = diagnostics.build_service_checks(
            systemd_state="degraded", critical_system=critical_count, advisory_system=4,
            failed_user=0, critical_names=("important.service",), advisory_names=(),
            user_names=(), known_nvpcr_regression=True,
        )
        assert checks[0].status == "critical"


def test_secure_boot_and_tpm_labels_are_human_readable(monkeypatch):
    monkeypatch.setattr(diagnostics, "command_exists", lambda name: True)

    status = """System:
  Secure Boot: disabled (setup)
Current Boot Loader:
        Product: systemd-boot 262-1-arch

Current Stub:
        Product: systemd-stub 262-1-arch
"""

    def fake_run(args, **kwargs):
        if args[:2] == ["bootctl", "status"]:
            return _result(args, rc=1, out=status, err="Permission denied")
        if args[:2] == ["systemd-analyze", "has-tpm2"]:
            return _result(args, out="yes\n+firmware\n+driver\n")
        return _result(args)

    monkeypatch.setattr(diagnostics, "run_command", fake_run)
    secure = next(check for check in diagnostics.check_boot_environment() if check.id == "secure_boot")
    assert secure.summary == "Выключен (режим настройки UEFI)"
    tpm = diagnostics.check_tpm2()
    assert tpm.summary == "Доступен"
