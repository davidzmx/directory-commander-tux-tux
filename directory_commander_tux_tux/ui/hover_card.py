"""Tarjeta con la información de un archivo o carpeta al dejar el ratón sobre él."""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QFormLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..core import ai_metadata, archive, fsops
from ..core.listing import Entry, format_size, format_time, list_dir, sort_entries
from ..core.media_info import (MediaInfo, codec_name, format_duration, is_media_extension, read_media_info,
                               video_frame)
from .background import run_bg
from .model import IMAGE_EXTENSIONS, _thumb_pool, icon_for, image_reader
from ..core.i18n import N_, tr

HOVER_PREVIEW_SIZES: dict[str, tuple[QSize, str]] = {
    "standard": (QSize(112, 150), N_("Estándar (112 × 150)")),
    "medium": (QSize(200, 250), N_("Mediano (200 × 250)")),
    "large": (QSize(320, 400), N_("Grande (320 × 400)")),
    "huge": (QSize(480, 600), N_("Muy grande (480 × 600)")),
    "original": (QSize(0, 0), N_("Tamaño real (100%)")),
}

THUMB_BOX = QSize(112, 150)
FOLDER_ICON_BOX = QSize(64, 64)
FOLDER_NAMES_SHOWN = 10
FOLDER_SIZE_SECONDS = 3.0
FOLDER_NAME_WIDTH = 330
AUTO_HIDE_MS = 12000
MAX_IMAGE_FILE = 200 * 1024 * 1024
LONG_VALUE = 40
_thumb_cache: OrderedDict[tuple[str, float], QImage] = OrderedDict()
_CACHE_SIZE = 64


_folder_size_pool = QThreadPool()
_folder_size_pool.setMaxThreadCount(1)


@dataclass
class FolderContents:
    folders: list[str]
    files: list[str]
    folder_count: int
    file_count: int


def read_folder_contents(path: str, show_hidden: bool, shown: int = FOLDER_NAMES_SHOWN) -> FolderContents:
    """Primeros nombres de cada tipo, en el orden de la lista, y cuántos hay en total."""
    entries = sort_entries(list_dir(path, show_hidden))
    folders = [e.name for e in entries if e.is_dir]
    files = [e.name for e in entries if not e.is_dir]
    return FolderContents(folders[:shown], files[:shown], len(folders), len(files))


def read_image_info(path: str, in_archive: bool = False) -> tuple[int, int, int]:
    """(ancho, alto, profundidad) leyendo solo la cabecera. Ceros si no se puede saber."""
    reader = image_reader(path, in_archive)
    size = reader.size()
    depth = 0
    pixel_format = reader.imageFormat()
    if pixel_format != QImage.Format.Format_Invalid:
        depth = QImage.toPixelFormat(pixel_format).bitsPerPixel()
    return (size.width(), size.height(), depth) if size.isValid() else (0, 0, 0)


