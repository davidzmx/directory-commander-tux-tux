"""Operaciones de archivos: copiar, mover, borrar, tamaños y hashes."""
from __future__ import annotations

import enum
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

from . import archive, remote
from .i18n import tr

CHUNK = 1024 * 1024
PART_SUFFIX = ".detuxpart"
HASH_ALGORITHMS = ("md5", "sha1", "sha256", "sha512", "blake2b")
PAUSE_POLL_S = 0.05
REPORT_EVERY_S = 0.05  # a gigabytes por segundo, avisar por cada fragmento saturaría la interfaz


class Collision(enum.Enum):
    REPLACE = "replace"
    SKIP = "skip"
    RENAME = "rename"
    UPDATE_ALL = "update_all"            # lo nuevo, y lo existente solo si el origen es más reciente
    UPDATE_EXISTING = "update_existing"  # solo lo que ya existe, si el origen es más reciente
    RENAME_OLD = "rename_old"            # el existente pasa a otro nombre y el nuevo ocupa el suyo
    ASK = "ask"                          # se pregunta por cada archivo


class Action(enum.Enum):
    """Lo que se puede contestar cuando un archivo ya existe en el destino."""
    REPLACE = "replace"
    RENAME_NEW = "rename_new"
    RENAME_OLD = "rename_old"
    KEEP_NEWER = "keep_newer"
    SKIP = "skip"
    SKIP_IDENTICAL = "skip_identical"


_POLICY_OF = {Action.REPLACE: Collision.REPLACE, Action.RENAME_NEW: Collision.RENAME, Action.RENAME_OLD: Collision.RENAME_OLD,
              Action.KEEP_NEWER: Collision.UPDATE_ALL, Action.SKIP: Collision.SKIP}


@dataclass(frozen=True)
class Answer:
    action: Action
    to_all: bool = False  # también para los que falten por preguntar
    name: str | None = None  # con RENAME_NEW, el nombre elegido; sin él se busca uno libre


MTIME_TOLERANCE = 2.0  # FAT y exFAT guardan la fecha con resolución de 2 segundos


def _is_newer(src: str, dest: str) -> bool:
    try:
        return os.lstat(src).st_mtime > os.lstat(dest).st_mtime + MTIME_TOLERANCE
    except OSError:  # p. ej. un miembro de un comprimido, que no tiene fecha que comparar: mejor no pisar nada
        return False


def _is_identical(src: str, dest: str) -> bool:
    """Por tamaño y fecha, como al comparar carpetas: leer los dos archivos enteros por cada conflicto sería muy lento."""
    try:
        first, second = os.lstat(src), os.lstat(dest)
    except OSError:
        return False
    return first.st_size == second.st_size and abs(first.st_mtime - second.st_mtime) <= MTIME_TOLERANCE


class Cancelled(Exception):
    pass


class _SkipFile(Exception):
    """Se pidió dejar de copiar el archivo actual y seguir con el siguiente."""


@dataclass
class TransferState:
    """Cómo va una copia. La escribe el hilo de trabajo y la lee la ventana de progreso cuando quiere,
    en vez de recibir un aviso por cada fragmento copiado."""
    ready: bool = False  # aún se está midiendo lo que hay que copiar
    total_bytes: int = 0
    total_items: int = 0
    bytes_done: int = 0
    items_done: int = 0
    current_name: str = ""
    current_size: int = 0
    current_done: int = 0
    skip_requested: bool = False


class ProgressSink:
    """Puente entre la operación y quien la observa. Sirve también para cancelar, pausar y omitir."""

    def __init__(self, on_progress=None) -> None:
        self._on_progress = on_progress
        self._cancel = threading.Event()
        self._paused = threading.Event()
        self.transfer: TransferState | None = None

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def skip_current(self) -> None:
        if self.transfer is not None:
            self.transfer.skip_requested = True

    def check(self) -> None:
        """Lo que hace el trabajo entre fragmentos: aquí es donde se detiene, se pausa o se omite."""
        while self._paused.is_set() and not self._cancel.is_set() and not (self.transfer and self.transfer.skip_requested):
            time.sleep(PAUSE_POLL_S)
        if self._cancel.is_set():
            raise Cancelled()

    def report(self, done: int, total: int, name: str) -> None:
        if self._on_progress:
            self._on_progress(done, total, name)


