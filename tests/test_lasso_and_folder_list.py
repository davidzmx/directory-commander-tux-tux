"""Seleccionar arrastrando con el ratón, la lista de carpetas debajo de la ruta y el contorno de la barra de cada panel."""
import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication, QRubberBand, QStyleOptionViewItem

from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.file_view import SELECT_EVERY_MS, Lasso
from directory_commander_tux_tux.ui.main_window import MainWindow

LEFT = Qt.MouseButton.LeftButton
NO_BUTTON = Qt.MouseButton.NoButton
NO_KEYS = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def settle(ms: int = 40) -> None:
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
    for number in range(60):
        (left / f"archivo{number:02}.txt").write_text("x", encoding="utf-8")
    main = MainWindow(Config(), [str(left), str(right)])
    main.resize(1000, 500)
    main.show()
    end = 200
    while main.source.model.rowCount() != 60 and end:
        settle(20)
        end -= 1
    settle()
    yield main
    main.close()


@pytest.fixture(params=["List", "Details"])
def view(window, request):
    """Lo que se ve al abrir el programa es la lista; los detalles son otra vista distinta."""
    window.run_command(f"Set VIEW={request.param}")
    settle(120)
    shown = window.source.current_view
    assert shown.isVisible()
    return shown


def send(view, kind, position: QPoint, button=NO_BUTTON, buttons=LEFT, keys=NO_KEYS) -> None:
    point = QPointF(position)
    QApplication.sendEvent(view.viewport(), QMouseEvent(kind, point, view.viewport().mapToGlobal(point), button, buttons, keys))


def press(view, position, keys=NO_KEYS):
    send(view, QEvent.Type.MouseButtonPress, position, LEFT, LEFT, keys)


def drag(view, position, keys=NO_KEYS):
    send(view, QEvent.Type.MouseMove, position, NO_BUTTON, LEFT, keys)
    settle(SELECT_EVERY_MS + 15)  # la selección va un poco por detrás del rectángulo, a propósito


def release(view, position, keys=NO_KEYS):
    send(view, QEvent.Type.MouseButtonRelease, position, LEFT, NO_BUTTON, keys)


def selected_rows(view) -> list[int]:
    return sorted({index.row() for index in view.selectionModel().selectedIndexes()})


def beside_name(view, row: int) -> QPoint:
    """Un punto de esa fila a la derecha de su nombre, donde no se está tocando el archivo."""
    rect = view.name_rect(view.model().index(row, 0))
    position = QPoint(rect.right() + 3, rect.center().y())
    assert not view.name_index_at(position).isValid()
    return position


def on_name(view, row: int) -> QPoint:
    return view.name_rect(view.model().index(row, 0)).center()


def band_of(view):
    return view.viewport().findChild(QRubberBand)


def test_dragging_from_outside_the_names_selects_what_it_touches(window, view):
    press(view, beside_name(view, 2))
    drag(view, on_name(view, 2) + QPoint(5, 0))
    drag(view, on_name(view, 6))
    assert selected_rows(view) == [2, 3, 4, 5, 6]
    release(view, on_name(view, 6))
    assert selected_rows(view) == [2, 3, 4, 5, 6]


def test_the_rectangle_is_shown_while_dragging_and_goes_away(window, view):
    press(view, beside_name(view, 1))
    target = on_name(view, 4)
    drag(view, target)
    band = band_of(view)
    assert band is not None and band.isVisible()
    assert band.geometry().adjusted(-1, -1, 1, 1).contains(target), (band.geometry(), target)
    image = view.viewport().grab().toImage()  # se ve de verdad, aunque la lista lleve el resaltado transparente
    assert image.pixelColor(band.geometry().topLeft()).name() != view.viewport().palette().base().color().name()
    release(view, target)
    settle()
    assert band_of(view) is None or not band_of(view).isVisible()


