"""Bloc de notas rápido desplegable para fragmentos de texto, comandos y rutas."""
from __future__ import annotations

import html
import json
import os
import re

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QKeySequence, QMouseEvent,
                           QPainter, QShortcut, QTextCharFormat, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QSizeGrip, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import notes
from ..core.i18n import tr
from . import theme
from .icons import themed


class SearchHighlightDelegate(QStyledItemDelegate):
    """Resalta en la lista las coincidencias del texto de búsqueda."""

    def __init__(self, filter_callback, parent=None) -> None:
        super().__init__(parent)
        self.filter_callback = filter_callback

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        query = self.filter_callback().strip()
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not query or query.lower() not in text.lower():
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        t = theme.current()
        bg = "#7c5e10" if t.name == "dark" else "#fff59d"
        fg = "#ffffff" if t.name == "dark" else "#000000"

        escaped_query = html.escape(query)
        escaped_text = html.escape(text)
        pattern = re.compile(re.escape(escaped_query), re.IGNORECASE)
        highlighted = pattern.sub(f'<span style="background-color:{bg}; color:{fg}; font-weight:bold;">\\g<0></span>', escaped_text)

        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        text_color = opt.palette.text().color().name()
        if opt.state & QStyle.StateFlag.State_Selected:
            text_color = t.sel_text
        doc.setHtml(f'<span style="color:{text_color};">{highlighted}</span>')

        painter.save()
        painter.translate(opt.rect.topLeft() + QPoint(4, 2))
        painter.setClipRect(QRectF(0, 0, opt.rect.width() - 8, opt.rect.height() - 4))
        doc.drawContents(painter)
        painter.restore()


