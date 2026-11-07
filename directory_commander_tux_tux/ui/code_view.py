"""Código con resaltado de sintaxis, Markdown formateado y JSON como texto ordenado o como árbol.

El resaltado lo hace Pygments, que reconoce cientos de lenguajes por el nombre del archivo o por su contenido. El texto se
divide en piezas en segundo plano, y cada línea se pinta con lo que le toca: así un archivo grande no congela la ventana."""
from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QImageReader, QPainter, QPen, QPixmap, QSyntaxHighlighter, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat,
                           QTextFrameFormat, QTextLength, QTextTable)
from PySide6.QtWidgets import QTextBrowser, QTreeWidget, QTreeWidgetItem

from . import theme
from .background import run_bg
from ..core.i18n import tr

try:
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
    from pygments.styles import get_style_by_name
    from pygments.util import ClassNotFound
except ImportError:  # sin Pygments, el código se ve sin colores
    get_lexer_by_name = get_lexer_for_filename = guess_lexer = get_style_by_name = None
    ClassNotFound = Exception

MARKDOWN_EXTENSIONS = {"md", "markdown", "mdown", "mkd"}
JSON_EXTENSIONS = {"json", "geojson", "jsonc", "ipynb", "webmanifest", "har"}
MAX_HIGHLIGHT = 2 * 1024 * 1024  # más grande, se ve sin colores: dividirlo tardaría demasiado
MAX_TREE_CHILDREN = 2000  # de cada objeto o lista: lo demás, en una fila «…»
STYLES = {"dark": "one-dark", "light": "default"}
HIGHLIGHT_DELAY_MS = 350