def test_rectangle_follows_at_once_and_selection_does_not_slow_it_down(window, view, monkeypatch):
    press(view, beside_name(view, 1))
    target = on_name(view, 4)
    send(view, QEvent.Type.MouseMove, target, NO_BUTTON, LEFT)
    band = band_of(view)
    assert band.geometry().adjusted(-1, -1, 1, 1).contains(target)  # sin esperar nada
    assert selected_rows(view) == []
    settle(SELECT_EVERY_MS + 15)
    assert selected_rows(view) == [1, 2, 3, 4]


def test_each_position_is_measured_only_once(window, monkeypatch):
    window.run_command("Set VIEW=List")
    settle(120)
    view = window.source.thumbs
    press(view, beside_name(view, 1))
    drag(view, on_name(view, 4))
    target = on_name(view, 8)
    calls = []
    original = view.name_rect
    monkeypatch.setattr(view, "name_rect", lambda index: (calls.append(1), original(index))[1])
    drag(view, target)
    assert not calls and selected_rows(view) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_releasing_before_the_timer_keeps_what_was_drawn(window, view):
    press(view, beside_name(view, 1))
    target = on_name(view, 5)
    send(view, QEvent.Type.MouseMove, target, NO_BUTTON, LEFT)
    release(view, target)
    assert selected_rows(view) == [1, 2, 3, 4, 5]


def test_going_back_unselects_what_is_no_longer_touched(window, view):
    press(view, beside_name(view, 2))
    drag(view, on_name(view, 8))
    assert selected_rows(view) == [2, 3, 4, 5, 6, 7, 8]
    drag(view, on_name(view, 3))
    assert selected_rows(view) == [2, 3]


def test_dragging_upwards_also_works(window, view):
    press(view, beside_name(view, 6))
    drag(view, on_name(view, 3))
    assert selected_rows(view) == [3, 4, 5, 6]


def test_a_rectangle_that_touches_no_name_selects_nothing(window, view):
    press(view, beside_name(view, 1))
    drag(view, beside_name(view, 5))
    assert selected_rows(view) == []


def test_full_row_selection_counts_the_whole_row(window):
    window.run_command("Set VIEW=Details")
    settle(120)
    view = window.source.details
    start = view.visualRect(view.model().index(1, 1)).center()
    press(view, start)  # todavía sin fila completa, ahí no hay archivo
    view.full_row = True
    drag(view, view.visualRect(view.model().index(3, 1)).center())
    assert selected_rows(view) == [1, 2, 3]


def test_a_simple_click_outside_still_clears_the_selection(window, view):
    window.source.select_all()
    press(view, beside_name(view, 1))
    assert selected_rows(view) == []
    release(view, beside_name(view, 1))
    assert band_of(view) is None or not band_of(view).isVisible()


def test_shift_adds_to_what_was_selected(window, view):
    window.source.select_paths([window.source.model.entry(0).path])
    press(view, beside_name(view, 3), SHIFT)
    drag(view, on_name(view, 5), SHIFT)
    assert selected_rows(view) == [0, 3, 4, 5]


def test_ctrl_inverts_what_it_touches(window, view):
    window.source.select_paths([window.source.model.entry(row).path for row in (0, 4)])
    press(view, beside_name(view, 3), CTRL)
    drag(view, on_name(view, 5), CTRL)
    assert selected_rows(view) == [0, 3, 5]


def test_ctrl_unselects_a_file_clicked_before(window, view):
    press(view, on_name(view, 2))  # la lista selecciona por sí misma: en la vista de lista, solo una columna
    release(view, on_name(view, 2))
    assert selected_rows(view) == [2]
    press(view, beside_name(view, 1), CTRL)
    drag(view, on_name(view, 3), CTRL)
    assert selected_rows(view) == [1, 3]


def test_dragging_a_name_still_moves_the_file_instead(window, view, monkeypatch):
    started = []
    monkeypatch.setattr(view, "startDrag", lambda actions: started.append(actions))
    press(view, on_name(view, 2))
    assert window.source._lasso is None
    drag(view, on_name(view, 8))
    release(view, on_name(view, 8))
    assert band_of(view) is None or not band_of(view).isVisible()
    assert selected_rows(view) == [2] and started


