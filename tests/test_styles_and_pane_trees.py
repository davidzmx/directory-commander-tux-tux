"""Estilos de doble árbol, nombres de los estilos y orden del visor y los metadatos."""
import ctypes
import json
import os
import stat
import sys
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import settings_io
from directory_commander_tux_tux.core.config import DEFAULT_STYLES, METADATA_TOGGLE_COMMAND, VIEWER_TOGGLE_COMMAND, Config
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.folder_tree import LOADED_ROLE, FolderTree
from directory_commander_tux_tux.ui.main_window import MainWindow


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folders(tmp_path):
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    (left / "sub").mkdir(parents=True)
    (right / "otra").mkdir(parents=True)
    return left, right


@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    config = Config()
    config["show_hidden"] = True  # en un Windows normal, el directorio temporal cuelga de AppData, que está oculto: el árbol se quedaría antes de llegar
    main = MainWindow(config, [str(folder) for folder in folders])
    main.resize(1500, 800)
    main.show()
    QApplication.processEvents()
    yield main
    main.close()
    main.deleteLater()
    QApplication.processEvents()


def wait_until(condition, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def same(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def hidden_folder(parent):
    folder = parent / ".oculta"
    folder.mkdir()
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetFileAttributesW(str(folder), stat.FILE_ATTRIBUTE_HIDDEN)
    return folder


@pytest.fixture
def lone_tree(qapp_offscreen, tmp_path):
    tree = FolderTree()
    yield tree, tree._node(tree, "raíz", str(tmp_path), QIcon())
    tree.dispose()


def test_showing_hidden_folders_makes_the_way_to_one_wait_for_the_new_list(lone_tree, tmp_path):
    tree, item = lone_tree
    hidden = hidden_folder(tmp_path)
    tree._load(item)
    assert wait_until(lambda: item.data(0, LOADED_ROLE)) and item.childCount() == 0
    tree.set_show_hidden(True)
    tree._descend(item, [str(hidden)], tree._sync_token)  # sin esperar: la lista que hay aún no la tiene
    assert wait_until(lambda: same(tree.current_path() or ".", hidden)), tree.current_path()


def test_a_list_read_before_showing_hidden_folders_is_not_used(lone_tree, tmp_path):
    tree, item = lone_tree
    hidden_folder(tmp_path)
    tree._load(item)
    tree.set_show_hidden(True)  # la lectura ya estaba en marcha, con la opción de antes
    assert wait_until(lambda: item.data(0, LOADED_ROLE)) and item.childCount() == 1


def item_for(tree, path):
    """El elemento de la carpeta, una vez que el árbol ha llegado a ella."""
    assert wait_until(lambda: same(tree.current_path() or ".", path), 15), (tree.current_path(), str(path))
    return tree.currentItem()


# ------------------------------------------------------------------ el orden del visor y los metadatos
def test_default_toolbar_has_metadata_before_viewer():
    from directory_commander_tux_tux.core.config import DEFAULT_TOOLBAR
    commands = [item["commands"][0] for item in DEFAULT_TOOLBAR if item.get("commands")]
    assert commands.index(VIEWER_TOGGLE_COMMAND) == commands.index(METADATA_TOGGLE_COMMAND) + 1


def show_both(window):
    window.run_command("Set VIEWPANE=On")
    window.run_command("Set METAPANE=On")
    QApplication.processEvents()


def test_viewer_goes_above_metadata_by_default(window):
    show_both(window)
    assert window.viewer_dock.y() < window.meta_dock.y()


def test_the_first_time_together_they_share_the_height(window):
    show_both(window)
    assert abs(window.viewer_dock.height() - window.meta_dock.height()) < 40


def test_a_height_the_user_chose_survives_hiding_and_showing(window):
    show_both(window)
    window.resizeDocks([window.viewer_dock, window.meta_dock], [500, 150], Qt.Orientation.Vertical)
    QApplication.processEvents()
    height = window.viewer_dock.height()
    assert height > window.meta_dock.height() + 100
    window.run_command("Set METAPANE=Off")
    QApplication.processEvents()
    window.run_command("Set METAPANE=On")
    QApplication.processEvents()
    assert window.viewer_dock.height() > window.meta_dock.height() + 100


def test_the_choice_is_applied_at_once_and_survives_changing_style(window):
    show_both(window)
    window.config["viewer_above_metadata"] = False
    window._preferences_changed()
    QApplication.processEvents()
    assert window.meta_dock.y() < window.viewer_dock.y()
    window.run_command('Set STYLE="Imágenes"')
    window.run_command("Set METAPANE=On")
    QApplication.processEvents()
    assert window.meta_dock.y() < window.viewer_dock.y()


def test_arranging_does_not_show_a_hidden_panel(window):
    assert window.meta_dock.isHidden() and window.viewer_dock.isHidden()
    window._arrange_side_docks()
    QApplication.processEvents()
    assert window.meta_dock.isHidden() and window.viewer_dock.isHidden()


def test_a_panel_moved_elsewhere_stays_there(window):
    show_both(window)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, window.meta_dock)
    window._arrange_side_docks()
    assert window.dockWidgetArea(window.meta_dock) == Qt.DockWidgetArea.LeftDockWidgetArea


def test_preferences_offer_the_order(window):
    dialog = PreferencesDialog(window.config, window)
    assert dialog.panel_order.currentData() is True
    dialog.panel_order.setCurrentIndex(1)
    dialog.apply_to(window.config)
    assert window.config["viewer_above_metadata"] is False
    assert PreferencesDialog(window.config, window).panel_order.currentData() is False


def test_the_order_travels_in_the_settings_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"viewer_above_metadata": False}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"viewer_above_metadata": False}, [])


