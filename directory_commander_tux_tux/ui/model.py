"""Modelo de la lista de archivos: carga asíncrona, carpetas desplegables y miniaturas."""
from __future__ import annotations

import os
import shutil
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QBuffer, QFileInfo, QIODevice, QMimeData, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from ..core import ai_metadata, archive, fsops, git_tools, media_info, remote
from ..core.drives import DRIVE_GROUPS
from ..core.listing import (COMPUTER_PATH, Entry, format_size, format_time, has_visible_entries, is_results, list_dir, matches_content_type, matches_filter,
                            sort_entries)
from . import theme
from .background import run_bg
from .drag_drop import path_url
from ..core.i18n import tr

EntryRole = Qt.ItemDataRole.UserRole + 1
GenerationRole = Qt.ItemDataRole.UserRole + 2  # (prompt, workflow, programa) de una imagen o un vídeo generado con IA, o None
GitRole = Qt.ItemDataRole.UserRole + 3  # (estado de git, si es el de lo que contiene una carpeta), o None
COLUMNS = (("name", tr("Nombre")), ("size", tr("Tamaño")), ("type", tr("Tipo")), ("modified", tr("Modificación")), ("attrs", tr("Atrib")),
           ("free", tr("Espacio Libre")), ("used", tr("Porcentaje Usado")), ("filesystem", tr("Sistema de Archivos")), ("location", tr("Ubicación")))
IMAGE_EXTENSIONS = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
THUMB_SIZE = 128
MAX_THUMB_FILE = 200 * 1024 * 1024
MAX_REMEMBERED_MARKS = 100_000
FOLDER_PREVIEW_IMAGES = 4
MAX_FOLDER_SCAN = 2000  # en una carpeta enorme no se recorre todo para encontrar sus imágenes
MAX_CACHED_PREVIEWS = 2000

_icons = QFileIconProvider()
_ext_cache: dict[str, QIcon] = {}
_thumb_pool = QThreadPool()
_thumb_pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) // 2))
_video_pool = QThreadPool()  # aparte: cada vídeo tarda un segundo, y las imágenes no deben esperar a que terminen
_video_pool.setMaxThreadCount(2)
# Se conservan al volver a leer la carpeta y al cambiar de pestaña: los de los vídeos cuestan, y los de las carpetas leen varias imágenes.
_video_thumbs: OrderedDict = OrderedDict()
_folder_thumbs: OrderedDict = OrderedDict()
_pending_previews: set = set()


def _remember(cache: OrderedDict, key, value) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > MAX_CACHED_PREVIEWS:
        cache.popitem(last=False)


def generation_text(marks) -> str:
    """«ComfyUI · prompt y workflow»: lo que dicen las marcas, para la descripción emergente."""
    if not marks:
        return ""
    has_prompt, workflow, tool, model = (tuple(marks) + ("",))[:4]
    if not has_prompt and not workflow:
        text = tr("Generada con IA ({tool})", tool=tool) if tool else tr("Generada con IA")
        return text + ("\n" + tr("Modelo: {model}", model=model) if model else "")
    carries = tr("prompt y workflow") if has_prompt and workflow else tr("prompt") if has_prompt else tr("workflow")
    text = tr("Generada con IA ({tool}): lleva {carries}", tool=tool, carries=carries) if tool else tr("Generada con IA: lleva {carries}", carries=carries)
    return text + ("\n" + tr("Modelo: {model}", model=model) if model else "")


