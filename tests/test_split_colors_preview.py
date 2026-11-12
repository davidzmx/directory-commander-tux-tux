"""Dividir paneles, sus colores configurables y el esquema del editor de estilos."""
import json
import os

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import context_menu, pane_colors, settings_io, styles
from directory_commander_tux_tux.core.config import DEFAULT_STYLES, Config
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import GENERAL_TERMINAL, MainWindow
from directory_commander_tux_tux.ui.style_preview import StylePreview
from directory_commander_tux_tux.ui.styles_page import StylesPage


@pytest.fixture
def folders(tmp_path):
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    left.mkdir()
    right.mkdir()
    return left, right


def open_window(folders) -> MainWindow:
    main = MainWindow(Config(), [str(folder) for folder in folders])
    main.resize(1500, 800)
    main.show()
    QTest.qWait(30)
    return main


@pytest.fixture
def window(qapp_for_gc, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    main = open_window(folders)
    yield main
    theme.set_current(theme.DARK)
    main.close()
    main.deleteLater()
    QApplication.processEvents()


def settle() -> None:
    QTest.qWait(40)


def corner(widget, window) -> QPoint:
    return widget.mapTo(window, QPoint(0, 0))


def same(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


# ------------------------------------------------------------------ el árbol de una disposición
@pytest.mark.parametrize("text, expected", [("0", 0), ("h(0,1)", ("h", (0, 1))), ("v(h(0,1),2)", ("v", (("h", (0, 1)), 2))), ("h(0,v(1,v(2,3)))", ("h", (0, ("v", (1, ("v", (2, 3)))))))])
def test_layouts_are_read(text, expected):
    assert styles.layout_tree(text) == expected and styles.valid_layout(text)


@pytest.mark.parametrize("text", ["", "x", "h()", "h(0)", "h(0,1", "h(0,1))", "h(0,0)", "h(1,2)", "h(0,1,2,3,4,5,6,7,8)", "h(0,1)x", "5-Grid", 3, None, "v(0,,1)"])
def test_wrong_layouts_are_rejected(text):
    assert not styles.valid_layout(text) and styles.pane_count(text) == 2


def test_every_named_layout_is_valid_and_has_its_panes():
    assert [styles.pane_count(mode) for mode in styles.PANE_MODES] == [1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 6, 8]


@pytest.mark.parametrize("mode, pane, where, expected, new", [
    ("Off", 0, "right", "Vertical", 1), ("Off", 0, "below", "Horizontal", 1),
    ("Vertical", 1, "right", "3-Vertical", 2), ("Horizontal", 1, "below", "3-Horizontal", 2),
    ("Vertical", 1, "below", "3-Left", 2), ("Horizontal", 0, "right", "v(h(0,2),1)", 2),
    ("Vertical", 0, "right", "h(0,2,1)", 2), ("Vertical", 0, "below", "h(v(0,2),1)", 2),
    ("3-Left", 0, "below", "h(v(0,3),v(1,2))", 3), ("3-Top", 2, "right", "4-Grid", 3),
])
def test_splitting_a_pane(mode, pane, where, expected, new):
    assert styles.split_layout(mode, pane, where) == (expected, new)


def test_there_is_no_ninth_pane():
    assert styles.split_layout("8-Grid", 0, "right") is None
    assert styles.split_layout("4-Grid", 0, "right") == ("v(h(0,4,1),h(2,3))", 4)
    assert styles.split_layout("Vertical", 3, "right") is None  # no está en esa disposición


@pytest.mark.parametrize("mode", list(styles.PANE_MODES) + ["h(v(0,3),v(1,2))", "h(0,2,1)"])
def test_rects_cover_the_whole_square_without_overlapping(mode):
    rects = styles.layout_rects(mode)
    assert abs(sum(width * height for _x, _y, width, height in rects.values()) - 1.0) < 1e-9
    assert sorted(rects) == list(range(styles.pane_count(mode)))


def test_send_arrow_with_a_custom_layout():
    assert styles.send_direction("h(v(0,2),1)", 0, 2) == "senddown"
    assert styles.send_direction("h(v(0,2),1)", 2, 1) == "sendright"
    assert styles.send_direction("h(0,2,1)", 2, 1) == "sendright"
    assert styles.send_direction("3-Top", 0, 2) == "senddown"


# ------------------------------------------------------------------ dividir en la ventana
def menu_entries(window) -> dict:
    return {action.text(): action for action in window.build_folder_menu(window.source).actions()}


def test_background_menu_offers_both_splits(window):
    entries = menu_entries(window)
    assert entries["Dividir panel - Horizontal"].isEnabled() and entries["Dividir panel - Vertical"].isEnabled()


def test_menu_of_a_file_does_not_offer_them(window, folders):
    (folders[0] / "a.txt").write_text("x", encoding="utf-8")
    window.source.refresh()
    settle()
    assert "Dividir panel - Vertical" not in context_menu.DEFAULT_LAYOUTS[context_menu.FILE]


def test_new_options_reach_a_menu_saved_before_they_existed():
    saved = {"folder": {"order": ["Pegar", "Abrir en una pestaña nueva", "Abrir terminal aquí"], "hidden": []}}
    order = context_menu.normalize(saved)["folder"]["order"]
    assert order.index("Dividir panel - Horizontal") == order.index("Abrir en una pestaña nueva") + 1


def test_splitting_vertical_creates_a_pane_to_the_right_in_the_same_folder(window, folders):
    window.apply_style("Explorador")
    settle()
    assert window.dest is None
    menu_entries(window)["Dividir panel - Vertical"].trigger()
    settle()
    assert window.dual == "Vertical"
    first, second = window.groups[0], window.groups[1]
    assert second.isVisible() and corner(second, window).x() > corner(first, window).x() and corner(second, window).y() == corner(first, window).y()
    assert same(second.current.path, first.current.path)
    assert window.source is second.current and window.dest is first.current
    assert abs(first.width() - second.width()) < 20


def test_splitting_horizontal_creates_a_pane_below_in_the_same_folder(window):
    window.apply_style("Explorador")
    settle()
    menu_entries(window)["Dividir panel - Horizontal"].trigger()
    settle()
    assert window.dual == "Horizontal"
    first, second = window.groups[0], window.groups[1]
    assert corner(second, window).y() > corner(first, window).y() and corner(second, window).x() == corner(first, window).x()
    assert same(second.current.path, first.current.path) and abs(first.height() - second.height()) < 20


def test_the_clicked_pane_is_the_one_divided_and_the_others_keep_their_size(window, folders):
    window.splitter.setSizes([900, 300])
    settle()
    right_width = window.groups[1].width()
    window.groups[0].current.activated.emit(window.groups[0].current)
    window.split_pane("below")
    settle()
    a, b, c = window.groups[:3]
    assert window.dual == "h(v(0,2),1)"
    assert corner(c, window).y() > corner(a, window).y() and corner(c, window).x() == corner(a, window).x() and corner(b, window).x() > corner(a, window).x()
    assert abs(b.width() - right_width) < 20  # el otro panel no cambia
    assert abs(a.height() - c.height()) < 20 and b.height() > a.height()
    assert same(c.current.path, folders[0]) and same(b.current.path, folders[1])


def test_splitting_to_the_right_only_halves_that_pane(window):
    window.splitter.setSizes([900, 300])
    settle()
    left_width, right_width = window.groups[0].width(), window.groups[1].width()
    window.groups[0].current.activated.emit(window.groups[0].current)
    window.split_pane("right")
    settle()
    a, b, c = window.groups[:3]
    assert window.dual == "h(0,2,1)"
    assert corner(a, window).x() < corner(c, window).x() < corner(b, window).x()
    assert abs(b.width() - right_width) < 20 and abs(a.width() - left_width / 2) < 20 and abs(c.width() - left_width / 2) < 20


def test_up_to_eight_and_then_the_options_are_disabled(window):
    window.resize(1800, 1000)
    for where in ("right", "below", "right", "below", "right", "below"):
        window.split_pane(where)
    settle()
    assert styles.pane_count(window.dual) == styles.MAX_PANES == 8
    assert all(group.isVisible() for group in window.groups)
    entries = menu_entries(window)
    assert not entries["Dividir panel - Horizontal"].isEnabled() and not entries["Dividir panel - Vertical"].isEnabled()
    before = window.dual
    messages = []
    window.statusBar().messageChanged.connect(messages.append)
    window.split_pane("right")
    assert window.dual == before and any("máximo" in message for message in messages)


def test_every_combination_of_splits_leaves_the_panes_apart(window):
    for first in ("right", "below"):
        for second in ("right", "below"):
            for third in ("right", "below"):
                window.apply_style("Explorador")
                settle()
                for where in (first, second, third):
                    window.split_pane(where)
                settle()
                assert styles.pane_count(window.dual) == 4
                rects = [group.rect().translated(corner(group, window)) for group in window.groups[:4]]
                for index, rect in enumerate(rects):
                    assert rect.width() > 40 and rect.height() > 40
                    assert not any(rect.intersects(other) for other in rects[index + 1:])


def test_divided_pane_inside_an_archive_or_elsewhere_keeps_its_path(window, tmp_path):
    other = tmp_path / "otra"
    other.mkdir()
    window.source.navigate(str(other))
    settle()
    window.run_command("Set SPLIT=Vertical")
    settle()
    assert same(window.source.path, other)


def test_a_pane_used_before_goes_to_the_folder_and_keeps_its_tabs(window, tmp_path, folders):
    window.set_dual("3-Vertical")
    settle()
    used = window.groups[2].current
    window.groups[2].add_tab(str(tmp_path))
    window.apply_style("Vertical doble panel")
    settle()
    window.split_pane("right")
    settle()
    assert same(window.groups[2].current.path, window.groups[0].current.path)
    assert window.groups[2].count() == 2 and used is not None


def test_wrong_value_of_the_command(window):
    with pytest.raises(ValueError):
        window._set_split("diagonal")


def test_terminals_stay_with_their_panes_when_dividing(window, monkeypatch):
    class Session:
        def __init__(self, *_args):
            self.closed = False

        def shell_is_idle(self):
            return True

        def current_directory(self):
            return None

        def write(self, _data):
            pass

        def resize(self, _cols, _rows):
            pass

        def close(self):
            self.closed = True

    from directory_commander_tux_tux.ui.terminal import TerminalView
    monkeypatch.setattr(TerminalView, "session_factory", Session)
    window.run_command("Set TERMINAL=On")
    settle()
    session = window.terminals[0].session
    window.split_pane("below")
    settle()
    assert window.terminals[0].session is session and not session.closed and window.terminal_panels[0].isVisible()
    assert window.terminal_panels[2].isHidden()


def test_double_tree_gives_the_new_pane_its_tree(window):
    window.apply_style("Vertical doble árbol")
    settle()
    window.split_pane("right")
    settle()
    assert window.tree_panels[2].isVisible()


def test_session_reopens_a_divided_window(qapp_for_gc, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = open_window(folders)
    first.groups[0].current.activated.emit(first.groups[0].current)
    first.split_pane("below")
    settle()
    height = first.groups[0].height()
    first.close()
    first.deleteLater()
    QApplication.processEvents()
    second = open_window(folders)
    settle()
    assert second.dual == "h(v(0,2),1)" and second.groups[2].isVisible() and abs(second.groups[0].height() - height) < 40
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_a_style_can_keep_a_divided_arrangement(window, monkeypatch):
    window.split_pane("below")
    settle()
    monkeypatch.setattr(window, "_ask_text", lambda *_args: "Dividido")
    window._save_current_style()
    layout = window._style_named("Dividido")["dual"]
    assert styles.valid_layout(layout)
    page = StylesPage(window.config)
    page.show_style("Dividido")
    assert page.panes.currentData() == layout and page.panes.currentText() == "Disposición propia"
    page.viewer.setChecked(True)
    page.apply_to(window.config)
    assert window._style_named("Dividido")["dual"] == layout
    window.apply_style("Vertical doble panel")
    settle()
    window.apply_style("Dividido")
    settle()
    assert window.dual == layout


def test_going_back_to_a_style_after_dividing(window):
    window.split_pane("below")
    window.split_pane("right")
    settle()
    window.apply_style("Vertical doble panel")
    settle()
    assert window.dual == "Vertical" and window.sides[0].parent() is window.splitter and window.sides[2].isHidden() and window.sides[3].isHidden()
    assert corner(window.groups[1], window).x() > corner(window.groups[0], window).x()


# ------------------------------------------------------------------ los colores
def test_without_choosing_anything_the_theme_stays_exact():
    assert theme.with_pane_colors(theme.DARK, {}) == theme.DARK
    assert theme.with_pane_colors(theme.LIGHT, None) == theme.LIGHT
    assert theme.with_pane_colors(theme.DARK, {"source": "azul", "dest": 3, "nada": "#112233"}) == theme.DARK


def test_chosen_colors_change_bar_list_and_selection():
    colored = theme.with_pane_colors(theme.DARK, {"source": "#006633", "dest": "#663300", "other": "#333366"})
    assert (colored.bar_source, colored.bar_dest, colored.bar_other) == ("#006633", "#663300", "#333366")
    assert colored.list_source == theme.DARK.list_source
    assert colored.list_dest != theme.DARK.list_dest and colored.list_other != theme.DARK.list_other
    assert colored.sel_source != theme.DARK.sel_source and colored.sel_dest != theme.DARK.sel_dest
    assert colored.name == "dark"


def test_a_grey_color_gives_a_grey_selection():
    selected = QColor(theme.with_pane_colors(theme.DARK, {"source": "#555555"}).sel_source)
    assert selected.saturation() == 0 and selected.value() >= 130


@pytest.mark.parametrize("base", [theme.DARK, theme.LIGHT])
@pytest.mark.parametrize("color", ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff", "#101010", "#f0f0f0"])
def test_selection_can_be_read_with_any_color(base, color):
    selection = QColor(theme.with_pane_colors(base, {"source": color}).sel_source)
    text = QColor(base.sel_text)
    assert abs(selection.lightness() - text.lightness()) > 60


def test_colors_are_cleaned():
    assert pane_colors.clean_colors({"source": "#AABBCC", "dest": "azul", "x": "#000000"}) == {"source": "#aabbcc"}
    assert pane_colors.clean_colors("nada") == {}


def stylesheet_of() -> str:
    return QApplication.instance().styleSheet()


def use_colors(window, **colors) -> None:
    """Una combinación propia con esos colores, en uso."""
    window.config["pane_color_customs"] = [{"name": "Prueba", **colors}]
    window.config["pane_color_scheme"] = pane_colors.custom_key("Prueba")
    window._preferences_changed()


def test_window_uses_the_chosen_colors_at_once(window):
    use_colors(window, source="#006633", dest="#663300", other="#333366")
    assert theme.current().bar_source == "#006633" and "#006633" in stylesheet_of() and "#663300" in stylesheet_of() and "#333366" in stylesheet_of()
    window.config["pane_color_scheme"] = pane_colors.DEFAULT_SCHEME
    window._preferences_changed()
    assert theme.current().bar_source == theme.DARK.bar_source and "#006633" not in stylesheet_of()


def test_chosen_colors_survive_changing_the_theme(window):
    use_colors(window, dest="#663300")
    window.run_command("Set DARKMODE=Toggle")
    assert theme.current().name == "light" and theme.current().bar_dest == "#663300"
    window.run_command("Set DARKMODE=Toggle")
    assert theme.current().name == "dark" and theme.current().bar_dest == "#663300"


def test_selection_and_level_colors_follow_the_role(window):
    use_colors(window, other="#333366")
    window.set_dual("4-Grid")
    settle()
    assert window.groups[3].current.property("role") == "other"
    view = window.groups[3].current.details
    assert view.pane_role == "other" and view.selection_color() == QColor(theme.current().sel_other)
    assert theme.level_color("other", 1) != theme.level_color("source", 1)


# ------------------------------------------------------------------ las combinaciones
def contrast(first, second) -> float:
    def luminance(color: str) -> float:
        value = QColor(color)
        parts = [channel / 255 for channel in (value.red(), value.green(), value.blue())]
        linear = [part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4 for part in parts]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_there_are_three_builtin_combinations():
    assert list(pane_colors.BUILTIN_SCHEMES) == ["blue-red", "green-amber", "violet-teal"]
    for _label, by_theme in pane_colors.BUILTIN_SCHEMES.values():
        assert set(by_theme) == {"dark", "light"}


@pytest.mark.parametrize("scheme", list(pane_colors.BUILTIN_SCHEMES))
@pytest.mark.parametrize("base", [theme.DARK, theme.LIGHT], ids=["oscuro", "claro"])
def test_builtin_combinations_can_be_read_in_both_themes(scheme, base):
    colored = theme.with_pane_colors(base, pane_colors.scheme_colors(scheme, [], base.name))
    for role in ("source", "dest", "other"):
        assert contrast(theme.crumb_color(colored, role), theme.crumb_text_color(colored, role)) >= 4.5, role  # la ruta
        assert contrast(getattr(colored, f"bar_{role}"), theme.bar_icon_color(colored, role)) >= 3, role  # los iconos de la barra
        assert contrast(getattr(colored, f"sel_{role}"), colored.sel_text) >= 4.5, role  # la fila seleccionada
        assert contrast(getattr(colored, f"list_{role}"), colored.text) >= 4.5, role  # los archivos de la lista
    assert colored.bar_source != colored.bar_dest


@pytest.mark.parametrize("scheme", ["green-amber", "violet-teal"])
def test_other_combinations_change_with_the_theme(scheme):
    assert pane_colors.scheme_colors(scheme, [], "dark") != pane_colors.scheme_colors(scheme, [], "light")
    assert pane_colors.scheme_colors(scheme, [], "dark")["source"] != pane_colors.scheme_colors(scheme, [], "dark")["dest"]


def test_panes_without_their_own_color_select_like_the_active_one():
    colored = theme.with_pane_colors(theme.DARK, {"source": "#1f4a38"})
    assert colored.sel_other == colored.sel_source != theme.DARK.sel_other
    both = theme.with_pane_colors(theme.DARK, {"source": "#1f4a38", "other": "#333366"})
    assert both.sel_other != both.sel_source


def test_default_combination_is_the_theme_itself():
    assert pane_colors.scheme_colors("blue-red", [], "dark") == {} and pane_colors.scheme_colors("no existe", [], "dark") == {}


def test_choosing_a_combination_applies_it_in_both_themes(window):
    window.config["pane_color_scheme"] = "green-amber"
    window._preferences_changed()
    assert theme.current().bar_source == pane_colors.BUILTIN_SCHEMES["green-amber"][1]["dark"]["source"]
    window.run_command("Set DARKMODE=Toggle")
    assert theme.current().name == "light" and theme.current().bar_source == pane_colors.BUILTIN_SCHEMES["green-amber"][1]["light"]["source"]
    window.run_command("Set DARKMODE=Toggle")


def test_preferences_list_and_select_combinations(window):
    window.config["pane_color_customs"] = [{"name": "Mía", "source": "#112233"}]
    dialog = PreferencesDialog(window.config)
    box = dialog.pane_scheme
    assert [box.itemData(row) for row in range(box.count())] == ["blue-red", "green-amber", "violet-teal", "custom:Mía"]
    assert box.currentData() == "blue-red" and not dialog.delete_scheme.isEnabled()
    assert not dialog.pane_colors["source"].isEnabled() and dialog.pane_colors["source"].default == theme.DARK.bar_source
    box.setCurrentIndex(box.findData("violet-teal"))
    assert dialog.pane_colors["source"].value == pane_colors.BUILTIN_SCHEMES["violet-teal"][1]["dark"]["source"]
    dialog.apply_to(window.config)
    assert window.config["pane_color_scheme"] == "violet-teal"
    box.setCurrentIndex(box.findData("custom:Mía"))
    assert dialog.pane_colors["source"].value == "#112233" and dialog.pane_colors["source"].isEnabled() and dialog.delete_scheme.isEnabled()
    dialog.deleteLater()


def test_swatches_follow_the_theme_chosen_in_the_dialog(window):
    dialog = PreferencesDialog(window.config)
    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    assert dialog.pane_colors["dest"].default == theme.LIGHT.bar_dest
    dialog.pane_scheme.setCurrentIndex(dialog.pane_scheme.findData("green-amber"))
    assert dialog.pane_colors["dest"].value == pane_colors.BUILTIN_SCHEMES["green-amber"][1]["light"]["dest"]
    dialog.deleteLater()


def test_new_combination_starts_from_what_is_shown(window, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.QInputDialog.getText", lambda *_args, **_kwargs: ("Corporativa", True))
    dialog = PreferencesDialog(window.config)
    dialog.pane_scheme.setCurrentIndex(dialog.pane_scheme.findData("green-amber"))
    dialog._new_pane_scheme()
    assert dialog.pane_scheme.currentData() == "custom:Corporativa" and dialog.pane_colors["source"].isEnabled()
    button = dialog.pane_colors["dest"]
    monkeypatch.setattr("directory_commander_tux_tux.ui.color_button.QColorDialog.getColor", lambda *_args: QColor("#123456"))
    button.choose()
    dialog.apply_to(window.config)
    assert window.config["pane_color_scheme"] == "custom:Corporativa"
    saved = window.config["pane_color_customs"][0]
    assert saved["name"] == "Corporativa" and saved["dest"] == "#123456"
    assert saved["source"] == pane_colors.BUILTIN_SCHEMES["green-amber"][1]["dark"]["source"]
    window._preferences_changed()
    assert theme.current().bar_dest == "#123456"
    dialog.deleteLater()


def test_cancelling_or_repeating_the_name_of_a_combination(window, monkeypatch):
    dialog = PreferencesDialog(window.config)
    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.QInputDialog.getText", lambda *_args, **_kwargs: ("X", False))
    dialog._new_pane_scheme()
    assert dialog.pane_scheme.count() == 3
    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.QInputDialog.getText", lambda *_args, **_kwargs: ("Mía", True))
    dialog._new_pane_scheme()
    dialog._new_pane_scheme()
    assert [dialog.pane_scheme.itemText(row) for row in range(3, 5)] == ["Mía", "Mía (2)"]
    dialog.deleteLater()


def test_deleting_a_combination_goes_back_to_the_default(window):
    use_colors(window, source="#112233")
    dialog = PreferencesDialog(window.config)
    assert dialog.pane_scheme.currentData() == "custom:Prueba"
    dialog._delete_pane_scheme()
    assert dialog.pane_scheme.currentData() == "blue-red" and dialog.pane_scheme.findData("custom:Prueba") < 0
    dialog._delete_pane_scheme()  # la de serie no se elimina
    assert dialog.pane_scheme.count() == 3
    dialog.apply_to(window.config)
    assert window.config["pane_color_customs"] == [] and window.config["pane_color_scheme"] == "blue-red"
    dialog.deleteLater()


def test_old_loose_colors_become_a_combination(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 11, "pane_colors": {"source": "#112233", "dest": "mal"}}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert "pane_colors" not in config.data
    assert config["pane_color_scheme"] == "custom:Personalizada" and config["pane_color_customs"] == [{"name": "Personalizada", "source": "#112233"}]


def test_without_old_colors_the_default_stays(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 11, "pane_colors": {}}), encoding="utf-8")
    assert Config(str(tmp_path))["pane_color_scheme"] == "blue-red"


def test_a_settings_file_carries_only_valid_combinations(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {
        "pane_color_scheme": "custom:A", "pane_color_customs": [{"name": "A", "source": "#112233", "dest": "url(x)"}, {"name": "a"}, {"source": "#000000"}, 3]}}), encoding="utf-8")
    settings = settings_io.read_settings(str(path))[0]
    assert settings["pane_color_scheme"] == "custom:A" and settings["pane_color_customs"] == [{"name": "A", "source": "#112233"}]


# ------------------------------------------------------------------ el esquema del editor
def kinds(regions) -> list:
    return [(region.kind, region.index) for region in regions]


def overlap(first, second) -> bool:
    return first.x < second.x + second.width - 1e-9 and second.x < first.x + first.width - 1e-9 and first.y < second.y + second.height - 1e-9 and second.y < first.y + first.height - 1e-9


@pytest.mark.parametrize("style", DEFAULT_STYLES, ids=[style["name"] for style in DEFAULT_STYLES])
@pytest.mark.parametrize("full_width", [False, True])
def test_preview_of_every_builtin_fills_the_window_without_overlapping(style, full_width):
    regions = styles.preview_regions(style, full_width)
    assert abs(sum(region.width * region.height for region in regions) - 1.0) < 1e-9
    for index, region in enumerate(regions):
        assert region.width > 0 and region.height > 0
        assert not any(overlap(region, other) for other in regions[index + 1:])


def test_preview_vertical_panel_terminal_has_the_terminal_to_the_right():
    regions = {(region.kind, region.index): region for region in styles.preview_regions(next(s for s in DEFAULT_STYLES if s["name"] == "Vertical panel-terminal"))}
    assert regions[(styles.TERMINAL, 0)].x > regions[(styles.PANE, 0)].x and regions[(styles.TREE, 0)].x == 0.0


def test_preview_horizontal_panel_terminal_has_the_terminal_below():
    regions = {(region.kind, region.index): region for region in styles.preview_regions(next(s for s in DEFAULT_STYLES if s["name"] == "Horizontal panel-terminal"))}
    assert regions[(styles.TERMINAL, 0)].y > regions[(styles.PANE, 0)].y


def test_preview_shows_each_part_of_the_style():
    style = {"dual": "4-Grid", "tree": True, "double_tree": True, "viewer": True, "meta": True, "terminals": ["first", "third", "general"]}
    found = kinds(styles.preview_regions(style))
    assert all((styles.PANE, pane) in found and (styles.PANE_TREE, pane) in found for pane in range(4))
    assert (styles.TERMINAL, 0) in found and (styles.TERMINAL, 2) in found and (styles.TERMINAL, 1) not in found
    assert (styles.GENERAL_TERMINAL_PANEL, 0) in found and (styles.VIEWER, 0) in found and (styles.META, 0) in found


def test_preview_general_terminal_full_width_goes_below_everything():
    style = {"dual": "Vertical", "tree": True, "viewer": True, "terminals": ["general"]}
    regions = {region.kind: region for region in styles.preview_regions(style, full_width=True)}
    assert regions[styles.GENERAL_TERMINAL_PANEL].width == 1.0 and regions[styles.GENERAL_TERMINAL_PANEL].x == 0.0
    regions = {region.kind: region for region in styles.preview_regions(style, full_width=False)}
    assert regions[styles.GENERAL_TERMINAL_PANEL].width < 1.0
    stacked = {region.kind: region for region in styles.preview_regions({"dual": "Horizontal", "tree": False, "terminals": ["general"]}, full_width=True)}
    assert stacked[styles.GENERAL_TERMINAL_PANEL].x > 0.0  # con paneles apilados, a la derecha también en la ventana


def test_preview_with_viewer_on_top_and_order_of_docks():
    top = {region.kind: region for region in styles.preview_regions({"dual": "Off", "tree": False, "viewer": True, "viewer_area": "top"})}
    assert top[styles.VIEWER].y == 0.0 and top[styles.VIEWER].width == 1.0 and top[styles.PANE].y > 0.0
    style = {"dual": "Off", "tree": False, "viewer": True, "meta": True}
    above = {region.kind: region for region in styles.preview_regions(style, viewer_above=True)}
    below = {region.kind: region for region in styles.preview_regions(style, viewer_above=False)}
    assert above[styles.VIEWER].y < above[styles.META].y and below[styles.META].y < below[styles.VIEWER].y


def test_preview_with_nonsense_values_does_not_fail():
    assert styles.preview_regions({"dual": "5-Grid", "terminals": "x", "terminal_position": 3, "viewer_area": 9})


def test_page_preview_follows_what_is_selected_and_edited(window):
    page = StylesPage(window.config)
    page.show_style("Vertical panel-terminal")
    assert (styles.TERMINAL, 0) in kinds(page.preview.regions)
    page.show_style("Comandante")
    assert (styles.PANE, 1) in kinds(page.preview.regions) and (styles.TREE, 0) not in kinds(page.preview.regions)
    page.panes.setCurrentIndex(page.panes.findData("4-Grid"))
    assert (styles.PANE, 3) in kinds(page.preview.regions)
    page.pane_terminals[3].setChecked(True)
    assert (styles.TERMINAL, 3) in kinds(page.preview.regions)
    page.tree.setCurrentIndex(page.tree.findData("shared"))
    assert (styles.TREE, 0) in kinds(page.preview.regions)


def test_page_preview_knows_the_full_width_option(window):
    window.config["general_terminal_full_width"] = True
    page = StylesPage(window.config)
    assert page.preview.full_width is True


def test_preview_paints_every_kind_of_part(window):
    preview = StylePreview(window.config)
    preview.resize(500, 220)
    preview.set_style({"dual": "4-Grid", "tree": True, "double_tree": True, "viewer": True, "meta": True, "terminals": ["first", "general"]})
    assert not preview.grab().isNull()
    preview.resize(20, 20)  # sin sitio para los textos
    assert not preview.grab().isNull()
    preview.deleteLater()


def test_preview_uses_the_chosen_colors(window):
    use_colors(window, source="#006633")
    preview = StylePreview(window.config)
    preview.resize(400, 200)
    preview.set_style({"dual": "Off", "tree": False})
    image = preview.grab().toImage()
    assert image.pixelColor(100, 6) == QColor("#006633")
    preview.deleteLater()


# ------------------------------------------------------------------ cerrar un panel
@pytest.mark.parametrize("mode, pane, expected", [
    ("Vertical", 1, "Off"), ("Vertical", 0, "Off"), ("Horizontal", 0, "Off"),
    ("3-Vertical", 1, "Vertical"), ("3-Horizontal", 2, "Horizontal"), ("3-Left", 0, "Horizontal"), ("3-Left", 1, "Vertical"), ("3-Top", 2, "Vertical"), ("3-Top", 0, "Horizontal"),
    ("4-Grid", 3, "3-Top"), ("4-Grid", 0, "v(0,h(1,2))"), ("4-Vertical", 1, "3-Vertical"), ("h(v(0,2),1)", 2, "Vertical"), ("h(v(0,2),1)", 0, "h(1,0)"), ("h(v(0,h(2,3)),1)", 3, "h(v(0,2),1)"),
])
def test_layout_without_a_pane(mode, pane, expected):
    extents = {index: (100, 100) for index in range(styles.pane_count(mode))}
    result, sizes = styles.close_in_layout(mode, pane, extents)
    assert result == expected and styles.valid_layout(result)
    assert sorted(sizes) == list(range(styles.pane_count(result)))


def test_closing_the_only_pane_or_one_that_does_not_exist_is_not_possible():
    assert styles.close_in_layout("Off", 0, {}) is None
    assert styles.close_in_layout("Vertical", 3, {}) is None


def test_the_space_goes_to_the_pane_beside_it():
    sizes = styles.close_in_layout("3-Vertical", 1, {0: (100, 50), 1: (200, 50), 2: (300, 50)})[1]
    assert sizes == {0: (300.0, 50), 1: (300, 50)}
    sizes = styles.close_in_layout("3-Vertical", 0, {0: (100, 50), 1: (200, 50), 2: (300, 50)})[1]
    assert sizes == {0: (300.0, 50), 1: (300, 50)}


def test_the_space_is_shared_by_a_group_of_panes_beside_it():
    sizes = styles.close_in_layout("3-Left", 0, {0: (100, 100), 1: (100, 40), 2: (100, 60)})[1]
    assert sizes == {0: (200.0, 40), 1: (200.0, 60)}


def close_entry(window):
    return menu_entries(window)["Cerrar este panel"]


def activate(window, side: int) -> None:
    window.groups[side].current.activated.emit(window.groups[side].current)


def test_menu_offers_closing_only_with_more_than_one_pane(window):
    assert close_entry(window).isEnabled()
    window.apply_style("Explorador")
    settle()
    assert not close_entry(window).isEnabled()


def test_closing_undoes_a_split(window, folders):
    window.apply_style("Explorador")
    settle()
    window.split_pane("right")
    settle()
    assert window.dual == "Vertical" and window.active_side == 1
    close_entry(window).trigger()
    settle()
    assert window.dual == "Off" and window.dest is None and window.sides[1].isHidden()
    assert same(window.source.path, folders[0]) and window.groups[0].width() > 800
    assert window.groups[1].count() == 0  # sus pestañas se han cerrado de verdad


def test_closing_the_left_pane_keeps_the_right_one(window, folders):
    activate(window, 0)
    close_entry(window).trigger()
    settle()
    assert window.dual == "Off" and same(window.source.path, folders[1]) and window.source is window.groups[0].current
    assert window.groups[0].isVisible() and not window.groups[1].isVisible() and window.groups[0].width() > 800


def test_panes_keep_their_folders_and_tabs_when_a_lower_one_closes(window, tmp_path, folders):
    window.set_dual("4-Vertical")
    settle()
    paths = [folders[0], folders[1], tmp_path / "tercero", tmp_path / "cuarto"]
    for side, path in enumerate(paths):
        path.mkdir(exist_ok=True)
        window.groups[side].current.navigate(str(path))
    settle()
    window.groups[2].add_tab(str(folders[0]))
    third = window.groups[2]
    activate(window, 1)
    window.close_pane()
    settle()
    assert window.dual == "3-Vertical" and window.groups[1] is third and third.count() == 2
    assert same(window.groups[2].current.path, paths[3]) and all(window.groups[side].isVisible() for side in range(3)) and not window.groups[3].isVisible()
    assert window.groups[3].count() == 0
    assert corner(window.groups[0], window).x() < corner(window.groups[1], window).x() < corner(window.groups[2], window).x()


def test_active_returns_to_the_one_used_before(window):
    window.set_dual("3-Vertical")
    settle()
    activate(window, 2)
    activate(window, 1)
    window.close_pane()
    settle()
    assert window.dual == "Vertical" and window.active_side == 1  # el 2 pasó a ser el 1
    assert window.dest is window.groups[0].current


def test_space_goes_to_the_neighbour_and_the_others_do_not_change(window):
    window.set_dual("3-Vertical")
    settle()
    window.splitter.setSizes([300, 300, 900])
    settle()
    # Los lados enteros: con tres hay marcos y con dos no, y el panel de dentro gana lo que ocupaba el suyo.
    third_width = window.sides[2].width()
    first_width, second_width = window.sides[0].width(), window.sides[1].width()
    activate(window, 1)
    window.close_pane()
    settle()
    assert abs(window.sides[1].width() - third_width) < 20
    assert abs(window.sides[0].width() - (first_width + second_width)) < 20


class Session:
    def __init__(self, *_args):
        self.closed = False
        self.idle = True
        self.sent = b""

    def shell_is_idle(self):
        return self.idle

    def current_directory(self):
        return None

    def write(self, data):
        self.sent += data

    def resize(self, _cols, _rows):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_terminals(monkeypatch):
    from directory_commander_tux_tux.ui.terminal import TerminalView
    monkeypatch.setattr(TerminalView, "session_factory", Session)


def test_closing_ends_the_terminal_of_that_pane_and_keeps_the_others(window, fake_terminals):
    window.set_dual("3-Vertical")
    settle()
    for side in (0, 1, 2):
        activate(window, side)
        window.run_command("Set TERMINAL=On")
    settle()
    closing, kept = window.terminals[1].session, window.terminals[2].session
    activate(window, 1)
    window.close_pane()
    settle()
    assert closing.closed and not kept.closed
    assert window.terminals[1].session is kept and window.terminal_panels[1].isVisible()
    assert window.terminals[3].session is None and window.terminal_panels[3].isHidden()


def test_a_running_program_asks_before_closing(window, fake_terminals, monkeypatch):
    window.run_command("Set TERMINAL=On")
    settle()
    session = window.terminals[0].session
    session.idle = False
    questions = []
    monkeypatch.setattr(window, "_confirm", lambda text, _dangerous=False: questions.append(text) or False)
    window.close_pane()
    assert window.dual == "Vertical" and len(questions) == 1 and not session.closed
    monkeypatch.setattr(window, "_confirm", lambda _text, _dangerous=False: True)
    window.close_pane()
    settle()
    assert window.dual == "Off" and session.closed


def test_terminal_of_a_moved_pane_still_works_with_its_new_number(window, fake_terminals, folders, tmp_path):
    window.set_dual("3-Vertical")
    settle()
    activate(window, 2)
    window.run_command("Set TERMINAL=On")
    settle()
    terminal = window.terminals[2]
    activate(window, 1)
    window.close_pane()
    settle()
    assert window.terminals[1] is terminal
    terminal.screen_.feed_text("PS C:\\> " if terminal._family == "powershell" else "$ ")
    terminal._last_activity = 0
    other = tmp_path / "otra"
    other.mkdir()
    window.groups[1].current.navigate(str(other))
    for _attempt in range(40):
        QTest.qWait(25)
        if terminal.session.sent:
            break
    assert b"otra" in terminal.session.sent
    # Sus botones actúan sobre él, con su número nuevo.
    window.terminal_follow_buttons[1].click()
    assert not terminal.following
    assert terminal.folder() == window.groups[1].current.path
    terminal.session.on_exit = None
    terminal.finished.emit()
    assert window.terminal_panels[1].isHidden()


def test_tree_of_a_moved_pane_acts_on_its_pane(window, tmp_path):
    window.apply_style("Vertical doble árbol")
    settle()
    window.split_pane("right")
    settle()
    tree = window.pane_trees[2]
    activate(window, 1)
    window.close_pane()
    settle()
    assert window.pane_trees[1] is tree and window.tree_panels[1].isVisible()
    other = tmp_path / "otra"
    other.mkdir()
    tree.pathActivated.emit(str(other))
    settle()
    assert same(window.groups[1].current.path, other) and window.active_side == 1


def test_closed_pane_can_be_used_again_empty(window, folders):
    window.split_pane("right")
    settle()
    window.groups[2].add_tab(str(folders[0]))
    window.close_pane()
    settle()
    assert window.groups[3].count() == 0
    window.split_pane("below")
    settle()
    assert window.groups[2].count() == 1 and same(window.groups[2].current.path, window.groups[window._dest_side].current.path)


def test_closing_and_splitting_many_times_keeps_everything_apart(window):
    for where in ("right", "below", "right", None, "below", None, None, "right", "below", None):
        if where is None:
            window.close_pane()
        else:
            window.split_pane(where)
        settle()
        count = styles.pane_count(window.dual)
        rects = [window.groups[side].rect().translated(corner(window.groups[side], window)) for side in range(count)]
        assert all(window.groups[side].isVisible() and window.groups[side].current is not None for side in range(count))
        assert all(not window.groups[side].isVisible() for side in range(count, styles.MAX_PANES))
        for index, rect in enumerate(rects):
            assert rect.width() > 40 and rect.height() > 40 and not any(rect.intersects(other) for other in rects[index + 1:])
        assert window.source is window.groups[window.active_side].current and window.active_side < count


def test_session_after_closing_a_pane(qapp_for_gc, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    third = tmp_path / "tercero"
    third.mkdir()
    first = open_window(folders)
    first.set_dual("3-Vertical")
    settle()
    first.groups[2].current.navigate(str(third))
    settle()
    activate(first, 1)
    first.close_pane()
    settle()
    first.close()
    first.deleteLater()
    QApplication.processEvents()
    second = open_window(())  # sin rutas de inicio, que sustituirían a las guardadas
    settle()
    assert second.dual == "Vertical" and same(second.groups[1].current.path, third) and second.groups[2].count() == 0
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_a_style_can_be_used_right_after_closing(window):
    window.set_dual("4-Grid")
    settle()
    activate(window, 0)
    window.close_pane()
    settle()
    window.apply_style("Vertical doble panel")
    settle()
    assert window.dual == "Vertical" and window.dest is not None
    window.apply_style("Horizontal panel-terminal")
    settle()
    assert window.dual == "Off" and window.groups[0].isVisible()


def test_wrong_value_of_the_pane_command(window):
    with pytest.raises(ValueError):
        window._set_pane("abrir")


def test_closing_with_a_single_pane_does_nothing(window):
    window.apply_style("Explorador")
    settle()
    window.close_pane()
    assert window.dual == "Off" and window.source is not None


def test_window_closes_cleanly_after_moving_panes(qapp_for_gc, tmp_path, monkeypatch, folders, fake_terminals):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    main = open_window(folders)
    main.set_dual("4-Grid")
    settle()
    for side in range(4):
        activate(main, side)
        main.run_command("Set TERMINAL=On")
    settle()
    sessions = [terminal.session for terminal in main.terminals[:4]]
    activate(main, 1)
    main.close_pane()
    settle()
    main.close()
    main.deleteLater()
    QApplication.processEvents()
    assert all(session.closed for session in sessions)


# ------------------------------------------------------------------ la X de cada panel
def close_button(view):
    return next(button for button, icon in view._tool_buttons if icon == "close")


def test_x_closes_only_its_pane_even_if_it_is_not_the_active_one(window, tmp_path, folders):
    window.set_dual("3-Vertical")
    settle()
    third = tmp_path / "tercero"
    third.mkdir()
    window.groups[2].current.navigate(str(third))
    settle()
    activate(window, 0)
    close_button(window.groups[1].current).click()
    settle()
    assert window.dual == "Vertical" and same(window.groups[0].current.path, folders[0]) and same(window.groups[1].current.path, third)
    assert window.active_side == 0  # el activo sigue siendo el que era


def test_x_says_what_it_does(window):
    assert close_button(window.groups[0].current).toolTip() == "Cerrar este panel"
    labels = dict(next(buttons for bar, _title, buttons in __import__("directory_commander_tux_tux.core.bar_buttons", fromlist=["bars"]).bars({}) if bar == "pane"))
    assert labels["close"] == "Cerrar este panel"


def test_x_closes_its_tree_and_its_terminal(window, fake_terminals):
    window.apply_style("Vertical doble árbol")
    settle()
    window.groups[1].current.activated.emit(window.groups[1].current)
    window.run_command("Set TERMINAL=On")
    settle()
    session = window.terminals[1].session
    tree_panel, terminal_panel = window.tree_panels[1], window.terminal_panels[1]
    close_button(window.groups[1].current).click()
    settle()
    assert window.dual == "Off" and session.closed and tree_panel.isHidden() and terminal_panel.isHidden()
    assert window.tree_panels[0].isVisible()


def test_x_on_the_first_of_two_keeps_the_second(window, folders):
    close_button(window.groups[0].current).click()
    settle()
    assert window.dual == "Off" and same(window.source.path, folders[1])


def test_with_one_pane_there_is_no_x_and_it_cannot_be_closed(window):
    window.apply_style("Explorador")
    settle()
    view = window.groups[0].current
    assert close_button(view).isHidden()
    messages = []
    window.statusBar().messageChanged.connect(messages.append)
    window.run_command("Set PANE=Close")
    assert window.dual == "Off" and window.source is view and view.isVisible()
    assert any("al menos un panel" in message for message in messages)


def test_closing_down_to_one_always_leaves_a_pane(window):
    window.set_dual("4-Grid")
    settle()
    for _step in range(6):
        window.run_command("Set PANE=Close")
        settle()
    assert window.dual == "Off" and window.source is not None and window.groups[0].isVisible()


# ------------------------------------------------------------------ arrastrar entre paneles
def drop_on(widget, paths, modifiers=Qt.KeyboardModifier.NoModifier, position=None) -> None:
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    point = position or widget.rect().center()
    enter = QDragEnterEvent(point, actions, mime, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(widget, enter)
    assert enter.isAccepted()
    move = QDragMoveEvent(point, actions, mime, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(widget, move)
    drop = QDropEvent(QPointF(point), actions, mime, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(widget, drop)
    DROPPED.append(mime)  # Qt la usa después de volver


DROPPED: list = []


def wait_for(condition, seconds: float = 8.0) -> bool:
    for _attempt in range(int(seconds * 40)):
        QTest.qWait(25)
        if condition():
            return True
    return condition()


@pytest.fixture
def four_folders(window, tmp_path):
    window.set_dual("4-Grid")
    settle()
    folders = [tmp_path / f"panel{index}" for index in range(4)]
    for side, folder in enumerate(folders):
        folder.mkdir()
        window.groups[side].current.navigate(str(folder))
    settle()
    return folders


@pytest.mark.parametrize("origin", range(4))
@pytest.mark.parametrize("target", range(4))
def test_drag_moves_between_every_pair_of_panes(window, four_folders, origin, target):
    if origin == target:
        return
    item = four_folders[origin] / "a.txt"
    item.write_text("x", encoding="utf-8")
    view = window.groups[target].current
    drop_on(view.current_view.viewport(), [item])
    assert wait_for(lambda: (four_folders[target] / "a.txt").exists() and not item.exists() and window.jobs.running == 0)
    assert wait_for(lambda: "a.txt" in [entry.name for entry in view.model.entries])
    assert wait_for(lambda: "a.txt" not in [entry.name for entry in window.groups[origin].current.model.entries])


@pytest.mark.parametrize("target", [1, 2, 3])
def test_ctrl_copies_to_any_pane(window, four_folders, target):
    item = four_folders[0] / "a.txt"
    item.write_text("x", encoding="utf-8")
    drop_on(window.groups[target].current.current_view.viewport(), [item], Qt.KeyboardModifier.ControlModifier)
    assert wait_for(lambda: (four_folders[target] / "a.txt").exists() and window.jobs.running == 0)
    assert item.exists()


def test_a_folder_can_be_dragged_to_another_pane(window, four_folders):
    folder = four_folders[2] / "carpeta"
    folder.mkdir()
    (folder / "dentro.txt").write_text("x", encoding="utf-8")
    drop_on(window.groups[3].current.current_view.viewport(), [folder])
    assert wait_for(lambda: (four_folders[3] / "carpeta" / "dentro.txt").exists() and not folder.exists() and window.jobs.running == 0)


def test_several_items_to_a_pane(window, four_folders):
    items = [four_folders[1] / f"{name}.txt" for name in "abc"]
    for item in items:
        item.write_text("x", encoding="utf-8")
    drop_on(window.groups[2].current.current_view.viewport(), items)
    assert wait_for(lambda: all((four_folders[2] / item.name).exists() for item in items) and window.jobs.running == 0)


@pytest.mark.parametrize("part", ["pane_bar", "status"])
def test_dropping_on_the_bar_or_the_status_of_a_pane_also_works(window, four_folders, part):
    item = four_folders[0] / "a.txt"
    item.write_text("x", encoding="utf-8")
    view = window.groups[3].current
    widget = getattr(view, part)
    drop_on(view, [item], position=widget.mapTo(view, widget.rect().center()))
    assert wait_for(lambda: (four_folders[3] / "a.txt").exists() and window.jobs.running == 0)


def test_dropping_on_a_pane_in_the_same_folder_does_nothing(window, four_folders):
    item = four_folders[0] / "a.txt"
    item.write_text("x", encoding="utf-8")
    window.groups[1].current.navigate(str(four_folders[0]))
    settle()
    view = window.groups[1].current
    drop_on(view.current_view.viewport(), [item])
    QTest.qWait(150)
    assert item.exists() and window.jobs.running == 0
    assert [path.name for path in four_folders[0].iterdir()] == ["a.txt"]


def test_drag_works_after_dividing_and_closing_panes(window, tmp_path, folders):
    window.split_pane("below")
    settle()
    new_folder = tmp_path / "nueva"
    new_folder.mkdir()
    window.source.navigate(str(new_folder))
    settle()
    item = folders[1] / "b.txt"
    item.write_text("x", encoding="utf-8")
    drop_on(window.source.current_view.viewport(), [item])
    assert wait_for(lambda: (new_folder / "b.txt").exists() and window.jobs.running == 0)
    activate(window, 0)
    window.close_pane()
    settle()
    assert window.dual != "4-Grid"
    drop_on(window.groups[0].current.current_view.viewport(), [new_folder / "b.txt"])
    assert wait_for(lambda: window.jobs.running == 0 and (folders[1] / "b.txt").exists())


def test_right_button_drop_on_any_pane_asks_what_to_do(window, four_folders, monkeypatch):
    item = four_folders[0] / "a.txt"
    item.write_text("x", encoding="utf-8")
    asked = []
    monkeypatch.setattr(window, "_files_dropped_menu", lambda view, paths, target, move: asked.append((view, paths)))
    view = window.groups[3].current
    view._dropped([str(item)], None, True, True)
    QTest.qWait(30)
    assert view.current_view.drop_handler is not None


# ------------------------------------------------------------------ el marco de color de cada panel
def frame(window, index):
    side = window.sides[index]
    return side.frame_color, side.frame_line, side.frame_alpha, side.contentsMargins().left()


def test_frame_values_keep_the_default_where_nothing_valid_was_chosen():
    assert pane_colors.clean_frame_colors(["#AABBCC", "rojo", None]) == ["#aabbcc"] + [""] * 7
    assert pane_colors.frame_colors(["", "#000000"])[:3] == [pane_colors.FRAME_COLORS[0], "#000000", pane_colors.FRAME_COLORS[2]]
    assert pane_colors.frame_width(99) == pane_colors.MAX_FRAME_WIDTH and pane_colors.frame_width(True) == pane_colors.DEFAULT_FRAME_WIDTH
    assert pane_colors.frame_dim(-5) == 0 and pane_colors.frame_dim(100) == pane_colors.MAX_FRAME_DIM
    assert pane_colors.frames_after(1) == 2 and pane_colors.frames_after(9) == 7 and pane_colors.frames_after("x") == 2
    assert [pane_colors.frames_shown("more_than", 2, count) for count in (2, 3)] == [False, True]
    assert [pane_colors.frames_shown("more_than", 4, count) for count in (4, 5)] == [False, True]
    assert pane_colors.frames_shown("always", 7, 1) and pane_colors.frames_shown("otro", 2, 3)


def test_with_more_than_two_panes_each_one_gets_a_frame_of_its_color(window):
    assert all(side.frame_color is None and side.contentsMargins().left() == 0 for side in window.sides)
    window.split_pane("right")
    settle()
    room = max(pane_colors.DEFAULT_FRAME_WIDTH, pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH)
    assert [side.frame_color for side in window.sides[:3]] == list(pane_colors.FRAME_COLORS[:3])
    assert all(side.contentsMargins().left() == room for side in window.sides[:3])
    side = window.sides[2]
    inside = window.groups[2].mapTo(side, QPoint(0, 0))
    assert inside.x() >= room and inside.y() >= room  # lo de dentro deja sitio al marco
    image = side.grab().toImage()
    assert image.pixelColor(1, side.height() // 2) == QColor(pane_colors.FRAME_COLORS[2])  # el nuevo es el activo: sin atenuar
    window.close_pane()
    settle()
    assert all(side.frame_color is None for side in window.sides)


def test_a_pane_divided_later_also_gets_its_frame(window):
    """Al colocarse en su sitio, Qt le volvía a aplicar los estilos y le quitaba el sitio del marco."""
    window.groups[1].current.activated.emit(window.groups[1].current)
    window.split_pane("below")
    settle()
    window.groups[0].current.activated.emit(window.groups[0].current)
    window.split_pane("below")
    settle()
    assert window.dual == "h(v(0,3),v(1,2))"
    room = max(pane_colors.DEFAULT_FRAME_WIDTH, pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH)
    for index in range(4):
        side = window.sides[index]
        assert side.frame_color == pane_colors.FRAME_COLORS[index] and side.contentsMargins().left() == room, index
        assert window.groups[index].mapTo(side, QPoint(0, 0)).x() >= room, index
    image = window.sides[3].grab().toImage()
    assert image.pixelColor(1, window.sides[3].height() // 2) == QColor(pane_colors.FRAME_COLORS[3])


def test_the_active_frame_stands_out_and_the_others_are_dimmed_only_if_chosen(window):
    window.run_command("Set DUAL=4-Grid")
    settle()
    assert frame(window, 0)[1:3] == (pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH, 255)
    assert frame(window, 1)[1:3] == (pane_colors.DEFAULT_FRAME_WIDTH, 255)  # sin atenuar, si no se pide
    window.config["pane_frame_dim_on"] = True
    window._preferences_changed()
    alpha = round(255 * (100 - pane_colors.DEFAULT_FRAME_DIM) / 100)
    assert frame(window, 1)[1:3] == (pane_colors.DEFAULT_FRAME_WIDTH, alpha) and frame(window, 0)[2] == 255
    margins = [frame(window, index)[3] for index in range(4)]
    window.groups[2].current.activated.emit(window.groups[2].current)
    assert frame(window, 2)[1:3] == (pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH, 255) and frame(window, 0)[1:3] == (pane_colors.DEFAULT_FRAME_WIDTH, alpha)
    assert [frame(window, index)[3] for index in range(4)] == margins  # cambiar de activo no mueve nada


def test_frames_follow_the_preferences(window):
    window.config["pane_frames"] = "always"
    window.config["pane_frame_width"] = 6
    window.config["pane_frame_active_width"] = 2
    window.config["pane_frame_dim_on"] = True
    window.config["pane_frame_dim"] = 0
    window.config["pane_frame_colors"] = ["", "#123456"]
    window._preferences_changed()
    assert frame(window, 1) == ("#123456", 6, 255, 6) and frame(window, 0) == (pane_colors.FRAME_COLORS[0], 2, 255, 6)
    window.config["pane_frames"] = "more_than"
    window.config["pane_frames_after"] = 4
    window.run_command("Set DUAL=4-Grid")
    assert all(side.frame_color is None for side in window.sides)
    window.split_pane("right")
    assert all(window.sides[index].frame_color for index in range(5))


def test_the_toolbar_button_shows_and_hides_the_frames(window):
    window.run_command("Set DUAL=3-Vertical")
    assert window.sides[0].frame_color
    window.run_command("Set PANEFRAMES=Toggle")
    assert window.config["pane_frames_on"] is False and all(side.frame_color is None and side.contentsMargins().left() == 0 for side in window.sides)
    window.run_command("Set PANEFRAMES=Toggle")
    assert window.config["pane_frames_on"] is True and window.sides[0].frame_color
    assert any(item.get("commands") == ["Set PANEFRAMES=Toggle"] for item in window.config["toolbar"])


def test_preferences_page_saves_the_frame_options(window):
    dialog = PreferencesDialog(window.config)
    assert dialog.frames_on.isChecked() and dialog.frame_mode.currentData() == "more_than" and dialog.frames_after.value() == 2
    assert dialog.frame_width.value() == pane_colors.DEFAULT_FRAME_WIDTH and dialog.frame_active_width.value() == pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH
    dialog.frame_mode.setCurrentIndex(dialog.frame_mode.findData("always"))
    assert not dialog.frames_after.isEnabled()
    dialog.frames_on.setChecked(False)
    assert not dialog.frame_width.isEnabled() and not dialog.frame_colors[0].isEnabled() and not dialog.frame_mode.isEnabled()
    dialog.frames_on.setChecked(True)
    dialog.frame_mode.setCurrentIndex(dialog.frame_mode.findData("more_than"))
    dialog.frames_after.setValue(3)
    dialog.frame_width.setValue(5)
    dialog.frame_active_width.setValue(8)
    assert not dialog.frame_dim_on.isChecked() and not dialog.frame_dim.isEnabled()
    dialog.frame_dim_on.setChecked(True)
    assert dialog.frame_dim.isEnabled()
    dialog.frame_dim.setValue(70)
    dialog.frame_colors[3].set_value("#abcdef")
    dialog.apply_to(window.config)
    assert [window.config[key] for key in ("pane_frames_on", "pane_frames", "pane_frames_after", "pane_frame_width", "pane_frame_active_width", "pane_frame_dim_on",
                                           "pane_frame_dim")] == [True, "more_than", 3, 5, 8, True, 70]
    assert window.config["pane_frame_colors"] == ["", "", "", "#abcdef", "", "", "", ""]
    dialog.reset_frame_colors.click()
    dialog.apply_to(window.config)
    assert window.config["pane_frame_colors"] == []


def test_frame_settings_can_be_imported_and_wrong_values_are_dropped(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {
        "pane_frames": "always", "pane_frame_width": 50, "pane_frame_colors": ["#FFFFFF", 3], "pane_frames_after": 1, "pane_frame_dim": 300,
        "pane_frames_on": False}}), encoding="utf-8")
    values, _skipped = settings_io.read_settings(str(path))
    assert values["pane_frames"] == "always" and values["pane_frame_width"] == pane_colors.MAX_FRAME_WIDTH and values["pane_frames_on"] is False
    assert values["pane_frame_colors"] == ["#ffffff"] + [""] * 7 and values["pane_frames_after"] == 2 and values["pane_frame_dim"] == pane_colors.MAX_FRAME_DIM
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"pane_frames": "a veces"}}), encoding="utf-8")
    values, _skipped = settings_io.read_settings(str(path))
    assert "pane_frames" not in values


@pytest.mark.parametrize("old, mode, on", [("never", "more_than", False), ("more_than_two", "more_than", True), ("always", "always", True)])
def test_the_first_way_of_choosing_frames_is_kept(tmp_path, old, mode, on):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 17, "pane_frames": old}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["pane_frames"] == mode and config["pane_frames_on"] is on


def test_an_old_toolbar_gets_the_frames_button_next_to_the_general_terminal(tmp_path):
    toolbar = [{"label": "Otro", "commands": ["Go UP"]}, {"label": "Terminal general", "commands": ["Set GENERALTERMINAL=Toggle"]}, {"label": "Último", "commands": ["Go BACK"]}]
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 17, "toolbar": toolbar}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [item["commands"][0] for item in config["toolbar"]] == ["Go UP", "Set GENERALTERMINAL=Toggle", "Set PANEFRAMES=Toggle", "Go BACK", "Set GENERATIONMARKS=Toggle", "Set OPENVIDEOS=Toggle", "Set OPENIMAGES=Toggle", "Set NOTES=Toggle"]
