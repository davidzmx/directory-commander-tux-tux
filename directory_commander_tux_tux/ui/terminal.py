"""Una terminal integrada: dibuja la pantalla de `vt_screen` y le pasa el teclado al intérprete."""
from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QKeySequence, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QComboBox, QMenu

from ..core.i18n import tr
from ..core.pty_session import PtySession
from ..core.shells import DEFAULT_FAMILY, OTHER, POWERSHELL, Shell, can_clear_line_with_escape, cd_command, default_shell, looks_like_prompt, prompt_folder
from ..core.vt_screen import Screen
from . import theme

TOGGLE_SHORTCUT = "Ctrl+Shift+O"
GENERAL_TOGGLE_SHORTCUT = "Ctrl+Shift+G"
# Lo único que la terminal deja pasar a la ventana. El resto de atajos son teclas para el programa que se esté ejecutando.
_WINDOW_KEYS = [QKeySequence(TOGGLE_SHORTCUT), QKeySequence(GENERAL_TOGGLE_SHORTCUT), QKeySequence("Ctrl+Tab"), QKeySequence("Ctrl+Shift+Tab"), QKeySequence("Ctrl+Shift+Backtab"), QKeySequence("Alt+F4")]
_FONT_FAMILIES = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Noto Sans Mono", "Liberation Mono"]
_REPAINT_MS = 16
_RESIZE_MS = 60
_WHEEL_LINES = 3
_PENDING_CHUNKS = 4  # si el programa escribe sin parar, se le frena para que la ventana siga respondiendo
_PENDING_WAIT_SECONDS = 2
_FOLLOW_RETRY_MS = 250
_QUIET_SECONDS = 0.4  # justo tras pulsar Intro, el programa recién lanzado aún podría recibir como suya la orden
_DARK_COLORS = ("#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#2f6fd8", "#881798", "#3a96dd", "#cccccc",
                "#767676", "#e74856", "#16c60c", "#f9f1a5", "#5b9bf5", "#b4009e", "#61d6d6", "#f2f2f2")
_LIGHT_COLORS = ("#1f2124", "#b3121f", "#0e7a0c", "#8a6d00", "#1a4fa3", "#7a1589", "#0f7f8f", "#6a6e75",
                 "#4a4d52", "#d1242f", "#1a8f18", "#a37f00", "#2d6db5", "#a0208f", "#1098a8", "#1f2124")
_CURSOR_KEYS = {Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C", Qt.Key.Key_Left: "D", Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F"}
_FUNCTION_KEYS = {Qt.Key.Key_F1: "P", Qt.Key.Key_F2: "Q", Qt.Key.Key_F3: "R", Qt.Key.Key_F4: "S"}
_TILDE_KEYS = {Qt.Key.Key_Insert: 2, Qt.Key.Key_Delete: 3, Qt.Key.Key_PageUp: 5, Qt.Key.Key_PageDown: 6, Qt.Key.Key_F5: 15, Qt.Key.Key_F6: 17,
               Qt.Key.Key_F7: 18, Qt.Key.Key_F8: 19, Qt.Key.Key_F9: 20, Qt.Key.Key_F10: 21, Qt.Key.Key_F11: 23, Qt.Key.Key_F12: 24}
_CONTROL_SYMBOLS = {Qt.Key.Key_Space: "\0", Qt.Key.Key_At: "\0", Qt.Key.Key_BracketLeft: "\x1b", Qt.Key.Key_Backslash: "\x1c", Qt.Key.Key_BracketRight: "\x1d",
                    Qt.Key.Key_AsciiCircum: "\x1e", Qt.Key.Key_Underscore: "\x1f", Qt.Key.Key_Slash: "\x1f"}


