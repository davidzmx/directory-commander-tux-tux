"""Ser el administrador de archivos del sistema, y dejar de serlo. Sin Qt.

En Windows se escribe solo en la parte del registro del usuario, sin permisos de administrador, y lo que había se guarda para devolverlo.
En Linux se usa xdg-mime, que es lo que respetan Dolphin, Nautilus y los demás escritorios.
"""
from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
import sys

from .. import APP_ID, APP_NAME
from .i18n import tr

OFF = "off"
FILESYSTEM = "filesystem"  # todas las carpetas del sistema de archivos
EXCEPT = "except"  # todas salvo las de una lista
CLASSES_ROOT = "Software\\Classes"
FOLDER_CLASSES = ("Directory", "Drive")  # no «Folder», que incluye la Papelera o el Panel de control: aquí no se pueden mostrar
VERB = "directorycommandertuxtux"
EXPLORER_VERB = "directorycommandertuxtux_explorer"
PREVIOUS_VALUE = "DirectoryCommanderTuxTuxPrevious"
PREVIOUS_DELEGATE = "DirectoryCommanderTuxTuxPreviousDelegate"
_NOTHING = "<nada>"
EXPLORER_DELEGATE = "{11dbb47c-a525-400b-9e80-a54615a090c0}"  # lo que usa Windows para abrir una carpeta en el Explorador
WIN_E_KEY = "CLSID\\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}\\shell\\opennewwindow\\command"
DESKTOP_FILE = f"{APP_ID}.desktop"
DIRECTORY_TYPE = "inode/directory"
_GENERATED = "X-DirectoryCommanderTuxTux-Generated=true"


def launch_arguments() -> list[str]:
    """Cómo se arranca el programa. Empaquetado es un ejecutable; desde el código, Python sin ventana de consola."""
    if getattr(sys, "frozen", False):
        # Un AppImage se monta en una carpeta que desaparece al cerrarlo: lo que hay que abrir es el propio archivo.
        return [os.environ.get("APPIMAGE") or sys.executable]
    python = sys.executable
    if sys.platform == "win32":
        windowless = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = windowless if os.path.exists(windowless) else python
    return [python, "-m", APP_ID.replace("-", "_")]


def _command_line(arguments: list[str], with_path: bool = True) -> str:
    quoted = " ".join(f'"{argument}"' if " " in argument else argument for argument in arguments)
    return f'{quoted} "%1"' if with_path else quoted


def clean_argument(path: str) -> str:
    """Windows pasa las unidades como "C:\\" y esa barra escapa la comilla de cierre: el programa recibe C:" en lugar de C:\\, a veces con un espacio detrás."""
    if sys.platform != "win32":
        return path
    path = path.strip(' "')
    return path + "\\" if len(path) == 2 and path[1] == ":" else path


def is_exception(path: str, exceptions: list[str]) -> bool:
    """Esa carpeta, o una de dentro, está en la lista de las que sigue abriendo el Explorador. Solo existe en Windows: son siempre rutas de Windows."""
    target = ntpath.normcase(ntpath.normpath(path))
    for item in exceptions:
        folder = ntpath.normcase(ntpath.normpath(item))
        if target == folder or target.startswith(folder.rstrip(ntpath.sep) + ntpath.sep):
            return True
    return False


# ---------------------------------------------------------------- Windows
class Registry:
    """La parte del registro del usuario. `root` permite probarlo en otra rama sin tocar lo que usa Windows."""

    def __init__(self, root: str = CLASSES_ROOT) -> None:
        import winreg

        self._winreg = winreg
        self.root = root

    def _path(self, key: str) -> str:
        return f"{self.root}\\{key}"

    def read(self, key: str, name: str = "") -> str | None:
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._path(key)) as handle:
                return winreg.QueryValueEx(handle, name)[0]
        except OSError:
            return None

    def write(self, key: str, name: str, value: str) -> None:
        winreg = self._winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._path(key)) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete_value(self, key: str, name: str = "") -> None:
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._path(key), 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except OSError:
            pass

    def delete_tree(self, key: str) -> None:
        winreg = self._winreg
        path = self._path(key)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as handle:
                children = []
                while True:
                    try:
                        children.append(winreg.EnumKey(handle, len(children)))
                    except OSError:
                        break
            for child in children:
                self.delete_tree(f"{key}\\{child}")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass

    def delete_if_empty(self, key: str) -> None:
        """Una clave que se creó para esto no debe quedarse. `DeleteKey` no borra si tiene subclaves; los valores se miran antes."""
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._path(key)) as handle:
                keys, values, _changed = winreg.QueryInfoKey(handle)
            if not keys and not values:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self._path(key))
        except OSError:
            pass


