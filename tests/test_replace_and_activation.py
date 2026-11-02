"""El archivo que ya existe (qué se puede contestar), activar un panel desde sus pestañas y que se lean los colores de la barra y de las listas."""
import os
import time
import zipfile

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton

from directory_commander_tux_tux.core import fsops, pane_colors
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.core.fsops import Action, Answer, Collision
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.main_window import MainWindow
from directory_commander_tux_tux.ui.replace_dialog import ReplaceDialog

SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
NO_KEYS = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


def settle(ms: int = 50) -> None:
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
def folders(tmp_path):
    origin, target = tmp_path / "origen", tmp_path / "destino"
    origin.mkdir()
    target.mkdir()
    (origin / "a.txt").write_text("nuevo", encoding="utf-8")
    (target / "a.txt").write_text("viejo", encoding="utf-8")
    return origin, target


def copy_answering(origin, target, *answers, move=False):
    pending = list(answers)
    asked = []

    def ask(src, dest):
        asked.append((os.path.basename(src), dest))
        return pending.pop(0)

    result = fsops.transfer([str(origin / "a.txt")], str(target), move, Collision.ASK, ask=ask)
    return result, asked


# ---- contestar ----

def test_without_conflict_nothing_is_asked(tmp_path, folders):
    origin, _target = folders
    other = tmp_path / "vacia"
    other.mkdir()
    result, asked = copy_answering(origin, other)
    assert not asked and result.done == 1 and (other / "a.txt").read_text(encoding="utf-8") == "nuevo"


def test_replace(folders):
    origin, target = folders
    result, asked = copy_answering(origin, target, Answer(Action.REPLACE))
    assert asked == [("a.txt", str(target / "a.txt"))]
    assert (target / "a.txt").read_text(encoding="utf-8") == "nuevo"
    assert result.placed == [(str(origin / "a.txt"), str(target / "a.txt"), False)]  # lo reemplazado no se puede deshacer


def test_rename_new_with_the_chosen_name(folders):
    origin, target = folders
    result, _ = copy_answering(origin, target, Answer(Action.RENAME_NEW, name="otro.txt"))
    assert (target / "a.txt").read_text(encoding="utf-8") == "viejo"
    assert (target / "otro.txt").read_text(encoding="utf-8") == "nuevo"
    assert result.placed == [(str(origin / "a.txt"), str(target / "otro.txt"), True)]


def test_rename_new_without_a_name_finds_a_free_one(folders):
    origin, target = folders
    copy_answering(origin, target, Answer(Action.RENAME_NEW))
    assert (target / "a.txt").read_text(encoding="utf-8") == "viejo"
    assert (target / "a (2).txt").read_text(encoding="utf-8") == "nuevo"


def test_chosen_name_taken_meanwhile_asks_again(folders):
    origin, target = folders
    (target / "ocupado.txt").write_text("otro", encoding="utf-8")
    _, asked = copy_answering(origin, target, Answer(Action.RENAME_NEW, name="ocupado.txt"), Answer(Action.SKIP))
    assert [dest for _src, dest in asked] == [str(target / "a.txt"), str(target / "ocupado.txt")]
    assert (target / "ocupado.txt").read_text(encoding="utf-8") == "otro"


def test_rename_old_moves_the_existing_one_aside(folders):
    origin, target = folders
    result, _ = copy_answering(origin, target, Answer(Action.RENAME_OLD))
    assert (target / "a.txt").read_text(encoding="utf-8") == "nuevo"
    assert (target / "a (2).txt").read_text(encoding="utf-8") == "viejo"
    assert result.renamed == [(str(target / "a.txt"), str(target / "a (2).txt"))]
    assert result.placed[0][2] is True  # el que ocupa ese nombre es nuevo: se puede deshacer


def test_rename_old_gives_the_name_back_if_the_copy_fails(folders, monkeypatch):
    origin, target = folders

    def broken(self, _src, _dest):
        raise OSError("disco lleno")

    monkeypatch.setattr(fsops._Transfer, "_copy_file", broken)
    result, _ = copy_answering(origin, target, Answer(Action.RENAME_OLD))
    assert (target / "a.txt").read_text(encoding="utf-8") == "viejo" and not (target / "a (2).txt").exists()
    assert result.errors and not result.renamed


