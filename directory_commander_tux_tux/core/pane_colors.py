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
