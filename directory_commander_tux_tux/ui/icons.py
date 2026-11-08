"""Juego de iconos propio de Directory Commander Tux Tux, dibujado como SVG y coloreado según el tema."""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

# Lienzo de 24×24. `{c}` es el color del tema, `{f}` el de carpeta y `{a}` el de acento.
_FOLDER = '<path d="M3 7a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" fill="{f}" stroke="none"/>'
_DOC = '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'
_SHAPES = {
    "goback": '<path d="M20 12H5M11 6l-6 6 6 6"/>',
    "goforward": '<path d="M4 12h15M13 6l6 6-6 6"/>',
    "goup": '<path d="M12 20V5M6 11l6-6 6 6"/>',
    "godown": '<path d="M12 4v15M6 13l6 6 6-6"/>',
    "sort": '<path d="M4 6h9M4 12h6M4 18h3"/><path d="M17 5v14M14 16l3 3 3-3"/>',
    "metapane": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M14 5v14"/><circle cx="17.500" cy="9" r=".6" fill="{a}" stroke="{a}"/><path d="M17.500 12v4" stroke="{a}"/>',
    "goroot": '<path d="M5 4h14"/><path d="M12 20V8M7 13l5-5 5 5"/>',
    "refresh": '<path d="M19 8a8 8 0 1 0 1 6"/><path d="M20 3v5h-5"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h4v-6h4v6h4V10"/>',
    "favorites": '<path d="M12 3l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.3l-5.7 3.2 1.2-6.4L2.800 9.700l6.400-.8z" fill="{f}" stroke="{f}"/>',
    "favoritesadd": '<path d="M10 2l2.300 4.900 5.300.7-3.900 3.600 1 5.300-4.700-2.600-4.700 2.600 1-5.300L2.400 7.600l5.300-.7z" fill="{f}" stroke="{f}"/>'
                    '<path d="M18 14v8M14 18h8" stroke="{a}" stroke-width="2.400"/>',
    "favoritesedit": '<path d="M10 2l2.300 4.900 5.300.7-3.900 3.600 1 5.300-4.700-2.600-4.700 2.600 1-5.300L2.400 7.600l5.300-.7z" fill="{f}" stroke="{f}"/>'
                     '<path d="M13 22l1-3.500 6-6 2.500 2.500-6 6z" fill="{a}" stroke="{a}" stroke-width="1"/>',
    "folder": _FOLDER,
    "save": '<path d="M5 3h11l3 3v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 3v5h7V3"/><rect x="7" y="13" width="10" height="6" rx="1"/>',
    "diskusage": '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M12 3v18M3 13h9M12 9h9M16 9v12"/>'
                 '<rect x="3.800" y="3.800" width="7.400" height="8.400" fill="{a}" stroke="none"/>',
    "aimarks": '<path d="M3 4h8l10 10-7 7L3 11z"/><circle cx="7.500" cy="8.500" r="1.300"/>'
               '<path d="M18.500 1l1.200 2.800L22.500 5l-2.800 1.200L18.500 9l-1.200-2.800L14.500 5l2.800-1.200z" fill="{a}" stroke="{a}" stroke-width="1"/>',
    "openvideos": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M10 8.5v7l6-3.5z" fill="{a}"/>',
    "openvideossystem": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M9 9.5v5l4.5-2.5z"/><path d="M14 4h7v7M21 4l-7 7" stroke="{a}" stroke-width="2"/>',
    "openimages": '<rect x="3" y="5" width="18" height="14" rx="1"/><circle cx="8.500" cy="10" r="1.700" fill="{a}" stroke="none"/><path d="M4 18l6-5 3 3 3-3 4 4"/>',
    "openimagessystem": '<rect x="3" y="5" width="18" height="14" rx="1"/><circle cx="8.500" cy="10" r="1.700"/><path d="M4 18l5-4 2.5 2.5 2.5-2.5 3 4"/><path d="M14 4h7v7M21 4l-7 7" stroke="{a}" stroke-width="2"/>',
    "makedir": _FOLDER + '<path d="M12 11v6M9 14h6" stroke="#ffffff" stroke-width="2"/>',
    "copy": '<rect x="9" y="8" width="11" height="13" rx="1"/><path d="M5 16V4h11"/>',
    "move": '<path d="M6 3h8l4 4v5"/><path d="M6 3v18h6"/><path d="M14 18h7M18 15l3 3-3 3" stroke="{a}" stroke-width="2.200"/>',
    "rename": '<path d="M3 8h13v8H3z"/><path d="M19 4v16M16.500 4h5M16.500 20h5"/>',
    "delete": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/>',
    "deletepermanent": '<path d="M5 5l14 14M19 5L5 19" stroke-width="2.400"/>',
    "undo": '<path d="M8 5L3 10l5 5"/><path d="M3 10h11a6 6 0 0 1 0 12h-3"/>',
    "selectall": '<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 2"/><path d="M8 12l3 3 5-6" stroke="{a}" stroke-width="2.200"/>',
    "selectnone": '<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 2"/>',
    "selectinvert": '<rect x="4" y="4" width="16" height="16" rx="1"/><path d="M4 20L20 4V20z" fill="{c}" stroke="none"/>',
    "selectwild": '<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 2"/><path d="M12 8v8M8.500 10l7 4M15.500 10l-7 4" stroke="{a}" stroke-width="2"/>',
    "reselect": '<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 2"/><path d="M15 9a4 4 0 1 0 1 3"/>',
    "show": '<rect x="3" y="5" width="18" height="14" rx="1"/><circle cx="8.500" cy="10" r="1.700"/><path d="M4 18l6-5 3 3 3-3 4 4"/>',
    "slideshow": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M10 9v6l5-3z" fill="{c}"/>',
    "cyclemodes": '<path d="M4 6h16M4 12h16M4 18h16"/><path d="M8 4v16"/>',
    "detailsmode": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "listmode": '<path d="M4 7h7M4 12h7M4 17h7M14 7h6M14 12h6M14 17h6"/>',
    "smallicons": '<rect x="4" y="5" width="4" height="4"/><path d="M10 7h10"/><rect x="4" y="15" width="4" height="4"/><path d="M10 17h10"/>',
    "largeicons": '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
    "thumbnails": '<rect x="3" y="4" width="8" height="7"/><rect x="13" y="4" width="8" height="7"/><rect x="3" y="13" width="8" height="7"/><rect x="13" y="13" width="8" height="7"/>',
    "tile": '<rect x="3" y="5" width="6" height="5"/><path d="M11 7h10"/><rect x="3" y="14" width="6" height="5"/><path d="M11 16h10"/>',
    "dualdisplay": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M12 5v14"/><path d="M3 9h18" stroke="{a}"/>',
    "dualhorizontal": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M3 12h18"/>',
    "single": '<rect x="3" y="5" width="18" height="14" rx="1"/>',
    "viewerpane": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M14 5v14"/><path d="M16 9h3M16 12h3" stroke="{a}"/>',
    "tree": '<path d="M5 4v14h4M5 11h4"/><rect x="10" y="8.500" width="9" height="5"/><rect x="10" y="15.500" width="9" height="5"/>',
    "flat": '<path d="M5 6h14M5 12h14M5 18h14"/><path d="M3 6h.1M3 12h.1M3 18h.1"/>',
    "properties": _DOC + '<path d="M12 12v5M12 9.500v.1" stroke="{a}" stroke-width="2.200"/>',
    "setattr": '<path d="M4 6h10M4 12h5M4 18h8"/><path d="M14 12l2 2 4-5" stroke="{a}" stroke-width="2.200"/>',
    "setdate": '<rect x="4" y="5" width="16" height="15" rx="1"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    "format": '<path d="M4 6h10M4 12h16M4 18h10"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/>',
    "filter": '<path d="M3 5h18l-7 8v7l-4-2v-5z"/>',
    "compare": '<rect x="3" y="4" width="8" height="16" rx="1"/><rect x="13" y="4" width="8" height="16" rx="1"/><path d="M5.500 9h3M5.500 13h3M15.500 9h3M15.500 13h3" stroke="{a}"/>',
    "search": '<circle cx="11" cy="11" r="6.500"/><path d="M16 16l5 5"/>',
    "sync": '<path d="M4 9a8 8 0 0 1 14-3l2 2"/><path d="M20 3v5h-5"/><path d="M20 15a8 8 0 0 1-14 3l-2-2"/><path d="M4 21v-5h5"/>',
    "duplicates": '<rect x="4" y="4" width="10" height="13"/><rect x="10" y="8" width="10" height="13" fill="{bg}"/>',
    "getsizes": '<path d="M5 20V9M12 20V4M19 20v-7"/>',
    "extract": '<path d="M4 8h16v12H4z"/><path d="M12 3v9M8.500 8.500L12 12l3.500-3.500" stroke="{a}" stroke-width="2"/>',
    "addtozip": '<path d="M4 8h16v12H4z"/><path d="M12 12V3M8.500 6.500L12 3l3.500 3.500" stroke="{a}" stroke-width="2"/>',
    "shortcut": '<path d="M10 14a4 4 0 0 1 0-6l3-3a4 4 0 0 1 6 6l-2 2"/><path d="M14 10a4 4 0 0 1 0 6l-3 3a4 4 0 0 1-6-6l2-2"/>',
    "newfile": _DOC + '<path d="M12 12v6M9 15h6" stroke="{a}" stroke-width="2"/>',
    "newtextfile": _DOC + '<path d="M9 12h6M9 15h6M9 18h4"/>',
    "drive": '<rect x="3" y="7" width="18" height="10" rx="1"/><path d="M17 12h.1" stroke-width="2.600"/><path d="M6 12h6"/>',
    "gomycomputer": '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M9 20h6M12 16v4"/>',
    "godesktop": '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M9 20h6M12 16v4"/><path d="M6 8h3" stroke="{a}"/>',
    "downloads": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 20h16"/>',
    "gomydocuments": _DOC + '<path d="M9 13h6M9 16h6"/>',
    "gotrash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    "movetop": '<path d="M5 4h14"/><path d="M12 20V8M7 13l5-5 5 5"/>',
    "movebottom": '<path d="M5 20h14"/><path d="M12 4v12M7 11l5 5 5-5"/>',
    "paneframes": '<rect x="3" y="4" width="8" height="16" rx="1" stroke="{a}" stroke-width="2"/><rect x="13" y="4" width="8" height="16" rx="1"/>',
    "treepane": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M10 4v16"/><path d="M5.500 8v7.500h2M5.500 11.500h2" stroke="{a}"/>',
    "terminalpane": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 11h18"/><path d="M6.500 13.500l2 1.750-2 1.750M11 17.500h4" stroke="{a}"/>',
    "terminalonly": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M7 9l3 3-3 3M12 15h5" stroke="{a}"/>',
    "remote": '<rect x="4" y="3" width="16" height="7" rx="1"/><rect x="4" y="12" width="16" height="7" rx="1"/><path d="M7 6.500h.1M7 15.500h.1" stroke="{a}" stroke-width="2.400"/><path d="M11 6.500h6M11 15.500h6"/>',
    "disconnect": '<rect x="4" y="3" width="16" height="7" rx="1"/><rect x="4" y="12" width="16" height="7" rx="1"/><path d="M14.500 13.500l5 5M19.500 13.500l-5 5" stroke="{a}" stroke-width="2"/><path d="M7 6.500h.1M7 15.500h.1" stroke-width="2.400"/>',
    "filespane": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M7 9h10M7 12h10M7 15h6" stroke="{a}"/>',
    "terminalgeneral":'<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 12h18M12 4v8"/><path d="M6.500 14l2 1.750-2 1.750M11 17.500h4" stroke="{a}"/>',
    "prefs": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.600 5.600l2.100 2.100M16.300 16.300l2.100 2.100M18.400 5.600l-2.100 2.100M7.700 16.300l-2.100 2.100"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.500 9.500a2.500 2.500 0 1 1 3.500 2.300c-.7.300-1 .8-1 1.700M12 16.800v.1"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.500v.1" stroke-width="2.200"/>',
    "logs": '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "jobs": '<path d="M4 7h11M4 12h7M4 17h11"/><path d="M18 9l3 3-3 3"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "zoomin": '<circle cx="10.500" cy="10.500" r="6.500"/><path d="M15.500 15.500L21 21M10.500 7.500v6M7.500 10.500h6"/>',
    "zoomout": '<circle cx="10.500" cy="10.500" r="6.500"/><path d="M15.500 15.500L21 21M7.500 10.500h6"/>',
    "zoomactual": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M8 9.500v5M6.500 10.500L8 9.500M15.500 9.500v5M14 10.500l1.500-1M11.700 10.500v.1M11.700 13.500v.1"/>',
    "zoomshrink": '<rect x="3" y="5" width="18" height="14" rx="1"/><rect x="8.500" y="9" width="7" height="6"/>',
    "zoomfit": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M6.500 11V8.500H9M17.500 13v2.500H15"/>',
    "rotateleft": '<path d="M4 4v5h5"/><path d="M4.500 9a8 8 0 1 1-.5 4.500"/>',
    "rotateright": '<path d="M20 4v5h-5"/><path d="M19.500 9a8 8 0 1 0 .5 4.500"/>',
    "fullscreen": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "framestepback": '<path d="M6 5v14" stroke-width="2.400"/><path d="M19 5l-10 7 10 7z" fill="{c}"/>',
    "framestepforward": '<path d="M18 5v14" stroke-width="2.400"/><path d="M5 5l10 7-10 7z" fill="{c}"/>',
    "saveframe": '<path d="M4 7h3l2-3h6l2 3h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.500"/>',
    "themetoggle": '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="{c}"/>',
    "crop": '<path d="M7 2v15h15"/><path d="M2 7h15v15"/>',
    "savecrop": '<path d="M5 3h11l4 4v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 21v-7h8v7M8 3v5h6"/>',
    "sendright": '<path d="M4 6l9 6-9 6z" fill="{c}"/><path d="M17 5v14" stroke-width="2.400"/>',
    "sendleft": '<path d="M20 6l-9 6 9 6z" fill="{c}"/><path d="M7 5v14" stroke-width="2.400"/>',
    "senddown": '<path d="M6 4l6 9 6-9z" fill="{c}"/><path d="M5 17h14" stroke-width="2.400"/>',
    "sendup": '<path d="M6 20l6-9 6 9z" fill="{c}"/><path d="M5 7h14" stroke-width="2.400"/>',
    "mediastop": '<rect x="6" y="6" width="12" height="12" fill="{c}"/>',
    "mediarepeat": '<path d="M17 3l4 4-4 4"/><path d="M3 11V9a2 2 0 0 1 2-2h16"/><path d="M7 21l-4-4 4-4"/><path d="M21 13v2a2 2 0 0 1-2 2H3"/>',
    "mediaplay": '<path d="M7 4l13 8-13 8z" fill="{c}"/>',
    "mediapause": '<path d="M7 4h3v16H7zM14 4h3v16h-3z" fill="{c}"/>',
    "mediavolume": '<path d="M3 9v6h4l5 4V5L7 9z" fill="{c}"/><path d="M16 9a4 4 0 0 1 0 6M18.500 6a8 8 0 0 1 0 12"/>',
    "mediamuted": '<path d="M3 9v6h4l5 4V5L7 9z" fill="{c}"/><path d="M16 9l5 6M21 9l-5 6"/>',
    "toolbarmore": '<path d="M5 5l7 7-7 7M12 5l7 7-7 7" stroke="{a}" stroke-width="2.600"/>',
    "pin": '<path d="M9 3h6l-1 7 3 4H7l3-4zM12 14v7"/>',
    "copypath": '<rect x="6" y="5" width="12" height="16" rx="1"/><path d="M9 5V3h6v2M9 11h6M9 15h6"/>',
    "plus": '<path d="M12 5v14M5 12h14" stroke-width="2.400"/>',
    "swap": '<path d="M4 8h15M15 4l4 4-4 4"/><path d="M20 16H5M9 12l-4 4 4 4"/>',
    "collapse": '<path d="M5 12h14"/><rect x="3" y="4" width="18" height="16" rx="1"/>',
    "menu-arrow": '<path d="M7 10l5 5 5-5" fill="{c}"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6" stroke-width="2.200"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6" stroke-width="2.200"/>',
    "file-kind": _DOC,
}

