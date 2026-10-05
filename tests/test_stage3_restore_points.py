from pathlib import Path

from src.core.restore_points import (
    parse_restore_points_csv,
    restore_point_is_important,
    restore_point_reason,
    safe_restore_point_description,
)

ROOT = Path(__file__).resolve().parents[1]


def test_stage3_required_files_exist():
    assert (ROOT / "docs" / "STAGE_3.md").is_file()
    assert (ROOT / "src" / "gui" / "restore_points_page.py").is_file()


def test_restore_points_csv_parser_reads_stage3_fields():
    csv_text = (
        '0|||||||\n'
        '41|2026-09-21 11:42:03 +0300|root|684 MiB|number|'
        'перед обновлением системы|important=yes|\n'
        '42|2026-09-21 12:00:00 +0300|oleg|16 KiB|timeline|Hourly||\n'
    )
    points = parse_restore_points_csv(csv_text)
    assert len(points) == 2
    assert points[0].number == 41
    assert points[0].important is True
    assert points[0].exclusive_size_bytes == 684 * 1024**2
    assert points[0].reason == "Перед обновлением системы"
    assert points[1].reason == "Создана автоматически по времени"


def test_restore_points_fast_csv_without_used_space_keeps_description_and_userdata_aligned():
    csv_text = (
        '14|2026-09-23 11:11:00 +0300|root|number|'
        'Arch Manager: перед обновлением системы|important=yes|\n'
    )
    points = parse_restore_points_csv(csv_text)
    assert len(points) == 1
    point = points[0]
    assert point.number == 14
    assert point.description == "Arch Manager: перед обновлением системы"
    assert point.userdata == "important=yes"
    assert point.important is True
    assert point.exclusive_size_bytes is None
    assert point.reason == "Перед обновлением системы"


def test_restore_point_importance_parser_is_key_based():
    assert restore_point_is_important("foo=bar,important=yes")
    assert restore_point_is_important("important=true,foo=bar")
    assert not restore_point_is_important("important=no")
    assert not restore_point_is_important("notimportant=yes")


def test_restore_point_description_is_safe_and_user_friendly():
    assert safe_restore_point_description("", 17) == "Точка восстановления №17"
    assert safe_restore_point_description("Normal name", 17) == "Normal name"
    assert safe_restore_point_description("bad\x1b[?25l text", 17) == "Повреждённое описание"


def test_reason_does_not_invent_unknown_origin():
    assert restore_point_reason("Custom point", "number") == "—"
    assert restore_point_reason("Ручная точка", "number") == "Создана вручную"

    assert (
        restore_point_reason("Arch Manager: перед полным обновлением системы", "number")
        == "Перед обновлением системы"
    )


def test_stage3_runtime_remains_read_only():
    core = (ROOT / "src" / "core" / "restore_points.py").read_text(encoding="utf-8")
    gui = (ROOT / "src" / "gui" / "restore_points_page.py").read_text(encoding="utf-8")
    source = core + "\n" + gui
    forbidden = (
        "sudo ",
        "pkexec",
        " snapper create",
        " snapper delete",
        " snapper modify",
        "set-config",
    )
    assert all(token not in source for token in forbidden)


def test_stage3_gui_is_compact_has_multiselect_and_refresh():
    source = (ROOT / "src" / "gui" / "restore_points_page.py").read_text(encoding="utf-8")
    assert "Только важные" not in source
    assert "Поиск по названию" not in source
    assert "QTableWidget.SelectionMode.ExtendedSelection" in source
    assert "Удалить выбранные" in source
    assert "Техническая информация" in source
    assert "Обновить список" in source


def test_stage3_error_state_hides_workspace_until_list_is_readable():
    source = (ROOT / "src" / "gui" / "restore_points_page.py").read_text(encoding="utf-8")
    assert "QStackedWidget" in source
    assert "Не удалось получить точки восстановления" in source
    assert "Настроить доступ" in source
    assert '"Занимает"' not in source
    assert "Удалить выбранные" in source


def test_stage3_read_helper_is_narrow_and_non_mutating():
    helper = (ROOT / "src" / "privileged" / "read_restore_points.sh").read_text(encoding="utf-8")
    assert "snapper" in helper
    assert '"filesystem", "du"' not in helper
    assert "--fingerprint" in helper
    assert "--disable-used-space" in helper
    assert "number,date,user,cleanup,description,userdata,pre-number" in helper
    assert "number,date,user,used-space" not in helper
    assert " list " in helper.replace("\\\n", " ") or "\n    list \\\n" in helper
    forbidden = (" create", " delete", " modify", " rollback", " set-config", " undochange")
    assert all(token not in helper for token in forbidden)


def test_stage3_one_time_access_rule_can_only_run_read_helper():
    setup = (ROOT / "scripts" / "setup-restore-points-read-access.sh").read_text(encoding="utf-8")
    assert "NOPASSWD:" in setup
    assert "install_dir=/usr/lib/arch-manager" in setup
    assert "read-restore-points" in setup
    assert "ALLOW_USERS" not in setup
    assert "SYNC_ACL" not in setup
    assert "visudo -cf" in setup


def test_stage3_list_uses_nopasswd_read_helper_and_fingerprint(monkeypatch, tmp_path):
    import src.core.restore_points as module
    from src.core.command import CommandResult

    helper = tmp_path / "read-restore-points"
    helper.write_text("test", encoding="utf-8")
    monkeypatch.setattr(module, "PRIVILEGED_READER", helper)
    monkeypatch.setattr(module, "_snapper_configured", lambda: True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    calls = []
    fingerprint = "a" * 64

    def fake_run(args, *, timeout=30.0, accepted_returncodes=None):
        calls.append(tuple(args))
        if args[-1] == "--fingerprint":
            return CommandResult(tuple(args), 0, f"version=1\nfingerprint={fingerprint}\n", "")
        return CommandResult(
            tuple(args),
            0,
            '7|2026-09-21 12:00:00 +0300|root||number|Manual point|important=yes|\n',
            "",
        )

    monkeypatch.setattr(module, "run_command", fake_run)
    first = module.collect_restore_points_list()
    second = module.collect_restore_points_list()

    assert first.readable is True and second.readable is True
    assert first.points[0].number == 7
    assert calls[0] == ("sudo", "-n", str(helper), "--fingerprint")
    assert calls[1] == ("sudo", "-n", str(helper))
    assert calls[2] == ("sudo", "-n", str(helper), "--fingerprint")
    assert len(calls) == 3  # second read came from the local cache

