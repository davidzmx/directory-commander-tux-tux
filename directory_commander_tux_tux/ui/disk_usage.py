"""El uso del disco de una carpeta: arriba, sus carpetas y archivos de mayor a menor, con lo que ocupa cada uno; abajo, un mapa de
cuadros en el que cada archivo es un rectángulo de área proporcional a su tamaño, de un color según su tipo. Los dos van a la par:
lo que se elige en uno se marca en el otro. Un doble clic en el mapa entra en esa carpeta; «Subir» vuelve a la de arriba."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QSplitter, QStyle, QStyledItemDelegate, QToolBar, QToolTip, QTreeWidget,
                               QTreeWidgetItem, QWidget)

from ..core import disk_usage
from ..core.disk_usage import Node
from ..core.listing import format_size, format_time
from . import theme
from .background import run_bg
from .icons import app_icon, themed
from ..core.i18n import tr

NODE_ROLE = Qt.ItemDataRole.UserRole + 1
MIN_AREA = 3.0  # en píxeles: lo más pequeño no se dibuja por separado
CUSHION_FROM = 8.0  # desde este lado, el cuadro lleva relieve
KINDS = {
    "image": ({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg", "heic", "avif", "raw", "cr2", "nef", "psd", "xcf", "ico"}, "#43a047"),
    "video": ({"mp4", "mkv", "avi", "mov", "webm", "wmv", "flv", "m4v", "mpg", "mpeg", "ts", "3gp"}, "#e53935"),
    "audio": ({"mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma", "aiff"}, "#ab47bc"),
    "archive": ({"zip", "7z", "rar", "tar", "gz", "bz2", "xz", "zst", "tgz", "deb", "rpm", "appimage", "iso", "img", "jar"}, "#fbc02d"),
    "document": ({"pdf", "doc", "docx", "odt", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "txt", "md", "rtf", "epub", "csv"}, "#1e88e5"),
    "code": ({"py", "js", "ts", "java", "c", "h", "cpp", "hpp", "rs", "go", "rb", "php", "html", "css", "json", "xml", "yml", "yaml", "sh", "sql"}, "#00acc1"),
    "binary": ({"so", "dll", "exe", "bin", "o", "a", "pyc", "class", "dat", "db", "sqlite", "pack", "idx"}, "#8d6e63"),
}
OTHER_COLOR = "#90a4ae"
_COLOR_BY_EXTENSION = {extension: color for extensions, color in KINDS.values() for extension in extensions}


def color_for(node: Node) -> QColor:
    return QColor(_COLOR_BY_EXTENSION.get(node.extension, OTHER_COLOR))


def kind_label(kind: str) -> str:
    return {"image": tr("Imágenes"), "video": tr("Vídeos"), "audio": tr("Audio"), "archive": tr("Comprimidos e imágenes de disco"),
            "document": tr("Documentos"), "code": tr("Código"), "binary": tr("Programas y datos")}.get(kind, tr("Otros"))


def layout(node: Node, rect: QRectF, leaves: list, folders: list) -> None:
    """Coloca los hijos de la carpeta en su rectángulo, y los de sus subcarpetas dentro de cada una. Lo diminuto se queda sin dibujar."""
    children = [child for child in node.children if child.size > 0]
    places = disk_usage.squarify([child.size for child in children], rect.x(), rect.y(), rect.width(), rect.height())
    for child, (x, y, width, height) in zip(children, places):
        if width * height < MIN_AREA:
            break  # vienen de mayor a menor: los siguientes son aún más pequeños
        place = QRectF(x, y, width, height)
        if child.is_dir and child.children:
            folders.append((place, child))
            layout(child, place, leaves, folders)
        else:
            leaves.append((place, child))


class Treemap(QWidget):
    nodeChosen = Signal(object)  # un clic: el archivo o la carpeta
    zoomRequested = Signal(object)  # doble clic: entrar en esa carpeta
    menuRequested = Signal(object, object)  # nodo, punto global

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self.root: Node | None = None
        self.selected: Node | None = None
        self._leaves: list = []
        self._folders: list = []
        self._picture: QPixmap | None = None

    def set_root(self, node: Node | None) -> None:
        self.root = node
        self._picture = None
        self.update()

    def set_selected(self, node: Node | None) -> None:
        self.selected = node
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._picture = None

    def _draw(self) -> QPixmap:
        """El mapa se dibuja una vez y se guarda: solo cambia al cambiar de carpeta o de tamaño."""
        self._leaves, self._folders = [], []
        picture = QPixmap(self.size() * self.devicePixelRatioF())
        picture.setDevicePixelRatio(self.devicePixelRatioF())
        picture.fill(QColor(theme.current().panel))
        if self.root is None or self.root.size <= 0:
            return picture
        layout(self.root, QRectF(self.rect()), self._leaves, self._folders)
        painter = QPainter(picture)
        border = QPen(QColor(0, 0, 0, 90), 0)
        for place, node in self._leaves:
            color = color_for(node)
            if min(place.width(), place.height()) >= CUSHION_FROM:
                # Relieve, como un cojín: más claro arriba a la izquierda y más oscuro abajo a la derecha.
                shade = QLinearGradient(place.topLeft(), place.bottomRight())
                shade.setColorAt(0, color.lighter(150))
                shade.setColorAt(0.5, color)
                shade.setColorAt(1, color.darker(160))
                painter.fillRect(place, shade)
                painter.setPen(border)
                painter.drawRect(place)
            else:
                painter.fillRect(place, color)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 1))
        for place, _node in self._folders:  # el contorno de cada carpeta, para ver qué va junto
            if min(place.width(), place.height()) >= CUSHION_FROM * 2:
                painter.drawRect(place)
        painter.end()
        return picture

    def paintEvent(self, _event) -> None:
        if self._picture is None:
            self._picture = self._draw()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._picture)
        place = self.place_of(self.selected)
        if place is not None:
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.drawRect(place.adjusted(1, 1, -1, -1))
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawRect(place.adjusted(1, 1, -1, -1))

    def place_of(self, node: Node | None) -> QRectF | None:
        if node is None:
            return None
        if node is self.root:
            return QRectF(self.rect())
        return next((place for place, item in self._leaves + self._folders if item is node), None)

    def node_at(self, point: QPointF) -> Node | None:
        return next((node for place, node in self._leaves if place.contains(point)), None)

    def _folder_at(self, point: QPointF) -> Node | None:
        """La carpeta más honda que contiene el punto."""
        inside = [node for place, node in self._folders if place.contains(point)]
        return inside[-1] if inside else None

    def mousePressEvent(self, event) -> None:
        node = self.node_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and node is not None:
            self.nodeChosen.emit(node)
        elif event.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit(node or self.root, event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event) -> None:
        folder = self._folder_at(event.position())
        if folder is not None:
            self.zoomRequested.emit(folder)

    def event(self, event) -> bool:
        """La descripción de un cuadro se muestra al pararse sobre él y se queda mientras el ratón no salga de ese cuadro.
        Antes se pedía en cada movimiento: parpadeaba, y al reutilizar la ventana con un texto más largo salía cortada."""
        if event.type() == QEvent.Type.ToolTip:
            point = QPointF(event.pos())
            node = self.node_at(point)
            if node is None:
                QToolTip.hideText()
                event.ignore()
                return True
            text = f"{node.path}\n{format_size(node.size)}"
            if QToolTip.isVisible() and QToolTip.text() != text:
                QToolTip.hideText()  # una nueva, con su tamaño, en lugar de reutilizar la de otro cuadro
            QToolTip.showText(event.globalPos(), text, self, self.place_of(node).toAlignedRect())
            return True
        return super().event(event)


class _ShareDelegate(QStyledItemDelegate):
    """La columna de la barra: qué parte de su carpeta ocupa."""

    def paint(self, painter, option, index) -> None:
        share = index.data(Qt.ItemDataRole.UserRole)
        if share is None:
            super().paint(painter, option, index)
            return
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        bar = option.rect.adjusted(3, 4, -3, -4)
        t = theme.current()
        painter.fillRect(bar, QColor(t.border))
        painter.fillRect(QRectF(bar.x(), bar.y(), bar.width() * share, bar.height()), QColor(t.meter_ok))


class DiskUsageWindow(QMainWindow):
    """Analiza la carpeta en segundo plano. `open_in_pane(carpeta, archivo)` lleva el panel activo hasta lo elegido, y
    `delete(rutas, definitivo, al_terminar, parent=…, detail=…)` lo borra: al terminar, se quita del árbol y del mapa sin volver a analizar."""

    def __init__(self, path: str, open_in_pane=None, parent=None, delete=None, open_file=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setWindowIcon(app_icon())
        self.path = os.path.normpath(path)
        self.open_in_pane = open_in_pane
        self.delete = delete
        self.open_file = open_file  # abrir un archivo con su programa, como desde la lista
        self.top: Node | None = None
        self._call = None
        self._items: dict[int, QTreeWidgetItem] = {}
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels([tr("Nombre"), tr("Parte"), tr("Tamaño"), tr("Archivos"), tr("Modificación")])
        self.tree.setItemDelegateForColumn(1, _ShareDelegate(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemExpanded.connect(self._fill)
        self.tree.currentItemChanged.connect(self._tree_chosen)
        self.tree.itemDoubleClicked.connect(lambda item, _column: self.activate(item.data(0, NODE_ROLE)))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda point: self.tree.itemAt(point) is not None and self._menu(self.tree.itemAt(point).data(0, NODE_ROLE), self.tree.viewport().mapToGlobal(point)))
        self.map = Treemap()
        self.map.nodeChosen.connect(self.select_node)
        self.map.zoomRequested.connect(self.zoom)
        self.map.menuRequested.connect(self._menu)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.map)
        splitter.setSizes([300, 400])
        self.setCentralWidget(splitter)
        bar = QToolBar(tr("Uso del disco"))
        bar.setMovable(False)
        self.addToolBar(bar)
        self.refresh_action = self._action(bar, tr("Volver a analizar"), "refresh", self.scan)
        self.up_action = self._action(bar, tr("Subir"), "goup", self.zoom_out)
        self.open_action = self._action(bar, tr("Mostrar en el panel"), "goforward", lambda: self._open(self._current()))
        self.stop_action = self._action(bar, tr("Detener"), "close", self.stop)
        self.trash_action = self._action(bar, tr("Enviar a la papelera"), "delete", lambda: self.delete_node(self._current(), False))
        self.trash_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.erase_action = self._action(bar, tr("Eliminar definitivamente"), "deletepermanent", lambda: self.delete_node(self._current(), True))
        self.erase_action.setShortcut(QKeySequence("Shift+Delete"))
        for action in (self.trash_action, self.erase_action):
            action.setVisible(delete is not None)
        bar.addSeparator()
        legend = QWidget()  # qué tipo de archivo es cada color
        row = QHBoxLayout(legend)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(4)
        for kind, (_extensions, color) in [*KINDS.items(), ("other", (set(), OTHER_COLOR))]:
            chip = QLabel()
            chip.setPixmap(self._chip(color).pixmap(12, 12))
            row.addWidget(chip)
            row.addWidget(QLabel(kind_label(kind)))
            row.addSpacing(6)
        bar.addWidget(legend)
        self.setWindowTitle(tr("Uso del disco — {path}", path=self.path))
        self.resize(1000, 760)
        self.scan()

    @staticmethod
    def _chip(color: str) -> QIcon:
        pixmap = QPixmap(12, 12)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)

    def _action(self, bar: QToolBar, label: str, icon: str, slot) -> QAction:
        action = bar.addAction(themed(icon), label)
        action.triggered.connect(lambda _checked=False: slot())
        return action

    # ---------------------------------------------------------------- análisis
    def scan(self) -> None:
        self.stop()
        self.tree.clear()
        self._items = {}
        self.map.set_root(None)
        self.statusBar().showMessage(tr("Analizando {path}…", path=self.path))
        self.stop_action.setEnabled(True)
        self.refresh_action.setEnabled(False)
        path = self.path
        call = None

        def current(handler):
            """Solo cuenta lo que llega del análisis en curso: uno detenido avisa después, quizá con la ventana ya cerrada."""
            return lambda value: handler(value) if call is not None and self._call is call else None

        call = run_bg(lambda sink: disk_usage.scan(path, sink), current(self._scanned), current(self._failed), current(self._progress))
        self._call = call

    def stop(self) -> None:
        if self._call is not None:
            self._call.cancel()
            self._call = None
            self.statusBar().showMessage(tr("Análisis detenido"))
        self.stop_action.setEnabled(False)
        self.refresh_action.setEnabled(True)

    def _progress(self, value) -> None:
        files, _total, folder = value
        self.statusBar().showMessage(tr("Analizando: {files} archivos… {folder}", files=files, folder=folder))

    def _failed(self, exc) -> None:
        self._call = None
        self.stop_action.setEnabled(False)
        self.refresh_action.setEnabled(True)
        self.statusBar().showMessage(tr("No se pudo analizar: {exc}", exc=exc))

    def _scanned(self, top: Node) -> None:
        self._call = None
        self.stop_action.setEnabled(False)
        self.refresh_action.setEnabled(True)
        self.top = top
        root = self._item(self.tree.invisibleRootItem(), top, 1.0)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        for column in (1, 2, 3, 4):
            self.tree.resizeColumnToContents(column)
        self.tree.setColumnWidth(1, 120)
        self.map.set_root(top)
        self.up_action.setEnabled(False)
        self.statusBar().showMessage(tr("{size} en {files} archivos", size=format_size(top.size), files=top.files))

    # ---------------------------------------------------------------- árbol
    def _item(self, parent: QTreeWidgetItem, node: Node, share: float) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [node.name, "", format_size(node.size), str(node.files) if node.is_dir else "", format_time(node.mtime)])
        item.setData(0, NODE_ROLE, node)
        item.setData(1, Qt.ItemDataRole.UserRole, share)
        item.setToolTip(1, f"{share * 100:.1f} %")
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setIcon(0, themed("folder") if node.is_dir else self._chip(color_for(node).name()))
        if node.error:
            item.setToolTip(0, tr("No se pudo leer: {error}", error=node.error))
        if node.is_dir and node.files + len(node.folders):
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self._items[id(node)] = item
        return item

    def _fill(self, item: QTreeWidgetItem) -> None:
        """Los hijos se crean al desplegar: una carpeta con cientos de miles de archivos se abre al momento."""
        node = item.data(0, NODE_ROLE)
        if node is None or item.childCount() or not node.children:
            return
        for child in node.children:
            self._item(item, child, child.size / node.size if node.size else 0.0)

    def item_for(self, node: Node) -> QTreeWidgetItem | None:
        for step in disk_usage.path_to(node)[:-1]:
            item = self._items.get(id(step))
            if item is None:
                return None
            item.setExpanded(True)
        return self._items.get(id(node))

    def _current(self) -> Node | None:
        item = self.tree.currentItem()
        return item.data(0, NODE_ROLE) if item is not None else None

    def _tree_chosen(self, item, _previous) -> None:
        node = item.data(0, NODE_ROLE) if item is not None else None
        if node is not None and self.map.root is not None and node is not self.map.root and self.map.root not in disk_usage.path_to(node):
            self.zoom(node.parent if not node.is_dir else node)  # fuera de lo que se ve en el mapa: el mapa va hasta ella
        self.map.set_selected(node)

    def select_node(self, node: Node) -> None:
        item = self.item_for(node)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    # ---------------------------------------------------------------- mapa
    def zoom(self, node: Node | None) -> None:
        if node is None or not node.is_dir:
            return
        self.map.set_root(node)
        self.up_action.setEnabled(node is not self.top)

    def zoom_out(self) -> None:
        if self.map.root is not None and self.map.root.parent is not None:
            self.zoom(self.map.root.parent)

    def _menu(self, node: Node | None, position) -> None:
        if node is not None:
            self.build_menu(node).exec(position)

    def build_menu(self, node: Node) -> QMenu:
        menu = QMenu(self)
        if not node.is_dir and self.open_file is not None:
            menu.addAction(themed("goforward"), tr("Abrir"), lambda: self.open_file(node.path))
        menu.addAction(themed("folder"), tr("Mostrar en el panel"), lambda: self._open(node))
        if self.delete is not None and node.parent is not None:
            menu.addSeparator()
            trash = menu.addAction(themed("delete"), tr("Enviar a la papelera"), lambda: self.delete_node(node, False))
            trash.setShortcut(QKeySequence(Qt.Key.Key_Delete))  # en el menú se ve la tecla; la que actúa es la de la ventana
            erase = menu.addAction(themed("deletepermanent"), tr("Eliminar definitivamente, sin papelera"), lambda: self.delete_node(node, True))
            erase.setShortcut(QKeySequence("Shift+Delete"))
            menu.addSeparator()
        if node.parent is not None:
            menu.addAction(themed("folder"), tr("Ver su carpeta en el mapa"), lambda: self.zoom(node.parent))
        if self.map.root is not None and self.map.root.parent is not None:
            menu.addAction(themed("goup"), tr("Subir"), self.zoom_out)
        return menu

    def activate(self, node: Node | None) -> None:
        """Doble clic en la lista: un archivo se abre con su programa; una carpeta se despliega, como en cualquier árbol."""
        if node is not None and not node.is_dir and self.open_file is not None:
            self.open_file(node.path)

    def _open(self, node: Node | None) -> None:
        if node is None or self.open_in_pane is None:
            return
        if node.is_dir:
            self.open_in_pane(node.path, "")
        else:
            self.open_in_pane(os.path.dirname(node.path), node.path)

    # ---------------------------------------------------------------- borrar
    def delete_node(self, node: Node | None, permanent: bool) -> None:
        """La carpeta analizada no se borra desde aquí: solo lo de dentro."""
        if node is None or node.parent is None or self.delete is None:
            return
        path = node.path

        def done(_result) -> None:
            if os.path.lexists(path):
                self.statusBar().showMessage(tr("No se pudo borrar {name}", name=node.name), 6000)
                return
            self.forget(node)
            self.statusBar().showMessage(tr("Liberados {size}. Quedan {total} en {files} archivos", size=format_size(node.size),
                                            total=format_size(self.top.size), files=self.top.files), 8000)

        self.delete([path], permanent, done, parent=self, detail=f" ({format_size(node.size)})")

    def forget(self, node: Node) -> None:
        """Lo borrado sale del árbol y del mapa, y lo que ocupaba se descuenta de cada carpeta por encima."""
        parent = node.parent
        item = self._items.pop(id(node), None)
        if item is not None:
            (item.parent() or self.tree.invisibleRootItem()).removeChild(item)
        disk_usage.remove(node)
        step = parent
        while step is not None:
            step_item = self._items.get(id(step))
            if step_item is not None:
                step_item.setText(2, format_size(step.size))
                step_item.setText(3, str(step.files))
                for row in range(step_item.childCount()):
                    child = step_item.child(row)
                    child_node = child.data(0, NODE_ROLE)
                    share = child_node.size / step.size if child_node is not None and step.size else 0.0
                    child.setData(1, Qt.ItemDataRole.UserRole, share)
                    child.setToolTip(1, f"{share * 100:.1f} %")
            step = step.parent
        shown = self.map.root
        if shown is node or (shown is not None and node in disk_usage.path_to(shown)):
            shown = parent
        self.map.selected = None if self.map.selected is node else self.map.selected
        self.zoom(shown)
        self.map.set_root(shown)  # se vuelve a dibujar con lo que queda
        item = self._items.get(id(parent))
        if item is not None:
            self.tree.setCurrentItem(item)

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)