_ALIASES = {
    "go-previous": "goback", "go-next": "goforward", "go-up": "goup", "go-home": "home", "go-jump": "move",
    "view-refresh": "refresh", "edit-copy": "copy", "edit-rename": "rename", "edit-delete": "delete",
    "folder-new": "makedir", "view-split-left-right": "dualdisplay", "view-split-top-bottom": "dualhorizontal",
    "view-restore": "single", "view-list-details": "detailsmode", "view-list-icons": "thumbnails",
    "view-list-tree": "flat", "document-preview": "viewerpane", "edit-find": "search", "folder-sync": "sync",
    "edit-copy-path": "duplicates", "utilities-terminal": "terminal", "drive-harddisk": "drive",
    "bookmark-new": "favoritesadd", "process-stop": "close", "copyas": "copy", "moveas": "move", "rename2": "rename",
    "duplicate": "copy", "newzipfile": "addtozip", "goupnewtab": "goup", "goupnewlister": "goup", "pathfield": "folder",
    "favoriteslist": "favorites", "smartfavorites": "favorites",
    "recentlist": "logs", "powermode": "detailsmode", "deletesecure": "deletepermanent", "viewerlink": "show",
    "undodlg": "undo", "drivebuttons": "drive", "gonetwork": "gomycomputer", "golibraries": "folder",
    "gocollection": "folder", "listmode2": "listmode", "editmeta": "setattr", "setcomment": "setattr",
    "setcolor": "setattr", "status": "setattr", "play": "slideshow", "screenshot": "show", "taskmanager": "jobs",
}