@dataclass
class OpResult:
    done: int = 0
    skipped: int = 0
    cancelled: bool = False
    errors: list[tuple[str, str]] = field(default_factory=list)
    unattended: bool = False  # los errores solo se anotan: al terminar no se muestra ningún aviso
    # (origen, destino final, si el destino es nuevo): solo lo nuevo se puede deshacer sin riesgo.
    placed: list[tuple[str, str, bool]] = field(default_factory=list)
    # (nombre que tenía, nombre que tiene): archivos existentes apartados para dejar sitio al nuevo.
    renamed: list[tuple[str, str]] = field(default_factory=list)
    summary: str = ""  # lo que se dice al terminar, en lugar de «terminado»

    @property
    def ok(self) -> bool:
        return not self.errors and not self.cancelled


def _is_real_dir(path: str) -> bool:
    return os.path.isdir(path) and not os.path.islink(path) and not os.path.isjunction(path)


def tree_size(path: str, sink: ProgressSink | None = None) -> tuple[int, int]:
    """(bytes, archivos) de un elemento. No sigue enlaces y omite lo que no se puede leer."""
    if remote.is_remote(path):
        from . import remote_ops
        return remote_ops.tree_size(path, sink)
    location = archive.split_archive_path(path)
    if location is not None:
        members = [m for m in archive.members_under(*location) if not m.is_dir]
        return sum(m.size for m in members), len(members)
    try:
        st = os.lstat(path)
    except OSError:
        return 0, 0
    if not _is_real_dir(path):
        return (0 if stat.S_ISLNK(st.st_mode) else st.st_size), 1
    total, count, _complete = _walk_size(path, sink)
    return total, count


def tree_size_within(path: str, seconds: float, sink: ProgressSink | None = None) -> tuple[int, bool]:
    """(bytes, si dio tiempo a contarlo todo). Para mostrar un dato sin esperar a recorrer un disco entero."""
    total, _count, complete = _walk_size(path, sink, time.monotonic() + seconds)
    return total, complete


def _walk_size(path: str, sink: ProgressSink | None, deadline: float | None = None) -> tuple[int, int, bool]:
    total = count = 0
    stack = [path]
    while stack:
        if sink:
            sink.check()
        if deadline is not None and time.monotonic() >= deadline:
            return total, count, False
        try:
            with os.scandir(stack.pop()) as it:
                for item in it:
                    try:
                        if item.is_symlink() or item.is_junction():
                            count += 1
                        elif item.is_dir(follow_symlinks=False):
                            stack.append(item.path)
                        else:
                            total += item.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count, True


def unique_name(dest_dir: str, name: str) -> str:
    """Ruta libre en dest_dir: `nombre (2).ext`, `nombre (3).ext`..."""
    candidate = os.path.join(dest_dir, name)
    if not os.path.lexists(candidate):
        return candidate
    stem, ext = _split_name(name)
    index = 2
    while True:
        candidate = os.path.join(dest_dir, f"{stem} ({index}){ext}")
        if not os.path.lexists(candidate):
            return candidate
        index += 1


def _split_name(name: str) -> tuple[str, str]:
    lower = name.lower()
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix):]
    return os.path.splitext(name)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_inside(child: str, parent: str) -> bool:
    child_n = os.path.normcase(os.path.abspath(child))
    parent_n = os.path.normcase(os.path.abspath(parent))
    return child_n == parent_n or child_n.startswith(parent_n.rstrip(os.sep) + os.sep)


def same_volume(path: str, folder: str) -> bool:
    try:
        return os.lstat(path).st_dev == os.stat(folder).st_dev
    except OSError:
        return False


def can_move(paths: list[str]) -> bool:
    """De dentro de un comprimido solo se puede copiar, y la raíz de una unidad no se mueve."""
    return not any(not remote.is_remote(path) and (archive.is_inside_archive(path) or is_filesystem_root(path)) for path in paths)


