import json
import os
import sys
import threading
import time
import wave
import zipfile

import pytest
import shiboken6
from PySide6.QtCore import (QBuffer, QCoreApplication, QEvent, QEventLoop, QItemSelectionModel, QMimeData, QPoint,
                            QPointF, QRect, QSize, Qt, QUrl)
from PySide6.QtGui import (QAction, QCloseEvent, QColor, QContextMenuEvent, QDragEnterEvent, QDragMoveEvent, QDropEvent, QGuiApplication, QHelpEvent, QIcon, QImage,
                           QMouseEvent, QPalette, QWheelEvent)
from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoFrameFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFormLayout, QLineEdit, QMenu, QMessageBox, QStyle, QStyleOptionToolBar, QToolButton, QWidget,
                               QTreeWidgetItem)

from directory_commander_tux_tux.core import bar_buttons, context_menu, desktop_apps, fsops, media_info, platform_utils, settings_io, tab_settings, windows_shell
from directory_commander_tux_tux.core.windows_shell import ShellItem, clean_items
from directory_commander_tux_tux.core.config import CONFIG_VERSION, DEFAULT_TOOLBAR, Config
from directory_commander_tux_tux.core.fsops import Collision, OpResult
from directory_commander_tux_tux.ui.transfer_dialog import SpeedGraph, TransferDialog, format_clock
from directory_commander_tux_tux.ui import clipboard, hover_card as hover_card_module, image_ops, theme
from directory_commander_tux_tux.core.listing import COMPUTER_PATH, Entry, entry_from_path, format_size, has_computer_view
from directory_commander_tux_tux.ui.favorites_pages import AddFavoriteDialog
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog, PropertiesDialog, RenameDialog
from directory_commander_tux_tux.ui.icons import has_icon, themed
from directory_commander_tux_tux.ui.image_viewer import ImageViewer, color_depth, format_aspect, slider_to_zoom, zoom_to_slider
from directory_commander_tux_tux.ui.file_view import COLUMN_INDEX, Breadcrumb, FileView, path_crumbs
from directory_commander_tux_tux.ui.main_window import MAX_TAB_NAME, MainWindow, _tab_title, move_to_trash
from directory_commander_tux_tux.ui.media_player import SEEK_MIN_WIDTH, MediaPlayer, SeekSlider, frame_index_at, frame_middle_ms
from directory_commander_tux_tux.ui.video_viewer import format_exact_position
from directory_commander_tux_tux.ui.model import COLUMNS, EntryRole
from directory_commander_tux_tux.ui.shell_menu import add_shell_items, tidy_separators
from directory_commander_tux_tux.ui.preview import PreviewPane


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def tree(tmp_path):
    left = tmp_path / "izq"
    right = tmp_path / "der"
    (left / "subcarpeta").mkdir(parents=True)
    right.mkdir()
    (left / "a.txt").write_text("A", encoding="utf-8")
    (left / "b.txt").write_text("B", encoding="utf-8")
    (left / "subcarpeta" / "c.txt").write_text("C", encoding="utf-8")
    return left, right


@pytest.fixture
def window(app, tmp_path, tree, monkeypatch):
    left, right = tree
    cfg = Config(str(tmp_path / "cfg"))
    for style in cfg["styles"]:  # la mayoría de las pruebas usan la tabla de detalles; las de lista la piden
        if style["view"] == "list":
            style["view"] = "details"
    win = MainWindow(cfg, [str(left), str(right)])
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)
    monkeypatch.setattr(win, "_job_error_dialog", lambda *a, **k: None)
    win.show()
    assert wait_until(lambda: win.groups[0].current.model.rowCount() == 3)
    assert wait_until(lambda: win.groups[1].current.path == os.path.normpath(str(right)))
    yield win
    win.jobs.cancel_all()
    win.jobs.pool.waitForDone(3000)
    win.close()
    win.deleteLater()
    QCoreApplication.processEvents()


def names(view):
    return [e.name for e in view.model.entries]


def select(view, *wanted):
    view.select_paths([os.path.join(view.path, n) for n in wanted])


def test_loads_dual_panes_with_sorted_entries(window):
    assert names(window.source) == ["subcarpeta", "a.txt", "b.txt"]
    assert window.dest is not None
    assert window.source.property("role") == "source"
    assert window.dest.property("role") == "dest"


def test_navigation_and_history(window, tree):
    left, _ = tree
    view = window.source
    view.navigate(str(left / "subcarpeta"))
    assert wait_until(lambda: names(view) == ["c.txt"])
    window.run_command("Go BACK")
    assert wait_until(lambda: names(view)[:1] == ["subcarpeta"])
    window.run_command("Go FORWARD")
    assert wait_until(lambda: names(view) == ["c.txt"])
    window.run_command("Go UP")
    assert wait_until(lambda: view.path == os.path.normpath(str(left)))


def test_go_missing_path_reports_without_changing(window, tree, monkeypatch):
    errors = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: errors.append(a[2]))
    before = window.source.path
    window.run_command('Go "/no/existe/seguro"')
    assert errors and window.source.path == before


def test_copy_to_dest_job(window, tree):
    _, right = tree
    select(window.source, "a.txt", "subcarpeta")
    window.run_command("Copy")
    assert wait_until(lambda: sorted(names(window.dest)) == ["a.txt", "subcarpeta"])
    assert (right / "subcarpeta" / "c.txt").read_text(encoding="utf-8") == "C"
    assert (tree[0] / "a.txt").exists()


def test_move_to_dest_job(window, tree):
    left, right = tree
    select(window.source, "a.txt")
    window.run_command("Copy MOVE")
    assert wait_until(lambda: names(window.dest) == ["a.txt"])
    assert not (left / "a.txt").exists()
    assert wait_until(lambda: "a.txt" not in names(window.source))


def test_collision_prompt_respected(window, tree, monkeypatch):
    left, right = tree
    (right / "a.txt").write_text("viejo", encoding="utf-8")
    monkeypatch.setattr(window, "_ask_collision", lambda *_: fsops.Answer(fsops.Action.SKIP))
    select(window.source, "a.txt")
    window.run_command("Copy")
    assert wait_until(lambda: window.jobs.running == 0)
    assert (right / "a.txt").read_text(encoding="utf-8") == "viejo"


def test_collision_cancel_stops_the_operation(window, tree, monkeypatch):
    left, right = tree
    (right / "a.txt").write_text("viejo", encoding="utf-8")
    monkeypatch.setattr(window, "_ask_collision", lambda *_: None)
    select(window.source, "a.txt")
    window.run_command("Copy MOVE")
    assert wait_until(lambda: window.jobs.running == 0)
    assert (left / "a.txt").exists() and (right / "a.txt").read_text(encoding="utf-8") == "viejo"


def test_copy_into_archive_rejected(window, tree, monkeypatch):
    left, right = tree
    with zipfile.ZipFile(right / "z.zip", "w") as zf:
        zf.writestr("x.txt", "x")
    errors = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: errors.append(a[2]))
    window.dest.navigate(str(right / "z.zip"))
    assert wait_until(lambda: names(window.dest) == ["x.txt"])
    select(window.source, "a.txt")
    window.run_command("Copy")
    assert errors and window.jobs.running == 0


def test_permanent_delete_needs_confirmation(window, tree, monkeypatch):
    left, _ = tree
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    select(window.source, "a.txt")
    window.run_command("Delete NORECYCLE")
    assert window.jobs.running == 0 and (left / "a.txt").exists()
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    window.run_command("Delete NORECYCLE")
    assert wait_until(lambda: not (left / "a.txt").exists())


def test_create_folder_command(window, tree):
    left, _ = tree
    window.run_command('CreateFolder NAME="carpeta nueva"')
    assert (left / "carpeta nueva").is_dir()
    assert wait_until(lambda: "carpeta nueva" in names(window.source))


def test_selection_commands(window):
    view = window.source
    window.run_command("Select ALL")
    assert len(view.selected_paths()) == 3
    window.run_command("Select NONE")
    assert view.selected_paths() == []
    window.run_command("Select PATTERN=*.txt")
    assert len(view.selected_paths()) == 2
    window.run_command("Select INVERT")
    assert [os.path.basename(p) for p in view.selected_paths()] == ["subcarpeta"]


def test_select_compare(window, tree):
    _, right = tree
    (right / "a.txt").write_text("x", encoding="utf-8")
    assert wait_until(lambda: names(window.dest) == ["a.txt"])
    window.run_command("Select SOURCETODEST=in")
    assert [os.path.basename(p) for p in window.source.selected_paths()] == ["a.txt"]
    window.run_command("Select SOURCETODEST=notin")
    assert len(window.source.selected_paths()) == 2


def test_selection_survives_refresh(window):
    view = window.source
    select(view, "b.txt")
    view.refresh()
    time.sleep(0.05)
    assert wait_until(lambda: [os.path.basename(p) for p in view.selected_paths()] == ["b.txt"])


def test_filter_and_status(window):
    view = window.source
    view.filter_edit.setText("*.txt")
    assert names(view) == ["a.txt", "b.txt"]
    assert "filtrado" in view.status.text()
    view.filter_edit.setText("")
    assert len(names(view)) == 3


def test_flat_view(window):
    window.run_command("Set FLATVIEW=Toggle")
    assert wait_until(lambda: sorted(names(window.source)) == ["a.txt", "b.txt", "c.txt"])
    window.run_command("Set FLATVIEW=Toggle")
    assert wait_until(lambda: "subcarpeta" in names(window.source))


def test_dual_modes(window):
    window.run_command("Set DUAL=Off")
    assert window.dest is None and not window.groups[1].isVisible()
    window.run_command("Set DUAL=Horizontal")
    assert window.dest is not None and window.splitter.orientation() == Qt.Orientation.Vertical
    window.run_command("Set DUAL=Vertical")
    assert window.splitter.orientation() == Qt.Orientation.Horizontal


def test_tabs(window):
    group = window.groups[0]
    window.run_command("Go TABNEW")
    assert group.count() == 2
    window.run_command("Go TABCLOSE")
    assert group.count() == 1
    window.run_command("Go TABCLOSE")
    assert group.count() == 1  # nunca se queda sin pestañas


def test_swap(window, tree):
    left, right = tree
    window.run_command("Go SWAP")
    assert window.source.path == os.path.normpath(str(right))
    assert window.dest.path == os.path.normpath(str(left))


def test_unknown_command_does_not_crash(window):
    window.run_command("NoExiste FOO")
    assert "desconocido" in window.statusBar().currentMessage()


def test_archive_browse_and_extract_to_dest(window, tree):
    left, right = tree
    with zipfile.ZipFile(left / "datos.zip", "w") as zf:
        zf.writestr("dentro/x.txt", "X")
    assert wait_until(lambda: "datos.zip" in names(window.source))
    select(window.source, "datos.zip")
    window.run_command("Extract")
    assert wait_until(lambda: (right / "datos" / "dentro" / "x.txt").exists())
    window.source.navigate(str(left / "datos.zip"))
    assert wait_until(lambda: names(window.source) == ["dentro"])
    select(window.source, "dentro")
    window.run_command("Copy")
    assert wait_until(lambda: (right / "dentro" / "x.txt").exists())


def test_create_zip_job(window, tree, monkeypatch):
    left, right = tree
    monkeypatch.setattr(window, "_ask_text", lambda *a: "paquete")
    select(window.source, "a.txt")
    window.run_command("Archive")
    assert wait_until(lambda: (right / "paquete.zip").exists())
    assert zipfile.ZipFile(right / "paquete.zip").namelist() == ["a.txt"]


def test_get_sizes(window):
    view = window.source
    window.run_command("GetSizes")
    assert wait_until(lambda: next(e for e in view.model.entries if e.name == "subcarpeta").dir_size == 1)


def test_hash_goes_to_log(window):
    select(window.source, "a.txt")
    window.run_command("GetSizes HASH=sha256")
    assert wait_until(lambda: "559aead0" in window.log.toPlainText())


def test_clipboard_copy_paste(window, tree):
    _, right = tree
    select(window.source, "a.txt")
    window.run_command("Clipboard COPY")
    window.dest.activated.emit(window.dest)
    window.run_command("Clipboard PASTE")
    assert wait_until(lambda: (right / "a.txt").exists())
    assert (tree[0] / "a.txt").exists()


@pytest.mark.parametrize("cut", [True, False])
def test_clipboard_formats_interoperate(app, cut, tmp_path):
    path = str(tmp_path / "a b.txt")
    mime = clipboard.build_mime([path], cut)
    assert bytes(mime.data("application/x-kde-cutselection")) == (b"1" if cut else b"0")
    first_line = bytes(mime.data("x-special/gnome-copied-files")).split(b"\n")[0]
    assert first_line == (b"cut" if cut else b"copy")
    assert clipboard.read_files(mime) == ([path], cut)
    # Datos como los que escribe otro gestor, con un único formato cada vez.
    for fmt, value in (("x-special/gnome-copied-files", b"cut\n" if cut else b"copy\n"),
                       ("application/x-kde-cutselection", b"1" if cut else b"0"),
                       ("Preferred DropEffect", (2 if cut else 5).to_bytes(4, "little"))):
        other = QMimeData()
        other.setUrls([QUrl.fromLocalFile(path)])
        other.setData(fmt, value)
        assert clipboard.read_files(other)[1] is cut


def test_drop_handler_copies_and_ignores_same_folder(window, tree):
    left, right = tree
    window._files_dropped(window.dest, [str(left / "a.txt")], None, False)
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    window._files_dropped(window.source, [str(left / "a.txt")], None, False)
    assert window.jobs.running == 0
    assert not (left / "a (2).txt").exists()


def test_rename_dialog_preview_blocks_duplicates(app, tree):
    left, _ = tree
    entries = [Entry("a.txt", str(left / "a.txt"), False), Entry("b.txt", str(left / "b.txt"), False)]
    dialog = RenameDialog(str(left), entries, {"subcarpeta"}, {})
    dialog.mode.setCurrentIndex(dialog.mode.findData("regex"))
    dialog.pattern.setText("(.*)")
    dialog.replacement.setText("igual")
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert not ok.isEnabled()
    dialog.replacement.setText("foto_[#]")
    dialog.seq_digits.setValue(3)
    assert ok.isEnabled()
    assert dialog.new_names == ["foto_001.txt", "foto_002.txt"]
    dialog.pattern.setText("(")
    assert not ok.isEnabled()


def test_find_panel(window, tree):
    panel = window.find_panel
    panel.pattern.setText("c.*")
    panel.start()
    assert wait_until(lambda: not panel.is_running and names(window.source) == ["c.txt"])
    assert window.source.model.entries[0].path.endswith("c.txt")


def test_sync_panel_copies_but_never_deletes(window, tree):
    left, right = tree
    (right / "solo_der.txt").write_text("D", encoding="utf-8")
    assert wait_until(lambda: names(window.dest) == ["solo_der.txt"])
    panel = window.sync_panel
    panel.start()
    assert wait_until(lambda: panel.results.rowCount() == 4)
    assert panel.to_dest.isEnabled() and panel.to_source.isEnabled()
    panel.to_dest.click()
    assert wait_until(lambda: (right / "a.txt").exists() and (right / "subcarpeta" / "c.txt").exists())
    assert (right / "solo_der.txt").exists()


def test_dupe_panel_refuses_removing_every_copy(window, tree, monkeypatch):
    left, _ = tree
    (left / "copia.txt").write_text("A", encoding="utf-8")
    warnings = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.utility_panels.QMessageBox.warning", lambda *a: warnings.append(a[2]))
    panel = window.dupe_panel
    panel.start()
    assert wait_until(lambda: panel.results.rowCount() == 2)
    panel.results.selectAll()
    panel._remove()
    assert warnings and (left / "a.txt").exists() and (left / "copia.txt").exists()


def test_preview_shows_text(window, tree):
    window.run_command("Set VIEWPANE=On")
    select(window.source, "a.txt")
    assert wait_until(lambda: window.preview.stack.currentWidget() is window.preview.text)
    assert window.preview.text.toPlainText() == "A"


def test_thumbnail_mode_shares_selection(window):
    window.run_command("Set VIEW=Thumbnails")
    select(window.source, "a.txt")
    assert window.source.stack.currentWidget() is window.source.thumbs
    assert window.source.thumbs.selectionModel().selectedRows()
    window.run_command("Set VIEW=Details")


def test_session_restored(app, tmp_path, tree):
    left, right = tree
    cfg_dir = str(tmp_path / "cfg2")
    first = MainWindow(Config(cfg_dir), [str(left), str(right)])
    first.groups[0].add_tab(str(left / "subcarpeta"))
    first.set_dual("Horizontal")
    first.close()
    second = MainWindow(Config(cfg_dir))
    try:
        assert second.dual == "Horizontal"
        assert [v.path for v in second.groups[0].views()] == [os.path.normpath(str(left)),
                                                              os.path.normpath(str(left / "subcarpeta"))]
        assert second.groups[1].current.path == os.path.normpath(str(right))
    finally:
        second.close()


def test_session_with_deleted_folder_falls_back(app, tmp_path, tree):
    cfg = Config(str(tmp_path / "cfg3"))
    cfg["session"] = {"tabs": [[str(tmp_path / "ya-no-existe")], "basura"]}
    win = MainWindow(cfg)
    try:
        assert win.groups[0].current.path == os.path.normpath(os.path.expanduser("~"))
        assert win.groups[1].count() == 1
    finally:
        win.close()


def test_bad_toolbar_config_is_ignored(app, tmp_path):
    cfg = Config(str(tmp_path / "cfg4"))
    cfg["toolbar"] = ["no soy un botón", {"sin": "comando"}, {"label": "Ok", "command": "Go UP"}]
    win = MainWindow(cfg)
    try:
        assert [a.text() for a in win.toolbar.actions()] == ["Ok"]
    finally:
        win.close()


def test_shortcut_scope_does_not_hit_files_from_other_panels(window, tree, monkeypatch):
    left, _ = tree
    called = []
    monkeypatch.setattr(window, "_cmd_delete", lambda c: called.append(c))
    window.activateWindow()
    select(window.source, "a.txt")
    window.run_command("Set UTILITY=Find")
    window.find_panel.pattern.setFocus()
    if QApplication.focusWidget() is not window.find_panel.pattern:
        pytest.skip("la plataforma offscreen no entregó el foco")
    QTest.keyClick(window.find_panel.pattern, Qt.Key.Key_Delete)
    assert called == []


def test_narrow_pane_gives_room_to_name(app):
    view = FileView()
    view.resize(340, 300)
    view.show()
    assert view.width() == 340
    assert wait_until(lambda: view.details.isColumnHidden(2) and view.details.isColumnHidden(4))
    view.resize(900, 300)
    assert wait_until(lambda: not view.details.isColumnHidden(2) and not view.details.isColumnHidden(4))
    view.close()


def test_preview_image_fits_and_pane_can_shrink(app, tmp_path):
    image = QImage(3000, 2000, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    path = str(tmp_path / "grande.png")
    image.save(path)
    preview = PreviewPane()
    preview.resize(500, 400)
    preview.show()
    preview.show_entry(Entry("grande.png", path, False, size=os.path.getsize(path)))
    assert wait_until(lambda: preview.stack.currentWidget() is preview.image and not preview.image.pixmap().isNull())
    shown = preview.image.pixmap()
    assert 0 < shown.width() <= preview.image.width() and shown.height() <= preview.image.height()
    preview.resize(200, 150)
    assert wait_until(lambda: preview.image.width() <= 200)
    assert preview.image.pixmap().width() <= preview.image.width()
    preview.close()


def test_long_path_does_not_force_window_width(app, tmp_path):
    deep = tmp_path
    for index in range(12):
        deep = deep / f"una-carpeta-con-nombre-largo-{index}"
    deep.mkdir(parents=True)
    win = MainWindow(Config(str(tmp_path / "cfg-largo")), [str(deep), str(deep)])
    win.resize(1000, 700)
    win.show()
    assert wait_until(lambda: win.source.model.path == os.path.normpath(str(deep)))
    QCoreApplication.processEvents()
    assert win.width() == 1000
    win.run_command("Set UTILITY=Sync")
    wait_until(lambda: False, 0.1)
    assert win.width() == 1000
    buttons = win.source.breadcrumb._buttons
    assert buttons[-1].isVisibleTo(win.source.breadcrumb) and not buttons[0].isVisibleTo(win.source.breadcrumb)
    win.close()


def test_docks_can_be_closed_from_title_bar(window):
    window.run_command("Set VIEWPANE=On")
    assert not window.viewer_dock.isHidden()
    buttons = window.viewer_dock.titleBarWidget().findChildren(QToolButton)
    buttons[-1].click()
    assert window.viewer_dock.isHidden()


def test_closing_tab_while_loading_is_safe(window, tree):
    left, _ = tree
    group = window.groups[0]
    view = group.add_tab(str(left))
    group.close_tab(group.indexOf(view))
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    wait_until(lambda: False, 0.2)  # la carga pendiente termina sin tocar el objeto destruido
    assert group.count() == 1 and window.source.path == os.path.normpath(str(left))


# ---- barras movibles ----

def test_bars_are_movable_and_can_be_locked(window):
    assert all(bar.isMovable() for bar in window._all_bars())
    window.run_command("Set LOCKBARS=Toggle")
    assert not any(bar.isMovable() for bar in window._all_bars())
    window.run_command("Set LOCKBARS=Off")
    assert all(bar.isMovable() for bar in window._all_bars())


def test_menu_lives_in_its_own_bar_and_cannot_be_hidden(window):
    assert window.menuBar() is window.main_menu
    assert window.menu_tools.widgetForAction(window.menu_tools.actions()[0]) is window.main_menu
    assert not window.menu_tools.toggleViewAction().isEnabled()


def test_bar_positions_survive_restart(app, tmp_path, tree):
    left, right = tree
    cfg_dir = str(tmp_path / "barras")
    first = MainWindow(Config(cfg_dir), [str(left), str(right)])
    first.show()
    first.addToolBar(Qt.ToolBarArea.BottomToolBarArea, first.toolbar)
    first.removeToolBarBreak(first.location_bar)
    first.addToolBar(Qt.ToolBarArea.LeftToolBarArea, first.favorites_bar)
    first.run_command('Toolbar NAME="Favorites Bar" TOGGLE')
    wait_until(lambda: False, 0.05)
    first.close()
    second = MainWindow(Config(cfg_dir), [str(left), str(right)])
    second.show()
    try:
        assert second.toolBarArea(second.toolbar) == Qt.ToolBarArea.BottomToolBarArea
        assert second.toolBarArea(second.favorites_bar) == Qt.ToolBarArea.LeftToolBarArea
        assert second.toolBarArea(second.menu_tools) == Qt.ToolBarArea.TopToolBarArea
    finally:
        second.close()


def test_state_from_older_version_is_ignored(app, tmp_path, tree):
    left, right = tree
    cfg = Config(str(tmp_path / "antiguo"))
    old = MainWindow(cfg, [str(left), str(right)])
    old.addToolBar(Qt.ToolBarArea.BottomToolBarArea, old.toolbar)
    state = bytes(old.saveState(0).toHex()).decode()
    old.close()
    cfg["session"] = {"tabs": [[str(left)], [str(right)]], "state": state}
    new = MainWindow(cfg)
    try:
        assert new.toolBarArea(new.toolbar) == Qt.ToolBarArea.TopToolBarArea
    finally:
        new.close()


def drag_bar(window, bar, window_target):
    """Arrastra el asa con eventos de ratón. Las coordenadas globales son fijas porque, al
    desprenderse, la barra deja de estar donde estaba."""
    option = QStyleOptionToolBar()
    bar.initStyleOption(option)
    handle = bar.style().subElementRect(QStyle.SubElement.SE_ToolBarHandle, option, bar).center()
    begin = bar.mapToGlobal(handle)
    finish = window.mapToGlobal(window_target)

    def send(kind, point, buttons):
        event = QMouseEvent(kind, QPointF(bar.mapFromGlobal(point)), QPointF(point), Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(bar, event)

    send(QEvent.Type.MouseButtonPress, begin, Qt.MouseButton.LeftButton)
    for step in range(1, 9):
        send(QEvent.Type.MouseMove, begin + (finish - begin) * step / 8, Qt.MouseButton.LeftButton)
        wait_until(lambda: False, 0.02)
    wait_until(lambda: False, 0.3)
    send(QEvent.Type.MouseButtonRelease, finish, Qt.MouseButton.NoButton)
    wait_until(lambda: False, 0.3)


def test_dragging_handle_puts_bar_in_the_menu_row(window):
    window.resize(1200, 800)
    wait_until(lambda: False, 0.1)
    drag_bar(window, window.style_bar, QPoint(900, 12))
    assert window.style_bar.geometry().y() == window.menu_tools.geometry().y()
    assert window.toolBarArea(window.style_bar) == Qt.ToolBarArea.TopToolBarArea
    drag_bar(window, window.toolbar, QPoint(4, 400))
    assert window.toolBarArea(window.toolbar) == Qt.ToolBarArea.LeftToolBarArea
    assert window.toolbar.orientation() == Qt.Orientation.Vertical


def test_locked_bar_ignores_dragging(window):
    window.resize(1200, 800)
    window.run_command("Set LOCKBARS=On")
    wait_until(lambda: False, 0.1)
    before = window.style_bar.geometry()
    drag_bar(window, window.style_bar, QPoint(900, 12))
    assert window.style_bar.geometry() == before


def test_location_bar_cannot_go_vertical(window):
    assert not window.location_bar.isAreaAllowed(Qt.ToolBarArea.LeftToolBarArea)
    assert window.location_bar.isAreaAllowed(Qt.ToolBarArea.BottomToolBarArea)


# ---- doble clic en la barra de pestañas ----

def test_double_click_on_tab_bar_opens_tab(window, tree):
    left, _ = tree
    group = window.groups[0]
    bar = group.tabBar()
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, bar.rect_of(0).center())
    assert group.count() == 2
    assert group.current.path == os.path.normpath(str(left))


def test_double_click_on_empty_strip_opens_tab(window):
    group = window.groups[0]
    group.resize(900, 500)
    x = group.width() - 40
    target = group.childAt(x, 6)
    assert target is None or target is group or target is group.tabBar()
    QTest.mouseDClick(target or group, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      (target or group).mapFrom(group, QPoint(x, 6)))
    assert group.count() == 2


def test_double_click_on_file_list_does_not_open_tab(window):
    group = window.groups[0]
    viewport = group.current.details.viewport()
    QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      QPoint(viewport.width() // 2, viewport.height() - 5))
    assert group.count() == 1


# ---- tarjeta al pasar el ratón ----

def name_point(view, row_name):
    """Un punto sobre el nombre de ese archivo, en la vista activa."""
    index = view.model.index(view.model.row_of(os.path.join(view.path, row_name)), 0)
    if view.current_view is view.details:
        return view.details.name_rect(index).center()
    return view.thumbs.visualRect(index).center()


def hover(view, row_name):
    point = name_point(view, row_name)
    viewport = view.current_view.viewport()
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.Type.ToolTip, point, viewport.mapToGlobal(point)))
    return point


def form_rows(card):
    rows = {}
    for row in range(card.form.rowCount()):
        label = card.form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget().text().rstrip(":")
        rows[label] = card.form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget().text()
    return rows


def test_hover_image_shows_card_like_explorer(window, tree):
    left, _ = tree
    image = QImage(988, 1304, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    image.save(str(left / "superG.png"))
    assert wait_until(lambda: "superG.png" in names(window.source))
    view = window.source
    card = view.hover_card
    hover(view, "superG.png")
    assert wait_until(card.isVisible)
    assert card.title.text() == "superG.png"
    assert "988 x 1304" in card.subtitle.text() and "Imagen PNG" in card.subtitle.text()
    rows = form_rows(card)
    assert set(rows) == {"Tamaño", "Dimensiones", "Modificado"}
    assert rows["Dimensiones"].startswith("988 x 1304")
    assert wait_until(lambda: not card.thumb.pixmap().isNull() and card.thumb.pixmap().height() == 148 or
                      card.thumb.pixmap().height() > 100)


def test_hover_video_without_ffmpeg_still_shows_data(window, tree, monkeypatch):
    left, _ = tree
    monkeypatch.setattr(media_info, "find_tool", lambda *a: None)
    from tests.test_media import mp4_bytes
    (left / "peli.mp4").write_bytes(mp4_bytes())
    assert wait_until(lambda: "peli.mp4" in names(window.source))
    view = window.source
    hover(view, "peli.mp4")
    assert wait_until(view.hover_card.isVisible)
    rows = form_rows(view.hover_card)
    assert rows["Dimensiones"] == "1920 x 1080" and rows["Duración"] == "1:30"
    assert "Vídeo MP4" in view.hover_card.subtitle.text()


def test_hover_audio_cover_becomes_thumbnail(window, tree):
    left, _ = tree
    from tests.test_media import flac_bytes
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(0x993366)
    image.save(buffer, "PNG")
    (left / "tema.flac").write_bytes(flac_bytes(["TITLE=Mi tema", "ARTIST=Banda"], bytes(buffer.data())))
    assert wait_until(lambda: "tema.flac" in names(window.source))
    view = window.source
    hover(view, "tema.flac")
    assert wait_until(view.hover_card.isVisible)
    rows = form_rows(view.hover_card)
    assert rows["Título"] == "Mi tema" and rows["Artista"] == "Banda" and rows["Duración"] == "0:05"
    card = view.hover_card
    assert wait_until(lambda: card.thumb.pixmap().toImage().pixelColor(card.thumb.pixmap().width() // 2,
                                                                       card.thumb.pixmap().height() // 2).red() > 120)


def test_hover_text_is_not_interpreted_as_html(window, tree):
    left, _ = tree
    from tests.test_media import flac_bytes
    (left / "t.flac").write_bytes(flac_bytes(["TITLE=<b>negrita</b>"]))
    assert wait_until(lambda: "t.flac" in names(window.source))
    view = window.source
    hover(view, "t.flac")
    assert wait_until(view.hover_card.isVisible)
    label = view.hover_card.form.itemAt(view.hover_card.form.rowCount() - 2, QFormLayout.ItemRole.FieldRole).widget()
    assert form_rows(view.hover_card)["Título"] == "<b>negrita</b>"
    assert label.textFormat() == Qt.TextFormat.PlainText


def test_hover_folder_and_plain_file_show_basic_card(window):
    view = window.source
    hover(view, "a.txt")
    assert wait_until(view.hover_card.isVisible)
    assert set(form_rows(view.hover_card)) == {"Tamaño", "Modificado"}
    hover(view, "subcarpeta")
    assert wait_until(lambda: view.hover_card.title.text() == "subcarpeta")
    assert "Dimensiones" not in form_rows(view.hover_card)


def test_hover_dismissed_when_moving_or_pressing(window):
    view = window.source
    card = view.hover_card
    hover(view, "a.txt")
    assert wait_until(card.isVisible)
    viewport = view.details.viewport()
    QTest.mouseMove(viewport, name_point(view, "b.txt"))
    assert wait_until(lambda: not card.isVisible(), 1)
    hover(view, "a.txt")
    assert wait_until(card.isVisible)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, viewport.height() - 2))
    assert not card.isVisible()
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, viewport.height() - 2))


def test_stale_hover_result_is_discarded(window):
    view = window.source
    card = view.hover_card
    hover(view, "a.txt")
    card.dismiss()  # el ratón se fue antes de que terminara la lectura
    wait_until(lambda: False, 0.2)
    assert not card.isVisible()


def test_hover_can_be_turned_off(window):
    view = window.source
    window.run_command("Set HOVERINFO=Toggle")
    hover(view, "a.txt")
    wait_until(lambda: False, 0.15)
    assert not view.hover_card.isVisible()
    window.run_command("Set HOVERINFO=On")
    hover(view, "a.txt")
    assert wait_until(view.hover_card.isVisible)


def test_hover_works_in_thumbnails_and_inside_archives(window, tree):
    left, _ = tree
    with zipfile.ZipFile(left / "d.zip", "w") as zf:
        zf.writestr("foto.png", b"no se abre desde el zip")
    window.run_command("Set VIEW=Thumbnails")
    assert wait_until(lambda: "d.zip" in names(window.source))
    view = window.source
    hover(view, "a.txt")
    assert wait_until(view.hover_card.isVisible)
    view.hover_card.dismiss()
    view.navigate(str(left / "d.zip"))
    assert wait_until(lambda: names(view) == ["foto.png"])
    hover(view, "foto.png")
    assert wait_until(view.hover_card.isVisible)
    assert "Dimensiones" not in form_rows(view.hover_card)


def test_hover_outside_the_name_shows_no_card(window):
    view = window.source
    viewport = view.details.viewport()
    y = name_point(view, "a.txt").y()
    outside = QPoint(viewport.width() - 8, y)
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.Type.ToolTip, outside, viewport.mapToGlobal(outside)))
    wait_until(lambda: False, 0.15)
    assert not view.hover_card.isVisible()


# ---- resaltado solo sobre el nombre ----

def test_hover_highlight_follows_the_name_only(window):
    view = window.source
    details = view.details
    viewport = details.viewport()
    point = name_point(view, "a.txt")
    row = view.model.row_of(os.path.join(view.path, "a.txt"))
    QTest.mouseMove(viewport, point)
    assert wait_until(lambda: details.hover_row == row, 1)
    QTest.mouseMove(viewport, QPoint(viewport.width() - 8, point.y()))
    assert wait_until(lambda: details.hover_row == -1, 1)
    QTest.mouseMove(viewport, point)
    assert wait_until(lambda: details.hover_row == row, 1)
    QApplication.sendEvent(viewport, QEvent(QEvent.Type.Leave))
    assert details.hover_row == -1


def test_name_rect_covers_icon_and_text_not_the_column(window):
    view = window.source
    details = view.details
    index = view.model.index(view.model.row_of(os.path.join(view.path, "a.txt")), 0)
    cell, area = details.visualRect(index), details.name_rect(index)
    assert area.left() > cell.left() and area.right() < cell.right()
    assert area.width() >= details.fontMetrics().horizontalAdvance("a.txt")
    assert details.name_index_at(area.center()).row() == index.row()
    assert not details.name_index_at(QPoint(area.right() + 5, area.center().y())).isValid()


def test_highlight_is_painted_only_inside_the_name(app, tmp_path):
    (tmp_path / "archivo.txt").write_text("x", encoding="utf-8")
    view = FileView()
    view.setStyleSheet("QAbstractItemView { background: #000000; }")
    view.resize(800, 300)
    view.navigate(str(tmp_path))
    view.show()
    assert wait_until(lambda: view.model.rowCount() == 1)
    wait_until(lambda: False, 0.05)
    details = view.details
    details.set_hover_row(0)
    wait_until(lambda: False, 0.05)
    image = details.viewport().grab().toImage()
    area = details.name_rect(view.model.index(0, 0))
    expected = QColor(theme.current().hover)
    assert image.pixelColor(area.right() - 1, area.top() + 1) == expected
    assert image.pixelColor(area.right() + 8, area.top() + 1) != expected
    assert image.pixelColor(details.viewport().width() - 5, area.top() + 1) != expected
    view.close()


