"""Idiomas: la mecánica, que el catálogo de inglés esté completo y que la interfaz real se vea traducida."""
import ast
import json
import os
import re
import string
import subprocess
import sys

import pytest

from directory_commander_tux_tux.core import context_menu, i18n
from directory_commander_tux_tux.core.i18n import N_, tr
from tests.test_ui import app, tree, window  # noqa: F401 - se usan como fixtures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "directory_commander_tux_tux")


@pytest.fixture(autouse=True)
def back_to_spanish():
    yield
    i18n.set_strings(i18n.SOURCE_CODE, {})


def marked_texts() -> dict[str, str]:
    """Los textos que el código marca con tr o N_, y dónde está cada uno."""
    found: dict[str, str] = {}
    for folder, _dirs, files in os.walk(PACKAGE):
        for name in files:
            if name.endswith(".py") and name != "i18n.py":
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as source:
                    tree = ast.parse(source.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("tr", "N_") and node.args and isinstance(node.args[0], ast.Constant):
                        found.setdefault(node.args[0].value, f"{name}:{node.lineno}")
    return found


def english() -> dict:
    with open(os.path.join(PACKAGE, "languages", "en.json"), encoding="utf-8") as source:
        return json.load(source)


def placeholders(text: str) -> set[str]:
    return {field for _literal, field, _spec, _conversion in string.Formatter().parse(text) if field is not None}


def test_without_a_language_the_text_is_the_key():
    assert tr("Abrir") == "Abrir" and tr("No existe: {src}", src="x") == "No existe: x" and tr("Texto sin traducción") == "Texto sin traducción"
    assert N_("Abrir") == "Abrir"


def test_translation_is_used_and_filled():
    i18n.set_strings("xx", {"Abrir": "Ouvrir", "No existe: {src}": "Introuvable : {src}"})
    assert tr("Abrir") == "Ouvrir" and tr("No existe: {src}", src="a.txt") == "Introuvable : a.txt"
    assert tr("Lo que no está") == "Lo que no está"
    assert N_("Abrir") == "Abrir"  # lo marcado con N_ sigue siendo la clave


def test_tr_accepts_any_placeholder_name():
    assert tr("{text} y {self}", text="a", self="b") == "a y b"


@pytest.mark.parametrize("bad", ["{otra}", "{src", "{0}"])
def test_badly_written_translation_does_not_break(bad):
    i18n.set_strings("xx", {"No existe: {src}": bad})
    assert tr("No existe: {src}", src="a") == "No existe: a"


def write_language(folder, file_name, **data):
    folder.mkdir(exist_ok=True)
    (folder / file_name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_user_folder_adds_languages_and_overrides_builtin(tmp_path):
    folder = tmp_path / "languages"
    write_language(folder, "fr.json", name="Français", code="fr", strings={"Abrir": "Ouvrir"})
    write_language(folder, "mio.json", name="English mine", code="en", strings={"Abrir": "Open it"})
    found = i18n.available_languages(str(folder))
    assert found["fr"]["name"] == "Français" and found["en"]["name"] == "English mine" and found[i18n.SOURCE_CODE]["name"] == i18n.SOURCE_NAME
    i18n.set_language("en", str(folder))
    assert tr("Abrir") == "Open it" and tr("Cerrar") == "Cerrar"  # solo lo que trae su archivo
    assert i18n.current_language() == "en"


def test_builtin_languages_without_user_folder():
    found = i18n.available_languages("")
    assert {"es", "en"} <= set(found) and found["en"]["name"] == "English"
    i18n.set_language("en")
    assert tr("Abrir") == "Open"


@pytest.mark.parametrize("content", ["no es json", "[]", '{"name": "x"}', '{"strings": []}', '{"code": "auto", "strings": {}}', '{"code": 5, "strings": {}}'])
def test_bad_language_files_are_ignored(tmp_path, content):
    (tmp_path / "malo.json").write_text(content, encoding="utf-8")
    found = i18n.available_languages(str(tmp_path))
    assert set(found) == set(i18n.available_languages(""))


def test_bad_entries_inside_a_language_are_ignored(tmp_path):
    write_language(tmp_path, "xx.json", name="X", code="xx", strings={"Abrir": 5, "Cerrar": "", "Pegar": "Coller"})
    assert i18n.available_languages(str(tmp_path))["xx"]["strings"] == {"Pegar": "Coller"}


def test_file_name_is_the_code_if_it_has_none(tmp_path):
    write_language(tmp_path, "pt.json", strings={"Abrir": "Abrir!"})
    found = i18n.available_languages(str(tmp_path))
    assert found["pt"]["name"] == "pt"


def test_unknown_language_falls_back_to_the_source():
    i18n.set_language("no-existe")
    assert i18n.current_language() == i18n.SOURCE_CODE and tr("Abrir") == "Abrir"


@pytest.mark.parametrize("preference,system,expected", [
    ("en", "es_MX", "en"), ("es", "en_US", "es"), ("auto", "es_MX", "es"), ("auto", "en_GB", "en"), ("auto", "fr_FR", "en"), ("xx", "es_ES", "es"),
    ("auto", "pt_BR", "pt_BR"), ("auto", "pt_PT", "en"),
])
def test_resolve(preference, system, expected):
    assert i18n.resolve(preference, system, {"es": {}, "en": {}, "pt_BR": {}}) == expected


def test_resolve_without_english_uses_the_source():
    assert i18n.resolve("auto", "fr_FR", {"es": {}}) == "es"


def test_template_has_every_text_and_is_not_a_language_until_named(tmp_path):
    path = i18n.write_template(str(tmp_path / "languages"))
    with open(path, encoding="utf-8") as source:
        data = json.load(source)
    assert set(data["strings"]) == set(english()["strings"]) and not any(data["strings"].values())
    assert i18n.write_template(str(tmp_path / "languages")) != path  # no pisa la anterior


def test_filled_template_appears_as_a_language(tmp_path):
    path = i18n.write_template(str(tmp_path))
    with open(path, encoding="utf-8") as source:
        data = json.load(source)
    assert "xx" in i18n.available_languages(str(tmp_path))  # con el código de ejemplo, ya se puede elegir para probarla
    data.update(name="Français", code="fr")
    data["strings"]["Abrir"] = "Ouvrir"
    with open(path, "w", encoding="utf-8") as out:
        json.dump(data, out, ensure_ascii=False)
    assert i18n.available_languages(str(tmp_path))["fr"]["strings"] == {"Abrir": "Ouvrir"}


def test_english_catalog_matches_the_code():
    marked = marked_texts()
    strings = english()["strings"]
    missing = {text: where for text, where in marked.items() if text not in strings}
    assert not missing, f"Textos sin traducir al inglés: {missing}"
    assert not sorted(set(strings) - set(marked)), "Traducciones de textos que ya no existen"


def test_english_keeps_the_placeholders():
    wrong = {text: value for text, value in english()["strings"].items() if placeholders(text) != placeholders(value)}
    assert not wrong


def test_open_with_of_windows_takes_the_language_of_the_program(app):
    from PySide6.QtWidgets import QMenu
    from directory_commander_tux_tux.core.windows_shell import ShellItem
    from directory_commander_tux_tux.ui.shell_menu import add_shell_items

    class Shell:
        def items(self):
            return [ShellItem(label="A&brir con", verb="openas", submenu=60, command=8)]

    def shown(language):
        i18n.set_strings(*language)
        menu = QMenu()
        place = menu.addAction("x")
        add_shell_items(menu, Shell(), open_with_place=place)
        return [action.text() for action in menu.actions()]

    assert shown(("es", {})) == ["A&brir con"]
    assert shown(("en", {"Abrir con": "Open with"})) == ["Open with"]


def test_commands_are_not_translatable():
    commands = [text for text in marked_texts() if re.match(r"^(Go|Copy|Select|Clipboard|Favorites|Prefs|Image|Delete|Toolbar|Rename|Show|Undo|Help|CLI|Print) [A-Z]{2,}", text)]
    assert not commands


@pytest.mark.parametrize("which", [context_menu.FOLDER, context_menu.FILE])
def test_context_menu_options_stay_different_in_english(which):
    strings = english()["strings"]
    keys = [key for key in context_menu.DEFAULT_LAYOUTS[which] if key != context_menu.SEPARATOR and key != context_menu.SHELL_ITEMS]
    assert len({strings[key] for key in keys}) == len(keys)


def test_context_menu_arranges_by_key_in_another_language(window, monkeypatch):
    """La configuración guarda las claves en español: ocultar y ordenar debe seguir funcionando con los textos traducidos."""
    strings = english()["strings"]
    i18n.set_strings("en", strings)
    window.config["context_menus"] = {"folder": {"order": ["Actualizar", "---", *context_menu.DEFAULT_LAYOUTS["folder"]], "hidden": ["Pegar"]}}
    menu = window.build_folder_menu(window.source)
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels[0] == "Refresh" and "Paste" not in labels and "New folder" in labels


def test_toolbar_and_styles_are_shown_translated_and_stored_as_keys(window):
    i18n.set_strings("en", english()["strings"])
    stored = window.config["toolbar"][0]["label"]
    window._build_toolbar()
    window._build_style_bar()
    assert window.toolbar.actions()[0].text() == "Back" and stored == "Atrás"
    assert "Commander" in [action.text() for action in window.style_bar.actions()]
    assert "Comandante" in window._style_buttons  # se sigue identificando por su clave


def test_buttons_written_by_the_user_are_shown_as_they_are(window):
    i18n.set_strings("en", english()["strings"])
    window.config["toolbar"] = [{"label": "Mi botón", "commands": ["Go UP"], "children": []}]
    window._build_toolbar()
    assert window.toolbar.actions()[0].text() == "Mi botón"


def test_preference_lists_languages_and_saves_the_choice(app, tmp_path):
    from directory_commander_tux_tux.core.config import Config
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    config = Config(str(tmp_path))
    write_language(tmp_path / "languages", "fr.json", name="Français", code="fr", strings={"Abrir": "Ouvrir"})
    assert config["language"] == i18n.AUTO
    dialog = PreferencesDialog(config)
    codes = [dialog.language.itemData(row) for row in range(dialog.language.count())]
    assert codes[0] == i18n.AUTO and {"es", "en", "fr"} <= set(codes)
    assert dialog.language.currentData() == i18n.AUTO
    dialog.language.setCurrentIndex(dialog.language.findData("fr"))
    dialog.apply_to(config)
    assert config["language"] == "fr"
    assert PreferencesDialog(config).language.currentData() == "fr"


def test_create_translation_button(app, tmp_path, monkeypatch):
    from directory_commander_tux_tux.core.config import Config
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    opened, shown = [], []
    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.QDesktopServices.openUrl", lambda url: opened.append(url.toLocalFile()))
    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.QMessageBox.information", lambda _p, _t, message: shown.append(message))
    config = Config(str(tmp_path))
    dialog = PreferencesDialog(config)
    dialog._create_translation()
    assert os.path.isfile(os.path.join(config.language_folder, "plantilla.json")) and "plantilla.json" in shown[0]
    assert os.path.normpath(opened[0]) == os.path.normpath(config.language_folder)
    dialog._open_language_folder()
    assert len(opened) == 2


def run_preferences(window, monkeypatch, language):
    """Las preferencias se aceptan con el idioma cambiado o no, sin mostrarse."""
    offers = []

    def accepted(dialog):
        if language:
            dialog.language.setCurrentIndex(dialog.language.findData(language))
        return 1

    monkeypatch.setattr("directory_commander_tux_tux.ui.dialogs.PreferencesDialog.exec", accepted)
    monkeypatch.setattr(window, "_offer_restart", lambda: offers.append(1))
    window.run_command("Prefs")
    from tests.test_ui import settle
    settle()
    return offers


def test_changing_language_offers_to_restart(window, monkeypatch):
    assert run_preferences(window, monkeypatch, "en") == [1]
    assert window.config["language"] == "en"


def test_leaving_the_language_alone_does_not_ask(window, monkeypatch):
    assert run_preferences(window, monkeypatch, None) == []


def test_restart_opens_a_new_program_after_closing(window, monkeypatch):
    calls = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QProcess.startDetached", lambda program, arguments: calls.append((program, arguments)))
    monkeypatch.setattr(window, "close", lambda: True)
    window._restart()
    assert calls and calls[0][0] == sys.executable


def test_restart_does_nothing_if_the_window_stays_open(window, monkeypatch):
    calls = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QProcess.startDetached", lambda *a: calls.append(a))
    monkeypatch.setattr(window, "close", lambda: False)
    window._restart()
    assert not calls


def test_refusing_the_restart_keeps_working(window, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda *_a: False)
    monkeypatch.setattr(window, "_restart", lambda: pytest.fail("no debe reiniciar"))
    window._offer_restart()


def test_language_setting_travels_in_the_settings_file(tmp_path):
    from directory_commander_tux_tux.core import settings_io

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"language": "en"}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"language": "en"}, [])


