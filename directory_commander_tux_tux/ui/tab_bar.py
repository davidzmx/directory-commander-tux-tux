"""Barra de pestañas: tamaños configurables, varias filas, texto horizontal en vertical y arrastre de archivos."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QToolTip

from ..core import tab_settings
from . import theme
from .drag_drop import DropDecider, accept_drop, dropped_paths, show_operation

ADD_BUTTON_WIDTH = 20
QT_EXTRA_TAB_MARGIN = 12  # margen que Qt suma a cada pestaña, además del icono y del relleno de la hoja de estilos
VERTICAL_SHAPES = {QTabBar.Shape.RoundedWest, QTabBar.Shape.RoundedEast, QTabBar.Shape.TriangularWest, QTabBar.Shape.TriangularEast}


def folders_in(mime) -> list[str]:
    return [path for path in dropped_paths(mime) if os.path.isdir(path)]


class PaneTabBar(QTabBar):
    def __init__(self, group) -> None:
        super().__init__(group)
        self.group = group
        self.settings = tab_settings.normalize(None)
        self._drag_index = -1
        self._drag_timer = QTimer(self, singleShot=True)
        self._drag_timer.timeout.connect(self._bring_dragged_to_front)
        self._row_hover = -1
        self._row_pressed = -1
        self._row_press_position = QPoint()
        self._row_skip = -1
        self._decider = DropDecider()
        self.setAcceptDrops(True)
        self._rows_key = None
        self._rows = None

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._rows_key = None
        super().changeEvent(event)

    def is_vertical(self) -> bool:
        return self.shape() in VERTICAL_SHAPES

    def rows_active(self) -> bool:
        return self.settings["multi_row"] and not self.is_vertical()

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        settings = self.settings
        if self.is_vertical():
            # Qt calcula el tamaño para texto girado, con el largo en la altura; aquí el texto va horizontal.
            width = hint.height() if settings["vertical_auto_width"] else settings["vertical_width"]
            return QSize(width, max(hint.width(), self.fontMetrics().height() + 8))
        width = hint.width() - QT_EXTRA_TAB_MARGIN
        if settings["same_size"]:
            if settings["same_size_mode"] == "fixed":
                width = settings["fixed_size"]
            else:
                width = max(super(PaneTabBar, self).tabSizeHint(i).width() for i in range(self.count())) - QT_EXTRA_TAB_MARGIN
        if settings["max_size_on"]:
            width = min(width, settings["max_size"])
        if settings["min_size_on"]:
            width = max(width, settings["min_size"])
        hint.setWidth(width)
        return hint

    def minimumTabSizeHint(self, index: int) -> QSize:
        """Qt comprime las pestañas si se pueden recortar. Solo debe hacerlo si se eligió reducirlas para que quepan."""
        return super().minimumTabSizeHint(index) if self.settings["shrink"] else self.tabSizeHint(index)

    # ---------------------------------------------------------------- varias filas
    # QTabBar solo sabe colocar las pestañas en una línea, así que aquí las coloca, dibuja y atiende esta clase.
    def _row_layout(self) -> tuple[list[QRect], QPoint, int]:
        """Rectángulo de cada pestaña, punto donde acaba la última fila y altura de una fila.
        Qt lo pregunta muchas veces seguidas al colocar la ventana, y medir cada pestaña cuesta: se recuerda mientras nada cambie."""
        # El ancho de una pestaña depende de su texto y de si lleva botón de cerrar, que puede ir solo en la activa.
        tabs = [(self.tabText(index), self.tabButton(index, self.ButtonPosition.RightSide) is not None) for index in range(self.count())]
        key = (self.group.width(), self.settings, self.iconSize(), tabs)
        if key != self._rows_key:
            self._rows_key, self._rows = key, self._measure_rows()
        return self._rows

    def _measure_rows(self) -> tuple[list[QRect], QPoint, int]:
        hints = [self.tabSizeHint(index) for index in range(self.count())]
        height = max((hint.height() for hint in hints), default=self.fontMetrics().height() + 8)
        available = self.group.width()
        rects = []
        x = y = 0
        for hint in hints:
            if x and x + hint.width() > available:
                x = 0
                y += height
            rects.append(QRect(x, y, hint.width(), height))
            x += hint.width()
        if self.settings["new_button"] and x and x + ADD_BUTTON_WIDTH + 5 > available:
            x = 0
            y += height  # el botón de nueva pestaña también necesita su sitio
        return rects, QPoint(x, y), height

    def row_end(self) -> QPoint:
        return self._row_layout()[1]

    def row_height(self) -> int:
        return self._row_layout()[2]

    def row_count(self) -> int:
        _rects, end, height = self._row_layout()
        return end.y() // height + 1

    def rect_of(self, index: int) -> QRect:
        return self._row_layout()[0][index] if self.rows_active() else self.tabRect(index)

    def index_at(self, position: QPoint) -> int:
        if not self.rows_active():
            return self.tabAt(position)
        for index, rect in enumerate(self._row_layout()[0]):
            if rect.contains(position):
                return index
        return -1

    def sizeHint(self) -> QSize:
        if not self.rows_active():
            return super().sizeHint()
        _rects, end, height = self._row_layout()
        return QSize(self.group.width(), end.y() + height)

    def minimumSizeHint(self) -> QSize:
        if not self.rows_active():
            return super().minimumSizeHint()
        return QSize(40, self.sizeHint().height())  # el ancho no debe impedir que se encoja la ventana

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.rows_active():
            index = self.index_at(event.pos())
            if index >= 0:
                QToolTip.showText(event.globalPos(), self.tabToolTip(index), self)
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        if not self.rows_active():
            super().mousePressEvent(event)
            return
        index = self.index_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self.setCurrentIndex(index)
            self._row_pressed = index
            self._row_press_position = event.position().toPoint()
            self._row_skip = -1

    def mouseMoveEvent(self, event) -> None:
        if not self.rows_active():
            super().mouseMoveEvent(event)
            return
        position = event.position().toPoint()
        index = self.index_at(position)
        if index != self._row_hover:
            self._row_hover = index
            self.update()
        moved = (position - self._row_press_position).manhattanLength() >= QApplication.startDragDistance()
        if self._row_pressed < 0 or not event.buttons() & Qt.MouseButton.LeftButton or not moved:
            return
        if index >= 0 and index not in (self._row_pressed, self._row_skip):
            self.moveTab(self._row_pressed, index)
            self._row_pressed = index
            # Si las pestañas miden distinto, otra puede quedar bajo el ratón: sin esto volverían a intercambiarse.
            self._row_skip = self.index_at(position)
        elif index != self._row_skip:
            self._row_skip = -1

    def mouseReleaseEvent(self, event) -> None:
        if not self.rows_active():
            super().mouseReleaseEvent(event)
            return
        self._row_pressed = -1

    def mouseDoubleClickEvent(self, event) -> None:
        if not self.rows_active():
            super().mouseDoubleClickEvent(event)
        elif event.button() == Qt.MouseButton.LeftButton:
            self.tabBarDoubleClicked.emit(self.index_at(event.position().toPoint()))

    def leaveEvent(self, event) -> None:
        self._row_hover = -1
        super().leaveEvent(event)
        self.update()

    # ---------------------------------------------------------------- dibujo
    def paintEvent(self, event) -> None:
        rows = self.rows_active()
        if not rows and not self.is_vertical():
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        colors = theme.current()
        row_rects = self._row_layout()[0] if rows else None
        for index in range(self.count()):
            rect = row_rects[index] if rows else self.tabRect(index)
            button = self.tabButton(index, self.ButtonPosition.RightSide)
            if button is not None:
                target = (rect.right() - button.width() - 3, rect.center().y() - button.height() // 2)
                if (button.x(), button.y()) != target:  # Qt lo colocaría según su propia disposición
                    button.move(*target)
            if not event.rect().intersects(rect):
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            option.rect = rect
            if rows:
                option.state &= ~QStyle.StateFlag.State_MouseOver
                if index == self._row_hover:
                    option.state |= QStyle.StateFlag.State_MouseOver
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)
            left = rect.left() + 6
            icon = self.tabIcon(index)
            if not icon.isNull():
                painter.drawPixmap(left, rect.center().y() - 8, icon.pixmap(16, 16))
                left += 20
            right = rect.right() - 4
            if button is not None:
                right -= button.width() + 2
            painter.setPen(QColor(colors.accent_text if index == self.currentIndex() else colors.text))
            text = self.fontMetrics().elidedText(self.tabText(index), Qt.TextElideMode.ElideRight, max(0, right - left + 1))
            painter.drawText(QRect(left, rect.top(), max(0, right - left + 1), rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    # ---------------------------------------------------------------- arrastrar archivos sobre las pestañas
    def dragEnterEvent(self, event) -> None:
        if self._decider.enter(event):  # aunque ahora no se pueda soltar, pasar por una pestaña la trae al frente
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        index = self.index_at(event.position().toPoint())
        if index != self._drag_index:
            self._drag_index = index
            self._drag_timer.stop()
            if self.settings["drag_to_front"] and index >= 0 and index != self.currentIndex():
                self._drag_timer.start(self.settings["drag_delay_ms"])
        if self._drops_into_tab(index):
            self._decider.paths = dropped_paths(event.mimeData())
            show_operation(event, self._decider.operation(event, self.group.widget(index).path))
        elif folders_in(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def _drops_into_tab(self, index: int) -> bool:
        """Si soltar ahí lleva a esa pestaña, tratada como carpeta. Si no, las carpetas abren una pestaña nueva."""
        return index >= 0 and self.settings["tab_as_folder"]

    def dragLeaveEvent(self, event) -> None:
        self._stop_drag()
        super().dragLeaveEvent(event)

    def _stop_drag(self) -> None:
        self._drag_timer.stop()
        self._drag_index = -1

    def _bring_dragged_to_front(self) -> None:
        if 0 <= self._drag_index < self.count():
            self.setCurrentIndex(self._drag_index)

    def dropEvent(self, event) -> None:
        index = self.index_at(event.position().toPoint())
        self._stop_drag()
        if self._drops_into_tab(index):
            view = self.group.widget(index)
            self._decider.begin(event)
            operation = self._decider.operation(event, view.path)
            signal = view.filesDroppedMenu if self._decider.take_right_button() else view.filesDropped
            if operation is None:
                event.ignore()
                return
            accept_drop(event)
            paths = self._decider.paths
            QTimer.singleShot(0, lambda: signal.emit(view, paths, None, operation == "move"))
            return
        folders = folders_in(event.mimeData())
        if not folders:
            event.ignore()
            return
        accept_drop(event)
        QTimer.singleShot(0, lambda: self.group.open_folders_in_tabs(folders))