# ---- línea de la barra de pestañas ----

def test_tab_bar_draws_no_base_line(window):
    assert all(not group.tabBar().drawBase() for group in window.groups)


# ---- menús contextuales ----

def labels(menu):
    return [action.text() for action in menu.actions() if action.text()]


def right_click(view, point):
    """Secuencia completa: pulsación, menú y soltar, como en Windows."""
    viewport = view.current_view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, viewport.mapToGlobal(point))
    QApplication.sendEvent(viewport, event)
    QTest.mouseRelease(viewport, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)


@pytest.fixture
def menus(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_show_file_menu", lambda view, pos: calls.append(("archivo", view)))
    monkeypatch.setattr(window, "_show_folder_menu", lambda view, pos: calls.append(("carpeta", view)))
    for view in window.all_views():
        view.fileMenu.disconnect()
        view.folderMenu.disconnect()
        view.fileMenu.connect(lambda v, p: calls.append(("archivo", v)))
        view.folderMenu.connect(lambda v, p: calls.append(("carpeta", v)))
    return calls


def test_right_click_on_name_opens_file_menu_and_selects(window, menus):
    view = window.source
    right_click(view, name_point(view, "a.txt"))
    assert menus == [("archivo", view)]
    assert [os.path.basename(p) for p in view.selected_paths()] == ["a.txt"]


def test_right_click_on_selected_file_keeps_multiple_selection(window, menus):
    view = window.source
    select(view, "a.txt", "b.txt")
    right_click(view, name_point(view, "b.txt"))
    assert menus[0][0] == "archivo"
    assert len(view.selected_paths()) == 2


def test_right_click_on_row_outside_name_opens_folder_menu_without_selecting(window, menus):
    view = window.source
    viewport = view.details.viewport()
    point = QPoint(viewport.width() - 8, name_point(view, "a.txt").y())
    right_click(view, point)
    assert menus == [("carpeta", view)]
    assert view.selected_paths() == []


def test_right_click_on_empty_area_opens_folder_menu_and_keeps_selection(window, menus):
    view = window.source
    select(view, "b.txt")
    viewport = view.details.viewport()
    right_click(view, QPoint(30, viewport.height() - 6))
    assert menus == [("carpeta", view)]
    assert [os.path.basename(p) for p in view.selected_paths()] == ["b.txt"]


def test_right_click_in_thumbnails(window, menus):
    window.run_command("Set VIEW=Thumbnails")
    view = window.source
    right_click(view, name_point(view, "a.txt"))
    assert menus[-1][0] == "archivo"
    viewport = view.thumbs.viewport()
    right_click(view, QPoint(viewport.width() - 6, viewport.height() - 6))
    assert menus[-1][0] == "carpeta"


def test_right_click_activates_the_other_pane(window):
    dest = window.dest
    point = QPoint(30, dest.details.viewport().height() - 6)
    QTest.mousePress(dest.details.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseRelease(dest.details.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
    assert window.source is dest


def test_keyboard_menu_key(window, menus):
    view = window.source
    view.focus_list()
    QTest.keyClick(view.details, Qt.Key.Key_Menu)
    assert menus[-1][0] == "carpeta"
    select(view, "a.txt")
    view.details.selectionModel().setCurrentIndex(view.model.index(view.model.row_of(os.path.join(view.path, "a.txt")), 0),
                                                  QItemSelectionModel.SelectionFlag.NoUpdate)
    QTest.keyClick(view.details, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    assert menus[-1][0] == "archivo"


def test_file_menu_contents(window):
    view = window.source
    select(view, "a.txt")
    entries = labels(window.build_file_menu(view))
    for expected in ("Abrir", "Copiar", "Cortar", "Copiar a destino", "Renombrar", "Enviar a la papelera", "Propiedades"):
        assert expected in entries
    assert "Nueva carpeta" not in entries and "Pegar" not in entries and "Abrir terminal aquí" not in entries
    assert "Abrir en una pestaña nueva" not in entries
    select(view, "subcarpeta")
    assert "Abrir en una pestaña nueva" in labels(window.build_file_menu(view))
    select(view, "a.txt", "b.txt")
    entries = labels(window.build_file_menu(view))
    assert "Abrir" not in entries and "Calcular hash" in entries


def test_folder_menu_contents(window):
    entries = labels(window.build_folder_menu(window.source))
    for expected in ("Pegar", "Nueva carpeta", "Ver", "Ordenar por", "Actualizar", "Abrir terminal aquí",
                     "Copiar la ruta de esta carpeta", "Propiedades de esta carpeta"):
        assert expected in entries
    for wrong in ("Renombrar", "Enviar a la papelera", "Copiar a destino", "Eliminar definitivamente"):
        assert wrong not in entries


def test_menus_inside_archive_disable_what_cannot_work(window, tree):
    left, _ = tree
    with zipfile.ZipFile(left / "z.zip", "w") as zf:
        zf.writestr("dentro.txt", "x")
    view = window.source
    view.navigate(str(left / "z.zip"))
    assert wait_until(lambda: names(view) == ["dentro.txt"])
    select(view, "dentro.txt")

    def enabled(menu, label):
        return next(a for a in menu.actions() if a.text() == label).isEnabled()

    file_menu = window.build_file_menu(view)
    assert enabled(file_menu, "Copiar") and enabled(file_menu, "Copiar a destino")
    assert not enabled(file_menu, "Renombrar") and not enabled(file_menu, "Enviar a la papelera")
    folder_menu = window.build_folder_menu(view)
    assert not enabled(folder_menu, "Nueva carpeta") and not enabled(folder_menu, "Pegar")
    assert not enabled(folder_menu, "Propiedades de esta carpeta")


def test_all_menu_entries_use_available_commands(window, monkeypatch):
    commands_used = []
    monkeypatch.setattr(window, "_add_command", lambda menu, label, command, *a, **k: (commands_used.append(command),
                                                                                       QMenu.addAction(menu, label))[1])
    view = window.source
    (view.path and select(view, "a.txt", "subcarpeta"))
    window.build_file_menu(view)
    window.build_folder_menu(view)
    assert commands_used
    assert [c for c in commands_used if window.commands_unavailable([c])] == []


def test_folder_menu_actions_work(window, tree, monkeypatch):
    left, _ = tree
    view = window.source
    monkeypatch.setattr(AddFavoriteDialog, "exec", lambda _dialog: 1)

    def trigger(label, menu=None):
        menu = menu or window.build_folder_menu(view)
        next(a for a in menu.actions() if a.text() == label).trigger()

    trigger("Nuevo archivo de texto")
    assert (left / "Nuevo archivo.txt").exists()
    trigger("Copiar la ruta de esta carpeta")
    assert QApplication.clipboard().text() == view.path
    trigger("Añadir a favoritos")
    assert window.config["favorites"][0]["path"] == view.path
    trigger("Mostrar ocultos")
    assert window.config["show_hidden"] is True


def test_view_submenu_marks_current_mode(window):
    menu = window.build_folder_menu(window.source)
    modes = next(a.menu() for a in menu.actions() if a.text() == "Ver")
    checked = [a.text() for a in modes.actions() if a.isChecked()]
    assert checked == ["Detalles"]
    next(a for a in modes.actions() if a.text() == "Lista").trigger()
    assert window.source.view_mode == "list"


def test_file_menu_open_navigates_into_folder(window, tree):
    left, _ = tree
    view = window.source
    select(view, "subcarpeta")
    next(a for a in window.build_file_menu(view).actions() if a.text() == "Abrir").trigger()
    assert wait_until(lambda: view.path == os.path.normpath(str(left / "subcarpeta")))


def test_properties_measures_folder_without_blocking(app, tree):
    left, _ = tree
    dialog = PropertiesDialog([entry_from_path(str(left))])
    assert dialog.size_label.text() == "Calculando…"
    assert wait_until(lambda: "3 archivos" in dialog.size_label.text())
    dialog.reject()


def test_closing_properties_cancels_measurement(app, tree):
    left, _ = tree
    dialog = PropertiesDialog([entry_from_path(str(left))])
    dialog.reject()
    assert dialog._measure.sink.is_cancelled
    wait_until(lambda: False, 0.1)  # el resultado que llegue tarde se ignora sin errores


def test_properties_command_for_current_folder(window, monkeypatch):
    seen = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.PropertiesDialog",
                        lambda entries, parent: type("D", (), {"exec": lambda s: seen.append(entries)})())
    window.run_command("Properties CURRENT")
    assert seen[0][0].path == window.source.path and seen[0][0].is_dir


# ---- selección con clics reales ----

def mouse(view_widget, kind, point, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    QApplication.sendEvent(view_widget, QMouseEvent(kind, QPointF(point), QPointF(view_widget.mapToGlobal(point)), button,
                                                    buttons, modifiers))


def click(view_widget, point, modifiers=Qt.KeyboardModifier.NoModifier):
    mouse(view_widget, QEvent.Type.MouseButtonPress, point, modifiers=modifiers)
    mouse(view_widget, QEvent.Type.MouseButtonRelease, point, modifiers=modifiers)


def double_click(view_widget, point):
    click(view_widget, point)
    mouse(view_widget, QEvent.Type.MouseButtonDblClick, point)
    mouse(view_widget, QEvent.Type.MouseButtonRelease, point)


def selected_names(view):
    return [e.name for e in view.selected_entries()]


def item_point(view, name):
    return view.thumbs.visualRect(view.model.index(view.model.row_of(os.path.join(view.path, name)), 0)).center()


@pytest.mark.parametrize("mode", ["Thumbnails", "List", "SmallIcons", "LargeIcons", "Tiles", "FilmStrip"])
def test_real_click_selects_in_every_view(window, tree, mode):
    """Con `selectedRows()`, un clic en estas vistas no seleccionaba nada para la aplicación."""
    _, right = tree
    window.run_command(f"Set VIEW={mode}")
    view = window.source
    wait_until(lambda: False, 0.05)
    click(view.thumbs.viewport(), item_point(view, "a.txt"))
    assert selected_names(view) == ["a.txt"]
    window.run_command("Copy")
    assert wait_until(lambda: (right / "a.txt").exists())


def test_viewer_shows_file_clicked_in_thumbnails(window, tree):
    left, _ = tree
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    image.save(str(left / "foto.png"))
    window.run_command("Set VIEWPANE=On")
    window.run_command("Set VIEW=Thumbnails")
    view = window.source
    assert wait_until(lambda: "foto.png" in names(view))
    wait_until(lambda: False, 0.05)
    click(view.thumbs.viewport(), item_point(view, "foto.png"))
    preview = window.preview
    assert wait_until(lambda: preview.stack.currentWidget() is preview.image and not preview.image.pixmap().isNull())
    click(view.thumbs.viewport(), item_point(view, "a.txt"))
    assert wait_until(lambda: preview.text.toPlainText() == "A" and preview.stack.currentWidget() is preview.text)


def test_viewer_shows_file_clicked_in_details(window, tree):
    window.run_command("Set VIEWPANE=On")
    view = window.source
    click(view.details.viewport(), name_point(view, "a.txt"))
    assert wait_until(lambda: window.preview.text.toPlainText() == "A")


def test_click_on_name_selects_and_outside_does_not(window):
    view = window.source
    viewport = view.details.viewport()
    point = name_point(view, "a.txt")
    click(viewport, QPoint(viewport.width() - 8, point.y()))
    assert selected_names(view) == []
    area = view.details.name_rect(view.model.index(view.model.row_of(os.path.join(view.path, "a.txt")), 0))
    click(viewport, QPoint(area.right() + 6, point.y()))
    assert selected_names(view) == []
    click(viewport, point)
    assert selected_names(view) == ["a.txt"]


def test_click_outside_name_clears_selection_unless_ctrl(window):
    view = window.source
    viewport = view.details.viewport()
    click(viewport, name_point(view, "a.txt"))
    outside = QPoint(viewport.width() - 8, viewport.height() - 8)
    click(viewport, outside, Qt.KeyboardModifier.ControlModifier)
    assert selected_names(view) == ["a.txt"]
    click(viewport, outside)
    assert selected_names(view) == []


def test_ctrl_click_on_names_adds_to_selection(window):
    view = window.source
    viewport = view.details.viewport()
    click(viewport, name_point(view, "a.txt"))
    click(viewport, name_point(view, "b.txt"), Qt.KeyboardModifier.ControlModifier)
    assert sorted(selected_names(view)) == ["a.txt", "b.txt"]


def test_drag_from_outside_name_selects_what_it_touches(window):
    view = window.source
    viewport = view.details.viewport()
    start = QPoint(viewport.width() - 8, name_point(view, "a.txt").y())
    mouse(viewport, QEvent.Type.MouseButtonPress, start)
    mouse(viewport, QEvent.Type.MouseMove, name_point(view, "b.txt"))
    mouse(viewport, QEvent.Type.MouseButtonRelease, name_point(view, "b.txt"))
    assert selected_names(view) == ["a.txt", "b.txt"]


def test_full_row_option_restores_old_behaviour(window):
    window.run_command("Set FULLROWSELECT=Toggle,NameCol")
    view = window.source
    viewport = view.details.viewport()
    click(viewport, QPoint(viewport.width() - 8, name_point(view, "a.txt").y()))
    assert selected_names(view) == ["a.txt"]
    assert window.config["full_row_select"] is True


def test_selection_is_painted_only_on_the_name(window, tree):
    """Se compara con una fila sin seleccionar: Qt pinta su propio fondo de fila antes que los delegados."""
    view = window.source
    details = view.details
    viewport = details.viewport()
    index_a = view.model.index(view.model.row_of(os.path.join(view.path, "a.txt")), 0)
    index_b = view.model.index(view.model.row_of(os.path.join(view.path, "b.txt")), 0)
    select(view, "a.txt")
    wait_until(lambda: False, 0.05)
    image = viewport.grab().toImage()
    area = details.name_rect(index_a)
    other = details.name_rect(index_b)
    columns = [area.right() + 8, viewport.width() // 2, viewport.width() - 5]
    expected = QColor(theme.current().sel_source)
    assert image.pixelColor(area.right() - 1, area.top() + 1) == expected
    for x in columns:
        assert image.pixelColor(x, area.top() + 1) == image.pixelColor(x, other.top() + 1)
    window.run_command("Set FULLROWSELECT=On")
    wait_until(lambda: False, 0.05)
    image = viewport.grab().toImage()
    for x in columns:
        assert image.pixelColor(x, area.top() + 1) == expected


def test_rename_editor_keeps_visible_selection_color(window):
    view = window.source
    select(view, "a.txt")
    view.current_view.setCurrentIndex(view.model.index(view.model.row_of(os.path.join(view.path, "a.txt")), 0))
    view.begin_rename()
    editor = view.details.findChild(QLineEdit)
    assert editor is not None
    assert editor.palette().color(QPalette.ColorRole.Highlight).alpha() == 255
    assert view.details.palette().color(QPalette.ColorRole.Highlight).alpha() == 0


def test_selection_stays_name_only_after_theme_change(window):
    window.config["theme"] = "light"
    window.apply_theme()
    assert window.source.details.palette().color(QPalette.ColorRole.Highlight).alpha() == 0
    window.config["theme"] = "dark"
    window.apply_theme()


def test_breadcrumb_hides_old_segments_at_once(app):
    crumb = Breadcrumb()
    crumb.set_path(os.path.abspath(os.path.join(os.sep, "uno", "dos")))
    old = list(crumb._buttons)
    crumb.set_path(os.path.abspath(os.path.join(os.sep, "tres")))
    assert all(button.isHidden() for button in old)


# ---- doble clic ----

def count_opened(view):
    opened = []
    view.openFile.disconnect()
    view.openFile.connect(lambda _v, entry: opened.append(entry.name))
    return opened


def test_double_click_opens_file_only_once(window):
    view = window.source
    opened = count_opened(view)
    double_click(view.details.viewport(), name_point(view, "a.txt"))
    assert opened == ["a.txt"]


@pytest.mark.parametrize("mode", ["Thumbnails", "List"])
def test_double_click_opens_once_in_other_views(window, mode):
    window.run_command(f"Set VIEW={mode}")
    view = window.source
    opened = count_opened(view)
    wait_until(lambda: False, 0.05)
    double_click(view.thumbs.viewport(), item_point(view, "a.txt"))
    assert opened == ["a.txt"]


def test_double_click_enters_folder_with_one_history_step(window, tree):
    left, _ = tree
    view = window.source
    before = len(view._history)
    double_click(view.details.viewport(), name_point(view, "subcarpeta"))
    assert wait_until(lambda: view.path == os.path.normpath(str(left / "subcarpeta")))
    assert len(view._history) == before + 1


def test_double_click_outside_name_opens_nothing(window):
    view = window.source
    opened = count_opened(view)
    viewport = view.details.viewport()
    double_click(viewport, QPoint(viewport.width() - 8, name_point(view, "a.txt").y()))
    assert opened == []


def test_enter_key_opens_once(window):
    view = window.source
    opened = count_opened(view)
    click(view.details.viewport(), name_point(view, "a.txt"))
    QTest.keyClick(view.details, Qt.Key.Key_Return)
    assert opened == ["a.txt"]


# ---- Este equipo ----

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="«Este equipo» solo existe en Windows")


def test_breadcrumb_targets_are_real_paths(tmp_path):
    crumbs = path_crumbs(str(tmp_path / "a"))
    assert crumbs[-1] == ("a", str(tmp_path / "a"))
    assert all(label for label, _target in crumbs)


@windows_only
def test_breadcrumb_starts_with_this_pc():
    crumbs = path_crumbs("C:\\tests\\x")
    assert [label for label, _ in crumbs] == ["Este equipo", "C:", "tests", "x"]
    assert crumbs[0][1] == COMPUTER_PATH and crumbs[1][1] == "C:\\"
    assert path_crumbs(COMPUTER_PATH) == [("Este equipo", COMPUTER_PATH)]


@windows_only
def test_computer_lists_drives(window):
    view = window.source
    window.run_command("Go /mycomputer")
    assert wait_until(lambda: view.model.path == COMPUTER_PATH and view.model.row_of("C:\\") >= 0)
    drive = next(e for e in view.model.entries if e.path.upper().startswith("C:"))
    assert drive.is_drive and drive.is_dir and "(C:)" in drive.name and "Unidad" in drive.type_label
    assert window.groups[0].tabText(window.groups[0].indexOf(view)) == "Este equipo"
    assert "Este equipo" in window.windowTitle() and "<" not in window.windowTitle()


@windows_only
def test_up_from_drive_root_reaches_computer_and_back(window):
    view = window.source
    view.navigate("C:\\")
    window.run_command("Go UP")
    assert wait_until(lambda: view.path == COMPUTER_PATH and view.model.row_of("C:\\") >= 0)
    window.run_command("Go UP")
    assert view.path == COMPUTER_PATH
    assert view.model.row_of("C:\\") >= 0
    double_click(view.details.viewport(), view.details.name_rect(view.model.index(view.model.row_of("C:\\"), 0)).center())
    assert wait_until(lambda: view.path == "C:\\")


@windows_only
@pytest.mark.parametrize("command", ["Delete NORECYCLE", "Delete", "Copy", "Copy MOVE", "Rename ADVANCED", "Archive",
                                     'CreateFolder NAME="x"', "SetAttr MODIFIED=now", "GetSizes", "Clipboard CUT"])
def test_destructive_commands_blocked_on_computer(window, tree, monkeypatch, command):
    _, right = tree
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    view = window.source
    window.run_command("Go /mycomputer")
    assert wait_until(lambda: view.model.row_of("C:\\") >= 0)
    view.select_paths(["C:\\"])
    assert selected_names(view)
    window.run_command(command)
    assert window.jobs.running == 0
    assert "No disponible" in window.statusBar().currentMessage()
    assert os.listdir(right) == []


@windows_only
def test_copy_into_computer_pane_is_rejected(window, tree, monkeypatch):
    errors = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: errors.append(a[2]))
    window.dest.navigate(COMPUTER_PATH)
    select(window.source, "a.txt")
    window.run_command("Copy")
    assert errors and window.jobs.running == 0


@windows_only
def test_computer_menus_offer_only_safe_entries(window):
    view = window.source
    window.run_command("Go /mycomputer")
    assert wait_until(lambda: view.model.row_of("C:\\") >= 0)
    view.select_paths(["C:\\"])
    entries = labels(window.build_file_menu(view))
    assert "Abrir" in entries and "Propiedades" in entries
    assert "Enviar a la papelera" not in entries and "Renombrar" not in entries
    folder = window.build_folder_menu(view)
    assert not next(a for a in folder.actions() if a.text() == "Nueva carpeta").isEnabled()


@windows_only
def test_computer_tab_survives_restart(app, tmp_path, tree):
    cfg_dir = str(tmp_path / "equipo")
    first = MainWindow(Config(cfg_dir), [str(tree[0]), str(tree[1])])
    first.source.navigate(COMPUTER_PATH)
    first.close()
    second = MainWindow(Config(cfg_dir))
    try:
        assert second.groups[0].current.path == COMPUTER_PATH
    finally:
        second.close()


@windows_only
def test_typing_this_pc_in_the_path_field(window):
    view = window.source
    view.breadcrumb.begin_edit()
    view.breadcrumb._edit.setText("este equipo")
    view.breadcrumb._commit()
    assert view.path == COMPUTER_PATH


# ---- Este equipo con grupos, espacio libre y uso ----

def fake_drives():
    return [
        Entry("Rápido (C:)", "C:\\", True, dir_size=1000, free_space=50, is_drive=True, filesystem="NTFS", drive_kind="ssd"),
        Entry("Lento (E:)", "E:\\", True, dir_size=1000, free_space=800, is_drive=True, filesystem="exFAT", drive_kind="hdd"),
        Entry("Otro (D:)", "D:\\", True, dir_size=2000, free_space=1000, is_drive=True, filesystem="NTFS", drive_kind="ssd"),
        Entry("Red (Z:)", "Z:\\", True, is_drive=True, drive_kind="network"),
    ]


@pytest.fixture
def computer_view(window, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.core.drives.list_drives", lambda with_details=True: fake_drives())
    view = window.source
    view.navigate(COMPUTER_PATH)
    assert wait_until(lambda: view.model.path == COMPUTER_PATH and view.model.row_of("C:\\") >= 0)
    return view


def test_computer_groups_and_order(computer_view):
    model = computer_view.model
    rows = [model.index(row, 0).data() for row in range(model.rowCount())]
    assert rows == ["Unidades de Estado Sólido (2)", "Otro (D:)", "Rápido (C:)",
                    "Unidades de Disco Duro (1)", "Lento (E:)",
                    "Unidades de Red (1)", "Red (Z:)"]


def test_computer_columns_show_free_and_filesystem(computer_view):
    model = computer_view.model
    row = model.row_of("C:\\")
    by_key = {key: model.index(row, index).data() for index, (key, _label) in enumerate(COLUMNS)}
    assert by_key["free"] == "50 B" and by_key["filesystem"] == "NTFS"
    assert by_key["type"] == "Unidad de Disco Local (SSD)"
    assert model.entry(row).used_fraction == pytest.approx(0.95)
    details = computer_view.details
    assert details.isColumnHidden(COLUMN_INDEX["modified"]) and details.isColumnHidden(COLUMN_INDEX["attrs"])
    computer_view.resize(1000, 400)
    assert not details.isColumnHidden(COLUMN_INDEX["free"]) and not details.isColumnHidden(COLUMN_INDEX["used"])


@pytest.mark.parametrize("width,gone,kept", [
    (1100, [], ["filesystem", "type", "used", "free"]),
    (800, ["filesystem"], ["type", "used", "free"]),
    (600, ["filesystem", "type"], ["used", "free"]),
    (420, ["filesystem", "type", "used"], ["free"]),
])
def test_narrow_computer_view_keeps_drive_names_readable(computer_view, width, gone, kept):
    computer_view._apply_columns(width)
    computer_view._apply_columns(width)  # el resultado no debe cambiar al aplicarlo otra vez
    details = computer_view.details
    for key in gone:
        assert details.isColumnHidden(COLUMN_INDEX[key]), key
    for key in kept:
        assert not details.isColumnHidden(COLUMN_INDEX[key]), key


def test_columns_return_to_normal_in_a_folder(computer_view, tree):
    left, _ = tree
    computer_view.navigate(str(left))
    assert wait_until(lambda: "a.txt" in names(computer_view))
    details = computer_view.details
    assert details.isColumnHidden(COLUMN_INDEX["free"]) and details.isColumnHidden(COLUMN_INDEX["filesystem"])
    assert not details.isColumnHidden(COLUMN_INDEX["modified"])
    assert computer_view.model.group_rows() == []
    assert details.rowSpan(0, 0) == 1


def test_group_rows_are_not_elements(computer_view):
    model = computer_view.model
    group = model.group_rows()[0]
    assert model.entry(group) is None
    assert not model.index(group, 0).flags() & Qt.ItemFlag.ItemIsSelectable
    assert all(not e.is_group for e in model.entries) and len(model.entries) == 4
    computer_view.select_all()
    assert all(e.is_drive for e in computer_view.selected_entries())
    details = computer_view.details
    assert details.columnSpan(group, 0) == len(COLUMNS)
    center = details.visualRect(model.index(group, 0)).center()
    click(details.viewport(), center)
    double_click(details.viewport(), center)
    assert computer_view.path == COMPUTER_PATH


def test_sorting_keeps_groups(computer_view):
    window_header = computer_view.details.horizontalHeader()
    window_header.setSortIndicator(COLUMN_INDEX["free"], Qt.SortOrder.DescendingOrder)
    model = computer_view.model
    rows = [model.index(row, 0).data() for row in range(model.rowCount())]
    assert rows[:3] == ["Unidades de Estado Sólido (2)", "Otro (D:)", "Rápido (C:)"]
    window_header.setSortIndicator(COLUMN_INDEX["free"], Qt.SortOrder.AscendingOrder)
    assert model.index(1, 0).data() == "Rápido (C:)"
    assert computer_view.details.columnSpan(0, 0) == len(COLUMNS)


def test_groups_are_hidden_in_other_views(window, computer_view):
    window.run_command("Set VIEW=Thumbnails")
    model = computer_view.model
    for group in model.group_rows():
        assert computer_view.thumbs.isRowHidden(group)
    assert not computer_view.thumbs.isRowHidden(model.row_of("C:\\"))


def test_filter_drops_empty_groups(computer_view):
    computer_view.model.set_filter("Lento")
    model = computer_view.model
    assert [model.index(row, 0).data() for row in range(model.rowCount())] == ["Unidades de Disco Duro (1)", "Lento (E:)"]


def test_usage_bar_is_painted_red_when_nearly_full(window, computer_view):
    computer_view.resize(1000, 400)
    wait_until(lambda: False, 0.05)
    details = computer_view.details
    model = computer_view.model
    column = COLUMN_INDEX["used"]
    image = details.viewport().grab().toImage()
    full = details.visualRect(model.index(model.row_of("C:\\"), column))
    half = details.visualRect(model.index(model.row_of("D:\\"), column))
    t = theme.current()
    assert image.pixelColor(full.left() + 20, full.center().y()) == QColor(t.meter_low)
    assert image.pixelColor(half.left() + 20, half.center().y()) == QColor(t.meter_ok)
    assert image.pixelColor(half.right() - 12, half.center().y()) == QColor(t.border)


def test_expanding_a_drive_shows_its_folders(computer_view, tree, monkeypatch):
    left, _ = tree
    model = computer_view.model
    drive = model.entries[0]
    drive.path = str(left)
    model.set_expanded(drive, True)
    assert wait_until(lambda: "subcarpeta" in [e.name for e in model.entries])
    assert any(e.name == "subcarpeta" for e in computer_view.model.entries)


@windows_only
def test_real_drive_data_is_read(window):
    from directory_commander_tux_tux.core.drives import list_drives
    drives = list_drives()
    system = next(d for d in drives if d.path.upper().startswith("C:"))
    assert system.drive_kind in ("ssd", "hdd", "local")
    assert system.dir_size and system.free_space is not None and system.filesystem
    assert 0 <= system.used_fraction <= 1
    light = next(d for d in list_drives(with_details=False) if d.path.upper().startswith("C:"))
    assert light.drive_kind == "" and light.dir_size is None


# ---- árbol con secciones ----

def tree_children(item):
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_tree_has_home_favorites_and_drives(window):
    tree_widget = window.tree
    tops = [tree_widget.topLevelItem(i) for i in range(tree_widget.topLevelItemCount())]
    assert [t.isExpanded() for t in tops] == [True, True, True]
    assert tops[0] is tree_widget.home_item and tops[1] is tree_widget.favorites_item and tops[2] is tree_widget.root_item
    assert assert_label(tops[0]) and tops[1].text(0) == "Favoritos"


def assert_label(item):
    return item.text(0) == ("Inicio" if sys.platform == "win32" else "Home")


@windows_only
def test_home_section_lists_known_folders(window):
    labels_found = tree_children(window.tree.home_item)
    assert labels_found and set(labels_found) <= {"Escritorio", "Descargas", "Documentos", "Imágenes", "Música", "Vídeos"}


def test_linux_home_is_a_real_folder_section(app, monkeypatch, tmp_path):
    monkeypatch.setattr("directory_commander_tux_tux.ui.folder_tree.has_computer_view", lambda: False)
    monkeypatch.setattr("directory_commander_tux_tux.ui.folder_tree.home_dir", lambda: str(tmp_path))
    (tmp_path / "Proyectos").mkdir()
    (tmp_path / "archivo.txt").write_text("x", encoding="utf-8")
    from directory_commander_tux_tux.ui.folder_tree import FolderTree
    widget = FolderTree()
    try:
        assert widget.home_item.text(0) == "Home" and widget.root_item.text(0) == "Sistema de archivos"
        assert wait_until(lambda: tree_children(widget.home_item) == ["Proyectos"])
    finally:
        widget.dispose()


def test_favorites_appear_in_tree_and_follow_changes(window, tree):
    left, _ = tree
    window.run_command("Favorites ADD")
    favorites = window.tree.favorites_item
    assert tree_children(favorites) == [os.path.basename(window.source.path)]
    assert favorites.child(0).data(0, Qt.ItemDataRole.UserRole) == window.source.path
    window.config["favorites"] = []
    window._rebuild_favorites_bar()
    assert favorites.childCount() == 0


def test_missing_favorite_is_disabled(window, tmp_path):
    window.config["favorites"] = [{"label": "Borrada", "path": str(tmp_path / "ya-no-existe")}]
    window._rebuild_favorites_bar()
    assert window.tree.favorites_item.child(0).isDisabled()


def test_clicking_tree_item_navigates(window, tree):
    left, _ = tree
    window.config["favorites"] = [{"label": "Sub", "path": str(left / "subcarpeta")}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item.child(0)
    rect = window.tree.visualItemRect(item)
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert wait_until(lambda: window.source.path == os.path.normpath(str(left / "subcarpeta")))


def test_expanding_favorite_loads_subfolders_only(window, tree):
    left, _ = tree
    window.config["favorites"] = [{"label": "Izq", "path": str(left)}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item.child(0)
    item.setExpanded(True)
    assert wait_until(lambda: tree_children(item) == ["subcarpeta"])
    assert item.child(0).childCount() == 0


def test_tree_updates_when_a_folder_appears(window, tree):
    left, _ = tree
    window.config["favorites"] = [{"label": "Izq", "path": str(left)}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item.child(0)
    item.setExpanded(True)
    assert wait_until(lambda: tree_children(item) == ["subcarpeta"])
    (left / "nueva").mkdir()
    assert wait_until(lambda: "nueva" in tree_children(item), 5)
    (left / "nueva").rmdir()
    assert wait_until(lambda: "nueva" not in tree_children(item), 5)


def test_tree_keeps_expanded_children_on_reload(window, tree):
    left, _ = tree
    (left / "subcarpeta" / "dentro").mkdir()
    window.config["favorites"] = [{"label": "Izq", "path": str(left)}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item.child(0)
    item.setExpanded(True)
    assert wait_until(lambda: tree_children(item) == ["subcarpeta"])
    child = item.child(0)
    child.setExpanded(True)
    assert wait_until(lambda: tree_children(child) == ["dentro"])
    (left / "otra").mkdir()
    assert wait_until(lambda: "otra" in tree_children(item), 5)
    assert child.isExpanded() and tree_children(child) == ["dentro"]


def test_folder_without_subfolders_has_no_arrow(window, tree):
    left, _ = tree
    window.config["favorites"] = [{"label": "Izq", "path": str(left)}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item.child(0)
    item.setExpanded(True)
    assert wait_until(lambda: tree_children(item) == ["subcarpeta"])
    policy = item.child(0).childIndicatorPolicy()
    assert policy == QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless


@windows_only
def test_tree_selects_this_pc(window):
    window.run_command("Go /mycomputer")
    assert wait_until(lambda: window.tree.current_path() == COMPUTER_PATH)


@windows_only
def test_tree_lists_drives_under_this_pc(window):
    assert wait_until(lambda: window.tree.root_item.childCount() > 0, 5)
    assert any("(C:)" in text for text in tree_children(window.tree.root_item))


def test_closing_window_while_tree_loads_is_safe(app, tmp_path, tree):
    left, right = tree
    win = MainWindow(Config(str(tmp_path / "cerrar")), [str(left), str(right)])
    win.show()
    win.tree.sync_to(str(left))
    win.close()
    wait_until(lambda: False, 0.2)


def test_card_stays_on_screen(window):
    card = window.source.hover_card
    screen = QGuiApplication.primaryScreen().availableGeometry()
    card._position = screen.bottomRight()
    card.resize(300, 200)
    card._place()
    assert card.x() + card.width() <= screen.right() + 1 and card.y() + card.height() <= screen.bottom() + 1


def test_status_row_counts(window, tree):
    view = window.source
    assert wait_until(lambda: view.status.dirs.text() == "0 / 1" and view.status.files.text() == "0 / 2")
    select(view, "a.txt")
    assert view.status.files.text() == "1 / 2"
    assert "txt" in view.status.chip.text()
    assert view.status.meter.isVisibleTo(view.status)


# ---- carpetas desplegables ----

def test_expand_folder_inline(window, tree):
    left, right = tree
    view = window.source
    entry = next(e for e in view.model.entries if e.name == "subcarpeta")
    view.model.set_expanded(entry, True)
    assert wait_until(lambda: names(view) == ["subcarpeta", "c.txt", "a.txt", "b.txt"])
    child = next(e for e in view.model.entries if e.name == "c.txt")
    assert child.depth == 1
    view.select_paths([child.path])
    window.run_command("Copy")
    assert wait_until(lambda: (right / "c.txt").exists())
    view.model.set_expanded(next(e for e in view.model.entries if e.name == "subcarpeta"), False)
    assert names(view) == ["subcarpeta", "a.txt", "b.txt"]


def test_expanded_state_and_selection_survive_refresh(window):
    view = window.source
    view.model.set_expanded(next(e for e in view.model.entries if e.name == "subcarpeta"), True)
    assert wait_until(lambda: "c.txt" in names(view))
    child = next(e for e in view.model.entries if e.name == "c.txt")
    view.select_paths([child.path])
    view.refresh()
    time.sleep(0.05)
    assert wait_until(lambda: "c.txt" in names(view) and [e.name for e in view.selected_entries()] == ["c.txt"])


def test_parent_and_child_selected_copy_only_parent(window, tree):
    _, right = tree
    view = window.source
    view.model.set_expanded(next(e for e in view.model.entries if e.name == "subcarpeta"), True)
    assert wait_until(lambda: "c.txt" in names(view))
    assert len(view.selected_paths()) == 0
    window.run_command("Select ALL")
    assert sorted(os.path.basename(p) for p in view.selected_paths()) == ["a.txt", "b.txt", "subcarpeta"]
    window.run_command("Copy")
    assert wait_until(lambda: (right / "subcarpeta" / "c.txt").exists() and window.jobs.running == 0)
    assert not (right / "c.txt").exists()


def test_chevron_click_expands_without_opening(window):
    view = window.source
    index = view.model.index(view.model.row_of(next(e.path for e in view.model.entries if e.name == "subcarpeta")), 0)
    rect = view.details.visualRect(index)
    QTest.mouseClick(view.details.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     rect.topLeft() + QPoint(5, rect.height() // 2))
    assert wait_until(lambda: "c.txt" in names(view))
    assert view.path.endswith("izq")


# ---- vistas, estilos y tema ----

@pytest.mark.parametrize("mode", ["List", "SmallIcons", "LargeIcons", "Tiles", "Thumbnails", "FilmStrip", "Details", "Power"])
def test_every_view_mode_renders(window, mode):
    window.run_command(f"Set VIEW={mode}")
    window.source.grab()
    assert window.source.view_mode == {"power": "details"}.get(mode.lower(), mode.lower())


def test_view_cycle(window):
    window.run_command("Set VIEW=Cycle,Details,List,Tiles")
    assert window.source.view_mode == "list"
    window.run_command("Set VIEW=Cycle,Details,List,Tiles")
    assert window.source.view_mode == "tiles"
    window.run_command("Set VIEW=Cycle,Details,List,Tiles")
    assert window.source.view_mode == "details"


def test_styles_apply_layout(window):
    window.run_command('Set STYLE="Único"')
    assert window.dest is None and window.tree_dock.isHidden() and window.config["style"] == "Único"
    window.run_command('Set STYLE="Imágenes"')
    assert window.source.view_mode == "thumbnails" and not window.viewer_dock.isHidden()
    window.run_command('Set STYLE="Tira de Fotogramas"')
    assert window.source.view_mode == "filmstrip"
    assert window.dockWidgetArea(window.viewer_dock) == Qt.DockWidgetArea.TopDockWidgetArea
    window.run_command("Set METAPANE=On")
    assert not window.meta_dock.isHidden()
    window.run_command('Set STYLE="Vertical doble panel"')
    assert window.dest is not None and not window.tree_dock.isHidden() and window.meta_dock.isHidden()
    assert window.dockWidgetArea(window.viewer_dock) == Qt.DockWidgetArea.RightDockWidgetArea


def test_style_bar_has_each_style(window):
    assert {"Vertical doble panel", "Vertical doble árbol", "Vertical panel-terminal", "Comandante", "Explorador", "Horizontal doble panel", "Horizontal doble árbol",
            "Horizontal panel-terminal", "Imágenes", "Tira de Fotogramas", "Único"} == set(window._style_buttons)
    assert window._style_buttons["Vertical doble panel"].isChecked()


def test_save_custom_style(window, monkeypatch):
    window.run_command("Set DUAL=Off")
    monkeypatch.setattr(window, "_ask_text", lambda *a: "Mío")
    window._save_current_style()
    assert window._style_named("mío")["dual"] == "Off"
    assert "Mío" in window._style_buttons
    window.run_command("Set DUAL=Vertical")
    window.run_command("Set STYLE=Mío")
    assert window.dest is None


def test_content_type_filters_files_not_folders(window, tree):
    left, _ = tree
    (left / "foto.jpg").write_bytes(b"x")
    assert wait_until(lambda: "foto.jpg" in names(window.source))
    window.run_command("Set CONTENTTYPE=images")
    assert names(window.source) == ["subcarpeta", "foto.jpg"]
    assert window.content_box.checked_keys() == ("images",)
    window.run_command("Set CONTENTTYPE=none")
    assert "a.txt" in names(window.source)


def test_location_bar_follows_source(window, tree):
    left, right = tree
    assert window.location._path == window.source.path
    window._view_activated(window.dest)
    assert window.location._path == os.path.normpath(str(right))
    window.location.pathEntered.emit(str(left))
    assert wait_until(lambda: window.source.path == os.path.normpath(str(left)))


def test_theme_switch_keeps_working(window):
    window.config["theme"] = "light"
    window.apply_theme()
    assert theme.current().name == "light"
    window.source.grab()
    window.config["theme"] = "auto"
    window.apply_theme()
    assert theme.current().name in ("dark", "light")
    window.config["theme"] = "dark"
    window.apply_theme()
    assert theme.current().name == "dark"


def test_all_default_toolbar_commands_are_available(window):
    def walk(items):
        for item in items:
            yield from item.get("commands", [])
            yield from walk(item.get("children", []))
    lines = list(walk(window.config["toolbar"]))
    assert lines
    assert [line for line in lines if window.commands_unavailable([line])] == []


def test_all_menu_commands_are_available(window):
    from directory_commander_tux_tux.ui.main_window import MENU_ITEMS
    lines = [item[4] for item in MENU_ITEMS if item[4]]
    assert [line for line in lines if window.commands_unavailable([line])] == []


def test_all_toolbar_icons_exist(window):
    def walk(items):
        for item in items:
            if item.get("icon"):
                yield item["icon"]
            yield from walk(item.get("children", []))
    assert [name for name in walk(window.config["toolbar"]) if not has_icon(name)] == []


# ---- comandos que no se deben ejecutar a medias ----

def test_partial_command_does_nothing(window, tree):
    _, right = tree
    select(window.source, "a.txt")
    window.run_command("Copy SENDMAIL")
    assert window.jobs.running == 0 and not (right / "a.txt").exists()
    assert "No disponible" in window.statusBar().currentMessage()


def test_button_with_keydown_runs_one_variant_only(window, tree):
    left, right = tree
    select(window.source, "a.txt")
    window.run_commands(["@nodeselect", "@keydown:none", "Copy", "@keydown:shift", "Copy MOVE"])
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert (left / "a.txt").exists()  # sin Mayús es copia: el original sigue ahí
    assert len(window._undo) == 1


def test_button_confirm_can_stop_it(window, tree, monkeypatch):
    left, right = tree
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    select(window.source, "a.txt")
    window.run_commands(["@confirm:¿Copiar?", "Copy"])
    assert window.jobs.running == 0 and not (right / "a.txt").exists()


def test_button_with_unknown_modifier_does_not_run(window, tree):
    _, right = tree
    select(window.source, "a.txt")
    window.run_commands(["@if:Set VIEW=Details", "Copy"])
    assert window.jobs.running == 0 and not (right / "a.txt").exists()


def test_web_url_opens_in_browser(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "_open_url", opened.append)
    window.run_command("https://example.test/ayuda")
    assert opened == ["https://example.test/ayuda"]


# ---- comandos añadidos ----

def test_sort_commands(window):
    view = window.source
    window.run_command("Set SORTBY=size SORTREVERSE=on")
    assert view.details.horizontalHeader().sortIndicatorSection() == 1
    assert view.details.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    window.run_command("Set SORTBY=name SORTREVERSE=off")
    assert names(view) == ["subcarpeta", "a.txt", "b.txt"]


def test_navlock_moves_both_panes(window, tree):
    left, right = tree
    (right / "subcarpeta").mkdir()
    window.run_command("Set NAVLOCK=Toggle")
    window.source.navigate(str(left / "subcarpeta"))
    assert window.dest.path == os.path.normpath(str(right / "subcarpeta"))
    window.run_command("Go UP")
    assert window.dest.path == os.path.normpath(str(right))
    window.run_command("Set NAVLOCK=Toggle")
    window.source.navigate(str(left / "subcarpeta"))
    assert window.dest.path == os.path.normpath(str(right))


def test_navlock_ignores_missing_counterpart(window, tree):
    left, right = tree
    window.run_command("Set NAVLOCK=On")
    window.source.navigate(str(left / "subcarpeta"))
    assert window.dest.path == os.path.normpath(str(right))


def test_delete_without_trash_setting_always_confirms(window, tree, monkeypatch):
    left, _ = tree
    window.run_command("Set DELRECYCLEBIN=Toggle")
    window.run_command("Set DELRECYCLECONFIRM=Toggle")
    assert window.config["use_trash"] is False and window.config["confirm_delete"] is False
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    select(window.source, "a.txt")
    window.run_command("Delete")
    assert window.jobs.running == 0 and (left / "a.txt").exists()


def test_set_state_changes_source(window, tree):
    _, right = tree
    window.run_command("Set STATE=Dest")
    assert window.source.path == os.path.normpath(str(right))


def test_checkbox_mode(window):
    view = window.source
    window.run_command("Set CHECKBOXMODE=Toggle")
    select(view, "a.txt", "b.txt")
    window.run_command("Select TOCHECKS")
    window.run_command("Select NONE")
    assert view.selected_paths() == []
    window.run_command("Select FROMCHECKS")
    assert sorted(os.path.basename(p) for p in view.selected_paths()) == ["a.txt", "b.txt"]
    row = view.model.row_of(os.path.join(view.path, "a.txt"))
    index = view.model.index(row, 0)
    assert index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert view.model.setData(index, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    assert view.model.checked == {os.path.join(view.path, "b.txt")}
    view.grab()
    window.run_command("Set CHECKBOXMODE=Toggle")
    assert view.model.checked == set()


def test_darkmode_toggle(window):
    window.run_command("Set DARKMODE=togglesmart")
    assert window.config["theme"] == "light" and theme.current().name == "light"
    window.run_command("Set DARKMODE=togglesmart")
    assert theme.current().name == "dark"


def test_expandable_folders_off_removes_arrows(window):
    view = window.source
    window.run_command("Set EXPANDABLEFOLDERS=Toggle CONFIRM")
    assert wait_until(lambda: view.model.can_expand is False)
    view.model.set_expanded(next(e for e in view.model.entries if e.name == "subcarpeta"), True)
    wait_until(lambda: False, 0.1)
    assert "c.txt" not in names(view)
    window.run_command("Set EXPANDABLEFOLDERS=On")
    assert wait_until(lambda: view.model.can_expand is True)


def test_friendly_dates_in_list(window, tree):
    left, _ = tree
    view = window.source
    window.run_command("Set FRIENDLYDATES=today")
    index = view.model.index(view.model.row_of(str(left / "a.txt")), 3)
    assert index.data().startswith("Hoy ")
    window.run_command("Set FRIENDLYDATES=off")
    assert not index.data().startswith("Hoy ")


def test_grid_lines_and_status_toggle(window):
    window.run_command("Set GRIDLINESH=toggle CONFIRM")
    assert window.source.details.property("grid") == "on"
    window.source.grab()
    window.run_command("Set STATUSBAR=Toggle")
    assert window.source.status.isHidden()
    window.run_command("Set STATUSBAR=Toggle")
    assert not window.source.status.isHidden()


def test_quick_filter_and_show_everything(window):
    window.run_command("Set QUICKFILTER")
    assert not window.source.filter_edit.isHidden()
    window.source.filter_edit.setText("*.txt")
    window.run_command("Set SHOWEVERYTHING=Toggle")
    assert window.source.filter_edit.text() == "" and window.config["show_hidden"] is True


def test_utility_with_toggle(window):
    window.run_command("Set UTILITY=FileLog,Toggle")
    assert not window.utility_dock.isHidden() and window.utility.currentWidget() is window.log
    window.run_command("Set UTILITY=FileLog,Toggle")
    assert window.utility_dock.isHidden()
    window.run_command("Set OUTPUT=Toggle")
    assert not window.utility_dock.isHidden()


def test_copy_update_all_job(window, tree):
    left, right = tree
    (right / "a.txt").write_text("viejo", encoding="utf-8")
    (right / "b.txt").write_text("destino más nuevo", encoding="utf-8")
    os.utime(right / "a.txt", (1000, 1000))
    os.utime(left / "b.txt", (1000, 1000))
    select(window.source, "a.txt", "b.txt")
    window.run_command("Copy UPDATEALL")
    assert wait_until(lambda: window.jobs.running == 0 and (right / "a.txt").read_text(encoding="utf-8") == "A")
    assert (right / "b.txt").read_text(encoding="utf-8") == "destino más nuevo"


def test_copy_duplicate_with_date_template(window, tree):
    left, _ = tree
    select(window.source, "a.txt")
    window.run_command('Copy DUPLICATE PATTERN * AS "* - {date|D#yyyy}" IGNOREEXT')
    expected = left / f"a - {time.strftime('%Y')}.txt"
    assert wait_until(lambda: expected.exists() and window.jobs.running == 0)
    window.run_command("Undo")
    assert wait_until(lambda: window.jobs.running == 0)


def test_copy_archive_single_makes_one_zip_each(window, tree):
    _, right = tree
    select(window.source, "a.txt", "b.txt")
    window.run_command("Copy ARCHIVE=single")
    assert wait_until(lambda: (right / "a.txt.zip").exists() and (right / "b.txt.zip").exists())


def test_create_empty_zip_is_browsable(window, tree):
    left, _ = tree
    window.run_command('CreateFolder ZIP NAME="vacio"')
    assert zipfile.is_zipfile(left / "vacio.zip")
    window.source.navigate(str(left / "vacio.zip"))
    assert wait_until(lambda: window.source.model.path.endswith("vacio.zip"))


def test_select_similar_and_between(window, tree):
    left, right = tree
    (left / "foto.jpg").write_bytes(b"x")
    (right / "a.jpg").write_bytes(b"x")
    assert wait_until(lambda: "foto.jpg" in names(window.source) and "a.jpg" in names(window.dest))
    view = window.source
    select(view, "a.txt")
    window.run_command("Select SIMILAR")
    assert sorted(os.path.basename(p) for p in view.selected_paths()) == ["a.txt", "b.txt"]
    window.run_command("Select NONE")
    window.run_command("Select SOURCETODEST=in,noext")
    assert [os.path.basename(p) for p in view.selected_paths()] == ["a.txt"]
    select(view, "a.txt", "b.txt")
    window.run_command("Select SOURCETODEST=notin,noext DESELECTNOMATCH")
    assert [os.path.basename(p) for p in view.selected_paths()] == ["b.txt"]
    window.run_command("Select DESTTOSOURCE=notin")
    assert [os.path.basename(p) for p in window.dest.selected_paths()] == ["a.jpg"]


def test_recent_folders_and_clearing_them(window, tree):
    left, _ = tree
    for _ in range(3):
        window.source.navigate(str(left / "subcarpeta"))
        window.source.navigate(str(left))
    recent = window.config["recent"]
    assert recent[0]["path"] == os.path.normpath(str(left))
    assert [item["path"] for item in recent].count(os.path.normpath(str(left))) == 1
    window.run_command("Recent CLEAR")
    assert window.config["recent"] == []


def test_favorites_bar_toggle_and_content(window):
    window.run_command("Favorites ADD")
    window.run_command('Toolbar NAME="Favorites Bar" TOGGLE')
    assert not window.favorites_bar.isHidden() and window.config["show_favorites_bar"] is True
    window.config["favorites"] = [{"label": "Barra de favoritos", "bar": True, "children": window.config["favorites"]}]
    window._rebuild_favorites_bar()
    assert [a.toolTip() for a in window.favorites_bar.actions()] == [window.source.path]
    window.run_command('Toolbar NAME="Favorites Bar" TOGGLE')
    assert window.favorites_bar.isHidden()


def test_layout_save_and_load(window, tree):
    left, right = tree
    window.groups[0].add_tab(str(left / "subcarpeta"))
    window.run_command('Set STYLE="Horizontal doble panel"')
    window.save_layout("trabajo")
    window.run_command('Set STYLE="Único"')
    window.groups[0].close_tab(window.groups[0].count() - 1)
    window.load_layout("trabajo")
    assert window.dual == "Horizontal"
    assert [v.path for v in window.groups[0].views()] == [os.path.normpath(str(left)),
                                                          os.path.normpath(str(left / "subcarpeta"))]


def test_layout_with_missing_folder_keeps_current_tabs(window, tree):
    left, _ = tree
    window.config["layouts"] = {"viejo": {"tabs": [[str(left / "borrada")], []], "dual": "Vertical", "style": ""}}
    window.load_layout("viejo")
    assert window.groups[0].count() == 1 and window.source.path == os.path.normpath(str(left))
    with pytest.raises(ValueError):
        window.load_layout("no existe")


def test_tab_groups(window, tree):
    left, right = tree
    window.save_tab_group("g")
    window.source.navigate(str(left / "subcarpeta"))
    window.load_tab_group("g")
    assert window.groups[0].current.path in (os.path.normpath(str(left)), os.path.normpath(str(left / "subcarpeta")))
    assert [v.path for v in window.groups[0].views()] == [os.path.normpath(str(left))]


def test_go_newtab_from_selection(window, tree):
    left, _ = tree
    select(window.source, "subcarpeta", "a.txt")
    window.run_command("Go NEWTAB=nofocus,findexisting FROMSEL")
    assert window.groups[0].count() == 2
    assert window.groups[0].widget(1).path == os.path.normpath(str(left / "subcarpeta"))
    assert window.source.path == os.path.normpath(str(left))


def test_print_folder_to_clipboard(window):
    window.run_command("Print FOLDER AS=txt TO=clip FLATVIEW=no QUIET")
    text = QApplication.clipboard().text()
    assert text.startswith("Nombre\t") and "a.txt" in text and "subcarpeta" in text


def test_redo_rename_and_move(window, tree):
    left, right = tree
    row = window.source.model.row_of(str(left / "a.txt"))
    window.source.model.setData(window.source.model.index(row, 0), "z.txt")
    window.run_command("Undo")
    assert (left / "a.txt").exists()
    window.run_command("Undo REDO")
    assert (left / "z.txt").exists() and not (left / "a.txt").exists()
    window.run_command("Undo")
    assert wait_until(lambda: "a.txt" in names(window.source))
    select(window.source, "a.txt")
    window.run_command("Copy MOVE")
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    window.run_command("Undo")
    assert wait_until(lambda: (left / "a.txt").exists() and window.jobs.running == 0)
    window.run_command("Undo REDO")
    assert wait_until(lambda: (right / "a.txt").exists() and not (left / "a.txt").exists() and window.jobs.running == 0)


def test_new_action_clears_redo(window, tree):
    left, _ = tree
    window.run_command('CreateFolder NAME="uno"')
    row = window.source.model.row_of(str(left / "a.txt"))
    window.source.model.setData(window.source.model.index(row, 0), "z.txt")
    window.run_command("Undo")
    assert window._redo
    window.run_command('CreateFolder NAME="dos"')
    assert window._redo == []


def test_split_and_join_commands(window, tree, monkeypatch):
    left, right = tree
    (left / "grande.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    assert wait_until(lambda: "grande.bin" in names(window.source))
    monkeypatch.setattr(window, "_ask_part_size", lambda: 2 * 1024 * 1024)
    select(window.source, "grande.bin")
    window.run_command("Split")
    assert wait_until(lambda: (right / "grande.bin.002").exists() and window.jobs.running == 0)
    assert wait_until(lambda: "grande.bin.001" in names(window.dest))
    window._view_activated(window.dest)
    select(window.source, "grande.bin.001")
    window.run_command("Join")
    assert wait_until(lambda: (left / "grande.bin (2)").exists() or (left / "grande (2).bin").exists()
                      or (left / "grande.bin").stat().st_size == 3 * 1024 * 1024 and window.jobs.running == 0)


def _png(path, width=40, height=20, color=0x336699):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    image.save(str(path))


def test_image_convert_to_dest_keeps_original(window, tree):
    left, right = tree
    _png(left / "foto.png")
    assert wait_until(lambda: "foto.png" in names(window.source))
    select(window.source, "foto.png")
    window.run_command("Image CONVERT=jpg")
    assert wait_until(lambda: (right / "foto.jpg").exists() and window.jobs.running == 0)
    assert (left / "foto.png").exists()
    assert not QImage(str(right / "foto.jpg")).isNull()
    window.run_command("Undo")
    assert wait_until(lambda: window.jobs.running == 0)


def test_image_rotate_replace_asks_and_preserves_date(window, tree, monkeypatch):
    left, _ = tree
    _png(left / "foto.png")
    os.utime(left / "foto.png", (500000, 500000))
    assert wait_until(lambda: "foto.png" in names(window.source))
    select(window.source, "foto.png")
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    window.run_command("Image ROTATE=90 HERE REPLACE PRESERVEDATE")
    assert window.jobs.running == 0 and QImage(str(left / "foto.png")).width() == 40
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    window.run_command("Image ROTATE=90 HERE REPLACE PRESERVEDATE")
    assert wait_until(lambda: QImage(str(left / "foto.png")).width() == 20 and window.jobs.running == 0)
    assert int(os.path.getmtime(left / "foto.png")) == 500000


def test_image_resize_with_suffix_never_overwrites(window, tree):
    left, _ = tree
    _png(left / "foto.png", 400, 200)
    assert wait_until(lambda: "foto.png" in names(window.source))
    select(window.source, "foto.png")
    window.run_command("Image CONVERT=png HERE ADDSUFFIX HEIGHT=100 WIDTH=100 PRESERVEASPECTRATIO")
    target = left / "foto_100x50.png"
    assert wait_until(lambda: target.exists() and window.jobs.running == 0)
    assert QImage(str(target)).width() == 100 and QImage(str(left / "foto.png")).width() == 400


def test_image_ops_report_errors_and_continue(app, tmp_path):
    bad = tmp_path / "rota.png"
    bad.write_bytes(b"no es una imagen")
    good = tmp_path / "buena.png"
    _png(good)
    result = image_ops.process_images([str(bad), str(good)], str(tmp_path), "jpg")
    assert len(result.errors) == 1 and result.done == 1
    assert (tmp_path / "buena.jpg").exists() and bad.exists()


def test_image_transparency_becomes_white_in_jpeg(app, tmp_path):
    src = tmp_path / "t.png"
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(0x00000000)
    image.save(str(src))
    image_ops.process_images([str(src)], str(tmp_path), "jpg")
    assert QImage(str(tmp_path / "t.jpg")).pixelColor(5, 5).lightness() > 240


def test_image_unwritable_format_is_rejected(app):
    with pytest.raises(ValueError):
        image_ops.normalize_format("formato-inventado")


def test_favorites_export_and_import_through_window(window, tmp_path, tree):
    left, _ = tree
    path = tmp_path / "favoritos.json"
    window.config["favorites"] = [{"label": "Izq", "path": str(left)}]
    assert window.export_favorites_file(str(path)) == 1
    window.config["favorites"] = []
    window._rebuild_favorites_bar()
    assert window.import_favorites_file(str(path)) == (1, 0)
    assert window.config["favorites"] == [{"label": "Izq", "path": str(left)}]
    assert window.tree.favorites_item.childCount() == 1
    assert window.import_favorites_file(str(path)) == (0, 0)  # importar dos veces no duplica
    assert len(window.config["favorites"]) == 1


def test_favorites_commands_from_menu_use_dialogs(window, tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    monkeypatch.setattr("directory_commander_tux_tux.ui.extra_commands.QFileDialog.getSaveFileName", lambda *a, **k: (str(path), ""))
    window.config["favorites"] = [{"label": "a", "path": "/a"}]
    window.run_command("Favorites EXPORT")
    assert path.exists()
    window.config["favorites"] = []
    shown = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.extra_commands.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr("directory_commander_tux_tux.ui.extra_commands.QMessageBox.information", lambda *a: shown.append(a[2]))
    window.run_command("Favorites IMPORT")
    assert window.config["favorites"] == [{"label": "a", "path": "/a"}] and "1 favoritos" in shown[0]


def test_invalid_favorites_file_shows_error_and_changes_nothing(window, tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text("no es json", encoding="utf-8")
    errors = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.extra_commands.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: errors.append(a[2]))
    window.config["favorites"] = [{"label": "a", "path": "/a"}]
    window.run_command("Favorites IMPORT")
    assert errors and window.config["favorites"] == [{"label": "a", "path": "/a"}]


def test_favorites_menu_offers_import_and_export(window):
    window._fill_favorites()
    assert {"Importar favoritos…", "Exportar favoritos…"} <= set(labels(window.favorites_menu))


def test_settings_export_and_import_apply_at_once(window, tmp_path):
    path = tmp_path / "configuracion.json"
    window.config["confirm_delete"] = False
    window.export_settings_file(str(path))
    window.config["confirm_delete"] = True
    assert window.import_settings_file(str(path)) == []
    assert window.config["confirm_delete"] is False
    path.write_text('{"format": "directory-commander-tux-tux-settings", "version": 1, "settings": {"theme": "light", '
                    '"show_hidden": true, "full_row_select": true, "friendly_dates": "normal", "lock_bars": true, '
                    '"hover_info": false, "show_favorites_bar": true, "style": "Único", '
                    '"favorites": [{"label": "F", "path": "/f"}], "toolbar": [{"label": "Solo", "commands": ["Go UP"]}]}}',
                    encoding="utf-8")
    assert window.import_settings_file(str(path)) == []
    view = window.source
    assert theme.current().name == "light" and window.config["show_hidden"] is True
    assert view.details.full_row and view.model.friendly_dates == "normal" and not view.hover_enabled
    assert not window.toolbar.isMovable() and not window.favorites_bar.isHidden()
    assert window.dest is None and [a.text() for a in window.toolbar.actions()] == ["Solo"]
    assert window.tree.favorites_item.child(0).text(0) == "F"
    window.config["theme"] = "dark"
    window.apply_theme()


def test_import_settings_saved_to_disk(window, tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"format": "directory-commander-tux-tux-settings", "version": 1, "settings": {"dest_tint": false}}', encoding="utf-8")
    window.import_settings_file(str(path))
    assert Config(window.config.directory)["dest_tint"] is False
    window.config["dest_tint"] = True
    window.apply_theme()


def test_settings_with_unknown_style_falls_back(window, tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"format": "directory-commander-tux-tux-settings", "version": 1, "settings": {"style": "No existe"}}', encoding="utf-8")
    window.import_settings_file(str(path))
    assert window._style_named(window.config["style"]) is not None


def test_import_settings_keeps_open_tabs_and_ffmpeg(window, tmp_path, tree):
    left, _ = tree
    window.config["ffmpeg_dir"] = "/mi/ffmpeg"
    path = tmp_path / "c.json"
    path.write_text('{"format": "directory-commander-tux-tux-settings", "version": 1, "settings": {"ffmpeg_dir": "/malicioso", "dest_tint": true}}', encoding="utf-8")
    ignored = window.import_settings_file(str(path))
    assert ignored == ["ffmpeg_dir"] and window.config["ffmpeg_dir"] == "/mi/ffmpeg"
    assert window.source.path == os.path.normpath(str(left))


def test_import_settings_asks_first(window, tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text('{"format": "directory-commander-tux-tux-settings", "version": 1, "settings": {"dest_tint": false}}', encoding="utf-8")
    monkeypatch.setattr("directory_commander_tux_tux.ui.extra_commands.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    window.run_command("Prefs IMPORT")
    assert window.config["dest_tint"] is True


def test_toolbar_disables_unavailable_buttons(window):
    window.config["toolbar"] = [
        {"label": "Subir", "icon": "#goup", "display": "both", "commands": ["Go UP"]},
        {"label": "Actualizar todo", "icon": "#copy", "display": "both", "commands": ["Copy SENDMAIL"]},
        {"label": "Espacio", "display": "label", "commands": ["Set SPACER"]},
        {"label": "Menú", "display": "both", "commands": [], "children": [{"label": "Hijo", "commands": ["Go ROOT"]}]},
    ]
    window._build_toolbar()
    by_label = {a.text(): a for a in window.toolbar.actions() if a.text()}
    assert by_label["Subir"].isEnabled() and not by_label["Actualizar todo"].isEnabled()
    assert by_label["Menú"].isEnabled()
    window._reset_toolbar()
    assert any(a.text() == "Deshacer" for a in window.toolbar.actions())


# ---- deshacer ----

def test_undo_inline_rename(window, tree):
    left, _ = tree
    row = window.source.model.row_of(str(left / "a.txt"))
    window.source.model.setData(window.source.model.index(row, 0), "nuevo.txt")
    assert (left / "nuevo.txt").exists()
    window.run_command("Undo")
    assert (left / "a.txt").exists() and not (left / "nuevo.txt").exists()


def test_undo_move_restores_original(window, tree):
    left, right = tree
    select(window.source, "a.txt")
    window.run_command("Copy MOVE")
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    window.run_command("Undo")
    assert wait_until(lambda: (left / "a.txt").exists() and not (right / "a.txt").exists() and window.jobs.running == 0)


def test_undo_never_touches_replaced_files(window, tree, monkeypatch):
    left, right = tree
    (right / "a.txt").write_text("viejo", encoding="utf-8")
    monkeypatch.setattr(window, "_ask_collision", lambda *_: fsops.Answer(fsops.Action.REPLACE))
    select(window.source, "a.txt")
    window.run_command("Copy")
    assert wait_until(lambda: (right / "a.txt").read_text(encoding="utf-8") == "A" and window.jobs.running == 0)
    window.run_command("Undo")
    assert window.jobs.running == 0
    assert (right / "a.txt").read_text(encoding="utf-8") == "A"


def test_undo_with_empty_stack(window):
    window.run_command("Undo")
    assert "nada" in window.statusBar().currentMessage()


# ---- comandos nuevos ----

def test_copy_as_uses_new_name(window, tree, monkeypatch):
    _, right = tree
    monkeypatch.setattr(window, "_ask_text", lambda *a: "renombrado.txt")
    select(window.source, "a.txt")
    window.run_command("Copy AS")
    assert wait_until(lambda: (right / "renombrado.txt").exists())
    assert not (right / "a.txt").exists()


def test_move_here_into_new_folder(window, tree):
    left, _ = tree
    select(window.source, "a.txt", "b.txt")
    window.run_command('Copy MOVE HERE CREATEFOLDER="lote"')
    assert wait_until(lambda: (left / "lote" / "a.txt").exists() and (left / "lote" / "b.txt").exists())
    assert not (left / "a.txt").exists()


def test_create_folder_with_date_code(window, tree):
    left, _ = tree
    window.run_command('CreateFolder NAME="{date|yyyy}"')
    assert (left / time.strftime("%Y")).is_dir()


def test_filetype_new_does_not_overwrite(window, tree):
    left, _ = tree
    window.run_command("FileType NEW=.txt")
    window.run_command("FileType NEW=.txt")
    assert (left / "Nuevo archivo.txt").exists() and (left / "Nuevo archivo (2).txt").exists()


def test_setattr_modified_now(window, tree):
    left, _ = tree
    os.utime(left / "a.txt", (1000, 1000))
    select(window.source, "a.txt")
    window.run_command("SetAttr MODIFIED=now")
    assert os.path.getmtime(left / "a.txt") > 1000000


def test_reselect(window):
    select(window.source, "a.txt")
    window.run_command("Select NONE")
    assert window.source.selected_paths() == []
    window.run_command("Select RESELECT")
    assert [os.path.basename(p) for p in window.source.selected_paths()] == ["a.txt"]


def test_go_up_newtab(window, tree):
    left, _ = tree
    window.source.navigate(str(left / "subcarpeta"))
    window.run_command("Go UP NEWTAB")
    assert window.groups[0].count() == 2
    assert window.source.path == os.path.normpath(str(left))


def test_favorites_add(window):
    window.run_command("Favorites ADD")
    window.run_command("Favorites ADD")
    assert [f["path"] for f in window.config["favorites"]] == [window.source.path]


def test_image_viewer_navigation(window, tree):
    left, _ = tree
    for name in ("uno.png", "dos.png"):
        image = QImage(300, 200, QImage.Format.Format_RGB32)
        image.fill(0x336699)
        image.save(str(left / name))
    assert wait_until(lambda: "uno.png" in names(window.source))
    select(window.source, "uno.png")
    window.run_command("Show")
    viewer = window._viewers[-1]
    assert wait_until(lambda: viewer.label.pixmap() is not None and not viewer.label.pixmap().isNull())
    assert viewer.paths[viewer.index].endswith("uno.png")
    QTest.keyClick(viewer, Qt.Key.Key_Right)
    assert viewer.paths[viewer.index].endswith("dos.png")
    QTest.keyClick(viewer, Qt.Key.Key_Right)
    assert viewer.paths[viewer.index].endswith("uno.png")
    QTest.keyClick(viewer, Qt.Key.Key_Plus)
    assert viewer.zoom is not None
    QTest.keyClick(viewer, Qt.Key.Key_0)
    assert viewer.zoom is None
    viewer.close()


def test_image_viewer_bad_file(window, tree):
    left, _ = tree
    (left / "roto.png").write_bytes(b"no es una imagen")
    assert wait_until(lambda: "roto.png" in names(window.source))
    select(window.source, "roto.png")
    window.run_command("Show")
    viewer = window._viewers[-1]
    assert wait_until(lambda: "No se pudo abrir" in viewer.label.text())
    viewer.close()


def test_show_without_images(window):
    window.run_command("Show")
    assert "imágenes" in window.statusBar().currentMessage()


def test_metadata_panel(window, tree):
    window.run_command("Set METAPANE=On")
    select(window.source, "a.txt")
    def rows():
        return dict(row for _group, group_rows in window.metadata.groups() for row in group_rows)

    assert wait_until(lambda: rows().get("Nombre") == "a.txt")
    assert rows()["Tipo"] == "Archivo TXT"


def test_find_result_opens_and_selects_inside_folder(window, tree):
    left, _ = tree
    target = left / "subcarpeta" / "c.txt"
    window._navigate_focus(str(left / "subcarpeta"), str(target))
    assert wait_until(lambda: [os.path.basename(p) for p in window.source.selected_paths()] == ["c.txt"])


def test_select_paths_accepts_unnormalized(window, tree):
    left, _ = tree
    window.source.select_paths([str(left).replace("\\", "/") + "/a.txt"])
    assert [os.path.basename(p) for p in window.source.selected_paths()] == ["a.txt"]


def test_tree_follows_source_at_startup(window, tree):
    left, _ = tree
    window.run_command("Set HIDDEN=On")  # en Windows, el directorio temporal cuelga de AppData, que está oculto
    window.source.navigate(str(left / "subcarpeta"))
    target = os.path.normcase(os.path.normpath(str(left / "subcarpeta")))
    assert wait_until(lambda: os.path.normcase(os.path.normpath(window.tree.current_path() or ".")) == target, 15)


def test_breadcrumb_handles_root_and_repeated_names(app):
    from directory_commander_tux_tux.ui.file_view import Breadcrumb
    crumb = Breadcrumb()
    crumb.set_path(os.path.abspath(os.path.join(os.sep, "a", "b", "a")))
    labels = [b.text() for b in crumb._buttons]
    assert labels[-1] == "a" and labels[-3] == "a ›"
    assert all(label.strip(" ›") for label in labels)


def test_help_menu_is_last(window):
    assert window.menuBar().actions()[-1].text() == "Ay&uda"


def test_move_to_trash_from_worker_thread(tmp_path, app):
    """La papelera real funciona fuera del hilo principal (cada plataforma tiene su backend).

    Deja un archivo en la papelera del usuario, así que solo corre si se pide con DETUX_REAL_TRASH=1.
    """
    if not os.environ.get("DETUX_REAL_TRASH"):
        pytest.skip("usa la papelera real; actívala con DETUX_REAL_TRASH=1")
    target = tmp_path / "detux-prueba-papelera.txt"
    target.write_text("x", encoding="utf-8")
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(move_to_trash(str(target))))
    thread.start()
    thread.join(30)
    assert outcome == [True]
    assert not target.exists()


# ---- barra de pestañas y reproducción en el visor ----

def settle():
    for _ in range(5):
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)


def open_folders(group, parent, count):
    for number in range(count):
        folder = parent / f"carpeta numero {number}"
        folder.mkdir()
        group.add_tab(str(folder))
    settle()


def write_wav(path, seconds=2):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 8000 * seconds)
    return path


# ---- pestañas ----

def test_tabs_keep_their_full_name_when_they_do_not_fit(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    bar = group.tabBar()
    for index in range(group.count()):
        needed = bar.fontMetrics().horizontalAdvance(group.tabText(index))
        assert bar.rect_of(index).width() > needed
    assert group.overflow_button.isVisible()
    assert group.hidden_tabs()


def test_native_scroll_buttons_are_not_shown(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    arrows = [b for b in group.tabBar().findChildren(QToolButton) if b.objectName().startswith("Scroll")]
    assert arrows and all(b.width() == 0 for b in arrows)


def test_overflow_menu_lists_hidden_tabs_and_opens_them(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    group.setCurrentIndex(0)
    settle()
    hidden = group.hidden_tabs()
    assert hidden and 0 not in hidden
    menu = group.overflow_menu()
    actions = menu.actions()
    assert [a.text() for a in actions[:len(hidden)]] == [group.tabText(i) for i in hidden]
    assert actions[-1].text() == "Abrir nueva pestaña" and actions[-2].isSeparator()
    target = hidden[-1]
    actions[len(hidden) - 1].trigger()
    settle()
    assert group.currentIndex() == target and target not in group.hidden_tabs()
    assert group.tabBar().rect().intersected(group.tabBar().rect_of(target)).width() >= group.tabBar().rect_of(target).width() - 2


def test_overflow_menu_opens_new_tab(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    count = group.count()
    group.overflow_menu().actions()[-1].trigger()
    assert group.count() == count + 1


def test_overflow_button_does_not_cover_tabs(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    settle()
    assert group.tabBar().geometry().right() < group.overflow_button.geometry().left()


def test_add_button_stays_visible_with_many_tabs(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    add, overflow = group.add_button, group.overflow_button
    assert add.isVisible() and overflow.isVisible()
    assert add.geometry().right() <= group.width()
    assert overflow.mapTo(group, QPoint(overflow.width(), 0)).x() <= add.x()
    assert add.y() < group.tabBar().height()
    count = group.count()
    QTest.mouseClick(add, Qt.MouseButton.LeftButton)
    assert group.count() == count + 1 and add.isVisible()


def test_add_button_returns_when_tabs_fit_again(window, tmp_path):
    set_tabs(window, multi_row=False)
    group = window.groups[0]
    start = group.count()
    open_folders(group, tmp_path, 12)
    while group.count() > start:
        group.close_tab(group.count() - 1)
    settle()
    assert not group.overflow_button.isVisible() and group.add_button.isVisible() and not group.hidden_tabs()
    assert group.add_button.x() > group.tabBar().rect_of(group.count() - 1).right()


def test_many_tabs_do_not_force_a_wider_window(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    assert group.tabBar().minimumSizeHint().width() < 200


def test_close_button_on_every_tab_by_default(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    bar = group.tabBar()
    side = bar.ButtonPosition.RightSide
    settle()
    assert all(bar.tabButton(i, side) is not None and not bar.tabButton(i, side).isHidden() for i in range(group.count()))
    group.setCurrentIndex(0)
    view = group.widget(2)
    bar.tabButton(2, side).click()  # cierra esa pestaña, aunque no sea la activa
    assert group.indexOf(view) < 0 and group.currentIndex() == 0


def test_close_button_only_on_current_tab(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    set_tabs(window, close_active_only=True)
    bar = group.tabBar()
    side = bar.ButtonPosition.RightSide
    group.setCurrentIndex(0)
    settle()
    assert bar.tabButton(0, side) is not None and bar.tabButton(1, side) is None
    group.setCurrentIndex(1)
    settle()
    assert bar.tabButton(0, side) is None and bar.tabButton(1, side) is not None


def test_tab_width_is_the_name_plus_icon(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    set_tabs(window, close_active_only=True)
    bar = group.tabBar()
    group.setCurrentIndex(0)
    settle()
    text = bar.fontMetrics().horizontalAdvance(group.tabText(1))
    assert bar.iconSize().width() + text < bar.rect_of(1).width() <= bar.iconSize().width() + text + 30
    active = bar.rect_of(0).width() - bar.fontMetrics().horizontalAdvance(group.tabText(0))
    assert active - (bar.rect_of(1).width() - text) >= bar.tabButton(0, bar.ButtonPosition.RightSide).width()


def test_single_tab_has_no_close_button(app, tmp_path):
    win = MainWindow(Config(str(tmp_path / "cfg")), [str(tmp_path), str(tmp_path)])
    win.show()
    settle()
    group = win.groups[0]
    assert group.count() == 1
    button = group.tabBar().tabButton(0, group.tabBar().ButtonPosition.RightSide)
    assert not button.isHidden() and not button.isEnabled()  # se ve, pero no cierra la última pestaña
    group.add_tab(str(tmp_path))
    settle()
    assert button.isEnabled()
    win.close()


def test_long_folder_name_is_shortened_only_in_the_title():
    name = "una carpeta con un nombre extraordinariamente largo de verdad"
    title = _tab_title(os.path.join(os.sep, "datos", name))
    assert "…" in title and title.endswith(name[-10:]) and len(os.path.basename(title)) <= MAX_TAB_NAME + 6
    assert _tab_title(os.path.join(os.sep, "datos", "Fotos")).endswith("Fotos")


# ---- reproducción ----

@pytest.fixture
def media_window(window, tmp_path):
    pytest.importorskip("PySide6.QtMultimedia")
    window.config["viewer_muted"] = True  # las pruebas no deben sonar
    window.run_command("Set VIEWPANE=On")
    yield window
    window.preview.dispose()


def loaded(pane):
    return pane.media is not None and pane.media.has_source and pane.media.player.duration() > 0


def test_selecting_audio_opens_the_player(media_window):
    win = media_window
    path = write_wav(os.path.join(win.source.path, "tono.wav"))
    win.preview.show_entry(entry_from_path(str(path)))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media
    assert win.preview.stack.currentWidget() is media
    assert media.player.hasAudio() and not media.player.hasVideo()
    assert media.pages.currentWidget() is media.audio_page
    assert media.seek.maximum() == media.player.duration()
    assert media.seek.elapsed_text() == "00:00" and media.seek.remaining_text().endswith("/ 00:02") and media.audio.isMuted()


def test_same_entry_does_not_restart(media_window, monkeypatch):
    win = media_window
    entry = entry_from_path(str(write_wav(os.path.join(win.source.path, "tono.wav"))))
    win.preview.show_entry(entry)
    assert wait_until(lambda: loaded(win.preview))
    monkeypatch.setattr(win.preview.media, "load", lambda path: pytest.fail("no debe volver a cargarse"))
    win.preview.show_entry(entry)
    settle()
    assert win.preview.media.has_source


def test_selecting_another_file_closes_the_media(media_window, tree):
    win = media_window
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, "tono.wav")))))
    assert wait_until(lambda: loaded(win.preview))
    select(win.source, "a.txt")
    assert wait_until(lambda: win.preview.stack.currentWidget() is win.preview.text)
    assert not win.preview.media.has_source


def test_browsing_quickly_opens_only_the_last_file(media_window):
    win = media_window
    first = entry_from_path(str(write_wav(os.path.join(win.source.path, "uno.wav"))))
    last = entry_from_path(str(write_wav(os.path.join(win.source.path, "dos.wav"))))
    win.preview.show_entry(first)
    win.preview.show_entry(last)
    assert wait_until(lambda: loaded(win.preview))
    assert win.preview.media.path == last.path
    assert win.preview.media.player.source().toLocalFile().endswith("dos.wav")


def test_release_frees_the_file_at_once(media_window):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    win.preview.release_media()
    os.rename(path, path + ".2")  # en Windows fallaría si siguiera abierto
    os.rename(path + ".2", path)
    os.remove(path)


def test_release_before_opening_cancels_it(media_window):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    win.preview.release_media()
    os.remove(path)
    settle()
    assert not win.preview._media_timer.isActive()
    assert win.preview.media is None or not win.preview.media.has_source


def test_play_button_reopens_a_released_file(media_window):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media
    media.player.pause()
    media.player.setPosition(1000)
    assert wait_until(lambda: media.player.position() >= 900)
    win.preview.release_media()
    assert not media.has_source
    media.toggle_play()
    assert wait_until(lambda: media.has_source and media.player.position() >= 900)


def test_inline_rename_releases_the_file(media_window, tree):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.run_command("Go REFRESH")
    model = win.source.model
    assert wait_until(lambda: any(model.entry(r) and model.entry(r).name == "tono.wav" for r in range(model.rowCount())))
    row = next(r for r in range(model.rowCount()) if model.entry(r) and model.entry(r).name == "tono.wav")
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    assert model.setData(model.index(row, 0), "nuevo.wav")
    assert os.path.exists(os.path.join(win.source.path, "nuevo.wav"))


def test_starting_a_job_releases_the_file(media_window):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    win.start_transfer([path], win.dest.path if win.dest else win.groups[1].current.path, True)
    assert wait_until(lambda: not os.path.exists(path))
    assert not win.preview.media.has_source


def test_hiding_the_viewer_stops_and_showing_it_reopens(media_window, tree):
    win = media_window
    write_wav(os.path.join(win.source.path, "tono.wav"))
    win.run_command("Go REFRESH")
    model = win.source.model
    assert wait_until(lambda: any(model.entry(r) and model.entry(r).name == "tono.wav" for r in range(model.rowCount())))
    select(win.source, "tono.wav")
    assert wait_until(lambda: loaded(win.preview))
    win.run_command("Set VIEWPANE=Off")
    settle()
    assert not win.preview.media.has_source
    win.run_command("Set VIEWPANE=On")
    assert wait_until(lambda: loaded(win.preview))


def test_damaged_file_shows_a_message(media_window):
    win = media_window
    path = os.path.join(win.source.path, "roto.mp4")
    with open(path, "wb") as out:
        out.write(b"esto no es un video" * 100)
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: win.preview.media is not None and win.preview.stack.currentWidget() is win.preview.info
                      and "No se pudo reproducir" in win.preview.info.text())
    assert not win.preview.media.has_source
    os.remove(path)  # no queda bloqueado


def test_autoplay_can_be_turned_off_and_is_remembered(media_window):
    win = media_window
    win.config["viewer_autoplay"] = False
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media
    assert media.player.playbackState() != media.player.PlaybackState.PlayingState
    assert not media.auto_button.isChecked()
    media.auto_button.click()
    assert win.config["viewer_autoplay"] is True


def test_volume_and_mute_are_remembered(media_window):
    win = media_window
    path = str(write_wav(os.path.join(win.source.path, "tono.wav")))
    win.preview.show_entry(entry_from_path(path))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media
    media.volume.setValue(50)
    assert win.config["viewer_volume"] == 50 and media.audio.volume() == pytest.approx(0.25)
    media.mute_button.click()
    assert win.config["viewer_muted"] is False and not media.audio.isMuted()
    media.mute_button.click()
    assert win.config["viewer_muted"] is True and media.audio.isMuted()


def test_preview_works_without_a_configuration(app):
    pane = PreviewPane()
    pane.show_entry(None)
    pane.release_media()
    pane.apply_icons()
    pane.dispose()


def test_click_on_seek_bar_jumps_there(media_window):
    win = media_window
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, "tono.wav")))))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media
    media.player.pause()
    seek = media.seek
    point = QPointF(seek.width() / 2, seek.height() / 2)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, point, seek.mapToGlobal(point), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(seek, event)
    assert wait_until(lambda: abs(media.player.position() - media.player.duration() / 2) < 400)


def test_player_settings_travel_in_exported_configuration(tmp_path):
    path = tmp_path / "c.json"
    settings_io.export_settings(str(path), {"viewer_autoplay": False, "viewer_volume": 30, "viewer_muted": True})
    settings, ignored = settings_io.read_settings(str(path))
    assert settings == {"viewer_autoplay": False, "viewer_volume": 30, "viewer_muted": True} and not ignored
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"viewer_volume": "alto"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({}, ["viewer_volume"])


def test_format_position():
    pytest.importorskip("PySide6.QtMultimedia")
    from directory_commander_tux_tux.ui.media_player import format_position

    assert format_position(0) == "00:00" and format_position(65_000) == "01:05" and format_position(3_661_000) == "1:01:01"


# ---- el contenido no fija el tamaño del visor ----

LONG_NAME = "nombre_de_archivo_muy_largo_sin_espacios_para_comprobar_el_ancho_minimo_del_visor_2023"


@pytest.mark.parametrize("extension", ["txt", "png", "zzz"])
def test_long_file_name_does_not_widen_the_viewer(app, tmp_path, extension):
    path = tmp_path / f"{LONG_NAME}.{extension}"
    path.write_bytes(b"x")
    pane = PreviewPane()
    pane.resize(300, 400)
    pane.show()
    before = pane.minimumSizeHint().width()
    pane.show_entry(entry_from_path(str(path)))
    settle()
    assert pane.minimumSizeHint().width() == before < 120
    assert pane.title.heightForWidth(120) > pane.title.heightForWidth(600)  # se parte en varias líneas


def test_short_file_name_is_left_alone(app, tmp_path):
    path = tmp_path / "corto.txt"
    path.write_bytes(b"x")
    pane = PreviewPane()
    pane.show_entry(entry_from_path(str(path)))
    assert "corto.txt" in pane.title.text()


def test_media_does_not_set_a_minimum_size_for_the_viewer(media_window):
    win = media_window
    before = win.preview.minimumSizeHint().width()
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, LONG_NAME + ".wav")))))
    assert wait_until(lambda: loaded(win.preview))
    assert win.preview.minimumSizeHint().width() == before
    assert win.viewer_dock.minimumSizeHint().width() < 160


def test_controls_hide_by_priority_when_narrow(media_window):
    win = media_window
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, "tono.wav")))))
    assert wait_until(lambda: loaded(win.preview))
    media = win.preview.media

    def shown(width):
        media.controls.resize(width, 30)
        media._fit_controls()
        return [not w.isHidden() for w in controls]

    controls = (media.play_button, media.stop_button, media.repeat_button, media.mute_button, media.auto_button, media.volume)
    assert shown(600) == [True] * 6
    spacing = media.controls.layout().spacing()
    exact = SEEK_MIN_WIDTH + sum(min(w.sizeHint().width(), w.maximumWidth()) for w in controls) + 6 * spacing
    assert shown(exact) == [True] * 6  # justo el ancho que necesitan: no se oculta ninguno
    assert shown(exact - 1) == [True, True, True, True, True, False]
    assert shown(60) == [True, False, False, False, False, False]
    states = [shown(width) for width in range(60, 400, 20)]
    assert all(sum(a) <= sum(b) for a, b in zip(states, states[1:]))  # con más sitio nunca se oculta nada


# ---- selección por nombre en lista e iconos ----

def list_view(window, tree, mode="list"):
    left, _ = tree
    (left / ("nombre_largo_para_ensanchar_la_columna" * 2 + ".txt")).write_text("x", encoding="utf-8")
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: view.model.rowCount() == 4)
    view.set_view_mode(mode)
    settle()
    return view, view.thumbs


def row_of(view, name):
    return next(r for r in range(view.model.rowCount()) if view.model.entry(r) and view.model.entry(r).name == name)


def blank_point(items, index):
    rect, name = items.visualRect(index), items.name_rect(index)
    assert name.right() + 10 < rect.right()
    return QPoint(name.right() + 8, rect.center().y())


def test_list_click_outside_name_selects_nothing(window, tree):
    view, items = list_view(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=blank_point(items, index))
    assert view.selected_entries() == []
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=items.name_rect(index).center())
    assert [e.name for e in view.selected_entries()] == ["a.txt"]
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=blank_point(items, index))
    assert view.selected_entries() == []


