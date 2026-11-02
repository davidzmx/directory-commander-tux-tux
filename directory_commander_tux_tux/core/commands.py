"""Lenguaje de comandos: cada botón, atajo y menú es un texto que se interpreta aquí."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from .i18n import tr

_TOKEN = re.compile(r'(?:[^\s"]+|"[^"]*")+')
_DATE_CODE = re.compile(r"\{(date|time)(?:\|D?T?#?([^}]*))?\}")
_DATE_PARTS = (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S"))

# Argumentos que se entienden en cada comando. `Copy UPDATEALL` no debe comportarse como un
# `Copy` normal, así que lo que no figure aquí se rechaza en lugar de ignorarse.
_IGNORED = {"USEQUALKEYS", "HEADING", "NOOPENINTABS", "SHOWICONS", "CONFIRM", "INVERT"}
KNOWN_ARGS: dict[str, set[str]] = {
    "go": {"UP", "BACK", "FORWARD", "ROOT", "HOME", "HOMEDIR", "REFRESH", "PATHFIELD", "PATHFIELDPATHTREE", "TABNEW",
           "TABCLOSE", "TABNEXT", "TABPREV", "SWAP", "DEST", "BACKLIST", "FORWARDLIST", "NEWTAB", "FROMSEL",
           "DRIVEBUTTONS", "TABGROUPSAVE", "TABGROUPLIST"},
    "copy": {"MOVE", "HERE", "TO", "AS", "DUPLICATE", "ADDTOARCHIVE", "ADDTOZIP", "ZIP", "ARCHIVE", "EXTRACT",
             "MAKELINK", "CREATEFOLDER", "UPDATEALL", "UPDATEEXISTING", "PATTERN", "IGNOREEXT"},
    "delete": {"NORECYCLE", "QUIET", "SHIFT"},
    "createfolder": {"NAME", "ZIP", "ARCHIVE"},
    "createfile": {"NAME"},
    "rename": {"ADVANCED", "SIMPLE", "PRESET"},
    "select": {"ALL", "NONE", "INVERT", "PATTERN", "SOURCETODEST", "DESTTOSOURCE", "DESELECTNOMATCH", "RESELECT",
               "SIMILAR", "TOCHECKS", "FROMCHECKS"},
    "clipboard": {"COPY", "CUT", "PASTE", "COPYNAMES", "COPYPATH"},
    "getsizes": {"HASH"},
    "compare": {"FILES"},
    "properties": {"CURRENT"},
    "setattr": {"MODIFIED"},
    "show": {"AUTOFILELIST", "SLIDESHOW", "FULLSCREEN"},
    "undo": {"REDO"},
    "prefs": {"PAGE", "BARS", "STYLELIST", "LAYOUTSAVE", "LAYOUTLIST", "LAYOUTEDIT", "THEMES", "IMPORT", "EXPORT"},
    "help": {"ABOUT"},
    "favorites": {"ADD", "ADDDIALOG", "IMPORT", "EXPORT", "MENU", "EDIT", "SMART"},
    "cli": {"DOSPROMPT"},
    "archive": set(),
    "extract": set(),
    "close": {"PROGRAM"},
    "filetype": {"NEW", "NEWMENU", "CONTEXTMENU"},
    "image": {"CONVERT", "HERE", "ROTATE", "REPLACE", "PRESERVEDATE", "ADDSUFFIX", "HEIGHT", "WIDTH",
              "PRESERVEASPECTRATIO"},
    "toolbar": {"NAME", "STATE", "LOCAL", "TOGGLE", "LIST", "RESETDEFAULTS"},
    "recent": {"CLEAR"},
    "find": set(),
    "print": {"FOLDER", "AS", "TO", "FLATVIEW", "QUIET"},
    "split": set(),
    "join": set(),
    "play": set(),
}
_FREE_FORM = {"run", "marker"}
_SET_BARE = {"CONTENTTYPE", "SPACER", "QUICKFILTER"}
_UNSUPPORTED_VALUES = {("copy", "MAKELINK:junction"), ("toolbar", "LIST:sets"), ("image", "CONVERT:!list")}
# Comandos que no hacen nada útil si no llevan alguno de estos argumentos.
_REQUIRED_ARGS = {"print": {"FOLDER"}}
_UTILITY_VALUES = {"find", "sync", "dupe", "jobs", "log", "filelog", "off", "toggle"}

# Modificadores `@` que solo afectan a cómo se muestra el botón, o que ya se interpretan aquí.
# Cualquier otro (`@if`, `@set`, `@admin`…) cambia lo que hace la función: esa función no se ejecuta.
_SAFE_MODIFIERS = {"nodeselect", "disablenosel", "toggle", "icon", "label", "hideif", "hideblock", "nofilenamequoting",
                   "enableif", "disableif", "showif", "keydown", "confirm"}
_KEY_NAMES = ("ctrl", "shift", "alt")


def _modifier_name(line: str) -> str:
    return re.split(r"[:\s]", line[1:], maxsplit=1)[0].lower()


def _keydown_matches(spec: str, pressed: set[str]) -> bool:
    spec = spec.strip().lower()
    if spec.startswith("!"):
        return not _keydown_matches(spec[1:], pressed)
    if spec == "common":
        return True
    if spec == "any":
        return bool(pressed)
    if spec == "none":
        return not pressed
    return {key for key in _KEY_NAMES if key in spec} == pressed


def plan_function(lines: list[str], pressed: set[str] | None = None) -> list[tuple[str, str]]:
    """Convierte las líneas de un botón en pasos ("run", instrucción) o ("confirm", pregunta).

    Un bloque `@keydown:shift` solo cuenta si esa tecla está pulsada, de modo que un botón con
    variantes ejecuta una sola y no todas seguidas.
    """
    pressed = pressed or set()
    plan: list[tuple[str, str]] = []
    active = True
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@"):
            name = _modifier_name(line)
            value = line.split(":", 1)[1] if ":" in line else ""
            if name == "keydown":
                active = _keydown_matches(value, pressed)
            elif name == "confirm" and active:
                plan.append(("confirm", value.split("|", 1)[0] or tr("¿Continuar?")))
            continue
        if active:
            plan.append(("run", line))
    return plan


def function_unavailable(lines: list[str], known_set_keys: set[str]) -> str | None:
    """Motivo por el que un botón completo no se puede ejecutar, o None si se puede."""
    for raw in lines:
        line = raw.strip()
        if line.startswith("@"):
            name = _modifier_name(line)
            if name not in _SAFE_MODIFIERS:
                return tr("modificador @{name}", name=name)
            continue
        reason = unsupported_reason(line, known_set_keys)
        if reason:
            return reason
    return None


def tokenize(text: str) -> list[str]:
    return [token.replace('"', "") for token in _TOKEN.findall(text)]


@dataclass
class Command:
    name: str
    tokens: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    values: dict[str, str] = field(default_factory=dict)

    def has(self, flag: str) -> bool:
        return flag.upper() in self.flags

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key.upper(), default)

    @property
    def first(self) -> str:
        """Primer argumento tal como se escribió (p. ej. una ruta)."""
        return self.tokens[0] if self.tokens else ""


def parse(text: str) -> Command | None:
    """Interpreta `Copy MOVE TO="C:\\x y"`. Nombre y claves no distinguen mayúsculas."""
    tokens = tokenize(text.strip())
    if not tokens:
        return None
    command = Command(tokens[0].lower(), tokens[1:])
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if sep and key:
            command.values[key.upper()] = value
        else:
            command.flags.add(token.upper())
    return command


def unsupported_reason(text: str, known_set_keys: set[str]) -> str | None:
    """Motivo por el que una instrucción no se puede ejecutar, o None si se puede.

    `known_set_keys` son las opciones de `Set` que implementa la interfaz.
    """
    line = text.strip()
    if not line or line.startswith("@"):
        return None
    first = line.split(" ", 1)[0]
    if is_web_url(first) and "{" not in first:
        return None
    if line.startswith(("=", "[", "]")) or "://" in first:
        return tr("usa funciones de script o de red")
    command = parse(line)
    if command is None:
        return None
    if command.name in _FREE_FORM:
        return None
    if command.name == "set":
        for key, value in command.values.items():
            if key not in known_set_keys:
                return tr("opción Set {key}", key=key)
            if key == "UTILITY" and value.split(",")[0].lower() not in _UTILITY_VALUES:
                return tr("utilidad {value}", value=value)
        for flag in command.flags - _SET_BARE - _IGNORED:
            return tr("opción Set {flag}", flag=flag)
        return None
    known = KNOWN_ARGS.get(command.name)
    if known is None:
        return tr("comando {name}", name=command.name)
    required = _REQUIRED_ARGS.get(command.name)
    if required and not (required & command.flags or required & command.values.keys()):
        return tr("{name} sin {join}", name=command.name, join=', '.join(sorted(required)))
    for key, value in command.values.items():
        if key not in known and key not in _IGNORED:
            return tr("argumento {key}", key=key)
        if (command.name, key) in _UNSUPPORTED_VALUES or (command.name, f"{key}:{value}") in _UNSUPPORTED_VALUES:
            return tr("argumento {key}={value}", key=key, value=value)
    for position, token in enumerate(command.tokens):
        upper = token.upper()
        if "=" in token or upper in known or upper in _IGNORED:
            continue
        if command.name == "go" and position == 0:
            if "://" in token or token.lower() in ("/network", "/quickaccess"):
                return tr("destino {token}", token=token)
            continue  # es una ruta
        if command.name == "copy" and position > 0 and command.tokens[position - 1].upper() in ("AS", "PATTERN"):
            continue
        if command.name == "go" and token.startswith("!"):
            continue
        return tr("argumento {token}", token=token)
    return None


def is_web_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def expand_date_codes(text: str, now: datetime | None = None) -> str:
    """Sustituye `{date|yyyy-MM-dd}` y `{time|HH-mm-ss}` por la fecha y hora actuales."""
    moment = now or datetime.now()

    def replace(match: re.Match) -> str:
        pattern = match.group(2) or ("yyyy-MM-dd" if match.group(1) == "date" else "HH-mm-ss")
        for token, code in _DATE_PARTS:
            pattern = pattern.replace(token, code)
        return moment.strftime(pattern)

    return _DATE_CODE.sub(replace, text)


def expand_args(tokens: list[str], source: str, dest: str, selected: list[str]) -> list[str]:
    """Sustituye marcadores después de tokenizar, así un nombre con espacios sigue siendo
    un solo argumento y no se interpreta como parte del comando."""
    first = selected[0] if selected else ""
    simple = {"{sourcepath}": source, "{destpath}": dest, "{filepath}": first}
    result: list[str] = []
    for token in tokens:
        if token == "{allfilepath}":
            result.extend(selected)
            continue
        for marker, value in simple.items():
            token = token.replace(marker, value)
        result.append(token)
    return result
