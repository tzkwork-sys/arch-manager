from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Iterable

from .command import run_command


DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS = 3
MIN_PACKAGE_CACHE_KEEP_VERSIONS = 0
MAX_PACKAGE_CACHE_KEEP_VERSIONS = 3


@dataclass(frozen=True)
class MaintenanceSummary:
    reclaimable_cache_bytes: int | None
    orphan_packages: int | None
    journal_usage_bytes: int | None
    config_attention_files: int | None
    trash_bytes: int | None = None
    trash_items: int | None = None
    trash_locations: int | None = None
    thumbnail_cache_bytes: int | None = None
    thumbnail_items: int | None = None
    orphan_installed_bytes: int | None = None
    orphan_names: tuple[str, ...] = ()
    config_attention_paths: tuple[str, ...] = ()
    cache_keep_versions: int = DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS

    @property
    def known_reclaimable_bytes(self) -> int | None:
        """Known space that can be reclaimed by the visible Stage 6 actions.

        Journal cleanup is intentionally excluded because ``journalctl --disk-usage``
        reports active and archived journals together while vacuuming only removes
        archived files. Counting the full journal size would over-promise.
        """
        values = (
            self.reclaimable_cache_bytes,
            self.orphan_installed_bytes,
            self.trash_bytes,
            self.thumbnail_cache_bytes,
        )
        known = [value for value in values if value is not None]
        return sum(known) if known else None


_SIZE_UNITS = {
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gib": 1024**3,
    "t": 1000**4,
    "tb": 1000**4,
    "tib": 1024**4,
}


def parse_size_to_bytes(value: str) -> int | None:
    cleaned = value.strip().replace(",", ".")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b?|bytes?)\b", cleaned, re.I)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("byte", "bytes"):
        unit = "b"
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return None
    return int(number * factor)