def test_order_is_kept_and_restored_on_the_next_start(qapp_offscreen, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.config["viewer_above_metadata"] = False
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    first._preferences_changed()
    show_both(first)
    first.close()
    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    QApplication.processEvents()
    assert not second.meta_dock.isHidden() and not second.viewer_dock.isHidden()
    assert second.meta_dock.y() < second.viewer_dock.y()
    second.close()


def test_an_older_session_gets_the_chosen_order_once(qapp_offscreen, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    first.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, first.viewer_dock)  # así quedaban: los metadatos encima
    show_both(first)
    assert first.meta_dock.y() < first.viewer_dock.y()
    first.close()
    saved = json.loads((tmp_path / "cfg" / "config.json").read_text(encoding="utf-8"))
    del saved["session"]["viewer_above_metadata"]
    (tmp_path / "cfg" / "config.json").write_text(json.dumps(saved), encoding="utf-8")
    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    QApplication.processEvents()
    assert second.viewer_dock.y() < second.meta_dock.y()
    second.close()


# ------------------------------------------------------------------ nombres y migración
def test_styles_have_their_new_names_and_no_metadata_style():
    names = [style["name"] for style in DEFAULT_STYLES]
    assert names[:2] == ["Vertical doble panel", "Vertical doble árbol"]
    assert names[5:7] == ["Horizontal doble panel", "Horizontal doble árbol"]
    assert "Metadatos" not in names and "Vertical Dual" not in names and "Horizontal Dual" not in names


OLD_STYLES = [{"name": "Vertical Dual", "dual": "Vertical", "tree": True, "viewer": False, "meta": False, "view": "details"},
              {"name": "Horizontal Dual", "dual": "Horizontal", "tree": True, "viewer": False, "meta": False, "view": "list"},
              {"name": "Metadatos", "dual": "Off", "tree": True, "viewer": False, "meta": True, "view": "list"},
              {"name": "Mío", "dual": "Off", "tree": False, "viewer": True, "meta": True, "view": "list"}]


def upgraded(tmp_path, **stored) -> Config:
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 6, "styles": json.loads(json.dumps(OLD_STYLES)), **stored}), encoding="utf-8")
    return Config(str(tmp_path))


def test_upgrade_renames_styles_removes_metadata_and_adds_the_double_trees(tmp_path):
    config = upgraded(tmp_path)
    assert [style["name"] for style in config["styles"]] == ["Vertical doble panel", "Vertical doble árbol", "Vertical panel-terminal", "Horizontal doble panel", "Horizontal doble árbol", "Horizontal panel-terminal", "Mío"]
    assert config["styles"][0]["view"] == "details"  # lo que el usuario guardó en ese estilo sigue igual
    assert config["styles"][1]["double_tree"] is True


def test_upgrade_moves_the_current_style(tmp_path):
    assert upgraded(tmp_path, style="Vertical Dual")["style"] == "Vertical doble panel"
    assert upgraded(tmp_path, style="Metadatos")["style"] == "Explorador"
    assert upgraded(tmp_path, style="Mío")["style"] == "Mío"


