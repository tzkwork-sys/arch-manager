#!/usr/bin/env bash
# shellcheck disable=SC2034
iso_name="arch-manager-recovery"
iso_label="AM_RECOVERY"
iso_publisher="Arch Manager"
iso_application="Arch Manager Recovery"
iso_version="15"
install_dir="arch"
buildmodes=('iso')
bootmodes=('bios.syslinux' 'uefi.systemd-boot')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-b' '1M')
file_permissions=(
  ["/root/customize_airootfs.sh"]="0:0:755"
  ["/usr/local/bin/arch-manager-recovery"]="0:0:755"
  ["/usr/local/libexec/arch-manager/recovery-bootlog"]="0:0:755"
  ["/usr/local/libexec/arch-manager/recovery-watchdog"]="0:0:755"
)
