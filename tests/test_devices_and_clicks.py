"""Los dispositivos de Linux en el árbol, como en el panel de lugares del escritorio, y el modo de abrir con un clic."""
import json
import os
import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem

from directory_commander_tux_tux.core import devices
from directory_commander_tux_tux.ui.folder_tree import PATH_ROLE, USAGE_ROLE, FolderTree
from directory_commander_tux_tux.ui.model import EntryRole
from tests.test_ui import app, tree, wait_until, window  # noqa: F401 - se usan como fixtures

linux_only = pytest.mark.skipif(sys.platform == "win32", reason="en Windows las unidades están en «Este equipo»")


def part(name, fstype="ext4", label=None, partlabel=None, mounts=(), parttype="0fc63daf-8483-4772-8e79-3d69d8477de4", size=10 ** 9, **extra):
    return {"path": f"/dev/{name}", "name": name, "type": "part", "fstype": fstype, "label": label, "partlabel": partlabel, "size": size,
            "mountpoints": list(mounts) or [None], "rm": False, "hotplug": False, "parttype": parttype, **extra}


# Como el de un equipo con Windows y Linux en dos discos, un disco de datos y un USB.
LSBLK = json.dumps({"blockdevices": [
    {"path": "/dev/loop0", "name": "loop0", "type": "loop", "fstype": "squashfs", "mountpoints": ["/snap/core/1"], "size": 4096},
    {"path": "/dev/sda", "name": "sda", "type": "disk", "fstype": None, "size": 2 * 10 ** 12, "rm": False, "hotplug": False,
     "children": [part("sda1", "ntfs", "WD_SSD", parttype="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")]},
    {"path": "/dev/sdb", "name": "sdb", "type": "disk", "fstype": None, "size": 16 * 10 ** 9, "rm": True, "hotplug": True,
     "children": [part("sdb1", "vfat", "USB", mounts=["/media/ana/USB"], parttype="0xc")]},
    {"path": "/dev/nvme0n1", "name": "nvme0n1", "type": "disk", "fstype": None, "size": 2 * 10 ** 12, "rm": False, "hotplug": False, "children": [
        part("nvme0n1p1", "", partlabel="Microsoft reserved partition", parttype="e3c9e316-0b5c-4db8-817d-f92df00215ae"),
        part("nvme0n1p2", "ntfs", "Windows 11", parttype="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"),
        part("nvme0n1p3", "vfat", mounts=["/boot/efi"], parttype="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"),
        part("nvme0n1p4", "ext4", partlabel="Linux filesystem data", mounts=["/"]),
        part("nvme0n1p5", "swap", mounts=["[SWAP]"]),
        part("nvme0n1p6", "ntfs", "BIOS_RVY", parttype="de94bba4-06d1-4d40-a16a-bfd50179d6ac"),
        part("nvme0n1p7", "ext4", size=5 * 1024 ** 3),
    ]},
]})


def test_lsblk_gives_the_devices_the_desktop_shows():
    found = devices.parse_lsblk(LSBLK)
    assert [device.name for device in found] == ["Windows 11", "Linux filesystem data", "Volumen de 5 GiB", "WD_SSD", "USB"]
    by_name = {device.name: device for device in found}
    assert by_name["Linux filesystem data"].is_root and by_name["Linux filesystem data"].icon_name == "drive-harddisk-root"
    assert by_name["Linux filesystem data"].used_fraction is not None  # la raíz siempre se puede medir
    assert not by_name["WD_SSD"].mounted and by_name["WD_SSD"].drive == "/dev/sda" and not by_name["WD_SSD"].removable
    usb = by_name["USB"]
    assert usb.removable and usb.drive == "/dev/sdb" and usb.mount_point == "/media/ana/USB" and usb.icon_name == "drive-removable-media-usb"


