"""Navegación de solo lectura dentro de archivos comprimidos, tratados como carpetas."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import PurePosixPath
from .i18n import tr

ZIP_SUFFIXES = (".zip", ".cbz", ".jar", ".apk", ".epub")
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
TOOL_SUFFIXES = (".rar", ".7z")  # Python no sabe leerlos: los lee bsdtar, que Windows ya trae como tar.exe
ARCHIVE_SUFFIXES = ZIP_SUFFIXES + TAR_SUFFIXES
_TOOL_LINE = re.compile(r"^([-d])\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d{1,2}:\d{2}|\d{4})\s(.*)$")
# Las fechas salen en el idioma del sistema, abreviadas. Basta con estos dos: si no, el archivo se queda sin fecha, que no es grave.
_MONTHS = {name: number for number, names in enumerate(
    (("ene", "jan"), ("feb",), ("mar",), ("abr", "apr"), ("may",), ("jun",), ("jul",), ("ago", "aug"), ("sep", "set"), ("oct",), ("nov",), ("dic", "dec")), 1) for name in names}


@dataclass(frozen=True, slots=True)
class Member:
    name: str
    is_dir: bool
    size: int
    mtime: float
    stored_name: str


@lru_cache(maxsize=1)
def _archive_tool() -> str:
    """El programa que lee RAR y 7z. Sin él, esos archivos son archivos sin más."""
    for name in ("bsdtar", "tar"):
        tool = shutil.which(name)
        if tool:
            try:
                if b"bsdtar" in _run_tool([tool, "--version"]).stdout:
                    return tool
            except OSError:
                continue
    return ""


def _run_tool(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_TOOL_ENCODING = "mbcs" if sys.platform == "win32" else "utf-8"


def has_archive_suffix(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(ARCHIVE_SUFFIXES) or (lowered.endswith(TOOL_SUFFIXES) and bool(_archive_tool()))


def is_archive_file(path: str) -> bool:
    return has_archive_suffix(path) and os.path.isfile(path)


def split_archive_path(path: str) -> tuple[str, str] | None:
    """Separa `dir/a.zip/x/y` en (`dir/a.zip`, `x/y`). None si no pasa por un comprimido."""
    current = os.path.normpath(path)
    inner: list[str] = []
    while True:
        if has_archive_suffix(current) and os.path.isfile(current):
            return current, "/".join(reversed(inner))
        parent, tail = os.path.split(current)
        if not tail or parent == current:
            return None
        inner.append(tail)
        current = parent


def is_inside_archive(path: str) -> bool:
    """Si es algo de dentro de un comprimido. El comprimido en sí es un archivo más del disco."""
    location = split_archive_path(path)
    return location is not None and bool(location[1])


def safe_member_name(raw: str) -> str | None:
    """Normaliza un nombre interno; rechaza rutas absolutas y `..` para evitar zip-slip."""
    name = raw.replace("\\", "/").strip("/")
    if not name or (len(name) > 1 and name[1] == ":"):
        return None
    if raw.startswith(("/", "\\")):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _tool_time(month: str, day: str, moment: str) -> float:
    number = _MONTHS.get(month.lower()[:3])
    if number is None:
        return 0.0
    now = datetime.now()
    try:
        if ":" in moment:  # si es reciente dice la hora en vez del año
            hour, minute = moment.split(":")
            stamp = datetime(now.year, number, int(day), int(hour), int(minute))
            return (stamp if stamp <= now else stamp.replace(year=now.year - 1)).timestamp()
        return datetime(int(moment), number, int(day)).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def _read_tool_members(path: str) -> list[Member]:
    listed = _run_tool([_archive_tool(), "-tvf", path])
    if listed.returncode != 0:
        raise OSError(tr("No se puede leer el comprimido: {exc}", exc=listed.stderr.decode(_TOOL_ENCODING, "replace").strip()))
    members = []
    for line in listed.stdout.decode(_TOOL_ENCODING, "replace").splitlines():
        match = _TOOL_LINE.match(line)
        name = safe_member_name(match.group(6)) if match else None
        if name is not None:
            members.append(Member(name, match.group(1) == "d", int(match.group(2)), _tool_time(*match.group(3, 4, 5)), match.group(6)))
    return members


def _read_members(path: str) -> list[Member]:
    found: dict[str, Member] = {}
    if path.lower().endswith(TOOL_SUFFIXES):
        found = {member.name: member for member in _read_tool_members(path)}
    elif path.lower().endswith(ZIP_SUFFIXES):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = safe_member_name(info.filename)
                if name is None:
                    continue
                try:
                    mtime = time.mktime(info.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    mtime = 0.0
                found[name] = Member(name, info.is_dir(), info.file_size, mtime, info.filename)
    else:
        with tarfile.open(path) as tf:
            for info in tf:
                name = safe_member_name(info.name)
                if name is None or not (info.isfile() or info.isdir()):
                    continue
                found[name] = Member(name, info.isdir(), info.size, float(info.mtime), info.name)
    for member in list(found.values()):
        for parent in PurePosixPath(member.name).parents:
            key = parent.as_posix()
            if key != "." and key not in found:
                found[key] = Member(key, True, 0, member.mtime, "")
    return sorted(found.values(), key=lambda m: m.name)


@lru_cache(maxsize=8)
def _cached_members(path: str, mtime_ns: int, size: int) -> tuple[Member, ...]:
    return tuple(_read_members(path))


def list_members(path: str) -> tuple[Member, ...]:
    st = os.stat(path)
    try:
        return _cached_members(path, st.st_mtime_ns, st.st_size)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        raise OSError(tr("No se puede leer el comprimido: {exc}", exc=exc)) from exc


def list_children(path: str, inner: str) -> list[Member]:
    prefix = inner + "/" if inner else ""
    children = [
        m for m in list_members(path)
        if m.name.startswith(prefix) and "/" not in m.name[len(prefix):] and m.name != inner
    ]
    if inner and not children and not any(m.name == inner and m.is_dir for m in list_members(path)):
        raise FileNotFoundError(tr("No existe dentro del comprimido: {inner}", inner=inner))
    return children


def members_under(path: str, inner: str) -> list[Member]:
    prefix = inner + "/" if inner else ""
    return [m for m in list_members(path) if not inner or m.name == inner or m.name.startswith(prefix)]


def extract_to(path: str, inner: str, dest_dir: str, on_chunk=None) -> list[str]:
    """Extrae un miembro o subárbol en dest_dir y devuelve las rutas creadas.

    `on_chunk(nbytes)` se llama por cada bloque y puede lanzar una excepción para cancelar.
    Los destinos se derivan de nombres ya validados, nunca del nombre crudo del comprimido.
    """
    selected = members_under(path, inner)
    if not selected:
        raise FileNotFoundError(tr("No existe dentro del comprimido: {inner}", inner=inner))
    base = PurePosixPath(inner).parent.as_posix() if inner else "."
    strip = 0 if base == "." else len(base) + 1
    created: list[str] = []
    opener = _member_opener(path)
    with _open_archive(path) as handle:
        for member in selected:
            target = os.path.join(dest_dir, *member.name[strip:].split("/"))
            if member.is_dir:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".detuxpart"
            try:
                with opener(handle, member) as src, open(tmp, "wb") as out:
                    while chunk := src.read(1024 * 1024):
                        out.write(chunk)
                        if on_chunk:
                            on_chunk(len(chunk))
                os.replace(tmp, target)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            if member.mtime:
                os.utime(target, (member.mtime, member.mtime))
            created.append(target)
    return created


class _ToolMember:
    """Lee un miembro de un RAR o un 7z a medida que el programa lo va sacando."""

    def __init__(self, archive_path: str, member: Member) -> None:
        # Lo que busca es un patrón: un nombre como «foto [1].jpg» no se encontraría.
        pattern = re.sub(r"([\\\[\]*?])", r"\\\1", member.stored_name)
        self._abandoned = False
        self._process = subprocess.Popen([_archive_tool(), "-xOf", archive_path, pattern], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)

    def read(self, size: int) -> bytes:
        return self._process.stdout.read(size)

    def abandon(self) -> None:
        """Ya se ha leído lo que hacía falta: que no cuente como un error que el programa no pueda seguir."""
        self._abandoned = True

    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _traceback) -> None:
        if kind is not None or self._abandoned:  # no hace falta que termine de sacarlo
            self._process.kill()
        self._process.stdout.close()
        errors = self._process.stderr.read()
        self._process.stderr.close()
        code = self._process.wait()
        if kind is None and code != 0 and not self._abandoned:
            raise OSError(tr("No se puede leer el comprimido: {exc}", exc=errors.decode(_TOOL_ENCODING, "replace").strip()))


def _member_opener(path: str):
    lowered = path.lower()
    return _ToolMember if lowered.endswith(TOOL_SUFFIXES) else _open_zip_member if lowered.endswith(ZIP_SUFFIXES) else _open_tar_member


def _member_at(member_path: str) -> tuple[str, Member]:
    location = split_archive_path(member_path)
    if location is None or not location[1]:
        raise FileNotFoundError(tr("No existe dentro del comprimido: {inner}", inner=member_path))
    for member in list_members(location[0]):
        if member.name == location[1] and not member.is_dir:
            return location[0], member
    raise FileNotFoundError(tr("No existe dentro del comprimido: {inner}", inner=location[1]))


def _read_bytes(path: str, member: Member, count: int | None = None) -> bytes:
    data = bytearray()
    with _open_archive(path) as handle, _member_opener(path)(handle, member) as source:
        while count is None or len(data) < count:
            chunk = source.read(1024 * 1024 if count is None else min(1024 * 1024, count - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        if hasattr(source, "abandon"):
            source.abandon()
    return bytes(data)


@lru_cache(maxsize=2)
def _whole_member(path: str, name: str, _mtime_ns: int) -> bytes:
    """El visor y la tarjeta suelen pedir el mismo archivo seguido: en un RAR, sacarlo otra vez se notaría."""
    return _read_bytes(path, next(member for member in list_members(path) if member.name == name))


def read_member(member_path: str, max_size: int) -> bytes | None:
    """El contenido de un archivo de dentro de un comprimido, en memoria: no se escribe nada en disco. None si pasa de `max_size`."""
    path, member = _member_at(member_path)
    if member.size > max_size:
        return None
    return _whole_member(path, member.name, os.stat(path).st_mtime_ns)


def read_member_start(member_path: str, count: int) -> bytes:
    """Solo el principio: para mirar un texto no hace falta sacarlo entero."""
    path, member = _member_at(member_path)
    return _read_bytes(path, member, count)


def _open_archive(path: str):
    if path.lower().endswith(TOOL_SUFFIXES):
        return contextlib.nullcontext(path)
    if path.lower().endswith(ZIP_SUFFIXES):
        return zipfile.ZipFile(path)
    return tarfile.open(path)


def _open_zip_member(handle: zipfile.ZipFile, member: Member):
    return handle.open(member.stored_name)


def _open_tar_member(handle: tarfile.TarFile, member: Member):
    stream = handle.extractfile(member.stored_name)
    if stream is None:
        raise OSError(tr("No se puede leer {name}", name=member.name))
    return stream


def create_zip(sources: list[str], dest_zip: str, on_chunk=None) -> None:
    """Crea un ZIP de forma atómica. No sigue enlaces simbólicos a carpetas."""
    tmp = dest_zip + ".detuxpart"
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                root_parent = os.path.dirname(os.path.abspath(src))
                for file_path in _iter_files(src):
                    arc = os.path.relpath(file_path, root_parent).replace(os.sep, "/")
                    if os.path.isdir(file_path):
                        zf.writestr(arc + "/", b"")
                        continue
                    info = zipfile.ZipInfo.from_file(file_path, arc)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(file_path, "rb") as fin, zf.open(info, "w", force_zip64=True) as fout:
                        while chunk := fin.read(1024 * 1024):
                            fout.write(chunk)
                            if on_chunk:
                                on_chunk(len(chunk))
        os.replace(tmp, dest_zip)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _iter_files(src: str):
    if not os.path.isdir(src) or os.path.islink(src):
        yield src
        return
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        if not dirs and not files:
            yield root
        for name in files:
            yield os.path.join(root, name)
