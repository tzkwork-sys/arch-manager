from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _executable(path, text):
    path.write_text("#!/usr/bin/env bash\n" + text, encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def test_update_runner_reuses_authorization_without_invalidating_it(tmp_path):
    log = tmp_path / "sudo.log"
    sudo = _executable(tmp_path / "sudo", '''
printf '%s\n' "$*" >> "$AUTH_LOG"
case "$1" in
    -v) exit 0 ;;
    -n) shift; exec "$@" ;;
    *) exit 99 ;;
esac
''')
    pacman = _executable(tmp_path / "pacman", "exit 0\n")
    source = (ROOT / "scripts/run-system-update-session.sh").read_text()
    source = source.replace("SUDO=/usr/bin/sudo", f'SUDO="{sudo}"')
    source = source.replace("PACMAN=/usr/bin/pacman", f'PACMAN="{pacman}"')
    source = source.replace("/usr/bin/sleep 45", "/usr/bin/sleep 0.01")
    runner = tmp_path / "runner.sh"
    runner.write_text(source)
    status = tmp_path / "status"
    result = subprocess.run(
        ["bash", str(runner), "--official", "yes", "--restore-point", "no",
         "--status-file", str(status)],
        env={**os.environ, "AUTH_LOG": str(log), "ARCH_MANAGER_PAUSE_ON_EXIT": "0"},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "-v"
    assert "-k" not in calls
    assert f"-n {pacman} -Syu" in calls
    assert status.read_text().startswith("success\t")


@pytest.mark.parametrize("worker_exit", [0, 7])
def test_aur_keepalive_never_prompts_and_stops_on_exit(tmp_path, worker_exit):
    log = tmp_path / "sudo.log"
    ticket = tmp_path / "ticket"
    sudo = _executable(tmp_path / "sudo", '''
[[ "$*" == '-n -v' ]] || exit 99
if [[ -e "$AUTH_TICKET" ]]; then
    printf 'cached\n' >> "$AUTH_LOG"
else
    printf 'missing\n' >> "$AUTH_LOG"
    exit 1
fi
''')
    source = (ROOT / "scripts/run-aur-operation.sh").read_text()
    functions = source[source.index("start_sudo_keepalive() {"):source.index("trap cleanup EXIT")]
    functions = functions.replace("/usr/bin/sleep 45", "/usr/bin/sleep 0.01")
    runner = tmp_path / "keepalive.sh"
    runner.write_text(f'''
SUDO="{sudo}"
KEEPALIVE_PID=""
PAUSE_ON_EXIT=0
{functions}
trap cleanup EXIT
start_sudo_keepalive
worker_pid=$KEEPALIVE_PID
start_sudo_keepalive
[[ "$KEEPALIVE_PID" == "$worker_pid" ]] || exit 90
for ((i=0; i<200; i++)); do
    grep -q missing "$AUTH_LOG" 2>/dev/null && break
    /usr/bin/sleep 0.01
done
grep -q missing "$AUTH_LOG" || exit 91
touch "$AUTH_TICKET"
for ((i=0; i<200; i++)); do
    grep -q cached "$AUTH_LOG" && break
    /usr/bin/sleep 0.01
done
grep -q cached "$AUTH_LOG" || exit 92
# Cleanup must finish even on failure, with no background renewals left.
trap 'cleanup; kill -0 "$worker_pid" 2>/dev/null && exit 93; exit {worker_exit}' EXIT
exit {worker_exit}
''')
    result = subprocess.run(
        ["bash", str(runner)],
        env={**os.environ, "AUTH_LOG": str(log), "AUTH_TICKET": str(ticket)},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == worker_exit, result.stderr
    assert set(log.read_text().splitlines()) == {"missing", "cached"}


def test_aur_operations_start_keepalive_only_before_package_mutation():
    source = (ROOT / "scripts/run-aur-operation.sh").read_text()
    for stage in ("yay-install", "yay-remove", "yay-update", "yay-update-all"):
        assert f'write_status running "{stage}"\n' + "start_sudo_keepalive" in source.replace(
            "\n    start_sudo_keepalive", "\nstart_sudo_keepalive"
        )
    assert source.index("stop_sudo_keepalive\n", source.index("cleanup() {")) < source.index(
        'if [[ "$PAUSE_ON_EXIT"'
    )