def test_an_older_lsblk_with_one_mount_point_also_works():
    old = json.dumps({"blockdevices": [{"path": "/dev/sdc1", "name": "sdc1", "type": "part", "fstype": "ext4", "label": "Datos",
                                        "mountpoint": "/media/datos", "size": 10, "parttype": None}]})
    assert [(device.name, device.mount_point) for device in devices.parse_lsblk(old)] == [("Datos", "/media/datos")]


def test_mounting_unmounting_and_ejecting_go_through_udisks(monkeypatch):
    calls = []
    state = {"mounted": False}

    def run(argv, _timeout):
        calls.append(argv)
        if argv[0] == "lsblk":
            mounts = ["/media/ana/USB"] if state["mounted"] else [None]
            return json.dumps({"blockdevices": [{"path": "/dev/sdb", "name": "sdb", "type": "disk", "rm": True, "size": 1,
                                                 "children": [part("sdb1", "vfat", "USB", mounts=mounts, parttype="0xc")]}]})
        if argv[1] == "mount":
            state["mounted"] = True
            return "Mounted /dev/sdb1 at /media/ana/USB\n"
        if argv[1] == "unmount":
            state["mounted"] = False
        return ""

    monkeypatch.setattr(devices, "_run", run)
    monkeypatch.setattr(devices, "list_devices", lambda: devices.parse_lsblk(run(["lsblk"], 1)))
    assert devices.mount("/dev/sdb1") == "/media/ana/USB"
    assert ["udisksctl", "mount", "-b", "/dev/sdb1"] in calls
    assert devices.mount("/dev/sdb1") == "/media/ana/USB" and sum(argv[:2] == ["udisksctl", "mount"] for argv in calls) == 1  # ya lo estaba
    devices.eject(devices.find("/dev/sdb1"))
    assert calls[-2:] == [["udisksctl", "unmount", "-b", "/dev/sdb1"], ["udisksctl", "power-off", "-b", "/dev/sdb"]]


def test_a_failing_udisks_gives_its_message(monkeypatch):
    import subprocess

    monkeypatch.undo()  # el `_run` de verdad, con un udisksctl que falla
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "Error mounting: Not authorized\n"))
    with pytest.raises(OSError, match="Not authorized"):
        devices.unmount("/dev/sdb1")


@linux_only
def test_the_tree_lists_devices_with_their_usage_and_mounts_on_click(app):
    tree = FolderTree()
    tree.resize(300, 600)
    tree.show()
    assert tree.devices_item.isHidden()  # hasta saber si hay alguno
    tree.set_devices(devices.parse_lsblk(LSBLK))
    section = tree.devices_item
    assert not section.isHidden() and section.isExpanded() and section.text(0) == "Dispositivos"
    items = {section.child(i).text(0): section.child(i) for i in range(section.childCount())}
    assert list(items) == ["Windows 11", "Linux filesystem data", "Volumen de 5 GiB", "WD_SSD", "USB"]
    assert items["USB"].data(0, PATH_ROLE) == "/media/ana/USB" and items["WD_SSD"].data(0, PATH_ROLE) == ""
    assert items["Linux filesystem data"].data(0, USAGE_ROLE) is not None and items["WD_SSD"].data(0, USAGE_ROLE) is None
    assert tree.visualItemRect(items["Linux filesystem data"]).height() > tree.visualItemRect(items["WD_SSD"]).height()  # lleva su barra
    asked = []
    tree.deviceActivated.connect(asked.append)
    opened = []
    tree.pathActivated.connect(opened.append)
    for name in ("WD_SSD", "USB"):
        rect = tree.visualItemRect(items[name])
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(rect.left() + 30, rect.center().y()))
    assert asked == ["/dev/sda1"] and opened == ["/media/ana/USB"]
    tree.set_devices([device for device in devices.parse_lsblk(LSBLK) if device.name != "USB"])  # se ha quitado
    assert section.childCount() == 4 and items["WD_SSD"] is section.child(3)  # los que siguen no se vuelven a crear
    tree.dispose()


