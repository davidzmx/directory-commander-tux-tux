"""Visor rápido: imágenes, texto, vídeo, audio y, para lo demás, información básica."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMenu, QPlainTextEdit, QSizePolicy, QStackedWidget, QStyledItemDelegate,
                               QTextBrowser, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import ai_metadata, archive, media_info
from ..core.exiftool import DOWNLOAD_URL, find_exiftool, language_for, read_metadata
from ..core.listing import Entry, format_size, format_time
from .background import run_bg
from .code_view import CodeHighlighter, JsonTree, MarkdownView, is_json, is_markdown, json_lexer, lexer_for, pretty_json
from .icons import themed
from .model import IMAGE_EXTENSIONS, image_reader
from ..core.i18n import current_language, tr

TEXT_LIMIT = 256 * 1024
JSON_LIMIT = 16 * 1024 * 1024  # para verlo ordenado o en árbol hay que leerlo entero
WRAP_ANYWHERE_FROM = 24  # un nombre sin espacios más largo no cabría en un visor estrecho
MEDIA_DELAY_MS = 150  # al recorrer la lista con el teclado no se abre cada archivo por el que se pasa
METADATA_DELAY_MS = 120


def shrink_freely(label: QLabel) -> None:
    """Sin esto, la palabra más larga del texto fija el ancho mínimo del visor y no se puede encoger."""
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    label.setSizePolicy(policy)


class PreviewPane(QWidget):
    editRequested = Signal(str)  # abrir el Markdown que se ve en su editor
    jsonRequested = Signal(str)  # abrir el JSON que se ve en su ventana

    def __init__(self, config=None, parent=None) -> None:
        super().__init__(parent)
        self.config = {} if config is None else config
        self._generation = 0
        self._pixmap: QPixmap | None = None
        self.media = None
        self._media_path: str | None = None
        self._media_timer = QTimer(self, singleShot=True, interval=MEDIA_DELAY_MS)
        self._media_timer.timeout.connect(self._start_media)
        self.autoplay_allowed = lambda: True  # con una ventana de vídeo abierta no deben sonar los dos a la vez
        self.customize_controls = lambda: None
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setContentsMargins(6, 4, 6, 4)
        shrink_freely(self.title)
        self.image = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(80, 80)
        # Sin esto, el tamaño de la imagen impide que el visor pueda encogerse.
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.highlighter = CodeHighlighter(self.text.document())
        self.markdown = MarkdownView()
        self.json_tree = JsonTree()
        self._source_text = ""  # el texto tal como está en el archivo: «Original» y «Código»
        self._json_data = None
        self._json_text = ""
        self._entry_path = ""
        # Formas de ver un Markdown o un JSON: se muestran solo con ellos.
        self.modes = QWidget()
        modes_row = QHBoxLayout(self.modes)
        modes_row.setContentsMargins(6, 0, 6, 2)
        modes_row.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QToolButton] = {}
        for key, label in (("formatted", tr("Formateado")), ("tree", tr("Árbol")), ("source", tr("Original"))):
            button = QToolButton(text=label, checkable=True, autoRaise=True)
            self.mode_group.addButton(button)
            self.mode_buttons[key] = button
            modes_row.addWidget(button)
            button.clicked.connect(lambda _checked=False, k=key: self._show_mode(k))
        modes_row.addStretch(1)
        self.edit_button = QToolButton(text=tr("Editar"), autoRaise=True)
        self.edit_button.setIcon(themed("rename"))
        self.edit_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.edit_button.setToolTip(tr("Abrir en el editor de Markdown, con el texto a la izquierda y cómo se ve a la derecha"))
        self.edit_button.clicked.connect(lambda: self.editRequested.emit(self._entry_path))
        modes_row.addWidget(self.edit_button)
        self.window_button = QToolButton(text=tr("Abrir en ventana"), autoRaise=True)
        self.window_button.setIcon(themed("show"))
        self.window_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.window_button.setToolTip(tr("Ver el JSON en su propia ventana, más grande, con búsqueda"))
        self.window_button.clicked.connect(lambda: self.jsonRequested.emit(self._entry_path))
        modes_row.addWidget(self.window_button)
        self.modes.hide()
        self.info = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.info.setWordWrap(True)
        shrink_freely(self.info)
        self.stack = QStackedWidget()
        for widget in (self.info, self.image, self.text, self.markdown, self.json_tree):
            self.stack.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.modes)
        layout.addWidget(self.stack, 1)
        self.show_entry(None)

    def show_entry(self, entry: Entry | None) -> None:
        if entry is not None and entry.path == self._media_path:
            return  # la misma selección tras refrescar la carpeta no debe reiniciar la reproducción
        self._generation += 1
        generation = self._generation
        self._pixmap = None
        self.modes.hide()
        self._stop_media()
        if entry is None:
            self.title.setText("")
            self.info.setText(tr("Selecciona un archivo para verlo aquí"))
            self.stack.setCurrentWidget(self.info)
            return
        self.title.setText(tr("<b>{value}</b><br>{value2} · {value3}", value=_escape(_wrappable(entry.display_name)), value2=format_size(entry.effective_size), value3=format_time(entry.mtime)))
        if entry.in_archive and not entry.is_dir:
            self._show_member(generation, entry)
            return
        if entry.is_dir or not os.path.isfile(entry.path) or archive.split_archive_path(entry.path):
            self.info.setText(entry.type_label)
            self.stack.setCurrentWidget(self.info)
            return
        if media_info.is_media_extension(entry.extension):
            self._media_path = entry.path
            self.info.setText(tr("Cargando…"))
            self.stack.setCurrentWidget(self.info)
            self._media_timer.start()
            return
        if entry.extension in IMAGE_EXTENSIONS:
            self.info.setText(tr("Cargando…"))
            self.stack.setCurrentWidget(self.info)
            run_bg(lambda _s: _read_image(entry.path), lambda img: self._show_image(generation, img, entry))
            return
        limit = JSON_LIMIT if is_json(entry.name) else TEXT_LIMIT
        run_bg(lambda _s: _read_text(entry.path, limit), lambda txt: self._show_text(generation, txt, entry),
               lambda exc: self._show_info(generation, str(exc)))

    def _show_member(self, generation: int, entry: Entry) -> None:
        """Un archivo de dentro de un comprimido se lee a la memoria, sin dejar nada en el disco. Un vídeo no: no suele caber."""
        if media_info.is_media_extension(entry.extension):
            self._show_info(generation, tr("{type_label}\nPara reproducirlo, cópialo fuera del comprimido", type_label=entry.type_label))
            return
        self.info.setText(tr("Cargando…"))
        self.stack.setCurrentWidget(self.info)
        failed = lambda exc: self._show_info(generation, str(exc))  # noqa: E731
        if entry.extension in IMAGE_EXTENSIONS:
            run_bg(lambda _s: _read_image(entry.path, True), lambda image: self._show_image(generation, image, entry), failed)
        else:
            run_bg(lambda _s: _decode_text(archive.read_member_start(entry.path, TEXT_LIMIT)), lambda text: self._show_text(generation, text, entry), failed)

    # ---------------------------------------------------------------- vídeo y audio
    def _start_media(self) -> None:
        if self.media is None:
            try:
                from .media_player import MediaPlayer
            except ImportError as exc:  # en Linux puede faltar una biblioteca del sistema
                self._show_info(self._generation, tr("No se puede reproducir: Qt Multimedia no está disponible\n{exc}", exc=exc))
                return
            self.media = MediaPlayer(self.config)
            self.media.failed.connect(self._media_failed)
            self.media.customizeRequested.connect(lambda: self.customize_controls())
            self.stack.addWidget(self.media)
        self.media.load(self._media_path, None if self.autoplay_allowed() else False)
        self.stack.setCurrentWidget(self.media)

    def _media_failed(self, message: str) -> None:
        self.media.clear()
        self._show_info(self._generation, tr("No se pudo reproducir este archivo\n{message}\n\nCon doble clic se abre en la aplicación predeterminada.", message=message))

    def _stop_media(self) -> None:
        self._media_timer.stop()
        self._media_path = None
        if self.media is not None:
            self.media.clear()

    def release_media(self) -> None:
        """Hay que llamarla antes de modificar archivos: en Windows el reproductor los mantiene bloqueados."""
        if self._media_timer.isActive():
            self._media_timer.stop()
            self._media_path = None  # aún no se abrió: volverá a abrirse al seleccionarlo de nuevo
            self._show_info(self._generation, "")
        elif self.media is not None:
            self.media.release()

    def pause_media(self) -> None:
        if self.media is not None and self.media.has_source:
            self.media.player.pause()

    def apply_bar_buttons(self) -> None:
        if self.media is not None:
            self.media.apply_bar_buttons()

    def apply_icons(self) -> None:
        if self.media is not None:
            self.media.apply_icons()

    def dispose(self) -> None:
        self._stop_media()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if not event.spontaneous():  # minimizar la ventana no detiene la reproducción
            self._stop_media()

    # ---------------------------------------------------------------- imágenes y texto
    def _show_image(self, generation: int, image, entry: Entry) -> None:
        if generation != self._generation:
            return
        if image.isNull():
            self._show_info(generation, tr("No se pudo leer la imagen"))
            return
        self._pixmap = QPixmap.fromImage(image)
        self.title.setText(self.title.text() + f" · {image.width()}×{image.height()}")
        self.stack.setCurrentWidget(self.image)  # antes de escalar: una página oculta no tiene tamaño real
        self._fit_image()

    def _show_text(self, generation: int, text: str | None, entry: Entry) -> None:
        if generation != self._generation:
            return
        if text is None:
            self._show_info(generation, tr("{type_label}\nSin vista previa para este tipo de archivo", type_label=entry.type_label))
            return
        self._source_text, self._entry_path = text, entry.path
        self._json_data, self._json_text = None, ""
        if is_json(entry.name):
            try:
                self._json_data, self._json_text = pretty_json(text)
            except ValueError as exc:
                self._json_text = ""
                self.title.setText(self.title.text() + "<br>" + _escape(tr("No es un JSON válido: {error}", error=exc)))
        markdown, json_ok = is_markdown(entry.name), bool(self._json_text)
        self.mode_buttons["formatted"].setVisible(markdown or json_ok)
        self.mode_buttons["tree"].setVisible(json_ok)
        self.mode_buttons["source"].setText(tr("Código") if markdown else tr("Original"))
        self.mode_buttons["source"].setVisible(markdown or json_ok)
        self.edit_button.setVisible(markdown and not entry.in_archive and os.path.isfile(entry.path))
        self.window_button.setVisible(json_ok and not entry.in_archive and os.path.isfile(entry.path))
        self.modes.setVisible(markdown or json_ok)
        self._show_mode("formatted" if markdown or json_ok else "source")

    def _show_mode(self, key: str) -> None:
        """Formateado: el Markdown dibujado, o el JSON ordenado y con colores. Árbol: el JSON desplegable. Original o código: el archivo tal cual, con colores."""
        self.mode_buttons[key].setChecked(True)
        name = os.path.basename(self._entry_path)
        if key == "formatted" and is_markdown(name):
            self.stack.setCurrentWidget(self.markdown)  # antes de dibujarlo: las imágenes se ajustan a su ancho
            self.markdown.path = self._entry_path
            self.markdown.show_markdown(self._source_text, os.path.dirname(self._entry_path))
            return
        if key == "tree" and self._json_data is not None:
            self.json_tree.show_data(self._json_data)
            self.stack.setCurrentWidget(self.json_tree)
            return
        formatted_json = key == "formatted" and bool(self._json_text)
        text = self._json_text if formatted_json else self._source_text
        self.highlighter.set_lexer(None)  # sin colores viejos mientras se divide el nuevo
        self.text.setPlainText(text)
        self.highlighter.set_lexer(json_lexer() if formatted_json else lexer_for(name, text))
        self.stack.setCurrentWidget(self.text)

    def _show_info(self, generation: int, message: str) -> None:
        if generation == self._generation:
            self.info.setText(message)
            self.stack.setCurrentWidget(self.info)

    def _fit_image(self) -> None:
        if self._pixmap is not None:
            self.image.setPixmap(self._pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_image()


class _WrappedValues(QStyledItemDelegate):
    """El valor ocupa las líneas que necesite al ancho de su columna: un prompt largo o una ruta se leen enteros, no recortados."""

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        text = index.data() or ""
        if index.column() != 1 or not text:
            return size
        width = max(40, self.parent().header().sectionSize(1) - 10)
        flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
        height = option.fontMetrics.boundingRect(QRect(0, 0, width, 1_000_000), flags, text).height()
        return QSize(size.width(), max(size.height(), height + 6))


class MetadataPanel(QWidget):
    """Datos del elemento seleccionado. De un archivo los lee ExifTool, en segundo plano. Sin ExifTool, o en
    carpetas y dentro de comprimidos, se muestra lo que da el sistema de archivos."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.exiftool_dir = ""
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("Dato"), tr("Valor")])
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setIndentation(12)
        self.tree.setItemDelegate(_WrappedValues(self.tree))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().sectionResized.connect(lambda *_sizes: self.tree.doItemsLayout())  # la altura de cada fila depende del ancho
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setOpenExternalLinks(True)
        self.note.setTextFormat(Qt.TextFormat.RichText)
        self.note.setContentsMargins(8, 4, 8, 4)
        self.note.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.note)
        self._entry: Entry | None = None
        self._timer = QTimer(self, singleShot=True, interval=METADATA_DELAY_MS)  # recorrer la lista con las flechas no lanza un proceso por fila
        self._timer.timeout.connect(self._start_reading)

    def groups(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Lo que se está mostrando."""
        result = []
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            result.append((top.text(0), [(top.child(row).text(0), top.child(row).text(1)) for row in range(top.childCount())]))
        return result

    def _context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is not None and item.parent() is not None:
            menu = QMenu(self)
            menu.addAction(themed("copy"), tr("Copiar el valor"), lambda: QGuiApplication.clipboard().setText(item.text(1)))
            menu.addAction(tr("Copiar el dato y el valor"), lambda: QGuiApplication.clipboard().setText(f"{item.text(0)}: {item.text(1)}"))
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def _show_groups(self, groups: list[tuple[str, list[tuple[str, str]]]]) -> None:
        self.tree.clear()
        for name, rows in groups:
            top = QTreeWidgetItem(self.tree, [name])
            font = QFont(top.font(0))
            font.setBold(True)
            top.setFont(0, font)
            top.setFirstColumnSpanned(True)
            for tag, value in rows:
                row = QTreeWidgetItem(top, [tag, value])
                row.setToolTip(1, value if len(value) <= 1500 else value[:1500] + "…")
                for column in (0, 1):  # con un valor de varias líneas, el dato va arriba, a su altura
                    row.setTextAlignment(column, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            top.setExpanded(True)
        self.tree.doItemsLayout()

    @staticmethod
    def _basic_groups(entry: Entry) -> list[tuple[str, list[tuple[str, str]]]]:
        rows = [(tr("Nombre"), entry.name), (tr("Ubicación"), os.path.dirname(entry.path)), (tr("Tipo"), entry.type_label)]
        if not entry.is_dir or entry.dir_size is not None:
            rows.append((tr("Tamaño"), format_size(entry.effective_size)))
        rows += [(tr("Modificación"), format_time(entry.mtime)), (tr("Atributos"), entry.attrs)]
        return [(tr("Archivo"), [(tag, value) for tag, value in rows if value])]

    @staticmethod
    def generation_groups(path: str, extension: str) -> list[tuple[str, list[tuple[str, str]]]]:
        """Lo que dejó el programa de IA con que se generó la imagen o el vídeo: el programa, el prompt, sus ajustes y si lleva el flujo."""
        if extension not in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS:
            return []
        found = ai_metadata.read_generation(path, extension)
        if found is None or not (found.has_prompt or found.workflow):
            return []
        rows = [(tr("Programa"), found.tool or tr("No se sabe")), (tr("Prompt"), found.prompt), (tr("Prompt negativo"), found.negative), *found.settings]
        if found.workflow:
            rows.append((tr("Workflow"), tr("Sí, con {count} nodos", count=found.nodes) if found.nodes else tr("Sí")))
        return [(tr("Generación con IA"), [(tag, value) for tag, value in rows if value])]

    def show_entry(self, entry: Entry | None) -> None:
        self._timer.stop()
        self._entry = entry
        self.note.hide()
        if entry is None:
            self.tree.clear()
        elif entry.is_dir or not os.path.isfile(entry.path):
            self._show_groups(self._basic_groups(entry))  # ExifTool recorrería todos los archivos de una carpeta
        elif find_exiftool(self.exiftool_dir) is None:
            self._show_groups(self._basic_groups(entry))
            self._show_note(tr('No se encontró ExifTool, que es quien lee los metadatos. <a href="{url}">Descárgalo de su web</a> e indica su carpeta en Preferencias → General.', url=DOWNLOAD_URL))
            path, extension = entry.path, entry.extension
            run_bg(lambda _sink: self.generation_groups(path, extension),
                   lambda groups: self._entry is entry and groups and self._show_groups(groups + self._basic_groups(entry)))
        else:
            self._timer.start()

    def _show_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()

    def _start_reading(self) -> None:
        entry, tool = self._entry, find_exiftool(self.exiftool_dir)
        if entry is None or tool is None:
            return
        path, language = entry.path, language_for(current_language())

        def done(value) -> None:
            generated, groups = value
            if self._entry is entry:
                self._show_groups(generated + (groups or self._basic_groups(entry)))
                if not groups:
                    self._show_note(tr("ExifTool no encontró datos en este archivo."))

        def failed(exc) -> None:
            if self._entry is entry:
                self._show_groups(self._basic_groups(entry))
                self._show_note(_escape(str(exc)))

        extension = entry.extension
        run_bg(lambda _sink: (self.generation_groups(path, extension), read_metadata(path, tool, language)), done, failed)


def _wrappable(text: str) -> str:
    """Permite partir por cualquier punto un nombre largo, con espacios de ancho cero."""
    return "\u200b".join(text) if len(text) >= WRAP_ANYWHERE_FROM else text


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _read_image(path: str, in_archive: bool = False):
    reader = image_reader(path, in_archive)
    reader.setAutoTransform(True)
    return reader.read()


def _read_text(path: str, limit: int = TEXT_LIMIT) -> str | None:
    """Texto inicial del archivo, o None si parece binario."""
    with open(path, "rb") as fin:
        return _decode_text(fin.read(limit))


def _decode_text(raw: bytes) -> str | None:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    if b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", "replace")
