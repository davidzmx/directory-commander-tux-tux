"""Páginas de las preferencias con las opciones de la barra de pestañas."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import tab_settings
from ..core.i18n import tr

NEW_TAB_LABELS = ((tr("Carpeta actual"), "current"), (tr("Duplicar"), "duplicate"), (tr("Carpeta predeterminada"), "default"),
                  (tr("Carpeta de inicio"), "home"), (tr("Ubicación"), "location"))


def _combo(choices, value) -> QComboBox:
    combo = QComboBox()
    for label, data in choices:
        combo.addItem(label, data)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    return combo


def _spin(value: int, low: int, high: int, suffix: str = tr(" píxeles")) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


def _indented(widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addSpacing(22)
    row.addWidget(widget, 1)
    return row


class PathField(QWidget):
    def __init__(self, value: str, title: str) -> None:
        super().__init__()
        self.title = title
        self.edit = QLineEdit(value)
        browse = QPushButton(tr("Elegir…"))
        browse.clicked.connect(self._choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.title, self.edit.text())
        if folder:
            self.edit.setText(os.path.normpath(folder))

    def text(self) -> str:
        return self.edit.text().strip()


class TabSettingsPages:
    """Crea las páginas y devuelve lo elegido. `pages` son pares (título, página)."""

    def __init__(self, raw) -> None:
        self.settings = tab_settings.normalize(raw)
        self.checks: dict[str, QCheckBox] = {}
        self.pages = [(tr("Barra"), self._bar_page()), (tr("Dimensiones"), self._size_page()),
                      (tr("Opciones"), self._options_page())]

    def _check(self, key: str, label: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setChecked(self.settings[key])
        self.checks[key] = box
        return box

    def _new_tab_row(self, layout: QVBoxLayout, key: str, label: str) -> None:
        box = self._check(key, label)
        combo = _combo(NEW_TAB_LABELS, self.settings[f"{key}_action"])
        path = PathField(self.settings[f"{key}_path"], tr("Carpeta de la nueva pestaña"))
        row = QHBoxLayout()
        row.addWidget(combo)
        row.addWidget(path, 1)
        layout.addWidget(box)
        layout.addLayout(row)
        setattr(self, f"{key}_action", combo)
        setattr(self, f"{key}_path", path)

        def update() -> None:
            combo.setEnabled(box.isChecked())
            path.setEnabled(box.isChecked() and combo.currentData() == "location")

        box.toggled.connect(lambda _on: update())
        combo.currentIndexChanged.connect(lambda _i: update())
        update()

    def _bar_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._new_tab_row(layout, "new_button", tr("Mostrar botón de nueva pestaña:"))
        self._new_tab_row(layout, "double_click", tr("Doble clic para abrir una nueva pestaña:"))
        self.default_folder = PathField(self.settings["default_folder"], tr("Carpeta predeterminada"))
        self.default_folder.edit.setPlaceholderText(tr("Vacío: la carpeta de inicio"))
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel(tr("Carpeta predeterminada:")))
        default_row.addWidget(self.default_folder, 1)
        layout.addLayout(default_row)
        layout.addSpacing(8)
        layout.addWidget(self._check("open_next_to_active", tr("Abrir nuevas pestañas al lado de la pestaña activa")))
        rows = self._check("multi_row", tr("Acomodar las pestañas en varias filas cuando no quepan (arriba o abajo)"))
        layout.addWidget(rows)
        menu = self._check("overflow_menu", tr("Usar el menú » cuando las pestañas no tengan espacio"))
        layout.addWidget(menu)
        children = [self._check("menu_all_tabs", tr("Mostrar todas las pestañas en el menú")),
                    self._check("menu_when_shrunk", tr("Mostrar el menú cuando las pestañas estén reducidas"))]
        for child in children:
            layout.addLayout(_indented(child))

        def update_menu() -> None:
            menu.setEnabled(not rows.isChecked())
            for child in children:
                child.setEnabled(menu.isChecked() and not rows.isChecked())

        rows.toggled.connect(lambda _on: update_menu())
        menu.toggled.connect(lambda _on: update_menu())
        update_menu()
        layout.addSpacing(8)
        self.show_bar = _combo(((tr("Si es necesario"), "needed"), (tr("Si es necesario (equilibrado)"), "balanced"), (tr("Siempre"), "always")),
                               self.settings["show_bar"])
        self.position = _combo(((tr("Abajo"), "bottom"), (tr("Arriba"), "top"), (tr("Derecha"), "right"), (tr("Izquierda"), "left")),
                               self.settings["position"])
        self.dual_position = _combo(((tr("Normal"), "normal"), (tr("Juntos"), "together"), (tr("Separados"), "apart")),
                                    self.settings["dual_position"])
        form = QFormLayout()
        form.addRow(tr("Mostrar barra de pestañas:"), self.show_bar)
        form.addRow(tr("Posición de la barra de pestañas:"), self.position)
        form.addRow(tr("Posición con doble panel:"), self.dual_position)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _size_page(self) -> QWidget:
        settings = self.settings
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(tr("<b>Pestañas horizontales</b>")))
        same = self._check("same_size", tr("Mismo tamaño:"))
        self.same_auto = QRadioButton(tr("Tamaño automático"))
        self.same_fixed = QRadioButton(tr("Tamaño fijo:"))
        (self.same_fixed if settings["same_size_mode"] == "fixed" else self.same_auto).setChecked(True)
        self.fixed_size = _spin(settings["fixed_size"], 20, 1000)
        self.max_size = _spin(settings["max_size"], 20, 1000)
        self.min_size = _spin(settings["min_size"], 20, 1000)
        maximum = self._check("max_size_on", tr("Tamaño máximo:"))
        minimum = self._check("min_size_on", tr("Tamaño mínimo:"))
        grid = QGridLayout()
        grid.addWidget(same, 0, 0)
        grid.addWidget(self.same_auto, 1, 0)
        grid.addWidget(self.same_fixed, 2, 0)
        grid.addWidget(self.fixed_size, 2, 1)
        grid.addWidget(maximum, 3, 0)
        grid.addWidget(self.max_size, 3, 1)
        grid.addWidget(minimum, 4, 0)
        grid.addWidget(self.min_size, 4, 1)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        layout.addWidget(self._check("shrink", tr("Reducir tamaño para caber en el espacio disponible")))
        layout.addSpacing(10)
        layout.addWidget(QLabel(tr("<b>Pestañas verticales</b>")))
        self.vertical_auto = QRadioButton(tr("Ancho automático"))
        self.vertical_fixed = QRadioButton(tr("Ancho inicial:"))
        (self.vertical_auto if settings["vertical_auto_width"] else self.vertical_fixed).setChecked(True)
        self.vertical_width = _spin(settings["vertical_width"], 40, 1000)
        vertical = QGridLayout()
        vertical.addWidget(self.vertical_auto, 0, 0)
        vertical.addWidget(self.vertical_fixed, 1, 0)
        vertical.addWidget(self.vertical_width, 1, 1)
        vertical.setColumnStretch(2, 1)
        layout.addLayout(vertical)
        layout.addStretch(1)

        def update() -> None:
            self.same_auto.setEnabled(same.isChecked())
            self.same_fixed.setEnabled(same.isChecked())
            self.fixed_size.setEnabled(same.isChecked() and self.same_fixed.isChecked())
            self.max_size.setEnabled(maximum.isChecked())
            self.min_size.setEnabled(minimum.isChecked())
            self.vertical_width.setEnabled(self.vertical_fixed.isChecked())

        for control in (same, self.same_fixed, maximum, minimum, self.vertical_fixed):
            control.toggled.connect(lambda _on: update())
        update()
        return page

    def _options_page(self) -> QWidget:
        settings = self.settings
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._check("show_drive", tr("Mostrar la letra de unidad en la etiqueta de pestaña")))
        layout.addWidget(self._check("show_icons", tr("Mostrar iconos de carpeta")))
        layout.addWidget(self._check("show_index", tr("Mostrar índice de pestaña")))
        drag = self._check("drag_to_front", tr("Al arrastrar archivos sobre una pestaña, traerla al frente tras:"))
        self.drag_delay = _spin(settings["drag_delay_ms"], 0, 10000, " ms")
        self.drag_delay.setEnabled(drag.isChecked())
        drag.toggled.connect(self.drag_delay.setEnabled)
        row = QHBoxLayout()
        row.addWidget(drag)
        row.addWidget(self.drag_delay)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self._check("tab_as_folder", tr("Tratar la pestaña como carpeta al arrastrar o con clic derecho")))
        layout.addSpacing(8)
        self.selected_click = _combo(((tr("Renombrar pestaña"), "rename"), (tr("Ir a la pestaña anterior"), "previous"), (tr("No hacer nada"), "nothing")),
                                     settings["selected_click"])
        click_row = QHBoxLayout()
        click_row.addWidget(QLabel(tr("Al hacer clic en la pestaña seleccionada:")))
        click_row.addWidget(self.selected_click)
        click_row.addStretch(1)
        layout.addLayout(click_row)
        layout.addSpacing(8)
        layout.addWidget(QLabel(tr("<b>Al cerrar pestañas</b>")))
        layout.addWidget(self._check("double_click_close", tr("Doble clic para cerrar una pestaña")))
        layout.addWidget(QLabel(tr("También se cierran haciendo clic en ellas con el botón central del ratón.")))
        show_close = self._check("show_close", tr("Mostrar botón Cerrar en cada pestaña"))
        for child in (self._check("close_active_only", tr("Solo en la pestaña activa")), self._check("small_close", tr("Usar botones pequeños"))):
            child.setEnabled(show_close.isChecked())
            show_close.toggled.connect(child.setEnabled)
        layout.addWidget(show_close)
        layout.addLayout(_indented(self.checks["close_active_only"]))
        layout.addLayout(_indented(self.checks["small_close"]))
        layout.addWidget(self._check("window_close_closes_tab", tr("El botón Cerrar de la ventana cierra la pestaña activa")))
        layout.addWidget(self._check("close_window_with_last_tab", tr("Cerrar la ventana al cerrar la última pestaña")))
        layout.addStretch(1)
        return page

    def values(self) -> dict:
        values = {key: box.isChecked() for key, box in self.checks.items()}
        values.update({
            "new_button_action": self.new_button_action.currentData(),
            "new_button_path": self.new_button_path.text(),
            "double_click_action": self.double_click_action.currentData(),
            "double_click_path": self.double_click_path.text(),
            "default_folder": self.default_folder.text(),
            "show_bar": self.show_bar.currentData(),
            "position": self.position.currentData(),
            "dual_position": self.dual_position.currentData(),
            "same_size_mode": "fixed" if self.same_fixed.isChecked() else "auto",
            "fixed_size": self.fixed_size.value(),
            "max_size": self.max_size.value(),
            "min_size": self.min_size.value(),
            "vertical_auto_width": self.vertical_auto.isChecked(),
            "vertical_width": self.vertical_width.value(),
            "drag_delay_ms": self.drag_delay.value(),
            "selected_click": self.selected_click.currentData(),
        })
        return tab_settings.normalize(values)
