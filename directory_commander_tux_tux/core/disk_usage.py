"""Qué ocupa cada carpeta y cada archivo, para el mapa de uso del disco. Sin Qt, para poder probarlo sin interfaz.

El recorrido no sigue enlaces ni entra en otros discos montados dentro (como /proc o una unidad USB bajo /media al analizar /):
cuenta lo que hay en el disco de la carpeta. Un archivo con varios enlaces duros se cuenta una vez."""
from __future__ import annotations

import os
import stat
from array import array

REPORT_EVERY = 2000  # archivos entre aviso y aviso del avance


class Node:
    """Una carpeta o un archivo. Los archivos de cada carpeta se guardan juntos, en un texto y una lista de números, y solo se
    convierten en nodos al pedir sus hijos: con millones de archivos, un objeto por cada uno ocuparía gigas de memoria."""

    __slots__ = ("name", "is_dir", "size", "files", "mtime", "parent", "folders", "error", "_root_path", "_names", "_sizes", "_children")

    def __init__(self, name: str, is_dir: bool, size: int = 0, files: int = 0, mtime: float = 0.0, parent: "Node | None" = None,
                 root_path: str = "") -> None:
        self.name, self.is_dir, self.size, self.files, self.mtime, self.parent = name, is_dir, size, files, mtime, parent
        self.folders: list[Node] = []
        self.error = ""  # una carpeta que no se pudo leer
        self._root_path = root_path
        self._names: list[str] = []  # mientras se recorre; luego, en un solo texto
        self._sizes = array("q")
        self._children: list[Node] | None = None

    def __repr__(self) -> str:
        return f"Node({self.path!r}, {self.size})"

    @property
    def path(self) -> str:
        if self.parent is None:
            return self._root_path or self.name
        return os.path.join(self.parent.path, self.name)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.name)[1][1:].lower() if not self.is_dir else ""

    @property
    def children(self) -> list["Node"]:
        """Subcarpetas y archivos, de mayor a menor."""
        if self._children is None:
            names = self._names.split("\0") if isinstance(self._names, str) and self._names else []
            files = [Node(name, False, size, 1, parent=self) for name, size in zip(names, self._sizes)]
            self._children = sorted([*self.folders, *files], key=lambda child: child.size, reverse=True) if self.is_dir else []
        return self._children


def scan(root: str, sink=None, one_filesystem: bool = True) -> Node:
    """El árbol de `root`. `sink` informa del avance y permite cancelar."""
    root = os.path.normpath(root)
    info = os.stat(root)
    top = Node(os.path.basename(root.rstrip(os.sep)) or root, True, mtime=info.st_mtime, root_path=root)
    device = info.st_dev
    seen_links: set[tuple[int, int]] = set()
    counted = 0
    stack = [(top, root)]
    order: list[Node] = []
    while stack:
        folder, folder_path = stack.pop()
        order.append(folder)
        if sink is not None:
            sink.check()
        try:
            with os.scandir(folder_path) as found:
                items = list(found)
        except OSError as exc:
            folder.error = exc.strerror or str(exc)
            continue
        names, sizes = folder._names, folder._sizes
        for item in items:
            try:
                details = item.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(details.st_mode):
                if one_filesystem and details.st_dev != device:
                    continue  # otro disco montado aquí dentro
                child = Node(item.name, True, mtime=details.st_mtime, parent=folder)
                folder.folders.append(child)
                stack.append((child, item.path))
                continue
            if details.st_nlink > 1:
                key = (details.st_dev, details.st_ino)
                if key in seen_links:
                    continue
                seen_links.add(key)
            names.append(item.name)
            sizes.append(details.st_size if stat.S_ISREG(details.st_mode) else 0)
            counted += 1
            if sink is not None and counted % REPORT_EVERY == 0:
                sink.report(counted, 0, folder_path)
        folder._names = "\0".join(names)
    for folder in reversed(order):  # de las más profundas hacia arriba: cada una suma lo de sus hijas, ya sumadas
        folder.size = sum(folder._sizes) + sum(child.size for child in folder.folders)
        folder.files = len(folder._sizes) + sum(child.files for child in folder.folders)
    return top


def _worst(row: list[float], side: float) -> float:
    """La peor proporción (lado largo entre lado corto) de los rectángulos de una fila apoyada en un lado de largo `side`."""
    total = sum(row)
    if total <= 0 or side <= 0:
        return float("inf")
    largest, smallest = max(row), min(row)
    return max(side * side * largest / (total * total), total * total / (side * side * smallest))


def squarify(values: list[float], x: float, y: float, width: float, height: float) -> list[tuple[float, float, float, float]]:
    """Reparte el rectángulo en uno por valor, de área proporcional y lo más cuadrados posible (el algoritmo «squarified»).
    Los valores deben venir de mayor a menor; los nulos dan un rectángulo vacío."""
    total = sum(values)
    result: list[tuple[float, float, float, float]] = []
    if total <= 0 or width <= 0 or height <= 0:
        return [(x, y, 0.0, 0.0) for _ in values]
    scale = width * height / total
    areas = [value * scale for value in values]
    index = 0
    while index < len(areas):
        if areas[index] <= 0:
            result.append((x, y, 0.0, 0.0))
            index += 1
            continue
        side = min(width, height)
        row = [areas[index]]
        index += 1
        while index < len(areas) and areas[index] > 0 and _worst(row + [areas[index]], side) <= _worst(row, side):
            row.append(areas[index])
            index += 1
        row_total = sum(row)
        if width >= height:  # la fila es una columna a la izquierda
            column = row_total / height
            top = y
            for area in row:
                piece = area / column
                result.append((x, top, column, piece))
                top += piece
            x, width = x + column, width - column
        else:  # una fila arriba
            line = row_total / width
            left = x
            for area in row:
                piece = area / line
                result.append((left, y, piece, line))
                left += piece
            y, height = y + line, height - line
    return result


def remove(node: Node) -> None:
    """Quita del árbol algo que se ha borrado: deja de contar en todas las carpetas por encima, que se vuelven a ordenar."""
    parent = node.parent
    if parent is None:
        return
    if node.is_dir:
        parent.folders = [folder for folder in parent.folders if folder is not node]
    else:
        names = parent._names.split("\0") if isinstance(parent._names, str) and parent._names else []
        if node.name in names:
            position = names.index(node.name)
            del names[position]
            del parent._sizes[position]
            parent._names = "\0".join(names)
    if parent._children is not None:
        parent._children = [child for child in parent._children if child is not node]
    step = parent
    while step is not None:
        step.size -= node.size
        step.files -= node.files
        if step._children is not None:
            step._children.sort(key=lambda child: child.size, reverse=True)
        step = step.parent


def path_to(node: Node) -> list[Node]:
    """De la raíz al nodo."""
    chain = []
    while node is not None:
        chain.append(node)
        node = node.parent
    return chain[::-1]


def find(root: Node, path: str) -> Node | None:
    """El nodo de esa ruta dentro del árbol, bajando solo por la rama que la contiene."""
    path = os.path.normpath(path)
    node = root
    while node is not None and node.path != path:
        node = next((child for child in node.children if path == child.path or path.startswith(child.path.rstrip(os.sep) + os.sep)), None)
    return node
