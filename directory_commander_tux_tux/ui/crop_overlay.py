"""Selección de una parte de la imagen en el visor: se dibuja, se mueve y se ajusta con el ratón."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

HANDLE_SIZE = 8
GRAB_DISTANCE = 6
DIMMED = QColor(0, 0, 0, 140)
_CURSORS = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.SizeAllCursor}


class CropOverlay(QWidget):
    """Capa sobre la imagen. La selección se guarda en píxeles de la imagen: así no cambia con el zoom."""

    selectionChanged = Signal()
    confirmed = Signal()  # doble clic dentro de la selección

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self.label = label
        self.image_size = QSize()
        self.selection = QRect()
        self._grabbed = ""  # "new", "move" o un borde: n, e, s, w y sus esquinas
        self._grabbed_at = QPointF()
        self._start = QRect()
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_selection(self, area: QRect) -> None:
        self.selection = area
        self.update()
        self.selectionChanged.emit()

    def image_on_screen(self) -> QRectF:
        """Dónde se ve la imagen: el rótulo la centra."""
        shown = self.label.pixmap()
        if shown is None or shown.isNull():
            return QRectF()
        return QRectF((self.label.width() - shown.width()) // 2, (self.label.height() - shown.height()) // 2, shown.width(), shown.height())

    def to_image(self, position: QPointF) -> QPointF:
        """Punto de la imagen bajo ese punto de la pantalla, sin salirse de ella."""
        screen = self.image_on_screen()
        if screen.isEmpty():
            return QPointF()
        x = (position.x() - screen.x()) * self.image_size.width() / screen.width()
        y = (position.y() - screen.y()) * self.image_size.height() / screen.height()
        return QPointF(max(0.0, min(x, self.image_size.width())), max(0.0, min(y, self.image_size.height())))

    def selection_on_screen(self) -> QRectF:
        screen = self.image_on_screen()
        if screen.isEmpty() or self.image_size.isEmpty():
            return QRectF()
        scale_x, scale_y = screen.width() / self.image_size.width(), screen.height() / self.image_size.height()
        return QRectF(screen.x() + self.selection.x() * scale_x, screen.y() + self.selection.y() * scale_y,
                      self.selection.width() * scale_x, self.selection.height() * scale_y)

    def _zone(self, position: QPointF) -> str:
        """Qué parte de la selección hay bajo el puntero: un borde, una esquina, el interior o nada."""
        if self.selection.isEmpty():
            return ""
        area = self.selection_on_screen()
        near = lambda a, b: abs(a - b) <= GRAB_DISTANCE
        inside_x = area.left() - GRAB_DISTANCE <= position.x() <= area.right() + GRAB_DISTANCE
        inside_y = area.top() - GRAB_DISTANCE <= position.y() <= area.bottom() + GRAB_DISTANCE
        vertical = "n" if near(position.y(), area.top()) and inside_x else "s" if near(position.y(), area.bottom()) and inside_x else ""
        horizontal = "w" if near(position.x(), area.left()) and inside_y else "e" if near(position.x(), area.right()) and inside_y else ""
        if vertical or horizontal:
            return vertical + horizontal
        return "move" if area.contains(position) else ""

    def _dragged_area(self, point: QPointF) -> QRect:
        x, y = round(point.x()), round(point.y())
        if self._grabbed == "new":
            anchor = self._start.topLeft()
            return QRect(min(anchor.x(), x), min(anchor.y(), y), abs(x - anchor.x()), abs(y - anchor.y()))
        start = self._start
        if self._grabbed == "move":
            moved_x = round(point.x() - self._grabbed_at.x())
            moved_y = round(point.y() - self._grabbed_at.y())
            return QRect(max(0, min(start.x() + moved_x, self.image_size.width() - start.width())),
                         max(0, min(start.y() + moved_y, self.image_size.height() - start.height())), start.width(), start.height())
        left, top = start.x(), start.y()
        right, bottom = left + start.width(), top + start.height()
        if "w" in self._grabbed:
            left = x
        if "e" in self._grabbed:
            right = x
        if "n" in self._grabbed:
            top = y
        if "s" in self._grabbed:
            bottom = y
        return QRect(min(left, right), min(top, bottom), abs(right - left), abs(bottom - top))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        position = event.position()
        point = self.to_image(position)
        self._grabbed = self._zone(position) or "new"
        self._grabbed_at = point
        if self._grabbed == "new":
            self._start = QRect(point.toPoint(), QSize(0, 0))
            self.set_selection(QRect())
        else:
            self._start = QRect(self.selection)

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        if self._grabbed:
            self.set_selection(self._dragged_area(self.to_image(position)))
        else:
            self.setCursor(_CURSORS.get(self._zone(position), Qt.CursorShape.CrossCursor))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grabbed = ""

    def mouseDoubleClickEvent(self, event) -> None:
        if self._zone(event.position()) == "move":
            self.confirmed.emit()

    def paintEvent(self, _event) -> None:
        if self.selection.isEmpty():
            return
        painter = QPainter(self)
        area = self.selection_on_screen()
        screen = self.image_on_screen()
        for part in (QRectF(screen.left(), screen.top(), screen.width(), area.top() - screen.top()),
                     QRectF(screen.left(), area.bottom(), screen.width(), screen.bottom() - area.bottom()),
                     QRectF(screen.left(), area.top(), area.left() - screen.left(), area.height()),
                     QRectF(area.right(), area.top(), screen.right() - area.right(), area.height())):
            painter.fillRect(part, DIMMED)
        painter.setPen(QPen(QColor("white"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(area)
        painter.setPen(QPen(QColor("black"), 1))
        painter.setBrush(QColor("white"))
        half = HANDLE_SIZE / 2
        for x in (area.left(), area.center().x(), area.right()):
            for y in (area.top(), area.center().y(), area.bottom()):
                if QPointF(x, y) != area.center():
                    painter.drawRect(QRectF(x - half, y - half, HANDLE_SIZE, HANDLE_SIZE))
        text = f"{self.selection.width()} x {self.selection.height()}"
        box = painter.fontMetrics().boundingRect(text).adjusted(-4, -2, 4, 2)
        box.moveTopLeft(QPoint(round(area.left()) + 6, max(2, round(area.top()) - box.height() - 6)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(DIMMED)
        painter.drawRect(box)
        painter.setPen(QColor("white"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
