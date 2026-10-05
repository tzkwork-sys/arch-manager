#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'Arch Manager Stage 4 helper installer: %s\n' "$*" >&2
    exit 2
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_HELPER="$PROJECT_DIR/src/privileged/manage_restore_points.sh"
SOURCE_READ_HELPER="$PROJECT_DIR/src/privileged/read_restore_points.sh"
SOURCE_POLICY="$PROJECT_DIR/packaging/polkit/org.archmanager.manage-restore-points.policy"

ACTION_ID='org.archmanager.manage-restore-points'
INSTALLED_HELPER='/usr/local/libexec/arch-manager/manage-restore-points'
INSTALLED_READ_HELPER='/usr/lib/arch-manager/read-restore-points'
INSTALLED_POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'
LEGACY_POLICY='/usr/local/share/polkit-1/actions/org.archmanager.manage-restore-points.policy'
INSTALLED_RULE='/etc/polkit-1/rules.d/00-arch-manager-restore-points.rules'

for command_path in \
    /usr/bin/bash \
    /usr/bin/cmp \
    /usr/bin/getent \
    /usr/bin/id \
    /usr/bin/install \
    /usr/bin/mktemp \
    /usr/bin/pkaction \
    /usr/bin/pkexec \
    /usr/bin/python \
    /usr/bin/rm \
    /usr/bin/stat \
    /usr/bin/sudo; do
    [[ -x "$command_path" ]] || fail "required command is missing: $command_path"
done

[[ -f "$SOURCE_HELPER" && ! -L "$SOURCE_HELPER" ]] || fail 'helper source is missing or is a symlink'
[[ -f "$SOURCE_READ_HELPER" && ! -L "$SOURCE_READ_HELPER" ]] || fail 'read helper source is missing or is a symlink'
[[ -f "$SOURCE_POLICY" && ! -L "$SOURCE_POLICY" ]] || fail 'Polkit policy source is missing or is a symlink'
/usr/bin/bash -n "$SOURCE_HELPER" || fail 'helper source has invalid shell syntax'
/usr/bin/bash -n "$SOURCE_READ_HELPER" || fail 'read helper source has invalid shell syntax'
"$SOURCE_HELPER" --self-test >/dev/null || fail 'helper source self-test failed'
"$SOURCE_READ_HELPER" --probe >/dev/null || fail 'read helper source probe failed'
/usr/bin/getent group polkitd >/dev/null || fail 'polkitd group is missing; is polkit installed?'

if (( EUID == 0 )); then
    TARGET_USER=${SUDO_USER:-}
    [[ -n "$TARGET_USER" && "$TARGET_USER" != root ]] \
        || fail 'run this installer as the desktop user, not directly as root'
else
    TARGET_USER=$(/usr/bin/id -un)
fi

/usr/bin/getent passwd "$TARGET_USER" >/dev/null || fail "desktop user does not exist: $TARGET_USER"
TARGET_USER_JSON=$(/usr/bin/python -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TARGET_USER")

rule_tmp=$(/usr/bin/mktemp)
trap 'rm -f -- "$rule_tmp"' EXIT

cat > "$rule_tmp" <<RULE
polkit.addRule(function(action, subject) {
    if (action.id != "${ACTION_ID}") {
        return polkit.Result.NOT_HANDLED;
    }

    if (action.lookup("program") != "${INSTALLED_HELPER}" ||
        action.lookup("user") != "root") {
        return polkit.Result.NO;
    }

    if (subject.user == ${TARGET_USER_JSON} &&
        subject.local == true &&
        subject.active == true) {
        return polkit.Result.AUTH_ADMIN;
    }

    return polkit.Result.NO;
});
RULE

# Ask for administrator authentication only for installation itself.
/usr/bin/sudo -v

/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 \
    /usr/local/libexec/arch-manager
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 \
    "$SOURCE_HELPER" "$INSTALLED_HELPER"

# Stage 3 may already have installed the narrow read-only helper and sudoers
# rule. Refresh only that existing helper; do not add a new sudoers grant
# from the Stage 4 installer.
read_helper_updated=0
if /usr/bin/sudo /usr/bin/test -f "$INSTALLED_READ_HELPER"; then
    /usr/bin/sudo /usr/bin/install -o root -g root -m 0755 \
        "$SOURCE_READ_HELPER" "$INSTALLED_READ_HELPER"
    read_helper_updated=1
fi

# Install the action in the canonical system action directory.  This path is
# supported by all relevant Polkit versions and is where Arch packages place
# their action definitions.  Remove the failed Stage 4.3 local-path copy.
/usr/bin/sudo /usr/bin/install -o root -g root -m 0644 \
    "$SOURCE_POLICY" "$INSTALLED_POLICY"
/usr/bin/sudo /usr/bin/rm -f -- "$LEGACY_POLICY"

if ! /usr/bin/sudo /usr/bin/test -d /etc/polkit-1/rules.d; then
    /usr/bin/sudo /usr/bin/install -d -o root -g polkitd -m 0750 \
        /etc/polkit-1/rules.d
fi
/usr/bin/sudo /usr/bin/install -o root -g polkitd -m 0640 \
    "$rule_tmp" "$INSTALLED_RULE"

[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_HELPER")" == 'root:root 755' ]] \
    || fail 'installed helper ownership or mode is incorrect'
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_POLICY")" == 'root:root 644' ]] \
    || fail 'installed Polkit policy ownership or mode is incorrect'
[[ "$(/usr/bin/sudo /usr/bin/stat -Lc '%U:%G %a' "$INSTALLED_RULE")" == 'root:polkitd 640' ]] \
    || fail 'installed Polkit rule ownership or mode is incorrect'

/usr/bin/cmp -s "$SOURCE_HELPER" "$INSTALLED_HELPER" \
    || fail 'installed helper does not match the project source'
if (( read_helper_updated == 1 )); then
    /usr/bin/cmp -s "$SOURCE_READ_HELPER" "$INSTALLED_READ_HELPER" \
        || fail 'installed read helper does not match the project source'
fi
/usr/bin/cmp -s "$SOURCE_POLICY" "$INSTALLED_POLICY" \
    || fail 'installed Polkit policy does not match the project source'
/usr/bin/sudo /usr/bin/cmp -s "$rule_tmp" "$INSTALLED_RULE" \
    || fail 'installed Polkit rule does not match the generated rule'

"$INSTALLED_HELPER" --self-test >/dev/null \
    || fail 'installed helper self-test failed'

# Do not invoke a privileged restore-point operation here. Merely verify that
# polkit has registered the custom action bound to the fixed helper path.
registered=0
for (( attempt = 0; attempt < 50; attempt++ )); do
    if /usr/bin/pkaction --action-id "$ACTION_ID" >/dev/null 2>&1; then
        registered=1
        break
    fi
    /usr/bin/sleep 0.1
done
(( registered == 1 )) || fail 'Polkit did not register the Arch Manager action'

printf 'Installed helper: %s\n' "$INSTALLED_HELPER"
if (( read_helper_updated == 1 )); then
    printf 'Updated read helper: %s\n' "$INSTALLED_READ_HELPER"
fi
printf 'Installed Polkit action: %s\n' "$ACTION_ID"
printf 'Authorized desktop user: %s\n' "$TARGET_USER"
printf 'Authentication mode: administrator password required for each action\n'
