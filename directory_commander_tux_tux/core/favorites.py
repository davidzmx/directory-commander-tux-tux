"""Árbol de favoritos. Sin Qt, para poder probarlo sin interfaz.

Una lista de elementos, cada uno de un tipo:
  carpeta  {"label": …, "path": …}
  archivo  {"label": …, "path": …, "file": True}
  rama     {"label": …, "children": […]}
  línea    {"separator": True}
  espacio  {"space": True}
  título   {"header": …}
La «Barra de favoritos» es una rama fija con {"bar": True}. Es siempre la primera, y si está vacía no se guarda.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Iterator

from .i18n import N_

BAR_LABEL = N_("Barra de favoritos")
MAX_DEPTH = 8  # un archivo importado no debe poder agotar la pila
_DIGITS = re.compile(r"(\d+)")


def kind(node) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("separator"):
        return "separator"
    if node.get("space"):
        return "space"
    if isinstance(node.get("header"), str):
        return "header"
    if isinstance(node.get("children"), list):
        return "branch"
    if isinstance(node.get("path"), str) and node["path"].strip():
        return "file" if node.get("file") is True else "folder"
    return ""


def clean(items, depth: int = 0) -> tuple[list[dict], int]:
    """Elementos válidos, con la barra primero, y cuántos se descartaron. Una ruta que aquí no
    exista se conserva: el archivo puede venir de otro equipo."""
    result: list[dict] = []
    on_bar: list[dict] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        what = kind(item)
        if what in ("separator", "space"):
            result.append({what: True})
        elif what == "header":
            result.append({"header": item["header"]})
        elif what == "branch" and depth < MAX_DEPTH:
            children, dropped = clean(item["children"], depth + 1)
            skipped += dropped
            if depth == 0 and item.get("bar") is True:
                on_bar.extend(children)
            else:
                label = item.get("label")
                result.append({"label": label if isinstance(label, str) and label else "?", "children": children})
        elif what in ("folder", "file"):
            label = item.get("label")
            node = {"label": label if isinstance(label, str) and label else item["path"], "path": item["path"]}
            if what == "file":
                node["file"] = True
            # Una versión anterior marcaba así lo que iba en la barra, sin rama propia.
            if depth == 0 and item.get("bar") is True:
                on_bar.append(node)
            else:
                result.append(node)
        else:
            skipped += 1
    if on_bar:
        result.insert(0, {"label": BAR_LABEL, "bar": True, "children": on_bar})
    return result, skipped


def bar_items(nodes) -> list[dict]:
    """Lo que hay en la barra de favoritos."""
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and node.get("bar") is True and isinstance(node.get("children"), list):
            return node["children"]
    return []


def other_items(nodes) -> list[dict]:
    return [node for node in (nodes if isinstance(nodes, list) else []) if not (isinstance(node, dict) and node.get("bar") is True)]


def with_bar(nodes) -> list[dict]:
    """Copia editable con la rama de la barra siempre presente y en primer lugar."""
    cleaned = copy.deepcopy(clean(nodes)[0])
    if not (cleaned and cleaned[0].get("bar") is True):
        cleaned.insert(0, {"label": BAR_LABEL, "bar": True, "children": []})
    return cleaned


def walk(nodes, depth: int = 0) -> Iterator[dict]:
    """Todas las carpetas y archivos favoritos, también los que están dentro de ramas."""
    for node in nodes if isinstance(nodes, list) else []:
        what = kind(node)
        if what in ("folder", "file"):
            yield node
        elif what == "branch" and depth < MAX_DEPTH:
            yield from walk(node["children"], depth + 1)


def contains(nodes, path: str) -> bool:
    return any(node["path"] == path for node in walk(nodes))


def _sort_key(node: dict) -> tuple:
    label = str(node.get("label", "")).casefold()
    return (kind(node) != "branch", [int(part) if part.isdigit() else part for part in _DIGITS.split(label)])


def sort_level(nodes: list[dict]) -> None:
    """Ordena un nivel, con las ramas delante. Las líneas, espacios y títulos no se mueven: lo que hay
    entre ellos se ordena por separado. La barra de favoritos sigue la primera."""
    fixed = {"separator", "space", "header"}
    start = 0
    for position in range(len(nodes) + 1):
        if position == len(nodes) or kind(nodes[position]) in fixed or nodes[position].get("bar") is True:
            nodes[start:position] = sorted(nodes[start:position], key=_sort_key)
            start = position + 1
