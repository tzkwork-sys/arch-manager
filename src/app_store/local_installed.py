from __future__ import annotations

from collections.abc import Callable, Iterable
from configparser import ConfigParser, Error as ConfigError
from pathlib import Path
import os

from src.core.command import CommandResult, run_command

from .aur.models import ForeignPackage
from .categories import normalize_categories
from .models import Application


_DESKTOP_DIRS = (Path('/usr/share/applications'), Path('/usr/local/share/applications'))


def _localized_value(section, key: str) -> str:
    language = (os.environ.get('LC_MESSAGES') or os.environ.get('LANG') or '').split('.', 1)[0]
    candidates: list[str] = []
    if language:
        candidates.append(f'{key}[{language}]')
        if '_' in language:
            candidates.append(f'{key}[{language.split("_", 1)[0]}]')
    candidates.extend((f'{key}[ru_RU]', f'{key}[ru]', key))
    for candidate in dict.fromkeys(candidates):
        value = str(section.get(candidate, '') or '').strip()
        if value:
            return value
    return ''


def _split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or '').split(';') if item.strip())


class LocalInstalledApplicationReader:
    """Build desktop-app records for installed foreign packages absent from AUR.

    ``AurService.installed()`` already separates confirmed AUR packages from
    foreign packages that aurweb does not know about. This reader only turns
    that latter set into normal ``Application`` cards using their installed
    ``.desktop`` files. It never installs, removes or updates packages.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_command,
        desktop_dirs: Iterable[Path] = _DESKTOP_DIRS,
    ) -> None:
        self.runner = runner
        self.desktop_dirs = tuple(Path(item).resolve() for item in desktop_dirs)

    def _desktop_path_allowed(self, path: Path) -> bool:
        try:
            resolved_parent = path.parent.resolve()
        except OSError:
            return False
        return any(resolved_parent == directory for directory in self.desktop_dirs)

    def build(self, packages: Iterable[ForeignPackage]) -> tuple[Application, ...]:
        applications: list[Application] = []
        seen_ids: set[str] = set()
        for package in packages:
            for application in self._from_package(package):
                key = application.app_id.casefold()
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                applications.append(application)
        applications.sort(key=lambda item: (item.name.casefold(), item.app_id.casefold()))
        return tuple(applications)

    def _from_package(self, package: ForeignPackage) -> tuple[Application, ...]:
        result = self.runner(['pacman', '-Qlq', package.name], timeout=20)
        if not result.ok:
            return ()

        applications: list[Application] = []
        for raw_line in result.stdout.splitlines():
            value = raw_line.strip()
            if not value.endswith('.desktop'):
                continue
            path = Path(value)
            if not path.is_absolute() or not self._desktop_path_allowed(path):
                continue
            application = self._read_desktop(path, package)
            if application is not None:
                applications.append(application)
        return tuple(applications)

    @staticmethod
    def _read_desktop(path: Path, package: ForeignPackage) -> Application | None:
        if not path.is_file():
            return None
        parser = ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            with path.open('r', encoding='utf-8', errors='replace') as handle:
                parser.read_file(handle)
        except (OSError, ConfigError):
            return None
        if not parser.has_section('Desktop Entry'):
            return None
        section = parser['Desktop Entry']
        if str(section.get('Type', 'Application')).strip() != 'Application':
            return None
        if str(section.get('Hidden', '')).strip().casefold() == 'true':
            return None
        if str(section.get('NoDisplay', '')).strip().casefold() == 'true':
            return None

        name = _localized_value(section, 'Name') or package.name
        comment = _localized_value(section, 'Comment')
        generic_name = _localized_value(section, 'GenericName')
        summary = comment or generic_name or 'Локально установленное приложение'
        raw_categories = _split_semicolon(str(section.get('Categories', '') or ''))
        categories = normalize_categories(raw_categories)
        keywords = _split_semicolon(_localized_value(section, 'Keywords'))
        icon = str(section.get('Icon', '') or '').strip() or None
        homepage = str(section.get('URL', '') or '').strip() or None
        icon_type = 'local' if icon and Path(icon).is_absolute() else 'stock'

        return Application(
            app_id=path.name,
            package_name=package.name,
            package_names=(package.name,),
            name=name,
            summary=summary,
            categories=categories,
            raw_categories=raw_categories,
            keywords=keywords,
            icon=icon,
            icon_type=icon_type,
            homepage=homepage,
            repository='Локальный пакет',
            available_version=package.installed_version,
            installed_version=package.installed_version,
            installed=True,
            update_available=False,
            desktop_entry=path.name,
            metadata_complete=False,
            metadata_issues=('foreign-package-without-appstream',),
            metadata_source=str(path),
        )
