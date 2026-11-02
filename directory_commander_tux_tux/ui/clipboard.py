"""Portapapeles de archivos, compatible con Explorer, Dolphin y Nautilus."""
from __future__ import annotations

import os
import struct

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

_KDE_CUT = "application/x-kde-cutselection"
_GNOME = "x-special/gnome-copied-files"
_WINDOWS_EFFECT = "Preferred DropEffect"
_WINDOWS_MIME = f'application/x-qt-windows-mime;value="{_WINDOWS_EFFECT}"'
_EFFECT_MOVE = 2
_EFFECT_COPY = 5


def build_mime(paths: list[str], cut: bool) -> QMimeData:
    urls = [QUrl.fromLocalFile(p) for p in paths]
    mime = QMimeData()
    mime.setUrls(urls)
    mime.setText("\n".join(paths))
    mime.setData(_KDE_CUT, b"1" if cut else b"0")
    gnome = "\n".join(["cut" if cut else "copy"] + [u.toString() for u in urls])
    mime.setData(_GNOME, gnome.encode("utf-8"))
    effect = struct.pack("<I", _EFFECT_MOVE if cut else _EFFECT_COPY)
    mime.setData(_WINDOWS_MIME, effect)
    mime.setData(_WINDOWS_EFFECT, effect)
    return mime


def set_files(paths: list[str], cut: bool) -> None:
    QApplication.clipboard().setMimeData(build_mime(paths, cut))


def read_files(mime: QMimeData | None = None) -> tuple[list[str], bool]:
    """Devuelve (rutas, cortar). Solo admite archivos locales."""
    mime = mime or QApplication.clipboard().mimeData()
    if mime is None:
        return [], False
    paths = [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
    return paths, _is_cut(mime)


def _is_cut(mime: QMimeData) -> bool:
    if mime.hasFormat(_KDE_CUT):
        return bytes(mime.data(_KDE_CUT)).strip() == b"1"
    if mime.hasFormat(_GNOME):
        return bytes(mime.data(_GNOME)).split(b"\n", 1)[0].strip() == b"cut"
    for fmt in (_WINDOWS_MIME, _WINDOWS_EFFECT):
        if mime.hasFormat(fmt):
            raw = bytes(mime.data(fmt))
            if len(raw) >= 4:
                effect = struct.unpack("<I", raw[:4])[0]
                return bool(effect & _EFFECT_MOVE) and not effect & 1  # 1 = copiar
    return False
