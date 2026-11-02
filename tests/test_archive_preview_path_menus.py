"""Ver lo que hay dentro de un comprimido sin sacarlo al disco, la opción de desplegarlos y los menús del › de la ruta."""
import json
import os
import subprocess
import time
import zipfile

import pytest
from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QPoint, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import archive, settings_io
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import MainWindow


def settle(ms: int = 40) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(check, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        settle(20)
    return check()


def png_bytes(color: str = "#3366cc", size: int = 40) -> bytes:
    image = QImage(size, size + 10, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


@pytest.fixture
def tree(tmp_path):
    left = tmp_path / "izq"
    (left / "uno" / "dos").mkdir(parents=True)
    (left / "uno" / "otra").mkdir()
    (left / "sin_nada").mkdir()
    (tmp_path / "der").mkdir()
    QApplication.instance() or QApplication([])
    with zipfile.ZipFile(left / "fotos.zip", "w") as handle:
        handle.writestr("foto.png", png_bytes())
        handle.writestr("nota.txt", "hola desde el zip")
        handle.writestr("binario.bin", b"\0\1\2" * 50)
        handle.writestr("video.mp4", b"x" * 50)
        handle.writestr("rota.png", b"no soy una imagen")
    return tmp_path


@pytest.fixture
def window(tree, monkeypatch):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tree / "config"))
    main = MainWindow(Config(), [str(tree / "izq"), str(tree / "der")])
    main.resize(1000, 600)
    main.show()
    assert wait_until(lambda: main.source.model.rowCount() > 0 and main.source.model._probing is None)
    settle(60)
    yield main
    main.close()


def entry_named(view, name):
    return next(entry for entry in view.model.entries if entry.name == name)


def unfolded_zip(window):
    view = window.source
    view.model.set_expanded(entry_named(view, "fotos.zip"), True)
    assert wait_until(lambda: any(entry.in_archive for entry in view.model.entries))
    settle()
    return view


# ---- leer sin extraer ----

def member(tree, name):
    return os.path.join(str(tree / "izq" / "fotos.zip"), name)


def test_read_member_returns_its_bytes_and_writes_nothing(tree):
    before = sorted(os.listdir(tree / "izq"))
    assert archive.read_member(member(tree, "nota.txt"), 1000) == b"hola desde el zip"
    assert sorted(os.listdir(tree / "izq")) == before


def test_read_member_respects_the_limit(tree):
    assert archive.read_member(member(tree, "nota.txt"), 3) is None


def test_read_member_start_gives_only_the_beginning(tree):
    assert archive.read_member_start(member(tree, "nota.txt"), 4) == b"hola"


def test_missing_member_is_an_error(tree):
    with pytest.raises(OSError):
        archive.read_member(member(tree, "no_esta.txt"), 1000)
    with pytest.raises(OSError):
        archive.read_member(str(tree / "izq" / "fotos.zip"), 1000)


def test_a_changed_archive_is_read_again(tree):
    path = tree / "izq" / "cambia.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("a.txt", "antes")
    assert archive.read_member(os.path.join(str(path), "a.txt"), 100) == b"antes"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("a.txt", "despues del cambio")
    os.utime(path, (time.time() + 50, time.time() + 50))
    assert archive.read_member(os.path.join(str(path), "a.txt"), 100) == b"despues del cambio"


needs_tool = pytest.mark.skipif(not archive._archive_tool(), reason="sin bsdtar")


@pytest.fixture
def seven_zip(tmp_path):
    source = tmp_path / "origen"
    source.mkdir()
    QApplication.instance() or QApplication([])
    (source / "foto [1].png").write_bytes(png_bytes())
    (source / "largo.txt").write_bytes(b"linea\n" * 200000)
    path = tmp_path / "prueba.7z"
    subprocess.run([archive._archive_tool(), "-a", "-cf", str(path), "-C", str(source), "."], check=True, capture_output=True)
    return path


@needs_tool
def test_7z_member_is_read_in_memory(seven_zip):
    data = archive.read_member(os.path.join(str(seven_zip), "foto [1].png"), 10_000_000)
    assert QImage.fromData(data).width() == 40