def lexer_for(name: str, text: str = ""):
    """El lenguaje del archivo, por su nombre o, si no lo dice, por cómo empieza. None si no se sabe o no hay Pygments."""
    if get_lexer_for_filename is None:
        return None
    try:
        return get_lexer_for_filename(name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        pass
    if text.startswith("#!"):
        try:
            return guess_lexer(text[:4000], stripnl=False, ensurenl=False)
        except ClassNotFound:
            return None
    return None


def json_lexer():
    if get_lexer_by_name is None:
        return None
    return get_lexer_by_name("json", stripnl=False, ensurenl=False)


def tokenize(lexer, text: str) -> dict[int, list[tuple[int, int, object]]]:
    """Por línea, (columna, largo, tipo de pieza). Sin las piezas de texto normal, que no llevan color. Una pieza de varias líneas,
    como un comentario largo, se reparte entre ellas."""
    from pygments.token import Text, Whitespace
    starts, position = [0], text.find("\n")
    while position >= 0:
        starts.append(position + 1)
        position = text.find("\n", position + 1)
    lines: dict[int, list] = {}
    line = 0
    for position, kind, value in lexer.get_tokens_unprocessed(text):
        if not value or kind in (Text, Whitespace):
            continue
        while line + 1 < len(starts) and starts[line + 1] <= position:
            line += 1
        end, current = position + len(value), line
        while position < end:
            line_end = starts[current + 1] - 1 if current + 1 < len(starts) else len(text)
            if min(end, line_end) > position:
                lines.setdefault(current, []).append((position - starts[current], min(end, line_end) - position, kind))
            if current + 1 >= len(starts):
                break
            current += 1
            position = starts[current]
    return lines


class CodeHighlighter(QSyntaxHighlighter):
    """Pinta cada línea con las piezas que le corresponden. Se vuelve a dividir el texto al cambiarlo, en segundo plano y con un poco de retraso."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self.lexer = None
        self._lines: dict[int, list[tuple[int, int, object]]] = {}
        self._formats: dict[object, QTextCharFormat] = {}
        self._style_name = ""
        self._version = 0
        self._timer = QTimer(self, singleShot=True, interval=HIGHLIGHT_DELAY_MS)
        self._timer.timeout.connect(self._tokenize)
        # Solo los cambios de texto: pintar los colores también avisa de un «cambio», sin letras quitadas ni añadidas, y reaccionar a
        # ese aviso volvía a colorear sin fin.
        document.contentsChange.connect(lambda _position, removed, added: (removed or added) and self._changed())

    def set_lexer(self, lexer) -> None:
        self.lexer = lexer
        self._lines = {}
        self._tokenize()

    def _changed(self) -> None:
        if self.lexer is not None:
            self._timer.start()

    def _format(self, kind) -> QTextCharFormat:
        name = STYLES.get(theme.current().name, "default")
        if name != self._style_name:
            self._formats, self._style_name = {}, name
        found = self._formats.get(kind)
        if found is None:
            found = QTextCharFormat()
            try:
                style = get_style_by_name(name).style_for_token(kind)
            except (ClassNotFound, TypeError):
                style = {}
            if style.get("color"):
                found.setForeground(QColor("#" + style["color"]))
            if style.get("bold"):
                found.setFontWeight(QFont.Weight.Bold)
            if style.get("italic"):
                found.setFontItalic(True)
            self._formats[kind] = found
        return found

    def _tokenize(self) -> None:
        self._version += 1
        version, lexer = self._version, self.lexer
        text = self.document().toPlainText()
        if lexer is None or len(text) > MAX_HIGHLIGHT:
            self._lines = {}
            self.rehighlight()
            return
        run_bg(lambda _sink: tokenize(lexer, text), lambda lines: self._apply(version, lines))

    def _apply(self, version: int, lines) -> None:
        if version == self._version:  # si el texto ha cambiado desde entonces, ya viene otra división
            self._lines = lines
            self.rehighlight()

    def highlightBlock(self, _text: str) -> None:
        for start, length, kind in self._lines.get(self.currentBlock().blockNumber(), ()):
            self.setFormat(start, length, self._format(kind))


def pretty_json(text: str) -> tuple[object, str]:
    """El JSON ordenado con sangría, y su contenido. Un error dice dónde está el fallo."""
    data = json.loads(text)
    return data, json.dumps(data, indent=2, ensure_ascii=False)


def _short(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if len(value) <= 300 else json.dumps(value[:300], ensure_ascii=False) + "…"
    if isinstance(value, dict):
        return tr("{count} claves", count=len(value))
    if isinstance(value, list):
        return tr("{count} elementos", count=len(value))
    return json.dumps(value)


def _kind(value) -> str:
    return {dict: tr("objeto"), list: tr("lista"), str: tr("texto"), bool: tr("sí/no"), type(None): "null"}.get(type(value), tr("número"))


_VALUE_COLORS = {str: "#1f5fbf", bool: "#d4a017", type(None): "#8a8f98"}  # los números, verdes
_icons: dict[tuple, QIcon] = {}


def _icon(kind: str, color: str) -> QIcon:
    """`{}` para un objeto, `[]` para una lista y un cuadrito del color de su tipo para un valor."""
    key = (kind, color)
    if key not in _icons:
        picture = QPixmap(16, 16)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "value":
            painter.fillRect(QRectF(5, 5, 6, 6), QColor(color))
        else:
            font = QFont()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QRectF(0, 0, 16, 16), Qt.AlignmentFlag.AlignCenter, "{ }" if kind == "object" else "[ ]")
        painter.end()
        _icons[key] = QIcon(picture)
    return _icons[key]


class JsonTree(QTreeWidget):
    """El JSON como árbol desplegable, con líneas entre los niveles: cada objeto con `{ }`, cada lista con `[ ]` y cada valor como
    «clave : valor». Los hijos se crean al desplegar: un archivo con cientos de miles de elementos se abre al momento."""

    PATH_ROLE = Qt.ItemDataRole.UserRole + 1  # la ruta del elemento dentro del JSON, como lista de claves e índices

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(18)
        self.setRootIsDecorated(True)
        self.itemExpanded.connect(self._fill)
        self._values: dict[int, object] = {}

    def show_data(self, data) -> None:
        self.clear()
        self._values = {}
        root = self._item(self.invisibleRootItem(), "JSON", data, [])
        root.setExpanded(True)

    def _item(self, parent, key: str, value, path: list) -> QTreeWidgetItem:
        if isinstance(value, (dict, list)):
            text = key
            icon = _icon("object" if isinstance(value, dict) else "array", theme.current().dir_text)
        else:
            text = f"{key} : {_short(value)}"
            icon = _icon("value", _VALUE_COLORS.get(type(value), "#2e9e44"))
        item = QTreeWidgetItem(parent, [text])
        item.setIcon(0, icon)
        item.setData(0, self.PATH_ROLE, path)
        tip = _kind(value) + (f" · {_short(value)}" if isinstance(value, (dict, list)) else "")
        if isinstance(value, str) and len(value) > 300:
            tip += "\n" + value[:4000]
        item.setToolTip(0, tip)
        if isinstance(value, (dict, list)) and value:
            self._values[id(item)] = value
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _fill(self, item: QTreeWidgetItem) -> None:
        value = self._values.pop(id(item), None)
        if value is None:
            return
        path = item.data(0, self.PATH_ROLE) or []
        pairs = value.items() if isinstance(value, dict) else enumerate(value)
        for count, (key, element) in enumerate(pairs):
            if count == MAX_TREE_CHILDREN:
                QTreeWidgetItem(item, ["… " + tr("y {count} más", count=len(value) - MAX_TREE_CHILDREN)])
                break
            self._item(item, str(key), element, [*path, key])

    def fill_all(self, item: QTreeWidgetItem | None = None, budget: list | None = None) -> None:
        """Despliega todo lo de debajo, hasta un máximo de filas: con un archivo enorme, se queda en las primeras."""
        budget = budget if budget is not None else [MAX_TREE_CHILDREN * 10]
        item = item or self.topLevelItem(0)
        if item is None or budget[0] <= 0:
            return
        item.setExpanded(True)
        budget[0] -= item.childCount()
        for row in range(item.childCount()):
            child = item.child(row)
            if child.childIndicatorPolicy() == QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator or child.childCount():
                self.fill_all(child, budget)

    def item_for(self, path: list) -> QTreeWidgetItem | None:
        """La fila de esa ruta, creando y desplegando lo que haga falta por el camino."""
        item = self.topLevelItem(0)
        for step in path:
            if item is None:
                return None
            item.setExpanded(True)
            item = next((item.child(row) for row in range(item.childCount()) if (item.child(row).data(0, self.PATH_ROLE) or [None])[-1] == step), None)
        return item

    def drawBranches(self, painter, rect, index) -> None:
        """Las líneas punteadas que unen cada elemento con su padre, y un cuadro con + o − en los que se despliegan."""
        indent = self.indentation()
        color = QColor(theme.current().dim)
        painter.save()
        painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
        levels = []
        parent = index
        while parent.isValid():
            levels.append(parent)
            parent = parent.parent()
        levels.reverse()  # de la raíz a esta fila
        middle_y = rect.center().y()
        for depth, step in enumerate(levels):
            x = rect.left() + depth * indent + indent // 2
            last = not step.sibling(step.row() + 1, 0).isValid()
            if step == index:
                painter.drawLine(x, rect.top() if depth else middle_y, x, middle_y if last else rect.bottom())
                painter.drawLine(x, middle_y, x + indent // 2, middle_y)
                if self.model().hasChildren(index) or self.itemFromIndex(index).childIndicatorPolicy() == QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator:
                    box = QRectF(x - 4.5, middle_y - 4.5, 9, 9)
                    painter.setPen(QPen(color, 1))
                    painter.fillRect(box, QColor(theme.current().panel))
                    painter.drawRect(box)
                    painter.drawLine(int(x - 2), middle_y, int(x + 2), middle_y)
                    if not self.isExpanded(index):
                        painter.drawLine(x, middle_y - 2, x, middle_y + 2)
            elif not last:
                painter.drawLine(x, rect.top(), x, rect.bottom())
        painter.restore()

    def item_texts(self, item: QTreeWidgetItem | None = None) -> list[str]:
        """Lo que se ve bajo una fila (o bajo la raíz), para las pruebas y para copiar."""
        parent = item or self.invisibleRootItem()
        return [parent.child(row).text(0) for row in range(parent.childCount())]


def json_path(path: list) -> str:
    """`pages[0].nodes[2].id`: cómo se llega a ese valor desde la raíz."""
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else (f".{step}" if text else str(step))
    return text or "$"


_VOID_TAG = re.compile(r"<(br|hr|img|wbr|input|meta|link|source|col|area|embed|param|track)\b([^<>]*?)\s*(?<!/)>", re.IGNORECASE)
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)*?\1")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def markdown_for_qt(text: str) -> str:
    """El Markdown con las etiquetas HTML que no se cierran (`<br>`, `<img …>`) escritas como `<br/>`. Qt cuenta las etiquetas
    abiertas y, con una que nunca se cierra, cree que todo lo que sigue es HTML y se salta el texto: solo quedaban los trozos de código.
    El código, en bloques o entre comillas invertidas, se deja como está."""
    lines, fence = [], ""
    for line in text.split("\n"):
        found = _FENCE.match(line)
        if fence:
            if found and found.group(1)[0] == fence[0] and len(found.group(1)) >= len(fence):
                fence = ""
            lines.append(line)
            continue
        if found:
            fence = found.group(1)
            lines.append(line)
            continue
        pieces, last = [], 0
        for code in _CODE_SPAN.finditer(line):
            pieces.append(_VOID_TAG.sub(r"<\1\2/>", line[last:code.start()]))
            pieces.append(code.group(0))
            last = code.end()
        pieces.append(_VOID_TAG.sub(r"<\1\2/>", line[last:]))
        lines.append("".join(pieces))
    return "\n".join(lines)


def heading_anchor(text: str) -> str:
    """El ancla que GitHub da a un título: «Estilos: una disposición» → «estilos-una-disposición»."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def is_markdown(name: str) -> bool:
    return os.path.splitext(name)[1][1:].lower() in MARKDOWN_EXTENSIONS


def is_json(name: str) -> bool:
    return os.path.splitext(name)[1][1:].lower() in JSON_EXTENSIONS


class MarkdownView(QTextBrowser):
    """Markdown dibujado como en GitHub, dentro de lo que permite Qt: las imágenes se reducen al ancho disponible (en una tabla, al de
    su columna), los bloques de código y el código suelto llevan fondo, y las tablas, bordes y relleno. Se vuelve a ajustar al cambiar de tamaño.

    Los enlaces: a otro Markdown, se muestra aquí (o, si alguien escucha `markdownLinkClicked`, lo decide él); a un título del mismo
    documento (`#estilos`), se va a él; lo demás, con su programa."""

    markdownLinkClicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._follow)
        self.path = ""  # el archivo que se muestra, para los enlaces relativos
        self.handles_markdown_links = True
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 1.15)  # el texto de GitHub es algo mayor que el de los menús
        self.setFont(font)
        self._natural: dict[str, tuple[int, int]] = {}  # tamaño original de cada imagen, por su nombre
        self._fit_timer = QTimer(self, singleShot=True, interval=120)
        self._fit_timer.timeout.connect(self.fit_images)

    def show_file(self, path: str) -> None:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        self.path = path
        self.verticalScrollBar().setValue(0)
        self.show_markdown(text, os.path.dirname(path))

    def _follow(self, url: QUrl) -> None:
        if url.scheme() in ("", "file") and not url.path() and url.fragment():
            self.go_to_heading(url.fragment())
            return
        target = self.document().baseUrl().resolved(url) if url.isRelative() else url
        local = os.path.normpath(target.toLocalFile()) if target.isLocalFile() else ""  # Qt la da con «/», también en Windows
        if local and is_markdown(local) and os.path.isfile(local):
            if self.handles_markdown_links:
                self.show_file(local)
                if target.fragment():
                    self.go_to_heading(target.fragment())
            self.markdownLinkClicked.emit(local)
            return
        from .open_external import open_externally
        open_externally(local or target.toString())

    def go_to_heading(self, anchor: str) -> bool:
        """Como GitHub: el ancla de un título es su texto en minúsculas, sin signos y con guiones en lugar de espacios."""
        wanted = anchor.lower()
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0 and heading_anchor(block.text()) == wanted:
                self.verticalScrollBar().setValue(int(self.document().documentLayout().blockBoundingRect(block).y()))
                return True
            block = block.next()
        return False

    def show_markdown(self, text: str, folder: str) -> None:
        bar = self.verticalScrollBar()
        position = bar.value()
        self.document().setBaseUrl(QUrl.fromLocalFile(folder.rstrip(os.sep) + os.sep))  # sus imágenes, junto a él
        self.setMarkdown(markdown_for_qt(text))
        self._style()
        self.fit_images()
        bar.setValue(position)

    def _colors(self) -> dict[str, str]:
        dark = theme.current().name == "dark"
        return {"code": "#161b22" if dark else "#f6f8fa", "inline": "#2d333b" if dark else "#eff1f3", "border": "#3d444d" if dark else "#d1d9e0"}

    def _style(self) -> None:
        colors = self._colors()
        document = self.document()
        cursor = QTextCursor(document)
        block = document.begin()
        changes = []
        while block.isValid():
            form = block.blockFormat()
            code_block = form.hasProperty(QTextFormat.Property.BlockCodeFence) or form.nonBreakableLines()
            level = form.headingLevel()
            if code_block:
                form.setBackground(QColor(colors["code"]))
                form.setLeftMargin(max(form.leftMargin(), 8))
            elif level:
                # Qt solo da al primer trozo el tamaño y la negrita del título: lo de detrás de un tachado o de un código salía como texto normal.
                first = block.begin().fragment().charFormat() if not block.begin().atEnd() else None
                if first is not None:
                    whole = QTextCharFormat()
                    whole.setProperty(QTextFormat.Property.FontSizeAdjustment, first.property(QTextFormat.Property.FontSizeAdjustment) or 0)
                    whole.setFontWeight(QFont.Weight.Bold)
                    changes.append(("merge", (block.position(), block.length() - 1), whole))
                form.setTopMargin(22 if level <= 2 else 16)
                form.setBottomMargin(12 if level <= 2 else 8)
                if level <= 2:  # la línea debajo de los títulos grandes
                    form.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth, QTextLength(QTextLength.Type.PercentageLength, 100))
            elif block.textList() is not None:
                form.setLineHeight(130, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                form.setBottomMargin(3)
            else:
                form.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                form.setBottomMargin(12)
            changes.append(("block", block.position(), form))
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                char = fragment.charFormat()
                if not code_block and char.fontFixedPitch() and not char.isImageFormat():
                    char.setBackground(QColor(colors["inline"]))
                    changes.append(("char", (fragment.position(), fragment.length()), char))
                iterator += 1
            block = block.next()
        for kind, where, form in changes:
            if kind == "block":
                cursor.setPosition(where)
                cursor.setBlockFormat(form)
            elif kind == "merge":
                cursor.setPosition(where[0])
                cursor.setPosition(where[0] + where[1], QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(form)
            else:
                cursor.setPosition(where[0])
                cursor.setPosition(where[0] + where[1], QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(form)
        for frame in document.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                table = frame.format()
                table.setBorder(1)
                table.setBorderBrush(QColor(colors["border"]))
                table.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                table.setBorderCollapse(True)
                table.setCellPadding(6)
                table.setCellSpacing(0)
                table.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                frame.setFormat(table)

    def _natural_size(self, name: str) -> tuple[int, int]:
        if name not in self._natural:
            url = self.document().baseUrl().resolved(QUrl(name))
            size = QImageReader(url.toLocalFile()).size() if url.isLocalFile() else None
            self._natural[name] = (size.width(), size.height()) if size is not None and size.isValid() else (0, 0)
        return self._natural[name]

    def fit_images(self) -> None:
        """Cada imagen, como mucho del ancho que tiene: el de la vista o, dentro de una tabla, el de su columna."""
        document = self.document()
        room = max(60, self.viewport().width() - 2 * int(document.documentMargin()) - 4)
        cursor = QTextCursor(document)
        changes = []
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.charFormat().isImageFormat():
                    image = fragment.charFormat().toImageFormat()
                    width, height = self._natural_size(image.name())
                    cursor.setPosition(fragment.position())
                    table = cursor.currentTable()
                    space = room // table.columns() - 16 if table is not None else room
                    if width and height:
                        shown = min(width, max(40, space))
                        changes.append((fragment.position(), fragment.length(), image, shown, round(height * shown / width)))
                iterator += 1
            block = block.next()
        for position, length, image, width, height in changes:
            if round(image.width()) == width:
                continue
            image.setWidth(width)
            image.setHeight(height)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(image)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_timer.start()