def test_key_tables_stay_in_spanish_when_imported_with_another_language():
    """Lo que se guarda en la configuración no debe depender del idioma con el que se arrancó."""
    code = ("from directory_commander_tux_tux.core import i18n; i18n.set_language('en');"
            "from directory_commander_tux_tux.core import config, context_menu;"
            "print(config.DEFAULT_TOOLBAR[0]['label'], config.DEFAULT_STYLES[3]['name'], context_menu.DEFAULT_LAYOUTS['folder'][0])")
    output = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    assert output.stdout.split() == ["Atrás", "Comandante", "Pegar"]


UI_WALK = r"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
from PySide6.QtCore import QLocale
from directory_commander_tux_tux import app as application
from directory_commander_tux_tux.core import i18n
from directory_commander_tux_tux.core.config import Config
config = Config(sys.argv[1])
config["language"] = "en"
application._choose_language(config)
from PySide6.QtWidgets import QApplication, QAbstractButton, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QMenu, QTabBar, QWidget
from PySide6.QtGui import QAction, QImage, QColor
from directory_commander_tux_tux.ui.main_window import MainWindow
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.image_viewer import ImageViewer
from directory_commander_tux_tux.ui.transfer_dialog import TransferDialog
from directory_commander_tux_tux.core.fsops import ProgressSink, TransferState
qt = QApplication([])
work = sys.argv[2]
window = MainWindow(config, [work, work])
window.resize(1500, 900)
window.show()
qt.processEvents()
texts = {}
def note(text, where):
    if text:
        texts.setdefault(text, where)
