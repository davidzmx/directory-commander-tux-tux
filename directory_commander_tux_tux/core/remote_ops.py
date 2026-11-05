"""Copiar, mover, borrar y crear cuando uno de los lados es un servidor SFTP. Sin Qt.

Las mismas reglas que las copias locales de `fsops`: un archivo se publica en el destino solo cuando está completo,
y los conflictos se resuelven igual. Lo que cambia es cómo se lee y se escribe en cada lado.
"""
from __future__ import annotations

import os
import posixpath
import stat
from dataclasses import dataclass

from . import fsops, remote
from .fsops import Action, Cancelled, Collision, OpResult, ProgressSink, TransferState, MTIME_TOLERANCE, PART_SUFFIX, CHUNK
from .i18n import tr


@dataclass
class _Stat:
    is_dir: bool
    is_link: bool
    size: int
    mtime: float


class _LocalSide:
    remote = False

    def join(self, folder: str, name: str) -> str:
        return os.path.join(folder, name)

    def name(self, path: str) -> str:
        return os.path.basename(path.rstrip("/\\"))

    def folder(self, path: str) -> str:
        return os.path.dirname(path.rstrip("/\\"))

    def stat(self, path: str) -> _Stat | None:
        try:
            st = os.lstat(path)
        except OSError:
            return None
        link = stat.S_ISLNK(st.st_mode) or os.path.isjunction(path)
        return _Stat(stat.S_ISDIR(st.st_mode), link, st.st_size, st.st_mtime)

    def listdir(self, path: str) -> list[str]:
        return os.listdir(path)

    def open_read(self, path: str):
        return open(path, "rb")

    def open_write(self, path: str):
        return open(path, "wb")

    def mkdir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def replace(self, source: str, target: str) -> None:
        os.replace(source, target)

    def rename(self, source: str, target: str) -> None:
        os.rename(source, target)

    def remove(self, path: str) -> None:
        fsops.remove_tree(path)

    def set_mtime(self, path: str, mtime: float) -> None:
        os.utime(path, (mtime, mtime))

    def points_to_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def close(self) -> None:
        pass


class _RemoteSide:
    """Las rutas son las completas, `sftp://…`; al servidor solo le llega la carpeta de dentro."""
    remote = True

    def __init__(self, path: str) -> None:
        self.server = remote.server_of(path)
        self.sftp = remote.connection_for(path).new_sftp()

    def join(self, folder: str, name: str) -> str:
        return remote.join(folder, name)

    def name(self, path: str) -> str:
        return remote.name_of(path)

    def folder(self, path: str) -> str:
        return remote.parent(path) or remote.root(path)

    def stat(self, path: str) -> _Stat | None:
        try:
            st = self.sftp.lstat(remote.inner_of(path))
        except OSError:
            return None
        mode = st.st_mode or 0
        return _Stat(stat.S_ISDIR(mode), stat.S_ISLNK(mode), st.st_size or 0, float(st.st_mtime or 0))

    def listdir(self, path: str) -> list[str]:
        return self.sftp.listdir(remote.inner_of(path))

    def open_read(self, path: str):
        handle = self.sftp.open(remote.inner_of(path), "rb")
        handle.prefetch()  # pide los bloques por adelantado: sin esto, cada lectura espera su respuesta
        return handle

    def open_write(self, path: str):
        handle = self.sftp.open(remote.inner_of(path), "wb")
        handle.set_pipelined(True)
        return handle

    def mkdir(self, path: str) -> None:
        inner = remote.inner_of(path)
        missing = []
        while inner != "/":
            try:
                self.sftp.stat(inner)
                break
            except OSError:
                missing.append(inner)
                inner = posixpath.dirname(inner)
        for folder in reversed(missing):
            self.sftp.mkdir(folder)

    def replace(self, source: str, target: str) -> None:
        remote._replace(self.sftp, remote.inner_of(source), remote.inner_of(target))

    def rename(self, source: str, target: str) -> None:
        self.sftp.rename(remote.inner_of(source), remote.inner_of(target))

    def remove(self, path: str) -> None:
        _remove_tree(self.sftp, remote.inner_of(path))

    def set_mtime(self, path: str, mtime: float) -> None:
        self.sftp.utime(remote.inner_of(path), (mtime, mtime))

    def points_to_dir(self, path: str) -> bool:
        try:
            return stat.S_ISDIR(self.sftp.stat(remote.inner_of(path)).st_mode or 0)
        except OSError:
            return False

    def close(self) -> None:
        self.sftp.close()