def is_executable_target(path: str) -> bool:
    """Si una ruta es un ejecutable sobre el que se pueden soltar archivos para abrirlos."""
    if not path or not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if sys.platform == "win32":
        return ext in ("exe", "bat", "cmd", "com", "ps1")
    # Un programa o un script que se puede ejecutar. El permiso no basta: en un disco FAT, exFAT o NTFS lo tienen todos los archivos.
    if not os.access(path, os.X_OK):
        return False
    try:
        with open(path, "rb") as source:
            start = source.read(4)
    except OSError:
        return False
    return start == b"\x7fELF" or start.startswith(b"#!")


def drop_operation(paths: list[str], dest_dir: str, force_move: bool = False, force_copy: bool = False) -> str | None:
    """Qué hace soltar esos elementos en una carpeta: "move", "copy", "open_with" o None si no hay nada que hacer.
    Como en cualquier explorador: en la misma unidad se mueve y a otra unidad se copia, salvo que se pida lo contrario."""
    if is_executable_target(dest_dir):
        if not paths or any(_same_path(path, dest_dir) for path in paths):
            return None
        return "open_with"
    if remote.is_remote(dest_dir) or any(remote.is_remote(path) for path in paths):
        from . import remote_ops
        operation = remote_ops.drop_operation(paths, dest_dir, force_move)
        return "copy" if operation and force_copy else operation
    if not paths or not os.path.isdir(dest_dir) or archive.split_archive_path(dest_dir) is not None:
        return None
    if all(_same_path(os.path.dirname(path.rstrip("/\\")), dest_dir) for path in paths):
        return None
    if any(_is_real_dir(path) and is_inside(dest_dir, path) for path in paths):
        return None
    if force_copy or not can_move(paths):
        return "copy"
    if force_move:
        return "move"
    return "move" if all(same_volume(path, dest_dir) for path in paths) else "copy"


