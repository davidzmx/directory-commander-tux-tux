"""Un panel de archivos: barra propia, lista en varias vistas, filtro y estado."""
from __future__ import annotations

import fnmatch
import os
from pathlib import PurePath

from PySide6.QtCore import (QEvent, QFileSystemWatcher, QItemSelection, QItemSelectionModel, QModelIndex, QObject, QPoint, QRect,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSlider, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListView, QMenu, QRubberBand, QSizePolicy, QStackedLayout, QStackedWidget, QStyle,
                               QStyleOptionViewItem, QStyledItemDelegate, QTableView, QToolButton, QVBoxLayout,
                               QWidget)

from ..core import archive
from ..core.bar_buttons import PATH
from ..core.listing import COMPUTER_LABEL, COMPUTER_PATH, format_size, has_computer_view, is_results, list_dir, location_label, results_name, sort_entries
from ..core.platform_utils import home_dir, root_of
from . import theme
from .drag_drop import DropDecider, accept_drop, show_operation, start_drag
from .hover_card import HoverCard
from .icons import pixmap, themed
from .model import COLUMNS, EntryRole, FileModel, folder_icon
from ..core.i18n import tr

COLUMN_INDEX = {key: index for index, (key, _label) in enumerate(COLUMNS)}
INDENT = 14
CHEVRON = 15
NAME_PADDING = 32    # márgenes e icono de 16 px
DISK_FULL = 0.9
GROUP_ROW_EXTRA = 12
MIN_DRIVE_NAME_WIDTH = 190
MIN_BREADCRUMB_WIDTH = 60
DRIVE_COLUMN_WIDTHS = {"filesystem": 130, "type": 190, "used": 150, "free": 92}  # en el orden en que se ocultan
SIZE_COLUMN_WIDTH = 72
CHECKBOX_WIDTH = 20


def paint_find_match(painter, view, option, text: str, text_rect: QRect, margin: int) -> None:
    """Resalta en el nombre lo que se está buscando. Se dibuja encima del texto ya pintado, de nuevo y con su fondo, porque Qt pinta el texto de una vez."""
    needle = view.find_text
    if not needle or needle not in text.lower():
        return
    metrics = option.fontMetrics
    room = text_rect.width() - 2 * margin
    left = text_rect.left() + margin
    shown = text
    if metrics.horizontalAdvance(text) > room:
        wrapped = option.features & QStyleOptionViewItem.ViewItemFeature.WrapText
        shown = "" if wrapped else metrics.elidedText(text, option.textElideMode, room)
    position = shown.lower().find(needle)
    if position < 0:  # la coincidencia no está a la vista, o el texto ocupa varias líneas: se marca debajo, para que no parezca que este no coincide
        painter.fillRect(QRect(left, text_rect.bottom() - 1, room, 2), QColor(theme.FIND_MATCH))
        return
    text = shown
    width = metrics.horizontalAdvance(text)
    if option.displayAlignment & Qt.AlignmentFlag.AlignHCenter:
        left += (room - width) // 2
    left += metrics.horizontalAdvance(text[:position])
    found = text[position:position + len(needle)]
    height = metrics.height()
    area = QRect(left, text_rect.center().y() - height // 2, metrics.horizontalAdvance(found), height)
    painter.save()
    painter.setClipRect(text_rect, Qt.ClipOperation.IntersectClip)
    painter.fillRect(area, QColor(theme.FIND_MATCH))
    painter.setPen(QColor(theme.FIND_MATCH_TEXT))
    painter.setFont(option.font)
    painter.drawText(area, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, found)
    painter.restore()


def paint_style_option_match(painter, view, delegate, option, index) -> None:
    """Para los delegados que pintan con Qt: el nombre va donde el estilo dice."""
    if not view.find_text:
        return
    filled = QStyleOptionViewItem(option)
    delegate.initStyleOption(filled, index)
    style = view.style()
    text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, filled, view)
    margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, filled, view) + 1
    paint_find_match(painter, view, filled, filled.text, text_rect, margin)


def paint_level(painter, view, rect: QRect, entry) -> None:
    """Fondo de los elementos de una carpeta desplegada. La sangría no cambia."""
    if view.level_colors and entry is not None and entry.depth > 0:
        painter.fillRect(rect, theme.level_color(view.pane_role, entry.depth))


class CellDelegate(QStyledItemDelegate):
    """Columnas distintas del nombre: solo se marcan como seleccionadas con la selección de fila completa."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selected_now = False

    def paint(self, painter, option, index) -> None:
        view = self.parent()
        paint_level(painter, view, option.rect, index.data(EntryRole))
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and view.full_row
        if selected:
            painter.fillRect(option.rect, view.selection_color())
        if COLUMNS[index.column()][0] == "used":
            self._paint_usage(painter, option.rect, index.data(EntryRole))
            return
        plain = option.__class__(option)
        plain.state &= ~(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected)
        self._selected_now = selected
        super().paint(painter, plain, index)
        self._selected_now = False

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if self._selected_now:
            option.palette.setColor(QPalette.ColorRole.Text, QColor(theme.current().sel_text))

    @staticmethod
    def _paint_usage(painter, cell: QRect, entry) -> None:
        """Barra de ocupación de una unidad. Se vuelve roja con poco espacio libre."""
        fraction = entry.used_fraction if entry is not None else None
        if fraction is None:
            return
        t = theme.current()
        bar = cell.adjusted(4, 3, -6, -3)
        painter.fillRect(bar, QColor(t.border))
        painter.fillRect(bar.left(), bar.top(), int(bar.width() * fraction), bar.height(),
                         QColor(t.meter_low if fraction >= DISK_FULL else t.meter_ok))


class NameDelegate(QStyledItemDelegate):
    """Dibuja la sangría, la flecha de las carpetas desplegables y la selección en el nombre."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selected_now = False

    def _offset(self, entry) -> int:
        return (entry.depth * INDENT if entry else 0) + CHEVRON

    def name_rect(self, cell: QRect, entry, metrics, checkable: bool) -> QRect:
        """Lo que ocupa el archivo en la fila: casilla, icono y nombre, no el resto de la columna."""
        left = cell.left() + self._offset(entry)
        width = NAME_PADDING + (CHECKBOX_WIDTH if checkable else 0) + metrics.horizontalAdvance(entry.display_name)
        return QRect(left, cell.top(), min(width, cell.right() - left + 1), cell.height())

    def paint(self, painter, option, index) -> None:
        entry = index.data(EntryRole)
        if index.column() != 0 or entry is None:
            super().paint(painter, option, index)
            return
        if entry.is_group:
            self._paint_group(painter, option.rect, entry.name, option.font)
            return
        paint_level(painter, self.parent(), option.rect, entry)
        offset = self._offset(entry)
        model = index.model()
        if model.can_unfold(entry):
            name = "chevron-down" if model.is_expanded(entry.path) else "chevron-right"
            target = QRect(option.rect.left() + offset - CHEVRON + 1, option.rect.center().y() - 6, 12, 12)
            painter.drawPixmap(target, pixmap(name, 12))
        view = self.parent()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        area = option.rect if view.full_row else self.name_rect(option.rect, entry, option.fontMetrics, model.check_mode)
        if selected:
            painter.fillRect(area, view.selection_color())
        elif view.hover_row == index.row():
            painter.fillRect(area, QColor(theme.current().hover))
        shifted = option.__class__(option)
        shifted.rect = option.rect.adjusted(offset, 0, 0, 0)
        shifted.state &= ~(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected)
        self._selected_now = selected
        super().paint(painter, shifted, index)
        self._selected_now = False
        paint_style_option_match(painter, view, self, shifted, index)

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if self._selected_now:
            option.palette.setColor(QPalette.ColorRole.Text, QColor(theme.current().sel_text))

    @staticmethod
    def _paint_group(painter, cell: QRect, title: str, font) -> None:
        """Título de un grupo: texto seguido de una línea hasta el final de la fila."""
        t = theme.current()
        painter.save()
        heading = QFont(font)
        heading.setPointSizeF(font.pointSizeF() + 1.5)
        painter.setFont(heading)
        painter.setPen(QColor(t.group_text))
        text_rect = cell.adjusted(8, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        width = painter.fontMetrics().horizontalAdvance(title)
        painter.setPen(QColor(t.accent))
        y = cell.center().y()
        painter.drawLine(text_rect.left() + width + 8, y, cell.right() - 4, y)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        # Las casillas se dibujan desplazadas: el clic debe medirse en el mismo sitio.
        shifted = option.__class__(option)
        shifted.rect = option.rect.adjusted(self._offset(index.data(EntryRole)), 0, 0, 0)
        return super().editorEvent(event, model, shifted, index)

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if editor is not None:
            editor.setPalette(QApplication.palette())  # la tabla tiene el color de selección transparente
        return editor

    def updateEditorGeometry(self, editor, option, index) -> None:
        super().updateEditorGeometry(editor, option, index)
        editor.setGeometry(editor.geometry().adjusted(self._offset(index.data(EntryRole)) + 18, 0, 0, 0))


class TileDelegate(QStyledItemDelegate):
    """Vista de mosaicos: icono grande con nombre, tipo y tamaño."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(262, 54)

    def paint(self, painter, option, index) -> None:
        entry = index.data(EntryRole)
        if entry is None:
            return
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        icon_rect = QRect(option.rect.left() + 4, option.rect.top() + 4, 48, 48)
        option.icon.paint(painter, icon_rect)
        text_rect = option.rect.adjusted(58, 6, -4, -4)
        details = entry.type_label if entry.is_dir else f"{entry.type_label} · {format_size(entry.effective_size)}"
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        t = theme.current()
        painter.setPen(QColor(t.sel_text if selected else t.dir_text if entry.is_dir else t.text))
        elided = option.fontMetrics.elidedText(entry.display_name, Qt.TextElideMode.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, elided)
        painter.setPen(QColor(t.sel_text if selected else t.dim))
        painter.drawText(text_rect.adjusted(0, option.fontMetrics.height() + 2, 0, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, details)
        painter.restore()
        name_line = QRect(text_rect.left(), text_rect.top(), text_rect.width(), option.fontMetrics.height())
        plain = QStyleOptionViewItem(option)
        plain.displayAlignment = Qt.AlignmentFlag.AlignLeft
        paint_find_match(painter, self.parent(), plain, entry.display_name, name_line, 0)


_MAX_REMEMBERED_SIZES = 200_000


class ItemDelegate(QStyledItemDelegate):
    """Lista e iconos: el archivo ocupa su icono y su nombre, no toda la celda.
    En la lista, además, sangría y flecha de las carpetas desplegables, como en detalles."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sizes: dict[tuple, QSize] = {}

    def _indent(self, index: QModelIndex) -> int:
        if not self.parent().list_mode:
            return 0
        entry = index.data(EntryRole)
        return (entry.depth * INDENT if entry else 0) + CHEVRON

    def sizeHint(self, option, index) -> QSize:
        """En la lista, Qt pregunta por todos los archivos cada vez que recoloca, también en las pestañas que no se ven, y al cambiar de tema lo hace muchas veces.
        Lo que mide depende solo de estos datos, así que basta con medir cada nombre una vez."""
        view = self.parent()
        if not view.list_mode:
            return super().sizeHint(option, index)
        entry = index.data(EntryRole)
        metrics = option.fontMetrics
        key = (entry.display_name, entry.depth, index.model().check_mode, metrics.height(), metrics.averageCharWidth()) if entry else None
        size = self._sizes.get(key) if key else None
        if size is None:
            size = QSize(super().sizeHint(option, index).width() + self._indent(index), metrics.height() + 3)
            if key:
                if len(self._sizes) >= _MAX_REMEMBERED_SIZES:
                    self._sizes.clear()
                self._sizes[key] = size
        return size

    def content_rect(self, option: QStyleOptionViewItem, index: QModelIndex) -> QRect:
        view = self.parent()
        self.initStyleOption(option, index)
        option.rect = option.rect.adjusted(self._indent(index), 0, 0, 0)
        style = view.style()
        icon = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option, view)
        text = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, view)
        if option.decorationPosition == QStyleOptionViewItem.Position.Top:
            return icon.united(text)
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, option, view) + 1
        width = min(option.fontMetrics.horizontalAdvance(option.text) + 2 * margin + 3, text.width())
        return QRect(option.rect.left(), option.rect.top(), text.left() - option.rect.left() + width, option.rect.height())

    def paint(self, painter, option, index) -> None:
        view = self.parent()
        narrow = QStyleOptionViewItem(option)
        # Qt marca la celda entera bajo el ratón: solo debe resaltarse si está sobre el archivo.
        narrow.state &= ~QStyle.StateFlag.State_MouseOver
        if view.hover_row == index.row():
            narrow.state |= QStyle.StateFlag.State_MouseOver
        model = index.model()
        entry = index.data(EntryRole)
        if view.list_mode:
            paint_level(painter, view, option.rect, entry)
        if view.list_mode and entry is not None and model.can_unfold(entry):
            name = "chevron-down" if model.is_expanded(entry.path) else "chevron-right"
            left = option.rect.left() + entry.depth * INDENT + 1
            painter.drawPixmap(QRect(left, option.rect.center().y() - 6, 12, 12), pixmap(name, 12))
        if not view.full_row and view.viewMode() != QListView.ViewMode.IconMode:
            narrow.rect = self.content_rect(QStyleOptionViewItem(option), index)
        elif view.full_row and view.list_mode:
            narrow.rect = option.rect.adjusted(self._indent(index), 0, 0, 0)
        super().paint(painter, narrow, index)
        paint_style_option_match(painter, view, self, narrow, index)


