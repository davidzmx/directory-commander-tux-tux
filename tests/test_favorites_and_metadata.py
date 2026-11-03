"""Favoritos con ramas, recientes, favoritos inteligentes, avisos y panel de metadatos."""
import json
import os
import stat
import sys
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMenu

from directory_commander_tux_tux.core import exiftool, favorites, frequent, listing, settings_io
from directory_commander_tux_tux.core.config import METADATA_TOGGLE_COMMAND, Config
from directory_commander_tux_tux.core.listing import Entry
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.favorites_pages import (BAR, BRANCH, FILE, FOLDER, HEADER, KIND_ROLE, PATH_ROLE, SEPARATOR, SPACE, AddFavoriteDialog,
                                                       FavoritesPage)
from directory_commander_tux_tux.ui.main_window import MainWindow
from directory_commander_tux_tux.ui.preview import MetadataPanel
from directory_commander_tux_tux.ui.shell_menu import add_section, menu_text


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    (left / "sub").mkdir(parents=True)
    right.mkdir()
    main = MainWindow(Config(), [str(left), str(right)])
    main.set_dual("Vertical")
    main.resize(1500, 700)
    main.show()
    QApplication.processEvents()
    yield main
    main.close()
    main.deleteLater()
    QApplication.processEvents()


def texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def wait_until(condition, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def tree_rows(item, depth=0):
    rows = []
    for row in range(item.childCount()):
        child = item.child(row)
        rows.append((depth, child.data(0, KIND_ROLE), child.text(0)))
        rows += tree_rows(child, depth + 1)
    return rows


def find_item(root, text):
    for row in range(root.childCount()):
        child = root.child(row)
        if child.text(0) == text:
            return child
        found = find_item(child, text)
        if found is not None:
            return found
    return None


BAR_NODE = {"label": favorites.BAR_LABEL, "bar": True}


# ------------------------------------------------------------------ el árbol, sin interfaz
def test_clean_keeps_every_kind_and_puts_the_bar_first():
    nodes = [{"label": "A", "path": "/a"}, {"separator": True}, {"space": True}, {"header": "Título"}, {"label": "F", "path": "/f", "file": True},
             {"label": "Rama", "children": [{"label": "B", "path": "/b"}]}, {**BAR_NODE, "children": [{"label": "C", "path": "/c"}]}]
    cleaned, skipped = favorites.clean(nodes)
    assert skipped == 0
    assert cleaned[0]["bar"] is True and cleaned[0]["children"] == [{"label": "C", "path": "/c"}]
    assert cleaned[1:] == nodes[:-1]


def test_clean_drops_an_empty_bar_and_invalid_items():
    cleaned, skipped = favorites.clean([{**BAR_NODE, "children": []}, "basura", {"path": ""}, {"label": "x"}, {"label": "R", "children": [5, {"path": "/ok"}]}])
    assert cleaned == [{"label": "R", "children": [{"label": "/ok", "path": "/ok"}]}]
    assert skipped == 4


def test_old_mark_moves_into_the_bar_branch():
    cleaned, _ = favorites.clean([{"label": "A", "path": "/a", "bar": True}, {"label": "B", "path": "/b"}])
    assert favorites.bar_items(cleaned) == [{"label": "A", "path": "/a"}]
    assert favorites.other_items(cleaned) == [{"label": "B", "path": "/b"}]


def test_hostile_depth_does_not_exhaust_the_stack():
    node = {"label": "x", "path": "/x"}
    for _ in range(5000):
        node = {"label": "r", "children": [node]}
    cleaned, skipped = favorites.clean([node])
    assert skipped >= 1 and cleaned


def test_walk_and_contains_look_inside_branches():
    nodes = [{"label": "R", "children": [{"label": "A", "path": "/a"}, {"label": "S", "children": [{"label": "F", "path": "/f", "file": True}]}]}, {"separator": True}]
    assert [node["path"] for node in favorites.walk(nodes)] == ["/a", "/f"]
    assert favorites.contains(nodes, "/f") and not favorites.contains(nodes, "/z")


def test_with_bar_is_an_independent_copy():
    original = [{"label": "R", "children": []}]
    copy = favorites.with_bar(original)
    copy[1]["children"].append({"label": "A", "path": "/a"})
    assert copy[0]["bar"] is True and original == [{"label": "R", "children": []}]


def test_sort_uses_natural_order_branches_first_and_respects_separators():
    nodes = [{"label": "b10", "path": "/1"}, {"label": "b9", "path": "/2"}, {"label": "Rama", "children": []}, {"separator": True},
             {"label": "z", "path": "/3"}, {"label": "a", "path": "/4"}]
    favorites.sort_level(nodes)
    assert [node.get("label", "|") for node in nodes] == ["Rama", "b9", "b10", "|", "a", "z"]


def test_sort_keeps_the_bar_first():
    nodes = favorites.with_bar([{"label": "z", "path": "/z"}, {"label": "a", "path": "/a"}])
    favorites.sort_level(nodes)
    assert nodes[0]["bar"] is True and [node["label"] for node in nodes[1:]] == ["a", "z"]


# ------------------------------------------------------------------ conservar
def test_nested_favorites_survive_export_and_import(tmp_path):
    path = tmp_path / "f.json"
    nodes = [{**BAR_NODE, "children": [{"label": "A", "path": "/a"}]}, {"label": "R", "children": [{"label": "B", "path": "/b"}]}, {"separator": True}]
    settings_io.export_favorites(str(path), nodes)
    assert settings_io.read_favorites(str(path)) == (nodes, 0)


def test_import_merges_without_duplicates_inside_branches(window, tmp_path):
    path = tmp_path / "f.json"
    settings_io.export_favorites(str(path), [{"label": "R", "children": [{"label": "A", "path": "/a"}, {"label": "B", "path": "/b"}]}])
    window.config["favorites"] = [{"label": "A", "path": "/a"}]
    assert window.import_favorites_file(str(path)) == (1, 0)
    assert window.config["favorites"] == [{"label": "A", "path": "/a"}, {"label": "R", "children": [{"label": "B", "path": "/b"}]}]
    assert window.import_favorites_file(str(path)) == (0, 0)


def test_upgrade_from_a_visible_bar_keeps_favorites_on_it(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 4, "show_favorites_bar": True, "favorites": [{"label": "A", "path": "/a"}, "basura"],
                                                      "recent": [{"path": "/r", "count": 7}, {"path": "/s"}, "basura"]}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert favorites.bar_items(config["favorites"]) == [{"label": "A", "path": "/a"}]
    assert config["smart_points"] == {"/r": 21}


def test_upgrade_from_the_checkbox_version_moves_them_to_the_branch(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 5, "show_favorites_bar": True, "folder_visits": {"/v": 4},
                                                      "favorites": [{"label": "A", "path": "/a", "bar": True}, {"label": "B", "path": "/b"}]}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert favorites.bar_items(config["favorites"]) == [{"label": "A", "path": "/a"}]
    assert favorites.other_items(config["favorites"]) == [{"label": "B", "path": "/b"}]
    assert config["smart_points"] == {"/v": 12} and "folder_visits" not in config.data


def test_upgrade_with_a_hidden_bar_leaves_favorites_out_of_it(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 4, "favorites": [{"label": "A", "path": "/a"}]}), encoding="utf-8")
    assert Config(str(tmp_path))["favorites"] == [{"label": "A", "path": "/a"}]


