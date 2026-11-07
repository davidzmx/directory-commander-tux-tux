"""Diálogos: atributos, preferencias y propiedades."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QScrollArea,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import bar_buttons, fsops, i18n, pane_colors, shells, styles
from ..core.config import GENERATION_MARK_KINDS, THUMB_SIZES, config_dir
from ..core.exiftool import find_exiftool
from ..core.media_info import find_tool
from ..core.i18n import tr
from ..core.listing import Entry, format_size, format_time
from .open_external import open_externally
from .background import run_bg
from .bar_buttons_page import BarButtonsPage
from . import theme
from .color_button import ColorButton
from .styles_page import StylesPage
from .context_menu_pages import ContextMenuPages
from .favorites_pages import FavoritesPage, RecentPage, FrequentFoldersPage, FrequentPointsPage
from .preference_pages import PreferencePages
from .sessions_page import SessionsPage
from .sftp import SftpPage
from .system_pages import ComparePage, FileManagerPage, GitPage
from .tab_settings_pages import TabSettingsPages


_ATTR_LABELS = {"r": tr("Solo lectura"), "h": tr("Oculto"), "s": tr("Sistema"), "a": tr("Listo para archivar"),
                "ur": tr("Propietario: leer"), "uw": tr("Propietario: escribir"), "ux": tr("Propietario: ejecutar"),
                "gr": tr("Grupo: leer"), "gw": tr("Grupo: escribir"), "gx": tr("Grupo: ejecutar"),
                "or": tr("Otros: leer"), "ow": tr("Otros: escribir"), "ox": tr("Otros: ejecutar")}


class SetAttrDialog(QDialog):
    """Con varios elementos solo se aplican las casillas que cambie el usuario: una casilla
    con estado mixto se queda intermedia y no toca esos atributos."""

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Atributos"))
        current = [fsops.get_attributes(p) for p in paths]
        self.boxes: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("{count} elemento(s)", count=len(paths))))
        for key in current[0]:
            box = QCheckBox(_ATTR_LABELS[key])
            values = {c[key] for c in current}
            if len(values) == 1:
                box.setChecked(values.pop())
            else:
                box.setTristate(True)
                box.setCheckState(Qt.CheckState.PartiallyChecked)
            layout.addWidget(box)
            self.boxes[key] = box
        self._initial = {k: b.checkState() for k, b in self.boxes.items()}
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes(self) -> dict[str, bool]:
        result = {}
        for key, box in self.boxes.items():
            state = box.checkState()
            if state != self._initial[key] and state != Qt.CheckState.PartiallyChecked:
                result[key] = state == Qt.CheckState.Checked
        return result


def _scrolling_page(content) -> QScrollArea:
    """Una página que, si no cabe, se desplaza hacia abajo en lugar de agrandar la ventana. A lo ancho no: se ajusta a la ventana."""
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.addLayout(content)
    layout.addStretch(1)
    scroll = QScrollArea()
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll


class PreferencesDialog(QDialog):
    applied = Signal()  # se pulsó Aplicar: los cambios ya están en la configuración

    def __init__(self, config, parent=None, sample_folder: str = "", sample_items: list[str] | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Preferencias"))
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        self.resize(900, min(640, screen.height() - 80) if screen is not None else 640)  # que se vean los botones de abajo
        self.theme = QComboBox()
        for label, value in ((tr("Oscuro"), "dark"), (tr("Claro"), "light"), (tr("Según el escritorio"), "auto")):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(config.get("theme", "dark"))))
        self.language_folder = getattr(config, "language_folder", "") or os.path.join(config_dir(), i18n.LANGUAGE_FOLDER)
        self.language = QComboBox()
        self.language.addItem(tr("Automático (idioma del sistema)"), i18n.AUTO)
        for language in sorted(i18n.available_languages(self.language_folder).values(), key=lambda item: item["name"].casefold()):
            self.language.addItem(language["name"], language["code"])
        self.language.setCurrentIndex(max(0, self.language.findData(config.get("language", i18n.AUTO))))
        self.language.setToolTip(tr("Se aplica al reiniciar el programa"))
        languages_folder = QPushButton(tr("Carpeta de idiomas"))
        languages_folder.setToolTip(tr("Los idiomas que dejes en esta carpeta aparecen en la lista la próxima vez que abras las preferencias"))
        languages_folder.clicked.connect(self._open_language_folder)
        new_language = QPushButton(tr("Crear traducción"))
        new_language.setToolTip(tr("Crea en la carpeta de idiomas un archivo con todos los textos del programa, listo para traducirlos"))
        new_language.clicked.connect(self._create_translation)
        language_row = QHBoxLayout()
        language_row.addWidget(self.language, 1)
        language_row.addWidget(languages_folder)
        language_row.addWidget(new_language)
        self.dest_tint = QCheckBox(tr("Teñir el fondo del panel de destino"))
        self.dest_tint.setChecked(config.get("dest_tint", True))
        self.confirm = QCheckBox(tr("Confirmar antes de eliminar"))
        self.confirm.setChecked(config.get("confirm_delete", True))
        self.general_status_bar = QCheckBox(tr("Mostrar la barra de estado general, abajo de la ventana"))
        self.general_status_bar.setToolTip(tr("El archivo bajo el ratón o lo seleccionado en el panel activo, el espacio libre y los avisos. "
                                              "Sin ella, los avisos van en la fila de estado del panel activo"))
        self.general_status_bar.setChecked(config.get("general_status_bar", True) is not False)
        self.pane_free_space = QComboBox()
        for label, value in ((tr("Solo en los paneles con una carpeta de otro disco"), "other_disk"), (tr("En todos los paneles"), "always"),
                             (tr("En ninguno: solo en la barra general"), "never")):
            self.pane_free_space.addItem(label, value)
        self.pane_free_space.setCurrentIndex(max(0, self.pane_free_space.findData(config.get("pane_free_space", "other_disk"))))
        self.pane_free_space.setToolTip(tr("Con la barra de estado general, dónde más se muestra el espacio libre. Sin ella, se ve en todos los paneles"))
        self.general_status_bar.toggled.connect(self.pane_free_space.setEnabled)
        self.pane_free_space.setEnabled(self.general_status_bar.isChecked())
        self.list_column_chars = QSpinBox()
        self.list_column_chars.setRange(0, 300)
        self.list_column_chars.setSuffix(tr(" caracteres"))
        self.list_column_chars.setSpecialValueText(tr("Sin límite"))
        self.list_column_chars.setToolTip(tr("Un nombre más largo se recorta por el medio, con «…», y se ve entero al pasar el ratón"))
        chars = config.get("list_column_chars", 50)
        self.list_column_chars.setValue(chars if isinstance(chars, int) and not isinstance(chars, bool) else 50)
        self.thumbnail_size = QComboBox()
        for size in THUMB_SIZES:
            self.thumbnail_size.addItem(tr("{size} píxeles", size=size), size)
        self.thumbnail_size.setCurrentIndex(max(0, self.thumbnail_size.findData(config.get("thumbnail_size", 128))))
        self.video_thumbnails = QComboBox()
        for label, value in ((tr("Cuatro fotogramas y la duración"), "four"), (tr("Un fotograma y la duración"), "one"), (tr("Ninguna: el icono"), "off")):
            self.video_thumbnails.addItem(label, value)
        self.video_thumbnails.setCurrentIndex(max(0, self.video_thumbnails.findData(config.get("video_thumbnails", "four"))))
        self.video_thumbnails.setToolTip(tr("Los fotogramas los saca FFmpeg, si está instalado: cada vídeo tarda alrededor de un segundo, en segundo plano"))
        self.folder_previews = QCheckBox(tr("En las miniaturas, mostrar en cada carpeta sus primeras imágenes"))
        self.folder_previews.setChecked(config.get("folder_previews") is not False)
        self.generation_marks = QCheckBox(tr("Marcar si una imagen o un vídeo lleva su prompt (P) o su workflow (W)"))
        self.generation_marks.setToolTip(tr("También se muestran y ocultan con su botón de la barra. Se leen sus metadatos en segundo plano; "
                                            "el prompt, el programa y sus ajustes se ven en el panel de metadatos"))
        self.generation_marks.setChecked(config.get("show_generation_marks") is True)
        self.generation_kinds = QComboBox()
        for label, value in ((tr("Las dos: prompt (P) y workflow (W)"), "both"), (tr("Solo la del prompt (P)"), "prompt"), (tr("Solo la del workflow (W)"), "workflow")):
            self.generation_kinds.addItem(label, value)
        kinds = config.get("generation_mark_kinds", "both")
        self.generation_kinds.setCurrentIndex(max(0, self.generation_kinds.findData(kinds if kinds in GENERATION_MARK_KINDS else "both")))
        self.generation_marks.toggled.connect(self.generation_kinds.setEnabled)
        self.generation_kinds.setEnabled(self.generation_marks.isChecked())
        self.default_sort = QComboBox()
        for label, value in ((tr("Tipo"), "type"), (tr("Nombre"), "name"), (tr("Tamaño"), "size"), (tr("Fecha de modificación"), "modified")):
            self.default_sort.addItem(label, value)
        self.default_sort.setCurrentIndex(max(0, self.default_sort.findData(config.get("default_sort", "type"))))
        self.default_sort.setToolTip(tr("Con qué orden empieza cada pestaña. Las carpetas van siempre primero; dentro de cada tipo, por nombre"))
        self.default_sort_reverse = QCheckBox(tr("Al revés"))
        self.default_sort_reverse.setChecked(config.get("default_sort_reverse") is True)
        self.remember_folder_views = QCheckBox(tr("Recordar la vista y el orden de cada carpeta"))
        self.remember_folder_views.setToolTip(tr("Al volver a una carpeta se ve como la dejaste. Una carpeta sin nada recordado sigue la vista de la pestaña"))
        self.remember_folder_views.setChecked(config.get("remember_folder_views") is not False)
        self.verify_copies = QCheckBox(tr("Comprobar cada copia comparándola con el original (más lento)"))
        self.verify_copies.setToolTip(tr("Al terminar cada archivo se vuelve a leer del disco y se compara su hash con el del original. Si no son iguales, "
                                         "la copia se descarta y, al mover, el original se conserva. Para una sola copia, «Copiar a destino y comprobar la copia» "
                                         "del botón Copiar"))
        self.verify_copies.setChecked(config.get("verify_copies") is True)
        self.confirm_exit = QCheckBox(tr("Confirmar antes de salir del programa"))
        self.confirm_exit.setToolTip(tr("Con conexiones SSH/SFTP abiertas se pregunta siempre, porque se cerrarán"))
        self.confirm_exit.setChecked(config.get("confirm_exit", True) is not False)
        self.resort_on_change = QCheckBox(tr("Reordenar la lista en cuanto se copia, se mueve o cambia un archivo"))
        self.resort_on_change.setToolTip(tr("Sin marcar, lo que ya se ve no se mueve y lo nuevo aparece al final, hasta que se actualiza la carpeta (F5)"))
        self.resort_on_change.setChecked(config.get("resort_on_change") is True)
        self.rename_stem = QCheckBox(tr("Al renombrar, seleccionar solo el nombre, sin la extensión"))
        self.rename_stem.setChecked(config.get("rename_selects_stem") is not False)
        self.single_click = QCheckBox(tr("Abrir archivos y carpetas con un clic; seleccionar con la marca + de su icono"))
        self.single_click.setToolTip(tr("Al pasar el ratón por un archivo aparece un + sobre su icono: púlsalo para seleccionarlo. "
                                        "Ctrl y Mayús siguen seleccionando como siempre"))
        self.single_click.setChecked(config.get("single_click") is True)
        self.hover_info = QCheckBox(tr("Mostrar información al pasar el ratón por un archivo"))
        self.hover_info.setChecked(config.get("hover_info", True))
        self.level_colors = QCheckBox(tr("Mostrar en otro color los niveles de las carpetas desplegadas"))
        self.level_colors.setChecked(config.get("tree_level_colors", True))
        self.open_images = QComboBox()
        for label, value in ((tr("Visor interno"), "viewer"), (tr("Aplicación predeterminada del sistema"), "system")):
            self.open_images.addItem(label, value)
        self.open_images.setCurrentIndex(max(0, self.open_images.findData(config.get("open_images", "viewer"))))
        self.open_videos = QComboBox()
        for label, value in ((tr("Visor interno"), "viewer"), (tr("Aplicación predeterminada del sistema"), "system")):
            self.open_videos.addItem(label, value)
        self.open_videos.setCurrentIndex(max(0, self.open_videos.findData(config.get("open_videos", "viewer"))))
        self.open_markdown = QComboBox()
        for label, value in ((tr("Editor interno, con el texto y cómo se ve"), "editor"), (tr("Aplicación predeterminada del sistema"), "system")):
            self.open_markdown.addItem(label, value)
        self.open_markdown.setCurrentIndex(max(0, self.open_markdown.findData(config.get("open_markdown", "editor"))))
        self.open_json = QComboBox()
        for label, value in ((tr("Visor interno: árbol, formateado u original"), "viewer"), (tr("Aplicación predeterminada del sistema"), "system")):
            self.open_json.addItem(label, value)
        self.open_json.setCurrentIndex(max(0, self.open_json.findData(config.get("open_json", "viewer"))))
        self.open_archives = QComboBox()
        for label, value in ((tr("Aplicación predeterminada del sistema"), "system"), (tr("Explorarlos aquí, como una carpeta"), "browse")):
            self.open_archives.addItem(label, value)
        self.open_archives.setCurrentIndex(max(0, self.open_archives.findData(config.get("open_archives", "system"))))
        self.panel_order = QComboBox()
        self.panel_order.addItem(tr("Visor arriba y metadatos abajo"), True)
        self.panel_order.addItem(tr("Metadatos arriba y visor abajo"), False)
        self.panel_order.setCurrentIndex(0 if config.get("viewer_above_metadata", True) else 1)
        self.ffmpeg_dir = QLineEdit(config.get("ffmpeg_dir", ""))
        self.ffmpeg_dir.setPlaceholderText(tr("Opcional: carpeta con ffmpeg, para ver fotogramas de los vídeos"))
        browse = QPushButton(tr("Elegir…"))
        browse.clicked.connect(self._choose_ffmpeg)
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.addWidget(self.ffmpeg_dir, 1)
        ffmpeg_row.addWidget(browse)
        basics, opening, colors, programs = (QFormLayout() for _ in range(4))
        # Vistas y miniaturas, en grupos: sin ellos, lo de debajo de «Marcas de IA:» parecía parte de ellas.
        in_list, in_thumbnails, marks, browsing = (QFormLayout() for _ in range(4))
        basics.addRow(tr("Idioma:"), language_row)
        basics.addRow(tr("Tema:"), self.theme)
        colors.addRow("", self.dest_tint)
        basics.addRow("", self.confirm)
        basics.addRow("", self.confirm_exit)
        basics.addRow("", self.verify_copies)
        basics.addRow("", self.general_status_bar)
        basics.addRow(tr("Espacio libre en los paneles:"), self.pane_free_space)
        in_list.addRow(tr("Ancho máximo de las columnas de la lista:"), self.list_column_chars)
        in_list.addRow(self.level_colors)
        sort_row = QHBoxLayout()
        sort_row.addWidget(self.default_sort, 1)
        sort_row.addWidget(self.default_sort_reverse)
        in_list.addRow(tr("Orden al abrir una pestaña:"), sort_row)
        in_list.addRow(self.remember_folder_views)
        in_thumbnails.addRow(tr("Tamaño:"), self.thumbnail_size)
        in_thumbnails.addRow(tr("De los vídeos:"), self.video_thumbnails)
        in_thumbnails.addRow(self.folder_previews)
        marks.addRow(self.generation_marks)  # las de git, en Integración con Git
        marks.addRow(tr("Cuáles:"), self.generation_kinds)
        browsing.addRow(self.single_click)
        browsing.addRow(self.rename_stem)
        browsing.addRow(self.resort_on_change)
        browsing.addRow(self.hover_info)
        opening.addRow(tr("Abrir las imágenes con:"), self.open_images)
        opening.addRow(tr("Abrir los vídeos con:"), self.open_videos)
        opening.addRow(tr("Abrir los Markdown con:"), self.open_markdown)
        opening.addRow(tr("Abrir los JSON con:"), self.open_json)
        opening.addRow(tr("Abrir los comprimidos con:"), self.open_archives)
        self.unfold_archives = QCheckBox(tr("Desplegar los comprimidos en la lista, como las carpetas"))
        self.unfold_archives.setChecked(bool(config.get("unfold_archives", True)))
        opening.addRow("", self.unfold_archives)
        opening.addRow(tr("Con el visor y los metadatos a la vez:"), self.panel_order)
        self._custom_schemes = pane_colors.clean_customs(config.get("pane_color_customs"))
        self.pane_scheme = QComboBox()
        for key, (label, _colors) in pane_colors.BUILTIN_SCHEMES.items():
            self.pane_scheme.addItem(tr(label), key)
        for custom in self._custom_schemes:
            self.pane_scheme.addItem(custom["name"], pane_colors.custom_key(custom["name"]))
        self.pane_scheme.setCurrentIndex(max(0, self.pane_scheme.findData(config.get("pane_color_scheme", pane_colors.DEFAULT_SCHEME))))
        new_scheme = QPushButton(tr("Nueva…"))
        new_scheme.setToolTip(tr("Crea una combinación a partir de los colores que se ven"))
        new_scheme.clicked.connect(self._new_pane_scheme)
        self.delete_scheme = QPushButton(tr("Eliminar"))
        self.delete_scheme.clicked.connect(self._delete_pane_scheme)
        scheme_row = QHBoxLayout()
        scheme_row.addWidget(self.pane_scheme, 1)
        scheme_row.addWidget(new_scheme)
        scheme_row.addWidget(self.delete_scheme)
        colors.addRow(tr("Colores de los paneles:"), scheme_row)
        self.pane_colors = {role: ColorButton(title, "", "#000000") for role, title in (("source", tr("Panel activo")), ("dest", tr("Panel de destino")), ("other", tr("Otros paneles")))}
        colors_row = QHBoxLayout()
        for role, button in self.pane_colors.items():
            colors_row.addWidget(QLabel(button.title + ":"))
            colors_row.addWidget(button)
            button.chosen.connect(lambda r=role: self._scheme_color_chosen(r))
        colors_row.addStretch(1)
        colors.addRow("", colors_row)
        self.path_pale_light = QCheckBox(tr("En el tema claro"))
        self.path_pale_light.setChecked(bool(config.get("pane_path_pale_light", False)))
        self.path_pale_dark = QCheckBox(tr("En el tema oscuro"))
        self.path_pale_dark.setChecked(bool(config.get("pane_path_pale_dark", False)))
        pale_row = QHBoxLayout()
        pale_row.addWidget(self.path_pale_light)
        pale_row.addWidget(self.path_pale_dark)
        pale_row.addStretch(1)
        colors.addRow(tr("Ruta más tenue que la barra:"), pale_row)
        self.frames_on = QCheckBox(tr("Mostrar"))
        self.frames_on.setChecked(config.get("pane_frames_on", True) is not False)
        self.frames_on.setToolTip(tr("Un borde de color alrededor de cada panel, su terminal y su árbol, para ver qué va con qué. "
                                     "También se muestra y oculta con su botón de la barra de herramientas"))
        self.frame_mode = QComboBox()
        self.frame_mode.addItem(tr("Con más de"), "more_than")
        self.frame_mode.addItem(tr("Siempre"), "always")
        self.frame_mode.setCurrentIndex(max(0, self.frame_mode.findData(config.get("pane_frames", pane_colors.DEFAULT_FRAME_MODE))))
        self.frames_after = QSpinBox()
        self.frames_after.setRange(pane_colors.MIN_FRAMES_AFTER, pane_colors.MAX_FRAMES_AFTER)
        self.frames_after.setValue(pane_colors.frames_after(config.get("pane_frames_after")))
        self.frames_after_label = QLabel(tr("paneles"))
        frame_row = QHBoxLayout()
        for widget in (self.frames_on, self.frame_mode, self.frames_after, self.frames_after_label):
            frame_row.addWidget(widget)
        frame_row.addStretch(1)
        colors.addRow(tr("Marco de color de cada panel:"), frame_row)
        self.frame_width = self._frame_spin(1, pane_colors.MAX_FRAME_WIDTH, " px", pane_colors.frame_width(config.get("pane_frame_width")))
        self.frame_active_width = self._frame_spin(1, pane_colors.MAX_FRAME_WIDTH, " px",
                                                   pane_colors.frame_width(config.get("pane_frame_active_width"), pane_colors.DEFAULT_ACTIVE_FRAME_WIDTH))
        self.frame_dim = self._frame_spin(0, pane_colors.MAX_FRAME_DIM, " %", pane_colors.frame_dim(config.get("pane_frame_dim")))
        self.frame_dim.setToolTip(tr("Cuánto se atenúan los marcos de los paneles que no son el activo"))
        self.frame_dim_on = QCheckBox(tr("Atenuar los demás"))
        self.frame_dim_on.setChecked(config.get("pane_frame_dim_on") is True)
        sizes_row = QHBoxLayout()
        for label, widget in ((tr("Grosor:"), self.frame_width), (tr("Del panel activo:"), self.frame_active_width)):
            sizes_row.addWidget(QLabel(label))
            sizes_row.addWidget(widget)
        sizes_row.addWidget(self.frame_dim_on)
        sizes_row.addWidget(self.frame_dim)
        sizes_row.addStretch(1)
        colors.addRow("", sizes_row)
        chosen = pane_colors.clean_frame_colors(config.get("pane_frame_colors"))
        self.frame_colors = [ColorButton(tr("Panel {number}", number=index + 1), chosen[index], default) for index, default in enumerate(pane_colors.FRAME_COLORS)]
        self.reset_frame_colors = QPushButton(tr("Restablecer"))
        self.reset_frame_colors.setToolTip(tr("Vuelve a los colores por defecto"))
        self.reset_frame_colors.clicked.connect(lambda: [button.set_value("") for button in self.frame_colors])
        frame_colors_row = QHBoxLayout()
        for button in self.frame_colors:
            frame_colors_row.addWidget(button)
        frame_colors_row.addWidget(self.reset_frame_colors)
        frame_colors_row.addStretch(1)
        colors.addRow("", frame_colors_row)
        self.frame_mode.currentIndexChanged.connect(self._show_frame_options)
        self.frames_on.toggled.connect(self._show_frame_options)
        self.frame_dim_on.toggled.connect(self._show_frame_options)
        self._show_frame_options()
        self.path_folder_menus = QCheckBox(tr("Mostrar las subcarpetas al pulsar el › de la ruta"))
        self.path_folder_menus.setChecked(bool(config.get("path_folder_menus", False)))
        browsing.addRow(self.path_folder_menus)
        views = QVBoxLayout()
        for title, form in ((tr("Lista y detalles"), in_list), (tr("Miniaturas"), in_thumbnails), (tr("Marcas de IA"), marks),
                            (tr("Ratón y ruta"), browsing)):
            box = QGroupBox(title)
            box.setLayout(form)
            views.addWidget(box)
        self.theme.currentIndexChanged.connect(self._show_pane_scheme)
        self.pane_scheme.currentIndexChanged.connect(self._show_pane_scheme)
        self._show_pane_scheme()
        programs.addRow("FFmpeg:", ffmpeg_row)
        self.ffmpeg_found = self._program_status(self.ffmpeg_dir, lambda folder: find_tool("ffmpeg", folder), "ffmpeg")
        programs.addRow("", self.ffmpeg_found)
        self.exiftool_dir = QLineEdit(config.get("exiftool_dir", ""))
        self.exiftool_dir.setPlaceholderText(tr("Carpeta con exiftool, para el panel de metadatos. Vacío: se busca en el PATH"))
        browse_exiftool = QPushButton(tr("Elegir…"))
        browse_exiftool.clicked.connect(self._choose_exiftool)
        exiftool_row = QHBoxLayout()
        exiftool_row.addWidget(self.exiftool_dir, 1)
        exiftool_row.addWidget(browse_exiftool)
        programs.addRow("ExifTool:", exiftool_row)
        self.exiftool_found = self._program_status(self.exiftool_dir, find_exiftool, "exiftool")
        programs.addRow("", self.exiftool_found)
        self.terminal_shell = QComboBox()
        for shell in shells.available_shells():
            self.terminal_shell.addItem(shell.label, shell.key)
        self.terminal_shell.addItem(tr("Otro programa…"), shells.CUSTOM)
        self.terminal_shell.setCurrentIndex(max(0, self.terminal_shell.findData(config.get("terminal_shell", ""))))
        self.terminal_command = QLineEdit(config.get("terminal_command", ""))
        self.terminal_command.setPlaceholderText(tr("Programa y sus argumentos"))
        self.browse_terminal = QPushButton(tr("Elegir…"))
        self.browse_terminal.clicked.connect(self._choose_terminal)
        terminal_row = QHBoxLayout()
        terminal_row.addWidget(self.terminal_shell)
        terminal_row.addWidget(self.terminal_command, 1)
        terminal_row.addWidget(self.browse_terminal)
        programs.addRow(tr("Terminal:"), terminal_row)
        programs.addRow("", QLabel(tr("Con esta se abren las terminales. Cada una se puede cambiar después con la lista de su barra de título.")))
        self.general_terminal_full_width = QCheckBox(tr("Con los paneles en vertical, la terminal general ocupa todo el ancho de la ventana, también bajo el árbol de carpetas"))
        self.general_terminal_full_width.setChecked(bool(config.get("general_terminal_full_width", False)))
        programs.addRow("", self.general_terminal_full_width)
        self.find_place = QComboBox()
        self.find_place.addItem(tr("En una ventana aparte"), "window")
        self.find_place.addItem(tr("Abajo, en la ventana principal"), "bottom")
        self.find_place.setCurrentIndex(max(0, self.find_place.findData(config.get("find_panel_place", "window"))))
        basics.addRow(tr("Panel de buscar:"), self.find_place)
        self.terminal_shell.currentIndexChanged.connect(self._terminal_choice_changed)
        self._terminal_choice_changed()
        self.tabs = TabSettingsPages(config.get("folder_tabs"))
        self.pages = PreferencePages()
        # General, en varias páginas: en una sola no cabía en una pantalla normal.
        for title, form in ((tr("Idioma y avisos"), basics), (tr("Vistas y miniaturas"), views), (tr("Abrir archivos"), opening),
                            (tr("Colores y marcos"), colors), (tr("Programas"), programs)):
            self.pages.addTab(_scrolling_page(form), title, tr("General"))
        for title, page in self.tabs.pages:
            self.pages.addTab(page, title, tr("Pestañas"))
        self.menus = ContextMenuPages(config, sample_folder, sample_items)
        self.shell_items = self.menus.shell_items
        for title, page in self.menus.pages:
            self.pages.addTab(page, title, tr("Menús"))
        self.bar_buttons = BarButtonsPage(config)
        self.pages.addTab(self.bar_buttons, tr("Barras de botones"), tr("Barras y estilos"))
        self.styles_page = StylesPage(config)
        self.pages.addTab(self.styles_page, tr("Estilos"), tr("Barras y estilos"))
        self.favorites_page = FavoritesPage(config)
        self.recent_page = RecentPage(config)
        self.smart_page = FrequentFoldersPage(config)
        self.points_page = FrequentPointsPage(config)
        self.pages.addTab(self.favorites_page, tr("Lista de Favoritos"), tr("Rutas frecuentes"))
        self.pages.addTab(self.recent_page, tr("Lista de Recientes"), tr("Rutas frecuentes"))
        self.pages.addTab(self.smart_page, tr("Favoritos inteligentes"), tr("Rutas frecuentes"))
        self.pages.addTab(self.points_page, tr("Puntaje"), tr("Rutas frecuentes"))
        self.sessions_page = SessionsPage(config)
        self.pages.addTab(self.sessions_page, tr("Sesiones"))
        self.sftp_page = SftpPage(config)
        self.pages.addTab(self.sftp_page, tr("SSH/SFTP"))
        self.git_page = GitPage(config)
        self.git_page.barRequested.connect(lambda: self.show_bars(bar_buttons.GIT))
        self.pages.addTab(self.git_page, tr("Integración con Git"))
        self.compare_page = ComparePage(config)
        self.pages.addTab(self.compare_page, tr("Comparar archivos"))
        self.file_manager_page = FileManagerPage(config)
        self.pages.addTab(self.file_manager_page, tr("Administrador de archivos"))
        self.pages.currentChanged.connect(self._page_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Aceptar"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(tr("Aplicar"))
        self.apply_button.clicked.connect(self.apply)
        layout = QVBoxLayout(self)
        layout.addWidget(self.pages, 1)
        layout.addWidget(buttons)

    def apply(self) -> None:
        """Aplica los cambios sin cerrar la ventana."""
        self.apply_to(self.config)
        self.applied.emit()

    def show_bars(self, bar: str = "") -> None:
        """Abre la página de las barras de botones, en esa barra si se indica."""
        if bar == bar_buttons.STYLES:
            self.show_styles()
            return
        self.pages.setCurrentWidget(self.bar_buttons)
        self.bar_buttons.show_bar(bar)

    def show_styles(self) -> None:
        self.pages.setCurrentWidget(self.styles_page)

    def show_tabs(self) -> None:
        self.pages.setCurrentWidget(self.tabs.pages[0][1])

    def show_favorites(self) -> None:
        self.pages.setCurrentWidget(self.favorites_page)

    def show_sessions(self) -> None:
        self.pages.setCurrentWidget(self.sessions_page)

    def show_sftp(self) -> None:
        self.pages.setCurrentWidget(self.sftp_page)

    def _page_changed(self, _index: int) -> None:
        if self.pages.currentWidget() is self.menus.shell_page:
            self.menus.load_shell_options()

    def _open_language_folder(self) -> None:
        os.makedirs(self.language_folder, exist_ok=True)
        open_externally(self.language_folder)

    def _create_translation(self) -> None:
        try:
            path = i18n.write_template(self.language_folder)
        except OSError as exc:
            QMessageBox.warning(self, tr("Crear traducción"), str(exc))
            return
        QMessageBox.information(self, tr("Crear traducción"), tr(
            "Se creó {name}.\nCambia su nombre y su código, escribe las traducciones y vuelve a abrir las preferencias. "
            "Lo que dejes vacío se mostrará en español.", name=os.path.basename(path)))
        open_externally(self.language_folder)

    @staticmethod
    def _program_status(field: QLineEdit, finder, name: str) -> QLabel:
        """Debajo de la carpeta de un programa, cuál se está usando: el de esa carpeta o, si está vacía, el que se encontró en el sistema."""
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        def update() -> None:
            folder = field.text().strip()
            found = finder(folder)
            if found is None:
                label.setText(tr("No se encontró {name} en el sistema", name=name) if not folder else
                              tr("No se encontró {name} en esa carpeta ni en el sistema", name=name))
            elif folder and os.path.dirname(found) == os.path.normpath(folder):
                label.setText(tr("Se usa {path}", path=found))
            else:
                label.setText(tr("Se usa el del sistema: {path}", path=found))

        field.textChanged.connect(update)
        update()
        return label

    def _choose_ffmpeg(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta que contiene ffmpeg"), self.ffmpeg_dir.text())
        if folder:
            self.ffmpeg_dir.setText(os.path.normpath(folder))

    def _choose_exiftool(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta que contiene exiftool"), self.exiftool_dir.text())
        if folder:
            self.exiftool_dir.setText(os.path.normpath(folder))

    def _show_pane_scheme(self) -> None:
        """Los colores de la combinación con el tema elegido aquí. Las que trae el programa no se cambian: se crea otra a partir de ellas."""
        base = theme.resolve(self.theme.currentData())
        key = self.pane_scheme.currentData()
        colors = pane_colors.scheme_colors(key, self._custom_schemes, base.name)
        custom = key not in pane_colors.BUILTIN_SCHEMES
        for role, button in self.pane_colors.items():
            button.set_default(getattr(base, f"bar_{role}"))
            button.set_value(colors.get(role, ""))
            button.setEnabled(custom)
        self.delete_scheme.setEnabled(custom)

    @staticmethod
    def _frame_spin(low: int, high: int, suffix: str, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setValue(value)
        return spin

    def _show_frame_options(self) -> None:
        shown = self.frames_on.isChecked()
        for widget in (self.frame_mode, self.frame_width, self.frame_active_width, self.frame_dim_on, *self.frame_colors, self.reset_frame_colors):
            widget.setEnabled(shown)
        self.frame_dim.setEnabled(shown and self.frame_dim_on.isChecked())
        counted = shown and self.frame_mode.currentData() == "more_than"
        self.frames_after.setEnabled(counted)
        self.frames_after_label.setEnabled(counted)

    def _scheme_color_chosen(self, role: str) -> None:
        for custom in self._custom_schemes:
            if pane_colors.custom_key(custom["name"]) == self.pane_scheme.currentData():
                custom[role] = self.pane_colors[role].value

    def _new_pane_scheme(self) -> None:
        name, accepted = QInputDialog.getText(self, tr("Nueva combinación de colores"), tr("Nombre:"), text=tr("Mi combinación"))
        name = name.strip()
        if not accepted or not name:
            return
        taken = [custom["name"] for custom in self._custom_schemes]
        name = styles.unique_name(name, taken)
        self._custom_schemes.append({"name": name, **{role: button.value or button.default for role, button in self.pane_colors.items()}})
        self.pane_scheme.addItem(name, pane_colors.custom_key(name))
        self.pane_scheme.setCurrentIndex(self.pane_scheme.count() - 1)

    def _delete_pane_scheme(self) -> None:
        key = self.pane_scheme.currentData()
        if key not in pane_colors.BUILTIN_SCHEMES:
            self._custom_schemes = [custom for custom in self._custom_schemes if pane_colors.custom_key(custom["name"]) != key]
            self.pane_scheme.removeItem(self.pane_scheme.currentIndex())
            self.pane_scheme.setCurrentIndex(self.pane_scheme.findData(pane_colors.DEFAULT_SCHEME))

    def _terminal_choice_changed(self) -> None:
        custom = self.terminal_shell.currentData() == shells.CUSTOM
        self.terminal_command.setVisible(custom)
        self.browse_terminal.setVisible(custom)

    def _choose_terminal(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Programa de la terminal"))
        if path:
            path = os.path.normpath(path)
            self.terminal_command.setText(f'"{path}"' if " " in path else path)

    def apply_to(self, config) -> None:
        config["language"] = self.language.currentData()
        config["theme"] = self.theme.currentData()
        config["dest_tint"] = self.dest_tint.isChecked()
        config["confirm_delete"] = self.confirm.isChecked()
        config["confirm_exit"] = self.confirm_exit.isChecked()
        config["verify_copies"] = self.verify_copies.isChecked()
        config["general_status_bar"] = self.general_status_bar.isChecked()
        config["pane_free_space"] = self.pane_free_space.currentData()
        config["list_column_chars"] = self.list_column_chars.value()
        config["thumbnail_size"] = self.thumbnail_size.currentData()
        config["video_thumbnails"] = self.video_thumbnails.currentData()
        config["folder_previews"] = self.folder_previews.isChecked()
        config["show_generation_marks"] = self.generation_marks.isChecked()
        config["generation_mark_kinds"] = self.generation_kinds.currentData()
        config["remember_folder_views"] = self.remember_folder_views.isChecked()
        config["default_sort"] = self.default_sort.currentData()
        config["default_sort_reverse"] = self.default_sort_reverse.isChecked()
        config["single_click"] = self.single_click.isChecked()
        config["rename_selects_stem"] = self.rename_stem.isChecked()
        config["resort_on_change"] = self.resort_on_change.isChecked()
        config["hover_info"] = self.hover_info.isChecked()
        config["open_images"] = self.open_images.currentData()
        config["open_videos"] = self.open_videos.currentData()
        config["open_archives"] = self.open_archives.currentData()
        config["open_markdown"] = self.open_markdown.currentData()
        config["open_json"] = self.open_json.currentData()
        config["tree_level_colors"] = self.level_colors.isChecked()
        config["viewer_above_metadata"] = bool(self.panel_order.currentData())
        config["shell_menu_items"] = self.shell_items.isChecked()
        config["context_menus"] = self.menus.values()
        config["ffmpeg_dir"] = self.ffmpeg_dir.text().strip()
        config["folder_tabs"] = self.tabs.values()
        config["bar_buttons"] = self.bar_buttons.values()
        config["bar_order"] = self.bar_buttons.order_values()
        self.bar_buttons.apply_git_to(config)
        self.styles_page.apply_to(config)  # detrás de las barras: un nombre cambiado debe cambiar también en ellas
        config["exiftool_dir"] = self.exiftool_dir.text().strip()
        command = self.terminal_command.text().strip()
        # La primera es la predeterminada: sin guardarla, sigue siéndolo aunque esta cambie o se instale otra.
        chosen = self.terminal_shell.currentData()
        config["terminal_shell"] = "" if self.terminal_shell.currentIndex() == 0 or (chosen == shells.CUSTOM and not command) else chosen
        config["terminal_command"] = command
        config["general_terminal_full_width"] = self.general_terminal_full_width.isChecked()
        config["pane_color_scheme"] = self.pane_scheme.currentData()
        config["saved_sessions"] = self.sessions_page.values()
        config["restore_session"] = self.sessions_page.restore.isChecked()
        config["find_panel_place"] = self.find_place.currentData()
        self.compare_page.apply_to(config)
        self.git_page.apply_to(config)
        self.sftp_page.apply_to(config)
        self.file_manager_page.apply_to(config)
        config["unfold_archives"] = self.unfold_archives.isChecked()
        config["path_folder_menus"] = self.path_folder_menus.isChecked()
        config["pane_path_pale_light"] = self.path_pale_light.isChecked()
        config["pane_path_pale_dark"] = self.path_pale_dark.isChecked()
        config["pane_color_customs"] = pane_colors.clean_customs(self._custom_schemes)
        config["pane_frames_on"] = self.frames_on.isChecked()
        config["pane_frames"] = self.frame_mode.currentData()
        config["pane_frames_after"] = self.frames_after.value()
        config["pane_frame_width"] = self.frame_width.value()
        config["pane_frame_active_width"] = self.frame_active_width.value()
        config["pane_frame_dim_on"] = self.frame_dim_on.isChecked()
        config["pane_frame_dim"] = self.frame_dim.value()
        colors = [button.value for button in self.frame_colors]
        config["pane_frame_colors"] = colors if any(colors) else []
        config["favorites"] = self.favorites_page.values()
        config["favorites_autosort"] = self.favorites_page.auto_sort.isChecked()
        self.recent_page.apply_to(config)
        self.smart_page.apply_to(config, self.points_page.values())


class PropertiesDialog(QDialog):
    """El tamaño de una carpeta grande puede tardar: se calcula aparte y se cancela al cerrar."""

    def __init__(self, entries: list[Entry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Propiedades"))
        self.resize(460, 240)
        form = QFormLayout()
        if len(entries) == 1:
            entry = entries[0]
            form.addRow(tr("Nombre:"), self._value(entry.name))
            form.addRow(tr("Ubicación:"), self._value(os.path.dirname(entry.path)))
            form.addRow(tr("Tipo:"), self._value(entry.type_label))
            form.addRow(tr("Modificado:"), self._value(format_time(entry.mtime)))
            form.addRow(tr("Atributos:"), self._value(entry.attrs))
            if entry.is_link:
                try:
                    form.addRow(tr("Destino del enlace:"), self._value(os.path.realpath(entry.path)))
                except OSError:
                    pass
        else:
            form.addRow(tr("Elementos:"), self._value(str(len(entries))))
        self.size_label = self._value(tr("Calculando…"))
        form.addRow(tr("Tamaño:"), self.size_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self._closed = False
        paths = [e.path for e in entries]

        def measure(sink):
            sizes = [fsops.tree_size(path, sink) for path in paths]
            return sum(s[0] for s in sizes), sum(s[1] for s in sizes)

        self._measure = run_bg(measure, self._measured, lambda _exc: self._measure_failed())

    def _measured(self, value: tuple[int, int]) -> None:
        if not self._closed:
            size, count = value
            self.size_label.setText(tr("{value} ({size:,} bytes) en {count:,} archivos", value=format_size(size), size=size, count=count))

    def _measure_failed(self) -> None:
        if not self._closed:
            self.size_label.setText(tr("No se pudo calcular"))

    def done(self, result: int) -> None:
        self._closed = True
        self._measure.cancel()
        super().done(result)

    @staticmethod
    def _value(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label