@needs_tool
def test_stopping_early_with_7z_is_not_an_error(seven_zip):
    assert archive.read_member_start(os.path.join(str(seven_zip), "largo.txt"), 12) == b"linea\nlinea\n"


# ---- en el visor ----

def show_in_viewer(window, entry):
    window.preview.show_entry(entry)
    assert wait_until(lambda: window.preview.stack.currentWidget() is not window.preview.info or "Cargando" not in window.preview.info.text())


def test_viewer_shows_an_image_from_inside(window):
    view = unfolded_zip(window)
    show_in_viewer(window, entry_named(view, "foto.png"))
    assert window.preview.stack.currentWidget() is window.preview.image
    assert window.preview._pixmap is not None and window.preview._pixmap.width() == 40
    assert "40×50" in window.preview.title.text()


def test_viewer_shows_text_from_inside(window):
    view = unfolded_zip(window)
    show_in_viewer(window, entry_named(view, "nota.txt"))
    assert window.preview.stack.currentWidget() is window.preview.text
    assert window.preview.text.toPlainText() == "hola desde el zip"


def test_viewer_says_binary_has_no_preview(window):
    view = unfolded_zip(window)
    show_in_viewer(window, entry_named(view, "binario.bin"))
    assert window.preview.stack.currentWidget() is window.preview.info
    assert "Sin vista previa" in window.preview.info.text()


def test_viewer_explains_that_video_must_be_copied_out(window):
    view = unfolded_zip(window)
    show_in_viewer(window, entry_named(view, "video.mp4"))
    assert "cópialo fuera" in window.preview.info.text()
    assert window.preview._media_path is None


def test_viewer_says_a_damaged_image_cannot_be_read(window):
    view = unfolded_zip(window)
    show_in_viewer(window, entry_named(view, "rota.png"))
    assert "No se pudo leer" in window.preview.info.text()


def test_viewer_also_works_after_entering_the_archive(window, tree):
    view = window.source
    view.navigate(str(tree / "izq" / "fotos.zip"))
    assert wait_until(lambda: any(entry.name == "foto.png" for entry in view.model.entries))
    show_in_viewer(window, entry_named(view, "foto.png"))
    assert window.preview.stack.currentWidget() is window.preview.image


def test_selecting_it_in_the_list_shows_it(window):
    view = unfolded_zip(window)
    if not window.viewer_dock.isVisible():
        window.run_command("Set VIEWPANE=Toggle")
        settle(100)
    view.select_paths([entry_named(view, "foto.png").path])
    assert wait_until(lambda: window.preview.stack.currentWidget() is window.preview.image)


# ---- en la tarjeta y en las miniaturas ----