def test_current_version_does_not_touch_favorites_or_points(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 6, "show_favorites_bar": True, "favorites": [{"label": "A", "path": "/a"}],
                                                      "recent": [{"path": "/r", "count": 7}], "smart_points": {"/z": 2}}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["favorites"] == [{"label": "A", "path": "/a"}] and config["smart_points"] == {"/z": 2}


def test_upgrade_puts_the_metadata_button_before_the_viewer_button(tmp_path):
    toolbar = [{"label": "Visor", "commands": ["Set VIEWPANE=Toggle"]}, {"label": "Otro", "commands": ["Go UP"]}]
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 5, "toolbar": toolbar}), encoding="utf-8")
    labels = [item["commands"][0] for item in Config(str(tmp_path))["toolbar"]]
    assert labels == [METADATA_TOGGLE_COMMAND, "Set VIEWPANE=Toggle", "Go UP", "Set GENERALTERMINAL=Toggle", "Set PANEFRAMES=Toggle", "Compare FILES"]


def test_settings_validate_recents_and_smart_favorites_on_import(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {
        "recent_list": {"enabled": "sí", "max": 99999999, "virtual": False}, "history_size": 0,
        "smart_favorites": {"max_shown": "x", "min_points": -5, "exclude": [{"pattern": "*\\temp"}, {"pattern": ""}, 3], "points": {"copy_to": 99, "delete": "x"}},
        "exiftool_dir": "C:\\malicioso"}}), encoding="utf-8")
    settings, ignored = settings_io.read_settings(str(path))
    assert settings["recent_list"] == {"enabled": True, "virtual": False, "skip_transit": True, "max": frequent.MAX_RECENT_LIMIT}
    assert settings["history_size"] == 1
    smart = settings["smart_favorites"]
    assert smart["max_shown"] == 10 and smart["min_points"] == 0 and smart["exclude"] == [{"pattern": "*\\temp", "subfolders": False}]
    assert smart["points"]["copy_to"] == 99 and smart["points"]["delete"] == 8
    assert "exiftool_dir" not in settings and "exiftool_dir" in ignored  # ejecutaría lo que hubiera en una carpeta ajena


def test_points_and_visits_do_not_travel_in_a_settings_file():
    assert "smart_points" not in settings_io.SETTINGS_TYPES and "recent" not in settings_io.SETTINGS_TYPES


# ------------------------------------------------------------------ añadir
def add_dialog(path="C:\\padre\\carpeta", nodes=None, **kwargs):
    return AddFavoriteDialog(path, nodes or [], None, **kwargs)


def test_add_dialog_shows_the_folder_and_selects_the_parent_part(qapp_offscreen):
    dialog = add_dialog(os.path.join("raiz", "padre", "Y"))
    assert dialog.folder.text() == os.path.join("raiz", "padre", "Y") and not dialog.folder.isEnabled()
    assert dialog.name.text() == os.path.join("raiz", "padre", "Y")
    assert dialog.name.selectedText() == os.path.join("raiz", "padre") + os.sep


def test_add_dialog_lists_only_branches_with_the_bar_first(qapp_offscreen):
    dialog = add_dialog(nodes=[{"label": "A", "path": "/a"}, {"label": "R", "children": [{"label": "S", "children": []}, {"label": "B", "path": "/b"}]}])
    root = dialog.root
    assert root.text(0) == "Favoritos" and [root.child(i).text(0) for i in range(root.childCount())] == ["Barra de favoritos", "R"]
    assert root.child(1).child(0).text(0) == "S" and root.child(1).childCount() == 1
    assert dialog.tree.currentItem() is root


def test_add_dialog_adds_to_the_chosen_branch(qapp_offscreen):
    dialog = add_dialog("/x", [{"label": "R", "children": []}])
    dialog.name.setText(" Mío ")
    dialog.tree.setCurrentItem(dialog.root.child(1))
    assert dialog.result_nodes() == [{"label": "R", "children": [{"label": "Mío", "path": "/x"}]}]


def test_add_dialog_adds_to_the_favorites_bar(qapp_offscreen):
    dialog = add_dialog("/x")
    dialog.name.setText("Mío")
    dialog.tree.setCurrentItem(dialog.root.child(0))
    assert favorites.bar_items(dialog.result_nodes()) == [{"label": "Mío", "path": "/x"}]


def test_add_dialog_creates_a_branch_inside_the_selected_one(qapp_offscreen, monkeypatch):
    dialog = add_dialog("/x", [{"label": "R", "children": []}])
    monkeypatch.setattr(dialog, "_ask_branch_name", lambda: "Nueva")
    dialog.tree.setCurrentItem(dialog.root.child(1))
    dialog.new_branch()
    assert dialog.tree.currentItem().text(0) == "Nueva"
    dialog.name.setText("Mío")
    assert dialog.result_nodes() == [{"label": "R", "children": [{"label": "Nueva", "children": [{"label": "Mío", "path": "/x"}]}]}]


def test_add_dialog_cancelled_branch_name_creates_nothing(qapp_offscreen, monkeypatch):
    dialog = add_dialog("/x")
    monkeypatch.setattr(dialog, "_ask_branch_name", lambda: "")
    dialog.new_branch()
    assert dialog.root.childCount() == 1


def test_add_dialog_needs_a_name_and_warns_of_duplicates(qapp_offscreen):
    dialog = add_dialog("/x", [{"label": "A", "path": "/x"}])
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok.text() == "Aceptar" and ok.isEnabled()
    dialog.name.setText("   ")
    assert not ok.isEnabled()
    assert any(label.text() == "Esta carpeta ya está en favoritos." for label in dialog.findChildren(QLabel))


def test_add_dialog_sorts_when_asked(qapp_offscreen):
    dialog = add_dialog("/x", [{"label": "z", "path": "/z"}], auto_sort=True)
    dialog.name.setText("a")
    assert [node["label"] for node in dialog.result_nodes()] == ["a", "z"]


def test_cancelling_the_dialog_changes_nothing(window, monkeypatch):
    monkeypatch.setattr(AddFavoriteDialog, "exec", lambda _dialog: 0)
    window.run_command("Favorites ADDDIALOG")
    assert window.config["favorites"] == []


def test_accepting_the_dialog_updates_tree_and_bar(window, monkeypatch):
    def accept(dialog):
        dialog.name.setText("Aquí")
        dialog.tree.setCurrentItem(dialog.root.child(0))
        return 1

    monkeypatch.setattr(AddFavoriteDialog, "exec", accept)
    window.run_command("Favorites ADDDIALOG")
    assert favorites.bar_items(window.config["favorites"])[0]["path"] == window.source.path
    assert [action.text() for action in window.favorites_bar.actions()] == ["Aquí"]
    assert window.tree.favorites_item.child(0).child(0).text(0) == "Aquí"


