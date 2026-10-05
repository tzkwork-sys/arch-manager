"""Narrow client for the root-owned shared Recovery helper."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
import re
import signal

from .recovery import RecoveryRequest, RecoveryValidationError


PKEXEC_PATH = Path("/usr/bin/pkexec")
HELPER_PATH = Path("/usr/local/libexec/arch-manager/manage-recovery")


class RecoveryExecutionError(RuntimeError):
    pass


class RecoveryHelperUnavailable(RecoveryExecutionError):
    pass


class RecoveryAuthorizationCancelled(RecoveryExecutionError):
    pass


class RecoveryPreparationFailed(RecoveryExecutionError):
    pass


class RecoveryStalePreparation(RecoveryPreparationFailed):
    pass


class RecoveryUsbFailed(RecoveryExecutionError):
    pass


_BROKEN_ROOT_RE = re.compile(r"^@\.broken-\d{8}-\d{6}$")
_USB_DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9._+-]+$")
_USB_IDENTITY_RE = re.compile(r"^k1:[0-9a-f]{64}$")


@dataclass(frozen=True)
class RecoveryExecutionResult:
    action: str
    restore_point_id: int | None
    code: str




@dataclass(frozen=True)
class RecoveryUsbExecutionResult:
    action: str
    device: str
    code: str


@dataclass(frozen=True)
class BrokenRootsDeletionResult:
    deleted: tuple[str, ...]


_ERROR_MESSAGES = {
    "mkarchiso_missing": "Для создания среды восстановления требуется компонент archiso (mkarchiso).",
    "recovery_profile_invalid": "Профиль Recovery неполный: отсутствуют обязательные пакеты Archiso.",
    "iso_build_failed": "Не удалось собрать Recovery ISO. Подробный журнал: /var/log/arch-manager/recovery-build.log",
    "iso_missing": "Образ восстановления или файлы Recovery-профиля не найдены.",
    "invalid_btrfs_layout": "Схема Btrfs не подходит для безопасного восстановления.",
    "snapshot_not_found": "Выбранная точка восстановления больше не существует.",
    "insufficient_boot_space": "На /boot недостаточно свободного места для среды восстановления.",
    "boot_write_failed": "Не удалось записать файлы среды восстановления в /boot.",
    "boot_not_ready": "systemd-boot не готов для однократной загрузки восстановления.",
    "iso_filesystem_invalid": "Образ восстановления находится на неподходящей файловой системе.",
    "stale_preparation": "Среда восстановления устарела или была изменена. Arch Manager подготовит её заново.",
    "boot_entry_invalid": "Запись загрузчика Recovery не прошла проверку.",
    "helper_unavailable": "Системный helper восстановления недоступен.",
    "recovery_busy": "Другая операция Recovery уже выполняется. Дождитесь её завершения и повторите действие.",
    "broken_root_invalid": "Некорректная сохранённая копия прежней системы.",
    "broken_root_missing": "Сохранённая прежняя система уже отсутствует.",
    "broken_root_nested": "Внутри сохранённой системы обнаружен отдельный Btrfs-подтом. Автоматическое удаление остановлено для безопасности.",
    "broken_root_delete_failed": "Не удалось безопасно удалить сохранённую прежнюю систему.",
    "usb_device_invalid": "Выбранный накопитель не является доступной USB-флешкой целиком.",
    "usb_device_changed": "Выбранная флешка изменилась или была переподключена. Обновите список накопителей и выберите её заново.",
    "usb_identity_restart_required": "Arch Manager был обновлён во время работы. Полностью закройте приложение, откройте его заново и снова выберите флешку.",
    "usb_device_system_mount": "На выбранном накопителе обнаружен системный или служебный раздел. Запись остановлена для защиты данных.",
    "usb_device_swap": "На выбранном накопителе используется swap. Запись остановлена для защиты системы.",
    "usb_device_busy": "Не удалось безопасно отключить разделы выбранной флешки перед записью.",
    "usb_device_too_small": "Выбранная флешка меньше Recovery-образа.",
    "usb_write_failed": "Не удалось записать Recovery-образ на USB-флешку.",
    "usb_verify_failed": "Записанная флешка не прошла контрольную проверку.",
    "usb_image_mismatch": "На выбранной флешке нет актуальной среды Arch Manager Recovery. Создайте или обновите её.",
    "usb_uefi_unavailable": "Автоматическая загрузка с USB доступна только при загрузке системы в UEFI и установленном efibootmgr.",
    "usb_esp_missing": "На Recovery-флешке не найден EFI-загрузочный раздел.",
    "usb_boot_entry_failed": "Не удалось подготовить одноразовую UEFI-загрузку с Recovery-флешки. Подробности: /var/log/arch-manager/recovery-usb-boot.log",
}


def _helper_error_code(stdout: str) -> str | None:
    """Return the last recognized ERROR code even if helper output has noise."""
    for line in reversed((stdout or "").splitlines()):
        fields = line.strip().split("\t")
        if len(fields) == 2 and fields[0] == "ERROR" and fields[1] in _ERROR_MESSAGES:
            return fields[1]
    return None


def _unknown_helper_message(prefix: str, returncode: int, stderr: str) -> str:
    detail = " ".join((stderr or "").strip().split())
    if len(detail) > 240:
        detail = detail[:237] + "…"
    if detail:
        return f"{prefix} (код {returncode}): {detail}"
    return f"{prefix} (код {returncode})."


def _validate_optional_request(request: RecoveryRequest | None) -> None:
    """The normal GUI must not bind prepare/reboot to a snapshot."""
    if request is None:
        return
    if not isinstance(request, RecoveryRequest):
        raise TypeError("request must be RecoveryRequest or None")
    point_id = request.restore_point_id
    if not isinstance(point_id, int) or isinstance(point_id, bool) or not 1 <= point_id <= 999_999_999:
        raise RecoveryValidationError("Некорректный идентификатор точки восстановления.")
    raise RecoveryValidationError(
        "Выбор точки выполняется внутри Recovery-среды после перезагрузки."
    )


def _runtime_ready() -> None:
    if not PKEXEC_PATH.is_file() or not os.access(PKEXEC_PATH, os.X_OK):
        raise RecoveryHelperUnavailable("Не найден системный механизм авторизации Polkit.")
    try:
        if HELPER_PATH.is_symlink():
            raise RecoveryHelperUnavailable("Системный helper восстановления установлен небезопасно.")
        info = HELPER_PATH.stat()
    except FileNotFoundError as exc:
        raise RecoveryHelperUnavailable("Системный helper восстановления не установлен.") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or info.st_mode & 0o022
        or not info.st_mode & 0o111
    ):
        raise RecoveryHelperUnavailable("Системный helper восстановления имеет небезопасные права доступа.")


def recovery_helper_available() -> bool:
    """Read-only installation probe for the GUI; it never invokes Polkit."""
    try:
        _runtime_ready()
    except RecoveryHelperUnavailable:
        return False
    return True


def execute_recovery_action(
    action: str,
    request: RecoveryRequest | None = None,
    *,
    timeout: float = 1800,
) -> RecoveryExecutionResult:
    """Run one fixed shared-environment operation; no snapshot ID crosses this boundary."""
    if action not in {"prepare", "reboot", "cancel"}:
        raise RecoveryValidationError("Неподдерживаемое действие восстановления.")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    _validate_optional_request(request)
    _runtime_ready()

    command = (str(PKEXEC_PATH), "--user", "root", str(HELPER_PATH), action)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecoveryPreparationFailed("Операция восстановления не завершилась вовремя.") from exc
    except OSError as exc:
        raise RecoveryHelperUnavailable("Не удалось запустить системный helper восстановления.") from exc

    if completed.returncode == 126:
        raise RecoveryAuthorizationCancelled("Авторизация отменена.")

    fields = completed.stdout.strip().split("\t")
    if completed.returncode != 0:
        code = _helper_error_code(completed.stdout) or "unknown"
        message = _ERROR_MESSAGES.get(code) or _unknown_helper_message(
            "Операция восстановления завершилась с неизвестной ошибкой",
            completed.returncode,
            completed.stderr,
        )
        if code == "stale_preparation":
            raise RecoveryStalePreparation(message)
        raise RecoveryPreparationFailed(message)

    expected = "cancelled" if action == "cancel" else "prepared" if action == "prepare" else "rebooting"
    if fields != ["OK", expected]:
        raise RecoveryPreparationFailed("Privileged helper вернул неожиданный результат.")
    return RecoveryExecutionResult(action, None, expected)

def _validate_usb_device_argument(device: str) -> str:
    if not isinstance(device, str) or not _USB_DEVICE_RE.fullmatch(device):
        raise RecoveryValidationError("Некорректный путь к USB-устройству.")
    return device


def execute_recovery_usb_action(
    action: str,
    device: str,
    identity: str,
    *,
    timeout: float = 1800,
) -> RecoveryUsbExecutionResult:
    """Write a trusted Recovery ISO to a whole USB disk or boot it once via UEFI."""
    if action not in {"usb-create", "usb-reboot"}:
        raise RecoveryValidationError("Неподдерживаемое действие USB Recovery.")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    device = _validate_usb_device_argument(device)
    if not isinstance(identity, str) or not _USB_IDENTITY_RE.fullmatch(identity):
        raise RecoveryValidationError("Некорректная идентификация USB-устройства.")
    _runtime_ready()

    command = (str(PKEXEC_PATH), "--user", "root", str(HELPER_PATH), action, device, identity)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        # Ask the whole session to stop, but never block the GUI forever if a
        # privileged descendant cannot be signalled by the desktop process.
        stopped = False
        if process is not None:
            for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
                try:
                    os.killpg(process.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    process.communicate(timeout=grace)
                    stopped = True
                    break
                except subprocess.TimeoutExpired:
                    continue
        if stopped:
            message = "Операция с Recovery-флешкой превысила лимит времени и была остановлена."
        else:
            message = (
                "Операция с Recovery-флешкой превысила лимит времени. Не удалось подтвердить "
                "остановку системного helper; не отключайте флешку до завершения активности устройства."
            )
        raise RecoveryUsbFailed(message) from exc
    except OSError as exc:
        raise RecoveryHelperUnavailable("Не удалось запустить системный helper восстановления.") from exc

    if completed.returncode == 126:
        raise RecoveryAuthorizationCancelled("Авторизация отменена.")

    fields = completed.stdout.strip().split("\t")
    if completed.returncode != 0:
        code = _helper_error_code(completed.stdout) or "unknown"
        raise RecoveryUsbFailed(
            _ERROR_MESSAGES.get(code)
            or _unknown_helper_message(
                "Операция с Recovery-флешкой завершилась с неизвестной ошибкой",
                completed.returncode,
                completed.stderr,
            )
        )

    expected = "usb-written" if action == "usb-create" else "usb-rebooting"
    if fields != ["OK", expected, device]:
        raise RecoveryUsbFailed("Privileged helper вернул неожиданный результат.")
    return RecoveryUsbExecutionResult(action, device, expected)


def delete_broken_roots(
    names: tuple[str, ...] | list[str],
    *,
    timeout: float = 600,
) -> BrokenRootsDeletionResult:
    """Delete only explicitly named top-level @.broken-* safety copies."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    cleaned: list[str] = []
    for raw in names:
        if not isinstance(raw, str) or not _BROKEN_ROOT_RE.fullmatch(raw):
            raise RecoveryValidationError("Некорректное имя сохранённой прежней системы.")
        if raw not in cleaned:
            cleaned.append(raw)
    if not cleaned or len(cleaned) > 20:
        raise RecoveryValidationError("Выберите от одной до двадцати сохранённых систем.")
    _runtime_ready()

    command = (
        str(PKEXEC_PATH),
        "--user",
        "root",
        str(HELPER_PATH),
        "delete-broken",
        *cleaned,
    )
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecoveryPreparationFailed(
            "Удаление сохранённой прежней системы не завершилось вовремя."
        ) from exc
    except OSError as exc:
        raise RecoveryHelperUnavailable(
            "Не удалось запустить системный helper восстановления."
        ) from exc

    if completed.returncode == 126:
        raise RecoveryAuthorizationCancelled("Авторизация отменена.")

    fields = completed.stdout.strip().split("\t")
    if completed.returncode != 0:
        code = _helper_error_code(completed.stdout) or "unknown"
        raise RecoveryPreparationFailed(
            _ERROR_MESSAGES.get(code)
            or _unknown_helper_message(
                "Не удалось удалить сохранённую прежнюю систему",
                completed.returncode,
                completed.stderr,
            )
        )

    if len(fields) != 3 or fields[:2] != ["OK", "deleted-broken"]:
        raise RecoveryPreparationFailed("Privileged helper вернул неожиданный результат.")
    deleted = tuple(value for value in fields[2].split(",") if value)
    if deleted != tuple(cleaned):
        raise RecoveryPreparationFailed("Privileged helper подтвердил неожиданный набор удалённых систем.")
    return BrokenRootsDeletionResult(deleted)

