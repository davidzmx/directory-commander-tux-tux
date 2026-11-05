"""Lo que hace falta saber de git para las terminales, y las órdenes que se escriben en ellas. Sin Qt."""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .shells import CMD, FISH, OTHER, POSIX, POWERSHELL

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_TIMEOUT = 5
# Órdenes que no piden nada: se escriben tal cual en la terminal.
SIMPLE_COMMANDS = {"status": "git status", "push": "git push", "pull": "git pull", "fetch": "git fetch",
                   "diff": "git --no-pager diff --stat", "log": "git --no-pager log --oneline -n 20"}
# Las que abren una ventana: lo que se elige en ella se añade detrás.
ADD, UNSTAGE, COMMIT = "add", "unstage", "commit"
DEFAULT_COMMANDS = {**SIMPLE_COMMANDS, ADD: "git add --", UNSTAGE: "git restore --staged --", COMMIT: "git commit"}
UNSTAGE_WITHOUT_COMMITS = "git rm --cached --"  # sin ningún commit, restore no tiene de dónde recuperar
CUSTOM_PREFIX = "custom-"
_POWERSHELL_QUOTES = re.compile("['‘’‚‛]")


def git_program(configured: str = "") -> str:
    """La ruta de git, la indicada o la que se encuentre. Vacío si no está instalado."""
    return (configured if configured and shutil.which(configured) else "") or shutil.which("git") or ""


