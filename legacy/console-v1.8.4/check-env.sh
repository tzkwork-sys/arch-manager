#!/usr/bin/env bash
set -u

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

ok()   { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
miss() { printf '[MISS] %s\n' "$*"; }

printf '===== Arch Manager v1.8.4 — проверка окружения =====\n\n'

if [[ -f /etc/arch-release ]]; then
    ok "Arch Linux обнаружен"
else
    warn "Файл /etc/arch-release не найден"
fi

printf '\n-- Команды --\n'
for cmd in bash sudo pacman dialog checkupdates paccache pacdiff yay konsole systemctl journalctl xdg-user-dir plasmashell findmnt blkid bootctl bsdtar btrfs snapper; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=""
        case "$cmd" in
            pacman) ver=$(pacman --version 2>/dev/null | sed -n '2p' | xargs) ;;
            dialog) ver=$(dialog --version 2>/dev/null | head -n1) ;;
            yay) ver=$(yay --version 2>/dev/null | head -n1) ;;
            konsole) ver=$(konsole --version 2>/dev/null | head -n1) ;;
            plasmashell) ver=$(plasmashell --version 2>/dev/null | head -n1) ;;
            btrfs) ver=$(btrfs version 2>/dev/null | head -n1) ;;
            snapper) ver=$(snapper --version 2>/dev/null | head -n1) ;;
        esac
        ok "$cmd${ver:+ — $ver}"
    else
        miss "$cmd"
    fi
done

printf '\n-- Пакеты --\n'
for pkg in dialog pacman-contrib konsole xdg-user-dirs libarchive btrfs-progs snapper; do
    if pacman -Q "$pkg" >/dev/null 2>&1; then
        ok "$(pacman -Q "$pkg")"
    else
        miss "$pkg"
    fi
done

if pacman -Q packagekit >/dev/null 2>&1 || pacman -Q packagekit-qt6 >/dev/null 2>&1; then
    warn "PackageKit установлен, но Arch Manager его использовать не будет"
else
    ok "PackageKit не установлен"
fi

printf '\n-- Btrfs / Snapper --\n'
root_fs=$(findmnt -no FSTYPE / 2>/dev/null || true)
root_src=$(findmnt -no SOURCE / 2>/dev/null || true)
root_opts=$(findmnt -no OPTIONS / 2>/dev/null || true)
printf 'Root source: %s\n' "${root_src:-не определён}"
printf 'Root fs:     %s\n' "${root_fs:-не определена}"
printf 'Root opts:   %s\n' "${root_opts:-не определены}"

root_subvol=""
if [[ "$root_src" == *"["*"]"* ]]; then
    root_subvol="${root_src#*[}"
    root_subvol="${root_subvol%]}"
    root_subvol="${root_subvol#/}"
fi
printf 'Root subvol: %s\n' "${root_subvol:-не определён}"

snap_mount=$(findmnt -rn -M /.snapshots -o SOURCE 2>/dev/null || true)
printf 'Snapshots:   %s\n' "${snap_mount:-не смонтировано}"

if [[ "$root_fs" == "btrfs" ]]; then
    ok "Корневая файловая система — Btrfs"
else
    warn "Корень / не Btrfs — снапшоты Snapper для / недоступны"
fi

if [[ "$root_fs" == "btrfs" && "$root_subvol" == "@" ]]; then
    ok "Схема / → @ совместима с отдельным @snapshots"
elif [[ "$root_fs" == "btrfs" ]]; then
    warn "Корневой subvolume не @ — автоматическая разметка @snapshots будет отключена"
fi

if [[ "$snap_mount" == *"[/@snapshots]"* ]]; then
    ok "Отдельный @snapshots смонтирован в /.snapshots"
elif [[ -n "$snap_mount" ]]; then
    warn "В /.snapshots смонтирован другой источник: $snap_mount"
else
    warn "Отдельный @snapshots ещё не настроен"
