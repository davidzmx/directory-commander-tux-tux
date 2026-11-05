"""Montar, desmontar y expulsar dispositivos con UDisks2 por D-Bus, como lo hace el escritorio. Si el sistema lo exige, él mismo muestra su
ventana para pedir la contraseña (polkit), sin que aquí se vea ni se guarde.

La conexión con el bus del sistema se abre una vez y se conserva: el sistema limita cuántas puede tener abiertas cada usuario, y si algún
programa las acapara, `udisksctl`, que abre una nueva cada vez, ya no puede conectar. Las llamadas son asíncronas: mientras se espera
la contraseña la ventana sigue respondiendo."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject

from ..core.i18n import tr

try:
    from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
except ImportError:  # Windows, o un Qt sin D-Bus
    QDBusConnection = None

SERVICE = "org.freedesktop.UDisks2"
BLOCKS = "/org/freedesktop/UDisks2/block_devices/"
FILESYSTEM = "org.freedesktop.UDisks2.Filesystem"
CALL_TIMEOUT_MS = 10 * 60 * 1000  # puede estar esperando a que se escriba la contraseña
DISMISSED = "NotAuthorizedDismissed"  # se cerró la ventana de la contraseña: no es un error que haya que contar


class Cancelled(Exception):
    """Se canceló la petición de la contraseña."""


def object_path(device: str) -> str:
    """/dev/nvme0n1p2 → …/block_devices/nvme0n1p2. UDisks escribe lo que no es letra ni número como _ y su código."""
    name = os.path.basename(device)
    return BLOCKS + "".join(char if char.isascii() and char.isalnum() else f"_{ord(char):02x}" for char in name)


def _plain(value):
    """Lo que devuelve D-Bus en una variante o una ruta de objeto, como texto o número de Python."""
    for unwrap in ("variant", "path"):
        if hasattr(value, unwrap):
            value = getattr(value, unwrap)()
    return value


class UDisks(QObject):
    def __init__(self, parent=None, bus_type=None, service: str = SERVICE) -> None:
        super().__init__(parent)
        self._bus_type = bus_type
        self._service = service
        self._bus = None
        self._attempt = 0
        self._watchers: set = set()

    @staticmethod
    def supported() -> bool:
        return QDBusConnection is not None

    def bus(self):
        """La conexión, abriéndola si aún no lo está. Una que falló se vuelve a intentar la próxima vez, con otro nombre: Qt no reintenta la misma."""
        if self._bus is not None and self._bus.isConnected():
            return self._bus
        if QDBusConnection is None:
            return None
        self._attempt += 1
        name = f"directory-commander-tux-tux-udisks-{id(self)}-{self._attempt}"
        bus_type = QDBusConnection.BusType.SystemBus if self._bus_type is None else self._bus_type
        bus = QDBusConnection.connectToBus(bus_type, name)
        if bus.isConnected():
            self._bus = bus
            return bus
        self._last_error = bus.lastError().message()
        QDBusConnection.disconnectFromBus(name)
        return None

    def connection_error(self) -> str:
        return getattr(self, "_last_error", "") or tr("No hay conexión con el bus del sistema")

    def _call(self, path: str, interface: str, method: str, arguments: list, done, failed) -> None:
        bus = self.bus()
        if bus is None:
            failed(OSError(self.connection_error()))
            return
        message = QDBusMessage.createMethodCall(self._service, path, interface, method)
        message.setArguments(arguments)
        watcher = QDBusPendingCallWatcher(bus.asyncCall(message, CALL_TIMEOUT_MS), self)
        self._watchers.add(watcher)
        watcher.finished.connect(lambda finished: self._finished(finished, done, failed))

    def _finished(self, watcher, done, failed) -> None:
        self._watchers.discard(watcher)
        reply = watcher.reply()
        watcher.deleteLater()
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            if reply.errorName().endswith(DISMISSED):
                failed(Cancelled())
            else:
                failed(OSError(reply.errorMessage() or reply.errorName()))
            return
        done([_plain(value) for value in reply.arguments()])

    # ---------------------------------------------------------------- lo que se puede hacer
    def mount(self, device: str, done, failed) -> None:
        """`done(dónde)`."""
        self._call(object_path(device), FILESYSTEM, "Mount", [{}], lambda values: done(str(values[0]) if values else ""), failed)

    def unmount(self, device: str, done, failed) -> None:
        self._call(object_path(device), FILESYSTEM, "Unmount", [{}], lambda _values: done(), failed)

    def power_off(self, device: str, done, failed) -> None:
        """Apaga el disco de ese dispositivo, para quitarlo con seguridad. Lo que tenga montado debe estar ya desmontado."""
        def with_drive(values) -> None:
            drive = str(values[0]) if values else ""
            if not drive or drive == "/":
                failed(OSError(tr("No se sabe a qué disco pertenece {device}", device=device)))
                return
            self._call(drive, "org.freedesktop.UDisks2.Drive", "PowerOff", [{}], lambda _values: done(), failed)

        self._call(object_path(device), "org.freedesktop.DBus.Properties", "Get", ["org.freedesktop.UDisks2.Block", "Drive"], with_drive, failed)
