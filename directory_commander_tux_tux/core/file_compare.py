"""Comparar dos archivos: si son iguales, qué tienen de distinto y con qué programa externo se puede hacer. Sin Qt."""
from __future__ import annotations

import difflib
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_CHUNK = 1024 * 1024
_TEXT_SAMPLE = 64 * 1024
_MAX_PYTHON_DIFF = 8 * 1024 * 1024  # sin el programa diff, compararlos línea a línea en Python tardaría demasiado a partir de aquí
_DIFF_TIMEOUT = 300
MAX_SHOWN_LINES = 200_000
_ALLOWED_CONTROLS = frozenset(b"\t\n\r\f\x1b\b")

CUSTOM = "custom"
# Clave, nombre, programas en el PATH y, en Windows, dónde se suele instalar. Los argumentos van delante de los dos archivos.
_TOOLS = (
    ("kompare", "Kompare", ("kompare",), (), ()),
    ("meld", "Meld", ("meld",), (r"Meld\Meld.exe",), ()),
    ("kdiff3", "KDiff3", ("kdiff3",), (r"KDiff3\kdiff3.exe",), ()),
    ("bcompare", "Beyond Compare", ("bcompare", "BCompare"), (r"Beyond Compare 5\BCompare.exe", r"Beyond Compare 4\BCompare.exe", r"Beyond Compare 3\BCompare.exe"), ()),
    ("winmerge", "WinMerge", ("WinMergeU",), (r"WinMerge\WinMergeU.exe",), ()),
    ("diffuse", "Diffuse", ("diffuse",), (r"Diffuse\Diffuse.exe",), ()),
    ("tkdiff", "TkDiff", ("tkdiff",), (), ()),
    ("p4merge", "P4Merge", ("p4merge",), (r"Perforce\p4merge.exe",), ()),
    ("code", "Visual Studio Code", ("code",), (r"Microsoft VS Code\Code.exe",), ("--diff",)),
    ("codium", "VSCodium", ("codium",), (r"VSCodium\VSCodium.exe",), ("--diff",)),
)


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    argv: tuple[str, ...]


@dataclass
class TextDiff:
    lines: list[str]  # en formato unificado; sin ninguna, solo cambian los finales de línea o la codificación
    added: int
    removed: int
    truncated: bool = False


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def first_difference(first: str, second: str, is_cancelled=None) -> int | None:
    """La posición del primer byte distinto. None si son idénticos. Se comparan por trozos: pueden ser enormes."""
    if _same_file(first, second):
        return None
    position = 0
    with open(first, "rb") as one, open(second, "rb") as other:
        while True:
            if is_cancelled is not None and is_cancelled():
                raise InterruptedError
            chunk, against = one.read(_CHUNK), other.read(_CHUNK)
            if chunk != against:
                shared = min(len(chunk), len(against))
                return position + next((index for index in range(shared) if chunk[index] != against[index]), shared)
            if not chunk:
                return None
            position += len(chunk)


def identical(first: str, second: str, is_cancelled=None) -> bool:
    """Con distinto tamaño ni hace falta leerlos."""
    return os.path.getsize(first) == os.path.getsize(second) and first_difference(first, second, is_cancelled) is None


def _encoding_of(sample: bytes) -> str | None:
    """La codificación con la que se puede leer como texto. None si no parece texto."""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\0" in sample or any(byte < 32 and byte not in _ALLOWED_CONTROLS for byte in sample):
        return None
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as exc:
        # Puede que la muestra haya cortado un carácter por la mitad.
        return "utf-8" if exc.start >= len(sample) - 3 and len(sample) == _TEXT_SAMPLE else "cp1252"


def _read_sample(path: str) -> bytes:
    with open(path, "rb") as source:
        return source.read(_TEXT_SAMPLE)


def is_text(path: str) -> bool:
    return _encoding_of(_read_sample(path)) is not None


