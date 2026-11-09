"""Botón Aplicar, botón de tema, Personalizar… y menú al arrastrar con el botón derecho."""
import json
import os

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMenu, QToolButton

from directory_commander_tux_tux.core import fsops, windows_shell
from directory_commander_tux_tux.core.config import CONFIG_VERSION, THEME_TOGGLE_COMMAND, Config
from directory_commander_tux_tux.core.windows_shell import ShellItem
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from tests.test_ui import (ANY_ACTION, NO_KEYS, FakeDrag, FakeShell, app, file_mime, image_folder, name_center, names, select, send_to_view, settle, strip_point,  # noqa: F401
                           tree, tree_item, viewer, wait_until, window)

RIGHT = Qt.MouseButton.RightButton


# ---------------------------------------------------------------- Aplicar
def test_apply_saves_and_keeps_the_window_open(app):
    config = {"confirm_delete": True}
    dialog = PreferencesDialog(config)
    dialog.show()
    applied = []
    dialog.applied.connect(lambda: applied.append(1))
    dialog.confirm.setChecked(False)
    dialog.apply_button.click()
    assert config["confirm_delete"] is False and applied == [1] and dialog.isVisible()
    dialog.close()


def test_buttons_of_the_preferences(app):
    dialog = PreferencesDialog({})
    box = dialog.findChild(QDialogButtonBox)
    assert box.button(QDialogButtonBox.StandardButton.Ok) and box.button(QDialogButtonBox.StandardButton.Cancel)
    assert dialog.apply_button.text() == "Aplicar" and not dialog.apply_button.isDefault()


def accept_with(window, monkeypatch, change, result=0):
    """Abre las preferencias de verdad y hace `change` con ellas abiertas, en vez de esperar a que alguien pulse."""
    def run(dialog):
        change(dialog)
        return result

    monkeypatch.setattr(PreferencesDialog, "exec", run)
    window.run_command("Prefs")


def test_apply_takes_effect_on_the_window(window, monkeypatch):
    def change(dialog):
        dialog.bar_buttons.lists["toolbar"].item(0).setCheckState(Qt.CheckState.Unchecked)
        dialog.apply_button.click()
        assert window.toolbar.actions()[0].text() != "Atrás"  # con las preferencias aún abiertas

    accept_with(window, monkeypatch, change)
    assert window.config["bar_buttons"]["toolbar"] == ["Atrás"]  # cancelar después no deshace lo aplicado


def test_cancel_without_applying_changes_nothing(window, monkeypatch):
    accept_with(window, monkeypatch, lambda dialog: dialog.confirm.setChecked(False))
    assert window.config["confirm_delete"] is True


def test_applied_theme_changes_while_open(window, monkeypatch):
    colors = []

    def change(dialog):
        dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
        dialog.apply_button.click()
        colors.append(theme.current().name)

    accept_with(window, monkeypatch, change)
    assert colors == ["light"]
    window.config["theme"] = "dark"
    window.apply_theme()


def test_language_applied_asks_to_restart_only_after_closing(window, monkeypatch):
    offers = []
    monkeypatch.setattr(window, "_offer_restart", lambda: offers.append(1))

    def change(dialog):
        dialog.language.setCurrentIndex(dialog.language.findData("en"))
        dialog.apply_button.click()
        settle()
        assert not offers

    accept_with(window, monkeypatch, change)
    settle()
    assert offers == [1]


# ---------------------------------------------------------------- tema
def theme_action(window):
    return next(action for action in window.toolbar.actions() if action.text() == "Cambiar tema")


def test_toolbar_has_a_theme_button_that_toggles(window):
    action = theme_action(window)
    assert not action.icon().isNull() and theme.current().name == "dark"
    window.toolbar.widgetForAction(action).click()
    settle()
    assert window.config["theme"] == "light" and theme.current().name == "light"
    window.toolbar.widgetForAction(theme_action(window)).click()  # el botón es nuevo: la barra se reconstruye con el tema
    settle()
    assert window.config["theme"] == "dark" and theme.current().name == "dark"


def test_theme_button_from_following_the_desktop(window, monkeypatch):
    monkeypatch.setattr(theme, "resolve", lambda name: theme.THEMES["light"] if name == "auto" else theme.THEMES.get(name, theme.DARK))
    window.config["theme"] = "auto"
    window.apply_theme()
    assert theme.current().name == "light"
    window.run_command(THEME_TOGGLE_COMMAND)
    assert window.config["theme"] == "dark"


