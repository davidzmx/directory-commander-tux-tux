"""Los estilos con terminal y los que crea el usuario."""
import json
import os

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import settings_io, styles
from directory_commander_tux_tux.core.config import DEFAULT_STYLES, Config
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import GENERAL_TERMINAL, MainWindow
from directory_commander_tux_tux.ui.styles_page import StylesPage
from directory_commander_tux_tux.ui.terminal import TerminalView


class FakeSession:
    instances: list = []

    def __init__(self, argv, cwd, cols, rows, on_data, on_exit, env=None) -> None:
        self.argv, self.cwd, self.on_exit = argv, cwd, on_exit
        self.closed = False
        self.sent = b""
        FakeSession.instances.append(self)

    def shell_is_idle(self) -> bool:
        return True

    def current_directory(self):
        return None

    def write(self, data: bytes) -> None:
        self.sent += data

    def resize(self, cols: int, rows: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folders(tmp_path):
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    left.mkdir()
    right.mkdir()
    return left, right


@pytest.fixture
def sessions(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(TerminalView, "session_factory", FakeSession)
    return FakeSession.instances


def open_window(folders) -> MainWindow:
    main = MainWindow(Config(), [str(folder) for folder in folders])
    main.resize(1500, 800)
    main.show()
    QTest.qWait(30)
    return main


def close_window(main) -> None:
    main.close()
    main.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    main = open_window(folders)
    yield main
    close_window(main)


def settle() -> None:
    QTest.qWait(40)
    for _ in range(4):  # con la máquina cargada, los divisores anidados pueden necesitar más de una pasada para colocarse
        QApplication.processEvents()


def left_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, 0)).x()


def right_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(widget.width(), 0)).x()


def top_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, 0)).y()


def bottom_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, widget.height())).y()


def use(window, name: str) -> None:
    window.run_command(f'Set STYLE="{name}"')
    settle()


# ------------------------------------------------------------------ los dos estilos nuevos
def test_vertical_panel_terminal_is_tree_pane_and_a_terminal_on_the_right(window, folders, sessions):
    use(window, "Vertical panel-terminal")
    pane, terminal = window.groups[0], window.terminal_panels[0]
    assert not window.tree_dock.isHidden() and window.dest is None and window.sides[1].isHidden() and terminal.isVisible()
    assert right_of(window.tree_dock, window) <= left_of(pane, window)
    assert left_of(terminal, window) >= right_of(pane, window)
    assert abs(top_of(terminal, window) - top_of(pane, window)) < 3 and abs(terminal.height() - pane.height()) < 3
    assert abs(terminal.width() - pane.width()) < 20  # en el sitio del segundo panel
    assert len(sessions) == 1 and os.path.samefile(sessions[0].cwd, folders[0])


def test_horizontal_panel_terminal_is_tree_pane_and_a_terminal_below(window):
    use(window, "Horizontal panel-terminal")
    pane, terminal = window.groups[0], window.terminal_panels[0]
    assert not window.tree_dock.isHidden() and window.dest is None and terminal.isVisible()
    assert right_of(window.tree_dock, window) <= left_of(pane, window)
    assert top_of(terminal, window) >= bottom_of(pane, window)
    assert abs(left_of(terminal, window) - left_of(pane, window)) < 3 and abs(terminal.width() - pane.width()) < 3
    assert abs(terminal.height() - pane.height()) < 20


def test_the_terminal_of_the_style_follows_the_pane(window, folders, sessions):
    use(window, "Vertical panel-terminal")
    terminal = window.terminals[0]
    terminal.screen_.feed_text("PS C:\\> " if terminal._family == "powershell" else "$ ")
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    for _attempt in range(40):
        QTest.qWait(25)
        if sessions[0].sent:
            break
    assert b"derecha" in sessions[0].sent


def test_focus_stays_in_the_list_when_choosing_the_style(window):
    window.source.focus_list()
    use(window, "Horizontal panel-terminal")
    assert window.focusWidget() is not window.terminals[0]


def test_leaving_the_style_hides_its_terminal_and_coming_back_reuses_the_session(window, sessions):
    use(window, "Vertical panel-terminal")
    use(window, "Vertical doble panel")
    assert window.terminal_panels[0].isHidden() and window.dest is not None
    assert len(sessions) == 1 and not sessions[0].closed  # lo que tenga en marcha sigue
    use(window, "Horizontal panel-terminal")
    assert window.terminal_panels[0].isVisible() and len(sessions) == 1
    assert top_of(window.terminal_panels[0], window) >= bottom_of(window.groups[0], window)


def test_changing_between_both_styles_moves_the_terminal(window):
    use(window, "Vertical panel-terminal")
    use(window, "Horizontal panel-terminal")
    assert top_of(window.terminal_panels[0], window) >= bottom_of(window.groups[0], window)
    use(window, "Vertical panel-terminal")
    assert left_of(window.terminal_panels[0], window) >= right_of(window.groups[0], window)
    assert abs(window.terminal_panels[0].width() - window.groups[0].width()) < 20


def test_a_terminal_opened_by_hand_is_not_closed_by_a_style_without_terminals(window):
    window.run_command("Set TERMINAL=On")
    settle()
    use(window, "Comandante")
    assert window.terminal_panels[0].isVisible()