def test_skip(folders):
    origin, target = folders
    result, _ = copy_answering(origin, target, Answer(Action.SKIP))
    assert result.skipped == 1 and (target / "a.txt").read_text(encoding="utf-8") == "viejo"


def test_cancel_stops_and_touches_nothing(folders):
    origin, target = folders
    result, _ = copy_answering(origin, target, None, move=True)
    assert result.cancelled and (origin / "a.txt").exists() and (target / "a.txt").read_text(encoding="utf-8") == "viejo"


@pytest.mark.parametrize("newer", [True, False])
def test_keep_newer(folders, newer):
    origin, target = folders
    now = time.time()
    os.utime(origin / "a.txt", (now, now + (100 if newer else -100)))
    os.utime(target / "a.txt", (now, now))
    copy_answering(origin, target, Answer(Action.KEEP_NEWER))
    assert (target / "a.txt").read_text(encoding="utf-8") == ("nuevo" if newer else "viejo")


@pytest.fixture
def several(tmp_path):
    origin, target = tmp_path / "o", tmp_path / "d"
    for folder in (origin, target):
        (folder / "sub").mkdir(parents=True)
    for name in ("uno.txt", "dos.txt", "sub/tres.txt"):
        (origin / name).write_text("nuevo", encoding="utf-8")
        (target / name).write_text("viejo", encoding="utf-8")
    return origin, target


def transfer_folder_contents(origin, target, ask):
    return fsops.transfer([str(origin / "uno.txt"), str(origin / "dos.txt"), str(origin / "sub")], str(target), False, Collision.ASK, ask=ask)


def test_each_file_is_asked_also_inside_a_merged_folder(several):
    origin, target = several
    asked = []

    def ask(src, _dest):
        asked.append(os.path.basename(src))
        return Answer(Action.SKIP)

    transfer_folder_contents(origin, target, ask)
    assert asked == ["uno.txt", "dos.txt", "tres.txt"]


@pytest.mark.parametrize("action, first, second", [(Action.REPLACE, "nuevo", "nuevo"), (Action.SKIP, "viejo", "viejo"), (Action.RENAME_NEW, "viejo", "viejo")])
def test_to_all_stops_asking(several, action, first, second):
    origin, target = several
    asked = []

    def ask(src, _dest):
        asked.append(src)
        return Answer(action, True)

    transfer_folder_contents(origin, target, ask)
    assert len(asked) == 1
    assert (target / "uno.txt").read_text(encoding="utf-8") == first and (target / "sub" / "tres.txt").read_text(encoding="utf-8") == second


def test_skip_identical_keeps_asking_for_the_different_ones(several):
    origin, target = several
    moment = time.time() - 1000
    (origin / "uno.txt").write_text("viejo", encoding="utf-8")  # el mismo tamaño y la misma fecha que su pareja
    os.utime(origin / "uno.txt", (moment, moment))
    os.utime(target / "uno.txt", (moment, moment))
    (origin / "dos.txt").write_text("otro tamaño", encoding="utf-8")
    (origin / "sub" / "tres.txt").write_text("también otro", encoding="utf-8")
    asked = []

    def ask(src, _dest):
        asked.append(os.path.basename(src))
        return Answer(Action.SKIP_IDENTICAL) if len(asked) == 1 else Answer(Action.SKIP)

    result = transfer_folder_contents(origin, target, ask)
    assert asked == ["uno.txt", "dos.txt", "tres.txt"]  # uno.txt se pregunta solo una vez: ya no hay que decidir nada sobre él
    assert result.skipped == 3


@pytest.fixture
def zipped(tmp_path):
    path = tmp_path / "c.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("a.txt", "del zip")
    target = tmp_path / "d"
    target.mkdir()
    (target / "a.txt").write_text("viejo", encoding="utf-8")
    return str(path / "a.txt"), target


