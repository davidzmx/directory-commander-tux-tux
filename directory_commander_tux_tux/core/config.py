"""Configuración persistente en JSON, en la ruta habitual de cada sistema."""
from __future__ import annotations

import json
import os
import shutil
import sys

from .. import APP_ID
from . import favorites
from .i18n import AUTO, LANGUAGE_FOLDER, N_, tr
from .pane_colors import DEFAULT_SCHEME, adopt_old_colors

CONFIG_VERSION = 21
PORTABLE_MARKER = "portable.txt"
VISIT_POINTS = 3


def _portable_marker() -> str:
    return os.path.join(os.path.dirname(sys.executable), PORTABLE_MARKER)


def is_portable() -> bool:
    """Versión portable: empaquetada y con la marca junto al programa."""
    return bool(getattr(sys, "frozen", False)) and os.path.isfile(_portable_marker())


def config_dir() -> str:
    override = os.environ.get("DIRECTORY_COMMANDER_TUX_TUX_CONFIG")
    if override:
        return override
    if is_portable():
        return os.path.join(os.path.dirname(sys.executable), "config")
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DirectoryCommanderTuxTux")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_ID)


def _legacy_config_folders() -> list[str]:
    """Dónde guardaba la configuración el programa con sus nombres anteriores, empezando por el último."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return [os.path.join(base, name) for name in ("DirectoryExplorerTux", "DirectoryTux")]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [os.path.join(base, name) for name in ("directory-explorer-tux", "directory-tux")]


def _sep() -> dict:
    return {"separator": True}


def _btn(label: str, icon: str, command: str, tip: str = "", children: list | None = None) -> dict:
    return {"label": label, "icon": icon, "commands": [command], "tip": tip or label, "display": "icon",
            "children": children or []}


def _item(label: str, icon: str, command: str) -> dict:
    return {"label": label, "icon": icon, "commands": [command], "children": []}


THEME_TOGGLE_COMMAND = "Set DARKMODE=Toggle"
METADATA_TOGGLE_COMMAND = "Set METAPANE=Toggle"
VIEWER_TOGGLE_COMMAND = "Set VIEWPANE=Toggle"
TERMINAL_COMMAND = "CLI DOSPROMPT"
TERMINAL_TOGGLE_COMMAND = "Set TERMINAL=Toggle"
GENERAL_TERMINAL_COMMAND = "Set GENERALTERMINAL=Toggle"
OLD_TERMINAL_BUTTON = "Panel de terminal"
COMPARE_COMMAND = "Compare FILES"
FRAMES_TOGGLE_COMMAND = "Set PANEFRAMES=Toggle"
GENERATION_MARKS_COMMAND = "Set GENERATIONMARKS=Toggle"
DISK_USAGE_COMMAND = "DiskUsage"
OLD_SIZES_COMMAND = "GetSizes"
OLD_SIZES_LABEL = "Tamaños de carpeta"
IMAGE_VIEWER_COMMAND = "Show"
DEFAULT_TOOLBAR = [
    _btn(N_("Atrás"), "goback", "Go BACK", N_("Atrás (Alt+←)")),
    _btn(N_("Adelante"), "goforward", "Go FORWARD", N_("Adelante (Alt+→)")),
    _btn(N_("Subir"), "goup", "Go UP", N_("Subir un nivel (Alt+↑)"), [
        _item(N_("Subir un nivel"), "goup", "Go UP"),
        _item(N_("Ir a la raíz"), "goroot", "Go ROOT"),
        _item(N_("Abrir la carpeta superior en otra pestaña"), "goup", "Go UP NEWTAB"),
    ]),
    _sep(),
    _btn(N_("Seleccionar todo"), "selectall", "Select ALL", N_("Seleccionar todo (Ctrl+A)"), [
        _item(N_("Seleccionar todo"), "selectall", "Select ALL"),
        _item(N_("Quitar selección"), "selectnone", "Select NONE"),
        _item(N_("Invertir selección"), "selectinvert", "Select INVERT"),
        _item(N_("Recuperar la selección anterior"), "reselect", "Select RESELECT"),
    ]),
    _btn(N_("Seleccionar por patrón"), "selectwild", "Select PATTERN", N_("Seleccionar por patrón (Ctrl+S)"), [
        _item(N_("Por patrón…"), "selectwild", "Select PATTERN"),
        _item(N_("Lo que también está en destino"), "selectall", "Select SOURCETODEST=in"),
        _item(N_("Lo que falta en destino"), "selectinvert", "Select SOURCETODEST=notin"),
    ]),
    _sep(),
    _btn(N_("Copiar"), "copy", "Copy", N_("Copiar la selección al panel de destino (F6)"), [
        _item(N_("Copiar a destino"), "copy", "Copy"),
        _item(N_("Copiar con otro nombre…"), "copy", "Copy AS"),
        _item(N_("Duplicar aquí"), "copy", "Copy DUPLICATE"),
        _item(N_("Copiar a destino y comprobar la copia"), "copy", "Copy VERIFY"),
        _item(N_("Crear enlace simbólico en destino"), "shortcut", "Copy MAKELINK=softlink"),
    ]),
    _btn(N_("Mover"), "move", "Copy MOVE", N_("Mover la selección al panel de destino (Mayús+F6)"), [
        _item(N_("Mover a destino"), "move", "Copy MOVE"),
        _item(N_("Mover con otro nombre…"), "move", "Copy MOVE AS"),
        _item(N_("Mover a una carpeta nueva aquí…"), "makedir", "Copy MOVE HERE CREATEFOLDER"),
    ]),
    _sep(),
    _btn(N_("Nueva carpeta"), "makedir", "CreateFolder", N_("Nueva carpeta (F7)"), [
        _item(N_("Nueva carpeta…"), "makedir", "CreateFolder"),
        _item(N_("Carpeta con la fecha y hora"), "makedir", 'CreateFolder NAME="{date|yyyy-MM-dd} {time|HH-mm-ss}"'),
        _item(N_("Archivo de texto nuevo"), "newtextfile", "FileType NEW=.txt"),
    ]),
    _btn(N_("Renombrar"), "rename", "Rename ADVANCED", N_("Renombrar uno o varios (Ctrl+F2)")),
    _btn(N_("Eliminar"), "delete", "Delete", N_("Enviar a la papelera (Supr)"), [
        _item(N_("Enviar a la papelera"), "delete", "Delete"),
        _item(N_("Eliminar definitivamente"), "deletepermanent", "Delete NORECYCLE"),
    ]),
    _btn(N_("Deshacer"), "undo", "Undo", N_("Deshacer la última operación (Ctrl+Z)")),
    _sep(),
    _btn(N_("Marcas de IA"), "aimarks", GENERATION_MARKS_COMMAND, N_("Mostrar u ocultar las marcas de prompt y workflow de las imágenes y vídeos generados con IA")),
    _btn(N_("Mostrar imágenes"), "show", IMAGE_VIEWER_COMMAND, N_("Abrir el visor de imágenes"), [
        _item(N_("Abrir el visor"), "show", "Show"),
        _item(N_("Presentación a pantalla completa"), "slideshow", "Show SLIDESHOW FULLSCREEN"),
    ]),
    _btn(N_("Modo de visualización"), "cyclemodes", "Set VIEW=Cycle,List,Details,SmallIcons,LargeIcons,Tiles,Thumbnails",
         N_("Cambiar de vista"), [
             _item(N_("Lista"), "listmode", "Set VIEW=List"),
             _item(N_("Detalles"), "detailsmode", "Set VIEW=Details"),
             _item(N_("Iconos pequeños"), "smallicons", "Set VIEW=SmallIcons"),
             _item(N_("Iconos grandes"), "largeicons", "Set VIEW=LargeIcons"),
             _item(N_("Mosaicos"), "tile", "Set VIEW=Tiles"),
             _item(N_("Miniaturas"), "thumbnails", "Set VIEW=Thumbnails"),
         ]),
    _btn(N_("Vista dual"), "dualdisplay", "Set DUAL=Toggle", N_("Mostrar u ocultar el segundo panel")),
    _btn(N_("Panel de metadatos"), "metapane", METADATA_TOGGLE_COMMAND, N_("Mostrar u ocultar los metadatos (Ctrl+Mayús+M)")),
    _btn(N_("Panel de visualización"), "viewerpane", VIEWER_TOGGLE_COMMAND, N_("Mostrar u ocultar el visor (F3)")),
    _sep(),
    _btn(N_("Propiedades"), "properties", "Properties", N_("Propiedades (Alt+Intro)")),
    _btn(N_("Atributos"), "setattr", "SetAttr", N_("Cambiar atributos"), [
        _item(N_("Cambiar atributos…"), "setattr", "SetAttr"),
        _item(N_("Poner la fecha actual"), "setdate", "SetAttr MODIFIED=now"),
    ]),
    _btn(N_("Uso del disco"), "diskusage", DISK_USAGE_COMMAND, N_("Uso del disco: qué carpetas y archivos ocupan más, en una lista y un mapa"), [
        _item(N_("Uso del disco…"), "diskusage", DISK_USAGE_COMMAND),
        _item(N_("Calcular tamaños de carpetas"), "getsizes", OLD_SIZES_COMMAND),
    ]),
    _sep(),
    _btn(N_("Buscar"), "search", "Set UTILITY=Find", N_("Buscar archivos (Ctrl+Shift+F)")),
    _btn(N_("Sincronizar"), "sync", "Set UTILITY=Sync", N_("Comparar los dos paneles")),
    _btn(N_("Duplicados"), "duplicates", "Set UTILITY=Dupe", N_("Buscar archivos duplicados")),
    _btn(N_("Comparar archivos"), "compare", COMPARE_COMMAND, N_("Comparar los dos archivos seleccionados")),
    _btn(N_("Terminal"), "terminal", TERMINAL_COMMAND, N_("Abrir una terminal aquí (Ctrl+Alt+T)")),
    _btn(N_("Terminal general"), "terminalgeneral", GENERAL_TERMINAL_COMMAND, N_("Mostrar u ocultar la terminal general (Ctrl+Mayús+G)")),
    _btn(N_("Marcos de los paneles"), "paneframes", FRAMES_TOGGLE_COMMAND, N_("Mostrar u ocultar el marco de color de cada panel")),
    _sep(),
    _btn(N_("Cambiar tema"), "themetoggle", THEME_TOGGLE_COMMAND, N_("Cambiar entre el tema claro y el oscuro")),
]

DEFAULT_STYLES = [
    {"name": N_("Vertical doble panel"), "dual": "Vertical", "tree": True, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Vertical doble árbol"), "dual": "Vertical", "tree": True, "double_tree": True, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Vertical panel-terminal"), "dual": "Off", "tree": True, "viewer": False, "meta": False, "view": "list", "terminals": ["first"], "terminal_position": "right"},
    {"name": N_("Comandante"), "dual": "Vertical", "tree": False, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Explorador"), "dual": "Off", "tree": True, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Horizontal doble panel"), "dual": "Horizontal", "tree": True, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Horizontal doble árbol"), "dual": "Horizontal", "tree": True, "double_tree": True, "viewer": False, "meta": False, "view": "list"},
    {"name": N_("Horizontal panel-terminal"), "dual": "Off", "tree": True, "viewer": False, "meta": False, "view": "list", "terminals": ["first"], "terminal_position": "below"},
    {"name": N_("Imágenes"), "dual": "Off", "tree": True, "viewer": True, "meta": False, "view": "thumbnails"},
    {"name": N_("Tira de Fotogramas"), "dual": "Off", "tree": False, "viewer": True, "meta": False, "view": "filmstrip",
     "viewer_area": "top"},
    {"name": N_("Único"), "dual": "Off", "tree": False, "viewer": False, "meta": False, "view": "list"},
]
STYLE_RENAMES = {"Vertical Dual": "Vertical doble panel", "Horizontal Dual": "Horizontal doble panel"}
OLD_METADATA_STYLE = "Metadatos"

THUMB_SIZES = (64, 96, 128, 160, 192, 256)
VIDEO_THUMBNAILS = ("four", "one", "off")
GENERATION_MARK_KINDS = ("both", "prompt", "workflow")
DEFAULT_SORTS = ("type", "name", "size", "modified")

DEFAULTS: dict = {
    "config_version": CONFIG_VERSION,
    "theme": "dark",
    "dest_tint": True,
    "confirm_delete": True,
    "confirm_exit": True,
    "verify_copies": False,  # releer cada copia y compararla con el original
    "single_click": False,  # un clic abre, y se selecciona con la marca + del icono
    "use_trash": True,
    "expandable_folders": True,
    "friendly_dates": "off",
    "show_favorites_bar": False,
    "general_status_bar": True,
    "pane_free_space": "other_disk",  # con la barra general, en qué paneles se ve también el espacio libre: other_disk, always o never
    "list_column_chars": 50,  # el ancho máximo de las columnas de la vista de lista; 0 sin límite
    "thumbnail_size": 128,
    "video_thumbnails": "four",  # four: cuatro fotogramas y la duración; one: uno; off: el icono
    "folder_previews": True,  # las carpetas, en las miniaturas, con las primeras imágenes de dentro
    "show_generation_marks": False,  # las marcas de prompt y workflow de las imágenes y vídeos generados con IA; se muestran con su botón
    "generation_mark_kinds": "both",  # cuáles: both, prompt o workflow
    "git_marks": True,  # en la lista, el estado de git de cada archivo y carpeta
    "default_sort": "type",  # con qué orden empieza cada pestaña: type, name, size o modified (las carpetas, siempre primero)
    "default_sort_reverse": False,
    "remember_folder_views": True,  # la vista y el orden de cada carpeta
    "folder_views": {},  # ruta: [vista, columna, al revés]
    "detail_columns": {},  # los anchos elegidos a mano en la vista de detalles
    "lock_bars": False,
    "full_row_select": False,
    "hover_info": True,
    "language": AUTO,
    "open_images": "viewer",
    "open_archives": "system",
    "open_videos": "viewer",
    "open_json": "viewer",  # viewer: el visor de JSON del programa; system: la aplicación predeterminada
    "open_markdown": "editor",  # editor: el de doble vista del programa; system: la aplicación predeterminada
    "tree_level_colors": True,
    "shell_menu_items": True,
    "context_menus": {},
    "folder_tabs": {},
    "viewer_autoplay": True,
    "viewer_repeat": False,
    "video_window_repeat": True,
    "bar_buttons": {},
    "bar_order": {},
    "viewer_muted": False,
    "viewer_volume": 80,
    "viewer_above_metadata": True,
    "ffmpeg_dir": "",
    "exiftool_dir": "",
    "terminal_shell": "",
    "terminal_command": "",
    "general_terminal_full_width": False,
    "saved_sessions": [],
    "restore_session": True,
    "find_panel_place": "window",
    "find_window_columns": 1,
    "find_window_geometry": "",
    "compare_tool": "",
    "compare_command": "",
    "compare_external_images": False,
    "git_enabled": True,
    "git_show_branch": True,
    "git_show_user": True,
    "git_pane_branch": True,
    "git_commands": {},
    "git_custom_commands": [],
    "git_run_commands": True,
    "git_program": "",
    "file_manager_new_tab": True,
    "file_manager_exceptions": [],
    "file_manager_previous": "",
    "unfold_archives": True,
    "path_folder_menus": False,  # el › de la ruta despliega las subcarpetas
    "pane_color_scheme": DEFAULT_SCHEME,
    "pane_path_pale_light": False,  # la ruta de la barra de cada panel, más tenue que el resto de la barra
    "pane_path_pale_dark": False,
    "pane_color_customs": [],
    "pane_frames_on": True,  # el marco de color de cada panel, con su terminal y su árbol
    "pane_frames": "more_than",  # con más de `pane_frames_after` paneles, o always
    "pane_frames_after": 2,
    "pane_frame_width": 3,
    "pane_frame_active_width": 5,
    "pane_frame_dim_on": False,  # atenuar los de los paneles que no son el activo
    "pane_frame_dim": 50,  # cuánto, en %
    "pane_frame_colors": [],  # uno por panel; vacío, el de serie
    "recent": [],
    "recent_list": {},
    "history_size": 10,
    "smart_favorites": {},
    "smart_points": {},
    "layouts": {},
    "tabgroups": {},
    "show_hidden": False,
    "favorites": [],
    "favorites_autosort": False,
    "rename_presets": {},
    "rename_selects_stem": True,  # al renombrar uno, queda seleccionado el nombre sin la extensión
    "resort_on_change": False,  # al aparecer o cambiar un archivo, reordenar la lista ya; si no, lo nuevo va al final hasta actualizar
    "toolbar": DEFAULT_TOOLBAR,
    "styles": DEFAULT_STYLES,
    "style": N_("Vertical doble panel"),
    "session": {},
}


def _make_list_the_first_view(stored: dict) -> None:
    """Hasta la v2 la primera vista era Detalles. Un estilo de serie que la conserve pasa a Lista."""
    builtin = {style["name"] for style in DEFAULT_STYLES if style["view"] == "list"} | set(STYLE_RENAMES)
    styles = stored.get("styles")
    for style in styles if isinstance(styles, list) else []:
        if isinstance(style, dict) and style.get("name") in builtin and style.get("view") == "details":
            style["view"] = "list"
    toolbar = stored.get("toolbar")
    for button in toolbar if isinstance(toolbar, list) else []:
        children = button.get("children") if isinstance(button, dict) else None
        if not isinstance(children, list):
            continue
        order = {"Set VIEW=List": 0, "Set VIEW=Details": 1}

        def rank(item) -> int:
            commands = item.get("commands") if isinstance(item, dict) else None
            return order.get(commands[0], 2) if isinstance(commands, list) and commands else 2

        if any(rank(item) == 0 for item in children):
            children.sort(key=rank)  # el orden de los demás se mantiene


def _add_theme_button(stored: dict) -> None:
    """Hasta la v3 la barra no tenía botón de tema: se añade al final de la que ya tenga el usuario."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list):
        return
    if not any(isinstance(item, dict) and THEME_TOGGLE_COMMAND in (item.get("commands") or []) for item in toolbar):
        toolbar.extend(json.loads(json.dumps(DEFAULT_TOOLBAR[-2:])))