def test_a_style_with_terminals_closes_the_others(window):
    window.run_command("Set GENERALTERMINAL=On")
    settle()
    use(window, "Vertical panel-terminal")
    assert window.terminal_panels[GENERAL_TERMINAL].isHidden() and window.terminal_panels[0].isVisible()


def test_automatic_position_returns_after_the_style(window):
    use(window, "Vertical panel-terminal")
    assert window.pane_areas[0].orientation() == Qt.Orientation.Horizontal
    use(window, "Vertical doble panel")
    window.run_command("Set TERMINAL=On")
    settle()
    assert window.pane_areas[0].orientation() == Qt.Orientation.Vertical
    assert top_of(window.terminal_panels[0], window) >= bottom_of(window.groups[0], window)


def test_session_reopens_the_style_with_its_terminal_and_its_size(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = open_window(folders)
    use(first, "Vertical panel-terminal")
    first.pane_areas[0].setSizes([900, 300])
    settle()
    width = first.terminal_panels[0].width()
    close_window(first)
    second = open_window(folders)
    settle()
    assert second.config["style"] == "Vertical panel-terminal" and second.dest is None
    assert second.terminal_panels[0].isVisible() and second.pane_areas[0].orientation() == Qt.Orientation.Horizontal
    assert abs(second.terminal_panels[0].width() - width) < 40
    close_window(second)


def test_session_respects_that_the_user_closed_the_terminal_of_the_style(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = open_window(folders)
    use(first, "Horizontal panel-terminal")
    first.run_command("Set TERMINAL=Off")
    close_window(first)
    second = open_window(folders)
    settle()
    assert second.terminal_panels[0].isHidden()
    close_window(second)


@pytest.mark.parametrize("version", [6, 8, 10])
def test_upgrade_adds_both_styles_next_to_their_partners(tmp_path, version):
    stored = [{"name": "Vertical doble árbol", "dual": "Vertical"}, {"name": "Horizontal doble árbol", "dual": "Horizontal"}, {"name": "Mío", "dual": "Off"}]
    (tmp_path / "config.json").write_text(json.dumps({"config_version": version, "styles": stored}), encoding="utf-8")
    names = [style["name"] for style in Config(str(tmp_path))["styles"]]
    assert names.index("Vertical panel-terminal") == names.index("Vertical doble árbol") + 1
    assert names.index("Horizontal panel-terminal") == names.index("Horizontal doble árbol") + 1
    assert names.count("Vertical panel-terminal") == 1 and "Mío" in names


def test_upgrade_without_partners_puts_them_at_the_end_and_only_once(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 10, "styles": [{"name": "Mío"}]}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [style["name"] for style in config["styles"]] == ["Mío", "Vertical panel-terminal", "Horizontal panel-terminal"]
    config.save()
    assert len(Config(str(tmp_path))["styles"]) == 3


# ------------------------------------------------------------------ reglas sin interfaz
def test_terminals_and_position_of_a_style():
    assert styles.terminals_of({}) is None
    assert styles.terminals_of({"terminals": "first"}) is None
    assert styles.terminals_of({"terminals": ["first", "nada", 3]}) == {"first"}
    assert styles.position_of({"terminal_position": "right"}) == "right"
    assert styles.position_of({"terminal_position": "arriba"}) is None


def test_unique_name_ignores_case():
    assert styles.unique_name("Mío", ["otro"]) == "Mío"
    assert styles.unique_name("Mío", ["MÍO", "mío (2)"]) == "Mío (3)"


def test_rename_reaches_everything_that_names_a_style():
    config = {"style": "Viejo", "bar_buttons": {"styles": ["Viejo", "Otro"]}, "bar_order": {"styles": ["Otro", "Viejo"], "pane": ["Viejo"]},
              "layouts": {"a": {"style": "Viejo"}, "b": {"style": "Otro"}, "c": 3}}
    styles.rename_references(config, {"Viejo": "Nuevo"})
    assert config["style"] == "Nuevo" and config["bar_buttons"]["styles"] == ["Nuevo", "Otro"] and config["bar_order"]["styles"] == ["Otro", "Nuevo"]
    assert config["bar_order"]["pane"] == ["Viejo"] and config["layouts"]["a"]["style"] == "Nuevo" and config["layouts"]["b"]["style"] == "Otro"


def test_missing_defaults_are_copies():
    missing = styles.missing_defaults([{"name": style["name"]} for style in DEFAULT_STYLES[1:]])
    assert [style["name"] for style in missing] == [DEFAULT_STYLES[0]["name"]]
    missing[0]["dual"] = "x"
    assert DEFAULT_STYLES[0]["dual"] != "x"


# ------------------------------------------------------------------ la página de estilos
@pytest.fixture
def page(window):
    return StylesPage(window.config)


def select(page, name: str) -> None:
    page.show_style(name)


def set_combo(box, value) -> None:
    box.setCurrentIndex(box.findData(value))


def stored(window, name: str) -> dict:
    return next(style for style in window.config["styles"] if style["name"] == name)


def test_page_shows_every_style_starting_with_the_current_one(window, page):
    assert page.names.count() == len(window.config["styles"])
    assert page.names.currentItem().text() == "Vertical doble panel" and page.panes.currentData() == "Vertical" and page.tree.currentData() == "shared"
    select(page, "Vertical panel-terminal")
    assert page.panes.currentData() == "Off" and page.terminal_first.isChecked() and page.position.currentData() == "right"
    assert not page.terminal_second.isEnabled() and page.position.isEnabled()
    select(page, "Vertical doble árbol")
    assert page.tree.currentData() == "each" and not page.terminal_first.isChecked() and not page.position.isEnabled()
    select(page, "Imágenes")
    assert page.viewer.isChecked() and page.view.currentData() == "thumbnails"


def test_new_style_with_everything_chosen(window, page):
    page.add()
    assert page.name.hasFocus() or page.name.selectedText() == "Estilo nuevo"
    page.name.setText("Doble con terminales")
    page.name.textEdited.emit("Doble con terminales")
    set_combo(page.panes, "Horizontal")
    set_combo(page.tree, "each")
    set_combo(page.view, "details")
    page.meta.setChecked(True)
    page.terminal_first.setChecked(True)
    page.terminal_second.setChecked(True)
    page.terminal_general.setChecked(True)
    set_combo(page.position, "below")
    page.apply_to(window.config)
    assert stored(window, "Doble con terminales") == {"name": "Doble con terminales", "dual": "Horizontal", "tree": True, "double_tree": True, "viewer": False, "meta": True,
                                                       "view": "details", "terminals": ["first", "second", "general"], "terminal_position": "below"}


def test_applying_a_style_made_on_the_page_places_everything(window, page):
    page.add()
    page.name.setText("Pruebas")
    page.name.textEdited.emit("Pruebas")
    set_combo(page.panes, "Vertical")
    set_combo(page.tree, "none")
    page.terminal_first.setChecked(True)
    page.terminal_second.setChecked(True)
    page.terminal_general.setChecked(True)
    page.apply_to(window.config)
    window._preferences_changed()
    assert "Pruebas" in window._style_buttons
    use(window, "Pruebas")
    assert window.tree_dock.isHidden() and window.dest is not None
    assert all(window.terminal_panels[index].isVisible() for index in (0, 1, GENERAL_TERMINAL))
    assert top_of(window.terminal_panels[0], window) >= bottom_of(window.groups[0], window)


def test_second_terminal_is_not_saved_with_a_single_pane(window, page):
    page.add()
    page.terminal_second.setChecked(True)
    set_combo(page.panes, "Off")
    page.terminal_first.setChecked(True)
    page.apply_to(window.config)
    assert stored(window, "Estilo nuevo")["terminals"] == ["first"]


def test_editing_only_the_name_of_a_builtin_keeps_it_free_of_terminals(window, page):
    select(page, "Comandante")
    page.name.setText("Mi comandante")
    page.name.textEdited.emit("Mi comandante")
    page.apply_to(window.config)
    assert "terminals" not in stored(window, "Mi comandante")


def test_renaming_updates_the_current_style_and_the_bar(window):
    names = [style["name"] for style in window.config["styles"]]
    window.config["bar_buttons"] = {"styles": ["Comandante"]}
    window.config["bar_order"] = {"styles": ["Comandante", "Único", *(name for name in names if name not in ("Comandante", "Único"))]}
    page = StylesPage(window.config)
    select(page, "Vertical doble panel")
    page.name.setText("Principal")
    page.name.textEdited.emit("Principal")
    select(page, "Comandante")
    page.name.setText("Mío")
    page.name.textEdited.emit("Mío")
    page.apply_to(window.config)
    assert window.config["style"] == "Principal"
    assert window.config["bar_buttons"]["styles"] == ["Mío"]
    assert [style["name"] for style in window.config["styles"]][:2] == ["Mío", "Único"]


def test_repeated_and_empty_names_are_resolved(window, page):
    select(page, "Comandante")
    page.name.setText("único")
    page.name.textEdited.emit("único")
    select(page, "Explorador")
    page.name.setText("   ")
    page.name.textEdited.emit("   ")
    page.apply_to(window.config)
    names = [style["name"] for style in window.config["styles"]]
    assert "Explorador" in names and len({name.casefold() for name in names}) == len(names)
    assert any(name.casefold() == "único (2)" for name in names)


def test_duplicate_keeps_everything_with_another_name(window, page):
    select(page, "Tira de Fotogramas")
    page.duplicate()
    page.apply_to(window.config)
    copy = stored(window, "Tira de Fotogramas (2)")
    assert copy["viewer_area"] == "top" and copy["view"] == "filmstrip"


def test_removing_and_bringing_back(window, page):
    select(page, "Comandante")
    page.remove()
    assert page.names.count() == len(window.config["styles"]) - 1
    page.apply_to(window.config)
    assert not any(style["name"] == "Comandante" for style in window.config["styles"])
    page = StylesPage(window.config)
    page.restore_defaults()
    page.apply_to(window.config)
    assert stored(window, "Comandante")["dual"] == "Vertical"
    assert len(window.config["styles"]) == len(DEFAULT_STYLES)


def test_the_last_style_cannot_be_removed(window):
    window.config["styles"] = [{"name": "Solo", "dual": "Off"}]
    page = StylesPage(window.config)
    assert not page.remove_button.isEnabled()
    page.remove()
    assert page.names.count() == 1


def test_removing_the_current_style_applies_another(window, page):
    select(page, "Vertical doble panel")
    page.remove()
    page.apply_to(window.config)
    window._preferences_changed()
    first = window.config["styles"][0]["name"]
    assert window.config["style"] == first and window._style_buttons[first].isChecked()


def test_changing_the_style_in_use_applies_it_at_once(window, page):
    select(page, "Vertical doble panel")
    set_combo(page.panes, "Off")
    page.apply_to(window.config)
    window._preferences_changed()
    assert window.dest is None


def test_only_renaming_the_style_in_use_does_not_touch_the_window(window, page, monkeypatch):
    window.run_command("Set DUAL=Off")  # un cambio a mano: reaplicar el estilo lo desharía
    select(page, "Vertical doble panel")
    page.name.setText("Otro nombre")
    page.name.textEdited.emit("Otro nombre")
    page.apply_to(window.config)
    window._preferences_changed()
    assert window.dest is None and window.config["style"] == "Otro nombre"


def test_the_page_is_part_of_the_preferences(window):
    dialog = PreferencesDialog(window.config)
    dialog.show_styles()
    assert dialog.pages.currentWidget() is dialog.styles_page
    dialog.styles_page.add()
    dialog.apply_to(window.config)
    assert any(style["name"] == "Estilo nuevo" for style in window.config["styles"])
    dialog.deleteLater()


def test_menu_opens_the_page(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "open_preferences", lambda **kwargs: calls.append(kwargs))
    actions = [action for menu in window.main_menu.findChildren(type(window.favorites_menu)) for action in menu.actions()]
    next(action for action in actions if action.text() == "Crear o cambiar estilos…").trigger()
    settle()
    assert calls == [{"styles_page": True}]


def test_saved_current_style_remembers_terminals_and_position(window, monkeypatch):
    use(window, "Vertical panel-terminal")
    monkeypatch.setattr(window, "_ask_text", lambda *_args: "Guardado")
    window._save_current_style()
    saved = window._style_named("Guardado")
    assert saved["terminals"] == ["first"] and saved["terminal_position"] == "right" and saved["dual"] == "Off"
    use(window, "Vertical doble panel")
    assert window.terminal_panels[0].isHidden()
    use(window, "Guardado")
    assert window.terminal_panels[0].isVisible() and window.pane_areas[0].orientation() == Qt.Orientation.Horizontal


def test_a_style_with_nonsense_values_does_not_break_anything(window):
    window.config["styles"].append({"name": "Raro", "terminals": ["nada", 3], "terminal_position": 7, "view": "list"})
    use(window, "Raro")
    assert all(panel.isHidden() for panel in window.terminal_panels)


def test_styles_page_copes_with_a_damaged_list(qapp_offscreen):
    page = StylesPage({"styles": [3, {"sin": "nombre"}, {"name": "Bueno"}]})
    assert page.names.count() == 1


def test_an_imported_file_keeps_the_terminals_of_its_styles(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"styles": [{"name": "Mío", "terminals": ["general"]}]}}), encoding="utf-8")
    assert settings_io.read_settings(str(path))[0]["styles"][0]["terminals"] == ["general"]


