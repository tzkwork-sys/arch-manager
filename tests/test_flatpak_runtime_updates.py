"""Updates must include Flatpak runtimes without losing ordinary apps."""
from src.app_store.flatpak import FlatpakService
from src.core.command import CommandResult


def _runner(argv, *, timeout=45):
    del timeout
    line = ' '.join(argv)
    content = ''
    if 'list --app --system' in line:
        content = 'org.example.Editor\tEditor\t1.0\tstable\tflathub\n'
    elif 'list --runtime --system' in line:
        content = ('org.freedesktop.Platform.GL.default\t26.2.2\t25.08\n'
                   'org.freedesktop.Platform.VAAPI.Intel\t\t25.08\n')
    elif 'remote-ls --updates --app --system' in line:
        content = 'org.example.Editor\t1.1\tstable\n'
    elif 'remote-ls --updates --runtime --system' in line:
        content = ('org.freedesktop.Platform.GL.default\t26.2.4\t25.08\n'
                   'org.freedesktop.Platform.VAAPI.Intel\t\t25.08\n')
    return CommandResult(tuple(argv), 0, content, '')


def test_apps_and_runtimes_are_both_visible():
    details = FlatpakService(runner=_runner).update_details()
    assert details.available and details.error is None
    assert {(item.name, item.installation) for item in details.items} == {
        ('org.example.Editor', 'system'),
        ('org.freedesktop.Platform.GL.default', 'system'),
        ('org.freedesktop.Platform.VAAPI.Intel', 'system'),
    }
    versions = {item.name: item.new_version for item in details.items}
    assert versions['org.example.Editor'] == '1.1'
    assert versions['org.freedesktop.Platform.GL.default'] == '26.2.4'
    assert versions['org.freedesktop.Platform.VAAPI.Intel'] == 'новая ревизия'