def _adopt_favorites_tree(stored: dict, version: int) -> None:
    """Hasta la v4 la barra de favoritos mostraba todos: quien la tenía visible los conserva en ella.
    En la v5 se marcaban con una casilla, y en la v6 es una rama. Las visitas contadas pasan a puntos,
    a razón de un doble clic cada una."""
    items = stored.get("favorites")
    if version < 5 and stored.get("show_favorites_bar") is True and isinstance(items, list):
        for favorite in items:
            if isinstance(favorite, dict):
                favorite["bar"] = True
    stored["favorites"] = favorites.clean(items)[0]
    visits = stored.pop("folder_visits", None)
    if isinstance(visits, dict):
        counts = {path: count for path, count in visits.items() if isinstance(path, str) and isinstance(count, int)}
    else:
        recent = stored.get("recent")
        counts = {item["path"]: item["count"] for item in (recent if isinstance(recent, list) else [])
                  if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("count"), int)}
    stored["smart_points"] = {path: count * VISIT_POINTS for path, count in counts.items()}


def _runs(item, command: str) -> bool:
    return isinstance(item, dict) and isinstance(item.get("commands"), list) and command in item["commands"]


def _add_metadata_button(stored: dict) -> None:
    """Hasta la v5 la barra no tenía botón de metadatos: se pone delante del visor, o al final si la barra no lo tiene."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list):
        return
    if any(_runs(item, METADATA_TOGGLE_COMMAND) for item in toolbar):
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, METADATA_TOGGLE_COMMAND))
    viewer = next((position for position, item in enumerate(toolbar) if _runs(item, VIEWER_TOGGLE_COMMAND)), None)
    toolbar.insert(len(toolbar) if viewer is None else viewer, button)


def _put_metadata_button_first(stored: dict) -> None:
    """En la v6 el botón de metadatos iba detrás del visor. Se cambian solo si siguen juntos: otro orden lo eligió el usuario."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list):
        return
    for position, (viewer, metadata) in enumerate(zip(toolbar, toolbar[1:])):
        if _runs(viewer, VIEWER_TOGGLE_COMMAND) and _runs(metadata, METADATA_TOGGLE_COMMAND):
            toolbar[position:position + 2] = [metadata, viewer]
            order = stored.get("bar_order")
            keys = order.get("toolbar") if isinstance(order, dict) else None
            for spot, pair in enumerate(zip(keys, keys[1:]) if isinstance(keys, list) else ()):
                if pair == (viewer.get("label"), metadata.get("label")):
                    keys[spot:spot + 2] = reversed(pair)
                    break
            return


