"""Desplegar comprimidos, la flecha de las carpetas vacías, el filtro de varios tipos y las preferencias en vertical."""
import json
import os
import shutil
import subprocess
import zipfile

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget

from directory_commander_tux_tux.core import archive, bar_buttons, fsops
from directory_commander_tux_tux.core.config import CONFIG_VERSION, Config
from directory_commander_tux_tux.core.listing import Entry, entry_from_path, matches_content_type
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.check_combo import CheckCombo
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import MainWindow
from directory_commander_tux_tux.ui.model import FileModel


def settle(ms: int = 40) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(check, seconds: float = 8.0) -> bool:
    for _ in range(int(seconds / 0.02)):
        if check():
            return True
        settle(20)
    return check()


@pytest.fixture
def tree(tmp_path):
    left = tmp_path / "izq"
    (left / "llena").mkdir(parents=True)
    (left / "llena" / "dentro.txt").write_text("x", encoding="utf-8")
    (left / "vacia").mkdir()
    (left / "con_subcarpeta" / "vacia_dentro").mkdir(parents=True)
    (left / "solo_oculto").mkdir()
    hidden = left / "solo_oculto" / "oculto.txt"
    hidden.write_text("x", encoding="utf-8")
    if os.name == "nt":
        subprocess.run(["attrib", "+h", str(hidden)], check=True)
    else:
        os.rename(hidden, left / "solo_oculto" / ".oculto.txt")
    (left / "a.txt").write_text("x", encoding="utf-8")
    (left / "foto.jpg").write_bytes(b"x")
    (left / "tema.mp3").write_bytes(b"x")
    with zipfile.ZipFile(left / "paquete.zip", "w") as handle:
        handle.writestr("primero.txt", "uno")
        handle.writestr("sub/segundo.txt", "dos dos")
    (tmp_path / "der").mkdir()
    return tmp_path


@pytest.fixture
def window(tree, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tree / "config"))
    main = MainWindow(Config(), [str(tree / "izq"), str(tree / "der")])
    main.resize(1000, 500)
    main.show()
    assert wait_until(lambda: main.source.model.rowCount() > 0 and main.source.model._probing is None)
    settle(60)
    yield main
    main.close()


def entry_named(view, name: str):
    return next(entry for entry in view.model.entries if entry.name == name)


def names(view) -> list[str]:
    return [entry.name for entry in view.model.entries]


def unfolded(view, entry) -> None:
    view.model.set_expanded(entry, True)
    assert wait_until(lambda: view.model.is_expanded(entry.path) and view.model._probing is None)
    settle()


# ---- carpetas vacías ----

def test_only_folders_with_something_get_the_arrow(window):
    model = window.source.model
    assert model.can_unfold(entry_named(window.source, "llena"))
    assert model.can_unfold(entry_named(window.source, "con_subcarpeta"))
    assert not model.can_unfold(entry_named(window.source, "vacia"))


def test_folder_with_only_hidden_files_follows_the_option(window, tree):
    view = window.source
    assert not view.model.can_unfold(entry_named(view, "solo_oculto"))
    view.show_hidden = True
    view.refresh()
    assert wait_until(lambda: view.model._probing is None and view.model.rowCount() > 0)
    settle(100)
    assert wait_until(lambda: view.model.can_unfold(entry_named(view, "solo_oculto")))


def test_empty_folder_cannot_be_unfolded(window):
    view = window.source
    entry = entry_named(view, "vacia")
    view.model.set_expanded(entry, True)
    settle(80)
    assert not view.model.is_expanded(entry.path)


def test_empty_folder_inside_an_unfolded_one_loses_it_too(window):
    view = window.source
    unfolded(view, entry_named(view, "con_subcarpeta"))
    assert not view.model.can_unfold(entry_named(view, "vacia_dentro"))


def test_folder_that_gets_something_regains_it_when_refreshing(window, tree):
    view = window.source
    assert not view.model.can_unfold(entry_named(view, "vacia"))
    (tree / "izq" / "vacia" / "nuevo.txt").write_text("x", encoding="utf-8")
    view.refresh()
    assert wait_until(lambda: view.model.can_unfold(entry_named(view, "vacia")))