def _side(path: str):
    return _RemoteSide(path) if remote.is_remote(path) else _LocalSide()


def _remove_tree(sftp, inner: str) -> None:
    if inner in ("", "/"):
        raise PermissionError(tr("No se puede eliminar la raíz del servidor"))
    mode = sftp.lstat(inner).st_mode or 0
    if stat.S_ISDIR(mode):
        for name in sftp.listdir(inner):
            _remove_tree(sftp, posixpath.join(inner, name))
        sftp.rmdir(inner)
    else:
        sftp.remove(inner)


def _size_of(side, path: str, sink: ProgressSink | None) -> tuple[int, int]:
    info = side.stat(path)
    if info is None:
        return 0, 0
    if not info.is_dir or info.is_link:
        return (0 if info.is_link else info.size), 1
    total = count = 0
    stack = [path]
    while stack:
        if sink:
            sink.check()
        folder = stack.pop()
        try:
            names = side.listdir(folder)
        except OSError:
            continue
        for name in names:
            child = side.join(folder, name)
            found = side.stat(child)
            if found is None:
                continue
            if found.is_dir and not found.is_link:
                stack.append(child)
            else:
                total += 0 if found.is_link else found.size
                count += 1
    return total, count


def tree_size(path: str, sink: ProgressSink | None = None) -> tuple[int, int]:
    side = _side(path)
    try:
        return _size_of(side, path, sink)
    finally:
        side.close()


