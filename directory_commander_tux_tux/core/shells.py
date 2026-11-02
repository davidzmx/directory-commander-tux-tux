"""Qué intérprete abre la terminal integrada y cómo se le escribe una orden. Sin Qt."""
from __future__ import annotations

import ntpath
import os
import re
import shlex
import shutil
import sys
from typing import NamedTuple

from .i18n import tr

CMD = "cmd"
POWERSHELL = "powershell"
POSIX = "posix"
FISH = "fish"
OTHER = "other"  # no se sabe cómo pedirle que cambie de carpeta, ni si está ocupado: no sigue al panel
CUSTOM = "custom"
DEFAULT_FAMILY = POWERSHELL if sys.platform == "win32" else POSIX

_FAMILIES = {"cmd": CMD, "powershell": POWERSHELL, "pwsh": POWERSHELL, "bash": POSIX, "sh": POSIX, "dash": POSIX, "zsh": POSIX, "ksh": POSIX, "fish": FISH}
_UNIX_SHELLS = (("bash", "Bash"), ("zsh", "Zsh"), ("fish", "Fish"), ("ksh", "Ksh"), ("dash", "Dash"), ("tcsh", "Tcsh"), ("pwsh", "PowerShell"), ("nu", "Nushell"))
# En Windows es lo único que distingue el indicador de comandos de una pregunta de un script, como un `pause` o un `Read-Host`.
_PROMPT_ENDINGS = {CMD: (">",), POWERSHELL: (">", "$", "#", "❯", "➜", "λ"), POSIX: ("$", "#", "%", ">", "❯", "➜", "λ"), FISH: ("$", "#", "%", ">", "❯", "➜", "λ")}
_POWERSHELL_QUOTES = re.compile("['\u2018\u2019\u201a\u201b]")  # PowerShell también acepta las comillas tipográficas como comilla simple
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\|\\\\")
_POWERSHELL_PROMPT = re.compile(r"PS (.+?)>( |$)")


class Shell(NamedTuple):
    key: str
    label: str
    argv: list[str]
    family: str
    env: dict[str, str] = {}


def family_of(program: str) -> str:
    name = ntpath.basename(program).lower()
    return _FAMILIES.get(name[:-4] if name.endswith(".exe") else name, OTHER)


