"""Regression checks for maintenance fixed-point cleanup and Overview reentry."""
from pathlib import Path
import importlib.util
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_orphan_cleanup_works_to_fixed_point_with_safe_commands(tmp_path):
    """Mock pacman: removing A reveals B; one cleanup must remove both."""
    helper = (ROOT / 'src/privileged/manage_maintenance.sh').read_text(encoding='utf-8')
    state = tmp_path / 'state'
    state.write_text('0', encoding='utf-8')
    fake_pacman = tmp_path / 'pacman'
    fake_pacman.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        f'state={str(state)!r}\n'
        'n=$(cat "$state")\n'
        'if [[ "${1:-}" == "-Qtdq" ]]; then\n'
        '    case "$n" in\n'
        '        0) printf "orphan-a\\n" ;;\n'
        '        1) printf "orphan-b\\n" ;;\n'
        '        *) exit 1 ;;\n'
        '    esac\n'
        'elif [[ "${1:-}" == "-R" && "${2:-}" == "--noconfirm" && "${3:-}" == "--" ]]; then\n'
        '    if [[ "$n" == 0 && "${4:-}" == orphan-a ]] || '
        '[[ "$n" == 1 && "${4:-}" == orphan-b ]]; then\n'
        '        printf "%s\\n" "$((n+1))" > "$state"\n'
        '    else exit 19; fi\n'
        'else exit 20; fi\n', encoding='utf-8')
    fake_pacman.chmod(0o700)
    helper = helper.replace('PACMAN=/usr/bin/pacman', 'PACMAN=' + str(fake_pacman))
    helper = helper.replace('PACCACHE=/usr/bin/paccache', 'PACCACHE=/usr/bin/true')
    helper = helper.replace('JOURNALCTL=/usr/bin/journalctl', 'JOURNALCTL=/usr/bin/true')
    helper = helper.replace('require_root() {\n    [[ ${EUID:-$(/usr/bin/id -u)} -eq 0 ]] || fail \'must run as root\'\n}', 'require_root() { :; }')
    mock_helper = tmp_path / 'mock-helper.sh'
    mock_helper.write_text(helper, encoding='utf-8')
    completed = subprocess.run(
        ['/usr/bin/bash', str(mock_helper), 'clean', '--cache-keep', '3', 'orphans'],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'OK\torphans\t2' in completed.stdout
    assert 'OK\tdone' in completed.stdout
    assert state.read_text(encoding='utf-8').strip() == '2'


def test_dashboard_does_not_present_active_journal_as_reclaimable():
    from src.core.maintenance import MaintenanceSummary
    from src.gui.dashboard import _maintenance_state
    summary = MaintenanceSummary(
        reclaimable_cache_bytes=0, orphan_packages=0, journal_usage_bytes=200_000_000,
        config_attention_files=0, trash_bytes=0, thumbnail_cache_bytes=0,
        orphan_installed_bytes=0,
    )
    value, status, detail = _maintenance_state(summary)
    assert value == 'Очистка не требуется'
    assert status == 'ok'
    assert 'Активные' in detail


@pytest.mark.skipif(importlib.util.find_spec('PySide6') is None, reason='PySide6 отсутствует')
def test_overview_refreshes_once_only_when_reentered():
    from src.gui.main_window import MainWindow

    class Stack:
        def __init__(self): self.current = 3
        def currentIndex(self): return self.current
        def setCurrentIndex(self, index): self.current = index
        def count(self): return 7

    class Nav:
        def __init__(self): self.current = 3
        def currentRow(self): return self.current
        def setCurrentRow(self, index): self.current = index

    class Label:
        def setText(self, _value): pass

    class Settings:
        def setValue(self, *_): pass

    class Dashboard:
        def __init__(self): self.calls = 0
        def refresh(self): self.calls += 1
        def ensure_loaded(self): pass

    dashboard = Dashboard()
    pages = [dashboard] + [SimpleNamespace(ensure_loaded=lambda: None) for _ in range(6)]
    window = SimpleNamespace(
        navigation=Nav(), stack=Stack(), sidebar_brand=Label(),
        sidebar_stack=SimpleNamespace(setCurrentIndex=lambda _: None),
        pages=pages, settings=Settings(),
    )
    MainWindow.set_page(window, 0)
    assert dashboard.calls == 1
    MainWindow.set_page(window, 0)
    assert dashboard.calls == 1
    MainWindow.set_page(window, 4)
    MainWindow.set_page(window, 0)
    assert dashboard.calls == 2


@pytest.mark.skipif(importlib.util.find_spec('PySide6') is None, reason='PySide6 отсутствует')
def test_overview_navigation_handles_nonrefreshable_test_placeholder():
    # Regression: some existing navigation tests use object() for each page.
    from unittest.mock import Mock
    from src.gui.main_window import MainWindow

    view = SimpleNamespace(
        stack=Mock(), navigation=Mock(), sidebar_brand=Mock(),
        sidebar_stack=Mock(), pages=[object() for _ in range(7)],
        settings=Mock(), _sync_app_store_sidebar_selection_from_page=Mock(),
    )
    view.stack.count.return_value = 7
    view.stack.currentIndex.return_value = 5
    view.navigation.currentRow.return_value = 0
    MainWindow.set_page(view, 0)
    view.sidebar_brand.setText.assert_called_once_with('Arch Manager')
    view.stack.setCurrentIndex.assert_called_once_with(0)


def test_maintenance_scan_locks_cleanup_and_shows_verified_status():
    source = (ROOT / 'src/gui/maintenance_page.py').read_text(encoding='utf-8')
    assert 'self.busy_bar.setVisible(busy)' in source
    assert 'maintenance_changed = Signal()' in source
    assert 'self._scan_worker, self._cleanup_worker, self._pacdiff_worker' in source
    assert 'self._post_cleanup_actions = result.completed_actions' in source
    assert 'Выбранные категории перепроверены.' in source
    completed = source.split('def _cleanup_completed(', 1)[1].split('def _cleanup_failed(', 1)[0]
    assert 'QMessageBox.information(' not in completed