def key_text(key, modifiers, text: str, app_cursor: bool = False) -> str:
    """Lo que un programa de consola espera recibir por una tecla. Vacío si no le corresponde ninguna."""
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    code = 1 + shift + 2 * alt + 4 * ctrl
    if key in _CURSOR_KEYS:
        letter = _CURSOR_KEYS[key]
        return f"\x1b[1;{code}{letter}" if code > 1 else f"{chr(27)}{'O' if app_cursor else '['}{letter}"
    if key in _FUNCTION_KEYS:
        letter = _FUNCTION_KEYS[key]
        return f"\x1b[1;{code}{letter}" if code > 1 else f"\x1bO{letter}"
    if key in _TILDE_KEYS:
        number = _TILDE_KEYS[key]
        return f"\x1b[{number};{code}~" if code > 1 else f"\x1b[{number}~"
    if key == Qt.Key.Key_Backtab:
        return "\x1b[Z"
    if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        return "\x1b\r" if alt else "\r"
    if key == Qt.Key.Key_Backspace:
        return "\x08" if ctrl else "\x1b\x7f" if alt else "\x7f"
    if key == Qt.Key.Key_Tab:
        return "\t"
    if key == Qt.Key.Key_Escape:
        return "\x1b"
    printable = bool(text) and text.isprintable()
    if ctrl and alt and printable:
        return text  # en Windows, AltGr llega como Ctrl+Alt: es una letra como @ o #, no un atajo
    if ctrl and not alt:
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key - Qt.Key.Key_A + 1)
        return _CONTROL_SYMBOLS.get(key, "")
    if not printable:
        return ""
    return f"\x1b{text}" if alt else text


def _palette_color(number: int, base: tuple[str, ...]) -> QColor:
    if number < 16:
        return QColor(base[number])
    if number < 232:
        red, green, blue = (number - 16) // 36, (number - 16) // 6 % 6, (number - 16) % 6
        return QColor(*(level * 40 + 55 if level else 0 for level in (red, green, blue)))
    level = (number - 232) * 10 + 8
    return QColor(level, level, level)


