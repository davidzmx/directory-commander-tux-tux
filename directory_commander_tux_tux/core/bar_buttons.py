"""Qué botones muestra cada barra y en qué orden. Sin Qt, para poder probarlo sin interfaz."""
from __future__ import annotations

from . import git_tools
from .config import DEFAULT_STYLES, DEFAULT_TOOLBAR
from .i18n import tr

TOOLBAR = "toolbar"
STYLES = "styles"
LOCATION = "location"
PANE = "pane"
IMAGE_VIEWER = "image_viewer"
VIDEO_VIEWER = "video_viewer"
VIDEO_CONTROLS = "video_controls"
PLAYER_CONTROLS = "player_controls"
GIT = "git"

SEPARATOR = "|"
SPACER = "<->"
PATH = "path"
SEEK = "seek"
REPEATABLE = {SEPARATOR, SPACER}  # pueden estar varias veces; lo demás, una sola
ALWAYS_SHOWN = {PATH, SEEK, SEPARATOR, SPACER}  # se mueven pero no tienen casilla para ocultarlos
_WITH_LINES = {TOOLBAR, IMAGE_VIEWER, VIDEO_VIEWER}  # las barras en las que una línea separadora tiene sentido

_LINE = (SEPARATOR, "")
_FIXED_BARS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    LOCATION: (tr("Barra de localización"), [(PATH, tr("Ruta")), ("recent", tr("Carpetas recientes")), ("content_type", tr("Filtro de contenido"))]),
    PANE: (tr("Barra de cada panel"), [("goback", tr("Atrás")), ("goforward", tr("Adelante")), ("goup", tr("Subir")), ("refresh", tr("Actualizar")),
                                   ("home", tr("Inicio")), ("favorites", tr("Favoritos")), (PATH, tr("Ruta")), ("menu-arrow", tr("Carpetas recientes")), ("copypath", tr("Copiar la ruta")), ("gitbranch", tr("Rama de git")),
                                   ("thumbnails", tr("Tamaño de las miniaturas")), ("treepane", tr("Árbol de este panel")), ("terminalpane", tr("Terminal de este panel")), ("terminalonly", tr("Solo la terminal en este panel")), ("remote", tr("Panel remoto (SFTP)")), ("disconnect", tr("Desconectar del servidor")), ("send", tr("Mostrar esta carpeta en el otro panel")), ("swap", tr("Intercambiar paneles")),
                                   ("close", tr("Cerrar este panel"))]),
    GIT: (tr("Barra de git de las terminales"), [("status", tr("Status")), ("add", tr("Add…")), ("unstage", tr("Unstage…")), ("commit", tr("Commit…")), ("push", tr("Push")), ("pull", tr("Pull")),
                                                 ("fetch", tr("Fetch")), ("diff", tr("Diff")), ("log", tr("Log"))]),
    IMAGE_VIEWER: (tr("Visor de imágenes"), [("previous", tr("Imagen anterior")), ("next", tr("Imagen siguiente")), ("rotate_left", tr("Girar a la izquierda")),
                                         ("rotate_right", tr("Girar a la derecha")), _LINE, ("zoom_in", tr("Acercar")), ("zoom_out", tr("Alejar")),
                                         ("actual", tr("Tamaño real")), ("fit_shrink", tr("Reducir para caber en la ventana")),
                                         ("fit_enlarge", tr("Ampliar a la ventana")), ("zoom_slider", tr("Control de zoom")), _LINE,
                                         ("fullscreen", tr("Pantalla completa")), ("slideshow", tr("Presentación")), _LINE, ("crop", tr("Recortar")),
                                         ("save_crop", tr("Guardar selección")), _LINE, ("rename", tr("Renombrar")), ("delete", tr("Eliminar")), _LINE,
                                         ("ai_prompt", tr("Info de IA")), ("reveal", tr("Mostrar en el panel"))]),
    VIDEO_VIEWER: (tr("Ventana de vídeo: barra superior"), [("previous", tr("Vídeo anterior")), ("next", tr("Vídeo siguiente")), _LINE,
                                                            ("rotate_left", tr("Girar a la izquierda")), ("rotate_right", tr("Girar a la derecha")),
                                                            ("flip_h", tr("Voltear horizontalmente")), ("flip_v", tr("Voltear verticalmente")), _LINE,
                                                            ("save_frame", tr("Guardar fotograma")),
                                                            _LINE, ("fullscreen", tr("Pantalla completa")), _LINE, ("ai_prompt", tr("Info de IA")), ("reveal", tr("Mostrar en el panel"))]),
    VIDEO_CONTROLS: (tr("Ventana de vídeo: controles"), [("play", tr("Reproducir o pausar")), ("stop", tr("Detener")), ("repeat", tr("Repetir")),
                                                         ("speed", tr("Velocidad de reproducción")),
                                                         ("frame_back", tr("Fotograma anterior")), ("frame_forward", tr("Fotograma siguiente")), (SEEK, tr("Barra de progreso")),
                                                         ("mute", tr("Silenciar")), ("volume", tr("Volumen"))]),
    PLAYER_CONTROLS: (tr("Visor lateral: controles de vídeo y audio"), [("play", tr("Reproducir o pausar")), ("stop", tr("Detener")), ("repeat", tr("Repetir")),
                                                                        ("speed", tr("Velocidad de reproducción")), (SEEK, tr("Barra de progreso")),
                                                                        ("auto", "Auto"), ("mute", tr("Silenciar")), ("volume", tr("Volumen"))]),
}


