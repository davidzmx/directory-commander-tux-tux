"""El panel de buscar: opciones, resultados en un panel como una carpeta más, plantillas y el comando de Linux."""
import os
import time
import zipfile

import pytest
from PySide6.QtCore import QDate, QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import file_search, listing, settings_io
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.core.listing import get_results, is_results, results_path
from directory_commander_tux_tux.ui.file_view import COLUMN_INDEX, path_crumbs
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


@pytest.fixture
def tree(tmp_path):
    left = tmp_path / "izq"
    (left / "sub").mkdir(parents=True)
    (tmp_path / "der").mkdir()
    for name, text in (("mascota.png", "x"), ("nota.txt", "con aguja"), ("sub/mascota2.txt", "aguja"), ("otro.txt", "nada"), ("grande.bin", "z" * 5000)):
        (left / name).write_text(text, encoding="utf-8")
    with zipfile.ZipFile(left / "c.zip", "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("mascota_zip.txt", "dentro")
    return tmp_path


@pytest.fixture
def window(tree, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tree / "config"))
    listing._results.clear()
    main = MainWindow(Config(), [str(tree / "izq"), str(tree / "der")])
    main.resize(1200, 700)
    main.show()
    assert wait_until(lambda: main.source.model.rowCount() > 0)
    settle(60)
    yield main
    main.jobs.cancel_all()
    if main.find_panel._call:
        main.find_panel._call.cancel()
        wait_until(lambda: not main.find_panel.is_running)
    main.close()
    listing._results.clear()


def names(view) -> list[str]:
    return sorted(entry.name for entry in view.model.entries)


def search(window, text="", **changes):
    panel = window.find_panel
    if text:
        panel.name.box.setEditText(text)
    for key, value in changes.items():
        getattr(panel, key).setChecked(value)
    panel.start()
    assert wait_until(lambda: not panel.is_running)
    settle(30)
    target = window.groups[window.active_side].current
    assert wait_until(lambda: target.model._loading is None)
    settle(30)
    return target


# ---- buscar ----

def test_defaults_are_those_of_the_reference(window):
    panel = window.find_panel
    assert panel.name.flags["wildcards"].isChecked() and panel.name.flags["partial"].isChecked() and not panel.name.flags["regex"].isChecked()
    assert not panel.content.flags["wildcards"].isChecked() and panel.content.flags["partial"].isChecked() and "utf8" in panel.content.flags
    assert panel.subfolders.isChecked() and not panel.archives.isChecked() and panel.clear.isChecked()
    assert panel.target.currentData() == "source_new" and panel.collection.currentText() == "Resultados de la Búsqueda"
    assert panel.date_filter.op.currentData() == panel.time_filter.op.currentData() == panel.size_filter.op.currentData() == "ignore"
    assert panel.excludes.values == file_search.default_excludes()


def test_results_open_in_the_active_pane_as_a_folder(window, tree):
    view = search(window, "*mascot*")
    assert is_results(view.path) and names(view) == ["mascota.png", "mascota2.txt"]
    assert path_crumbs(view.path) == [("Resultados de la Búsqueda", view.path)]
    assert window.find_panel.summary.text().startswith("2 resultados")


def test_results_show_where_each_one_is(window, tree):
    view = search(window, "mascota2")
    assert not view.details.isColumnHidden(COLUMN_INDEX["location"]) and view.current_view is view.details
    assert view.model.index(0, COLUMN_INDEX["location"]).data() == str(tree / "izq" / "sub")


def test_normal_folders_do_not_have_the_location_column(window, tree):
    view = window.source
    assert view.details.isColumnHidden(COLUMN_INDEX["location"])
    search(window, "mascota2")
    view.navigate(str(tree / "izq"))
    settle(100)
    assert view.details.isColumnHidden(COLUMN_INDEX["location"])


def test_leaving_the_results_restores_the_view(window, tree):
    view = window.source
    assert view.view_mode == "list"
    window.find_panel.target.setCurrentIndex(window.find_panel.target.findData("source"))
    search(window, "mascota2")
    assert view.view_mode == "details"
    view.back()
    settle(100)
    assert view.view_mode == "list" and view.path == str(tree / "izq")


