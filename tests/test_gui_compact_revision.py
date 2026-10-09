from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src" / "gui"


def _source(name: str) -> str:
    return (GUI / name).read_text(encoding="utf-8")


def test_internal_pages_share_compact_header_and_action_area_without_redundant_back_button():
    source = _source("page_base.py")
    assert "layout.setContentsMargins(32, 18, 32, 24)" in source
    assert "Назад к обзору" not in source
    assert "self.header_actions = QHBoxLayout()" in source
    assert "def add_header_action" in source


def test_dashboard_uses_quiet_maintenance_threshold_and_amount_in_summary():
    source = _source("dashboard.py")
    assert "MAINTENANCE_WARNING_BYTES = 512 * 1024 * 1024" in source
    assert 'label = f"Есть что очистить ({amount})"' in source
    assert 'return label, "ok"' in source
    assert 'return label, "warning"' in source


def test_updates_use_header_actions_consistent_selection_and_compact_table():
    source = _source("updates_page.py")
    assert 'layout = self.create_page_layout("Обновления")' in source
    assert "self.add_header_action(self.refresh_button)" in source
    assert "self.add_header_action(self.install_button)" in source
    assert "self.table.currentCellChanged.connect(self._show_selected_package)" in source
    assert "choice.setCheckState(Qt.CheckState.Checked)" in source
    assert "visible_rows = max(2, min(len(items), 10))" not in source
    assert "self.table.setMaximumHeight(16777215)" in source
    assert "collect_installed_package_info" in source


def test_recovery_keeps_restore_table_compact_so_recovery_method_stays_visible():
    center = _source("recovery_center_page.py")
    points = _source("restore_points_page.py")
    recovery = _source("recovery_page.py")
    assert "attach_header_actions(self.header_actions)" in center
    assert "layout.addWidget(self.restore_points, 0, Qt.AlignmentFlag.AlignTop)" in center
    assert "layout.setAlignment(Qt.AlignmentFlag.AlignTop)" in center
    assert "self.body_stack.setFixedHeight(306)" in points
    assert "self.table.setFixedHeight(300)" in points
    assert "visible_issues.extend(readiness.notes)" not in recovery
    assert "self.state_label.setVisible(False)" in points
    assert "self._fit_recovery_stack" in recovery


def test_maintenance_moves_secondary_information_behind_info_buttons():
    source = _source("maintenance_page.py")
    assert 'self.info_button.setText("ⓘ")' in source
    assert "def _show_info" in source
    assert 'self.summary_info_button.setText("ⓘ")' in source
    assert 'self.config_info_button.setText("ⓘ")' in source


def test_journal_is_embedded_in_settings_not_main_navigation():
    main = _source("main_window.py")
    settings = _source("settings_page.py")
    log = _source("log_page.py")
    assert '("Журнал", "view-list-text-symbolic")' not in main
    assert 'self.tabs.addTab(self.log_page, "Журнал действий")' in settings
    assert "LogPage(self, embedded=True)" in settings
    assert '"restore-point-failed": "Защитная точка не создана"' in log
    assert "Stage 5" not in settings


def test_system_page_uses_compact_cards_and_shared_status_badge():
    source = _source("system_page.py")
    assert "StatusBadge" in source
    assert "CATEGORY_TITLES" in source
    assert '"storage": "Диски и Btrfs"' in source
    assert '"services": "Службы"' in source
    assert 'QPushButton("Полная диагностика")' in source
    assert 'QPushButton("Технические детали")' in source
    assert "_build_identity_card" in source
    assert 'self._add_title(grid, "Сведения о системе", 0)' in source
    assert 'info_button.setText("ⓘ")' in source
    assert "def _show_category_details" in source
    assert "self.scroll.verticalScrollBar().setValue(0)" in source
