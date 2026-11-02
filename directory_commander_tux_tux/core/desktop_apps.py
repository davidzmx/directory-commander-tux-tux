"""Programas instalados en Linux que pueden abrir un archivo, según sus archivos .desktop."""
from __future__ import annotations

import configparser
import glob
import os
import shlex
from dataclasses import dataclass

_FILE_CODES = {"%f", "%F", "%u", "%U"}
_DROPPED_CODES = {"%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"}


@dataclass(frozen=True)
class DesktopApp:
    name: str
    exec_line: str
    icon: str = ""


def application_dirs() -> list[str]:
    """Por orden de prioridad: un archivo del usuario manda sobre el del sistema con el mismo nombre."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [os.path.join(folder, "applications") for folder in [home, *system.split(":")] if folder]


def _language() -> str:
    return (os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LANG") or "")[:2]


def read_desktop_file(path: str) -> tuple[DesktopApp, set[str]] | None:
    """El programa y los tipos de archivo que declara. None si no es un programa que se pueda ofrecer."""
    parser = configparser.RawConfigParser(strict=False, delimiters=("=",), comment_prefixes=("#",))
    parser.optionxform = str  # distingue `Name` de `name`
    try:
        with open(path, encoding="utf-8", errors="replace") as source:
            parser.read_file(source)
        entry = parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
        return None
    hidden = any(entry.get(key, "").lower() == "true" for key in ("Hidden", "NoDisplay"))
    if hidden or entry.get("Type") != "Application" or not entry.get("Exec") or not entry.get("Name"):
        return None
    name = entry.get(f"Name[{_language()}]") or entry["Name"]
    types = {kind for kind in entry.get("MimeType", "").split(";") if kind}
    return DesktopApp(name, entry["Exec"], entry.get("Icon", "")), types


def apps_for(mime_types: list[str], folders: list[str] | None = None) -> list[DesktopApp]:
    """Los programas que declaran alguno de esos tipos, sin repetir y por orden alfabético."""
    wanted = set(mime_types)
    seen: set[str] = set()
    found: list[DesktopApp] = []
    for folder in application_dirs() if folders is None else folders:
        for path in sorted(glob.glob(os.path.join(glob.escape(folder), "*.desktop"))):
            file_name = os.path.basename(path)
            if file_name in seen:
                continue
            seen.add(file_name)
            app = read_desktop_file(path)
            if app is not None and app[1] & wanted:
                found.append(app[0])
    return sorted(found, key=lambda app: app.name.casefold())


def command_for(app: DesktopApp, path: str) -> list[str]:
    """La orden que abre el archivo, según la línea `Exec` del programa."""
    command: list[str] = []
    placed = False
    for token in shlex.split(app.exec_line):
        if token in _FILE_CODES:
            command.append(path)
            placed = True
        elif token not in _DROPPED_CODES:
            command.append(token.replace("%%", "%"))
    return command if placed else [*command, path]
