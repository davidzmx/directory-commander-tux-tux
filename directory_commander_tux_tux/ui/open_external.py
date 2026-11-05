"""Abrir archivos, carpetas y direcciones con la aplicación predeterminada del sistema.

En Linux, Qt lo pide al portal del escritorio, que las primeras veces pregunta con qué aplicación abrir cada tipo de archivo
aunque haya una predeterminada. `xdg-open` usa la predeterminada directamente, como el propio gestor de archivos del escritorio.
Dentro de Flatpak o Snap no se puede salir del entorno aislado: ahí sí hace falta el portal."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from PySide6.QtCore import QMimeDatabase, QProcess, QUrl
from PySide6.QtGui import QDesktopServices

_mimes = QMimeDatabase()


def _use_xdg_open() -> bool:
    return sys.platform.startswith("linux") and not os.environ.get("FLATPAK_ID") and not os.environ.get("SNAP") and shutil.which("xdg-open") is not None


def open_externally(target: str) -> bool:
    """`target` es una ruta del equipo o una dirección como https://…. False si no se pudo lanzar."""
    if _use_xdg_open():
        started = QProcess.startDetached("xdg-open", [target])
        return bool(started[0] if isinstance(started, tuple) else started)
    return bool(QDesktopServices.openUrl(QUrl(target) if "://" in target else QUrl.fromLocalFile(target)))


def has_default_app(path: str) -> bool:
    """Si hay una aplicación predeterminada para ese tipo de archivo. Fuera de Linux lo dice el propio intento de abrirlo."""
    if not _use_xdg_open() or not shutil.which("xdg-mime"):
        return True
    mime = _mimes.mimeTypeForFile(path).name()
    try:
        answer = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True  # que lo intente: xdg-open preguntará si no hay ninguna
    return bool(answer.stdout.strip())