def _is_old_metadata_style(style) -> bool:
    return (isinstance(style, dict) and style.get("name") == OLD_METADATA_STYLE and style.get("meta") is True and style.get("dual") == "Off"
            and style.get("tree") is True and style.get("viewer") is False)


def _update_styles(stored: dict) -> None:
    """Hasta la v6 los paneles dobles se llamaban de otra forma y había un estilo de metadatos, que ahora tienen su botón.
    Los de doble árbol se añaden junto a su pareja. Un estilo de metadatos que el usuario haya cambiado sigue siendo suyo."""
    def renamed(name):
        return STYLE_RENAMES.get(name, name) if isinstance(name, str) else name

    styles = stored.get("styles")
    if isinstance(styles, list):
        styles[:] = [style for style in styles if not _is_old_metadata_style(style)]
        for style in styles:
            if isinstance(style, dict) and "name" in style:
                style["name"] = renamed(style["name"])
        for partner, new in (("Vertical doble panel", "Vertical doble árbol"), ("Horizontal doble panel", "Horizontal doble árbol")):
            names = [style.get("name") if isinstance(style, dict) else None for style in styles]
            if new not in names:
                definition = next(json.loads(json.dumps(style)) for style in DEFAULT_STYLES if style["name"] == new)
                styles.insert(names.index(partner) + 1 if partner in names else len(styles), definition)
    kept = isinstance(styles, list) and any(isinstance(style, dict) and style.get("name") == OLD_METADATA_STYLE for style in styles)
    if stored.get("style") == OLD_METADATA_STYLE and not kept:
        stored["style"] = "Explorador"
    stored["style"] = renamed(stored.get("style", DEFAULTS["style"]))
    for section in ("bar_buttons", "bar_order"):
        bars = stored.get(section)
        keys = bars.get("styles") if isinstance(bars, dict) else None
        if isinstance(keys, list):
            bars["styles"] = [renamed(key) for key in keys if kept or key != OLD_METADATA_STYLE]
    layouts = stored.get("layouts")
    for layout in layouts.values() if isinstance(layouts, dict) else ():
        if isinstance(layout, dict) and "style" in layout:
            layout["style"] = renamed(layout["style"])