def test_tab_and_location_say_the_name(window):
    view = search(window, "mascota")
    assert "Resultados" in window.groups[window.active_side].tabText(window.groups[window.active_side].currentIndex())
    assert listing.location_label(view.path) == "Resultados de la Búsqueda"


def test_results_can_go_to_the_other_pane_or_a_new_tab(window, tree):
    panel = window.find_panel
    panel.target.setCurrentIndex(panel.target.findData("dest"))
    search(window, "nota")
    assert is_results(window.dest.path) and window.source.path == str(tree / "izq")
    panel.target.setCurrentIndex(panel.target.findData("source_new"))
    tabs = window.groups[window.active_side].count()
    search(window, "otro")
    assert window.groups[window.active_side].count() == tabs + 1 and is_results(window.source.path)


def test_clear_previous_or_add_to_them(window):
    view = search(window, "nota")
    assert names(view) == ["nota.txt"]
    window.find_panel.collection.setEditText("Resultados de la Búsqueda")
    view = search(window, "otro")
    assert names(view) == ["otro.txt"]
    view = search(window, "nota", clear=False)
    assert names(view) == ["nota.txt", "otro.txt"]


def test_collections_are_kept_apart(window):
    search(window, "nota")
    panel = window.find_panel
    panel.collection.setEditText("Mis cosas")
    view = search(window, "otro")
    assert names(view) == ["otro.txt"] and view.path == results_path("Mis cosas")
    assert get_results("Resultados de la Búsqueda")
    view.navigate(results_path("Resultados de la Búsqueda"))
    settle(100)
    assert names(view) == ["nota.txt"]


def test_while_searching_the_button_stops(window, monkeypatch):
    panel = window.find_panel
    release = []

    def slow(state, is_cancelled, on_batch):
        while not is_cancelled():
            time.sleep(0.01)
        release.append(1)
        return file_search.Found(cancelled=True)

    monkeypatch.setattr(file_search, "run_search", slow)
    panel.start()
    assert panel.go.text() == "Detener" and panel.is_running
    panel.go.click()
    assert wait_until(lambda: not panel.is_running)
    assert release and panel.go.text() == "Buscar" and "detenida" in panel.summary.text()


def test_bad_expression_is_explained_and_nothing_is_searched(window):
    panel = window.find_panel
    panel.name.flags["regex"].setChecked(True)
    panel.name.box.setEditText("(")
    panel.start()
    assert not panel.is_running and panel.summary.text().startswith("⚠")
    assert not is_results(window.source.path)


def test_wildcards_and_regular_expressions_exclude_each_other(window):
    flags = window.find_panel.name.flags
    flags["regex"].setChecked(True)
    assert not flags["wildcards"].isChecked()
    flags["wildcards"].setChecked(True)
    assert not flags["regex"].isChecked()


def test_nowhere_to_search_is_said(window):
    panel = window.find_panel
    panel.follow_source = False
    panel.roots.set_values([])
    panel.start()
    assert "dónde" in panel.summary.text() and not panel.is_running


def test_inside_archives_when_asked(window):
    view = search(window, "mascota_zip", archives=True)
    assert names(view) == ["mascota_zip.txt"] and view.model.entries[0].in_archive


# ---- dónde buscar ----

def test_follows_the_active_pane(window, tree):
    panel = window.find_panel
    assert panel.roots.values == [str(tree / "izq")]
    window._view_activated(window.groups[1].current)
    settle()
    assert panel.roots.values == [str(tree / "der")]


def test_search_from_the_results_keeps_the_last_real_folder(window, tree):
    search(window, "nota")
    assert window.find_panel.roots.values == [str(tree / "izq")]
    assert names(search(window, "otro")) == ["otro.txt"]


