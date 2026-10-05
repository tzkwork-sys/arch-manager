from __future__ import annotations

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import tempfile
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PKGSTATS_API_URL = "https://pkgstats.archlinux.de/api/packages"
PKGSTATS_CACHE_SCHEMA = 1
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_PAGE_LIMIT = 10_000
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
USER_AGENT = "ArchManager-AppStore-Popularity/1.0"

_POPULARITY_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="arch-manager-popularity",
)


class PopularityError(RuntimeError):
    """Base error for optional package-popularity data."""


class PopularityNetworkError(PopularityError):
    """The pkgstats service could not be reached."""


class PopularityInvalidResponse(PopularityError):
    """The pkgstats service returned an unexpected payload."""


@dataclass(frozen=True, slots=True)
class PopularitySnapshot:
    """Read-only package popularity snapshot returned by pkgstats."""

    scores: Mapping[str, float]
    counts: Mapping[str, int]
    start_month: int | None = None
    end_month: int | None = None
    cache_hit: bool = False
    stale_cache: bool = False

    @classmethod
    def build(
        cls,
        scores: Mapping[str, float],
        counts: Mapping[str, int],
        *,
        start_month: int | None = None,
        end_month: int | None = None,
        cache_hit: bool = False,
        stale_cache: bool = False,
    ) -> "PopularitySnapshot":
        return cls(
            MappingProxyType(dict(scores)),
            MappingProxyType(dict(counts)),
            start_month=start_month,
            end_month=end_month,
            cache_hit=cache_hit,
            stale_cache=stale_cache,
        )