def test_list_ctrl_click_outside_name_keeps_selection(window, tree):
    view, items = list_view(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=items.name_rect(index).center())
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, blank_point(items, index))
    assert [e.name for e in view.selected_entries()] == ["a.txt"]


def test_list_right_click_outside_name_shows_folder_menu(window, tree):
    view, items = list_view(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    folder, files = [], []
    view.folderMenu.disconnect()  # sin esto se abriría el menú real, que espera a que alguien lo cierre
    view.fileMenu.disconnect()
    view.folderMenu.connect(lambda *a: folder.append(a))
    view.fileMenu.connect(lambda *a: files.append(a))
    point = blank_point(items, index)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.RightButton, pos=point)
    QCoreApplication.sendEvent(items.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, items.viewport().mapToGlobal(point)))
    assert folder and not files and view.selected_entries() == []
    point = items.name_rect(index).center()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.RightButton, pos=point)
    QCoreApplication.sendEvent(items.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, items.viewport().mapToGlobal(point)))
    assert files and [e.name for e in view.selected_entries()] == ["a.txt"]


def test_list_double_click_outside_name_opens_nothing(window, tree):
    view, items = list_view(window, tree)
    opened = []
    view.openFile.connect(lambda *a: opened.append(a))
    index = view.model.index(row_of(view, "a.txt"), 0)
    QTest.mouseDClick(items.viewport(), Qt.MouseButton.LeftButton, pos=blank_point(items, index))
    assert not opened


