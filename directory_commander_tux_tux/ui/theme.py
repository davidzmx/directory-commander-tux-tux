"""Temas de color. La hoja de estilos usa `role` para distinguir panel de origen y de destino."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtCore import Qt

from ..core.pane_colors import ROLES, clean_colors


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    panel: str
    field: str
    text: str
    dim: str
    border: str
    hover: str
    icon: str
    folder: str
    dir_text: str
    accent: str
    accent_text: str
    list_source: str
    list_dest: str
    list_other: str
    bar_source: str
    bar_dest: str
    bar_other: str
    sel_source: str
    sel_dest: str
    sel_other: str
    sel_text: str
    group_text: str
    chip: str
    meter_ok: str
    meter_low: str
    pale_path: bool = False  # la ruta de la barra de cada panel, más tenue que el resto de la barra


DARK = Theme(
    name="dark", window="#202124", panel="#17181a", field="#111214", text="#d9dadc", dim="#8b8e94", border="#3a3c40",
    hover="#34373c", icon="#c9ccd1", folder="#efb73e", dir_text="#8ab4f8", accent="#2d6db5", accent_text="#ffffff",
    list_source="#000000", list_dest="#190505", list_other="#000000", bar_source="#25374c", bar_dest="#452a2a", bar_other="#202124", sel_source="#1f4e85",
    sel_dest="#27501c", sel_other="#1f4e85", sel_text="#ffffff", group_text="#e8923c", chip="#2f6b2a", meter_ok="#3b82c4", meter_low="#d62f2f",
)
LIGHT = Theme(
    name="light", window="#f0f1f3", panel="#fafafb", field="#ffffff", text="#1f2124", dim="#6a6e75", border="#c9ccd1",
    hover="#dfe3e8", icon="#3c4047", folder="#e0a11b", dir_text="#1a4fa3", accent="#2d6db5", accent_text="#ffffff",
    list_source="#ffffff", list_dest="#fff6f5", list_other="#ffffff", bar_source="#1a6fc4", bar_dest="#cf3a32", bar_other="#f0f1f3", sel_source="#bcd6f5",
    sel_dest="#cfe8c4", sel_other="#bcd6f5", sel_text="#101114", group_text="#b3600f", chip="#bfe0b6", meter_ok="#3b82c4", meter_low="#d62f2f",
)
THEMES = {"dark": DARK, "light": LIGHT}
CHECK_MARK = os.path.join(os.path.dirname(__file__), "..", "resources", "check.svg").replace(os.sep, "/")  # el tema nativo apenas la dibuja en un árbol con fondo oscuro
GIT_COLORS = {"dark": ("#7ee787", "#1b3324", "#24452f"), "light": ("#116329", "#dafbe1", "#c2f0cd")}  # la rama: texto, fondo y fondo al pasar el ratón

_current = DARK


def current() -> Theme:
    return _current


def level_color(role: str, depth: int) -> QColor:
    """Fondo de una carpeta desplegada: el del panel, mezclado con su color y más intenso en cada nivel."""
    role = role if role in ROLES else "source"
    base = QColor(getattr(_current, f"list_{role}"))
    tint = QColor(tint_color(_current, role))
    amount = min(1.0, 0.5 + 0.25 * (depth - 1))
    return QColor(round(base.red() + (tint.red() - base.red()) * amount),
                  round(base.green() + (tint.green() - base.green()) * amount),
                  round(base.blue() + (tint.blue() - base.blue()) * amount))


def _mix(base: str, tint: str, amount: float) -> str:
    first, second = QColor(base), QColor(tint)
    return QColor(round(first.red() + (second.red() - first.red()) * amount),
                  round(first.green() + (second.green() - first.green()) * amount),
                  round(first.blue() + (second.blue() - first.blue()) * amount)).name()


def _luminance(color: str) -> float:
    value = QColor(color)
    parts = [channel / 255 for channel in (value.red(), value.green(), value.blue())]
    linear = [part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4 for part in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


_DARK_INK = "#1f2124"
FIND_MATCH = "#ffea80"  # las coincidencias de la búsqueda, igual en los dos temas: el texto encima siempre es oscuro
FIND_MATCH_TEXT = _DARK_INK
_ICON_CONTRAST = 3.0  # el mínimo para gráficos; el texto necesita 4.5
_TEXT_CONTRAST = 4.5


def _bar_of(theme: Theme, role: str) -> str:
    return getattr(theme, f"bar_{role}", theme.window)


def tint_color(theme: Theme, role: str) -> str:
    """El color del panel, suave, para ponerlo de fondo de otras cosas sin que estorbe al texto."""
    bar = _bar_of(theme, role)
    return _mix("#ffffff", bar, 0.28) if theme.name == "light" and bar != theme.window else bar


def crumb_color(theme: Theme, role: str) -> str:
    """El tramo de la ruta: el mismo color de la barra o, si se prefiere, ese color más tenue. Se elige por separado para cada tema."""
    bar = _bar_of(theme, role)
    if not theme.pale_path or bar == theme.window:
        return bar
    return _mix("#ffffff", bar, 0.28) if theme.name == "light" else _mix(theme.field, bar, 0.5)


def pane_outline(theme: Theme) -> str:
    return "#a0a0a0" if theme.name == "light" else "#55585e"


def pane_ring(theme: Theme, role: str) -> str:
    """La línea interior del contorno de la barra."""
    return _mix(_bar_of(theme, role), pane_outline(theme), 0.125)


def crumb_text_color(theme: Theme, role: str) -> str:
    crumb = crumb_color(theme, role)
    return next((color for color in (theme.text, _DARK_INK, "#ffffff") if contrast(crumb, color) >= _TEXT_CONTRAST), "#000000")


def bar_icon_color(theme: Theme, role: str) -> str:
    """Los iconos sobre el color de la barra: blancos en el tema claro si se distinguen, como también al elegir un color propio muy claro."""
    bar = _bar_of(theme, role)
    order = (theme.icon, "#ffffff", _DARK_INK) if theme.name == "dark" else ("#ffffff", theme.icon, _DARK_INK)
    return next((color for color in order if contrast(bar, color) >= _ICON_CONTRAST), theme.icon)


def bar_icon_disabled(theme: Theme, role: str) -> str:
    return _mix(_bar_of(theme, role), bar_icon_color(theme, role), 0.45)


def _selection(theme: Theme, color: str) -> str:
    """La fila seleccionada debe destacar sobre la lista y dejar leer su texto, sea cual sea el color elegido."""
    hue, saturation, value, _alpha = QColor(color).getHsv()
    if theme.name == "dark":
        return QColor.fromHsv(hue, max(saturation, 110) if saturation else 0, min(max(value, 130), 170)).name()
    return QColor.fromHsv(hue, min(saturation, 90), 245).name()


def with_pane_colors(theme: Theme, colors) -> Theme:
    """El tema con los colores elegidos para los paneles. El de la barra es tal cual, y de él salen el fondo y la selección."""
    changes: dict[str, str] = {}
    for role, color in clean_colors(colors).items():
        changes[f"bar_{role}"] = color
        changes[f"sel_{role}"] = _selection(theme, color)
        if role != "source":  # la lista del panel activo es siempre neutra, y de ella se distinguen las otras
            changes[f"list_{role}"] = _mix(theme.list_source, color, 0.12)
    if "sel_source" in changes:
        changes.setdefault("sel_other", changes["sel_source"])  # sin color propio, un panel más selecciona como el activo, no con el azul de serie
    return replace(theme, **changes)


def resolve(name: str) -> Theme:
    """`auto` sigue la preferencia clara u oscura del escritorio."""
    if name == "auto":
        app = QGuiApplication.instance()
        dark = app is None or app.styleHints().colorScheme() != Qt.ColorScheme.Light
        return DARK if dark else LIGHT
    return THEMES.get(name, DARK)


def request_color_scheme(setting: str) -> None:
    """Dice a Qt el tema elegido, y Qt pone con él la barra de título de todas las ventanas, también las que se abran después.
    Si se le pidiera solo a Windows, Qt la devolvería a la del sistema al poco."""
    app = QGuiApplication.instance()
    schemes = {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}
    wanted = schemes.get(setting, Qt.ColorScheme.Unknown)  # con «según el escritorio», sin pedir nada
    if app is not None and app.styleHints().colorScheme() != wanted:
        app.styleHints().setColorScheme(wanted)


def icon_theme_variant(name: str, dark: bool, installed) -> str:
    """El tema de iconos con el brillo que pide el fondo: `breeze` y `breeze-dark`, `Papirus-Light` y `Papirus-Dark`. Si no hay otro, el mismo."""
    stem = re.sub(r"[-_](dark|light)$", "", name, flags=re.IGNORECASE)
    wanted = (f"{stem}-dark", f"{stem}-Dark") if dark else (stem, f"{stem}-light", f"{stem}-Light")
    return next((option for option in (*wanted, name) if installed(option)), name)


def _icon_theme_installed(name: str) -> bool:
    return any(os.path.isfile(os.path.join(folder, name, "index.theme")) for folder in QIcon.themeSearchPaths())


_desktop_icon_theme: str | None = None


def match_icon_theme(dark: bool) -> None:
    """Los temas de iconos de Linux dibujan los iconos pequeños de un solo color, pensado para el fondo del escritorio: con el tema oscuro de aquí
    sobre un escritorio claro, las carpetas casi no se verían. En Windows no hay tema de iconos y no hace nada."""
    global _desktop_icon_theme
    if _desktop_icon_theme is None:
        _desktop_icon_theme = QIcon.themeName()
    if _desktop_icon_theme:
        wanted = icon_theme_variant(_desktop_icon_theme, dark, _icon_theme_installed)
        if wanted != QIcon.themeName():
            QIcon.setThemeName(wanted)


def set_current(theme: Theme) -> None:
    global _current
    _current = theme


def palette(theme: Theme) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: theme.window, QPalette.ColorRole.WindowText: theme.text,
        QPalette.ColorRole.Base: theme.field, QPalette.ColorRole.AlternateBase: theme.panel,
        QPalette.ColorRole.Text: theme.text, QPalette.ColorRole.Button: theme.window,
        QPalette.ColorRole.ButtonText: theme.text, QPalette.ColorRole.ToolTipBase: theme.panel,
        QPalette.ColorRole.ToolTipText: theme.text, QPalette.ColorRole.Highlight: theme.accent,
        QPalette.ColorRole.HighlightedText: theme.accent_text, QPalette.ColorRole.PlaceholderText: theme.dim,
        QPalette.ColorRole.Link: theme.dir_text,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(theme.dim))
    # El estilo oscurece el fondo de un botón desactivado: con el gris de siempre, su texto casi no se distinguía.
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(_mix(theme.dim, theme.text, 0.35)))
    return pal


def _pane_bar_rules(t: Theme, role: str) -> str:
    bar, crumb, ink = _bar_of(t, role), crumb_color(t, role), crumb_text_color(t, role)
    across, along = _mix(bar, "#000000", 1 / 7), _mix(pane_ring(t, role), "#000000", 1 / 7)
    pane = f'FileView[role="{role}"]'
    return f"""
    {pane} QFrame#paneBar {{ background: {bar}; }}
    {pane} QFrame#paneBar QToolButton:hover {{ background: {_mix(bar, bar_icon_color(t, role), 0.25)}; border-color: transparent; }}
    {pane} QFrame#crumbStrip {{ background: {crumb}; border-style: solid; border-width: 1px;
        border-left-color: {across}; border-right-color: {across}; border-top-color: {along}; border-bottom-color: {along}; }}
    {pane} QFrame#crumbStrip[fieldShown="true"] {{ background: {t.field}; }}
    {pane} QFrame#crumbStrip QToolButton {{ color: {ink}; }}
    {pane} QFrame#crumbStrip QToolButton:hover {{ background: {_mix(crumb, ink, 0.15)}; border-color: transparent; }}
    {pane} QFrame#crumbStrip[fieldShown="true"] QToolButton:hover {{ background: {t.hover}; }}
    {pane} QFrame#crumbStrip QLineEdit {{ color: {t.text}; background: transparent; border: none; padding: 0; }}"""