def test_hover_card_gets_size_and_thumbnail(window):
    view = unfolded_zip(window)
    entry = entry_named(view, "foto.png")
    card = view.hover_card
    card.show_for(entry, QPoint(50, 50))
    assert wait_until(lambda: card.isVisible())
    assert wait_until(lambda: card.thumb.pixmap().width() in (112, 40) and card.thumb.pixmap().toImage().pixelColor(card.thumb.pixmap().width() // 2, card.thumb.pixmap().height() // 2).name() == "#3366cc")
    labels = [row.text() for row in card.findChildren(type(card.title))]
    assert any("40" in text and "50" in text for text in labels)
    card.dismiss()


def test_hover_card_of_a_damaged_image_still_appears(window):
    view = unfolded_zip(window)
    card = view.hover_card
    card.show_for(entry_named(view, "rota.png"), QPoint(50, 50))
    assert wait_until(lambda: card.isVisible())
    card.dismiss()


def test_thumbnail_view_inside_an_archive(window, tree):
    window.run_command("Set VIEW=Thumbnails")
    view = window.source
    view.navigate(str(tree / "izq" / "fotos.zip"))
    assert wait_until(lambda: any(entry.name == "foto.png" for entry in view.model.entries))
    settle(100)
    path = entry_named(view, "foto.png").path
    assert wait_until(lambda: path in view.model._thumbs)


# ---- opción de desplegar comprimidos ----

def test_unfolding_archives_is_on_by_default_and_can_be_turned_off(window):
    view = window.source
    dialog = PreferencesDialog(window.config)
    assert dialog.unfold_archives.isChecked()
    assert view.model.can_unfold(entry_named(view, "fotos.zip"))
    dialog.unfold_archives.setChecked(False)
    dialog.apply_to(window.config)
    dialog.close()
    window._preferences_changed()
    assert window.config["unfold_archives"] is False
    assert not view.model.can_unfold(entry_named(view, "fotos.zip"))
    assert view.model.can_unfold(entry_named(view, "uno"))  # las carpetas siguen igual


def test_turning_it_off_folds_what_was_unfolded(window):
    view = unfolded_zip(window)
    assert any(entry.in_archive for entry in view.model.entries)
    window.config["unfold_archives"] = False
    window._preferences_changed()
    assert not any(entry.in_archive for entry in view.model.entries)
    assert not view.model.is_expanded(entry_named(view, "fotos.zip").path)


def test_new_tabs_follow_the_option(window):
    window.config["unfold_archives"] = False
    window._preferences_changed()
    new = window.groups[0].add_tab(window.source.path)
    assert new.model.unfold_archives is False


def test_entering_an_archive_still_works_with_the_option_off(window, tree):
    window.config["unfold_archives"] = False
    window._preferences_changed()
    view = window.source
    view.navigate(str(tree / "izq" / "fotos.zip"))
    assert wait_until(lambda: any(entry.name == "foto.png" for entry in view.model.entries))


def test_options_can_be_exported(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"unfold_archives": False, "path_folder_menus": True}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"unfold_archives": False, "path_folder_menus": True}, [])


# ---- el › de la ruta ----

def live_arrows(breadcrumb):
    return [breadcrumb._arrows[button] for button in breadcrumb._buttons if button in breadcrumb._arrows]


def test_off_by_default(window):
    dialog = PreferencesDialog(window.config)
    assert not dialog.path_folder_menus.isChecked()
    dialog.close()
    crumb = window.source.breadcrumb
    assert not crumb._arrows and crumb._buttons[0].text().endswith("›")


def turn_on(window):
    window.config["path_folder_menus"] = True
    window._preferences_changed()
    settle()


def test_turning_it_on_gives_each_part_its_arrow(window, tree):
    turn_on(window)
    for crumb in (window.source.breadcrumb, window.location):
        assert len(live_arrows(crumb)) == len(crumb._buttons)
        assert not any(button.text().endswith("›") for button in crumb._buttons)
        assert all(arrow.text() == "›" for arrow in live_arrows(crumb))


def test_turning_it_off_leaves_it_as_before(window):
    turn_on(window)
    window.config["path_folder_menus"] = False
    window._preferences_changed()
    assert not window.source.breadcrumb._arrows and window.source.breadcrumb._buttons[0].text().endswith("›")


def menu_of(window, crumb, position):
    arrow = live_arrows(crumb)[position]
    menu = arrow.menu()
    menu.aboutToShow.emit()
    return arrow, menu


def test_menu_lists_only_subfolders_and_marks_the_one_on_the_path(window, tree):
    turn_on(window)
    view = window.source
    view.navigate(str(tree / "izq" / "uno"))
    assert wait_until(lambda: view.breadcrumb._path == str(tree / "izq" / "uno"))
    settle()
    crumb = view.breadcrumb
    arrow, menu = menu_of(window, crumb, len(crumb._buttons) - 2)  # el › de «izq»
    texts = [action.text() for action in menu.actions()]
    assert texts == ["sin_nada", "uno"]  # sin los archivos
    assert [action.font().bold() for action in menu.actions()] == [False, True]
    assert all(not action.icon().isNull() for action in menu.actions())
    assert arrow.text() == "˅"
    menu.aboutToHide.emit()
    assert arrow.text() == "›"


