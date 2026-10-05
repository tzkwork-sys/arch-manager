from __future__ import annotations

from dataclasses import dataclass
import bz2
from html import escape
import gzip
import lzma
import os
from pathlib import Path
import re
from typing import BinaryIO, Iterable
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from .categories import normalize_categories


_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_ACCEPTED_COMPONENT_TYPES = {"desktop", "desktop-application"}
_OFFICIAL_REPOSITORIES = {"core", "extra", "multilib"}
_METADATA_SUFFIXES = (".xml", ".xml.gz", ".xml.xz", ".xml.bz2")


@dataclass(frozen=True, slots=True)
class AppStreamComponent:
    app_id: str
    package_names: tuple[str, ...]
    name: str
    summary: str
    description: str
    description_html: str
    categories: tuple[str, ...]
    raw_categories: tuple[str, ...]
    keywords: tuple[str, ...]
    icon: str | None
    icon_type: str | None
    screenshots: tuple[str, ...]
    homepage: str | None
    bugtracker: str | None
    help_url: str | None
    license: str | None
    desktop_entry: str | None
    launchable_binary: str | None
    source_repository: str | None
    source_file: str
    metadata_complete: bool
    metadata_issues: tuple[str, ...]
    publisher: str | None = None


@dataclass(frozen=True, slots=True)
class AppStreamReadResult:
    components: tuple[AppStreamComponent, ...]
    components_seen: int
    desktop_components_seen: int
    skipped_components: int
    errors: tuple[str, ...]
    metadata_files: tuple[str, ...]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _lang_key(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("-", "_").split(".", 1)[0].strip().lower() or None


def _system_locale_preferences() -> tuple[str, ...]:
    candidates: list[str] = []
    for variable in ("LANGUAGE", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "")
        for item in value.split(":"):
            key = _lang_key(item)
            if key and key not in {"c", "posix"}:
                candidates.append(key)
                if "_" in key:
                    candidates.append(key.split("_", 1)[0])
    candidates.extend(("en_us", "en"))
    return tuple(dict.fromkeys(candidates))


def _direct_children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local_name(child.tag) == name]


def _choose_localized(nodes: Iterable[ET.Element], preferences: tuple[str, ...]) -> ET.Element | None:
    items = list(nodes)
    if not items:
        return None
    by_lang: dict[str | None, ET.Element] = {}
    for node in items:
        by_lang.setdefault(_lang_key(node.get(_XML_LANG)), node)
    for preference in preferences:
        if preference in by_lang:
            return by_lang[preference]
        short = preference.split("_", 1)[0]
        if short in by_lang:
            return by_lang[short]
    if None in by_lang:
        return by_lang[None]
    if "en" in by_lang:
        return by_lang["en"]
    return items[0]


def _localized_text(parent: ET.Element, name: str, preferences: tuple[str, ...]) -> str:
    node = _choose_localized(_direct_children(parent, name), preferences)
    return _clean_text("".join(node.itertext())) if node is not None else ""


def _flatten_description(description: ET.Element, preferences: tuple[str, ...]) -> str:
    children = list(description)
    if not children:
        return _clean_text("".join(description.itertext()))

    languages = {_lang_key(child.get(_XML_LANG)) for child in children}
    selected_language: str | None = None
    for preference in preferences:
        if preference in languages:
            selected_language = preference
            break
        short = preference.split("_", 1)[0]
        if short in languages:
            selected_language = short
            break
    if selected_language is None and None not in languages:
        selected_language = "en" if "en" in languages else next(iter(languages))

    paragraphs: list[str] = []
    for child in children:
        child_language = _lang_key(child.get(_XML_LANG))
        if child_language != selected_language:
            continue
        kind = _local_name(child.tag)
        if kind in {"ul", "ol"}:
            for item in child.iter():
                if _local_name(item.tag) == "li":
                    text = _clean_text("".join(item.itertext()))
                    if text:
                        paragraphs.append(f"• {text}")
            continue
        text = _clean_text("".join(child.itertext()))
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _selected_description_children(description: ET.Element, preferences: tuple[str, ...]) -> list[ET.Element]:
    children = list(description)
    if not children:
        return []
    languages = {_lang_key(child.get(_XML_LANG)) for child in children}
    selected_language: str | None = None
    for preference in preferences:
        if preference in languages:
            selected_language = preference
            break
        short = preference.split("_", 1)[0]
        if short in languages:
            selected_language = short
            break
    if selected_language is None and None not in languages:
        selected_language = "en" if "en" in languages else next(iter(languages))
    return [child for child in children if _lang_key(child.get(_XML_LANG)) == selected_language]


