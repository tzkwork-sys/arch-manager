from __future__ import annotations

import importlib.util
from pathlib import Path

from src.app_store.aur.models import AurPackage


ROOT = Path(__file__).resolve().parents[2]


def _load_integration():
    path = ROOT / "src/gui/app_store/aur/integration.py"
    spec = importlib.util.spec_from_file_location("arch_manager_stage732_integration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _aur(name: str, *, installed: bool, description: str = "", maintainer: str | None = "dev") -> AurPackage:
    package = AurPackage(
        name=name,
        package_base=name,
        version="2.0-1",
        description=description,
        maintainer=maintainer,
    )
    if installed:
        return package.with_local_state(installed_version="1.0-1")
    return package


def test_stage732_installed_aur_filter_is_local_and_installed_only():
    filter_installed = _load_integration().filter_installed_aur_packages
    packages = (
        _aur("google-chrome", installed=True, description="Web browser"),
        _aur("tetris-terminal-git", installed=True, description="Tetris game", maintainer="alice"),
        _aur("not-installed", installed=False, description="Tetris helper"),
    )

    assert [item.name for item in filter_installed(packages, "")] == [
        "google-chrome",
        "tetris-terminal-git",
    ]
    assert [item.name for item in filter_installed(packages, "tetris")] == [
        "tetris-terminal-git",
    ]
    assert [item.name for item in filter_installed(packages, "alice game")] == [
        "tetris-terminal-git",
    ]


def test_stage732_page_contract_survives_stage75_source_expansion():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")

    assert 'source_selectable = view in {"catalog", "installed", "updates"}' in page
    assert 'view == "installed" and source == "official"' in page
    assert "self._aur_service.installed_async" in page
    assert "filter_installed_aur_packages" in page
    assert 'self._queue_aur_installed()' in page
    assert 'self._queue_aur_installed()' in page
    assert 'Источник обновлений: все, официальные или AUR' in page


def test_stage732_official_update_policy_is_visible_in_updates_view():
    page = (ROOT / "src/gui/app_store/page.py").read_text(encoding="utf-8")

    assert "appStoreOfficialUpdatePolicyNote" in page
    assert "полным " in page and "системным обновлением Arch Linux" in page
    assert "отдельной кнопки «Обновить»" in page
    assert 'self.update_policy_note.setVisible(view == "updates")' in page
