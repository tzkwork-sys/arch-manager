from pathlib import Path
from tests.source_bundles import recovery_page_source


def test_recovery_page_has_no_snapshot_selector_or_prepare_button():
    code = recovery_page_source(Path("."))
    # A combo box is now used only to choose the physical USB target; snapshot
    # selection must still happen exclusively inside the autonomous Recovery UI.
    assert "point_combo" not in code
    assert "Точка для восстановления" not in code
    assert 'QPushButton("Подготовить' not in code
    assert "RecoveryRequest" not in code


def test_recovery_page_prepares_automatically_on_first_full_check():
    code = recovery_page_source(Path("."))
    assert "refresh(auto_prepare=True)" in code
    assert "_prepare_automatically" in code
    assert 'self._run_action("prepare")' in code
    assert "автоматически подготовит актуальную Recovery-среду" in code


def test_recovery_page_reuses_cached_state_and_only_full_checks_after_change():
    code = recovery_page_source(Path("."))
    assert "collect_recovery_fingerprint" in code
    assert "_check_for_changes" in code
    assert "_state_fingerprint" in code
    assert "if value != self._state_fingerprint:" in code
    assert "self.refresh(auto_prepare=True)" in code
    ensure = code[code.index("    def ensure_loaded"):code.index("    def _manual_refresh")]
    assert "self._readiness is None" in ensure
    assert "self._check_for_changes()" in ensure


def test_recovery_page_is_compact_and_uses_small_info_button():
    code = recovery_page_source(Path("."))
    assert "QToolButton" in code
    assert 'self.info_button.setText("ⓘ")' in code
    assert "technical_card" not in code
    assert "Показать техническую информацию" not in code
    assert "QScrollArea" not in code


def test_recovery_page_explains_selection_happens_after_reboot():
    code = recovery_page_source(Path("."))
    assert "Точку восстановления вы выберете уже после перезагрузки" in code
    assert "Recovery покажет актуальные точки восстановления" in code
    assert 'QPushButton("Перезагрузить в режим восстановления")' in code


def test_post_prepare_verification_cannot_loop_auto_prepare():
    code = recovery_page_source(Path("."))
    assert "self.refresh(auto_prepare=False)" in code


def test_recovery_busy_check_has_one_text_line_only():
    code = recovery_page_source(Path("."))
    refresh = code[code.index("    def refresh"):code.index("    def _failed")]
    assert 'self.summary_title.setText("Проверяю Btrfs, systemd-boot и Recovery-среду…")' in refresh
    assert "self.summary_text.setVisible(False)" in refresh
    assert 'self._set_busy("", True)' in refresh
    assert "Проверяю готовность" not in refresh