@linux_only
def test_opening_an_unmounted_device_mounts_it_and_shows_it(window, monkeypatch, tmp_path):
    point = tmp_path / "montado"
    point.mkdir()
    found = devices.parse_lsblk(LSBLK)
    monkeypatch.setattr(devices, "list_devices", lambda: found)
    monkeypatch.setattr(devices, "mount", lambda path: str(point) if path == "/dev/sda1" else pytest.fail(path))
    window._refresh_devices()
    assert wait_until(lambda: window.tree.devices_item.childCount() == 5)
    view = window.source
    window.open_device("/dev/sda1", view)
    assert wait_until(lambda: view.path == str(point))
    menu = window._device_menu("/dev/sdb1", lambda: view)
    assert [action.text() for action in menu.actions()] == ["Abrir", "Desmontar", "Quitar con seguridad"]
    assert [action.text() for action in window._device_menu("/dev/nvme0n1p4", lambda: view).actions()] == ["Abrir"]  # la raíz no se desmonta


@linux_only
def test_unmounting_moves_away_the_panes_inside(window, monkeypatch, tmp_path):
    inside = tmp_path / "usb" / "fotos"
    inside.mkdir(parents=True)
    usb = devices.Device("/dev/sdb1", "USB", str(tmp_path / "usb"), 1, True, "/dev/sdb")
    monkeypatch.setattr(devices, "list_devices", lambda: [usb])
    unmounted = []
    monkeypatch.setattr(devices, "unmount", unmounted.append)
    window._refresh_devices()
    assert wait_until(lambda: window._devices == [usb])
    view = window.source
    view.navigate(str(inside))
    assert wait_until(lambda: view.path == str(inside))
    window.unmount_device("/dev/sdb1")
    assert wait_until(lambda: unmounted == ["/dev/sdb1"])
    assert view.path == os.path.expanduser("~")


# ------------------------------------------------------------------ un clic
def _point(view, name):
    index = view.model.index(view.model.row_of(os.path.join(view.path, name)), 0)
    return index, view.details.name_rect(index)


def test_single_click_opens_and_the_marker_selects(window):
    window.config["single_click"] = True
    window._preferences_changed()
    view = window.groups[0].current
    viewport = view.details.viewport()
    index, rect = _point(view, "a.txt")
    QTest.mouseMove(viewport, rect.center())
    assert view.details.hover_row == index.row() and viewport.cursor().shape() == Qt.CursorShape.PointingHandCursor
    icon = view.details._name_delegate.icon_rect(view.details, _option(view.details, index), index)
    marker = QPoint(icon.left() + 3, icon.top() + 3)
    assert view.details.marker_at(index, marker)
    opened = []
    view.openFile.connect(lambda _view, entry: opened.append(entry.name))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, marker)
    assert [entry.name for entry in view.selected_entries()] == ["a.txt"] and opened == []  # la marca selecciona sin abrir
    _index, other = _point(view, "b.txt")
    other_icon = view.details._name_delegate.icon_rect(view.details, _option(view.details, _index), _index)
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(other_icon.left() + 3, other_icon.top() + 3))
    assert sorted(entry.name for entry in view.selected_entries()) == ["a.txt", "b.txt"]  # se suma a la selección
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, marker)
    assert [entry.name for entry in view.selected_entries()] == ["b.txt"]  # y se quita
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, rect.center())
    assert opened == []  # con Ctrl, selecciona como siempre
    view._opened_by_click = 0
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert opened == ["a.txt"]
    _folder, folder_rect = _point(view, "subcarpeta")
    view._opened_by_click = 0
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, folder_rect.center())
    assert view.path.endswith("subcarpeta")


def test_double_click_stays_the_default(window):
    view = window.groups[0].current
    viewport = view.details.viewport()
    _index, rect = _point(view, "subcarpeta")
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert not view.path.endswith("subcarpeta") and not view.details.single_click
    QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert view.path.endswith("subcarpeta")


def _option(details, index):
    """Como la que recibe el delegado del nombre: desplazada por la sangría y la flecha."""
    option = QStyleOptionViewItem()
    details.initViewItemOption(option)
    option.rect = details.visualRect(index).adjusted(details._name_delegate._offset(index.data(EntryRole)), 0, 0, 0)
    return option


