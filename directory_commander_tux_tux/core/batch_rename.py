"""Renombrado masivo: las reglas de sus dos pestañas, las secuencias y las variables. Sin Qt, para poder probarlo sin interfaz.

Por cada archivo, primero las opciones básicas (cambiar el nombre y la extensión: un texto que reemplaza a lo que había o va delante
o detrás, con una secuencia detrás y, si se pide, en minúsculas o mayúsculas) y luego las avanzadas, sobre el resultado: insertar un
texto y reemplazar una parte, en el nombre, en la extensión o en los dos."""
from __future__ import annotations

import datetime
import os
import re
import struct
from dataclasses import asdict, dataclass, field, fields

from .i18n import N_, tr

NAME_PLACES = ("replace", "before", "after")
CASES = ("lower", "upper", "sentence", "title")
INSERT_PLACES = ("position", "before", "after", "before_extension")
REPLACE_KINDS = ("range", "search")
APPLY_TO = ("name", "extension", "both")
SEQUENCES = ("1", "01", "001", "0001", "A", "a", "0A")  # el primer elemento: lo demás sigue de él
_MONTHS = (N_("enero"), N_("febrero"), N_("marzo"), N_("abril"), N_("mayo"), N_("junio"), N_("julio"), N_("agosto"), N_("septiembre"),
           N_("octubre"), N_("noviembre"), N_("diciembre"))
_SHORT_MONTHS = (N_("ene"), N_("feb"), N_("mar"), N_("abr"), N_("may"), N_("jun"), N_("jul"), N_("ago"), N_("sep"), N_("oct"), N_("nov"), N_("dic"))
_DAYS = (N_("lunes"), N_("martes"), N_("miércoles"), N_("jueves"), N_("viernes"), N_("sábado"), N_("domingo"))
_SHORT_DAYS = (N_("lun"), N_("mar."), N_("mié"), N_("jue"), N_("vie"), N_("sáb"), N_("dom"))
_TIME_CODES = "XpIHMSzZ"
_DATE_CODES = "xDdmbByYaAwWj"
_PHOTO_CODES = "#X #p #I #H #M #S #z #Z #x #D #d #m #b #B #y #Y #a #A #w #W #j".split()
_TAGS = ("WIDTH", "HEIGHT", "MP3TITLE", "MP3ARTIST", "MP3ALBUM", "MP3YEAR", "MP3TRACK", "MP3GENRE", "MP3COMMENT", "MP3MIN", "MP3SEC",
         "MP3BITRATE", "MP3SAMPLERATE", "MP3MODE")
_VARIABLE = re.compile(r"%(?:(" + "|".join(_TAGS) + r")%|(#?)([" + _TIME_CODES + _DATE_CODES + r"%]))")
EXIF_HEAD_BYTES = 256 * 1024


@dataclass
class FileDetails:
    """Lo que pueden insertar las variables. Se lee solo si el texto las usa."""
    modified: float = 0.0  # segundos desde 1970
    taken: datetime.datetime | None = None  # la fecha de captura de una foto
    width: int = 0
    height: int = 0
    tags: dict[str, str] = field(default_factory=dict)  # de un MP3: MP3TITLE, MP3ARTIST…


@dataclass
class BatchRule:
    # Opciones básicas
    name_on: bool = False
    name_text: str = ""
    name_place: str = "replace"
    name_sequence_on: bool = False
    name_sequence: str = "000"
    name_case_on: bool = False
    name_case: str = "lower"
    extension_on: bool = False
    extension_text: str = ""
    extension_place: str = "after"
    extension_sequence_on: bool = False
    extension_sequence: str = "000"
    extension_case_on: bool = False
    extension_case: str = "lower"
    # Opciones avanzadas
    insert_on: bool = False
    insert_text: str = ""
    insert_place: str = "position"
    insert_position: int = 0
    insert_before: str = ""
    insert_after: str = ""
    replace_on: bool = False
    replace_kind: str = "range"
    replace_from: int = 0
    replace_to: int = 0
    replace_search: str = ""
    replace_regex: bool = False
    replace_with: str = ""
    apply_to: str = "name"
    case_sensitive: bool = False

    def to_dict(self) -> dict:
        return {"kind": "batch", **asdict(self)}

    @classmethod
    def from_dict(cls, data) -> "BatchRule":
        """Lo que no se conoce o no tiene el tipo esperado se queda como de serie. Los perfiles de la versión anterior se traducen."""
        if not isinstance(data, dict):
            return cls()
        if data.get("kind") != "batch" and "mode" in data:
            return _from_old_preset(data)
        rule = cls()
        for item in fields(cls):
            value = data.get(item.name)
            if isinstance(value, type(getattr(rule, item.name))) and not (isinstance(value, bool) and item.type == "int"):
                setattr(rule, item.name, value)
        return rule

    def uses(self, pattern: str) -> bool:
        texts = [self.name_text if self.name_on else "", self.extension_text if self.extension_on else "", self.insert_text if self.insert_on else "",
                 self.replace_with if self.replace_on else ""]
        return any(re.search(pattern, text) for text in texts)