# ------------------------------------------------------------------ qué estilos se ven en la barra y en qué orden
def bar_names(window) -> list[str]:
    """Los botones de la barra de estilos, de izquierda a derecha."""
    return [action.text() for action in window.style_bar.actions() if action.text()]


def row_of(page, name: str) -> int:
    return next(row for row in range(page.names.count()) if page.names.item(row).text() == name)


def accept(window, page) -> None:
    page.apply_to(window.config)
    window._preferences_changed()
    settle()


def test_every_style_starts_checked_in_its_order(window, page):
    names = [style["name"] for style in window.config["styles"]]
    assert [page.names.item(row).text() for row in range(page.names.count())] == names
    assert all(page.names.item(row).checkState() == Qt.CheckState.Checked for row in range(page.names.count()))


def test_unchecking_hides_it_from_the_bar_but_it_still_works(window, page):
    page.names.item(row_of(page, "Comandante")).setCheckState(Qt.CheckState.Unchecked)
    accept(window, page)
    assert "Comandante" not in bar_names(window) and "Explorador" in bar_names(window)
    assert window.config["bar_buttons"]["styles"] == ["Comandante"]
    use(window, "Comandante")
    assert window.config["style"] == "Comandante" and window.tree_dock.isHidden()


def test_checking_it_again_brings_it_back(window, page):
    page.names.item(row_of(page, "Comandante")).setCheckState(Qt.CheckState.Unchecked)
    accept(window, page)
    page = StylesPage(window.config)
    item = page.names.item(row_of(page, "Comandante"))
    assert item.checkState() == Qt.CheckState.Unchecked
    item.setCheckState(Qt.CheckState.Checked)
    accept(window, page)
    assert "Comandante" in bar_names(window) and "styles" not in window.config["bar_buttons"]