def test_leaving_the_details_scrolls_and_keeps_selecting(window):
    window.run_command("Set VIEW=Details")
    settle(120)
    view = window.source.details
    assert view.verticalScrollBar().maximum() > 0
    press(view, beside_name(view, 1))
    outside = QPoint(on_name(view, 1).x(), view.viewport().rect().bottom() + 30)
    drag(view, outside)
    before = selected_rows(view)[-1]
    settle(300)
    assert view.verticalScrollBar().value() > 0
    assert selected_rows(view)[-1] > before and selected_rows(view)[0] == 1
    release(view, outside)


def test_leaving_the_list_scrolls_sideways_and_keeps_selecting(window):
    window.run_command("Set VIEW=List")
    settle(120)
    window.resize(500, 300)
    settle(120)
    view = window.source.thumbs
    assert view.horizontalScrollBar().maximum() > 0
    press(view, beside_name(view, 0))
    outside = QPoint(view.viewport().rect().right() + 30, view.viewport().rect().bottom())
    drag(view, outside)
    before = selected_rows(view)[-1]
    settle(400)
    assert view.horizontalScrollBar().value() > 0
    assert selected_rows(view)[-1] > before
    release(view, outside)


def test_rectangle_stays_anchored_when_scrolled(window):
    window.run_command("Set VIEW=Details")
    settle(120)
    view = window.source.details
    press(view, beside_name(view, 1))
    drag(view, on_name(view, 2))
    view.verticalScrollBar().setValue(10)
    settle()
    drag(view, on_name(view, 12))
    assert selected_rows(view) == list(range(1, 13))


def test_keyboard_continues_from_the_last_row_touched(window, view):
    press(view, beside_name(view, 1))
    drag(view, on_name(view, 4))
    release(view, on_name(view, 4))
    assert view.currentIndex().row() == 4


def test_releasing_outside_the_window_is_noticed(window, view):
    press(view, beside_name(view, 1))
    drag(view, on_name(view, 3))
    send(view, QEvent.Type.MouseMove, on_name(view, 6), NO_BUTTON, NO_BUTTON)
    assert window.source._lasso is None
    assert selected_rows(view) == [1, 2, 3]


@pytest.mark.parametrize("mode", ["LargeIcons", "Thumbnails", "Tiles"])
def test_views_with_icons_select_by_dragging_too(window, mode):
    window.run_command(f"Set VIEW={mode}")
    settle(150)
    view = window.source.current_view
    assert view is window.source.thumbs
    first = view.visualRect(view.model().index(0, 0))
    start = first.topLeft() - QPoint(1, 1) if first.left() > 0 and first.top() > 0 else QPoint(view.viewport().width() - 2, 1)
    assert not view.name_index_at(start).isValid()
    press(view, start)
    drag(view, view.visualRect(view.model().index(1, 0)).center())
    assert 1 in selected_rows(view)
    release(view, view.visualRect(view.model().index(1, 0)).center())


def test_selected_rows_in_the_list_can_be_a_different_column(window):
    window.run_command("Set VIEW=List")
    settle(120)
    window.resize(600, 300)
    settle(120)
    view = window.source.thumbs
    first, other = view.model().index(0, 0), None
    for row in range(view.model().rowCount()):
        if view.visualRect(view.model().index(row, 0)).left() > view.visualRect(first).left():
            other = row
            break
    assert other is not None
    press(view, beside_name(view, 0))
    drag(view, on_name(view, other))
    assert other in selected_rows(view)


def test_lasso_ignores_group_titles(window):
    window.run_command("Set VIEW=Details")
    settle(120)
    view = window.source.details
    rows = view.rows_touching(view.name_rect(view.model().index(0, 0)).united(view.name_rect(view.model().index(2, 0))))
    assert rows == [0, 1, 2]
    original = view.model().entry
    view.model().entry = lambda row: None if row == 1 else original(row)
    try:
        assert view.rows_touching(view.name_rect(view.model().index(0, 0)).united(view.name_rect(view.model().index(2, 0)))) == [0, 2]
    finally:
        del view.model().entry