def test_folder_emptied_since_it_was_checked_loses_it_on_unfolding(window, tree):
    view = window.source
    entry = entry_named(view, "llena")
    (tree / "izq" / "llena" / "dentro.txt").unlink()
    assert view.model.can_unfold(entry)
    view.model.set_expanded(entry, True)
    assert wait_until(lambda: not view.model.can_unfold(entry))
    assert not view.model.is_expanded(entry.path)


def test_folder_that_cannot_be_read_keeps_the_arrow():
    from directory_commander_tux_tux.core.listing import has_visible_entries

    assert has_visible_entries(Entry("no", os.path.join("no", "existe", "aqui"), True), False)


def test_drives_are_not_probed(qapp_for_gc, monkeypatch):
    model = FileModel()
    calls = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.model.run_bg", lambda *a, **k: calls.append(a))
    model._probe_folders([Entry("C:", "C:\\", True, is_drive=True)])
    assert not calls


def test_flat_view_has_no_arrows(window):
    view = window.source
    view.model.can_expand = False
    assert not view.model.can_unfold(entry_named(view, "llena"))
    assert not view.model.can_unfold(entry_named(view, "paquete.zip"))
    view.model.can_expand = True


def chevron_pixels_at(view, name: str) -> int:
    """Lo que hay dibujado donde va la flecha, distinto del fondo."""
    list_view = view.current_view
    index = list_view.model().index(view.model.row_of(entry_named(view, name).path), 0)
    rect = list_view.visualRect(index)
    image = list_view.viewport().grab().toImage()
    box = [image.pixelColor(x, y).name() for x in range(rect.left() + 1, rect.left() + 13) for y in range(rect.top(), rect.top() + rect.height())]
    return sum(color != box[0] for color in box)


@pytest.mark.parametrize("mode", ["List", "Details"])
def test_arrow_is_drawn_or_not(window, mode):
    window.run_command(f"Set VIEW={mode}")
    settle(120)
    view = window.source
    assert chevron_pixels_at(view, "llena") > 0
    assert chevron_pixels_at(view, "vacia") == 0
    assert chevron_pixels_at(view, "paquete.zip") > 0
    assert chevron_pixels_at(view, "a.txt") == 0


# ---- comprimidos ----

def test_archives_get_the_arrow_and_plain_files_do_not(window):
    model = window.source.model
    assert model.can_unfold(entry_named(window.source, "paquete.zip"))
    assert not model.can_unfold(entry_named(window.source, "a.txt"))


def test_unfolding_an_archive_shows_its_content(window):
    view = window.source
    zipped = entry_named(view, "paquete.zip")
    unfolded(view, zipped)
    found = names(view)
    assert found.index("primero.txt") == found.index("paquete.zip") + 2 or found.index("sub") == found.index("paquete.zip") + 1
    child = entry_named(view, "primero.txt")
    assert child.depth == 1 and child.in_archive and child.path == os.path.join(zipped.path, "primero.txt")
    sub = entry_named(view, "sub")
    assert sub.is_dir and view.model.can_unfold(sub)
    unfolded(view, sub)
    assert entry_named(view, "segundo.txt").depth == 2


def test_folding_it_again_hides_everything(window):
    view = window.source
    zipped = entry_named(view, "paquete.zip")
    unfolded(view, zipped)
    unfolded(view, entry_named(view, "sub"))
    view.model.set_expanded(zipped, False)
    settle()
    assert "primero.txt" not in names(view) and "segundo.txt" not in names(view)
    assert not view.model.is_expanded(os.path.join(zipped.path, "sub"))


def test_clicking_the_arrow_of_an_archive_unfolds_it(window):
    view = window.source
    window.run_command("Set VIEW=Details")
    settle(120)
    details = view.details
    entry = entry_named(view, "paquete.zip")
    rect = details.visualRect(details.model().index(view.model.row_of(entry.path), 0))
    point = QPointF(QPoint(rect.left() + 6, rect.center().y()))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, point, details.viewport().mapToGlobal(point), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(details.viewport(), press)
    assert wait_until(lambda: view.model.is_expanded(entry.path))


