"""La ventana del renombrado masivo: las reglas a la izquierda, en dos pestañas, y a la derecha los archivos con su nombre nuevo.
Nada se toca en el disco hasta pulsar «Renombrar»."""
from __future__ import annotations

import datetime
import os

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTabWidget, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import batch_rename, rename
from ..core.batch_rename import BatchRule
from ..core.i18n import N_, tr
from .icons import themed

PREVIEW_DELAY_MS = 150
PANEL_WIDTH = 300
_PLACES_NAME = (("replace", N_("Reemplazar")), ("before", N_("Antes del nombre antiguo")), ("after", N_("Después del nombre antiguo")))
_PLACES_EXTENSION = (("replace", N_("Reemplazar")), ("before", N_("Antes de la extensión antigua")), ("after", N_("Después de la extensión antigua")))
_CASES = (("lower", N_("minúsculas")), ("upper", N_("MAYÚSCULAS")), ("sentence", N_("Formato oración")), ("title", N_("Formato Título")))
_APPLY_TO = (("name", N_("Nombre")), ("extension", N_("Extensión")), ("both", N_("Nombre y extensión")))
OLD_ROLE = Qt.ItemDataRole.UserRole


def _combo(options) -> QComboBox:
    box = QComboBox()
    for value, label in options:
        box.addItem(tr(label), value)
    return box


def _editable() -> QComboBox:
    """Un texto que recuerda lo escrito antes en esta ventana, como en FileMenu Tools."""
    box = QComboBox()
    box.setEditable(True)
    box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    return box


def _text_of(widget) -> str:
    return widget.currentText() if isinstance(widget, QComboBox) else widget.text()


def _set_text(widget, text: str) -> None:
    if isinstance(widget, QComboBox):
        widget.setEditText(text)
    else:
        widget.setText(text)