def test_stops_following_when_a_folder_is_chosen(window, tree, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.find_panel.FolderPicker.ask", lambda *a, **k: [str(tree / "der")])
    panel = window.find_panel
    panel._add_root()
    assert not panel.follow_source and panel.roots.values == [str(tree / "izq"), str(tree / "der")]
    window._view_activated(window.groups[1].current)
    settle()
    assert panel.roots.values == [str(tree / "izq"), str(tree / "der")]


def test_several_places_at_once(window, tree):
    (tree / "der" / "nota2.txt").write_text("x")
    panel = window.find_panel
    panel.follow_source = False
    panel.roots.set_values([str(tree / "izq"), str(tree / "der")])
    assert names(search(window, "nota")) == ["nota.txt", "nota2.txt"]


def test_chips_can_be_removed(window, tree):
    panel = window.find_panel
    panel.follow_source = False
    panel.roots.set_values(["uno", "dos"])
    panel.roots.remove_value("uno")
    assert panel.roots.values == ["dos"]
    panel.roots.add_value("dos")
    assert panel.roots.values == ["dos"]


def test_exclusions_apply(window):
    panel = window.find_panel
    panel.excludes.add_value("sub")
    assert names(search(window, "mascota")) == ["mascota.png"]


def test_menus_offer_their_options(window):
    panel = window.find_panel
    menu = panel.roots_button.menu()
    menu.aboutToShow.emit()
    texts = [action.text() for action in menu.actions()]
    assert "Seguir al panel activo" in texts and "Añadir todas las unidades" in texts
    menu = panel.excludes_button.menu()
    menu.aboutToShow.emit()
    assert "Excluir las carpetas ocultas y del sistema" in [action.text() for action in menu.actions()]


# ---- filtros en la interfaz ----

def test_date_editor_shows_only_what_it_needs(window):
    editor = window.find_panel.date_filter
    window.run_command("Set UTILITY=Find")
    settle()
    assert editor.first.isHidden() and editor.amount.isHidden()
    editor.op.setCurrentIndex(editor.op.findData("between"))
    assert not editor.first.isHidden() and not editor.second.isHidden() and editor.amount.isHidden()
    editor.op.setCurrentIndex(editor.op.findData("within"))
    assert editor.first.isHidden() and not editor.amount.isHidden() and not editor.unit.isHidden()


def test_filters_reach_the_search(window):
    panel = window.find_panel
    panel.size_filter.op.setCurrentIndex(panel.size_filter.op.findData("greater"))
    panel.size_filter.first.setValue(1)
    panel.size_filter.unit.setCurrentIndex(panel.size_filter.unit.findData("KB"))
    assert names(search(window)) == ["grande.bin"]


def test_date_filter_reaches_the_search(window):
    panel = window.find_panel
    panel.date_filter.op.setCurrentIndex(panel.date_filter.op.findData("after"))
    panel.date_filter.first.setDate(QDate.currentDate().addDays(1))
    assert names(search(window, "nota")) == []


def test_kind_filter(window):
    panel = window.find_panel
    panel.kind_filter.kind.setCurrentIndex(panel.kind_filter.kind.findData("folders"))
    assert names(search(window)) == ["sub"]
    assert panel.kind_filter.content_type.isEnabled() is False
    panel.kind_filter.kind.setCurrentIndex(panel.kind_filter.kind.findData("type"))
    assert panel.kind_filter.content_type.isEnabled()


# ---- modo avanzado ----

def test_advanced_starts_from_what_was_written(window):
    panel = window.find_panel
    panel.name.box.setEditText("mascota")
    panel.content.box.setEditText("aguja")
    panel.advanced.setChecked(True)
    assert panel.modes.currentWidget() is panel.rules
    assert [rule.state()["field"] for rule in panel.rules.rules] == ["name", "content"]
    assert panel.rules.rules[0].editors["name"].box.currentText() == "mascota"


def test_advanced_rules_search(window):
    panel = window.find_panel
    panel.advanced.setChecked(True)
    rules = panel.rules
    first = rules.rules[0]
    first.editors["name"].box.setEditText("mascota")
    second = rules.add_rule()
    second.field.setCurrentIndex(second.field.findData("name"))
    second.join.setCurrentIndex(second.join.findData("or"))
    second.editors["name"].box.setEditText("otro")
    assert names(search(window)) == ["mascota.png", "mascota2.txt", "otro.txt"]
    second.join.setCurrentIndex(second.join.findData("and"))
    second.negate.setCurrentIndex(second.negate.findData(True))
    second.editors["name"].box.setEditText("png")
    assert names(search(window)) == ["mascota2.txt"]


def test_removing_a_rule_leaves_the_first_without_join(window):
    panel = window.find_panel
    panel.advanced.setChecked(True)
    rules = panel.rules
    second = rules.add_rule()
    assert not second.join.isHidden() or not panel.isVisible()
    rules._remove(rules.rules[0])
    assert rules.rules == [second] and second.join.isHidden()


# ---- plantillas y restablecer ----

def test_template_saves_and_restores_everything(window):
    panel = window.find_panel
    panel.name.box.setEditText("guardado")
    panel.archives.setChecked(True)
    panel.size_filter.op.setCurrentIndex(panel.size_filter.op.findData("greater"))
    panel.save_template("Mi plantilla")
    panel.reset_all()
    assert panel.name.box.currentText() == "" and not panel.archives.isChecked() and panel.size_filter.op.currentData() == "ignore"
    panel.templates_menu.aboutToShow.emit()
    assert "Mi plantilla" in [action.text() for action in panel.templates_menu.actions()]
    panel.load_template("Mi plantilla")
    assert panel.name.box.currentText() == "guardado" and panel.archives.isChecked() and panel.size_filter.op.currentData() == "greater"
    panel.delete_template("Mi plantilla")
    assert "Mi plantilla" not in window.config["find_templates"]


def test_partial_resets(window):
    panel = window.find_panel
    panel.name.box.setEditText("x")
    panel.size_filter.op.setCurrentIndex(panel.size_filter.op.findData("greater"))
    panel.reset_texts()
    assert panel.name.box.currentText() == "" and panel.size_filter.op.currentData() == "greater"
    panel.reset_filters()
    assert panel.size_filter.op.currentData() == "ignore"


def test_history_and_options_are_remembered(window, tree):
    search(window, "nota")
    search(window, "otro")
    box = window.find_panel.name.box
    assert [box.itemText(row) for row in range(box.count())][:2] == ["otro", "nota"]
    assert window.config["find_state"]["name"]["text"] == "otro"


def test_templates_can_be_exported(tmp_path):
    import json

    path = tmp_path / "a.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"find_templates": {"x": {}}}}), encoding="utf-8")
    assert settings_io.read_settings(str(path))[0] == {"find_templates": {"x": {}}}


# ---- comando ----

def test_command_only_shown_where_it_is_used(window):
    panel = window.find_panel
    assert panel.command.isHidden() is (os.name == "nt")


def test_command_preview_follows_the_options(window):
    panel = window.find_panel
    panel.show_command(True)
    panel.name.box.setEditText("mascota")
    assert "find " in panel.command.toPlainText() and "*mascota*" in panel.command.toPlainText()
    panel.content.box.setEditText("aguja")
    assert "grep" in panel.command.toPlainText() and "aguja" in panel.command.toPlainText()
    panel.subfolders.setChecked(False)
    assert "-maxdepth" in panel.command.toPlainText()
    panel.size_filter.op.setCurrentIndex(panel.size_filter.op.findData("greater"))
    assert "-size" in panel.command.toPlainText()
    panel.name.flags["regex"].setChecked(True)
    panel.name.box.setEditText("(")
    assert "Patrón" in panel.command.toPlainText()


# ---- los resultados como carpeta ----

def test_results_can_be_copied(window, tree):
    view = search(window, "nota")
    view.select_all()
    window.run_command("Copy")
    assert wait_until(lambda: (tree / "der" / "nota.txt").exists())
    assert (tree / "der" / "nota.txt").read_text(encoding="utf-8") == "con aguja"


def test_deleted_files_leave_the_results(window, tree, monkeypatch):
    view = search(window, "*mascot*")
    (tree / "izq" / "mascota.png").unlink()
    view.refresh()
    assert wait_until(lambda: names(view) == ["mascota2.txt"])
    assert get_results("Resultados de la Búsqueda") == [str(tree / "izq" / "sub" / "mascota2.txt")]


def test_opening_a_folder_found_goes_there(window, tree):
    view = search(window, "sub")
    entry = next(entry for entry in view.model.entries if entry.name == "sub")
    view.open_entry(entry)
    assert wait_until(lambda: view.path == str(tree / "izq" / "sub"))


def test_going_back_returns_to_the_folder_and_forward_to_the_results(window, tree):
    window.find_panel.target.setCurrentIndex(window.find_panel.target.findData("source"))
    view = search(window, "nota")
    results = view.path
    view.back()
    assert wait_until(lambda: view.path == str(tree / "izq") and "otro.txt" in names(view))
    view.forward()
    assert wait_until(lambda: view.path == results and names(view) == ["nota.txt"])


def test_things_that_make_no_sense_there_are_refused(window, tree):
    view = search(window, "nota")
    assert view.parent_path() == ""
    window.run_command("CreateFolder NAME=nueva")
    assert "resultados" in window.statusBar().currentMessage()
    window.run_command("Copy HERE")
    assert "resultados" in window.statusBar().currentMessage()
    assert not (tree / "izq" / "nueva").exists()
    assert not view.model.can_expand


def test_copying_towards_a_pane_with_results_is_refused(window, tree, monkeypatch):
    warnings = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda *a: warnings.append(a[2]))
    panel = window.find_panel
    panel.target.setCurrentIndex(panel.target.findData("dest"))
    search(window, "nota")
    window.source.select_all()
    settle()
    window.run_command("Copy")
    settle(80)
    assert warnings and "resultados" in warnings[0]