class _Transfer:
    def __init__(self, move: bool, policy: Collision, sink: ProgressSink, total: int, result: OpResult,
                 state: TransferState | None = None, ask=None, verify: bool = False) -> None:
        self.move, self.policy, self.sink, self.total, self.result = move, policy, sink, total, result
        self.verify = verify
        self.ask = ask
        self.skip_identical = False
        self.state = state or TransferState()
        self.bytes_done = 0
        self.current = ""
        self.final_dest = ""
        self._last_report = 0.0

    def add(self, nbytes: int) -> None:
        self.bytes_done += nbytes
        self.state.bytes_done = self.bytes_done
        self.state.current_done += nbytes
        now = time.monotonic()
        if now - self._last_report >= REPORT_EVERY_S or self.bytes_done >= self.total:
            self._last_report = now
            self.sink.report(self.bytes_done, self.total, self.current)

    def _begin_item(self, name: str, size: int) -> None:
        self.current = name
        state = self.state
        state.current_name, state.current_size, state.current_done, state.skip_requested = name, size, 0, False

    def _count_without_copying(self, src: str) -> None:
        """Lo que no se copia también cuenta como hecho: si no, las barras nunca llegarían al final."""
        size, count = tree_size(src)
        self._begin_item(os.path.basename(src), size)
        self.add(size)
        self.state.items_done += count

    def chunk(self, nbytes: int) -> None:
        self.sink.check()
        self.add(nbytes)

    def _place_top(self, src: str, dest: str) -> None:
        existed = os.path.lexists(dest)
        if self._place(src, dest):
            created = not existed or self.final_dest != dest or any(original == dest for original, _moved in self.result.renamed)
            self.result.placed.append((src, self.final_dest, created))

    def run_source(self, src: str, dest_dir: str, new_name: str | None = None) -> None:
        name = new_name or os.path.basename(src.rstrip("/\\"))
        self.current = name
        if archive.split_archive_path(dest_dir) is not None:
            raise OSError(tr("No se puede escribir dentro de un comprimido"))
        if not os.path.isdir(dest_dir):
            raise OSError(tr("El destino no es una carpeta: {dest_dir}", dest_dir=dest_dir))
        if self.move and is_filesystem_root(src):
            raise OSError(tr("No se puede mover la raíz de una unidad"))
        location = archive.split_archive_path(src)
        if location is not None and location[1]:
            self._extract(src, location, dest_dir, name)
            return
        if not os.path.lexists(src):
            raise FileNotFoundError(tr("No existe: {src}", src=src))
        if _is_real_dir(src) and is_inside(dest_dir, src):
            raise OSError(tr("No se puede copiar o mover una carpeta dentro de sí misma"))
        if _same_path(os.path.dirname(src.rstrip("/\\")), dest_dir) and new_name is None:
            if self.move:
                self.result.skipped += 1
                return
            self._place_top(src, unique_name(dest_dir, name))
            return
        self._place_top(src, os.path.join(dest_dir, name))

    def _extract(self, src: str, location: tuple[str, str], dest_dir: str, name: str) -> None:
        size, count = tree_size(src)
        self._begin_item(name, size)
        natural = os.path.basename(location[1].rstrip("/"))
        wanted = os.path.join(dest_dir, name)
        final = self._resolve(src, wanted) if os.path.lexists(wanted) and not _is_real_dir(wanted) else wanted
        if final is None:
            self.result.skipped += 1
            self.add(size)
            self.state.items_done += count
            return
        if os.path.normcase(final) == os.path.normcase(os.path.join(dest_dir, natural)):
            archive.extract_to(*location, dest_dir, self.chunk)
        else:  # extraer no sabe de otros nombres: se saca aparte y luego se le da el suyo
            holder = tempfile.mkdtemp(prefix=".detux", dir=dest_dir)
            try:
                archive.extract_to(*location, holder, self.chunk)
                os.replace(os.path.join(holder, natural), final)
            finally:
                shutil.rmtree(holder, ignore_errors=True)
        self.state.items_done += count
        self.result.done += 1

    def _resolve(self, src: str, dest: str) -> str | None:
        """Adónde va un archivo cuyo destino ya existe. None: no se copia."""
        policy = self.policy
        if policy is Collision.ASK:
            if self.skip_identical and _is_identical(src, dest):
                return None
            answer = self.ask(src, dest)
            if answer is None:
                raise Cancelled()
            if answer.action is Action.SKIP_IDENTICAL:
                self.skip_identical = True
                return self._resolve(src, dest)  # si este no es idéntico, se vuelve a preguntar por él
            policy = _POLICY_OF[answer.action]
            if answer.to_all:
                self.policy = policy
            if answer.action is Action.RENAME_NEW and answer.name:
                chosen = os.path.join(os.path.dirname(dest), answer.name)
                return self._resolve(src, chosen) if os.path.lexists(chosen) else chosen
        if policy is Collision.SKIP:
            return None
        if policy is Collision.RENAME:
            return unique_name(os.path.dirname(dest), os.path.basename(dest))
        if policy is Collision.RENAME_OLD:
            moved = unique_name(os.path.dirname(dest), os.path.basename(dest))
            os.rename(dest, moved)
            self.result.renamed.append((dest, moved))
            return dest
        if policy in (Collision.UPDATE_ALL, Collision.UPDATE_EXISTING) and not _is_newer(src, dest):
            return None
        return dest

    def _undo_rename_old(self, dest: str) -> None:
        """Si el nuevo no llegó a su sitio, el existente recupera su nombre."""
        for pair in reversed(self.result.renamed):
            if pair[0] == dest and not os.path.lexists(dest):
                os.rename(pair[1], dest)
                self.result.renamed.remove(pair)
                return

    def _place(self, src: str, dest: str) -> bool:
        """Coloca src en dest. Devuelve False si algo quedó sin mover o copiar."""
        self.sink.check()
        self.current = os.path.basename(src)
        src_is_dir = _is_real_dir(src)
        if os.path.lexists(dest):
            dest_is_dir = _is_real_dir(dest)
            if src_is_dir != dest_is_dir:
                self.result.errors.append((src, tr("El destino existe y es de otro tipo (carpeta/archivo)")))
                return False
            if not src_is_dir:
                dest = self._resolve(src, dest)
                if dest is None:
                    self.result.skipped += 1
                    self._count_without_copying(src)
                    return False
        elif self.policy is Collision.UPDATE_EXISTING:
            self.result.skipped += 1
            self._count_without_copying(src)
            return False
        if self.move and not os.path.lexists(dest):
            try:
                size, count = tree_size(src)
                os.rename(src, dest)
                self._begin_item(os.path.basename(src), size)
                self.add(size)
                self.state.items_done += count
                self.result.done += 1
                self.final_dest = dest
                return True
            except OSError:
                pass  # p. ej. otro volumen: se copia y luego se borra el origen
        try:
            ok = self._copy_dir(src, dest) if src_is_dir else self._copy_leaf(src, dest)
        except Cancelled:
            self._undo_rename_old(dest)
            raise
        if not ok:
            self._undo_rename_old(dest)
        if ok and self.move:
            try:
                remove_tree(src)
            except OSError as exc:
                self.result.errors.append((src, tr("Copiado, pero no se pudo borrar el origen: {exc}", exc=exc)))
                return False
        self.final_dest = dest
        return ok

    def _copy_dir(self, src: str, dest: str) -> bool:
        try:
            os.makedirs(dest, exist_ok=True)
            with os.scandir(src) as it:
                children = [item.name for item in it]
        except OSError as exc:
            self.result.errors.append((src, str(exc)))
            return False
        ok = True
        for child in children:
            try:
                ok = self._place(os.path.join(src, child), os.path.join(dest, child)) and ok
            except OSError as exc:
                self.result.errors.append((os.path.join(src, child), str(exc)))
                ok = False
        try:
            shutil.copystat(src, dest)
        except OSError:
            pass
        if ok:
            self.result.done += 1
        return ok

    def _copy_leaf(self, src: str, dest: str) -> bool:
        try:
            if os.path.islink(src):
                if os.path.lexists(dest):
                    os.unlink(dest)
                os.symlink(os.readlink(src), dest)
            else:
                self._copy_file(src, dest)
        except _SkipFile:
            self.add(max(0, self.state.current_size - self.state.current_done))
            self.state.items_done += 1
            self.result.skipped += 1
            return False
        except OSError as exc:
            self.state.items_done += 1
            self.result.errors.append((src, str(exc)))
            return False
        self.state.items_done += 1
        self.result.done += 1
        return True

    def _copy_file(self, src: str, dest: str) -> None:
        if os.path.exists(dest) and os.path.samefile(src, dest):
            return
        tmp = dest + PART_SUFFIX
        self._begin_item(os.path.basename(src), os.path.getsize(src))
        original = hashlib.blake2b() if self.verify else None
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    self.sink.check()
                    if self.state.skip_requested:
                        raise _SkipFile()
                    fout.write(chunk)
                    if original is not None:
                        original.update(chunk)
                    self.add(len(chunk))
                if original is not None:
                    fout.flush()
                    os.fsync(fout.fileno())  # que esté en el disco, para releer lo que de verdad quedó escrito
            if original is not None and _written_hash(tmp, self.sink) != original.digest():
                raise OSError(tr("La copia no es igual al original: se ha descartado"))
            try:
                shutil.copystat(src, tmp)
            except OSError:
                pass  # algunos sistemas de archivos no conservan estos metadatos
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def _written_hash(path: str, sink: ProgressSink) -> bytes:
    """El hash de lo escrito, leído del disco: en Linux se descarta antes la copia que guarda el sistema en memoria, que daría
    siempre lo que se acaba de escribir aunque el disco hubiera fallado."""
    digest = hashlib.blake2b()
    with open(path, "rb") as fin:
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fin.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        while chunk := fin.read(CHUNK):
            sink.check()
            digest.update(chunk)
    return digest.digest()


