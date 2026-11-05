"""Un JSON en su propia ventana: como árbol desplegable, ordenado y coloreado, o tal cual. Se busca por clave o por valor."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFontDatabase, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QStackedWidget, QToolBar

from .code_view import CodeHighlighter, JsonTree, json_lexer, json_path, pretty_json
from .icons import app_icon, themed
from ..core.i18n import tr

MAX_JSON = 64 * 1024 * 1024


def _walk(value, path: list):
    """Cada elemento, en el orden en que se ve en el árbol: (ruta, clave, valor)."""
    pairs = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, element in pairs:
        step = [*path, key]
        yield step, key, element
        yield from _walk(element, step)


class JsonViewer(QMainWindow):
    """Solo para ver: no cambia el archivo."""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(app_icon())
        self.path = os.path.normpath(path)
        with open(self.path, "rb") as fin:
            raw = fin.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise OSError(tr("Es demasiado grande para verlo aquí"))
        self.source = raw.decode("utf-8-sig", "replace")
        self.data, self.formatted = pretty_json(self.source)  # ValueError si no es un JSON válido
        self.tree = JsonTree()
        self.tree.show_data(self.data)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = CodeHighlighter(self.text.document())
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.text)
        self.setCentralWidget(self.stack)
        self._found_at = -1
        bar = QToolBar(tr("JSON"))
        bar.setMovable(False)
        self.addToolBar(bar)
        self.modes: dict[str, QAction] = {}
        for key, label in (("tree", tr("Árbol")), ("formatted", tr("Formateado")), ("source", tr("Original"))):
            action = bar.addAction(label, lambda k=key: self.show_mode(k))
            action.setCheckable(True)
            self.modes[key] = action
        bar.addSeparator()
        bar.addAction(tr("Desplegar todo"), self.tree.fill_all)
        bar.addAction(tr("Plegar todo"), self._collapse)
        bar.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar una clave o un valor"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(280)
        self.search.returnPressed.connect(self.find_next)
        self.search.textChanged.connect(lambda _text: setattr(self, "_found_at", -1))
        bar.addWidget(self.search)
        bar.addAction(themed("search"), tr("Siguiente"), self.find_next)
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(find)
        self.setWindowTitle(f"{os.path.basename(self.path)} — {tr('Visor de JSON')}")
        self.statusBar().showMessage(tr("{kind} con {count} elementos", kind=tr("Objeto") if isinstance(self.data, dict) else tr("Lista") if isinstance(self.data, list) else tr("Valor"),
                                        count=sum(1 for _item in _walk(self.data, []))))
        self.show_mode("tree")
        self.resize(900, 760)

    def show_mode(self, key: str) -> None:
        for name, action in self.modes.items():
            action.setChecked(name == key)
        if key == "tree":
            self.stack.setCurrentWidget(self.tree)
            return
        formatted = key == "formatted"
        self.highlighter.set_lexer(None)
        self.text.setPlainText(self.formatted if formatted else self.source)
        self.highlighter.set_lexer(json_lexer())
        self.stack.setCurrentWidget(self.text)

    def _collapse(self) -> None:
        self.tree.collapseAll()
        root = self.tree.topLevelItem(0)
        if root is not None:
            root.setExpanded(True)

    def find_next(self) -> bool:
        """La siguiente clave o valor que contiene lo escrito: se despliega hasta él y se marca. Al llegar al final, vuelve a empezar."""
        needle = self.search.text().strip().casefold()
        if not needle:
            return False
        self.show_mode("tree")
        found = [path for path, key, value in _walk(self.data, [])
                 if needle in str(key).casefold() or (not isinstance(value, (dict, list)) and needle in json.dumps(value, ensure_ascii=False).casefold())]
        if not found:
            self.statusBar().showMessage(tr("No se encontró «{text}»", text=self.search.text()), 4000)
            return False
        self._found_at = (self._found_at + 1) % len(found)
        item = self.tree.item_for(found[self._found_at])
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self.statusBar().showMessage(tr("{position} de {count}", position=self._found_at + 1, count=len(found)), 4000)
        return True

    def value_at(self, path: list):
        value = self.data
        for step in path:
            value = value[step]
        return value

    def _menu(self, position) -> None:
        item = self.tree.itemAt(position)
        path = item.data(0, JsonTree.PATH_ROLE) if item is not None else None
        if path is None:
            return
        value = self.value_at(path)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        menu = QMenu(self)
        menu.addAction(themed("copy"), tr("Copiar el valor"), lambda: QGuiApplication.clipboard().setText(text))
        menu.addAction(tr("Copiar la ruta"), lambda: QGuiApplication.clipboard().setText(json_path(path)))
        menu.exec(self.tree.viewport().mapToGlobal(position))