def windows_state(registry: Registry) -> tuple[bool, bool]:
    """(si está puesto para las carpetas, si lo está para Win+E)."""
    replaced = all(registry.read(f"{cls}\\shell") == VERB for cls in FOLDER_CLASSES)
    launcher = registry.read(WIN_E_KEY) or ""
    return replaced, bool(launcher) and registry.read(WIN_E_KEY, PREVIOUS_VALUE) is not None


def replace_explorer(registry: Registry, arguments: list[str]) -> None:
    """Las carpetas se abren aquí. Se añade también abrirlas en el Explorador, para poder seguir haciéndolo con el botón derecho."""
    for cls in FOLDER_CLASSES:
        shell = f"{cls}\\shell"
        if registry.read(shell) != VERB:
            registry.write(shell, PREVIOUS_VALUE, registry.read(shell) or _NOTHING)
        registry.write(f"{shell}\\{VERB}", "", tr("Abrir con {APP_NAME}", APP_NAME=APP_NAME))
        registry.write(f"{shell}\\{VERB}\\command", "", _command_line(arguments))
        registry.write(f"{shell}\\{EXPLORER_VERB}", "", tr("Abrir en el Explorador de archivos"))
        registry.write(f"{shell}\\{EXPLORER_VERB}\\command", "", "C:\\Windows\\Explorer.exe")
        registry.write(f"{shell}\\{EXPLORER_VERB}\\command", "DelegateExecute", EXPLORER_DELEGATE)
        registry.write(shell, "", VERB)


def restore_explorer(registry: Registry) -> None:
    """Todo queda como estaba antes de reemplazarlo, también si otro programa era el que abría las carpetas."""
    for cls in FOLDER_CLASSES:
        shell = f"{cls}\\shell"
        previous = registry.read(shell, PREVIOUS_VALUE)
        if registry.read(shell) == VERB or previous is not None:
            if previous is None or previous == _NOTHING:
                registry.delete_value(shell)
            else:
                registry.write(shell, "", previous)
        registry.delete_value(shell, PREVIOUS_VALUE)
        registry.delete_tree(f"{shell}\\{VERB}")
        registry.delete_tree(f"{shell}\\{EXPLORER_VERB}")
        registry.delete_if_empty(shell)
        registry.delete_if_empty(cls)


def set_win_e(registry: Registry, arguments: list[str], enabled: bool) -> None:
    """Win+E abre este programa. Con DelegateExecute vacío, Windows usa la orden en vez de su Explorador."""
    mine = registry.read(WIN_E_KEY, PREVIOUS_VALUE) is not None
    if enabled:
        if not mine:
            current = registry.read(WIN_E_KEY)
            delegate = registry.read(WIN_E_KEY, "DelegateExecute")
            registry.write(WIN_E_KEY, PREVIOUS_VALUE, current if current is not None else _NOTHING)
            registry.write(WIN_E_KEY, PREVIOUS_DELEGATE, delegate if delegate is not None else _NOTHING)
        registry.write(WIN_E_KEY, "", _command_line(arguments, with_path=False))
        registry.write(WIN_E_KEY, "DelegateExecute", "")
    elif mine:
        for name, saved in (("", PREVIOUS_VALUE), ("DelegateExecute", PREVIOUS_DELEGATE)):
            previous = registry.read(WIN_E_KEY, saved)
            if previous is None or previous == _NOTHING:
                registry.delete_value(WIN_E_KEY, name)
            else:
                registry.write(WIN_E_KEY, name, previous)
            registry.delete_value(WIN_E_KEY, saved)
        parts = WIN_E_KEY.split("\\")
        for length in range(len(parts), 0, -1):  # hasta CLSID, si no queda nada dentro: las que tengan algo no se tocan
            registry.delete_if_empty("\\".join(parts[:length]))


