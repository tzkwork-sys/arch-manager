"""Read-only Recovery readiness checks and the Stage 7 execution contract.

The normal Arch Manager GUI never chooses a snapshot for local Recovery.
Readiness describes one shared Recovery environment which is reusable for all
current and future snapshots until the trusted Recovery profile/engine changes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from datetime import datetime
from pathlib import Path
import re
from typing import Callable

from .command import CommandResult, run_command
from .restore_points import RestorePoint, collect_restore_points_list


LEGACY_STATE_DIR = Path("/.snapshots/.arch-manager-recovery")
PRIVILEGED_RECOVERY_READER = Path("/usr/lib/arch-manager/read-recovery-state")
LEGACY_ISO = Path("/data/Arch-Recovery/Arch-Manager-Recovery.iso")
LEGACY_LOCAL_FILES = (
    Path("/boot/arch-manager-local/vmlinuz-linux"),
    Path("/boot/arch-manager-local/initramfs-linux.img"),
    Path("/boot/loader/entries/arch-manager-recovery.conf"),
)
_BROKEN_ROOT_RE = re.compile(r"^@\.broken-\d{8}-\d{6}$")
_SAFE_MARKER_VALUE = re.compile(r"^[^\x00\r\n]{1,500}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RecoveryPaths:
    """Paths are injectable so checks are testable without a real Btrfs host."""

    snapshots: Path = Path("/.snapshots")
    legacy_state: Path = LEGACY_STATE_DIR
    legacy_iso: Path = LEGACY_ISO
    local_files: tuple[Path, ...] = LEGACY_LOCAL_FILES
    # Only use this when a caller has a safe, already-visible top-level view.
    # Recovery readiness never mounts subvolid=5 just to populate it.
    top_level: Path | None = None
    legacy_helper: Path = Path("/data/Arch-Recovery/restore.sh")
    state_reader: Path | None = PRIVILEGED_RECOVERY_READER


@dataclass(frozen=True)
class RecoveryIssue:
    code: str
    message: str
    technical: str = ""
    blocking: bool = True


@dataclass(frozen=True)
class LegacyRestoreRecord:
    marker_found: bool = False
    report_found: bool = False
    version: str | None = None
    restored_at: str | None = None
    snapshot_number: int | None = None
    backup_name: str | None = None
    report_path: Path | None = None


@dataclass(frozen=True)
class RecoveryReadiness:
    checked_at: datetime
    points: tuple[RestorePoint, ...]
    root_is_btrfs: bool | None
    root_layout_ok: bool | None
    snapshots_available: bool | None
    snapshots_layout_ok: bool | None
    systemd_boot_detected: bool | None
    iso_ready: bool | None
    boot_files_ready: bool | None
    environment_ready: bool | None
    legacy_environment_found: bool
    previous_restore: LegacyRestoreRecord
    broken_roots: tuple[str, ...]
    issues: tuple[RecoveryIssue, ...] = ()
    notes: tuple[str, ...] = ()
    state_fingerprint: str | None = None

    @property
    def can_prepare(self) -> bool:
        """Whether the shared environment may be prepared safely."""
        return not any(issue.blocking for issue in self.issues)

    @property
    def environment_prepared(self) -> bool:
        return self.environment_ready is True

    @property
    def has_restore_points(self) -> bool:
        return bool(self.points)

    @property
    def can_boot_recovery(self) -> bool:
        """The shared Recovery environment is bootable independently of snapshots."""
        return self.can_prepare and self.environment_prepared

    @property
    def ready(self) -> bool:
        """Current user-facing Recovery readiness."""
        return self.can_boot_recovery

    @property
    def partial(self) -> bool:
        return any(
            value is None
            for value in (
                self.root_is_btrfs,
                self.root_layout_ok,
                self.snapshots_available,
                self.snapshots_layout_ok,
                self.systemd_boot_detected,
                self.iso_ready,
                self.boot_files_ready,
                self.environment_ready,
            )
        )


class RecoveryValidationError(ValueError):
    """A Recovery request did not pass the fixed validation contract."""


@dataclass(frozen=True)
class RecoveryRequest:
    """Optional future preselection contract for the autonomous Recovery engine."""

    restore_point_id: int


@dataclass(frozen=True)
class RecoveryPlan:
    restore_point: RestorePoint
    procedure: tuple[str, ...]
    executor_action: str = "recovery-mode"


def _result_text(result: CommandResult) -> str:
    return "\n".join((result.stdout, result.stderr)).strip()


def _findmnt(field: str, target: str, runner: Callable[..., CommandResult]) -> str | None:
    result = runner(["findmnt", "-rn", "-o", field, "--target", target], timeout=8)
    if not result.ok:
        return None
    value = result.stdout.strip().splitlines()
    return value[0].strip() if value else ""


def _mount_subvolume(options: str | None, source: str | None) -> str | None:
    """Return the exact Btrfs subvolume from findmnt output, never a substring."""
    if options is None or source is None:
        return None
    for option in options.split(","):
        key, separator, value = option.strip().partition("=")
        if separator and key == "subvol":
            return value
    match = re.search(r"\[(/[^\]]+)\]$", source.strip())
    return match.group(1) if match else ""


def _root_is_at(options: str | None, source: str | None) -> bool | None:
    subvolume = _mount_subvolume(options, source)
    return None if subvolume is None else subvolume == "/@"


def _snapshots_is_separate(source: str | None, options: str | None) -> bool | None:
    subvolume = _mount_subvolume(options, source)
    return None if subvolume is None else subvolume == "/@snapshots"


def _entry_is_local_recovery(entry: Path) -> bool:
    """Compatibility fallback: identify a generic local Recovery boot entry."""
    try:
        lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    options = next((line[8:].strip() for line in lines if line.startswith("options ")), None)
    if not options:
        return False
    values = dict(token.split("=", 1) for token in options.split() if "=" in token)
    # Snapshot preselection is intentionally not a readiness criterion.
    return values.get("arch_manager_local") == "1"


def _parse_recovery_state(text: str) -> tuple[bool, bool, bool, bool] | None:
    """Parse the fixed v2 status fields from the root-owned read-only helper."""
    fields: dict[str, str] = {}
    allowed = {
        "version",
        "systemd_boot",
        "iso_ready",
        "boot_files_ready",
        "environment_ready",
    }
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in allowed:
            fields[key] = value.strip()

    if fields.get("version") != "2":
        return None
    status_keys = ("systemd_boot", "iso_ready", "boot_files_ready", "environment_ready")
    if any(fields.get(key) not in {"0", "1"} for key in status_keys):
        return None
    return tuple(fields[key] == "1" for key in status_keys)  # type: ignore[return-value]


def _parse_broken_roots(text: str) -> tuple[str, ...]:
    roots: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "broken_root" and _BROKEN_ROOT_RE.fullmatch(value.strip()):
            name = value.strip()
            if name not in roots:
                roots.append(name)
    return tuple(sorted(roots, reverse=True))


def _parse_recovery_fingerprint(text: str) -> str | None:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"version", "fingerprint"}:
            fields[key] = value.strip()
    fingerprint = fields.get("fingerprint", "")
    if fields.get("version") != "2" or not _FINGERPRINT_RE.fullmatch(fingerprint):
        return None
    return fingerprint


def _recovery_cache_path() -> Path:
    override = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".cache"
    return base / "arch-manager" / "recovery-state.json"


def _load_recovery_state_cache(
    fingerprint: str,
) -> tuple[tuple[bool, bool, bool, bool], tuple[str, ...]] | None:
    try:
        payload = json.loads(_recovery_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("version") != 2 or payload.get("fingerprint") != fingerprint:
        return None
    values = tuple(payload.get(key) for key in (
        "systemd_boot", "iso_ready", "boot_files_ready", "environment_ready"
    ))
    if any(type(value) is not bool for value in values):
        return None
    raw_roots = payload.get("broken_roots", [])
    if not isinstance(raw_roots, list) or any(
        not isinstance(value, str) or not _BROKEN_ROOT_RE.fullmatch(value)
        for value in raw_roots
    ):
        return None
    roots = tuple(sorted(dict.fromkeys(raw_roots), reverse=True))
    return values, roots  # type: ignore[return-value]


def _save_recovery_state_cache(
    fingerprint: str,
    status: tuple[bool, bool, bool, bool],
    broken_roots: tuple[str, ...],
) -> None:
    path = _recovery_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "fingerprint": fingerprint,
                    "systemd_boot": status[0],
                    "iso_ready": status[1],
                    "boot_files_ready": status[2],
                    "environment_ready": status[3],
                    "broken_roots": list(broken_roots),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass


def _read_privileged_recovery_state(
    paths: RecoveryPaths,
    runner: Callable[..., CommandResult],
) -> tuple[bool, bool, bool, bool, str | None, tuple[str, ...]] | None:
    """Read protected /boot state and the safe list of preserved old roots."""
    reader = paths.state_reader
    if reader is None or not reader.is_file():
        return None

    fingerprint: str | None = None
    fingerprint_result = runner(
        ["sudo", "-n", str(reader), "--fingerprint"], timeout=8
    )
    if fingerprint_result.ok:
        fingerprint = _parse_recovery_fingerprint(fingerprint_result.stdout)

    use_persistent_cache = reader == PRIVILEGED_RECOVERY_READER
    if fingerprint is not None and use_persistent_cache:
        cached = _load_recovery_state_cache(fingerprint)
        if cached is not None:
            status, broken_roots = cached
            return (*status, fingerprint, broken_roots)

    result = runner(["sudo", "-n", str(reader)], timeout=30)
    if not result.ok:
        return None
    status = _parse_recovery_state(result.stdout)
    if status is None:
        return None
    broken_roots = _parse_broken_roots(result.stdout)
    returned_fingerprint = _parse_recovery_fingerprint(result.stdout) or fingerprint
    if returned_fingerprint is not None and use_persistent_cache:
        _save_recovery_state_cache(returned_fingerprint, status, broken_roots)
    return (*status, returned_fingerprint, broken_roots)


def collect_recovery_fingerprint(
    *,
    paths: RecoveryPaths | None = None,
    runner: Callable[..., CommandResult] = run_command,
) -> str | None:
    """Return a cheap change token without hashing large Recovery artifacts."""
    paths = paths or RecoveryPaths()
    reader = paths.state_reader
    if reader is None or not reader.is_file():
        return None
    result = runner(["sudo", "-n", str(reader), "--fingerprint"], timeout=8)
    if not result.ok:
        return None
    return _parse_recovery_fingerprint(result.stdout)


def _parse_marker(path: Path) -> LegacyRestoreRecord:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LegacyRestoreRecord()
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"version", "date", "snapshot", "backup"} and _SAFE_MARKER_VALUE.match(value):
            fields[key] = value
    snapshot = int(fields["snapshot"]) if fields.get("snapshot", "").isdigit() else None
    backup = fields.get("backup")
    return LegacyRestoreRecord(True, False, fields.get("version"), fields.get("date"), snapshot, backup)


def _legacy_record(paths: RecoveryPaths) -> LegacyRestoreRecord:
    marker = paths.legacy_state / "last-restore.env"
    if marker.is_file():
        return _parse_marker(marker)
    try:
        reports = sorted(
            paths.legacy_state.glob("restore-*.txt"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        reports = []
    return LegacyRestoreRecord(report_found=bool(reports), report_path=reports[0] if reports else None)


def _broken_roots(paths: RecoveryPaths) -> tuple[str, ...]:
    if paths.top_level is None:
        return ()
    try:
        return tuple(
            sorted(entry.name for entry in paths.top_level.iterdir() if _BROKEN_ROOT_RE.fullmatch(entry.name))
        )
    except OSError:
        return ()


def collect_recovery_readiness(
    *,
    paths: RecoveryPaths | None = None,
    runner: Callable[..., CommandResult] = run_command,
    points_loader: Callable[[], object] = collect_restore_points_list,
    now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
) -> RecoveryReadiness:
    """Collect independent read-only facts; a failed probe remains a partial result."""
    paths = paths or RecoveryPaths()
    issues: list[RecoveryIssue] = []
    notes: list[str] = []

    try:
        points_result = points_loader()
        points = tuple(points_result.points)
        if not points:
            issues.append(
                RecoveryIssue(
                    "no-points",
                    "Нет доступных точек восстановления.",
                    blocking=False,
                )
            )
        if not points_result.readable:
            issues.append(
                RecoveryIssue(
                    "points-unreadable",
                    "Не удалось прочитать список точек восстановления в обычной системе.",
                    points_result.note or "",
                    blocking=False,
                )
            )
    except Exception as exc:  # independent OS-facing boundary
        points = ()
        issues.append(
            RecoveryIssue(
                "points-check-failed",
                "Не удалось проверить список точек восстановления.",
                str(exc),
                blocking=False,
            )
        )

    root_fs = _findmnt("FSTYPE", "/", runner)
    root_source = _findmnt("SOURCE", "/", runner)
    root_options = _findmnt("OPTIONS", "/", runner)
    root_is_btrfs = None if root_fs is None else root_fs == "btrfs"
    if root_is_btrfs is False:
        issues.append(RecoveryIssue("root-not-btrfs", "Корневая система не использует Btrfs."))
    elif root_is_btrfs is None:
        issues.append(RecoveryIssue("root-check-failed", "Не удалось определить файловую систему корня."))

    root_layout = _root_is_at(root_options, root_source) if root_is_btrfs else None
    if root_is_btrfs and root_layout is False:
        issues.append(
            RecoveryIssue(
                "root-layout",
                "Текущая схема системы не подходит для локального восстановления.",
                "Ожидается корневой subvolume @.",
            )
        )
    elif root_is_btrfs and root_layout is None:
        issues.append(RecoveryIssue("root-layout-unknown", "Не удалось полностью проверить схему корня."))

    snapshots_exists = paths.snapshots.is_dir()
    snapshots_fs = _findmnt("FSTYPE", str(paths.snapshots), runner) if snapshots_exists else ""
    snapshots_source = _findmnt("SOURCE", str(paths.snapshots), runner) if snapshots_exists else ""
    snapshots_options = _findmnt("OPTIONS", str(paths.snapshots), runner) if snapshots_exists else ""
    snapshots_available = snapshots_exists and snapshots_fs == "btrfs"
    if not snapshots_exists:
        issues.append(RecoveryIssue("snapshots-missing", "Не найдено хранилище точек восстановления."))
    elif snapshots_fs is None:
        snapshots_available = None
        issues.append(RecoveryIssue("snapshots-check-failed", "Не удалось проверить хранилище точек восстановления."))
    elif not snapshots_available:
        issues.append(RecoveryIssue("snapshots-not-btrfs", "Хранилище точек восстановления не использует Btrfs."))

    snapshots_layout = _snapshots_is_separate(snapshots_source, snapshots_options) if snapshots_available else None
    if snapshots_available and snapshots_layout is False:
        issues.append(
            RecoveryIssue(
                "snapshots-layout",
                "Точки восстановления находятся не в ожидаемом отдельном subvolume @snapshots.",
            )
        )
    elif snapshots_available and snapshots_layout is None:
        issues.append(RecoveryIssue("snapshots-layout-unknown", "Не удалось полностью проверить хранилище точек."))

    protected_state = _read_privileged_recovery_state(paths, runner)
    if protected_state is not None:
        (
            systemd_boot,
            iso_ready,
            boot_files_ready,
            environment_ready,
            state_fingerprint,
            broken,
        ) = protected_state
    else:
        state_fingerprint = None
        broken = _broken_roots(paths)
        # Compatibility fallback only. The installed v2 read helper performs the
        # authoritative hash/metadata validation of the ISO and boot files.
        boot_status = runner(["bootctl", "--no-pager", "status"], timeout=8)
        boot_text = _result_text(boot_status)
        systemd_boot = None if not boot_status.available else "systemd-boot" in boot_text
        try:
            files_exist = all(path.is_file() and path.stat().st_size > 0 for path in paths.local_files)
        except OSError:
            files_exist = False
        boot_files_ready = files_exist and _entry_is_local_recovery(paths.local_files[-1])
        try:
            iso_ready = paths.legacy_iso.is_file() and paths.legacy_iso.stat().st_size > 0
        except OSError:
            iso_ready = False
        environment_ready = bool(systemd_boot is True and iso_ready and boot_files_ready)

    if systemd_boot is False:
        issues.append(RecoveryIssue("boot-not-systemd", "systemd-boot не готов для локального режима восстановления."))
    elif systemd_boot is None:
        issues.append(RecoveryIssue("boot-check-failed", "Не удалось проверить systemd-boot."))

    if not environment_ready:
        issues.append(
            RecoveryIssue(
                "recovery-environment",
                "Среда восстановления ещё не подготовлена или устарела.",
                "Arch Manager подготовит её автоматически.",
                blocking=False,
            )
        )

    legacy_environment = bool(iso_ready) or paths.legacy_helper.is_file() or bool(boot_files_ready)
    previous = _legacy_record(paths)
    if (
        previous.backup_name
        and _BROKEN_ROOT_RE.fullmatch(previous.backup_name)
        and previous.backup_name not in broken
    ):
        notes.append(
            "Маркер последнего восстановления указывает на сохранённую прежнюю систему; "
            "её наличие требует отдельной безопасной проверки."
        )
    if broken:
        notes.append(
            "Обнаружена сохранённая прежняя система после восстановления. "
            "Она не изменяется проверкой готовности."
        )

    return RecoveryReadiness(
        checked_at=now(),
        points=points,
        root_is_btrfs=root_is_btrfs,
        root_layout_ok=root_layout,
        snapshots_available=snapshots_available,
        snapshots_layout_ok=snapshots_layout,
        systemd_boot_detected=systemd_boot,
        iso_ready=iso_ready,
        boot_files_ready=boot_files_ready,
        environment_ready=environment_ready,
        legacy_environment_found=legacy_environment,
        previous_restore=previous,
        broken_roots=broken,
        issues=tuple(issues),
        notes=tuple(notes),
        state_fingerprint=state_fingerprint,
    )


def build_recovery_plan(request: RecoveryRequest, readiness: RecoveryReadiness) -> RecoveryPlan:
    """Validate an optional preselection without binding environment readiness to it."""
    if (
        not isinstance(request.restore_point_id, int)
        or isinstance(request.restore_point_id, bool)
        or request.restore_point_id < 1
    ):
        raise RecoveryValidationError("Некорректный идентификатор точки восстановления.")
    point = next((point for point in readiness.points if point.number == request.restore_point_id), None)
    if point is None:
        raise RecoveryValidationError("Выбранная точка восстановления недоступна.")
    if not readiness.can_prepare:
        raise RecoveryValidationError("Система пока не пригодна для безопасного восстановления.")
    return RecoveryPlan(
        point,
        (
            "Загрузить общую автономную Recovery-среду.",
            "Показать актуальные точки из @snapshots уже после перезагрузки.",
            "Выбранную там точку применить только после отдельного подтверждения.",
            "Перед заменой @ сохранить прежнюю систему под резервным именем.",
        ),
    )