# ------------------------------------------------------------------ el fallo del panel inactivo
def test_add_from_a_pane_button_uses_that_pane_even_if_focus_returns_to_the_other(window, monkeypatch):
    """Lo que pasa de verdad: al cerrarse el menú, el foco vuelve al panel que lo tenía y este se activa."""
    seen = []
    monkeypatch.setattr(AddFavoriteDialog, "exec", lambda dialog: seen.append(dialog.path) or 0)
    active = window.source
    other = window.groups[1 - window.active_side].current
    menu = other.favorites_button.menu()
    menu.aboutToShow.emit()
    assert window.source is other
    window._view_activated(active)
    menu.actions()[0].trigger()
    assert seen == [other.path]


def test_opening_a_favorite_from_a_pane_button_uses_that_pane(window, tmp_path):
    active = window.source
    other = window.groups[1 - window.active_side].current
    target = os.path.normpath(str(tmp_path / "izquierda" / "sub"))
    window.config["favorites"] = [{"label": "Destino", "path": target}]
    menu = other.favorites_button.menu()
    menu.aboutToShow.emit()
    window._view_activated(active)
    next(action for action in menu.actions() if action.text() == "Destino").trigger()
    assert other.path == target and active.path != target and window.source is other


def test_pane_menu_gives_focus_to_its_pane(window):
    other = window.groups[1 - window.active_side].current
    window.activateWindow()
    other.favorites_button.menu().aboutToShow.emit()
    QApplication.processEvents()
    if window.isActiveWindow():  # sin ventana activa el sistema no reparte el foco
        assert QApplication.focusWidget() is other.current_view


def test_clicking_the_pane_bar_or_its_path_activates_the_pane(window):
    other = window.groups[1 - window.active_side].current
    QTest.mouseClick(other.pane_bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1, 1))
    assert window.source is other
    active = window.groups[1 - window.active_side].current
    active.breadcrumb.pathEntered.emit(active.path)
    assert window.source is active


# ------------------------------------------------------------------ el menú
def test_menu_shows_branches_separators_and_smart_folders(window, tmp_path):
    frequent_folder = tmp_path / "frecuente"
    frequent_folder.mkdir()
    window.config["favorites"] = [{**BAR_NODE, "children": [{"label": "Fija", "path": str(tmp_path)}]},
                                  {"label": "Normal", "path": str(tmp_path)}, {"separator": True}, {"header": "Título"},
                                  {"label": "Rama", "children": [{"label": "Dentro", "path": str(tmp_path)}]}, {"label": "Vacía", "children": []}]
    window.config["smart_points"] = {str(frequent_folder): 50}
    menu = QMenu()
    window._populate_favorites(menu)
    assert texts(menu) == ["Añadir a Favoritos\tCtrl+D", "Editar Favoritos", "Favoritos", "Barra de favoritos", "Normal", "Título", "Rama", "Vacía",
                           "Favoritos inteligentes", str(frequent_folder)]
    submenus = {action.text(): action.menu() for action in menu.actions() if action.menu()}
    assert texts(submenus["Barra de favoritos"]) == ["Fija"] and texts(submenus["Rama"]) == ["Dentro"]
    assert texts(submenus["Vacía"]) == ["(vacío)"]


def test_empty_menu_keeps_the_bar_branch_and_hides_the_smart_section(window):
    window.config["smart_points"] = {}
    menu = QMenu()
    window._populate_favorites(menu)
    assert texts(menu) == ["Añadir a Favoritos\tCtrl+D", "Editar Favoritos", "Favoritos", "Barra de favoritos"]


def test_missing_favorite_is_disabled_and_missing_frequent_folder_hidden(window, tmp_path):
    window.config["favorites"] = [{"label": "Borrada", "path": str(tmp_path / "no-existe")}]
    window.config["smart_points"] = {str(tmp_path / "tampoco"): 99}
    menu = QMenu()
    window._populate_favorites(menu)
    assert not next(action for action in menu.actions() if action.text() == "Borrada").isEnabled()
    assert "Favoritos inteligentes" not in texts(menu)


def test_file_favorite_opens_the_file(window, tmp_path, monkeypatch):
    sample = tmp_path / "nota.txt"
    sample.write_text("x", encoding="utf-8")
    opened = []
    monkeypatch.setattr(window, "_open_file", lambda _view, entry: opened.append(entry.path))
    window.config["favorites"] = [{"label": "Nota", "path": str(sample), "file": True}]
    menu = QMenu()
    window._populate_favorites(menu)
    next(action for action in menu.actions() if action.text() == "Nota").trigger()
    assert opened == [os.path.normpath(str(sample))]


def test_ampersand_in_a_name_is_shown_literally():
    assert menu_text("R&B") == "R&&B"


def test_computer_view_can_be_a_favorite(window):
    window.source.navigate(listing.COMPUTER_PATH)
    window.run_command("Favorites ADD")
    assert window.config["favorites"] == [{"label": listing.COMPUTER_LABEL, "path": listing.COMPUTER_PATH}]
    menu = QMenu()
    window._populate_favorites(menu)
    assert next(action for action in menu.actions() if action.text() == listing.COMPUTER_LABEL).isEnabled()


def test_direct_add_twice_says_it_is_already_there(window):
    window.run_command("Favorites ADD")
    window.run_command("Favorites ADD")
    assert len(window.config["favorites"]) == 1 and "Ya está" in window.statusBar().currentMessage()


def test_shortcut_opens_the_dialog_and_duplicate_moved(window, monkeypatch):
    seen = []
    monkeypatch.setattr(AddFavoriteDialog, "exec", lambda dialog: seen.append(dialog.path) or 0)
    actions = [action for action in window.actions() if action.shortcut().toString() == "Ctrl+D"]
    assert len(actions) == 1
    actions[0].trigger()
    assert seen == [window.source.path]
    every = [action for menu in window.findChildren(QMenu) for action in menu.actions()]
    assert not [action for action in every if action.shortcut().toString() == "Ctrl+D"]
    assert any(action.shortcut().toString() == "Ctrl+Alt+D" for action in every)


def test_clicking_the_button_opens_the_menu_under_it(window):
    button = window.source.favorites_button
    assert button.isVisible()
    menu = button.menu()
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert menu.isVisible() and len(menu.actions()) >= 4
    assert menu.geometry().top() >= button.mapToGlobal(QPoint(0, button.height())).y() - 1
    menu.close()
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)


def test_pane_menu_refills_without_duplicating(window):
    menu = window.source.favorites_button.menu()
    menu.aboutToShow.emit()
    first = texts(menu)
    menu.aboutToShow.emit()
    assert texts(menu) == first


def test_section_title_is_a_styled_label(qapp_offscreen):
    menu = QMenu()
    add_section(menu, "Título")
    action = menu.actions()[0]
    assert action.defaultWidget().objectName() == "menuSection" and action.defaultWidget().text() == "Título" and not action.isEnabled()


def test_menu_bar_keeps_import_and_export(window):
    window._fill_favorites()
    assert {"Importar favoritos…", "Exportar favoritos…", "Añadir a Favoritos\tCtrl+D"} <= set(texts(window.favorites_menu))


