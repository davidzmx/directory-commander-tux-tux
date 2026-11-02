"""Un botón que muestra un color y permite elegir otro. Vacío significa el color de serie, que depende del tema."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton

from ..core.i18n import tr


class ColorButton(QPushButton):
    chosen = Signal()  # solo cuando lo elige el usuario

    def __init__(self, title: str, value: str, default: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.value = value
        self.default = default
        self.setFixedSize(QSize(46, 22))
        self.clicked.connect(self.choose)
        self._show()

    def _show(self) -> None:
        shown = self.value or self.default
        self.setStyleSheet(f"QPushButton {{ background: {shown}; border: 1px solid palette(mid); border-radius: 2px; }}")
        self.setToolTip(f"{self.title}: {shown}" + ("" if self.value else f" ({tr('por defecto')})"))

    def set_value(self, value: str) -> None:
        self.value = value
        self._show()

    def set_default(self, default: str) -> None:
        self.default = default
        self._show()

    def choose(self) -> None:
        color = QColorDialog.getColor(QColor(self.value or self.default), self, self.title)
        if color.isValid():
            self.set_value(color.name())
            self.chosen.emit()
