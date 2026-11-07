"""La ventana del renombrado masivo, el menú con varios archivos y renombrar uno seleccionando solo su nombre."""
import datetime
import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from directory_commander_tux_tux.core.batch_rename import BatchRule
from directory_commander_tux_tux.ui.batch_rename import BatchRenameDialog
from tests.test_ui import app, labels, select, tree, wait_until, window  # noqa: F401 - se usan como fixtures


@pytest.fixture
def photos(tmp_path):
    for name in ("c.png", "a.png", "b.png"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "otro.txt").write_text("x", encoding="utf-8")
    return tmp_path


def dialog_for(folder, names, profiles=None):
    return BatchRenameDialog(str(folder), names, {"otro.txt"}, {} if profiles is None else profiles)


def new_column(dialog):
    return [dialog.list.topLevelItem(row).text(1) for row in range(len(dialog))]


def test_the_list_shows_each_new_name_in_its_order(app, photos):
    dialog = dialog_for(photos, ["c.png", "a.png", "b.png"])
    assert new_column(dialog) == ["c.png", "a.png", "b.png"] and not dialog.rename_button.isEnabled()
    dialog.name_box.setChecked(True)
    dialog.name["text"].setText("foto_")
    dialog.name["sequence_on"].setChecked(True)
    dialog.name["sequence"].setEditText("001")
    dialog.update_preview()
    assert new_column(dialog) == ["foto_001.png", "foto_002.png", "foto_003.png"] and dialog.rename_button.isEnabled()
    dialog.list.topLevelItem(2).setSelected(True)
    dialog.move_selected(-len(dialog))  # al principio: la secuencia sigue el orden de la lista
    assert dialog.names == ["b.png", "c.png", "a.png"] and new_column(dialog)[0] == "foto_001.png"
    assert dialog.preview.text() == "foto_001.png"  # el seleccionado
    dialog.remove_selected()
    assert dialog.names == ["c.png", "a.png"]


def test_the_options_of_each_rule_turn_on_with_it(app, photos):
    dialog = dialog_for(photos, ["a.png"])
    assert not dialog.name["sequence"].isEnabled() and not dialog.replace_search.isEnabled() and dialog.replace_from.isEnabled()
    dialog.name["sequence_on"].setChecked(True)
    dialog.replace_by_search.setChecked(True)
    assert dialog.name["sequence"].isEnabled() and dialog.replace_search.isEnabled() and not dialog.replace_from.isEnabled()