@pytest.mark.parametrize("answer, expected", [(Answer(Action.SKIP), {"a.txt": "viejo"}),
                                              (Answer(Action.REPLACE), {"a.txt": "del zip"}),
                                              (Answer(Action.RENAME_NEW, name="z.txt"), {"a.txt": "viejo", "z.txt": "del zip"}),
                                              (Answer(Action.RENAME_OLD), {"a.txt": "del zip", "a (2).txt": "viejo"}),
                                              (Answer(Action.KEEP_NEWER), {"a.txt": "viejo"})])
def test_taking_a_file_out_of_an_archive_obeys_the_answer(zipped, answer, expected):
    member, target = zipped
    result = fsops.transfer([member], str(target), False, Collision.ASK, ask=lambda *_: answer)
    assert not result.errors
    assert {item.name: item.read_text(encoding="utf-8") for item in target.iterdir()} == expected


# ---- la ventana que pregunta ----

@pytest.fixture
def dialog(qapp_offscreen, folders):
    origin, target = folders
    shown = ReplaceDialog(str(origin / "a.txt"), str(target / "a.txt"))
    yield shown
    shown.close()


def test_dialog_shows_both_files_and_starts_on_replace(dialog, folders):
    origin, target = folders
    assert dialog.replace_button.text() == "Reemplazar" and dialog.replace_button.isDefault()
    assert dialog.name_edit.text() == "a.txt" and dialog.name_edit.selectedText() == "a"
    tips = {label.toolTip() for label in dialog.findChildren(type(dialog.notice))}
    assert {str(origin), str(target)} <= tips


def test_replace_button_answers(dialog):
    dialog.replace_button.click()
    assert dialog.answer == Answer(Action.REPLACE)


def test_writing_a_name_turns_the_button_into_rename(dialog):
    dialog.name_edit.setText("otro.txt")
    assert dialog.replace_button.text() == "Renombrar" and dialog.replace_button.isEnabled()
    dialog.replace_button.click()
    assert dialog.answer == Answer(Action.RENAME_NEW, False, "otro.txt")


def test_going_back_to_the_same_name_replaces_again(dialog):
    dialog.name_edit.setText("otro.txt")
    dialog.name_edit.setText("a.txt")
    assert dialog.replace_button.text() == "Reemplazar"


def test_unusable_names_cannot_be_chosen(dialog, folders):
    _origin, target = folders
    (target / "ya.txt").write_text("x", encoding="utf-8")
    for name in ("ya.txt", "a/b.txt", ""):
        dialog.name_edit.setText(name)
        assert not dialog.replace_button.isEnabled() and not dialog.notice.isHidden(), name
    dialog.name_edit.setText("libre.txt")
    assert dialog.replace_button.isEnabled() and dialog.notice.isHidden()


def test_cancel_leaves_no_answer(dialog):
    dialog.reject()
    assert dialog.answer is None


def menu_choices(menu):
    return [action.text() for action in menu.actions()]


def test_menus_offer_the_other_ways(dialog):
    assert menu_choices(dialog.replace_menu) == ["Reemplazar todos\tMayús", "Renombrar el nuevo\tCtrl", "Renombrar los nuevos (todos)\tCtrl+Mayús",
                                                 "Renombrar el existente\tAlt+Ctrl", "Renombrar los existentes (todos)\tAlt+Mayús",
                                                 "Conservar el más reciente", "Conservar el más reciente (todos)"]
    assert menu_choices(dialog.skip_menu) == ["Omitir todos\tMayús", "Omitir los idénticos\tCtrl"]
    arrows = [button for button in dialog.findChildren(QToolButton) if button.menu() is not None]
    assert {button.menu() for button in arrows} == {dialog.replace_menu, dialog.skip_menu}


@pytest.mark.parametrize("row, expected", [(0, Answer(Action.REPLACE, True)), (1, Answer(Action.RENAME_NEW)), (2, Answer(Action.RENAME_NEW, True)),
                                           (3, Answer(Action.RENAME_OLD)), (4, Answer(Action.RENAME_OLD, True)),
                                           (5, Answer(Action.KEEP_NEWER)), (6, Answer(Action.KEEP_NEWER, True))])
def test_replace_menu_answers(dialog, row, expected):
    dialog.replace_menu.actions()[row].trigger()
    assert dialog.answer == expected


