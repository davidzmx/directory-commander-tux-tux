"""El diálogo de añadir a favoritos y las páginas de favoritos, recientes y favoritos inteligentes de las preferencias."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import favorites, frequent, listing
from ..core.i18n import tr
from .icons import themed
from .model import folder_icon

KIND_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
LIST_ROLE = Qt.ItemDataRole.UserRole
SEPARATOR_TEXT = "──────────"
ROOT, BAR, BRANCH, FOLDER, FILE, SEPARATOR, SPACE, HEADER = "root", "bar", "branch", "folder", "file", "separator", "space", "header"
_CONTAINERS = (ROOT, BAR, BRANCH)
_WITH_PATH = (FOLDER, FILE)


def _information(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("prefsInfo")
    label.setWordWrap(True)
    return label


def _ask_text(parent, title: str, prompt: str, text: str = "") -> str:
    answer, ok = QInputDialog.getText(parent, title, prompt, QLineEdit.EchoMode.Normal, text)
    return answer.strip() if ok else ""


def _spin(value: int, low: int, high: int) -> QSpinBox:
    box = QSpinBox(minimum=low, maximum=high, value=value)
    box.setMinimumWidth(80)
    return box


def _with_unit(box: QSpinBox, unit: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(box)
    row.addWidget(QLabel(unit))
    row.addStretch(1)
    return row


class AddFavoriteDialog(QDialog):
    """Pide el nombre y la rama. Nada se guarda hasta aceptar, tampoco las ramas nuevas."""

    def __init__(self, path: str, nodes: list, parent=None, auto_sort: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Añadir a Favoritos"))
        self.resize(460, 340)
        self.path = path
        self.auto_sort = auto_sort
        self.nodes = favorites.with_bar(nodes)
        self._lists: list[list] = []
        label = listing.location_label(path)
        self.folder = QLineEdit(label)
        self.folder.setEnabled(False)
        self.name = QLineEdit(label)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.root = self._branch_item(self.tree, tr("Favoritos"), self.nodes)
        self._fill_branches(self.root, self.nodes)
        self.tree.expandAll()
        self.tree.setCurrentItem(self.root)
        new_branch = QPushButton(tr("Nueva Rama"))
        new_branch.clicked.connect(self.new_branch)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Aceptar"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.name.textChanged.connect(lambda text: self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(text.strip())))
        row = QHBoxLayout()
        row.addWidget(new_branch)
        row.addStretch(1)
        row.addWidget(self.buttons)
        form = QFormLayout()
        form.addRow(tr("Carpeta:"), self.folder)
        form.addRow(tr("Nombre:"), self.name)
        form.addRow(tr("Crear en:"), self.tree)
        layout = QVBoxLayout(self)
        layout.addLayout(form, 1)
        if favorites.contains(self.nodes, path):
            layout.addWidget(QLabel(tr("Esta carpeta ya está en favoritos.")))
        layout.addLayout(row)
        self.name.setFocus()
        # Queda seleccionada la parte de la carpeta superior: al borrarla se queda solo el nombre.
        prefix = len(label) - len(os.path.basename(label.rstrip("/\\")))
        self.name.setSelection(0, prefix if 0 < prefix < len(label) else len(label))

    def _branch_item(self, parent, text: str, children: list) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setIcon(0, themed("folder"))
        item.setData(0, LIST_ROLE, len(self._lists))  # un número: Qt copiaría una lista de Python
        self._lists.append(children)
        return item

    def _fill_branches(self, parent: QTreeWidgetItem, nodes: list) -> None:
        for node in nodes:
            if favorites.kind(node) == BRANCH:
                item = self._branch_item(parent, tr(node["label"]) if node.get("bar") else node["label"], node["children"])
                self._fill_branches(item, node["children"])

    def _ask_branch_name(self) -> str:
        return _ask_text(self, tr("Nueva Rama"), tr("Nombre de la rama:"))

    def new_branch(self) -> None:
        name = self._ask_branch_name()
        if name:
            parent = self.tree.currentItem() or self.root
            node = {"label": name, "children": []}
            self._lists[parent.data(0, LIST_ROLE)].append(node)
            item = self._branch_item(parent, name, node["children"])
            parent.setExpanded(True)
            self.tree.setCurrentItem(item)

    def result_nodes(self) -> list:
        """Los favoritos con el nuevo en la rama elegida."""
        target = self._lists[(self.tree.currentItem() or self.root).data(0, LIST_ROLE)]
        target.append({"label": self.name.text().strip(), "path": self.path})
        if self.auto_sort:
            favorites.sort_level(target)
        return favorites.clean(self.nodes)[0]


class _FavoritesTree(QTreeWidget):
    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        root = self.topLevelItem(0)
        for row in range(root.childCount()):
            if root.child(row).data(0, KIND_ROLE) == BAR and row != 0:  # la barra de favoritos sigue la primera
                root.insertChild(0, root.takeChild(row))
                root.child(0).setExpanded(True)
                break


class FavoritesPage(QWidget):
    """Árbol de favoritos con sus ramas. Se guarda al aceptar o aplicar las preferencias."""

    def __init__(self, config) -> None:
        super().__init__()
        self.tree = _FavoritesTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.DoubleClicked)
        self.tree.invisibleRootItem().setFlags(Qt.ItemFlag.ItemIsEnabled)
        nodes = favorites.with_bar(config.get("favorites"))
        self._states = listing.path_states([node["path"] for node in favorites.walk(nodes)])
        self.root = self._item(self.tree, ROOT, tr("Favoritos"))
        self._fill(self.root, nodes)
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(lambda _now, _before: self._selection_changed())

        new_menu = QMenu(self)
        new_menu.addAction(tr("Carpeta Favorita…"), self.new_folder)
        new_menu.addAction(tr("Archivo Favorito…"), self.new_file)
        new_menu.addAction(tr("Rama…"), self.new_branch)
        new_menu.addSeparator()
        new_menu.addAction(tr("Separador"), lambda: self._insert(SEPARATOR, SEPARATOR_TEXT))
        new_menu.addAction(tr("Espacio"), lambda: self._insert(SPACE, tr("(espacio)")))
        new_menu.addAction(tr("Encabezado…"), self.new_header)
        self.new_button = self._tool(tr("Nuevo"), "plus")
        self.new_button.setMenu(new_menu)
        self.new_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.edit_button = self._tool(tr("Editar"), "rename", self.edit_current)
        self.remove_button = self._tool(tr("Eliminar"), "delete", self.remove_current)
        self.up_button = self._tool(tr("Subir"), "goup", lambda: self.move_current(-1))
        self.down_button = self._tool(tr("Bajar"), "godown", lambda: self.move_current(1))
        for button, text, top in ((self.up_button, tr("Mover al principio"), True), (self.down_button, tr("Mover al final"), False)):
            menu = QMenu(self)
            menu.addAction(text, lambda t=top: self.move_to_edge(t))
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.sort_button = self._tool(tr("Ordenar"), "sort", self.sort_current)
        tools = QHBoxLayout()
        tools.addWidget(QLabel(tr("Favoritos:")))
        for button in (self.new_button, self.edit_button, self.remove_button, self.up_button, self.down_button, self.sort_button):
            tools.addWidget(button)
        tools.addStretch(1)
        self.path_field = QLineEdit()
        self.path_field.setPlaceholderText(tr("Ruta del favorito seleccionado"))
        self.path_field.textEdited.connect(self._path_edited)
        self.path_field.editingFinished.connect(self._refresh_current_icon)
        self.auto_sort = QCheckBox(tr("Ordenar automáticamente nuevos favoritos"))
        self.auto_sort.setChecked(config.get("favorites_autosort", False) is True)
        layout = QVBoxLayout(self)
        layout.addWidget(_information(tr(
            "Los favoritos dan acceso rápido a carpetas y archivos desde el botón ★ de cada panel, el menú Favoritos y el árbol de carpetas. "
            "Las ramas los agrupan en submenús. Arrástralos para ordenarlos o para meterlos en una rama.\n\n"
            "Lo que pongas en «Barra de favoritos» aparece en esa barra, si está visible; lo demás, en su botón ★ Otros Favoritos.")))
        layout.addLayout(tools)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.path_field)
        layout.addWidget(self.auto_sort)
        self.tree.setCurrentItem(self.root)
        self._selection_changed()

    @staticmethod
    def _tool(text: str, icon: str, slot=None) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setIcon(themed(icon))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if slot is not None:
            button.clicked.connect(lambda _checked=False: slot())
        return button

    # ---------------------------------------------------------------- elementos
    def _item(self, parent, kind: str, text: str, path: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)
        item.setData(0, KIND_ROLE, kind)
        item.setData(0, PATH_ROLE, path)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if kind in _CONTAINERS:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        if kind not in (ROOT, BAR):
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if kind in (BRANCH, FOLDER, FILE, HEADER):
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        self._set_icon(item)
        if kind == HEADER:
            font = QFont(item.font(0))
            font.setBold(True)
            item.setFont(0, font)
        return item

    def _set_icon(self, item: QTreeWidgetItem) -> None:
        kind, path = item.data(0, KIND_ROLE), item.data(0, PATH_ROLE)
        if kind in _CONTAINERS:
            item.setIcon(0, themed("folder"))
        elif kind in _WITH_PATH:
            exists = self._states.get(path)
            if exists is None and path not in self._states:
                exists = self._states[path] = path == listing.COMPUTER_PATH or os.path.exists(path)
            item.setIcon(0, folder_icon(path) if exists else themed("file-kind" if kind == FILE else "folder"))
            item.setToolTip(0, listing.location_label(path))

    def _fill(self, parent: QTreeWidgetItem, nodes: list) -> None:
        for node in nodes:
            kind = favorites.kind(node)
            if kind == BRANCH:
                bar = node.get("bar") is True
                item = self._item(parent, BAR if bar else BRANCH, tr(node["label"]) if bar else node["label"])
                self._fill(item, node["children"])
            elif kind in _WITH_PATH:
                self._item(parent, kind, node["label"], node["path"])
            elif kind == SEPARATOR:
                self._item(parent, SEPARATOR, SEPARATOR_TEXT)
            elif kind == SPACE:
                self._item(parent, SPACE, tr("(espacio)"))
            elif kind == HEADER:
                self._item(parent, HEADER, node["header"])

    def _read(self, parent: QTreeWidgetItem) -> list:
        nodes: list = []
        for row in range(parent.childCount()):
            item = parent.child(row)
            kind, text = item.data(0, KIND_ROLE), item.text(0)
            if kind == BAR:
                nodes.append({"label": favorites.BAR_LABEL, "bar": True, "children": self._read(item)})
            elif kind == BRANCH:
                nodes.append({"label": text, "children": self._read(item)})
            elif kind == FOLDER:
                nodes.append({"label": text, "path": item.data(0, PATH_ROLE)})
            elif kind == FILE:
                nodes.append({"label": text, "path": item.data(0, PATH_ROLE), "file": True})
            elif kind in (SEPARATOR, SPACE):
                nodes.append({kind: True})
            elif kind == HEADER:
                nodes.append({"header": text})
        return nodes

    def values(self) -> list:
        return favorites.clean(self._read(self.root))[0]

    # ---------------------------------------------------------------- selección
    def _current(self) -> QTreeWidgetItem:
        return self.tree.currentItem() or self.root

    def _selection_changed(self) -> None:
        item = self._current()
        kind = item.data(0, KIND_ROLE)
        fixed = kind in (ROOT, BAR)
        self.edit_button.setEnabled(bool(item.flags() & Qt.ItemFlag.ItemIsEditable))
        self.remove_button.setEnabled(not fixed)
        self.up_button.setEnabled(not fixed)
        self.down_button.setEnabled(not fixed)
        self.path_field.setEnabled(kind in _WITH_PATH)
        self.path_field.setText(item.data(0, PATH_ROLE) if kind in _WITH_PATH else "")

    def _path_edited(self, text: str) -> None:
        item = self._current()
        if item.data(0, KIND_ROLE) in _WITH_PATH and text.strip():
            item.setData(0, PATH_ROLE, text.strip())

    def _refresh_current_icon(self) -> None:
        item = self._current()
        if item.data(0, KIND_ROLE) in _WITH_PATH:
            self._states.pop(item.data(0, PATH_ROLE), None)
            self._set_icon(item)

    # ---------------------------------------------------------------- nuevos
    def _ask_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, tr("Carpeta Favorita"), "")

    def _ask_file(self) -> str:
        return QFileDialog.getOpenFileName(self, tr("Archivo Favorito"), "")[0]

    def _ask(self, title: str, prompt: str, text: str = "") -> str:
        return _ask_text(self, title, prompt, text)

    def new_folder(self) -> None:
        path = self._ask_folder()
        if path:
            path = os.path.normpath(path)
            self._insert(FOLDER, os.path.basename(path.rstrip("/\\")) or path, path)

    def new_file(self) -> None:
        path = self._ask_file()
        if path:
            path = os.path.normpath(path)
            self._insert(FILE, os.path.basename(path), path)

    def new_branch(self) -> None:
        name = self._ask(tr("Nueva Rama"), tr("Nombre de la rama:"))
        if name:
            self._insert(BRANCH, name)

    def new_header(self) -> None:
        text = self._ask(tr("Encabezado"), tr("Texto del encabezado:"))
        if text:
            self._insert(HEADER, text)

    def _insert(self, kind: str, text: str, path: str = "") -> None:
        """Dentro de la rama seleccionada, o detrás del elemento seleccionado."""
        current = self._current()
        parent = current if current.data(0, KIND_ROLE) in _CONTAINERS else current.parent()
        item = self._item(parent, kind, text, path)
        if current is not parent:
            parent.insertChild(parent.indexOfChild(current) + 1, parent.takeChild(parent.indexOfChild(item)))
        parent.setExpanded(True)
        if self.auto_sort.isChecked() and kind in (FOLDER, FILE, BRANCH):
            self._sort(parent)
            item = self._find(parent, kind, text, path)
        self.tree.setCurrentItem(item)

    @staticmethod
    def _find(parent: QTreeWidgetItem, kind: str, text: str, path: str) -> QTreeWidgetItem | None:
        for row in range(parent.childCount()):
            item = parent.child(row)
            if item.data(0, KIND_ROLE) == kind and item.text(0) == text and item.data(0, PATH_ROLE) == path:
                return item
        return None

    # ---------------------------------------------------------------- cambios
    def edit_current(self) -> None:
        item = self._current()
        if item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.tree.editItem(item)

    def remove_current(self) -> None:
        item = self._current()
        if item.data(0, KIND_ROLE) not in (ROOT, BAR):
            item.parent().removeChild(item)
            self._selection_changed()

    def _lowest_row(self, parent: QTreeWidgetItem) -> int:
        return 1 if parent is self.root else 0  # no se puede poner delante de la barra de favoritos

    def _move_to(self, item: QTreeWidgetItem, row: int) -> None:
        parent = item.parent()
        parent.takeChild(parent.indexOfChild(item))
        parent.insertChild(row, item)
        self.tree.expandRecursively(self.tree.indexFromItem(item))  # sacarla de su sitio la pliega
        self.tree.setCurrentItem(item)

    def move_current(self, delta: int) -> None:
        item = self._current()
        if item.data(0, KIND_ROLE) in (ROOT, BAR):
            return
        parent = item.parent()
        row = parent.indexOfChild(item) + delta
        if self._lowest_row(parent) <= row < parent.childCount():
            self._move_to(item, row)

    def move_to_edge(self, to_top: bool) -> None:
        item = self._current()
        if item.data(0, KIND_ROLE) not in (ROOT, BAR):
            parent = item.parent()
            self._move_to(item, self._lowest_row(parent) if to_top else parent.childCount() - 1)

    def _sort(self, parent: QTreeWidgetItem) -> None:
        nodes = self._read(parent)
        favorites.sort_level(nodes)
        parent.takeChildren()
        self._fill(parent, nodes)
        self.tree.expandRecursively(self.tree.indexFromItem(parent))

    def sort_current(self) -> None:
        """Ordena el contenido de la rama seleccionada, o el nivel del elemento seleccionado."""
        item = self._current()
        self.tree.setCurrentItem(None)
        self._sort(item if item.data(0, KIND_ROLE) in _CONTAINERS else item.parent())
        self.tree.setCurrentItem(self.root)


class RecentPage(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        settings = frequent.normalize_recent(config.get("recent_list"))
        self.enabled = QCheckBox(tr("Habilitar la lista de recientes"))
        self.enabled.setChecked(settings["enabled"])
        self.maximum = _spin(settings["max"], 1, frequent.MAX_RECENT_LIMIT)
        self.virtual = QCheckBox(tr("Incluir carpetas virtuales (Este equipo y el interior de los comprimidos)"))
        self.virtual.setChecked(settings["virtual"])
        self.skip_transit = QCheckBox(tr("Ignorar las carpetas por las que solo se pasa de camino a otra"))
        self.skip_transit.setChecked(settings["skip_transit"])
        history = config.get("history_size", 10)
        self.history = _spin(history if isinstance(history, int) else 10, 1, frequent.MAX_HISTORY_LIMIT)
        form = QFormLayout()
        form.addRow(tr("Tamaño máximo:"), _with_unit(self.maximum, tr("carpetas")))
        history_form = QFormLayout()
        history_form.addRow(tr("Tamaño de historial:"), _with_unit(self.history, tr("carpetas por pestaña")))
        layout = QVBoxLayout(self)
        layout.addWidget(_information(tr(
            "La lista de recientes guarda las últimas carpetas usadas en todo el programa y está en el menú del botón Atrás de la barra de herramientas.\n\n"
            "Además, cada pestaña tiene su propio historial, el que recorren los botones Atrás y Adelante.")))
        layout.addWidget(self.enabled)
        layout.addLayout(form)
        layout.addWidget(self.virtual)
        layout.addWidget(self.skip_transit)
        layout.addSpacing(16)
        layout.addLayout(history_form)
        layout.addStretch(1)

    def apply_to(self, config) -> None:
        config["recent_list"] = {"enabled": self.enabled.isChecked(), "max": self.maximum.value(), "virtual": self.virtual.isChecked(),
                                 "skip_transit": self.skip_transit.isChecked()}
        config["history_size"] = self.history.value()


class FrequentFoldersPage(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        settings = frequent.normalize_smart(config.get("smart_favorites"))
        self.clear_requested = False
        self.enabled = QCheckBox(tr("Habilitar los favoritos inteligentes"))
        self.enabled.setChecked(settings["enabled"])
        self.check_startup = QCheckBox(tr("Comprobar al inicio si hay carpetas que ya no existen"))
        self.check_startup.setChecked(settings["check_startup"])
        self.max_shown = _spin(settings["max_shown"], 1, 100)
        self.min_points = _spin(settings["min_points"], 0, 100000)
        self.table_size = _spin(settings["table_size"], 1, 10000)
        self.clear_button = QPushButton(tr("Limpiar"))
        self.clear_button.setToolTip(tr("Olvida los puntos de todas las carpetas al aceptar o aplicar"))
        self.clear_button.clicked.connect(self._clear)
        table_row = _with_unit(self.table_size, tr("carpetas"))
        table_row.insertWidget(2, self.clear_button)
        form = QFormLayout()
        form.addRow(tr("Mostrar como máximo:"), _with_unit(self.max_shown, tr("carpetas")))
        form.addRow(tr("Con al menos:"), _with_unit(self.min_points, tr("punto(s)")))
        form.addRow(tr("Tabla de referencia:"), table_row)
        self.exclude = QListWidget()
        for rule in settings["exclude"]:
            self._add_rule(rule["pattern"], rule["subfolders"])
        self.exclude.currentItemChanged.connect(lambda _now, _before: self._rule_selected())
        self.edit_rule_button = self._button(tr("Editar"), self.edit_rule)
        self.remove_rule_button = self._button(tr("Remover"), self.remove_rule)
        tools = QHBoxLayout()
        tools.addWidget(QLabel(tr("Excluir estas carpetas:")))
        tools.addWidget(self._button(tr("Añadir carpeta"), self.add_folder))
        tools.addWidget(self._button(tr("Añadir comodín"), self.add_wildcard))
        tools.addWidget(self.edit_rule_button)
        tools.addWidget(self.remove_rule_button)
        tools.addStretch(1)
        self.subfolders = QCheckBox(tr("Aplicar a todas las subcarpetas"))
        self.subfolders.toggled.connect(self._subfolders_toggled)
        layout = QVBoxLayout(self)
        layout.addWidget(_information(tr(
            "Las carpetas que más usas aparecen solas debajo de tus favoritos.\n\n"
            "Cada acción da a su carpeta una cantidad de puntos, que se edita en la página Puntaje, y las que acumulan bastantes se muestran en la lista.")))
        layout.addWidget(self.enabled)
        layout.addWidget(self.check_startup)
        layout.addLayout(form)
        layout.addLayout(tools)
        layout.addWidget(self.exclude, 1)
        layout.addWidget(self.subfolders)
        self._rule_selected()

    @staticmethod
    def _button(text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _clear(self) -> None:
        self.clear_requested = True
        self.clear_button.setEnabled(False)

    def _add_rule(self, pattern: str, subfolders: bool = False) -> QListWidgetItem:
        item = QListWidgetItem(pattern)
        item.setData(Qt.ItemDataRole.UserRole, subfolders)
        self.exclude.addItem(item)
        return item

    def _rule_selected(self) -> None:
        item = self.exclude.currentItem()
        self.edit_rule_button.setEnabled(item is not None)
        self.remove_rule_button.setEnabled(item is not None)
        self.subfolders.setEnabled(item is not None)
        self.subfolders.blockSignals(True)
        self.subfolders.setChecked(item is not None and item.data(Qt.ItemDataRole.UserRole) is True)
        self.subfolders.blockSignals(False)

    def _subfolders_toggled(self, checked: bool) -> None:
        item = self.exclude.currentItem()
        if item is not None:
            item.setData(Qt.ItemDataRole.UserRole, checked)

    def _ask_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, tr("Carpeta que se excluye"), "")

    def _ask(self, title: str, prompt: str, text: str = "") -> str:
        return _ask_text(self, title, prompt, text)

    def add_folder(self) -> None:
        path = self._ask_folder()
        if path:
            self.exclude.setCurrentItem(self._add_rule(os.path.normpath(path)))

    def add_wildcard(self) -> None:
        pattern = self._ask(tr("Añadir comodín"), tr("Ruta con comodines (ej.: *\\temp\\*):"))
        if pattern:
            self.exclude.setCurrentItem(self._add_rule(pattern))

    def edit_rule(self) -> None:
        item = self.exclude.currentItem()
        if item is not None:
            pattern = self._ask(tr("Editar"), tr("Ruta con comodines (ej.: *\\temp\\*):"), item.text())
            if pattern:
                item.setText(pattern)

    def remove_rule(self) -> None:
        row = self.exclude.currentRow()
        if row >= 0:
            self.exclude.takeItem(row)

    def apply_to(self, config, points: dict) -> None:
        items = (self.exclude.item(row) for row in range(self.exclude.count()))
        config["smart_favorites"] = {"enabled": self.enabled.isChecked(), "check_startup": self.check_startup.isChecked(),
                                     "max_shown": self.max_shown.value(), "min_points": self.min_points.value(), "table_size": self.table_size.value(),
                                     "exclude": [{"pattern": item.text(), "subfolders": item.data(Qt.ItemDataRole.UserRole) is True} for item in items],
                                     "points": points}
        if self.clear_requested:
            config["smart_points"] = {}
            self.clear_requested = False
            self.clear_button.setEnabled(True)
        table = config.get("smart_points")
        if isinstance(table, dict):
            frequent.trim(table, self.table_size.value())


class FrequentPointsPage(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        points = frequent.normalize_smart(config.get("smart_favorites"))["points"]
        rows = sorted(((tr(label), key) for key, label, _default in frequent.ACTIONS), key=lambda row: row[0].casefold())
        self.spins: dict[str, QSpinBox] = {}
        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels([tr("Acción"), tr("Puntos")])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for row, (label, key) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.spins[key] = QSpinBox(minimum=0, maximum=100000, value=points[key])
            self.table.setCellWidget(row, 1, self.spins[key])
        layout = QVBoxLayout(self)
        layout.addWidget(_information(tr("Los puntos que da cada acción a la carpeta en la que ocurre. Las carpetas con más puntos son las que aparecen en los favoritos inteligentes.")))
        layout.addWidget(self.table, 1)

    def values(self) -> dict[str, int]:
        return {key: spin.value() for key, spin in self.spins.items()}