def _from_old_preset(data: dict) -> BatchRule:
    """Hasta ahora, un perfil era buscar y reemplazar (texto, comodines o expresión regular) con «[#]» para numerar. Las mayúsculas pasan
    a las opciones básicas, que van antes: lo que pone el reemplazo ya no se convierte."""
    mode, pattern, replacement = data.get("mode"), str(data.get("pattern", "")), str(data.get("replacement", ""))
    if mode == "wildcard":
        pattern = "".join("(.*)" if c == "*" else "(.)" if c == "?" else re.escape(c) for c in pattern)
        counter = iter(range(1, 100))
        replacement = "".join(f"${next(counter)}" if c in "*?" else c for c in replacement)
        mode = "regex"
    rule = BatchRule(replace_on=bool(pattern), replace_kind="search", replace_search=pattern, replace_regex=mode == "regex",
                     replace_with=re.sub(r"\\(\d)", r"$\1", replacement) if mode == "regex" else replacement,
                     apply_to="name" if data.get("ignore_ext", True) else "both")
    case = data.get("case")
    if case in CASES:
        rule.name_on, rule.name_place, rule.name_case_on, rule.name_case = True, "after", True, case
    return rule


# ---------------------------------------------------------------- secuencias
def next_in_sequence(value: str) -> str:
    """El siguiente: 001 → 002, 009 → 010, 0A → 0B, AZ → BA, z → aa. Cada cifra o letra se cuenta como en un cuentakilómetros."""
    characters = list(value)
    for position in range(len(characters) - 1, -1, -1):
        character = characters[position]
        if character.isdigit() and character != "9":
            characters[position] = chr(ord(character) + 1)
            return "".join(characters)
        if character.isalpha() and character not in "zZ" and character.isascii():
            characters[position] = chr(ord(character) + 1)
            return "".join(characters)
        if character == "9":
            characters[position] = "0"
        elif character in "zZ":
            characters[position] = "a" if character == "z" else "A"
        else:
            continue  # un guion o un espacio no cuentan
        if position == 0 or not any(c.isalnum() for c in characters[:position]):
            first = "1" if character == "9" else "a" if character == "z" else "A"
            characters.insert(position, first)
            return "".join(characters)
    return value + "1" if not value else "".join(characters)


# ---------------------------------------------------------------- variables
def has_variables(text: str) -> bool:
    return bool(_VARIABLE.search(text))


def _timezone(moment: datetime.datetime, long: bool) -> str:
    local = moment.astimezone()
    if long:
        return local.tzname() or ""
    offset = local.utcoffset() or datetime.timedelta()
    minutes = int(offset.total_seconds() // 60)
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02}{abs(minutes) % 60:02}"


def _date_part(code: str, moment: datetime.datetime) -> str:
    """Como los códigos de strftime, pero con los nombres en el idioma del programa y sin «:», que no cabe en un nombre de archivo."""
    if code == "X":
        return f"{moment.hour:02}_{moment.minute:02}_{moment.second:02}"
    if code == "p":
        return "AM" if moment.hour < 12 else "PM"
    if code == "I":
        return f"{(moment.hour % 12) or 12:02}"
    if code in "HMSdmyY":
        return moment.strftime("%" + code)
    if code in "zZ":
        return _timezone(moment, code == "Z")
    if code == "x":
        return f"{moment.day:02}-{moment.month:02}-{moment.year}"
    if code == "D":
        return tr("{weekday}, {day} de {month} de {year}", weekday=tr(_DAYS[moment.weekday()]), day=moment.day, month=tr(_MONTHS[moment.month - 1]),
                  year=moment.year)
    if code == "b":
        return tr(_SHORT_MONTHS[moment.month - 1]).capitalize()
    if code == "B":
        return tr(_MONTHS[moment.month - 1]).capitalize()
    if code == "a":
        return tr(_SHORT_DAYS[moment.weekday()]).rstrip(".").capitalize()
    if code == "A":
        return tr(_DAYS[moment.weekday()]).capitalize()
    if code == "w":
        return str((moment.weekday() + 1) % 7)
    if code == "W":
        return moment.strftime("%W")
    if code == "j":
        return moment.strftime("%j")
    return ""