class NotesPopup(QDialog):
    """Ventana emergente tipo popup redimensionable anclada a la barra de herramientas."""

    def __init__(self, config_dir: str, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.config_dir = config_dir
        self.notes: list[notes.NoteItem] = []
        self._current_id: str | None = None
        self._loading = False
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()
        self._pending_selection = ""
        self._host_widget: QWidget | None = None
        self._dialog_open: bool = False

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setMinimumSize(440, 280)

        # Cargar tamaño guardado
        saved_size = self._load_saved_size()
        self.resize(saved_size or QSize(540, 380))

        # Temporizador para auto-guardado
        self._save_timer = QTimer(self, singleShot=True, interval=300)
        self._save_timer.timeout.connect(self._save_current_note)

        # Temporizador para feedback de copiado
        self._copied_timer = QTimer(self, singleShot=True, interval=1500)
        self._copied_timer.timeout.connect(lambda: self.copied_label.hide())

        # Temporizador para auto-copia al seleccionar texto
        self._selection_copy_timer = QTimer(self, singleShot=True, interval=120)
        self._selection_copy_timer.timeout.connect(self._copy_selected_text)

        self._build_ui()
        self.load_notes()
        self.apply_theme()

    def _size_file_path(self) -> str:
        return os.path.join(self.config_dir, "notes_size.json")

    def _load_saved_size(self) -> QSize | None:
        try:
            path = self._size_file_path()
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) == 2:
                    return QSize(max(440, int(data[0])), max(280, int(data[1])))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return None

    def _save_size(self) -> None:
        try:
            path = self._size_file_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump([self.width(), self.height()], f)
        except OSError:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._save_size()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # Marco exterior con borde estilizado
        frame = QFrame(self)
        frame.setObjectName("notesFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 6, 6, 6)
        frame_layout.setSpacing(6)
        main_layout.addWidget(frame, 1)

        # Barra superior: filtro y botones de acción rápida
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(tr("Buscar notas o comandos…"))
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._filter_notes)
        top_bar.addWidget(self.filter_input, 1)

        self.btn_new = QToolButton()
        self.btn_new.setText(tr("Nueva"))
        self.btn_new.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_new.setToolTip(tr("Crear nueva nota (Ctrl+N)"))
        self.btn_new.clicked.connect(self.new_note)
        top_bar.addWidget(self.btn_new)

        self.btn_paste = QToolButton()
        self.btn_paste.setText(tr("Pegar"))
        self.btn_paste.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_paste.setToolTip(tr("Crear nota desde el portapapeles"))
        self.btn_paste.clicked.connect(self.paste_from_clipboard)
        top_bar.addWidget(self.btn_paste)

        self.btn_import = QToolButton()
        self.btn_import.setToolTip(tr("Importar notas…"))
        self.btn_import.clicked.connect(self.import_notes)
        top_bar.addWidget(self.btn_import)

        self.btn_export = QToolButton()
        self.btn_export.setToolTip(tr("Exportar notas…"))
        self.btn_export.clicked.connect(self.export_notes)
        top_bar.addWidget(self.btn_export)

        frame_layout.addLayout(top_bar)

        # Contenedor central (Splitter)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Panel izquierdo: lista de notas
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_delegate = SearchHighlightDelegate(lambda: self.filter_input.text(), self.list_widget)
        self.list_widget.setItemDelegate(self.list_delegate)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        left_layout.addWidget(self.list_widget)

        splitter.addWidget(left_widget)

        # Panel derecho: título, botón de copiado y editor
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        editor_top = QHBoxLayout()
        editor_top.setSpacing(4)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText(tr("Título de la nota"))
        self.title_input.textEdited.connect(self._on_title_edited)
        self.title_input.selectionChanged.connect(self._on_title_selection_changed)
        editor_top.addWidget(self.title_input, 1)

        self.copied_label = QLabel(tr("¡Copiado!"))
        self.copied_label.setStyleSheet("color: #4caf50; font-weight: bold; padding: 0 4px;")
        self.copied_label.hide()
        editor_top.addWidget(self.copied_label)

        self.btn_copy = QToolButton()
        self.btn_copy.setText(tr("Copiar"))
        self.btn_copy.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_copy.setToolTip(tr("Copiar contenido al portapapeles (Enter)"))
        self.btn_copy.clicked.connect(self.copy_current_note)
        editor_top.addWidget(self.btn_copy)

        self.btn_delete = QToolButton()
        self.btn_delete.setToolTip(tr("Eliminar esta nota"))
        self.btn_delete.clicked.connect(self.delete_current_note)
        editor_top.addWidget(self.btn_delete)

        right_layout.addLayout(editor_top)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr("Escribe o pega aquí fragmentos, rutas o comandos…"))
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(9)
        self.editor.setFont(font)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.selectionChanged.connect(self._on_editor_selection_changed)
        right_layout.addWidget(self.editor, 1)

        # Barra inferior con tirador de tamaño
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 0, 0, 0)
        bottom_bar.addStretch(1)
        self.size_grip = QSizeGrip(self)
        bottom_bar.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        right_layout.addLayout(bottom_bar)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        frame_layout.addWidget(splitter, 1)

        # Atajos
        QShortcut(QKeySequence("Ctrl+N"), self, self.new_note)

    # ------------------------------------------------------------------ Redimensionamiento
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            margin = 12
            on_right = pos.x() >= self.width() - margin
            on_bottom = pos.y() >= self.height() - margin
            if on_right or on_bottom:
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            new_w = max(440, self._resize_start_size.width() + delta.x())
            new_h = max(280, self._resize_start_size.height() + delta.y())
            self.resize(new_w, new_h)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            self._resizing = False
            self._save_size()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._save_current_note()
        self._save_size()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            pos = event.globalPosition().toPoint()
            if not self.geometry().contains(pos):
                self.hide()
                if self._host_widget is not None and self._host_widget.isVisible():
                    host_rect = QRect(self._host_widget.mapToGlobal(QPoint(0, 0)), self._host_widget.size())
                    if host_rect.contains(pos):
                        return True  # Si se pulsa el botón del panel, consumir para no reabrirlo
                return False  # Cierra el popup y deja pasar el clic al botón/elemento destino
        return False

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and not getattr(self, "_dialog_open", False):
                self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ Tema y Estilos
    def apply_theme(self) -> None:
        t = theme.current()
        icon_color = t.text  # Alto contraste idéntico al texto

        self.setStyleSheet(f"""
            QDialog {{ background: {t.window}; border: 1px solid {t.border}; border-radius: 6px; }}
            QFrame#notesFrame {{ background: {t.panel}; border-radius: 4px; }}
            QPlainTextEdit {{
                background: {t.field};
                color: {t.text};
                border: 1px solid {t.border};
                border-radius: 3px;
                selection-background-color: {t.sel_source};
                selection-color: {t.sel_text};
            }}
            QLineEdit {{
                background: {t.field};
                color: {t.text};
                border: 1px solid {t.border};
                padding: 3px;
                border-radius: 3px;
                selection-background-color: {t.sel_source};
                selection-color: {t.sel_text};
            }}
            QListWidget {{
                background: {t.field};
                color: {t.text};
                border: 1px solid {t.border};
                border-radius: 3px;
            }}
            QListWidget::item {{
                padding: 3px;
                border-radius: 2px;
            }}
            QListWidget::item:selected {{
                background: {t.sel_source};
                color: {t.sel_text};
            }}
            QToolButton {{
                background: {t.window};
                color: {t.text};
                border: 1px solid {t.border};
                border-radius: 4px;
                padding: 3px 6px;
                font-weight: 500;
            }}
            QToolButton:hover {{
                background: {t.hover};
                border: 1px solid {t.accent};
            }}
            QToolButton:pressed {{
                background: {t.border};
            }}
        """)

        self.btn_new.setIcon(themed("newtextfile", color=icon_color))
        self.btn_paste.setIcon(themed("copy", color=icon_color))
        self.btn_copy.setIcon(themed("copy", color=icon_color))
        self.btn_delete.setIcon(themed("delete", color=icon_color))
        self.btn_import.setIcon(themed("extract", color=icon_color))
        self.btn_export.setIcon(themed("addtozip", color=icon_color))

        self._update_editor_highlights()
        self.list_widget.viewport().update()

    # ------------------------------------------------------------------ Selección y Auto-copiado
    def _on_editor_selection_changed(self) -> None:
        if self._loading:
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().replace("\u2029", "\n")
            if text.strip():
                self._pending_selection = text
                self._selection_copy_timer.start()

    def _on_title_selection_changed(self) -> None:
        if self._loading:
            return
        text = self.title_input.selectedText()
        if text.strip():
            self._pending_selection = text
            self._selection_copy_timer.start()

    def _copy_selected_text(self) -> None:
        if self._pending_selection:
            QApplication.clipboard().setText(self._pending_selection)
            self.flash_copied(tr("¡Copiado!"))
            self._pending_selection = ""

    # ------------------------------------------------------------------ Notas y Filtro
    def load_notes(self) -> None:
        self.notes = notes.load_notes(self.config_dir)
        if not self.notes:
            welcome = notes.create_note(
                content=tr("# Comandos y notas rápidas\n# Escribe aquí rutas, comandos o datos temporales.\n# Haz doble clic en la lista para copiar al instante."),
                title=tr("Bienvenido"),
            )
            self.notes.append(welcome)
            notes.save_notes(self.config_dir, self.notes)
        self._refresh_list()
        if self.notes:
            self._select_note(self.notes[0].id)

    def _refresh_list(self) -> None:
        filter_text = self.filter_input.text().strip().lower()
        self.list_widget.clear()
        for note in self.notes:
            if filter_text and filter_text not in note.title.lower() and filter_text not in note.content.lower():
                continue
            item = QListWidgetItem(note.title)
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            item.setToolTip(note.content[:200] if len(note.content) > 200 else note.content)
            self.list_widget.addItem(item)
        self.list_widget.viewport().update()

    def _filter_notes(self) -> None:
        self._refresh_list()
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self._update_editor_highlights()

    def _update_editor_highlights(self) -> None:
        filter_text = self.filter_input.text().strip()
        extra_selections = []
        if filter_text:
            doc = self.editor.document()
            cursor = QTextCursor(doc)
            t = theme.current()
            bg_col = QColor("#7c5e10" if t.name == "dark" else "#fff59d")
            fg_col = QColor("#ffffff" if t.name == "dark" else "#000000")
            fmt = QTextCharFormat()
            fmt.setBackground(bg_col)
            fmt.setForeground(fg_col)
            fmt.setFontWeight(QFont.Weight.Bold)

            while True:
                cursor = doc.find(filter_text, cursor)
                if cursor.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.format = fmt
                sel.cursor = cursor
                extra_selections.append(sel)

        self.editor.setExtraSelections(extra_selections)

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.list_widget.item(row)
        if item is None:
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        self._select_note(note_id)

    def _select_note(self, note_id: str) -> None:
        note = next((n for n in self.notes if n.id == note_id), None)
        if note is None:
            return
        self._current_id = note_id
        self._loading = True
        self.title_input.setText(note.title)
        self.editor.setPlainText(note.content)
        self._loading = False

        # Sincronizar selección en la lista si no está ya
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == note_id:
                if self.list_widget.currentRow() != i:
                    self.list_widget.setCurrentRow(i)
                break
        self._update_editor_highlights()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._save_timer.start()
        self._update_editor_highlights()

    def _on_title_edited(self, text: str) -> None:
        if self._loading or not self._current_id:
            return
        note = next((n for n in self.notes if n.id == self._current_id), None)
        if note:
            note.title = text.strip() or notes.title_for(note.content)
            self._update_list_item_title(note.id, note.title)
            self._save_timer.start()

    def _update_list_item_title(self, note_id: str, title: str) -> None:
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == note_id:
                it.setText(title)
                break
        self.list_widget.viewport().update()

    def _save_current_note(self) -> None:
        if not self._current_id:
            return
        content = self.editor.toPlainText()
        custom_title = self.title_input.text().strip()
        is_default_title = custom_title in (tr("Nueva nota"), "Nueva nota", "")
        actual_custom = None if is_default_title else custom_title
        notes.update_note(self.notes, self._current_id, content, actual_custom)
        note = next((n for n in self.notes if n.id == self._current_id), None)
        if note:
            if actual_custom is None:
                self.title_input.setText(note.title)
            self._update_list_item_title(note.id, note.title)
        notes.save_notes(self.config_dir, self.notes)

    def new_note(self) -> None:
        self._save_current_note()
        new_item = notes.create_note("", tr("Nueva nota"))
        self.notes.insert(0, new_item)
        notes.save_notes(self.config_dir, self.notes)
        self._refresh_list()
        self._select_note(new_item.id)
        self.editor.setFocus()

    def paste_from_clipboard(self) -> None:
        self._save_current_note()
        clip_text = QApplication.clipboard().text()
        if not clip_text:
            return
        new_item = notes.create_note(clip_text)
        self.notes.insert(0, new_item)
        notes.save_notes(self.config_dir, self.notes)
        self._refresh_list()
        self._select_note(new_item.id)
        self.flash_copied(tr("¡Pegado!"))

    def copy_current_note(self) -> None:
        text = self.editor.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.flash_copied(tr("¡Copiado!"))

    def flash_copied(self, message: str = "") -> None:
        self.copied_label.setText(message or tr("¡Copiado!"))
        self.copied_label.show()
        self._copied_timer.start()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        note_id = item.data(Qt.ItemDataRole.UserRole)
        note = next((n for n in self.notes if n.id == note_id), None)
        if note and note.content:
            QApplication.clipboard().setText(note.content)
            self.flash_copied(tr("¡Copiado!"))

    def delete_current_note(self) -> None:
        if not self._current_id:
            return
        notes.delete_note(self.notes, self._current_id)
        notes.save_notes(self.config_dir, self.notes)
        self._current_id = None
        self._refresh_list()
        if self.notes:
            self._select_note(self.notes[0].id)
        else:
            self.title_input.clear()
            self.editor.clear()

    # ------------------------------------------------------------------ Importar y Exportar
    def export_notes(self) -> None:
        self._dialog_open = True
        try:
            path, _ = QFileDialog.getSaveFileName(
                self,
                tr("Exportar notas"),
                os.path.join(os.path.expanduser("~"), "notas.json"),
                "JSON (*.json);;" + tr("Todos los archivos (*)")
            )
            if not path:
                return
            self._save_current_note()
            notes.export_notes_to_file(path, self.notes)
            self.flash_copied(tr("¡Notas exportadas!"))
        finally:
            self._dialog_open = False

    def import_notes(self) -> None:
        self._dialog_open = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                tr("Importar notas"),
                os.path.expanduser("~"),
                "JSON / Text (*.json *.txt *.md);;" + tr("Todos los archivos (*)")
            )
            if not path:
                return
            self._save_current_note()
            added = notes.import_notes_from_file(path, self.notes)
            if added > 0:
                notes.save_notes(self.config_dir, self.notes)
                self._refresh_list()
                self._select_note(self.notes[-1].id)
                self.flash_copied(tr("¡{count} notas importadas!", count=added))
            else:
                self.flash_copied(tr("No se importaron notas"))
        finally:
            self._dialog_open = False

    # ------------------------------------------------------------------ Despliegue
    def popup_at(self, widget: QWidget | None = None) -> None:
        """Posiciona y muestra el popup justo debajo del widget anfitrión o centrado."""
        if self.isVisible():
            self.hide()
            return

        self._host_widget = widget
        self.apply_theme()

        if widget is not None and widget.isVisible():
            geo = widget.geometry()
            global_pos = widget.mapToGlobal(QPoint(0, geo.height()))
            screen_geo = widget.screen().availableGeometry() if widget.screen() else QApplication.primaryScreen().availableGeometry()
            x = global_pos.x()
            y = global_pos.y() + 4
            # Evitar salir de la pantalla por la derecha
            if x + self.width() > screen_geo.right():
                x = screen_geo.right() - self.width() - 8
            # Evitar salir de la pantalla por abajo
            if y + self.height() > screen_geo.bottom():
                y = widget.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
        else:
            p = self.parentWidget() or self.parent()
            if p is not None and hasattr(p, "geometry"):
                screen_geo = p.screen().availableGeometry() if p.screen() else QApplication.primaryScreen().availableGeometry()
                x = p.geometry().right() - self.width() - 20
                y = p.geometry().top() + 70
            else:
                screen = QApplication.primaryScreen()
                screen_geo = screen.availableGeometry() if screen else None
                if screen_geo:
                    x = screen_geo.center().x() - self.width() // 2
                    y = screen_geo.center().y() - self.height() // 2
                else:
                    x, y = 100, 100

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.filter_input.setFocus()