def stylesheet(t: Theme, dest_tint: bool = True) -> str:
    list_dest = t.list_dest if dest_tint else t.list_source
    list_other = t.list_other if dest_tint else t.list_source
    return f"""
    QMainWindow::separator {{ background: {t.border}; width: 3px; height: 3px; }}
    QSplitter::handle {{ background: {t.border}; }}
    QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; }}

    QMenuBar {{ background: {t.window}; padding: 1px 4px; }}
    QMenuBar::item {{ background: transparent; padding: 3px 7px; }}
    QMenuBar::item:selected {{ background: {t.hover}; }}
    QMenu {{ background: {t.panel}; border: 1px solid {t.border}; }}
    QMenu::item {{ padding: 4px 26px 4px 14px; }}
    QMenu::icon {{ left: 6px; }}
    QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu::item:disabled {{ color: {t.dim}; }}
    QMenu::item:default {{ font-weight: bold; }}
    QMenu::icon:checked {{ background: {t.accent}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 3px 6px; }}
    QMenu#folderList {{ border: 1px solid {t.accent}; padding: 0; }}
    QMenu#folderList::item {{ padding: 1px 8px 1px 4px; }}
    QMenu#folderList::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu#folderList::icon {{ left: 3px; }}
    QLabel#menuSection {{ background: {t.hover}; color: {t.text}; font-weight: bold; padding: 3px 6px; }}
    QLabel#prefsInfo {{ background: {tint_color(t, "source")}; color: {t.text}; border-radius: 3px; padding: 6px 8px; }}
    QLabel#notice {{ color: {t.dir_text}; padding-left: 6px; }}
    QToolButton#gitBranch, QLabel#gitBranch {{ background: {GIT_COLORS[t.name][1]}; color: {GIT_COLORS[t.name][0]}; border: 1px solid {GIT_COLORS[t.name][0]}; border-radius: 3px; padding: 0 5px; font-weight: bold; }}
    QToolButton#gitBranch:hover {{ background: {GIT_COLORS[t.name][2]}; }}
    QToolButton#gitBranch::menu-indicator {{ image: none; width: 0; }}
    QLabel#gitBranch {{ margin: 2px 0; }}
    QLabel#gitUser {{ color: {t.dim}; padding: 0 3px; }}
    QLabel#gitUser:hover {{ color: {t.text}; text-decoration: underline; }}
    QLabel#findHint {{ color: {t.dim}; }}
    QLabel#findHeading {{ font-weight: bold; }}
    QFrame#findChips {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 2px; }}
    QFrame#findChip {{ background: {t.hover}; border: 1px solid {t.border}; border-radius: 2px; }}
    QFrame#findBar {{ background: {t.window}; border-top: 1px solid {t.border}; }}
    QFrame#findBar[noMatch="true"] QLineEdit {{ background: {_mix(t.field, "#d62f2f", 0.25)}; }}
    QLabel#findCount {{ color: {t.dim}; }}
    QLabel#busyNotice {{ background: {t.accent}; color: {t.accent_text}; border-radius: 4px; padding: 12px 22px; font-weight: bold; }}

    QToolBar {{ background: {t.window}; border: none; border-bottom: 1px solid {t.border}; padding: 1px 4px; spacing: 2px; }}
    QToolBar::separator {{ background: {t.border}; width: 1px; margin: 3px 5px; }}
    QToolBar::handle:horizontal {{ background: {t.border}; width: 3px; margin: 3px 4px 3px 0; }}
    QToolBar::handle:vertical {{ background: {t.border}; height: 3px; margin: 0 3px 4px 3px; }}
    QFrame#hoverCard {{ background: {t.panel}; border: 1px solid {t.border}; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 2px; }}
    QToolButton:hover {{ background: {t.hover}; border-color: {t.border}; }}
    QToolButton:pressed {{ background: {t.accent}; }}
    QToolButton:checked {{ background: {t.accent}; color: {t.accent_text}; }}
    QToolButton:disabled {{ color: {t.dim}; }}
    QToolButton[popupMode="1"] {{ padding-right: 12px; }}
    QToolButton::menu-button {{ border: none; width: 11px; }}
    QToolButton::menu-indicator {{ image: none; }}
    QListView::indicator:unchecked, QCheckBox::indicator:unchecked, QTreeView::indicator:unchecked {{ width: 14px; height: 14px; background: {t.field}; border: 1px solid {t.text}; border-radius: 3px; }}
    QTreeView::indicator:checked {{ width: 14px; height: 14px; background: {t.accent}; border: 1px solid {t.accent}; border-radius: 3px; image: url({CHECK_MARK}); }}
    QCheckBox::indicator:unchecked:disabled {{ border-color: {t.border}; }}

    QToolButton#styleTab {{ border: 1px solid {t.border}; border-radius: 2px; padding: 1px 6px; }}
    QToolButton#styleTab:checked {{ background: {t.accent}; border-color: {t.accent}; color: {t.accent_text}; }}
    QToolButton#styleNew {{ border: 1px dashed {t.accent}; border-radius: 2px; padding: 1px 6px 1px 3px; }}
    QToolButton#styleNew:hover {{ background: {t.hover}; }}
    QToolButton#qt_toolbar_ext_button {{ background: {t.field}; border: 1px solid {t.accent}; border-radius: 2px; padding: 0; }}
    QToolButton#qt_toolbar_ext_button:hover {{ background: {t.hover}; }}
    QLabel#barLabel {{ color: {t.text}; padding: 0 8px 0 4px; }}

    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 2px; padding: 1px 3px; }}
    QComboBox {{ combobox-popup: 0; }}
    QComboBox QAbstractItemView {{ background: {t.panel}; selection-background-color: {t.accent}; }}
    QComboBox QAbstractItemView::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QFrame#crumbFrame {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 2px; }}
    QToolButton#splitArrow {{ border: 1px solid {t.border}; border-left: none; border-radius: 0; padding: 0 2px; }}
    QToolButton#crumb {{ padding: 1px 2px; }}
    QToolButton#crumbArrow {{ padding: 1px 3px; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar {{ background: {t.window}; }}
    QTabBar::tab {{ background: {t.window}; border: 1px solid {t.border}; border-bottom: none; padding: 3px 5px 3px 5px; margin-right: 1px; }}
    QTabBar::scroller {{ width: 0px; }}
    QTabBar[arrows="true"]::scroller {{ width: 36px; }}
    QTabBar::tab:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QTabBar::tab:hover:!selected {{ background: {t.hover}; }}

    QFrame#paneBar {{ background: {t.window}; border: none; }}
    FileView QFrame#crumbFrame {{ background: transparent; border: none; }}
    FileView QFrame#paneBar QToolButton {{ padding: 1px; }}
    FileView QFrame#crumbStrip QToolButton {{ padding: 0 2px; }}
    QFrame#paneBarFrame {{ border: 1px solid {pane_outline(t)}; }}
{"".join(_pane_bar_rules(t, role) for role in (*ROLES, "single"))}

    QHeaderView {{ background: {t.window}; }}
    QHeaderView::section {{ background: {t.window}; color: {t.text}; border: none; border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; padding: 3px 6px; }}
    QHeaderView::section:hover {{ background: {t.hover}; }}

    QAbstractItemView {{ outline: none; }}
    FileView QAbstractItemView {{ background: {t.list_source}; border: none; }}
    FileView[role="dest"] QAbstractItemView {{ background: {list_dest}; }}
    FileView[role="other"] QAbstractItemView {{ background: {list_other}; }}
    FileView QListView::item:selected {{ background: {t.sel_source}; color: {t.sel_text}; }}
    FileView[role="dest"] QListView::item:selected {{ background: {t.sel_dest}; }}
    FileView[role="other"] QListView::item:selected {{ background: {t.sel_other}; }}
    FileView QListView::item:hover:!selected {{ background: {t.hover}; }}
    QTableView[grid="on"]::item {{ border-bottom: 1px solid {t.border}; }}

    QTreeView {{ background: {t.panel}; border: none; }}
    QTreeView::item {{ padding: 1px 0; }}
    QTreeView::item:selected {{ background: {t.sel_source}; color: {t.sel_text}; }}
    QTreeView::item:hover:!selected {{ background: {t.hover}; }}

    QTreeWidget#prefsNavigation::item {{ padding: 3px 2px; }}

    QFrame#dockTitle {{ background: {t.window}; border-bottom: 1px solid {t.border}; }}
    QFrame#statusRow {{ background: {t.window}; border-top: 1px solid {t.border}; }}
    QLabel#chip {{ background: {t.chip}; border-radius: 2px; padding: 0 5px; }}

    QScrollBar:vertical {{ background: {t.window}; width: 12px; margin: 0; }}
    QScrollBar:horizontal {{ background: {t.window}; height: 12px; margin: 0; }}
    QScrollBar::handle {{ background: {t.border}; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {t.dim}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
    """