def decorate_video(image: QImage, duration: float, frames: int) -> QImage:
    """Las líneas entre los cuatro fotogramas y la duración en la esquina de abajo, sobre un fondo oscuro."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    if frames == 4:
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.drawLine(image.width() // 2, 0, image.width() // 2, image.height())
        painter.drawLine(0, image.height() // 2, image.width(), image.height() // 2)
    if duration > 0:
        font = QFont()
        font.setPixelSize(max(9, min(13, image.height() // 7)))
        font.setBold(True)
        metrics = QFontMetrics(font)
        text = media_info.format_duration(duration)
        badge = QRectF(2, image.height() - metrics.height() - 3, metrics.horizontalAdvance(text) + 8, metrics.height() + 1)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return image


def folder_with_images(folder: QIcon, images: list[QImage], side: int) -> QIcon:
    """El icono de la carpeta con sus primeras imágenes dentro, de una a cuatro, en una cuadrícula sobre el cuerpo de la carpeta."""
    canvas = QPixmap(side, side)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    folder.paint(painter, QRect(0, 0, side, side))
    body = QRectF(side * 0.13, side * 0.33, side * 0.74, side * 0.5)
    columns = 1 if len(images) == 1 else 2
    rows = 1 if len(images) <= 2 else 2
    gap = max(2.0, side / 40)
    cell_width, cell_height = (body.width() - gap * (columns - 1)) / columns, (body.height() - gap * (rows - 1)) / rows
    for number, image in enumerate(images[:FOLDER_PREVIEW_IMAGES]):
        cell = QRectF(body.left() + (number % columns) * (cell_width + gap), body.top() + (number // columns) * (cell_height + gap), cell_width, cell_height)
        size = image.size().scaled(int(cell.width()) - 2, int(cell.height()) - 2, Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(cell.center())
        painter.fillRect(target.adjusted(-1, -1, 1, 1), QColor(255, 255, 255, 230))
        painter.drawImage(target, image)
    painter.end()
    return QIcon(canvas)


def image_reader(path: str, in_archive: bool = False) -> QImageReader:
    """El lector de una imagen, del disco, de dentro de un comprimido o de un servidor. Si es demasiado grande, no da imagen."""
    if not in_archive and not remote.is_remote(path):
        return QImageReader(path)
    try:
        data = remote.read_bytes(path, MAX_THUMB_FILE) if remote.is_remote(path) else archive.read_member(path, MAX_THUMB_FILE)
    except OSError:
        data = None
    if data is None:
        return QImageReader()
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.source = buffer  # el lector no se queda con él, y lo necesita mientras lee
    return reader


def folder_icon(path: str) -> QIcon:
    """El icono propio de una carpeta que existe: Descargas, Documentos, una unidad, Este equipo…"""
    if path == COMPUTER_PATH:
        return _icons.icon(QFileIconProvider.IconType.Computer)
    return _icons.icon(QFileInfo(path))


def icon_for(entry: Entry) -> QIcon:
    if entry.is_drive:
        return _icons.icon(QFileInfo(entry.path))
    if entry.is_dir:
        return _icons.icon(QFileIconProvider.IconType.Folder)
    ext = entry.extension
    if ext:
        cached = _ext_cache.get(ext)
        if cached is not None:
            return cached
    if os.path.exists(entry.path):
        icon = _icons.icon(QFileInfo(entry.path))
    else:  # miembro de un comprimido
        icon = _icons.icon(QFileIconProvider.IconType.File)
    if ext and ext not in ("exe", "lnk", "ico", "appimage", "desktop"):  # estos tienen icono propio
        _ext_cache[ext] = icon
    return icon


class FileModel(QAbstractTableModel):
    loadFinished = Signal(str)  # mensaje de error, vacío si fue bien
    aboutToRename = Signal()
    renameFailed = Signal(str)
    renamed = Signal(str, str)
    sizesChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.path = ""
        self._all: list[Entry] = []
        self._rows: list[Entry] = []
        self._entries: list[Entry] = []
        self._filter = ""
        self._content_types: tuple[str, ...] = ()
        self._sort_column = "name"
        self._sort_reverse = False
        self._generation = 0
        self._loading = None
        self.show_thumbnails = False
        self.can_expand = True
        self.show_hidden = False
        self.free_space: int | None = None
        self.device: int | None = None  # el disco de la carpeta: el espacio libre de otro disco se ve en su panel
        self.total_space: int | None = None
        self._thumbs: dict[str, QIcon] = {}
        self._thumb_pending: set[str] = set()
        self.thumb_size = THUMB_SIZE
        self.video_thumbnails = "four"  # four, one u off
        self.folder_previews = True
        self.generation_marks = False  # se muestran con su botón de la barra
        self.git_marks = True
        self._git_states: tuple = ({}, {}, ())  # archivos, carpetas con cambios dentro, carpetas ignoradas
        self.generation_kinds = "both"  # both, prompt o workflow
        self.reloading = False
        self.ffmpeg_dir = ""
        self._generated: dict[tuple, bool] = {}  # por (ruta, fecha de modificación): sigue valiendo al volver a leer la carpeta
        self._generated_pending: set[tuple] = set()
        self._dir_sizes: dict[str, int] = {}
        self._expanded: set[str] = set()
        self._children: dict[str, list[Entry]] = {}
        self._childless: set[str] = set()  # carpetas que se sabe que no tienen nada que desplegar
        self._probing = None
        self._in_archive = False
        self.expandable = True
        self.unfold_archives = True
        self.remote_previews = False  # miniaturas de un servidor: descargan cada imagen
        self.check_mode = False
        self.checked: set[str] = set()
        self.friendly_dates = "off"

    def set_friendly_dates(self, mode: str) -> None:
        self.friendly_dates = mode
        if self._rows:
            self.dataChanged.emit(self.index(0, 3), self.index(len(self._rows) - 1, 3))

    def set_check_mode(self, enabled: bool) -> None:
        self.layoutAboutToBeChanged.emit()
        self.check_mode = enabled
        if not enabled:
            self.checked.clear()
        self.layoutChanged.emit()

    def set_checked(self, paths, checked: bool = True) -> None:
        self.checked.update(paths) if checked else self.checked.difference_update(paths)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0),
                                  [Qt.ItemDataRole.CheckStateRole])

    def ignore_pending(self) -> None:
        self._generation += 1
        if self._loading:
            self._loading.cancel()
            self._loading = None
        if self._probing:
            self._probing.cancel()
            self._probing = None

    # --- carga ---
    def load(self, path: str, show_hidden: bool = False, flat: bool = False, resort: bool = True, renamed: dict[str, str] | None = None) -> None:
        """`resort` en falso, al volver a leer la misma carpeta porque algo cambió en ella: lo que ya estaba no se mueve, y lo nuevo va al
        final. Así una descarga, una copia o un archivo que se guarda no reordenan la lista, con ella ordenada por fecha, mientras se mira."""
        self._generation += 1
        previous = {entry.path: position for position, entry in enumerate(self._all)} if not resort and path == self.path and not flat else None
        if previous is not None and renamed:
            previous.update({os.path.normpath(new): previous[os.path.normpath(old)] for old, new in renamed.items() if os.path.normpath(old) in previous})
        generation = self._generation
        if self._loading:
            self._loading.cancel()
        if self._probing:
            self._probing.cancel()
            self._probing = None
        column, reverse = self._sort_column, self._sort_reverse
        self.show_hidden = show_hidden
        self.can_expand = self.expandable and not flat and not is_results(path)
        if path != self.path:
            self.checked.clear()
        if path != self.path or flat:
            self._dir_sizes.clear()
            self._expanded.clear()
            self._children.clear()
            self._childless.clear()
        expanded = set(self._expanded)

        def work(sink):
            entries = list_dir(path, show_hidden, flat, lambda: sink.is_cancelled)
            entries = sort_entries(entries, column, reverse)
            children = {}
            for folder in expanded:
                try:
                    children[folder] = sort_entries(list_dir(folder, show_hidden), column, reverse)
                except OSError:
                    continue  # la carpeta ya no existe: deja de estar desplegada
            try:
                local = not (remote.is_remote(path) or archive.split_archive_path(path))
                disk = (shutil.disk_usage(path), os.stat(path).st_dev) if local else None
            except OSError:
                disk = None
            return entries, children, disk

        self.path = path
        self._in_archive = path == COMPUTER_PATH or archive.split_archive_path(path) is not None
        self._loading = run_bg(
            work,
            on_done=lambda value: self._loaded(generation, value, previous),
            on_error=lambda exc: self._failed(generation, exc),
        )

    def _loaded(self, generation: int, value, previous: dict[str, int] | None = None) -> None:
        if generation != self._generation:
            return
        entries, children, disk = value
        if previous:
            known = sorted((entry for entry in entries if entry.path in previous), key=lambda entry: previous[entry.path])
            entries = known + [entry for entry in entries if entry.path not in previous]  # los nuevos, entre sí, en su orden
        self.free_space, self.total_space = (disk[0].free, disk[0].total) if disk else (None, None)
        self.device = disk[1] if disk else None
        self._loading = None
        self._thumbs.clear()
        self._thumb_pending.clear()
        for group in [entries, *children.values()]:
            for entry in group:
                if entry.path in self._dir_sizes:
                    entry.dir_size = self._dir_sizes[entry.path]
        self.reloading = True  # la misma carpeta o una nueva, leída otra vez: lo que se veía debe seguir viéndose
        self.beginResetModel()
        self._all = entries
        self._children = children
        self._expanded &= set(children)
        self._rows = self._build_rows()
        self.endResetModel()
        self.reloading = False
        self.loadFinished.emit("")
        self._probe_folders([entry for group in [entries, *children.values()] for entry in group])

    def _probe_folders(self, entries: list[Entry]) -> None:
        """Mira en segundo plano qué carpetas están vacías, para quitarles la flecha. Mientras tanto la conservan: en una unidad de red puede tardar."""
        folders = [entry for entry in entries if entry.is_dir and not entry.is_drive]
        if not folders or not self.can_expand:
            return
        generation = self._generation
        show_hidden = self.show_hidden

        def work(sink):
            checked, empty = set(), set()
            for entry in folders:
                if sink.is_cancelled:
                    break
                checked.add(entry.path)
                if not has_visible_entries(entry, show_hidden):
                    empty.add(entry.path)
            return checked, empty

        self._probing = run_bg(work, on_done=lambda value: self._probed(generation, value))

    def _probed(self, generation: int, value) -> None:
        if generation != self._generation:
            return
        self._probing = None
        checked, empty = value
        self._childless = (self._childless - checked) | empty
        self._repaint_names()

    def _repaint_names(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0), [Qt.ItemDataRole.DecorationRole])

    def _failed(self, generation: int, exc: BaseException) -> None:
        if generation != self._generation:
            return
        self._loading = None
        self.beginResetModel()
        self._all, self._rows, self._entries = [], [], []
        self.endResetModel()
        self.free_space = self.total_space = self.device = None
        self.loadFinished.emit(str(exc) or exc.__class__.__name__)

    # --- filas visibles ---
    def _build_rows(self) -> list[Entry]:
        rows = self._build_grouped_rows() if self.path == COMPUTER_PATH else self._build_plain_rows(self._all)
        self._entries = [e for e in rows if not e.is_group]
        return rows

    def _build_grouped_rows(self) -> list[Entry]:
        """Las unidades, bajo un título por cada tipo. Un grupo sin unidades visibles no aparece."""
        rows: list[Entry] = []
        for key, (title, _kind) in DRIVE_GROUPS.items():
            members = self._build_plain_rows([e for e in self._all if e.drive_kind == key])
            count = sum(1 for e in members if e.is_drive)
            if count:
                rows.append(Entry(f"{title} ({count})", f"<{key}>", False, is_group=True))
                rows.extend(members)
        return rows

    def _build_plain_rows(self, top: list[Entry]) -> list[Entry]:
        rows: list[Entry] = []

        def add(entries: list[Entry], depth: int) -> None:
            for entry in entries:
                if not (matches_filter(entry.display_name, self._filter)
                        and matches_content_type(entry, self._content_types)):
                    continue
                if "ai" in self._content_types and not self.has_ai_tags(entry):
                    continue
                entry.depth = depth
                rows.append(entry)
                if entry.path in self._expanded:
                    add(self._children.get(entry.path, []), depth + 1)

        add(top, 0)
        return rows

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._rows = self._build_rows()
        self.endResetModel()

    def is_expanded(self, path: str) -> bool:
        return path in self._expanded

    def can_unfold(self, entry: Entry) -> bool:
        """Lleva flecha: una carpeta con algo dentro o un comprimido, que se despliega igual. De los comprimidos no se mira antes: habría que abrirlos todos."""
        if not self.can_expand or entry.is_group or entry.path in self._childless:
            return False
        return entry.is_dir or (self.unfold_archives and not (entry.in_archive or self._in_archive) and archive.has_archive_suffix(entry.name))

    def set_unfold_archives(self, enabled: bool) -> None:
        """Al desactivarlo se pliegan los que estuvieran desplegados, y lo de dentro deja de verse."""
        if enabled == self.unfold_archives:
            return
        self.unfold_archives = enabled
        if not enabled and not self._in_archive:
            self._expanded = {path for path in self._expanded if archive.split_archive_path(path) is None}
        self._rebuild()

    def set_expanded(self, entry: Entry, expanded: bool) -> None:
        """Despliega o pliega una carpeta o un comprimido dentro de la propia lista."""
        if not self.can_unfold(entry) or expanded == (entry.path in self._expanded):
            return
        if not expanded:
            prefix = entry.path.rstrip("/\\") + os.sep
            self._expanded = {p for p in self._expanded if p != entry.path and not p.startswith(prefix)}
            self._rebuild()
            return
        generation = self._generation
        path = entry.path
        show_hidden, column, reverse = self.show_hidden, self._sort_column, self._sort_reverse
        run_bg(lambda _s: sort_entries(list_dir(path, show_hidden), column, reverse),
               on_done=lambda children: self._children_loaded(generation, path, children),
               on_error=lambda exc: self.renameFailed.emit(str(exc)))

    def _children_loaded(self, generation: int, path: str, children: list[Entry]) -> None:
        if generation != self._generation:
            return
        if not children:  # se ha quedado vacía desde que se miró, o es un comprimido sin nada
            self._childless.add(path)
            self._repaint_names()
            return
        self._children[path] = children
        self._expanded.add(path)
        self._rebuild()
        self._probe_folders(children)

    def set_filter(self, pattern: str) -> None:
        if pattern != self._filter:
            self._filter = pattern
            self._rebuild()

    def set_content_types(self, content_types) -> None:
        wanted = tuple(content_types)
        if wanted != self._content_types:
            self._content_types = wanted
            if "ai" in wanted:
                for entry in self._all:
                    if not entry.is_dir and entry.extension in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS:
                        self._generation_mark(entry)
            self._rebuild()

    @property
    def content_types(self) -> tuple[str, ...]:
        return self._content_types

    @property
    def filter_text(self) -> str:
        return self._filter

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        self._sort_column = COLUMNS[column][0]
        self._sort_reverse = order == Qt.SortOrder.DescendingOrder
        self.layoutAboutToBeChanged.emit()
        self._all = sort_entries(self._all, self._sort_column, self._sort_reverse)
        self._children = {p: sort_entries(c, self._sort_column, self._sort_reverse) for p, c in self._children.items()}
        self._rows = self._build_rows()
        self.layoutChanged.emit()

    # --- acceso ---
    def _row(self, row: int) -> Entry | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def entry(self, row: int) -> Entry | None:
        """Elemento de esa fila. Los títulos de grupo no son elementos: dan None."""
        item = self._row(row)
        return None if item is None or item.is_group else item

    @property
    def entries(self) -> list[Entry]:
        return self._entries

    def entry_rows(self):
        """Pares (fila, elemento), sin los títulos de grupo: la fila no coincide con la posición en `entries`."""
        return ((row, item) for row, item in enumerate(self._rows) if not item.is_group)

    def group_rows(self) -> list[int]:
        return [row for row, item in enumerate(self._rows) if item.is_group]

    def row_of(self, path: str) -> int:
        for row, entry in enumerate(self._rows):
            if entry.path == path:
                return row
        return -1

    def apply_dir_sizes(self, sizes: dict[str, int]) -> None:
        self._dir_sizes.update(sizes)  # sobreviven a las recargas de esta misma carpeta
        for entry in self._rows:
            if entry.path in sizes:
                entry.dir_size = sizes[entry.path]
        if self._rows:
            self.dataChanged.emit(self.index(0, 1), self.index(len(self._rows) - 1, 1))
        self.sizesChanged.emit()

    # --- interfaz de QAbstractTableModel ---
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section][1]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        entry = self._row(index.row())
        if entry is None:
            return None
        column = COLUMNS[index.column()][0]
        if entry.is_group and role != EntryRole:
            return entry.name if role == Qt.ItemDataRole.DisplayRole and column == "name" else None
        if role == EntryRole:
            return entry
        if role == Qt.ItemDataRole.EditRole and column == "name":
            return entry.name
        if role == Qt.ItemDataRole.DisplayRole:
            if column == "name":
                return entry.display_name
            if column == "size":
                if entry.is_dir and entry.dir_size is None:
                    return ""
                return format_size(entry.effective_size)
            if column == "type":
                return entry.type_label
            if column == "modified":
                return format_time(entry.mtime, self.friendly_dates)
            if column == "free":
                return format_size(entry.free_space) if entry.free_space is not None else ""
            if column == "used":
                return ""  # lo dibuja el delegado como una barra
            if column == "filesystem":
                return entry.filesystem
            if column == "location":
                return os.path.dirname(entry.path)
            return entry.attrs
        if role == Qt.ItemDataRole.CheckStateRole and column == "name" and self.check_mode:
            return Qt.CheckState.Checked if entry.path in self.checked else Qt.CheckState.Unchecked
        if role == GenerationRole and column == "name":
            return self._shown_marks(self._generation_mark(entry))
        if role == GitRole and column == "name":
            return self.git_state(entry)
        if role == Qt.ItemDataRole.DecorationRole and column == "name":
            if self.show_thumbnails:
                thumb = self._thumbnail(entry)
                if thumb is not None:
                    return thumb
            return icon_for(entry)
        if role == Qt.ItemDataRole.ForegroundRole:
            if entry.hidden or (column == "name" and (self.git_state(entry) or ("",))[0] == "!"):  # lo que git ignora, más tenue
                return QColor(theme.current().dim)
            if entry.is_dir and column == "name":
                return QColor(theme.current().dir_text)
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole and column in ("size", "free"):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == "name":
                marks = self._generated.get((entry.path, entry.mtime)) if self.generation_marks else None
                return entry.path + ("\n" + generation_text(marks) if marks else "")
            if column == "used" and entry.used_fraction is not None:
                return tr("{value:.1f} % usado", value=entry.used_fraction * 100)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        item = self._row(index.row())
        if item is not None and item.is_group:
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.column() == 0 and not self._in_archive and self._rows[index.row()].in_archive is False:
            flags |= Qt.ItemFlag.ItemIsEditable
        if index.column() == 0 and self.check_mode:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        entry = self.entry(index.row())
        if role == Qt.ItemDataRole.CheckStateRole and entry is not None:
            state = value.value if hasattr(value, "value") else value
            self.set_checked([entry.path], state == Qt.CheckState.Checked.value)
            return True
        if role != Qt.ItemDataRole.EditRole or entry is None or value == entry.name:
            return False
        if remote.is_remote(entry.path):
            self._rename_on_server(entry, str(value))
            return True
        try:
            old = entry.path
            self.aboutToRename.emit()
            target = fsops.rename_one(entry.path, str(value))
        except (OSError, ValueError) as exc:
            self.renameFailed.emit(str(exc))
            return False
        entry.path = target
        entry.name = entry.display_name = os.path.basename(target)
        self.dataChanged.emit(index, index)
        self.renamed.emit(old, target)
        return True

    def _rename_on_server(self, entry: Entry, name: str) -> None:
        """En segundo plano: con el servidor colgado, la lista no debe quedarse esperando."""
        old, generation = entry.path, self._generation

        def renamed(target: str) -> None:
            row = self.row_of(old)
            if generation == self._generation and row >= 0:
                entry.path, entry.name = target, remote.name_of(target)
                entry.display_name = entry.name
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
            self.renamed.emit(old, target)

        run_bg(lambda _sink: fsops.rename_one(old, name), on_done=renamed, on_error=lambda exc: self.renameFailed.emit(str(exc)))

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setUrls([path_url(self._rows[r].path) for r in rows])
        return mime

    # --- miniaturas ---
    def _thumbnail(self, entry: Entry) -> QIcon | None:
        if entry.extension in IMAGE_EXTENSIONS and not entry.is_dir:
            thumb = self._thumbs.get(entry.path)
            if thumb is None:
                self._request_thumb(entry)
            return thumb
        local = not (entry.in_archive or self._in_archive or remote.is_remote(entry.path))
        if entry.is_dir and self.folder_previews and local and not entry.is_drive:
            return self._folder_preview(entry)
        if entry.extension in media_info.VIDEO_EXTENSIONS and self.video_thumbnails != "off" and local:
            return self._video_preview(entry)
        return None

    def _repaint_path(self, path: str) -> None:
        row = self.row_of(path)
        if row >= 0:
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def _video_preview(self, entry: Entry) -> QIcon | None:
        """Cuatro fotogramas repartidos a lo largo del vídeo, o uno, con su duración. Los saca FFmpeg: sin él, el icono."""
        path, side, ffmpeg_dir = entry.path, self.thumb_size, self.ffmpeg_dir
        frames = 4 if self.video_thumbnails == "four" else 1
        key = ("video", path, entry.mtime, entry.size, side, frames, ffmpeg_dir)
        if key in _video_thumbs:
            _video_thumbs.move_to_end(key)
            return _video_thumbs[key] or None
        if key in _pending_previews or entry.size <= 0:
            return None
        _pending_previews.add(key)

        def work(_sink):
            info = media_info.read_media_info(path, ffmpeg_dir)
            duration = info.duration if info is not None else 0.0
            data = media_info.video_thumbnail(path, ffmpeg_dir, duration, side, frames)
            return (QImage.fromData(data) if data else QImage()), duration

        def done(value) -> None:
            image, duration = value
            _pending_previews.discard(key)
            _remember(_video_thumbs, key, QIcon(QPixmap.fromImage(decorate_video(image, duration, frames))) if not image.isNull() else False)
            self._repaint_path(path)

        def failed(_exc) -> None:
            _pending_previews.discard(key)
            _remember(_video_thumbs, key, False)

        run_bg(work, on_done=done, on_error=failed, pool=_video_pool)
        return None

    def _folder_preview(self, entry: Entry) -> QIcon | None:
        """La carpeta con sus primeras imágenes dentro. Sin imágenes, el icono de siempre."""
        path, side = entry.path, self.thumb_size
        key = ("folder", path, entry.mtime, side)
        if key in _folder_thumbs:
            _folder_thumbs.move_to_end(key)
            return _folder_thumbs[key] or None
        if key in _pending_previews:
            return None
        _pending_previews.add(key)
        cell = max(16, side // 2)

        def work(_sink):
            names = []
            with os.scandir(path) as found:
                for number, item in enumerate(found):
                    if number >= MAX_FOLDER_SCAN:
                        break
                    extension = os.path.splitext(item.name)[1][1:].lower()
                    if extension in IMAGE_EXTENSIONS and not item.name.startswith(".") and item.is_file() and item.stat().st_size <= MAX_THUMB_FILE:
                        names.append(item.name)
            images = []
            for name in sorted(names, key=str.lower):
                reader = QImageReader(os.path.join(path, name))
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    reader.setScaledSize(size.scaled(QSize(cell, cell), Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    images.append(image)
                    if len(images) == FOLDER_PREVIEW_IMAGES:
                        break
            return images

        def done(images) -> None:
            _pending_previews.discard(key)
            _remember(_folder_thumbs, key, folder_with_images(icon_for(entry), images, side) if images else False)
            if images:
                self._repaint_path(path)

        def failed(_exc) -> None:
            _pending_previews.discard(key)
            _remember(_folder_thumbs, key, False)

        run_bg(work, on_done=done, on_error=failed, pool=_thumb_pool)
        return None

    def set_previews(self, video_thumbnails: str, folder_previews: bool, ffmpeg_dir: str) -> None:
        if (video_thumbnails, folder_previews, ffmpeg_dir) == (self.video_thumbnails, self.folder_previews, self.ffmpeg_dir):
            return
        self.video_thumbnails, self.folder_previews, self.ffmpeg_dir = video_thumbnails, folder_previews, ffmpeg_dir
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0), [Qt.ItemDataRole.DecorationRole])

    def set_generation_marks(self, enabled: bool, kinds: str = "both") -> None:
        if (enabled, kinds) != (self.generation_marks, self.generation_kinds):
            self.generation_marks, self.generation_kinds = enabled, kinds
            if self.rowCount():
                self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0), [GenerationRole])

    def _request_thumb(self, entry: Entry) -> None:
        path = entry.path
        in_archive = entry.in_archive
        on_server = remote.is_remote(path)
        if on_server and not self.remote_previews:
            return  # se descargarían todas las imágenes de la carpeta: solo si se ha pedido
        if path in self._thumb_pending or entry.size > MAX_THUMB_FILE or not (in_archive or on_server or os.path.isfile(path)):
            return
        self._thumb_pending.add(path)
        generation = self._generation

        side = self.thumb_size

        def work(_sink):
            reader = image_reader(path, in_archive)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(QSize(side, side), Qt.AspectRatioMode.KeepAspectRatio))
            return reader.read()

        run_bg(work, on_done=lambda image: self._thumb_ready(generation, path, image), pool=_thumb_pool)

    def set_thumb_size(self, size: int) -> None:
        """Las miniaturas ya hechas eran de otro tamaño: se vuelven a hacer al pedirlas."""
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self._thumbs.clear()
        self._thumb_pending.clear()
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0), [Qt.ItemDataRole.DecorationRole])

    def git_state(self, entry: Entry):
        if not self.git_marks or not any(self._git_states):
            return None
        return git_tools.state_of(entry.path, *self._git_states)

    def set_git_states(self, states) -> None:
        states = states or ({}, {}, ())
        if states == self._git_states:
            return  # al volver a leer la carpeta suele ser lo mismo: avisar haría recolocar la lista para nada
        self._git_states = states
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0), [GitRole, Qt.ItemDataRole.ForegroundRole])

    def set_git_marks(self, enabled: bool) -> None:
        if enabled != self.git_marks:
            self.git_marks = enabled
            if self.rowCount():
                self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0), [GitRole, Qt.ItemDataRole.ForegroundRole])

    def _shown_marks(self, marks):
        """Las marcas que se han elegido mostrar: las dos, solo la del prompt o solo la del flujo."""
        if not marks:
            return None
        has_prompt, workflow, tool = marks[:3]
        has_prompt = has_prompt and self.generation_kinds != "workflow"
        workflow = workflow and self.generation_kinds != "prompt"
        has_ai_badge = bool(tool) and self.generation_kinds == "both" and not marks[0] and not marks[1]
        return (has_prompt, workflow, tool) if (has_prompt or workflow or has_ai_badge) else None

    def has_ai_tags(self, entry: Entry) -> bool:
        """Indica si una entrada tiene marcas de IA (prompt, workflow o generada con IA). Las carpetas siempre pasan."""
        if entry.is_dir:
            return True
        if entry.in_archive or remote.is_remote(entry.path):
            return False
        if entry.extension not in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS:
            return False
        key = (entry.path, entry.mtime)
        if key in self._generated:
            marks = self._generated[key]
            return bool(marks and (marks[0] or marks[1] or (len(marks) > 2 and marks[2])))
        self._generation_mark(entry)
        return False

    def may_have_generation_marks(self, entry: Entry) -> bool:
        """Si se mostrarán sus marcas de IA cuando se lean: lo que puede tenerlas, del equipo y con las marcas activadas."""
        return (self.generation_marks and not entry.is_dir and not entry.in_archive and not remote.is_remote(entry.path)
                and entry.extension in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS)

    def _generation_mark(self, entry: Entry):
        """(prompt, workflow, programa), o None. Solo de los archivos del equipo: hay que leer sus metadatos, y se hace en segundo plano."""
        path = entry.path
        key = (path, entry.mtime)
        if key in self._generated:
            return self._generated[key] if (self.generation_marks or "ai" in self._content_types) else None
        extension = entry.extension
        if (not (self.generation_marks or "ai" in self._content_types) or entry.is_dir or entry.in_archive or remote.is_remote(path) or key in self._generated_pending
                or extension not in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS):
            return None
        self._generated_pending.add(key)
        if len(self._generated) > MAX_REMEMBERED_MARKS:
            self._generated.clear()

        def found(generation) -> None:
            self._generated_pending.discard(key)
            marks = generation.marks if generation is not None and (generation.has_prompt or generation.workflow or bool(generation.tool)) else None
            self._generated[key] = marks
            if "ai" in self._content_types:
                self._rebuild()
            else:
                row = self.row_of(path)
                if marks and row >= 0:
                    self.dataChanged.emit(self.index(row, 0), self.index(row, 0), [GenerationRole, Qt.ItemDataRole.ToolTipRole])

        run_bg(lambda _sink: ai_metadata.read_generation(path, extension), on_done=found, pool=_thumb_pool)
        return None

    def _thumb_ready(self, generation: int, path: str, image) -> None:
        if generation != self._generation or image.isNull():
            return
        self._thumbs[path] = QIcon(QPixmap.fromImage(image))
        row = self.row_of(path)
        if row >= 0:
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