def test_upgrade_keeps_a_metadata_style_the_user_changed(tmp_path):
    changed = [{"name": "Metadatos", "dual": "Vertical", "tree": False, "viewer": False, "meta": True, "view": "list"}]
    config = upgraded(tmp_path, styles=changed, style="Metadatos", bar_order={"styles": ["Metadatos"]})
    assert config["style"] == "Metadatos" and "Metadatos" in [style["name"] for style in config["styles"]]
    assert config["bar_order"]["styles"] == ["Metadatos"]


def test_upgrade_renames_in_bars_and_layouts(tmp_path):
    config = upgraded(tmp_path, bar_order={"styles": ["Metadatos", "Horizontal Dual", "Vertical Dual"]}, bar_buttons={"styles": ["Vertical Dual", "Metadatos"]},
                      layouts={"Trabajo": {"style": "Horizontal Dual"}, "Roto": "basura", "Sin": {}})
    assert config["bar_order"]["styles"] == ["Horizontal doble panel", "Vertical doble panel"]
    assert config["bar_buttons"]["styles"] == ["Vertical doble panel"]
    assert config["layouts"]["Trabajo"]["style"] == "Horizontal doble panel"
    assert config["layouts"]["Roto"] == "basura" and config["layouts"]["Sin"] == {}


def test_upgrade_survives_damaged_values(tmp_path):
    config = upgraded(tmp_path, styles=["basura", {"sin": "nombre"}], bar_order={"styles": [1, None]}, layouts="basura", toolbar="basura")
    assert config["styles"][0] == "basura" and config["styles"][1] == {"sin": "nombre"}


VIEWER = {"label": "Panel de visualización", "commands": [VIEWER_TOGGLE_COMMAND]}
METADATA = {"label": "Panel de metadatos", "commands": [METADATA_TOGGLE_COMMAND]}


def test_upgrade_swaps_the_two_buttons_in_the_toolbar_and_in_the_chosen_order(tmp_path):
    other = {"label": "Otro", "commands": ["Go UP"]}
    order = ["Otro", "Panel de visualización", "Panel de metadatos", "|"]
    config = upgraded(tmp_path, toolbar=[other, VIEWER, METADATA], bar_order={"toolbar": order})
    assert [item["label"] for item in config["toolbar"]] == ["Otro", "Panel de metadatos", "Panel de visualización", "Terminal general", "Marcos de los paneles", "Comparar archivos", "Marcas de IA", "Abrir vídeos", "Abrir imágenes", "Notas"]
    assert config["bar_order"]["toolbar"] == ["Otro", "Panel de metadatos", "Panel de visualización", "|"]


def test_upgrade_does_not_touch_buttons_the_user_separated(tmp_path):
    other = {"label": "Otro", "commands": ["Go UP"]}
    config = upgraded(tmp_path, toolbar=[VIEWER, other, METADATA])
    assert [item["label"] for item in config["toolbar"]] == ["Panel de visualización", "Otro", "Panel de metadatos", "Terminal general", "Marcos de los paneles", "Comparar archivos", "Marcas de IA", "Abrir vídeos", "Abrir imágenes", "Notas"]


def test_a_custom_button_with_the_old_name_still_works(window):
    window.run_command('Set STYLE="Horizontal Dual"')
    assert window.config["style"] == "Horizontal doble panel" and window.dual == "Horizontal"


# ------------------------------------------------------------------ doble árbol
@pytest.mark.parametrize("style, orientation", [("Vertical doble árbol", Qt.Orientation.Horizontal), ("Horizontal doble árbol", Qt.Orientation.Vertical)])
def test_each_side_has_its_own_tree(window, style, orientation):
    window.run_command(f'Set STYLE="{style}"')
    QApplication.processEvents()
    assert window.double_tree and window.splitter.orientation() == orientation
    assert window.tree_dock.isHidden()
    for side, area, group, panel in zip(window.sides[:2], window.pane_areas, window.groups, window.tree_panels):
        assert panel.isVisible() and group.isVisible()
        assert panel.geometry().right() < area.geometry().left()  # el árbol, a la izquierda de su panel
        assert side.orientation() == Qt.Orientation.Horizontal
        assert 90 <= panel.width() < group.width()