def _cygwin_root() -> str:
    """Donde lo instaló su instalador. Vacío si no está en el registro."""
    if sys.platform != "win32":
        return ""
    import winreg
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Cygwin\setup"), (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Cygwin\setup"),
                       (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Cygwin\setup")):
        try:
            with winreg.OpenKey(root, path) as key:
                return str(winreg.QueryValueEx(key, "rootdir")[0])
        except OSError:
            continue
    return ""


def _wsl_has_distribution() -> bool:
    """`wsl.exe` existe en todo Windows reciente, aunque no haya nada instalado: lo que cuenta es tener alguna distribución."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as key:
            return winreg.QueryInfoKey(key)[0] > 0
    except OSError:
        return False


def _windows_shells(environ, which, isfile, cygwin_root, wsl_installed) -> list[Shell]:
    shells = []
    system_powershell = ntpath.join(environ.get("SystemRoot", "C:\\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    powershell = which("powershell.exe") or (system_powershell if isfile(system_powershell) else None)
    if powershell:
        shells.append(Shell(POWERSHELL, "Windows PowerShell", [powershell, "-NoLogo"], POWERSHELL))
    program_files = [environ[name] for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)") if environ.get(name)]
    pwsh = which("pwsh.exe") or next((path for path in (ntpath.join(folder, "PowerShell", "7", "pwsh.exe") for folder in program_files) if isfile(path)), None)
    if pwsh:
        shells.append(Shell("pwsh", "PowerShell 7", [pwsh, "-NoLogo"], POWERSHELL))
    shells.append(Shell(CMD, tr("Símbolo del sistema (cmd)"), [environ.get("COMSPEC") or "cmd.exe"], CMD))
    # El bash de Git\bin solo lanza el de usr\bin: visto desde fuera, siempre tendría un programa en marcha y nunca seguiría al panel.
    # CHERE_INVOKING hace que no salte a la carpeta personal al arrancar.
    git = which("git.exe")
    git_roots = [*(ntpath.join(folder, "Git") for folder in program_files), ntpath.join(environ.get("LOCALAPPDATA", ""), "Programs", "Git"),
                 *([ntpath.dirname(ntpath.dirname(git))] if git else [])]
    git_bash = next((path for path in (ntpath.join(root, "usr", "bin", "bash.exe") for root in git_roots) if isfile(path)), None)
    if git_bash:
        shells.append(Shell("gitbash", "Git Bash", [git_bash, "--login", "-i"], POSIX, {"MSYSTEM": "MINGW64", "CHERE_INVOKING": "1"}))
    cygwin_bash = next((path for path in (ntpath.join(root, "bin", "bash.exe") for root in (cygwin_root(), "C:\\cygwin64", "C:\\cygwin") if root) if isfile(path)), None)
    if cygwin_bash:
        shells.append(Shell("cygwin", "Cygwin", [cygwin_bash, "--login", "-i"], POSIX, {"CHERE_INVOKING": "1"}))
    msys_bash = "C:\\msys64\\usr\\bin\\bash.exe"
    if isfile(msys_bash):
        shells.append(Shell("msys2", "MSYS2", [msys_bash, "--login", "-i"], POSIX, {"MSYSTEM": "UCRT64", "CHERE_INVOKING": "1"}))
    wsl = which("wsl.exe")
    if wsl and wsl_installed():
        shells.append(Shell("wsl", "WSL", [wsl], OTHER))  # arranca en la carpeta pedida, pero desde aquí no se ve si está ocupado
    return shells


def _unix_shells(environ, which) -> list[Shell]:
    """Bash va delante: es el que se abre si no se elige otro, aunque el usuario tenga otro en $SHELL. Ese se añade si no es de los conocidos."""
    shells = [Shell(name, label, [path], family_of(path)) for name, label in _UNIX_SHELLS if (path := which(name))]
    user = environ.get("SHELL")
    name = ntpath.basename(user) if user else ""
    if user and which(user) and all(shell.key != name for shell in shells):
        shells.append(Shell(name, name, [user], family_of(user)))
    if not shells and which("/bin/sh"):
        shells.append(Shell("sh", "sh", ["/bin/sh"], POSIX))
    return shells


def available_shells(environ=os.environ, which=shutil.which, isfile=os.path.isfile, windows: bool = sys.platform == "win32",
                     cygwin_root=_cygwin_root, wsl_installed=_wsl_has_distribution) -> list[Shell]:
    """Los intérpretes instalados. El primero es el que se usa si no se elige otro: PowerShell en Windows y bash en Linux."""
    return _windows_shells(environ, which, isfile, cygwin_root, wsl_installed) if windows else _unix_shells(environ, which)


def default_shell(environ=os.environ, which=shutil.which) -> Shell:
    shells = available_shells(environ, which)
    if not shells:
        raise OSError(tr("No se encontró ningún intérprete de comandos"))
    return shells[0]


def custom_shell(command: str, isfile=os.path.isfile, windows: bool = sys.platform == "win32") -> Shell:
    """Un programa escrito por el usuario, con sus argumentos. Si todo el texto es un archivo, no hacen falta comillas por sus espacios."""
    command = command.strip()
    try:
        argv = [command] if isfile(command) else [part.strip('"') if windows else part for part in shlex.split(command, posix=not windows)]
    except ValueError as exc:
        raise OSError(tr("No se entiende el programa de la terminal: {exc}", exc=exc)) from None
    if not argv:
        raise OSError(tr("No se ha indicado el programa de la terminal"))
    family = family_of(argv[0])
    return Shell(CUSTOM, ntpath.basename(argv[0]), argv, family, {"CHERE_INVOKING": "1"} if windows and family in (POSIX, FISH) else {})


def chosen_shell(key: str = "", command: str = "", **search) -> Shell:
    """El intérprete elegido en las preferencias. Si ya no está instalado es un error: cambiar a otro sin avisar sería peor."""
    if key == CUSTOM:
        return custom_shell(command)
    if not key:
        shells = available_shells(**search)
        if not shells:
            raise OSError(tr("No se encontró ningún intérprete de comandos"))
        return shells[0]
    found = next((shell for shell in available_shells(**search) if shell.key == key), None)
    if found is None:
        raise OSError(tr("No se encontró la terminal elegida en las preferencias: {name}", name=key))
    return found


def cd_command(path: str, family: str = DEFAULT_FAMILY) -> str | None:
    """La orden que lleva al intérprete a esa carpeta. None si no hay forma segura de escribirla: el nombre de una carpeta nunca debe
    ejecutarse como otra cosa. Con un espacio delante, los intérpretes de Linux no la guardan en su historial."""
    if not path.isprintable():
        return None
    if family == CMD:
        # cmd expande %VARIABLE% incluso entre comillas, y no admite una ruta de red como carpeta actual.
        return None if "%" in path or '"' in path or path.startswith("\\\\") else f'cd /d "{path}"\r'
    if family == POWERSHELL:
        quoted = _POWERSHELL_QUOTES.sub(lambda match: match.group() * 2, path)
        return f"Set-Location -LiteralPath '{quoted}'\r"
    if family in (POSIX, FISH):
        folder = path.replace("\\", "/") if _WINDOWS_PATH.match(path) else path
        if family == FISH:  # dentro de sus comillas simples la barra invertida escapa: con las de bash, un nombre podría cerrarlas y ejecutarse
            return " cd '" + folder.replace("\\", "\\\\").replace("'", "\\'") + "'\r"
        return f" cd {shlex.quote(folder)}\r"
    return None


def prompt_folder(text: str) -> str | None:
    """La carpeta que muestra el indicador habitual de PowerShell, que no le dice al sistema en cuál está. None si el indicador es otro."""
    match = _POWERSHELL_PROMPT.match(text)
    return match.group(1) if match else None


def can_clear_line_with_escape(family: str, windows: bool = sys.platform == "win32") -> bool:
    return windows and family in (CMD, POWERSHELL)


def looks_like_prompt(text: str, family: str, windows: bool = sys.platform == "win32") -> bool:
    """En Linux basta con que haya algo escrito: el sistema dice de verdad quién recibe el teclado."""
    if not text or family not in _PROMPT_ENDINGS:
        return False
    return not windows or text.endswith(_PROMPT_ENDINGS[family])