def notify_windows() -> None:
    """Para que el Explorador se entere sin reiniciar la sesión."""
    import ctypes

    shcne_assocchanged = 0x08000000
    ctypes.windll.shell32.SHChangeNotify(shcne_assocchanged, 0, None, None)


def windows_folder_handler() -> str:
    """Con qué se abren ahora las carpetas: lo que Windows diga, no lo que se haya escrito aquí."""
    import ctypes
    from ctypes import wintypes

    assocstr_command = 1
    for name in ("Directory", "Folder"):  # Windows mira en ese orden, y lo normal es que Directory no tenga verbo propio
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shlwapi.AssocQueryStringW(0, assocstr_command, name, None, buffer, ctypes.byref(size)) == 0:
            return buffer.value
    return ""


# ---------------------------------------------------------------- Linux
def _desktop_text(arguments: list[str]) -> str:
    return "\n".join(("[Desktop Entry]", "Type=Application", f"Name={APP_NAME}", f"GenericName={tr('Gestor de archivos')}", f"Exec={_command_line(arguments, with_path=False)} %F", f"Icon={APP_ID}",
                      "Terminal=false", "Categories=Qt;System;FileTools;FileManager;", f"MimeType={DIRECTORY_TYPE};", f"StartupWMClass={APP_ID}", _GENERATED, ""))


def applications_folder() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "applications")


def _run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, stdin=subprocess.DEVNULL, timeout=10)


def xdg_available() -> bool:
    return shutil.which("xdg-mime") is not None


def linux_default(run=_run) -> str:
    """El programa que abre ahora las carpetas: `org.kde.dolphin.desktop`, `org.gnome.Nautilus.desktop`…"""
    done = run(["xdg-mime", "query", "default", DIRECTORY_TYPE])
    return done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else ""


def become_linux_default(arguments: list[str], run=_run) -> str:
    """Devuelve el que había, para poder volver a él."""
    previous = linux_default(run)
    folder = applications_folder()
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, DESKTOP_FILE), "w", encoding="utf-8") as handle:
        handle.write(_desktop_text(arguments))
    done = run(["xdg-mime", "default", DESKTOP_FILE, DIRECTORY_TYPE])
    if done.returncode != 0:
        raise OSError(done.stderr.decode("utf-8", "replace").strip() or tr("xdg-mime no ha podido cambiarlo"))
    return "" if previous == DESKTOP_FILE else previous


def _forget_linux_default() -> None:
    """Si antes no había ninguno elegido, xdg-mime no sabe volver a eso: se quita la línea de la lista del usuario."""
    folder = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(folder, "mimeapps.list")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        kept = [line for line in lines if line.strip() != f"{DIRECTORY_TYPE}={DESKTOP_FILE}"]
        if kept != lines:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(kept)


def restore_linux_default(previous: str, run=_run) -> None:
    if previous:
        done = run(["xdg-mime", "default", previous, DIRECTORY_TYPE])
        if done.returncode != 0:
            raise OSError(done.stderr.decode("utf-8", "replace").strip() or tr("xdg-mime no ha podido cambiarlo"))
    else:
        _forget_linux_default()
    desktop = os.path.join(applications_folder(), DESKTOP_FILE)
    try:
        with open(desktop, encoding="utf-8") as handle:
            generated = _GENERATED in handle.read()
        if generated:  # el de un paquete instalado no es nuestro
            os.remove(desktop)
    except OSError:
        pass


def restore_system_default(previous: str = "") -> None:
    """Deja el sistema como estaba. Se puede llamar sin abrir la ventana, con --restore-file-manager."""
    if sys.platform == "win32":
        registry = Registry()
        restore_explorer(registry)
        set_win_e(registry, [], False)
        notify_windows()
    else:
        restore_linux_default(previous)
