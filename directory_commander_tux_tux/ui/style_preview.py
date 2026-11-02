"""El esquema de un estilo en su editor: cómo quedaría la ventana, con los colores reales de los paneles."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..core import styles
from ..core.i18n import tr
from . import theme

_BAR_HEIGHT = 9
_GAP = 2


class StylePreview(QWidget):
    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.full_width = bool(config.get("general_terminal_full_width", False))
        self.viewer_above = bool(config.get("viewer_above_metadata", True))
        self.regions: list[styles.Region] = []
        self.pane_notes: dict[int, list[str]] = {}  # lo que se escribe dentro de cada panel, debajo de su nombre
        self.setMinimumHeight(150)
        self.setToolTip(tr("Así quedaría la ventana con este estilo"))

    def set_style(self, style: dict) -> None:
        self.regions = styles.preview_regions(style, self.full_width, self.viewer_above)
        self.update()

    def set_session(self, layout: dict, tabs: dict[int, list[str]]) -> None:
        self.pane_notes = tabs
        self.setToolTip(tr("Así quedará la ventana con esta sesión"))
        self.set_style(layout)

    def paintEvent(self, _event) -> None:
        colors = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        whole = QRectF(self.rect())
        painter.fillRect(whole, QColor(colors.window))
        for region in self.regions:
            rect = QRectF(whole.x() + region.x * whole.width() + _GAP / 2, whole.y() + region.y * whole.height() + _GAP / 2,
                          max(1.0, region.width * whole.width() - _GAP), max(1.0, region.height * whole.height() - _GAP))
            self._paint_region(painter, region, rect, colors)
        painter.setPen(QColor(colors.border))
        painter.drawRect(whole.adjusted(0, 0, -1, -1))

    def _paint_region(self, painter: QPainter, region: styles.Region, rect: QRectF, colors) -> None:
        if region.kind == styles.PANE:
            role = ("source", "dest", "other", "other")[region.index]
            painter.fillRect(rect, QColor(getattr(colors, f"list_{role}")))
            bar = QRectF(rect.x(), rect.y(), rect.width(), min(_BAR_HEIGHT, rect.height()))
            painter.fillRect(bar, QColor(getattr(colors, f"bar_{role}")))
            painter.fillRect(bar.adjusted(bar.width() * 0.2, 0, -bar.width() * 0.15, 0), QColor(theme.crumb_color(colors, role)))
            label = tr("Panel {number}", number=region.index + 1)
        elif region.kind in (styles.TERMINAL, styles.GENERAL):
            painter.fillRect(rect, QColor(colors.field))
            label = tr("Terminal general") if region.kind == styles.GENERAL else tr("Terminal")
        else:
            painter.fillRect(rect, QColor(colors.panel))
            label = {styles.TREE: tr("Árbol"), styles.PANE_TREE: tr("Árbol"), styles.VIEWER: tr("Visor"), styles.META: tr("Metadatos")}[region.kind]
        painter.setPen(QColor(colors.border))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(QColor(colors.text))
        metrics = painter.fontMetrics()
        notes = self.pane_notes.get(region.index, []) if region.kind == styles.PANE else []
        if notes:
            self._paint_notes(painter, label, notes, rect.adjusted(3, _BAR_HEIGHT + 2, -3, -2), colors)
        elif metrics.horizontalAdvance(label) < rect.width() - 4 and metrics.height() < rect.height():
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _paint_notes(painter: QPainter, label: str, notes: list[str], rect: QRectF, colors) -> None:
        """El nombre del panel y sus pestañas, tantas como quepan."""
        metrics = painter.fontMetrics()
        rows = int(rect.height() // metrics.height())
        lines = [label, *notes]
        if len(lines) > rows > 0:
            lines = [*lines[:rows - 1], tr("… y {count} más", count=len(lines) - rows + 1)]
        for position, line in enumerate(lines[:max(rows, 0)]):
            painter.setPen(QColor(colors.text if position == 0 else colors.dim))
            row = QRectF(rect.x(), rect.y() + position * metrics.height(), rect.width(), metrics.height())
            painter.drawText(row, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(line, Qt.TextElideMode.ElideMiddle, int(rect.width())))