def test_edit_favorites_opens_that_preferences_page(window, monkeypatch):
    seen = []
    monkeypatch.setattr(PreferencesDialog, "exec", lambda dialog: seen.append(dialog.pages.currentWidget() is dialog.favorites_page) or 0)
    window.run_command("Favorites EDIT")
    assert seen == [True]


# ------------------------------------------------------------------ la barra de favoritos
def test_favorites_bar_shows_its_branch_and_a_menu_for_the_rest(window, tmp_path):
    window.config["favorites"] = [{**BAR_NODE, "children": [{"label": "B", "path": str(tmp_path)}, {"label": "Sub", "children": [{"label": "C", "path": str(tmp_path)}]}]},
                                  {"label": "A", "path": str(tmp_path)}]
    window._rebuild_favorites_bar()
    labels = [action.text() for action in window.favorites_bar.actions() if action.text()]
    assert labels == ["B", "Sub", "Otros Favoritos"]
    others = next(action for action in window.favorites_bar.actions() if action.text() == "Otros Favoritos").menu()
    others.aboutToShow.emit()
    assert texts(others) == ["A"]
    assert window.tree.favorites_item.childCount() == 2  # el árbol los muestra todos


def test_empty_favorites_bar_explains_itself_and_has_no_other_menu(window):
    window.config["favorites"] = []
    window._rebuild_favorites_bar()
    actions = window.favorites_bar.actions()
    assert len(actions) == 1 and not actions[0].isEnabled()


def test_tree_shows_branches_without_paths(window, tmp_path):
    window.config["favorites"] = [{"label": "Rama", "children": [{"label": "A", "path": str(tmp_path)}]}, {"separator": True}, {"label": "F", "path": str(tmp_path), "file": True}]
    window._rebuild_favorites_bar()
    item = window.tree.favorites_item
    assert item.childCount() == 1 and item.child(0).text(0) == "Rama" and item.child(0).data(0, Qt.ItemDataRole.UserRole) == ""
    assert item.child(0).child(0).text(0) == "A"


# ------------------------------------------------------------------ la página de favoritos
def page_with(nodes=None, **config):
    return FavoritesPage({"favorites": nodes or [], **config})


def test_page_shows_the_tree_with_the_fixed_bar(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "R", "children": [{"label": "B", "path": "/b"}]}, {"separator": True}, {"space": True}, {"header": "T"}])
    kinds = [(depth, kind) for depth, kind, _text in tree_rows(page.root)]
    assert kinds == [(0, BAR), (0, FOLDER), (0, BRANCH), (1, FOLDER), (0, SEPARATOR), (0, SPACE), (0, HEADER)]
    assert page.root.text(0) == "Favoritos" and page.root.child(0).text(0) == "Barra de favoritos"


def test_page_returns_what_it_received(qapp_offscreen):
    nodes = [{**BAR_NODE, "children": [{"label": "C", "path": "/c"}]}, {"label": "A", "path": "/a"}, {"label": "F", "path": "/f", "file": True},
             {"label": "R", "children": [{"label": "B", "path": "/b"}]}, {"separator": True}, {"space": True}, {"header": "T"}]
    assert page_with(nodes).values() == nodes


def test_page_new_folder_goes_behind_the_selection_and_inside_a_branch(qapp_offscreen, monkeypatch, tmp_path):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "Z", "path": "/z"}, {"label": "R", "children": []}])
    monkeypatch.setattr(page, "_ask_folder", lambda: str(tmp_path))
    page.tree.setCurrentItem(find_item(page.root, "A"))
    page.new_folder()
    assert [node.get("label") for node in page.values()] == ["A", tmp_path.name, "Z", "R"]
    assert page.tree.currentItem().data(0, PATH_ROLE) == os.path.normpath(str(tmp_path))
    page.tree.setCurrentItem(find_item(page.root, "R"))
    page.new_folder()
    assert page.values()[3]["children"] == [{"label": tmp_path.name, "path": os.path.normpath(str(tmp_path))}]


def test_page_new_file_branch_and_header(qapp_offscreen, monkeypatch, tmp_path):
    sample = tmp_path / "nota.txt"
    sample.write_text("x", encoding="utf-8")
    page = page_with()
    monkeypatch.setattr(page, "_ask_file", lambda: str(sample))
    monkeypatch.setattr(page, "_ask", lambda _title, _prompt, text="": "Texto")
    page.new_file()
    page.new_branch()
    page.tree.setCurrentItem(page.root)
    page.new_header()
    values = page.values()
    assert values[0] == {"label": "nota.txt", "path": os.path.normpath(str(sample)), "file": True}
    assert values[1] == {"label": "Texto", "children": []} and values[2] == {"header": "Texto"}


def test_page_cancelled_questions_add_nothing(qapp_offscreen, monkeypatch):
    page = page_with()
    monkeypatch.setattr(page, "_ask_folder", lambda: "")
    monkeypatch.setattr(page, "_ask_file", lambda: "")
    monkeypatch.setattr(page, "_ask", lambda *_a, **_k: "")
    page.new_folder()
    page.new_file()
    page.new_branch()
    page.new_header()
    assert page.values() == []


def test_page_moves_within_its_level_but_never_above_the_bar(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "B", "path": "/b"}, {"label": "C", "path": "/c"}])
    page.tree.setCurrentItem(find_item(page.root, "A"))
    page.move_current(-1)
    assert [node["label"] for node in page.values()] == ["A", "B", "C"]
    page.move_current(1)
    assert [node["label"] for node in page.values()] == ["B", "A", "C"]
    page.tree.setCurrentItem(find_item(page.root, "C"))
    page.move_to_edge(True)
    assert [node["label"] for node in page.values()] == ["C", "B", "A"]
    page.move_to_edge(False)
    assert [node["label"] for node in page.values()] == ["B", "A", "C"]
    assert page.root.child(0).data(0, KIND_ROLE) == BAR


def test_page_fixed_items_cannot_be_changed(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}])
    for item in (page.root, page.root.child(0)):
        page.tree.setCurrentItem(item)
        assert not page.remove_button.isEnabled() and not page.edit_button.isEnabled() and not page.up_button.isEnabled()
        page.remove_current()
        page.move_current(1)
    assert page.root.child(0).data(0, KIND_ROLE) == BAR and page.root.childCount() == 2


def test_page_removes_and_renames(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "B", "path": "/b"}])
    page.tree.setCurrentItem(find_item(page.root, "A"))
    page.remove_current()
    item = find_item(page.root, "B")
    item.setText(0, "Otro")
    assert page.values() == [{"label": "Otro", "path": "/b"}]


def test_page_path_field_follows_and_edits_the_selection(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "R", "children": []}])
    page.tree.setCurrentItem(find_item(page.root, "A"))
    assert page.path_field.text() == "/a" and page.path_field.isEnabled()
    page.path_field.setText("/nuevo")
    page._path_edited("/nuevo")
    assert page.values()[0]["path"] == "/nuevo"
    page.tree.setCurrentItem(find_item(page.root, "R"))
    assert page.path_field.text() == "" and not page.path_field.isEnabled()