def test_moving_changes_the_order_of_the_bar(window, page):
    select(page, "Único")
    for _step in range(page.names.count()):
        page.move_current(-1)
    assert page.names.currentItem().text() == "Único" and page.names.currentRow() == 0
    select(page, "Vertical doble panel")
    page.move_current(1)
    accept(window, page)
    names = bar_names(window)
    assert names[0] == "Único" and names.index("Vertical doble árbol") < names.index("Vertical doble panel")
    assert names[-1] == "Nuevo estilo…"


def test_moving_past_the_ends_does_nothing(window, page):
    page.names.setCurrentRow(0)
    first = page.names.item(0).text()
    page.move_current(-1)
    assert page.names.item(0).text() == first and page.names.currentRow() == 0


def test_dragged_rows_keep_their_own_style(window, page):
    """Al soltar, la lista reordena sus filas: cada una debe seguir siendo su estilo."""
    item = page.names.takeItem(row_of(page, "Imágenes"))
    page.names.insertItem(0, item)
    page.names.setCurrentRow(0)
    assert page.name.text() == "Imágenes" and page.view.currentData() == "thumbnails"
    page.meta.setChecked(True)
    accept(window, page)
    assert window.config["styles"][0]["name"] == "Imágenes" and window.config["styles"][0]["meta"] is True
    assert stored(window, "Comandante")["meta"] is False