def restore_renamed(pairs: list[tuple[str, str]], result: OpResult) -> None:
    """Al deshacer: los archivos apartados vuelven a su nombre, si sigue libre."""
    for original, moved in pairs:
        try:
            if os.path.lexists(moved) and not os.path.lexists(original):
                os.rename(moved, original)
        except OSError as exc:
            result.errors.append((moved, str(exc)))


def transfer(sources: list[str], dest_dir: str, move: bool = False,
             policy: Collision = Collision.RENAME, sink: ProgressSink | None = None,
             new_name: str | None = None, ask=None, verify: bool = False) -> OpResult:
    """Copia o mueve. Un archivo se publica en el destino solo cuando está completo.

    `verify` relee cada copia y la compara con el original (BLAKE2b): si no son iguales, la copia se descarta y, al mover, el
    original se conserva. No se aplica con un servidor por medio ni al extraer de un comprimido.

    `new_name` da otro nombre en el destino y exige un único origen.
    `ask(origen, destino)`, con Collision.ASK, se llama desde este hilo y devuelve un Answer, o None para cancelar.
    """
    if remote.is_remote(dest_dir) or any(remote.is_remote(src) for src in sources):
        from . import remote_ops
        return remote_ops.transfer(sources, dest_dir, move, policy, sink, new_name, ask)
    if new_name is not None and len(sources) != 1:
        raise ValueError(tr("Solo se puede dar otro nombre a un elemento cada vez"))
    sink = sink or ProgressSink()
    result = OpResult()
    state = sink.transfer = TransferState()
    if move:
        # Se rechaza antes de medir: medir la raíz recorrería el disco entero, y todo lo montado en él, para nada.
        roots = [src for src in sources if is_filesystem_root(src)]
        result.errors.extend((src, tr("No se puede mover la raíz de una unidad")) for src in roots)
        sources = [src for src in sources if src not in roots]
        if not sources:
            state.ready = True
            return result
    try:
        sizes = [tree_size(s, sink) for s in sources]
        total = sum(size for size, _count in sizes)
        state.total_bytes, state.total_items, state.ready = total, sum(count for _size, count in sizes), True
        job = _Transfer(move, policy, sink, total, result, state, ask, verify)
        for src in sources:
            try:
                job.run_source(src, dest_dir, new_name)
            except OSError as exc:
                result.errors.append((src, str(exc)))
    except Cancelled:
        result.cancelled = True
    return result


