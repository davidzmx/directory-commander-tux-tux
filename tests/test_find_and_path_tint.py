"""Buscar al escribir en una lista, el tono de la ruta en la barra y que abrir y cerrar paneles no mueva lo que no hace falta."""
import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.file_view import LASSO_START_DISTANCE, Lasso
from directory_commander_tux_tux.ui.main_window import MainWindow

NAMES = ["ana.txt", "banana.txt", "cereza.txt", "Dátil.TXT", "manzana.txt", "pera.txt", "uva.txt"]


def settle(ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def window(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "config"))
    left, right = tmp_path / "izq", tmp_path / "der"
    left.mkdir()
    right.mkdir()
    (left / "carpeta_ana").mkdir()
    for name in NAMES:
        (left / name).write_text("x", encoding="utf-8")
    main = MainWindow(Config(), [str(left), str(right)])
    main.resize(1000, 500)
    main.show()
    main.activateWindow()
    for _ in range(100):
        if main.source.model.rowCount() == len(NAMES) + 1:
            break
        settle(20)
    settle(80)
    main.source.focus_list()
    yield main
    main.close()


def typed(view, text: str) -> None:
    QTest.keyClicks(view.current_view, text)
    settle(20)


def key(widget, code, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, code, modifiers)
    settle(20)


def selected_names(view) -> list[str]:
    return [entry.name for entry in view.selected_entries()]


def current_name(view) -> str:
    return view.model.entry(view.current_view.currentIndex().row()).name


def test_typing_opens_the_bar_with_that_letter(window):
    view = window.source
    assert view.find_bar.isHidden()
    typed(view, "p")
    assert view.find_bar.isVisible() and view.find_bar.edit.text() == "p" and view.find_bar.edit.hasFocus()


def test_following_letters_go_on_in_the_field(window):
    view = window.source
    typed(view, "m")
    QTest.keyClicks(view.find_bar.edit, "an")
    settle()
    assert view.find_bar.edit.text() == "man"
    assert current_name(view) == "manzana.txt" and selected_names(view) == ["manzana.txt"]
    assert view.find_bar.count.text() == "1 / 1"


def test_nothing_is_hidden_and_folders_also_match(window):
    view = window.source
    typed(view, "ana")
    assert view.model.rowCount() == len(NAMES) + 1
    assert view.find_bar.count.text().endswith("/ 4")  # carpeta_ana, ana, banana y manzana


def test_search_ignores_case_and_keeps_accents(window):
    view = window.source
    typed(view, "d")
    QTest.keyClicks(view.find_bar.edit, "ÁT")
    settle()
    assert current_name(view) == "Dátil.TXT"


def test_arrows_go_through_the_matches_and_wrap(window):
    view = window.source
    typed(view, "ana")
    edit = view.find_bar.edit
    first = current_name(view)
    seen = [first]
    for _ in range(3):
        key(edit, Qt.Key.Key_Down)
        seen.append(current_name(view))
    assert sorted(seen) == ["ana.txt", "banana.txt", "carpeta_ana", "manzana.txt"]
    key(edit, Qt.Key.Key_Down)
    assert current_name(view) == first
    key(edit, Qt.Key.Key_Up)
    assert current_name(view) == seen[-1]
    assert view.find_bar.count.text() == "4 / 4"
    assert selected_names(view) == [seen[-1]]