def test_order_and_hidden_of_the_previous_page_are_respected(window):
    names = [style["name"] for style in window.config["styles"]]
    window.config["bar_order"] = {"styles": ["Único", "Comandante", *(name for name in names if name not in ("Único", "Comandante"))], "pane": ["home"]}
    window.config["bar_buttons"] = {"styles": ["Explorador"], "pane": ["home"]}
    page = StylesPage(window.config)
    assert [page.names.item(row).text() for row in range(2)] == ["Único", "Comandante"]
    assert page.names.item(row_of(page, "Explorador")).checkState() == Qt.CheckState.Unchecked
    page.apply_to(window.config)
    assert [style["name"] for style in window.config["styles"]][:2] == ["Único", "Comandante"]
    assert window.config["bar_order"] == {"pane": ["home"]} and window.config["bar_buttons"] == {"styles": ["Explorador"], "pane": ["home"]}


def test_a_new_style_is_visible_and_goes_last(window, page):
    page.add()
    accept(window, page)
    assert bar_names(window)[-2:] == ["Estilo nuevo", "Nuevo estilo…"]


def test_hiding_every_style_leaves_only_the_button(window, page):
    for row in range(page.names.count()):
        page.names.item(row).setCheckState(Qt.CheckState.Unchecked)
    accept(window, page)
    assert bar_names(window) == ["Nuevo estilo…"]


def test_hiding_the_style_in_use_keeps_the_window_as_it_is(window, page):
    window.run_command("Set DUAL=Off")
    page.names.item(row_of(page, "Vertical doble panel")).setCheckState(Qt.CheckState.Unchecked)
    accept(window, page)
    assert window.dest is None and window.config["style"] == "Vertical doble panel"


def test_the_bars_page_no_longer_lists_the_styles_bar(window):
    dialog = PreferencesDialog(window.config)
    assert "styles" not in dialog.bar_buttons.lists
    titles = [dialog.bar_buttons.bar_list.item(row).text() for row in range(dialog.bar_buttons.bar_list.count())]
    assert "Barra de estilos" not in titles and "Barra de herramientas" in titles
    dialog.deleteLater()


def test_accepting_preferences_keeps_the_choices_of_every_bar(window):
    window.config["bar_buttons"] = {"styles": ["Comandante"], "pane": ["home"]}
    dialog = PreferencesDialog(window.config)
    dialog.apply_to(window.config)
    dialog.deleteLater()
    assert window.config["bar_buttons"] == {"styles": ["Comandante"], "pane": ["home"]}


def test_customize_from_the_styles_bar_opens_the_styles_page(window, monkeypatch):
    seen = []
    monkeypatch.setattr(PreferencesDialog, "exec", lambda dialog: seen.append(dialog.pages.currentWidget() is dialog.styles_page) or 0)
    window.open_preferences("styles")
    assert seen == [True]


# ------------------------------------------------------------------ el botón «Nuevo estilo…»
def test_new_style_button_goes_right_after_the_last_style_shown(window):
    actions = [action.text() for action in window.style_bar.actions()]
    assert actions[-1] == "Nuevo estilo…" and actions[-2] == "Único"
    assert "Nuevo estilo…" not in window._style_buttons
    assert not window.new_style_action.isCheckable()
    assert window.style_bar.widgetForAction(window.new_style_action).objectName() == "styleNew"


def test_new_style_button_shows_its_dots(window):
    assert window.style_bar.widgetForAction(window.new_style_action).text() == "Nuevo estilo…"


def test_when_it_does_not_fit_the_button_can_still_be_reached(window):
    from PySide6.QtWidgets import QToolButton
    window.resize(700, 800)
    settle()
    assert window.style_bar.findChild(QToolButton, "qt_toolbar_ext_button").isVisible()
    assert window.new_style_action in window.style_bar.actions()


def test_new_style_button_follows_the_hidden_styles(window):
    window.config["bar_buttons"] = {"styles": ["Único"]}
    window._build_style_bar()
    assert [action.text() for action in window.style_bar.actions()][-2:] == ["Tira de Fotogramas", "Nuevo estilo…"]


def test_new_style_button_opens_the_creator_with_a_style_started(window, monkeypatch):
    seen = []

    def fake_exec(dialog):
        page = dialog.styles_page
        seen.append((dialog.pages.currentWidget() is page, page.names.currentItem().text(), page.name.text(), page.name.selectedText()))
        return 0

    monkeypatch.setattr(PreferencesDialog, "exec", fake_exec)
    before = json.dumps(window.config["styles"])
    window.new_style_action.trigger()
    settle()
    assert seen == [(True, "Estilo nuevo", "Estilo nuevo", "Estilo nuevo")]
    assert json.dumps(window.config["styles"]) == before  # al cancelar no queda nada