class PkgstatsPopularityClient:
    """Small read-only client for the official pkgstats JSON API."""

    def __init__(
        self,
        *,
        base_url: str = PKGSTATS_API_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("?")
        self.timeout = max(0.5, float(timeout))
        self.page_limit = max(1, min(10_000, int(page_limit)))
        self.max_response_bytes = max(64 * 1024, int(max_response_bytes))
        self.opener = opener

    def fetch_all(self) -> PopularitySnapshot:
        scores: dict[str, float] = {}
        counts: dict[str, int] = {}
        start_month: int | None = None
        end_month: int | None = None
        offset = 0
        total: int | None = None

        while total is None or offset < total:
            query = urlencode({"limit": self.page_limit, "offset": offset})
            payload = self._get_json(f"{self.base_url}?{query}")
            rows, page_total = self._parse_page(payload)
            total = page_total if total is None else max(total, page_total)
            if not rows:
                break

            for row in rows:
                name = row["name"]
                scores[name] = row["popularity"]
                counts[name] = row["count"]
                row_start = row.get("startMonth")
                row_end = row.get("endMonth")
                if isinstance(row_start, int):
                    start_month = row_start if start_month is None else min(start_month, row_start)
                if isinstance(row_end, int):
                    end_month = row_end if end_month is None else max(end_month, row_end)

            offset += len(rows)
            # The documented offset cap is 100000. Refuse an endless/invalid API loop.
            if offset > 100_000:
                raise PopularityInvalidResponse("pkgstats result exceeds supported pagination range")

        return PopularitySnapshot.build(
            scores,
            counts,
            start_month=start_month,
            end_month=end_month,
        )

    def _get_json(self, url: str) -> Any:
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PopularityNetworkError(str(exc)) from exc
        if len(raw) > self.max_response_bytes:
            raise PopularityInvalidResponse("pkgstats response is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PopularityInvalidResponse("pkgstats response is not valid JSON") from exc

    @staticmethod
    def _parse_page(payload: Any) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(payload, dict):
            raise PopularityInvalidResponse("pkgstats response is not an object")
        rows = payload.get("packagePopularities")
        total = payload.get("total")
        if not isinstance(rows, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise PopularityInvalidResponse("pkgstats response has invalid list metadata")

        parsed: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                raise PopularityInvalidResponse("pkgstats package entry is not an object")
            name = item.get("name")
            popularity = item.get("popularity")
            count = item.get("count")
            if not isinstance(name, str) or not name.strip():
                raise PopularityInvalidResponse("pkgstats package name is invalid")
            if isinstance(popularity, bool) or not isinstance(popularity, (int, float)):
                raise PopularityInvalidResponse("pkgstats popularity is invalid")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise PopularityInvalidResponse("pkgstats count is invalid")
            if float(popularity) < 0:
                raise PopularityInvalidResponse("pkgstats popularity is negative")
            parsed.append(
                {
                    "name": name.strip(),
                    "popularity": float(popularity),
                    "count": count,
                    "startMonth": item.get("startMonth"),
                    "endMonth": item.get("endMonth"),
                }
            )
        return parsed, total


def default_popularity_cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "arch-manager" / "app-store" / "popularity-v1.json"


class PopularityCache:
    """Single bounded daily cache for the pkgstats package snapshot."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path) if path is not None else default_popularity_cache_path()
        self.ttl = max(0.0, float(ttl))
        self.clock = clock

    def load(self, *, allow_stale: bool = False) -> PopularitySnapshot | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            self._discard()
            return None
        if not isinstance(payload, dict) or payload.get("schema") != PKGSTATS_CACHE_SCHEMA:
            self._discard()
            return None
        saved_at = payload.get("saved_at")
        scores = payload.get("scores")
        counts = payload.get("counts")
        if (
            isinstance(saved_at, bool)
            or not isinstance(saved_at, (int, float))
            or not isinstance(scores, dict)
            or not isinstance(counts, dict)
        ):
            self._discard()
            return None
        stale = self.clock() - float(saved_at) > self.ttl
        if stale and not allow_stale:
            return None
        try:
            parsed_scores = {
                str(name): float(value)
                for name, value in scores.items()
                if isinstance(name, str)
                and not isinstance(value, bool)
                and isinstance(value, (int, float))
                and float(value) >= 0
            }
            parsed_counts = {
                str(name): int(value)
                for name, value in counts.items()
                if isinstance(name, str)
                and not isinstance(value, bool)
                and isinstance(value, int)
                and value >= 0
            }
        except (TypeError, ValueError):
            self._discard()
            return None
        return PopularitySnapshot.build(
            parsed_scores,
            parsed_counts,
            start_month=payload.get("start_month") if isinstance(payload.get("start_month"), int) else None,
            end_month=payload.get("end_month") if isinstance(payload.get("end_month"), int) else None,
            cache_hit=True,
            stale_cache=stale,
        )

    def save(self, snapshot: PopularitySnapshot) -> None:
        payload = {
            "schema": PKGSTATS_CACHE_SCHEMA,
            "saved_at": self.clock(),
            "start_month": snapshot.start_month,
            "end_month": snapshot.end_month,
            "scores": dict(snapshot.scores),
            "counts": dict(snapshot.counts),
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".popularity-", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _discard(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class PkgstatsPopularityService:
    """Cached async facade. Popularity is optional and never blocks catalog loading."""

    def __init__(
        self,
        *,
        client: PkgstatsPopularityClient | None = None,
        cache: PopularityCache | None = None,
    ) -> None:
        self.client = client or PkgstatsPopularityClient()
        self.cache = cache or PopularityCache()

    def load(self, *, use_cache: bool = True) -> PopularitySnapshot:
        if use_cache:
            cached = self.cache.load()
            if cached is not None:
                return cached
        stale = self.cache.load(allow_stale=True) if use_cache else None
        try:
            fresh = self.client.fetch_all()
        except PopularityError:
            if stale is not None:
                return replace(stale, cache_hit=True, stale_cache=True)
            raise
        if use_cache:
            try:
                self.cache.save(fresh)
            except OSError:
                pass
        return fresh

    def load_async(
        self,
        *,
        use_cache: bool = True,
        executor: Executor | None = None,
    ) -> Future[PopularitySnapshot]:
        return (executor or _POPULARITY_EXECUTOR).submit(self.load, use_cache=use_cache)
