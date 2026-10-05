from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_stage731_aur_install_action_matches_official_header_placement():
    details = (ROOT / "src/gui/app_store/aur/details.py").read_text(encoding="utf-8")
    header = details.split("    def _build_header(self) -> None:", 1)[1].split(
        "    def _build_summary(self) -> None:", 1
    )[0]
    init = details.split("    def _section_title(self, text: str) -> QLabel:", 1)[0]

    assert 'self.install_button.setObjectName("appStoreAurInstallButton")' in header
    assert "self.install_button.setMinimumWidth(130)" in header
    assert "actions.addWidget(self.install_button)" in header
    assert "row.addLayout(actions)" in header

    # AUR package action belongs to the header, just like the official package action.
    assert "action_row.addWidget(self.install_button)" not in init
    assert 'self.close_button = QPushButton("Закрыть")' in init
    assert "close_row.addStretch(1)" in init
