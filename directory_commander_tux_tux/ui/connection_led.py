"""El foquito verde de una conexión SSH/SFTP activa. Parpadea mientras sigue viva; si se pierde, desaparece."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

BLINK_MS = 600
LED_SIZE = 12
# (brillo, color, borde) encendido y en la otra mitad del parpadeo: más oscuro, pero sin apagarse del todo, como un piloto.
_LIT = ("#eaffe4", "#36d936", "#137a13")
_DIM = ("#5f9a5f", "#1d6b1d", "#0f3f0f")


def paint_led(painter: QPainter, rect: QRectF, lit: bool) -> None:
    """Un circulito verde con su brillo, como una bombilla pequeña."""
    size = min(rect.width(), rect.height())
    circle = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
    shine, body, edge = _LIT if lit else _DIM
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QRadialGradient(circle.center() - QPointF(size * 0.18, size * 0.18), size * 0.62)
    gradient.setColorAt(0.0, QColor(shine))
    gradient.setColorAt(0.45, QColor(body))
    gradient.setColorAt(1.0, QColor(edge))
    painter.setPen(QPen(QColor(edge), 0.8))
    painter.setBrush(gradient)
    painter.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
    painter.restore()


class ConnectionLed(QWidget):
    """El foquito de una pestaña conectada a un servidor."""

    def __init__(self, server: str, parent=None) -> None:
        super().__init__(parent)
        self.server = server
        self.lit = True
        self.setFixedSize(LED_SIZE + 4, LED_SIZE)
        self.setToolTip(server)

    def set_lit(self, lit: bool) -> None:
        if lit != self.lit:
            self.lit = lit
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        paint_led(painter, QRectF(2, 0, LED_SIZE, LED_SIZE).adjusted(1, 1, -1, -1), self.lit)