def walk(widget, where):
    note(widget.windowTitle(), where + ":title")
    note(widget.toolTip(), where + ":tip")
    for child in [widget, *widget.findChildren(QWidget)]:
        name = type(child).__name__
        note(child.toolTip(), name + ":tip")
        if isinstance(child, (QLabel, QAbstractButton)): note(child.text(), name)
        if isinstance(child, QLineEdit): note(child.placeholderText(), name)
        if isinstance(child, QGroupBox): note(child.title(), name)
        if isinstance(child, QComboBox):
            for i in range(child.count()): note(child.itemText(i), name)
        if isinstance(child, QTabBar):
            for i in range(child.count()): note(child.tabText(i), name)
        if isinstance(child, QMenu): note(child.title(), name)
        for action in child.actions():
            note(action.text(), "action"); note(action.toolTip(), "action:tip")
            if action.menu():
                note(action.menu().title(), "menu")
def walk_menu(menu):
    for action in menu.actions():
        note(action.text(), "menu item")
        if action.menu(): walk_menu(action.menu())
walk(window, "main")
window.source.select_paths([os.path.join(work, "a.txt")])
qt.processEvents()
walk_menu(window.build_file_menu(window.source))
walk_menu(window.build_folder_menu(window.source))
model = window.source.model
for column in range(model.columnCount()):
    note(model.headerData(column, __import__("PySide6.QtCore").QtCore.Qt.Orientation.Horizontal), "header")
