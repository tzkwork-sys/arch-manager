from datetime import datetime
from pathlib import Path

from src.core.system_info import SYSTEM_WARNING_BADGE_THRESHOLD
from src.core.updates import (
    UpdateDetails,
    UpdateItem,
    UpdateSourceDetails,
    _parse_pacman_info_blocks,
    _parse_size_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "src" / "gui" / "dashboard.py"
SYSTEM_PAGE = ROOT / "src" / "gui" / "system_page.py"
UPDATES_PAGE = ROOT / "src" / "gui" / "updates_page.py"
APP_STORE_PAGE = ROOT / "src" / "gui" / "app_store" / "page.py"
MAIN_WINDOW = ROOT / "src" / "gui" / "main_window.py"


def test_small_system_warning_count_threshold_is_six():
    assert SYSTEM_WARNING_BADGE_THRESHOLD == 6
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    system_page = SYSTEM_PAGE.read_text(encoding="utf-8")
    assert "len(warnings) < SYSTEM_WARNING_BADGE_THRESHOLD" in dashboard
    assert "count < SYSTEM_WARNING_BADGE_THRESHOLD" in system_page
    assert 'self.state_badge.set_status(\n                    "ok"' in system_page


def test_overview_cards_are_whole_clickable_status_outlined_buttons_and_timestamp_is_below_header():
    source = DASHBOARD.read_text(encoding="utf-8")
    assert "class SummaryCard(QPushButton):" in source
    assert 'border: 2px solid {color.name()}' in source
    assert '"Обновления": "Посмотреть обновления →"' in source
    assert source.index("root.addWidget(self.checked_label)") < source.index("root.addLayout(grid)")
    assert "self._refresh_pending = True" in source


def test_update_download_size_parser_and_item_are_backward_compatible():
    assert _parse_size_bytes("1.50 MiB") == round(1.5 * 1024**2)
    assert _parse_size_bytes("800.00 KiB") == 800 * 1024
    item = UpdateItem("linux", "1", "2", "official")
    assert item.download_size is None

    blocks = _parse_pacman_info_blocks(
        "Repository      : core\n"
        "Name            : linux\n"
        "Version         : 2\n"
        "Download Size   : 128.50 MiB\n\n"
        "Repository      : extra\n"
        "Name            : demo\n"
        "Version         : 3\n"
        "Download Size   : 2.00 MiB\n"
    )
    assert blocks["linux"]["Download Size"] == "128.50 MiB"
    assert blocks["demo"]["Download Size"] == "2.00 MiB"


def test_updates_table_has_integrated_source_filter_and_download_column():
    source = UPDATES_PAGE.read_text(encoding="utf-8")
    assert "class _SourceFilterHeader(QHeaderView):" in source
    assert '["Выбор", "Пакет", "Установлено", "Доступно", "Скачать", "Источник  ▾"]' in source
    assert "self.source_combo" not in source
    assert '"Все источники", "all"' in source
    assert '"официальные: {format_bytes(known_bytes)}"' in source
    assert '"AUR: размер определяется при сборке"' in source


def test_download_summary_keeps_aur_size_explicitly_unknown_until_build():
    # Pure model regression: total count still works when only official items have
    # a meaningful pre-download size.
    details = UpdateDetails(
        official=UpdateSourceDetails(
            (UpdateItem("linux", "1", "2", "official", 10 * 1024**2),),
            True,
        ),
        aur=UpdateSourceDetails(
            (UpdateItem("example-aur", "1", "2", "aur"),),
            True,
        ),
        checked_at=datetime.now().astimezone(),
    )
    assert details.total == 2
    assert sum(item.download_size or 0 for item in details.official.items) == 10 * 1024**2
    assert details.aur.items[0].download_size is None


def test_app_store_reflows_on_actual_scroll_viewport_resize():
    source = APP_STORE_PAGE.read_text(encoding="utf-8")
    assert "self.scroll.viewport().installEventFilter(self)" in source
    assert "watched is self.scroll.viewport()" in source
    assert "QEvent.Type.Resize" in source
    assert "QTimer.singleShot(0, self._relayout_cards)" in source


def test_application_always_starts_on_overview():
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    restore = source[source.index("    def restore_window_state"): source.index("    def closeEvent")]
    assert "self.set_page(0)" in restore
    assert 'self.settings.value("window/currentPage"' not in restore


def test_partial_update_failures_also_trigger_state_refresh():
    source = UPDATES_PAGE.read_text(encoding="utf-8")
    assert 'if result.state in {"official-failed", "aur-failed", "interrupted", "failed"}:' in source
    assert "self.update_state.invalidate()" in source
    assert "self.system_update_finished.emit()" in source
