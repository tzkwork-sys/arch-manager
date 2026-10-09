#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'Arch Manager Recovery helper installer: %s\n' "$*" >&2; exit 2; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_HELPER="$PROJECT_DIR/src/privileged/manage_recovery.sh"
SOURCE_READER="$PROJECT_DIR/src/privileged/read_recovery_state.sh"
SOURCE_RECOVERY="$PROJECT_DIR/recovery/engine/arch-recovery.sh"
SOURCE_HELPER_LIB="$PROJECT_DIR/src/privileged/recovery_lib"
SOURCE_PROFILE="$PROJECT_DIR/recovery/archiso"
SOURCE_POLICY="$PROJECT_DIR/packaging/polkit/org.archmanager.manage-recovery.policy"
HELPER='/usr/local/libexec/arch-manager/manage-recovery'
HELPER_LIB='/usr/local/libexec/arch-manager/recovery_lib'
READ_HELPER='/usr/lib/arch-manager/read-recovery-state'
RECOVERY='/usr/local/libexec/arch-manager/arch-recovery.sh'
RECOVERY_RESOURCES='/usr/local/share/arch-manager/recovery'
RECOVERY_STAGE="/usr/local/share/arch-manager/.recovery.stage.$$"
HELPER_LIB_STAGE="/usr/local/libexec/arch-manager/.recovery_lib.stage.$$"
HELPER_LIB_BACKUP=""
RECOVERY_BACKUP=""
install_complete=0
POLICY='/usr/share/polkit-1/actions/org.archmanager.manage-recovery.policy'
RULE='/etc/polkit-1/rules.d/00-arch-manager-recovery.rules'
ACTION='org.archmanager.manage-recovery'

for command_path in /usr/bin/bash /usr/bin/chmod /usr/bin/chown /usr/bin/cmp /usr/bin/cp /usr/bin/flock /usr/bin/getent /usr/bin/id /usr/bin/install /usr/bin/mktemp /usr/bin/mv /usr/bin/pkaction /usr/bin/python /usr/bin/rm /usr/bin/sleep /usr/bin/stat /usr/bin/sudo /usr/bin/visudo; do
    [[ -x "$command_path" ]] || fail "required command is missing: $command_path"
done
for source in "$SOURCE_HELPER" "$SOURCE_READER" "$SOURCE_RECOVERY" "$SOURCE_POLICY"; do [[ -f "$source" && ! -L "$source" ]] || fail "source is missing or is a symlink: $source"; done
[[ -d "$SOURCE_HELPER_LIB" && ! -L "$SOURCE_HELPER_LIB" ]] || fail "Recovery helper module directory is missing"
for module in common.sh iso.sh usb.sh local.sh; do [[ -f "$SOURCE_HELPER_LIB/$module" && ! -L "$SOURCE_HELPER_LIB/$module" ]] || fail "Recovery helper module is missing: $module"; done
[[ -d "$SOURCE_PROFILE" && ! -L "$SOURCE_PROFILE" ]] || fail 'minimal Recovery profile is missing'
if [[ ! -x /usr/bin/efibootmgr ]]; then
    printf 'Note: /usr/bin/efibootmgr is not installed; USB creation will work, but one-click USB BootNext will stay unavailable.\n' >&2