dialog = PreferencesDialog(config, window)
walk(dialog, "preferences")
for bar_list in dialog.bar_buttons.lists.values():
    for row in range(bar_list.count()): note(bar_list.item(row).text(), "bars page")
for bar_row in range(dialog.bar_buttons.bar_list.count()): note(dialog.bar_buttons.bar_list.item(bar_row).text(), "bars page")
for editor in (dialog.menus.folder_editor, dialog.menus.file_editor):
    for row in range(editor.list.count()): note(editor.list.item(row).text(), "menus page")
image = QImage(8, 8, QImage.Format.Format_RGB32); image.fill(QColor("red"))
picture = os.path.join(work, "p.png"); image.save(picture)
viewer = ImageViewer([picture], 0, host=window)
walk(viewer, "image viewer")
sink = ProgressSink(); sink.transfer = TransferState(ready=True, total_bytes=10, total_items=1, current_name="a", current_size=10)
walk(TransferDialog(i18n.tr("Copiando"), "a", "b", sink), "transfer")
print(json.dumps(texts, ensure_ascii=False))
os._exit(0)
"""


def test_real_interface_is_in_english(tmp_path):
    """La interfaz real, con el idioma fijado como al arrancar: ningún texto del programa debe quedar en español."""
    config_folder, work = tmp_path / "cfg", tmp_path / "work"
    config_folder.mkdir()
    work.mkdir()
    (work / "a.txt").write_text("x", encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8", DIRECTORY_COMMANDER_TUX_TUX_CONFIG=str(config_folder))
    output = subprocess.run([sys.executable, "-c", UI_WALK, str(config_folder), str(work)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert output.returncode == 0, output.stderr[-2000:]
    shown = json.loads(output.stdout.strip().splitlines()[-1])
    assert len(shown) > 150
    same_in_both = {key for key, value in english()["strings"].items() if key == value}
    left_in_spanish = {text: where for text, where in shown.items()
                       if (text in english()["strings"] and text not in same_in_both) or re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡]", text)}
    left_in_spanish = {text: where for text, where in left_in_spanish.items() if text != i18n.SOURCE_NAME}
    assert not left_in_spanish