def normalize(raw) -> dict[str, list[str]]:
    source = raw if isinstance(raw, dict) else {}
    return {bar: [key for key in dict.fromkeys(keys) if isinstance(key, str)] for bar, keys in source.items()
            if isinstance(bar, str) and isinstance(keys, list)}


def normalize_order(raw) -> dict[str, list[str]]:
    """Como `normalize`, pero las líneas separadoras pueden repetirse."""
    result: dict[str, list[str]] = {}
    for bar, keys in (raw if isinstance(raw, dict) else {}).items():
        if isinstance(bar, str) and isinstance(keys, list):
            seen: set[str] = set()
            result[bar] = [key for key in keys if isinstance(key, str) and (key in REPEATABLE or not (key in seen or seen.add(key)))]
    return result


def hidden(config, bar: str) -> set[str]:
    return set(normalize(config.get("bar_buttons")).get(bar, ()))


def ordered(config, bar: str, default: list[str]) -> list[str]:
    """Las claves por el orden elegido. Lo que ya no existe se descarta, y lo nuevo queda detrás de quien lo precede de serie."""
    stored = normalize_order(config.get("bar_order")).get(bar)
    if not stored:
        return list(default)
    known = set(default)
    result = [key for key in stored if key in known or (key in REPEATABLE and bar in _WITH_LINES)]
    for position, key in enumerate(default):
        if key in result or key in REPEATABLE:
            continue
        before = next((other for other in reversed(default[:position]) if other not in REPEATABLE and other in result), None)
        result.insert(result.index(before) + 1 if before else 0, key)
    if bar == VIDEO_VIEWER and "ai_prompt" in result and "reveal" in result:
        ai_idx = result.index("ai_prompt")
        rev_idx = result.index("reveal")
        if ai_idx != rev_idx - 1:
            result.pop(ai_idx)
            rev_idx = result.index("reveal")
            result.insert(rev_idx, "ai_prompt")
    return result


def _fixed_buttons(config, bar: str) -> list[tuple[str, str]]:
    """Los botones de una barra. La de git tiene además las órdenes que se hayan añadido."""
    custom = [(item["key"], item["label"]) for item in git_tools.clean_custom(config.get("git_custom_commands"))] if bar == GIT else []
    return [*_FIXED_BARS[bar][1], *custom]


def arranged(config, bar: str) -> list[str]:
    """El orden de una de las barras con botones fijos."""
    return ordered(config, bar, [key for key, _label in _fixed_buttons(config, bar)])


def toolbar_key(item) -> str:
    """Identifica un botón de la barra de herramientas. Vacío en lo que no es un botón: líneas y espacios."""
    if not isinstance(item, dict) or item.get("separator"):
        return ""
    commands = item.get("commands")
    first = commands[0] if isinstance(commands, list) and commands and isinstance(commands[0], str) else item.get("command")
    first = first if isinstance(first, str) else ""
    if first.lower() == "set spacer" or first.lower().startswith("marker"):
        return ""
    label = item.get("label")
    return label if isinstance(label, str) and label else first


def _toolbar_slot(item) -> str:
    """Como `toolbar_key`, pero también nombra las líneas y los espacios, que se pueden mover."""
    key = toolbar_key(item)
    if key or not isinstance(item, dict):
        return key
    if item.get("separator"):
        return SEPARATOR
    commands = item.get("commands")
    is_spacer = isinstance(commands, list) and any(isinstance(line, str) and line.lower() == "set spacer" for line in commands)
    return SPACER if is_spacer or str(item.get("command")).lower() == "set spacer" else ""


def arrange_toolbar(config, items: list) -> list:
    """Los elementos de la barra de herramientas por el orden elegido, sin cambiar lo que guarda cada uno."""
    if not normalize_order(config.get("bar_order")).get(TOOLBAR):
        return items
    slots = [_toolbar_slot(item) for item in items]
    by_slot: dict[str, list] = {}
    for item, slot in zip(items, slots):
        by_slot.setdefault(slot, []).append(item)
    result: list = []
    for key in ordered(config, TOOLBAR, [slot for slot in slots if slot]):
        if key == SEPARATOR:
            result.append({"separator": True})
        elif key == SPACER:
            result.append({"commands": ["Set Spacer"]})
        else:
            result.extend(by_slot.get(key, ()))
    return result


def bars(config) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Cada barra personalizable en su orden de serie: identificador, título y sus elementos como (clave, texto)."""
    toolbar = config.get("toolbar")
    styles = config.get("styles")
    slots = [_toolbar_slot(item) for item in (toolbar if isinstance(toolbar, list) else DEFAULT_TOOLBAR)]
    names = [style.get("name") if isinstance(style, dict) else None for style in (styles if isinstance(styles, list) else DEFAULT_STYLES)]
    tools = [slot for position, slot in enumerate(slots) if slot and (slot in REPEATABLE or slot not in slots[:position])]
    result = [(TOOLBAR, tr("Barra de herramientas"), [(key, tr(key) if key not in REPEATABLE else "") for key in tools]),
              (STYLES, tr("Barra de estilos"), [(name, tr(name)) for name in dict.fromkeys(names) if isinstance(name, str) and name])]
    return result + [(bar, title, _fixed_buttons(config, bar)) for bar, (title, _buttons) in _FIXED_BARS.items()]


def can_have_lines(bar: str) -> bool:
    return bar in _WITH_LINES