def test_list_full_row_setting_uses_the_whole_cell(window, tree):
    view, items = list_view(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    point = blank_point(items, index)
    view.set_full_row(True)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert [e.name for e in view.selected_entries()] == ["a.txt"]


@pytest.mark.parametrize("mode", ["smallicons", "largeicons", "thumbnails", "tiles"])
def test_other_item_modes_select_on_the_item(window, tree, mode):
    view, items = list_view(window, tree, mode)
    index = view.model.index(row_of(view, "a.txt"), 0)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=items.name_rect(index).center())
    assert [e.name for e in view.selected_entries()] == ["a.txt"]


# ---- fotograma del vídeo anterior ----

def fake_frame():
    return QVideoFrame(QVideoFrameFormat(QSize(4, 4), QVideoFrameFormat.PixelFormat.Format_RGBA8888))


def test_previous_frame_is_cleared_and_new_video_waits_for_its_own(media_window, tmp_path):
    win = media_window
    pane = win.preview
    first = str(write_wav(os.path.join(win.source.path, "uno.wav")))
    pane.show_entry(entry_from_path(first))
    assert wait_until(lambda: loaded(pane))
    media = pane.media
    sink = media.video.videoSink()
    sink.setVideoFrame(fake_frame())
    assert sink.videoFrame().isValid()
    pane.show_entry(None)
    assert not sink.videoFrame().isValid()
    video = os.path.join(win.source.path, "video.mp4")
    with open(video, "wb") as out:
        out.write(b"\0" * 64)
    media.path = video
    media.pages.setCurrentWidget(media.video)
    media.failed.disconnect()
    media._open(play=False)
    assert media.pages.currentWidget() is media.waiting_page
    sink.setVideoFrame(fake_frame())
    settle()
    assert media.pages.currentWidget() is media.video or not media.has_source


# ---- opciones de pestañas ----

def set_tabs(window, **options):
    window.config["folder_tabs"] = options
    window.apply_tab_settings()
    settle()
    return window.groups[0]


def test_tab_settings_discard_invalid_values():
    settings = tab_settings.normalize({"position": "arriba", "show_bar": 3, "fixed_size": 5, "max_size": "x", "show_index": 1,
                                       "drag_delay_ms": 99999, "show_drive": False, "desconocida": 1})
    assert settings["position"] == "top" and settings["show_bar"] == "always" and settings["fixed_size"] == 20
    assert settings["max_size"] == 100 and settings["show_index"] is False and settings["drag_delay_ms"] == 10000
    assert settings["show_drive"] is False and "desconocida" not in settings
    assert tab_settings.normalize("no") == tab_settings.DEFAULTS


@pytest.mark.parametrize("position,dual_position,dual,expected", [
    ("top", "normal", "Vertical", ("top", "top")),
    ("left", "normal", "Vertical", ("left", "left")),
    ("left", "together", "Vertical", ("right", "left")),
    ("right", "apart", "Vertical", ("left", "right")),
    ("top", "together", "Vertical", ("top", "top")),
    ("top", "together", "Horizontal", ("bottom", "top")),
    ("bottom", "apart", "Horizontal", ("top", "bottom")),
    ("left", "together", "Horizontal", ("left", "left")),
    ("left", "together", "Off", ("left", "left")),
])
def test_tab_positions_with_dual_panes(position, dual_position, dual, expected):
    assert tab_settings.tab_positions(position, dual_position, dual) == expected


def test_new_button_can_be_hidden(window):
    group = set_tabs(window, new_button=False)
    assert not group.add_button.isVisible()
    group = set_tabs(window)
    assert group.add_button.isVisible()


def test_new_tab_goes_next_to_active(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    group.setCurrentIndex(0)
    set_tabs(window, open_next_to_active=True)
    view = group.add_tab(str(tmp_path))
    assert group.indexOf(view) == 1 and group.currentIndex() == 1


@pytest.mark.parametrize("action", ["home", "default", "location"])
def test_new_tab_folder_options(window, tmp_path, action, monkeypatch):
    target = tmp_path / "elegida"
    target.mkdir()
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.platform_utils.home_dir", lambda: str(target))
    group = set_tabs(window, new_button_action=action, new_button_path=str(target), default_folder=str(target))
    group.add_button.click()
    assert wait_until(lambda: group.current.path == os.path.normpath(str(target)))


def test_new_tab_with_missing_location_goes_home(window, tmp_path, monkeypatch):
    home = tmp_path / "casa"
    home.mkdir()
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.platform_utils.home_dir", lambda: str(home))
    group = set_tabs(window, new_button_action="location", new_button_path=str(tmp_path / "no-existe"))
    group.add_button.click()
    assert group.current.path == os.path.normpath(str(home))


def test_duplicate_copies_view_settings(window):
    group = set_tabs(window, new_button_action="duplicate")
    group.current.set_view_mode("list")
    group.add_button.click()
    assert group.current.view_mode == "list" and group.count() == 2


def test_new_tab_from_current_does_not_copy_view_settings(window):
    group = window.groups[0]
    group.current.set_view_mode("list")
    group.add_button.click()
    assert group.current.view_mode == "details"


def test_double_click_can_be_turned_off(window):
    group = set_tabs(window, double_click=False)
    count = group.count()
    group._tab_double_clicked(-1)
    group._tab_double_clicked(0)
    assert group.count() == count


def test_double_click_action_is_independent(window, tmp_path, monkeypatch):
    target = tmp_path / "casa"
    target.mkdir()
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.platform_utils.home_dir", lambda: str(target))
    group = set_tabs(window, double_click_action="home")
    group._tab_double_clicked(-1)
    assert group.current.path == os.path.normpath(str(target))


def test_double_click_can_close_a_tab(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    set_tabs(window, double_click_close=True)
    count = group.count()
    group._tab_double_clicked(0)
    assert group.count() == count - 1


def test_without_menu_uses_scroll_arrows(window, tmp_path):
    group = set_tabs(window, overflow_menu=False, multi_row=False)
    open_folders(group, tmp_path, 12)
    settle()
    assert not group.overflow_button.isVisible()
    arrows = [b for b in group.tabBar().findChildren(QToolButton) if b.objectName().startswith("Scroll")]
    assert arrows and all(b.width() > 0 for b in arrows)


def test_menu_can_list_all_tabs(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    set_tabs(window, menu_all_tabs=True)
    assert group.hidden_tabs() == list(range(group.count()))


def test_shrink_fits_all_tabs_and_menu_is_optional(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    set_tabs(window, shrink=True, multi_row=False)
    bar = group.tabBar()
    assert bar.rect_of(group.count() - 1).right() <= bar.width() + 1
    assert not group.overflow_button.isVisible() and group.add_button.isVisible()
    set_tabs(window, shrink=True, menu_when_shrunk=True, multi_row=False)
    assert group.overflow_button.isVisible()


@pytest.mark.parametrize("mode,expected", [("needed", (False, False)), ("balanced", (True, True)), ("always", (True, True))])
def test_show_bar_modes(window, tmp_path, mode, expected):
    window.groups[1].add_tab(str(tmp_path))
    set_tabs(window, show_bar=mode)
    window.groups[1].close_tab(1) if mode == "needed" else None
    settle()
    shown = tuple(not g.tabBar().isHidden() for g in window.groups[:2])
    assert shown == expected


def test_balanced_hides_both_only_when_both_have_one_tab(window):
    set_tabs(window, show_bar="balanced")
    assert all(g.tabBar().isHidden() for g in window.groups)
    assert not window.groups[0].add_button.isVisible()


@pytest.mark.parametrize("position", ["top", "bottom", "left", "right"])
def test_tab_bar_positions(window, position):
    group = set_tabs(window, position=position)
    settle()
    bar = group.tabBar().geometry()
    page = QRect(group.current.mapTo(group, QPoint(0, 0)), group.current.size())
    if position == "top":
        assert bar.bottom() <= page.top()
    elif position == "bottom":
        assert bar.top() >= page.bottom()
    elif position == "left":
        assert bar.right() <= page.left()
    else:
        assert bar.left() >= page.right()
    set_tabs(window)


@pytest.mark.parametrize("position", ["bottom", "left", "right"])
def test_add_and_menu_buttons_work_in_every_position(window, tmp_path, position):
    group = set_tabs(window, position=position, multi_row=False)
    open_folders(group, tmp_path, 40)
    settle()
    bar = group.tabBar().geometry()
    add, menu = group.add_button.geometry(), group.overflow_button.geometry()
    assert group.add_button.isVisible() and group.overflow_button.isVisible() and group.hidden_tabs()
    assert group.rect().contains(add) and group.rect().contains(menu) and not add.intersects(menu)
    assert not bar.intersects(add) and not bar.intersects(menu)
    set_tabs(window)


def test_dual_position_applies_to_each_pane(window):
    window.set_dual("Vertical")
    set_tabs(window, position="left", dual_position="together")
    assert window.groups[0].tabPosition() == window.groups[0].TabPosition.East
    assert window.groups[1].tabPosition() == window.groups[1].TabPosition.West
    set_tabs(window)


def test_vertical_tabs_use_chosen_width_and_horizontal_text(window, tmp_path):
    group = set_tabs(window, position="left", vertical_width=150)
    open_folders(group, tmp_path, 2)
    bar = group.tabBar()
    assert bar.rect_of(0).width() == 150 and bar.rect_of(0).height() < 60
    assert not bar.rows_active()
    set_tabs(window, position="left", vertical_auto_width=True)
    assert bar.rect_of(0).width() != 150
    group.grab()  # el dibujo propio no debe fallar
    set_tabs(window)


def test_same_size_fixed_max_and_min(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    bar = group.tabBar()
    set_tabs(window, same_size=True, same_size_mode="fixed", fixed_size=90)
    assert {bar.rect_of(i).width() for i in range(group.count())} == {90}
    set_tabs(window, same_size=True)
    assert len({bar.rect_of(i).width() for i in range(group.count())}) == 1
    set_tabs(window, max_size_on=True, max_size=60)
    assert max(bar.rect_of(i).width() for i in range(group.count())) == 60
    set_tabs(window, min_size_on=True, min_size=500)
    assert min(bar.rect_of(i).width() for i in range(group.count())) == 500


def test_label_options(window, tmp_path):
    group = window.groups[0]
    view = group.add_tab(str(tmp_path))
    name = os.path.basename(str(tmp_path))
    set_tabs(window, show_index=True)
    assert group.tabText(0).startswith("1 ") and group.tabText(1).startswith("2 ")
    group.close_tab(0)
    settle()
    assert group.tabText(0).startswith("1 ")
    set_tabs(window, show_drive=False)
    assert group.tabText(0) == name
    set_tabs(window, show_icons=False)
    assert group.tabIcon(0).isNull()
    set_tabs(window)
    assert not group.tabIcon(0).isNull()
    assert view.path


def test_close_button_options(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    bar = group.tabBar()
    side = bar.ButtonPosition.RightSide
    set_tabs(window, show_close=False)
    assert bar.tabButton(group.currentIndex(), side) is None
    new_view = group.add_tab(str(tmp_path))
    assert bar.tabButton(group.indexOf(new_view), side) is None
    set_tabs(window, small_close=True)
    assert bar.tabButton(group.currentIndex(), side).width() == 12
    set_tabs(window)
    assert bar.tabButton(group.currentIndex(), side).width() == 16
    before = group.count()
    bar.tabButton(group.currentIndex(), side).click()
    assert group.count() == before - 1


def test_middle_click_closes_tab(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    bar = group.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.MiddleButton, pos=bar.rect_of(0).center())
    assert group.count() == 1


def test_click_on_selected_tab_goes_to_previous(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    set_tabs(window, selected_click="previous")
    bar = group.tabBar()
    group.setCurrentIndex(0)
    group.setCurrentIndex(1)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(1).center())
    assert wait_until(lambda: group.currentIndex() == 0)


def test_click_on_another_tab_only_activates_it(window, tmp_path, monkeypatch):
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    set_tabs(window, selected_click="rename")
    asked = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QInputDialog.getText", lambda *a: asked.append(a) or ("x", True))
    bar = group.tabBar()
    group.setCurrentIndex(1)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(0).center())
    time.sleep(group._click_timer.interval() / 1000 + 0.05)
    settle()
    assert group.currentIndex() == 0 and not asked


def test_click_on_selected_tab_renames_and_name_survives_restart(app, tmp_path, tree, monkeypatch):
    left, right = tree
    cfg = str(tmp_path / "cfg")
    win = MainWindow(Config(cfg), [str(left), str(right)])
    win.show()
    settle()
    group = win.groups[0]
    win.config["folder_tabs"] = {"selected_click": "rename"}
    win.apply_tab_settings()
    settle()
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QInputDialog.getText", lambda *a: ("Mi pestaña", True))
    bar = group.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(0).center())
    assert wait_until(lambda: group.tabText(0) == "Mi pestaña")
    win.close()
    other = MainWindow(Config(cfg))
    assert other.groups[0].tabText(0) == "Mi pestaña"
    other.close()


def test_double_click_does_not_also_trigger_selected_click(window, monkeypatch):
    group = set_tabs(window, selected_click="rename", double_click=False)
    asked = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QInputDialog.getText", lambda *a: asked.append(a) or ("", False))
    bar = group.tabBar()
    point = bar.rect_of(0).center()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=point)
    time.sleep(group._click_timer.interval() / 1000 + 0.05)
    settle()
    assert not asked


def test_tab_menu_actions(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 2)
    labels_ = [a.text() for a in group.tab_menu(0).actions()]
    assert {"Abrir nueva pestaña", "Duplicar pestaña", "Renombrar pestaña…", "Cerrar pestaña", "Cerrar las demás pestañas"} <= set(labels_)
    next(a for a in group.tab_menu(0).actions() if a.text() == "Cerrar las demás pestañas").trigger()
    assert group.count() == 1
    assert [a.text() for a in group.tab_menu(-1).actions() if not a.isSeparator()] == ["Abrir nueva pestaña", "Personalizar…"]


def test_tab_as_folder_right_click_shows_folder_menu(window, monkeypatch):
    group = set_tabs(window, tab_as_folder=True)
    received = []
    group.current.folderMenu.disconnect()
    group.current.folderMenu.connect(lambda *a: received.append(a))
    group._show_tab_menu(group.tabBar().rect_of(0).center())
    assert received


DRAG_DATA = []  # el evento no conserva sus datos: si se destruyeran antes de usarlo, el proceso se cerraría


def drag_event(kind, bar, point, path):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    DRAG_DATA.append(mime)
    if kind == QEvent.Type.Drop:
        return QDropEvent(QPointF(point), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    return QDragMoveEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def test_dragging_over_tab_brings_it_to_front(window, tmp_path, tree):
    left, _ = tree
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    set_tabs(window, drag_delay_ms=10)
    bar = group.tabBar()
    group.setCurrentIndex(1)
    bar.dragMoveEvent(drag_event(QEvent.Type.DragMove, bar, bar.rect_of(0).center(), left / "a.txt"))
    assert wait_until(lambda: group.currentIndex() == 0)


def test_drag_to_front_can_be_turned_off(window, tmp_path, tree):
    left, _ = tree
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    set_tabs(window, drag_to_front=False)
    bar = group.tabBar()
    group.setCurrentIndex(1)
    bar.dragMoveEvent(drag_event(QEvent.Type.DragMove, bar, bar.rect_of(0).center(), left / "a.txt"))
    assert not bar._drag_timer.isActive()


def test_drop_on_tab_copies_only_when_treated_as_folder(window, tmp_path, tree):
    _, right = tree
    file = right / "c.txt"
    file.write_text("c", encoding="utf-8")
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    bar = group.tabBar()
    received = []
    group.widget(0).filesDropped.connect(lambda *a: received.append(a))
    bar.dropEvent(drag_event(QEvent.Type.Drop, bar, bar.rect_of(0).center(), file))
    settle()
    assert not received
    set_tabs(window, tab_as_folder=True)
    bar.dropEvent(drag_event(QEvent.Type.Drop, bar, bar.rect_of(0).center(), file))
    assert wait_until(lambda: received)
    assert received[0][1] == [os.path.normpath(str(file))] and received[0][2] is None


def test_last_tab_closes_right_pane_then_window(window):
    set_tabs(window, close_window_with_last_tab=True)
    window.set_dual("Vertical")
    window.groups[1].close_tab(0)
    assert window.dual == "Off"
    window.groups[0].close_tab(0)
    assert not window.isVisible()


def test_last_tab_stays_by_default(window):
    window.groups[0].close_tab(0)
    assert window.groups[0].count() == 1 and window.isVisible()


def test_window_close_button_closes_active_tab_only_when_spontaneous(window, tmp_path, monkeypatch):
    group = window.groups[0]
    open_folders(group, tmp_path, 1)
    set_tabs(window, window_close_closes_tab=True)
    window.active_side = 0
    event = QCloseEvent()
    monkeypatch.setattr(event, "spontaneous", lambda: True, raising=False)
    window.closeEvent(event)
    assert group.count() == 1 and not event.isAccepted()


def test_preferences_pages_roundtrip(app):
    config = {"folder_tabs": {"new_button_action": "location", "new_button_path": "/x", "position": "left", "show_bar": "balanced",
                              "same_size": True, "same_size_mode": "fixed", "fixed_size": 77, "selected_click": "previous",
                              "drag_delay_ms": 300, "vertical_auto_width": True, "show_index": True, "shrink": True}}
    dialog = PreferencesDialog(config)
    assert dialog.pages.count() == (16 if windows_shell.AVAILABLE else 15)
    dialog.apply_to(config)
    assert config["folder_tabs"] == {**tab_settings.DEFAULTS, **config["folder_tabs"]}
    saved = config["folder_tabs"]
    assert saved["new_button_path"] == "/x" and saved["position"] == "left" and saved["fixed_size"] == 77
    assert saved["same_size_mode"] == "fixed" and saved["vertical_auto_width"] is True and saved["drag_delay_ms"] == 300


def test_preferences_defaults_do_not_change_anything(app):
    config = {}
    dialog = PreferencesDialog(config)
    dialog.apply_to(config)
    assert config["folder_tabs"] == tab_settings.DEFAULTS


def test_preferences_enable_only_what_applies(app):
    dialog = PreferencesDialog({})
    tabs = dialog.tabs
    assert not tabs.new_button_path.isEnabled()
    tabs.new_button_action.setCurrentIndex(tabs.new_button_action.findData("location"))
    assert tabs.new_button_path.isEnabled()
    tabs.checks["new_button"].setChecked(False)
    assert not tabs.new_button_action.isEnabled() and not tabs.new_button_path.isEnabled()
    tabs.checks["overflow_menu"].setChecked(False)
    assert not tabs.checks["menu_all_tabs"].isEnabled()
    tabs.checks["show_close"].setChecked(False)
    assert not tabs.checks["close_active_only"].isEnabled() and not tabs.checks["small_close"].isEnabled()


def test_tab_settings_travel_in_exported_configuration(window, tmp_path):
    path = tmp_path / "c.json"
    window.config["folder_tabs"] = {"position": "bottom"}
    window.export_settings_file(str(path))
    window.config["folder_tabs"] = {}
    assert window.import_settings_file(str(path)) == []
    assert window.groups[0].tabPosition() == window.groups[0].TabPosition.South
    set_tabs(window)


def test_invalid_tab_settings_in_file_do_not_break_the_window(window, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "version": 1,
                                "settings": {"folder_tabs": {"position": "dentro", "fixed_size": "x"}}}), encoding="utf-8")
    assert window.import_settings_file(str(path)) == []
    assert window.groups[0].tabPosition() == window.groups[0].TabPosition.North


# ---- pestañas en varias filas ----

@pytest.fixture
def row_group(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 12)
    set_tabs(window, multi_row=True)
    settle()
    yield group
    set_tabs(window)


def test_rows_show_every_tab_without_overlap(row_group):
    bar = row_group.tabBar()
    rects = [bar.rect_of(i) for i in range(row_group.count())]
    assert bar.row_count() > 1 and len({r.top() for r in rects}) > 1
    for index, rect in enumerate(rects):
        assert rect.left() >= 0 and rect.right() < row_group.width()
        assert not any(rect.intersects(other) for other in rects[index + 1:])
    assert row_group.hidden_tabs() == [] and not row_group.overflow_button.isVisible()


def test_rows_are_the_default():
    assert tab_settings.normalize(None)["multi_row"] is True
    assert tab_settings.normalize({"show_close": True})["multi_row"] is True  # configuración guardada antes de existir la opción


def test_rows_every_close_button_is_inside_its_tab(row_group):
    bar = row_group.tabBar()
    row_group.grab()
    settle()
    for index in range(row_group.count()):
        assert bar.rect_of(index).contains(bar.tabButton(index, bar.ButtonPosition.RightSide).geometry())


def test_rows_push_the_list_down(row_group):
    bar = row_group.tabBar()
    settle()
    assert bar.geometry().height() == bar.row_count() * bar.row_height()
    assert row_group.current.mapTo(row_group, QPoint(0, 0)).y() >= bar.geometry().bottom()


def test_rows_keep_add_button_after_last_tab(row_group):
    bar = row_group.tabBar()
    add = row_group.add_button
    assert add.isVisible() and row_group.rect().contains(add.geometry())
    for index in range(row_group.count()):
        assert not bar.rect_of(index).translated(bar.geometry().topLeft()).intersects(add.geometry())
    count = row_group.count()
    add.click()
    assert row_group.count() == count + 1


def test_rows_reflow_when_the_pane_changes_width(window, row_group):
    bar = row_group.tabBar()
    before = bar.row_count()
    window.resize(window.width() + 700, window.height())
    settle()
    assert bar.row_count() < before
    assert bar.geometry().height() == bar.row_count() * bar.row_height()


def test_rows_do_not_force_a_wider_window(row_group):
    assert row_group.tabBar().minimumSizeHint().width() < 100


def test_rows_click_activates_the_tab_under_the_mouse(row_group):
    bar = row_group.tabBar()
    for index in (0, row_group.count() - 2, 3):
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(index).center())
        assert row_group.currentIndex() == index


def test_rows_close_button_closes_the_active_tab(row_group):
    bar = row_group.tabBar()
    row_group.setCurrentIndex(6)
    settle()
    row_group.grab()
    button = bar.tabButton(6, bar.ButtonPosition.RightSide)
    assert bar.rect_of(6).contains(button.geometry())
    count = row_group.count()
    view = row_group.widget(6)
    button.click()
    assert row_group.count() == count - 1 and row_group.indexOf(view) < 0


def test_rows_middle_click_and_double_click(window, row_group):
    bar = row_group.tabBar()
    count = row_group.count()
    view = row_group.widget(8)
    QTest.mouseClick(bar, Qt.MouseButton.MiddleButton, pos=bar.rect_of(8).center())
    assert row_group.count() == count - 1 and row_group.indexOf(view) < 0
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(0).center())
    assert row_group.count() == count
    set_tabs(window, multi_row=True, double_click_close=True)
    view = row_group.widget(2)
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(2).center())
    assert row_group.indexOf(view) < 0