def test_right_key_unfolds_an_archive(window):
    view = window.source
    window.run_command("Set VIEW=Details")
    settle(120)
    entry = entry_named(view, "paquete.zip")
    view.select_paths([entry.path])
    view.details.setCurrentIndex(view.model.index(view.model.row_of(entry.path), 0))
    view.details.setFocus()
    QTest.keyClick(view.details, Qt.Key.Key_Right)
    assert wait_until(lambda: view.model.is_expanded(entry.path))
    QTest.keyClick(view.details, Qt.Key.Key_Left)
    assert wait_until(lambda: not view.model.is_expanded(entry.path))


def test_it_stays_unfolded_after_refreshing(window):
    view = window.source
    zipped = entry_named(view, "paquete.zip")
    unfolded(view, zipped)
    view.refresh()
    assert wait_until(lambda: "primero.txt" in names(view))


def test_a_damaged_archive_says_so_and_does_not_unfold(window, tree):
    view = window.source
    (tree / "izq" / "roto.zip").write_bytes(b"esto no es un zip")
    view.refresh()
    assert wait_until(lambda: "roto.zip" in names(view))
    errors = []
    view.model.renameFailed.connect(errors.append)
    entry = entry_named(view, "roto.zip")
    view.model.set_expanded(entry, True)
    assert wait_until(lambda: bool(errors))
    assert not view.model.is_expanded(entry.path)


def test_an_empty_archive_loses_the_arrow_when_trying(window, tree):
    view = window.source
    with zipfile.ZipFile(tree / "izq" / "vacio.zip", "w"):
        pass
    view.refresh()
    assert wait_until(lambda: "vacio.zip" in names(view))
    entry = entry_named(view, "vacio.zip")
    assert view.model.can_unfold(entry)
    view.model.set_expanded(entry, True)
    assert wait_until(lambda: not view.model.can_unfold(entry))


def test_a_zip_inside_a_zip_has_no_arrow(window, tree):
    view = window.source
    with zipfile.ZipFile(tree / "izq" / "doble.zip", "w") as handle:
        handle.writestr("otro.zip", b"x")
    view.refresh()
    assert wait_until(lambda: "doble.zip" in names(view))
    unfolded(view, entry_named(view, "doble.zip"))
    assert not view.model.can_unfold(entry_named(view, "otro.zip"))


def test_members_shown_in_the_list_cannot_be_renamed_or_changed(window):
    view = window.source
    unfolded(view, entry_named(view, "paquete.zip"))
    entry = entry_named(view, "primero.txt")
    index = view.model.index(view.model.row_of(entry.path), 0)
    assert not view.model.flags(index) & Qt.ItemFlag.ItemIsEditable
    view.select_paths([entry.path])
    menu = window._build_selection_menu(view) if hasattr(window, "_build_selection_menu") else None
    if menu is not None:
        texts = {action.text(): action.isEnabled() for action in menu.actions()}
        assert texts.get("Renombrar") is False and texts.get("Enviar a la papelera") is False


def test_member_shown_in_the_list_can_be_copied(window, tree):
    view = window.source
    unfolded(view, entry_named(view, "paquete.zip"))
    view.select_paths([entry_named(view, "primero.txt").path])
    window.run_command("Copy")
    assert wait_until(lambda: (tree / "der" / "primero.txt").exists() and window.jobs.running == 0)
    assert (tree / "der" / "primero.txt").read_text(encoding="utf-8") == "uno"
    assert (tree / "izq" / "paquete.zip").exists()


# ---- RAR y 7z, con el programa que los lee ----

needs_tool = pytest.mark.skipif(not archive._archive_tool(), reason="sin bsdtar")


@pytest.fixture
def seven_zip(tmp_path):
    source = tmp_path / "origen"
    (source / "sub carpeta").mkdir(parents=True)
    (source / "vacía").mkdir()
    (source / "uno.txt").write_text("uno", encoding="utf-8")
    (source / "sub carpeta" / "dos año [1].txt").write_text("dos dos", encoding="utf-8")
    path = tmp_path / "prueba.7z"
    subprocess.run([archive._archive_tool(), "-a", "-cf", str(path), "-C", str(source), "."], check=True, capture_output=True)
    return path