class _DropMixin:
    """Arrastrar y soltar entre carpetas, paneles y otras aplicaciones. Sobre el nombre de una carpeta va a ella;
    en otro sitio, a la carpeta del panel. En la misma unidad mueve y a otra copia; Mayús obliga a mover y Ctrl a copiar."""

    drop_handler = None
    find_text = ""  # lo que se está buscando, en minúsculas: se resalta en los nombres

    def _setup_drag(self) -> None:
        # QListView lo desactiva cada vez que cambia de modo o de movimiento: hay que repetirlo después.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._decider = DropDecider()

    def startDrag(self, _actions) -> None:
        indexes = self.selectedIndexes()
        if indexes:
            start_drag(self, self.model().mimeData(indexes), indexes[0].data(Qt.ItemDataRole.DecorationRole) or QIcon())

    def content_offset(self) -> QPoint:
        return QPoint(self.horizontalOffset(), self.verticalOffset())

    def _drop_folder_at(self, position: QPoint):
        """La carpeta bajo ese punto. Solo cuenta su nombre, como al seleccionar."""
        index = self.name_index_at(position)
        entry = index.data(EntryRole) if index.isValid() else None
        return (index, entry) if entry is not None and entry.is_dir else (QModelIndex(), None)

    def _drop_destination(self, entry) -> str:
        return entry.path if entry is not None else self.model().path

    def dragEnterEvent(self, event) -> None:
        if self._decider.enter(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._decider.paths:
            self._decider.begin(event)
        index, entry = self._drop_folder_at(event.position().toPoint())
        self.set_hover_row(index.row() if index.isValid() else -1)
        show_operation(event, self._decider.operation(event, self._drop_destination(entry)))

    def dragLeaveEvent(self, event) -> None:
        self.set_hover_row(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.set_hover_row(-1)
        self._decider.begin(event)
        _index, entry = self._drop_folder_at(event.position().toPoint())
        operation = self._decider.operation(event, self._drop_destination(entry))
        ask = self._decider.take_right_button()
        if operation is None or not self.drop_handler:
            event.ignore()
            return
        accept_drop(event)
        paths, target = self._decider.paths, entry.path if entry is not None else None
        # Después de contestar: la copia puede preguntar qué hacer con lo que ya existe, y mientras tanto
        # el programa desde el que se arrastra seguiría bloqueado.
        QTimer.singleShot(0, lambda: self.drop_handler(paths, target, operation == "move", ask))


class DetailsView(_DropMixin, QTableView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.hover_row = -1
        self.full_row = False
        self.level_colors = True
        self.pane_role = "single"
        self._name_delegate = NameDelegate(self)
        self.setItemDelegate(CellDelegate(self))
        # QTableView rellena la fila seleccionada con este color antes de llamar a los delegados,
        # que son los que dibujan la selección. La hoja de estilos no llega a ese relleno.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self._setup_drag()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        self.setIconSize(QSize(16, 16))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setItemDelegateForColumn(0, self._name_delegate)
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 5)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def name_rect(self, index: QModelIndex) -> QRect:
        name_index = index.siblingAtColumn(0)
        return self._name_delegate.name_rect(self.visualRect(name_index), name_index.data(EntryRole), self.fontMetrics(),
                                             self.model().check_mode)

    def selection_color(self) -> QColor:
        t = theme.current()
        return QColor(t.sel_dest if self.pane_role == "dest" else t.sel_other if self.pane_role == "other" else t.sel_source)

    def name_index_at(self, position: QPoint) -> QModelIndex:
        """Índice del archivo bajo ese punto. Sin selección de fila completa, solo cuenta su nombre."""
        index = self.indexAt(position)
        if not index.isValid() or self.model().entry(index.row()) is None:
            return QModelIndex()
        if self.full_row:
            return index.siblingAtColumn(0)
        if index.column() == 0 and self.name_rect(index).contains(position):
            return index
        return QModelIndex()

    def rows_touching(self, rect: QRect) -> list[int]:
        """Filas que toca un rectángulo, en coordenadas de la lista: puede salirse por arriba o por abajo. Sin selección de fila completa, solo cuenta el nombre."""
        model, header = self.model(), self.verticalHeader()
        count = model.rowCount()
        if not count:
            return []
        first, last = header.logicalIndexAt(rect.top()), header.logicalIndexAt(rect.bottom())
        if first < 0:
            first = 0 if rect.top() < header.sectionViewportPosition(0) else count
        if last < 0:
            last = -1 if rect.bottom() < header.sectionViewportPosition(0) else count - 1
        return [row for row in range(first, last + 1)
                if model.entry(row) is not None and (self.full_row or self.name_rect(model.index(row, 0)).intersects(rect))]

    def lasso_rows(self):
        """Devuelve la función que dice qué filas toca un rectángulo, dado respecto al contenido de la lista."""
        return lambda rect: self.rows_touching(rect.translated(-self.content_offset()))

    def set_hover_row(self, row: int) -> None:
        if row == self.hover_row:
            return
        previous, self.hover_row = self.hover_row, row
        for changed in (previous, row):
            if changed >= 0:
                self.viewport().update(self.visualRect(self.model().index(changed, 0)))


class ItemsView(_DropMixin, QListView):
    """Lista, iconos, mosaicos, miniaturas y tira de fotogramas: la misma vista configurada."""

    MODES = {
        # modo: (iconos en cuadrícula, tamaño icono, celda, con saltos de línea, flujo horizontal)
        "list": (False, 16, None, True, False),
        "smallicons": (False, 16, (210, 22), True, True),
        "largeicons": (True, 48, (104, 88), True, True),
        "tiles": (False, 48, (270, 58), True, True),
        "thumbnails": (True, 128, (148, 172), True, True),
        "filmstrip": (True, 96, (118, 134), False, True),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self._setup_drag()
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.full_row = False
        self.level_colors = True
        self.pane_role = "single"
        self.list_mode = False
        self.hover_row = -1
        self._tile = TileDelegate(self)
        self._plain = ItemDelegate(self)
        self.configure("thumbnails")

    def configure(self, mode: str) -> None:
        grid, icon, cell, wrap, horizontal = self.MODES[mode]
        self.list_mode = mode == "list"
        # Con carpetas desplegadas cada fila mide distinto; en lista, además, por su nombre.
        self.setUniformItemSizes(not self.list_mode)
        # Medir cada fila lleva tiempo: en carpetas enormes, por lotes para no bloquear la ventana.
        self.setLayoutMode(QListView.LayoutMode.Batched if self.list_mode else QListView.LayoutMode.SinglePass)
        self.setBatchSize(2000)
        self.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.setMovement(QListView.Movement.Static)
        self._setup_drag()
        self.setFlow(QListView.Flow.LeftToRight if horizontal else QListView.Flow.TopToBottom)
        self.setWrapping(wrap)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(icon, icon))
        self.setGridSize(QSize(*cell) if cell else QSize())
        self.setWordWrap(grid)
        self.setItemDelegate(self._tile if mode == "tiles" else self._plain)

    def finish_layout(self) -> None:
        """Coloca ya todos los elementos, sin esperar a los lotes."""
        if self.list_mode:
            self.setLayoutMode(QListView.LayoutMode.SinglePass)
            self.doItemsLayout()
            self.setLayoutMode(QListView.LayoutMode.Batched)

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:
        if self.list_mode and index.isValid() and not self.visualRect(index).isValid():
            self.finish_layout()  # ese archivo aún no tiene posición: sin ella no habría adónde ir
        super().scrollTo(index, hint)

    def set_hover_row(self, row: int) -> None:
        if row == self.hover_row:
            return
        previous, self.hover_row = self.hover_row, row
        for changed in (previous, row):
            if changed >= 0:
                self.viewport().update(self.visualRect(self.model().index(changed, 0)))

    def name_rect(self, index: QModelIndex) -> QRect:
        if self.itemDelegate() is not self._plain:
            return self.visualRect(index)
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = self.visualRect(index)
        return self._plain.content_rect(option, index)

    def lasso_rows(self):
        """Devuelve la función que dice qué elementos toca un rectángulo, dado respecto al contenido de la lista. Solo cuenta lo que ocupan, no el hueco de su celda.
        Preguntar dónde está un elemento mide su texto, y hacerlo con todos en cada movimiento se notaría: se mide una vez y luego solo se compara."""
        model = self.model()
        offset = self.content_offset()
        boxes = []
        for row in range(model.rowCount()):
            if model.entry(row) is not None:
                index = model.index(row, 0)
                box = (self.visualRect(index) if self.full_row else self.name_rect(index)).translated(offset)
                boxes.append((row, box.left(), box.top(), box.right(), box.bottom()))

        def touching(rect: QRect) -> list[int]:
            left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
            return [row for row, x1, y1, x2, y2 in boxes if x1 <= right and left <= x2 and y1 <= bottom and top <= y2]

        return touching

    def name_index_at(self, position: QPoint) -> QModelIndex:
        """Índice del archivo bajo ese punto: solo cuenta lo que ocupa, no el hueco de su celda."""
        index = self.indexAt(position)
        if not index.isValid() or self.model().entry(index.row()) is None:
            return QModelIndex()
        return index if self.full_row or self.name_rect(index).contains(position) else QModelIndex()


def path_crumbs(path: str) -> list[tuple[str, str]]:
    """Tramos de la barra de ruta como (etiqueta, destino). En Windows empiezan por «Este equipo»."""
    if is_results(path):
        return [(results_name(path), path)]
    crumbs = [(COMPUTER_LABEL, COMPUTER_PATH)] if has_computer_view() else []
    if not path or path == COMPUTER_PATH:
        return crumbs
    accumulated = None
    for part in PurePath(path).parts:
        accumulated = PurePath(part) if accumulated is None else accumulated / part
        crumbs.append((part.rstrip("\\/") or part, str(accumulated)))  # la raíz de Linux es solo "/"
    return crumbs


class Breadcrumb(QFrame):
    """Ruta en botones; un clic en el espacio libre permite escribirla."""

    pathEntered = Signal(str)
    fieldShown = Signal(bool)  # la ruta está como texto, para escribirla o junto a la lista de carpetas

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("crumbFrame")
        self._path = ""
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(1, 0, 1, 0)
        self._bar = QWidget()
        self._row = QHBoxLayout(self._bar)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)
        self._edit = QLineEdit()
        self._edit.returnPressed.connect(self._commit)
        self._edit.installEventFilter(self)
        self._stack.addWidget(self._bar)
        self._stack.addWidget(self._edit)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Mínimo explícito: si no, una ruta larga impone su ancho a toda la ventana en vez de recortarse.
        self.setMinimumWidth(60)
        self._buttons: list[QToolButton] = []
        self._arrows: dict[QToolButton, QToolButton] = {}
        self.folder_menus = False

    def set_folder_menus(self, enabled: bool) -> None:
        """Si el › de cada tramo es un botón que despliega sus subcarpetas, o solo una separación."""
        if enabled != self.folder_menus:
            self.folder_menus = enabled
            if self._path:
                self.set_path(self._path)

    def _folder_arrow(self, folder: str, inside: str) -> QToolButton:
        arrow = QToolButton()
        arrow.setObjectName("crumbArrow")
        arrow.setAutoRaise(True)
        arrow.setText(ARROW_CLOSED)
        arrow.setToolTip(tr("Subcarpetas"))
        arrow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(arrow)
        menu.aboutToShow.connect(lambda: self._fill_folder_menu(menu, folder, inside))
        menu.aboutToShow.connect(lambda: arrow.setText(ARROW_OPEN))
        menu.aboutToHide.connect(lambda: arrow.setText(ARROW_CLOSED))
        arrow.setMenu(menu)
        return arrow

    def _fill_folder_menu(self, menu: QMenu, folder: str, inside: str) -> None:
        """Las subcarpetas de ese tramo. La que forma parte de la ruta, en negrita."""
        menu.clear()
        try:
            folders = [entry for entry in sort_entries(list_dir(folder)) if entry.is_dir]
        except OSError as exc:
            menu.addAction(str(exc)).setEnabled(False)
            return
        if not folders:
            menu.addAction(tr("Sin subcarpetas")).setEnabled(False)
        for entry in folders:
            action = menu.addAction(folder_icon(entry.path) if entry.is_drive else themed("folder"), entry.display_name)
            action.triggered.connect(lambda _=False, target=entry.path: self.pathEntered.emit(target))
            if os.path.normcase(entry.path) == os.path.normcase(inside):
                font = action.font()
                font.setBold(True)
                action.setFont(font)

    def set_path(self, path: str) -> None:
        self._path = path
        self._edit.setText(location_label(path))
        self.show_buttons()
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._buttons = []
        self._arrows = {}
        for position, (label, target) in enumerate(crumbs := path_crumbs(path)):
            last = position == len(crumbs) - 1
            button = QToolButton()
            button.setObjectName("crumb")
            button.setAutoRaise(True)
            button.setText(label if last or self.folder_menus else f"{label} ›")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(lambda _=False, t=target: self.pathEntered.emit(t))
            self._row.addWidget(button)
            self._buttons.append(button)
            if self.folder_menus:
                self._arrows[button] = self._folder_arrow(target, "" if last else crumbs[position + 1][1])
                self._row.addWidget(self._arrows[button])
        self._row.addStretch(1)
        self._elide()

    def show_field(self) -> None:
        self._stack.setCurrentWidget(self._edit)
        self._edit.setText(location_label(self._path))
        self.fieldShown.emit(True)

    def show_buttons(self) -> None:
        self._stack.setCurrentWidget(self._bar)
        self.fieldShown.emit(False)

    def begin_edit(self) -> None:
        self.show_field()
        self._edit.selectAll()
        self._edit.setFocus()

    def is_editing(self) -> bool:
        return self._stack.currentWidget() is self._edit

    def _commit(self) -> None:
        text = self._edit.text().strip().strip('"')
        self.show_buttons()
        if has_computer_view() and text.casefold() == COMPUTER_LABEL.casefold():
            self.pathEntered.emit(COMPUTER_PATH)
        elif text:
            self.pathEntered.emit(os.path.expanduser(text))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_edit()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._edit:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.show_buttons()
                return True
            if event.type() == QEvent.Type.FocusOut:
                self.show_buttons()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        """Si no cabe la ruta, oculta los tramos iniciales: el final es lo que más importa."""
        available = self.width() - 12
        used = 0
        for button in reversed(self._buttons):
            arrow = self._arrows.get(button)
            used += button.sizeHint().width() + (arrow.sizeHint().width() if arrow else 0)
            shown = used <= available or button is self._buttons[-1]
            button.setVisible(shown)
            if arrow:
                arrow.setVisible(shown)


class FindBar(QFrame):
    """Aparece al escribir en la lista: busca en los nombres de la carpeta y resalta lo que coincide, sin ocultar nada."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("findBar")
        self.edit = QLineEdit()
        self.count = QLabel()
        self.count.setObjectName("findCount")
        self.pin = QToolButton()
        self.pin.setCheckable(True)
        self.pin.setAutoRaise(True)
        self.pin.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # pulsarlo no debe parecer que se ha salido de la búsqueda
        self.pin.setToolTip(tr("Mantener abierta la búsqueda"))
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 4, 2)
        row.setSpacing(6)
        row.addWidget(QLabel(tr("Buscar:")))
        row.addWidget(self.edit, 1)
        row.addWidget(self.count)
        row.addWidget(self.pin)
        self.apply_icons()

    def apply_icons(self) -> None:
        self.pin.setIcon(themed("pin"))


class FolderList(QObject):
    """Muestra un menú de carpetas como una lista desplegable: justo debajo de la ruta, con su mismo ancho y marcando la carpeta actual."""

    MIN_WIDTH = 260  # con un panel estrecho, la lista debe seguir dejando leer las rutas

    def __init__(self, menu: QMenu, anchor: QWidget, breadcrumb: Breadcrumb, current_path) -> None:
        super().__init__(menu)
        self.menu, self.anchor, self.breadcrumb, self.current_path = menu, anchor, breadcrumb, current_path
        menu.setObjectName("folderList")
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(self._showing)
        menu.aboutToHide.connect(breadcrumb.show_buttons)
        menu.installEventFilter(self)

    def _showing(self) -> None:
        width = max(self.anchor.width(), self.MIN_WIDTH)
        self.menu.setFixedWidth(width)
        room = width - 2 * self.menu.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize) - 16
        metrics = self.menu.fontMetrics()
        for action in self.menu.actions():
            path = action.data()
            if path:
                action.setToolTip(location_label(path))
                action.setText(metrics.elidedText(location_label(path), Qt.TextElideMode.ElideMiddle, room))
        self.breadcrumb.show_field()

    def _place(self) -> None:
        screen = self.anchor.screen().availableGeometry()
        corner = self.anchor.mapToGlobal(QPoint(0, self.anchor.height()))
        x = max(screen.left(), min(corner.x(), screen.right() + 1 - self.menu.width()))
        y = corner.y()
        if y + self.menu.height() > screen.bottom() + 1:  # si no cabe debajo, encima, como cualquier lista desplegable
            y = max(screen.top(), self.anchor.mapToGlobal(QPoint(0, 0)).y() - self.menu.height())
        self.menu.move(x, y)
        current = self.current_path()
        for action in self.menu.actions():
            if action.data() == current:
                self.menu.setActiveAction(action)
                break

    def eventFilter(self, watched, event) -> bool:
        if watched is self.menu and event.type() == QEvent.Type.Show:
            self._place()
        return False


class PaneBar(QFrame):
    """La línea interior del contorno se pinta aquí y no en la hoja de estilos: así la ruta, que va encima, la cubre con la suya."""

    role = "single"

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(theme.pane_ring(theme.current(), self.role)))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


SELECT_EVERY_MS = 30
PATH_TOOLS = {"menu-arrow", "copypath"}
GIT_BRANCH_MAX_WIDTH = 140
ARROW_CLOSED = "›"
ARROW_OPEN = "˅"
FIND_PAGE = 10


LASSO_START_DISTANCE = 3  # lo justo para que un clic con el pulso movido no dibuje nada; más se notaría como un retraso al empezar
LASSO_MEASURE_AT_ONCE = 5000  # hasta aquí, medir al pulsar pasa desapercibido; en carpetas enormes se deja para cuando haga falta


class Lasso:
    """Arrastrar desde fuera de un nombre dibuja un rectángulo y selecciona lo que toca.
    Ctrl invierte lo que toca y Mayús lo añade a lo que ya había."""

    def __init__(self, view, position: QPoint, keys) -> None:
        self.view, self.press, self.last, self.keys = view, position, position, keys
        self.start = position + view.content_offset()  # si la lista se desplaza, el rectángulo sigue anclado al mismo sitio
        # Por filas: al pulsar un nombre, la lista selecciona solo su columna, y al invertir celda a celda la fila seguiría medio seleccionada.
        self.base = {index.row() for index in view.selectionModel().selectedIndexes()}
        self.band: QRubberBand | None = None
        self.rows: list[int] | None = None
        # Mejor aquí que al empezar a arrastrar: ahora nada se está moviendo y no se nota.
        self._touching = view.lasso_rows() if view.model().rowCount() <= LASSO_MEASURE_AT_ONCE else None
        self._scroller = QTimer(view, interval=50)
        self._scroller.timeout.connect(self._scroll)
        # El rectángulo sigue al ratón en cada movimiento. Seleccionar cuesta más, y si se hiciera también en cada uno el rectángulo se quedaría atrás.
        self._selecter = QTimer(view, interval=SELECT_EVERY_MS, singleShot=True)
        self._selecter.timeout.connect(self._update_selection)

    def move(self, position: QPoint) -> None:
        self.last = position
        if self.band is None:
            if (position - self.press).manhattanLength() < LASSO_START_DISTANCE:
                return
            self.band = QRubberBand(QRubberBand.Shape.Rectangle, self.view.viewport())
            palette = self.band.palette()  # la de la lista de detalles lleva el resaltado transparente
            palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.current().accent))
            self.band.setPalette(palette)
            self.band.show()
            self._scroller.start()
        self._update()

    def _update(self) -> None:
        rect = QRect(self.start - self.view.content_offset(), self.last).normalized()
        self.band.setGeometry(rect.intersected(self.view.viewport().rect()))
        if not self._selecter.isActive():
            self._selecter.start()

    def _update_selection(self) -> None:
        if self._touching is None:
            self._touching = self.view.lasso_rows()
        offset = self.view.content_offset()
        rows = self._touching(QRect(self.start, self.last + offset).normalized())
        if rows != self.rows:
            self.rows = rows
            self._select(rows)

    def _select(self, touched: list[int]) -> None:
        if self.keys & Qt.KeyboardModifier.ControlModifier:
            rows = sorted(self.base ^ set(touched))
        elif self.keys & Qt.KeyboardModifier.ShiftModifier:
            rows = sorted(self.base | set(touched))
        else:
            rows = touched
        model = self.view.model()
        last_column = max(0, model.columnCount() - 1)
        chosen = QItemSelection()
        position = 0
        while position < len(rows):
            end = position
            while end + 1 < len(rows) and rows[end + 1] == rows[end] + 1:
                end += 1
            chosen.select(model.index(rows[position], 0), model.index(rows[end], last_column))
            position = end + 1
        self.view.selectionModel().select(chosen, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _scroll(self) -> None:
        """Al salir de la lista arrastrando, se desplaza para poder seguir seleccionando."""
        area = self.view.viewport().rect()
        before = self.view.content_offset()
        for bar, value, low, high in ((self.view.verticalScrollBar(), self.last.y(), area.top(), area.bottom()),
                                      (self.view.horizontalScrollBar(), self.last.x(), area.left(), area.right())):
            if value < low:
                bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepSub)
            elif value > high:
                bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd)
        if self.view.content_offset() != before:
            self._update()

    def finish(self) -> None:
        self._scroller.stop()
        self._scroller.deleteLater()
        if self._selecter.isActive():  # lo último que se dibujó es lo que debe quedar seleccionado
            self._selecter.stop()
            self._update_selection()
        self._selecter.deleteLater()
        if self.band is not None:
            self.band.hide()
            self.band.deleteLater()
        if self.rows:  # el teclado sigue desde lo último que se tocó
            self.view.selectionModel().setCurrentIndex(self.view.model().index(self.rows[-1], 0), QItemSelectionModel.SelectionFlag.NoUpdate)


class DiskMeter(QWidget):
    """Barra de ocupación del disco. Se vuelve roja con poco espacio libre."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(56, 12)
        self._used = 0.0
        self.has_data = False
        self.hide()

    def set_usage(self, free: int | None, total: int | None) -> None:
        self.has_data = bool(total)
        self.setVisible(self.has_data and self.parentWidget().width() >= 280)
        self._used = 1 - (free or 0) / total if total else 0.0
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(t.border))
        painter.fillRect(0, 0, int(self.width() * self._used), self.height(),
                         QColor(t.meter_low if self._used >= 0.9 else t.meter_ok))


