"""Opciones de la barra de pestañas. Sin Qt, para poder probar sin interfaz cómo se validan y combinan."""
from __future__ import annotations

NEW_TAB_ACTIONS = ("current", "duplicate", "default", "home", "location")
SHOW_BAR = ("needed", "balanced", "always")
POSITIONS = ("bottom", "top", "right", "left")
DUAL_POSITIONS = ("normal", "together", "apart")
SELECTED_TAB_CLICK = ("rename", "previous", "nothing")
SAME_SIZE_MODES = ("auto", "fixed")

DEFAULTS: dict = {
    # Barra
    "new_button": True,
    "new_button_action": "current",
    "new_button_path": "",
    "double_click": True,
    "double_click_action": "current",
    "double_click_path": "",
    "default_folder": "",
    "open_next_to_active": False,
    "multi_row": True,
    "overflow_menu": True,
    "menu_all_tabs": False,
    "menu_when_shrunk": False,
    "show_bar": "always",
    "position": "top",
    "dual_position": "normal",
    # Dimensiones
    "same_size": False,
    "same_size_mode": "auto",
    "fixed_size": 100,
    "max_size_on": False,
    "max_size": 100,
    "min_size_on": False,
    "min_size": 40,
    "shrink": False,
    "vertical_auto_width": False,
    "vertical_width": 128,
    # Opciones
    "show_drive": True,
    "show_icons": True,
    "show_index": False,
    "drag_to_front": True,
    "drag_delay_ms": 1000,
    "tab_as_folder": False,
    "selected_click": "nothing",
    "double_click_close": False,
    "show_close": True,
    "close_active_only": False,
    "small_close": False,
    "window_close_closes_tab": False,
    "close_window_with_last_tab": False,
}
_CHOICES = {
    "new_button_action": NEW_TAB_ACTIONS,
    "double_click_action": NEW_TAB_ACTIONS,
    "show_bar": SHOW_BAR,
    "position": POSITIONS,
    "dual_position": DUAL_POSITIONS,
    "same_size_mode": SAME_SIZE_MODES,
    "selected_click": SELECTED_TAB_CLICK,
}
_RANGES = {
    "fixed_size": (20, 1000),
    "max_size": (20, 1000),
    "min_size": (20, 1000),
    "vertical_width": (40, 1000),
    "drag_delay_ms": (0, 10000),
}


def _valid(key: str, value) -> bool:
    default = DEFAULTS[key]
    if key in _CHOICES:
        return value in _CHOICES[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)


def normalize(raw) -> dict:
    """Todas las opciones con un valor utilizable: el archivo de configuración puede venir de cualquier sitio."""
    source = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in source and _valid(key, source[key]):
            value = source[key]
            if key in _RANGES:
                low, high = _RANGES[key]
                value = max(low, min(high, value))
            settings[key] = value
    return settings


def tab_positions(position: str, dual_position: str, dual: str) -> tuple[str, str]:
    """Posición de las pestañas de cada panel. Juntos y separados solo se aplican cuando las pestañas
    quedan a lo largo de la línea que divide los paneles; si no, ambos usan la posición elegida."""
    side_by_side = dual == "Vertical" and position in ("left", "right")
    stacked = dual == "Horizontal" and position in ("top", "bottom")
    if dual_position == "together":
        if side_by_side:
            return "right", "left"
        if stacked:
            return "bottom", "top"
    elif dual_position == "apart":
        if side_by_side:
            return "left", "right"
        if stacked:
            return "top", "bottom"
    return position, position


def bar_visible(show_bar: str, count: int, counts_of_visible_panes: list[int]) -> bool:
    if show_bar == "needed":
        return count > 1
    if show_bar == "balanced":  # así las listas de los dos paneles quedan alineadas
        return any(other > 1 for other in counts_of_visible_panes)
    return True