def _add_compare_button(stored: dict) -> None:
    """Junto a los de buscar duplicados y sincronizar, o al final si la barra no los tiene."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list) or any(_runs(item, COMPARE_COMMAND) for item in toolbar):
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, COMPARE_COMMAND))
    after = next((position for position, item in enumerate(toolbar) if _runs(item, "Set UTILITY=Dupe")), None)
    toolbar.insert(len(toolbar) if after is None else after + 1, button)


def _add_frames_button(stored: dict) -> None:
    """Junto al de la terminal general, o al final si la barra no lo tiene."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list) or any(_runs(item, FRAMES_TOGGLE_COMMAND) for item in toolbar):
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, FRAMES_TOGGLE_COMMAND))
    after = next((position for position, item in enumerate(toolbar) if _runs(item, GENERAL_TERMINAL_COMMAND)), None)
    toolbar.insert(len(toolbar) if after is None else after + 1, button)


def _add_marks_button(stored: dict) -> None:
    """Delante del de abrir el visor de imágenes, o al final si la barra no lo tiene."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list) or any(_runs(item, GENERATION_MARKS_COMMAND) for item in toolbar):
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, GENERATION_MARKS_COMMAND))
    viewer = next((position for position, item in enumerate(toolbar) if _runs(item, IMAGE_VIEWER_COMMAND)), None)
    toolbar.insert(len(toolbar) if viewer is None else viewer, button)


def _add_verified_copy(stored: dict) -> None:
    """En el menú del botón Copiar, detrás de «Duplicar aquí»: copiar comprobando la copia."""
    toolbar = stored.get("toolbar")
    for button in toolbar if isinstance(toolbar, list) else []:
        children = button.get("children") if isinstance(button, dict) else None
        if _runs(button, "Copy") and isinstance(children, list) and not any(_runs(child, "Copy VERIFY") for child in children):
            item = next(json.loads(json.dumps(child)) for default in DEFAULT_TOOLBAR if _runs(default, "Copy")
                        for child in default["children"] if _runs(child, "Copy VERIFY"))
            after = next((position for position, child in enumerate(children) if _runs(child, "Copy DUPLICATE")), len(children) - 1)
            children.insert(after + 1, item)


def _swap_sizes_for_disk_usage(stored: dict) -> None:
    """El botón de calcular los tamaños de las carpetas pasa a ser el del uso del disco, en su mismo sitio; calcular los tamaños queda en su menú.
    Un botón que el usuario haya cambiado (otra etiqueta u otras órdenes) se respeta."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list) or any(_runs(item, DISK_USAGE_COMMAND) for item in toolbar):
        return
    old = next((position for position, item in enumerate(toolbar) if isinstance(item, dict) and item.get("commands") == [OLD_SIZES_COMMAND]
                and item.get("label") == OLD_SIZES_LABEL), None)
    if old is None:
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, DISK_USAGE_COMMAND))
    toolbar[old] = button
    for section in ("bar_buttons", "bar_order"):
        bars = stored.get(section)
        keys = bars.get("toolbar") if isinstance(bars, dict) else None
        if isinstance(keys, list):
            bars["toolbar"] = [button["label"] if key == OLD_SIZES_LABEL else key for key in keys]


