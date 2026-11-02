"""Idiomas de la interfaz. Sin Qt, para que también los use el núcleo.

El programa está escrito en español y ese texto es la clave: `tr("Abrir")` devuelve la traducción del idioma elegido
o, si no la hay, el propio texto. Así una traducción incompleta sigue funcionando.

Cada idioma es un archivo JSON:

    {"name": "Français", "code": "fr", "strings": {"Abrir": "Ouvrir", "No existe: {path}": "Introuvable : {path}"}}

Se buscan en la carpeta `languages` del programa y en la de la configuración del usuario, que tiene prioridad:
para añadir un idioma o corregir una traducción basta con dejar ahí un archivo, sin tocar el programa.

El idioma se fija al arrancar, antes de importar la interfaz, y cambiarlo requiere reiniciar: algunas tablas de textos
se calculan una sola vez, al importarse. Lo que además se usa como identificador (opciones de los menús, nombres de
estilos, botones guardados en la configuración) se marca con `N_` y se traduce solo al mostrarlo.
"""
from __future__ import annotations

import json
import os

SOURCE_CODE = "es"
SOURCE_NAME = "Español"
FALLBACK_CODE = "en"  # si el idioma del sistema no está disponible
AUTO = "auto"
LANGUAGE_FOLDER = "languages"
TEMPLATE_CODE = "plantilla"
MAX_FILE_SIZE = 16 * 1024 * 1024

_strings: dict[str, str] = {}
_current = SOURCE_CODE


def N_(text: str) -> str:
    """Marca un texto para traducirlo más adelante, al mostrarlo. Lo que devuelve sigue siendo la clave."""
    return text


def tr(text: str, /, **values) -> str:
    """Traduce un texto. `values` rellena sus `{marcas}`; una traducción mal escrita no debe romper nada: se usa el original."""
    translated = _strings.get(text) or text
    if not values:
        return translated
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError):
        return text.format(**values)


def current_language() -> str:
    return _current


def builtin_folder() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), LANGUAGE_FOLDER)


def read_language_file(path: str) -> dict | None:
    """Contenido de un archivo de idioma, o None si no sirve. Viene de cualquier sitio: no se da nada por hecho."""
    try:
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return None
        with open(path, encoding="utf-8-sig") as fin:
            data = json.load(fin)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("strings"), dict):
        return None
    code = data.get("code") or os.path.splitext(os.path.basename(path))[0]
    if not isinstance(code, str) or not code or code in (AUTO, TEMPLATE_CODE):
        return None
    name = data.get("name")
    strings = {key: value for key, value in data["strings"].items() if isinstance(key, str) and isinstance(value, str) and value}
    return {"code": code, "name": name if isinstance(name, str) and name else code, "strings": strings, "path": path}


def available_languages(user_folder: str = "") -> dict[str, dict]:
    """Los idiomas que hay, por código. El de la carpeta del usuario manda sobre el que trae el programa."""
    found: dict[str, dict] = {SOURCE_CODE: {"code": SOURCE_CODE, "name": SOURCE_NAME, "strings": {}, "path": ""}}
    for folder in (builtin_folder(), user_folder):
        try:
            names = sorted(os.listdir(folder)) if folder else []
        except OSError:
            continue
        for name in names:
            if name.lower().endswith(".json"):
                data = read_language_file(os.path.join(folder, name))
                if data is not None:
                    found[data["code"]] = data
    return found


def resolve(preference: str, system_locale: str, available) -> str:
    """El código que corresponde a una preferencia. Con `auto`, o si el elegido ya no existe, el del sistema."""
    if preference in available:
        return preference
    for candidate in (system_locale, system_locale.split("_")[0]):
        if candidate in available:
            return candidate
    return FALLBACK_CODE if FALLBACK_CODE in available else SOURCE_CODE


def set_language(code: str, user_folder: str = "") -> None:
    global _current, _strings
    language = available_languages(user_folder).get(code)
    _current = language["code"] if language else SOURCE_CODE
    _strings = dict(language["strings"]) if language else {}


def set_strings(code: str, strings: dict[str, str]) -> None:
    """Para las pruebas: un idioma sin archivo."""
    global _current, _strings
    _current, _strings = code, dict(strings)


def template_data() -> dict:
    """Un idioma por rellenar, con todos los textos que usa el programa."""
    catalog = read_language_file(os.path.join(builtin_folder(), f"{FALLBACK_CODE}.json"))
    keys = sorted(catalog["strings"]) if catalog else []
    return {"name": "Nuevo idioma", "code": "xx", "strings": dict.fromkeys(keys, "")}


def write_template(user_folder: str) -> str:
    """Crea en la carpeta del usuario un archivo para empezar una traducción, sin pisar ninguno. Devuelve su ruta."""
    os.makedirs(user_folder, exist_ok=True)
    path = os.path.join(user_folder, f"{TEMPLATE_CODE}.json")
    number = 2
    while os.path.exists(path):
        path = os.path.join(user_folder, f"{TEMPLATE_CODE}-{number}.json")
        number += 1
    with open(path, "w", encoding="utf-8") as fout:
        json.dump(template_data(), fout, ensure_ascii=False, indent=2)
    return path
