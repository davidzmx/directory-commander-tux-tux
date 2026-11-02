"""Buscar archivos: qué se pide, cómo se comprueba cada archivo y el comando equivalente en Linux. Sin Qt.

Lo que se pide es un diccionario sencillo, el mismo que se guarda en las plantillas. En Windows busca este módulo.
En Linux busca `find`, y aquí solo se comprueba después lo que `find` no sabe hacer.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as day_time

from . import archive
from .i18n import tr
from .listing import CONTENT_TYPES, _is_hidden, _windows_attrs

UNITS = {"bytes": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
PERIODS = {"minutes": 1, "hours": 60, "days": 60 * 24, "weeks": 60 * 24 * 7, "months": 60 * 24 * 30, "years": 60 * 24 * 365}
DATE_FIELDS = ("modified", "created", "accessed")
ATTRIBUTES = ("hidden", "readonly", "system", "archive")
DEFAULT_COLLECTION = tr("Resultados de la Búsqueda")
BATCH = 200
_CHUNK = 1024 * 1024
_OVERLAP = 4096
_MAX_MEMBER_TEXT = 256 * 1024 * 1024
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0



def default_excludes() -> list[str]:
    if sys.platform == "win32":
        return ["$Recycle.Bin\\*", "System Volume Information\\*"]
    return ["/proc/*", "/sys/*", "/dev/*", "/run/*"]


def default_state() -> dict:
    today = date.today().isoformat()
    return {
        "name": {"text": "", "wildcards": True, "regex": False, "any_word": False, "case": False, "diacritics": False, "partial": True},
        "content": {"text": "", "wildcards": False, "regex": False, "any_word": False, "case": False, "diacritics": False, "partial": True, "utf8": False},
        "kind": {"kind": "all", "content_type": next(iter(CONTENT_TYPES))},
        "date": {"op": "ignore", "field": "modified", "first": today, "second": today, "amount": 7, "unit": "days"},
        "time": {"op": "ignore", "first": "00:00", "second": "23:59"},
        "size": {"op": "ignore", "first": 1.0, "second": 10.0, "unit": "MB"},
        "roots": [], "follow": True, "subfolders": True, "archives": False, "quiet": True,
        "exclude": default_excludes(), "exclude_hidden": False,
        "advanced": False, "clauses": [],
        "target": "source_new", "collection": DEFAULT_COLLECTION, "clear": True,
    }


def merged_state(raw) -> dict:
    """Lo guardado, con lo que le falte de lo predeterminado: una plantilla antigua, o editada a mano, debe seguir sirviendo."""
    state = default_state()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in state and isinstance(state[key], dict) and isinstance(value, dict):
                state[key].update(value)
            elif key in state and type(value) is type(state[key]):
                state[key] = value
    return state


# ---------------------------------------------------------------- texto
def _strip_marks(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def _text_pattern(query: dict, word: str) -> str:
    if query.get("regex"):
        return word
    source = _strip_marks(word) if query.get("diacritics") else word
    escaped = re.escape(source)
    if query.get("wildcards"):
        escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
    return escaped


def _words(query: dict) -> list[str]:
    text = query.get("text", "")
    return text.split() if query.get("any_word") else ([text] if text else [])


def compile_text(query: dict, whole_word_when_exact: bool = False):
    """Una función que dice si un texto coincide. None si no se pide nada. Una expresión regular mal escrita es un ValueError."""
    words = _words(query)
    if not words:
        return None
    flags = re.DOTALL | (0 if query.get("case") else re.IGNORECASE)
    try:
        patterns = [re.compile(_text_pattern(query, word), flags) for word in words]
    except re.error as exc:
        raise ValueError(tr("Patrón no válido: {exc}", exc=exc)) from exc
    fold = _strip_marks if query.get("diacritics") else (lambda text: text)
    if query.get("partial", True):
        return lambda text: any(pattern.search(fold(text)) for pattern in patterns)
    if whole_word_when_exact:
        bounded = [re.compile(rf"(?<!\w)(?:{pattern.pattern})(?!\w)", flags) for pattern in patterns]
        return lambda text: any(pattern.search(fold(text)) for pattern in bounded)
    return lambda text: any(pattern.fullmatch(fold(text)) for pattern in patterns)


def _encoding(head: bytes, query: dict) -> tuple[str, int]:
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", 2
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8", 3
    if query.get("utf8") or sys.platform != "win32":
        return "utf-8", 0
    return "cp1252", 0


def _content_matches(item: "Item", query: dict, matcher, is_cancelled) -> bool:
    if item.is_dir:
        return False
    try:
        if item.in_archive:
            data = archive.read_member(item.path, _MAX_MEMBER_TEXT)
            if data is None:
                return False
            encoding, skip = _encoding(data[:4], query)
            return bool(matcher(data[skip:].decode(encoding, "replace")))
        with open(item.path, "rb") as handle:
            chunk = handle.read(_CHUNK)
            encoding, skip = _encoding(chunk[:4], query)
            chunk = chunk[skip:]
            tail = ""
            while chunk:
                if is_cancelled and is_cancelled():
                    return False
                text = tail + chunk.decode(encoding, "replace")
                if matcher(text):
                    return True
                tail = text[-_OVERLAP:]
                chunk = handle.read(_CHUNK)
    except OSError:
        return False
    return False


# ---------------------------------------------------------------- fecha, hora, tamaño
def _parse_date(text) -> date:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        return date.today()


def _parse_time(text) -> day_time:
    try:
        return day_time.fromisoformat(str(text))
    except ValueError:
        return day_time(0, 0)


def date_matches(test: dict, stamp: float, now: float | None = None) -> bool:
    op = test.get("op", "ignore")
    if op == "ignore":
        return True
    if op == "within":
        minutes = max(0, int(test.get("amount", 0))) * PERIODS.get(test.get("unit"), PERIODS["days"])
        return ((now if now is not None else time.time()) - stamp) <= minutes * 60
    day = datetime.fromtimestamp(stamp).date()
    first, second = _parse_date(test.get("first")), _parse_date(test.get("second"))
    low, high = min(first, second), max(first, second)
    return {"on": day == first, "before": day < first, "after": day > first,
            "between": low <= day <= high, "not_between": not low <= day <= high}.get(op, True)


def _minutes(moment: day_time) -> int:
    return moment.hour * 60 + moment.minute


def time_matches(test: dict, stamp: float) -> bool:
    op = test.get("op", "ignore")
    if op == "ignore":
        return True
    current = _minutes(datetime.fromtimestamp(stamp).time())
    first, second = _minutes(_parse_time(test.get("first"))), _minutes(_parse_time(test.get("second")))
    low, high = min(first, second), max(first, second)
    distance = min(abs(current - first), 24 * 60 - abs(current - first))  # las 23:30 están a media hora de las 00:00
    return {"at": current == first, "before": current < first, "after": current > first, "between": low <= current <= high,
            "not_between": not low <= current <= high, "around_1": distance <= 60, "around_6": distance <= 360}.get(op, True)


def _size_bytes(test: dict, key: str) -> int:
    return round(float(test.get(key, 0)) * UNITS.get(test.get("unit"), 1))


def size_matches(test: dict, size: int) -> bool:
    op = test.get("op", "ignore")
    if op == "ignore":
        return True
    first, second = _size_bytes(test, "first"), _size_bytes(test, "second")
    low, high = min(first, second), max(first, second)
    return {"equals": size == first, "smaller": size < first, "greater": size > first, "between": low <= size <= high,
            "not_between": not low <= size <= high, "around_25": abs(size - first) <= first * 0.25,
            "around_50": abs(size - first) <= first * 0.5}.get(op, True)


# ---------------------------------------------------------------- lo que se comprueba de cada elemento
@dataclass
class Item:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    modified: float = 0.0
    created: float = 0.0
    accessed: float = 0.0
    attrs: str = ""
    hidden: bool = False
    in_archive: bool = False


def _item_from_stat(path: str, name: str, info: os.stat_result, is_dir: bool) -> Item:
    attrs = _windows_attrs(info) if sys.platform == "win32" else ("-" if info.st_mode & stat.S_IWUSR else "r")
    created = getattr(info, "st_birthtime", info.st_ctime)
    return Item(path, name, is_dir, 0 if is_dir else info.st_size, info.st_mtime, created, info.st_atime, attrs, _is_hidden(name, info))


def item_from_path(path: str) -> Item | None:
    """Lo necesario de una ruta ya conocida: la de un resultado anterior o la que devuelve otro programa."""
    name = os.path.basename(path.rstrip("/\\")) or path
    try:
        info = os.lstat(path)
    except OSError:
        location = archive.split_archive_path(path)
        if location is None or not location[1]:
            return None
        try:
            member = next((m for m in archive.list_members(location[0]) if m.name == location[1]), None)
        except OSError:
            return None
        return None if member is None else Item(path, name, member.is_dir, member.size, member.mtime, member.mtime, member.mtime, in_archive=True)
    return _item_from_stat(path, name, info, os.path.isdir(path))


def attribute_matches(item: Item, attribute: str) -> bool:
    if attribute == "hidden":
        return item.hidden
    if sys.platform != "win32":
        return attribute == "readonly" and "r" in item.attrs
    return {"readonly": "r", "system": "s", "archive": "a"}.get(attribute, "?") in item.attrs


def kind_matches(test: dict, item: Item) -> bool:
    kind = test.get("kind", "all")
    if kind == "files":
        return not item.is_dir
    if kind == "folders":
        return item.is_dir
    if kind == "type":
        group = CONTENT_TYPES.get(test.get("content_type"))
        return not item.is_dir and (group is None or os.path.splitext(item.name)[1][1:].lower() in group[1])
    return True


@dataclass
class Search:
    """Lo pedido, ya listo para comprobar muchos elementos sin volver a compilarlo."""
    state: dict
    name_matcher: object = None
    content_matcher: object = None
    rules: list = field(default_factory=list)
    is_cancelled: object = None

    def clause_matches(self, clause: dict, item: Item) -> bool:
        kind = clause.get("field")
        matched = True
        if kind == "name":
            matched = bool(clause["matcher"](item.name)) if clause.get("matcher") else True
        elif kind == "content":
            matched = _content_matches(item, clause.get("value", {}), clause["matcher"], self.is_cancelled) if clause.get("matcher") else True
        elif kind == "kind":
            matched = kind_matches(clause.get("value", {}), item)
        elif kind == "date":
            value = clause.get("value", {})
            matched = date_matches(value, getattr(item, value.get("field", "modified"), item.modified))
        elif kind == "time":
            matched = time_matches(clause.get("value", {}), item.modified)
        elif kind == "size":
            matched = not item.is_dir and size_matches(clause.get("value", {}), item.size)
        elif kind == "attribute":
            matched = attribute_matches(item, clause.get("value", {}).get("attribute", "hidden"))
        return matched != bool(clause.get("negate"))

    def matches(self, item: Item) -> bool:
        """Lo que cuesta poco primero: leer el contenido solo cuando todo lo demás ya coincide."""
        state = self.state
        if state.get("advanced"):
            result = None
            for clause in self.rules:
                if result is None or clause.get("join", "and") == "and":
                    # Y: si ya no coincide, ni se mira. O: si ya coincide, tampoco.
                    result = (result is None or result) and self.clause_matches(clause, item)
                else:
                    result = result or self.clause_matches(clause, item)
            return True if result is None else bool(result)
        if not kind_matches(state["kind"], item):
            return False
        if self.name_matcher and not self.name_matcher(item.name):
            return False
        if not date_matches(state["date"], getattr(item, state["date"].get("field", "modified"), item.modified)):
            return False
        if not time_matches(state["time"], item.modified):
            return False
        if state["size"].get("op", "ignore") != "ignore" and (item.is_dir or not size_matches(state["size"], item.size)):
            return False
        if self.content_matcher and not _content_matches(item, state["content"], self.content_matcher, self.is_cancelled):
            return False
        return True


def prepare(state: dict, is_cancelled=None) -> Search:
    search = Search(state, is_cancelled=is_cancelled)
    search.name_matcher = compile_text(state["name"])
    search.content_matcher = compile_text(state["content"], whole_word_when_exact=True)
    for clause in state.get("clauses", ()):
        prepared = dict(clause)
        if clause.get("field") == "name":
            prepared["matcher"] = compile_text(clause.get("value", {}))
        elif clause.get("field") == "content":
            prepared["matcher"] = compile_text(clause.get("value", {}), whole_word_when_exact=True)
        search.rules.append(prepared)
    return search


# ---------------------------------------------------------------- recorrer
def is_excluded(path: str, name: str, patterns: list[str]) -> bool:
    """Un nombre o una ruta, con comodines. «carpeta\\*» es lo mismo que la carpeta: no se entra en ella."""
    fold = str.casefold if sys.platform == "win32" else (lambda text: text)
    path, name = fold(path), fold(name)
    for raw in patterns:
        pattern = fold(raw)
        if pattern.endswith(("\\*", "/*")):
            pattern = pattern[:-2]
        if fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, f"*{os.sep}{pattern}"):
            return True
    return False


def _walk(state: dict, is_cancelled, denied: list[str], archives_only: bool = False):
    excludes = state.get("exclude", [])
    for root in state.get("roots", []):
        pending = [root]
        while pending:
            if is_cancelled and is_cancelled():
                return
            folder = pending.pop()
            try:
                with os.scandir(folder) as it:
                    found = list(it)
            except OSError:
                denied.append(folder)
                continue
            for entry in found:
                if is_cancelled and is_cancelled():
                    return
                try:
                    info = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError:
                    continue
                if is_excluded(entry.path, entry.name, excludes):
                    continue
                item = _item_from_stat(entry.path, entry.name, info, is_dir)
                if is_dir:
                    if state.get("exclude_hidden") and (item.hidden or "s" in item.attrs):
                        continue
                    if state.get("subfolders", True) and not entry.is_symlink() and not entry.is_junction():
                        pending.append(entry.path)
                if not archives_only:
                    yield item
                if state.get("archives") and not is_dir and archive.has_archive_suffix(entry.name):
                    yield from _archive_items(entry.path, denied)


def _archive_items(path: str, denied: list[str]):
    try:
        members = archive.list_members(path)
    except OSError:
        denied.append(path)
        return
    for member in members:
        yield Item(os.path.join(path, *member.name.split("/")), member.name.rsplit("/", 1)[-1], member.is_dir, member.size,
                   member.mtime, member.mtime, member.mtime, in_archive=True)


@dataclass
class Found:
    paths: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)  # carpetas o comprimidos en los que no se pudo mirar
    cancelled: bool = False


class _Collector:
    def __init__(self, on_batch) -> None:
        self.on_batch = on_batch
        self.found = Found()
        self._pending: list[str] = []

    def add(self, path: str) -> None:
        self.found.paths.append(path)
        self._pending.append(path)
        if self.on_batch and len(self._pending) >= BATCH:
            self.flush()

    def flush(self) -> None:
        if self.on_batch and self._pending:
            self.on_batch(self._pending)
        self._pending = []


def run_search(state: dict, is_cancelled=None, on_batch=None) -> Found:
    search = prepare(state, is_cancelled)
    collector = _Collector(on_batch)
    if uses_command():
        _run_command(state, search, collector, is_cancelled)
        items = _walk(state, is_cancelled, collector.found.denied, archives_only=True) if state.get("archives") else ()
    else:
        items = _walk(state, is_cancelled, collector.found.denied)
    for item in items:
        if is_cancelled and is_cancelled():
            break
        if search.matches(item):
            collector.add(item.path)
    collector.flush()
    collector.found.cancelled = bool(is_cancelled and is_cancelled())
    return collector.found


# ---------------------------------------------------------------- el comando de Linux
def uses_command() -> bool:
    return sys.platform != "win32" and shutil.which("find") is not None


def _glob_text(text: str) -> str:
    return re.sub(r"([\\\[\]*?])", r"\\\1", text)


def _glob_for(query: dict, word: str) -> str:
    if query.get("wildcards"):
        pattern = word.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    else:
        pattern = _glob_text(word)
    if query.get("partial", True):
        pattern = f"*{pattern.strip('*') if query.get('wildcards') else pattern}*"
    return pattern


def _name_tests(query: dict) -> list[str] | None:
    """None si `find` no sabe hacerlo: una expresión regular de Python o ignorar los diacríticos."""
    words = _words(query)
    if not words:
        return []
    if query.get("regex") or query.get("diacritics"):
        return None
    option = "-name" if query.get("case") else "-iname"
    tests = [token for word in words for token in ("-o", option, _glob_for(query, word))][1:]
    return ["(", *tests, ")"] if len(words) > 1 else tests


def _grep_tests(query: dict) -> list[str] | None:
    words = _words(query)
    if not words:
        return []
    if query.get("diacritics"):
        return None
    options = ["-q", "-s"]
    if not query.get("case"):
        options.append("-i")
    if not query.get("partial", True):
        options.append("-w")
    if query.get("regex"):
        options.append("-P")
        patterns = ["-e", "|".join(f"(?:{word})" for word in words)]
    elif query.get("wildcards"):
        options.append("-E")
        patterns = [token for word in words for token in ("-e", re.escape(word).replace(r"\*", ".*").replace(r"\?", "."))]
    else:
        options.append("-F")
        patterns = [token for word in words for token in ("-e", word)]
    return ["!", "-type", "d", "-exec", "grep", *options, *patterns, "--", "{}", ";"]


def _date_tests(test: dict) -> list[str] | None:
    op = test.get("op", "ignore")
    if op == "ignore":
        return []
    letter = {"modified": "m", "created": "B", "accessed": "a"}.get(test.get("field"), "m")
    if op == "within":
        minutes = max(0, int(test.get("amount", 0))) * PERIODS.get(test.get("unit"), PERIODS["days"])
        return None if letter == "B" else [f"-{letter}min", f"-{minutes}"]
    first, second = _parse_date(test.get("first")), _parse_date(test.get("second"))
    low, high = min(first, second), max(first, second)
    option = f"-newer{letter}t"
    next_day = timedelta(days=1)
    return {"on": [option, first.isoformat(), "!", option, (first + next_day).isoformat()],
            "before": ["!", option, first.isoformat()],
            "after": [option, (first + next_day).isoformat()],
            "between": [option, low.isoformat(), "!", option, (high + next_day).isoformat()],
            "not_between": ["(", "!", option, low.isoformat(), "-o", option, (high + next_day).isoformat(), ")"]}.get(op)


def _size_tests(test: dict) -> list[str] | None:
    op = test.get("op", "ignore")
    if op == "ignore":
        return []
    first, second = _size_bytes(test, "first"), _size_bytes(test, "second")
    low, high = min(first, second), max(first, second)
    if op in ("around_25", "around_50"):
        share = 0.25 if op == "around_25" else 0.5
        low, high, op = round(first * (1 - share)), round(first * (1 + share)), "between"
    # -size +Nc significa «más de N bytes»: para que los límites cuenten hay que correrlos uno.
    return ["!", "-type", "d", *{"equals": ["-size", f"{first}c"], "smaller": ["-size", f"-{first}c"], "greater": ["-size", f"+{first}c"],
                                 "between": ["-size", f"+{max(low - 1, 0)}c", "-size", f"-{high + 1}c"] if low else ["-size", f"-{high + 1}c"],
                                 "not_between": ["(", "-size", f"-{low}c", "-o", "-size", f"+{high}c", ")"]}.get(op, [])]


def _kind_tests(test: dict) -> list[str]:
    kind = test.get("kind", "all")
    if kind == "files":
        return ["!", "-type", "d"]
    if kind == "folders":
        return ["-type", "d"]
    if kind == "type":
        group = CONTENT_TYPES.get(test.get("content_type"))
        if group is not None:
            options = [token for extension in sorted(group[1]) for token in ("-o", "-iname", f"*.{extension}")][1:]
            return ["!", "-type", "d", "(", *options, ")"]
    return []


def _clause_tests(clause: dict) -> list[str] | None:
    kind, value = clause.get("field"), clause.get("value", {})
    tests = {"name": lambda: _name_tests(value), "content": lambda: _grep_tests(value), "kind": lambda: _kind_tests(value), "date": lambda: _date_tests(value),
             "size": lambda: _size_tests(value)}.get(kind, lambda: None)()
    if tests is None:
        return None
    return ["!", "(", *tests, ")"] if clause.get("negate") and tests else tests


@dataclass
class Command:
    argv: list[str]
    python_checks: list[str]  # lo que `find` no sabe hacer: lo comprueba el programa con lo que devuelve
    environment: dict = field(default_factory=dict)


def _exclude_tests(state: dict) -> list[str]:
    options: list[str] = []
    for raw in state.get("exclude", []):
        pattern = raw[:-2] if raw.endswith(("/*", "\\*")) else raw
        options += ["-o", "-path" if "/" in pattern else "-name", pattern]
    if state.get("exclude_hidden"):
        options += ["-o", "(", "-type", "d", "-name", ".*", ")"]
    return ["(", *options[1:], ")", "-prune", "-o"] if options else []


def build_command(state: dict) -> Command:
    """`find` con todo lo que sabe hacer. Lo demás se apunta en `python_checks`."""
    checks: list[str] = []
    tests: list[str] = []
    if state.get("advanced"):
        joined: list[str] = []
        for clause in state.get("clauses", ()):
            part = _clause_tests(clause)
            if part is None:
                # En una mezcla de Y y O no se puede quitar una parte sin cambiar el resultado: se comprueban todas después.
                checks = [tr("todas las reglas, porque alguna no se puede expresar con find")]
                joined = []
                break
            if not part:
                continue
            if joined and clause.get("join", "and") == "or":
                joined.append("-o")
            joined += ["(", *part, ")"]
        tests = joined
    else:
        steps = ((_kind_tests(state["kind"]), ""), (_name_tests(state["name"]), tr("el nombre (expresión regular o sin diacríticos)")),
                 (_date_tests(state["date"]), tr("la fecha de creación")),
                 (_size_tests(state["size"]), ""), (_grep_tests(state["content"]), tr("el texto, sin diacríticos")))
        for part, note in steps:
            if part is None:
                checks.append(note)
            else:
                tests += part
        if state["time"].get("op", "ignore") != "ignore":
            checks.append(tr("la hora"))
    if state.get("archives"):
        checks.append(tr("lo que hay dentro de los comprimidos"))
    roots = state.get("roots", []) or ["."]
    depth = ["-mindepth", "1"] + ([] if state.get("subfolders", True) else ["-maxdepth", "1"])
    print_part = ["(", *tests, ")", "-print0"] if tests else ["-print0"]
    environment = {"LC_ALL": "C.UTF-8"} if state["content"].get("utf8") and state["content"].get("text") else {}
    return Command(["find", *roots, *depth, *_exclude_tests(state), *print_part], checks, environment)


_BARE = {"(": r"\(", ")": r"\)", "!": r"\!", ";": r"\;"}


def describe_command(command: Command) -> str:
    """El texto que se enseña: se puede pegar en una terminal y hace lo mismo."""
    words = [_BARE.get(word, shlex.quote(word)) for word in command.argv]
    prefix = "".join(f"{key}={value} " for key, value in command.environment.items())
    text = prefix + " ".join(words)
    if command.python_checks:
        text += "\n# " + tr("Después, el programa comprueba: {checks}", checks=", ".join(check for check in command.python_checks if check))
    return text


def _run_command(state: dict, search: Search, collector: _Collector, is_cancelled) -> None:
    command = build_command(state)
    process = subprocess.Popen(command.argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               env={**os.environ, **command.environment})
    only_checks = _residual_state(state, command)
    residual = prepare(only_checks, is_cancelled) if only_checks is not None else None
    tail = b""
    try:
        while chunk := process.stdout.read(65536):
            if is_cancelled and is_cancelled():
                process.kill()
                return
            parts = (tail + chunk).split(b"\0")
            tail = parts.pop()
            for raw in parts:
                path = os.fsdecode(raw)
                if residual is None:
                    collector.add(path)
                    continue
                item = item_from_path(path)
                if item is not None and residual.matches(item):
                    collector.add(path)
    finally:
        process.stdout.close()
        process.wait()


def _residual_state(state: dict, command: Command) -> dict | None:
    """Lo pedido, solo con lo que `find` no ha podido comprobar. None si no queda nada."""
    if not [check for check in command.python_checks if check != tr("lo que hay dentro de los comprimidos")]:
        return None
    if state.get("advanced"):
        return state
    blank = default_state()
    residual = {**state, "kind": blank["kind"], "size": blank["size"], "content": state["content"] if state["content"].get("diacritics") else blank["content"]}
    if _name_tests(state["name"]) is not None:
        residual["name"] = blank["name"]
    if _date_tests(state["date"]) is not None:
        residual["date"] = blank["date"]
    return residual

