"""El panel de buscar archivos. Los resultados no van aquí: se abren en un panel, como una carpeta más."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QByteArray, QDate, QPoint, QRect, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit,
                               QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTimeEdit, QToolButton, QVBoxLayout, QWidget)

from ..core import file_search
from ..core.i18n import N_, tr
from ..core.listing import COMPUTER_PATH, CONTENT_TYPES, get_results, is_results, results_path, set_results, add_results
from .background import run_bg
from .folder_picker import FolderPicker

HISTORY_SIZE = 20
FLOW_SPACING = 12
SECTION_SPACING = 10
HEADING_SPACING = 5
WINDOW_MARGIN = 14
WINDOW_WIDTH = 1200
ONE_COLUMN_WIDTH = 760
WINDOW_EXTRA_WIDTH = 30
WINDOW_EXTRA_HEIGHT = 40  # la fila de arriba y los márgenes. Si se abre algún filtro, la ventana crece
FRAME_MARGIN = 60  # la barra de título y algo de margen con el borde de la pantalla
ROOM_CHECK_MS = 40  # lo que tarda en colocarse de nuevo lo que ha cambiado de tamaño
MENU_MARK = " ▾"
UNIT_NAMES = tuple(file_search.UNITS)
RESULT_TARGETS = (("source_new", N_("Panel activo (pestaña nueva)")), ("source", N_("Panel activo")), ("dest_new", N_("Panel de destino (pestaña nueva)")), ("dest", N_("Panel de destino")))
DATE_OPS = (("ignore", N_("ignorar")), ("on", N_("el día")), ("before", N_("antes de")), ("after", N_("después de")), ("between", N_("entre")), ("not_between", N_("fuera de")), ("within", N_("en los últimos")))
TIME_OPS = (("ignore", N_("ignorar")), ("at", N_("a las")), ("before", N_("antes de")), ("after", N_("después de")), ("between", N_("entre")), ("not_between", N_("fuera de")),
            ("around_1", N_("± una hora")), ("around_6", N_("± seis horas")))
SIZE_OPS = (("ignore", N_("ignorar")), ("equals", N_("igual a")), ("smaller", N_("menor que")), ("greater", N_("mayor que")), ("between", N_("entre")), ("not_between", N_("fuera de")),
            ("around_25", N_("± 25 %")), ("around_50", N_("± 50 %")))
PERIOD_NAMES = (("minutes", N_("minutos")), ("hours", N_("horas")), ("days", N_("días")), ("weeks", N_("semanas")), ("months", N_("meses")), ("years", N_("años")))
DATE_FIELD_NAMES = (("modified", N_("Modificación")), ("created", N_("Creación")), ("accessed", N_("Último acceso")))
KIND_NAMES = (("all", N_("Todos los archivos y carpetas")), ("files", N_("Solo archivos")), ("folders", N_("Solo carpetas")), ("type", N_("Archivos de un tipo")))
ATTRIBUTE_NAMES = (("hidden", N_("Oculto")), ("readonly", N_("Solo lectura")), ("system", N_("Sistema")), ("archive", N_("Listo para archivar")))
CLAUSE_FIELDS = (("name", N_("Nombre del archivo")), ("content", N_("Texto dentro del archivo")), ("kind", N_("Tipo")), ("date", N_("Fecha")), ("time", N_("Hora")), ("size", N_("Tamaño")), ("attribute", N_("Atributo")))


def _combo(options, parent=None) -> QComboBox:
    box = QComboBox(parent)
    for key, label in options:
        box.addItem(tr(label), key)
    return box


def _choose(box: QComboBox, key) -> None:
    box.setCurrentIndex(max(0, box.findData(key)))


class FlowLayout(QLayout):
    """Coloca los elementos en fila y pasa a la siguiente cuando no caben."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(3, 3, 3, 3)
        self.setSpacing(3)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, place: bool) -> int:
        margins = self.contentsMargins()
        left, right = rect.x() + margins.left(), rect.right() - margins.right()
        x, y, line = left, rect.y() + margins.top(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right + 1 and line:
                x, y, line = left, y + line + self.spacing(), 0
            if place:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line + margins.bottom() - rect.y()


class Chips(QFrame):
    """Una lista corta de rutas o patrones, cada uno con su ✕ para quitarlo, y un + para añadir otro."""

    changed = Signal()

    def __init__(self, on_add, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("findChips")
        self.values: list[str] = []
        self._flow = FlowLayout(self)
        self._add = QToolButton()
        self._add.setText("+")
        self._add.setToolTip(tr("Añadir"))
        self._add.clicked.connect(on_add)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._rebuild()

    def set_values(self, values, notify: bool = True) -> None:
        wanted = list(dict.fromkeys(value for value in values if value))
        if wanted != self.values:
            self.values = wanted
            self._rebuild()
            if notify:
                self.changed.emit()

    def add_value(self, value: str) -> None:
        self.set_values([*self.values, value])

    def remove_value(self, value: str) -> None:
        self.set_values([item for item in self.values if item != value])

    def _rebuild(self) -> None:
        while self._flow.count():
            widget = self._flow.takeAt(0).widget()
            if widget is not None and widget is not self._add:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for value in self.values:
            self._flow.addWidget(self._chip(value))
        self._flow.addWidget(self._add)
        self.updateGeometry()

    def _chip(self, value: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("findChip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(5, 0, 1, 0)
        row.setSpacing(2)
        label = QLabel(self.fontMetrics().elidedText(value, Qt.TextElideMode.ElideMiddle, 340))
        label.setToolTip(value)
        row.addWidget(label)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip(tr("Quitar"))
        close.clicked.connect(lambda _=False, v=value: self.remove_value(v))
        row.addWidget(close)
        return chip


class _TextEditor(QWidget):
    """Una lista con lo último buscado y las opciones de cómo compararlo."""

    changed = Signal()
    FLAGS = (("wildcards", N_("Comodines (* y ?)"), N_("* vale por cualquier texto y ? por un solo carácter. Ejemplo: *.pdf")),
             ("any_word", N_("Cualquier palabra"), N_("Separa las palabras con espacios: basta con que aparezca una")),
             ("case", N_("Distinguir mayúsculas"), N_("Sin marcar, «Foto» y «foto» son lo mismo")),
             ("regex", N_("Expresión regular"), N_("Para patrones avanzados. Ejemplo: ^IMG_\\d+")),
             ("diacritics", N_("Ignorar acentos"), N_("«cafe» encuentra también «café»")))
    PARTIAL_NAME = (N_("Basta con una parte"), N_("Sin marcar, tiene que coincidir el nombre completo"))
    PARTIAL_TEXT = (N_("Basta con una parte"), N_("Sin marcar, tiene que ser una palabra completa"))

    def __init__(self, with_utf8: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.box = QComboBox()
        self.box.setEditable(True)
        self.box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.box.setMinimumContentsLength(16)
        self.box.editTextChanged.connect(lambda _text: self.changed.emit())
        self.flags: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(1)
        grid.setHorizontalSpacing(14)
        partial = self.PARTIAL_TEXT if with_utf8 else self.PARTIAL_NAME
        for position, (key, label, tip) in enumerate((*self.FLAGS, ("partial", *partial))):
            box = QCheckBox(tr(label))
            box.setToolTip(tr(tip))
            box.toggled.connect(lambda checked, k=key: self._flag_toggled(k, checked))
            self.flags[key] = box
            grid.addWidget(box, position // 3, position % 3)
        if with_utf8:
            box = QCheckBox(tr("Leer como UTF-8"))
            box.setToolTip(tr("Para archivos de texto en UTF-8 que no empiezan con una marca que lo indique"))
            box.toggled.connect(lambda _checked: self.changed.emit())
            self.flags["utf8"] = box
            grid.addWidget(box, 0, 3)
        grid.setColumnStretch(3, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.box)
        layout.addLayout(grid)

    def _flag_toggled(self, key: str, checked: bool) -> None:
        if checked and key in ("wildcards", "regex"):  # o una cosa o la otra: un * no significa lo mismo
            self.flags["regex" if key == "wildcards" else "wildcards"].setChecked(False)
        self.changed.emit()

    def state(self) -> dict:
        return {"text": self.box.currentText(), **{key: box.isChecked() for key, box in self.flags.items()}}

    def set_state(self, state: dict) -> None:
        self.box.setEditText(str(state.get("text", "")))
        for key, box in self.flags.items():
            box.setChecked(bool(state.get(key, False)))

    def set_history(self, items: list[str]) -> None:
        text = self.box.currentText()
        self.box.blockSignals(True)
        self.box.clear()
        self.box.addItems(items)
        self.box.setEditText(text)
        self.box.blockSignals(False)


class _DateEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.op = _combo(DATE_OPS)
        self.field = _combo(DATE_FIELD_NAMES)
        self.first = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.second = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.amount = QSpinBox(minimum=1, maximum=9999, value=7)
        self.unit = _combo(PERIOD_NAMES)
        _choose(self.unit, "days")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.op, self.field, self.first, self.second, self.amount, self.unit):
            row.addWidget(widget)
        row.addStretch(1)
        self.op.currentIndexChanged.connect(self._shape)
        for signal in (self.op.currentIndexChanged, self.field.currentIndexChanged, self.first.dateChanged, self.second.dateChanged, self.amount.valueChanged, self.unit.currentIndexChanged):
            signal.connect(lambda *_: self.changed.emit())
        self._shape()

    def _shape(self) -> None:
        op = self.op.currentData()
        self.field.setVisible(op != "ignore")
        self.first.setVisible(op in ("on", "before", "after", "between", "not_between"))
        self.second.setVisible(op in ("between", "not_between"))
        self.amount.setVisible(op == "within")
        self.unit.setVisible(op == "within")

    def state(self) -> dict:
        return {"op": self.op.currentData(), "field": self.field.currentData(), "first": self.first.date().toString(Qt.DateFormat.ISODate),
                "second": self.second.date().toString(Qt.DateFormat.ISODate), "amount": self.amount.value(), "unit": self.unit.currentData()}

    def set_state(self, state: dict) -> None:
        _choose(self.op, state.get("op"))
        _choose(self.field, state.get("field"))
        self.first.setDate(QDate.fromString(str(state.get("first")), Qt.DateFormat.ISODate))
        self.second.setDate(QDate.fromString(str(state.get("second")), Qt.DateFormat.ISODate))
        self.amount.setValue(int(state.get("amount", 7)))
        _choose(self.unit, state.get("unit"))


class _TimeEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.op = _combo(TIME_OPS)
        self.first = QTimeEdit(QTime(0, 0), displayFormat="HH:mm")
        self.second = QTimeEdit(QTime(23, 59), displayFormat="HH:mm")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.op, self.first, self.second):
            row.addWidget(widget)
        row.addStretch(1)
        self.op.currentIndexChanged.connect(self._shape)
        for signal in (self.op.currentIndexChanged, self.first.timeChanged, self.second.timeChanged):
            signal.connect(lambda *_: self.changed.emit())
        self._shape()

    def _shape(self) -> None:
        op = self.op.currentData()
        self.first.setVisible(op != "ignore")
        self.second.setVisible(op in ("between", "not_between"))

    def state(self) -> dict:
        return {"op": self.op.currentData(), "first": self.first.time().toString("HH:mm"), "second": self.second.time().toString("HH:mm")}

    def set_state(self, state: dict) -> None:
        _choose(self.op, state.get("op"))
        self.first.setTime(QTime.fromString(str(state.get("first")), "HH:mm"))
        self.second.setTime(QTime.fromString(str(state.get("second")), "HH:mm"))


class _SizeEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.op = _combo(SIZE_OPS)
        self.first = QDoubleSpinBox(decimals=2, maximum=1e12, value=1)
        self.second = QDoubleSpinBox(decimals=2, maximum=1e12, value=10)
        self.unit = QComboBox()
        for name in UNIT_NAMES:
            self.unit.addItem(name, name)
        _choose(self.unit, "MB")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.op, self.first, self.second, self.unit):
            row.addWidget(widget)
        row.addStretch(1)
        self.op.currentIndexChanged.connect(self._shape)
        for signal in (self.op.currentIndexChanged, self.first.valueChanged, self.second.valueChanged, self.unit.currentIndexChanged):
            signal.connect(lambda *_: self.changed.emit())
        self._shape()

    def _shape(self) -> None:
        op = self.op.currentData()
        self.first.setVisible(op != "ignore")
        self.second.setVisible(op in ("between", "not_between"))
        self.unit.setVisible(op != "ignore")

    def state(self) -> dict:
        return {"op": self.op.currentData(), "first": self.first.value(), "second": self.second.value(), "unit": self.unit.currentData()}

    def set_state(self, state: dict) -> None:
        _choose(self.op, state.get("op"))
        self.first.setValue(float(state.get("first", 1)))
        self.second.setValue(float(state.get("second", 10)))
        _choose(self.unit, state.get("unit"))


class _KindEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.kind = _combo(KIND_NAMES)
        self.content_type = _combo([(key, name) for key, (name, _extensions) in CONTENT_TYPES.items()])
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.kind)
        row.addWidget(self.content_type, 1)
        self.kind.currentIndexChanged.connect(self._shape)
        self.kind.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.content_type.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._shape()

    def _shape(self) -> None:
        self.content_type.setEnabled(self.kind.currentData() == "type")

    def state(self) -> dict:
        return {"kind": self.kind.currentData(), "content_type": self.content_type.currentData()}

    def set_state(self, state: dict) -> None:
        _choose(self.kind, state.get("kind"))
        _choose(self.content_type, state.get("content_type"))


class _AttributeEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.attribute = _combo(ATTRIBUTE_NAMES)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.attribute)
        row.addStretch(1)
        self.attribute.currentIndexChanged.connect(lambda _i: self.changed.emit())

    def state(self) -> dict:
        return {"attribute": self.attribute.currentData()}

    def set_state(self, state: dict) -> None:
        _choose(self.attribute, state.get("attribute"))


class _Rule(QFrame):
    """Una regla del modo avanzado: cómo se une a la anterior, si debe coincidir o no, qué se mira y con qué valor."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, first: bool, parent=None) -> None:
        super().__init__(parent)
        self.join = _combo((("and", N_("Y")), ("or", N_("O"))))
        self.join.setVisible(not first)
        self.negate = _combo(((False, N_("Coincide")), (True, N_("No coincide"))))
        self.field = _combo(CLAUSE_FIELDS)
        self.editors = {"name": _TextEditor(), "content": _TextEditor(True), "kind": _KindEditor(), "date": _DateEditor(), "time": _TimeEditor(),
                        "size": _SizeEditor(), "attribute": _AttributeEditor()}
        blank = file_search.default_state()
        self.editors["name"].set_state(blank["name"])  # una regla nueva compara como las casillas de siempre
        self.editors["content"].set_state(blank["content"])
        self.stack = QStackedWidget()
        for editor in self.editors.values():
            self.stack.addWidget(editor)
            editor.changed.connect(self.changed)
        remove = QToolButton()
        remove.setText("−")
        remove.setToolTip(tr("Quitar esta regla"))
        remove.clicked.connect(lambda: self.removed.emit(self))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.join, self.negate, self.field):
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.stack, 1)
        row.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)
        self.field.currentIndexChanged.connect(self._show_editor)
        for box in (self.join, self.negate):
            box.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._show_editor()

    def _show_editor(self) -> None:
        self.stack.setCurrentWidget(self.editors[self.field.currentData()])
        self.changed.emit()

    def state(self) -> dict:
        kind = self.field.currentData()
        return {"join": self.join.currentData(), "negate": self.negate.currentData(), "field": kind, "value": self.editors[kind].state()}

    def set_state(self, state: dict) -> None:
        _choose(self.join, state.get("join", "and"))
        _choose(self.negate, bool(state.get("negate")))
        _choose(self.field, state.get("field", "name"))
        value = state.get("value")
        if isinstance(value, dict):
            self.editors[self.field.currentData()].set_state(value)


class _Rules(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rules: list[_Rule] = []
        self._column = QVBoxLayout()
        self._column.setContentsMargins(0, 0, 0, 0)
        add = QPushButton(tr("Añadir regla"))
        add.clicked.connect(lambda: self.add_rule())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._column)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

    def add_rule(self, state: dict | None = None) -> _Rule:
        rule = _Rule(not self.rules)
        if state:
            rule.set_state(state)
        rule.changed.connect(self.changed)
        rule.removed.connect(self._remove)
        self.rules.append(rule)
        self._column.addWidget(rule)
        self.changed.emit()
        return rule

    def _remove(self, rule: _Rule) -> None:
        self.rules.remove(rule)
        rule.setParent(None)
        rule.deleteLater()
        if self.rules:
            self.rules[0].join.setVisible(False)
        self.changed.emit()

    def state(self) -> list[dict]:
        return [rule.state() for rule in self.rules]

    def set_state(self, clauses: list) -> None:
        while self.rules:
            rule = self.rules.pop()
            rule.setParent(None)
            rule.deleteLater()
        for clause in clauses:
            if isinstance(clause, dict):
                self.add_rule(clause)
        self.changed.emit()


class FindPanel(QWidget):
    showResults = Signal(str, str)  # colección, dónde abrirla
    columnsChanged = Signal()
    needsRoom = Signal(int)  # le faltan esos píxeles de alto para verse sin desplazarse
    resultsChanged = Signal(str)
    searchFinished = Signal(str)

    def __init__(self, get_source, get_dest, config=None, parent=None) -> None:
        super().__init__(parent)
        self._get_source, self._get_dest = get_source, get_dest
        self.get_open_tabs = lambda: []  # lo da la ventana: al crear este panel aún no hay pestañas
        self.config = config if config is not None else {}
        self._call = None
        self._stopped = False
        self._last_folder = ""
        self._loading = False

        self.advanced = QCheckBox(tr("Modo avanzado"))
        self.advanced.toggled.connect(self._show_mode)
        self.templates_button = QToolButton()
        self.templates_button.setText(tr("Plantillas") + MENU_MARK)
        self.templates_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.templates_menu = QMenu(self)
        self.templates_menu.aboutToShow.connect(self._fill_templates)
        self.templates_button.setMenu(self.templates_menu)
        reset = QToolButton()
        reset.setText(tr("Restablecer") + MENU_MARK)
        reset.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.reset_menu = QMenu(self)
        self.reset_menu.addAction(tr("Todas las opciones"), self.reset_all)
        self.reset_menu.addAction(tr("Solo el nombre y el texto"), self.reset_texts)
        self.reset_menu.addAction(tr("Solo fecha, hora y tamaño"), self.reset_filters)
        reset.setMenu(self.reset_menu)
        self.summary = QLabel()
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.columns_box = _combo(((1, N_("Una columna")), (2, N_("Dos columnas"))))
        self.columns_box.setToolTip(tr("Cómo se colocan «Qué buscar» y «Dónde buscar» en esta ventana"))
        self.columns_box.activated.connect(self._columns_chosen)
        self.columns_box.hide()
        top = QHBoxLayout()
        top.addWidget(self.advanced)
        top.addWidget(self.templates_button)
        top.addWidget(reset)
        top.addWidget(self.summary, 1)
        top.addWidget(self.columns_box)

        # Izquierda: qué se busca
        self.name = _TextEditor()
        self.pattern = self.name.box.lineEdit()
        self.content = _TextEditor(True)
        self.kind_filter = _KindEditor()
        self.date_filter = _DateEditor()
        self.time_filter = _TimeEditor()
        self.size_filter = _SizeEditor()
        simple = QWidget()
        self.pattern.setPlaceholderText(tr("Parte del nombre o un patrón. Ejemplo: informe o *.pdf"))
        self.content.box.lineEdit().setPlaceholderText(tr("Lo que está escrito dentro de los archivos. Tarda más, porque hay que abrirlos"))
        self.rule_hint = QLabel(tr("Los archivos deben cumplir todo lo que rellenes: el nombre y el texto a la vez, si escribes los dos."))
        self.rule_hint.setObjectName("findHint")
        self.rule_hint.setWordWrap(True)
        form = self._form = QGridLayout(simple)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)
        for row, (label, widget) in enumerate(((tr("Nombre del archivo:"), self.name), (tr("Texto dentro del archivo:"), self.content))):
            title = QLabel(label)
            title.setContentsMargins(0, 4, 0, 0)  # a la altura del campo que tiene al lado
            form.addWidget(title, row, 0, Qt.AlignmentFlag.AlignTop)
            form.addWidget(widget, row, 1)
        form.addWidget(self.rule_hint, 2, 1)
        form.addWidget(QLabel(tr("Tipo:")), 3, 0)
        form.addWidget(self.kind_filter, 3, 1)
        limits = QWidget()
        flow = FlowLayout(limits)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(FLOW_SPACING)
        for label, widget in ((tr("Fecha:"), self.date_filter), (tr("Hora:"), self.time_filter), (tr("Tamaño:"), self.size_filter)):
            pair = QWidget()
            pair_row = QHBoxLayout(pair)
            pair_row.setContentsMargins(0, 0, 0, 0)
            pair_row.addWidget(QLabel(label))
            pair_row.addWidget(widget)
            flow.addWidget(pair)
        form.addWidget(limits, 4, 0, 1, 2)
        form.setColumnStretch(1, 1)
        form.setRowStretch(5, 1)  # lo que sobre, debajo de todo: repartido entre las filas parecerían separadas
        self.rules = _Rules()
        self.modes = QStackedWidget()
        self.modes.addWidget(simple)
        self.modes.addWidget(self.rules)

        # Derecha: dónde y qué hacer con lo encontrado
        self.roots = Chips(self._add_root)
        self.roots_button = self._menu_button(tr("Buscar en:"), self._fill_roots_menu)
        self.follow_source = False
        self.subfolders = QCheckBox(tr("Buscar en subcarpetas"))
        self.archives = QCheckBox(tr("Buscar dentro de comprimidos"))
        self.quiet = QCheckBox(tr("No avisar de las carpetas sin permiso"))
        self.subfolders.setToolTip(tr("Sin marcar, solo se mira lo que está directamente en esas carpetas"))
        self.archives.setToolTip(tr("Mira también dentro de los ZIP y otros comprimidos. Tarda más"))
        self.quiet.setToolTip(tr("Sin marcar, al terminar se dice en qué carpetas no se pudo mirar"))
        self.excludes = Chips(self._add_exclude)
        self.excludes_button = self._menu_button(tr("Excluir:"), self._fill_excludes_menu)
        self.roots_button.setToolTip(tr("Más opciones: seguir al panel activo, añadir unidades…"))
        self.excludes_button.setToolTip(tr("Más opciones: añadir un patrón, excluir las carpetas ocultas…"))
        self.exclude_hidden = False
        self.target = _combo(RESULT_TARGETS)
        self.collection = QComboBox()
        self.collection.setEditable(True)
        self.collection.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.collection.setToolTip(tr("Cada nombre guarda sus propios resultados. Usa otro para conservar los de una búsqueda anterior"))
        self.clear = QCheckBox(tr("Limpiar resultados anteriores"))
        self.clear.setToolTip(tr("Sin marcar, lo nuevo se añade a lo que ya hay con ese nombre"))
        self.go = QPushButton(tr("Buscar"))
        self.go.setDefault(True)
        self.command = QPlainTextEdit()
        self.command.setReadOnly(True)
        self.command.setMaximumHeight(self.fontMetrics().height() * 3 + 12)
        self.command_label = QLabel(tr("Comando:"))

        side = self._side = QGridLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setVerticalSpacing(3)
        side.addWidget(self.roots_button, 0, 0, Qt.AlignmentFlag.AlignTop)
        side.addWidget(self.roots, 0, 1)
        where = QWidget()
        where_flow = FlowLayout(where)
        where_flow.setContentsMargins(0, 0, 0, 0)
        where_flow.setSpacing(FLOW_SPACING)
        for box in (self.subfolders, self.archives, self.quiet):
            where_flow.addWidget(box)
        side.addWidget(where, 1, 1)
        side.addWidget(self.excludes_button, 2, 0, Qt.AlignmentFlag.AlignTop)
        side.addWidget(self.excludes, 2, 1)
        side.addWidget(QLabel(tr("Resultados en:")), 4, 0)
        side.addWidget(self.target, 4, 1)
        side.addWidget(QLabel(tr("Guardados como:")), 5, 0)
        side.addWidget(self.collection, 5, 1)
        side.addWidget(self.command_label, 6, 0, Qt.AlignmentFlag.AlignTop)
        side.addWidget(self.command, 6, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.clear)
        buttons.addStretch(1)
        buttons.addWidget(self.go)
        side.setRowStretch(7, 1)
        side.addLayout(buttons, 8, 1)
        side.setColumnStretch(1, 1)

        body = self.body = QWidget()
        columns = self._columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(SECTION_SPACING)
        what, where_to = QVBoxLayout(), QVBoxLayout()
        for section, title, content in ((what, tr("Qué buscar"), self.modes), (where_to, tr("Dónde buscar"), side)):
            heading = QLabel(title)
            heading.setObjectName("findHeading")
            section.setSpacing(HEADING_SPACING)
            section.addWidget(heading)
            section.addWidget(content, 1) if content is self.modes else section.addLayout(content, 1)
        columns.addLayout(what, 1)
        self._line = QFrame()
        self._line.setFrameShape(QFrame.Shape.VLine)
        columns.addWidget(self._line)
        columns.addLayout(where_to, 1)
        self.one_column = False
        scroll = self._scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)
        layout.addLayout(top)
        layout.addWidget(scroll, 1)

        self.show_command(sys.platform != "win32")
        self.go.clicked.connect(self._go_clicked)
        self.name.box.lineEdit().returnPressed.connect(self.start)
        self.content.box.lineEdit().returnPressed.connect(self.start)
        for editor in (self.name, self.content, self.kind_filter, self.date_filter, self.time_filter, self.size_filter, self.rules, self.roots, self.excludes):
            editor.changed.connect(self._options_changed)
        for box in (self.subfolders, self.archives, self.quiet, self.clear, self.advanced):
            box.toggled.connect(lambda _checked: self._options_changed())
        for editor in (self.date_filter, self.time_filter, self.size_filter, self.rules, self.roots, self.excludes):
            editor.changed.connect(self.check_room)  # los que cambian de alto; lo que se escribe, no
        self.set_state(file_search.merged_state(self.config.get("find_state")))
        self.sync_source()

    @staticmethod
    def _menu_button(text: str, fill) -> QToolButton:
        button = QToolButton()
        button.setText(text + MENU_MARK)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda: fill(menu))
        button.setMenu(menu)
        return button

    def show_columns_choice(self, shown: bool) -> None:
        """Solo tiene sentido en su propia ventana: abajo siempre van dos columnas."""
        self.columns_box.setVisible(shown)
        _choose(self.columns_box, self.config.get("find_window_columns", 1))

    def _columns_chosen(self, _row: int) -> None:
        self.config["find_window_columns"] = self.columns_box.currentData()
        self.columnsChanged.emit()

    def check_room(self) -> None:
        QTimer.singleShot(ROOM_CHECK_MS, self._report_room)

    def _report_room(self) -> None:
        missing = self._scroll.verticalScrollBar().maximum()
        if missing > 0:
            self.needsRoom.emit(missing)

    def set_one_column(self, one: bool) -> bool:
        """Lo de buscar y lo de dónde buscar, uno encima del otro o uno junto al otro. True si ha cambiado."""
        if one == self.one_column:
            return False
        self.one_column = one
        self._columns.setDirection(QBoxLayout.Direction.TopToBottom if one else QBoxLayout.Direction.LeftToRight)
        self._line.setFrameShape(QFrame.Shape.HLine if one else QFrame.Shape.VLine)
        self._columns.setStretch(0, 0 if one else 1)  # en vertical, lo que sobre va antes del botón Buscar y no entre las filas
        # Las etiquetas de las dos partes alineadas: una debajo de la otra se notaría si no.
        width = max(self._form.itemAtPosition(row, 0).sizeHint().width() for row in (0, 1)) if one else 0
        self._form.setColumnMinimumWidth(0, width)
        self._side.setColumnMinimumWidth(0, width)
        return True

    def window_size(self) -> QSize:
        """Lo que necesita en su propia ventana para que se vea todo y no sobre."""
        width = max(ONE_COLUMN_WIDTH if self.one_column else WINDOW_WIDTH, self.body.sizeHint().width() + WINDOW_EXTRA_WIDTH) + 2 * WINDOW_MARGIN
        layout = self.body.layout()
        # Según el ancho: las filas que se reparten en varias líneas no se saben hasta entonces.
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.totalSizeHint().height()
        # A dos columnas el cálculo ya sobra: las filas que reparte se miden con todo el ancho, y si faltase algo la ventana crece.
        return QSize(width, height + (WINDOW_EXTRA_HEIGHT if self.one_column else 0) + 2 * WINDOW_MARGIN)

    def show_command(self, shown: bool) -> None:
        self.command_label.setVisible(shown)
        self.command.setVisible(shown)
        self._update_command()

    def _show_mode(self, advanced: bool) -> None:
        self.modes.setCurrentIndex(1 if advanced else 0)
        if advanced and not self.rules.rules:
            self._rules_from_simple()

    def _rules_from_simple(self) -> None:
        """Al pasar al modo avanzado se parte de lo que ya estaba escrito."""
        state = self.state()
        clauses = []
        if state["name"]["text"]:
            clauses.append({"join": "and", "negate": False, "field": "name", "value": state["name"]})
        if state["content"]["text"]:
            clauses.append({"join": "and", "negate": False, "field": "content", "value": state["content"]})
        if state["kind"]["kind"] != "all":
            clauses.append({"join": "and", "negate": False, "field": "kind", "value": state["kind"]})
        for key in ("date", "time", "size"):
            if state[key]["op"] != "ignore":
                clauses.append({"join": "and", "negate": False, "field": key, "value": state[key]})
        self.rules.set_state(clauses or [{"field": "name"}])

    # ---------------------------------------------------------------- estado
    def state(self) -> dict:
        target_roots = self.roots.values
        return {"name": self.name.state(), "content": self.content.state(), "kind": self.kind_filter.state(), "date": self.date_filter.state(), "time": self.time_filter.state(),
                "size": self.size_filter.state(), "roots": list(target_roots), "follow": self.follow_source, "subfolders": self.subfolders.isChecked(),
                "archives": self.archives.isChecked(), "quiet": self.quiet.isChecked(), "exclude": list(self.excludes.values), "exclude_hidden": self.exclude_hidden,
                "advanced": self.advanced.isChecked(), "clauses": self.rules.state(), "target": self.target.currentData(),
                "collection": self.collection.currentText().strip() or file_search.DEFAULT_COLLECTION, "clear": self.clear.isChecked()}

    def set_state(self, state: dict) -> None:
        self._loading = True
        self.name.set_state(state["name"])
        self.content.set_state(state["content"])
        self.kind_filter.set_state(state["kind"])
        self.date_filter.set_state(state["date"])
        self.time_filter.set_state(state["time"])
        self.size_filter.set_state(state["size"])
        self.follow_source = bool(state["follow"])
        self.roots.set_values(state["roots"], notify=False)
        self.subfolders.setChecked(bool(state["subfolders"]))
        self.archives.setChecked(bool(state["archives"]))
        self.quiet.setChecked(bool(state["quiet"]))
        self.excludes.set_values(state["exclude"], notify=False)
        self.exclude_hidden = bool(state["exclude_hidden"])
        self.rules.set_state(state["clauses"])
        self.advanced.setChecked(bool(state["advanced"]))
        self.modes.setCurrentIndex(1 if state["advanced"] else 0)
        _choose(self.target, state["target"])
        self.collection.setEditText(state["collection"])
        self.clear.setChecked(bool(state["clear"]))
        self._loading = False
        self._refresh_histories()
        self._options_changed()

    def _options_changed(self) -> None:
        if not self._loading:
            self._update_command()

    def _update_command(self) -> None:
        if self.command.isHidden():
            return
        try:
            state = self.state()
            file_search.prepare(state)
        except ValueError as exc:
            self.command.setPlainText(str(exc))
            return
        self.command.setPlainText(file_search.describe_command(file_search.build_command(state)))

    def _refresh_histories(self) -> None:
        history = self.config.get("find_history") or {}
        self.name.set_history(list(history.get("name", [])))
        self.content.set_history(list(history.get("content", [])))

    def _remember(self, state: dict) -> None:
        history = self.config.get("find_history") or {}
        for key in ("name", "content"):
            text = state[key]["text"]
            if text:
                history[key] = list(dict.fromkeys([text, *history.get(key, [])]))[:HISTORY_SIZE]
        self.config["find_history"] = history
        self.config["find_state"] = state
        self._refresh_histories()

    # ---------------------------------------------------------------- dónde buscar
    def sync_source(self) -> None:
        """Con «seguir al panel activo», la primera ruta es la del panel. Los resultados y «Este equipo» no son carpetas en las que buscar."""
        path = self._get_source()
        if path and not is_results(path) and path != COMPUTER_PATH:
            self._last_folder = path
        if not self.follow_source or not self._last_folder or self.roots.values == [self._last_folder]:
            return
        self.roots.set_values([self._last_folder])

    def _add_root(self) -> None:
        chosen = FolderPicker.ask(tr("Buscar en"), self.config, self.get_open_tabs(), parent=self)
        if chosen:
            self.follow_source = False
            self.roots.set_values([*self.roots.values, *chosen])

    def _fill_roots_menu(self, menu: QMenu) -> None:
        menu.clear()
        follow = menu.addAction(tr("Seguir al panel activo"))
        follow.setCheckable(True)
        follow.setChecked(self.follow_source)
        follow.toggled.connect(self.set_follow)
        menu.addSeparator()
        menu.addAction(tr("Añadir la carpeta del panel activo"), lambda: self.roots.add_value(self._last_folder))
        destination = self._get_dest()
        add_dest = menu.addAction(tr("Añadir la carpeta del panel de destino"), lambda: self.roots.add_value(destination))
        add_dest.setEnabled(bool(destination) and os.path.isdir(destination))
        menu.addAction(tr("Añadir otra carpeta…"), self._add_root)
        menu.addAction(tr("Añadir todas las unidades"), self._add_drives)
        menu.addSeparator()
        menu.addAction(tr("Quitar todas"), lambda: self.roots.set_values([]))

    def set_follow(self, follow: bool) -> None:
        self.follow_source = follow
        if follow:
            self.sync_source()

    def _add_drives(self) -> None:
        from ..core.drives import list_drives

        self.follow_source = False
        self.roots.set_values([*self.roots.values, *[drive.path for drive in list_drives()]])

    def _add_exclude(self) -> None:
        chosen = FolderPicker.ask(tr("Excluir"), self.config, self.get_open_tabs(), patterns=True, parent=self)
        if chosen:
            self.excludes.set_values([*self.excludes.values, *chosen])

    def _add_exclude_pattern(self) -> None:
        text, accepted = QInputDialog.getText(self, tr("Excluir"), tr("Carpeta o patrón con comodines:"))
        if accepted and text.strip():
            self.excludes.add_value(text.strip())

    def _fill_excludes_menu(self, menu: QMenu) -> None:
        menu.clear()
        hidden = menu.addAction(tr("Excluir las carpetas ocultas y del sistema"))
        hidden.setCheckable(True)
        hidden.setChecked(self.exclude_hidden)
        hidden.toggled.connect(self._set_exclude_hidden)
        menu.addSeparator()
        menu.addAction(tr("Añadir una carpeta…"), self._add_exclude)
        menu.addAction(tr("Añadir un patrón…"), self._add_exclude_pattern)
        menu.addSeparator()
        menu.addAction(tr("Volver a las exclusiones por defecto"), lambda: self.excludes.set_values(file_search.default_excludes()))

    def _set_exclude_hidden(self, excluded: bool) -> None:
        self.exclude_hidden = excluded
        self._options_changed()

    # ---------------------------------------------------------------- plantillas y restablecer
    def _templates(self) -> dict:
        templates = self.config.get("find_templates")
        return templates if isinstance(templates, dict) else {}

    def _fill_templates(self) -> None:
        menu = self.templates_menu
        menu.clear()
        menu.addAction(tr("Guardar como plantilla…"), self._save_template)
        templates = self._templates()
        if templates:
            menu.addSeparator()
            for name in sorted(templates, key=str.casefold):
                menu.addAction(name, lambda n=name: self.load_template(n))
            menu.addSeparator()
            remove = menu.addMenu(tr("Eliminar"))
            for name in sorted(templates, key=str.casefold):
                remove.addAction(name, lambda n=name: self.delete_template(n))

    def _save_template(self) -> None:
        name, accepted = QInputDialog.getText(self, tr("Plantillas"), tr("Nombre de la plantilla:"))
        if accepted and name.strip():
            self.save_template(name.strip())

    def save_template(self, name: str) -> None:
        self.config["find_templates"] = {**self._templates(), name: self.state()}

    def load_template(self, name: str) -> None:
        if name in self._templates():
            self.set_state(file_search.merged_state(self._templates()[name]))
            self.sync_source()

    def delete_template(self, name: str) -> None:
        self.config["find_templates"] = {key: value for key, value in self._templates().items() if key != name}

    def reset_all(self) -> None:
        self.set_state(file_search.default_state())
        self.sync_source()
        self.summary.setText("")

    def reset_texts(self) -> None:
        blank = file_search.default_state()
        self.name.set_state(blank["name"])
        self.content.set_state(blank["content"])

    def reset_filters(self) -> None:
        blank = file_search.default_state()
        self.kind_filter.set_state(blank["kind"])
        self.date_filter.set_state(blank["date"])
        self.time_filter.set_state(blank["time"])
        self.size_filter.set_state(blank["size"])

    # ---------------------------------------------------------------- buscar
    def _go_clicked(self) -> None:
        if self._call:
            self._stopped = True
            self._call.cancel()
        else:
            self.start()

    def start(self) -> None:
        if self._call:
            return
        state = self.state()
        self.sync_source()
        state["roots"] = list(self.roots.values)
        collection = state["collection"]
        if not state["roots"]:
            self.summary.setText(tr("⚠ Indica dónde buscar"))
            return
        try:
            file_search.prepare(state)
        except ValueError as exc:
            self.summary.setText(f"⚠ {exc}")
            return
        self._remember(state)
        if state["clear"] or not get_results(collection):
            set_results(collection, [])
        self.summary.setText(tr("Buscando…"))
        self.go.setText(tr("Detener"))
        self._stopped = False
        self.showResults.emit(collection, state["target"])

        def work(sink):
            return file_search.run_search(state, lambda: sink.is_cancelled, lambda batch: sink.report(0, 0, list(batch)))

        self._collection, self._state = collection, state
        self._call = run_bg(work, self._finished, self._failed, self._add_batch)

    def _add_batch(self, value) -> None:
        _done, _total, batch = value
        add_results(self._collection, batch)
        self.resultsChanged.emit(self._collection)

    def _finished(self, found: file_search.Found) -> None:
        known = set(get_results(self._collection))
        add_results(self._collection, [path for path in found.paths if path not in known])
        self._call = None
        self.go.setText(tr("Buscar"))
        text = tr("{count} resultados{value}", count=len(found.paths), value=tr(" (detenida)") if self._stopped or found.cancelled else "")
        if found.denied and not self._state["quiet"]:
            text += " · " + tr("Sin permiso para mirar en {count} carpetas: {first}", count=len(found.denied), first=found.denied[0])
        self.summary.setText(text)
        self.searchFinished.emit(self._collection)

    def _failed(self, exc: BaseException) -> None:
        self._call = None
        self.go.setText(tr("Buscar"))
        self.summary.setText(f"⚠ {exc}")

    @property
    def is_running(self) -> bool:
        return self._call is not None

    def results_path(self) -> str:
        return results_path(self.collection.currentText().strip() or file_search.DEFAULT_COLLECTION)


class FindWindow(QDialog):
    """La búsqueda separada de la ventana principal, si se prefiere así. Recuerda dónde estaba."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Buscar archivos"))
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN)
        saved = config.get("find_window_geometry")
        self._placed = isinstance(saved, str) and bool(saved)
        if self._placed:
            self.restoreGeometry(QByteArray.fromHex(saved.encode()))

    def hold(self, panel: "FindPanel") -> None:
        self._layout.addWidget(panel)
        panel.show()
        self.panel = panel
        panel.needsRoom.connect(self._make_room)
        if not self._placed:
            self.fit(panel)

    def fit(self, panel: "FindPanel") -> None:
        self.resize(panel.window_size())
        panel.check_room()

    def release(self, panel: "FindPanel") -> None:
        panel.needsRoom.disconnect(self._make_room)

    def _make_room(self, missing: int) -> None:
        if self.isVisible() and not self.isMaximized():
            screen = self.screen().availableGeometry().height() - FRAME_MARGIN
            self.resize(self.width(), min(self.height() + missing, screen))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:  # hasta que se ve no se sabe cuántas líneas ocupa lo que se reparte
            self.panel.check_room()

    def hideEvent(self, event) -> None:
        self.config["find_window_geometry"] = bytes(self.saveGeometry().toHex()).decode()
        super().hideEvent(event)
