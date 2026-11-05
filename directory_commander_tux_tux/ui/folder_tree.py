"""Árbol de carpetas con secciones: Inicio (Home en Linux), Favoritos y las unidades. En Linux, además, los dispositivos.

Las carpetas se leen al desplegarlas y siempre en segundo plano: una unidad lenta o de red
no debe congelar la ventana.
"""
from __future__ import annotations

import os
from pathlib import PurePath

import shiboken6
from PySide6.QtCore import QEvent, QFileInfo, QFileSystemWatcher, QPoint, QRect, QRectF, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QFileIconProvider, QFrame, QHeaderView, QStyle, QStyledItemDelegate, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QTreeWidgetItemIterator)

from ..core import devices as storage, favorites as favorites_tree, remote
from ..core.listing import COMPUTER_LABEL, COMPUTER_PATH, _is_hidden, format_size, has_computer_view, is_results, list_dir
from ..core.platform_utils import home_dir
from . import theme
from .background import run_bg
from .connection_led import LED_SIZE, paint_led
from .drag_drop import DropDecider, accept_drop, folder_mime, show_operation, start_drag
from .icons import themed
from ..core.i18n import tr

PATH_ROLE = Qt.ItemDataRole.UserRole
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1
DEVICE_ROLE = Qt.ItemDataRole.UserRole + 2  # el dispositivo (/dev/…) de un elemento de «Dispositivos»
ICON_KEY_ROLE = Qt.ItemDataRole.UserRole + 4  # de qué es el icono que lleva: no se vuelve a poner si no cambia
USAGE_ROLE = Qt.ItemDataRole.UserRole + 3  # la parte ocupada de un dispositivo montado, de 0 a 1
USAGE_BAR_HEIGHT = 4
DISK_FULL = 0.9
SUBFOLDER_CHECK_LIMIT = 400   # a partir de aquí se supone que tiene subcarpetas, sin comprobarlo
SCAN_LIMIT = 2000
MAX_WATCHED = 256
EXPAND_ON_HOVER_MS = 800

KNOWN_FOLDERS = (
    (tr("Escritorio"), QStandardPaths.StandardLocation.DesktopLocation),
    (tr("Descargas"), QStandardPaths.StandardLocation.DownloadLocation),
    (tr("Documentos"), QStandardPaths.StandardLocation.DocumentsLocation),
    (tr("Imágenes"), QStandardPaths.StandardLocation.PicturesLocation),
    (tr("Música"), QStandardPaths.StandardLocation.MusicLocation),
    (tr("Vídeos"), QStandardPaths.StandardLocation.MoviesLocation),
)

_icons = QFileIconProvider()


def _update(item: QTreeWidgetItem, role, value) -> None:
    old = item.data(0, role)
    if old != value:
        if role == Qt.ItemDataRole.ToolTipRole and old and QToolTip.isVisible() and QToolTip.text() == old:
            QToolTip.hideText()  # la que se ve ya no es cierta: al mover el ratón se abre la nueva
        item.setData(0, role, value)


def usage_tooltip(name: str, lines: list[str], total: int | None, free: int | None) -> str:
    rows = [name, *lines]
    if total:
        rows.append(tr("Tamaño: {total}", total=format_size(total)))
        if free is not None:
            rows.append(tr("Ocupado: {used} ({percent} %)", used=format_size(total - free), percent=round((total - free) * 100 / total)))
            rows.append(tr("Libre: {free} ({percent} %)", free=format_size(free), percent=round(free * 100 / total)))
    return "\n".join(rows)


def device_tooltip(device: storage.Device) -> str:
    where = [device.path + (f" · {device.fstype}" if device.fstype else "")]
    if device.mounted:
        return usage_tooltip(device.name, where + [tr("Montado en {point}", point=device.mount_point)], device.total or device.size, device.free)
    return usage_tooltip(device.name, where + [tr("Sin montar: un clic lo monta")], device.size, None)