def test_rows_drag_moves_a_tab(row_group):
    bar = row_group.tabBar()
    view = row_group.widget(0)
    moved_to = bar.rect_of(2).center()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.rect_of(0).center())
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(moved_to), bar.mapToGlobal(QPointF(moved_to)), Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(bar, event)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=moved_to)
    assert row_group.indexOf(view) > 0 and row_group.tabBar().tabText(row_group.indexOf(view)) == row_group.tabText(row_group.indexOf(view))


def test_rows_context_menu_and_file_drag_use_the_right_tab(window, row_group, tree):
    left, _ = tree
    bar = row_group.tabBar()
    point = bar.rect_of(row_group.count() - 1).center()
    assert bar.index_at(point) == row_group.count() - 1
    set_tabs(window, multi_row=True, drag_delay_ms=10)
    row_group.setCurrentIndex(0)
    bar.dragMoveEvent(drag_event(QEvent.Type.DragMove, bar, point, left / "a.txt"))
    assert wait_until(lambda: row_group.currentIndex() == row_group.count() - 1)


def test_rows_paint_and_tooltip_do_not_fail(row_group):
    bar = row_group.tabBar()
    assert not bar.grab().isNull()
    point = bar.rect_of(1).center()
    assert QCoreApplication.sendEvent(bar, QHelpEvent(QEvent.Type.ToolTip, point, bar.mapToGlobal(point)))


def test_rows_only_apply_above_or_below(window, tmp_path):
    group = window.groups[0]
    open_folders(group, tmp_path, 3)
    set_tabs(window, multi_row=True, position="left")
    assert not group.tabBar().rows_active()
    set_tabs(window, multi_row=True, position="bottom")
    bar = group.tabBar()
    assert bar.rows_active()
    settle()
    assert bar.geometry().top() >= group.current.mapTo(group, QPoint(0, group.current.height())).y()
    set_tabs(window)


def test_rows_turn_off_cleanly(window, row_group):
    bar = row_group.tabBar()
    set_tabs(window, multi_row=False)
    settle()
    assert not bar.rows_active() and bar.geometry().height() < 2 * bar.rect_of(0).height()
    assert row_group.overflow_button.isVisible()


def test_preferences_rows_option_disables_the_menu_options(app):
    dialog = PreferencesDialog({"folder_tabs": {"multi_row": False}})
    checks = dialog.tabs.checks
    assert checks["overflow_menu"].isEnabled() and checks["menu_all_tabs"].isEnabled()
    checks["multi_row"].setChecked(True)
    assert not checks["overflow_menu"].isEnabled() and not checks["menu_all_tabs"].isEnabled()
    config = {}
    dialog.apply_to(config)
    assert config["folder_tabs"]["multi_row"] is True


# ---- lista como primera vista ----

def view_labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test_list_is_the_default_view(app, tmp_path):
    win = MainWindow(Config(str(tmp_path / "cfg")), [str(tmp_path), str(tmp_path)])
    win.show()
    settle()
    assert win.source.view_mode == "list" and win.source.current_view is win.source.thumbs
    group = win.groups[0]
    assert group.add_tab(str(tmp_path)).view_mode == "list"
    win.close()


def test_list_comes_first_in_every_view_menu(window):
    menu = window.build_folder_menu(window.source)
    views = next(a.menu() for a in menu.actions() if a.text() == "Ver")
    assert view_labels(views)[:2] == ["Lista", "Detalles"]
    toolbar = next(b for b in DEFAULT_TOOLBAR if b.get("icon") == "cyclemodes")
    assert [c["label"] for c in toolbar["children"]][:2] == ["Lista", "Detalles"]
    assert "Set VIEW=Cycle,List" in toolbar["commands"][0]


def test_shortcut_one_is_list(window):
    action = next(a for a in window.findChildren(QAction) if a.text() == "Lista" and a.shortcut().toString())
    assert action.shortcut().toString() == "Ctrl+1"


def test_older_configuration_moves_to_list(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "config_version": 2,
        "favorites": [{"label": "f", "path": "/x"}],
        "styles": [{"name": "Vertical Dual", "view": "details"}, {"name": "Imágenes", "view": "thumbnails"},
                   {"name": "Mío", "view": "details"}],
        "toolbar": [{"label": "Ver", "commands": ["Set VIEW=Cycle"], "children": [
            {"label": "Grandes", "commands": ["Set VIEW=LargeIcons"]}, {"label": "Pequeños", "commands": ["Set VIEW=SmallIcons"]},
            {"label": "Lista", "commands": ["Set VIEW=List"]}, {"label": "Detalles", "commands": ["Set VIEW=Details"]}]},
            {"label": "Otro", "commands": ["Go UP"], "children": [{"label": "B", "commands": ["Go ROOT"]}, {"separator": True}]},
            {"separator": True}],
    }), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["config_version"] == CONFIG_VERSION
    views = {style["name"]: style["view"] for style in config["styles"]}
    assert (views["Vertical doble panel"], views["Imágenes"], views["Mío"]) == ("list", "thumbnails", "details")  # el estilo propio no cambia
    children = config["toolbar"][0]["children"]
    assert [c["label"] for c in children] == ["Lista", "Detalles", "Grandes", "Pequeños"]
    assert config["toolbar"][1]["children"][0]["label"] == "B"
    assert config["favorites"] == [{"label": "f", "path": "/x"}]


def test_current_configuration_keeps_details_if_chosen(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 3, "styles": [{"name": "Vertical Dual", "view": "details"}]}), encoding="utf-8")
    assert Config(str(tmp_path))["styles"][0]["view"] == "details"


def test_computer_view_stays_in_details_with_list_mode(window, tree):
    if not has_computer_view():
        pytest.skip("Solo donde existe «Este equipo»")
    left, _ = tree
    view = window.source
    view.set_view_mode("list")
    view.navigate(COMPUTER_PATH)
    assert view.stack.currentWidget() is view.details and view.current_view is view.details
    view.navigate(str(left))
    assert view.stack.currentWidget() is view.thumbs and view.view_mode == "list"


# ---- desplegar carpetas en lista ----

def list_with_folder(window, tree):
    left, _ = tree
    view = window.source
    view.set_view_mode("list")
    settle()
    return view, view.thumbs, left


def chevron_point(items, index):
    rect = items.visualRect(index)
    entry = index.data(EntryRole)
    return QPoint(rect.left() + entry.depth * 14 + 7, rect.center().y())


def test_list_chevron_expands_and_collapses_folder(window, tree):
    view, items, _left = list_with_folder(window, tree)
    model = view.model
    index = model.index(row_of(view, "subcarpeta"), 0)
    rows = model.rowCount()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=chevron_point(items, index))
    assert wait_until(lambda: model.rowCount() == rows + 1)
    child = model.index(row_of(view, "c.txt"), 0)
    assert child.data(EntryRole).depth == 1
    assert view.selected_entries() == []  # desplegar no selecciona
    index = model.index(row_of(view, "subcarpeta"), 0)
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=chevron_point(items, index))
    assert wait_until(lambda: model.rowCount() == rows)


def test_list_child_is_indented_and_selectable(window, tree):
    view, items, _left = list_with_folder(window, tree)
    model = view.model
    model.set_expanded(model.entry(row_of(view, "subcarpeta")), True)
    assert wait_until(lambda: any(model.entry(r) and model.entry(r).name == "c.txt" for r in range(model.rowCount())))
    settle()
    child = model.index(row_of(view, "c.txt"), 0)
    top = model.index(row_of(view, "a.txt"), 0)
    assert items.name_rect(child).left() - items.visualRect(child).left() == items.name_rect(top).left() - items.visualRect(top).left() + 14
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=items.name_rect(child).center())
    assert [e.name for e in view.selected_entries()] == ["c.txt"]


def test_list_file_has_no_chevron_action(window, tree):
    view, items, _left = list_with_folder(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    rows = view.model.rowCount()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=chevron_point(items, index))
    settle()
    assert view.model.rowCount() == rows and view.selected_entries() == []


def test_chevron_only_in_list_among_item_views(window, tree):
    view, items, _left = list_with_folder(window, tree)
    view.set_view_mode("smallicons")
    settle()
    index = view.model.index(row_of(view, "subcarpeta"), 0)
    rows = view.model.rowCount()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=chevron_point(items, index))
    settle()
    assert view.model.rowCount() == rows


def test_list_chevron_respects_the_expandable_setting(window, tree):
    view, items, _left = list_with_folder(window, tree)
    window.run_command("Set EXPANDABLEFOLDERS=Off")
    assert wait_until(lambda: not view.model.can_expand)
    index = view.model.index(row_of(view, "subcarpeta"), 0)
    rows = view.model.rowCount()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=chevron_point(items, index))
    settle()
    assert view.model.rowCount() == rows


def test_list_row_is_as_compact_as_its_text(window, tree):
    view, items, _left = list_with_folder(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)
    assert items.visualRect(index).height() == items.fontMetrics().height() + 3


def test_list_long_name_is_not_cut(window, tree):
    left, _ = tree
    name = "nombre_mucho_mas_largo_que_el_de_los_demas_archivos.txt"
    (left / name).write_text("x", encoding="utf-8")
    view = window.source
    view.set_view_mode("list")
    window.run_command("Go REFRESH")
    assert wait_until(lambda: view.model.rowCount() == 4)
    settle()
    items = view.thumbs
    rect = items.name_rect(view.model.index(row_of(view, name), 0))
    assert rect.width() >= items.fontMetrics().horizontalAdvance(name)


def test_list_hover_only_on_the_file(window, tree):
    view, items, _left = list_with_folder(window, tree)
    index = view.model.index(row_of(view, "a.txt"), 0)

    def move(point):
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point), items.viewport().mapToGlobal(QPointF(point)), Qt.MouseButton.NoButton,
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(items.viewport(), event)

    move(items.name_rect(index).center())
    assert items.hover_row == index.row()
    move(QPoint(items.viewport().width() - 2, items.viewport().height() - 2))
    assert items.hover_row == -1


# ---- abrir imágenes ----

@pytest.fixture
def image_file(tree):
    left, _ = tree
    path = left / "foto.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    second = left / "otra.png"
    assert image.save(str(second))
    return path


def open_entry_in(window, path):
    window.run_command("Go REFRESH")
    model = window.source.model
    assert wait_until(lambda: any(model.entry(r) and model.entry(r).path == os.path.normpath(str(path)) for r in range(model.rowCount())))
    window._open_file(window.source, entry_from_path(os.path.normpath(str(path))))


def test_image_opens_in_internal_viewer_by_default(window, image_file, monkeypatch):
    opened = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url))
    assert window.config["open_images"] == "viewer"
    open_entry_in(window, image_file)
    assert not opened and len(window._viewers) == 1
    viewer = window._viewers[0]
    assert viewer.isVisible() and len(viewer.paths) == 2
    assert viewer.paths[viewer.index] == os.path.normpath(str(image_file))
    viewer.close()


def test_image_opens_with_system_application_if_chosen(window, image_file, monkeypatch):
    opened = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url))
    window.config["open_images"] = "system"
    open_entry_in(window, image_file)
    assert len(opened) == 1 and not window._viewers


def test_other_files_always_open_with_the_system(window, tree, monkeypatch):
    left, _ = tree
    opened = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url))
    window._open_file(window.source, entry_from_path(str(left / "a.txt")))
    assert len(opened) == 1 and not window._viewers


def test_double_click_on_image_opens_a_single_viewer(window, image_file):
    window.run_command("Go REFRESH")
    view = window.source
    assert wait_until(lambda: any(view.model.entry(r) and view.model.entry(r).name == "foto.png" for r in range(view.model.rowCount())))
    view.set_view_mode("list")
    settle()
    items = view.thumbs
    point = items.name_rect(view.model.index(row_of(view, "foto.png"), 0)).center()
    QTest.mouseClick(items.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(items.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert wait_until(lambda: len(window._viewers) == 1)
    settle()
    assert len(window._viewers) == 1
    window._viewers[0].close()


def test_preferences_choose_how_images_open(app):
    config = {"open_images": "system"}
    dialog = PreferencesDialog(config)
    assert dialog.open_images.currentData() == "system"
    dialog.open_images.setCurrentIndex(dialog.open_images.findData("viewer"))
    dialog.apply_to(config)
    assert config["open_images"] == "viewer"
    assert PreferencesDialog({}).open_images.currentData() == "viewer"


def test_open_images_setting_is_validated_on_import(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"open_images": "otro"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({}, ["open_images"])
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"open_images": "system"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"open_images": "system"}, [])


def test_list_can_reach_a_file_the_batches_have_not_placed_yet(window, tmp_path):
    folder = tmp_path / "muchos"
    folder.mkdir()
    for number in range(60):
        (folder / f"archivo_{number:03}.txt").write_text("", encoding="utf-8")
    view = window.source
    view.set_view_mode("list")
    items = view.thumbs
    items.setBatchSize(5)
    view.navigate(str(folder))
    assert wait_until(lambda: view.model.rowCount() == 60)
    last = view.model.index(59, 0)
    items.doItemsLayout()  # con lotes de 5 solo coloca los primeros
    assert not items.visualRect(last).isValid()
    items.scrollTo(last)
    assert items.visualRect(last).isValid()
    assert items.viewport().rect().intersects(items.visualRect(last))
    assert items.layoutMode() == items.LayoutMode.Batched


def test_current_view_is_marked_in_the_view_menu(window):
    window.source.set_view_mode("list")
    menu = window.build_folder_menu(window.source)
    views = next(a.menu() for a in menu.actions() if a.text() == "Ver")
    checked = [a.text() for a in views.actions() if a.isChecked()]
    assert checked == ["Lista"]
    assert "QMenu::icon:checked" in QApplication.instance().styleSheet()


# ---- niveles de carpetas desplegadas en otro color ----

def expanded_view(window, tree, mode):
    view = window.source
    view.set_view_mode(mode)
    model = view.model
    model.set_expanded(model.entry(row_of(view, "subcarpeta")), True)
    assert wait_until(lambda: any(model.entry(r) and model.entry(r).name == "c.txt" for r in range(model.rowCount())))
    settle()
    return view, model


def pixel_right_of_name(view, model, name):
    """Color del fondo de esa fila, en el hueco a la derecha del nombre."""
    target = view.current_view
    index = model.index(row_of(view, name), 0)
    point = target.name_rect(index).topRight() + QPoint(6, 2)
    return target.viewport().grab().toImage().pixelColor(point)


@pytest.mark.parametrize("mode", ["list", "details"])
def test_expanded_level_has_its_own_background(window, tree, mode):
    view, model = expanded_view(window, tree, mode)
    color = pixel_right_of_name(view, model, "c.txt")
    assert color == theme.level_color(view.details.pane_role, 1)
    assert pixel_right_of_name(view, model, "a.txt") != color


@pytest.mark.parametrize("mode", ["list", "details"])
def test_level_colors_can_be_turned_off_and_keep_the_indent(window, tree, mode):
    view, model = expanded_view(window, tree, mode)
    target = view.current_view
    child = model.index(row_of(view, "c.txt"), 0)
    top = model.index(row_of(view, "a.txt"), 0)
    indent = target.name_rect(child).left() - target.name_rect(top).left()
    assert indent == 14
    window.config["tree_level_colors"] = False
    window._apply_level_colors()
    settle()
    assert pixel_right_of_name(view, model, "c.txt") == pixel_right_of_name(view, model, "a.txt")
    assert target.name_rect(child).left() - target.name_rect(top).left() == indent


def test_each_level_and_pane_has_its_color():
    first, second = theme.level_color("source", 1), theme.level_color("source", 2)
    assert first != second and first != theme.level_color("dest", 1)
    assert theme.level_color("source", 3) == theme.level_color("source", 9)  # no se sale del color del panel


def test_new_tab_uses_the_level_color_setting(window, tmp_path):
    window.config["tree_level_colors"] = False
    assert window.groups[0].add_tab(str(tmp_path)).thumbs.level_colors is False


def test_preferences_level_colors_option(app):
    config = {}
    dialog = PreferencesDialog(config)
    assert dialog.level_colors.isChecked()
    dialog.level_colors.setChecked(False)
    dialog.apply_to(config)
    assert config["tree_level_colors"] is False


# ---- información de carpetas al pasar el ratón ----

def section_texts(card):
    return [card.sections.itemAt(i).widget().text() for i in range(card.sections.count())]


def test_folder_card_shows_size_folders_and_files(window, tree):
    left, _ = tree
    (left / "subcarpeta" / "dentro").mkdir()
    (left / "subcarpeta" / "grande.bin").write_bytes(b"x" * 2048)
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(card.isVisible)
    assert card.title.text() == "subcarpeta" and card.subtitle.isHidden()
    texts = section_texts(card)
    assert texts[0] == "Carpetas:" and texts[1] == "dentro"
    assert texts[2] == "Archivos:" and texts[3].split("\n") == ["c.txt", "grande.bin"]
    assert wait_until(lambda: form_rows(card).get("Tamaño") == format_size(2049))
    assert card.row.direction() == card.row.Direction.RightToLeft  # el icono, a la derecha


def test_folder_card_limits_long_lists(window, tree):
    left, _ = tree
    many = left / "muchos"
    many.mkdir()
    for number in range(13):
        (many / f"f{number:02}.txt").write_text("", encoding="utf-8")
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "muchos" in names(window.source))
    view = window.source
    hover(view, "muchos")
    assert wait_until(view.hover_card.isVisible)
    lines = section_texts(view.hover_card)[1].split("\n")
    assert len(lines) == 11 and lines[0] == "f00.txt" and lines[-1] == "… y 3 más"


def test_folder_card_empty_folder(window, tree):
    left, _ = tree
    (left / "vacia").mkdir()
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "vacia" in names(window.source))
    view = window.source
    hover(view, "vacia")
    assert wait_until(view.hover_card.isVisible)
    assert section_texts(view.hover_card) == ["Carpeta vacía"]


def test_folder_card_follows_the_hidden_files_setting(window, tree, monkeypatch):
    left, _ = tree
    folder = left / "subcarpeta"
    (folder / "visible.txt").write_text("", encoding="utf-8")
    real = hover_card_module.list_dir
    monkeypatch.setattr(hover_card_module, "list_dir", lambda path, hidden: [e for e in real(path, True) if hidden or e.name != "c.txt"])
    view = window.source
    hover(view, "subcarpeta")
    assert wait_until(view.hover_card.isVisible)
    assert section_texts(view.hover_card)[1] == "visible.txt"
    view.hover_card.dismiss()
    view.show_hidden = True
    hover(view, "subcarpeta")
    assert wait_until(view.hover_card.isVisible)
    assert section_texts(view.hover_card)[1].split("\n") == ["c.txt", "visible.txt"]


def test_folder_card_names_are_plain_text(window, tree):
    left, _ = tree
    (left / "subcarpeta" / "<b>negrita<b>.txt").write_text("", encoding="utf-8") if os.name != "nt" else None
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(card.isVisible)
    for index in range(card.sections.count()):
        assert card.sections.itemAt(index).widget().textFormat() == Qt.TextFormat.PlainText


def test_folder_card_unreadable_folder(window, monkeypatch):
    def fail(*_a):
        raise PermissionError("no")

    monkeypatch.setattr(hover_card_module, "list_dir", fail)
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(card.isVisible)
    assert card.subtitle.text() == "No se puede leer esta carpeta" and not form_rows(card)


def test_folder_card_stops_measuring_when_dismissed(window, monkeypatch):
    seen = []

    def slow(path, seconds, sink):
        seen.append(sink)
        while not sink.is_cancelled:
            time.sleep(0.01)
        sink.check()

    monkeypatch.setattr(hover_card_module.fsops, "tree_size_within", slow)
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(lambda: card.isVisible() and seen)
    assert form_rows(card)["Tamaño"] == "calculando…"
    card.dismiss()
    assert wait_until(lambda: seen[0].is_cancelled)
    assert hover_card_module._folder_size_pool.waitForDone(3000)


def test_folder_card_reports_when_time_ran_out(window, monkeypatch):
    monkeypatch.setattr(hover_card_module.fsops, "tree_size_within", lambda *_a: (2048, False))
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(lambda: card.isVisible() and form_rows(card).get("Tamaño", "").startswith("más de"))


def test_file_card_is_restored_after_a_folder_card(window):
    view = window.source
    hover(view, "subcarpeta")
    card = view.hover_card
    assert wait_until(card.isVisible)
    hover(view, "a.txt")
    assert wait_until(lambda: card.title.text() == "a.txt")
    assert not card.subtitle.isHidden() and section_texts(card) == []
    assert card.row.direction() == card.row.Direction.LeftToRight and card.thumb.width() == 112


def test_size_within_time_limit(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 10)
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "b").write_bytes(b"x" * 5)
    assert fsops.tree_size_within(str(tmp_path), 30) == (15, True)
    assert fsops.tree_size_within(str(tmp_path), 0) == (0, False)
    assert fsops.tree_size(str(tmp_path)) == (15, 2)


# ---- visor de imágenes ----

