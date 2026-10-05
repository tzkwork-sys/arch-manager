#!/usr/bin/env bash
set -euo pipefail

export PATH=/usr/bin:/bin
export LC_ALL=C
export LANG=C

fail() {
    printf 'Arch Manager: %s\n' "$*" >&2
    exit 1
}

requested_uid="${1:-}"
[[ "$requested_uid" =~ ^[0-9]+$ ]] || fail 'invalid user id'
script_path="$(readlink -f -- "$0")"
[[ -n "$script_path" && -f "$script_path" ]] || fail 'cannot resolve setup script path'

# The script elevates itself only for this one-time installation. The GUI runs
# it as the signed-in user; Polkit then shows the normal administrator prompt.
if (( EUID != 0 )); then
    PKEXEC=/usr/bin/pkexec
    [[ -x "$PKEXEC" ]] || fail 'pkexec is not available'
    exec "$PKEXEC" "$script_path" "$requested_uid"
fi

user_line="$(getent passwd "$requested_uid" || true)"
[[ -n "$user_line" ]] || fail 'user not found'
IFS=: read -r target_user _ actual_uid _ _ _ _ <<<"$user_line"
[[ "$actual_uid" == "$requested_uid" ]] || fail 'user id mismatch'
[[ "$target_user" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || fail 'unsupported user name'
[[ "$target_user" != root ]] || fail 'root does not need this access rule'

project_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)"
source_helper="$project_root/src/privileged/read_restore_points.sh"
[[ -f "$source_helper" ]] || fail 'read helper is missing from the project'

install_dir=/usr/lib/arch-manager
installed_helper="$install_dir/read-restore-points"
sudoers_file="/etc/sudoers.d/arch-manager-read-restore-points-${requested_uid}"

install -d -o root -g root -m 0755 "$install_dir"
install -o root -g root -m 0755 "$source_helper" "$installed_helper"

# Grant exactly one root command to exactly this local user. The installed
# command is root-owned and rejects every argument except a harmless --probe,
# so it cannot be reused to run arbitrary Snapper operations.
tmp_rule="$(mktemp)"
trap 'rm -f "$tmp_rule"' EXIT
printf '%s ALL=(root) NOPASSWD: %s\n' "$target_user" "$installed_helper" >"$tmp_rule"
chmod 0440 "$tmp_rule"

if ! /usr/bin/visudo -cf "$tmp_rule" >/dev/null; then
    fail 'generated sudo rule did not pass validation'
fi
install -o root -g root -m 0440 "$tmp_rule" "$sudoers_file"

# Validate that the rule works for the selected account. Do not expose the
# snapshot list during setup; only the exit status matters here.
if ! /usr/bin/su -s /bin/sh -c "/usr/bin/sudo -n '$installed_helper' --probe >/dev/null" "$target_user"; then
    rm -f "$sudoers_file" "$installed_helper"
    fail 'installed read rule could not be verified'
fi

printf 'OK\n'