def test_a_name_written_goes_with_rename_new_but_not_with_all(dialog):
    dialog.name_edit.setText("otro.txt")
    dialog.replace_menu.actions()[1].trigger()
    assert dialog.answer == Answer(Action.RENAME_NEW, False, "otro.txt")
    dialog.replace_menu.actions()[2].trigger()
    assert dialog.answer == Answer(Action.RENAME_NEW, True)


@pytest.mark.parametrize("keys, expected", [(SHIFT, Answer(Action.REPLACE, True)), (CTRL, Answer(Action.RENAME_NEW)), (CTRL | SHIFT, Answer(Action.RENAME_NEW, True)),
                                            (ALT | CTRL, Answer(Action.RENAME_OLD)), (ALT | SHIFT, Answer(Action.RENAME_OLD, True))])
def test_keys_held_on_replace(dialog, monkeypatch, keys, expected):
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: keys))
    dialog.replace_button.click()
    assert dialog.answer == expected


@pytest.mark.parametrize("keys, expected", [(NO_KEYS, Answer(Action.SKIP)), (SHIFT, Answer(Action.SKIP, True)), (CTRL, Answer(Action.SKIP_IDENTICAL))])
def test_keys_held_on_skip(dialog, monkeypatch, keys, expected):
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: keys))
    dialog.skip_button.click()
    assert dialog.answer == expected


def test_dates_say_which_is_newer(qapp_offscreen, folders):
    origin, target = folders
    now = time.time()
    os.utime(origin / "a.txt", (now, now))
    os.utime(target / "a.txt", (now - 500, now - 500))
    shown = ReplaceDialog(str(origin / "a.txt"), str(target / "a.txt"))
    texts = [label.text() for label in shown.findChildren(type(shown.notice))]
    assert any("más reciente" in text for text in texts) and any("más antiguo" in text for text in texts)
    shown.close()


def test_text_thumbnail_reads_other_encodings_and_cut_characters(qapp_offscreen):
    from directory_commander_tux_tux.ui.replace_dialog import _decode

    assert _decode("versión".encode("latin-1") + b" final") == "versión final"
    assert _decode("una línea".encode()) == "una línea"
    assert _decode("cortado é".encode()[:-1]) == "cortado "


def test_member_of_an_archive_can_be_described(qapp_offscreen, zipped):
    member, target = zipped
    shown = ReplaceDialog(member, str(target / "a.txt"))
    assert shown.name_edit.text() == "a.txt"
    shown.close()


# ---- en la ventana principal ----

@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "config"))
    left, right = tmp_path / "izq", tmp_path / "der"
    left.mkdir()
    right.mkdir()
    (left / "a.txt").write_text("nuevo", encoding="utf-8")
    (right / "a.txt").write_text("viejo", encoding="utf-8")
    main = MainWindow(Config(), [str(left), str(right)])
    main.resize(1000, 500)
    main.show()
    assert wait_until(lambda: main.source.model.rowCount() == 1 and main.dest.model.rowCount() == 1)
    yield main
    main.jobs.cancel_all()
    main.close()


def answer_the_dialog(choose) -> None:
    """Contesta a la ventana de verdad en cuanto aparece."""

    def attempt():
        shown = QApplication.activeModalWidget()
        if isinstance(shown, ReplaceDialog):
            choose(shown)
        else:
            QTimer.singleShot(20, attempt)

    QTimer.singleShot(20, attempt)


def copy_selected(main) -> None:
    main.source.select_all()
    main.run_command("Copy")


def test_copy_asks_with_the_real_dialog_and_renames(window, tmp_path):
    def choose(shown):
        shown.name_edit.setText("otro.txt")
        shown.replace_button.click()

    answer_the_dialog(choose)
    copy_selected(window)
    assert wait_until(lambda: (tmp_path / "der" / "otro.txt").exists() and window.jobs.running == 0)
    assert (tmp_path / "der" / "a.txt").read_text(encoding="utf-8") == "viejo"
    assert (tmp_path / "der" / "otro.txt").read_text(encoding="utf-8") == "nuevo"