def is_filesystem_root(path: str) -> bool:
    absolute = os.path.abspath(path)
    return os.path.dirname(absolute) == absolute


def remove_tree(path: str) -> None:
    """Borra sin pasar por la papelera. Un enlace se elimina sin tocar su destino."""
    if is_filesystem_root(path):
        raise PermissionError(tr("No se puede eliminar la raíz de una unidad: {path}", path=path))
    if os.path.isjunction(path):
        os.rmdir(path)
    elif os.path.islink(path):
        try:
            os.unlink(path)
        except IsADirectoryError:
            os.rmdir(path)
        except PermissionError:
            os.rmdir(path)  # enlace simbólico a carpeta en Windows
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def delete_paths(paths: list[str], permanent: bool = False, trash=None, sink: ProgressSink | None = None) -> OpResult:
    """Borra rutas. Si la papelera falla informa del error; nunca borra en definitiva por su cuenta."""
    if any(remote.is_remote(path) for path in paths):
        if not permanent:
            raise OSError(tr("En un servidor no hay papelera: solo se puede eliminar definitivamente"))
        from . import remote_ops
        return remote_ops.delete_paths(paths, sink)
    sink = sink or ProgressSink()
    result = OpResult()
    for index, path in enumerate(paths):
        if sink.is_cancelled:
            result.cancelled = True
            break
        sink.report(index, len(paths), os.path.basename(path))
        if archive.split_archive_path(path) is not None and not archive.is_archive_file(path):
            result.errors.append((path, tr("No se puede borrar dentro de un comprimido")))
            continue
        if is_filesystem_root(path):
            result.errors.append((path, tr("No se puede eliminar la raíz de una unidad")))
            continue
        try:
            if permanent:
                remove_tree(path)
            elif trash is None or not trash(path):
                result.errors.append((path, tr("No se pudo enviar a la papelera")))
                continue
            result.done += 1
        except OSError as exc:
            result.errors.append((path, str(exc)))
    return result