def test_the_marker_covers_small_icons_and_a_corner_of_thumbnails():
    from PySide6.QtCore import QRect
    from directory_commander_tux_tux.ui.file_view import marker_rect
    assert marker_rect(QRect(10, 10, 16, 16)) == QRect(10, 10, 16, 16)
    assert marker_rect(QRect(10, 10, 48, 48)) == QRect(10, 10, 48, 48)
    assert marker_rect(QRect(0, 0, 128, 128)) == QRect(0, 0, 32, 32)  # la miniatura se sigue viendo


def test_hovering_shows_the_file_in_the_viewer_except_media(window, tmp_path, monkeypatch):
    from directory_commander_tux_tux.core.listing import entry_from_path
    shown, described = [], []
    monkeypatch.setattr(window.preview, "show_entry", lambda entry: shown.append(entry and entry.name))
    monkeypatch.setattr(window.metadata, "show_entry", lambda entry: described.append(entry and entry.name))
    window.viewer_dock.show()
    window.meta_dock.show()
    view = window.source
    text = entry_from_path(os.path.join(view.path, "a.txt"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    window._hover_preview(view, text)
    window._show_hover_preview()
    assert shown == [] or shown[-1] != "a.txt"  # sin el modo de un clic, el visor sigue con la selección
    window.config["single_click"] = True
    window._hover_preview(view, text)
    assert wait_until(lambda: shown and shown[-1] == "a.txt" and described[-1] == "a.txt")
    shown.clear()
    window._hover_preview(view, entry_from_path(str(video)))
    window._show_hover_preview()
    assert "clip.mp4" not in shown  # un vídeo no: empezaría a reproducirse
    window._hover_preview(view, None)
    window._show_hover_preview()
    assert shown[-1] is None or shown[-1] != "a.txt"  # vuelve a lo seleccionado


def test_files_open_with_xdg_open_on_linux(monkeypatch):
    from PySide6.QtCore import QProcess
    from directory_commander_tux_tux.ui import open_external
    launched = []
    monkeypatch.setattr(open_external, "_use_xdg_open", lambda: True)
    monkeypatch.setattr(QProcess, "startDetached", lambda program, arguments: launched.append((program, arguments)) or (True, 1))
    assert open_external.open_externally("/tmp/informe.pdf") and open_external.open_externally("https://example.org")
    assert launched == [("xdg-open", ["/tmp/informe.pdf"]), ("xdg-open", ["https://example.org"])]


@linux_only
def test_mounting_goes_through_udisks_and_a_cancelled_password_is_not_an_error(window, monkeypatch, tmp_path):
    from directory_commander_tux_tux.ui import udisks
    point = tmp_path / "montado"
    point.mkdir()
    found = devices.parse_lsblk(LSBLK)
    monkeypatch.setattr(devices, "list_devices", lambda: found)
    window._refresh_devices()
    assert wait_until(lambda: window._devices == found)
    answers = {"/dev/sda1": str(point)}
    warnings = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda _parent, _title, text, *args: warnings.append(text))

    class FakeUDisks:
        def bus(self):
            return object()

        def mount(self, device, done, failed):
            if device in answers:
                done(answers[device])
            elif device == "/dev/nvme0n1p2":
                failed(udisks.Cancelled())
            else:
                failed(OSError("Error connecting to the udisks daemon: org.freedesktop.DBus.Error.LimitsExceeded"))

    monkeypatch.setattr(window, "_udisks", FakeUDisks())
    view = window.source
    window.open_device("/dev/sda1", view)
    assert view.path == str(point)
    window.open_device("/dev/nvme0n1p2", view)
    assert warnings == [] and "Cancelado" in window.statusBar().currentMessage()
    window.open_device("/dev/nvme0n1p7", view)
    assert len(warnings) == 1 and "Wine" in warnings[0]  # se explica por qué el sistema no deja conectar