def expand_variables(text: str, details: FileDetails | None) -> str:
    """Pone en el texto las fechas, las medidas o las etiquetas de su archivo. Sin datos de ese archivo, las variables quedan vacías."""
    def value(found: re.Match) -> str:
        tag, photo, code = found.groups()
        if code == "%":
            return "%"
        if details is None:
            return ""
        if tag:
            if tag == "WIDTH":
                return str(details.width) if details.width else ""
            if tag == "HEIGHT":
                return str(details.height) if details.height else ""
            return details.tags.get(tag, "")
        moment = details.taken if photo and details.taken else datetime.datetime.fromtimestamp(details.modified)
        return _date_part(code, moment)

    return _VARIABLE.sub(value, text)


# ---------------------------------------------------------------- reglas
def _split(name: str) -> tuple[str, str]:
    """Nombre y extensión, sin el punto. «.gitignore» es todo nombre."""
    dot = name.rfind(".")
    return (name[:dot], name[dot + 1:]) if dot > 0 else (name, "")


def convert_case(text: str, case: str) -> str:
    if case == "lower":
        return text.lower()
    if case == "upper":
        return text.upper()
    if case == "sentence":
        return text[:1].upper() + text[1:].lower()
    if case == "title":
        return re.sub(r"[^\W_]+", lambda word: word.group(0)[:1].upper() + word.group(0)[1:].lower(), text)
    return text


def _placed(old: str, piece: str, place: str) -> str:
    return piece if place == "replace" else piece + old if place == "before" else old + piece


def _template(replacement: str) -> str:
    """«$1» y «$0» (o «$&»), como en el menú: para `re.sub`, «\\g<1>». Lo demás va tal cual, también las barras."""
    escaped = replacement.replace("\\", "\\\\")
    return re.sub(r"\$(\d{1,2}|&)", lambda found: "\\g<0>" if found.group(1) == "&" else f"\\g<{int(found.group(1))}>", escaped)


def _find(text: str, wanted: str, case_sensitive: bool) -> int:
    return text.find(wanted) if case_sensitive else text.casefold().find(wanted.casefold())


def _advanced(text: str, rule: BatchRule, details: FileDetails | None, search) -> str:
    if rule.insert_on and rule.insert_place != "before_extension":  # esa, en `new_names`: depende de dónde acaba el nombre
        inserted = expand_variables(rule.insert_text, details)
        if rule.insert_place == "position":
            at = min(max(rule.insert_position, 0), len(text))
            text = text[:at] + inserted + text[at:]
        else:
            anchor = rule.insert_before if rule.insert_place == "before" else rule.insert_after
            found = _find(text, anchor, rule.case_sensitive) if anchor else -1
            if found >= 0:
                at = found if rule.insert_place == "before" else found + len(anchor)
                text = text[:at] + inserted + text[at:]
    if rule.replace_on:
        replacement = expand_variables(rule.replace_with, details)
        if rule.replace_kind == "range":
            start = min(max(rule.replace_from, 0), len(text))
            end = min(max(rule.replace_to, start), len(text))
            text = text[:start] + replacement + text[end:]
        elif search is not None:
            text = search.sub(_template(replacement) if rule.replace_regex else lambda _found: replacement, text)
    return text


def _compiled_search(rule: BatchRule):
    if not (rule.replace_on and rule.replace_kind == "search" and rule.replace_search):
        return None
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    try:
        return re.compile(rule.replace_search if rule.replace_regex else re.escape(rule.replace_search), flags)
    except re.error as exc:
        raise ValueError(tr("Expresión regular no válida: {exc}", exc=exc)) from exc