def _read_lines(path: str) -> list[str]:
    encoding = _encoding_of(_read_sample(path)) or "utf-8"
    with open(path, "rb") as source:
        text = source.read().decode(encoding, "replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def diff_program() -> str:
    """El programa diff: en Linux suele estar siempre, y en Windows viene con Git."""
    found = shutil.which("diff")
    if found:
        return found
    git = shutil.which("git")
    bundled = os.path.join(os.path.dirname(os.path.dirname(git)), "usr", "bin", "diff.exe") if git else ""
    return bundled if bundled and os.path.isfile(bundled) else ""


def _count(lines: list[str], limit_reached: bool = False) -> TextDiff:
    added = sum(line.startswith("+") and not line.startswith("+++ ") for line in lines)
    removed = sum(line.startswith("-") and not line.startswith("--- ") for line in lines)
    return TextDiff(lines[:MAX_SHOWN_LINES], added, removed, limit_reached or len(lines) > MAX_SHOWN_LINES)


def _with_program(program: str, first: str, second: str) -> TextDiff | None:
    """None si no puede con ellos, por ejemplo un diff reducido que no conoce alguna opción."""
    arguments = [program, "-u", "--strip-trailing-cr", "--text", "--label", os.path.basename(first) or first, "--label", os.path.basename(second) or second, "--", first, second]
    try:
        done = subprocess.run(arguments, capture_output=True, stdin=subprocess.DEVNULL, timeout=_DIFF_TIMEOUT, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1):
        return None
    return _count(done.stdout.decode("utf-8", "replace").splitlines())


def text_diff(first: str, second: str, program: str | None = None) -> TextDiff | None:
    """Las diferencias de dos archivos de texto. None si son demasiado grandes para compararlos sin el programa diff.
    Ese programa solo sirve con texto en UTF-8: con otra codificación vería bytes distintos donde hay el mismo texto."""
    program = diff_program() if program is None else program
    plain = {_encoding_of(_read_sample(path)) for path in (first, second)} == {"utf-8"}
    if program and plain:
        found = _with_program(program, first, second)
        if found is not None:
            return found
    if max(os.path.getsize(first), os.path.getsize(second)) > _MAX_PYTHON_DIFF:
        return None
    return _count(list(difflib.unified_diff(_read_lines(first), _read_lines(second), os.path.basename(first), os.path.basename(second), lineterm="")))


# ------------------------------------------------------------------ comparadores externos
def _found_on_windows(places: tuple[str, ...]) -> str:
    roots = [os.environ.get(name, "") for name in ("ProgramFiles", "ProgramFiles(x86)")]
    local = os.environ.get("LOCALAPPDATA", "")
    roots.append(os.path.join(local, "Programs") if local else "")
    return next((path for root in roots if root for place in places if os.path.isfile(path := os.path.join(root, place))), "")


def _find_tool(names: tuple[str, ...], places: tuple[str, ...]) -> str:
    """Un .cmd o .bat no vale: Windows lo pasaría por cmd, que interpretaría los caracteres especiales del nombre de un archivo."""
    for name in names:
        found = shutil.which(name)
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    return _found_on_windows(places) if sys.platform == "win32" else ""


def detect_tools() -> list[Tool]:
    """Los comparadores instalados en este equipo."""
    tools = []
    for key, label, names, places, arguments in _TOOLS:
        program = _find_tool(names, places)
        if program:
            tools.append(Tool(key, label, (program, *arguments)))
    return tools


def custom_arguments(command: str, first: str, second: str) -> list[str]:
    """El programa escrito por el usuario. {a} y {b} marcan dónde van los archivos; sin ellos, van al final."""
    windows = sys.platform == "win32"
    if os.path.isfile(command):
        words = [command]
    else:
        try:
            words = [part.strip('"') if windows else part for part in shlex.split(command, posix=not windows)]
        except ValueError:
            return []
    if not words:
        return []
    if "{a}" in words or "{b}" in words:
        return [first if word == "{a}" else second if word == "{b}" else word for word in words]
    return [*words, first, second]


def external_arguments(config, first: str, second: str) -> list[str]:
    """Lo que hay que lanzar, según las preferencias. Vacío: la comparación interna."""
    chosen = config.get("compare_tool", "")
    if not chosen:
        return []
    if chosen == CUSTOM:
        return custom_arguments(str(config.get("compare_command", "")).strip(), first, second)
    tool = next((tool for tool in detect_tools() if tool.key == chosen), None)
    return [*tool.argv, first, second] if tool else []