@pytest.fixture
def image_folder(tree):
    left, _ = tree
    for name, size in (("uno.png", (400, 300)), ("dos.png", (40, 30)), ("tres.png", (300, 400))):
        image = QImage(*size, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        assert image.save(str(left / name))
    return left


@pytest.fixture
def viewer(window, image_folder):
    paths = [os.path.normpath(str(image_folder / n)) for n in ("uno.png", "dos.png", "tres.png")]
    shown = window._new_image_viewer(paths, 0)
    shown.show()
    assert wait_until(lambda: shown._shown is not None)
    settle()
    yield shown
    if _alive_viewer(window, shown):
        shown.close()


def _alive_viewer(window, shown):
    return shiboken6.isValid(shown)


def loaded_viewer(shown, name):
    return wait_until(lambda: os.path.basename(shown.current_path) == name and shown.status_labels["format"].text() != "")


def test_viewer_status_bar_shows_image_information(viewer):
    labels = viewer.status_labels
    assert labels["name"].text() == "uno.png" and labels["position"].text() == "1/3"
    assert labels["format"].text() == "400 x 300 x 24 Imagen PNG"
    assert labels["aspect"].text() == "4:3"
    assert labels["mode"].text().startswith("AJUSTAR (")
    assert labels["size"].text().endswith("B") and labels["time"].text().endswith("seg")
    assert viewer.current_path in viewer.windowTitle()


def test_viewer_toolbar_buttons_navigate(viewer):
    actions = viewer.actions_by_key
    actions["next"].trigger()
    assert loaded_viewer(viewer, "dos.png")
    assert viewer.status_labels["position"].text() == "2/3"
    actions["previous"].trigger()
    assert loaded_viewer(viewer, "uno.png")
    actions["last"].trigger()
    assert loaded_viewer(viewer, "tres.png")
    actions["first"].trigger()
    assert loaded_viewer(viewer, "uno.png")
    actions["previous"].trigger()  # da la vuelta
    assert loaded_viewer(viewer, "tres.png")


def test_viewer_zoom_actions_and_slider(viewer):
    actions = viewer.actions_by_key
    assert actions["fit_enlarge"].isChecked() and viewer.zoom is None
    actions["actual"].trigger()
    assert viewer.zoom == 1.0 and actions["actual"].isChecked() and not actions["fit_enlarge"].isChecked()
    assert viewer.status_labels["mode"].text() == "ZOOM (100%)"
    assert viewer.label.pixmap().width() == 400
    actions["zoom_in"].trigger()
    assert viewer.zoom == pytest.approx(1.25) and viewer.label.pixmap().width() == 500
    actions["zoom_out"].trigger()
    assert viewer.zoom == pytest.approx(1.0)
    assert slider_to_zoom(zoom_to_slider(1.0)) == pytest.approx(1.0, abs=0.01)
    viewer.zoom_slider.setValue(zoom_to_slider(2.0))
    assert viewer.zoom == pytest.approx(2.0, abs=0.02)
    actions["fit_enlarge"].trigger()
    assert viewer.zoom is None


def test_viewer_zoom_stays_within_limits(viewer):
    viewer.set_zoom(15.0)
    viewer.change_zoom(4)
    assert viewer.zoom == 16.0
    viewer.set_zoom(0.06)
    viewer.change_zoom(0.1)
    assert viewer.zoom == 0.05


def test_viewer_fit_modes_differ_for_small_images(viewer):
    viewer.actions_by_key["next"].trigger()
    assert loaded_viewer(viewer, "dos.png")
    settle()
    assert viewer.label.pixmap().width() > 40  # ampliar llena la ventana
    viewer.actions_by_key["fit_shrink"].trigger()
    assert viewer.label.pixmap().width() == 40  # reducir no agranda lo que ya cabe
    assert viewer.status_labels["mode"].text() == "REDUCIR (100%)"


def test_viewer_rotation_is_only_for_viewing(viewer, image_folder):
    before = (image_folder / "uno.png").read_bytes()
    viewer.set_zoom(1.0)
    viewer.actions_by_key["rotate_right"].trigger()
    assert viewer.label.pixmap().width() == 300 and viewer.label.pixmap().height() == 400
    viewer.actions_by_key["rotate_left"].trigger()
    assert viewer.label.pixmap().width() == 400 and viewer.rotation == 0
    viewer.actions_by_key["rotate_left"].trigger()
    assert viewer.rotation == 270
    assert (image_folder / "uno.png").read_bytes() == before
    viewer.actions_by_key["next"].trigger()
    assert loaded_viewer(viewer, "dos.png")
    assert viewer.rotation == 0


def send_wheel(target, delta, modifiers=Qt.KeyboardModifier.NoModifier):
    point = QPointF(target.rect().center())
    event = QWheelEvent(point, target.mapToGlobal(point), QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, modifiers,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QCoreApplication.sendEvent(target, event)


def test_viewer_wheel_changes_image_and_ctrl_wheel_zooms(viewer):
    port = viewer.scroll.viewport()
    send_wheel(port, -120)
    assert loaded_viewer(viewer, "dos.png")
    send_wheel(port, 120)
    assert loaded_viewer(viewer, "uno.png")
    scale = viewer.current_scale()
    send_wheel(port, 120, Qt.KeyboardModifier.ControlModifier)
    assert viewer.zoom == pytest.approx(scale * 1.25)


def test_viewer_wheel_scrolls_a_large_zoom(viewer):
    viewer.set_zoom(8.0)
    settle()
    send_wheel(viewer.scroll.viewport(), -120)
    assert os.path.basename(viewer.current_path) == "uno.png"


def test_viewer_drag_pans_a_large_zoom(viewer):
    viewer.set_zoom(8.0)
    settle()
    label = viewer.label
    assert label.cursor().shape() == Qt.CursorShape.OpenHandCursor
    bar = viewer.scroll.horizontalScrollBar()
    bar.setValue(bar.maximum() // 2)
    start = bar.value()

    def mouse(kind, x, buttons):
        point = QPointF(x, 50)
        button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
        QCoreApplication.sendEvent(label, QMouseEvent(kind, point, label.mapToGlobal(point), button, buttons, Qt.KeyboardModifier.NoModifier))

    mouse(QEvent.Type.MouseButtonPress, 200, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, 150, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseButtonRelease, 150, Qt.MouseButton.NoButton)
    assert bar.value() == start + 50


def test_viewer_fullscreen_hides_bars_and_esc_returns(viewer):
    viewer.actions_by_key["fullscreen"].trigger()
    assert viewer.isFullScreen() and viewer.toolbar.isHidden() and viewer.statusBar().isHidden() and viewer.menuBar().isHidden()
    QTest.keyClick(viewer, Qt.Key.Key_Escape)
    assert not viewer.isFullScreen() and viewer.isVisible() and not viewer.toolbar.isHidden()


def test_viewer_slideshow_button(viewer):
    action = viewer.actions_by_key["slideshow"]
    action.trigger()
    assert viewer.timer.isActive() and action.isChecked()
    action.trigger()
    assert not viewer.timer.isActive() and not action.isChecked()


def test_viewer_copy_image(viewer):
    viewer.actions_by_key["copy"].trigger()
    assert QApplication.clipboard().pixmap().width() == 400


def use_fake_trash(monkeypatch):
    removed = []

    def fake(path):
        removed.append(path)
        os.remove(path)
        return True

    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.move_to_trash", fake)
    return removed


def test_viewer_delete_asks_and_moves_to_next(viewer, monkeypatch):
    removed = use_fake_trash(monkeypatch)
    asked = []

    def answer(_parent, _title, question, *_a):
        asked.append(question)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.question", answer)
    path = viewer.current_path
    viewer.actions_by_key["delete"].trigger()
    assert removed == [path] and not os.path.exists(path)
    assert "papelera" in asked[0] and len(viewer.paths) == 2
    assert loaded_viewer(viewer, "dos.png")
    assert viewer.status_labels["position"].text() == "1/2"


def test_viewer_delete_can_be_cancelled(viewer, monkeypatch):
    removed = use_fake_trash(monkeypatch)
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.question", lambda *a: QMessageBox.StandardButton.No)
    path = viewer.current_path
    viewer.actions_by_key["delete"].trigger()
    assert not removed and os.path.exists(path) and len(viewer.paths) == 3


def test_viewer_delete_without_confirmation_if_turned_off(window, viewer, monkeypatch):
    removed = use_fake_trash(monkeypatch)
    window.config["confirm_delete"] = False
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.question", lambda *a: pytest.fail("no debe preguntar"))
    viewer.actions_by_key["delete"].trigger()
    assert len(removed) == 1


def test_viewer_delete_failure_keeps_the_image(viewer, monkeypatch):
    def refuse(_path):
        raise OSError("sin papelera")

    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.move_to_trash", refuse)
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.question", lambda *a: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.warning", lambda *a: warnings.append(a[2]))
    path = viewer.current_path
    viewer.actions_by_key["delete"].trigger()
    assert warnings and os.path.exists(path) and len(viewer.paths) == 3


def test_viewer_deleting_the_last_image_closes_it(window, image_folder, monkeypatch):
    use_fake_trash(monkeypatch)
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.question", lambda *a: QMessageBox.StandardButton.Yes)
    shown = window._new_image_viewer([os.path.normpath(str(image_folder / "uno.png"))], 0)
    shown.show()
    assert wait_until(lambda: shown._shown is not None)
    closed = []
    shown.destroyed.connect(lambda *_a: closed.append(True))
    shown.actions_by_key["delete"].trigger()
    assert wait_until(lambda: closed)


def test_viewer_rename_updates_title_and_undo(window, viewer, image_folder, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QInputDialog.getText", lambda *a: ("nuevo.png", True))
    viewer.actions_by_key["rename"].trigger()
    target = os.path.normpath(str(image_folder / "nuevo.png"))
    assert os.path.exists(target) and viewer.current_path == target
    assert viewer.status_labels["name"].text() == "nuevo.png" and target in viewer.windowTitle()
    assert window._undo[-1]["kind"] == "rename"


def test_viewer_rename_rejects_existing_name(viewer, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QInputDialog.getText", lambda *a: ("dos.png", True))
    warnings = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.warning", lambda *a: warnings.append(a[2]))
    path = viewer.current_path
    viewer.actions_by_key["rename"].trigger()
    assert warnings and viewer.current_path == path and os.path.exists(path)


def test_viewer_reveal_selects_the_file_in_the_list(window, viewer):
    other = os.path.dirname(os.path.dirname(viewer.current_path))
    window.source.navigate(other)
    viewer.actions_by_key["reveal"].trigger()
    view = window.source
    assert view.path == os.path.dirname(viewer.current_path)
    assert wait_until(lambda: [e.path for e in view.selected_entries()] == [viewer.current_path] or view.current_entry() is not None and view.current_entry().path == viewer.current_path)


def test_viewer_without_host_disables_file_actions(app, tmp_path):
    path = tmp_path / "a.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    shown = ImageViewer([str(path)], 0)
    for key in ("rename", "delete", "reveal"):
        assert not shown.actions_by_key[key].isEnabled()
    shown.delete_current()
    assert path.exists()
    shown.close()


def test_viewer_menus_include_every_function(viewer):
    menus = [a.menu() for a in viewer.menuBar().actions()]
    assert [m.title() for m in menus] == ["&Archivo", "&Editar", "&Ver"]
    listed = {a for m in menus for a in m.actions()}
    assert set(viewer.actions_by_key.values()) <= listed


def test_viewer_icons_exist():
    for name in ("zoomin", "zoomout", "zoomactual", "zoomshrink", "zoomfit", "rotateleft", "rotateright", "fullscreen"):
        assert has_icon(name)


@pytest.mark.parametrize("width,height,expected", [(4032, 3024, "4:3"), (1920, 1080, "16:9"), (988, 1304, "0.76:1"), (500, 500, "1:1"), (0, 10, "")])
def test_format_aspect(width, height, expected):
    assert format_aspect(width, height) == expected


def test_color_depth():
    assert color_depth(QImage(2, 2, QImage.Format.Format_RGB32)) == 24
    assert color_depth(QImage(2, 2, QImage.Format.Format_ARGB32)) == 32
    assert color_depth(QImage(2, 2, QImage.Format.Format_Grayscale8)) == 8


# ---- el desplegado no mueve la lista ----

@pytest.fixture
def long_list(window, tree):
    left, _ = tree
    for number in range(150):
        (left / f"archivo_{number:03}.txt").write_text("", encoding="utf-8")
    for number in range(150):
        (left / "subcarpeta" / f"dentro_{number:03}.txt").write_text("", encoding="utf-8")
    view = window.source
    view.set_view_mode("list")
    window.run_command("Go REFRESH")
    assert wait_until(lambda: view.model.rowCount() == 153)
    wait_for_layout(view.thumbs)
    return view


def wait_for_layout(items):
    items.finish_layout()
    settle()


def expand(view, name, expanded=True):
    model = view.model
    rows = model.rowCount()
    model.set_expanded(model.entry(row_of(view, name)), expanded)
    assert wait_until(lambda: model.rowCount() != rows)
    settle()


def test_expanding_does_not_jump_to_the_current_file(long_list):
    view = long_list
    items = view.thumbs
    model = view.model
    assert items.horizontalScrollBar().maximum() > 0
    last = model.index(model.rowCount() - 1, 0)
    items.setCurrentIndex(model.index(0, 0))  # el archivo actual está al principio
    bar = items.horizontalScrollBar()
    bar.setValue(bar.maximum())
    before = bar.value()
    assert before > 0 and last.isValid()
    expand(view, "subcarpeta")
    wait_for_layout(items)
    assert bar.value() == before


def test_expanding_from_the_start_stays_at_the_start_with_last_file_current(long_list):
    view = long_list
    items = view.thumbs
    model = view.model
    items.setCurrentIndex(model.index(model.rowCount() - 1, 0))
    bar = items.horizontalScrollBar()
    bar.setValue(0)
    expand(view, "subcarpeta")
    wait_for_layout(items)
    assert bar.value() == 0


def test_expanding_keeps_selection_and_current_file(long_list):
    view = long_list
    model = view.model
    view.select_paths([model.entry(row_of(view, "a.txt")).path])
    view.thumbs.setCurrentIndex(model.index(row_of(view, "a.txt"), 0))
    expand(view, "subcarpeta")
    assert [e.name for e in view.selected_entries()] == ["a.txt"]
    assert view.current_entry().name == "a.txt"


def test_collapsing_does_not_jump_either(long_list):
    view = long_list
    items = view.thumbs
    expand(view, "subcarpeta")
    wait_for_layout(items)
    items.setCurrentIndex(view.model.index(view.model.rowCount() - 1, 0))
    bar = items.horizontalScrollBar()
    bar.setValue(0)
    expand(view, "subcarpeta", False)
    wait_for_layout(items)
    assert bar.value() == 0


def test_view_still_scrolls_to_a_current_file_set_by_keyboard(long_list):
    items = long_list.thumbs
    model = long_list.model
    items.setCurrentIndex(model.index(model.rowCount() - 1, 0))
    assert items.hasAutoScroll()
    assert items.viewport().rect().intersects(items.visualRect(model.index(model.rowCount() - 1, 0)))


def test_opening_another_folder_starts_at_the_beginning(window, long_list, tree):
    view = long_list
    _left, right = tree
    bar = view.thumbs.horizontalScrollBar()
    bar.setValue(bar.maximum())
    view.navigate(str(right))
    assert wait_until(lambda: view.model.rowCount() == 0)
    settle()
    assert bar.value() == 0


def test_details_keeps_its_vertical_position_when_expanding(window, tree):
    left, _ = tree
    for number in range(150):
        (left / f"archivo_{number:03}.txt").write_text("", encoding="utf-8")
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: view.model.rowCount() == 153)
    settle()
    bar = view.details.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    before = bar.value()
    view.details.setCurrentIndex(view.model.index(view.model.rowCount() - 1, 0))
    bar.setValue(before)
    expand(view, "subcarpeta")
    assert bar.value() == before


# ---- la imagen que se ve se marca en su panel ----

def test_viewer_marks_the_current_image_in_its_pane(window, image_folder):
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "uno.png" in names(view))
    select(view, "dos.png")
    window.run_command("Show")
    viewer = window._viewers[-1]
    assert viewer.origin is view
    first = viewer.current_path
    assert [e.path for e in view.selected_entries()] == [first]
    viewer.actions_by_key["next"].trigger()
    assert [e.path for e in view.selected_entries()] == [viewer.current_path] != [first]
    assert view.current_entry().path == viewer.current_path
    viewer.actions_by_key["previous"].trigger()
    assert [e.path for e in view.selected_entries()] == [first]
    viewer.close()


def test_viewer_marks_the_image_opened_with_double_click(window, image_folder):
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "uno.png" in names(view))
    window._open_file(view, entry_from_path(os.path.normpath(str(image_folder / "uno.png"))))
    viewer = window._viewers[-1]
    assert viewer.origin is view
    viewer.step(1)
    assert [e.path for e in view.selected_entries()] == [viewer.current_path]
    viewer.close()


def test_viewer_scrolls_the_list_to_the_marked_image(window, tree):
    left, _ = tree
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for number in range(200):
        (left / f"relleno_{number:03}.txt").write_text("", encoding="utf-8")
    paths = []
    for name in ("a_imagen.png", "z_imagen.png"):
        assert image.save(str(left / name))
        paths.append(os.path.normpath(str(left / name)))
    view = window.source
    view.set_view_mode("list")
    window.run_command("Go REFRESH")
    assert wait_until(lambda: view.model.rowCount() == 205)
    viewer = window._new_image_viewer(paths, 0, view)
    viewer.step(1)
    items = view.thumbs
    index = view.model.index(view.model.row_of(paths[1]), 0)
    assert items.viewport().rect().intersects(items.visualRect(index))
    viewer.close()


def test_viewer_does_not_touch_a_pane_that_moved_elsewhere(window, image_folder, tree):
    _left, right = tree
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "uno.png" in names(view))
    select(view, "uno.png")
    window.run_command("Show")
    viewer = window._viewers[-1]
    view.navigate(str(right))
    assert wait_until(lambda: view.model.rowCount() == 0)
    viewer.step(1)
    assert view.selected_entries() == []
    viewer.close()


def test_viewer_survives_its_pane_being_closed(window, image_folder, tmp_path):
    group = window.groups[0]
    tab = group.add_tab(os.path.normpath(str(image_folder)))
    assert wait_until(lambda: "uno.png" in names(tab))
    viewer = window._new_image_viewer([os.path.normpath(str(image_folder / n)) for n in ("uno.png", "dos.png")], 0, tab)
    group.close_tab(group.indexOf(tab))
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle()
    viewer.step(1)
    assert viewer.index == 1
    viewer.close()


def test_viewer_marks_a_renamed_image_when_the_list_reloads(window, image_folder, monkeypatch):
    view = window.source
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "uno.png" in names(view))
    select(view, "uno.png")
    window.run_command("Show")
    viewer = window._viewers[-1]
    assert wait_until(lambda: viewer._shown is not None)
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QInputDialog.getText", lambda *a: ("nuevo.png", True))
    viewer.actions_by_key["rename"].trigger()
    target = os.path.normpath(str(image_folder / "nuevo.png"))
    assert wait_until(lambda: [e.path for e in view.selected_entries()] == [target])
    viewer.close()


# ---- mostrar la carpeta en el otro panel ----

def test_send_button_shows_this_folder_in_the_other_pane(window, tree):
    left, right = tree
    window.set_dual("Vertical")
    first, second = window.groups[0].current, window.groups[1].current
    assert first.path == os.path.normpath(str(left)) and second.path == os.path.normpath(str(right))
    second.send_button.click()  # el botón de un panel envía su carpeta, aunque ese panel no sea el activo
    assert wait_until(lambda: first.path == os.path.normpath(str(right)))
    assert second.path == os.path.normpath(str(right))


def test_send_button_is_next_to_swap_and_says_what_it_does(window):
    view = window.source
    bar = view.pane_bar.layout()
    widgets = [bar.itemAt(i).widget() for i in range(bar.count())]
    position = widgets.index(view.send_button)
    assert widgets[position + 1].toolTip() == "Intercambiar paneles"
    assert view.send_button.toolTip() == "Mostrar esta carpeta en el otro visualizador de archivos"


def test_send_button_hidden_with_a_single_pane(window):
    window.set_dual("Off")
    settle()
    assert window.source.send_button.isHidden()
    window.set_dual("Vertical")
    settle()
    assert not window.source.send_button.isHidden()


@pytest.mark.parametrize("mode,first,second", [("Vertical", "sendright", "sendleft"), ("Horizontal", "senddown", "sendup")])
def test_send_button_points_to_the_other_pane(window, mode, first, second):
    window.set_dual(mode)
    icons = [dict((b, i) for b, i in g.current._tool_buttons)[g.current.send_button] for g in window.groups[:2]]
    assert icons == [first, second]


def test_send_button_survives_theme_change_with_its_direction(window):
    window.set_dual("Vertical")
    view = window.groups[1].current
    window.apply_theme()
    assert dict(view._tool_buttons)[view.send_button] == "sendleft" and has_icon("sendleft")


# ---- controles de vídeo ----

@pytest.fixture
def player(media_window):
    win = media_window
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, "tono.wav"), 3))))
    assert wait_until(lambda: loaded(win.preview))
    return win, win.preview.media


def test_controls_are_in_one_row_in_order(player):
    _win, media = player
    row = media.controls.layout()
    widgets = [row.itemAt(i).widget() for i in range(row.count())]
    assert widgets == [media.play_button, media.stop_button, media.repeat_button, media.seek, media.auto_button, media.mute_button, media.volume]
    assert media.auto_button.text() == "Auto"
    assert media.layout().indexOf(media.seek) < 0


def test_stop_button_returns_to_start_and_keeps_the_file(player):
    _win, media = player
    media.player.play()
    media.player.setPosition(1500)
    assert wait_until(lambda: media.player.position() >= 1000)
    media.stop_button.click()
    assert wait_until(lambda: media.player.position() == 0)
    assert media.player.playbackState() == media.player.PlaybackState.StoppedState and media.has_source
    assert media.seek.value() == 0
    media.play_button.click()
    assert media.player.playbackState() == media.player.PlaybackState.PlayingState


def test_stop_without_a_file_does_nothing(player):
    _win, media = player
    media.release()
    media.stop_button.click()
    assert not media.has_source


def test_repeat_button_loops_and_is_remembered(player):
    win, media = player
    assert not media.repeat_button.isChecked() and media.player.loops() == 1
    media.repeat_button.click()
    assert win.config["viewer_repeat"] is True and media.player.loops() < 0
    media.repeat_button.click()
    assert win.config["viewer_repeat"] is False and media.player.loops() == 1


def test_repeat_setting_applies_to_the_next_file(player):
    win, media = player
    win.config["viewer_repeat"] = True
    win.preview.show_entry(entry_from_path(str(write_wav(os.path.join(win.source.path, "otro.wav")))))
    assert wait_until(lambda: media.player.source().toLocalFile().endswith("otro.wav"))
    assert media.repeat_button.isChecked() and media.player.loops() < 0


def test_seek_bar_writes_elapsed_and_remaining(app):
    bar = SeekSlider(Qt.Orientation.Horizontal)
    bar.setRange(0, 74_000)
    bar.setValue(18_000)
    assert bar.elapsed_text() == "00:18" and bar.remaining_text() == "-00:56 / 01:14"
    bar.resize(400, 22)
    assert not bar.grab().isNull()
    bar.resize(20, 22)  # sin sitio para el texto no debe fallar
    assert not bar.grab().isNull()


def test_seek_bar_drag_follows_the_mouse(app):
    bar = SeekSlider(Qt.Orientation.Horizontal)
    bar.setRange(0, 1000)
    bar.resize(200, 22)
    moved = []
    bar.sliderMoved.connect(moved.append)

    def mouse(kind, x, button, buttons):
        point = QPointF(x, 10)
        QCoreApplication.sendEvent(bar, QMouseEvent(kind, point, bar.mapToGlobal(point), button, buttons, Qt.KeyboardModifier.NoModifier))

    mouse(QEvent.Type.MouseButtonPress, 50, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert bar.isSliderDown() and bar.value() == pytest.approx(250, abs=5)
    mouse(QEvent.Type.MouseMove, 150, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert bar.value() == pytest.approx(750, abs=5) and len(moved) == 2
    mouse(QEvent.Type.MouseButtonRelease, 150, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    assert not bar.isSliderDown()


def test_seek_bar_is_ignored_while_the_user_drags_it(player):
    _win, media = player
    media.seek.setSliderDown(True)
    media.seek.setValue(1000)
    media._position_changed(0)
    assert media.seek.value() == 1000
    media.seek.setSliderDown(False)


def test_volume_wedge_click_sets_volume(player):
    win, media = player
    wedge = media.volume
    point = QPointF(wedge.width() / 2, wedge.height() / 2)
    QCoreApplication.sendEvent(wedge, QMouseEvent(QEvent.Type.MouseButtonPress, point, wedge.mapToGlobal(point), Qt.MouseButton.LeftButton,
                                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert win.config["viewer_volume"] == pytest.approx(50, abs=3)
    assert not wedge.grab().isNull()


def test_player_icons_exist():
    for name in ("mediastop", "mediarepeat", "sendright", "sendleft", "senddown", "sendup"):
        assert has_icon(name)


# ---- la barra de botones no fija el ancho del panel ----

def shown_tools(view):
    return {icon for button, icon in view._tool_buttons if not button.isHidden()}


def test_pane_bar_does_not_set_the_pane_minimum_width(app):
    view = FileView()
    view.resize(150, 300)
    view.show()
    settle()
    assert view.width() == 150
    assert view.pane_bar.parentWidget().width() == 150  # el marco que dibuja su contorno
    view.close()


def test_pane_bar_hides_buttons_by_priority(app):
    view = FileView()
    view.show()
    view.resize(1000, 300)
    settle()
    send = dict(view._tool_buttons)[view.send_button]
    every = {icon for _button, icon in view._tool_buttons}
    assert shown_tools(view) == every
    view.resize(260, 300)
    settle()
    narrow = shown_tools(view)
    assert "goup" in narrow and "goback" in narrow and "favorites" not in narrow
    view.resize(120, 300)
    settle()
    assert shown_tools(view) <= {"goup"} | {"goback", "goforward", send}
    view.resize(1000, 300)
    settle()
    assert shown_tools(view) == every
    view.close()


def test_pane_bar_buttons_fit_inside_the_bar(app):
    view = FileView()
    view.show()
    for width in (900, 500, 340, 260, 200, 150):
        view.resize(width, 300)
        settle()
        layout = view.pane_bar.layout()
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and not widget.isHidden():
                assert widget.geometry().right() < view.pane_bar.width() + 1, (width, index)
    view.close()


def test_dual_tools_stay_hidden_with_one_pane_even_with_room(window):
    window.set_dual("Off")
    settle()
    view = window.source
    view.resize(1200, 400)
    view._fit_pane_bar()
    icons = dict((icon, button) for button, icon in view._tool_buttons)
    assert view.send_button.isHidden() and icons["swap"].isHidden() and icons["close"].isHidden()
    assert not icons["goup"].isHidden()
    window.set_dual("Vertical")
    settle()


# ---- opciones del sistema y de otros programas en los menús ----

class FakeShell:
    """Hace de menú de Windows: en estas pruebas no se carga ni se ejecuta ningún programa."""

    def __init__(self, items=None, children=None, invoke_error=None) -> None:
        self._items = items if items is not None else [ShellItem(label="&Otro programa", command=7, verb="otro")]
        self._children = children or {}
        self.invoke_error = invoke_error
        self.invoked = []
        self.prepared = []
        self.closed = False
        self.is_open = True

    def items(self):
        return self._items

    def read_menu(self, hmenu):
        return self._children.get(hmenu, [])

    def prepare_submenu(self, hmenu, position=0):
        self.prepared.append((hmenu, position))

    def invoke(self, command):
        if self.invoke_error:
            raise OSError(self.invoke_error)
        self.invoked.append(command)
        return True

    def close(self):
        self.closed = True
        self.is_open = False


def menu_with_end():
    menu = QMenu()
    menu.addAction("Propia")
    menu.addSeparator()
    menu.addAction("Propiedades")
    return menu


def test_clean_items_removes_repeats_and_empty_groups():
    items = [ShellItem(separator=True), ShellItem(label="Cortar", verb="cut"), ShellItem(separator=True), ShellItem(label="Git", verb="git_gui"),
             ShellItem(separator=True), ShellItem(separator=True), ShellItem(label="", verb="dibujada"), ShellItem(label="COPIAR", verb="COPY"),
             ShellItem(label="Nuevo", submenu=5), ShellItem(separator=True), ShellItem(label="Propiedades", verb="properties"), ShellItem(separator=True)]
    kept = clean_items(items)
    assert [(i.label, i.separator) for i in kept] == [("Git", False), ("", True), ("Nuevo", False)]


def test_clean_items_keeps_everything_in_submenus():
    assert [i.label for i in clean_items([ShellItem(label="Copiar", verb="copy")], own_verbs=())] == ["Copiar"]


def test_shell_items_go_before_the_last_group():
    menu = menu_with_end()
    add_shell_items(menu, FakeShell())
    labels_ = [("-" if a.isSeparator() else a.text()) for a in menu.actions()]
    assert labels_ == ["Propia", "-", "&Otro programa", "-", "Propiedades"]


def test_shell_item_runs_its_command():
    menu = menu_with_end()
    shell = FakeShell()
    add_shell_items(menu, shell)
    next(a for a in menu.actions() if "Otro" in a.text()).trigger()
    assert shell.invoked == [7]


def test_shell_item_keeps_state():
    menu = menu_with_end()
    add_shell_items(menu, FakeShell([ShellItem(label="Apagada", enabled=False, command=1), ShellItem(label="Marcada", checked=True, command=2)]))
    by_text = {a.text(): a for a in menu.actions()}
    assert not by_text["Apagada"].isEnabled() and by_text["Marcada"].isChecked()


def test_failing_program_does_not_break_the_menu():
    menu = menu_with_end()
    errors = []
    add_shell_items(menu, FakeShell(invoke_error="fallo del programa"), errors.append)
    next(a for a in menu.actions() if "Otro" in a.text()).trigger()
    assert errors == ["fallo del programa"]


def test_nothing_is_added_without_items():
    menu = menu_with_end()
    add_shell_items(menu, FakeShell([ShellItem(label="Cortar", verb="cut")]))
    assert len(menu.actions()) == 3


def test_menu_without_separators_gets_the_items_at_the_end():
    menu = QMenu()
    menu.addAction("Propia")
    add_shell_items(menu, FakeShell())
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ["Propia", "&Otro programa"]


def test_submenu_is_read_only_when_opened_and_only_once():
    shell = FakeShell([ShellItem(label="&Nuevo", submenu=50, position=3)], {50: [ShellItem(label="Carpeta", command=9), ShellItem(separator=True), ShellItem(label="Copiar", command=10, verb="copy")]})
    menu = menu_with_end()
    add_shell_items(menu, shell)
    submenu = next(a.menu() for a in menu.actions() if a.menu())
    assert shell.prepared == [] and submenu.title() == "&Nuevo"
    submenu.aboutToShow.emit()
    assert shell.prepared == [(50, 3)]
    assert [a.text() for a in submenu.actions() if not a.isSeparator()] == ["Carpeta", "Copiar"]  # dentro de un submenú no se quita nada
    submenu.aboutToShow.emit()
    assert shell.prepared == [(50, 3)]
    next(a for a in submenu.actions() if a.text() == "Carpeta").trigger()
    assert shell.invoked == [9]


def test_empty_submenu_says_so():
    shell = FakeShell([ShellItem(label="Vacío", submenu=50)])
    menu = menu_with_end()
    add_shell_items(menu, shell)
    submenu = next(a.menu() for a in menu.actions() if a.menu())
    submenu.aboutToShow.emit()
    only = submenu.actions()
    assert [a.text() for a in only] == ["(vacío)"] and not only[0].isEnabled()


def test_submenu_is_not_read_after_closing_the_menu():
    shell = FakeShell([ShellItem(label="Nuevo", submenu=50)])
    menu = menu_with_end()
    add_shell_items(menu, shell)
    submenu = next(a.menu() for a in menu.actions() if a.menu())
    shell.close()
    submenu.aboutToShow.emit()
    assert shell.prepared == []


@pytest.fixture
def menu_shown(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "_run_menu", lambda menu, _position: shown.append([a.text() for a in menu.actions()]))
    return shown


def test_folder_menu_includes_other_programs(window, menu_shown, monkeypatch):
    shell = FakeShell()
    calls = []

    def create(path, hwnd):
        calls.append((path, hwnd))
        return shell

    monkeypatch.setattr(windows_shell, "menu_for_folder", create)
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert calls == [(window.source.path, int(window.winId()))]
    assert "&Otro programa" in menu_shown[0] and shell.closed


def test_file_menu_includes_other_programs_for_the_selection(window, menu_shown, monkeypatch):
    shell = FakeShell()
    calls = []
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda paths, hwnd: calls.append(paths) or shell)
    select(window.source, "a.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert calls == [[window.source.selected_entries()[0].path]]
    assert "&Otro programa" in menu_shown[0] and shell.closed


def test_preference_turns_other_programs_off(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: pytest.fail("no debe cargar extensiones"))
    window.config["shell_menu_items"] = False
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert menu_shown


def test_shell_failure_still_shows_own_menu(window, menu_shown, monkeypatch):
    def fail(*_a):
        raise OSError("Windows no pudo")

    monkeypatch.setattr(windows_shell, "menu_for_folder", fail)
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert menu_shown and "Nueva carpeta" in menu_shown[0]


def test_shell_menu_is_closed_if_showing_fails(window, monkeypatch):
    shell = FakeShell()
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: shell)

    def explode(*_a):
        raise RuntimeError("fallo")

    monkeypatch.setattr(window, "_run_menu", explode)
    with pytest.raises(RuntimeError):
        window._show_folder_menu(window.source, QPoint(1, 1))
    assert shell.closed


def test_computer_view_and_archives_have_no_shell_menu(window, menu_shown, monkeypatch, tmp_path):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: pytest.fail("no aplica"))
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: pytest.fail("no aplica"))
    archive = tmp_path / "c.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("dentro.txt", "x")
    view = window.source
    view.navigate(str(archive))
    assert wait_until(lambda: "dentro.txt" in names(view))
    window._show_folder_menu(view, QPoint(1, 1))
    select(view, "dentro.txt")
    window._show_file_menu(view, QPoint(1, 1))
    assert len(menu_shown) == 2
    if has_computer_view():
        view.navigate(COMPUTER_PATH)
        window._show_folder_menu(view, QPoint(1, 1))
        assert len(menu_shown) == 3


@pytest.fixture
def zip_in_left(window, tree):
    left, _ = tree
    path = left / "datos.zip"
    with zipfile.ZipFile(path, "w") as out:
        out.writestr("dentro.txt", "x")
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "datos.zip" in names(window.source))
    return path


def test_selected_zip_gets_the_options_of_other_programs(window, zip_in_left, menu_shown, monkeypatch):
    calls = []
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda paths, hwnd: calls.append(paths) or FakeShell())
    select(window.source, "datos.zip")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert calls == [[os.path.normpath(str(zip_in_left))]]
    assert "&Otro programa" in menu_shown[0]


def test_zip_opens_with_the_system_by_default(window, zip_in_left, monkeypatch):
    opened = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    view = window.source
    folder = view.path
    view.open_entry(entry_from_path(str(zip_in_left)))
    assert [os.path.normpath(p) for p in opened] == [os.path.normpath(str(zip_in_left))]
    assert view.path == folder


def test_zip_can_be_set_to_open_as_a_folder(window, zip_in_left, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: pytest.fail("no debe abrir un programa"))
    window.config["open_archives"] = "browse"
    window.source.open_entry(entry_from_path(str(zip_in_left)))
    assert wait_until(lambda: "dentro.txt" in names(window.source))


def test_zip_without_a_program_is_browsed(window, zip_in_left, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: False)
    window.source.open_entry(entry_from_path(str(zip_in_left)))
    assert wait_until(lambda: "dentro.txt" in names(window.source))


def test_menu_can_browse_a_zip(window, zip_in_left):
    select(window.source, "datos.zip")
    menu = window.build_file_menu(window.source)
    next(a for a in menu.actions() if a.text() == context_menu.BROWSE_ARCHIVE).trigger()
    assert wait_until(lambda: "dentro.txt" in names(window.source))


def test_commands_treat_a_selected_zip_as_a_normal_file(window, zip_in_left, tree):
    _, right = tree
    select(window.source, "datos.zip")
    window._ask_text = lambda *_a: "paquete"
    window.run_command("Archive")
    assert wait_until(lambda: (right / "paquete.zip").exists())
    assert fsops.make_link(str(zip_in_left), str(right), "hardlink")


def test_open_archives_preference(app, tmp_path):
    config = {}
    dialog = PreferencesDialog(config)
    assert dialog.open_archives.currentData() == "system"
    dialog.open_archives.setCurrentIndex(dialog.open_archives.findData("browse"))
    dialog.apply_to(config)
    assert config["open_archives"] == "browse"
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"open_archives": "otro"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({}, ["open_archives"])


OPEN_WITH_ITEM = ShellItem(label="A&brir con", verb="openas", submenu=60, command=8, position=2)


def actions_of(menu):
    return [a.text() for a in menu.actions()]


def test_open_with_of_the_system_goes_after_open(window, menu_shown, monkeypatch):
    shell = FakeShell([ShellItem(label="Otro", command=7, verb="otro"), OPEN_WITH_ITEM],
                      {60: [ShellItem(label="WinRAR", command=9)]})
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: shell)

    def run(menu, _position):
        texts = actions_of(menu)
        assert texts[:2] == ["Abrir", "A&brir con"] and texts.count("A&brir con") == 1 and context_menu.OPEN_WITH not in texts
        assert "Otro" in texts[2:]
        submenu = next(a.menu() for a in menu.actions() if a.text() == "A&brir con")
        submenu.aboutToShow.emit()
        assert shell.prepared == [(60, 2)]
        next(a for a in submenu.actions() if a.text() == "WinRAR").trigger()

    monkeypatch.setattr(window, "_run_menu", run)
    select(window.source, "a.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert shell.invoked == [9]


def test_open_with_stays_if_the_system_does_not_offer_it(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: FakeShell())
    select(window.source, "a.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert menu_shown[0][:2] == ["Abrir", context_menu.OPEN_WITH]


def test_open_with_can_be_hidden(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: FakeShell([ShellItem(label="Otro", command=7, verb="otro"), OPEN_WITH_ITEM]))
    set_layout(window, "file", hidden=[context_menu.OPEN_WITH])
    select(window.source, "a.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert "A&brir con" not in menu_shown[0] and context_menu.OPEN_WITH not in menu_shown[0] and "Otro" in menu_shown[0]


def test_open_with_only_for_one_file(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: None)
    select(window.source, "a.txt", "b.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert context_menu.OPEN_WITH not in menu_shown[0]
    select(window.source, "subcarpeta")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert context_menu.OPEN_WITH not in menu_shown[1]


def test_own_open_with_shows_the_windows_dialog(window, monkeypatch):
    calls = []
    monkeypatch.setattr(windows_shell, "AVAILABLE", True)
    monkeypatch.setattr(windows_shell, "open_with_dialog", lambda path, hwnd, *a: calls.append(path), raising=False)
    select(window.source, "a.txt")
    menu = window.build_file_menu(window.source)
    next(a for a in menu.actions() if a.text() == context_menu.OPEN_WITH).trigger()
    assert calls == [window.source.selected_entries()[0].path]


def test_open_with_on_linux_lists_the_programs(window, monkeypatch):
    monkeypatch.setattr(windows_shell, "AVAILABLE", False)
    app_ = desktop_apps.DesktopApp("Editor", "editor --nuevo %f")
    monkeypatch.setattr(desktop_apps, "apps_for", lambda _types: [app_])
    launched = []
    monkeypatch.setattr(platform_utils, "run_program", lambda args, cwd: launched.append((args, cwd)))
    select(window.source, "a.txt")
    entry_path = window.source.selected_entries()[0].path
    menu = window.build_file_menu(window.source)
    submenu = next(a.menu() for a in menu.actions() if a.text() == context_menu.OPEN_WITH)
    submenu.aboutToShow.emit()
    assert actions_of(submenu)[0] == "Editor" and actions_of(submenu)[-1] == "Otro programa…"
    submenu.actions()[0].trigger()
    assert launched == [(["editor", "--nuevo", entry_path], os.path.dirname(entry_path))]


def test_preferences_do_not_list_open_with_among_other_programs(app, monkeypatch):
    menu = FakeShell([ShellItem(label="Otro", command=7, verb="otro"), OPEN_WITH_ITEM])
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: menu)
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: menu)
    dialog = PreferencesDialog({}, None, "carpeta", ["a.txt"])
    dialog.menus.load_shell_options()
    listed = [dialog.menus.shell_list.item(row).text() for row in range(dialog.menus.shell_list.count())]
    assert listed == ["Otro"]


def test_preferences_shell_option_is_saved(app):
    config = {}
    dialog = PreferencesDialog(config)
    assert dialog.shell_items.isChecked()
    dialog.shell_items.setChecked(False)
    dialog.apply_to(config)
    assert config["shell_menu_items"] is False


@pytest.fixture
def crop_viewer(window, tmp_path):
    """Imagen de 400 x 300: mitad izquierda roja y mitad derecha azul."""
    folder = tmp_path / "recortes"
    folder.mkdir()
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for x in range(200, 400):
        for y in range(300):
            image.setPixelColor(x, y, QColor("blue"))
    path = folder / "foto.png"
    assert image.save(str(path))
    other = folder / "otra.png"
    assert image.save(str(other))
    shown = window._new_image_viewer([os.path.normpath(str(path)), os.path.normpath(str(other))], 0)
    shown.show()
    assert wait_until(lambda: shown._shown is not None)
    settle()
    yield shown
    if shiboken6.isValid(shown):
        shown.close()


def screen_point(viewer_, x, y):
    """Punto de la pantalla para un punto de la imagen."""
    area = viewer_.crop.image_on_screen()
    return QPoint(round(area.x() + x * area.width() / 400), round(area.y() + y * area.height() / 300))


def drag_selection(viewer_, start, end):
    overlay = viewer_.crop
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(overlay, end)
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=end)


def near(area, x, y, width, height, tolerance=4):
    return all(abs(a - b) <= tolerance for a, b in ((area.x(), x), (area.y(), y), (area.width(), width), (area.height(), height)))


def test_crop_starts_and_ends_with_its_button(crop_viewer):
    action = crop_viewer.actions_by_key["crop"]
    assert not crop_viewer.crop.isVisible() and not crop_viewer.actions_by_key["save_crop"].isEnabled()
    action.trigger()
    assert crop_viewer.crop.isVisible() and action.isChecked()
    assert crop_viewer.crop.geometry() == crop_viewer.label.rect()
    assert crop_viewer.actions_by_key["save_crop"].isVisible() and not crop_viewer.actions_by_key["delete"].isVisible()
    action.trigger()
    assert not crop_viewer.crop.isVisible() and not action.isChecked()
    assert not crop_viewer.actions_by_key["save_crop"].isVisible() and crop_viewer.actions_by_key["delete"].isVisible()


def test_dragging_selects_in_image_pixels(crop_viewer):
    crop_viewer.set_cropping(True)
    drag_selection(crop_viewer, screen_point(crop_viewer, 100, 50), screen_point(crop_viewer, 300, 200))
    area = crop_viewer.crop.selection
    assert near(area, 100, 50, 200, 150)
    assert crop_viewer.actions_by_key["save_crop"].isEnabled()
    assert crop_viewer.status_labels["selection"].text() == f"Selección: {area.width()} x {area.height()}"


def test_selection_keeps_its_pixels_when_zooming(crop_viewer):
    crop_viewer.set_cropping(True)
    drag_selection(crop_viewer, screen_point(crop_viewer, 100, 50), screen_point(crop_viewer, 300, 200))
    before = QRect(crop_viewer.crop.selection)
    crop_viewer.set_zoom(2.0)
    assert crop_viewer.crop.selection == before
    assert crop_viewer.crop.geometry() == crop_viewer.label.rect()
    on_screen = crop_viewer.crop.selection_on_screen()
    assert abs(on_screen.width() - before.width() * 2) <= 1


def test_dragging_backwards_and_past_the_edge_stays_inside_the_image(crop_viewer):
    crop_viewer.set_cropping(True)
    drag_selection(crop_viewer, screen_point(crop_viewer, 100, 100), QPoint(1, 1))  # QTest toma (0, 0) como el centro
    assert near(crop_viewer.crop.selection, 0, 0, 100, 100)


def test_selection_can_be_moved_without_leaving_the_image(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(100, 100, 100, 100))
    drag_selection(crop_viewer, screen_point(crop_viewer, 150, 150), screen_point(crop_viewer, 170, 160))
    assert near(crop_viewer.crop.selection, 120, 110, 100, 100)
    assert crop_viewer.crop.selection.size() == QSize(100, 100)
    drag_selection(crop_viewer, screen_point(crop_viewer, 170, 160), crop_viewer.crop.rect().bottomRight())
    moved = crop_viewer.crop.selection
    assert moved.x() + moved.width() <= 400 and moved.y() + moved.height() <= 300 and moved.size() == QSize(100, 100)


def test_selection_can_be_resized_by_a_corner_and_a_side(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(100, 100, 100, 100))
    drag_selection(crop_viewer, screen_point(crop_viewer, 200, 200), screen_point(crop_viewer, 300, 250))
    assert near(crop_viewer.crop.selection, 100, 100, 200, 150)
    crop_viewer.crop.set_selection(QRect(100, 100, 100, 100))
    drag_selection(crop_viewer, screen_point(crop_viewer, 100, 150), screen_point(crop_viewer, 50, 170))
    assert near(crop_viewer.crop.selection, 50, 100, 150, 100)


def test_click_outside_clears_the_selection(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(100, 100, 50, 50))
    point = screen_point(crop_viewer, 300, 250)
    drag_selection(crop_viewer, point, point)
    assert crop_viewer.crop.selection.isEmpty() and not crop_viewer.actions_by_key["save_crop"].isEnabled()


def choose_target(monkeypatch, path):
    asked = []

    def dialog(_parent, _title, suggested, _filter):
        asked.append(suggested)
        return str(path), ""

    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QFileDialog.getSaveFileName", dialog)
    return asked


def test_saving_writes_a_new_image_and_leaves_the_original(crop_viewer, monkeypatch):
    source = crop_viewer.current_path
    before = open(source, "rb").read()
    target = os.path.join(os.path.dirname(source), "foto_recorte.png")
    asked = choose_target(monkeypatch, target)
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(190, 10, 20, 30))
    crop_viewer.save_crop()
    assert asked == [target]
    assert wait_until(lambda: not crop_viewer.crop.isVisible())
    saved = QImage(target)
    assert (saved.width(), saved.height()) == (20, 30)
    assert saved.pixelColor(0, 0) == QColor("red") and saved.pixelColor(19, 0) == QColor("blue")
    assert open(source, "rb").read() == before
    assert crop_viewer.current_path == source
    assert crop_viewer.paths[1] == target and crop_viewer.status_labels["position"].text() == "1/3"