def test_page_sorts_the_selected_branch(qapp_offscreen):
    page = page_with([{"label": "Z", "path": "/z"}, {"label": "R", "children": [{"label": "b", "path": "/b"}, {"label": "a", "path": "/a"}]}])
    page.tree.setCurrentItem(find_item(page.root, "R"))
    page.sort_current()
    assert [node["label"] for node in page.values()[1]["children"]] == ["a", "b"]
    assert [node["label"] for node in page.values()] == ["Z", "R"]
    page.tree.setCurrentItem(page.root)
    page.sort_current()
    assert [node["label"] for node in page.values()] == ["R", "Z"]


def test_page_auto_sort_applies_to_new_favorites(qapp_offscreen, monkeypatch, tmp_path):
    (tmp_path / "aaa").mkdir()
    page = page_with([{"label": "zzz", "path": "/z"}], favorites_autosort=True)
    assert page.auto_sort.isChecked()
    monkeypatch.setattr(page, "_ask_folder", lambda: str(tmp_path / "aaa"))
    page.new_folder()
    assert [node["label"] for node in page.values()] == ["aaa", "zzz"]
    assert page.tree.currentItem().text(0) == "aaa"


def test_page_drop_flags_only_allow_containers(qapp_offscreen):
    page = page_with([{"label": "A", "path": "/a"}, {"label": "R", "children": []}])
    assert page.tree.dragDropMode() == page.tree.DragDropMode.InternalMove
    assert not page.tree.invisibleRootItem().flags() & Qt.ItemFlag.ItemIsDropEnabled
    assert page.root.flags() & Qt.ItemFlag.ItemIsDropEnabled and not page.root.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert find_item(page.root, "R").flags() & Qt.ItemFlag.ItemIsDropEnabled
    assert not find_item(page.root, "A").flags() & Qt.ItemFlag.ItemIsDropEnabled and find_item(page.root, "A").flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert not page.root.child(0).flags() & Qt.ItemFlag.ItemIsDragEnabled


def test_preferences_save_favorites_and_settings_only_when_applied(qapp_offscreen):
    config = {"favorites": [{"label": "A", "path": "/a"}], "smart_points": {"/x": 5, "/y": 9}}
    dialog = PreferencesDialog(config)
    dialog.favorites_page.tree.setCurrentItem(find_item(dialog.favorites_page.root, "A"))
    dialog.favorites_page.remove_current()
    dialog.favorites_page.auto_sort.setChecked(True)
    dialog.recent_page.maximum.setValue(25)
    dialog.recent_page.history.setValue(30)
    dialog.smart_page.min_points.setValue(7)
    dialog.points_page.spins["copy_to"].setValue(55)
    dialog.smart_page.clear_button.click()
    assert config["favorites"] == [{"label": "A", "path": "/a"}] and config["smart_points"] == {"/x": 5, "/y": 9}
    dialog.apply_to(config)
    assert config["favorites"] == [] and config["favorites_autosort"] is True
    assert config["recent_list"]["max"] == 25 and config["history_size"] == 30
    assert config["smart_favorites"]["min_points"] == 7 and config["smart_favorites"]["points"]["copy_to"] == 55
    assert config["smart_points"] == {}


def test_smart_page_exclusion_rules(qapp_offscreen, monkeypatch, tmp_path):
    config = {"smart_favorites": {"exclude": [{"pattern": "C:\\a", "subfolders": True}]}}
    dialog = PreferencesDialog(config)
    page = dialog.smart_page
    assert not page.subfolders.isEnabled()
    page.exclude.setCurrentRow(0)
    assert page.subfolders.isChecked() and page.subfolders.isEnabled()
    monkeypatch.setattr(page, "_ask_folder", lambda: str(tmp_path))
    monkeypatch.setattr(page, "_ask", lambda *_a, **_k: "*temp*")
    page.add_folder()
    assert not page.subfolders.isChecked()
    page.subfolders.setChecked(True)
    page.add_wildcard()
    page.edit_rule()
    page.exclude.setCurrentRow(0)
    page.remove_rule()
    dialog.apply_to(config)
    assert config["smart_favorites"]["exclude"] == [{"pattern": os.path.normpath(str(tmp_path)), "subfolders": True}, {"pattern": "*temp*", "subfolders": False}]


def test_points_page_lists_every_action_sorted(qapp_offscreen):
    dialog = PreferencesDialog({"smart_favorites": {"points": {"copy_to": 77}}})
    page = dialog.points_page
    assert page.table.rowCount() == len(frequent.ACTIONS) and page.values()["copy_to"] == 77 and page.values()["move_to"] == 10
    names = [page.table.item(row, 0).text() for row in range(page.table.rowCount())]
    assert names == sorted(names, key=str.casefold)


# ------------------------------------------------------------------ recientes
def test_recent_respects_enabled_and_size(window):
    window.config["recent"] = []
    window.config["recent_list"] = {"enabled": False}
    window._remember_recent("/a")
    assert window.config["recent"] == []
    window.config["recent_list"] = {"max": 2}
    for path in ("/a", "/b", "/c"):
        window._recent_entered = ("", 0.0)
        window._remember_recent(path)
    assert [item["path"] for item in window.config["recent"]] == ["/c", "/b"]


def test_recent_virtual_folders_follow_the_setting(window):
    window.config["recent"] = []
    window.config["recent_list"] = {"virtual": False}
    window._remember_recent(listing.COMPUTER_PATH)
    assert window.config["recent"] == []
    window.config["recent_list"] = {"virtual": True}
    window._remember_recent(listing.COMPUTER_PATH)
    assert window.config["recent"] == [{"path": listing.COMPUTER_PATH}]


def test_transit_folder_leaves_the_recent_list(window):
    parent = os.path.normpath("/uno")
    child = os.path.normpath("/uno/dos")
    window.config["recent"] = []
    window._recent_entered = ("", 0.0)
    window._remember_recent(parent)
    window._remember_recent(child)
    assert [item["path"] for item in window.config["recent"]] == [child]


def test_transit_setting_off_keeps_the_folder(window):
    parent, child = os.path.normpath("/uno"), os.path.normpath("/uno/dos")
    window.config["recent"] = []
    window.config["recent_list"] = {"skip_transit": False}
    window._recent_entered = ("", 0.0)
    window._remember_recent(parent)
    window._remember_recent(child)
    assert [item["path"] for item in window.config["recent"]] == [child, parent]


def test_doing_something_in_a_folder_means_it_was_not_transit(window):
    parent, child = os.path.normpath("/uno"), os.path.normpath("/uno/dos")
    window.config["recent"] = []
    window._recent_entered = ("", 0.0)
    window._remember_recent(parent)
    window._award("create_folder", parent)
    window._remember_recent(child)
    assert [item["path"] for item in window.config["recent"]] == [child, parent]


def test_slow_step_is_not_transit(window, monkeypatch):
    parent, child = os.path.normpath("/uno"), os.path.normpath("/uno/dos")
    window.config["recent"] = []
    window._recent_entered = (parent, time.monotonic() - frequent.TRANSIT_SECONDS - 1)
    window.config["recent"] = [{"path": parent}]
    window._remember_recent(child)
    assert [item["path"] for item in window.config["recent"]] == [child, parent]