@needs_tool
def test_7z_is_read_like_any_other(seven_zip):
    assert archive.is_archive_file(str(seven_zip))
    members = {member.name: member for member in archive.list_members(str(seven_zip))}
    assert set(members) == {"uno.txt", "sub carpeta", "sub carpeta/dos año [1].txt", "vacía"}
    assert members["sub carpeta"].is_dir and members["uno.txt"].size == 3 and members["uno.txt"].mtime > 0


@needs_tool
def test_7z_member_with_special_characters_comes_out(seven_zip, tmp_path):
    out = tmp_path / "salida"
    out.mkdir()
    archive.extract_to(str(seven_zip), "sub carpeta/dos año [1].txt", str(out))
    assert (out / "dos año [1].txt").read_text(encoding="utf-8") == "dos dos"
    archive.extract_to(str(seven_zip), "sub carpeta", str(out))
    assert (out / "sub carpeta" / "dos año [1].txt").exists()


@needs_tool
def test_copying_out_of_a_7z_uses_the_normal_path(seven_zip, tmp_path):
    out = tmp_path / "copia"
    out.mkdir()
    result = fsops.transfer([os.path.join(str(seven_zip), "uno.txt")], str(out))
    assert not result.errors and (out / "uno.txt").read_text(encoding="utf-8") == "uno"


@needs_tool
def test_damaged_7z_gives_an_error(tmp_path):
    bad = tmp_path / "malo.7z"
    bad.write_bytes(b"nada")
    with pytest.raises(OSError):
        archive.list_members(str(bad))


@needs_tool
def test_cancelling_while_reading_does_not_leave_the_program_running(seven_zip, tmp_path):
    def cancel(_size):
        raise KeyboardInterrupt

    out = tmp_path / "salida"
    out.mkdir()
    with pytest.raises(KeyboardInterrupt):
        archive.extract_to(str(seven_zip), "uno.txt", str(out), cancel)
    assert list(out.iterdir()) == []


def test_without_the_program_rar_is_just_a_file(monkeypatch):
    monkeypatch.setattr(archive, "_archive_tool", lambda: "")
    assert not archive.has_archive_suffix("algo.rar") and not archive.has_archive_suffix("algo.7z")
    assert archive.has_archive_suffix("algo.zip")


def test_listing_understands_dates_in_several_languages():
    assert archive._tool_time("nov.", "2", "2020") > 0 and archive._tool_time("Jan", "15", "2021") > 0 and archive._tool_time("ene.", "15", "2021") > 0
    assert archive._tool_time("ene.", "15", "2021") == archive._tool_time("Jan", "15", "2021")
    assert archive._tool_time("???", "15", "2021") == 0.0


def test_listing_never_trusts_names_that_leave_the_folder(monkeypatch):
    class Listed:
        returncode = 0
        stdout = b"-rw-r--r--  0 0      0           7 nov. 02 00:21 ../fuera.txt\r\n-rw-r--r--  0 0      0           3 nov. 02 00:21 bien.txt\r\n"
        stderr = b""

    monkeypatch.setattr(archive, "_run_tool", lambda _arguments: Listed)
    monkeypatch.setattr(archive, "_archive_tool", lambda: "bsdtar")
    assert [member.name for member in archive._read_tool_members("x.rar")] == ["bien.txt"]


# ---- filtro de contenido ----

def test_several_types_can_pass():
    photo = Entry("a.jpg", "a.jpg", False)
    song = Entry("a.mp3", "a.mp3", False)
    text = Entry("a.txt", "a.txt", False)
    assert matches_content_type(photo, ("images", "audio")) and matches_content_type(song, ("images", "audio"))
    assert not matches_content_type(text, ("images", "audio"))
    assert matches_content_type(text, ()) and matches_content_type(text, "") and matches_content_type(photo, "images")


def test_filter_label_has_its_new_name(window):
    labels = [label.text() for label in window.location_bar.findChildren(type(window.location_folder))]
    assert "Filtro de contenido" in labels
    assert ("content_type", "Filtro de contenido") in dict((bar, buttons) for bar, _title, buttons in bar_buttons.bars(window.config))[bar_buttons.LOCATION]


def test_command_accepts_several(window):
    view = window.source
    window.run_command("Set CONTENTTYPE=images,audio")
    assert window.content_box.checked_keys() == ("images", "audio")
    found = names(view)
    assert "foto.jpg" in found and "tema.mp3" in found and "a.txt" not in found and "llena" in found
    window.run_command("Set CONTENTTYPE=none")
    assert "a.txt" in names(view) and window.content_box.checked_keys() == ()


