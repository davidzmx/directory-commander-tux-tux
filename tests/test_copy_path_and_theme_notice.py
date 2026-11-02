"""El botón de copiar la ruta, poder configurar la barra de cada panel y lo que se ve al cambiar de tema."""
import pytest
from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QLabel

from directory_commander_tux_tux.core import bar_buttons
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.core.listing import COMPUTER_PATH, has_computer_view, location_label
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.bar_buttons_page import BarButtonsPage
from directory_commander_tux_tux.ui.file_view import FileView
from directory_commander_tux_tux.ui.main_window import MainWindow


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
    main = MainWindow(Config(), [str(left), str(right)])
    main.resize(1000, 500)
    main.show()
    settle(80)
    yield main
    main.close()


@pytest.fixture
def copied(monkeypatch):
    """El portapapeles del sistema es de quien usa el equipo: las pruebas no deben pisarlo."""
    texts = []

    class Clipboard:
        setText = staticmethod(texts.append)

    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: Clipboard))
    return texts


def tool(view, key):
    return next(button for button, icon in view._tool_buttons if icon == key)


# ---- copiar la ruta ----

def test_button_copies_the_path_of_its_pane(window, tmp_path, copied):
    other = window.groups[1].current
    tool(other, "copypath").click()
    settle()
    assert copied == [str(tmp_path / "der")]
    assert str(tmp_path / "der") in window.statusBar().currentMessage()


def test_button_sits_next_to_the_folder_list(window):
    view = window.source
    row = view.crumb_strip.layout()
    widgets = [row.itemAt(position).widget() for position in range(row.count())]
    assert widgets == [view.folder_label, view.breadcrumb, view.history_button, tool(view, "copypath")]
    assert tool(view, "copypath").toolTip() == "Copiar la ruta"
    assert not tool(view, "copypath").icon().isNull()


@pytest.mark.skipif(not has_computer_view(), reason="solo donde existe esa vista")
def test_copying_the_computer_view_gives_its_name(window, copied):
    view = window.source
    view.navigate(COMPUTER_PATH)
    settle(120)
    window.run_command("Clipboard COPYPATH")
    assert copied == [location_label(COMPUTER_PATH)]


# ---- la barra de cada panel se puede configurar ----

def test_new_button_is_in_the_preferences_of_the_bar(window):
    page = BarButtonsPage(window.config)
    keys = [page.lists[bar_buttons.PANE].item(row).data(Qt.ItemDataRole.UserRole) for row in range(page.lists[bar_buttons.PANE].count())]
    assert keys.index("copypath") == keys.index("menu-arrow") + 1
    assert {"goback", "goforward", "goup", "refresh", "home", "favorites", "terminalpane", "send", "swap", "close"} <= set(keys)


def test_someone_who_had_reordered_the_bar_also_gets_the_new_button(window):
    window.config["bar_order"] = {"pane": ["home", "goup", "path", "menu-arrow", "goback"]}
    arranged = bar_buttons.arranged(window.config, bar_buttons.PANE)
    assert arranged.index("copypath") == arranged.index("menu-arrow") + 1


def test_hiding_and_reordering_applies_at_once(window):
    window.config["bar_buttons"] = {"pane": ["goforward", "copypath"]}
    window.config["bar_order"] = {"pane": ["home", "goback", "path"]}
    window._apply_bar_buttons()
    settle()
    view = window.source
    assert tool(view, "goforward").isHidden() and tool(view, "copypath").isHidden()
    assert not tool(view, "home").isHidden()
    bar = view.pane_bar.layout()
    widgets = [bar.itemAt(position).widget() for position in range(bar.count())]
    assert widgets[0] is tool(view, "home")  # lo que no se nombra queda tras quien lo precede de serie
    assert widgets.index(tool(view, "home")) < widgets.index(tool(view, "goback")) < widgets.index(view.crumb_strip)


def test_a_tool_away_from_the_path_gets_the_icon_for_the_bar(window):
    window.config["theme"] = "light"
    window.config["bar_order"] = {"pane": ["copypath", "path", "goup"]}
    window._preferences_changed()
    settle()
    view = window.source
    button = tool(view, "copypath")
    assert button.parentWidget() is view.pane_bar
    image = button.icon().pixmap(16, 16).toImage()
    drawn = {image.pixelColor(x, y).name() for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() == 255}
    assert theme.bar_icon_color(theme.current(), view.property("role")) in drawn