def test_history_of_each_tab_follows_its_size(window, tmp_path):
    view = window.source
    view.history_limit = 3
    for index in range(5):
        folder = tmp_path / f"h{index}"
        folder.mkdir()
        view.navigate(str(folder))
    assert len(view._history) == 3 and view._history[-1] == os.path.normpath(str(tmp_path / "h4"))
    assert view._history_pos == 2
    view.back()
    assert view.path == os.path.normpath(str(tmp_path / "h3"))


def test_history_size_reaches_open_and_new_tabs(window):
    window.config["history_size"] = 4
    window._preferences_changed()
    assert all(view.history_limit == 4 for view in window.all_views())
    assert window.groups[0].add_tab(window.source.path, activate=False).history_limit == 4


# ------------------------------------------------------------------ puntos
def test_award_adds_the_points_of_the_table():
    config = {"smart_favorites": {"points": {"copy_to": 11}}, "smart_points": {}}
    frequent.award(config, "/a", "copy_to")
    frequent.award(config, "/a", "copy_to")
    frequent.award(config, "/a", "context_menu")  # de serie no da puntos
    assert config["smart_points"] == {"/a": 22}


@pytest.mark.parametrize("path", [listing.COMPUTER_PATH, ""])
def test_virtual_places_get_no_points(path):
    config = {"smart_points": {}}
    frequent.award(config, path, "copy_to")
    assert config["smart_points"] == {}


def test_disabled_or_excluded_folders_get_no_points():
    config = {"smart_favorites": {"enabled": False}, "smart_points": {}}
    frequent.award(config, "/a", "copy_to")
    assert config["smart_points"] == {}
    config = {"smart_favorites": {"exclude": [{"pattern": os.path.normpath("/a"), "subfolders": True}]}, "smart_points": {}}
    frequent.award(config, os.path.normpath("/a/b/c"), "copy_to")
    assert config["smart_points"] == {}
    frequent.award(config, os.path.normpath("/ab"), "copy_to")
    assert list(config["smart_points"]) == [os.path.normpath("/ab")]


def test_exclusion_without_subfolders_and_with_wildcards():
    only = [{"pattern": os.path.normpath("/a"), "subfolders": False}]
    assert frequent.is_excluded(os.path.normpath("/a"), only) and not frequent.is_excluded(os.path.normpath("/a/b"), only)
    wildcard = [{"pattern": "*temp*", "subfolders": False}]
    assert frequent.is_excluded(os.path.normpath("/x/temp/y"), wildcard) and not frequent.is_excluded(os.path.normpath("/x/otra/y"), wildcard)


def test_table_forgets_the_lowest_when_full():
    config = {"smart_favorites": {"table_size": 2}, "smart_points": {"/a": 100, "/b": 1}}
    frequent.award(config, "/c", "copy_to")
    assert set(config["smart_points"]) == {"/a", "/c"}


def test_shown_filters_orders_and_limits():
    points = {f"/{i}": 40 + i for i in range(15)}
    points.update({"/poco": 39, "/favorita": 500})
    config = {"smart_points": points, "favorites": [{"label": "R", "children": [{"label": "f", "path": "/favorita"}]}]}
    shown = [item["path"] for item in frequent.shown(config)]
    assert len(shown) == 10 and shown[0] == "/14" and "/poco" not in shown and "/favorita" not in shown
    assert frequent.shown({**config, "smart_favorites": {"enabled": False}}) == []
    assert len(frequent.shown({**config, "smart_favorites": {"max_shown": 3, "min_points": 0}})) == 3


def test_gone_folders_keeps_those_on_a_missing_drive(tmp_path):
    missing = tmp_path / "no"
    found = frequent.gone_folders([str(tmp_path), str(missing)] + (["Q:\\no-hay-unidad"] if sys.platform == "win32" and not os.path.isdir("Q:\\") else []))
    assert found == [str(missing)]


def test_startup_forgets_folders_that_no_longer_exist(window, tmp_path):
    gone = str(tmp_path / "ya-no")
    window.config["smart_points"] = {gone: 50, str(tmp_path): 50}
    window._check_smart_folders()
    assert wait_until(lambda: gone not in window.config["smart_points"])
    assert str(tmp_path) in window.config["smart_points"]


def test_startup_check_can_be_turned_off(window, tmp_path):
    gone = str(tmp_path / "ya-no")
    window.config["smart_points"] = {gone: 50}
    window.config["smart_favorites"] = {"check_startup": False}
    window._check_smart_folders()
    QApplication.processEvents()
    assert gone in window.config["smart_points"]


def selected_view(window, tmp_path):
    (tmp_path / "izquierda" / "a.txt").write_text("x", encoding="utf-8")
    view = window.source
    view.refresh()
    assert wait_until(lambda: view.model.row_of(os.path.normpath(str(tmp_path / "izquierda" / "a.txt"))) >= 0)
    view.select_paths([os.path.normpath(str(tmp_path / "izquierda" / "a.txt"))])
    return view


def test_commands_award_their_folder(window, tmp_path, monkeypatch):
    view = selected_view(window, tmp_path)
    monkeypatch.setattr(window, "_cmd_copy", lambda _cmd: None)
    monkeypatch.setattr(window, "_cmd_clipboard", lambda _cmd: None)
    monkeypatch.setattr(window, "_cmd_setattr", lambda _cmd: None)
    window.config["smart_points"] = {}
    window.run_command("Copy")
    assert window.config["smart_points"] == {view.path: 5, window.dest.path: 10}
    window.config["smart_points"] = {}
    window.run_command("Copy MOVE")
    assert window.config["smart_points"] == {view.path: 5, window.dest.path: 10}
    window.config["smart_points"] = {}
    window.run_command("Clipboard COPY")
    window.run_command("SetAttr")
    assert window.config["smart_points"] == {view.path: 15}


def test_commands_without_selection_award_nothing(window, monkeypatch):
    monkeypatch.setattr(window, "_cmd_copy", lambda _cmd: None)
    window.source.select_paths([])
    window.config["smart_points"] = {}
    window.run_command("Copy")
    assert window.config["smart_points"] == {}


def test_double_click_on_a_folder_and_on_a_file_award_points(window, tmp_path, monkeypatch):
    view = selected_view(window, tmp_path)
    window.config["smart_points"] = {}
    sub = listing.entry_from_path(os.path.normpath(str(tmp_path / "izquierda" / "sub")))
    view.open_entry(sub)
    assert window.config["smart_points"] == {sub.path: 3}
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QDesktopServices.openUrl", lambda _url: True)
    window.config["smart_points"] = {}
    window._open_file(view, listing.entry_from_path(os.path.normpath(str(tmp_path / "izquierda" / "a.txt"))))
    assert window.config["smart_points"] == {view.path: 3}


def test_thumbnails_view_awards_points(window):
    window.config["smart_points"] = {}
    window.run_command("Set VIEW=Thumbnails")
    assert window.config["smart_points"] == {window.source.path: 8}