class _Copy:
    """Como `fsops._Transfer`, entre dos lados cualesquiera. Los enlaces del servidor no se recrean: se copia a lo que apuntan, si es un archivo."""

    def __init__(self, source, target, move: bool, policy: Collision, sink: ProgressSink, total: int, result: OpResult, ask) -> None:
        self.src_side, self.dest_side = source, target
        self.move, self.policy, self.sink, self.total, self.result, self.ask = move, policy, sink, total, result, ask
        self.state = sink.transfer
        self.bytes_done = 0
        self.skip_identical = False
        self.same_server = source.remote and target.remote and source.server == target.server

    def add(self, nbytes: int) -> None:
        self.bytes_done += nbytes
        self.state.bytes_done = self.bytes_done
        self.state.current_done += nbytes
        self.sink.report(self.bytes_done, self.total, self.state.current_name)

    def begin(self, name: str, size: int) -> None:
        state = self.state
        state.current_name, state.current_size, state.current_done, state.skip_requested = name, size, 0, False

    def run(self, src: str, dest_dir: str, new_name: str | None) -> None:
        info = self.src_side.stat(src)
        if info is None:
            raise FileNotFoundError(tr("No existe: {src}", src=src))
        name = new_name or self.src_side.name(src)
        if self.same_server and info.is_dir and remote.is_inside(dest_dir, src):
            raise OSError(tr("No se puede copiar o mover una carpeta dentro de sí misma"))
        if self.same_server and remote.normalize(self.src_side.folder(src)) == remote.normalize(dest_dir) and new_name is None:
            if self.move:
                self.result.skipped += 1
                return
            target = self._unique(dest_dir, name)
        else:
            target = self.dest_side.join(dest_dir, name)
        self.place(src, info, target)

    def _unique(self, folder: str, name: str) -> str:
        stem, ext = fsops._split_name(name)
        candidate, index = self.dest_side.join(folder, name), 2
        while self.dest_side.stat(candidate) is not None:
            candidate = self.dest_side.join(folder, f"{stem} ({index}){ext}")
            index += 1
        return candidate

    def _skip(self, src: str, info) -> None:
        size, count = _size_of(self.src_side, src, None)
        self.begin(self.src_side.name(src), size)
        self.add(size)
        self.state.items_done += count
        self.result.skipped += 1

    def _resolve(self, src: str, info, dest: str, existing) -> str | None:
        policy = self.policy
        if policy is Collision.ASK:
            identical = existing.size == info.size and abs(existing.mtime - info.mtime) <= MTIME_TOLERANCE
            if self.skip_identical and identical:
                return None
            answer = self.ask(src, dest)
            if answer is None:
                raise Cancelled()
            if answer.action is Action.SKIP_IDENTICAL:
                self.skip_identical = True
                return self._resolve(src, info, dest, existing)
            policy = fsops._POLICY_OF[answer.action]
            if answer.to_all:
                self.policy = policy
            if answer.action is Action.RENAME_NEW and answer.name:
                chosen = self.dest_side.join(self.dest_side.folder(dest), answer.name)
                found = self.dest_side.stat(chosen)
                return self._resolve(src, info, chosen, found) if found is not None else chosen
        if policy is Collision.SKIP:
            return None
        if policy is Collision.RENAME:
            return self._unique(self.dest_side.folder(dest), self.dest_side.name(dest))
        if policy is Collision.RENAME_OLD:
            moved = self._unique(self.dest_side.folder(dest), self.dest_side.name(dest))
            self.dest_side.rename(dest, moved)
            self.result.renamed.append((dest, moved))
            return dest
        if policy in (Collision.UPDATE_ALL, Collision.UPDATE_EXISTING) and not info.mtime > existing.mtime + MTIME_TOLERANCE:
            return None
        return dest

    def place(self, src: str, info, dest: str) -> bool:
        self.sink.check()
        existing = self.dest_side.stat(dest)
        if existing is not None:
            if info.is_dir != existing.is_dir:
                self.result.errors.append((src, tr("El destino existe y es de otro tipo (carpeta/archivo)")))
                return False
            if not info.is_dir:
                dest = self._resolve(src, info, dest, existing)
                if dest is None:
                    self._skip(src, info)
                    return False
        elif self.policy is Collision.UPDATE_EXISTING:
            self._skip(src, info)
            return False
        if self.move and self.same_server and self.dest_side.stat(dest) is None:
            size, count = _size_of(self.src_side, src, None)
            self.src_side.rename(src, dest)  # en el mismo servidor, mover es cambiar de nombre
            self.begin(self.src_side.name(src), size)
            self.add(size)
            self.state.items_done += count
            self.result.done += 1
            self.result.placed.append((src, dest, True))
            return True
        ok = self._copy_dir(src, dest) if info.is_dir and not info.is_link else self._copy_file(src, info, dest)
        if ok and self.move:
            try:
                self.src_side.remove(src)
            except OSError as exc:
                self.result.errors.append((src, tr("Copiado, pero no se pudo borrar el origen: {exc}", exc=exc)))
                return False
        if ok:
            self.result.placed.append((src, dest, existing is None))
        return ok

    def _copy_dir(self, src: str, dest: str) -> bool:
        try:
            self.dest_side.mkdir(dest)
            names = self.src_side.listdir(src)
        except OSError as exc:
            self.result.errors.append((src, str(exc)))
            return False
        ok = True
        for name in names:
            child = self.src_side.join(src, name)
            found = self.src_side.stat(child)
            if found is None:
                continue
            try:
                ok = self.place(child, found, self.dest_side.join(dest, name)) and ok
            except OSError as exc:
                self.result.errors.append((child, str(exc)))
                ok = False
        if ok:
            self.result.done += 1
        return ok

    def _copy_file(self, src: str, info, dest: str) -> bool:
        if info.is_link and self.src_side.points_to_dir(src):  # recorrerlo podría no acabar nunca
            self.state.items_done += 1
            self.result.skipped += 1
            return False
        partial = dest + PART_SUFFIX
        self.begin(self.src_side.name(src), info.size)
        try:
            with self.src_side.open_read(src) as fin, self.dest_side.open_write(partial) as fout:
                while chunk := fin.read(CHUNK):
                    self.sink.check()
                    if self.state.skip_requested:
                        raise fsops._SkipFile()
                    fout.write(chunk)
                    self.add(len(chunk))
            try:
                self.dest_side.set_mtime(partial, info.mtime)
            except OSError:
                pass
            self.dest_side.replace(partial, dest)
        except fsops._SkipFile:
            self._discard(partial)
            self.add(max(0, self.state.current_size - self.state.current_done))
            self.state.items_done += 1
            self.result.skipped += 1
            return False
        except OSError as exc:
            self._discard(partial)
            self.state.items_done += 1
            self.result.errors.append((src, str(exc)))
            return False
        except BaseException:
            self._discard(partial)
            raise
        self.state.items_done += 1
        self.result.done += 1
        return True

    def _discard(self, partial: str) -> None:
        try:
            if self.dest_side.stat(partial) is not None:
                self.dest_side.remove(partial)
        except OSError:
            pass