def _safe_inline_html(node: ET.Element) -> str:
    pieces: list[str] = []
    if node.text:
        pieces.append(escape(node.text))
    allowed_inline = {"em": "em", "strong": "strong", "code": "code"}
    for child in node:
        tag = allowed_inline.get(_local_name(child.tag))
        inner = _safe_inline_html(child)
        if tag:
            pieces.append(f"<{tag}>{inner}</{tag}>")
        else:
            # Unsupported AppStream/foreign markup is flattened to escaped text.
            pieces.append(inner)
        if child.tail:
            pieces.append(escape(child.tail))
    return "".join(pieces)


def _safe_description_html(description: ET.Element, preferences: tuple[str, ...]) -> str:
    children = _selected_description_children(description, preferences)
    if not children:
        text = _clean_text("".join(description.itertext()))
        return f"<p>{escape(text)}</p>" if text else ""
    blocks: list[str] = []
    for child in children:
        kind = _local_name(child.tag)
        if kind == "p":
            inner = _safe_inline_html(child).strip()
            if inner:
                blocks.append(f"<p>{inner}</p>")
        elif kind in {"ul", "ol"}:
            items: list[str] = []
            for item in child:
                if _local_name(item.tag) != "li":
                    continue
                inner = _safe_inline_html(item).strip()
                if inner:
                    items.append(f"<li>{inner}</li>")
            if items:
                blocks.append(f"<{kind}>{''.join(items)}</{kind}>")
        else:
            text = _clean_text("".join(child.itertext()))
            if text:
                blocks.append(f"<p>{escape(text)}</p>")
    return "".join(blocks)


def _localized_description(component: ET.Element, preferences: tuple[str, ...]) -> tuple[str, str]:
    description = _choose_localized(_direct_children(component, "description"), preferences)
    if description is None:
        return "", ""
    return _flatten_description(description, preferences), _safe_description_html(description, preferences)


def _component_publisher(component: ET.Element, preferences: tuple[str, ...]) -> str | None:
    """Return the best available AppStream developer/publisher label.

    Modern AppStream uses ``<developer><name>…</name></developer>`` while
    older catalogs may still expose ``developer_name`` or ``project_group``.
    The value is presentation metadata only; it is never inferred from the
    package maintainer or repository.
    """

    developer = next(iter(_direct_children(component, "developer")), None)
    if developer is not None:
        value = _localized_text(developer, "name", preferences)
        if value:
            return value
    for tag in ("developer_name", "project_group"):
        value = _localized_text(component, tag, preferences)
        if value:
            return value
    return None