def test_suggested_name_does_not_overwrite(crop_viewer, monkeypatch):
    source = crop_viewer.current_path
    taken = os.path.join(os.path.dirname(source), "foto_recorte.png")
    open(taken, "wb").close()
    suggested = image_ops.crop_name(source)
    assert suggested != taken and os.path.dirname(suggested) == os.path.dirname(source) and suggested.endswith(".png")


def test_suggested_name_of_an_unwritable_format_is_png(tmp_path):
    assert image_ops.crop_name(str(tmp_path / "x.formatoraro")).endswith("x_recorte.png")


def test_crop_saves_what_is_seen_after_rotating(crop_viewer, monkeypatch):
    target = os.path.join(os.path.dirname(crop_viewer.current_path), "girada.png")
    choose_target(monkeypatch, target)
    crop_viewer.set_cropping(True)
    crop_viewer.rotate(90)
    assert crop_viewer.crop.isVisible() and crop_viewer.crop.image_size == QSize(300, 400)
    crop_viewer.crop.set_selection(QRect(10, 10, 280, 100))  # girada a la derecha, la mitad roja queda arriba
    crop_viewer.save_crop()
    assert wait_until(lambda: not crop_viewer.crop.isVisible())
    saved = QImage(target)
    assert (saved.width(), saved.height()) == (280, 100) and saved.pixelColor(10, 10) == QColor("red")


def test_crop_to_jpeg_flattens_and_uses_the_extension(crop_viewer, monkeypatch):
    target = os.path.join(os.path.dirname(crop_viewer.current_path), "corte.jpg")
    choose_target(monkeypatch, target)
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 50))
    crop_viewer.save_crop()
    assert wait_until(lambda: not crop_viewer.crop.isVisible())
    reader = QImage(target)
    assert reader.width() == 50 and not os.path.exists(target + fsops.PART_SUFFIX)


def test_failed_crop_warns_and_keeps_the_selection(crop_viewer, monkeypatch):
    choose_target(monkeypatch, os.path.join(os.path.dirname(crop_viewer.current_path), "no-se-puede.formatoraro"))
    warnings = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QMessageBox.warning", lambda _p, _t, message: warnings.append(message))
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 50))
    crop_viewer.save_crop()
    assert wait_until(lambda: warnings)
    assert crop_viewer.crop.isVisible() and not crop_viewer.crop.selection.isEmpty()


def test_cancelled_save_keeps_the_selection(crop_viewer, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QFileDialog.getSaveFileName", lambda *a: ("", ""))
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 50))
    crop_viewer.save_crop()
    assert crop_viewer.crop.isVisible() and crop_viewer.crop.selection == QRect(0, 0, 50, 50)


def test_changing_image_cancels_the_crop(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 50))
    crop_viewer.step(1)
    assert not crop_viewer.crop.isVisible() and crop_viewer.crop.selection.isEmpty()
    assert not crop_viewer.actions_by_key["crop"].isChecked()


def test_escape_leaves_the_crop_before_closing(crop_viewer):
    crop_viewer.set_cropping(True)
    QTest.keyClick(crop_viewer, Qt.Key.Key_Escape)
    assert crop_viewer.isVisible() and not crop_viewer.crop.isVisible()


def test_enter_and_double_click_save_the_selection(crop_viewer, monkeypatch):
    saved = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.image_viewer.QFileDialog.getSaveFileName", lambda *a: saved.append(1) or ("", ""))
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(100, 100, 100, 100))
    QTest.keyClick(crop_viewer, Qt.Key.Key_Return)
    QTest.mouseDClick(crop_viewer.crop, Qt.MouseButton.LeftButton, pos=screen_point(crop_viewer, 150, 150))
    assert len(saved) == 2


def test_wheel_does_not_lose_the_selection(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 50))
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    assert crop_viewer._wheel(event) is False
    assert crop_viewer.index == 0 and not crop_viewer.crop.selection.isEmpty()


def test_copy_takes_only_the_selection(crop_viewer):
    crop_viewer.set_cropping(True)
    crop_viewer.crop.set_selection(QRect(0, 0, 50, 40))
    crop_viewer.copy_image()
    assert QApplication.clipboard().image().size() == QSize(50, 40)


def test_slideshow_stops_when_cropping(crop_viewer):
    crop_viewer.toggle_slideshow()
    crop_viewer.set_cropping(True)
    assert not crop_viewer.timer.isActive() and not crop_viewer.actions_by_key["slideshow"].isChecked()


def test_crop_icons_exist():
    assert has_icon("crop") and has_icon("savecrop")


class FakeFrame:
    def __init__(self, start=0, end=33333, image=None):
        self.start, self.end, self.image = start, end, image

    def isValid(self):
        return True

    def startTime(self):
        return self.start

    def endTime(self):
        return self.end

    def width(self):
        return 64

    def height(self):
        return 48

    def toImage(self):
        return self.image


class FakePlayer:
    """Hace de reproductor: recuerda lo que se le pide. En estas pruebas no se abre ningún vídeo."""

    def __init__(self, duration=4000, rate=30.0):
        self._duration, self.rate = duration, rate
        self.requested = []
        self.at = 0
        self.paused = 0
        self.state = QMediaPlayer.PlaybackState.PausedState

    def duration(self):
        return self._duration

    def position(self):
        return self.at

    def setPosition(self, value):
        self.requested.append(value)
        self.at = value

    def pause(self):
        self.paused += 1
        self.state = QMediaPlayer.PlaybackState.PausedState

    def playbackState(self):
        return self.state

    def source(self):
        return QUrl.fromLocalFile("v.mp4")

    def metaData(self):
        data = QMediaMetaData()
        if self.rate:
            data.insert(QMediaMetaData.Key.VideoFrameRate, self.rate)
        return data


@pytest.fixture
def fake_media(app, monkeypatch):
    player = MediaPlayer({"viewer_muted": True}, windowed=True)
    fake = FakePlayer()
    real_player = player.player
    player.player = fake
    player.path = "v.mp4"
    player._first_frame_us = 0
    frame = FakeFrame()
    monkeypatch.setattr(player, "current_frame", lambda: frame)
    yield player, fake
    player._finish_steps()
    player.player = real_player


def test_frame_arithmetic():
    interval = 1000000 / 30
    assert frame_index_at(0, 0, interval) == 0
    assert frame_index_at(33333, 0, interval) == 1  # los tiempos llegan redondeados hacia abajo
    assert frame_index_at(50000, 0, interval) == 1
    assert frame_index_at(0, 66666, interval) == 0  # antes de que empiece el vídeo se ve su primer fotograma
    assert frame_middle_ms(0, 0, interval) == 16 and frame_middle_ms(1, 0, interval) == 50 and frame_middle_ms(2, 66666, interval) == 149


def test_step_goes_to_the_middle_of_the_next_frame(fake_media):
    media, fake = fake_media
    fake.at = 1000
    fake.state = QMediaPlayer.PlaybackState.PlayingState
    media.step_frame(1)
    assert fake.paused == 1 and fake.state == QMediaPlayer.PlaybackState.PausedState
    assert fake.requested == [1050]
    media._frame_arrived(FakeFrame())
    assert media._step_index is None


def test_step_back_goes_to_the_previous_frame(fake_media):
    media, fake = fake_media
    fake.at = 1000
    media.step_frame(-1)
    assert fake.requested == [983]


def test_quick_steps_wait_for_each_other_and_none_is_lost(fake_media):
    media, fake = fake_media
    fake.at = 1000
    for _ in range(3):
        media.step_frame(1)
    assert fake.requested == [1050]  # los demás esperan a que llegue este fotograma
    media._frame_arrived(FakeFrame())
    media._frame_arrived(FakeFrame())
    media._frame_arrived(FakeFrame())
    assert fake.requested == [1050, 1083, 1116] and media._step_index is None


def test_step_back_from_the_first_frame_does_nothing(fake_media):
    media, fake = fake_media
    media.step_frame(-1)
    assert fake.requested == [] and media._step_index is None


def test_step_does_not_ask_for_the_end_of_the_video(fake_media):
    media, fake = fake_media
    fake.at = 3990
    media.step_frame(1)
    assert fake.requested == [] and media._step_index is None


def test_frame_length_comes_from_the_file_if_qt_does_not_know_it(fake_media, monkeypatch):
    media, fake = fake_media
    fake.rate = None
    monkeypatch.setattr("directory_commander_tux_tux.ui.media_player.read_media_info", lambda *a: media_info.MediaInfo(frame_rate=25.0))
    assert media.frame_interval_us() == 40000
    monkeypatch.setattr("directory_commander_tux_tux.ui.media_player.read_media_info", lambda *a: None)
    media._file_rate = None
    media._seen_interval = 20000
    assert media.frame_interval_us() == 20000


@pytest.mark.parametrize("name", ["v.ts", "v.M2TS", "v.wmv"])
def test_formats_without_exact_steps_do_not_offer_them(fake_media, name):
    media, fake = fake_media
    media.path = name
    media._show_step_availability()
    assert not media.back_button.isEnabled() and "formato" in media.forward_button.toolTip()
    media.step_frame(1)
    assert fake.requested == [] and fake.paused == 0


def test_side_viewer_has_no_frame_buttons(app):
    assert MediaPlayer({}).back_button is None


@pytest.fixture
def video_folder(window, tree):
    left, _ = tree
    for name in ("uno.mp4", "dos.mkv", "tres.mp4"):
        (left / name).write_bytes(b"no es un video")
    window.config["viewer_muted"] = True
    window.config["viewer_autoplay"] = False
    window.run_command("Go REFRESH")
    assert wait_until(lambda: "uno.mp4" in names(window.source))
    return left


def open_video(window, folder, monkeypatch, name="uno.mp4"):
    opened = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    window.source.open_entry(entry_from_path(os.path.normpath(str(folder / name))))
    return opened


def test_video_opens_in_the_internal_viewer_by_default(window, video_folder, monkeypatch):
    opened = open_video(window, video_folder, monkeypatch)
    assert not opened and len(window._viewers) == 1
    viewer = window._viewers[0]
    assert viewer.isVisible() and sorted(os.path.basename(p) for p in viewer.paths) == ["dos.mkv", "tres.mp4", "uno.mp4"]
    assert os.path.basename(viewer.current_path) == "uno.mp4"
    assert viewer.status_labels["position"].text() == f"{viewer.index + 1}/3"
    assert [e.path for e in window.source.selected_entries()] == [viewer.current_path]
    viewer.close()


def test_video_can_open_with_the_system(window, video_folder, monkeypatch):
    window.config["open_videos"] = "system"
    opened = open_video(window, video_folder, monkeypatch)
    assert len(opened) == 1 and not window._viewers


def test_video_preference_is_saved_and_validated(app, tmp_path):
    config = {}
    dialog = PreferencesDialog(config)
    assert dialog.open_videos.currentData() == "viewer"
    dialog.open_videos.setCurrentIndex(dialog.open_videos.findData("system"))
    dialog.apply_to(config)
    assert config["open_videos"] == "system"
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"open_videos": "otro"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({}, ["open_videos"])


def test_video_viewer_changes_video_and_marks_it(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    first = viewer.current_path
    viewer.actions_by_key["next"].trigger()
    assert viewer.current_path != first
    assert [e.path for e in window.source.selected_entries()] == [viewer.current_path]
    viewer.close()


def test_video_that_cannot_play_offers_the_system(window, video_folder, monkeypatch):
    opened = open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    viewer._failed("Formato no admitido")
    assert viewer.stack.currentWidget() is viewer.failure_page and "uno.mp4" in viewer.message.text()
    assert not viewer.actions_by_key["save_frame"].isEnabled()
    viewer.open_system_button.click()
    assert len(opened) == 1
    viewer.step(1)
    assert viewer.stack.currentWidget() is viewer.media  # el siguiente vuelve a intentarse
    viewer.close()


def test_closing_the_video_viewer_lets_go_of_the_file(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    media = viewer.media
    viewer.close()
    assert not media.has_source


def test_file_operations_release_the_video_viewer(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    calls = []
    monkeypatch.setattr(viewer.media, "release", lambda: calls.append(1))
    window.jobs.aboutToStart.emit()
    assert calls == [1]
    viewer.close()


def test_side_viewer_does_not_play_while_a_video_window_is_open(window, video_folder, monkeypatch):
    window.config["viewer_autoplay"] = True
    window.run_command("Set VIEWPANE=On")
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    assert window.preview.autoplay_allowed() is False
    loads = []

    class FakeSide(QWidget):
        def load(self, path, play=None):
            loads.append((path, play))

        def clear(self):
            pass

        def release(self):
            pass

        def apply_icons(self):
            pass

    side = window.preview.media
    window.preview.media = FakeSide()
    window.preview._media_path = "x.mp4"
    window.preview._start_media()
    assert loads == [("x.mp4", False)]
    window.preview.media = side
    window.preview._media_path = None
    viewer.close()
    assert wait_until(lambda: window.preview.autoplay_allowed() is True)
    window.preview.dispose()


def save_frame_of(viewer, monkeypatch, image, position, target):
    frame = FakeFrame(image=image)
    monkeypatch.setattr(viewer.media, "current_frame", lambda: frame)
    fake = FakePlayer()
    fake.at = position
    fake.state = QMediaPlayer.PlaybackState.PlayingState
    real = viewer.media.player
    viewer.media.player = fake
    asked = []

    def dialog(_parent, _title, suggested, _filter):
        asked.append(suggested)
        return target, ""

    monkeypatch.setattr("directory_commander_tux_tux.ui.video_viewer.QFileDialog.getSaveFileName", dialog)
    viewer.save_frame()
    viewer.media.player = real
    return fake, asked


def test_save_frame_writes_a_png_of_the_whole_frame(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    target = str(video_folder / "captura")  # sin extensión: se añade
    fake, asked = save_frame_of(viewer, monkeypatch, image, 83456, target)
    assert fake.state == QMediaPlayer.PlaybackState.PausedState  # para que lo guardado sea lo que se ve
    assert os.path.basename(asked[0]) == "uno_fotograma_01-23-456.png"
    assert wait_until(lambda: (video_folder / "captura.png").exists() and not (video_folder / ("captura.png" + fsops.PART_SUFFIX)).exists())
    saved = QImage(str(video_folder / "captura.png"))
    assert (saved.width(), saved.height()) == (64, 48) and saved.pixelColor(5, 5) == QColor("green")
    viewer.close()


def test_cancelling_save_frame_writes_nothing(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    before = set(os.listdir(video_folder))
    save_frame_of(viewer, monkeypatch, QImage(4, 4, QImage.Format.Format_RGB32), 0, "")
    assert set(os.listdir(video_folder)) == before
    viewer.close()


def test_frame_name_does_not_overwrite_and_shows_hours(tmp_path):
    video = tmp_path / "v.mp4"
    (tmp_path / "v_fotograma_00-01-005.png").write_bytes(b"x")
    assert os.path.basename(image_ops.frame_name(str(video), 1005)) != "v_fotograma_00-01-005.png"
    assert os.path.basename(image_ops.frame_name(str(video), 3661007)) == "v_fotograma_1-01-01-007.png"


def test_exact_position_text():
    assert format_exact_position(83456) == "01:23.456" and format_exact_position(5) == "00:00.005"


def test_click_on_the_video_plays_or_pauses(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    toggles = []
    monkeypatch.setattr(viewer.media, "toggle_play", lambda: toggles.append(1))
    QTest.mouseClick(viewer.media.video, Qt.MouseButton.LeftButton)
    assert len(toggles) == 1
    QTest.mouseClick(viewer.media.video, Qt.MouseButton.RightButton)
    assert len(toggles) == 1
    viewer.close()


def test_double_click_on_the_video_changes_fullscreen_and_keeps_playing(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    toggles, fullscreen = [], []
    monkeypatch.setattr(viewer.media, "toggle_play", lambda: toggles.append(1))
    monkeypatch.setattr(viewer, "toggle_fullscreen", lambda: fullscreen.append(1))
    viewer._video_clicked(False)  # lo que llega: el primer clic, el doble clic y al soltar el segundo
    viewer._video_clicked(True)
    viewer._video_clicked(False)
    assert len(toggles) == 2 and len(fullscreen) == 1  # dos cambios: queda como estaba
    viewer._video_clicked(False)
    assert len(toggles) == 3  # el siguiente clic vuelve a contar
    viewer.close()


def test_video_window_always_plays_on_opening(window, video_folder, monkeypatch):
    window.config["viewer_autoplay"] = False
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    loads = []
    monkeypatch.setattr(viewer.media, "load", lambda path, play=None: loads.append(play))
    viewer.load_current()
    assert loads == [True]
    viewer.close()


def test_video_window_has_no_auto_button_but_the_side_viewer_does(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    assert viewer.media.auto_button is None
    assert not any(button.text() == "Auto" for button in viewer.media.controls.findChildren(QToolButton))
    viewer.close()
    assert MediaPlayer({}).auto_button.text() == "Auto"


def test_repeat_is_on_by_default_only_in_the_video_window(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    media = viewer.media
    assert media.repeat_button.isChecked() and window.config["video_window_repeat"] is True
    assert window.config["viewer_repeat"] is False
    media.repeat_button.click()
    assert window.config["video_window_repeat"] is False and window.config["viewer_repeat"] is False
    viewer.close()
    side = MediaPlayer({})
    side._load_settings()
    assert not side.repeat_button.isChecked()


def test_video_window_repeat_uses_the_setting_of_a_fresh_config(app):
    media = MediaPlayer({}, windowed=True)
    media._load_settings()
    assert media.repeat_button.isChecked()


def actions_text(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def internal_choice_menu(window, name, shell, monkeypatch):
    """Lo que muestra «Abrir con» al abrirlo. Se mira mientras el menú sigue abierto."""
    window.run_command("Go REFRESH")
    assert wait_until(lambda: name in names(window.source))
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: shell)
    captured = {}

    def run(menu, _position):
        entry = next(a for a in menu.actions() if a.text().replace("&", "") == "Abrir con")
        submenu = entry.menu()
        captured["submenu"] = None
        if submenu is not None:
            submenu.aboutToShow.emit()
            captured["submenu"] = actions_text(submenu)
            captured["internal"] = next((a for a in submenu.actions() if a.text() == "Visualizador interno"), None)
            if captured["internal"] is not None:
                captured["internal"].trigger()

    monkeypatch.setattr(window, "_run_menu", run)
    select(window.source, name)
    window._show_file_menu(window.source, QPoint(1, 1))
    return captured


def write_image(path):
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))


def test_open_with_offers_the_internal_viewer_for_images_set_to_the_system(window, tree, monkeypatch):
    left, _ = tree
    write_image(left / "foto.png")
    window.config["open_images"] = "system"
    shell = FakeShell([OPEN_WITH_ITEM], {60: [ShellItem(label="Paint", command=9)]})
    captured = internal_choice_menu(window, "foto.png", shell, monkeypatch)
    assert captured["submenu"] == ["Visualizador interno", "Paint"]
    assert len(window._viewers) == 1 and isinstance(window._viewers[0], ImageViewer)
    window._viewers[0].close()


def test_open_with_offers_the_internal_viewer_for_videos_set_to_the_system(window, video_folder, monkeypatch):
    window.config["open_videos"] = "system"
    shell = FakeShell([OPEN_WITH_ITEM], {60: [ShellItem(label="VLC", command=9)]})
    captured = internal_choice_menu(window, "uno.mp4", shell, monkeypatch)
    assert captured["submenu"] == ["Visualizador interno", "VLC"]
    assert len(window._viewers) == 1 and hasattr(window._viewers[0], "media")
    window._viewers[0].close()


def test_open_with_does_not_repeat_the_internal_viewer_if_it_is_the_default(window, video_folder, monkeypatch):
    shell = FakeShell([OPEN_WITH_ITEM], {60: [ShellItem(label="VLC", command=9)]})
    captured = internal_choice_menu(window, "uno.mp4", shell, monkeypatch)
    assert captured["submenu"] == ["VLC"] and not window._viewers


def test_open_with_of_other_files_does_not_offer_the_internal_viewer(window, tree, monkeypatch):
    window.config["open_images"] = window.config["open_videos"] = "system"
    shell = FakeShell([OPEN_WITH_ITEM], {60: [ShellItem(label="Bloc de notas", command=9)]})
    assert internal_choice_menu(window, "a.txt", shell, monkeypatch)["submenu"] == ["Bloc de notas"]


def test_internal_viewer_without_a_submenu_of_the_system(window, video_folder, monkeypatch):
    """Sin programas asociados, Windows no da un submenú sino una opción que abre la ventana de elegir."""
    window.config["open_videos"] = "system"
    shell = FakeShell([ShellItem(label="A&brir con", verb="openas", command=8)])
    captured = internal_choice_menu(window, "uno.mp4", shell, monkeypatch)
    assert captured["submenu"] == ["Visualizador interno", "Elegir otra aplicación…"]
    window._viewers[0].close()


def test_internal_viewer_when_other_programs_are_turned_off(window, video_folder, monkeypatch):
    window.config["open_videos"] = "system"
    window.config["shell_menu_items"] = False
    captured = internal_choice_menu(window, "uno.mp4", None, monkeypatch)
    assert captured["submenu"] == ["Visualizador interno", "Elegir otra aplicación…"]
    window._viewers[0].close()


def toolbar_labels(bar):
    return [action.text() for action in bar.actions() if not action.isSeparator()]


def set_removed(window, **bars):
    window.config["bar_buttons"] = bars
    window.apply_theme()
    window._apply_bar_buttons()


def test_preferences_list_every_bar(app):
    dialog = PreferencesDialog({})
    page = dialog.bar_buttons
    assert set(page.lists) == {"toolbar", "location", "pane", "git", "image_viewer", "video_viewer", "video_controls", "player_controls"}
    assert page.values() == {}
    tools = [page.lists["toolbar"].item(row).text() for row in range(page.lists["toolbar"].count())]
    assert "Copiar" in tools and "Terminal" in tools


def test_preferences_save_what_is_unchecked(app):
    config = {"bar_buttons": {"pane": ["home"]}}
    dialog = PreferencesDialog(config)
    page = dialog.bar_buttons
    pane = page.lists["pane"]
    states = {pane.item(row).data(Qt.ItemDataRole.UserRole): pane.item(row).checkState() for row in range(pane.count())}
    assert states["home"] == Qt.CheckState.Unchecked and states["goup"] == Qt.CheckState.Checked
    tools = page.lists["toolbar"]
    tools.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.apply_to(config)
    assert config["bar_buttons"] == {"toolbar": [tools.item(0).text()], "pane": ["home"]}


def test_show_all_marks_the_current_bar(app):
    config = {"bar_buttons": {"pane": ["home"], "toolbar": ["Copiar"]}}
    dialog = PreferencesDialog(config)
    page = dialog.bar_buttons
    page.bar_list.setCurrentRow(list(page.lists).index("pane"))
    page.show_all()
    assert page.values() == {"toolbar": ["Copiar"]}


def test_main_toolbar_hides_chosen_buttons_and_extra_lines(window):
    assert "Terminal" in toolbar_labels(window.toolbar)
    tools = [item["label"] for item in window.config["toolbar"] if item.get("label")]
    assert "Propiedades" in tools
    set_removed(window, toolbar=["Terminal", "Buscar", "Sincronizar", "Duplicados"])
    labels = toolbar_labels(window.toolbar)
    assert "Terminal" not in labels and "Copiar" in labels
    actions = window.toolbar.actions()
    assert not actions[-1].isSeparator()  # el grupo quedó vacío y su línea tampoco está
    set_removed(window)
    assert "Terminal" in toolbar_labels(window.toolbar)


def test_restoring_the_toolbar_brings_back_hidden_buttons(window):
    set_removed(window, toolbar=["Terminal"], pane=["home"])
    window._reset_toolbar()
    assert "Terminal" in toolbar_labels(window.toolbar)
    assert window.config["bar_buttons"] == {"pane": ["home"]}


def test_style_bar_hides_chosen_styles_and_they_still_apply(window):
    set_removed(window, styles=["Comandante"])
    assert "Comandante" not in toolbar_labels(window.style_bar) and "Único" in toolbar_labels(window.style_bar)
    window.apply_style("Comandante")
    assert window.config["style"] == "Comandante"


def test_location_bar_hides_its_buttons(window):
    set_removed(window, location=["recent", "content_type"])
    assert not window._location_recent_action.isVisible() and not any(a.isVisible() for a in window._content_actions)
    set_removed(window)
    assert window._location_recent_action.isVisible() and all(a.isVisible() for a in window._content_actions)


def test_pane_bar_hides_chosen_buttons(window):
    window.resize(1400, 700)
    settle()
    view = window.source
    view.resize(900, view.height())
    set_removed(window, pane=["home", "send", "menu-arrow"])
    tools = {icon: button for button, icon in view._tool_buttons}
    view._fit_pane_bar()
    assert view.send_button.isHidden() and view.history_button.isHidden() and tools["home"].isHidden()
    assert not tools["goup"].isHidden()
    set_removed(window)
    assert not tools["home"].isHidden()


def test_new_panes_also_hide_chosen_buttons(window):
    window.config["bar_buttons"] = {"pane": ["home"]}
    view = window.groups[0].add_tab(window.source.path, activate=False)
    assert view._removed_tools == {"home"}


def test_image_viewer_toolbar_keeps_slider_and_shortcuts(window, viewer):
    assert viewer._zoom_action in viewer.toolbar.actions()
    set_removed(window, image_viewer=["zoom_slider", "delete", "rotate_left"])
    actions = viewer.toolbar.actions()
    assert viewer.actions_by_key["delete"] not in actions and viewer._zoom_action not in actions
    assert viewer.actions_by_key["next"] in actions
    assert viewer.actions_by_key["delete"] in viewer.actions()  # el atajo sigue
    set_removed(window)
    assert viewer._zoom_action in viewer.toolbar.actions()
    viewer.set_zoom(2.0)
    assert viewer.zoom_slider.value() == zoom_to_slider(2.0)  # el control sigue vivo y sigue el zoom


def test_new_image_viewer_uses_the_choice(window, image_folder):
    window.config["bar_buttons"] = {"image_viewer": ["crop"]}
    shown = window._new_image_viewer([os.path.normpath(str(image_folder / "uno.png"))], 0)
    assert shown.actions_by_key["crop"] not in shown.toolbar.actions()
    shown.close()


def test_hidden_button_of_the_viewer_toolbar_can_still_crop(window, viewer):
    set_removed(window, image_viewer=["crop"])
    viewer.actions_by_key["crop"].trigger()
    assert viewer.crop.isVisible()
    viewer.set_cropping(False)


def test_video_window_toolbar_and_controls_hide_chosen_buttons(window, video_folder, monkeypatch):
    open_video(window, video_folder, monkeypatch)
    viewer = window._viewers[0]
    viewer.resize(1000, 600)
    settle()
    media = viewer.media
    assert wait_until(lambda: viewer.stack.currentWidget() is viewer.failure_page)  # el archivo no es un vídeo de verdad
    viewer.stack.setCurrentWidget(media)
    settle()
    media.apply_bar_buttons()
    assert media.stop_button.isVisible() and media.back_button.isVisible()
    set_removed(window, video_viewer=["reveal", "fullscreen"], video_controls=["stop", "frame_back", "play", "volume"])
    assert viewer.actions_by_key["reveal"] not in viewer.toolbar.actions()
    assert viewer.actions_by_key["fullscreen"] in viewer.actions()
    assert not media.stop_button.isVisible() and not media.back_button.isVisible() and not media.play_button.isVisible()
    assert not media.volume.isVisible() and media.forward_button.isVisible() and media.seek.isVisible()
    set_removed(window)
    assert media.stop_button.isVisible() and media.play_button.isVisible()
    viewer.close()


def test_side_player_uses_its_own_choice(app):
    media = MediaPlayer({"bar_buttons": {"player_controls": ["auto"], "video_controls": ["stop"]}})
    media.resize(600, 300)
    media.show()
    settle()
    media.apply_bar_buttons()
    assert not media.auto_button.isVisible() and media.stop_button.isVisible()
    media.close()


def test_bar_buttons_setting_is_validated_on_import(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"bar_buttons": {"pane": ["home", 3, "home"], "toolbar": "x"}}}),
                    encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"bar_buttons": {"pane": ["home"]}}, [])


def test_toolbar_key():
    assert bar_buttons.toolbar_key({"label": "Copiar", "commands": ["Copy"]}) == "Copiar"
    assert bar_buttons.toolbar_key({"commands": ["Copy"]}) == "Copy"
    assert bar_buttons.toolbar_key({"command": "Copy"}) == "Copy"
    for not_a_button in ({"separator": True}, {"label": "x", "commands": ["Set Spacer"]}, "basura", {}):
        assert bar_buttons.toolbar_key(not_a_button) == ""


def test_bar_lists_use_an_imported_toolbar():
    listed = {bar: buttons for bar, _title, buttons in bar_buttons.bars({"toolbar": [{"label": "Mío", "commands": ["Go UP"]}, {"separator": True}],
                                                                         "styles": [{"name": "Propio"}, "basura"]})}
    assert listed["toolbar"] == [("Mío", "Mío"), (bar_buttons.SEPARATOR, "")] and listed["styles"] == [("Propio", "Propio")]


NO_KEYS = Qt.KeyboardModifier.NoModifier
ANY_ACTION = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction


def file_mime(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    DRAG_DATA.append(mime)
    return mime


def drop_event(point, paths, keys=NO_KEYS, actions=ANY_ACTION):
    return QDropEvent(QPointF(point), actions, file_mime(*paths), Qt.MouseButton.LeftButton, keys)


def move_event(point, paths, keys=NO_KEYS, actions=ANY_ACTION):
    return QDragMoveEvent(point, actions, file_mime(*paths), Qt.MouseButton.LeftButton, keys)


def name_center(view, name):
    return view.current_view.name_rect(view.model.index(row_of(view, name), 0)).center()


def blank_of(view):
    return view.current_view.viewport().rect().bottomRight() - QPoint(5, 5)


def send_to_view(view, event):
    """Directamente al manejador: Qt solo entrega estos eventos al destino que recuerda su gestor de arrastre real."""
    widget = view.current_view
    if isinstance(event, QDragEnterEvent):
        widget.dragEnterEvent(event)
    elif isinstance(event, QDragMoveEvent):
        widget.dragMoveEvent(event)
    else:
        widget.dropEvent(event)
    return event


@pytest.mark.parametrize("mode", ["List", "Details", "SmallIcons", "LargeIcons", "Tiles", "Thumbnails"])
def test_every_view_can_drag_and_receive(window, mode):
    window.run_command(f"Set VIEW={mode}")
    settle()
    widget = window.source.current_view
    assert widget.dragEnabled() and widget.viewport().acceptDrops()
    assert widget.dragDropMode() == QAbstractItemView.DragDropMode.DragDrop


class FakeDrag:
    started = []

    def __init__(self, _source):
        self.mime = None

    def setMimeData(self, mime):
        self.mime = mime

    def setPixmap(self, pixmap):
        assert not pixmap.isNull()

    def setHotSpot(self, _point):
        pass

    def exec(self, *_args):
        FakeDrag.started.append([url.toLocalFile() for url in self.mime.urls()])
        return Qt.DropAction.IgnoreAction


@pytest.mark.parametrize("mode", ["List", "Details", "Thumbnails"])
def test_dragging_a_name_starts_a_drag(window, tree, monkeypatch, mode):
    left, _ = tree
    FakeDrag.started = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.drag_drop.QDrag", FakeDrag)
    window.run_command(f"Set VIEW={mode}")
    settle()
    view = window.source
    point = name_center(view, "a.txt")
    viewport = view.current_view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(viewport, point + QPoint(30, 12))
    QTest.mouseMove(viewport, point + QPoint(60, 24))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point + QPoint(60, 24))
    assert [os.path.normpath(p) for p in FakeDrag.started[0]] == [os.path.normpath(str(left / "a.txt"))]


def test_dragging_several_takes_them_all(window, tree, monkeypatch):
    left, _ = tree
    FakeDrag.started = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.drag_drop.QDrag", FakeDrag)
    window.run_command("Set VIEW=List")
    settle()
    view = window.source
    select(view, "a.txt", "b.txt")
    view.current_view.startDrag(ANY_ACTION)
    assert len(FakeDrag.started[0]) == 2


@pytest.mark.parametrize("mode", ["List", "Details", "Thumbnails"])
def test_drop_on_a_folder_moves_into_it(window, tree, mode):
    left, _ = tree
    window.run_command(f"Set VIEW={mode}")
    settle()
    view = window.source
    event = send_to_view(view, drop_event(name_center(view, "subcarpeta"), [left / "a.txt"]))
    assert event.isAccepted()
    assert wait_until(lambda: (left / "subcarpeta" / "a.txt").exists() and not (left / "a.txt").exists() and window.jobs.running == 0)


def test_drop_on_the_other_pane_moves_a_folder(window, tree):
    left, right = tree
    window.run_command("Set VIEW=List")
    settle()
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "subcarpeta"]))
    assert wait_until(lambda: (right / "subcarpeta").is_dir() and not (left / "subcarpeta").exists() and window.jobs.running == 0)