def _adopt_old_frame_modes(stored: dict) -> None:
    """La primera forma de elegirlos tenía «nunca» y un número fijo, dos."""
    mode = stored.get("pane_frames")
    if mode == "never":
        stored["pane_frames"], stored["pane_frames_on"] = "more_than", False
    elif mode == "more_than_two":
        stored["pane_frames"] = "more_than"


def _add_general_terminal_button(stored: dict) -> None:
    """En la v8 el botón junto a «Abrir terminal» actuaba sobre el panel activo, y ahora cada panel tiene el suyo: pasa a ser el de la terminal
    general y conserva su sitio. Sin él, va junto al de abrir una terminal o al final si la barra no lo tiene. Un botón creado por el usuario sigue siendo suyo."""
    toolbar = stored.get("toolbar")
    if not isinstance(toolbar, list) or any(_runs(item, GENERAL_TERMINAL_COMMAND) for item in toolbar):
        return
    button = next(json.loads(json.dumps(item)) for item in DEFAULT_TOOLBAR if _runs(item, GENERAL_TERMINAL_COMMAND))
    old = next((position for position, item in enumerate(toolbar) if _runs(item, TERMINAL_TOGGLE_COMMAND) and item.get("label") == OLD_TERMINAL_BUTTON), None)
    if old is None:
        opener = next((position for position, item in enumerate(toolbar) if _runs(item, TERMINAL_COMMAND)), None)
        toolbar.insert(len(toolbar) if opener is None else opener + 1, button)
        return
    toolbar[old] = button
    for section in ("bar_buttons", "bar_order"):
        bars = stored.get(section)
        keys = bars.get("toolbar") if isinstance(bars, dict) else None
        if isinstance(keys, list):
            bars["toolbar"] = [button["label"] if key == OLD_TERMINAL_BUTTON else key for key in keys]