def new_names(names: list[str], rule: BatchRule, details=None) -> list[str]:
    """Los nombres nuevos, en el orden de la lista: la secuencia sigue ese orden. `details(índice)` da los datos de cada archivo para
    las variables. ValueError si la expresión regular no es válida."""
    search = _compiled_search(rule)
    name_sequence = rule.name_sequence or "1"
    extension_sequence = rule.extension_sequence or "1"
    result = []
    for index, old in enumerate(names):
        info = details(index) if details is not None else None
        stem, extension = _split(old)
        if rule.name_on:
            piece = expand_variables(rule.name_text, info)
            if rule.name_sequence_on:
                piece += name_sequence
                name_sequence = next_in_sequence(name_sequence)
            stem = _placed(stem, piece, rule.name_place)
            if rule.name_case_on:
                stem = convert_case(stem, rule.name_case)
        if rule.extension_on:
            piece = expand_variables(rule.extension_text, info)
            if rule.extension_sequence_on:
                piece += extension_sequence
                extension_sequence = next_in_sequence(extension_sequence)
            extension = _placed(extension, piece, rule.extension_place)
            if rule.extension_case_on:
                extension = convert_case(extension, rule.extension_case)
        if rule.insert_on and rule.insert_place == "before_extension":
            stem += expand_variables(rule.insert_text, info)  # al final del nombre, delante del punto, sea a lo que sea que se aplique
        if rule.insert_on or rule.replace_on:
            if rule.apply_to == "name":
                stem = _advanced(stem, rule, info, search)
            elif rule.apply_to == "extension":
                extension = _advanced(extension, rule, info, search)
            else:
                stem, extension = _split(_advanced(f"{stem}.{extension}" if extension else stem, rule, info, search))
        result.append(f"{stem}.{extension}" if extension else stem)
    return result


