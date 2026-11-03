"""El lado de cada panel: el propio panel, su terminal y su árbol, juntos dentro de un marco de color si se pide."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QMargins, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter


class FramedSplitter(QSplitter):
    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._color: str | None = None
        self._room = 0
        self._line = 0
        self._alpha = 255

    @property
    def frame_color(self) -> str | None:
        return self._color

    @property
    def frame_line(self) -> int:
        return self._line if self._color else 0

    @property
    def frame_alpha(self) -> int:
        return self._alpha

    def set_frame(self, color: str | None, room: int, line: int | None = None, alpha: int = 255) -> None:
        """`room` es el sitio que se deja alrededor, el mismo en todos los paneles para que nada se mueva al cambiar de activo;
        `line`, lo que se pinta de él. Sin color, sin marco: lo de dentro vuelve a ocupar todo el sitio."""
        room = room if color else 0
        line = min(room, room if line is None else line)
        if (color, room, line, alpha) == (self._color, self._room, self._line, self._alpha):
            return
        self._color, self._room, self._line, self._alpha = color, room, line, alpha
        self._keep_room()
        self.update()

    def _keep_room(self) -> None:
        if self.contentsMargins() != QMargins(self._room, self._room, self._room, self._room):
            self.setContentsMargins(self._room, self._room, self._room, self._room)
            self.refresh()

    def event(self, event) -> bool:
        handled = super().event(event)
        # Al cambiar de sitio, Qt le vuelve a aplicar la hoja de estilos, y eso le quita el margen del marco.
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange, QEvent.Type.ParentChange, QEvent.Type.Show):
            self._keep_room()
        return handled

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._color or not self._line:
            return
        painter = QPainter(self)
        color = QColor(self._color)
        color.setAlpha(self._alpha)
        width, height, line = self.width(), self.height(), self._line
        for rect in (QRect(0, 0, width, line), QRect(0, height - line, width, line), QRect(0, line, line, height - 2 * line), QRect(width - line, line, line, height - 2 * line)):
            painter.fillRect(rect, color)