def test_dropping_files_awards_both_folders(window, tmp_path, monkeypatch):
    monkeypatch.setattr(window, "start_transfer", lambda *_a: None)
    window.config["smart_points"] = {}
    source = os.path.normpath(str(tmp_path / "izquierda" / "a.txt"))
    target = os.path.normpath(str(tmp_path / "derecha"))
    window._files_dropped(window.source, [source], target, True)
    assert window.config["smart_points"] == {os.path.dirname(source): 5, target: 10}


def test_folder_check_does_not_wait_for_a_slow_folder(monkeypatch, tmp_path):
    real_exists = os.path.exists

    def slow(path):
        if path == "/lenta":
            time.sleep(1)
        return real_exists(path)

    monkeypatch.setattr(listing.os.path, "exists", slow)
    started = time.monotonic()
    states = listing.path_states([str(tmp_path), "/lenta", str(tmp_path / "no"), listing.COMPUTER_PATH], timeout=0.2)
    assert time.monotonic() - started < 0.8
    assert states == {str(tmp_path): True, "/lenta": None, str(tmp_path / "no"): False, listing.COMPUTER_PATH: True}


# ------------------------------------------------------------------ avisos
def test_status_bar_never_shows_and_the_notice_goes_to_the_active_pane(window):
    geometry = window.source.geometry()
    window.statusBar().showMessage("Aviso de prueba", 5000)
    QApplication.processEvents()
    assert window.statusBar().isHidden() and window.source.geometry() == geometry
    assert window.source.status.notice.text() == "Aviso de prueba" and not window.source.status.notice.isHidden()
    other = window.groups[1 - window.active_side].current
    assert other.status.notice.text() == "" and other.status.notice.isHidden()


def test_notice_goes_away_with_the_message(window):
    window.statusBar().showMessage("Aviso", 5000)
    window.statusBar().clearMessage()
    assert window.source.status.notice.text() == "" and window.source.status.notice.isHidden()


def test_notice_takes_the_place_of_the_counters_and_gives_it_back(window):
    status = window.source.status
    window.statusBar().showMessage("Aviso", 5000)
    assert status.dirs.isHidden() and status.files.isHidden() and status.size.isHidden()
    window.statusBar().clearMessage()
    assert not status.dirs.isHidden() and not status.files.isHidden() and not status.size.isHidden()


def test_notice_does_not_widen_a_narrow_pane(window):
    status = window.source.status
    status.set_notice("x" * 500)
    QApplication.processEvents()
    assert status.minimumSizeHint().width() < 300


# ------------------------------------------------------------------ el botón de metadatos
def test_toolbar_has_a_metadata_button_that_toggles_the_panel(window):
    action = next(action for action in window.toolbar.actions() if action.text() == "Panel de metadatos")
    assert window.meta_dock.isHidden()
    action.trigger()
    assert not window.meta_dock.isHidden()
    action.trigger()
    assert window.meta_dock.isHidden()


def test_metadata_button_is_next_to_the_viewer_button(window):
    labels = [action.text() for action in window.toolbar.actions()]
    assert labels.index("Panel de visualización") == labels.index("Panel de metadatos") + 1