class ShellBox(QComboBox):
    """La lista del intérprete de una terminal. Cambiarlo cierra lo que haya en ella: nunca sin querer, con la rueda o con el teclado."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

    def wheelEvent(self, event) -> None:
        event.ignore()


class TerminalView(QAbstractScrollArea):
    """Empieza su intérprete la primera vez que se muestra, en la carpeta que indica `folder`. Sigue vivo aunque se oculte."""
    activated = Signal()
    started = Signal()
    finished = Signal()
    failed = Signal(str)
    _received = Signal(int, bytes)
    _ended = Signal(int)
    session_factory = PtySession

    def __init__(self, folder: Callable[[], str], shell: Callable[[], Shell] = default_shell, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.shell = shell
        self.session = None
        self.running_shell: Shell | None = None
        self._generation = 0  # lo que aún llegue del intérprete anterior ya no es de esta pantalla
        self.following = True
        self._family = DEFAULT_FAMILY
        self._credits = threading.Semaphore(_PENDING_CHUNKS)
        self._selection: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._selecting = False
        self._current_folder = ""
        self._wanted_folder: str | None = None
        self._line_started = False
        self._last_activity = 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setMinimumSize(120, 60)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if sys.platform != "win32":
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)  # si apareciese con el primer historial, la terminal se estrecharía y recortaría lo ya escrito
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setFamilies([*_FONT_FAMILIES, font.family()])
        font.setPointSize(10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self._fonts: dict[tuple, QFont] = {}
        self._measure()
        self.screen_ = self._new_screen()
        self.apply_theme()
        self._repaint_timer = QTimer(self, singleShot=True, interval=_REPAINT_MS)
        self._repaint_timer.timeout.connect(self._refresh)
        self._resize_timer = QTimer(self, singleShot=True, interval=_RESIZE_MS)
        self._resize_timer.timeout.connect(self._send_size)
        self._follow_timer = QTimer(self, interval=_FOLLOW_RETRY_MS)
        self._follow_timer.timeout.connect(self._try_follow)
        self._received.connect(self._feed, Qt.ConnectionType.QueuedConnection)
        self._ended.connect(self._session_ended, Qt.ConnectionType.QueuedConnection)

    def _measure(self) -> None:
        metrics = QFontMetricsF(self.font())
        self._cell_width = max(1.0, metrics.horizontalAdvance("M"))
        self._cell_height = max(1, round(metrics.height()))
        self._ascent = metrics.ascent()

    def _grid_size(self) -> tuple[int, int]:
        size = self.viewport().size()
        return max(2, int(size.width() / self._cell_width)), max(1, size.height() // self._cell_height)

    def _new_screen(self) -> Screen:
        cols, rows = self._grid_size()
        return Screen(cols, rows, respond=self._write)

    def sizeHint(self) -> QSize:
        return QSize(640, 240)

    # ------------------------------------------------------------------ el intérprete
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.session is None:
            QTimer.singleShot(0, self._start)  # cuando el diseño ya le ha dado su tamaño

    def _start(self) -> None:
        if self.session is not None or not self.isVisible():
            return
        try:
            self._open_session(self.folder())
        except OSError as exc:
            self.failed.emit(tr("No se pudo abrir la terminal: {exc}", exc=exc))

    def switch_shell(self) -> None:
        """Abre otro intérprete en la carpeta en la que está este. Si el nuevo no arranca da OSError, y el anterior sigue como estaba."""
        self._open_session(self.current_path())

    def current_path(self) -> str:
        """La carpeta en la que está ahora el intérprete, aunque se haya movido con cd. Si no se puede saber, la última a la que se le llevó."""
        found = None
        if self.session is not None:
            found = prompt_folder(self.screen_.line_before_cursor()) if self._family == POWERSHELL else self.session.current_directory()
        return found if found and os.path.isdir(found) else self._current_folder or self.folder()

    def _open_session(self, folder: str) -> None:
        cols, rows = self._grid_size()
        shell = self.shell()
        generation = self._generation + 1
        session = self.session_factory(shell.argv, folder, cols, rows, lambda data: self._data_arrived(generation, data), lambda: self._exit_arrived(generation), shell.env)
        previous, self.session = self.session, session
        self._generation = generation
        self.running_shell = shell
        self.screen_ = self._new_screen()
        self._selection = None
        if previous is not None:
            threading.Thread(target=previous.close, daemon=True).start()  # el sistema puede tardar, y la ventana no debe esperarlo
        self._family = shell.family
        self._current_folder = folder
        self._wanted_folder = None
        self._line_started = False
        self._last_activity = time.monotonic()
        self._refresh()
        self.started.emit()

    @property
    def can_follow(self) -> bool:
        return self._family != OTHER

    def set_following(self, following: bool) -> None:
        """Al activarlo se vuelve a ir a la carpeta del panel, aunque parezca la misma: mientras tanto el usuario ha podido moverse con cd."""
        self.following = following
        self._wanted_folder = None
        self._current_folder = "" if following else self._current_folder
        self._try_follow()

    def follow(self, path: str) -> None:
        """Lleva el intérprete a la carpeta del panel, ahora o en cuanto deje de estar ocupado. Solo se quedan sin ir las carpetas que no se pueden escribir sin riesgo."""
        if self.session is None or not self.following:
            return
        self._wanted_folder = path if path != self._current_folder and cd_command(path, self._family) else None
        self._try_follow()

    def _ready_for_command(self) -> bool:
        """Se escribe solo en un indicador vacío: nunca en lo que el usuario esté tecleando ni en un programa que espera una respuesta."""
        screen = self.screen_
        if self._line_started or screen.alt or time.monotonic() - self._last_activity < _QUIET_SECONDS:
            return False
        # Sin hijos, el intérprete aún puede estar en un `pause` o un `set /p` de un .bat.
        return looks_like_prompt(screen.prompt_text(), self._family) and self.session.shell_is_idle()

    @property
    def family(self) -> str:
        return self._family

    def run_command(self, command: str, execute: bool = True) -> bool:
        """Escribe una orden, y la ejecuta si se pide. False si ahora no se puede: nunca sobre lo que el usuario esté tecleando ni en un programa que espera otra cosa."""
        if self.session is None or not self._ready_for_command():
            return False
        self._send_text(command + "\r" if execute else command)
        return True

    def _try_follow(self) -> None:
        if self._wanted_folder is None or self.session is None:
            self._follow_timer.stop()
        elif self._ready_for_command():
            self._current_folder, self._wanted_folder = self._wanted_folder, None
            self._follow_timer.stop()
            self._write(cd_command(self._current_folder, self._family).encode())
        elif not self._follow_timer.isActive():
            self._follow_timer.start()

    def _data_arrived(self, generation: int, data: bytes) -> None:
        """Desde el hilo de lectura. El de un intérprete ya sustituido no debe tocar nada: la ventana puede estar cerrándose.
        El nuevo puede escribir antes de que `_open_session` anote su número, y un intérprete rápido perdería así su indicador."""
        if generation < self._generation:
            return
        self._credits.acquire(timeout=_PENDING_WAIT_SECONDS)
        if generation >= self._generation:
            self._received.emit(generation, data)
        else:
            self._credits.release()

    def _exit_arrived(self, generation: int) -> None:
        if generation >= self._generation:
            self._ended.emit(generation)

    def _feed(self, generation: int, data: bytes) -> None:
        self._credits.release()
        if generation != self._generation:
            return
        self._last_activity = time.monotonic()
        self.screen_.feed(data)
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _session_ended(self, generation: int) -> None:
        if generation != self._generation:
            return
        self.session = None
        self.running_shell = None
        self._follow_timer.stop()
        self.finished.emit()

    def _write(self, data: bytes) -> None:
        if self.session is not None:
            self.session.write(data)

    def _send_text(self, text: str) -> None:
        self._last_activity = time.monotonic()
        # Con Intro o Ctrl+C la línea vuelve a estar vacía, y Esc la vacía en cmd y PowerShell. Cualquier otra tecla podría haber dejado texto a medias.
        self._line_started = not (text.endswith(("\r", "\x03")) or (text == "\x1b" and can_clear_line_with_escape(self._family)))
        self._selection = None
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self._write(text.encode("utf-8", "replace"))
        self.viewport().update()

    def dispose(self) -> None:
        """Al cerrar la ventana. Termina también lo que siga en marcha dentro."""
        session, self.session = self.session, None
        self._generation += 1
        self._follow_timer.stop()
        if session is not None:
            session.close()

    # ------------------------------------------------------------------ teclado
    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride:
            if QKeySequence(event.keyCombination()) not in _WINDOW_KEYS:
                event.accept()  # Supr, Ctrl+A o F5 son para el programa y no para los archivos del panel
            return True
        return super().event(event)

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False  # el tabulador completa nombres, no cambia de control

    def keyPressEvent(self, event) -> None:
        key, modifiers = event.key(), event.modifiers()
        if QKeySequence(event.keyCombination()) in _WINDOW_KEYS or key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            event.ignore()
            return
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        only_ctrl = modifiers == Qt.KeyboardModifier.ControlModifier
        if (modifiers == ctrl_shift and key == Qt.Key.Key_C) or (only_ctrl and key == Qt.Key.Key_C and self._selection):
            self.copy_selection()
            return
        if (modifiers == ctrl_shift and key == Qt.Key.Key_V) or (only_ctrl and key == Qt.Key.Key_V and sys.platform == "win32"):
            self.paste()
            return
        if modifiers == Qt.KeyboardModifier.ShiftModifier and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (-bar.pageStep() if key == Qt.Key.Key_PageUp else bar.pageStep()))
            return
        text = key_text(key, modifiers, event.text(), self.screen_.app_cursor)
        if text:
            self._send_text(text)

    def inputMethodEvent(self, event) -> None:
        if event.commitString():
            self._send_text(event.commitString())
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            x, y = int(self.screen_.x * self._cell_width), self.screen_.y * self._cell_height
            return QRect(x, y, int(self._cell_width), self._cell_height)
        return super().inputMethodQuery(query)

    # ------------------------------------------------------------------ portapapeles
    def selected_text(self) -> str:
        if not self._selection:
            return ""
        start, end = sorted(self._selection)
        return self.screen_.text_between(start, end)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)
        self._selection = None
        self.viewport().update()

    def paste(self) -> None:
        text = QApplication.clipboard().text().replace("\r\n", "\r").replace("\n", "\r").replace("\x1b", "")
        if text:
            self._send_text(f"\x1b[200~{text}\x1b[201~" if self.screen_.bracketed_paste else text)

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        copy = menu.addAction(tr("Copiar"), self.copy_selection)
        copy.setEnabled(bool(self._selection))
        menu.addAction(tr("Pegar"), self.paste)
        return menu

    def contextMenuEvent(self, event) -> None:
        self.build_menu().exec(event.globalPos())

    # ------------------------------------------------------------------ ratón
    def _cell_at(self, position) -> tuple[int, int]:
        row = max(0, min(int(position.y() // self._cell_height), self.screen_.rows - 1))
        column = max(0, min(round(position.x() / self._cell_width), self.screen_.cols))
        return self.screen_.dropped + self.verticalScrollBar().value() + row, column

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.activated.emit()
        self.viewport().update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position())
            self._selection = (cell, cell)
            self._selecting = True
            self.viewport().update()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting and self._selection:
            self._selection = (self._selection[0], self._cell_at(event.position()))
            self.viewport().update()

    def mouseReleaseEvent(self, _event) -> None:
        self._selecting = False
        if self._selection and self._selection[0] == self._selection[1]:
            self._selection = None
            self.viewport().update()

    def mouseDoubleClickEvent(self, event) -> None:
        """Selecciona la palabra: lo que hay entre dos espacios, como una ruta o un nombre de archivo."""
        row, column = self._cell_at(event.position())
        cells = self.screen_.line(row - self.screen_.dropped)
        column = min(column, len(cells) - 1)
        if not cells or cells[column][0] == " ":
            return
        start = end = column
        while start > 0 and cells[start - 1][0] != " ":
            start -= 1
        while end + 1 < len(cells) and cells[end + 1][0] != " ":
            end += 1
        self._selection = ((row, start), (row, end + 1))
        self._selecting = False
        self.viewport().update()

    def wheelEvent(self, event) -> None:
        if self.screen_.alt:  # sin historial: la rueda mueve el programa, como las flechas
            steps = abs(event.angleDelta().y()) // 120 * _WHEEL_LINES
            arrow = "A" if event.angleDelta().y() > 0 else "B"
            self._write((f"{chr(27)}{'O' if self.screen_.app_cursor else '['}{arrow}" * steps).encode())
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------ dibujo
    def apply_theme(self) -> None:
        current = theme.current()
        base = _DARK_COLORS if current.name == "dark" else _LIGHT_COLORS
        self._background = QColor(current.field)
        self._foreground = QColor(current.text)
        self._selection_background = QColor(current.accent)
        self._selection_text = QColor(current.accent_text)
        self._cursor_color = QColor(current.text)
        self._colors = [_palette_color(number, base) for number in range(256)]
        self._style_cache: dict = {}
        self.viewport().update()

    def _resolve(self, style) -> tuple[QColor, QColor | None]:
        """Colores de texto y de fondo de un estilo. Sin fondo propio, None: ya está pintado el general."""
        found = self._style_cache.get(style)
        if found is None:
            fg_value = style.fg + 8 if style.bold and isinstance(style.fg, int) and style.fg < 8 else style.fg
            foreground, background = self._color(fg_value, self._foreground), self._color(style.bg, None)
            if style.inverse:
                foreground, background = background or self._background, foreground
            if style.dim:
                foreground = QColor(foreground)
                foreground.setAlpha(150)
            found = self._style_cache[style] = (foreground, background)
        return found

    def _color(self, value, default):
        if value is None:
            return default
        return QColor(*value) if isinstance(value, tuple) else self._colors[value]

    def _font_for(self, style) -> QFont:
        key = (style.bold, style.italic, style.underline, style.strike)
        font = self._fonts.get(key)
        if font is None:
            font = QFont(self.font())
            font.setBold(style.bold)
            font.setItalic(style.italic)
            font.setUnderline(style.underline)
            font.setStrikeOut(style.strike)
            self._fonts[key] = font
        return font

    def paintEvent(self, _event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self._background)
        screen = self.screen_
        first = self.verticalScrollBar().value()
        selection = sorted(self._selection) if self._selection and self._selection[0] != self._selection[1] else None
        for row in range(screen.rows):
            index = first + row
            if index < screen.line_count:
                self._paint_line(painter, screen.line(index), row, index + screen.dropped, selection)
        cursor_row = screen.cursor_index - first
        if screen.cursor_visible and 0 <= cursor_row < screen.rows:
            rect = QRectF(screen.x * self._cell_width, cursor_row * self._cell_height, self._cell_width, self._cell_height)
            if self.hasFocus():
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
                painter.fillRect(rect, QColor("#ffffff"))
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            else:
                painter.setPen(self._cursor_color)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_line(self, painter: QPainter, line, row: int, number: int, selection) -> None:
        select_from, select_to = -1, -1
        if selection and selection[0][0] <= number <= selection[1][0]:
            select_from = selection[0][1] if number == selection[0][0] else 0
            select_to = selection[1][1] if number == selection[1][0] else len(line)
        top = row * self._cell_height
        column, size = 0, len(line)
        while column < size:
            style = line[column][1]
            selected = select_from <= column < select_to
            end = column + 1
            while end < size and line[end][1] == style and (select_from <= end < select_to) == selected:
                end += 1
            self._paint_run(painter, line[column:end], column, top, style, selected)
            column = end

    def _paint_run(self, painter: QPainter, cells, column: int, top: int, style, selected: bool) -> None:
        foreground, background = (self._selection_text, self._selection_background) if selected else self._resolve(style)
        if background is not None:
            left = round(column * self._cell_width)
            painter.fillRect(left, top, round((column + len(cells)) * self._cell_width) - left, self._cell_height, background)
        text = "".join(cell[0] for cell in cells)
        if not text.strip() and not style.underline and not style.strike:
            return
        painter.setFont(self._font_for(style))
        painter.setPen(foreground)
        baseline = top + self._ascent
        if text.isascii():
            painter.drawText(QPointF(column * self._cell_width, baseline), text)
            return
        # Las letras de otras fuentes no miden lo mismo: cada una en su celda para que no se desplace lo que sigue.
        for offset, cell in enumerate(cells):
            if cell[0] and cell[0] != " ":
                painter.drawText(QPointF((column + offset) * self._cell_width, baseline), cell[0])

    def _refresh(self) -> None:
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum()
        bar.setRange(0, len(self.screen_.history))
        bar.setPageStep(self.screen_.rows)
        if at_end:
            bar.setValue(bar.maximum())
        self.viewport().update()

    def scrollContentsBy(self, _dx: int, _dy: int) -> None:
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cols, rows = self._grid_size()
        self.screen_.resize(cols, rows)
        self._refresh()
        self._resize_timer.start()  # el intérprete redibuja cada vez: al arrastrar, solo cuando se detiene

    def _send_size(self) -> None:
        if self.session is not None:
            self.session.resize(self.screen_.cols, self.screen_.rows)