class BatchRenameDialog(QDialog):
    renamed = Signal(list)  # [(nombre antiguo, nombre nuevo)] de cada vez que se renombra

    def __init__(self, directory: str, names: list[str], others: set[str], profiles: dict, image_size=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Renombrado masivo"))
        self.resize(1200, 560)
        self.directory = directory
        self.others = others  # los demás archivos de la carpeta: con ellos no puede chocar ningún nombre nuevo
        self.profiles = profiles
        self.image_size = image_size
        self._details: dict[str, batch_rename.FileDetails] = {}
        self._loading = False
        self.new_names: list[str] = []
        self.problems: dict[int, str] = {}
        self._timer = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self.update_preview)

        tabs = QTabWidget()
        tabs.addTab(self._basic_page(), tr("Opciones básicas"))
        tabs.addTab(self._advanced_page(), tr("Opciones avanzadas"))
        tabs.setUsesScrollButtons(False)  # las dos siempre a la vista: si no caben, se recorta su texto
        tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs = tabs
        left = QVBoxLayout()
        left.addWidget(tabs, 1)
        left.addWidget(self._profiles_box())
        panel = QWidget()
        panel.setLayout(left)
        panel.setFixedWidth(PANEL_WIDTH)

        self.list = QTreeWidget()
        self.list.setColumnCount(3)
        self.list.setHeaderLabels([tr("Nombre antiguo"), tr("Nombre nuevo"), tr("Fecha de modificación")])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)  # el orden es el de la secuencia
        self.list.setUniformRowHeights(True)
        header = self.list.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 370)
        header.resizeSection(1, 380)
        self.list.model().rowsMoved.connect(lambda *_args: self._schedule())
        for name in names:
            self._add_row(name)
        QShortcut(QKeySequence.StandardKey.Delete, self.list, self.remove_selected)

        self.folder = QLabel(self.directory + os.sep)
        self.folder.setObjectName("batchRenameFolder")
        self.folder.setStyleSheet("color: palette(link); border-bottom: 1px solid palette(mid); padding: 2px 4px;")
        self.message = QLabel()
        self.message.setWordWrap(True)
        tools = QHBoxLayout()
        for icon, tip, action in (("goup", tr("Subir"), lambda: self.move_selected(-1)), ("godown", tr("Bajar"), lambda: self.move_selected(1)),
                                  ("movetop", tr("Al principio"), lambda: self.move_selected(-len(self))),
                                  ("movebottom", tr("Al final"), lambda: self.move_selected(len(self)))):
            button = QToolButton()
            button.setIcon(themed(icon))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=action: a())
            tools.addWidget(button)
        tools.addStretch(1)
        self.refresh_button = QPushButton(themed("refresh"), tr("&Actualizar"))
        self.refresh_button.setToolTip(tr("Vuelve a leer las fechas y los datos de los archivos y recalcula los nombres nuevos"))
        self.refresh_button.clicked.connect(self.refresh)
        self.rename_button = QPushButton(themed("rename"), tr("Renombrar"))
        self.rename_button.setDefault(True)
        self.rename_button.clicked.connect(self.rename_all)
        tools.addWidget(self.refresh_button)
        tools.addWidget(self.rename_button)
        right = QVBoxLayout()
        right.addLayout(tools)
        right.addWidget(self.folder)
        right.addWidget(self.list, 1)
        right.addWidget(self.message)

        layout = QHBoxLayout(self)
        layout.addWidget(panel)
        layout.addLayout(right, 1)
        self._connect_all(self)
        self._show_enabled()
        self.update_preview()

    def __len__(self) -> int:
        return self.list.topLevelItemCount()

    # ---------------------------------------------------------------- pestañas
    def _text_row(self, widget, menu_builder) -> QHBoxLayout:
        """Un texto con su botón ►, que inserta en él variables o patrones donde está el cursor."""
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addWidget(widget, 1)
        button = QToolButton()
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setToolTip(tr("Insertar…"))
        button.clicked.connect(lambda: self._insert_menu(widget, menu_builder, button))
        row.addWidget(button)
        widget.insert_button = button
        return row

    def _part_box(self, title: str, places) -> tuple[QGroupBox, dict]:
        box = QGroupBox(title)
        box.setCheckable(True)
        box.setChecked(False)
        parts = {"text": QLineEdit(), "place": _combo(places), "sequence_on": QCheckBox(tr("Añadir secuencia:")),
                 "sequence": QComboBox(), "case_on": QCheckBox(tr("Convertir a:")), "case": _combo(_CASES)}
        parts["sequence"].setEditable(True)
        parts["sequence"].addItems(batch_rename.SEQUENCES)
        parts["sequence"].setEditText("000")
        parts["sequence"].setToolTip(tr("El primer elemento: 001 sigue con 002, 003…; 0A con 0B, 0C…"))
        form = QFormLayout(box)
        form.addRow(tr("Cadena:"), self._text_row(parts["text"], self._variables_menu))
        form.addRow(tr("Situación:"), parts["place"])
        form.addRow(parts["sequence_on"])
        form.addRow(parts["sequence"])
        form.addRow(parts["case_on"])
        form.addRow(parts["case"])
        return box, parts

    def _basic_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.name_box, self.name = self._part_box(tr("Cambiar el nombre."), _PLACES_NAME)
        self.extension_box, self.extension = self._part_box(tr("Cambiar la extensión."), _PLACES_EXTENSION)
        self.extension["place"].setCurrentIndex(self.extension["place"].findData("after"))
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setToolTip(tr("Cómo quedará el primer archivo seleccionado de la lista"))
        preview_row = QFormLayout()
        preview_row.addRow(tr("Vista previa:"), self.preview)
        layout.addWidget(self.name_box)
        layout.addWidget(self.extension_box)
        layout.addLayout(preview_row)
        layout.addStretch(1)
        return page

    def _advanced_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.insert_box = QGroupBox(tr("Insertar."))
        self.insert_box.setCheckable(True)
        self.insert_box.setChecked(False)
        self.insert_text = _editable()
        self.insert_at_position = QRadioButton(tr("En la posición:"))
        self.insert_at_position.setChecked(True)
        self.insert_at_before = QRadioButton(tr("Antes de la cadena:"))
        self.insert_at_after = QRadioButton(tr("Después de la cadena:"))
        self.insert_at_extension = QRadioButton(tr("Antes de la extensión"))
        self.insert_position = QSpinBox(maximum=10_000)
        self.insert_before = QLineEdit()
        self.insert_after = QLineEdit()
        self._insert_places = QButtonGroup(self)
        for button in (self.insert_at_position, self.insert_at_before, self.insert_at_after, self.insert_at_extension):
            self._insert_places.addButton(button)
        # En cuadrícula, como en FileMenu Tools: cada opción a la izquierda y su dato a la derecha.
        grid = QGridLayout(self.insert_box)
        text_row = self._text_row(self.insert_text, self._variables_menu)
        text_row.insertWidget(0, QLabel(tr("Cadena:")))  # su texto, a todo lo ancho, como en FileMenu Tools
        grid.addLayout(text_row, 0, 0, 1, 2)
        for row, (button, field) in enumerate(((self.insert_at_position, self.insert_position), (self.insert_at_before, self.insert_before),
                                              (self.insert_at_after, self.insert_after), (self.insert_at_extension, None)), 1):
            grid.addWidget(button, row, 0, 1, 1 if field else 2)
            if field is not None:
                grid.addWidget(field, row, 1)
        grid.setColumnStretch(1, 1)

        self.replace_box = QGroupBox(tr("Reemplazar."))
        self.replace_box.setCheckable(True)
        self.replace_box.setChecked(False)
        self.replace_by_range = QRadioButton(tr("Desde la posición:"))
        self.replace_by_range.setChecked(True)
        self.replace_by_search = QRadioButton(tr("Buscar la cadena:"))
        self._replace_kinds = QButtonGroup(self)
        self._replace_kinds.addButton(self.replace_by_range)
        self._replace_kinds.addButton(self.replace_by_search)
        self.replace_from = QSpinBox(maximum=10_000)
        self.replace_to = QSpinBox(maximum=10_000)
        self.replace_to.setToolTip(tr("Se quita lo que hay desde la primera posición hasta esta, sin incluirla, y en su lugar va «Cambiar por»"))
        self.replace_search = _editable()
        self.replace_regex = QCheckBox(tr("Utilizar expresiones regulares."))
        self.replace_with = _editable()
        grid = QGridLayout(self.replace_box)
        until = QLabel(tr("Hasta:"))
        until.setIndent(20)
        grid.addWidget(self.replace_by_range, 0, 0)
        grid.addWidget(self.replace_from, 0, 1)
        grid.addWidget(until, 1, 0)
        grid.addWidget(self.replace_to, 1, 1)
        grid.addWidget(self.replace_by_search, 2, 0, 1, 2)
        grid.addLayout(self._text_row(self.replace_search, self._regex_menu), 3, 0, 1, 2)
        grid.addWidget(self.replace_regex, 4, 0, 1, 2)
        grid.addWidget(QLabel(tr("Cambiar por:")), 5, 0, 1, 2)
        grid.addLayout(self._text_row(self.replace_with, self._replacement_menu), 6, 0, 1, 2)
        grid.setColumnStretch(1, 1)

        self.apply_to = _combo(_APPLY_TO)
        self.case_sensitive = QCheckBox(tr("Distinguir entre Mayúsculas/Minúsculas."))
        bottom = QGridLayout()
        bottom.addWidget(QLabel(tr("Aplicar a:")), 0, 0)
        bottom.addWidget(self.apply_to, 0, 1)
        bottom.addWidget(self.case_sensitive, 1, 0, 1, 2)
        bottom.setColumnStretch(1, 1)
        layout.addWidget(self.insert_box)
        layout.addWidget(self.replace_box)
        layout.addLayout(bottom)
        layout.addStretch(1)
        return page

    def _profiles_box(self) -> QGroupBox:
        box = QGroupBox(tr("Perfiles"))
        row = QHBoxLayout(box)
        row.setSpacing(2)
        self.profile_box = QComboBox()
        self.profile_box.addItems(sorted(self.profiles))
        self.profile_box.setCurrentIndex(-1)
        row.addWidget(self.profile_box, 1)
        for icon, tip, action in (("folder", tr("Cargar perfil seleccionado"), self._load_selected_profile),
                                  ("save", tr("Guardar configuración en un perfil"), self.save_profile),
                                  ("close", tr("Eliminar perfil seleccionado"), self.delete_profile)):
            button = QToolButton()
            button.setIcon(themed(icon))
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=action: a())
            row.addWidget(button)
        return box

    # ---------------------------------------------------------------- menús ►
    def _insert_menu(self, widget, builder, button) -> None:
        menu = QMenu(self)
        builder(menu, lambda text: self._insert_text(widget, text))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    @staticmethod
    def _insert_text(widget, text: str) -> None:
        line = widget.lineEdit() if isinstance(widget, QComboBox) else widget
        line.insert(text)
        line.setFocus()

    @staticmethod
    def _variables_menu(menu: QMenu, insert) -> None:
        for title, variables in batch_rename.VARIABLE_GROUPS:
            group = menu.addMenu(tr(title))
            for code, label in variables:
                group.addAction(f"{tr(label)}\t{code}", lambda c=code: insert(c))

    @staticmethod
    def _regex_menu(menu: QMenu, insert) -> None:
        for code, label in batch_rename.REGEX_HELP:
            menu.addAction(f"{tr(label)}\t{code}", lambda c=code: insert(c))

    def _replacement_menu(self, menu: QMenu, insert) -> None:
        for code, label in batch_rename.MATCH_HELP:
            menu.addAction(f"{tr(label)}\t{code}", lambda c=code: insert(c))
        menu.addSeparator()
        self._variables_menu(menu, insert)

    # ---------------------------------------------------------------- reglas
    def rule(self) -> BatchRule:
        rule = BatchRule()
        for prefix, box, parts in (("name", self.name_box, self.name), ("extension", self.extension_box, self.extension)):
            setattr(rule, f"{prefix}_on", box.isChecked())
            setattr(rule, f"{prefix}_text", parts["text"].text())
            setattr(rule, f"{prefix}_place", parts["place"].currentData())
            setattr(rule, f"{prefix}_sequence_on", parts["sequence_on"].isChecked())
            setattr(rule, f"{prefix}_sequence", parts["sequence"].currentText().strip())
            setattr(rule, f"{prefix}_case_on", parts["case_on"].isChecked())
            setattr(rule, f"{prefix}_case", parts["case"].currentData())
        rule.insert_on = self.insert_box.isChecked()
        rule.insert_text = self.insert_text.currentText()
        rule.insert_place = next(place for place, button in (("position", self.insert_at_position), ("before", self.insert_at_before),
                                                             ("after", self.insert_at_after), ("before_extension", self.insert_at_extension)) if button.isChecked())
        rule.insert_position = self.insert_position.value()
        rule.insert_before = self.insert_before.text()
        rule.insert_after = self.insert_after.text()
        rule.replace_on = self.replace_box.isChecked()
        rule.replace_kind = "range" if self.replace_by_range.isChecked() else "search"
        rule.replace_from, rule.replace_to = self.replace_from.value(), self.replace_to.value()
        rule.replace_search = self.replace_search.currentText()
        rule.replace_regex = self.replace_regex.isChecked()
        rule.replace_with = self.replace_with.currentText()
        rule.apply_to = self.apply_to.currentData()
        rule.case_sensitive = self.case_sensitive.isChecked()
        return rule

    def set_rule(self, rule: BatchRule) -> None:
        self._loading = True
        for prefix, box, parts in (("name", self.name_box, self.name), ("extension", self.extension_box, self.extension)):
            box.setChecked(getattr(rule, f"{prefix}_on"))
            parts["text"].setText(getattr(rule, f"{prefix}_text"))
            parts["place"].setCurrentIndex(max(0, parts["place"].findData(getattr(rule, f"{prefix}_place"))))
            parts["sequence_on"].setChecked(getattr(rule, f"{prefix}_sequence_on"))
            parts["sequence"].setEditText(getattr(rule, f"{prefix}_sequence"))
            parts["case_on"].setChecked(getattr(rule, f"{prefix}_case_on"))
            parts["case"].setCurrentIndex(max(0, parts["case"].findData(getattr(rule, f"{prefix}_case"))))
        self.insert_box.setChecked(rule.insert_on)
        _set_text(self.insert_text, rule.insert_text)
        {"position": self.insert_at_position, "before": self.insert_at_before, "before_extension": self.insert_at_extension}.get(
            rule.insert_place, self.insert_at_after).setChecked(True)
        self.insert_position.setValue(rule.insert_position)
        self.insert_before.setText(rule.insert_before)
        self.insert_after.setText(rule.insert_after)
        self.replace_box.setChecked(rule.replace_on)
        (self.replace_by_range if rule.replace_kind == "range" else self.replace_by_search).setChecked(True)
        self.replace_from.setValue(rule.replace_from)
        self.replace_to.setValue(rule.replace_to)
        _set_text(self.replace_search, rule.replace_search)
        self.replace_regex.setChecked(rule.replace_regex)
        _set_text(self.replace_with, rule.replace_with)
        self.apply_to.setCurrentIndex(max(0, self.apply_to.findData(rule.apply_to)))
        self.case_sensitive.setChecked(rule.case_sensitive)
        self._loading = False
        self._show_enabled()
        self.update_preview()

    def _connect_all(self, root) -> None:
        for widget in root.findChildren(QWidget):
            if isinstance(widget, QLineEdit) and widget is not self.preview:
                widget.textChanged.connect(self._schedule)
            elif isinstance(widget, QComboBox) and widget is not self.profile_box:
                widget.currentIndexChanged.connect(self._schedule)
                widget.editTextChanged.connect(self._schedule)
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widget.toggled.connect(self._schedule)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._schedule)
            elif isinstance(widget, QGroupBox) and widget.isCheckable():
                widget.toggled.connect(self._schedule)

    def _schedule(self, *_args) -> None:
        if not self._loading:
            self._show_enabled()
            self._timer.start()

    def _show_enabled(self) -> None:
        """Como en FileMenu Tools: cada dato, activo solo si su opción lo está."""
        for parts in (self.name, self.extension):
            parts["sequence"].setEnabled(parts["sequence_on"].isChecked())
            parts["case"].setEnabled(parts["case_on"].isChecked())
        self.insert_position.setEnabled(self.insert_at_position.isChecked())
        self.insert_before.setEnabled(self.insert_at_before.isChecked())
        self.insert_after.setEnabled(self.insert_at_after.isChecked())
        by_range = self.replace_by_range.isChecked()
        for widget in (self.replace_from, self.replace_to):
            widget.setEnabled(by_range)
        for widget in (self.replace_search, self.replace_search.insert_button, self.replace_regex):
            widget.setEnabled(not by_range)

    # ---------------------------------------------------------------- lista
    def _add_row(self, name: str) -> None:
        path = os.path.join(self.directory, name)
        try:
            modified = datetime.datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%d/%m/%Y %H:%M:%S")
        except OSError:
            modified = ""
        item = QTreeWidgetItem([name, name, modified])
        item.setData(0, OLD_ROLE, name)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)  # sin poder soltar dentro de otro
        self.list.addTopLevelItem(item)

    @property
    def names(self) -> list[str]:
        return [self.list.topLevelItem(row).data(0, OLD_ROLE) for row in range(len(self))]

    def move_selected(self, steps: int) -> None:
        rows = sorted(self.list.indexOfTopLevelItem(item) for item in self.list.selectedItems())
        if not rows:
            return
        items = [self.list.takeTopLevelItem(row - taken) for taken, row in enumerate(rows)]
        target = max(0, min(len(self), rows[0] + steps))
        for offset, item in enumerate(items):
            self.list.insertTopLevelItem(target + offset, item)
            item.setSelected(True)
        self.list.scrollToItem(items[0])
        self.update_preview()

    def remove_selected(self) -> None:
        """Como Supr en FileMenu Tools: ese archivo no se renombra."""
        for item in self.list.selectedItems():
            self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        self.update_preview()

    def refresh(self) -> None:
        self._details.clear()
        names = self.names
        self.list.clear()
        for name in names:
            if os.path.lexists(os.path.join(self.directory, name)):
                self._add_row(name)
        self.update_preview()

    def update_preview(self) -> None:
        self._timer.stop()
        self._current_rule = rule = self.rule()
        names = self.names
        try:
            self.new_names = batch_rename.new_names(names, rule, self._details_for(rule, names))
        except ValueError as exc:
            self.new_names = []
            self.problems = {}
            self.message.setText(f"⚠ {exc}")
            self.rename_button.setEnabled(False)
            return
        self.problems = rename.find_problems(names, self.new_names, self.others)
        normal = self.palette().text().color()
        for row, new in enumerate(self.new_names):
            item = self.list.topLevelItem(row)
            item.setText(0, names[row])
            item.setText(1, new)
            item.setForeground(1, QColor("#d04040") if row in self.problems else normal)
            item.setToolTip(1, self.problems.get(row, ""))
        changed = sum(1 for old, new in zip(names, self.new_names) if old != new)
        if self.problems:
            self.message.setText(tr("⚠ {count} con problemas: {value}", count=len(self.problems), value=next(iter(self.problems.values()))))
        else:
            self.message.setText(tr("Se renombrarán {changed} de {count} elementos", changed=changed, count=len(names)))
        self.rename_button.setEnabled(changed > 0)
        first = self.list.selectedItems()[0] if self.list.selectedItems() else self.list.topLevelItem(0)
        self.preview.setText(first.text(1) if first is not None else "")

    def _details_for(self, rule: BatchRule, names: list[str]):
        """Solo si las reglas usan variables: las de fecha piden la del archivo, y las demás, abrirlo. Lo leído se recuerda hasta «Actualizar»,
        pero con otras reglas puede hacer falta más: la fecha de captura o las etiquetas."""
        if not any(batch_rename.has_variables(text) for text in (rule.name_text, rule.extension_text, rule.insert_text, rule.replace_with)):
            return None
        wanted = tuple(rule.uses(pattern) for pattern in (r"%#", r"%(WIDTH|HEIGHT)%", r"%MP3"))
        if wanted != getattr(self, "_details_kinds", None):
            self._details.clear()
            self._details_kinds = wanted

        def details(index: int) -> batch_rename.FileDetails:
            name = names[index]
            if name not in self._details:
                self._details[name] = batch_rename.read_details(os.path.join(self.directory, name), rule, self.image_size)
            return self._details[name]

        return details

    # ---------------------------------------------------------------- renombrar
    def rename_all(self) -> None:
        self.update_preview()
        names = self.names
        duplicated = [index for index, reason in self.problems.items() if reason == tr("Nombre duplicado en el lote")]
        if duplicated:
            QMessageBox.warning(self, tr("Renombrado masivo"), tr("Algunos archivos serán renombrados con el mismo nombre. Cambia las reglas de "
                                                                  "renombrado para evitar que haya archivos con el mismo nombre."))
            return
        if self.problems:
            row, reason = next(iter(self.problems.items()))
            QMessageBox.warning(self, tr("Renombrado masivo"), f"{self.new_names[row]}\n\n{reason}")
            return
        errors = rename.apply_renames(self.directory, names, self.new_names)
        failed = {name for name, _message in errors}
        pairs = [(old, new) for old, new in zip(names, self.new_names) if old != new and old not in failed]
        if pairs:
            self.renamed.emit(pairs)
        if errors:
            QMessageBox.warning(self, tr("Renombrado masivo"), "\n".join(tr("Ha ocurrido un error al renombrar «{old}»: {message}", old=old, message=message)
                                                                        for old, message in errors[:10]))
        done = dict(pairs)
        for row in range(len(self)):
            item = self.list.topLevelItem(row)
            old = item.data(0, OLD_ROLE)
            if old in done:
                item.setData(0, OLD_ROLE, done[old])
        self._details.clear()
        if not errors:
            answer = QMessageBox.question(self, tr("Renombrado masivo"),
                                          tr("Todos los archivos se han renombrado. ¿Quieres seguir renombrando los mismos archivos?"))
            if answer != QMessageBox.StandardButton.Yes:
                self.accept()
                return
        self.update_preview()

    # ---------------------------------------------------------------- perfiles
    def load_profile(self, name: str) -> bool:
        if name not in self.profiles:
            return False
        self.profile_box.setCurrentText(name)
        self.set_rule(BatchRule.from_dict(self.profiles[name]))
        return True

    def _load_selected_profile(self) -> None:
        if self.profile_box.currentText():
            self.load_profile(self.profile_box.currentText())

    def save_profile(self, name: str | None = None) -> None:
        if name is None:
            name, accepted = QInputDialog.getText(self, tr("Guardar perfil"), tr("Introduce el nombre del perfil:"), text=self.profile_box.currentText())
            if not accepted:
                return
        name = name.strip()
        if not name:
            return
        if name in self.profiles and QMessageBox.question(self, tr("Guardar perfil"), tr("Ya existe un perfil llamado «{name}». ¿Quieres reemplazarlo?",
                                                                                         name=name)) != QMessageBox.StandardButton.Yes:
            return
        if name not in self.profiles:
            self.profile_box.addItem(name)
        self.profiles[name] = self.rule().to_dict()
        self.profile_box.setCurrentText(name)

    def delete_profile(self) -> None:
        name = self.profile_box.currentText()
        if name not in self.profiles:
            return
        if QMessageBox.question(self, tr("Perfiles"), tr("¿Seguro que quieres eliminar el perfil «{name}»?", name=name)) != QMessageBox.StandardButton.Yes:
            return
        del self.profiles[name]
        self.profile_box.removeItem(self.profile_box.findText(name))
        self.profile_box.setCurrentIndex(-1)
