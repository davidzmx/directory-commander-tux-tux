"""Lo común al arrastrar archivos: empezar el arrastre y decidir qué hace soltarlos en cada sitio."""
from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ..core import fsops, remote

DRAG_ICON = 32
_own_drag_with_right_button = False


def path_url(path: str) -> QUrl:
    """La dirección de un elemento para arrastrarlo: un archivo del equipo o, de un panel remoto, una sftp://."""
    if not remote.is_remote(path):
        return QUrl.fromLocalFile(path)
    user, host, port = remote.server_parts(remote.server_of(path))
    url = QUrl()
    url.setScheme("sftp")
    url.setUserName(user)
    url.setHost(host)
    url.setPort(port)
    url.setPath(remote.inner_of(path), QUrl.ParsingMode.DecodedMode)
    return url


def url_path(url: QUrl) -> str | None:
    if url.isLocalFile():
        return os.path.normpath(url.toLocalFile())
    if url.scheme() == "sftp" and url.host():
        server = remote.server_key(url.userName(), url.host(), url.port(remote.DEFAULT_PORT))
        return remote.make_path(server, url.path(QUrl.ComponentFormattingOption.FullyDecoded) or "/")
    return None


def dropped_paths(mime) -> list[str]:
    return [path for path in (url_path(url) for url in mime.urls()) if path] if mime.hasUrls() else []


class DropDecider:
    """Qué hará soltar lo arrastrado. Comprobarlo lee el disco, y durante el arrastre se pregunta sin parar:
    se recuerda la respuesta para cada carpeta y combinación de teclas."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self._answers: dict[tuple, str | None] = {}
        self._right_button = False

    def enter(self, event) -> bool:
        """Empieza un arrastre sobre este sitio."""
        self._right_button = False
        return self.begin(event)

    def begin(self, event) -> bool:
        self.paths = dropped_paths(event.mimeData())
        self._answers.clear()
        self._note_buttons(event)
        return bool(self.paths)

    def _note_buttons(self, event) -> None:
        # Al soltar, el botón ya no está pulsado: se recuerda mientras dura el arrastre.
        self._right_button = self._right_button or bool(event.buttons() & Qt.MouseButton.RightButton)

    def take_right_button(self) -> bool:
        """Si se arrastró con el botón derecho. Se pregunta una vez, al soltar."""
        pressed = self._right_button or _own_drag_with_right_button
        self._right_button = False
        return pressed

    def operation(self, event, destination: str) -> str | None:
        """"move", "copy" o None si soltar ahí no hace nada. Mayús obliga a mover y Ctrl a copiar."""
        self._note_buttons(event)
        modifiers = event.modifiers()
        force_move = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        force_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        key = (destination, force_move, force_copy)
        if key not in self._answers:
            self._answers[key] = fsops.drop_operation(self.paths, destination, force_move, force_copy)
        answer = self._answers[key]
        if answer == "move" and not event.possibleActions() & Qt.DropAction.MoveAction:
            return "copy"  # quien arrastra no permite mover
        return answer


def show_operation(event, operation: str | None) -> None:
    """Durante el arrastre: el cursor indica lo que va a pasar."""
    if operation is None:
        event.ignore()
    else:
        event.setDropAction(Qt.DropAction.MoveAction if operation == "move" else Qt.DropAction.CopyAction)
        event.accept()


def accept_drop(event) -> None:
    """Al soltar, se dice siempre «copia» aunque se vaya a mover: el trabajo lo hace este programa, y con «mover»
    otro programa podría borrar sus archivos antes de que terminara la operación."""
    if event.possibleActions() & Qt.DropAction.CopyAction:
        event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()


def _drag_picture(icon: QIcon, count: int) -> QPixmap:
    picture = icon.pixmap(DRAG_ICON, DRAG_ICON) if not icon.isNull() else QPixmap()
    if picture.isNull():
        picture = QPixmap(DRAG_ICON, DRAG_ICON)
        picture.fill(Qt.GlobalColor.transparent)
    if count > 1:
        painter = QPainter(picture)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2a6fb8"))
        area = picture.rect().adjusted(picture.width() // 2 - 2, picture.height() // 2 - 2, 0, 0)
        painter.drawEllipse(area)
        painter.setPen(QColor("white"))
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, str(count))
        painter.end()
    return picture


def start_drag(source: QWidget, mime: QMimeData, icon: QIcon = QIcon()) -> None:
    """Empieza el arrastre. Lo que se hace al soltar lo decide el destino, así que el resultado no se usa."""
    count = len(mime.urls())
    if not count:
        return
    drag = QDrag(source)
    drag.setMimeData(mime)
    picture = _drag_picture(icon, count)
    drag.setPixmap(picture)
    drag.setHotSpot(QPoint(picture.width() // 2, picture.height() // 2))
    global _own_drag_with_right_button
    _own_drag_with_right_button = bool(QApplication.mouseButtons() & Qt.MouseButton.RightButton)
    try:
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)
    finally:
        _own_drag_with_right_button = False


def folder_mime(path: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([path_url(path)])
    return mime