def device_icon(device: storage.Device) -> QIcon:
    """El del tema del escritorio. Sin montar, con su emblema, como en el panel de lugares del sistema."""
    base = QIcon.fromTheme(device.icon_name, QIcon.fromTheme("drive-harddisk", themed("drive")))
    emblem = QIcon.fromTheme("emblem-unmounted")
    if device.mounted or emblem.isNull():
        return base
    icon = QIcon()
    for size in (16, 22, 32):
        pixmap = QPixmap(base.pixmap(size, size))
        painter = QPainter(pixmap)
        painter.drawPixmap(QRect(size // 2, size // 2, size // 2, size // 2), emblem.pixmap(size // 2, size // 2))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class _TreeDelegate(QStyledItemDelegate):
    """Lo que el árbol dibuja además del nombre: la barra de ocupación de un dispositivo y el foquito de un servidor conectado."""

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        if index.data(USAGE_ROLE) is not None:
            size.setHeight(size.height() + USAGE_BAR_HEIGHT + 2)
        if self._has_led(index):
            size.setWidth(size.width() + LED_SIZE + 6)
        return size

    def _has_led(self, index) -> bool:
        tree = self.parent()
        item = tree.itemFromIndex(index)
        return tree.led_lit is not None and item is not None and item is tree.remote_item

    def _text_rect(self, option, index) -> tuple[QRect, int]:
        """Dónde va el nombre, como lo coloca el estilo, y lo que mide."""
        filled = option.__class__(option)
        self.initStyleOption(filled, index)
        tree = self.parent()
        return tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, filled, tree), filled.fontMetrics.horizontalAdvance(filled.text)

    def paint(self, painter, option, index) -> None:
        usage = index.data(USAGE_ROLE)
        if usage is None:
            super().paint(painter, option, index)
        else:
            # El nombre sube lo que ocupa la barra, que va debajo de él, como en el panel de lugares del sistema.
            raised = option.__class__(option)
            raised.rect = option.rect.adjusted(0, 0, 0, -(USAGE_BAR_HEIGHT + 2))
            painter.save()
            super().paint(painter, raised, index)
            painter.restore()
            colors = theme.current()
            text_left = self._text_rect(raised, index)[0].left()
            bar = QRect(text_left, option.rect.bottom() - USAGE_BAR_HEIGHT - 1, max(0, option.rect.right() - text_left - 6), USAGE_BAR_HEIGHT)
            painter.fillRect(bar, QColor(colors.border))
            painter.fillRect(bar.left(), bar.top(), int(bar.width() * usage), bar.height(),
                             QColor(colors.meter_low if usage >= DISK_FULL else colors.meter_ok))
        if self._has_led(index):
            text, width = self._text_rect(option, index)
            left = text.left() + width + 8
            top = option.rect.center().y() - LED_SIZE / 2
            paint_led(painter, QRectF(left, top, LED_SIZE, LED_SIZE).adjusted(1, 1, -1, -1), self.parent().led_lit)


def _same_path(a: str, b: str) -> bool:
    if remote.is_remote(a) or remote.is_remote(b):
        return remote.is_remote(a) and remote.is_remote(b) and remote.normalize(a) == remote.normalize(b)
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _inside(path: str, base: str) -> bool:
    """`path` está dentro de `base`, a cualquier profundidad."""
    path, base = (os.path.normcase(os.path.normpath(p)) for p in (path, base))
    return path.startswith(base.rstrip(os.sep) + os.sep)


def _has_subfolder(path: str, show_hidden: bool) -> bool:
    """Si la carpeta tiene alguna subcarpeta visible. Una carpeta enorme se da por positiva."""
    if remote.is_remote(path):
        return True  # preguntarlo al servidor por cada carpeta haría muy lento desplegar
    try:
        with os.scandir(path) as it:
            for position, item in enumerate(it):
                if position >= SCAN_LIMIT:
                    return True
                try:
                    if item.is_dir() and (show_hidden or not _is_hidden(item.name, item.stat(follow_symlinks=False))):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _read_children(path: str, show_hidden: bool, sink) -> list[tuple[str, str, bool, bool]]:
    """Subcarpetas de `path` como (etiqueta, ruta, tiene subcarpetas, es unidad)."""
    if path == COMPUTER_PATH:
        from ..core.drives import list_drives

        return [(drive.name, drive.path, True, True) for drive in list_drives(with_details=False)]
    entries = list_dir(path, show_hidden, is_cancelled=lambda: sink.is_cancelled)
    folders = sorted((e for e in entries if e.is_dir), key=lambda e: e.natkey)
    rows = []
    for position, entry in enumerate(folders):
        sink.check()
        known = position >= SUBFOLDER_CHECK_LIMIT or _has_subfolder(entry.path, show_hidden)
        rows.append((entry.name, entry.path, known, False))
    return rows


class FolderTree(QTreeWidget):
    pathActivated = Signal(str)
    filesDropped = Signal(list, str, bool)  # rutas, carpeta de destino, mover
    filesDroppedMenu = Signal(list, str, bool)  # igual, soltando con el botón derecho: se pregunta qué hacer
    deviceActivated = Signal(str)  # un dispositivo sin montar: hay que montarlo antes de mostrarlo
    deviceMenuRequested = Signal(str, QPoint)  # el dispositivo y dónde va su menú

    def __init__(self, show_hidden: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.show_hidden = show_hidden
        self._disposed = False
        self._calls: set = set()
        self._waiting: dict[int, list] = {}
        self._sync_token = 0
        self.remote_item: QTreeWidgetItem | None = None  # con el panel en un servidor, sus carpetas en lugar de las del equipo
        self.devices_item: QTreeWidgetItem | None = None
        self._devices_shown = False
        self.led_lit: bool | None = None  # el foquito del servidor: None sin conexión viva; si no, en qué mitad del parpadeo está
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(12)
        self.setItemDelegate(_TreeDelegate(self))  # las filas de los dispositivos montados son más altas: no todas miden lo mismo
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(90)
        # Con el ancho justo al contenido, los nombres se ven enteros y el árbol se desplaza en horizontal.
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._watcher = QFileSystemWatcher(self)
        self._pending_reload: set[str] = set()
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(400)
        self._reload_timer.timeout.connect(self._reload_pending)
        self._watcher.directoryChanged.connect(self._directory_changed)
        self.itemExpanded.connect(self._item_expanded)
        self.itemClicked.connect(self._item_clicked)
        self._decider = DropDecider()
        self._drop_item: QTreeWidgetItem | None = None
        self._expand_timer = QTimer(self, singleShot=True, interval=EXPAND_ON_HOVER_MS)
        self._expand_timer.timeout.connect(self._expand_drop_item)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._build_sections()

    # ---------------------------------------------------------------- secciones
    def _node(self, parent, label: str, path: str, icon, has_children: bool = True) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, PATH_ROLE, path)
        item.setData(0, LOADED_ROLE, False)
        item.setIcon(0, icon)
        item.setToolTip(0, path)
        self._set_indicator(item, has_children)
        return item

    @staticmethod
    def _set_indicator(item: QTreeWidgetItem, has_children: bool) -> None:
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator if has_children
                                     else QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)

    def _folder_icon(self):
        return _icons.icon(QFileIconProvider.IconType.Folder)

    def _build_sections(self) -> None:
        home = home_dir()
        on_windows = has_computer_view()
        self.home_item = self._node(self, tr("Inicio") if on_windows else "Home", home, themed("home"))
        if on_windows:
            # En Windows, Inicio es una lista de accesos; en Linux, la propia carpeta personal.
            self.home_item.setData(0, LOADED_ROLE, True)
            for label, location in KNOWN_FOLDERS:
                path = QStandardPaths.writableLocation(location)
                if path and os.path.isdir(path) and not _same_path(path, home):
                    self._node(self.home_item, label, os.path.normpath(path), _icons.icon(QFileInfo(path)))
        self.favorites_item = self._node(self, tr("Favoritos"), "", themed("favorites"), has_children=False)
        self.favorites_item.setData(0, LOADED_ROLE, True)
        if on_windows:
            self.root_item = self._node(self, COMPUTER_LABEL, COMPUTER_PATH, _icons.icon(QFileIconProvider.IconType.Computer))
        else:
            self.root_item = self._node(self, tr("Sistema de archivos"), os.sep, themed("drive"))
            self.devices_item = self._node(self, tr("Dispositivos"), "", themed("drive"), has_children=False)
            self.devices_item.setData(0, LOADED_ROLE, True)
            self.devices_item.setHidden(True)  # hasta saber qué dispositivos hay
        for item in (self.home_item, self.favorites_item, self.root_item):
            item.setExpanded(True)

    def _sections(self) -> list[QTreeWidgetItem]:
        return [item for item in (self.home_item, self.favorites_item, self.root_item, self.devices_item) if item is not None]

    def set_devices(self, found: list[storage.Device]) -> None:
        """Los dispositivos, conservando los elementos que siguen ahí: así no se pliega lo que se había desplegado."""
        section = self.devices_item
        if section is None:
            return
        existing = {section.child(i).data(0, DEVICE_ROLE): section.child(i) for i in range(section.childCount())}
        wanted = {device.path for device in found}
        for path, item in existing.items():
            if path not in wanted:
                section.removeChild(item)
        for position, device in enumerate(found):
            item = existing.get(device.path)
            if item is None:
                item = QTreeWidgetItem([device.name])
                item.setData(0, DEVICE_ROLE, device.path)
                section.insertChild(position, item)
            elif section.indexOfChild(item) != position:
                section.removeChild(item)
                section.insertChild(position, item)
            was_mounted = bool(item.data(0, PATH_ROLE))
            # Solo lo que ha cambiado: se repasa cada pocos segundos, y tocar una fila mientras se ve su descripción emergente la cierra.
            _update(item, Qt.ItemDataRole.DisplayRole, device.name)
            if item.data(0, ICON_KEY_ROLE) != (device.icon_name, device.mounted):
                item.setData(0, ICON_KEY_ROLE, (device.icon_name, device.mounted))
                item.setIcon(0, device_icon(device))
            _update(item, Qt.ItemDataRole.ToolTipRole, device_tooltip(device))
            usage = device.used_fraction
            _update(item, USAGE_ROLE, None if usage is None else round(usage, 3))
            _update(item, PATH_ROLE, device.mount_point)
            if was_mounted != device.mounted:
                item.takeChildren()
                item.setData(0, LOADED_ROLE, False)
            self._set_indicator(item, device.mounted)
        hidden = not found or self.remote_item is not None
        section.setHidden(hidden)
        if found and not self._devices_shown:
            self._devices_shown = True  # se despliega la primera vez; luego, como lo deje el usuario
            section.setExpanded(True)

    def set_root_usage(self, total: int | None, free: int | None) -> None:
        """La barra y la descripción de «Sistema de archivos», como las de los dispositivos."""
        if self.devices_item is None or not total or free is None:
            return
        _update(self.root_item, USAGE_ROLE, round((total - free) / total, 3))
        _update(self.root_item, Qt.ItemDataRole.ToolTipRole, usage_tooltip(tr("Sistema de archivos"), [os.sep], total, free))

    def set_led(self, lit: bool | None) -> None:
        if lit == self.led_lit:
            return
        self.led_lit = lit
        if self.remote_item is not None:
            self.remote_item.emitDataChanged()  # vuelve a medir y dibujar la fila, con o sin foquito

    def apply_icons(self) -> None:
        self.home_item.setIcon(0, themed("home"))
        self.favorites_item.setIcon(0, themed("favorites"))
        if not has_computer_view():
            self.root_item.setIcon(0, themed("drive"))

    def set_favorites(self, nodes: list[dict]) -> None:
        self.favorites_item.takeChildren()
        self._add_favorites(self.favorites_item, favorites_tree.clean(nodes)[0])
        self._set_indicator(self.favorites_item, self.favorites_item.childCount() > 0)
        self.favorites_item.setExpanded(True)

    def _add_favorites(self, parent: QTreeWidgetItem, nodes: list[dict]) -> None:
        """Las carpetas favoritas y las ramas. El resto no lleva a ninguna carpeta."""
        for node in nodes:
            what = favorites_tree.kind(node)
            if what == "branch":
                branch = self._node(parent, tr(node["label"]) if node.get("bar") else node["label"], "", self._folder_icon(), has_children=False)
                branch.setData(0, LOADED_ROLE, True)
                self._add_favorites(branch, node["children"])
                self._set_indicator(branch, branch.childCount() > 0)
            elif what == "folder":
                item = self._node(parent, node["label"], node["path"], self._folder_icon())
                item.setDisabled(node["path"] != COMPUTER_PATH and not os.path.isdir(node["path"]))

    def set_show_hidden(self, show_hidden: bool) -> None:
        if show_hidden == self.show_hidden:
            return
        self.show_hidden = show_hidden
        iterator = QTreeWidgetItemIterator(self)
        loaded = []
        while iterator.value():
            item = iterator.value()
            if item.data(0, LOADED_ROLE) and item.data(0, PATH_ROLE) and item is not self.home_item:
                loaded.append(item)
            iterator += 1
        if not has_computer_view():
            loaded.append(self.home_item)
        for item in loaded:
            if item.data(0, PATH_ROLE) != COMPUTER_PATH:
                item.setData(0, LOADED_ROLE, False)  # lo que despliegue hasta una carpeta oculta debe esperar a la lista nueva
                self._load(item)

    # ---------------------------------------------------------------- carga
    def _load(self, item: QTreeWidgetItem, then=None) -> None:
        if then is not None:
            self._waiting.setdefault(id(item), []).append(then)
        path, show_hidden = item.data(0, PATH_ROLE), self.show_hidden

        def finished(rows) -> None:
            self._calls.discard(call)
            if show_hidden != self.show_hidden and not self._disposed and shiboken6.isValid(item):
                self._load(item)  # se leyó con la opción de antes; lo que espera sigue esperando
                return
            self._loaded(item, rows)

        def failed(_exc) -> None:
            self._calls.discard(call)
            self._loaded(item, [])

        call = run_bg(lambda sink: _read_children(path, show_hidden, sink), finished, failed)
        self._calls.add(call)

    def _loaded(self, item: QTreeWidgetItem, rows: list) -> None:
        if self._disposed or not shiboken6.isValid(item):
            return
        self._merge(item, rows)
        item.setData(0, LOADED_ROLE, True)
        self._set_indicator(item, item.childCount() > 0)
        path = item.data(0, PATH_ROLE)
        if path != COMPUTER_PATH and len(self._watcher.directories()) < MAX_WATCHED and os.path.isdir(path):
            self._watcher.addPath(path)
        for then in self._waiting.pop(id(item), []):
            then()

    def _merge(self, item: QTreeWidgetItem, rows: list) -> None:
        """Actualiza los hijos sin recrear los que siguen: así conservan lo que ya tenían desplegado."""
        wanted = {os.path.normcase(row[1]) for row in rows}
        for position in range(item.childCount() - 1, -1, -1):
            if os.path.normcase(item.child(position).data(0, PATH_ROLE)) not in wanted:
                item.takeChild(position)
        existing = {os.path.normcase(item.child(i).data(0, PATH_ROLE)): item.child(i) for i in range(item.childCount())}
        for position, (label, path, has_children, is_drive) in enumerate(rows):
            child = existing.get(os.path.normcase(path))
            if child is None:
                icon = _icons.icon(QFileInfo(path)) if is_drive else self._folder_icon()
                child = QTreeWidgetItem([label])
                child.setData(0, PATH_ROLE, path)
                child.setData(0, LOADED_ROLE, False)
                child.setIcon(0, icon)
                child.setToolTip(0, path)
                item.insertChild(position, child)
            if not child.data(0, LOADED_ROLE) or child.childCount() == 0:
                self._set_indicator(child, has_children)

    def _item_expanded(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, LOADED_ROLE) and item.data(0, PATH_ROLE):
            self._load(item)

    def _directory_changed(self, path: str) -> None:
        self._pending_reload.add(os.path.normcase(os.path.normpath(path)))
        self._reload_timer.start()

    def _reload_pending(self) -> None:
        pending, self._pending_reload = self._pending_reload, set()
        iterator = QTreeWidgetItemIterator(self)
        matches = []
        while iterator.value():
            item = iterator.value()
            path = item.data(0, PATH_ROLE)
            if path and item.data(0, LOADED_ROLE) and os.path.normcase(os.path.normpath(path)) in pending:
                matches.append(item)
            iterator += 1
        for item in matches:
            self._load(item)

    # ---------------------------------------------------------------- arrastrar
    def _can_drag(self, item: QTreeWidgetItem | None) -> bool:
        """Solo las carpetas de verdad de las secciones de unidades y de dispositivos: no los accesos ni las propias unidades o dispositivos."""
        if item is None or item is self.root_item or item.parent() is self.root_item and has_computer_view() or item.data(0, DEVICE_ROLE):
            return False
        sections = {id(section) for section in (self.root_item, self.devices_item) if section is not None}
        ancestor = item.parent()
        while ancestor is not None and id(ancestor) not in sections:
            ancestor = ancestor.parent()
        return ancestor is not None and os.path.isdir(item.data(0, PATH_ROLE) or "")

    def startDrag(self, _actions) -> None:
        item = self.currentItem()
        if self._can_drag(item):
            start_drag(self, folder_mime(item.data(0, PATH_ROLE)), item.icon(0))

    def _mark_drop_item(self, item: QTreeWidgetItem | None) -> None:
        if item is self._drop_item:
            return
        if self._drop_item is not None and shiboken6.isValid(self._drop_item):
            self._drop_item.setData(0, Qt.ItemDataRole.BackgroundRole, None)
        self._drop_item = item
        self._expand_timer.stop()
        if item is not None:
            item.setBackground(0, QColor(theme.current().accent))
            self._expand_timer.start()

    def _expand_drop_item(self) -> None:
        """Pasar un rato por encima de una carpeta la despliega, para poder soltar en lo que tiene dentro."""
        if self._drop_item is not None and shiboken6.isValid(self._drop_item):
            self._drop_item.setExpanded(True)

    def _drop_target(self, event) -> QTreeWidgetItem | None:
        item = self.itemAt(event.position().toPoint())
        return item if item is not None and os.path.isdir(item.data(0, PATH_ROLE) or "") else None

    def dragEnterEvent(self, event) -> None:
        if self._decider.enter(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        item = self._drop_target(event)
        self._mark_drop_item(item)
        show_operation(event, self._decider.operation(event, item.data(0, PATH_ROLE)) if item is not None else None)

    def dragLeaveEvent(self, event) -> None:
        self._mark_drop_item(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        item = self._drop_target(event)
        self._mark_drop_item(None)
        self._decider.begin(event)
        operation = self._decider.operation(event, item.data(0, PATH_ROLE)) if item is not None else None
        signal = self.filesDroppedMenu if self._decider.take_right_button() else self.filesDropped
        if operation is None:
            event.ignore()
            return
        accept_drop(event)
        paths, target = self._decider.paths, item.data(0, PATH_ROLE)
        QTimer.singleShot(0, lambda: signal.emit(paths, target, operation == "move"))

    # ---------------------------------------------------------------- selección
    def _item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if item.data(0, DEVICE_ROLE) and not path:
            self.deviceActivated.emit(item.data(0, DEVICE_ROLE))
        elif path:
            self.pathActivated.emit(path)
        else:
            item.setExpanded(not item.isExpanded())

    def viewportEvent(self, event) -> bool:
        """Una descripción con otro texto se abre de nuevo en lugar de reutilizar la que está a la vista: al reutilizarla,
        la ventana conservaba a veces el alto del texto anterior, y la de un dispositivo, más larga, salía cortada."""
        if event.type() == QEvent.Type.ToolTip:
            item = self.itemAt(event.pos())
            text = item.toolTip(0) if item is not None else ""
            if not text:
                QToolTip.hideText()
                event.ignore()
                return True
            if QToolTip.isVisible() and QToolTip.text() != text:
                QToolTip.hideText()
            QToolTip.showText(event.globalPos(), text, self.viewport(), self.visualItemRect(item))
            return True
        return super().viewportEvent(event)

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is not None and item.data(0, DEVICE_ROLE):
            self.deviceMenuRequested.emit(item.data(0, DEVICE_ROLE), event.globalPos())
        else:
            super().contextMenuEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentItem() is not None:
            self._item_clicked(self.currentItem(), 0)
        else:
            super().keyPressEvent(event)

    def current_path(self) -> str:
        item = self.currentItem()
        return item.data(0, PATH_ROLE) if item is not None else ""

    def sync_to(self, path: str) -> None:
        """Despliega hasta `path` en la sección de unidades y lo marca. Lo más reciente manda:
        si se navega a otro sitio mientras tanto, el recorrido anterior se abandona."""
        self._sync_token += 1
        if is_results(path):
            return  # no es una carpeta: el árbol se queda donde estaba
        if remote.is_remote(path):
            self._show_server(remote.root(path))
            self._descend(self.remote_item, [target for _label, target in remote.crumbs(path)[1:]], self._sync_token)
            return
        self._show_server("")
        if path == COMPUTER_PATH:
            self._select(self.root_item)
            return
        anchor = self._anchor_for(path)
        if anchor is not None:
            # Dentro de lo que está marcado (lo pulsado en Inicio, en Favoritos o en un dispositivo, o una carpeta suya): se sigue ahí,
            # sin saltar a la misma carpeta dentro de «Sistema de archivos».
            item, base = anchor
            chain, accumulated = [], base
            for part in PurePath(os.path.relpath(path, base)).parts if not _same_path(path, base) else ():
                accumulated = os.path.join(accumulated, part)
                chain.append(accumulated)
            self._descend(item, chain, self._sync_token)
            return
        chain, accumulated = [], None
        for part in PurePath(path).parts:
            accumulated = PurePath(part) if accumulated is None else accumulated / part
            chain.append(str(accumulated))
        if not has_computer_view():
            chain = chain[1:]  # la raíz ya es la propia sección
        self._descend(self.root_item, chain, self._sync_token)

    def _anchor_for(self, path: str) -> tuple[QTreeWidgetItem, str] | None:
        """La carpeta marcada, o una de las que la contienen, si `path` es ella misma o está dentro."""
        item = self.currentItem()
        while item is not None:
            base = item.data(0, PATH_ROLE)
            if isinstance(base, str) and base and base != COMPUTER_PATH and not item.isHidden() and not remote.is_remote(base):
                if _same_path(path, base) or _inside(path, base):
                    return item, base
            item = item.parent()
        return None

    def _show_server(self, root: str) -> None:
        """Las carpetas del servidor de esa raíz, o con "", las del equipo."""
        current = self.remote_item.data(0, PATH_ROLE) if self.remote_item is not None else ""
        if current == root:
            return
        if self.remote_item is not None:
            self.takeTopLevelItem(self.indexOfTopLevelItem(self.remote_item))
            self.remote_item = None
            self.led_lit = None
        for item in self._sections():
            item.setHidden(bool(root) or (item is self.devices_item and item.childCount() == 0))
        if root:
            server = remote.server_of(root)
            user, host, port = remote.server_parts(server)
            label = f"{user}@{host}" + ("" if port == remote.DEFAULT_PORT else f":{port}")
            self.remote_item = self._node(self, label, root, themed("remote"))
            self.remote_item.setToolTip(0, tr("En el servidor {server}", server=server))
            self.remote_item.setExpanded(True)

    def _descend(self, item: QTreeWidgetItem, chain: list[str], token: int) -> None:
        if token != self._sync_token or self._disposed or not shiboken6.isValid(item):
            return
        if not chain:
            self._select(item)
            return
        if not item.data(0, LOADED_ROLE):
            self._load(item, lambda: self._descend(item, chain, token))
            return
        for position in range(item.childCount()):
            child = item.child(position)
            if _same_path(child.data(0, PATH_ROLE), chain[0]):
                item.setExpanded(True)
                self._descend(child, chain[1:], token)
                return
        self._select(item)  # oculta o dentro de un comprimido: se queda en lo más cercano

    def _select(self, item: QTreeWidgetItem) -> None:
        self.setCurrentItem(item)
        self.scrollToItem(item)
        self.horizontalScrollBar().setValue(0)

    def dispose(self) -> None:
        self._disposed = True
        self._sync_token += 1
        self._reload_timer.stop()
        for call in list(self._calls):
            call.cancel()

