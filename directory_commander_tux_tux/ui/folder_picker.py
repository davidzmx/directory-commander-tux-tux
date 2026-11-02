"""Ventana para elegir una o varias carpetas: con las casillas del árbol, de las listas de arriba o escribiendo la ruta."""
from __future__ import annotations

import os

from PySide6.QtCore import QFileInfo, QStandardPaths, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileIconProvider, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QTreeWidgetItem, QTreeWidgetItemIterator,
                               QVBoxLayout)

from ..core import favorites as favorites_tree
from ..core import frequent
from ..core.i18n import tr
from ..core.listing import COMPUTER_LABEL, COMPUTER_PATH, has_computer_view
from ..core.platform_utils import home_dir
from .folder_tree import KNOWN_FOLDERS, LOADED_ROLE, PATH_ROLE, FolderTree, _icons, _same_path
from .icons import themed

GROUP_ROLE = Qt.ItemDataRole.UserRole + 2  # las unidades que representa una fila que no es una carpeta


def _check(item: QTreeWidgetItem) -> None:
    """Qt solo dibuja la casilla si la fila tiene un estado. Marcarla como «que se puede marcar» no basta: ya lo son todas."""
    if item.data(0, Qt.ItemDataRole.CheckStateRole) is None:
        item.setCheckState(0, Qt.CheckState.Unchecked)


class CheckedFolderTree(FolderTree):
    """Primero los accesos a las carpetas personales y después las unidades, todo con su casilla. Aquí no se arrastra ni se suelta nada."""

    drives_item: QTreeWidgetItem | None = None  # se crea desde el constructor de la clase base

    def __init__(self, show_hidden: bool = False, parent=None) -> None:
        super().__init__(show_hidden, parent)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)

    def _node(self, parent, label: str, path: str, icon, has_children: bool = True) -> QTreeWidgetItem:
        item = super()._node(parent, label, path, icon, has_children)
        _check(item)
        return item

    def _build_sections(self) -> None:
        home = home_dir()
        places = [(label, os.path.normpath(path)) for label, location in KNOWN_FOLDERS if (path := QStandardPaths.writableLocation(location)) and os.path.isdir(path)]
        for label, path in sorted(places, key=lambda place: place[0].casefold()):
            if not _same_path(path, home):
                self._node(self, label, path, _icons.icon(QFileInfo(path)))
        self.home_item = self._node(self, tr("Perfil"), home, themed("home"))
        if has_computer_view():
            from ..core.drives import local_drives

            self.root_item = self._node(self, COMPUTER_LABEL, COMPUTER_PATH, _icons.icon(QFileIconProvider.IconType.Computer))
            drives = local_drives()
            if drives:
                letters = ", ".join(drive.rstrip("\\") for drive in drives)
                self.drives_item = self._node(self.root_item, tr("Discos duros locales ({letters})", letters=letters), "", _icons.icon(QFileIconProvider.IconType.Drive), has_children=False)
                self.drives_item.setData(0, LOADED_ROLE, True)
                self.drives_item.setData(0, GROUP_ROLE, drives)
                self.drives_item.setToolTip(0, letters)
        else:
            self.root_item = self._node(self, tr("Sistema de archivos"), os.sep, themed("drive"))
        self.root_item.setExpanded(True)

    def _merge(self, item: QTreeWidgetItem, rows: list) -> None:
        group = self.drives_item if item is self.root_item else None
        if group is not None:
            item.removeChild(group)  # no es una carpeta: la lista de unidades que llega no lo incluye
        super()._merge(item, rows)
        if group is not None:
            item.insertChild(0, group)
        for position in range(item.childCount()):
            _check(item.child(position))

    def stop_syncing(self) -> None:
        self._sync_token += 1

    def checked_paths(self) -> list[str]:
        found: list[str] = []
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            item = iterator.value()
            if item.checkState(0) == Qt.CheckState.Checked:
                path = item.data(0, PATH_ROLE)
                if path == COMPUTER_PATH:
                    from ..core.drives import list_drives

                    found += [drive.path for drive in list_drives(with_details=False)]
                else:
                    found += item.data(0, GROUP_ROLE) or ([path] if path else [])
            iterator += 1
        return list(dict.fromkeys(found))


