"""Ventana principal. Toda acción del usuario termina en `run_command`."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace

from PySide6.QtCore import QByteArray, QEvent, QFile, QMimeDatabase, QPoint, QProcess, QSize, QStandardPaths, QStorageInfo, QThreadPool, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon, QImageReader, QKeySequence
from PySide6.QtWidgets import QProxyStyle, QStyle
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMenuBar, QMessageBox,
                               QSizePolicy, QSplitter,
                               QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from .. import APP_NAME, REPO_URL, __version__
from ..core import sessions, archive, bar_buttons, commands, context_menu, desktop_apps, devices, favorites, file_compare, frequent, fsops, git_tools, listing, pane_colors, platform_utils, remote, rename, shells, styles, tab_settings, windows_shell
from ..core.config import COMPARE_COMMAND, DEFAULT_SORTS, GENERATION_MARK_KINDS, STYLE_RENAMES, THUMB_SIZES, VIDEO_THUMBNAILS, Config
from ..core.fsops import Answer, Collision, OpResult
from ..core import media_info
from ..core.media_info import VIDEO_EXTENSIONS
from ..core.listing import (COMPUTER_LABEL, COMPUTER_PATH, CONTENT_TYPES, entry_from_path, format_size, format_time, has_computer_view, is_results, results_exist, results_name, results_path,
                            location_label)
from . import clipboard, theme
from .background import GuiAsker, collect_garbage_in_gui_thread, run_bg
from .check_combo import CheckCombo
from .batch_rename import BatchRenameDialog
from .dialogs import PreferencesDialog, PropertiesDialog, SetAttrDialog
from .extra_commands import ExtraCommands, _turn
from .favorites_pages import AddFavoriteDialog
from .file_view import Breadcrumb, DiskMeter, FileView, FolderList
from .hover_card import read_image_info
from .code_view import is_json, is_markdown
from .shell_menu import LAST_GROUP, add_section, add_shell_items, menu_text, tidy_separators
from .drag_drop import accept_drop
from .replace_dialog import ReplaceDialog
from . import sftp, udisks
from .connection_led import BLINK_MS, ConnectionLed
from .open_external import has_default_app, open_externally
from .tab_bar import ADD_BUTTON_WIDTH, PaneTabBar, folders_in
from .folder_tree import PATH_ROLE, FolderTree
from .icons import app_icon, themed
from .image_viewer import ImageViewer
from .model import IMAGE_EXTENSIONS, folder_icon
from .preview import MetadataPanel, PreviewPane
from .pane_frame import FramedSplitter
from .terminal import GENERAL_TOGGLE_SHORTCUT, TOGGLE_SHORTCUT, ShellBox, TerminalView
from .compare_view import CompareWindow, compare_files
from .find_panel import FindPanel, FindWindow
from .git_bar import GitBar
from .utility_panels import DupePanel, JobsPanel, LogPanel, SyncPanel, UtilityPanel
from ..core import i18n
from ..core.i18n import N_, tr

# (menú, etiqueta, icono, atajos, comando, solo cuando el foco está en los paneles)
MENU_ITEMS = [
    (N_("&Archivo"), N_("Nueva carpeta"), "makedir", "F7", "CreateFolder", True),
    (N_("&Archivo"), N_("Nuevo archivo"), "newfile", "Shift+F7", "CreateFile", True),
    (N_("&Archivo"), N_("Abrir terminal aquí"), "terminal", "Ctrl+Alt+T", "CLI DOSPROMPT", True),
    (N_("&Archivo"), N_("Panel remoto (SFTP)…"), "remote", None, "Set SFTP=Menu", False),
    (N_("&Archivo"), None, None, None, None, False),
    (N_("&Archivo"), N_("Comprimir en ZIP…"), "addtozip", None, "Archive", True),
    (N_("&Archivo"), N_("Extraer en destino"), "extract", None, "Extract", True),
    (N_("&Archivo"), None, None, None, None, False),
    (N_("&Archivo"), N_("Propiedades"), "properties", "Alt+Return", "Properties", True),
    (N_("&Archivo"), N_("Atributos…"), "setattr", None, "SetAttr", True),
    (N_("&Archivo"), None, None, None, None, False),
    (N_("&Archivo"), N_("Salir"), "close", "Ctrl+Q", "Close", False),
    (N_("&Editar"), N_("Deshacer"), "undo", "Ctrl+Z", "Undo", True),
    (N_("&Editar"), N_("Rehacer"), None, "Ctrl+Y", "Undo REDO", True),
    (N_("&Editar"), None, None, None, None, False),
    (N_("&Editar"), N_("Copiar"), "copy", "Ctrl+C", "Clipboard COPY", True),
    (N_("&Editar"), N_("Cortar"), None, "Ctrl+X", "Clipboard CUT", True),
    (N_("&Editar"), N_("Pegar"), None, "Ctrl+V", "Clipboard PASTE", True),
    (N_("&Editar"), N_("Copiar rutas como texto"), None, "Ctrl+Shift+C", "Clipboard COPYNAMES", True),
    (N_("&Editar"), None, None, None, None, False),
    (N_("&Editar"), N_("Copiar a destino"), "copy", "F6", "Copy", True),
    (N_("&Editar"), N_("Mover a destino"), "move", "Shift+F6", "Copy MOVE", True),
    (N_("&Editar"), N_("Duplicar aquí"), None, "Ctrl+Alt+D", "Copy HERE", True),
    (N_("&Editar"), N_("Renombrar"), "rename", "F2", "Rename", True),
    (N_("&Editar"), N_("Renombrado masivo…"), "rename", "Ctrl+F2", "Rename ADVANCED", True),
    (N_("&Editar"), N_("Enviar a la papelera"), "delete", "Delete", "Delete", True),
    (N_("&Editar"), N_("Eliminar definitivamente"), "deletepermanent", "Shift+Delete", "Delete NORECYCLE", True),
    (N_("&Editar"), None, None, None, None, False),
    (N_("&Editar"), N_("Seleccionar todo"), "selectall", "Ctrl+A", "Select ALL", True),
    (N_("&Editar"), N_("Quitar selección"), "selectnone", "Ctrl+Shift+A", "Select NONE", True),
    (N_("&Editar"), N_("Invertir selección"), "selectinvert", "Ctrl+I", "Select INVERT", True),
    (N_("&Editar"), N_("Seleccionar por patrón…"), "selectwild", "Ctrl+S", "Select PATTERN", True),
    (N_("&Editar"), N_("Recuperar la selección anterior"), "reselect", None, "Select RESELECT", True),
    (N_("&Editar"), N_("Seleccionar lo que también está en destino"), None, None, "Select SOURCETODEST=in", True),
    (N_("&Editar"), N_("Seleccionar lo que falta en destino"), None, None, "Select SOURCETODEST=notin", True),
    (N_("&Ver"), N_("Lista"), "listmode", "Ctrl+1", "Set VIEW=List", False),
    (N_("&Ver"), N_("Detalles"), "detailsmode", "Ctrl+2", "Set VIEW=Details", False),
    (N_("&Ver"), N_("Iconos pequeños"), "smallicons", "Ctrl+3", "Set VIEW=SmallIcons", False),
    (N_("&Ver"), N_("Iconos grandes"), "largeicons", "Ctrl+4", "Set VIEW=LargeIcons", False),
    (N_("&Ver"), N_("Mosaicos"), "tile", "Ctrl+5", "Set VIEW=Tiles", False),
    (N_("&Ver"), N_("Miniaturas"), "thumbnails", "Ctrl+6", "Set VIEW=Thumbnails", False),
    (N_("&Ver"), None, None, None, None, False),
    (N_("&Ver"), N_("Un panel"), "single", None, "Set DUAL=Off", False),
    (N_("&Ver"), N_("Doble panel vertical"), "dualdisplay", None, "Set DUAL=Vertical", False),
    (N_("&Ver"), N_("Doble panel horizontal"), "dualhorizontal", None, "Set DUAL=Horizontal", False),
    (N_("&Ver"), None, None, None, None, False),
    (N_("&Ver"), N_("Árbol de carpetas"), "tree", "Ctrl+Shift+T", "Set TREE=Toggle", False),
    (N_("&Ver"), N_("Árbol del panel"), "treepane", None, "Set PANETREE=Toggle", False),
    (N_("&Ver"), N_("Visor"), "viewerpane", "F3", "Set VIEWPANE=Toggle", False),
    (N_("&Ver"), N_("Metadatos"), "metapane", "Ctrl+Shift+M", "Set METAPANE=Toggle", False),
    (N_("&Ver"), N_("Terminal del panel"), "terminalpane", TOGGLE_SHORTCUT, "Set TERMINAL=Toggle", False),
    (N_("&Ver"), N_("Solo la terminal en este panel"), "terminalonly", None, "Set TERMINALPANE=On", False),
    (N_("&Ver"), N_("Terminal general"), "terminalgeneral", GENERAL_TOGGLE_SHORTCUT, "Set GENERALTERMINAL=Toggle", False),
    (N_("&Ver"), N_("Línea de comandos"), "terminal", "Ctrl+Shift+P", "Set CMDLINE=Toggle", False),
    (N_("&Ver"), None, None, None, None, False),
    (N_("&Ver"), N_("Vista plana"), "flat", "Ctrl+Shift+V", "Set FLATVIEW=Toggle", False),
    (N_("&Ver"), N_("Mostrar ocultos"), None, "Ctrl+H", "Set HIDDEN=Toggle", False),
    (N_("&Ver"), N_("Filtrar…"), "filter", "Ctrl+F", "Set FILTER=Show", False),
    (N_("&Ver"), N_("Marcas de IA"), "aimarks", None, "Set GENERATIONMARKS=Toggle", False),
    (N_("&Ver"), N_("Abrir vídeos con el visor interno"), "openvideos", None, "Set OPENVIDEOS=Toggle", False),
    (N_("&Ver"), N_("Abrir imágenes con el visor interno"), "openimages", None, "Set OPENIMAGES=Toggle", False),
    (N_("&Ver"), N_("Líneas entre filas"), None, None, "Set GRIDLINESH=Toggle", False),
    (N_("&Ver"), N_("Seleccionar la fila completa"), None, None, "Set FULLROWSELECT=Toggle", False),
    (N_("&Ver"), N_("Fechas relativas"), None, None, "Set FRIENDLYDATES=normal", False),
    (N_("&Ver"), N_("Fechas completas"), None, None, "Set FRIENDLYDATES=off", False),
    (N_("&Ver"), N_("Ordenar por…"), None, None, "Set SORTBY=sortlist", False),
    (N_("&Ver"), N_("Bloqueo de navegación"), None, None, "Set NAVLOCK=Toggle", False),
    (N_("&Ver"), N_("Calcular tamaños de carpetas"), "getsizes", "Ctrl+Shift+Z", "GetSizes", True),
    (N_("&Ir"), N_("Atrás"), "goback", "Alt+Left", "Go BACK", False),
    (N_("&Ir"), N_("Adelante"), "goforward", "Alt+Right", "Go FORWARD", False),
    (N_("&Ir"), N_("Subir"), "goup", "Alt+Up", "Go UP", False),
    (N_("&Ir"), N_("Raíz"), "goroot", "Ctrl+Backspace", "Go ROOT", False),
    (N_("&Ir"), N_("Inicio"), "home", "Alt+Home", "Go HOME", False),
    (N_("&Ir"), N_("Actualizar"), "refresh", "F5;Ctrl+R", "Go REFRESH", False),
    (N_("&Ir"), N_("Escribir ruta"), "folder", "F4;Ctrl+L", "Go PATHFIELD", False),
    (N_("&Ir"), None, None, None, None, False),
    (N_("&Ir"), N_("Este equipo"), "gomycomputer", None, "Go /mycomputer", False),
    (N_("&Ir"), N_("Escritorio"), "godesktop", None, "Go /desktop", False),
    (N_("&Ir"), N_("Documentos"), "gomydocuments", None, "Go /mydocuments", False),
    (N_("&Ir"), N_("Descargas"), "downloads", None, "Go /downloads", False),
    (N_("&Ir"), None, None, None, None, False),
    (N_("&Ir"), N_("Nueva pestaña"), "plus", "Ctrl+T", "Go TABNEW", False),
    (N_("&Ir"), N_("Cerrar pestaña"), "close", "Ctrl+W", "Go TABCLOSE", False),
    (N_("&Ir"), N_("Pestaña siguiente"), None, "Ctrl+Tab", "Go TABNEXT", False),
    (N_("&Ir"), N_("Pestaña anterior"), None, "Ctrl+Shift+Tab", "Go TABPREV", False),
    (N_("&Ir"), None, None, None, None, False),
    (N_("&Ir"), N_("Intercambiar paneles"), "swap", "Ctrl+Shift+X", "Go SWAP", False),
    (N_("&Ir"), N_("Abrir en el otro panel"), "sendright", "Ctrl+Shift+D", "Go DEST", False),
    (N_("&Ajustes"), N_("Preferencias…"), "prefs", "Ctrl+,", "Prefs", False),
    (N_("&Ajustes"), N_("Perfiles SSH/SFTP…"), "remote", None, "Prefs SFTP", False),
    (N_("&Ajustes"), N_("Barras visibles…"), "single", None, "Toolbar LIST", False),
    (N_("&Ajustes"), N_("Bloquear las barras"), None, None, "Set LOCKBARS=Toggle", False),
    (N_("&Ajustes"), N_("Información al pasar el ratón"), None, None, "Set HOVERINFO=Toggle", False),
    (N_("&Ajustes"), N_("Barra de favoritos"), "favorites", None, "Toolbar NAME=\"Favorites Bar\" TOGGLE", False),
    (N_("&Ajustes"), N_("Icono en la esquina superior derecha"), None, None, "Set APPLOGO=Toggle", False),
    (N_("&Ajustes"), None, None, None, None, False),
    (N_("&Ajustes"), N_("Guardar diseño…"), "dualdisplay", None, "Prefs LAYOUTSAVE", False),
    (N_("&Ajustes"), N_("Abrir diseño…"), "dualdisplay", None, "Prefs LAYOUTLIST", False),
    (N_("&Ajustes"), N_("Eliminar diseño…"), "delete", None, "Prefs LAYOUTEDIT", False),
    (N_("&Ajustes"), N_("Exportar configuración…"), "addtozip", None, "Prefs EXPORT", False),
    (N_("&Ajustes"), N_("Importar configuración…"), "extract", None, "Prefs IMPORT", False),
    (N_("&Ajustes"), None, None, None, None, False),
    (N_("&Herramientas"), N_("Buscar archivos"), "search", "Ctrl+Shift+F", "Set UTILITY=Find", False),
    (N_("&Herramientas"), N_("Sincronizar paneles"), "sync", None, "Set UTILITY=Sync", False),
    (N_("&Herramientas"), N_("Buscar duplicados"), "duplicates", None, "Set UTILITY=Dupe", False),
    (N_("&Herramientas"), N_("Comparar archivos"), "compare", None, COMPARE_COMMAND, False),
    (N_("&Herramientas"), N_("Uso del disco…"), "diskusage", None, "DiskUsage", False),
    (N_("&Herramientas"), N_("Visor de imágenes"), "show", None, "Show", False),
    (N_("&Herramientas"), N_("Convertir imágenes…"), "show", None, "Image CONVERT", True),
    (N_("&Herramientas"), N_("Girar 90° a la derecha"), "refresh", None, "Image ROTATE=90 HERE REPLACE PRESERVEDATE", True),
    (N_("&Herramientas"), N_("Girar 90° a la izquierda"), "undo", None, "Image ROTATE=270 HERE REPLACE PRESERVEDATE", True),
    (N_("&Herramientas"), None, None, None, None, False),
    (N_("&Herramientas"), N_("Dividir archivo…"), "duplicates", None, "Split", True),
    (N_("&Herramientas"), N_("Unir archivos"), "addtozip", None, "Join", True),
    (N_("&Herramientas"), N_("Copiar lo más reciente a destino"), "sync", None, "Copy UPDATEALL", True),
    (N_("&Herramientas"), N_("Guardar listado de la carpeta…"), "logs", None, "Print FOLDER", False),
    (N_("&Herramientas"), None, None, None, None, False),
    (N_("&Herramientas"), N_("Trabajos"), "jobs", None, "Set UTILITY=Jobs", False),
    (N_("&Herramientas"), N_("Registro"), "logs", "Ctrl+U", "Set UTILITY=Log", False),
    (N_("Ay&uda"), N_("Acerca de Directory Commander Tux Tux"), "about", None, "Help ABOUT", False),
]

LAYOUT_STATE_VERSION = 2  # súbelo si cambian las barras o paneles de la ventana
DEFAULT_STYLE_NAME = "Vertical doble panel"
PANE_BRANCH_POLL_MS = 2000
HOVER_PREVIEW_MS = 150
LIST_COLUMN_CHARS = 50  # el ancho máximo de las columnas de la lista, de serie
DEVICES_POLL_MS = 3000  # un USB que se conecta, o un disco que se monta desde otro programa
COMPARE_STATE_DELAY_MS = 60
SHELL_BOX_MAX_WIDTH = 150
PANE_TREE_WIDTH = 260
TERMINAL_MIN_HEIGHT = 160
SSH_CHOICE = "ssh:"  # en la lista de intérpretes de una terminal, un perfil SSH: la terminal se abre en ese servidor
GENERAL_TERMINAL = styles.MAX_PANES  # las terminales son la de cada panel y, detrás, la general
BAR_OVERFLOW_WIDTH = 26
# En «Este equipo» los elementos son unidades enteras: estas operaciones no tienen sentido ahí.
NOT_IN_RESULTS = {"diskusage", "createfolder", "createfile", "filetype", "archive", "extract", "split", "join", "cli"}  # hacen algo en la carpeta, y los resultados no son una
# En un panel remoto: lo que sabe hacer en un servidor. Lo demás lee o escribe archivos del equipo.
ON_SERVER = {"go", "copy", "delete", "rename", "createfolder", "createfile", "select", "set", "clipboard", "favorites", "help", "close", "prefs",
             "toolbar", "recent", "marker", "compare"}
# Formas de Copy que trabajan con archivos del equipo: con un servidor de por medio, no.
REMOTE_COPY_LIMITS = ("ADDTOARCHIVE", "ADDTOZIP", "ZIP", "ARCHIVE", "EXTRACT", "MAKELINK", "UPDATEALL", "UPDATEEXISTING")
NOT_ON_COMPUTER = {"diskusage", "copy", "delete", "rename", "createfolder", "createfile", "filetype", "archive", "extract", "setattr",
                   "image", "split", "join", "getsizes", "cli", "run", "print", "play"}
HIDDEN_FILESYSTEMS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "efivarfs", "cgroup2"}
VIEW_MODES = {"list": "list", "details": "details", "power": "details", "smallicons": "smallicons",
              "largeicons": "largeicons", "tiles": "tiles", "thumbnails": "thumbnails", "filmstrip": "filmstrip"}
SPECIAL_FOLDERS = {"/desktop": QStandardPaths.StandardLocation.DesktopLocation,
                   "/mydocuments": QStandardPaths.StandardLocation.DocumentsLocation,
                   "/downloads": QStandardPaths.StandardLocation.DownloadLocation,
                   "/home": QStandardPaths.StandardLocation.HomeLocation}


def move_to_trash(path: str) -> bool:
    result = QFile.moveToTrash(path)
    return bool(result[0]) if isinstance(result, tuple) else bool(result)


RESULTS_REFRESH_MS = 400
MAX_TAB_NAME = 32
FAVORITE_SHORTCUT = "Ctrl+D"
OVERFLOW_BUTTON_WIDTH = 20
MAX_WIDGET_SIZE = 16777215
TAB_POSITIONS = {"top": QTabWidget.TabPosition.North, "bottom": QTabWidget.TabPosition.South,
                 "left": QTabWidget.TabPosition.West, "right": QTabWidget.TabPosition.East}


def _location_exists(path: str) -> bool:
    return (path == COMPUTER_PATH and has_computer_view()) or results_exist(path) or os.path.isdir(path)


def remote_host(path: str) -> str:
    """El servidor, tal como se escribió en el perfil: una IP o un nombre."""
    return remote.server_parts(remote.server_of(path))[1]


def _image_size(path: str) -> tuple[int, int]:
    """Las medidas de una imagen, leyendo solo su cabecera. (0, 0) si no lo es."""
    size = QImageReader(path).size()
    return (size.width(), size.height()) if size.isValid() else (0, 0)


def _tab_title(path: str, show_drive: bool = True) -> str:
    if path == COMPUTER_PATH:
        return COMPUTER_LABEL
    if is_results(path):
        return results_name(path)
    if remote.is_remote(path):
        name = remote.name_of(path) if remote.inner_of(path) != "/" else "/"
        return f"{name} ({remote_host(path)})"  # que se vea siempre que no es una carpeta del equipo
    name = os.path.basename(path.rstrip("/\\")) or path
    if len(name) > MAX_TAB_NAME:  # la ruta completa queda en la descripción emergente
        name = f"{name[:MAX_TAB_NAME - 11]}…{name[-10:]}"
    drive = os.path.splitdrive(path)[0]
    return f"({drive}) {name}" if show_drive and drive and name != path else name


class _BarStyle(QProxyStyle):
    """Da su ancho al botón » de las barras: de serie mide 12 píxeles y casi no se ve."""

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ToolBarExtensionExtent:
            return BAR_OVERFLOW_WIDTH
        return super().pixelMetric(metric, option, widget)


def _bar_style() -> QProxyStyle:
    """Uno solo y de la aplicación: si fuera de la ventana, al cerrarla se destruiría antes que las barras que lo usan."""
    app = QApplication.instance()
    style = app.findChild(_BarStyle)
    if style is None:
        style = _BarStyle(QApplication.style().name())
        style.setParent(app)
    return style


class PaneGroup(QTabWidget):
    """Un lado de la ventana, con sus pestañas y el botón para abrir otra."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        self.settings = tab_settings.normalize(None)
        self.setTabBar(PaneTabBar(self))
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        # Sin recortar: cada pestaña conserva su nombre y las que no caben pasan al menú ».
        self.setElideMode(Qt.TextElideMode.ElideNone)
        bar = self.tabBar()
        bar.setExpanding(False)
        bar.setDrawBase(False)  # la línea base usa el color del estilo y no el del tema: se veía blanca
        self.setAcceptDrops(True)
        self.add_button = QToolButton(self)
        self.add_button.setAutoRaise(True)
        self.add_button.setToolTip(tr("Nueva pestaña (Ctrl+T)"))
        self.add_button.setIcon(themed("plus"))
        self.add_button.setIconSize(QSize(12, 12))
        self.add_button.clicked.connect(lambda: self.open_new_tab("new_button"))
        self.overflow_button = QToolButton(self)
        self.overflow_button.setAutoRaise(True)
        self.overflow_button.setText("»")
        self.overflow_button.setToolTip(tr("Más pestañas"))
        self.overflow_button.clicked.connect(self._show_overflow_menu)
        self.overflow_button.hide()
        self._current_view: FileView | None = None
        self._previous_view: FileView | None = None
        self._pressed_selected = -1
        self._press_position = QPoint()
        self._click_timer = QTimer(self, singleShot=True, interval=QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self._selected_tab_clicked)
        bar.setMouseTracking(True)
        bar.installEventFilter(self)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._show_tab_menu)
        bar.tabMoved.connect(lambda *_: self._after_tabs_changed())
        bar.tabBarDoubleClicked.connect(self._tab_double_clicked)
        self.currentChanged.connect(lambda _i: window.on_tab_changed(self))
        self.currentChanged.connect(self._remember_current)

    def open_new_tab(self, origin: str = "new_button") -> FileView:
        """`origin` indica qué opción decide la carpeta: el botón nuevo o el doble clic."""
        settings = self.settings
        action = settings[f"{origin}_action"]
        source = self.current
        home = platform_utils.home_dir()
        target = {
            "current": source.path if source else home,
            "duplicate": source.path if source else home,
            "default": settings["default_folder"] or home,
            "home": home,
            "location": settings[f"{origin}_path"] or home,
        }[action]
        if not _location_exists(target):
            self.window_.statusBar().showMessage(tr("No existe la carpeta {target}", target=target), 5000)
            target = home
        view = self.add_tab(target)
        if action == "duplicate" and source is not None:
            view.copy_view_settings(source)
        return view

    def _in_tab_strip(self, position: QPoint) -> bool:
        """El hueco junto a las pestañas pertenece a este widget y no a la barra."""
        bar = self.tabBar().geometry()
        return bar.left() <= position.x() <= bar.right() if self.tabBar().is_vertical() else bar.top() <= position.y() <= bar.bottom()

    def mouseDoubleClickEvent(self, event) -> None:
        if not self._in_tab_strip(event.position().toPoint()):
            super().mouseDoubleClickEvent(event)
        elif self.settings["double_click"]:
            self.open_new_tab("double_click")

    def open_folders_in_tabs(self, folders: list[str]) -> None:
        for folder in folders:
            self.add_tab(folder)

    # Soltar una carpeta en el hueco de las pestañas o en el botón de nueva pestaña.
    def dragEnterEvent(self, event) -> None:
        if self._in_tab_strip(event.position().toPoint()) and folders_in(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        folders = folders_in(event.mimeData()) if self._in_tab_strip(event.position().toPoint()) else []
        if not folders:
            event.ignore()
            return
        accept_drop(event)
        QTimer.singleShot(0, lambda: self.open_folders_in_tabs(folders))

    def _tab_double_clicked(self, index: int) -> None:
        if index >= 0 and self.settings["double_click_close"]:
            self.close_tab(index)
        elif self.settings["double_click"]:
            self.open_new_tab("double_click")

    @property
    def current(self) -> FileView | None:
        return self.currentWidget()

    def views(self) -> list[FileView]:
        return [self.widget(i) for i in range(self.count())]

    def add_tab(self, path: str, activate: bool = True) -> FileView:
        view = self.window_.create_view()
        title = _tab_title(path, self.settings["show_drive"])
        if activate and self.settings["open_next_to_active"] and self.count():
            index = self.insertTab(self.currentIndex() + 1, view, themed("folder"), title)
        else:
            index = self.addTab(view, themed("folder"), title)
        view.navigate(path)
        if activate:
            self.setCurrentIndex(index)
            view.focus_list()
        self._after_tabs_changed()
        return view

    def _add_close_button(self, index: int) -> None:
        small = self.settings["small_close"]
        close = QToolButton()
        close.setAutoRaise(True)
        close.setIcon(themed("close"))
        close.setIconSize(QSize(8, 8) if small else QSize(10, 10))
        close.setFixedSize(12, 12) if small else close.setFixedSize(16, 16)
        close.setToolTip(tr("Cerrar pestaña (Ctrl+W)"))
        close.clicked.connect(lambda _=False, v=self.widget(index): self.close_tab(self.indexOf(v)))
        self.tabBar().setTabButton(index, self.tabBar().ButtonPosition.RightSide, close)

    def close_tab(self, index: int) -> None:
        if index < 0:
            return
        if self.count() <= 1:
            if self.settings["close_window_with_last_tab"]:
                self.window_.last_tab_closed(self)
            return
        view = self.widget(index)
        view.dispose()
        self.removeTab(index)
        view.deleteLater()
        self._after_tabs_changed()

    def close_all_tabs(self) -> None:
        """Al cerrar el panel entero. No pasa por `close_tab`, que nunca deja un panel sin pestañas."""
        for index in reversed(range(self.count())):
            view = self.widget(index)
            view.dispose()
            self.removeTab(index)
            view.deleteLater()

    def _tab_text(self, index: int) -> str:
        view = self.widget(index)
        name = view.tab_name or _tab_title(view.path, self.settings["show_drive"])
        if view.tab_name and remote.is_remote(view.path):
            name = f"{name} ({remote_host(view.path)})"
        return f"{index + 1} {name}" if self.settings["show_index"] else name

    def _tab_icon(self, view: FileView):
        if remote.is_remote(view.path):
            return themed("remote")  # aunque no se muestren iconos: es lo que avisa de que no es del equipo
        if not self.settings["show_icons"]:
            return QIcon()
        return themed("search" if is_results(view.path) else "gomycomputer" if view.path == COMPUTER_PATH else "folder")

    def update_leds(self, lit: bool) -> None:
        """El foquito de las pestañas conectadas a un servidor. Sin conexión viva no hay foquito: Qt reserva el sitio de un botón aunque esté oculto."""
        bar = self.tabBar()
        side = bar.ButtonPosition.LeftSide
        for index in range(self.count()):
            path = self.widget(index).path
            server = remote.server_of(path) if remote.is_remote(path) else ""
            led = bar.tabButton(index, side)
            if not isinstance(led, ConnectionLed):
                led = None
            if server and remote.is_alive(server):
                if led is None or led.server != server:
                    led = ConnectionLed(server)
                    led.setToolTip(tr("Conectado con {server}", server=server))
                    bar.setTabButton(index, side, led)
                led.set_lit(lit)
            elif led is not None:
                bar.setTabButton(index, side, None)
                led.deleteLater()

    def retitle(self, view: FileView) -> None:
        index = self.indexOf(view)
        if index >= 0:
            self.setTabText(index, self._tab_text(index))
            self.setTabToolTip(index, tr("En el servidor: {place}", place=location_label(view.path)) if remote.is_remote(view.path) else location_label(view.path))
            self.setTabIcon(index, self._tab_icon(view))
            QTimer.singleShot(0, self._place_add_button)

    def _after_tabs_changed(self) -> None:
        self._update_close_buttons()
        if self.settings["show_index"]:  # abrir, cerrar o mover una pestaña cambia los números
            for index in range(self.count()):
                self.setTabText(index, self._tab_text(index))
        self.window_.update_tab_bars()
        QTimer.singleShot(0, self._place_add_button)

    def _remember_current(self, _index: int) -> None:
        view = self.current
        if view is not self._current_view:
            self._previous_view, self._current_view = self._current_view, view
        self._update_close_buttons()

    def _update_close_buttons(self) -> None:
        """Crea o quita la `x` según las opciones. Solo con el botón quitado la pestaña mide lo que su
        nombre, porque Qt reserva su sitio aunque esté oculto."""
        bar = self.tabBar()
        side = bar.ButtonPosition.RightSide
        settings = self.settings
        can_close = self.count() > 1 or settings["close_window_with_last_tab"]
        for index in range(self.count()):
            button = bar.tabButton(index, side)
            wanted = settings["show_close"] and (not settings["close_active_only"] or index == self.currentIndex())
            if button is not None and not wanted:
                bar.setTabButton(index, side, None)
                button.deleteLater()
            elif button is None and wanted:
                self._add_close_button(index)
                button = bar.tabButton(index, side)
            if wanted:
                button.setEnabled(can_close)  # la última pestaña no se puede cerrar: se ve, pero desactivado
        bar.updateGeometry()
        bar.update()
        QTimer.singleShot(0, self._place_add_button)  # la pestaña activa ocupa más: el `+` debe seguirla

    def eventFilter(self, watched, event) -> bool:
        bar = self.tabBar()
        if watched is bar:
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress:
                index = bar.index_at(event.position().toPoint())
                if event.button() == Qt.MouseButton.MiddleButton and index >= 0:
                    self.close_tab(index)
                    return True
                self._activate_pane()
                self._pressed_selected = index if event.button() == Qt.MouseButton.LeftButton and index == self.currentIndex() else -1
                self._press_position = event.position().toPoint()
            elif kind == QEvent.Type.MouseButtonRelease:
                position = event.position().toPoint()
                moved = (position - self._press_position).manhattanLength() >= QApplication.startDragDistance()
                if self._pressed_selected >= 0 and not moved and bar.index_at(position) == self._pressed_selected:
                    self._click_timer.start()  # espera: podría ser el primer clic de un doble clic
                self._pressed_selected = -1
            elif kind == QEvent.Type.MouseButtonDblClick:
                self._click_timer.stop()
        return super().eventFilter(watched, event)

    def _activate_pane(self) -> None:
        """Un clic en la barra de pestañas, también en la pestaña activa, activa el panel. Pulsar otra pestaña ya lo hace al cambiar."""
        view = self.current
        if view is not None:
            self.window_._view_activated(view)
            view.focus_list()  # si no, el teclado seguiría en el panel anterior

    def mousePressEvent(self, event) -> None:
        self._activate_pane()  # el hueco de la barra a la derecha de las pestañas
        super().mousePressEvent(event)

    def _selected_tab_clicked(self) -> None:
        action = self.settings["selected_click"]
        if action == "rename":
            self.rename_tab(self.currentIndex())
        elif action == "previous" and self._previous_view is not None and self.indexOf(self._previous_view) >= 0:
            self._activate_view(self._previous_view)

    def rename_tab(self, index: int) -> None:
        view = self.widget(index)
        name, accepted = QInputDialog.getText(self, tr("Renombrar pestaña"), tr("Nombre (vacío: el de la carpeta):"),
                                              QLineEdit.EchoMode.Normal, view.tab_name)
        if accepted:
            view.tab_name = name.strip()
            self.retitle(view)

    def _show_tab_menu(self, position: QPoint) -> None:
        index = self.tabBar().index_at(position)
        target = self.tabBar().mapToGlobal(position)
        if index >= 0 and self.settings["tab_as_folder"]:
            view = self.widget(index)
            view.folderMenu.emit(view, target)
            return
        self.tab_menu(index).exec(target)

    def tab_menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        menu.addAction(themed("plus"), tr("Abrir nueva pestaña"), lambda: self.open_new_tab("new_button"))
        if index < 0:
            self._add_customize_tabs(menu)
            return menu
        view = self.widget(index)
        menu.addAction(tr("Duplicar pestaña"), lambda: self.add_tab(view.path).copy_view_settings(view))
        menu.addAction(tr("Renombrar pestaña…"), lambda: self.rename_tab(self.indexOf(view)))
        menu.addSeparator()
        menu.addAction(themed("close"), tr("Cerrar pestaña"), lambda: self.close_tab(self.indexOf(view)))
        others = menu.addAction(tr("Cerrar las demás pestañas"), lambda: self._close_others(view))
        others.setEnabled(self.count() > 1)
        self._add_customize_tabs(menu)
        return menu

    def _add_customize_tabs(self, menu: QMenu) -> None:
        menu.addSeparator()
        menu.addAction(themed("prefs"), tr("Personalizar…"), lambda: QTimer.singleShot(0, lambda: self.window_.open_preferences(tabs=True)))

    def _close_others(self, keep: FileView) -> None:
        for view in self.views():
            if view is not keep:
                self.close_tab(self.indexOf(view))

    def apply_settings(self, settings: dict, position: QTabWidget.TabPosition) -> None:
        bar = self.tabBar()
        self.settings = bar.settings = settings
        self.setTabPosition(position)
        clips = settings["shrink"] or settings["same_size"] or settings["max_size_on"]
        self.setElideMode(Qt.TextElideMode.ElideRight if clips else Qt.TextElideMode.ElideNone)
        arrows = not settings["overflow_menu"] and not settings["multi_row"]
        if bar.property("arrows") != arrows:  # sin el menú, Qt muestra sus flechas para desplazar las pestañas
            bar.setProperty("arrows", arrows)
            bar.style().unpolish(bar)
            bar.style().polish(bar)
        side = bar.ButtonPosition.RightSide
        for index in range(self.count()):
            button = bar.tabButton(index, side)
            if button is not None:
                bar.setTabButton(index, side, None)  # se recrea con el tamaño elegido
                button.deleteLater()
            self.retitle(self.widget(index))
        self._after_tabs_changed()

    def _place_add_button(self) -> None:
        """Con sitio, el `+` va justo después de la última pestaña; sin él, al final de la barra, tras el menú »."""
        bar = self.tabBar()
        if not self.count():
            return
        settings = self.settings
        vertical = bar.is_vertical()
        shown = not bar.isHidden()
        if bar.rows_active():
            end = bar.row_end()
            bar.setMaximumSize(MAX_WIDGET_SIZE, MAX_WIDGET_SIZE)
            self.overflow_button.hide()
            self.add_button.setVisible(shown and settings["new_button"])
            self.add_button.setFixedSize(ADD_BUTTON_WIDTH, max(18, bar.row_height() - 2))
            self.add_button.move(end.x() + 3, bar.geometry().top() + end.y() + 1)
            self.add_button.raise_()
            return
        hints = [bar.tabSizeHint(index) for index in range(self.count())]
        # Se suman los tamaños porque la posición de cada pestaña depende del desplazamiento de la barra.
        used = sum(hint.height() if vertical else hint.width() for hint in hints)
        total = self.height() if vertical else self.width()
        add = shown and settings["new_button"]
        full = used + (ADD_BUTTON_WIDTH + 5 if add else 0) > total
        menu = shown and full and settings["overflow_menu"] and (not settings["shrink"] or settings["menu_when_shrunk"])
        self.add_button.setVisible(add)
        self.overflow_button.setVisible(menu)
        height = max(18, 22 if vertical else bar.height() - 2)
        self.add_button.setFixedSize(ADD_BUTTON_WIDTH, height)
        self.overflow_button.setFixedSize(OVERFLOW_BUTTON_WIDTH, height)
        reserved = (OVERFLOW_BUTTON_WIDTH + 1 if menu else 0) + (ADD_BUTTON_WIDTH + 2 if add and full else 0)
        # El sitio se reserva limitando la barra: Qt solo reserva las esquinas con las pestañas arriba.
        if vertical:
            bar.setMaximumSize(MAX_WIDGET_SIZE, total - height - 3 if reserved else MAX_WIDGET_SIZE)
            left = bar.geometry().left() + 1
            top = total - height - 1 if full else used + 3
            self.add_button.move(left, top)
            self.overflow_button.move(left + (ADD_BUTTON_WIDTH + 2 if add else 0), top)
        else:
            bar.setMaximumSize(total - reserved if reserved else MAX_WIDGET_SIZE, MAX_WIDGET_SIZE)
            top = bar.geometry().top() + 1
            self.add_button.move(total - ADD_BUTTON_WIDTH - 1 if full else used + 3, top)
            self.overflow_button.move(total - reserved, top)
        self.add_button.raise_()
        self.overflow_button.raise_()

    def hidden_tabs(self) -> list[int]:
        """Pestañas que van en el menú: las que no se ven enteras. La activa nunca lo está, porque Qt la desplaza hasta mostrarla."""
        if self.settings["menu_all_tabs"]:
            return list(range(self.count()))
        bar = self.tabBar()
        if bar.rows_active():
            return []  # en varias filas se ven todas

        def clipped(rect) -> bool:  # Qt puede pasarse un píxel
            visible = bar.rect().intersected(rect)
            return visible.width() < rect.width() - 2 or visible.height() < rect.height() - 2

        return [index for index in range(self.count()) if index != self.currentIndex() and clipped(bar.tabRect(index))]

    def overflow_menu(self) -> QMenu:
        menu = QMenu(self)
        for index in self.hidden_tabs():
            action = menu.addAction(self.tabIcon(index), self.tabText(index))
            action.triggered.connect(lambda _=False, view=self.widget(index): self._activate_view(view))
        menu.addSeparator()
        menu.addAction(themed("plus"), tr("Abrir nueva pestaña"), lambda: self.open_new_tab("new_button"))
        return menu

    def _show_overflow_menu(self) -> None:
        self.overflow_menu().exec(self.overflow_button.mapToGlobal(self.overflow_button.rect().bottomLeft()))

    def _activate_view(self, view: FileView) -> None:
        self.setCurrentWidget(view)
        view.focus_list()

    def apply_icons(self) -> None:
        self.add_button.setIcon(themed("plus"))
        bar = self.tabBar()
        for index in range(self.count()):
            self.setTabIcon(index, self._tab_icon(self.widget(index)))
            button = bar.tabButton(index, bar.ButtonPosition.RightSide)
            if button:
                button.setIcon(themed("close"))
            self.widget(index).apply_icons()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_add_button()


class MainWindow(ExtraCommands, QMainWindow):
    def __init__(self, config: Config | None = None, start_paths: list[str] | None = None) -> None:
        super().__init__()
        self.config = config or Config()
        self._temp_dirs: list[str] = []
        self.remote_files = sftp.RemoteFiles(self)
        self.remote_files.changed.connect(self._remote_copy_changed)
        self._remote_connector = sftp.RemoteConnector(self, self._report)
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._viewers: list[ImageViewer] = []
        self.nav_lock = False
        self._following = False
        self._apply_theme_globals()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1320, 820)

        collect_garbage_in_gui_thread()
        # Los paneles de más no tienen pestañas hasta que un estilo los pide: no cuestan nada.
        self.groups = [PaneGroup(self) for _pane in range(styles.MAX_PANES)]
        for group in self.groups:
            group.settings = group.tabBar().settings = self.folder_tab_settings()
        self._pane_history = list(range(styles.MAX_PANES))  # del activo al menos reciente: el destino es el anterior
        self.active_side = 0
        self.dual = "Vertical"
        self._pane_shape: tuple | None = tuple(range(styles.MAX_PANES))
        self._pane_splitters: list[tuple[QSplitter, tuple]] = []
        self._pane_extents: dict[int, tuple[float, float]] | None = None  # al dividir, lo que ocupa cada panel: los demás no deben cambiar
        self._panes_to_balance = False
        self.double_tree = False
        self._terminal_position: str | None = None  # dónde pone el estilo la terminal de cada panel. Sin indicarlo, según los paneles
        self._style_terminals: set[int] = set()  # las que abrió el estilo: se cierran con él, y las abiertas a mano no
        # Los paneles que solo muestran su terminal, con lo que tenían antes: si su terminal seguía al panel y si mostraban su árbol.
        self._terminal_only: dict[int, tuple[bool, bool]] = {}
        self._style_terminal_panes: set[int] = set()
        self._style_sizing: set[int] = set()
        self._applied_style = ""
        self._docks_viewer_above = True
        self._docks_to_balance = False
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        # Cada lado es su panel con su terminal y, con los estilos de doble árbol, su propio árbol a la izquierda.
        self.sides = [FramedSplitter(Qt.Orientation.Horizontal) for _group in self.groups]
        self.pane_areas = [QSplitter(Qt.Orientation.Vertical) for _group in self.groups]
        for side, area, group in zip(self.sides, self.pane_areas, self.groups):
            area.addWidget(group)
            area.setChildrenCollapsible(False)
            area.setHandleWidth(3)
            side.addWidget(area)
            side.setChildrenCollapsible(False)
            side.setHandleWidth(3)
            self.splitter.addWidget(side)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)
        for side in self.sides[2:]:
            side.hide()
        # Las partes de una disposición que no es una simple fila: con n paneles, como mucho n - 2, porque la de fuera es la propia ventana.
        self._pane_nests = [QSplitter() for _nest in range(styles.MAX_PANES - 2)]
        for nest in self._pane_nests:
            nest.setChildrenCollapsible(False)
            nest.setHandleWidth(3)
        # Los paneles juntos y la terminal general, que ocupa el espacio de ambos.
        self.work_area = QSplitter(Qt.Orientation.Vertical)
        self.work_area.addWidget(self.splitter)
        self.work_area.setChildrenCollapsible(False)
        self.work_area.setHandleWidth(3)
        self.terminal_areas = [*self.pane_areas, self.work_area]

        self.command_line = QLineEdit()
        self.command_line.setPlaceholderText(tr("Comando: Go \"ruta\", Copy MOVE, Select PATTERN=*.jpg, Run programa {filepath}…"))
        self.command_line.returnPressed.connect(self._command_line_entered)
        self.command_line.hide()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.work_area, 1)
        layout.addWidget(self.command_line)
        self.setCentralWidget(central)

        self._build_docks()
        self.main_menu = QMenuBar()
        self.main_menu.setNativeMenuBar(False)  # dentro de una barra movible, nunca como menú global del escritorio
        self.main_menu.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._compare_menu_action: QAction | None = None
        self._compare_bar_actions: list[QAction] = []
        self._open_media_actions: list[tuple[QAction, str]] = []
        self._build_menus()
        horizontal = Qt.ToolBarArea.TopToolBarArea | Qt.ToolBarArea.BottomToolBarArea
        # El menú, los estilos y la ruta solo tienen sentido en horizontal; la ruta necesita ancho.
        self.menu_tools = self._make_bar("menu-bar", tr("Menú"), horizontal, floatable=False)
        self.menu_tools.addWidget(self.main_menu)
        self.menu_tools.toggleViewAction().setEnabled(False)  # sin menú no se podría volver a mostrar nada
        self.addToolBarBreak()
        self.toolbar = self._make_bar("main-toolbar", tr("Barra de herramientas"))
        self.addToolBarBreak()
        self.style_bar = self._make_bar("style-bar", tr("Estilos"), horizontal, floatable=False)
        self.addToolBarBreak()
        self.location_bar = self._make_bar("location-bar", tr("Localización"), horizontal, floatable=False)
        self.addToolBarBreak()
        self.favorites_bar = self._make_bar("favorites-bar", tr("Barra de favoritos"))
        self.favorites_bar.setIconSize(QSize(16, 16))
        self.favorites_bar.setVisible(bool(self.config.get("show_favorites_bar", False)))
        self._apply_bar_lock()
        self._build_toolbar()
        self._build_style_bar()
        self._build_location_bar()
        self._rebuild_favorites_bar()
        # La barra de estado general se ve o no según las preferencias, nunca a ratos: mostrarla y ocultarla movería toda la ventana.
        # Sin ella, sus avisos van en la fila del panel activo.
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().messageChanged.connect(self._show_notice)
        self._build_general_status()
        self._restore_session(start_paths or [])
        self._check_smart_folders()
        if self.config.warning:
            self.log.log(self.config.warning)
            self.statusBar().showMessage(self.config.warning, 10000)

    def _show_notice(self, text: str) -> None:
        general = self._general_status_on()
        for view in self.all_views():
            view.status.set_notice(text if view is self.source and not general else "")

    # ------------------------------------------------------------------ barra de estado general
    def _general_status_on(self) -> bool:
        return self.config.get("general_status_bar", True) is not False

    def _build_general_status(self) -> None:
        """Abajo de la ventana: el archivo bajo el ratón o lo seleccionado en el panel activo, el espacio libre, y los avisos."""
        bar = self.statusBar()
        self.general_info = QLabel()
        self.general_info.setTextFormat(Qt.TextFormat.PlainText)
        self.general_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # un nombre largo no ensancha la ventana
        self.general_free = QLabel()
        self.general_meter = DiskMeter()
        self.general_flags = QLabel()  # filtro activo, ocultos visibles
        self.general_jobs = QLabel()  # copias y movimientos en curso
        self._media_facts: dict[tuple, str] = {}  # (ruta, fecha): dimensiones o duración
        self._facts_pending: set[tuple] = set()
        bar.addWidget(self.general_info, 1)
        bar.addPermanentWidget(self.general_jobs)
        bar.addPermanentWidget(self.general_flags)
        bar.addPermanentWidget(self.general_free)
        bar.addPermanentWidget(self.general_meter)
        self._apply_general_status()

    def _apply_general_status(self) -> None:
        self.statusBar().setVisible(self._general_status_on())
        self._update_free_space_panes()
        self._show_notice(self.statusBar().currentMessage())
        self._update_general_status()

    def _entry_facts(self, entry) -> list[str]:
        """Nombre, tipo, tamaño, fecha y, de una imagen, sus dimensiones; de un vídeo, sus dimensiones y su duración; de un audio, su duración.
        Lo último se lee en segundo plano."""
        size = "" if entry.is_dir and entry.dir_size is None else format_size(entry.effective_size)
        facts = [entry.display_name, entry.type_label, size, format_time(entry.mtime, self.config.get("friendly_dates", "off"))]
        key = (entry.path, entry.mtime)
        local = not (entry.is_dir or entry.in_archive or remote.is_remote(entry.path))
        if key in self._media_facts:
            facts.append(self._media_facts[key])
        elif local and (entry.extension in IMAGE_EXTENSIONS or media_info.is_media_extension(entry.extension)) and key not in self._facts_pending:
            self._facts_pending.add(key)
            path, extension, ffmpeg_dir = entry.path, entry.extension, self.config.get("ffmpeg_dir", "")

            def work(_sink) -> str:
                if extension in IMAGE_EXTENSIONS:
                    width, height, _depth = read_image_info(path)
                    return f"{width} × {height}" if width else ""
                info = media_info.read_media_info(path, ffmpeg_dir)
                if info is None:
                    return ""
                size = f"{info.width} × {info.height}" if info.width and info.height else ""
                return " · ".join(part for part in (size, media_info.format_duration(info.duration) if info.duration else "") if part)

            def done(text: str) -> None:
                self._facts_pending.discard(key)
                if len(self._media_facts) > 5000:
                    self._media_facts.clear()
                self._media_facts[key] = text
                if text:
                    self._update_general_status()

            run_bg(work, done, lambda _exc: self._facts_pending.discard(key))
        return facts

    def _update_jobs_status(self) -> None:
        if hasattr(self, "general_jobs"):
            text = self.jobs.summary()
            self.general_jobs.setText(text)
            self.general_jobs.setVisible(bool(text))

    def _update_free_space_panes(self) -> None:
        """Con la barra general, el espacio libre del panel activo va en ella; un panel con una carpeta de otro disco muestra el suyo."""
        on = self._general_status_on()
        active = self.source.model.device if getattr(self, "groups", None) and self.groups[self.active_side].current is not None else None
        mode = self.config.get("pane_free_space", "other_disk")
        for view in self.all_views():
            other_disk = view.model.device is None or view.model.device != active
            view.set_free_space_shown(not on or mode == "always" or (mode != "never" and other_disk))

    def _update_general_status(self) -> None:
        if not hasattr(self, "general_info") or not self._general_status_on() or not self.groups or self.groups[self.active_side].current is None:
            return
        view = self.source
        entry = getattr(self, "_hover_entry", None)
        selected = view.selected_entries()
        if entry is None and len(selected) == 1:
            entry = selected[0]  # uno solo seleccionado: sus datos, como el de bajo el ratón
        if entry is not None:
            self.general_info.setText(" · ".join(part for part in self._entry_facts(entry) if part))
        else:
            shown = selected or view.model.entries
            folders = sum(1 for item in shown if item.is_dir)
            size = format_size(sum(item.effective_size for item in shown))
            counts = tr("{folders} carpetas y {files} archivos, {size}", folders=folders, files=len(shown) - folders, size=size)
            self.general_info.setText(tr("Seleccionados: {counts}", counts=counts) if selected else counts)
        flags = []
        if view.model.filter_text or view.model.content_types:
            flags.append(tr("Filtro: {filter}", filter=", ".join(part for part in (view.model.filter_text, *view.model.content_types) if part)))
        if view.show_hidden:
            flags.append(tr("Ocultos visibles"))
        self.general_flags.setText(" · ".join(flags))
        self.general_flags.setVisible(bool(flags))
        free, total = view.model.free_space, view.model.total_space
        if free is not None and total:
            self.general_free.setText(tr("{free} libres ({percent} %) de {total}", free=format_size(free), percent=round(free * 100 / total), total=format_size(total)))
        else:
            self.general_free.setText(tr("{value} libres", value=format_size(free)) if free is not None else "")
        self.general_meter.set_usage(free, view.model.total_space)
        self.general_meter.setVisible(free is not None)

    # ------------------------------------------------------------------ tema
    def _wanted_theme(self) -> tuple[theme.Theme, str]:
        setting = self.config.get("theme", "dark")
        theme.request_color_scheme(setting)
        base = theme.resolve(setting)
        current = theme.with_pane_colors(base, pane_colors.scheme_colors(self.config.get("pane_color_scheme"), self.config.get("pane_color_customs"), base.name))
        current = replace(current, pale_path=bool(self.config.get(f"pane_path_pale_{base.name}", base.pale_path)))
        return current, theme.stylesheet(current, self.config.get("dest_tint", True))

    def _apply_theme_globals(self) -> None:
        current, sheet = self._wanted_theme()
        theme.set_current(current)
        theme.match_icon_theme(current.name == "dark")
        app = QApplication.instance()
        app.setPalette(theme.palette(current))
        if app.styleSheet() != sheet:  # aplicarla vuelve a pulir todos los widgets abiertos
            app.setStyleSheet(sheet)

    def _show_busy_notice(self, text: str) -> QLabel:
        """Lo que sigue bloquea la ventana unos segundos: se dibuja ya, porque después no habrá ocasión."""
        host = QApplication.activeModalWidget() or self
        notice = QLabel(text, host)
        notice.setObjectName("busyNotice")
        notice.adjustSize()
        notice.move((host.width() - notice.width()) // 2, (host.height() - notice.height()) // 2)
        notice.show()
        notice.raise_()
        notice.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        return notice

    def apply_theme(self) -> None:
        notice = None
        if self.isVisible() and self._wanted_theme()[1] != QApplication.instance().styleSheet():
            notice = self._show_busy_notice(tr("Aplicando el tema…"))
        try:
            self._apply_theme_everywhere()
        finally:
            if notice is not None:
                QApplication.restoreOverrideCursor()
                notice.hide()
                notice.deleteLater()

    def _apply_theme_everywhere(self) -> None:
        self._apply_theme_globals()
        self._build_toolbar()
        self._build_style_bar()
        self._refresh_location_icons()
        for group in self.groups:
            group.apply_icons()
        for button, icon in self._dock_buttons:
            button.setIcon(themed(icon))
        self.manage_sessions_button.setIcon(themed("prefs"))
        for bar in self._all_bars():
            self._dress_overflow_button(bar)
        for action, icon in self._menu_icons:
            action.setIcon(themed(icon))
        for tree in self.all_trees():
            tree.apply_icons()
        for terminal in self.terminals:
            terminal.apply_theme()
        self.preview.apply_icons()
        for viewer in self._viewers:
            if _alive(viewer):
                viewer.apply_icons()
        for view in self.all_views():
            view.model.layoutChanged.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # La ventana principal nace con la del sistema aunque Qt ya sepa el tema elegido. A partir de ahí, Qt la cambia con el tema.
        platform_utils.set_dark_title_bar(int(self.winId()), theme.current().name == "dark")

    # ------------------------------------------------------------------ construcción
    def menuBar(self) -> QMenuBar:
        """El menú vive dentro de una barra movible y no en el sitio fijo de la ventana."""
        return self.main_menu

    def _make_bar(self, name: str, title: str, areas=Qt.ToolBarArea.AllToolBarAreas, floatable: bool = True) -> QToolBar:
        bar = QToolBar(title)
        bar.setObjectName(name)
        bar.setAllowedAreas(areas)
        bar.setFloatable(floatable)
        bar.setIconSize(QSize(18, 18))
        bar.setStyle(_bar_style())
        self._dress_overflow_button(bar)
        bar.orientationChanged.connect(lambda _orientation: self._dress_overflow_button(bar))  # la barra pone su icono al girar
        self.addToolBar(bar)
        return bar

    @staticmethod
    def _dress_overflow_button(bar: QToolBar) -> None:
        """El botón » de una barra. Se busca cada vez: lo crea la barra, y puede rehacerlo."""
        overflow = bar.findChild(QToolButton, "qt_toolbar_ext_button")
        if overflow is not None:
            overflow.setIcon(themed("toolbarmore"))
            overflow.setIconSize(QSize(16, 16))
            overflow.setToolTip(tr("Mostrar lo que no cabe en la barra"))

    def _all_bars(self) -> list[QToolBar]:
        return [self.menu_tools, self.toolbar, self.style_bar, self.location_bar, self.favorites_bar]

    def _apply_bar_lock(self) -> None:
        locked = bool(self.config.get("lock_bars", False))
        for bar in self._all_bars():
            bar.setMovable(not locked)

    def _set_lockbars(self, value: str) -> None:
        self.config["lock_bars"] = _turn(value, bool(self.config.get("lock_bars", False)))
        self._apply_bar_lock()

    def _set_hoverinfo(self, value: str) -> None:
        self.config["hover_info"] = _turn(value, bool(self.config.get("hover_info", True)))
        for view in self.all_views():
            view.hover_enabled = self.config["hover_info"]
            view.hover_card.dismiss()

    def _set_applogo(self, value: str) -> None:
        self.config["show_app_icon"] = _turn(value, bool(self.config.get("show_app_icon", True)))
        self._apply_app_logo_visibility()

    def _apply_app_logo_visibility(self) -> None:
        if hasattr(self, "app_logo_button"):
            self.app_logo_button.setVisible(bool(self.config.get("show_app_icon", True)))

    def _show_app_logo_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(app_icon(), tr("Acerca de {APP_NAME}…", APP_NAME=APP_NAME), lambda: self.run_command("Help ABOUT"))
        menu.addSeparator()
        menu.addAction(tr("Ocultar este icono"), self._hide_app_logo)
        menu.exec(self.app_logo_button.mapToGlobal(pos))

    def _hide_app_logo(self) -> None:
        self.config["show_app_icon"] = False
        self._apply_app_logo_visibility()

    def createPopupMenu(self) -> QMenu:
        """Clic derecho en una barra: mostrar u ocultar barras y paneles, y bloquearlas."""
        menu = super().createPopupMenu() or QMenu(self)
        menu.addSeparator()
        lock = menu.addAction(tr("Bloquear las barras"))
        lock.setCheckable(True)
        lock.setChecked(bool(self.config.get("lock_bars", False)))
        lock.triggered.connect(lambda checked: self.run_command(f"Set LOCKBARS={'On' if checked else 'Off'}"))
        bar = self._bar_under_cursor()
        menu.addAction(themed("prefs"), tr("Personalizar…"), lambda: self.customize_bars(bar))
        return menu

    def _bar_under_cursor(self) -> str:
        """La barra de botones sobre la que se pulsó. Las demás llevan a la barra de herramientas, la primera de la lista."""
        bars = ((self.toolbar, bar_buttons.TOOLBAR), (self.style_bar, bar_buttons.STYLES), (self.location_bar, bar_buttons.LOCATION))
        widget = QApplication.widgetAt(QCursor.pos())
        while widget is not None:
            for bar, key in bars:
                if widget is bar:
                    return key
            widget = widget.parentWidget()
        return bar_buttons.TOOLBAR

    def _build_docks(self) -> None:
        self._dock_buttons: list[tuple[QToolButton, str]] = []
        self.tree = FolderTree(bool(self.config.get("show_hidden", False)))
        self.tree.pathActivated.connect(self._tree_path_activated)
        self.tree.filesDropped.connect(lambda paths, target, move: self._files_dropped(self.source, paths, target, move))
        self.tree.filesDroppedMenu.connect(lambda paths, target, move: self._files_dropped_menu(self.source, paths, target, move))
        self.tree.deviceActivated.connect(lambda device: self.open_device(device, self.source))
        self.tree.deviceMenuRequested.connect(lambda device, where: self._device_menu(device, lambda: self.source).exec(where))
        self.tree_dock = self._dock(tr("Árbol de Carpetas"), "tree", self.tree, Qt.DockWidgetArea.LeftDockWidgetArea,
                                    [("collapse", tr("Plegar todo"), self.tree.collapseAll)])
        self.pane_trees: list[FolderTree] = []
        self.tree_panels: list[QWidget] = []
        self._pane_tree_paths: dict[int, str] = {}
        self.terminals: list[TerminalView] = []
        self.terminal_panels: list[QWidget] = []
        self.terminal_follow_buttons: list[QToolButton] = []
        self.terminal_files_buttons: list[QToolButton] = []
        self.terminal_close_buttons: list[QToolButton] = []
        self.terminal_titles: list[QLabel] = []
        self.git_bars: list[GitBar] = []
        self._compare_timer = QTimer(self, singleShot=True, interval=COMPARE_STATE_DELAY_MS)
        self._compare_timer.timeout.connect(self._update_compare_state)
        self._pane_branch_checks: set = set()
        self._pane_branch_timer = QTimer(self, interval=PANE_BRANCH_POLL_MS)
        self._pane_branch_timer.timeout.connect(self._pane_branch_tick)
        self._pane_branch_timer.start()
        self._hover_entry = None
        self._hover_preview_entry = None
        self._hover_preview_timer = QTimer(self, singleShot=True, interval=HOVER_PREVIEW_MS)
        self._hover_preview_timer.timeout.connect(self._show_hover_preview)
        self._exit_asked = ""  # mientras se sale por el menú: "default" pregunta según las preferencias, "always" siempre
        self._led_lit = True
        self._led_timer = QTimer(self, interval=BLINK_MS)  # el foquito de las conexiones: parpadea, y desaparece si se pierden
        self._led_timer.timeout.connect(self._led_tick)
        self._led_timer.start()
        self._devices: list[devices.Device] = []
        self._devices_reading = False
        self._devices_timer = QTimer(self, interval=DEVICES_POLL_MS)
        self._devices_timer.timeout.connect(self._refresh_devices)
        self._udisks = udisks.UDisks(self)
        if not has_computer_view() and devices.is_supported():  # en Windows, las unidades ya están en «Este equipo»
            self._udisks.bus()  # se abre ya: si luego algún programa acapara las conexiones del sistema, esta sigue sirviendo
            self._devices_timer.start()
            QTimer.singleShot(0, self._refresh_devices)
        self._terminal_start_folders: dict[int, str] = {}
        self.terminal_shell_boxes: list[ShellBox] = []
        self.terminal_ssh_buttons: list[QToolButton] = []
        self.terminal_ssh_off_buttons: list[QToolButton] = []
        self.terminal_leds: list[ConnectionLed] = []
        self._terminal_choices: dict[int, str] = {}  # el intérprete elegido en la propia terminal. Las demás usan el de las preferencias
        self._terminal_pinned: set[int] = set()  # elegidas en su lista: no cambian entre el equipo y un servidor al cambiar su panel
        self._terminal_preference = self._terminal_preference_now()
        self._terminals_sized: dict[int, object] = {}
        self._general_docked = False
        for side in range(len(self.groups)):
            self._build_pane_tree(side)
            self._build_terminal(side)
        self._build_terminal(GENERAL_TERMINAL)

        self.preview = PreviewPane(self.config)
        self.preview.editRequested.connect(self.open_markdown_editor)
        self.preview.jsonRequested.connect(self.open_json_viewer)
        self.viewer_dock = self._dock(tr("Visor"), "viewer", self.preview, Qt.DockWidgetArea.RightDockWidgetArea)
        self.viewer_dock.visibilityChanged.connect(lambda _v: self._update_preview())
        self.metadata = MetadataPanel(self, config=self.config)
        self.metadata.exiftool_dir = self.config.get("exiftool_dir", "")
        self.meta_dock = self._dock(tr("Metadatos"), "metadata", self.metadata, Qt.DockWidgetArea.RightDockWidgetArea)
        self.meta_dock.visibilityChanged.connect(lambda _v: self._update_preview())
        self.meta_dock.hide()

        self.preview.autoplay_allowed = lambda: not self._video_viewer_open()
        self.preview.customize_controls = lambda: self.customize_bars(bar_buttons.PLAYER_CONTROLS)
        self.jobs = JobsPanel()
        self._collision_asker = GuiAsker(lambda src, dest: self._ask_collision(src, dest), self)
        self.jobs.aboutToStart.connect(self.release_media)
        self.jobs.jobFinished.connect(self._job_finished)
        self.jobs.progressChanged.connect(self._update_jobs_status)
        self.log = LogPanel()
        self.find_panel = FindPanel(self._source_path_for_find, lambda: self.dest.path if self.dest is not None else "", self.config)
        self.find_panel.get_open_tabs = lambda: [view.path for view in self.all_views()]
        self.find_panel.columnsChanged.connect(self._place_find_panel)
        self.find_panel.showResults.connect(self._show_find_results)
        self.find_panel.resultsChanged.connect(self._find_results_changed)
        self.find_panel.searchFinished.connect(lambda _collection: self._refresh_find_results())
        self._results_timer = QTimer(self, singleShot=True, interval=RESULTS_REFRESH_MS)
        self._results_timer.timeout.connect(self._refresh_find_results)
        self.sync_panel = SyncPanel(self._sync_pair)
        self.dupe_panel = DupePanel(lambda: [self.source.path], move_to_trash)
        for panel in (self.sync_panel, self.dupe_panel):
            panel.navigateTo.connect(self._navigate_focus)
        self.sync_panel.runJob.connect(self.jobs.start)
        self.dupe_panel.runJob.connect(self.jobs.start)
        self.utility = UtilityPanel(self.jobs, self.find_panel, self.sync_panel, self.dupe_panel, self.log)
        self.utility.setMinimumWidth(240)
        self.utility_dock = self._dock(tr("Utilidades"), "utility", self.utility, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.utility_dock.hide()
        self.find_window: FindWindow | None = None
        self._place_find_panel()
        self._build_general_dock()
        self.resizeDocks([self.tree_dock], [280], Qt.Orientation.Horizontal)

    def _build_general_dock(self) -> None:
        """Donde va la terminal general cuando ocupa todo el ancho: la zona inferior abarca también por debajo de los paneles laterales."""
        dock = QDockWidget(tr("Terminal general"), self)
        dock.setObjectName("general-terminal")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        dock.setTitleBarWidget(QWidget())  # la terminal ya lleva la suya
        dock.toggleViewAction().setVisible(False)  # se muestra y oculta con la terminal, y vacío no serviría de nada
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock, Qt.Orientation.Vertical)
        dock.hide()
        self.general_dock = dock

    def _build_pane_tree(self, side: int) -> None:
        """El árbol propio de un lado, para los estilos de doble árbol. Actúa siempre sobre el panel de su lado."""
        tree = FolderTree(bool(self.config.get("show_hidden", False)))
        # Busca su número cada vez: al cerrar otro panel puede cambiar.
        tree.pathActivated.connect(lambda path: self._pane_tree_activated(self.pane_trees.index(tree), path))
        tree.filesDropped.connect(lambda paths, target, move: self._files_dropped(self.groups[self.pane_trees.index(tree)].current, paths, target, move))
        tree.filesDroppedMenu.connect(lambda paths, target, move: self._files_dropped_menu(self.groups[self.pane_trees.index(tree)].current, paths, target, move))
        pane = lambda: self.groups[self.pane_trees.index(tree)].current  # noqa: E731
        tree.deviceActivated.connect(lambda device: self.open_device(device, pane()))
        tree.deviceMenuRequested.connect(lambda device, where: self._device_menu(device, pane).exec(where))
        tree.set_devices(self._devices)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._title_bar(tr("Árbol de Carpetas"), [("collapse", tr("Plegar todo"), tree.collapseAll), ("close", tr("Ocultar"), panel.hide)]))
        layout.addWidget(tree, 1)
        panel.hide()
        self.sides[side].insertWidget(0, panel)
        self.sides[side].setStretchFactor(0, 0)
        self.sides[side].setStretchFactor(1, 1)
        self.pane_trees.append(tree)
        self.tree_panels.append(panel)

    def _build_terminal(self, index: int) -> None:
        """Una terminal: la de un panel ocupa el sitio libre junto a él y la general el de ambos. Arranca la primera vez que se muestra."""
        general = index == GENERAL_TERMINAL
        def number() -> int:
            """Se busca cada vez: al cerrar otro panel puede cambiar."""
            return self.terminals.index(terminal)

        terminal = TerminalView(lambda: self._terminal_folder(number()), lambda: self._chosen_shell(number()))
        if not general:
            terminal.activated.connect(lambda: self._view_activated(self.groups[number()].current))
        terminal.started.connect(lambda: self._terminal_started(number()))
        terminal.finished.connect(lambda: self._show_terminal(number(), False))
        terminal.failed.connect(lambda message: self._terminal_failed(number(), message))
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = self._title_bar(tr("Terminal general") if general else tr("Terminal"), [])
        title = bar.layout().itemAt(0).widget()
        title.base_text = title.text()
        files = self._title_button("filespane", tr("Volver a mostrar los archivos de este panel"), lambda: self._set_terminal_only(number(), False))
        files.hide()  # solo en un panel que muestra únicamente su terminal
        close = self._title_button("close", tr("Ocultar"), lambda: self._terminal_close_clicked(number()))
        bar.layout().addWidget(files)
        bar.layout().addWidget(close)
        follow = self._title_button("shortcut", "", lambda: self._set_terminal_follow(number(), not terminal.following))
        follow.setCheckable(True)
        follow.setChecked(True)
        box = ShellBox()
        box.setMaximumWidth(SHELL_BOX_MAX_WIDTH)  # el nombre de algunos es larguísimo: la barra de git necesita ese sitio
        box.setToolTip(tr("Intérprete de esta terminal. Al cambiarlo se queda en la misma carpeta"))
        box.activated.connect(lambda row: self._terminal_shell_chosen(number(), row))
        ssh = self._title_button("remote", tr("Conectar esta terminal a un servidor por SSH"),
                                 lambda: self.build_ssh_menu(number()).exec(QCursor.pos()))
        ssh_off = self._title_button("disconnect", tr("Desconectar esta terminal del servidor"), lambda: self.disconnect_terminal(number()))
        ssh_off.hide()  # solo conectada
        led = ConnectionLed("")
        led.hide()  # solo con la conexión SSH viva
        bar.layout().insertWidget(1, led)
        bar.layout().insertWidget(2, box)
        bar.layout().insertWidget(3, ssh)
        bar.layout().insertWidget(4, ssh_off)
        bar.layout().insertWidget(5, follow)
        git = GitBar(terminal, self.config, lambda text: self.statusBar().showMessage(text, 6000))
        bar.layout().setStretch(0, 0)  # el título, a su ancho. El hueco lo ocupa la barra de git, también cuando está vacía
        bar.layout().insertWidget(6, git, 1)
        self.git_bars.append(git)
        layout.addWidget(bar)
        layout.addWidget(terminal, 1)
        panel.hide()
        area = self.terminal_areas[index]
        area.addWidget(panel)
        area.setStretchFactor(0, 1)
        area.setStretchFactor(1, 0)
        self.terminals.append(terminal)
        self.terminal_panels.append(panel)
        self.terminal_follow_buttons.append(follow)
        self.terminal_files_buttons.append(files)
        self.terminal_close_buttons.append(close)
        self.terminal_titles.append(title)
        self.terminal_shell_boxes.append(box)
        self.terminal_ssh_buttons.append(ssh)
        self.terminal_ssh_off_buttons.append(ssh_off)
        self.terminal_leds.append(led)
        self._refresh_follow_button(index)

    def _chosen_shell(self, index: int | None = None) -> shells.Shell:
        choice = self._terminal_choices.get(index) or ""
        if choice.startswith(SSH_CHOICE):
            choice = ""  # mientras conecta con el servidor, o si no se pudo, el del equipo
        return shells.chosen_shell(choice or self.config.get("terminal_shell", ""), self.config.get("terminal_command", ""))

    def _terminal_preference_now(self) -> tuple:
        return self.config.get("terminal_shell", ""), self.config.get("terminal_command", "")

    def _terminal_started(self, index: int) -> None:
        self._refresh_follow_button(index)
        self._refresh_shell_box(index)
        server, title = self.terminals[index].server, self.terminal_titles[index]
        title.setText(f"{title.base_text} ({remote.server_parts(server)[1]})" if server else title.base_text)
        title.setToolTip(tr("En el servidor {server}", server=server) if server else "")

    def _refresh_shell_box(self, index: int) -> None:
        """Muestra lo que se está ejecutando. Conectada a un servidor, solo él: no se cambia en la lista, sino desconectándola.
        Puede que el intérprete ya no esté en la lista: se cambiaron las preferencias con ella abierta."""
        terminal, box = self.terminals[index], self.terminal_shell_boxes[index]
        server = terminal.server
        self.terminal_ssh_buttons[index].setEnabled(not server)
        self.terminal_ssh_off_buttons[index].setVisible(bool(server))
        box.clear()
        if server:
            choice = self._terminal_choices.get(index) or ""
            # Elegida con su botón, el perfil elegido; abierta al conectar su panel, el perfil de ese servidor, si lo hay.
            name = choice[len(SSH_CHOICE):] if choice.startswith(SSH_CHOICE) else next(
                (profile.name for profile in sftp.profiles(self.config) if profile.server == server), "")
            box.addItem(f"SSH: {name or remote.server_parts(server)[1]}", "ssh")
            box.setEnabled(False)
            box.setToolTip(tr("Conectada por SSH a {server}. Para usar otro intérprete, desconéctala", server=server))
            return
        box.setEnabled(True)
        box.setToolTip(tr("Intérprete de esta terminal. Al cambiarlo se queda en la misma carpeta"))
        choices = shells.available_shells()
        command = self.config.get("terminal_command", "")
        if command:
            try:
                choices.append(shells.custom_shell(command))
            except OSError:
                pass  # se avisará si llega a elegirse
        running = terminal.running_shell
        if running is not None and running is not remote.SSH_SHELL and all(shell.key != running.key for shell in choices):
            choices.append(running)
        for shell in choices:
            box.addItem(shell.label, shell.key)
        choice = self._terminal_choices.get(index) or ""
        local_choice = "" if choice.startswith(SSH_CHOICE) else choice
        shown = running.key if running is not None else local_choice or self.config.get("terminal_shell", "")
        box.setCurrentIndex(max(0, box.findData(shown)))

    def _terminal_shell_chosen(self, index: int, row: int) -> None:
        terminal, key = self.terminals[index], self.terminal_shell_boxes[index].itemData(row)
        current = terminal.running_shell.key if terminal.running_shell else None
        if key and key != "ssh" and key != current and not terminal.server and self._can_close_terminal_program(index, tr("Al cambiar de intérprete se cerrará")):
            self._terminal_pinned.add(index)  # lo ha elegido el usuario: su panel ya no la cambia de sitio
            self._switch_terminal_shell(index, key)
        self._refresh_shell_box(index)
        terminal.setFocus()

    def _can_close_terminal_program(self, index: int, what: str) -> bool:
        session = self.terminals[index].session
        return session is None or session.shell_is_idle() or self._confirm(tr("En esta terminal hay un programa en marcha. {what}. ¿Continuar?", what=what), True)

    def build_ssh_menu(self, index: int) -> QMenu:
        """Los perfiles con los que se puede abrir la terminal en un servidor, como el botón de conectar de un panel."""
        menu = QMenu(self)
        terminal = self.terminals[index]
        number = lambda: self.terminals.index(terminal)  # noqa: E731 - al cerrar otro panel puede cambiar
        connected = bool(terminal.server)
        profiles = sftp.profiles(self.config)
        for profile in profiles:
            action = menu.addAction(themed("remote"), profile.name, lambda p=profile: self._ssh_chosen(number(), p))
            action.setToolTip(profile.server)
            action.setEnabled(not connected)
        if not profiles:
            menu.addAction(tr("No hay perfiles guardados")).setEnabled(False)
        menu.addSeparator()
        menu.addAction(themed("plus"), tr("Conexión nueva…"), lambda: self.new_terminal_connection(number())).setEnabled(not connected)
        if connected:
            menu.addAction(themed("disconnect"), tr("Desconectar del servidor"), lambda: self.disconnect_terminal(number()))
        menu.addAction(themed("prefs"), tr("Administrar perfiles…"), lambda: QTimer.singleShot(0, lambda: self.open_preferences(sftp_page=True)))
        return menu

    def _ssh_chosen(self, index: int, profile: remote.Profile, password: str | None = None, typed: bool = False) -> None:
        if self._can_close_terminal_program(index, tr("Al conectarla al servidor se cerrará")):
            self._terminal_pinned.add(index)  # su panel ya no la cambia de sitio
            self.connect_terminal(index, profile, password, typed)

    def new_terminal_connection(self, index: int) -> None:
        stored = sftp.profiles(self.config)
        dialog = sftp.ProfileDialog(quick=True, taken=[profile.name for profile in stored], parent=self)
        if not dialog.exec():
            return
        profile = dialog.profile()
        if dialog.save_profile.isChecked():
            self.config[sftp.PROFILES_KEY] = [item.to_dict() for item in stored] + [profile.to_dict()]
        password = dialog.typed_password()
        self._ssh_chosen(index, profile, password or None, typed=bool(password))

    def disconnect_terminal(self, index: int) -> None:
        """La terminal vuelve al intérprete del equipo, en la carpeta de su panel. Si nada más usa ese servidor, se cierra la conexión."""
        terminal = self.terminals[index]
        server = terminal.server
        if not server or not self._can_close_terminal_program(index, tr("Al desconectarla se cerrará")):
            return
        self._terminal_pinned.add(index)  # aunque su panel esté en ese servidor, no vuelve a él sola
        self._terminal_choices.pop(index, None)
        self._terminal_to_local(index)
        self._release_server(server)
        self._refresh_shell_box(index)
        terminal.setFocus()

    def _release_server(self, server: str) -> None:
        """Se desconecta si ningún panel ni ninguna terminal lo usa."""
        if not any(remote.server_of(view.path) == server for view in self.all_views()) and not any(terminal.server == server for terminal in self.terminals):
            remote.disconnect(server)

    def connect_terminal(self, index: int, profile: remote.Profile, password: str | None = None, typed: bool = False) -> None:
        """Abre la terminal en ese servidor, conectando antes si hace falta. En la carpeta de su panel si es de ese servidor."""
        terminal = self.terminals[index]
        choice = SSH_CHOICE + profile.name

        def connected(connection) -> None:
            if not _alive(terminal) or terminal not in self.terminals:
                return
            position = self.terminals.index(terminal)
            self._terminal_choices[position] = choice
            pane = self._terminal_group(position).current.path
            folder = pane if remote.server_of(pane) == connection.server else connection.start_path()
            try:
                terminal.restart_in(folder)
            except OSError as exc:
                self._terminal_switch_failed(exc)
            self._show_terminal(position, True)
            self._refresh_shell_box(position)

        self._remote_connector.open(profile, connected, password, typed)

    def _terminal_to_local(self, index: int) -> None:
        path = self._terminal_group(index).current.path
        try:
            self.terminals[index].restart_in(path if not remote.is_remote(path) and os.path.isdir(path) else platform_utils.home_dir())
        except OSError as exc:
            self._terminal_switch_failed(exc)

    def _switch_terminal_shell(self, index: int, key: str) -> None:
        previous = self._terminal_choices.get(index)
        self._terminal_choices[index] = key
        try:
            self.terminals[index].switch_shell()
        except OSError as exc:
            if previous is None:
                del self._terminal_choices[index]
            else:
                self._terminal_choices[index] = previous
            self._terminal_switch_failed(exc)

    def _terminal_switch_failed(self, exc: OSError) -> None:
        message = tr("No se pudo abrir la terminal: {exc}", exc=exc)
        self._report(message)
        QMessageBox.warning(self, APP_NAME, message)

    def _reopen_with_chosen_shell(self, index: int) -> None:
        """Ocultar no cierra la sesión: al volver a abrirla, si entretanto cambió el intérprete que le toca, pasa a ese en la misma carpeta.
        Con un programa en marcha no se toca: cerrarlo sin preguntar sería peor."""
        terminal = self.terminals[index]
        if terminal.session is None or terminal.running_shell is None:
            return
        try:
            if self._chosen_shell(index).argv == terminal.running_shell.argv:
                return
            if terminal.session.shell_is_idle():
                terminal.switch_shell()
            else:
                self.statusBar().showMessage(tr("Esta terminal tiene un programa en marcha y sigue con {name}", name=terminal.running_shell.label), 8000)
        except OSError as exc:
            self._terminal_switch_failed(exc)

    def _terminal_group(self, index: int) -> PaneGroup:
        """El panel de una terminal. La general va con el panel activo."""
        return self.groups[self.active_side if index == GENERAL_TERMINAL else index]

    def _terminal_folder(self, index: int) -> str:
        """Dentro de un comprimido o en «Este equipo» no hay una carpeta real donde situarse."""
        saved = self._terminal_start_folders.pop(index, "")
        if saved and os.path.isdir(saved):
            return saved  # la de la sesión que se acaba de cargar
        path = self._terminal_group(index).current.path
        choice = self._terminal_choices.get(index, "")
        if choice.startswith(SSH_CHOICE):  # elegida en su lista: en ese servidor
            profile = remote.find_profile(self.config.get(sftp.PROFILES_KEY), choice[len(SSH_CHOICE):])
            if profile is not None and remote.is_connected(profile.server):
                return path if remote.server_of(path) == profile.server else remote.connection_for(remote.make_path(profile.server)).start_path()
            if profile is not None:
                terminal = self.terminals[index]
                QTimer.singleShot(0, lambda: terminal in self.terminals and self.connect_terminal(self.terminals.index(terminal), profile))
        elif remote.is_remote(path) and remote.is_connected(remote.server_of(path)):
            return path  # se abre en el servidor
        return path if os.path.isdir(path) else platform_utils.home_dir()

    def _terminal_failed(self, index: int, message: str) -> None:
        self._terminal_choices.pop(index, None)  # si ya no está instalado, sin esto no se podría volver a abrir: su lista va dentro
        self._show_terminal(index, False)
        self._report(message)
        QMessageBox.warning(self, APP_NAME, message)

    def _show_terminal(self, index: int, visible: bool, focus: bool = True) -> None:
        """Ocultarla no termina lo que se esté ejecutando en ella: al volver a mostrarla sigue igual."""
        panel, terminal = self.terminal_panels[index], self.terminals[index]
        if visible:
            was_hidden = panel.isHidden()
            panel.show()
            if index == GENERAL_TERMINAL:
                self.general_dock.setVisible(self._general_docked)
            if self._terminals_sized.get(index) != self._terminal_place(index):
                self._size_terminal(index)
            if was_hidden:
                self._reopen_with_chosen_shell(index)
            if focus:
                terminal.setFocus()
        else:
            had_focus = terminal.hasFocus()
            if index in self._terminal_only:
                self._leave_terminal_only(index)  # sin su terminal quedaría vacío: vuelve a mostrar sus archivos
                self._after_terminal_only_change()
            panel.hide()
            if index == GENERAL_TERMINAL:
                self.general_dock.hide()
            if had_focus:
                self._terminal_group(index).current.focus_list()

    def _terminal_close_clicked(self, index: int) -> None:
        """En un panel que solo muestra su terminal, la X cierra el panel, como la de su barra cuando muestra archivos."""
        if index in self._terminal_only:
            self.close_pane(index)
        else:
            self._show_terminal(index, False)

    # ------------------------------------------------------------------ paneles solo de terminal
    def _file_panes(self) -> list[int]:
        return [side for side in range(styles.pane_count(self.dual)) if side not in self._terminal_only]

    def _set_terminal_only(self, side: int, only: bool, focus: bool = True) -> bool:
        """Un panel que muestra solo su terminal, en todo su sitio. Nunca es el origen ni el destino, y su terminal deja de seguirle:
        si se le pide que siga, va con el panel activo. False si no puede ser, porque siempre debe quedar un panel con archivos."""
        if only == (side in self._terminal_only):
            return True
        if only:
            if side >= styles.pane_count(self.dual) or not [pane for pane in self._file_panes() if pane != side]:
                self.statusBar().showMessage(tr("Siempre debe quedar al menos un panel con archivos"), 5000)
                return False
            self._enter_terminal_only(side)
            if side == self.active_side:
                self._view_activated(self.groups[self._dest_side].current)  # el destino pasa a ser el origen: ya no cuenta este panel
            self._show_terminal(side, True, focus)
        else:
            self._leave_terminal_only(side)
            if focus:
                self.groups[side].current.focus_list()
        self._after_terminal_only_change()
        return True

    def _enter_terminal_only(self, side: int, before: tuple[bool, bool] | None = None) -> None:
        terminal = self.terminals[side]
        self._terminal_only[side] = before or (terminal.following, not self.tree_panels[side].isHidden())
        if before is None:
            terminal.set_following(False)
        self.groups[side].hide()
        self.tree_panels[side].hide()
        self._refresh_terminal_only_buttons(side)

    def _leave_terminal_only(self, side: int) -> None:
        """Lo que se hizo en la terminal manda: la lista vuelve en la carpeta a la que se llegó con cd, y no al revés."""
        following, tree = self._terminal_only.pop(side)
        self._style_terminal_panes.discard(side)
        self.groups[side].setVisible(side < styles.pane_count(self.dual))
        terminal, view = self.terminals[side], self.groups[side].current
        here = terminal.current_path() if terminal.session is not None else ""
        here = os.path.normpath(here) if here and os.path.isdir(here) else ""  # como la guarda el panel: así no se vuelve a escribir cd
        if here and os.path.normcase(here) != os.path.normcase(view.path):
            view.navigate(here)  # la terminal aún no sigue a su panel: no se entera
        terminal.set_following(following, here)
        if tree:
            self._show_pane_tree(side, True)
        if not self.terminal_panels[side].isHidden():
            self._size_terminal(side)  # vuelve a su sitio junto a los archivos
        self._refresh_terminal_only_buttons(side)

    def _after_terminal_only_change(self) -> None:
        self._update_roles()
        self._sync_terminals()
        self._sync_tree()
        self._update_preview()

    def _refresh_terminal_only_buttons(self, index: int) -> None:
        only = index in self._terminal_only
        self.terminal_files_buttons[index].setVisible(only)
        self.terminal_close_buttons[index].setToolTip(tr("Cerrar este panel") if only else tr("Ocultar"))
        self._refresh_follow_button(index)

    def _set_terminalpane(self, value: str) -> None:
        """On, el panel activo pasa a mostrar solo su terminal. Off, todos los que lo hacen vuelven a mostrar sus archivos."""
        value = value.lower()
        if value in ("on", "yes", "toggle"):
            self._set_terminal_only(self.active_side, True)
        elif value in ("off", "no"):
            for side in sorted(self._terminal_only):
                self._leave_terminal_only(side)
            self._after_terminal_only_change()
        else:
            raise ValueError(tr("Valor no válido para TERMINALPANE: {value}", value=value))

    def _terminal_place(self, index: int) -> object:
        return "dock" if index == GENERAL_TERMINAL and self._general_docked else self.terminal_areas[index].orientation()

    def _size_terminal(self, index: int) -> None:
        """Abajo, con menos altura que la lista; a la derecha, la mitad del sitio. Después manda lo que elija el usuario."""
        area = self.terminal_areas[index]
        self._terminals_sized[index] = self._terminal_place(index)
        if index == GENERAL_TERMINAL and self._general_docked:
            total = self.centralWidget().height() + self.general_dock.height()
            self.resizeDocks([self.general_dock], [min(max(TERMINAL_MIN_HEIGHT, total // 3), total // 2)], Qt.Orientation.Vertical)
            return
        if area.orientation() == Qt.Orientation.Vertical:
            total = area.height()
            # Si la coloca el estilo, ocupa el sitio de un panel.
            size = total // 2 if index != GENERAL_TERMINAL and self._terminal_position else min(max(TERMINAL_MIN_HEIGHT, total // 3), total // 2)
        else:
            total = area.width()
            size = total // 2
        area.setSizes([max(0, total - size), size])

    def _place_general_terminal(self) -> None:
        """Con los paneles en vertical puede ocupar todo el ancho de la ventana. A la derecha de los paneles, siempre su espacio."""
        panel, terminal = self.terminal_panels[GENERAL_TERMINAL], self.terminals[GENERAL_TERMINAL]
        docked = bool(self.config.get("general_terminal_full_width", False)) and not styles.stacked(self.dual)
        visible = not panel.isHidden()
        if docked != self._general_docked:
            had_focus = terminal.hasFocus()
            self._general_docked = docked
            if docked:
                self.general_dock.setWidget(panel)
            else:
                self.work_area.addWidget(panel)
                self.work_area.setStretchFactor(0, 1)
                self.work_area.setStretchFactor(1, 0)
            panel.setVisible(visible)  # al cambiar de sitio se oculta
            if visible:
                QTimer.singleShot(0, lambda: self._size_terminal(GENERAL_TERMINAL))
            if had_focus:
                terminal.setFocus()
        self.general_dock.setVisible(docked and visible)

    def _orient_terminals(self) -> None:
        self._place_general_terminal()
        automatic = Qt.Orientation.Horizontal if styles.stacked(self.dual) else Qt.Orientation.Vertical
        chosen = {"right": Qt.Orientation.Horizontal, "below": Qt.Orientation.Vertical}.get(self._terminal_position, automatic)
        for index, area in enumerate(self.terminal_areas):
            orientation = automatic if index == GENERAL_TERMINAL else chosen
            if area.orientation() != orientation:
                area.setOrientation(orientation)
                if not self.terminal_panels[index].isHidden():
                    QTimer.singleShot(0, lambda i=index: self._size_terminal(i))  # el panel aún no tiene su nuevo tamaño

    def _set_terminal_follow(self, index: int, following: bool) -> None:
        self.terminals[index].set_following(following)
        self._refresh_follow_button(index)
        self._sync_terminals()

    def _refresh_follow_button(self, index: int) -> None:
        terminal, button = self.terminals[index], self.terminal_follow_buttons[index]
        button.setEnabled(terminal.can_follow)
        button.setChecked(terminal.following and terminal.can_follow)
        if not terminal.can_follow:
            button.setToolTip(tr("Esta terminal no puede seguir al panel"))
        elif index == GENERAL_TERMINAL or index in self._terminal_only:
            button.setToolTip(tr("Seguir al panel activo"))
        else:
            button.setToolTip(tr("Seguir a este panel"))

    def all_trees(self) -> list[FolderTree]:
        return [self.tree, *self.pane_trees]

    def _dock(self, title: str, name: str, widget: QWidget, area, tools: list | None = None) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setTitleBarWidget(self._title_bar(title, [*(tools or []), ("close", tr("Ocultar"), dock.hide)]))
        self.addDockWidget(area, dock)
        return dock

    def _title_bar(self, title: str, tools: list) -> QFrame:
        bar = QFrame()
        bar.setObjectName("dockTitle")
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 3, 2)
        row.setSpacing(1)
        label = QLabel(title)
        label.setMinimumWidth(20)
        row.addWidget(label, 1)
        for icon, tip, action in tools:
            row.addWidget(self._title_button(icon, tip, action))
        return bar

    def _title_button(self, icon: str, tip: str, action) -> QToolButton:
        button = QToolButton()
        button.setIcon(themed(icon))
        button.setIconSize(QSize(12, 12))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.clicked.connect(lambda _=False, a=action: a())
        self._dock_buttons.append((button, icon))
        return button

    def _menu_states(self) -> dict:
        """Las opciones de los menús que están activas o no: se muestran marcadas cuando lo están."""
        modes = {f"Set VIEW={name}": mode for name, mode in (("List", "list"), ("Details", "details"), ("SmallIcons", "smallicons"),
                                                               ("LargeIcons", "largeicons"), ("Tiles", "tiles"), ("Thumbnails", "thumbnails"))}
        states = {command: (lambda mode=mode: self.source.view_mode == mode) for command, mode in modes.items()}
        states.update({
            "Set DUAL=Off": lambda: self.dual == "Off",
            "Set DUAL=Vertical": lambda: self.dual == "Vertical",
            "Set DUAL=Horizontal": lambda: self.dual == "Horizontal",
            "Set TREE=Toggle": lambda: (any(not panel.isHidden() for panel in self.tree_panels) if self.double_tree else not self.tree_dock.isHidden()),
            "Set VIEWPANE=Toggle": lambda: not self.viewer_dock.isHidden(),
            "Set METAPANE=Toggle": lambda: not self.meta_dock.isHidden(),
            "Set FLATVIEW=Toggle": lambda: self.source.flat,
            "Set HIDDEN=Toggle": lambda: bool(self.config.get("show_hidden", False)),
            "Set GENERATIONMARKS=Toggle": lambda: self.config.get("show_generation_marks") is True,
            "Set OPENVIDEOS=Toggle": lambda: self.config.get("open_videos", "viewer") == "viewer",
            "Set OPENVIDEOS=viewer": lambda: self.config.get("open_videos", "viewer") == "viewer",
            "Set OPENVIDEOS=system": lambda: self.config.get("open_videos", "viewer") == "system",
            "Set OPENIMAGES=Toggle": lambda: self.config.get("open_images", "viewer") == "viewer",
            "Set OPENIMAGES=viewer": lambda: self.config.get("open_images", "viewer") == "viewer",
            "Set OPENIMAGES=system": lambda: self.config.get("open_images", "viewer") == "system",
            "Set GRIDLINESH=Toggle": lambda: self.source.grid_lines,
            "Set FULLROWSELECT=Toggle": lambda: bool(self.config.get("full_row_select", False)),
            "Set FRIENDLYDATES=normal": lambda: self.config.get("friendly_dates", "off") == "normal",
            "Set FRIENDLYDATES=off": lambda: self.config.get("friendly_dates", "off") == "off",
            "Set NAVLOCK=Toggle": lambda: self.nav_lock,
            "Set HOVERINFO=Toggle": lambda: bool(self.config.get("hover_info", True)),
            "Set APPLOGO=Toggle": lambda: bool(self.config.get("show_app_icon", True)),
        })
        for size in THUMB_SIZES:
            states[f"Set THUMBNAILSIZE={size}"] = (lambda s=size: self._thumbnail_size() == s)
        return states

    def _update_menu_checks(self) -> None:
        states = self._menu_states()
        for action, command in self._checked_menu_actions:
            try:
                action.setChecked(bool(states[command]()))
            except (AttributeError, RuntimeError):
                pass  # al arrancar, aún sin paneles

    def _build_menus(self) -> None:
        self._menu_icons: list[tuple[QAction, str]] = []
        self._checked_menu_actions: list[tuple[QAction, str]] = []
        menus: dict[str, QMenu] = {}
        central = self.centralWidget()
        for menu_name, label, icon, shortcut, command, pane_only in MENU_ITEMS:
            menu = menus.get(menu_name)
            if menu is None:
                menu = menus[menu_name] = self.menuBar().addMenu(tr(menu_name))
            if label is None:
                menu.addSeparator()
                continue
            action = QAction(tr(label), self)
            if command == COMPARE_COMMAND:
                self._compare_menu_action = action
                action.setEnabled(False)
            if icon:
                action.setIcon(themed(icon))
                self._menu_icons.append((action, icon))
            if shortcut:
                action.setShortcuts([QKeySequence(s) for s in shortcut.split(";")])
                if pane_only:
                    # Para que Supr o Ctrl+C en el panel de búsqueda no actúen sobre los archivos.
                    action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                    central.addAction(action)
                else:
                    self.addAction(action)
            action.triggered.connect(lambda _=False, c=command: self.run_command(c))
            menu.addAction(action)
            if command in self._menu_states():
                action.setCheckable(True)
                self._checked_menu_actions.append((action, command))
        for menu in menus.values():
            menu.aboutToShow.connect(self._update_menu_checks)
        go_menu = menus["&Ir"]
        go_menu.addSeparator()
        self.drives_menu = go_menu.addMenu(themed("drive"), tr("Unidades"))
        self.drives_menu.aboutToShow.connect(self._fill_drives)
        settings = menus["&Ajustes"]
        settings.addAction(themed("single"), tr("Guardar el estilo actual…"), self._save_current_style)
        settings.addAction(themed("delete"), tr("Eliminar el estilo actual…"), self._remove_current_style)
        settings.addAction(themed("prefs"), tr("Crear o cambiar estilos…"), lambda: QTimer.singleShot(0, lambda: self.open_preferences(styles_page=True)))
        settings.addSeparator()
        settings.addAction(themed("refresh"), tr("Restaurar la barra predeterminada"), self._reset_toolbar)
        self.favorites_menu = QMenu(tr("&Favoritos"), self)
        self.favorites_menu.aboutToShow.connect(self._fill_favorites)
        self.menuBar().insertMenu(menus["&Ajustes"].menuAction(), self.favorites_menu)
        self._build_session_corner()
        add_favorite = QAction(self)
        add_favorite.setShortcut(FAVORITE_SHORTCUT)
        add_favorite.triggered.connect(lambda: self.run_command("Favorites ADDDIALOG"))
        self.addAction(add_favorite)

    # ------------------------------------------------------------------ sesiones guardadas
    def _build_session_corner(self) -> None:
        corner = self._session_corner = QWidget()  # la barra de menú no se queda con él: sin esta referencia, Python lo destruye
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(3)
        self.session_box = QComboBox()
        self.session_box.setMinimumWidth(190)
        self.session_box.setToolTip(tr("Cargar una sesión guardada"))
        self.session_box.activated.connect(self._session_chosen)
        self.save_session_button = QToolButton()
        self.save_session_button.setText(tr("Guardar sesión"))
        self.save_session_button.setToolTip(tr("Guarda los paneles, las pestañas y las terminales con un nombre"))
        self.save_session_button.clicked.connect(self.ask_save_named_session)
        self.new_session_button = QToolButton()
        self.new_session_button.setText(tr("Nueva sesión"))
        self.new_session_button.setToolTip(tr("Cierra la sesión actual y empieza otra con el estilo Vertical doble panel"))
        self.new_session_button.clicked.connect(self.ask_new_session)
        self.manage_sessions_button = QToolButton()
        self.manage_sessions_button.setIcon(themed("prefs"))
        self.manage_sessions_button.setToolTip(tr("Administrar las sesiones guardadas"))
        self.manage_sessions_button.clicked.connect(lambda: self.open_preferences(sessions_page=True))
        row.addWidget(self.session_box)
        row.addWidget(self.save_session_button)
        row.addWidget(self.new_session_button)
        row.addWidget(self.manage_sessions_button)
        self.app_logo_button = QToolButton()
        self.app_logo_button.setIcon(app_icon())
        self.app_logo_button.setIconSize(QSize(22, 22))
        self.app_logo_button.setAutoRaise(True)
        self.app_logo_button.setToolTip(tr("Acerca de {APP_NAME}", APP_NAME=APP_NAME))
        self.app_logo_button.clicked.connect(lambda: self.run_command("Help ABOUT"))
        self.app_logo_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.app_logo_button.customContextMenuRequested.connect(self._show_app_logo_menu)
        row.addWidget(self.app_logo_button)
        self._apply_app_logo_visibility()
        self.menuBar().setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._current_session = ""
        self._session_baseline: dict = {}  # cómo estaba la ventana al empezar una sesión sin nombre, para saber si ha cambiado
        self._refresh_session_box()

    def _refresh_session_box(self) -> None:
        if sessions.find(self.config.get("saved_sessions"), self._current_session) is None:
            self._current_session = ""  # se ha eliminado la que estaba abierta: lo que hay en la ventana sigue, pero ya sin nombre
        box = self.session_box
        box.clear()
        box.addItem(tr("Sesiones…"), "")
        for name in sessions.names(self.config.get("saved_sessions")):
            box.addItem(name, name)
        box.setCurrentIndex(max(0, box.findData(self._current_session)))

    def _session_chosen(self, row: int) -> None:
        name = self.session_box.itemData(row)
        if name and name != self._current_session and self._settle_current_session():
            self.load_named_session(name)
        elif name:
            self._refresh_session_box()
        else:
            self.session_box.setCurrentIndex(max(0, self.session_box.findData(self._current_session)))

    def _ask_choice(self, question: str, options: list[str]) -> int:
        """Una pregunta con botones propios y Cancelar. Devuelve cuál se ha pulsado, o -1 si se cancela."""
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, question, QMessageBox.StandardButton.Cancel, self)
        box.button(QMessageBox.StandardButton.Cancel).setText(tr("Cancelar"))
        buttons = [box.addButton(option, QMessageBox.ButtonRole.AcceptRole) for option in options]
        box.setDefaultButton(buttons[0])
        box.exec()
        return buttons.index(box.clickedButton()) if box.clickedButton() in buttons else -1

    def ask_save_named_session(self) -> bool:
        """Con una sesión abierta, se puede sobrescribir o guardar como otra. False si al final no se guarda."""
        if self._current_session:
            answer = self._ask_choice(tr("La sesión abierta es «{name}». ¿Qué quieres hacer?", name=self._current_session), [tr("Sobrescribirla"), tr("Guardar como nueva…")])
            if answer == 0:
                self.save_named_session(self._current_session)
                return True
            if answer < 0:
                return False
        return self._ask_new_session_name()

    def _ask_new_session_name(self) -> bool:
        name = self._ask_text(tr("Guardar sesión"), tr("Nombre de la sesión:"), sessions.default_name())
        if name and name.strip():
            name = name.strip()
            if sessions.find(self.config.get("saved_sessions"), name) is None or self._confirm(tr("Ya hay una sesión llamada «{name}». ¿Sustituirla?", name=name), True):
                self.save_named_session(name)
                return True
        return False

    def session_has_changes(self) -> bool:
        saved = sessions.find(self.config.get("saved_sessions"), self._current_session) if self._current_session else self._session_baseline
        return not sessions.same(saved or {}, self._session_snapshot())

    def _settle_current_session(self) -> bool:
        """Antes de dejar la sesión actual: si tiene cambios, pregunta qué hacer con ellos. False si se cancela."""
        if not self.session_has_changes():
            return True
        question = tr("¿Guardar los cambios de la sesión «{name}»?", name=self._current_session) if self._current_session else tr("La sesión actual no está guardada. ¿Guardarla?")
        answer = self._ask_choice(question, [tr("Guardar"), tr("No guardar")])
        if answer == 0:
            if self._current_session:
                self.save_named_session(self._current_session)
                return True
            return self._ask_new_session_name()
        return answer == 1

    def _clear_window_for_session(self, confirm_text: str) -> bool:
        """Cierra las terminales y las pestañas. False si hay programas en marcha y se prefiere no seguir."""
        busy = [terminal for terminal in self.terminals if terminal.session is not None and not terminal.session.shell_is_idle()]
        if busy and not self._confirm(confirm_text, True):
            return False
        for index, terminal in enumerate(self.terminals):
            self._show_terminal(index, False)
            terminal.dispose()
        self._terminal_choices.clear()
        self._terminals_sized.clear()
        for group in self.groups:
            group.close_all_tabs()
        return True

    def ask_new_session(self) -> None:
        if self._settle_current_session():
            self.new_session()

    def new_session(self) -> None:
        """Deja la ventana como la primera vez, sin nombre. Sin mover ni cambiar de tamaño la ventana."""
        if not self._clear_window_for_session(tr("Hay programas en marcha en las terminales. Al empezar una sesión nueva se cerrarán. ¿Continuar?")):
            return
        self._current_session = ""
        self.config["style"] = DEFAULT_STYLE_NAME
        self._apply_session({}, [], live=True)
        self._panes_to_balance = True
        QTimer.singleShot(0, self._balance_panes)
        self._session_baseline = self._session_snapshot()
        self._refresh_session_box()
        self.statusBar().showMessage(tr("Sesión nueva"), 4000)

    def save_named_session(self, name: str) -> None:
        self.config["saved_sessions"] = sessions.stored(self.config.get("saved_sessions"), name, self._session_snapshot())
        self._current_session = name
        self._refresh_session_box()
        self._save_config()
        self.statusBar().showMessage(tr("Sesión guardada: {name}", name=name), 4000)

    def load_named_session(self, name: str) -> None:
        data = sessions.find(self.config.get("saved_sessions"), name)
        if data is None:
            self._refresh_session_box()
            return
        if not self._clear_window_for_session(tr("Hay programas en marcha en las terminales. Al cargar la sesión se cerrarán. ¿Continuar?")):
            self._refresh_session_box()
            return
        self._current_session = name
        if isinstance(data.get("style"), str):
            self.config["style"] = data["style"]
        self._apply_session(data, [], live=True)
        self._refresh_session_box()
        self.statusBar().showMessage(tr("Sesión cargada: {name}", name=name), 4000)

    # ------------------------------------------------------------------ rama de git en cada panel
    def _check_pane_branch(self, view: FileView) -> None:
        """Se mira en segundo plano: una carpeta de red puede tardar en responder."""
        wanted = bool(self.config.get("git_enabled", True)) and bool(self.config.get("git_pane_branch", True)) and "gitbranch" not in bar_buttons.hidden(self.config, bar_buttons.PANE)
        path = view.path
        if not wanted or not path or frequent.is_virtual(path):
            view.set_git_branch(None)
            return
        if view in self._pane_branch_checks:
            return
        self._pane_branch_checks.add(view)
        configured = self.config.get("git_program", "")

        def find(_sink) -> str | None:
            git = git_tools.git_program(configured)
            return git_tools.folder_branch(git, path) if git else None

        def finished(branch) -> None:
            self._pane_branch_checks.discard(view)
            if _alive(view):
                if view.path == path:
                    view.set_git_branch(branch)
                else:
                    self._check_pane_branch(view)  # se ha cambiado de carpeta mientras tanto

        run_bg(find, finished, lambda _exc: self._pane_branch_checks.discard(view))

    def _check_pane_branches(self) -> None:
        for group in self.groups:
            if group.current is not None and group.isVisibleTo(self):
                self._check_pane_branch(group.current)

    def _pane_branch_tick(self) -> None:
        if self.isActiveWindow():  # también se puede cambiar de rama fuera del programa
            self._check_pane_branches()

    # ------------------------------------------------------------------ barras
    def _build_toolbar(self) -> None:
        self.toolbar.clear()
        self._compare_bar_actions.clear()
        self._open_media_actions.clear()
        items = self.config.get("toolbar", [])
        removed = bar_buttons.hidden(self.config, bar_buttons.TOOLBAR)
        self._add_toolbar_items(self.toolbar, [item for item in bar_buttons.arrange_toolbar(self.config, items) if bar_buttons.toolbar_key(item) not in removed]
                                if isinstance(items, list) else [])
        tidy_separators(self.toolbar)
        self._update_media_open_buttons()

    def _add_toolbar_items(self, bar: QToolBar, items) -> None:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("separator"):
                bar.addSeparator()
                continue
            lines = self._button_commands(item)
            if any(line.lower().startswith("marker") for line in lines):
                continue
            if any(line.lower() == "set spacer" for line in lines):
                spacer = QWidget()
                spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                bar.addWidget(spacer)
                continue
            children = item.get("children") if isinstance(item.get("children"), list) else []
            if not lines and not children:
                continue
            self._add_toolbar_button(bar, item, lines, children)

    def _add_toolbar_button(self, bar: QToolBar, item: dict, lines: list[str], children: list) -> None:
        # Los textos de serie se traducen al mostrarlos; los que escribió el usuario se quedan como están.
        label = tr(item.get("label") or "") or (lines[0] if lines else "")
        action = QAction(themed(item.get("icon", "")), label, self)
        reason = self.commands_unavailable(lines)
        tip = tr(item.get("tip") or "") or label
        action.setToolTip(tr("{tip}\n(No disponible: {reason})", tip=tip, reason=reason) if reason else tip)
        action.setEnabled(reason is None and (bool(lines) or bool(children)))
        if COMPARE_COMMAND in lines:
            self._compare_bar_actions.append(action)
            action.setEnabled(self.compare_pair() is not None)
        from ..core.config import OPEN_IMAGES_COMMAND, OPEN_VIDEOS_COMMAND
        if OPEN_VIDEOS_COMMAND in lines:
            action.setCheckable(True)
            self._open_media_actions.append((action, "open_videos"))
        elif OPEN_IMAGES_COMMAND in lines:
            action.setCheckable(True)
            self._open_media_actions.append((action, "open_images"))
        elif lines and lines[0] in self._menu_states():
            action.setCheckable(True)
            self._checked_menu_actions.append((action, lines[0]))
        if lines:
            action.triggered.connect(lambda _=False, c=lines: self.run_commands(c))
        bar.addAction(action)
        button = bar.widgetForAction(action)
        if button is None:
            return
        has_icon = not action.icon().isNull()
        display = item.get("display", "icon")
        if display == "label" or not has_icon:
            style = Qt.ToolButtonStyle.ToolButtonTextOnly
        elif display == "both":
            style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        else:
            style = Qt.ToolButtonStyle.ToolButtonIconOnly
        button.setToolButtonStyle(style)
        first = lines[0].lower() if lines else ""
        if first.startswith(("go back", "go forward")):
            self._attach_history_menu(button, first.startswith("go forward"))
        elif children:
            menu = QMenu(button)
            self._fill_button_menu(menu, children)
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup if lines
                                else QToolButton.ToolButtonPopupMode.InstantPopup)
            if not lines:
                action.setEnabled(True)

    def _fill_button_menu(self, menu: QMenu, children: list) -> None:
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("separator"):
                menu.addSeparator()
                continue
            lines = self._button_commands(child)
            grand = child.get("children") if isinstance(child.get("children"), list) else []
            label = tr(child.get("label") or "") or (lines[0] if lines else "")
            if grand and not lines:
                self._fill_button_menu(menu.addMenu(themed(child.get("icon", "")), label), grand)
                continue
            if any(line.lower() == "set spacer" for line in lines):
                menu.addSeparator()
                continue
            action = menu.addAction(themed(child.get("icon", "")), label)
            reason = self.commands_unavailable(lines) if lines else tr("sin función")
            action.setEnabled(reason is None)
            action.setToolTip(tr("No disponible: {reason}", reason=reason) if reason else tr(child.get("tip") or "") or label)
            if lines and lines[0] in self._menu_states():
                action.setCheckable(True)
                self._checked_menu_actions.append((action, lines[0]))
            if reason is None:
                action.triggered.connect(lambda _=False, c=lines: self.run_commands(c))
        menu.aboutToShow.connect(self._update_menu_checks)

    def _update_media_open_buttons(self) -> None:
        v_mode = self.config.get("open_videos", "viewer")
        i_mode = self.config.get("open_images", "viewer")
        for action, kind in getattr(self, "_open_media_actions", []):
            try:
                if kind == "open_videos":
                    is_viewer = (v_mode == "viewer")
                    action.setIcon(themed("openvideos" if is_viewer else "openvideossystem"))
                    tip = tr("Abrir vídeos: Visualizador interno (clic para usar la aplicación del sistema)") if is_viewer else tr("Abrir vídeos: Aplicación del sistema (clic para usar el visualizador interno)")
                    action.setToolTip(tip)
                    action.setChecked(is_viewer)
                elif kind == "open_images":
                    is_viewer = (i_mode == "viewer")
                    action.setIcon(themed("openimages" if is_viewer else "openimagessystem"))
                    tip = tr("Abrir imágenes: Visualizador interno (clic para usar la aplicación del sistema)") if is_viewer else tr("Abrir imágenes: Aplicación del sistema (clic para usar el visualizador interno)")
                    action.setToolTip(tip)
                    action.setChecked(is_viewer)
            except (AttributeError, RuntimeError):
                pass
        self._update_menu_checks()

    @staticmethod
    def _button_commands(item: dict) -> list[str]:
        lines = item.get("commands")
        if isinstance(lines, list):
            return [line for line in lines if isinstance(line, str)]
        command = item.get("command")
        return [command] if isinstance(command, str) else []

    def _attach_history_menu(self, button: QToolButton, forward: bool) -> None:
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda: (menu.clear(), self.source.build_history_menu(menu, forward)))
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def _build_style_bar(self) -> None:
        bar = self.style_bar
        bar.clear()
        label = QLabel(tr("Estilos:"))
        label.setObjectName("barLabel")
        bar.addWidget(label)
        self._style_buttons: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        removed = bar_buttons.hidden(self.config, bar_buttons.STYLES)
        names = [style.get("name") for style in self.config.get("styles", []) if isinstance(style, dict) and style.get("name")]
        for name in bar_buttons.ordered(self.config, bar_buttons.STYLES, list(dict.fromkeys(names))):
            if name in removed:
                continue
            action = QAction(tr(name), self)
            action.setCheckable(True)
            action.setChecked(name == self.config.get("style"))
            action.triggered.connect(lambda _=False, n=name: self.apply_style(n))
            group.addAction(action)
            bar.addAction(action)
            button = bar.widgetForAction(action)
            button.setObjectName("styleTab")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._style_buttons[name] = action
        self.new_style_action = QAction(tr("Nuevo estilo…"), self)
        self.new_style_action.setIconText(tr("Nuevo estilo…"))  # sin esto, la barra le quita los puntos suspensivos
        self.new_style_action.setToolTip(tr("Crear un estilo en las preferencias"))
        self.new_style_action.setIcon(themed("plus"))
        self.new_style_action.triggered.connect(lambda: QTimer.singleShot(0, lambda: self.open_preferences(new_style=True)))
        bar.addAction(self.new_style_action)
        button = bar.widgetForAction(self.new_style_action)
        button.setObjectName("styleNew")
        button.setIconSize(QSize(12, 12))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def _build_location_bar(self) -> None:
        # Acciones propias: las que crea addWidget se destruyen con el control al quitarlas de la barra, y aquí se reordenan.
        def own_action(widget: QWidget) -> QWidgetAction:
            action = QWidgetAction(self)
            action.setDefaultWidget(widget)
            return action

        label = QLabel(tr("Localización"))
        label.setObjectName("barLabel")
        self.location_folder = QLabel()
        self.location_folder.setContentsMargins(0, 0, 4, 0)
        self.location = Breadcrumb()
        self.location.set_folder_menus(bool(self.config.get("path_folder_menus", False)))
        self.location.pathEntered.connect(lambda path: self.source.navigate(path))
        self._location_path_actions = [own_action(label), own_action(self.location_folder), own_action(self.location)]
        self.location_recent = QToolButton()
        self.location_recent.setToolTip(tr("Carpetas recientes"))
        self.location_recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.location_recent)
        menu.aboutToShow.connect(lambda: (menu.clear(), self.source.build_recent_menu(menu)))
        self.location_recent.setMenu(menu)
        self._location_list = FolderList(menu, self.location, self.location, lambda: self.source.path)
        self._location_recent_action = own_action(self.location_recent)
        type_label = QLabel(tr("Filtro de contenido"))
        type_label.setObjectName("barLabel")
        self.content_box = CheckCombo(tr("<Ninguno>"))
        for key, (name, _extensions) in CONTENT_TYPES.items():
            self.content_box.add_option(name, key)
        self.content_box.setMinimumWidth(150)
        self.content_box.set_checked_keys(())
        self.content_box.checkedChanged.connect(self._content_type_chosen)
        self._content_actions = [own_action(type_label), own_action(self.content_box)]
        self._refresh_location_icons()
        self._apply_location_buttons()

    def _apply_location_buttons(self) -> None:
        groups = {bar_buttons.PATH: self._location_path_actions, "recent": [self._location_recent_action], "content_type": self._content_actions}
        self.location_bar.clear()
        for key in bar_buttons.arranged(self.config, bar_buttons.LOCATION):
            self.location_bar.addActions(groups[key])
        removed = bar_buttons.hidden(self.config, bar_buttons.LOCATION)
        self._location_recent_action.setVisible("recent" not in removed)
        for action in self._content_actions:
            action.setVisible("content_type" not in removed)

    def _apply_bar_buttons(self) -> None:
        """Aplica lo elegido en las preferencias a lo que no se reconstruye con el tema."""
        self._apply_location_buttons()
        removed = bar_buttons.hidden(self.config, bar_buttons.PANE)
        order = bar_buttons.arranged(self.config, bar_buttons.PANE)
        for view in self.all_views():
            view.history_limit = self._history_size()
            view.set_tool_order(order)
            view.set_removed_tools(removed)
        self.preview.apply_bar_buttons()
        for viewer in self._viewers:
            if _alive(viewer):
                viewer.apply_bar_buttons()
        self._refresh_location_icons()

    def _refresh_location_icons(self) -> None:
        self.location_folder.setPixmap(themed("folder").pixmap(16, 16))
        self.location_recent.setIcon(themed("menu-arrow"))

    def _content_type_chosen(self) -> None:
        self.source.model.set_content_types(self.content_box.checked_keys())
        self.source._update_status()

    def create_view(self) -> FileView:
        view = FileView()
        view.show_hidden = self.config.get("show_hidden", False)
        view.model.expandable = bool(self.config.get("expandable_folders", True))
        view.model.unfold_archives = bool(self.config.get("unfold_archives", True))
        view.breadcrumb.set_folder_menus(bool(self.config.get("path_folder_menus", False)))
        view.model.friendly_dates = self.config.get("friendly_dates", "off")
        view.hover_enabled = bool(self.config.get("hover_info", True))
        view.model.aboutToRename.connect(lambda: self.release_media())
        view.set_tool_order(bar_buttons.arranged(self.config, bar_buttons.PANE))
        view.set_removed_tools(bar_buttons.hidden(self.config, bar_buttons.PANE))
        view.history_limit = self._history_size()
        view.favoritesMenuRequested.connect(self._view_favorites_menu)
        view.folderOpened.connect(lambda _view, path: self._award("double_click", path))
        view.hover_card.ffmpeg_dir = self.config.get("ffmpeg_dir", "")
        view.hover_card.preview_size = self.config.get("hover_preview_size", "standard")
        view.set_full_row(bool(self.config.get("full_row_select", False)))
        view.set_single_click(self.config.get("single_click") is True)
        view.set_resort_on_change(self.config.get("resort_on_change") is True)
        view.set_list_name_limit(self._list_name_limit())
        view.set_thumbnail_size(self._thumbnail_size())
        self._apply_previews(view)
        column = self.config.get("default_sort", "type")
        view.set_default_sort(column if column in DEFAULT_SORTS else "type", self.config.get("default_sort_reverse") is True)
        view.set_free_space_shown(not self._general_status_on())  # hasta que se sepa en qué disco está su carpeta
        view.set_column_widths(self.config.get("detail_columns"))
        view.columnsResized.connect(self._remember_columns)
        view.set_level_colors(bool(self.config.get("tree_level_colors", True)))
        view.model.remote_previews = sftp.previews(self.config)
        style = self._style_named(self.config.get("style", ""))
        view.set_view_mode(style.get("view", "list") if style else "list")
        view.activated.connect(self._view_activated)
        view.hoveredEntryChanged.connect(lambda entry, v=view: self._hover_preview(v, entry))
        view.statusChanged.connect(lambda v=view: (self._update_free_space_panes(), v is self.source and self._update_general_status()))
        view.pathChanged.connect(self._view_path_changed)
        view.pathChanged.connect(self._check_pane_branch)
        view.pathChanged.connect(lambda _view: self._compare_timer.start())
        view.selectionChanged.connect(self._view_selection_changed)
        view.openFile.connect(self._open_file)
        view.fileMenu.connect(self._show_file_menu)
        view.folderMenu.connect(self._show_folder_menu)
        view.filesDropped.connect(self._files_dropped)
        view.filesDroppedMenu.connect(self._files_dropped_menu)
        view.loadFailed.connect(lambda _v, message: self.statusBar().showMessage(f"⚠ {message}", 8000))
        view.commandRequested.connect(self._view_command)
        view.renamed.connect(self._view_renamed)
        return view

    def _history_size(self) -> int:
        size = self.config.get("history_size", 10)
        return size if isinstance(size, int) and size > 0 else 10

    def _view_command(self, view: FileView, command: str) -> None:
        self._view_activated(view)
        self.run_command(command)

    def _view_renamed(self, old: str, new: str) -> None:
        if remote.is_remote(new):
            return
        self._push_undo({"kind": "rename", "dir": os.path.dirname(new), "pairs": [(os.path.basename(old), os.path.basename(new))]})

    def _push_undo(self, entry: dict) -> None:
        self._undo.append(entry)
        self._redo.clear()

    # ------------------------------------------------------------------ estilos
    def _style_named(self, name: str) -> dict | None:
        for style in self.config.get("styles", []):
            if isinstance(style, dict) and str(style.get("name", "")).casefold() == name.casefold():
                return style
        # Un botón propio o un archivo importado pueden seguir usando el nombre anterior.
        renamed = next((new for old, new in STYLE_RENAMES.items() if old.casefold() == name.casefold()), None)
        return self._style_named(renamed) if renamed else None

    def apply_style(self, name: str) -> None:
        style = self._style_named(name)
        if style is None:
            raise ValueError(tr("Estilo desconocido: {name}", name=name))
        self.config["style"] = style["name"]
        self._applied_style = self._style_layout(style)
        self._terminal_position = styles.position_of(style)
        self.set_dual(style.get("dual", "Vertical"))
        self._set_double_tree(bool(style.get("double_tree", False)))
        self.tree_dock.setVisible(bool(style.get("tree", True)) and not self.double_tree)
        self._show_pane_trees(bool(style.get("tree", True)) and self.double_tree)
        area = {"top": Qt.DockWidgetArea.TopDockWidgetArea, "bottom": Qt.DockWidgetArea.BottomDockWidgetArea}.get(
            style.get("viewer_area"), Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(area, self.viewer_dock)
        self._arrange_side_docks()
        self.viewer_dock.setVisible(bool(style.get("viewer", False)))
        self.meta_dock.setVisible(bool(style.get("meta", False)))
        self._balance_side_docks()
        if self.viewer_dock.isVisibleTo(self):
            if style.get("viewer_area") == "top":
                self.resizeDocks([self.viewer_dock], [max(240, self.height() - 340)], Qt.Orientation.Vertical)
            else:
                self.resizeDocks([self.viewer_dock], [360], Qt.Orientation.Horizontal)
        self._apply_style_terminals(style)
        for view in self.all_views():
            view.set_view_mode(style.get("view", "list"))
        action = self._style_buttons.get(style["name"])
        if action:
            action.setChecked(True)
        self._sync_tree()
        self._update_preview()

    @staticmethod
    def _style_layout(style: dict) -> str:
        """Lo que coloca la ventana: cambiarle solo el nombre a un estilo no obliga a volver a aplicarlo."""
        return json.dumps({key: value for key, value in style.items() if key != "name"}, sort_keys=True)

    def _apply_style_terminals(self, style: dict) -> None:
        """Abre las que pide y cierra las demás. Si no dice nada, solo las que abrió el estilo anterior. Cerrarlas no termina lo que tengan en marcha."""
        names = styles.terminals_of(style)
        wanted = {index for index, name in enumerate(styles.TERMINALS) if names is not None and name in names and self._terminal_can_show(index)}
        closing = self._style_terminals if names is None else set(range(len(self.terminals)))
        for index in closing - wanted - set(self._terminal_only):  # de esas se encarga `_apply_style_terminal_panes`
            self._show_terminal(index, False)
        self._style_terminals = wanted
        for index in sorted(wanted):
            if self.terminal_panels[index].isHidden() or self._terminals_sized.get(index) != self._terminal_place(index):
                self._style_sizing.add(index)
            self._show_terminal(index, True, focus=False)
        if self._style_sizing:
            QTimer.singleShot(0, self._size_style_terminals)  # los paneles aún no tienen su nuevo tamaño
        self._apply_style_terminal_panes(style)

    def _apply_style_terminal_panes(self, style: dict) -> None:
        """Los paneles que el estilo quiere solo con su terminal. Si no dice nada, solo vuelven a mostrar sus archivos los que puso así el estilo anterior."""
        names = styles.terminal_panes_of(style)
        count = styles.pane_count(self.dual)
        wanted = {index for index, name in enumerate(styles.PANE_TERMINALS) if names is not None and name in names and index < count}
        if len(wanted) == count:
            wanted.discard(0)  # siempre queda un panel con archivos
        leaving = self._style_terminal_panes if names is None else set(self._terminal_only)
        for side in sorted(leaving - wanted):
            if side in self._terminal_only:
                self._leave_terminal_only(side)
                if styles.terminals_of(style) is not None and side not in self._style_terminals:
                    self._show_terminal(side, False)  # como las demás terminales: el estilo dice cuáles se ven
        self._after_terminal_only_change()
        for side in sorted(wanted):
            self._set_terminal_only(side, True, focus=False)  # si no queda otro con archivos, se queda con los suyos
        self._style_terminal_panes = wanted & set(self._terminal_only)

    def _terminal_can_show(self, index: int) -> bool:
        return index == GENERAL_TERMINAL or index < styles.pane_count(self.dual)

    def _size_style_terminals(self) -> None:
        for index in self._style_sizing:
            if not self.terminal_panels[index].isHidden():
                self._size_terminal(index)
        self._style_sizing.clear()

    def _set_double_tree(self, double: bool) -> None:
        self.double_tree = double
        self.tree_dock.toggleViewAction().setEnabled(not double)  # con un árbol por panel, el general solo estorbaría

    def _show_pane_trees(self, visible: bool) -> None:
        count = styles.pane_count(self.dual)
        for index, panel in enumerate(self.tree_panels):
            if visible and index < count and index not in self._terminal_only:
                self._show_pane_tree(index, True)
            elif not visible or index >= count or index in self._terminal_only:
                panel.hide()  # el de un panel que no se ve: tendrá su ancho cuando se muestre

    def _show_pane_tree(self, index: int, visible: bool) -> None:
        """El árbol propio de un panel, a su izquierda. Con un árbol general a la vez, este sigue al panel activo y el propio a su panel."""
        side, panel = self.sides[index], self.tree_panels[index]
        if not visible:
            panel.hide()
        elif panel.isHidden():
            panel.show()
            side.setSizes([PANE_TREE_WIDTH, max(PANE_TREE_WIDTH, side.width() - PANE_TREE_WIDTH)])
            self._pane_tree_paths.pop(index, None)  # mientras estaba oculto no seguía a su panel
            self._sync_pane_trees()

    def _arrange_side_docks(self) -> None:
        """Visor y metadatos, uno encima del otro en el orden elegido. Si el usuario movió uno a otro sitio, se respeta."""
        above = bool(self.config.get("viewer_above_metadata", True))
        first, second = (self.viewer_dock, self.meta_dock) if above else (self.meta_dock, self.viewer_dock)
        if all(self.dockWidgetArea(dock) == Qt.DockWidgetArea.RightDockWidgetArea and not dock.isFloating() and not self.tabifiedDockWidgets(dock)
               for dock in (first, second)):
            self.splitDockWidget(first, second, Qt.Orientation.Vertical)
            self._docks_to_balance = True
        self._docks_viewer_above = above
        self._balance_side_docks()

    def _balance_side_docks(self) -> None:
        """La primera vez que se ven juntos, a medias: si no, el visor queda muy bajo. Luego manda lo que elija el usuario."""
        if self._docks_to_balance and not self.viewer_dock.isHidden() and not self.meta_dock.isHidden():
            self._docks_to_balance = False
            self.resizeDocks([self.viewer_dock, self.meta_dock], [1000, 1000], Qt.Orientation.Vertical)

    def current_style_definition(self, name: str) -> dict:
        tree = any(not panel.isHidden() for panel in self.tree_panels) if self.double_tree else not self.tree_dock.isHidden()
        definition = {"name": name, "dual": self.dual, "tree": tree, "double_tree": self.double_tree,
                      "viewer": not self.viewer_dock.isHidden(), "meta": not self.meta_dock.isHidden(),
                      "view": self.source.view_mode,
                      "terminals": [name for name, panel in zip(styles.TERMINALS, self.terminal_panels) if not panel.isHidden()],
                      "terminal_panes": self._terminal_pane_names()}
        if self._terminal_position:
            definition["terminal_position"] = self._terminal_position
        return definition

    def _terminal_pane_names(self) -> list[str]:
        return [styles.PANE_TERMINALS[side] for side in sorted(self._terminal_only) if side < styles.pane_count(self.dual)]

    def _save_current_style(self) -> None:
        name = self._ask_text(tr("Guardar estilo"), tr("Nombre del estilo:"), self.config.get("style", ""))
        if not name:
            return
        styles = self.config.get("styles", [])
        definition = self.current_style_definition(name)
        for index, style in enumerate(styles):
            if str(style.get("name", "")).casefold() == name.casefold():
                definition["name"] = style["name"]
                styles[index] = definition
                break
        else:
            styles.append(definition)
        self.config["style"] = definition["name"]
        self._build_style_bar()

    def _remove_current_style(self) -> None:
        name = self.config.get("style", "")
        styles = self.config.get("styles", [])
        if len(styles) <= 1 or self._style_named(name) is None:
            return
        if self._confirm(tr("¿Eliminar el estilo «{name}»?", name=name)):
            self.config["styles"] = [s for s in styles if s.get("name") != name]
            self.config["style"] = self.config["styles"][0].get("name", "")
            self._build_style_bar()

    # ------------------------------------------------------------------ barra predeterminada
    def _reset_toolbar(self) -> None:
        from ..core.config import DEFAULT_TOOLBAR
        self.config["toolbar"] = DEFAULT_TOOLBAR
        stored = bar_buttons.normalize(self.config.get("bar_buttons"))
        stored.pop(bar_buttons.TOOLBAR, None)
        self.config["bar_buttons"] = stored
        order = bar_buttons.normalize_order(self.config.get("bar_order"))
        order.pop(bar_buttons.TOOLBAR, None)
        self.config["bar_order"] = order
        self._build_toolbar()

    # ------------------------------------------------------------------ estado de paneles
    @property
    def source(self) -> FileView:
        return self.groups[self.active_side].current

    @property
    def active_side(self) -> int:
        return self._active_side

    @active_side.setter
    def active_side(self, side: int) -> None:
        self._active_side = side
        self._pane_history.remove(side)
        self._pane_history.insert(0, side)

    @property
    def _dest_side(self) -> int | None:
        """Con más de dos paneles, el destino es el último que estuvo activo. Uno que solo muestra su terminal no puede serlo."""
        count = styles.pane_count(self.dual)
        return next((side for side in self._pane_history if side != self.active_side and side < count and side not in self._terminal_only), None)

    @property
    def dest(self) -> FileView | None:
        side = self._dest_side
        return None if side is None else self.groups[side].current

    def all_views(self) -> list[FileView]:
        return [view for group in self.groups for view in group.views()]

    def _sync_pair(self) -> tuple[str, str] | None:
        if self.dest is None or COMPUTER_PATH in (self.source.path, self.dest.path) or is_results(self.source.path) or is_results(self.dest.path):
            return None
        return self.source.path, self.dest.path

    def _writable_target(self) -> str:
        """Carpeta para crear resultados: el destino si admite escritura y, si no, el origen."""
        if self.dest is not None and os.path.isdir(self.dest.path) and not archive.split_archive_path(self.dest.path):
            return self.dest.path
        return self.source.path

    def _update_roles(self) -> None:
        self._update_general_status()
        self._update_free_space_panes()
        if hasattr(self, "find_panel"):
            self.find_panel.sync_source()
        destination = self._dest_side
        for side, group in enumerate(self.groups):
            for view in group.views():
                if self.dual == "Off":
                    view.set_role("single")
                else:
                    view.set_role("source" if side == self.active_side else "dest" if side == destination else "other")
                view.set_dual_tools_visible(self.dual != "Off")
                # Pulsar el botón activa ese panel: la flecha va hacia el destino que tendrá entonces.
                target = destination if side == self.active_side else self.active_side
                view.set_send_direction(styles.send_direction(self.dual, side, target if target is not None else side))
        if self.groups[self.active_side].current:
            self.setWindowTitle(f"{location_label(self.source.path)} — {APP_NAME}")
            self.location.set_path(self.source.path)
            self.content_box.set_checked_keys(self.source.model.content_types)
        self._apply_pane_frames()

    def _view_activated(self, view: FileView) -> None:
        self._compare_timer.start()
        side = next((index for index, group in enumerate(self.groups) if view in group.views()), self.active_side)
        if side != self.active_side and self.dual != "Off" and side not in self._terminal_only:
            self.active_side = side
            self._update_roles()
            self._sync_tree()
            self._sync_terminals()
            self._update_preview()

    def on_tab_changed(self, group: PaneGroup) -> None:
        if group.current is None or not hasattr(self, "location"):
            return
        side = self.groups.index(group)
        if side < styles.pane_count(self.dual) and side not in self._terminal_only:  # un panel que no se ve no pasa a ser el activo porque se le añada una pestaña
            self.active_side = side
        self._update_roles()
        self._sync_tree()
        self._sync_terminals()
        self._update_preview()

    def _view_path_changed(self, view: FileView) -> None:
        for group in self.groups:
            group.retitle(view)
        if hasattr(self, "location") and view is self.groups[self.active_side].current:
            self._update_roles()
            self._sync_tree()
            if view.path != view.previous_path and not is_results(view.path):
                self._remember_recent(view.path)
                self._follow_navigation(view, view.previous_path)
        if hasattr(self, "location"):
            self._sync_pane_trees()  # el panel que no está activo también tiene su árbol
            self._sync_terminals()

    def _view_selection_changed(self, view: FileView) -> None:
        self._compare_timer.start()  # sin esperar, recorrer la selección se notaría al arrastrar un rectángulo
        if view is self.source:
            self._update_preview()

    # ------------------------------------------------------------------ comparar archivos
    def compare_pair(self) -> tuple[str, str] | None:
        """Los dos archivos a comparar. Manda el panel activo; si no tiene justo dos elementos, se cuentan los de todos los paneles a la vista."""
        if not all(hasattr(self, name) for name in ("groups", "_active_side")) or self.groups[self.active_side].current is None:
            return None
        chosen = self.source.selected_entries()
        if len(chosen) != 2:
            chosen = [entry for group in self.groups if group.current is not None and group.isVisibleTo(self) for entry in group.current.selected_entries()]
        if len(chosen) != 2 or any(entry.is_dir or archive.is_inside_archive(entry.path) for entry in chosen):
            return None
        first, second = chosen[0].path, chosen[1].path
        return None if os.path.normcase(first) == os.path.normcase(second) else (first, second)

    def _update_compare_state(self) -> None:
        enabled = self.compare_pair() is not None
        for action in [self._compare_menu_action, *self._compare_bar_actions]:
            if action is not None:
                action.setEnabled(enabled)

    def _cmd_compare(self, _cmd: commands.Command) -> None:
        pair = self.compare_pair()
        if pair is None:
            self.statusBar().showMessage(tr("Selecciona dos archivos para compararlos"), 5000)
            return
        self.compare_files(*pair, file_compare.external_arguments(self.config, *pair))

    def compare_files(self, first: str, second: str, external: list[str]) -> None:
        for_images = bool(self.config.get("compare_external_images", False))
        outcome: list[tuple[str, object]] = []

        def work(sink):
            result = OpResult()
            try:
                outcome.append(compare_files(first, second, external, for_images, lambda: sink.is_cancelled))
                result.done = 1
            except InterruptedError:
                result.cancelled = True
            except OSError as exc:
                result.errors.append((first, str(exc)))
            return result

        self.jobs.start(tr("Comparar archivos"), work, lambda _result: outcome and self._show_comparison(first, second, *outcome[0]))

    def _show_comparison(self, first: str, second: str, kind: str, value) -> None:
        if kind == "external":
            started = QProcess.startDetached(value[0], value[1:])
            if started[0] if isinstance(started, tuple) else started:
                return
            self._report(tr("No se pudo abrir el comparador: {program}", program=value[0]))
            self.compare_files(first, second, [])
            return
        window = CompareWindow(first, second, self)
        {"identical": lambda: window.show_identical(), "text": lambda: window.show_text(value), "images": lambda: window.show_images(value), "binary": lambda: window.show_binary(value)}[kind]()
        window.show()
        window.raise_()

    def _update_preview(self) -> None:
        if self.groups[self.active_side].current is None:
            return
        selected = self.source.selected_entries()
        entry = selected[0] if selected else None
        if entry is not None and remote.is_remote(entry.path):
            self._preview_remote(entry)
            return
        if self.viewer_dock.isVisible():
            self.preview.show_entry(entry)
        if self.meta_dock.isVisible():
            self.metadata.show_entry(entry)

    def _list_name_limit(self) -> int:
        value = self.config.get("list_column_chars", LIST_COLUMN_CHARS)
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 500 else LIST_COLUMN_CHARS

    def _thumbnail_size(self) -> int:
        value = self.config.get("thumbnail_size", 128)
        return value if value in THUMB_SIZES else 128

    def _apply_previews(self, view) -> None:
        videos = self.config.get("video_thumbnails", "four")
        view.set_previews(videos if videos in VIDEO_THUMBNAILS else "four", self.config.get("folder_previews") is not False, self.config.get("ffmpeg_dir", ""))
        kinds = self.config.get("generation_mark_kinds", "both")
        view.set_generation_marks(self.config.get("show_generation_marks") is True, kinds if kinds in GENERATION_MARK_KINDS else "both")
        view.folder_views = self.config.setdefault_dict("folder_views") if self.config.get("remember_folder_views") is not False else None
        view.set_git_marks(self.config.get("git_marks") is not False, self.config.get("git_program", ""))

    def _remember_columns(self, widths: dict) -> None:
        """Las pestañas que se abran después las usan. Las de las columnas que no se ven en este panel se conservan."""
        stored = self.config.get("detail_columns")
        merged = {**(stored if isinstance(stored, dict) else {}), **widths}
        if "name" not in widths:
            merged.pop("name", None)  # el nombre ha vuelto a ajustarse al panel
        self.config["detail_columns"] = merged

    def _hover_preview(self, view: FileView, entry) -> None:
        """En el modo de un clic, el visor y los metadatos muestran el archivo bajo el ratón, como el panel de información del escritorio.
        Los vídeos y el audio no: empezarían a sonar con solo pasar por encima. Tampoco los del servidor, que habría que descargar.
        Al salir del archivo vuelven a lo seleccionado."""
        self._hover_entry = entry
        self._update_general_status()
        if self.config.get("single_click") is not True or not (self.viewer_dock.isVisible() or self.meta_dock.isVisible()):
            return
        if entry is not None and (remote.is_remote(entry.path) or media_info.is_media_extension(entry.extension)):
            return
        self._hover_preview_entry = entry
        self._hover_preview_timer.start()  # al cruzar la lista no se carga cada archivo que se toca de paso

    def _show_hover_preview(self) -> None:
        entry = self._hover_preview_entry
        if entry is None:
            self._update_preview()
            return
        if self.viewer_dock.isVisible():
            self.preview.show_entry(entry)
        if self.meta_dock.isVisible():
            self.metadata.show_entry(entry)

    def _preview_remote(self, entry) -> None:
        """Se descarga, si así se ha pedido y cabe, y se muestra la copia. Mientras tanto, nada: lo anterior sería de otro archivo."""
        for dock, panel in ((self.viewer_dock, self.preview), (self.meta_dock, self.metadata)):
            if dock.isVisible():
                panel.show_entry(None)
        if entry.is_dir or not sftp.previews(self.config) or not (self.viewer_dock.isVisible() or self.meta_dock.isVisible()):
            return

        def ready(local: str) -> None:
            current = self.source.selected_entries()
            if not current or current[0].path != entry.path:
                return  # ya se ha elegido otro
            copy = entry_from_path(local)
            if self.viewer_dock.isVisible():
                self.preview.show_entry(copy)
            if self.meta_dock.isVisible():
                self.metadata.show_entry(copy)

        self.remote_files.fetch_for_preview(entry, sftp.preview_limit(self.config), ready)

    def _sync_tree(self) -> None:
        # isHidden y no isVisible: durante el arranque la ventana aún no se ha mostrado.
        if not hasattr(self, "tree_dock"):
            return
        if not self.tree_dock.isHidden() and not remote.is_remote(self.source.path):  # el árbol es de las carpetas del equipo
            self.tree.sync_to(self.source.path)
        self._sync_pane_trees()

    def _sync_pane_trees(self) -> None:
        """Solo si cambió su carpeta: al pasar de un panel a otro, cada árbol se queda donde lo dejó el usuario."""
        for side, group in enumerate(self.groups):
            if group.current is None or self.tree_panels[side].isHidden() or self._pane_tree_paths.get(side) == group.current.path:
                continue
            self._pane_tree_paths[side] = group.current.path
            self.pane_trees[side].sync_to(group.current.path)

    def _sync_terminals(self) -> None:
        """Cada terminal sigue a su panel, y la general y las de los paneles que solo la muestran, al activo. Un comprimido o «Este equipo» no son carpetas: se queda en la última."""
        active = self.groups[self.active_side]
        for index, terminal in enumerate(self.terminals):
            group = active if index == GENERAL_TERMINAL or index in self._terminal_only else self.groups[index]
            path = group.current.path if group.current is not None else ""
            if remote.is_remote(path) and not remote.is_connected(remote.server_of(path)):
                continue
            if not remote.is_remote(path) and not os.path.isdir(path):
                continue
            if terminal.session is not None and terminal.server != remote.server_of(path):
                # Su panel ha pasado de este equipo a un servidor, o al revés: si no está ocupada, la terminal cambia con él.
                # La general y las de los paneles solo de terminal no: siguen al activo, y perderían lo que tienen a cada cambio.
                if index != GENERAL_TERMINAL and index not in self._terminal_only and index not in self._terminal_pinned and terminal.ready_for_command():
                    try:
                        terminal.restart_in(path)
                    except OSError as exc:
                        self._report(tr("No se pudo abrir la terminal: {exc}", exc=exc))
                continue
            terminal.follow(path)

    def _pane_tree_activated(self, side: int, path: str) -> None:
        view = self.groups[side].current
        self._view_activated(view)
        if remote.is_remote(path) or _location_exists(path):
            view.navigate(path)
        else:
            self.statusBar().showMessage(tr("No existe la carpeta: {path}", path=path), 5000)

    def _tree_path_activated(self, path: str) -> None:
        if _location_exists(path):
            self.source.navigate(path)
        else:
            self.statusBar().showMessage(tr("No existe la carpeta: {path}", path=path), 5000)

    def _source_path_for_find(self) -> str:
        """Al crear el panel de buscar aún no hay pestañas."""
        return self.source.path if all(hasattr(self, name) for name in ("groups", "_active_side")) and self.groups[self.active_side].current else ""

    def _show_find_results(self, collection: str, target: str) -> None:
        """Los resultados se abren como una carpeta más: se pueden seleccionar, copiar o borrar igual."""
        path = results_path(collection)
        destination = self._dest_side if self._dest_side is not None else self.active_side
        group = self.groups[destination if target.startswith("dest") else self.active_side]
        view = group.current
        if target.endswith("_new"):
            # Si esos resultados ya tienen su pestaña se usa esa: buscar varias veces no debe ir llenando el panel de pestañas iguales.
            view = next((tab for tab in group.views() if tab.path == path), None) or group.add_tab(path)
            group.setCurrentWidget(view)
        if view.path == path:
            view.refresh()
        else:
            view.navigate(path)

    def _find_results_changed(self, _collection: str) -> None:
        if not self._results_timer.isActive():  # mientras busca llegan muchos: una lista que se recargue sin parar no se podría usar
            self._results_timer.start()

    def _refresh_find_results(self) -> None:
        self._results_timer.stop()
        for view in self.all_views():
            if is_results(view.path):
                view.refresh()

    def _navigate_focus(self, folder: str, item: str) -> None:
        self.source.navigate(folder, focus=item)

    # ------------------------------------------------------------------ comandos
    def commands_unavailable(self, lines: list[str]) -> str | None:
        """Motivo por el que un botón no se puede ejecutar, o None."""
        known_set = {name[5:].upper() for name in dir(self) if name.startswith("_set_")}
        return commands.function_unavailable(lines, known_set)

    @staticmethod
    def _pressed_keys() -> set[str]:
        modifiers = QApplication.keyboardModifiers()
        names = ((Qt.KeyboardModifier.ControlModifier, "ctrl"), (Qt.KeyboardModifier.ShiftModifier, "shift"),
                 (Qt.KeyboardModifier.AltModifier, "alt"))
        return {name for flag, name in names if modifiers & flag}

    def run_commands(self, lines: list[str]) -> None:
        """Ejecuta un botón: las instrucciones que correspondan a las teclas pulsadas, en orden."""
        reason = self.commands_unavailable(lines)
        if reason:
            self._report(tr("No disponible en Directory Commander Tux Tux: {reason}", reason=reason))
            return
        for kind, value in commands.plan_function(lines, self._pressed_keys()):
            if kind == "confirm":
                if not self._confirm(value):
                    return
            else:
                self.run_command(value)

    def run_command(self, text: str) -> None:
        first = text.strip().split(" ", 1)[0]
        if commands.is_web_url(first) and "{" not in first:
            self._open_url(first)
            return
        command = commands.parse(text)
        if command is None:
            return
        handler = getattr(self, f"_cmd_{command.name}", None)
        if handler is None:
            self._report(tr("Comando desconocido: {name}", name=command.name))
            return
        reason = self.commands_unavailable([text])
        if reason:
            # Ejecutar solo una parte podría hacer algo distinto de lo pedido.
            self._report(tr("No disponible en Directory Commander Tux Tux: {text} ({reason})", text=text, reason=reason))
            return
        if is_results(self.source.path) and (command.name in NOT_IN_RESULTS or (command.name == "copy" and command.has("HERE"))):
            self.statusBar().showMessage(tr("No disponible en los resultados de una búsqueda: abre primero una carpeta"), 5000)
            return
        if remote.is_remote(self.source.path) and (command.name not in ON_SERVER or (command.name == "clipboard" and (command.has("COPY") or command.has("CUT")))):
            self.statusBar().showMessage(tr("No disponible en un panel remoto"), 5000)
            return
        if self.source.path == COMPUTER_PATH and (command.name in NOT_ON_COMPUTER or
                                                  (command.name == "clipboard" and command.has("CUT"))):
            self.statusBar().showMessage(tr("No disponible en {COMPUTER_LABEL}: entra primero en una unidad", COMPUTER_LABEL=COMPUTER_LABEL), 5000)
            return
        self._award_command(command)
        try:
            handler(command)
        except (OSError, ValueError) as exc:
            self._report(f"{text}: {exc}")
            QMessageBox.warning(self, APP_NAME, str(exc))

    def _report(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.log.log(message)

    def _command_line_entered(self) -> None:
        text = self.command_line.text().strip()
        self.command_line.clear()
        if text:
            self.log.log(f"> {text}")
            self.run_command(text)

    def _cmd_marker(self, _cmd: commands.Command) -> None:
        pass

    def _cmd_go(self, cmd: commands.Command) -> None:
        view = self.source
        if cmd.has("NEWTAB") and cmd.has("UP"):
            self.groups[self.active_side].add_tab(view.parent_path() or view.path)
            return
        if cmd.has("FROMSEL"):
            folders = [e.path for e in view.selected_entries() if e.is_dir]
            focus = "nofocus" not in (cmd.get("NEWTAB") or "").lower()
            for path in folders:
                self.groups[self.active_side].add_tab(path, activate=focus)
            return
        if "DRIVEBUTTONS" in cmd.values or cmd.has("DRIVEBUTTONS"):
            self._drives_menu_popup()
            return
        if cmd.has("TABGROUPSAVE"):
            self._save_tab_group()
            return
        if "TABGROUPLIST" in cmd.values or cmd.has("TABGROUPLIST"):
            self._popup_named(sorted(self.config.get("tabgroups", {})), self.load_tab_group,
                              tr("No hay grupos de pestañas guardados"), "folder")
            return
        simple = {
            "UP": view.up, "BACK": view.back, "FORWARD": view.forward, "ROOT": view.go_root, "HOME": view.go_home,
            "HOMEDIR": view.go_home, "REFRESH": lambda: view.refresh(resort=True), "PATHFIELD": self._begin_path_edit,
            "PATHFIELDPATHTREE": self._begin_path_edit,
            "TABNEW": lambda: self.groups[self.active_side].add_tab(view.path),
            "TABCLOSE": lambda: self.groups[self.active_side].close_tab(self.groups[self.active_side].currentIndex()),
            "TABNEXT": lambda: self._cycle_tab(1), "TABPREV": lambda: self._cycle_tab(-1),
            "SWAP": self._swap_panes, "DEST": self._open_in_dest,
            "BACKLIST": lambda: self._popup_history(False), "FORWARDLIST": lambda: self._popup_history(True),
        }
        for flag, action in simple.items():
            if cmd.has(flag) or flag in cmd.values:
                action()
                return
        if not cmd.first:
            raise ValueError(tr("Go necesita una ruta o una acción"))
        special = self._special_folder(cmd.first)
        path = special or os.path.expanduser(cmd.first)
        if not _location_exists(path) and archive.split_archive_path(path) is None:
            raise FileNotFoundError(tr("No existe la carpeta: {path}", path=path))
        view.navigate(path)

    def _special_folder(self, token: str) -> str | None:
        key = token.lower()
        if key in SPECIAL_FOLDERS:
            return QStandardPaths.writableLocation(SPECIAL_FOLDERS[key])
        if key == "/mycomputer":
            return COMPUTER_PATH if has_computer_view() else platform_utils.root_of(self.source.path)
        if key == "/trash":
            if sys.platform == "win32":
                raise OSError(tr("En Windows, abre la Papelera de reciclaje desde el Explorador"))
            return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash", "files")
        return None

    def _begin_path_edit(self) -> None:
        self.location.begin_edit()

    def _popup_history(self, forward: bool) -> None:
        menu = QMenu(self)
        self.source.build_history_menu(menu, forward)
        if not menu.isEmpty():
            menu.exec(self.cursor().pos())

    def _cycle_tab(self, delta: int) -> None:
        group = self.groups[self.active_side]
        group.setCurrentIndex((group.currentIndex() + delta) % group.count())

    def _swap_panes(self) -> None:
        if self.dest:
            a, b = self.source.path, self.dest.path
            self.source.navigate(b)
            self.dest.navigate(a)

    def _open_in_dest(self) -> None:
        if self.dest:
            self.dest.navigate(self.source.path)

    def _cmd_copy(self, cmd: commands.Command) -> None:
        view = self.source
        limited = any(cmd.has(flag) or flag in cmd.values for flag in REMOTE_COPY_LIMITS) or (cmd.has("DUPLICATE") and cmd.has("AS"))
        if limited and (remote.is_remote(view.path) or (self.dest is not None and remote.is_remote(self.dest.path) and not cmd.has("HERE"))):
            raise OSError(tr("No disponible con un panel remoto"))
        if cmd.has("ADDTOARCHIVE") or cmd.has("ADDTOZIP") or cmd.has("ZIP") or cmd.has("ARCHIVE"):
            self._cmd_archive(cmd)
            return
        if cmd.has("EXTRACT") or "EXTRACT" in cmd.values:
            self._cmd_extract(cmd)
            return
        paths = view.selected_paths()
        if not paths:
            self.statusBar().showMessage(tr("No hay nada seleccionado"), 4000)
            return
        view.remember_selection()
        if cmd.has("DUPLICATE") and cmd.has("AS"):
            template_index = [t.upper() for t in cmd.tokens].index("AS") + 1
            if template_index >= len(cmd.tokens):
                raise ValueError(tr("Falta el nombre después de AS"))
            self._duplicate_with_template(paths, cmd.tokens[template_index], cmd.has("IGNOREEXT"))
            return
        if cmd.has("DUPLICATE") or cmd.has("HERE"):
            target = view.path
        else:
            target = cmd.get("TO") or (self.dest.path if self.dest else None)
            if target is None:
                target = QFileDialog.getExistingDirectory(self, tr("Carpeta de destino"), view.path)
                if not target:
                    return
        if "ARCHIVE" in cmd.values:
            if cmd.get("ARCHIVE").lower() != "single":
                raise ValueError(tr("Valor no admitido: ARCHIVE={get}", get=cmd.get('ARCHIVE')))
            self._zip_each(paths, target)
            return
        if "MAKELINK" in cmd.values or cmd.has("MAKELINK"):
            self._make_links(paths, target, cmd.get("MAKELINK", "softlink"))
            return
        if cmd.has("UPDATEALL") or cmd.has("UPDATEEXISTING"):
            policy = Collision.UPDATE_ALL if cmd.has("UPDATEALL") else Collision.UPDATE_EXISTING
            self._copy_update(paths, target, cmd.has("MOVE"), policy)
            return
        if cmd.has("CREATEFOLDER") or "CREATEFOLDER" in cmd.values:
            name = cmd.get("CREATEFOLDER") or self._ask_text(tr("Mover a una carpeta nueva"), tr("Nombre de la carpeta:"), tr("Nueva carpeta"))
            if not name:
                return
            target = fsops.create_folder(target, commands.expand_date_codes(name))
        new_name = None
        if cmd.has("AS"):
            if len(paths) != 1:
                raise ValueError(tr("«Con otro nombre» solo funciona con un elemento seleccionado"))
            new_name = self._ask_text(tr("Otro nombre"), tr("Nombre en el destino:"), os.path.basename(paths[0]))
            if not new_name:
                return
            reason = fsops.validate_name(new_name)
            if reason:
                raise ValueError(reason)
        self.start_transfer(paths, target, cmd.has("MOVE"), new_name, verify=cmd.has("VERIFY") or None)

    def _make_links(self, paths: list[str], target: str, kind: str) -> None:
        created = []
        for path in paths:
            created.append(fsops.make_link(path, target, kind.lower()))
        self._push_undo({"kind": "created", "paths": created})
        self._refresh_all()

    def start_transfer(self, paths: list[str], target: str, move: bool, new_name: str | None = None, verify: bool | None = None) -> None:
        """`verify`: comparar cada copia con su original; sin decirlo, lo que diga Preferencias."""
        if archive.split_archive_path(target) is not None:
            raise OSError(tr("No se puede escribir dentro de un comprimido. Extrae primero los archivos."))
        if target == COMPUTER_PATH:
            raise OSError(tr("No se puede copiar directamente en {COMPUTER_LABEL}: abre primero una unidad o carpeta.", COMPUTER_LABEL=COMPUTER_LABEL))
        if is_results(target):
            raise OSError(tr("No se puede copiar en los resultados de una búsqueda: abre primero una carpeta."))
        verb = tr("Mover") if move else tr("Copiar")
        if verify is None:
            verify = self.config.get("verify_copies") is True
        if verify:
            verb = tr("{verb} y comprobar", verb=verb)

        def remember(result: OpResult) -> None:
            if result.placed and not remote.is_remote(target) and not any(remote.is_remote(path) for path in paths):
                self._push_undo({"kind": "transfer", "move": move, "placed": list(result.placed), "renamed": list(result.renamed)})

        self.jobs.start(tr("{verb} {count} elementos", verb=verb, count=len(paths)),
                        lambda sink: fsops.transfer(paths, target, move, Collision.ASK, sink, new_name,
                                                    lambda src, dest: self._collision_asker(sink, src, dest), verify), remember,
                        (tr("Moviendo") if move else tr("Copiando"), os.path.dirname(paths[0]), target))
        self.statusBar().showMessage(tr("Operación en curso…"), 3000)

    def _ask_collision(self, src: str, dest: str) -> Answer | None:
        """Se pregunta cuando la copia llega a ese archivo, también dentro de una carpeta que se combina."""
        dialog = ReplaceDialog(src, dest, self)
        dialog.exec()
        dialog.deleteLater()
        return dialog.answer

    def _execute_with_files(self, exe_path: str, paths: list[str]) -> None:
        import subprocess
        abs_exe = os.path.abspath(exe_path)
        abs_paths = [os.path.abspath(p) for p in paths]
        cwd = os.path.dirname(abs_exe)
        try:
            if sys.platform == "win32":
                try:
                    cmd_args = subprocess.list2cmdline(abs_paths)
                    os.startfile(abs_exe, "open", cmd_args, cwd=cwd)
                    return
                except OSError:
                    pass
            subprocess.Popen([abs_exe, *abs_paths], cwd=cwd, start_new_session=sys.platform != "win32")
        except OSError as exc:
            self.statusBar().showMessage(tr("No se pudo ejecutar {exe}: {exc}", exe=os.path.basename(abs_exe), exc=exc), 6000)

    def _files_dropped(self, view: FileView, paths: list[str], target: str | None, move: bool) -> None:
        destination = target or view.path
        if target and fsops.is_executable_target(target):
            self._execute_with_files(target, paths)
            return
        if remote.is_remote(destination) or any(remote.is_remote(path) for path in paths):
            if paths and fsops.drop_operation(paths, destination, force_move=move) is not None:
                try:
                    self.start_transfer(paths, destination, move)
                except OSError as exc:
                    QMessageBox.warning(self, APP_NAME, str(exc))
            return
        here = os.path.normcase(os.path.normpath(destination))
        if not paths or all(os.path.normcase(os.path.dirname(os.path.normpath(p))) == here for p in paths):
            return
        self._award("move_from" if move else "copy_from", os.path.dirname(os.path.normpath(paths[0])))
        self._award("move_to" if move else "copy_to", destination)
        try:
            self.start_transfer(paths, destination, move)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))

    def _files_dropped_menu(self, view: FileView, paths: list[str], target: str | None, default_move: bool) -> None:
        """Arrastrar con el botón derecho: se pregunta qué hacer. En negrita, lo que haría arrastrar con el izquierdo."""
        destination = target or view.path
        if target and fsops.is_executable_target(target):
            menu = QMenu(self)
            action = menu.addAction(tr("Abrir con {name}", name=os.path.basename(target)),
                                    lambda: self._execute_with_files(target, paths))
            font = action.font()
            font.setBold(True)
            action.setFont(font)
            menu.setDefaultAction(action)
            menu.addSeparator()
            menu.addAction(tr("Cancelar"))
            self._run_menu(menu, QCursor.pos())
            return
        menu = QMenu(self)
        copy = menu.addAction(tr("Copiar aquí"), lambda: self._files_dropped(view, paths, target, False))
        move = menu.addAction(tr("Mover aquí"), lambda: self._files_dropped(view, paths, target, True))
        link = menu.addAction(tr("Crear acceso directo aquí"), lambda: self._create_shortcuts(paths, destination))
        menu.addSeparator()
        menu.addAction(tr("Cancelar"))
        movable = fsops.can_move(paths)
        move.setEnabled(movable)
        link.setEnabled(not any(archive.is_inside_archive(path) for path in paths))
        menu.setDefaultAction(move if default_move and movable else copy)
        handlers = []
        if self.config.get("shell_menu_items", True):
            try:
                handlers = windows_shell.drop_handlers(paths, destination, int(self.winId()))
            except OSError as exc:  # sin esas opciones, el menú propio sigue funcionando
                self.log.log(tr("No se pudo leer el menú del sistema: {exc}", exc=exc))
        try:
            for handler in handlers:
                add_shell_items(menu, handler, lambda message: self.statusBar().showMessage(f"⚠ {message}", 8000), copy)
            self._run_menu(menu, QCursor.pos())
        finally:
            for handler in handlers:
                handler.close()

    def _create_shortcuts(self, paths: list[str], destination: str) -> None:
        created = []
        try:
            for path in paths:
                name = os.path.basename(path.rstrip("/\\")) or path[0]
                if windows_shell.AVAILABLE:
                    link = fsops.unique_name(destination, tr("{name} - acceso directo.lnk", name=name))
                    windows_shell.create_shortcut(path, link)
                else:
                    link = fsops.make_link(path, destination, "softlink")
                created.append(link)
        except OSError as exc:
            self._report(str(exc))
        if created:
            self._push_undo({"kind": "created", "paths": created})
            self._refresh_all()

    def _cmd_delete(self, cmd: commands.Command) -> None:
        paths = self.source.selected_paths()
        if not paths:
            return
        self.source.remember_selection()
        self.delete_paths(paths, cmd.has("NORECYCLE"), quiet=cmd.has("QUIET"))

    def delete_paths(self, paths: list[str], permanent: bool = False, done=None, quiet: bool = False, parent: QWidget | None = None, detail: str = "") -> bool:
        """Envía a la papelera o elimina, en segundo plano. `done(resultado)` al terminar. Devuelve False si no se confirmó."""
        # En un servidor no hay papelera.
        permanent = permanent or not self.config.get("use_trash", True) or any(remote.is_remote(path) for path in paths)
        name = os.path.basename(paths[0]) if len(paths) == 1 else tr("{count} elementos", count=len(paths))
        name += detail
        question = (tr("¿Eliminar definitivamente {name}?\nEsta acción no se puede deshacer.", name=name) if permanent
                    else tr("¿Enviar {name} a la papelera?", name=name))
        # El borrado definitivo siempre se confirma, aunque se desactive la confirmación general.
        must_ask = permanent or (self.config.get("confirm_delete", True) and not quiet)
        if must_ask and not (self._confirm(question, permanent, parent=parent) if parent is not None else self._confirm(question, permanent)):
            return False
        self.jobs.start(tr("Eliminar") if permanent else tr("Enviar a la papelera"),
                        lambda sink: fsops.delete_paths(paths, permanent, move_to_trash, sink), done)
        return True

    def _confirm(self, question: str, dangerous: bool = False, parent: QWidget | None = None) -> bool:
        """`parent`: la ventana sobre la que se pregunta, si no es la principal (el mapa de uso del disco, por ejemplo)."""
        default = QMessageBox.StandardButton.No if dangerous else QMessageBox.StandardButton.Yes
        answer = QMessageBox.question(parent or self, APP_NAME, question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, default)
        return answer == QMessageBox.StandardButton.Yes

    def _ask_text(self, title: str, label: str, value: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, value)
        return text.strip() if ok and text.strip() else None

    def _cmd_createfolder(self, cmd: commands.Command) -> None:
        if cmd.has("ZIP") or cmd.has("ARCHIVE"):
            self._create_empty_zip(cmd.get("NAME"))
            return
        name = cmd.get("NAME") or self._ask_text(tr("Nueva carpeta"), tr("Nombre:"), tr("Nueva carpeta"))
        if name and remote.is_remote(self.source.path):
            self._create_on_server(fsops.create_folder, commands.expand_date_codes(name))
        elif name:
            path = fsops.create_folder(self.source.path, commands.expand_date_codes(name))
            self._push_undo({"kind": "created", "paths": [path]})
            self.source.navigate(self.source.path, focus=path)

    def _cmd_createfile(self, cmd: commands.Command) -> None:
        name = cmd.get("NAME") or self._ask_text(tr("Nuevo archivo"), tr("Nombre:"), tr("Nuevo archivo.txt"))
        if name and remote.is_remote(self.source.path):
            self._create_on_server(fsops.create_file, commands.expand_date_codes(name))
        elif name:
            path = fsops.create_file(self.source.path, commands.expand_date_codes(name))
            self._push_undo({"kind": "created", "paths": [path]})
            self.source.navigate(self.source.path, focus=path)

    def _create_on_server(self, create, name: str) -> None:
        """En segundo plano: un servidor lento o colgado no debe dejar la ventana esperando. Deshacer no sabe de servidores."""
        view, folder = self.source, self.source.path
        reason = fsops.validate_name(name)
        if reason:
            raise ValueError(reason)

        def created(path: str) -> None:
            if _alive(view) and view.path == folder:
                view.navigate(folder, add_history=False, focus=path)

        run_bg(lambda _sink: create(folder, name), on_done=created,
               on_error=lambda exc: (self._report(f"{name}: {exc}"), QMessageBox.warning(self, APP_NAME, str(exc))))

    def _cmd_filetype(self, cmd: commands.Command) -> None:
        if cmd.has("NEWMENU"):
            self._new_file_menu()
            return
        if cmd.has("CONTEXTMENU") or "CONTEXTMENU" in cmd.values:
            if "background" in (cmd.get("CONTEXTMENU") or "").lower() or not self.source.selected_entries():
                self._show_folder_menu(self.source, self.cursor().pos())
            else:
                self._show_file_menu(self.source, self.cursor().pos())
            return
        extension = cmd.get("NEW", "")
        name = fsops.unique_name(self.source.path, tr("Nuevo archivo{extension}", extension=extension))
        self._cmd_createfile(commands.Command("createfile", [], set(), {"NAME": os.path.basename(name)}))

    def _cmd_rename(self, cmd: commands.Command) -> None:
        view = self.source
        entries = view.selected_entries()
        preset = cmd.get("PRESET")
        if preset and preset.split(",")[0] == "!list":
            self._popup_named(sorted(self.config.get("rename_presets", {})),
                              lambda name: self.run_command(f'Rename PRESET="{name}"'),
                              tr("No hay presets de renombrado guardados"), "rename")
            return
        if not entries:
            return
        if archive.split_archive_path(view.path) is not None or any(e.in_archive for e in entries):
            raise OSError(tr("No se puede renombrar dentro de un comprimido"))
        if remote.is_remote(view.path) and (cmd.has("ADVANCED") or preset or len(entries) != 1 or view.flat):
            raise OSError(tr("En un panel remoto se renombra de uno en uno"))
        if not cmd.has("ADVANCED") and not preset and len(entries) == 1 and not view.flat:
            view.begin_rename(self.config.get("rename_selects_stem") is not False)
            return
        if view.flat or any(e.depth for e in entries):
            raise OSError(tr("Para renombrar en lote, sal de la vista plana o pliega las carpetas"))
        selected = {e.name for e in entries}
        others = {e.name for e in view.model.entries if e.name not in selected and not e.depth}
        presets = self.config.get("rename_presets", {})
        dialog = BatchRenameDialog(view.path, [e.name for e in entries], others, presets, _image_size, self)
        if preset and not dialog.load_profile(preset):
            raise ValueError(tr("Preset desconocido: {preset}", preset=preset))

        def renamed(pairs: list) -> None:
            self._push_undo({"kind": "rename", "dir": view.path, "pairs": pairs})
            for old, new in pairs:
                self.log.log(tr("Renombrado: {old} → {new}", old=old, new=new))
            view.refresh(renamed={os.path.join(view.path, old): os.path.join(view.path, new) for old, new in pairs})

        dialog.renamed.connect(renamed)
        self.release_media()  # en Windows, un archivo abierto en el visor no se puede renombrar
        dialog.exec()
        self.config["rename_presets"] = presets

    def _cmd_undo(self, cmd: commands.Command) -> None:
        redoing = cmd.has("REDO")
        stack = self._redo if redoing else self._undo
        opposite = self._undo if redoing else self._redo
        if not stack:
            self.statusBar().showMessage(tr("No hay nada que rehacer") if redoing else tr("No hay nada que deshacer"), 4000)
            return
        entry = stack.pop()
        kind = entry["kind"]
        if kind == "rename":
            pairs = entry["pairs"]
            self.release_media()
            errors = rename.apply_renames(entry["dir"], [new for _old, new in pairs], [old for old, _new in pairs])
            self._log_undo_errors(errors)
            failed = {name for name, _message in errors}
            reverted = [(new, old) for old, new in pairs if new not in failed]
            if reverted:
                opposite.append({"kind": "rename", "dir": entry["dir"], "pairs": reverted})
            self._refresh_all()
        elif kind == "created":
            paths = [p for p in entry["paths"] if os.path.lexists(p)]
            self.jobs.start(tr("Deshacer"), lambda sink: fsops.delete_paths(paths, False, move_to_trash, sink))
        elif kind == "transfer":
            self._undo_transfer(entry, opposite)

    def _log_undo_errors(self, errors: list[tuple[str, str]]) -> None:
        for name, message in errors:
            self.log.log(tr("Deshacer {name}: {message}", name=name, message=message))
        if errors:
            self.statusBar().showMessage(tr("Deshacer: {count} errores. Mira el registro.", count=len(errors)), 6000)

    def _undo_transfer(self, entry: dict, opposite: list[dict]) -> None:
        """Solo se deshace lo que creó la operación. Lo que reemplazó o combinó no se toca."""
        safe = [(src, dest) for src, dest, created in entry["placed"] if created and os.path.lexists(dest)]
        skipped = len(entry["placed"]) - len(safe)
        if skipped:
            self.log.log(tr("Deshacer: {skipped} elementos reemplazaron o combinaron algo existente y no se revierten", skipped=skipped))
        if not safe:
            self.statusBar().showMessage(tr("Nada que deshacer sin riesgo"), 5000)
            return
        renamed = entry.get("renamed", [])
        if not entry["move"]:
            paths = [dest for _src, dest in safe]

            def erase(sink):
                result = fsops.delete_paths(paths, False, move_to_trash, sink)
                fsops.restore_renamed(renamed, result)
                return result

            self.jobs.start(tr("Deshacer copia"), erase)
            return

        def work(sink):
            total = OpResult()
            for src, dest in safe:
                sink.check()
                part = fsops.transfer([dest], os.path.dirname(src), True, Collision.SKIP, sink,
                                      os.path.basename(src))
                total.done += part.done
                total.errors.extend(part.errors)
                total.placed.extend(part.placed)
                total.cancelled = total.cancelled or part.cancelled
            fsops.restore_renamed(renamed, total)
            return total

        def remember(result: OpResult) -> None:
            if result.placed:
                opposite.append({"kind": "transfer", "move": True, "placed": list(result.placed)})

        self.jobs.start(tr("Deshacer movimiento"), work, remember)

    def _cmd_select(self, cmd: commands.Command) -> None:
        view = self.source
        if cmd.has("ALL"):
            view.select_all()
        elif cmd.has("NONE"):
            view.remember_selection()
            view.select_none()
        elif cmd.has("INVERT"):
            view.select_invert()
        elif cmd.has("RESELECT"):
            view.select_paths(view.last_selection)
        elif cmd.has("SIMILAR"):
            self._select_similar()
        elif cmd.has("TOCHECKS"):
            view.set_check_mode(True)
            view.model.set_checked([e.path for e in view.selected_entries()])
        elif cmd.has("FROMCHECKS"):
            view.select_paths(sorted(view.model.checked))
        elif any(k in cmd.values or cmd.has(k) for k in ("SOURCETODEST", "DESTTOSOURCE")):
            self._select_between(cmd)
        else:
            pattern = cmd.get("PATTERN") or self._ask_text(tr("Seleccionar"), tr("Patrón (ej.: *.jpg):"), "*")
            if pattern:
                view.select_glob(pattern)

    def _cmd_set(self, cmd: commands.Command) -> None:
        if cmd.has("SPACER"):
            return
        if cmd.has("QUICKFILTER"):
            self.source.show_filter()
            return
        if cmd.has("CONTENTTYPE") and not cmd.values:
            self.content_box.setFocus()
            self.content_box.showPopup()
            return
        if not cmd.values:
            raise ValueError(tr("Set necesita OPCIÓN=valor"))
        for key, value in cmd.values.items():
            getattr(self, f"_set_{key.lower()}")(value)

    def _set_style(self, value: str) -> None:
        self.apply_style(value)

    def _set_contenttype(self, value: str) -> None:
        """Uno o varios tipos, separados por comas."""
        keys = [key.strip() for key in value.lower().split(",") if key.strip() not in ("", "none", "off")]
        for key in keys:
            if key not in CONTENT_TYPES:
                raise ValueError(tr("Tipo de contenido desconocido: {value}", value=key))
        self.source.model.set_content_types(keys)
        self.source._update_status()
        self.content_box.set_checked_keys(keys)

    def _set_dual(self, value: str) -> None:
        modes = {name.lower(): name for name in styles.PANE_MODES}
        mode = {**modes, "toggle": None, "on": "Vertical"}.get(value.lower(), "?")
        if mode == "?":
            raise ValueError(tr("Valor no válido para DUAL: {value}", value=value))
        self.set_dual(mode or ("Off" if self.dual != "Off" else "Vertical"))

    def _apply_level_colors(self) -> None:
        for view in self.all_views():
            view.set_level_colors(bool(self.config.get("tree_level_colors", True)))

    def folder_tab_settings(self) -> dict:
        return tab_settings.normalize(self.config.get("folder_tabs"))

    def apply_tab_settings(self) -> None:
        settings = self.folder_tab_settings()
        positions = tab_settings.tab_positions(settings["position"], settings["dual_position"], self.dual)
        for side, group in enumerate(self.groups):  # juntos y separados son cosa de los dos primeros: los demás, la posición elegida
            group.apply_settings(settings, TAB_POSITIONS[positions[side] if side < len(positions) else settings["position"]])

    def update_tab_bars(self) -> None:
        """Muestra u oculta la barra de pestañas de cada panel según la opción elegida."""
        groups = self.groups[:styles.pane_count(getattr(self, "dual", "Off"))]
        counts = [group.count() for group in groups]
        for group in self.groups:
            group.tabBar().setVisible(tab_settings.bar_visible(group.settings["show_bar"], group.count(), counts))
            QTimer.singleShot(0, group._place_add_button)

    def last_tab_closed(self, group: PaneGroup) -> None:
        """Con doble panel se cierra solo el de la derecha: el izquierdo es el único que puede quedar solo. Con más, cada panel es parte
        de la disposición y sigue con su última pestaña."""
        count = styles.pane_count(self.dual)
        if count == 1:
            self.run_command("Close")  # como Salir: pregunta si hace falta
        elif count == 2 and group is self.groups[1]:
            self.set_dual("Off")

    def set_dual(self, mode: str) -> None:
        mode = mode if styles.valid_layout(mode) else "Vertical"
        previous_count = styles.pane_count(self.dual)
        count = styles.pane_count(mode)
        self.dual = mode
        self._arrange_panes(mode)
        if not self._file_panes():
            self._leave_terminal_only(0)  # con menos paneles, solo quedaban de terminal
        active = self.active_side if self.active_side in self._file_panes() else self._file_panes()[0]
        history = list(self._pane_history)  # abrirle su primera pestaña a un panel no cuenta como haberlo usado
        for side, group in enumerate(self.groups):
            if side < count and group.count() == 0:
                group.add_tab(self.groups[0].current.path if self.groups[0].current else platform_utils.home_dir())
            group.setVisible(side < count and side not in self._terminal_only)
            self.sides[side].setVisible(side < count)
        self._pane_history = history
        self.active_side = active
        if max(count, previous_count) > 2 or self._pane_extents:
            self._panes_to_balance = True
            QTimer.singleShot(0, self._balance_panes)
        self._orient_terminals()
        self._sync_terminals()
        self._update_roles()
        self.apply_tab_settings()

    def _apply_pane_frames(self) -> None:
        """Cada panel, con su terminal y su árbol, dentro de un marco de su color: el del lugar que ocupa. El del activo, más grueso,
        y los demás, atenuados. Todos dejan el mismo sitio: cambiar de panel activo no mueve nada."""
        config = self.config
        shown = config.get("pane_frames_on", True) is not False and pane_colors.frames_shown(config.get("pane_frames"), config.get("pane_frames_after"), styles.pane_count(self.dual))
        width = pane_colors.frame_width(config.get("pane_frame_width"))
        active_width = pane_colors.frame_width(config.get("pane_frame_active_width"), pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH)
        dim = pane_colors.frame_dim(config.get("pane_frame_dim")) if config.get("pane_frame_dim_on") is True else 0
        alpha = round(255 * (100 - dim) / 100)
        for index, (side, color) in enumerate(zip(self.sides, pane_colors.frame_colors(config.get("pane_frame_colors")))):
            active = index == self.active_side
            side.set_frame(color if shown else None, max(width, active_width), active_width if active else width, 255 if active else alpha)

    def _set_paneframes(self, value: str) -> None:
        value = value.lower()
        self.config["pane_frames_on"] = self.config.get("pane_frames_on", True) is False if value == "toggle" else value in ("on", "yes")
        self._apply_pane_frames()

    def _arrange_panes(self, mode: str) -> None:
        """Una simple fila o columna, como siempre, o partes dentro de otras. Cambiar de una a otra mueve los paneles, sin cerrar nada de lo que tengan dentro."""
        tree = styles.layout_tree(mode)
        children = tree[1] if isinstance(tree, tuple) else (tree,)
        used = [leaf for child in children for leaf in ([child] if isinstance(child, int) else styles._leaves(child))]
        spare = tuple(side for side in range(styles.MAX_PANES) if side not in used)
        nested = any(isinstance(child, tuple) for child in children)
        shape = tree if nested else (*children, *spare)
        if shape != self._pane_shape:
            self._pane_shape = shape
            focused = QApplication.focusWidget()
            nests = iter(self._pane_nests)
            self._pane_splitters = []

            def put(splitter: QSplitter, position: int, widget: QWidget) -> None:
                # Solo lo que cambia de sitio. Cambiar de padre obliga a Qt a volver a aplicar los estilos a todo lo que lleva dentro, también a las pestañas ocultas.
                if splitter.widget(position) is not widget:
                    splitter.insertWidget(position, widget)

            def place(splitter: QSplitter, nodes: tuple) -> None:
                self._pane_splitters.append((splitter, nodes))
                for position, node in enumerate(nodes):
                    if isinstance(node, int):
                        put(splitter, position, self.sides[node])
                    else:
                        nest = next(nests)
                        nest.setOrientation(Qt.Orientation.Horizontal if node[0] == "h" else Qt.Orientation.Vertical)
                        put(splitter, position, nest)  # aún sin paneles: así estos no cambian de padre dos veces
                        place(nest, node[1])
                        nest.show()

            place(self.splitter, children)
            for position, side in enumerate(spare, len(children)):
                put(self.splitter, position, self.sides[side])
            for nest in nests:  # los que no hacen falta ya están vacíos
                nest.setParent(None)
            self._panes_to_balance = True
            QTimer.singleShot(0, self._balance_panes)
            if focused is not None:
                focused.setFocus()
        if isinstance(tree, tuple):
            self.splitter.setOrientation(Qt.Orientation.Horizontal if tree[0] == "h" else Qt.Orientation.Vertical)

    def _balance_panes(self) -> None:
        """Todos del mismo tamaño la primera vez. Después manda lo que elija el usuario, y al dividir un panel solo cambia ese."""
        if not self._panes_to_balance:
            return
        self._panes_to_balance = False
        extents, self._pane_extents = self._pane_extents, None
        if not self._pane_splitters:
            self._pane_splitters = [(self.splitter, self._pane_shape)]
        for splitter, nodes in self._pane_splitters:
            horizontal = splitter.orientation() == Qt.Orientation.Horizontal
            sizes = [round(styles.node_extent(node, horizontal, extents)) for node in nodes] if extents else [1000] * len(nodes)
            splitter.setSizes([*sizes, *[1000] * (splitter.count() - len(sizes))])

    def _set_pane(self, value: str) -> None:
        if value.lower() != "close":
            raise ValueError(tr("Valor no válido para PANE: {value}", value=value))
        self.close_pane()

    def close_pane(self, side: int | None = None) -> None:
        """Cierra un panel, el activo si no se dice otro, con sus pestañas y su terminal. Su sitio pasa al de al lado, y el activo vuelve a ser el que lo fue antes."""
        side = self.active_side if side is None else side
        count = styles.pane_count(self.dual)
        terminal = self.terminals[side]
        if count < 2:
            self.statusBar().showMessage(tr("Siempre debe quedar al menos un panel"), 5000)
            return
        if terminal.session is not None and not terminal.session.shell_is_idle() and not self._confirm(
                tr("En la terminal de este panel hay un programa en marcha. Al cerrar el panel se cerrará. ¿Continuar?"), True):
            return
        extents = {index: (self.sides[index].width(), self.sides[index].height()) for index in range(count)}
        mode, sizes = styles.close_in_layout(self.dual, side, extents)
        history = [pane for pane in self._pane_history if pane != side]
        if side in self._terminal_only:
            self._terminal_only.pop(side)
            self._style_terminal_panes.discard(side)
            self._refresh_terminal_only_buttons(side)  # el número queda libre: si se vuelve a usar, será un panel de archivos
        self._show_terminal(side, False)
        terminal.dispose()
        terminal.following = True
        self._terminal_choices.pop(side, None)
        self._terminal_pinned.discard(side)
        self._refresh_follow_button(side)
        self.tree_panels[side].hide()
        self._pane_tree_paths.pop(side, None)
        self._style_terminals.discard(side)
        self._style_sizing.discard(side)
        self._move_pane_to_the_end(side)
        last = styles.MAX_PANES - 1
        renumbered = [pane - 1 if pane > side else pane for pane in history]
        self._pane_history = [*renumbered, last]
        # Si solo quedan paneles de terminal, `set_dual` le devuelve los archivos al primero.
        self._active_side = next((pane for pane in renumbered if pane < count - 1 and pane not in self._terminal_only), 0)
        self._pane_shape = None  # los paneles han cambiado de número: hay que volver a colocarlos aunque la forma sea la misma
        self._pane_extents = sizes
        active, order = self._active_side, list(self._pane_history)
        self.set_dual(mode)
        self.groups[last].close_all_tabs()
        self._active_side, self._pane_history = active, order  # al ocultar el panel, Qt da el foco al siguiente, que se habría vuelto el activo
        self._update_roles()
        self._sync_terminals()
        self._sync_tree()
        self._update_preview()
        self.source.focus_list()

    def _move_pane_to_the_end(self, side: int) -> None:
        """Todo lo de ese panel pasa al último número, libre, y los siguientes bajan uno: siempre están seguidos."""
        last = styles.MAX_PANES - 1
        for items in (self.groups, self.sides, self.pane_areas, self.pane_trees, self.tree_panels, self.terminals, self.terminal_panels,
                      self.terminal_follow_buttons, self.terminal_files_buttons, self.terminal_close_buttons, self.terminal_titles, self.terminal_shell_boxes,
                      self.terminal_ssh_buttons, self.terminal_ssh_off_buttons, self.terminal_leds, self.terminal_areas, self.git_bars):
            items.insert(last, items.pop(side))

        def moved(pane: int) -> int:
            return last if pane == side else pane - 1 if side < pane <= last else pane

        self._terminal_choices = {moved(pane): value for pane, value in self._terminal_choices.items()}
        self._terminal_pinned = {moved(pane) for pane in self._terminal_pinned}
        self._terminals_sized = {moved(pane): value for pane, value in self._terminals_sized.items()}
        self._pane_tree_paths = {moved(pane): value for pane, value in self._pane_tree_paths.items()}
        self._style_terminals = {moved(pane) for pane in self._style_terminals}
        self._style_sizing = {moved(pane) for pane in self._style_sizing}
        self._terminal_only = {moved(pane): value for pane, value in self._terminal_only.items()}
        self._style_terminal_panes = {moved(pane) for pane in self._style_terminal_panes}

    def _set_split(self, value: str) -> None:
        where = {"horizontal": "below", "vertical": "right"}.get(value.lower())
        if where is None:
            raise ValueError(tr("Valor no válido para SPLIT: {value}", value=value))
        self.split_pane(where)

    def split_pane(self, where: str) -> None:
        """Un panel nuevo a la derecha o debajo del activo, en su misma carpeta. Pasa a ser el activo, y el anterior su destino."""
        side = self.active_side
        result = styles.split_layout(self.dual, side, where)
        if result is None:
            self.statusBar().showMessage(tr("Ya hay {count} paneles, el máximo", count=styles.MAX_PANES), 5000)
            return
        mode, new = result
        extents = {index: (self.sides[index].width(), self.sides[index].height()) for index in range(styles.pane_count(self.dual))}
        width, height = extents[side]
        extents[side] = extents[new] = (width / 2, height) if where == "right" else (width, height / 2)
        path = self.source.path
        group = self.groups[new]
        if new in self._terminal_only:
            self._leave_terminal_only(new)  # lo fue con otra disposición: el panel nuevo muestra la carpeta
        if group.count() == 0:
            group.add_tab(path)
        elif group.current.path != path:
            group.current.navigate(path)  # ya se usó antes con otra disposición, y conserva sus pestañas
        self._pane_extents = extents
        self.set_dual(mode)
        if self.double_tree and not self.tree_panels[side].isHidden():
            self._show_pane_trees(True)
        self._view_activated(group.current)
        group.current.focus_list()

    def _set_view(self, value: str) -> None:
        parts = [p.strip().lower() for p in value.split(",") if p.strip()]
        if parts and parts[0] == "cycle":
            cycle = [VIEW_MODES[p] for p in parts[1:] if p in VIEW_MODES] or list(dict.fromkeys(VIEW_MODES.values()))
            current = self.source.view_mode
            mode = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[0]
        else:
            mode = VIEW_MODES.get(parts[0]) if parts else None
        if mode is None:
            raise ValueError(tr("Vista desconocida: {value}", value=value))
        self.source.set_view_mode(mode)
        self.source.remember_folder_view()
        if mode == "thumbnails":
            self._award("thumbnails")

    def set_thumbnail_size(self, size: int) -> None:
        if size not in THUMB_SIZES:
            return
        self.config["thumbnail_size"] = size
        self.config.save()
        for view in self.all_views():
            view.set_thumbnail_size(size)

    def _set_thumbnailsize(self, value: str) -> None:
        try:
            size = int(value)
        except ValueError:
            raise ValueError(tr("Valor no válido para THUMBNAILSIZE: {value}", value=value))
        if size not in THUMB_SIZES:
            raise ValueError(tr("Valor no válido para THUMBNAILSIZE: {value}", value=value))
        self.set_thumbnail_size(size)

    def _set_flatview(self, value: str) -> None:
        current = self.source.flat
        self.source.set_flat(not current if value.lower() == "toggle" else value.lower() in ("on", "yes"))

    def _set_hidden(self, value: str) -> None:
        new = not self.config.get("show_hidden", False) if value.lower() == "toggle" else value.lower() in ("on", "yes")
        self.config["show_hidden"] = new
        for tree in self.all_trees():
            tree.set_show_hidden(new)
        for view in self.all_views():
            view.set_show_hidden(new)

    def _set_filter(self, _value: str) -> None:
        self.source.show_filter()

    @staticmethod
    def _toggle(dock: QDockWidget, value: str) -> None:
        value = value.lower()
        dock.setVisible(dock.isHidden() if value == "toggle" else value in ("on", "yes"))

    def _set_tree(self, value: str) -> None:
        if self.double_tree:
            value = value.lower()
            self._show_pane_trees(all(panel.isHidden() for panel in self.tree_panels) if value == "toggle" else value in ("on", "yes"))
        else:
            self._toggle(self.tree_dock, value)
        self._sync_tree()

    def _set_panetree(self, value: str) -> None:
        value = value.lower()
        side = self.active_side
        self._show_pane_tree(side, self.tree_panels[side].isHidden() if value == "toggle" else value in ("on", "yes"))

    def _set_viewpane(self, value: str) -> None:
        self._toggle(self.viewer_dock, value)
        self._balance_side_docks()
        self._update_preview()

    def _set_metapane(self, value: str) -> None:
        self._toggle(self.meta_dock, value)
        self._balance_side_docks()
        self._update_preview()

    def _set_terminal(self, value: str) -> None:
        value = value.lower()
        side = self.active_side
        self._show_terminal(side, self.terminal_panels[side].isHidden() if value == "toggle" else value in ("on", "yes"))

    def _set_generalterminal(self, value: str) -> None:
        value = value.lower()
        self._show_terminal(GENERAL_TERMINAL, self.terminal_panels[GENERAL_TERMINAL].isHidden() if value == "toggle" else value in ("on", "yes"))

    # ------------------------------------------------------------------ el foquito de las conexiones
    def _led_tick(self) -> None:
        self._led_lit = not self._led_lit
        for group in self.groups:
            group.update_leds(self._led_lit)
        for terminal, led in zip(self.terminals, self.terminal_leds):
            server = terminal.server
            alive = bool(server) and terminal.session is not None and remote.is_alive(server)
            if alive and led.server != server:
                led.server = server
                led.setToolTip(tr("Conectado con {server}", server=server))
            led.setVisible(alive)
            led.set_lit(self._led_lit)
        for tree in self.all_trees():
            root = tree.remote_item.data(0, PATH_ROLE) if tree.remote_item is not None else ""
            tree.set_led(self._led_lit if root and remote.is_alive(remote.server_of(root)) else None)

    # ------------------------------------------------------------------ dispositivos (Linux)
    def _refresh_devices(self) -> None:
        if self._devices_reading:
            return
        self._devices_reading = True

        def read(result) -> None:
            self._devices_reading = False
            found, root = result
            if not _alive(self):
                return
            if devices.changed(self._devices, found):
                self._devices = found
                for tree in self.all_trees():
                    tree.set_devices(found)
            else:
                self._devices = found  # lo ocupado, al día para el menú y la barra de estado, sin tocar el árbol
            for tree in self.all_trees():
                tree.set_root_usage(*root)

        def failed(_exc) -> None:
            self._devices_reading = False

        def work(_sink):
            try:
                usage = shutil.disk_usage(os.sep)
                root = (usage.total, usage.free)
            except OSError:
                root = (None, None)
            return devices.list_devices(), root

        run_bg(work, on_done=read, on_error=failed)

    def _device(self, path: str) -> devices.Device | None:
        return next((device for device in self._devices if device.path == path), None)

    def _device_job(self, use_udisks, fallback, done, failed_text: str, name: str) -> None:
        """Por D-Bus si hay conexión con el bus del sistema; si no, con `udisksctl` en segundo plano.
        Cancelar la ventana de la contraseña no es un error: solo se dice en la barra de estado."""
        def failed(exc) -> None:
            self.statusBar().clearMessage()
            if isinstance(exc, udisks.Cancelled):
                self.statusBar().showMessage(tr("Cancelado: {device}", device=name), 4000)
                return
            message = failed_text.format(device=name, exc=exc)
            if "LimitsExceeded" in str(exc) or "maximum number of active connections" in str(exc):
                message += "\n\n" + tr("El sistema no admite más conexiones de tu usuario con su bus: algún programa las está acaparando "
                                         "(por ejemplo, una ventana de Wine). Al cerrarlo, o al cerrar la sesión, se resuelve.")
            self._report(message.split("\n")[0])
            QMessageBox.warning(self, APP_NAME, message)

        if self._udisks.bus() is not None:
            use_udisks(done, failed)
        else:
            run_bg(lambda _sink: fallback(), on_done=done, on_error=failed)

    def open_device(self, path: str, view: FileView) -> None:
        """Lo muestra en ese panel, montándolo antes si hace falta. Si el sistema pide la contraseña, la pide él, en su propia ventana."""
        found = self._device(path)
        if found is not None and found.mounted:
            self._view_activated(view)
            view.navigate(found.mount_point)
            return
        name = found.name if found is not None else path
        self.statusBar().showMessage(tr("Montando {device}…", device=name), 120000)

        def mounted(point: str) -> None:
            self.statusBar().clearMessage()
            self._refresh_devices()
            if point and _alive(view):
                self._view_activated(view)
                view.navigate(point)

        self._device_job(lambda done, failed: self._udisks.mount(path, done, failed), lambda: devices.mount(path), mounted,
                         tr("No se pudo montar {device}: {exc}"), name)

    def _device_menu(self, path: str, view_of) -> QMenu:
        """`view_of` da el panel en el que se abre: el del árbol, o el activo en el árbol de la ventana."""
        menu = QMenu(self)
        found = self._device(path)
        mounted = found is not None and found.mounted
        menu.addAction(themed("folder"), tr("Abrir"), lambda: self.open_device(path, view_of()))
        if not mounted:
            menu.addAction(themed("drive"), tr("Montar"), lambda: self.open_device(path, view_of()))
        elif not found.is_root:
            menu.addAction(themed("disconnect"), tr("Desmontar"), lambda: self.unmount_device(path))
        if found is not None and found.removable:
            menu.addAction(themed("close"), tr("Quitar con seguridad"), lambda: self.unmount_device(path, eject=True))
        return menu

    def unmount_device(self, path: str, eject: bool = False) -> None:
        """Los paneles que estén dentro pasan a la carpeta personal: si no, el sistema no lo deja desmontar.
        Para quitarlo con seguridad se desmonta todo lo de ese disco y después se apaga."""
        found = self._device(path)
        if found is None:
            return
        mounted = [device for device in self._devices if device.mounted and (device.drive == found.drive if eject else device.path == path)]
        for device in mounted:
            for view in self.all_views():
                if not remote.is_remote(view.path) and _inside(view.path, device.mount_point):
                    view.navigate(platform_utils.home_dir())
        self.statusBar().showMessage(tr("Desmontando {device}…", device=found.name), 120000)

        def finished(_value=None) -> None:
            self.statusBar().clearMessage()
            self._report(tr("Ya se puede quitar {device}", device=found.name) if eject else tr("{device} desmontado", device=found.name))
            self._refresh_devices()

        def by_udisks(done, failed) -> None:
            pending = [device.path for device in mounted]

            def next_step(*_args) -> None:
                if pending:
                    self._udisks.unmount(pending.pop(0), next_step, failed)
                elif eject:
                    self._udisks.power_off(path, done, failed)
                else:
                    done()

            next_step()

        self._device_job(by_udisks, lambda: devices.eject(found) if eject else devices.unmount(path), finished,
                         tr("No se pudo desmontar {device}: {exc}"), found.name)

    # ------------------------------------------------------------------ paneles remotos
    def _set_sftp(self, value: str) -> None:
        """Menu, el menú de las conexiones; Off, el panel activo vuelve a las carpetas del equipo; otro valor, el perfil con ese nombre."""
        key = value.strip()
        if key.lower() == "menu":
            self.build_sftp_menu(self.source).exec(QCursor.pos())
        elif key.lower() == "off":
            self.leave_remote(self.source)
        else:
            profile = remote.find_profile(self.config.get(sftp.PROFILES_KEY), key)
            if profile is None:
                raise ValueError(tr("No hay ningún perfil SSH/SFTP llamado «{name}»", name=key))
            self.connect_remote(profile, self.source)

    def build_sftp_menu(self, view: FileView) -> QMenu:
        menu = QMenu(self)
        on_server = remote.is_remote(view.path)  # ya conectado: primero hay que desconectarlo
        profiles = sftp.profiles(self.config)
        for profile in profiles:
            action = menu.addAction(themed("remote"), profile.name, lambda p=profile: self.connect_remote(p, view))
            action.setToolTip(profile.server)
            action.setEnabled(not on_server)
        if not profiles:
            menu.addAction(tr("No hay perfiles guardados")).setEnabled(False)
        menu.addSeparator()
        menu.addAction(themed("plus"), tr("Conexión nueva…"), lambda: self.new_remote_connection(view)).setEnabled(not on_server)
        if on_server:
            menu.addAction(themed("disconnect"), tr("Desconectar del servidor"), lambda: self.leave_remote(view))
        menu.addAction(themed("prefs"), tr("Administrar perfiles…"), lambda: QTimer.singleShot(0, lambda: self.open_preferences(sftp_page=True)))
        return menu

    def connect_remote(self, profile: remote.Profile, view: FileView) -> None:
        """El panel pasa a mostrar la carpeta inicial del perfil. Lo que haya que preguntar, se pregunta antes."""
        self._remote_connector.open(profile, lambda connection: self._show_remote(view, connection))

    def _show_remote(self, view: FileView, connection) -> None:
        if not _alive(view):
            return
        if not remote.is_remote(view.path):
            view.local_before_remote = view.path  # adónde vuelve al desconectar
        self._unpin_terminal_of(view)
        self._view_activated(view)
        view.navigate(connection.start_path())
        view.focus_list()

    def leave_remote(self, view: FileView) -> None:
        """El panel vuelve a la carpeta del equipo en la que estaba. Si ningún otro panel usa ese servidor, se cierra la conexión."""
        server = remote.server_of(view.path)
        if not server:
            return
        back = getattr(view, "local_before_remote", "")
        self._unpin_terminal_of(view)
        view.navigate(back if back and os.path.isdir(back) else platform_utils.home_dir())
        self._release_server(server)

    def _unpin_terminal_of(self, view: FileView) -> None:
        """Al conectar o desconectar su panel, la terminal de ese panel vuelve a ir con él, aunque se hubiera elegido otra cosa en su lista."""
        side = next((index for index, group in enumerate(self.groups) if view in group.views()), None)
        if side is not None and not self._terminal_choices.get(side, "").startswith(SSH_CHOICE):
            self._terminal_pinned.discard(side)

    def _can_reconnect(self, path: str) -> bool:
        """Una pestaña remota guardada se restaura si queda un perfil de ese servidor con el que volver a conectar."""
        return remote.is_remote(path) and any(profile.server == remote.server_of(path) for profile in sftp.profiles(self.config))

    def _reconnect_remote_tabs(self) -> None:
        """Conecta los servidores de las pestañas remotas restauradas. Hasta entonces muestran que no hay conexión."""
        servers = {remote.server_of(view.path) for view in self.all_views() if remote.is_remote(view.path)}
        for server in sorted(servers):
            if remote.is_connected(server):
                self._server_ready(server)
                continue
            profile = next((item for item in sftp.profiles(self.config) if item.server == server), None)
            if profile is not None:
                self._remote_connector.open(profile, lambda _connection, s=server: self._server_ready(s))

    def _server_ready(self, server: str) -> None:
        for view in self.all_views():
            if remote.server_of(view.path) == server:
                view.refresh()
        self._sync_terminals()
        self._pane_tree_paths.clear()
        self._sync_pane_trees()

    def new_remote_connection(self, view: FileView) -> None:
        stored = sftp.profiles(self.config)
        dialog = sftp.ProfileDialog(quick=True, taken=[profile.name for profile in stored], parent=self)
        if not dialog.exec():
            return
        profile = dialog.profile()
        if dialog.save_profile.isChecked():
            self.config[sftp.PROFILES_KEY] = [item.to_dict() for item in stored] + [profile.to_dict()]
        password = dialog.typed_password()
        self._remote_connector.open(profile, lambda connection: self._show_remote(view, connection), password or None, typed=bool(password))

    def _open_remote_file(self, entry) -> None:
        """Se descarga a una carpeta temporal y se abre con su programa. Si se guardan cambios, se pregunta si se suben."""
        if not sftp.open_files(self.config):
            self.statusBar().showMessage(tr("Abrir los archivos del servidor está desactivado en Preferencias → SFTP"), 6000)
            return
        self.statusBar().showMessage(tr("Descargando {name}…", name=entry.name), 30000)

        def opened(local: str) -> None:
            self.statusBar().clearMessage()
            if is_markdown(entry.name) and self.config.get("open_markdown", "editor") == "editor":
                self.open_markdown_editor(local)  # al guardar en el editor, la copia cambia y se sube como con cualquier programa
            elif not open_externally(local):
                self._report(tr("No hay un programa para abrir {name}", name=entry.name))

        self.remote_files.fetch_for_opening(entry.path, opened, lambda exc: self._report(tr("No se pudo descargar {name}: {exc}", name=entry.name, exc=exc)))

    def _remote_copy_changed(self, local: str, path: str) -> None:
        """Se ha guardado la copia de un archivo del servidor: se sube, preguntando antes o no según Preferencias → SSH/SFTP.
        Si mientras tanto alguien lo cambió en el servidor, se pregunta siempre: subirla borraría ese cambio."""
        name = remote.name_of(path)
        server = remote.label(remote.parent(path) or remote.root(path))
        if not sftp.uploads_without_asking(self.config) and not self._confirm(
                tr("Se ha guardado un cambio en «{name}», abierto desde {server}.\n¿Subirlo al servidor, sustituyendo el que hay allí?", name=name, server=server)):
            return

        def uploaded(_path: str) -> None:
            self.statusBar().showMessage(tr("{name} subido al servidor", name=name), 5000)
            for view in self.all_views():
                if remote.is_remote(view.path) and remote.normalize(view.path) == remote.normalize(remote.parent(path) or remote.root(path)):
                    view.refresh()

        def failed(exc) -> None:
            if isinstance(exc, sftp.RemoteChanged):
                if self._confirm(tr("«{name}» ha cambiado en {server} desde que lo abriste. Si subes tu copia, ese cambio se perderá.\n¿Subirla igualmente?",
                                    name=name, server=server), dangerous=True):
                    self.remote_files.upload(local, uploaded, failed, force=True)
                return
            QMessageBox.warning(self, APP_NAME, tr("No se pudo subir {name}: {exc}", name=name, exc=exc))

        self.statusBar().showMessage(tr("Subiendo {name}…", name=name), 30000)
        self.remote_files.upload(local, uploaded, failed)

    def _set_cmdline(self, value: str) -> None:
        value = value.lower()
        visible = self.command_line.isHidden() if value == "toggle" else value in ("on", "yes")
        self.command_line.setVisible(visible)
        if visible:
            self.command_line.setFocus()

    def _find_apart(self) -> bool:
        return self.config.get("find_panel_place", "window") == "window"

    def _place_find_panel(self) -> None:
        """Abajo, como una pestaña más, o en su propia ventana, según las preferencias."""
        panel = self.find_panel
        position = self.utility.indexOf(panel)
        # Abajo no cabría en una columna: el panel inferior es ancho y bajo.
        changed = panel.set_one_column(self._find_apart() and self.config.get("find_window_columns", 1) == 1)
        panel.show_columns_choice(self._find_apart())
        if self._find_apart() and position < 0 and changed:
            self.find_window.fit(panel)
        if self._find_apart() and position >= 0:
            self.utility.removeTab(position)
            if self.find_window is None:
                self.find_window = FindWindow(self.config, self)
            self.find_window.hold(panel)
        elif not self._find_apart() and position < 0:
            self.find_window.hide()
            self.find_window.release(panel)
            self.utility.insertTab(1, panel, tr("Buscar"))

    def _show_find_window(self, toggle: bool) -> None:
        window = self.find_window
        if toggle and window.isVisible():
            window.hide()
            return
        window.show()
        window.raise_()
        window.activateWindow()
        self.find_panel.pattern.setFocus()

    def _set_utility(self, value: str) -> None:
        parts = [p.strip() for p in value.lower().split(",")]
        value = {"filelog": "log"}.get(parts[0], parts[0])
        if value == "find" and self._find_apart():
            self._show_find_window("toggle" in parts[1:])
            return
        tabs = {"jobs": self.jobs, "find": self.find_panel, "sync": self.sync_panel, "dupe": self.dupe_panel,
                "log": self.log}
        if value == "toggle":
            self.utility_dock.setVisible(self.utility_dock.isHidden())
        elif value == "off":
            self.utility_dock.hide()
        elif value in tabs:
            if "toggle" in parts[1:] and not self.utility_dock.isHidden() and self.utility.currentWidget() is tabs[value]:
                self.utility_dock.hide()
                return
            self.utility_dock.show()
            self.utility.setCurrentWidget(tabs[value])
            if value == "find":
                self.find_panel.pattern.setFocus()
        else:
            raise ValueError(tr("Utilidad desconocida: {value}", value=value))

    def _cmd_clipboard(self, cmd: commands.Command) -> None:
        view = self.source
        if cmd.has("COPY") or cmd.has("CUT"):
            paths = view.selected_paths()
            if paths:
                clipboard.set_files(paths, cmd.has("CUT"))
                self.statusBar().showMessage(tr("{count} elementos en el portapapeles", count=len(paths)), 3000)
        elif cmd.has("PASTE"):
            paths, cut = clipboard.read_files()
            if paths:
                self.start_transfer(paths, view.path, cut)
                if cut:
                    QApplication.clipboard().clear()
        elif cmd.has("COPYNAMES") or "COPYNAMES" in cmd.values:
            paths = view.selected_paths()
            if cmd.get("COPYNAMES", "").lower() == "nopaths":
                paths = [os.path.basename(p) for p in paths]
            if paths:
                QApplication.clipboard().setText("\n".join(paths))
        elif cmd.has("COPYPATH"):
            path = location_label(view.path)
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage(tr("Ruta copiada: {path}", path=path), 3000)
        else:
            raise ValueError(tr("Clipboard necesita COPY, CUT, PASTE, COPYNAMES o COPYPATH"))

    def _cmd_getsizes(self, cmd: commands.Command) -> None:
        view = self.source
        algorithm = cmd.get("HASH")
        if algorithm:
            self._hash_selection(view, algorithm.lower())
            return
        folders = [e.path for e in view.selected_entries() if e.is_dir] or \
                  [e.path for e in view.model.entries if e.is_dir]
        sizes: dict[str, int] = {}

        def work(sink):
            for index, path in enumerate(folders):
                sink.check()
                sink.report(index, len(folders), os.path.basename(path))
                sizes[path] = fsops.tree_size(path, sink)[0]
            # Se ven en la columna Tamaño de Detalles, en la tarjeta, en las barras de estado y al ordenar por tamaño; en las demás vistas no hay columna.
            where = "" if view.view_mode == "details" else " " + tr("Se ven en la columna Tamaño de la vista Detalles.")
            return OpResult(done=len(folders), summary=tr("Tamaño de {count} carpetas: {size} en total.", count=len(folders),
                                                          size=format_size(sum(sizes.values()))) + where)

        self.jobs.start(tr("Calcular tamaños"), work, lambda _r: view.model.apply_dir_sizes(sizes))

    def _cmd_diskusage(self, _cmd: commands.Command) -> None:
        """El mapa de uso del disco de la carpeta seleccionada o, sin una, de la abierta."""
        from .disk_usage import DiskUsageWindow

        view = self.source
        folders = [entry.path for entry in view.selected_entries() if entry.is_dir and not entry.in_archive]
        target = folders[0] if len(folders) == 1 else view.path
        if archive.split_archive_path(target) is not None:
            self.statusBar().showMessage(tr("No disponible dentro de un comprimido"), 5000)
            return

        def show(folder: str, selected: str) -> None:
            pane = self.source
            pane.navigate(folder, focus=selected or None)
            self.activateWindow()

        def open_file(path: str) -> None:
            if os.path.isfile(path):
                self._open_file(self.source, entry_from_path(path))

        DiskUsageWindow(target, show, self, delete=self.delete_paths, open_file=open_file).show()

    def _hash_selection(self, view: FileView, algorithm: str) -> None:
        if algorithm not in fsops.HASH_ALGORITHMS:
            raise ValueError(tr("Algoritmo no admitido: {algorithm}. Disponibles: {join}", algorithm=algorithm, join=', '.join(fsops.HASH_ALGORITHMS)))
        files = [e.path for e in view.selected_entries() if not e.is_dir and os.path.isfile(e.path)]
        if not files:
            self.statusBar().showMessage(tr("Selecciona uno o más archivos"), 4000)
            return
        lines: list[str] = []

        def work(sink):
            result = OpResult()
            for index, path in enumerate(files):
                sink.report(index, len(files), os.path.basename(path))
                try:
                    lines.append(f"{fsops.hash_file(path, algorithm, sink)}  {path}")
                    result.done += 1
                except OSError as exc:
                    result.errors.append((path, str(exc)))
            return result

        def done(_result) -> None:
            for line in lines:
                self.log.log(f"{algorithm}: {line}")
            self._set_utility("log")

        self.jobs.start(tr("Calcular {algorithm}", algorithm=algorithm), work, done)

    def _cmd_properties(self, cmd: commands.Command) -> None:
        entries = [entry_from_path(self.source.path)] if cmd.has("CURRENT") else self.source.selected_entries()
        if entries:
            PropertiesDialog(entries, self).exec()

    def _cmd_setattr(self, cmd: commands.Command) -> None:
        paths = [p for p in self.source.selected_paths() if not archive.is_inside_archive(p)]
        if not paths:
            return
        if cmd.get("MODIFIED"):
            if cmd.get("MODIFIED").lower() != "now":
                raise ValueError(tr("SetAttr MODIFIED solo admite now"))
            for path in paths:
                os.utime(path)
        else:
            dialog = SetAttrDialog(paths, self)
            if not dialog.exec():
                return
            changes = dialog.changes()
            for path in paths:
                fsops.set_attributes(path, changes)
        self.source.refresh()

    def _cmd_show(self, cmd: commands.Command) -> None:
        view = self.source
        selected = [e.path for e in view.selected_entries() if e.extension in IMAGE_EXTENSIONS and os.path.isfile(e.path)]
        everything = [e.path for e in view.model.entries if e.extension in IMAGE_EXTENSIONS and os.path.isfile(e.path)]
        paths = selected if len(selected) > 1 else everything
        if not paths:
            self.statusBar().showMessage(tr("No hay imágenes en esta carpeta"), 4000)
            return
        start = paths.index(selected[0]) if selected and selected[0] in paths else 0
        viewer = self._new_image_viewer(paths, start, view)
        if cmd.has("SLIDESHOW"):
            viewer.toggle_slideshow()
        if cmd.has("FULLSCREEN"):
            viewer.showFullScreen()
        else:
            viewer.show()

    def _new_image_viewer(self, paths: list[str], start: int, view: FileView | None = None) -> ImageViewer:
        """`view` es el panel desde el que se abre: en él se va marcando la imagen que se está viendo."""
        viewer = ImageViewer(paths, start, host=self, origin=view)
        self._viewers = [v for v in self._viewers if _alive(v)] + [viewer]
        return viewer

    def _new_video_viewer(self, paths: list[str], start: int, view: FileView | None = None):
        """Ventana de vídeo, o None si Qt Multimedia no está disponible. Se importa aquí porque iniciarlo lleva tiempo."""
        try:
            from .video_viewer import VideoViewer
        except ImportError as exc:  # en Linux puede faltar una biblioteca del sistema
            self.log.log(tr("Qt Multimedia no está disponible: {exc}", exc=exc))
            return None
        self.preview.pause_media()
        viewer = VideoViewer(self.config, paths, start, host=self, origin=view)
        self._viewers = [v for v in self._viewers if _alive(v)] + [viewer]
        return viewer

    def _video_viewer_open(self) -> bool:
        return any(_alive(v) and hasattr(v, "media") and v.isVisible() for v in self._viewers)

    def release_media(self) -> None:
        """Hay que llamarla antes de modificar archivos: en Windows el reproductor los mantiene bloqueados."""
        self.preview.release_media()
        for viewer in self._viewers:
            if _alive(viewer) and hasattr(viewer, "media"):
                viewer.media.release()

    # Lo que los visores piden a la ventana: las mismas reglas que en la lista de archivos.
    def viewer_image_changed(self, origin: FileView | None, path: str) -> None:
        if origin is not None and _alive(origin):
            origin.mark_path(path)

    def viewer_delete_question(self, path: str) -> str | None:
        name = os.path.basename(path)
        if not self.config.get("use_trash", True):
            return tr("¿Eliminar definitivamente {name}? No se puede deshacer.", name=name)
        return tr("¿Enviar {name} a la papelera?", name=name) if self.config.get("confirm_delete", True) else None

    def viewer_delete(self, path: str) -> str:
        """Devuelve el motivo del fallo, o vacío si se eliminó."""
        self.release_media()
        result = fsops.delete_paths([path], not self.config.get("use_trash", True), move_to_trash)
        self._refresh_all()
        return result.errors[0][1] if result.errors else ""

    def viewer_rename(self, path: str, new_name: str) -> str:
        self.release_media()
        target = fsops.rename_one(path, new_name)
        self._view_renamed(path, target)
        self._refresh_all()
        return target

    def viewer_reveal(self, path: str) -> None:
        self.source.navigate(os.path.dirname(path), focus=path)
        self.raise_()
        self.activateWindow()

    def _cmd_prefs(self, cmd: commands.Command) -> None:
        if cmd.has("STYLELIST"):
            self.style_bar.setVisible(True)
            return
        if cmd.has("LAYOUTSAVE") or "LAYOUTSAVE" in cmd.values:
            name = self._ask_text(tr("Guardar diseño"), tr("Nombre del diseño:"), "")
            if name:
                self.save_layout(name)
            return
        if cmd.has("LAYOUTLIST"):
            self._popup_named(sorted(self.config.get("layouts", {})), self.load_layout, tr("No hay diseños guardados"))
            return
        if cmd.has("LAYOUTEDIT"):
            self._popup_named(sorted(self.config.get("layouts", {})), self._delete_layout, tr("No hay diseños guardados"), "delete")
            return
        if cmd.has("EXPORT"):
            self._export_settings_dialog()
            return
        if cmd.has("IMPORT"):
            self._import_settings_dialog()
            return
        if cmd.has("SFTP"):
            self.open_preferences(sftp_page=True)
            return
        self.open_preferences(cmd.get("BARS"))

    def open_preferences(self, bars: str | None = None, tabs: bool = False, parent: QWidget | None = None, favorites_page: bool = False, styles_page: bool = False,
                         new_style: bool = False, sessions_page: bool = False, sftp_page: bool = False) -> None:
        """`bars` abre la página de las barras de botones, en esa barra si no está vacío; `tabs`, la de las pestañas;
        `favorites_page`, la lista de favoritos; `styles_page`, la de los estilos; `new_style`, la misma con uno nuevo ya empezado; `sessions_page`, la de las sesiones."""
        language = self.config.get("language", i18n.AUTO)
        dialog = PreferencesDialog(self.config, parent or self, self.source.path, [e.path for e in self.source.selected_entries()])


        def follow_session() -> None:
            self._current_session = dialog.sessions_page.followed_name(self._current_session)

        dialog.applied.connect(follow_session)
        dialog.applied.connect(self._preferences_changed)
        if sftp_page:
            dialog.show_sftp()
        elif sessions_page:
            dialog.show_sessions()
        elif styles_page or new_style:
            dialog.show_styles()
            if new_style:
                dialog.styles_page.add()
        elif favorites_page:
            dialog.show_favorites()
        elif bars is not None:
            dialog.show_bars(bars)
        elif tabs:
            dialog.show_tabs()
        if dialog.exec():
            dialog.apply_to(self.config)
            follow_session()
            self._preferences_changed()
        # Al cerrar la ventana, también si se aplicó y luego se canceló: reiniciar con ella abierta la dejaría a medias.
        if self.config.get("language", i18n.AUTO) != language:
            QTimer.singleShot(0, self._offer_restart)

    def _apply_path_options(self) -> None:
        menus = bool(self.config.get("path_folder_menus", False))
        self.location.set_folder_menus(menus)
        for view in self.all_views():
            view.model.set_unfold_archives(bool(self.config.get("unfold_archives", True)))
            view.breadcrumb.set_folder_menus(menus)

    def _preferences_changed(self) -> None:
        self.apply_theme()
        self._apply_general_status()
        self._apply_pane_frames()
        self._apply_path_options()
        self._apply_app_logo_visibility()
        self._refresh_session_box()
        for bar in self.git_bars:
            bar.apply_settings()
        self._check_pane_branches()
        self._place_find_panel()
        self.apply_tab_settings()
        self._apply_level_colors()
        self._apply_bar_buttons()
        self._rebuild_favorites_bar()
        self.metadata.exiftool_dir = self.config.get("exiftool_dir", "")
        self._update_media_open_buttons()
        self._place_general_terminal()
        if self._terminal_preference_now() != self._terminal_preference:
            self._terminal_preference = self._terminal_preference_now()
            self._terminal_choices.clear()  # es lo último que se ha pedido: manda sobre lo elegido antes en la lista de cada una
        for index, terminal in enumerate(self.terminals):
            if terminal.session is not None:
                self._refresh_shell_box(index)
        if self._docks_viewer_above != bool(self.config.get("viewer_above_metadata", True)):
            self._arrange_side_docks()
        self._build_style_bar()
        current = self._style_named(self.config.get("style", ""))
        if current is not None and self._style_layout(current) != self._applied_style:
            self.apply_style(current["name"])  # se ha cambiado el estilo que está en uso, o se ha eliminado
        self._update_preview()
        for view in self.all_views():
            view.hover_enabled = bool(self.config.get("hover_info", True))
            view.hover_card.ffmpeg_dir = self.config.get("ffmpeg_dir", "")
            view.hover_card.preview_size = self.config.get("hover_preview_size", "standard")
            view.model.remote_previews = sftp.previews(self.config)
            view.set_single_click(self.config.get("single_click") is True)
            view.set_resort_on_change(self.config.get("resort_on_change") is True)
            view.set_list_name_limit(self._list_name_limit())
            view.set_thumbnail_size(self._thumbnail_size())
            self._apply_previews(view)

    def customize_bars(self, bar: str = "", parent: QWidget | None = None) -> None:
        """Lo que hace «Personalizar…». Después de cerrarse el menú desde el que se pide."""
        QTimer.singleShot(0, lambda: self.open_preferences(bar, parent=parent))

    def _offer_restart(self) -> None:
        if self._confirm(tr("El idioma cambiará al reiniciar el programa. ¿Reiniciar ahora?")):
            self._restart()

    def _restart(self) -> None:
        """Se cierra como siempre, guardando la sesión, y vuelve a abrirse. Si al cerrar se decide no salir, se queda abierto."""
        arguments = sys.argv[1:] if getattr(sys, "frozen", False) else sys.orig_argv[1:]
        if self.close():
            QProcess.startDetached(sys.executable, arguments)

    def _cmd_cli(self, cmd: commands.Command) -> None:
        if not cmd.has("DOSPROMPT"):
            raise ValueError(tr("CLI solo admite DOSPROMPT"))
        path = self.source.path
        if archive.split_archive_path(path) is not None:
            raise OSError(tr("No se puede abrir una terminal dentro de un comprimido"))
        platform_utils.open_terminal(path, self._chosen_shell() if sys.platform == "win32" else None)

    def _cmd_run(self, cmd: commands.Command) -> None:
        if not cmd.tokens:
            raise ValueError(tr("Run necesita un programa"))
        selected = self.source.selected_paths()
        dest = self.dest.path if self.dest else ""
        platform_utils.run_program(commands.expand_args(cmd.tokens, self.source.path, dest, selected), self.source.path)

    def _cmd_archive(self, cmd: commands.Command) -> None:
        paths = self.source.selected_paths()
        if not paths:
            return
        if any(archive.is_inside_archive(p) for p in paths):
            raise OSError(tr("Extrae primero los archivos del comprimido"))
        target_dir = self.source.path if cmd.has("HERE") else self._writable_target()
        default =(os.path.basename(paths[0]) if len(paths) == 1 else tr("Archivos")) + ".zip"
        name = self._ask_text(tr("Comprimir"), tr("Nombre del ZIP (en {target_dir}):", target_dir=target_dir), default)
        if not name:
            return
        reason = fsops.validate_name(name)
        if reason:
            raise ValueError(reason)
        target = fsops.unique_name(target_dir, name if name.lower().endswith(".zip") else name + ".zip")

        def work(sink):
            archive.create_zip(paths, target, lambda _n: sink.check())
            return OpResult(done=len(paths))

        self.jobs.start(tr("Comprimir en {basename}", basename=os.path.basename(target)), work,
                        lambda _r: self._push_undo({"kind": "created", "paths": [target]}))

    def _cmd_extract(self, cmd: commands.Command) -> None:
        files = [e.path for e in self.source.selected_entries() if archive.is_archive_file(e.path)]
        if not files:
            self.statusBar().showMessage(tr("Selecciona un comprimido"), 4000)
            return
        target_dir = self.source.path if cmd.has("HERE") else self._writable_target()
        if archive.split_archive_path(target_dir):
            raise OSError(tr("El destino está dentro de un comprimido"))

        def work(sink):
            result = OpResult()
            for path in files:
                stem = fsops._split_name(os.path.basename(path))[0]
                folder = fsops.unique_name(target_dir, stem)
                os.makedirs(folder)
                archive.extract_to(path, "", folder, lambda _n: sink.check())
                result.done += 1
            return result

        self.jobs.start(tr("Extraer"), work)

    def _cmd_favorites(self, cmd: commands.Command) -> None:
        if cmd.has("ADDDIALOG"):
            self._add_favorite_dialog(self.source)
        elif cmd.has("ADD"):
            path = self.source.path
            nodes = self.config.get("favorites", [])
            if favorites.contains(nodes, path):
                self.statusBar().showMessage(tr("Ya está en favoritos: {path}", path=path), 4000)
            else:
                label = COMPUTER_LABEL if path == COMPUTER_PATH else os.path.basename(path.rstrip("/\\")) or path
                nodes.append({"label": label, "path": path})
                self.config["favorites"] = nodes
                self._rebuild_favorites_bar()
                self.statusBar().showMessage(tr("Añadido a favoritos: {path}", path=path), 4000)
        elif cmd.has("IMPORT"):
            self._import_favorites_dialog()
        elif cmd.has("EXPORT"):
            self._export_favorites_dialog()
        elif cmd.has("EDIT"):
            self._edit_favorites()
        elif cmd.has("SMART"):
            self._popup_paths(self.smart_favorites(), tr("Aún no hay carpetas frecuentes"))
        elif cmd.has("MENU"):
            menu = QMenu(self)
            self._populate_favorites(menu)
            menu.exec(self.cursor().pos())
        else:
            raise ValueError(tr("Favorites necesita ADD, IMPORT o MENU"))

    def _add_favorite_dialog(self, view: FileView, path: str = "") -> None:
        """La carpeta abierta en el panel o, desde el menú de una carpeta de la lista, esa carpeta."""
        dialog = AddFavoriteDialog(path or view.path, self.config.get("favorites", []), self, self.config.get("favorites_autosort", False) is True)
        if dialog.exec():
            self.config["favorites"] = dialog.result_nodes()
            self._rebuild_favorites_bar()

    def _view_favorites_menu(self, view: FileView, menu: QMenu) -> None:
        """El botón de favoritos de un panel: lo que se elija se aplica a ese panel. También se le da el foco:
        al cerrarse el menú, Qt se lo devuelve a quien lo tenía y activaría el otro panel."""
        self._view_activated(view)
        view.current_view.setFocus()
        menu.clear()
        self._populate_favorites(menu, view)

    def _fill_favorites(self) -> None:
        menu = self.favorites_menu
        menu.clear()
        self._populate_favorites(menu)
        menu.addSeparator()
        menu.addAction(themed("extract"), tr("Importar favoritos…"), lambda: self.run_command("Favorites IMPORT"))
        menu.addAction(themed("addtozip"), tr("Exportar favoritos…"), lambda: self.run_command("Favorites EXPORT"))
        menu.addAction(themed("logs"), tr("Carpetas recientes…"), lambda: self.run_command("Recent"))

    def _populate_favorites(self, menu: QMenu, view: FileView | None = None) -> None:
        """Añadir y editar, los favoritos con sus ramas y las carpetas más visitadas. Con `view`, todo se aplica a ese panel."""
        nodes = favorites.with_bar(self.config.get("favorites", []))
        smart = self.smart_favorites()
        states = listing.path_states([node["path"] for node in favorites.walk(nodes)] + [item["path"] for item in smart])
        # El atajo es de la ventana. Aquí solo se muestra: una acción con él en cada menú lo volvería ambiguo.
        menu.addAction(themed("favoritesadd"), f"{tr('Añadir a Favoritos')}\t{FAVORITE_SHORTCUT}", lambda: self._add_favorite_dialog(view or self.source))
        menu.addAction(themed("favoritesedit"), tr("Editar Favoritos"), lambda: self.run_command("Favorites EDIT"))
        add_section(menu, tr("Favoritos"))
        self._add_favorite_items(menu, nodes, states, view)
        shown = [item["path"] for item in smart if states[item["path"]] is not False]
        if shown:
            add_section(menu, tr("Favoritos inteligentes"))
        for path in shown:
            self._add_folder_action(menu, {"label": listing.location_label(path), "path": path}, states[path], view)

    def _add_favorite_items(self, menu: QMenu, nodes: list, states: dict, view: FileView | None = None) -> None:
        for node in nodes:
            what = favorites.kind(node)
            if what == "branch":
                submenu = menu.addMenu(themed("folder"), menu_text(tr(node["label"]) if node.get("bar") else node["label"]))
                if node["children"]:
                    self._add_favorite_items(submenu, node["children"], states, view)
                else:
                    submenu.addAction(tr("(vacío)")).setEnabled(False)
            elif what in ("folder", "file"):
                self._add_folder_action(menu, node, states.get(node["path"]), view)
            elif what == "separator":
                menu.addSeparator()
            elif what == "space":
                menu.addAction("").setEnabled(False)
            elif what == "header":
                add_section(menu, node["header"])

    def _add_folder_action(self, menu: QMenu, node: dict, exists: bool | None, view: FileView | None) -> None:
        path = node["path"]
        action = menu.addAction(folder_icon(path) if exists else themed("file-kind" if node.get("file") else "folder"), menu_text(node["label"]))
        action.setToolTip(listing.location_label(path))
        action.setEnabled(exists is not False)
        action.triggered.connect(lambda _=False: self._open_favorite(node, view or self.source))

    def _open_favorite(self, node: dict, view: FileView) -> None:
        self._view_activated(view)
        path = node["path"]
        if node.get("file") or os.path.isfile(path):
            self._open_file(view, entry_from_path(path))
        else:
            view.navigate(path)

    def _fill_drives(self) -> None:
        menu = self.drives_menu
        menu.clear()
        menu.addAction(themed("home"), tr("Carpeta personal"), lambda: self.source.go_home())
        for volume in QStorageInfo.mountedVolumes():
            if not volume.isValid() or not volume.isReady():
                continue
            kind = bytes(volume.fileSystemType()).decode(errors="replace")
            root = volume.rootPath()
            if kind in HIDDEN_FILESYSTEMS or root.startswith(("/snap", "/boot/efi", "/run/user", "/var/lib")):
                continue
            label = volume.displayName() or root
            action = menu.addAction(themed("drive"), f"{label} ({root})")
            action.triggered.connect(lambda _=False, p=root: self.source.navigate(p))

    def _cmd_help(self, _cmd: commands.Command) -> None:
        QMessageBox.about(self, tr("Acerca de {APP_NAME}", APP_NAME=APP_NAME),
                          tr("<b>{APP_NAME} {version__}</b><br>Gestor de archivos de doble panel para Linux y Windows.<br>Proyecto independiente de código abierto. Desarrollado con IA para uso personal. El software y código se provee tal cual (as is) sin garantía de ningún tipo.<br><br>Repositorio:<br><a href=\"{repo_url}\">{repo_url}</a>",
                             APP_NAME=APP_NAME, version__=__version__, repo_url=REPO_URL))

    def _cmd_close(self, cmd: commands.Command) -> None:
        """Salir. Con PROGRAM=confirm se pregunta aunque en las preferencias no se pida confirmar."""
        self._exit_asked = "always" if (cmd.get("PROGRAM") or "").lower() == "confirm" else "default"
        try:
            self.close()
        finally:
            self._exit_asked = ""

    def open_connections(self) -> list[str]:
        """Las conexiones SSH/SFTP que se cerrarán al salir, como usuario@servidor."""
        servers = set(remote.active_servers())
        servers.update(terminal.server for terminal in self.terminals if terminal.server)
        return [server.removesuffix(f":{remote.DEFAULT_PORT}") for server in sorted(servers)]

    def _confirm_exit(self, always: bool = False) -> bool:
        """Con conexiones abiertas se pregunta siempre: se van a cerrar. Lo demás, según las preferencias."""
        connections = self.open_connections()
        lines = []
        if self.jobs.running:
            lines.append(tr("Hay {running} operaciones en curso: se cancelarán.", running=self.jobs.running))
        if connections:
            lines.append(tr("Se cerrarán las conexiones SSH/SFTP con: {servers}.", servers=", ".join(connections)))
        if not lines and not always and self.config.get("confirm_exit", True) is False:
            return True
        lines.append(tr("¿Salir de {APP_NAME}?", APP_NAME=APP_NAME))
        return self._confirm("\n\n".join(lines), dangerous=bool(connections or self.jobs.running))

    # ------------------------------------------------------------------ abrir y menú contextual
    def _open_file(self, view: FileView, entry, internal: bool = False) -> None:
        """`internal` abre la imagen o el vídeo en su visor aunque lo configurado sea la aplicación del sistema."""
        if remote.is_remote(entry.path):
            self._open_remote_file(entry)
            return
        self._award("double_click", view.path)
        if archive.is_archive_file(entry.path):
            # Sin un programa asociado, se explora aquí en vez de no hacer nada.
            if self.config.get("open_archives", "system") == "browse" or not has_default_app(entry.path) or not open_externally(entry.path):
                view.navigate(entry.path)
            return
        if is_markdown(entry.name) and archive.split_archive_path(entry.path) is None and (internal or self.config.get("open_markdown", "editor") == "editor"):
            self.open_markdown_editor(entry.path)
            return
        if is_json(entry.name) and archive.split_archive_path(entry.path) is None and (internal or self.config.get("open_json", "viewer") == "viewer"):
            if self.open_json_viewer(entry.path):
                return
        image = entry.extension in IMAGE_EXTENSIONS and (internal or self.config.get("open_images", "viewer") == "viewer")
        video = entry.extension in VIDEO_EXTENSIONS and (internal or self.config.get("open_videos", "viewer") == "viewer")
        location = archive.split_archive_path(entry.path)
        if location is None:
            if image or video:
                # Con los demás archivos de ese tipo de la carpeta, para poder pasar de uno a otro.
                kinds = IMAGE_EXTENSIONS if image else VIDEO_EXTENSIONS
                paths = [e.path for e in view.model.entries if e.extension in kinds and os.path.isfile(e.path)]
                if entry.path not in paths:  # de otra carpeta (desde el mapa de uso del disco, por ejemplo): solo ese
                    paths = [entry.path]
                self._show_in_viewer(paths, paths.index(entry.path), image, view)
            else:
                open_externally(entry.path)
            return
        temp = tempfile.mkdtemp(prefix="directory-commander-tux-tux-")
        self._temp_dirs.append(temp)

        def opened(created: list[str]) -> None:
            if created and (image or video):
                self._show_in_viewer(created[:1], 0, image, None)
            elif created:
                open_externally(created[0])

        run_bg(lambda _s: archive.extract_to(*location, temp), opened,
               lambda exc: self._report(tr("No se pudo abrir {name}: {exc}", name=entry.name, exc=exc)))

    def open_markdown_editor(self, path: str) -> None:
        """Un archivo ya abierto en un editor no se abre dos veces: se trae al frente."""
        from .markdown_editor import MarkdownEditor

        for editor in self.findChildren(MarkdownEditor):
            if os.path.normcase(editor.path) == os.path.normcase(os.path.normpath(path)):
                editor.showNormal()
                editor.raise_()
                editor.activateWindow()
                return
        try:
            editor = MarkdownEditor(path, self)
        except UnicodeDecodeError:
            self._report(tr("{name} no está en UTF-8: se abre con su programa para no estropearlo al guardar", name=os.path.basename(path)))
            open_externally(path)
            return
        except OSError as exc:
            self._report(tr("No se pudo abrir {name}: {exc}", name=os.path.basename(path), exc=exc))
            return
        editor.setWindowFlag(Qt.WindowType.Window)
        editor.show()

    def open_json_viewer(self, path: str) -> bool:
        """En su ventana. Un archivo que no es un JSON válido se abre con su programa, que dirá dónde falla."""
        from .json_viewer import JsonViewer

        for viewer in self.findChildren(JsonViewer):
            if os.path.normcase(viewer.path) == os.path.normcase(os.path.normpath(path)):
                viewer.showNormal()
                viewer.raise_()
                viewer.activateWindow()
                return True
        try:
            viewer = JsonViewer(path, self)
        except ValueError as exc:
            self._report(tr("{name} no es un JSON válido: {exc}", name=os.path.basename(path), exc=exc))
            return False
        except OSError as exc:
            self._report(tr("No se pudo abrir {name}: {exc}", name=os.path.basename(path), exc=exc))
            return False
        viewer.setWindowFlag(Qt.WindowType.Window)
        viewer.show()
        return True

    def _show_in_viewer(self, paths: list[str], start: int, image: bool, view: FileView | None) -> None:
        viewer = self._new_image_viewer(paths, start, view) if image else self._new_video_viewer(paths, start, view)
        if viewer is not None:
            viewer.show()
        else:
            open_externally(paths[start])

    def _add_command(self, menu: QMenu, label: str, command: str, enabled: bool = True, icon: str = "") -> QAction:
        action = menu.addAction(themed(icon), label, lambda: self.run_command(command))
        action.setEnabled(enabled)
        return action

    def _internal_viewer_choice(self, view: FileView, entry):
        """Añade a un submenú el visor interno, si es imagen o vídeo y lo configurado es la aplicación del sistema.
        None si no aplica: con el visor interno configurado, ya es lo que hace «Abrir»."""
        setting = "open_images" if entry.extension in IMAGE_EXTENSIONS else "open_videos" if entry.extension in VIDEO_EXTENSIONS else ""
        if not setting or self.config.get(setting, "viewer") != "system":
            return None

        def add(submenu: QMenu) -> None:
            submenu.addAction(themed("show"), tr("Visualizador interno"), lambda: self._open_file(view, entry, internal=True))
            submenu.addSeparator()

        return add

    def _add_open_with(self, menu: QMenu, view: FileView, entry, enabled: bool) -> None:
        """En Windows la entrada se cambia al mostrar el menú por la del sistema, con sus programas. Esta queda
        para cuando no se piden las opciones de otros programas."""
        path = entry.path
        menu.open_with_first = first = self._internal_viewer_choice(view, entry)
        if windows_shell.AVAILABLE and first is None:
            action = menu.addAction(tr(context_menu.OPEN_WITH), lambda: self._open_with_dialog(path))
        else:
            programs = menu.addMenu(tr(context_menu.OPEN_WITH))
            programs.aboutToShow.connect(lambda: self._fill_open_with(programs, path, first))
            action = programs.menuAction()
        action.setEnabled(enabled)

    def _open_with_dialog(self, path: str) -> None:
        try:
            windows_shell.open_with_dialog(path, int(self.winId()))
        except OSError as exc:
            self._report(str(exc))

    def _fill_open_with(self, menu: QMenu, path: str, first=None) -> None:
        menu.clear()
        if first is not None:
            first(menu)
        if windows_shell.AVAILABLE:
            menu.addAction(tr("Elegir otra aplicación…"), lambda: self._open_with_dialog(path))
            return
        kind = QMimeDatabase().mimeTypeForFile(path)
        for app in desktop_apps.apps_for([kind.name(), *kind.allAncestors()]):
            menu.addAction(QIcon.fromTheme(app.icon), app.name, lambda a=app: self._launch(desktop_apps.command_for(a, path), path))
        menu.addSeparator()
        menu.addAction(tr("Otro programa…"), lambda: self._open_with_chosen_program(path))
        tidy_separators(menu)

    def _open_with_chosen_program(self, path: str) -> None:
        program, _filter = QFileDialog.getOpenFileName(self, tr("Elegir el programa"))
        if program:
            self._launch([program, path], path)

    def _launch(self, command: list[str], path: str) -> None:
        try:
            platform_utils.run_program(command, os.path.dirname(path))
        except OSError as exc:
            self._report(tr("No se pudo abrir {basename}: {exc}", basename=os.path.basename(path), exc=exc))

    def build_file_menu(self, view: FileView) -> QMenu:
        """Menú de lo seleccionado: lo que se hace con esos archivos o carpetas."""
        entries = view.selected_entries()
        if view.path == COMPUTER_PATH:
            return self._build_drive_menu(view, entries)
        in_archive = archive.split_archive_path(view.path) is not None or any(e.in_archive for e in entries)  # también si es un comprimido desplegado en la lista
        on_server = remote.is_remote(view.path)
        local = not in_archive and not on_server  # lo que solo se hace con archivos del equipo
        folders = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        images = [e for e in files if e.extension in IMAGE_EXTENSIONS]
        menu = QMenu(self)
        add = lambda *args, **kwargs: self._add_command(menu, *args, **kwargs)
        if len(entries) == 1:
            menu.addAction(themed("goforward"), tr("Abrir"), lambda: view.open_entry(entries[0]))
        if len(entries) == 1 and files:
            self._add_open_with(menu, view, entries[0], local)
        if len(entries) == 1 and local and is_markdown(entries[0].name):
            menu.addAction(themed("rename"), tr("Editar Markdown"), lambda: self.open_markdown_editor(entries[0].path))
        if len(entries) == 1 and local and is_json(entries[0].name):
            menu.addAction(themed("show"), tr("Ver JSON"), lambda: self.open_json_viewer(entries[0].path))
        if len(entries) == 1 and archive.is_archive_file(entries[0].path):
            menu.addAction(themed("folder"), tr(context_menu.BROWSE_ARCHIVE), lambda: view.navigate(entries[0].path))
        if folders:
            add(tr("Abrir en una pestaña nueva"), "Go NEWTAB FROMSEL", icon="plus")
        if len(entries) == 1 and folders:
            menu.addAction(themed("favoritesadd"), tr("Añadir a favoritos"), lambda: self._add_favorite_dialog(view, folders[0].path))
        if images and not on_server:
            add(tr("Ver imágenes"), "Show", icon="show")
        if view.view_mode == "thumbnails":
            self._add_thumbnail_size_menu(menu, getattr(view.thumbs, "thumb_size", None))
        menu.addSeparator()
        add(tr("Copiar"), "Clipboard COPY", not on_server, "copy")
        add(tr("Cortar"), "Clipboard CUT", local)
        menu.addSeparator()
        add(tr("Copiar a destino"), "Copy", self.dest is not None, "copy")
        add(tr("Mover a destino"), "Copy MOVE", self.dest is not None and not in_archive, "move")
        add(tr("Duplicar aquí"), "Copy HERE", not in_archive)
        menu.addSeparator()
        add(tr("Renombrar"), "Rename" if len(entries) == 1 else "Rename ADVANCED", not in_archive and (len(entries) == 1 or not on_server), "rename")
        add(tr("Enviar a la papelera"), "Delete", local, "delete")
        add(tr("Eliminar definitivamente"), "Delete NORECYCLE", not in_archive, "deletepermanent")
        menu.addSeparator()
        if any(archive.is_archive_file(e.path) for e in entries):
            add(tr("Extraer"), "Extract", icon="extract")
        add(tr("Comprimir en ZIP…"), "Archive", local, "addtozip")
        if images and local:
            tools = menu.addMenu(themed("show"), tr("Imagen"))
            self._add_command(tools, tr("Convertir…"), "Image CONVERT")
            self._add_command(tools, tr("Girar 90° a la derecha"), "Image ROTATE=90 HERE REPLACE PRESERVEDATE")
            self._add_command(tools, tr("Girar 90° a la izquierda"), "Image ROTATE=270 HERE REPLACE PRESERVEDATE")
        if files and local:
            hashes = menu.addMenu(tr("Calcular hash"))
            for algorithm in fsops.HASH_ALGORITHMS:
                self._add_command(hashes, algorithm, f"GetSizes HASH={algorithm}")
        if folders and local:
            add(tr("Calcular tamaño"), "GetSizes", icon="getsizes")
        menu.addSeparator()
        add(tr("Copiar rutas como texto"), "Clipboard COPYNAMES")
        add(tr("Atributos…"), "SetAttr", local, "setattr")
        add(tr("Propiedades"), "Properties", not on_server, "properties")
        menu = self._arrange_menu(menu, context_menu.FILE)
        # La clave de la configuración sigue siendo la misma: solo cambia lo que se lee.
        shown = {tr("Copiar rutas como texto"): tr("Copiar ruta de la carpeta") if entries[0].is_dir else tr("Copiar ruta del archivo")} if len(entries) == 1 \
            else {tr("Renombrar"): tr("Renombrado masivo…")}
        for action in menu.actions():
            if action.text() in shown:
                action.setText(shown[action.text()])
        return menu

    def _arrange_menu(self, menu: QMenu, which: str) -> QMenu:
        """Deja el menú como se configuró en las preferencias. `shell_before` es la entrada delante de la que van
        las opciones de otros programas (None: al final). Si están ocultas, `shell_wanted` evita pedirlas a Windows."""
        self._award("context_menu")
        layout = context_menu.normalize(self.config.get("context_menus"))[which]
        # La configuración guarda las claves, que son los textos sin traducir.
        by_text = {action.text(): action for action in menu.actions() if not action.isSeparator()}
        actions = {key: by_text[tr(key)] for key in layout["order"] if tr(key) in by_text}
        for action in menu.actions():
            menu.removeAction(action)
        keys = context_menu.arranged_keys(layout, set(actions))
        menu.shell_wanted = context_menu.SHELL_ITEMS in keys
        menu.shell_before = None
        if menu.shell_wanted:
            # Respecto a una entrada real: las líneas de alrededor pueden desaparecer al ordenar el menú.
            following = keys[keys.index(context_menu.SHELL_ITEMS) + 1:]
            next_key = next((key for key in following if key != context_menu.SEPARATOR), None)
            menu.shell_before = actions[next_key] if next_key else None
        menu.open_with_place = actions[context_menu.OPEN_WITH] if context_menu.OPEN_WITH in keys else None
        menu.open_with_hidden = context_menu.OPEN_WITH in layout["hidden"]
        for key in keys:
            if key == context_menu.SEPARATOR:
                menu.addSeparator()
            elif key != context_menu.SHELL_ITEMS:
                menu.addAction(actions[key])
        tidy_separators(menu)
        return menu

    def _build_drive_menu(self, view: FileView, entries: list) -> QMenu:
        menu = QMenu(self)
        if len(entries) == 1:
            menu.addAction(themed("goforward"), tr("Abrir"), lambda: view.open_entry(entries[0]))
        self._add_command(menu, tr("Abrir en una pestaña nueva"), "Go NEWTAB FROMSEL", icon="plus")
        menu.addSeparator()
        self._add_command(menu, tr("Propiedades"), "Properties", icon="properties")
        return menu

    def build_folder_menu(self, view: FileView) -> QMenu:
        """Menú de la carpeta abierta: lo que se hace en ella, sin tocar lo seleccionado."""
        in_archive = view.path == COMPUTER_PATH or is_results(view.path) or archive.split_archive_path(view.path) is not None
        on_server = remote.is_remote(view.path)
        local = not in_archive and not on_server  # lo que solo se hace con carpetas del equipo
        menu = QMenu(self)
        add = lambda *args, **kwargs: self._add_command(menu, *args, **kwargs)
        add(tr("Pegar"), "Clipboard PASTE", bool(clipboard.read_files()[0]) and not in_archive)
        menu.addSeparator()
        add(tr("Nueva carpeta"), "CreateFolder", not in_archive, "makedir")
        add(tr("Nuevo archivo de texto"), "CreateFile" if on_server else "FileType NEW=.txt", not in_archive, "newtextfile")
        add(tr("Nuevo ZIP vacío"), "CreateFolder ZIP", local, "addtozip")
        menu.addSeparator()
        modes = menu.addMenu(themed("cyclemodes"), tr("Ver"))
        for label, icon, mode in ((tr("Lista"), "listmode", "list"), (tr("Detalles"), "detailsmode", "details"),
                                  (tr("Iconos pequeños"), "smallicons", "smallicons"), (tr("Iconos grandes"), "largeicons", "largeicons"),
                                  (tr("Mosaicos"), "tile", "tiles"), (tr("Miniaturas"), "thumbnails", "thumbnails")):
            item = self._add_command(modes, label, f"Set VIEW={mode}", icon=icon)
            item.setCheckable(True)
            item.setChecked(view.view_mode == mode)
        if view.view_mode == "thumbnails":
            self._add_thumbnail_size_menu(menu, getattr(view.thumbs, "thumb_size", None))
        order = menu.addMenu(tr("Ordenar por"))
        for label, key in ((tr("Nombre"), "name"), (tr("Tamaño"), "size"), (tr("Tipo"), "type"), (tr("Modificación"), "modified"),
                           (tr("Atributos"), "attrs")):
            self._add_command(order, label, f"Set SORTBY={key}")
        order.addSeparator()
        self._add_command(order, tr("Invertir el orden"), "Set SORTREVERSE=Toggle")
        flat = add(tr("Vista plana"), "Set FLATVIEW=Toggle", local, "flat")
        flat.setCheckable(True)
        flat.setChecked(view.flat)
        hidden = add(tr("Mostrar ocultos"), "Set HIDDEN=Toggle")
        hidden.setCheckable(True)
        hidden.setChecked(bool(self.config.get("show_hidden", False)))
        menu.addSeparator()
        add(tr("Seleccionar todo"), "Select ALL", bool(view.model.entries), "selectall")
        add(tr("Actualizar"), "Go REFRESH", icon="refresh")
        add(tr("Calcular tamaños de carpetas"), "GetSizes", local, "getsizes")
        add(tr("Uso del disco…"), "DiskUsage", local, "diskusage")
        menu.addSeparator()
        add(tr("Abrir en una pestaña nueva"), "Go TABNEW", icon="plus")
        can_split = styles.pane_count(self.dual) < styles.MAX_PANES
        add(tr("Dividir panel - Horizontal"), "Set SPLIT=Horizontal", can_split, "dualhorizontal")
        add(tr("Dividir panel - Vertical"), "Set SPLIT=Vertical", can_split, "dualdisplay")
        add(tr("Cerrar este panel"), "Set PANE=Close", self.dual != "Off", "close")
        add(tr("Solo la terminal en este panel"), "Set TERMINALPANE=On", len(self._file_panes()) > 1, "terminalonly")
        add(tr("Panel remoto (SFTP)…"), "Set SFTP=Menu", not on_server, "remote")
        if on_server:
            add(tr("Desconectar del servidor"), "Set SFTP=Off", icon="disconnect")
        add(tr("Abrir terminal aquí"), "CLI DOSPROMPT", not in_archive and not remote.is_remote(view.path), "terminal")
        add(tr("Añadir a favoritos"), "Favorites ADDDIALOG", icon="favoritesadd")
        menu.addAction(tr("Copiar la ruta de esta carpeta"), lambda: QApplication.clipboard().setText(view.path))
        menu.addSeparator()
        add(tr("Propiedades de esta carpeta"), "Properties CURRENT", local, "properties")
        return self._arrange_menu(menu, context_menu.FOLDER)

    def _add_thumbnail_size_menu(self, menu: QMenu, current_size: int | None = None) -> QMenu:
        size_menu = menu.addMenu(themed("thumbnails"), tr("Tamaño de las miniaturas"))
        current = current_size if current_size in THUMB_SIZES else self._thumbnail_size()
        for size in THUMB_SIZES:
            item = self._add_command(size_menu, tr("{size} píxeles", size=size), f"Set THUMBNAILSIZE={size}")
            item.setCheckable(True)
            item.setChecked(size == current)
        return size_menu

    def _show_file_menu(self, view: FileView, position) -> None:
        self._view_activated(view)
        paths = [e.path for e in view.selected_entries()]
        usable = paths and not any(archive.is_inside_archive(p) for p in paths)
        self._exec_with_shell_items(self.build_file_menu(view), position,
                                    (lambda window: windows_shell.menu_for_items(paths, window)) if usable else None)

    def _show_folder_menu(self, view: FileView, position) -> None:
        self._view_activated(view)
        path = view.path
        usable = path != COMPUTER_PATH and not is_results(path) and archive.split_archive_path(path) is None
        self._exec_with_shell_items(self.build_folder_menu(view), position,
                                    (lambda window: windows_shell.menu_for_folder(path, window)) if usable else None)

    def _exec_with_shell_items(self, menu: QMenu, position, create) -> None:
        """Muestra el menú con las opciones del sistema y de otros programas, si están activadas y disponibles."""
        shell = None
        if create is not None and self.config.get("shell_menu_items", True) and getattr(menu, "shell_wanted", True):
            try:
                shell = create(int(self.winId()))
                if shell is not None:
                    hidden = set(context_menu.normalize(self.config.get("context_menus"))["shell_hidden"])
                    if getattr(menu, "open_with_hidden", False):
                        hidden.add(windows_shell.OPEN_WITH_VERB)
                    add_shell_items(menu, shell, lambda message: self.statusBar().showMessage(f"⚠ {message}", 8000),
                                    getattr(menu, "shell_before", LAST_GROUP), hidden, getattr(menu, "open_with_place", None),
                                    getattr(menu, "open_with_first", None))
            except OSError as exc:  # sin esas opciones, el menú propio sigue funcionando
                self.log.log(tr("No se pudo leer el menú del sistema: {exc}", exc=exc))
        try:
            self._run_menu(menu, position)
        finally:
            if shell is not None:
                shell.close()

    def _run_menu(self, menu: QMenu, position) -> None:
        menu.exec(position)

    # ------------------------------------------------------------------ trabajos
    def _refresh_all(self) -> None:
        for view in self.all_views():
            view.refresh()

    def _job_finished(self, title: str, result: OpResult) -> None:
        self._refresh_all()
        for path, message in result.errors:
            self.log.log(f"{title}: {path}: {message}")
        if result.cancelled:
            self._report(tr("{title}: cancelado", title=title))
        elif result.errors:
            self._report(tr("{title}: {count} errores", title=title, count=len(result.errors)))
            if not result.unattended:
                self._set_utility("log")
                self._job_error_dialog(title, result)
        else:
            detail = tr(" ({skipped} omitidos)", skipped=result.skipped) if result.skipped else ""
            self.statusBar().showMessage(result.summary or tr("{title}: terminado{detail}", title=title, detail=detail), 8000 if result.summary else 5000)

    def _job_error_dialog(self, title: str, result: OpResult) -> None:
        sample = "\n".join(f"{os.path.basename(p)}: {m}" for p, m in result.errors[:5])
        QMessageBox.warning(self, title, tr("{count} errores. Mira el registro para el detalle.\n\n{sample}", count=len(result.errors), sample=sample))

    # ------------------------------------------------------------------ sesión
    def _restore_session(self, start_paths: list[str]) -> None:
        session = self.config.get("session") or {}
        if not self.config.get("restore_session", True):
            session = {"geometry": session.get("geometry")}  # dónde estaba la ventana no es parte de la disposición
            self.config["style"] = DEFAULT_STYLE_NAME
        self._apply_session(session, start_paths)
        name = session.get("name")
        self._current_session = name if isinstance(name, str) and sessions.find(self.config.get("saved_sessions"), name) is not None else ""
        self._session_baseline = self._session_snapshot()
        self._refresh_session_box()
        QTimer.singleShot(0, self._check_pane_branches)

    def _apply_session(self, session: dict, start_paths: list[str], live: bool = False) -> None:
        """`live`: con la ventana ya abierta, al cargar una sesión guardada. Ahí la ventana no se mueve de sitio."""
        home = platform_utils.home_dir()
        stored = session.get("tabs") or [[home], [home]]
        stored_names = session.get("tab_names") or []
        for side in range(styles.MAX_PANES):
            tabs = stored[side] if side < len(stored) and isinstance(stored[side], list) else []
            names = stored_names[side] if side < len(stored_names) and isinstance(stored_names[side], list) else []
            valid = [(p, names[i] if i < len(names) and isinstance(names[i], str) else "") for i, p in enumerate(tabs)
                     if isinstance(p, str) and (_location_exists(p) or self._can_reconnect(p))]
            if not valid and side < 2:
                valid = [(home, "")]
            for path, name in valid:
                view = self.groups[side].add_tab(path, activate=False)
                if name:
                    view.tab_name = name
                    self.groups[side].retitle(view)
        for side, path in enumerate(start_paths[:2]):
            if path == COMPUTER_PATH and has_computer_view():
                self.groups[side].current.navigate(path)
            elif os.path.isdir(path) or archive.split_archive_path(path):
                self.groups[side].current.navigate(os.path.abspath(path))
        for group in self.groups:
            group.setCurrentIndex(0)
        geometry = session.get("geometry")
        if geometry and not live:
            self.restoreGeometry(QByteArray.fromHex(geometry.encode()))
        self.active_side = 0
        style = self._style_named(self.config.get("style", "")) or self._style_named("Vertical doble panel")
        if style is not None:
            self.apply_style(style["name"])
        else:
            self.set_dual("Vertical")
        dual = session.get("dual")
        if styles.valid_layout(dual):
            self.set_dual(dual)
        state = session.get("state")
        if state:
            # Un estado de otra versión de la disposición se descarta y se usa la predeterminada.
            self.restoreState(QByteArray.fromHex(state.encode()), LAYOUT_STATE_VERSION)
            self._docks_viewer_above = session.get("viewer_above_metadata")
            if self._docks_viewer_above != bool(self.config.get("viewer_above_metadata", True)):
                self._arrange_side_docks()
            if self.double_tree:
                self.tree_dock.hide()
        splitter = session.get("splitter")
        if splitter:
            self.splitter.restoreState(QByteArray.fromHex(splitter.encode()))
            self._panes_to_balance = False
        for nest, saved in zip(self._pane_nests, session.get("pane_nests") or ()):
            nest.restoreState(QByteArray.fromHex(str(saved).encode()))
        count = styles.pane_count(self.dual)
        for index, (side, panel, saved) in enumerate(zip(self.sides, self.tree_panels, session.get("pane_trees") or ())):
            if isinstance(saved, dict):  # también sin un árbol por panel: cada uno puede mostrar el suyo
                side.restoreState(QByteArray.fromHex(str(saved.get("sizes", "")).encode()))
                panel.setVisible(bool(saved.get("visible", self.double_tree)) and index < count)
        panes = session.get("pane_terminals")
        stored = {**dict(enumerate(panes[:GENERAL_TERMINAL] if isinstance(panes, list) else ())), GENERAL_TERMINAL: session.get("general_terminal")}
        for index, saved in stored.items():
            if not isinstance(saved, dict):
                continue
            self.terminals[index].following = saved.get("follow") is not False
            self._refresh_follow_button(index)
            if live and isinstance(saved.get("folder"), str):
                self._terminal_start_folders[index] = saved["folder"]
            if isinstance(saved.get("shell"), str):
                self._terminal_choices[index] = saved["shell"]
                if saved["shell"].startswith(SSH_CHOICE):
                    self._terminal_pinned.add(index)
            self._style_sizing.discard(index)  # manda el tamaño guardado, si lo hay, sobre el del estilo
            if not saved.get("visible"):
                self._show_terminal(index, False)  # el estilo la abre, pero el usuario la cerró
            elif self._terminal_can_show(index):
                self.terminal_panels[index].show()
                docked = index == GENERAL_TERMINAL and self._general_docked
                if bool(saved.get("docked")) != docked:  # se cambió la preferencia a mano en el archivo: lo guardado no vale
                    QTimer.singleShot(0, lambda i=index: self._size_terminal(i))
                    continue
                if not docked:
                    self.terminal_areas[index].restoreState(QByteArray.fromHex(str(saved.get("sizes", "")).encode()))
                elif isinstance(saved.get("height"), int):  # el estado de la ventana no sirve: se guardó sin la terminal dentro
                    QTimer.singleShot(0, lambda height=saved["height"]: self.resizeDocks([self.general_dock], [height], Qt.Orientation.Vertical))
                self._terminals_sized[index] = self._terminal_place(index)
        if isinstance(session.get("terminal_only"), list):  # las sesiones de antes no lo dicen: se quedan los que ponga el estilo
            self._restore_terminal_only(session["terminal_only"])
        if live:
            self._restore_tab_choices(session)
        QTimer.singleShot(0, self._reconnect_remote_tabs)  # con la ventana ya a la vista: puede haber que preguntar algo
        self._orient_terminals()
        self.source.focus_list()
        self._sync_tree()

    def _restore_terminal_only(self, saved: list) -> None:
        wanted = {entry["pane"]: (entry.get("follow") is not False, bool(entry.get("tree"))) for entry in saved
                  if isinstance(entry, dict) and isinstance(entry.get("pane"), int) and 0 <= entry["pane"] < styles.pane_count(self.dual)}
        for side in sorted(set(self._terminal_only) - set(wanted)):
            self._leave_terminal_only(side)
        for side, before in sorted(wanted.items()):
            if side in self._terminal_only:
                self._terminal_only[side] = before
            elif not self.terminal_panels[side].isHidden() and [pane for pane in self._file_panes() if pane != side]:
                self._enter_terminal_only(side, before)  # su terminal ya sigue o no según lo guardado
        self._style_terminal_panes &= set(self._terminal_only)
        if self.active_side in self._terminal_only:
            self.active_side = self._file_panes()[0]
        self._after_terminal_only_change()

    def _restore_tab_choices(self, session: dict) -> None:
        """Lo que solo guarda una sesión con nombre: en cada panel, la pestaña elegida y cómo se ve cada una, y qué panel estaba activo."""
        current_tabs = session.get("current_tabs") if isinstance(session.get("current_tabs"), list) else []
        view_modes = session.get("view_modes") if isinstance(session.get("view_modes"), list) else []
        for side, group in enumerate(self.groups):
            modes = view_modes[side] if side < len(view_modes) and isinstance(view_modes[side], list) else []
            for view, mode in zip(group.views(), modes):
                if mode == "details" or mode in view.thumbs.MODES:  # lo guardado puede venir editado a mano
                    view.set_view_mode(mode)
            wanted = current_tabs[side] if side < len(current_tabs) else 0
            if isinstance(wanted, int) and 0 <= wanted < group.count():
                group.setCurrentIndex(wanted)
        side = session.get("active_side")
        if isinstance(side, int) and side in self._file_panes():
            self.active_side = side
            self._update_roles()

    def _terminal_session(self, index: int) -> dict:
        terminal = self.terminals[index]
        return {"sizes": bytes(self.terminal_areas[index].saveState().toHex()).decode(), "visible": not self.terminal_panels[index].isHidden(),
                "follow": self.terminals[index].following, "shell": self._terminal_choices.get(index), "folder": terminal.current_path() if terminal.session is not None else "",
                "docked": index == GENERAL_TERMINAL and self._general_docked, "height": self.terminal_panels[index].height()}

    def save_session(self) -> None:
        self.config["session"] = {**self._session_snapshot(), "name": self._current_session}
        self._save_config()

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            self.log.log(tr("No se pudo guardar la configuración: {exc}", exc=exc))

    def _layout_description(self) -> dict:
        """Cómo está colocada la ventana, en el formato de un estilo: con él se dibuja el esquema de la sesión."""
        current = self._style_named(self.config.get("style", "")) or {}
        layout = {"dual": self.dual, "tree": not self.tree_dock.isHidden() or (self.double_tree and any(not panel.isHidden() for panel in self.tree_panels)), "double_tree": self.double_tree,
                  "viewer": not self.viewer_dock.isHidden(), "meta": not self.meta_dock.isHidden(),
                  "terminals": [name for index, name in enumerate(styles.TERMINALS) if not self.terminal_panels[index].isHidden()],
                  "terminal_panes": self._terminal_pane_names()}
        if current.get("viewer_area") in ("top", "bottom"):
            layout["viewer_area"] = current["viewer_area"]
        if self._terminal_position:
            layout["terminal_position"] = self._terminal_position
        return layout

    def _session_snapshot(self) -> dict:
        return {
            "layout": self._layout_description(),
            "style": self.config.get("style", ""),
            "active_side": self.active_side,
            "current_tabs": [group.currentIndex() for group in self.groups],
            "view_modes": [[v.view_mode for v in group.views()] for group in self.groups],
            "tabs": [[v.path for v in group.views()] for group in self.groups],
            "tab_names": [[v.tab_name for v in group.views()] for group in self.groups],
            "dual": self.dual,
            "geometry": bytes(self.saveGeometry().toHex()).decode(),
            "state": bytes(self.saveState(LAYOUT_STATE_VERSION).toHex()).decode(),
            "splitter": bytes(self.splitter.saveState().toHex()).decode(),
            "pane_nests": [bytes(nest.saveState().toHex()).decode() for nest in self._pane_nests],
            "viewer_above_metadata": self._docks_viewer_above,
            "pane_trees": [{"sizes": bytes(side.saveState().toHex()).decode(), "visible": not panel.isHidden()}
                           for side, panel in zip(self.sides, self.tree_panels)],
            "pane_terminals": [self._terminal_session(index) for index in range(GENERAL_TERMINAL)],
            "terminal_only": [{"pane": side, "follow": following, "tree": tree} for side, (following, tree) in sorted(self._terminal_only.items())],
            "general_terminal": self._terminal_session(GENERAL_TERMINAL),
        }

    def open_external_paths(self, paths: list[str]) -> None:
        """Llegan de otra invocación del programa: una carpeta abierta desde fuera. Cada una, en una pestaña del panel activo."""
        group = self.groups[self.active_side]
        for path in paths:
            if os.path.isdir(path) or archive.split_archive_path(path):
                group.add_tab(os.path.abspath(path), activate=True)
            else:
                self.statusBar().showMessage(tr("No se puede abrir: {path}", path=path), 6000)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        # Spontaneous: solo el botón de la ventana, no el menú Salir.
        if event.spontaneous() and self.folder_tab_settings()["window_close_closes_tab"]:
            group = self.groups[self.active_side]
            if group.count() > 1:
                group.close_tab(group.currentIndex())
                event.ignore()
                return
        # Lo pide el usuario: el botón de la ventana o Salir. Cerrarla desde el propio programa, como al reiniciar, no pregunta otra vez.
        if event.spontaneous() or self._exit_asked:
            if not self._confirm_exit(self._exit_asked == "always"):
                event.ignore()
                return
            if self.jobs.running:
                self.jobs.cancel_all()
                self.jobs.pool.waitForDone(5000)
        elif self.jobs.running:
            if not self._confirm(tr("Hay {running} operaciones en curso. ¿Cancelarlas y salir?", running=self.jobs.running), True):
                event.ignore()
                return
            self.jobs.cancel_all()
            self.jobs.pool.waitForDone(5000)
        if self.find_window is not None:
            self.find_window.hide()  # guarda dónde estaba
        self.save_session()
        for terminal in self.terminals:
            terminal.dispose()
        self.preview.dispose()
        for tree in self.all_trees():
            tree.dispose()
        for view in self.all_views():
            view.dispose()
        for viewer in self._viewers:
            if _alive(viewer):
                viewer.close()
        for temp in self._temp_dirs:
            shutil.rmtree(temp, ignore_errors=True)
        self.remote_files.dispose()
        remote.disconnect_all()
        QThreadPool.globalInstance().waitForDone(2000)
        event.accept()


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normpath(path), os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _alive(widget) -> bool:
    import shiboken6
    return shiboken6.isValid(widget)
