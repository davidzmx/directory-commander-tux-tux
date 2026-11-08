"""Páginas de las preferencias con la configuración de los menús de clic derecho."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import context_menu, windows_shell
from ..core.context_menu import SEPARATOR, SHELL_ITEMS, SHELL_ITEMS_LABEL
from ..core.i18n import tr

KEY_ROLE = Qt.ItemDataRole.UserRole
SEPARATOR_TEXT = "──────────"


class MenuEditor(QWidget):
    """Lista de un menú: la casilla lo muestra u oculta, y se ordena con los botones o arrastrando."""

    def __init__(self, title: str, which: str, layout_data: dict) -> None:
        super().__init__()
        self.which = which
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        buttons = QHBoxLayout()
        buttons.setSpacing(3)
        for label, tip, slot in (("▲", tr("Subir"), lambda: self._move(-1)), ("▼", tr("Bajar"), lambda: self._move(1)),
                                 (tr("+ Línea"), tr("Añadir una línea separadora"), self._add_separator),
                                 (tr("− Línea"), tr("Quitar la línea separadora seleccionada"), self._remove_separator),
                                 (tr("Restablecer"), tr("Volver al menú original"), self.reset)):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, s=slot: s())
            buttons.addWidget(button)
        buttons.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(QLabel(f"<b>{title}</b>"))
        column.addWidget(self.list, 1)
        column.addLayout(buttons)
        self._load(layout_data)

    def _load(self, layout_data: dict) -> None:
        self.list.clear()
        hidden = set(layout_data["hidden"])
        for key in layout_data["order"]:
            self._add_item(key, key not in hidden)

    def _add_item(self, key: str, shown: bool = True, row: int | None = None) -> None:
        item = QListWidgetItem(tr({SEPARATOR: SEPARATOR_TEXT, SHELL_ITEMS: SHELL_ITEMS_LABEL}.get(key, key)))
        item.setData(KEY_ROLE, key)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if key != SEPARATOR:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
        item.setFlags(flags)
        if row is None:
            self.list.addItem(item)
        else:
            self.list.insertItem(row, item)

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < self.list.count():
            item = self.list.takeItem(row)
            self.list.insertItem(target, item)
            self.list.setCurrentRow(target)

    def _add_separator(self) -> None:
        row = self.list.currentRow()
        self._add_item(SEPARATOR, row=row + 1 if row >= 0 else self.list.count())

    def _remove_separator(self) -> None:
        item = self.list.currentItem()
        if item is not None and item.data(KEY_ROLE) == SEPARATOR:
            self.list.takeItem(self.list.row(item))

    def reset(self) -> None:
        self._load(context_menu.normalize_layout(None, self.which))

    def values(self) -> dict:
        items = [self.list.item(row) for row in range(self.list.count())]
        return {"order": [item.data(KEY_ROLE) for item in items],
                "hidden": [item.data(KEY_ROLE) for item in items
                           if item.data(KEY_ROLE) != SEPARATOR and item.checkState() != Qt.CheckState.Checked]}


class ShellTreeWidgetItem(QTreeWidgetItem):
    """Elemento de la lista de opciones del sistema con compatibilidad para la API de QListWidgetItem."""

    def text(self, column: int = 0) -> str:
        return super().text(column)

    def setText(self, *args) -> None:
        if len(args) == 1:
            super().setText(0, args[0])
        else:
            super().setText(args[0], args[1])

    def checkState(self, column: int = 0) -> Qt.CheckState:
        return super().checkState(column)

    def setCheckState(self, *args) -> None:
        if len(args) == 1:
            super().setCheckState(0, args[0])
        else:
            super().setCheckState(args[0], args[1])

    def data(self, *args):
        if len(args) == 1:
            return super().data(0, args[0])
        return super().data(args[0], args[1])

    def setData(self, *args) -> None:
        if len(args) == 2:
            super().setData(0, args[0], args[1])
        else:
            super().setData(args[0], args[1], args[2])


class ShellTreeWidget(QTreeWidget):
    """Árbol/tabla de 2 columnas para opciones del sistema y su programa asociado."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderLabels([tr("Opción"), tr("Programa")])
        self.setRootIsDecorated(False)
        self.setSortingEnabled(True)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(1, 160)

    def count(self) -> int:
        return self.topLevelItemCount()

    def item(self, row: int) -> ShellTreeWidgetItem | None:
        return self.topLevelItem(row)  # type: ignore[return-value]

    def addItem(self, item: ShellTreeWidgetItem | QTreeWidgetItem | str) -> None:
        if isinstance(item, QTreeWidgetItem):
            self.addTopLevelItem(item)
        else:
            text = item.text() if hasattr(item, "text") else str(item)
            self.addTopLevelItem(ShellTreeWidgetItem([text, ""]))