def test_rows_above_and_below_the_visible_part_count(window):
    window.run_command("Set VIEW=Details")
    settle(120)
    view = window.source.details
    x = on_name(view, 0).x()
    from PySide6.QtCore import QRect

    assert view.rows_touching(QRect(x, -500, 5, 490)) == []
    assert view.rows_touching(QRect(x, -500, 5, 510))[0] == 0
    assert view.rows_touching(QRect(x, 0, 5, 100000))[-1] == 59
    assert view.rows_touching(QRect(x, 100000, 5, 5)) == []


def test_lasso_class_needs_some_distance(window, view):
    lasso = Lasso(view, QPoint(10, 10), NO_KEYS)
    lasso.move(QPoint(11, 11))
    assert lasso.band is None
    lasso.finish()


def test_list_measures_each_name_once_and_notices_what_changes_its_size(window, monkeypatch):
    window.run_command("Set VIEW=List")
    settle(120)
    view = window.source.thumbs
    delegate = view._plain
    measured = len(delegate._sizes)
    assert measured >= 60
    index = view.model().index(0, 0)
    option = QStyleOptionViewItem()
    view.initViewItemOption(option)
    first = delegate.sizeHint(option, index)
    assert len(delegate._sizes) == measured and first == delegate.sizeHint(option, index)
    window.source.set_check_mode(True)  # la casilla ocupa sitio
    settle(60)
    assert delegate.sizeHint(option, index).width() > first.width()
    window.source.set_check_mode(False)
    entry = view.model().entry(0)
    original = entry.display_name
    try:
        entry.display_name = original + "_mucho_más_largo"
        view.model().dataChanged.emit(index, index)
        assert delegate.sizeHint(option, index).width() > first.width()
    finally:
        entry.display_name = original


# ---- la lista de carpetas ----

def open_list(view):
    menu = view._history_menu
    menu.popup(view.history_button.mapToGlobal(QPoint(0, view.history_button.height())))
    settle(60)
    return menu


@pytest.fixture
def visited(window, tmp_path):
    view = window.source
    for name in ("uno", "dos"):
        (tmp_path / name).mkdir()
        view.navigate(str(tmp_path / name))
        settle(60)
    return view


def test_folder_list_opens_below_the_path_with_its_width(visited):
    menu = open_list(visited)
    strip = visited.crumb_strip
    if strip.width() >= visited.folder_list.MIN_WIDTH:
        assert menu.width() == strip.width()
    assert menu.pos() == strip.mapToGlobal(QPoint(0, strip.height()))
    menu.close()


def test_narrow_pane_keeps_a_readable_list(visited, monkeypatch):
    monkeypatch.setattr(visited.crumb_strip, "width", lambda: 90)
    menu = open_list(visited)
    assert menu.width() == visited.folder_list.MIN_WIDTH
    menu.close()


def test_folder_list_has_icons_and_marks_the_current_folder(visited, tmp_path):
    menu = open_list(visited)
    assert all(not action.icon().isNull() for action in menu.actions())
    colors = menu.grab().toImage()
    row = menu.actionGeometry(menu.activeAction())
    assert colors.pixelColor(row.right() - 3, row.center().y()).name() == theme.current().accent
    assert menu.activeAction().data() == str(tmp_path / "dos")
    assert [action.data() for action in menu.actions()][0] == str(tmp_path / "dos")
    menu.close()


def test_path_turns_into_text_while_the_list_is_open(visited):
    assert not visited.breadcrumb.is_editing()
    menu = open_list(visited)
    assert visited.breadcrumb.is_editing() and visited.crumb_strip.property("fieldShown") is True
    menu.close()
    settle()
    assert not visited.breadcrumb.is_editing() and visited.crumb_strip.property("fieldShown") is False