def test_new_style_button_keeps_the_style_when_accepted(window, monkeypatch):
    def fake_exec(dialog):
        page = dialog.styles_page
        page.name.setText("Hecho con el botón")
        page.name.textEdited.emit("Hecho con el botón")
        page.terminal_first.setChecked(True)
        return 1

    monkeypatch.setattr(PreferencesDialog, "exec", fake_exec)
    window.new_style_action.trigger()
    settle()
    assert bar_names(window)[-2:] == ["Hecho con el botón", "Nuevo estilo…"]
    assert stored(window, "Hecho con el botón")["terminals"] == ["first"]
    assert window.config["style"] == "Vertical doble panel"  # crearlo no lo aplica: se elige en la barra


def test_style_bar_is_rebuilt_without_losing_the_button(window):
    window.run_command("Set DARKMODE=Toggle")
    window._build_style_bar()
    assert sum(action.text() == "Nuevo estilo…" for action in window.style_bar.actions()) == 1


# ------------------------------------------------------------------ el botón » de las barras
def test_overflow_button_is_wide_and_has_its_own_icon(window):
    from PySide6.QtWidgets import QToolButton
    window.resize(700, 800)
    settle()
    for bar in (window.style_bar, window.toolbar, window.location_bar):
        overflow = bar.findChild(QToolButton, "qt_toolbar_ext_button")
        assert not overflow.icon().isNull() and overflow.iconSize().width() == 16
    overflow = window.style_bar.findChild(QToolButton, "qt_toolbar_ext_button")
    assert overflow.isVisible() and overflow.width() >= 24 and overflow.toolTip() == "Mostrar lo que no cabe en la barra"


def test_overflow_button_keeps_its_icon_when_the_theme_or_the_orientation_changes(window):
    from PySide6.QtWidgets import QToolButton
    def overflow():
        return window.toolbar.findChild(QToolButton, "qt_toolbar_ext_button")

    before = overflow().icon().cacheKey()
    window.run_command("Set DARKMODE=Toggle")
    assert overflow().icon().cacheKey() != before and not overflow().icon().isNull()
    before = overflow().icon().cacheKey()
    window.addToolBar(Qt.ToolBarArea.LeftToolBarArea, window.toolbar)
    settle()
    assert overflow().iconSize().width() == 16 and not overflow().icon().isNull()
    assert overflow().toolTip() == "Mostrar lo que no cabe en la barra"


def test_new_style_button_looks_different_from_the_styles(window):
    from directory_commander_tux_tux.ui import theme
    button = window.style_bar.widgetForAction(window.new_style_action)
    style = window.style_bar.widgetForAction(window._style_buttons["Comandante"])
    assert button.objectName() != style.objectName() and not button.icon().isNull() and style.icon().isNull()
    assert "QToolButton#styleNew" in theme.stylesheet(theme.current(), True)
    assert not button.isCheckable() and button.text() == "Nuevo estilo…"


# ------------------------------------------------------------------ hasta cuatro paneles
def add_style(window, name: str, dual: str, **extra) -> None:
    window.config["styles"].append({"name": name, "dual": dual, "tree": False, "viewer": False, "meta": False, "view": "list", **extra})


def apply_panes(window, dual: str, **extra) -> None:
    add_style(window, dual, dual, **extra)
    use(window, dual)


def assert_no_overlap(window, count: int) -> None:
    rects = [window.groups[side].rect().translated(window.groups[side].mapTo(window, QPoint(0, 0))) for side in range(count)]
    for first in range(count):
        for second in range(first + 1, count):
            assert not rects[first].intersects(rects[second])


def test_pane_modes_are_consistent():
    assert styles.pane_count("Off") == 1 and styles.pane_count("Vertical") == 2 and styles.pane_count("3-Left") == 3 and styles.pane_count("4-Grid") == 4
    assert styles.pane_count("no existe") == 2
    assert max(styles.pane_count(mode) for mode in styles.PANE_MODES) == styles.MAX_PANES
    assert styles.stacked("Horizontal") and styles.stacked("4-Horizontal") and not styles.stacked("4-Grid") and not styles.stacked("3-Top") and not styles.stacked("Off")


@pytest.mark.parametrize("mode, side, target, icon", [("Vertical", 0, 1, "sendright"), ("Vertical", 1, 0, "sendleft"), ("Horizontal", 0, 1, "senddown"), ("Horizontal", 1, 0, "sendup"),
                                                      ("4-Grid", 0, 3, "sendright"), ("4-Grid", 3, 1, "sendup"), ("3-Left", 1, 2, "senddown"), ("3-Left", 2, 0, "sendleft"), ("Off", 3, 0, "sendright")])
def test_send_arrow_points_to_the_other_pane(mode, side, target, icon):
    assert styles.send_direction(mode, side, target) == icon


def test_a_normal_window_only_creates_tabs_for_its_two_panes(window):
    assert [group.count() for group in window.groups] == [1, 1, 0, 0]
    assert len(window.all_views()) == 2 and window.sides[2].isHidden() and window.sides[3].isHidden()


@pytest.mark.parametrize("mode", ["3-Vertical", "3-Horizontal", "3-Left", "3-Top", "4-Vertical", "4-Horizontal", "4-Grid"])
def test_every_arrangement_shows_its_panes_without_overlapping(window, mode):
    apply_panes(window, mode)
    count = styles.pane_count(mode)
    assert all(window.groups[side].isVisible() and window.groups[side].current is not None for side in range(count))
    assert all(window.groups[side].width() > 150 and window.groups[side].height() > 150 for side in range(count))
    assert all(not window.groups[side].isVisible() for side in range(count, styles.MAX_PANES))
    assert_no_overlap(window, count)
    assert window.dual == mode