class ContextMenuPages:
    """Crea las páginas y devuelve lo elegido. `sample_paths` son la carpeta abierta y lo seleccionado, para leer
    qué opciones de otros programas hay: leerlas carga sus extensiones, por eso se hace al abrir su página."""

    def __init__(self, config, sample_folder: str = "", sample_items: list[str] | None = None) -> None:
        settings = context_menu.normalize(config.get("context_menus"))
        self.sample_folder = sample_folder
        self.sample_items = sample_items or []
        self.shell_hidden = dict(settings["shell_hidden"])
        self.shell_loaded = False
        self.folder_editor = MenuEditor(tr("Al pulsar en un hueco de la carpeta"), context_menu.FOLDER, settings[context_menu.FOLDER])
        self.file_editor = MenuEditor(tr("Al pulsar en archivos o carpetas"), context_menu.FILE, settings[context_menu.FILE])
        menus = QWidget()
        row = QHBoxLayout(menus)
        row.addWidget(self.folder_editor)
        row.addWidget(self.file_editor)

        self.shell_items = QCheckBox(tr("Mostrar en los menús las opciones del sistema y de otros programas"))
        self.shell_items.setChecked(config.get("shell_menu_items", True))
        self.shell_list = ShellTreeWidget()
        self.shell_list.setEnabled(self.shell_items.isChecked())
        self.shell_items.toggled.connect(self.shell_list.setEnabled)
        hint = QLabel(tr("Desmarca lo que no quieras ver. La lista muestra lo que ofrece la carpeta abierta y lo que tengas seleccionado al abrir las preferencias."))
        hint.setWordWrap(True)
        self.shell_page = QWidget()
        column = QVBoxLayout(self.shell_page)
        column.addWidget(self.shell_items)
        column.addWidget(hint)
        column.addWidget(self.shell_list, 1)
        self.pages = [(tr("Menús contextuales"), menus)]
        if windows_shell.AVAILABLE:
            self.pages.append((tr("Otros programas"), self.shell_page))
        self._show_shell_options({})

    def _show_shell_options(self, found: dict[str, str | tuple[str, str]]) -> None:
        self.shell_list.clear()
        labels: dict[str, tuple[str, str]] = {}
        for key, val in found.items():
            if isinstance(val, tuple):
                labels[key] = val
            else:
                prog = windows_shell.detect_program(val, key)
                labels[key] = (val, prog)
        for key, label in self.shell_hidden.items():
            if key not in labels:
                prog = windows_shell.detect_program(label, key)
                labels[key] = (label, prog)

        for key, (label, prog) in sorted(labels.items(), key=lambda pair: pair[1][0].casefold()):
            item = ShellTreeWidgetItem([label, prog])
            item.setData(0, KEY_ROLE, key)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked if key in self.shell_hidden else Qt.CheckState.Checked)
            self.shell_list.addItem(item)
        self.shell_list.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def load_shell_options(self) -> None:
        """Lee las opciones instaladas, solo la primera vez que se abre la página."""
        if self.shell_loaded:
            return
        self.shell_loaded = True
        self.shell_hidden = self._current_hidden()
        found: dict[str, tuple[str, str]] = {}
        readers = []
        if self.sample_folder:
            readers.append(lambda: windows_shell.menu_for_folder(self.sample_folder))
            readers.append(lambda: windows_shell.menu_for_items([self.sample_folder]))
        if self.sample_items:
            readers.append(lambda: windows_shell.menu_for_items(self.sample_items))
        for read in readers:
            try:
                menu = read()
            except OSError:
                continue
            if menu is None:
                continue
            try:
                # «Abrir con» ya es una entrada de la página de menús: no se ofrece también aquí.
                for item in windows_shell.clean_items(menu.items(), hidden_keys=(windows_shell.OPEN_WITH_VERB,)):
                    if not item.separator:
                        prog = getattr(item, "program", "") or windows_shell.detect_program(item.label, item.verb)
                        found[windows_shell.shell_key(item)] = (item.label.replace("&", ""), prog)
            finally:
                menu.close()
        self._show_shell_options(found)

    def _current_hidden(self) -> dict[str, str]:
        hidden = dict(self.shell_hidden)
        for row in range(self.shell_list.count()):
            item = self.shell_list.item(row)
            if item is None:
                continue
            key = item.data(KEY_ROLE)
            if item.checkState() == Qt.CheckState.Checked:
                hidden.pop(key, None)
            else:
                hidden[key] = item.text()
        return hidden

    def values(self) -> dict:
        return {context_menu.FOLDER: self.folder_editor.values(), context_menu.FILE: self.file_editor.values(),
                "shell_hidden": self._current_hidden()}