def split_file(path: str, dest_dir: str, part_size: int, sink: ProgressSink | None = None) -> OpResult:
    """Divide un archivo en `nombre.001`, `nombre.002`… sin tocar el original."""
    if part_size <= 0:
        raise ValueError(tr("El tamaño de cada parte debe ser mayor que cero"))
    sink = sink or ProgressSink()
    result = OpResult()
    total = os.path.getsize(path)
    base = os.path.basename(path)
    done = 0
    created: list[str] = []
    try:
        with open(path, "rb") as fin:
            index = 1
            while True:
                remaining = part_size
                target = os.path.join(dest_dir, f"{base}.{index:03d}")
                if os.path.lexists(target):
                    raise FileExistsError(tr("Ya existe: {basename}", basename=os.path.basename(target)))
                chunk = fin.read(min(CHUNK, remaining))
                if not chunk and index > 1:
                    break
                created.append(target)
                with open(target, "wb") as fout:
                    while chunk:
                        sink.check()
                        fout.write(chunk)
                        done += len(chunk)
                        remaining -= len(chunk)
                        sink.report(done, total, os.path.basename(target))
                        chunk = fin.read(min(CHUNK, remaining)) if remaining else b""
                result.done += 1
                index += 1
                if done >= total:
                    break
    except Cancelled:
        result.cancelled = True
        _remove_quietly(created)
    except OSError as exc:
        result.errors.append((path, str(exc)))
        _remove_quietly(created)
    if not result.ok:
        result.done = 0
    return result


