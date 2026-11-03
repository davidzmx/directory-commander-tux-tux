"""Página de las preferencias para crear y cambiar los estilos: qué paneles, árboles y terminales muestra cada uno."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from ..core import bar_buttons, styles
from ..core.i18n import N_, tr
from .style_preview import StylePreview

ENTRY_ROLE = Qt.ItemDataRole.UserRole
_PANES = (("Off", N_("Un panel")), ("Vertical", N_("Dos, uno junto al otro")), ("Horizontal", N_("Dos, uno encima del otro")),
          ("3-Vertical", N_("Tres, uno junto al otro")), ("3-Horizontal", N_("Tres, uno encima del otro")),
          ("3-Left", N_("Tres: uno a la izquierda y dos a la derecha")), ("3-Top", N_("Tres: dos arriba y uno abajo")),
          ("4-Vertical", N_("Cuatro, uno junto al otro")), ("4-Horizontal", N_("Cuatro, uno encima del otro")), ("4-Grid", N_("Cuatro, en cuadrícula")),
          ("6-Grid", N_("Seis, en cuadrícula de tres por dos")), ("8-Grid", N_("Ocho, en cuadrícula de cuatro por dos")))
_PANE_TERMINAL_LABELS = (N_("Terminal del primer panel"), N_("Terminal del segundo panel"), N_("Terminal del tercer panel"), N_("Terminal del cuarto panel"),
                         N_("Terminal del quinto panel"), N_("Terminal del sexto panel"), N_("Terminal del séptimo panel"), N_("Terminal del octavo panel"))
_TREES = (("none", N_("Sin árbol")), ("shared", N_("Un árbol")), ("each", N_("Un árbol por panel")))
_VIEWS = (("list", N_("Lista")), ("details", N_("Detalles")), ("smallicons", N_("Iconos pequeños")), ("largeicons", N_("Iconos grandes")),
          ("tiles", N_("Mosaico")), ("thumbnails", N_("Miniaturas")), ("filmstrip", N_("Tira de fotogramas")))
_POSITIONS = (("", N_("Automática: abajo o, con los paneles uno encima del otro, a la derecha")), ("right", N_("A la derecha de su panel")), ("below", N_("Debajo de su panel")))


def _combo(options) -> QComboBox:
    box = QComboBox()
    for value, label in options:
        box.addItem(tr(label), value)
    return box


class StylesPage(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        stored = config.get("styles")
        found = {style["name"]: style for style in (stored if isinstance(stored, list) else [])
                 if isinstance(style, dict) and isinstance(style.get("name"), str) and style["name"]}
        # Cada fila apunta a su estilo: se pueden reordenar arrastrándolas. Junto a él, cómo se llamaba, porque lo que lo nombra en otros sitios debe seguirlo.
        self._styles: dict[int, dict] = {}
        self._origins: dict[int, str | None] = {}
        self._next_entry = 0
        self._loading = False
        self.names = QListWidget()
        self.names.setMaximumWidth(250)
        self.names.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.names.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.name = QLineEdit()
        self.panes = _combo(_PANES)
        self.tree = _combo(_TREES)
        self.viewer = QCheckBox(tr("Visor"))
        self.meta = QCheckBox(tr("Metadatos"))
        self.view = _combo(_VIEWS)
        self.pane_terminals = tuple(QCheckBox(tr(label)) for label in _PANE_TERMINAL_LABELS)
        self.pane_only = tuple(QCheckBox(tr("Solo la terminal, sin archivos")) for _label in _PANE_TERMINAL_LABELS)
        self.terminal_general = QCheckBox(tr("Terminal general"))
        self.position = _combo(_POSITIONS)
        form = QFormLayout()
        form.addRow(tr("Nombre:"), self.name)
        form.addRow(tr("Paneles:"), self.panes)
        form.addRow(tr("Árbol de carpetas:"), self.tree)
        form.addRow(tr("Vista:"), self.view)
        form.addRow(tr("Paneles laterales:"), self.viewer)
        form.addRow("", self.meta)
        terminals = QVBoxLayout()  # en una sola fila del formulario: así las de los paneles que no hay se ocultan sin dejar hueco
        terminals.setContentsMargins(0, 0, 0, 0)
        self.pane_rows: list[QWidget] = []
        for check, only in zip(self.pane_terminals, self.pane_only):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(check)
            line.addWidget(only)
            line.addStretch(1)
            terminals.addWidget(row)
            self.pane_rows.append(row)
        terminals.addWidget(self.terminal_general)
        form.addRow(tr("Terminales:"), terminals)
        form.addRow(tr("Posición de la terminal del panel:"), self.position)
        self.add_button = self._button(tr("Nuevo"), self.add)
        self.duplicate_button = self._button(tr("Duplicar"), self.duplicate)
        self.remove_button = self._button(tr("Eliminar"), self.remove)
        self.restore_button = self._button(tr("Recuperar los estilos por defecto"), self.restore_defaults)
        self.restore_button.setToolTip(tr("Vuelve a añadir los estilos por defecto que hayas eliminado. No cambia los demás"))
        buttons = QHBoxLayout()
        for button in (self.add_button, self.duplicate_button, self.remove_button, self.restore_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        hint = QLabel(tr("Un estilo es una forma de colocar la ventana. Marca los que quieras ver en la barra de estilos y cambia su orden con ▲ y ▼ o arrastrándolos."))
        hint.setWordWrap(True)
        up = self._button("▲", lambda: self.move_current(-1))
        up.setToolTip(tr("Subir"))
        down = self._button("▼", lambda: self.move_current(1))
        down.setToolTip(tr("Bajar"))
        order_row = QHBoxLayout()
        order_row.addWidget(up)
        order_row.addWidget(down)
        order_row.addStretch(1)
        left = QVBoxLayout()
        left.addWidget(self.names, 1)
        left.addLayout(order_row)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addLayout(buttons)
        self.preview = StylePreview(config)
        right.addWidget(self.preview, 1)
        right.addWidget(hint)
        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addLayout(right, 1)
        hidden = bar_buttons.hidden(config, bar_buttons.STYLES)
        for name in bar_buttons.ordered(config, bar_buttons.STYLES, list(found)):
            self._add_row(json.loads(json.dumps(found[name])), name, name not in hidden)
        self.names.currentRowChanged.connect(self._show_row)
        self.name.textEdited.connect(self._edited)
        for box in (self.panes, self.tree, self.view, self.position):
            box.currentIndexChanged.connect(self._edited)
        for check in (self.viewer, self.meta, *self.pane_terminals, *self.pane_only, self.terminal_general):
            check.toggled.connect(self._edited)
        current = next((row for row in range(self.names.count()) if self._style_at(row)["name"] == config.get("style")), 0)
        self.names.setCurrentRow(current)
        self._show_row(current)

    @staticmethod
    def _button(text: str, action) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda _=False: action())
        return button

    def _add_row(self, style: dict, origin: str | None, shown: bool = True) -> None:
        entry = self._next_entry
        self._next_entry += 1
        self._styles[entry] = style
        self._origins[entry] = origin
        item = QListWidgetItem(tr(style["name"]))
        item.setData(ENTRY_ROLE, entry)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
        self.names.addItem(item)

    def _style_at(self, row: int) -> dict | None:
        item = self.names.item(row)
        return self._styles[item.data(ENTRY_ROLE)] if item is not None else None

    def _all_styles(self) -> list[dict]:
        return [self._style_at(row) for row in range(self.names.count())]

    def move_current(self, delta: int) -> None:
        row, target = self.names.currentRow(), self.names.currentRow() + delta
        if row >= 0 and 0 <= target < self.names.count():
            self.names.insertItem(target, self.names.takeItem(row))
            self.names.setCurrentRow(target)

    def _show_row(self, row: int) -> None:
        self._loading = True
        style = self._style_at(row)
        if style is not None:
            self.name.setText(tr(style["name"]))
            layout = style.get("dual", "Vertical")
            if self.panes.findData(layout) < 0 and styles.valid_layout(layout):
                self.panes.addItem(tr("Disposición propia"), layout)  # guardada tras dividir paneles: no debe perderse al editar el estilo
            self.panes.setCurrentIndex(max(0, self.panes.findData(layout)))
            tree = "none" if not style.get("tree", True) else "each" if style.get("double_tree") else "shared"
            self.tree.setCurrentIndex(self.tree.findData(tree))
            self.view.setCurrentIndex(max(0, self.view.findData(style.get("view", "list"))))
            self.viewer.setChecked(bool(style.get("viewer", False)))
            self.meta.setChecked(bool(style.get("meta", False)))
            wanted = styles.terminals_of(style) or set()
            for name, check in zip(styles.TERMINALS, (*self.pane_terminals, self.terminal_general)):
                check.setChecked(name in wanted)
            only = styles.terminal_panes_of(style) or set()
            for name, check in zip(styles.PANE_TERMINALS, self.pane_only):
                check.setChecked(name in only)
            self.position.setCurrentIndex(max(0, self.position.findData(styles.position_of(style) or "")))
            self.preview.set_style(style)
        self._loading = False
        self._update_enabled()

    def _update_enabled(self) -> None:
        self.remove_button.setEnabled(self.names.count() > 1)
        count = styles.pane_count(self.panes.currentData())
        for index, row in enumerate(self.pane_rows):
            row.setVisible(index < count)
        # Siempre debe quedar un panel con archivos: con uno solo, ni se ofrece.
        full = sum(check.isChecked() for check in self.pane_only[:count]) >= count - 1
        for check in self.pane_only:
            check.setEnabled(count > 1 and (check.isChecked() or not full))
        self.position.setEnabled(any(check.isChecked() for check in self.pane_terminals[:count]))

    def _edited(self, *_args) -> None:
        row = self.names.currentRow()
        style = self._style_at(row)
        if self._loading or style is None:
            return
        text = self.name.text().strip()
        if text and text != tr(style["name"]):
            style["name"] = text  # sin cambios se queda con su nombre original, que es el que se traduce
        self.names.item(row).setText(tr(style["name"]))
        style["dual"] = self.panes.currentData()
        style["tree"] = self.tree.currentData() != "none"
        if self.tree.currentData() == "each":
            style["double_tree"] = True
        else:
            style.pop("double_tree", None)
        style["view"] = self.view.currentData()
        style["viewer"] = self.viewer.isChecked()
        style["meta"] = self.meta.isChecked()
        count = styles.pane_count(style["dual"])
        wanted = [name for index, (name, check) in enumerate(zip(styles.TERMINALS, (*self.pane_terminals, self.terminal_general)))
                  if check.isChecked() and (name == styles.GENERAL or index < count)]
        if wanted or "terminals" in style:  # sin marcar ninguna y sin haberlas tenido, no cierra las que se abran a mano
            style["terminals"] = wanted
        only = [name for index, (name, check) in enumerate(zip(styles.PANE_TERMINALS, self.pane_only)) if check.isChecked() and index < count]
        if only or "terminal_panes" in style:
            style["terminal_panes"] = only
        if self.position.currentData():
            style["terminal_position"] = self.position.currentData()
        else:
            style.pop("terminal_position", None)
        self.preview.set_style(style)
        self._update_enabled()

    def _append(self, style: dict, origin: str | None = None) -> None:
        style["name"] = styles.unique_name(style["name"], (item["name"] for item in self._all_styles()))
        self._add_row(style, origin)

    def add(self) -> None:
        self._append({"name": tr("Estilo nuevo"), "dual": "Off", "tree": True, "viewer": False, "meta": False, "view": "list", "terminals": []})
        self.names.setCurrentRow(self.names.count() - 1)
        self.name.setFocus()
        self.name.selectAll()

    def duplicate(self) -> None:
        style = self._style_at(self.names.currentRow())
        if style is not None:
            copy = json.loads(json.dumps(style))
            copy["name"] = tr(copy["name"])
            self._append(copy)
            self.names.setCurrentRow(self.names.count() - 1)

    def remove(self) -> None:
        row = self.names.currentRow()
        if self.names.count() > 1 and row >= 0:
            self.names.takeItem(row)
            self._update_enabled()

    def restore_defaults(self) -> None:
        for style in styles.missing_defaults(self._all_styles()):
            self._append(style, style["name"])

    def apply_to(self, config) -> None:
        """El orden de la lista es el de la barra. Lo que ocultaban y ordenaban las versiones anteriores ya está en ella al abrirse."""
        if not self.names.count():
            return  # una configuración sin estilos válidos se queda como estaba
        names: list[str] = []
        hidden: list[str] = []
        renames: dict[str, str] = {}
        result = []
        for row in range(self.names.count()):
            item = self.names.item(row)
            style = json.loads(json.dumps(self._styles[item.data(ENTRY_ROLE)]))
            origin = self._origins[item.data(ENTRY_ROLE)]
            style["name"] = styles.unique_name(style["name"], names)
            names.append(style["name"])
            result.append(style)
            if item.checkState() != Qt.CheckState.Checked:
                hidden.append(style["name"])
            if origin and origin != style["name"]:
                renames[origin] = style["name"]
        config["styles"] = result
        styles.rename_references(config, renames)
        if config.get("style") not in names:
            config["style"] = names[0]
        order = config.get("bar_order")
        if isinstance(order, dict):
            order.pop(bar_buttons.STYLES, None)
        shown = config.get("bar_buttons")
        if not isinstance(shown, dict):
            shown = config["bar_buttons"] = {}
        shown[bar_buttons.STYLES] = hidden
        if not hidden:
            del shown[bar_buttons.STYLES]

    def show_style(self, name: str) -> None:
        for row, style in enumerate(self._all_styles()):
            if style["name"] == name:
                self.names.setCurrentRow(row)