fi
/usr/bin/bash -n "$SOURCE_HELPER" || fail 'helper syntax is invalid'
for module in common.sh iso.sh usb.sh local.sh; do /usr/bin/bash -n "$SOURCE_HELPER_LIB/$module" || fail "helper module syntax is invalid: $module"; done
/usr/bin/bash -n "$SOURCE_READER" || fail 'read helper syntax is invalid'
/usr/bin/bash -n "$SOURCE_RECOVERY" || fail 'Recovery script syntax is invalid'
"$SOURCE_HELPER" --self-test >/dev/null || fail 'helper self-test failed'
/usr/bin/getent group polkitd >/dev/null || fail 'polkitd group is missing'
if (( EUID == 0 )); then TARGET_USER=${SUDO_USER:-}; [[ -n "$TARGET_USER" && "$TARGET_USER" != root ]] || fail 'run as desktop user, not root'; else TARGET_USER=$(/usr/bin/id -un); fi
/usr/bin/getent passwd "$TARGET_USER" >/dev/null || fail 'desktop user does not exist'
[[ "$TARGET_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || fail 'unsupported desktop user name'
TARGET_UID=$(/usr/bin/id -u "$TARGET_USER")
SUDOERS="/etc/sudoers.d/arch-manager-read-recovery-${TARGET_UID}"
TARGET_JSON=$(/usr/bin/python -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TARGET_USER")
rule_tmp=$(/usr/bin/mktemp)
sudoers_tmp=$(/usr/bin/mktemp)
cleanup_install() {
    local rc=$?
    /usr/bin/rm -f -- "$rule_tmp" "$sudoers_tmp"
    if (( install_complete == 0 )); then
        # Never ask for another password during error cleanup. If authorization
        # expired, leave the root-owned backups for administrator recovery.
        for pair in "$HELPER_LIB_BACKUP|$HELPER_LIB" "$RECOVERY_BACKUP|$RECOVERY_RESOURCES"; do
            backup=${pair%%|*}; target=${pair#*|}
            [[ -n "$backup" ]] || continue
            if /usr/bin/sudo -n /usr/bin/test -d "$backup"; then
                /usr/bin/sudo -n /usr/bin/rm -rf -- "$target" \
                    && /usr/bin/sudo -n /usr/bin/mv -- "$backup" "$target" \
                    || printf 'Recovery rollback incomplete; backup preserved at %s\n' "$backup" >&2
            fi
        done
    fi
    return "$rc"
}
trap cleanup_install EXIT
trap 'exit 130' HUP INT TERM
cat > "$rule_tmp" <<RULE
polkit.addRule(function(action, subject) {
    if (action.id != "${ACTION}") return polkit.Result.NOT_HANDLED;
    if (action.lookup("program") != "${HELPER}" || action.lookup("user") != "root") return polkit.Result.NO;
    if (subject.user == ${TARGET_JSON} && subject.local == true && subject.active == true) return polkit.Result.AUTH_ADMIN;
    return polkit.Result.NO;
});
RULE
printf '%s ALL=(root) NOPASSWD: %s\n' "$TARGET_USER" "$READ_HELPER" > "$sudoers_tmp"
/usr/bin/chmod 0440 "$sudoers_tmp"
/usr/bin/visudo -cf "$sudoers_tmp" >/dev/null || fail 'generated Recovery read rule is invalid'
/usr/bin/sudo -v
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /usr/local/libexec/arch-manager
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 "$HELPER_LIB_STAGE"
for module in common.sh iso.sh usb.sh local.sh; do /usr/bin/sudo /usr/bin/install -o root -g root -m 0644 "$SOURCE_HELPER_LIB/$module" "$HELPER_LIB_STAGE/$module"; done
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /usr/lib/arch-manager
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /usr/local/share/arch-manager
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 "$RECOVERY_STAGE"
/usr/bin/sudo /usr/bin/cp -a -- "$SOURCE_PROFILE" "$RECOVERY_STAGE/archiso"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 "$SOURCE_RECOVERY" "$RECOVERY_STAGE/arch-manager-recovery"
/usr/bin/sudo /usr/bin/chown -R root:root "$RECOVERY_STAGE"
/usr/bin/sudo /usr/bin/chmod -R go-w "$RECOVERY_STAGE"
if /usr/bin/sudo /usr/bin/test -e "$HELPER_LIB"; then
    HELPER_LIB_BACKUP="${HELPER_LIB}.previous.$(/usr/bin/date +%s).$$"
    /usr/bin/sudo /usr/bin/mv -- "$HELPER_LIB" "$HELPER_LIB_BACKUP"
fi
/usr/bin/sudo /usr/bin/mv -- "$HELPER_LIB_STAGE" "$HELPER_LIB"
if /usr/bin/sudo /usr/bin/test -e "$RECOVERY_RESOURCES"; then
    RECOVERY_BACKUP="${RECOVERY_RESOURCES}.previous.$(/usr/bin/date +%s).$$"
    /usr/bin/sudo /usr/bin/mv -- "$RECOVERY_RESOURCES" "$RECOVERY_BACKUP"
fi
/usr/bin/sudo /usr/bin/mv -- "$RECOVERY_STAGE" "$RECOVERY_RESOURCES"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 "$SOURCE_HELPER" "$HELPER"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 "$SOURCE_READER" "$READ_HELPER"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 "$SOURCE_RECOVERY" "$RECOVERY"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0644 "$SOURCE_POLICY" "$POLICY"
if ! /usr/bin/sudo /usr/bin/test -d /etc/polkit-1/rules.d; then /usr/bin/sudo /usr/bin/install -d -o root -g polkitd -m 0750 /etc/polkit-1/rules.d; fi
/usr/bin/sudo /usr/bin/install -o root -g polkitd -m 0640 "$rule_tmp" "$RULE"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0440 "$sudoers_tmp" "$SUDOERS"
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$HELPER")" == root:root\ 755 ]] || fail 'helper ownership or mode is incorrect'
for module in common.sh iso.sh usb.sh local.sh; do [[ "$(/usr/bin/stat -Lc '%U:%G %a' "$HELPER_LIB/$module")" == root:root\ 644 ]] || fail "helper module ownership or mode is incorrect: $module"; done
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$READ_HELPER")" == root:root\ 755 ]] || fail 'read helper ownership or mode is incorrect'
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$RECOVERY")" == root:root\ 755 ]] || fail 'Recovery script ownership or mode is incorrect'
[[ "$(/usr/bin/stat -Lc '%U:%G %a' "$RECOVERY_RESOURCES/arch-manager-recovery")" == root:root\ 755 ]] || fail 'Recovery resources ownership or mode is incorrect'
/usr/bin/cmp -s "$SOURCE_HELPER" "$HELPER" && /usr/bin/cmp -s "$SOURCE_READER" "$READ_HELPER" && /usr/bin/cmp -s "$SOURCE_RECOVERY" "$RECOVERY" && /usr/bin/cmp -s "$SOURCE_POLICY" "$POLICY" || fail 'installed files do not match sources'
for module in common.sh iso.sh usb.sh local.sh; do /usr/bin/cmp -s "$SOURCE_HELPER_LIB/$module" "$HELPER_LIB/$module" || fail "installed helper module does not match source: $module"; done
"$HELPER" --self-test >/dev/null || fail 'installed helper self-test failed'
/usr/bin/sudo -n "$READ_HELPER" >/dev/null || fail 'installed read-only Recovery probe could not be used without prompting'
registered=0
for (( attempt = 0; attempt < 50; attempt++ )); do
    if /usr/bin/pkaction --action-id "$ACTION" >/dev/null 2>&1; then registered=1; break; fi
    /usr/bin/sleep 0.1
done
(( registered == 1 )) || fail 'Polkit did not register the Recovery action'
install_complete=1
printf 'Previous Recovery directory backups (if any): %s %s\n' "$HELPER_LIB_BACKUP" "$RECOVERY_BACKUP"
printf 'Installed Recovery helper: %s\nInstalled Recovery read probe: %s\nAuthorized desktop user: %s\n' "$HELPER" "$READ_HELPER" "$TARGET_USER"