def transfer(sources: list[str], dest_dir: str, move: bool = False, policy: Collision = Collision.RENAME,
             sink: ProgressSink | None = None, new_name: str | None = None, ask=None) -> OpResult:
    """Lo mismo que `fsops.transfer`, con el origen, el destino o los dos en un servidor."""
    if new_name is not None and len(sources) != 1:
        raise ValueError(tr("Solo se puede dar otro nombre a un elemento cada vez"))
    sink = sink or ProgressSink()
    result = OpResult()
    sink.transfer = TransferState()
    sides = []
    try:
        target = _side(dest_dir)
        sides.append(target)
        found = target.stat(dest_dir)
        if found is None or not found.is_dir:
            raise OSError(tr("El destino no es una carpeta: {dest_dir}", dest_dir=dest_dir))
        by_server: dict[str, object] = {}
        groups = []
        for src in sources:
            key = remote.server_of(src)
            if key not in by_server:
                by_server[key] = _side(src)
                sides.append(by_server[key])
            groups.append((src, by_server[key]))
        sizes = [_size_of(side, src, sink) for src, side in groups]
        state = sink.transfer
        state.total_bytes, state.total_items, state.ready = sum(size for size, _ in sizes), sum(count for _, count in sizes), True
        for src, side in groups:
            job = _Copy(side, target, move, policy, sink, state.total_bytes, result, ask)
            try:
                job.run(src, dest_dir, new_name)
            except OSError as exc:
                result.errors.append((src, str(exc)))
            policy = job.policy  # lo elegido «para todos» vale también para los siguientes
    except Cancelled:
        result.cancelled = True
    except OSError as exc:
        result.errors.append((dest_dir, str(exc)))
    finally:
        for side in sides:
            side.close()
    return result


def delete_paths(paths: list[str], sink: ProgressSink | None = None) -> OpResult:
    """En un servidor no hay papelera: se borra siempre en definitiva."""
    sink = sink or ProgressSink()
    result = OpResult()
    sides: dict[str, _RemoteSide] = {}
    try:
        for index, path in enumerate(paths):
            if sink.is_cancelled:
                result.cancelled = True
                break
            sink.report(index, len(paths), remote.name_of(path))
            try:
                side = sides.get(remote.server_of(path)) or sides.setdefault(remote.server_of(path), _RemoteSide(path))
                side.remove(path)
                result.done += 1
            except OSError as exc:
                result.errors.append((path, str(exc)))
    finally:
        for side in sides.values():
            side.close()
    return result


def _free(sftp, inner: str, name: str) -> None:
    try:
        sftp.lstat(inner)
    except FileNotFoundError:
        return
    raise FileExistsError(tr("Ya existe: {name}", name=name))


def create_folder(parent: str, name: str) -> str:
    path = remote.join(parent, name)
    remote.connection_for(parent).run(lambda sftp: sftp.mkdir(remote.inner_of(path)))
    return path


def create_file(parent: str, name: str) -> str:
    path = remote.join(parent, name)

    def create(sftp) -> None:
        _free(sftp, remote.inner_of(path), name)
        sftp.open(remote.inner_of(path), "w").close()

    remote.connection_for(parent).run(create)
    return path


def rename_one(path: str, new_name: str) -> str:
    target = remote.join(remote.parent(path) or remote.root(path), new_name)

    def rename(sftp) -> None:
        _free(sftp, remote.inner_of(target), new_name)
        sftp.rename(remote.inner_of(path), remote.inner_of(target))

    remote.connection_for(path).run(rename)
    return target


def drop_operation(paths: list[str], dest_dir: str, force_move: bool = False) -> str | None:
    """En el mismo servidor se mueve, como en la misma unidad; entre equipos distintos se copia, salvo que se pida mover."""
    if not paths or all(remote.normalize(_parent_of(path)) == remote.normalize(dest_dir) if remote.is_remote(path) and remote.is_remote(dest_dir)
                        else False for path in paths):
        return None
    same = all(remote.server_of(path) == remote.server_of(dest_dir) for path in paths)
    return "move" if force_move or same else "copy"


def _parent_of(path: str) -> str:
    return remote.parent(path) or remote.root(path)

