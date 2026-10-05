from datetime import datetime
import json
from pathlib import Path

import src.core.activity_log as activity_log
from src.core.activity_log import append_activity, read_activity_entries
from src.core.reboot_status import RebootStatus
from src.core.updates import parse_update_line, strip_terminal_control_sequences


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "gui" / "updates_page.py"
LOG_PAGE = ROOT / "src" / "gui" / "log_page.py"
VERSION = ROOT / "src" / "__init__.py"
STAGE5 = ROOT / "docs" / "STAGE_5.md"


def test_stage5_2_strips_ansi_sequences_from_aur_versions():
    raw = "\x1b[1mgoogle-chrome 153.0.8010.52-1 -> 154.0.8037.57-1\x1b[15m"
    item = parse_update_line(raw, source="aur")
    assert item is not None
    assert item.name == "google-chrome"
    assert item.current_version == "153.0.8010.52-1"
    assert item.new_version == "154.0.8037.57-1"
    assert "[15m" not in item.new_version
    assert strip_terminal_control_sequences(raw).endswith("154.0.8037.57-1")


def test_stage5_2_activity_log_round_trip(tmp_path, monkeypatch):
    log_path = tmp_path / "activity.jsonl"
    monkeypatch.setattr(activity_log, "activity_log_path", lambda: log_path)
    append_activity(
        "updates",
        "update-session",
        "success",
        "done",
        data={"restore_point": 12, "packages": ["google-chrome"]},
    )
    entries = read_activity_entries(limit=10, category="updates")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "update-session"
    assert entry.result == "success"
    assert entry.data["restore_point"] == 12
    assert entry.data["packages"] == ["google-chrome"]


def test_stage5_2_activity_log_ignores_corrupt_lines(tmp_path, monkeypatch):
    log_path = tmp_path / "activity.jsonl"
    log_path.write_text("not-json\n" + json.dumps({"timestamp": datetime.now().astimezone().isoformat(), "category": "updates", "action": "update-session", "result": "success"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(activity_log, "activity_log_path", lambda: log_path)
    entries = read_activity_entries(limit=10)
    assert len(entries) == 1
    assert entries[0].result == "success"


def test_stage5_2_updates_page_has_info_report_and_reboot_check():
    source = PAGE.read_text(encoding="utf-8")
    assert "Подробности последнего сеанса обновления" in source
    assert "assess_reboot_status()" in source
    assert "_record_update_session" in source
    assert "_load_last_report" in source
    assert "_show_last_report" in source
    assert "Технические детали" in source
    assert "Snapper ID" in source


def test_stage5_2_journal_is_no_longer_placeholder():
    source = LOG_PAGE.read_text(encoding="utf-8")
    assert "PlaceholderPage" not in source
    assert "read_activity_entries" in source
    assert "Обновление системы" in source
    assert "Подробности выбранного события" in source


def test_stage5_2_reboot_status_type_has_three_state_model():
    status = RebootStatus("unknown", "summary", "detail")
    assert not status.recommended
    status = RebootStatus("recommended", "summary", "detail")
    assert status.recommended


def test_stage5_2_aur_updates_are_selectable_in_table():
    source = PAGE.read_text(encoding="utf-8")
    assert 'class _SourceFilterHeader(QHeaderView)' in source
    assert '("AUR", "aur")' in source
    assert "ItemIsUserCheckable" in source
    assert "_selected_aur_packages" in source
    assert "AUR-пакеты можно выбирать по одному" in source


def test_stage5_2_version_and_docs():
    assert '__version__ = "0.6.1-stage6"' in VERSION.read_text(encoding="utf-8")
    text = STAGE5.read_text(encoding="utf-8")
    assert "ANSI" in text
    assert "перезагруз" in text.casefold()
    assert "журнал" in text.casefold()
