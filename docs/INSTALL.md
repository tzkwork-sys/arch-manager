# Installation — 0.7.0-beta.1

This is a preliminary release for Arch Linux x86_64. KDE Plasma is the primary
desktop tested during development. Do not run the GUI as root.

## Dependencies

Install official dependencies with a **full system upgrade**, not `pacman -Sy`:

```sh
sudo pacman -Syu --needed python pyside6 sudo polkit pacman-contrib appstream
```

Snapper/Btrfs functionality additionally needs `snapper` and `btrfs-progs` with
an existing root Snapper configuration. Recovery builds require `archiso`;
one-click USB boot additionally requires `efibootmgr`. SMART diagnostics use
`smartmontools` and `nvme-cli`. AUR functionality is optional and requires `yay`
installed separately. A graphical Polkit authentication agent and a supported
terminal (for example Konsole) must be available.

## Application

Extract the release sources, open a terminal in that directory, and run:

```sh
bash scripts/install.sh
```

The installer copies the application into a new directory under
`~/.local/share/arch-manager/versions` (or `$XDG_DATA_HOME`). The launcher is
`~/.local/bin/arch-manager-gui`. The original download can then be removed.
Ensure `~/.local/bin` is in PATH. Re-running installs a new copy and switches
the launcher; previous copies are retained. Do not install while a GUI instance
or package/Recovery operation is running.

## Optional system components

From the installed directory printed by the installer, enable only what you need:

```sh
bash scripts/install-app-store-helper.sh
bash scripts/install-stage6-helper.sh
bash scripts/setup-restore-points-read-access.sh "$(id -u)"
bash scripts/install-stage4-helper.sh
bash scripts/install-system-diagnostics-helper.sh
# Only on a compatible Recovery configuration:
bash scripts/install-stage7-recovery-helper.sh
```

These scripts request administrator authorization and install fixed root-owned
helpers, Polkit rules and narrowly scoped read-only sudo permissions. Ordinary
application installation does not enable Recovery or reboot the computer.
When updating, update enabled helpers from the same release as the GUI.
Recovery directory backups are retained with `.previous.*` names. Interrupted
updates may require administrator repair; do not reboot into Recovery until its
readiness checks pass. Close all instances before updating helpers.

## Recovery limitations

Local Recovery currently requires Btrfs root `@`, `@snapshots` mounted at
`/.snapshots`, systemd-boot with ESP at `/boot`, and `/data/Arch-Recovery` on a
different block device from the root. GRUB, ext4, encrypted layouts and Secure
Boot are **not claimed as supported**. Opening the Recovery page may request
authorization and automatically build an ISO/write its boot files if prerequisites
are met. USB creation erases the selected drive. Keep an independent backup;
snapshots on the same disk are not a backup against disk failure.

## Removal

Close the GUI and finish all operations. Run the relevant `uninstall-*-helper.sh`
scripts from the installed copy **before** deleting it. Remove the user launcher
`~/.local/bin/arch-manager-gui`, desktop entry
`~/.local/share/applications/arch-manager-gui.desktop`, icon
`~/.local/share/icons/hicolor/scalable/apps/arch-manager-gui.svg`, and any
`Arch Manager.desktop` shortcut you created. Then remove installed application
copies. Caches/logs and snapshots are not automatically deleted. Read-only
Snapper permission rules, Recovery ISO/boot files and retained helper backups
require explicit administrator review; do not delete snapshots to uninstall.
