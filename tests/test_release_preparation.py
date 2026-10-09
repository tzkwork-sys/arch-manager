import subprocess
import sys
import os
import pytest
import re
from pathlib import Path

from src.core.app_store_executor import _run_package_process


ROOT = Path(__file__).resolve().parents[1]


def test_project_front_page_links_and_contribution_templates():
    readme = (ROOT / "README.md").read_text()
    links = re.findall(r'\]\(([^)]+)\)|(?:href|src)="([^"]+)"', readme)
    for markdown, html in links:
        link = markdown or html
        if "://" not in link:
            assert (ROOT / link.split("#")[0]).is_file(), link
    for name in ("CONTRIBUTING.md", "SECURITY.md", ".github/CODEOWNERS",
                 ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/bug_report.yml",
                 ".github/ISSUE_TEMPLATE/feature_request.yml"):
        assert (ROOT / name).is_file()
    assert "Предварительная версия" in readme
    assert "README_HISTORY.md" in readme


def test_slow_package_process_finishes_after_timeout_without_being_killed():
    result = _run_package_process(
        [sys.executable, "-c", "import time; time.sleep(.1); print('finished')"], timeout=.01
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "finished"


def test_release_documents_and_installer_contract():
    assert "GPL-3.0-only" in (ROOT / "LICENSE").read_text()
    source = (ROOT / "scripts/install.sh").read_text()
    assert "mktemp -d" in source
    assert 'bash "$DEST/scripts/install-stage1.sh"' in source
    assert "install-stage7-recovery-helper.sh" not in source
    assert "Опасные системные действия не выполнялись" not in (ROOT / "src/app.py").read_text()
    subprocess.run(["bash", "-n", str(ROOT / "scripts/install.sh")], check=True)


def test_recovery_stages_before_replacing_and_keeps_backups():
    source = (ROOT / "scripts/install-stage7-recovery-helper.sh").read_text()
    assert source.index('"$SOURCE_HELPER_LIB/$module" "$HELPER_LIB_STAGE/$module"') < source.index(
        '"$HELPER_LIB" "$HELPER_LIB_BACKUP"'
    )
    assert source.index('"$SOURCE_PROFILE" "$RECOVERY_STAGE/archiso"') < source.index(
        '"$RECOVERY_RESOURCES" "$RECOVERY_BACKUP"'
    )
    assert "trap cleanup_install EXIT" in source
    assert "install_complete=1" in source


@pytest.mark.parametrize("complete", [0, 1])
def test_recovery_cleanup_restores_backups_only_on_failure(tmp_path, complete):
    source = (ROOT / "scripts/install-stage7-recovery-helper.sh").read_text()
    cleanup = source[source.index("cleanup_install() {"):source.index("trap cleanup_install EXIT")]
    # Replace sudo with a harmless argument-forwarding shell function: paths
    # are exclusively under pytest's temporary directory, never system paths.
    cleanup = cleanup.replace("/usr/bin/sudo", "fake_sudo")
    for name in ("lib", "resources", "old-lib", "old-resources"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "marker").write_text(name)
    script = f'''
fake_sudo() {{ [[ "$1" != -n ]] || shift; "$@"; }}
rule_tmp="{tmp_path}/rule"
sudoers_tmp="{tmp_path}/sudoers"
HELPER_LIB="{tmp_path}/lib"
RECOVERY_RESOURCES="{tmp_path}/resources"
HELPER_LIB_BACKUP="{tmp_path}/old-lib"
RECOVERY_BACKUP="{tmp_path}/old-resources"
install_complete={complete}
{cleanup}
trap cleanup_install EXIT
exit 7
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 7
    assert (tmp_path / "lib/marker").read_text() == ("lib" if complete else "old-lib")
    assert (tmp_path / "resources/marker").read_text() == ("resources" if complete else "old-resources")


@pytest.mark.skipif(os.geteuid() == 0, reason="user installer intentionally refuses root")
def test_user_install_copies_distribution_before_launcher(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("src", "scripts", "recovery", "packaging", "desktop", "docs"):
        (project / name).mkdir()
    for name in ("main.py", "requirements.txt", "README.md", "LICENSE", "COPYING"):
        (project / name).write_text("fixture")
    (project / "scripts/install.sh").write_text((ROOT / "scripts/install.sh").read_text())
    (project / "scripts/install-stage1.sh").write_text(
        'test -f "$(dirname "$0")/../main.py"\n'
    )
    result = subprocess.run(["bash", str(project / "scripts/install.sh")],
        env={**os.environ, "XDG_DATA_HOME": str(tmp_path / "data")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    installed = list((tmp_path / "data/arch-manager/versions").iterdir())
    assert len(installed) == 1
    assert (installed[0] / "main.py").read_text() == "fixture"
