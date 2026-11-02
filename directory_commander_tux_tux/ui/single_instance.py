"""Abrir una carpeta desde fuera como pestaña de la ventana que ya está abierta, en vez de en otra ventana."""
from __future__ import annotations

import ctypes
import hashlib
import json
import sys

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_WAIT_MS = 1500
_PROBE_MS = 200


def server_name(config_folder: str) -> str:
    """Una por cada configuración: quien usa otra configuración es otro programa, a efectos de esto."""
    return "directory-commander-tux-tux-" + hashlib.sha256(config_folder.encode("utf-8", "replace")).hexdigest()[:16]


def send_to_running(name: str, paths: list[str]) -> bool:
    """True si ya había una ventana abierta y las ha recibido."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(_WAIT_MS):
        return False
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # si no, Windows no deja que la otra ventana se ponga delante
    socket.write(json.dumps(paths).encode("utf-8"))
    socket.flush()
    sent = socket.bytesToWrite() == 0 or socket.waitForBytesWritten(_WAIT_MS)  # si la otra ventana está bloqueada, mejor abrir una nueva que perder la carpeta
    socket.disconnectFromServer()
    return sent


class PathServer(QObject):
    """Recibe las rutas de los que se abren después."""

    def __init__(self, name: str, on_paths, parent=None) -> None:
        super().__init__(parent)
        self._on_paths = on_paths
        self._server = QLocalServer(self)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._server.newConnection.connect(self._accept)
        probe = QLocalSocket()
        probe.connectToServer(name)
        if probe.waitForConnected(_PROBE_MS):  # ya hay otra ventana recibiendo: sigue siendo ella, y a esta no le llega nada
            probe.disconnectFromServer()
            self.listening = False
            return
        QLocalServer.removeServer(name)  # en Linux queda el archivo si la ventana anterior se cerró de golpe
        self.listening = self._server.listen(name)

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._finish(s))
            if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._finish(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if socket in self._buffers:
            self._buffers[socket].extend(bytes(socket.readAll()))

    def _finish(self, socket: QLocalSocket) -> None:
        data = self._buffers.pop(socket, None)
        if data is None:
            return
        data.extend(bytes(socket.readAll()))
        socket.deleteLater()
        try:
            paths = json.loads(bytes(data).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(paths, list):
            self._on_paths([path for path in paths if isinstance(path, str)])
