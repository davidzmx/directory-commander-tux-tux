"""Una lista desplegable en la que se marcan varias opciones a la vez. Se queda abierta mientras se marcan."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QStyle, QStyledItemDelegate, QStyleOptionComboBox, QStyleOptionViewItem, QStylePainter

from . import theme

KEY_ROLE = Qt.ItemDataRole.UserRole
_BOX = 13
_MARK_ROOM = _BOX + 10


class _MarkDelegate(QStyledItemDelegate):
    """Dibuja la casilla aquí: en las listas de un desplegable, con nuestra hoja de estilos, Qt no dibuja la marcada."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + _MARK_ROOM, hint.height())

    def paint(self, painter, option, index) -> None:
        view = self.parent().view()
        view.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, view)  # el fondo de toda la fila, también bajo la casilla
        shifted = QStyleOptionViewItem(option)
        shifted.rect = option.rect.adjusted(_MARK_ROOM, 0, 0, 0)
        super().paint(painter, shifted, index)
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is None:
            return
        colors = theme.current()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        ink = QColor(colors.accent_text if selected else colors.text)
        box = QRectF(option.rect.left() + 5.5, option.rect.center().y() - _BOX / 2 + 0.5, _BOX, _BOX)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(ink, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 2, 2)
        if Qt.CheckState(state) == Qt.CheckState.Checked:
            painter.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            x, y, side = box.left(), box.top(), box.width()
            painter.drawPolyline([QPointF(x + side * 0.22, y + side * 0.52), QPointF(x + side * 0.43, y + side * 0.74), QPointF(x + side * 0.8, y + side * 0.26)])
        painter.restore()


class CheckCombo(QComboBox):
    """La primera opción es la de ninguno: elegirla desmarca las demás. Lo que se ve es lo que hay marcado."""

    checkedChanged = Signal()

    def __init__(self, none_text: str, parent=None) -> None:
        super().__init__(parent)
        self.none_text = none_text
        self.setModel(QStandardItemModel(self))
        self.setItemDelegate(_MarkDelegate(self))
        item = QStandardItem(none_text)
        item.setData("", KEY_ROLE)
        self.model().appendRow(item)
        self.view().viewport().installEventFilter(self)
        self.view().installEventFilter(self)

    def add_option(self, text: str, key: str) -> None:
        item = QStandardItem(text)
        item.setData(key, KEY_ROLE)
        item.setCheckable(True)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.model().appendRow(item)

    def _options(self) -> list[QStandardItem]:
        return [self.model().item(row) for row in range(1, self.model().rowCount())]

    def checked_keys(self) -> tuple[str, ...]:
        return tuple(item.data(KEY_ROLE) for item in self._options() if item.checkState() == Qt.CheckState.Checked)

    def set_checked_keys(self, keys) -> None:
        """Sin avisar: se usa para reflejar lo que ya tiene el panel."""
        wanted = set(keys)
        for item in self._options():
            item.setCheckState(Qt.CheckState.Checked if item.data(KEY_ROLE) in wanted else Qt.CheckState.Unchecked)
        self._show_summary()

    def summary(self) -> str:
        names = [item.text() for item in self._options() if item.checkState() == Qt.CheckState.Checked]
        return ", ".join(names) or self.none_text

    def _show_summary(self) -> None:
        self.setToolTip(self.summary())
        self.update()

    def toggle_row(self, row: int) -> None:
        item = self.model().item(row)
        if item is None:
            return
        if row == 0:
            for option in self._options():
                option.setCheckState(Qt.CheckState.Unchecked)
            self.hidePopup()  # no hay nada más que marcar
        else:
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
        self._show_summary()
        self.checkedChanged.emit()

    def eventFilter(self, watched, event) -> bool:
        # Primero el tipo: al destruirse la lista también llegan eventos aquí, y entonces ya no se le puede preguntar nada.
        kind = event.type()
        if kind == QEvent.Type.MouseButtonRelease and watched is self.view().viewport():
            index = self.view().indexAt(event.position().toPoint())
            if index.isValid():
                self.toggle_row(index.row())
            return True  # si no, la lista se cerraría con el primer clic
        if kind == QEvent.Type.KeyPress and watched is self.view() and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.key() == Qt.Key.Key_Space:
                self.toggle_row(self.view().currentIndex().row())
            else:
                self.hidePopup()
            return True
        return False

    def paintEvent(self, _event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        room = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self).width()
        option.currentText = self.fontMetrics().elidedText(self.summary(), Qt.TextElideMode.ElideRight, room - 2)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
