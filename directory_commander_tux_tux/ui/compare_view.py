"""La ventana con el resultado de comparar dos archivos: idénticos, texto con sus diferencias o dos imágenes lado a lado."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QLocale, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QImageReader, QPainter, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from ..core import file_compare
from ..core.i18n import tr
from ..core.listing import format_size
from . import theme
from .model import IMAGE_EXTENSIONS

_ZOOM_STEP = 1.25
_MAX_ZOOM = 64.0  # veces lo que ocupa entera
_DETAIL_LINES = 3
_SAME_SHAPE = 0.01  # la misma proporción, salvo redondeos: es la misma foto a otro tamaño
_NONZERO = bytes([0]) + bytes([255]) * 255  # cualquier valor distinto de cero, a 255
_DIFF_COLORS = {"dark": {"+": ("#7ee787", "#12261a"), "-": ("#ff9492", "#2d1518"), "@": ("#8ab4f8", "#17233a")},
                "light": {"+": ("#116329", "#dafbe1"), "-": ("#a40e26", "#ffe2e0"), "@": ("#1a4fa3", "#e2ecfb")}}


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1][1:].lower() in IMAGE_EXTENSIONS


def _load(path: str) -> tuple[QImage, str]:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image_format = bytes(reader.format()).decode("ascii", "replace").upper()  # después de leer ya no lo dice
    return reader.read(), image_format


def _image_details(path: str, image: QImage, image_format: str) -> str:
    """Los datos que se muestran encima de cada imagen."""
    pixels = image.width() * image.height()
    stat = os.stat(path)
    return "\n".join((f"{image.width()} × {image.height()} ({QLocale().toString(pixels / 1_000_000, 'f', 1)} MP)",
                      " · ".join(item for item in (format_size(stat.st_size), image_format) if item),
                      tr("Modificada: {date}", date=f"{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}")))


@dataclass
class ImageComparison:
    first: QImage
    second: QImage
    marked: QImage | None  # la primera, apagada, con lo distinto en rojo. None si no se pueden comparar píxel a píxel
    different: int  # píxeles
    total: int
    area: tuple[int, int, int, int] | None  # x, y, ancho y alto de lo distinto
    first_details: str = ""
    second_details: str = ""

    @property
    def same_size(self) -> bool:
        return self.first.size() == self.second.size()


def _different_pixels(first: QImage, second: QImage, is_cancelled=None) -> tuple[bytearray, int, tuple[int, int, int, int] | None]:
    """Una máscara con 255 en cada píxel distinto. Se trabaja con enteros de una fila entera: Python tardaría demasiado píxel a píxel."""
    width, height = first.width(), first.height()
    one, other = bytes(first.constBits()), bytes(second.constBits())
    stride_one, stride_other, row_bytes = first.bytesPerLine(), second.bytesPerLine(), width * 4
    mask = bytearray(width * height)
    different = 0
    left, right, top, bottom = width, -1, -1, -1
    for row in range(height):
        if is_cancelled is not None and is_cancelled():
            raise InterruptedError
        start_one, start_other = row * stride_one, row * stride_other
        a, b = one[start_one:start_one + row_bytes], other[start_other:start_other + row_bytes]
        if a == b:
            continue
        xored = (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(row_bytes, "little")
        joined = 0
        for channel in range(4):
            joined |= int.from_bytes(xored[channel::4], "little")
        marks = joined.to_bytes(width, "little").translate(_NONZERO)
        mask[row * width:(row + 1) * width] = marks
        different += width - marks.count(0)
        left = min(left, marks.find(255))
        right = max(right, marks.rfind(255))
        top = top if top >= 0 else row
        bottom = row
    return mask, different, (left, top, right - left + 1, bottom - top + 1) if different else None


def _marked_image(first: QImage, mask: bytearray, area) -> QImage:
    """La primera en gris y apagada, y encima lo distinto en rojo. Con un recuadro: un píxel suelto no se vería con la imagen reducida."""
    width, height = first.width(), first.height()
    alpha = QImage(bytes(mask), width, height, width, QImage.Format.Format_Grayscale8).copy()
    overlay = QImage(width, height, QImage.Format.Format_ARGB32)
    overlay.fill(QColor("#ff2d2d"))
    overlay.setAlphaChannel(alpha)
    result = QImage(width, height, QImage.Format.Format_RGB32)
    result.fill(QColor("#202124"))
    painter = QPainter(result)
    painter.setOpacity(0.35)
    painter.drawImage(0, 0, first.convertToFormat(QImage.Format.Format_Grayscale8))
    painter.setOpacity(1.0)
    painter.drawImage(0, 0, overlay)
    if area:
        painter.setPen(QPen(QColor("#ff2d2d"), max(1, max(width, height) // 300)))
        painter.drawRect(*area)
    painter.end()
    return result


def compare_images(first_path: str, second_path: str, is_cancelled=None) -> ImageComparison | None:
    """Se puede llamar desde otro hilo. None si alguna no se puede leer: entonces se comparan como archivos sin más."""
    (first, first_format), (second, second_format) = _load(first_path), _load(second_path)
    if first.isNull() or second.isNull():
        return None
    first = first.convertToFormat(QImage.Format.Format_RGBA8888)
    second = second.convertToFormat(QImage.Format.Format_RGBA8888)
    details = _image_details(first_path, first, first_format), _image_details(second_path, second, second_format)
    total = first.width() * first.height()
    if abs(first.width() / first.height() - second.width() / second.height()) > _SAME_SHAPE:
        return ImageComparison(first, second, None, total, total, None, *details)  # encajarlas deformaría una y saldría todo distinto, sin decir nada
    resized = second if second.size() == first.size() else second.scaled(first.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    mask, different, area = _different_pixels(first, resized.convertToFormat(QImage.Format.Format_RGBA8888), is_cancelled)
    return ImageComparison(first, second, _marked_image(first, mask, area), different, total, area, *details)


def compare_files(first: str, second: str, external: list[str], external_for_images: bool = False, is_cancelled=None) -> tuple[str, object]:
    """Qué ha salido y lo que hay que mostrar. Si son idénticos no hace falta abrir nada más, tampoco un programa externo. Se llama desde otro hilo."""
    if file_compare.identical(first, second, is_cancelled):
        return "identical", None
    images = is_image(first) and is_image(second)
    if external and (external_for_images or not images):
        return "external", external
    if images:
        comparison = compare_images(first, second, is_cancelled)
        if comparison is not None:
            return "images", comparison
    if file_compare.is_text(first) and file_compare.is_text(second):
        found = file_compare.text_diff(first, second)
        if found is not None:
            return "text", found
    return "binary", file_compare.first_difference(first, second, is_cancelled)


class _DiffColors(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.formats = {}
        for mark, (text, background) in _DIFF_COLORS[theme.current().name].items():
            text_format = QTextCharFormat()
            text_format.setForeground(QColor(text))
            text_format.setBackground(QColor(background))
            self.formats[mark] = text_format

    def highlightBlock(self, text: str) -> None:
        if text.startswith(("+++ ", "--- ")):
            return
        chosen = self.formats.get(text[:1])
        if chosen is not None:
            self.setFormat(0, len(text), chosen)


class _ImagePane(QWidget):
    """Una de las imágenes, siempre con su propia proporción. Todas se acercan y se mueven a la vez, para mirar el mismo sitio en cada una."""

    def __init__(self, title: str, image: QImage, group: "ImageComparisonView") -> None:
        super().__init__()
        self.title, self.image, self.group = title, image, group
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._fit: tuple[float, QPixmap] | None = None
        self._dragging: QPointF | None = None

    def fit_scale(self) -> float:
        return min(self.width() / max(1, self.image.width()), self.height() / max(1, self.image.height()), 1.0)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.current().panel))
        fit = self.fit_scale()
        scale = fit * self.group.zoom_level
        size_x, size_y = self.image.width() * scale, self.image.height() * scale
        x = (self.width() - size_x) / 2 + self.group.offset.x()
        y = (self.height() - size_y) / 2 + self.group.offset.y()
        if self.group.zoom_level == 1:
            if self._fit is None or self._fit[0] != scale:  # reducir una imagen enorme en cada repintado se notaría
                smooth = self.image.scaled(round(size_x) or 1, round(size_y) or 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self._fit = (scale, QPixmap.fromImage(smooth))
            painter.drawPixmap(QPointF(x, y), self._fit[1])
        else:
            source = QRectF((0 - x) / scale, (0 - y) / scale, self.width() / scale, self.height() / scale).intersected(QRectF(self.image.rect()))
            target = QRectF(x + source.x() * scale, y + source.y() * scale, source.width() * scale, source.height() * scale)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1)
            painter.drawImage(target, self.image, source)

    def wheelEvent(self, event) -> None:
        self.group.zoom(_ZOOM_STEP if event.angleDelta().y() > 0 else 1 / _ZOOM_STEP)

    def mousePressEvent(self, event) -> None:
        self._dragging = event.position()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None and self.group.zoom_level > 1:
            self.group.pan(event.position() - self._dragging)
        self._dragging = event.position()

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self.group.reset_view()


class ImageComparisonView(QWidget):
    """Las dos imágenes lado a lado, cada una con sus datos. La columna de las diferencias se muestra si se pide."""

    def __init__(self, first_path: str, second_path: str, comparison: ImageComparison) -> None:
        super().__init__()
        self.zoom_level = 1.0  # 1: enteras
        self.offset = QPointF(0, 0)
        columns = [(os.path.basename(first_path), comparison.first, comparison.first_details), (os.path.basename(second_path), comparison.second, comparison.second_details)]
        if comparison.marked is not None:
            columns.append((tr("Diferencias"), comparison.marked, tr("En rojo, los píxeles que cambian. El recuadro marca la zona que los contiene")))
        self.panes = [_ImagePane(name, image, self) for name, image, _details in columns]
        self.columns: list[QWidget] = []
        self.detail_labels: list[QLabel] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for pane, (name, _image, details) in zip(self.panes, columns):
            holder = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            title = QLabel(name)
            title.setStyleSheet("font-weight: bold;")
            title.setToolTip(name)
            info = QLabel(details)
            info.setWordWrap(True)
            info.setMinimumHeight(info.fontMetrics().lineSpacing() * _DETAIL_LINES)  # las tres columnas de imágenes deben empezar a la misma altura
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_labels.append(info)
            for label in (title, info):
                label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                column.addWidget(label)
            column.addWidget(pane, 1)
            row.addWidget(holder, 1)
            self.columns.append(holder)
        self.show_differences(False)

    def show_differences(self, shown: bool) -> None:
        if len(self.columns) > 2:
            self.columns[2].setVisible(shown)

    def zoom(self, factor: float) -> None:
        level = max(1.0, min(_MAX_ZOOM, self.zoom_level * factor))
        if level == 1:
            self.reset_view()
            return
        self.offset *= level / self.zoom_level  # para seguir mirando el mismo sitio
        self.zoom_level = level
        self._repaint()

    def pan(self, delta: QPointF) -> None:
        self.offset += delta
        self._repaint()

    def reset_view(self) -> None:
        self.zoom_level = 1.0
        self.offset = QPointF(0, 0)
        self._repaint()

    def _repaint(self) -> None:
        for pane in self.panes:
            pane.update()


def _number(value: int) -> str:
    return QLocale().toString(value)


def _size_text(path: str) -> str:
    try:
        return _number(os.path.getsize(path))
    except OSError:
        return "?"


class CompareWindow(QDialog):
    """No bloquea la ventana principal: se puede dejar abierta mientras se sigue trabajando."""

    def __init__(self, first: str, second: str, parent=None) -> None:
        super().__init__(parent)
        self.first, self.second = first, second
        self.setWindowTitle(tr("Comparar archivos"))
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        paths = QLabel(f"{first}\n{second}")
        paths.setTextFormat(Qt.TextFormat.PlainText)
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        self.layout_ = QVBoxLayout(self)
        self.layout_.addWidget(paths)
        self.layout_.addWidget(self.verdict)

    def _say(self, text: str, good: bool = False) -> None:
        self.verdict.setText(text)
        if good:
            self.verdict.setStyleSheet(f"color: {theme.GIT_COLORS[theme.current().name][0]}; font-weight: bold; font-size: 11pt;")

    def show_identical(self) -> None:
        self._say(tr("Los archivos son idénticos"), True)
        self.layout_.addStretch(1)
        self.resize(520, 130)

    def show_text(self, found: file_compare.TextDiff) -> None:
        if not found.lines:
            self._say(tr("El texto es idéntico: solo cambian los finales de línea o la codificación"), True)
            self.layout_.addStretch(1)
            self.resize(520, 130)
            return
        self._say(tr("Líneas añadidas: {added}. Líneas quitadas: {removed}", added=_number(found.added), removed=_number(found.removed))
                  + (tr(". Se muestran solo las primeras {count} líneas", count=_number(file_compare.MAX_SHOWN_LINES)) if found.truncated else ""))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setFrameShape(QFrame.Shape.StyledPanel)
        self.colors = _DiffColors(self.text.document())
        self.text.setPlainText("\n".join(found.lines))
        self.layout_.addWidget(self.text, 1)
        self.resize(900, 620)

    def show_images(self, comparison: ImageComparison) -> None:
        if comparison.marked is None:
            self._say(tr("Tienen distinto tamaño y distinta proporción: no se pueden comparar píxel a píxel"))
        elif comparison.different == 0 and comparison.same_size:
            self._say(tr("Las imágenes son idénticas, aunque los archivos no lo sean"), True)
        else:
            percent = QLocale().toString(100 * comparison.different / max(1, comparison.total), "f", 2)
            text = tr("Píxeles distintos: {count} ({percent} %)", count=_number(comparison.different), percent=percent)
            if comparison.area:
                text += tr(". Zona con diferencias: {w} × {h} en {x}, {y}", w=comparison.area[2], h=comparison.area[3], x=comparison.area[0], y=comparison.area[1])
            if not comparison.same_size:
                text += tr(". Tienen distinto tamaño: para compararlas, la segunda se ajusta al de la primera")
            self._say(text)
        self.view = ImageComparisonView(self.first, self.second, comparison)
        self.view.setToolTip(tr("Rueda: acercar o alejar. Arrastrar: mover. Doble clic: ver entera"))
        self.differences = QCheckBox(tr("Mostrar las diferencias"))
        self.differences.setToolTip(tr("Añade una columna con los píxeles que cambian marcados en rojo"))
        self.differences.setEnabled(comparison.marked is not None and comparison.different > 0)
        self.differences.toggled.connect(self.view.show_differences)
        self.layout_.addWidget(self.differences)
        self.layout_.addWidget(self.view, 1)
        self.resize(1000, 640)

    def show_binary(self, position: int | None) -> None:
        sizes = tr("Tamaños: {first} y {second} bytes", first=_size_text(self.first), second=_size_text(self.second))
        where = tr("El primer byte distinto está en la posición {position}", position=_number(position)) if position is not None else ""
        self._say("\n".join(item for item in (tr("Los archivos son distintos"), sizes, where) if item))
        self.layout_.addStretch(1)
        self.resize(520, 170)