def test_choosing_from_the_list_goes_there(visited, tmp_path):
    menu = open_list(visited)
    next(action for action in menu.actions() if action.data() == str(tmp_path / "uno")).trigger()
    menu.close()
    settle(60)
    assert visited.path == str(tmp_path / "uno") and not visited.breadcrumb.is_editing()


def test_long_paths_are_shortened_keeping_the_whole_one_in_the_tooltip(visited, tmp_path):
    deep = tmp_path / ("carpeta_de_nombre_largo_" * 8)
    deep.mkdir()
    visited.navigate(str(deep))
    settle(60)
    menu = open_list(visited)
    action = next(item for item in menu.actions() if item.data() == str(deep))
    assert action.text() != str(deep) and "…" in action.text() and action.toolTip() == str(deep)
    menu.close()


def test_location_bar_list_also_opens_below_its_path(window, tmp_path):
    (tmp_path / "uno").mkdir()
    window.source.navigate(str(tmp_path / "uno"))
    settle(60)
    menu = window.location_recent.menu()
    menu.popup(QPoint(0, 0))
    settle(60)
    assert menu.objectName() == "folderList"
    assert menu.pos() == window.location.mapToGlobal(QPoint(0, window.location.height()))
    menu.close()


def test_writing_a_path_still_works(window, tmp_path):
    (tmp_path / "uno").mkdir()
    view = window.source
    view.breadcrumb.begin_edit()
    assert view.crumb_strip.property("fieldShown") is True
    view.breadcrumb._edit.setText(str(tmp_path / "uno"))
    view.breadcrumb._commit()
    settle(60)
    assert view.path == str(tmp_path / "uno") and view.crumb_strip.property("fieldShown") is False


# ---- el contorno ----

def test_outline_colors_are_those_of_the_reference():
    colored = theme.with_pane_colors(theme.LIGHT, {"source": "#009800", "dest": "#e86800"})
    assert theme.pane_outline(colored) == "#a0a0a0"
    assert theme.pane_ring(colored, "source") == "#149914" and theme.pane_ring(colored, "dest") == "#df6f14"
    sheet = theme.stylesheet(colored)
    assert "#008200" in sheet and "#118311" in sheet


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_bar_is_drawn_with_its_two_lines(window, mode):
    window.config["theme"] = mode
    window.config["pane_color_scheme"] = "green-amber"
    window._preferences_changed()
    settle(60)
    current = theme.current()
    view = window.groups[0].current
    assert view.property("role") == "source" and view.pane_bar.role == "source"
    frame = view.pane_bar.parentWidget()
    image = frame.grab().toImage()
    x = 1  # junto al borde izquierdo: ahí no hay botones
    middle = frame.height() // 2
    assert image.pixelColor(0, middle).name() == theme.pane_outline(current)
    assert image.pixelColor(x, middle).name() == theme.pane_ring(current, "source")
    assert image.pixelColor(x + 1, middle).name() == current.bar_source
    assert image.pixelColor(x + 1, 0).name() == theme.pane_outline(current)
    assert image.pixelColor(x + 1, 1).name() == theme.pane_ring(current, "source")


def test_path_covers_the_inner_line_with_its_own(window):
    window.config["theme"] = "light"
    window.config["pane_color_scheme"] = "green-amber"
    window.config["pane_path_pale_light"] = True
    window._preferences_changed()
    settle(60)
    view = window.groups[0].current
    frame = view.pane_bar.parentWidget()
    strip = view.crumb_strip
    image = frame.grab().toImage()
    x = strip.mapTo(frame, QPoint(strip.width() // 2, 0)).x()
    assert image.pixelColor(x, 0).name() == "#a0a0a0"
    assert image.pixelColor(x, 1).name() == "#118311"
    assert image.pixelColor(strip.mapTo(frame, QPoint(0, 0)).x(), frame.height() // 2).name() == "#008200"
    assert frame.height() <= 28
    assert QColor(image.pixelColor(x, 2)).name() == theme.crumb_color(theme.current(), "source")