def test_files_found_can_be_renamed(window, tree):
    view = search(window, "nota")
    index = view.model.index(0, 0)
    assert view.model.flags(index) & Qt.ItemFlag.ItemIsEditable
    assert view.model.setData(index, "nota_nueva.txt")
    assert (tree / "izq" / "nota_nueva.txt").exists()


def test_it_is_not_remembered_as_a_recent_folder(window):
    search(window, "nota")
    assert not any(is_results(item.get("path", "") if isinstance(item, dict) else str(item)) for item in window.config.get("recent_folders", []) or [])


def test_session_does_not_break_if_results_are_gone(window):
    from directory_commander_tux_tux.ui.main_window import _location_exists

    view = search(window, "nota")
    assert _location_exists(view.path)
    listing._results.clear()
    assert not _location_exists(view.path)


def test_repeating_a_search_reuses_the_tab_of_its_results(window):
    group = window.groups[window.active_side]
    before = group.count()
    search(window, "nota")
    assert group.count() == before + 1 and is_results(group.current.path)
    results = group.current
    group.setCurrentIndex(0)
    search(window, "otro")
    assert group.count() == before + 1 and group.current is results and names(results) == ["otro.txt"]


def test_the_stored_old_default_becomes_a_new_tab(tmp_path):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"config_version": 14, "find_state": {"target": "source"}, "find_window_geometry": "abc"}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["find_state"]["target"] == "source_new" and config["find_window_geometry"] == ""