# ------------------------------------------------------------------ ExifTool
def fake_exiftool(folder, output, version="13.37", exit_code=0):
    """Un programa de verdad, que responde como ExifTool y anota lo que recibe."""
    payload = folder / "salida.json"
    payload.write_text(output, encoding="utf-8")
    log = folder / "recibido.txt"
    script = folder / "falso.py"
    script.write_text(
        "import sys\n"
        f"if '-ver' in sys.argv:\n    print({version!r}); sys.exit(0)\n"
        f"open({str(log)!r}, 'wb').write(sys.stdin.buffer.read())\n"
        f"open({str(log)!r} + '.args', 'w', encoding='utf-8').write(repr(sys.argv[1:]))\n"
        f"sys.stdout.buffer.write(open({str(payload)!r}, 'rb').read())\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    if sys.platform == "win32":
        tool = folder / "exiftool.cmd"
        tool.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        tool = folder / "exiftool"
        tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    exiftool._usable.pop(str(tool), None)
    return str(tool), log


SAMPLE = json.dumps([{"SourceFile": "x", "ExifTool:ExifToolVersion": {"val": 13.37, "desc": "Versión"}, "ExifTool:Warning": {"val": "Aviso", "desc": "Aviso"},
                      "System:FileName": {"val": "x.png", "desc": "Nombre"}, "File:ImageWidth": {"val": 800, "num": 800, "desc": "Ancho"},
                      "XMP-dc:Subject": {"val": ["uno", "dos"], "desc": "Tema"}, "IFD0:Sin": 5}])


def test_parse_groups_in_order_and_hides_the_version():
    groups = exiftool.parse(SAMPLE.encode("utf-8"))
    assert [name for name, _rows in groups] == ["ExifTool", "System", "File", "XMP-dc", "IFD0"]
    assert dict(groups)["ExifTool"] == [("Aviso", "Aviso")]
    assert dict(groups)["File"] == [("Ancho", "800")] and dict(groups)["XMP-dc"] == [("Tema", "uno, dos")] and dict(groups)["IFD0"] == [("Sin", "5")]


def test_parse_shortens_enormous_values():
    groups = exiftool.parse(json.dumps([{"File:Comment": "x" * 100000}]).encode())
    assert len(dict(groups)["File"][0][1]) == exiftool.MAX_VALUE_LENGTH + 1


@pytest.mark.parametrize("output", [b"", b"[]", b"{}", b"[5]"])
def test_parse_without_data_gives_nothing(output):
    assert exiftool.parse(output) == []


def test_parse_rejects_garbage():
    with pytest.raises(exiftool.ExifToolError):
        exiftool.parse(b"esto no es json")


@pytest.mark.parametrize("code, expected", [("es", "es"), ("en", "en"), ("pt", "en"), ("zh-CN", "zh_cn"), ("de-AT", "de"), ("", "es")])
def test_language_codes(code, expected):
    assert exiftool.language_for(code) == expected


def test_read_sends_the_path_by_stdin_in_utf8(tmp_path):
    tool, log = fake_exiftool(tmp_path, SAMPLE)
    groups = exiftool.read_metadata("C:\\carpeta ñ\\año 東京.png", tool, "es")
    assert dict(groups)["File"] == [("Ancho", "800")]
    assert open(log, "rb").read() == "C:\\carpeta ñ\\año 東京.png\n".encode("utf-8") or open(log, "rb").read().rstrip() == "C:\\carpeta ñ\\año 東京.png".encode("utf-8")
    arguments = open(str(log) + ".args", encoding="utf-8").read()
    assert "-json" in arguments and "filename=utf8" in arguments and "'es'" in arguments and "東京" not in arguments


@pytest.mark.parametrize("path", ["C:\\a\nb.png", "C:\\a\rb.png", "-oculto.png"])
def test_read_refuses_names_that_could_be_taken_for_options(tmp_path, path):
    tool, log = fake_exiftool(tmp_path, SAMPLE)
    with pytest.raises(exiftool.ExifToolError):
        exiftool.read_metadata(path, tool)
    assert not os.path.exists(log)


def test_read_refuses_an_old_version(tmp_path):
    tool, log = fake_exiftool(tmp_path, SAMPLE, version="12.23")
    with pytest.raises(exiftool.ExifToolError, match="antiguo"):
        exiftool.read_metadata("C:\\a.png", tool)
    assert not os.path.exists(log)


def test_read_refuses_something_that_is_not_exiftool(tmp_path):
    tool, _log = fake_exiftool(tmp_path, SAMPLE, version="no soy exiftool")
    with pytest.raises(exiftool.ExifToolError):
        exiftool.read_metadata("C:\\a.png", tool)


def test_find_prefers_the_chosen_folder_and_never_the_current_one(tmp_path, monkeypatch):
    name = "exiftool.exe" if sys.platform == "win32" else "exiftool"
    (tmp_path / "elegida").mkdir()
    (tmp_path / "actual").mkdir()
    (tmp_path / "elegida" / name).write_text("", encoding="utf-8")
    (tmp_path / "actual" / name).write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(tmp_path / "actual")
    assert exiftool.find_exiftool(str(tmp_path / "elegida")) == str(tmp_path / "elegida" / name)
    assert exiftool.find_exiftool("") is None
    assert exiftool.find_exiftool("relativa") is None


def test_packaged_program_uses_its_own_exiftool_unless_another_folder_is_chosen(tmp_path, monkeypatch):
    name = "exiftool.exe" if sys.platform == "win32" else "exiftool"
    for folder in ("programa/exiftool", "elegida", "ruta"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / name).write_text("", encoding="utf-8")
    (tmp_path / "ruta" / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "ruta"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "programa" / "directory-commander-tux-tux.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    own = str(tmp_path / "programa" / "exiftool" / name)
    assert exiftool.find_exiftool("") == own
    assert exiftool.find_exiftool(str(tmp_path / "vacía")) == own
    assert exiftool.find_exiftool(str(tmp_path / "elegida")) == str(tmp_path / "elegida" / name)


@pytest.mark.skipif(sys.platform != "win32", reason="es una limitación de su lanzador para Windows")
def test_tool_in_a_folder_the_system_code_page_cannot_write_starts_by_its_short_path(tmp_path):
    plain = tmp_path / "exiftool.exe"
    plain.write_text("", encoding="utf-8")
    assert exiftool._launchable(str(plain)) == str(plain)
    # Cirílico y japonés: ninguna página de códigos de Windows tiene los dos.
    (tmp_path / "Жук東京").mkdir()
    foreign = tmp_path / "Жук東京" / "exiftool.exe"
    foreign.write_text("", encoding="utf-8")
    short = exiftool._launchable(str(foreign))
    if short == str(foreign):
        pytest.skip("este volumen no tiene nombres cortos")
    assert os.path.samefile(short, foreign)
    short.encode("mbcs", "strict")


@pytest.mark.skipif(sys.platform != "win32", reason="ese nombre es el de la descarga para Windows")
def test_find_accepts_the_name_of_the_official_download(tmp_path, monkeypatch):
    (tmp_path / "exiftool(-k).exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    assert exiftool.find_exiftool(str(tmp_path)) == str(tmp_path / "exiftool(-k).exe")


def sample_entry(tmp_path, name="foto.png"):
    path = tmp_path / name
    path.write_bytes(b"x")
    return Entry(name, str(path), False, size=1)


def test_panel_without_exiftool_shows_basics_and_where_to_get_it(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: None)
    panel = MetadataPanel()
    panel.show_entry(sample_entry(tmp_path))
    assert dict(panel.groups()[0][1])["Nombre"] == "foto.png"
    assert not panel.note.isHidden() and "exiftool.org" in panel.note.text()


def test_panel_with_exiftool_shows_its_groups_in_the_background(qapp_offscreen, tmp_path, monkeypatch):
    calls = []

    def read(path, tool, language):
        calls.append((path, tool, language))
        return [("File", [("Ancho", "800")])]

    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: "herramienta")
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.read_metadata", read)
    panel = MetadataPanel()
    entry = sample_entry(tmp_path)
    panel.show_entry(entry)
    assert calls == []  # no se lanza nada al momento: se puede estar recorriendo la lista
    assert wait_until(lambda: panel.groups() == [("File", [("Ancho", "800")])])
    assert calls == [(entry.path, "herramienta", "es")] and panel.note.isHidden()


def test_panel_passing_quickly_only_reads_the_last_file(qapp_offscreen, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: "herramienta")
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.read_metadata", lambda path, _tool, _language: calls.append(path) or [("G", [("t", path)])])
    panel = MetadataPanel()
    first, last = sample_entry(tmp_path, "a.png"), sample_entry(tmp_path, "b.png")
    panel.show_entry(first)
    panel.show_entry(last)
    assert wait_until(lambda: panel.groups() == [("G", [("t", last.path)])])
    assert calls == [last.path]


def test_panel_ignores_a_late_answer_for_another_file(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: "herramienta")

    def slow(path, _tool, _language):
        time.sleep(0.3)
        return [("G", [("t", path)])]

    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.read_metadata", slow)
    panel = MetadataPanel()
    first = sample_entry(tmp_path, "a.png")
    panel.show_entry(first)
    assert wait_until(lambda: not panel._timer.isActive())
    panel.show_entry(None)
    time.sleep(0.4)
    QApplication.processEvents()
    assert panel.groups() == []


def test_panel_never_runs_exiftool_on_a_folder(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: "herramienta")
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.read_metadata", lambda *_a: pytest.fail("no debe leer carpetas"))
    panel = MetadataPanel()
    panel.show_entry(Entry("carpeta", str(tmp_path), True))
    time.sleep(0.2)
    QApplication.processEvents()
    assert panel.groups()[0][0] == "Archivo"


def test_panel_shows_the_error_and_the_basics_when_exiftool_fails(qapp_offscreen, tmp_path, monkeypatch):
    def broken(*_args):
        raise exiftool.ExifToolError("Falló <mal>")

    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: "herramienta")
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.read_metadata", broken)
    panel = MetadataPanel()
    panel.show_entry(sample_entry(tmp_path))
    assert wait_until(lambda: not panel.note.isHidden())
    assert "&lt;mal&gt;" in panel.note.text() and dict(panel.groups()[0][1])["Nombre"] == "foto.png"


def test_panel_clears_with_nothing_selected(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.preview.find_exiftool", lambda _folder: None)
    panel = MetadataPanel()
    panel.show_entry(sample_entry(tmp_path))
    panel.show_entry(None)
    assert panel.groups() == [] and panel.note.isHidden()


def test_exiftool_folder_is_saved_and_reaches_the_panel(window, tmp_path):
    window.config["exiftool_dir"] = str(tmp_path)
    window._preferences_changed()
    assert window.metadata.exiftool_dir == str(tmp_path)
    dialog = PreferencesDialog(window.config)
    assert dialog.exiftool_dir.text() == str(tmp_path)
    dialog.exiftool_dir.setText(" ")
    dialog.apply_to(window.config)
    assert window.config["exiftool_dir"] == ""
