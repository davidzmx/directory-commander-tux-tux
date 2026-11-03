"""Las combinaciones de colores de los paneles: las que trae el programa y las que crea el usuario. Sin Qt."""
from __future__ import annotations

import re

from .i18n import N_

ROLES = ("source", "dest", "other")
DEFAULT_SCHEME = "blue-red"
CUSTOM_PREFIX = "custom:"
# Cada una tiene sus colores para el tema oscuro y para el claro. En la de serie, los del propio tema. Sin «otros», esos paneles van sin color.
BUILTIN_SCHEMES: dict[str, tuple[str, dict[str, dict[str, str]]]] = {
    DEFAULT_SCHEME: (N_("Azul y rojo"), {"dark": {}, "light": {}}),
    "green-amber": (N_("Verde y naranja"), {"dark": {"source": "#1f4a38", "dest": "#573513"},
                                            "light": {"source": "#009800", "dest": "#e86800"}}),
    "violet-teal": (N_("Violeta y turquesa"), {"dark": {"source": "#3a2f5c", "dest": "#174a4f"},
                                               "light": {"source": "#6a3fc0", "dest": "#00858a"}}),
}
OLD_CUSTOM_NAME = "Personalizada"
# El marco de color que agrupa cada panel con su terminal y su árbol. Colores vivos, que se distinguen entre sí con los dos temas.
FRAME_MODES = ("more_than", "always")  # con más de cierto número de paneles, o siempre. Se encienden y apagan aparte
DEFAULT_FRAME_MODE = "more_than"
DEFAULT_FRAMES_AFTER = 2
MIN_FRAMES_AFTER, MAX_FRAMES_AFTER = 2, 7  # con uno solo no haría falta, y con ocho ya no hay más
DEFAULT_FRAME_WIDTH = 3
DEFAULT_ACTIVE_FRAME_WIDTH = 5
MAX_FRAME_WIDTH = 8
DEFAULT_FRAME_DIM = 50  # cuánto se atenúan, en %, los marcos de los paneles que no son el activo
MAX_FRAME_DIM = 90
FRAME_COLORS = ("#3b82f6", "#f59e0b", "#22c55e", "#ef4444", "#a855f7", "#06b6d4", "#ec4899", "#84cc16")


def clean_colors(raw) -> dict[str, str]:
    """Colores válidos para cada papel. Lo que no sea un color queda como de serie."""
    source = raw if isinstance(raw, dict) else {}
    return {role: source[role].lower() for role in ROLES if isinstance(source.get(role), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", source[role])}


def clean_customs(raw) -> list[dict[str, str]]:
    """Las combinaciones del usuario. Sin nombre no se podrían elegir, y dos iguales no se distinguirían."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip() and name.strip().casefold() not in seen:
            seen.add(name.strip().casefold())
            result.append({"name": name.strip(), **clean_colors(item)})
    return result


def clean_frame_colors(raw) -> list[str]:
    """Uno por panel. Vacío, o lo que no sea un color, es el de serie."""
    source = raw if isinstance(raw, list) else []
    return [source[index].lower() if index < len(source) and isinstance(source[index], str) and re.fullmatch(r"#[0-9a-fA-F]{6}", source[index]) else ""
            for index in range(len(FRAME_COLORS))]


def frame_colors(raw) -> list[str]:
    return [chosen or default for chosen, default in zip(clean_frame_colors(raw), FRAME_COLORS)]


def _number(raw, low: int, high: int, default: int) -> int:
    return max(low, min(high, raw)) if isinstance(raw, int) and not isinstance(raw, bool) else default


def frame_width(raw, default: int = DEFAULT_FRAME_WIDTH) -> int:
    return _number(raw, 1, MAX_FRAME_WIDTH, default)


def frame_dim(raw) -> int:
    return _number(raw, 0, MAX_FRAME_DIM, DEFAULT_FRAME_DIM)


def frames_after(raw) -> int:
    return _number(raw, MIN_FRAMES_AFTER, MAX_FRAMES_AFTER, DEFAULT_FRAMES_AFTER)


def frames_shown(mode, after, pane_count: int) -> bool:
    mode = mode if mode in FRAME_MODES else DEFAULT_FRAME_MODE
    return mode == "always" or pane_count > frames_after(after)


def custom_key(name: str) -> str:
    return CUSTOM_PREFIX + name


def scheme_colors(scheme, customs, theme_name: str) -> dict[str, str]:
    """Los colores de una combinación con ese tema. Si ya no existe, los de serie."""
    if scheme in BUILTIN_SCHEMES:
        return dict(BUILTIN_SCHEMES[scheme][1].get(theme_name, {}))
    for custom in clean_customs(customs):
        if scheme == custom_key(custom["name"]):
            return clean_colors(custom)
    return {}


def adopt_old_colors(stored: dict) -> None:
    """Hasta la v11 había tres colores sueltos. Si se eligieron, pasan a ser una combinación propia en uso."""
    colors = clean_colors(stored.pop("pane_colors", None))
    if colors:
        stored["pane_color_customs"] = [*clean_customs(stored.get("pane_color_customs")), {"name": OLD_CUSTOM_NAME, **colors}]
        stored["pane_color_scheme"] = custom_key(OLD_CUSTOM_NAME)
