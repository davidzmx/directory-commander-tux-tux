"""Orden de los elementos de las barras."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu

from directory_commander_tux_tux.core import bar_buttons, settings_io
from directory_commander_tux_tux.core.bar_buttons import PATH, SEEK, SEPARATOR
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import MainWindow
from directory_commander_tux_tux.ui.media_player import MediaPlayer


@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    left = tmp_path / "izquierda"
    (left / "sub").mkdir(parents=True)
    main = MainWindow(Config(), [str(left), str(left / "sub")])
    main.show()
    QApplication.processEvents()
    yield main
    main.close()
    main.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


def texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def first_keys(config, bar, *first):
    """Un orden completo, como el que guarda la página: los indicados y detrás el resto."""
    default = next(buttons for name, _title, buttons in bar_buttons.bars(config) if name == bar)
    return [*first, *[key for key, _label in default if key not in first]]


def set_order(window, **bars):
    window.config["bar_order"] = bars
    window.apply_theme()
    window._apply_bar_buttons()


def layout_widgets(layout):
    return [layout.itemAt(index).widget() for index in range(layout.count())]


# ------------------------------------------------------------------ el orden, sin interfaz
def test_order_without_choice_is_the_default():
    assert bar_buttons.ordered({}, bar_buttons.PANE, ["a", "b"]) == ["a", "b"]


def test_order_follows_the_choice_and_drops_what_no_longer_exists():
    config = {"bar_order": {"styles": ["c", "ya-no-está", "a", "b"]}}
    assert bar_buttons.ordered(config, "styles", ["a", "b", "c"]) == ["c", "a", "b"]


def test_new_button_stays_behind_its_default_neighbour():
    config = {"bar_order": {"styles": ["c", "a"]}}
    assert bar_buttons.ordered(config, "styles", ["a", "nuevo", "c", "último"]) == ["c", "último", "a", "nuevo"]
    assert bar_buttons.ordered({"bar_order": {"styles": ["c"]}}, "styles", ["primero", "c"]) == ["primero", "c"]


def test_lines_only_count_on_bars_that_can_have_them():
    assert bar_buttons.ordered({"bar_order": {"pane": ["b", SEPARATOR, "a"]}}, "pane", ["a", "b"]) == ["b", "a"]
    assert bar_buttons.ordered({"bar_order": {"toolbar": ["b", SEPARATOR, SEPARATOR, "a"]}}, "toolbar", ["a", "b"]) == ["b", SEPARATOR, SEPARATOR, "a"]


def test_arrange_toolbar_moves_buttons_and_lines_keeping_what_each_one_stores():
    first = {"label": "Uno", "commands": ["Go UP"], "children": [{"label": "x"}]}
    second = {"label": "Dos", "commands": ["Go BACK"]}
    items = [first, {"separator": True}, second, {"commands": ["Set Spacer"]}, {"commands": ["Marker"]}]
    assert bar_buttons.arrange_toolbar({}, items) is items
    arranged = bar_buttons.arrange_toolbar({"bar_order": {"toolbar": ["Dos", "<->", SEPARATOR, "Uno"]}}, items)
    assert arranged == [second, {"commands": ["Set Spacer"]}, {"separator": True}, first]
    assert arranged[3] is first


def test_toolbar_keeps_a_button_added_after_ordering():
    items = [{"label": "Uno", "commands": ["a"]}, {"label": "Dos", "commands": ["b"]}, {"label": "Nuevo", "commands": ["c"]}]
    arranged = bar_buttons.arrange_toolbar({"bar_order": {"toolbar": ["Dos", "Uno"]}}, items)
    assert [item["label"] for item in arranged] == ["Dos", "Nuevo", "Uno"]


@pytest.mark.parametrize("raw", [None, 3, "x", {"pane": "x"}, {3: ["a"]}])
def test_damaged_order_is_ignored(raw):
    assert bar_buttons.normalize_order(raw) == {}
    assert bar_buttons.ordered({"bar_order": raw}, "pane", ["a"]) == ["a"]


def test_order_is_validated_on_import(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"bar_order": {"toolbar": ["a", 3, "a", SEPARATOR, SEPARATOR], "pane": "x"}}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"bar_order": {"toolbar": ["a", SEPARATOR, SEPARATOR]}}, [])


# ------------------------------------------------------------------ la página de preferencias
def keys_of(buttons_list):
    return [buttons_list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(buttons_list.count())]


def test_page_saves_nothing_until_something_moves(qapp_offscreen):
    dialog = PreferencesDialog({})
    assert dialog.bar_buttons.order_values() == {}


def test_page_moves_with_the_buttons_and_saves_only_that_bar(qapp_offscreen):
    config = {}
    dialog = PreferencesDialog(config)
    page = dialog.bar_buttons
    page.show_bar("pane")
    panes = page.lists["pane"]
    before = keys_of(panes)
    panes.setCurrentRow(1)
    page.move_current(-1)
    dialog.apply_to(config)
    assert config["bar_order"] == {"pane": [before[1], before[0], *before[2:]]}
    panes.setCurrentRow(0)
    page.move_current(-1)  # el primero no puede subir más
    assert keys_of(panes)[0] == before[1]


def test_page_shows_the_stored_order_and_keeps_hidden_buttons_in_place(qapp_offscreen):
    config = {"bar_order": {"pane": first_keys({}, "pane", "home", "goup")}, "bar_buttons": {"pane": ["home"]}}
    dialog = PreferencesDialog(config)
    page = dialog.bar_buttons
    panes = page.lists["pane"]
    assert keys_of(panes)[0] == "home" and panes.item(0).checkState() == Qt.CheckState.Unchecked
    assert keys_of(panes).index("goup") < keys_of(panes).index("menu-arrow")
    assert page.values() == {"pane": ["home"]}


def test_page_restores_the_order_but_not_the_ticks(qapp_offscreen):
    dialog = PreferencesDialog({"bar_order": {"pane": first_keys({}, "pane", "home", "goup")}, "bar_buttons": {"pane": ["home"]}})
    page = dialog.bar_buttons
    page.show_bar("pane")
    page.reset_order()
    assert page.order_values() == {} and page.values() == {"pane": ["home"]}


def test_page_adds_and_removes_lines_only_where_they_fit(qapp_offscreen):
    dialog = PreferencesDialog({})
    page = dialog.bar_buttons
    page.show_bar("image_viewer")
    viewer = page.lists["image_viewer"]
    assert not page.add_line_button.isHidden()
    count = viewer.count()
    viewer.setCurrentRow(0)
    page.add_line()
    assert keys_of(viewer)[1] == SEPARATOR and viewer.count() == count + 1
    viewer.setCurrentRow(1)
    page.remove_line()
    assert viewer.count() == count
    viewer.setCurrentRow(0)
    page.remove_line()  # solo quita líneas
    assert viewer.count() == count
    page.show_bar("pane")
    assert page.add_line_button.isHidden()


def test_path_and_progress_rows_move_but_have_no_checkbox(qapp_offscreen):
    dialog = PreferencesDialog({})
    page = dialog.bar_buttons
    for bar, key in (("pane", PATH), ("location", PATH), ("video_controls", SEEK), ("player_controls", SEEK)):
        buttons_list = page.lists[bar]
        item = buttons_list.item(keys_of(buttons_list).index(key))
        assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert page.values() == {}  # sin casilla, no cuentan como ocultos


def test_rows_can_be_dragged_but_not_dropped_on_each_other(qapp_offscreen):
    dialog = PreferencesDialog({})
    page = dialog.bar_buttons
    for buttons_list in page.lists.values():
        assert buttons_list.dragDropMode() == buttons_list.DragDropMode.InternalMove
        assert not buttons_list.item(0).flags() & Qt.ItemFlag.ItemIsDropEnabled


def test_a_dropped_row_order_is_what_gets_saved(qapp_offscreen):
    """Lo que hace Qt al soltar en una lista: cambia las filas de sitio. La casilla viaja con la fila."""
    dialog = PreferencesDialog({"bar_buttons": {"pane": ["goup"]}})
    page = dialog.bar_buttons
    panes = page.lists["pane"]
    row = keys_of(panes).index("goup")
    panes.model().moveRow(panes.model().index(row, 0).parent(), row, panes.model().index(row, 0).parent(), 0)
    assert keys_of(panes)[0] == "goup" and page.order_values()["pane"][0] == "goup" and page.values() == {"pane": ["goup"]}


# ------------------------------------------------------------------ las barras reales
def test_main_toolbar_uses_the_order_and_restoring_it_goes_back(window):
    labels = [action.text() for action in window.toolbar.actions() if not action.isSeparator()]
    set_order(window, toolbar=first_keys(window.config, "toolbar", "Copiar", SEPARATOR, "Atrás"))
    after = [action.text() for action in window.toolbar.actions() if not action.isSeparator()]
    assert after[:2] == ["Copiar", "Atrás"] and window.toolbar.actions()[1].isSeparator()
    assert set(after) == set(labels)  # lo que no se nombró sigue en la barra
    window._reset_toolbar()
    assert [action.text() for action in window.toolbar.actions() if not action.isSeparator()] == labels
    assert window.config["bar_order"] == {}


def test_style_bar_uses_the_order(window):
    set_order(window, styles=first_keys(window.config, "styles", "Único", "Comandante"))
    labels = [action.text() for action in window.style_bar.actions() if action.text()]
    assert labels[:2] == ["Único", "Comandante"] and "Explorador" in labels


def test_location_bar_moves_its_groups_and_they_keep_working(window):
    bar = window.location_bar
    set_order(window, location=["content_type", "recent", PATH])
    actions = bar.actions()
    assert actions[:2] == window._content_actions and actions[2] is window._location_recent_action
    assert actions[3:] == window._location_path_actions
    window.resize(1200, 700)
    QApplication.processEvents()
    assert bar.widgetForAction(window._location_path_actions[-1]) is window.location
    assert window.location.parent() is bar and window.content_box.parent() is bar and not window.content_box.isHidden()
    assert bar.widgetForAction(window._location_recent_action) is window.location_recent
    window.config["bar_buttons"] = {"location": ["recent"]}
    window._apply_bar_buttons()
    QApplication.processEvents()  # la barra coloca sus controles al activar su diseño
    assert not window._location_recent_action.isVisible() and not window.content_box.isHidden()
    window.source.navigate(window.source.path)
    set_order(window)
    assert bar.actions()[:len(window._location_path_actions)] == window._location_path_actions


def test_pane_bar_moves_buttons_and_path_in_every_view(window):
    set_order(window, pane=first_keys({}, "pane", "close", PATH, "goup", "home"))
    for view in window.all_views():
        widgets = layout_widgets(view.pane_bar.layout())
        tools = {icon: button for button, icon in view._tool_buttons}
        assert widgets[:4] == [tools["close"], view.crumb_strip, tools["goup"], tools["home"]]
        assert layout_widgets(view.crumb_strip.layout()) == [view.folder_label, view.breadcrumb]
        assert len(widgets) == len(tools) + 2 and view.git_label in widgets  # no se pierde ninguno: con la ruta y la rama
        assert view.pane_bar.layout().stretch(widgets.index(view.crumb_strip)) == 1


def test_new_tab_uses_the_order(window):
    window.config["bar_order"] = {"pane": first_keys({}, "pane", "home", "goup")}
    view = window.groups[0].add_tab(window.source.path, activate=False)
    tools = {icon: button for button, icon in view._tool_buttons}
    assert layout_widgets(view.pane_bar.layout())[:2] == [tools["home"], tools["goup"]]


def test_side_player_controls_use_the_order(qapp_offscreen):
    media = MediaPlayer({"bar_order": {"player_controls": first_keys({}, "player_controls", "volume", SEEK, "play")}})
    widgets = layout_widgets(media.controls.layout())
    assert widgets[:3] == [media.volume, media.seek, media.play_button]
    assert media.stop_button in widgets and media.auto_button in widgets and media.back_button is None
    assert media.controls.layout().stretch(1) == 1
    media.close()


def test_video_window_controls_use_their_own_order(qapp_offscreen):
    media = MediaPlayer({"bar_order": {"video_controls": first_keys({}, "video_controls", "frame_forward", "frame_back"), "player_controls": ["volume"]}}, windowed=True)
    assert layout_widgets(media.controls.layout())[:2] == [media.forward_button, media.back_button]
    media.close()


def test_image_viewer_toolbar_uses_the_order(window, tmp_path):
    photo = tmp_path / "uno.png"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    image.save(str(photo))
    viewer = window._new_image_viewer([str(photo)], 0)
    set_order(window, image_viewer=first_keys({}, "image_viewer", "delete", SEPARATOR, "next", "zoom_slider"))
    actions = viewer.toolbar.actions()
    assert actions[0] is viewer.actions_by_key["delete"] and actions[1].isSeparator()
    assert actions[2] is viewer.actions_by_key["next"] and actions[3] is viewer._zoom_action
    viewer.close()