def _image_thumb(path: str, in_archive: bool = False, target_box: QSize | None = None) -> QImage:
    reader = image_reader(path, in_archive)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and target_box is not None and target_box.width() > 0 and target_box.height() > 0:
        reader.setScaledSize(size.scaled(target_box, Qt.AspectRatioMode.KeepAspectRatio))
    elif size.isValid() and target_box is None:
        reader.setScaledSize(size.scaled(THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def card_details(entry: Entry, image: tuple[int, int, int] | None, media: MediaInfo | None) -> tuple[str, list[tuple[str, str]]]:
    """Subtítulo y filas de la tarjeta. Solo incluye los datos que se conocen."""
    extension = entry.extension.upper()
    size = "" if entry.is_dir and entry.dir_size is None else format_size(entry.effective_size)
    rows: list[tuple[str, str]] = []
    subtitle = entry.type_label
    if size:
        rows.append((tr("Tamaño"), size))
    if entry.is_drive:
        if entry.free_space is not None:
            rows.append((tr("Espacio libre"), format_size(entry.free_space)))
        if entry.used_fraction is not None:
            rows.append((tr("Usado"), f"{entry.used_fraction * 100:.1f} %"))
        if entry.filesystem:
            rows.append((tr("Sistema de archivos"), entry.filesystem))
    if image and image[0]:
        width, height, depth = image
        dimensions = f"{width} x {height} x {depth}" if depth else f"{width} x {height}"
        subtitle = tr("{dimensions} Imagen {extension}", dimensions=dimensions, extension=extension)
        rows.append((tr("Dimensiones"), dimensions))
    if media is not None:
        label = tr("Vídeo") if media.kind == "video" else tr("Audio")
        subtitle = f"{label} {extension}"
        if media.width:
            subtitle = f"{media.width} x {media.height} {subtitle}"
            rows.append((tr("Dimensiones"), f"{media.width} x {media.height}"))
        if media.duration:
            rows.append((tr("Duración"), format_duration(media.duration)))
        if media.codec:
            rows.append((tr("Códec de vídeo"), codec_name(media.codec)))
        if media.audio_codec:
            rows.append((tr("Códec de audio"), codec_name(media.audio_codec)))
        if media.sample_rate:
            channels = {0: "", 1: tr(", mono"), 2: tr(", estéreo")}.get(media.channels, tr(", {channels} canales", channels=media.channels))
            rows.append((tr("Audio"), tr("{value:g} kHz{channels}", value=media.sample_rate / 1000, channels=channels)))
        if media.bitrate:
            rows.append((tr("Tasa de bits"), tr("{value} kb/s", value=media.bitrate // 1000)))
        for label, value in ((tr("Título"), media.title), (tr("Artista"), media.artist), (tr("Álbum"), media.album)):
            if value:
                rows.append((label, value))
    modified = format_time(entry.mtime)
    if modified:
        rows.append((tr("Modificado"), modified))
    return subtitle, rows


def generation_rows(generated) -> list[tuple[str, str]]:
    """Con qué programa de IA se generó y con qué modelo, si lo dejó escrito."""
    if generated is None or not (generated.has_prompt or generated.workflow or bool(generated.tool)):
        return []
    rows = [(tr("Generada con IA"), generated.tool or tr("No se sabe con qué programa"))]
    return rows + ([(tr("Modelo"), generated.model)] if generated.model else [])


class HoverCard(QFrame):
    """Ventana flotante con miniatura y datos. Nada de esto bloquea la interfaz: los datos
    se leen en segundo plano y se descartan si el ratón ya se movió a otro archivo."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("hoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.ffmpeg_dir = ""
        self.show_hidden = False
        self.preview_size = "standard"
        self._size_call = None
        self._token = 0
        self._position = QPoint()
        self.current_path = ""
        self.thumb = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFixedSize(THUMB_BOX)
        self.title = QLabel()
        self.title.setObjectName("hoverTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 6, 0, 0)
        self.form.setVerticalSpacing(1)
        self.form.setHorizontalSpacing(10)
        self.sections = QVBoxLayout()
        self.sections.setContentsMargins(0, 0, 0, 0)
        self.sections.setSpacing(1)
        self.size_value: QLabel | None = None
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        text.addLayout(self.form)
        text.addLayout(self.sections)
        text.addStretch(1)
        self.row = QHBoxLayout(self)
        row = self.row
        row.setContentsMargins(8, 8, 10, 8)
        row.setSpacing(10)
        row.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        self.setMaximumWidth(460)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self.dismiss)

    def dismiss(self) -> None:
        self._token += 1
        if self._size_call is not None:
            self._size_call.cancel()
            self._size_call = None
        self.current_path = ""
        self._hide_timer.stop()
        self.hide()

    def show_for(self, entry: Entry, position: QPoint) -> None:
        if entry.path == self.current_path and self.isVisible():
            return
        self.dismiss()
        self.current_path = entry.path
        self._position = position
        token = self._token
        path, extension, ffmpeg_dir = entry.path, entry.extension, self.ffmpeg_dir
        readable = not entry.is_dir and archive.split_archive_path(path) is None and os.path.isfile(path)
        member = entry.in_archive and not entry.is_dir
        if entry.is_dir and not entry.is_drive and archive.split_archive_path(path) is None:
            show_hidden = self.show_hidden
            run_bg(lambda _s: read_folder_contents(path, show_hidden),
                   lambda contents: self._folder_ready(token, entry, contents),
                   lambda _exc: self._folder_ready(token, entry, None))
            return

        def work(_sink):
            if member and extension in IMAGE_EXTENSIONS:
                return read_image_info(path, True), None, None
            if not readable:
                return None, None, None
            generated = ai_metadata.read_generation(path, extension) if extension in ai_metadata.IMAGE_EXTENSIONS | ai_metadata.VIDEO_EXTENSIONS else None
            if extension in IMAGE_EXTENSIONS:
                return read_image_info(path), None, generated
            if is_media_extension(extension):
                return None, read_media_info(path, ffmpeg_dir), generated
            return None, None, generated

        run_bg(work, lambda value: self._details_ready(token, entry, *value),
               lambda _exc: self._details_ready(token, entry, None, None))

    def _details_ready(self, token: int, entry: Entry, image, media, generated=None) -> None:
        if token != self._token:
            return
        subtitle, rows = card_details(entry, image, media)
        self._reset_content(entry, subtitle, folder=False)
        for label, value in rows + generation_rows(generated):
            self._add_row(label, value)
        self._reveal()
        self._load_thumbnail(token, entry, image, media)

    def _preview_target_box(self) -> QSize:
        return HOVER_PREVIEW_SIZES.get(self.preview_size, HOVER_PREVIEW_SIZES["standard"])[0]

    def _reset_content(self, entry: Entry, subtitle: str, folder: bool) -> None:
        """Las carpetas llevan el icono a la derecha y sin subtítulo; los archivos, la miniatura a la izquierda."""
        self.title.setText(entry.name)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle.setText(subtitle)
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle.setVisible(bool(subtitle))
        self.size_value = None
        while self.form.rowCount():
            self.form.removeRow(0)
        while self.sections.count():
            widget = self.sections.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.row.setDirection(QHBoxLayout.Direction.RightToLeft if folder else QHBoxLayout.Direction.LeftToRight)
        box = self._preview_target_box()
        if folder or box.isEmpty():
            self.thumb.setFixedSize(FOLDER_ICON_BOX if folder else THUMB_BOX)
            self.setMaximumWidth(460)
        else:
            self.thumb.setFixedSize(box)
            self.setMaximumWidth(max(460, box.width() + 350))
        self.thumb.setPixmap(icon_for(entry).pixmap(64, 64))

    def _add_row(self, label: str, value: str) -> QLabel:
        name = QLabel(f"{label}:")
        name.setStyleSheet("font-weight: 600;")
        content = QLabel(value)
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setWordWrap(len(value) > LONG_VALUE)  # los valores cortos, en una sola línea
        self.form.addRow(name, content)
        return content

    def _reveal(self) -> None:
        self.adjustSize()
        self._place()
        self.show()
        self._hide_timer.start()

    # ---------------------------------------------------------------- carpetas
    def _folder_ready(self, token: int, entry: Entry, contents: FolderContents | None) -> None:
        if token != self._token:
            return
        self._reset_content(entry, "" if contents is not None else tr("No se puede leer esta carpeta"), folder=True)
        if contents is not None:
            known = entry.dir_size is not None
            self.size_value = self._add_row(tr("Tamaño"), format_size(entry.effective_size) if known else tr("calculando…"))
            self._add_names(tr("Carpetas"), contents.folders, contents.folder_count)
            self._add_names(tr("Archivos"), contents.files, contents.file_count)
            if not contents.folder_count and not contents.file_count:
                self._add_section_text(tr("Carpeta vacía"))
            if not known:
                self._measure_folder(token, entry.path)
        self._reveal()

    def _add_section_text(self, text: str, heading: bool = False) -> None:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if heading:
            label.setStyleSheet("font-weight: 600;")
        else:
            label.setContentsMargins(12, 0, 0, 0)
        self.sections.addWidget(label)

    def _add_names(self, heading: str, names: list[str], total: int) -> None:
        if not total:
            return
        self._add_section_text(f"{heading}:", heading=True)
        metrics = self.fontMetrics()
        lines = [metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, FOLDER_NAME_WIDTH) for name in names]
        if total > len(names):
            lines.append(tr("… y {value} más", value=total - len(names)))
        self._add_section_text("\n".join(lines))

    def _measure_folder(self, token: int, path: str) -> None:
        def done(result: tuple[int, bool]) -> None:
            if token == self._token and self.size_value is not None:
                size, complete = result
                self.size_value.setText(format_size(size) if complete else tr("más de {value}", value=format_size(size)))
                self.adjustSize()

        self._size_call = run_bg(lambda sink: fsops.tree_size_within(path, FOLDER_SIZE_SECONDS, sink), done, pool=_folder_size_pool)

    def _load_thumbnail(self, token: int, entry: Entry, image, media) -> None:
        path = entry.path
        key = (path, entry.mtime, self.preview_size)
        cached = _thumb_cache.get(key)
        if cached is not None:
            self._set_thumbnail(token, cached)
            return
        is_image = bool(image and image[0]) and entry.size <= MAX_IMAGE_FILE
        cover = media.cover if media is not None else None
        is_video = media is not None and media.kind == "video"
        if not (is_image or cover or is_video):
            return
        ffmpeg_dir, duration = self.ffmpeg_dir, media.duration if media is not None else 0.0
        in_archive = entry.in_archive
        target_box = self._preview_target_box()

        def work(_sink):
            if is_image:
                return _image_thumb(path, in_archive, target_box)
            raw = cover or video_frame(path, ffmpeg_dir, duration)
            return QImage.fromData(raw) if raw else QImage()

        run_bg(work, lambda result: self._thumb_loaded(token, key, result), pool=_thumb_pool)

    def _thumb_loaded(self, token: int, key, image: QImage) -> None:
        if image.isNull():
            return
        box = self._preview_target_box()
        screen = QGuiApplication.screenAt(self._position) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        max_w = int(avail.width() * 0.8) if avail else 1200
        max_h = int(avail.height() * 0.8) if avail else 900
        if box.width() > 0 and box.height() > 0:
            image = image.scaled(box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        elif self.preview_size == "original":
            if image.width() > max_w or image.height() > max_h:
                image = image.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            image = image.scaled(THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        _thumb_cache[key] = image
        while len(_thumb_cache) > _CACHE_SIZE:
            _thumb_cache.popitem(last=False)
        self._set_thumbnail(token, image)

    def _set_thumbnail(self, token: int, image: QImage) -> None:
        if token == self._token:
            self.thumb.setFixedSize(image.size())
            self.setMaximumWidth(max(460, image.width() + 350))
            self.thumb.setPixmap(QPixmap.fromImage(image))
            self.adjustSize()
            self._place()

    def _place(self) -> None:
        """Junto al cursor, sin salirse de la pantalla."""
        target = self._position + QPoint(14, 18)
        screen = QGuiApplication.screenAt(self._position) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if target.x() + self.width() > area.right():
                target.setX(self._position.x() - self.width() - 8)
            if target.y() + self.height() > area.bottom():
                target.setY(self._position.y() - self.height() - 8)
            target.setX(max(area.left(), target.x()))
            target.setY(max(area.top(), target.y()))
        self.move(target)