def test_moved_by_dragging_can_be_undone(window, tree):
    left, right = tree
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"]))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    window.run_command("Undo")
    assert wait_until(lambda: (left / "a.txt").exists() and not (right / "a.txt").exists() and window.jobs.running == 0)


def test_ctrl_copies_and_shift_moves(window, tree, monkeypatch):
    left, right = tree
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"], Qt.KeyboardModifier.ControlModifier))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert (left / "a.txt").exists()
    monkeypatch.setattr(fsops, "same_volume", lambda *_a: False)
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "b.txt"], Qt.KeyboardModifier.ShiftModifier))
    assert wait_until(lambda: (right / "b.txt").exists() and not (left / "b.txt").exists() and window.jobs.running == 0)


def test_other_drive_copies_by_default(window, tree, monkeypatch):
    left, right = tree
    monkeypatch.setattr(fsops, "same_volume", lambda *_a: False)
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"]))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert (left / "a.txt").exists()


def test_program_that_only_lets_copy_is_copied(window, tree):
    left, right = tree
    send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"], actions=Qt.DropAction.CopyAction))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert (left / "a.txt").exists()


def test_source_is_told_copy_even_when_moving(window, tree):
    """Si se le dijera «mover», otro programa podría borrar sus archivos antes de que terminara el trabajo."""
    left, right = tree
    event = send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"]))
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction
    assert wait_until(lambda: (right / "a.txt").exists() and not (left / "a.txt").exists() and window.jobs.running == 0)


def test_drop_where_it_already_is_does_nothing(window, tree):
    left, _ = tree
    view = window.source
    event = send_to_view(view, drop_event(blank_of(view), [left / "a.txt"]))
    assert not event.isAccepted()
    settle()
    assert window.jobs.running == 0 and sorted(os.listdir(left)) == ["a.txt", "b.txt", "subcarpeta"]


def test_folder_cannot_be_dropped_on_itself(window, tree):
    left, _ = tree
    view = window.source
    event = send_to_view(view, drop_event(name_center(view, "subcarpeta"), [left / "subcarpeta"]))
    assert not event.isAccepted()
    assert (left / "subcarpeta").is_dir()


def test_cursor_shows_what_will_happen(window, tree, monkeypatch):
    left, _ = tree
    view = window.source
    widget = view.current_view
    send_to_view(view, QDragEnterEvent(name_center(view, "subcarpeta"), ANY_ACTION, file_mime(left / "a.txt"), Qt.MouseButton.LeftButton, NO_KEYS))
    event = send_to_view(view, move_event(name_center(view, "subcarpeta"), [left / "a.txt"]))
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.MoveAction
    assert widget.hover_row == row_of(view, "subcarpeta")
    event = send_to_view(view, move_event(name_center(view, "subcarpeta"), [left / "a.txt"], Qt.KeyboardModifier.ControlModifier))
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction
    event = send_to_view(view, move_event(blank_of(view), [left / "a.txt"]))
    assert not event.isAccepted() and widget.hover_row == -1


def test_drop_on_a_file_name_goes_to_the_pane_folder(window, tree):
    left, right = tree
    (right / "c.txt").write_text("c", encoding="utf-8")
    window.dest.refresh()
    assert wait_until(lambda: "c.txt" in names(window.dest))
    send_to_view(window.dest, drop_event(name_center(window.dest, "c.txt"), [left / "a.txt"]))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)


def test_dropping_does_not_reach_a_compressed_file(window, tree):
    left, right = tree
    with zipfile.ZipFile(right / "z.zip", "w") as out:
        out.writestr("x.txt", "x")
    window.dest.navigate(str(right / "z.zip"))
    assert wait_until(lambda: "x.txt" in names(window.dest))
    event = send_to_view(window.dest, drop_event(blank_of(window.dest), [left / "a.txt"]))
    assert not event.isAccepted()


def strip_point(group):
    """Un punto del hueco a la derecha de las pestañas, que no pertenece a la barra."""
    bar = group.tabBar()
    return QPoint(group.width() - 60, bar.geometry().center().y())


def test_folder_dropped_on_the_tab_strip_opens_a_new_tab(window, tree):
    left, _ = tree
    window.resize(1400, 700)
    settle()
    group = window.groups[0]
    tabs = group.count()
    point = strip_point(group)
    assert group._in_tab_strip(point)
    enter = QDragEnterEvent(point, ANY_ACTION, file_mime(left / "subcarpeta"), Qt.MouseButton.LeftButton, NO_KEYS)
    group.dragEnterEvent(enter)
    assert enter.isAccepted()
    event = drop_event(point, [left / "subcarpeta"])
    group.dropEvent(event)
    assert event.isAccepted()
    assert wait_until(lambda: group.count() == tabs + 1)
    assert os.path.normpath(group.current.path) == os.path.normpath(str(left / "subcarpeta"))
    assert (left / "subcarpeta").is_dir()  # la carpeta no se mueve ni se copia


def test_several_folders_open_a_tab_each_and_files_are_ignored(window, tree):
    left, right = tree
    group = window.groups[0]
    tabs = group.count()
    group.dropEvent(drop_event(strip_point(group), [left / "subcarpeta", right, left / "a.txt"]))
    assert wait_until(lambda: group.count() == tabs + 2)


def test_only_files_on_the_tab_strip_do_nothing(window, tree):
    left, _ = tree
    group = window.groups[0]
    tabs = group.count()
    point = strip_point(group)
    enter = QDragEnterEvent(point, ANY_ACTION, file_mime(left / "a.txt"), Qt.MouseButton.LeftButton, NO_KEYS)
    group.dragEnterEvent(enter)
    assert not enter.isAccepted()
    event = drop_event(point, [left / "a.txt"])
    group.dropEvent(event)
    settle()
    assert not event.isAccepted() and group.count() == tabs


def test_drop_outside_the_tab_strip_is_not_for_the_tabs(window, tree):
    left, _ = tree
    group = window.groups[0]
    tabs = group.count()
    event = drop_event(QPoint(group.width() // 2, group.height() // 2), [left / "subcarpeta"])
    group.dropEvent(event)
    settle()
    assert not event.isAccepted() and group.count() == tabs


def test_folder_dropped_on_a_tab_opens_a_new_tab(window, tree):
    left, _ = tree
    group = window.groups[0]
    tabs = group.count()
    bar = group.tabBar()
    event = drop_event(bar.rect_of(0).center(), [left / "subcarpeta"])
    bar.dropEvent(event)
    assert event.isAccepted()
    assert wait_until(lambda: group.count() == tabs + 1)
    assert os.path.normpath(group.current.path) == os.path.normpath(str(left / "subcarpeta"))


def test_tab_treated_as_folder_receives_the_folder(window, tree):
    left, right = tree
    group = window.groups[1]
    tabs = group.count()
    set_tabs(window, tab_as_folder=True)
    group = window.groups[1]
    bar = group.tabBar()
    bar.dropEvent(drop_event(bar.rect_of(0).center(), [left / "subcarpeta"]))
    assert wait_until(lambda: (right / "subcarpeta").is_dir() and window.jobs.running == 0)
    assert group.count() == tabs


def tree_item(window, path):
    return window.tree._node(window.tree.favorites_item, "prueba", str(path), QIcon())


def test_drop_on_the_folder_tree_moves_there(window, tree, monkeypatch):
    left, right = tree
    item = tree_item(window, right)
    monkeypatch.setattr(window.tree, "itemAt", lambda *_a: item)
    event = drop_event(QPoint(5, 5), [left / "a.txt"])
    window.tree.dropEvent(event)
    assert event.isAccepted()
    assert wait_until(lambda: (right / "a.txt").exists() and not (left / "a.txt").exists() and window.jobs.running == 0)


def test_folder_tree_marks_where_it_will_drop_and_expands_it(window, tree, monkeypatch):
    left, right = tree
    folder_tree = window.tree
    item = tree_item(window, right)
    item.setExpanded(False)
    monkeypatch.setattr(folder_tree, "itemAt", lambda *_a: item)
    folder_tree._expand_timer.setInterval(10)
    enter = QDragEnterEvent(QPoint(5, 5), ANY_ACTION, file_mime(left / "a.txt"), Qt.MouseButton.LeftButton, NO_KEYS)
    folder_tree.dragEnterEvent(enter)
    event = move_event(QPoint(5, 5), [left / "a.txt"])
    folder_tree.dragMoveEvent(event)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.MoveAction
    assert folder_tree._drop_item is item and item.background(0).color() == QColor(theme.current().accent)
    assert wait_until(item.isExpanded)
    folder_tree._mark_drop_item(None)
    assert item.data(0, Qt.ItemDataRole.BackgroundRole) is None


def test_folder_tree_refuses_where_there_is_no_folder(window, tree, monkeypatch):
    left, _ = tree
    monkeypatch.setattr(window.tree, "itemAt", lambda *_a: window.tree.favorites_item)
    event = drop_event(QPoint(5, 5), [left / "a.txt"])
    window.tree.dropEvent(event)
    assert not event.isAccepted()


def test_folder_tree_only_drags_real_folders(window, tree):
    left, _ = tree
    folder_tree = window.tree
    child = folder_tree._node(folder_tree.root_item, "unidad", str(left), QIcon())
    grandchild = folder_tree._node(child, "carpeta", str(left / "subcarpeta"), QIcon())
    assert folder_tree._can_drag(grandchild)
    assert not folder_tree._can_drag(folder_tree.home_item) and not folder_tree._can_drag(folder_tree.root_item)
    assert not folder_tree._can_drag(tree_item(window, left))
    if has_computer_view():
        assert not folder_tree._can_drag(child)  # una unidad


def fake_sink(**fields):
    sink = fsops.ProgressSink()
    sink.transfer = fsops.TransferState(ready=True, **fields)
    return sink


def stopped_dialog(sink, verb="Copiando", source="a", target="b"):
    """Sin temporizador y con el tiempo a cero: las pruebas lo hacen avanzar a mano."""
    dialog = TransferDialog(verb, source, target, sink)
    dialog._tick_timer.stop()
    dialog._last_tick = None
    dialog._elapsed = 0.0
    dialog._history.clear()
    return dialog


def test_progress_window_shows_everything(app):
    sink = fake_sink(total_bytes=1000 * 1024 * 1024, total_items=4, bytes_done=750 * 1024 * 1024, items_done=2, current_name="video.mp4",
                     current_size=500 * 1024 * 1024, current_done=250 * 1024 * 1024)
    dialog = stopped_dialog(sink, "Copiando", "C:/origen", "H:/")
    dialog.tick(100.0)
    dialog.tick(101.0)
    assert dialog.windowTitle().startswith("(75%) Copiando - ")
    assert dialog.total_bar.value() == 750 and dialog.current_bar.value() == 500
    assert "video.mp4" in dialog.current_label.toolTip()
    assert dialog.current_size.text() == f"{format_size(250 * 1024 * 1024)} / {format_size(500 * 1024 * 1024)}"
    assert dialog.items_label.text() == "2 / 4 elementos:"
    assert dialog.elapsed_label.text() == "00:01 transcurrido"
    assert "origen" in dialog.from_label.toolTip() and dialog.to_label.toolTip() == "H:/"
    dialog.job_finished()


def test_progress_window_measures_speed_and_time_left(app):
    sink = fake_sink(total_bytes=1000, total_items=1, current_name="a", current_size=1000)
    dialog = stopped_dialog(sink)
    for second, done in enumerate((0, 100, 200, 300)):
        sink.transfer.bytes_done = sink.transfer.current_done = done
        dialog.tick(50.0 + second)
    assert dialog.graph.samples[-1] == pytest.approx(100, abs=1)
    assert dialog.graph.average == pytest.approx(100, abs=1)
    assert dialog.remaining_label.text() == "00:07 restante"
    dialog.job_finished()


def test_progress_window_waits_while_measuring(app):
    sink = fsops.ProgressSink()
    dialog = TransferDialog("Moviendo", "a", "b", sink)
    dialog._tick_timer.stop()
    assert dialog.current_label.text() == "Preparando…" and dialog.total_bar.maximum() == 0 and not dialog.skip_button.isEnabled()
    assert dialog.windowTitle().startswith("Moviendo - ")
    sink.transfer = fsops.TransferState(ready=False)
    dialog.tick()
    assert dialog.current_label.text() == "Preparando…"
    dialog.job_finished()


def test_progress_window_pause_button(app):
    sink = fake_sink(total_bytes=1000, total_items=1, bytes_done=500, current_name="a", current_size=1000, current_done=500)
    dialog = stopped_dialog(sink)
    dialog.tick(10.0)
    dialog.tick(11.0)
    sink.pause()
    dialog.tick(50.0)
    dialog.tick(60.0)
    assert dialog._elapsed == 1.0 and "En pausa" in dialog.windowTitle() and dialog.remaining_label.text() == "en pausa"
    sink.resume()
    dialog.pause_button.click()
    assert sink.is_paused and dialog.pause_button.text() == "Reanudar"
    dialog.pause_button.click()
    assert not sink.is_paused and dialog.pause_button.text() == "Pausar"
    dialog.job_finished()


def test_progress_window_skip_and_cancel(app):
    sink = fake_sink(total_bytes=1000, total_items=1, bytes_done=500, current_name="a", current_size=1000, current_done=500)
    dialog = TransferDialog("Copiando", "a", "b", sink)
    dialog._tick_timer.stop()
    cancelled = []
    dialog.cancelRequested.connect(lambda: cancelled.append(1))
    assert dialog.skip_button.isEnabled()
    dialog.skip_button.click()
    assert sink.transfer.skip_requested and not dialog.skip_button.isEnabled()
    dialog.cancel_button.click()
    assert cancelled == [1] and not dialog.cancel_button.isEnabled() and not dialog.pause_button.isEnabled()
    dialog.job_finished()


def test_progress_window_escape_does_not_cancel_and_closing_does(app):
    sink = fake_sink(total_bytes=1000, total_items=1)
    dialog = TransferDialog("Copiando", "a", "b", sink)
    dialog._tick_timer.stop()
    cancelled = []
    dialog.cancelRequested.connect(lambda: cancelled.append(1))
    dialog.show()
    settle()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible() and not cancelled
    dialog.close()
    assert dialog.isVisible() and cancelled == [1]  # sigue abierta hasta que el trabajo se detiene
    dialog.job_finished()
    assert not dialog.isVisible()


def test_speed_graph_draws_with_and_without_data(app):
    graph = SpeedGraph()
    graph.resize(300, 90)
    assert not graph.grab().isNull()
    for value in (0, 100, 5000, 2000):
        graph.add_sample(value, 1800)
    image = graph.grab().toImage()
    assert image.pixelColor(296, 85).green() > image.pixelColor(296, 85).red()  # la velocidad reciente, en verde


def test_clock_text():
    assert format_clock(4) == "00:04" and format_clock(125.7) == "02:05" and format_clock(3725) == "1:02:05" and format_clock(-3) == "00:00"


def slow_job(window, title="Copiar", on_done=None, errors=()):
    release = threading.Event()

    def work(sink):
        sink.transfer = fsops.TransferState(ready=True, total_bytes=100, total_items=1, current_name="a", current_size=100)
        while not release.is_set():
            sink.check()
            time.sleep(0.005)
        return OpResult(errors=list(errors))

    window.jobs.start(title, work, on_done, ("Copiando", "de", "a"))
    return release


def transfer_windows(window):
    return window.findChildren(TransferDialog)


def test_long_copy_opens_the_progress_window_and_closes_it(window):
    release = slow_job(window)
    dialog = transfer_windows(window)[-1]
    assert not dialog.isVisible()  # no aparece enseguida: lo breve no debe parpadear
    assert wait_until(dialog.isVisible)
    assert dialog.windowTitle().startswith("(0%) Copiando")
    release.set()
    assert wait_until(lambda: window.jobs.running == 0)
    assert not shiboken6.isValid(dialog) or not dialog.isVisible()


def test_quick_copy_never_shows_the_window(window, tree):
    left, right = tree
    window.start_transfer([str(left / "a.txt")], str(right), False)
    assert wait_until(lambda: window.jobs.running == 0)
    settle()
    assert all(not dialog.isVisible() for dialog in transfer_windows(window))


def test_cancel_in_the_window_cancels_the_job(window):
    release = slow_job(window)
    dialog = transfer_windows(window)[-1]
    dialog.cancel_button.click()
    assert wait_until(lambda: window.jobs.running == 0)
    release.set()


def test_window_of_real_copy_is_told_every_path(window, tree, monkeypatch):
    left, right = tree
    captured = []
    real_start = window.jobs.start
    monkeypatch.setattr(window.jobs, "start", lambda title, fn, on_done=None, transfer=None: (captured.append(transfer), real_start(title, fn, on_done, transfer)))
    window.start_transfer([str(left / "a.txt")], str(right), True)
    assert wait_until(lambda: window.jobs.running == 0)
    assert captured == [("Moviendo", str(left), str(right))]


def test_errors_show_a_warning_unless_unattended(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(window, "_job_error_dialog", lambda *a: warnings.append(a))
    release = slow_job(window, errors=[("x", "falló")])
    release.set()
    assert wait_until(lambda: warnings)
    warnings.clear()
    release = slow_job(window, errors=[("x", "falló")])
    transfer_windows(window)[-1].unattended.setChecked(True)
    release.set()
    assert wait_until(lambda: window.jobs.running == 0)
    settle()
    assert not warnings
    assert "falló" in window.log.toPlainText()


def test_video_icons_exist():
    assert has_icon("framestepback") and has_icon("framestepforward") and has_icon("saveframe")


def test_menu_icons_have_a_left_margin():
    menu = QMenu()
    menu.setStyleSheet("QMenu { background: #000000; } QMenu::item { padding: 4px 26px 4px 14px; } QMenu::icon { left: 6px; }")
    menu.addAction(themed("makedir"), "Nueva carpeta")
    image = menu.grab().toImage()
    middle = image.height() // 2
    first = next(x for x in range(1, image.width()) if image.pixelColor(x, middle) != QColor("#000000"))
    assert first >= 6


# Con Windows de verdad: solo se lee el menú; ninguna opción se ejecuta.
windows_only = pytest.mark.skipif(not windows_shell.AVAILABLE, reason="Solo en Windows")


@windows_only
def test_real_folder_menu_can_be_read(app, tmp_path):
    shell = windows_shell.menu_for_folder(str(tmp_path))
    items = shell.items()
    assert any(i.label for i in items) and any(i.separator for i in items) or items
    shell.close()
    assert not shell.is_open
    shell.close()  # cerrar dos veces no debe fallar


@windows_only
def test_real_item_menu_finds_standard_verbs(app, tmp_path):
    one, two = tmp_path / "a.txt", tmp_path / "b.txt"
    one.write_text("", encoding="utf-8")
    two.write_text("", encoding="utf-8")
    for paths in ([str(one)], [str(one), str(two)]):
        shell = windows_shell.menu_for_items(paths)
        verbs = {i.verb.lower() for i in shell.items()}
        assert "properties" in verbs and "delete" in verbs
        assert not verbs & {i.verb.lower() for i in clean_items(shell.items())} & windows_shell.OWN_VERBS
        shell.close()


@windows_only
def test_real_items_from_different_folders_have_no_menu(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "s" / "b.txt").write_text("", encoding="utf-8")
    assert windows_shell.menu_for_items([str(tmp_path / "a.txt"), str(tmp_path / "s" / "b.txt")]) is None


@windows_only
def test_real_menu_for_a_path_that_does_not_exist_fails_cleanly(tmp_path):
    with pytest.raises(OSError):
        windows_shell.menu_for_folder(str(tmp_path / "no-existe"))
    with pytest.raises(OSError):
        windows_shell.menu_for_items([str(tmp_path / "no-existe.txt")])


@windows_only
def test_reserved_bitmap_values_are_not_read():
    for value in (0, 1, 11, 2 ** 64 - 1):
        assert windows_shell.read_bitmap(value) is None


# ---- iconos de las opciones de otros programas ----

def test_legacy_menu_image_is_used_when_there_is_no_modern_one():
    assert windows_shell.choose_bitmap(0, 0x4605) == 0x4605
    assert windows_shell.choose_bitmap(0x1234, 0x4605) == 0x1234
    assert windows_shell.choose_bitmap(2 ** 64 - 1, 0x4605) == 0x4605  # valor reservado: la imagen la dibujaría el menú
    assert windows_shell.choose_bitmap(0, 0) == 0


# ---- configurar los menús de clic derecho ----

def texts_of(menu):
    return ["-" if a.isSeparator() else a.text() for a in menu.actions()]


def set_layout(window, which, **layout):
    window.config["context_menus"] = {which: layout}


def first_in_order(which, *keys):
    """Orden completo, como el que guardan las preferencias, con esas entradas delante."""
    return list(keys) + [k for k in context_menu.DEFAULT_LAYOUTS[which] if k not in keys or k == context_menu.SEPARATOR]


def test_default_layouts_match_the_real_menus(window, tree):
    folder = window.build_folder_menu(window.source)
    assert {t for t in texts_of(folder) if t != "-"} <= set(context_menu.DEFAULT_LAYOUTS[context_menu.FOLDER])
    assert texts_of(folder)[0] == "Pegar" and texts_of(folder)[-1] == "Propiedades de esta carpeta"
    select(window.source, "a.txt")
    files = window.build_file_menu(window.source)
    assert {t for t in texts_of(files) if t != "-"} <= set(context_menu.DEFAULT_LAYOUTS[context_menu.FILE])
    assert texts_of(files)[0] == "Abrir" and texts_of(files)[-1] == "Propiedades"
    assert "-" not in (texts_of(files)[0], texts_of(files)[-1])


def test_hidden_entry_is_not_in_the_menu(window):
    set_layout(window, "folder", hidden=["Vista plana", "Actualizar"])
    labels_ = texts_of(window.build_folder_menu(window.source))
    assert "Vista plana" not in labels_ and "Actualizar" not in labels_ and "Mostrar ocultos" in labels_


def test_entries_follow_the_chosen_order(window):
    set_layout(window, "folder", order=first_in_order("folder", "Actualizar", "---", "Pegar", "Nueva carpeta"))
    labels_ = texts_of(window.build_folder_menu(window.source))
    assert labels_[:4] == ["Actualizar", "-", "Pegar", "Nueva carpeta"]
    assert "Propiedades de esta carpeta" in labels_  # lo no mencionado sigue apareciendo


def test_hiding_a_whole_group_leaves_no_double_lines(window):
    set_layout(window, "folder", hidden=["Seleccionar todo", "Actualizar", "Calcular tamaños de carpetas", "Pegar"])
    labels_ = texts_of(window.build_folder_menu(window.source))
    assert labels_[0] != "-" and labels_[-1] != "-"
    assert not any(a == b == "-" for a, b in zip(labels_, labels_[1:]))


def test_arranged_entries_still_work(window, tree):
    set_layout(window, "folder", order=first_in_order("folder", "Actualizar", "Ver"))
    menu = window.build_folder_menu(window.source)
    views = next(a.menu() for a in menu.actions() if a.text() == "Ver")
    next(a for a in views.actions() if a.text() == "Detalles").trigger()
    assert window.source.view_mode == "details"
    assert menu.actions()[0].text() == "Actualizar"


def test_conditional_entries_stay_conditional(window, tree):
    set_layout(window, "file", order=first_in_order("file", "Ver imágenes", "Extraer", "Abrir"))
    select(window.source, "a.txt")
    labels_ = texts_of(window.build_file_menu(window.source))
    assert labels_[0] == "Abrir" and "Ver imágenes" not in labels_ and "Extraer" not in labels_


def test_invalid_menu_configuration_is_ignored(window):
    window.config["context_menus"] = {"folder": {"order": ["No existe", 5, "Pegar", "Pegar"], "hidden": "nada"}, "file": 3, "shell_hidden": ["x"]}
    labels_ = texts_of(window.build_folder_menu(window.source))
    assert labels_[0] == "Pegar" and labels_.count("Pegar") == 1 and "Propiedades de esta carpeta" in labels_
    assert context_menu.normalize(window.config["context_menus"])["shell_hidden"] == {}
    select(window.source, "a.txt")
    assert "Propiedades" in texts_of(window.build_file_menu(window.source))


def test_new_program_options_appear_in_saved_layouts():
    saved = [k for k in context_menu.DEFAULT_LAYOUTS["file"] if k != "Duplicar aquí"]
    order = context_menu.normalize_layout({"order": saved}, "file")["order"]
    assert order[order.index("Mover a destino") + 1] == "Duplicar aquí"


def test_shell_items_go_where_the_layout_says(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: FakeShell())
    set_layout(window, "folder", order=first_in_order("folder", context_menu.SHELL_ITEMS, "---", "Pegar"))
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert menu_shown[0][0] == "&Otro programa"


def test_shell_items_at_the_very_end(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: FakeShell())
    order = [k for k in context_menu.DEFAULT_LAYOUTS["folder"] if k != context_menu.SHELL_ITEMS] + [context_menu.SHELL_ITEMS]
    set_layout(window, "folder", order=order)
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert menu_shown[0][-1] == "&Otro programa"
    assert menu_shown[0][-2] == ""  # con su línea delante


def test_shell_items_in_the_middle_keep_their_lines(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: FakeShell())
    window._show_folder_menu(window.source, QPoint(1, 1))
    labels_ = menu_shown[0]
    position = labels_.index("&Otro programa")
    assert labels_[position - 1] == "" and labels_[position + 1] == "" and labels_[position + 2] == "Propiedades de esta carpeta"
    assert not any(a == b == "" for a, b in zip(labels_, labels_[1:]))


def test_shell_items_keep_their_place_if_the_next_entry_does_not_apply(window, menu_shown, monkeypatch, tree):
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: FakeShell())
    order = first_in_order("file", context_menu.SHELL_ITEMS, "Extraer", "Copiar")  # «Extraer» no aplica a un .txt
    set_layout(window, "file", order=order)
    select(window.source, "a.txt")
    window._show_file_menu(window.source, QPoint(1, 1))
    assert menu_shown[0][0] == "&Otro programa" and menu_shown[0][1] == "" and menu_shown[0][2] == "Copiar"


def test_hiding_the_shell_entry_does_not_load_extensions(window, menu_shown, monkeypatch):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: pytest.fail("no debe cargar extensiones"))
    set_layout(window, "folder", hidden=[context_menu.SHELL_ITEMS])
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert menu_shown


def test_one_program_option_can_be_hidden(window, menu_shown, monkeypatch):
    shell = FakeShell([ShellItem(label="Con &verbo", command=1, verb="WinRAR_Add"), ShellItem(label="&Sin verbo", command=2), ShellItem(label="Se queda", command=3)])
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: shell)
    window.config["context_menus"] = {"shell_hidden": {"winrar_add": "Con verbo", "sin verbo": "Sin verbo"}}
    window._show_folder_menu(window.source, QPoint(1, 1))
    assert "Se queda" in menu_shown[0] and "Con &verbo" not in menu_shown[0] and "&Sin verbo" not in menu_shown[0]


def test_shell_key_is_stable_across_files():
    assert windows_shell.shell_key(ShellItem(label='Añadir a "uno.rar"', verb="WinRAR_Add")) == windows_shell.shell_key(ShellItem(label='Añadir a "dos.rar"', verb="WinRAR_Add"))
    assert windows_shell.shell_key(ShellItem(label="&Sin Verbo")) == "sin verbo"


def test_tidy_separators():
    menu = QMenu()
    menu.addSeparator()
    menu.addAction("a")
    menu.addSeparator()
    menu.addSeparator()
    menu.addAction("b")
    menu.addSeparator()
    tidy_separators(menu)
    assert texts_of(menu) == ["a", "-", "b"]


def editor_keys(editor):
    return [editor.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(editor.list.count())]


def test_preferences_menu_editor_saves_order_and_hidden(app):
    config = {}
    dialog = PreferencesDialog(config)
    editor = dialog.menus.folder_editor
    assert editor_keys(editor) == context_menu.DEFAULT_LAYOUTS["folder"]
    row = editor_keys(editor).index("Actualizar")
    editor.list.setCurrentRow(row)
    editor._move(-1)
    editor.list.item(row - 1).setCheckState(Qt.CheckState.Unchecked)
    dialog.apply_to(config)
    saved = config["context_menus"]["folder"]
    assert saved["order"][row - 1] == "Actualizar" and saved["hidden"] == ["Actualizar"]
    second = PreferencesDialog(config)
    again = second.menus.folder_editor
    assert editor_keys(again)[row - 1] == "Actualizar"
    assert again.list.item(row - 1).checkState() == Qt.CheckState.Unchecked


def test_preferences_menu_editor_limits_and_separators(app):
    dialog = PreferencesDialog({})
    editor = dialog.menus.file_editor
    keys = editor_keys(editor)
    editor.list.setCurrentRow(0)
    editor._move(-1)
    assert editor_keys(editor) == keys
    editor.list.setCurrentRow(0)
    editor._add_separator()
    assert editor_keys(editor)[1] == "---"
    editor.list.setCurrentRow(1)
    editor._remove_separator()
    assert editor_keys(editor) == keys
    editor.list.setCurrentRow(0)
    editor._remove_separator()  # solo se quitan líneas
    assert editor_keys(editor) == keys


def test_preferences_menu_editor_reset(app):
    config = {"context_menus": {"folder": {"order": first_in_order("folder", "Actualizar"), "hidden": ["Pegar"]}}}
    dialog = PreferencesDialog(config)
    editor = dialog.menus.folder_editor
    assert editor_keys(editor)[0] == "Actualizar"
    editor.reset()
    assert editor_keys(editor) == context_menu.DEFAULT_LAYOUTS["folder"]
    assert editor.values()["hidden"] == []


def test_preferences_reads_program_options_only_when_opening_the_page(app, monkeypatch, tmp_path):
    calls = []

    def reader(*args):
        calls.append(args)
        return FakeShell([ShellItem(label="&Git", verb="git_gui", command=1), ShellItem(label="Cortar", verb="cut", command=2)])

    monkeypatch.setattr(windows_shell, "menu_for_folder", reader)
    monkeypatch.setattr(windows_shell, "menu_for_items", reader)
    dialog = PreferencesDialog({}, None, str(tmp_path), [str(tmp_path / "a.txt")])
    assert not calls
    menus = dialog.menus
    if not windows_shell.AVAILABLE:
        menus.load_shell_options()
    else:
        dialog.pages.setCurrentWidget(menus.shell_page)
    assert len(calls) == 3
    count = menus.shell_list.count()
    labels_ = [menus.shell_list.item(i).text() for i in range(count)]
    assert labels_ == ["Git"]  # «Cortar» ya está en el menú propio
    menus.load_shell_options()
    assert len(calls) == 3


def test_preferences_hiding_a_program_option_is_saved(app, monkeypatch, tmp_path):
    monkeypatch.setattr(windows_shell, "menu_for_folder", lambda *a: FakeShell([ShellItem(label="&Git", verb="git_gui", command=1)]))
    monkeypatch.setattr(windows_shell, "menu_for_items", lambda *a: None)
    config = {}
    dialog = PreferencesDialog(config, None, str(tmp_path))
    dialog.menus.load_shell_options()
    dialog.menus.shell_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.apply_to(config)
    assert config["context_menus"]["shell_hidden"] == {"git_gui": "Git"}
    again = PreferencesDialog(config)  # sigue en la lista aunque no se haya leído el menú, para poder recuperarla
    assert again.menus.shell_list.item(0).text() == "Git"
    assert again.menus.shell_list.item(0).checkState() == Qt.CheckState.Unchecked
    again.menus.shell_list.item(0).setCheckState(Qt.CheckState.Checked)
    again.apply_to(config)
    assert config["context_menus"]["shell_hidden"] == {}


def test_preferences_shell_failure_keeps_the_page_usable(app, monkeypatch, tmp_path):
    def fail(*_a):
        raise OSError("no")

    monkeypatch.setattr(windows_shell, "menu_for_folder", fail)
    monkeypatch.setattr(windows_shell, "menu_for_items", fail)
    dialog = PreferencesDialog({}, None, str(tmp_path))
    dialog.menus.load_shell_options()
    assert dialog.menus.shell_list.count() == 0


def test_preferences_shell_list_follows_the_main_checkbox(app):
    dialog = PreferencesDialog({})
    menus = dialog.menus
    assert menus.shell_list.isEnabled()
    menus.shell_items.setChecked(False)
    assert not menus.shell_list.isEnabled()


def test_menu_settings_travel_in_exported_configuration(window, tmp_path):
    path = tmp_path / "c.json"
    window.config["context_menus"] = {"folder": {"hidden": ["Actualizar"]}}
    window.export_settings_file(str(path))
    window.config["context_menus"] = {}
    assert window.import_settings_file(str(path)) == []
    assert "Actualizar" not in texts_of(window.build_folder_menu(window.source))
