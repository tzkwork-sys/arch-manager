from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping, Any

from .models import Application


CACHE_SCHEMA_VERSION = 4


_MEMORY_CATALOG_LIMIT = 2
_MEMORY_CATALOGS: "OrderedDict[tuple[str, str], tuple[tuple[Application, ...], dict[str, Any]]]" = OrderedDict()


def _memory_key(path: Path, fingerprint: str) -> tuple[str, str]:
    return str(path), fingerprint


def _load_memory_catalog(path: Path, fingerprint: str) -> tuple[tuple[Application, ...], dict[str, Any]] | None:
    key = _memory_key(path, fingerprint)
    cached = _MEMORY_CATALOGS.get(key)
    if cached is None:
        return None
    applications, stats = cached
    _MEMORY_CATALOGS.move_to_end(key)
    return applications, dict(stats)


def _save_memory_catalog(
    path: Path,
    fingerprint: str,
    applications: Iterable[Application],
    stats: Mapping[str, Any],
) -> None:
    key = _memory_key(path, fingerprint)
    _MEMORY_CATALOGS[key] = (tuple(applications), dict(stats))
    _MEMORY_CATALOGS.move_to_end(key)
    while len(_MEMORY_CATALOGS) > _MEMORY_CATALOG_LIMIT:
        _MEMORY_CATALOGS.popitem(last=False)


def default_cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "arch-manager" / "app-store" / f"catalog-v{CACHE_SCHEMA_VERSION}.json"


def _stat_record(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "missing": True}
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def build_catalog_fingerprint(
    metadata_files: Iterable[Path],
    *,
    pacman_local_dir: Path = Path("/var/lib/pacman/local"),
    pacman_sync_dir: Path = Path("/var/lib/pacman/sync"),
    pacman_config: Path = Path("/etc/pacman.conf"),
) -> str:
    payload: dict[str, Any] = {
        "schema": CACHE_SCHEMA_VERSION,
        "metadata": [_stat_record(Path(path)) for path in sorted(metadata_files, key=lambda item: str(item))],
        "pacman_local": _stat_record(pacman_local_dir),
        "pacman_config": _stat_record(pacman_config),
        "pacman_sync": [],
    }
    if pacman_sync_dir.is_dir():
        payload["pacman_sync"] = [
            _stat_record(path)
            for path in sorted(pacman_sync_dir.glob("*.db"), key=lambda item: str(item))
        ]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CatalogCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_cache_path()

    def load(self, fingerprint: str) -> tuple[tuple[Application, ...], Mapping[str, Any]] | None:
        cached = _load_memory_catalog(self.path, fingerprint)
        if cached is not None:
            return cached
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if payload.get("schema") != CACHE_SCHEMA_VERSION or payload.get("fingerprint") != fingerprint:
            return None
        applications = payload.get("applications")
        stats = payload.get("stats")
        if not isinstance(applications, list) or not isinstance(stats, dict):
            return None
        try:
            parsed = tuple(Application.from_dict(item) for item in applications if isinstance(item, dict))
        except (TypeError, ValueError):
            return None
        _save_memory_catalog(self.path, fingerprint, parsed, stats)
        return parsed, dict(stats)

    def save(self, fingerprint: str, applications: Iterable[Application], stats: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        application_list = tuple(applications)
        stats_dict = dict(stats)
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "applications": [application.to_dict() for application in application_list],
            "stats": stats_dict,
        }
        _save_memory_catalog(self.path, fingerprint, application_list, stats_dict)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd, temporary_name = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