class StatusRow(QFrame):
    """Estado del panel: carpetas y archivos seleccionados/total, bytes y espacio libre."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusRow")
        self.folder_icon, self.file_icon = QLabel(), QLabel()
        self.dirs, self.files, self.size, self.message, self.free = QLabel(), QLabel(), QLabel(), QLabel(), QLabel()
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        self.chip = QLabel()
        self.chip.setObjectName("chip")
        self.chip.hide()
        self.meter = DiskMeter()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 1, 6, 1)
        row.setSpacing(4)
        for widget in (self.folder_icon, self.dirs):
            row.addWidget(widget)
        row.addSpacing(6)
        for widget in (self.file_icon, self.files):
            row.addWidget(widget)
        row.addSpacing(10)
        row.addWidget(self.size)
        row.addSpacing(10)
        row.addWidget(self.chip)
        row.addWidget(self.message, 1)
        row.addWidget(self.notice, 3)
        row.addWidget(self.free)
        row.addWidget(self.meter)
        self.setMinimumWidth(80)  # lo que no cabe se oculta por prioridad, sin ensanchar la ventana
        self.apply_icons()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit(event.size().width())

    def _fit(self, width: int) -> None:
        self.free.setVisible(width >= 340)
        self.meter.setVisible(width >= 280 and self.meter.has_data)
        noticing = bool(self.notice.text())
        for widget in (self.folder_icon, self.dirs, self.file_icon, self.files, self.message):
            widget.setVisible(not noticing)
        self.size.setVisible(width >= 220 and not noticing)
        self.chip.setMaximumWidth(16777215 if width >= 440 and not noticing else 0)

    def apply_icons(self) -> None:
        self.folder_icon.setPixmap(pixmap("folder", 14))
        self.file_icon.setPixmap(pixmap("file-kind", 14))

    def text(self) -> str:
        return " ".join(w.text() for w in (self.dirs, self.files, self.size, self.message, self.free) if w.text())

    def set_notice(self, text: str) -> None:
        """Un aviso pasajero del programa. Ocupa el sitio de los contadores, sin mover nada de la ventana."""
        self.notice.setText(text)
        self.notice.setToolTip(text)
        self.notice.setVisible(bool(text))
        self._fit(self.width())

    def show_message(self, text: str) -> None:
        self.dirs.clear()
        self.files.clear()
        self.size.clear()
        self.chip.hide()
        self.message.setText(text)


class FileView(QWidget):
    """Una carpeta abierta. Conserva historial, selección y desplazamiento al recargar."""

    activated = Signal(object)
    pathChanged = Signal(object)
    selectionChanged = Signal(object)
    openFile = Signal(object, object)   # vista, Entry
    fileMenu = Signal(object, object)    # vista, QPoint global: clic derecho sobre un archivo o carpeta
    folderMenu = Signal(object, object)  # vista, QPoint global: clic derecho fuera de cualquier nombre
    filesDropped = Signal(object, list, object, bool)  # vista, rutas, destino|None, mover
    filesDroppedMenu = Signal(object, list, object, bool)  # igual, soltando con el botón derecho: se pregunta qué hacer
    loadFailed = Signal(object, str)
    commandRequested = Signal(object, str)
    favoritesMenuRequested = Signal(object, object)
    folderOpened = Signal(object, str)
    renamed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = FileModel(self)
        self.path = ""
        self.tab_name = ""  # nombre elegido para la pestaña; vacío, el de la carpeta
        self.show_hidden = False
        self.flat = False
        self.view_mode = "details"
        self._history: list[str] = []
        self._history_pos = -1
        self._saved_selection: list[str] = []
        self._saved_current = ""
        self._saved_scroll = (0, 0)
        self._focus_after_load: str | None = None
        self.last_selection: list[str] = []
        self.grid_lines = False
        self.previous_path = ""
        self.hover_enabled = True
        self.hover_card = HoverCard(self)
        self._computer_columns = False
        self._had_groups = False
        self._hover_row = -1
        self._lasso: Lasso | None = None
        self._role_to_show = False
        self._mode_before_results = ""
        self.setProperty("role", "single")
        self.setAcceptDrops(True)
        self._panel_decider = DropDecider()

        self.pane_bar = PaneBar()
        self.pane_bar.setObjectName("paneBar")
        self.pane_bar.setMinimumHeight(24)
        self.pane_bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.pane_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pane_bar.customContextMenuRequested.connect(self._pane_bar_menu)
        self.history_limit = 0  # 0: sin límite
        self._dual_tools_visible = True
        self._removed_tools: set[str] = set()
        self._tool_buttons: list[tuple[QToolButton, str]] = []
        bar = QHBoxLayout(self.pane_bar)
        bar.setContentsMargins(3, 0, 3, 0)
        bar.setSpacing(1)
        for icon, tip, command in (("goback", tr("Atrás"), "Go BACK"), ("goforward", tr("Adelante"), "Go FORWARD"),
                                   ("goup", tr("Subir"), "Go UP"), ("refresh", tr("Actualizar"), "Go REFRESH"),
                                   ("home", tr("Inicio"), "Go HOME")):
            bar.addWidget(self._tool(icon, tip, command))
        self.favorites_button = self._tool_button("favorites", tr("Favoritos"))
        self.favorites_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._favorites_menu = QMenu(self)
        self._favorites_menu.aboutToShow.connect(lambda: self.favoritesMenuRequested.emit(self, self._favorites_menu))
        self.favorites_button.setMenu(self._favorites_menu)
        bar.addWidget(self.favorites_button)
        self.folder_label = QLabel()
        self.folder_label.setContentsMargins(4, 0, 2, 0)
        self.crumb_strip = QFrame()
        self.crumb_strip.setObjectName("crumbStrip")
        strip_row = QHBoxLayout(self.crumb_strip)
        strip_row.setContentsMargins(0, 0, 0, 0)
        strip_row.setSpacing(1)
        strip_row.addWidget(self.folder_label)
        self.breadcrumb = Breadcrumb()
        self.breadcrumb.pathEntered.connect(lambda _path: self.activated.emit(self))
        self.breadcrumb.pathEntered.connect(self.navigate)
        strip_row.addWidget(self.breadcrumb, 1)
        bar.addWidget(self.crumb_strip, 1)
        self.history_button = QToolButton()
        self.history_button.setToolTip(tr("Carpetas recientes"))
        self.history_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._history_menu = QMenu(self)
        self._history_menu.aboutToShow.connect(lambda: self.activated.emit(self))
        self._history_menu.aboutToShow.connect(self._fill_history_menu)
        self.history_button.setMenu(self._history_menu)
        self.folder_list = FolderList(self._history_menu, self.crumb_strip, self.breadcrumb, lambda: self.path)
        self.breadcrumb.fieldShown.connect(self._path_field_shown)
        strip_row.addWidget(self.history_button)
        self._tool_buttons.append((self.history_button, "menu-arrow"))
        strip_row.addWidget(self._tool("copypath", tr("Copiar la ruta"), "Clipboard COPYPATH"))
        self.git_label = QLabel()
        self.git_label.setObjectName("gitBranch")
        self.git_label.hide()
        self.git_branch = ""
        bar.addWidget(self.git_label)
        bar.addWidget(self._tool("treepane", tr("Mostrar u ocultar el árbol de carpetas de este panel"), "Set PANETREE=Toggle"))
        bar.addWidget(self._tool("terminalpane", tr("Mostrar u ocultar la terminal de este panel"), "Set TERMINAL=Toggle"))
        bar.addWidget(self._tool("terminalonly", tr("Mostrar solo la terminal en este panel"), "Set TERMINALPANE=On"))
        self.send_button = self._tool("sendright", tr("Mostrar esta carpeta en el otro visualizador de archivos"), "Go DEST")
        bar.addWidget(self.send_button)
        for icon, tip, command in (("swap", tr("Intercambiar paneles"), "Go SWAP"), ("close", tr("Cerrar este panel"), "Set PANE=Close")):
            bar.addWidget(self._tool(icon, tip, command))

        self.details = DetailsView()
        self.thumbs = ItemsView()
        self.details.setModel(self.model)
        self.thumbs.setModel(self.model)
        self.thumbs.setModelColumn(0)
        self.thumbs.setSelectionModel(self.details.selectionModel())
        self.details.drop_handler = self._dropped
        self.thumbs.drop_handler = self._dropped
        self._set_default_columns()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.details)
        self.stack.addWidget(self.thumbs)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("Filtrar (ej.: *.jpg o texto)"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.model.set_filter)
        self.filter_edit.textChanged.connect(lambda _t: self._update_status())
        self.filter_edit.hide()
        self.filter_edit.installEventFilter(self)

        self.find_bar = FindBar()
        self.find_bar.hide()
        self.find_bar.edit.textChanged.connect(self._find_changed)
        self.find_bar.edit.installEventFilter(self)
        self._find_matches: list[int] = []
        self._find_path = ""

        self.status = StatusRow()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar_frame = QFrame()
        bar_frame.setObjectName("paneBarFrame")
        bar_frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar_frame_layout = QVBoxLayout(bar_frame)
        bar_frame_layout.setContentsMargins(0, 0, 0, 0)
        bar_frame_layout.addWidget(self.pane_bar)
        layout.addWidget(bar_frame)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.find_bar)
        layout.addWidget(self.status)
        self.pane_bar.installEventFilter(self)  # un clic en la barra activa el panel
        self.breadcrumb.installEventFilter(self)  # y también en la ruta, que no le pasa el clic a la barra
        self.apply_icons()

        for view in (self.details, self.thumbs):
            # Solo `doubleClicked`: `activated` también se emite en el doble clic y abriría todo dos veces.
            # Intro se atiende en eventFilter.
            view.doubleClicked.connect(self._activate_index)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, v=view: self._context_menu(v, pos))
            view.installEventFilter(self)
            view.viewport().installEventFilter(self)
        self.details.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.loadFinished.connect(self._load_finished)
        self.model.renameFailed.connect(lambda msg: self.loadFailed.emit(self, msg))
        self.model.renamed.connect(self.renamed)
        self.model.sizesChanged.connect(self._update_status)
        self.model.modelAboutToBeReset.connect(self._save_view_state)
        self.model.modelReset.connect(self._restore_view_state)
        self.model.modelReset.connect(self._apply_group_rows)
        self.model.layoutChanged.connect(self._apply_group_rows)
        self.details.verticalScrollBar().valueChanged.connect(lambda _v: self.details.set_hover_row(-1))

        self._watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(350)
        self._refresh_timer.timeout.connect(self.refresh)
        self._watcher.directoryChanged.connect(lambda _p: self._refresh_timer.start())

    def build_pane_bar_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(themed("prefs"), tr("Personalizar…"), lambda: QTimer.singleShot(0, lambda: self.commandRequested.emit(self, "Prefs BARS=pane")))
        return menu

    def _pane_bar_menu(self, position: QPoint) -> None:
        self.build_pane_bar_menu().exec(self.pane_bar.mapToGlobal(position))

    def _tool_button(self, icon: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        self._tool_buttons.append((button, icon))
        return button

    def _tool(self, icon: str, tip: str, command: str) -> QToolButton:
        button = self._tool_button(icon, tip)
        button.clicked.connect(lambda _=False, c=command: self.commandRequested.emit(self, c))
        return button

    def _bar_icon(self, icon: str):
        """Los iconos de la barra se dibujan para el color que tiene la barra de este panel."""
        current, role = theme.current(), self.property("role")
        return themed(icon, theme.bar_icon_color(current, role), theme.bar_icon_disabled(current, role))

    def _path_field_shown(self, shown: bool) -> None:
        strip = self.crumb_strip
        if strip.property("fieldShown") == shown:
            return
        strip.setProperty("fieldShown", shown)
        strip.style().unpolish(strip)
        strip.style().polish(strip)
        strip.update()
        self._apply_bar_icons()  # el fondo ya no es el mismo: en un color fuerte los iconos son blancos, y sobre el campo no se verían

    def _apply_bar_icons(self) -> None:
        current, role = theme.current(), self.property("role")
        for button, icon in self._tool_buttons:
            if button.parentWidget() is self.crumb_strip:  # va sobre el tramo de la ruta, no sobre el color de la barra
                button.setIcon(themed(icon, current.icon if self.crumb_strip.property("fieldShown") else theme.crumb_text_color(current, role)))
            else:
                button.setIcon(self._bar_icon(icon))

    def apply_icons(self) -> None:
        self._apply_bar_icons()
        self.folder_label.setPixmap(pixmap("folder", 16))
        self.status.apply_icons()
        self.find_bar.apply_icons()

    def _set_default_columns(self) -> None:
        widths = {"size": 72, "type": 92, "modified": 112, "attrs": 44, "free": 92, "used": 150, "filesystem": 130, "location": 260}
        for column, (key, _) in enumerate(COLUMNS):
            if key in widths:
                self.details.setColumnWidth(column, widths[key])
        # El nombre absorbe el espacio libre: así un panel estrecho no recorta las demás columnas.
        self.details.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.details.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self._apply_columns()

    # --- navegación ---
    def navigate(self, path: str, add_history: bool = True, focus: str | None = None) -> None:
        path = os.path.normpath(path)
        if add_history:
            if not self._history or self._history[self._history_pos] != path:
                del self._history[self._history_pos + 1:]
                self._history.append(path)
                if self.history_limit:
                    del self._history[:-self.history_limit]
                self._history_pos = len(self._history) - 1
        changed = path != self.path
        if is_results(path) and not is_results(self.path):  # sin la columna de la ubicación, los resultados no dirían dónde está cada uno
            self._mode_before_results = self.view_mode
            self.set_view_mode("details")
        elif is_results(self.path) and not is_results(path) and self._mode_before_results:
            self.set_view_mode(self._mode_before_results)
            self._mode_before_results = ""
        self.previous_path = self.path
        self.path = path
        self.stack.setCurrentWidget(self.current_view)
        self._focus_after_load = focus
        if changed and self.filter_edit.text():
            self.filter_edit.clear()
        self.breadcrumb.set_path(path)
        self._apply_columns()
        self._watch(path)
        self.model.load(path, self.show_hidden, self.flat)
        self.status.show_message(tr("Cargando…"))
        self.pathChanged.emit(self)

    def _watch(self, path: str) -> None:
        current = self._watcher.directories()
        if current:
            self._watcher.removePaths(current)
        if archive.split_archive_path(path) is None and os.path.isdir(path):
            self._watcher.addPath(path)

    def refresh(self) -> None:
        self.model.load(self.path, self.show_hidden, self.flat)

    def can_go_back(self) -> bool:
        return self._history_pos > 0

    def can_go_forward(self) -> bool:
        return self._history_pos < len(self._history) - 1

    def back(self) -> None:
        if self.can_go_back():
            previous = self.path
            self._history_pos -= 1
            self.navigate(self._history[self._history_pos], add_history=False, focus=previous)

    def forward(self) -> None:
        if self.can_go_forward():
            self._history_pos += 1
            self.navigate(self._history[self._history_pos], add_history=False)

    def up(self) -> None:
        parent = self.parent_path()
        if parent:
            self.navigate(parent, focus=self.path)

    def parent_path(self) -> str:
        if self.path == COMPUTER_PATH or is_results(self.path):
            return ""
        if self.path == root_of(self.path):
            return COMPUTER_PATH if has_computer_view() else ""
        parent = os.path.dirname(self.path.rstrip("/\\"))
        return parent if parent != self.path else ""

    def go_root(self) -> None:
        if self.path != COMPUTER_PATH and not is_results(self.path):
            self.navigate(root_of(self.path))

    def go_home(self) -> None:
        self.navigate(home_dir())

    # --- selección ---
    @property
    def current_view(self) -> QAbstractItemView:
        return self.details if self._uses_details() else self.thumbs

    def selected_entries(self):
        # No `selectedRows()`: exige todas las columnas, y un clic en miniaturas o lista solo marca la primera.
        rows = sorted({i.row() for i in self.details.selectionModel().selectedIndexes()})
        return [e for e in (self.model.entry(r) for r in rows) if e is not None]

    def selected_paths(self) -> list[str]:
        """Rutas para operar. Lo que cuelga de una carpeta también seleccionada se omite:
        con carpetas desplegadas, de otro modo se copiaría dos veces."""
        entries = self.selected_entries()
        folders = tuple(e.path.rstrip("/\\") + os.sep for e in entries if e.is_dir)
        return [e.path for e in entries if not e.path.startswith(folders)]

    def current_entry(self):
        index = self.current_view.currentIndex()
        return self.model.entry(index.row()) if index.isValid() else None

    def select_paths(self, paths: list[str]) -> None:
        wanted = {os.path.normpath(p) for p in paths}
        selection = QItemSelection()
        for row, entry in self.model.entry_rows():
            if entry.path in wanted:
                selection.select(self.model.index(row, 0), self.model.index(row, len(COLUMNS) - 1))
        self.details.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def remember_selection(self) -> None:
        selected = [e.path for e in self.selected_entries()]
        if selected:
            self.last_selection = selected

    def select_all(self) -> None:
        self.details.selectAll()

    def select_none(self) -> None:
        self.details.selectionModel().clearSelection()

    def select_invert(self) -> None:
        current = {e.path for e in self.selected_entries()}
        self.select_paths([e.path for e in self.model.entries if e.path not in current])

    def select_glob(self, pattern: str) -> None:
        pattern = pattern.casefold()
        self.select_paths([e.path for e in self.model.entries if fnmatch.fnmatchcase(e.name.casefold(), pattern)])

    def select_compare(self, other: "FileView", present: bool) -> None:
        """Selecciona lo que está (o no está) en el otro panel, por nombre."""
        names = {e.name.casefold() for e in other.model.entries}
        self.select_paths([e.path for e in self.model.entries if (e.name.casefold() in names) == present])

    # --- modos ---
    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        self.model.show_thumbnails = mode in ("thumbnails", "filmstrip", "tiles")
        if mode != "details":
            self.thumbs.configure(mode)
        self.stack.setCurrentWidget(self.current_view)

    def _uses_details(self) -> bool:
        """«Este equipo» siempre va en detalles: sus columnas de capacidad y uso no caben en otra vista."""
        return self.view_mode == "details" or self.path == COMPUTER_PATH

    def copy_view_settings(self, other: "FileView") -> None:
        """Para duplicar una pestaña: misma vista, mismo orden y mismo historial."""
        self.set_view_mode(other.view_mode)
        self.set_show_hidden(other.show_hidden)
        self.set_flat(other.flat)
        header = other.details.horizontalHeader()
        self.details.horizontalHeader().setSortIndicator(header.sortIndicatorSection(), header.sortIndicatorOrder())
        self._history = list(other._history)
        self._history_pos = other._history_pos

    def set_show_hidden(self, value: bool) -> None:
        if value != self.show_hidden:
            self.show_hidden = value
            self.refresh()

    def set_flat(self, value: bool) -> None:
        if value != self.flat and self.path != COMPUTER_PATH and not is_results(self.path) and archive.split_archive_path(self.path) is None:
            self.flat = value
            self.refresh()

    def show_filter(self) -> None:
        self.filter_edit.show()
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    def begin_rename(self) -> None:
        index = self.current_view.currentIndex()
        if index.isValid():
            self.current_view.edit(self.model.index(index.row(), 0))

    def set_role(self, role: str) -> None:
        if self.property("role") == role:
            return
        self.setProperty("role", role)
        self.pane_bar.role = role
        self.details.pane_role = self.thumbs.pane_role = role
        self._role_to_show = True
        if self.isVisible():  # con muchas pestañas sería mucho trabajo para lo que no se ve: se hace al mostrarlas
            self._show_role()

    def _show_role(self) -> None:
        self._role_to_show = False
        for widget in [self, *self.findChildren(QWidget)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self._apply_bar_icons()

    def showEvent(self, event) -> None:
        if self._role_to_show:
            self._show_role()
        super().showEvent(event)

    def set_check_mode(self, enabled: bool) -> None:
        self.model.set_check_mode(enabled)

    def set_expandable(self, enabled: bool) -> None:
        self.model.expandable = enabled
        self.refresh()

    def set_level_colors(self, enabled: bool) -> None:
        self.details.level_colors = self.thumbs.level_colors = enabled
        self.details.viewport().update()
        self.thumbs.viewport().update()

    def set_full_row(self, enabled: bool) -> None:
        self.details.full_row = self.thumbs.full_row = enabled
        self.details.viewport().update()
        self.thumbs.viewport().update()

    def set_grid_lines(self, enabled: bool) -> None:
        self.grid_lines = enabled
        self.details.setProperty("grid", "on" if enabled else "off")
        self.details.style().unpolish(self.details)
        self.details.style().polish(self.details)
        self.details.update()

    def set_dual_tools_visible(self, visible: bool) -> None:
        self._dual_tools_visible = visible
        self._fit_pane_bar()

    def _fit_pane_bar(self) -> None:
        """Muestra los botones que caben junto a la ruta, para que la barra no fije el ancho mínimo del panel.
        Se ocultan primero los que tienen otro camino rápido; Subir es el último."""
        tools = self._bar_tools()
        dual_only = (tools["close"], tools["swap"], self.send_button, tools["terminalonly"])
        keys = ("terminalonly", "copypath", "favorites", "home", "refresh", "menu-arrow", "close", "swap", "send", "gitbranch", "treepane", "terminalpane", "goforward", "goback", "goup")
        order = [tools[key] for key in keys]
        wanted = [tools[key] for key in keys if key not in self._removed_tools and (self._dual_tools_visible or tools[key] not in dual_only) and (key != "gitbranch" or self.git_branch)]
        layout = self.pane_bar.layout()
        margins = layout.contentsMargins()
        outlines = 4  # los contornos de la barra y de la ruta
        room = self.width() - outlines - margins.left() - margins.right() - self.folder_label.sizeHint().width() - MIN_BREADCRUMB_WIDTH
        for button in order:
            button.setVisible(False)
        needed = [button.sizeHint().width() + layout.spacing() for button in wanted]
        for position, button in enumerate(wanted):
            button.setVisible(sum(needed[position:]) <= room)

    def _bar_tools(self) -> dict:
        tools = {icon: button for button, icon in self._tool_buttons}
        tools["send"] = self.send_button
        tools["gitbranch"] = self.git_label
        return tools

    def set_git_branch(self, branch: str | None) -> None:
        """La rama de la carpeta que se muestra. Vacía si no es un repositorio."""
        if (branch or "") != self.git_branch:
            self.git_branch = branch or ""
            self.git_label.setText(self.git_label.fontMetrics().elidedText(f"⎇ {branch}", Qt.TextElideMode.ElideMiddle, GIT_BRANCH_MAX_WIDTH) if branch else "")
            self.git_label.setToolTip(tr("Rama de git: {branch}", branch=branch) if branch else "")
            self._fit_pane_bar()

    def set_removed_tools(self, keys: set[str]) -> None:
        """Los botones que no se quieren en la barra, según las preferencias."""
        self._removed_tools = keys
        self._fit_pane_bar()

    def set_tool_order(self, keys: list[str]) -> None:
        """Coloca los botones y la ruta en el orden elegido. La ruta se lleva el espacio que sobre."""
        tools = self._bar_tools()
        layout = self.pane_bar.layout()
        while layout.count():
            layout.takeAt(0)
        strip_row = self.crumb_strip.layout()
        while strip_row.count():
            strip_row.takeAt(0)
        beside_path = False
        for key in keys:
            if key == PATH:
                strip_row.addWidget(self.folder_label)
                strip_row.addWidget(self.breadcrumb, 1)
                layout.addWidget(self.crumb_strip, 1)
                beside_path = True
            elif beside_path and key in PATH_TOOLS:
                strip_row.addWidget(tools[key])  # justo tras la ruta, forman parte de su tramo
            else:
                beside_path = False
                if key in tools:
                    layout.addWidget(tools[key])
        self._apply_bar_icons()

    def set_send_direction(self, icon: str) -> None:
        """La flecha del botón apunta hacia donde está el otro panel."""
        self._tool_buttons = [(button, icon if button is self.send_button else name) for button, name in self._tool_buttons]
        self.send_button.setIcon(self._bar_icon(icon))

    def mark_path(self, path: str) -> None:
        """Selecciona ese archivo y lo muestra en la lista. Si aún no está, al terminar la carga en curso."""
        row = self.model.row_of(path)
        if row < 0:
            if os.path.dirname(path) == self.path:
                self._focus_after_load = path
            return
        self.select_paths([path])
        index = self.model.index(row, 0)
        self.details.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.current_view.scrollTo(index)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_columns(event.size().width())
        self._fit_pane_bar()

    def _apply_columns(self, width: int | None = None) -> None:
        """Columnas según lo que se muestra. En un panel estrecho, el nombre importa más que el resto."""
        width = self.width() if width is None else width
        computer = self.path == COMPUTER_PATH
        if computer:
            hidden = {"modified", "attrs", "location"}
            # Se ocultan en este orden hasta que el nombre de la unidad quepa entero. Con anchos fijos,
            # no con columnWidth(): una columna oculta mide 0 y la siguiente pasada la volvería a mostrar.
            used = sum(DRIVE_COLUMN_WIDTHS.values()) + SIZE_COLUMN_WIDTH
            for key, column_width in DRIVE_COLUMN_WIDTHS.items():
                if width - used >= MIN_DRIVE_NAME_WIDTH:
                    break
                hidden.add(key)
                used -= column_width
        else:
            hidden = {"free", "used", "filesystem"} | ({"attrs", "type"} if is_results(self.path) else {"location"})
            for limit, key in ((440, "attrs"), (360, "type")):
                if width < limit:
                    hidden.add(key)
        if computer != self._computer_columns:
            self._computer_columns = computer
            self.details.setColumnWidth(COLUMN_INDEX["type"], 190 if computer else 92)
        for key, index in COLUMN_INDEX.items():
            self.details.setColumnHidden(index, key in hidden)

    def _apply_group_rows(self) -> None:
        """Los títulos de grupo ocupan toda la fila en detalles y no aparecen en las demás vistas."""
        groups = self.model.group_rows()
        if not groups and not self._had_groups:
            return
        self._had_groups = bool(groups)
        self.details.clearSpans()
        height = self.details.verticalHeader().defaultSectionSize()
        for row in range(self.model.rowCount()):
            self.thumbs.setRowHidden(row, row in groups)
            self.details.setRowHeight(row, height + GROUP_ROW_EXTRA if row in groups else height)
        for row in groups:
            self.details.setSpan(row, 0, 1, len(COLUMNS))

    # --- eventos internos ---
    def _save_view_state(self) -> None:
        self._saved_selection = [e.path for e in self.selected_entries()]
        current = self.current_entry()
        self._saved_current = current.path if current else ""
        view = self.current_view
        self._saved_scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())

    def _restore_view_state(self) -> None:
        self.details.hover_row = -1
        view = self.current_view
        view.setAutoScroll(False)  # si no, Qt saltaría hasta el archivo actual al restaurarlo
        if self._saved_selection:
            self.select_paths(self._saved_selection)
        if self._saved_current:
            row = self.model.row_of(self._saved_current)
            if row >= 0:
                self.details.selectionModel().setCurrentIndex(self.model.index(row, 0),
                                                              QItemSelectionModel.SelectionFlag.NoUpdate)
        view.setAutoScroll(True)
        across, down = self._saved_scroll
        if across and view is self.thumbs:
            view.finish_layout()  # la barra no admite el valor hasta conocer el ancho total
        view.horizontalScrollBar().setValue(across)
        view.verticalScrollBar().setValue(down)

    def _load_finished(self, error: str) -> None:
        if error:
            self.status.show_message(f"⚠ {error}")
            self.loadFailed.emit(self, error)
            return
        self._find_after_load()
        if self._focus_after_load:
            focus = os.path.normpath(self._focus_after_load)
            self._focus_after_load = None
            row = self.model.row_of(focus)
            if row >= 0:
                index = self.model.index(row, 0)
                self.details.selectionModel().setCurrentIndex(
                    index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
                self.current_view.scrollTo(index)
        self._update_status()

    def _selection_changed(self, *_args) -> None:
        self._update_status()
        self.selectionChanged.emit(self)

    def _update_status(self) -> None:
        entries = self.model.entries
        selected = self.selected_entries()
        folders = sum(1 for e in entries if e.is_dir)
        sel_folders = sum(1 for e in selected if e.is_dir)
        total = sum(e.effective_size for e in entries)
        sel_size = sum(e.effective_size for e in selected)
        status = self.status
        status.dirs.setText(f"{sel_folders} / {folders}")
        status.files.setText(f"{len(selected) - sel_folders} / {len(entries) - folders}")
        status.size.setText(f"{format_size(sel_size)} / {format_size(total)}")
        extensions = {e.extension for e in selected if not e.is_dir}
        count = len(selected) - sel_folders
        if len(extensions) == 1 and next(iter(extensions)):
            status.chip.setText(f"{count} · {next(iter(extensions))}")
            status.chip.show()
        else:
            status.chip.hide()
        status.message.setText("filtrado" if self.model.filter_text or self.model.content_types else "")
        free = self.model.free_space
        status.free.setText(tr("{value} libres", value=format_size(free)) if free is not None else "")
        status.meter.set_usage(free, self.model.total_space)

    def _activate_index(self, index: QModelIndex) -> None:
        entry = self.model.entry(index.row())
        if entry is not None:
            self.open_entry(entry)

    def open_entry(self, entry) -> None:
        if entry.is_dir:
            self.navigate(entry.path)
            self.folderOpened.emit(self, entry.path)
        else:
            self.openFile.emit(self, entry)

    def hit_index(self, view: QAbstractItemView, position: QPoint) -> QModelIndex:
        """El archivo bajo ese punto. Solo cuenta su nombre, no el resto de la fila o de la celda."""
        return view.name_index_at(position)

    def _context_menu(self, view: QAbstractItemView, position: QPoint) -> None:
        index = self.hit_index(view, position)
        target = view.viewport().mapToGlobal(position)
        if not index.isValid():
            self.folderMenu.emit(self, target)
            return
        entry = self.model.entry(index.row())
        if entry is not None and entry.path not in {e.path for e in self.selected_entries()}:
            self.select_paths([entry.path])
        self.fileMenu.emit(self, target)

    def _keyboard_menu(self) -> None:
        """Tecla de menú o Mayús+F10: menú de los seleccionados o, si no hay ninguno, de la carpeta."""
        view = self.current_view
        current = view.currentIndex()
        if self.selected_entries() and current.isValid():
            area = view.name_rect(current)
            self.fileMenu.emit(self, view.viewport().mapToGlobal(area.center()))
        else:
            self.folderMenu.emit(self, view.viewport().mapToGlobal(QPoint(8, 8)))

    def dragEnterEvent(self, event) -> None:
        """Lo que no sea la lista, como la barra del panel o su fila de estado: soltar ahí es soltar en su carpeta."""
        if self._panel_decider.enter(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._panel_decider.paths:
            self._panel_decider.begin(event)
        show_operation(event, self._panel_decider.operation(event, self.path))

    def dropEvent(self, event) -> None:
        self._panel_decider.begin(event)
        operation = self._panel_decider.operation(event, self.path)
        ask = self._panel_decider.take_right_button()
        if operation is None:
            event.ignore()
            return
        accept_drop(event)
        paths = self._panel_decider.paths
        QTimer.singleShot(0, lambda: self._dropped(paths, None, operation == "move", ask))

    def _dropped(self, paths: list[str], target: str | None, move: bool, ask: bool = False) -> None:
        (self.filesDroppedMenu if ask else self.filesDropped).emit(self, paths, target, move)

    def _chevron_hit(self, view: QAbstractItemView, pos: QPoint) -> bool:
        """Si el clic cae en la flecha de una carpeta o de un comprimido, lo despliega o lo pliega."""
        if view is self.thumbs and not view.list_mode:
            return False
        index = view.indexAt(pos)
        if not index.isValid() or index.column() != 0:
            return False
        entry = self.model.entry(index.row())
        if entry is None or not self.model.can_unfold(entry):
            return False
        left = view.visualRect(index).left() + entry.depth * INDENT
        if not left <= pos.x() < left + CHEVRON:
            return False
        self.model.set_expanded(entry, not self.model.is_expanded(entry.path))
        return True

    def _handle_hover(self, watched, event) -> bool:
        """Devuelve True si el evento era la petición de tooltip y ya se atendió."""
        kind = event.type()
        viewports = (self.details.viewport(), self.thumbs.viewport())
        if kind == QEvent.Type.ToolTip and self._lasso is not None:
            return True  # mientras se selecciona, una tarjeta encima estorbaría
        if kind == QEvent.Type.ToolTip and watched in viewports and self.hover_enabled:
            view = self.details if watched is viewports[0] else self.thumbs
            index = self.hit_index(view, event.pos())
            entry = self.model.entry(index.row()) if index.isValid() else None
            if entry is None:
                self.hover_card.dismiss()
            else:
                self._hover_row = index.row()
                self.hover_card.show_hidden = self.show_hidden
                self.hover_card.show_for(entry, event.globalPos())
            return True
        if not self.hover_card.current_path:
            return False
        if kind == QEvent.Type.MouseMove and watched in viewports:
            view = self.details if watched is viewports[0] else self.thumbs
            if self.hit_index(view, event.position().toPoint()).row() != self._hover_row:
                self.hover_card.dismiss()
        elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.KeyPress,
                      QEvent.Type.Hide):
            self.hover_card.dismiss()
        return False

    # --- buscar al escribir ---
    def begin_find(self, text: str = "") -> None:
        bar = self.find_bar
        if bar.isHidden():
            bar.edit.blockSignals(True)
            bar.edit.clear()
            bar.edit.blockSignals(False)
            bar.show()
        self._find_path = self.path
        bar.edit.setFocus()
        bar.edit.insert(text)

    def end_find(self, focus_list: bool = True) -> None:
        bar = self.find_bar
        if bar.isHidden():
            return
        had_focus = bar.edit.hasFocus()
        bar.hide()
        bar.pin.setChecked(False)
        self._set_find_text("")
        self._find_matches = []
        if focus_list or had_focus:
            self.focus_list()

    def _set_find_text(self, text: str) -> None:
        self.details.find_text = self.thumbs.find_text = text.lower()
        self.current_view.viewport().update()

    def _update_find_count(self) -> None:
        bar = self.find_bar
        row = self.current_view.currentIndex().row()
        position = self._find_matches.index(row) + 1 if row in self._find_matches else 0
        bar.count.setText(f"{position} / {len(self._find_matches)}")
        no_match = bool(bar.edit.text()) and not self._find_matches
        if bar.property("noMatch") != no_match:
            bar.setProperty("noMatch", no_match)
            bar.edit.style().unpolish(bar.edit)
            bar.edit.style().polish(bar.edit)

    def _find_changed(self, text: str, move: bool = True) -> None:
        self._set_find_text(text)
        needle = text.lower()
        model = self.model
        self._find_matches = []
        if needle:
            for row in range(model.rowCount()):
                entry = model.entry(row)
                if entry is not None and needle in entry.display_name.lower():
                    self._find_matches.append(row)
        current = self.current_view.currentIndex().row()
        if move and self._find_matches and current not in self._find_matches:
            self._go_to_row(next((row for row in self._find_matches if row > current), self._find_matches[0]))
        self._update_find_count()

    def _go_to_row(self, row: int) -> None:
        index = self.model.index(row, 0)
        self.details.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.current_view.scrollTo(index)

    def find_step(self, step: int) -> None:
        """Salta a otra coincidencia, y al llegar al final vuelve a empezar."""
        matches = self._find_matches
        if not matches:
            return
        current = self.current_view.currentIndex().row()
        if current in matches:
            target = matches[(matches.index(current) + step) % len(matches)]
        else:
            later = [row for row in matches if (row > current) == (step > 0) and row != current]
            target = (later[0] if step > 0 else later[-1]) if later else matches[0 if step > 0 else -1]
        self._go_to_row(target)
        self._update_find_count()

    def _find_after_load(self) -> None:
        """Al cambiar de carpeta la búsqueda se cierra, salvo si se pidió mantenerla. Al actualizarse la misma, sigue."""
        if self.find_bar.isHidden():
            return
        if self.path != self._find_path and not self.find_bar.pin.isChecked():
            self.end_find(focus_list=False)
            return
        moved = self.path != self._find_path
        self._find_path = self.path
        self._find_changed(self.find_bar.edit.text(), move=moved)

    def _find_key(self, event) -> bool:
        key = event.key()
        back = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        steps = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1, Qt.Key.Key_PageUp: -FIND_PAGE, Qt.Key.Key_PageDown: FIND_PAGE, Qt.Key.Key_F3: -1 if back else 1}
        if key == Qt.Key.Key_Escape:
            self.end_find()
        elif key in steps:
            self.find_step(steps[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.current_view.currentIndex()
            opens = index.isValid() and index.row() in self._find_matches
            self.end_find()
            if opens:
                self._activate_index(index)
        else:
            return False
        return True

    def _starts_find(self, event) -> bool:
        """Una letra o un símbolo sin Ctrl ni Alt. Los dos a la vez son AltGr: con ella se escriben caracteres como @ o €."""
        text = event.text()
        if not text or not text.isprintable() or text == " ":
            return False
        modifiers = event.modifiers()
        ctrl, alt = bool(modifiers & Qt.KeyboardModifier.ControlModifier), bool(modifiers & Qt.KeyboardModifier.AltModifier)
        return ctrl == alt and not modifiers & Qt.KeyboardModifier.MetaModifier

    def eventFilter(self, watched, event) -> bool:
        if self._handle_hover(watched, event):
            return True
        kind = event.type()
        if watched is self.find_bar.edit:
            if kind == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_F3:
                event.accept()  # en la ventana, F3 es otra cosa; aquí es la siguiente coincidencia
                return True
            if kind == QEvent.Type.KeyPress:
                return self._find_key(event)
            if (kind == QEvent.Type.FocusOut and not self.find_bar.pin.isChecked()
                    and event.reason() in (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)):
                self.end_find(focus_list=False)
            return False
        view = self.details if watched is self.details.viewport() else (self.thumbs if watched is self.thumbs.viewport() else None)
        if view is not None:
            if kind == QEvent.Type.MouseMove:
                if self._lasso is not None:
                    if event.buttons() & Qt.MouseButton.LeftButton:
                        self._lasso.move(event.position().toPoint())
                    else:  # se soltó fuera de la ventana, o al cambiar a otra
                        self._end_lasso()
                    return True
                view.set_hover_row(view.name_index_at(event.position().toPoint()).row())
            elif kind == QEvent.Type.Leave:
                view.set_hover_row(-1)
            elif kind == QEvent.Type.MouseButtonRelease and self._lasso is not None and event.button() == Qt.MouseButton.LeftButton:
                self._end_lasso()
                return True
            elif (kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton
                    and not view.name_index_at(event.position().toPoint()).isValid()):
                return True  # fuera del nombre, un doble clic no abre nada
        if kind == QEvent.Type.FocusIn:
            if watched in (self.details, self.thumbs):
                self.activated.emit(self)
        elif kind == QEvent.Type.MouseButtonPress:
            self.activated.emit(self)
            if event.button() == Qt.MouseButton.BackButton:
                self.back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
                self.forward()
                return True
            if event.button() == Qt.MouseButton.RightButton:
                # Fuera de un nombre el menú es el de la carpeta: la selección no debe cambiar.
                if view is not None and not self.hit_index(view, event.position().toPoint()).isValid():
                    return True
            if view is not None and event.button() == Qt.MouseButton.LeftButton:
                position = event.position().toPoint()
                if self._chevron_hit(view, position):
                    return True
                if not view.name_index_at(position).isValid():
                    # Solo se selecciona pulsando el nombre. Fuera de él, se quita la selección (Ctrl y Mayús la conservan).
                    keep = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                    if not event.modifiers() & keep:
                        self.select_none()
                    self._end_lasso()
                    self._lasso = Lasso(view, position, event.modifiers())
                    view.setFocus()
                    return True
        elif kind == QEvent.Type.KeyPress and watched is self.filter_edit and event.key() == Qt.Key.Key_Escape:
            self.filter_edit.clear()
            self.filter_edit.hide()
            self.current_view.setFocus()
            return True
        elif (kind == QEvent.Type.KeyPress and watched in (self.details, self.thumbs) and self._starts_find(event)
              and self.current_view.state() != QAbstractItemView.State.EditingState):
            self.begin_find(event.text())
            return True
        elif kind == QEvent.Type.KeyPress and watched in (self.details, self.thumbs):
            shift_f10 = event.key() == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if event.key() == Qt.Key.Key_Menu or shift_f10:
                self._keyboard_menu()
                return True
            if event.key() == Qt.Key.Key_Backspace:
                self.up()
                return True
            if event.key() == Qt.Key.Key_Return and not self.current_view.state() == QAbstractItemView.State.EditingState:
                index = self.current_view.currentIndex()
                if index.isValid():
                    self._activate_index(index)
                return True
            if watched is self.details and event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Left):
                entry = self.current_entry()
                if entry is not None and self.model.can_unfold(entry):
                    wanted = event.key() == Qt.Key.Key_Right
                    if wanted != self.model.is_expanded(entry.path):
                        self.model.set_expanded(entry, wanted)
                        return True
        return super().eventFilter(watched, event)

    def _end_lasso(self) -> None:
        if self._lasso is not None:
            self._lasso.finish()
            self._lasso = None

    def focus_list(self) -> None:
        self.current_view.setFocus()

    def dispose(self) -> None:
        """Se llama antes de destruir la vista, para ignorar las cargas que sigan en curso."""
        self._refresh_timer.stop()
        self.hover_card.dismiss()
        self.model.ignore_pending()

    def _fill_history_menu(self) -> None:
        self._history_menu.clear()
        self.build_recent_menu(self._history_menu)

    def build_recent_menu(self, menu: QMenu) -> None:
        seen: set[str] = set()
        for index in range(len(self._history) - 1, -1, -1):
            path = self._history[index]
            if path not in seen:
                seen.add(path)
                action = menu.addAction(folder_icon(path), location_label(path))
                action.setData(path)
                action.triggered.connect(lambda _=False, i=index: self._jump_history(i))

    def build_history_menu(self, parent: QMenu, forward: bool) -> None:
        indices = range(self._history_pos + 1, len(self._history)) if forward else range(self._history_pos - 1, -1, -1)
        for index in indices:
            action = parent.addAction(self._history[index])
            action.triggered.connect(lambda _=False, i=index: self._jump_history(i))

    def _jump_history(self, index: int) -> None:
        self._history_pos = index
        self.navigate(self._history[index], add_history=False)
