"""Conversión, giro y redimensionado de imágenes con Qt, para ejecutar en segundo plano."""
from __future__ import annotations

import os

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QImageWriter, QPainter, QTransform

from ..core import fsops
from ..core.fsops import OpResult, ProgressSink
from ..core.i18n import tr

_ALIASES = {"jpeg": "jpg", "tiff": "tif"}
_NO_ALPHA = {"jpg", "bmp"}
JPEG_QUALITY = 95


def writable_formats() -> list[str]:
    found = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
    return sorted({_ALIASES.get(f, f) for f in found})


def normalize_format(name: str) -> str:
    name = name.strip().lower().lstrip(".")
    name = _ALIASES.get(name, name)
    if name not in writable_formats():
        raise ValueError(tr("No se puede escribir el formato {value}. Disponibles: {join}", value=name or tr("(vacío)"), join=', '.join(writable_formats())))
    return name


def _flatten(image: QImage) -> QImage:
    """JPEG y BMP no guardan transparencia: se compone sobre blanco en vez de dejar negro."""
    if not image.hasAlphaChannel():
        return image
    result = QImage(image.size(), QImage.Format.Format_RGB32)
    result.fill(QColor("white"))
    painter = QPainter(result)
    painter.drawImage(0, 0, image)
    painter.end()
    return result


def _scaled(image: QImage, width: int | None, height: int | None, keep_aspect: bool) -> QImage:
    if not width and not height:
        return image
    target = QSize(width or image.width(), height or image.height())
    mode = Qt.AspectRatioMode.KeepAspectRatio if keep_aspect else Qt.AspectRatioMode.IgnoreAspectRatio
    return image.scaled(target, mode, Qt.TransformationMode.SmoothTransformation)


def _target_path(path: str, dest_dir: str, fmt: str, replace: bool, suffix: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(path))
    if replace:
        return os.path.join(os.path.dirname(path), stem + (f".{fmt}" if fmt != ext[1:].lower() else ext))
    return fsops.unique_name(dest_dir, f"{stem}{suffix}.{fmt}")


def _read(path: str) -> QImage:
    # Lectores y escritores viven solo aquí: mientras existen, Windows no deja mover ni borrar el archivo.
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise OSError(reader.errorString() or tr("No se pudo leer la imagen"))
    return image


def _write(image: QImage, path: str, fmt: str) -> None:
    writer = QImageWriter(path, fmt.encode())
    if fmt == "jpg":
        writer.setQuality(JPEG_QUALITY)
    if not writer.write(image):
        message = writer.errorString() or tr("No se pudo escribir la imagen")
        del writer
        fsops._remove_quietly([path])
        raise OSError(message)


def crop_name(path: str) -> str:
    """Nombre libre para el recorte, junto al original y en su formato si se puede escribir."""
    stem, ext = os.path.splitext(os.path.basename(path))
    fmt = ext[1:].lower()
    if _ALIASES.get(fmt, fmt) not in writable_formats():
        ext = ".png"
    return fsops.unique_name(os.path.dirname(path), tr("{stem}_recorte{ext}", stem=stem, ext=ext))


def save_crop(path: str, area: QRect, rotation: int, target: str) -> None:
    """Guarda en `target` esa parte de la imagen, a su resolución completa. El giro es el que se ve en el visor."""
    image = _read(path)
    if rotation:
        image = image.transformed(QTransform().rotate(rotation), Qt.TransformationMode.SmoothTransformation)
    if not image.rect().contains(area):
        raise OSError(tr("La imagen ha cambiado en el disco"))
    fmt = normalize_format(os.path.splitext(target)[1])
    image = image.copy(area)
    if fmt in _NO_ALPHA:
        image = _flatten(image)
    temporary = target + fsops.PART_SUFFIX
    _write(image, temporary, fmt)
    os.replace(temporary, target)


def frame_name(path: str, position_ms: int) -> str:
    """Nombre libre para un fotograma, junto al vídeo y con el momento al que corresponde."""
    seconds, milliseconds = divmod(max(0, position_ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    moment = f"{hours}-{minutes:02}-{seconds:02}-{milliseconds:03}" if hours else f"{minutes:02}-{seconds:02}-{milliseconds:03}"
    stem = os.path.splitext(os.path.basename(path))[0]
    return fsops.unique_name(os.path.dirname(path), tr("{stem}_fotograma_{moment}.png", stem=stem, moment=moment))


def save_frame(image: QImage, target: str) -> None:
    temporary = target + fsops.PART_SUFFIX
    _write(image, temporary, "png")
    os.replace(temporary, target)


def process_images(paths: list[str], dest_dir: str, fmt: str | None = None, rotate: int = 0, width: int | None = None,
                   height: int | None = None, keep_aspect: bool = True, replace: bool = False, add_suffix: bool = False,
                   preserve_date: bool = False, sink: ProgressSink | None = None) -> OpResult:
    """Procesa cada imagen por separado: si una falla, las demás siguen y se informa del error.

    Sin `replace` nunca sobrescribe. Con `replace`, el original solo se sustituye cuando la
    imagen nueva ya está escrita por completo.
    """
    sink = sink or ProgressSink()
    result = OpResult()
    for index, path in enumerate(paths):
        if sink.is_cancelled:
            result.cancelled = True
            break
        sink.report(index, len(paths), os.path.basename(path))
        try:
            image = _read(path)
            out_format = fmt or normalize_format(os.path.splitext(path)[1])
            if rotate:
                image = image.transformed(QTransform().rotate(rotate), Qt.TransformationMode.SmoothTransformation)
            image = _scaled(image, width, height, keep_aspect)
            if out_format in _NO_ALPHA:
                image = _flatten(image)
            suffix = f"_{image.width()}x{image.height()}" if add_suffix else ""
            target = _target_path(path, dest_dir, out_format, replace, suffix)
            original_time = os.stat(path).st_mtime
            tmp = target + fsops.PART_SUFFIX
            _write(image, tmp, out_format)
            existed = os.path.lexists(target)
            os.replace(tmp, target)
            if replace and os.path.normcase(target) != os.path.normcase(path):
                os.unlink(path)  # cambió de extensión: la imagen convertida ya está a salvo
            if preserve_date:
                os.utime(target, (original_time, original_time))
            result.done += 1
            result.placed.append((path, target, not existed and not replace))
        except (OSError, ValueError) as exc:
            result.errors.append((path, str(exc)))
    return result
