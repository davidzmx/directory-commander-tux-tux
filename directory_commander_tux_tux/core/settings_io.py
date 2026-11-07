"""Exportar e importar favoritos y configuración en archivos JSON.

Un archivo importado puede venir de cualquier sitio: lo que no se entiende o tiene un tipo
inesperado se descarta y se informa, en lugar de llegar a la configuración.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime

from . import bar_buttons, favorites as favorites_tree, frequent, pane_colors, remote
from .config import GENERATION_MARK_KINDS, THUMB_SIZES, VIDEO_THUMBNAILS
from .i18n import N_, tr

FAVORITES_FORMAT = "directory-commander-tux-tux-favorites"
SETTINGS_FORMAT = "directory-commander-tux-tux-settings"
FORMAT_VERSION = 2  # la 2 añade los grupos, el sistema y la carpeta personal de origen, la fecha, las contraseñas y las claves de los servidores
MAX_FILE_SIZE = 16 * 1024 * 1024

# Lo que viaja en un archivo de configuración. No incluye la sesión ni las carpetas recientes,
# que son propias de cada equipo. Tampoco `ffmpeg_dir` ni el programa de la terminal: importarlos
# haría que se ejecutase lo que hubiera elegido quien creó el archivo. Por lo mismo, tampoco las órdenes de git ni el comparador externo.
SETTINGS_TYPES: dict[str, type] = {
    "theme": str, "language": str, "dest_tint": bool, "confirm_delete": bool, "confirm_exit": bool, "single_click": bool, "use_trash": bool, "expandable_folders": bool,
    "friendly_dates": str, "show_favorites_bar": bool, "general_status_bar": bool, "pane_free_space": str, "verify_copies": bool, "list_column_chars": int, "thumbnail_size": int, "video_thumbnails": str, "folder_previews": bool, "show_generation_marks": bool, "generation_mark_kinds": str, "remember_folder_views": bool, "default_sort": str, "default_sort_reverse": bool, "git_marks": bool, "detail_columns": dict, "lock_bars": bool, "full_row_select": bool, "hover_info": bool, "hover_preview_size": str,
    "viewer_autoplay": bool, "viewer_repeat": bool, "video_window_repeat": bool, "bar_buttons": dict, "bar_order": dict, "viewer_muted": bool, "viewer_volume": int, "viewer_above_metadata": bool, "folder_tabs": dict, "open_images": str, "open_archives": str, "open_videos": str, "open_markdown": str, "open_json": str, "tree_level_colors": bool, "shell_menu_items": bool, "context_menus": dict,
    "show_hidden": bool, "favorites": list, "favorites_autosort": bool, "recent_list": dict, "history_size": int, "smart_favorites": dict, "rename_presets": dict, "rename_selects_stem": bool, "resort_on_change": bool, "toolbar": list, "styles": list, "style": str,
    "layouts": dict, "tabgroups": dict, "general_terminal_full_width": bool, "pane_color_scheme": str, "pane_color_customs": list, "pane_path_pale_light": bool, "pane_path_pale_dark": bool, "pane_frames_on": bool, "pane_frames": str, "pane_frames_after": int, "pane_frame_width": int, "pane_frame_active_width": int, "pane_frame_dim_on": bool, "pane_frame_dim": int, "pane_frame_colors": list, "unfold_archives": bool, "path_folder_menus": bool, "find_templates": dict, "saved_sessions": list, "restore_session": bool, "find_panel_place": str, "find_window_columns": int, "git_enabled": bool, "git_show_branch": bool, "git_show_user": bool, "git_pane_branch": bool, "git_run_commands": bool,
    # Los perfiles SSH/SFTP sí viajan: sin sus contraseñas, que están en el llavero del sistema.
    "ssh_profiles": list, "sftp_open_files": bool, "sftp_upload": str, "sftp_preview": bool, "sftp_preview_mb": int,
}

SSH = "ssh"


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    contents: str
    keys: tuple[str, ...]


GROUPS: tuple[Group, ...] = (
    Group("appearance", N_("Aspecto"), N_("Tema, idioma, estilos, colores de los paneles, de sus marcos y de los niveles del árbol, "
                                          "si la terminal general ocupa todo el ancho de la ventana, el ancho de las columnas y el tamaño de las miniaturas"),
          ("theme", "language", "style", "styles", "dest_tint", "tree_level_colors", "pane_color_scheme", "pane_color_customs", "pane_path_pale_light",
           "pane_path_pale_dark", "pane_frames_on", "pane_frames", "pane_frames_after", "pane_frame_width", "pane_frame_active_width", "pane_frame_dim_on",
           "pane_frame_dim", "pane_frame_colors", "general_terminal_full_width", "list_column_chars", "thumbnail_size", "video_thumbnails", "folder_previews",
           "show_generation_marks", "generation_mark_kinds", "detail_columns")),
    Group("bars", N_("Barras"), N_("La barra de herramientas, los botones y el orden de cada barra, la barra de favoritos, la de estado y bloquear las barras"),
          ("toolbar", "bar_buttons", "bar_order", "show_favorites_bar", "general_status_bar", "pane_free_space", "lock_bars")),
    Group("menus", N_("Menús contextuales"), N_("Cómo los has personalizado y si incluyen las entradas del sistema"), ("context_menus", "shell_menu_items")),
    Group("favorites", N_("Favoritos"), N_("Los favoritos, cómo se ordenan y los ajustes de los favoritos inteligentes"),
          ("favorites", "favorites_autosort", "smart_favorites")),
    Group("recent", N_("Recientes"), N_("Los ajustes de la lista de recientes y el tamaño del historial"), ("recent_list", "history_size")),
    Group("behavior", N_("Comportamiento"), N_("Confirmar al eliminar y al salir, abrir con un clic, papelera, ocultos, selección, fechas, qué abre cada tipo de archivo y más"),
          ("confirm_delete", "confirm_exit", "verify_copies", "single_click", "rename_selects_stem", "resort_on_change", "remember_folder_views", "default_sort", "default_sort_reverse", "use_trash", "show_hidden", "expandable_folders", "full_row_select", "hover_info", "hover_preview_size", "friendly_dates", "path_folder_menus",
           "unfold_archives", "open_images", "open_archives", "open_videos", "open_markdown", "open_json")),
    Group("viewer", N_("Visor y reproductor"), N_("Reproducción automática, repetir, volumen, silencio y dónde van los metadatos"),
          ("viewer_autoplay", "viewer_repeat", "video_window_repeat", "viewer_muted", "viewer_volume", "viewer_above_metadata")),
    Group("layouts", N_("Diseños, grupos de pestañas y sesiones"), N_("Los guardados, los ajustes de las pestañas y si se restaura la sesión al iniciar"),
          ("layouts", "tabgroups", "saved_sessions", "restore_session", "folder_tabs")),
    Group("find", N_("Búsqueda"), N_("Las plantillas, dónde se muestra la búsqueda y en cuántas columnas"),
          ("find_templates", "find_panel_place", "find_window_columns")),
    Group("rename", N_("Renombrar"), N_("Los preajustes del renombrado múltiple"), ("rename_presets",)),
    Group("git", N_("Integración con Git"), N_("Si se activa y qué se muestra: rama, usuario, rama en cada panel y marcas en la lista"),
          ("git_enabled", "git_show_branch", "git_show_user", "git_pane_branch", "git_marks", "git_run_commands")),
    Group(SSH, N_("SSH/SFTP"), N_("Los perfiles, abrir y editar archivos del servidor y la vista previa"),
          ("ssh_profiles", "sftp_open_files", "sftp_upload", "sftp_preview", "sftp_preview_mb")),
)
_GROUP_OF = {key: group.key for group in GROUPS for key in group.keys}
assert set(_GROUP_OF) == set(SETTINGS_TYPES), "cada ajuste que viaja tiene que estar en un grupo"
DEFAULT_GROUPS = tuple(group.key for group in GROUPS)

# Lo que no viaja nunca, y por qué. Se explica al exportar.
NOT_EXPORTED: tuple[tuple[str, str], ...] = (
    (N_("La sesión abierta y la lista de carpetas recientes"), N_("Son de este equipo. Las sesiones guardadas sí viajan, en su grupo.")),
    (N_("Las visitas que cuentan los favoritos inteligentes"), N_("Cada equipo las cuenta según cómo lo uses en él.")),
    (N_("El tamaño y la posición de las ventanas"), N_("Dependen de la pantalla de cada equipo.")),
    (N_("La vista y el orden recordados de cada carpeta"), N_("Son rutas de este equipo.")),
    (N_("El administrador de archivos predeterminado"), N_("Cambia el comportamiento de todo el sistema: se decide en cada equipo.")),
    (N_("Los programas: el intérprete de la terminal, ffmpeg, exiftool, el programa git y sus órdenes, y el comparador de archivos"),
     N_("Importarlos haría que este equipo ejecutase lo que eligió quien creó el archivo, y sus rutas dependen de cada sistema.")),
)
_ALLOWED_VALUES = {"default_sort": {"type", "name", "size", "modified"}, "pane_free_space": {"other_disk", "always", "never"}, "sftp_upload": {"ask", "auto"}, "find_panel_place": {"bottom", "window"}, "pane_frames": set(pane_colors.FRAME_MODES), "find_window_columns": {1, 2}, "theme": {"dark", "light", "auto"}, "friendly_dates": {"off", "today", "normal"},
                   "open_images": {"viewer", "system"}, "open_archives": {"system", "browse"},
                   "open_videos": {"viewer", "system"}, "open_markdown": {"editor", "system"}, "open_json": {"viewer", "system"}}


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


def platform_name() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def export_name(now: datetime | None = None) -> str:
    """El nombre que se propone al exportar, con la fecha y la hora para no pisar una exportación anterior. Sin «:», que Windows no admite."""
    return f"configuracion_{(now or datetime.now()):%Y-%m-%d_%H-%M}.json"


def group_of(key: str) -> str:
    return _GROUP_OF.get(key, "")


def portable_settings(config_data: dict, groups=DEFAULT_GROUPS) -> dict:
    chosen = set(groups)
    return {key: config_data[key] for key in SETTINGS_TYPES if key in config_data and _GROUP_OF[key] in chosen}


# ------------------------------------------------------------------ contraseñas
_CIPHER = "scrypt-aes-256-gcm"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1
_AAD = SETTINGS_FORMAT.encode()


class WrongKey(ValueError):
    """La clave no abre las contraseñas, o el archivo se ha modificado."""


def _derive(key: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(key.encode("utf-8"))


def seal_passwords(passwords: dict[str, str], key: str) -> dict:
    """Cifradas con una clave: scrypt para sacar la llave de la clave, y AES-256-GCM, que además detecta cualquier cambio."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    data = AESGCM(_derive(key, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)).encrypt(nonce, json.dumps(passwords).encode("utf-8"), _AAD)
    encode = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return {"cipher": _CIPHER, "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P, "salt": encode(salt), "nonce": encode(nonce), "data": encode(data)}


def _clean_passwords(raw) -> dict[str, str]:
    result = {}
    for server, password in (raw.items() if isinstance(raw, dict) else ()):
        try:
            remote.server_parts(server)
        except (AttributeError, ValueError):
            continue
        if isinstance(password, str) and password:
            result[server] = password
    return result


def open_passwords(block, key: str = "") -> dict[str, str]:
    """Las contraseñas de un archivo, por conexión. Las cifradas necesitan la clave: si no es la buena, `WrongKey`."""
    if not isinstance(block, dict):
        return {}
    if "plain" in block:
        return _clean_passwords(block["plain"])
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n, r, p = block.get("n"), block.get("r"), block.get("p")
    # Unos valores enormes harían que abrirlas tardase o gastase memoria sin fin.
    if block.get("cipher") != _CIPHER or n not in {2 ** k for k in range(14, 21)} or r != 8 or p not in (1, 2, 3, 4):
        raise ValueError(tr("Las contraseñas del archivo están cifradas de una forma que no se conoce"))
    try:
        salt, nonce, data = (base64.b64decode(block.get(name) or "", validate=True) for name in ("salt", "nonce", "data"))
        plain = AESGCM(_derive(key, salt, n, r, p)).decrypt(nonce, data, _AAD)
    except InvalidTag as exc:
        raise WrongKey(tr("La clave no es correcta")) from exc
    except ValueError as exc:
        raise ValueError(tr("Las contraseñas del archivo están dañadas")) from exc
    try:
        return _clean_passwords(json.loads(plain.decode("utf-8")))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(tr("Las contraseñas del archivo están dañadas")) from exc


# ------------------------------------------------------------------ exportar
def export_settings(path: str, config_data: dict, groups=DEFAULT_GROUPS, passwords: dict[str, str] | None = None, key: str = "",
                    known_hosts=()) -> None:
    """Con `passwords`, las contraseñas SSH/SFTP: cifradas con `key`, o en texto plano si no hay clave.
    Las contraseñas y las claves de los servidores solo viajan con el grupo SSH/SFTP."""
    chosen = [group.key for group in GROUPS if group.key in set(groups)]
    data = {"format": SETTINGS_FORMAT, "version": FORMAT_VERSION, "exported": datetime.now().isoformat(timespec="seconds"), "platform": platform_name(),
            "home": os.path.expanduser("~"), "groups": chosen, "settings": portable_settings(config_data, chosen)}
    if SSH in chosen and passwords:
        data["ssh_passwords"] = seal_passwords(passwords, key) if key else {"plain": dict(passwords)}
    if SSH in chosen and known_hosts:
        data["ssh_known_hosts"] = list(known_hosts)
    _write_json(path, data)


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
    if key == "ssh_profiles":
        return remote.clean_profiles(value)
    if key == "list_column_chars":
        return max(0, min(500, value))
    if key == "thumbnail_size":
        return value if value in THUMB_SIZES else None
    if key == "video_thumbnails":
        return value if value in VIDEO_THUMBNAILS else None
    if key == "generation_mark_kinds":
        return value if value in GENERATION_MARK_KINDS else None
    if key == "detail_columns":
        return {name: width for name, width in value.items() if isinstance(name, str) and isinstance(width, int) and not isinstance(width, bool)
                and 20 <= width <= 4000}  # vacío vale: las columnas, a su ancho de serie
    if key == "sftp_preview_mb":
        return max(1, min(1024, value))
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


@dataclass
class Bundle:
    """Lo que trae un archivo de configuración, ya revisado. Nada se aplica hasta elegir los grupos con `chosen_settings`."""
    settings: dict
    ignored: list[str]
    platform: str = ""  # vacío en los archivos de antes de la versión 2
    home: str = ""
    exported: str = ""  # fecha y hora en ISO 8601, tal como venga
    passwords: dict | None = None  # el bloque tal cual: si está cifrado, hace falta la clave
    known_hosts: list[str] = field(default_factory=list)

    def groups(self) -> list[str]:
        present = {_GROUP_OF[key] for key in self.settings}
        if self.passwords or self.known_hosts:
            present.add(SSH)
        return [group.key for group in GROUPS if group.key in present]

    @property
    def encrypted(self) -> bool:
        return bool(self.passwords) and "plain" not in self.passwords

    @property
    def other_system(self) -> bool:
        return bool(self.platform) and self.platform != platform_name()


def read_bundle(path: str) -> Bundle:
    """Todo lo válido de un archivo, y los nombres de lo descartado. También admite una copia de `config.json`."""
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError(tr("El archivo no es una configuración de Directory Commander Tux Tux"))
    exported = data.get("format") == SETTINGS_FORMAT
    if exported:
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
    text = lambda name: data.get(name) if exported and isinstance(data.get(name), str) else ""  # noqa: E731
    passwords = data.get("ssh_passwords") if exported and isinstance(data.get("ssh_passwords"), dict) else None
    hosts = data.get("ssh_known_hosts") if exported and isinstance(data.get("ssh_known_hosts"), list) else []
    return Bundle(settings, ignored, text("platform") if text("platform") in ("linux", "windows", "macos") else "", text("home"), text("exported"),
                  passwords or None, [line for line in hosts if isinstance(line, str) and line.strip()])


def read_settings(path: str) -> tuple[dict, list[str]]:
    """Los ajustes válidos de un archivo, de todos sus grupos, y los nombres de lo descartado."""
    bundle = read_bundle(path)
    return bundle.settings, bundle.ignored


def _home_mover(old: str, windows: bool, new: str):
    """Cambia la carpeta personal de quien exportó por la de este equipo al principio de cada ruta. El resto de la ruta se conserva."""
    separators = "\\/" if windows else "/"
    old = old.rstrip(separators) or old
    same = (lambda a, b: a.casefold() == b.casefold()) if windows else (lambda a, b: a == b)

    def move(value):
        if isinstance(value, dict):
            return {key: move(item) for key, item in value.items()}
        if isinstance(value, list):
            return [move(item) for item in value]
        if not isinstance(value, str) or len(value) < len(old) or not same(value[:len(old)], old):
            return value
        rest = value[len(old):]
        if rest and rest[0] not in separators:
            return value  # /home/ana no es /home/anabel
        parts = [part for part in re.split("[\\\\/]" if windows else "/", rest) if part]
        return os.path.join(new, *parts)
    return move


def chosen_settings(bundle: Bundle, groups) -> dict:
    """Los ajustes de los grupos elegidos, con las rutas de la carpeta personal de origen llevadas a la de este equipo."""
    chosen = set(groups)
    settings = {key: value for key, value in bundle.settings.items() if _GROUP_OF[key] in chosen}
    home = os.path.expanduser("~")
    windows = bundle.platform == "windows" if bundle.platform else sys.platform == "win32"
    if bundle.home and (bundle.home != home or bundle.other_system):
        settings = _home_mover(bundle.home, windows, home)(settings)
    return settings
