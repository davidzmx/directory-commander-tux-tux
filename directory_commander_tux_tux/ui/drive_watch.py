"""Avisa cuando se conecta o se quita una unidad, como un USB, o cambia su espacio libre. Windows no lo dice a quien solo mira
las carpetas: se pregunta cada poco, y solo mientras algo lo necesita, como una pestaña en «Este equipo» o el árbol."""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import QObject, QTimer, Signal

from ..core import drives
from ..core.listing import has_computer_view
from .background import run_bg

CHECK_EVERY_MS = 2000


class DriveWatcher(QObject):
    changed = Signal(dict)  # unidad → (total, libre), o None si no se puede consultar

    def __init__(self) -> None:
        super().__init__()
        self.last: dict | None = None
        # Solo se avisa a quien mira ahora: avisar a todas las listas y los árboles que se han creado, cada vez que cambia el espacio
        # libre de una unidad, que es a cada momento, sería trabajo en balde.
        self._users: dict[int, tuple[QObject, object]] = {}
        self._busy = False
        self._timer = QTimer(self, interval=CHECK_EVERY_MS)
        self._timer.timeout.connect(self._check)
        self.changed.connect(self._tell)

    def watch(self, user: QObject, callback) -> None:
        if not has_computer_view() or id(user) in self._users:
            return
        self._users[id(user)] = (user, callback)
        user.destroyed.connect(lambda _object=None, key=id(user): self._forget(key))
        if not self._timer.isActive():
            self._timer.start()

    def unwatch(self, user: QObject) -> None:
        self._forget(id(user))

    def _forget(self, key: int) -> None:
        self._users.pop(key, None)
        if not self._users:
            self._timer.stop()

    def _tell(self, found: dict) -> None:
        for key, (user, callback) in list(self._users.items()):
            if shiboken6.isValid(user):
                callback(found)
            else:
                self._forget(key)

    def _check(self) -> None:
        if self._busy:
            return  # una unidad lenta en responder: no se acumulan las consultas
        self._busy = True

        def done(found: dict) -> None:
            self._busy = False
            if found != self.last:
                first = self.last is None
                self.last = found
                if not first:  # la primera vez solo se apunta cómo está
                    self.changed.emit(found)

        run_bg(lambda _sink: drives.drive_space(), on_done=done, on_error=lambda _exc: setattr(self, "_busy", False))


_watcher: DriveWatcher | None = None


def watcher() -> DriveWatcher:
    global _watcher
    if _watcher is None:
        _watcher = DriveWatcher()
    return _watcher