fi

if [[ -f /etc/snapper/configs/root ]]; then
    ok "Snapper root-конфигурация найдена"
else
    warn "Snapper root-конфигурация ещё не создана"
fi

if systemctl is-enabled --quiet snapper-timeline.timer 2>/dev/null; then
    ok "snapper-timeline.timer включён"
else
    warn "snapper-timeline.timer выключен"
fi

if systemctl is-enabled --quiet snapper-cleanup.timer 2>/dev/null; then
    ok "snapper-cleanup.timer включён"
else
    warn "snapper-cleanup.timer выключен"
fi

printf '\n-- Пути --\n'
printf 'Project: %s\n' "$APP_DIR"
if [[ -d /data/Projects ]]; then
    [[ -w /data/Projects ]] && ok "/data/Projects доступен на запись" || warn "/data/Projects существует, но не доступен на запись"
else
    warn "/data/Projects пока не существует"
fi

if command -v xdg-user-dir >/dev/null 2>&1; then
    desktop=$(xdg-user-dir DESKTOP 2>/dev/null || true)
    printf 'Desktop: %s\n' "${desktop:-не определён}"
fi

printf '\n-- Итог --\n'
missing=()
command -v dialog >/dev/null 2>&1 || missing+=(dialog)
command -v checkupdates >/dev/null 2>&1 || missing+=(pacman-contrib)
command -v konsole >/dev/null 2>&1 || missing+=(konsole)
command -v xdg-user-dir >/dev/null 2>&1 || missing+=(xdg-user-dirs)
command -v bsdtar >/dev/null 2>&1 || missing+=(libarchive)
command -v btrfs >/dev/null 2>&1 || missing+=(btrfs-progs)
command -v snapper >/dev/null 2>&1 || missing+=(snapper)

