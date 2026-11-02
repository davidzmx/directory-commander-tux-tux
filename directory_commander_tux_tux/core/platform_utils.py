"""Diferencias entre Windows, KDE, GNOME y otros escritorios Linux."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from .i18n import tr


def desktop() -> str:
    if sys.platform == "win32":
        return "windows"
    current = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in current:
        return "kde"
    if "GNOME" in current or "UNITY" in current or "CINNAMON" in current or "BUDGIE" in current:
        return "gnome"
    return "other"


_TERMINALS = {
    "kde": [("konsole", ["--workdir", "{path}"])],
    "gnome": [("kgx", ["--working-directory={path}"]), ("gnome-terminal", ["--working-directory={path}"]),
              ("ptyxis", ["--working-directory={path}"])],
    "other": [],
}
_FALLBACK_TERMINALS = [
    ("konsole", ["--workdir", "{path}"]),
    ("kgx", ["--working-directory={path}"]),
    ("gnome-terminal", ["--working-directory={path}"]),
    ("ptyxis", ["--working-directory={path}"]),
    ("xfce4-terminal", ["--working-directory={path}"]),
    ("tilix", ["--working-directory={path}"]),
    ("alacritty", ["--working-directory", "{path}"]),
    ("xterm", []),
]


def terminal_command(path: str, which=shutil.which, env_desktop: str | None = None) -> list[str] | None:
    """Terminal para el escritorio actual, o None si no hay ninguno instalado."""
    current = env_desktop or desktop()
    if current == "windows":
        if which("wt.exe"):
            return ["wt.exe", "-d", path]
        return ["cmd.exe"]  # open_terminal ya lo arranca con cwd=path
    for program, args in _TERMINALS.get(current, []) + _FALLBACK_TERMINALS:
        if which(program):
            return [program] + [arg.replace("{path}", path) for arg in args]
    return None


def open_terminal(path: str, shell=None) -> None:
    """En Windows, `shell` es el intérprete elegido en las preferencias. En Linux manda el programa de terminal del escritorio."""
    if shell is not None and sys.platform == "win32":
        subprocess.Popen(shell.argv, cwd=path, env={**os.environ, **shell.env}, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return
    command = terminal_command(path)
    if command is None:
        raise OSError(tr("No se encontró ningún programa de terminal instalado"))
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen(command, cwd=path, creationflags=flags, start_new_session=sys.platform != "win32")


def run_program(args: list[str], cwd: str) -> None:
    """Lanza un programa sin usar un shell: los argumentos llegan tal cual."""
    subprocess.Popen(args, cwd=cwd if os.path.isdir(cwd) else None, start_new_session=sys.platform != "win32")


def set_dark_title_bar(window_id: int, dark: bool) -> None:
    """Pide a Windows una barra de título clara u oscura. En Linux la decide el escritorio."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    immersive_dark_mode = 20
    value = wintypes.BOOL(dark)
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(window_id), immersive_dark_mode,
                                                   ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        pass  # es solo estético: sin DWM la ventana sigue funcionando


def home_dir() -> str:
    return os.path.expanduser("~")


def root_of(path: str) -> str:
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    return drive + os.sep if drive else os.sep
