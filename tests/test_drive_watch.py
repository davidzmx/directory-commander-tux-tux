"""«Este equipo» se actualiza al conectar o quitar una unidad, como un USB, y muestra el espacio libre de cada una al día."""
import sys

import pytest

from directory_commander_tux_tux.core import drives
from directory_commander_tux_tux.core.listing import COMPUTER_PATH
from directory_commander_tux_tux.ui import drive_watch
from directory_commander_tux_tux.ui.file_view import FileView
from directory_commander_tux_tux.ui.folder_tree import PATH_ROLE, FolderTree
from tests.test_ui import app, wait_until  # noqa: F401 - se usan como fixtures

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="«Este equipo» es de Windows")


@pytest.fixture
def computer(app):
    view = FileView()
    view.show()
    view.navigate(COMPUTER_PATH)
    assert wait_until(lambda: view.model.rowCount() > 0 and not view.model.is_loading)
    yield view
    view.close()


def shown(view):
    return {entry.path: (entry.dir_size, entry.free_space) for entry in view.model.entries if entry.is_drive}


def test_free_space_is_updated_without_reading_the_drives_again(computer, monkeypatch):
    found = {path: (total, free + 4096) if free is not None else None for path, (total, free) in shown(computer).items()}
    reloads = []
    monkeypatch.setattr(computer, "refresh", lambda **kwargs: reloads.append(kwargs))
    drive_watch.watcher().changed.emit(found)
    assert reloads == []
    assert all(shown(computer)[path][1] == space[1] for path, space in found.items() if space is not None)


def test_a_new_drive_reads_this_computer_again(computer, monkeypatch):
    found = {path: (total, free) if free is not None else None for path, (total, free) in shown(computer).items()}
    found["Z:\\"] = (16 * 2**30, 8 * 2**30)
    reloads = []
    monkeypatch.setattr(computer, "refresh", lambda **kwargs: reloads.append(kwargs))
    drive_watch.watcher().changed.emit(found)
    assert reloads == [{"resort": True}]


def test_the_tree_reads_its_drives_again_when_one_appears(app, monkeypatch):
    tree = FolderTree()
    tree._load(tree.root_item)
    assert wait_until(lambda: tree.root_item.childCount() > 0)
    loads = []
    monkeypatch.setattr(tree, "_load", lambda item, then=None: loads.append(item))
    current = {tree.root_item.child(row).data(0, PATH_ROLE): None for row in range(tree.root_item.childCount())}
    drive_watch.watcher().changed.emit(current)
    assert loads == []
    drive_watch.watcher().changed.emit({**current, "Z:\\": (1, 1)})
    assert loads == [tree.root_item]
    tree.dispose()


def test_the_watcher_only_asks_while_needed_and_tells_only_changes(app, monkeypatch):
    answers = iter([{"C:\\": (100, 50)}, {"C:\\": (100, 50)}, {"C:\\": (100, 40)}])
    monkeypatch.setattr(drives, "drive_space", lambda: next(answers))
    watcher = drive_watch.DriveWatcher()
    told = []
    watcher.changed.connect(told.append)
    user = FileView()
    watcher.watch(user, lambda _found: None)
    assert watcher._timer.isActive()
    for _ in range(3):
        watcher._check()
        assert wait_until(lambda: not watcher._busy)
    assert told == [{"C:\\": (100, 40)}]  # la primera vez solo se apunta, y lo que no cambia no se cuenta
    watcher.unwatch(user)
    assert not watcher._timer.isActive()


def test_only_who_is_watching_now_is_told(app, monkeypatch):
    watcher = drive_watch.DriveWatcher()
    told = []
    looking, elsewhere = FileView(), FileView()
    watcher.watch(looking, lambda found: told.append(("mira", found)))
    watcher.watch(elsewhere, lambda found: told.append(("otra", found)))
    watcher.unwatch(elsewhere)  # se fue a otra carpeta
    watcher.changed.emit({"C:\\": (1, 1)})
    assert told == [("mira", {"C:\\": (1, 1)})]
    looking.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not watcher._users and not watcher._timer.isActive()
