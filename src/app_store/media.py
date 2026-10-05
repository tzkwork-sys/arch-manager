from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_MEDIA_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="arch-manager-media")
MEDIA_CACHE_SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_MEMORY_BYTES = 24 * 1024 * 1024
DEFAULT_MAX_DISK_BYTES = 96 * 1024 * 1024


def default_media_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "arch-manager" / "app-store" / f"media-v{MEDIA_CACHE_SCHEMA_VERSION}"


@dataclass(frozen=True, slots=True)
class MediaLoadResult:
    url: str
    data: bytes
    source: str  # "memory", "disk" or "network"


class MediaLoadError(RuntimeError):
    pass


class MediaCache:
    """Small safe image cache used by the read-only application details page."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES,
        max_disk_bytes: int = DEFAULT_MAX_DISK_BYTES,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_media_cache_dir()
        self.timeout = max(0.5, float(timeout))
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.max_memory_bytes = max(64 * 1024, int(max_memory_bytes))
        self.max_disk_bytes = max(self.max_bytes, int(max_disk_bytes))
        self._memory: "OrderedDict[str, bytes]" = OrderedDict()
        self._memory_bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _validate_url(url: str) -> str:
        value = (url or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MediaLoadError("unsupported media URL")
        return value

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.img"

    def _remember(self, url: str, data: bytes) -> None:
        with self._lock:
            previous = self._memory.pop(url, None)
            if previous is not None:
                self._memory_bytes -= len(previous)
            self._memory[url] = data
            self._memory.move_to_end(url)
            self._memory_bytes += len(data)
            while self._memory and self._memory_bytes > self.max_memory_bytes:
                _evicted_url, evicted_data = self._memory.popitem(last=False)
                self._memory_bytes -= len(evicted_data)

    def _lookup_memory(self, url: str) -> bytes | None:
        with self._lock:
            data = self._memory.get(url)
            if data is None:
                return None
            self._memory.move_to_end(url)
            return data

    def _drop_memory(self, url: str) -> None:
        with self._lock:
            previous = self._memory.pop(url, None)
            if previous is not None:
                self._memory_bytes -= len(previous)

    def _touch(self, path: Path) -> None:
        try:
            path.touch(exist_ok=True)
        except OSError:
            pass

    def _prune_disk_cache(self) -> None:
        try:
            entries = [
                candidate
                for candidate in self.cache_dir.glob("*.img")
                if candidate.is_file()
            ]
        except OSError:
            return
        total = 0
        stats: list[tuple[int, Path]] = []
        for candidate in entries:
            try:
                stat = candidate.stat()
            except OSError:
                continue
            total += stat.st_size
            stats.append((stat.st_mtime_ns, candidate))
        if total <= self.max_disk_bytes:
            return
        for _mtime_ns, candidate in sorted(stats, key=lambda item: item[0]):
            if total <= self.max_disk_bytes:
                break
            try:
                size = candidate.stat().st_size
                candidate.unlink()
            except OSError:
                continue
            total -= size

    def load(self, url: str, *, retry: bool = False) -> MediaLoadResult:
        url = self._validate_url(url)
        path = self._cache_path(url)

        if retry:
            self._drop_memory(url)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # A stale/readonly file should not block a fresh attempt.
                pass
        else:
            data = self._lookup_memory(url)
            if data is not None:
                return MediaLoadResult(url=url, data=data, source="memory")

            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if data:
                self._touch(path)
                self._remember(url, data)
                return MediaLoadResult(url=url, data=data, source="disk")

        request = Request(
            url,
            headers={
                "User-Agent": "ArchManager-AppStore/1.0",
                "Accept": "image/*,*/*;q=0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type and not content_type.startswith("image/"):
                    raise MediaLoadError(f"unexpected media type: {content_type}")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self.max_bytes:
                    raise MediaLoadError("media file is too large")
                data = response.read(self.max_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise MediaLoadError(str(exc)) from exc
        if not data:
            raise MediaLoadError("empty media response")
        if len(data) > self.max_bytes:
            raise MediaLoadError("media file is too large")

        temporary: Path | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=".media-", suffix=".tmp", dir=self.cache_dir)
            temporary = Path(temporary_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            self._prune_disk_cache()
        except OSError:
            # Cache persistence is an optimization; a read-only/full cache must
            # never turn a successfully downloaded screenshot into a GUI error.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

        self._remember(url, data)
        return MediaLoadResult(url=url, data=data, source="network")

    def load_async(
        self,
        url: str,
        *,
        retry: bool = False,
        executor: Executor | None = None,
    ) -> Future[MediaLoadResult]:
        pool = executor or _MEDIA_EXECUTOR
        return pool.submit(self.load, url, retry=retry)
