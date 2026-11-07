"""Al aparecer o cambiar un archivo, la lista no se reordena hasta actualizarla, salvo que se pida en las preferencias."""
import os
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.ui.file_view import COLUMN_INDEX, FileView
from tests.test_ui import app, wait_until  # noqa: F401 - se usan como fixtures


@pytest.fixture
def dated(tmp_path):
    now = time.time()
    for number in range(5):
        path = tmp_path / f"foto_{number}.png"
        path.write_bytes(b"x")
        os.utime(path, (now - 1000 + number, now - 1000 + number))
    return tmp_path


def names(view):
    return [view.model.entry(row).name for row in range(view.model.rowCount())]


@pytest.fixture
def newest_first(app, dated):
    view = FileView()
    view.resize(800, 400)
    view.show()
    view.navigate(str(dated))
    assert wait_until(lambda: view.model.rowCount() == 5)
    view.details.sortByColumn(COLUMN_INDEX["modified"], Qt.SortOrder.DescendingOrder)
    assert names(view)[0] == "foto_4.png"
    yield view
    view.close()


def test_new_and_changed_files_do_not_move_the_others(newest_first, dated):
    view = newest_first
    (dated / "descarga.crdownload").write_bytes(b"x")
    os.utime(dated / "foto_0.png")  # como al guardarlo de nuevo: ahora es el más reciente
    view.refresh()
    assert wait_until(lambda: view.model.rowCount() == 6)
    assert names(view) == ["foto_4.png", "foto_3.png", "foto_2.png", "foto_1.png", "foto_0.png", "descarga.crdownload"]
    view.refresh(resort=True)  # F5
    assert wait_until(lambda: names(view)[:2] == ["descarga.crdownload", "foto_0.png"] or names(view)[:2] == ["foto_0.png", "descarga.crdownload"])


def test_the_preference_sorts_at_once(newest_first, dated):
    view = newest_first
    view.set_resort_on_change(True)
    (dated / "nuevo.png").write_bytes(b"x")
    view.refresh()
    assert wait_until(lambda: names(view)[:1] == ["nuevo.png"])


def test_showing_hidden_files_sorts_them_in_place(newest_first, dated):
    view = newest_first
    hidden = dated / ".oculta.png"
    hidden.write_bytes(b"x")
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 2)
    view.set_show_hidden(True)
    assert wait_until(lambda: names(view)[:1] == [".oculta.png"])


def test_a_renamed_file_keeps_its_place(newest_first, dated):
    view = newest_first
    os.replace(dated / "foto_2.png", dated / "zzz.png")
    view.refresh(renamed={str(dated / "foto_2.png"): str(dated / "zzz.png")})
    assert wait_until(lambda: "zzz.png" in names(view))
    assert names(view) == ["foto_4.png", "foto_3.png", "zzz.png", "foto_1.png", "foto_0.png"]


@pytest.fixture
def long_list(app, tmp_path):
    for number in range(300):
        (tmp_path / f"archivo_{number:04d}.txt").write_bytes(b"x")
    view = FileView()
    view.resize(800, 300)
    view.show()
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.model.rowCount() == 300)
    view.set_view_mode("list")
    bar = view.thumbs.horizontalScrollBar()
    assert wait_until(lambda: bar.maximum() > 2)
    yield view, tmp_path, bar
    view.close()


def test_at_the_end_of_the_list_it_stays_at_the_end_when_a_column_appears(long_list):
    view, folder, bar = long_list
    columns = bar.maximum()
    for number in range(80):  # hasta que haga falta otra columna
        bar.setValue(bar.maximum())
        (folder / f"nuevo_{number:03d}.txt").write_bytes(b"x")
        view.refresh()
        assert wait_until(lambda: view.model.rowCount() == 301 + number)
        assert bar.value() == bar.maximum(), number
        if bar.maximum() > columns:
            break
    assert bar.maximum() > columns


def test_relaying_out_the_list_keeps_its_column(long_list):
    view, _folder, bar = long_list
    bar.setValue(bar.maximum() // 2)
    middle = bar.value()
    view.thumbs.scheduleDelayedItemsLayout()
    for _ in range(20):
        QApplication.processEvents()
    view.thumbs.doItemsLayout()
    assert bar.value() == middle