def _remove_quietly(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def split_sequence(first_part: str) -> list[str]:
    """Partes consecutivas de una serie a partir de `nombre.001`. Vacío si no es una serie."""
    stem, ext = os.path.splitext(first_part)
    if not (len(ext) == 4 and ext[1:].isdigit()):
        return []
    parts: list[str] = []
    index = int(ext[1:])
    while os.path.isfile(f"{stem}.{index:03d}"):
        parts.append(f"{stem}.{index:03d}")
        index += 1
    return parts


def join_files(parts: list[str], target: str, sink: ProgressSink | None = None) -> OpResult:
    """Une partes en un archivo nuevo. El destino solo aparece si la unión termina bien."""
    sink = sink or ProgressSink()
    result = OpResult()
    if os.path.lexists(target):
        result.errors.append((target, tr("El destino ya existe")))
        return result
    total = sum(os.path.getsize(p) for p in parts)
    done = 0
    tmp = target + PART_SUFFIX
    try:
        with open(tmp, "wb") as fout:
            for part in parts:
                with open(part, "rb") as fin:
                    while chunk := fin.read(CHUNK):
                        sink.check()
                        fout.write(chunk)
                        done += len(chunk)
                        sink.report(done, total, os.path.basename(part))
        os.replace(tmp, target)
        result.done = len(parts)
        result.placed.append((parts[0], target, True))
    except Cancelled:
        result.cancelled = True
        _remove_quietly([tmp])
    except OSError as exc:
        result.errors.append((target, str(exc)))
        _remove_quietly([tmp])
    return result


def make_link(source: str, dest_dir: str, kind: str = "softlink") -> str:
    """Crea un enlace en dest_dir. En Windows, el simbólico puede requerir permisos especiales."""
    if archive.is_inside_archive(source) or archive.split_archive_path(dest_dir) is not None:
        raise OSError(tr("No se pueden crear enlaces con elementos de un comprimido"))
    target = unique_name(dest_dir, os.path.basename(source.rstrip("/\\")))
    if kind == "hardlink":
        os.link(source, target)
    elif kind in ("softlink", "auto", "autonosoft"):
        os.symlink(os.path.abspath(source), target, target_is_directory=os.path.isdir(source))
    elif kind == "relsoftlink":
        os.symlink(os.path.relpath(source, dest_dir), target, target_is_directory=os.path.isdir(source))
    else:
        raise ValueError(tr("Tipo de enlace no admitido: {kind}", kind=kind))
    return target


def get_attributes(path: str) -> dict[str, bool]:
    """Atributos modificables: `rhsa` en Windows y permisos `rwx` en POSIX."""
    st = os.lstat(path)
    if os.name == "nt":
        flags = getattr(st, "st_file_attributes", 0)
        return {"r": bool(flags & stat.FILE_ATTRIBUTE_READONLY), "h": bool(flags & stat.FILE_ATTRIBUTE_HIDDEN),
                "s": bool(flags & stat.FILE_ATTRIBUTE_SYSTEM), "a": bool(flags & stat.FILE_ATTRIBUTE_ARCHIVE)}
    return {name: bool(st.st_mode & bit) for name, bit in _POSIX_BITS.items()}


_POSIX_BITS = {"ur": stat.S_IRUSR, "uw": stat.S_IWUSR, "ux": stat.S_IXUSR, "gr": stat.S_IRGRP, "gw": stat.S_IWGRP,
               "gx": stat.S_IXGRP, "or": stat.S_IROTH, "ow": stat.S_IWOTH, "ox": stat.S_IXOTH}
_WINDOWS_BITS = {"r": stat.FILE_ATTRIBUTE_READONLY, "h": stat.FILE_ATTRIBUTE_HIDDEN, "s": stat.FILE_ATTRIBUTE_SYSTEM,
                 "a": stat.FILE_ATTRIBUTE_ARCHIVE}


def set_attributes(path: str, changes: dict[str, bool]) -> None:
    """Aplica solo las claves indicadas y conserva el resto de atributos."""
    st = os.lstat(path)
    if os.name == "nt":
        import ctypes

        flags = getattr(st, "st_file_attributes", 0)
        for key, value in changes.items():
            flags = flags | _WINDOWS_BITS[key] if value else flags & ~_WINDOWS_BITS[key]
        keep = stat.FILE_ATTRIBUTE_ARCHIVE | stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_READONLY | stat.FILE_ATTRIBUTE_SYSTEM
        value = flags & keep or stat.FILE_ATTRIBUTE_NORMAL
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.SetFileAttributesW(ctypes.c_wchar_p(path), value):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    mode = stat.S_IMODE(st.st_mode)
    for key, value in changes.items():
        mode = mode | _POSIX_BITS[key] if value else mode & ~_POSIX_BITS[key]
    os.chmod(path, mode)


def hash_file(path: str, algorithm: str = "sha256", sink: ProgressSink | None = None) -> str:
    if algorithm not in HASH_ALGORITHMS:
        raise ValueError(tr("Algoritmo no admitido: {algorithm}", algorithm=algorithm))
    digest = hashlib.new(algorithm)
    with open(path, "rb") as fin:
        while chunk := fin.read(CHUNK):
            if sink:
                sink.check()
            digest.update(chunk)
    return digest.hexdigest()


_ILLEGAL_WINDOWS = set('<>:"/\\|?*')


def validate_name(name: str) -> str | None:
    """Devuelve el motivo por el que un nombre no es válido, o None si lo es."""
    if not name or name in (".", ".."):
        return tr("El nombre está vacío o está reservado")
    if "/" in name or "\\" in name or "\0" in name:
        return tr("El nombre no puede contener separadores de ruta")
    if os.name == "nt":
        if any(ch in _ILLEGAL_WINDOWS or ord(ch) < 32 for ch in name):
            return tr("El nombre no puede contener < > : \" | ? *")
        if name.endswith((" ", ".")):
            return tr("En Windows el nombre no puede terminar en espacio o punto")
    return None


def create_folder(parent: str, name: str) -> str:
    reason = validate_name(name)
    if reason:
        raise ValueError(reason)
    if remote.is_remote(parent):
        from . import remote_ops
        return remote_ops.create_folder(parent, name)
    path = os.path.join(parent, name)
    os.mkdir(path)
    return path


def create_file(parent: str, name: str) -> str:
    reason = validate_name(name)
    if reason:
        raise ValueError(reason)
    if remote.is_remote(parent):
        from . import remote_ops
        return remote_ops.create_file(parent, name)
    path = os.path.join(parent, name)
    with open(path, "x", encoding="utf-8"):
        pass
    return path


def rename_one(path: str, new_name: str) -> str:
    reason = validate_name(new_name)
    if reason:
        raise ValueError(reason)
    if remote.is_remote(path):
        from . import remote_ops
        return remote_ops.rename_one(path, new_name)
    target = os.path.join(os.path.dirname(path), new_name)
    # En Windows y macOS solo cambiar mayúsculas apunta al mismo archivo.
    if os.path.lexists(target) and not (os.path.exists(path) and os.path.samefile(path, target)):
        raise FileExistsError(tr("Ya existe: {new_name}", new_name=new_name))
    os.rename(path, target)
    return target
