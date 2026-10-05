#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'Arch Manager diagnostics helper installer: %s\n' "$*" >&2
    exit 2
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_HELPER="$PROJECT_DIR/src/privileged/read_system_diagnostics.py"
SOURCE_POLICY="$PROJECT_DIR/packaging/polkit/org.archmanager.read-system-diagnostics.policy"

ACTION_ID='org.archmanager.read-system-diagnostics'
INSTALLED_HELPER='/usr/local/libexec/arch-manager/read-system-diagnostics'
INSTALLED_POLICY='/usr/share/polkit-1/actions/org.archmanager.read-system-diagnostics.policy'
INSTALLED_RULE='/etc/polkit-1/rules.d/00-arch-manager-system-diagnostics.rules'

for command_path in /usr/bin/cmp /usr/bin/getent /usr/bin/id /usr/bin/install /usr/bin/mktemp /usr/bin/pkaction /usr/bin/python /usr/bin/rm /usr/bin/stat /usr/bin/sudo; do
    [[ -x "$command_path" ]] || fail "required command is missing: $command_path"
done

[[ -f "$SOURCE_HELPER" && ! -L "$SOURCE_HELPER" ]] || fail 'helper source is missing or is a symlink'
[[ -f "$SOURCE_POLICY" && ! -L "$SOURCE_POLICY" ]] || fail 'Polkit policy source is missing or is a symlink'
/usr/bin/python -m py_compile "$SOURCE_HELPER" || fail 'helper source has invalid Python syntax'
"$SOURCE_HELPER" --self-test >/dev/null || fail 'helper source self-test failed'
/usr/bin/getent group polkitd >/dev/null || fail 'polkitd group is missing; is polkit installed?'

if (( EUID == 0 )); then
    TARGET_USER=${SUDO_USER:-}
    [[ -n "$TARGET_USER" && "$TARGET_USER" != root ]] || fail 'run this installer as the desktop user, not directly as root'
else
    TARGET_USER=$(/usr/bin/id -un)
fi
/usr/bin/getent passwd "$TARGET_USER" >/dev/null || fail "desktop user does not exist: $TARGET_USER"
TARGET_USER_JSON=$(/usr/bin/python -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TARGET_USER")

rule_tmp=$(/usr/bin/mktemp)
trap '/usr/bin/rm -f -- "$rule_tmp"' EXIT
cat > "$rule_tmp" <<RULE
polkit.addRule(function(action, subject) {
    if (action.id != "${ACTION_ID}") {
        return polkit.Result.NOT_HANDLED;
    }
    if (action.lookup("program") != "${INSTALLED_HELPER}" || action.lookup("user") != "root") {
        return polkit.Result.NO;
    }
    if (subject.user == ${TARGET_USER_JSON} && subject.local == true && subject.active == true) {
        return polkit.Result.AUTH_ADMIN;
    }
    return polkit.Result.NO;
});
RULE

/usr/bin/sudo -v
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /usr/local/libexec/arch-manager
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 "$SOURCE_HELPER" "$INSTALLED_HELPER"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0644 "$SOURCE_POLICY" "$INSTALLED_POLICY"
if ! /usr/bin/sudo /usr/bin/test -d /etc/polkit-1/rules.d; then
    /usr/bin/sudo /usr/bin/install -d -o root -g polkitd -m 0750 /etc/polkit-1/rules.d
fi
/usr/bin/sudo /usr/bin/install -o root -g polkitd -m 0640 "$rule_tmp" "$INSTALLED_RULE"

[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_HELPER")" == 'root:root 755' ]] || fail 'installed helper ownership or mode is incorrect'
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_POLICY")" == 'root:root 644' ]] || fail 'installed policy ownership or mode is incorrect'
[[ "$(/usr/bin/sudo /usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_RULE")" == 'root:polkitd 640' ]] || fail 'installed Polkit rule ownership or mode is incorrect'
/usr/bin/cmp -s "$SOURCE_HELPER" "$INSTALLED_HELPER" || fail 'installed helper does not match source'
/usr/bin/cmp -s "$SOURCE_POLICY" "$INSTALLED_POLICY" || fail 'installed policy does not match source'
"$INSTALLED_HELPER" --self-test >/dev/null || fail 'installed helper self-test failed'

registered=0
for (( attempt = 0; attempt < 50; attempt++ )); do
    if /usr/bin/pkaction --action-id "$ACTION_ID" >/dev/null 2>&1; then registered=1; break; fi
    /usr/bin/sleep 0.1
done
(( registered == 1 )) || fail 'Polkit did not register the Arch Manager diagnostics action'

printf 'Installed helper: %s\n' "$INSTALLED_HELPER"
printf 'Installed Polkit action: %s\n' "$ACTION_ID"
printf 'Authorized desktop user: %s\n' "$TARGET_USER"