def test_cancelling_the_dialog_stops_the_move(window, tmp_path):
    answer_the_dialog(lambda shown: shown.reject())
    window.source.select_all()
    window.run_command("Copy MOVE")
    settle(150)
    assert wait_until(lambda: window.jobs.running == 0)
    assert (tmp_path / "izq" / "a.txt").exists() and (tmp_path / "der" / "a.txt").read_text(encoding="utf-8") == "viejo"


def test_undo_gives_the_existing_file_its_name_back(window, tmp_path, monkeypatch):
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.move_to_trash", lambda path: (os.unlink(path), True)[1])
    answer_the_dialog(lambda shown: shown.replace_menu.actions()[3].trigger())
    copy_selected(window)
    moved = tmp_path / "der" / "a (2).txt"
    assert wait_until(lambda: moved.exists() and window.jobs.running == 0)
    assert (tmp_path / "der" / "a.txt").read_text(encoding="utf-8") == "nuevo"
    window.run_command("Undo")
    assert wait_until(lambda: not moved.exists() and window.jobs.running == 0)
    assert (tmp_path / "der" / "a.txt").read_text(encoding="utf-8") == "viejo"


def test_cancelling_a_waiting_copy_does_not_leave_the_thread_blocked(window, tmp_path, monkeypatch):
    """Si se cancela mientras espera la respuesta, el hilo termina aunque nadie conteste."""
    monkeypatch.setattr(window, "_ask_collision", lambda *_: (window.jobs.cancel_all(), settle(120))[1])
    copy_selected(window)
    assert wait_until(lambda: window.jobs.running == 0)
    assert (tmp_path / "der" / "a.txt").read_text(encoding="utf-8") == "viejo"


# ---- activar el panel desde su barra de pestañas ----

def click(widget, position: QPoint) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
    settle()


def test_click_on_the_active_tab_activates_the_pane(window):
    assert window.active_side == 0
    bar = window.groups[1].tabBar()
    click(bar, bar.tabRect(bar.currentIndex()).center())
    assert window.active_side == 1
    assert window.groups[1].current.property("role") == "source" and window.groups[0].current.property("role") == "dest"