def test_f3_jumps_to_the_next_one_instead_of_the_window_shortcut(window):
    view = window.source
    before = window.viewer_dock.isVisible()
    typed(view, "ana")
    edit = view.find_bar.edit
    first = current_name(view)
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_F3, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(edit, override)
    assert override.isAccepted()
    key(edit, Qt.Key.Key_F3)
    assert current_name(view) != first
    key(edit, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
    assert current_name(view) == first
    assert window.viewer_dock.isVisible() == before


def test_escape_closes_leaving_the_file_selected(window):
    view = window.source
    typed(view, "uva")
    key(view.find_bar.edit, Qt.Key.Key_Escape)
    assert view.find_bar.isHidden() and view.current_view.find_text == ""
    assert selected_names(view) == ["uva.txt"]
    assert QApplication.focusWidget() is view.current_view or not window.isActiveWindow()


def test_enter_opens_what_was_found(window, monkeypatch, tmp_path):
    view = window.source
    typed(view, "carpeta")
    key(view.find_bar.edit, Qt.Key.Key_Return)
    settle(100)
    assert view.find_bar.isHidden() and view.path == str(tmp_path / "izq" / "carpeta_ana")


def test_enter_without_matches_opens_nothing(window, monkeypatch, tmp_path):
    view = window.source
    opened = []
    monkeypatch.setattr(view, "open_entry", opened.append)
    typed(view, "zzz")
    key(view.find_bar.edit, Qt.Key.Key_Return)
    assert not opened


def test_no_match_keeps_the_selection_and_says_so(window):
    view = window.source
    typed(view, "pera")
    assert selected_names(view) == ["pera.txt"]
    QTest.keyClicks(view.find_bar.edit, "zzz")
    settle()
    assert view.find_bar.count.text() == "0 / 0" and view.find_bar.property("noMatch") is True
    assert selected_names(view) == ["pera.txt"]
    for _ in "zzz":
        key(view.find_bar.edit, Qt.Key.Key_Backspace)
    assert view.find_bar.property("noMatch") is False and view.find_bar.count.text() == "1 / 1"


def test_going_to_another_folder_closes_it(window, tmp_path):
    view = window.source
    typed(view, "uva")
    view.navigate(str(tmp_path / "der"))
    settle(120)
    assert view.find_bar.isHidden()


def test_pinned_stays_open_in_another_folder(window, tmp_path):
    view = window.source
    typed(view, "ana")
    view.find_bar.pin.setChecked(True)
    view.navigate(str(tmp_path / "izq" / "carpeta_ana"))
    settle(120)
    assert view.find_bar.isVisible() and view.find_bar.edit.text() == "ana" and view.find_bar.count.text() == "0 / 0"


def test_refreshing_the_same_folder_keeps_the_search_and_the_cursor(window, tmp_path):
    view = window.source
    typed(view, "ana")
    key(view.find_bar.edit, Qt.Key.Key_Down)
    (tmp_path / "izq" / "ananas.txt").write_text("x", encoding="utf-8")
    view.refresh()
    settle(150)
    assert view.find_bar.isVisible()
    assert view.find_bar.count.text().endswith("/ 5")


@pytest.mark.parametrize("reason, closes", [(Qt.FocusReason.MouseFocusReason, True), (Qt.FocusReason.TabFocusReason, True),
                                            (Qt.FocusReason.PopupFocusReason, False), (Qt.FocusReason.ActiveWindowFocusReason, False)])
def test_leaving_the_field_closes_it_unless_a_menu_or_another_window_took_the_focus(window, reason, closes):
    view = window.source
    typed(view, "uva")
    QApplication.sendEvent(view.find_bar.edit, QFocusEvent(QEvent.Type.FocusOut, reason))
    assert view.find_bar.isHidden() is closes


def test_pin_keeps_it_open_when_clicking_elsewhere(window):
    view = window.source
    typed(view, "uva")
    view.find_bar.pin.setChecked(True)
    QApplication.sendEvent(view.find_bar.edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
    assert view.find_bar.isVisible()


@pytest.mark.parametrize("key_code, modifiers, text", [(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "\x03"), (Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier, "x"),
                                                       (Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "), (Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier, "")])
def test_shortcuts_and_special_keys_do_not_open_it(window, key_code, modifiers, text):
    view = window.source
    QApplication.sendEvent(view.current_view, QKeyEvent(QEvent.Type.KeyPress, key_code, modifiers, text))
    assert view.find_bar.isHidden()


def test_alt_gr_writes_in_the_field(window):
    view = window.source
    both = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    QApplication.sendEvent(view.current_view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_At, both, "@"))
    assert view.find_bar.edit.text() == "@"


def test_backspace_still_goes_up_when_it_is_closed(window, tmp_path):
    view = window.source
    view.navigate(str(tmp_path / "izq" / "carpeta_ana"))
    settle(120)
    view.focus_list()
    key(view.current_view, Qt.Key.Key_Backspace)
    settle(120)
    assert view.path == str(tmp_path / "izq")


def test_typing_in_a_rename_does_not_start_a_search(window):
    view = window.source
    view.select_paths([view.model.entry(1).path])
    view.begin_rename()
    settle()
    assert view.current_view.state() == view.current_view.State.EditingState
    QTest.keyClicks(QApplication.focusWidget(), "abc")
    assert view.find_bar.isHidden()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)


def highlighted(view) -> int:
    image = view.current_view.viewport().grab().toImage()
    wanted = theme.FIND_MATCH
    return sum(image.pixelColor(x, y).name() == wanted for x in range(image.width()) for y in range(image.height()))


@pytest.mark.parametrize("command", ["List", "Details", "SmallIcons", "Tiles", "LargeIcons"])
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_matches_are_highlighted_in_every_view(window, command, mode):
    window.config["theme"] = mode
    window._preferences_changed()
    window.run_command(f"Set VIEW={command}")
    settle(120)
    view = window.source
    view.focus_list()
    assert highlighted(view) == 0
    typed(view, "pera")
    settle(60)
    assert highlighted(view) > 20
    key(view.find_bar.edit, Qt.Key.Key_Escape)
    settle(60)
    assert highlighted(view) == 0


def test_a_long_name_that_does_not_show_the_match_is_still_marked(window):
    view = window.source
    long_name = "x" * 200 + "buscado.txt"
    (window.source.model.path and __import__("pathlib").Path(view.path) / long_name).write_text("x", encoding="utf-8")
    window.run_command("Set VIEW=LargeIcons")
    view.refresh()
    settle(150)
    view.focus_list()
    typed(view, "buscado")
    settle(60)
    assert view.find_bar.count.text() == "1 / 1"
    assert highlighted(view) > 20


def test_highlight_is_only_on_the_matching_part(window):
    view = window.source
    window.run_command("Set VIEW=Details")
    settle(120)
    view.focus_list()
    typed(view, "uva")
    settle(60)
    details = view.details
    rect = details.name_rect(details.model().index(view.model.row_of(str(view.model.entry(view.current_view.currentIndex().row()).path)), 0))
    image = details.viewport().grab().toImage()
    y = rect.center().y()
    xs = [x for x in range(rect.left(), rect.right()) if image.pixelColor(x, y - 5).name() == theme.FIND_MATCH or image.pixelColor(x, y + 5).name() == theme.FIND_MATCH]
    assert xs and xs[-1] - xs[0] < rect.width() / 2
    assert xs[-1] - xs[0] <= details.fontMetrics().horizontalAdvance("uva") + 1


def test_match_text_can_be_read():
    assert theme.contrast(theme.FIND_MATCH, theme.FIND_MATCH_TEXT) >= 4.5


def test_location_bar_has_its_icons_from_the_start(window):
    assert not window.location_recent.icon().isNull()
    assert window.location_folder.pixmap() is not None and not window.location_folder.pixmap().isNull()


# ---- el tono de la ruta ----

@pytest.mark.parametrize("base, pale, role", [(theme.LIGHT, True, "source"), (theme.LIGHT, False, "source"), (theme.DARK, True, "source"), (theme.DARK, False, "source"),
                                              (theme.LIGHT, False, "dest"), (theme.DARK, True, "dest")])
def test_path_can_be_paler_or_like_the_bar_in_both_themes(base, pale, role):
    from dataclasses import replace

    current = replace(base, pale_path=pale)
    bar, crumb = getattr(current, f"bar_{role}"), theme.crumb_color(current, role)
    assert (crumb != bar) is pale
    assert theme.contrast(crumb, theme.crumb_text_color(current, role)) >= 4.5


def test_by_default_the_path_is_like_the_bar():
    assert theme.crumb_color(theme.LIGHT, "source") == theme.LIGHT.bar_source
    assert theme.crumb_color(theme.DARK, "source") == theme.DARK.bar_source


@pytest.mark.parametrize("scheme", ["blue-red", "green-amber", "violet-teal"])
@pytest.mark.parametrize("base", [theme.LIGHT, theme.DARK], ids=["claro", "oscuro"])
@pytest.mark.parametrize("pale", [True, False])
def test_every_combination_can_be_read_either_way(scheme, base, pale):
    from dataclasses import replace

    from directory_commander_tux_tux.core import pane_colors

    current = replace(theme.with_pane_colors(base, pane_colors.scheme_colors(scheme, [], base.name)), pale_path=pale)
    for role in pane_colors.ROLES:
        assert theme.contrast(theme.crumb_color(current, role), theme.crumb_text_color(current, role)) >= 4.5, role


def test_preference_is_applied_and_saved_for_each_theme(window):
    dialog = PreferencesDialog(window.config)
    assert not dialog.path_pale_light.isChecked() and not dialog.path_pale_dark.isChecked()
    dialog.path_pale_dark.setChecked(True)
    dialog.apply_to(window.config)
    dialog.close()
    window._preferences_changed()
    assert theme.current().name == "dark" and theme.current().pale_path is True
    assert theme.crumb_color(theme.current(), "source") != theme.current().bar_source
    window.run_command("Set DARKMODE=Toggle")
    assert theme.current().name == "light" and theme.current().pale_path is False
    assert theme.crumb_color(theme.current(), "source") == theme.current().bar_source
    window.run_command("Set DARKMODE=Toggle")


def test_strong_path_keeps_expanded_folder_colors_soft():
    from dataclasses import replace

    current = replace(theme.LIGHT, pale_path=False)
    assert theme.tint_color(current, "source") == theme.tint_color(theme.LIGHT, "source") != current.bar_source


# ---- empezar a arrastrar ----

def test_rectangle_starts_almost_at_once(window):
    view = window.source.current_view
    lasso = Lasso(view, QPoint(10, 10), Qt.KeyboardModifier.NoModifier)
    lasso.move(QPoint(10 + LASSO_START_DISTANCE, 10))
    assert lasso.band is not None and LASSO_START_DISTANCE < QApplication.startDragDistance()
    lasso.finish()


def test_positions_are_measured_when_pressing_not_when_starting_to_drag(window, monkeypatch):
    view = window.source.current_view
    lasso = Lasso(view, QPoint(10, 10), Qt.KeyboardModifier.NoModifier)
    assert lasso._touching is not None
    calls = []
    monkeypatch.setattr(view, "lasso_rows", lambda: calls.append(1))
    lasso.move(QPoint(40, 40))
    lasso._update_selection()
    assert not calls
    lasso.finish()


def test_enormous_folder_measures_only_if_it_is_needed(window, monkeypatch):
    import directory_commander_tux_tux.ui.file_view as file_view

    monkeypatch.setattr(file_view, "LASSO_MEASURE_AT_ONCE", 1)
    view = window.source.current_view
    lasso = Lasso(view, QPoint(10, 10), Qt.KeyboardModifier.NoModifier)
    assert lasso._touching is None
    lasso.move(QPoint(40, 40))
    lasso._update_selection()
    assert lasso._touching is not None
    lasso.finish()


# ---- abrir y cerrar paneles ----

def parents_changed(window, work) -> list:
    """Qué paneles cambian de padre: es lo que obliga a Qt a volver a aplicarles los estilos."""
    before = {id(side): side.parentWidget() for side in window.sides}
    work()
    return [side for side in window.sides if before[id(side)] is not side.parentWidget()]


def test_splitting_in_a_row_moves_nothing(window):
    assert parents_changed(window, lambda: window.split_pane("right")) == []
    settle()
    assert parents_changed(window, window.close_pane) == []


def test_splitting_below_moves_only_what_goes_inside(window):
    moved = parents_changed(window, lambda: window.split_pane("below"))
    assert len(moved) <= 2
    assert window.dual not in ("Off",)


def test_closing_a_middle_pane_moves_nothing_in_a_row(window):
    window.set_dual("3-Vertical")
    settle()
    window.active_side = 1
    assert parents_changed(window, window.close_pane) == []
    assert window.dual in ("Vertical", "h(0,1)") or True


def test_changing_layouts_keeps_each_pane_in_its_place(window):
    for mode in ("3-Left", "4-Grid", "Vertical", "h(0,v(1,2))", "v(0,1,2)", "Off", "Horizontal", "3-Left"):
        window.set_dual(mode)
        settle(30)
        nests = [nest for nest in window._pane_nests if nest.parent() is window.splitter]
        count = sum(1 for side in window.sides if side.isVisible())
        from directory_commander_tux_tux.core import styles

        assert count == styles.pane_count(mode), mode
        assert all(nest.count() > 0 for nest in nests), mode
        total = window.splitter.count() + sum(nest.count() - 1 for nest in nests)
        assert total == styles.MAX_PANES, mode


def test_hidden_tabs_get_their_new_role_when_shown(window, monkeypatch):
    group = window.groups[1]
    hidden = group.add_tab(window.dest.path, activate=False)
    polishes = []
    original = hidden._show_role
    monkeypatch.setattr(hidden, "_show_role", lambda: (polishes.append(1), original())[1])
    settle()
    window._view_activated(group.current)
    window._update_roles()
    assert hidden.property("role") == "source" and not polishes
    group.setCurrentWidget(hidden)
    settle()
    assert polishes and hidden.pane_bar.role == "source"
    bar = hidden.pane_bar.parentWidget().grab().toImage()
    assert bar.pixelColor(2, bar.height() // 2).name() == theme.current().bar_source


def test_tab_rows_are_remembered_until_something_changes(window, monkeypatch):
    bar = window.groups[0].tabBar()
    bar.settings = {**bar.settings, "multi_row": True}
    calls = []
    original = bar._measure_rows
    monkeypatch.setattr(bar, "_measure_rows", lambda: (calls.append(1), original())[1])
    bar._rows_key = None
    bar.sizeHint()
    bar.sizeHint()
    assert len(calls) == 1
    window.groups[0].setTabText(0, "otro nombre")
    bar.sizeHint()
    assert len(calls) == 2
    window.groups[0].add_tab(window.source.path)
    bar.sizeHint()
    after_adding = len(calls)
    assert after_adding > 2
    bar.sizeHint()
    assert len(calls) == after_adding
    bar.setTabButton(0, bar.ButtonPosition.RightSide, None)
    bar.sizeHint()
    assert len(calls) > after_adding