def test_grid_has_two_rows_of_two_of_the_same_size(window):
    apply_panes(window, "4-Grid")
    a, b, c, d = window.groups
    assert left_of(b, window) > left_of(a, window) and top_of(b, window) == top_of(a, window)
    assert top_of(c, window) > top_of(a, window) and left_of(c, window) == left_of(a, window)
    assert left_of(d, window) == left_of(b, window) and top_of(d, window) == top_of(c, window)
    assert abs(a.width() - b.width()) < 10 and abs(a.height() - c.height()) < 10


def test_one_left_and_two_right(window):
    apply_panes(window, "3-Left")
    a, b, c = window.groups[:3]
    assert left_of(b, window) > left_of(a, window) and left_of(c, window) == left_of(b, window)
    assert top_of(c, window) > top_of(b, window) and a.height() > b.height()


def test_two_on_top_and_one_below(window):
    apply_panes(window, "3-Top")
    a, b, c = window.groups[:3]
    assert top_of(a, window) == top_of(b, window) and top_of(c, window) > top_of(a, window) and c.width() > a.width()


def test_returning_to_two_panes_keeps_everything_working(window, folders):
    apply_panes(window, "4-Grid")
    use(window, "Vertical doble panel")
    assert window.dual == "Vertical" and window.dest is not None and window.sides[2].isHidden() and window.sides[3].isHidden()
    assert window.sides[0].parent() is window.splitter and window.sides[1].parent() is window.splitter
    assert window.splitter.orientation() == Qt.Orientation.Horizontal and left_of(window.groups[1], window) > left_of(window.groups[0], window)
    use(window, "Horizontal doble panel")
    assert top_of(window.groups[1], window) > top_of(window.groups[0], window)


def test_active_pane_outside_a_smaller_arrangement_moves_to_the_first(window):
    apply_panes(window, "4-Grid")
    view = window.groups[3].current
    view.activated.emit(view)
    assert window.active_side == 3
    use(window, "Vertical doble panel")
    assert window.active_side == 0 and window.source is window.groups[0].current


def test_destination_is_the_pane_that_was_active_before(window):
    apply_panes(window, "4-Grid")
    assert window.active_side == 0 and window.dest is window.groups[1].current
    window.groups[2].current.activated.emit(window.groups[2].current)
    assert window.source is window.groups[2].current and window.dest is window.groups[0].current
    window.groups[3].current.activated.emit(window.groups[3].current)
    assert window.source is window.groups[3].current and window.dest is window.groups[2].current


def test_roles_of_four_panes(window):
    apply_panes(window, "4-Grid")
    window.groups[2].current.activated.emit(window.groups[2].current)
    assert [group.current.property("role") for group in window.groups] == ["dest", "other", "source", "other"]


def test_two_panes_keep_their_roles(window):
    assert [group.current.property("role") for group in window.groups[:2]] == ["source", "dest"]
    window.groups[1].current.activated.emit(window.groups[1].current)
    assert [group.current.property("role") for group in window.groups[:2]] == ["dest", "source"]


def test_copying_goes_to_the_destination_pane(window, tmp_path, folders):
    apply_panes(window, "3-Vertical")
    third = tmp_path / "tercero"
    third.mkdir()
    window.groups[2].current.navigate(str(third))
    window.groups[2].current.activated.emit(window.groups[2].current)
    window.groups[0].current.activated.emit(window.groups[0].current)
    assert os.path.samefile(window.dest.path, third)
    window.run_command("Go DEST")
    settle()
    assert os.path.samefile(window.groups[2].current.path, window.groups[0].current.path)


def test_new_panes_start_in_the_folder_of_the_first(window, folders):
    apply_panes(window, "3-Vertical")
    assert os.path.samefile(window.groups[2].current.path, folders[0])
    assert window.active_side == 0  # abrirle la pestaña no lo vuelve activo


def test_each_pane_has_its_terminal_and_they_follow_it(window, sessions, folders, tmp_path):
    apply_panes(window, "4-Grid", terminals=["first", "second", "third", "fourth"])
    settle()
    assert len(sessions) == 4 and all(window.terminal_panels[side].isVisible() for side in range(4))
    terminal = window.terminals[3]
    terminal.screen_.feed_text("PS C:\\> " if terminal._family == "powershell" else "$ ")
    terminal._last_activity = 0
    other = tmp_path / "otra"
    other.mkdir()
    window.groups[3].current.navigate(str(other))
    for _attempt in range(40):
        QTest.qWait(25)
        if sessions[3].sent:
            break
    assert b"otra" in sessions[3].sent and sessions[0].sent == b""


def test_terminal_button_of_the_third_pane_opens_its_own(window, sessions):
    apply_panes(window, "3-Top")
    view = window.groups[2].current
    next(button for button, icon in view._tool_buttons if icon == "terminalpane").click()
    settle()
    assert window.active_side == 2 and window.terminal_panels[2].isVisible() and window.terminal_panels[0].isHidden()


def test_terminals_of_panes_that_do_not_exist_in_the_style_are_ignored(window, sessions):
    apply_panes(window, "Vertical", terminals=["first", "fourth"])
    assert window.terminal_panels[0].isVisible() and window.terminal_panels[3].isHidden() and len(sessions) == 1