def test_click_on_the_empty_part_of_the_tab_row_activates_the_pane(window):
    group = window.groups[1]
    bar = group.tabBar()
    position = QPoint(group.width() - 5, bar.height() // 2)
    assert bar.index_at(bar.mapFrom(group, position)) < 0
    click(group.childAt(position) or group, bar.mapFrom(group, position))
    assert window.active_side == 1


def test_click_on_the_group_outside_its_bar_activates_the_pane(window):
    group = window.groups[1]
    group.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert window.active_side == 1


def test_click_on_the_empty_part_of_several_rows_activates_the_pane(window):
    group = window.groups[1]
    bar = group.tabBar()
    bar.settings = {**bar.settings, "multi_row": True}
    group.updateGeometry()
    settle()
    assert bar.index_at(QPoint(bar.width() - 2, 2)) < 0
    click(bar, QPoint(bar.width() - 2, 2))
    assert window.active_side == 1


def test_keyboard_follows_the_pane_activated_from_its_tab(window):
    bar = window.groups[1].tabBar()
    window.activateWindow()
    click(bar, bar.tabRect(bar.currentIndex()).center())
    assert window.focusWidget() is window.groups[1].current.current_view or QApplication.focusWidget() is window.groups[1].current.current_view


def test_click_on_the_tab_keeps_what_clicking_the_selected_tab_does(window):
    group = window.groups[1]
    group.settings = {**group.settings, "selected_click": "rename"}
    renamed = []
    group.rename_tab = renamed.append
    bar = group.tabBar()
    click(bar, bar.tabRect(bar.currentIndex()).center())
    assert wait_until(lambda: renamed == [bar.currentIndex()])


def test_click_on_the_path_activates_the_pane(window):
    breadcrumb = window.groups[1].current.breadcrumb
    click(breadcrumb, QPoint(breadcrumb.width() - 3, breadcrumb.height() // 2))
    assert window.active_side == 1


# ---- colores que se lean ----

@pytest.mark.parametrize("name", ["light", "dark"])
@pytest.mark.parametrize("customs", [{}, {"source": "#fff8d0", "dest": "#102030", "other": "#ff00aa"}], ids=["de serie", "propios"])
def test_pane_bar_can_be_read_with_any_color(name, customs):
    colored = theme.with_pane_colors(theme.THEMES[name], customs)
    for role in pane_colors.ROLES:
        assert theme.contrast(getattr(colored, f"bar_{role}"), theme.bar_icon_color(colored, role)) >= 3, role
        assert theme.contrast(theme.crumb_color(colored, role), theme.crumb_text_color(colored, role)) >= 4.5, role


def test_light_theme_has_a_strong_bar_and_a_pale_path_if_chosen():
    from dataclasses import replace

    colored = replace(theme.with_pane_colors(theme.LIGHT, {"source": "#009800", "dest": "#e86800"}), pale_path=True)
    assert theme.crumb_color(colored, "source") == "#b8e2b8" and theme.crumb_color(colored, "dest") == "#f9d5b8"
    assert theme.bar_icon_color(colored, "source") == "#ffffff" and theme.bar_icon_color(colored, "dest") == "#ffffff"


def test_dark_theme_keeps_one_color_in_the_whole_bar():
    colored = theme.with_pane_colors(theme.DARK, {"source": "#1f4a38"})
    assert theme.crumb_color(colored, "source") == "#1f4a38"


def test_pane_without_color_stays_neutral():
    assert theme.crumb_color(theme.LIGHT, "other") == theme.LIGHT.window
    assert theme.bar_icon_color(theme.LIGHT, "single") == theme.LIGHT.icon


def pixel_colors(window, widget) -> set[str]:
    image = widget.grab().toImage()
    return {image.pixelColor(x, y).name() for x in range(0, image.width()) for y in range(0, image.height())}


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_bar_icons_are_drawn_for_the_bar_of_each_role(window, mode):
    window.config["theme"] = mode
    window.config["pane_color_scheme"] = "green-amber"
    window._preferences_changed()
    settle()
    current = theme.current()
    for view in (window.groups[0].current, window.groups[1].current):
        role = view.property("role")
        tool = next(button for button, icon in view._tool_buttons if icon == "goup")
        image = tool.icon().pixmap(16, 16).toImage()
        drawn = {image.pixelColor(x, y).name() for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() == 255}
        assert theme.bar_icon_color(current, role) in drawn


def test_icons_change_when_the_pane_changes_role(window):
    window.config["theme"] = "light"
    window.config["pane_color_scheme"] = "blue-red"
    window._preferences_changed()
    view = window.groups[1].current
    assert view.property("role") == "dest"
    window.set_dual("3-Vertical")
    settle()
    third = window.groups[2].current
    assert third.property("role") == "other"
    tool = next(button for button, icon in third._tool_buttons if icon == "goup")
    image = tool.icon().pixmap(16, 16).toImage()
    drawn = {image.pixelColor(x, y).name() for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() == 255}
    assert theme.LIGHT.icon in drawn  # su barra no tiene color: el blanco no se vería


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_marked_row_of_a_dropdown_can_be_read(qapp_offscreen, mode):
    """La fila marcada de una lista salía con el texto blanco sobre el fondo claro."""
    app = QApplication.instance()
    previous_style, previous_sheet, previous_palette = app.style().objectName(), app.styleSheet(), app.palette()
    app.setStyle("Fusion")
    current = theme.THEMES[mode]
    app.setPalette(theme.palette(current))
    app.setStyleSheet(theme.stylesheet(current))
    combo = QComboBox()
    combo.addItems(["Azul y rojo", "Verde y naranja", "Violeta y turquesa"])
    combo.show()
    try:
        settle()
        combo.showPopup()
        settle()
        view = combo.view()
        index = view.model().index(1, 0)
        view.setCurrentIndex(index)
        settle()
        image = view.viewport().grab(view.visualRect(index)).toImage()
        colors = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}
        counts = {color: 0 for color in colors}
        for x in range(image.width()):
            for y in range(image.height()):
                counts[image.pixelColor(x, y).name()] += 1
        background = max(counts, key=counts.get)
        assert max(theme.contrast(background, color) for color in colors) >= 4.5
        combo.hidePopup()
    finally:
        combo.close()
        app.setStyleSheet(previous_sheet)
        app.setPalette(previous_palette)
        app.setStyle(previous_style)
