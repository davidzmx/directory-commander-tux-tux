"""Las páginas de las preferencias: una lista vertical a la izquierda, con las parecidas agrupadas."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QStackedWidget, QTreeWidget, QTreeWidgetItem, QWidget

PAGE_ROLE = Qt.ItemDataRole.UserRole
NAVIGATION_WIDTH = 190


class PreferencePages(QWidget):
    """Se usa como un QTabWidget. Un grupo no tiene página propia: elegirlo lleva a la primera de las suyas."""

    currentChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.navigation = QTreeWidget()
        self.navigation.setObjectName("prefsNavigation")
        self.navigation.setHeaderHidden(True)
        self.navigation.setRootIsDecorated(False)
        self.navigation.setItemsExpandable(False)
        self.navigation.setIndentation(14)
        self.navigation.setFixedWidth(NAVIGATION_WIDTH)
        self.navigation.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stack = QStackedWidget()
        self._items: list[QTreeWidgetItem] = []
        self._groups: dict[str, QTreeWidgetItem] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        self.navigation.currentItemChanged.connect(self._item_chosen)
        self.stack.currentChanged.connect(self.currentChanged)

    def _group(self, title: str) -> QTreeWidgetItem:
        group = self._groups.get(title)
        if group is None:
            group = self._groups[title] = QTreeWidgetItem(self.navigation, [title])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setExpanded(True)
        return group

    def addTab(self, page: QWidget, title: str, group: str = "") -> int:
        index = self.stack.addWidget(page)
        item = QTreeWidgetItem(self._group(group) if group else self.navigation, [title])
        item.setData(0, PAGE_ROLE, index)
        self._items.append(item)
        if index == 0:
            self.navigation.setCurrentItem(item)
        return index

    def _item_chosen(self, item: QTreeWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        index = item.data(0, PAGE_ROLE)
        if index is None:
            self.navigation.setCurrentItem(item.child(0))
        else:
            self.stack.setCurrentIndex(index)

    def count(self) -> int:
        return self.stack.count()

    def widget(self, index: int) -> QWidget:
        return self.stack.widget(index)

    def tabText(self, index: int) -> str:
        return self._items[index].text(0)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def currentWidget(self) -> QWidget:
        return self.stack.currentWidget()

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._items):
            self.navigation.setCurrentItem(self._items[index])

    def setCurrentWidget(self, page: QWidget) -> None:
        self.setCurrentIndex(self.stack.indexOf(page))