def _add_panel_terminal_styles(stored: dict) -> None:
    """Hasta la v10 no había estilos con una terminal en lugar del segundo panel: cada uno va junto a su pareja, o al final."""
    styles = stored.get("styles")
    if not isinstance(styles, list):
        return
    for partner, new in (("Vertical doble árbol", "Vertical panel-terminal"), ("Horizontal doble árbol", "Horizontal panel-terminal")):
        names = [style.get("name") if isinstance(style, dict) else None for style in styles]
        if new not in names:
            definition = next(json.loads(json.dumps(style)) for style in DEFAULT_STYLES if style["name"] == new)
            styles.insert(names.index(partner) + 1 if partner in names else len(styles), definition)


class Config:
    """Diccionario persistente. Un archivo dañado se aparta y no impide arrancar."""

    def __init__(self, directory: str | None = None) -> None:
        self.directory = directory or config_dir()
        self.path = os.path.join(self.directory, "config.json")
        if directory is None and not os.environ.get("DIRECTORY_COMMANDER_TUX_TUX_CONFIG") and not is_portable():
            self._adopt_legacy_config()  # una carpeta elegida a propósito empieza como se indicó, y la portable no se lleva nada del usuario
        self.data = json.loads(json.dumps(DEFAULTS))
        self.warning = ""
        self._load()

    def _adopt_legacy_config(self) -> None:
        """La primera vez con el nombre nuevo, copia la configuración anterior. El original no se toca."""
        legacy = next((folder for folder in _legacy_config_folders() if os.path.isfile(os.path.join(folder, "config.json"))), None)
        if os.path.isfile(self.path) or legacy is None:
            return
        try:
            os.makedirs(self.directory, exist_ok=True)
            shutil.copy2(os.path.join(legacy, "config.json"), self.path)
            languages = os.path.join(legacy, "languages")
            if os.path.isdir(languages):
                shutil.copytree(languages, os.path.join(self.directory, "languages"), dirs_exist_ok=True)
        except OSError:
            pass  # sin copia se arranca con la configuración predeterminada

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fin:
                stored = json.load(fin)
            if not isinstance(stored, dict):
                raise ValueError(tr("el contenido no es un objeto JSON"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            backup = self.path + ".corrupto"
            try:
                os.replace(self.path, backup)
            except OSError:
                backup = ""
            self.warning = tr("No se pudo leer la configuración ({exc}). Copia guardada en: {backup}", exc=exc, backup=backup)
            return
        version = stored.get("config_version", 1)
        if version < 2:
            # La barra de la v1 tenía otro formato. Favoritos, presets y sesión se conservan.
            for key in ("toolbar", "dual", "view_mode", "show_tree", "show_viewer"):
                stored.pop(key, None)
        if version < 3:
            _make_list_the_first_view(stored)
        if version < 4:
            _add_theme_button(stored)
        if version < 6:
            _adopt_favorites_tree(stored, version)
            _add_metadata_button(stored)
        if version < 7:
            _put_metadata_button_first(stored)
            _update_styles(stored)
        if version < 9:
            _add_general_terminal_button(stored)
        if version < 11:
            _add_panel_terminal_styles(stored)
        if version < 12:
            adopt_old_colors(stored)
        if version < 14:
            _add_compare_button(stored)
        if version < 18:
            _add_frames_button(stored)
            _adopt_old_frame_modes(stored)
        if version < 19:
            _add_marks_button(stored)
        if version < 20:
            _add_verified_copy(stored)
        if version < 21:
            _swap_sizes_for_disk_usage(stored)
        if version < 17:
            stored.pop("find_window_geometry", None)  # era de otra forma de colocar el panel, con otras medidas
        if version < 15:
            find_state = stored.get("find_state")
            if isinstance(find_state, dict) and find_state.get("target") == "source":
                find_state["target"] = "source_new"  # el predeterminado de antes, guardado sin que nadie lo eligiese
        if version < 13:
            stored.pop("pane_path_pale_light", None)  # Preferencias la guardaba activada aunque nadie la hubiese elegido
        if version < 10 and stored.get("terminal_shell") == "cmd":
            del stored["terminal_shell"]  # era la predeterminada, y Preferencias la guardaba aunque nadie la hubiese elegido
        stored["config_version"] = CONFIG_VERSION
        self.data.update(stored)

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    @property
    def language_folder(self) -> str:
        """Donde el usuario puede dejar idiomas nuevos o sus correcciones."""
        return os.path.join(self.directory, LANGUAGE_FOLDER)

    def setdefault_dict(self, key: str) -> dict:
        """Sección de tipo diccionario, que se crea si falta o si el archivo la tenía dañada."""
        value = self.data.get(key)
        if not isinstance(value, dict):
            value = self.data[key] = {}
        return value

    def save(self) -> None:
        os.makedirs(self.directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fout:
            json.dump(self.data, fout, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