# ---------------------------------------------------------------- datos de cada archivo
def photo_taken(path: str) -> datetime.datetime | None:
    """La fecha de captura del EXIF de una foto JPEG o TIFF, o None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(EXIF_HEAD_BYTES)
    except OSError:
        return None
    if head[:2] == b"\xff\xd8":
        position = 2
        while position + 4 <= len(head) and head[position] == 0xFF:
            marker, length = head[position + 1], struct.unpack(">H", head[position + 2:position + 4])[0]
            if marker == 0xE1 and head[position + 4:position + 10] == b"Exif\0\0":
                return _exif_date(head[position + 10:position + 2 + length])
            if marker in (0xDA, 0xD9):
                break
            position += 2 + length
        return None
    if head[:4] in (b"II*\0", b"MM\0*"):
        return _exif_date(head)
    return None


def _exif_date(tiff: bytes) -> datetime.datetime | None:
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    order = "<" if tiff[:2] == b"II" else ">"
    found: dict[int, str] = {}

    def walk(offset: int, depth: int) -> None:
        if depth > 2 or not 8 <= offset <= len(tiff) - 2:
            return
        count = struct.unpack_from(order + "H", tiff, offset)[0]
        for entry in range(offset + 2, min(offset + 2 + count * 12, len(tiff) - 11), 12):
            tag, kind, amount, value = struct.unpack_from(order + "HHII", tiff, entry)
            if tag == 0x8769:
                walk(value, depth + 1)
            elif tag in (0x9003, 0x9004, 0x0132) and kind == 2 and amount >= 19 and value + 19 <= len(tiff):
                found[tag] = tiff[value:value + 19].decode("ascii", "replace")

    try:
        walk(struct.unpack_from(order + "I", tiff, 4)[0], 0)
    except struct.error:
        return None
    for tag in (0x9003, 0x9004, 0x0132):  # la de captura, la de digitalización y la del archivo
        try:
            return datetime.datetime.strptime(found[tag], "%Y:%m:%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
    return None


def mp3_tags(path: str) -> dict[str, str]:
    from .media_info import read_media_info

    info = read_media_info(path) if path.lower().endswith(".mp3") else None
    if info is None:
        return {}
    channels = {1: tr("Mono"), 2: tr("Estéreo")}.get(info.channels, str(info.channels or ""))
    return {"MP3TITLE": info.title, "MP3ARTIST": info.artist, "MP3ALBUM": info.album, "MP3YEAR": info.year, "MP3TRACK": info.track,
            "MP3GENRE": info.genre, "MP3COMMENT": info.comment, "MP3MIN": str(int(info.duration // 60)), "MP3SEC": str(int(info.duration)),
            "MP3BITRATE": str(info.bitrate // 1000) if info.bitrate else "", "MP3SAMPLERATE": str(info.sample_rate or ""), "MP3MODE": channels}


def read_details(path: str, rule: BatchRule, image_size=None) -> FileDetails:
    """Solo lo que usan las reglas: abrir cada archivo para buscar su EXIF o sus etiquetas cuesta. `image_size(path)` da (ancho, alto)."""
    try:
        details = FileDetails(modified=os.stat(path).st_mtime)
    except OSError:
        details = FileDetails()
    if rule.uses(r"%#"):
        details.taken = photo_taken(path)
    if rule.uses(r"%(WIDTH|HEIGHT)%") and image_size is not None:
        details.width, details.height = image_size(path)
    if rule.uses(r"%MP3"):
        details.tags = mp3_tags(path)
    return details


VARIABLE_GROUPS = (
    (N_("Hora de modificación del archivo"), [("%X", N_("Hora local (14_05_30)")), ("%p", N_("Indicador AM/PM")), ("%I", N_("Hora (formato de 12 horas)")),
                                               ("%H", N_("Hora (formato de 24 horas)")), ("%M", N_("Minutos")), ("%S", N_("Segundos")),
                                               ("%z", N_("Zona horaria (corta)")), ("%Z", N_("Zona horaria (larga)"))]),
    (N_("Fecha de modificación del archivo"), [("%x", N_("Fecha local corta (08-10-2011)")), ("%D", N_("Fecha local larga")),
                                                ("%d", N_("Día del mes (01-31)")), ("%m", N_("Mes (01-12)")), ("%b", N_("Nombre del mes, corto")),
                                                ("%B", N_("Nombre del mes, largo")), ("%y", N_("Año sin el siglo (00-99)")), ("%Y", N_("Año con el siglo")),
                                                ("%a", N_("Día de la semana, corto")), ("%A", N_("Día de la semana, largo")),
                                                ("%w", N_("Día de la semana (0-6, 0 = domingo)")), ("%W", N_("Semana del año (00-53)")),
                                                ("%j", N_("Día del año (001-366)"))]),
    (N_("Fotos: medidas"), [("%WIDTH%", N_("Ancho (en píxeles)")), ("%HEIGHT%", N_("Alto (en píxeles)"))]),
    (N_("Fotos: hora de captura"), [("%#X", N_("Hora local (14_05_30)")), ("%#H", N_("Hora (formato de 24 horas)")), ("%#M", N_("Minutos")),
                                     ("%#S", N_("Segundos"))]),
    (N_("Fotos: fecha de captura"), [("%#x", N_("Fecha local corta (08-10-2011)")), ("%#D", N_("Fecha local larga")), ("%#d", N_("Día del mes (01-31)")),
                                      ("%#m", N_("Mes (01-12)")), ("%#B", N_("Nombre del mes, largo")), ("%#Y", N_("Año con el siglo"))]),
    (N_("Archivos MP3"), [("%MP3TITLE%", N_("Título")), ("%MP3ARTIST%", N_("Artista")), ("%MP3ALBUM%", N_("Álbum")), ("%MP3YEAR%", N_("Año")),
                           ("%MP3TRACK%", N_("Número de pista")), ("%MP3GENRE%", N_("Género")), ("%MP3COMMENT%", N_("Comentario")),
                           ("%MP3MIN%", N_("Duración en minutos")), ("%MP3SEC%", N_("Duración en segundos")), ("%MP3BITRATE%", N_("Tasa de bits (kbps)")),
                           ("%MP3SAMPLERATE%", N_("Tasa de muestreo")), ("%MP3MODE%", N_("Modo (mono, estéreo)"))]),
)
REGEX_HELP = ((".", N_("Cualquier carácter")), (r"\d", N_("Cualquier dígito (0-9)")), (r"\w", N_("Cualquier carácter de una palabra")),
              (r"\s", N_("Cualquier espacio en blanco")), ("[a-z]", N_("Cualquier letra minúscula")), ("[A-Z]", N_("Cualquier letra mayúscula")),
              ("*", N_("Repetición (cero o más)")), ("+", N_("Repetición (una o más)")), ("?", N_("Opcional")), ("|", N_("Alternativa")),
              ("^", N_("Inicio")), ("$", N_("Final")), ("(", N_("Inicio de subexpresión")), (")", N_("Fin de subexpresión")))
MATCH_HELP = (("$&", N_("Coincidencia completa")), ("$1", N_("Subexpresión n.º 1")), ("$2", N_("Subexpresión n.º 2")), ("$3", N_("Subexpresión n.º 3")))
