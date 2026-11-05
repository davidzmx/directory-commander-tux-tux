"""Editor de Markdown con doble vista: el texto a la izquierda y cómo queda a la derecha, al momento."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QFontDatabase, QIcon, QKeySequence, QTextCursor
from PySide6.QtWidgets import QMainWindow, QMessageBox, QPlainTextEdit, QSplitter, QTextBrowser, QToolBar

from .code_view import CodeHighlighter, MarkdownView, lexer_for
from .icons import app_icon, themed
from ..core.i18n import tr

PREVIEW_DELAY_MS = 250
MAX_EDITABLE = 8 * 1024 * 1024


class MarkdownEditor(QMainWindow):
    """Se guarda con Ctrl+S, en UTF-8 y con los saltos de línea que tenía el archivo. Al cerrar con cambios sin guardar, pregunta."""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(app_icon())
        self.path = os.path.normpath(path)
        self.newline = "\n"
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.highlighter = CodeHighlighter(self.editor.document())
        self.preview = MarkdownView()
        # La vista muestra el texto que se edita: un enlace a otro Markdown lo abre en otro editor, sin cambiar esta.
        self.preview.handles_markdown_links = False
        self.preview.markdownLinkClicked.connect(self._open_linked)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.preview)
        self.splitter.setSizes([1, 1])
        self.setCentralWidget(self.splitter)
        self._timer = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self.update_preview)
        self._build_toolbar()
        self.statusBar()
        self.load()
        # Solo al cambiar el texto: el coloreado también avisa de «cambios» y recargaba la vista sin parar, con la barra de desplazamiento volviendo atrás.
        self.editor.document().contentsChange.connect(lambda _position, removed, added: (removed or added) and self._timer.start())
        self.editor.document().modificationChanged.connect(lambda _changed: self._update_title())
        self.editor.verticalScrollBar().valueChanged.connect(self._follow_scroll)
        self.resize(1100, 720)

    # ---------------------------------------------------------------- barra
    def _build_toolbar(self) -> None:
        bar = QToolBar(tr("Editor"))
        bar.setMovable(False)
        self.addToolBar(bar)
        self.save_action = self._action(bar, tr("Guardar"), "save", QKeySequence.StandardKey.Save, self.save)
        bar.addSeparator()
        for label, shortcut, before, after in ((tr("Negrita"), "Ctrl+B", "**", "**"), (tr("Cursiva"), "Ctrl+I", "*", "*"),
                                               (tr("Código"), "Ctrl+`", "`", "`"), (tr("Enlace"), "Ctrl+K", "[", "](https://)")):
            self._action(bar, label, "", shortcut, lambda b=before, a=after: self.wrap_selection(b, a))
        for label, prefix in ((tr("Título"), "# "), (tr("Lista"), "- "), (tr("Cita"), "> ")):
            self._action(bar, label, "", None, lambda p=prefix: self.prefix_lines(p))
        bar.addSeparator()
        self.view_actions = {}
        for key, label in (("both", tr("Las dos")), ("text", tr("Solo el texto")), ("preview", tr("Solo la vista"))):
            action = self._action(bar, label, "", None, lambda k=key: self.show_side(k))
            action.setCheckable(True)
            self.view_actions[key] = action
        self.view_actions["both"].setChecked(True)

    def _action(self, bar: QToolBar, label: str, icon: str, shortcut, slot) -> QAction:
        action = QAction(themed(icon) if icon else QIcon(), label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        bar.addAction(action)
        return action

    # ---------------------------------------------------------------- archivo
    def load(self) -> None:
        with open(self.path, "rb") as fin:
            raw = fin.read(MAX_EDITABLE + 1)
        if len(raw) > MAX_EDITABLE:
            raise OSError(tr("Es demasiado grande para editarlo aquí"))
        text = raw.decode("utf-8")  # otra codificación: mejor no abrirlo que estropearlo al guardar
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.editor.setPlainText(text.replace("\r\n", "\n"))
        self.editor.document().setModified(False)
        self.highlighter.set_lexer(lexer_for("x.md"))
        self.update_preview()
        self._update_title()

    def save(self) -> bool:
        text = self.editor.toPlainText().replace("\n", self.newline)
        temporary = self.path + ".guardando~"
        try:
            with open(temporary, "w", encoding="utf-8", newline="") as fout:
                fout.write(text)
            os.replace(temporary, self.path)  # de una vez: si algo falla a medias, el original sigue entero
        except OSError as exc:
            QMessageBox.warning(self, tr("Guardar"), tr("No se pudo guardar {name}: {exc}", name=os.path.basename(self.path), exc=exc))
            return False
        self.editor.document().setModified(False)
        self.statusBar().showMessage(tr("Guardado"), 3000)
        return True

    def _open_linked(self, path: str) -> None:
        owner = self.parent()
        if owner is not None and hasattr(owner, "open_markdown_editor"):
            owner.open_markdown_editor(path)
        else:
            MarkdownEditor(path, owner).show()

    def _update_title(self) -> None:
        mark = "*" if self.editor.document().isModified() else ""
        self.setWindowTitle(f"{mark}{os.path.basename(self.path)} — {tr('Editor de Markdown')}")

    def closeEvent(self, event) -> None:
        if self.editor.document().isModified():
            answer = QMessageBox.question(self, tr("Editor de Markdown"), tr("{name} tiene cambios sin guardar. ¿Guardarlos?", name=os.path.basename(self.path)),
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel or (answer == QMessageBox.StandardButton.Save and not self.save()):
                event.ignore()
                return
        super().closeEvent(event)

    # ---------------------------------------------------------------- vista
    def update_preview(self) -> None:
        self.preview.show_markdown(self.editor.toPlainText(), os.path.dirname(self.path))  # conserva dónde estaba: al escribir no vuelve arriba

    def _source_headings(self) -> list[int]:
        """Las líneas del texto que son títulos, sin contar las de los bloques de código."""
        found, fence = [], False
        block = self.editor.document().begin()
        while block.isValid():
            text = block.text()
            if text.lstrip().startswith(("```", "~~~")):
                fence = not fence
            elif not fence and re.match(r"\s{0,3}#{1,6}\s", text):
                found.append(block.blockNumber())
            block = block.next()
        return found

    def _preview_headings(self) -> list[float]:
        found = []
        document = self.preview.document()
        block = document.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0:
                found.append(document.documentLayout().blockBoundingRect(block).y())
            block = block.next()
        return found

    def _follow_scroll(self, _value: int) -> None:
        """La vista va a la misma sección que el texto: al título que está encima de la primera línea visible, y dentro de la sección,
        en proporción. Con imágenes o bloques grandes, una proporción del documento entero se desajustaba mucho."""
        target = self.preview.verticalScrollBar()
        top_line = self.editor.firstVisibleBlock().blockNumber()
        lines, places = self._source_headings(), self._preview_headings()
        total_lines = self.editor.document().blockCount()
        height = self.preview.document().size().height()
        if len(lines) != len(places) or not lines:
            source = self.editor.verticalScrollBar()
            if source.maximum() > 0:
                target.setValue(round(source.value() / source.maximum() * target.maximum()))
            return
        anchors = [(0, 0.0)] + list(zip(lines, places)) + [(total_lines, height)]
        for (line, place), (next_line, next_place) in zip(anchors, anchors[1:]):
            if line <= top_line < next_line or next_line == total_lines:
                share = (top_line - line) / max(1, next_line - line)
                target.setValue(round(place + share * (next_place - place)))
                return

    def show_side(self, key: str) -> None:
        for name, action in self.view_actions.items():
            action.setChecked(name == key)
        self.editor.setVisible(key != "preview")
        self.preview.setVisible(key != "text")

    # ---------------------------------------------------------------- formato
    def wrap_selection(self, before: str, after: str) -> None:
        cursor = self.editor.textCursor()
        selected = cursor.selectedText()
        cursor.insertText(f"{before}{selected}{after}")
        if not selected:
            cursor.movePosition(QTextCursor.MoveOperation.Left, n=len(after))
            self.editor.setTextCursor(cursor)

    def prefix_lines(self, prefix: str) -> None:
        """Pone el prefijo al principio de cada línea seleccionada, o de la línea donde está el cursor."""
        cursor = self.editor.textCursor()
        start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        block = self.editor.document().findBlock(start)
        last = self.editor.document().findBlock(end)
        cursor.beginEditBlock()
        while block.isValid():
            line = QTextCursor(block)
            line.insertText(prefix)
            if block == last:
                break
            block = block.next()
        cursor.endEditBlock()
