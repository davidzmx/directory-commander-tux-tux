"""Utilidades de carpetas: comparar/sincronizar y detectar duplicados."""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass


MTIME_TOLERANCE = 2.0  # FAT y exFAT guardan la fecha con resolución de 2 segundos


def _walk(root: str, is_cancelled=None):
    for current, dirs, files in os.walk(root, followlinks=False):
        if is_cancelled and is_cancelled():
            return
        yield current, dirs, files


@dataclass(frozen=True, slots=True)
class SyncItem:
    rel: str
    status: str  # only_a | only_b | newer_a | newer_b | differs
    is_dir: bool = False


def compare_dirs(a: str, b: str, by_content: bool = False, is_cancelled=None) -> list[SyncItem]:
    """Compara dos árboles y solo informa: no modifica nada. Una carpeta que solo existe
    en un lado aparece una vez, sin desglosar su contenido."""
    items: list[SyncItem] = []
    stack = [""]
    while stack:
        if is_cancelled and is_cancelled():
            break
        rel = stack.pop()
        left = _scan(os.path.join(a, rel))
        right = _scan(os.path.join(b, rel))
        for name in sorted(left.keys() | right.keys(), key=str.casefold):
            child = os.path.join(rel, name) if rel else name
            l_item, r_item = left.get(name), right.get(name)
            if r_item is None:
                items.append(SyncItem(child, "only_a", l_item[0]))
            elif l_item is None:
                items.append(SyncItem(child, "only_b", r_item[0]))
            elif l_item[0] and r_item[0]:
                stack.append(child)
            elif l_item[0] != r_item[0]:
                items.append(SyncItem(child, "differs"))
            else:
                status = _compare_files(os.path.join(a, child), os.path.join(b, child), l_item, r_item, by_content)
                if status:
                    items.append(SyncItem(child, status))
    return sorted(items, key=lambda i: i.rel.casefold())


def _scan(path: str) -> dict[str, tuple[bool, int, float]]:
    found = {}
    try:
        with os.scandir(path) as it:
            for item in it:
                try:
                    st = item.stat(follow_symlinks=False)
                    is_dir = item.is_dir(follow_symlinks=False) and not item.is_junction()
                    found[item.name] = (is_dir, st.st_size, st.st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return found


def _compare_files(pa: str, pb: str, l_item, r_item, by_content: bool) -> str | None:
    same_size = l_item[1] == r_item[1]
    delta = l_item[2] - r_item[2]
    if by_content:
        if same_size and _file_digest(pa) == _file_digest(pb):
            return None
    elif same_size and abs(delta) <= MTIME_TOLERANCE:
        return None
    if abs(delta) <= MTIME_TOLERANCE:
        return "differs"
    return "newer_a" if delta > 0 else "newer_b"


def _file_digest(path: str, limit: int | None = None) -> str | None:
    digest = hashlib.blake2b(digest_size=20)
    remaining = limit
    try:
        with open(path, "rb") as fin:
            while chunk := fin.read(1024 * 1024 if remaining is None else min(remaining, 1024 * 1024)):
                digest.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
    except OSError:
        return None
    return digest.hexdigest()


def find_duplicates(roots: list[str], min_size: int = 1, is_cancelled=None) -> list[list[str]]:
    """Agrupa archivos idénticos: tamaño, luego hash del inicio y por último hash completo."""
    by_size: dict[int, list[str]] = defaultdict(list)
    seen_files: set[tuple[int, int]] = set()
    for root in roots:
        for current, _dirs, files in _walk(root, is_cancelled):
            for name in files:
                path = os.path.join(current, name)
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if not os.path.isfile(path) or os.path.islink(path) or st.st_size < min_size:
                    continue
                # Hardlinks y raíces solapadas son el mismo archivo, no un duplicado.
                identity = (st.st_dev, st.st_ino)
                if st.st_ino and identity in seen_files:
                    continue
                seen_files.add(identity)
                by_size[st.st_size].append(path)

    groups: list[list[str]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for partial in _bucket(paths, lambda p: _file_digest(p, 64 * 1024), is_cancelled):
            if size <= 64 * 1024:
                groups.append(sorted(partial))
                continue
            groups.extend(sorted(g) for g in _bucket(partial, _file_digest, is_cancelled))
    return sorted(groups, key=lambda g: g[0].casefold())


def _bucket(paths: list[str], key, is_cancelled) -> list[list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        if is_cancelled and is_cancelled():
            return []
        digest = key(path)
        if digest is not None:
            buckets[digest].append(path)
    return [b for b in buckets.values() if len(b) > 1]