def _repository_from_source(path: Path, origin: str | None) -> str | None:
    stem = path.name
    for suffix in (".gz", ".xz", ".bz2"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem.endswith(".xml"):
        stem = stem[:-4]
    if stem in _OFFICIAL_REPOSITORIES:
        return stem
    if origin:
        for repository in _OFFICIAL_REPOSITORIES:
            if origin == repository or origin.endswith(f"-{repository}"):
                return repository
    return None


def _is_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


class AppStreamReader:
    """Read Arch Linux AppStream collection metadata without root privileges."""

    DEFAULT_METADATA_DIRS = (
        Path("/usr/share/swcatalog/xml"),
        Path("/var/lib/swcatalog/xml"),
        Path("/usr/share/app-info/xmls"),
        Path("/var/cache/app-info/xmls"),
    )

    def __init__(
        self,
        metadata_dirs: Iterable[Path] | None = None,
        *,
        icon_root: Path = Path("/usr/share/swcatalog/icons"),
        locale_preferences: Iterable[str] | None = None,
    ) -> None:
        self.metadata_dirs = tuple(Path(path) for path in (metadata_dirs or self.DEFAULT_METADATA_DIRS))
        self.icon_root = Path(icon_root)
        requested: list[str] = []
        for item in locale_preferences or ():
            key = _lang_key(item)
            if not key:
                continue
            requested.append(key)
            if "_" in key:
                requested.append(key.split("_", 1)[0])
        self.locale_preferences = tuple(dict.fromkeys(requested)) or _system_locale_preferences()

    def find_metadata_files(self) -> tuple[Path, ...]:
        found: dict[Path, None] = {}
        for directory in self.metadata_dirs:
            if not directory.is_dir():
                continue
            # Current Arch Linux files are core.xml.gz, extra.xml.gz and multilib.xml.gz.
            for repository in sorted(_OFFICIAL_REPOSITORIES):
                for suffix in _METADATA_SUFFIXES:
                    candidate = directory / f"{repository}{suffix}"
                    if candidate.is_file():
                        found[candidate.resolve()] = None
            # Also accept legacy Arch-named collection files, but not arbitrary third-party catalogs.
            for candidate in directory.glob("*arch*.xml*"):
                if candidate.is_file() and any(str(candidate).endswith(suffix) for suffix in _METADATA_SUFFIXES):
                    found[candidate.resolve()] = None
        return tuple(sorted(found, key=lambda path: str(path)))

    def read(self, metadata_files: Iterable[Path] | None = None) -> AppStreamReadResult:
        paths = tuple(Path(path) for path in (metadata_files if metadata_files is not None else self.find_metadata_files()))
        components: list[AppStreamComponent] = []
        errors: list[str] = []
        seen = 0
        desktop_seen = 0
        skipped = 0
        for path in paths:
            try:
                parsed, file_seen, file_desktop, file_skipped = self._read_file(path)
            except (OSError, EOFError, ET.ParseError, lzma.LZMAError) as exc:
                errors.append(f"{path}: {exc}")
                continue
            components.extend(parsed)
            seen += file_seen
            desktop_seen += file_desktop
            skipped += file_skipped
        return AppStreamReadResult(
            components=tuple(components),
            components_seen=seen,
            desktop_components_seen=desktop_seen,
            skipped_components=skipped,
            errors=tuple(errors),
            metadata_files=tuple(str(path) for path in paths),
        )

    def _open(self, path: Path) -> BinaryIO:
        name = path.name
        if name.endswith(".gz"):
            return gzip.open(path, "rb")
        if name.endswith(".xz"):
            return lzma.open(path, "rb")
        if name.endswith(".bz2"):
            return bz2.open(path, "rb")
        return path.open("rb")

    def _read_file(self, path: Path) -> tuple[list[AppStreamComponent], int, int, int]:
        parsed: list[AppStreamComponent] = []
        seen = 0
        desktop_seen = 0
        skipped = 0
        origin: str | None = None
        media_baseurl: str | None = None
        with self._open(path) as stream:
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start" and origin is None and _local_name(element.tag) == "components":
                    origin = element.get("origin")
                    media_baseurl = element.get("media_baseurl") or element.get("media-baseurl")
                    continue
                if event != "end" or _local_name(element.tag) != "component":
                    continue
                seen += 1
                component_type = (element.get("type") or "").strip()
                if component_type not in _ACCEPTED_COMPONENT_TYPES:
                    element.clear()
                    continue
                desktop_seen += 1
                try:
                    component = self._parse_component(
                        element,
                        path=path,
                        origin=origin,
                        media_baseurl=media_baseurl,
                    )
                except (AttributeError, TypeError, ValueError):
                    component = None
                if component is None:
                    skipped += 1
                else:
                    parsed.append(component)
                element.clear()
        return parsed, seen, desktop_seen, skipped

    def _parse_component(
        self,
        element: ET.Element,
        *,
        path: Path,
        origin: str | None,
        media_baseurl: str | None,
    ) -> AppStreamComponent | None:
        app_id = _localized_text(element, "id", self.locale_preferences)
        if not app_id:
            return None

        package_names = tuple(
            dict.fromkeys(
                text
                for node in _direct_children(element, "pkgname")
                if (text := _clean_text(node.text))
            )
        )
        name = _localized_text(element, "name", self.locale_preferences) or app_id.removesuffix(".desktop")
        summary = _localized_text(element, "summary", self.locale_preferences)
        description, description_html = _localized_description(element, self.locale_preferences)

        categories_parent = next(iter(_direct_children(element, "categories")), None)
        raw_categories = tuple(
            dict.fromkeys(
                text
                for node in (_direct_children(categories_parent, "category") if categories_parent is not None else ())
                if (text := _clean_text(node.text))
            )
        )
        categories = normalize_categories(raw_categories)

        keywords: list[str] = []
        keywords_parent = next(iter(_direct_children(element, "keywords")), None)
        if keywords_parent is not None:
            preferred_keywords = [
                node for node in _direct_children(keywords_parent, "keyword")
                if _lang_key(node.get(_XML_LANG)) in {None, *self.locale_preferences}
            ]
            for node in preferred_keywords:
                value = _clean_text(node.text)
                if value and value not in keywords:
                    keywords.append(value)

        icon, icon_type = self._select_icon(element, origin)
        screenshots = self._read_screenshots(element, media_baseurl)

        homepage = None
        bugtracker = None
        help_url = None
        for node in _direct_children(element, "url"):
            kind = node.get("type")
            value = _clean_text(node.text) or None
            if not value or not _is_url(value):
                continue
            if kind == "homepage" and homepage is None:
                homepage = value
            elif kind == "bugtracker" and bugtracker is None:
                bugtracker = value
            elif kind in {"help", "faq"} and help_url is None:
                help_url = value
        project_license = _localized_text(element, "project_license", self.locale_preferences) or None
        publisher = _component_publisher(element, self.locale_preferences)

        desktop_entry = None
        launchable_binary = None
        for node in _direct_children(element, "launchable"):
            launchable_type = node.get("type")
            value = _clean_text(node.text)
            if launchable_type == "desktop-id" and value and desktop_entry is None:
                desktop_entry = value
            elif launchable_type in {"binary", "cockpit-manifest"} and value and launchable_binary is None:
                launchable_binary = value
        provides = next(iter(_direct_children(element, "provides")), None)
        if launchable_binary is None and provides is not None:
            binary = next(iter(_direct_children(provides, "binary")), None)
            if binary is not None:
                launchable_binary = _clean_text(binary.text) or None

        issues: list[str] = []
        if not package_names:
            issues.append("missing-package-name")
        if not summary:
            issues.append("missing-summary")
        if not description:
            issues.append("missing-description")
        if not desktop_entry:
            issues.append("missing-desktop-entry")
        if icon is None:
            issues.append("missing-icon")

        return AppStreamComponent(
            app_id=app_id,
            package_names=package_names,
            name=name,
            summary=summary,
            description=description,
            description_html=description_html,
            categories=categories,
            raw_categories=raw_categories,
            keywords=tuple(keywords),
            icon=icon,
            icon_type=icon_type,
            screenshots=screenshots,
            homepage=homepage,
            bugtracker=bugtracker,
            help_url=help_url,
            license=project_license,
            desktop_entry=desktop_entry,
            launchable_binary=launchable_binary,
            source_repository=_repository_from_source(path, origin),
            source_file=str(path),
            metadata_complete=not any(issue in issues for issue in ("missing-package-name", "missing-desktop-entry")),
            metadata_issues=tuple(issues),
            publisher=publisher,
        )

    def _select_icon(self, component: ET.Element, origin: str | None) -> tuple[str | None, str | None]:
        icons = _direct_children(component, "icon")
        ranked: list[tuple[int, int, ET.Element]] = []
        type_rank = {"cached": 0, "local": 1, "stock": 2, "remote": 3}
        for icon in icons:
            icon_type = icon.get("type") or "stock"
            width = int(icon.get("width") or 0) if (icon.get("width") or "").isdigit() else 0
            ranked.append((type_rank.get(icon_type, 9), -width, icon))
        for _, _, icon in sorted(ranked, key=lambda item: (item[0], item[1])):
            value = _clean_text(icon.text)
            if not value:
                continue
            icon_type = icon.get("type") or "stock"
            if icon_type == "cached":
                return self._resolve_cached_icon(value, icon, origin), icon_type
            if icon_type == "local":
                return value, icon_type
            if icon_type == "stock":
                return value, icon_type
            if icon_type == "remote" and _is_url(value):
                return value, icon_type
        return None, None

    def _resolve_cached_icon(self, value: str, icon: ET.Element, origin: str | None) -> str:
        candidate = Path(value)
        if candidate.is_absolute():
            return str(candidate)
        if not origin:
            return value
        width = icon.get("width")
        height = icon.get("height") or width
        sizes: list[str] = []
        if width and height:
            sizes.append(f"{width}x{height}")
        sizes.extend(("128x128", "64x64", "48x48"))
        for size in dict.fromkeys(sizes):
            path = self.icon_root / origin / size / value
            if path.is_file():
                return str(path)
        # Keep the deterministic expected local path even if an icon package is partially broken.
        return str(self.icon_root / origin / (sizes[0] if sizes else "64x64") / value)

    def _read_screenshots(self, component: ET.Element, media_baseurl: str | None) -> tuple[str, ...]:
        urls: list[str] = []
        screenshots = next(iter(_direct_children(component, "screenshots")), None)
        if screenshots is None:
            return ()
        for screenshot in _direct_children(screenshots, "screenshot"):
            images = [node for node in screenshot.iter() if _local_name(node.tag) in {"image", "source-image"}]
            images.sort(key=lambda node: 0 if node.get("type") in {None, "source"} else 1)
            for image in images:
                value = _clean_text(image.text)
                if not value:
                    continue
                if media_baseurl and not _is_url(value):
                    value = urljoin(media_baseurl.rstrip("/") + "/", value.lstrip("/"))
                if value not in urls:
                    urls.append(value)
                    break
        return tuple(urls)
