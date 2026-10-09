#!/usr/bin/bash
# Optional, explicit per-user permission. Never overwrite pacman-owned helpers.
set -euo pipefail
export PATH=/usr/bin:/bin LC_ALL=C LANG=C

fail() { printf 'Arch Manager: %s\n' "$*" >&2; exit 1; }
[[ $# == 1 ]] || fail 'usage: arch-manager-enable-snapper-read USER_ID'
requested_uid=$1
[[ $requested_uid =~ ^[0-9]+$ ]] || fail 'invalid user id'
(( requested_uid > 0 )) || fail 'root does not need this permission'
script_path=$(readlink -f -- "$0")
[[ $script_path == /usr/share/arch-manager/scripts/setup-restore-points-read-access.sh ]] \
    || fail 'run the package-installed command, not a source copy'
if (( EUID != 0 )); then
    exec /usr/bin/pkexec "$script_path" "$requested_uid"
fi

user_line=$(getent passwd "$requested_uid") || fail 'user not found'
IFS=: read -r target_user _ actual_uid _ _ _ _ <<<"$user_line"
[[ $actual_uid == "$requested_uid" && $target_user != root ]] || fail 'user id mismatch'
[[ $target_user =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || fail 'unsupported user name'
helper=/usr/lib/arch-manager/read-restore-points
[[ -f $helper && ! -L $helper && -x $helper ]] || fail 'package read helper is missing'
[[ $(stat -Lc '%u:%g:%a' "$helper") == 0:0:755 ]] || fail 'unsafe helper permissions'
sudoers_file="/etc/sudoers.d/arch-manager-read-restore-points-${requested_uid}"
[[ ! -e $sudoers_file && ! -L $sudoers_file ]] || fail 'permission already exists; review it before replacing it'
tmp_rule=$(mktemp)
trap 'rm -f -- "$tmp_rule"' EXIT
printf '%s ALL=(root) NOPASSWD: %s\n' "$target_user" "$helper" >"$tmp_rule"
chmod 0440 "$tmp_rule"
/usr/bin/visudo -cf "$tmp_rule" >/dev/null || fail 'sudo rule validation failed'
install -o root -g root -m 0440 "$tmp_rule" "$sudoers_file"
if ! /usr/bin/su -s /bin/sh -c "/usr/bin/sudo -n '$helper' --probe >/dev/null" "$target_user"; then
    rm -f -- "$sudoers_file"
    fail 'read access validation failed'
fi
printf 'OK\n'
