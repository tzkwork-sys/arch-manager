from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Iterable

from .models import AurPackage


AUR_CACHE_SCHEMA_VERSION = 2
DEFAULT_SEARCH_TTL_SECONDS = 5 * 60
DEFAULT_INFO_TTL_SECONDS = 30 * 60
DEFAULT_MAX_DISK_BYTES = 12 * 1024 * 1024


def default_aur_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "arch-manager" / "app-store" / "aur"


class AurCache:
    """Separate bounded JSON cache for AUR search/info responses."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        search_ttl: float = DEFAULT_SEARCH_TTL_SECONDS,
        info_ttl: float = DEFAULT_INFO_TTL_SECONDS,
        max_disk_bytes: int = DEFAULT_MAX_DISK_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_aur_cache_dir()
        self.search_ttl = max(0.0, float(search_ttl))
        self.info_ttl = max(0.0, float(info_ttl))
        self.max_disk_bytes = max(64 * 1024, int(max_disk_bytes))
        self.clock = clock

    def get_search(self, query: str, *, allow_stale: bool = False) -> tuple[AurPackage, ...] | None:
        key = (query or "").strip().casefold()
        if not key:
            return None
        return self._load(self._path("search", key), self.search_ttl, allow_stale=allow_stale)

    def set_search(self, query: str, packages: Iterable[AurPackage]) -> None:
        key = (query or "").strip().casefold()
        if key:
            self._save(self._path("search", key), packages)

    def get_info(self, package_name: str, *, allow_stale: bool = False) -> AurPackage | None:
        key = (package_name or "").strip()
        if not key:
            return None
        packages = self._load(self._path("info", key), self.info_ttl, allow_stale=allow_stale)
        return packages[0] if packages else None

    def set_info(self, packages: Iterable[AurPackage]) -> None:
        for package in packages:
            self._save(self._path("info", package.name), (package,))

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}.json"

    def _load(self, path: Path, ttl: float, *, allow_stale: bool) -> tuple[AurPackage, ...] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            self._discard(path)
            return None
        if not isinstance(payload, dict) or payload.get("schema") != AUR_CACHE_SCHEMA_VERSION:
            self._discard(path)
            return None
        saved_at = payload.get("saved_at")
        items = payload.get("packages")
        if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)) or not isinstance(items, list):
            self._discard(path)
            return None
        if not allow_stale and self.clock() - float(saved_at) > ttl:
            return None
        if not all(isinstance(item, dict) for item in items):
            self._discard(path)
            return None
        try:
            packages = tuple(AurPackage.from_dict(item) for item in items)
        except (TypeError, ValueError):
            self._discard(path)
            return None
        try:
            path.touch(exist_ok=True)
        except OSError:
            pass
        return packages

    def _save(self, path: Path, packages: Iterable[AurPackage]) -> None:
        package_list = tuple(packages)
        payload: dict[str, Any] = {
            "schema": AUR_CACHE_SCHEMA_VERSION,
            "saved_at": self.clock(),
            "packages": [package.to_dict() for package in package_list],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=".aur-", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            self._prune()
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _prune(self) -> None:
        try:
            files = [path for path in self.cache_dir.rglob("*.json") if path.is_file()]
        except OSError:
            return
        stats: list[tuple[int, int, Path]] = []
        total = 0
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            stats.append((stat.st_mtime_ns, stat.st_size, path))
        if total <= self.max_disk_bytes:
            return
        for _mtime, size, path in sorted(stats, key=lambda item: item[0]):
            if total <= self.max_disk_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