def test_last_part_lists_its_own_subfolders(window, tree):
    turn_on(window)
    view = window.source
    view.navigate(str(tree / "izq" / "uno"))
    assert wait_until(lambda: view.breadcrumb._path == str(tree / "izq" / "uno"))
    settle()
    _arrow, menu = menu_of(window, view.breadcrumb, len(view.breadcrumb._buttons) - 1)
    assert [action.text() for action in menu.actions()] == ["dos", "otra"]
    assert not any(action.font().bold() for action in menu.actions())


def test_choosing_goes_there(window, tree):
    turn_on(window)
    view = window.source
    crumb = view.breadcrumb
    _arrow, menu = menu_of(window, crumb, len(crumb._buttons) - 1)
    next(action for action in menu.actions() if action.text() == "uno").trigger()
    assert wait_until(lambda: view.path == str(tree / "izq" / "uno"))


def test_location_bar_also_goes_there(window, tree):
    turn_on(window)
    crumb = window.location
    _arrow, menu = menu_of(window, crumb, len(crumb._buttons) - 1)
    next(action for action in menu.actions() if action.text() == "uno").trigger()
    assert wait_until(lambda: window.source.path == str(tree / "izq" / "uno"))


def test_folder_without_subfolders_says_so(window, tree):
    turn_on(window)
    view = window.source
    view.navigate(str(tree / "izq" / "sin_nada"))
    assert wait_until(lambda: view.breadcrumb._path == str(tree / "izq" / "sin_nada"))
    settle()
    _arrow, menu = menu_of(window, view.breadcrumb, len(view.breadcrumb._buttons) - 1)
    actions = menu.actions()
    assert len(actions) == 1 and actions[0].text() == "Sin subcarpetas" and not actions[0].isEnabled()


def test_folder_that_cannot_be_read_says_why(window, tree):
    crumb = window.source.breadcrumb
    crumb.set_folder_menus(True)
    arrow = crumb._folder_arrow(str(tree / "no_existe"), "")
    menu = arrow.menu()
    menu.aboutToShow.emit()
    assert len(menu.actions()) == 1 and not menu.actions()[0].isEnabled()


def test_arrows_hide_with_their_part_when_there_is_no_room(window, tree):
    turn_on(window)
    crumb = window.source.breadcrumb
    crumb.resize(90, crumb.height())
    settle()
    assert crumb._buttons[0].isHidden() and crumb._arrows[crumb._buttons[0]].isHidden()
    assert not crumb._buttons[-1].isHidden() and not crumb._arrows[crumb._buttons[-1]].isHidden()


def test_clicking_a_part_still_goes_there(window, tree):
    turn_on(window)
    view = window.source
    view.navigate(str(tree / "izq" / "uno"))
    assert wait_until(lambda: view.breadcrumb._path == str(tree / "izq" / "uno"))
    settle()
    view.breadcrumb._buttons[-2].click()
    assert wait_until(lambda: view.path == str(tree / "izq"))


# ---- iconos al escribir la ruta ----

def drawn(button) -> set:
    image = button.icon().pixmap(16, 16).toImage()
    return {image.pixelColor(x, y).name() for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() == 255}


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("pale", [False, True])
def test_icons_beside_the_path_stay_visible_when_it_becomes_text(window, mode, pale):
    window.config["theme"] = mode
    window.config["pane_color_scheme"] = "green-amber"
    window.config[f"pane_path_pale_{mode}"] = pale
    window._preferences_changed()
    settle()
    view = window.source
    current = theme.current()
    buttons = [view.history_button, next(button for button, icon in view._tool_buttons if icon == "copypath")]
    view.breadcrumb.begin_edit()
    settle()
    for button in buttons:
        colors = drawn(button)
        assert colors and max(theme.contrast(current.field, color) for color in colors) >= 3
    view.breadcrumb.show_buttons()
    settle()
    crumb = theme.crumb_color(current, view.property("role"))
    for button in buttons:
        assert max(theme.contrast(crumb, color) for color in drawn(button)) >= 3

