from pathlib import Path
import importlib.util

from src.app_store.actions import build_remove_request
from src.app_store.aur.models import ForeignPackage
from src.app_store.local_installed import LocalInstalledApplicationReader
from src.app_store.models import Application
from src.app_store.package_actions import PacmanPackageActionPlanner
from src.app_store.aur.models import AurPackage
from src.core.command import CommandResult


ROOT = Path(__file__).resolve().parents[1]


def _merge_store_results():
    path = ROOT / 'src/gui/app_store/aur/integration.py'
    spec = importlib.util.spec_from_file_location('arch_manager_local_integration', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.merge_store_results


def _result(command, returncode=0, stdout='', stderr='') -> CommandResult:
    return CommandResult(tuple(command), returncode, stdout, stderr)


def test_local_reader_builds_desktop_card_for_foreign_non_aur_package(tmp_path: Path):
    applications_dir = tmp_path / 'applications'
    applications_dir.mkdir()
    desktop = applications_dir / 'vpnus.desktop'
    desktop.write_text(
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Name=VPNUS\n'
        'Comment=VPN client with a system tray and a privileged tunnel daemon\n'
        'Icon=vpnus\n'
        'Categories=Network;Security;\n',
        encoding='utf-8',
    )

    def runner(command, **_kwargs):
        assert command == ['pacman', '-Qlq', 'vpnus']
        return _result(command, stdout=f'{desktop}\n/usr/bin/vpnus\n')

    reader = LocalInstalledApplicationReader(runner=runner, desktop_dirs=(applications_dir,))
    apps = reader.build((ForeignPackage('vpnus', '3.2.0-1'),))

    assert len(apps) == 1
    app = apps[0]
    assert app.name == 'VPNUS'
    assert app.package_name == 'vpnus'
    assert app.installed is True
    assert app.installed_version == '3.2.0-1'
    assert app.available_version == '3.2.0-1'
    assert app.repository == 'Локальный пакет'
    assert app.desktop_entry == 'vpnus.desktop'
    assert app.icon == 'vpnus'
    assert app.categories == ('internet', 'system-tools')
    assert 'VPN client' in app.summary


def test_local_reader_skips_hidden_desktop_entry(tmp_path: Path):
    applications_dir = tmp_path / 'applications'
    applications_dir.mkdir()
    desktop = applications_dir / 'hidden.desktop'
    desktop.write_text(
        '[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n',
        encoding='utf-8',
    )

    reader = LocalInstalledApplicationReader(
        runner=lambda command, **_kwargs: _result(command, stdout=f'{desktop}\n'),
        desktop_dirs=(applications_dir,),
    )
    assert reader.build((ForeignPackage('hidden', '1-1'),)) == ()


def test_store_merge_supports_local_source_without_changing_old_sources():
    merge_store_results = _merge_store_results()
    official = Application(app_id='official.desktop', package_name='official', name='Official')
    local = Application(
        app_id='vpnus.desktop',
        package_name='vpnus',
        name='VPNUS',
        installed=True,
        repository='Локальный пакет',
    )
    aur = AurPackage(name='aur-demo', package_base='aur-demo', version='1')

    assert merge_store_results((official,), (aur,), source='local', local=(local,)) == (local,)
    mixed = merge_store_results((official,), (aur,), source='all', local=(local,))
    assert {getattr(item, 'name', '') for item in mixed} == {'Official', 'VPNUS', 'aur-demo'}
    assert merge_store_results((official,), (aur,), source='official', local=(local,)) == (official,)
    assert merge_store_results((official,), (aur,), source='aur', local=(local,)) == (aur,)


def test_remove_planner_accepts_installed_local_package(tmp_path: Path):
    calls = []

    def runner(command, **_kwargs):
        command = tuple(command)
        calls.append(command)
        if command == ('pacman', '-Sl'):
            return _result(command, stdout='extra other 1-1\n')
        if command == ('pacman', '-Q', '--', 'vpnus'):
            return _result(command, stdout='vpnus 3.2.0-1\n')
        if command[:2] == ('pacman', '-Rsp'):
            return _result(command, stdout='vpnus|3.2.0-1\n')
        raise AssertionError(command)

    planner = PacmanPackageActionPlanner(runner=runner, lock_path=tmp_path / 'db.lck')
    plan = planner.plan(build_remove_request('vpnus'))
    assert [item.package_name for item in plan.changes] == ['vpnus']
    assert ('pacman', '-Q', '--', 'vpnus') in calls


def test_privileged_remove_does_not_require_official_repository_anymore():
    helper = (ROOT / 'src/privileged/manage_applications.sh').read_text(encoding='utf-8')
    remove_block = helper.split('run_remove() {', 1)[1].split('\nself_test() {', 1)[0]
    assert 'official_repository_for' not in remove_block
    assert '"$PACMAN" -Rs --noconfirm "$package_name"' in remove_block
    assert 'official_repository_for "$package_name"' in helper
