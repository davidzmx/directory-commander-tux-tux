"""Montar, desmontar y expulsar por D-Bus. Un UDisks de mentira en el bus de la sesión: el del sistema montaría discos de verdad."""
import os
import sys
import time

import pytest

QtDBus = pytest.importorskip("PySide6.QtDBus")
from PySide6.QtCore import ClassInfo, Property, QObject, Slot  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from directory_commander_tux_tux.ui import udisks  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="UDisks es de Linux")
QDBusConnection = QtDBus.QDBusConnection
REAL_BUS = udisks.UDisks.bus  # conftest la anula en todas las pruebas; aquí se usa, con el bus de la sesión


@ClassInfo({"D-Bus Interface": "org.freedesktop.UDisks2.Filesystem"})
class _Filesystem(QObject):
    def __init__(self, calls):
        super().__init__()
        self.calls = calls

    @Slot("QVariantMap", result=str)
    def Mount(self, options):
        self.calls.append(("mount", dict(options)))
        return "/media/ana/USB"

    @Slot("QVariantMap")
    def Unmount(self, options):
        self.calls.append(("unmount", dict(options)))


@ClassInfo({"D-Bus Interface": "org.freedesktop.UDisks2.Block"})
class _Block(QObject):
    def _drive(self):
        return "/org/freedesktop/UDisks2/drives/USB_1"

    Drive = Property(str, _drive)


@ClassInfo({"D-Bus Interface": "org.freedesktop.UDisks2.Drive"})
class _Drive(QObject):
    def __init__(self, calls):
        super().__init__()
        self.calls = calls

    @Slot("QVariantMap")
    def PowerOff(self, options):
        self.calls.append(("power-off", dict(options)))


@pytest.fixture
def fake_udisks(monkeypatch):
    monkeypatch.setattr(udisks.UDisks, "bus", REAL_BUS)
    app = QApplication.instance() or QApplication([])
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        pytest.skip("no hay bus de sesión")
    service = f"org.directorycommander.TestUDisks{os.getpid()}"
    assert bus.registerService(service)
    calls = []
    keep = [_Filesystem(calls), _Block(), _Drive(calls)]
    # En UDisks un mismo objeto ofrece varias interfaces; aquí cada una va en un dispositivo distinto, que basta para probar cada llamada.
    assert bus.registerObject(udisks.object_path("/dev/sdb1"), keep[0], QDBusConnection.RegisterOption.ExportAllSlots)
    assert bus.registerObject(udisks.object_path("/dev/sdb2"), keep[1], QDBusConnection.RegisterOption.ExportAllProperties)
    assert bus.registerObject("/org/freedesktop/UDisks2/drives/USB_1", keep[2], QDBusConnection.RegisterOption.ExportAllSlots)
    client = udisks.UDisks(bus_type=QDBusConnection.BusType.SessionBus, service=service)
    yield client, calls, app
    for path in (udisks.object_path("/dev/sdb1"), udisks.object_path("/dev/sdb2"), "/org/freedesktop/UDisks2/drives/USB_1"):
        bus.unregisterObject(path)
    bus.unregisterService(service)


def _wait(app, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        app.processEvents()
        time.sleep(0.01)
    return condition()


def test_object_paths_follow_udisks_naming():
    assert udisks.object_path("/dev/nvme0n1p2") == "/org/freedesktop/UDisks2/block_devices/nvme0n1p2"
    assert udisks.object_path("/dev/dm-0") == "/org/freedesktop/UDisks2/block_devices/dm_2d0"


def test_mount_unmount_and_errors_over_dbus(fake_udisks):
    client, calls, app = fake_udisks
    results = []
    client.mount("/dev/sdb1", results.append, lambda exc: results.append(exc))
    client.unmount("/dev/sdb1", lambda: results.append("desmontado"), lambda exc: results.append(exc))
    client.mount("/dev/sdz9", results.append, lambda exc: results.append(exc))
    assert _wait(app, lambda: len(results) == 3)
    # Las respuestas llegan cuando el servicio contesta, no necesariamente en el orden de las preguntas.
    errors = [result for result in results if isinstance(result, OSError)]
    assert sorted(result for result in results if isinstance(result, str)) == ["/media/ana/USB", "desmontado"]
    assert len(errors) == 1 and "sdz9" in str(errors[0])  # no existe
    assert sorted(calls) == [("mount", {}), ("unmount", {})]


def test_power_off_finds_the_drive_of_the_device(fake_udisks):
    client, calls, app = fake_udisks
    done = []
    client.power_off("/dev/sdb2", lambda: done.append(True), lambda exc: done.append(exc))
    assert _wait(app, lambda: done)
    assert done == [True] and calls == [("power-off", {})]


def test_a_bus_that_cannot_connect_is_reported(monkeypatch):
    QApplication.instance() or QApplication([])
    client = udisks.UDisks()
    monkeypatch.setattr(client, "bus", lambda: None)
    failed = []
    client.mount("/dev/sdb1", lambda point: failed.append(point), failed.append)
    assert isinstance(failed[0], OSError)
