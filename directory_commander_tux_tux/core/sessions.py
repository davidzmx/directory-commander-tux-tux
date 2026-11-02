"""Las sesiones guardadas: paneles, pestañas y terminales con un nombre. Sin Qt."""
from __future__ import annotations

import os
from datetime import datetime

from . import styles


_TEXT_KEYS = ("geometry", "state", "splitter")  # se convierten a bytes al cargarla


def default_name(now: datetime | None = None) -> str:
    return f"sesion_{(now or datetime.now()):%Y%m%d_%H%M%S}"


def _usable(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("tabs", []), list) and all(isinstance(data.get(key, ""), str) for key in _TEXT_KEYS)


def clean(raw) -> list[dict]:
    """Lo guardado. Sin nombre no se podría elegir, sin datos no habría nada que cargar y con dos iguales no se distinguirían."""
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip() and _usable(item.get("data")) and name.strip().casefold() not in seen:
            seen.add(name.strip().casefold())
            result.append({"name": name.strip(), "data": item["data"]})
    return result


def names(raw) -> list[str]:
    return [item["name"] for item in clean(raw)]


def find(raw, name: str) -> dict | None:
    wanted = name.strip().casefold()
    return next((item["data"] for item in clean(raw) if item["name"].casefold() == wanted), None)


def stored(raw, name: str, data: dict) -> list[dict]:
    """La lista con esa sesión. Si ya había una con ese nombre, la sustituye en su sitio."""
    items = clean(raw)
    wanted = name.strip().casefold()
    for item in items:
        if item["name"].casefold() == wanted:
            item["data"] = data
            return items
    return [*items, {"name": name.strip(), "data": data}]


def renamed(raw, old: str, new: str) -> list[dict]:
    """La lista con otro nombre para esa sesión. Un nombre vacío o ya usado por otra es un ValueError."""
    items = clean(raw)
    target = new.strip()
    if not target:
        raise ValueError("vacío")
    if any(item["name"].casefold() == target.casefold() and item["name"].casefold() != old.strip().casefold() for item in items):
        raise ValueError("repetido")
    for item in items:
        if item["name"].casefold() == old.strip().casefold():
            item["name"] = target
    return items


def removed(raw, name: str) -> list[dict]:
    wanted = name.strip().casefold()
    return [item for item in clean(raw) if item["name"].casefold() != wanted]


def describe(data: dict) -> tuple[int, int]:
    """Cuántos paneles con pestañas y cuántas pestañas guarda."""
    tabs = [group for group in data.get("tabs", []) if isinstance(group, list) and group] if isinstance(data.get("tabs"), list) else []
    return len(tabs), sum(len(group) for group in tabs)


def _terminals_shown(data: dict) -> list[str]:
    panes = data.get("pane_terminals") if isinstance(data.get("pane_terminals"), list) else []
    saved = [*panes[:styles.MAX_PANES], *[None] * (styles.MAX_PANES - min(len(panes), styles.MAX_PANES)), data.get("general_terminal")]
    return [name for name, item in zip(styles.TERMINALS, saved) if isinstance(item, dict) and item.get("visible")]


def layout_of(data: dict, known_styles=()) -> dict:
    """Cómo queda la ventana con esa sesión, descrito como un estilo. Las guardadas por versiones anteriores no lo dicen: se parte del estilo que usaban."""
    layout = data.get("layout")
    if isinstance(layout, dict):
        return layout
    name = data.get("style")
    style = next((item for item in known_styles if isinstance(item, dict) and item.get("name") == name), {})
    return {**style, "dual": data.get("dual") or style.get("dual", "Vertical"), "terminals": _terminals_shown(data)}


def tab_labels(data: dict) -> dict[int, list[str]]:
    """Las pestañas que guarda cada panel, con su nombre o el de su carpeta."""
    tabs = data.get("tabs") if isinstance(data.get("tabs"), list) else []
    names = data.get("tab_names") if isinstance(data.get("tab_names"), list) else []
    result: dict[int, list[str]] = {}
    for pane, group in enumerate(tabs):
        given = names[pane] if pane < len(names) and isinstance(names[pane], list) else []
        result[pane] = [given[position] if position < len(given) and isinstance(given[position], str) and given[position]
                        else os.path.basename(path.rstrip("\\/")) or path
                        for position, path in enumerate(group if isinstance(group, list) else []) if isinstance(path, str)]
    return result


def _signature(data: dict) -> dict:
    """Lo que al cambiar se considera un cambio de la sesión. Mover un separador o cambiar de pestaña activa no merece una pregunta."""
    return {"dual": data.get("dual"), "style": data.get("style"), "tabs": data.get("tabs"), "tab_names": data.get("tab_names"), "view_modes": data.get("view_modes"),
            "terminals": _terminals_shown(data), "layout": data.get("layout")}


def same(first: dict, second: dict) -> bool:
    """Si dos estados de la ventana son la misma sesión. Lo que una de ellas no guarda por ser de una versión anterior no cuenta."""
    one, other = _signature(first), _signature(second)
    if one["layout"] is None or other["layout"] is None:
        one["layout"] = other["layout"] = None
    return one == other