_SIZES = (16, 20, 24, 32, 48)
_cache: dict[tuple, QIcon] = {}


def _svg(name: str, t: theme.Theme, color: str, accent: str | None = None) -> bytes | None:
    shape = _SHAPES.get(name)
    if shape is None:
        return None
    body = (shape.replace("{c}", color).replace("{f}", t.folder).replace("{a}", accent or t.dir_text)
            .replace("{bg}", t.window))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.700" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _render(data: bytes, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(data)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def has_icon(name: str) -> bool:
    return _ALIASES.get(name.lstrip("#"), name.lstrip("#")) in _SHAPES


def themed(name: str, color: str | None = None, disabled_color: str | None = None) -> QIcon:
    """Icono por nombre. Admite también la forma `#copy`; si no existe, va vacío.
    Con `color` se dibuja de ese color entero, para ponerlo sobre un fondo que no es el del tema."""
    key = _ALIASES.get(name.lstrip("#"), name.lstrip("#"))
    t = theme.current()
    cached = _cache.get((key, t.name, color, disabled_color))
    if cached is not None:
        return cached
    data = _svg(key, t, color or t.icon, color)
    icon = QIcon()
    if data is not None:
        disabled = _svg(key, t, disabled_color or t.dim, disabled_color)
        for size in _SIZES:
            icon.addPixmap(_render(data, size))
            icon.addPixmap(_render(disabled, size), QIcon.Mode.Disabled)
    _cache[(key, t.name, color, disabled_color)] = icon
    return icon


def pixmap(name: str, size: int = 16) -> QPixmap:
    return themed(name).pixmap(size, size)


def app_icon() -> QIcon:
    return QIcon(os.path.join(os.path.dirname(__file__), "..", "resources", "directory-commander-tux-tux.svg"))
