"""Flatpak/Flathub integration. No shell, root, or pacman mutations."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
from typing import Callable

from src.core.command import CommandResult, run_command
from .background import create_background_executor
from .models import Application
from .search import search_matches

_WORKERS = create_background_executor(max_workers=2, thread_name_prefix="arch-manager-flatpak")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9._-]+$")
_SCOPES = ("system", "user")


class FlatpakError(RuntimeError):
    pass


@dataclass(frozen=True)
class FlatpakAction:
    operation: str
    app_id: str
    installation: str

    def argv(self) -> tuple[str, ...]:
        if self.operation not in {"install", "remove", "update"}:
            raise ValueError("Invalid Flatpak operation")
        if self.installation not in _SCOPES or not _ID_RE.fullmatch(self.app_id):
            raise ValueError("Invalid Flatpak application reference")
        command = "uninstall" if self.operation == "remove" else self.operation
        args = ["flatpak", command, "--assumeyes", "--noninteractive", f"--{self.installation}"]
        if self.operation == "install":
            args.append("flathub")
        args.append(self.app_id)
        return tuple(args)


def _rows(output: str, length: int) -> tuple[tuple[str, ...], ...]:
    rows = []
    for line in output.splitlines():
        columns = tuple(column.strip() for column in line.split("\t"))
        if len(columns) >= length and _ID_RE.fullmatch(columns[0]):
            rows.append(columns)
    return tuple(rows)


def find_flatpak_icon(app_id: str, *, scope: str = "system", origin: str = "flathub") -> str | None:
    """Find a locally cached Flatpak icon without a network fetch or shell.

    Exported icons cover installed applications; AppStream icons cached by
    ``flatpak search`` cover many not-yet-installed Flathub applications.
    """
    if not _ID_RE.fullmatch(app_id) or scope not in _SCOPES:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", origin):
        return None
    user_data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "flatpak"
    system_data = Path("/var/lib/flatpak")
    roots = (user_data, system_data) if scope == "user" else (system_data, user_data)
    arch = platform.machine() or "x86_64"
    candidates: list[Path] = []
    for root in roots:
        for size, extension in (("128x128", ".png"), ("256x256", ".png"),
                                ("64x64", ".png"), ("scalable", ".svg")):
            candidates.append(root / "exports/share/icons/hicolor" / size / "apps" / f"{app_id}{extension}")
        for size in ("128x128", "64x64"):
            candidates.append(root / "appstream" / origin / arch / "active/icons" / size / f"{app_id}.png")
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _application(app_id: str, name: str, *, scope: str, version: str = "", summary: str = "", installed: bool = False, origin: str = "flathub") -> Application:
    return Application(
        app_id=f"flatpak:{scope}:{app_id}",
        package_name=app_id,
        name=name or app_id,
        package_names=(app_id,),
        summary=summary or f"Flatpak · {origin}",
        icon=find_flatpak_icon(app_id, scope=scope, origin=origin) or app_id,
        repository=f"Flatpak · {origin}",
        available_version=version or None,
        installed_version=(version or None) if installed else None,
        installed=installed,
        metadata_source=f"flatpak:{scope}",
        desktop_entry=f"{app_id}.desktop" if installed else None,
    )


def flatpak_card_tooltip(application: Application) -> tuple[str, ...]:
    """Provide technical Flatpak metadata outside the shared catalog-card widget.

    The original store widget has a deliberate no-technical-package-fields
    contract. Only Flatpak cards request these details for their tooltip.
    """
    scope = (application.metadata_source or "").removeprefix("flatpak:")
    scope_label = "общесистемная" if scope == "system" else "пользовательская"
    installation_label = "Установка" if application.installed else "Предлагаемая установка"
    return (
        f"Flatpak ID: {application.package_name}",
        f"Источник: {application.repository or 'Flatpak'}",
        f"Версия: {application.installed_version or application.available_version or 'не указана'}",
        f"{installation_label}: {scope_label}",
    )


class FlatpakService:
    """Read-only catalog and installed state; actions are validated separately."""

    def __init__(self, *, runner: Callable[..., CommandResult] = run_command) -> None:
        self.runner = runner

    def _read(self, argv: list[str], *, timeout: float = 45) -> str:
        result = self.runner(argv, timeout=timeout)
        if not result.available:
            raise FlatpakError("Flatpak не установлен")
        if not result.ok:
            reason = (result.stderr or result.stdout).strip().splitlines()
            raise FlatpakError(reason[-1] if reason else "Не удалось получить сведения Flatpak")
        return result.stdout

    def installed(self) -> tuple[Application, ...]:
        apps = []
        errors = []
        for scope in _SCOPES:
            try:
                output = self._read(["flatpak", "list", "--app", f"--{scope}", "--columns=application:f,name:f,version:f,branch:f,origin:f,description:f"])
            except FlatpakError as exc:
                errors.append(f"{scope}: {exc}")
                continue
            for columns in _rows(output, 5):
                app_id, name, version, _branch, origin = columns[:5]
                summary = columns[5] if len(columns) > 5 else ""
                apps.append(_application(app_id, name, scope=scope, version=version,
                                         summary=summary, installed=True, origin=origin))
        # A partial read must never be shown as a verified zero-install state.
        if errors:
            raise FlatpakError("Не удалось полностью проверить установки Flatpak: " + "; ".join(errors))
        return tuple(apps)

    def installed_async(self) -> Future[tuple[Application, ...]]:
        return _WORKERS.submit(self.installed)

    def _flathub_scope(self) -> str:
        # A system remote is preferred to match KDE Discover's usual system installations.
        for scope in _SCOPES:
            try:
                output = self._read(["flatpak", "remotes", f"--{scope}", "--columns=name:f"], timeout=12)
            except FlatpakError:
                continue
            if "flathub" in output.split():
                return scope
        raise FlatpakError("Flathub не подключён. Добавьте Flathub через Discover или flatpak remote-add.")

    def search(self, query: str) -> tuple[Application, ...]:
        term = query.strip()
        if len(term) < 2:
            return ()
        scope = self._flathub_scope()
        output = self._read(["flatpak", "search", "--columns=application:f,name:f,description:f,version:f,remotes:f", term], timeout=65)
        apps = []
        found = set()
        for app_id, name, description, version, remotes, *_ in _rows(output, 5):
            if app_id in found or "flathub" not in remotes.casefold():
                continue
            if not search_matches((app_id, name, description), term):
                continue
            found.add(app_id)
            apps.append(_application(app_id, name, scope=scope, version=version, summary=description))
        return tuple(apps)

    def search_async(self, query: str) -> Future[tuple[Application, ...]]:
        return _WORKERS.submit(self.search, query)

    def update_details(self):
        # Query applications and runtimes separately: --app is retained for
        # compatibility, while --runtime discovers Mesa, VAAPI, codecs, etc.
        from src.core.updates import UpdateItem, UpdateSourceDetails

        applications = {
            (app.metadata_source.removeprefix("flatpak:"), app.package_name): app
            for app in self.installed()
        }
        updates = []
        seen: set[tuple[str, str, str]] = set()

        for scope in _SCOPES:
            runtimes: dict[tuple[str, str], str] = {}
            runtime_output = self._read(
                ["flatpak", "list", "--runtime", f"--{scope}",
                 "--columns=application:f,version:f,branch:f"], timeout=45,
            )
            for app_id, version, branch, *_ in _rows(runtime_output, 3):
                runtimes[(app_id, branch)] = version or "—"

            for kind in ("app", "runtime"):
                # Preserve the existing --app query, important for regular apps.
                if kind == "app" and not any(key[0] == scope for key in applications):
                    continue
                if kind == "runtime" and not runtimes:
                    continue
                output = self._read(
                    ["flatpak", "remote-ls", "--updates", f"--{kind}", f"--{scope}",
                     "--columns=application:f,version:f,branch:f"], timeout=75,
                )
                for app_id, version, branch, *_ in _rows(output, 3):
                    key = (scope, app_id, branch)
                    if key in seen:
                        continue
                    if kind == "app":
                        app = applications.get((scope, app_id))
                        if app is None:
                            continue
                        current = app.installed_version or "—"
                    else:
                        current = runtimes.get((app_id, branch))
                        if current is None:
                            continue
                    seen.add(key)
                    updates.append(UpdateItem(
                        app_id, current,
                        version if version and version != current else "новая ревизия",
                        "flatpak", installation=scope,
                    ))
        return UpdateSourceDetails(tuple(updates), True)


def flatpak_update_details():
    from src.core.updates import UpdateSourceDetails
    from src.core.command import command_exists
    if not command_exists("flatpak"):
        return UpdateSourceDetails((), True)
    try:
        return FlatpakService().update_details()
    except FlatpakError as exc:
        return UpdateSourceDetails((), True, str(exc))