if ((${#missing[@]} == 0)); then
    ok "Все официальные зависимости Arch Manager установлены"
else
    printf 'Нужно установить: %s\n' "${missing[*]}"
    printf 'Команда: sudo pacman -S --needed %s\n' "${missing[*]}"
fi

if command -v yay >/dev/null 2>&1; then
    ok "yay найден — AUR-функции доступны"
else
    warn "yay не найден — AUR-функции недоступны до его установки"
fi

printf '\n-- Аварийный помощник --\n'
helper="$APP_DIR/arch-recovery.sh"
if [[ -f "$helper" ]]; then
    if bash -n "$helper" 2>/dev/null; then
        ok "arch-recovery.sh найден и прошёл проверку Bash"
    else
        warn "arch-recovery.sh найден, но содержит синтаксическую ошибку"
    fi
else
    miss "arch-recovery.sh в папке проекта"
fi

if [[ -f /data/RESTORE-ARCH.sh ]]; then
    if [[ -f "$helper" ]] && cmp -s "$helper" /data/RESTORE-ARCH.sh 2>/dev/null; then
        ok "/data/RESTORE-ARCH.sh — актуальная копия"
    else
        warn "/data/RESTORE-ARCH.sh существует, но отличается от файла проекта"
    fi
else
    warn "/data/RESTORE-ARCH.sh ещё не подготовлен"
fi

if [[ -f /boot/RESTORE-ARCH.sh ]]; then
    if [[ -f "$helper" ]] && cmp -s "$helper" /boot/RESTORE-ARCH.sh 2>/dev/null; then
        ok "/boot/RESTORE-ARCH.sh — актуальная копия"
    else
        warn "/boot/RESTORE-ARCH.sh существует, но отличается от файла проекта"
    fi
else
    warn "/boot/RESTORE-ARCH.sh ещё не подготовлен"
fi


printf '\n-- Локальное восстановление без USB --\n'
boot_status=""
current_entry=""
if command -v bootctl >/dev/null 2>&1; then
    # Обычному пользователю содержимое /boot может быть закрыто. Не считаем
    # это отсутствием systemd-boot: сначала читаем сведения о текущем EFI loader.
    boot_status=$(SYSTEMD_PAGER=cat bootctl --no-pager status 2>/dev/null || true)
    current_entry=$(sed -n 's/^[[:space:]]*Current Entry:[[:space:]]*//p' <<<"$boot_status" | head -n1)

    if grep -qE '^[[:space:]]*Product:[[:space:]]+systemd-boot([[:space:]]|$)' <<<"$boot_status"; then
        ok "systemd-boot обнаружен по текущему EFI-загрузчику"
    elif sudo -n true 2>/dev/null && sudo bootctl --esp-path=/boot is-installed >/dev/null 2>&1; then
        ok "systemd-boot подтверждён на ESP /boot с правами администратора"
    else
        warn "systemd-boot не удалось подтвердить без запроса sudo; Arch Manager перепроверит его с root-правами перед восстановлением"
    fi
else
    miss "bootctl"
fi

if [[ -n "$current_entry" ]]; then
    printf 'Current entry: %s\n' "$current_entry"
    if [[ "$current_entry" == *.efi ]]; then
        ok "Текущая обычная загрузка использует UKI; временная Type #1 запись Recovery с ней совместима"
    fi
fi

if [[ -d /boot/loader/entries ]] || (sudo -n true 2>/dev/null && sudo test -d /boot/loader/entries); then
    ok "каталог записей systemd-boot найден: /boot/loader/entries"
else
    ok "/boot/loader/entries при необходимости будет создан автоматически с root-правами"
fi

if [[ -f /data/Arch-Recovery/Arch-Manager-Recovery.iso ]]; then
    iso_dev=$(findmnt -rn -T /data/Arch-Recovery/Arch-Manager-Recovery.iso -o SOURCE 2>/dev/null || true)
    iso_dev="${iso_dev%%[*}"
    root_dev="${root_src%%[*}"
    if [[ -n "$iso_dev" && -n "$root_dev" && "$(readlink -f "$iso_dev" 2>/dev/null)" != "$(readlink -f "$root_dev" 2>/dev/null)" ]]; then
        ok "Recovery ISO лежит вне системного Btrfs-раздела: $iso_dev"
    else
        warn "Recovery ISO должен лежать на отдельном от корня разделе (рекомендуется /data)"
    fi
else
    warn "Recovery ISO ещё не создан; локальный режим сможет создать его при первом запуске"
fi

printf '\n-- Аварийная флешка --\n'
if command -v mkarchiso >/dev/null 2>&1; then
    ok "archiso установлен — собственную аварийную флешку можно создавать"
    releng="/usr/share/archiso/configs/releng"
    if [[ -f "$releng/profiledef.sh" && -f "$releng/pacman.conf" && -d "$releng/efiboot" && -d "$releng/syslinux" ]]; then
        if bash -n "$releng/profiledef.sh" 2>/dev/null; then
            ok "официальный профиль archiso releng найден и прошёл проверку Bash"
        else
            warn "profiledef.sh официального профиля releng содержит синтаксическую ошибку"
        fi
    else
        warn "официальный профиль archiso releng неполон — рекомендуется переустановить archiso"
    fi
else
    warn "archiso не установлен — он понадобится только при создании аварийной флешки"
fi

if [[ -f /data/Arch-Recovery/Arch-Manager-Recovery.iso ]]; then
    ok "аварийный загрузочный образ уже создан: /data/Arch-Recovery/Arch-Manager-Recovery.iso"
else
    warn "аварийный загрузочный образ ещё не создан"
fi

if command -v blkid >/dev/null 2>&1 && blkid -t LABEL=ARCH_MANAGER_RECOVERY -o device 2>/dev/null | grep -q .; then
    ok "подключённая аварийная флешка Arch Manager обнаружена"
else
    warn "аварийная флешка с меткой ARCH_MANAGER_RECOVERY сейчас не подключена"
fi