def _run(git: str, folder: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([git, "--no-optional-locks", "-C", folder, *arguments], capture_output=True, stdin=subprocess.DEVNULL, timeout=_TIMEOUT, creationflags=_NO_WINDOW)


def _text(done: subprocess.CompletedProcess) -> str:
    return done.stdout.decode("utf-8", "replace").strip()


def _clean_command(value) -> str:
    """Una sola línea: un salto de línea en la terminal ejecutaría la orden a medias."""
    return value.strip() if isinstance(value, str) and value.strip().isprintable() else ""


def clean_commands(raw) -> dict[str, str]:
    """Las órdenes cambiadas de las que trae el programa. Lo que coincide con la de serie no se guarda."""
    source = raw if isinstance(raw, dict) else {}
    return {key: _clean_command(source.get(key)) for key, default in DEFAULT_COMMANDS.items() if _clean_command(source.get(key)) not in ("", default)}


def clean_custom(raw) -> list[dict]:
    """Las órdenes añadidas. Sin orden no hay nada que ejecutar, y el nombre vacío se sustituye por ella."""
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        key = item.get("key") if isinstance(item, dict) else None
        command = _clean_command(item.get("command")) if isinstance(item, dict) else ""
        if isinstance(key, str) and key.startswith(CUSTOM_PREFIX) and key not in seen and command:
            seen.add(key)
            result.append({"key": key, "label": _clean_command(item.get("label")) or command, "command": command, "files": item.get("files") is True})
    return result


def new_custom_key(custom: list[dict]) -> str:
    taken = {item["key"] for item in custom}
    return next(key for number in range(1, len(taken) + 2) if (key := f"{CUSTOM_PREFIX}{number}") not in taken)


def command_of(config, key: str) -> str:
    """La orden que ejecuta ese botón, con lo que se haya cambiado en las preferencias."""
    if key.startswith(CUSTOM_PREFIX):
        return next((item["command"] for item in clean_custom(config.get("git_custom_commands")) if item["key"] == key), "")
    return clean_commands(config.get("git_commands")).get(key, DEFAULT_COMMANDS.get(key, ""))


def current_branch(git: str, folder: str) -> str | None:
    """La rama activa. Sin rama, el commit en el que se está. None si esa carpeta no está en un repositorio."""
    try:
        branch = _run(git, folder, "symbolic-ref", "--short", "-q", "HEAD")
        if branch.returncode == 0:
            return _text(branch)
        commit = _run(git, folder, "rev-parse", "--short", "HEAD")
        return _text(commit) if commit.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_folder(folder: str) -> str:
    """La carpeta .git del repositorio al que pertenece. También en un worktree o un submódulo, donde .git es un archivo que dice dónde está."""
    current = os.path.abspath(folder)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8", errors="replace") as link:
                    text = link.read(4096).strip()
            except OSError:
                text = ""
            if text.startswith("gitdir:"):
                candidate = os.path.normpath(os.path.join(current, text[len("gitdir:"):].strip()))
        if os.path.isfile(os.path.join(candidate, "HEAD")):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def folder_branch(git: str, folder: str) -> str | None:
    """Como `current_branch`, pero sin lanzar git si no hace falta: los paneles lo miran a menudo y sus carpetas no suelen ser repositorios."""
    found = _git_folder(folder)
    if not found:
        return None
    try:
        with open(os.path.join(found, "HEAD"), encoding="utf-8", errors="replace") as head:
            text = head.read(4096).strip()
    except OSError:
        return None
    if not text:
        return None
    if not text.startswith("ref:"):
        return text[:7]
    reference = text[len("ref:"):].strip()
    if reference == "refs/heads/.invalid":  # el formato nuevo de referencias no guarda aquí la rama
        return current_branch(git, folder)
    return reference.removeprefix("refs/heads/")


def current_user(git: str, folder: str) -> tuple[str, str]:
    """Nombre y correo con los que git firmaría en esa carpeta. Vacíos si no están configurados."""
    try:
        done = _run(git, folder, "config", "--get-regexp", r"^user\.(name|email)$")
    except (OSError, subprocess.SubprocessError):
        return "", ""
    values: dict[str, str] = {}
    for line in _text(done).splitlines():
        key, _space, value = line.partition(" ")
        values[key.lower()] = value.strip()  # el último es el que manda: primero salen los generales y después los del repositorio
    return values.get("user.name", ""), values.get("user.email", "")


def has_commits(git: str, folder: str) -> bool:
    try:
        return _run(git, folder, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


@dataclass(frozen=True)
class Change:
    path: str  # relativa a la carpeta de la terminal
    index: str  # lo que ya está preparado para el commit
    worktree: str  # lo que hay sin preparar
    previous: str = ""  # el nombre anterior, en un archivo renombrado

    @property
    def state(self) -> str:
        return (self.index + self.worktree).strip() or "?"

    @property
    def can_add(self) -> bool:
        return self.worktree != " "

    @property
    def staged(self) -> bool:
        return self.index not in " ?!"

    @property
    def untracked(self) -> bool:
        return self.worktree == "?"

    @property
    def paths(self) -> list[str]:
        """Un renombrado afecta a los dos nombres: con uno solo, el otro se quedaría a medias."""
        return [self.path, self.previous] if self.previous else [self.path]

    @property
    def label(self) -> str:
        return f"{self.state:<3} {self.previous} → {self.path}" if self.previous else f"{self.state:<3} {self.path}"


def changed_files(git: str, folder: str) -> list[Change]:
    prefix = _text(_run(git, folder, "rev-parse", "--show-prefix"))
    done = _run(git, folder, "status", "--porcelain=v1", "-z")
    if done.returncode != 0:
        raise OSError(done.stderr.decode("utf-8", "replace").strip())
    fields = done.stdout.decode("utf-8", "replace").split("\0")
    changes = []
    position = 0
    while position < len(fields) and fields[position]:
        line = fields[position]
        index, worktree, path = line[0], line[1], line[3:]
        previous = ""
        if index in "RC" or worktree in "RC":
            position += 1
            # Una copia no toca el original.
            previous = posixpath.relpath(fields[position], prefix or ".") if "R" in (index, worktree) and position < len(fields) and fields[position] else ""
        relative = posixpath.relpath(path, prefix or ".")
        changes.append(Change(relative + "/" if path.endswith("/") else relative, index, worktree, previous))  # una carpeta sin seguimiento entera
        position += 1
    return changes


def branches(git: str, folder: str) -> list[str]:
    try:
        done = _run(git, folder, "branch", "--format=%(refname:short)")
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in _text(done).splitlines() if line.strip()] if done.returncode == 0 else []


def quote(text: str, family: str) -> str | None:
    """Una palabra para esa terminal. None si no hay forma segura de escribirla: lo que se escribe nunca debe poder ejecutarse como otra cosa."""
    if not text.isprintable():
        return None
    if family == POWERSHELL:
        if '"' in text or text.endswith("\\"):
            return None  # PowerShell 5 se las pasa mal a los programas externos: una palabra podría partirse en varias opciones
        return "'" + _POWERSHELL_QUOTES.sub(lambda match: match.group() * 2, text) + "'"
    if family == CMD:
        return None if "%" in text or '"' in text or "!" in text or text.endswith("\\") else f'"{text}"'
    if family == FISH:
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if family == POSIX:
        return shlex.quote(text)
    return None


def _words(words: list[str], family: str) -> str | None:
    quoted = [quote(word, family) for word in words]
    return None if None in quoted else " ".join(quoted)


def files_command(base: str, paths: list[str], family: str = POSIX) -> str | None:
    """Esa orden con esos archivos detrás. None si no hay ninguno o alguno no se puede escribir sin riesgo."""
    if not paths or family == OTHER:
        return None
    words = _words(paths, family)
    return None if words is None else f"{base} {words}"


def add_command(paths: list[str], family: str = POSIX) -> str | None:
    return files_command(DEFAULT_COMMANDS[ADD], paths, family)


def _chain(first: str, second: str, family: str) -> str:
    """La segunda solo si la primera ha ido bien. Cada intérprete lo escribe a su manera: PowerShell 5 no tiene &&."""
    if family == POWERSHELL:
        return f"{first}; if ($?) {{ {second} }}"
    return f"{first}; and {second}" if family == FISH else f"{first} && {second}"


def commit_command(message: str, family: str = POSIX, amend: bool = False, all_changes: bool = False, paths: list[str] | None = None, to_add: list[str] | None = None,
                   base: str = DEFAULT_COMMANDS[COMMIT], add_base: str = DEFAULT_COMMANDS[ADD]) -> str | None:
    """`git commit`. Cada línea del mensaje va en su -m: un salto de línea en la terminal ejecutaría la orden a medias.
    Con `paths`, solo esos archivos. Los de `to_add` no los conoce git todavía: sin añadirlos antes, el commit los rechazaría."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if family == OTHER or (not lines and not amend):
        return None
    parts = [base]
    if all_changes:
        parts.append("-a")
    if amend:
        parts.append("--amend")
        if not lines:
            parts.append("--no-edit")
    for line in lines:
        quoted = quote(line, family)
        if quoted is None:
            return None
        parts += ["-m", quoted]
    if paths:
        words = _words(paths, family)
        if words is None:
            return None
        parts += ["--", words]
    command = " ".join(parts)
    if to_add:
        adding = files_command(add_base, to_add, family)
        return None if adding is None else _chain(adding, command, family)
    return command


def switch_command(branch: str, family: str = POSIX, create: bool = False) -> str | None:
    quoted = quote(branch, family)
    return None if quoted is None or family == OTHER or branch.startswith("-") else f"git switch {'-c ' if create else ''}{quoted}"


# Lo que se marca en la lista, de más a menos importante: en conflicto, modificado, añadido, renombrado, borrado, nuevo sin seguimiento e ignorado.
STATE_ORDER = "UMARD?!"


def _state_of(index: str, worktree: str) -> str:
    pair = index + worktree
    if "U" in pair or pair in ("AA", "DD"):
        return "U"
    if pair in ("??", "!!"):
        return pair[0]
    for state in "DRA":
        if state in pair:
            return state
    return "M"


def _more_important(first: str | None, second: str) -> str:
    return second if first is None or STATE_ORDER.index(second) < STATE_ORDER.index(first) else first


def folder_states(git: str, folder: str) -> tuple[dict[str, str], dict[str, str], tuple[tuple[str, str], ...]]:
    """El estado de git de lo que hay en la carpeta y debajo: (archivo → estado, carpeta → el estado más importante de lo que
    contiene, (carpeta entera ignorada o sin seguimiento, su estado)). Las rutas, absolutas. Vacío si la carpeta no está en un repositorio."""
    if not _git_folder(folder):
        return {}, {}, ()
    top = _run(git, folder, "rev-parse", "--show-toplevel")
    done = _run(git, folder, "status", "--porcelain=v1", "-z", "--ignored", "--", ".")
    if top.returncode != 0 or done.returncode != 0:
        return {}, {}, ()
    root = os.path.normpath(_text(top))
    base = os.path.normcase(os.path.normpath(folder))
    files: dict[str, str] = {}
    folders: dict[str, str] = {}
    whole: list[tuple[str, str]] = []  # git da la carpeta, no cada archivo: lo de dentro tiene su mismo estado
    fields = done.stdout.decode("utf-8", "replace").split("\0")
    position = 0
    while position < len(fields) and fields[position]:
        line = fields[position]
        index, worktree, path = line[0], line[1], line[3:]
        if index in "RC" or worktree in "RC":
            position += 1  # el nombre anterior
        position += 1
        state = _state_of(index, worktree)
        absolute = os.path.normpath(os.path.join(root, path))
        files[absolute] = _more_important(files.get(absolute), state)
        if state in "!?" and path.endswith("/"):
            whole.append((absolute, state))
            continue  # una carpeta entera sin seguimiento o ignorada: ella misma lleva la marca
        parent = os.path.dirname(absolute)
        while os.path.normcase(parent).startswith(base) and len(parent) >= len(base) and state != "!":
            folders[parent] = _more_important(folders.get(parent), state)
            if os.path.normcase(parent) == base:
                break
            parent = os.path.dirname(parent)
    return files, folders, tuple(whole)


def state_of(path: str, files: dict[str, str], folders: dict[str, str], whole: tuple[tuple[str, str], ...]) -> tuple[str, bool] | None:
    """(estado, si es el de lo que contiene una carpeta) de una ruta, o None si no tiene cambios."""
    path = os.path.normpath(path)
    if path in files:
        return files[path], False
    if path in folders:
        return folders[path], True
    for folder, state in whole:
        if path.startswith(folder + os.sep):
            return state, False
    return None
