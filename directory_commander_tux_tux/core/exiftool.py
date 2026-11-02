"""Metadatos de un archivo mediante ExifTool, un programa aparte que el usuario instala.

Los archivos pueden ser hostiles y lo que ExifTool devuelve es solo texto para mostrar. Las versiones
anteriores a la 12.24 podían ejecutar código al leer un archivo preparado: esas no se usan.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys

from .i18n import tr
from .media_info import find_tool

MIN_VERSION = 12.24
TIMEOUT_SECONDS = 15
MAX_VALUE_LENGTH = 2000
DOWNLOAD_URL = "https://exiftool.org/"
_LANGUAGES = {"cs", "de", "en", "en_ca", "en_gb", "es", "fi", "fr", "it", "ja", "ko", "nl", "pl", "ru", "sk", "sv", "tr", "zh_cn", "zh_tw"}
_HIDDEN = {"ExifTool:ExifToolVersion"}
_usable: dict[str, float | None] = {}


class ExifToolError(Exception):
    pass


def find_exiftool(folder: str = "") -> str | None:
    """Busca en la carpeta elegida, junto al programa y en el PATH. Nunca en la carpeta que se está viendo."""
    own = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "exiftool")
    # `exiftool(-k).exe` es como se llama en la descarga oficial para Windows.
    names = ("exiftool.exe", "exiftool(-k).exe") if sys.platform == "win32" else ("exiftool",)
    for extra in (folder, own if getattr(sys, "frozen", False) else ""):
        for name in names:
            candidate = os.path.join(extra, name)
            if extra and os.path.isabs(extra) and os.path.isfile(candidate):
                return candidate
    return find_tool("exiftool")


def _launchable(tool: str) -> str:
    """Su lanzador para Windows no arranca desde una ruta con caracteres ajenos a la página de códigos del sistema."""
    if sys.platform == "win32":
        try:
            tool.encode("mbcs", "strict")
        except UnicodeEncodeError:
            short = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetShortPathNameW(tool, short, len(short)):
                return short.value
    return tool


def _run(tool: str, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # Desde su propia carpeta: nada de lo que haya en la carpeta que se está viendo debe poder cargarse.
    return subprocess.run([_launchable(tool), *args], input=stdin if stdin is not None else b"", capture_output=True, timeout=TIMEOUT_SECONDS,
                          creationflags=flags, cwd=os.path.dirname(tool) or None, check=False)


def version(tool: str) -> float | None:
    """La versión instalada, consultada una sola vez. None si no responde como ExifTool."""
    if tool not in _usable:
        try:
            _usable[tool] = float(_run(tool, ["-ver"]).stdout.decode("ascii", "replace").strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            _usable[tool] = None
    return _usable[tool]


def language_for(code: str) -> str:
    """El idioma de las descripciones. El español es el idioma base del programa."""
    wanted = (code or "es").replace("-", "_").lower()
    return wanted if wanted in _LANGUAGES else (wanted.split("_")[0] if wanted.split("_")[0] in _LANGUAGES else "en")


def _text(value) -> str:
    if isinstance(value, list):
        text = ", ".join(_text(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= MAX_VALUE_LENGTH else text[:MAX_VALUE_LENGTH] + "…"


def parse(output: bytes) -> list[tuple[str, list[tuple[str, str]]]]:
    """Convierte la salida JSON larga con grupos en [(grupo, [(descripción, valor)])], en el orden en que llega."""
    try:
        data = json.loads(output.decode("utf-8", "replace") or "[]")
    except ValueError as exc:
        raise ExifToolError(tr("ExifTool devolvió datos que no se entienden")) from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return []
    groups: dict[str, list[tuple[str, str]]] = {}
    for key, value in data[0].items():
        if key == "SourceFile" or key in _HIDDEN or ":" not in key:
            continue
        group, tag = key.split(":", 1)
        if isinstance(value, dict) and "val" in value:
            description, shown = value.get("desc") or tag, value["val"]
        else:
            description, shown = tag, value
        groups.setdefault(group, []).append((str(description), _text(shown)))
    return list(groups.items())


def read_metadata(path: str, tool: str, language: str = "es") -> list[tuple[str, list[tuple[str, str]]]]:
    """La ruta va por la entrada estándar, en UTF-8: así sirven los nombres con cualquier carácter."""
    found = version(tool)
    if found is None:
        raise ExifToolError(tr("No se pudo ejecutar ExifTool: {tool}", tool=tool))
    if found < MIN_VERSION:
        raise ExifToolError(tr("ExifTool {found} es demasiado antiguo y no es seguro: hace falta la versión {needed} o posterior", found=found, needed=MIN_VERSION))
    if "\n" in path or "\r" in path or path.startswith("-"):
        raise ExifToolError(tr("ExifTool no puede leer un archivo con este nombre"))
    try:
        done = _run(tool, ["-json", "-long", "-G1", "-lang", language, "-charset", "filename=utf8", "-@", "-"], path.encode("utf-8") + b"\n")
    except subprocess.TimeoutExpired as exc:
        raise ExifToolError(tr("ExifTool tardó demasiado")) from exc
    except OSError as exc:
        raise ExifToolError(str(exc)) from exc
    return parse(done.stdout)
