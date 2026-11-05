"""Lectura de carpetas y ordenación natural, independientes de la interfaz."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from . import archive
from .i18n import tr

_DIGITS = re.compile(r"(\d+)")

# Ubicación virtual con las unidades de Windows. `<` y `>` no son válidos en una ruta de
# Windows: si esta ruta llegara por error a una operación de archivos, fallaría sin crear nada.
COMPUTER_PATH = "<computer>"
_RESULTS_PREFIX = "<resultados:"
_results: dict[str, list[str]] = {}  # las colecciones de resultados de búsqueda: no se guardan al salir
COMPUTER_LABEL = tr("Este equipo")
FOLDER_CHECK_SECONDS = 0.25


def has_computer_view() -> bool:
    return sys.platform == "win32"


def path_states(paths: list[str], timeout: float = FOLDER_CHECK_SECONDS) -> dict[str, bool | None]:
    """Si existe cada ruta. None si no contesta a tiempo: una unidad de red caída tarda
    varios segundos, y por ella no debe quedarse parado un menú. Los hilos que tarden terminan solos."""
    states: dict[str, bool] = {}

    def check(path: str) -> None:
        states[path] = path == COMPUTER_PATH or os.path.exists(path)

    unique = list(dict.fromkeys(paths))
    threads = [threading.Thread(target=check, args=(path,), daemon=True) for path in unique]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return {path: states.get(path) for path in unique}


def location_label(path: str) -> str:
    if path.startswith("sftp://"):
        from . import remote
        return remote.label(path)
    return COMPUTER_LABEL if path == COMPUTER_PATH else results_name(path) or path


def results_path(name: str) -> str:
    return f"{_RESULTS_PREFIX}{name.replace('/', '-').replace(chr(92), '-')}>"


def is_results(path: str) -> bool:
    return path.startswith(_RESULTS_PREFIX) and path.endswith(">")


def results_name(path: str) -> str:
    """El nombre de la colección. Vacío si esa ruta no es una."""
    return path[len(_RESULTS_PREFIX):-1] if is_results(path) else ""


def get_results(name: str) -> list[str]:
    return _results.get(results_name(results_path(name)), [])


def set_results(name: str, paths: list[str]) -> None:
    _results[results_name(results_path(name))] = list(paths)


def add_results(name: str, paths: list[str]) -> None:
    current = _results.setdefault(results_name(results_path(name)), [])
    known = set(current)
    current.extend(path for path in paths if path not in known)


def results_exist(path: str) -> bool:
    return is_results(path) and results_name(path) in _results


def _list_results(name: str) -> list[Entry]:
    """Lo que ya no existe deja de contar: se ha podido borrar desde la propia lista."""
    entries = []
    for path in _results.get(name, []):
        try:
            entries.append(entry_from_path(path))
        except OSError:
            location = archive.split_archive_path(path)
            if location is not None and location[1]:
                member = next((m for m in _try_members(location[0]) if m.name == location[1]), None)
                if member is not None:
                    entries.append(Entry(os.path.basename(path), path, member.is_dir, member.size, member.mtime, attrs="zip", in_archive=True))
    _results[name] = [entry.path for entry in entries]
    return entries


def _try_members(path: str):
    try:
        return archive.list_members(path)
    except OSError:
        return ()


def natural_key(name: str) -> tuple:
    parts = _DIGITS.split(name.casefold())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


@dataclass(slots=True)
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    is_link: bool = False
    attrs: str = ""
    hidden: bool = False
    display_name: str = ""
    dir_size: int | None = None
    depth: int = 0
    is_drive: bool = False
    free_space: int | None = None
    filesystem: str = ""
    drive_kind: str = ""
    is_group: bool = False  # título de un grupo en la lista, no un elemento real
    in_archive: bool = False  # está dentro de un comprimido: se puede ver y copiar, pero no cambiar
    natkey: tuple = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name
        self.natkey = natural_key(self.display_name)

    @property
    def extension(self) -> str:
        return "" if self.is_dir else os.path.splitext(self.name)[1].lstrip(".").lower()

    @property
    def effective_size(self) -> int:
        return self.dir_size if self.dir_size is not None else self.size

    @property
    def used_fraction(self) -> float | None:
        """Parte ocupada de una unidad, de 0 a 1, o None si no se conoce."""
        if not self.is_drive or not self.dir_size or self.free_space is None:
            return None
        return 1 - self.free_space / self.dir_size

    @property
    def type_label(self) -> str:
        if self.is_drive:
            from .drives import DRIVE_GROUPS
            return DRIVE_GROUPS[self.drive_kind][1] if self.drive_kind in DRIVE_GROUPS else tr("Unidad")
        if self.is_dir:
            return tr("Carpeta (enlace)") if self.is_link else tr("Carpeta")
        return tr("Archivo {upper}", upper=self.extension.upper()) if self.extension else tr("Archivo")


def _windows_attrs(st: os.stat_result) -> str:
    flags = getattr(st, "st_file_attributes", 0)
    table = ((stat.FILE_ATTRIBUTE_READONLY, "r"), (stat.FILE_ATTRIBUTE_HIDDEN, "h"),
             (stat.FILE_ATTRIBUTE_SYSTEM, "s"), (stat.FILE_ATTRIBUTE_ARCHIVE, "a"))
    return "".join(ch if flags & bit else "-" for bit, ch in table)


def _is_hidden(name: str, st: os.stat_result) -> bool:
    if sys.platform == "win32":
        return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN)
    return name.startswith(".")


def entry_from_scan(item: os.DirEntry, display_name: str = "") -> Entry:
    st = item.stat(follow_symlinks=False)
    is_link = item.is_symlink() or item.is_junction()
    is_dir = item.is_dir(follow_symlinks=True)
    attrs = _windows_attrs(st) if sys.platform == "win32" else stat.filemode(st.st_mode)[1:]
    return Entry(item.name, item.path, is_dir, 0 if is_dir else st.st_size, st.st_mtime, is_link,
                 attrs, _is_hidden(item.name, st), display_name)


def entry_from_path(path: str) -> Entry:
    st = os.lstat(path)
    is_link = stat.S_ISLNK(st.st_mode) or os.path.isjunction(path)
    is_dir = os.path.isdir(path)
    name = os.path.basename(path.rstrip("/\\")) or path
    attrs = _windows_attrs(st) if sys.platform == "win32" else stat.filemode(st.st_mode)[1:]
    return Entry(name, path, is_dir, 0 if is_dir else st.st_size, st.st_mtime, is_link, attrs,
                 _is_hidden(name, st))


def list_dir(path: str, show_hidden: bool = False, flat: bool = False, is_cancelled=None) -> list[Entry]:
    """Devuelve el contenido de `path`. `flat` recorre subcarpetas sin seguir enlaces."""
    if path == COMPUTER_PATH:
        from .drives import list_drives

        return list_drives()
    if is_results(path):
        return _list_results(results_name(path))
    if path.startswith("sftp://"):
        from . import remote
        return remote.list_remote(path, show_hidden)
    location = archive.split_archive_path(path)
    if location is not None:
        return _list_archive(*location)
    if flat:
        return _list_flat(path, show_hidden, is_cancelled)
    entries: list[Entry] = []
    with os.scandir(path) as it:
        for item in it:
            if is_cancelled and is_cancelled():
                break
            try:
                entry = entry_from_scan(item)
            except OSError:
                continue
            if show_hidden or not entry.hidden:
                entries.append(entry)
    return entries


def _list_archive(archive_path: str, inner: str) -> list[Entry]:
    base = os.path.join(archive_path, *inner.split("/")) if inner else archive_path
    return [
        Entry(m.name.rsplit("/", 1)[-1], os.path.join(base, m.name.rsplit("/", 1)[-1]), m.is_dir, m.size, m.mtime,
              attrs="zip", hidden=False, in_archive=True)
        for m in archive.list_children(archive_path, inner)
    ]


def has_visible_entries(entry: Entry, show_hidden: bool) -> bool:
    """Si una carpeta tiene algo que desplegar. Si no se puede saber, se responde que sí: al desplegarla se verá el motivo."""
    if entry.path.startswith("sftp://"):
        return True  # preguntarle al servidor por cada carpeta haría muy lento el listado
    try:
        if entry.in_archive:
            return bool(archive.list_children(*archive.split_archive_path(entry.path)))
        with os.scandir(entry.path) as it:
            for item in it:
                if show_hidden:
                    return True
                try:
                    if not _is_hidden(item.name, item.stat(follow_symlinks=False)):
                        return True
                except OSError:
                    return True
    except OSError:
        return True
    return False


def _list_flat(root: str, show_hidden: bool, is_cancelled) -> list[Entry]:
    entries: list[Entry] = []
    stack = [root]
    while stack:
        if is_cancelled and is_cancelled():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                items = list(it)
        except OSError:
            if current == root:
                raise
            continue
        for item in items:
            try:
                entry = entry_from_scan(item, os.path.relpath(item.path, root))
            except OSError:
                continue
            if entry.hidden and not show_hidden:
                continue
            if entry.is_dir:
                if not entry.is_link:
                    stack.append(item.path)
                continue
            entries.append(entry)
    return entries


SORT_KEYS = {
    "name": lambda e: e.natkey,
    "size": lambda e: (e.effective_size, e.natkey),
    "type": lambda e: (e.type_label.casefold(), e.natkey),
    "modified": lambda e: (e.mtime, e.natkey),
    "attrs": lambda e: (e.attrs, e.natkey),
    "free": lambda e: (e.free_space if e.free_space is not None else -1, e.natkey),
    "used": lambda e: (e.used_fraction if e.used_fraction is not None else -1.0, e.natkey),
    "filesystem": lambda e: (e.filesystem.casefold(), e.natkey),
    "location": lambda e: (os.path.dirname(e.path).casefold(), e.natkey),
}


def sort_entries(entries: list[Entry], column: str = "name", reverse: bool = False) -> list[Entry]:
    """Ordena con las carpetas siempre primero, con independencia del sentido."""
    key = SORT_KEYS[column]
    folders = sorted((e for e in entries if e.is_dir), key=key, reverse=reverse)
    files = sorted((e for e in entries if not e.is_dir), key=key, reverse=reverse)
    return folders + files


def matches_filter(name: str, pattern: str) -> bool:
    """Coincidencia sin distinguir mayúsculas. Sin comodines busca una subcadena."""
    if not pattern:
        return True
    pattern = pattern.casefold()
    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    return fnmatch.fnmatchcase(name.casefold(), pattern)


CONTENT_TYPES = {
    "images": (tr("Imágenes"), {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg", "ico", "heic", "avif"}),
    "audio": (tr("Audio"), {"mp3", "flac", "ogg", "opus", "wav", "m4a", "aac", "wma"}),
    "video": (tr("Vídeo"), {"mp4", "mkv", "avi", "mov", "webm", "wmv", "m4v", "mpg", "mpeg"}),
    "documents": (tr("Documentos"), {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "epub"}),
    "archives": (tr("Comprimidos"), {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "jar", "cbz"}),
    "code": (tr("Código"), {"py", "js", "ts", "c", "h", "cpp", "hpp", "rs", "go", "java", "cs", "sh", "html", "css", "json", "xml", "yml", "yaml", "toml"}),
}


def matches_content_type(entry: Entry, content_types) -> bool:
    """Basta con ser de uno de los tipos. Las carpetas siempre pasan: ocultarlas impediría navegar."""
    groups = [CONTENT_TYPES[key] for key in ((content_types,) if isinstance(content_types, str) else content_types) if key in CONTENT_TYPES]
    return not groups or entry.is_dir or any(entry.extension in extensions for _name, extensions in groups)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def format_time(timestamp: float, friendly: str = "off", today: date | None = None) -> str:
    """`friendly`: `off` siempre con fecha, `today` dice «Hoy» y `normal` también «Ayer»."""
    if not timestamp:
        return ""
    try:
        moment = datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return ""
    if friendly in ("today", "normal"):
        delta = ((today or date.today()) - moment.date()).days
        if delta == 0:
            return moment.strftime(tr("Hoy %H:%M"))
        if delta == 1 and friendly == "normal":
            return moment.strftime(tr("Ayer %H:%M"))
    return moment.strftime("%Y-%m-%d %H:%M")


def listing_text(entries: list[Entry]) -> str:
    """Contenido de una carpeta como texto separado por tabuladores."""
    lines = [tr("Nombre\tTamaño\tTipo\tModificación")]
    for entry in entries:
        size = "" if entry.is_dir and entry.dir_size is None else str(entry.effective_size)
        lines.append(f"{entry.display_name}\t{size}\t{entry.type_label}\t{format_time(entry.mtime)}")
    return "\n".join(lines) + "\n"