def test_general_terminal_spans_the_four_panes(window):
    apply_panes(window, "4-Grid", terminals=["general"])
    panel = window.terminal_panels[GENERAL_TERMINAL]
    assert top_of(panel, window) >= bottom_of(window.groups[3], window)
    assert left_of(panel, window) <= left_of(window.groups[2], window) and right_of(panel, window) >= right_of(window.groups[3], window)


def test_double_tree_with_three_panes(window):
    add_style(window, "Árboles", "3-Vertical")
    window.config["styles"][-1].update(tree=True, double_tree=True)
    use(window, "Árboles")
    assert all(window.tree_panels[side].isVisible() for side in range(3)) and window.tree_panels[3].isHidden()
    use(window, "Vertical doble panel")
    assert all(panel.isHidden() for panel in window.tree_panels)


def test_last_tab_of_a_pane_does_not_break_the_arrangement(window, monkeypatch):
    apply_panes(window, "4-Grid")
    group = window.groups[1]
    group.settings = {**group.settings, "close_window_with_last_tab": True}
    group.close_tab(0)
    assert window.dual == "4-Grid" and group.current is not None and group.isVisible()


def test_second_pane_of_two_still_closes_with_its_last_tab(window):
    group = window.groups[1]
    group.settings = {**group.settings, "close_window_with_last_tab": True}
    group.close_tab(0)
    assert window.dual == "Off"


def test_command_accepts_the_new_arrangements(window):
    window.run_command("Set DUAL=4-grid")
    assert window.dual == "4-Grid"
    window.run_command("Set DUAL=Toggle")
    assert window.dual == "Off"
    with pytest.raises(ValueError):
        window._set_dual("5-Grid")


def test_a_style_with_an_unknown_arrangement_falls_back(window):
    apply_panes(window, "5-Grid")
    assert window.dual == "Vertical" and window.dest is not None


def test_session_reopens_four_panes_with_their_folders_and_sizes(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    third = tmp_path / "tercero"
    third.mkdir()
    first = open_window(folders)
    apply_panes(first, "4-Grid")
    first.groups[2].current.navigate(str(third))
    settle()
    first._pane_nests[0].setSizes([900, 300])
    settle()
    width = first.groups[0].width()
    close_window(first)
    second = open_window(folders)
    settle()
    assert second.dual == "4-Grid" and all(group.isVisible() for group in second.groups)
    assert os.path.samefile(second.groups[2].current.path, third)
    assert abs(second.groups[0].width() - width) < 40
    close_window(second)


def test_session_of_two_panes_from_the_previous_version_still_opens(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    config = Config()
    config["session"] = {"tabs": [[str(folders[0])], [str(folders[1])]], "dual": "Vertical"}
    main = MainWindow(config, [])
    main.resize(1500, 800)
    main.show()
    settle()
    assert [group.count() for group in main.groups] == [1, 1, 0, 0] and main.dual == "Vertical" and os.path.samefile(main.groups[1].current.path, folders[1])
    close_window(main)


def test_saved_layout_remembers_four_panes(window, tmp_path, folders):
    apply_panes(window, "4-Grid")
    window.groups[3].current.navigate(str(folders[1]))
    settle()
    window.save_layout("Mi diseño")
    use(window, "Vertical doble panel")
    window.load_layout("Mi diseño")
    settle()
    assert window.dual == "4-Grid" and os.path.samefile(window.groups[3].current.path, folders[1])


def test_saved_current_style_keeps_the_arrangement(window, monkeypatch):
    apply_panes(window, "3-Left", terminals=["third"])
    monkeypatch.setattr(window, "_ask_text", lambda *_args: "Guardado")
    window._save_current_style()
    saved = window._style_named("Guardado")
    assert saved["dual"] == "3-Left" and saved["terminals"] == ["third"]


def test_page_offers_every_arrangement_and_its_terminals(window, page):
    assert {page.panes.itemData(row) for row in range(page.panes.count())} == set(styles.PANE_MODES)
    page.add()
    set_combo(page.panes, "Off")
    assert [check.isEnabled() for check in page._pane_terminals] == [True, False, False, False]
    set_combo(page.panes, "3-Top")
    assert [check.isEnabled() for check in page._pane_terminals] == [True, True, True, False]
    set_combo(page.panes, "4-Grid")
    assert all(check.isEnabled() for check in page._pane_terminals)
    page.terminal_fourth.setChecked(True)
    assert page.position.isEnabled()
    page.apply_to(window.config)
    assert stored(window, "Estilo nuevo")["dual"] == "4-Grid" and stored(window, "Estilo nuevo")["terminals"] == ["fourth"]


def test_terminals_of_panes_removed_on_the_page_are_not_saved(window, page):
    page.add()
    set_combo(page.panes, "4-Grid")
    page.terminal_third.setChecked(True)
    page.terminal_fourth.setChecked(True)
    set_combo(page.panes, "Vertical")
    page.apply_to(window.config)
    assert stored(window, "Estilo nuevo")["terminals"] == []


def test_closing_a_window_with_four_panes_ends_every_terminal(qapp_offscreen, tmp_path, monkeypatch, folders, sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    main = open_window(folders)
    apply_panes(main, "4-Grid", terminals=["first", "second", "third", "fourth", "general"])
    settle()
    assert len(sessions) == 5
    close_window(main)
    assert all(session.closed for session in sessions)