class FolderPicker(QDialog):
    """Lo marcado es lo que cuenta: el campo solo se usa si no hay nada marcado. Con `patterns`, en él también se puede escribir un patrón con comodines; si no, tiene que ser una carpeta que exista."""

    def __init__(self, title: str, config, tabs: list[str], patterns: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.patterns = patterns
        recent = [item.get("path", "") for item in config.get("recent", []) if isinstance(item, dict)]
        favorite = [node["path"] for node in favorites_tree.walk(favorites_tree.clean(config.get("favorites"))[0]) if favorites_tree.kind(node) == "folder"]
        self.recent = self._places(recent)
        self.favorites = self._places(favorite)
        self.tabs = self._places(tabs)
        form = QFormLayout()
        form.addRow(tr("Reciente:"), self.recent)
        form.addRow(tr("Favoritos:"), self.favorites)
        form.addRow(tr("Pestañas:"), self.tabs)
        self.tree = CheckedFolderTree(True)  # también las ocultas y las del sistema: aquí se elige dónde buscar, no qué se ve en la lista
        self.tree.setFrameShape(QFrame.Shape.StyledPanel)
        self.tree.pathActivated.connect(self._row_chosen)
        self.tree.itemClicked.connect(lambda _item, _column: self.tree.stop_syncing())
        self.tree.currentItemChanged.connect(self._current_changed)
        self.field = QLineEdit()
        if patterns:
            self.field.setPlaceholderText(tr("Carpeta o patrón con comodines"))
        self.field.textEdited.connect(lambda _text: self.notice.hide())
        self.field.returnPressed.connect(self.submit)
        field_row = QHBoxLayout()
        field_row.addWidget(QLabel(tr("Carpeta:")))
        field_row.addWidget(self.field, 1)
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Aceptar"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.tree, 1)
        layout.addLayout(field_row)
        layout.addWidget(self.notice)
        layout.addWidget(self.buttons)
        self.resize(420, 520)
        self.chosen: list[str] = []

    def _places(self, paths: list[str]) -> QComboBox:
        box = QComboBox()
        box.addItem("", "")
        for path in dict.fromkeys(path for path in paths if path and not frequent.is_virtual(path)):
            box.addItem(path, path)
        box.setEnabled(box.count() > 1)
        box.activated.connect(lambda _row, b=box: self._place_chosen(b))
        return box

    def _place_chosen(self, box: QComboBox) -> None:
        path = box.currentData()
        for other in (self.recent, self.favorites, self.tabs):
            if other is not box:
                other.setCurrentIndex(0)
        if path:
            self.field.setText(path)
            self.notice.hide()
            self.tree.sync_to(path)

    def _row_chosen(self, path: str) -> None:
        self.field.setText("" if path == COMPUTER_PATH else path)
        self.notice.hide()

    def _current_changed(self, item, _previous) -> None:
        if item is not None and self.tree.hasFocus() and item.data(0, PATH_ROLE):  # con el teclado. Si no tiene el foco, es el árbol siguiendo a una de las listas
            self._row_chosen(item.data(0, PATH_ROLE))

    def values(self) -> list[str]:
        text = self.field.text().strip().strip('"')
        chosen = self.tree.checked_paths() or ([text] if text else [])
        return list(dict.fromkeys(os.path.normpath(path) if os.path.isdir(path) else path for path in chosen))

    def submit(self) -> None:
        values = self.values()
        wrong = [] if self.patterns else [path for path in values if not os.path.isdir(path)]
        if wrong:
            self.notice.setText(tr("Esa carpeta no existe: {path}", path=wrong[0]))
            self.notice.show()
            return
        self.chosen = values
        self.accept()

    def done(self, result: int) -> None:
        self.tree.dispose()
        super().done(result)

    @classmethod
    def ask(cls, title: str, config, tabs: list[str], patterns: bool = False, parent=None) -> list[str]:
        dialog = cls(title, config, tabs, patterns, parent)
        dialog.exec()
        chosen = dialog.chosen
        dialog.deleteLater()
        return chosen