@pytest.mark.parametrize("target", ["bar", "button"])
def test_right_click_on_the_bar_offers_to_customize_it(window, monkeypatch, target):
    view = window.source
    shown = []
    monkeypatch.setattr(view, "_pane_bar_menu", lambda position: shown.append(position))
    view.pane_bar.customContextMenuRequested.disconnect()
    view.pane_bar.customContextMenuRequested.connect(view._pane_bar_menu)
    widget = view.pane_bar if target == "bar" else tool(view, "goup")
    QApplication.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(2, 2), widget.mapToGlobal(QPoint(2, 2))))
    assert shown
    assert view.build_pane_bar_menu().actions()[0].text() == "Personalizar…"


def test_right_button_on_the_path_does_not_start_writing(window):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    crumb = window.source.breadcrumb
    point = QPointF(crumb.width() - 3, crumb.height() / 2)
    for button, editing in ((Qt.MouseButton.RightButton, False), (Qt.MouseButton.LeftButton, True)):
        QApplication.sendEvent(crumb, QMouseEvent(QEvent.Type.MouseButtonPress, point, crumb.mapToGlobal(point), button, button, Qt.KeyboardModifier.NoModifier))
        assert crumb.is_editing() is editing


# ---- cambiar de tema ----

def test_each_theme_asks_for_its_color_scheme(window, monkeypatch):
    asked = []
    monkeypatch.setattr(theme, "request_color_scheme", asked.append)
    window.config["theme"] = "dark"
    window.run_command("Set DARKMODE=Toggle")
    assert asked[-1] == "light" and theme.current().name == "light"
    window.run_command("Set DARKMODE=Toggle")
    assert asked[-1] == "dark" and theme.current().name == "dark"


def test_desktop_theme_leaves_the_scheme_to_the_system(window, monkeypatch):
    requested = []
    hints = QApplication.instance().styleHints()

    class Hints:
        colorScheme = staticmethod(lambda: Qt.ColorScheme.Dark)
        setColorScheme = staticmethod(requested.append)

    class App:
        styleHints = staticmethod(lambda: Hints)

    monkeypatch.setattr(theme.QGuiApplication, "instance", staticmethod(lambda: App))
    theme.request_color_scheme("auto")
    assert requested == [Qt.ColorScheme.Unknown]
    requested.clear()
    theme.request_color_scheme("light")
    assert requested == [Qt.ColorScheme.Light]
    requested.clear()
    theme.request_color_scheme("dark")
    assert requested == []  # ya es ese: pedirlo otra vez haría trabajar a todas las ventanas
    assert hints is not None


def spy_on_notice(window, monkeypatch):
    seen = []
    original = window._apply_theme_globals

    def globals_spy():
        notices = [label for label in window.findChildren(QLabel) if label.objectName() == "busyNotice" and label.isVisible()]
        seen.append([label.text() for label in notices])
        original()

    monkeypatch.setattr(window, "_apply_theme_globals", globals_spy)
    return seen


def test_notice_is_shown_while_the_theme_changes(window, monkeypatch):
    seen = spy_on_notice(window, monkeypatch)
    window.run_command("Set DARKMODE=Toggle")
    assert seen == [["Aplicando el tema…"]]
    settle()
    assert not [label for label in window.findChildren(QLabel) if label.objectName() == "busyNotice"]
    assert QApplication.overrideCursor() is None


def test_no_notice_if_nothing_has_to_be_repainted(window, monkeypatch):
    seen = spy_on_notice(window, monkeypatch)
    window.apply_theme()
    assert seen == [[]]


def test_notice_goes_away_even_if_something_fails(window, monkeypatch):
    def broken():
        raise RuntimeError("fallo")

    monkeypatch.setattr(window, "_apply_theme_everywhere", broken)
    window.config["theme"] = "light" if theme.current().name == "dark" else "dark"
    with pytest.raises(RuntimeError):
        window.apply_theme()
    settle()
    assert QApplication.overrideCursor() is None
    assert not [label for label in window.findChildren(QLabel) if label.objectName() == "busyNotice" and label.isVisible()]


def test_notice_can_be_read_in_both_themes():
    for current in (theme.DARK, theme.LIGHT):
        assert theme.contrast(current.accent, current.accent_text) >= 4.5
        assert "busyNotice" in theme.stylesheet(current)


def test_file_view_alone_has_the_button(qapp_for_gc):
    view = FileView()
    assert tool(view, "copypath") is not None
    view.close()