def test_a_chosen_target_is_kept_after_upgrading(tmp_path):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"config_version": 14, "find_state": {"target": "dest"}}), encoding="utf-8")
    assert Config(str(tmp_path))["find_state"]["target"] == "dest"


def test_every_option_explains_itself(window):
    panel = window.find_panel
    for editor in (panel.name, panel.content):
        assert all(box.toolTip() for box in editor.flags.values())
    assert panel.pattern.placeholderText() and panel.content.box.lineEdit().placeholderText()
    assert panel.name.flags["partial"].toolTip() != panel.content.flags["partial"].toolTip()
    assert "a la vez" in panel.rule_hint.text()
    assert not hasattr(panel, "index")


def test_name_and_text_must_both_match(window):
    panel = window.find_panel
    panel.content.box.setEditText("aguja")
    view = search(window, "nota")
    assert names(view) == ["nota.txt"]
    panel.content.box.setEditText("no aparece en ninguno")
    view = search(window, "nota")
    assert names(view) == []


def test_the_rows_of_the_form_do_not_spread_out(window):
    """Lo que sobra queda abajo: las filas de arriba siguen juntas aunque la ventana sea alta."""
    panel = window.find_panel
    window.config["find_panel_place"] = "window"
    window._place_find_panel()
    window._set_utility("find")
    window.find_window.resize(1320, 900)
    settle(60)
    gap = panel.content.mapTo(panel, panel.content.rect().topLeft()).y() - panel.name.mapTo(panel, panel.name.rect().bottomLeft()).y()
    assert gap < 12
    below = panel.kind_filter.mapTo(panel, panel.kind_filter.rect().topLeft()).y() - panel.rule_hint.mapTo(panel, panel.rule_hint.rect().bottomLeft()).y()
    assert below < 12
    window.find_window.hide()
