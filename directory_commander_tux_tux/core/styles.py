"""Los estilos que define el usuario: sus valores posibles, cómo se reparten los paneles y lo que hay que actualizar al cambiarles el nombre. Sin Qt."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import NamedTuple

from .config import DEFAULT_STYLES

MAX_PANES = 4
# Cómo se reparten los paneles: h, uno junto al otro; v, uno encima del otro. Cada número es un panel.
PANE_MODES = {"Off": "0",
              "Vertical": "h(0,1)",
              "Horizontal": "v(0,1)",
              "3-Vertical": "h(0,1,2)",
              "3-Horizontal": "v(0,1,2)",
              "3-Left": "h(0,v(1,2))",
              "3-Top": "v(h(0,1),2)",
              "4-Vertical": "h(0,1,2,3)",
              "4-Horizontal": "v(0,1,2,3)",
              "4-Grid": "v(h(0,1),h(2,3))"}
VIEWS = ("list", "details", "smallicons", "largeicons", "tiles", "thumbnails", "filmstrip")
FIRST, SECOND, THIRD, FOURTH, GENERAL = "first", "second", "third", "fourth", "general"
TERMINALS = (FIRST, SECOND, THIRD, FOURTH, GENERAL)  # la de cada panel y, detrás, la general
POSITIONS = ("right", "below")


@lru_cache(maxsize=128)
def _parse(text: str):
    """El árbol de una disposición: un número o (dirección, hijos). None si no es válida."""
    position = 0

    def node():
        nonlocal position
        match = re.compile(r"\d").match(text, position)
        if match:
            position += 1
            return int(match.group())
        if text[position:position + 2] not in ("h(", "v("):
            raise ValueError
        direction = text[position]
        position += 2
        children = [node()]
        while text[position:position + 1] == ",":
            position += 1
            children.append(node())
        if text[position:position + 1] != ")" or len(children) < 2:
            raise ValueError
        position += 1
        return direction, tuple(children)

    try:
        tree = node()
    except ValueError:
        return None
    if position != len(text) or sorted(_leaves(tree)) != list(range(len(_leaves(tree)))) or len(_leaves(tree)) > MAX_PANES:
        return None
    return tree


def _leaves(tree) -> list[int]:
    return [tree] if isinstance(tree, int) else [leaf for child in tree[1] for leaf in _leaves(child)]


def layout_tree(mode):
    """El árbol de una disposición, por su nombre o escrita tal cual. None si no es válida."""
    return _parse(PANE_MODES.get(mode, mode)) if isinstance(mode, str) else None


def valid_layout(mode) -> bool:
    return layout_tree(mode) is not None


def pane_count(mode: str) -> int:
    tree = layout_tree(mode)
    return len(_leaves(tree)) if tree is not None else 2


def stacked(mode: str) -> bool:
    """Paneles uno encima del otro: lo que los acompaña va a su derecha, porque abajo no queda sitio."""
    tree = layout_tree(mode)
    return isinstance(tree, tuple) and tree[0] == "v" and all(isinstance(child, int) for child in tree[1])


def layout_rects(mode: str) -> dict[int, tuple[float, float, float, float]]:
    """Dónde queda cada panel en un cuadrado de lado 1, repartido a partes iguales: (x, y, ancho, alto)."""
    rects: dict[int, tuple[float, float, float, float]] = {}

    def place(node, x, y, width, height):
        if isinstance(node, int):
            rects[node] = (x, y, width, height)
            return
        direction, children = node
        for position, child in enumerate(children):
            if direction == "h":
                place(child, x + width * position / len(children), y, width / len(children), height)
            else:
                place(child, x, y + height * position / len(children), width, height / len(children))

    place(layout_tree(mode) if valid_layout(mode) else layout_tree("Vertical"), 0.0, 0.0, 1.0, 1.0)
    return rects


def send_direction(mode: str, side: int, target: int) -> str:
    """El icono con la flecha que va de un panel hacia otro. Si uno queda encima del otro, aunque sea en parte, apunta hacia arriba o hacia abajo."""
    rects = layout_rects(mode)
    if side not in rects or target not in rects:
        return "sendright"
    x, y, width, _height = rects[side]
    other_x, other_y, other_width, _other_height = rects[target]
    if x < other_x + other_width - 1e-9 and other_x < x + width - 1e-9:
        return "senddown" if other_y > y else "sendup"
    return "sendright" if other_x > x else "sendleft"


def _layout_text(tree) -> str:
    if isinstance(tree, int):
        return str(tree)
    return f"{tree[0]}({','.join(_layout_text(child) for child in tree[1])})"


def _layout_name(tree) -> str:
    """Las que tienen nombre se guardan por él: las opciones de las pestañas y los botones cuentan con esos nombres."""
    text = _layout_text(tree)
    return next((name for name, layout in PANE_MODES.items() if layout == text), text)


def split_layout(mode: str, pane: int, where: str) -> tuple[str, int] | None:
    """La disposición con un panel nuevo a la derecha o debajo de `pane`, y el número del nuevo. None si no cabe otro."""
    tree = layout_tree(mode)
    count = pane_count(mode)
    if tree is None or count >= MAX_PANES or pane not in _leaves(tree):
        return None
    direction = "h" if where == "right" else "v"

    def split(node):
        if node == pane:
            return direction, (pane, count)
        if isinstance(node, int):
            return node
        children = []
        for child in node[1]:
            if child == pane and node[0] == direction:  # en la misma dirección va a su lado, sin otro nivel
                children.extend((pane, count))
            else:
                children.append(split(child))
        return node[0], tuple(children)

    return _layout_name(split(tree)), count


def node_extent(node, horizontal: bool, extents: dict) -> float:
    """Lo que mide una parte a lo largo de un separador: la suma de lo que va en esa dirección, o lo mayor de lo que va en la otra."""
    if isinstance(node, int):
        return extents.get(node, (0, 0))[0 if horizontal else 1]
    parts = [node_extent(child, horizontal, extents) for child in node[1]]
    return sum(parts) if (node[0] == "h") == horizontal else max(parts)


def _parent_of(tree, pane: int):
    """El nodo del que cuelga directamente un panel."""
    if isinstance(tree, int):
        return None
    if pane in tree[1]:
        return tree
    return next((found for child in tree[1] if (found := _parent_of(child, pane)) is not None), None)


def close_in_layout(mode: str, pane: int, extents: dict[int, tuple[float, float]]) -> tuple[str, dict[int, tuple[float, float]]] | None:
    """La disposición sin ese panel y lo que medirá cada uno de los que quedan. Se renumeran para no dejar huecos: el 2 pasa a ser el 1 si se cierra el 1.
    Su sitio es para el panel de al lado, que es lo que se espera al cerrarlo. None si es el único."""
    tree = layout_tree(mode)
    if not isinstance(tree, tuple) or pane not in _leaves(tree):
        return None
    parent = _parent_of(tree, pane)
    position = parent[1].index(pane)
    neighbour = parent[1][position - 1 if position > 0 else 1]
    horizontal = parent[0] == "h"
    axis = 0 if horizontal else 1
    old = node_extent(neighbour, horizontal, extents)
    factor = (old + extents.get(pane, (0, 0))[axis]) / old if old else 1.0
    sizes = dict(extents)
    for leaf in _leaves(neighbour):
        width, height = sizes.get(leaf, (0, 0))
        sizes[leaf] = (width * factor, height) if horizontal else (width, height * factor)

    def renumbered(leaf: int) -> int:
        return leaf - 1 if leaf > pane else leaf

    def without(node):
        if isinstance(node, int):
            return renumbered(node)
        children = []
        for child in node[1]:
            if child == pane:
                continue
            kept = without(child)
            # Si al quitarlo una parte queda con un solo hijo o va en la misma dirección, sobra ese nivel.
            children.extend(kept[1] if isinstance(kept, tuple) and kept[0] == node[0] else (kept,))
        return children[0] if len(children) == 1 else (node[0], tuple(children))

    return _layout_name(without(tree)), {renumbered(leaf): size for leaf, size in sizes.items() if leaf != pane}


TREE, PANE_TREE, PANE, TERMINAL, GENERAL_TERMINAL_PANEL, VIEWER, META = "tree", "pane_tree", "pane", "terminal", "general", "viewer", "meta"


class Region(NamedTuple):
    """Una parte de la ventana en el esquema de un estilo, en un cuadrado de lado 1."""
    kind: str
    index: int
    x: float
    y: float
    width: float
    height: float


def preview_regions(style: dict, full_width: bool = False, viewer_above: bool = True) -> list[Region]:
    """Lo que ocupa cada parte de la ventana con un estilo. Son proporciones orientativas: al usarlo, cada separador se puede mover."""
    mode = style.get("dual", "Vertical")
    mode = mode if valid_layout(mode) else "Vertical"
    regions: list[Region] = []
    x, y, width, height = 0.0, 0.0, 1.0, 1.0
    wanted = terminals_of(style) or set()
    general = GENERAL in wanted
    if general and full_width and not stacked(mode):
        regions.append(Region(GENERAL_TERMINAL_PANEL, 0, 0.0, 0.72, 1.0, 0.28))
        height = 0.72
        general = False
    viewer = bool(style.get("viewer", False))
    if viewer and style.get("viewer_area") in ("top", "bottom"):
        part = height * 0.5
        regions.append(Region(VIEWER, 0, x, y if style["viewer_area"] == "top" else y + height - part, width, part))
        y += part if style["viewer_area"] == "top" else 0.0
        height -= part
        viewer = False
    meta = bool(style.get("meta", False))
    if viewer or meta:
        column = 0.24
        width -= column
        docks = [kind for kind, shown in ((VIEWER, viewer), (META, meta)) if shown]
        docks = docks if viewer_above else docks[::-1]
        for position, kind in enumerate(docks):
            regions.append(Region(kind, 0, x + width, y + height * position / len(docks), column, height / len(docks)))
    double_tree = bool(style.get("double_tree", False))
    if style.get("tree", True) and not double_tree:
        regions.append(Region(TREE, 0, x, y, 0.2, height))
        x += 0.2
        width -= 0.2
    if general:
        if stacked(mode):
            width /= 2
            regions.append(Region(GENERAL_TERMINAL_PANEL, 0, x + width, y, width, height))
        else:
            regions.append(Region(GENERAL_TERMINAL_PANEL, 0, x, y + height * 0.68, width, height * 0.32))
            height *= 0.68
    position = position_of(style) or ("right" if stacked(mode) else "below")
    automatic = position_of(style) is None
    for pane, (rel_x, rel_y, rel_width, rel_height) in sorted(layout_rects(mode).items()):
        pane_x, pane_y, pane_width, pane_height = x + rel_x * width, y + rel_y * height, rel_width * width, rel_height * height
        if double_tree and style.get("tree", True):
            part = pane_width * 0.3
            regions.append(Region(PANE_TREE, pane, pane_x, pane_y, part, pane_height))
            pane_x += part
            pane_width -= part
        if pane < MAX_PANES and TERMINALS[pane] in wanted:
            if position == "right":
                pane_width /= 2
                regions.append(Region(TERMINAL, pane, pane_x + pane_width, pane_y, pane_width, pane_height))
            else:
                part = pane_height * (0.34 if automatic else 0.5)
                pane_height -= part
                regions.append(Region(TERMINAL, pane, pane_x, pane_y + pane_height, pane_width, part))
        regions.append(Region(PANE, pane, pane_x, pane_y, pane_width, pane_height))
    return regions


def terminals_of(style: dict) -> set[str] | None:
    """Qué terminales pide un estilo. None si no dice nada: entonces no cierra las que se hayan abierto a mano."""
    wanted = style.get("terminals")
    return {name for name in wanted if name in TERMINALS} if isinstance(wanted, list) else None


def position_of(style: dict) -> str | None:
    position = style.get("terminal_position")
    return position if position in POSITIONS else None


def unique_name(name: str, taken) -> str:
    """Dos estilos no pueden llamarse igual, ni con otras mayúsculas: se buscan por su nombre."""
    used = {str(other).casefold() for other in taken}
    if name.casefold() not in used:
        return name
    number = 2
    while f"{name} ({number})".casefold() in used:
        number += 1
    return f"{name} ({number})"


def missing_defaults(styles: list) -> list[dict]:
    """Copias de los estilos de serie que ya no están en la lista."""
    names = {str(style.get("name", "")).casefold() for style in styles if isinstance(style, dict)}
    return [json.loads(json.dumps(style)) for style in DEFAULT_STYLES if style["name"].casefold() not in names]


def rename_references(config, renames: dict[str, str]) -> None:
    """Lo que nombra a un estilo por su nombre: el actual, el orden y lo oculto en su barra, y los diseños guardados."""
    if config.get("style") in renames:
        config["style"] = renames[config["style"]]
    for section in ("bar_buttons", "bar_order"):
        bars = config.get(section)
        keys = bars.get("styles") if isinstance(bars, dict) else None
        if isinstance(keys, list):
            bars["styles"] = [renames.get(key, key) if isinstance(key, str) else key for key in keys]
    layouts = config.get("layouts")
    for layout in layouts.values() if isinstance(layouts, dict) else ():
        if isinstance(layout, dict) and layout.get("style") in renames:
            layout["style"] = renames[layout["style"]]