def validate_cache_keep_versions(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("cache keep versions must be an integer")
    if not MIN_PACKAGE_CACHE_KEEP_VERSIONS <= value <= MAX_PACKAGE_CACHE_KEEP_VERSIONS:
        raise ValueError(
            f"cache keep versions must be between {MIN_PACKAGE_CACHE_KEEP_VERSIONS} "
            f"and {MAX_PACKAGE_CACHE_KEEP_VERSIONS}"
        )
    return value


def _cache_reclaimable(keep_versions: int) -> int | None:
    keep_versions = validate_cache_keep_versions(keep_versions)
    result = run_command(
        ["paccache", "-dv", "-k", str(keep_versions), "--nocolor"], timeout=20
    )
    if not result.available:
        return None
    text = "\n".join((result.stdout, result.stderr))
    if "no candidate packages found" in text.lower():
        return 0

    patterns = (
        r"disk space saved:\s*([^\)\n]+)",
        r"space saved:\s*([^\)\n]+)",
        r"freed:\s*([^\)\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return parse_size_to_bytes(match.group(1))
    return None


def _orphan_names() -> tuple[str, ...] | None:
    result = run_command(["pacman", "-Qtdq"], timeout=12)
    if not result.available:
        return None
    if result.returncode not in (0, 1):
        return None
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _orphan_installed_size(names: tuple[str, ...]) -> int | None:
    if not names:
        return 0

    total = 0
    # Keep command lines bounded even on unusually old/unclean installations.
    for offset in range(0, len(names), 80):
        chunk = names[offset : offset + 80]
        result = run_command(["pacman", "-Qi", "--", *chunk], timeout=20)
        if result.returncode != 0:
            return None
        found = 0
        for match in re.finditer(r"^Installed Size\s*:\s*(.+)$", result.stdout, re.M):
            parsed = parse_size_to_bytes(match.group(1))
            if parsed is None:
                return None
            total += parsed
            found += 1
        if found != len(chunk):
            return None
    return total


def _journal_usage() -> int | None:
    result = run_command(["journalctl", "--disk-usage", "--no-pager"], timeout=10)
    if result.returncode != 0:
        return None
    # Example: "Archived and active journals take up 368.0M in the file system."
    match = re.search(r"take up\s+([^\s]+)", result.stdout, re.I)
    return parse_size_to_bytes(match.group(1)) if match else None


def _config_attention_paths() -> tuple[str, ...] | None:
    result = run_command(["pacdiff", "-o", "--nocolor"], timeout=15)
    if not result.available:
        return None
    if result.returncode not in (0, 1):
        return None
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _directory_usage(path: Path) -> tuple[int, int] | None:
    """Return bytes and regular-entry count without following symbolic links."""
    if not path.exists():
        return (0, 0)
    if path.is_symlink() or not path.is_dir():
        return None

    total = 0
    count = 0
    stack = [path]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except (FileNotFoundError, PermissionError, OSError):
                        return None
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return (total, count)


def _decode_mount_field(value: str) -> str:
    # /proc/self/mountinfo escapes spaces and a few other bytes as octal sequences.
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _mounted_top_directories() -> tuple[Path, ...]:
    mountinfo = Path("/proc/self/mountinfo")
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()

    paths: list[Path] = []
    seen: set[str] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 5:
            continue
        mount_point = _decode_mount_field(fields[4])
        if mount_point in seen:
            continue
        seen.add(mount_point)
        paths.append(Path(mount_point))
    return tuple(paths)


def _owned_directory(path: Path, uid: int) -> bool:
    try:
        info = path.lstat()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == uid


def discover_trash_roots(
    *,
    home: Path | None = None,
    uid: int | None = None,
    mount_points: Iterable[Path] | None = None,
) -> tuple[Path, ...]:
    """Discover freedesktop Trash locations belonging to the current user.

    KDE/Plasma keeps files deleted from another mounted filesystem on that
    filesystem when possible. Therefore looking only at ``~/.local/share/Trash``
    misses common setups with a separate /data partition or removable drives.
    """
    home = Path.home() if home is None else Path(home)
    uid = os.getuid() if uid is None else uid

    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME", "").strip()
    xdg_data_home = Path(xdg_data_home_raw) if xdg_data_home_raw else home / ".local" / "share"
    if not xdg_data_home.is_absolute():
        xdg_data_home = home / ".local" / "share"

    candidates: list[Path] = [xdg_data_home / "Trash"]
    points = tuple(mount_points) if mount_points is not None else _mounted_top_directories()
    for topdir in points:
        try:
            top_info = topdir.lstat()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if not stat.S_ISDIR(top_info.st_mode) or stat.S_ISLNK(top_info.st_mode):
            continue

        shared = topdir / ".Trash"
        try:
            shared_info = shared.lstat()
        except (FileNotFoundError, PermissionError, OSError):
            shared_info = None
        if (
            shared_info is not None
            and stat.S_ISDIR(shared_info.st_mode)
            and not stat.S_ISLNK(shared_info.st_mode)
            and bool(shared_info.st_mode & stat.S_ISVTX)
        ):
            candidates.append(shared / str(uid))

        candidates.append(topdir / f".Trash-{uid}")

    roots: list[Path] = []
    identities: set[tuple[int, int]] = set()
    for candidate in candidates:
        if not _owned_directory(candidate, uid):
            continue
        try:
            info = candidate.stat()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in identities:
            continue
        identities.add(identity)
        roots.append(candidate)
    return tuple(roots)


def _trash_usage(
    home: Path,
    *,
    uid: int | None = None,
    mount_points: Iterable[Path] | None = None,
) -> tuple[int, int, int] | None:
    roots = discover_trash_roots(home=home, uid=uid, mount_points=mount_points)
    total_bytes = 0
    total_items = 0
    for root in roots:
        files = _directory_usage(root / "files")
        info = _directory_usage(root / "info")
        if files is None or info is None:
            return None
        total_bytes += files[0] + info[0]
        total_items += files[1]
    return (total_bytes, total_items, len(roots))


def _thumbnail_usage(home: Path) -> tuple[int, int] | None:
    return _directory_usage(home / ".cache" / "thumbnails")


def collect_maintenance(
    cache_keep_versions: int = DEFAULT_PACKAGE_CACHE_KEEP_VERSIONS,
) -> MaintenanceSummary:
    cache_keep_versions = validate_cache_keep_versions(cache_keep_versions)
    home = Path.home()
    orphan_names = _orphan_names()
    config_paths = _config_attention_paths()
    trash = _trash_usage(home)
    thumbnails = _thumbnail_usage(home)

    return MaintenanceSummary(
        reclaimable_cache_bytes=_cache_reclaimable(cache_keep_versions),
        orphan_packages=None if orphan_names is None else len(orphan_names),
        journal_usage_bytes=_journal_usage(),
        config_attention_files=None if config_paths is None else len(config_paths),
        trash_bytes=None if trash is None else trash[0],
        trash_items=None if trash is None else trash[1],
        trash_locations=None if trash is None else trash[2],
        thumbnail_cache_bytes=None if thumbnails is None else thumbnails[0],
        thumbnail_items=None if thumbnails is None else thumbnails[1],
        orphan_installed_bytes=(
            None if orphan_names is None else _orphan_installed_size(orphan_names)
        ),
        orphan_names=() if orphan_names is None else orphan_names,
        config_attention_paths=() if config_paths is None else config_paths,
        cache_keep_versions=cache_keep_versions,
    )