def test_each_tree_follows_its_own_pane(window, folders):
    window.run_command('Set STYLE="Vertical doble árbol"')
    left, right = folders
    assert item_for(window.pane_trees[0], left) and item_for(window.pane_trees[1], right)
    assert window.groups[1].current.navigate(str(right / "otra")) is None
    assert item_for(window.pane_trees[1], right / "otra")
    assert same(window.pane_trees[0].current_path(), left)


def test_clicking_a_tree_opens_the_folder_in_its_pane_and_activates_it(window, folders):
    window.run_command('Set STYLE="Vertical doble árbol"')
    left, right = folders
    assert window.active_side == 0
    tree = window.pane_trees[1]
    parent = item_for(tree, right)
    tree.expandItem(parent)
    assert wait_until(lambda: parent.childCount() > 0)
    QApplication.processEvents()
    child = parent.child(0)
    tree.scrollToItem(child)
    rect = tree.visualItemRect(child)
    point = rect.topLeft() + QPoint(8, rect.height() // 2)  # la columna es tan ancha como el nombre más largo: su centro puede quedar fuera
    assert tree.itemAt(point) is child and tree.viewport().rect().contains(point)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert wait_until(lambda: same(window.groups[1].current.path, right / "otra"))
    assert window.active_side == 1 and window.source is window.groups[1].current
    assert same(window.groups[0].current.path, left)


def test_changing_the_active_pane_does_not_move_the_other_tree(window, monkeypatch):
    window.run_command('Set STYLE="Vertical doble árbol"')
    QApplication.processEvents()
    calls = []
    for tree in window.pane_trees:
        monkeypatch.setattr(tree, "sync_to", calls.append)
    window._view_activated(window.groups[1].current)
    window._view_activated(window.groups[0].current)
    assert calls == []


# ------------------------------------------------------------------ el árbol de un solo panel
def test_a_pane_shows_and_hides_its_own_tree_with_the_button_of_its_bar(window, folders):
    left, right = folders
    assert not window.double_tree and all(panel.isHidden() for panel in window.tree_panels)
    window.groups[1].current._bar_tools()["treepane"].click()
    QApplication.processEvents()
    assert window.active_side == 1
    assert window.tree_panels[1].isVisible() and window.tree_panels[0].isHidden()
    assert window.tree_panels[1].geometry().right() < window.pane_areas[1].geometry().left()
    assert item_for(window.pane_trees[1], right)
    window.groups[1].current.navigate(str(right / "otra"))
    assert item_for(window.pane_trees[1], right / "otra")  # sigue a su panel, también sin un árbol por panel
    window.run_command("Set PANETREE=Toggle")
    assert window.tree_panels[1].isHidden()


def test_the_tree_of_a_pane_does_not_touch_the_general_tree(window, folders):
    window.run_command("Set TREE=On")
    window.run_command("Set PANETREE=On")
    QApplication.processEvents()
    assert not window.tree_dock.isHidden() and window.tree_panels[0].isVisible()
    window.run_command("Set PANETREE=Off")
    assert not window.tree_dock.isHidden() and window.tree_panels[0].isHidden()


def test_a_tree_shown_again_goes_to_where_its_pane_is_now(window, folders):
    left, _right = folders
    window.run_command("Set PANETREE=On")
    assert item_for(window.pane_trees[0], left)
    window.run_command("Set PANETREE=Off")
    window.groups[0].current.navigate(str(left / "sub"))
    window.run_command("Set PANETREE=On")
    assert item_for(window.pane_trees[0], left / "sub")


def test_the_session_remembers_which_panes_show_their_tree(window):
    window.groups[1].current.activated.emit(window.groups[1].current)
    window.run_command("Set PANETREE=On")
    snapshot = window._session_snapshot()
    window.run_command("Set PANETREE=Off")
    window._apply_session(snapshot, [])
    QApplication.processEvents()
    assert window.tree_panels[1].isVisible() and window.tree_panels[0].isHidden()


def test_a_new_tab_moves_the_tree_of_its_side(window, folders):
    window.run_command('Set STYLE="Vertical doble árbol"')
    left, right = folders
    assert item_for(window.pane_trees[1], right)
    window.groups[1].add_tab(str(left), activate=True)
    assert item_for(window.pane_trees[1], left)
    assert same(window.pane_trees[0].current_path(), left)


def test_files_dropped_on_a_tree_act_for_its_pane(window, folders, monkeypatch):
    window.run_command('Set STYLE="Vertical doble árbol"')
    dropped = []
    monkeypatch.setattr(window, "_files_dropped", lambda view, *rest: dropped.append(view))
    window.pane_trees[1].filesDropped.emit(["a"], "b", False)
    assert dropped == [window.groups[1].current]


def test_tree_command_shows_and_hides_both(window):
    window.run_command('Set STYLE="Vertical doble árbol"')
    window.run_command("Set TREE=Toggle")
    assert all(panel.isHidden() for panel in window.tree_panels) and window.tree_dock.isHidden()
    window.run_command("Set TREE=Toggle")
    assert not any(panel.isHidden() for panel in window.tree_panels[:2]) and window.tree_dock.isHidden()
    window.run_command("Set TREE=Off")
    assert all(panel.isHidden() for panel in window.tree_panels)
    window.run_command("Set TREE=On")
    assert not any(panel.isHidden() for panel in window.tree_panels[:2])


def test_toggling_with_only_one_tree_hidden_hides_the_other(window):
    window.run_command('Set STYLE="Vertical doble árbol"')
    window.tree_panels[0].hide()
    window.run_command("Set TREE=Toggle")
    assert all(panel.isHidden() for panel in window.tree_panels)


def test_other_styles_return_to_the_shared_tree(window):
    window.run_command('Set STYLE="Horizontal doble árbol"')
    window.run_command('Set STYLE="Vertical doble panel"')
    assert not window.double_tree and not window.tree_dock.isHidden()
    assert all(panel.isHidden() for panel in window.tree_panels)
    assert window.tree_dock.toggleViewAction().isEnabled()
    window.run_command('Set STYLE="Comandante"')
    assert window.tree_dock.isHidden() and all(panel.isHidden() for panel in window.tree_panels)


def test_shared_tree_cannot_be_shown_on_top_of_the_two(window):
    window.run_command('Set STYLE="Vertical doble árbol"')
    assert not window.tree_dock.toggleViewAction().isEnabled()


def test_a_single_pane_hides_the_second_side_and_its_tree(window):
    window.run_command('Set STYLE="Vertical doble árbol"')
    QApplication.processEvents()
    window.run_command("Set DUAL=Off")
    QApplication.processEvents()
    assert window.sides[1].isHidden() and not window.tree_panels[1].isVisible()
    assert window.tree_panels[0].isVisible()
    window.run_command("Set DUAL=Vertical")
    QApplication.processEvents()
    assert window.tree_panels[1].isVisible() and window.groups[1].isVisible()


def test_favorites_and_hidden_reach_every_tree(window, folders):
    window.config["favorites"] = [{"label": "F", "path": str(folders[0])}]
    window._rebuild_favorites_bar()
    assert all(tree.favorites_item.childCount() == 1 for tree in window.all_trees())
    window.run_command("Set HIDDEN=On")
    assert all(tree.show_hidden for tree in window.all_trees())


def test_saved_style_remembers_the_double_tree(window, monkeypatch):
    window.run_command('Set STYLE="Vertical doble árbol"')
    monkeypatch.setattr(window, "_ask_text", lambda *a: "Mío")
    window._save_current_style()
    assert window._style_named("Mío")["double_tree"] is True and window._style_named("Mío")["tree"] is True
    window.run_command('Set STYLE="Único"')
    window.run_command('Set STYLE="Mío"')
    assert window.double_tree


def test_session_restores_the_trees_with_their_widths(qapp_offscreen, tmp_path, monkeypatch, folders):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    first.run_command('Set STYLE="Horizontal doble árbol"')
    QApplication.processEvents()
    first.sides[0].setSizes([400, 1000])
    first.tree_panels[1].hide()
    QApplication.processEvents()
    width = first.tree_panels[0].width()
    first.close()
    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    QApplication.processEvents()
    assert second.double_tree and second.tree_dock.isHidden()
    assert second.tree_panels[1].isHidden() and second.tree_panels[0].isVisible()
    assert abs(second.tree_panels[0].width() - width) < 40
    second.close()


def test_theme_change_reaches_the_buttons_of_the_new_trees(window):
    window.run_command('Set STYLE="Vertical doble árbol"')
    window.run_command("Set DARKMODE=Toggle")
    assert all(not tree.home_item.icon(0).isNull() for tree in window.all_trees())
