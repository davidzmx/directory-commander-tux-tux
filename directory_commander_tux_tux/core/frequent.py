"""Carpetas recientes y favoritos inteligentes: sus ajustes y su puntuación. Sin Qt.

Cada acción en una carpeta le da puntos, y las que acumulan bastantes se ofrecen solas debajo de los favoritos.
"""
from __future__ import annotations

import fnmatch
import os

from . import archive, favorites
from .i18n import N_
from .listing import COMPUTER_PATH, is_results

DEFAULT_RECENT = {"enabled": True, "max": 10, "virtual": True, "skip_transit": True}
MAX_RECENT_LIMIT = 1000
MAX_HISTORY_LIMIT = 1000
TRANSIT_SECONDS = 3.0  # entrar en una subcarpeta antes de este tiempo y sin hacer nada: solo se estaba de paso

# Clave, texto y puntos de serie.
ACTIONS = (
    ("sizes", N_("Calcular tamaños de carpetas"), 5),
    ("attributes", N_("Cambiar atributos o marcas de tiempo"), 10),
    ("transit", N_("Carpeta de tránsito"), 0),
    ("copy_to", N_("Copiar a"), 10),
    ("clip_copy", N_("Copiar al portapapeles"), 5),
    ("copy_from", N_("Copiar de"), 5),
    ("clip_cut", N_("Cortar al portapapeles"), 5),
    ("create_folder", N_("Crear carpeta"), 8),
    ("double_click", N_("Doble clic"), 3),
    ("run", N_("Ejecutar un programa externo"), 5),
    ("delete", N_("Eliminar archivos o carpetas"), 8),
    ("show", N_("Mostrar archivos o reproducir sonidos"), 8),
    ("move_to", N_("Mover a"), 10),
    ("move_from", N_("Mover de"), 5),
    ("paste", N_("Pegar del portapapeles"), 10),
    ("rename", N_("Renombrar archivos o carpetas"), 8),
    ("split_join", N_("Unir o dividir archivos"), 8),
    ("context_menu", N_("Usar el menú contextual de un archivo"), 0),
    ("thumbnails", N_("Ver en modo miniaturas"), 8),
)
DEFAULT_SMART = {"enabled": True, "check_startup": True, "max_shown": 10, "min_points": 40, "table_size": 100, "exclude": [],
                 "points": {key: points for key, _label, points in ACTIONS}}
_LIMITS = {"max_shown": (1, 100), "min_points": (0, 100000), "table_size": (1, 10000)}


def _number(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def normalize_recent(raw) -> dict:
    source = raw if isinstance(raw, dict) else {}
    result = {key: source[key] if isinstance(source.get(key), bool) else value for key, value in DEFAULT_RECENT.items() if key != "max"}
    result["max"] = _number(source.get("max"), DEFAULT_RECENT["max"], 1, MAX_RECENT_LIMIT)
    return result


def normalize_smart(raw) -> dict:
    source = raw if isinstance(raw, dict) else {}
    result = {key: source[key] if isinstance(source.get(key), bool) else DEFAULT_SMART[key] for key in ("enabled", "check_startup")}
    for key, (low, high) in _LIMITS.items():
        result[key] = _number(source.get(key), DEFAULT_SMART[key], low, high)
    exclude = source.get("exclude")
    result["exclude"] = [{"pattern": item["pattern"], "subfolders": item.get("subfolders") is True}
                         for item in (exclude if isinstance(exclude, list) else [])
                         if isinstance(item, dict) and isinstance(item.get("pattern"), str) and item["pattern"].strip()]
    points = source.get("points") if isinstance(source.get("points"), dict) else {}
    result["points"] = {key: _number(points.get(key), default, 0, 100000) for key, _label, default in ACTIONS}
    return result


def is_virtual(path: str) -> bool:
    return path == COMPUTER_PATH or is_results(path) or path.startswith("sftp://") or archive.is_inside_archive(path)


def is_excluded(path: str, exclude: list[dict]) -> bool:
    """Una regla vale para esa carpeta, o también para las que tenga dentro si lo pide."""
    target = os.path.normcase(os.path.normpath(path))
    for rule in exclude:
        pattern = os.path.normcase(os.path.normpath(rule["pattern"]))
        current = target
        while True:
            if fnmatch.fnmatchcase(current, pattern):
                return True
            parent = os.path.dirname(current)
            if not rule["subfolders"] or parent == current:
                break
            current = parent
    return False


def award(config, path: str, action: str) -> None:
    """Suma a la carpeta los puntos de esa acción. Si la tabla se llena, se olvida lo que tiene menos puntos."""
    settings = normalize_smart(config.get("smart_favorites"))
    points = settings["points"].get(action, 0)
    if not settings["enabled"] or points <= 0 or not path or is_virtual(path) or is_excluded(path, settings["exclude"]):
        return
    table = config.get("smart_points")
    if not isinstance(table, dict):
        table = config["smart_points"] = {}
    current = table.get(path, 0)
    table[path] = (current if isinstance(current, int) else 0) + points
    trim(table, settings["table_size"])


def trim(table: dict, size: int) -> None:
    if len(table) > size:
        for forgotten in sorted(table, key=table.get)[:len(table) - size]:
            del table[forgotten]


def shown(config) -> list[dict]:
    """Las carpetas que hay que ofrecer: las de más puntos, sin repetir favoritos ni lo excluido."""
    settings = normalize_smart(config.get("smart_favorites"))
    table = config.get("smart_points")
    if not settings["enabled"] or not isinstance(table, dict):
        return []
    chosen = {os.path.normcase(node["path"]) for node in favorites.walk(config.get("favorites", []))}
    frequent = [{"path": path, "points": points} for path, points in table.items()
                if isinstance(points, int) and points >= settings["min_points"]
                and os.path.normcase(path) not in chosen and not is_excluded(path, settings["exclude"])]
    return sorted(frequent, key=lambda item: -item["points"])[:settings["max_shown"]]


def gone_folders(paths: list[str]) -> list[str]:
    """Las que ya no existen. Si no está ni su unidad puede ser un disco desconectado: esas se conservan."""
    return [path for path in paths if not os.path.isdir(path) and os.path.isdir(os.path.splitdrive(path)[0] + os.sep)]
