"""On-demand, bounded AppStream metadata for Flatpak details (not catalog search).

Source: Flathub public API. Only the details dialog requests this data; no
network operations occur while building or filtering application cards.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .background import create_background_executor

_EXECUTOR = create_background_executor(max_workers=2, thread_name_prefix="arch-manager-flathub-details")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9._-]+$")
_API = "https://flathub.org/api/v2/appstream/"
_MAX_RESPONSE = 512 * 1024
_TTL = 24 * 60 * 60
_SCREENSHOT_LIMIT = 8


class FlathubMetadataError(RuntimeError):
    """The optional details request failed; Flatpak actions remain available."""


@dataclass(frozen=True, slots=True)
class FlathubMetadata:
    app_id: str
    name: str = ""
    summary: str = ""
    description_html: str = ""
    screenshots: tuple[str, ...] = ()
    homepage: str | None = None
    bugtracker: str | None = None
    help_url: str | None = None
    license: str | None = None
    publisher: str | None = None
    icon_url: str | None = None


class _SafeDescription(HTMLParser):
    _tags = frozenset({"p", "ul", "ol", "li", "b", "strong", "em", "i", "code", "br"})
    _blocked = frozenset({"script", "style", "iframe", "svg", "object"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._blocked:
            self.blocked += 1
        elif not self.blocked and tag in self._tags:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._blocked:
            self.blocked = max(0, self.blocked - 1)
        elif not self.blocked and tag in self._tags and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked:
            self.parts.append(escape(data))


def sanitize_description(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _SafeDescription()
    try:
        parser.feed(value[:12000])
        parser.close()
    except ValueError:
        return ""
    return "".join(parser.parts)[:12000]


def _clean_string(value: object, limit: int = 500) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _external_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        p = urlsplit(value)
    except ValueError:
        return None
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return None
    try:
        port = p.port
    except ValueError:
        return None
    if port not in (None, 443) or p.hostname in {"localhost", "127.0.0.1", "::1"}:
        return None
    return value


def _image_url(value: object) -> str | None:
    link = _external_url(value)
    if link is None:
        return None
    # Flathub mirrors screenshots/icon sources. Never request arbitrary host data.
    if urlsplit(link).hostname not in {"dl.flathub.org", "flathub.org"}:
        return None
    return link


def _screenshots(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for entry in value:
        if len(items) >= _SCREENSHOT_LIMIT:
            break
        if not isinstance(entry, dict):
            continue
        # Flathub's API usually supplies 624x351, 752x423 and source variants.
        image = None
        for key in ("624x351", "752x423", "1248x702", "source", "url"):
            item = entry.get(key)
            if isinstance(item, dict):
                item = item.get("url")
            image = _image_url(item)
            if image:
                break
        if image and image not in items:
            items.append(image)
    return tuple(items)


def parse_flathub_metadata(payload: object, app_id: str) -> FlathubMetadata:
    if not _ID.fullmatch(app_id):
        raise ValueError("Недопустимый Flatpak ID")
    if not isinstance(payload, dict):
        raise FlathubMetadataError("Flathub вернул неверный формат метаданных")
    response_id = payload.get("id")
    if response_id != app_id:
        raise FlathubMetadataError("Flathub вернул данные другого приложения")
    urls = payload.get("urls") if isinstance(payload.get("urls"), dict) else {}
    return FlathubMetadata(
        app_id=app_id,
        name=_clean_string(payload.get("name"), 150),
        summary=_clean_string(payload.get("summary"), 500),
        description_html=sanitize_description(payload.get("description")),
        screenshots=_screenshots(payload.get("screenshots")),
        homepage=_external_url(urls.get("homepage")),
        bugtracker=_external_url(urls.get("bugtracker")),
        help_url=_external_url(urls.get("help") or urls.get("faq") or urls.get("documentation")),
        license=_clean_string(payload.get("project_license") or payload.get("license"), 150) or None,
        publisher=_clean_string(payload.get("developer_name"), 150) or None,
        icon_url=_image_url(payload.get("icon")),
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FlathubMetadataError("Неожиданное перенаправление ответа Flathub")


class FlathubMetadataService:
    """On-demand details with a small, TTL-bounded JSON cache."""

    def __init__(self, *, cache_dir: Path | None = None,
                 fetcher: Callable[[str], bytes] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "arch-manager/app-store/flathub-metadata-v1"
        self.fetcher = fetcher or self._fetch
        self.clock = clock

    @staticmethod
    def _fetch(app_id: str) -> bytes:
        url = _API + quote(app_id, safe="")
        opener = build_opener(_NoRedirect())
        request = Request(url, headers={"User-Agent": "ArchManager/FlatpakDetails-1", "Accept": "application/json"})
        try:
            with opener.open(request, timeout=9) as response:
                if response.status != 200:
                    raise FlathubMetadataError(f"Flathub HTTP {response.status}")
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" not in content_type:
                    raise FlathubMetadataError("Flathub вернул не JSON")
                body = response.read(_MAX_RESPONSE + 1)
        except (OSError, HTTPError, URLError, TimeoutError) as exc:
            raise FlathubMetadataError(f"Не удалось получить данные Flathub: {exc}") from exc
        if len(body) > _MAX_RESPONSE:
            raise FlathubMetadataError("Слишком большой ответ Flathub")
        return body

    def _cache_file(self, app_id: str) -> Path:
        return self.cache_dir / f"{app_id}.json"

    def _read_cache(self, app_id: str) -> tuple[object, float] | None:
        path = self._cache_file(app_id)
        try:
            if path.stat().st_size > _MAX_RESPONSE:
                return None
            content = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(content, dict) or content.get("id") != app_id:
                return None
            return content, path.stat().st_mtime
        except (OSError, ValueError, UnicodeError):
            return None

    def _save_cache(self, app_id: str, body: bytes) -> None:
        tmp: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="._metadata-", suffix=".tmp", dir=self.cache_dir)
            with os.fdopen(fd, "wb") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self._cache_file(app_id))
            tmp = None
            # Prevent unbounded growth from searching/opening many apps.
            files = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            for old in files[120:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass  # Caching must never block details.
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def load(self, app_id: str, *, force: bool = False) -> FlathubMetadata:
        if not _ID.fullmatch(app_id):
            raise ValueError("Недопустимый Flatpak ID")
        cached = self._read_cache(app_id)
        if cached and not force and self.clock() - cached[1] < _TTL:
            return parse_flathub_metadata(cached[0], app_id)
        try:
            body = self.fetcher(app_id)
            if len(body) > _MAX_RESPONSE:
                raise FlathubMetadataError("Слишком большой ответ Flathub")
            data = json.loads(body)
            result = parse_flathub_metadata(data, app_id)
        except (FlathubMetadataError, OSError, ValueError, TypeError, UnicodeError) as exc:
            if cached:
                return parse_flathub_metadata(cached[0], app_id)
            raise FlathubMetadataError(f"Данные Flathub недоступны: {exc}") from exc
        self._save_cache(app_id, body)
        return result

    def load_async(self, app_id: str) -> Future[FlathubMetadata]:
        return _EXECUTOR.submit(self.load, app_id)