def test_unknown_type_is_rejected(window, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: None)
    window.run_command("Set CONTENTTYPE=images,inventado")
    assert window.source.model.content_types == ()


def popup_click(combo, row: int) -> None:
    view = combo.view()
    rect = view.visualRect(combo.model().index(row, 0))
    point = QPointF(rect.center())
    QApplication.sendEvent(view.viewport(), QMouseEvent(QEvent.Type.MouseButtonRelease, point, view.viewport().mapToGlobal(point),
                                                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def test_marking_keeps_the_list_open_and_filters_at_once(window):
    combo, view = window.content_box, window.source
    combo.showPopup()
    settle()
    assert combo.view().isVisible()
    keys = [combo.model().item(row).data(Qt.ItemDataRole.UserRole) for row in range(combo.model().rowCount())]
    popup_click(combo, keys.index("images"))
    popup_click(combo, keys.index("audio"))
    settle()
    assert combo.view().isVisible()
    assert combo.checked_keys() == ("images", "audio") and view.model.content_types == ("images", "audio")
    assert "a.txt" not in names(view) and "foto.jpg" in names(view) and "tema.mp3" in names(view)
    assert combo.summary() == "Imágenes, Audio"
    popup_click(combo, keys.index("images"))
    assert combo.checked_keys() == ("audio",) and "foto.jpg" not in names(view)
    popup_click(combo, 0)
    settle()
    assert combo.checked_keys() == () and combo.summary() == "<Ninguno>" and "a.txt" in names(view)
    combo.hidePopup()


def test_space_marks_with_the_keyboard(window):
    combo = window.content_box
    combo.showPopup()
    settle()
    combo.view().setCurrentIndex(combo.model().index(1, 0))
    QTest.keyClick(combo.view(), Qt.Key.Key_Space)
    assert len(combo.checked_keys()) == 1
    combo.hidePopup()


def test_each_pane_keeps_its_filter_and_the_list_shows_it(window):
    window.run_command("Set CONTENTTYPE=images,audio")
    window._view_activated(window.groups[1].current)
    settle()
    assert window.content_box.checked_keys() == ()
    window._view_activated(window.groups[0].current)
    settle()
    assert window.content_box.checked_keys() == ("images", "audio")


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_a_marked_row_shows_a_mark_and_keeps_its_box(window, mode):
    window.config["theme"] = mode
    window._preferences_changed()
    combo = window.content_box
    combo.showPopup()
    settle()
    view = combo.view()
    view.setCurrentIndex(combo.model().index(0, 0))
    settle()

    def box(row):
        image = view.viewport().grab(view.visualRect(combo.model().index(row, 0))).toImage()
        return [image.pixelColor(x, y).name() for x in range(22) for y in range(image.height())]

    def ink(colors):
        background = max(set(colors), key=colors.count)
        return sum(theme.contrast(background, color) >= 3 for color in colors)

    empty = box(2)
    assert ink(empty) > 15  # la casilla vacía se ve
    view.setCurrentIndex(combo.model().index(2, 0))
    settle()
    empty_highlighted = box(2)  # sobre el color de resaltar se ve menos que sobre el fondo: hay que comparar con ella
    view.setCurrentIndex(combo.model().index(0, 0))
    settle()
    popup_click(combo, 2)
    combo.view().setCurrentIndex(combo.model().index(0, 0))
    settle()
    assert ink(box(2)) > ink(empty) + 5  # y la marcada se ve más, también con la fila sin resaltar
    combo.view().setCurrentIndex(combo.model().index(2, 0))
    settle()
    assert ink(box(2)) > ink(empty_highlighted) + 5  # y resaltada
    popup_click(combo, 2)
    combo.hidePopup()


def test_long_summary_is_cut_in_the_box(qapp_for_gc):
    combo = CheckCombo("<Ninguno>")
    for number in range(4):
        combo.add_option(f"Tipo larguísimo número {number}", str(number))
    combo.resize(120, 24)
    combo.set_checked_keys(["0", "1", "2", "3"])
    combo.show()
    combo.grab()
    assert "número 3" in combo.toolTip()
    combo.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_list_with_marks_can_be_read(window, mode):
    window.config["theme"] = mode
    window._preferences_changed()
    combo = window.content_box
    combo.showPopup()
    settle()
    view = combo.view()
    index = combo.model().index(2, 0)
    view.setCurrentIndex(index)
    settle()
    image = view.viewport().grab(view.visualRect(index)).toImage()
    counts = {}
    for x in range(image.width()):
        for y in range(image.height()):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    background = max(counts, key=counts.get)
    assert max(theme.contrast(background, color) for color in counts) >= 4.5
    combo.hidePopup()


# ---- preferencias ----

def navigation_tree(dialog) -> list:
    tree = dialog.pages.navigation
    result = []
    for row in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(row)
        result.append((item.text(0), [item.child(position).text(0) for position in range(item.childCount())]))
    return result


def test_pages_are_vertical_and_grouped(window):
    dialog = PreferencesDialog(window.config)
    assert not isinstance(dialog.pages, QTabWidget)
    tree = dict(navigation_tree(dialog))
    assert tree["General"] == ["Idioma y avisos", "Vistas y miniaturas", "Abrir archivos", "Colores y marcos", "Programas"]
    assert tree["Pestañas"] == ["Barra", "Dimensiones", "Opciones"]
    assert tree["Barras y estilos"] == ["Barras de botones", "Estilos"]
    assert tree["Rutas frecuentes"] == ["Lista de Favoritos", "Lista de Recientes", "Favoritos inteligentes", "Puntaje"]
    assert tree["Menús"][0] == "Menús contextuales"
    dialog.close()


def test_choosing_a_group_goes_to_its_first_page(window):
    dialog = PreferencesDialog(window.config)
    pages = dialog.pages
    tree = pages.navigation
    group = next(tree.topLevelItem(row) for row in range(tree.topLevelItemCount()) if tree.topLevelItem(row).text(0) == "Rutas frecuentes")
    tree.setCurrentItem(group)
    assert pages.currentWidget() is dialog.favorites_page
    assert tree.currentItem() is group.child(0)
    dialog.close()


def test_every_page_can_be_reached_from_the_list(window):
    dialog = PreferencesDialog(window.config)
    pages = dialog.pages
    for index in range(pages.count()):
        pages.setCurrentIndex(index)
        assert pages.currentIndex() == index and pages.currentWidget() is pages.widget(index)
        assert pages.navigation.currentItem().text(0) == pages.tabText(index)
    dialog.close()


def test_opening_on_a_given_page_marks_it_in_the_list(window):
    dialog = PreferencesDialog(window.config)
    dialog.show_styles()
    assert dialog.pages.navigation.currentItem().text(0) == "Estilos"
    dialog.show_favorites()
    assert dialog.pages.navigation.currentItem().text(0) == "Lista de Favoritos"
    dialog.close()


# ---- por defecto ----

def test_paler_path_is_off_by_default(window):
    assert theme.LIGHT.pale_path is False and theme.DARK.pale_path is False
    dialog = PreferencesDialog(window.config)
    assert not dialog.path_pale_light.isChecked() and not dialog.path_pale_dark.isChecked()
    dialog.close()
    assert theme.crumb_color(theme.LIGHT, "source") == theme.LIGHT.bar_source


def test_who_had_it_saved_without_choosing_gets_the_new_default(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 12, "pane_path_pale_light": True, "pane_path_pale_dark": True}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["pane_path_pale_light"] is False
    assert config["pane_path_pale_dark"] is True
    assert config["config_version"] == CONFIG_VERSION


def test_a_choice_made_after_that_is_kept(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": CONFIG_VERSION, "pane_path_pale_light": True}), encoding="utf-8")
    assert Config(str(tmp_path))["pane_path_pale_light"] is True


def test_no_visible_text_says_de_serie():
    root = os.path.join(os.path.dirname(__file__), "..", "directory_commander_tux_tux", "languages", "en.json")
    with open(root, encoding="utf-8") as handle:
        keys = json.load(handle)["strings"]
    assert not [key for key in keys if "de serie" in key]
    assert entry_from_path is not None and shutil is not None
