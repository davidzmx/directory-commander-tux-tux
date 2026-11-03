"""Exportar e importar favoritos y configuración en archivos JSON.

Un archivo importado puede venir de cualquier sitio: lo que no se entiende o tiene un tipo
inesperado se descarta y se informa, en lugar de llegar a la configuración.
"""
from __future__ import annotations

import json
import os

from . import bar_buttons, favorites as favorites_tree, frequent, pane_colors
from .i18n import tr

FAVORITES_FORMAT = "directory-commander-tux-tux-favorites"
SETTINGS_FORMAT = "directory-commander-tux-tux-settings"
FORMAT_VERSION = 1
MAX_FILE_SIZE = 16 * 1024 * 1024

# Lo que viaja en un archivo de configuración. No incluye la sesión ni las carpetas recientes,
# que son propias de cada equipo. Tampoco `ffmpeg_dir` ni el programa de la terminal: importarlos
# haría que se ejecutase lo que hubiera elegido quien creó el archivo. Por lo mismo, tampoco las órdenes de git ni el comparador externo.
SETTINGS_TYPES: dict[str, type] = {
    "theme": str, "language": str, "dest_tint": bool, "confirm_delete": bool, "use_trash": bool, "expandable_folders": bool,
    "friendly_dates": str, "show_favorites_bar": bool, "lock_bars": bool, "full_row_select": bool, "hover_info": bool,
    "viewer_autoplay": bool, "viewer_repeat": bool, "video_window_repeat": bool, "bar_buttons": dict, "bar_order": dict, "viewer_muted": bool, "viewer_volume": int, "viewer_above_metadata": bool, "folder_tabs": dict, "open_images": str, "open_archives": str, "open_videos": str, "tree_level_colors": bool, "shell_menu_items": bool, "context_menus": dict,
    "show_hidden": bool, "favorites": list, "favorites_autosort": bool, "recent_list": dict, "history_size": int, "smart_favorites": dict, "rename_presets": dict, "toolbar": list, "styles": list, "style": str,
    "layouts": dict, "tabgroups": dict, "general_terminal_full_width": bool, "pane_color_scheme": str, "pane_color_customs": list, "pane_path_pale_light": bool, "pane_path_pale_dark": bool, "pane_frames_on": bool, "pane_frames": str, "pane_frames_after": int, "pane_frame_width": int, "pane_frame_active_width": int, "pane_frame_dim_on": bool, "pane_frame_dim": int, "pane_frame_colors": list, "unfold_archives": bool, "path_folder_menus": bool, "find_templates": dict, "saved_sessions": list, "restore_session": bool, "find_panel_place": str, "find_window_columns": int, "git_enabled": bool, "git_show_branch": bool, "git_show_user": bool, "git_pane_branch": bool, "git_run_commands": bool,
}
_ALLOWED_VALUES = {"find_panel_place": {"bottom", "window"}, "pane_frames": set(pane_colors.FRAME_MODES), "find_window_columns": {1, 2}, "theme": {"dark", "light", "auto"}, "friendly_dates": {"off", "today", "normal"},
                   "open_images": {"viewer", "system"}, "open_archives": {"system", "browse"},
                   "open_videos": {"viewer", "system"}}


def _write_json(path: str, data: dict) -> None:
    """Escritura atómica: un fallo no estropea un archivo anterior con ese nombre."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fout:
            json.dump(data, fout, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_json(path: str):
    if os.path.getsize(path) > MAX_FILE_SIZE:
        raise ValueError(tr("El archivo es demasiado grande para ser una configuración"))
    try:
        with open(path, encoding="utf-8-sig") as fin:
            return json.load(fin)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(tr("El archivo no es un JSON válido: {exc}", exc=exc)) from exc


def clean_favorites(items) -> tuple[list[dict], int]:
    """Favoritos válidos y cuántos se descartaron."""
    return favorites_tree.clean(items)


def export_favorites(path: str, favorites: list[dict]) -> None:
    _write_json(path, {"format": FAVORITES_FORMAT, "version": FORMAT_VERSION, "favorites": clean_favorites(favorites)[0]})


def read_favorites(path: str) -> tuple[list[dict], int]:
    """Admite un archivo exportado o una simple lista de favoritos."""
    data = _read_json(path)
    if isinstance(data, dict):
        if data.get("format") not in (FAVORITES_FORMAT, None) or "favorites" not in data:
            raise ValueError(tr("El archivo no contiene favoritos"))
        data = data["favorites"]
    if not isinstance(data, list):
        raise ValueError(tr("El archivo no contiene favoritos"))
    return clean_favorites(data)


def portable_settings(config_data: dict) -> dict:
    return {key: config_data[key] for key in SETTINGS_TYPES if key in config_data}


def export_settings(path: str, config_data: dict) -> None:
    _write_json(path, {"format": SETTINGS_FORMAT, "version": FORMAT_VERSION, "settings": portable_settings(config_data)})


def _sanitize(key: str, value):
    """Valor utilizable o None si no lo es."""
    expected = SETTINGS_TYPES[key]
    if not isinstance(value, expected):
        return None
    if key in _ALLOWED_VALUES and value not in _ALLOWED_VALUES[key]:
        return None
    if key == "favorites":
        return clean_favorites(value)[0]
    if key == "toolbar":
        return [item for item in value if isinstance(item, dict)] or None
    if key == "bar_buttons":
        return bar_buttons.normalize(value)
    if key == "bar_order":
        return bar_buttons.normalize_order(value)
    if key == "recent_list":
        return frequent.normalize_recent(value)
    if key == "smart_favorites":
        return frequent.normalize_smart(value)
    if key == "history_size":
        return max(1, min(frequent.MAX_HISTORY_LIMIT, value))
    if key == "pane_color_customs":
        return pane_colors.clean_customs(value)
    if key in ("pane_frame_width", "pane_frame_active_width"):
        return pane_colors.frame_width(value)
    if key == "pane_frame_dim":
        return pane_colors.frame_dim(value)
    if key == "pane_frames_after":
        return pane_colors.frames_after(value)
    if key == "pane_frame_colors":
        return pane_colors.clean_frame_colors(value)
    if key == "styles":
        return [item for item in value if isinstance(item, dict) and isinstance(item.get("name"), str)] or None
    return value


def read_settings(path: str) -> tuple[dict, list[str]]:
    """Ajustes válidos y nombres de lo descartado. También admite una copia de `config.json`."""
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError(tr("El archivo no es una configuración de Directory Commander Tux Tux"))
    if data.get("format") == SETTINGS_FORMAT:
        source = data.get("settings")
        if not isinstance(source, dict):
            raise ValueError(tr("El archivo de configuración está incompleto"))
    elif "config_version" in data:
        source = {k: v for k, v in data.items() if k in SETTINGS_TYPES}
    else:
        raise ValueError(tr("El archivo no es una configuración de Directory Commander Tux Tux"))
    settings: dict = {}
    ignored: list[str] = []
    for key, value in source.items():
        cleaned = _sanitize(key, value) if key in SETTINGS_TYPES else None
        if cleaned is None:
            ignored.append(key)
        else:
            settings[key] = cleaned
    return settings, ignored