def test_duplicated_names_and_names_already_taken_are_not_renamed(app, photos, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = dialog_for(photos, ["a.png", "b.png"])
    dialog.name_box.setChecked(True)
    dialog.name["text"].setText("igual")
    dialog.update_preview()
    assert dialog.problems and dialog.list.topLevelItem(0).foreground(1).color() != dialog.palette().text().color()
    dialog.rename_all()
    assert "mismo nombre" in warnings[-1] and (photos / "a.png").exists()
    dialog.list.topLevelItem(1).setSelected(True)
    dialog.remove_selected()
    dialog.name["text"].setText("otro")
    dialog.extension_box.setChecked(True)
    dialog.extension["text"].setText("txt")
    dialog.extension["place"].setCurrentIndex(dialog.extension["place"].findData("replace"))
    dialog.rename_all()
    assert "otro.txt" in warnings[-1] and (photos / "a.png").exists()


def test_renaming_changes_the_files_and_can_go_on_with_the_same_ones(app, photos, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog = dialog_for(photos, ["a.png", "b.png"])
    done = []
    dialog.renamed.connect(done.append)
    dialog.name_box.setChecked(True)
    dialog.name["text"].setText("x_")
    dialog.name["place"].setCurrentIndex(dialog.name["place"].findData("before"))
    dialog.rename_all()
    assert (photos / "x_a.png").exists() and (photos / "x_b.png").exists() and not (photos / "a.png").exists()
    assert done == [[("a.png", "x_a.png"), ("b.png", "x_b.png")]]
    assert dialog.names == ["x_a.png", "x_b.png"] and new_column(dialog) == ["x_x_a.png", "x_x_b.png"]  # sigue con los renombrados


def test_insert_before_the_extension_from_the_advanced_tab(app, photos):
    dialog = dialog_for(photos, ["a.png"])
    dialog.insert_box.setChecked(True)
    dialog.insert_text.setEditText("_copia")
    dialog.insert_at_extension.setChecked(True)
    dialog.update_preview()
    assert new_column(dialog) == ["a_copia.png"] and dialog.rule().insert_place == "before_extension"
    rule = dialog.rule()
    dialog.set_rule(BatchRule())
    dialog.set_rule(rule)
    assert dialog.insert_at_extension.isChecked() and not dialog.insert_position.isEnabled()


def test_variables_are_inserted_where_the_cursor_is_and_read_the_file_date(app, photos):
    dialog = dialog_for(photos, ["a.png"])
    dialog.name_box.setChecked(True)
    dialog._insert_text(dialog.name["text"], "%Y")
    dialog.update_preview()
    year = datetime.datetime.fromtimestamp(os.stat(photos / "a.png").st_mtime).year
    assert new_column(dialog) == [f"{year}.png"]


def test_profiles_are_saved_loaded_and_deleted(app, photos, monkeypatch):
    profiles = {"viejo": {"mode": "simple", "pattern": "a", "replacement": "z", "ignore_ext": True, "case": "none"}}
    dialog = dialog_for(photos, ["a.png"], profiles)
    assert dialog.load_profile("viejo") and new_column(dialog) == ["z.png"]  # de la versión anterior
    dialog.insert_box.setChecked(True)
    dialog.insert_text.setEditText("_v2")
    dialog.insert_at_position.setChecked(True)
    dialog.insert_position.setValue(99)
    dialog.save_profile("nuevo")
    assert profiles["nuevo"]["kind"] == "batch" and profiles["nuevo"]["insert_text"] == "_v2"
    dialog.set_rule(BatchRule())
    assert new_column(dialog) == ["a.png"]
    assert dialog.load_profile("nuevo") and new_column(dialog) == ["z_v2.png"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.delete_profile()
    assert "nuevo" not in profiles and dialog.profile_box.findText("nuevo") < 0


def test_several_selected_files_offer_batch_rename(window, tree):
    view = window.source
    select(view, "a.txt")
    assert "Renombrar" in labels(window.build_file_menu(view))
    select(view, "a.txt", "b.txt")
    found = labels(window.build_file_menu(view))
    assert "Renombrado masivo…" in found and "Renombrar" not in found


def test_batch_rename_from_the_window_can_be_undone(window, tree, monkeypatch):
    left, _ = tree
    view = window.source
    select(view, "a.txt", "b.txt")

    def run(dialog):
        dialog.name_box.setChecked(True)
        dialog.name["text"].setText("nuevo_")
        dialog.name["sequence_on"].setChecked(True)
        dialog.name["sequence"].setEditText("1")
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        dialog.rename_all()
        return 1

    monkeypatch.setattr(BatchRenameDialog, "exec", run)
    window.run_command("Rename ADVANCED")
    assert (left / "nuevo_1.txt").exists() and (left / "nuevo_2.txt").exists()
    window.run_command("Undo")
    assert wait_until(lambda: (left / "a.txt").exists() and (left / "b.txt").exists())


def editor_of(view):
    return next(widget for widget in view.current_view.viewport().findChildren(QLineEdit) if widget.isVisible())


@pytest.mark.parametrize("stem_only, expected", [(True, "135651424_p10"), (False, "135651424_p10.jpg")])
def test_renaming_one_selects_its_name_without_the_extension(window, tree, stem_only, expected):
    left, _ = tree
    (left / "135651424_p10.jpg").write_bytes(b"x")
    view = window.source
    view.refresh()
    assert wait_until(lambda: view.model.row_of(str(left / "135651424_p10.jpg")) >= 0)
    window.config["rename_selects_stem"] = stem_only
    view.mark_path(str(left / "135651424_p10.jpg"))
    window.run_command("Rename")
    QApplication.processEvents()
    assert editor_of(view).selectedText() == expected


def test_a_folder_selects_its_whole_name_and_what_is_typed_is_kept(window, tree):
    left, _ = tree
    view = window.source
    view.mark_path(str(left / "subcarpeta"))
    window.run_command("Rename")
    QApplication.processEvents()
    editor = editor_of(view)
    assert editor.selectedText() == "subcarpeta"
    editor.setText("escrito")
    model = view.model
    model.dataChanged.emit(model.index(0, 0), model.index(model.rowCount() - 1, 0), [Qt.ItemDataRole.DisplayRole])  # como al llegar una marca
    QApplication.processEvents()
    assert editor.text() == "escrito"