@pytest.mark.parametrize("stored,expected_extra", [([{"label": "Mío", "commands": ["Go UP"], "children": []}], 9), ([{"label": "x", "commands": [THEME_TOGGLE_COMMAND]}], 7)])
def test_old_configuration_gets_the_theme_button(tmp_path, stored, expected_extra):
    # Los botones de metadatos, de la terminal, de comparar y de los marcos también se añaden a las barras anteriores a la v6.
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 3, "toolbar": stored}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["config_version"] == CONFIG_VERSION and len(config["toolbar"]) == len(stored) + expected_extra
    assert sum(THEME_TOGGLE_COMMAND in (item.get("commands") or []) for item in config["toolbar"]) == 1


def test_button_removed_by_the_user_does_not_come_back(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": CONFIG_VERSION, "toolbar": [{"label": "Mío", "commands": ["Go UP"]}]}), encoding="utf-8")
    assert len(Config(str(tmp_path))["toolbar"]) == 1


# ---------------------------------------------------------------- Personalizar…
def customize_of(menu):
    return next(action for action in menu.actions() if action.text() == "Personalizar…")


def opened_preferences(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "open_preferences", lambda bars=None, tabs=False, parent=None: opened.append((bars, tabs, parent)))
    return opened


def test_right_click_on_a_bar_offers_customize(window, monkeypatch):
    opened = opened_preferences(window, monkeypatch)
    monkeypatch.setattr(window, "_bar_under_cursor", lambda: "styles")
    menu = window.createPopupMenu()
    labels = [action.text() for action in menu.actions()]
    assert "Bloquear las barras" in labels and "Barra de herramientas" in labels and labels[-1] == "Personalizar…"  # lo de antes sigue
    customize_of(menu).trigger()
    settle()
    assert opened == [("styles", False, None)]


def test_bar_under_the_cursor(window, monkeypatch):
    window.resize(1200, 700)
    settle()
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QApplication.widgetAt", lambda _pos: window.style_bar.widgetForAction(window.style_bar.actions()[1]))
    assert window._bar_under_cursor() == "styles"
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QApplication.widgetAt", lambda _pos: window.toolbar)
    assert window._bar_under_cursor() == "toolbar"
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QApplication.widgetAt", lambda _pos: None)
    assert window._bar_under_cursor() == "toolbar"


def test_customize_opens_the_page_of_that_bar(window, monkeypatch):
    seen = []

    def run(dialog):
        seen.append((dialog.pages.currentWidget() is dialog.bar_buttons, dialog.bar_buttons.stack.currentWidget() is dialog.bar_buttons.lists["pane"]))
        return 0

    monkeypatch.setattr(PreferencesDialog, "exec", run)
    window.open_preferences("pane")
    assert seen == [(True, True)]


def test_pane_bar_offers_customize(window, monkeypatch):
    seen = []
    monkeypatch.setattr(PreferencesDialog, "exec", lambda dialog: seen.append(dialog.bar_buttons.stack.currentWidget() is dialog.bar_buttons.lists["pane"]) or 0)
    customize_of(window.source.build_pane_bar_menu()).trigger()
    assert wait_until(lambda: seen == [True])


def test_tab_bar_offers_customize(window, monkeypatch):
    opened = opened_preferences(window, monkeypatch)
    group = window.groups[0]
    for index in (0, -1):
        customize_of(group.tab_menu(index)).trigger()
    assert wait_until(lambda: len(opened) == 2) and opened[0] == (None, True, None)


def test_customize_tabs_opens_the_tab_page(window, monkeypatch):
    seen = []
    monkeypatch.setattr(PreferencesDialog, "exec", lambda dialog: seen.append(dialog.pages.currentWidget() is dialog.tabs.pages[0][1]) or 0)
    window.open_preferences(tabs=True)
    assert seen == [True]


def test_image_viewer_bar_offers_customize(window, viewer, monkeypatch):
    opened = opened_preferences(window, monkeypatch)
    customize_of(viewer.createPopupMenu()).trigger()
    assert wait_until(lambda: opened == [("image_viewer", False, viewer)])


def test_viewer_without_a_window_cannot_customize(app, image_folder):
    from directory_commander_tux_tux.ui.image_viewer import ImageViewer

    alone = ImageViewer([str(image_folder / "uno.png")])
    assert not customize_of(alone.createPopupMenu()).isEnabled()
    alone.close()


@pytest.fixture
def video_window(window, tree):
    from directory_commander_tux_tux.ui.video_viewer import VideoViewer

    left, _ = tree
    shown = VideoViewer(window.config, [str(left / "a.txt")], host=window)
    yield shown
    shown.close()


def test_video_window_offers_customize(window, video_window, monkeypatch):
    opened = opened_preferences(window, monkeypatch)
    customize_of(video_window.createPopupMenu()).trigger()
    customize_of(video_window.media.build_controls_menu()).trigger()
    assert wait_until(lambda: opened == [("video_viewer", False, video_window), ("video_controls", False, video_window)])


def test_side_player_controls_offer_customize(window, monkeypatch):
    from directory_commander_tux_tux.ui.media_player import MediaPlayer

    opened = opened_preferences(window, monkeypatch)
    window.preview.media = media = MediaPlayer(window.config)
    media.customizeRequested.connect(lambda: window.preview.customize_controls())
    customize_of(media.build_controls_menu()).trigger()
    assert wait_until(lambda: opened == [("player_controls", False, None)])
    window.preview.media = None


# ---------------------------------------------------------------- arrastrar con el botón derecho
def right_drop(point, paths, keys=NO_KEYS, actions=ANY_ACTION, buttons=RIGHT):
    return QDropEvent(QPointF(point), actions, file_mime(*paths), buttons, keys)


@pytest.fixture
def menus(window, monkeypatch):
    """El menú que se habría mostrado, antes de que se cierre."""
    shown = []

    def run(menu, _position):
        shown.append(menu)
        for callback in run.during:
            callback(menu)

    run.during = []
    monkeypatch.setattr(window, "_run_menu", run)
    window._test_menu_run = run
    return shown


def labels_of(menu):
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def choose(window, label):
    def pick(menu):
        next(action for action in menu.actions() if action.text() == label).trigger()

    window._test_menu_run.during.append(pick)


def test_right_drop_asks_instead_of_acting(window, tree, menus):
    left, right = tree
    event = send_to_view(window.dest, right_drop(window.dest.current_view.viewport().rect().center(), [left / "a.txt"]))
    assert event.isAccepted()
    assert wait_until(lambda: menus)
    assert labels_of(menus[0])[-4:] == ["Copiar aquí", "Mover aquí", "Crear acceso directo aquí", "Cancelar"]
    assert (left / "a.txt").exists() and not (right / "a.txt").exists()  # nada se ha hecho todavía


def own_menu_only(monkeypatch):
    monkeypatch.setattr(windows_shell, "drop_handlers", lambda *a: [])


def test_order_of_the_menu_and_bold_default(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, _ = tree
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    menu = menus[0]
    assert labels_of(menu) == ["Copiar aquí", "Mover aquí", "Crear acceso directo aquí", "Cancelar"]
    assert menu.defaultAction().text() == "Mover aquí"
    assert [action.isSeparator() for action in menu.actions()] == [False, False, False, True, False]
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, False)
    assert menus[1].defaultAction().text() == "Copiar aquí"


def test_copy_here(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, right = tree
    choose(window, "Copiar aquí")
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert (left / "a.txt").exists()


def test_move_here(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, right = tree
    choose(window, "Mover aquí")
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, False)
    assert wait_until(lambda: (right / "a.txt").exists() and not (left / "a.txt").exists() and window.jobs.running == 0)


def test_cancel_does_nothing(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, right = tree
    choose(window, "Cancelar")
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    settle()
    assert (left / "a.txt").exists() and os.listdir(right) == [] and window.jobs.running == 0


def test_into_a_folder_of_the_list(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, _ = tree
    choose(window, "Mover aquí")
    send_to_view(window.source, right_drop(name_center(window.source, "subcarpeta"), [left / "a.txt"]))
    assert wait_until(lambda: (left / "subcarpeta" / "a.txt").exists() and window.jobs.running == 0)


def test_left_drop_is_not_asked(window, tree, menus):
    left, right = tree
    send_to_view(window.dest, right_drop(window.dest.current_view.viewport().rect().center(), [left / "a.txt"], buttons=Qt.MouseButton.LeftButton))
    assert wait_until(lambda: (right / "a.txt").exists() and window.jobs.running == 0)
    assert not menus


def test_button_is_remembered_from_the_drag_because_at_drop_it_is_released(window, tree, menus, monkeypatch):
    """Al soltar ya no hay ningún botón pulsado."""
    own_menu_only(monkeypatch)
    left, _ = tree
    widget = window.dest.current_view
    center = widget.viewport().rect().center()
    widget.dragEnterEvent(QDragEnterEvent(center, ANY_ACTION, file_mime(left / "a.txt"), RIGHT, NO_KEYS))
    widget.dragMoveEvent(QDragMoveEvent(center, ANY_ACTION, file_mime(left / "a.txt"), RIGHT, NO_KEYS))
    widget.dropEvent(right_drop(center, [left / "a.txt"], buttons=Qt.MouseButton.NoButton))
    assert wait_until(lambda: menus)
    menus.clear()
    # El siguiente arrastre, con el izquierdo, ya no pregunta.
    widget.dragEnterEvent(QDragEnterEvent(center, ANY_ACTION, file_mime(left / "b.txt"), Qt.MouseButton.LeftButton, NO_KEYS))
    widget.dropEvent(right_drop(center, [left / "b.txt"], buttons=Qt.MouseButton.NoButton))
    assert wait_until(lambda: window.jobs.running == 0)
    settle()
    assert not menus


def test_on_the_folder_tree(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, right = tree
    item = tree_item(window, right)
    monkeypatch.setattr(window.tree, "itemAt", lambda *_a: item)
    window.tree.dropEvent(right_drop(QPoint(5, 5), [left / "a.txt"]))
    assert wait_until(lambda: menus)


def test_on_a_tab_treated_as_folder(window, tree, menus, monkeypatch):
    from tests.test_ui import set_tabs

    own_menu_only(monkeypatch)
    left, _ = tree
    set_tabs(window, tab_as_folder=True)
    bar = window.groups[1].tabBar()
    bar.dropEvent(right_drop(bar.rect_of(0).center(), [left / "a.txt"]))
    assert wait_until(lambda: menus)


def test_what_cannot_move_is_disabled(window, tree, menus, monkeypatch):
    import zipfile

    own_menu_only(monkeypatch)
    left, _ = tree
    with zipfile.ZipFile(left / "z.zip", "w") as out:
        out.writestr("x.txt", "x")
    window._files_dropped_menu(window.dest, [os.path.join(str(left / "z.zip"), "x.txt")], None, True)
    menu = menus[0]
    states = {action.text(): action.isEnabled() for action in menu.actions() if not action.isSeparator()}
    assert states == {"Copiar aquí": True, "Mover aquí": False, "Crear acceso directo aquí": False, "Cancelar": True}
    assert menu.defaultAction().text() == "Copiar aquí"


def test_options_of_other_programs_go_first_and_can_be_used(window, tree, menus, monkeypatch):
    left, _ = tree
    shell = FakeShell([ShellItem(label="TeraCopy here", command=7), ShellItem(label="TeraMove here", command=8)])
    calls = []
    monkeypatch.setattr(windows_shell, "drop_handlers", lambda paths, destination, hwnd: calls.append((paths, destination)) or [shell])
    choose(window, "TeraMove here")
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    menu = menus[0]
    assert labels_of(menu) == ["TeraCopy here", "TeraMove here", "Copiar aquí", "Mover aquí", "Crear acceso directo aquí", "Cancelar"]
    assert menu.actions()[2].isSeparator()
    assert shell.invoked == [8] and shell.closed and calls == [([str(left / "a.txt")], window.dest.path)]


def test_preference_turns_other_programs_off(window, tree, menus, monkeypatch):
    left, _ = tree
    window.config["shell_menu_items"] = False
    monkeypatch.setattr(windows_shell, "drop_handlers", lambda *a: pytest.fail("no debe cargar extensiones"))
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    assert "Copiar aquí" in labels_of(menus[0])


def test_failing_extensions_still_show_the_menu(window, tree, menus, monkeypatch):
    left, _ = tree

    def fail(*_a):
        raise OSError("falló")

    monkeypatch.setattr(windows_shell, "drop_handlers", fail)
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    assert "Mover aquí" in labels_of(menus[0])


def test_starting_a_drag_with_the_right_button(window, tree, monkeypatch):
    left, _ = tree
    FakeDrag.started = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.drag_drop.QDrag", FakeDrag)
    window.run_command("Set VIEW=List")
    settle()
    view = window.source
    point = name_center(view, "a.txt")
    viewport = view.current_view.viewport()
    QTest.mousePress(viewport, RIGHT, pos=point)
    QTest.mouseMove(viewport, point + QPoint(30, 12))
    QTest.mouseMove(viewport, point + QPoint(60, 24))
    QTest.mouseRelease(viewport, RIGHT, pos=point + QPoint(60, 24))
    assert [os.path.normpath(p) for p in FakeDrag.started[0]] == [os.path.normpath(str(left / "a.txt"))]


def test_right_click_without_moving_still_opens_the_menu(window, tree, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.drag_drop.QDrag", lambda *_a: pytest.fail("no es un arrastre"))
    window.run_command("Set VIEW=List")
    settle()
    view = window.source
    shown = []
    view.fileMenu.disconnect()
    view.fileMenu.connect(lambda *a: shown.append(a))
    point = name_center(view, "a.txt")
    viewport = view.current_view.viewport()
    QTest.mousePress(viewport, RIGHT, pos=point)
    QTest.mouseRelease(viewport, RIGHT, pos=point)
    view._context_menu(view.current_view, point)
    assert shown


@pytest.mark.skipif(not windows_shell.AVAILABLE, reason="los accesos directos .lnk son de Windows")
def test_create_shortcut_writes_a_real_lnk(tmp_path):
    target = tmp_path / "destino de prueba.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "acceso.lnk"
    windows_shell.create_shortcut(str(target), str(link))
    data = link.read_bytes()
    assert data[:4] == b"L\x00\x00\x00"  # cabecera de un acceso directo
    assert str(target).encode("utf-16-le") in data or target.name.encode("ascii") in data


@pytest.mark.skipif(not windows_shell.AVAILABLE, reason="los accesos directos .lnk son de Windows")
def test_shortcut_here_from_the_menu_and_undo(window, tree, menus, monkeypatch):
    own_menu_only(monkeypatch)
    left, right = tree
    choose(window, "Crear acceso directo aquí")
    window._files_dropped_menu(window.dest, [str(left / "a.txt"), str(left / "subcarpeta")], None, True)
    links = sorted(os.listdir(right))
    assert links == ["a.txt - acceso directo.lnk", "subcarpeta - acceso directo.lnk"]
    window._files_dropped_menu(window.dest, [str(left / "a.txt")], None, True)
    assert "a.txt - acceso directo (2).lnk" in os.listdir(right)  # no pisa el anterior
    window.run_command("Undo")
    assert wait_until(lambda: "a.txt - acceso directo (2).lnk" not in os.listdir(right) and window.jobs.running == 0)


def test_shortcut_elsewhere_is_a_link(window, tree, monkeypatch):
    left, right = tree
    calls = []
    monkeypatch.setattr(windows_shell, "AVAILABLE", False)
    monkeypatch.setattr(fsops, "make_link", lambda path, destination, kind: calls.append((path, destination, kind)) or "x")
    window._create_shortcuts([str(left / "a.txt")], str(right))
    assert calls == [(str(left / "a.txt"), str(right), "softlink")]


def test_shortcut_failure_is_reported(window, tree, monkeypatch):
    left, right = tree
    monkeypatch.setattr(windows_shell, "AVAILABLE", True)

    def fail(*_a):
        raise OSError("sin permiso")

    monkeypatch.setattr(windows_shell, "create_shortcut", fail, raising=False)
    window._create_shortcuts([str(left / "a.txt")], str(right))
    assert os.listdir(right) == []


def test_video_window_shows_the_size_of_a_turned_video(video_window, monkeypatch):
    """El giro se lee del fotograma: el de los metadatos es un tipo que PySide6 no sabe convertir, y lanzaba una excepción en cada fotograma."""
    from PySide6.QtCore import QSize
    from PySide6.QtMultimedia import QtVideo, QVideoFrame, QVideoFrameFormat

    frame = QVideoFrame(QVideoFrameFormat(QSize(1920, 1080), QVideoFrameFormat.PixelFormat.Format_RGBA8888))
    monkeypatch.setattr(video_window.media, "current_frame", lambda: frame)
    monkeypatch.setattr(video_window.media, "frame_interval_us", lambda: 40000)
    video_window._show_format()
    assert video_window.status_labels["format"].text() == "1920 x 1080 · 25 fps"
    frame.setRotation(QtVideo.Rotation.Clockwise90)
    video_window._show_format()
    assert video_window.status_labels["format"].text() == "1080 x 1920 · 25 fps"
